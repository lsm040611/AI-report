"""말투 정규화.

평가지에 실제로 적히는 문장은 말투가 제각각이다.

    강사 개조식   "한 장 한 메시지 원칙 지킴", "두괄식 필수"
    강사 명령형   "예상 질문 5개를 작성할 것"
    동료 요청형   "말을 끊지 말아주세요", "~해 주시면 좋겠습니다"
    평서 반말체   "톤이 급해졌다", "연습이 필요하다"

이걸 그대로 리포트에 실으면 한 문서 안에서 문장마다 어조가 튄다. 여기서
전부 `~합니다` 존댓말로 맞춘다. **원문은 어디서도 지우지 않는다** — 표현
계층이 정규화본을 본문으로 쓰고 원문은 그 아래에 남긴다.

한국어 어미는 표로 다 적을 수 없어서, 표로 잡히지 않는 것은 받침으로 판단한다.
"""
from __future__ import annotations

import re
from typing import List

__all__ = ["polish", "to_formal", "normalize", "sentences", "is_formal"]

# 사전에 적어 두는 편이 확실한 어미들. 위에서부터 먼저 맞는 것을 쓴다.
ENDINGS = [
    # 강사 명령형
    (r"해\s*볼\s*것$", "해 봅니다"), (r"어\s*볼\s*것$", "어 봅니다"),
    (r"볼\s*것$", "봅니다"), (r"할\s*것$", "합니다"), (r"쓸\s*것$", "씁니다"),
    (r"낼\s*것$", "냅니다"), (r"들\s*것$", "듭니다"), (r"칠\s*것$", "칩니다"),
    (r"줄\s*것$", "줍니다"), (r"둘\s*것$", "둡니다"), (r"익힐\s*것$", "익힙니다"),
    (r"말\s*것$", "않습니다"),
    # 명사형 어미
    (r"하기$", "합니다"), (r"기$", "습니다"),
    # 개조식 명사 종결 중 '~음' — 받침 있는 어간에 붙는다 (있음, 없음, 같음)
    (r"([가-힣])음$", r"\1습니다"),
    # 축약된 서술격 조사 — '필수다' 는 '필숩니다' 가 아니라 '필수입니다'
    (r"아니다$", "아닙니다"), (r"이다$", "입니다"), (r"하다$", "합니다"),
    # 요청형 (진단서베이 주관식)
    (r"지\s*말아\s*주세요$", "지 않습니다"), (r"지\s*마세요$", "지 않습니다"),
    (r"해\s*주시면\s*좋겠습니다$", "합니다"), (r"주시면\s*좋겠습니다$", "합니다"),
    (r"해\s*주세요$", "합니다"), (r"주세요$", "줍니다"),
    (r"으면\s*좋겠습니다$", "으면 합니다"), (r"기를\s*바랍니다$", "합니다"),
    (r"바람$", "바랍니다"),
]

# 강사가 과제를 명사로만 적어 두는 경우
ACTION_NOUNS = ("연습", "정리", "준비", "작성", "점검", "확인", "메모",
                "요약", "공유", "기록", "복습", "훈련", "리허설", "검토")

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|(?<=다)\s+(?=[가-힣])")


def sentences(text: str) -> List[str]:
    """문장 단위로 자른다. 개조식은 마침표가 없는 경우가 많아 어미도 본다."""
    parts = [p.strip() for p in _SENT_SPLIT.split(text or "") if p and p.strip()]
    return parts or ([text.strip()] if (text or "").strip() else [])


def is_formal(s: str) -> bool:
    return s.rstrip().rstrip(".").endswith(("니다", "세요"))


def polish(sentence: str) -> str:
    """문장 하나를 존댓말로 맞추고 문장부호를 정리한다."""
    s = (sentence or "").strip().rstrip(" .")
    if not s:
        return ""

    # 이미 존댓말이어도 표를 먼저 통과시킨다 — '주시면 좋겠습니다' 같은 요청형은
    # 어미만 보면 정중하지만, 리포트에서는 실행 문장으로 바꿔야 한다.
    for pattern, repl in ENDINGS:
        new = re.sub(pattern, repl, s)
        if new != s:
            return new + "."

    haeyo = _from_haeyo(s)                   # 해요체 (밀려요 · 아쉬워요 · 있어요)
    if haeyo:
        return haeyo + "."

    nominal = _from_nominal(s)               # 개조식 '~ㅁ' (지킴 · 함 · 됨)
    if nominal:
        return nominal + "."

    for noun in ACTION_NOUNS:
        if s.endswith(noun):
            return f"{s}{josa(s, '을', '를')} 합니다."

    formal = to_formal(s)
    if formal != s:
        return formal + "."
    if is_formal(s):
        return s + "."

    # 서술어 없이 명사로 끝나는 개조식 ("두괄식 필수", "Q&A 준비 부족")
    if "가" <= s[-1] <= "힣":
        return s + "입니다."
    return s + "."


# 표로 적어 두는 편이 확실한 해요체. 나머지는 아래에서 받침으로 계산한다.
_HAEYO_FIXED = [("이에요", "입니다"), ("예요", "입니다"), ("에요", "입니다"),
                ("해요", "합니다"), ("돼요", "됩니다"), ("봐요", "봅니다"),
                ("이죠", "입니다")]


