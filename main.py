"""HR AI Report Engine — 백엔드 진입점.

파이프라인:
    업로드 -> 스키마 인식 -> 정제(규칙 19개) -> 카드
          -> source_type 승인 -> 검수 관문(flags)
          -> 생성 위임(R-16 검사) -> 리포트 HTML -> 발송 매핑표
"""
from __future__ import annotations

import os
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

import auth
from config import AUTO_APPROVE, MODEL, USE_LLM, mode_banner
from database import Base, engine, ensure_columns
from routers import (cards, dashboard, handoff, insights, reports,
                     roster, uploads, usage)

Base.metadata.create_all(bind=engine)
ensure_columns()          # 이미 있는 표에 새로 생긴 열을 맞춰 준다
roster.seed_if_empty()    # 명부가 비어 있으면 seed/employees.csv 를 넣는다

app = FastAPI(
    title="HR AI Report Engine",
    version="0.6",
    description=("데이터 계약 v0.5 구현. 규칙 ID와 코드가 1:1로 대응합니다.\n\n"
                 f"현재 모드 — {mode_banner()}"),
)

# 인터넷에 올렸을 때의 첫 관문. 라우터보다 먼저 건다.
auth.install(app)

app.include_router(uploads.router)
app.include_router(cards.router)
app.include_router(handoff.router)
app.include_router(reports.router)
app.include_router(insights.router)
app.include_router(roster.router)
app.include_router(usage.router)
app.include_router(dashboard.router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    """예상 못 한 오류도 JSON 으로 돌려준다.

    기본 동작은 본문이 평문 'Internal Server Error' 라서, 화면에서는
    "not valid JSON" 이라는 엉뚱한 메시지만 보이고 진짜 원인이 가려진다.
    """
    tb = traceback.format_exc()
    print(tb)                                  # 터미널에는 전문을 남긴다
    return JSONResponse(status_code=500, content={
        "detail": f"{type(exc).__name__}: {exc}",
        "where": f"{request.method} {request.url.path}",
        "traceback": tb.strip().splitlines()[-6:],
    })


@app.get("/contract")
def contract_vocabulary():
    """UI 가 화면을 그리는 데 필요한 고정 어휘 전부.

    UI 트랙이 통합 명세 §5 에서 요청한 확인 사항(issueCode 고정 집합,
    severity 4종, 섹션 id)의 답이 이 한 번의 호출에 다 들어 있다.
    """
    import contract
    return contract.describe()


@app.get("/rules")
def rules():
    """구현된 정제 규칙 목록. 계약 규칙표와 대조하기 위한 엔드포인트."""
    from pipeline.rules import REGISTRY
    return [{
        "id": r.rule_id, "owner": r.owner,
        "problem": r.problem, "policy": r.policy,
        "needs_generation": r.needs_generation,
    } for r in sorted(REGISTRY.values(), key=lambda x: x.rule_id)]


@app.get("/health")
def health():
    """로그인 없이 열리는 유일한 길 — 호스팅이 서버 생사를 확인하는 데 쓴다.

    그래서 여기에는 자료를 담지 않는다. 켜져 있는가, 어떤 모드인가까지만.
    """
    return {"status": "ok", "auto_approve": AUTO_APPROVE,
            "generation": MODEL if USE_LLM else "mock",
            "auth": "on" if auth.enabled() else "local-only",
            # 화면 지문. 브라우저에서 본 것과 다르면 예전 판을 보고 있는 것이다.
            "web": web_build()}


# --------------------------------------------------------------------------
INDEX = """
<!doctype html><html lang=ko><head><meta charset=utf-8>
<title>HR AI Report Engine</title>
<style>
 :root{--red:#DA1B33;--ink:#231D18;--muted:#6E655C;--line:#E2D7C0;--cream:#FBF5E4}
 *{box-sizing:border-box}
 body{font-family:'Pretendard','Malgun Gothic',sans-serif;background:#F0E8D6;
      color:var(--ink);margin:0;padding:40px 16px 80px;line-height:1.7}
 .sheet{max-width:720px;margin:0 auto;background:#fff;border:1px solid var(--line);
        border-top:6px solid var(--red);border-radius:3px;padding:36px 40px}
 h1{font-size:28px;letter-spacing:-.03em;margin:0 0 6px}
 .mode{font-size:13px;color:var(--muted);margin-bottom:26px}
 .drop{border:2px dashed var(--line);border-radius:6px;padding:28px;text-align:center;
       background:var(--cream)}
 input[type=file]{font:inherit}
 button{font:inherit;font-weight:700;background:var(--red);color:#fff;border:0;
        border-radius:20px;padding:9px 22px;cursor:pointer;margin-top:14px}
 button[disabled]{opacity:.5;cursor:progress}
 .hint{display:block;font-size:12.5px;color:var(--muted);margin-top:6px}
 #out{margin-top:26px}
 .ftabs{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0 4px}
 .ftab{font:inherit;font-size:12.5px;font-weight:600;background:#fff;color:var(--ink-2);
       border:1px solid var(--line);border-radius:20px;padding:6px 14px;cursor:pointer}
 .ftab.on{background:var(--sk-red,var(--red));border-color:var(--red);color:#fff}
 .ftab .cnt{opacity:.65;margin-left:5px;font-weight:700}
 .row{display:flex;align-items:center;gap:12px;padding:11px 0;
      border-bottom:1px solid #EFE8D9}
 .row a{color:var(--red);font-weight:700;text-decoration:none}
 .nm{flex:1;font-weight:700}
 .blk{font-size:13px;color:var(--muted)}
 pre{background:var(--cream);border:1px solid var(--line);border-radius:4px;
     padding:14px;font-size:12.5px;overflow:auto;white-space:pre-wrap}
 .err{color:var(--red);font-weight:700}
</style></head><body>
<div class=sheet>
  <h1>HR AI 리포트 엔진</h1>
  <div class=mode>__MODE__</div>

  <form id=f class=drop>
    <div>평가지(.xlsx)를 올리면 리포트까지 한 번에 만듭니다.
      <span class=hint>여러 개를 한 번에 고를 수 있습니다 — 1차수와 2차수를 함께 올리면 성장 비교가 붙습니다.</span></div>
    <input type=file name=file accept=".xlsx,.xlsm" multiple required>
    <br><button id=b type=submit>업로드하고 리포트 만들기</button>
  </form>

  <div id=out></div>
  <p style="font-size:13px;color:var(--muted);margin-top:30px">
    <a href="/dashboard">HRD 대시보드 →</a> ·
    API 계약서: <a href="/docs">/docs</a> · 규칙 목록: <a href="/rules">/rules</a>
  </p>
</div>
<script>
const f=document.getElementById('f'),out=document.getElementById('out'),b=document.getElementById('b');
const esc=s=>String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
f.addEventListener('submit',async e=>{
  e.preventDefault(); b.disabled=true; out.innerHTML='<p>처리 중…</p>';
  try{
    const r=await fetch('/uploads',{method:'POST',body:new FormData(f)});
    const raw=await r.text();
    let d;
    try{ d=JSON.parse(raw); }
    catch(_){
      // 서버가 JSON 이 아닌 걸 돌려준 경우 — 원문을 그대로 보여 준다
      out.innerHTML='<p class=err>서버 오류 (HTTP '+r.status+')</p><pre>'+
        raw.slice(0,1200).replace(/</g,'&lt;')+'</pre>'; return;
    }
    if(!r.ok){
      let h='<p class=err>'+(d.detail||('HTTP '+r.status))+'</p>';
      if(d.traceback) h+='<pre>'+d.traceback.join('\\n').replace(/</g,'&lt;')+'</pre>';
      out.innerHTML=h; return;
    }
    render(d);
  }catch(err){out.innerHTML='<p class=err>'+err+'</p>';}
  finally{b.disabled=false;}
});

// 파일이 하나면 목록만, 여럿이면 파일 탭을 두고 골라 보게 한다.
let LAST=null;
function render(d){
  LAST=d;
  const ups=d.uploads||[];
  const kinds=Object.entries(d.by_source_type||{}).map(([k,v])=>k+' '+v+'명').join(' · ');
  const made=(d.reports||[]).filter(x=>x.report_id).length;
  let h='<p><b>파일 '+ups.length+'개 · 카드 '+d.cards+'장</b>'+
        (kinds?' · '+kinds:'')+' · 리포트 '+made+'편</p>';
  if(ups.length>1){
    h+='<div class=ftabs>'+ups.map((u,i)=>
      '<button class="ftab'+(i===0?' on':'')+'" data-i="'+i+'">'+
      esc(u.filename)+' <span class=cnt>'+
      ((u.reports||[]).filter(r=>r.report_id).length)+'</span></button>').join('')+'</div>';
  }
  h+='<div id=flist></div>';
  out.innerHTML=h;
  out.querySelectorAll('.ftab').forEach(t=>t.onclick=()=>{
    out.querySelectorAll('.ftab').forEach(x=>x.classList.remove('on'));
    t.classList.add('on');
    showFile(+t.dataset.i);
  });
  showFile(0);
}

function showFile(i){
  const u=(LAST.uploads||[])[i]; if(!u) return;
  const list=document.getElementById('flist');
  if(u.error){ list.innerHTML='<p class=err>'+esc(u.error)+'</p>'; return; }
  const rows=(u.reports||[]).map(x=>
    '<div class=row><span class=nm>'+esc(x.name)+'</span>'+
    (x.report_id? '<a href="'+x.html+'" target=_blank>리포트 열기 ↗</a>'
                : '<span class=blk>'+esc(x.blocked_by)+'</span>')+'</div>').join('');
  let h=rows||'<p class=blk>이 파일에서 만들어진 리포트가 없습니다.</p>';
  if(u.upload_id) h+='<div class=row><button id=send data-u="'+u.upload_id+'">'+
      '본인 메일로 보내기</button><span id=sendmsg class=blk></span></div>';
  list.innerHTML=h;
  const b=document.getElementById('send');
  if(b) b.onclick=()=>sendMail(b);
}

// 발송은 버튼을 눌러야만 나간다. 업로드만으로는 아무에게도 가지 않는다.
async function sendMail(b){
  const msg=document.getElementById('sendmsg');
  b.disabled=true; msg.textContent=' 보내는 중…';
  try{
    const r=await fetch('/reports/send/upload/'+b.dataset.u,{method:'POST'});
    const d=await r.json();
    if(!r.ok){ msg.textContent=' '+(d.detail||'실패'); return; }
    const dry=(d.results||[]).some(x=>x.dry_run);
    msg.textContent=dry
      ? ' 미리보기 — '+(d.mail&&d.mail.note||'')+' (실제로 보내지 않았습니다)'
      : ' '+d.sent+'/'+d.total+'명에게 보냈습니다';
    const bad=(d.results||[]).filter(x=>!x.sent&&!x.dry_run);
    if(bad.length) msg.textContent+=' · 실패 '+bad.length+'건: '+esc(bad[0].reason||'');
  }catch(e){ msg.textContent=' '+e; }
  finally{ b.disabled=false; }
}
</script></body></html>
"""


WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "index.html")

