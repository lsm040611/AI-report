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

from tone import is_formal, josa, polish

from . import prompts
from .themes import anonymous_lines, common_themes

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


def warm() -> None:
    """여러 스레드가 동시에 부르기 전에 클라이언트를 미리 만들어 둔다."""
    _client_or_none()


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


# 거절 시 서버가 대체 모델로 재시도해 주는 기능. 쓸 수 있는 모델이 정해져 있어서,
# 아무 모델에나 붙이면 400 이 나고 그 뒤로 모든 호출이 조용히 목 모드로 떨어진다.
FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5", "claude-mythos-5")


def _supports_fallbacks(model: str) -> bool:
    return any(model.startswith(m) for m in FALLBACK_MODELS)


def _call(client, user_prompt: str, schema: dict) -> Optional[dict]:
    """Claude 호출. 구조화 출력으로 스키마를 강제한다.

    max_tokens 는 생각(thinking)과 응답 글자를 **합쳐서** 자른다. 이 모델은
    생각이 기본으로 켜져 있어서, 넉넉히 잡지 않으면 JSON 이 중간에서 잘리고
    파싱 실패로 조용히 목 모드로 떨어진다.
    """
    kwargs = dict(
        model=MODEL,
        max_tokens=16000,
        system=prompts.SYSTEM,
        messages=[{"role": "user", "content": user_prompt}],
        output_config={
            "format": {"type": "json_schema", "schema": schema},
            "effort": EFFORT,
        },
    )

    if _supports_fallbacks(MODEL):
        try:
            # 안전 분류기가 거절하면 서버가 대체 모델로 자동 재시도한다.
            resp = client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                **kwargs,
            )
        except TypeError:
            # SDK 가 낡아 fallbacks 인자를 모르는 경우만 일반 경로로 내려간다.
            # 예전에는 모든 예외를 여기서 삼켜, 400·429 까지 두 번씩 때렸다.
            resp = client.messages.create(**kwargs)
    else:
        resp = client.messages.create(**kwargs)

    stop = getattr(resp, "stop_reason", None)
    if stop == "refusal":
        why = getattr(getattr(resp, "stop_details", None), "category", None)
        raise RuntimeError(f"안전 분류기가 거절했습니다 (분류: {why or '미상'})")
    if stop == "max_tokens":
        raise RuntimeError("응답이 max_tokens 에서 잘렸습니다 — HR_EFFORT 를 낮추십시오")

    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    if not text.strip():
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    # 쓴 토큰을 함께 돌려준다. 발표 전에 한 건이 얼마나 드는지 봐 두면
    # 전체를 돌렸을 때 얼마가 나올지 가늠할 수 있다.
    usage = getattr(resp, "usage", None)
    if usage is not None:
        data["usage"] = {k: getattr(usage, k, None) for k in
                         ("input_tokens", "output_tokens",
                          "cache_creation_input_tokens", "cache_read_input_tokens")}
    return data


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
    """주관식 종합 (목 모드).

    **응답 문장을 그대로 옮기지 않는다.** 단서를 지워도 문장에는 그 사람의
    말버릇과 겪은 사건이 남아 작성자가 특정된다.

    여러 명이 공통으로 말한 주제가 있으면 그것을 쓴다 — 가장 안전하고, 여러
    명이 같은 말을 했다는 사실 자체가 정보다. 공통 주제가 없으면 억지로
    만들지 않고 **한 줄씩 나열한다.** 다만 나열도 익명이어야 해서 인용된
    대사·말버릇을 한 번 더 걷어내고 순서를 다시 늘어놓는다.
    """
    items = [t for t in payload.get("items", []) if t.strip()]
    min_common = int(payload.get("min_common") or 2)
    if not items:
        return {"text": "", "evidence": [], "error": "응답 없음"}

    picked = common_themes(items, min_docs=min_common, top=3) if len(items) >= min_common else []
    if not picked:
        return _mock_r11_listed(items, payload.get("role"))

    # 목 모드는 뜻을 이해하지 못하므로 '무엇을 하라'까지는 쓰지 않는다.
    # 주제어와 인원수만 담백하게 적는다 — 없는 판단을 지어내지 않기 위해서다.
    role = payload.get("role")
    frame = ("<b>{w}</b> 부분이 강점으로 {n}명의 응답에서 공통으로 언급됨."
             if role == "strength" else
             "<b>{w}</b> 부분에 대한 보완 요청이 {n}명의 응답에서 공통으로 언급됨.")

    lines, evidence = [], []
    for t in picked:
        word, n = t["word"], t["docs"]
        lines.append(frame.format(w=word, n=n))
        quote = next((s for s in items if word in s), items[0])
        evidence.append({"quote": quote, "why": f"{n}명이 공통으로 언급한 주제"})
    return {"text": "\n".join(lines), "evidence": evidence}


