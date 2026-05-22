# Relinqo Launch Night Runbook

Goal: make the app safe enough to promote tonight, verify the paid path, then send a small batch of one-to-one founder emails.

## 1. Automated Safety Checks

Run locally before deploying:

```bash
python -m pytest tests -q
python scripts/launch_night_check.py --base-url https://www.relinqo.com
```

Current local baseline after this pass:

- `58 passed` in the test suite.
- Production `/health` is alive.
- Production `/auth/rescue` returns `404`.
- Logged-out `/leads` and `/stats` return `401`.
- Unsigned Twilio voice/SMS webhooks return `403`.
- No obvious live/test secret values were found in tracked files.

Known launch blockers or follow-ups:

- Deploy the server-side auth gating change for `/review`, `/analytics`, `/pipeline`, `/templates`, `/setup`, and `/settings`; production still serves those HTML shells until this code is deployed.
- Configure `STRIPE_WEBHOOK_SECRET` on Doteasy. The current public `/stripe/webhook` response is `Stripe webhook secret is not configured`, so subscription webhooks will not update workspace billing state correctly.

## 2. Production Workspace Readiness

Log into the owner workspace and open `/settings`.

Use the Launch Checklist card as the source of truth. Required before a real pilot:

- Business profile is ready.
- Gmail is connected.
- Billing or pilot comp is active.
- Owner alert destination is configured.
- Owner SMS alert is verified.
- Phone rescue number is active.
- Human review is on.
- Automation is not paused.

Keep human review enabled for the first customer. Do not enable fully unattended auto-send during the first pilot.

## 3. Stripe Test

Stripe stays in test mode tonight.

1. Use a QA workspace.
2. Start Checkout from `/settings`.
3. Pay with test card `4242 4242 4242 4242`, any future expiry, any CVC.
4. Confirm the browser returns to `/settings?checkout=success`.
5. Confirm `/api/billing/status` shows active billing.
6. Confirm Customer Portal opens from "Manage billing."
7. In Stripe test dashboard, confirm:
   - Checkout Session completed.
   - Customer created.
   - Subscription active.
   - Webhook delivered to `/stripe/webhook`.

If webhooks still show the missing-secret error, set the Doteasy `STRIPE_WEBHOOK_SECRET` from Stripe's test webhook endpoint signing secret, restart the app, and rerun `scripts/launch_night_check.py`.

## 4. Core Product Smoke Test

Gmail:

- Reconnect Gmail if the account predates the Calendar scope.
- Send a real test lead email into the connected inbox.
- Poll inbox or wait for the scheduler.
- Verify lead created, classified, summarized, and drafted.
- Manually send the reply to a test inbox and confirm receipt.

SMS/phone:

- Call `+17822121292` from a separate verified caller phone.
- Do not answer the owner phone.
- Confirm caller gets outreach SMS.
- Reply by SMS with an urgent service request.
- Confirm lead appears in dashboard.
- Confirm owner alert SMS arrives at `+18254401394`.
- If owner alert does not arrive, stop customer onboarding and inspect Doteasy/Twilio logs.

Pipeline/templates/scheduling/reviews:

- Move a test lead through New, Contacted, Quoted, Scheduled, Won.
- Verify outcome mirrors pipeline stage.
- Add deal value, tags, star, and internal note.
- Create the three starter templates from `OUTREACH_EMAILS.md`.
- Enable scheduling, add availability, book one slot from `/book/{token}`, and confirm dashboard plus Google Calendar.
- Add a conflicting calendar event and confirm the slot disappears.
- Set review delay to `0h`, add a test review URL, mark test lead Won, run `/api/review-requests/run`, and confirm the review request behavior.

## 5. Outreach Batch

Use `data/outreach_prospects.csv` as the tracker.

Rules for tonight:

- Send one-to-one founder emails manually from a trusted Gmail account.
- Send 5 first, wait 15-20 minutes, then send 15 more only if there are no bounce/spam issues.
- Use only public business emails listed by the business.
- Include identity, a real contact address, and an opt-out line.
- CTA is a free pilot or one-lead demo, not "buy now."

Success tonight:

- Automated safety checks pass after deployment and Stripe webhook configuration.
- Stripe test checkout and portal work.
- Gmail ingest plus manual reply works.
- Owner-alert SMS either passes or is recorded as the one customer-onboarding blocker.
- 20 high-quality prospect emails are sent and tracked.
