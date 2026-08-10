"""교육 현장에서 줄임말로 쓰이는 영어 낱말의 뜻풀이.

강사는 자기들끼리 통하는 말로 적는다 — "one-pager로 수치 정리", "softener +
대안 제시". 강사에게는 한 단어면 되는 말이지만, 리포트를 받는 사람은 그
단어를 몰라서 **무엇을 하라는 것인지 알 수 없다.** 교정 노트는 다음 준비
전에 다시 보라고 만든 장인데, 뜻을 모르면 볼 이유가 없어진다.

**여기 있는 것만 풀이한다.** 왜 목록으로 두는가 —

교정 표현 대부분은 협상 영어 문장 그 자체다("We may need to adjust our
pricing"). 그것은 **말하라고 가르친 문장**이라 번역하면 수업이 없어진다.
영어라고 다 풀어 버리면 학습 대상을 지우게 되므로, 말할 문장이 아니라
**분류·도구를 가리키는 용어**만 골라서 목록에 담는다.

새 낱말이 나오면 여기에 한 줄 넣으면 된다. 코드는 안 고쳐도 된다.
"""
from __future__ import annotations

import re
from typing import Dict, List

# 낱말(소문자) → 한국어 풀이. 짧게 — 괄호 안에 들어갈 길이여야 한다.
TERMS: Dict[str, str] = {
    # 문서·자료
    "one-pager": "핵심만 한 장으로 정리한 문서",
    "onepager": "핵심만 한 장으로 정리한 문서",
    "one pager": "핵심만 한 장으로 정리한 문서",
    "leave-behind": "상담 뒤 상대에게 남기고 오는 자료",
    "agenda": "회의에서 다룰 항목을 미리 적은 목록",
    "executive summary": "결론만 앞에 모아 둔 요약",
    "fact sheet": "숫자와 사실만 정리한 한 장짜리 자료",
    "checklist": "빠뜨리지 않게 항목을 적어 둔 점검표",

    # 말하기 기법
    "softener": "거절·반대를 부드럽게 만드는 완충 표현",
    "acknowledging phrase": "상대 말을 먼저 받아 주는 표현",
    "hedging": "단정을 피해 여지를 남기는 말투",
    "filler": "말이 막힐 때 채우는 군말 (um, you know 같은)",
    "small talk": "본론 전에 나누는 가벼운 대화",
    "paraphrasing": "상대 말을 내 말로 바꿔 되짚어 주는 것",
    "mirroring": "상대의 말·속도를 따라가며 맞추는 것",
    "signposting": "지금 무슨 말을 할지 미리 알려 주는 것",
    "closing": "대화를 결론으로 매듭짓는 단계",
    "opening": "본론을 여는 첫 마디",
    "elevator pitch": "짧은 시간에 핵심만 말하는 설명",

    # 협상 개념
    "batna": "협상이 깨졌을 때 택할 최선의 대안",
    "zopa": "양쪽이 합의할 수 있는 조건의 범위",
    "anchoring": "먼저 제시한 숫자로 기준을 잡는 것",
    "trade-off": "하나를 내주고 다른 하나를 얻는 맞바꿈",
    "concession": "협상에서 내주는 양보",
    "walk-away": "더는 받아들일 수 없는 한계선",
    "win-win": "양쪽 모두 얻는 것이 있는 결론",
    "deal breaker": "이것이 안 되면 합의가 깨지는 조건",

    # 발표·회의
    "storyline": "이야기가 이어지도록 짠 발표 흐름",
    "takeaway": "듣는 사람이 가져갈 핵심 한 가지",
    "q&a": "질의응답",
    "follow-up": "그 뒤에 이어서 하는 연락이나 조치",
    "action item": "회의에서 정한 실행 과제",
    "wrap-up": "마무리 정리",
    "buy-in": "상대가 납득하고 따라오게 만드는 것",
    "alignment": "생각과 방향을 서로 맞추는 것",
    "framing": "무엇을 앞세워 말할지 정하는 틀 잡기",
    "debrief": "끝난 뒤 함께 되짚어 보는 자리",
}

# 긴 것부터 찾는다 — 'one pager' 를 'one' 으로 자르면 안 된다.
_ORDER = sorted(TERMS, key=len, reverse=True)
_HANGUL = re.compile(r"[가-힣]")


def has_korean(text: str) -> bool:
    return bool(_HANGUL.search(text or ""))


def find(*texts: str) -> List[dict]:
    """주어진 글에서 풀이가 필요한 낱말을 찾는다.

    **한국어가 섞인 글에서만 찾는다.** 통째로 영어인 것은 말하라고 가르친
    문장이므로 건드리지 않는다 — 거기에 풀이를 붙이면 배울 문장 옆에
    번역이 붙어 버려서, 외우라는 것인지 참고하라는 것인지 흐려진다.
    """
    out: List[dict] = []
    seen = set()
    for text in texts:
        if not text or not has_korean(text):
            continue
        low = text.lower()
        for term in _ORDER:
            if term in seen or term not in low:
                continue
            # 낱말 경계를 본다 — 'closing' 이 'disclosing' 안에서 잡히면 안 된다
            if not re.search(r"(?<![A-Za-z0-9])" + re.escape(term)
                             + r"(?![A-Za-z0-9])", low):
                continue
            seen.add(term)
            out.append({"term": term, "meaning": TERMS[term]})
    return out
