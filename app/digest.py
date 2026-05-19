from datetime import datetime, timedelta, timezone

from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from app.config import settings
from app.mailer import send_email
from app.models import FollowUp, Lead, LeadActivity, OrgSettings


def build_daily_digest(db: Session, org_id: int | None = None) -> dict:
    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    cutoff = datetime(yesterday.year, yesterday.month, yesterday.day, tzinfo=timezone.utc)

    lead_q = db.query(Lead).filter(Lead.created_at >= cutoff)
    activity_q = db.query(LeadActivity).filter(LeadActivity.created_at >= cutoff)
    review_q = db.query(Lead).filter(Lead.status.in_(["new", "review_required", "drafted"]))
    followup_q = db.query(FollowUp).filter(FollowUp.status == "scheduled")

    if org_id is not None:
        lead_q = lead_q.filter(Lead.org_id == org_id)
        activity_q = activity_q.filter(LeadActivity.org_id == org_id)
        review_q = review_q.filter(Lead.org_id == org_id)
        followup_q = followup_q.filter(FollowUp.org_id == org_id)

    new_today = lead_q.all()
    activities = activity_q.all()
    urgent = [lead for lead in new_today if lead.urgency_score >= 5]
    auto_sent = [a for a in activities if a.activity_type == "auto_sent"]
    awaiting_review_count = review_q.count()
    spam_filtered = [lead for lead in new_today if lead.category == "spam"]
    followups_count = followup_q.count()

    top_leads = [
        {
            "id": lead.id,
            "category": lead.category,
            "sender_name": lead.sender_name or "Unknown",
            "sender_email": lead.sender_email,
            "summary": lead.summary,
            "status": lead.status,
            "urgency_score": lead.urgency_score,
        }
        for lead in sorted(new_today, key=lambda item: item.urgency_score, reverse=True)
        if lead.category != "spam"
    ][:8]

    return {
        "new_leads": len(new_today),
        "urgent_leads": len(urgent),
        "auto_replies_sent": len(auto_sent),
        "awaiting_review": awaiting_review_count,
        "spam_filtered": len(spam_filtered),
        "needs_followup": followups_count,
        "top_leads": top_leads,
    }


def send_daily_digest(summary: dict, org_settings: OrgSettings | None = None) -> tuple[bool, str]:
    biz = (org_settings.business_name if org_settings else settings.business_name) or "relinqo"
    digest_to = org_settings.digest_to_email if org_settings else settings.digest_to_email

    subject = f"{biz} — {summary['new_leads']} new leads | {summary['auto_replies_sent']} auto-replied"

    lines = [
        f"Good morning — here's your {biz} lead summary.\n",
        f"  New leads:         {summary['new_leads']}",
        f"  Auto-replied:      {summary['auto_replies_sent']}",
        f"  Urgent (alerted):  {summary['urgent_leads']}",
        f"  Awaiting review:   {summary['awaiting_review']}",
        f"  Spam filtered:     {summary['spam_filtered']}",
        f"  Follow-ups due:    {summary['needs_followup']}",
    ]

    top = summary.get("top_leads", [])
    if top:
        lines.append("\n--- Leads handled ---\n")
        for lead in top:
            urgency = "!!" if lead["urgency_score"] >= 5 else "!" if lead["urgency_score"] >= 4 else ""
            lines.append(
                f"  {urgency} #{lead['id']} [{lead['category']}] {lead['sender_name']} "
                f"<{lead['sender_email']}> — {lead['status']}"
            )
            if lead["summary"]:
                lines.append(f"     {lead['summary'][:120]}")
            lines.append("")

    if summary["awaiting_review"] > 0:
        lines.append(f"\n{summary['awaiting_review']} lead(s) need your review:")
        lines.append(f"  {settings.public_base_url}/review\n")

    if summary["auto_replies_sent"] > 0:
        lines.append(f"{summary['auto_replies_sent']} replies were sent automatically on your behalf overnight.")

    lines.append(f"\n— {biz} via relinqo")

    body = "\n".join(lines)
    return send_email(to_email=digest_to, subject=subject, body=body, org_settings=org_settings)


