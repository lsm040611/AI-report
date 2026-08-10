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
from typing import Any, Dict, List, Optional

# ══════════════════════════════════════════════════════════════
# 1. 스타일 — 시안 v2 확정본
# ══════════════════════════════════════════════════════════════
CSS = """
@import url('https://cdn.jsdelivr.net/gh/wanteddev/wanted-sans@v1.0.3/packages/wanted-sans/fonts/webfonts/variable/complete/WantedSansVariable.min.css');
/* 화면(프로토타입)과 같은 서체·색을 쓴다. 리포트만 따로 놀면 같은 제품으로
   보이지 않는다. 토큰 **이름**은 그대로 두고 값만 갈아끼웠다 — 아래 규칙
   163개가 전부 이 이름을 참조하고 있어서, 이름을 바꾸면 다 고쳐야 한다.
   인터넷이 안 되면 서체만 시스템 것으로 떨어지고 나머지는 그대로다. */
:root{
  --sk-red:#EA002C;          /* 포인트 — 선·배지·숫자에만 (프론트 action-red) */
  --sk-red-deep:#B80023;
  --sk-red-soft:#FDE9ED;
  --butter:#FAFAFC;          /* 머리부 · 강조 패널 (pearl) */
  --butter-2:#F2F2F5;        /* 패널 안쪽 톤 */
  --cream:#FAFAFC;           /* 섹션 배경 */
  --cream-2:#F5F5F7;         /* 칩 · 보조 배경 (parchment) */
  --page:#F5F5F7;            /* 지면 바깥 */
  --paper:#FFFFFF;
  --gold:#F47725;            /* 강점 표시 (프론트 sub-orange) */
  --ink:#1B1B1D;
  --ink-2:#48484D;
  --muted:#78787E;
  --faint:#AFAFB5;
  --line:#E4E4E7;
  --line-2:#F0F0F2;
  --up:#1C8A53;   --up-soft:#E8F4EE;  --up-line:#C9E3D5;
  --down:#B80023; --down-soft:#FDE9ED;
  --warn:#B4540F; --warn-soft:#FDF1E7; --warn-line:#F3D6BC;
  --sans:'Wanted Sans','Noto Sans KR','Pretendard',-apple-system,BlinkMacSystemFont,'Segoe UI','Malgun Gothic','맑은 고딕',sans-serif;
  --mono:'IBM Plex Mono','SFMono-Regular',Consolas,'D2Coding','Menlo',monospace;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:var(--sans);background:var(--page);color:var(--ink);
  font-size:15.5px;line-height:1.75;-webkit-font-smoothing:antialiased;padding:34px 16px 60px}
.sheet{max-width:780px;margin:0 auto;background:var(--paper);
  border:1px solid var(--line);border-radius:14px;overflow:hidden;
  box-shadow:0 1px 3px rgba(0,0,0,.04),0 8px 28px rgba(0,0,0,.05)}

/* ── 머리부 ── */
.masthead{background:var(--butter);border-top:6px solid var(--sk-red);padding:32px 46px 28px}
.masthead .eyebrow{display:inline-block;font-size:12px;font-weight:700;letter-spacing:.1em;
  color:var(--sk-red);background:var(--paper);border:1px solid #F0D3D6;padding:3px 12px;border-radius:20px}
.masthead h1{font-size:42px;font-weight:800;letter-spacing:-.035em;margin:14px 0 6px;
  line-height:1.1;color:var(--ink)}
.masthead h1 .alias{font-size:19px;font-weight:500;color:var(--faint);margin-left:11px;letter-spacing:0}
.masthead .where{font-size:13.5px;color:var(--muted);margin:0 0 4px;
  display:flex;flex-wrap:wrap;align-items:center;gap:0 9px}
/* 소속 · 직급 · 사번 사이의 가운뎃점. 글자로 넣으면 하나가 비었을 때
   점만 덩그러니 남는다 — 앞에 무엇이 있을 때만 그린다. */
.masthead .where span + span::before{content:'·';margin-right:9px;color:var(--line)}
.masthead .where .eid{font-family:var(--mono);font-size:12.5px;
  letter-spacing:.02em;color:var(--faint)}
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
/* 차트는 본문보다 좁게 둔다. 폭을 꽉 채우면 그림 하나가 한 화면을 덮어
   정작 읽어야 할 점수·코멘트가 밀린다. */
.radarwrap{margin:0 auto;padding:2px 0 0;max-width:400px}
.radar-legend{justify-content:center;gap:10px 22px;margin:10px 0 6px;font-size:12.5px;color:var(--muted)}
.radar-legend .it{gap:7px}
.radar-note{font-size:12.5px;color:var(--muted);text-align:center;margin:0 auto 22px;max-width:420px;line-height:1.6}

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
.gtable td.n.faint{color:var(--faint)}
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
/* 강조 3종. 클래스명은 뜻이고 색은 여기서만 정한다 — UI 에 얹을 때는 이 세 줄을
   공통 디자인 시스템 토큰으로 갈아끼우면 되고, 본문 HTML 은 손대지 않아도 된다. */
em.em-issue-expression{font-style:normal;color:var(--sk-red-deep);background:#FBD9DF;
  padding:1px 4px;border-radius:2px;font-weight:600}
em.em-corrected-expression{font-style:normal;font-weight:700;color:var(--up);
  background:#CFE7E0;padding:1px 4px;border-radius:2px}
em.em-key-concept{font-style:normal;font-weight:700;background:#FBEDBE;
  padding:1px 4px;border-radius:2px}

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
/* 원어 표현의 뜻 — 원문 아래, 원문보다 작게 */
.fixcard .ko{margin-top:7px;font-size:13px;line-height:1.6;font-weight:400;
  color:var(--ink-2);word-break:keep-all}
.fixcard .side.x .ko{color:var(--muted)}
.fixcard .ko::before{content:'뜻 ';font-size:11px;font-weight:700;
  color:var(--muted);letter-spacing:.02em}
/* 줄임말 풀이 — 배울 문구보다 눈에 띄면 안 된다 */
.fixcard .gloss{padding:10px 20px;border-top:1px solid var(--line);
  background:var(--paper);display:flex;flex-wrap:wrap;gap:6px 18px}
.fixcard .gt{font-size:12.5px;color:var(--muted);line-height:1.6}
.fixcard .gt b{font-weight:700;color:var(--ink-2);margin-right:6px;
  font-family:var(--mono);font-size:12px}
.fixcard .gt b::after{content:'=';margin-left:6px;color:var(--muted);
  font-family:var(--sans);font-weight:400}

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

# 강조 서식 키 매핑 — 파서(R-05)가 내는 키와 짧은 키 둘 다 받는다.
#
# 클래스명이 색이 아니라 **뜻**인 것이 요점이다. 통합 명세 §2-③ 이 요구한다 —
# 엔진은 인라인 색상을 넣지 않고, 색은 공통 디자인 시스템 토큰이 정한다.
# 그래야 UI 에 얹었을 때와 PDF 로 뽑았을 때의 서식이 같아진다.
EMPHASIS = {
    "issue_expression": "em-issue-expression",       # 원본 붉은색    → 고칠 표현
    "corrected_expression": "em-corrected-expression",  # 굵게+밑줄   → 권장 표현
    "key_concept": "em-key-concept",                 # 원본 굵게      → 핵심 개념
    "bad": "em-issue-expression",
    "fix": "em-corrected-expression",
    "key": "em-key-concept",
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
                    f'<td class="n faint">{fmt(r.get("curr"), digits)}</td></tr>')
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
            cls = EMPHASIS.get({"issue": "bad"}.get(kind, kind), EMPHASIS["fix"])
            quote = (f'<span class="q"><em class="{cls}">'
                     f'{esc(p["quote"])}</em></span> ' if p.get("quote") else "")
            rows.append(f'<div class="part"><span class="tag k-{esc(kind)}">{label}</span>'
                        f'<div>{quote}{esc(p.get("note") or "")}</div></div>')
        parts = f'<div class="parts">{"".join(rows)}</div>'
    close = (f'<div class="close">{s["closingHtml"]}</div>'
             if s.get("closingHtml") else "")
    return ('<div class="mcard">' + done +
            f'<div class="head">{esc(s.get("head") or "MEMORIZE BY NEXT SESSION")}</div>'
            f'<div class="say"{sid_attr(s)}>{s["sentence"]}</div>'
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
                                  "html": s.get("html"), "sid": s.get("sid")}]
    out = []
    for b in blocks:
        body = b.get("html") or ("" if is_blank(b.get("runs")) else runs_html(b["runs"]))
        if not body:
            continue
        cls = "narr gap" if b.get("tone") == "gap" else "narr"
        out.append(f'<div class="{cls}"{sid_attr(b)}>{body}</div>')
    if not out:
        return ""                       # 본문이 없으면 안내 박스만 남기지 않는다
    return "".join(out) + note_boxes(s.get("notes"))


def _themes(s: dict) -> str:
    if is_blank(s.get("items")):
        return ""
    out = []
    for t in s["items"]:
        cnt = f'<div class="cnt">{esc(t["count"])}</div>' if t.get("count") else ""
        out.append(f'<div class="theme"{sid_attr(t)}><div class="txt">'
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
        # 줄임말 풀이. 교정 표현 아래에 조용히 붙인다 — 배울 문구보다
        # 눈에 띄면 무엇이 본론인지 흐려진다.
        terms = ""
        if c.get("terms"):
            items = "".join(
                f'<span class="gt"><b>{esc(t["term"])}</b>'
                f'{esc(t["meaning"])}</span>' for t in c["terms"])
            terms = f'<div class="gloss">{items}</div>'
        # 영어 표현은 원어로 둔다 — 말하라고 가르친 문장이라 바꾸면 수업이
        # 사라진다. 대신 뜻을 바로 아래 적는다. 영어가 익숙하지 않은 사람은
        # 뜻이 없으면 무엇을 외우라는 것인지 알 수 없다.
        def ko(v):
            return f'<div class="ko">{esc(v)}</div>' if v else ""

        out.append(
            f'<div class="fixcard"><div class="row">'
            f'<div class="side {left_cls}"><div class="tagline">{left_label}</div>'
            f'<div class="txt">{c.get("leftHtml") or esc(c.get("left"))}</div>'
            f'{ko(c.get("leftKo"))}</div>'
            f'<div class="side o"><div class="tagline">{esc(c.get("rightLabel") or "✓ 이렇게")}</div>'
            f'<div class="txt">{c.get("rightHtml") or esc(c.get("right"))}</div>'
            f'{ko(c.get("rightKo"))}</div>'
            f'</div>{terms}{note}</div>')
    return f'<div class="fixgrid">{"".join(out)}</div>'


def _todo(s: dict) -> str:
    if is_blank(s.get("items")):
        return ""
    lis = []
    for i in s["items"]:
        sub = f'<span class="sub">{esc(i["sub"])}</span>' if i.get("sub") else ""
        lis.append(f'<li{sid_attr(i)}><span class="box"></span><div>'
                   f'{i.get("html") or esc(i.get("text"))}{sub}</div></li>')
    when = f'<span class="when">{esc(s["when"])}</span>' if s.get("when") else ""
    src = f'<div class="src">{esc(s["src"])}</div>' if s.get("src") else ""
    return (f'<div class="todo"><div class="head">'
            f'<span class="t">{esc(s.get("head") or "하나만 가져가신다면")}</span>{when}</div>'
            f'<ul>{"".join(lis)}</ul>{src}</div>')


def _legend(s: dict) -> str:
    items = []
    if s.get("showMarks", True):
        items += [
            f'<span class="it"><em class="{EMPHASIS["bad"]}">고칠 표현</em> 고쳐야 할 표현</span>',
            f'<span class="it"><em class="{EMPHASIS["fix"]}">권장 표현</em> 대신 쓸 표현</span>',
            f'<span class="it"><em class="{EMPHASIS["key"]}">핵심 개념</em> 기억할 개념</span>',
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


def sid_attr(d: dict) -> str:
    """AI 가 만든 문장에 붙는 표식.

    UI 검수 화면이 이 속성으로 문장을 집어 근거를 조회한다
    (GET /reports/{id}/evidence). 사람이 쓴 원문에는 붙지 않는다 —
    붙어 있으면 "이 문장은 생성물"이라는 뜻이다.
    """
    sid = d.get("sid")
    return f' data-sentence-id="{esc(sid)}"' if sid else ""


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
        out.append(f'<div class="{cls}"{sid_attr(n)}>{title}{n.get("html", "")}</div>')
    return "".join(out)


# ══════════════════════════════════════════════════════════════
# 4. 문서 조립
# ══════════════════════════════════════════════════════════════

# 섹션 kind → UI 목차의 4개 묶음 (통합 명세 §2-③).
# UI 의 스크롤 스파이가 data-section 을 읽고, 순서가 곧 목차 순서다.
SECTION_GROUP = {
    "glance": "items", "scores": "items", "relation": "items",
    "narrative": "feedback", "themes": "feedback", "fixnotes": "feedback",
    "growth": "compare",
    "todo": "next", "memorize": "next", "legend": "next", "note": "next",
}
GROUP_ORDER = ("items", "feedback", "compare", "next")
GROUP_LABEL = {"items": "항목별 평가", "feedback": "서술 피드백",
               "compare": "성장 비교", "next": "다음 학습 제안"}


def group_sections(sections) -> Dict[str, list]:
    """섹션을 묶음별로 가른다. 묶음 안의 순서는 넣은 순서 그대로다."""
    buckets: Dict[str, list] = {g: [] for g in GROUP_ORDER}
    for s in sections or []:
        buckets[SECTION_GROUP.get(s.get("kind"), "items")].append(s)
    return buckets


def render_sections(sections, anchors: bool = True) -> str:
    """묶음마다 `<div data-section>` 을 두르고 그 안에 섹션을 넣는다.

    묶음이 비면 껍데기도 만들지 않는다 — 통합 명세가 빈 섹션을 금지한다.
    이전 회차가 없으면 compare 묶음이 통째로 사라지고, UI 는 목차에서도
    그 항목을 지운다.
    """
    buckets = group_sections(sections)
    no, prev_rendered = 0, False
    out = []

    for gid in GROUP_ORDER:
        chunk = []
        for s in buckets[gid]:
            fn = RENDERERS.get(s.get("kind"))
            if fn is None:
                raise ValueError(f"알 수 없는 섹션 kind: {s.get('kind')}")
            inner = fn(s)
            if not inner:
                continue                 # 내용이 비면 섹션째로 생략

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
                head = f'<h2><span class="no">{no:02d}</span>{esc(s["title"])}</h2>{lead}'

            style = (' style="padding-top:24px;padding-bottom:24px"'
                     if s.get("compact") else "")
            cls_attr = f' class="{" ".join(cls)}"' if cls else ""
            chunk.append(f"<section{cls_attr}{style}>{head}{inner}</section>")

        if not chunk:
            continue
        body = "\n".join(chunk)
        out.append(f'<div data-section="{gid}">\n{body}\n</div>' if anchors else body)
    return "\n".join(out)


def toc(sections) -> List[dict]:
    """실제로 렌더된 묶음만 추린 목차. UI 사이드바가 그대로 쓴다."""
    buckets = group_sections(sections)
    return [{"id": g, "label": GROUP_LABEL[g]} for g in GROUP_ORDER
            if any(RENDERERS[s["kind"]](s) for s in buckets[g])]


# ══════════════════════════════════════════════════════════════
# 5. 스타일 가두기 — 남의 페이지 안에 본문을 넣을 때
# ══════════════════════════════════════════════════════════════
def scoped_css(prefix: str) -> str:
    """모든 규칙 앞에 `prefix` 를 붙여, 이 스타일이 바깥으로 새지 않게 한다.

    본문을 UI 페이지 안에 끼워 넣을 때 필요하다. 우리 CSS 에는 `section`,
    `h2`, `*` 같은 넓은 선택자가 있어서, 그대로 얹으면 UI 의 다른 곳까지
    같이 바뀐다. 실제로 한 번 얹어 보고 알았다 — 사이드바 글자가 리포트
    서체로 변했다.

    `:root` 는 변수를 담고 있으니 prefix 자체로 바꾼다. 변수는 상속되므로
    안쪽 요소는 그대로 값을 읽는다.
    """
    return _prefix_block(CSS, prefix.strip())


def _prefix_block(css: str, prefix: str) -> str:
    out, i, n = [], 0, len(css)
    while i < n:
        brace = css.find("{", i)
        if brace < 0:
            out.append(css[i:])
            break
        head = css[i:brace]
        end = _match_brace(css, brace)
        body = css[brace + 1:end]

        sel = head.strip()
        lead = head[:len(head) - len(head.lstrip())]
        if sel.startswith("@media") or sel.startswith("@supports"):
            out.append(f"{lead}{sel}{{{_prefix_block(body, prefix)}}}")
        elif sel.startswith("@"):
            out.append(f"{lead}{sel}{{{body}}}")          # @font-face 등은 그대로
        else:
            out.append(f"{lead}{_prefix_selector(sel, prefix)}{{{body}}}")
        i = end + 1
    return "".join(out)


def _match_brace(css: str, open_at: int) -> int:
    depth = 0
    for j in range(open_at, len(css)):
        if css[j] == "{":
            depth += 1
        elif css[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return len(css) - 1


def _prefix_selector(sel: str, prefix: str) -> str:
    parts = []
    for one in sel.split(","):
        s = one.strip()
        if not s:
            continue
        if s in (":root", "html", "body"):
            parts.append(prefix)                          # 변수는 상속으로 내려간다
        elif s.startswith(prefix):
            parts.append(s)                               # 이미 가둬져 있다
        else:
            parts.append(f"{prefix} {s}")
    return ",".join(parts)


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
    prog = f'<div class="prog">{esc(m["program"])}</div>' if m.get("program") else ""

    # 직급이 있으면 '강지우 대리님', 없으면 '강지우 님'.
    # 직급을 아는데도 이름만 부르면 사내 문서에서는 어색하다.
    #
    # 이름이 비면 '님' 한 글자만 남는다. 그건 받는 사람에게 아무 뜻이 없고,
    # 무엇이 잘못됐는지도 알려 주지 않는다. 그럴 땐 모른다고 말한다.
    name = (p.get("name") or "").strip()
    if not name:
        title = "이름 미상"
    elif p.get("honorific") is False:
        title = esc(name)
    elif p.get("position"):
        title = f'{esc(name)} {esc(p["position"])}님'
    else:
        title = f'{esc(name)} 님'

    # 소속 · 직급 · 사번. 같은 이름이 둘일 때 이 리포트가 누구 것인지
    # 종이 위에서 가릴 수 있는 것은 사번뿐이다. 제목의 '대리님' 은 호칭이라
    # 직급을 한 번 더 또박또박 적어 둔다 — 인쇄해서 돌릴 때 필요하다.
    bits = []
    if p.get("team"):
        bits.append(f'<span>{esc(p["team"])}</span>')
    if p.get("position"):
        bits.append(f'<span>{esc(p["position"])}</span>')
    if p.get("empId"):
        bits.append(f'<span class="eid">{esc(p["empId"])}</span>')
    where = f'<div class="where">{"".join(bits)}</div>' if bits else ""
    return (f'<div class="masthead">{eyebrow}'
            f'<h1>{title}{alias}</h1>{where}{prog}</div>{strip}')


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
