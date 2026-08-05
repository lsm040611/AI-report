"""생성 실행기. **파이썬 코드에서 LLM 을 호출하는 유일한 파일이다.**

규칙(pipeline/rules/*)은 여전히 생성형을 부르지 않는다. 규칙은 handoff 큐에
"여기부터는 문장 생성"이라고 적어 두기만 하고, 이 파일이 그 큐를 가져가
Claude API 를 호출한 뒤 결과를 R-16 검사로 되돌려 보낸다.

키가 없으면 목(mock) 생성기로 자동 전환된다. 목 모드에서도 전 구간이 돌아가고,
생성된 항목에는 engine="mock" 이 남아 리포트가 이를 표기한다.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from config import EFFORT, MODEL, USE_LLM

from . import prompts

_client = None


def engine_name() -> str:
    return f"claude:{MODEL}" if USE_LLM else "mock"


def _client_or_none():
    global _client
    if not USE_LLM:
        return None
    if _client is None:
        import anthropic                      # 키가 있을 때만 import 비용을 낸다
        _client = anthropic.Anthropic()
    return _client


# --------------------------------------------------------------------------
def generate(rule_id: str, task: str, payload: dict) -> Dict[str, object]:
    """{text, evidence, engine, error?} 를 돌려준다. 예외를 밖으로 던지지 않는다."""
    label, user_prompt, schema = prompts.build(task, payload)

    client = _client_or_none()
    if client is None:
        return {**_mock(rule_id, task, payload), "engine": "mock", "task_label": label}

    try:
        data = _call(client, user_prompt, schema)
    except Exception as exc:                   # noqa: BLE001 — 어떤 실패든 목으로 내려간다
        out = _mock(rule_id, task, payload)
        return {**out, "engine": "mock",
                "task_label": label,
                "error": f"{type(exc).__name__}: {exc}"}

    if data is None:
        out = _mock(rule_id, task, payload)
        return {**out, "engine": "mock", "task_label": label,
                "error": "모델이 응답을 거부했거나 비어 있었습니다"}

    # text/evidence 외의 키(암기 문장의 parts·closing 등)도 그대로 넘긴다
    return {**data,
            "text": data.get("text", ""),
            "evidence": data.get("evidence", []),
            "engine": engine_name(),
            "task_label": label}


def _call(client, user_prompt: str, schema: dict) -> Optional[dict]:
    """Claude 호출. 구조화 출력으로 스키마를 강제한다."""
    kwargs = dict(
        model=MODEL,
        max_tokens=8000,
        system=prompts.SYSTEM,
        messages=[{"role": "user", "content": user_prompt}],
        output_config={
            "format": {"type": "json_schema", "schema": schema},
            "effort": EFFORT,
        },
    )

    try:
        # 안전 분류기가 거절하면 서버가 대체 모델로 자동 재시도한다.
        resp = client.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            **kwargs,
        )
    except Exception:                          # noqa: BLE001
        # 베타 기능을 못 쓰는 환경이면 일반 경로로 한 번 더
        resp = client.messages.create(**kwargs)

    if getattr(resp, "stop_reason", None) == "refusal":
        return None

    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------
# 목 생성기 — 원문을 재배열만 한다. 없는 사실을 만들지 않으므로 R-16을 통과한다.
# --------------------------------------------------------------------------
def _mock(rule_id: str, task: str, payload: dict) -> Dict[str, object]:
    if task == "rewrite_neutral_third_person":
        return _mock_r11(payload)
    if task == "classify_emphasis":
        # 문장의 뜻을 읽는 일이라 목으로 흉내 낼 수 없다. 아무것도 바꾸지 않으면
        # 서식으로 뽑은 판정이 그대로 남는다 — 지금까지의 동작과 같다.
        return {"text": "", "evidence": [],
                "error": "목 모드에서는 강조의 뜻을 판정할 수 없습니다 — "
                         "서식 기반 판정을 그대로 씁니다"}
    if task == "translate_en_to_ko":
        # 번역은 목으로 흉내 낼 수 없다. 영어를 그대로 '번역본'이라고 돌려주면
        # 리포트에 영어가 한국어인 척 실린다. 아무것도 만들지 않고 이유를 남긴다.
        return {"text": "", "evidence": [],
                "error": "목 모드에서는 번역할 수 없습니다 — "
                         "ANTHROPIC_API_KEY 를 설정하면 한국어 번역이 붙습니다"}
    if task == "curate_memorize":
        return _mock_memorize(payload)
    if task == "curate_gap_comment":
        return _mock_gap(payload)
    if task.startswith("curate_"):
        return _mock_r17(payload)
    if task == "extract_common_traits":
        return _mock_r19(payload)
    if task == "propose_competency_mapping":
        name = payload.get("area_name", "")
        return {"text": name,
                "evidence": [{"quote": name, "why": "원본 역량명을 그대로 표준명으로 사용(목 모드)"}]}
    return {"text": "", "evidence": []}


def _mock_r11(payload: dict) -> Dict[str, object]:
    items: List[str] = [t for t in payload.get("items", []) if t.strip()]
    picked = sorted(items, key=len, reverse=True)[:2]
    lines, evidence = [], []
    for t in picked:
        head = _first_clause(t)
        lines.append(f"<b>{head}</b> 같은 의견이 응답에 담겼습니다.")
        evidence.append({"quote": t, "why": "응답 원문"})
    return {"text": "\n".join(lines), "evidence": evidence}


def _mock_r17(payload: dict) -> Dict[str, object]:
    """실천 항목을 만든다. **강사가 강조 표시한 구간이 1순위 재료다.**

    강사는 고쳐야 할 곳에 색을 칠하고 대신 쓸 표현에 밑줄을 긋는다. 그 손자국을
    그대로 옮기는 편이 서술 전체를 요약하는 것보다 정확하고, 사람마다 말투가
    달라지지도 않는다. 강조가 하나도 없을 때만 서술 문장으로 내려간다.
    """
    lines: List[str] = []
    evidence: List[dict] = []
    used = set()

    def add(line: str, quote: str, why: str):
        if len(lines) >= 3 or quote in used:
            return
        used.add(quote)
        lines.append(line)
        evidence.append({"quote": quote, "why": why})

    # ① 교정 쌍 — "이렇게 말고 → 이렇게"
    for p in payload.get("pairs") or []:
        issue, fix = p["issue"], p["fix"]
        add(f"<b>{issue}</b> 대신 <b>{fix}</b>{_josa(fix, '을', '를')} 씁니다.",
            fix, "강사가 밑줄로 표시한 권장 표현")

    spans = payload.get("emphasis") or []
    paired = {p["fix"] for p in (payload.get("pairs") or [])}
    paired |= {p["issue"] for p in (payload.get("pairs") or [])}

    # ② 쌍을 이루지 못한 권장 표현
    for s in spans:
        if s["kind"] == "fix" and s["text"] not in paired:
            add(f"<b>{s['text']}</b> 표현을 의식적으로 씁니다.",
                s["text"], "강사가 밑줄로 표시한 권장 표현")

    # ③ 핵심 개념
    for s in spans:
        if s["kind"] == "key":
            t = s["text"]
            add(f"<b>{t}</b>{_josa(t, '을', '를')} 염두에 두고 준비합니다.",
                t, "강사가 굵게 표시한 핵심 개념")

    # ④ 짝 없는 '고칠 표현'
    for s in spans:
        if s["kind"] == "issue" and s["text"] not in paired:
            add(f"<b>{s['text']}</b> 라고 말하는 습관을 줄입니다.",
                s["text"], "강사가 붉은색으로 표시한 고칠 표현")

    # ⑤ 강조가 없으면 서술 원문에서
    if not lines:
        ev = payload.get("evidence", [])
        actions = [e for e in ev if e.get("role") == "next_action"] or ev
        for e in actions:
            for sent in _pick_sentences(e.get("quote") or ""):
                add(_polish(sent), sent, "강사 코멘트 원문")

    if not lines:
        return {"text": "", "evidence": []}
    return {"text": "\n".join(lines), "evidence": evidence}


_REQUEST_TAIL = ("좋겠습니다", "주세요", "주시면", "필요합니다", "할 것", "볼 것", "것.")


def _in_order(text: str, limit: int = 2) -> List[str]:
    """원문 순서대로 앞에서부터 문장을 가져온다."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text or "")
             if len(p.strip()) >= 8]
    return [p[:120] for p in parts[:limit]]


