"""SMS-intake lead pipeline.

After-hours / missed-call flow sends the caller a text saying "tell us
what you need." When the caller replies, that inbound SMS becomes a
Lead — no voicemail required. This is the primary path for modern
callers who won't leave voicemails.

Two entrypoints:
  - `send_outreach_sms(call_event_id)` — fires the outreach SMS after
    /incoming or /dial-status decides not to ring the owner (or the
    owner didn't pick up).
  - `process_sms_lead(call_event_id, body, media_urls)` — creates the
    Lead when the caller replies via SMS.
"""
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import CallEvent, Lead, OrgSettings, PhoneRoutingRule
from app.phone_leads import (
    business_context,
    format_owner_summary,
    record_and_send_sms,
    recent_sms_exists,
    synthetic_sender_email,
)

logger = logging.getLogger(__name__)


RECENT_CALL_WINDOW_MINUTES = 30


SMS_EXTRACTION_PROMPT = """\
You are extracting structured lead data from an SMS conversation with a
customer who just called a local home service business (plumbing, HVAC,
roofing, electrical, or similar) and was invited to text back.

{business_context}

## Input

Caller phone number (from Twilio): {from_number}
Was this after business hours? {after_hours}
Customer's SMS message: {body}
Media attachments: {num_media}

## Output

Return ONLY a JSON object with these fields:

{{
  "is_spam": boolean,
  "customer_name": string or null,
  "callback_number": string or null,
  "service_address": string or null,
  "issue_summary": string,
  "category": "urgent_request" | "quote_request" | "general_inquiry" | "existing_customer" | "spam",
  "urgency_score": integer 1-5,
  "owner_alert_needed": boolean,
  "confidence": float 0.0-1.0
}}

## Rules

- is_spam: true if the message is a telemarketer, solicitation, or
  obviously unrelated. The caller's phone number IS a real number
  (Twilio verified), so very short replies like "yes" are still real
  people — just low-confidence leads.
- customer_name: only if the customer signs their name or gives it.
- callback_number: extract ONLY if customer provides one in the
  text. Else null (we default to their Twilio-provided From number).
- service_address: only if explicitly stated.
- issue_summary: one sentence, what the customer wants. If the SMS
  is too short (e.g. "yes", "call me"), use "Customer replied to
  our outreach; needs a callback to capture details."
- urgency_score: 5 for active emergencies, 4 for quote with deadline,
  3 for quote/scheduling, 2 for general, 1 for spam/ambiguous.
- owner_alert_needed: true for urgency_score >= 3.

Return ONLY the JSON object.
"""


def _extract_with_claude(
    body: str,
    from_number: str,
    after_hours: bool,
    num_media: int,
    org_settings: OrgSettings | None,
) -> dict[str, Any] | None:
    from app.ai import _get_client, ai_available

    if not ai_available():
        return None
    try:
        prompt = SMS_EXTRACTION_PROMPT.format(
            business_context=business_context(org_settings),
            from_number=from_number,
            after_hours="yes" if after_hours else "no",
            body=body or "(empty)",
            num_media=num_media,
        )
        client = _get_client()
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3].strip()
        return json.loads(raw)
    except Exception:
        logger.exception("Claude SMS extraction failed")
        return None


def _heuristic_extract(body: str, from_number: str) -> dict[str, Any]:
    if not body or len(body.strip()) < 2:
        return {
            "is_spam": False,
            "customer_name": None,
            "callback_number": None,
            "service_address": None,
            "issue_summary": "Customer replied to our outreach with an empty or very short message.",
            "category": "general_inquiry",
            "urgency_score": 2,
            "owner_alert_needed": True,
            "confidence": 0.4,
        }
    digits = re.findall(r"\d", body)
    callback = "".join(digits[:10]) if len(digits) >= 10 else None
    return {
        "is_spam": False,
        "customer_name": None,
        "callback_number": callback,
        "service_address": None,
        "issue_summary": body[:200],
        "category": "general_inquiry",
        "urgency_score": 3,
        "owner_alert_needed": True,
        "confidence": 0.5,
    }


def send_outreach_sms(call_event_id: int) -> None:
    """Fire the "we'll text you" SMS to the caller. Idempotent via
    recent_sms_exists — won't double-send on retry.

    Synchronous function; called from twilio_voice.py via
    asyncio.create_task so it doesn't block the TwiML response.
    """
    db = SessionLocal()
    try:
        call = db.query(CallEvent).filter(CallEvent.id == call_event_id).first()
        if not call:
            logger.warning("send_outreach_sms: call_event_id=%s missing", call_event_id)
            return
        if not call.from_number:
            return

        if recent_sms_exists(db, call.org_id, call.from_number, "outreach", within_minutes=30):
            logger.info("outreach suppressed — recent outreach to %s exists", call.from_number)
            return

        org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == call.org_id).first()
        if org_settings and (
            org_settings.automation_paused
            or (org_settings.org and org_settings.org.subscription_status not in {"active", "trialing"})
        ):
            logger.info("send_outreach_sms skipped for org_id=%s", call.org_id)
            return
        biz_name = (org_settings.business_name if org_settings else "") or "the team"

        if call.is_after_hours:
            body = (
                f"Hi from {biz_name}! You just called us. We're closed right "
                f"now but reply here with what you need and we'll get back to "
                f"you ASAP. Photos are welcome."
            )
        else:
            body = (
                f"Hi from {biz_name}! We missed your call. Reply here with "
                f"what you need and we'll get back to you shortly. Photos "
                f"are welcome."
            )

        record_and_send_sms(
            db,
            org_id=call.org_id,
            to_number=call.from_number,
            body=body,
            purpose="outreach",
            lead_id=None,
            call_event_id=call.id,
            org_settings=org_settings,
        )
    except Exception:
        logger.exception("send_outreach_sms failed call_event_id=%s", call_event_id)
    finally:
        db.close()


