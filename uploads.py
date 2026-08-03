"""업로드 -> 파싱 -> 카드 생성. 파이프라인의 입구."""
from __future__ import annotations

import os
import shutil
from typing import Any, Dict

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from database import get_db
from models import (Card, CompetencyMapping, Handoff, PersonResolution,
                    RosterEntry, Upload)
from pipeline import run_pipeline
from pipeline.rules.base import max_severity

router = APIRouter(prefix="/uploads", tags=["uploads"])
STORAGE = "./storage"


@router.post("")
def create_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "xlsx 파일만 지원합니다")

    os.makedirs(STORAGE, exist_ok=True)
    path = os.path.join(STORAGE, file.filename)
    with open(path, "wb") as out:            # 원본은 그대로 보관, 이후 읽기만
        shutil.copyfileobj(file.file, out)

    result = run_pipeline(path, roster=_roster(db), competency_map=_comp_map(db))

    upload = Upload(filename=file.filename, stored_path=path,
                    warnings=result["warnings"])
    db.add(upload)
    db.flush()

    for card_json in result["cards"]:
        db.add(_to_row(upload.id, card_json))
    db.flush()

    _queue_handoffs(db, upload.id, result["handoffs"])
    db.commit()

    return {
        "upload_id": upload.id,
        **result["summary"],
        "warnings": result["warnings"],
        "next": f"/uploads/{upload.id}/cards 에서 source_type 승인 필요",
    }


@router.get("/{upload_id}/cards")
def list_cards(upload_id: int, db: Session = Depends(get_db)):
    rows = db.query(Card).filter(Card.upload_id == upload_id).all()
    return [{
        "id": c.id, "name": c.person_name, "status": c.person_status,
        "source_type": c.source_type, "type_confirmed": c.type_confirmed,
        "max_severity": c.max_severity,
        "flags": c.card_json.get("flags", []),
    } for c in rows]


# --------------------------------------------------------------------------
def _to_row(upload_id: int, card: Dict[str, Any]) -> Card:
    ctx = card.get("context", {})
    prov = card.get("provenance", {})
    return Card(
        upload_id=upload_id,
        schema_version=card.get("schema_version", "0.5"),
        direction=card.get("direction", "individual_row"),
        person_name=card["person"]["name"],
        person_id=card["person"].get("person_id"),
        person_status=card["person"].get("status", "regular"),
        course_name=_first(ctx, ("과정명", "특강명", "진단명")),
        session_date=_first(ctx, ("날짜", "일자")),
        round_label=_first(ctx, ("차수", "회차")),
        source_type=card.get("source_type", {}).get("type"),
        type_confirmed=bool(card.get("source_type", {}).get("confirmed_by_operator")),
        max_severity=max_severity(card),
        card_json=card,
        source_file=prov.get("file", ""),
        source_sheet=prov.get("sheet", ""),
        source_row=str(prov.get("row") or prov.get("rows") or ""),
    )


def _first(d: dict, keys):
    for k in keys:
        for actual, v in d.items():
            if k in actual:
                return str(v)
    return None


def _queue_handoffs(db: Session, upload_id: int, handoffs) -> None:
    cards = {c.person_name: c.id
             for c in db.query(Card).filter(Card.upload_id == upload_id).all()}
    for h in handoffs:
        cid = cards.get(h.get("person"))
        if cid:
            db.add(Handoff(card_id=cid, rule_id=h["rule_id"],
                           task=h["task"], payload=h["payload"]))


def _roster(db: Session) -> dict:
    return {
        "people": [{"person_id": r.person_id, "name": r.name, "alias": r.alias,
                    "부서": r.department} for r in db.query(RosterEntry).all()],
        "resolved": {r.memo_key: r.person_id
                     for r in db.query(PersonResolution).all()},
    }


def _comp_map(db: Session) -> dict:
    return {m.raw_name.lower(): m.canonical
            for m in db.query(CompetencyMapping).all()}
