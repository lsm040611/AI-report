"""HRD 담당자용 대시보드.

개인 리포트가 "나는 어떤가"를 답한다면, 여기는 "우리 과정은 어떤가"를 답한다.
네 가지만 본다.

  1. 조직 성장 지표 — 차수별 평균과 강사별 성장폭
  2. 영역별 히트맵 — 어느 역량이 더디게 개선되는가 (다음 특강 주제의 근거)
  3. 데이터 신뢰도 알림 — 원본 평균과 재계산이 어긋난 건 (R-03)
  4. 비정규 참가자 — 발송 보류 대상 일괄 확인·승인 (R-07·R-15)

차트 설계
  · 모든 차트가 **단일 계열**이다. 색으로 항목을 구분하지 않으므로 색각 이상에서도
    읽는 데 지장이 없고, 범례가 필요 없다. 값은 전부 숫자로도 함께 적는다.
  · 히트맵은 한 색의 명도 단계(순차)만 쓴다. 무지개 배색을 쓰지 않는다.
  · 셀 안 숫자는 단계별로 먹색/흰색을 골라 대비 4.5 이상을 유지한다(계산해서 확인).
"""
from __future__ import annotations

import html as _html
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import Card
from pipeline.rules.base import HOLD, is_sendable, max_severity

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# 순차 램프 — 낮음 → 높음. 기존 리포트의 --up-soft ~ --up 사이 5단계.
RAMP = ["#E6F0EC", "#C4DED6", "#8FC0B4", "#4E9385", "#0B6151"]
RAMP_INK = ["#231D18", "#231D18", "#231D18", "#231D18", "#FFFFFF"]
BAR = "#0B6151"

EXCLUDED_STATUS = ("audit", "excluded")


class ApproveBody(BaseModel):
    card_ids: List[int]
    operator: str
    decision: str = "approve"          # approve | exclude


# ══════════════════════════════════════════════════════════════
@router.get("/data")
def data(course: Optional[str] = None, db: Session = Depends(get_db)):
    cards = db.query(Card).all()
    courses = sorted({c.course_name for c in cards if c.course_name})
    if course is None and courses:
        # 기본값은 이름순이 아니라 '가장 볼 것이 많은 과정' — 회차가 여럿인 쪽
        def weight(name):
            sub = [c for c in cards if c.course_name == name]
            return (len({c.round_label for c in sub if c.round_label}), len(sub))
        course = max(courses, key=weight)

    scope = [c for c in cards if c.course_name == course]
    counted = [c for c in scope if c.person_status not in EXCLUDED_STATUS]

    rounds = _round_order(counted)
    return {
        "courses": courses,
        "course": course,
        "rounds": _round_stats(counted, rounds),
        "instructors": _instructor_stats(counted, rounds),
        "areas": _area_stats(counted, rounds),
        "integrity": _integrity(scope),
        "holds": _holds(scope),
        "excluded": [c.person_name for c in scope
                     if c.person_status in EXCLUDED_STATUS],
    }


@router.post("/approve")
def approve(body: ApproveBody, db: Session = Depends(get_db)):
    """보류 대상 일괄 처리. 누가 언제 열었는지는 플래그에 남는다."""
    done = []
    for cid in body.card_ids:
        card = db.get(Card, cid)
        if not card:
            continue
        d = dict(card.card_json)
        flags = []
        for f in d.get("flags", []):
            f = dict(f)
            if f.get("severity") == HOLD and not f.get("resolved"):
                f.update({"resolved": True, "decision": body.decision,
                          "resolved_by": body.operator,
                          "memo": "대시보드에서 일괄 처리"})
            flags.append(f)
        d["flags"] = flags
        if body.decision == "exclude":
            person = dict(d.get("person", {}))
            person["status"] = "excluded"
            d["person"] = person
            card.person_status = "excluded"
        card.card_json = d
        card.max_severity = max_severity(d)
        done.append(cid)
    db.commit()
    return {"ok": True, "updated": done}


# ══════════════════════════════════════════════════════════════
def _avg(card: Card) -> Optional[float]:
    return ((card.card_json or {}).get("score_summary") or {}).get("average")


def _round_order(cards: List[Card]) -> List[str]:
    """차수를 날짜순으로 정렬한다. 라벨의 글자순이 아니라 실제 순서로."""
    first: Dict[str, str] = {}
    for c in cards:
        label = c.round_label or "단일 회차"
        d = c.session_date or ""
        if label not in first or d < first[label]:
            first[label] = d
    return [k for k, _ in sorted(first.items(), key=lambda kv: (kv[1], kv[0]))]


