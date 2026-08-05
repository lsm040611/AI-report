"""생성 작업별 프롬프트.

공통 원칙 하나: **모든 생성물은 근거 구절을 함께 낸다.**
R-16 이 그 근거를 원문과 대조해서 통과 여부를 정하므로, 근거를 지어내면
저장 자체가 되지 않는다. 프롬프트가 그 제약을 미리 알려 주는 편이 통과율이 높다.
"""
from __future__ import annotations

import json
from typing import Tuple

SYSTEM = """당신은 사내 HRD 팀의 리포트 편집자입니다. 교육 참가자 본인에게 전달되는
개인 피드백 리포트의 문장을 다듬습니다.

지켜야 할 규칙:
1. 원문에 없는 사실을 새로 만들지 않습니다. 관찰되지 않은 원인·의도·평가를 추정하지 않습니다.
2. 모든 문장은 제공된 원문 구절에 근거해야 합니다. 근거로 인용하는 구절은
   제공된 원문에서 **글자 그대로** 가져옵니다. 요약하거나 다듬어서 인용하지 않습니다.
3. 받는 사람은 평가 대상 본인입니다. 3인칭 관찰 서술이 아니라, 정중한 한국어
   존댓말로 씁니다. 비난형 표현은 실행 중심 코칭 문장으로 바꿉니다.
4. 엔진 사정(재계산·익명성 규칙·파싱)은 절대 쓰지 않습니다. 받는 사람이
   알아야 하는 사실만 씁니다.
5. 특정 개인을 지목할 수 있는 표현(이름, 부서, 특정 사건의 시점)은 쓰지 않습니다.

출력은 지정된 JSON 스키마를 따릅니다."""

# text + evidence 를 강제하는 스키마. R-16 검사가 이 evidence 를 원문과 대조한다.
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "description": "리포트에 실릴 최종 문장. 줄바꿈으로 항목을 구분할 수 있다.",
        },
        "evidence": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "quote": {
                        "type": "string",
                        "description": "제공된 원문에서 글자 그대로 옮긴 구절 (20자 이상 권장)",
                    },
                    "why": {"type": "string", "description": "이 구절이 근거가 되는 이유 한 줄"},
                },
                "required": ["quote", "why"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["text", "evidence"],
    "additionalProperties": False,
}


# 암기 문장 카드는 문장 하나로 끝나지 않는다 — 어느 교정 표현에서 나온
# 조각인지, 마무리 문장은 무엇인지가 함께 와야 카드가 완성된다.
MEMORIZE_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string",
                 "description": "외울 문장 한 개. 강사 교정 표현을 그대로 포함한다."},
        "parts": {
            "type": "array", "minItems": 1, "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "quote": {"type": "string",
                              "description": "문장 안에 들어간 강사 교정 표현(원문 그대로)"},
                    "note": {"type": "string", "description": "그 표현이 무엇을 고치는지 한 줄"},
                },
                "required": ["quote", "note"], "additionalProperties": False,
            },
        },
        "closing": {"type": "string",
                    "description": "강점은 유지하고 이번 약점만 보완한다는 마무리 한 문장"},
        "evidence": OUTPUT_SCHEMA["properties"]["evidence"],
    },
    "required": ["text", "parts", "closing", "evidence"],
    "additionalProperties": False,
}


EMPHASIS_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "판단 요약 한 줄"},
        "spans": {
            "type": "array", "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "quote": {"type": "string",
                              "description": "원문에 그대로 있는 구간 (부분 문자열이어야 함)"},
                    "kind": {"type": "string",
                             "enum": ["issue", "fix", "key", "none"]},
                    "why": {"type": "string", "description": "그렇게 판단한 이유 한 줄"},
                },
                "required": ["quote", "kind", "why"], "additionalProperties": False,
            },
        },
        "evidence": OUTPUT_SCHEMA["properties"]["evidence"],
    },
    "required": ["text", "spans", "evidence"],
    "additionalProperties": False,
}


def build(task: str, payload: dict) -> Tuple[str, str, dict]:
    """(작업 설명, 사용자 프롬프트, 출력 스키마) 반환."""
    if task == "classify_emphasis":
        return "R-05 강조 의미 판정", _r05(payload), EMPHASIS_SCHEMA
    if task == "rewrite_neutral_third_person":
        return "R-11 익명 재작성", _r11(payload), OUTPUT_SCHEMA
    if task == "translate_en_to_ko":
        return "R-13 번역", _r13(payload), OUTPUT_SCHEMA
    if task == "curate_memorize":
        return "R-17 암기 문장", _r17_memorize(payload), MEMORIZE_SCHEMA
    if task == "curate_gap_comment":
        return "R-17 개선점 코멘트", _r17_gap(payload), OUTPUT_SCHEMA
    if task.startswith("curate_"):
        return f"R-17 큐레이션({payload.get('form')})", _r17(payload), OUTPUT_SCHEMA
    if task == "extract_common_traits":
        return "R-19 공통 특징", _r19(payload), OUTPUT_SCHEMA
    if task == "propose_competency_mapping":
        return "R-18 역량 매핑 제안", _r18(payload), OUTPUT_SCHEMA
    return task, json.dumps(payload, ensure_ascii=False, indent=2), OUTPUT_SCHEMA


