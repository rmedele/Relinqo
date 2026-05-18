"""Voicemail -> Lead processing pipeline.

Runs Claude extraction on a freshly-transcribed voicemail, creates a Lead,
fires owner SMS alert + optional caller confirmation. Deduped against
SMS-intake leads for the same call (first channel wins; second appends).
"""
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.billing import org_has_billing_access
from app.database import SessionLocal
from app.models import CallEvent, Lead, OrgSettings, PhoneRoutingRule, Voicemail
from app.phone_leads import (
    business_context,
    record_and_send_sms,
    recent_sms_exists,
    send_owner_alert_sms,
    successful_lead_sms_exists,
    synthetic_sender_email,
)

logger = logging.getLogger(__name__)


VOICEMAIL_EXTRACTION_PROMPT = """\
You are extracting structured lead data from a voicemail left for a local
home service business (plumbing, HVAC, roofing, electrical, or similar).

{business_context}

## Input

You will receive the caller's phone number and a voicemail transcript
produced by Twilio's speech-to-text. The transcript may be noisy, missing
words, or garbled — especially for numbers and names.

Caller phone number (from Twilio): {from_number}
Transcript: {transcript}

## Output

Return ONLY a JSON object with these fields, no markdown, no commentary:

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

- is_spam: true if the transcript is a telemarketer, robocall, solicitation,
  or completely unintelligible. When true, set category="spam" and
  urgency_score=1.
- customer_name: only extract if the caller clearly states their name.
  Do not guess.
- callback_number: extract ONLY if you see 10 or 11 contiguous digits in
  the transcript representing a phone number. If none, return null (the
  caller's Twilio-provided number will be used as the default).
- service_address: extract only if explicitly stated. Do not infer.
- issue_summary: one sentence, the customer's actual words paraphrased.
  If transcript is too short or unintelligible, use "Voicemail received;
  transcript unclear."
- category: match the content. Emergencies (leaks, no heat, flooding,
  gas) -> urgent_request. Price/estimate -> quote_request. Existing
  customer references -> existing_customer. Vague -> general_inquiry.
- urgency_score: 5 for active emergencies, 4 for quote requests with a
  deadline, 3 for standard quote requests, 2 for general questions, 1
  for spam or unclear.
- owner_alert_needed: true for urgency_score >= 3 or category==urgent_request.
- confidence: your confidence in this classification (not the transcript
  quality).

Return ONLY the JSON object.
"""


