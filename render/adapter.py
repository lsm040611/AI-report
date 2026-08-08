"""정규화 카드(계약 v0.5) → 표현 카드(리포트 템플릿 스키마).

두 폴더를 잇는 계층. 백엔드의 카드는 **데이터**고 렌더러의 카드는 **표현**이라
모양이 다르다. 여기서 그 간극을 메운다.

번역 규칙 세 가지:
1. 점수 데이터는 그대로 옮긴다.
2. "가장 잘한 것 / 키울 것" 같은 판단은 여기서 만든다 — 백엔드 어디에도 없는,
   순수한 표현 계층의 결정이다. 근거는 항상 점수 비교뿐이고 문장을 지어내지 않는다.
3. 문장이 필요한 자리(주제 요약·실천 제안)는 R-16 을 통과한 생성물만 쓴다.
   생성물이 없으면 그 섹션은 만들지 않는다 — 렌더러가 빈 섹션을 지운다.
"""
from __future__ import annotations

import html as _html
import re
from typing import Dict, List, Optional

from pipeline.rules.base import quote_allowed

from .radar import legend_html, radar_svg
from .template import EMPHASIS, runs_html

# --------------------------------------------------------------------------
EYEBROW = {
    "누적교육": "개인 피드백 리포트",
    "단발특강": "특강 피드백 리포트",
    "진단서베이": "리더십 개인 진단 리포트",
}
SCORE_LABEL = {
    "누적교육": "이번 회차 평균",
    "단발특강": "오늘 평균",
    "진단서베이": "전체 평균",
}
STRENGTH_TITLE = {
    "누적교육": "이번에 통한 것",
    "단발특강": "오늘 잘하신 점",
}
TODO_HEAD = {
    "누적교육": ("하나만 가져가신다면", "다음 회차까지"),
    "단발특강": ("오늘 중 하나만 가져가신다면", "다음 특강 전까지"),
    "진단서베이": ("가장 많이 나온 목소리로부터", "앞으로 90일"),
}
PROGRAM_KEYS = ("과정명", "특강명", "진단명", "프로그램", "교육명")


# ══════════════════════════════════════════════════════════════
# 문장 ↔ 근거 색인
# ══════════════════════════════════════════════════════════════
class Sentences:
    """AI 가 만든 문장마다 id 를 붙이고 그 근거를 나란히 적어 둔다.

    UI 검수 화면의 R-16 근거 대조가 문장을 클릭하면 이 표를 조회한다
    (통합 명세 §2-③). 지금까지 근거는 카드 안에만 있었고 화면에서
    문장과 이어 볼 방법이 없었다 — 그 다리를 여기서 놓는다.
    """

    def __init__(self, card: dict):
        prov = card.get("provenance") or {}
        row = prov.get("row") or prov.get("rows")
        parts = [prov.get("file"), prov.get("sheet"),
                 f"{row}행" if row else None]
        self.ref = " › ".join(str(p) for p in parts if p)
        self.items: List[dict] = []

    def add(self, text: str, rule_id: str = "", quote: str = "") -> str:
        sid = f"s{len(self.items) + 1}"
        self.items.append({
            "sentenceId": sid,
            "aiText": re.sub(r"<[^>]+>", "", text or "").strip(),
            "ruleId": rule_id,
            "sourceRef": self.ref,
            "sourceText": f'"{quote}"' if quote else "",
        })
        return sid


def _quotes_of(g: dict) -> List[str]:
    return [(e.get("quote") or "").strip() for e in (g.get("evidence") or [])]


