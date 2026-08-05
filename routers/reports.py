"""리포트 조립.

계약의 리포트 구조 공식:
    리포트 = 공통 틀(항상 동일)
           + 성장 섹션 (이전 회차가 있으면 · R-14)
           + 큐레이션 섹션 (source_type이 결정 · R-17)
섹션의 있고 없음은 담당자 임의가 아니라 규칙이 결정한다.

HTML 렌더링까지 여기서 끝난다 (render 패키지). Node 런타임은 필요 없다.
"""
from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from database import get_db
from models import Card, Report
from pipeline.rules.base import RuleContext, is_sendable, quote_allowed
from pipeline.rules.report import (r14_growth, r14_repeat_signal,
                                   r19_extract_best_practice)
from render import render, to_presentation_card

router = APIRouter(prefix="/reports", tags=["reports"])


# ══════════════════════════════════════════════════════════════
@router.post("/generate")
def generate(card_id: int, db: Session = Depends(get_db)):
    card = db.get(Card, card_id)
    if not card:
        raise HTTPException(404, "카드 없음")

    ok, reason = is_sendable(card.card_json)
    if not ok:
        raise HTTPException(409, f"진행 불가 — {reason}")

    report = build_report(db, card)
    db.commit()
    return {"report_id": report.id,
            "sections": [s["kind"] for s in report.body["presentation"]["sections"]],
            "html": f"/reports/{report.id}/html"}


def build_report(db: Session, card: Card) -> Report:
    """카드 한 장 → 리포트 한 편. uploads 자동 모드에서도 이 함수를 부른다."""
    previous = _previous(db, card)
    data = card.card_json

    growth = repeat = None
    if (data.get("source_type") or {}).get("type") == "누적교육":
        growth = r14_growth(data, previous.card_json if previous else None)
        repeat = r14_repeat_signal(data, previous.card_json if previous else None)

    peer_avg, peer_n, peer_label = _peer_averages(db, card)

    presentation = to_presentation_card(
        data, growth=growth, repeat=repeat,
        peer_avg=peer_avg, peer_label=peer_label, peer_n=peer_n,
        team=_team(data), contact=card.session_date or "",
    )

    body = {
        "schema_version": data.get("schema_version"),
        # 데이터 뷰 — 프론트가 직접 그리고 싶을 때 쓴다
        "sections": {
            "header": {"name": data["person"]["name"], "context": data.get("context", {})},
            "scores": data.get("scores", []),
            "summary": data.get("score_summary", {}),
            "growth": growth,
            "generated": data.get("generated", []),
            "quote_allowed": quote_allowed(data),
        },
        # 표현 뷰 — 렌더러가 그대로 먹는다
        "presentation": presentation,
    }

    report = card.report or Report(card_id=card.id, body={}, status="draft")
    report.body = body
    db.add(report)
    db.flush()
    return report


@router.get("/{report_id}")
def get_report(report_id: int, db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if not report:
        raise HTTPException(404, "리포트 없음")
    return {"id": report.id, "card_id": report.card_id,
            "status": report.status, "body": report.body}


@router.get("/{report_id}/html", response_class=HTMLResponse)
def report_html(report_id: int, db: Session = Depends(get_db)):
    """완성된 리포트 HTML. 브라우저에서 Ctrl+P → 'PDF로 저장' 하면 PDF가 된다."""
    report = db.get(Report, report_id)
    if not report:
        raise HTTPException(404, "리포트 없음")
    return HTMLResponse(render(report.body["presentation"]))


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


# ══════════════════════════════════════════════════════════════
@router.get("/dispatch-table/list")
def dispatch_table(course: Optional[str] = None,
                   include_draft: bool = True,
                   db: Session = Depends(get_db)):
    """발송 매핑표.

    실제 메일 발송은 프로토타입 범위 밖이다(계약 '다음 단계' 항목).
    명부가 붙기 전까지는 대상 매핑표까지만 산출한다.
    """
    q = db.query(Report).join(Card)
    if not include_draft:
        q = q.filter(Report.status == "reviewed")
    if course:
        q = q.filter(Card.course_name == course)

    rows = []
    for r in q.all():
        c = r.card
        ok, reason = is_sendable(c.card_json)
        rows.append({
            "report_id": r.id, "status": r.status, "name": c.person_name,
            "person_id": c.person_id or "(명부 미입력)",
            "course": c.course_name, "date": c.session_date,
            "sendable": ok, "blocked_by": reason or None,
            "quote_allowed": quote_allowed(c.card_json),
            "html": f"/reports/{r.id}/html",
        })
    return {"count": len(rows), "rows": rows}


@router.get("/aggregate/course")
def aggregate(course: str, db: Session = Depends(get_db)):
    """HRD용 통합본. R-19 조건(n>=3)을 못 채우면 섹션을 만들지 않는다."""
    cards = [c.card_json for c in
             db.query(Card).filter(Card.course_name == course).all()]
    if not cards:
        raise HTTPException(404, "해당 과정의 카드 없음")

    ctx = RuleContext(source_file="aggregate", source_sheet=course)
    best = r19_extract_best_practice(cards, ctx)

    scores = [c["score_summary"]["average"] for c in cards
              if (c.get("score_summary") or {}).get("average") is not None]
    return {
        "course": course,
        "n": len(cards),
        "average": round(sum(scores) / len(scores), 2) if scores else None,
        "best_practice": best or {"skipped": "그룹 n<3 — 개인 특정 위험(R-19)"},
        "pending_generation": len(ctx.handoffs),
    }


# ══════════════════════════════════════════════════════════════
def _previous(db: Session, card: Card) -> Optional[Card]:
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


def _peer_averages(db: Session, card: Card) -> Tuple[Dict[str, float], int, str]:
    """같은 차시 참가자들의 역량별 평균 — 척도 트랙의 회색 선.

    '나만 낮은가'는 리포트를 받는 사람의 두 번째 질문이다. 같은 시트에서
    같이 평가받은 사람들만 비교 대상으로 삼는다.
    """
    if card.direction != "individual_row":
        return {}, 0, "차수 평균"

    peers = (db.query(Card)
               .filter(Card.upload_id == card.upload_id,
                       Card.source_sheet == card.source_sheet,
                       Card.direction == "individual_row")
               .all())
    if len(peers) < 2:
        return {}, len(peers), "차수 평균"

    bucket: Dict[str, List[float]] = {}
    for p in peers:
        for it in (p.card_json.get("scores") or []):
            if it.get("score") is None:
                continue
            key = it.get("area_name") or it.get("question_id")
            if key:
                bucket.setdefault(key, []).append(float(it["score"]))

    avg = {k: round(sum(v) / len(v), 2) for k, v in bucket.items() if v}
    label = "차수 평균" if card.round_label else "참석자 평균"
    return avg, len(peers), label


def _team(data: dict) -> str:
    stype = (data.get("source_type") or {}).get("type")
    return "HRD 조직문화팀" if stype == "진단서베이" else "HRD 교육운영팀"
