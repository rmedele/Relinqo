import bcrypt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Organization, OrgSettings, User


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


def get_org_settings(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> OrgSettings:
    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).first()
    if not org_settings:
        raise HTTPException(status_code=500, detail="Organization settings not found")
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
        org = db.query(Organization).filter(Organization.api_key == api_key).first()
        if org:
            org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org.id).first()
            if org_settings:
                return org, org_settings

    raise HTTPException(status_code=401, detail="Not authenticated")