# ══════════════════════════════════════════════════════════════
def to_presentation_card(card: dict,
                         growth: Optional[dict] = None,
                         repeat: Optional[List[dict]] = None,
                         peer_avg: Optional[Dict[str, float]] = None,
                         peer_label: str = "차수 평균",
                         peer_n: Optional[int] = None,
                         team: str = "HRD 교육운영팀",
                         contact: str = "") -> dict:
    stype = (card.get("source_type") or {}).get("type") or "unknown"
    person = card.get("person") or {}
    context = card.get("context") or {}
    scale = _scale(card)
    sid = Sentences(card)

    sections: List[dict] = []
    sections.append(_glance(card, stype, scale, peer_n, growth))

    if growth and growth.get("status") == "compared":
        sections.append(_growth(growth, repeat, scale))

    if card.get("direction") == "aggregated_responses":
        sections.append(_relation(card, scale))
    else:
        sections.append(_scores(card, scale, peer_avg or {}, peer_label,
                                growth, stype))

    sections.extend(_narratives(card, stype, sid))
    sections.extend(_themes(card, sid))
    sections.append(_fixnotes(card))

    # 암기 문장 카드가 있으면 그것이 곧 실천 항목이다 — 같은 말을 두 번 쓰지 않는다
    memorize = _memorize(card, stype, repeat, sid)
    sections.append(memorize or _todo(card, stype, sid))
    # 범례는 강사가 색·굵기로 표시한 것이 실제로 있을 때만 쓸모가 있다.
    # 진단서베이는 강조 표기가 아예 없어서 '고칠 표현 / 권장 표현 / 핵심 개념'
    # 설명만 덩그러니 남는다 — 없는 표기를 설명하는 꼴이라 뺀다.
    delta = bool(growth and growth.get("status") == "compared")
    marks = bool(_emphasis_kinds(card))
    if marks or delta:
        sections.append({"kind": "legend", "compact": True,
                         "showMarks": marks, "showDelta": delta})

    lines = [f'이 리포트는 {person.get("name")} 님 본인에게만 발송되었습니다.']
    if contact:
        lines.append(f"문의: {team} · {contact}")
    if _uses_mock(card):
        lines.append("일부 문장은 목(mock) 생성기로 작성되었습니다 — "
                     "생성 API 연결 시 실제 생성물로 대체됩니다.")

    return {
        "person": {"name": person.get("name"), "alias": person.get("alias")},
        "meta": {
            "eyebrow": EYEBROW.get(stype, "개인 피드백 리포트"),
            "program": _program(context),
            "items": _meta_items(context),
        },
        "sections": [s for s in sections if s],
        # 문장 id ↔ 근거. 화면에는 나오지 않고 GET /reports/{id}/evidence 가 쓴다.
        "evidence": sid.items,
        "footer": {"team": team, "lines": lines},
    }


# ══════════════════════════════════════════════════════════════
# 01 한눈에
# ══════════════════════════════════════════════════════════════
def _glance(card: dict, stype: str, scale: dict, peer_n: Optional[int],
            growth: Optional[dict] = None) -> dict:
    summary = card.get("score_summary") or {}
    areas = _areas(card)
    rows: List[dict] = []

    if areas:
        best = max(areas, key=lambda a: a["score"])
        rows.append({"tone": "good",
                     "chip": "✓ 가장 높음" if stype == "진단서베이" else "✓ 가장 잘한 것",
                     "name": best["label"],
                     "value": _num(best["score"], scale),
                     "valueSuffix": f'/{_num(scale["max"], scale)}'})

        if len(areas) > 1:
            low = min(areas, key=lambda a: a["score"])
            if low["label"] != best["label"]:
                avg = summary.get("average")
                why = (f'전체 평균보다 {abs(round(avg - low["score"], 1))}점 낮습니다.'
                       if avg is not None else None)
                rows.append({"tone": "grow",
                             "chip": "↗ 가장 낮음" if stype == "진단서베이" else "↗ 키울 것",
                             "name": low["label"], "why": why,
                             "value": _num(low["score"], scale),
                             "valueSuffix": f'/{_num(scale["max"], scale)}'})

    gap = _perception_gap(card)
    if gap:
        rows.append(gap)

    lines = []
    if areas:
        lines.append(f"{len(areas)}개 {'문항' if card.get('direction') == 'aggregated_responses' else '역량'} 평균")
    agg = card.get("aggregation") or {}
    if agg.get("n_respondents"):
        lines.append(f'응답 <b>{agg["n_respondents"]}건</b>')
    elif peer_n:
        lines.append(f"같은 차시 참가자 <b>{peer_n}명</b>")

    score_block = _headline(summary, scale, stype, lines, growth)

    notes = []
    if stype == "진단서베이":
        notes.append({"html": "이 결과는 본인의 성찰과 개발 계획을 위한 자료이며, "
                              "인사 평가·보상에 사용되지 않습니다."})
    if (agg.get("anonymity") or {}).get("aggregate_only"):
        rels = ", ".join((agg["anonymity"]["aggregate_only"]))
        notes.append({"html": f"{rels} 응답은 ‘전체’ 점수에 포함되어 있습니다."})

    title = {"누적교육": "이번 회차, 한눈에", "단발특강": "오늘 특강, 한눈에",
             "진단서베이": "진단 결과, 한눈에"}.get(stype, "한눈에")
    return {"kind": "glance", "title": title, "score": score_block,
            "rows": rows, "notes": notes}


