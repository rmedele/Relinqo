import hashlib
import hmac
import html
import secrets

from app.config import settings
from app.models import Organization


def widget_token(org: Organization) -> str:
    token_seed = org.api_key_hash or org.api_key or ""
    payload = f"widget:{org.id}:{org.slug}:{token_seed}".encode("utf-8")
    digest = hmac.new(settings.session_secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return digest[:32]


def verify_widget_token(org: Organization, token: str | None) -> bool:
    return secrets.compare_digest(token or "", widget_token(org))


def widget_embed_code(org: Organization) -> str:
    base = settings.public_base_url.rstrip("/")
    workspace = html.escape(org.slug, quote=True)
    token = html.escape(widget_token(org), quote=True)
    return (
        f'<div data-relinqo-widget data-workspace="{workspace}" data-token="{token}"></div>\n'
        f'<script src="{base}/api/widget/embed.js" async></script>'
    )
