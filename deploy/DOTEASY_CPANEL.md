# relinqo on Doteasy/cPanel

relinqo is a FastAPI application, not a static website. Uploading files to
`public_html` only works if cPanel also runs the Python application through
Setup Python App or Application Manager.

## Required cPanel Feature

In cPanel, search for one of these:

- `Setup Python App`
- `Application Manager`

If neither exists, Doteasy shared hosting cannot run the full app from File
Manager alone. Use Railway for hosting and point the Doteasy domain at Railway.

## Upload Folder

Upload and extract `leadrelay-cpanel-full.zip` into:

```text
/home/dariomed/leadrelay
```

Do not put the app code directly inside `public_html` unless Doteasy's Python
App tool explicitly asks for that. A Python app root is usually outside
`public_html`.

## Python App Settings

Create a Python app with:

```text
Python version: 3.11
Application root: /home/dariomed/leadrelay
Application URL: your domain root, or /Reese if you really want the app there
Application startup file: passenger_wsgi.py
Application entry point: application
```

If you deploy at `/Reese`, some absolute URLs may still behave like root-level
paths. The cleanest deployment is the domain root or a dedicated subdomain such
as `app.yourdomain.com`.

## Dependencies

Install packages from:

```text
requirements.txt
```

The cPanel Python App page usually has a requirements installer. If Doteasy does
not provide that UI, ask support to install the requirements into the app's
virtualenv.

## Environment

Create a `.env` file in `/home/dariomed/leadrelay` or set environment variables
in the Python App UI:

```env
APP_ENV=production
PUBLIC_BASE_URL=https://www.relinqo.com
SESSION_SECRET=replace-with-a-long-random-secret
DATABASE_URL=sqlite:///./data/leadrelay.db
LLM_PROVIDER=anthropic
LLM_API_KEY=replace-me
GOOGLE_CLIENT_ID=replace-me
GOOGLE_CLIENT_SECRET=replace-me
TWILIO_ACCOUNT_SID=replace-me
TWILIO_AUTH_TOKEN=replace-me
TWILIO_FROM_NUMBER=replace-me
SMS_ALERT_TO_NUMBER=replace-me
RELINQO_RUN_MIGRATIONS_ON_STARTUP=false
```

Make sure `/home/dariomed/leadrelay/data` is writable by the cPanel account.

Passenger imports `passenger_wsgi.py` every time it boots a worker, so leave
`RELINQO_RUN_MIGRATIONS_ON_STARTUP=false` on Doteasy. Run migrations manually
after pulling code that changes the database:

```bash
python -m alembic upgrade head
```

## After Boot

Visit:

```text
/health
```

Then:

```text
/register
```

Update Google OAuth redirect URI:

```text
https://www.relinqo.com/auth/google/callback
```

Update Twilio webhooks to the same public base URL.