def _headline(summary: dict, scale: dict, stype: str, lines: List[str],
              growth: Optional[dict]) -> Optional[dict]:
    """머리 패널에 무엇을 크게 띄울지 정한다.

    평균을 크게 띄우면 '3.7 → 3.7, 변화 없음'으로 읽힌다. 실제로는 한 역량이
    0.5 올랐고 두 역량이 새로 추가됐는데도 그렇다. 평균 하나로 뭉뚱그리면 개선이
    묻히므로, **비교할 이전 회차가 있고 실제로 오른 역량이 있으면 그 변화를**
    크게 띄우고 평균은 아래로 내린다.
    """
    avg = summary.get("average")
    if avg is None:
        return None

    avg_txt = f"{avg:.1f}"
    best = None
    if growth and growth.get("status") == "compared":
        ups = [d for d in growth.get("deltas", []) if (d.get("delta") or 0) > 0]
        if ups:
            best = max(ups, key=lambda d: d["delta"])

    if not best:
        return {"label": SCORE_LABEL.get(stype, "평균"), "value": avg_txt,
                "max": _num(scale["max"], scale, force_decimal=scale["max"] <= 5),
                "lines": lines}

    prev, curr = best.get("previous"), best.get("current")
    detail = [f'<b>{_esc(best.get("label"))}</b> {_num(prev, scale)} → {_num(curr, scale)}']
    detail.append(f'{SCORE_LABEL.get(stype, "평균")} <b>{avg_txt}</b>'
                  + (f' (지난 회차 {growth["prev_average"]:.1f})'
                     if growth.get("prev_average") is not None else ""))
    if not growth.get("average_comparable"):
        detail.append("역량 구성이 달라 평균 비교는 참고용입니다")

    return {"label": "가장 크게 오른 역량", "value": f'+{best["delta"]:.1f}',
            "max": None, "lines": detail}


def _perception_gap(card: dict) -> Optional[dict]:
    """관계군 간 인식 차이가 가장 큰 항목. 진단서베이에만 있다."""
    if card.get("direction") != "aggregated_responses":
        return None
    best = None
    for item in card.get("scores", []):
        rel = item.get("by_relation") or {}
        if len(rel) < 2 or item.get("score") is None:
            continue
        spread = round(max(rel.values()) - min(rel.values()), 2)
        # 차이가 같다면 점수가 낮은 쪽을 고른다 — 높은 역량의 인식 차이보다
        # 낮은 역량의 인식 차이가 받는 사람에게 더 쓸모 있다.
        key = (spread, -float(item["score"]))
        if best is None or key > best[0]:
            best = (key, item, rel)
    if not best or best[0][0] < 0.5:
        return None
    _, item, rel = best
    spread = round(max(rel.values()) - min(rel.values()), 2)
    hi = max(rel, key=rel.get)
    lo = min(rel, key=rel.get)
    return {"tone": "gap", "chip": "△ 인식 차이",
            "name": item.get("area_name") or item.get("question_id"),
            "why": f"{hi} {rel[hi]:.1f} / {lo} {rel[lo]:.1f} — "
                   f"{lo} 쪽에서 더 낮게 보고 있습니다.",
            "value": f"{spread:.1f}"}


