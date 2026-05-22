# reqlinqo Deployment

## Railway (recommended for MVP)

### 1. Push to GitHub

```bash
git init
git add -A
git commit -m "Initial commit"
gh repo create reqlinqo --private --push
```

### 2. Connect Railway

1. Go to [railway.app](https://railway.app), sign in with GitHub
2. New Project → Deploy from GitHub Repo → select reqlinqo
3. Railway auto-detects the Dockerfile

### 3. Add a persistent volume

**Critical** — without this, your SQLite DB and photos are lost on every deploy.

1. In the Railway service, go to Settings → Volumes
2. Add volume: mount path `/app/data`, size 1GB ($0.25/mo)
3. This persists the SQLite database, contact requests, and uploaded photos

### 4. Set environment variables

In Railway → Variables, add:

```env
APP_ENV=production
PUBLIC_BASE_URL=https://your-app.up.railway.app
SESSION_SECRET=<generate with: python -c "import secrets; print(secrets.token_hex(32))">
DATABASE_URL=${{Postgres.DATABASE_URL}}

# Google OAuth (required for Gmail integration)
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret

# Claude AI (required for lead classification)
LLM_PROVIDER=anthropic
LLM_API_KEY=your-anthropic-api-key

# Optional: Twilio SMS alerts
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM_NUMBER=
SMS_ALERT_TO_NUMBER=
```

### 5. Update Google OAuth redirect URI

In [Google Cloud Console](https://console.cloud.google.com/apis/credentials):
1. Edit your OAuth 2.0 Client ID
2. Add authorized redirect URI: `https://your-app.up.railway.app/auth/google/callback`
3. Add authorized JavaScript origin: `https://your-app.up.railway.app`

### 6. Deploy

Railway deploys automatically on push to main. The Dockerfile runs `alembic upgrade head` before starting the server, so migrations are applied on every deploy.

### Estimated cost

- Railway Hobby plan: $5/mo
- Volume (1GB): $0.25/mo
- Total: ~$5.25/mo

---

## Self-hosted (Raspberry Pi / VPS)

### Required env

```env
APP_ENV=production
APP_HOST=127.0.0.1
APP_PORT=8081
PUBLIC_BASE_URL=https://your-domain.example
SESSION_SECRET=<random 64-char hex>
DATABASE_URL=postgresql+psycopg://leadrelay:<password>@localhost:5432/leadrelay
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret
LLM_PROVIDER=anthropic
LLM_API_KEY=your-anthropic-api-key
```

### Docker Compose

```bash
docker compose up -d
```

This starts:
- `leadrelay` — main app on port 8081
- `inbox-poller` — polls Gmail every 60s (if using IMAP fallback)

Database data persists in the `leadrelay-postgres` Docker volume. Photos and local artifacts persist in `leadrelay-data`.

### Reverse proxy

Put nginx or Caddy in front for TLS. Config files are in `deploy/nginx/` and `deploy/Caddyfile`.

### Systemd (without Docker)

```bash
sudo cp deploy/systemd/leadrelay.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now leadrelay
```

For inbox polling:
```bash
sudo cp deploy/systemd/leadrelay-inbox-poll.service /etc/systemd/system/
sudo cp deploy/systemd/leadrelay-inbox-poll.timer /etc/systemd/system/
sudo systemctl enable --now leadrelay-inbox-poll.timer
```

---

## Post-deploy checklist

- [ ] Visit `/health` to confirm the app is running
- [ ] Register an account at `/register`
- [ ] Connect Gmail at `/setup` or `/settings`
- [ ] Send a test email to your connected Gmail
- [ ] Trigger `/mailbox/poll` to ingest it
- [ ] Verify the lead appears in `/review`
- [ ] Configure scheduling availability in `/settings` if desired
- [ ] Seed demo leads: `POST /demo/seed` (development mode only)

## Seed demo leads (authenticated)

```bash
curl -X POST https://your-app.up.railway.app/demo/seed \
  -H "Cookie: session=your-session-cookie"
```
