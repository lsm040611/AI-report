"""과정 단위 집계 — UI 통합 지점 ⑤.

두 가지 원칙만 지키면 나머지는 산수다.

1. **집계 단위는 항상 동일 과정 안.** 과정 횡단 통합 지표는 만들지 않는다.
   서로 다른 교육의 점수를 한 줄에 놓으면 그 줄은 아무 뜻도 없다.
2. **회차 간 평가 영역 구성이 다르면 그 항목은 비교하지 않는다.** 1차에 없던
   역량이 2차에 생겼다면 '상승'이 아니라 '새로 생김'이다.

자동 인사이트 문구는 여기서 지어내지 않는다. 점수에서 곧바로 읽히는 사실만
문장으로 옮기고, 그 문장이 어느 숫자에서 나왔는지 `basis` 에 함께 싣는다
(R-16 의 취지 — 근거 없는 문장 금지).
"""
from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import Card, Course

router = APIRouter(prefix="/insights", tags=["insights"])


@router.get("/courses")
def list_courses(db: Session = Depends(get_db)):
    """인사이트 탭의 2단 셀렉터(유형 → 과정)가 쓰는 목록."""
    out = []
    for c in db.query(Course).all():
        cards = _cards_of(db, c.course_id)
        out.append({"courseId": c.course_id, "title": c.title,
                    "sourceType": c.source_type, "instructor": c.instructor,
                    "people": len({x.person_name for x in cards}),
                    "rounds": _round_labels(cards)})
    return {"courses": out}


@router.get("/course/{course_id}")
def course_insight(course_id: str, db: Session = Depends(get_db)):
    course = db.query(Course).filter(Course.course_id == course_id).first()
    if not course:
        raise HTTPException(404, f"모르는 과정입니다 — {course_id}")
    cards = _cards_of(db, course_id)
    if not cards:
        raise HTTPException(404, "이 과정에는 아직 카드가 없습니다")

    stype = course.source_type or _guess_type(cards)
    head = {"courseId": course_id, "title": course.title,
            "sourceType": stype, "people": len({c.person_name for c in cards}),
            "cards": len(cards)}

    if stype == "누적교육":
        return {**head, **_accumulated(cards)}
    if stype == "진단서베이":
        return {**head, **_diagnosis(cards)}
    return {**head, **_single(cards)}


# ══════════════════════════════════════════════════════════════
# 누적교육 — 회차별 추이 + 항목별 이번 회차 vs 과정 평균
# ══════════════════════════════════════════════════════════════
def _accumulated(cards: List[Card]) -> dict:
    by_round: Dict[str, List[Card]] = {}
    for c in cards:
        by_round.setdefault(c.round_label or "회차 미상", []).append(c)
    order = sorted(by_round, key=_round_key)

    trend = []
    for label in order:
        vals = [_avg_of(c) for c in by_round[label]]
        vals = [v for v in vals if v is not None]
        trend.append({"round": label, "n": len(by_round[label]),
                      "average": round(sum(vals) / len(vals), 2) if vals else None})

    latest = order[-1] if order else None
    course_avg = _area_averages(cards)
    latest_avg = _area_averages(by_round.get(latest, [])) if latest else {}

    # 회차마다 평가 영역이 다르면 비교하지 않는다 — 없는 것과 낮은 것은 다르다
    shared = sorted(set(latest_avg) & set(course_avg))
    dropped = sorted(set(course_avg) - set(latest_avg))
    areas = [{"area": a, "latest": latest_avg[a], "courseAverage": course_avg[a],
              "delta": round(latest_avg[a] - course_avg[a], 2)} for a in shared]
    areas.sort(key=lambda x: x["delta"])

    return {
        "kind": "누적교육",
        "trend": trend,
        "latestRound": latest,
        "areas": areas,
        "areasNotCompared": dropped,
        "insights": _accum_insights(trend, areas, dropped),
    }


def _accum_insights(trend: List[dict], areas: List[dict],
                    dropped: List[str]) -> List[dict]:
    out = []
    pts = [t for t in trend if t["average"] is not None]
    if len(pts) >= 2:
        first, last = pts[0], pts[-1]
        d = round(last["average"] - first["average"], 2)
        word = "올랐습니다" if d > 0 else ("내렸습니다" if d < 0 else "유지되었습니다")
        out.append({
            "text": f'{first["round"]}부터 {last["round"]}까지 과정 평균이 '
                    f'{abs(d)}점 {word}.',
            "basis": f'{first["round"]} {first["average"]} → '
                     f'{last["round"]} {last["average"]}'})
    if areas:
        low, high = areas[0], areas[-1]
        if low["delta"] < 0:
            out.append({"text": f'이번 회차에서 과정 평균보다 낮은 항목은 '
                                f'"{low["area"]}" 입니다.',
                        "basis": f'{low["latest"]} vs 과정 평균 {low["courseAverage"]}'})
        if high["delta"] > 0:
            out.append({"text": f'"{high["area"]}" 은(는) 과정 평균을 웃돕니다.',
                        "basis": f'{high["latest"]} vs 과정 평균 {high["courseAverage"]}'})
    if dropped:
        out.append({"text": f'이번 회차에 없는 평가 항목이 있어 비교에서 뺐습니다 — '
                            f'{", ".join(dropped)}.',
                    "basis": "회차 간 평가 영역 구성이 다름"})
    return out