# ══════════════════════════════════════════════════════════════
# 성장 비교 (R-14)
# ══════════════════════════════════════════════════════════════
def _growth(growth: dict, repeat: Optional[List[dict]], scale: dict) -> dict:
    rows = []
    for d in growth.get("deltas", []):
        rows.append({"name": d.get("label") or d.get("area"),
                     "prev": d.get("previous"), "curr": d.get("current"),
                     "delta": d.get("delta")})
    for n in growth.get("new_areas", []):
        rows.append({"name": n.get("label") or n.get("area"),
                     "curr": n.get("current"), "isNew": True})

    notes = []
    if not growth.get("average_comparable"):
        notes.append({"html": "두 회차의 평가 역량 구성이 달라, 전체 평균 비교는 표시하지 "
                              "않았습니다. 위 표는 <b>두 회차에 모두 있던 역량</b> 기준입니다."})
    elif growth.get("prev_average") is not None and growth.get("curr_average") is not None:
        p, c = growth["prev_average"], growth["curr_average"]
        notes.append({"html": f"전체 평균은 <b>{p:.1f} → {c:.1f} ({c - p:+.1f})</b> 입니다."})

    followup = None
    hits = repeat or []
    if hits:
        ev = [{"text": "지난 회차에 교정한 표현이 이번 회차 강점에 다시 나타났습니다: "}]
        ev.append({"text": hits[0]["expression"], "emphasis": "key_concept"})
        followup = {"title": "✓ 지난 과제, 잘 이어가고 계십니다",
                    "prevLabel": "지난 회차 과제",
                    "prevAction": hits[0].get("prev_action") or hits[0]["expression"],
                    "evidence": ev}

    return {"kind": "growth", "title": "지난 회차 이후 달라진 점", "tint": True,
            "followup": followup,
            "tableTitle": "두 회차에 공통으로 있던 역량",
            "columns": ["역량", "변화", "지난 회차", "이번 회차", "증감"],
            "rows": rows, "notes": notes}


# ══════════════════════════════════════════════════════════════
# 역량별 결과 (척도 트랙)
# ══════════════════════════════════════════════════════════════
def _scores(card: dict, scale: dict, peer_avg: Dict[str, float], peer_label: str,
            growth: Optional[dict] = None, stype: str = "") -> dict:
    items = []
    spot_key, spot_gap = None, 0.0

    for it in card.get("scores", []):
        if it.get("score") is None:
            continue
        name = it.get("area_name") or it.get("question_id") or ""
        avg = peer_avg.get(name)
        if avg is not None and (it["score"] - avg) < spot_gap:
            spot_key, spot_gap = name, it["score"] - avg
        items.append({
            "name": name,
            "desc": it.get("definition") or (it.get("canonical_area") or ""),
            "value": _num(it["score"], scale),
            "groupAvg": round(avg, 2) if avg is not None else None,
            "groupAvgLabel": f"{peer_label} {avg:.1f}" if avg is not None else None,
            "_key": name,
        })

    for i in items:
        if spot_key and i["_key"] == spot_key:
            i["spot"] = True
            i["memo"] = f"{peer_label}과의 차이가 가장 큰 항목입니다."
        i.pop("_key", None)

    lead = (f"붉은 점이 내 점수, 회색 선이 {peer_label}입니다 · "
            f'{_num(scale["min"], scale)}~{_num(scale["max"], scale)}점') if peer_avg else \
           f'붉은 점이 내 점수입니다 · {_num(scale["min"], scale)}~{_num(scale["max"], scale)}점'

    section = {"kind": "scores", "title": "역량별 결과", "lead": lead, "tint": True,
               "scaleMin": scale["min"], "scaleMax": scale["max"], "items": items,
               # 척도가 1~10인 과정은 % 를 함께 적어 다른 과정과 나란히 읽게 한다
               "showPercent": stype == "단발특강" or scale.get("max") not in (5, 5.0)}
    section.update(_radar(card, scale, growth))
    return section