# 브라우저가 화면을 붙들고 있으면 고쳐서 올려도 예전 것이 계속 보인다.
# 그 상태로는 "고쳤는데 왜 그대로냐"를 몇 번이고 되풀이하게 된다.
NO_CACHE = {"Cache-Control": "no-store, must-revalidate", "Pragma": "no-cache"}


def web_build() -> str:
    """지금 서버가 들고 있는 화면의 지문. 어느 판이 떠 있는지 확인용."""
    if not os.path.exists(WEB):
        return "none"
    import hashlib
    with open(WEB, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:8]


@app.get("/", response_class=HTMLResponse)
def index():
    """첫 화면. UI 프로토타입이 빌드돼 있으면 그것을, 없으면 한 장짜리를 준다.

    프로토타입은 `tools/build_web.py` 가 만든다. 없어도 서버는 온전히 돌아야
    한다 — 프론트가 없다고 엔진을 못 쓰게 되면 곤란하다.
    """
    if os.path.exists(WEB):
        return HTMLResponse(open(WEB, encoding="utf-8").read(), headers=NO_CACHE)
    return HTMLResponse(INDEX.replace("__MODE__", mode_banner()), headers=NO_CACHE)


LIST_PAGE = """<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>만들어진 리포트</title><style>
body{font-family:'Malgun Gothic',sans-serif;background:#F0E8D6;color:#231D18;
     margin:0;padding:36px 16px 80px;line-height:1.7}
.s{max-width:820px;margin:0 auto;background:#fff;border:1px solid #E2D7C0;
   border-top:6px solid #DA1B33;border-radius:3px;padding:30px 34px}
h1{font-size:23px;margin:0 0 4px}
p.m{color:#6E655C;font-size:13px;margin:0 0 20px}
h2{font-size:15px;margin:26px 0 8px;padding-top:16px;border-top:1px solid #EFE8D9}
h2:first-of-type{border-top:0;padding-top:0}
h2 span{font-weight:400;font-size:12.5px;color:#6E655C;margin-left:8px}
table{border-collapse:collapse;width:100%;font-size:13.5px}
td,th{border-bottom:1px solid #EFE8D9;padding:9px 6px;text-align:left}
th{font-size:11.5px;color:#6E655C;font-weight:600}
a{color:#DA1B33;font-weight:700;text-decoration:none}
.no{color:#6E655C;font-weight:400}
.empty{background:#FBF5E4;border:1px dashed #E2D7C0;border-radius:6px;
       padding:26px;text-align:center;color:#6E655C;font-size:13.5px}
.fname{font-size:12.5px;font-weight:700;color:#4A423A;margin:14px 0 2px;
       background:#FBF5E4;border-radius:4px;padding:6px 10px}
.fname span{font-weight:400;color:#6E655C;margin-left:6px}
</style></head><body><div class=s>
<h1>만들어진 리포트</h1>
<p class=m>화면을 새로 고쳐도 여기는 그대로입니다. 링크를 열면 리포트가 뜨고,
브라우저에서 <b>Ctrl+P → PDF로 저장</b> 하면 파일로 받을 수 있습니다.<br>
<b>문장</b> 칸이 <b>AI</b> 면 말투 정제·번역·익명화가 적용된 문장이고,
<b>목</b> 이면 API 호출이 실패해 정제되지 않은 것입니다.</p>
__BODY__
<p style="font-size:12.5px;color:#6E655C;margin-top:26px">
<a href="/">← 처음으로</a> · <a href="/usage/page">API 사용 현황</a> ·
<a href="/roster/setup">사원 명부</a> · <a href="/simple">간단 업로드</a></p>
</div></body></html>"""


