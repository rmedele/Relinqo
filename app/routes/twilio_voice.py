"""Twilio Programmable Voice webhooks for LeadRelay phone lead capture.

V1 scope: missed-call rescue, voicemail-to-lead, after-hours intake.
All endpoints are signature-validated in production.
"""
import asyncio
import logging
import xml.sax.saxutils as saxutils
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CallEvent, PhoneNumber, Voicemail
from app.phone_routing import get_routing_rule, is_within_business_hours, should_dial_owner
from app.twilio_signature import verify_twilio_signature
from app.voicemail_processor import process_voicemail

router = APIRouter(prefix="/twilio/voice", tags=["twilio-voice"])
logger = logging.getLogger(__name__)


def _twiml(xml_body: str) -> Response:
    return Response(
        content=f'<?xml version="1.0" encoding="UTF-8"?>{xml_body}',
        media_type="application/xml",
    )


_DEFAULT_MISSED_GREETING = (
    "Sorry we missed you. Please leave your name, phone number, "
    "and a short description of what you need after the beep."
)
_DEFAULT_AFTER_HOURS_GREETING = (
    "Thanks for calling. We are closed right now, but leave your name, "
    "phone number, and a short description of what you need after the "
    "beep and we will get back to you as soon as we can."
)


def _voicemail_twiml(greeting: str) -> str:
    safe_greeting = saxutils.escape(greeting)
    return (
        "<Response>"
        f'<Say voice="Polly.Joanna">{safe_greeting}</Say>'
        '<Record '
        'maxLength="120" '
        'playBeep="true" '
        'trim="trim-silence" '
        'finishOnKey="#" '
        'transcribe="true" '
        'transcribeCallback="/twilio/voice/transcription-complete" '
        'action="/twilio/voice/recording-complete" '
        'method="POST"/>'
        '<Say>We did not catch that. Please call back. Goodbye.</Say>'
        "</Response>"
    )


def _dial_twiml(owner_phone: str, caller_id: str, timeout: int) -> str:
    """Dial the owner first. If no-answer/busy/failed, /dial-status falls
    through to voicemail TwiML."""
    safe_owner = saxutils.escape(owner_phone)
    safe_caller = saxutils.escape(caller_id)
    return (
        "<Response>"
        f'<Dial timeout="{timeout}" '
        f'callerId="{safe_caller}" '
        'action="/twilio/voice/dial-status" '
        'method="POST">'
        f'<Number>{safe_owner}</Number>'
        '</Dial>'
        "</Response>"
    )


def _redact(phone: str) -> str:
    if not phone or len(phone) < 4:
        return "***"
    return f"***{phone[-4:]}"


@router.post("/incoming", dependencies=[Depends(verify_twilio_signature)])
async def incoming_call(request: Request, db: Session = Depends(get_db)) -> Response:
    """Call entrypoint. Branches:
      - In business hours + owner configured -> <Dial> owner, action routes
        to /dial-status which falls through to voicemail on no-answer.
      - After hours OR no owner configured -> voicemail directly.
    """
    form = await request.form()
    call_sid = form.get("CallSid", "")
    from_num = form.get("From", "")
    to_num = form.get("To", "")
    from_city = form.get("FromCity") or None
    from_state = form.get("FromState") or None

    phone = db.query(PhoneNumber).filter(
        PhoneNumber.phone_number == to_num,
        PhoneNumber.is_active == True,  # noqa: E712
    ).first()

    if not phone:
        logger.warning(
            "twilio incoming for unregistered number to=%s from=%s sid=%s",
            to_num, _redact(from_num), call_sid,
        )
        return _twiml("<Response><Hangup/></Response>")

    now_utc = datetime.now(timezone.utc)
    in_hours = is_within_business_hours(db, phone.org_id, now_utc)
    rule = get_routing_rule(db, phone.org_id)
    is_after_hours = not in_hours
    dial_owner = should_dial_owner(rule, in_hours)

    # Idempotent insert — Twilio may retry on timeout.
    stmt = sqlite_insert(CallEvent).values(
        org_id=phone.org_id,
        twilio_call_sid=call_sid,
        from_number=from_num,
        to_number=to_num,
        from_city=from_city,
        from_state=from_state,
        direction="inbound",
        status="ringing",
        is_after_hours=is_after_hours,
    ).on_conflict_do_nothing(index_elements=["twilio_call_sid"])
    db.execute(stmt)
    db.commit()

    logger.info(
        "twilio incoming sid=%s org=%s from=%s to=%s after_hours=%s dial_owner=%s",
        call_sid, phone.org_id, _redact(from_num), to_num, is_after_hours, dial_owner,
    )

    if dial_owner and rule is not None:
        return _twiml(_dial_twiml(
            owner_phone=rule.owner_phone,
            caller_id=to_num,  # show business line on owner's handset
            timeout=rule.ring_timeout_seconds or 20,
        ))

    greeting = _DEFAULT_AFTER_HOURS_GREETING if is_after_hours else _DEFAULT_MISSED_GREETING
    if rule:
        if is_after_hours and rule.after_hours_greeting:
            greeting = rule.after_hours_greeting
        elif not is_after_hours and rule.voicemail_greeting:
            greeting = rule.voicemail_greeting
    return _twiml(_voicemail_twiml(greeting))