def _radar(card: dict, scale: dict, growth: Optional[dict]) -> dict:
    """레이더 차트. 축 순서는 원본 엑셀의 헤더 순서를 그대로 따른다."""
    scored = [s for s in card.get("scores", [])
              if (s.get("area_name") or s.get("question_id"))]
    if len(scored) < 3:
        return {}

    prev_by_label, new_labels = {}, set()
    if growth and growth.get("status") == "compared":
        for d in growth.get("deltas", []):
            prev_by_label[d.get("label")] = (d.get("previous"), d.get("delta"))
        new_labels = {n.get("label") for n in growth.get("new_areas", [])}

    axes, curr_vals, prev_vals = [], [], []
    for s in scored:
        label = s.get("area_name") or s.get("question_id")
        prev, delta = prev_by_label.get(label, (None, None))
        axes.append({"name": label, "delta": delta,
                     "missing": s.get("score") is None,
                     "is_new": label in new_labels})
        curr_vals.append(s.get("score"))
        prev_vals.append(prev)

    series = [{"label": "이번 회차", "values": curr_vals,
               "color": "var(--sk-red)", "fill": 0.10}]
    if any(v is not None for v in prev_vals):
        series.insert(0, {"label": "지난 회차", "values": prev_vals,
                          "color": "var(--faint)", "dash": True})

    svg = radar_svg(axes, series, scale)
    if not svg:
        return {}
    note = ("바깥으로 갈수록 높은 점수입니다. 지난 회차와 겹쳐 보면 어느 축이 넓어졌는지 보입니다."
            if len(series) > 1 else "바깥으로 갈수록 높은 점수입니다.")
    return {"radar": svg, "radarLegend": legend_html(series), "radarNote": note}


def _memorize(card: dict, stype: str, repeat: Optional[List[dict]],
              sid: Sentences) -> dict:
    """암기 문장 카드 — AI 코칭 파트."""
    g = next((x for x in _generated(card, "R-17")
              if x.get("task") == "curate_memorize" and (x.get("text") or "").strip()),
             None)
    if not g:
        return {}
    extra = g.get("extra") or {}
    kinds = _emphasis_kinds(card)

    parts = []
    for p in (extra.get("parts") or []):
        quote = (p.get("quote") or "").strip()
        if not quote:
            continue
        # 표현의 성격은 강사가 칠한 서식에서 온다 — 생성물이 정하는 게 아니다
        parts.append({"kind": kinds.get(quote, "fix"), "quote": quote,
                      "note": (p.get("note") or "").strip()})

    badge = None
    if repeat:
        badge = f'지난 회차 암기 문장 반영 확인됨 — "{repeat[0]["expression"][:40]}"'

    head = ("MEMORIZE BY NEXT SESSION" if stype == "누적교육"
            else "APPLY IN YOUR NEXT TALK")
    quotes = _quotes_of(g)
    return {"kind": "memorize", "title": "다음까지 외울 문장", "tint": True,
            "head": head, "badge": badge,
            "sid": sid.add(g["text"], "R-17", quotes[0] if quotes else ""),
            "sentence": _highlight(_safe(g["text"]), parts), "parts": parts,
            "closingHtml": _safe(extra.get("closing") or "")}


EMPHASIS_CLASS = {"issue_expression": "issue",
                  "corrected_expression": "fix",
                  "key_concept": "key"}


def _emphasis_kinds(card: dict) -> Dict[str, str]:
    """강사가 칠한 서식 → 표현별 성격. 표기 그대로가 키다."""
    out: Dict[str, str] = {}
    for nar in card.get("narratives", []):
        for r in (nar.get("runs") or []):
            kind = EMPHASIS_CLASS.get(r.get("emphasis"))
            text = (r.get("text") or "").strip()
            if kind and text:
                out.setdefault(text, kind)
    return out


def _highlight(sentence: str, parts: List[dict]) -> str:
    """암기 문장 안에서 각 조각이 어느 교정 표현인지 색으로 잇는다.

    문장 전체를 한 덩어리로 두면 '어디가 강사 표현인지'가 사라진다.
    클래스는 리포트 범례와 같은 것을 쓴다 — 색은 스타일시트가 정한다.
    """
    for p in parts:
        q = _safe(p.get("quote") or "")
        if not q or q not in sentence:
            continue
        cls = EMPHASIS.get({"issue": "bad"}.get(p.get("kind"), p.get("kind")),
                           EMPHASIS["fix"])
        sentence = sentence.replace(q, f'<em class="{cls}">{q}</em>', 1)
    return sentence


