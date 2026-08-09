"""API 가 실제로 쓰였는가 — 증거를 보여 주는 곳.

호출이 실패하면 엔진은 조용히 목(mock) 문장으로 떨어진다. 리포트는 그대로
나오지만 정제도 번역도 익명화도 안 된 문장이 실린다. 화면만 보고는 알 수 없다.

그래서 문장 하나하나가 **무엇으로 만들어졌는지** 세어 보여 준다.
목이 하나라도 있으면 그 이유(마지막 오류)까지 함께 적는다.
"""
from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from config import MODEL, USE_LLM
from database import get_db
from models import Card

router = APIRouter(prefix="/usage", tags=["usage"])

# Sonnet 5 소개가 (100만 토큰당 달러). 바뀔 수 있으니 어림값으로만 쓴다.
PRICE = {"claude-sonnet-5": (2.0, 10.0), "claude-opus-5": (5.0, 25.0)}
KRW = 1400


def _collect(db: Session) -> dict:
    by_engine: Dict[str, int] = {}
    errors: List[dict] = []
    tin = tout = 0
    rules: Dict[str, Dict[str, int]] = {}

    for c in db.query(Card).all():
        for g in (c.card_json.get("generated") or []):
            eng = g.get("engine") or "unknown"
            by_engine[eng] = by_engine.get(eng, 0) + 1

            rid = g.get("rule_id") or "?"
            slot = rules.setdefault(rid, {"api": 0, "mock": 0})
            slot["mock" if eng == "mock" else "api"] += 1

            if g.get("error"):
                errors.append({"name": c.person_name, "rule": rid,
                               "reason": g["error"]})
            u = (g.get("extra") or {}).get("usage") or {}
            tin += int(u.get("input_tokens") or 0)
            tout += int(u.get("output_tokens") or 0)

    din, dout = PRICE.get(MODEL, (3.0, 15.0))
    cost = tin / 1e6 * din + tout / 1e6 * dout
    total = sum(by_engine.values())
    mock = by_engine.get("mock", 0)
    return {
        "mode": MODEL if USE_LLM else "mock (키 없음)",
        "sentences": total,
        "byEngine": by_engine,
        "mockCount": mock,
        "apiCount": total - mock,
        "byRule": rules,
        "tokens": {"input": tin, "output": tout},
        "costUsd": round(cost, 4),
        "costKrw": int(cost * KRW),
        "errors": errors[-10:],
        "note": ("실제 잔액은 console.anthropic.com/settings/billing 에서 "
                 "확인하십시오. 여기 값은 이 서버가 만든 것만 셉니다 "
                 "(서버가 다시 뜨면 0부터입니다)."),
    }


@router.get("")
def usage_json(db: Session = Depends(get_db)):
    return _collect(db)


PAGE = """<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>API 사용 현황</title><style>
body{font-family:'Malgun Gothic',sans-serif;background:#F0E8D6;color:#231D18;
     margin:0;padding:36px 16px 80px;line-height:1.7}
.s{max-width:680px;margin:0 auto;background:#fff;border:1px solid #E2D7C0;
   border-top:6px solid #DA1B33;border-radius:3px;padding:30px 34px}
h1{font-size:23px;margin:0 0 4px}
p.m{color:#6E655C;font-size:13px;margin:0 0 22px}
.big{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:22px}
.box{flex:1;min-width:130px;background:#FBF5E4;border:1px solid #E2D7C0;
     border-radius:6px;padding:14px 16px}
.box b{display:block;font-size:11.5px;color:#6E655C;font-weight:600}
.box span{font-size:21px;font-weight:700}
table{border-collapse:collapse;width:100%;font-size:13.5px;margin-top:6px}
td,th{border-bottom:1px solid #EFE8D9;padding:8px 6px;text-align:left}
th{font-size:11.5px;color:#6E655C}
.bad{background:#FBE9EA;border:1px solid #F0C9CE;border-radius:6px;
     padding:14px 16px;margin-top:18px;font-size:13px}
.bad b{color:#A00E22}
.ok{background:#E6F0EC;border:1px solid #CBDFD8;border-radius:6px;
    padding:14px 16px;margin-top:18px;font-size:13px;color:#0B6151}
a{color:#DA1B33;font-weight:700;text-decoration:none}
</style></head><body><div class=s>
<h1>API 사용 현황</h1>
<p class=m>__MODE__</p>
__BODY__
<p style="font-size:12.5px;color:#6E655C;margin-top:24px">__NOTE__</p>
<p style="font-size:12.5px;margin-top:14px">
<a href="/">← 처음으로</a> · <a href="/list">만들어진 리포트</a></p>
</div></body></html>"""


@router.get("/page", response_class=HTMLResponse)
def usage_page(db: Session = Depends(get_db)):
    d = _collect(db)
    esc = __import__("html").escape

    box = (f'<div class=big>'
           f'<div class=box><b>만든 문장</b><span>{d["sentences"]}건</span></div>'
           f'<div class=box><b>API 로 만든 것</b><span>{d["apiCount"]}건</span></div>'
           f'<div class=box><b>목으로 떨어진 것</b><span>{d["mockCount"]}건</span></div>'
           f'<div class=box><b>쓴 토큰</b>'
           f'<span>{d["tokens"]["input"] + d["tokens"]["output"]:,}</span></div>'
           f'<div class=box><b>어림 비용</b><span>{d["costKrw"]:,}원</span></div>'
           f'</div>')

    if d["sentences"] == 0:
        verdict = ('<div class=bad><b>아직 만든 문장이 없습니다.</b><br>'
                   '평가지를 올려 리포트를 만들어 보십시오.</div>')
    elif d["mockCount"] == 0:
        verdict = ('<div class=ok><b>전부 API 로 만들었습니다.</b> '
                   '말투 정제·번역·익명화가 모두 적용된 문장입니다.</div>')
    else:
        rows = "".join(
            f"<tr><td>{esc(e['name'])}</td><td>{esc(e['rule'])}</td>"
            f"<td>{esc(e['reason'])}</td></tr>" for e in d["errors"])
        verdict = (
            f'<div class=bad><b>{d["mockCount"]}건이 목(mock) 문장입니다.</b><br>'
            f'API 호출이 실패해 정제되지 않은 문장이 실렸습니다. 아래가 이유입니다.'
            f'</div>'
            + (f'<table><tr><th>대상</th><th>규칙</th><th>이유</th></tr>'
               f'{rows}</table>' if rows else
               '<p style="font-size:13px;color:#6E655C">이유가 기록되지 않았습니다 — '
               '이 리포트는 키를 넣기 전에 만들어졌을 수 있습니다.</p>'))

    byrule = "".join(
        f"<tr><td>{esc(k)}</td><td>{v['api']}건</td><td>{v['mock']}건</td></tr>"
        for k, v in sorted(d["byRule"].items()))
    table = (f'<h3 style="font-size:14px;margin:24px 0 4px">규칙별</h3>'
             f'<table><tr><th>규칙</th><th>API</th><th>목</th></tr>{byrule}</table>'
             if byrule else "")

    return HTMLResponse(
        PAGE.replace("__MODE__", f'생성 모델 — <b>{esc(d["mode"])}</b>')
            .replace("__BODY__", box + verdict + table)
            .replace("__NOTE__", esc(d["note"])))
