import asyncio
import json
import logging
import os
import re
import secrets
import xml.sax.saxutils as xml_escape
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.sessions import SessionMiddleware

from app.auth import get_current_user, get_org_from_session_or_api_key, get_org_settings, org_can_use_automation
from app.billing import billing_enabled, org_has_billing_access
from app.classifier import classify_demo_lead, classify_lead
from app.config import settings
from app.database import Base, SessionLocal, engine, get_db
from app.email_parser import parse_forwarded_email
from app.followups import run_followups
from app.review_requests import run_due_review_requests
from app.inbox_poll import poll_inbox
from app.mailer import send_email, smtp_configured
from app.models import Lead, Organization, OrgSettings, User
from app.routes.auth import router as auth_router
from app.routes.billing import router as billing_router, webhook_router as stripe_webhook_router
from app.routes.google_oauth import router as google_oauth_router
from app.routes.leads import ingest_lead, router as lead_router
from app.routes.settings import router as settings_router
from app.routes.scheduling import router as scheduling_router
from app.routes.sms_webhook import router as sms_router
from app.routes.twilio_voice import router as twilio_voice_router
from app.routes.phone_provisioning import router as phone_provisioning_router
from app.routes.widget import router as widget_router
from app.schemas import DemoInboundRequest, DemoInboundResponse, DemoLeadRequest, DemoLeadResponse, ForwardedEmailIngestRequest, HealthResponse, LeadIngestRequest
from app.sms import send_sms_to
from app.twilio_signature import verify_twilio_signature
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

if settings.app_env != "production":
    Base.metadata.create_all(bind=engine)


FLUSH_INTERVAL_SECONDS = 30
FOLLOWUP_INTERVAL_SECONDS = 300
REVIEW_REQUEST_INTERVAL_SECONDS = 600


def _validate_startup_settings() -> None:
    if settings.app_env != "production":
        return
    errors = []
    if not settings.public_base_url or settings.public_base_url.startswith("http://127.0.0.1"):
        errors.append("PUBLIC_BASE_URL must be set to the production URL")
    if not settings.session_secret or settings.session_secret == "change-me-to-random-secret":
        errors.append("SESSION_SECRET must be set to a random production secret")
    if settings.llm_provider == "anthropic" and not settings.llm_api_key:
        errors.append("LLM_API_KEY is required when LLM_PROVIDER=anthropic")
    if errors:
        raise RuntimeError("; ".join(errors))


_validate_startup_settings()


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
        org = db.query(Organization).filter(Organization.id == org_id).first()
        if not org_can_use_automation(org, org_settings):
            logger.info("Pending send flush skipped for org_id=%s", org_id)
            continue
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
            if tick % REVIEW_REQUEST_INTERVAL_SECONDS == 0:
                orgs = db.query(Organization).all()
                for org in orgs:
                    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org.id).first()
                    run_due_review_requests(db, org_id=org.id, org_settings=org_settings)
        except Exception:
            logger.exception("Scheduler tick failed")
        finally:
            db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_scheduler_loop())
    logger.info(
        "Background scheduler started (flush=%ds, followups=%ds, reviews=%ds)",
        FLUSH_INTERVAL_SECONDS, FOLLOWUP_INTERVAL_SECONDS, REVIEW_REQUEST_INTERVAL_SECONDS,
    )
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


BILLING_GATED_PATHS = {
    "/review",
    "/analytics",
    "/pipeline",
    "/templates",
    "/mailbox/poll",
    "/forwarded-email",
    "/ingest-lead",
    "/flush-pending",
    "/daily-digest",
    "/weekly-summary",
}
BILLING_GATED_PREFIXES = (
    "/leads",
    "/stats",
    "/threads",
    "/api/lead-map",
    "/api/templates",
    "/api/pipeline",
    "/api/review-requests",
    "/api/schedule",
    "/api/phone",
)


def _is_billing_gated_path(path: str) -> bool:
    return path in BILLING_GATED_PATHS or any(path.startswith(prefix) for prefix in BILLING_GATED_PREFIXES)


def _expects_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept and "application/json" not in accept


def _session_cookie_https_only() -> bool:
    return settings.app_env == "production"


