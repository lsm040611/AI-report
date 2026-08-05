"""R-08 ~ R-10 : 진단서베이(aggregated_responses) 전용. 담당 = code.

direction 이 "aggregated_responses" 인 카드에만 적용된다.
교육 평가(individual_row)에서는 aggregation 이 null 이다.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from .base import BLOCK_QUOTE, CODE, RuleContext, add_flag, mark_applied, rule

MIN_N = 3                       # 관계별 분리 표시 최소 인원
NON_ANONYMOUS = {"상사"}        # v0.5: 상사는 비익명 관계로 정의


# --------------------------------------------------------------------------
@rule("R-08", CODE,
      "문항 정의가 별도 시트에 있음",
      "문항 번호로 시트를 이어 붙여 역량명과 정의문을 가져온다")
def r08_join_question_defs(card: dict, ctx: RuleContext,
                           definitions: Dict[str, dict] | None = None) -> None:
    definitions = definitions if definitions is not None else ctx.roster.get("question_defs", {})
    if not definitions:
        return
    joined = 0
    for item in card.get("scores", []):
        qid = str(item.get("question_id") or "").strip()
        meta = definitions.get(qid) or definitions.get(qid.upper())
        if not meta:
            continue
        if meta.get("area_name"):
            item["area_name"] = meta["area_name"]
        if meta.get("definition"):
            item["definition"] = meta["definition"]
        joined += 1
    if joined:
        mark_applied(card, "R-08", f"문항정의 시트 조인 ({joined}문항)")


# --------------------------------------------------------------------------
@rule("R-09", CODE,
      "응답 여러 건이 한 사람의 결과가 됨",
      "피평가자 기준으로 묶고 문항별·관계별 평균을 낸다")
def r09_aggregate_responses(target_name: str, responses: List[dict]) -> dict:
    """N행 -> 1인 집계. 카드의 scores / aggregation 블록을 만들어 돌려준다.

    responses: [{"relation": "구성원", "answers": {"Q1": 4, ...}}, ...]
    """
    by_relation_count: Dict[str, int] = defaultdict(int)
    per_q_all: Dict[str, List[float]] = defaultdict(list)
    per_q_rel: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
    order: List[str] = []

    for r in responses:
        rel = r.get("relation") or "미상"
        by_relation_count[rel] += 1
        for qid, val in (r.get("answers") or {}).items():
            if val is None or not _numeric(val):
                continue
            if qid not in per_q_all:
                order.append(qid)
            per_q_all[qid].append(float(val))
            per_q_rel[qid][rel].append(float(val))

    scores = []
    for qid in order:                       # 원본 문항 순서를 유지한다
        allv = per_q_all[qid]
        scores.append({
            "question_id": qid,
            "area_name": qid,               # R-08 이 문항정의로 덮어쓴다
            "score": round(sum(allv) / len(allv), 2),
            "n": len(allv),
            "by_relation": {},              # R-10에서 채운다
            "_rel_raw": {k: list(v) for k, v in per_q_rel[qid].items()},
        })

    return {
        "person": {"name": target_name},
        "direction": "aggregated_responses",
        "scores": scores,
        "aggregation": {
            "n_respondents": len(responses),
            "by_relation": dict(by_relation_count),
        },
        "provenance": {"applied_rules": ["R-09 응답 집계(N행->1인)"]},
    }


def _numeric(v) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    try:
        float(str(v).strip())
        return True
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------
@rule("R-10", CODE,
      "응답자가 적어 작성자가 특정됨",
      "상사는 비익명 관계. 그 외는 n>=3만 분리 표시하고 미달은 전체 평균에만 반영")
def r10_apply_anonymity(card: dict, ctx: RuleContext) -> None:
    if card.get("direction") != "aggregated_responses":
        return

    agg = card.setdefault("aggregation", {})
    rel_counts: Dict[str, int] = agg.get("by_relation", {})

    separate, aggregate_only = [], []
    for rel, n in rel_counts.items():
        if rel in NON_ANONYMOUS or n >= MIN_N:
            separate.append(rel)
        else:
            aggregate_only.append(rel)

    for item in card.get("scores", []):
        raw = item.pop("_rel_raw", {})
        item["by_relation"] = {
            rel: round(sum(v) / len(v), 2)
            for rel, v in raw.items() if rel in separate and v
        }
        if aggregate_only:
            item["aggregate_only"] = {
                "relations": sorted(aggregate_only),
                "note": f"전체 평균에만 반영 (n<{MIN_N})",
            }

    agg["anonymity"] = {
        "min_n_rule": MIN_N,
        "non_anonymous_relations": sorted(NON_ANONYMOUS & set(rel_counts)),
        "by_relation_display": {
            **{r: "separate" for r in separate},
            **{r: "aggregate_only" for r in aggregate_only},
        },
        "separate": sorted(separate),
        "aggregate_only": sorted(aggregate_only),
    }

    if aggregate_only:
        add_flag(card, "anonymity_risk", BLOCK_QUOTE,
                 detail=(f"분리 미표시 관계: {sorted(aggregate_only)} "
                         f"(n<{MIN_N}, 전체 평균에만 반영) — 주관식 직접 인용 금지"))
    mark_applied(card, "R-10", "익명성 판정")
