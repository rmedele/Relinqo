from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr


class LeadIngestRequest(BaseModel):
    source: str
    sender_name: str | None = None
    sender_email: EmailStr
    subject: str | None = None
    body: str


class LeadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
    source: str
    sender_name: str | None
    sender_email: str
    subject: str | None
    body: str
    phone: str | None
    location: str | None
    category: str
    urgency_score: int
    summary: str | None
    recommended_reply: str | None
    owner_alert_needed: bool
    status: str
    confidence: float
    next_step: str | None
    raw_payload: str | None
    thread_id: str | None = None
    send_at: datetime | None = None
    outcome: str | None = None
    outcome_notes: str | None = None
    outcome_at: datetime | None = None
    parent_lead_id: int | None = None
    booking_token: str | None = None
    photo_count: int = 0


class ClassificationResult(BaseModel):
    category: str
    urgency_score: int
    summary: str
    recommended_reply: str
    owner_alert_needed: bool
    confidence: float
    next_step: str
    extracted_phone: str | None = None
    extracted_location: str | None = None
    photo_analysis: str | None = None


class SendReviewResponse(BaseModel):
    lead_id: int
    status: str
    sent: bool
    message: str
    sent_to: str | None = None
    subject: str | None = None


class LeadActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: int
    activity_type: str
    message: str
    created_at: datetime


class LeadUpdateRequest(BaseModel):
    subject: str | None = None
    body: str | None = None
    recommended_reply: str | None = None
    status: str | None = None
    next_step: str | None = None


class LeadOutcomeRequest(BaseModel):
    outcome: str  # won, lost, no_response
    outcome_notes: str | None = None


class ForwardedEmailIngestRequest(BaseModel):
    token: str
    raw_email: str | None = None
    from_email: EmailStr | None = None
    from_name: str | None = None
    subject: str | None = None
    body: str | None = None
    source: str = "forwarded_email"


class DigestResponse(BaseModel):
    status: str
    summary: dict[str, Any]


class StatsResponse(BaseModel):
    total_leads: int
    today_leads: int
    sent_count: int
    response_rate: float
    avg_response_minutes: float | None
    by_category: dict[str, int]
    by_status: dict[str, int]
    by_outcome: dict[str, int]
    close_rate: float | None
    avg_close_minutes: float | None


class PaginatedLeadsResponse(BaseModel):
    items: list[LeadResponse]
    total: int
    page: int
    page_size: int
    pages: int


class HealthResponse(BaseModel):
    ok: bool
    app: str


# --- Photo schemas ---

class LeadPhotoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: int
    filename: str
    file_size: int
    mime_type: str
    ai_analysis: str | None
    created_at: datetime


# --- Scheduling schemas ---

class ScheduleAvailabilityCreate(BaseModel):
    day_of_week: int  # 0=Monday, 6=Sunday
    start_time: str  # "09:00"
    end_time: str  # "17:00"
    is_active: bool = True


class ScheduleAvailabilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    org_id: int
    day_of_week: int
    start_time: str
    end_time: str
    is_active: bool


class AvailableSlotResponse(BaseModel):
    slot_start: datetime
    slot_end: datetime


class BookingCreateRequest(BaseModel):
    customer_name: str
    customer_email: EmailStr
    customer_phone: str | None = None
    customer_notes: str | None = None
    slot_start: datetime


class BookingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    org_id: int
    lead_id: int | None
    token: str
    slot_start: datetime
    slot_end: datetime
    customer_name: str
    customer_email: str
    customer_phone: str | None
    customer_notes: str | None
    status: str
    created_at: datetime
    cancelled_at: datetime | None