def find_recent_call_for_sender(db: Session, from_number: str) -> CallEvent | None:
    """Return the most recent CallEvent from this number within the
    outreach-reply window, where we sent an outreach SMS. Used by the
    SMS webhook to decide whether to treat an inbound SMS as a lead
    reply or as an owner-approval command."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=RECENT_CALL_WINDOW_MINUTES)
    return db.query(CallEvent).filter(
        CallEvent.from_number == from_number,
        CallEvent.created_at >= cutoff,
    ).order_by(CallEvent.created_at.desc()).first()


def process_sms_lead(
    call_event_id: int,
    body: str,
    media_urls: list[str] | None = None,
) -> int | None:
    """Classify an inbound SMS reply, create a Lead, alert the owner.

    Dedups against voicemail-created leads on the same call — if one
    exists, appends the SMS content instead of creating a duplicate.

    Returns the Lead id if one was created, else None. Runs synchronously
    inside the /sms/webhook request; Claude call is fast (~1s).
    """
    db = SessionLocal()
    try:
        call = db.query(CallEvent).filter(CallEvent.id == call_event_id).first()
        if not call:
            logger.error("process_sms_lead: missing call_event_id=%s", call_event_id)
            return None

        org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == call.org_id).first()
        automation_allowed = not (
            org_settings
            and (
                org_settings.automation_paused
                or (org_settings.org and org_settings.org.subscription_status not in {"active", "trialing"})
            )
        )
        routing_rule = db.query(PhoneRoutingRule).filter(
            PhoneRoutingRule.org_id == call.org_id,
        ).first()

        num_media = len(media_urls or [])
        extraction = _extract_with_claude(
            body, call.from_number, call.is_after_hours, num_media, org_settings,
        )
        if extraction is None:
            extraction = _heuristic_extract(body, call.from_number)

        if extraction.get("is_spam"):
            logger.info("sms_lead: spam reply from %s, skipping", call.from_number)
            return None

        # Dedup with voicemail pipeline.
        existing_lead = db.query(Lead).filter(Lead.call_event_id == call.id).first()
        if existing_lead:
            appended = (existing_lead.body or "") + "\n\n[Customer SMS reply]\n" + (body or "")
            existing_lead.body = appended
            # If the existing lead was a voicemail and SMS reply has a
            # clearer summary, upgrade urgency when higher.
            new_urgency = int(extraction.get("urgency_score", 0))
            if new_urgency > (existing_lead.urgency_score or 0):
                existing_lead.urgency_score = new_urgency
            db.commit()
            logger.info(
                "sms_lead: appended to existing lead_id=%s (voicemail got there first)",
                existing_lead.id,
            )
            return existing_lead.id

        callback = extraction.get("callback_number") or call.from_number
        lead = Lead(
            org_id=call.org_id,
            source="phone",
            call_event_id=call.id,
            sender_name=extraction.get("customer_name"),
            sender_email=synthetic_sender_email(call.from_number),
            subject=f"SMS reply from {callback}",
            body=body or "(empty SMS)",
            phone=callback,
            location=extraction.get("service_address"),
            category=extraction.get("category", "general_inquiry"),
            urgency_score=int(extraction.get("urgency_score", 3)),
            summary=extraction.get("issue_summary"),
            owner_alert_needed=bool(extraction.get("owner_alert_needed", True)),
            status="new",
            confidence=float(extraction.get("confidence", 0.5)),
            next_step="review_sms_lead",
            raw_payload=json.dumps({
                "channel": "sms",
                "from_number": call.from_number,
                "to_number": call.to_number,
                "num_media": num_media,
                "media_urls": media_urls or [],
                "after_hours": call.is_after_hours,
            }),
        )
        db.add(lead)
        db.commit()
        db.refresh(lead)
        logger.info(
            "sms_lead: call_event_id=%s -> lead_id=%s category=%s urgency=%s",
            call_event_id, lead.id, lead.category, lead.urgency_score,
        )

        owner_number = (routing_rule.owner_phone if routing_rule else "") or (
            org_settings.sms_alert_to_number if org_settings else ""
        )
        if automation_allowed and owner_number:
            record_and_send_sms(
                db,
                org_id=call.org_id,
                to_number=owner_number,
                body=format_owner_summary(lead, call, org_settings, channel_label="SMS"),
                purpose="owner_alert",
                lead_id=lead.id,
                call_event_id=call.id,
                org_settings=org_settings,
            )
        return lead.id
    except Exception:
        logger.exception("process_sms_lead failed call_event_id=%s", call_event_id)
        db.rollback()
        return None
    finally:
        db.close()
