"""행 단위 검증 — UI 검증 화면의 표를 만드는 곳 (통합 지점 ①).

카드를 만들기 **전에** 돈다. 카드가 된 뒤에 잡는 문제는 이미 늦다 —
담당자는 엑셀의 몇 행이 어떻게 잘못됐는지를 보고 고치고 싶어 하지,
카드 JSON 을 보고 싶어 하지 않는다.

여기서 나가는 `issueCode` 는 반드시 `contract.ISSUE_CODES` 에 있는 것이어야
한다. UI 가 그 코드로 수정 모달의 입력 필드를 고르기 때문이다. 새 검사를
추가할 때는 contract.py 를 먼저 고친다.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional

from contract import ISSUE_CODES
from .detect import EMAIL, EMP_ID, NAME, SCORE, SUMMARY, DetectedSchema
from .reader import Sheet, looks_numeric

EMPID_OK = re.compile(r"^[A-Za-z]{1,3}-?\d{3,}$|^\d{4,}$")
EMAIL_OK = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

# 사번에서 흔한 오타 — 손으로 옮겨 적을 때 숫자와 글자가 바뀐다
CONFUSED = {"O": "0", "o": "0", "I": "1", "l": "1", "S": "5", "B": "8"}


def validate_sheet(sheet: Sheet, schema: DetectedSchema,
                   roster: Optional[dict] = None) -> dict:
    """{rows, summary} 를 돌려준다. rows 는 문제가 있는 행만 담는다."""
    rows: List[dict] = []
    seen_names: Dict[str, List[int]] = {}
    roster_people = ((roster or {}).get("people")) or []

    name_col = schema.name_column
    emp_cols = schema.by_kind(EMP_ID)
    email_cols = schema.by_kind(EMAIL)
    score_cols = [c for c in schema.by_kind(SCORE)] + list(schema.by_kind(SUMMARY))

    for r in schema.data_rows:
        row = sheet.row(r)
        excel_row = r + 1                       # 엑셀은 1부터 센다 — 담당자가 보는 번호
        name = _cell(row, name_col)
        if name:
            seen_names.setdefault(name, []).append(excel_row)

        found: List[dict] = []
        if name_col and not name:
            found.append(_issue("NAME_MISSING", excel_row, "", None,
                                "이름 칸이 비어 있습니다"))

        for c in emp_cols:
            found += _check_empid(_cell(row, c), excel_row, c.header)
        for c in email_cols:
            found += _check_email(_cell(row, c), excel_row, c.header)
        for c in score_cols:
            found += _check_score(_cell(row, c), excel_row, c.header, c.scale)

        for f in found:
            f["name"] = name
            f["empId"] = next((_cell(row, c) for c in emp_cols if _cell(row, c)), "")
            rows.append(f)

    rows += _check_duplicates(seen_names, roster_people)
    rows.sort(key=lambda x: (x["rowNumber"], x["issueCode"]))

    total = len(schema.data_rows)
    bad_rows = {x["rowNumber"] for x in rows}
    return {
        "rows": rows,
        "summary": {
            "recognized": total,
            "ok": total - len(bad_rows),
            "errors": sum(1 for x in rows if x["severity"] == "error"),
            "warnings": sum(1 for x in rows if x["severity"] == "warning"),
        },
    }


# --------------------------------------------------------------------------
def _cell(row, col) -> str:
    if col is None or col.index >= len(row):
        return ""
    return (row[col.index].text or "").strip()


def _issue(code: str, row_number: int, original: str,
           suggested: Optional[str], message: str, **extra) -> dict:
    spec = ISSUE_CODES[code]                    # 없는 코드면 여기서 바로 터진다
    return {
        "rowNumber": row_number,
        "issueCode": code,
        "severity": spec["default_severity"],
        "field": spec["field"],
        "message": message,
        "originalValue": original,
        "suggestedValue": suggested or "",
        **extra,
    }


def _check_empid(value: str, row_number: int, header: str) -> List[dict]:
    if not value:
        return [_issue("EMPID_MISSING", row_number, "", None,
                       f"사번 없음 — '{header}' 칸이 비어 있습니다")]
    if EMPID_OK.match(value):
        return []
    fixed = "".join(CONFUSED.get(ch, ch) for ch in value)
    if fixed != value and EMPID_OK.match(fixed):
        wrong = next(ch for ch in value if ch in CONFUSED)
        return [_issue("EMPID_FORMAT", row_number, value, fixed,
                       f"사번 형식 오류 — 숫자 {CONFUSED[wrong]}이 "
                       f"문자 {wrong}로 입력됨")]
    return [_issue("EMPID_FORMAT", row_number, value, None,
                   f"사번 형식 오류 — '{value}' 는 사번 형식이 아닙니다")]


def _check_email(value: str, row_number: int, header: str) -> List[dict]:
    if not value:
        return [_issue("EMAIL_MISSING", row_number, "", None,
                       "이메일 없음 — 이 사람에게는 리포트를 보낼 수 없습니다")]
    if EMAIL_OK.match(value):
        return []
    return [_issue("EMAIL_FORMAT", row_number, value, _fix_email(value),
                   f"이메일 형식 오류 — '{value}'")]


_TYPO_DOMAIN = {"gmail.con": "gmail.com", "gmai.com": "gmail.com",
                "naver.con": "naver.com", "nate.con": "nate.com",
                "hanmail.ne": "hanmail.net"}


def _fix_email(value: str) -> Optional[str]:
    """흔한 도메인 오타만 고쳐 제안한다. 확신이 없으면 제안하지 않는다."""
    if value.count("@") != 1:
        return None
    local, domain = value.split("@")
    fixed = _TYPO_DOMAIN.get(domain.lower())
    return f"{local}@{fixed}" if fixed else None


def _check_score(value: str, row_number: int, header: str,
                 scale: Optional[dict]) -> List[dict]:
    if not value:
        return [_issue("SCORE_MISSING", row_number, "", None,
                       f"점수 누락 — '{header}'")]
    if not looks_numeric(value):
        return [_issue("SCORE_NOT_NUMERIC", row_number, value, None,
                       f"점수 칸에 숫자가 아닌 값 — '{header}' 에 '{value}'")]
    if not scale:
        return [_issue("SCALE_UNKNOWN", row_number, value, None,
                       f"'{header}' 의 척도를 찾지 못해 범위 검사를 건너뛰었습니다")]
    lo, hi = scale.get("min"), scale.get("max")
    if lo is None or hi is None:
        return []
    n = float(value)
    if lo <= n <= hi:
        return []
    return [_issue("SCORE_OUT_OF_RANGE", row_number, value, None,
                   f"점수 범위 이탈 — '{header}' {value}점 (허용 {_num(lo)}-{_num(hi)})")]


def _num(v) -> str:
    f = float(v)
    return str(int(f)) if f == int(f) else str(f)


def _check_duplicates(seen: Dict[str, List[int]],
                      roster_people: List[dict]) -> List[dict]:
    """같은 이름이 두 번 이상 나오면 담당자에게 누가 누구인지 묻는다 (R-15).

    이름이 같다고 같은 사람이라 단정하지 않는다. 그렇게 단정하면 두 사람의
    평가가 한 장의 카드로 합쳐지고, 그 리포트는 둘 다에게 틀린 것이 된다.
    """
    out = []
    for name, rownums in seen.items():
        if len(rownums) < 2:
            continue
        cands = [{"name": p["name"], "empId": p.get("person_id"),
                  "dept": p.get("부서")}
                 for p in roster_people if p.get("name") == name]
        for rn in rownums:
            out.append(_issue(
                "DUPLICATE_NAME", rn, name, None,
                f"동명이인 후보 — 명단에 {name} {len(rownums)}명 (R-15 확인)",
                candidates=cands))
    return out
