import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.alerts import send_owner_alert, send_sms_approval_request
from app.auth import get_current_user, get_org_settings
from app.classifier import classify_lead
from app.config import settings
from app.database import get_db
from app.digest import build_daily_digest, build_weekly_summary, send_daily_digest, send_weekly_summary
from app.email_parser import parse_lead_fields
from app.followups import schedule_followups
from app.mailer import send_email
from app.models import Lead, LeadActivity, LeadNote, LeadPhoto, OrgSettings, ReplyTemplate, ScheduleAvailability, User
from app.schemas import (
    DigestResponse,
    LeadActivityResponse,
    LeadIngestRequest,
    LeadNoteCreateRequest,
    LeadNoteResponse,
    LeadNoteUpdateRequest,
    LeadOutcomeRequest,
    LeadPhotoResponse,
    LeadResponse,
    LeadUpdateRequest,
    PaginatedLeadsResponse,
    ReplyTemplateCreateRequest,
    ReplyTemplateResponse,
    ReplyTemplateUpdateRequest,
    SendReviewResponse,
    StatsResponse,
)

PHOTOS_BASE_DIR = Path(__file__).resolve().parent.parent / "data" / "photos"
MIME_TO_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

router = APIRouter()
logger = logging.getLogger(__name__)


def _scoped_lead(db: Session, lead_id: int, org_id: int) -> Lead:
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.org_id == org_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


def log_activity(db: Session, lead_id: int, org_id: int, activity_type: str, message: str) -> None:
    db.add(LeadActivity(org_id=org_id, lead_id=lead_id, activity_type=activity_type, message=message))
    db.commit()


UNDO_SEND_SECONDS = 60


def _make_thread_id(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]


def _save_lead_photos(
    db: Session, lead: Lead, org_id: int, attachments: list[dict], ai_analysis: str | None = None,
) -> list[LeadPhoto]:
    """Save photo attachments to disk and create LeadPhoto records."""
    photo_dir = PHOTOS_BASE_DIR / str(org_id) / str(lead.id)
    photo_dir.mkdir(parents=True, exist_ok=True)
    photos = []
    for att in attachments:
        ext = MIME_TO_EXT.get(att["mime_type"], "jpg")
        stored_name = f"{uuid4().hex}.{ext}"
        file_path = photo_dir / stored_name
        file_path.write_bytes(att["data"])
        photo = LeadPhoto(
            org_id=org_id,
            lead_id=lead.id,
            filename=att["filename"],
            stored_filename=stored_name,
            file_path=str(file_path),
            file_size=len(att["data"]),
            mime_type=att["mime_type"],
            ai_analysis=ai_analysis,
        )
        db.add(photo)
        photos.append(photo)
    db.commit()
    return photos


def ingest_lead(
    payload: LeadIngestRequest,
    db: Session,
    org_id: int | None = None,
    org_settings: OrgSettings | None = None,
    attachments: list[dict] | None = None,
) -> Lead:
    """Core lead ingestion logic. Called from routes and background tasks."""
    parsed = parse_lead_fields(payload.model_dump())
    enriched = {**payload.model_dump(), **parsed}

    # Prepare booking URL if scheduling is enabled
    booking_token = None
    booking_url = None
    if org_settings and org_settings.scheduling_enabled:
        has_availability = db.query(ScheduleAvailability).filter(
            ScheduleAvailability.org_id == (org_id or 1),
            ScheduleAvailability.is_active == True,
        ).first()
        if has_availability:
            booking_token = secrets.token_urlsafe(32)
            booking_url = f"{settings.public_base_url}/book/{booking_token}"

    # Prepare images for vision analysis
    images = None
    if attachments:
        images = [{"data": a["data"], "mime_type": a["mime_type"]} for a in attachments]

    classification = classify_lead(
        enriched, org_settings=org_settings, images=images, booking_url=booking_url,
    )

    phone = classification.extracted_phone or enriched.get("phone")
    location = classification.extracted_location or enriched.get("location")

    human_review = org_settings.human_review if org_settings else True
    auto_threshold = org_settings.auto_send_confidence_threshold if org_settings else 0.85

    status = "spam" if classification.category == "spam" else "drafted" if human_review else "ready_to_send"
    thread_id = _make_thread_id(payload.sender_email)

    # Detect if this is a reply to an existing conversation
    parent_lead_id = None
    subject_lower = (payload.subject or "").lower()
    is_reply = subject_lower.startswith("re:") or subject_lower.startswith("fwd:")
    if is_reply:
        parent = (
            db.query(Lead)
            .filter(Lead.org_id == (org_id or 1), Lead.thread_id == thread_id)
            .order_by(Lead.created_at.desc())
            .first()
        )
        if parent:
            parent_lead_id = parent.id
            log_activity(db, parent.id, parent.org_id, "customer_replied", f"Customer replied: {payload.subject or '(no subject)'}")

    # Enrich summary with photo analysis if available
    summary = classification.summary
    if classification.photo_analysis:
        summary = f"{summary}\n\nPhoto analysis: {classification.photo_analysis}"

    lead = Lead(
        org_id=org_id or 1,
        source=payload.source,
        sender_name=enriched.get("sender_name"),
        sender_email=payload.sender_email,
        subject=payload.subject,
        body=payload.body,
        phone=phone,
        location=location,
        category=classification.category,
        urgency_score=classification.urgency_score,
        summary=summary,
        recommended_reply=classification.recommended_reply,
        owner_alert_needed=classification.owner_alert_needed,
        status=status,
        confidence=classification.confidence,
        next_step=classification.next_step,
        raw_payload=json.dumps(payload.model_dump()),
        thread_id=thread_id,
        parent_lead_id=parent_lead_id,
        booking_token=booking_token,
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)

    # Save photo attachments to disk
    if attachments:
        _save_lead_photos(db, lead, lead.org_id, attachments, ai_analysis=classification.photo_analysis)
        log_activity(db, lead.id, lead.org_id, "photos_attached", f"{len(attachments)} photo(s) attached and analyzed.")

    if lead.owner_alert_needed:
        send_owner_alert(db, lead, org_settings=org_settings)

    log_activity(db, lead.id, lead.org_id, "ingested", f"Lead created from {lead.source} with status {lead.status}.")

    can_auto_send = (
        not human_review
        and lead.category not in {"spam", "urgent_request"}
        and lead.confidence >= auto_threshold
        and lead.recommended_reply
    )
    if can_auto_send:
        lead.send_at = datetime.now(timezone.utc) + timedelta(seconds=UNDO_SEND_SECONDS)
        lead.status = "pending_send"
        db.commit()
        log_activity(db, lead.id, lead.org_id, "pending_send", f"Auto-reply scheduled in {UNDO_SEND_SECONDS}s — cancel before {lead.send_at.strftime('%H:%M:%S UTC')}")
        logger.info("Scheduled auto-send lead_id=%s at %s", lead.id, lead.send_at)

    # Send SMS approval request for drafted leads when human review is on
    if lead.status == "drafted" and lead.category != "spam":
        send_sms_approval_request(db, lead, org_settings=org_settings)

    schedule_followups(db, lead)
    logger.info("Ingested lead_id=%s category=%s urgency=%s", lead.id, lead.category, lead.urgency_score)
    return lead


