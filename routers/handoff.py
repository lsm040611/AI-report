"""생성 위임 큐.

규칙 코드는 LLM을 호출하지 않는다. 생성이 필요한 작업만 여기에 쌓이고,
generation/worker.py(내장 워커) 또는 외부 자동화가 가져간다.
어느 쪽이든 돌아온 결과는 R-16 검사를 통과해야만 저장된다.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from database import get_db
from generation import drain
from generation.worker import engine_name
from models import Card, Handoff
from pipeline.rules.base import EVIDENCE_REQUIRED
from pipeline.rules.report import r16_verify_generated

router = APIRouter(prefix="/handoff", tags=["handoff"])


class Callback(BaseModel):
    """외부 생성 결과.

    작업마다 딸려 오는 필드가 다르다 — R-05 는 `spans`, 암기 문장은 `parts`·`closing`.
    추가 필드를 그대로 통과시켜야 외부 자동화(n8n 등)도 내장 워커와 같은 일을 할 수 있다.
    """
    model_config = ConfigDict(extra="allow")

    text: str
    evidence: List[dict] = []     # [{"quote": "...", "why": "..."}]
    engine: Optional[str] = None


@router.get("/pending")
def pending(rule_id: Optional[str] = None, limit: int = 50,
            db: Session = Depends(get_db)):
    """외부 워크플로가 폴링해 가져가는 작업 목록."""
    q = db.query(Handoff).filter(Handoff.status == "pending")
    if rule_id:
        q = q.filter(Handoff.rule_id == rule_id)
    return [{"id": h.id, "card_id": h.card_id, "rule_id": h.rule_id,
             "task": h.task, "payload": h.payload,
             "evidence_required": h.rule_id in EVIDENCE_REQUIRED}
            for h in q.limit(limit)]


@router.post("/run")
def run(upload_id: Optional[int] = None, auto_accept: bool = True,
        operator: str = "내장 워커", db: Session = Depends(get_db)):
    """내장 워커로 큐를 소진한다. 키가 없으면 목 모드로 돈다."""
    return drain(db, upload_id=upload_id, operator=operator, auto_accept=auto_accept)


@router.get("/engine")
def engine():
    return {"engine": engine_name()}


@router.post("/{handoff_id}/callback")
def callback(handoff_id: int, body: Callback, db: Session = Depends(get_db)):
    """외부 생성 결과 반환. 근거 없는 문장은 여기서 거부된다(R-16)."""
    h = db.get(Handoff, handoff_id)
    if not h:
        raise HTTPException(404, "handoff 없음")
    card = db.get(Card, h.card_id)
    if not card:
        raise HTTPException(404, "카드 없음")

    payload = body.model_dump()
    ok, reason = r16_verify_generated(
        payload, card.card_json, require_evidence=h.rule_id in EVIDENCE_REQUIRED)

    h.result = payload
    h.status = "returned" if ok else "rejected"
    h.reject_reason = None if ok else reason
    db.commit()

    if not ok:
        raise HTTPException(422, f"R-16 위반 — {reason}")
    return {"ok": True, "status": h.status,
            "next": f"POST /handoff/{h.id}/accept — 원문과 대조 후 수락"}


@router.post("/{handoff_id}/accept")
def accept(handoff_id: int, operator: str, db: Session = Depends(get_db)):
    """사람이 원문과 나란히 대조한 뒤에만 카드에 반영된다(R-16 3단계)."""
    from generation.runner import _accept          # 수락 로직은 한 곳에만 둔다

    h = db.get(Handoff, handoff_id)
    if not h or h.status != "returned":
        raise HTTPException(400, "반환된 결과가 없습니다")
    card = db.get(Card, h.card_id)
    if not card:
        raise HTTPException(404, "카드 없음")

    _accept(db, h, card, h.result or {}, operator)
    db.commit()
    return {"ok": True, "status": h.status}


@router.get("/{handoff_id}")
def get_handoff(handoff_id: int, db: Session = Depends(get_db)):
    h = db.get(Handoff, handoff_id)
    if not h:
        raise HTTPException(404, "handoff 없음")
    return {"id": h.id, "card_id": h.card_id, "rule_id": h.rule_id,
            "task": h.task, "status": h.status, "payload": h.payload,
            "result": h.result, "reject_reason": h.reject_reason}
