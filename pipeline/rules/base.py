"""규칙 인프라. 19개 규칙이 공유하는 어휘와 게이트가 전부 여기에 있다.

계약의 severity 4단계가 실제로 무언가를 막는 지점이 `is_sendable()` 하나뿐이라는 것이
이 파일의 요점이다. 리포트 생성과 발송 매핑표가 같은 함수를 부른다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# 담당 (계약 규칙표의 owner 열)
# --------------------------------------------------------------------------
CODE = "code"
AI = "ai"
HUMAN = "human"

# --------------------------------------------------------------------------
# severity 4단계
# --------------------------------------------------------------------------
NOTICE = "notice"                    # 통과. 리포트에 사유만 표기
REVIEW = "review"                    # 통과하되 담당자 확인 권장
BLOCK_QUOTE = "block_direct_quote"   # 발송 가능, 원문 인용 경로만 차단
HOLD = "hold"                        # 진행 차단

SEVERITY_ORDER = {NOTICE: 0, REVIEW: 1, BLOCK_QUOTE: 2, HOLD: 3}

# 파이썬이 완결할 수 없어 handoff 로 넘기는 규칙들
GENERATION_RULES = {"R-05", "R-11", "R-13", "R-17", "R-18", "R-19"}

# 그중 원문 인용 근거(R-16)를 반드시 대야 하는 것들.
# R-18 은 매핑 제안이라 원문 인용 근거가 존재할 수 없어 제외한다.
EVIDENCE_REQUIRED = {"R-05", "R-11", "R-13", "R-17", "R-19"}

# 다른 작업의 재료를 바꾸는 규칙 — 큐에서 먼저 처리해야 한다.
# R-05 가 강조의 뜻을 바로잡으면 R-17(교정 노트·암기 문장)의 재료가 달라진다.
PRIORITY_RULES = ("R-05",)


# --------------------------------------------------------------------------
# 규칙 등록부
# --------------------------------------------------------------------------
@dataclass
class Rule:
    rule_id: str
    owner: str
    problem: str
    policy: str
    func: Callable

    @property
    def needs_generation(self) -> bool:
        return self.rule_id in GENERATION_RULES


REGISTRY: Dict[str, Rule] = {}


def rule(rule_id: str, owner: str, problem: str, policy: str):
    """규칙 함수를 등록부에 올린다. 함수 자체는 그대로 돌려준다.

    시그니처가 규칙마다 달라(카드를 고치는 것 / 판정만 하는 것 / 값을 만드는 것)
    래핑하지 않고 등록만 한다. `/rules` 엔드포인트가 이 등록부를 그대로 노출한다.
    """
    def deco(fn: Callable) -> Callable:
        REGISTRY[rule_id] = Rule(rule_id, owner, problem, policy, fn)
        fn.rule_id = rule_id            # type: ignore[attr-defined]
        return fn
    return deco


# --------------------------------------------------------------------------
# 규칙 실행 문맥
# --------------------------------------------------------------------------
@dataclass
class RuleContext:
    """규칙이 카드 밖에서 필요로 하는 것 전부.

    handoffs 는 규칙이 '파이썬으로는 여기까지'라고 선언하는 창구다.
    파이프라인이 끝나면 이 목록이 그대로 생성 큐에 적재된다.
    """
    source_file: str = ""
    source_sheet: str = ""
    roster: Dict[str, Any] = field(default_factory=dict)
    competency_map: Dict[str, str] = field(default_factory=dict)
    auto_approve: bool = False
    handoffs: List[dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# 카드 조작 헬퍼
# --------------------------------------------------------------------------
def add_flag(card: dict, code: str, severity: str, **extra) -> dict:
    """검수 플래그를 붙인다. 같은 코드+대상은 한 번만 쌓인다."""
    flags = card.setdefault("flags", [])
    target = extra.get("target")
    for f in flags:
        if f.get("code") == code and f.get("target") == target:
            return f
    flag = {"code": code, "severity": severity, "resolved": False}
    flag.update({k: v for k, v in extra.items() if v is not None})
    flags.append(flag)
    return flag


def mark_applied(card: dict, rule_id: str, note: str = "") -> None:
    """어떤 규칙이 이 카드를 건드렸는지 남긴다 (계약: 모든 값에 출처를 남긴다)."""
    applied = card.setdefault("provenance", {}).setdefault("applied_rules", [])
    entry = f"{rule_id} {note}".strip()
    if entry not in applied:
        applied.append(entry)


def request_handoff(ctx: RuleContext, card: dict, rule_id: str,
                    task: str, payload: dict) -> None:
    """생성이 필요한 작업을 큐에 올린다. 파이썬은 여기서 손을 뗀다.

    어느 카드의 것인지 **이름만으로 가리지 않는다.** 1차수와 2차수를 함께
    올리면 같은 사람의 카드가 둘이 되는데, 이름만 붙여 보내면 큐를 붙일 때
    한쪽 카드로 다 몰린다. 그러면 나머지 카드는 문장이 하나도 없는 리포트가
    된다 — 겉보기에 '리포트가 안 만들어진' 것과 같다.
    """
    prov = card.get("provenance") or {}
    row = prov.get("row") or prov.get("rows")
    ctx.handoffs.append({
        "person": (card.get("person") or {}).get("name"),
        "source_file": prov.get("file"),
        "source_sheet": prov.get("sheet"),
        "source_row": str(row) if row is not None else None,
        "rule_id": rule_id,
        "task": task,
        "payload": payload,
    })


# --------------------------------------------------------------------------
# 검수 관문
# --------------------------------------------------------------------------
def max_severity(card: dict) -> Optional[str]:
    """미해결 플래그 중 가장 높은 severity. 목록 화면 정렬에 쓴다."""
    worst, rank = None, -1
    for f in card.get("flags", []):
        if f.get("resolved"):
            continue
        r = SEVERITY_ORDER.get(f.get("severity"), -1)
        if r > rank:
            worst, rank = f.get("severity"), r
    return worst


def is_sendable(card: dict) -> Tuple[bool, str]:
    """이 카드를 **보낼 수** 있는가. 발송 매핑표의 게이트."""
    person = card.get("person") or {}
    if person.get("status") == "excluded":
        return False, "담당자가 제외 처리한 카드입니다"

    for f in card.get("flags", []):
        if f.get("severity") == HOLD and not f.get("resolved"):
            action = f.get("action") or "담당자 확인 필요"
            return False, f"미해결 hold [{f.get('code')}] {action}"

    if not (card.get("source_type") or {}).get("confirmed_by_operator"):
        return False, "source_type 승인 전입니다"
    return True, ""


# **보내는 것**만 막는 hold. 리포트를 만드는 것은 막지 않는다.
#   누구인지 못 정했다 → 잘못된 사람에게 갈 수 있으니 발송을 막는다
#   청강생이다         → 보낼지 말지는 담당자가 정한다 (핸드오프 v2 §4-5)
# 셋 다 리포트 내용에는 문제가 없고, 오히려 담당자가 **보고 나서** 판단해야 한다.
# 리포트를 안 만들면 검수 화면에 볼 것이 없어 판단할 수가 없다.
DISPATCH_ONLY_HOLDS = {
    "person_not_in_roster",
    "ambiguous_person",
    "non_regular_participant",
    # 평가지 사번과 명부 이름이 다르다. 리포트 내용은 멀쩡하니 만들어 두고,
    # 누구에게 보낼지는 담당자가 보고 정한다.
    "empid_name_mismatch",
}


def is_reportable(card: dict) -> Tuple[bool, str]:
    """이 카드로 **리포트를 만들** 수 있는가.

    발송 게이트와 나눈 이유 — 명부에 없는 사람이라고 리포트를 못 만들 이유는
    없다. 못 하는 건 '보내는 것'뿐이다. 둘을 한 함수로 묶어 두었더니, 명부를
    붙인 순간 명부에 없는 사람이 전원 hold 가 되어 리포트가 한 편도 안 나왔다.
    담당자는 검수 화면에서 볼 것조차 없어진다.
    """
    person = card.get("person") or {}
    if person.get("status") == "excluded":
        return False, "담당자가 제외 처리한 카드입니다"

    for f in card.get("flags", []):
        if f.get("severity") != HOLD or f.get("resolved"):
            continue
        if f.get("code") in DISPATCH_ONLY_HOLDS:
            continue                       # 발송에서만 막는다
        action = f.get("action") or "담당자 확인 필요"
        return False, f"미해결 hold [{f.get('code')}] {action}"

    if not (card.get("source_type") or {}).get("confirmed_by_operator"):
        return False, "source_type 승인 전입니다"
    return True, ""


def quote_allowed(card: dict) -> bool:
    """block_direct_quote 가 걸려 있으면 원문 인용 경로를 통과시키지 않는다."""
    return not any(f.get("severity") == BLOCK_QUOTE and not f.get("resolved")
                   for f in card.get("flags", []))


# --------------------------------------------------------------------------
# 자동 모드
# --------------------------------------------------------------------------
AUTO_MEMO = "자동 모드에서 통과시켰습니다. 운영 모드에서는 담당자 승인이 필요합니다."


def auto_resolve(card: dict, operator: str = "자동 모드") -> List[str]:
    """hold 를 자동 승인하고 source_type 을 자동 확정한다.

    계약이 요구하는 잠금을 없앤 것이 아니라, 잠금을 여는 주체를 사람에서
    설정값으로 바꾼 것이다. 누가 열었는지는 플래그에 그대로 기록된다.
    """
    opened = []
    for f in card.get("flags", []):
        if f.get("severity") == HOLD and not f.get("resolved"):
            f.update({"resolved": True, "decision": "approve",
                      "resolved_by": operator, "memo": AUTO_MEMO})
            opened.append(f.get("code"))

    st = card.setdefault("source_type", {})
    if not st.get("confirmed_by_operator"):
        st["confirmed_by_operator"] = True
        st["confirmed_by"] = operator
    return opened