def _round_stats(cards: List[Card], rounds: List[str]) -> List[dict]:
    out = []
    for label in rounds:
        vals = [_avg(c) for c in cards if (c.round_label or "단일 회차") == label]
        vals = [v for v in vals if v is not None]
        out.append({"label": label, "n": len(vals),
                    "average": round(sum(vals) / len(vals), 2) if vals else None,
                    "date": min((c.session_date or "") for c in cards
                                if (c.round_label or "단일 회차") == label) or None})
    for i in range(1, len(out)):
        a, b = out[i - 1]["average"], out[i]["average"]
        out[i]["delta"] = round(b - a, 2) if (a is not None and b is not None) else None
    return out


def _instructor_stats(cards: List[Card], rounds: List[str]) -> List[dict]:
    """강사별 성장폭. 강사 이름은 원본 메타에서 그대로 가져온다."""
    by: Dict[str, Dict[str, List[float]]] = {}
    for c in cards:
        who = _instructor(c)
        if not who:
            continue
        v = _avg(c)
        if v is None:
            continue
        by.setdefault(who, {}).setdefault(c.round_label or "단일 회차", []).append(v)

    out = []
    for who, per in by.items():
        seq = [(r, round(sum(per[r]) / len(per[r]), 3)) for r in rounds if per.get(r)]
        if not seq:
            continue
        delta = round(seq[-1][1] - seq[0][1], 3) if len(seq) > 1 else None
        out.append({"name": who, "rounds": dict(seq), "delta": delta,
                    "first": seq[0][1], "last": seq[-1][1],
                    "n": sum(len(v) for v in per.values())})
    out.sort(key=lambda x: (x["delta"] is None, -(x["delta"] or 0)))
    return out


def _instructor(card: Card) -> Optional[str]:
    ctx = (card.card_json or {}).get("context") or {}
    for k, v in ctx.items():
        if "강사" in str(k) or "instructor" in str(k).lower():
            return str(v)
    return None


def _area_stats(cards: List[Card], rounds: List[str]) -> List[dict]:
    """영역별 평균. 표준 역량명이 있으면 그쪽으로 묶는다(회차마다 표기가 달라도 합쳐짐)."""
    order: List[str] = []
    by: Dict[str, Dict[str, List[float]]] = {}
    for c in cards:
        label = c.round_label or "단일 회차"
        for s in (c.card_json or {}).get("scores", []):
            if s.get("score") is None:
                continue
            key = s.get("canonical_area") or s.get("area_name")
            if not key:
                continue
            if key not in by:
                by[key] = {}
                order.append(key)
            by[key].setdefault(label, []).append(float(s["score"]))

    out = []
    for key in order:
        per = {r: round(sum(by[key][r]) / len(by[key][r]), 2)
               for r in rounds if by[key].get(r)}
        seen = [per[r] for r in rounds if r in per]
        out.append({"name": key, "by_round": per,
                    "delta": round(seen[-1] - seen[0], 2) if len(seen) > 1 else None,
                    "latest": seen[-1] if seen else None})
    return out


def _integrity(cards: List[Card]) -> List[dict]:
    """R-03: 원본 평균과 엔진 재계산이 어긋난 건."""
    out = []
    for c in cards:
        for f in (c.card_json or {}).get("flags", []):
            if f.get("code") != "average_mismatch":
                continue
            summary = (c.card_json or {}).get("score_summary") or {}
            out.append({"card_id": c.id, "name": c.person_name,
                        "round": c.round_label, "detail": f.get("detail"),
                        "original": summary.get("original_average"),
                        "recomputed": summary.get("average")})
    return out


def _holds(cards: List[Card]) -> List[dict]:
    out = []
    for c in cards:
        pending = [f for f in (c.card_json or {}).get("flags", [])
                   if f.get("severity") == HOLD and not f.get("resolved")]
        auto = [f for f in (c.card_json or {}).get("flags", [])
                if f.get("severity") == HOLD and f.get("resolved_by") == "자동 모드"]
        if not pending and not auto and c.person_status not in EXCLUDED_STATUS:
            continue
        ok, reason = is_sendable(c.card_json)
        out.append({
            "card_id": c.id, "name": c.person_name, "status": c.person_status,
            "round": c.round_label, "sendable": ok, "blocked_by": reason or None,
            "reasons": [f.get("action") or f.get("detail") or f.get("code")
                        for f in (pending or auto)],
            "auto_approved": bool(auto and not pending),
        })
    return out


# ══════════════════════════════════════════════════════════════
# 화면
# ══════════════════════════════════════════════════════════════
@router.get("", response_class=HTMLResponse)
def page(course: Optional[str] = None, db: Session = Depends(get_db)):
    return HTMLResponse(_render(data(course=course, db=db)))


