import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import FollowUp, Lead, OrgSettings

logger = logging.getLogger(__name__)

FOLLOWUP_SCHEDULE_HOURS = [2, 24, 72]


def schedule_followups(db: Session, lead: Lead) -> list[FollowUp]:
    if lead.category not in {"quote_request", "general_inquiry", "urgent_request"}:
        return []

    scheduled = []
    now = datetime.now(timezone.utc)
    for index, hours in enumerate(FOLLOWUP_SCHEDULE_HOURS, start=1):
        followup = FollowUp(
            org_id=lead.org_id,
            lead_id=lead.id,
            scheduled_for=now + timedelta(hours=hours),
            followup_type=f"followup_{index}",
            status="scheduled",
        )
        db.add(followup)
        scheduled.append(followup)

    db.commit()
    logger.info("Scheduled %s followups for lead_id=%s", len(scheduled), lead.id)
    return scheduled


def pending_followups(db: Session, org_id: int | None = None) -> list[FollowUp]:
    q = db.query(FollowUp).filter(FollowUp.status == "scheduled")
    if org_id is not None:
        q = q.filter(FollowUp.org_id == org_id)
    return q.all()


def run_followups(db: Session, org_id: int | None = None, org_settings: OrgSettings | None = None) -> dict:
    """Process all due follow-ups: send emails and mark as sent or failed."""
    if org_settings and org_settings.automation_paused:
        logger.info("Follow-up run skipped for org_id=%s because automation is paused", org_settings.org_id)
        return {"processed": 0, "sent": 0, "failed": 0, "skipped": 0}
    if org_settings and org_settings.org and org_settings.org.subscription_status not in {"active", "trialing"}:
        logger.info("Follow-up run skipped for inactive org_id=%s", org_settings.org_id)
        return {"processed": 0, "sent": 0, "failed": 0, "skipped": 0}

    now = datetime.now(timezone.utc)
    q = db.query(FollowUp).filter(FollowUp.status == "scheduled", FollowUp.scheduled_for <= now)
    if org_id is not None:
        q = q.filter(FollowUp.org_id == org_id)
    due = q.all()

    results = {"processed": 0, "sent": 0, "failed": 0, "skipped": 0}

    for followup in due:
        lead = followup.lead
        if not lead or lead.status == "spam":
            followup.status = "skipped"
            db.commit()
            results["skipped"] += 1
            results["processed"] += 1
            continue

        if not lead.recommended_reply:
            followup.status = "skipped"
            db.commit()
            results["skipped"] += 1
            results["processed"] += 1
            continue

        if lead.status not in {"sent", "review_required"}:
            followup.status = "skipped"
            db.commit()
            results["skipped"] += 1
            results["processed"] += 1
            continue

        from app.mailer import send_email

        sent, message = send_email(
            to_email=lead.sender_email,
            subject=f"Re: {lead.subject or 'Your inquiry'}",
            body=lead.recommended_reply,
            org_settings=org_settings,
        )

        followup.status = "sent" if sent else "failed"
        followup.sent_at = now if sent else None
        db.commit()

        if sent:
            results["sent"] += 1
        else:
            results["failed"] += 1
            logger.warning("Follow-up %s failed for lead %s: %s", followup.id, lead.id, message)

        results["processed"] += 1

    logger.info("Follow-up run complete: %s", results)
    return results
