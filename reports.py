"""리포트 조립.

계약의 리포트 구조 공식:
    리포트 = 공통 틀(항상 동일)
           + 성장 섹션 (이전 회차가 있으면 · R-14)
           + 큐레이션 섹션 (source_type이 결정 · R-17)
섹션의 있고 없음은 담당자 임의가 아니라 규칙이 결정한다.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Card, Report
from pipeline.rules.base import is_sendable
from pipeline.rules.report import (r11_gate_direct_quote, r14_growth,
                                   r14_repeat_signal, r19_extract_best_practice)

router = APIRouter(prefix="/reports", tags=["reports"])


@router.post("/generate")
def generate(card_id: int, db: Session = Depends(get_db)):
    card = db.get(Card, card_id)
    if not card:
        raise HTTPException(404, "카드 없음")

    ok, reason = is_sendable(card.card_json)
    if not ok:
        raise HTTPException(409, f"진행 불가 — {reason}")

    body = _assemble(card, _previous(db, card))
    report = card.report or Report(card_id=card.id, body={}, status="draft")
    report.body = body
    db.add(report)
    db.commit()
    return {"report_id": report.id, "sections": list(body["sections"].keys())}


@router.post("/{report_id}/review")
def review(report_id: int, operator: str, db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if not report:
        raise HTTPException(404, "리포트 없음")
    report.status = "reviewed"
    report.reviewed_by = operator
    report.reviewed_at = dt.datetime.utcnow()
    db.commit()
    return {"ok": True, "status": report.status}


@router.get("/dispatch-table")
def dispatch_table(course: str | None = None, db: Session = Depends(get_db)):
    """발송 매핑표.

    실제 메일 발송은 프로토타입 범위 밖이다(계약 '다음 단계' 항목).
    명부가 붙기 전까지는 대상 매핑표까지만 산출한다.
    """
    q = db.query(Report).join(Card).filter(Report.status == "reviewed")
    if course:
        q = q.filter(Card.course_name == course)
    rows = []
    for r in q.all():
        c = r.card
        rows.append({
            "report_id": r.id, "name": c.person_name,
            "person_id": c.person_id or "(명부 미입력)",
            "course": c.course_name, "date": c.session_date,
            "quote_allowed": r11_gate_direct_quote(c.card_json),
        })
    return {"count": len(rows), "rows": rows}


@router.get("/aggregate")
def aggregate(course: str, db: Session = Depends(get_db)):
    """HRD용 통합본. R-19 조건(n>=3)을 못 채우면 섹션을 만들지 않는다."""
    cards = [c.card_json for c in
             db.query(Card).filter(Card.course_name == course).all()]
    if not cards:
        raise HTTPException(404, "해당 과정의 카드 없음")

    from pipeline.rules.base import RuleContext
    ctx = RuleContext(source_file="aggregate", source_sheet=course)
    best = r19_extract_best_practice(cards, ctx)

    scores = [c["score_summary"]["average"] for c in cards
              if c.get("score_summary", {}).get("average") is not None]
    return {
        "course": course,
        "n": len(cards),
        "average": round(sum(scores) / len(scores), 2) if scores else None,
        "best_practice": best or {"skipped": "그룹 n<3 — 개인 특정 위험(R-19)"},
        "pending_generation": len(ctx.handoffs),
    }


# --------------------------------------------------------------------------
def _assemble(card: Card, previous: Card | None) -> dict:
    data = card.card_json
    sections = {
        # 공통 틀 — 모든 리포트가 항상 동일하게 가진다
        "header": {"name": data["person"]["name"], "context": data["context"]},
        "scores": data["scores"],
        "summary": data["score_summary"],
        "narratives": _narratives(data),
    }

    # 성장 섹션 — 이전 회차가 있을 때만 (R-14)
    if data.get("source_type", {}).get("type") == "누적교육":
        growth = r14_growth(data, previous.card_json if previous else None)
        growth["applied_feedback"] = r14_repeat_signal(
            data, previous.card_json if previous else None)
        sections["growth"] = growth

    # 큐레이션 섹션 — 승인된 생성물이 있을 때만 (R-17 + R-16)
    curation = [g for g in data.get("generated", []) if g["rule_id"] == "R-17"]
    if curation:
        sections["curation"] = curation

    return {"schema_version": data.get("schema_version"), "sections": sections}


def _narratives(data: dict) -> list:
    """block_direct_quote 가 걸린 카드는 원문 인용 경로를 아예 통과시키지 않는다."""
    allow_quote = r11_gate_direct_quote(data)
    out = []
    for n in data.get("narratives", []):
        if n.get("exposure_policy") == "summarize_only" or not allow_quote:
            rewritten = next((g["text"] for g in data.get("generated", [])
                              if g["rule_id"] == "R-11"), None)
            out.append({"role": n.get("role"), "mode": "summary",
                        "text": rewritten,
                        "pending": rewritten is None})
        else:
            out.append({"role": n.get("role"), "mode": "verbatim",
                        "runs": n.get("runs", []),
                        "translation_ko": n.get("translation_ko")})
    return out


def _previous(db: Session, card: Card) -> Card | None:
    if not card.session_date:
        return None
    q = (db.query(Card)
           .filter(Card.person_name == card.person_name,
                   Card.course_name == card.course_name,
                   Card.session_date < card.session_date)
           .order_by(Card.session_date.desc()))
    if card.person_id:
        q = q.filter(Card.person_id == card.person_id)
    return q.first()
