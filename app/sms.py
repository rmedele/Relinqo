import base64
import json
import logging
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.config import settings
from app.models import OrgSettings

logger = logging.getLogger(__name__)


def sms_configured(org_settings: OrgSettings | None = None) -> bool:
    if org_settings:
        return bool(
            org_settings.twilio_account_sid
            and org_settings.twilio_auth_token
            and org_settings.twilio_from_number
            and org_settings.sms_alert_to_number
        )
    return bool(
        settings.twilio_account_sid
        and settings.twilio_auth_token
        and settings.twilio_from_number
        and settings.sms_alert_to_number
    )


def _twilio_credentials(org_settings: OrgSettings | None) -> tuple[str, str, str] | None:
    """Return (account_sid, auth_token, from_number) or None if unconfigured.
    Unlike sms_configured(), this does NOT require a configured to_number
    (callers can pass their own destination)."""
    if org_settings and org_settings.twilio_account_sid and org_settings.twilio_auth_token and org_settings.twilio_from_number:
        return (
            org_settings.twilio_account_sid,
            org_settings.twilio_auth_token,
            org_settings.twilio_from_number,
        )
    if settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_from_number:
        return (settings.twilio_account_sid, settings.twilio_auth_token, settings.twilio_from_number)
    return None


def send_sms(body: str, org_settings: OrgSettings | None = None) -> tuple[bool, str]:
    """Legacy helper — sends to the org's configured sms_alert_to_number.
    Preserved for existing owner-alert/approval flows."""
    if not sms_configured(org_settings):
        return False, "Twilio SMS not configured"
    to_number = org_settings.sms_alert_to_number if org_settings else settings.sms_alert_to_number
    ok, msg, _sid = send_sms_to(body, to_number, org_settings)
    return ok, msg


def send_sms_to(
    body: str,
    to_number: str,
    org_settings: OrgSettings | None = None,
) -> tuple[bool, str, str | None]:
    """Send an SMS to an arbitrary destination. Returns (ok, message, twilio_message_sid).
    The caller is responsible for recording the send in sms_notifications."""
    if org_settings and org_settings.org and org_settings.org.subscription_status not in {"active", "trialing"}:
        return False, "Workspace is not active", None
    creds = _twilio_credentials(org_settings)
    if creds is None:
        return False, "Twilio SMS not configured", None
    if not to_number:
        return False, "Missing to_number", None

    sid, token, from_number = creds
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    data = urlencode({
        "To": to_number,
        "From": from_number,
        "Body": body[:1600],
    }).encode()

    credentials = base64.b64encode(f"{sid}:{token}".encode()).decode()

    req = Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Basic {credentials}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urlopen(req, timeout=15) as resp:
            raw = resp.read()
            if resp.status in (200, 201):
                try:
                    payload = json.loads(raw.decode("utf-8"))
                    message_sid = payload.get("sid")
                except Exception:
                    message_sid = None
                logger.info("SMS sent to %s (sid=%s)", to_number, message_sid)
                return True, "sent", message_sid
            return False, f"Twilio returned status {resp.status}", None
    except HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        logger.error(
            "SMS send failed: HTTP %s from=%s to=%s body=%s",
            exc.code, from_number, to_number, body,
        )
        return False, f"HTTP {exc.code}: {body}", None
    except Exception as exc:
        logger.exception("SMS send failed")
        return False, str(exc), None
