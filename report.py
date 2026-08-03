"""R-11 · R-14 · R-16 · R-17 · R-19 : 리포트 생성 단계 규칙.

R-14는 순수 코드. 나머지는 '준비'까지만 코드가 하고 문장 생성은 handoff.
R-16(충실성)은 다른 규칙을 검사하는 메타 규칙이라, 생성물이 들어올 때마다
근거 연결을 강제한다.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from .base import (AI, BLOCK_QUOTE, CODE, HUMAN, NOTICE, RuleContext, add_flag,
                   mark_applied, request_handoff, rule)

# --------------------------------------------------------------------------
# R-11 : 익명성 — 4종 단서 소거
# --------------------------------------------------------------------------
AFFILIATION = re.compile(r"(저희|우리)\s*(팀|부서|파트|조)(에서는|에서|은|는|이|가)?")
SELF_REF = re.compile(r"(제가|저의|저는|저도|내가|나의|저에게|제)\s*")
TONE_NOISE = re.compile(r"[ㅠㅜㅋㅎ]+|\.{2,}|[!?]{2,}|[\U0001F300-\U0001FAFF]")
EVENT_TIME = re.compile(
    r"(작년|재작년|올해|지난\s*\S+|\d{1,2}월|\d{4}년)\s*\S*?\s*(때|에|에서|당시)?\b\s*")


@rule("R-11", f"{CODE}+{AI}+{HUMAN}",
      "주관식에 작성자를 특정할 단서가 담김",
      "①사건·시점 ②소속 지칭 ③자기지칭 ④어투·문체 4종 단서 소거 후 건조체 3인칭 재작성")
def r11_anonymize(card: dict, ctx: RuleContext) -> None:
    """코드가 지울 수 있는 단서를 먼저 지우고, 재작성만 밖으로 넘긴다.

    계약이 요구하는 '단일 건조체 전면 재작성'은 문장 생성이므로
    파이썬 코드로는 완결되지 않는다. 다만 소거는 규칙으로 가능하고,
    소거된 결과를 근거로 넘기면 생성 측이 볼 원문에도 단서가 남지 않는다.
    """
    for nar in card.get("narratives", []):
        items = nar.get("raw_items")
        if not items or nar.get("exposure_policy") != "summarize_only":
            continue

        stripped = []
        for it in items:
            t = it.get("text", "")
            t = EVENT_TIME.sub("", t)
            t = AFFILIATION.sub("일부 응답자는 ", t)
            t = SELF_REF.sub("", t)
            t = TONE_NOISE.sub("", t)
            stripped.append(re.sub(r"\s+", " ", t).strip())

        nar["clue_stripped"] = stripped     # 중간 산출물(발송 대상 아님)
        nar["reconstructed"] = None         # 재작성 결과 자리
        mark_applied(card, "R-11", "단서 3종 소거(사건·소속·자기지칭·어투)")

        request_handoff(ctx, card, "R-11", "rewrite_neutral_third_person", {
            "items": stripped,
            "constraint": ("여러 명이 공통으로 말한 주제만 남길 것. "
                           "단일 건조체 3인칭. 비난형은 실행 중심 코칭 문장으로 변환"),
            "min_common": 2,
        })


def r11_gate_direct_quote(card: dict) -> bool:
    """block_direct_quote 플래그가 있으면 원문 인용 경로를 아예 막는다."""
    return not any(f["severity"] == BLOCK_QUOTE for f in card.get("flags", []))


# --------------------------------------------------------------------------
# R-14 : 회차 간 성장  (담당 = code)
# --------------------------------------------------------------------------
@rule("R-14", CODE,
      "같은 사람의 회차 간 연결",
      "공존 역량별 Δ 중심. 신규 역량 분리, 구성 다르면 평균 비교 미표시, 첫 회차 표시")
def r14_growth(current: dict, previous: Optional[dict]) -> dict:
    """성장 섹션 데이터. 리포트 렌더러가 그대로 쓸 수 있는 형태로 낸다."""
    if previous is None:
        return {"status": "first_session",
                "message": "첫 회차 — 다음 회차부터 성장 비교 시작"}

    def index(card):
        out = {}
        for s in card.get("scores", []):
            key = s.get("canonical_area") or s.get("area_name")
            if key and s.get("score") is not None:
                out[key] = (s["score"], s.get("scale", {}))
        return out

    cur, prev = index(current), index(previous)
    shared = sorted(set(cur) & set(prev))

    deltas = []
    for area in shared:
        c_score, c_scale = cur[area]
        p_score, p_scale = prev[area]
        if c_scale != p_scale:      # 척도가 다르면 비율로 환산해 비교
            c_v = _ratio(c_score, c_scale)
            p_v = _ratio(p_score, p_scale)
            deltas.append({"area": area, "delta": round(c_v - p_v, 4),
                           "unit": "ratio", "previous": p_score, "current": c_score})
        else:
            deltas.append({"area": area, "delta": round(c_score - p_score, 2),
                           "unit": "score", "previous": p_score, "current": c_score})

    return {
        "status": "compared",
        "deltas": deltas,
        "new_areas": sorted(set(cur) - set(prev)),      # "이번 회차 신규" 배지
        "dropped_areas": sorted(set(prev) - set(cur)),
        # 영역 구성이 다르면 전체 평균 비교는 표시하지 않는다(성장 왜곡 방지)
        "average_comparable": set(cur) == set(prev),
        "applied_rules": ["R-14 회차 간 Δ 산출"],
    }


def _ratio(score: float, scale: dict) -> float:
    lo, hi = scale.get("min", 1), scale.get("max", 5)
    return (score - lo) / (hi - lo) if hi > lo else 0.0


def r14_repeat_signal(current: dict, previous: Optional[dict]) -> List[dict]:
    """이전 회차 gap에서 지적된 표현이 이번 회차 strength에 등장하면 '적용됨'.

    생성이 아니라 문자열 대조라 코드로 완결된다.
    (예: 1차수 gap의 'reduce operating costs'가 2차수 strength에 등장)
    """
    if not previous:
        return []

    def emphasized(card, role):
        out = []
        for n in card.get("narratives", []):
            if n.get("role") != role:
                continue
            for r in n.get("runs", []):
                if r.get("emphasis") in ("corrected_expression", "key_concept"):
                    out.append(r["text"].strip().strip('"'))
        return [t for t in out if len(t) >= 4]

    prev_fix = emphasized(previous, "gap")
    cur_text = " ".join(
        r.get("text", "") for n in current.get("narratives", [])
        if n.get("role") == "strength" for r in n.get("runs", [])
    ).lower()

    return [{"expression": e, "evidence": "이전 회차 교정 표현이 이번 강점에 등장"}
            for e in prev_fix if e.lower() in cur_text]


# --------------------------------------------------------------------------
# R-16 : AI 생성물 충실성 (메타 규칙)
# --------------------------------------------------------------------------
@rule("R-16", f"{AI}+{HUMAN}",
      "AI 생성물이 강사 의도를 왜곡할 위험",
      "모든 생성 문장은 원문 특정 구절에 근거를 둔다. 근거 연결 + 검수 대조")
def r16_verify_generated(generated: dict, source_card: dict) -> tuple[bool, str]:
    """handoff 결과가 되돌아올 때 저장 전에 통과해야 하는 검사.

    근거(evidence) 없이 온 생성물은 저장하지 않는다. 왜곡을 사후에
    표시하는 게 아니라 애초에 들어오지 못하게 막는 방식.
    """
    if not generated.get("text"):
        return False, "빈 생성물"
    evidence = generated.get("evidence") or []
    if not evidence:
        return False, "근거 원문 미연결 — R-16 위반"

    corpus = " ".join(
        r.get("text", "")
        for n in source_card.get("narratives", [])
        for r in (n.get("runs") or [])
    ) + " ".join(
        i.get("text", "")
        for n in source_card.get("narratives", [])
        for i in (n.get("raw_items") or [])
    )
    for ev in evidence:
        if ev.get("quote") and ev["quote"][:20] not in corpus:
            return False, f"근거 구절이 원문에 없음: {ev['quote'][:20]}"
    return True, ""


# --------------------------------------------------------------------------
# R-17 : 큐레이션 — source_type이 형태를 결정
# --------------------------------------------------------------------------
CURATION_BY_TYPE = {
    "누적교육": ("연계형", "다음 세션까지의 실천 1가지"),
    "단발특강": ("정리형", "강사 Homework·지적을 한 문단 실천 제안으로 요약"),
    "진단서베이": ("제안형", "여러 응답에 공통된 주제에서만 도출 (R-11·R-16 이중 제약)"),
}


@rule("R-17", AI,
      "AI 큐레이션을 어떤 리포트에 넣을 것인가",
      "포함 여부와 형태는 source_type이 결정. 근거 부족 시 섹션 자체를 생략")
def r17_prepare_curation(card: dict, ctx: RuleContext) -> Optional[dict]:
    stype = card.get("source_type", {}).get("type")
    spec = CURATION_BY_TYPE.get(stype)
    if not spec:
        return None
    form, instruction = spec

    # 근거가 될 원문 수집. 부족하면 섹션을 만들지 않는다.
    evidence = []
    for n in card.get("narratives", []):
        if n.get("role") in ("gap", "next_action"):
            text = "".join(r.get("text", "") for r in (n.get("runs") or []))
            if text.strip():
                evidence.append({"role": n["role"], "quote": text.strip(),
                                 "source_cell": n.get("source_cell")})
    if not evidence:
        add_flag(card, "curation_skipped", NOTICE,
                 detail="근거 원문 부족 — 섹션 생략(R-17)")
        return None

    request_handoff(ctx, card, "R-17", f"curate_{form}", {
        "form": form, "instruction": instruction, "evidence": evidence,
    })
    mark_applied(card, "R-17", f"큐레이션 {form} 준비")
    return {"form": form, "evidence": evidence}


# --------------------------------------------------------------------------
# R-19 : Best Practice
# --------------------------------------------------------------------------
MIN_GROUP = 3


@rule("R-19", f"{AI}+{HUMAN}",
      "우수 수행의 공통점을 팀 전체 발전에 활용",
      "상위 수행자의 익명화된 공통 특징. 그룹 n>=3, 식별 단서 제거, R-16 준수")
def r19_extract_best_practice(cards: List[dict], ctx: RuleContext,
                              top_ratio: float = 0.3) -> Optional[dict]:
    """상위 그룹 추출까지는 통계. 특징 문장화만 handoff.

    n<3이면 개인이 특정되므로 섹션 자체를 만들지 않는다.
    """
    scored = [c for c in cards if c.get("score_summary", {}).get("average") is not None]
    if len(scored) < MIN_GROUP:
        return None

    scored.sort(key=lambda c: c["score_summary"]["average"], reverse=True)
    n = max(MIN_GROUP, int(len(scored) * top_ratio))
    top = scored[:n]
    if len(top) < MIN_GROUP:
        return None

    # 식별 단서(이름·발제 사례)를 뺀 강점 원문만 모은다
    corpus = []
    for c in top:
        for nar in c.get("narratives", []):
            if nar.get("role") != "strength":
                continue
            text = "".join(r.get("text", "") for r in (nar.get("runs") or []))
            for other in cards:
                nm = other.get("person", {}).get("name")
                al = other.get("person", {}).get("alias")
                for token in filter(None, [nm, al]):
                    text = text.replace(token, "○○")
            corpus.append(text.strip())

    request_handoff(ctx, top[0], "R-19", "extract_common_traits", {
        "group_size": len(top),
        "anonymized_strengths": corpus,
        "constraint": "3명 이상에서 공통으로 나타난 행동·표현만. 개인 사례 언급 금지",
    })
    return {"group_size": len(top), "corpus_size": len(corpus)}