def _from_haeyo(s: str) -> str:
    """해요체를 '~합니다'로. 동료 응답은 대부분 이 말투로 적힌다.

    받침으로 계산한다 — 있어요→있습니다(받침 있음), 가요→갑니다(받침 없음),
    밀려요→밀립니다('리+어요'가 줄어든 꼴), 어려워요→어렵습니다(ㅂ 불규칙).
    """
    if not s.endswith("요") and not s.endswith("죠"):
        return ""
    for tail, repl in _HAEYO_FIXED:
        if s.endswith(tail):
            return s[: -len(tail)] + repl
    if s.endswith("네요"):
        return _seubnida(s[:-2])
    if s.endswith("워요"):                    # ㅂ 불규칙 — 아쉬워요 → 아쉽습니다
        return _with_jong(s[:-2], 17) + "습니다"
    if s.endswith("려요"):                    # 밀려요 → 밀립니다
        return s[:-2] + "립니다"
    if s.endswith(("아요", "어요", "지요")):
        return _seubnida(s[:-2])
    if s.endswith("죠"):                      # 부족하죠 → 부족합니다
        return _seubnida(s[:-1])
    # 남은 해요체 — 줄어든 어간을 그대로 어간으로 본다 (가요 → 갑니다)
    return _seubnida(s[:-1]) if len(s) >= 2 else ""


def _seubnida(stem: str) -> str:
    """어간에 '습니다'를 붙인다. 받침이 없으면 ㅂ을 넣는다 (가 → 갑니다)."""
    if not stem:
        return ""
    last = stem[-1]
    if not ("가" <= last <= "힣"):
        return stem + "습니다"
    if (ord(last) - 0xAC00) % 28:            # 받침 있음 — 있 → 있습니다
        return stem + "습니다"
    return _with_jong(stem, 17) + "니다"      # 하 → 합니다


def _with_jong(s: str, jong: int) -> str:
    """마지막 글자의 받침을 갈아 끼운다."""
    if not s or not ("가" <= s[-1] <= "힣"):
        return s
    code = ord(s[-1]) - 0xAC00
    return s[:-1] + chr(0xAC00 + code - (code % 28) + jong)


def _from_nominal(s: str) -> str:
    """'지킴 → 지킵니다' 처럼 ㅁ 명사형을 서술형으로 되돌린다.

    받침 ㅁ 을 ㅂ 으로 바꾸고 '니다'를 붙이면 대부분 맞는다 —
    지킴→지킵니다, 함→합니다, 됨→됩니다, 옴→옵니다, 임→입니다.
    """
    last = s[-1] if s else ""
    if not ("가" <= last <= "힣"):
        return ""
    code = ord(last) - 0xAC00
    if code % 28 != 16:                      # 받침이 ㅁ 이 아니면 해당 없음
        return ""
    return s[:-1] + chr(0xAC00 + code - 16 + 17) + "니다"


def to_formal(s: str) -> str:
    """평서 반말체('~다')를 존댓말('~합니다')로.

    받침으로 판단한다 — 받침이 없으면 명사 뒤 서술격 조사로 보고 '입니다',
    ㄴ이면 ㅂ으로 바꾸고(한다→합니다), 그 밖에는 '습니다'를 붙인다.
    """
    if is_formal(s) or not s.endswith("다"):
        return s
    if s.endswith("는다"):                        # 먹는다 → 먹습니다
        return s[:-2] + "습니다"
    stem = s[-2] if len(s) >= 2 else ""
    if not ("가" <= stem <= "힣"):
        return s
    code = ord(stem) - 0xAC00
    jong = code % 28
    if jong == 0:                                # 필수다 → 필수입니다
        return s[:-1] + "입니다"
    if jong == 4:                                # 한다 → 합니다 / 온다 → 옵니다
        return s[:-2] + chr(0xAC00 + code - 4 + 17) + "니다"
    return s[:-1] + "습니다"                      # 급해졌다 → 급해졌습니다


def normalize(text: str, limit: int = 0) -> str:
    """여러 문장으로 된 글 전체의 말투를 맞춘다."""
    out = [polish(s) for s in sentences(text)]
    out = [s for s in out if s]
    if limit:
        out = out[:limit]
    return " ".join(out)


def josa(word: str, with_batchim: str, without_batchim: str) -> str:
    """받침에 따라 조사를 고른다. '설득력을' / '레버리지를' 처럼.

    영어 표현도 읽히는 소리를 기준으로 판단한다 — hedging 은 '헤징'이라 받침이
    있고(을), understand 는 '언더스탠드'라 받침이 없다(를).
    """
    w = (word or "").strip().rstrip('"\'”’)]…. ').strip()
    if not w:
        return without_batchim
    ch = w[-1]
    if "가" <= ch <= "힣":
        return with_batchim if (ord(ch) - 0xAC00) % 28 else without_batchim
    low = w.lower()
    if low.endswith("ng") or (low[-1].isalpha() and low[-1] in "nlmr"):
        return with_batchim
    return without_batchim