def _e(s) -> str:
    return _html.escape(str(s if s is not None else ""), quote=True)


def _cell_color(v: Optional[float], lo: float, hi: float):
    if v is None or hi <= lo:
        return "var(--cream-2)", "var(--muted)"
    i = int(round((v - lo) / (hi - lo) * (len(RAMP) - 1)))
    i = max(0, min(len(RAMP) - 1, i))
    return RAMP[i], RAMP_INK[i]


def _render(d: dict) -> str:
    rounds = d["rounds"]
    areas = d["areas"]
    course = d.get("course")

    # ── 과정 선택 ──
    tabs = "".join(
        f'<a class="tab{" on" if c == course else ""}" href="/dashboard?course={_e(c)}">{_e(c)}</a>'
        for c in d["courses"]) or '<span class="muted">업로드된 과정이 없습니다</span>'

    # ── 헤드라인 ──
    tiles = []
    for r in rounds:
        avg = r["average"]
        delta = r.get("delta")
        chip = ""
        if delta is not None:
            cls = "up" if delta > 0 else ("down" if delta < 0 else "flat")
            sign = "▲ +" if delta > 0 else ("▼ " if delta < 0 else "― ")
            chip = f'<span class="chip {cls}">{sign}{abs(delta):.2f}</span>'
        tiles.append(
            f'<div class="tile"><div class="k">{_e(r["label"])}'
            f'<span class="n2">{_e(r["date"] or "")}</span></div>'
            f'<div class="v">{avg if avg is not None else "—"}{chip}</div>'
            f'<div class="k">참가자 {r["n"]}명</div></div>')
    tiles_html = "".join(tiles) or '<p class="muted">집계할 카드가 없습니다.</p>'

    # ── 강사별 성장폭 (단일 계열 막대) ──
    ins = [x for x in d["instructors"] if x.get("delta") is not None]
    bars = ""
    if ins:
        top = max(abs(x["delta"]) for x in ins) or 1
        rows = []
        for x in ins:
            w = abs(x["delta"]) / top * 100
            rows.append(
                f'<div class="brow" title="{_e(x["name"])} · '
                f'{x["first"]:.2f} → {x["last"]:.2f} (평가 {x["n"]}건)">'
                f'<div class="bl">{_e(x["name"])}</div>'
                f'<div class="btrack"><i style="width:{w:.1f}%"></i></div>'
                f'<div class="bv">{x["delta"]:+.2f}'
                f'<span class="n2">{x["first"]:.2f} → {x["last"]:.2f}</span></div></div>')
        bars = f'<div class="bars">{"".join(rows)}</div>'
    else:
        bars = '<p class="muted">회차가 둘 이상이어야 성장폭을 낼 수 있습니다.</p>'

    # ── 영역별 히트맵 ──
    vals = [v for a in areas for v in a["by_round"].values()]
    lo, hi = (min(vals), max(vals)) if vals else (0, 1)
    round_labels = [r["label"] for r in rounds]
    head = "".join(f"<th>{_e(r)}</th>" for r in round_labels)
    body = []
    for a in areas:
        cells = []
        for r in round_labels:
            v = a["by_round"].get(r)
            bg, ink = _cell_color(v, lo, hi)
            cells.append(f'<td class="cell" title="{_e(a["name"])} · {_e(r)}: '
                         f'{v if v is not None else "미평가"}">'
                         f'<span style="background:{bg};color:{ink}">'
                         f'{v if v is not None else "—"}</span></td>')
        dl = a.get("delta")
        if dl is None:
            dtxt = '<span class="n2">신규</span>'
        elif dl > 0:
            dtxt = f'<span class="up">▲ +{dl:.2f}</span>'
        elif dl < 0:
            dtxt = f'<span class="down">▼ {abs(dl):.2f}</span>'
        else:
            dtxt = '<span class="muted">― 유지</span>'
        body.append(f'<tr><td class="nm">{_e(a["name"])}</td>{"".join(cells)}'
                    f'<td class="d">{dtxt}</td></tr>')

    swatches = "".join(f'<i style="background:{c}"></i>' for c in RAMP)
    heat = (f'<table class="heat"><thead><tr><th>영역</th>{head}<th>증감</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>'
            f'<div class="scale"><span class="n2">{lo:.2f}</span>{swatches}'
            f'<span class="n2">{hi:.2f}</span></div>') if areas else \
           '<p class="muted">집계할 점수가 없습니다.</p>'

    lowest = min((a for a in areas if a.get("latest") is not None),
                 key=lambda a: a["latest"], default=None)
    hint = (f'<div class="hint">가장 최근 회차에서 <b>{_e(lowest["name"])}</b> 가 '
            f'{lowest["latest"]:.2f} 로 가장 낮습니다 — 다음 특강 주제를 여기서 잡을 수 있습니다.</div>'
            if lowest else "")

    # ── 데이터 신뢰도 ──
    integ = "".join(
        f'<li><b>{_e(x["name"])}</b> {_e(x["round"] or "")} — 원본 '
        f'<s>{_e(x["original"])}</s> → 재계산 <b>{_e(x["recomputed"])}</b>'
        f'<span class="n2">{_e(x["detail"])}</span></li>' for x in d["integrity"])
    integ_html = (f'<ul class="list">{integ}</ul>' if integ
                  else '<p class="muted">원본 평균과 어긋난 건이 없습니다.</p>')

    # ── 비정규 참가자 / 보류 ──
    holds = ""
    for h in d["holds"]:
        state = ("자동 승인됨" if h["auto_approved"] else
                 ("발송 가능" if h["sendable"] else "보류 중"))
        cls = "flat" if h["auto_approved"] else ("up" if h["sendable"] else "hold")
        holds += (f'<li><label><input type="checkbox" value="{h["card_id"]}"> '
                  f'<b>{_e(h["name"])}</b></label> '
                  f'<span class="chip {cls}">{state}</span>'
                  f'<span class="n2">{_e(" · ".join(x for x in h["reasons"] if x))}</span></li>')
    holds_html = (f'<ul class="list checks" id="holds">{holds}</ul>'
                  '<div class="act"><input id="op" placeholder="담당자 이름">'
                  '<button onclick="bulk(\'approve\')">선택 승인</button>'
                  '<button class="ghost" onclick="bulk(\'exclude\')">선택 제외</button>'
                  '<span id="msg" class="n2"></span></div>') if holds else \
        '<p class="muted">보류 대상이 없습니다.</p>'

    return _SHELL.replace("__TABS__", tabs) \
                 .replace("__COURSE__", _e(course or "—")) \
                 .replace("__TILES__", tiles_html) \
                 .replace("__BARS__", bars) \
                 .replace("__HEAT__", heat + hint) \
                 .replace("__INTEG__", integ_html) \
                 .replace("__HOLDS__", holds_html)


