from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import Organization, TrialCode, TrialCodeRedemption, User


class TrialCodeError(ValueError):
    pass


def normalize_trial_code(value: str | None) -> str:
    return " ".join((value or "").split()).strip().upper()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def validate_trial_code(db: Session, raw_code: str, *, now: datetime | None = None) -> TrialCode:
    code_value = normalize_trial_code(raw_code)
    code = db.query(TrialCode).filter(TrialCode.code == code_value).first()
    if not code:
        raise TrialCodeError("This code does not exist.")
    if not code.active:
        raise TrialCodeError("This code is no longer active.")

    current = now or _now()
    if code.expires_at and _as_utc(code.expires_at) < current:
        raise TrialCodeError("This code has expired.")

    max_redemptions = max(1, int(code.max_redemptions or 1))
    if int(code.redemption_count or 0) >= max_redemptions:
        if max_redemptions == 1:
            raise TrialCodeError("This code has already been used.")
        raise TrialCodeError("This code is no longer active.")

    return code


def has_active_pilot(org: Organization, *, now: datetime | None = None) -> bool:
    if org.subscription_status != "trialing" or not org.trial_ends_at:
        return False
    return _as_utc(org.trial_ends_at) >= (now or _now())


def trial_expired(org: Organization, *, now: datetime | None = None) -> bool:
    if org.subscription_status != "trialing" or not org.trial_ends_at:
        return False
    return _as_utc(org.trial_ends_at) < (now or _now())


def trial_days_left(org: Organization, *, now: datetime | None = None) -> int | None:
    if not org.trial_ends_at:
        return None
    seconds = (_as_utc(org.trial_ends_at) - (now or _now())).total_seconds()
    if seconds <= 0:
        return 0
    return max(0, int((seconds + 86399) // 86400))


def pilot_state(org: Organization, *, now: datetime | None = None) -> str:
    if trial_expired(org, now=now):
        return "ended"
    if has_active_pilot(org, now=now):
        days_left = trial_days_left(org, now=now)
        return "ending_soon" if days_left is not None and days_left <= 3 else "active"
    return "none"


def redeem_trial_code(
    db: Session,
    *,
    org: Organization,
    user: User,
    raw_code: str,
    now: datetime | None = None,
) -> TrialCode:
    current = now or _now()
    if has_active_pilot(org, now=current):
        raise TrialCodeError("This workspace already has an active pilot.")

    code = validate_trial_code(db, raw_code, now=current)
    trial_days = max(1, int(code.trial_days or 14))
    normalized = normalize_trial_code(code.code)

    org.subscription_status = "trialing"
    org.plan = "founding_pilot"
    org.trial_started_at = current
    org.trial_ends_at = current + timedelta(days=trial_days)
    org.pilot_code = normalized

    code.redemption_count = int(code.redemption_count or 0) + 1
    if max(1, int(code.max_redemptions or 1)) == 1:
        code.redeemed_by_user_id = user.id
        code.redeemed_by_workspace_id = org.id
        code.redeemed_at = current

    db.add(
        TrialCodeRedemption(
            trial_code_id=code.id,
            code=normalized,
            user_id=user.id,
            org_id=org.id,
            trial_days=trial_days,
            redeemed_at=current,
        )
    )
    return code
