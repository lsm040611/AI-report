"""리포트 렌더러 — report_template.js 의 파이썬 이식본.

원본(자바스크립트)과 같은 HTML/CSS 를 낸다. 이식한 이유는 하나다:
백엔드가 파이썬이라 Node 런타임 없이 `uvicorn` 하나로 리포트까지 끝내기 위해서다.
디자인 시안(리포트_시안_v2.html)이 기준이고, 이 파일은 그것을 찍어내기만 한다.

    html = render(card)     # card 스키마는 render/adapter.py 가 만든다

색을 바꾸려면 CSS 안의 :root 변수만 고치면 전체에 반영된다.
"""
from __future__ import annotations

import html as _html
import math
from typing import Any, List, Optional

# ══════════════════════════════════════════════════════════════
# 1. 스타일 — 시안 v2 확정본
# ══════════════════════════════════════════════════════════════
CSS = """
:root{
  --sk-red:#DA1B33;          /* 포인트 — 선·배지·숫자에만 */
  --sk-red-deep:#A00E22;
  --sk-red-soft:#FBE9EA;
  --butter:#FAF1DC;          /* 머리부 · 강조 패널 */
  --butter-2:#F5E7C4;        /* 패널 안쪽 톤 */
  --cream:#FBF5E4;           /* 섹션 배경 */
  --cream-2:#F2E9D6;         /* 칩 · 보조 배경 */
  --page:#F0E8D6;            /* 지면 바깥 */
  --paper:#FFFFFF;
  --gold:#D9A521;            /* 강점 표시 */
  --ink:#231D18;
  --ink-2:#4A423A;
  --muted:#6E655C;
  --faint:#9C9188;
  --line:#E2D7C0;
  --line-2:#EFE8D9;
  --up:#0B6151;   --up-soft:#E6F0EC;  --up-line:#CBDFD8;
  --down:#A84A1A; --down-soft:#F8EBE0;
  --warn:#8A5209; --warn-soft:#FBF0DA; --warn-line:#E6D2A8;
  --sans:'Pretendard','Pretendard Variable',-apple-system,BlinkMacSystemFont,'Segoe UI','Malgun Gothic','맑은 고딕',sans-serif;
  --mono:'SFMono-Regular',Consolas,'D2Coding','Menlo',monospace;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:var(--sans);background:var(--page);color:var(--ink);
  font-size:15.5px;line-height:1.75;-webkit-font-smoothing:antialiased;padding:34px 16px 60px}
.sheet{max-width:780px;margin:0 auto;background:var(--paper);
  border:1px solid var(--line);border-radius:3px;overflow:hidden}

/* ── 머리부 ── */
.masthead{background:var(--butter);border-top:6px solid var(--sk-red);padding:32px 46px 28px}
.masthead .eyebrow{display:inline-block;font-size:12px;font-weight:700;letter-spacing:.1em;
  color:var(--sk-red);background:var(--paper);border:1px solid #F0D3D6;padding:3px 12px;border-radius:20px}
.masthead h1{font-size:42px;font-weight:800;letter-spacing:-.035em;margin:14px 0 6px;
  line-height:1.1;color:var(--ink)}
.masthead h1 .alias{font-size:19px;font-weight:500;color:var(--faint);margin-left:11px;letter-spacing:0}
.masthead .prog{font-size:16.5px;font-weight:600;color:var(--ink-2)}
.metastrip{background:var(--paper);border-top:1px solid var(--line);
  border-bottom:1px solid var(--line);padding:13px 46px;display:flex;flex-wrap:wrap;gap:6px 0}
.metastrip div{font-size:13.5px;color:var(--ink-2);padding:0 18px;border-left:1px solid var(--line-2)}
.metastrip div:first-child{padding-left:0;border-left:none}
.metastrip .k{color:var(--muted);margin-right:7px;font-size:12.5px}
.metastrip .v{font-weight:600;color:var(--ink)}

/* ── 섹션 ── */
section{padding:34px 46px;border-top:1px solid var(--line-2)}
section.tint{background:var(--cream)}
section.first{border-top:none}
section.merge{border-top:none;padding-top:0}
h2{font-size:21px;font-weight:800;letter-spacing:-.02em;margin-bottom:6px;
  display:flex;align-items:center;gap:13px}
h2 .no{flex:none;width:28px;height:28px;border-radius:4px;background:var(--sk-red);color:#fff;
  font-family:var(--mono);font-size:13px;font-weight:700;
  display:flex;align-items:center;justify-content:center;letter-spacing:-.02em}
h2 + .lead{font-size:14px;color:var(--muted);margin:0 0 22px 41px;line-height:1.65}
h3{font-size:14.5px;font-weight:700;color:var(--ink);margin:26px 0 12px}

/* ── 01 한눈에 ── */
.hero{display:flex;gap:0;border:1px solid var(--line);border-radius:5px;overflow:hidden}
.hero .score{flex:0 0 218px;background:var(--butter-2);color:var(--ink);padding:24px 26px;
  display:flex;flex-direction:column;justify-content:center;
  border-right:1px solid var(--line);position:relative}
.hero .score::before{content:'';position:absolute;left:0;top:0;bottom:0;width:5px;background:var(--sk-red)}
.hero .score .l{font-size:12.5px;font-weight:700;color:var(--sk-red-deep);letter-spacing:.03em}
.hero .score .n{font-size:62px;font-weight:800;line-height:.98;letter-spacing:-.05em;
  margin:8px 0 0;color:var(--ink)}
.hero .score .n small{font-size:22px;font-weight:500;color:var(--muted);letter-spacing:-.02em}
.hero .score .cmp{margin-top:14px;padding-top:13px;border-top:1px solid rgba(35,29,24,.16);
  font-size:13px;color:var(--ink-2);line-height:1.6}
.hero .score .cmp b{color:var(--ink);font-weight:700}
.hero .list{flex:1;min-width:0}
.hero .row{display:flex;align-items:center;gap:16px;padding:17px 24px;border-bottom:1px solid var(--line-2)}
.hero .row:last-child{border-bottom:none}
.chip{flex:none;display:inline-flex;align-items:center;gap:5px;font-size:12.5px;font-weight:700;
  padding:4px 12px;border-radius:20px;white-space:nowrap;min-width:104px;justify-content:center}
.chip.good{background:var(--up-soft);color:var(--up)}
.chip.grow{background:var(--warn-soft);color:var(--warn)}
.chip.done{background:var(--sk-red-soft);color:var(--sk-red-deep)}
.chip.gap{background:var(--down-soft);color:var(--down)}
.hero .row .body{flex:1;min-width:0}
.hero .row .nm{font-size:19px;font-weight:700;letter-spacing:-.02em;line-height:1.3}
.hero .row .why{font-size:13px;color:var(--muted);margin-top:3px;line-height:1.55}
.hero .row .v{flex:none;font-family:var(--mono);font-size:23px;font-weight:700;
  letter-spacing:-.03em;text-align:right}
.hero .row .v small{font-size:13px;color:var(--faint);font-weight:500}
.hero .row .v.word{font-family:var(--sans);font-size:17px;font-weight:700;color:var(--sk-red-deep)}

/* ── 척도 트랙 ── */
.crow{padding:18px 0;border-bottom:1px solid var(--line-2)}
.crow:last-child{border-bottom:none}
.crow.spot{background:var(--warn-soft);margin:0 -16px;padding:18px 16px;border-radius:4px}
.crow .top{display:flex;align-items:baseline;gap:11px;margin-bottom:10px}
.crow .an{font-size:17px;font-weight:700;letter-spacing:-.015em;flex:none}
.crow .ad{font-size:13px;color:var(--muted);font-weight:400;flex:1;line-height:1.5}
.crow .val{font-family:var(--mono);font-size:22px;font-weight:700;letter-spacing:-.03em;flex:none}
.crow .val small{font-size:12.5px;color:var(--faint);font-weight:500}
.track{position:relative;height:26px}
.track .rail{position:absolute;top:11px;left:0;right:0;height:5px;background:var(--cream-2);border-radius:3px}
.track .tick{position:absolute;top:8px;width:1px;height:11px;background:var(--line);transform:translateX(-.5px)}
.track .me{position:absolute;top:4px;width:17px;height:17px;border-radius:50%;
  background:var(--sk-red);border:3px solid var(--paper);transform:translateX(-8.5px);
  box-shadow:0 0 0 1.5px var(--sk-red)}
.track .avg{position:absolute;top:1px;width:2px;height:23px;background:var(--ink-2);
  transform:translateX(-1px);opacity:.6}
.track .avgl{position:absolute;top:24px;font-size:11px;color:var(--muted);
  transform:translateX(-50%);white-space:nowrap}
.scaleends{display:flex;justify-content:space-between;font-size:11px;color:var(--faint);margin-top:22px}
.crow .memo{font-size:13.5px;color:var(--ink-2);border-left:3px solid var(--line);
  padding:4px 0 4px 13px;margin-top:14px;line-height:1.7}

/* ── 레이더 차트 ── */
.radarwrap{margin:2px 0 0;padding:4px 0 0}
.radar-legend{justify-content:center;gap:10px 22px;margin:2px 0 20px;font-size:12.5px;color:var(--muted)}
.radar-legend .it{gap:7px}
.radar-note{font-size:12.5px;color:var(--muted);text-align:center;margin:-12px 0 20px}

/* ── 암기 문장 카드 ── */
.mcard{border:1px solid var(--line);border-radius:5px;overflow:hidden;background:var(--paper)}
.mcard .done{background:var(--up-soft);color:var(--up);border-bottom:1px solid var(--up-line);
  padding:11px 20px;font-size:13.5px;font-weight:700;display:flex;align-items:center;gap:8px}
.mcard .head{background:#3B3026;color:#fff;padding:11px 20px;font-size:11.5px;
  font-weight:700;letter-spacing:.14em;font-family:var(--mono)}
.mcard .say{padding:22px 24px;font-size:18.5px;line-height:1.75;font-weight:700;
  color:var(--ink);background:var(--butter)}
.mcard .parts{padding:15px 24px 6px}
.mcard .part{display:flex;gap:11px;align-items:flex-start;padding:0 0 11px;
  font-size:13.5px;line-height:1.6;color:var(--ink-2)}
/* 칩 색은 리포트 범례를 그대로 따른다 — 초록=권장, 빨강=고칠, 노랑=핵심.
   여기서 색을 달리 쓰면 같은 문서 안에서 빨강의 뜻이 두 개가 된다. */
.mcard .part .tag{flex:none;font-size:11px;font-weight:700;border-radius:20px;
  padding:3px 10px;white-space:nowrap;background:var(--cream-2);color:var(--ink-2)}
.mcard .part .tag.k-fix{background:#CFE7E0;color:var(--up)}
.mcard .part .tag.k-issue{background:#FBD9DF;color:var(--sk-red-deep)}
.mcard .part .tag.k-key{background:#FBEDBE;color:var(--ink)}
.mcard .part .q{font-weight:700}
.mcard .close{padding:14px 24px;border-top:1px solid var(--line-2);background:var(--cream);
  font-size:14px;color:var(--ink-2);line-height:1.75}

/* ── 성장 표 ── */
.gtable{width:100%;border-collapse:collapse;font-size:14.5px}
.gtable th{text-align:left;font-size:11.5px;letter-spacing:.04em;color:var(--muted);
  font-weight:600;padding:0 12px 9px 0;border-bottom:1.5px solid var(--line)}
.gtable td{padding:14px 12px 14px 0;border-bottom:1px solid var(--line-2);vertical-align:middle}
.gtable td.n{font-family:var(--mono);font-weight:700;white-space:nowrap;font-size:15.5px}
.gtable .nm{font-weight:700;font-size:15.5px;letter-spacing:-.01em}
.gtable .nm span{font-weight:400;color:var(--muted);font-size:13px;margin-left:5px}
.dbar{position:relative;height:22px;min-width:110px}
.dbar .zero{position:absolute;left:50%;top:0;bottom:0;width:1px;background:var(--line)}
.dbar i{position:absolute;top:6px;height:11px;border-radius:2px}
.dbar i.p{left:50%;background:var(--up)}
.dbar i.m{right:50%;background:var(--down)}
.dbar .flat{position:absolute;left:50%;top:9px;width:11px;height:4px;
  background:var(--faint);transform:translateX(-5.5px);border-radius:2px}
.delta{font-family:var(--mono);font-weight:700;font-size:15px;white-space:nowrap}
.delta.p{color:var(--up)} .delta.m{color:var(--down)} .delta.z{color:var(--muted)}
.pill{display:inline-block;font-size:12px;font-weight:600;padding:3px 11px;border-radius:20px;
  background:var(--cream-2);color:var(--ink-2);white-space:nowrap}

/* ── 지난 과제 이행 ── */
.follow{border:1px solid var(--up-line);border-radius:5px;overflow:hidden;margin-bottom:26px}
.follow .head{background:var(--up-soft);color:var(--up);padding:12px 20px;font-size:14.5px;
  font-weight:700;display:flex;align-items:center;gap:9px;border-bottom:1px solid var(--up-line)}
.follow .in{padding:18px 20px}
.follow .pa{font-size:14.5px;color:var(--muted);line-height:1.7}
.follow .pa b{color:var(--ink);font-weight:700}
.follow .ev{margin-top:13px;font-size:15px;color:var(--ink);background:var(--up-soft);
  border-radius:3px;padding:13px 16px;line-height:1.8}

/* ── 서술 블록 ── */
.narr{border-left:4px solid var(--gold);background:var(--cream);border-radius:0 4px 4px 0;
  padding:20px 24px;font-size:16px;line-height:1.95;white-space:pre-wrap}
.narr + .narr{margin-top:14px}
.narr.gap{border-left-color:var(--sk-red)}
em.bad{font-style:normal;color:var(--sk-red-deep);background:#FBD9DF;
  padding:1px 4px;border-radius:2px;font-weight:600}
em.fix{font-style:normal;font-weight:700;color:var(--up);background:#CFE7E0;padding:1px 4px;border-radius:2px}
em.key{font-style:normal;font-weight:700;background:#FBEDBE;padding:1px 4px;border-radius:2px}

/* ── 표현 교정 노트 ── */
.fixgrid{display:grid;gap:14px}
.fixcard{border:1px solid var(--line);border-radius:5px;overflow:hidden;background:var(--paper)}
.fixcard .row{display:flex}
.fixcard .side{flex:1 1 50%;padding:17px 20px;min-width:0}
.fixcard .side.x{background:var(--sk-red-soft);border-right:1px solid var(--line)}
.fixcard .side.o{background:var(--up-soft)}
.fixcard .side.n{background:var(--cream-2);border-right:1px solid var(--line)}
.fixcard .tagline{display:flex;align-items:center;gap:7px;font-size:12.5px;font-weight:800;
  letter-spacing:.02em;margin-bottom:9px}
.fixcard .side.x .tagline{color:var(--sk-red)}
.fixcard .side.o .tagline{color:var(--up)}
.fixcard .side.n .tagline{color:var(--muted)}
.fixcard .txt{font-size:16px;line-height:1.6;word-break:break-word;font-weight:600}
.fixcard .side.x .txt{color:var(--sk-red-deep);text-decoration:line-through;
  text-decoration-color:rgba(168,0,31,.45);text-decoration-thickness:1.5px}
.fixcard .side.o .txt{color:var(--up);font-weight:700}
.fixcard .side.n .txt{color:var(--ink-2)}
.fixcard .note{padding:12px 20px;font-size:13.5px;color:var(--ink-2);
  border-top:1px solid var(--line);line-height:1.7;background:var(--paper)}
.fixcard .note b{color:var(--sk-red-deep);font-weight:700;margin-right:4px}

/* ── 실천 체크리스트 ── */
.todo{border:1px solid #4A3D2E;border-radius:5px;overflow:hidden}
.todo .head{background:#3B3026;color:#fff;padding:13px 20px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.todo .head .t{font-size:15.5px;font-weight:700;letter-spacing:-.01em}
.todo .head .when{margin-left:auto;font-size:12px;color:#C7BBB1;font-weight:500}
.todo ul{list-style:none;padding:4px 20px 16px}
.todo li{display:flex;gap:14px;align-items:flex-start;padding:15px 0;
  border-bottom:1px solid var(--line-2);font-size:16px;line-height:1.7}
.todo li:last-child{border-bottom:none}
.todo .box{flex:none;width:19px;height:19px;border:2px solid #8A7B68;border-radius:3px;margin-top:5px}
.todo .sub{display:block;font-size:13.5px;color:var(--muted);margin-top:4px;line-height:1.6}
.todo .src{padding:12px 20px;background:var(--cream);border-top:1px solid var(--line-2);
  font-size:13px;color:var(--muted);line-height:1.6}

/* ── 관계별 결과 (진단) ── */
.keyline{border:1px solid var(--warn-line);background:var(--butter);border-radius:5px;
  overflow:hidden;margin-bottom:24px}
.keyline .h{background:var(--butter-2);color:var(--warn);padding:11px 20px;font-size:14px;
  font-weight:700;border-bottom:1px solid var(--warn-line)}
.keyline .b{padding:17px 20px;font-size:15.5px;line-height:1.85}
.keyline .b b{font-weight:800}
.maptable{width:100%;border-collapse:collapse;font-size:14.5px}
.maptable th{font-size:11.5px;letter-spacing:.04em;color:var(--muted);font-weight:600;
  padding:0 10px 9px 0;border-bottom:1.5px solid var(--line);text-align:left}
.maptable th.c,.maptable td.c{text-align:center}
.maptable td{padding:13px 10px 13px 0;border-bottom:1px solid var(--line-2)}
.maptable .nm{font-weight:700;font-size:15.5px;letter-spacing:-.01em}
.maptable .nm span{display:block;font-size:13px;color:var(--muted);font-weight:400;
  line-height:1.5;margin-top:2px}
.dot{display:inline-block;font-family:var(--mono);font-size:15px;font-weight:700;
  padding:5px 13px;border-radius:20px;min-width:52px;letter-spacing:-.02em}
.dot.hi{background:var(--up-soft);color:var(--up)}
.dot.mid{background:var(--cream-2);color:var(--ink-2)}
.dot.lo{background:#FBD9DF;color:var(--sk-red-deep)}
.footnote{font-size:13px;color:var(--muted);margin-top:14px}

/* ── 주제 ── */
.theme{display:flex;gap:16px;align-items:flex-start;padding:17px 0;border-bottom:1px solid var(--line-2)}
.theme:last-of-type{border-bottom:none}
.theme .txt{flex:1;font-size:15.5px;line-height:1.8}
.theme .txt b{font-weight:700}
.theme .cnt{flex:none;font-size:12.5px;font-weight:600;color:var(--ink-2);
  background:var(--cream-2);padding:4px 12px;border-radius:20px;margin-top:3px;white-space:nowrap}

/* ── 안내 박스 ── */
.note-box{background:var(--cream);border:1px solid var(--line);border-left:4px solid var(--line);
  border-radius:0 3px 3px 0;padding:16px 19px;font-size:13.5px;color:var(--ink-2);
  line-height:1.8;margin-top:20px}
.note-box.accent{border-left-color:var(--gold);background:var(--butter)}
.note-box .h{font-weight:700;color:var(--ink);display:block;margin-bottom:5px;font-size:14px}
.note-box b{font-weight:700;color:var(--ink)}

/* ── 범례 · 푸터 ── */
.legend{display:flex;gap:14px 26px;flex-wrap:wrap;font-size:13.5px;color:var(--ink-2);line-height:1.7}
.legend .it{display:flex;align-items:center;gap:8px}
footer{padding:22px 46px 28px;background:var(--cream);border-top:1px solid var(--line);
  font-size:12.5px;color:var(--muted);line-height:1.9}
footer .brand{font-size:12px;letter-spacing:.1em;color:var(--sk-red);font-weight:700;
  display:block;margin-bottom:7px}

@media (max-width:660px){
  .masthead,section,footer,.metastrip{padding-left:24px;padding-right:24px}
  .masthead h1{font-size:32px}
  .hero{flex-direction:column}
  .hero .score{flex:none}
  .hero .row{flex-wrap:wrap;gap:9px 14px}
  .hero .row .v{margin-left:auto}
  .fixcard .row{flex-direction:column}
  .fixcard .side.x,.fixcard .side.n{border-right:none;border-bottom:1px solid var(--line)}
  .metastrip div{border-left:none;padding:0 16px 0 0}
  .gtable td:nth-child(2),.gtable th:nth-child(2){display:none}
}
@media print{
  body{background:#fff;padding:0}
  *{-webkit-print-color-adjust:exact;print-color-adjust:exact}
  .sheet{margin:0;border:none;max-width:none}
  section,.fixcard,.follow,.todo,.crow,.hero,.keyline{break-inside:avoid}
}
"""

