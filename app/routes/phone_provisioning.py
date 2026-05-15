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
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_owner_active
from app.config import settings
from app.database import get_db
from app.models import CallEvent, Lead, PhoneNumber, PhoneRoutingRule, SmsNotification, User
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


class RescueSetupRequest(BaseModel):
    area_code: str
    owner_phone: str
    ring_timeout_seconds: int = 20
    ring_owner_first: bool = True


class ForwardingSetupRequest(BaseModel):
    current_business_number: str
    owner_phone: str
    area_code: str
    carrier: str = "unknown"
    ring_timeout_seconds: int = 20


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


def _upsert_routing_rule(
    db: Session,
    *,
    org_id: int,
    owner_phone: str,
    ring_owner_first: bool = True,
    ring_timeout_seconds: int = 20,
    send_caller_confirmation: bool = True,
) -> PhoneRoutingRule:
    owner = _normalize_e164(owner_phone)
    rule = db.query(PhoneRoutingRule).filter(PhoneRoutingRule.org_id == org_id).first()
    if rule is None:
        rule = PhoneRoutingRule(
            org_id=org_id,
            owner_phone=owner,
            ring_owner_first=ring_owner_first,
            ring_timeout_seconds=ring_timeout_seconds,
            send_caller_confirmation=send_caller_confirmation,
        )
        db.add(rule)
    else:
        rule.owner_phone = owner
        rule.ring_owner_first = ring_owner_first
        rule.ring_timeout_seconds = ring_timeout_seconds
        rule.send_caller_confirmation = send_caller_confirmation
    return rule


def _carrier_key(carrier: str | None) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", (carrier or "unknown").strip().lower()).strip("_")
    aliases = {
        "i_dont_know": "unknown",
        "dont_know": "unknown",
        "other_manual": "other",
        "at_t": "att",
        "at_t_wireless": "att",
    }
    return aliases.get(key, key or "unknown")


def _format_phone_for_code(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _forwarding_code(rescue_number: str, carrier: str | None) -> dict:
    carrier_id = _carrier_key(carrier)
    digits = _format_phone_for_code(rescue_number)
    code = f"*61*{rescue_number}#"
    tel_code = f"*61*{quote(rescue_number, safe='+')}%23"
    notes = "Run this from the phone that owns your current business number."
    carrier_name = carrier_id.replace("_", " ").title()

    if carrier_id in {"telus", "rogers", "bell"}:
        carrier_name = {"telus": "Telus", "rogers": "Rogers", "bell": "Bell"}[carrier_id]
        notes = "Works for many Canadian mobile lines. If it fails, try your carrier's call-forwarding settings."
    elif carrier_id in {"verizon", "att", "t_mobile"}:
        carrier_name = {"verizon": "Verizon", "att": "AT&T", "t_mobile": "T-Mobile"}[carrier_id]
        notes = "Works for many US mobile lines. If it fails, try your carrier's call-forwarding settings."
    elif carrier_id in {"other", "unknown"}:
        carrier_name = "Other carrier"
        notes = "This code works on many mobile carriers, but some require enabling missed-call forwarding in account settings."

    return {
        "carrier": carrier_id,
        "carrier_name": carrier_name,
        "activation_code": code,
        "tel_link": f"tel:{tel_code}",
        "plain_digits": digits,
        "notes": notes,
        "steps": [
            "Open this from the phone that owns your current business number.",
            "Tap Activate missed-call rescue, then press call in your phone app.",
            "Come back to LeadRelay and run the test.",
        ],
    }


def _forwarding_payload(
    row: PhoneNumber | None,
    rule: PhoneRoutingRule | None,
    *,
    test_call: CallEvent | None = None,
) -> dict:
    if not rule:
        return {
            "status": "not_started",
            "current_business_number": "",
            "carrier": "unknown",
            "activation": None,
            "verified_at": None,
            "test_call_event_id": None,
            "message": "Missed-call rescue has not been set up yet.",
        }

    activation = _forwarding_code(row.phone_number, rule.forwarding_carrier) if row else None
    status = rule.forwarding_setup_status or "not_started"
    message = {
        "not_started": "Missed-call rescue has not been set up yet.",
        "provisioned": "Your rescue number is ready. Activate forwarding from your current business phone.",
        "activation_shown": "Activate forwarding from your current business phone, then run the test.",
        "testing": "Call your current business number from another phone and do not answer.",
        "live": "You're live. Missed calls will now be rescued.",
        "failed": "We did not see a forwarded test call yet. Try the code again or choose manual help.",
    }.get(status, "Missed-call rescue status updated.")

    return {
        "status": status,
        "current_business_number": rule.current_business_number,
        "carrier": rule.forwarding_carrier,
        "activation": activation,
        "verified_at": rule.forwarding_verified_at.isoformat() if rule.forwarding_verified_at else None,
        "test_started_at": rule.forwarding_test_started_at.isoformat() if rule.forwarding_test_started_at else None,
        "test_call_event_id": rule.forwarding_test_call_event_id,
        "test_call": None if test_call is None else {
            "id": test_call.id,
            "from_number": test_call.from_number,
            "to_number": test_call.to_number,
            "started_at": test_call.started_at.isoformat() if test_call.started_at else None,
        },
        "message": message,
    }


def _phone_payload(row: PhoneNumber | None, rule: PhoneRoutingRule | None) -> dict:
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
            "current_business_number": rule.current_business_number,
            "forwarding_carrier": rule.forwarding_carrier,
            "forwarding_setup_status": rule.forwarding_setup_status,
            "forwarding_verified_at": rule.forwarding_verified_at.isoformat() if rule.forwarding_verified_at else None,
        },
    }