# --------------------------------------------------------------------------
_ROLE_HINT = {
    "strength": "이 칸은 **잘한 점**을 적는 칸입니다.",
    "gap": "이 칸은 **보완할 점**을 적는 칸입니다.",
    "next_action": "이 칸은 **다음 과제**를 적는 칸입니다.",
    "change_request": "이 칸은 **바라는 변화**를 적는 칸입니다.",
}


def _r05(p: dict) -> str:
    marked = p.get("marked") or []
    mk = "\n".join(f'- ({m.get("format") or "서식 있음"}) "{m.get("text")}"'
                   for m in marked) or "- (표시된 구간 없음)"
    role = _ROLE_HINT.get(p.get("role") or "", "")

    return f"""강사가 평가지에 적은 코멘트입니다. 강조 표시된 구간이 **무엇을 뜻하는지**
문장을 읽고 판정하십시오.

{role}

[코멘트 원문]
{p.get('text', '')}

[강사가 서식으로 표시한 구간]
{mk}

**중요**: 서식은 "여기를 보라"는 표시일 뿐, 무엇인지는 알려 주지 않습니다.
강사에 따라 좋은 점이든 나쁜 점이든 가리지 않고 전부 굵게 칠하기도 합니다.
**서식 종류를 근거로 삼지 말고, 문장의 뜻으로만 판단하십시오.**

각 구간을 넷 중 하나로 분류합니다.
- `issue` : 강사가 **고치라고 지적한** 표현이나 행동
- `fix`   : 그 대신 **쓰라고 제시한** 표현
- `key`   : 기억해 둘 개념·원칙 (고칠 것도, 대체 표현도 아님)
- `none`  : 강조 습관일 뿐 의미가 없는 구간 (사람 이름, 단순 나열 등)

추가 규칙
1. `quote` 는 원문에 **글자 그대로** 있는 부분 문자열이어야 합니다. 다듬지 마십시오.
2. 서식이 없어도 `"A" → "B"` 처럼 교정 쌍이 분명하면 그 구간도 넣으십시오.
   서식을 빠뜨리는 강사도 있습니다.
3. 표시된 구간은 **빠짐없이** 판정에 포함하십시오. 의미가 없으면 `none` 으로 냅니다.
4. `issue` 와 `fix` 는 짝을 이룰 때가 많습니다. 원문에서 앞에 나온 쪽이 대개 `issue` 지만,
   순서가 아니라 문맥으로 판단하십시오.

`text` 에는 판단 요약을 한 줄로 적고, evidence 에는 판단 근거가 된 원문 구절을
글자 그대로 넣으십시오."""


def _r11(p: dict) -> str:
    items = "\n".join(f"- {t}" for t in p.get("items", []))
    return f"""아래는 한 리더에 대한 동료·구성원·상사의 주관식 응답입니다.
작성자를 특정할 단서(시점·소속·자기지칭·어투)는 이미 제거된 상태입니다.

원문 응답 ({p.get('label', '주관식')}):
{items}

할 일: **두 명 이상이 공통으로 말한 주제만** 골라, 각 주제를 한 문단으로 재작성하십시오.
- 한 명만 말한 내용은 버립니다. 응답이 {p.get('min_common', 2)}건 미만인 주제는 쓰지 않습니다.
- 주제가 여러 개면 각 주제를 줄바꿈으로 구분합니다. 최대 3개.
- 각 문단은 굵게 강조할 핵심 요청 한 문장으로 시작하고, 그 뒤에 배경을 붙입니다.
  핵심 문장은 <b>...</b> 로 감쌉니다.
- 비난이 아니라 요청으로 읽히게 씁니다.

evidence 에는 위 원문 목록에서 그대로 옮긴 구절을 주제 수만큼 넣으십시오."""


def _r13(p: dict) -> str:
    keep = p.get("preserve_verbatim") or []
    keep_txt = "\n".join(f'- {k}' for k in keep) or "- (없음)"
    return f"""아래 영어 강사 코멘트를 한국어로 옮기십시오.

원문:
{p.get('source_text', '')}

원어를 그대로 두어야 하는 구간 (학습 대상 표현이므로 번역하지 않습니다):
{keep_txt}

할 일: 자연스러운 한국어 존댓말로 번역하되, 위 구간은 영어 원문 그대로 남깁니다.
전문 용어는 억지로 옮기지 않습니다.

evidence 에는 번역의 근거가 된 영어 원문 구절을 글자 그대로 넣으십시오."""


