from app.config import settings
from app.database import SessionLocal
from app.models import CallEvent, Lead, Organization, OrgSettings, PhoneRoutingRule, SmsNotification
from app import phone_leads
from app.sms_intake import process_sms_lead


def test_sms_lead_owner_alert_uses_platform_fallback(monkeypatch):
    monkeypatch.setattr(settings, "sms_alert_to_number", "+18254401394")
    monkeypatch.setattr(
        phone_leads,
        "send_sms_to",
        lambda body, to_number, org_settings=None, **kwargs: (True, "sent", "SM_OWNER_ALERT"),
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


def test_record_sms_retries_with_platform_default_after_org_number_failure(monkeypatch):
    monkeypatch.setattr(settings, "twilio_from_number", "+14035550000")
    attempts = []

    def fake_send(body, to_number, org_settings=None, **kwargs):
        attempts.append(kwargs.get("from_number"))
        if kwargs.get("from_number") == "+14035550123":
            return False, "from number cannot send", None
        return True, "sent", "SM_FALLBACK"

    monkeypatch.setattr(phone_leads, "send_sms_to", fake_send)

    db = SessionLocal()
    try:
        org = Organization(name="Retry Plumbing", slug="retry-plumbing")
        db.add(org)
        db.flush()
        settings_row = OrgSettings(org_id=org.id, business_name="Retry Plumbing")
        db.add(settings_row)
        db.commit()

        notification = phone_leads.record_and_send_sms(
            db,
            org_id=org.id,
            to_number="+18254401394",
            body="Owner alert",
            purpose="owner_alert",
            lead_id=None,
            call_event_id=None,
            org_settings=settings_row,
            from_number="+14035550123",
        )

        assert notification.status == "sent"
        assert notification.twilio_message_sid == "SM_FALLBACK"
        assert attempts == ["+14035550123", None]
    finally:
        db.close()


def test_sms_update_retries_missing_owner_alert_for_existing_phone_lead(monkeypatch):
    monkeypatch.setattr(
        phone_leads,
        "send_sms_to",
        lambda body, to_number, org_settings=None, **kwargs: (True, "sent", "SM_RETRIED_OWNER_ALERT"),
    )

    db = SessionLocal()
    try:
        org = Organization(name="Existing Plumbing", slug="existing-plumbing")
        db.add(org)
        db.flush()
        db.add(OrgSettings(org_id=org.id, business_name="Existing Plumbing"))
        db.add(PhoneRoutingRule(org_id=org.id, owner_phone="+14035559999"))
        call = CallEvent(
            org_id=org.id,
            twilio_call_sid="CA_EXISTING_NO_ALERT",
            from_number="+17802633390",
            to_number="+17822121292",
            status="completed",
            dial_status="no-answer",
            is_after_hours=False,
        )
        db.add(call)
        db.flush()
        lead = Lead(
            org_id=org.id,
            source="phone",
            call_event_id=call.id,
            sender_email="caller-17802633390@phone.leadrelay.local",
            subject="Voicemail from +17802633390",
            body="Original voicemail",
            phone="+17802633390",
            category="general_inquiry",
            urgency_score=2,
            summary="Original voicemail",
            owner_alert_needed=True,
            status="new",
            confidence=0.4,
        )
        db.add(lead)
        db.commit()
        call_id = call.id
        lead_id = lead.id
    finally:
        db.close()

    assert process_sms_lead(call_id, "Actually this is urgent, furnace is out") == lead_id

    db = SessionLocal()
    try:
        alert = db.query(SmsNotification).filter(
            SmsNotification.lead_id == lead_id,
            SmsNotification.purpose == "owner_alert",
        ).one()
        assert alert.status == "sent"
        assert alert.twilio_message_sid == "SM_RETRIED_OWNER_ALERT"
    finally:
        db.close()
