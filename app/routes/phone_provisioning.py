"""Phone-number provisioning API.

The whole point of this module: Bob never has to touch Twilio. He clicks
"Create my business line" in the LeadRelay UI, we buy him a number
on the platform Twilio account, configure the webhooks, and he's live
in under 5 seconds.

Endpoints:
  - POST /api/phone/search      -> search available numbers by area code
  - POST /api/phone/provision   -> buy + configure a specific number
  - GET  /api/phone/my-number   -> return the org's current phone setup
  - POST /api/phone/routing     -> set owner_phone + ring preferences
  - POST /api/phone/release     -> release the number (debug/admin only)
"""
import logging
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import settings
from app.database import get_db
from app.models import PhoneNumber, PhoneRoutingRule, User
from app.twilio_client import (
    TwilioError,
    lookup_owned_number,
    provision_number,
    release_number,
    search_available_numbers,
    update_number_webhooks,
)

router = APIRouter(prefix="/api/phone", tags=["phone-provisioning"])
logger = logging.getLogger(__name__)


class SearchRequest(BaseModel):
    area_code: str
    country: str = "US"
    limit: int = 5


class ProvisionRequest(BaseModel):
    phone_number: str  # E.164, e.g. "+15874567890"
    friendly_name: str | None = None


class AdoptRequest(BaseModel):
    """For a number the user bought in the Twilio Console before
    onboarding. We look it up by E.164, reconfigure its webhooks, and
    save it to the org. No Twilio charge — it's already theirs."""
    phone_number: str
    owner_phone: str | None = None


class RoutingRequest(BaseModel):
    owner_phone: str | None = None        # E.164
    ring_owner_first: bool = True
    ring_timeout_seconds: int = 20
    send_caller_confirmation: bool = True
    voicemail_greeting: str | None = None
    after_hours_greeting: str | None = None


def _webhook_urls() -> tuple[str, str]:
    """Absolute URLs for Twilio to hit. Built from PUBLIC_BASE_URL."""
    base = (settings.public_base_url or "").rstrip("/")
    if not base:
        raise HTTPException(
            status_code=500,
            detail="PUBLIC_BASE_URL not configured — can't provision webhooks.",
        )
    return (
        f"{base}/twilio/voice/incoming",
        f"{base}/twilio/voice/call-status",
    )


