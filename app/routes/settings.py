from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_org_settings, require_owner
from app.database import get_db
from app.models import OrgSettings, User

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
    # Behavior
    human_review: bool
    auto_send_confidence_threshold: float
    forwarding_token: str
    owner_alert_email: str
    digest_to_email: str
    default_timezone: str


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
    human_review: bool | None = None
    auto_send_confidence_threshold: float | None = None
    forwarding_token: str | None = None
    owner_alert_email: str | None = None
    digest_to_email: str | None = None
    default_timezone: str | None = None


@router.get("", response_model=OrgSettingsResponse)
def get_settings(
    user: User = Depends(require_owner),
    org_settings: OrgSettings = Depends(get_org_settings),
):
    return OrgSettingsResponse(
        smtp_host=org_settings.smtp_host,
        smtp_port=org_settings.smtp_port,
        smtp_username=org_settings.smtp_username,
        smtp_from_email=org_settings.smtp_from_email,
        smtp_use_tls=org_settings.smtp_use_tls,
        imap_host=org_settings.imap_host,
        imap_port=org_settings.imap_port,
        imap_username=org_settings.imap_username,
        imap_mailbox=org_settings.imap_mailbox,
        imap_search_criteria=org_settings.imap_search_criteria,
        inbox_poll_enabled=org_settings.inbox_poll_enabled,
        business_name=org_settings.business_name,
        business_services=org_settings.business_services,
        business_area=org_settings.business_area,
        business_hours=org_settings.business_hours,
        business_phone=org_settings.business_phone,
        business_tone=org_settings.business_tone,
        business_reply_signature=org_settings.business_reply_signature,
        twilio_account_sid=org_settings.twilio_account_sid,
        twilio_from_number=org_settings.twilio_from_number,
        sms_alert_to_number=org_settings.sms_alert_to_number,
        google_oauth_email=org_settings.google_oauth_email,
        scheduling_enabled=org_settings.scheduling_enabled,
        scheduling_slot_duration=org_settings.scheduling_slot_duration,
        scheduling_buffer_minutes=org_settings.scheduling_buffer_minutes,
        scheduling_max_days_ahead=org_settings.scheduling_max_days_ahead,
        google_calendar_id=org_settings.google_calendar_id,
        google_calendar_sync_enabled=org_settings.google_calendar_sync_enabled,
        review_request_enabled=org_settings.review_request_enabled,
        review_url=org_settings.review_url,
        review_delay_hours=org_settings.review_delay_hours,
        review_request_channel=org_settings.review_request_channel,
        review_request_subject=org_settings.review_request_subject,
        review_request_body=org_settings.review_request_body,
        human_review=org_settings.human_review,
        auto_send_confidence_threshold=org_settings.auto_send_confidence_threshold,
        forwarding_token=org_settings.forwarding_token,
        owner_alert_email=org_settings.owner_alert_email,
        digest_to_email=org_settings.digest_to_email,
        default_timezone=org_settings.default_timezone,
    )


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