# ══════════════════════════════════════════════════════════════
# 2. 유틸
# ══════════════════════════════════════════════════════════════

# 강조 서식 키 매핑 — 파서(R-05)가 내는 키와 짧은 키 둘 다 받는다
EMPHASIS = {
    "issue_expression": "bad",      # 원본 붉은색   → 고칠 표현
    "corrected_expression": "fix",  # 원본 굵게+밑줄 → 권장 표현
    "key_concept": "key",           # 원본 굵게     → 핵심 개념
    "bad": "bad", "fix": "fix", "key": "key",
}


def esc(s: Any) -> str:
    if s is None:
        return ""
    return _html.escape(str(s), quote=True)


def runs_html(runs) -> str:
    """runs → HTML. 문자열을 주면 이스케이프만 한다."""
    if runs is None:
        return ""
    if isinstance(runs, str):
        return esc(runs)
    out = []
    for r in runs:
        text = esc(r.get("text") if r.get("text") is not None else r.get("t"))
        cls = EMPHASIS.get(r.get("emphasis") or r.get("e"))
        out.append(f'<em class="{cls}">{text}</em>' if cls else text)
    return "".join(out)


def ratio(v, mn, mx) -> Optional[float]:
    """점수를 척도상 위치(%)로. 척도가 과정마다 달라도 그대로 동작한다."""
    if v is None or mx == mn:
        return None
    p = ((float(v) - mn) / (mx - mn)) * 100
    return max(0.0, min(100.0, p))