# ══════════════════════════════════════════════════════════════
# 관계별 결과 (진단서베이)
# ══════════════════════════════════════════════════════════════
def _relation(card: dict, scale: dict) -> dict:
    agg = card.get("aggregation") or {}
    anon = agg.get("anonymity") or {}
    # 표시 가능한 관계군만 열로 넣는다 — 비공개 칸을 뚫어 두고 설명하지 않는다
    rels = [r for r in anon.get("separate", []) if r != "미상"]

    rows = []
    for it in card.get("scores", []):
        if it.get("score") is None:
            continue
        values = [{"value": _num(it["score"], scale), "tone": _tone(it["score"], scale)}]
        for r in rels:
            v = (it.get("by_relation") or {}).get(r)
            values.append({"value": _num(v, scale) if v is not None else "—",
                           "tone": _tone(v, scale) if v is not None else "mid"})
        rows.append({"name": it.get("area_name") or it.get("question_id"),
                     "desc": it.get("definition"), "values": values})

    if not rows:
        return {}

    rows.sort(key=lambda r: float(str(r["values"][0]["value"])), reverse=True)

    footnote = None
    if anon.get("aggregate_only"):
        counts = agg.get("by_relation") or {}
        parts = [f'{r} 응답 {counts[r]}건' if counts.get(r) else f"{r} 응답"
                 for r in anon["aggregate_only"]]
        footnote = f'{", ".join(parts)}은 ‘전체’ 점수에 포함되어 있습니다.'

    return {"kind": "relation", "title": "누가 어떻게 보고 있는가",
            "lead": "같은 사람도 보는 위치에 따라 다르게 보입니다. "
                    "차이가 큰 지점이 곧 여지가 있는 지점입니다.",
            "tint": True, "rowHeader": "역량", "columns": ["전체"] + rels,
            "rows": rows, "footnote": footnote}


def _tone(v, scale: dict) -> str:
    if v is None:
        return "mid"
    lo, hi = scale["min"], scale["max"]
    r = (float(v) - lo) / (hi - lo) if hi > lo else 0.5
    return "hi" if r >= 0.7 else ("mid" if r >= 0.45 else "lo")


# ══════════════════════════════════════════════════════════════
# 강사 서술 원문
# ══════════════════════════════════════════════════════════════
CURATED_TITLE = "이 지점을 이렇게 보면 좋습니다"


def _narratives(card: dict, stype: str, sid: Sentences) -> List[dict]:
    """강사 서술은 원문 그대로 싣고, 개선점에는 코멘트를 덧붙인다.

    원문이 본문이다 — 강사가 무엇을 봤는지는 그 사람의 문장으로 전달되어야 한다.
    다만 개선점은 메모체라 무엇을 하라는 것인지 흐릿할 때가 있어, 그 아래에
    일정한 말투로 정리한 코멘트를 한 문단 덧붙인다(R-17).
    """
    if not quote_allowed(card):
        return []            # R-11: 원문 인용 경로 차단

    curated = _gap_comment(card)
    out: List[dict] = []
    commented = False

    for nar in card.get("narratives", []):
        if nar.get("exposure_policy") == "summarize_only":
            continue
        runs = [r for r in (nar.get("runs") or []) if (r.get("text") or "").strip()]
        if not runs:
            continue
        role = nar.get("role")

        if role == "strength":
            section = {"kind": "narrative", "tone": None,
                       "title": STRENGTH_TITLE.get(stype, "잘하신 점"),
                       "mergeWithPrev": bool(out)}
            _fill_body(section, nar, runs, sid)
            out.append(section)
            continue

        if role != "gap":
            continue         # next_action 은 실천 체크리스트로 간다

        section = {"kind": "narrative", "title": "함께 살펴보면 좋을 점",
                   "tone": "gap", "mergeWithPrev": bool(out)}
        _fill_body(section, nar, runs, sid)
        if curated and not commented:
            # 개선점 열이 여럿이면 코멘트는 첫 칸에만 붙인다
            quotes = _quotes_of(curated)
            section.setdefault("notes", []).insert(
                0, {"accent": True, "title": CURATED_TITLE,
                    "sid": sid.add(curated["text"], "R-17",
                                   quotes[0] if quotes else _plain_text(runs)),
                    "html": _safe(curated["text"])})
            commented = True
        out.append(section)
    return out