_SHELL = """
<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HRD 대시보드</title>
<style>
:root{--sk-red:#DA1B33;--ink:#231D18;--ink-2:#4A423A;--muted:#6E655C;--faint:#9C9188;
 --line:#E2D7C0;--line-2:#EFE8D9;--cream:#FBF5E4;--cream-2:#F2E9D6;--page:#F0E8D6;
 --up:#0B6151;--up-soft:#E6F0EC;--down:#A84A1A;--warn:#8A5209;--warn-soft:#FBF0DA;
 --sans:'Pretendard','Malgun Gothic',sans-serif;--mono:Consolas,'D2Coding',monospace}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:var(--sans);background:var(--page);color:var(--ink);line-height:1.7;
 padding:32px 16px 70px;font-size:15px}
.wrap{max-width:940px;margin:0 auto}
h1{font-size:26px;letter-spacing:-.03em;margin-bottom:4px}
.sub{color:var(--muted);font-size:13.5px;margin-bottom:20px}
.tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:22px}
.tab{font-size:13px;padding:6px 14px;border-radius:20px;border:1px solid var(--line);
 background:#fff;color:var(--ink-2);text-decoration:none}
.tab.on{background:var(--sk-red);border-color:var(--sk-red);color:#fff;font-weight:700}
section{background:#fff;border:1px solid var(--line);border-radius:5px;
 padding:22px 24px;margin-bottom:18px}
h2{font-size:16px;font-weight:800;letter-spacing:-.02em;margin-bottom:4px}
.lead{font-size:13px;color:var(--muted);margin-bottom:18px}
.muted{color:var(--muted);font-size:13.5px}
.n2{color:var(--faint);font-size:12px;font-weight:400;margin-left:8px}
.chip{display:inline-block;font-size:12px;font-weight:700;padding:2px 10px;
 border-radius:20px;margin-left:9px}
.chip.up{background:var(--up-soft);color:var(--up)}
.chip.down{background:#F8EBE0;color:var(--down)}
.chip.flat{background:var(--cream-2);color:var(--ink-2)}
.chip.hold{background:#FBE9EA;color:#A00E22}
.tiles{display:flex;gap:14px;flex-wrap:wrap}
.tile{flex:1 1 190px;background:var(--cream);border:1px solid var(--line);
 border-radius:5px;padding:16px 18px}
.tile .k{font-size:12px;color:var(--muted);font-weight:600}
.tile .v{font-family:var(--mono);font-size:34px;font-weight:800;letter-spacing:-.04em;
 line-height:1.2;margin:4px 0}
.bars{display:grid;gap:10px}
.brow{display:flex;align-items:center;gap:14px}
.bl{flex:0 0 130px;font-size:13.5px;font-weight:700}
.btrack{flex:1;background:var(--cream-2);border-radius:3px;height:14px;overflow:hidden}
.btrack i{display:block;height:100%;background:#0B6151;border-radius:0 4px 4px 0}
.bv{flex:0 0 150px;text-align:right;font-family:var(--mono);font-weight:700;font-size:14px}
.heat{width:100%;border-collapse:separate;border-spacing:2px;font-size:14px}
.heat th{font-size:11.5px;letter-spacing:.04em;color:var(--muted);font-weight:600;
 text-align:center;padding-bottom:4px}
.heat th:first-child,.heat .nm{text-align:left}
.heat .nm{font-weight:700;font-size:14px;padding-right:10px}
.heat .cell{text-align:center}
.heat .cell span{display:block;padding:9px 4px;border-radius:3px;
 font-family:var(--mono);font-weight:700}
.heat .d{text-align:right;font-family:var(--mono);font-weight:700;font-size:13px;
 white-space:nowrap;padding-left:8px}
.up{color:var(--up)} .down{color:var(--down)}
.scale{display:flex;align-items:center;gap:3px;justify-content:flex-end;margin-top:10px}
.scale i{width:26px;height:9px;border-radius:2px;display:block}
.hint{margin-top:14px;background:var(--warn-soft);border:1px solid #E6D2A8;
 border-radius:4px;padding:11px 14px;font-size:13.5px;color:var(--ink-2)}
.list{list-style:none;display:grid;gap:9px}
.list li{padding:10px 12px;background:var(--cream);border:1px solid var(--line-2);
 border-radius:4px;font-size:13.5px}
.list s{color:var(--faint)}
.checks label{cursor:pointer}
.act{margin-top:14px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.act input{font:inherit;font-size:13px;padding:6px 10px;border:1px solid var(--line);
 border-radius:4px}
button{font:inherit;font-size:13px;font-weight:700;background:var(--sk-red);color:#fff;
 border:0;border-radius:20px;padding:7px 16px;cursor:pointer}
button.ghost{background:#fff;color:var(--ink-2);border:1px solid var(--line)}
a.back{color:var(--sk-red);font-weight:700;text-decoration:none;font-size:13.5px}
</style></head><body><div class=wrap>
<h1>HRD 대시보드</h1>
<div class=sub>과정: <b>__COURSE__</b> · 청강생·제외 대상은 집계에서 빠집니다 ·
 <a class=back href="/">업로드 화면</a></div>
<div class=tabs>__TABS__</div>

<section><h2>조직 성장 지표</h2>
 <p class=lead>차수별 평균. 개인 평균을 다시 평균한 값입니다.</p>
 <div class=tiles>__TILES__</div></section>

<section><h2>강사별 성장폭</h2>
 <p class=lead>첫 회차 대비 마지막 회차의 평균 변화. 막대에 마우스를 올리면 원값이 나옵니다.</p>
 __BARS__</section>

<section><h2>영역별 히트맵</h2>
 <p class=lead>진할수록 높은 점수입니다. 어느 역량이 더디게 개선되는지 봅니다.</p>
 __HEAT__</section>

<section><h2>데이터 신뢰도 알림</h2>
 <p class=lead>원본 엑셀의 평균과 엔진 재계산이 어긋난 건입니다 (R-03).</p>
 __INTEG__</section>

<section><h2>비정규 참가자 · 발송 보류</h2>
 <p class=lead>청강생·명부 미확인 등으로 잠긴 대상입니다 (R-07·R-15).</p>
 __HOLDS__</section>
</div>
<script>
async function bulk(decision){
  const ids=[...document.querySelectorAll('#holds input:checked')].map(x=>+x.value);
  const op=document.getElementById('op').value.trim();
  const msg=document.getElementById('msg');
  if(!ids.length){msg.textContent='대상을 선택하세요';return;}
  if(!op){msg.textContent='담당자 이름을 입력하세요';return;}
  msg.textContent='처리 중…';
  const r=await fetch('/dashboard/approve',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({card_ids:ids,operator:op,decision})});
  msg.textContent = r.ok ? '완료' : ('실패 ' + r.status);
  if(r.ok) setTimeout(()=>location.reload(),600);
}
</script></body></html>
"""
