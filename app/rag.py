"""Business knowledge retrieval for lightweight RAG.

This is intentionally dependency-light: it uses the business profile plus
manual knowledge documents and ranks them with keyword overlap. The interface
can be swapped for vector embeddings later without changing lead ingestion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import BusinessKnowledgeDocument, OrgSettings

TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
STOPWORDS = {
    "the", "and", "for", "you", "your", "with", "that", "this", "from", "are", "can",
    "need", "what", "when", "where", "have", "has", "our", "will", "about", "into",
}


@dataclass
class KnowledgeHit:
    title: str
    content: str
    source: str
    category: str
    score: float


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in TOKEN_RE.findall(text or "") if len(t) > 2 and t.lower() not in STOPWORDS}


def business_profile_context(org_settings: OrgSettings | None) -> str:
    if not org_settings:
        return ""
    parts = []
    if org_settings.business_name:
        parts.append(f"Business: {org_settings.business_name}")
    if org_settings.business_services:
        parts.append(f"Services: {org_settings.business_services}")
    if org_settings.business_area:
        parts.append(f"Service area: {org_settings.business_area}")
    if org_settings.business_hours:
        parts.append(f"Hours: {org_settings.business_hours}")
    if org_settings.business_phone:
        parts.append(f"Phone: {org_settings.business_phone}")
    return "\n".join(parts)


def retrieve_business_knowledge(
    db: Session,
    org_id: int,
    query: str,
    org_settings: OrgSettings | None = None,
    limit: int = 5,
) -> list[KnowledgeHit]:
    query_terms = _tokens(query)
    rows = (
        db.query(BusinessKnowledgeDocument)
        .filter(
            BusinessKnowledgeDocument.org_id == org_id,
            BusinessKnowledgeDocument.is_active == True,
        )
        .order_by(BusinessKnowledgeDocument.updated_at.desc())
        .limit(200)
        .all()
    )
    hits: list[KnowledgeHit] = []
    for row in rows:
        text = f"{row.title} {row.category} {row.content}"
        terms = _tokens(text)
        overlap = query_terms & terms
        score = float(len(overlap))
        if row.category.lower() in query.lower():
            score += 1.5
        if score > 0 or not query_terms:
            hits.append(KnowledgeHit(row.title, row.content, row.source, row.category, score))

    hits.sort(key=lambda h: h.score, reverse=True)

    if org_settings:
        profile = business_profile_context(org_settings)
        if profile:
            hits.insert(0, KnowledgeHit("Business profile", profile, "settings", "profile", 999.0))

    return hits[:limit]


def format_knowledge_context(hits: list[KnowledgeHit]) -> str:
    if not hits:
        return ""
    blocks = []
    for hit in hits:
        content = re.sub(r"\s+", " ", hit.content).strip()
        if len(content) > 900:
            content = content[:900].rstrip() + "..."
        blocks.append(f"[{hit.category}] {hit.title}: {content}")
    return "\n".join(blocks)
