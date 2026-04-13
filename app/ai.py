"""AI-powered lead classification and reply generation using Claude."""

import base64
import json
import logging
from typing import Any

import anthropic

from app.config import settings
from app.models import OrgSettings
from app.schemas import ClassificationResult

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.llm_api_key)
    return _client


def ai_available() -> bool:
    return settings.llm_provider == "anthropic" and bool(settings.llm_api_key)


def _business_context(org_settings: OrgSettings | None = None) -> str:
    parts = []
    if org_settings:
        if org_settings.business_name:
            parts.append(f"Business: {org_settings.business_name}")
        if org_settings.business_services:
            parts.append(f"Services offered: {org_settings.business_services}")
        if org_settings.business_area:
            parts.append(f"Service area: {org_settings.business_area}")
        if org_settings.business_hours:
            parts.append(f"Hours: {org_settings.business_hours}")
        if org_settings.business_phone:
            parts.append(f"Phone: {org_settings.business_phone}")
        if org_settings.business_tone:
            parts.append(f"Tone: {org_settings.business_tone}")
    else:
        if settings.business_name:
            parts.append(f"Business: {settings.business_name}")
        if settings.business_services:
            parts.append(f"Services offered: {settings.business_services}")
        if settings.business_area:
            parts.append(f"Service area: {settings.business_area}")
        if settings.business_hours:
            parts.append(f"Hours: {settings.business_hours}")
        if settings.business_phone:
            parts.append(f"Phone: {settings.business_phone}")
        if settings.business_tone:
            parts.append(f"Tone: {settings.business_tone}")
    return "\n".join(parts) if parts else "No business profile configured."


CLASSIFY_AND_REPLY_PROMPT = """\
You are an AI assistant managing the inbox for a local home service business. Your job is to classify incoming leads and draft a reply on behalf of the business.

{business_context}

## Instructions

Analyze the incoming message and return a JSON object with these fields:

- "category": one of "urgent_request", "quote_request", "general_inquiry", "existing_customer", "spam"
- "urgency_score": integer 1-5 (1=low, 5=emergency)
- "summary": one sentence describing what the customer needs
- "recommended_reply": a personalized reply from the business to the customer (see Reply Rules below)
- "owner_alert_needed": boolean — true if the business owner should be notified immediately
- "confidence": float 0.0-1.0 — how confident you are in the classification
- "next_step": what the business should do next (e.g. "schedule_estimate", "dispatch_tech", "review", "discard")
- "extracted_phone": phone number if found in the message, else null
- "extracted_location": location/address if found, else null

## Classification Rules

- **urgent_request**: Active emergencies — burst pipes, no heat in winter, gas smells, active leaks, flooding, electrical hazards. These need immediate owner attention.
- **quote_request**: Customer wants pricing, an estimate, or to schedule work. Not an emergency but high intent.
- **general_inquiry**: Questions, information requests, or vague messages that need clarification.
- **existing_customer**: References a previous job, invoice, appointment, or indicates they are a returning customer.
- **spam**: Marketing, SEO pitches, unsolicited vendor outreach, phishing, or irrelevant messages.

## Reply Rules

- For spam: set recommended_reply to an empty string. Do not reply to spam.
- For all other categories: write a short, warm, helpful reply as if you are a member of the business team.
- Reference the customer's specific situation (don't be generic).
- If the business name is configured, sign off as the business.
- Keep it under 4 sentences.
- For urgent requests: acknowledge the urgency, let them know the owner has been alerted, ask for address/callback if not provided.
- For quote requests: confirm you received the request, mention what info would help (photos, address, timeline).
- For general inquiries: thank them, ask a clarifying question to move things forward.
- For existing customers: acknowledge the relationship, confirm you'll look into it.
- Never promise specific pricing, timelines, or commit to appointments — the owner handles that.
- If a reply signature is configured, append it.
- If a booking link is provided in the message context, include it naturally in non-spam replies (e.g., "You can pick a time that works for you here: [link]").

## Photo Analysis (if photos are attached)

If the message includes photos:
- Describe what you see in each photo (damage type, severity, affected area, condition).
- Factor the visual evidence into your urgency_score — visible water damage, structural issues, or safety hazards should increase urgency.
- Include a "photo_analysis" field in your JSON: a brief description of what the photos show and their implications for service priority.
- Reference the photos in your recommended_reply (e.g., "Based on the photos you shared, we can see...").
- If no photos are attached, omit the "photo_analysis" field or set it to null.

Return ONLY the JSON object, no markdown fencing or extra text.
"""


def classify_and_reply(
    payload: dict[str, Any],
    org_settings: OrgSettings | None = None,
    images: list[dict] | None = None,
    booking_url: str | None = None,
) -> ClassificationResult:
    client = _get_client()
    business_context = _business_context(org_settings)

    sender_name = payload.get("sender_name") or "Unknown"
    sender_email = payload.get("sender_email") or "Unknown"
    subject = payload.get("subject") or "(no subject)"
    body = payload.get("body") or "(empty)"

    user_text = (
        f"From: {sender_name} <{sender_email}>\n"
        f"Subject: {subject}\n"
        f"Source: {payload.get('source', 'unknown')}\n"
    )
    if booking_url:
        user_text += f"Booking link: {booking_url}\n"
    user_text += f"\n{body}"

    # Build content blocks — text + optional images
    content: list[dict] = [{"type": "text", "text": user_text}]
    if images:
        for img in images:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img["mime_type"],
                    "data": base64.b64encode(img["data"]).decode(),
                },
            })

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        system=CLASSIFY_AND_REPLY_PROMPT.format(business_context=business_context),
        messages=[{"role": "user", "content": content}],
    )

    raw = response.content[0].text.strip()
    # Strip markdown fencing if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3].strip()

    data = json.loads(raw)

    # Append signature if configured
    reply = data.get("recommended_reply", "")
    signature = (org_settings.business_reply_signature if org_settings else settings.business_reply_signature)
    if reply and signature:
        reply = f"{reply}\n\n{signature}"

    return ClassificationResult(
        category=data["category"],
        urgency_score=data["urgency_score"],
        summary=data["summary"],
        recommended_reply=reply,
        owner_alert_needed=data["owner_alert_needed"],
        confidence=data["confidence"],
        next_step=data["next_step"],
        extracted_phone=data.get("extracted_phone"),
        extracted_location=data.get("extracted_location"),
        photo_analysis=data.get("photo_analysis"),
    )