@app.middleware("http")
async def billing_access_middleware(request: Request, call_next):
    if not billing_enabled() or request.method == "OPTIONS" or not _is_billing_gated_path(request.url.path):
        return await call_next(request)

    user_id = request.session.get("user_id")
    if not user_id:
        return await call_next(request)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
        if user and not org_has_billing_access(user.org):
            if _expects_html(request):
                return RedirectResponse(url="/settings?billing=required", status_code=303)
            return JSONResponse(
                status_code=402,
                content={
                    "detail": "Billing required",
                    "billing_required": True,
                    "subscription_status": user.org.subscription_status,
                },
            )
    finally:
        db.close()

    return await call_next(request)


app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    same_site="lax",
    https_only=_session_cookie_https_only(),
)

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
    # Trust the configured domain plus Railway's healthcheck/internal hosts
    primary_host = settings.public_base_url.replace("https://", "").replace("http://", "").split(":")[0]
    allowed_hosts = [
        primary_host,
        "healthcheck.railway.app",
        "*.up.railway.app",
        "*.railway.app",
        "*.railway.internal",
    ]
    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN")
    if railway_domain:
        allowed_hosts.append(railway_domain)
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
app.include_router(billing_router)
app.include_router(stripe_webhook_router)
app.include_router(google_oauth_router)
app.include_router(lead_router)
app.include_router(scheduling_router)
app.include_router(settings_router)
app.include_router(sms_router)
app.include_router(twilio_voice_router)
app.include_router(phone_provisioning_router)
app.include_router(widget_router)

UI_DIR = Path(__file__).resolve().parent / "ui"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONTACT_REQUESTS_PATH = DATA_DIR / "contact_requests.jsonl"
DEMO_LEADS_PATH = DATA_DIR / "demo_leads.jsonl"
app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")


def _contact_status_markup(status: str | None) -> str:
    if status == "success":
        return '<div class="demo-alert demo-alert-success">Thanks - demo request received. We saved it and routed it for follow-up.</div>'
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


def _store_demo_record(record: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with DEMO_LEADS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def _load_demo_record(demo_id: str) -> dict | None:
    if not DEMO_LEADS_PATH.exists():
        return None
    # JSONL stays tiny for this demo workflow. Walk backwards for recent IDs.
    lines = DEMO_LEADS_PATH.read_text(encoding="utf-8").splitlines()
    for line in reversed(lines[-500:]):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("demo_id") == demo_id:
            return record
    return None


def _demo_contact_payload(request: Request) -> dict[str, str | bool]:
    base = str(request.base_url).rstrip("/")
    return {
        "enabled": bool(settings.demo_inbox_email or settings.demo_phone_number),
        "demo_inbox_email": settings.demo_inbox_email,
        "demo_phone_number": settings.demo_phone_number,
        "demo_inbound_url": f"{base}/api/demo/inbound",
        "demo_sms_webhook_url": f"{base}/demo/sms/webhook",
        "demo_voice_webhook_url": f"{base}/demo/voice/incoming",
    }


def _demo_token_valid(token: str | None) -> bool:
    if settings.demo_forwarding_token:
        return secrets.compare_digest(token or "", settings.demo_forwarding_token)
    return settings.app_env != "production"


def _normalize_public_phone(raw: str | None) -> str:
    return re.sub(r"[^\d+]", "", raw or "")


def _ingest_demo_inbound(
    request: Request,
    payload: DemoInboundRequest,
    *,
    require_token: bool = True,
) -> DemoInboundResponse:
    if require_token and not _demo_token_valid(payload.token):
        raise HTTPException(status_code=401, detail="Invalid demo forwarding token")

    sender_name = (payload.from_name or "").strip() or "Demo customer"
    sender_email = str(payload.from_email or "demo-customer@example.com")
    subject = (payload.subject or "").strip() or f"{(payload.trade or 'service').title()} demo lead"
    demo_payload = DemoLeadRequest(
        sender_name=sender_name,
        sender_email=sender_email,
        phone=payload.from_phone,
        trade=payload.trade,
        subject=subject,
        lead_text=payload.body,
    )
    result = _build_demo_response(request, demo_payload)
    demo_id = secrets.token_urlsafe(8)
    demo_url = f"{str(request.base_url).rstrip('/')}/demo?lead={demo_id}"
    record = {
        "demo_id": demo_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": payload.source,
        "from_name": sender_name,
        "from_email": str(payload.from_email or ""),
        "from_phone": payload.from_phone or "",
        "subject": subject,
        "body": payload.body,
        "trade": payload.trade or "",
        "demo_url": demo_url,
        "result": result.model_dump(),
    }
    _store_demo_record(record)
    return DemoInboundResponse(ok=True, demo_id=demo_id, demo_url=demo_url, result=result)


def _demo_twiml(message: str | None = None) -> Response:
    if message is None:
        xml = '<?xml version="1.0" encoding="UTF-8"?><Response/>'
    else:
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f"<Response><Message>{xml_escape.escape(message)}</Message></Response>"
        )
    return Response(content=xml, media_type="application/xml")


