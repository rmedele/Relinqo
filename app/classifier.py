import logging
import re
from typing import Any

from app.config import settings
from app.models import OrgSettings
from app.reply_generator import generate_reply
from app.schemas import ClassificationResult

logger = logging.getLogger(__name__)

SPAM_TERMS = {"seo", "backlinks", "crypto", "marketing package", "guest post", "casino"}
URGENT_TERMS = {"emergency", "urgent", "asap", "burst pipe", "no heat", "active leak", "flood"}
QUOTE_TERMS = {"quote", "estimate", "pricing", "consultation", "cost"}
EXISTING_CUSTOMER_TERMS = {"invoice", "existing customer", "follow-up", "follow up", "already a customer", "service issue"}


def classify_lead(
    payload: dict[str, Any],
    org_settings: OrgSettings | None = None,
    images: list[dict] | None = None,
    booking_url: str | None = None,
) -> ClassificationResult:
    """Classify a lead using AI when available, falling back to heuristics."""
    from app.ai import ai_available, classify_and_reply

    if ai_available():
        try:
            result = classify_and_reply(
                payload, org_settings=org_settings, images=images, booking_url=booking_url,
            )
            logger.info("AI classification: category=%s confidence=%.2f", result.category, result.confidence)
            return result
        except Exception:
            logger.exception("AI classification failed, falling back to heuristics")

    return _heuristic_classify(payload, booking_url=booking_url)


def classify_demo_lead(payload: dict[str, Any], booking_url: str | None = None) -> ClassificationResult:
    """Classify public demo leads without external AI calls or side effects."""
    return _heuristic_classify(payload, booking_url=booking_url)


def _heuristic_classify(payload: dict[str, Any], booking_url: str | None = None) -> ClassificationResult:
    """Keyword-based fallback classifier."""
    body = (payload.get("body") or "").lower()
    subject = (payload.get("subject") or "").lower()
    text = f"{subject}\n{body}"

    spam_score = sum(term in text for term in SPAM_TERMS)
    if spam_score:
        return ClassificationResult(
            category="spam",
            urgency_score=1,
            summary="Likely spam or promotional outreach.",
            recommended_reply="",
            owner_alert_needed=False,
            confidence=0.98,
            next_step="discard",
        )

    category = "general_inquiry"
    urgency_score = 2
    owner_alert_needed = False
    confidence = 0.72
    next_step = "review"

    if any(term in text for term in URGENT_TERMS):
        category = "urgent_request"
        urgency_score = 5
        owner_alert_needed = True
        confidence = 0.95
        next_step = "alert_owner_immediately"
    elif any(term in text for term in QUOTE_TERMS):
        category = "quote_request"
        urgency_score = 4
        owner_alert_needed = True
        confidence = 0.87
        next_step = "send_quote_reply_and_schedule_followup"
    elif any(term in text for term in EXISTING_CUSTOMER_TERMS):
        category = "existing_customer"
        urgency_score = 3
        owner_alert_needed = True
        confidence = 0.82
        next_step = "review_customer_context"

    summary = _summarize(payload)
    recommended_reply = generate_reply(category, payload.get("sender_name"), booking_url=booking_url) if category != "spam" else ""

    return ClassificationResult(
        category=category,
        urgency_score=urgency_score,
        summary=summary,
        recommended_reply=recommended_reply,
        owner_alert_needed=owner_alert_needed,
        confidence=confidence,
        next_step=next_step,
    )


def _summarize(payload: dict[str, Any]) -> str:
    body = re.sub(r"\s+", " ", payload.get("body", "")).strip()
    snippet = body[:220] + ("..." if len(body) > 220 else "")
    return f"Lead from {payload.get('sender_email')}: {snippet}"
