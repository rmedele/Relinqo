"""Smoke tests for the review-request scheduling + rendering pipeline.

Does NOT exercise actual email/SMS send — the run_due_review_requests path is
covered up to the point where it would call send_email/send_sms_to (no Twilio
or SMTP configured in the test env, so it records 'failed' which is fine for
asserting the lifecycle moves the row out of 'scheduled')."""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Lead, Organization, OrgSettings, ReviewRequest
from app.review_requests import (
    DEFAULT_REVIEW_BODY,
    render_review_body,
    run_due_review_requests,
    schedule_review_request,
)


def _seed_org(db: Session, *, enabled=True, review_url="https://g.page/r/test", delay=0):
    org = Organization(name="Test Co", slug=f"test-{datetime.now(timezone.utc).timestamp()}")
    db.add(org)
    db.commit()
    db.refresh(org)

    settings = OrgSettings(
        org_id=org.id,
        business_name="Test Co",
        review_request_enabled=enabled,
        review_url=review_url,
        review_delay_hours=delay,
        review_request_channel="email",
        review_request_subject="Quick favor",
        review_request_body=DEFAULT_REVIEW_BODY,
    )
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return org, settings


def _seed_lead(db: Session, org: Organization, *, name="Alex Customer", email="alex@example.com"):
    lead = Lead(
        org_id=org.id,
        source="test",
        sender_name=name,
        sender_email=email,
        subject="Furnace tune-up",
        body="Need a tune-up please.",
        status="sent",
        outcome="won",
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


def test_schedule_review_request_creates_row():
    db = SessionLocal()
    try:
        org, settings = _seed_org(db, delay=72)
        lead = _seed_lead(db, org)
        req = schedule_review_request(db, lead, settings)
        assert req is not None
        assert req.status == "scheduled"
        assert req.lead_id == lead.id
        # delay=72h -> scheduled_for is roughly 72h ahead. SQLite drops tz info
        # on round-trip; coerce to UTC if naive before comparing.
        scheduled = req.scheduled_for
        if scheduled.tzinfo is None:
            scheduled = scheduled.replace(tzinfo=timezone.utc)
        delta_hours = (scheduled - datetime.now(timezone.utc)).total_seconds() / 3600
        assert 71 < delta_hours < 73
    finally:
        db.close()


def test_schedule_review_request_idempotent():
    db = SessionLocal()
    try:
        org, settings = _seed_org(db)
        lead = _seed_lead(db, org)
        first = schedule_review_request(db, lead, settings)
        second = schedule_review_request(db, lead, settings)
        assert first.id == second.id
        count = db.query(ReviewRequest).filter(ReviewRequest.lead_id == lead.id).count()
        assert count == 1
    finally:
        db.close()


def test_schedule_review_request_skipped_when_disabled():
    db = SessionLocal()
    try:
        org, settings = _seed_org(db, enabled=False)
        lead = _seed_lead(db, org)
        assert schedule_review_request(db, lead, settings) is None
    finally:
        db.close()


def test_schedule_review_request_skipped_without_url():
    db = SessionLocal()
    try:
        org, settings = _seed_org(db, review_url="")
        lead = _seed_lead(db, org)
        assert schedule_review_request(db, lead, settings) is None
    finally:
        db.close()


def test_render_review_body_substitutes_variables():
    db = SessionLocal()
    try:
        org, settings = _seed_org(db)
        lead = _seed_lead(db, org, name="Megan Foster", email="m@example.com")
        rendered = render_review_body(settings.review_request_body, lead, settings)
        assert "Megan" in rendered
        assert "Test Co" in rendered
        assert settings.review_url in rendered
        assert "{{" not in rendered  # all placeholders consumed
    finally:
        db.close()


def test_run_due_processes_only_due_rows():
    db = SessionLocal()
    try:
        org, settings = _seed_org(db, delay=72)
        lead = _seed_lead(db, org)
        # Manually create a row that's already due, plus a future one.
        due_row = ReviewRequest(
            org_id=org.id, lead_id=lead.id,
            scheduled_for=datetime.now(timezone.utc) - timedelta(minutes=5),
            channel="email", status="scheduled",
        )
        future_row = ReviewRequest(
            org_id=org.id, lead_id=lead.id,
            scheduled_for=datetime.now(timezone.utc) + timedelta(hours=24),
            channel="email", status="scheduled",
        )
        db.add_all([due_row, future_row])
        db.commit()

        result = run_due_review_requests(db, org_id=org.id, org_settings=settings)
        assert result["processed"] == 1
        db.refresh(due_row)
        db.refresh(future_row)
        # Email isn't configured in tests, so the due row terminates as failed —
        # the important thing is it left 'scheduled'.
        assert due_row.status in {"sent", "failed"}
        assert future_row.status == "scheduled"
    finally:
        db.close()
