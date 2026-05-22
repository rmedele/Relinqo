import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
import re as _re

from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.orm import Session

from app.auth import get_current_user, get_org_settings, hash_password, require_owner, verify_password
from app.billing import new_org_subscription_status
from app.config import settings
from app.database import get_db
from app.rate_limit import login_limiter, register_limiter
from app.mailer import send_email
from app.models import InviteToken, Organization, OrgSettings, PasswordResetToken, User, hash_api_key

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    org_name: str
    email: EmailStr
    password: str
    display_name: str | None = None

    @field_validator("org_name")
    @classmethod
    def validate_org_name(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("Organization name must be at least 2 characters")
        if len(v) > 100:
            raise ValueError("Organization name must be 100 characters or fewer")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if len(v) > 128:
            raise ValueError("Password must be 128 characters or fewer")
        if not _re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not _re.search(r"[a-z]", v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not _re.search(r"\d", v):
            raise ValueError("Password must contain at least one number")
        return v


class InviteRequest(BaseModel):
    email: EmailStr
    display_name: str | None = None


class AcceptInviteRequest(BaseModel):
    token: str
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if len(v) > 128:
            raise ValueError("Password must be 128 characters or fewer")
        if not _re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not _re.search(r"[a-z]", v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not _re.search(r"\d", v):
            raise ValueError("Password must contain at least one number")
        return v


def _user_dict(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
    }


def _org_dict(org: Organization, include_api_key: bool = False) -> dict:
    d = {
        "id": org.id,
        "name": org.name,
        "slug": org.slug,
        "subscription_status": org.subscription_status,
        "plan": org.plan,
        "billing_exempt": bool(org.billing_exempt),
    }
    if include_api_key:
        d["api_key"] = org.api_key
    return d


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


@router.post("/login")
def login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)):
    login_limiter.check(request)
    user = db.query(User).filter(User.email == payload.email, User.is_active.is_(True)).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    request.session["user_id"] = user.id
    return {"ok": True, "user": _user_dict(user), "org": _org_dict(user.org)}


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.post("/register")
def register(request: Request, payload: RegisterRequest, db: Session = Depends(get_db)):
    register_limiter.check(request)
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=409, detail="Email already registered")

    slug = payload.org_name.strip().lower().replace(" ", "-")[:100]
    base_slug = slug
    counter = 1
    while db.query(Organization).filter(Organization.slug == slug).first():
        slug = f"{base_slug}-{counter}"
        counter += 1

    raw_api_key = secrets.token_hex(32)
    org = Organization(
        name=payload.org_name.strip(),
        slug=slug,
        api_key=f"deprecated-new-{raw_api_key[:16]}",
        api_key_hash=hash_api_key(raw_api_key),
        subscription_status=new_org_subscription_status(),
        plan="full_service",
    )
    db.add(org)
    db.flush()

    db.add(OrgSettings(org_id=org.id))

    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name or payload.email.split("@")[0],
        role="owner",
        org_id=org.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.refresh(org)

    request.session["user_id"] = user.id
    return {"ok": True, "user": _user_dict(user), "org": {**_org_dict(org), "api_key": raw_api_key}}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"user": _user_dict(user), "org": _org_dict(user.org)}


@router.post("/regenerate-api-key")
def regenerate_api_key(user: User = Depends(require_owner), db: Session = Depends(get_db)):
    raw_api_key = secrets.token_hex(32)
    user.org.api_key = f"deprecated-{user.org_id}-{raw_api_key[:16]}"
    user.org.api_key_hash = hash_api_key(raw_api_key)
    db.commit()
    return {"ok": True, "api_key": raw_api_key}


INVITE_EXPIRY_HOURS = 72


