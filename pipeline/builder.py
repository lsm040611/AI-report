"""정규화: 시트 + 추론된 스키마 -> 카드 N장.

계약의 "모든 양식은 카드 하나로 수렴한다"를 실행하는 지점.
1행 1인이든 16응답 3인이든 여기를 지나면 같은 구조가 된다.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, List, Optional

from .detect import (NAME, NARRATIVE, NOTE, RELATION, SCORE, SUMMARY,
                     DetectedSchema)
from .reader import Sheet, looks_numeric
from .rules import report, semantic, structural, survey
from .rules.base import RuleContext

SCHEMA_VERSION = "0.5"


def build_cards(sheet: Sheet, schema: DetectedSchema, ctx: RuleContext) -> List[dict]:
    if schema.direction == "aggregated_responses":
        return _build_aggregated(sheet, schema, ctx)
    return _build_individual(sheet, schema, ctx)


# --------------------------------------------------------------------------
def _build_individual(sheet: Sheet, schema: DetectedSchema,
                      ctx: RuleContext) -> List[dict]:
    cards = []
    name_col = schema.name_column
    if name_col is None:
        return cards
    summary_col = schema.summary_column

    for r in schema.data_rows:
        row = sheet.row(r)
        raw_name = row[name_col.index].text.strip() if name_col.index < len(row) else ""
        name, alias = split_name(raw_name)

        scores = [{
            "area_name": col.header,
            "definition": col.desc or None,
            "score": row[col.index].value if col.index < len(row) else None,
            "scale": dict(col.scale) if col.scale else None,
            "source_cell": row[col.index].coord if col.index < len(row) else None,
        } for col in schema.by_kind(SCORE)]

        narratives = [{
            "original_label": col.full_label,
            "language": _guess_language(row[col.index].text if col.index < len(row) else ""),
            "runs": _runs_of(row, col.index),
            "source_cell": row[col.index].coord if col.index < len(row) else None,
        } for col in schema.by_kind(NARRATIVE)
            if col.index < len(row) and row[col.index].text.strip()]

        # R-06 게이트: 각주·안내 행은 카드를 만들지 않는다
        if not structural.r06_is_person_row(name, scores, narratives):
            continue

        # 원본 평균란이 있으면 R-03 이 비교할 수 있게 따로 보관한다
        score_summary = {}
        if summary_col is not None and summary_col.index < len(row):
            raw = row[summary_col.index].value
            if raw is not None and str(raw).strip():
                score_summary["original_average"] = raw
                score_summary["original_cell"] = row[summary_col.index].coord

        card = {
            "schema_version": SCHEMA_VERSION,
            "direction": "individual_row",
            "person": {"name": name, "alias": alias,
                       "status": "regular", "person_id": None},
            "context": dict(schema.meta),
            "scores": scores,
            "score_summary": score_summary,
            "narratives": narratives,
            "aggregation": None,
            "flags": [],
            "provenance": {"file": ctx.source_file, "sheet": sheet.name,
                           "row": r + 1, "applied_rules": []},
        }
        _apply_common(card, ctx, _note_text(row, schema))
        cards.append(card)
    return cards


# --------------------------------------------------------------------------
def _build_aggregated(sheet: Sheet, schema: DetectedSchema,
                      ctx: RuleContext) -> List[dict]:
    name_col, rel_cols = schema.name_column, schema.by_kind(RELATION)
    if name_col is None:
        return []
    rel_col = rel_cols[0] if rel_cols else None

    grouped: Dict[str, List[dict]] = defaultdict(list)
    rows_of: Dict[str, List[int]] = defaultdict(list)

    for r in schema.data_rows:
        row = sheet.row(r)
        if name_col.index >= len(row):
            continue
        name, _alias = split_name(row[name_col.index].text.strip())
        if not name:
            continue
        answers = {c.header: (row[c.index].value if c.index < len(row) else None)
                   for c in schema.by_kind(SCORE)}
        grouped[name].append({
            "relation": (row[rel_col.index].text.strip() if rel_col
                         and rel_col.index < len(row) else "미상") or "미상",
            "answers": answers,
            "free_text": [{"label": c.full_label, "relation_hidden": True,
                           "text": row[c.index].text}
                          for c in schema.by_kind(NARRATIVE)
                          if c.index < len(row) and row[c.index].text.strip()],
        })
        rows_of[name].append(r + 1)

    cards = []
    for name, responses in grouped.items():
        card = survey.r09_aggregate_responses(name, responses)
        card.update({
            "schema_version": SCHEMA_VERSION,
            "context": dict(schema.meta),
            "score_summary": {},
            "narratives": _merge_free_text(responses),
            "flags": [],
        })
        card["person"].update({"alias": None, "status": "regular", "person_id": None})
        card["provenance"].update({"file": ctx.source_file, "sheet": sheet.name,
                                   "rows": rows_of[name]})
        survey.r08_join_question_defs(card, ctx)
        survey.r10_apply_anonymity(card, ctx)
        _apply_common(card, ctx, "")
        cards.append(card)
    return cards


def _merge_free_text(responses: List[dict]) -> List[dict]:
    """주관식은 응답자별 원문을 raw_items 로 모으고, 노출은 요약만 허용."""
    buckets: Dict[int, List[dict]] = defaultdict(list)
    labels: Dict[int, str] = {}
    for resp in responses:
        for i, item in enumerate(resp.get("free_text", [])):
            buckets[i].append({"relation_hidden": True, "text": item["text"]})
            labels.setdefault(i, item.get("label") or f"[주관식{i + 1}]")
    return [{
        "original_label": labels[i],
        "language": "ko",
        "exposure_policy": "summarize_only",
        "raw_items": items,
    } for i, items in sorted(buckets.items())]


# --------------------------------------------------------------------------
def _apply_common(card: dict, ctx: RuleContext, note: str) -> None:
    """모든 카드가 공통으로 통과하는 정제 규칙 순서.

    순서에 의미가 있다: 척도를 붙인 뒤 평균을 계산하고, 역할을 판별한 뒤
    source_type 을 정하고, 그 다음에야 큐레이션 형태가 결정된다.
    """
    structural.r01_normalize_dates(card, ctx)
    structural.r02_cast_scores(card, ctx)
    structural.r04_attach_scale(card, ctx)
    structural.r03_recompute_average(card, ctx)
    structural.r05_verify_runs(card, ctx)

    semantic.r12_apply(card, ctx)
    # 역할이 정해진 뒤에야 강조의 뜻을 물을 수 있다 (강점 칸이냐 보완 칸이냐로 뜻이 갈린다)
    structural.r05_request_semantic_check(card, ctx)
    semantic.r07_detect_audit(card, ctx, note)
    semantic.r15_resolve_person(card, ctx)
    semantic.r18_map_competencies(card, ctx)
    semantic.r13_prepare_translation(card, ctx)

    card["source_type"] = judge_source_type(card)

    report.r11_anonymize(card, ctx)
    report.r17_prepare_curation(card, ctx)


# --------------------------------------------------------------------------
def judge_source_type(card: dict) -> dict:
    """누적교육 / 단발특강 / 진단서베이 판정 + 근거.

    계약상 판정만으로는 다음 단계로 못 간다.
    confirmed_by_operator 가 True 가 되어야 진행된다.
    """
    context = card.get("context", {})
    keys = " ".join(str(k) for k in context.keys())

    if card.get("direction") == "aggregated_responses":
        return {"type": "진단서베이",
                "evidence": "데이터 방향이 'N응답→1인'이고 평가자 관계 열 존재",
                "confirmed_by_operator": False}

    if any(k in keys for k in ("차수", "회차")):
        round_label = next((str(v) for k, v in context.items()
                            if "차수" in str(k) or "회차" in str(k)), "")
        return {"type": "누적교육",
                "evidence": f"차수 표기({round_label}) 존재 → 회차 간 성장 연결 대상",
                "confirmed_by_operator": False}

    if any(k in keys for k in ("특강", "장소", "참석")):
        return {"type": "단발특강",
                "evidence": "차수 표기 없음 + '특강명·장소·참석' 메타 구성 → 성장 섹션 미적용",
                "confirmed_by_operator": False}

    return {"type": "unknown",
            "evidence": "판정 근거 부족 — 담당자 지정 필요",
            "confirmed_by_operator": False}


# --------------------------------------------------------------------------
def _runs_of(row, idx: int) -> List[dict]:
    if idx >= len(row):
        return []
    cell = row[idx]
    return cell.runs or [{"text": cell.text, "emphasis": None}]


def _note_text(row, schema: DetectedSchema) -> str:
    return " ".join(row[c.index].text for c in schema.by_kind(NOTE)
                    if c.index < len(row))


ALIAS = re.compile(r"^(?P<name>.+?)\s*[（(]\s*(?P<alias>[^)）]{1,24})\s*[)）]\s*$")


def split_name(raw: str):
    """'서준혁 (Aiden)' → ('서준혁', 'Aiden').

    영어 이름을 괄호로 병기하는 평가지가 많다. 그대로 두면 같은 사람이
    회차마다 다른 이름이 되고(괄호 표기가 빠지면 매칭 실패), 리포트 제목도
    '서준혁 (Aiden) 님' 이 된다.
    """
    raw = (raw or "").strip()
    m = ALIAS.match(raw)
    if not m:
        return raw, None
    name = m.group("name").strip()
    alias = m.group("alias").strip()
    return (name, alias) if name else (raw, None)


def _guess_language(text: str) -> str:
    hangul = sum(1 for ch in text if "가" <= ch <= "힣")
    return "ko" if hangul >= max(3, len(text) * 0.1) else "en"
