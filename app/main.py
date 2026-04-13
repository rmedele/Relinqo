import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.sessions import SessionMiddleware

from app.auth import get_current_user, get_org_from_session_or_api_key, get_org_settings
from app.classifier import classify_lead
from app.config import settings
from app.database import Base, SessionLocal, engine, get_db
from app.email_parser import parse_forwarded_email
from app.followups import run_followups
from app.inbox_poll import poll_inbox
from app.mailer import send_email, smtp_configured
from app.models import Lead, Organization, OrgSettings, User
from app.routes.auth import router as auth_router
from app.routes.google_oauth import router as google_oauth_router
from app.routes.leads import ingest_lead, router as lead_router
from app.routes.settings import router as settings_router
from app.routes.scheduling import router as scheduling_router
from app.routes.sms_webhook import router as sms_router
from app.schemas import ForwardedEmailIngestRequest, HealthResponse, LeadIngestRequest
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

Base.metadata.create_all(bind=engine)


FLUSH_INTERVAL_SECONDS = 30
FOLLOWUP_INTERVAL_SECONDS = 300


def _flush_pending(db: Session) -> int:
    """Send all leads whose undo window has passed, grouped by org."""
    now = datetime.now(timezone.utc)
    pending = db.query(Lead).filter(Lead.status == "pending_send", Lead.send_at <= now).all()

    # Group by org_id to load settings once per org
    by_org: dict[int, list[Lead]] = {}
    for lead in pending:
        by_org.setdefault(lead.org_id, []).append(lead)

    total = 0
    for org_id, leads in by_org.items():
        org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        for lead in leads:
            sent, message = send_email(
                to_email=lead.sender_email,
                subject=f"Re: {lead.subject or 'Your inquiry'}",
                body=lead.recommended_reply or "",
                org_settings=org_settings,
            )
            lead.status = "sent" if sent else "send_failed"
            lead.send_at = None
            db.commit()
            total += 1
    return total


async def _scheduler_loop():
    """Background loop that flushes pending sends and processes due follow-ups per org."""
    tick = 0
    while True:
        await asyncio.sleep(FLUSH_INTERVAL_SECONDS)
        tick += FLUSH_INTERVAL_SECONDS
        db = SessionLocal()
        try:
            _flush_pending(db)
            if tick % FOLLOWUP_INTERVAL_SECONDS == 0:
                # Run followups per org
                orgs = db.query(Organization).all()
                for org in orgs:
                    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org.id).first()
                    run_followups(db, org_id=org.id, org_settings=org_settings)
        except Exception:
            logger.exception("Scheduler tick failed")
        finally:
            db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_scheduler_loop())
    logger.info("Background scheduler started (flush=%ds, followups=%ds)", FLUSH_INTERVAL_SECONDS, FOLLOWUP_INTERVAL_SECONDS)
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_allowed_origins = [settings.public_base_url]
if settings.app_env == "development":
    _allowed_origins.append("http://localhost:8080")
    _allowed_origins.append("http://127.0.0.1:8080")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)
if settings.app_env == "production":
    # Trust only the configured domain in production
    allowed_hosts = [settings.public_base_url.replace("https://", "").replace("http://", "").split(":")[0]]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

    @app.middleware("http")
    async def production_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        # Redirect HTTP to HTTPS if behind a proxy that sets X-Forwarded-Proto
        if request.headers.get("x-forwarded-proto") == "http":
            from starlette.responses import RedirectResponse as StRedirect
            url = request.url.replace(scheme="https")
            return StRedirect(url=str(url), status_code=301)
        return response

app.include_router(auth_router)
app.include_router(google_oauth_router)
app.include_router(lead_router)
app.include_router(scheduling_router)
app.include_router(settings_router)
app.include_router(sms_router)

UI_DIR = Path(__file__).resolve().parent / "ui"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONTACT_REQUESTS_PATH = DATA_DIR / "contact_requests.jsonl"
app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")


def _contact_status_markup(status: str | None) -> str:
    if status == "success":
        return '<div class="demo-alert demo-alert-success">Thanks — demo request received. We saved it and routed it for follow-up.</div>'
    if status == "error":
        return '<div class="demo-alert demo-alert-error">Something went wrong saving your request. Please try again.</div>'
    return ""


