"""R-01 ~ R-06 : 결정론적 구조 정제. 전부 담당 = code.

계약 규칙표를 그대로 옮긴 것이라, 규칙 문면과 함수가 1:1로 대응한다.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Optional

from .base import CODE, NOTICE, REVIEW, RuleContext, add_flag, mark_applied, rule

EXCEL_EPOCH = dt.datetime(1899, 12, 30)   # 1900 윤년 버그 보정 포함


# --------------------------------------------------------------------------
@rule("R-01", CODE,
      "날짜가 시리얼값·datetime·문자열로 뒤섞임",
      "전부 ISO 형식(YYYY-MM-DD)으로 정규화한다")
def r01_normalize_dates(card: dict, ctx: RuleContext) -> None:
    context = card.get("context", {})
    for key, value in list(context.items()):
        if not any(k in key for k in ("날짜", "일자", "date")):
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
    original = summary.get("average")

    if original is None:
        # R-03b : 평균란 자체가 없는 양식(특강)
        summary["average"] = computed
        summary["note"] = "computed_by_engine"
        mark_applied(card, "R-03b", "평균란 없음 → 엔진 계산으로 보충")
        return

    summary["average"] = computed
    if abs(float(original) - computed) > 0.05:
        summary["note"] = "recomputed(원본 존재)"
        add_flag(card, "average_mismatch", REVIEW,
                 detail=f"원본 {original} vs 재계산 {computed}")
        mark_applied(card, "R-03", "원본 평균 오류 검출")
    else:
        summary["note"] = "computed_by_engine"


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
    lo, hi = scale["min"], scale["max"]
    return round((score - lo) / (hi - lo), 4) if hi > lo else None


# --------------------------------------------------------------------------
# R-05 : 강사의 강조 서식 -> 의미 이름
# 실제 변환은 reader.py 에서 셀을 읽는 시점에 일어난다.
# pandas.read_excel 은 서식을 버리므로 openpyxl rich_text 경로가 필수.
# --------------------------------------------------------------------------
@rule("R-05", CODE,
      "강사의 강조 서식",
      "빨강은 문제 표현, 굵게+밑줄은 교정 표현, 굵게는 핵심 개념으로 변환한다")
def r05_verify_runs(card: dict, ctx: RuleContext) -> None:
    """reader가 만든 runs가 계약의 emphasis 어휘만 쓰는지 검증한다."""
    allowed = {None, "issue_expression", "corrected_expression", "key_concept"}
    touched = False
    for nar in card.get("narratives", []):
        for run in nar.get("runs", []):
            if run.get("emphasis") not in allowed:
                run["emphasis"] = None
            if run.get("emphasis"):
                touched = True
    if touched:
        mark_applied(card, "R-05", "서식 → 의미 변환")


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
