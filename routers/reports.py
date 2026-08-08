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
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

import mailer
from database import get_db
from models import Card, Course, Report
from pipeline.rules.base import RuleContext, is_sendable, quote_allowed
from pipeline.rules.report import (r14_growth, r14_repeat_signal,
                                   r19_extract_best_practice)
from render import render, template, to_presentation_card

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
    """완성된 리포트 HTML 한 편. 브라우저에서 Ctrl+P → 'PDF로 저장' 하면 PDF가 된다."""
    report = db.get(Report, report_id)
    if not report:
        raise HTTPException(404, "리포트 없음")
    return HTMLResponse(render(report.body["presentation"]))


# ══════════════════════════════════════════════════════════════
# UI 통합 지점 ③ — 카드 → 리포트 본문 렌더
# ══════════════════════════════════════════════════════════════
@router.get("/{report_id}/body")
def report_body(report_id: int, db: Session = Depends(get_db)):
    """리포트 **본문만** 조각으로 준다. UI 의 placeholder 자리에 그대로 넣는다.

    통짜 HTML(`/html`)과 다른 점은 셋이다 — `<html>` 껍데기가 없고, 사이드바가
    이미 보여 주는 이름·과정 머리글이 빠지고, 스타일시트를 따로 준다.
    묶음마다 `data-section` 이 붙어 있어 스크롤 스파이가 바로 동작한다.
    """
    report = db.get(Report, report_id)
    if not report:
        raise HTTPException(404, "리포트 없음")
    pres = report.body["presentation"]
    sections = pres.get("sections") or []
    return {
        "reportId": report.id,
        "cardId": report.card_id,
        "maxWidth": 760,                       # 통합 명세가 정한 본문 폭
        "toc": template.toc(sections),         # 실제로 렌더된 묶음만
        "html": template.render_sections(sections),
        "css": template.CSS,
        "footerHtml": template.render_footer(pres.get("footer")),
        "sentenceCount": len(pres.get("evidence") or []),
    }


@router.get("/{report_id}/evidence")
def report_evidence(report_id: int, sentence_id: Optional[str] = None,
                    db: Session = Depends(get_db)):
    """문장 id → 근거. 검수 화면에서 문장을 클릭하면 이걸 부른다 (R-16 대조).

    본문에 `data-sentence-id` 가 붙은 문장은 전부 여기에 항목이 있다.
    붙어 있지 않은 문장은 사람이 쓴 원문이라 대조할 근거가 따로 없다.
    """
    report = db.get(Report, report_id)
    if not report:
        raise HTTPException(404, "리포트 없음")
    items = (report.body.get("presentation") or {}).get("evidence") or []
    if sentence_id:
        found = next((e for e in items if e["sentenceId"] == sentence_id), None)
        if not found:
            raise HTTPException(404, f"문장 없음 — {sentence_id}")
        return found
    return {"reportId": report.id, "items": items}


# ══════════════════════════════════════════════════════════════
# UI 통합 지점 ④ — 리포트 → PDF
# ══════════════════════════════════════════════════════════════
def pdf_filename(card: Card, db: Optional[Session] = None) -> str:
    """`{과정명}_{차수}_{구성원명}_{발송일YYYYMMDD}.pdf` — 통합 명세 §2-④.

    과정명은 **담당자가 확정한 이름**을 쓴다. 파일에 적혀 있던 원본 표기
    ("Global Negotiation Program (GN-1)")를 쓰면, 화면에서는 "협상 스킬 심화"로
    보던 리포트가 전혀 다른 이름으로 저장된다.
    """
    sent = card.report.sent_at if card.report else None
    day = (sent or dt.datetime.utcnow()).strftime("%Y%m%d")
    parts = [_course_title(card, db) or card.course_name or "과정",
             card.round_label or "", card.person_name or "구성원", day]
    safe = [re.sub(r'[\\/:*?"<>|]', "", str(p)).strip() for p in parts if p]
    return "_".join(safe) + ".pdf"


def _course_title(card: Card, db: Optional[Session]) -> Optional[str]:
    cid = (card.card_json.get("context") or {}).get("_course_id")
    if not cid or db is None:
        return None
    found = db.query(Course).filter(Course.course_id == cid).first()
    return found.title if found else None


def _render_pdf(html: str) -> Optional[bytes]:
    """설치돼 있으면 진짜 PDF 로 굽는다. 없으면 None.

    파이썬만으로 한글 PDF 를 제대로 뽑으려면 WeasyPrint 가 필요하고, 그건
    윈도우에서 GTK 를 따로 깔아야 한다. 팀원 전원이 비개발자라 필수 의존성으로
    두지 않았다 — 없으면 인쇄용 HTML 로 대신하고, 브라우저의 'PDF로 저장'을
    쓰게 한다. 나오는 문서는 같다.
    """
    try:
        from weasyprint import HTML          # noqa: PLC0415
    except Exception:                        # noqa: BLE001
        return None
    return HTML(string=html).write_pdf()


