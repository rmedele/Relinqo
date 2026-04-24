"""Thin Twilio REST client for number search + provisioning.

Used by `/api/phone/*` endpoints so the app can buy a number on an
org's behalf and wire webhooks without anyone leaving the LeadRelay UI.
Uses stdlib HTTP to avoid adding the `twilio` SDK as a dependency.

Auth token selection:
  - Platform-level: `settings.twilio_account_sid` + `settings.twilio_auth_token`
    (from env). This is what the prod deployment should use.
  - Per-org fallback: `OrgSettings.twilio_*` for BYO-Twilio customers
    (not recommended for Bob, but preserved for power users).
"""
import base64
import json
import logging
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from app.config import settings
from app.models import OrgSettings

logger = logging.getLogger(__name__)

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"


class TwilioError(Exception):
    def __init__(self, status: int, code: int | None, message: str, more_info: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.more_info = more_info


def _credentials(org_settings: OrgSettings | None) -> tuple[str, str]:
    """Return (account_sid, auth_token). Prefer platform-level creds."""
    if settings.twilio_account_sid and settings.twilio_auth_token:
        return settings.twilio_account_sid, settings.twilio_auth_token
    if org_settings and org_settings.twilio_account_sid and org_settings.twilio_auth_token:
        return org_settings.twilio_account_sid, org_settings.twilio_auth_token
    raise TwilioError(
        status=500, code=None,
        message="Twilio credentials not configured (set TWILIO_ACCOUNT_SID + TWILIO_AUTH_TOKEN).",
    )


def _auth_header(sid: str, token: str) -> str:
    creds = base64.b64encode(f"{sid}:{token}".encode()).decode()
    return f"Basic {creds}"


def _request(
    method: str,
    path: str,
    *,
    org_settings: OrgSettings | None = None,
    params: dict[str, Any] | None = None,
    form: dict[str, Any] | None = None,
    timeout: int = 15,
) -> dict:
    sid, token = _credentials(org_settings)
    url = f"{TWILIO_API_BASE}/Accounts/{sid}{path}"
    if params:
        url += "?" + urlencode({k: v for k, v in params.items() if v is not None})

    data_bytes = None
    headers = {"Authorization": _auth_header(sid, token), "Accept": "application/json"}
    if form is not None:
        data_bytes = urlencode({k: v for k, v in form.items() if v is not None}).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    req = Request(url, data=data_bytes, method=method, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            err = json.loads(body)
        except json.JSONDecodeError:
            err = {}
        raise TwilioError(
            status=e.code,
            code=err.get("code"),
            message=err.get("message") or body or "Twilio API error",
            more_info=err.get("more_info"),
        ) from e


def search_available_numbers(
    area_code: str,
    *,
    country: str = "US",
    sms_required: bool = True,
    voice_required: bool = True,
    limit: int = 5,
    org_settings: OrgSettings | None = None,
) -> list[dict]:
    """Return up to `limit` available local numbers in `area_code`.

    Each dict has: phone_number (E.164), friendly_name, locality, region.
    """
    params = {
        "AreaCode": area_code,
        "SmsEnabled": "true" if sms_required else None,
        "VoiceEnabled": "true" if voice_required else None,
        "PageSize": str(limit),
    }
    data = _request(
        "GET",
        f"/AvailablePhoneNumbers/{country}/Local.json",
        org_settings=org_settings,
        params=params,
    )
    results = []
    for item in data.get("available_phone_numbers", [])[:limit]:
        results.append({
            "phone_number": item.get("phone_number"),
            "friendly_name": item.get("friendly_name"),
            "locality": item.get("locality"),
            "region": item.get("region"),
            "iso_country": item.get("iso_country"),
        })
    return results


def provision_number(
    phone_number: str,
    *,
    voice_url: str,
    status_callback_url: str,
    friendly_name: str | None = None,
    org_settings: OrgSettings | None = None,
) -> dict:
    """Buy the given E.164 number on Twilio and configure its webhooks.

    Returns the provisioned IncomingPhoneNumber resource dict (contains
    `sid`, `phone_number`, etc.).
    """
    form = {
        "PhoneNumber": phone_number,
        "VoiceUrl": voice_url,
        "VoiceMethod": "POST",
        "StatusCallback": status_callback_url,
        "StatusCallbackMethod": "POST",
        # SMS webhook uses the existing /sms/webhook handler for owner
        # YES/NO approval + caller SMS replies.
        "SmsUrl": voice_url.replace("/twilio/voice/incoming", "/sms/webhook"),
        "SmsMethod": "POST",
        "FriendlyName": friendly_name or f"LeadRelay {phone_number}",
    }
    return _request(
        "POST", "/IncomingPhoneNumbers.json",
        org_settings=org_settings,
        form=form,
    )


def release_number(sid: str, *, org_settings: OrgSettings | None = None) -> None:
    """Release a previously-provisioned number (frees the Twilio charge)."""
    _request("DELETE", f"/IncomingPhoneNumbers/{sid}.json", org_settings=org_settings)