def fmt(v, digits: int = 1) -> str:
    if v is None:
        return "—"
    return f"{float(v):.{digits}f}"


def round_half_up(v, digits: int = 0):
    """엑셀 ROUND 방식(half-up). 파이썬 기본 round 는 은행가 반올림이라 4.5→4 가 된다."""
    if v is None:
        return None
    f = 10 ** digits
    return math.floor(abs(v) * f + 0.5) / f * (1 if v >= 0 else -1)


def is_blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, (list, tuple, dict)):
        return len(v) == 0
    if isinstance(v, str):
        return v.strip() == ""
    return False


def _pct(x: float) -> str:
    """CSS 좌표. 15.000000000002% 같은 부동소수 찌꺼기를 자른다."""
    return f"{x:.4g}"


# ══════════════════════════════════════════════════════════════
# 3. 섹션 렌더러 — 내용이 비면 빈 문자열을 돌려주고 섹션째로 사라진다
# ══════════════════════════════════════════════════════════════

def _glance(s: dict) -> str:
    if not s.get("score") and is_blank(s.get("rows")):
        return ""
    sc = s.get("score") or {}
    left = ""
    if s.get("score"):
        maxpart = f'<small> / {esc(sc.get("max"))}</small>' if sc.get("max") is not None else ""
        lines = ("" if is_blank(sc.get("lines"))
                 else f'<div class="cmp">{"<br>".join(sc["lines"])}</div>')
        left = (f'<div class="score"><div class="l">{esc(sc.get("label"))}</div>'
                f'<div class="n">{esc(sc.get("value"))}{maxpart}</div>{lines}</div>')

    rows = []
    for r in s.get("rows") or []:
        if r.get("valueWord"):
            val = f'<div class="v word">{esc(r["valueWord"])}</div>'
        elif r.get("value") is not None:
            suffix = f'<small>{esc(r["valueSuffix"])}</small>' if r.get("valueSuffix") else ""
            val = f'<div class="v">{esc(r["value"])}{suffix}</div>'
        else:
            val = ""
        why = f'<div class="why">{esc(r["why"])}</div>' if r.get("why") else ""
        rows.append(
            f'<div class="row"><span class="chip {esc(r.get("tone") or "good")}">'
            f'{esc(r.get("chip"))}</span>'
            f'<div class="body"><div class="nm">{esc(r.get("name"))}</div>{why}</div>{val}</div>')

    return (f'<div class="hero">{left}<div class="list">{"".join(rows)}</div></div>'
            + note_boxes(s.get("notes")))


