"""Twilio SMS webhook for reply-to-approve flow.

When the business owner replies YES <id> or NO <id>, this endpoint
processes the approval/rejection and sends or skips the draft reply.
"""
import logging
import re

from fastapi import APIRouter, Form, Depends, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.mailer import send_email
from app.models import Lead, OrgSettings
from app.sms import send_sms

router = APIRouter()
logger = logging.getLogger(__name__)

# Match "YES 42", "yes42", "NO 42", "no 42" etc.
APPROVE_RE = re.compile(r"^\s*(yes)\s*(\d+)\s*$", re.IGNORECASE)
REJECT_RE = re.compile(r"^\s*(no)\s*(\d+)\s*$", re.IGNORECASE)


def _twiml_response(message: str) -> Response:
    """Return a minimal TwiML response."""
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{message}</Message></Response>'
    return Response(content=xml, media_type="application/xml")


def _find_org_by_phone(db: Session, from_number: str) -> tuple[int | None, OrgSettings | None]:
    """Find the org whose sms_alert_to_number matches the sender."""
    # Normalize: strip everything except digits and leading +
    normalized = re.sub(r"[^\d+]", "", from_number)
    settings = db.query(OrgSettings).all()
    for s in settings:
        owner_num = re.sub(r"[^\d+]", "", s.sms_alert_to_number or "")
        if owner_num and owner_num == normalized:
            return s.org_id, s
    return None, None


@router.post("/sms/webhook")
def sms_webhook(
    Body: str = Form(""),
    From: str = Form(""),
    db: Session = Depends(get_db),
):
    """Handle incoming SMS from Twilio."""
    body = Body.strip()
    logger.info("SMS webhook received from=%s body=%r", From, body)

    approve_match = APPROVE_RE.match(body)
    reject_match = REJECT_RE.match(body)

    if not approve_match and not reject_match:
        return _twiml_response("Reply format: YES <lead#> or NO <lead#>")

    is_approve = approve_match is not None
    lead_id = int((approve_match or reject_match).group(2))

    # Find the org by the sender's phone number
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

    else:
        lead.status = "skipped"
        db.commit()

        from app.routes.leads import log_activity
        log_activity(db, lead.id, lead.org_id, "sms_rejected", "Lead rejected via SMS reply.")

        return _twiml_response(f"Lead #{lead_id} skipped.")
