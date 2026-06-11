import json
from fastapi.testclient import TestClient
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app import auth as auth_helpers
from app import main as main_module
from app import outbound_webhooks
from app.main import app
from app.config import settings
from app.database import SessionLocal
from app.models import (
    CallEvent,
    InviteToken,
    Lead,
    Organization,
    OrgSettings,
    PhoneNumber,
    PhoneRoutingRule,
    ScheduleAvailability,
    SmsNotification,
    SmsOptOut,
    TrialCode,
    TrialCodeRedemption,
    User,
)
from app.routes import billing as billing_routes
from app.routes import leads as lead_routes
from app.routes import phone_provisioning as phone_routes
from app.routes import settings as settings_routes
from app.schema_repair import ensure_org_settings_schema

client = TestClient(app)


def _auth_client(email: str | None = None):
    """Register a test org+user and return an authenticated TestClient."""
    import uuid
    unique = uuid.uuid4().hex[:8]
    user_email = email or f"test-{unique}@example.com"
    c = TestClient(app)
    res = c.post("/auth/register", json={
        "org_name": f"Test Org {unique}",
        "email": user_email,
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
    assert 'href="/demo"' in response.text
    assert 'id="live-test"' in response.text
    assert 'data-marketing-demo-contact' in response.text
    assert "Lead leak simulator" in response.text


def test_live_demo_page_has_public_form():
    response = client.get("/demo")
    assert response.status_code == 200
    assert 'id="liveDemoForm"' in response.text
    assert 'id="realDemoContact"' in response.text
    assert 'id="demoPhoneNumber"' in response.text
    assert 'name="lead_text"' in response.text
    assert 'data-sample="urgent"' in response.text


def test_live_demo_api_classifies_without_creating_lead():
    db = SessionLocal()
    try:
        before = db.query(Lead).count()
    finally:
        db.close()

    response = client.post(
        "/api/demo/lead",
        json={
            "sender_name": "Daniel",
            "phone": "+17805550134",
            "trade": "plumbing",
            "subject": "Burst pipe",
            "lead_text": "We have a burst pipe in the basement and need help ASAP in Edmonton.",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True
    assert data["category"] == "urgent_request"
    assert data["owner_alert_needed"] is True
    assert "book/demo-preview" in data["recommended_reply"]
    assert data["timeline"]

    db = SessionLocal()
    try:
        after = db.query(Lead).count()
    finally:
        db.close()
    assert after == before


def test_demo_config_and_real_inbound_flow(monkeypatch):
    monkeypatch.setattr(settings, "demo_inbox_email", "demo@relinqo.test")
    monkeypatch.setattr(settings, "demo_phone_number", "+17825550100")
    monkeypatch.setattr(settings, "demo_forwarding_token", "")
    monkeypatch.setattr(settings, "app_env", "development")

    config = client.get("/api/demo/config")
    assert config.status_code == 200
    assert config.json()["enabled"] is True
    assert config.json()["demo_inbox_email"] == "demo@relinqo.test"
    assert config.json()["demo_sms_webhook_url"].endswith("/demo/sms/webhook")
    assert config.json()["demo_voice_webhook_url"].endswith("/demo/voice/incoming")

    response = client.post(
        "/api/demo/inbound",
        json={
            "source": "email",
            "from_name": "Sam Pilot",
            "from_email": "sam@example.com",
            "from_phone": "+17805550135",
            "trade": "plumbing",
            "subject": "Water heater leaking",
            "body": "My water heater is leaking badly with water everywhere and we need someone ASAP.",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True
    assert data["demo_url"].endswith(f"/demo?lead={data['demo_id']}")
    assert data["result"]["category"] == "urgent_request"

    saved = client.get(f"/api/demo/leads/{data['demo_id']}")
    assert saved.status_code == 200
    assert saved.json()["from_name"] == "Sam Pilot"


def test_demo_inbound_requires_token_in_production(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "demo_forwarding_token", "demo-secret")

    payload = {"source": "email", "body": "Customer needs urgent plumbing help today."}
    assert client.post("/api/demo/inbound", json=payload).status_code == 401

    ok = client.post("/api/demo/inbound", json={**payload, "token": "demo-secret"})
    assert ok.status_code == 200, ok.text


def test_demo_voice_route_creates_preview_and_texts_link(monkeypatch):
    monkeypatch.setattr(settings, "demo_phone_number", "+17825550100")
    monkeypatch.setattr(settings, "app_env", "development")
    sent = {}

    def fake_send_sms(body, to_number, org_settings=None, *, from_number=None):
        sent.update({"body": body, "to": to_number, "from": from_number})
        return True, "sent", "SM_DEMO"

    monkeypatch.setattr(main_module, "send_sms_to", fake_send_sms)

    response = client.post(
        "/demo/voice/incoming",
        data={"From": "+17805550123", "To": "+17825550100", "CallSid": "CA_DEMO"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "I just texted you a link" in response.text
    assert sent["to"] == "+17805550123"
    assert sent["from"] == "+17825550100"
    assert "/demo?lead=" in sent["body"]

    demo_id = sent["body"].split("/demo?lead=")[1]
    saved = client.get(f"/api/demo/leads/{demo_id}")
    assert saved.status_code == 200
    assert saved.json()["source"] == "voice"


def test_book_demo_page_has_contact_form():
    response = client.get("/book-demo")
    assert response.status_code == 200
    assert 'action="/contact"' in response.text
    assert 'name="name"' in response.text
    assert 'name="company"' in response.text
    assert 'name="email"' in response.text
    assert 'name="phone"' in response.text
    assert 'name="business_type"' in response.text
    assert 'name="lead_source"' in response.text
    assert 'name="message"' in response.text
    assert "What lead source is leaking?" in response.text
    assert "Book my free audit" in response.text


def test_leads_require_auth():
    response = client.get("/leads")
    assert response.status_code == 401


def test_authenticated_page_shells_require_auth():
    for path in ["/review", "/analytics", "/pipeline", "/templates", "/setup", "/settings", "/admin"]:
        response = client.get(path)
        assert response.status_code == 401, path


def test_authenticated_page_shells_load_for_logged_in_user():
    c, _ = _auth_client()
    for path in ["/review", "/analytics", "/pipeline", "/templates", "/setup", "/settings"]:
        response = c.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith("text/html")


def test_admin_page_rejects_non_admin_user():
    c, _ = _auth_client()

    page = c.get("/admin")
    assert page.status_code == 403

    overview = c.get("/api/admin/overview")
    assert overview.status_code == 403


def test_platform_admin_overview_sees_cross_workspace_data():
    admin, _ = _auth_client(email="reesemedele@gmail.com")
    other, info = _auth_client()

    created = other.post(
        "/ingest-lead",
        json={
            "source": "website_form",
            "sender_name": "Cross Org Lead",
            "sender_email": "cross-org@example.com",
            "subject": "Need service",
            "body": "We need help with a leak and would like a quote.",
        },
    )
    assert created.status_code == 200, created.text

    page = admin.get("/admin")
    assert page.status_code == 200
    assert "Relinqo Admin" in page.text

    overview = admin.get("/api/admin/overview")
    assert overview.status_code == 200, overview.text
    data = overview.json()
    assert data["admin"]["email"] == "reesemedele@gmail.com"
    assert "reesemedele@gmail.com" in data["admin"]["allowed_emails"]
    assert any(org["slug"] == info["data"]["org"]["slug"] for org in data["orgs"])
    assert any(user["email"] == info["data"]["user"]["email"] for user in data["users"])
    assert any(lead["sender_email"] == "cross-org@example.com" for lead in data["recent_leads"])


def test_platform_admin_email_cannot_be_publicly_provisioned_in_production(monkeypatch):
    owner, info = _auth_client()
    monkeypatch.setattr(settings, "app_env", "production")

    register = client.post("/auth/register", json={
        "org_name": "Claim Admin",
        "email": "reesemedele@gmail.com",
        "password": "TestPass123",
        "display_name": "Not Reese",
    })
    assert register.status_code == 403

    invite = owner.post("/auth/invite", json={"email": "reesemedele@gmail.com"})
    assert invite.status_code == 403

    db = SessionLocal()
    try:
        token = "protected-admin-invite"
        db.add(InviteToken(
            org_id=info["data"]["org"]["id"],
            email="reesemedele@gmail.com",
            token=token,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        ))
        db.commit()
    finally:
        db.close()

    accept = client.post("/auth/accept-invite", json={"token": token, "password": "TestPass123"})
    assert accept.status_code == 403


def test_session_cookie_secure_only_in_production(monkeypatch):
    monkeypatch.setattr(main_module.settings, "app_env", "production")
    assert main_module._session_cookie_https_only() is True
    monkeypatch.setattr(main_module.settings, "app_env", "development")
    assert main_module._session_cookie_https_only() is False


def test_auth_login_register_flow():
    c, info = _auth_client()
    # /auth/me should return the logged-in user
    me = c.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == info["data"]["user"]["email"]

    # Logout clears session
    logout = c.post("/auth/logout")
    assert logout.status_code == 200

    # Login should be forgiving about email casing.
    mixed_case_login = c.post("/auth/login", json={
        "email": info["data"]["user"]["email"].upper(),
        "password": "TestPass123",
    })
    assert mixed_case_login.status_code == 200, mixed_case_login.text
    assert mixed_case_login.json()["user"]["email"] == info["data"]["user"]["email"]

    c.post("/auth/logout")

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
    needs_address = c.post(
        "/ingest-lead",
        json={
            "source": "gmail_api",
            "sender_name": "No Address Lead",
            "sender_email": "missing-address@example.com",
            "subject": "Need service but no address yet",
            "body": "Can someone call me about a repair?",
        },
    )
    assert needs_address.status_code == 200, needs_address.text

    response = c.get("/api/lead-map")
    assert response.status_code == 200
    items = response.json()["items"]
    mapped = [item for item in items if item["id"] == created.json()["id"]]
    assert mapped
    assert mapped[0]["lat"] == 53.5461
    assert mapped[0]["lng"] == -113.4938
    needs_location = response.json()["needs_location"]
    assert any(item["id"] == needs_address.json()["id"] for item in needs_location)


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
            "business_type": "Plumbing",
            "lead_source": "missed_calls",
            "current_response_time": "same_day",
            "message": "Need a walkthrough.",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/book-demo?status=success"


def test_contact_email_redirects_to_gmail_compose():
    response = client.get("/contact-email", follow_redirects=False)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://mail.google.com/mail/?")
    assert "view=cm" in location
    assert "to=reesemedele%40gmail.com" in location
    assert "relinqo+question" in location


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


def test_settings_response_coerces_legacy_nulls():
    org = Organization(name="Legacy Org", slug="legacy-null-org")
    user = User(email="owner@example.com", password_hash="x", role="owner", org=org)
    org_settings = OrgSettings(org_id=1)

    response = settings_routes._build_settings_response(user, org_settings)

    assert response.smtp_host == ""
    assert response.smtp_port == 587
    assert response.smtp_use_tls is True
    assert response.imap_host == "imap.gmail.com"
    assert response.imap_port == 993
    assert response.imap_mailbox == "INBOX"
    assert response.imap_search_criteria == "UNSEEN"
    assert response.scheduling_slot_duration == 60
    assert response.scheduling_buffer_minutes == 0
    assert response.scheduling_max_days_ahead == 7
    assert response.google_calendar_id == "primary"
    assert response.review_delay_hours == 72
    assert response.review_request_channel == "email"
    assert response.human_review is True
    assert response.automation_paused is False
    assert response.auto_send_confidence_threshold == 0.85
    assert response.default_timezone == "America/Edmonton"
    assert response.subscription_status == "trialing"
    assert response.plan == "beta"


def test_missing_org_settings_self_heals():
    import uuid

    unique = uuid.uuid4().hex[:8]
    db = SessionLocal()
    try:
        org = Organization(name=f"Missing Settings {unique}", slug=f"missing-settings-{unique}")
        db.add(org)
        db.flush()
        user = User(
            email=f"missing-settings-{unique}@example.com",
            password_hash="x",
            role="owner",
            org_id=org.id,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        assert db.query(OrgSettings).filter(OrgSettings.org_id == org.id).first() is None

        org_settings = auth_helpers.get_org_settings(user=user, db=db)

        assert org_settings.org_id == org.id
        assert org_settings.imap_host == "imap.gmail.com"
        assert db.query(OrgSettings).filter(OrgSettings.org_id == org.id).first() is not None
    finally:
        db.close()


def test_org_settings_schema_repair_adds_missing_columns(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE org_settings (id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL UNIQUE)"))

    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        ensure_org_settings_schema(db)
        columns = {column["name"] for column in inspect(engine).get_columns("org_settings")}
        assert "google_calendar_sync_enabled" in columns
        assert "review_request_enabled" in columns
        assert "outbound_webhook_events" in columns
        assert "default_timezone" in columns
    finally:
        db.close()
        engine.dispose()


def test_pilot_readiness_checklist_uses_live_workspace_state():
    c, info = _auth_client()
    first = c.get("/api/settings/readiness")
    assert first.status_code == 200
    items = {item["id"]: item for item in first.json()["items"]}
    assert items["business_profile"]["status"] == "missing"
    assert items["owner_alert_test"]["status"] == "missing"
    assert items["scheduling"]["required"] is False

    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.slug == info["data"]["org"]["slug"]).first()
        org.subscription_status = "trialing"
        org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org.id).first()
        org_settings.business_name = "Pilot Plumbing"
        org_settings.business_services = "plumbing, water heaters, drain cleaning"
        org_settings.business_area = "Edmonton"
        org_settings.business_hours = "Mon-Fri 8am-5pm"
        org_settings.google_oauth_email = "owner@example.com"
        org_settings.google_oauth_access_token = "access"
        org_settings.sms_alert_to_number = "+14035559999"
        org_settings.human_review = True
        org_settings.automation_paused = False
        db.add(PhoneNumber(
            org_id=org.id,
            twilio_sid=f"PN_READY_{org.id}",
            phone_number=f"+140355{org.id:06d}",
            is_active=True,
        ))
        db.add(PhoneRoutingRule(
            org_id=org.id,
            owner_phone="+14035559999",
            forwarding_setup_status="live",
        ))
        db.add(ScheduleAvailability(org_id=org.id, day_of_week=0, start_time="09:00", end_time="17:00", is_active=True))
        db.add(SmsNotification(
            org_id=org.id,
            direction="outbound",
            to_number="+14035559999",
            body="Owner alert",
            status="sent",
            purpose="owner_alert",
        ))
        db.commit()
    finally:
        db.close()

    ready = c.get("/api/settings/readiness")
    assert ready.status_code == 200
    data = ready.json()
    assert data["ready"] is True
    assert data["completed"] == data["total"]
    items = {item["id"]: item for item in data["items"]}
    assert items["business_profile"]["status"] == "ready"
    assert items["gmail"]["status"] == "ready"
    assert items["phone_rescue"]["status"] == "ready"
    assert items["owner_alert_test"]["status"] == "ready"
    assert items["scheduling"]["status"] == "missing"
    assert items["scheduling"]["required"] is False
    assert items["reviews"]["status"] == "warning"
    assert items["reviews"]["required"] is False


def test_outbound_webhook_test_endpoint_posts_signed_payload(monkeypatch):
    c, _ = _auth_client()
    captured = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.header_items())
        captured["body"] = req.data.decode("utf-8")
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(outbound_webhooks, "urlopen", fake_urlopen)

    saved = c.patch("/api/settings", json={
        "outbound_webhook_enabled": True,
        "outbound_webhook_url": "https://hooks.example.test/relinqo",
        "outbound_webhook_secret": "secret-123",
        "outbound_webhook_events": "lead.created,booking.created,lead.won",
    })
    assert saved.status_code == 200

    response = c.post("/api/settings/webhook/test")
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert captured["url"] == "https://hooks.example.test/relinqo"
    assert captured["headers"]["X-relinqo-event"] == "webhook.test"
    assert captured["headers"]["X-relinqo-signature"].startswith("sha256=")
    assert '"event":"webhook.test"' in captured["body"]


def test_lead_created_outbound_webhook_fires(monkeypatch):
    c, _ = _auth_client()
    events = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(req, timeout=0):
        events.append({
            "event": dict(req.header_items()).get("X-relinqo-event"),
            "body": req.data.decode("utf-8"),
        })
        return FakeResponse()

    monkeypatch.setattr(outbound_webhooks, "urlopen", fake_urlopen)
    c.patch("/api/settings", json={
        "outbound_webhook_enabled": True,
        "outbound_webhook_url": "https://hooks.example.test/relinqo",
        "outbound_webhook_events": "lead.created",
    })

    created = c.post("/ingest-lead", json={
        "source": "website_form",
        "sender_name": "Webhook Lead",
        "sender_email": "webhook@example.com",
        "subject": "Need plumbing help",
        "body": "We have a leak under the sink and need someone to call us.",
    })

    assert created.status_code == 200, created.text
    assert events
    assert events[0]["event"] == "lead.created"
    assert '"sender_email":"webhook@example.com"' in events[0]["body"]


def test_widget_embed_settings_and_script():
    c, info = _auth_client()

    widget = c.get("/api/settings/widget")
    assert widget.status_code == 200
    data = widget.json()
    assert data["workspace_slug"] == info["data"]["org"]["slug"]
    assert data["token"]
    assert 'data-relinqo-widget' in data["embed_code"]
    assert "/api/widget/embed.js" in data["embed_code"]

    script = c.get("/api/widget/embed.js")
    assert script.status_code == 200
    assert "application/javascript" in script.headers["content-type"]
    assert "data-relinqo-widget" in script.text
    assert "/api/public/widget/lead" in script.text


def test_public_widget_lead_creates_lead():
    c, info = _auth_client()
    widget = c.get("/api/settings/widget").json()
    payload = {
        "workspace": widget["workspace_slug"],
        "token": widget["token"],
        "name": "Website Customer",
        "email": "website.customer@example.com",
        "phone": "780-555-0199",
        "service": "plumbing",
        "message": "Kitchen sink is leaking and we need a quote this week.",
        "page_url": "https://example.test/contact",
    }

    created = c.post(
        "/api/public/widget/lead",
        content=json.dumps(payload),
        headers={"Content-Type": "text/plain", "Origin": "https://example.test"},
    )
    assert created.status_code == 200, created.text
    assert created.headers["access-control-allow-origin"] == "*"
    data = created.json()
    assert data["ok"] is True

    db = SessionLocal()
    try:
        lead = db.query(Lead).filter(Lead.id == data["lead_id"]).first()
        assert lead is not None
        assert lead.org_id == info["data"]["org"]["id"]
        assert lead.source == "website_widget"
        assert lead.sender_email == "website.customer@example.com"
        assert "780-555-0199" in lead.body
        assert "https://example.test/contact" in lead.body
    finally:
        db.close()


def test_public_widget_rejects_invalid_token_with_cors():
    c, _ = _auth_client()
    widget = c.get("/api/settings/widget").json()
    payload = {
        "workspace": widget["workspace_slug"],
        "token": "wrong-token",
        "email": "bad-token@example.com",
        "message": "This should not create a lead.",
    }
    response = c.post(
        "/api/public/widget/lead",
        content=json.dumps(payload),
        headers={"Content-Type": "text/plain", "Origin": "https://example.test"},
    )
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == "*"


def test_billing_status_defaults_to_configured_monthly_plan():
    c, _ = _auth_client()
    res = c.get("/api/billing/status")
    assert res.status_code == 200
    data = res.json()
    assert data["price"] == "USD 199/mo"
    assert data["active"] is True


def _create_trial_code(code: str, **overrides) -> None:
    db = SessionLocal()
    try:
        values = {
            "code": code,
            "description": "Audit qualified pilot",
            "max_redemptions": 1,
            "expires_at": datetime.now(timezone.utc) + timedelta(days=7),
            "trial_days": 14,
            "active": True,
            "source": "audit",
        }
        values.update(overrides)
        db.add(TrialCode(**values))
        db.commit()
    finally:
        db.close()


def test_register_with_valid_trial_code_starts_approval_first_pilot(monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_fake")
    monkeypatch.setattr(settings, "billing_enforced", True)
    _create_trial_code("AUDIT14", max_redemptions=3)

    c = TestClient(app)
    response = c.post("/auth/register", json={
        "org_name": "Pilot Plumbing",
        "email": "pilot-code@example.com",
        "password": "TestPass123",
        "display_name": "Pilot Owner",
        "trial_code": " audit14 ",
    })

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["org"]["subscription_status"] == "trialing"
    assert data["org"]["pilot_code"] == "AUDIT14"
    assert data["org"]["trial_days_left"] in {13, 14}

    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.slug == data["org"]["slug"]).one()
        user = db.query(User).filter(User.email == "pilot-code@example.com").one()
        org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org.id).one()
        code = db.query(TrialCode).filter(TrialCode.code == "AUDIT14").one()
        redemption = db.query(TrialCodeRedemption).filter(
            TrialCodeRedemption.trial_code_id == code.id,
            TrialCodeRedemption.org_id == org.id,
            TrialCodeRedemption.user_id == user.id,
        ).one()

        assert org.subscription_status == "trialing"
        assert org.plan == "founding_pilot"
        assert org.pilot_code == "AUDIT14"
        assert org.trial_started_at is not None
        assert org.trial_ends_at is not None
        assert 13 <= (org.trial_ends_at - org.trial_started_at).days <= 14
        assert org_settings.human_review is True
        assert code.redemption_count == 1
        assert redemption.redeemed_at is not None
    finally:
        db.close()


def test_register_rejects_invalid_expired_and_used_trial_codes(monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_fake")
    monkeypatch.setattr(settings, "billing_enforced", True)
    _create_trial_code("OLD14", expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    _create_trial_code("USED14", redemption_count=1, redeemed_at=datetime.now(timezone.utc))

    cases = [
        ("NOPE14", "This code does not exist."),
        ("OLD14", "This code has expired."),
        ("USED14", "This code has already been used."),
    ]

    for index, (code, message) in enumerate(cases):
        response = client.post("/auth/register", json={
            "org_name": f"Rejected Code {index}",
            "email": f"rejected-code-{index}@example.com",
            "password": "TestPass123",
            "display_name": "Rejected Owner",
            "trial_code": code,
        })
        assert response.status_code == 400
        assert response.json()["detail"] == message


def test_billing_status_reports_trial_window_and_blocks_expired_pilot(monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_fake")
    monkeypatch.setattr(settings, "billing_enforced", True)
    _create_trial_code("HVAC14")

    c = TestClient(app)
    created = c.post("/auth/register", json={
        "org_name": "HVAC Pilot",
        "email": "hvac-pilot@example.com",
        "password": "TestPass123",
        "display_name": "HVAC Owner",
        "trial_code": "hvac14",
    })
    assert created.status_code == 200, created.text

    status = c.get("/api/billing/status")
    assert status.status_code == 200
    active = status.json()
    assert active["trial_active"] is True
    assert active["trial_expired"] is False
    assert active["trial_days_left"] in {13, 14}
    assert active["trial_ends_at"] is not None

    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.slug == created.json()["org"]["slug"]).one()
        org.trial_ends_at = datetime.now(timezone.utc) - timedelta(hours=1)
        org.subscription_status = "trialing"
        db.commit()
    finally:
        db.close()

    expired = c.get("/api/billing/status")
    assert expired.status_code == 200
    expired_data = expired.json()
    assert expired_data["active"] is False
    assert expired_data["trial_active"] is False
    assert expired_data["trial_expired"] is True
    assert expired_data["pilot_state"] == "ended"

    blocked = c.get("/leads", headers={"accept": "application/json"})
    assert blocked.status_code == 402
    assert blocked.json()["billing_required"] is True


def test_billing_checkout_creates_stripe_session(monkeypatch):
    c, info = _auth_client()

    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.slug == info["data"]["org"]["slug"]).first()
        org.subscription_status = "incomplete"
        db.commit()
    finally:
        db.close()

    class FakeSession:
        id = "cs_test_123"
        url = "https://checkout.stripe.test/session"

    def fake_checkout(org, owner):
        org.stripe_customer_id = "cus_test_123"
        return FakeSession()

    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_fake")
    monkeypatch.setattr(settings, "billing_enforced", True)
    monkeypatch.setattr(billing_routes, "create_checkout_session", fake_checkout)

    res = c.post("/api/billing/checkout")
    assert res.status_code == 200, res.text
    assert res.json()["url"] == FakeSession.url

    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.slug == info["data"]["org"]["slug"]).first()
        assert org.stripe_customer_id == "cus_test_123"
        assert org.subscription_status == "incomplete"
        assert org.plan == "full_service"
    finally:
        db.close()


def test_billing_checkout_uses_absolute_public_base_url(monkeypatch):
    from types import SimpleNamespace

    from app import billing as billing_module
    from app.config import Settings

    captured = {}

    class FakeStripe:
        class Customer:
            @staticmethod
            def create(**kwargs):
                return SimpleNamespace(id="cus_test_123")

        class checkout:
            class Session:
                @staticmethod
                def create(**kwargs):
                    captured.update(kwargs)
                    return SimpleNamespace(id="cs_test_123", url="https://checkout.stripe.test/session")

    monkeypatch.setattr(billing_module, "_stripe", lambda: FakeStripe)
    monkeypatch.setattr(settings, "stripe_price_id", "")
    monkeypatch.setattr(settings, "stripe_price_amount_cents", 19900)
    monkeypatch.setattr(settings, "stripe_trial_period_days", 14)
    monkeypatch.setattr(
        settings,
        "public_base_url",
        Settings(public_base_url="leadrelay-production-4a37.up.railway.app").public_base_url,
    )

    org = Organization(id=123, name="LeadRelay", slug="leadrelay")
    owner = User(email="owner@example.com")

    billing_module.create_checkout_session(org, owner)

    assert captured["success_url"].startswith(
        "https://leadrelay-production-4a37.up.railway.app/settings?checkout=success"
    )
    assert captured["cancel_url"] == (
        "https://leadrelay-production-4a37.up.railway.app/settings?checkout=cancelled"
    )
    assert captured["line_items"][0]["price_data"]["unit_amount"] == 19900
    assert captured["line_items"][0]["price_data"]["recurring"] == {"interval": "month"}
    assert captured["subscription_data"]["trial_period_days"] == 14


def test_billing_gate_blocks_incomplete_workspace(monkeypatch):
    c, info = _auth_client()
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_fake")
    monkeypatch.setattr(settings, "billing_enforced", True)

    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.slug == info["data"]["org"]["slug"]).first()
        org.subscription_status = "incomplete"
        db.commit()
    finally:
        db.close()

    res = c.get("/leads", headers={"accept": "application/json"})
    assert res.status_code == 402
    assert res.json()["billing_required"] is True


def test_admin_billing_bypass_marks_workspace_active(monkeypatch):
    c, info = _auth_client()
    monkeypatch.setattr(settings, "billing_admin_token", "admin-test-token")
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_fake")
    monkeypatch.setattr(settings, "billing_enforced", True)

    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.slug == info["data"]["org"]["slug"]).first()
        org.subscription_status = "incomplete"
        db.commit()
    finally:
        db.close()

    res = client.post(
        "/api/billing/admin/bypass",
        headers={"X-Admin-Token": "admin-test-token"},
        json={"org_slug": info["data"]["org"]["slug"], "enabled": True, "reason": "pilot"},
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["active"] is True
    assert data["billing_exempt"] is True
    assert data["subscription_status"] == "active"

    phone_res = c.post("/api/phone/search", json={"area_code": "403"})
    assert phone_res.status_code != 402


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
        "friendly_name": "relinqo rescue line",
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
        return {"sid": "PN_TEST_124", "phone_number": phone, "friendly_name": "relinqo rescue line"}
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
        "friendly_name": "relinqo missed-call rescue",
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


def test_sms_status_callback_updates_owner_alert_and_phone_diagnostics():
    c, info = _auth_client()
    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.slug == info["data"]["org"]["slug"]).first()
        db.add(PhoneNumber(org_id=org.id, twilio_sid="PN_DIAG", phone_number="+14035550112", is_active=True))
        db.add(PhoneRoutingRule(org_id=org.id, owner_phone="+14035559999"))
        notification = SmsNotification(
            org_id=org.id,
            direction="outbound",
            to_number="+14035559999",
            body="Owner alert",
            twilio_message_sid="SM_DIAG_OWNER",
            status="sent",
            purpose="owner_alert",
        )
        db.add(notification)
        db.commit()
    finally:
        db.close()

    status = client.post(
        "/sms/status",
        data={
            "MessageSid": "SM_DIAG_OWNER",
            "MessageStatus": "undelivered",
            "ErrorCode": "30007",
            "ErrorMessage": "Carrier filtered message",
        },
    )
    assert status.status_code == 200

    phone = c.get("/api/phone/my-number")
    assert phone.status_code == 200
    owner_alert = phone.json()["rescue"]["last_owner_alert"]
    assert owner_alert["status"] == "failed"
    assert "30007" in owner_alert["error_message"]