def _demo_voice_twiml(message: str) -> Response:
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response>"
        f'<Say voice="alice">{xml_escape.escape(message)}</Say>'
        '<Pause length="1"/>'
        '<Say voice="alice">Thanks for trying Relinqo. Goodbye.</Say>'
        "</Response>"
    )
    return Response(content=xml, media_type="application/xml")


def _get_default_org_settings(db: Session) -> tuple[int, OrgSettings | None]:
    """Load the default org (id=1) settings for platform-level actions."""
    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == 1).first()
    return 1, org_settings


def _compact_demo_text(text: str, max_length: int = 130) -> str:
    compact = " ".join(text.split())
    if len(compact) <= max_length:
        return compact
    return compact[: max_length - 3].rstrip() + "..."


def _demo_category_label(category: str) -> str:
    labels = {
        "urgent_request": "Urgent job",
        "quote_request": "Quote request",
        "existing_customer": "Existing customer",
        "general_inquiry": "General inquiry",
        "spam": "Filtered spam",
    }
    return labels.get(category, category.replace("_", " ").title())


def _demo_urgency_label(score: int) -> str:
    if score >= 5:
        return "Emergency"
    if score >= 4:
        return "Hot lead"
    if score >= 3:
        return "Warm lead"
    return "Normal"


def _demo_pipeline_stage(category: str) -> str:
    stages = {
        "urgent_request": "New -> Contacted",
        "quote_request": "New -> Quoted",
        "existing_customer": "Contacted",
        "general_inquiry": "New",
        "spam": "Filtered",
    }
    return stages.get(category, "New")


def _demo_next_step_label(category: str) -> str:
    steps = {
        "urgent_request": "Alert owner now and ask customer for address, callback, and safe photos.",
        "quote_request": "Send quote-intake reply and offer a booking link.",
        "existing_customer": "Review account context before replying.",
        "general_inquiry": "Ask for service details and preferred timeline.",
        "spam": "Keep out of the active lead queue.",
    }
    return steps.get(category, "Review the lead and choose the next action.")


def _demo_timeline(category: str, owner_alert_needed: bool) -> list[dict[str, str]]:
    if category == "spam":
        return [
            {"label": "Captured", "detail": "Message received by the demo inbox."},
            {"label": "Filtered", "detail": "Spam risk detected, so it stays out of the active queue."},
        ]

    timeline = [
        {"label": "Captured", "detail": "Lead enters the Relinqo queue from email, form, phone, or SMS."},
        {"label": "Classified", "detail": "Urgency, job type, spam risk, and next action are assigned."},
        {"label": "Reply drafted", "detail": "A customer-ready response is prepared for review or autopilot."},
    ]
    if owner_alert_needed:
        timeline.append({"label": "Owner alerted", "detail": "Hot jobs get a concise SMS-style owner alert."})
    timeline.extend([
        {"label": "Tracked", "detail": "The lead moves through pipeline, booking, revenue, and notes."},
        {"label": "Review loop", "detail": "Won jobs can trigger a Google review request later."},
    ])
    return timeline