def _extract_with_claude(
    transcript: str, from_number: str, org_settings: OrgSettings | None,
) -> dict[str, Any] | None:
    from app.ai import _get_client, ai_available

    if not ai_available():
        return None
    try:
        prompt = VOICEMAIL_EXTRACTION_PROMPT.format(
            business_context=business_context(org_settings),
            from_number=from_number,
            transcript=transcript,
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
        logger.exception("Claude voicemail extraction failed")
        return None


def _heuristic_extract(transcript: str | None, from_number: str) -> dict[str, Any]:
    if not transcript or len(transcript.strip()) < 10:
        return {
            "is_spam": False,
            "customer_name": None,
            "callback_number": None,
            "service_address": None,
            "issue_summary": "Voicemail received; transcript unclear.",
            "category": "general_inquiry",
            "urgency_score": 2,
            "owner_alert_needed": True,
            "confidence": 0.3,
        }
    digits = re.findall(r"\d", transcript)
    callback = "".join(digits[:10]) if len(digits) >= 10 else None
    return {
        "is_spam": False,
        "customer_name": None,
        "callback_number": callback,
        "service_address": None,
        "issue_summary": transcript[:200],
        "category": "general_inquiry",
        "urgency_score": 2,
        "owner_alert_needed": True,
        "confidence": 0.5,
    }


async def process_voicemail(voicemail_id: int) -> None:
    """Classify a voicemail, create a Lead (unless one already exists for
    this call from an SMS reply), send owner SMS + caller confirmation.

    Idempotent via `voicemails.classified_at`.
    """
    db = SessionLocal()
    try:
        vm = db.query(Voicemail).filter(Voicemail.id == voicemail_id).first()
        if not vm or vm.classified_at is not None:
            return

        call = db.query(CallEvent).filter(CallEvent.id == vm.call_event_id).first()
        if not call:
            logger.error("process_voicemail: missing call_event for vm=%s", voicemail_id)
            return

        org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == call.org_id).first()
        automation_allowed = not (
            org_settings
            and (
                org_settings.automation_paused
                or (org_settings.org and not org_has_billing_access(org_settings.org))
            )
        )
        routing_rule = db.query(PhoneRoutingRule).filter(
            PhoneRoutingRule.org_id == call.org_id,
        ).first()

        transcript = vm.transcript or ""
        extraction = _extract_with_claude(transcript, call.from_number, org_settings)
        if extraction is None:
            extraction = _heuristic_extract(transcript, call.from_number)

        if extraction.get("is_spam"):
            vm.classified_at = datetime.now(timezone.utc)
            db.commit()
            logger.info("vm_id=%s spam, skipping Lead", voicemail_id)
            return

        # Dedup: if an SMS reply for the same call already created a Lead,
        # append the voicemail transcript to it rather than making a duplicate.
        existing_lead = db.query(Lead).filter(Lead.call_event_id == call.id).first()
        if existing_lead:
            appended = (existing_lead.body or "") + "\n\n[Voicemail transcript]\n" + (transcript or "(unavailable)")
            existing_lead.body = appended
            vm.classified_at = datetime.now(timezone.utc)
            db.commit()
            logger.info(
                "vm_id=%s: appended transcript to existing lead_id=%s (SMS got there first)",
                voicemail_id, existing_lead.id,
            )
            if (
                bool(extraction.get("owner_alert_needed", existing_lead.owner_alert_needed))
                and not successful_lead_sms_exists(
                    db,
                    org_id=call.org_id,
                    lead_id=existing_lead.id,
                    purpose="owner_alert",
                )
            ):
                send_owner_alert_sms(
                    db,
                    org_id=call.org_id,
                    lead=existing_lead,
                    call=call,
                    org_settings=org_settings,
                    routing_rule=routing_rule,
                    channel_label="voicemail",
                    automation_allowed=automation_allowed,
                )
            return

        callback = extraction.get("callback_number") or call.from_number
        lead = Lead(
            org_id=call.org_id,
            source="phone",
            call_event_id=call.id,
            sender_name=extraction.get("customer_name"),
            sender_email=synthetic_sender_email(call.from_number),
            subject=f"Voicemail from {callback}",
            body=transcript or "(transcript unavailable)",
            phone=callback,
            location=extraction.get("service_address"),
            category=extraction.get("category", "general_inquiry"),
            urgency_score=int(extraction.get("urgency_score", 2)),
            summary=extraction.get("issue_summary"),
            owner_alert_needed=bool(extraction.get("owner_alert_needed", True)),
            status="new",
            confidence=float(extraction.get("confidence", 0.5)),
            next_step="review_voicemail",
            raw_payload=json.dumps({
                "channel": "voicemail",
                "from_number": call.from_number,
                "to_number": call.to_number,
                "recording_url": vm.recording_url,
                "recording_duration": vm.recording_duration,
            }),
        )
        db.add(lead)
        vm.classified_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(lead)
        logger.info(
            "vm_id=%s -> lead_id=%s category=%s urgency=%s",
            voicemail_id, lead.id, lead.category, lead.urgency_score,
        )

        owner_alert = send_owner_alert_sms(
            db,
            org_id=call.org_id,
            lead=lead,
            call=call,
            org_settings=org_settings,
            routing_rule=routing_rule,
            channel_label="voicemail",
            automation_allowed=automation_allowed,
        )
        owner_number = owner_alert.to_number if owner_alert else ""

        # Caller confirmation SMS — only if we did NOT already outreach them
        # via the SMS-intake flow (outreach carries the same "we got your
        # request" message).
        send_confirmation = automation_allowed and (routing_rule.send_caller_confirmation if routing_rule else True)
        if send_confirmation and call.from_number and call.from_number != owner_number:
            already_reached = (
                recent_sms_exists(db, call.org_id, call.from_number, "caller_confirmation")
                or recent_sms_exists(db, call.org_id, call.from_number, "outreach")
            )
            if not already_reached:
                biz_name = (org_settings.business_name if org_settings else "") or "the team"
                record_and_send_sms(
                    db,
                    org_id=call.org_id,
                    to_number=call.from_number,
                    body=f"Hi from {biz_name}. We got your voicemail and will reach out shortly.",
                    purpose="caller_confirmation",
                    lead_id=lead.id,
                    call_event_id=call.id,
                    org_settings=org_settings,
                    from_number=call.to_number,
                )
    except Exception:
        logger.exception("process_voicemail failed vm_id=%s", voicemail_id)
        db.rollback()
    finally:
        db.close()
