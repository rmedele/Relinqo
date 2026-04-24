"""Twilio webhook signature validation.

Implements the algorithm described at
https://www.twilio.com/docs/usage/webhooks/webhooks-security without
pulling the full twilio-python SDK as a dependency.
"""
import base64
import hashlib
import hmac
import logging

from fastapi import Header, HTTPException, Request

from app.config import settings
from app.database import SessionLocal
from app.models import OrgSettings

logger = logging.getLogger(__name__)


def _compute_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """Twilio signs the URL concatenated with sorted param key+value pairs."""
    payload = url
    for key in sorted(params.keys()):
        payload += key + params[key]
    digest = hmac.new(
        auth_token.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def _canonical_url(request: Request) -> str:
    """Build the URL Twilio signed against.

    Railway terminates TLS upstream, so request.url.scheme is often "http"
    even though Twilio hit the public "https" URL. Rebuild from
    PUBLIC_BASE_URL when configured to avoid a perpetual 403.
    """
    base = (settings.public_base_url or "").rstrip("/")
    if base:
        # Preserve query string if present (Twilio includes it in the signed URL)
        qs = request.url.query
        suffix = f"?{qs}" if qs else ""
        return f"{base}{request.url.path}{suffix}"
    return str(request.url)


def _auth_token_for_request() -> str | None:
    """Resolve the Twilio auth token to verify the signature against.

    For V1 we use a single platform-level token. When multi-org BYO-Twilio
    returns we'll need to dispatch per-org based on the `To` number, but
    that lookup happens after signature validation — chicken/egg. The
    platform token is authoritative here.
    """
    if settings.twilio_auth_token:
        return settings.twilio_auth_token

    # Fallback: during early development the token may only live in the
    # default org's OrgSettings row. Pull it from there.
    db = SessionLocal()
    try:
        row = db.query(OrgSettings).filter(OrgSettings.org_id == 1).first()
        if row and row.twilio_auth_token:
            return row.twilio_auth_token
    finally:
        db.close()
    return None


async def verify_twilio_signature(
    request: Request,
    x_twilio_signature: str | None = Header(default=None, alias="X-Twilio-Signature"),
) -> None:
    """FastAPI dependency that rejects requests with bad Twilio signatures.

    Skipped entirely in development so ngrok/curl testing isn't blocked.
    Set TWILIO_SKIP_SIGNATURE_CHECK=true to bypass in staging for debugging.
    """
    if settings.app_env != "production":
        return

    if not x_twilio_signature:
        logger.warning("Twilio webhook missing X-Twilio-Signature header")
        raise HTTPException(status_code=403, detail="Missing signature")

    auth_token = _auth_token_for_request()
    if not auth_token:
        logger.error("No Twilio auth token configured — cannot validate signature")
        raise HTTPException(status_code=503, detail="Twilio not configured")

    # Twilio signs URL + sorted form params for POST webhooks
    form = await request.form()
    params = {k: str(v) for k, v in form.items()}
    url = _canonical_url(request)

    expected = _compute_signature(auth_token, url, params)
    if not hmac.compare_digest(expected, x_twilio_signature):
        logger.warning(
            "Twilio signature mismatch path=%s (expected_prefix=%s got_prefix=%s)",
            request.url.path,
            expected[:8],
            x_twilio_signature[:8],
        )
        raise HTTPException(status_code=403, detail="Invalid signature")
