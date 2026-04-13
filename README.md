# LeadRelay

## File tree
```text
lead-recovery-v1/
├── .env.example
├── README.md
├── requirements.txt
├── app/
│   ├── __init__.py
│   ├── alerts.py
│   ├── classifier.py
│   ├── config.py
│   ├── database.py
│   ├── digest.py
│   ├── email_parser.py
│   ├── followups.py
│   ├── main.py
│   ├── models.py
│   ├── reply_generator.py
│   ├── routes/
│   │   ├── __init__.py
│   │   └── leads.py
│   └── schemas.py
├── data/
├── scripts/
│   └── seed_demo.py
└── tests/
    ├── test_api.py
    ├── test_classifier.py
    └── test_parser.py
```

## What this app does
LeadRelay is a production-minded MVP for local service businesses that need help responding to inquiry emails fast without handing over their main mailbox. It ingests forwarded lead emails or webhook submissions, classifies the lead, stores it in SQLite, drafts a safe first reply, alerts the owner for hot leads, schedules follow-ups, and generates a daily digest.

## How the pieces work together
- `app/main.py` boots the FastAPI app and creates tables.
- `app/routes/leads.py` handles API endpoints.
- `app/email_parser.py` extracts name, phone, and location.
- `app/classifier.py` handles spam filtering plus deterministic classification, with a clear LLM extension point.
- `app/reply_generator.py` creates conservative reply drafts.
- `app/alerts.py` creates owner-alert records and email payloads.
- `app/followups.py` schedules follow-up records.
- `app/digest.py` builds the daily summary.
- `app/models.py` defines SQLite tables through SQLAlchemy.

## Environment variables
Copy `.env.example` to `.env` and adjust:
- `DATABASE_URL` SQLite path for local DB
- `HUMAN_REVIEW` defaults to true and should stay true for MVP
- `OWNER_ALERT_EMAIL` where urgent/hot lead alerts go
- `DIGEST_TO_EMAIL` daily digest target
- `SMTP_HOST` SMTP server hostname
- `SMTP_PORT` SMTP server port
- `SMTP_USERNAME` SMTP username/login
- `SMTP_PASSWORD` SMTP password
- `SMTP_USE_TLS` whether to use STARTTLS
- `SMTP_FROM_EMAIL` sender address used for alerts, digests, and replies
- `LLM_PROVIDER` and `LLM_API_KEY` reserved for future classifier integration
- `AUTO_SEND_CONFIDENCE_THRESHOLD` confidence needed before auto-send if human review is off

## HUMAN_REVIEW mode
Default is `HUMAN_REVIEW=true`.
That means lead replies are drafted and stored, but not automatically sent.
Owner alerts and daily digests are still allowed to send through SMTP.
The owner can review and then call:
- `POST /leads/{id}/review/send`

If `HUMAN_REVIEW=false`, LeadRelay can automatically send first-response emails for non-spam leads when confidence is at or above `AUTO_SEND_CONFIDENCE_THRESHOLD`.

## Local run guide
1. Create a venv:
```bash
python3 -m venv .venv
source .venv/bin/activate
```
2. Install deps:
```bash
pip install -r requirements.txt
```
3. Copy env file:
```bash
cp .env.example .env
```
4. Configure SMTP in `.env` if you want owner alerts, digest emails, or lead replies to actually send:
```env
SMTP_HOST=smtp.your-provider.com
SMTP_PORT=587
SMTP_USERNAME=your-user
SMTP_PASSWORD=your-password
SMTP_USE_TLS=true
SMTP_FROM_EMAIL=alerts@yourdomain.com
OWNER_ALERT_EMAIL=owner@yourdomain.com
DIGEST_TO_EMAIL=owner@yourdomain.com
```
5. Start the app:
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```
6. Open docs:
- `http://127.0.0.1:8080/docs`

## Sample ingest
```bash
curl -X POST http://127.0.0.1:8080/ingest-lead \
  -H 'Content-Type: application/json' \
  -d '{
    "source":"webhook",
    "sender_name":"Jamie",
    "sender_email":"jamie@example.com",
    "subject":"Need emergency plumber",
    "body":"Burst pipe in basement. Call me ASAP at 780-555-1212 in Edmonton."
  }'
```

## Seed demo data
```bash
python scripts/seed_demo.py
```

## Tests
```bash
pytest -q
```

## Deployment notes for MVP
- Keep this app on a dedicated VM, Pi, or small container.
- Feed it only forwarded lead emails or structured webhooks.
- Do not connect it to the full company mailbox.
- Keep `HUMAN_REVIEW=true` until templates and classification are proven.
- SMTP failures are logged and do not crash the app, but delivery should be tested before client rollout.
- Add a cron job later for follow-up processing and daily digest sending.

## Fastest v2 upgrades
1. Real outbound email sending with SMTP/provider abstraction
2. Inbox reply tracking and follow-up cancellation on customer reply
3. Gmail/Outlook OAuth integration for dedicated lead inboxes only
4. Better LLM classification layer with structured output
5. Simple dashboard UI for review, resend, close, and follow-up management

## What should improve before production
- stronger auth/admin controls
- actual SMTP delivery and retry handling
- better spam filtering
- reply approval audit trail
- real scheduler/worker for follow-ups and digest jobs
- rate limiting and request auth for ingest endpoints
