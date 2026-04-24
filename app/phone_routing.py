"""Helpers for phone-call routing decisions: business hours, owner
dialing, phone-number normalization.

Pure functions and DB reads only. No Twilio calls, no side effects.
"""
import logging
import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.models import OrgSettings, PhoneBusinessHours, PhoneRoutingRule

logger = logging.getLogger(__name__)


def normalize_phone(raw: str | None) -> str:
    """Return digits-only phone number. Twilio always sends E.164 (+15551234567)
    so stripping non-digits is safe for comparison."""
    if not raw:
        return ""
    return re.sub(r"\D", "", raw)


def get_routing_rule(db: Session, org_id: int) -> PhoneRoutingRule | None:
    return db.query(PhoneRoutingRule).filter(PhoneRoutingRule.org_id == org_id).first()


def _org_timezone(db: Session, org_id: int) -> ZoneInfo:
    """Resolve the timezone for business-hours calculations.

    Phone business-hours rows each carry a timezone, but in practice an
    org operates in one TZ. Use the first row's tz; fall back to
    org_settings.default_timezone; fall back to UTC.
    """
    row = db.query(PhoneBusinessHours).filter(
        PhoneBusinessHours.org_id == org_id,
    ).first()
    candidate = (row.timezone if row else None)
    if not candidate:
        settings_row = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
        candidate = settings_row.default_timezone if settings_row else None
    try:
        return ZoneInfo(candidate) if candidate else ZoneInfo("UTC")
    except ZoneInfoNotFoundError:
        logger.warning("unknown timezone %r for org_id=%s, falling back to UTC", candidate, org_id)
        return ZoneInfo("UTC")


def _parse_hhmm(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    m = re.match(r"^(\d{1,2}):(\d{2})$", value.strip())
    if not m:
        return None
    h, mm = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mm <= 59):
        return None
    return h, mm


def is_within_business_hours(db: Session, org_id: int, now_utc: datetime) -> bool:
    """Return True if `now_utc` falls inside an open window per the org's
    phone_business_hours rows.

    If there are no rows configured, treat as "always open" (24/7) so
    users who haven't set hours still get their phone rung — the
    owner-phone configuration is the real gate, not the hours table.
    """
    rows = db.query(PhoneBusinessHours).filter(
        PhoneBusinessHours.org_id == org_id,
    ).all()
    if not rows:
        return True

    tz = _org_timezone(db, org_id)
    local_now = now_utc.astimezone(tz)
    dow = local_now.weekday()  # 0=Mon, 6=Sun
    cur_minutes = local_now.hour * 60 + local_now.minute

    for row in rows:
        if row.day_of_week != dow:
            continue
        open_t = _parse_hhmm(row.open_time)
        close_t = _parse_hhmm(row.close_time)
        if not open_t or not close_t:
            # Explicit closed day (open/close both null) — skip.
            continue
        open_m = open_t[0] * 60 + open_t[1]
        close_m = close_t[0] * 60 + close_t[1]
        if open_m <= cur_minutes < close_m:
            return True
    return False


def should_dial_owner(rule: PhoneRoutingRule | None, in_hours: bool) -> bool:
    """Policy gate. Returns True iff we should ring the owner first."""
    if not rule:
        return False
    if not rule.ring_owner_first:
        return False
    if not (rule.owner_phone or "").strip():
        return False
    if not in_hours:
        return False
    return True
