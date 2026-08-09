"""업로드 -> 파싱 -> 카드 -> (자동 모드면) 생성 -> 리포트. 파이프라인의 입구."""
from __future__ import annotations

import os
import re
import shutil
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from pydantic import BaseModel, Field

from config import AUTO_APPROVE, STORAGE_DIR, mode_banner
from database import SessionLocal, get_db
from generation import drain
from models import (Card, CompetencyMapping, Course, CourseAlias, Handoff,
                    PersonResolution, RosterEntry, Upload, UploadDraft)
from pipeline import courses as coursematch
from pipeline import run_pipeline
from pipeline.analyze import analyze
from pipeline.rules.base import is_reportable, is_sendable, max_severity
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
def analyze_upload(file: List[UploadFile] = File(..., description="xlsx 파일 (여러 개 가능)"),
                   entryCourseKey: Optional[str] = Query(
                       None, description="과정 카드에서 진입한 경우의 과정 id"),
                   db: Session = Depends(get_db)):
    """카드를 만들지 않고 **판정만** 한다.

    여러 개를 함께 받는다. 1차수와 2차수를 같이 올리면 회차 간 성장 비교가
    그 자리에서 완성되기 때문이다 — 나눠 올리면 두 번째 파일을 올릴 때까지
    첫 리포트에 비교 섹션이 없다.

    담당자가 검증 화면에서 유형·과정을 확인하고 오류 행을 처리한 뒤에
    `POST /uploads/{draftId}/commit` 을 부르면 그때 카드가 만들어진다.
    여기서 취소하면 남는 것은 저장된 파일뿐이다.
    """
    files = [f for f in file if (f.filename or "").strip()]
    if not files:
        raise HTTPException(400, "파일이 없습니다")
    bad = [f.filename for f in files
           if not (f.filename or "").lower().endswith((".xlsx", ".xlsm"))]
    if bad:
        raise HTTPException(400, f"xlsx 파일만 지원합니다 — {', '.join(bad)}")

    os.makedirs(STORAGE_DIR, exist_ok=True)
    known, aliases, roster = _known_courses(db), _aliases(db), _roster(db)

    parts, paths = [], []
    for f in files:
        path = os.path.join(STORAGE_DIR, os.path.basename(f.filename))
        with open(path, "wb") as out:        # 원본은 그대로 보관, 이후 읽기만
            shutil.copyfileobj(f.file, out)
        paths.append(path)
        try:
            parts.append(analyze(path, known_courses=known, aliases=aliases,
                                 roster=roster, entry_course_key=entryCourseKey))
        except Exception as exc:             # noqa: BLE001
            raise HTTPException(400, f"{f.filename} 을(를) 읽지 못했습니다 — "
                                     f"{type(exc).__name__}: {exc}")

    result = _merge_analyses(parts)
    result["files"] = [{"name": os.path.basename(p), "path": p} for p in paths]

    draft = UploadDraft(filename=result["filename"], stored_path=paths[0],
                        analysis=result)
    db.add(draft)
    db.commit()
    return {"draftId": draft.id, **result}


