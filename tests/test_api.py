from fastapi.testclient import TestClient
from datetime import timedelta

from app.main import app
from app.database import SessionLocal
from app.models import CallEvent, Lead, Organization, OrgSettings, PhoneNumber, PhoneRoutingRule, SmsOptOut, User
from app.routes import leads as lead_routes
from app.routes import phone_provisioning as phone_routes

client = TestClient(app)


def _auth_client():
    """Register a test org+user and return an authenticated TestClient."""
    import uuid
    unique = uuid.uuid4().hex[:8]
    c = TestClient(app)
    res = c.post("/auth/register", json={
        "org_name": f"Test Org {unique}",
        "email": f"test-{unique}@example.com",
        "password": "TestPass123",
        "display_name": "Test User",
    })
    assert res.status_code == 200, res.text
    return c, {"data": res.json()}


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_marketing_page_links_to_book_demo():
    response = client.get("/")
    assert response.status_code == 200
    assert 'href="/book-demo"' in response.text


def test_book_demo_page_has_contact_form():
    response = client.get("/book-demo")
    assert response.status_code == 200
    assert 'action="/contact"' in response.text
    assert 'name="name"' in response.text
    assert 'name="company"' in response.text
    assert 'name="email"' in response.text
    assert 'name="phone"' in response.text
    assert 'name="message"' in response.text


def test_leads_require_auth():
    response = client.get("/leads")
    assert response.status_code == 401


def test_auth_login_register_flow():
    c, info = _auth_client()
    # /auth/me should return the logged-in user
    me = c.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == info["data"]["user"]["email"]

    # Logout clears session
    logout = c.post("/auth/logout")
    assert logout.status_code == 200

    # Unauthenticated client should fail
    me2 = client.get("/auth/me")
    assert me2.status_code == 401


def test_forwarded_email_ingest_requires_auth():
    response = client.post("/forwarded-email", json={"token": "wrong", "from_email": "lead@example.com", "body": "Need help"})
    assert response.status_code == 401


def test_forwarded_email_ingest_via_api_key():
    c, info = _auth_client()
    api_key = info["data"]["org"]["api_key"]

    # Update the org's forwarding token
    c.patch("/api/settings", json={"forwarding_token": "test-fwd-token"})

    raw_email = "From: Megan Foster <megan@example.com>\nSubject: Need plumbing help\n\nWe have an active leak in Edmonton. Call 780-555-0123."
    response = client.post(
        "/forwarded-email",
        headers={"X-API-Key": api_key},
        json={"token": "test-fwd-token", "raw_email": raw_email},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True

    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.slug == info["data"]["org"]["slug"]).first()
        assert org.api_key != api_key
        assert org.api_key_hash is not None
    finally:
        db.close()


def test_leads_hide_spam_by_default():
    c, _ = _auth_client()
    spam = c.post(
        "/ingest-lead",
        json={
            "source": "gmail_api",
            "sender_name": "Pitch Bot",
            "sender_email": "pitch@example.com",
            "subject": "Guaranteed SEO backlinks",
            "body": "We sell SEO backlinks and marketing package services.",
        },
    )
    assert spam.status_code == 200, spam.text
    assert spam.json()["status"] == "spam"

    legit = c.post(
        "/ingest-lead",
        json={
            "source": "gmail_api",
            "sender_name": "Real Lead",
            "sender_email": "real@example.com",
            "subject": "Plumbing estimate",
            "body": "My sink is leaking and I need a quote.",
        },
    )
    assert legit.status_code == 200, legit.text

    default_list = c.get("/leads")
    assert default_list.status_code == 200
    default_ids = {item["id"] for item in default_list.json()["items"]}
    assert legit.json()["id"] in default_ids
    assert spam.json()["id"] not in default_ids

    spam_list = c.get("/leads?include_spam=true")
    assert spam_list.status_code == 200
    spam_ids = {item["id"] for item in spam_list.json()["items"]}
    assert legit.json()["id"] in spam_ids
    assert spam.json()["id"] in spam_ids