def _store_contact_request(payload: dict[str, str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    with CONTACT_REQUESTS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def _get_default_org_settings(db: Session) -> tuple[int, OrgSettings | None]:
    """Load the default org (id=1) settings for platform-level actions."""
    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == 1).first()
    return 1, org_settings


def _create_contact_lead(payload: dict[str, str]) -> int:
    body = (
        f"Demo request from {payload['name']} at {payload['company']}. "
        f"Email: {payload['email']}. "
        f"Phone: {payload['phone'] or 'not provided'}. "
        f"Message: {payload['message'] or 'No message provided.'}"
    )
    subject = f"Book a demo — {payload['company']}"

    db = SessionLocal()
    try:
        org_id, org_settings = _get_default_org_settings(db)
        classification = classify_lead(
            {
                "source": "marketing_form",
                "sender_name": payload["name"],
                "sender_email": payload["email"],
                "subject": subject,
                "body": body,
                "phone": payload["phone"] or None,
                "location": None,
            },
            org_settings=org_settings,
        )
        status = "spam" if classification.category == "spam" else "drafted"

        lead = Lead(
            org_id=org_id,
            source="marketing_form",
            sender_name=payload["name"],
            sender_email=payload["email"],
            subject=subject,
            body=body,
            phone=payload["phone"] or None,
            location=None,
            category=classification.category,
            urgency_score=classification.urgency_score,
            summary=classification.summary,
            recommended_reply=classification.recommended_reply,
            owner_alert_needed=True,
            status=status,
            confidence=classification.confidence,
            next_step="review_demo_request",
            raw_payload=json.dumps(payload),
        )
        db.add(lead)
        db.commit()
        db.refresh(lead)
        return lead.id
    finally:
        db.close()


@app.get("/", include_in_schema=False)
@limiter.limit("30/minute")
def marketing_page(request: Request):
    return FileResponse(UI_DIR / "marketing.html")


@app.get("/book-demo", include_in_schema=False)
@limiter.limit("30/minute")
def book_demo_page(request: Request, status: str | None = None):
    html = (UI_DIR / "book-demo.html").read_text(encoding="utf-8")
    html = html.replace("{{CONTACT_STATUS}}", _contact_status_markup(status))
    return HTMLResponse(html)


@app.post("/forwarded-email")
@limiter.limit("20/minute")
def ingest_forwarded_email(
    request: Request,
    payload: ForwardedEmailIngestRequest,
    db: Session = Depends(get_db),
    org_ctx: tuple[Organization, OrgSettings] = Depends(get_org_from_session_or_api_key),
):
    org, org_settings = org_ctx
    if payload.token != (org_settings.forwarding_token if org_settings else settings.forwarding_token):
        raise HTTPException(status_code=401, detail="Invalid forwarding token")

    if payload.raw_email:
        parsed = parse_forwarded_email(payload.raw_email)
        sender_email = parsed.get("sender_email")
        if not sender_email:
            raise HTTPException(status_code=400, detail="Could not parse sender email from forwarded message")
        lead_payload = LeadIngestRequest(
            source=payload.source,
            sender_name=parsed.get("sender_name"),
            sender_email=sender_email,
            subject=parsed.get("subject"),
            body=parsed.get("body") or payload.raw_email,
        )
    else:
        if not payload.from_email or not payload.body:
            raise HTTPException(status_code=400, detail="from_email and body are required when raw_email is not provided")
        lead_payload = LeadIngestRequest(
            source=payload.source,
            sender_name=payload.from_name,
            sender_email=payload.from_email,
            subject=payload.subject,
            body=payload.body,
        )

    lead = ingest_lead(lead_payload, db, org_id=org.id, org_settings=org_settings)
    return {"ok": True, "lead_id": lead.id, "status": lead.status}


@app.post("/contact", include_in_schema=False)
@limiter.limit("5/minute")
def submit_contact_form(
    request: Request,
    name: str = Form(...),
    company: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    message: str = Form(""),
):
    payload = {
        "name": name.strip(),
        "company": company.strip(),
        "email": email.strip(),
        "phone": phone.strip(),
        "message": message.strip(),
    }

    try:
        _store_contact_request(payload)
        lead_id = _create_contact_lead(payload)

        db = SessionLocal()
        try:
            _, org_settings = _get_default_org_settings(db)
            alert_email = org_settings.owner_alert_email if org_settings else settings.owner_alert_email
            if smtp_configured(org_settings):
                send_email(
                    to_email=alert_email,
                    subject=f"LeadRelay demo request — {payload['company']}",
                    body=(
                        f"Lead ID: {lead_id}\n"
                        f"Name: {payload['name']}\n"
                        f"Company: {payload['company']}\n"
                        f"Email: {payload['email']}\n"
                        f"Phone: {payload['phone'] or 'n/a'}\n\n"
                        f"Message:\n{payload['message'] or '(none)'}\n"
                    ),
                    org_settings=org_settings,
                )
        finally:
            db.close()

        return RedirectResponse(url="/book-demo?status=success", status_code=303)
    except Exception:  # noqa: BLE001
        logging.exception("Failed to save contact form submission")
        return RedirectResponse(url="/book-demo?status=error", status_code=303)


@app.post("/mailbox/poll")
def mailbox_poll(
    user: User = Depends(get_current_user),
    org_settings: OrgSettings = Depends(get_org_settings),
    db: Session = Depends(get_db),
):
    result = poll_inbox(db, org_id=user.org_id, org_settings=org_settings)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "Mailbox poll failed"))
    return result


@app.get("/login", include_in_schema=False)
def login_page():
    return FileResponse(UI_DIR / "login.html")


@app.get("/forgot-password", include_in_schema=False)
def forgot_password_page():
    return FileResponse(UI_DIR / "forgot-password.html")


@app.get("/reset-password", include_in_schema=False)
def reset_password_page():
    return FileResponse(UI_DIR / "reset-password.html")


@app.get("/register", include_in_schema=False)
def register_page():
    return FileResponse(UI_DIR / "register.html")


@app.get("/review", include_in_schema=False)
def review_page():
    return FileResponse(UI_DIR / "index.html")


@app.get("/analytics", include_in_schema=False)
def analytics_page():
    return FileResponse(UI_DIR / "analytics.html")


@app.get("/setup", include_in_schema=False)
def setup_page():
    return FileResponse(UI_DIR / "setup.html")


@app.get("/settings", include_in_schema=False)
def settings_page():
    return FileResponse(UI_DIR / "settings.html")


@app.get("/book/{token}", include_in_schema=False)
def booking_page(token: str):
    return FileResponse(UI_DIR / "book.html")


@app.get("/health", response_model=HealthResponse)
@limiter.limit("60/minute")
def health(request: Request):
    return HealthResponse(ok=True, app=settings.app_name)
