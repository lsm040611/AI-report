"""R-11 · R-14 · R-16 · R-17 · R-19 : 리포트 생성 단계 규칙.

R-14는 순수 코드. 나머지는 '준비'까지만 코드가 하고 문장 생성은 handoff.
R-16(충실성)은 다른 규칙을 검사하는 메타 규칙이라, 생성물이 들어올 때마다
근거 연결을 강제한다.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .base import (AI, BLOCK_QUOTE, CODE, HUMAN, NOTICE, RuleContext, add_flag,
                   mark_applied, quote_allowed, request_handoff, rule)

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
            t = re.sub(r"\s+", " ", t).strip()
            if t:
                stripped.append(t)

        if not stripped:
            continue

        nar["clue_stripped"] = stripped     # 중간 산출물(발송 대상 아님)
        nar["reconstructed"] = None         # 재작성 결과 자리
        mark_applied(card, "R-11", "단서 4종 소거(사건·소속·자기지칭·어투)")

        request_handoff(ctx, card, "R-11", "rewrite_neutral_third_person", {
            "label": nar.get("original_label"),
            "role": nar.get("role"),
            "items": stripped,
            "constraint": ("여러 명이 공통으로 말한 주제만 남길 것. "
                           "단일 건조체 3인칭. 비난형은 실행 중심 코칭 문장으로 변환"),
            "min_common": 2,
        })


def r11_gate_direct_quote(card: dict) -> bool:
    """block_direct_quote 플래그가 있으면 원문 인용 경로를 아예 막는다."""
    return quote_allowed(card)


# --------------------------------------------------------------------------
# R-14 : 회차 간 성장  (담당 = code)
# --------------------------------------------------------------------------
def _norm_key(v) -> str:
    """역량명 대조용 키. 띄어쓰기·가운뎃점·괄호·대소문자 차이를 지운다."""
    if v is None:
        return ""
    return re.sub(r"[\s·・/_\-()\[\]]", "", str(v)).lower()


def _keys(item: dict) -> set:
    """한 역량을 가리킬 수 있는 이름 전부.

    표준 역량명(R-18)은 담당자 승인 여부에 따라 **업로드 시점마다 달라진다.**
    1차수를 올릴 때는 아직 매핑이 없어 `Persuasion` 이었다가, 2차수를 올릴 때는
    승인된 `설득력` 이 붙는 식이다. 한쪽 이름만으로 대조하면 같은 역량이
    "이번 회차에 새로 추가된 역량"으로 잘못 표시된다. 그래서 이름을 모아 두고
    **하나라도 겹치면 같은 역량**으로 본다.
    """
    return {k for k in (_norm_key(item.get("canonical_area")),
                        _norm_key(item.get("area_name")),
                        _norm_key(item.get("question_id"))) if k}


@rule("R-14", CODE,
      "같은 사람의 회차 간 연결",
      "공존 역량별 Δ 중심. 신규 역량 분리, 구성 다르면 평균 비교 미표시, 첫 회차 표시")
def r14_growth(current: dict, previous: Optional[dict]) -> dict:
    """성장 섹션 데이터. 리포트 렌더러가 그대로 쓸 수 있는 형태로 낸다."""
    if previous is None:
        return {"status": "first_session",
                "message": "첫 회차 — 다음 회차부터 성장 비교 시작"}

    def index(card):
        out = []
        for s in card.get("scores", []):
            if s.get("score") is None:
                continue
            ks = _keys(s)
            if not ks:
                continue
            label = s.get("area_name") or s.get("canonical_area") or s.get("question_id")
            out.append({"keys": ks, "score": s["score"],
                        "scale": s.get("scale", {}), "label": label})
        return out

    cur, prev = index(current), index(previous)

    deltas, new_areas = [], []
    matched_prev = set()

    for c in cur:
        p = next((i for i, x in enumerate(prev)
                  if i not in matched_prev and (x["keys"] & c["keys"])), None)
        if p is None:
            new_areas.append({"area": sorted(c["keys"])[0], "label": c["label"],
                              "current": c["score"]})
            continue
        matched_prev.add(p)
        pv = prev[p]
        if c["scale"] != pv["scale"]:      # 척도가 다르면 비율로 환산해 비교
            delta = round(_ratio(c["score"], c["scale"])
                          - _ratio(pv["score"], pv["scale"]), 4)
            unit = "ratio"
        else:
            delta = round(c["score"] - pv["score"], 2)
            unit = "score"
        deltas.append({"area": sorted(c["keys"])[0], "label": c["label"],
                       "delta": delta, "unit": unit,
                       "previous": pv["score"], "current": c["score"]})

    dropped = [prev[i]["label"] for i in range(len(prev)) if i not in matched_prev]

    return {
        "status": "compared",
        "deltas": deltas,
        "new_areas": new_areas,
        "dropped_areas": dropped,
        # 영역 구성이 다르면 전체 평균 비교는 표시하지 않는다(성장 왜곡 방지)
        "average_comparable": not new_areas and not dropped,
        "prev_average": (previous.get("score_summary") or {}).get("average"),
        "curr_average": (current.get("score_summary") or {}).get("average"),
        "applied_rules": ["R-14 회차 간 Δ 산출"],
    }


def _ratio(score: float, scale: dict) -> float:
    lo, hi = scale.get("min", 1), scale.get("max", 5)
    return (score - lo) / (hi - lo) if hi > lo else 0.0


def r14_repeat_signal(current: dict, previous: Optional[dict]) -> List[dict]:
    """이전 회차 gap에서 지적된 표현이 이번 회차 strength에 등장하면 '적용됨'.

    생성이 아니라 문자열 대조라 코드로 완결된다.
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

    prev_action = " ".join(
        r.get("text", "") for n in previous.get("narratives", [])
        if n.get("role") == "next_action" for r in n.get("runs", [])
    ).strip()

    hits = [{"expression": e, "evidence": "이전 회차 교정 표현이 이번 강점에 등장"}
            for e in prev_fix if e.lower() in cur_text]
    if hits and prev_action:
        hits[0]["prev_action"] = prev_action
    return hits


