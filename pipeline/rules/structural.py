"""R-01 ~ R-06 : 결정론적 구조 정제. 전부 담당 = code.

계약 규칙표를 그대로 옮긴 것이라, 규칙 문면과 함수가 1:1로 대응한다.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Optional

from .base import (AI, CODE, NOTICE, REVIEW, RuleContext, add_flag, mark_applied,
                   request_handoff, rule)

EXCEL_EPOCH = dt.datetime(1899, 12, 30)   # 1900 윤년 버그 보정 포함


# --------------------------------------------------------------------------
@rule("R-01", CODE,
      "날짜가 시리얼값·datetime·문자열로 뒤섞임",
      "전부 ISO 형식(YYYY-MM-DD)으로 정규화한다")
def r01_normalize_dates(card: dict, ctx: RuleContext) -> None:
    context = card.get("context", {})
    for key, value in list(context.items()):
        if not any(k in str(key) for k in ("날짜", "일자", "date", "Date")):
            continue
        iso = _to_iso(value)
        if iso and iso != value:
            context[key] = iso
            mark_applied(card, "R-01", "시리얼 날짜 변환")


def _to_iso(value) -> Optional[str]:
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, (int, float)) and 20000 < value < 80000:
        return (EXCEL_EPOCH + dt.timedelta(days=float(value))).date().isoformat()
    if isinstance(value, str):
        s = value.strip()
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y년 %m월 %d일", "%Y%m%d"):
            try:
                return dt.datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                continue
    return None


# --------------------------------------------------------------------------
@rule("R-02", CODE,
      '점수가 문자열("4.0")로 저장됨',
      "숫자로 바꾸고, 실패하면 결측으로 처리한다")
def r02_cast_scores(card: dict, ctx: RuleContext) -> None:
    for item in card.get("scores", []):
        raw = item.get("score")
        if isinstance(raw, (int, float)) or raw is None:
            continue
        try:
            item["score"] = float(str(raw).strip())
            mark_applied(card, "R-02", "문자열 점수 캐스팅")
        except (TypeError, ValueError):
            item["score"] = None
            add_flag(card, "missing_score", NOTICE, target=item.get("area_name"))
            mark_applied(card, "R-02", "캐스팅 실패 → 결측")

    # 원본 평균란도 같은 규칙으로 숫자화한다 (R-03 비교 대상이므로)
    summary = card.get("score_summary") or {}
    raw = summary.get("original_average")
    if raw is not None and not isinstance(raw, (int, float)):
        try:
            summary["original_average"] = float(str(raw).strip())
        except (TypeError, ValueError):
            summary["original_average"] = None


# --------------------------------------------------------------------------
@rule("R-03", CODE,
      "평균이 수식과 하드코딩으로 뒤섞임",
      "평균은 항상 엔진이 다시 계산한다. 원본과 0.05 넘게 다르면 review 플래그")
def r03_recompute_average(card: dict, ctx: RuleContext) -> None:
    values = [s["score"] for s in card.get("scores", []) if s.get("score") is not None]
    if not values:
        return
    computed = round(sum(values) / len(values), 2)
    summary = card.setdefault("score_summary", {})
    original = summary.get("original_average")

    summary["average"] = computed
    summary["n_areas"] = len(values)

    if original is None:
        # R-03b : 평균란 자체가 없는 양식(특강)
        summary["note"] = "computed_by_engine"
        mark_applied(card, "R-03b", "평균란 없음 → 엔진 계산으로 보충")
        return

    if abs(float(original) - computed) > 0.05:
        summary["note"] = "recomputed(원본 존재)"
        add_flag(card, "average_mismatch", REVIEW,
                 detail=f"원본 {original} vs 재계산 {computed}")
        mark_applied(card, "R-03", "원본 평균 오류 검출")
    else:
        summary["note"] = "computed_by_engine"
        mark_applied(card, "R-03", "원본 평균과 일치")


# --------------------------------------------------------------------------
@rule("R-04", CODE,
      "척도가 과정마다 다름 (1~5/0.5 · 1~10/정수)",
      "모든 점수에 척도 정보를 함께 붙인다. 비교와 시각화는 비율로 환산")
def r04_attach_scale(card: dict, ctx: RuleContext) -> None:
    """척도는 detect 단계에서 추론해 넣지만, 누락 시 값 분포로 보정한다."""
    scores = card.get("scores", [])
    if not scores:
        return
    known = [s["scale"] for s in scores if s.get("scale")]
    fallback = known[0] if known else _infer_scale(scores)
    for s in scores:
        if not s.get("scale"):
            s["scale"] = dict(fallback)
            mark_applied(card, "R-04", "척도 보충")
    card.setdefault("score_summary", {})["scale"] = dict(known[0] if known else fallback)


def _infer_scale(scores) -> dict:
    vals = [s["score"] for s in scores if s.get("score") is not None]
    if vals and max(vals) > 5:
        return {"min": 1, "max": 10, "step": 1}
    has_half = any(abs(v * 2 - round(v * 2)) < 1e-9 and abs(v - round(v)) > 1e-9
                   for v in vals)
    return {"min": 1, "max": 5, "step": 0.5 if has_half else 1}


def to_ratio(score: Optional[float], scale: dict) -> Optional[float]:
    """과정 간 비교용 0~1 환산. 척도가 다른 점수를 나란히 놓을 때만 쓴다."""
    if score is None:
        return None
    lo, hi = scale.get("min", 1), scale.get("max", 5)
    return round((score - lo) / (hi - lo), 4) if hi > lo else None


# --------------------------------------------------------------------------
# R-05 : 강사의 강조 서식 -> 의미 이름
# 실제 변환은 reader.py 에서 셀을 읽는 시점에 일어난다.
# pandas.read_excel 은 서식을 버리므로 openpyxl rich_text 경로가 필수.
# --------------------------------------------------------------------------
FORMAT_LABEL = {
    "issue_expression": "붉은 글자",
    "corrected_expression": "굵게+밑줄",
    "key_concept": "굵게",
}
KIND_TO_EMPHASIS = {
    "issue": "issue_expression",
    "fix": "corrected_expression",
    "key": "key_concept",
    "none": None,
}


@rule("R-05", f"{CODE}+{AI}",
      "강사의 강조 서식",
      "서식으로 후보를 잡고, 그 뜻은 문장을 읽어 판정한다. 서식 습관을 곧 의미로 믿지 않는다")
def r05_verify_runs(card: dict, ctx: RuleContext) -> None:
    """reader가 만든 runs가 계약의 emphasis 어휘만 쓰는지 검증한다."""
    allowed = set(FORMAT_LABEL) | {None}
    touched = False
    for nar in card.get("narratives", []):
        for run in nar.get("runs", []):
            if run.get("emphasis") not in allowed:
                run["emphasis"] = None
            if run.get("emphasis"):
                touched = True
    if touched:
        mark_applied(card, "R-05", "서식 → 의미 후보 추출")


def r05_request_semantic_check(card: dict, ctx: RuleContext) -> None:
    """강조 구간의 '뜻'을 문장으로 판정하도록 넘긴다.

    서식은 어디를 봐야 하는지를 알려 줄 뿐, 무엇인지는 알려 주지 않는다.
    강사가 좋은 점이든 나쁜 점이든 전부 굵게 칠하는 습관이라면, 서식을 그대로
    의미로 옮기는 순간 '권장 표현'과 '고칠 표현'이 뒤바뀐다. 그래서 서식은
    후보 추출까지만 쓰고, 판정은 문장을 읽어서 한다.

    역할(R-12)이 정해진 뒤에 호출해야 한다 — 같은 표현도 강점 칸에 있느냐
    보완 칸에 있느냐에 따라 뜻이 달라지기 때문이다.
    """
    for nar in card.get("narratives", []):
        runs = nar.get("runs") or []
        text = "".join(r.get("text", "") for r in runs)
        if not text.strip():
            continue
        marked = [{"text": (r.get("text") or "").strip(),
                   "format": FORMAT_LABEL.get(r.get("emphasis"), "")}
                  for r in runs if r.get("emphasis")]
        if not marked and "→" not in text and "->" not in text:
            continue                       # 강조도 화살표도 없으면 볼 것이 없다

        request_handoff(ctx, card, "R-05", "classify_emphasis", {
            "label": nar.get("original_label"),
            "role": nar.get("role"),
            "text": text,
            "marked": marked,
        })
    if card.get("narratives"):
        mark_applied(card, "R-05", "강조 의미 판정 요청")


def retag_runs(text: str, spans) -> list:
    """판정 결과로 runs 를 다시 만든다.

    구간이 겹치면 앞선 것만 남긴다. 원문 글자는 하나도 바뀌지 않고, 어디에
    어떤 의미가 붙는지만 달라진다.
    """
    marks = []
    for s in spans or []:
        quote = (s.get("quote") or "").strip()
        if not quote or s.get("kind") not in KIND_TO_EMPHASIS:
            continue
        idx = text.find(quote)
        if idx < 0:
            continue
        marks.append((idx, idx + len(quote), KIND_TO_EMPHASIS[s["kind"]]))

    marks.sort()
    out, cursor = [], 0
    for start, end, emphasis in marks:
        if start < cursor:
            continue                       # 앞 구간과 겹치면 버린다
        if start > cursor:
            out.append({"text": text[cursor:start], "emphasis": None})
        out.append({"text": text[start:end], "emphasis": emphasis})
        cursor = end
    if cursor < len(text):
        out.append({"text": text[cursor:], "emphasis": None})
    return [r for r in out if r["text"]]


# --------------------------------------------------------------------------
SYMBOL_START = re.compile(r"^\s*[*※#\-–—(\[<¹²³†‡]")


@rule("R-06", CODE,
      "각주·안내 행이 데이터에 섞임",
      "이름이 기호로 시작하거나 점수·서술이 전부 비면 사람 행이 아니다")
def r06_is_person_row(name, scores, narratives) -> bool:
    """다른 규칙과 달리 카드 생성 '이전'에 호출되는 게이트.

    검증 첫 실행에서 각주 행이 사람 카드로 잘못 생성돼 31장이 나왔던
    사례를 막는 규칙이다(계약: 26장으로 정상화).
    """
    if not name or not str(name).strip():
        return False
    if SYMBOL_START.match(str(name)):
        return False
    has_score = any(s.get("score") is not None for s in scores)
    has_text = any((n.get("runs") or n.get("raw_items")) for n in narratives)
    return has_score or has_text