def _pick_sentences(text: str, limit: int = 2) -> List[str]:
    """요청·과제로 읽히는 문장을 우선해서 최대 2개 고른다."""
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text)
             if len(p.strip()) >= 10]
    if not parts:
        return [text.strip()] if text.strip() else []
    ranked = sorted(parts, key=lambda s: (not s.rstrip(". ").endswith(_REQUEST_TAIL),
                                          len(s)))
    return [s[:110].strip() for s in ranked[:limit]]


def _josa(word: str, with_batchim: str, without_batchim: str) -> str:
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
    if low.endswith("ng") or low[-1] in "nlmr":
        return with_batchim          # 헤징 / 퍼슈에이전 / 툴 — 받침으로 끝남
    return without_batchim


# 강사가 쓰는 명령형 어미를 '~합니다' 로 통일한다. 리포트 안에서 말투가
# 항목마다 달라지면 누가 쓴 문장인지가 드러나 읽는 흐름이 끊긴다.
_ENDINGS = [
    # 강사가 쓰는 명령형
    (r"해\s*볼\s*것$", "해 봅니다"), (r"어\s*볼\s*것$", "어 봅니다"),
    (r"볼\s*것$", "봅니다"), (r"할\s*것$", "합니다"), (r"쓸\s*것$", "씁니다"),
    (r"낼\s*것$", "냅니다"), (r"들\s*것$", "듭니다"), (r"칠\s*것$", "칩니다"),
    (r"줄\s*것$", "줍니다"), (r"둘\s*것$", "둡니다"), (r"익힐\s*것$", "익힙니다"),
    (r"말\s*것$", "않습니다"),
    # 명사형 어미
    (r"하기$", "합니다"), (r"기$", "습니다"),
    # 축약된 서술격 조사 — '필수다' 는 '필숩니다' 가 아니라 '필수입니다'
    (r"아니다$", "아닙니다"), (r"이다$", "입니다"), (r"하다$", "합니다"),
    # 동료·구성원이 쓰는 요청형 (진단서베이)
    (r"지\s*말아\s*주세요$", "지 않습니다"), (r"지\s*마세요$", "지 않습니다"),
    (r"해\s*주시면\s*좋겠습니다$", "합니다"), (r"주시면\s*좋겠습니다$", "합니다"),
    (r"해\s*주세요$", "합니다"), (r"주세요$", "줍니다"),
]