# --------------------------------------------------------------------------
# R-16 : AI 생성물 충실성 (메타 규칙)
# --------------------------------------------------------------------------
@rule("R-16", f"{AI}+{HUMAN}",
      "AI 생성물이 강사 의도를 왜곡할 위험",
      "모든 생성 문장은 원문 특정 구절에 근거를 둔다. 근거 연결 + 검수 대조")
def r16_verify_generated(generated: dict, source_card: dict,
                         require_evidence: bool = True) -> Tuple[bool, str]:
    """handoff 결과가 되돌아올 때 저장 전에 통과해야 하는 검사.

    근거(evidence) 없이 온 생성물은 저장하지 않는다. 왜곡을 사후에
    표시하는 게 아니라 애초에 들어오지 못하게 막는 방식.
    """
    if not (generated.get("text") or "").strip():
        return False, "빈 생성물"

    evidence = generated.get("evidence") or []
    if not require_evidence:
        return True, ""
    if not evidence:
        return False, "근거 원문 미연결 — R-16 위반"

    corpus = source_corpus(source_card)
    for ev in evidence:
        quote = (ev.get("quote") or "").strip()
        if not quote:
            return False, "빈 근거 구절"
        probe = _norm(quote)[:20]
        if probe not in _norm(corpus):
            return False, f"근거 구절이 원문에 없음: {quote[:24]}"
    return True, ""


def source_corpus(card: dict) -> str:
    """R-16 이 근거를 대조할 원문 뭉치.

    clue_stripped 를 포함한다 — 진단서베이 재작성은 소거된 텍스트를 보고
    쓰므로, 소거 전 원문만 대조하면 정상 인용도 위반으로 잡힌다.
    """
    parts: List[str] = []
    for n in card.get("narratives", []):
        parts += [r.get("text", "") for r in (n.get("runs") or [])]
        parts += [i.get("text", "") for i in (n.get("raw_items") or [])]
        parts += list(n.get("clue_stripped") or [])
    return " ".join(parts)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


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
    stype = (card.get("source_type") or {}).get("type")
    spec = CURATION_BY_TYPE.get(stype)
    if not spec:
        return None
    form, instruction = spec

    # 근거가 될 원문 수집. 부족하면 섹션을 만들지 않는다.
    evidence = []
    for n in card.get("narratives", []):
        if n.get("role") not in ("gap", "next_action", "change_request"):
            continue
        if n.get("exposure_policy") == "summarize_only":
            text = " ".join(n.get("clue_stripped") or [])
        else:
            text = "".join(r.get("text", "") for r in (n.get("runs") or []))
        if text.strip():
            evidence.append({"role": n["role"], "quote": text.strip(),
                             "source_cell": n.get("source_cell")})

    emphasis, pairs = collect_emphasis(card)

    if not evidence and not emphasis:
        add_flag(card, "curation_skipped", NOTICE,
                 detail="근거 원문 부족 — 섹션 생략(R-17)")
        return None

    request_handoff(ctx, card, "R-17", f"curate_{form}", {
        "form": form, "instruction": instruction, "evidence": evidence,
        # 강사가 손으로 강조해 둔 곳이 곧 '고쳐야 할 지점'이다.
        # 실천 항목은 여기서부터 만든다.
        "emphasis": emphasis, "pairs": pairs,
        "person": (card.get("person") or {}).get("name"),
    })

    # 강사 교정 표현을 한 문장으로 엮은 '외울 문장'.
    # 진단서베이에는 교정 표현이라는 것이 없으므로 만들지 않는다.
    if stype in ("누적교육", "단발특강") and (pairs or
                                        any(e["kind"] == "fix" for e in emphasis)):
        request_handoff(ctx, card, "R-17", "curate_memorize", {
            "form": form,
            "pairs": pairs, "emphasis": emphasis,
            "strength_text": _strength_text(card),
        })
        mark_applied(card, "R-17", "암기 문장 준비")

    # '함께 살펴보면 좋을 점'을 강사 원문 대신 일정한 말투로 다시 쓴다.
    # 관찰의 근거는 여전히 원문이어야 하지만(R-16), 문장과 덧붙이는 코칭은 생성물이다.
    gap = _gap_source(card)
    if gap["runs"]:
        request_handoff(ctx, card, "R-17", "curate_gap_comment", {
            "form": form,
            "gap_runs": gap["runs"],
            "gap_text": gap["text"],
            "strength_text": gap["strength"],
            "weakest": _weakest_area(card),
            "emphasis": emphasis, "pairs": pairs,
        })
        mark_applied(card, "R-17", "개선점 코멘트 재작성 준비")

    mark_applied(card, "R-17", f"큐레이션 {form} 준비")
    return {"form": form, "evidence": evidence, "emphasis": emphasis}