@router.post("/dial-status", dependencies=[Depends(verify_twilio_signature)])
async def dial_status(request: Request, db: Session = Depends(get_db)) -> Response:
    """<Dial> action callback. If owner answered, end the call. Otherwise
    fall through to voicemail using the org's configured greeting.
    """
    form = await request.form()
    call_sid = form.get("CallSid", "")
    dial_status_val = form.get("DialCallStatus", "")
    logger.info("twilio dial-status sid=%s status=%s", call_sid, dial_status_val)

    call = None
    if call_sid:
        call = db.query(CallEvent).filter(CallEvent.twilio_call_sid == call_sid).first()
        if call is not None:
            call.dial_status = dial_status_val or None
            call.answered_by_owner = dial_status_val == "completed"
            db.commit()

    if dial_status_val == "completed":
        return _twiml("<Response/>")

    greeting = _DEFAULT_MISSED_GREETING
    if call is not None:
        rule = get_routing_rule(db, call.org_id)
        if rule and rule.voicemail_greeting:
            greeting = rule.voicemail_greeting
    return _twiml(_voicemail_twiml(greeting))


@router.post("/recording-complete", dependencies=[Depends(verify_twilio_signature)])
async def recording_complete(request: Request, db: Session = Depends(get_db)) -> Response:
    """Persist the voicemail row. Twilio may fire this multiple times on
    retry, so we UPSERT keyed on RecordingSid.
    """
    form = await request.form()
    call_sid = form.get("CallSid", "")
    rec_sid = form.get("RecordingSid", "")
    rec_url = form.get("RecordingUrl", "")
    try:
        duration = int(form.get("RecordingDuration", "0"))
    except (TypeError, ValueError):
        duration = 0

    call = db.query(CallEvent).filter(CallEvent.twilio_call_sid == call_sid).first()
    if not call:
        logger.warning(
            "twilio recording-complete: no call_event for sid=%s rec_sid=%s",
            call_sid, rec_sid,
        )
        return _twiml("<Response/>")

    stmt = sqlite_insert(Voicemail).values(
        call_event_id=call.id,
        twilio_recording_sid=rec_sid,
        recording_url=rec_url,
        recording_duration=duration,
        transcription_status="pending",
    ).on_conflict_do_nothing(index_elements=["twilio_recording_sid"])
    db.execute(stmt)
    db.commit()

    logger.info(
        "twilio recording complete call_sid=%s rec_sid=%s duration=%ss",
        call_sid, rec_sid, duration,
    )
    return _twiml("<Response/>")


@router.post("/transcription-complete", dependencies=[Depends(verify_twilio_signature)])
async def transcription_complete(request: Request, db: Session = Depends(get_db)) -> Response:
    """Twilio's native STT finished. Conditional UPDATE on the voicemail
    row — only enqueue the classify job if we actually transitioned the
    row from pending -> completed (prevents double-processing on retry).
    Transcript text is NOT logged (PII).
    """
    form = await request.form()
    call_sid = form.get("CallSid", "")
    rec_sid = form.get("RecordingSid", "")
    trans_status = form.get("TranscriptionStatus", "")
    trans_text = form.get("TranscriptionText") or None

    new_status = "completed" if trans_status == "completed" else "failed"

    result = db.query(Voicemail).filter(
        Voicemail.twilio_recording_sid == rec_sid,
        Voicemail.transcription_status == "pending",
    ).update(
        {
            "transcript": trans_text,
            "transcription_status": new_status,
        },
        synchronize_session=False,
    )
    db.commit()

    if result == 0:
        logger.info(
            "twilio transcription-complete: already processed rec_sid=%s",
            rec_sid,
        )
        return Response(status_code=200)

    vm = db.query(Voicemail).filter(Voicemail.twilio_recording_sid == rec_sid).first()
    if vm is None:
        logger.error("transcription-complete: updated row but vm lookup missed rec_sid=%s", rec_sid)
        return Response(status_code=200)

    logger.info(
        "twilio transcription complete call_sid=%s rec_sid=%s status=%s vm_id=%s",
        call_sid, rec_sid, new_status, vm.id,
    )
    # Fire-and-forget. The background task opens its own DB session.
    asyncio.create_task(process_voicemail(vm.id))
    return Response(status_code=200)


@router.post("/call-status", dependencies=[Depends(verify_twilio_signature)])
async def call_status(request: Request, db: Session = Depends(get_db)) -> Response:
    """Final call status — writes duration + terminal status to call_events."""
    form = await request.form()
    call_sid = form.get("CallSid", "")
    status_val = form.get("CallStatus", "")
    try:
        duration = int(form.get("CallDuration", "0")) if form.get("CallDuration") else None
    except (TypeError, ValueError):
        duration = None

    if call_sid:
        updates: dict = {"status": status_val or "completed"}
        if duration is not None:
            updates["duration_seconds"] = duration
        if status_val in ("completed", "no-answer", "busy", "failed", "canceled"):
            updates["ended_at"] = datetime.now(timezone.utc)
        db.query(CallEvent).filter(
            CallEvent.twilio_call_sid == call_sid,
        ).update(updates, synchronize_session=False)
        db.commit()

    logger.info(
        "twilio call-status sid=%s status=%s duration=%s",
        call_sid, status_val, duration,
    )
    return Response(status_code=200)
