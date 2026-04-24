"""End-to-end local simulation of a Twilio voice call.

Exercises the full pipeline WITHOUT needing a real Twilio number or
ngrok. Uses FastAPI's TestClient to POST Twilio-shaped form data at
the /twilio/voice/* webhooks and then asserts DB state.

Signature validation is bypassed because APP_ENV defaults to
"development". SMS is attempted via Twilio if credentials are
present in OrgSettings, otherwise the send is recorded as "failed"
and the script still prints the captured body.

Usage:
    PYTHONPATH=. python scripts/simulate_twilio_call.py
    PYTHONPATH=. python scripts/simulate_twilio_call.py --after-hours
    PYTHONPATH=. python scripts/simulate_twilio_call.py --spam
    PYTHONPATH=. python scripts/simulate_twilio_call.py --with-dial
"""
import argparse
import asyncio
import sys
import time
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient


def _ensure_seed(org_id: int, to_number: str, owner_phone: str | None, force_after_hours: bool):
    """Make sure a PhoneNumber row exists for the test-To number, and a
    PhoneRoutingRule exists if owner_phone was requested. If
    force_after_hours, seed a phone_business_hours row that is closed
    right now so /incoming flags the call as after-hours."""
    from app.database import SessionLocal
    from app.models import PhoneBusinessHours, PhoneNumber, PhoneRoutingRule

    db = SessionLocal()
    try:
        existing = db.query(PhoneNumber).filter(PhoneNumber.phone_number == to_number).first()
        if not existing:
            db.add(PhoneNumber(
                org_id=org_id,
                twilio_sid=f"PN{uuid.uuid4().hex[:30]}",
                phone_number=to_number,
                friendly_name="Simulated test number",
                is_active=True,
            ))
            db.commit()
            print(f"seed: created PhoneNumber {to_number} -> org_id={org_id}")

        if owner_phone:
            rule = db.query(PhoneRoutingRule).filter(PhoneRoutingRule.org_id == org_id).first()
            if rule:
                rule.owner_phone = owner_phone
                rule.ring_owner_first = True
            else:
                db.add(PhoneRoutingRule(
                    org_id=org_id,
                    owner_phone=owner_phone,
                    ring_owner_first=True,
                    send_caller_confirmation=True,
                ))
            db.commit()
            print(f"seed: routing rule org_id={org_id} owner_phone={owner_phone}")

        # Wipe any prior test-seeded hours to keep the scenario deterministic.
        db.query(PhoneBusinessHours).filter(PhoneBusinessHours.org_id == org_id).delete()
        if force_after_hours:
            # Seed a 00:00-00:01 window for today's weekday -> guaranteed closed.
            dow = datetime.now(timezone.utc).astimezone().weekday()
            db.add(PhoneBusinessHours(
                org_id=org_id,
                day_of_week=dow,
                open_time="00:00",
                close_time="00:01",
                timezone="UTC",
            ))
            db.commit()
            print(f"seed: forced after-hours (open window 00:00-00:01 UTC for dow={dow})")
        else:
            db.commit()
    finally:
        db.close()


def _print_state(call_sid: str, recording_sid: str):
    """Dump DB state for the call we just simulated."""
    from app.database import SessionLocal
    from app.models import CallEvent, Lead, SmsNotification, Voicemail

    db = SessionLocal()
    try:
        call = db.query(CallEvent).filter(CallEvent.twilio_call_sid == call_sid).first()
        if not call:
            print("FAIL: no CallEvent row found for sid", call_sid)
            return
        print(
            f"  CallEvent: id={call.id} status={call.status} "
            f"dial_status={call.dial_status} after_hours={call.is_after_hours} "
            f"duration={call.duration_seconds}"
        )

        vm = db.query(Voicemail).filter(Voicemail.twilio_recording_sid == recording_sid).first()
        if vm:
            print(
                f"  Voicemail: id={vm.id} trans_status={vm.transcription_status} "
                f"classified_at={vm.classified_at} "
                f"transcript_preview={(vm.transcript or '')[:80]!r}"
            )
        else:
            print("  Voicemail: (not created — expected if no recording was simulated)")

        lead = db.query(Lead).filter(Lead.call_event_id == call.id).first()
        if lead:
            print(
                f"  Lead: id={lead.id} category={lead.category} urgency={lead.urgency_score} "
                f"status={lead.status} phone={lead.phone} name={lead.sender_name!r}"
            )
            print(f"  Lead.summary: {lead.summary!r}")
        else:
            print("  Lead: (not created — spam or error)")

        sms_rows = db.query(SmsNotification).filter(
            SmsNotification.call_event_id == call.id,
        ).order_by(SmsNotification.id).all()
        for s in sms_rows:
            print(
                f"  SMS: purpose={s.purpose} to={s.to_number} status={s.status} "
                f"twilio_sid={s.twilio_message_sid}"
            )
            print(f"       body={s.body!r}")
        if not sms_rows:
            print("  SMS: (none sent)")
    finally:
        db.close()


