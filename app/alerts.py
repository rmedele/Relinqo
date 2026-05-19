import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.mailer import send_email
from app.models import Lead, OrgSettings, OwnerAlert
from app.sms import send_sms, sms_configured

logger = logging.getLogger(__name__)


def send_owner_alert(db: Session, lead: Lead, org_settings: OrgSettings | None = None) -> OwnerAlert:
    alert_email = org_settings.owner_alert_email if org_settings else settings.owner_alert_email
    email_payload = build_alert_email(lead)
    sent, message = send_email(
        to_email=alert_email,
        subject=email_payload["subject"],
        body=email_payload["body"],
        org_settings=org_settings,
    )
    alert = OwnerAlert(
        org_id=lead.org_id,
        lead_id=lead.id,
        alert_type="urgent" if lead.urgency_score >= 5 else "hot_lead",
        sent_to=alert_email,
        status="sent" if sent else f"failed: {message}",
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    logger.info("Owner alert processed for lead_id=%s sent_to=%s status=%s", lead.id, alert_email, alert.status)

    # Send SMS for urgent leads
    if lead.urgency_score >= 4 and sms_configured(org_settings):
        sms_body = f"[relinqo] Urgent lead from {lead.sender_name or lead.sender_email}: {lead.summary or lead.subject or 'No details'}"
        sms_sent, sms_msg = send_sms(sms_body, org_settings=org_settings)
        if sms_sent:
            logger.info("SMS alert sent for lead_id=%s", lead.id)
        else:
            logger.warning("SMS alert failed for lead_id=%s: %s", lead.id, sms_msg)

    return alert


def send_sms_approval_request(db: Session, lead: Lead, org_settings: OrgSettings | None = None) -> bool:
    """Send an SMS asking the owner to reply YES to approve sending the draft reply."""
    if not sms_configured(org_settings):
        logger.info("SMS not configured, skipping approval request for lead_id=%s", lead.id)
        return False

    sender = lead.sender_name or lead.sender_email
    summary = (lead.summary or lead.subject or "No details")[:100]
    sms_body = (
        f"[relinqo] New lead #{lead.id} from {sender}: \"{summary}\"\n"
        f"Reply YES {lead.id} to approve sending reply, or NO {lead.id} to skip."
    )
    sent, msg = send_sms(sms_body, org_settings=org_settings)
    if sent:
        logger.info("SMS approval request sent for lead_id=%s", lead.id)
    else:
        logger.warning("SMS approval request failed for lead_id=%s: %s", lead.id, msg)
    return sent


def build_alert_email(lead: Lead) -> dict[str, str]:
    subject = f"[{lead.category.upper()}] relinqo alert: {lead.sender_email}"
    body = (
        f"Category: {lead.category}\n"
        f"Urgency: {lead.urgency_score}\n"
        f"Sender: {lead.sender_name or 'Unknown'} <{lead.sender_email}>\n"
        f"Phone: {lead.phone or 'N/A'}\n"
        f"Summary: {lead.summary or ''}\n"
        f"Recommended action: {lead.next_step or 'Review lead'}\n"
    )
    return {"subject": subject, "body": body}
