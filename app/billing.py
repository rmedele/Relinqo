from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.config import settings
from app.models import Organization, User


STRIPE_API_VERSION = "2026-02-25.clover"
ACTIVE_BILLING_STATUSES = {"active", "trialing"}


class BillingError(RuntimeError):
    pass


def billing_configured() -> bool:
    return bool(settings.stripe_secret_key)


def billing_enabled() -> bool:
    return settings.billing_enforced and billing_configured()


def new_org_subscription_status() -> str:
    return "incomplete" if billing_enabled() else "trialing"


def org_has_billing_access(org: Organization | None) -> bool:
    if not org:
        return False
    if getattr(org, "billing_exempt", False):
        return True
    return org.subscription_status in ACTIVE_BILLING_STATUSES


def price_display() -> str:
    amount = settings.stripe_price_amount_cents / 100
    currency = settings.stripe_price_currency.upper()
    return f"{currency} {amount:,.2f}/mo"


def _stripe():
    if not settings.stripe_secret_key:
        raise BillingError("Stripe is not configured")
    try:
        import stripe
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise BillingError("Stripe SDK is not installed") from exc

    stripe.api_key = settings.stripe_secret_key
    stripe.api_version = STRIPE_API_VERSION
    return stripe


def _stripe_value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _metadata_value(obj: Any, key: str) -> str | None:
    metadata = _stripe_value(obj, "metadata", {}) or {}
    if isinstance(metadata, dict):
        return metadata.get(key)
    return getattr(metadata, key, None)


def _timestamp_to_datetime(value: int | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)


def _line_item() -> dict[str, Any]:
    if settings.stripe_price_id:
        return {"price": settings.stripe_price_id, "quantity": 1}
    return {
        "price_data": {
            "currency": settings.stripe_price_currency.lower(),
            "product_data": {"name": settings.stripe_product_name},
            "recurring": {"interval": "month"},
            "unit_amount": settings.stripe_price_amount_cents,
        },
        "quantity": 1,
    }


def create_checkout_session(org: Organization, owner: User) -> Any:
    stripe = _stripe()
    base_url = settings.public_base_url.rstrip("/")

    if not org.stripe_customer_id:
        customer = stripe.Customer.create(
            email=owner.email,
            name=org.name,
            metadata={"org_id": str(org.id), "org_slug": org.slug},
        )
        org.stripe_customer_id = customer.id

    return stripe.checkout.Session.create(
        mode="subscription",
        customer=org.stripe_customer_id,
        client_reference_id=str(org.id),
        line_items=[_line_item()],
        success_url=f"{base_url}/settings?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{base_url}/settings?checkout=cancelled",
        subscription_data={"metadata": {"org_id": str(org.id), "org_slug": org.slug}},
        metadata={"org_id": str(org.id), "org_slug": org.slug},
    )


def create_customer_portal_session(org: Organization) -> Any:
    stripe = _stripe()
    if not org.stripe_customer_id:
        raise BillingError("No Stripe customer is linked to this workspace")
    return stripe.billing_portal.Session.create(
        customer=org.stripe_customer_id,
        return_url=f"{settings.public_base_url.rstrip('/')}/settings?billing=portal-return",
    )


def construct_webhook_event(payload: bytes, signature: str | None) -> Any:
    if not settings.stripe_webhook_secret:
        raise BillingError("Stripe webhook secret is not configured")
    if not signature:
        raise BillingError("Missing Stripe signature")
    stripe = _stripe()
    return stripe.Webhook.construct_event(payload, signature, settings.stripe_webhook_secret)


def org_id_from_stripe_object(obj: Any) -> int | None:
    raw = _metadata_value(obj, "org_id") or _stripe_value(obj, "client_reference_id")
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def apply_subscription_to_org(org: Organization, subscription: Any) -> None:
    org.stripe_subscription_id = _stripe_value(subscription, "id") or org.stripe_subscription_id
    org.stripe_customer_id = _stripe_value(subscription, "customer") or org.stripe_customer_id
    org.subscription_status = _stripe_value(subscription, "status") or org.subscription_status
    org.subscription_current_period_end = _timestamp_to_datetime(
        _stripe_value(subscription, "current_period_end")
    )
    org.subscription_cancel_at_period_end = bool(
        _stripe_value(subscription, "cancel_at_period_end", False)
    )
    if org.plan in {"", "beta"}:
        org.plan = "full_service"


def apply_checkout_session_to_org(org: Organization, session: Any) -> None:
    org.stripe_customer_id = _stripe_value(session, "customer") or org.stripe_customer_id
    org.stripe_subscription_id = _stripe_value(session, "subscription") or org.stripe_subscription_id
    if org.stripe_subscription_id:
        org.subscription_status = "active"
    if org.plan in {"", "beta"}:
        org.plan = "full_service"
