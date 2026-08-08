"""판정만 하고 멈추는 단계 — UI 통합 지점 ①.

카드를 만들지 않는다. 담당자가 검증 화면에서 유형과 과정을 확인하기 전까지는
아무것도 확정하지 않는 것이 이 모듈의 요점이다. 예전에는 업로드 한 번에
카드까지 만들어 버려서, 담당자가 "이 유형 아닌데요" 하면 이미 만들어진
카드를 되돌려야 했다.

    analyze(path, ...) -> {sourceType, courseMatch, wave, context, rows, summary}

여기서 나가는 필드 이름은 파이썬 관례(snake_case)가 아니라 **UI 계약 그대로**
(camelCase)다. 경계에서 이름을 바꾸면 양쪽 문서를 대조할 때 사람이 헷갈린다.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional

from . import courses
from .builder import judge_from_context
from .detect import SCORE, detect, extract_question_defs
from .reader import read_workbook
from .validate import validate_sheet

PROGRAM_KEYS = ("과정명", "특강명", "진단명", "프로그램", "교육명")
INSTRUCTOR_KEYS = ("강사", "진행자", "코치", "instructor")
DATE_KEYS = ("날짜", "일자", "기간", "시행일", "date")
ROUND_KEYS = ("차수", "회차", "주차")


def analyze(path: str,
            known_courses: Optional[List[dict]] = None,
            aliases: Optional[Dict[str, str]] = None,
            roster: Optional[dict] = None,
            entry_course_key: Optional[str] = None) -> dict:
    """엑셀 한 개를 읽고 판정 결과만 돌려준다. 카드도 DB 도 건드리지 않는다."""
    sheets = read_workbook(path)
    _, def_sheets = extract_question_defs(sheets)

    data_sheets = []
    warnings: List[str] = []
    for sheet in sheets:
        schema = detect(sheet)
        if sheet.name not in def_sheets:
            warnings.extend(schema.warnings)
        if schema.name_column and schema.by_kind(SCORE) and schema.data_rows:
            data_sheets.append((sheet, schema))

    if not data_sheets:
        return {
            "filename": os.path.basename(path),
            "sourceType": {"type": "unknown",
                           "evidence": "판정 근거 부족 — 이름 열 또는 점수 열을 "
                                       "찾지 못했습니다. 담당자 지정이 필요합니다.",
                           "confirmedByOperator": False},
            "courseMatch": courses.match(None, None, None, None, [], {}),
            "context": {}, "rows": [],
            "summary": {"recognized": 0, "ok": 0, "errors": 0, "warnings": 0},
            "sheets": [], "warnings": warnings,
        }

    # 문맥은 시트마다 흩어져 있을 수 있다. 먼저 나온 값을 이긴 것으로 둔다 —
    # 보통 첫 데이터 시트가 대표 시트다.
    context: Dict[str, object] = {}
    for _, schema in data_sheets:
        for k, v in (schema.meta or {}).items():
            context.setdefault(str(k), v)

    direction = next((s.direction for _, s in data_sheets
                      if s.direction == "aggregated_responses"), "individual_row")
    source_type = judge_from_context(context, direction)

    rows: List[dict] = []
    total = {"recognized": 0, "ok": 0, "errors": 0, "warnings": 0}
    for sheet, schema in data_sheets:
        got = validate_sheet(sheet, schema, roster)
        for r in got["rows"]:
            r["sheet"] = sheet.name
        rows.extend(got["rows"])
        for k in total:
            total[k] += got["summary"][k]

    title = _pick(context, PROGRAM_KEYS) or _title_from_filename(path)
    instructor = _pick(context, INSTRUCTOR_KEYS)
    round_label = _pick(context, ROUND_KEYS)

    match = courses.match(title, source_type["type"], instructor, round_label,
                          known_courses or [], aliases or {})
    if entry_course_key:
        # 과정 카드에서 들어온 프리필 경로. 제안이 아니라 '확인해 달라'는 상태다.
        match = {**match, "mode": "link", "suggestedCourseId": entry_course_key,
                 "prefilled": True,
                 "evidence": f"{match['evidence']} (과정 카드에서 시작한 업로드라 "
                             f"해당 과정이 미리 채워져 있습니다 — 파일 내용과 "
                             f"일치하는지 확인해 주십시오.)"}

    out = {
        "filename": os.path.basename(path),
        "sourceType": {"type": source_type["type"],
                       "evidence": _humanize(source_type["evidence"]),
                       "confirmedByOperator": False},
        "courseMatch": match,
        "context": {str(k): _plain(v) for k, v in context.items()},
        "rows": rows,
        "summary": total,
        "sheets": [s.name for s, _ in data_sheets],
        "warnings": warnings,
    }
    if source_type["type"] == "진단서베이":
        out["wave"] = _wave(context, match, known_courses or [])
    return out


# --------------------------------------------------------------------------
def _humanize(evidence: str) -> str:
    """엔진 어투를 화면에 실을 문장으로. UI 가 이 문자열을 그대로 보여 준다."""
    text = str(evidence or "").strip()
    return text if text.startswith("판정 근거") else f"판정 근거 — {text}"


def _pick(context: dict, keys) -> Optional[str]:
    for want in keys:
        for actual, v in context.items():
            if want in str(actual).lower() or want in str(actual):
                s = _plain(v)
                if s:
                    return s
    return None


def _plain(v) -> str:
    if v is None:
        return ""
    if hasattr(v, "isoformat"):
        return v.isoformat()[:10]
    return str(v).strip()


_FNAME = re.compile(r"^\d{6,8}[_\-\s]*")


def _title_from_filename(path: str) -> str:
    """메타 블록에 과정명이 없을 때의 마지막 수단.

    '20260805_3차수_C조.xlsx' 처럼 날짜와 차수만 있는 파일도 있다. 이때는
    과정명을 지어내지 않고 빈 값을 돌려준다 — 지어내면 그 이름으로 과정이
    하나 생기고, 다음 회차와 이어지지 않는다.
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = _FNAME.sub("", stem)
    stem = re.sub(r"\d+\s*(차수|차|회차|회)", " ", stem)
    stem = re.sub(r"[A-Za-z가-힣]\s*조\b", " ", stem)
    stem = re.sub(r"[_\-]+", " ", stem).strip()
    return stem if len(stem) >= 2 else ""


def _wave(context: dict, match: dict, known: List[dict]) -> dict:
    """진단서베이 시행 회차 제안. 같은 과정의 기존 시행 수 + 1 이 기본이다."""
    date = _pick(context, DATE_KEYS) or ""
    cid = match.get("suggestedCourseId")
    prev = next((c for c in known if c["courseId"] == cid), None)
    done = len(prev.get("rounds") or []) if prev else 0
    basis = f"{date[:7] or '날짜 미상'} 시행"
    if prev and prev.get("rounds"):
        basis += f" · 직전 {prev['rounds'][-1]}"
    else:
        basis += " · 이전 시행 기록 없음"
    return {"suggested": done + 1, "basis": basis}