def _build_demo_response(request: Request, payload: DemoLeadRequest) -> DemoLeadResponse:
    lead_text = payload.lead_text.strip()
    if len(lead_text) < 12:
        raise HTTPException(status_code=400, detail="Demo lead text must be at least 12 characters.")

    sender_name = (payload.sender_name or "").strip() or "Daniel"
    sender_email = str(payload.sender_email or "demo-customer@example.com")
    phone = (payload.phone or "").strip() or "+1 780 555 0134"
    trade = (payload.trade or "").strip() or "plumbing"
    subject = (payload.subject or "").strip() or f"{trade.title()} lead from {sender_name}"
    booking_url = f"{str(request.base_url).rstrip('/')}/book/demo-preview"

    classification = classify_demo_lead(
        {
            "source": "live_demo",
            "sender_name": sender_name,
            "sender_email": sender_email,
            "subject": subject,
            "body": lead_text,
            "phone": phone,
            "location": None,
        },
        booking_url=booking_url,
    )
    lead_snippet = _compact_demo_text(lead_text, 110)
    owner_alert_prefix = "URGENT" if classification.owner_alert_needed else "NEW"
    owner_alert_preview = (
        f"[DEMO] {owner_alert_prefix} {trade} lead from {sender_name}: "
        f"{lead_snippet}. Call {phone}."
    )

    return DemoLeadResponse(
        ok=True,
        category=classification.category,
        category_label=_demo_category_label(classification.category),
        urgency_score=classification.urgency_score,
        urgency_label=_demo_urgency_label(classification.urgency_score),
        confidence=classification.confidence,
        summary=classification.summary,
        recommended_reply=classification.recommended_reply,
        owner_alert_needed=classification.owner_alert_needed,
        owner_alert_preview=owner_alert_preview,
        pipeline_stage=_demo_pipeline_stage(classification.category),
        next_step_label=_demo_next_step_label(classification.category),
        booking_url=booking_url,
        review_followup_preview=(
            "If this lead is marked Won, Relinqo can queue a review request for the configured delay."
            if classification.category != "spam"
            else "No review request is queued for filtered spam."
        ),
        timeline=_demo_timeline(classification.category, classification.owner_alert_needed),
    )


def _create_contact_lead(payload: dict[str, str]) -> int:
    body = (
        f"Demo request from {payload['name']} at {payload['company']}. "
        f"Email: {payload['email']}. "
        f"Phone: {payload['phone'] or 'not provided'}. "
        f"Business type: {payload.get('business_type') or 'not provided'}. "
        f"Biggest leak: {payload.get('lead_source') or 'not provided'}. "
        f"Current response time: {payload.get('current_response_time') or 'not provided'}. "
        f"Message: {payload['message'] or 'No message provided.'}"
    )
    subject = f"Book a demo - {payload['company']}"

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
    return FileResponse(UI_DIR / "marketing.html", headers={"Cache-Control": "no-store"})


@app.get("/book-demo", include_in_schema=False)
@limiter.limit("30/minute")
def book_demo_page(request: Request, status: str | None = None):
    html = (UI_DIR / "book-demo.html").read_text(encoding="utf-8")
    html = html.replace("{{CONTACT_STATUS}}", _contact_status_markup(status))
    return HTMLResponse(html)


@app.get("/demo", include_in_schema=False)
@limiter.limit("30/minute")
def live_demo_page(request: Request):
    return FileResponse(UI_DIR / "live-demo.html", headers={"Cache-Control": "no-store"})


@app.get("/website-widget", include_in_schema=False)
@limiter.limit("30/minute")
def website_widget_page(request: Request):
    return FileResponse(UI_DIR / "website-widget.html", headers={"Cache-Control": "no-store"})


@app.post("/api/demo/lead", response_model=DemoLeadResponse)
@limiter.limit("20/minute")
def run_live_demo(request: Request, payload: DemoLeadRequest):
    return _build_demo_response(request, payload)


@app.get("/api/demo/config")
@limiter.limit("30/minute")
def demo_config(request: Request):
    return _demo_contact_payload(request)


@app.post("/api/demo/inbound", response_model=DemoInboundResponse)
@limiter.limit("20/minute")
def demo_inbound(request: Request, payload: DemoInboundRequest):
    return _ingest_demo_inbound(request, payload)


@app.get("/api/demo/leads/{demo_id}")
@limiter.limit("60/minute")
def demo_lead_by_id(request: Request, demo_id: str):
    record = _load_demo_record(demo_id)
    if not record:
        raise HTTPException(status_code=404, detail="Demo lead not found")
    return record


@app.post("/demo/sms/webhook", dependencies=[Depends(verify_twilio_signature)])
@limiter.limit("30/minute")
async def demo_sms_webhook(
    request: Request,
    Body: str = Form(""),
    From: str = Form(""),
    To: str = Form(""),
):
    if settings.demo_phone_number and _normalize_public_phone(To) != _normalize_public_phone(settings.demo_phone_number):
        return _demo_twiml()

    body = Body.strip() or "Demo SMS lead with no message body."
    if len(body) < 12:
        body = f"{body} - customer is testing the Relinqo demo phone number."
    payload = DemoInboundRequest(
        source="sms",
        from_phone=From,
        subject="Demo SMS lead",
        body=body,
    )
    result = _ingest_demo_inbound(request, payload, require_token=False)
    return _demo_twiml(f"Got it. Relinqo demo created: {result.demo_url}")