_FORM_GUIDE = {
    "연계형": "다음 세션까지 실천할 행동 1~2가지. 지난 지적과 이어지도록 씁니다.",
    "정리형": "오늘 지적과 과제를 한 문단으로 묶어 실천 제안 1가지로 정리합니다.",
    "제안형": "여러 응답에 공통으로 나타난 주제에서만 도출한 실천 2~3가지.",
}


_KIND_LABEL = {"issue": "고칠 표현(붉은색)",
               "fix": "권장 표현(굵게+밑줄)",
               "key": "핵심 개념(굵게)"}


def _r17(p: dict) -> str:
    ev = "\n".join(f"- [{e.get('role')}] {e.get('quote')}"
                   for e in p.get("evidence", [])) or "- (없음)"

    spans = p.get("emphasis") or []
    marked = "\n".join(f'- {_KIND_LABEL.get(s["kind"], s["kind"])}: "{s["text"]}"'
                       for s in spans) or "- (없음)"
    pairs = p.get("pairs") or []
    pair_txt = "\n".join(f'- "{q["issue"]}"  →  "{q["fix"]}"' for q in pairs) or "- (없음)"

    form = p.get("form", "정리형")
    return f"""아래는 한 참가자에 대한 강사·동료의 지적과 과제입니다.

[강사가 직접 강조 표시한 구간]  ← 여기가 개선 지점입니다
{marked}

[교정 쌍 (이렇게 말고 → 이렇게)]
{pair_txt}

[서술 원문]
{ev}

할 일: 이 사람이 다음까지 실천할 항목을 **{form}**으로 만드십시오.
{_FORM_GUIDE.get(form, '')}

**작성 규칙**
1. **강조 표시된 구간을 중심으로** 항목을 만드십시오. 강조가 있으면 그것을 먼저 쓰고,
   강조가 없을 때만 서술 원문에서 뽑습니다. 강조된 표현은 문장 안에 원문 그대로 넣고
   <b>...</b> 로 감쌉니다.
2. 교정 쌍이 있으면 "무엇 대신 무엇을 쓴다" 형태로 한 항목을 만듭니다.
3. **모든 문장은 '~합니다' 로 끝냅니다.** 명령형('~할 것', '~하라'), 다짐형('~하겠다'),
   명사형('~하기')을 쓰지 않습니다. 말투는 항목 전체에서 동일해야 합니다.
4. 한 줄에 항목 하나, 최대 3줄. 각 줄은 60자를 넘기지 않습니다.
5. 원문에 없는 새 과제를 만들지 않습니다. 강조되지 않은 것을 강조된 것처럼 쓰지 않습니다.

evidence 에는 각 항목의 근거가 된 구절(강조 구간 또는 서술 원문)을 **글자 그대로**
넣으십시오. 항목 순서와 evidence 순서를 맞춰 주십시오."""


def _r17_gap(p: dict) -> str:
    """리포트의 '함께 살펴보면 좋을 점' 본문을 다시 쓴다."""
    spans = p.get("emphasis") or []
    marked = "\n".join(f'- {_KIND_LABEL.get(s["kind"], s["kind"])}: "{s["text"]}"'
                       for s in spans) or "- (없음)"
    pairs = p.get("pairs") or []
    pair_txt = "\n".join(f'- "{q["issue"]}"  →  "{q["fix"]}"' for q in pairs) or "- (없음)"

    w = p.get("weakest") or {}
    weak = (f'{w.get("name")} {w.get("score")}'
            + (f'/{w.get("max")}' if w.get("max") else "")) if w else "(없음)"
    strength = p.get("strength_text") or "(없음)"

    return f"""리포트의 **'함께 살펴보면 좋을 점'** 문단을 새로 쓰십시오.
받는 사람은 평가 대상 본인입니다.

[강사가 적은 개선점 원문]
{p.get('gap_text', '')}

[강사가 강조 표시한 구간]
{marked}

[교정 쌍 (이렇게 말고 → 이렇게)]
{pair_txt}

[강사가 적은 강점 원문 — 맥락 참고용]
{strength}

[점수가 가장 낮은 역량]
{weak}

**작성 규칙**
1. 원문을 그대로 옮기지 마십시오. **다시 쓴 문장**이어야 합니다.
   다만 강조 표시된 표현과 교정 쌍의 문구는 **원문 그대로** 인용하고 <b>...</b> 로 감쌉니다.
2. 3~4문장. 이 순서로 씁니다.
   ① 무엇이 관찰되었는지 (원문 개선점에 근거)
   ② 그것이 왜 결과에 영향을 주는지
   ③ 다음에 어떻게 하면 좋은지 — **여기서는 원문에 없는 조언을 덧붙여도 됩니다.**
      단, 관찰된 사실과 이어지는 조언이어야 하고, 일반론("연습이 중요합니다")은 쓰지 않습니다.
3. **모든 문장을 '~합니다' 체로 끝냅니다.** 평가·훈계조("~해야 합니다", "부족합니다",
   "~하지 못했습니다")를 쓰지 않습니다. 관찰과 제안으로만 씁니다.
4. 이름을 부르지 않고, '당신'·'귀하' 같은 2인칭 대명사도 쓰지 않습니다.
5. 점수·등수를 새로 계산하거나 원문에 없는 사실을 만들지 않습니다.
6. 줄바꿈 없이 한 문단으로 씁니다.

evidence 에는 이 문단의 근거가 된 **원문 구절을 글자 그대로** 넣으십시오
(개선점 원문 또는 강조 구간). 최소 1개."""