def _strength_text(card: dict) -> str:
    out = []
    for n in card.get("narratives", []):
        if n.get("role") != "strength" or n.get("exposure_policy") == "summarize_only":
            continue
        out.append("".join(r.get("text", "") for r in (n.get("runs") or [])))
    return " ".join(t for t in out if t.strip()).strip()


def _gap_source(card: dict) -> dict:
    """개선점 코멘트를 다시 쓰는 데 필요한 원문 묶음."""
    runs, texts, strengths = [], [], []
    for n in card.get("narratives", []):
        if n.get("exposure_policy") == "summarize_only":
            continue
        chunk = [{"text": (r.get("text") or ""), "emphasis": r.get("emphasis")}
                 for r in (n.get("runs") or []) if (r.get("text") or "").strip()]
        if not chunk:
            continue
        if n.get("role") in ("gap", "change_request"):
            runs.extend(chunk)
            texts.append("".join(c["text"] for c in chunk))
        elif n.get("role") == "strength":
            strengths.append("".join(c["text"] for c in chunk))
    return {"runs": runs, "text": " ".join(texts).strip(),
            "strength": " ".join(strengths).strip()}


def _weakest_area(card: dict) -> Optional[dict]:
    """점수가 가장 낮은 역량. 코멘트가 어디를 겨냥해야 하는지의 단서.

    **가장 낮은 역량이 하나로 특정될 때만** 돌려준다. 동점이면 임의로 하나를
    고르게 되고, 그러면 개선점 코멘트가 실제로 지적된 것과 상관없는 역량을
    가리키게 된다.
    """
    scored = [s for s in card.get("scores", []) if s.get("score") is not None]
    if len(scored) < 2:
        return None
    low = min(s["score"] for s in scored)
    lowest = [s for s in scored if s["score"] == low]
    if len(lowest) != 1:
        return None
    item = lowest[0]
    scale = item.get("scale") or {}
    return {"name": item.get("area_name") or item.get("question_id"),
            "score": item["score"], "max": scale.get("max")}


EMPHASIS_KIND = {
    "issue_expression": "issue",       # 붉은색 — 고칠 표현
    "corrected_expression": "fix",     # 굵게+밑줄 — 권장 표현
    "key_concept": "key",              # 굵게 — 핵심 개념
}


IMPROVEMENT_ROLES = ("gap", "next_action", "change_request")


def collect_emphasis(card: dict, roles=IMPROVEMENT_ROLES):
    """강조 표시된 구간과 'X → Y' 쌍을 모은다.

    강사는 고쳐야 할 곳에 색을 칠하고, 대신 쓸 표현에 밑줄을 긋는다.
    그 손자국이 개선점의 가장 정확한 위치다 — 서술 전체를 요약하는 것보다
    강조된 구간을 그대로 가져오는 편이 왜곡이 적다.

    기본값은 개선 관련 열만 본다. 강점 열의 강조는 '잘한 것'이라 실천 항목이
    될 수 없다 — 칭찬을 과제로 바꿔 놓으면 읽는 사람이 혼란스럽다.
    """
    spans, pairs = [], []
    for n in card.get("narratives", []):
        role = n.get("role")
        if roles and role not in roles:
            continue
        runs = n.get("runs") or []
        for i, r in enumerate(runs):
            kind = EMPHASIS_KIND.get(r.get("emphasis"))
            text = (r.get("text") or "").strip()
            if not kind or not text:
                continue
            spans.append({"kind": kind, "text": text, "role": role,
                          "source_cell": n.get("source_cell")})
            if kind == "issue":
                fix = next((runs[j] for j in range(i + 1, min(i + 4, len(runs)))
                            if runs[j].get("emphasis") == "corrected_expression"), None)
                if fix and (fix.get("text") or "").strip():
                    pairs.append({"issue": text, "fix": fix["text"].strip(),
                                  "role": role})
    return spans, pairs


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
    scored = [c for c in cards if (c.get("score_summary") or {}).get("average") is not None]
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
                p = other.get("person", {})
                for token in filter(None, [p.get("name"), p.get("alias")]):
                    text = text.replace(token, "○○")
            if text.strip():
                corpus.append(text.strip())

    if not corpus:
        return None

    request_handoff(ctx, top[0], "R-19", "extract_common_traits", {
        "group_size": len(top),
        "anonymized_strengths": corpus,
        "constraint": "3명 이상에서 공통으로 나타난 행동·표현만. 개인 사례 언급 금지",
    })
    return {"group_size": len(top), "corpus_size": len(corpus)}
