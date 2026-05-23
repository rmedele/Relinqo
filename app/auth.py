import bcrypt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.billing import ACTIVE_BILLING_STATUSES, org_has_billing_access
from app.models import Organization, OrgSettings, User, hash_api_key


ACTIVE_SUBSCRIPTION_STATUSES = ACTIVE_BILLING_STATUSES


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_owner(user: User = Depends(get_current_user)) -> User:
    if user.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")
    return user


def org_can_use_automation(org: Organization | None, org_settings: OrgSettings | None) -> bool:
    if not org_has_billing_access(org):
        return False
    if org_settings and org_settings.automation_paused:
        return False
    return True


def require_active_org(user: User = Depends(get_current_user)) -> User:
    if not org_has_billing_access(user.org):
        raise HTTPException(status_code=402, detail="Workspace is not active")
    return user


def require_owner_active(user: User = Depends(require_owner)) -> User:
    if not org_has_billing_access(user.org):
        raise HTTPException(status_code=402, detail="Workspace is not active")
    return user


def get_org_settings(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> OrgSettings:
    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).first()
    if not org_settings:
        org_settings = OrgSettings(org_id=user.org_id)
        db.add(org_settings)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).first()
            if not org_settings:
                raise HTTPException(status_code=500, detail="Organization settings not found")
        else:
            db.refresh(org_settings)
    return org_settings


def get_org_from_session_or_api_key(
    request: Request, db: Session = Depends(get_db)
) -> tuple[Organization, OrgSettings]:
    # Try session first
    user_id = request.session.get("user_id")
    if user_id:
        user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
        if user:
            org = user.org
            org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org.id).first()
            if org_settings:
                return org, org_settings

    # Try API key header
    api_key = request.headers.get("X-API-Key")
    if api_key:
        digest = hash_api_key(api_key)
        org = db.query(Organization).filter(Organization.api_key_hash == digest).first()
        # Legacy fallback for databases not yet migrated. New/regenerated keys
        # only use api_key_hash and never store the raw key.
        if not org:
            org = db.query(Organization).filter(Organization.api_key == api_key).first()
        if org:
            if not org_has_billing_access(org):
                raise HTTPException(status_code=402, detail="Workspace is not active")
            org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org.id).first()
            if org_settings:
                return org, org_settings

    raise HTTPException(status_code=401, detail="Not authenticated")
