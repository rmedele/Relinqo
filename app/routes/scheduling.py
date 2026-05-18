import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import get_current_user, get_org_settings
from app.billing import org_has_billing_access
from app.database import get_db
from app.models import Booking, Lead, OrgSettings, ScheduleAvailability, User
from app.routes.leads import log_activity
from app.schemas import (
    AvailableSlotResponse,
    BookingCreateRequest,
    BookingResponse,
    ScheduleAvailabilityCreate,
    ScheduleAvailabilityResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)


# --- Authenticated endpoints (business owner) ---


@router.get("/api/schedule/availability", response_model=list[ScheduleAvailabilityResponse])
def list_availability(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return (
        db.query(ScheduleAvailability)
        .filter(ScheduleAvailability.org_id == user.org_id)
        .order_by(ScheduleAvailability.day_of_week, ScheduleAvailability.start_time)
        .all()
    )


@router.put("/api/schedule/availability", response_model=list[ScheduleAvailabilityResponse])
def replace_availability(
    slots: list[ScheduleAvailabilityCreate],
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Bulk replace weekly availability windows."""
    # Validate
    for slot in slots:
        if slot.day_of_week < 0 or slot.day_of_week > 6:
            raise HTTPException(400, f"day_of_week must be 0-6, got {slot.day_of_week}")
        if slot.start_time >= slot.end_time:
            raise HTTPException(400, f"start_time must be before end_time: {slot.start_time} >= {slot.end_time}")

    # Delete existing and insert new
    db.query(ScheduleAvailability).filter(ScheduleAvailability.org_id == user.org_id).delete()
    created = []
    for slot in slots:
        row = ScheduleAvailability(
            org_id=user.org_id,
            day_of_week=slot.day_of_week,
            start_time=slot.start_time,
            end_time=slot.end_time,
            is_active=slot.is_active,
        )
        db.add(row)
        created.append(row)
    db.commit()
    for row in created:
        db.refresh(row)
    return created


@router.get("/api/schedule/bookings", response_model=list[BookingResponse])
def list_bookings(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    q = db.query(Booking).filter(Booking.org_id == user.org_id)
    if status:
        q = q.filter(Booking.status == status)
    return q.order_by(Booking.slot_start.desc()).offset((page - 1) * page_size).limit(page_size).all()


@router.patch("/api/schedule/bookings/{booking_id}", response_model=BookingResponse)
def update_booking_status(
    booking_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    new_status: str = Query(..., alias="status"),
):
    booking = db.query(Booking).filter(Booking.id == booking_id, Booking.org_id == user.org_id).first()
    if not booking:
        raise HTTPException(404, "Booking not found")
    if new_status not in {"confirmed", "cancelled", "completed"}:
        raise HTTPException(400, "Status must be one of: confirmed, cancelled, completed")

    booking.status = new_status
    if new_status == "cancelled":
        booking.cancelled_at = datetime.now(timezone.utc)
        # Best-effort: remove the matching event from Google Calendar.
        if booking.google_event_id:
            from app.calendar_sync import calendar_sync_active, delete_booking_event
            org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).first()
            if calendar_sync_active(org_settings):
                delete_booking_event(booking, org_settings, db)
    db.commit()
    db.refresh(booking)

    if booking.lead_id:
        log_activity(db, booking.lead_id, booking.org_id, "booking_updated", f"Booking {new_status} for {booking.customer_name} at {booking.slot_start.strftime('%b %d %I:%M %p')}")

    return booking


# --- Public endpoints (no auth — customer-facing) ---


def _get_lead_by_booking_token(db: Session, token: str) -> Lead:
    lead = db.query(Lead).filter(Lead.booking_token == token).first()
    if not lead:
        raise HTTPException(404, "Booking link not found")
    return lead


def _generate_available_slots(
    db: Session,
    org_id: int,
    org_settings: OrgSettings,
) -> list[dict]:
    """Generate available time slots for the next N days based on availability windows."""
    availability = (
        db.query(ScheduleAvailability)
        .filter(ScheduleAvailability.org_id == org_id, ScheduleAvailability.is_active == True)
        .all()
    )
    if not availability:
        return []

    # Group by day of week
    by_day: dict[int, list[ScheduleAvailability]] = {}
    for avail in availability:
        by_day.setdefault(avail.day_of_week, []).append(avail)

    slot_duration = org_settings.scheduling_slot_duration if org_settings else 60
    buffer = org_settings.scheduling_buffer_minutes if org_settings else 0
    max_days = org_settings.scheduling_max_days_ahead if org_settings else 7

    now = datetime.now(timezone.utc)
    slots = []

    for day_offset in range(1, max_days + 1):
        day = now + timedelta(days=day_offset)
        dow = day.weekday()  # 0=Monday

        if dow not in by_day:
            continue

        for window in by_day[dow]:
            # Parse start/end times
            sh, sm = map(int, window.start_time.split(":"))
            eh, em = map(int, window.end_time.split(":"))
            window_start = day.replace(hour=sh, minute=sm, second=0, microsecond=0)
            window_end = day.replace(hour=eh, minute=em, second=0, microsecond=0)

            cursor = window_start
            while cursor + timedelta(minutes=slot_duration) <= window_end:
                slot_end = cursor + timedelta(minutes=slot_duration)
                slots.append({"slot_start": cursor, "slot_end": slot_end})
                cursor = slot_end + timedelta(minutes=buffer)

    # Remove slots that are already booked
    if slots:
        existing = (
            db.query(Booking)
            .filter(
                Booking.org_id == org_id,
                Booking.status == "confirmed",
                Booking.slot_start >= slots[0]["slot_start"],
                Booking.slot_end <= slots[-1]["slot_end"],
            )
            .all()
        )
        booked_starts = {b.slot_start for b in existing}
        slots = [s for s in slots if s["slot_start"] not in booked_starts]

    # Layer in Google Calendar busy windows (org's external commitments).
    # Fail-open: if FreeBusy returns nothing or errors, slots are still shown.
    from app.calendar_sync import busy_windows, calendar_sync_active

    if slots and calendar_sync_active(org_settings):
        cal_busy = busy_windows(
            org_settings, db,
            start=slots[0]["slot_start"],
            end=slots[-1]["slot_end"],
        )
        if cal_busy:
            slots = [
                s for s in slots
                if not any(
                    s["slot_start"] < busy_end and s["slot_end"] > busy_start
                    for busy_start, busy_end in cal_busy
                )
            ]

    return slots


@router.get("/api/public/book/{token}/info")
def get_booking_info(token: str, db: Session = Depends(get_db)):
    """Return business name and lead context for the booking page."""
    lead = _get_lead_by_booking_token(db, token)
    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == lead.org_id).first()
    return {
        "business_name": org_settings.business_name if org_settings else "Our Business",
        "business_phone": org_settings.business_phone if org_settings else "",
        "lead_subject": lead.subject,
        "lead_sender_name": lead.sender_name,
    }


@router.get("/api/public/book/{token}/slots", response_model=list[AvailableSlotResponse])
def get_available_slots(token: str, db: Session = Depends(get_db)):
    lead = _get_lead_by_booking_token(db, token)
    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == lead.org_id).first()
    if not org_settings:
        return []
    slots = _generate_available_slots(db, lead.org_id, org_settings)
    return [AvailableSlotResponse(**s) for s in slots]


@router.post("/api/public/book/{token}", response_model=BookingResponse)
def create_booking(
    token: str,
    payload: BookingCreateRequest,
    db: Session = Depends(get_db),
):
    lead = _get_lead_by_booking_token(db, token)
    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == lead.org_id).first()
    if not org_settings:
        raise HTTPException(400, "Scheduling not configured")

    slot_duration = org_settings.scheduling_slot_duration
    slot_end = payload.slot_start + timedelta(minutes=slot_duration)

    # Validate slot is in the available set
    available = _generate_available_slots(db, lead.org_id, org_settings)
    slot_valid = any(
        s["slot_start"] == payload.slot_start and s["slot_end"] == slot_end
        for s in available
    )
    if not slot_valid:
        raise HTTPException(400, "Selected time slot is not available")

    # Race condition guard — check for conflicting booking
    conflict = (
        db.query(Booking)
        .filter(
            Booking.org_id == lead.org_id,
            Booking.status == "confirmed",
            Booking.slot_start < slot_end,
            Booking.slot_end > payload.slot_start,
        )
        .first()
    )
    if conflict:
        raise HTTPException(409, "This time slot was just booked. Please choose another.")

    booking = Booking(
        org_id=lead.org_id,
        lead_id=lead.id,
        token=secrets.token_urlsafe(32),
        slot_start=payload.slot_start,
        slot_end=slot_end,
        customer_name=payload.customer_name,
        customer_email=payload.customer_email,
        customer_phone=payload.customer_phone,
        customer_notes=payload.customer_notes,
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)

    # Log activity on the lead
    time_str = payload.slot_start.strftime("%b %d at %I:%M %p")
    log_activity(
        db, lead.id, lead.org_id, "booking_created",
        f"{payload.customer_name} booked {time_str}. Phone: {payload.customer_phone or 'n/a'}",
    )

    # Push to Google Calendar (best-effort) before notifications, so the owner
    # alert email implicitly confirms the calendar entry exists.
    from app.calendar_sync import calendar_sync_active, push_booking_event
    if calendar_sync_active(org_settings):
        push_booking_event(booking, lead, org_settings, db)

    # Notifications are handled in Step 9 — wired into this handler
    _send_booking_notifications(db, booking, lead, org_settings)

    logger.info("Booking created: lead_id=%s slot=%s", lead.id, payload.slot_start)
    return booking


def _send_booking_notifications(db: Session, booking: Booking, lead: Lead, org_settings: OrgSettings | None):
    """Send owner + customer notifications for a new booking."""
    from app.mailer import send_email, smtp_configured
    from app.sms import send_sms

    if org_settings and (
        org_settings.automation_paused
        or (org_settings.org and not org_has_billing_access(org_settings.org))
    ):
        return

    time_str = booking.slot_start.strftime("%b %d at %I:%M %p")

    # Notify owner via email
    if smtp_configured(org_settings):
        owner_email = org_settings.owner_alert_email if org_settings else ""
        if owner_email:
            send_email(
                to_email=owner_email,
                subject=f"New booking: {booking.customer_name} on {time_str}",
                body=(
                    f"A customer just booked a time slot.\n\n"
                    f"Name: {booking.customer_name}\n"
                    f"Email: {booking.customer_email}\n"
                    f"Phone: {booking.customer_phone or 'n/a'}\n"
                    f"Time: {time_str}\n"
                    f"Notes: {booking.customer_notes or '(none)'}\n\n"
                    f"Original lead: {lead.subject or '(no subject)'}\n"
                ),
                org_settings=org_settings,
            )

        # Send confirmation to customer
        business_name = org_settings.business_name if org_settings else "Our team"
        send_email(
            to_email=booking.customer_email,
            subject=f"Booking confirmed: {time_str}",
            body=(
                f"Hi {booking.customer_name},\n\n"
                f"Your appointment with {business_name} is confirmed for {time_str}.\n\n"
                f"If you need to reschedule, please reply to this email or call us"
                f"{' at ' + org_settings.business_phone if org_settings and org_settings.business_phone else ''}.\n\n"
                f"Thanks,\n{business_name}\n"
            ),
            org_settings=org_settings,
        )

    # Notify owner via SMS
    if org_settings and org_settings.twilio_account_sid and org_settings.sms_alert_to_number:
        send_sms(
            f"New booking from {booking.customer_name} on {time_str}. "
            f"Phone: {booking.customer_phone or 'n/a'}",
            org_settings=org_settings,
        )