def _merge_analyses(parts: List[dict]) -> dict:
    """여러 파일의 판정을 한 장으로 합친다.

    유형과 과정은 **첫 파일 것을 따른다.** 같이 올린 파일들은 같은 과정의
    다른 회차·조라고 보는 것이 자연스럽고, 파일마다 다른 과정을 붙일 수 있게
    하면 검증 화면이 파일 수만큼 늘어난다. 다르면 담당자가 화면에서 고친다.
    """
    if len(parts) == 1:
        return dict(parts[0])

    head = dict(parts[0])
    rows, context, sheets, warnings = [], {}, [], []
    total = {"recognized": 0, "ok": 0, "errors": 0, "warnings": 0}
    for p in parts:
        for r in p["rows"]:
            rows.append({**r, "file": p["filename"]})
        for k, v in (p.get("context") or {}).items():
            context.setdefault(k, v)
        sheets += [f'{p["filename"]} › {s}' for s in (p.get("sheets") or [])]
        warnings += p.get("warnings") or []
        for k in total:
            total[k] += p["summary"][k]

    kinds = {p["sourceType"]["type"] for p in parts}
    head["sourceType"] = dict(head["sourceType"])
    if len(kinds) > 1:
        head["sourceType"]["evidence"] += (
            f" (함께 올린 파일들의 판정이 갈립니다 — {', '.join(sorted(kinds))}. "
            f"첫 파일 기준으로 두었으니 확인해 주십시오.)")
    head.update({
        "filename": " + ".join(p["filename"] for p in parts),
        "rows": rows, "context": context, "sheets": sheets,
        "warnings": warnings, "summary": total,
    })
    return head


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: int, db: Session = Depends(get_db)):
    draft = db.get(UploadDraft, draft_id)
    if not draft:
        raise HTTPException(404, _gone(draft_id))
    return {"draftId": draft.id, "status": draft.status,
            "confirmed": draft.confirmed or None, "uploadId": draft.upload_id,
            **(draft.analysis or {})}


def _gone(draft_id: int) -> str:
    """판정 결과가 없을 때의 안내.

    무료 배포는 서버가 다시 뜨면 저장한 것이 전부 사라진다. 그때 담당자가
    보는 것은 '판정 결과 없음' 다섯 글자뿐이고, 자기가 뭘 잘못했는지 찾게 된다.
    무슨 일이 일어난 것인지 적어 준다.
    """
    return (f"판정 결과 {draft_id} 번을 찾을 수 없습니다. "
            f"서버가 다시 시작되면 올려 둔 것이 사라집니다(무료 요금제). "
            f"파일을 다시 올려 주십시오.")


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
        raise HTTPException(404, _gone(draft_id))
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

    # 함께 올린 파일을 모두 한 업로드로 넣는다. 회차 간 성장 비교(R-14)는
    # 카드가 다 모인 뒤에야 붙으므로, 파일마다 따로 만들면 완성되지 않는다.
    paths = [f["path"] for f in (draft.analysis or {}).get("files", [])
             if os.path.exists(f["path"])] or [draft.stored_path]

    upload = Upload(filename=draft.filename, stored_path=draft.stored_path,
                    warnings=[])
    db.add(upload)
    db.flush()

    all_cards, all_warnings = 0, []
    for path in paths:
        try:
            result = run_pipeline(path,
                                  roster=_roster(db),
                                  competency_map=_comp_map(db),
                                  auto_approve=False,   # 확정값이 이미 승인이다
                                  confirmed=confirmed)
        except Exception as exc:             # noqa: BLE001
            db.rollback()
            raise HTTPException(400, f"{os.path.basename(path)} 에서 카드를 "
                                     f"만들지 못했습니다 — "
                                     f"{type(exc).__name__}: {exc}")
        for card_json in result["cards"]:
            db.add(_to_row(upload.id, card_json))
        all_cards += len(result["cards"])
        all_warnings += result["warnings"]
        db.flush()
        _queue_handoffs(db, upload.id, result["handoffs"])

    upload.warnings = all_warnings
    result = {"warnings": all_warnings}

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
        "files": len(paths),
    }
    if req.generate:
        # 문장 생성은 1분 넘게 걸린다. 그 시간 동안 응답을 붙들고 있으면
        # 느린 서버에서는 중간에 끊기고(502), 그러면 여기까지 만든 것도
        # 무엇이 됐는지 알 수 없다. 뒤에서 돌리고 진행 상황을 물어보게 한다.
        jobs.start(upload.id)
        out["job"] = {"uploadId": upload.id, "state": "running",
                      "poll": f"/uploads/{upload.id}/status"}
    else:
        out["reports"] = []
    return out


