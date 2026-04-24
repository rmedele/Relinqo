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
- **Phone lead capture V1 — backend complete** (on `mobile-responsive-pass` branch, pending merge to `main`, NOT yet tested against a real Twilio number):
  - Full Twilio Programmable Voice integration: missed-call rescue, voicemail-to-lead, after-hours intake
  - `/incoming` webhook branches on business hours + routing rule: emits `<Dial>` to owner when open and owner is configured, falls through to voicemail on no-answer/busy/failed; skips dialing entirely after hours
  - Twilio native transcription + Claude Haiku extraction (caller name, callback number, service address, issue summary, urgency, category, is_spam) — deliberately chose Twilio STT over Whisper for V1 simplicity; Claude is robust to noisy transcripts
  - Creates a Lead with `source="phone"` and `call_event_id` FK on completion; spam short-circuits without creating a Lead
  - Sends owner SMS alert (urgency icons, `[AFTER HOURS]` prefix, dashboard deep link) + optional caller-confirmation SMS (deduped within 10 min to handle redial storms)
  - HMAC-SHA1 webhook signature validation (stdlib only, no `twilio` SDK dependency) — skipped in dev, enforced in production
  - Idempotent webhook handling via unique constraints on `twilio_call_sid` + `twilio_recording_sid`; conditional UPDATE on transcript so retries don't double-process
  - Local E2E test script: `scripts/simulate_twilio_call.py` — exercises the full pipeline via FastAPI TestClient without Twilio or ngrok. Flags: `--spam`, `--with-dial --owner-phone=...`, `--after-hours`

### High Priority — Next Up
- **Fix Google OAuth on production** — two steps needed:
  1. Set `PUBLIC_BASE_URL=https://leadrelay-production-4a37.up.railway.app` in Railway env vars (currently defaults to `http://127.0.0.1:8080` which breaks OAuth redirects)
  2. In Google Cloud Console → APIs & Services → Credentials → OAuth client, add `https://leadrelay-production-4a37.up.railway.app/auth/google/callback` as an authorized redirect URI
  - For local dev, set `PUBLIC_BASE_URL=http://127.0.0.1:8001` in `.env` and add `http://127.0.0.1:8001/auth/google/callback` to the redirect URIs in Google Console
- **Custom domain** — user plans to buy a domain (`leadrelay.app` or `getleadrelay.com`) and point it at Railway via CNAME. After DNS is live, swap `PUBLIC_BASE_URL` + Google OAuth URIs in both Railway env vars and Google Console.
- **Register first owner account** at `/register` + complete the setup wizard end-to-end on production.