def _growth(s: dict) -> str:
    if not s.get("followup") and is_blank(s.get("rows")):
        return ""
    out = ""

    f = s.get("followup")
    if f:
        prev = (f'<div class="pa">{esc(f.get("prevLabel"))} — '
                f'<b>{esc(f.get("prevAction"))}</b></div>') if f.get("prevLabel") else ""
        ev = f'<div class="ev">{runs_html(f.get("evidence"))}</div>' if f.get("evidence") else ""
        out += (f'<div class="follow"><div class="head">{esc(f.get("title"))}</div>'
                f'<div class="in">{prev}{ev}</div></div>')

    rows = s.get("rows") or []
    if rows:
        # 막대 길이 기준 = 이 표 안의 최대 변화폭 (절대치 막대를 쓰지 않는 이유는 README)
        max_abs = max((abs(r["delta"]) for r in rows if r.get("delta") is not None),
                      default=0)

        body = []
        for r in rows:
            digits = r.get("digits", 1)
            if r.get("isNew"):
                sub = f'<span>{esc(r["sub"])}</span>' if r.get("sub") else ""
                label = esc(r.get("newLabel") or "이번 회차에 새로 추가된 역량")
                body.append(
                    f'<tr><td class="nm">{esc(r.get("name"))}{sub}</td>'
                    f'<td colspan="3"><span class="pill">{label}</span></td>'
                    f'<td class="n" style="color:var(--faint)">{fmt(r.get("curr"), digits)}</td></tr>')
                continue

            d = r.get("delta")
            if d is None:
                bar, sym = '<div class="flat"></div>', '<span class="delta z">—</span>'
            elif d > 0:
                w = _pct(abs(d) / max_abs * 46) if max_abs else "0"
                bar = f'<i class="p" style="width:{w}%"></i>'
                sym = f'<span class="delta p">▲ +{fmt(d, digits)}</span>'
            elif d < 0:
                w = _pct(abs(d) / max_abs * 46) if max_abs else "0"
                bar = f'<i class="m" style="width:{w}%"></i>'
                sym = f'<span class="delta m">▼ {fmt(abs(d), digits)}</span>'
            else:
                bar, sym = '<div class="flat"></div>', '<span class="delta z">― 유지</span>'

            sub = f'<span>{esc(r["sub"])}</span>' if r.get("sub") else ""
            body.append(
                f'<tr><td class="nm">{esc(r.get("name"))}{sub}</td>'
                f'<td><div class="dbar"><div class="zero"></div>{bar}</div></td>'
                f'<td class="n">{fmt(r.get("prev"), digits)}</td>'
                f'<td class="n">{fmt(r.get("curr"), digits)}</td>'
                f'<td class="n">{sym}</td></tr>')

        h = s.get("columns") or ["역량", "변화", "이전", "이번", "증감"]
        title = f'<h3>{esc(s["tableTitle"])}</h3>' if s.get("tableTitle") else ""
        out += (title + '<table class="gtable"><thead><tr>'
                f'<th style="width:32%">{esc(h[0])}</th><th style="width:24%">{esc(h[1])}</th>'
                f'<th style="width:14%">{esc(h[2])}</th><th style="width:14%">{esc(h[3])}</th>'
                f'<th style="width:16%">{esc(h[4])}</th></tr></thead>'
                f'<tbody>{"".join(body)}</tbody></table>')

    return out + note_boxes(s.get("notes"))