def _sms_payload(row: SmsNotification | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "to_number": row.to_number,
        "purpose": row.purpose,
        "status": row.status,
        "twilio_message_sid": row.twilio_message_sid,
        "error_message": row.error_message,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _call_payload(row: CallEvent | None) -> dict | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "from_number": row.from_number,
        "to_number": row.to_number,
        "status": row.status,
        "dial_status": row.dial_status,
        "answered_by_owner": row.answered_by_owner,
        "is_after_hours": row.is_after_hours,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _latest_sms(db: Session, org_id: int, purpose: str) -> SmsNotification | None:
    return db.query(SmsNotification).filter(
        SmsNotification.org_id == org_id,
        SmsNotification.purpose == purpose,
    ).order_by(
        SmsNotification.created_at.desc(),
        SmsNotification.id.desc(),
    ).first()


def _rescue_stats(db: Session, org_id: int, row: PhoneNumber | None, rule: PhoneRoutingRule | None) -> dict:
    rescued_calls = db.query(CallEvent).filter(
        CallEvent.org_id == org_id,
        CallEvent.answered_by_owner == False,  # noqa: E712
    ).count()
    phone_leads = db.query(Lead).filter(
        Lead.org_id == org_id,
        Lead.source == "phone",
    ).count()
    outreach_sent = db.query(SmsNotification).filter(
        SmsNotification.org_id == org_id,
        SmsNotification.purpose == "outreach",
        SmsNotification.status.in_(("sent", "delivered")),
    ).count()
    last_call = db.query(CallEvent).filter(
        CallEvent.org_id == org_id,
    ).order_by(
        CallEvent.created_at.desc(),
        CallEvent.id.desc(),
    ).first()
    return {
        "is_live": bool(row and rule and rule.owner_phone),
        "rescued_calls": rescued_calls,
        "phone_leads": phone_leads,
        "outreach_sent": outreach_sent,
        "last_call": _call_payload(last_call),
        "last_outreach": _sms_payload(_latest_sms(db, org_id, "outreach")),
        "last_owner_alert": _sms_payload(_latest_sms(db, org_id, "owner_alert")),
        "last_caller_confirmation": _sms_payload(_latest_sms(db, org_id, "caller_confirmation")),
    }


@router.post("/search")
def search_numbers(
    payload: SearchRequest,
    user: User = Depends(require_owner_active),
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
    user: User = Depends(require_owner_active),
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


@router.post("/rescue-setup")
def rescue_setup(
    payload: RescueSetupRequest,
    user: User = Depends(require_owner_active),
    db: Session = Depends(get_db),
):
    """Grandpa-proof setup: user enters an area code + cell number.
    LeadRelay finds, buys, configures, and saves routing for a number.

    If the org already has an active number, this endpoint simply updates
    routing and returns the live setup instead of making the user start over.
    """
    area_code = (payload.area_code or "").strip()
    if not re.match(r"^\d{3}$", area_code):
        raise HTTPException(status_code=400, detail="Enter a 3-digit area code.")

    owner = _normalize_e164(payload.owner_phone)
    if len(re.sub(r"\D", "", owner)) < 10:
        raise HTTPException(status_code=400, detail="Enter the cell phone we should ring.")

    existing = db.query(PhoneNumber).filter(
        PhoneNumber.org_id == user.org_id,
        PhoneNumber.is_active == True,  # noqa: E712
    ).first()
    if existing:
        rule = _upsert_routing_rule(
            db,
            org_id=user.org_id,
            owner_phone=owner,
            ring_owner_first=payload.ring_owner_first,
            ring_timeout_seconds=payload.ring_timeout_seconds,
            send_caller_confirmation=True,
        )
        db.commit()
        db.refresh(rule)
        return {
            "ok": True,
            "already_had_number": True,
            **_phone_payload(existing, rule),
            "rescue": _rescue_stats(db, user.org_id, existing, rule),
            "message": "Your missed-call rescue line is live.",
        }

    voice_url, status_url = _webhook_urls()
    try:
        numbers = search_available_numbers(area_code=area_code, country="US", limit=1)
        if not numbers:
            # Canadian area codes are common for this product; try CA before failing.
            numbers = search_available_numbers(area_code=area_code, country="CA", limit=1)
        if not numbers:
            raise HTTPException(status_code=404, detail="No numbers found in that area code. Try a nearby area code.")

        chosen = numbers[0]["phone_number"]
        result = provision_number(
            chosen,
            voice_url=voice_url,
            status_callback_url=status_url,
            friendly_name=f"LeadRelay rescue line {chosen}",
        )
    except HTTPException:
        raise
    except TwilioError as e:
        logger.warning("twilio rescue setup failed area=%s: %s", area_code, e.message)
        raise HTTPException(status_code=e.status, detail=e.message)

    row = PhoneNumber(
        org_id=user.org_id,
        twilio_sid=result["sid"],
        phone_number=result["phone_number"],
        friendly_name=result.get("friendly_name") or f"LeadRelay rescue line {chosen}",
        is_active=True,
    )
    db.add(row)
    rule = _upsert_routing_rule(
        db,
        org_id=user.org_id,
        owner_phone=owner,
        ring_owner_first=payload.ring_owner_first,
        ring_timeout_seconds=payload.ring_timeout_seconds,
        send_caller_confirmation=True,
    )
    db.commit()
    db.refresh(row)
    db.refresh(rule)

    logger.info("rescue setup live phone_number=%s org_id=%s", row.phone_number, user.org_id)
    return {
        "ok": True,
        "already_had_number": False,
        **_phone_payload(row, rule),
        "rescue": _rescue_stats(db, user.org_id, row, rule),
        "message": "Your missed-call rescue line is live.",
    }


@router.post("/rescue-forwarding/setup")
def rescue_forwarding_setup(
    payload: ForwardingSetupRequest,
    user: User = Depends(require_owner_active),
    db: Session = Depends(get_db),
):
    """Set up the easy path: keep the public number, forward missed
    calls to a LeadRelay rescue number, then test that forwarding works.
    """
    area_code = (payload.area_code or "").strip()
    if not re.match(r"^\d{3}$", area_code):
        raise HTTPException(status_code=400, detail="Enter a 3-digit area code.")

    owner = _normalize_e164(payload.owner_phone)
    if len(re.sub(r"\D", "", owner)) < 10:
        raise HTTPException(status_code=400, detail="Enter the cell phone we should ring.")

    current_business_number = _normalize_e164(payload.current_business_number)
    if len(re.sub(r"\D", "", current_business_number)) < 10:
        raise HTTPException(status_code=400, detail="Enter the current business number customers call today.")

    row = db.query(PhoneNumber).filter(
        PhoneNumber.org_id == user.org_id,
        PhoneNumber.is_active == True,  # noqa: E712
    ).first()

    if row is None:
        voice_url, status_url = _webhook_urls()
        try:
            numbers = search_available_numbers(area_code=area_code, country="US", limit=1)
            if not numbers:
                numbers = search_available_numbers(area_code=area_code, country="CA", limit=1)
            if not numbers:
                raise HTTPException(status_code=404, detail="No rescue numbers found in that area code. Try a nearby area code.")

            chosen = numbers[0]["phone_number"]
            result = provision_number(
                chosen,
                voice_url=voice_url,
                status_callback_url=status_url,
                friendly_name=f"LeadRelay missed-call rescue {chosen}",
            )
        except HTTPException:
            raise
        except TwilioError as e:
            logger.warning("twilio forwarding setup failed area=%s: %s", area_code, e.message)
            raise HTTPException(status_code=e.status, detail=e.message)

        row = PhoneNumber(
            org_id=user.org_id,
            twilio_sid=result["sid"],
            phone_number=result["phone_number"],
            friendly_name=result.get("friendly_name") or f"LeadRelay missed-call rescue {chosen}",
            is_active=True,
        )
        db.add(row)

    rule = _upsert_routing_rule(
        db,
        org_id=user.org_id,
        owner_phone=owner,
        ring_owner_first=True,
        ring_timeout_seconds=payload.ring_timeout_seconds,
        send_caller_confirmation=True,
    )
    rule.current_business_number = current_business_number
    rule.forwarding_carrier = _carrier_key(payload.carrier)
    rule.forwarding_code_used = _forwarding_code(row.phone_number, rule.forwarding_carrier)["activation_code"]
    rule.forwarding_setup_status = "activation_shown"
    rule.forwarding_test_started_at = None
    rule.forwarding_test_call_event_id = None
    rule.forwarding_verified_at = None
    db.commit()
    db.refresh(row)
    db.refresh(rule)

    return {
        "ok": True,
        **_phone_payload(row, rule),
        "rescue": _rescue_stats(db, user.org_id, row, rule),
        "forwarding": _forwarding_payload(row, rule),
    }


@router.post("/rescue-forwarding/test/start")
def rescue_forwarding_test_start(
    user: User = Depends(require_owner_active),
    db: Session = Depends(get_db),
):
    row = db.query(PhoneNumber).filter(
        PhoneNumber.org_id == user.org_id,
        PhoneNumber.is_active == True,  # noqa: E712
    ).first()
    rule = db.query(PhoneRoutingRule).filter(PhoneRoutingRule.org_id == user.org_id).first()
    if not row or not rule or not rule.current_business_number:
        raise HTTPException(status_code=400, detail="Set up missed-call rescue before starting a test.")

    rule.forwarding_setup_status = "testing"
    rule.forwarding_test_started_at = datetime.now(timezone.utc)
    rule.forwarding_test_call_event_id = None
    rule.forwarding_verified_at = None
    db.commit()
    db.refresh(rule)

    return {
        "ok": True,
        "forwarding": _forwarding_payload(row, rule),
    }


@router.get("/rescue-forwarding/test/status")
def rescue_forwarding_test_status(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.query(PhoneNumber).filter(
        PhoneNumber.org_id == user.org_id,
        PhoneNumber.is_active == True,  # noqa: E712
    ).first()
    rule = db.query(PhoneRoutingRule).filter(PhoneRoutingRule.org_id == user.org_id).first()
    if not row or not rule:
        return {"ok": True, "forwarding": _forwarding_payload(row, rule)}

    test_call = None
    if rule.forwarding_test_started_at:
        test_call = (
            db.query(CallEvent)
            .filter(
                CallEvent.org_id == user.org_id,
                CallEvent.to_number == row.phone_number,
                CallEvent.started_at >= rule.forwarding_test_started_at,
            )
            .order_by(CallEvent.started_at.desc(), CallEvent.id.desc())
            .first()
        )

    if test_call:
        rule.forwarding_setup_status = "live"
        rule.forwarding_test_call_event_id = test_call.id
        rule.forwarding_verified_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(rule)

    return {
        "ok": True,
        "forwarding": _forwarding_payload(row, rule, test_call=test_call),
    }


@router.post("/rescue-forwarding/reset")
def rescue_forwarding_reset(
    user: User = Depends(require_owner_active),
    db: Session = Depends(get_db),
):
    row = db.query(PhoneNumber).filter(
        PhoneNumber.org_id == user.org_id,
        PhoneNumber.is_active == True,  # noqa: E712
    ).first()
    rule = db.query(PhoneRoutingRule).filter(PhoneRoutingRule.org_id == user.org_id).first()
    if not rule:
        return {"ok": True, "forwarding": _forwarding_payload(row, rule)}

    rule.forwarding_setup_status = "activation_shown" if row and rule.current_business_number else "not_started"
    rule.forwarding_test_started_at = None
    rule.forwarding_test_call_event_id = None
    rule.forwarding_verified_at = None
    db.commit()
    db.refresh(rule)
    return {"ok": True, "forwarding": _forwarding_payload(row, rule)}


@router.post("/adopt")
def adopt(
    payload: AdoptRequest,
    user: User = Depends(require_owner_active),
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
        **_phone_payload(row, rule),
        "rescue": _rescue_stats(db, user.org_id, row, rule),
        "forwarding": _forwarding_payload(row, rule),
    }


@router.post("/routing")
def set_routing(
    payload: RoutingRequest,
    user: User = Depends(require_owner_active),
    db: Session = Depends(get_db),
):
    """Upsert the org's routing rule. This is what gets Bob live —
    until he sets his owner_phone, the system just sends calls
    straight to voicemail/SMS-outreach without trying to ring him."""
    owner = _normalize_e164(payload.owner_phone) if payload.owner_phone else ""

    rule = db.query(PhoneRoutingRule).filter(PhoneRoutingRule.org_id == user.org_id).first()
    if rule is None:
        rule = _upsert_routing_rule(
            db,
            org_id=user.org_id,
            owner_phone=owner,
            ring_owner_first=payload.ring_owner_first,
            ring_timeout_seconds=payload.ring_timeout_seconds,
            send_caller_confirmation=payload.send_caller_confirmation,
        )
        rule.voicemail_greeting = payload.voicemail_greeting
        rule.after_hours_greeting = payload.after_hours_greeting
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
    user: User = Depends(require_owner_active),
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