def _post_form(client: TestClient, path: str, form: dict) -> None:
    resp = client.post(path, data=form)
    if resp.status_code >= 400:
        raise SystemExit(f"{path} returned {resp.status_code}: {resp.text}")
    body_preview = resp.text[:120].replace("\n", " ")
    print(f"  POST {path} -> {resp.status_code} ({body_preview!r})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--org-id", type=int, default=1)
    parser.add_argument("--to", default="+15550000001", help="The business's Twilio number")
    parser.add_argument("--from", dest="from_number", default="+15557654321",
                        help="Caller's number (From)")
    parser.add_argument("--owner-phone", default=None,
                        help="Set this to enable <Dial> (with --with-dial)")
    parser.add_argument("--with-dial", action="store_true",
                        help="Simulate <Dial> -> dial-status -> fallback to voicemail")
    parser.add_argument("--after-hours", action="store_true",
                        help="Claim the call arrived after hours")
    parser.add_argument("--spam", action="store_true",
                        help="Use a clearly-spammy transcript")
    parser.add_argument("--transcript", default=None, help="Custom transcript text")
    parser.add_argument("--sms-reply", default=None,
                        help="Simulate the after-hours SMS outreach flow: caller hangs up without leaving VM, then sends this SMS reply. Skips voicemail steps.")
    args = parser.parse_args()

    _ensure_seed(args.org_id, args.to, args.owner_phone, args.after_hours)

    # Import after seeding so any late model imports are resolved fresh.
    from app.main import app

    call_sid = f"CA{uuid.uuid4().hex[:30]}"
    recording_sid = f"RE{uuid.uuid4().hex[:30]}"

    transcript = args.transcript or (
        "Hi, this is a final notice about your car's extended warranty. "
        "Press one now."
        if args.spam
        else "Hi, this is Sarah Johnson at 555-123-4567. I have a burst "
             "pipe under my kitchen sink and water is going everywhere. "
             "Please call me back as soon as you can. My address is 123 "
             "Maple Street."
    )

    with TestClient(app) as client:
        print(f"\n=== /incoming call_sid={call_sid} ===")
        _post_form(client, "/twilio/voice/incoming", {
            "CallSid": call_sid,
            "From": args.from_number,
            "To": args.to,
            "FromCity": "Edmonton",
            "FromState": "AB",
            "CallerName": "TEST CALLER",
        })

        if args.with_dial:
            print(f"\n=== /dial-status (simulating no-answer) ===")
            _post_form(client, "/twilio/voice/dial-status", {
                "CallSid": call_sid,
                "DialCallStatus": "no-answer",
                "DialCallDuration": "20",
            })

        if args.sms_reply is not None:
            # Skip the voicemail path entirely — simulate a mobile caller
            # hanging up after the "we'll text you" message, then replying
            # by SMS a few seconds later.
            print(f"\n=== waiting for outreach SMS to fire ===")
            time.sleep(0.5)

            print(f"\n=== /sms/webhook (caller replies with: {args.sms_reply!r}) ===")
            _post_form(client, "/sms/webhook", {
                "From": args.from_number,
                "To": args.to,
                "Body": args.sms_reply,
                "NumMedia": "0",
                "MessageSid": f"SM{uuid.uuid4().hex[:30]}",
            })
        else:
            print(f"\n=== /recording-complete rec_sid={recording_sid} ===")
            _post_form(client, "/twilio/voice/recording-complete", {
                "CallSid": call_sid,
                "RecordingSid": recording_sid,
                "RecordingUrl": f"https://api.twilio.com/2010-04-01/Accounts/ACfake/Recordings/{recording_sid}",
                "RecordingDuration": "42",
                "RecordingStatus": "completed",
            })

            print(f"\n=== /transcription-complete (enqueues process_voicemail) ===")
            _post_form(client, "/twilio/voice/transcription-complete", {
                "CallSid": call_sid,
                "RecordingSid": recording_sid,
                "TranscriptionSid": f"TR{uuid.uuid4().hex[:30]}",
                "TranscriptionText": transcript,
                "TranscriptionStatus": "completed",
            })

            print("\n=== waiting for background process_voicemail... ===")
            _wait_for_classification(recording_sid, timeout_s=30)

        print(f"\n=== /call-status (final) ===")
        _post_form(client, "/twilio/voice/call-status", {
            "CallSid": call_sid,
            "CallStatus": "completed",
            "CallDuration": "65",
            "Timestamp": datetime.now(timezone.utc).isoformat(),
        })

    print("\n=== final state ===")
    _print_state(call_sid, recording_sid)
    return 0


def _wait_for_classification(recording_sid: str, timeout_s: int = 30) -> None:
    """Poll the voicemails table for classified_at to be set.

    TestClient runs each request on a new event loop, so asyncio.create_task
    inside the transcription-complete handler ends when that loop closes.
    We replay the task synchronously here if needed.
    """
    from app.database import SessionLocal
    from app.models import Voicemail
    from app.voicemail_processor import process_voicemail

    start = time.time()
    while time.time() - start < timeout_s:
        db = SessionLocal()
        try:
            vm = db.query(Voicemail).filter(
                Voicemail.twilio_recording_sid == recording_sid,
            ).first()
            if vm and vm.classified_at is not None:
                return
            if vm and vm.classified_at is None:
                # The fire-and-forget task likely got cancelled when the
                # TestClient loop closed. Run it inline.
                vm_id = vm.id
                db.close()
                print(f"  (inline-running process_voicemail for vm_id={vm_id})")
                asyncio.run(process_voicemail(vm_id))
                return
        finally:
            db.close()
        time.sleep(0.2)
    print("  TIMEOUT waiting for classification")


if __name__ == "__main__":
    sys.exit(main())