# ══════════════════════════════════════════════════════════════
# 생성 작업 — 응답을 붙들지 않고 뒤에서 돌린다
# ══════════════════════════════════════════════════════════════
class _Jobs:
    """업로드별 생성 진행 상황. 서버가 살아 있는 동안만 기억한다.

    큐를 따로 두지 않는 이유 — 작업의 진실은 이미 DB(handoffs) 에 있다.
    여기 있는 것은 "지금 돌고 있는가"와 "왜 실패했는가"뿐이다.
    """

    def __init__(self) -> None:
        self._state: Dict[int, dict] = {}
        self._lock = threading.Lock()

    def start(self, upload_id: int) -> None:
        with self._lock:
            if (self._state.get(upload_id) or {}).get("state") == "running":
                return
            self._state[upload_id] = {"state": "running", "error": None,
                                      "done": 0, "total": 0,
                                      "startedAt": time.monotonic()}
        threading.Thread(target=self._run, args=(upload_id,),
                         daemon=True, name=f"generate-{upload_id}").start()

    def _progress(self, upload_id: int, done: int, total: int) -> None:
        with self._lock:
            slot = self._state.get(upload_id)
            if slot is not None:
                slot["done"], slot["total"] = done, total

    def _run(self, upload_id: int) -> None:
        db = SessionLocal()
        try:
            gen = drain(db, upload_id=upload_id,
                        on_progress=lambda d, t: self._progress(upload_id, d, t))
            reports = _build_all(db, upload_id)
            with self._lock:
                started = (self._state.get(upload_id) or {}).get("startedAt")
                self._state[upload_id] = {
                    "state": "done", "error": None,
                    "generation": gen, "reports": reports,
                    "elapsed": round(time.monotonic() - started, 1) if started else None}
        except Exception as exc:                           # noqa: BLE001
            db.rollback()
            with self._lock:
                self._state[upload_id] = {
                    "state": "error",
                    "error": f"{type(exc).__name__}: {exc}"}
        finally:
            db.close()

    def get(self, upload_id: int) -> dict:
        with self._lock:
            return dict(self._state.get(upload_id) or {})


jobs = _Jobs()


@router.get("/{upload_id}/status")
def upload_status(upload_id: int, db: Session = Depends(get_db)):
    """생성이 어디까지 왔는지. 화면이 이걸 몇 초마다 물어본다."""
    cards = db.query(Card).filter(Card.upload_id == upload_id).all()
    if not cards:
        raise HTTPException(404, _gone(upload_id))

    ids = [c.id for c in cards]
    pending = (db.query(Handoff)
                 .filter(Handoff.card_id.in_(ids), Handoff.status == "pending")
                 .count())
    total = db.query(Handoff).filter(Handoff.card_id.in_(ids)).count()

    job = jobs.get(upload_id)
    state = job.get("state")
    if not state:
        # 서버가 다시 떴거나 이 업로드는 생성을 시킨 적이 없다.
        state = "done" if pending == 0 else "idle"

    # 진행은 돌고 있는 작업이 세는 값을 먼저 믿는다. DB 상태만 보면 한 묶음이
    # 통째로 끝나기 전까지 0 에서 멈춰 있어, 아무 일도 안 하는 것처럼 보인다.
    done = job.get("done") if job.get("state") == "running" else total - pending
    if not job.get("total") and state == "running":
        done = total - pending

    out = {"uploadId": upload_id, "state": state,
           "done": done or 0, "total": job.get("total") or total,
           "error": job.get("error"),
           "cards": _card_briefs(db, upload_id),
           "flags": _flag_list(db, upload_id)}
    out.update(_eta(job, out["done"], out["total"]))
    if state == "done":
        out["reports"] = job.get("reports") or _report_links(db, upload_id)
        out["generation"] = job.get("generation")
    return out