@router.get("/{report_id}/pdf")
def report_pdf(report_id: int, db: Session = Depends(get_db)):
    """PDF 로 내려준다. WeasyPrint 가 없으면 같은 내용의 인쇄용 HTML 을 준다."""
    report = db.get(Report, report_id)
    if not report:
        raise HTTPException(404, "리포트 없음")
    html = render(report.body["presentation"])
    name = pdf_filename(report.card, db)

    pdf = _render_pdf(html)
    if pdf is not None:
        return Response(pdf, media_type="application/pdf", headers={
            "Content-Disposition": f'attachment; filename*=UTF-8\'\'{quote(name)}'})

    # 대체 경로 — 내용·서식은 같고 컨테이너만 HTML 이다
    return HTMLResponse(html, headers={
        "X-Pdf-Fallback": "weasyprint-not-installed",
        "X-Suggested-Filename": quote(name),
    })


@router.post("/pdf/prepare")
def prepare_pdfs(upload_id: int, db: Session = Depends(get_db)):
    """발송 승인 시 PDF 를 미리 굽는다 (전제: 발송 후 리포트 불변).

    UI 가 기대하는 `{empId, pdfUrl, generatedAt}` 배열을 돌려준다.
    """
    cards = db.query(Card).filter(Card.upload_id == upload_id).all()
    if not cards:
        raise HTTPException(404, "업로드 없음 또는 카드 없음")
    now = dt.datetime.utcnow().isoformat(timespec="seconds")
    out = []
    for c in cards:
        if not c.report:
            continue
        ok, reason = is_sendable(c.card_json)
        out.append({"empId": c.person_id, "name": c.person_name,
                    "pdfUrl": f"/reports/{c.report.id}/pdf" if ok else None,
                    "filename": pdf_filename(c, db),
                    "generatedAt": now if ok else None,
                    "blockedBy": None if ok else reason})
    engine_name = "weasyprint" if _render_pdf("<p>x</p>") is not None else "browser-print"
    return {"uploadId": upload_id, "engine": engine_name, "items": out}


@router.get("/mail/status")
def mail_status():
    """발송 준비가 됐는지. 화면이 버튼 옆에 이 상태를 띄운다."""
    return mailer.status()


@router.post("/{report_id}/send")
def send_report(report_id: int, db: Session = Depends(get_db)):
    """리포트를 **본인 주소로만** 보낸다.

    주소는 평가지에 적힌 그 사람의 것만 쓴다. 받는 사람을 밖에서 지정할 수
    없게 한 것은 실수 한 번으로 남의 피드백이 가는 일을 막기 위해서다.
    """
    report = db.get(Report, report_id)
    if not report:
        raise HTTPException(404, "리포트 없음")
    card = db.get(Card, report.card_id)
    if not card:
        raise HTTPException(404, "카드 없음")

    person = (card.card_json.get("person") or {})
    to = person.get("email")
    if not mailer.valid(to):
        raise HTTPException(400, f"{person.get('name')} 님의 평가지에 이메일이 없습니다")

    context = card.card_json.get("context") or {}
    program = context.get("program") or context.get("과정명") or "피드백 리포트"
    round_ = context.get("차수") or context.get("round") or ""
    date = context.get("날짜") or context.get("date") or ""
    rater = context.get("강사") or context.get("평가자") or ""
    name = person.get("name") or "참가자"

    subject = " ".join(x for x in (program, round_) if x)
    subject = f"[{subject}] {name} 님 개인 피드백 리포트"

    # 리포트는 본문이 아니라 파일로 붙인다. 받는 사람이 그대로 저장할 수 있고,
    # 브라우저에서 열어 인쇄(PDF)까지 된다.
    html = render(report.body["presentation"])
    stamp = str(date).replace("-", "").replace(".", "")[:8]
    filename = "_".join(x for x in ("개인리포트", name, stamp) if x) + ".html"

    result = mailer.send(
        to, subject,
        mailer.report_body(name, program, round_, date, rater),
        attachment=(filename, html.encode("utf-8")),
    )
    if result.get("sent"):
        report.sent_at = dt.datetime.utcnow()
        db.commit()
    return {"report_id": report.id, "person": person.get("name"), **result}


@router.post("/send/upload/{upload_id}")
def send_upload(upload_id: int, db: Session = Depends(get_db)):
    """한 파일에서 나온 리포트를 사람마다 자기 주소로 보낸다."""
    cards = db.query(Card).filter(Card.upload_id == upload_id).all()
    if not cards:
        raise HTTPException(404, "업로드 없음")

    results = []
    for card in cards:
        report = db.query(Report).filter(Report.card_id == card.id).one_or_none()
        person = (card.card_json.get("person") or {})
        if report is None:
            results.append({"person": person.get("name"), "sent": False,
                            "reason": "리포트가 아직 없습니다"})
            continue
        try:
            results.append(send_report(report.id, db))
        except HTTPException as exc:
            results.append({"person": person.get("name"), "sent": False,
                            "reason": exc.detail})

    sent = sum(1 for r in results if r.get("sent"))
    return {"upload_id": upload_id, "sent": sent,
            "total": len(results), "mail": mailer.status(), "results": results}


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