# --- Authenticated route endpoints ---


@router.post("/ingest-lead", response_model=LeadResponse)
def ingest_lead_route(
    payload: LeadIngestRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    org_settings: OrgSettings = Depends(get_org_settings),
):
    return ingest_lead(payload, db, org_id=user.org_id, org_settings=org_settings)


@router.get("/leads", response_model=PaginatedLeadsResponse)
def list_leads(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    total = db.query(sa_func.count(Lead.id)).filter(Lead.org_id == user.org_id).scalar() or 0
    items = (
        db.query(Lead)
        .filter(Lead.org_id == user.org_id)
        .order_by(Lead.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    pages = (total + page_size - 1) // page_size if total else 1
    return PaginatedLeadsResponse(items=items, total=total, page=page, page_size=page_size, pages=pages)


@router.get("/leads/{lead_id}", response_model=LeadResponse)
def get_lead(lead_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _scoped_lead(db, lead_id, user.org_id)


@router.get("/leads/{lead_id}/photos", response_model=list[LeadPhotoResponse])
def list_lead_photos(lead_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _scoped_lead(db, lead_id, user.org_id)
    return db.query(LeadPhoto).filter(LeadPhoto.lead_id == lead_id, LeadPhoto.org_id == user.org_id).order_by(LeadPhoto.created_at.asc()).all()


@router.get("/leads/{lead_id}/photos/{photo_id}")
def get_lead_photo(lead_id: int, photo_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _scoped_lead(db, lead_id, user.org_id)
    photo = db.query(LeadPhoto).filter(LeadPhoto.id == photo_id, LeadPhoto.lead_id == lead_id, LeadPhoto.org_id == user.org_id).first()
    if not photo:
        raise HTTPException(status_code=404, detail="Photo not found")
    from fastapi.responses import FileResponse
    return FileResponse(photo.file_path, media_type=photo.mime_type)


@router.post("/leads/{lead_id}/review/send", response_model=SendReviewResponse)
def send_review_reply(
    lead_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    org_settings: OrgSettings = Depends(get_org_settings),
):
    lead = _scoped_lead(db, lead_id, user.org_id)
    if lead.category == "spam" and lead.source != "setup_wizard":
        raise HTTPException(status_code=400, detail="Spam leads are not sendable")

    subject = f"Re: {lead.subject or 'Your inquiry'}"
    sent, message = send_email(
        to_email=lead.sender_email,
        subject=subject,
        body=lead.recommended_reply or "",
        org_settings=org_settings,
    )
    lead.status = "sent" if sent else "send_failed"
    db.commit()
    log_activity(db, lead.id, lead.org_id, "reply_sent" if sent else "reply_failed", (f"Manual review send to {lead.sender_email}" if sent else f"Manual review send failed: {message}"))
    return SendReviewResponse(
        lead_id=lead.id,
        status=lead.status,
        sent=sent,
        message=(f"Reply sent to {lead.sender_email}" if sent else f"SMTP send failed: {message}"),
        sent_to=lead.sender_email,
        subject=subject,
    )


@router.get("/leads/{lead_id}/activities", response_model=list[LeadActivityResponse])
def list_lead_activities(lead_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _scoped_lead(db, lead_id, user.org_id)
    return db.query(LeadActivity).filter(LeadActivity.lead_id == lead_id, LeadActivity.org_id == user.org_id).order_by(LeadActivity.created_at.desc(), LeadActivity.id.desc()).all()


@router.patch("/leads/{lead_id}", response_model=LeadResponse)
def update_lead(lead_id: int, payload: LeadUpdateRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    lead = _scoped_lead(db, lead_id, user.org_id)

    changes = []
    for field in ["subject", "body", "recommended_reply", "status", "next_step", "deal_value", "tags", "pipeline_stage", "starred"]:
        value = getattr(payload, field)
        if value is not None:
            setattr(lead, field, value)
            changes.append(field)

    # Mirror pipeline_stage -> outcome when entering terminal stages
    if payload.pipeline_stage in {"won", "lost"} and lead.outcome != payload.pipeline_stage:
        lead.outcome = payload.pipeline_stage
        lead.outcome_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(lead)
    if changes:
        log_activity(db, lead.id, lead.org_id, "lead_updated", f"Updated fields: {', '.join(changes)}")
    return lead


@router.delete("/leads/{lead_id}")
def delete_lead(lead_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    lead = _scoped_lead(db, lead_id, user.org_id)
    db.delete(lead)
    db.commit()
    return {"ok": True, "deleted_id": lead_id}


VALID_OUTCOMES = {"won", "lost", "no_response"}


@router.post("/leads/{lead_id}/outcome", response_model=LeadResponse)
def set_lead_outcome(
    lead_id: int,
    payload: LeadOutcomeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if payload.outcome not in VALID_OUTCOMES:
        raise HTTPException(status_code=400, detail=f"Outcome must be one of: {', '.join(VALID_OUTCOMES)}")
    lead = _scoped_lead(db, lead_id, user.org_id)
    lead.outcome = payload.outcome
    lead.outcome_notes = payload.outcome_notes
    lead.outcome_at = datetime.now(timezone.utc)
    if payload.outcome in {"won", "lost"}:
        lead.pipeline_stage = payload.outcome
    db.commit()
    db.refresh(lead)
    log_activity(db, lead.id, lead.org_id, "outcome_set", f"Outcome set to '{payload.outcome}'" + (f": {payload.outcome_notes}" if payload.outcome_notes else ""))
    return lead


@router.post("/demo/seed")
def seed_demo_leads(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    org_settings: OrgSettings = Depends(get_org_settings),
):
    from app.config import settings
    if settings.app_env != "development":
        raise HTTPException(status_code=403, detail="Demo seeding is only available in development mode")
    payloads = [
        {"source": "website_form", "sender_name": "Megan Foster", "sender_email": "megan.foster82@gmail.com", "subject": "Basement sump issue", "body": "Our sump pump quit and water is collecting near the furnace in Sherwood Park. Call me at 780-555-0142."},
        {"source": "google_ads", "sender_name": "Daniel Ruiz", "sender_email": "daniel.ruiz.home@gmail.com", "subject": "Quote for hot water tank replacement", "body": "Need quote to replace a 50 gallon hot water tank in Edmonton. My number is 587-555-0188."},
        {"source": "email", "sender_name": "Alyssa Reed", "sender_email": "alyssa@reedpm.ca", "subject": "Furnace blowing cold air", "body": "Tenant says furnace is blowing cold air in our St. Albert duplex. Need morning service."},
        {"source": "facebook", "sender_name": "Noah Patel", "sender_email": "npatel1989@gmail.com", "subject": "AC stopped cooling", "body": "AC fan runs but house stays warm in Edmonton. Can someone come this week?"},
        {"source": "web_chat", "sender_name": "Jamie Lee", "sender_email": "jamielee.homeowner@gmail.com", "subject": "Breaker keeps tripping", "body": "Half the kitchen outlets lost power in Leduc and the breaker keeps tripping."},
        {"source": "website_form", "sender_name": "Kara Mitchell", "sender_email": "kara.mitchell23@yahoo.com", "subject": "Exterior lights estimate", "body": "Need estimate in Spruce Grove for two exterior fixtures and a bathroom timer replacement."},
        {"source": "thumbtack", "sender_name": "Brent Holloway", "sender_email": "brentholloway@outlook.com", "subject": "Missing shingles after wind", "body": "Missing shingles on detached home in Fort Saskatchewan. No active leak yet."},
        {"source": "email", "sender_name": "Priya Nair", "sender_email": "priya@mapledaycare.ca", "subject": "Roof leak over classroom", "body": "Small active leak around a roof vent over one classroom in Edmonton. Need assessment before snowfall."},
        {"source": "google_ads", "sender_name": "Chris Moreno", "sender_email": "cmoreno.family@gmail.com", "subject": "Garage door only opens a foot", "body": "Garage door opens about a foot then stops in Stony Plain. Need repair before Monday."},
        {"source": "website_form", "sender_name": "Lena Brooks", "sender_email": "lenabrooks77@gmail.com", "subject": "Insulated garage door quote", "body": "Interested in replacing our old single garage door with an insulated double in Edmonton."},
        {"source": "facebook", "sender_name": "Tyler Benson", "sender_email": "tyler.benson.home@gmail.com", "subject": "Water damage in kitchen", "body": "Dishwasher line leaked overnight in Beaumont and floor is wet. Cabinets may be affected."},
        {"source": "email", "sender_name": "Erin Walsh", "sender_email": "erin@walshholdings.ca", "subject": "Moisture check request", "body": "Minor sprinkler discharge in a storage room in Nisku last week. Looking for moisture inspection and cleanup quote."},
        {"source": "web_chat", "sender_name": "Sara Kim", "sender_email": "sarakim.home@gmail.com", "subject": "Slow kitchen drain", "body": "Kitchen sink drains slowly and gurgles when dishwasher runs in Edmonton. Not urgent."},
        {"source": "website_form", "sender_name": "Mark D'Souza", "sender_email": "mark.dsouza81@gmail.com", "subject": "Seasonal furnace tune-up", "body": "Need seasonal furnace tune-up for a townhouse in Edmonton we just moved into."},
        {"source": "google_ads", "sender_name": "Olivia Grant", "sender_email": "ogrant.homeowner@gmail.com", "subject": "Lights flicker when microwave runs", "body": "Dining room lights flicker when microwave is on in Sherwood Park. Should an electrician check this?"},
        {"source": "thumbtack", "sender_name": "Ben Carver", "sender_email": "ben.carver.roof@gmail.com", "subject": "Garage roof estimate", "body": "Need estimate for replacing asphalt shingles on a detached garage in Morinville this spring."},
        {"source": "website_form", "sender_name": "Julia Park", "sender_email": "jpark.shortnote@gmail.com", "subject": "Need help with something", "body": "Need help with something at the house in Edmonton. Not sure who handles it. Can someone call me?"},
        {"source": "email", "sender_name": "Kevin Morris", "sender_email": "kevin@rankrocketmedia.co", "subject": "Guaranteed SEO traffic", "body": "We can put your business at the top of Google in 7 days with exclusive leads and backlinks."},
    ]

    created_ids = []
    for item in payloads:
        lead = ingest_lead(LeadIngestRequest(**item), db, org_id=user.org_id, org_settings=org_settings)
        created_ids.append(lead.id)

    return {"ok": True, "seeded": len(created_ids), "lead_ids": created_ids}


@router.post("/leads/{lead_id}/cancel-send")
def cancel_send(lead_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    lead = _scoped_lead(db, lead_id, user.org_id)
    if lead.status != "pending_send":
        raise HTTPException(status_code=400, detail="Lead is not pending send")
    lead.status = "drafted"
    lead.send_at = None
    db.commit()
    log_activity(db, lead.id, lead.org_id, "send_cancelled", "Auto-send cancelled by admin.")
    return {"ok": True, "lead_id": lead.id, "status": lead.status}


@router.post("/flush-pending")
def flush_pending_sends(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    org_settings: OrgSettings = Depends(get_org_settings),
):
    """Send all leads whose send_at has passed for the current org."""
    now = datetime.now(timezone.utc)
    pending = db.query(Lead).filter(Lead.org_id == user.org_id, Lead.status == "pending_send", Lead.send_at <= now).all()
    results = []
    for lead in pending:
        sent, message = send_email(
            to_email=lead.sender_email,
            subject=f"Re: {lead.subject or 'Your inquiry'}",
            body=lead.recommended_reply or "",
            org_settings=org_settings,
        )
        lead.status = "sent" if sent else "send_failed"
        lead.send_at = None
        db.commit()
        log_activity(db, lead.id, lead.org_id, "auto_sent" if sent else "auto_send_failed",
                     f"Auto-reply {'sent to' if sent else 'failed for'} {lead.sender_email}: {message}")
        results.append({"lead_id": lead.id, "sent": sent})
    return {"ok": True, "processed": len(results), "results": results}


@router.get("/stats", response_model=StatsResponse)
def get_stats(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    org_id = user.org_id
    total = db.query(sa_func.count(Lead.id)).filter(Lead.org_id == org_id).scalar() or 0

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today = db.query(sa_func.count(Lead.id)).filter(Lead.org_id == org_id, Lead.created_at >= today_start).scalar() or 0

    sent = db.query(sa_func.count(Lead.id)).filter(Lead.org_id == org_id, Lead.status == "sent").scalar() or 0
    non_spam = db.query(sa_func.count(Lead.id)).filter(Lead.org_id == org_id, Lead.category != "spam").scalar() or 0
    response_rate = (sent / non_spam * 100) if non_spam else 0.0

    sent_leads = db.query(Lead).filter(Lead.org_id == org_id, Lead.status == "sent").all()
    if sent_leads:
        deltas = [(lead.updated_at - lead.created_at).total_seconds() / 60 for lead in sent_leads]
        avg_response = sum(deltas) / len(deltas)
    else:
        avg_response = None

    by_category = {}
    for row in db.query(Lead.category, sa_func.count(Lead.id)).filter(Lead.org_id == org_id).group_by(Lead.category).all():
        by_category[row[0]] = row[1]

    by_status = {}
    for row in db.query(Lead.status, sa_func.count(Lead.id)).filter(Lead.org_id == org_id).group_by(Lead.status).all():
        by_status[row[0]] = row[1]

    by_outcome = {}
    for row in db.query(Lead.outcome, sa_func.count(Lead.id)).filter(Lead.org_id == org_id, Lead.outcome.isnot(None)).group_by(Lead.outcome).all():
        by_outcome[row[0]] = row[1]

    outcomes_total = sum(by_outcome.values())
    won_count = by_outcome.get("won", 0)
    close_rate = round(won_count / outcomes_total * 100, 1) if outcomes_total else None

    won_leads = db.query(Lead).filter(Lead.org_id == org_id, Lead.outcome == "won", Lead.outcome_at.isnot(None)).all()
    if won_leads:
        close_deltas = [(lead.outcome_at - lead.created_at).total_seconds() / 60 for lead in won_leads]
        avg_close = round(sum(close_deltas) / len(close_deltas), 1)
    else:
        avg_close = None

    # Revenue / pipeline value
    won_revenue = (
        db.query(sa_func.coalesce(sa_func.sum(Lead.deal_value), 0.0))
        .filter(Lead.org_id == org_id, Lead.outcome == "won", Lead.deal_value.isnot(None))
        .scalar()
        or 0.0
    )
    pipeline_value = (
        db.query(sa_func.coalesce(sa_func.sum(Lead.deal_value), 0.0))
        .filter(
            Lead.org_id == org_id,
            Lead.deal_value.isnot(None),
            Lead.pipeline_stage.in_(["new", "contacted", "quoted", "scheduled"]),
            Lead.category != "spam",
        )
        .scalar()
        or 0.0
    )
    won_with_value = (
        db.query(sa_func.count(Lead.id))
        .filter(Lead.org_id == org_id, Lead.outcome == "won", Lead.deal_value.isnot(None))
        .scalar()
        or 0
    )
    avg_deal_size = round(won_revenue / won_with_value, 2) if won_with_value else None

    return StatsResponse(
        total_leads=total,
        today_leads=today,
        sent_count=sent,
        response_rate=round(response_rate, 1),
        avg_response_minutes=round(avg_response, 1) if avg_response is not None else None,
        by_category=by_category,
        by_status=by_status,
        by_outcome=by_outcome,
        close_rate=close_rate,
        avg_close_minutes=avg_close,
        won_revenue=round(float(won_revenue), 2),
        pipeline_value=round(float(pipeline_value), 2),
        avg_deal_size=avg_deal_size,
    )


@router.get("/stats/charts")
def get_chart_data(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    days: int = Query(30, ge=7, le=90),
):
    """Return time-series and breakdown data for dashboard charts."""
    org_id = user.org_id
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    leads = db.query(Lead).filter(Lead.org_id == org_id, Lead.created_at >= cutoff).all()

    # Leads per day
    daily: dict[str, dict] = {}
    for i in range(days):
        d = (now - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        daily[d] = {"date": d, "total": 0, "sent": 0, "spam": 0}

    for lead in leads:
        d = lead.created_at.strftime("%Y-%m-%d")
        if d in daily:
            daily[d]["total"] += 1
            if lead.status == "sent":
                daily[d]["sent"] += 1
            if lead.category == "spam":
                daily[d]["spam"] += 1

    # Source breakdown
    by_source: dict[str, int] = {}
    for lead in leads:
        by_source[lead.source] = by_source.get(lead.source, 0) + 1

    # Category breakdown
    by_category: dict[str, int] = {}
    for lead in leads:
        if lead.category != "spam":
            by_category[lead.category] = by_category.get(lead.category, 0) + 1

    # Outcome funnel
    outcome_counts = {"total": 0, "replied": 0, "won": 0, "lost": 0, "no_response": 0, "pending": 0}
    all_leads = db.query(Lead).filter(Lead.org_id == org_id, Lead.category != "spam").all()
    outcome_counts["total"] = len(all_leads)
    for lead in all_leads:
        if lead.status == "sent":
            outcome_counts["replied"] += 1
        if lead.outcome == "won":
            outcome_counts["won"] += 1
        elif lead.outcome == "lost":
            outcome_counts["lost"] += 1
        elif lead.outcome == "no_response":
            outcome_counts["no_response"] += 1
        elif lead.outcome is None and lead.status == "sent":
            outcome_counts["pending"] += 1

    return {
        "daily": list(daily.values()),
        "by_source": by_source,
        "by_category": by_category,
        "funnel": outcome_counts,
    }


@router.get("/stats/analytics")
def get_analytics(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    days: int = Query(30, ge=7, le=90),
):
    """Extended analytics data for the dedicated analytics dashboard."""
    org_id = user.org_id
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    leads = db.query(Lead).filter(Lead.org_id == org_id, Lead.created_at >= cutoff).all()
    all_leads = db.query(Lead).filter(Lead.org_id == org_id).all()

    # --- Response time trend (daily avg minutes) ---
    response_time_trend: dict[str, dict] = {}
    for i in range(days):
        d = (now - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        response_time_trend[d] = {"date": d, "avg_minutes": None, "count": 0}

    for lead in leads:
        if lead.status == "sent":
            d = lead.created_at.strftime("%Y-%m-%d")
            if d in response_time_trend:
                delta = (lead.updated_at - lead.created_at).total_seconds() / 60
                entry = response_time_trend[d]
                if entry["avg_minutes"] is None:
                    entry["avg_minutes"] = delta
                    entry["count"] = 1
                else:
                    # Running average
                    entry["count"] += 1
                    entry["avg_minutes"] = entry["avg_minutes"] + (delta - entry["avg_minutes"]) / entry["count"]

    for entry in response_time_trend.values():
        if entry["avg_minutes"] is not None:
            entry["avg_minutes"] = round(entry["avg_minutes"], 1)

    # --- Hourly distribution ---
    hourly = [0] * 24
    for lead in leads:
        hourly[lead.created_at.hour] += 1

    # --- Weekday distribution ---
    weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    weekday = {name: 0 for name in weekday_names}
    for lead in leads:
        weekday[weekday_names[lead.created_at.weekday()]] += 1

    # --- Source performance ---
    source_perf: dict[str, dict] = {}
    for lead in all_leads:
        if lead.category == "spam":
            continue
        s = lead.source
        if s not in source_perf:
            source_perf[s] = {"source": s, "count": 0, "sent": 0, "won": 0, "lost": 0}
        source_perf[s]["count"] += 1
        if lead.status == "sent":
            source_perf[s]["sent"] += 1
        if lead.outcome == "won":
            source_perf[s]["won"] += 1
        elif lead.outcome == "lost":
            source_perf[s]["lost"] += 1

    for sp in source_perf.values():
        sp["conversion_rate"] = round(sp["won"] / sp["count"] * 100, 1) if sp["count"] else 0

    # --- Category trend (daily by category) ---
    category_trend: dict[str, dict] = {}
    for i in range(days):
        d = (now - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        category_trend[d] = {"date": d}

    for lead in leads:
        if lead.category == "spam":
            continue
        d = lead.created_at.strftime("%Y-%m-%d")
        if d in category_trend:
            cat = lead.category
            category_trend[d][cat] = category_trend[d].get(cat, 0) + 1

    # --- Top senders ---
    sender_counts: dict[str, dict] = {}
    for lead in all_leads:
        if lead.category == "spam":
            continue
        email = lead.sender_email
        if email not in sender_counts:
            sender_counts[email] = {"email": email, "name": lead.sender_name, "count": 0, "latest": lead.created_at.isoformat()}
        sender_counts[email]["count"] += 1
        if lead.created_at.isoformat() > sender_counts[email]["latest"]:
            sender_counts[email]["latest"] = lead.created_at.isoformat()
            sender_counts[email]["name"] = lead.sender_name

    top_senders = sorted(sender_counts.values(), key=lambda x: x["count"], reverse=True)[:10]

    # --- Avg confidence ---
    confidences = [lead.confidence for lead in all_leads if lead.category != "spam" and lead.confidence]
    avg_confidence = round(sum(confidences) / len(confidences), 2) if confidences else None

    return {
        "response_time_trend": list(response_time_trend.values()),
        "hourly_distribution": hourly,
        "weekday_distribution": weekday,
        "source_performance": list(source_perf.values()),
        "category_trend": list(category_trend.values()),
        "top_senders": top_senders,
        "avg_confidence": avg_confidence,
    }


@router.get("/threads/{thread_id}")
def get_thread(
    thread_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Return all leads in a thread ordered chronologically for conversation view."""
    thread_leads = (
        db.query(Lead)
        .filter(Lead.org_id == user.org_id, Lead.thread_id == thread_id)
        .order_by(Lead.created_at.asc())
        .all()
    )
    if not thread_leads:
        raise HTTPException(status_code=404, detail="Thread not found")

    messages = []
    for lead in thread_leads:
        activities = (
            db.query(LeadActivity)
            .filter(LeadActivity.lead_id == lead.id)
            .order_by(LeadActivity.created_at.asc())
            .all()
        )
        messages.append({
            "id": lead.id,
            "sender_name": lead.sender_name,
            "sender_email": lead.sender_email,
            "subject": lead.subject,
            "body": lead.body,
            "category": lead.category,
            "status": lead.status,
            "urgency_score": lead.urgency_score,
            "recommended_reply": lead.recommended_reply,
            "parent_lead_id": lead.parent_lead_id,
            "created_at": lead.created_at.isoformat() if lead.created_at else None,
            "activities": [
                {"type": a.activity_type, "message": a.message, "created_at": a.created_at.isoformat()}
                for a in activities
            ],
        })

    return {
        "thread_id": thread_id,
        "sender_email": thread_leads[0].sender_email,
        "sender_name": thread_leads[0].sender_name,
        "message_count": len(messages),
        "messages": messages,
    }


@router.get("/threads")
def list_threads(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    """Return leads grouped by thread_id (sender email), paginated by thread."""
    thread_subq = (
        db.query(Lead.thread_id, sa_func.max(Lead.created_at).label("latest"))
        .filter(Lead.org_id == user.org_id)
        .group_by(Lead.thread_id)
        .order_by(sa_func.max(Lead.created_at).desc())
    )
    total_threads = thread_subq.count()
    thread_page = thread_subq.offset((page - 1) * page_size).limit(page_size).all()
    thread_ids = [row[0] for row in thread_page]

    leads = (
        db.query(Lead)
        .filter(Lead.org_id == user.org_id, Lead.thread_id.in_(thread_ids))
        .order_by(Lead.thread_id, Lead.created_at.desc())
        .all()
    )

    threads: dict[str, list] = {}
    for lead in leads:
        tid = lead.thread_id or f"orphan-{lead.id}"
        threads.setdefault(tid, []).append({
            "id": lead.id,
            "sender_name": lead.sender_name,
            "sender_email": lead.sender_email,
            "subject": lead.subject,
            "category": lead.category,
            "status": lead.status,
            "urgency_score": lead.urgency_score,
            "created_at": lead.created_at.isoformat() if lead.created_at else None,
        })

    pages = (total_threads + page_size - 1) // page_size if total_threads else 1
    return {"threads": threads, "thread_count": total_threads, "page": page, "pages": pages}


@router.post("/daily-digest", response_model=DigestResponse)
def daily_digest(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    org_settings: OrgSettings = Depends(get_org_settings),
):
    summary = build_daily_digest(db, org_id=user.org_id)
    sent, message = send_daily_digest(summary, org_settings=org_settings)
    status = "sent" if sent else f"failed: {message}"
    return DigestResponse(status=status, summary=summary)


@router.post("/weekly-summary", response_model=DigestResponse)
def weekly_summary(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    org_settings: OrgSettings = Depends(get_org_settings),
):
    summary = build_weekly_summary(db, org_id=user.org_id)
    sent, message = send_weekly_summary(summary, org_settings=org_settings)
    status = "sent" if sent else f"failed: {message}"
    return DigestResponse(status=status, summary=summary)


# --- Internal team notes ---


@router.get("/leads/{lead_id}/notes", response_model=list[LeadNoteResponse])
def list_lead_notes(lead_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _scoped_lead(db, lead_id, user.org_id)
    return (
        db.query(LeadNote)
        .filter(LeadNote.lead_id == lead_id, LeadNote.org_id == user.org_id)
        .order_by(LeadNote.pinned.desc(), LeadNote.created_at.desc())
        .all()
    )


@router.post("/leads/{lead_id}/notes", response_model=LeadNoteResponse)
def create_lead_note(
    lead_id: int,
    payload: LeadNoteCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _scoped_lead(db, lead_id, user.org_id)
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="Note body cannot be empty")
    note = LeadNote(
        org_id=user.org_id,
        lead_id=lead_id,
        user_id=user.id,
        author_name=user.display_name or user.email,
        body=body,
        pinned=payload.pinned,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    log_activity(db, lead_id, user.org_id, "note_added", f"{note.author_name} added a note")
    return note


@router.patch("/leads/{lead_id}/notes/{note_id}", response_model=LeadNoteResponse)
def update_lead_note(
    lead_id: int,
    note_id: int,
    payload: LeadNoteUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _scoped_lead(db, lead_id, user.org_id)
    note = (
        db.query(LeadNote)
        .filter(LeadNote.id == note_id, LeadNote.lead_id == lead_id, LeadNote.org_id == user.org_id)
        .first()
    )
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    if payload.body is not None:
        note.body = payload.body.strip()
    if payload.pinned is not None:
        note.pinned = payload.pinned
    db.commit()
    db.refresh(note)
    return note


@router.delete("/leads/{lead_id}/notes/{note_id}")
def delete_lead_note(
    lead_id: int,
    note_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _scoped_lead(db, lead_id, user.org_id)
    note = (
        db.query(LeadNote)
        .filter(LeadNote.id == note_id, LeadNote.lead_id == lead_id, LeadNote.org_id == user.org_id)
        .first()
    )
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    db.delete(note)
    db.commit()
    return {"ok": True, "deleted_id": note_id}


# --- Reply templates ---


@router.get("/api/templates", response_model=list[ReplyTemplateResponse])
def list_templates(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return (
        db.query(ReplyTemplate)
        .filter(ReplyTemplate.org_id == user.org_id)
        .order_by(ReplyTemplate.sort_order.asc(), ReplyTemplate.name.asc())
        .all()
    )


@router.post("/api/templates", response_model=ReplyTemplateResponse)
def create_template(
    payload: ReplyTemplateCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    name = payload.name.strip()
    body = payload.body.strip()
    if not name or not body:
        raise HTTPException(status_code=400, detail="Name and body required")
    tpl = ReplyTemplate(
        org_id=user.org_id, name=name, body=body, category=payload.category, sort_order=payload.sort_order,
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


@router.patch("/api/templates/{template_id}", response_model=ReplyTemplateResponse)
def update_template(
    template_id: int,
    payload: ReplyTemplateUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    tpl = db.query(ReplyTemplate).filter(ReplyTemplate.id == template_id, ReplyTemplate.org_id == user.org_id).first()
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    for field in ["name", "body", "category", "sort_order"]:
        value = getattr(payload, field)
        if value is not None:
            setattr(tpl, field, value)
    db.commit()
    db.refresh(tpl)
    return tpl


@router.delete("/api/templates/{template_id}")
def delete_template(
    template_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    tpl = db.query(ReplyTemplate).filter(ReplyTemplate.id == template_id, ReplyTemplate.org_id == user.org_id).first()
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    db.delete(tpl)
    db.commit()
    return {"ok": True, "deleted_id": template_id}


@router.post("/api/templates/{template_id}/use")
def increment_template_usage(
    template_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    tpl = db.query(ReplyTemplate).filter(ReplyTemplate.id == template_id, ReplyTemplate.org_id == user.org_id).first()
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    tpl.use_count += 1
    db.commit()
    return {"ok": True, "use_count": tpl.use_count}


# --- Pipeline (kanban) ---

PIPELINE_STAGES = ["new", "contacted", "quoted", "scheduled", "won", "lost"]


@router.get("/api/pipeline")
def get_pipeline(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Return all non-spam leads grouped by pipeline_stage for the kanban view."""
    leads = (
        db.query(Lead)
        .filter(Lead.org_id == user.org_id, Lead.category != "spam")
        .order_by(Lead.starred.desc(), Lead.created_at.desc())
        .all()
    )

    columns: dict[str, list[dict]] = {stage: [] for stage in PIPELINE_STAGES}
    totals: dict[str, float] = {stage: 0.0 for stage in PIPELINE_STAGES}

    for lead in leads:
        stage = lead.pipeline_stage if lead.pipeline_stage in columns else "new"
        columns[stage].append({
            "id": lead.id,
            "sender_name": lead.sender_name,
            "sender_email": lead.sender_email,
            "phone": lead.phone,
            "subject": lead.subject,
            "category": lead.category,
            "urgency_score": lead.urgency_score,
            "deal_value": lead.deal_value,
            "tags": lead.tags or "",
            "starred": lead.starred,
            "status": lead.status,
            "source": lead.source,
            "created_at": lead.created_at.isoformat() if lead.created_at else None,
            "summary": (lead.summary or "")[:160],
        })
        if lead.deal_value:
            totals[stage] += float(lead.deal_value)

    return {
        "stages": PIPELINE_STAGES,
        "columns": columns,
        "totals": {k: round(v, 2) for k, v in totals.items()},
        "counts": {k: len(v) for k, v in columns.items()},
    }


@router.get("/stats/revenue")
def get_revenue_trend(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    days: int = Query(30, ge=7, le=180),
):
    """Daily won revenue + new pipeline value over the period."""
    org_id = user.org_id
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    daily: dict[str, dict] = {}
    for i in range(days):
        d = (now - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        daily[d] = {"date": d, "won_revenue": 0.0, "new_pipeline": 0.0}

    won_leads = (
        db.query(Lead)
        .filter(
            Lead.org_id == org_id,
            Lead.outcome == "won",
            Lead.outcome_at >= cutoff,
            Lead.deal_value.isnot(None),
        )
        .all()
    )
    for lead in won_leads:
        d = lead.outcome_at.strftime("%Y-%m-%d")
        if d in daily:
            daily[d]["won_revenue"] += float(lead.deal_value or 0)

    pipeline_leads = (
        db.query(Lead)
        .filter(
            Lead.org_id == org_id,
            Lead.created_at >= cutoff,
            Lead.deal_value.isnot(None),
            Lead.category != "spam",
        )
        .all()
    )
    for lead in pipeline_leads:
        d = lead.created_at.strftime("%Y-%m-%d")
        if d in daily:
            daily[d]["new_pipeline"] += float(lead.deal_value or 0)

    return {
        "daily": [{"date": v["date"], "won_revenue": round(v["won_revenue"], 2), "new_pipeline": round(v["new_pipeline"], 2)} for v in daily.values()],
        "total_won": round(sum(v["won_revenue"] for v in daily.values()), 2),
        "total_pipeline": round(sum(v["new_pipeline"] for v in daily.values()), 2),
    }