def _r17_memorize(p: dict) -> str:
    """강사 교정 표현을 재료로 '외울 문장' 하나를 엮는다."""
    pairs = p.get("pairs") or []
    pair_txt = "\n".join(f'- "{q["issue"]}"  →  "{q["fix"]}"' for q in pairs) or "- (없음)"
    fixes = [s["text"] for s in (p.get("emphasis") or []) if s["kind"] == "fix"]
    keys = [s["text"] for s in (p.get("emphasis") or []) if s["kind"] == "key"]

    return f"""참가자가 **다음 회차까지 통째로 외울 문장 한 개**를 만드십시오.

[강사 교정 쌍 (이렇게 말고 → 이렇게)]
{pair_txt}

[강사가 권장한 표현]
{chr(10).join('- "' + f + '"' for f in fixes) or '- (없음)'}

[강사가 짚은 핵심 개념]
{chr(10).join('- ' + k for k in keys) or '- (없음)'}

[이번 회차 강점 원문 — 무엇을 유지해야 하는지 참고용]
{p.get('strength_text') or '(없음)'}

**작성 규칙**
1. `text` 는 **문장 하나**입니다. 위 권장 표현을 **글자 그대로** 문장 안에 넣어 엮습니다.
   표현을 바꾸거나 다듬지 마십시오 — 외워서 그대로 쓸 문장이기 때문입니다.
2. 재료가 영어면 문장도 영어로, 한국어면 한국어로 만듭니다. 실제 상황에서
   입 밖으로 낼 수 있는 자연스러운 문장이어야 합니다.
3. `parts` 에는 문장에 넣은 표현마다 한 줄씩, 그 표현이 **무엇을 고치는지**를 적습니다.
   `quote` 는 문장 안에 실제로 들어간 문자열과 정확히 일치해야 합니다.
4. `closing` 은 한국어 한 문장입니다. **이미 잘하고 있는 것은 그대로 두고 이번에 짚인
   부분만 이 문장으로 채운다**는 뜻이 담겨야 합니다. '~합니다' 로 끝냅니다.
   강점 원문에 근거가 있으면 무엇을 유지하는지 구체적으로 씁니다.
5. 원문에 없는 표현을 새로 만들지 않습니다.

evidence 에는 문장의 재료가 된 강사 원문 구절을 글자 그대로 넣으십시오."""


def _r19(p: dict) -> str:
    items = "\n".join(f"- {t}" for t in p.get("anonymized_strengths", []))
    return f"""아래는 이번 과정 상위 수행자 {p.get('group_size')}명의 강점 코멘트입니다.
이름은 이미 ○○ 로 치환되어 있습니다.

{items}

할 일: **3명 이상에게서 공통으로 나타난 행동·표현**만 골라 2~3줄로 정리하십시오.
개인 사례나 특정 케이스는 언급하지 않습니다. 한 줄에 한 가지.

evidence 에는 공통점의 근거가 된 구절을 그대로 넣으십시오."""


def _r18(p: dict) -> str:
    known = ", ".join(p.get("known", []))
    return f"""사내 표준 역량명으로 매핑해야 하는 항목이 있습니다.

원본 역량명: {p.get('area_name')}
현재 표준 역량 목록: {known or '(없음)'}

할 일: 위 원본 역량명이 표준 목록 중 어느 것에 해당하는지 고르십시오.
해당하는 것이 없으면 새 표준명을 제안하십시오.
text 에는 표준 역량명 **하나만** 쓰십시오 (설명 없이).
evidence 에는 판단 근거를 한 줄로 쓰되 quote 에 원본 역량명을 그대로 넣으십시오."""
