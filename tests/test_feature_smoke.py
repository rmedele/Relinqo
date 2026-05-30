import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app import outbound_webhooks
from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import Lead, Organization, PhoneNumber
from app.routes import leads as lead_routes
from app.routes import scheduling as scheduling_routes


@pytest.fixture(autouse=True)
def _disable_external_ai(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "none")
    monkeypatch.setattr(settings, "llm_api_key", "")


def _auth_client():
    unique = uuid4().hex[:10]
    client = TestClient(app)
    response = client.post(
        "/auth/register",
        json={
            "org_name": f"Smoke Org {unique}",
            "email": f"smoke-{unique}@example.com",
            "password": "TestPass123",
            "display_name": "Smoke User",
        },
    )
    assert response.status_code == 200, response.text
    return client, response.json()


def _create_lead(client, **overrides):
    unique = uuid4().hex[:8]
    payload = {
        "source": "smoke_test",
        "sender_name": "Taylor Customer",
        "sender_email": f"taylor-{unique}@example.com",
        "subject": "Need plumbing help",
        "body": "The kitchen sink is leaking and I need a quote in Edmonton.",
    }
    payload.update(overrides)
    response = client.post("/ingest-lead", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_reply_template_crud_use_and_delete_smoke():
    client, _ = _auth_client()

    created = client.post(
        "/api/templates",
        json={
            "name": "Schedule visit",
            "body": "Hi {{name}}, we can help. Book here: {{business}}",
            "category": "quote_request",
            "sort_order": 3,
        },
    )
    assert created.status_code == 200, created.text
    template_id = created.json()["id"]

    used = client.post(f"/api/templates/{template_id}/use")
    assert used.status_code == 200
    assert used.json()["use_count"] == 1

    updated = client.patch(
        f"/api/templates/{template_id}",
        json={"name": "Schedule inspection", "sort_order": 1},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Schedule inspection"

    listed = client.get("/api/templates")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [template_id]

    deleted = client.delete(f"/api/templates/{template_id}")
    assert deleted.status_code == 200
    assert client.get("/api/templates").json() == []


def test_pipeline_groups_deals_totals_tags_and_starred_smoke():
    client, _ = _auth_client()
    lead = _create_lead(client, sender_email=f"pipeline-{uuid4().hex}@example.com")

    updated = client.patch(
        f"/leads/{lead['id']}",
        json={
            "pipeline_stage": "quoted",
            "deal_value": 1250.50,
            "tags": "water-heater,priority",
            "starred": True,
        },
    )
    assert updated.status_code == 200, updated.text

    board = client.get("/api/pipeline")
    assert board.status_code == 200
    data = board.json()
    assert data["counts"]["quoted"] >= 1
    assert data["totals"]["quoted"] >= 1250.50
    card = next(item for item in data["columns"]["quoted"] if item["id"] == lead["id"])
    assert card["starred"] is True
    assert "priority" in card["tags"]


def test_internal_notes_pin_update_and_delete_smoke():
    client, _ = _auth_client()
    lead = _create_lead(client, sender_email=f"notes-{uuid4().hex}@example.com")

    note = client.post(
        f"/leads/{lead['id']}/notes",
        json={"body": "Customer prefers a morning callback.", "pinned": True},
    )
    assert note.status_code == 200, note.text
    note_id = note.json()["id"]

    notes = client.get(f"/leads/{lead['id']}/notes")
    assert notes.status_code == 200
    assert notes.json()[0]["pinned"] is True

    updated = client.patch(
        f"/leads/{lead['id']}/notes/{note_id}",
        json={"body": "Customer prefers tomorrow morning.", "pinned": False},
    )
    assert updated.status_code == 200
    assert updated.json()["pinned"] is False

    deleted = client.delete(f"/leads/{lead['id']}/notes/{note_id}")
    assert deleted.status_code == 200
    assert client.get(f"/leads/{lead['id']}/notes").json() == []


def test_schedule_booking_loop_blocks_booked_slot_smoke(monkeypatch):
    client, _ = _auth_client()
    monkeypatch.setattr(scheduling_routes, "_send_booking_notifications", lambda *args, **kwargs: None)
    monkeypatch.setattr(scheduling_routes, "dispatch_booking_created", lambda *args, **kwargs: None)

    settings = client.patch(
        "/api/settings",
        json={
            "business_name": "Smoke Plumbing",
            "scheduling_enabled": True,
            "scheduling_slot_duration": 60,
            "scheduling_buffer_minutes": 0,
            "scheduling_max_days_ahead": 7,
        },
    )
    assert settings.status_code == 200, settings.text

    availability = [
        {"day_of_week": day, "start_time": "09:00", "end_time": "11:00", "is_active": True}
        for day in range(7)
    ]
    saved = client.put("/api/schedule/availability", json=availability)
    assert saved.status_code == 200, saved.text
    assert len(saved.json()) == 7

    lead = _create_lead(client, sender_email=f"booking-{uuid4().hex}@example.com")
    assert lead["booking_token"]

    info = client.get(f"/api/public/book/{lead['booking_token']}/info")
    assert info.status_code == 200
    assert info.json()["business_name"] == "Smoke Plumbing"

    slots = client.get(f"/api/public/book/{lead['booking_token']}/slots")
    assert slots.status_code == 200
    assert slots.json(), "expected at least one generated appointment slot"
    chosen = slots.json()[0]["slot_start"]

    booked = client.post(
        f"/api/public/book/{lead['booking_token']}",
        json={
            "customer_name": "Taylor Customer",
            "customer_email": "taylor.booking@example.com",
            "customer_phone": "780-555-0100",
            "customer_notes": "Please knock loudly.",
            "slot_start": chosen,
        },
    )
    assert booked.status_code == 200, booked.text

    after_booking = client.get(f"/api/public/book/{lead['booking_token']}/slots")
    assert after_booking.status_code == 200
    assert chosen not in [item["slot_start"] for item in after_booking.json()]

    cancelled = client.patch(
        f"/api/schedule/bookings/{booked.json()['id']}?status=cancelled"
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_schedule_slots_use_workspace_timezone(monkeypatch):
    client, _ = _auth_client()
    monkeypatch.setattr(scheduling_routes, "dispatch_booking_created", lambda *args, **kwargs: None)

    saved_settings = client.patch(
        "/api/settings",
        json={
            "business_name": "Timezone Plumbing",
            "default_timezone": "America/Edmonton",
            "scheduling_enabled": True,
            "scheduling_slot_duration": 60,
            "scheduling_buffer_minutes": 0,
            "scheduling_max_days_ahead": 7,
        },
    )
    assert saved_settings.status_code == 200, saved_settings.text

    availability = [
        {"day_of_week": day, "start_time": "09:00", "end_time": "10:00", "is_active": True}
        for day in range(7)
    ]
    assert client.put("/api/schedule/availability", json=availability).status_code == 200

    lead = _create_lead(client, sender_email=f"timezone-{uuid4().hex}@example.com")
    info = client.get(f"/api/public/book/{lead['booking_token']}/info")
    assert info.status_code == 200
    assert info.json()["timezone"] == "America/Edmonton"

    slots = client.get(f"/api/public/book/{lead['booking_token']}/slots")
    assert slots.status_code == 200
    assert slots.json()

    first_start = datetime.fromisoformat(slots.json()[0]["slot_start"].replace("Z", "+00:00"))
    local_start = first_start.astimezone(ZoneInfo("America/Edmonton"))
    assert (local_start.hour, local_start.minute) == (9, 0)


def test_booking_created_webhook_includes_customer_notes(monkeypatch):
    client, _ = _auth_client()
    monkeypatch.setattr(scheduling_routes, "_send_booking_notifications", lambda *args, **kwargs: None)
    captured = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(req, timeout=0):
        captured.append(json.loads(req.data.decode("utf-8")))
        return FakeResponse()

    monkeypatch.setattr(outbound_webhooks, "urlopen", fake_urlopen)

    saved_settings = client.patch(
        "/api/settings",
        json={
            "business_name": "Webhook Plumbing",
            "scheduling_enabled": True,
            "scheduling_slot_duration": 60,
            "scheduling_buffer_minutes": 0,
            "scheduling_max_days_ahead": 7,
            "outbound_webhook_enabled": True,
            "outbound_webhook_url": "https://hooks.example.test/relinqo",
            "outbound_webhook_events": "booking.created",
        },
    )
    assert saved_settings.status_code == 200, saved_settings.text

    availability = [
        {"day_of_week": day, "start_time": "09:00", "end_time": "11:00", "is_active": True}
        for day in range(7)
    ]
    assert client.put("/api/schedule/availability", json=availability).status_code == 200

    lead = _create_lead(client, sender_email=f"booking-webhook-{uuid4().hex}@example.com")
    slots = client.get(f"/api/public/book/{lead['booking_token']}/slots")
    assert slots.status_code == 200
    chosen = slots.json()[0]["slot_start"]

    booked = client.post(
        f"/api/public/book/{lead['booking_token']}",
        json={
            "customer_name": "Taylor Customer",
            "customer_email": "taylor.webhook@example.com",
            "customer_phone": "780-555-0100",
            "customer_notes": "Please knock loudly.",
            "slot_start": chosen,
        },
    )
    assert booked.status_code == 200, booked.text
    assert captured
    assert captured[0]["event"] == "booking.created"
    assert captured[0]["data"]["notes"] == "Please knock loudly."
    assert captured[0]["data"]["customer_notes"] == "Please knock loudly."


def test_schedule_availability_rejects_invalid_window_smoke():
    client, _ = _auth_client()
    response = client.put(
        "/api/schedule/availability",
        json=[{"day_of_week": 1, "start_time": "17:00", "end_time": "09:00", "is_active": True}],
    )
    assert response.status_code == 400
    assert "start_time must be before end_time" in response.text


def test_manual_review_request_queue_list_and_cancel_smoke():
    client, _ = _auth_client()
    settings = client.patch(
        "/api/settings",
        json={
            "review_request_enabled": True,
            "review_url": "https://g.page/r/smoke-review",
            "review_delay_hours": 24,
            "review_request_channel": "email",
        },
    )
    assert settings.status_code == 200
    lead = _create_lead(client, sender_email=f"review-{uuid4().hex}@example.com")

    queued = client.post(f"/leads/{lead['id']}/review-request")
    assert queued.status_code == 200, queued.text
    request_id = queued.json()["review_request_id"]

    listed = client.get("/api/review-requests?status=scheduled")
    assert listed.status_code == 200
    assert request_id in [item["id"] for item in listed.json()["items"]]

    cancelled = client.delete(f"/api/review-requests/{request_id}")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_customer_reply_threads_are_grouped_smoke():
    client, _ = _auth_client()
    email = f"thread-{uuid4().hex}@example.com"
    first = _create_lead(client, sender_email=email, subject="Need furnace help")
    reply = _create_lead(client, sender_email=email, subject="Re: Need furnace help")

    threads = client.get("/threads")
    assert threads.status_code == 200
    matching = [
        items for items in threads.json()["threads"].values()
        if any(item["id"] == first["id"] for item in items)
    ]
    assert matching
    assert any(item["id"] == reply["id"] for item in matching[0])

    detail = client.get(f"/threads/{first['thread_id']}")
    assert detail.status_code == 200
    assert detail.json()["message_count"] == 2


def test_lead_map_backfill_updates_missing_coordinates_smoke(monkeypatch):
    client, _ = _auth_client()
    client.patch("/api/settings", json={"business_area": "Edmonton"})
    lead = _create_lead(
        client,
        sender_email=f"mapfill-{uuid4().hex}@example.com",
        body="Need service at 123 Jasper Ave.",
    )

    db = SessionLocal()
    try:
        row = db.query(Lead).filter(Lead.id == lead["id"]).first()
        row.location = "123 Jasper Ave"
        row.latitude = None
        row.longitude = None
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(lead_routes, "geocode_location", lambda value: (53.5461, -113.4938))
    monkeypatch.setattr(lead_routes, "polite_geocode_delay", lambda: None)

    response = client.post("/api/lead-map/backfill?limit=10")
    assert response.status_code == 200, response.text
    assert lead["id"] in response.json()["lead_ids"]

    mapped = client.get("/api/lead-map")
    assert mapped.status_code == 200
    item = next(item for item in mapped.json()["items"] if item["id"] == lead["id"])
    assert item["lat"] == 53.5461
    assert item["lng"] == -113.4938


def test_won_deal_updates_revenue_stats_and_review_queue_smoke():
    client, _ = _auth_client()
    client.patch(
        "/api/settings",
        json={"review_request_enabled": True, "review_url": "https://g.page/r/won-smoke", "review_delay_hours": 0},
    )
    lead = _create_lead(client, sender_email=f"won-{uuid4().hex}@example.com")
    client.patch(f"/leads/{lead['id']}", json={"deal_value": 900.0})

    outcome = client.post(
        f"/leads/{lead['id']}/outcome",
        json={"outcome": "won", "outcome_notes": "Booked and paid."},
    )
    assert outcome.status_code == 200, outcome.text
    assert outcome.json()["pipeline_stage"] == "won"

    stats = client.get("/stats")
    assert stats.status_code == 200
    assert stats.json()["won_revenue"] >= 900.0

    revenue = client.get("/stats/revenue?days=7")
    assert revenue.status_code == 200
    assert revenue.json()["total_won"] >= 900.0

    reviews = client.get("/api/review-requests?status=scheduled")
    assert reviews.status_code == 200
    assert any(item["lead_id"] == lead["id"] for item in reviews.json()["items"])


def test_regenerate_api_key_rotates_forwarded_email_secret_smoke():
    client, info = _auth_client()
    old_key = info["org"]["api_key"]

    rotated = client.post("/auth/regenerate-api-key")
    assert rotated.status_code == 200, rotated.text
    new_key = rotated.json()["api_key"]
    assert new_key != old_key

    client.patch("/api/settings", json={"forwarding_token": "smoke-forward-token"})
    response = TestClient(app).post(
        "/forwarded-email",
        headers={"X-API-Key": new_key},
        json={
            "token": "smoke-forward-token",
            "from_email": f"forward-{uuid4().hex}@example.com",
            "body": "Need a quote for a leaking sink.",
        },
    )
    assert response.status_code == 200, response.text


def test_phone_routing_normalizes_owner_phone_and_my_number_smoke():
    client, info = _auth_client()
    response = client.post("/api/phone/routing", json={"owner_phone": "(403) 555-9999"})
    assert response.status_code == 200, response.text
    assert response.json()["owner_phone"] == "+14035559999"

    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.slug == info["org"]["slug"]).first()
        db.add(
            PhoneNumber(
                org_id=org.id,
                twilio_sid=f"PN_SMOKE_{uuid4().hex[:12]}",
                phone_number=f"+1403{org.id % 10000000:07d}",
                is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()

    status = client.get("/api/phone/my-number")
    assert status.status_code == 200
    assert status.json()["routing"]["owner_phone"] == "+14035559999"


def test_cancel_send_clears_pending_reply_smoke():
    client, info = _auth_client()
    lead = _create_lead(client, sender_email=f"cancel-{uuid4().hex}@example.com")

    db = SessionLocal()
    try:
        org = db.query(Organization).filter(Organization.slug == info["org"]["slug"]).first()
        row = db.query(Lead).filter(Lead.id == lead["id"], Lead.org_id == org.id).first()
        row.status = "pending_send"
        row.send_at = datetime.now(timezone.utc) + timedelta(seconds=45)
        db.commit()
    finally:
        db.close()

    cancelled = client.post(f"/leads/{lead['id']}/cancel-send")
    assert cancelled.status_code == 200
    assert cancelled.json()["ok"] is True

    reloaded = client.get(f"/leads/{lead['id']}")
    assert reloaded.status_code == 200
    assert reloaded.json()["status"] == "drafted"
    assert reloaded.json()["send_at"] is None
