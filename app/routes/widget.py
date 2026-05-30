import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.auth import org_can_use_automation
from app.database import get_db
from app.models import Organization, OrgSettings
from app.rate_limit import RateLimiter
from app.routes.leads import ingest_lead
from app.schemas import LeadIngestRequest, WidgetLeadRequest
from app.widget import verify_widget_token

logger = logging.getLogger(__name__)
router = APIRouter(tags=["widget"])
widget_limiter = RateLimiter(max_requests=20, window_seconds=600)


WIDGET_JS = r"""
(function () {
  const currentScript = document.currentScript;
  const scriptUrl = currentScript ? currentScript.src : '';
  const endpoint = new URL('/api/public/widget/lead', scriptUrl || window.location.href).toString();
  const baseWorkspace = currentScript ? currentScript.dataset.workspace : '';
  const baseToken = currentScript ? currentScript.dataset.token : '';
  const containers = document.querySelectorAll('[data-relinqo-widget]');
  const targets = containers.length ? containers : [currentScript].filter(Boolean);

  const css = `
    .relinqo-widget{box-sizing:border-box;width:100%;max-width:520px;border:1px solid #d7dde8;border-radius:8px;padding:18px;background:#fff;color:#111827;font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;box-shadow:0 10px 30px rgba(15,23,42,.08)}
    .relinqo-widget *{box-sizing:border-box}
    .relinqo-widget h3{margin:0 0 6px;font-size:20px;line-height:1.25;color:#0f172a}
    .relinqo-widget p{margin:0 0 14px;color:#475569;font-size:14px;line-height:1.45}
    .relinqo-widget label{display:block;margin:0 0 10px;font-size:13px;font-weight:700;color:#334155}
    .relinqo-widget input,.relinqo-widget textarea{width:100%;margin-top:5px;border:1px solid #cbd5e1;border-radius:6px;padding:10px 11px;font:inherit;font-size:15px;color:#0f172a;background:#fff}
    .relinqo-widget textarea{min-height:108px;resize:vertical}
    .relinqo-widget button{border:0;border-radius:6px;background:#14532d;color:#fff;padding:10px 14px;font-weight:800;font-size:15px;cursor:pointer}
    .relinqo-widget button:disabled{opacity:.65;cursor:wait}
    .relinqo-widget .relinqo-row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
    .relinqo-widget .relinqo-status{margin-top:10px;font-size:14px;font-weight:700}
    .relinqo-widget .relinqo-status.ok{color:#166534}
    .relinqo-widget .relinqo-status.err{color:#b91c1c}
    .relinqo-widget .relinqo-hp{position:absolute;left:-10000px;top:auto;width:1px;height:1px;overflow:hidden}
    @media(max-width:560px){.relinqo-widget .relinqo-row{grid-template-columns:1fr}}
  `;

  if (!document.getElementById('relinqo-widget-style')) {
    const style = document.createElement('style');
    style.id = 'relinqo-widget-style';
    style.textContent = css;
    document.head.appendChild(style);
  }

  function text(node, fallback) {
    return node && node.dataset ? node.dataset.title || fallback : fallback;
  }

  function render(target) {
    const workspace = target.dataset.workspace || baseWorkspace;
    const token = target.dataset.token || baseToken;
    const title = text(target, 'Request a fast quote');
    const subtitle = target.dataset.subtitle || 'Tell us what you need. We will route it to the right person.';
    const button = target.dataset.button || 'Send request';
    const widget = document.createElement('form');
    widget.className = 'relinqo-widget';
    widget.innerHTML = `
      <h3></h3>
      <p></p>
      <div class="relinqo-row">
        <label>Name<input name="name" autocomplete="name" placeholder="Your name"></label>
        <label>Email<input name="email" type="email" autocomplete="email" placeholder="you@example.com" required></label>
      </div>
      <div class="relinqo-row">
        <label>Phone<input name="phone" autocomplete="tel" placeholder="(555) 555-0100"></label>
        <label>Service<input name="service" placeholder="Plumbing, HVAC, roofing..."></label>
      </div>
      <label>How can we help?<textarea name="message" required minlength="8" placeholder="Briefly describe the job, location, and urgency."></textarea></label>
      <label class="relinqo-hp">Company website<input name="company_website" tabindex="-1" autocomplete="off"></label>
      <button type="submit"></button>
      <div class="relinqo-status" aria-live="polite"></div>
    `;
    widget.querySelector('h3').textContent = title;
    widget.querySelector('p').textContent = subtitle;
    widget.querySelector('button').textContent = button;

    widget.addEventListener('submit', async (event) => {
      event.preventDefault();
      const submit = widget.querySelector('button');
      const status = widget.querySelector('.relinqo-status');
      const form = new FormData(widget);
      const payload = {
        workspace,
        token,
        name: form.get('name') || null,
        email: form.get('email'),
        phone: form.get('phone') || null,
        service: form.get('service') || null,
        message: form.get('message'),
        company_website: form.get('company_website') || null,
        page_url: window.location.href,
        source: 'website_widget'
      };
      submit.disabled = true;
      status.className = 'relinqo-status';
      status.textContent = 'Sending...';
      try {
        const response = await fetch(endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'text/plain;charset=UTF-8' },
          body: JSON.stringify(payload),
          credentials: 'omit'
        });
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error(data.detail || 'Could not send request.');
        widget.reset();
        status.className = 'relinqo-status ok';
        status.textContent = 'Sent. We will follow up shortly.';
      } catch (error) {
        status.className = 'relinqo-status err';
        status.textContent = error.message || 'Could not send request.';
      } finally {
        submit.disabled = false;
      }
    });

    if (target.tagName && target.tagName.toLowerCase() === 'script') {
      target.insertAdjacentElement('afterend', widget);
    } else {
      target.innerHTML = '';
      target.appendChild(widget);
    }
  }

  targets.forEach(render);
})();
""".strip()


