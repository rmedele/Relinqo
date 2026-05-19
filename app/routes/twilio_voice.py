"""Twilio Programmable Voice webhooks for relinqo phone lead capture.

V1 scope: missed-call rescue, voicemail-to-lead, after-hours intake.
The primary path for missed calls is SMS outreach — Twilio says "we'll
text you" and hangs up. Voicemail is a fallback that runs in parallel
so landline callers can still leave a message.

All endpoints are signature-validated in production.
"""
import asyncio
import logging
import xml.sax.saxutils as saxutils
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CallEvent, OrgSettings, PhoneNumber, Voicemail
from app.phone_routing import get_routing_rule, is_within_business_hours, should_dial_owner
from app.sms_intake import send_outreach_sms
from app.twilio_signature import verify_twilio_signature
from app.voicemail_processor import process_voicemail

router = APIRouter(prefix="/twilio/voice", tags=["twilio-voice"])
logger = logging.getLogger(__name__)


def _twiml(xml_body: str) -> Response:
    return Response(
        content=f'<?xml version="1.0" encoding="UTF-8"?>{xml_body}',
        media_type="application/xml",
    )


# Default greetings — org can override via PhoneRoutingRule.
_DEFAULT_MISSED_GREETING = (
    "Hi, thanks for calling. We missed you but we're sending you a text "
    "right now so you can tell us what you need. If you can't receive "
    "texts, please leave a message after the beep."
)
_DEFAULT_AFTER_HOURS_GREETING = (
    "Hi, thanks for calling. We're closed right now but we're sending you "
    "a text right now so you can tell us what you need. If you can't "
    "receive texts, please leave a message after the beep."
)


def _outreach_twiml(greeting: str) -> str:
    """Play the "we'll text you" message, then offer a short voicemail
    as a landline fallback. Mobile callers typically hang up during the
    Record; the outreach SMS has already fired in parallel."""
    safe = saxutils.escape(greeting)
    return (
        "<Response>"
        f'<Say voice="Polly.Joanna">{safe}</Say>'
        '<Record '
        'maxLength="90" '
        'playBeep="true" '
        'trim="trim-silence" '
        'timeout="5" '
        'finishOnKey="#" '
        'transcribe="true" '
        'transcribeCallback="/twilio/voice/transcription-complete" '
        'action="/twilio/voice/recording-complete" '
        'method="POST"/>'
        "</Response>"
    )


def _dial_twiml(owner_phone: str, caller_id: str, timeout: int) -> str:
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


def _greeting_for(rule, is_after_hours: bool) -> str:
    if rule:
        if is_after_hours and rule.after_hours_greeting:
            return rule.after_hours_greeting
        if not is_after_hours and rule.voicemail_greeting:
            return rule.voicemail_greeting
    return _DEFAULT_AFTER_HOURS_GREETING if is_after_hours else _DEFAULT_MISSED_GREETING


def _fire_outreach(call_event_id: int) -> None:
    """Kick off outreach SMS in the background so the TwiML response
    returns fast. send_outreach_sms is synchronous — wrap in a thread
    so we don't block the event loop on the Twilio API call."""
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, send_outreach_sms, call_event_id)


