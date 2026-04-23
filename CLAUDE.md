# LeadRelay

AI-powered inbox management SaaS for local service businesses (plumbing, HVAC, roofing, electrical, etc.). Connects to a business owner's Gmail via OAuth, classifies incoming lead emails with AI, generates personalized replies, and alerts the owner on hot leads via SMS. The goal is speed-to-lead: respond to every inquiry in minutes, not hours.

## Current Status

**MVP is deployed and live on Railway.** Production URL: https://leadrelay-production-4a37.up.railway.app

The full flow works end-to-end: Gmail connects via OAuth, incoming emails are polled via Gmail API, classified by Claude AI, replies are drafted + sent back through Gmail API, and urgent leads trigger SMS alerts. Two competitive moat features — smart scheduling links and photo intake with Claude vision — are also shipped.

### What's Done
- **Deployed to Railway** on the trial plan (Dockerfile build, persistent volume at `/app/data`, auto-redeploy on push to `main`)
- Full lead pipeline: ingestion, AI classification, reply generation, auto-send with 60s undo window
- Google OAuth for Gmail (read inbox + send replies) — replaces clunky IMAP/SMTP credential setup
- Multi-tenant architecture (org-level settings, API keys, user roles)
- Session-based auth with registration, login, password reset, team invites
- Setup wizard (3-step: business profile, connect Gmail, test lead)
- Lead review dashboard with filters, thread view, outcome tracking
- Analytics dashboard (daily trends, category breakdown, conversion funnel, response times)
- Settings portal (Gmail connection, SMTP/IMAP fallback, Twilio, business profile, behavior toggles, smart scheduling)
- Follow-up scheduler (2h / 24h / 72h auto follow-ups)
- Daily/weekly digest emails
- SMS alerts via Twilio for urgent leads (urgency >= 4)
- SMS approval flow (owner texts YES/NO to approve drafted reply)
- Rate limiting, CSRF protection, production security headers (with Railway-compatible TrustedHostMiddleware)
- **Smart scheduling links** — org configures weekly availability, AI replies embed a booking URL (`/book/{token}`), customers pick a slot, owner gets notified via email/SMS
- **Photo intake + Claude vision** — Gmail image attachments are extracted (up to 3 images, 5MB each), stored under `data/photos/{org_id}/{lead_id}/`, analyzed by Claude Haiku vision, and shown in the lead detail UI with a lightbox gallery
- **Mobile-responsive dashboard** (on `mobile-responsive-pass` branch, pending merge to `main`):
  - Responsive breakpoints at 1180px, 820px, and 560px across dashboard, analytics, and settings
  - Today's Priority section — surfaces urgent, new, and failed leads at top of queue
  - Quick action buttons — one-tap call/email customer, reply templates (Thank You, Schedule, Quote)
  - Lead Outcome controls (Won/Lost/No Response) added to main dashboard (was missing from HTML)
  - Mobile bottom navigation bar (Queue/Analytics/Settings) on small screens
  - Back-to-list button on mobile detail view
  - Touch-friendly: 44px min tap targets, 16px font inputs (prevents iOS zoom), disabled hover transforms on touch devices

### High Priority — Next Up
- **Fix Google OAuth on production** — two steps needed:
  1. Set `PUBLIC_BASE_URL=https://leadrelay-production-4a37.up.railway.app` in Railway env vars (currently defaults to `http://127.0.0.1:8080` which breaks OAuth redirects)
  2. In Google Cloud Console → APIs & Services → Credentials → OAuth client, add `https://leadrelay-production-4a37.up.railway.app/auth/google/callback` as an authorized redirect URI
  - For local dev, set `PUBLIC_BASE_URL=http://127.0.0.1:8001` in `.env` and add `http://127.0.0.1:8001/auth/google/callback` to the redirect URIs in Google Console
- **Custom domain** — user plans to buy a domain (`leadrelay.app` or `getleadrelay.com`) and point it at Railway via CNAME. After DNS is live, swap `PUBLIC_BASE_URL` + Google OAuth URIs in both Railway env vars and Google Console.
- **Register first owner account** at `/register` + complete the setup wizard end-to-end on production.

### What Needs Work Before Launch
- **Merge `mobile-responsive-pass` branch** — dashboard enhancements are built and tested, need to merge to `main` to deploy
- **Job queue** — in-process asyncio scheduler needs to be replaced with Celery/Redis for reliability
- **Email/SMS retry** — currently fire-and-forget, no retry on failure
- **More test coverage** — only ~280 lines of tests covering happy paths
- **Google OAuth app verification** — currently limited to 100 test users
- **Billing/Stripe** — no payment integration yet
- **Platform-level Twilio** — currently each org must configure their own Twilio credentials, which is impractical for non-technical users. Refactor so LeadRelay owns one Twilio account (credentials in `.env`), business owners just enter their phone number in settings, and all SMS sends through the platform account. Keep "bring your own Twilio" as an optional power-user override.
- **Image resize before vision API** — photos are sent to Claude at full resolution (~$0.013/image). Cap at 512–1024px to control costs.

### What's NOT Needed Yet
- PostgreSQL migration (SQLite is fine for < 100K leads)
- Horizontal scaling / Kubernetes
- Webhook support, CRM connectors, Slack integration
- Admin panel for platform operator
- White-label multi-domain support