def _cors_headers() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Max-Age": "86400",
    }


async def _parse_widget_payload(request: Request) -> WidgetLeadRequest:
    content_type = request.headers.get("content-type", "")
    try:
        if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
            form = await request.form()
            raw = dict(form)
        else:
            body = (await request.body()).decode("utf-8")
            raw = json.loads(body) if body else {}
        return WidgetLeadRequest.model_validate(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail="Invalid widget lead payload") from exc


@router.get("/api/widget/embed.js")
def widget_embed_script():
    return Response(
        content=WIDGET_JS,
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.options("/api/public/widget/lead")
def widget_lead_options():
    return Response(status_code=204, headers=_cors_headers())


@router.post("/api/public/widget/lead")
async def public_widget_lead(request: Request, db: Session = Depends(get_db)):
    widget_limiter.check(request)
    try:
        payload = await _parse_widget_payload(request)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=_cors_headers())

    if payload.company_website:
        return JSONResponse({"ok": True, "skipped": True}, headers=_cors_headers())

    org = db.query(Organization).filter(Organization.slug == payload.workspace).first()
    if not org:
        return JSONResponse({"detail": "Widget workspace not found"}, status_code=404, headers=_cors_headers())
    if not verify_widget_token(org, payload.token):
        return JSONResponse({"detail": "Invalid widget token"}, status_code=401, headers=_cors_headers())

    org_settings = db.query(OrgSettings).filter(OrgSettings.org_id == org.id).first()
    if not org_can_use_automation(org, org_settings):
        return JSONResponse({"detail": "Workspace is not active"}, status_code=402, headers=_cors_headers())

    service = (payload.service or "").strip()
    subject = f"{service} lead from website" if service else "Website lead"
    details = [
        payload.message.strip(),
        f"Phone: {payload.phone.strip()}" if payload.phone else "",
        f"Service: {service}" if service else "",
        f"Page: {payload.page_url}" if payload.page_url else "",
    ]
    body = "\n\n".join(item for item in details if item)
    source = payload.source.strip() or "website_widget"

    lead = ingest_lead(
        LeadIngestRequest(
            source=source,
            sender_name=(payload.name or "").strip() or None,
            sender_email=payload.email,
            subject=subject,
            body=body,
        ),
        db,
        org_id=org.id,
        org_settings=org_settings,
    )
    logger.info("Widget lead created org_id=%s lead_id=%s", org.id, lead.id)
    return JSONResponse(
        {"ok": True, "lead_id": lead.id, "status": lead.status},
        headers=_cors_headers(),
    )
