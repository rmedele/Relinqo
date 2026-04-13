import base64
import logging
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


def send_sms(body: str, org_settings: OrgSettings | None = None) -> tuple[bool, str]:
    if not sms_configured(org_settings):
        return False, "Twilio SMS not configured"

    sid = org_settings.twilio_account_sid if org_settings else settings.twilio_account_sid
    token = org_settings.twilio_auth_token if org_settings else settings.twilio_auth_token
    from_number = org_settings.twilio_from_number if org_settings else settings.twilio_from_number
    to_number = org_settings.sms_alert_to_number if org_settings else settings.sms_alert_to_number

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
            if resp.status in (200, 201):
                logger.info("SMS sent to %s", to_number)
                return True, "sent"
            return False, f"Twilio returned status {resp.status}"
    except Exception as exc:
        logger.exception("SMS send failed")
        return False, str(exc)