def test_lead_map_returns_geocoded_leads(monkeypatch):
    c, _ = _auth_client()
    monkeypatch.setattr(lead_routes, "geocode_location", lambda location: (53.5461, -113.4938))

    created = c.post(
        "/ingest-lead",
        json={
            "source": "gmail_api",
            "sender_name": "Map Lead",
            "sender_email": "map@example.com",
            "subject": "Plumbing estimate",
            "body": "My sink is leaking in Edmonton and I need a quote.",
            "location": "Edmonton",
        },
    )
    assert created.status_code == 200, created.text

    response = c.get("/api/lead-map")
    assert response.status_code == 200
    items = response.json()["items"]
    mapped = [item for item in items if item["id"] == created.json()["id"]]
    assert mapped
    assert mapped[0]["lat"] == 53.5461
    assert mapped[0]["lng"] == -113.4938


def test_rescue_endpoint_removed():
    assert client.get("/auth/rescue").status_code == 404


def test_contact_form_submission_redirects_to_success():
    response = client.post(
        "/contact",
        data={
            "name": "Reese",
            "company": "Reese Plumbing",
            "email": "reese@example.com",
            "phone": "780-555-0100",
            "message": "Need a walkthrough.",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/book-demo?status=success"


def test_ingest_lead():
    c, _ = _auth_client()
    response = c.post(
        "/ingest-lead",
        json={
            "source": "test",
            "sender_name": "Alex",
            "sender_email": "alex@example.com",
            "subject": "Need estimate",
            "body": "Can I get a quote for HVAC maintenance in Edmonton?",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["category"] in {"quote_request", "general_inquiry"}


def test_manual_review_send_ignores_confidence_threshold(monkeypatch):
    c, _ = _auth_client()
    created = c.post(
        "/ingest-lead",
        json={
            "source": "test",
            "sender_name": "Low Confidence",
            "sender_email": "lowconfidence@example.com",
            "subject": "Need help maybe",
            "body": "Not sure what I need but can someone call me?",
        },
    )
    assert created.status_code == 200
    lead_id = created.json()["id"]

    monkeypatch.setattr(lead_routes, "send_email", lambda **kwargs: (True, "sent"))

    response = c.post(f"/leads/{lead_id}/review/send")
    assert response.status_code == 200
    data = response.json()
    assert data["sent"] is True
    assert data["status"] == "sent"
    assert data["sent_to"] == "lowconfidence@example.com"
    assert data["subject"].startswith("Re: ")

    activities = c.get(f"/leads/{lead_id}/activities")
    assert activities.status_code == 200
    assert any(item["activity_type"] == "reply_sent" for item in activities.json())


def test_update_and_delete_lead():
    c, _ = _auth_client()
    created = c.post(
        "/ingest-lead",
        json={
            "source": "test",
            "sender_name": "Edit Me",
            "sender_email": "editme@example.com",
            "subject": "Initial subject",
            "body": "Initial body",
        },
    )
    lead_id = created.json()["id"]

    updated = c.patch(
        f"/leads/{lead_id}",
        json={"recommended_reply": "Updated draft", "status": "drafted"},
    )
    assert updated.status_code == 200
    assert updated.json()["recommended_reply"] == "Updated draft"

    deleted = c.delete(f"/leads/{lead_id}")
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True


def test_org_isolation():
    """Leads from one org should not be visible to another."""
    c1, _ = _auth_client()
    c2, _ = _auth_client()

    # Create a lead in org1
    res1 = c1.post("/ingest-lead", json={
        "source": "test", "sender_email": "isolated@example.com",
        "subject": "Org1 lead", "body": "Test isolation",
    })
    lead_id = res1.json()["id"]

    # Org2 should not see it
    res2 = c2.get(f"/leads/{lead_id}")
    assert res2.status_code == 404

    # Org2's lead list should not contain it
    list2 = c2.get("/leads")
    assert all(lead["id"] != lead_id for lead in list2.json()["items"])


def test_settings_crud():
    c, _ = _auth_client()
    # Get defaults
    res = c.get("/api/settings")
    assert res.status_code == 200
    assert res.json()["human_review"] is True

    # Update
    res = c.patch("/api/settings", json={"business_name": "Test Biz", "human_review": False})
    assert res.status_code == 200
    assert res.json()["business_name"] == "Test Biz"
    assert res.json()["human_review"] is False


def test_automation_paused_blocks_auto_send(monkeypatch):
    c, _ = _auth_client()
    c.patch("/api/settings", json={"human_review": False, "automation_paused": True})

    created = c.post(
        "/ingest-lead",
        json={
            "source": "test",
            "sender_name": "Auto Pause",
            "sender_email": "pause@example.com",
            "subject": "General question",
            "body": "Can someone tell me what services you offer?",
        },
    )
    assert created.status_code == 200, created.text
    data = created.json()
    assert data["status"] == "ready_to_send"
    assert data["send_at"] is None


def test_inactive_org_blocks_phone_provisioning():
    c, info = _auth_client()
    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.slug == info["data"]["org"]["slug"]).first()
        org.subscription_status = "disabled"
        db.commit()
    finally:
        db.close()

    res = c.post("/api/phone/search", json={"area_code": "403"})
    assert res.status_code == 402


def test_member_cannot_change_phone_routing():
    owner, _ = _auth_client()
    invite = owner.post("/auth/invite", json={"email": "member-phone@example.com"})
    assert invite.status_code == 200
    token = invite.json()["invite_url"].split("invite=")[1]

    member = TestClient(app)
    accepted = member.post("/auth/accept-invite", json={"token": token, "password": "MemberPass123"})
    assert accepted.status_code == 200

    res = member.post("/api/phone/routing", json={"owner_phone": "4035559999"})
    assert res.status_code == 403


def test_sms_stop_creates_opt_out():
    c, info = _auth_client()
    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.slug == info["data"]["org"]["slug"]).first()
        db.add(PhoneNumber(org_id=org.id, twilio_sid="PN_STOP", phone_number="+14035550111", is_active=True))
        db.commit()
        org_id = org.id
    finally:
        db.close()

    res = client.post("/sms/webhook", data={"Body": "STOP", "From": "+14035559999", "To": "+14035550111"})
    assert res.status_code == 200
    assert "unsubscribed" in res.text

    db = SessionLocal()
    try:
        row = db.query(SmsOptOut).filter(SmsOptOut.org_id == org_id, SmsOptOut.phone_number == "+14035559999").first()
        assert row is not None
        assert row.opted_in_at is None
    finally:
        db.close()


def test_rescue_setup_buys_number_and_saves_routing(monkeypatch):
    c, _ = _auth_client()

    monkeypatch.setattr(phone_routes, "_webhook_urls", lambda: (
        "https://example.test/twilio/voice/incoming",
        "https://example.test/twilio/voice/call-status",
    ))
    monkeypatch.setattr(phone_routes, "search_available_numbers", lambda **kwargs: [{
        "phone_number": "+14035550123",
        "friendly_name": "(403) 555-0123",
        "locality": "Calgary",
        "region": "AB",
    }])
    monkeypatch.setattr(phone_routes, "provision_number", lambda phone, **kwargs: {
        "sid": "PN_TEST_123",
        "phone_number": phone,
        "friendly_name": "LeadRelay rescue line",
    })

    res = c.post("/api/phone/rescue-setup", json={
        "area_code": "403",
        "owner_phone": "403-555-9999",
    })
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ok"] is True
    assert data["phone"]["phone_number"] == "+14035550123"
    assert data["routing"]["owner_phone"] == "+14035559999"
    assert data["rescue"]["is_live"] is True

    status = c.get("/api/phone/my-number")
    assert status.status_code == 200
    assert status.json()["phone"]["phone_number"] == "+14035550123"


def test_rescue_setup_existing_number_only_updates_routing(monkeypatch):
    c, _ = _auth_client()
    calls = {"provision": 0}

    monkeypatch.setattr(phone_routes, "_webhook_urls", lambda: (
        "https://example.test/twilio/voice/incoming",
        "https://example.test/twilio/voice/call-status",
    ))
    monkeypatch.setattr(phone_routes, "search_available_numbers", lambda **kwargs: [{
        "phone_number": "+14035550124",
        "friendly_name": "(403) 555-0124",
    }])
    def fake_provision(phone, **kwargs):
        calls["provision"] += 1
        return {"sid": "PN_TEST_124", "phone_number": phone, "friendly_name": "LeadRelay rescue line"}
    monkeypatch.setattr(phone_routes, "provision_number", fake_provision)

    first = c.post("/api/phone/rescue-setup", json={"area_code": "403", "owner_phone": "4035551111"})
    assert first.status_code == 200
    second = c.post("/api/phone/rescue-setup", json={"area_code": "403", "owner_phone": "4035552222"})
    assert second.status_code == 200
    assert second.json()["already_had_number"] is True
    assert second.json()["routing"]["owner_phone"] == "+14035552222"
    assert calls["provision"] == 1


def test_rescue_forwarding_setup_returns_activation_code(monkeypatch):
    c, _ = _auth_client()

    monkeypatch.setattr(phone_routes, "_webhook_urls", lambda: (
        "https://example.test/twilio/voice/incoming",
        "https://example.test/twilio/voice/call-status",
    ))
    monkeypatch.setattr(phone_routes, "search_available_numbers", lambda **kwargs: [{
        "phone_number": "+14035550155",
        "friendly_name": "(403) 555-0155",
    }])
    monkeypatch.setattr(phone_routes, "provision_number", lambda phone, **kwargs: {
        "sid": "PN_FORWARD_155",
        "phone_number": phone,
        "friendly_name": "LeadRelay missed-call rescue",
    })

    res = c.post("/api/phone/rescue-forwarding/setup", json={
        "current_business_number": "403-555-0100",
        "owner_phone": "403-555-9999",
        "area_code": "403",
        "carrier": "telus",
    })

    assert res.status_code == 200, res.text
    data = res.json()
    assert data["phone"]["phone_number"] == "+14035550155"
    assert data["routing"]["current_business_number"] == "+14035550100"
    assert data["forwarding"]["status"] == "activation_shown"
    assert data["forwarding"]["activation"]["activation_code"] == "*61*+14035550155#"
    assert data["forwarding"]["activation"]["tel_link"].endswith("%23")


def test_rescue_forwarding_test_status_marks_live(monkeypatch):
    c, info = _auth_client()
    org_slug = info["data"]["org"]["slug"]

    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.slug == org_slug).first()
        db.add(PhoneNumber(
            org_id=org.id,
            twilio_sid="PN_FORWARD_EXISTING",
            phone_number="+14035550177",
            is_active=True,
        ))
        db.add(PhoneRoutingRule(
            org_id=org.id,
            owner_phone="+14035559999",
            current_business_number="+14035550100",
            forwarding_carrier="telus",
            forwarding_setup_status="activation_shown",
            forwarding_code_used="*61*+14035550177#",
        ))
        db.commit()
    finally:
        db.close()

    started = c.post("/api/phone/rescue-forwarding/test/start")
    assert started.status_code == 200, started.text
    assert started.json()["forwarding"]["status"] == "testing"

    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.slug == org_slug).first()
        rule = db.query(PhoneRoutingRule).filter(PhoneRoutingRule.org_id == org.id).first()
        db.add(CallEvent(
            org_id=org.id,
            twilio_call_sid="CA_FORWARD_TEST",
            from_number="+14035551234",
            to_number="+14035550177",
            status="completed",
            answered_by_owner=False,
            started_at=rule.forwarding_test_started_at + timedelta(seconds=1),
        ))
        db.commit()
    finally:
        db.close()

    status = c.get("/api/phone/rescue-forwarding/test/status")
    assert status.status_code == 200
    data = status.json()["forwarding"]
    assert data["status"] == "live"
    assert data["test_call"]["from_number"] == "+14035551234"
