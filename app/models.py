import secrets

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


def _generate_api_key() -> str:
    return secrets.token_hex(32)


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), nullable=False, unique=True, index=True)
    api_key = Column(String(64), nullable=False, unique=True, index=True, default=_generate_api_key)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    users = relationship("User", back_populates="org", cascade="all, delete-orphan")
    settings = relationship("OrgSettings", back_populates="org", uselist=False, cascade="all, delete-orphan")
    leads = relationship("Lead", back_populates="org")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=True)
    role = Column(String(20), nullable=False, default="member")
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    org = relationship("Organization", back_populates="users")


class OrgSettings(Base):
    __tablename__ = "org_settings"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, unique=True)

    # SMTP
    smtp_host = Column(String(255), nullable=False, default="")
    smtp_port = Column(Integer, nullable=False, default=587)
    smtp_username = Column(String(255), nullable=False, default="")
    smtp_password = Column(String(255), nullable=False, default="")
    smtp_use_tls = Column(Boolean, nullable=False, default=True)
    smtp_from_email = Column(String(255), nullable=False, default="")

    # IMAP
    imap_host = Column(String(255), nullable=False, default="imap.gmail.com")
    imap_port = Column(Integer, nullable=False, default=993)
    imap_username = Column(String(255), nullable=False, default="")
    imap_password = Column(String(255), nullable=False, default="")
    imap_mailbox = Column(String(100), nullable=False, default="INBOX")
    imap_search_criteria = Column(String(100), nullable=False, default="UNSEEN")
    inbox_poll_enabled = Column(Boolean, nullable=False, default=False)

    # Business profile
    business_name = Column(String(255), nullable=False, default="")
    business_services = Column(Text, nullable=False, default="")
    business_area = Column(String(255), nullable=False, default="")
    business_hours = Column(String(255), nullable=False, default="Mon-Fri 8am-5pm")
    business_phone = Column(String(100), nullable=False, default="")
    business_tone = Column(String(255), nullable=False, default="friendly and professional")
    business_reply_signature = Column(Text, nullable=False, default="")

    # Twilio
    twilio_account_sid = Column(String(100), nullable=False, default="")
    twilio_auth_token = Column(String(100), nullable=False, default="")
    twilio_from_number = Column(String(50), nullable=False, default="")
    sms_alert_to_number = Column(String(50), nullable=False, default="")

    # Google OAuth
    google_oauth_access_token = Column(Text, nullable=False, default="")
    google_oauth_refresh_token = Column(Text, nullable=False, default="")
    google_oauth_token_expires_at = Column(DateTime(timezone=True), nullable=True)
    google_oauth_email = Column(String(255), nullable=False, default="")

    # Scheduling
    scheduling_enabled = Column(Boolean, nullable=False, default=False)
    scheduling_slot_duration = Column(Integer, nullable=False, default=60)
    scheduling_buffer_minutes = Column(Integer, nullable=False, default=0)
    scheduling_max_days_ahead = Column(Integer, nullable=False, default=7)

    # Behavior
    human_review = Column(Boolean, nullable=False, default=True)
    auto_send_confidence_threshold = Column(Float, nullable=False, default=0.85)
    forwarding_token = Column(String(100), nullable=False, default="")
    owner_alert_email = Column(String(255), nullable=False, default="")
    digest_to_email = Column(String(255), nullable=False, default="")
    default_timezone = Column(String(100), nullable=False, default="America/Edmonton")

    org = relationship("Organization", back_populates="settings")


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    source = Column(String(100), nullable=False)
    sender_name = Column(String(255), nullable=True)
    sender_email = Column(String(255), nullable=False, index=True)
    subject = Column(String(500), nullable=True)
    body = Column(Text, nullable=False)
    phone = Column(String(100), nullable=True)
    location = Column(String(255), nullable=True)
    category = Column(String(50), nullable=False, default="general_inquiry", index=True)
    urgency_score = Column(Integer, nullable=False, default=1)
    summary = Column(Text, nullable=True)
    recommended_reply = Column(Text, nullable=True)
    owner_alert_needed = Column(Boolean, nullable=False, default=False)
    status = Column(String(50), nullable=False, default="new", index=True)
    confidence = Column(Float, nullable=False, default=0.0)
    next_step = Column(String(255), nullable=True)
    raw_payload = Column(Text, nullable=True)
    thread_id = Column(String(255), nullable=True, index=True)
    send_at = Column(DateTime(timezone=True), nullable=True)
    outcome = Column(String(50), nullable=True, index=True)  # won, lost, no_response
    outcome_notes = Column(Text, nullable=True)
    outcome_at = Column(DateTime(timezone=True), nullable=True)
    parent_lead_id = Column(Integer, ForeignKey("leads.id"), nullable=True, index=True)

    booking_token = Column(String(64), nullable=True, unique=True, index=True)
    call_event_id = Column(Integer, ForeignKey("call_events.id"), nullable=True, index=True)

    org = relationship("Organization", back_populates="leads")
    call_event = relationship("CallEvent", back_populates="lead")
    parent_lead = relationship("Lead", remote_side="Lead.id", backref="replies", foreign_keys=[parent_lead_id])
    followups = relationship("FollowUp", back_populates="lead", cascade="all, delete-orphan")
    owner_alerts = relationship("OwnerAlert", back_populates="lead", cascade="all, delete-orphan")
    activities = relationship("LeadActivity", back_populates="lead", cascade="all, delete-orphan")
    photos = relationship("LeadPhoto", back_populates="lead", cascade="all, delete-orphan")
    bookings = relationship("Booking", back_populates="lead")

    @hybrid_property
    def photo_count(self):
        return len(self.photos) if self.photos else 0


