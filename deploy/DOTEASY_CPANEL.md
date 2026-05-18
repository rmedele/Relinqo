# reqlinqo on Doteasy/cPanel

reqlinqo is a FastAPI application, not a static website. Uploading files to
`public_html` only serves the marketing files; login and dashboard routes need
cPanel to run the Python application through Setup Python App or Application
Manager.

## Required cPanel Feature

In cPanel, search for one of these:

- `Setup Python App`
- `Application Manager`

If neither exists, Doteasy shared hosting cannot run the full app from File
Manager alone. Use a host that can run Python/ASGI apps, or ask Doteasy support
to enable Python App support for the account.

## Upload Folder

Upload the full app into:

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
Application URL: your domain root, or a dedicated subdomain such as app.yourdomain.com
Application startup file: passenger_wsgi.py
Application entry point: application
```

The cleanest deployment is the domain root or a dedicated app subdomain. If you
deploy at a subpath, absolute URLs such as `/login` and `/auth/login` may not
resolve through the Python app.

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
PUBLIC_BASE_URL=https://your-domain.example
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
REQLINQO_RUN_MIGRATIONS_ON_STARTUP=true
```

Make sure `/home/dariomed/leadrelay/data` is writable by the cPanel account.

## Smoke Test

After boot, visit:

```text
/health
```

Then test:

```text
/login
/auth/login
```

`/health` should return JSON. If `/health` returns an HTML app shell or a 404
from another backend, the domain is not routed to this FastAPI app yet.

Update Google OAuth redirect URI:

```text
https://your-domain.example/auth/google/callback
```

Update Twilio webhooks to the same public base URL.
