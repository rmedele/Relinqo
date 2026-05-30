"""Outbound webhooks for no-code handoff tools like Zapier and Make."""
import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.models import Booking, Lead, OrgSettings

logger = logging.getLogger(__name__)

DEFAULT_EVENTS = {"lead.created", "booking.created", "lead.won"}


def _enabled_events(org_settings: OrgSettings | None) -> set[str]:
    raw = (org_settings.outbound_webhook_events if org_settings else "") or ""
    events = {item.strip() for item in raw.split(",") if item.strip()}
    return events or DEFAULT_EVENTS


def webhook_configured(org_settings: OrgSettings | None, event_type: str) -> bool:
    if not org_settings or not org_settings.outbound_webhook_enabled or not org_settings.outbound_webhook_url:
        return False
    events = _enabled_events(org_settings)
    return "*" in events or event_type in events


def lead_webhook_payload(lead: Lead) -> dict:
    return {
        "id": lead.id,
        "source": lead.source,
        "sender_name": lead.sender_name,
        "sender_email": lead.sender_email,
        "subject": lead.subject,
        "phone": lead.phone,
        "location": lead.location,
        "category": lead.category,
        "urgency_score": lead.urgency_score,
        "status": lead.status,
        "outcome": lead.outcome,
        "deal_value": lead.deal_value,
        "pipeline_stage": lead.pipeline_stage,
        "summary": lead.summary,
        "recommended_reply": lead.recommended_reply,
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
        "updated_at": lead.updated_at.isoformat() if lead.updated_at else None,
    }


def booking_webhook_payload(booking: Booking, lead: Lead | None = None) -> dict:
    payload = {
        "id": booking.id,
        "lead_id": booking.lead_id,
        "customer_name": booking.customer_name,
        "customer_email": booking.customer_email,
        "customer_phone": booking.customer_phone,
        "slot_start": booking.slot_start.isoformat() if booking.slot_start else None,
        "slot_end": booking.slot_end.isoformat() if booking.slot_end else None,
        "status": booking.status,
        "notes": booking.customer_notes,
        "customer_notes": booking.customer_notes,
        "created_at": booking.created_at.isoformat() if booking.created_at else None,
    }
    if lead:
        payload["lead"] = lead_webhook_payload(lead)
    return payload


def send_webhook_event(
    org_settings: OrgSettings | None,
    event_type: str,
    data: dict,
    *,
    force: bool = False,
) -> tuple[bool, str]:
    if not force and not webhook_configured(org_settings, event_type):
        return False, "Outbound webhook not configured for this event"
    if not org_settings or not org_settings.outbound_webhook_url:
        return False, "Outbound webhook URL is missing"

    delivery_id = secrets.token_urlsafe(12)
    envelope = {
        "event": event_type,
        "delivery_id": delivery_id,
        "org_id": org_settings.org_id,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }
    body = json.dumps(envelope, separators=(",", ":"), default=str).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "relinqo-webhooks/1.0",
        "X-Relinqo-Event": event_type,
        "X-Relinqo-Delivery": delivery_id,
    }
    if org_settings.outbound_webhook_secret:
        signature = hmac.new(
            org_settings.outbound_webhook_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
        headers["X-Relinqo-Signature"] = f"sha256={signature}"

    req = Request(org_settings.outbound_webhook_url, data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=8) as resp:
            if 200 <= resp.status < 300:
                logger.info("outbound_webhook sent event=%s delivery=%s", event_type, delivery_id)
                return True, f"sent ({resp.status})"
            return False, f"webhook returned HTTP {resp.status}"
    except HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")[:400]
        logger.warning(
            "outbound_webhook failed event=%s status=%s body=%s",
            event_type,
            exc.code,
            response_body,
        )
        return False, f"HTTP {exc.code}: {response_body}"
    except Exception as exc:
        logger.exception("outbound_webhook failed event=%s", event_type)
        return False, str(exc)


def dispatch_lead_created(org_settings: OrgSettings | None, lead: Lead) -> None:
    send_webhook_event(org_settings, "lead.created", lead_webhook_payload(lead))


def dispatch_lead_won(org_settings: OrgSettings | None, lead: Lead) -> None:
    send_webhook_event(org_settings, "lead.won", lead_webhook_payload(lead))


def dispatch_booking_created(org_settings: OrgSettings | None, booking: Booking, lead: Lead | None = None) -> None:
    send_webhook_event(org_settings, "booking.created", booking_webhook_payload(booking, lead))