@app.get("/list", response_class=HTMLResponse)
def report_list():
    """만들어진 리포트 전부. 화면 상태와 상관없이 언제든 열린다.

    프로토타입은 새로 고치면 처음 화면으로 돌아간다 — 화면이 기억을 갖고
    있지 않기 때문이다. 그런데 리포트는 서버에 남아 있다. 그것을 볼 길이
    화면 안에만 있으면, 새로 고침 한 번에 방금 만든 것을 못 찾게 된다.
    """
    from models import Card, Course, Report
    from pipeline.rules.base import is_sendable
    from database import SessionLocal

    db = SessionLocal()
    try:
        cards = db.query(Card).order_by(Card.id.desc()).all()
        titles = {c.course_id: c.title for c in db.query(Course).all()}
        groups: dict = {}
        for c in cards:
            cid = (c.card_json.get("context") or {}).get("_course_id")
            key = titles.get(cid) or c.course_name or "과정 미상"
            groups.setdefault(key, []).append(c)

        if not groups:
            body = ('<div class=empty>아직 만들어진 리포트가 없습니다.<br>'
                    '<a href="/">처음 화면</a>에서 평가지를 올려 주세요.</div>')
        else:
            parts = []
            for title, rows in groups.items():
                made = sum(1 for c in rows if c.report)
                # 과정 안에서 다시 파일별로 묶는다. 여러 개를 함께 올리면
                # 한 줄로 이어져 어느 파일 사람인지 알 수 없다.
                byfile: dict = {}
                for c in rows:
                    byfile.setdefault(c.source_file or "파일 미상", []).append(c)

                blocks = []
                for fname, group in byfile.items():
                    trs = []
                    for c in group:
                        ok, why = is_sendable(c.card_json)
                        link = (f'<a href="/reports/{c.report.id}/html" '
                                f'target=_blank>리포트 열기 ↗</a>'
                                if c.report else '<span class=no>아직 없음</span>')
                        send = ("보낼 수 있음" if ok
                                else f'<span class=no>{_esc(why)}</span>')
                        gen = c.card_json.get("generated") or []
                        mock = sum(1 for g in gen if g.get("engine") == "mock")
                        made_by = ("—" if not gen else
                                   ("AI" if not mock else
                                    f'<span class=no>목 {mock}/{len(gen)}</span>'))
                        who = c.card_json.get("person") or {}
                        trs.append(f"<tr><td>{_esc(c.person_name)}</td>"
                                   f"<td>{_esc(who.get('position') or '')}</td>"
                                   f"<td>{_esc(c.person_id or '')}</td>"
                                   f"<td>{_esc(c.round_label or '')}</td>"
                                   f"<td>{made_by}</td>"
                                   f"<td>{link}</td><td>{send}</td></tr>")
                    head = (f'<div class=fname>{_esc(fname)} '
                            f'<span>{len(group)}명</span></div>'
                            if len(byfile) > 1 else "")
                    blocks.append(
                        head + f"<table><tr><th>이름</th><th>직급</th>"
                        f"<th>사번</th><th>차수</th><th>문장</th>"
                        f"<th>리포트</th><th>발송</th></tr>{''.join(trs)}</table>")

                parts.append(
                    f"<h2>{_esc(title)}<span>{made}/{len(rows)}편</span></h2>"
                    + "".join(blocks))
            body = "".join(parts)
        return HTMLResponse(LIST_PAGE.replace("__BODY__", body), headers=NO_CACHE)
    finally:
        db.close()


def _esc(s) -> str:
    import html
    return html.escape(str(s or ""))


@app.get("/favicon.ico")
def favicon():
    """탭 아이콘. 없으면 브라우저가 요청마다 404 를 콘솔에 남겨,
    정작 봐야 할 오류가 그 사이에 묻힌다."""
    from fastapi.responses import Response
    # 빨간 사각형 하나짜리 SVG — 파일을 따로 두지 않는다
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
           '<rect width="32" height="32" rx="6" fill="#DA1B33"/>'
           '<rect x="7" y="8" width="18" height="3" rx="1.5" fill="#fff"/>'
           '<rect x="7" y="15" width="18" height="3" rx="1.5" fill="#fff"/>'
           '<rect x="7" y="22" width="11" height="3" rx="1.5" fill="#fff"/>'
           "</svg>")
    return Response(svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/simple", response_class=HTMLResponse)
def simple():
    """한 장짜리 업로드 화면. 프론트가 말썽일 때 여기로 우회한다."""
    return HTMLResponse(INDEX.replace("__MODE__", mode_banner()))
