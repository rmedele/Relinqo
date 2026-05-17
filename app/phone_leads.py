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
from app.models import CallEvent, Lead, OrgSettings, PhoneRoutingRule, SmsNotification, SmsOptOut
from app.sms import send_sms_to

logger = logging.getLogger(__name__)

URGENCY_ICON = {1: "", 2: "", 3: "!", 4: "!!", 5: "!!!"}
SUCCESSFUL_SMS_STATUSES = {"sent", "delivered"}


def synthetic_sender_email(from_number: str) -> str:
    """Lead.sender_email is NOT NULL but phone leads have none. Use a
    routing-only address that clearly shows the origin channel."""
    digits = re.sub(r"\D", "", from_number) or "unknown"
    return f"caller-{digits}@phone.relinqo.local"


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
        SmsNotification.status.in_(SUCCESSFUL_SMS_STATUSES),
        SmsNotification.created_at >= cutoff,
    ).first() is not None


def successful_lead_sms_exists(
    db: Session,
    *,
    org_id: int,
    lead_id: int,
    purpose: str,
) -> bool:
    return db.query(SmsNotification).filter(
        SmsNotification.org_id == org_id,
        SmsNotification.lead_id == lead_id,
        SmsNotification.purpose == purpose,
        SmsNotification.status.in_(SUCCESSFUL_SMS_STATUSES),
    ).first() is not None


def owner_alert_number(
    org_settings: OrgSettings | None,
    routing_rule: PhoneRoutingRule | None,
) -> str:
    """Resolve the destination for phone-lead owner alerts."""
    candidates = (
        routing_rule.owner_phone if routing_rule else "",
        org_settings.sms_alert_to_number if org_settings else "",
        settings.sms_alert_to_number,
    )
    for candidate in candidates:
        normalized = (candidate or "").strip()
        if normalized:
            return normalized
    return ""


def send_owner_alert_sms(
    db: Session,
    *,
    org_id: int,
    lead: Lead,
    call: CallEvent,
    org_settings: OrgSettings | None,
    routing_rule: PhoneRoutingRule | None,
    channel_label: str,
    automation_allowed: bool,
) -> SmsNotification | None:
    """Send the launch-critical owner alert with explicit logging."""
    to_number = owner_alert_number(org_settings, routing_rule)
    if not to_number:
        logger.error(
            "SMS owner_alert skipped: no owner number org_id=%s lead_id=%s call_event_id=%s",
            org_id, lead.id, call.id,
        )
        return None
    if not automation_allowed:
        logger.warning(
            "SMS owner_alert skipped: automation gated org_id=%s lead_id=%s call_event_id=%s to=%s",
            org_id, lead.id, call.id, to_number,
        )
        return None
    notification = record_and_send_sms(
        db,
        org_id=org_id,
        to_number=to_number,
        body=format_owner_summary(lead, call, org_settings, channel_label=channel_label),
        purpose="owner_alert",
        lead_id=lead.id,
        call_event_id=call.id,
        org_settings=org_settings,
        from_number=call.to_number,
    )
    logger.info(
        "SMS owner_alert %s org_id=%s lead_id=%s call_event_id=%s to=%s sid=%s",
        notification.status,
        org_id,
        lead.id,
        call.id,
        to_number,
        notification.twilio_message_sid,
    )
    return notification


def sms_opted_out(db: Session, org_id: int, to_number: str) -> bool:
    normalized = re.sub(r"[^\d+]", "", to_number or "")
    return db.query(SmsOptOut).filter(
        SmsOptOut.org_id == org_id,
        SmsOptOut.phone_number == normalized,
        SmsOptOut.opted_in_at.is_(None),
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
    from_number: str | None = None,
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

    if sms_opted_out(db, org_id, to_number):
        notification.status = "skipped"
        notification.error_message = "recipient opted out"
        db.commit()
        return notification

    ok, msg, twilio_sid = send_sms_to(
        body,
        to_number,
        org_settings,
        from_number=from_number,
    )
    fallback_from = (settings.twilio_from_number or "").strip()
    if not ok and from_number and fallback_from and fallback_from != from_number.strip():
        logger.warning(
            "SMS %s to %s failed from %s; retrying from platform default %s",
            purpose, to_number, from_number, fallback_from,
        )
        fallback_ok, fallback_msg, fallback_sid = send_sms_to(body, to_number, org_settings)
        ok = fallback_ok
        twilio_sid = fallback_sid
        msg = f"{msg}; fallback: {fallback_msg}"

    notification.twilio_message_sid = twilio_sid
    notification.status = "sent" if ok else "failed"
    if not ok:
        notification.error_message = msg
        logger.warning("SMS %s to %s failed: %s", purpose, to_number, msg)
    db.commit()
    return notification
