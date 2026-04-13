from fastapi.testclient import TestClient

from app.main import app
from app.routes import leads as lead_routes

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