def build_weekly_summary(db: Session, org_id: int | None = None) -> dict:
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    two_weeks_ago = now - timedelta(days=14)

    def _q(model):
        q = db.query(model)
        if org_id is not None:
            q = q.filter(model.org_id == org_id)
        return q

    # This week's leads
    this_week = _q(Lead).filter(Lead.created_at >= week_ago).all()
    last_week = _q(Lead).filter(Lead.created_at >= two_weeks_ago, Lead.created_at < week_ago).all()

    sent_this_week = [l for l in this_week if l.status == "sent"]
    sent_last_week = [l for l in last_week if l.status == "sent"]

    # Response times
    def avg_response(leads):
        deltas = [(l.updated_at - l.created_at).total_seconds() / 60 for l in leads if l.status == "sent"]
        return round(sum(deltas) / len(deltas), 1) if deltas else None

    avg_this = avg_response(this_week)
    avg_last = avg_response(last_week)

    # Outcomes this week
    outcomes_this = _q(Lead).filter(Lead.outcome_at >= week_ago, Lead.outcome.isnot(None)).all()
    won = [l for l in outcomes_this if l.outcome == "won"]
    lost = [l for l in outcomes_this if l.outcome == "lost"]
    no_response = [l for l in outcomes_this if l.outcome == "no_response"]

    # Category breakdown
    by_category = {}
    for l in this_week:
        by_category[l.category] = by_category.get(l.category, 0) + 1

    # Auto vs manual
    auto_activities = _q(LeadActivity).filter(
        LeadActivity.created_at >= week_ago,
        LeadActivity.activity_type == "auto_sent",
    ).count()
    manual_activities = _q(LeadActivity).filter(
        LeadActivity.created_at >= week_ago,
        LeadActivity.activity_type == "reply_sent",
    ).count()

    return {
        "period_start": week_ago.strftime("%b %d"),
        "period_end": now.strftime("%b %d, %Y"),
        "total_leads": len(this_week),
        "total_leads_last_week": len(last_week),
        "sent_count": len(sent_this_week),
        "sent_last_week": len(sent_last_week),
        "avg_response_minutes": avg_this,
        "avg_response_last_week": avg_last,
        "auto_replies": auto_activities,
        "manual_replies": manual_activities,
        "won": len(won),
        "lost": len(lost),
        "no_response": len(no_response),
        "by_category": by_category,
        "spam_filtered": sum(1 for l in this_week if l.category == "spam"),
        "pending_review": _q(Lead).filter(Lead.status.in_(["new", "drafted", "review_required"])).count(),
        "followups_due": _q(FollowUp).filter(FollowUp.status == "scheduled").count(),
    }


def send_weekly_summary(summary: dict, org_settings: OrgSettings | None = None) -> tuple[bool, str]:
    biz = (org_settings.business_name if org_settings else settings.business_name) or "relinqo"
    digest_to = org_settings.digest_to_email if org_settings else settings.digest_to_email

    subject = f"{biz} — Weekly Report ({summary['period_start']} – {summary['period_end']})"

    def trend(current, previous, unit=""):
        if previous is None or current is None:
            return ""
        if previous == 0:
            return f" (new)" if current > 0 else ""
        diff = current - previous
        pct = round(diff / previous * 100)
        arrow = "↑" if diff > 0 else "↓" if diff < 0 else "→"
        return f" ({arrow} {abs(pct)}% vs last week)"

    lines = [
        f"Weekly summary for {biz} — {summary['period_start']} to {summary['period_end']}\n",
        "═══ VOLUME ═══",
        f"  New leads:        {summary['total_leads']}{trend(summary['total_leads'], summary['total_leads_last_week'])}",
        f"  Replies sent:     {summary['sent_count']}{trend(summary['sent_count'], summary['sent_last_week'])}",
        f"    Auto-replied:   {summary['auto_replies']}",
        f"    Manual:         {summary['manual_replies']}",
        f"  Spam filtered:    {summary['spam_filtered']}",
        "",
        "═══ RESPONSE TIME ═══",
        f"  Avg this week:    {summary['avg_response_minutes']}m" if summary['avg_response_minutes'] else "  Avg this week:    —",
        f"  Avg last week:    {summary['avg_response_last_week']}m" if summary['avg_response_last_week'] else "  Avg last week:    —",
    ]

    if summary['won'] or summary['lost'] or summary['no_response']:
        total_outcomes = summary['won'] + summary['lost'] + summary['no_response']
        close_pct = round(summary['won'] / total_outcomes * 100) if total_outcomes else 0
        lines += [
            "",
            "═══ OUTCOMES ═══",
            f"  Won:              {summary['won']}",
            f"  Lost:             {summary['lost']}",
            f"  No response:      {summary['no_response']}",
            f"  Close rate:       {close_pct}%",
        ]

    if summary['by_category']:
        lines += ["", "═══ BY CATEGORY ═══"]
        for cat, count in sorted(summary['by_category'].items(), key=lambda x: -x[1]):
            if cat != "spam":
                lines.append(f"  {cat.replace('_', ' ').title():20s} {count}")

    if summary['pending_review'] or summary['followups_due']:
        lines += [
            "",
            "═══ ACTION NEEDED ═══",
            f"  Pending review:   {summary['pending_review']}",
            f"  Follow-ups due:   {summary['followups_due']}",
        ]

    lines += [
        "",
        f"View your dashboard: {settings.public_base_url}/portal",
        f"\n— {biz} via relinqo",
    ]

    body = "\n".join(lines)
    return send_email(to_email=digest_to, subject=subject, body=body, org_settings=org_settings)