def _scores(s: dict) -> str:
    if is_blank(s.get("items")) and not s.get("radar"):
        return ""
    mn = s.get("scaleMin", 1)
    mx = s.get("scaleMax", 5)

    # 레이더는 '모양'을, 아래 트랙은 '거리'를 보여 준다. 둘은 한 섹션이다.
    head = ""
    if s.get("radar"):
        head = (f'<div class="radarwrap">{s["radar"]}</div>'
                + (s.get("radarLegend") or "")
                + (f'<p class="radar-note">{esc(s["radarNote"])}</p>'
                   if s.get("radarNote") else ""))
    if is_blank(s.get("items")):
        return head
    ticks = "".join(f'<div class="tick" style="left:{_pct(ratio(i, mn, mx))}%"></div>'
                    for i in range(int(mn), int(mx) + 1))

    out = []
    for it in s["items"]:
        me = ratio(it.get("value"), mn, mx)
        av = ratio(it.get("groupAvg"), mn, mx)
        if it.get("value") is None:
            val = "<small>미평가</small>"
        else:
            # 척도가 과정마다 달라 % 를 함께 적는다 (1~5 와 1~10 을 나란히 읽기 위해)
            pct = (f'<small> · {me:.0f}%</small>'
                   if s.get("showPercent") and me is not None else "")
            val = f'{esc(it["value"])}<small>/{esc(mx)}</small>{pct}'
        avg_html = ""
        if av is not None:
            label = esc(it.get("groupAvgLabel") or f'평균 {it.get("groupAvg")}')
            avg_html = (f'<div class="avg" style="left:{_pct(av)}%"></div>'
                        f'<div class="avgl" style="left:{_pct(av)}%">{label}</div>')
        me_html = "" if me is None else f'<div class="me" style="left:{_pct(me)}%"></div>'
        memo = f'<div class="memo">{esc(it["memo"])}</div>' if it.get("memo") else ""

        out.append(
            f'<div class="crow{" spot" if it.get("spot") else ""}">'
            f'<div class="top"><span class="an">{esc(it.get("name"))}</span>'
            f'<span class="ad">{esc(it.get("desc") or "")}</span>'
            f'<span class="val">{val}</span></div>'
            f'<div class="track"><div class="rail"></div>{ticks}{avg_html}{me_html}</div>'
            f'<div class="scaleends"><span>{esc(mn)}</span><span>{esc(mx)}</span></div>'
            f'{memo}</div>')
    return head + "".join(out)