def _polish(sentence: str) -> str:
    """말투를 '~합니다' 로 맞추고 문장부호를 정리한다."""
    s = (sentence or "").strip().rstrip(" .")
    if not s:
        return ""
    for pattern, repl in _ENDINGS:
        new = re.sub(pattern, repl, s)
        if new != s:
            return new + "."
    # 명사로 끝나는 과제("…되묻는 연습")는 서술어를 붙여 준다
    for noun in _ACTION_NOUNS:
        if s.endswith(noun):
            return f"{s}{_josa(s, '을', '를')} 합니다."
    return _to_formal(s) + "."


# 강사가 과제를 명사로만 적어 두는 경우가 많다
_ACTION_NOUNS = ("연습", "정리", "준비", "작성", "점검", "확인", "메모",
                 "요약", "공유", "기록", "복습", "훈련")


def _is_formal(s: str) -> bool:
    return s.rstrip().rstrip(".").endswith(("니다", "세요"))


def _to_formal(s: str) -> str:
    """강사 메모체('~다')를 존댓말('~합니다')로 바꾼다.

    강사는 평가지에 '톤이 급해졌다', '연습이 필요하다' 처럼 적는다. 그대로 실으면
    리포트 안에서 문장마다 말투가 달라진다. 한국어 어미 규칙이라 표로 다 적을 수
    없어 받침으로 판단한다 — 받침이 없으면 ㅂ을 붙이고(하다→합니다), ㄴ이면
    ㅂ으로 바꾸고(한다→합니다), 그 밖에는 '습니다'를 붙인다(있다→있습니다).
    """
    if s.endswith("니다") or not s.endswith("다"):
        return s
    if s.endswith("는다"):                       # 먹는다 → 먹습니다
        return s[:-2] + "습니다"
    stem = s[-2] if len(s) >= 2 else ""
    if not ("가" <= stem <= "힣"):
        return s
    code = ord(stem) - 0xAC00
    jong = code % 28
    if jong == 0:
        # 받침 없는 어간에 붙은 '다'는 대개 명사 뒤의 서술격 조사다
        # ('필수다' → '필수입니다'). 동사형('하다')은 위 표에서 먼저 걸러진다.
        return s[:-1] + "입니다"
    if jong == 4:                                # 한다 → 합니다 / 온다 → 옵니다
        return s[:-2] + chr(0xAC00 + code - 4 + 17) + "니다"
    return s[:-1] + "습니다"                      # 급해졌다 → 급해졌습니다


def _mock_memorize(payload: dict) -> Dict[str, object]:
    """암기 문장 (목 모드).

    문장을 엮는 것은 언어 모델의 일이라 목으로는 흉내 낼 수 없다. 대신 강사가
    권장한 표현을 **순서대로 이어 붙여** 외울 거리를 만든다. 표현 자체는 원문
    그대로라 외워서 쓰는 데는 지장이 없고, 매끄러운 한 문장이 필요하면 키를 붙이면 된다.
    """
    pairs = payload.get("pairs") or []
    spans = payload.get("emphasis") or []

    picked, parts, evidence = [], [], []
    for p in pairs[:2]:
        picked.append(p["fix"])
        parts.append({"quote": p["fix"], "note": f'"{p["issue"]}" 를 대신하는 표현입니다.'})
        evidence.append({"quote": p["fix"], "why": "강사가 밑줄로 표시한 권장 표현"})

    if not picked:
        for s in spans:
            if s["kind"] == "fix":
                picked.append(s["text"])
                parts.append({"quote": s["text"], "note": "강사가 권장한 표현입니다."})
                evidence.append({"quote": s["text"], "why": "권장 표현"})
            if len(picked) >= 2:
                break

    if not picked:
        return {"text": "", "evidence": []}

    sentence = "  …  ".join(picked)
    return {
        "text": sentence,
        "parts": parts,
        "closing": "이미 잘하고 있는 부분은 그대로 두고, 이번 회차에서 짚인 표현만 "
                   "위 문장으로 채웁니다.",
        "evidence": evidence,
    }


