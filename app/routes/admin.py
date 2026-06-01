from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import platform_admin_emails, require_platform_admin
from app.billing import org_has_billing_access
from app.database import get_db
from app.models import (
    Booking,
    CallEvent,
    Lead,
    Organization,
    OrgSettings,
    PhoneNumber,
    PhoneRoutingRule,
    ReviewRequest,
    SmsNotification,
    User,
)

router = APIRouter(tags=["admin"])
UI_DIR = Path(__file__).resolve().parent.parent / "ui"

OPEN_LEAD_STATUSES = {"new", "drafted", "ready_to_send", "pending_send", "send_failed"}


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _excerpt(value: str | None, limit: int = 180) -> str:
    compact = " ".join((value or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def _count_rows(rows) -> dict[int, int]:
    return {int(org_id): int(count or 0) for org_id, count in rows}


def _sum_rows(rows) -> dict[int, float]:
    return {int(org_id): float(value or 0.0) for org_id, value in rows}


def _org_ref(org: Organization | None) -> dict | None:
    if not org:
        return None
    return {"id": org.id, "name": org.name, "slug": org.slug}


@router.get("/admin", include_in_schema=False)
def admin_page(user: User = Depends(require_platform_admin)):
    return FileResponse(UI_DIR / "admin.html", headers={"Cache-Control": "no-store"})


@router.get("/api/admin/overview")
def admin_overview(
    limit: int = Query(100, ge=1, le=500),
    user: User = Depends(require_platform_admin),
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)

    orgs = db.query(Organization).order_by(Organization.created_at.desc()).all()
    org_by_id = {org.id: org for org in orgs}
    users = db.query(User).order_by(User.created_at.desc(), User.id.desc()).all()
    settings_rows = db.query(OrgSettings).all()
    settings_by_org = {row.org_id: row for row in settings_rows}

    users_by_org: dict[int, list[User]] = defaultdict(list)
    for row in users:
        users_by_org[row.org_id].append(row)

    total_leads_by_org = _count_rows(db.query(Lead.org_id, func.count(Lead.id)).group_by(Lead.org_id).all())
    open_leads_by_org = _count_rows(
        db.query(Lead.org_id, func.count(Lead.id))
        .filter(Lead.status.in_(OPEN_LEAD_STATUSES), Lead.category != "spam")
        .group_by(Lead.org_id)
        .all()
    )
    won_leads_by_org = _count_rows(
        db.query(Lead.org_id, func.count(Lead.id)).filter(Lead.outcome == "won").group_by(Lead.org_id).all()
    )
    latest_lead_by_org = {
        int(org_id): latest
        for org_id, latest in db.query(Lead.org_id, func.max(Lead.created_at)).group_by(Lead.org_id).all()
    }
    pipeline_value_by_org = _sum_rows(
        db.query(Lead.org_id, func.coalesce(func.sum(Lead.deal_value), 0.0))
        .filter(
            Lead.deal_value.isnot(None),
            Lead.pipeline_stage.in_(["new", "contacted", "quoted", "scheduled"]),
            Lead.category != "spam",
        )
        .group_by(Lead.org_id)
        .all()
    )
    won_revenue_by_org = _sum_rows(
        db.query(Lead.org_id, func.coalesce(func.sum(Lead.deal_value), 0.0))
        .filter(Lead.outcome == "won", Lead.deal_value.isnot(None))
        .group_by(Lead.org_id)
        .all()
    )

    phone_numbers_by_org: dict[int, list[str]] = defaultdict(list)
    for row in db.query(PhoneNumber).filter(PhoneNumber.is_active.is_(True)).all():
        phone_numbers_by_org[row.org_id].append(row.phone_number)

    routing_by_org = {row.org_id: row for row in db.query(PhoneRoutingRule).all()}

    total_leads = db.query(func.count(Lead.id)).scalar() or 0
    open_leads = (
        db.query(func.count(Lead.id))
        .filter(Lead.status.in_(OPEN_LEAD_STATUSES), Lead.category != "spam")
        .scalar()
        or 0
    )
    spam_leads = (
        db.query(func.count(Lead.id)).filter((Lead.category == "spam") | (Lead.status == "spam")).scalar() or 0
    )
    sent_leads = db.query(func.count(Lead.id)).filter(Lead.status == "sent").scalar() or 0
    won_leads = db.query(func.count(Lead.id)).filter(Lead.outcome == "won").scalar() or 0
    pipeline_value = (
        db.query(func.coalesce(func.sum(Lead.deal_value), 0.0))
        .filter(
            Lead.deal_value.isnot(None),
            Lead.pipeline_stage.in_(["new", "contacted", "quoted", "scheduled"]),
            Lead.category != "spam",
        )
        .scalar()
        or 0.0
    )
    won_revenue = (
        db.query(func.coalesce(func.sum(Lead.deal_value), 0.0))
        .filter(Lead.outcome == "won", Lead.deal_value.isnot(None))
        .scalar()
        or 0.0
    )
    due_reviews = (
        db.query(func.count(ReviewRequest.id))
        .filter(ReviewRequest.status == "scheduled", ReviewRequest.scheduled_for <= now)
        .scalar()
        or 0
    )

    org_payload = []
    for org in orgs:
        org_settings = settings_by_org.get(org.id)
        org_users = users_by_org.get(org.id, [])
        owners = [row for row in org_users if row.role == "owner"]
        routing = routing_by_org.get(org.id)
        org_payload.append(
            {
                "id": org.id,
                "name": org.name,
                "slug": org.slug,
                "created_at": _iso(org.created_at),
                "subscription_status": org.subscription_status,
                "plan": org.plan,
                "billing_active": org_has_billing_access(org),
                "billing_exempt": bool(org.billing_exempt),
                "billing_exempt_reason": org.billing_exempt_reason or "",
                "stripe_customer_linked": bool(org.stripe_customer_id),
                "stripe_subscription_linked": bool(org.stripe_subscription_id),
                "users_count": len(org_users),
                "owner_emails": [owner.email for owner in owners],
                "lead_count": total_leads_by_org.get(org.id, 0),
                "open_lead_count": open_leads_by_org.get(org.id, 0),
                "won_lead_count": won_leads_by_org.get(org.id, 0),
                "pipeline_value": round(pipeline_value_by_org.get(org.id, 0.0), 2),
                "won_revenue": round(won_revenue_by_org.get(org.id, 0.0), 2),
                "latest_lead_at": _iso(latest_lead_by_org.get(org.id)),
                "integrations": {
                    "gmail_connected": bool(org_settings and org_settings.google_oauth_email),
                    "gmail_email": org_settings.google_oauth_email if org_settings else "",
                    "inbox_poll_enabled": bool(org_settings and org_settings.inbox_poll_enabled),
                    "owner_alert_email": org_settings.owner_alert_email if org_settings else "",
                    "sms_alert_to_number": org_settings.sms_alert_to_number if org_settings else "",
                    "smtp_configured": bool(org_settings and org_settings.smtp_host and org_settings.smtp_username),
                    "imap_configured": bool(org_settings and org_settings.imap_host and org_settings.imap_username),
                    "twilio_configured": bool(
                        org_settings and org_settings.twilio_account_sid and org_settings.twilio_from_number
                    ),
                    "phone_numbers": phone_numbers_by_org.get(org.id, []),
                    "forwarding_status": routing.forwarding_setup_status if routing else "not_started",
                    "scheduling_enabled": bool(org_settings and org_settings.scheduling_enabled),
                    "calendar_sync_enabled": bool(org_settings and org_settings.google_calendar_sync_enabled),
                    "review_request_enabled": bool(org_settings and org_settings.review_request_enabled),
                    "outbound_webhook_enabled": bool(org_settings and org_settings.outbound_webhook_enabled),
                    "automation_paused": bool(org_settings and org_settings.automation_paused),
                    "human_review": bool(org_settings.human_review) if org_settings else True,
                },
            }
        )

    user_payload = [
        {
            "id": row.id,
            "email": row.email,
            "display_name": row.display_name,
            "role": row.role,
            "org": _org_ref(org_by_id.get(row.org_id)),
            "org_id": row.org_id,
            "is_active": bool(row.is_active),
            "created_at": _iso(row.created_at),
            "is_platform_admin": row.email.strip().lower() in platform_admin_emails(),
        }
        for row in users
    ]

    recent_leads = (
        db.query(Lead).order_by(Lead.created_at.desc(), Lead.id.desc()).limit(limit).all()
    )
    recent_lead_payload = [
        {
            "id": lead.id,
            "org_id": lead.org_id,
            "org": _org_ref(org_by_id.get(lead.org_id)),
            "created_at": _iso(lead.created_at),
            "updated_at": _iso(lead.updated_at),
            "source": lead.source,
            "sender_name": lead.sender_name,
            "sender_email": lead.sender_email,
            "phone": lead.phone,
            "location": lead.location,
            "subject": lead.subject,
            "body_excerpt": _excerpt(lead.body, 220),
            "summary_excerpt": _excerpt(lead.summary, 160),
            "category": lead.category,
            "urgency_score": lead.urgency_score,
            "status": lead.status,
            "pipeline_stage": lead.pipeline_stage,
            "outcome": lead.outcome,
            "deal_value": lead.deal_value,
            "owner_alert_needed": bool(lead.owner_alert_needed),
        }
        for lead in recent_leads
    ]

    recent_calls = [
        {
            "id": call.id,
            "org": _org_ref(org_by_id.get(call.org_id)),
            "from_number": call.from_number,
            "to_number": call.to_number,
            "status": call.status,
            "dial_status": call.dial_status,
            "answered_by_owner": bool(call.answered_by_owner),
            "duration_seconds": call.duration_seconds,
            "started_at": _iso(call.started_at),
        }
        for call in db.query(CallEvent).order_by(CallEvent.started_at.desc(), CallEvent.id.desc()).limit(25).all()
    ]

    recent_sms = [
        {
            "id": sms.id,
            "org": _org_ref(org_by_id.get(sms.org_id)),
            "direction": sms.direction,
            "to_number": sms.to_number,
            "purpose": sms.purpose,
            "status": sms.status,
            "body_excerpt": _excerpt(sms.body, 140),
            "error_message": _excerpt(sms.error_message, 140),
            "created_at": _iso(sms.created_at),
        }
        for sms in db.query(SmsNotification).order_by(SmsNotification.created_at.desc(), SmsNotification.id.desc()).limit(25).all()
    ]

    review_requests = [
        {
            "id": review.id,
            "org_id": review.org_id,
            "lead_id": review.lead_id,
            "scheduled_for": _iso(review.scheduled_for),
            "channel": review.channel,
            "status": review.status,
            "sent_at": _iso(review.sent_at),
            "error_message": _excerpt(review.error_message, 140),
        }
        for review in db.query(ReviewRequest)
        .order_by(ReviewRequest.scheduled_for.desc(), ReviewRequest.id.desc())
        .limit(25)
        .all()
    ]

    attention = [
        {
            "id": "billing",
            "label": "Billing not active",
            "count": sum(1 for org in orgs if not org_has_billing_access(org)),
        },
        {
            "id": "gmail",
            "label": "Gmail not connected",
            "count": sum(1 for row in org_payload if not row["integrations"]["gmail_connected"]),
        },
        {
            "id": "owner_alerts",
            "label": "Owner alerts missing",
            "count": sum(
                1
                for row in org_payload
                if not (
                    row["integrations"]["owner_alert_email"]
                    or row["integrations"]["sms_alert_to_number"]
                    or row["integrations"]["phone_numbers"]
                )
            ),
        },
        {"id": "review_requests", "label": "Due review requests", "count": int(due_reviews)},
    ]

    return {
        "generated_at": _iso(now),
        "admin": {
            "email": user.email,
            "allowed_emails": sorted(platform_admin_emails()),
        },
        "summary": {
            "organizations": len(orgs),
            "users": len(users),
            "active_users": sum(1 for row in users if row.is_active),
            "billing_active_orgs": sum(1 for org in orgs if org_has_billing_access(org)),
            "gmail_connected_orgs": sum(1 for row in settings_rows if row.google_oauth_email),
            "phone_numbers": sum(len(numbers) for numbers in phone_numbers_by_org.values()),
            "bookings": db.query(func.count(Booking.id)).scalar() or 0,
            "leads": int(total_leads),
            "open_leads": int(open_leads),
            "sent_leads": int(sent_leads),
            "spam_leads": int(spam_leads),
            "won_leads": int(won_leads),
            "pipeline_value": round(float(pipeline_value), 2),
            "won_revenue": round(float(won_revenue), 2),
            "due_reviews": int(due_reviews),
        },
        "attention": attention,
        "orgs": org_payload,
        "users": user_payload,
        "recent_leads": recent_lead_payload,
        "operations": {
            "recent_calls": recent_calls,
            "recent_sms": recent_sms,
            "review_requests": review_requests,
        },
    }