# ══════════════════════════════════════════════════════════════
# 단발특강 — 단일 회차 분포. 추이는 없다.
# ══════════════════════════════════════════════════════════════
def _single(cards: List[Card]) -> dict:
    vals = [v for v in (_avg_of(c) for c in cards) if v is not None]
    areas = _area_averages(cards)
    ranked = sorted(areas.items(), key=lambda kv: kv[1])
    insights = []
    if ranked:
        insights.append({"text": f'가장 낮은 항목은 "{ranked[0][0]}" 입니다.',
                         "basis": f"평균 {ranked[0][1]}"})
        insights.append({"text": f'가장 높은 항목은 "{ranked[-1][0]}" 입니다.',
                         "basis": f"평균 {ranked[-1][1]}"})
    return {
        "kind": "단발특강",
        "average": round(sum(vals) / len(vals), 2) if vals else None,
        "distribution": _histogram(vals),
        "areas": [{"area": a, "average": v} for a, v in ranked],
        "trend": None,                       # 단발은 비교 대상이 없다
        "insights": insights,
    }


# ══════════════════════════════════════════════════════════════
# 진단서베이 — 관계별 결과 + 시행 회차 추이
# ══════════════════════════════════════════════════════════════
def _diagnosis(cards: List[Card]) -> dict:
    waves: Dict[str, List[Card]] = {}
    for c in cards:
        waves.setdefault(_wave_of(c), []).append(c)
    order = sorted(waves, key=_round_key)

    rows: Dict[str, Dict[str, float]] = {}
    for w in order:
        for rel, v in _relation_averages(waves[w]).items():
            rows.setdefault(rel, {})[w] = v

    by_relation = []
    for rel, per_wave in rows.items():
        seq = [per_wave.get(w) for w in order]
        got = [x for x in seq if x is not None]
        delta = round(got[-1] - got[0], 2) if len(got) >= 2 else None
        by_relation.append({"relation": rel, "byWave": dict(zip(order, seq)),
                            "latest": got[-1] if got else None, "delta": delta})

    insights = []
    for r in by_relation:
        if r["delta"] is None:
            continue
        word = "올랐습니다" if r["delta"] > 0 else (
            "내렸습니다" if r["delta"] < 0 else "유지되었습니다")
        insights.append({
            "text": f'{r["relation"]} 응답 평균이 {abs(r["delta"])}점 {word}.',
            "basis": " → ".join(f"{w} {v}" for w, v in r["byWave"].items()
                                if v is not None)})
    if len(order) < 2:
        insights.append({"text": "다음 시행부터 성장 비교가 시작됩니다.",
                         "basis": f"시행 {len(order)}회"})

    return {"kind": "진단서베이", "waves": order,
            "byRelation": by_relation, "insights": insights}


# ══════════════════════════════════════════════════════════════
def _cards_of(db: Session, course_id: str) -> List[Card]:
    """이 과정에 속한 카드. commit 때 카드 문맥에 심어 둔 _course_id 로 찾는다."""
    return [c for c in db.query(Card).all()
            if (c.card_json.get("context") or {}).get("_course_id") == course_id]


def _guess_type(cards: List[Card]) -> str:
    for c in cards:
        if c.source_type:
            return c.source_type
    return "단발특강"


def _avg_of(card: Card) -> Optional[float]:
    v = (card.card_json.get("score_summary") or {}).get("average")
    return float(v) if v is not None else None


def _area_averages(cards: List[Card]) -> Dict[str, float]:
    bucket: Dict[str, List[float]] = {}
    for c in cards:
        for it in (c.card_json.get("scores") or []):
            if it.get("score") is None:
                continue
            key = it.get("area_name") or it.get("question_id")
            if key:
                bucket.setdefault(str(key), []).append(float(it["score"]))
    return {k: round(sum(v) / len(v), 2) for k, v in bucket.items() if v}


def _relation_averages(cards: List[Card]) -> Dict[str, float]:
    """관계별 평균. 역량 항목마다 붙어 있는 by_relation 을 모은다 (R-10).

    R-10 이 이미 익명성 기준(n>=3, 상사는 비익명)으로 걸러 놓은 것만
    항목에 남아 있다. 여기서 다시 판단하지 않는다 — 판단이 두 곳에 있으면
    한쪽만 고쳤을 때 조용히 어긋난다.
    """
    bucket: Dict[str, List[float]] = {}
    for c in cards:
        for item in (c.card_json.get("scores") or []):
            for rel, val in (item.get("by_relation") or {}).items():
                if val is not None:
                    bucket.setdefault(str(rel), []).append(float(val))
    return {k: round(sum(v) / len(v), 2) for k, v in bucket.items() if v}


def _round_labels(cards: List[Card]) -> List[str]:
    return sorted({c.round_label for c in cards if c.round_label}, key=_round_key)


def _wave_of(card: Card) -> str:
    ctx = card.card_json.get("context") or {}
    w = ctx.get("_wave")
    return f"{w}회" if w else (card.round_label or card.session_date or "시행 미상")


def _round_key(label: str):
    """'2차수' < '10차수' 로 정렬한다. 숫자가 없으면 뒤로 민다."""
    digits = "".join(ch for ch in str(label) if ch.isdigit())
    return (0, int(digits)) if digits else (1, str(label))


def _histogram(vals: List[float], bins: int = 5) -> List[dict]:
    if not vals:
        return []
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return [{"from": lo, "to": hi, "count": len(vals)}]
    width = (hi - lo) / bins
    out = []
    for i in range(bins):
        a, b = lo + width * i, lo + width * (i + 1)
        last = i == bins - 1
        n = sum(1 for v in vals if (a <= v <= b) if last or v < b)
        out.append({"from": round(a, 2), "to": round(b, 2), "count": n})
    return out
