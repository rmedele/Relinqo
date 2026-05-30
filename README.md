# relinqo

relinqo is an AI-powered inbox and missed-call rescue system for local service businesses. It connects to the owner's Gmail, classifies new inquiries, drafts fast replies, alerts the owner by SMS for hot leads, captures phone/SMS leads through Twilio, offers booking links, tracks deals in a pipeline, and sends review requests after won jobs.

Production is currently hosted on Doteasy/cPanel at `https://www.relinqo.com` with FastAPI, Passenger/WSGI-style hosting, and MySQL via `mysql+pymysql`.

## Run Locally

1. Create and activate a virtual environment.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install dependencies.

```powershell
python -m pip install -r requirements.txt -r requirements-dev.txt
```

3. Copy `.env.example` to `.env` and set at least `SESSION_SECRET`, `DATABASE_URL`, `PUBLIC_BASE_URL`, `LLM_PROVIDER`, and `LLM_API_KEY`. SQLite is fine locally.

```env
DATABASE_URL=sqlite:///./data/leadrelay.db
PUBLIC_BASE_URL=http://127.0.0.1:8001
APP_ENV=development
```

4. Start the app.

```powershell
uvicorn app.main:app --reload --port 8001
```

5. Open `http://127.0.0.1:8001`.

## New User Setup

Use this as the step-by-step path for a fresh pilot workspace.

1. Register a workspace at `/register`.
2. Start or bypass billing from `/settings`. For internal pilots, use the admin billing bypass endpoint. For Stripe QA, use Stripe test card `4242 4242 4242 4242`.
3. Fill in the business profile: name, services, area, hours, phone, tone, and reply signature.
4. Connect Gmail from `/setup` or `/settings`. Existing connected workspaces should reconnect after Calendar scope changes.
5. Configure missed-call rescue in `/settings`: enter the owner's phone, search or adopt a Twilio number, activate conditional forwarding if needed, then run the forwarding test.
6. Set weekly booking availability in `/settings` and enable smart scheduling.
7. Add the Google review URL and turn on Reviews on autopilot if the pilot will use review requests.
8. Send a test lead from `/setup`, the live demo, a forwarded email, or the Twilio phone simulation script.
9. Confirm the lead appears in `/review`, has a useful draft reply, and can be moved through `/pipeline`.
10. Move a lead to Won and confirm the review request queue shows the scheduled request.

The `/api/settings/readiness` endpoint powers the launch checklist in Settings and is the best quick signal that a workspace is ready.

## Feature Smoke Paths

These are the mechanical processes that should keep working before any customer pilot.

- Gmail: connect OAuth, send a real inquiry, poll the mailbox, review the draft, and manually send the reply.
- Website widget: copy the snippet from `/settings`, submit a WordPress/Webflow-style test lead, and confirm the lead appears in `/review`.
- Phone: call the configured Twilio number, miss the owner call, reply to the outreach SMS, and confirm the dashboard lead plus owner SMS alert.
- Scheduling: enable availability, open `/book/{token}`, book a slot, and confirm the slot disappears after booking.
- Pipeline: add deal value, tags, and notes; move the card through New, Contacted, Quoted, Scheduled, Won, and Lost.
- Templates: create a template, insert it on a lead, save edits, and confirm `use_count` increments.
- Reviews on autopilot: set review delay to `0`, mark a test lead Won, run `POST /api/review-requests/run`, and verify the configured channel.
- Billing: complete test Checkout, open Customer Portal, and confirm `/api/billing/status`.

## Run the Full Test Suite

```powershell
python -m pytest tests -q
```

For phone-capture local E2E without Twilio or ngrok:

```powershell
python scripts\simulate_twilio_call.py
python scripts\simulate_twilio_call.py --after-hours --sms-reply "furnace broken, need help ASAP"
python scripts\simulate_twilio_call.py --with-dial --owner-phone=+15555550100
```

## Key Environment Variables

- `APP_ENV`, `APP_HOST`, `APP_PORT`, `PUBLIC_BASE_URL`
- `SESSION_SECRET`
- `DATABASE_URL`
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`
- `LLM_PROVIDER=anthropic`, `LLM_API_KEY`
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `SMS_ALERT_TO_NUMBER`
- `STRIPE_PUBLISHABLE_KEY`, `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `BILLING_ADMIN_TOKEN`
- `SMTP_*` and `IMAP_*` fallback settings
- `BUSINESS_*` defaults used when seeding new behavior

## Doteasy/cPanel Production Notes

Production runs on Doteasy/cPanel, not Railway. Keep production secrets only in the Doteasy environment panel or an untracked local `.env`.

- Runtime: FastAPI served on `APP_PORT=8081`.
- Database: MySQL, for example `mysql+pymysql://user:password@localhost/dariomed_relinqo?charset=utf8mb4`.
- Migrations: keep `RELINQO_RUN_MIGRATIONS_ON_STARTUP=false` on cPanel and run `python -m alembic upgrade head` manually after schema changes.
- Background scheduler: keep `RELINQO_BACKGROUND_SCHEDULER_ENABLED=false` for Passenger web workers and run scheduled jobs through explicit cron/service commands.
- Public host: `PUBLIC_BASE_URL=https://www.relinqo.com`.
- Google OAuth redirect: `https://www.relinqo.com/auth/google/callback`.
- Twilio webhooks: `/twilio/voice/incoming`, `/twilio/voice/call-status`, `/sms/webhook`, and `/sms/status`.
- Stripe test mode is currently expected until live billing is intentionally enabled.
- Integration setup: see `INTEGRATIONS.md` for Zapier, Make, WordPress, and Webflow widget setup.

## Project Map

- `app/main.py` - FastAPI app, public pages, middleware, scheduler wiring.
- `app/routes/leads.py` - lead ingestion, review sends, stats, notes, templates, pipeline, review requests.
- `app/routes/settings.py` - org settings and readiness checklist.
- `app/routes/scheduling.py` - availability and public booking links.
- `app/routes/twilio_voice.py` - Twilio Programmable Voice intake.
- `app/routes/sms_webhook.py` - Twilio SMS replies, opt-out, status callbacks.
- `app/routes/phone_provisioning.py` - number search, provision, adopt, routing, forwarding tests.
- `app/routes/widget.py` - public WordPress/Webflow lead widget.
- `app/gmail.py`, `app/mailer.py`, `app/inbox_poll.py` - Gmail OAuth and email fallback.
- `app/calendar_sync.py` - Google Calendar event sync and FreeBusy filtering.
- `app/review_requests.py` - post-Won review request automation.
- `app/ui/` - vanilla HTML, CSS, and JavaScript dashboard.
- `tests/` - pytest coverage for backend mechanics, UI smoke checks, and launch safety.
