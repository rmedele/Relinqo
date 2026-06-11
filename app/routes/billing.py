import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_owner
from app.billing import (
    BillingError,
    apply_checkout_session_to_org,
    apply_subscription_to_org,
    billing_configured,
    billing_enabled,
    construct_webhook_event,
    create_checkout_session,
    create_customer_portal_session,
    org_has_billing_access,
    org_id_from_stripe_object,
    price_display,
)
from app.config import settings
from app.database import get_db
from app.models import Organization, User
from app.trial_codes import pilot_state, trial_days_left, trial_expired, has_active_pilot


router = APIRouter(prefix="/api/billing", tags=["billing"])
webhook_router = APIRouter(tags=["billing"])


class BillingStatusResponse(BaseModel):
    billing_enabled: bool
    billing_configured: bool
    active: bool
    subscription_status: str
    plan: str
    price: str
    billing_exempt: bool
    billing_exempt_reason: str
    stripe_customer_linked: bool
    stripe_subscription_linked: bool
    subscription_current_period_end: datetime | None
    subscription_cancel_at_period_end: bool
    pilot_code: str
    trial_started_at: datetime | None
    trial_ends_at: datetime | None
    trial_days_left: int | None
    trial_active: bool
    trial_expired: bool
    pilot_state: str


class BillingBypassRequest(BaseModel):
    org_id: int | None = None
    org_slug: str | None = None
    owner_email: str | None = None
    enabled: bool = True
    reason: str | None = None


def _status_response(org: Organization) -> BillingStatusResponse:
    return BillingStatusResponse(
        billing_enabled=billing_enabled(),
        billing_configured=billing_configured(),
        active=org_has_billing_access(org),
        subscription_status=org.subscription_status,
        plan=org.plan,
        price=price_display(),
        billing_exempt=bool(org.billing_exempt),
        billing_exempt_reason=org.billing_exempt_reason or "",
        stripe_customer_linked=bool(org.stripe_customer_id),
        stripe_subscription_linked=bool(org.stripe_subscription_id),
        subscription_current_period_end=org.subscription_current_period_end,
        subscription_cancel_at_period_end=bool(org.subscription_cancel_at_period_end),
        pilot_code=org.pilot_code or "",
        trial_started_at=org.trial_started_at,
        trial_ends_at=org.trial_ends_at,
        trial_days_left=trial_days_left(org),
        trial_active=has_active_pilot(org),
        trial_expired=trial_expired(org),
        pilot_state=pilot_state(org),
    )


def _find_org(db: Session, payload: BillingBypassRequest) -> Organization:
    query = db.query(Organization)
    if payload.org_id is not None:
        org = query.filter(Organization.id == payload.org_id).first()
    elif payload.org_slug:
        org = query.filter(Organization.slug == payload.org_slug).first()
    elif payload.owner_email:
        user = db.query(User).filter(User.email == payload.owner_email).first()
        org = user.org if user else None
    else:
        raise HTTPException(status_code=400, detail="Provide org_id, org_slug, or owner_email")
    if not org:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return org


def _stripe_value(obj, key: str):
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


@router.get("/status", response_model=BillingStatusResponse)
def billing_status(user: User = Depends(get_current_user)):
    return _status_response(user.org)


@router.post("/checkout")
def start_checkout(
    user: User = Depends(require_owner),
    db: Session = Depends(get_db),
):
    if user.org.billing_exempt:
        return {"ok": True, "billing_exempt": True, "url": "/setup"}
    if not billing_configured():
        return {"ok": False, "billing_configured": False, "detail": "Stripe is not configured"}

    try:
        session = create_checkout_session(user.org, user)
    except BillingError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Stripe checkout failed: {exc}") from exc

    user.org.subscription_status = "incomplete"
    if user.org.plan in {"", "beta"}:
        user.org.plan = "full_service"
    db.commit()
    return {"ok": True, "url": session.url, "session_id": session.id}


@router.post("/portal")
def start_customer_portal(user: User = Depends(require_owner)):
    try:
        session = create_customer_portal_session(user.org)
    except BillingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Stripe portal failed: {exc}") from exc
    return {"ok": True, "url": session.url}


@router.post("/admin/bypass", response_model=BillingStatusResponse)
def admin_billing_bypass(
    payload: BillingBypassRequest,
    db: Session = Depends(get_db),
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
):
    if not settings.billing_admin_token:
        raise HTTPException(status_code=403, detail="Billing admin token is not configured")
    if not x_admin_token or not secrets.compare_digest(x_admin_token, settings.billing_admin_token):
        raise HTTPException(status_code=403, detail="Invalid billing admin token")

    org = _find_org(db, payload)
    if payload.enabled:
        org.billing_exempt = True
        org.billing_exempt_reason = (payload.reason or "Admin billing bypass").strip()[:255]
        org.subscription_status = "active"
        org.plan = "full_service_test"
    else:
        org.billing_exempt = False
        org.billing_exempt_reason = ""
        if not org.stripe_subscription_id:
            org.subscription_status = "incomplete" if billing_enabled() else "trialing"
            org.subscription_current_period_end = None
            org.subscription_cancel_at_period_end = False
        if org.plan == "full_service_test":
            org.plan = "full_service"

    db.commit()
    db.refresh(org)
    return _status_response(org)


@webhook_router.post("/stripe/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    signature = request.headers.get("stripe-signature")
    try:
        event = construct_webhook_event(payload, signature)
    except BillingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook") from exc

    event_type = event["type"] if isinstance(event, dict) else event.type
    obj = event["data"]["object"] if isinstance(event, dict) else event.data.object

    org = None
    org_id = org_id_from_stripe_object(obj)
    if org_id is not None:
        org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org:
        subscription_id = _stripe_value(obj, "id") if event_type.startswith("customer.subscription.") else _stripe_value(obj, "subscription")
        customer_id = _stripe_value(obj, "customer")
        if subscription_id:
            org = db.query(Organization).filter(Organization.stripe_subscription_id == subscription_id).first()
        if not org and customer_id:
            org = db.query(Organization).filter(Organization.stripe_customer_id == customer_id).first()

    if not org:
        return {"ok": True, "ignored": True}

    if event_type == "checkout.session.completed":
        apply_checkout_session_to_org(org, obj)
    elif event_type in {"customer.subscription.created", "customer.subscription.updated"}:
        apply_subscription_to_org(org, obj)
    elif event_type == "customer.subscription.deleted":
        apply_subscription_to_org(org, obj)
        org.subscription_status = "canceled"
    elif event_type == "invoice.payment_failed" and org.subscription_status == "active":
        org.subscription_status = "past_due"
    elif event_type == "invoice.paid" and org.stripe_subscription_id:
        org.subscription_status = "active"

    db.commit()
    return {"ok": True}