def _mock_gap(payload: dict) -> Dict[str, object]:
    """'함께 살펴보면 좋을 점' 문단을 다시 쓴다 (목 모드).

    목 생성기에는 언어 모델이 없으므로 문장을 새로 지어낼 수 없다. 대신
    **강조 구간(짧은 어구)을 고정된 틀에 끼워** 문단을 조립한다. 어구는 원문
    그대로지만 문장 구조와 말투는 여기서 만든 것이라, 강사 코멘트를 그대로
    옮긴 것과는 다르다. API 키를 연결하면 진짜 재작성으로 바뀐다.
    """
    pairs = payload.get("pairs") or []
    spans = payload.get("emphasis") or []
    w = payload.get("weakest") or {}
    sentences: List[str] = []
    evidence: List[dict] = []

    if w.get("name"):
        sentences.append(f"이번 회차에서 함께 볼 지점은 <b>{w['name']}</b>입니다.")

    if pairs:
        p = pairs[0]
        sentences.append(
            f"<b>{p['issue']}</b>처럼 말한 장면이 있었고, 같은 자리에 "
            f"<b>{p['fix']}</b>{_josa(p['fix'], '을', '를')} 넣으면 "
            f"메시지가 훨씬 부드럽게 전달됩니다.")
        evidence.append({"quote": p["fix"], "why": "강사가 밑줄로 표시한 권장 표현"})
    else:
        fix = next((s for s in spans if s["kind"] == "fix"), None)
        key = next((s for s in spans if s["kind"] == "key"), None)
        issue = next((s for s in spans if s["kind"] == "issue"), None)
        if fix:
            sentences.append(
                f"강사가 짚어 준 <b>{fix['text']}</b>{_josa(fix['text'], '을', '를')} "
                f"의식적으로 쓰면 같은 내용이 더 분명하게 전달됩니다.")
            evidence.append({"quote": fix["text"], "why": "권장 표현"})
        elif key:
            sentences.append(
                f"<b>{key['text']}</b>{_josa(key['text'], '이', '가')} 이번 회차의 "
                f"핵심으로 짚혔고, 이 관점이 결과 차이를 만듭니다.")
            evidence.append({"quote": key["text"], "why": "핵심 개념"})
        elif issue:
            sentences.append(
                f"<b>{issue['text']}</b>{_josa(issue['text'], '이', '가')} 반복되면 "
                f"의도와 다르게 받아들여질 수 있습니다.")
            evidence.append({"quote": issue["text"], "why": "고칠 표현"})

    if not evidence:
        # 강조가 하나도 없으면 원문 문장에 기대되, 말투는 맞춘다
        # 문단이므로 원문에 적힌 순서를 그대로 따른다 (체크리스트와 다른 점)
        picked = _in_order(payload.get("gap_text") or "", limit=3)
        # 말투를 맞출 수 없는 메모 조각("Q&A 준비 부족 — 3회")은 문단에 넣지 않는다.
        # 한 문단 안에서 문장 하나만 말투가 다르면 오히려 더 눈에 띈다.
        usable = [(s, _polish(s)) for s in picked]
        usable = [(s, p) for s, p in usable if _is_formal(p)] or usable[:1]
        if not usable:
            return {"text": "", "evidence": []}
        for sent, polished in usable[:2]:
            sentences.append(polished)
            evidence.append({"quote": sent, "why": "강사 코멘트 원문"})

    sentences.append("다음 회차에는 이 부분을 미리 준비해 두고 들어가면 "
                     "같은 상황에서 선택지가 넓어집니다.")

    return {"text": " ".join(sentences), "evidence": evidence}


def _mock_r19(payload: dict) -> Dict[str, object]:
    items = payload.get("anonymized_strengths", [])
    if not items:
        return {"text": "", "evidence": []}
    head = _first_clause(items[0])
    return {"text": f"상위 수행자들에게 공통으로 나타난 특징: {head}",
            "evidence": [{"quote": items[0], "why": "상위 수행자 강점 원문"}]}


def _first_clause(text: str, limit: int = 46) -> str:
    for mark in (". ", "다. ", "요. ", " — ", ", "):
        idx = text.find(mark)
        if 8 < idx < limit:
            return text[:idx + len(mark)].strip().rstrip(",")
    return text[:limit].strip()
