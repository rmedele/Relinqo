"""Shared helpers for the phone lead pipeline.

Both voicemail-to-lead (`voicemail_processor`) and SMS-outreach-to-lead
(`sms_intake`) use these: owner alert formatting, outbound SMS sending +
persistence, synthetic sender_email, spam dedup window.
"""
import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.models import CallEvent, Lead, OrgSettings, SmsNotification
from app.sms import send_sms_to

logger = logging.getLogger(__name__)

URGENCY_ICON = {1: "", 2: "", 3: "!", 4: "!!", 5: "!!!"}


def synthetic_sender_email(from_number: str) -> str:
    """Lead.sender_email is NOT NULL but phone leads have none. Use a
    routing-only address that clearly shows the origin channel."""
    digits = re.sub(r"\D", "", from_number) or "unknown"
    return f"caller-{digits}@phone.leadrelay.local"


def business_context(org_settings: OrgSettings | None) -> str:
    if not org_settings:
        return "No business profile configured."
    parts = []
    if org_settings.business_name:
        parts.append(f"Business: {org_settings.business_name}")
    if org_settings.business_services:
        parts.append(f"Services offered: {org_settings.business_services}")
    if org_settings.business_area:
        parts.append(f"Service area: {org_settings.business_area}")
    return "\n".join(parts) if parts else "No business profile configured."


def format_owner_summary(
    lead: Lead,
    call: CallEvent,
    org_settings: OrgSettings | None,
    *,
    channel_label: str = "voicemail",
) -> str:
    """One-SMS summary for the business owner. `channel_label` is
    'voicemail' or 'SMS' so the owner knows how the lead came in."""
    hdr = "[AFTER HOURS] " if call.is_after_hours else ""
    icon = URGENCY_ICON.get(lead.urgency_score, "")
    callback = lead.phone or call.from_number
    name = lead.sender_name or "Unknown caller"
    summary = lead.summary or "No summary"
    if len(summary) > 140:
        summary = summary[:137] + "..."
    base_url = (settings.public_base_url or "").rstrip("/")
    link = f"{base_url}/review#lead-{lead.id}" if base_url else f"lead #{lead.id}"
    prefix = f"{hdr}{icon + ' ' if icon else ''}".strip()
    header_line = f"{prefix + ' ' if prefix else ''}New {lead.category} lead ({channel_label})"
    return (
        f"{header_line}\n"
        f"{name} · {callback}\n"
        f"{summary}\n"
        f"{link}"
    )


def recent_sms_exists(
    db: Session,
    org_id: int,
    to_number: str,
    purpose: str,
    within_minutes: int = 10,
) -> bool:
    """True if we sent this purpose to this number for this org recently.
    Used to suppress duplicate outreach / confirmation SMS on redial storms."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=within_minutes)
    return db.query(SmsNotification).filter(
        SmsNotification.org_id == org_id,
        SmsNotification.to_number == to_number,
        SmsNotification.purpose == purpose,
        SmsNotification.created_at >= cutoff,
    ).first() is not None


def record_and_send_sms(
    db: Session,
    *,
    org_id: int,
    to_number: str,
    body: str,
    purpose: str,
    lead_id: int | None,
    call_event_id: int | None,
    org_settings: OrgSettings | None,
) -> SmsNotification:
    """Persist intent, send via Twilio, update status. Always flushes an
    sms_notifications row even if the send fails — gives us an audit trail."""
    notification = SmsNotification(
        org_id=org_id,
        lead_id=lead_id,
        call_event_id=call_event_id,
        direction="outbound",
        to_number=to_number,
        body=body,
        purpose=purpose,
        status="queued",
    )
    db.add(notification)
    db.flush()

    ok, msg, twilio_sid = send_sms_to(body, to_number, org_settings)
    notification.twilio_message_sid = twilio_sid
    notification.status = "sent" if ok else "failed"
    if not ok:
        notification.error_message = msg
        logger.warning("SMS %s to %s failed: %s", purpose, to_number, msg)
    db.commit()
    return notification