def _memorize(s: dict) -> str:
    """암기 문장 카드. 강사 교정 표현을 한 문장으로 엮은 것."""
    if is_blank(s.get("sentence")):
        return ""
    done = (f'<div class="done">✓ {esc(s["badge"])}</div>' if s.get("badge") else "")
    parts = ""
    if not is_blank(s.get("parts")):
        rows = []
        for p in s["parts"]:
            kind = p.get("kind") or "fix"
            label = {"fix": "✓ 권장 표현", "issue": "✕ 고칠 표현",
                     "key": "핵심 개념"}.get(kind, "출처")
            cls = {"fix": "fix", "issue": "bad", "key": "key"}.get(kind, "fix")
            quote = (f'<span class="q"><em class="{cls}">'
                     f'{esc(p["quote"])}</em></span> ' if p.get("quote") else "")
            rows.append(f'<div class="part"><span class="tag k-{esc(kind)}">{label}</span>'
                        f'<div>{quote}{esc(p.get("note") or "")}</div></div>')
        parts = f'<div class="parts">{"".join(rows)}</div>'
    close = (f'<div class="close">{s["closingHtml"]}</div>'
             if s.get("closingHtml") else "")
    return ('<div class="mcard">' + done +
            f'<div class="head">{esc(s.get("head") or "MEMORIZE BY NEXT SESSION")}</div>'
            f'<div class="say">{s["sentence"]}</div>'
            + parts + close + '</div>') + note_boxes(s.get("notes"))


