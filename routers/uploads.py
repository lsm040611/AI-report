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
def create_upload(file: List[UploadFile] = File(..., description="xlsx 파일 (여러 개 가능)"),
                  auto: Optional[bool] = Query(
                      None, description="생성·리포트까지 한 번에. 미지정 시 설정값(HR_AUTO_APPROVE)"),
                  db: Session = Depends(get_db)):
    """평가지를 한 개 또는 여러 개 받아 처리한다.

    여러 개를 함께 받는 이유는 편의만이 아니다. 1차수와 2차수를 같이 올리면
    회차 간 성장 비교(R-14)가 그 자리에서 완성된다. 그래서 파일을 모두 읽어
    카드를 만든 **뒤에** 리포트를 만든다 — 순서를 어떻게 고르든 결과가 같도록.
    """
    files = [f for f in file if (f.filename or "").strip()]
    if not files:
        raise HTTPException(400, "파일이 없습니다")
    bad = [f.filename for f in files
           if not (f.filename or "").lower().endswith((".xlsx", ".xlsm"))]
    if bad:
        raise HTTPException(400, f"xlsx 파일만 지원합니다 — {', '.join(bad)}")

    auto_mode = AUTO_APPROVE if auto is None else auto
    os.makedirs(STORAGE_DIR, exist_ok=True)

    # ── 1단계: 파일마다 카드를 만들어 저장한다 ────────────────────────────
    entries: List[dict] = []
    for f in files:
        path = os.path.join(STORAGE_DIR, os.path.basename(f.filename))
        with open(path, "wb") as out:        # 원본은 그대로 보관, 이후 읽기만
            shutil.copyfileobj(f.file, out)

        try:
            result = run_pipeline(path,
                                  roster=_roster(db),
                                  competency_map=_comp_map(db),
                                  auto_approve=auto_mode)
        except Exception as exc:             # noqa: BLE001
            # 한 파일이 깨졌다고 나머지까지 버리지 않는다
            entries.append({"filename": f.filename, "cards": 0, "reports": [],
                            "error": f"엑셀을 읽지 못했습니다 — {type(exc).__name__}: {exc}"})
            continue

        upload = Upload(filename=f.filename, stored_path=path,
                        warnings=result["warnings"])
        db.add(upload)
        db.flush()
        for card_json in result["cards"]:
            db.add(_to_row(upload.id, card_json))
        db.flush()
        _queue_handoffs(db, upload.id, result["handoffs"])

        entries.append({"upload_id": upload.id, "filename": f.filename,
                        **result["summary"], "warnings": result["warnings"]})
    db.commit()

    response: Dict[str, Any] = {"mode": mode_banner(), "uploads": entries}
    _add_totals(response, entries)

    if not auto_mode:
        response["next"] = "각 업로드의 /uploads/{id}/cards 에서 source_type 승인 필요"
        return response

    # ── 2단계: 생성 큐를 소진한다 ────────────────────────────────────────
    # 여기서 죽어도 카드는 이미 저장돼 있다. 뒤 단계 실패로 앞 단계까지
    # 통째로 500이 되면 무엇이 됐고 무엇이 안 됐는지 알 수 없으므로 나눠 잡는다.
    for e in entries:
        if not e.get("upload_id"):
            continue
        try:
            e["generation"] = drain(db, upload_id=e["upload_id"])
        except Exception as exc:             # noqa: BLE001
            db.rollback()
            e["generation"] = {"error": f"{type(exc).__name__}: {exc}"}

    # ── 3단계: 모든 카드가 준비된 뒤에 리포트를 만든다 ────────────────────
    for e in entries:
        if not e.get("upload_id"):
            continue
        try:
            e["reports"] = _build_all(db, e["upload_id"])
        except Exception as exc:             # noqa: BLE001
            db.rollback()
            e["reports"] = []
            e["error"] = f"리포트 생성 실패 — {type(exc).__name__}: {exc}"

    _add_totals(response, entries)
    return response


def _add_totals(response: Dict[str, Any], entries: List[dict]) -> None:
    """파일별 결과를 합쳐 요약을 붙인다.

    단일 파일을 올리던 때의 응답 형태(`cards`, `reports`, `warnings` …)를 그대로
    유지한다 — 기존 스크립트와 테스트가 계속 동작해야 하기 때문이다.
    """
    by_type: Dict[str, int] = {}
    for e in entries:
        for k, v in (e.get("by_source_type") or {}).items():
            by_type[k] = by_type.get(k, 0) + v

    reports = [r for e in entries for r in (e.get("reports") or [])]
    response.update({
        "files": len(entries),
        "cards": sum(e.get("cards", 0) for e in entries),
        "by_source_type": by_type,
        "question_defs": sum(e.get("question_defs", 0) for e in entries),
        "pending_generation": sum(e.get("pending_generation", 0) for e in entries),
        "warnings": [w for e in entries for w in (e.get("warnings") or [])],
        "reports": reports,
    })
    if any("generation" in e for e in entries):
        gens = [e["generation"] for e in entries if isinstance(e.get("generation"), dict)]
        response["generation"] = {
            "accepted": sum(g.get("accepted", 0) for g in gens),
            "rejected": sum(g.get("rejected", 0) for g in gens),
            "rejects": [r for g in gens for r in (g.get("rejects") or [])],
        }
    if len(entries) == 1 and entries[0].get("upload_id"):
        response["upload_id"] = entries[0]["upload_id"]
    errors = [e["error"] for e in entries if e.get("error")]
    if errors:
        response["file_errors"] = errors


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