def _mock_r11_listed(items: List[str], role: str = None) -> Dict[str, object]:
    """공통 주제가 없을 때 — 한 줄씩 나열한다.

    나열이라고 원문 그대로는 아니다. `anonymous_lines` 로 대사·말버릇을 걷어내고
    가나다순으로 다시 늘어놓은 뒤, 말투까지 리포트 전체와 맞춘다. 몇 명이 썼는지는
    적지 않는다 — 응답 수가 적을 때 그 숫자 자체가 사람을 좁힌다.
    """
    lines = anonymous_lines(items, limit=5)
    if not lines:
        return {"text": "", "evidence": [], "error": "쓸 수 있는 응답이 없습니다"}
    return {
        "text": "\n".join(polish(s) for s in lines),
        "evidence": [{"quote": s, "why": "응답 원문(단서 소거 후)"} for s in lines],
    }


def _mock_r17(payload: dict) -> Dict[str, object]:
    """실천 항목을 만든다. **강사가 강조 표시한 구간이 1순위 재료다.**

    강사는 고쳐야 할 곳에 색을 칠하고 대신 쓸 표현에 밑줄을 긋는다. 그 손자국을
    그대로 옮기는 편이 서술 전체를 요약하는 것보다 정확하고, 사람마다 말투가
    달라지지도 않는다. 강조가 하나도 없을 때만 서술 문장으로 내려간다.
    """
    # 진단서베이에는 강조가 없다. 대신 익명 처리된 응답이 들어오는데,
    # 이건 사람마다 문장이 달라 그대로 실으면 작성자가 드러난다.
    anon = payload.get("anonymous_items")
    if anon:
        return _mock_r17_anonymous(anon, int(payload.get("min_common") or 2))

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
        add(f"<b>{issue}</b> 대신 <b>{fix}</b>{josa(fix, '을', '를')} 씁니다.",
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
            add(f"<b>{t}</b>{josa(t, '을', '를')} 염두에 두고 준비합니다.",
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
                add(polish(sent), sent, "강사 코멘트 원문")

    if not lines:
        return {"text": "", "evidence": []}
    return {"text": "\n".join(lines), "evidence": evidence}


def _mock_r17_anonymous(items: List[str], min_common: int = 2) -> Dict[str, object]:
    """진단서베이용 실천 항목 — 공통 주제어만으로 만든다.

    한 사람의 요청을 실천 항목으로 올리면 "저건 누가 쓴 말이다"가 바로 짚인다.
    두 명 이상이 말한 주제만 쓰면 그 연결이 끊긴다.
    """
    picked = common_themes(items, min_docs=min_common, top=3)
    if not picked:
        return {"text": "", "evidence": []}
    lines, evidence = [], []
    for t in picked:
        word = t["word"]
        lines.append(f"{word}{josa(word, '을', '를')} 주제로 팀과 이야기할 자리를 한 번 만듭니다.")
        quote = next((s for s in items if word in s), items[0])
        evidence.append({"quote": quote, "why": f"{t['docs']}명이 공통으로 언급한 주제"})
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
            f"<b>{p['fix']}</b>{josa(p['fix'], '을', '를')} 넣으면 "
            f"메시지가 훨씬 부드럽게 전달됩니다.")
        evidence.append({"quote": p["fix"], "why": "강사가 밑줄로 표시한 권장 표현"})
    else:
        fix = next((s for s in spans if s["kind"] == "fix"), None)
        key = next((s for s in spans if s["kind"] == "key"), None)
        issue = next((s for s in spans if s["kind"] == "issue"), None)
        if fix:
            sentences.append(
                f"강사가 짚어 준 <b>{fix['text']}</b>{josa(fix['text'], '을', '를')} "
                f"의식적으로 쓰면 같은 내용이 더 분명하게 전달됩니다.")
            evidence.append({"quote": fix["text"], "why": "권장 표현"})
        elif key:
            sentences.append(
                f"<b>{key['text']}</b>{josa(key['text'], '이', '가')} 이번 회차의 "
                f"핵심으로 짚혔고, 이 관점이 결과 차이를 만듭니다.")
            evidence.append({"quote": key["text"], "why": "핵심 개념"})
        elif issue:
            sentences.append(
                f"<b>{issue['text']}</b>{josa(issue['text'], '이', '가')} 반복되면 "
                f"의도와 다르게 받아들여질 수 있습니다.")
            evidence.append({"quote": issue["text"], "why": "고칠 표현"})

    if not evidence:
        # 강조가 하나도 없으면 원문 문장에 기대되, 말투는 맞춘다
        # 문단이므로 원문에 적힌 순서를 그대로 따른다 (체크리스트와 다른 점)
        picked = _in_order(payload.get("gap_text") or "", limit=3)
        # 말투를 맞출 수 없는 메모 조각("Q&A 준비 부족 — 3회")은 문단에 넣지 않는다.
        # 한 문단 안에서 문장 하나만 말투가 다르면 오히려 더 눈에 띈다.
        usable = [(s, polish(s)) for s in picked]
        usable = [(s, p) for s, p in usable if is_formal(p)] or usable[:1]
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
