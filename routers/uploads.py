"""업로드 -> 파싱 -> 카드 -> (자동 모드면) 생성 -> 리포트. 파이프라인의 입구."""
from __future__ import annotations

import os
import re
import shutil
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from pydantic import BaseModel, Field

from config import AUTO_APPROVE, STORAGE_DIR, mode_banner
from database import get_db
from generation import drain
from models import (Card, CompetencyMapping, Course, CourseAlias, Handoff,
                    PersonResolution, RosterEntry, Upload, UploadDraft)
from pipeline import courses as coursematch
from pipeline import run_pipeline
from pipeline.analyze import analyze
from pipeline.rules.base import is_sendable, max_severity
from contract import SOURCE_TYPES
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


# ══════════════════════════════════════════════════════════════
# UI 통합 지점 ① — 파일 업로드 → 판정
# ══════════════════════════════════════════════════════════════
@router.post("/analyze")
def analyze_upload(file: UploadFile = File(..., description="xlsx 파일 한 개"),
                   entryCourseKey: Optional[str] = Query(
                       None, description="과정 카드에서 진입한 경우의 과정 id"),
                   db: Session = Depends(get_db)):
    """카드를 만들지 않고 **판정만** 한다.

    담당자가 검증 화면에서 유형·과정을 확인하고 오류 행을 처리한 뒤에
    `POST /uploads/{draftId}/commit` 을 부르면 그때 카드가 만들어진다.
    여기서 취소하면 남는 것은 저장된 파일 하나뿐이다.
    """
    name = (file.filename or "").strip()
    if not name.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, f"xlsx 파일만 지원합니다 — {name or '이름 없음'}")

    os.makedirs(STORAGE_DIR, exist_ok=True)
    path = os.path.join(STORAGE_DIR, os.path.basename(name))
    with open(path, "wb") as out:            # 원본은 그대로 보관, 이후 읽기만
        shutil.copyfileobj(file.file, out)

    try:
        result = analyze(path,
                         known_courses=_known_courses(db),
                         aliases=_aliases(db),
                         roster=_roster(db),
                         entry_course_key=entryCourseKey)
    except Exception as exc:                 # noqa: BLE001
        raise HTTPException(400, f"엑셀을 읽지 못했습니다 — "
                                 f"{type(exc).__name__}: {exc}")

    draft = UploadDraft(filename=name, stored_path=path, analysis=result)
    db.add(draft)
    db.commit()
    return {"draftId": draft.id, **result}


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: int, db: Session = Depends(get_db)):
    draft = db.get(UploadDraft, draft_id)
    if not draft:
        raise HTTPException(404, "판정 결과 없음")
    return {"draftId": draft.id, "status": draft.status,
            "confirmed": draft.confirmed or None, "uploadId": draft.upload_id,
            **(draft.analysis or {})}


# ══════════════════════════════════════════════════════════════
# UI 통합 지점 ② — 검증 확정 → 카드 생성
# ══════════════════════════════════════════════════════════════
class RowFix(BaseModel):
    rowNumber: int
    field: str
    value: Optional[str] = None
    label: Optional[str] = Field(None, description="field=score 일 때 어느 역량인지")


class ConfirmedCourse(BaseModel):
    mode: str = Field(..., description="link | create")
    courseId: Optional[str] = None
    newTitle: Optional[str] = Field(None, description="mode=create 일 때만")


class CommitRequest(BaseModel):
    confirmedSourceType: str
    confirmedCourse: ConfirmedCourse
    confirmedWave: Optional[int] = None
    rowFixes: List[RowFix] = []
    excludedRows: List[int] = []
    operator: str = "담당자"
    generate: bool = Field(True, description="문장 생성까지 이어서 돌릴지")


@router.post("/{draft_id}/commit")
def commit_draft(draft_id: int, req: CommitRequest,
                 db: Session = Depends(get_db)):
    """담당자 확정값을 반영해 카드를 만든다.

    확정값은 **그대로 수용한다.** 엔진이 다르게 판정했더라도 다시 판정하지
    않는다 (통합 명세 §5-4). 담당자가 화면에서 본 것과 결과가 달라지는 것보다
    엔진이 틀린 채로 남는 편이 낫다 — 틀렸으면 다음 업로드에서 고치면 된다.
    """
    draft = db.get(UploadDraft, draft_id)
    if not draft:
        raise HTTPException(404, "판정 결과 없음")
    if draft.upload_id:
        raise HTTPException(409, f"이미 카드를 만든 판정입니다 — "
                                 f"업로드 {draft.upload_id}")
    if req.confirmedSourceType not in SOURCE_TYPES:
        raise HTTPException(400, f"모르는 유형입니다 — {req.confirmedSourceType} "
                                 f"(가능: {', '.join(SOURCE_TYPES)})")

    course = _resolve_course(db, req, draft)

    confirmed = {
        "sourceType": req.confirmedSourceType,
        "courseId": course["courseId"],
        "courseTitle": course["title"],
        "wave": req.confirmedWave,
        "rowFixes": [f.model_dump() for f in req.rowFixes],
        "excludedRows": req.excludedRows,
        "operator": req.operator,
    }

    try:
        result = run_pipeline(draft.stored_path,
                              roster=_roster(db),
                              competency_map=_comp_map(db),
                              auto_approve=False,      # 확정값이 이미 승인이다
                              confirmed=confirmed)
    except Exception as exc:                 # noqa: BLE001
        raise HTTPException(400, f"카드를 만들지 못했습니다 — "
                                 f"{type(exc).__name__}: {exc}")

    upload = Upload(filename=draft.filename, stored_path=draft.stored_path,
                    warnings=result["warnings"])
    db.add(upload)
    db.flush()
    for card_json in result["cards"]:
        db.add(_to_row(upload.id, card_json))
    db.flush()
    _queue_handoffs(db, upload.id, result["handoffs"])

    draft.upload_id = upload.id
    draft.status = "committed"
    draft.confirmed = confirmed
    db.commit()

    out = {
        "draftId": draft.id, "uploadId": upload.id,
        "courseId": course["courseId"], "courseTitle": course["title"],
        "courseCreated": course["created"],
        "cards": _card_briefs(db, upload.id),
        "flags": _flag_list(db, upload.id),
        "warnings": result["warnings"],
    }
    if req.generate:
        out["generation"] = drain(db, upload_id=upload.id)
        out["reports"] = _build_all(db, upload.id)
        out["flags"] = _flag_list(db, upload.id)
    return out


