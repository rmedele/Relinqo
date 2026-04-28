"""Twilio SMS webhook — handles TWO distinct inbound SMS flows:

1. **Caller SMS reply** (new, phone-lead pipeline): if the sender's
   number matches a CallEvent we received in the last 30 minutes,
   treat the message as the caller's reply to our "we'll text you"
   outreach. Creates a Lead and alerts the owner.

2. **Owner YES/NO approval** (existing email flow): if the sender is
   the configured owner number, parse "YES <lead#>" / "NO <lead#>"
   to approve or reject drafted replies.

Order matters: we check recent-caller FIRST so owner-approval stays
uncluttered even if the owner happens to call their own business number.
"""
import logging
import re

from fastapi import APIRouter, Depends, Form, Request, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.mailer import send_email
from app.models import Lead, OrgSettings, PhoneNumber, SmsOptOut
from app.sms_intake import find_recent_call_for_sender, process_sms_lead
from app.twilio_signature import verify_twilio_signature

router = APIRouter()
logger = logging.getLogger(__name__)

APPROVE_RE = re.compile(r"^\s*(yes)\s*(\d+)\s*$", re.IGNORECASE)
REJECT_RE = re.compile(r"^\s*(no)\s*(\d+)\s*$", re.IGNORECASE)
STOP_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit"}
START_WORDS = {"start", "unstop"}
HELP_WORDS = {"help", "info"}


def _twiml_response(message: str | None = None) -> Response:
    """TwiML <Message> auto-replies to the sender. Passing None returns
    an empty <Response/> (no auto-reply)."""
    if message is None:
        xml = '<?xml version="1.0" encoding="UTF-8"?><Response/>'
    else:
        xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{message}</Message></Response>'
    return Response(content=xml, media_type="application/xml")


def _find_org_by_phone(db: Session, from_number: str) -> tuple[int | None, OrgSettings | None]:
    normalized = re.sub(r"[^\d+]", "", from_number)
    settings_rows = db.query(OrgSettings).all()
    for s in settings_rows:
        owner_num = re.sub(r"[^\d+]", "", s.sms_alert_to_number or "")
        if owner_num and owner_num == normalized:
            return s.org_id, s
    return None, None


def _normalize_phone(raw: str) -> str:
    return re.sub(r"[^\d+]", "", raw or "")


def _find_org_by_twilio_to(db: Session, to_number: str) -> tuple[int | None, OrgSettings | None]:
    normalized = _normalize_phone(to_number)
    phone = db.query(PhoneNumber).filter(
        PhoneNumber.phone_number == normalized,
        PhoneNumber.is_active == True,  # noqa: E712
    ).first()
    if not phone:
        return None, None
    settings = db.query(OrgSettings).filter(OrgSettings.org_id == phone.org_id).first()
    return phone.org_id, settings


def _record_opt_out(db: Session, org_id: int, from_number: str, opted_out: bool) -> None:
    normalized = _normalize_phone(from_number)
    row = db.query(SmsOptOut).filter(
        SmsOptOut.org_id == org_id,
        SmsOptOut.phone_number == normalized,
    ).first()
    if opted_out:
        if row is None:
            db.add(SmsOptOut(org_id=org_id, phone_number=normalized, source="sms"))
        else:
            row.opted_in_at = None
        db.commit()
        return
    if row is not None:
        from datetime import datetime, timezone
        row.opted_in_at = datetime.now(timezone.utc)
        db.commit()


@router.post("/sms/webhook", dependencies=[Depends(verify_twilio_signature)])
async def sms_webhook(
    request: Request,
    Body: str = Form(""),
    From: str = Form(""),
    db: Session = Depends(get_db),
):
    """Route inbound SMS."""
    body = Body.strip()
    # NumMedia and MediaUrl0..N are Twilio MMS fields.
    form = await request.form()
    try:
        num_media = int(form.get("NumMedia", "0"))
    except (TypeError, ValueError):
        num_media = 0
    media_urls = [form.get(f"MediaUrl{i}") for i in range(num_media) if form.get(f"MediaUrl{i}")]
    to_number = form.get("To", "")
    command = body.lower()

    logger.info(
        "SMS webhook from=%s num_media=%s body_len=%s",
        From, num_media, len(body),
    )

    if command in STOP_WORDS | START_WORDS | HELP_WORDS:
        org_id, _org_settings = _find_org_by_twilio_to(db, to_number)
        if org_id is not None:
            if command in STOP_WORDS:
                _record_opt_out(db, org_id, From, opted_out=True)
                return _twiml_response("You have been unsubscribed. Reply START to resubscribe.")
            if command in START_WORDS:
                _record_opt_out(db, org_id, From, opted_out=False)
                return _twiml_response("You have been resubscribed.")
        if command in HELP_WORDS:
            return _twiml_response("LeadRelay helps this business respond to service requests. Reply STOP to opt out.")
        return _twiml_response(None)

    # --- 1. Recent-caller reply flow ---
    recent_call = find_recent_call_for_sender(db, From)
    if recent_call is not None:
        lead_id = process_sms_lead(recent_call.id, body, media_urls)
        if lead_id is not None:
            logger.info("sms/webhook: recent-caller reply -> lead_id=%s", lead_id)
        # Empty TwiML so Twilio doesn't auto-reply; our outreach SMS already
        # did the customer-facing messaging via record_and_send_sms.
        return _twiml_response(None)

    # --- 2. Owner YES/NO approval flow (existing) ---
    approve_match = APPROVE_RE.match(body)
    reject_match = REJECT_RE.match(body)

    if not approve_match and not reject_match:
        return _twiml_response("Reply format: YES <lead#> or NO <lead#>")

    is_approve = approve_match is not None
    lead_id = int((approve_match or reject_match).group(2))

    org_id, org_settings = _find_org_by_phone(db, From)
    if org_id is None:
        return _twiml_response("Phone number not linked to an account.")

    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.org_id == org_id).first()
    if not lead:
        return _twiml_response(f"Lead #{lead_id} not found.")

    if lead.status == "sent":
        return _twiml_response(f"Lead #{lead_id} was already sent.")

    if is_approve:
        if lead.status not in ("drafted", "new", "review_required"):
            return _twiml_response(f"Lead #{lead_id} is not in a sendable state ({lead.status}).")

        subject = f"Re: {lead.subject or 'Your inquiry'}"
        sent, message = send_email(
            to_email=lead.sender_email,
            subject=subject,
            body=lead.recommended_reply or "",
            org_settings=org_settings,
        )
        lead.status = "sent" if sent else "send_failed"
        db.commit()

        from app.routes.leads import log_activity
        log_activity(db, lead.id, lead.org_id, "reply_sent" if sent else "reply_failed",
                     f"Approved via SMS. {'Sent to' if sent else 'Failed for'} {lead.sender_email}")

        if sent:
            return _twiml_response(f"Reply sent to {lead.sender_email} for lead #{lead_id}.")
        return _twiml_response(f"Send failed for lead #{lead_id}. Check SMTP settings.")

    lead.status = "skipped"
    db.commit()

    from app.routes.leads import log_activity
    log_activity(db, lead.id, lead.org_id, "sms_rejected", "Lead rejected via SMS reply.")

    return _twiml_response(f"Lead #{lead_id} skipped.")
