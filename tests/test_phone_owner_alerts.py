from app.config import settings
from app.database import SessionLocal
from app.models import CallEvent, Organization, OrgSettings, SmsNotification
from app import phone_leads
from app.sms_intake import process_sms_lead


def test_sms_lead_owner_alert_uses_platform_fallback(monkeypatch):
    monkeypatch.setattr(settings, "sms_alert_to_number", "+18254401394")
    monkeypatch.setattr(
        phone_leads,
        "send_sms_to",
        lambda body, to_number, org_settings=None: (True, "sent", "SM_OWNER_ALERT"),
    )

    db = SessionLocal()
    try:
        org = Organization(name="Fallback Plumbing", slug="fallback-plumbing")
        db.add(org)
        db.flush()
        db.add(OrgSettings(org_id=org.id, business_name="Fallback Plumbing"))
        call = CallEvent(
            org_id=org.id,
            twilio_call_sid="CA_OWNER_ALERT_FALLBACK",
            from_number="+17802633390",
            to_number="+17822121292",
            status="completed",
            dial_status="no-answer",
            is_after_hours=False,
        )
        db.add(call)
        db.commit()
        call_id = call.id
    finally:
        db.close()

    lead_id = process_sms_lead(call_id, "My furnace quit and I need help ASAP")
    assert lead_id is not None

    db = SessionLocal()
    try:
        alert = db.query(SmsNotification).filter(
            SmsNotification.lead_id == lead_id,
            SmsNotification.purpose == "owner_alert",
        ).one()
        assert alert.to_number == "+18254401394"
        assert alert.status == "sent"
        assert alert.twilio_message_sid == "SM_OWNER_ALERT"
    finally:
        db.close()