def _eta(job: dict, done: int, total: int) -> dict:
    """지금까지 걸린 시간으로 남은 시간을 어림한다.

    호출 한 건이 대체로 비슷하게 걸리므로, 평균에 남은 건수를 곱하면 된다.
    한 건도 안 끝났으면 아직 알 수 없다고 말한다 — 아무 숫자나 보여 주는 것보다
    "곧 시작합니다"가 정직하다.
    """
    started = job.get("startedAt")
    if job.get("state") == "done":
        el = job.get("elapsed")
        return {"elapsedSec": el, "etaSec": 0,
                "etaText": f"{_mmss(el)} 걸렸습니다" if el else "완료"}
    if not started:
        return {"elapsedSec": None, "etaSec": None, "etaText": None}

    elapsed = round(time.monotonic() - started, 1)
    if not done or not total:
        return {"elapsedSec": elapsed, "etaSec": None,
                "etaText": "곧 시작합니다"}
    per = elapsed / done
    left = max(0, total - done)
    eta = round(per * left)
    return {"elapsedSec": elapsed, "etaSec": eta,
            "etaText": f"약 {_mmss(eta)} 남음" if left else "마무리 중"}


def _mmss(sec) -> str:
    s = int(round(sec or 0))
    return f"{s}초" if s < 60 else f"{s // 60}분 {s % 60}초"


def _report_links(db: Session, upload_id: int) -> List[dict]:
    out = []
    for c in db.query(Card).filter(Card.upload_id == upload_id).all():
        ok, reason = is_sendable(c.card_json)
        out.append({"card_id": c.id, "name": c.person_name,
                    "report_id": c.report.id if c.report else None,
                    "html": f"/reports/{c.report.id}/html" if c.report else None,
                    "blocked_by": None if ok else reason})
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
        person = c.card_json.get("person") or {}
        out.append({"cardId": c.id, "name": c.person_name,
                    # 여러 파일을 함께 올리면 화면이 파일별로 갈라 보여 준다
                    "file": c.source_file, "round": c.round_label,
                    # 사번은 없을 수 있다. 화면이 문자열로 다루므로 None 을
                    # 그대로 보내고, 대체값은 화면이 정하게 둔다.
                    "empId": c.person_id,
                    # 발송 화면이 수신자 표를 그릴 때 쓴다. 없으면 못 보낸다.
                    "email": person.get("email") or None,
                    "status": c.person_status,
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
        ok, reason = is_reportable(c.card_json)
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
    """생성 작업을 제 카드에 붙인다.

    **이름만으로 가리면 안 된다.** 1차수와 2차수를 함께 올리면 같은 사람의
    카드가 둘이고, 이름을 열쇠로 쓰면 전부 첫 카드로 몰린다. 그러면 2차수
    카드는 문장이 하나도 없는 리포트가 되어, 겉보기에 '리포트가 하나만
    만들어진' 것처럼 보인다.

    그래서 좁은 열쇠부터 차례로 맞춰 본다 — (이름·파일·행) → (이름·파일)
    → (이름). 마지막 것은 예전과 같은 한계이고, 그때는 동명이인 플래그가
    이미 붙어 있다.
    """
    exact, by_file, by_name = {}, {}, {}
    for c in db.query(Card).filter(Card.upload_id == upload_id).all():
        exact.setdefault((c.person_name, c.source_file, c.source_row), c.id)
        by_file.setdefault((c.person_name, c.source_file), c.id)
        by_name.setdefault(c.person_name, c.id)

    for h in handoffs:
        name = h.get("person")
        cid = (exact.get((name, h.get("source_file"), h.get("source_row")))
               or by_file.get((name, h.get("source_file")))
               or by_name.get(name))
        if cid:
            db.add(Handoff(card_id=cid, rule_id=h["rule_id"],
                           task=h["task"], payload=h["payload"]))


def _roster(db: Session) -> dict:
    return {
        "people": [{"person_id": r.person_id, "name": r.name, "alias": r.alias,
                    "email": r.email if r.dispatchable else None,
                    "status": r.status, "position": r.position,
                    "부서": r.department, "팀": r.team}
                   for r in db.query(RosterEntry).all()],
        "resolved": {r.memo_key: r.person_id
                     for r in db.query(PersonResolution).all()},
    }


def _comp_map(db: Session) -> dict:
    return {m.raw_name.lower(): m.canonical
            for m in db.query(CompetencyMapping).all()}
