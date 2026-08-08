"""과정 연결 — 파일 속 과정명이 기존 과정인가, 새 과정인가.

UI 검증 화면의 '과정 연결 카드'가 이 판단을 그대로 보여 준다. 그래서 여기서
나가는 `evidence` 는 로그가 아니라 **화면에 실릴 한국어 문장**이어야 한다
(통합 명세 §2-① "사람이 읽을 수 있는 한국어 문장").

판단의 재료는 셋뿐이다 — 표기 변형 사전, 과정명 닮은 정도, 강사 일치.
셋 다 아니면 새 과정을 제안한다. 억지로 잇는 것보다 하나 더 만드는 편이 낫다.
"""
from __future__ import annotations

import hashlib
import re
from typing import Dict, List, Optional

# 과정명에서 회차·조 표기를 떼어 낸다 — '리더십 교육 3차 A조' 와 '리더십 교육' 은
# 같은 과정이다. 이걸 안 떼면 회차마다 새 과정이 생긴다.
_ROUND = re.compile(r"\s*\d+\s*(차수|차|회차|회|기|주차)\b")
_GROUP = re.compile(r"\s*[A-Za-z가-힣]\s*조\b")
_NOISE = re.compile(r"[\s\-_·•/()\[\]{}<>「」『』\"'“”‘’]+")


def normalize(title: str) -> str:
    """비교용 표기. 띄어쓰기·기호·회차·조를 지운 알맹이만 남긴다."""
    s = str(title or "")
    s = _ROUND.sub(" ", s)
    s = _GROUP.sub(" ", s)
    return _NOISE.sub("", s).lower()


def issue_course_id(title: str) -> str:
    """courseId 발급. 같은 과정명이면 언제 불러도 같은 값이 나온다.

    영문이 섞여 있으면 그걸 쓰고(`leadership`), 순한글이면 짧은 해시를 붙인다
    (`crs-3f9a1c`). UI 는 이 값을 의미 없는 키로 다루므로 읽히기만 하면 된다.
    """
    ascii_part = "-".join(re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", str(title or "")))
    if len(ascii_part) >= 3:
        return ascii_part.lower()[:48]
    digest = hashlib.sha1(normalize(title).encode("utf-8")).hexdigest()[:6]
    return f"crs-{digest}"


def _bigrams(s: str) -> set:
    return {s[i:i + 2] for i in range(len(s) - 1)} or {s}


def similarity(a: str, b: str) -> float:
    """과정명 닮은 정도 0~1. 두 글자씩 겹치는 비율(자카드)."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ga, gb = _bigrams(na), _bigrams(nb)
    return len(ga & gb) / len(ga | gb)


# 이 위로는 "같은 과정"으로 본다. 낮추면 다른 과정이 붙고, 높이면 표기가
# 조금만 달라도 새 과정이 생긴다. 0.62 는 '리더십교육' ↔ '리더십 교육 과정'
# 은 붙고 '리더십교육' ↔ '협상스킬심화' 는 안 붙는 선이다.
LINK_THRESHOLD = 0.62


def match(title: Optional[str],
          source_type: Optional[str],
          instructor: Optional[str],
          round_label: Optional[str],
          known: List[dict],
          aliases: Dict[str, str]) -> dict:
    """통합 명세 §2-① 의 `courseMatch` 를 만든다.

    known   : [{courseId, title, sourceType, instructor, rounds:[...]}] 기존 과정
    aliases : {정규화된표기: courseId} 담당자가 확정해 둔 표기 변형 사전
    """
    title = (title or "").strip()
    if not title:
        return {"mode": "create", "suggestedCourseId": None,
                "suggestedTitle": "제목 없는 과정",
                "evidence": "제안 근거 — 파일에서 과정명을 찾지 못했습니다. "
                            "새 과정으로 만들거나 직접 골라 주십시오.",
                "candidates": []}

    # ① 표기 변형 사전에 있으면 더 볼 것이 없다 — 담당자가 이미 확정한 표기다
    hit = aliases.get(normalize(title))
    if hit:
        found = next((c for c in known if c["courseId"] == hit), None)
        if found:
            return {"mode": "link", "suggestedCourseId": hit,
                    "suggestedTitle": found["title"],
                    "evidence": f'제안 근거 — 이전에 담당자가 "{title}" 을(를) '
                                f'{found["title"]} 과정으로 확정한 기록이 있습니다.',
                    "candidates": [{"courseId": hit, "title": found["title"],
                                    "score": 1.0}]}

    # ② 같은 유형 안에서만 후보를 찾는다. 유형이 다르면 같은 이름이어도 다른 과정이다.
    pool = [c for c in known
            if not source_type or not c.get("sourceType")
            or c["sourceType"] == source_type]

    scored = []
    for c in pool:
        s = similarity(title, c["title"])
        if instructor and c.get("instructor") and instructor == c["instructor"]:
            s = min(1.0, s + 0.15)       # 강사가 같으면 한 뼘 당겨 준다
        scored.append({"courseId": c["courseId"], "title": c["title"],
                       "score": round(s, 3), "instructor": c.get("instructor"),
                       "rounds": c.get("rounds") or []})
    scored.sort(key=lambda x: -x["score"])
    top = scored[0] if scored else None

    if top and top["score"] >= LINK_THRESHOLD:
        return {"mode": "link", "suggestedCourseId": top["courseId"],
                "suggestedTitle": top["title"],
                "evidence": _link_evidence(title, top, instructor, round_label),
                "candidates": [{k: v for k, v in c.items()
                                if k in ("courseId", "title", "score")}
                               for c in scored[:5]]}

    return {"mode": "create", "suggestedCourseId": None,
            "suggestedTitle": title,
            "evidence": _create_evidence(source_type, round_label, top),
            "candidates": [{k: v for k, v in c.items()
                            if k in ("courseId", "title", "score")}
                           for c in scored[:5]]}


def _link_evidence(title: str, top: dict, instructor: Optional[str],
                   round_label: Optional[str]) -> str:
    bits = [f'과정명 유사("{title}" ↔ "{top["title"]}")']
    if instructor and top.get("instructor") == instructor:
        bits.append(f"강사 일치({instructor})")
    rounds = [r for r in (top.get("rounds") or []) if r]
    if round_label and rounds:
        bits.append(f'차수 연속({rounds[-1]} → {round_label})')
    elif round_label:
        bits.append(f"차수 표기({round_label})")
    return "제안 근거 — " + ", ".join(bits)


def _create_evidence(source_type: Optional[str], round_label: Optional[str],
                     top: Optional[dict]) -> str:
    if source_type == "단발특강":
        return ("제안 근거 — 차수 표기 없음, 단일 세션 구조 — "
                "기존 과정과 연속성이 확인되지 않습니다.")
    near = ""
    if top and top["score"] > 0:
        near = f' 가장 가까운 기존 과정은 "{top["title"]}"(유사도 {top["score"]:.2f})입니다.'
    head = (f"제안 근거 — 차수 표기({round_label})는 있으나 "
            if round_label else "제안 근거 — ")
    return head + "이름이 충분히 닮은 기존 과정이 없습니다." + near
