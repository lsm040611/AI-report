"""업로드 -> 파싱 -> 카드 -> (자동 모드면) 생성 -> 리포트. 파이프라인의 입구."""
from __future__ import annotations

import os
import re
import shutil
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from config import AUTO_APPROVE, STORAGE_DIR, mode_banner
from database import get_db
from generation import drain
from models import (Card, CompetencyMapping, Handoff, PersonResolution,
                    RosterEntry, Upload)
from pipeline import run_pipeline
from pipeline.rules.base import is_sendable, max_severity
from routers.reports import build_report

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post("")
def create_upload(file: UploadFile = File(...),
                  auto: Optional[bool] = Query(
                      None, description="생성·리포트까지 한 번에. 미지정 시 설정값(HR_AUTO_APPROVE)"),
                  db: Session = Depends(get_db)):
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "xlsx 파일만 지원합니다")

    auto_mode = AUTO_APPROVE if auto is None else auto

    os.makedirs(STORAGE_DIR, exist_ok=True)
    path = os.path.join(STORAGE_DIR, os.path.basename(file.filename))
    with open(path, "wb") as out:            # 원본은 그대로 보관, 이후 읽기만
        shutil.copyfileobj(file.file, out)

    try:
        result = run_pipeline(path,
                              roster=_roster(db),
                              competency_map=_comp_map(db),
                              auto_approve=auto_mode)
    except Exception as exc:                 # noqa: BLE001
        raise HTTPException(400, f"엑셀을 읽지 못했습니다 — {type(exc).__name__}: {exc}")

    upload = Upload(filename=file.filename, stored_path=path,
                    warnings=result["warnings"])
    db.add(upload)
    db.flush()

    for card_json in result["cards"]:
        db.add(_to_row(upload.id, card_json))
    db.flush()

    _queue_handoffs(db, upload.id, result["handoffs"])
    db.commit()

    response: Dict[str, Any] = {
        "upload_id": upload.id,
        "mode": mode_banner(),
        **result["summary"],
        "warnings": result["warnings"],
    }

    if not auto_mode:
        response["next"] = f"/uploads/{upload.id}/cards 에서 source_type 승인 필요"
        return response

    # ── 자동 모드: 생성 큐 소진 → 리포트 생성 ────────────────────────────
    # 여기서 죽어도 카드는 이미 저장돼 있다. 뒤 단계 실패로 앞 단계까지
    # 통째로 500이 되면 무엇이 됐고 무엇이 안 됐는지 알 수 없으므로 나눠 잡는다.
    try:
        response["generation"] = drain(db, upload_id=upload.id)
    except Exception as exc:                 # noqa: BLE001
        db.rollback()
        response["generation"] = {"error": f"{type(exc).__name__}: {exc}"}

    try:
        response["reports"] = _build_all(db, upload.id)
    except Exception as exc:                 # noqa: BLE001
        db.rollback()
        response["reports"] = []
        response["report_error"] = f"{type(exc).__name__}: {exc}"
    return response


@router.post("/{upload_id}/finish")
def finish(upload_id: int, db: Session = Depends(get_db)):
    """승인이 끝난 업로드에 대해 생성 + 리포트를 한 번에 돌린다(운영 모드용)."""
    if not db.query(Card).filter(Card.upload_id == upload_id).first():
        raise HTTPException(404, "업로드 없음")
    gen = drain(db, upload_id=upload_id)
    return {"generation": gen, "reports": _build_all(db, upload_id)}


@router.get("/{upload_id}/cards")
def list_cards(upload_id: int, db: Session = Depends(get_db)):
    rows = db.query(Card).filter(Card.upload_id == upload_id).all()
    if not rows:
        raise HTTPException(404, "업로드 없음 또는 카드 없음")
    out = []
    for c in rows:
        ok, reason = is_sendable(c.card_json)
        out.append({
            "id": c.id, "name": c.person_name, "status": c.person_status,
            "source_type": c.source_type, "type_confirmed": c.type_confirmed,
            "max_severity": c.max_severity, "sendable": ok, "blocked_by": reason or None,
            "flags": c.card_json.get("flags", []),
        })
    return out


# --------------------------------------------------------------------------
def _build_all(db: Session, upload_id: int) -> List[dict]:
    out = []
    for c in db.query(Card).filter(Card.upload_id == upload_id).all():
        ok, reason = is_sendable(c.card_json)
        if not ok:
            out.append({"card_id": c.id, "name": c.person_name,
                        "report_id": None, "blocked_by": reason})
            continue
        report = build_report(db, c)
        db.flush()
        out.append({"card_id": c.id, "name": c.person_name,
                    "report_id": report.id, "html": f"/reports/{report.id}/html"})
    db.commit()
    return out


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
        course_name=_first(ctx, ("과정명", "특강명", "진단명", "프로그램")),
        session_date=_first(ctx, ("날짜", "일자", "기간")),
        round_label=_round_label(_first(ctx, ("차수", "회차"))),
        source_type=(card.get("source_type") or {}).get("type"),
        type_confirmed=bool((card.get("source_type") or {}).get("confirmed_by_operator")),
        max_severity=max_severity(card),
        card_json=card,
        source_file=prov.get("file", ""),
        source_sheet=prov.get("sheet", ""),
        source_row=str(prov.get("row") or prov.get("rows") or ""),
    )


ROUND = re.compile(r"(\d+)\s*(차수|회차|주차)")


def _round_label(raw: Optional[str]) -> Optional[str]:
    """'2차수 · A조' → '2차수'.

    같은 회차인데 파일마다 조 표기가 붙었다 말았다 하면, 대시보드에서 한 회차가
    두 칸으로 갈라져 성장 추이가 어긋난다. 조 정보는 context 에 그대로 남는다.
    """
    if not raw:
        return None
    m = ROUND.search(str(raw))
    return f"{m.group(1)}{m.group(2)}" if m else str(raw).strip()


def _first(d: dict, keys) -> Optional[str]:
    for k in keys:
        for actual, v in d.items():
            if k in str(actual):
                return str(v)
    return None


def _queue_handoffs(db: Session, upload_id: int, handoffs) -> None:
    """이름으로 카드를 찾아 큐에 붙인다.

    동명이인이 같은 업로드에 있으면 첫 카드로 몰린다. R-15 가 person_id 를
    붙이기 전까지는 이름이 유일한 키라서 생기는 한계다(플래그로 표시된다).
    """
    cards = {}
    for c in db.query(Card).filter(Card.upload_id == upload_id).all():
        cards.setdefault(c.person_name, c.id)
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
