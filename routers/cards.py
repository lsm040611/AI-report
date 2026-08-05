"""검수 관문. 계약의 severity 4단계가 여기서 실제 게이트로 작동한다."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import Card, CompetencyMapping, PersonResolution
from pipeline.rules.base import is_sendable, max_severity

router = APIRouter(prefix="/cards", tags=["cards"])


class TypeConfirm(BaseModel):
    type: Optional[str] = None    # 담당자가 판정을 바꿀 수도 있다
    operator: str


class FlagResolve(BaseModel):
    decision: str                 # "approve" | "exclude"
    operator: str
    memo: Optional[str] = None


class MappingApprove(BaseModel):
    raw_name: str
    canonical: str
    operator: str


class PersonResolve(BaseModel):
    person_id: str
    operator: str


@router.get("/{card_id}")
def get_card(card_id: int, db: Session = Depends(get_db)):
    card = _get(db, card_id)
    ok, reason = is_sendable(card.card_json)
    return {"card": card.card_json, "sendable": ok, "blocked_by": reason or None}


@router.post("/{card_id}/source-type/confirm")
def confirm_type(card_id: int, body: TypeConfirm, db: Session = Depends(get_db)):
    """계약: 담당자 승인 전에는 다음 단계 진행 불가."""
    card = _get(db, card_id)
    data = dict(card.card_json)
    st = dict(data.get("source_type", {}))
    if body.type:
        st["type"] = body.type
        st["evidence"] = f"담당자 지정 ({body.operator})"
    st["confirmed_by_operator"] = True
    st["confirmed_by"] = body.operator
    data["source_type"] = st

    card.card_json = data
    card.source_type = st.get("type")
    card.type_confirmed = True
    db.commit()
    return {"ok": True, "source_type": st}


@router.post("/{card_id}/flags/{index}/resolve")
def resolve_flag(card_id: int, index: int, body: FlagResolve,
                 db: Session = Depends(get_db)):
    """hold 해제. 승인 또는 제외 — 둘 다 사람의 책임으로 기록된다."""
    card = _get(db, card_id)
    data = dict(card.card_json)
    flags = [dict(f) for f in data.get("flags", [])]
    if not 0 <= index < len(flags):
        raise HTTPException(404, "flag 없음")

    flags[index].update({"resolved": True, "decision": body.decision,
                         "resolved_by": body.operator, "memo": body.memo})
    data["flags"] = flags

    if body.decision == "exclude":
        person = dict(data.get("person", {}))
        person["status"] = "excluded"
        data["person"] = person
        card.person_status = "excluded"

    card.card_json = data
    card.max_severity = max_severity(data)
    db.commit()
    ok, reason = is_sendable(data)
    return {"ok": True, "sendable": ok, "blocked_by": reason or None}


@router.post("/mappings/approve")
def approve_mapping(body: MappingApprove, db: Session = Depends(get_db)):
    """R-18: 승인된 매핑은 저장해 재사용한다."""
    row = (db.query(CompetencyMapping)
             .filter(CompetencyMapping.raw_name == body.raw_name).one_or_none())
    if row:
        row.canonical, row.approved_by = body.canonical, body.operator
    else:
        db.add(CompetencyMapping(raw_name=body.raw_name,
                                 canonical=body.canonical,
                                 approved_by=body.operator))
    db.commit()
    return {"ok": True}


@router.post("/{card_id}/person/resolve")
def resolve_person(card_id: int, body: PersonResolve, db: Session = Depends(get_db)):
    """R-15: 담당자의 동명이인 판단을 기억한다."""
    card = _get(db, card_id)
    data = dict(card.card_json)
    person = dict(data.get("person", {}))
    person["person_id"] = body.person_id
    data["person"] = person

    # 이 판단으로 ambiguous_person hold 가 풀린다
    flags = []
    for f in data.get("flags", []):
        f = dict(f)
        if f.get("code") in ("ambiguous_person", "person_not_in_roster"):
            f.update({"resolved": True, "decision": "approve",
                      "resolved_by": body.operator})
        flags.append(f)
    data["flags"] = flags

    card.card_json = data
    card.person_id = body.person_id
    card.max_severity = max_severity(data)

    memo_key = f"{person.get('name')}|{person.get('alias')}|{card.course_name}"
    if not db.query(PersonResolution).filter(
            PersonResolution.memo_key == memo_key).one_or_none():
        db.add(PersonResolution(memo_key=memo_key, person_id=body.person_id))
    db.commit()
    return {"ok": True}


def _get(db: Session, card_id: int) -> Card:
    card = db.get(Card, card_id)
    if not card:
        raise HTTPException(404, "카드 없음")
    return card
