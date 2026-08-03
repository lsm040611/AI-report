"""생성 위임 큐.

파이썬 코드는 LLM을 호출하지 않는다. 생성이 필요한 작업만 여기서
외부 자동화(n8n 등)로 꺼내가고, 결과를 콜백으로 되돌려받는다.
되돌아온 결과는 R-16 검사를 통과해야만 저장된다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import Card, Handoff
from pipeline.rules.report import r16_verify_generated

router = APIRouter(prefix="/handoff", tags=["handoff"])


class Callback(BaseModel):
    text: str
    evidence: list[dict]      # [{"quote": "...", "source_cell": "G7"}]


@router.get("/pending")
def pending(rule_id: str | None = None, limit: int = 50,
            db: Session = Depends(get_db)):
    """외부 워크플로가 폴링해 가져가는 작업 목록."""
    q = db.query(Handoff).filter(Handoff.status == "pending")
    if rule_id:
        q = q.filter(Handoff.rule_id == rule_id)
    return [{"id": h.id, "card_id": h.card_id, "rule_id": h.rule_id,
             "task": h.task, "payload": h.payload} for h in q.limit(limit)]


@router.post("/{handoff_id}/callback")
def callback(handoff_id: int, body: Callback, db: Session = Depends(get_db)):
    """생성 결과 반환. 근거 없는 문장은 여기서 거부된다(R-16)."""
    h = db.get(Handoff, handoff_id)
    if not h:
        raise HTTPException(404, "handoff 없음")
    card = db.get(Card, h.card_id)

    ok, reason = r16_verify_generated(body.model_dump(), card.card_json)
    h.result = body.model_dump()
    h.status = "returned" if ok else "rejected"
    h.reject_reason = None if ok else reason
    db.commit()

    if not ok:
        raise HTTPException(422, f"R-16 위반 — {reason}")
    return {"ok": True, "status": h.status,
            "next": "검수 관문에서 원문과 대조 후 accept"}


@router.post("/{handoff_id}/accept")
def accept(handoff_id: int, operator: str, db: Session = Depends(get_db)):
    """사람이 원문과 나란히 대조한 뒤에만 카드에 반영된다(R-16 3단계)."""
    h = db.get(Handoff, handoff_id)
    if not h or h.status != "returned":
        raise HTTPException(400, "반환된 결과가 없습니다")

    card = db.get(Card, h.card_id)
    data = dict(card.card_json)
    data.setdefault("generated", []).append({
        "rule_id": h.rule_id, "task": h.task,
        "text": h.result["text"], "evidence": h.result["evidence"],
        "accepted_by": operator,
    })
    card.card_json = data
    h.status = "accepted"
    db.commit()
    return {"ok": True}
