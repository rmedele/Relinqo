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
  - Full Twilio Programmable Voice integration: missed-call rescue, voicemail-to-lead, after-hours intake, **SMS outreach after hangup** (primary path for modern callers who won't leave voicemails)
  - `/incoming` branches on business hours + routing rule: emits `<Dial>` to owner when open + owner configured, else plays a short "we'll text you" greeting + fires an outreach SMS in the background + offers a voicemail as a landline fallback
  - After-hours flow: no dial attempted, plays closed greeting, fires outreach SMS, caller's SMS reply becomes the Lead (no voicemail required)
  - Twilio native transcription + Claude Haiku extraction (caller name, callback number, service address, issue summary, urgency, category, is_spam) — deliberately chose Twilio STT over Whisper for V1 simplicity; Claude is robust to noisy transcripts
  - Dual-channel intake: voicemails AND inbound SMS replies both create Leads via Claude classification; deduped on `call_event_id` so one call = one Lead (first channel wins, second appends)
  - Sends owner SMS alert (urgency icons, `[AFTER HOURS]` prefix, `(voicemail)` / `(SMS)` channel tag, dashboard deep link) + optional caller-confirmation (deduped against outreach SMS to prevent double-texting)
  - HMAC-SHA1 webhook signature validation (stdlib only, no `twilio` SDK dependency) — skipped in dev, enforced in production
  - Idempotent webhook handling via unique constraints on `twilio_call_sid` + `twilio_recording_sid`; conditional UPDATE on transcript so retries don't double-process
  - **Self-service number provisioning**: `/api/phone/search` + `/api/phone/provision` buy a number via Twilio REST API, configure webhooks, and save it to the org — all from inside LeadRelay. Settings UI has a "Find me a number" button (area code → list → pick one → live). Customers never touch the Twilio console.
  - Local E2E test script: `scripts/simulate_twilio_call.py` — exercises all paths via FastAPI TestClient without Twilio or ngrok. Flags: `--spam`, `--with-dial --owner-phone=...`, `--after-hours`, `--sms-reply "message"` (simulates caller hanging up + replying via SMS)

### High Priority — Next Up (priority order — tackle top-to-bottom)

**STATE as of 2026-04-24:** Twilio env vars (`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER=+17822121292`, `SMS_ALERT_TO_NUMBER=+18254401394`) are set on Railway. `PUBLIC_BASE_URL=https://leadrelay-production-4a37.up.railway.app` is set. Google OAuth is working on production. SMTP/forgot-password is no longer being pursued.

1. **Remove the temporary `/auth/rescue` endpoint** — added during the Twilio test to bypass broken SMTP. See `app/routes/auth.py` — the block under `# TEMPORARY RESCUE FLOW`. Delete the block + the `HTMLResponse`/`Form` imports if they're unused after removal. Secret is `lr-rescue-2026-04-24-9f3a`, hardcoded, so this is a live backdoor until removed.

2. **Test Twilio phone capture end-to-end against production** (the code is done + merged to main + deployed via PR #7, Twilio env vars are set, the number +17822121292 is bought). Remaining steps:
   - Log into `/settings` → "Phone lead capture" card → use the "Already bought a number?" Adopt form → enter `+17822121292` + `+18254401394` → click Adopt. This calls `POST /api/phone/adopt` which configures webhooks on the existing number via Twilio REST.
   - Verify an additional phone on Twilio → Phone Numbers → Verified Caller IDs (the phone the test call will be placed FROM — trial accounts reject calls from unverified numbers)
   - Make a test call to +17822121292, don't answer the cell, hang up, verify: (a) cell gets summary SMS within ~10s of any SMS reply; (b) caller gets "we'll text you" SMS; (c) SMS reply creates a Lead in the dashboard with `source="phone"`.

3. **Custom domain** — user plans to buy a domain (`leadrelay.app` or `getleadrelay.com`) and point it at Railway via CNAME. After DNS is live, swap `PUBLIC_BASE_URL` + Google OAuth URIs in both Railway env vars and Google Console. Adopted Twilio numbers will need webhook URLs re-pointed (use `POST /api/phone/adopt` again, or update directly in Twilio Console).

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
  - `routes/phone_provisioning.py` - `/api/phone/*` endpoints — Bob clicks "Find me a number" in the UI, backend calls Twilio REST to buy + configure it on the platform account
  - `gmail.py` - Gmail API client (poll inbox, send email, token refresh)
  - `classifier.py` - Heuristic + AI lead classification (for email leads)
  - `reply_generator.py` - Template-based reply generation
  - `ai.py` - Claude API integration for email classification & reply generation
  - `voicemail_processor.py` - Background job: Claude extraction from voicemail transcripts -> Lead creation
  - `sms_intake.py` - After-hours SMS outreach send + inbound SMS reply -> Lead creation (the primary phone-lead path)
  - `phone_leads.py` - Shared helpers for both intake channels: owner-alert formatter, Twilio SMS send + persistence, synthetic sender_email, dedup windows
  - `phone_routing.py` - Business-hours gate, timezone-aware dial-owner decision logic, phone normalization
  - `twilio_client.py` - Thin stdlib REST client for buying numbers + configuring webhooks (no `twilio` SDK dep)
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
PYTHONPATH=. python scripts/simulate_twilio_call.py --after-hours --sms-reply "furnace broken, need help ASAP"  # SMS-outreach flow (caller hangs up, replies by text)
```
The script seeds `phone_numbers` / `phone_routing_rules` / `phone_business_hours` rows as needed. Signature validation is skipped automatically because `APP_ENV != production`. SMS sends are attempted but will be recorded as `failed` in `sms_notifications` unless Twilio credentials are present in `OrgSettings` — the test exercises the pipeline regardless.

## Key Config (.env)
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` - Google OAuth for Gmail integration
- `PUBLIC_BASE_URL` - Must match (a) the OAuth redirect URI registered in Google Cloud Console AND (b) the base URL used when provisioning Twilio numbers (phone webhooks are wired to `{PUBLIC_BASE_URL}/twilio/voice/*`). Changing this after provisioning leaves existing numbers pointing at the old URL — re-provision or update webhook URLs in Twilio Console.
- `LLM_PROVIDER=anthropic` / `LLM_API_KEY` - Claude AI for classification
- `HUMAN_REVIEW=false` - Auto-sends replies above confidence threshold
- SMTP/IMAP credentials (fallback when Gmail OAuth is not connected)
- `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` - **Platform-level Twilio account** — used for webhook signature validation, outbound SMS, AND number provisioning via `/api/phone/*`. These env vars are authoritative; `OrgSettings.twilio_*` columns are a BYO-Twilio fallback for power users.
- `TWILIO_FROM_NUMBER` - Default "from" number for owner-alert SMS when an org hasn't provisioned its own
- `SMS_ALERT_TO_NUMBER` - Default owner-alert destination (superseded by `phone_routing_rules.owner_phone` when configured)

## Key Architecture Decisions
- Gmail OAuth tokens are stored per-org in `OrgSettings` (not per-user)
- `inbox_poll.py` and `mailer.py` check for Gmail OAuth first, fall back to IMAP/SMTP
- Background scheduler runs in-process via asyncio (flush pending sends every 30s, follow-ups every 5min)
- All leads scoped by `org_id` for multi-tenant isolation
- Setup wizard test leads use `source="setup_wizard"` to bypass spam send restrictions

### Phone-capture specific decisions (V1)
- **SMS outreach is the PRIMARY missed-call path, voicemail is the fallback.** Most callers under 40 won't leave voicemail — they hang up the moment they hear a recording. The `/incoming` TwiML plays "we're sending you a text" + opens a `<Record>` with a 5-second silence timeout as a landline fallback. Outreach SMS fires in the background via `run_in_executor` so the TwiML response isn't blocked on the Twilio API call.
- **Twilio native transcription over Whisper/AssemblyAI** — free, async via `transcribeCallback`, good enough because Claude recovers well from noisy transcripts. Upgrade path preserved (download `RecordingUrl.mp3` via authenticated Twilio REST, pipe through Whisper, write back to `voicemails.transcript`).
- **`<Record>` over `<Gather>`** — voicemail has lower caller-abandonment than question-by-question voice prompts; Claude extracts structure from free-form speech better than Gather can orchestrate.
- **Single owner number, not ring groups** — `phone_routing_rules` stores one `owner_phone` per org. Ring groups + sequential dial are Week 4+ features.
- **`callerId` on `<Dial>` set to the Twilio business number**, not the caller's. Owner sees their business line lighting up on their handset (reliable cross-carrier); the customer's real number is preserved in `call_events.from_number` and shown in the SMS alert.
- **Business hours: empty table = 24/7 open**. The owner-phone field is the real gate; hours only matter once configured. Fail-closed (always voicemail after hours) if hours are present but current time falls outside.
- **Synthetic email for phone leads**: `Lead.sender_email` is NOT NULL but phone callers have none, so we synthesize `caller-{e164digits}@phone.leadrelay.local`. Phone leads carry `source="phone"` and `call_event_id` FK — UI should prefer `phone` + `call_event` over email fields when `source="phone"`.
- **Signature validation rebuilds the URL from `PUBLIC_BASE_URL`**, not `request.url`, because Railway terminates TLS upstream and would otherwise yield `http://` even though Twilio signed `https://`.
- **SMS dedup via `sms_notifications`**: caller confirmations suppressed if one has been sent to the same `to_number` for the same `org_id` within 10 minutes (protects against redial storms).
- **Fire-and-forget background job**: transcription-complete uses `asyncio.create_task(process_voicemail(vm_id))` — matches the existing in-process scheduler pattern. A sweep job is still needed (see Week 3 follow-ups) to recover from Railway restarts mid-classification.
- **Channel dedup on the Lead**: one CallEvent = one Lead regardless of how many channels responded. If voicemail classification beats SMS reply, the SMS body is appended to the existing Lead's body. If SMS gets there first, the voicemail transcript is appended. No duplicate Lead rows for the same physical call.
- **Recent-caller SMS window**: inbound SMS from a number that called within the last 30 min is treated as a lead-intake reply, NOT as the owner YES/NO approval flow. This means if the owner happens to test-call their own business number, their next SMS won't accidentally approve a lead. The `/sms/webhook` handler checks `find_recent_call_for_sender` FIRST; YES/NO parsing only runs if no recent call exists.
- **Self-service number provisioning is mandatory for the product to work.** Bob is a 60-year-old plumber. He will not go to the Twilio console, read docs, or configure webhooks. LeadRelay owns the provisioning flow end-to-end: `POST /api/phone/search` returns available numbers by area code, `POST /api/phone/provision` buys + wires one in a single call. Credentials come from `TWILIO_ACCOUNT_SID` + `TWILIO_AUTH_TOKEN` env vars (platform-level account), not from each org's `OrgSettings` — this matches the "platform-level Twilio" direction noted in the pre-launch TODOs.

### Phone-capture testing setup against real Twilio

**Canonical path (use the UI, not the DB):**
1. Set env vars in `.env`:
   ```
   TWILIO_ACCOUNT_SID=AC...            # from Twilio Account Dashboard
   TWILIO_AUTH_TOKEN=...               # from Twilio Account Dashboard
   PUBLIC_BASE_URL=https://xxx.ngrok-free.app   # ngrok URL (rotates on free tier)
   ```
2. `ngrok http 8001` in a second terminal. Paste the HTTPS URL into `PUBLIC_BASE_URL` and restart uvicorn so the new value is picked up — the provisioning endpoint uses it to wire webhook URLs on the Twilio number.
3. Verify the owner cell + any test-caller number on Twilio → Phone Numbers → Verified Caller IDs. Trial accounts silently reject calls/SMS to unverified numbers.
4. Log in at `https://xxx.ngrok-free.app/settings`. Scroll to "Phone lead capture" → enter area code + cell → "Find me a number" → pick one → "Use this". Provisioning takes ~3s; webhooks are auto-configured on the number.
5. Test scenarios to run:
   - **Missed-call → SMS outreach**: call from a different phone, don't answer your cell within 20s, hear the "we'll text you" greeting, hang up. Expect: outreach SMS to caller + owner alert within ~10s of any SMS reply.
   - **Voicemail fallback**: during the greeting, stay on the line and leave a voicemail (simulates a landline caller). Expect: Lead created from transcript, owner alert tagged `(voicemail)`.
   - **Owner answers**: call, pick up on your cell, hang up. Expect: no SMS, no Lead — normal call.
6. Verify DB state after each scenario:
   ```bash
   python -c "from app.database import SessionLocal; from app.models import *; \
   db=SessionLocal(); \
   [print('CALL', c.id, c.from_number, c.status, c.dial_status, 'after_hours=', c.is_after_hours) for c in db.query(CallEvent).order_by(CallEvent.id.desc()).limit(3)]; \
   [print('LEAD', l.id, l.sender_name, l.phone, l.category, 'urg=', l.urgency_score) for l in db.query(Lead).filter(Lead.source=='phone').order_by(Lead.id.desc()).limit(3)]; \
   [print('SMS', s.purpose, s.to_number, s.status) for s in db.query(SmsNotification).order_by(SmsNotification.id.desc()).limit(5)]"
   ```

**Common gotchas:**
- Ngrok free-tier URL rotates on every restart → the number's Twilio webhook URLs go stale. Workaround: re-run provisioning, OR update the number's webhook URLs in the Twilio Console, OR upgrade to ngrok paid for a reserved domain.
- Trial accounts: Twilio prefixes every call with "You are receiving a call from a Twilio trial account" — annoying but harmless.
- `APP_ENV=production` turns on webhook signature validation. Keep it at `development` or empty during local ngrok testing or every webhook will 403.
- Outbound SMS cost on trial is covered by the $15 credit but SMS to unverified numbers silently fails. Check `sms_notifications.status='failed'` + `error_message` column for Twilio's rejection reason.
- If `PUBLIC_BASE_URL` is not set when provisioning runs, `/api/phone/provision` returns 500 with "PUBLIC_BASE_URL not configured". Set it before clicking "Use this".
