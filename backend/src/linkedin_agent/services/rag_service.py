from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..infra.embeddings import get_embedder


def embed(text: str) -> list[float]:
    return get_embedder().embed(text)


def add_knowledge(db: Session, user_id: int, content: str, source: str = "manual"):
    item = models.ProfileKnowledge(user_id=user_id, content=content, source=source, embedding=embed(content))
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def search_knowledge(db: Session, user_id: int, query: str, top_k: int = 3) -> list[str]:
    rows = db.scalars(
        select(models.ProfileKnowledge)
        .where(models.ProfileKnowledge.user_id == user_id)
        .order_by(models.ProfileKnowledge.embedding.cosine_distance(embed(query)))
        .limit(top_k)
    ).all()
    return [r.content for r in rows]


def list_knowledge(db: Session, user_id: int):
    return (
        db.query(models.ProfileKnowledge)
        .filter(models.ProfileKnowledge.user_id == user_id)
        .order_by(models.ProfileKnowledge.created_at.desc())
        .all()
    )


def delete_knowledge(db: Session, user_id: int, item_id: int) -> bool:
    row = (
        db.query(models.ProfileKnowledge)
        .filter(models.ProfileKnowledge.id == item_id, models.ProfileKnowledge.user_id == user_id)
        .first()
    )
    if not row:
        return False
    db.delete(row)
    db.commit()
    return True
