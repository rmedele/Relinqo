from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_org_settings, require_owner
from app.billing import org_has_billing_access
from app.database import get_db
from app.models import OrgSettings, PhoneNumber, PhoneRoutingRule, ReplyTemplate, ScheduleAvailability, SmsNotification, User
from app.outbound_webhooks import send_webhook_event

router = APIRouter(prefix="/api/settings", tags=["settings"])


class OrgSettingsResponse(BaseModel):
    # SMTP
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_from_email: str
    smtp_use_tls: bool
    # IMAP
    imap_host: str
    imap_port: int
    imap_username: str
    imap_mailbox: str
    imap_search_criteria: str
    inbox_poll_enabled: bool
    # Business
    business_name: str
    business_services: str
    business_area: str
    business_hours: str
    business_phone: str
    business_tone: str
    business_reply_signature: str
    # Twilio
    twilio_account_sid: str
    twilio_from_number: str
    sms_alert_to_number: str
    # Google OAuth
    google_oauth_email: str
    # Scheduling
    scheduling_enabled: bool
    scheduling_slot_duration: int
    scheduling_buffer_minutes: int
    scheduling_max_days_ahead: int
    # Calendar sync
    google_calendar_id: str
    google_calendar_sync_enabled: bool
    # Review automation
    review_request_enabled: bool
    review_url: str
    review_delay_hours: int
    review_request_channel: str
    review_request_subject: str
    review_request_body: str
    # Outbound webhooks
    outbound_webhook_enabled: bool
    outbound_webhook_url: str
    outbound_webhook_events: str
    # Behavior
    human_review: bool
    automation_paused: bool
    auto_send_confidence_threshold: float
    forwarding_token: str
    owner_alert_email: str
    digest_to_email: str
    default_timezone: str
    subscription_status: str
    plan: str


class OrgSettingsUpdate(BaseModel):
    # All optional for PATCH
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str | None = None
    smtp_use_tls: bool | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    imap_username: str | None = None
    imap_password: str | None = None
    imap_mailbox: str | None = None
    imap_search_criteria: str | None = None
    inbox_poll_enabled: bool | None = None
    business_name: str | None = None
    business_services: str | None = None
    business_area: str | None = None
    business_hours: str | None = None
    business_phone: str | None = None
    business_tone: str | None = None
    business_reply_signature: str | None = None
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None
    sms_alert_to_number: str | None = None
    scheduling_enabled: bool | None = None
    scheduling_slot_duration: int | None = None
    scheduling_buffer_minutes: int | None = None
    scheduling_max_days_ahead: int | None = None
    google_calendar_id: str | None = None
    google_calendar_sync_enabled: bool | None = None
    review_request_enabled: bool | None = None
    review_url: str | None = None
    review_delay_hours: int | None = None
    review_request_channel: str | None = None
    review_request_subject: str | None = None
    review_request_body: str | None = None
    outbound_webhook_enabled: bool | None = None
    outbound_webhook_url: str | None = None
    outbound_webhook_secret: str | None = None
    outbound_webhook_events: str | None = None
    human_review: bool | None = None
    automation_paused: bool | None = None
    auto_send_confidence_threshold: float | None = None
    forwarding_token: str | None = None
    owner_alert_email: str | None = None
    digest_to_email: str | None = None
    default_timezone: str | None = None


