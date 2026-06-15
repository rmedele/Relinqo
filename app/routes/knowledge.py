from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import get_current_user, get_org_settings
from app.database import get_db
from app.models import BusinessKnowledgeDocument, OrgSettings, User
from app.rag import format_knowledge_context, retrieve_business_knowledge
from app.schemas import (
    BusinessKnowledgeCreate,
    BusinessKnowledgeResponse,
    BusinessKnowledgeUpdate,
    BusinessKnowledgeSearchResponse,
)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("", response_model=list[BusinessKnowledgeResponse])
def list_knowledge(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    active_only: bool = True,
):
    q = db.query(BusinessKnowledgeDocument).filter(BusinessKnowledgeDocument.org_id == user.org_id)
    if active_only:
        q = q.filter(BusinessKnowledgeDocument.is_active == True)
    return q.order_by(BusinessKnowledgeDocument.updated_at.desc()).all()


@router.post("", response_model=BusinessKnowledgeResponse)
def create_knowledge(
    payload: BusinessKnowledgeCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = BusinessKnowledgeDocument(org_id=user.org_id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{document_id}", response_model=BusinessKnowledgeResponse)
def update_knowledge(
    document_id: int,
    payload: BusinessKnowledgeUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.query(BusinessKnowledgeDocument).filter(
        BusinessKnowledgeDocument.id == document_id,
        BusinessKnowledgeDocument.org_id == user.org_id,
    ).first()
    if not row:
        raise HTTPException(404, "Knowledge document not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{document_id}")
def delete_knowledge(
    document_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = db.query(BusinessKnowledgeDocument).filter(
        BusinessKnowledgeDocument.id == document_id,
        BusinessKnowledgeDocument.org_id == user.org_id,
    ).first()
    if not row:
        raise HTTPException(404, "Knowledge document not found")
    row.is_active = False
    db.commit()
    return {"ok": True}


@router.get("/search", response_model=BusinessKnowledgeSearchResponse)
def search_knowledge(
    q: str = Query(..., min_length=2),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    org_settings: OrgSettings = Depends(get_org_settings),
):
    hits = retrieve_business_knowledge(db, user.org_id, q, org_settings=org_settings)
    return BusinessKnowledgeSearchResponse(
        query=q,
        context=format_knowledge_context(hits),
        hits=[
            {
                "title": hit.title,
                "category": hit.category,
                "source": hit.source,
                "score": hit.score,
                "content": hit.content,
            }
            for hit in hits
        ],
    )