def _relation(s: dict) -> str:
    if is_blank(s.get("rows")):
        return ""
    out = ""
    kl = s.get("keyline")
    if kl:
        out += (f'<div class="keyline"><div class="h">{esc(kl.get("title"))}</div>'
                f'<div class="b">{kl.get("html", "")}</div></div>')

    cols = s.get("columns") or []
    first_w = max(24, 100 - len(cols) * 20)
    head = (f'<th style="width:{first_w}%">{esc(s.get("rowHeader") or "역량")}</th>'
            + "".join(f'<th class="c" style="width:20%">{esc(c)}</th>' for c in cols))

    body = []
    for r in s["rows"]:
        cells = "".join(
            f'<td class="c"><span class="dot {esc(v.get("tone") or "mid")}">'
            f'{esc(v.get("value"))}</span></td>' for v in (r.get("values") or []))
        desc = f'<span>{esc(r["desc"])}</span>' if r.get("desc") else ""
        body.append(f'<tr><td class="nm">{esc(r.get("name"))}{desc}</td>{cells}</tr>')

    out += (f'<table class="maptable"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')
    if s.get("footnote"):
        out += f'<p class="footnote">{esc(s["footnote"])}</p>'
    return out + note_boxes(s.get("notes"))


def _narrative(s: dict) -> str:
    """강사 서술 블록. `runs`(원문) 또는 `html`(재작성본) 중 하나를 받는다."""
    blocks = s.get("blocks") or [{"runs": s.get("runs"), "tone": s.get("tone"),
                                  "html": s.get("html")}]
    out = []
    for b in blocks:
        body = b.get("html") or ("" if is_blank(b.get("runs")) else runs_html(b["runs"]))
        if not body:
            continue
        cls = "narr gap" if b.get("tone") == "gap" else "narr"
        out.append(f'<div class="{cls}">{body}</div>')
    if not out:
        return ""                       # 본문이 없으면 안내 박스만 남기지 않는다
    return "".join(out) + note_boxes(s.get("notes"))


def _themes(s: dict) -> str:
    if is_blank(s.get("items")):
        return ""
    out = []
    for t in s["items"]:
        cnt = f'<div class="cnt">{esc(t["count"])}</div>' if t.get("count") else ""
        out.append(f'<div class="theme"><div class="txt">'
                   f'{t.get("html") or esc(t.get("text"))}</div>{cnt}</div>')
    return "".join(out) + note_boxes(s.get("notes"))


def _fixnotes(s: dict) -> str:
    if is_blank(s.get("cards")):
        return ""
    out = []
    for c in s["cards"]:
        left_cls = "n" if c.get("neutral") else "x"
        left_label = esc(c.get("leftLabel") or ("지금의 습관" if c.get("neutral") else "✕ 이렇게 말고"))
        note = f'<div class="note"><b>왜</b>{esc(c["why"])}</div>' if c.get("why") else ""
        out.append(
            f'<div class="fixcard"><div class="row">'
            f'<div class="side {left_cls}"><div class="tagline">{left_label}</div>'
            f'<div class="txt">{c.get("leftHtml") or esc(c.get("left"))}</div></div>'
            f'<div class="side o"><div class="tagline">{esc(c.get("rightLabel") or "✓ 이렇게")}</div>'
            f'<div class="txt">{c.get("rightHtml") or esc(c.get("right"))}</div></div>'
            f'</div>{note}</div>')
    return f'<div class="fixgrid">{"".join(out)}</div>'


