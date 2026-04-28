import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from google_auth_oauthlib.flow import Flow
from sqlalchemy.orm import Session

from app.auth import get_org_settings, require_owner
from app.config import settings
from app.database import get_db
from app.gmail import SCOPES
from app.models import OrgSettings, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/google", tags=["google-oauth"])


def _build_flow(redirect_uri: str) -> Flow:
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(status_code=500, detail="Google OAuth is not configured")

    client_config = {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }
    flow = Flow.from_client_config(client_config, scopes=SCOPES, redirect_uri=redirect_uri)
    return flow


@router.get("/connect")
def google_connect(
    request: Request,
    user: User = Depends(require_owner),
):
    from_page = request.query_params.get("from", "settings")
    redirect_uri = f"{settings.public_base_url}/auth/google/callback"

    flow = _build_flow(redirect_uri)
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )

    request.session["google_oauth_state"] = state
    request.session["google_oauth_from"] = from_page

    return RedirectResponse(url=authorization_url)


@router.get("/callback")
def google_callback(
    request: Request,
    code: str | None = None,
    error: str | None = None,
    state: str | None = None,
    db: Session = Depends(get_db),
):
    if error:
        logger.warning("Google OAuth error: %s", error)
        from_page = request.session.pop("google_oauth_from", "settings")
        return RedirectResponse(url=f"/{from_page}?gmail=error&reason={error}")

    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    expected_state = request.session.get("google_oauth_state")
    if not expected_state or not state or state != expected_state:
        request.session.pop("google_oauth_state", None)
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login")

    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user or user.role != "owner":
        raise HTTPException(status_code=403, detail="Owner access required")

    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == user.org_id).first()
    if not org_settings:
        raise HTTPException(status_code=500, detail="Organization settings not found")

    redirect_uri = f"{settings.public_base_url}/auth/google/callback"
    flow = _build_flow(redirect_uri)

    flow.fetch_token(code=code)
    credentials = flow.credentials

    # Get the user's Gmail address
    gmail_email = ""
    try:
        resp = httpx.get(
            "https://www.googleapis.com/gmail/v1/users/me/profile",
            headers={"Authorization": f"Bearer {credentials.token}"},
        )
        if resp.status_code == 200:
            gmail_email = resp.json().get("emailAddress", "")
    except Exception:
        logger.warning("Failed to fetch Gmail profile")

    org_settings.google_oauth_access_token = credentials.token or ""
    org_settings.google_oauth_refresh_token = credentials.refresh_token or ""
    org_settings.google_oauth_email = gmail_email
    if credentials.expiry:
        from datetime import timezone
        org_settings.google_oauth_token_expires_at = credentials.expiry.replace(tzinfo=timezone.utc)
    org_settings.inbox_poll_enabled = True
    db.commit()

    logger.info("Google OAuth connected for org %d (%s)", user.org_id, gmail_email)

    from_page = request.session.pop("google_oauth_from", "settings")
    request.session.pop("google_oauth_state", None)

    if from_page == "setup":
        return RedirectResponse(url="/setup?step=2&gmail=connected")
    return RedirectResponse(url="/settings?gmail=connected")


@router.post("/disconnect")
def google_disconnect(
    user: User = Depends(require_owner),
    org_settings: OrgSettings = Depends(get_org_settings),
    db: Session = Depends(get_db),
):
    # Revoke the token at Google
    if org_settings.google_oauth_access_token:
        try:
            httpx.post(
                "https://oauth2.googleapis.com/revoke",
                params={"token": org_settings.google_oauth_access_token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except Exception:
            logger.warning("Failed to revoke Google OAuth token")

    org_settings.google_oauth_access_token = ""
    org_settings.google_oauth_refresh_token = ""
    org_settings.google_oauth_token_expires_at = None
    org_settings.google_oauth_email = ""
    db.commit()

    logger.info("Google OAuth disconnected for org %d", user.org_id)
    return {"ok": True, "message": "Gmail disconnected"}
