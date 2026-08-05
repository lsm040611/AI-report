"""R-07 · R-12 · R-13 · R-15 · R-18 : 의미 판단이 섞인 규칙.

전부 담당에 ai 또는 human 이 붙지만, 계약이 각 규칙마다
"확신 없으면 담당자에게 넘긴다"는 출구를 명시해 두었다.
따라서 코드는 확신 가능한 구간만 처리하고 나머지는 큐로 보낸다.
생성형 호출은 이 파일 어디에도 없다 — 호출은 generation/worker.py 한 곳뿐이다.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .base import (AI, CODE, HOLD, HUMAN, NOTICE, RuleContext, add_flag,
                   mark_applied, request_handoff, rule)

# --------------------------------------------------------------------------
# R-12 : 헤더 라벨 -> 역할. 확신도 3단계.
# --------------------------------------------------------------------------
ROLE_KEYWORDS: Dict[str, List[str]] = {
    "strength": ["strength", "강점", "highlight", "잘한", "우수", "좋았"],
    "gap": ["gap", "improvement", "보완", "개선", "미흡", "아쉬"],
    "next_action": ["next action", "homework", "과제", "액션", "실천", "todo"],
    "change_request": ["바라는 변화", "change request", "요청", "변화"],
}

# 서술 열의 관례적 순서
POSITIONAL = ["strength", "gap", "next_action", "change_request"]

HIGH, MEDIUM, LOW = "high", "medium", "low"


@rule("R-12", AI,
      "헤더 라벨이 제각각 (Strengths · Highlights · 주관식1)",
      "라벨이 아니라 의미로 역할을 판별한다. 확신도 3단계 — low는 무조건 unknown")
def r12_detect_role(label: str, position: Optional[int] = None,
                    total: Optional[int] = None) -> Tuple[str, str]:
    """(role, confidence) 반환.

    high   : 헤더 텍스트만으로 직접 판단 가능
    medium : 간접 근거(열 순서 등)로 추론 — 판정하되 승인 화면에 근거 강조
    low    : 근거 없음 -> unknown + 담당자 질문
    """
    text = (label or "").lower()
    hits = [role for role, kws in ROLE_KEYWORDS.items() if any(k in text for k in kws)]

    if len(hits) == 1:
        return hits[0], HIGH
    if len(hits) > 1:
        # 라벨 하나가 두 역할을 동시에 가리키면 직접 판단 불가
        return "unknown", LOW

    # 간접 근거: 서술 열의 관례적 순서 (강점 -> 보완 -> 액션 -> 요청)
    if position is not None and total is not None and 2 <= total <= 4:
        if position < len(POSITIONAL):
            return POSITIONAL[position], MEDIUM
    return "unknown", LOW


def r12_apply(card: dict, ctx: RuleContext) -> None:
    narratives = card.get("narratives", [])
    for i, nar in enumerate(narratives):
        if nar.get("role"):
            continue
        role, conf = r12_detect_role(nar.get("original_label", ""), i, len(narratives))
        nar["role"] = role
        nar["role_confidence"] = conf
        if conf == HIGH:
            mark_applied(card, "R-12", "라벨 역할 판별")
        elif conf == MEDIUM:
            add_flag(card, "role_inferred", NOTICE,
                     target=nar.get("original_label"),
                     detail="열 순서 기반 추론 — 승인 화면에서 근거 확인")
        else:
            add_flag(card, "role_unknown", HOLD,
                     target=nar.get("original_label"),
                     action="담당자가 역할을 지정해야 진행 가능")


# --------------------------------------------------------------------------
# R-07 : 비정규 참가자 탐지
# --------------------------------------------------------------------------
AUDIT_HINTS = ["청강", "참관", "옵저버", "observer", "audit", "비정규"]


@rule("R-07", f"{CODE}+{AI}+{HUMAN}",
      "청강생 등 비정규 참가자",
      "비고 열 등에서 탐지하고, 애매하면 담당자에게 묻는다. 감지 시 발송 보류")
def r07_detect_audit(card: dict, ctx: RuleContext, note_text: str = "") -> None:
    """비고 열과 신분 필드만 본다.

    context(과정 메타) 까지 뒤지면 과정명에 '참고'가 들어간 경우처럼
    관계없는 문자열에 걸려 전원이 청강생으로 잡힌다.
    """
    haystack = " ".join(filter(None, [
        note_text,
        str((card.get("person") or {}).get("status") or ""),
    ])).lower()

    if any(h in haystack for h in AUDIT_HINTS):
        card.setdefault("person", {})["status"] = "audit"
        add_flag(card, "non_regular_participant", HOLD,
                 detail=f"근거: {note_text.strip()[:40]}",
                 action="발송 전 담당자 확인 필수")
        mark_applied(card, "R-07", "청강생 감지 -> 담당자 확인 플래그")


# --------------------------------------------------------------------------
# R-15 : 동명이인
# --------------------------------------------------------------------------
@rule("R-15", f"{CODE}+{HUMAN}",
      "동명이인",
      "명부 사번을 내부 키로. 별칭·조·차수로 좁히고 둘 이상이면 담당자에게 묻고 기억한다")
def r15_resolve_person(card: dict, ctx: RuleContext) -> None:
    roster: List[dict] = ctx.roster.get("people", [])
    person = card.setdefault("person", {})
    name, alias = person.get("name"), person.get("alias")
    ctxd = card.get("context", {})

    if person.get("person_id"):
        # 평가지 이름 칸에 사번이 함께 적혀 있던 경우 — 이미 신원 키가 있다
        mark_applied(card, "R-15", "원본 사번 사용")
        return

    if not roster:
        # 제공 데이터가 더미라 명부가 아직 없다(계약 '다음 단계 확인' 항목).
        add_flag(card, "roster_missing", NOTICE,
                 detail="명부 미입력 — person_id 부여 불가")
        return

    # 담당자가 이전에 답한 것을 기억한다
    memo_key = f"{name}|{alias}|{_first(ctxd, ('과정명', '특강명', '진단명'))}"
    remembered = ctx.roster.get("resolved", {}).get(memo_key)
    if remembered:
        person["person_id"] = remembered
        mark_applied(card, "R-15", "이전 담당자 판단 재사용")
        return

    cands = [p for p in roster if p.get("name") == name]
    if len(cands) > 1 and alias:
        cands = [p for p in cands if p.get("alias") == alias] or cands
    if len(cands) > 1:
        for key in ("조", "차수", "부서"):
            if ctxd.get(key):
                narrowed = [p for p in cands if p.get(key) == ctxd[key]]
                if narrowed:
                    cands = narrowed
                    break

    if len(cands) == 1:
        person["person_id"] = cands[0].get("person_id")
        mark_applied(card, "R-15", "명부 매칭")
    elif len(cands) > 1:
        add_flag(card, "ambiguous_person", HOLD,
                 candidates=[c.get("person_id") for c in cands],
                 action="담당자가 본인을 지정해야 진행 가능")
    else:
        add_flag(card, "person_not_in_roster", HOLD, action="명부 등록 필요")


def _first(d: dict, keys) -> str:
    for k in keys:
        for actual, v in d.items():
            if k in str(actual):
                return str(v)
    return ""


# --------------------------------------------------------------------------
# R-18 : 표준 역량 매핑
# --------------------------------------------------------------------------
DEFAULT_COMPETENCY_MAP = {
    "persuasion": "설득력", "설득력": "설득력",
    "accuracy": "정확성", "정확성": "정확성",
    "tone": "어조", "어조": "어조",
    "이해관계 파악": "이해관계파악", "interest mapping": "이해관계파악",
    "논리 구조": "논리구조", "logical structure": "논리구조", "structure": "논리구조",
    "delivery": "전달력", "전달력": "전달력",
    "visuals": "자료구성", "q&a": "질의응답",
}


@rule("R-18", f"{AI}+{HUMAN}",
      "동일 역량의 명칭이 과정마다 다름 (Persuasion ↔ 설득력)",
      "AI가 매핑을 제안하고 담당자가 승인하며, 승인된 매핑은 저장해 재사용한다")
def r18_map_competencies(card: dict, ctx: RuleContext) -> None:
    table = {**DEFAULT_COMPETENCY_MAP, **ctx.competency_map}
    for item in card.get("scores", []):
        name = (item.get("area_name") or "").strip()
        if not name:
            continue
        canon = table.get(name.lower()) or table.get(name)
        if canon:
            item["canonical_area"] = canon
            mark_applied(card, "R-18", "표준 역량 매핑")
        else:
            item["canonical_area"] = None
            add_flag(card, "unmapped_competency", NOTICE, target=name,
                     action="담당자 승인 후 매핑 저장")
            request_handoff(ctx, card, "R-18", "propose_competency_mapping",
                            {"area_name": name,
                             "known": sorted(set(table.values()))})


# --------------------------------------------------------------------------
# R-13 : 영어 코멘트
# --------------------------------------------------------------------------
QUOTED = re.compile(r'"[^"]+"')


@rule("R-13", f"{CODE}+{AI}",
      "영어로 쓰인 강사 코멘트",
      "원문 보존 + 전문 한국어 번역 병기. 인용된 학습 대상 표현은 원어 유지")
def r13_prepare_translation(card: dict, ctx: RuleContext) -> None:
    for nar in card.get("narratives", []):
        if nar.get("language") != "en" or nar.get("translation_ko"):
            continue
        text = "".join(r.get("text", "") for r in nar.get("runs", []))
        if not text.strip():
            continue

        # 번역해서는 안 되는 구간을 먼저 잠근다 (따옴표 인용 + 강조된 표현)
        preserve = set(QUOTED.findall(text))
        preserve |= {r["text"].strip() for r in nar.get("runs", [])
                     if r.get("emphasis") in ("issue_expression", "corrected_expression")}

        nar["translation_ko"] = None            # 아직 비어 있음
        nar["preserve_original"] = sorted(preserve)
        add_flag(card, "translation_pending", NOTICE,
                 target=nar.get("original_label"))
        request_handoff(ctx, card, "R-13", "translate_en_to_ko", {
            "label": nar.get("original_label"),      # 결과를 어느 칸에 되돌릴지
            "source_text": text,
            "preserve_verbatim": sorted(preserve),
            "role": nar.get("role"),
        })
        mark_applied(card, "R-13", "번역 대상 표시 + 원어 유지 구간 잠금")