def _todo(s: dict) -> str:
    if is_blank(s.get("items")):
        return ""
    lis = []
    for i in s["items"]:
        sub = f'<span class="sub">{esc(i["sub"])}</span>' if i.get("sub") else ""
        lis.append(f'<li><span class="box"></span><div>'
                   f'{i.get("html") or esc(i.get("text"))}{sub}</div></li>')
    when = f'<span class="when">{esc(s["when"])}</span>' if s.get("when") else ""
    src = f'<div class="src">{esc(s["src"])}</div>' if s.get("src") else ""
    return (f'<div class="todo"><div class="head">'
            f'<span class="t">{esc(s.get("head") or "하나만 가져가신다면")}</span>{when}</div>'
            f'<ul>{"".join(lis)}</ul>{src}</div>')


def _legend(s: dict) -> str:
    items = [
        '<span class="it"><em class="bad">고칠 표현</em> 고쳐야 할 표현</span>',
        '<span class="it"><em class="fix">권장 표현</em> 대신 쓸 표현</span>',
        '<span class="it"><em class="key">핵심 개념</em> 기억할 개념</span>',
    ]
    if s.get("showDelta"):
        items.append('<span class="it"><span class="delta p">▲ 상승</span> · '
                     '<span class="delta z">― 유지</span> · '
                     '<span class="delta m">▼ 하락</span></span>')
    return f'<div class="legend">{"".join(items)}</div>'


def _note(s: dict) -> str:
    return note_boxes(s.get("notes"))


RENDERERS = {
    "glance": _glance, "growth": _growth, "scores": _scores, "relation": _relation,
    "narrative": _narrative, "themes": _themes, "fixnotes": _fixnotes,
    "todo": _todo, "memorize": _memorize, "legend": _legend, "note": _note,
}


def note_boxes(notes) -> str:
    if is_blank(notes):
        return ""
    out = []
    for n in notes:
        if isinstance(n, str):
            out.append(f'<div class="note-box">{n}</div>')
            continue
        title = f'<span class="h">{esc(n["title"])}</span>' if n.get("title") else ""
        cls = "note-box accent" if n.get("accent") else "note-box"
        out.append(f'<div class="{cls}">{title}{n.get("html", "")}</div>')
    return "".join(out)


# ══════════════════════════════════════════════════════════════
# 4. 문서 조립
# ══════════════════════════════════════════════════════════════

def render_sections(sections) -> str:
    no, prev_rendered = 0, False
    out = []
    for s in sections or []:
        fn = RENDERERS.get(s.get("kind"))
        if fn is None:
            raise ValueError(f"알 수 없는 섹션 kind: {s.get('kind')}")
        inner = fn(s)
        if not inner:
            continue                     # 내용이 비면 섹션째로 생략

        cls = []
        if s.get("tint"):
            cls.append("tint")
        if not prev_rendered:
            cls.append("first")
        elif s.get("mergeWithPrev"):
            cls.append("merge")
        prev_rendered = True

        head = ""
        if s.get("title"):
            no += 1
            lead = f'<p class="lead">{esc(s["lead"])}</p>' if s.get("lead") else ""
            head = (f'<h2><span class="no">{no:02d}</span>{esc(s["title"])}</h2>{lead}')

        style = ' style="padding-top:24px;padding-bottom:24px"' if s.get("compact") else ""
        cls_attr = f' class="{" ".join(cls)}"' if cls else ""
        out.append(f"<section{cls_attr}{style}>{head}{inner}</section>")
    return "\n".join(out)


def render_masthead(card: dict) -> str:
    m = card.get("meta") or {}
    p = card.get("person") or {}
    strip = ""
    if not is_blank(m.get("items")):
        cells = "".join(f'<div><span class="k">{esc(it.get("k"))}</span>'
                        f'<span class="v">{esc(it.get("v"))}</span></div>'
                        for it in m["items"])
        strip = f'<div class="metastrip">{cells}</div>'

    eyebrow = f'<div class="eyebrow">{esc(m["eyebrow"])}</div>' if m.get("eyebrow") else ""
    alias = f'<span class="alias">{esc(p["alias"])}</span>' if p.get("alias") else ""
    honorific = "" if p.get("honorific") is False else " 님"
    prog = f'<div class="prog">{esc(m["program"])}</div>' if m.get("program") else ""

    return (f'<div class="masthead">{eyebrow}'
            f'<h1>{esc(p.get("name"))}{honorific}{alias}</h1>{prog}</div>{strip}')


def render_footer(f) -> str:
    if not f:
        return ""
    brand = f'<span class="brand">{esc(f["team"])}</span>' if f.get("team") else ""
    lines = "<br>".join(esc(x) for x in (f.get("lines") or []))
    return f"<footer>{brand}{lines}</footer>"


def render(card: dict) -> str:
    """리포트 HTML 문서 전체를 만든다. card 스키마는 render/adapter.py 참고."""
    if not card or not (card.get("person") or {}).get("name"):
        raise ValueError("card.person.name 이 필요합니다")
    meta = card.get("meta") or {}
    title = card.get("title") or (
        f'{card["person"]["name"]} 님 — {meta.get("eyebrow") or "피드백 리포트"}')

    return ("<!DOCTYPE html>\n"
            '<html lang="ko"><head><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{esc(title)}</title>"
            f"<style>{CSS}</style></head>\n"
            '<body><div class="sheet">\n'
            + render_masthead(card) + "\n"
            + render_sections(card.get("sections")) + "\n"
            + render_footer(card.get("footer")) + "\n"
            "</div></body></html>")