### What Needs Work Before Launch
- **Phone lead capture — Week 3 follow-ups** (backend done, not yet shipped):
  - Admin UI for `phone_numbers`, `phone_routing_rules`, `phone_business_hours` — currently these must be seeded via direct DB inserts
  - Dashboard view of `call_events` + `voicemails` with an authenticated recording playback proxy (Twilio recording URLs require the account SID/token to fetch)
  - Sweep job for stuck voicemails — if Twilio's `transcribeCallback` never fires, the Voicemail row is orphaned in `pending` state. Need a periodic task that flips rows older than ~10 min to `failed` and still creates a Lead ("transcript unavailable, please listen")
  - Per-`from_number` rate limit on `/incoming` to defend against spam-dial storms (Twilio Voice Shield is an alternative)
  - Real end-to-end verification against a live Twilio number (scripted simulation passes; real call hasn't been made yet)
- **Merge `mobile-responsive-pass` branch** — dashboard enhancements + phone backend are built and tested, need to merge to `main` to deploy
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
  - `routes/sms_webhook.py` - Twilio SMS webhook for approval replies (YES/NO reply-to-approve)
  - `routes/twilio_voice.py` - Twilio Programmable Voice webhooks (phone lead capture V1)
  - `gmail.py` - Gmail API client (poll inbox, send email, token refresh)
  - `classifier.py` - Heuristic + AI lead classification (for email leads)
  - `reply_generator.py` - Template-based reply generation
  - `ai.py` - Claude API integration for email classification & reply generation
  - `voicemail_processor.py` - Background job: Claude extraction from voicemail transcripts -> Lead creation + owner/caller SMS
  - `phone_routing.py` - Business-hours gate, timezone-aware dial-owner decision logic, phone normalization
  - `twilio_signature.py` - HMAC-SHA1 Twilio webhook signature validator (stdlib only, no SDK dependency)
  - `inbox_poll.py` - IMAP polling (delegates to Gmail API when OAuth is connected)
  - `mailer.py` - Email sender (delegates to Gmail API when OAuth is connected)
  - `email_parser.py` - Regex extraction of phone/location/name
  - `alerts.py` - Owner alert system
  - `followups.py` - Auto follow-up scheduling (2h/24h/72h)
  - `digest.py` - Daily/weekly digest builder
  - `sms.py` - Twilio SMS send helpers: `send_sms(body, org_settings)` to the org's configured alert number, `send_sms_to(body, to, org_settings)` for arbitrary destinations (returns `twilio_message_sid`)
  - `models.py` - SQLAlchemy ORM models. Phone additions: `PhoneNumber`, `PhoneBusinessHours`, `PhoneRoutingRule`, `CallEvent`, `Voicemail`, `SmsNotification`; `Lead.call_event_id` FK
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

### Phone-capture local E2E simulation
No Twilio account or ngrok needed — uses FastAPI TestClient to exercise the full `/twilio/voice/*` pipeline and prints resulting DB rows:
```bash
PYTHONPATH=. python scripts/simulate_twilio_call.py                                   # happy path (voicemail -> lead)
PYTHONPATH=. python scripts/simulate_twilio_call.py --spam                            # spam transcript, asserts no Lead created
PYTHONPATH=. python scripts/simulate_twilio_call.py --with-dial --owner-phone=+1555...  # missed-call rescue (dial -> no-answer -> voicemail)
PYTHONPATH=. python scripts/simulate_twilio_call.py --after-hours --owner-phone=+1555...  # after-hours intake + [AFTER HOURS] prefix in owner SMS
```
The script seeds `phone_numbers` / `phone_routing_rules` / `phone_business_hours` rows as needed. Signature validation is skipped automatically because `APP_ENV != production`. SMS sends are attempted but will be recorded as `failed` in `sms_notifications` unless Twilio credentials are present in `OrgSettings` — the test exercises the pipeline regardless.

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

### Phone-capture specific decisions (V1)
- **Twilio native transcription over Whisper/AssemblyAI** — free, async via `transcribeCallback`, good enough because Claude recovers well from noisy transcripts. Upgrade path preserved (download `RecordingUrl.mp3` via authenticated Twilio REST, pipe through Whisper, write back to `voicemails.transcript`).
- **`<Record>` over `<Gather>`** — voicemail has lower caller-abandonment than question-by-question voice prompts; Claude extracts structure from free-form speech better than Gather can orchestrate.
- **Single owner number, not ring groups** — `phone_routing_rules` stores one `owner_phone` per org. Ring groups + sequential dial are Week 4+ features.
- **`callerId` on `<Dial>` set to the Twilio business number**, not the caller's. Owner sees their business line lighting up on their handset (reliable cross-carrier); the customer's real number is preserved in `call_events.from_number` and shown in the SMS alert.
- **Business hours: empty table = 24/7 open**. The owner-phone field is the real gate; hours only matter once configured. Fail-closed (always voicemail after hours) if hours are present but current time falls outside.
- **Synthetic email for phone leads**: `Lead.sender_email` is NOT NULL but phone callers have none, so we synthesize `caller-{e164digits}@phone.leadrelay.local`. Phone leads carry `source="phone"` and `call_event_id` FK — UI should prefer `phone` + `call_event` over email fields when `source="phone"`.
- **Signature validation rebuilds the URL from `PUBLIC_BASE_URL`**, not `request.url`, because Railway terminates TLS upstream and would otherwise yield `http://` even though Twilio signed `https://`.
- **SMS dedup via `sms_notifications`**: caller confirmations suppressed if one has been sent to the same `to_number` for the same `org_id` within 10 minutes (protects against redial storms).
- **Fire-and-forget background job**: transcription-complete uses `asyncio.create_task(process_voicemail(vm_id))` — matches the existing in-process scheduler pattern. A sweep job is still needed (see Week 3 follow-ups) to recover from Railway restarts mid-classification.

### Phone-capture testing setup against real Twilio
1. Buy a Twilio number (trial works). Get the `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN`.
2. Insert `phone_numbers` row: `INSERT INTO phone_numbers (org_id, twilio_sid, phone_number, is_active) VALUES (1, 'PN...', '+1555...', 1);`
3. Insert `phone_routing_rules` row with your cell as `owner_phone` (E.164 format, e.g. `+14035551234`).
4. Set Twilio credentials in `OrgSettings` (or as env vars) so SMS sends actually go out.
5. In Twilio Console → your number → Voice Configuration:
   - "A CALL COMES IN" webhook → `POST {PUBLIC_BASE_URL}/twilio/voice/incoming`
   - "CALL STATUS CHANGES" → `POST {PUBLIC_BASE_URL}/twilio/voice/call-status` with events `completed, no-answer, busy, failed`
6. For local testing: `ngrok http 8001`, use the ngrok HTTPS URL as `PUBLIC_BASE_URL` and in the Twilio Console webhooks.
7. Call the Twilio number from a different phone, leave a voicemail, verify Lead creation + owner SMS.