def _fill_body(section: dict, nar: dict, runs: List[dict],
               sid: Sentences) -> None:
    """본문을 채운다. 영어 코멘트면 한국어 번역을 본문으로 올린다.

    계약 R-13: 원문 보존 + 번역 병기. 받는 사람이 먼저 읽어야 하는 것은
    한국어이므로 번역을 본문에 두고, 강사가 쓴 영어 원문은 아래에 남긴다.
    번역이 아직 없으면 원문을 그대로 싣는다.
    """
    translated = (nar.get("translation_ko") or "").strip()
    if nar.get("language") == "en" and translated:
        # 번역문은 AI 가 만든 문장이므로 근거(=영어 원문)와 이어 둔다
        section["sid"] = sid.add(translated, "R-13", _plain_text(runs))
        section["html"] = _safe(translated)
        section["notes"] = [{"title": "강사 코멘트 원문", "html": runs_html(runs)}]
    else:
        section["runs"] = runs


def _gap_comment(card: dict) -> Optional[dict]:
    for g in _generated(card, "R-17"):
        if g.get("task") == "curate_gap_comment" and (g.get("text") or "").strip():
            return g
    return None


def _plain_text(runs: List[dict]) -> str:
    return "".join((r.get("text") or "") for r in runs).strip()


# ══════════════════════════════════════════════════════════════
# 공통 주제 (R-11 생성물)
# ══════════════════════════════════════════════════════════════
THEME_TITLE = {
    "strength": ("함께 일하는 분들이 짚은 강점", None),
    "gap": ("함께 일하는 분들이 바라는 변화", "비판이 아니라 요청으로 읽어 주십시오."),
    "change_request": ("함께 일하는 분들이 바라는 변화", "비판이 아니라 요청으로 읽어 주십시오."),
}


def _themes(card: dict, sid: Sentences) -> List[dict]:
    out = []
    for g in _generated(card, "R-11"):
        quotes = _quotes_of(g)
        items = []
        for i, line in enumerate(_lines(g.get("text"))):
            items.append({"html": _safe(line),
                          "sid": sid.add(line, "R-11",
                                         quotes[i] if i < len(quotes) else "")})
        if not items:
            continue
        title, lead = THEME_TITLE.get(g.get("role") or "",
                                      (g.get("label") or "응답에서 반복된 주제", None))
        out.append({"kind": "themes", "title": title, "lead": lead, "items": items})
    return out


# ══════════════════════════════════════════════════════════════
# 표현 교정 노트 — 강사가 쓴 X → Y 쌍을 회차와 무관하게 모은다
# ══════════════════════════════════════════════════════════════
def _fixnotes(card: dict) -> dict:
    if not quote_allowed(card):
        return {}
    cards = []
    for nar in card.get("narratives", []):
        runs = nar.get("runs") or []
        plain = " ".join((r.get("text") or "") for r in runs
                         if not r.get("emphasis")).strip()
        plain = re.sub(r"\s+", " ", plain)

        for i, r in enumerate(runs):
            if r.get("emphasis") != "issue_expression":
                continue
            fix = next((runs[j] for j in range(i + 1, min(i + 4, len(runs)))
                        if runs[j].get("emphasis") == "corrected_expression"), None)
            if not fix:
                continue
            cards.append({
                "left": (r.get("text") or "").strip(),
                "right": (fix.get("text") or "").strip(),
                "why": plain[:150].strip(" —→-") or None,
            })
    if not cards:
        return {}
    return {"kind": "fixnotes", "title": "나의 표현 교정 노트",
            "lead": "지금까지 교정받은 표현을 모았습니다. 다음 준비 전에 이 장만 다시 보셔도 됩니다.",
            "cards": cards}


