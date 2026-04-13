# LeadRelay

AI-powered inbox management SaaS for local service businesses (plumbing, HVAC, roofing, electrical, etc.). Connects to a business owner's Gmail via OAuth, classifies incoming lead emails with AI, generates personalized replies, and alerts the owner on hot leads via SMS. The goal is speed-to-lead: respond to every inquiry in minutes, not hours.

## Current Status

**MVP is feature-complete and working end-to-end.** Google OAuth for Gmail is live and tested. The full flow works: Gmail connects via one-click OAuth, incoming emails are polled via Gmail API, classified by Claude AI, replies are drafted and sent back through Gmail API, and urgent leads trigger SMS alerts.

### What's Done
- Full lead pipeline: ingestion, AI classification, reply generation, auto-send with 60s undo window
- Google OAuth for Gmail (read inbox + send replies) — replaces clunky IMAP/SMTP credential setup
- Multi-tenant architecture (org-level settings, API keys, user roles)
- Session-based auth with registration, login, password reset, team invites
- Setup wizard (3-step: business profile, connect Gmail, test lead)
- Lead review dashboard with filters, thread view, outcome tracking
- Analytics dashboard (daily trends, category breakdown, conversion funnel, response times)
- Settings portal (Gmail connection, SMTP/IMAP fallback, Twilio, business profile, behavior toggles)
- Follow-up scheduler (2h / 24h / 72h auto follow-ups)
- Daily/weekly digest emails
- SMS alerts via Twilio for urgent leads (urgency >= 4)
- SMS approval flow (owner texts YES/NO to approve drafted reply)
- Rate limiting, CSRF protection, production security headers

### High Priority — Next Up
- **Deploy to Railway ($5/mo hobby plan)** — init git repo, push to GitHub, connect Railway, configure env vars, update Google OAuth redirect URI to production URL. This is the immediate next step.

### What Needs Work Before Launch
- **Mobile-responsive dashboard** — target user is a plumber on their phone, not at a desk
- **Job queue** — in-process asyncio scheduler needs to be replaced with Celery/Redis for reliability
- **Email/SMS retry** — currently fire-and-forget, no retry on failure
- **More test coverage** — only ~280 lines of tests covering happy paths
- **Google OAuth app verification** — currently limited to 100 test users
- **Billing/Stripe** — no payment integration yet
- **Platform-level Twilio** — currently each org must configure their own Twilio credentials, which is impractical for non-technical users. Refactor so LeadRelay owns one Twilio account (credentials in `.env`), business owners just enter their phone number in settings, and all SMS sends through the platform account. Keep "bring your own Twilio" as an optional power-user override.

### What's NOT Needed Yet
- PostgreSQL migration (SQLite is fine for < 100K leads)
- Horizontal scaling / Kubernetes
- Webhook support, CRM connectors, Slack integration
- Admin panel for platform operator
- White-label / custom domain support

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
