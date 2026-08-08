"""파이프라인 진입점.

    엑셀 경로 -> {cards, handoffs, warnings, summary}

여기까지가 "파이썬이 확신할 수 있는 구간"이다. 문장 생성이 필요한 작업은
handoffs 에 담겨 나가고, generation/worker.py 가 가져간다.
"""
from __future__ import annotations

import datetime as dt
import os
from typing import Any, Dict, List, Optional

from .builder import build_cards
from .detect import SCORE, detect, extract_question_defs
from .reader import read_workbook
from .rules.base import RuleContext, auto_resolve, max_severity
from .rules.report import r19_extract_best_practice


def json_safe(obj: Any) -> Any:
    """DB(JSON 컬럼)에 저장 가능한 형태로 정규화한다.

    엑셀에는 파이썬 객체로 읽히는 값이 여럿 있다 — 서식이 섞인 셀, 날짜,
    시간, 수식 결과. 규칙 단계에서는 원래 타입이 필요하므로(R-01 이 날짜를
    변환하려면 datetime 이어야 한다) 손대지 않고, **저장 직전 여기서 한 번만**
    정리한다. 모르는 타입은 버리지 않고 문자열로 남긴다 — 값이 사라지는 것보다
    모양이 바뀌는 편이 낫다.
    """
    if obj is None or isinstance(obj, (str, bool, int, float)):
        return obj
    if isinstance(obj, dict):
        return {str(k): json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, (dt.datetime, dt.date, dt.time)):
        return obj.isoformat()
    return str(obj)


def apply_confirmations(cards: List[dict], confirmed: dict,
                        ctx: RuleContext) -> List[dict]:
    """담당자가 검증 화면에서 확정한 것들을 카드에 새긴다.

    고친 값은 **원본 엑셀이 아니라 카드에만** 반영된다. 원본은 검증 기준이라
    손대지 않는다는 것이 계약의 첫 줄이고, 그래서 무엇을 무엇으로 바꿨는지
    카드에 함께 남긴다 — 나중에 "이 점수 왜 다르지?"에 답할 수 있어야 한다.
    """
    excluded = {int(r) for r in (confirmed.get("excludedRows") or [])}
    fixes: Dict[int, List[dict]] = {}
    for f in (confirmed.get("rowFixes") or []):
        fixes.setdefault(int(f["rowNumber"]), []).append(f)

    stype = confirmed.get("sourceType")
    kept = []
    for card in cards:
        rows = _card_rows(card)
        if rows & excluded:
            ctx.warnings.append(
                f'{card["person"]["name"]} — 담당자가 제외한 행이라 카드를 만들지 않았습니다')
            continue

        if stype:
            card["source_type"] = {
                "type": stype,
                "evidence": "담당자가 검증 화면에서 확정한 유형입니다 "
                            "(엔진 재판정 없음)",
                "confirmed_by_operator": True,
                "confirmed_by": confirmed.get("operator") or "담당자",
            }
        if confirmed.get("courseId"):
            card.setdefault("context", {})["_course_id"] = confirmed["courseId"]
        if confirmed.get("wave"):
            card.setdefault("context", {})["_wave"] = confirmed["wave"]

        for rn in sorted(rows & set(fixes)):
            for f in fixes[rn]:
                _apply_fix(card, f, ctx)
        kept.append(card)
    return kept


def _card_rows(card: dict) -> set:
    prov = card.get("provenance") or {}
    raw = prov.get("rows") or prov.get("row")
    if raw is None:
        return set()
    if isinstance(raw, (list, tuple, set)):
        return {int(x) for x in raw if str(x).isdigit()}
    return {int(raw)} if str(raw).isdigit() else set()


def _apply_fix(card: dict, fix: dict, ctx: RuleContext) -> None:
    field, value = fix.get("field"), fix.get("value")
    person = card.setdefault("person", {})
    before = None

    if field in ("empId", "duplicate"):
        before, person["person_id"] = person.get("person_id"), value
    elif field == "email":
        before, person["email"] = person.get("email"), value
    elif field == "name":
        before, person["name"] = person.get("name"), value
    elif field == "score":
        before = _fix_score(card, fix)
    else:
        ctx.warnings.append(f"모르는 수정 필드라 건너뛰었습니다 — {field}")
        return

    card.setdefault("provenance", {}).setdefault("operator_fixes", []).append({
        "row": fix.get("rowNumber"), "field": field,
        "from": before, "to": value,
        "by": fix.get("by") or "담당자",
        "note": "원본 엑셀은 그대로이며 카드 생성 입력에만 반영되었습니다",
    })


