"""Review request automation.

When a lead's outcome flips to 'won' we schedule a ReviewRequest row to fire
N hours later. The scheduler tick (run_due_review_requests) processes scheduled
rows whose scheduled_for has passed and sends the configured message via email
and/or SMS.

Body templates support {{name}}, {{full_name}}, {{business}}, {{review_url}},
{{phone}}.

Defaults are applied to brand new orgs in render_review_body if their
review_request_body field is empty (legacy orgs created before the migration's
server_default takes effect at row insert time).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.billing import org_has_billing_access
from app.models import Lead, OrgSettings, ReviewRequest

logger = logging.getLogger(__name__)

PHONE_ONLY_EMAIL_DOMAINS = (
    "@phone.relinqo.local",
    "@phone.reqlinqo.local",
    "@phone.leadrelay.local",
)


DEFAULT_REVIEW_BODY = (
    "Hi {{name}},\n\n"
    "Thanks again for choosing {{business}} — it was a pleasure working with you.\n\n"
    "If you have 30 seconds, we'd really appreciate a quick Google review:\n"
    "{{review_url}}\n\n"
    "Reviews from neighbors like you are how small businesses like ours stay busy.\n\n"
    "Thanks,\n{{business}}\n"
)

DEFAULT_REVIEW_SMS = (
    "Hi {{name}}, thanks for choosing {{business}}! "
    "If you have 30 seconds, we'd really appreciate a Google review: {{review_url}}"
)


def schedule_review_request(
    db: Session,
    lead: Lead,
    org_settings: OrgSettings | None,
) -> ReviewRequest | None:
    """Create a scheduled ReviewRequest row for a won lead, if the org has the
    feature enabled and a review URL configured. Idempotent — does not duplicate
    an existing scheduled/sent request for the same lead."""
    if not org_settings or not org_settings.review_request_enabled:
        return None
    if org_settings.automation_paused:
        return None
    if org_settings.org and not org_has_billing_access(org_settings.org):
        return None
    if not org_settings.review_url:
        logger.info("review_request skipped lead_id=%s — no review_url set", lead.id)
        return None

    existing = (
        db.query(ReviewRequest)
        .filter(
            ReviewRequest.lead_id == lead.id,
            ReviewRequest.status.in_(["scheduled", "sent"]),
        )
        .first()
    )
    if existing:
        return existing

    delay_hours = max(0, int(org_settings.review_delay_hours or 0))
    scheduled_for = datetime.now(timezone.utc) + timedelta(hours=delay_hours)
    channel = org_settings.review_request_channel or "email"

    req = ReviewRequest(
        org_id=lead.org_id,
        lead_id=lead.id,
        scheduled_for=scheduled_for,
        channel=channel,
        status="scheduled",
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    logger.info(
        "Scheduled review_request id=%s lead_id=%s at=%s channel=%s",
        req.id, lead.id, scheduled_for.isoformat(), channel,
    )
    return req


def render_review_body(template: str, lead: Lead, org_settings: OrgSettings) -> str:
    body = (template or "").strip() or DEFAULT_REVIEW_BODY
    full_name = lead.sender_name or ""
    first_name = full_name.split(" ")[0] if full_name else "there"
    return (
        body.replace("{{name}}", first_name)
        .replace("{{full_name}}", full_name)
        .replace("{{business}}", org_settings.business_name or "us")
        .replace("{{review_url}}", org_settings.review_url or "")
        .replace("{{phone}}", lead.phone or "")
    )


def run_due_review_requests(
    db: Session,
    org_id: int | None = None,
    org_settings: OrgSettings | None = None,
) -> dict:
    """Process all due ReviewRequest rows. Imports send_email/send_sms_to inside
    the function to avoid circular import at module load."""
    from app.mailer import email_configured, send_email
    from app.routes.leads import log_activity
    from app.sms import send_sms_to, sms_configured

    now = datetime.now(timezone.utc)
    q = db.query(ReviewRequest).filter(
        ReviewRequest.status == "scheduled",
        ReviewRequest.scheduled_for <= now,
    )
    if org_id is not None:
        q = q.filter(ReviewRequest.org_id == org_id)
    due = q.all()

    results = {"processed": 0, "sent": 0, "failed": 0, "skipped": 0}

    for req in due:
        results["processed"] += 1
        lead = db.query(Lead).filter(Lead.id == req.lead_id).first()
        if not lead:
            req.status = "skipped"
            req.error_message = "lead missing"
            db.commit()
            results["skipped"] += 1
            continue

        if (
            not org_settings
            or not org_settings.review_request_enabled
            or not org_settings.review_url
            or org_settings.automation_paused
            or (org_settings.org and not org_has_billing_access(org_settings.org))
        ):
            req.status = "skipped"
            req.error_message = "review automation disabled at send time"
            db.commit()
            results["skipped"] += 1
            continue

        ok_any = False
        errors: list[str] = []
        wants_email = req.channel in ("email", "both")
        wants_sms = req.channel in ("sms", "both")

        if wants_email:
            if not lead.sender_email or any(
                domain in lead.sender_email for domain in PHONE_ONLY_EMAIL_DOMAINS
            ):
                # phone-only lead, skip email
                pass
            elif email_configured(org_settings):
                body = render_review_body(org_settings.review_request_body, lead, org_settings)
                subject = (
                    org_settings.review_request_subject
                    or "Quick favor — would you mind leaving us a review?"
                )
                sent, message = send_email(
                    to_email=lead.sender_email,
                    subject=subject,
                    body=body,
                    org_settings=org_settings,
                )
                if sent:
                    ok_any = True
                else:
                    errors.append(f"email: {message}")
            else:
                errors.append("email: not configured")

        if wants_sms:
            if not lead.phone:
                errors.append("sms: lead has no phone")
            elif sms_configured(org_settings):
                sms_body = render_review_body(DEFAULT_REVIEW_SMS, lead, org_settings)
                sent, message, _sid = send_sms_to(sms_body, lead.phone, org_settings)
                if sent:
                    ok_any = True
                else:
                    errors.append(f"sms: {message}")
            else:
                errors.append("sms: not configured")

        if ok_any:
            req.status = "sent"
            req.sent_at = now
            req.error_message = "; ".join(errors) if errors else None
            results["sent"] += 1
            log_activity(
                db, lead.id, lead.org_id, "review_requested",
                f"Review request sent via {req.channel}",
            )
        else:
            req.status = "failed"
            req.error_message = "; ".join(errors) or "no channel succeeded"
            results["failed"] += 1
            logger.warning("review_request id=%s failed: %s", req.id, req.error_message)

        db.commit()

    if results["processed"]:
        logger.info("Review-request run complete: %s", results)
    return results
