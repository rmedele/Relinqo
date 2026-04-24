"""Voicemail -> Lead processing pipeline.

Takes a freshly-transcribed voicemail, runs Claude extraction to pull
structured fields, creates a Lead row, sends owner alert SMS, and
optionally sends a confirmation SMS back to the caller.

The pipeline is called from the transcription-complete webhook via
asyncio.create_task for fire-and-forget background processing. A sweep
job (future) will re-enqueue any Voicemails stuck in 'completed
transcription, no classification' state.
"""
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import settings
from app.database import SessionLocal
from app.models import CallEvent, Lead, OrgSettings, PhoneRoutingRule, SmsNotification, Voicemail
from app.sms import send_sms_to

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


def _business_context(org_settings: OrgSettings | None) -> str:
    if not org_settings:
        return "No business profile configured."
    parts = []
    if org_settings.business_name:
        parts.append(f"Business: {org_settings.business_name}")
    if org_settings.business_services:
        parts.append(f"Services offered: {org_settings.business_services}")
    if org_settings.business_area:
        parts.append(f"Service area: {org_settings.business_area}")
    return "\n".join(parts) if parts else "No business profile configured."


def _extract_with_claude(
    transcript: str, from_number: str, org_settings: OrgSettings | None,
) -> dict[str, Any] | None:
    """Call Claude to extract structured fields. Returns None on failure."""
    from app.ai import _get_client, ai_available

    if not ai_available():
        return None

    try:
        prompt = VOICEMAIL_EXTRACTION_PROMPT.format(
            business_context=_business_context(org_settings),
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
    """Fallback when Claude is unavailable. No NLP, just punts to manual review."""
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

    # Crude phone-number grab: 10+ contiguous digits (possibly separated)
    digits = re.findall(r"\d", transcript)
    callback = None
    if len(digits) >= 10:
        callback = "".join(digits[:10])

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


def _synthetic_sender_email(from_number: str) -> str:
    """The Lead.sender_email column is NOT NULL. Phone leads don't have
    one, so synthesize a routing-only address that clearly indicates the
    lead originated from a voicemail, not a real inbox.
    """
    digits = re.sub(r"\D", "", from_number) or "unknown"
    return f"caller-{digits}@phone.leadrelay.local"


_URGENCY_ICON = {1: "", 2: "", 3: "!", 4: "!!", 5: "!!!"}


def _format_owner_summary(
    lead: Lead,
    call: CallEvent,
    vm: Voicemail,
    org_settings: OrgSettings | None,
) -> str:
    hdr = "[AFTER HOURS] " if call.is_after_hours else ""
    icon = _URGENCY_ICON.get(lead.urgency_score, "")
    callback = lead.phone or call.from_number
    name = lead.sender_name or "Unknown caller"
    summary = (lead.summary or "No transcript")
    if len(summary) > 140:
        summary = summary[:137] + "..."
    base_url = (settings.public_base_url or "").rstrip("/")
    link = f"{base_url}/review#lead-{lead.id}" if base_url else f"lead #{lead.id}"
    prefix = f"{hdr}{icon + ' ' if icon else ''}".strip()
    header_line = f"{prefix + ' ' if prefix else ''}New {lead.category} lead"
    return (
        f"{header_line}\n"
        f"{name} · {callback}\n"
        f"{summary}\n"
        f"{link}"
    )


def _recent_confirmation_exists(
    db, org_id: int, to_number: str, within_minutes: int = 10,
) -> bool:
    """Suppress duplicate confirmation SMS if one has been sent recently
    to the same number for the same org (handles redial storms)."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=within_minutes)
    return db.query(SmsNotification).filter(
        SmsNotification.org_id == org_id,
        SmsNotification.to_number == to_number,
        SmsNotification.purpose == "caller_confirmation",
        SmsNotification.created_at >= cutoff,
    ).first() is not None


def _record_and_send_sms(
    db,
    *,
    org_id: int,
    to_number: str,
    body: str,
    purpose: str,
    lead_id: int | None,
    call_event_id: int | None,
    org_settings: OrgSettings | None,
) -> None:
    """Send SMS via Twilio and persist the attempt to sms_notifications."""
    notification = SmsNotification(
        org_id=org_id,
        lead_id=lead_id,
        call_event_id=call_event_id,
        direction="outbound",
        to_number=to_number,
        body=body,
        purpose=purpose,
        status="queued",
    )
    db.add(notification)
    db.flush()

    ok, msg, twilio_sid = send_sms_to(body, to_number, org_settings)
    notification.twilio_message_sid = twilio_sid
    notification.status = "sent" if ok else "failed"
    if not ok:
        notification.error_message = msg
        logger.warning("SMS %s to %s failed: %s", purpose, to_number, msg)
    db.commit()


async def process_voicemail(voicemail_id: int) -> None:
    """Background job: classify a voicemail, create a Lead, send SMS
    alerts.

    Idempotent via the `classified_at` timestamp on Voicemail. If called
    twice concurrently the second invocation will no-op because the first
    will have set classified_at before the second starts Claude work.
    """
    db = SessionLocal()
    try:
        vm = db.query(Voicemail).filter(Voicemail.id == voicemail_id).first()
        if not vm:
            logger.warning("process_voicemail: voicemail_id=%s not found", voicemail_id)
            return
        if vm.classified_at is not None:
            logger.info("process_voicemail: voicemail_id=%s already classified", voicemail_id)
            return

        call = db.query(CallEvent).filter(CallEvent.id == vm.call_event_id).first()
        if not call:
            logger.error("process_voicemail: call_event_id=%s missing for vm=%s",
                         vm.call_event_id, voicemail_id)
            return

        org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == call.org_id).first()
        routing_rule = db.query(PhoneRoutingRule).filter(
            PhoneRoutingRule.org_id == call.org_id,
        ).first()

        # 1. Extract
        transcript = vm.transcript or ""
        extraction = _extract_with_claude(transcript, call.from_number, org_settings)
        if extraction is None:
            extraction = _heuristic_extract(transcript, call.from_number)

        # 2. Short-circuit spam without creating a Lead row
        if extraction.get("is_spam"):
            vm.classified_at = datetime.now(timezone.utc)
            db.commit()
            logger.info("voicemail_id=%s classified as spam, skipping Lead creation", voicemail_id)
            return

        # 3. Create Lead
        callback = extraction.get("callback_number") or call.from_number
        lead = Lead(
            org_id=call.org_id,
            source="phone",
            call_event_id=call.id,
            sender_name=extraction.get("customer_name"),
            sender_email=_synthetic_sender_email(call.from_number),
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
                "from_number": call.from_number,
                "to_number": call.to_number,
                "recording_url": vm.recording_url,
                "recording_duration": vm.recording_duration,
                "transcription_status": vm.transcription_status,
            }),
        )
        db.add(lead)
        vm.classified_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(lead)

        logger.info(
            "voicemail_id=%s -> lead_id=%s category=%s urgency=%s",
            voicemail_id, lead.id, lead.category, lead.urgency_score,
        )

        # 4. Owner SMS alert
        owner_number = (routing_rule.owner_phone if routing_rule else "") or (
            org_settings.sms_alert_to_number if org_settings else ""
        )
        if owner_number:
            _record_and_send_sms(
                db,
                org_id=call.org_id,
                to_number=owner_number,
                body=_format_owner_summary(lead, call, vm, org_settings),
                purpose="owner_alert",
                lead_id=lead.id,
                call_event_id=call.id,
                org_settings=org_settings,
            )
        else:
            logger.info("voicemail_id=%s: no owner number configured, skipping owner SMS", voicemail_id)

        # 5. Caller confirmation SMS (guarded)
        send_confirmation = routing_rule.send_caller_confirmation if routing_rule else True
        if send_confirmation and call.from_number and call.from_number != owner_number:
            if _recent_confirmation_exists(db, call.org_id, call.from_number):
                logger.info(
                    "voicemail_id=%s: skipped caller confirmation — recent send exists to %s",
                    voicemail_id, call.from_number,
                )
            else:
                business_name = (org_settings.business_name if org_settings else "") or "the team"
                body = (
                    f"Hi from {business_name}. We got your voicemail and will "
                    f"reach out shortly."
                )
                _record_and_send_sms(
                    db,
                    org_id=call.org_id,
                    to_number=call.from_number,
                    body=body,
                    purpose="caller_confirmation",
                    lead_id=lead.id,
                    call_event_id=call.id,
                    org_settings=org_settings,
                )
    except Exception:
        logger.exception("process_voicemail failed for voicemail_id=%s", voicemail_id)
        db.rollback()
    finally:
        db.close()