def _as_str(value: object, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _as_int(value: object, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default
    return bool(value)


def _build_settings_response(user: User, org_settings: OrgSettings) -> OrgSettingsResponse:
    return OrgSettingsResponse(
        smtp_host=_as_str(org_settings.smtp_host),
        smtp_port=_as_int(org_settings.smtp_port, 587),
        smtp_username=_as_str(org_settings.smtp_username),
        smtp_from_email=_as_str(org_settings.smtp_from_email),
        smtp_use_tls=_as_bool(org_settings.smtp_use_tls, True),
        imap_host=_as_str(org_settings.imap_host, "imap.gmail.com"),
        imap_port=_as_int(org_settings.imap_port, 993),
        imap_username=_as_str(org_settings.imap_username),
        imap_mailbox=_as_str(org_settings.imap_mailbox, "INBOX"),
        imap_search_criteria=_as_str(org_settings.imap_search_criteria, "UNSEEN"),
        inbox_poll_enabled=_as_bool(org_settings.inbox_poll_enabled),
        business_name=_as_str(org_settings.business_name),
        business_services=_as_str(org_settings.business_services),
        business_area=_as_str(org_settings.business_area),
        business_hours=_as_str(org_settings.business_hours, "Mon-Fri 8am-5pm"),
        business_phone=_as_str(org_settings.business_phone),
        business_tone=_as_str(org_settings.business_tone, "friendly and professional"),
        business_reply_signature=_as_str(org_settings.business_reply_signature),
        twilio_account_sid=_as_str(org_settings.twilio_account_sid),
        twilio_from_number=_as_str(org_settings.twilio_from_number),
        sms_alert_to_number=_as_str(org_settings.sms_alert_to_number),
        google_oauth_email=_as_str(org_settings.google_oauth_email),
        scheduling_enabled=_as_bool(org_settings.scheduling_enabled),
        scheduling_slot_duration=_as_int(org_settings.scheduling_slot_duration, 60),
        scheduling_buffer_minutes=_as_int(org_settings.scheduling_buffer_minutes, 0),
        scheduling_max_days_ahead=_as_int(org_settings.scheduling_max_days_ahead, 7),
        google_calendar_id=_as_str(org_settings.google_calendar_id, "primary"),
        google_calendar_sync_enabled=_as_bool(org_settings.google_calendar_sync_enabled),
        review_request_enabled=_as_bool(org_settings.review_request_enabled),
        review_url=_as_str(org_settings.review_url),
        review_delay_hours=_as_int(org_settings.review_delay_hours, 72),
        review_request_channel=_as_str(org_settings.review_request_channel, "email"),
        review_request_subject=_as_str(
            org_settings.review_request_subject,
            "Quick favor - would you mind leaving us a review?",
        ),
        review_request_body=_as_str(org_settings.review_request_body),
        outbound_webhook_enabled=_as_bool(org_settings.outbound_webhook_enabled),
        outbound_webhook_url=_as_str(org_settings.outbound_webhook_url),
        outbound_webhook_events=_as_str(
            org_settings.outbound_webhook_events,
            "lead.created,booking.created,lead.won",
        ),
        human_review=_as_bool(org_settings.human_review, True),
        automation_paused=_as_bool(org_settings.automation_paused),
        auto_send_confidence_threshold=_as_float(org_settings.auto_send_confidence_threshold, 0.85),
        forwarding_token=_as_str(org_settings.forwarding_token),
        owner_alert_email=_as_str(org_settings.owner_alert_email),
        digest_to_email=_as_str(org_settings.digest_to_email),
        default_timezone=_as_str(org_settings.default_timezone, "America/Edmonton"),
        subscription_status=_as_str(user.org.subscription_status, "trialing"),
        plan=_as_str(user.org.plan, "beta"),
    )


class ReadinessItem(BaseModel):
    id: str
    label: str
    status: str
    detail: str
    action: str | None = None


class ReadinessResponse(BaseModel):
    ready: bool
    score: int
    completed: int
    total: int
    items: list[ReadinessItem]


class WebhookTestResponse(BaseModel):
    ok: bool
    message: str


@router.get("", response_model=OrgSettingsResponse)
def get_settings(
    user: User = Depends(require_owner),
    org_settings: OrgSettings = Depends(get_org_settings),
):
    return _build_settings_response(user, org_settings)


@router.get("/readiness", response_model=ReadinessResponse)
def pilot_readiness(
    user: User = Depends(require_owner),
    org_settings: OrgSettings = Depends(get_org_settings),
    db: Session = Depends(get_db),
):
    phone = db.query(PhoneNumber).filter(
        PhoneNumber.org_id == user.org_id,
        PhoneNumber.is_active.is_(True),
    ).first()
    routing = db.query(PhoneRoutingRule).filter(PhoneRoutingRule.org_id == user.org_id).first()
    availability_count = db.query(ScheduleAvailability).filter(
        ScheduleAvailability.org_id == user.org_id,
        ScheduleAvailability.is_active.is_(True),
    ).count()
    template_count = db.query(ReplyTemplate).filter(ReplyTemplate.org_id == user.org_id).count()
    latest_owner_alert = db.query(SmsNotification).filter(
        SmsNotification.org_id == user.org_id,
        SmsNotification.purpose == "owner_alert",
    ).order_by(SmsNotification.created_at.desc()).first()

    items: list[ReadinessItem] = []

    def add(item_id: str, label: str, status: str, detail: str, action: str | None = None) -> None:
        items.append(ReadinessItem(id=item_id, label=label, status=status, detail=detail, action=action))

    business_ready = all([
        org_settings.business_name,
        org_settings.business_services,
        org_settings.business_area,
        org_settings.business_hours,
    ])
    add(
        "business_profile",
        "Business profile",
        "ready" if business_ready else "missing",
        "Business name, service area, services, and hours are filled in."
        if business_ready
        else "Fill in name, services, service area, and business hours before a pilot.",
        "Business profile card",
    )

    gmail_ready = bool(org_settings.google_oauth_email and (org_settings.google_oauth_refresh_token or org_settings.google_oauth_access_token))
    fallback_email_ready = bool(org_settings.smtp_host and org_settings.smtp_username and org_settings.imap_username)
    add(
        "gmail",
        "Gmail inbox connected",
        "ready" if gmail_ready else "warning" if fallback_email_ready else "missing",
        f"Connected as {org_settings.google_oauth_email}."
        if gmail_ready
        else "SMTP/IMAP fallback exists, but Gmail OAuth is the cleaner pilot path."
        if fallback_email_ready
        else "Connect Gmail so Relinqo can read leads and send replies.",
        "Gmail integration card",
    )

    add(
        "billing",
        "Billing or pilot comp",
        "ready" if org_has_billing_access(user.org) else "missing",
        "Workspace has billing access."
        if org_has_billing_access(user.org)
        else "Start Stripe checkout or use the admin billing bypass for a comped pilot.",
        "Billing card",
    )

    owner_alert_configured = bool(org_settings.sms_alert_to_number or org_settings.owner_alert_email or (routing and routing.owner_phone))
    add(
        "owner_alert",
        "Owner alert destination",
        "ready" if owner_alert_configured else "missing",
        "Owner alert email or phone is configured."
        if owner_alert_configured
        else "Add the owner cell/email that should get hot-lead alerts.",
        "Alerts and behavior card",
    )

    if latest_owner_alert and latest_owner_alert.status in {"sent", "delivered"}:
        alert_status = "ready"
        alert_detail = f"Last owner SMS alert is {latest_owner_alert.status}."
    elif latest_owner_alert and latest_owner_alert.status in {"failed", "undelivered"}:
        alert_status = "missing"
        alert_detail = f"Last owner SMS alert failed: {(latest_owner_alert.error_message or 'check Twilio logs')[:140]}"
    else:
        alert_status = "missing"
        alert_detail = "Run one real phone/SMS test and confirm the owner alert arrives."
    add("owner_alert_test", "Owner SMS verified", alert_status, alert_detail, "Phone health panel")

    phone_ready = bool(phone and routing and routing.owner_phone)
    phone_warning = bool(phone and not (routing and routing.owner_phone))
    add(
        "phone_rescue",
        "Demo/pilot phone number",
        "ready" if phone_ready else "warning" if phone_warning else "missing",
        f"Active rescue number {phone.phone_number} rings {routing.owner_phone}."
        if phone_ready
        else f"Active number {phone.phone_number} exists, but owner routing is missing."
        if phone_warning and phone
        else "Provision or adopt a Twilio number and set the owner cell.",
        "Phone lead capture card",
    )

    forwarding_status = routing.forwarding_setup_status if routing else "not_started"
    forwarding_ready = forwarding_status == "live"
    forwarding_warning = forwarding_status in {"activation_shown", "testing"}
    add(
        "forwarding_test",
        "Missed-call forwarding test",
        "ready" if forwarding_ready else "warning" if forwarding_warning else "missing",
        "Forwarding test is live."
        if forwarding_ready
        else "Activation code has been shown; finish the missed-call test."
        if forwarding_warning
        else "Run the forwarding setup/test from the customer's current business number.",
        "Activation card",
    )

    scheduling_ready = bool(org_settings.scheduling_enabled and availability_count)
    add(
        "scheduling",
        "Booking availability",
        "ready" if scheduling_ready else "warning" if org_settings.scheduling_enabled else "missing",
        f"{availability_count} active availability window(s) are configured."
        if scheduling_ready
        else "Scheduling is enabled, but no active availability windows are set."
        if org_settings.scheduling_enabled
        else "Turn on smart scheduling and set at least one availability window.",
        "Smart scheduling card",
    )

    add(
        "calendar_sync",
        "Calendar conflict protection",
        "ready" if org_settings.google_calendar_sync_enabled and gmail_ready else "warning",
        "Google Calendar sync is enabled and can hide busy slots."
        if org_settings.google_calendar_sync_enabled and gmail_ready
        else "Optional but valuable: reconnect Gmail with calendar scope, then enable calendar sync.",
        "Sync with Google Calendar card",
    )

    review_ready = bool(org_settings.review_request_enabled and org_settings.review_url)
    add(
        "reviews",
        "Review request loop",
        "ready" if review_ready else "missing" if org_settings.review_request_enabled else "warning",
        "Won jobs can queue a review request."
        if review_ready
        else "Review automation is on, but the Google review URL is missing."
        if org_settings.review_request_enabled
        else "Optional but high leverage: add the Google review URL and enable review requests.",
        "Reviews on autopilot card",
    )

    add(
        "templates",
        "Reply templates",
        "ready" if template_count else "warning",
        f"{template_count} saved template(s) are ready."
        if template_count
        else "Add 2-3 common replies for quotes, scheduling, and thank-yous.",
        "Templates page",
    )

    webhook_ready = bool(org_settings.outbound_webhook_enabled and org_settings.outbound_webhook_url)
    add(
        "outbound_webhooks",
        "Zapier/Make handoff",
        "ready" if webhook_ready else "warning",
        "Lead, booking, and won-deal events can be sent to an external automation tool."
        if webhook_ready
        else "Optional: add a Zapier or Make webhook URL to push lead events into Sheets, CRM, or Slack.",
        "Outbound webhooks card",
    )

    add(
        "pilot_guardrails",
        "Pilot guardrails",
        "ready" if org_settings.human_review and not org_settings.automation_paused else "warning",
        "Human review is on and automation is not paused."
        if org_settings.human_review and not org_settings.automation_paused
        else "For the first pilot, keep human review on and automation unpaused.",
        "Automation behavior card",
    )

    completed = sum(1 for item in items if item.status == "ready")
    total = len(items)
    score = round((completed / total) * 100) if total else 0
    return ReadinessResponse(
        ready=all(item.status != "missing" for item in items),
        score=score,
        completed=completed,
        total=total,
        items=items,
    )


@router.post("/webhook/test", response_model=WebhookTestResponse)
def test_outbound_webhook(
    user: User = Depends(require_owner),
    org_settings: OrgSettings = Depends(get_org_settings),
):
    ok, message = send_webhook_event(
        org_settings,
        "webhook.test",
        {
            "message": "Relinqo outbound webhook test",
            "business_name": org_settings.business_name,
            "owner_email": user.email,
        },
        force=True,
    )
    return WebhookTestResponse(ok=ok, message=message)


@router.patch("", response_model=OrgSettingsResponse)
def update_settings(
    payload: OrgSettingsUpdate,
    user: User = Depends(require_owner),
    org_settings: OrgSettings = Depends(get_org_settings),
    db: Session = Depends(get_db),
):
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(org_settings, field, value)
    db.commit()
    db.refresh(org_settings)
    return get_settings(user=user, org_settings=org_settings)
