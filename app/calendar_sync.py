"""Google Calendar two-way sync.

Reuses the org's existing Google OAuth credentials (gmail OAuth tokens stored on
OrgSettings). Three operations:

  - push_booking_event(booking, lead, org_settings, db) — creates a Calendar event
    when a booking is made, stores the resulting event id on the Booking row.
  - delete_booking_event(booking, org_settings, db) — best-effort delete on cancel.
  - busy_windows(org_settings, db, start, end) — returns a list of (start, end)
    tuples covering busy time in the org's calendar; used to filter out slots
    that conflict with existing calendar entries.

Calendar sync requires (a) OAuth refresh token present, (b)
google_calendar_sync_enabled=True. If the calendar scope was not granted at
OAuth time the API call will 403 — the wrapper catches that and logs without
breaking the booking flow.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import Booking, Lead, OrgSettings

logger = logging.getLogger(__name__)


def calendar_sync_active(org_settings: OrgSettings | None) -> bool:
    if not org_settings:
        return False
    return bool(
        org_settings.google_calendar_sync_enabled
        and org_settings.google_oauth_refresh_token
    )


def _build_service(org_settings: OrgSettings, db: Session | None):
    from googleapiclient.discovery import build  # local import for testability

    from app.gmail import _get_credentials

    creds = _get_credentials(org_settings, db)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def push_booking_event(
    booking: Booking,
    lead: Lead | None,
    org_settings: OrgSettings,
    db: Session,
) -> str | None:
    """Create a Calendar event for the booking. Returns the event id, or None on
    failure (the booking is still valid; sync is best-effort)."""
    if not calendar_sync_active(org_settings):
        return None

    business = org_settings.business_name or "reqlinqo"
    summary = f"{booking.customer_name} — {business}"
    description_lines = [
        f"Customer: {booking.customer_name}",
        f"Email: {booking.customer_email}",
    ]
    if booking.customer_phone:
        description_lines.append(f"Phone: {booking.customer_phone}")
    if lead and lead.subject:
        description_lines.append(f"Original inquiry: {lead.subject}")
    if booking.customer_notes:
        description_lines.append("")
        description_lines.append(f"Notes: {booking.customer_notes}")
    if lead and lead.summary:
        description_lines.append("")
        description_lines.append(f"Lead summary: {lead.summary}")

    tz = org_settings.default_timezone or "UTC"

    event_body = {
        "summary": summary,
        "description": "\n".join(description_lines),
        "start": {"dateTime": booking.slot_start.isoformat(), "timeZone": tz},
        "end": {"dateTime": booking.slot_end.isoformat(), "timeZone": tz},
        "attendees": [{"email": booking.customer_email, "displayName": booking.customer_name}],
        "reminders": {"useDefault": True},
    }

    try:
        service = _build_service(org_settings, db)
        created = service.events().insert(
            calendarId=org_settings.google_calendar_id or "primary",
            body=event_body,
            sendUpdates="all",
        ).execute()
        event_id = created.get("id")
        booking.google_event_id = event_id
        db.commit()
        logger.info("Calendar event created booking_id=%s event_id=%s", booking.id, event_id)
        return event_id
    except Exception:
        logger.exception("Failed to push booking %s to Google Calendar", booking.id)
        return None


def delete_booking_event(
    booking: Booking,
    org_settings: OrgSettings,
    db: Session,
) -> bool:
    if not calendar_sync_active(org_settings):
        return False
    if not booking.google_event_id:
        return False

    try:
        service = _build_service(org_settings, db)
        service.events().delete(
            calendarId=org_settings.google_calendar_id or "primary",
            eventId=booking.google_event_id,
            sendUpdates="all",
        ).execute()
        booking.google_event_id = None
        db.commit()
        return True
    except Exception:
        logger.exception("Failed to delete calendar event for booking %s", booking.id)
        return False


def busy_windows(
    org_settings: OrgSettings,
    db: Session,
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, datetime]]:
    """Return busy intervals from the org's primary calendar between start/end.
    Empty list on any failure (fail-open: the booking page still shows slots
    based on availability windows + existing bookings)."""
    if not calendar_sync_active(org_settings):
        return []

    try:
        service = _build_service(org_settings, db)
        result = service.freebusy().query(
            body={
                "timeMin": start.astimezone(timezone.utc).isoformat(),
                "timeMax": end.astimezone(timezone.utc).isoformat(),
                "items": [{"id": org_settings.google_calendar_id or "primary"}],
            }
        ).execute()
        cal_id = org_settings.google_calendar_id or "primary"
        busy = result.get("calendars", {}).get(cal_id, {}).get("busy", [])
        windows: list[tuple[datetime, datetime]] = []
        for entry in busy:
            try:
                s = datetime.fromisoformat(entry["start"].replace("Z", "+00:00"))
                e = datetime.fromisoformat(entry["end"].replace("Z", "+00:00"))
                windows.append((s, e))
            except (KeyError, ValueError):
                continue
        return windows
    except Exception:
        logger.exception("FreeBusy query failed for org %s", org_settings.org_id)
        return []