## Deployment Notes (Railway)

- **Hosting**: Railway, Dockerfile builder, persistent volume at `/app/data` for SQLite + photos
- **Auto-deploy**: pushes to `main` on GitHub (`rmedele/LeadRelay`) trigger rebuilds
- **Env vars set in Railway**: `APP_ENV=production`, `DATABASE_URL`, `SESSION_SECRET`, `LLM_PROVIDER`, `LLM_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `BUSINESS_*`, `OWNER_ALERT_EMAIL`, `DIGEST_TO_EMAIL`, `HUMAN_REVIEW=false`. `PUBLIC_BASE_URL` still needs to be set.
- **Gotchas fixed during deploy**:
  - Migration chain was broken: `0c61520c9265` (add indexes) ran before `f99f35365c7e` (create tables). Reordered.
  - `bcrypt>=4.1` dropped `__about__` which passlib 1.7.4 reads. Pinned `bcrypt==4.0.1` in `requirements.txt`.
  - `TrustedHostMiddleware` in `app/main.py` rejected Railway's internal healthcheck host with HTTP 400. Expanded allowlist to include `healthcheck.railway.app`, `*.up.railway.app`, `*.railway.app`, `*.railway.internal`, and `RAILWAY_PUBLIC_DOMAIN`.
  - Idempotent migration guards (`if_not_exists=True`) added so re-runs on a partially-seeded volume don't fail.

## Tech Stack
- **Backend**: FastAPI + Uvicorn
- **Database**: SQLite via SQLAlchemy 2.0 + Alembic migrations
- **AI/LLM**: Anthropic Claude API (Haiku model) for classification & reply generation
- **Email**: Google Gmail API via OAuth (primary), SMTP/IMAP as fallback
- **SMS**: Twilio for urgent lead alerts + approval workflow
- **Auth**: Session-based (bcrypt passwords, invite tokens, password reset)
- **Frontend**: Vanilla HTML/JS/CSS served as static files (dark theme, glass morphism)

## Project Structure
- `app/` - Main application package
  - `main.py` - FastAPI app setup, middleware, scheduler, marketing routes
  - `routes/leads.py` - Core API endpoints (ingest, review, CRUD, outcomes)
  - `routes/settings.py` - Org settings CRUD
  - `routes/auth.py` - Login, register, invite, password reset
  - `routes/google_oauth.py` - Google OAuth connect/callback/disconnect
  - `routes/sms_webhook.py` - Twilio SMS webhook for approval replies
  - `gmail.py` - Gmail API client (poll inbox, send email, token refresh)
  - `classifier.py` - Heuristic + AI lead classification
  - `reply_generator.py` - Template-based reply generation
  - `ai.py` - Claude API integration
  - `inbox_poll.py` - IMAP polling (delegates to Gmail API when OAuth is connected)
  - `mailer.py` - Email sender (delegates to Gmail API when OAuth is connected)
  - `email_parser.py` - Regex extraction of phone/location/name
  - `alerts.py` - Owner alert system
  - `followups.py` - Auto follow-up scheduling (2h/24h/72h)
  - `digest.py` - Daily/weekly digest builder
  - `sms.py` - Twilio SMS alerts for urgent leads
  - `models.py` - SQLAlchemy ORM models (Organization, User, OrgSettings, Lead, etc.)
  - `schemas.py` - Pydantic request/response schemas
  - `config.py` - Settings from .env
  - `auth.py` - Auth helpers (password hashing, session deps, role checks)
  - `database.py` - SQLAlchemy engine and session setup
  - `ui/` - Static frontend files
    - `setup.html` - 3-step setup wizard with Gmail OAuth
    - `settings.html` - Full settings portal with Gmail connect/disconnect
    - `index.html` + `app.js` - Lead review dashboard
    - `analytics.html` - Charts and conversion metrics
    - `marketing.html` - Landing page
    - `login.html`, `register.html`, `forgot-password.html`, `reset-password.html`
    - `styles.css` - Shared styles
- `scripts/` - Seed data, inbox polling, CLI tools
- `tests/` - pytest tests (with conftest.py for isolated test DB)
- `alembic/` - DB migrations

## Running
```bash
source .venv/bin/activate
uvicorn app.main:app --reload --port 8001
```

## Testing
```bash
PYTHONPATH=. pytest tests/ -v
```

## Key Config (.env)
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` - Google OAuth for Gmail integration
- `PUBLIC_BASE_URL` - Must match the OAuth redirect URI registered in Google Cloud Console
- `LLM_PROVIDER=anthropic` / `LLM_API_KEY` - Claude AI for classification
- `HUMAN_REVIEW=false` - Auto-sends replies above confidence threshold
- SMTP/IMAP credentials (fallback when Gmail OAuth is not connected)
- Twilio SID/token/phone for SMS alerts

## Key Architecture Decisions
- Gmail OAuth tokens are stored per-org in `OrgSettings` (not per-user)
- `inbox_poll.py` and `mailer.py` check for Gmail OAuth first, fall back to IMAP/SMTP
- Background scheduler runs in-process via asyncio (flush pending sends every 30s, follow-ups every 5min)
- All leads scoped by `org_id` for multi-tenant isolation
- Setup wizard test leads use `source="setup_wizard"` to bypass spam send restrictions