def _commit_idempotent(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


@router.post("/incoming", dependencies=[Depends(verify_twilio_signature)])
async def incoming_call(request: Request, db: Session = Depends(get_db)) -> Response:
    """Call entrypoint. Branches:
      - In hours + owner configured -> <Dial>, fall through to outreach on no-answer.
      - After hours OR no owner -> outreach TwiML + fire outreach SMS.
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

    if not db.query(CallEvent.id).filter(CallEvent.twilio_call_sid == call_sid).first():
        db.add(CallEvent(
            org_id=phone.org_id,
            twilio_call_sid=call_sid,
            from_number=from_num,
            to_number=to_num,
            from_city=from_city,
            from_state=from_state,
            direction="inbound",
            status="ringing",
            is_after_hours=is_after_hours,
        ))
        _commit_idempotent(db)

    # Re-fetch for the id (idempotent insert doesn't return it on conflict).
    call = db.query(CallEvent).filter(CallEvent.twilio_call_sid == call_sid).first()

    logger.info(
        "twilio incoming sid=%s org=%s from=%s to=%s after_hours=%s dial_owner=%s",
        call_sid, phone.org_id, _redact(from_num), to_num, is_after_hours, dial_owner,
    )

    if dial_owner and rule is not None:
        # Don't fire outreach yet — wait to see if the owner picks up.
        return _twiml(_dial_twiml(
            owner_phone=rule.owner_phone,
            caller_id=to_num,
            timeout=rule.ring_timeout_seconds or 20,
        ))

    # No dial attempted — fire outreach SMS immediately and return
    # TwiML that plays the "we'll text you" message.
    if call is not None:
        _fire_outreach(call.id)

    return _twiml(_outreach_twiml(_greeting_for(rule, is_after_hours)))


@router.post("/dial-status", dependencies=[Depends(verify_twilio_signature)])
async def dial_status(request: Request, db: Session = Depends(get_db)) -> Response:
    """<Dial> action callback. If owner picked up, end the call. If not,
    fire the outreach SMS and fall through to "we'll text you" TwiML.
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

    # Owner didn't pick up — fire outreach SMS and play the message.
    if call is not None:
        _fire_outreach(call.id)
        rule = get_routing_rule(db, call.org_id)
    else:
        rule = None
    return _twiml(_outreach_twiml(_greeting_for(rule, is_after_hours=False)))


@router.post("/recording-complete", dependencies=[Depends(verify_twilio_signature)])
async def recording_complete(request: Request, db: Session = Depends(get_db)) -> Response:
    """Persist the voicemail row — idempotent UPSERT keyed on RecordingSid.
    Skips very short recordings (< 2s) which are typically mobile callers
    who hung up during/after the "we'll text you" message.
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
        logger.warning("recording-complete: no call_event sid=%s rec=%s", call_sid, rec_sid)
        return _twiml("<Response/>")

    if duration < 2:
        logger.info("recording-complete: skipping %ss recording (likely hangup)", duration)
        return _twiml("<Response/>")

    if not db.query(Voicemail.id).filter(Voicemail.twilio_recording_sid == rec_sid).first():
        db.add(Voicemail(
            call_event_id=call.id,
            twilio_recording_sid=rec_sid,
            recording_url=rec_url,
            recording_duration=duration,
            transcription_status="pending",
        ))
        _commit_idempotent(db)

    logger.info(
        "twilio recording complete call_sid=%s rec_sid=%s duration=%ss",
        call_sid, rec_sid, duration,
    )
    return _twiml("<Response/>")


@router.post("/transcription-complete", dependencies=[Depends(verify_twilio_signature)])
async def transcription_complete(request: Request, db: Session = Depends(get_db)) -> Response:
    """Twilio STT finished. Conditional UPDATE + enqueue Claude classify.
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
        {"transcript": trans_text, "transcription_status": new_status},
        synchronize_session=False,
    )
    db.commit()

    if result == 0:
        logger.info("transcription-complete: already processed rec=%s", rec_sid)
        return Response(status_code=200)

    vm = db.query(Voicemail).filter(Voicemail.twilio_recording_sid == rec_sid).first()
    if vm is None:
        logger.error("transcription-complete: row missing after update rec=%s", rec_sid)
        return Response(status_code=200)

    logger.info(
        "twilio transcription complete call_sid=%s rec=%s status=%s vm=%s",
        call_sid, rec_sid, new_status, vm.id,
    )
    asyncio.create_task(process_voicemail(vm.id))
    return Response(status_code=200)


@router.post("/call-status", dependencies=[Depends(verify_twilio_signature)])
async def call_status(request: Request, db: Session = Depends(get_db)) -> Response:
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