@app.post("/demo/voice/incoming", dependencies=[Depends(verify_twilio_signature)])
@limiter.limit("30/minute")
async def demo_voice_incoming(
    request: Request,
    From: str = Form(""),
    To: str = Form(""),
    CallSid: str = Form(""),
):
    if settings.demo_phone_number and _normalize_public_phone(To) != _normalize_public_phone(settings.demo_phone_number):
        return _demo_voice_twiml("This Relinqo demo number is not configured. Please check the phone number and try again.")

    caller = From or "unknown caller"
    payload = DemoInboundRequest(
        source="voice",
        from_phone=From,
        subject="Demo phone call",
        body=(
            f"Caller {caller} dialed the Relinqo demo phone number. "
            "They want to see how a missed call becomes a lead, owner alert, and follow-up workflow."
        ),
    )
    result = _ingest_demo_inbound(request, payload, require_token=False)
    sms_sent = False
    if From and To:
        sms_body = (
            "Thanks for calling the Relinqo demo. Watch your call become a lead here: "
            f"{result.demo_url}"
        )
        sms_sent, _, _ = send_sms_to(sms_body, From, None, from_number=To)

    if sms_sent:
        message = "Thanks for calling the Relinqo demo. I just texted you a link that shows this call becoming a lead."
    else:
        message = f"Thanks for calling the Relinqo demo. Open {result.demo_url} to see this call become a lead."
    if CallSid:
        logger.info("demo_voice call_sid=%s from=%s sms_sent=%s demo_id=%s", CallSid, From, sms_sent, result.demo_id)
    return _demo_voice_twiml(message)


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
    business_type: str = Form(""),
    lead_source: str = Form(""),
    current_response_time: str = Form(""),
    message: str = Form(""),
):
    payload = {
        "name": name.strip(),
        "company": company.strip(),
        "email": email.strip(),
        "phone": phone.strip(),
        "business_type": business_type.strip(),
        "lead_source": lead_source.strip(),
        "current_response_time": current_response_time.strip(),
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
                    subject=f"relinqo demo request - {payload['company']}",
                    body=(
                        f"Lead ID: {lead_id}\n"
                        f"Name: {payload['name']}\n"
                        f"Company: {payload['company']}\n"
                        f"Email: {payload['email']}\n"
                        f"Phone: {payload['phone'] or 'n/a'}\n\n"
                        f"Business type: {payload['business_type'] or 'n/a'}\n"
                        f"Biggest leak: {payload['lead_source'] or 'n/a'}\n"
                        f"Current response time: {payload['current_response_time'] or 'n/a'}\n\n"
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


@app.get("/contact-email", include_in_schema=False)
def contact_email():
    query = urlencode(
        {
            "view": "cm",
            "fs": "1",
            "tf": "1",
            "to": "reesemedele@gmail.com",
            "su": "relinqo question",
            "body": "Hi Reese,\n\nI have a question about relinqo.",
        }
    )
    return RedirectResponse(url=f"https://mail.google.com/mail/?{query}", status_code=302)


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
def review_page(user: User = Depends(get_current_user)):
    return FileResponse(UI_DIR / "index.html")


@app.get("/analytics", include_in_schema=False)
def analytics_page(user: User = Depends(get_current_user)):
    return FileResponse(UI_DIR / "analytics.html")


@app.get("/pipeline", include_in_schema=False)
def pipeline_page(user: User = Depends(get_current_user)):
    return FileResponse(UI_DIR / "pipeline.html")


@app.get("/templates", include_in_schema=False)
def templates_page(user: User = Depends(get_current_user)):
    return FileResponse(UI_DIR / "templates.html")


@app.get("/setup", include_in_schema=False)
def setup_page(user: User = Depends(get_current_user)):
    return FileResponse(UI_DIR / "setup.html")


@app.get("/settings", include_in_schema=False)
def settings_page(user: User = Depends(get_current_user)):
    return FileResponse(UI_DIR / "settings.html")


@app.get("/book/{token}", include_in_schema=False)
def booking_page(token: str):
    return FileResponse(UI_DIR / "book.html")


@app.get("/health", response_model=HealthResponse)
@limiter.limit("60/minute")
def health(request: Request):
    return HealthResponse(ok=True, app=settings.app_name)