def _resolve_course(db: Session, req: CommitRequest, draft: UploadDraft) -> dict:
    """확정된 과정을 붙이거나 새로 만든다. courseId 발급 주체는 엔진이다."""
    c = req.confirmedCourse
    analysis = draft.analysis or {}
    raw_title = ((analysis.get("courseMatch") or {}).get("suggestedTitle") or
                 draft.filename)

    if c.mode == "link":
        if not c.courseId:
            raise HTTPException(400, "mode=link 에는 courseId 가 필요합니다")
        found = db.query(Course).filter(Course.course_id == c.courseId).first()
        if not found:
            raise HTTPException(404, f"모르는 과정입니다 — {c.courseId}")
        _remember_alias(db, raw_title, found.course_id, req.operator)
        return {"courseId": found.course_id, "title": found.title, "created": False}

    if c.mode != "create":
        raise HTTPException(400, f"mode 는 link 또는 create 입니다 — {c.mode}")

    title = (c.newTitle or raw_title or "제목 없는 과정").strip()
    cid = coursematch.issue_course_id(title)
    exists = db.query(Course).filter(Course.course_id == cid).first()
    if exists:
        # 같은 이름으로 두 번 만들려는 경우. 새로 만들지 않고 그 과정에 붙인다.
        _remember_alias(db, raw_title, exists.course_id, req.operator)
        return {"courseId": exists.course_id, "title": exists.title, "created": False}

    db.add(Course(course_id=cid, title=title,
                  source_type=req.confirmedSourceType,
                  instructor=_ctx(analysis, ("강사", "진행자")),
                  created_by=req.operator))
    db.flush()
    _remember_alias(db, raw_title, cid, req.operator)
    return {"courseId": cid, "title": title, "created": True}


def _remember_alias(db: Session, raw_title: Optional[str], course_id: str,
                    operator: str) -> None:
    """담당자가 확정한 표기를 사전에 적어 둔다 — 다음 업로드부터는 묻지 않는다."""
    key = coursematch.normalize(raw_title or "")
    if not key:
        return
    if db.query(CourseAlias).filter(CourseAlias.alias == key).first():
        return
    db.add(CourseAlias(alias=key, course_id=course_id, confirmed_by=operator))


def _ctx(analysis: dict, keys) -> Optional[str]:
    for k, v in (analysis.get("context") or {}).items():
        if any(want in str(k) for want in keys):
            return str(v)
    return None


def _known_courses(db: Session) -> List[dict]:
    out = []
    for c in db.query(Course).all():
        rounds = [r for (r,) in db.query(Card.round_label)
                  .filter(Card.card_json["context"]["_course_id"]
                          .as_string() == c.course_id)
                  .distinct().all() if r]
        out.append({"courseId": c.course_id, "title": c.title,
                    "sourceType": c.source_type, "instructor": c.instructor,
                    "rounds": sorted(rounds)})
    return out


def _aliases(db: Session) -> Dict[str, str]:
    return {a.alias: a.course_id for a in db.query(CourseAlias).all()}


def _card_briefs(db: Session, upload_id: int) -> List[dict]:
    out = []
    for c in db.query(Card).filter(Card.upload_id == upload_id).all():
        ok, reason = is_sendable(c.card_json)
        out.append({"cardId": c.id, "name": c.person_name,
                    "empId": c.person_id, "status": c.person_status,
                    "maxSeverity": c.max_severity,
                    "sendable": ok, "blockedBy": reason or None})
    return out


def _flag_list(db: Session, upload_id: int) -> List[dict]:
    """UI 검수 화면 배지에 그대로 쓰이는 형태. severity 4종만 나간다."""
    out = []
    for c in db.query(Card).filter(Card.upload_id == upload_id).all():
        for f in c.card_json.get("flags", []):
            if f.get("resolved"):
                continue
            out.append({"cardId": c.id, "empId": c.person_id,
                        "name": c.person_name,
                        "severity": f.get("severity"),
                        "code": f.get("code"),
                        "message": f.get("message") or f.get("action") or
                                   f.get("code")})
    return out


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
