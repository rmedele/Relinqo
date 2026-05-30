# relinqo Integrations

## Zapier and Make

Relinqo can send outbound events to Zapier Catch Hook URLs or Make Custom Webhook URLs.

1. In Zapier, create a Zap with **Webhooks by Zapier -> Catch Hook**.
2. Copy the Catch Hook URL.
3. In relinqo, open **Settings -> Outbound webhooks**.
4. Enable outbound webhooks, paste the hook URL, and keep the default events:
   `lead.created,booking.created,lead.won`.
5. Click **Send test webhook**, then finish mapping fields in Zapier.

Make follows the same pattern with **Webhooks -> Custom webhook**.

Relinqo sends JSON with this envelope:

```json
{
  "event": "lead.created",
  "delivery_id": "example",
  "org_id": 123,
  "sent_at": "2026-05-30T12:00:00+00:00",
  "data": {
    "id": 456,
    "sender_name": "Taylor Customer",
    "sender_email": "taylor@example.com",
    "phone": "780-555-0100",
    "category": "quote_request",
    "urgency_score": 3,
    "summary": "Customer needs a quote.",
    "status": "drafted"
  }
}
```

Headers:

- `X-Relinqo-Event`: event name
- `X-Relinqo-Delivery`: unique delivery id
- `X-Relinqo-Signature`: `sha256=...` HMAC signature when a signing secret is configured

Good first automations:

- `lead.created` -> create/update a row in Google Sheets.
- `lead.created` with `urgency_score >= 4` -> send Slack or Teams alert.
- `booking.created` -> create a CRM activity.
- `lead.won` -> create an invoice draft or review follow-up task.

## WordPress Widget

Use the website widget when a trades business wants a simple quote form on an existing WordPress site.

1. In relinqo, open **Settings -> Website widget**.
2. Copy the embed code.
3. In WordPress, add a **Custom HTML** block.
4. Paste the embed code and publish.

The default snippet looks like this:

```html
<div data-relinqo-widget data-workspace="workspace-slug" data-token="widget-token"></div>
<script src="https://www.relinqo.com/api/widget/embed.js" async></script>
```

Optional display customizations:

```html
<div
  data-relinqo-widget
  data-workspace="workspace-slug"
  data-token="widget-token"
  data-title="Request service"
  data-subtitle="Tell us what is going on and we will follow up fast."
  data-button="Send request"
></div>
<script src="https://www.relinqo.com/api/widget/embed.js" async></script>
```

## Webflow Widget

1. Add an **Embed** element to the Webflow page.
2. Paste the same widget code from **Settings -> Website widget**.
3. Publish the site.

The widget posts to `/api/public/widget/lead`, so it works from customer-owned domains without exposing the private API key. The widget token is scoped to public website intake; regenerating the workspace API key rotates the widget token too.

## Direct Public Widget POST

For custom sites that do not want the provided script, submit a POST request to:

```text
https://www.relinqo.com/api/public/widget/lead
```

Use a simple `text/plain` JSON body so browser requests do not need a CORS preflight:

```json
{
  "workspace": "workspace-slug",
  "token": "widget-token",
  "name": "Taylor Customer",
  "email": "taylor@example.com",
  "phone": "780-555-0100",
  "service": "plumbing",
  "message": "Kitchen sink is leaking and we need a quote.",
  "page_url": "https://example.com/contact",
  "source": "website_widget"
}
```