class FollowUp(Base):
    __tablename__ = "followups"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    scheduled_for = Column(DateTime(timezone=True), nullable=False, index=True)
    followup_type = Column(String(50), nullable=False)
    status = Column(String(50), nullable=False, default="scheduled", index=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)

    lead = relationship("Lead", back_populates="followups")


class OwnerAlert(Base):
    __tablename__ = "owner_alerts"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    alert_type = Column(String(50), nullable=False)
    sent_to = Column(String(255), nullable=False)
    sent_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=True)
    status = Column(String(50), nullable=False, default="queued")

    lead = relationship("Lead", back_populates="owner_alerts")


class LeadActivity(Base):
    __tablename__ = "lead_activities"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    activity_type = Column(String(50), nullable=False)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    lead = relationship("Lead", back_populates="activities")


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token = Column(String(64), nullable=False, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)


class InviteToken(Base):
    __tablename__ = "invite_tokens"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    email = Column(String(255), nullable=False)
    token = Column(String(64), nullable=False, unique=True, index=True)
    display_name = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=True)


class InboxMessage(Base):
    __tablename__ = "inbox_messages"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    source = Column(String(50), nullable=False, default="imap")
    external_id = Column(String(255), nullable=False, unique=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class LeadPhoto(Base):
    __tablename__ = "lead_photos"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    stored_filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer, nullable=False)
    mime_type = Column(String(100), nullable=False)
    ai_analysis = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    lead = relationship("Lead", back_populates="photos")


class ScheduleAvailability(Base):
    __tablename__ = "schedule_availability"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    day_of_week = Column(Integer, nullable=False)  # 0=Monday, 6=Sunday
    start_time = Column(String(5), nullable=False)  # "09:00"
    end_time = Column(String(5), nullable=False)  # "17:00"
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Booking(Base):
    __tablename__ = "bookings"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=True, index=True)
    token = Column(String(64), nullable=False, unique=True, index=True)
    slot_start = Column(DateTime(timezone=True), nullable=False)
    slot_end = Column(DateTime(timezone=True), nullable=False)
    customer_name = Column(String(255), nullable=False)
    customer_email = Column(String(255), nullable=False)
    customer_phone = Column(String(100), nullable=True)
    customer_notes = Column(Text, nullable=True)
    status = Column(String(50), nullable=False, default="confirmed")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)

    lead = relationship("Lead", back_populates="bookings")


class PhoneNumber(Base):
    __tablename__ = "phone_numbers"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    twilio_sid = Column(String(100), nullable=False, unique=True, index=True)
    phone_number = Column(String(32), nullable=False, unique=True, index=True)  # E.164
    friendly_name = Column(String(255), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PhoneBusinessHours(Base):
    __tablename__ = "phone_business_hours"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    day_of_week = Column(Integer, nullable=False)  # 0=Mon, 6=Sun
    open_time = Column(String(5), nullable=True)   # "09:00", null = closed
    close_time = Column(String(5), nullable=True)  # "17:00"
    timezone = Column(String(100), nullable=False, default="America/Edmonton")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PhoneRoutingRule(Base):
    __tablename__ = "phone_routing_rules"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, unique=True, index=True)
    ring_owner_first = Column(Boolean, nullable=False, default=True)
    owner_phone = Column(String(32), nullable=False, default="")  # E.164
    ring_timeout_seconds = Column(Integer, nullable=False, default=20)
    send_caller_confirmation = Column(Boolean, nullable=False, default=True)
    voicemail_greeting = Column(Text, nullable=True)
    after_hours_greeting = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class CallEvent(Base):
    __tablename__ = "call_events"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    twilio_call_sid = Column(String(64), nullable=False, unique=True, index=True)
    from_number = Column(String(32), nullable=False, index=True)
    to_number = Column(String(32), nullable=False, index=True)
    from_city = Column(String(100), nullable=True)
    from_state = Column(String(100), nullable=True)
    direction = Column(String(20), nullable=False, default="inbound")
    status = Column(String(30), nullable=False, default="ringing")
    dial_status = Column(String(30), nullable=True)
    answered_by_owner = Column(Boolean, nullable=False, default=False)
    duration_seconds = Column(Integer, nullable=True)
    is_after_hours = Column(Boolean, nullable=False, default=False)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    voicemail = relationship("Voicemail", back_populates="call_event", uselist=False, cascade="all, delete-orphan")
    lead = relationship("Lead", back_populates="call_event", uselist=False)


class Voicemail(Base):
    __tablename__ = "voicemails"

    id = Column(Integer, primary_key=True, index=True)
    call_event_id = Column(Integer, ForeignKey("call_events.id"), nullable=False, index=True)
    twilio_recording_sid = Column(String(64), nullable=False, unique=True, index=True)
    recording_url = Column(String(500), nullable=False)
    recording_duration = Column(Integer, nullable=False, default=0)
    transcript = Column(Text, nullable=True)
    transcript_confidence = Column(Float, nullable=True)
    transcription_status = Column(String(20), nullable=False, default="pending")
    classified_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    call_event = relationship("CallEvent", back_populates="voicemail")


class SmsNotification(Base):
    __tablename__ = "sms_notifications"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=True)
    call_event_id = Column(Integer, ForeignKey("call_events.id"), nullable=True)
    direction = Column(String(10), nullable=False, default="outbound")
    to_number = Column(String(32), nullable=False, index=True)
    body = Column(Text, nullable=False)
    twilio_message_sid = Column(String(64), nullable=True)
    status = Column(String(20), nullable=False, default="queued")
    purpose = Column(String(40), nullable=False, index=True)  # owner_alert | caller_confirmation | approval_request
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
