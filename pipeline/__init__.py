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


def run_pipeline(path: str,
                 roster: Optional[dict] = None,
                 competency_map: Optional[dict] = None,
                 auto_approve: bool = False) -> Dict[str, object]:
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