def _fix_score(card: dict, fix: dict) -> object:
    """점수 수정. 어느 역량인지는 label 로 짚는다."""
    label = (fix.get("label") or "").strip()
    for s in card.get("scores", []):
        if not label or label in str(s.get("area") or s.get("name") or ""):
            before = s.get("value")
            s["value"] = float(fix["value"]) if fix.get("value") not in (None, "") else None
            return before
    return None


def run_pipeline(path: str,
                 roster: Optional[dict] = None,
                 competency_map: Optional[dict] = None,
                 auto_approve: bool = False,
                 confirmed: Optional[dict] = None) -> Dict[str, object]:
    """엑셀 → 카드.

    `confirmed` 는 검증 화면에서 담당자가 확정한 것들이다
    ({sourceType, courseId, courseTitle, wave, rowFixes, excludedRows}).
    확정값이 오면 엔진은 그 자리를 **다시 판정하지 않는다** — 통합 명세 §5-4 의
    답이 이 한 줄이다. 담당자가 본 화면과 결과가 달라지는 것이 가장 나쁘다.
    """
    sheets = read_workbook(path)

    # R-08: 문항 정의가 별도 시트에 있으면 먼저 확보한다
    question_defs, def_sheets = extract_question_defs(sheets)

    ctx = RuleContext(
        source_file=os.path.basename(path),
        roster={**(roster or {}), "question_defs": question_defs},
        competency_map=competency_map or {},
        auto_approve=auto_approve,
    )

    cards: List[dict] = []
    used_sheets: List[str] = []

    for sheet in sheets:
        schema = detect(sheet)
        if sheet.name not in def_sheets:
            # 문항정의 시트는 애초에 데이터 시트가 아니므로 경고를 내지 않는다
            ctx.warnings.extend(schema.warnings)

        # 데이터 시트의 조건: 사람 열과 점수 열이 둘 다 있고 행이 있다.
        # 문항정의 시트는 숫자 열이 없어 여기서 자연히 걸러진다.
        if not schema.name_column or not schema.by_kind(SCORE) or not schema.data_rows:
            continue

        ctx.source_sheet = sheet.name
        made = build_cards(sheet, schema, ctx)
        if made:
            used_sheets.append(sheet.name)
            cards.extend(made)

    if confirmed:
        cards = apply_confirmations(cards, confirmed, ctx)

    if not cards:
        ctx.warnings.append(
            "카드를 한 장도 만들지 못했습니다 — 이름 열 또는 점수 열을 찾지 못했을 수 있습니다")

    # R-19: 개인이 아니라 그룹 단위 규칙이라 카드가 다 모인 뒤에 한 번 돈다
    best_practice = r19_extract_best_practice(cards, ctx)

    if auto_approve:
        for card in cards:
            auto_resolve(card)

    by_type: Dict[str, int] = {}
    for c in cards:
        t = (c.get("source_type") or {}).get("type") or "unknown"
        by_type[t] = by_type.get(t, 0) + 1

    # 카드와 큐는 JSON 컬럼에 그대로 들어간다. 엑셀에서 흘러든 값 중
    # 저장할 수 없는 것이 남아 있으면 여기서 마지막으로 걸러 낸다.
    cards = [json_safe(c) for c in cards]

    return {
        "cards": cards,
        "handoffs": json_safe(ctx.handoffs),
        "warnings": ctx.warnings,
        "summary": {
            "sheets": used_sheets,
            "cards": len(cards),
            "by_source_type": by_type,
            "question_defs": len(question_defs),
            "pending_generation": len(ctx.handoffs),
            "blocked": sum(1 for c in cards if max_severity(c) == "hold"),
            "best_practice_group": (best_practice or {}).get("group_size"),
        },
    }