@router.post("/invite")
def invite_member(
    payload: InviteRequest,
    user: User = Depends(require_owner),
    db: Session = Depends(get_db),
    org_settings: OrgSettings = Depends(get_org_settings),
):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=409, detail="Email already registered")

    token = secrets.token_urlsafe(48)
    invite = InviteToken(
        org_id=user.org_id,
        email=payload.email,
        token=token,
        display_name=payload.display_name,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=INVITE_EXPIRY_HOURS),
    )
    db.add(invite)
    db.commit()

    invite_url = f"{settings.public_base_url}/register?invite={token}"
    biz = org_settings.business_name or user.org.name
    email_body = (
        f"You've been invited to join {biz} on relinqo!\n\n"
        f"Click the link below to create your account:\n"
        f"{invite_url}\n\n"
        f"This invite expires in {INVITE_EXPIRY_HOURS} hours.\n\n"
        f"- {biz} via relinqo"
    )
    sent, msg = send_email(
        to_email=payload.email,
        subject=f"You're invited to join {biz} on relinqo",
        body=email_body,
        org_settings=org_settings,
    )

    return {
        "ok": True,
        "invite_sent": sent,
        "invite_url": invite_url,
        "message": f"Invite email {'sent to' if sent else 'could not be sent to'} {payload.email}" + ("" if sent else f" ({msg})"),
    }


@router.post("/accept-invite")
def accept_invite(request: Request, payload: AcceptInviteRequest, db: Session = Depends(get_db)):
    invite = db.query(InviteToken).filter(
        InviteToken.token == payload.token,
        InviteToken.accepted_at.is_(None),
    ).first()
    if not invite:
        raise HTTPException(status_code=404, detail="Invalid or already-used invite")
    if datetime.now(timezone.utc) > _as_utc(invite.expires_at):
        raise HTTPException(status_code=410, detail="Invite has expired")
    if db.query(User).filter(User.email == invite.email).first():
        raise HTTPException(status_code=409, detail="Email already registered")

    member = User(
        email=invite.email,
        password_hash=hash_password(payload.password),
        display_name=invite.display_name or invite.email.split("@")[0],
        role="member",
        org_id=invite.org_id,
    )
    db.add(member)
    invite.accepted_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(member)

    request.session["user_id"] = member.id
    org = db.query(Organization).filter(Organization.id == invite.org_id).first()
    return {"ok": True, "user": _user_dict(member), "org": _org_dict(org)}


# --- Password Reset ---

RESET_EXPIRY_HOURS = 1


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if len(v) > 128:
            raise ValueError("Password must be 128 characters or fewer")
        if not _re.search(r"[A-Z]", v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not _re.search(r"[a-z]", v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not _re.search(r"\d", v):
            raise ValueError("Password must contain at least one number")
        return v


@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    # Always return success to avoid email enumeration
    user = db.query(User).filter(User.email == payload.email, User.is_active.is_(True)).first()
    if not user:
        return {"ok": True, "message": "If that email exists, a reset link has been sent."}

    token = secrets.token_urlsafe(48)
    reset = PasswordResetToken(
        user_id=user.id,
        token=token,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=RESET_EXPIRY_HOURS),
    )
    db.add(reset)
    db.commit()

    reset_url = f"{settings.public_base_url}/reset-password?token={token}"
    sent, message = send_email(
        to_email=user.email,
        subject="relinqo - Password Reset",
        body=(
            f"You requested a password reset for your relinqo account.\n\n"
            f"Click the link below to set a new password:\n"
            f"{reset_url}\n\n"
            f"This link expires in {RESET_EXPIRY_HOURS} hour(s).\n"
            f"If you didn't request this, you can ignore this email.\n\n"
            f"- relinqo"
        ),
    )
    if not sent:
        logger.warning("Password reset email failed for user_id=%s email=%s: %s", user.id, user.email, message)

    return {"ok": True, "message": "If that email exists, a reset link has been sent."}


@router.post("/reset-password")
def reset_password(request: Request, payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    reset = db.query(PasswordResetToken).filter(
        PasswordResetToken.token == payload.token,
        PasswordResetToken.used_at.is_(None),
    ).first()
    if not reset:
        raise HTTPException(status_code=404, detail="Invalid or already-used reset link")
    if datetime.now(timezone.utc) > _as_utc(reset.expires_at):
        raise HTTPException(status_code=410, detail="Reset link has expired")

    user = db.query(User).filter(User.id == reset.user_id, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.password_hash = hash_password(payload.password)
    reset.used_at = datetime.now(timezone.utc)
    db.commit()

    request.session["user_id"] = user.id
    return {"ok": True, "message": "Password has been reset. You are now logged in."}