# ══════════════════════════════════════════════════════════════
# 실천 체크리스트 (R-17 생성물)
# ══════════════════════════════════════════════════════════════
def _todo(card: dict, stype: str, sid: Sentences) -> dict:
    """실천 체크리스트. 항목 아래에 근거가 된 강사 코멘트를 함께 보여 준다.

    R-16이 요구하는 '근거 연결'을 담당자 화면에만 두지 않고 리포트에도 드러낸다.
    받는 사람이 "이건 어디서 나온 말이지?"를 되묻지 않아도 되게 하기 위해서다.

    **단, 진단서베이에서는 근거를 싣지 않는다.** 거기서 근거는 강사가 아니라
    동료가 쓴 문장이고, 리더에게 그대로 보여 주면 누가 썼는지 짚인다.
    근거는 검수용으로 카드에만 남는다.
    """
    show_quote = quote_allowed(card) and card.get("direction") != "aggregated_responses"

    items = []
    for g in _generated(card, "R-17"):
        if g.get("task") == "curate_gap_comment":
            continue                    # 그쪽은 개선점 문단이라 체크리스트가 아니다
        lines = _lines(g.get("text"))
        quotes = _quotes_of(g)
        for i, line in enumerate(lines):
            quote = quotes[i] if i < len(quotes) else ""
            item = {"html": _safe(line), "sid": sid.add(line, "R-17", quote)}
            # 항목 문장이 원문을 거의 그대로 옮긴 경우엔 같은 말을 두 번 쓰지 않는다
            if show_quote and quote and _norm(quote)[:16] not in _norm(line):
                item["sub"] = f"강사 코멘트: {quote[:90]}"
            items.append(item)
    if not items:
        return {}
    head, when = TODO_HEAD.get(stype, ("하나만 가져가신다면", None))
    return {"kind": "todo", "title": "다음까지 해 볼 것", "tint": True,
            "head": head, "when": when, "items": items[:4]}


def _norm(s: str) -> str:
    return re.sub(r"[\s<>/b.,·\"'“”‘’]", "", s or "")


# ══════════════════════════════════════════════════════════════
# 유틸
# ══════════════════════════════════════════════════════════════
def _scale(card: dict) -> dict:
    s = (card.get("score_summary") or {}).get("scale")
    if not s:
        s = next((x.get("scale") for x in card.get("scores", []) if x.get("scale")), None)
    return dict(s or {"min": 1, "max": 5, "step": 1})


def _areas(card: dict) -> List[dict]:
    out = []
    for it in card.get("scores", []):
        if it.get("score") is None:
            continue
        out.append({"label": it.get("area_name") or it.get("question_id") or "",
                    "score": float(it["score"])})
    return out


def _num(v, scale: dict, force_decimal: bool = False):
    """척도 성격에 맞춰 4.0 / 9 를 구분해 표기한다."""
    if v is None:
        return None
    f = float(v)
    integral = abs(f - round(f)) < 1e-9
    if integral and scale.get("step", 1) >= 1 and not force_decimal:
        return str(int(round(f)))
    return f"{f:.1f}"


def _program(context: dict) -> Optional[str]:
    for key in PROGRAM_KEYS:
        for k, v in context.items():
            if key in str(k):
                return str(v)
    return None


def _meta_items(context: dict, limit: int = 5) -> List[dict]:
    prog = _program(context)
    items = []
    for k, v in context.items():
        if v is None or str(v).strip() == "":
            continue
        if str(v) == prog:
            continue
        items.append({"k": str(k), "v": str(v)})
        if len(items) >= limit:
            break
    return items


def _generated(card: dict, rule_id: str) -> List[dict]:
    return [g for g in (card.get("generated") or []) if g.get("rule_id") == rule_id]


def _uses_mock(card: dict) -> bool:
    return any((g.get("engine") or "") == "mock" for g in (card.get("generated") or []))


def _lines(text: Optional[str]) -> List[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


_ALLOWED = re.compile(r"&lt;(/?)b&gt;")


def _esc(s: str) -> str:
    """안내 박스처럼 HTML 을 그대로 받는 자리에 원문을 넣을 때."""
    return _html.escape(s or "", quote=False)


def _safe(s: str) -> str:
    """생성물에는 <b> 만 허용한다.

    원본 엑셀 셀 값이 생성물을 거쳐 HTML 로 흘러들 수 있으므로,
    전부 이스케이프한 뒤 <b> 만 되살린다.
    """
    return _ALLOWED.sub(r"<\1b>", _html.escape(s, quote=False))