def _normalize_e164(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return ""
    if raw.strip().startswith("+"):
        return f"+{digits}"
    # Assume US/CA if no country code
    if len(digits) == 10:
        return f"+1{digits}"
    return f"+{digits}"


@router.post("/search")
def search_numbers(
    payload: SearchRequest,
    user: User = Depends(get_current_user),
):
    """Find available Twilio numbers in the requested area code."""
    if not re.match(r"^\d{3}$", (payload.area_code or "").strip()):
        raise HTTPException(status_code=400, detail="area_code must be 3 digits")
    try:
        numbers = search_available_numbers(
            area_code=payload.area_code.strip(),
            country=payload.country,
            limit=max(1, min(payload.limit, 20)),
        )
    except TwilioError as e:
        logger.warning("twilio search failed: %s", e.message)
        raise HTTPException(status_code=e.status, detail=e.message)
    return {"numbers": numbers}


@router.post("/provision")
def provision(
    payload: ProvisionRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Buy the number on Twilio, configure webhooks, and save it to
    this org. Idempotent at the Twilio layer — calling twice with the
    same PhoneNumber returns 400 from Twilio, which we surface."""
    phone = _normalize_e164(payload.phone_number)
    if not phone:
        raise HTTPException(status_code=400, detail="phone_number required in E.164 format")

    # One number per org in V1 — fail fast if they already have one.
    existing = db.query(PhoneNumber).filter(
        PhoneNumber.org_id == user.org_id,
        PhoneNumber.is_active == True,  # noqa: E712
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Org already has phone number {existing.phone_number}. Release it first.",
        )

    voice_url, status_url = _webhook_urls()
    try:
        result = provision_number(
            phone,
            voice_url=voice_url,
            status_callback_url=status_url,
            friendly_name=payload.friendly_name,
        )
    except TwilioError as e:
        logger.warning("twilio provision failed for %s: %s", phone, e.message)
        raise HTTPException(status_code=e.status, detail=e.message)

    row = PhoneNumber(
        org_id=user.org_id,
        twilio_sid=result["sid"],
        phone_number=result["phone_number"],
        friendly_name=result.get("friendly_name") or payload.friendly_name,
        is_active=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    logger.info(
        "provisioned phone_number=%s sid=%s for org_id=%s",
        row.phone_number, row.twilio_sid, user.org_id,
    )
    return {
        "id": row.id,
        "phone_number": row.phone_number,
        "twilio_sid": row.twilio_sid,
        "friendly_name": row.friendly_name,
        "voice_url": voice_url,
        "status_callback_url": status_url,
    }


@router.post("/adopt")
def adopt(
    payload: AdoptRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Take over a number the user already bought in the Twilio console.
    Looks it up on their Twilio account, reconfigures its webhooks to
    point at LeadRelay, and saves it to the org. Optionally writes the
    routing rule (owner_phone) in the same call so the UI can 'finish
    setup' in one click.
    """
    phone = _normalize_e164(payload.phone_number)
    if not phone:
        raise HTTPException(status_code=400, detail="phone_number required in E.164 format")

    existing_local = db.query(PhoneNumber).filter(
        PhoneNumber.org_id == user.org_id,
        PhoneNumber.is_active == True,  # noqa: E712
    ).first()
    if existing_local:
        raise HTTPException(
            status_code=409,
            detail=f"Org already has phone number {existing_local.phone_number}. Release it first.",
        )

    try:
        remote = lookup_owned_number(phone)
    except TwilioError as e:
        logger.warning("twilio lookup failed for %s: %s", phone, e.message)
        raise HTTPException(status_code=e.status, detail=e.message)

    if not remote:
        raise HTTPException(
            status_code=404,
            detail=f"Number {phone} not found on this Twilio account. Buy it in the Twilio Console first, or use /provision to buy via LeadRelay.",
        )

    sid = remote.get("sid")
    voice_url, status_url = _webhook_urls()
    sms_url = f"{settings.public_base_url.rstrip('/')}/sms/webhook"

    try:
        update_number_webhooks(
            sid,
            voice_url=voice_url,
            status_callback_url=status_url,
            sms_url=sms_url,
            friendly_name=f"LeadRelay {phone}",
        )
    except TwilioError as e:
        logger.warning("twilio webhook update failed for %s: %s", sid, e.message)
        raise HTTPException(status_code=e.status, detail=e.message)

    row = PhoneNumber(
        org_id=user.org_id,
        twilio_sid=sid,
        phone_number=remote.get("phone_number") or phone,
        friendly_name=f"LeadRelay {phone}",
        is_active=True,
    )
    db.add(row)

    # If owner_phone provided, upsert the routing rule in one shot
    if payload.owner_phone:
        owner = _normalize_e164(payload.owner_phone)
        rule = db.query(PhoneRoutingRule).filter(PhoneRoutingRule.org_id == user.org_id).first()
        if rule is None:
            rule = PhoneRoutingRule(
                org_id=user.org_id,
                owner_phone=owner,
                ring_owner_first=True,
                ring_timeout_seconds=20,
                send_caller_confirmation=True,
            )
            db.add(rule)
        else:
            rule.owner_phone = owner
            rule.ring_owner_first = True

    db.commit()
    db.refresh(row)

    logger.info(
        "adopted phone_number=%s sid=%s org_id=%s",
        row.phone_number, row.twilio_sid, user.org_id,
    )
    return {
        "id": row.id,
        "phone_number": row.phone_number,
        "twilio_sid": row.twilio_sid,
        "friendly_name": row.friendly_name,
        "voice_url": voice_url,
        "status_callback_url": status_url,
        "sms_url": sms_url,
    }


@router.get("/my-number")
def my_number(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(PhoneNumber).filter(
        PhoneNumber.org_id == user.org_id,
        PhoneNumber.is_active == True,  # noqa: E712
    ).first()
    rule = db.query(PhoneRoutingRule).filter(PhoneRoutingRule.org_id == user.org_id).first()
    return {
        "phone": None if row is None else {
            "id": row.id,
            "phone_number": row.phone_number,
            "friendly_name": row.friendly_name,
            "twilio_sid": row.twilio_sid,
        },
        "routing": None if rule is None else {
            "owner_phone": rule.owner_phone,
            "ring_owner_first": rule.ring_owner_first,
            "ring_timeout_seconds": rule.ring_timeout_seconds,
            "send_caller_confirmation": rule.send_caller_confirmation,
            "voicemail_greeting": rule.voicemail_greeting,
            "after_hours_greeting": rule.after_hours_greeting,
        },
    }


@router.post("/routing")
def set_routing(
    payload: RoutingRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upsert the org's routing rule. This is what gets Bob live —
    until he sets his owner_phone, the system just sends calls
    straight to voicemail/SMS-outreach without trying to ring him."""
    owner = _normalize_e164(payload.owner_phone) if payload.owner_phone else ""

    rule = db.query(PhoneRoutingRule).filter(PhoneRoutingRule.org_id == user.org_id).first()
    if rule is None:
        rule = PhoneRoutingRule(
            org_id=user.org_id,
            owner_phone=owner,
            ring_owner_first=payload.ring_owner_first,
            ring_timeout_seconds=payload.ring_timeout_seconds,
            send_caller_confirmation=payload.send_caller_confirmation,
            voicemail_greeting=payload.voicemail_greeting,
            after_hours_greeting=payload.after_hours_greeting,
        )
        db.add(rule)
    else:
        rule.owner_phone = owner
        rule.ring_owner_first = payload.ring_owner_first
        rule.ring_timeout_seconds = payload.ring_timeout_seconds
        rule.send_caller_confirmation = payload.send_caller_confirmation
        rule.voicemail_greeting = payload.voicemail_greeting
        rule.after_hours_greeting = payload.after_hours_greeting
    db.commit()

    return {"ok": True, "owner_phone": owner, "ring_owner_first": payload.ring_owner_first}


@router.post("/release/{phone_id}")
def release(
    phone_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Release a provisioned number (DELETE on Twilio, soft-delete locally).
    Useful for dev/test so you can re-provision against a clean slate."""
    row = db.query(PhoneNumber).filter(
        PhoneNumber.id == phone_id,
        PhoneNumber.org_id == user.org_id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Number not found")
    try:
        release_number(row.twilio_sid)
    except TwilioError as e:
        # If Twilio says "already gone" (404), proceed with local cleanup.
        if e.status != 404:
            raise HTTPException(status_code=e.status, detail=e.message)
    row.is_active = False
    db.commit()
    return {"ok": True, "released": row.phone_number}
