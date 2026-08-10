"""사원 마스터 — 평가지에 없는 사번·이메일이 오는 곳.

평가지에는 이름만 적혀 있다. 리포트를 본인에게 보내려면 "이 이름이 누구인가"를
답해 줄 무언가가 필요하고, 실제 회사에서는 인사 시스템이 그 역할을 한다.
지금은 그 자리에 CSV 한 장을 세운다.

**동명이인일 때 여기서 한 명을 고르지 않는다.** 후보를 전부 돌려준다.
먼저 골라 버리면 오발송이 조용히 일어나고 아무도 모른다 — 누구인지 정하는 것은
R-15 와 담당자의 몫이다.
"""
from __future__ import annotations

import csv
import io
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from database import get_db
from models import RosterEntry

router = APIRouter(prefix="/roster", tags=["roster"])

# 인사 시스템이 내보내는 컬럼명 ↔ 우리 컬럼. 왼쪽을 바꾸면 다른 양식도 받는다.
COLUMNS = {
    "employee_id": "person_id",
    "name_ko": "name",
    "alias_en": "alias",
    "email": "email",
    "division": "department",
    "team": "team",
    "position": "position",
    "employment_status": "status",
    "manager_id": "manager_id",
    "note": "note",
}
# 흔히 다르게 적히는 이름들도 받아 준다
ALIASES = {
    "사번": "person_id", "employeeid": "person_id", "empid": "person_id",
    "이름": "name", "성명": "name", "name": "name",
    "별칭": "alias", "영문명": "alias", "alias": "alias",
    "이메일": "email", "메일": "email", "e-mail": "email",
    "부서": "department", "본부": "department", "division": "department",
    "팀": "team", "직위": "position", "직급": "position",
    "재직상태": "status", "status": "status", "비고": "note",
}


@router.post("/import")
def import_roster(file: UploadFile = File(..., description="employees.csv"),
                  replace: bool = Query(True, description="기존 명부를 비우고 넣을지"),
                  db: Session = Depends(get_db)):
    """사원 마스터 CSV 를 적재한다. 같은 사번이 오면 갱신한다."""
    return load_csv(db, file.file.read(), replace=replace)


def load_csv(db: Session, raw: bytes, replace: bool = True,
             keep_existing: bool = False) -> dict:
    """CSV 본문 → 명부. 화면 업로드와 서버 시작 시 자동 적재가 같은 길을 쓴다.

    keep_existing 이면 이미 있는 사번은 손대지 않는다 — 자동 적재가 사람이
    고쳐 둔 값을 덮어쓰지 않게 하려는 것이다.
    """
    text = _decode(raw)
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise HTTPException(400, "빈 파일이거나 머리글만 있습니다")

    mapping = _map_headers(rows[0].keys())
    if "person_id" not in mapping.values() or "name" not in mapping.values():
        raise HTTPException(400, f"사번·이름 열을 찾지 못했습니다 — 읽은 머리글: "
                                 f"{list(rows[0].keys())}")

    if replace:
        db.query(RosterEntry).delete()
        db.flush()

    seen, added, updated, skipped = set(), 0, 0, []
    for i, row in enumerate(rows, start=2):
        vals = {}
        for raw_key, col in mapping.items():
            v = (row.get(raw_key) or "").strip()
            vals[col] = v or None
        pid, name = vals.get("person_id"), vals.get("name")
        if not pid or not name:
            skipped.append({"line": i, "reason": "사번 또는 이름이 비었습니다"})
            continue
        if pid in seen:
            skipped.append({"line": i, "reason": f"사번 중복 — {pid}"})
            continue
        seen.add(pid)
        vals["status"] = (vals.get("status") or "active").lower()

        found = db.query(RosterEntry).filter(RosterEntry.person_id == pid).first()
        if found:
            if keep_existing:
                continue                     # 사람이 고쳐 둔 값을 덮지 않는다
            for k, v in vals.items():
                setattr(found, k, v)
            updated += 1
        else:
            db.add(RosterEntry(**vals))
            added += 1
    db.commit()

    total = db.query(RosterEntry).count()
    active = sum(1 for r in db.query(RosterEntry).all() if r.dispatchable)
    return {"added": added, "updated": updated, "skipped": skipped,
            "total": total, "dispatchable": active,
            "excluded": total - active,
            "columns": {k: v for k, v in mapping.items()}}


def append_to_seed(entries) -> int:
    """자동 등록한 사람을 seed/employees.csv 에도 적어 둔다.

    DB 만 고치면 서버가 다시 뜰 때 사라진다(무료 배포는 DB 가 휘발된다).
    파일에 적어 두면 다음에 뜰 때 자동 적재가 다시 채워 준다.

    파일을 못 쓰는 환경(읽기 전용 배포)에서는 조용히 넘어간다 — 명부에
    사람을 못 적었다고 리포트 생성을 실패시킬 이유는 없다. 그때는
    /roster/export 로 내려받아 저장소 파일을 갱신하면 된다.

    끄려면 HR_ROSTER_SEED_WRITE=0.
    """
    if os.getenv("HR_ROSTER_SEED_WRITE", "1").strip().lower() in ("0", "false", "no"):
        return 0
    rows = [e for e in (entries or []) if getattr(e, "person_id", None)]
    if not rows or not os.path.exists(SEED_CSV):
        return 0

    order = ["employee_id", "name_ko", "alias_en", "email", "division", "team",
             "position", "employment_status", "hire_date", "manager_id",
             "location", "note"]
    field = {"employee_id": "person_id", "name_ko": "name", "alias_en": "alias",
             "email": "email", "division": "department", "team": "team",
             "position": "position", "employment_status": "status",
             "note": "note"}
    try:
        raw = open(SEED_CSV, "rb").read()
        nl = "\r\n" if b"\r\n" in raw else "\n"
        text = raw.decode("utf-8-sig")
        have = {ln.split(",")[0] for ln in text.splitlines()[1:] if ln.strip()}

        out = []
        for e in rows:
            if e.person_id in have:
                continue
            cells = []
            for col in order:
                v = getattr(e, field[col], None) if col in field else None
                v = "" if v is None else str(v)
                # 값에 쉼표가 들어가면 칸이 밀린다. 표준대로 감싼다.
                cells.append(f'"{v}"' if ("," in v or '"' in v) else v)
            out.append(",".join(cells))
        if not out:
            return 0

        if not text.endswith(("\n", "\r")):
            text += nl
        with open(SEED_CSV, "wb") as fh:
            fh.write(("﻿" + text.lstrip("﻿")
                      + nl.join(out) + nl).encode("utf-8"))
        print(f"[명부] seed/employees.csv 에 {len(out)}명을 적었습니다")
        return len(out)
    except OSError as exc:
        print(f"[명부] 파일에 적지 못했습니다 (읽기 전용일 수 있습니다) — {exc}")
        return 0


# 저장소에 함께 두는 기본 명부. 무료 배포는 서버가 다시 뜰 때마다 DB 가
# 비워지는데, 그때마다 사람이 CSV 를 다시 올리는 것은 잊기 쉽다.
SEED_CSV = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "seed", "employees.csv")


def seed_if_empty() -> Optional[dict]:
    """서버가 뜰 때 기본 명부를 **모자란 만큼만** 채운다.

    예전에는 표가 완전히 비었을 때만 넣었다. 그래서 명부에 사람을 새로
    추가해도 배포한 서버는 영원히 예전 명부를 들고 있었고, 그 사람들은
    사번·직급·이메일이 안 붙어서 리포트가 통째로 '보류' 로 떨어졌다.
    화면에는 그 이유가 보이지 않으니 "사원 데이터가 안 읽힌다" 로만 보인다.

    그렇다고 통째로 덮어쓰면 담당자가 화면에서 고쳐 둔 것이 조용히 사라진다.
    그래서 **이미 있는 사번은 건드리지 않고, 없는 사번만 넣는다.**

    끄려면 HR_ROSTER_SEED=0.
    """
    if os.getenv("HR_ROSTER_SEED", "1").strip().lower() in ("0", "false", "no"):
        return None
    if not os.path.exists(SEED_CSV):
        print(f"[명부] 기본 명부 파일이 없습니다 — {SEED_CSV}")
        return None

    from database import SessionLocal
    db = SessionLocal()
    try:
        before = db.query(RosterEntry).count()
        with open(SEED_CSV, "rb") as fh:
            got = load_csv(db, fh.read(), replace=False, keep_existing=True)
        if got["added"]:
            print(f"[명부] 기본 명부에서 {got['added']}명을 채웠습니다 "
                  f"(이미 있던 {before}명은 그대로) — 지금 {got['total']}명, "
                  f"발송 가능 {got['dispatchable']}, 제외 {got['excluded']}")
        else:
            print(f"[명부] {got['total']}명 — 채울 사람 없음")
        return got
    except Exception as exc:                 # noqa: BLE001
        # 명부가 없다고 서버가 안 뜨면 안 된다. 리포트 생성은 명부 없이도 된다.
        print(f"[명부] 기본 명부를 넣지 못했습니다 — {type(exc).__name__}: {exc}")
        return None
    finally:
        db.close()


# 담당자로 들어올 수 있는 사람 — 기본은 인사팀. 회사마다 이름이 다르므로
# HR_STAFF_TEAMS 로 바꿀 수 있게 둔다 ("인사팀,HR팀,인재개발팀" 처럼).
def _staff_teams() -> set:
    raw = os.getenv("HR_STAFF_TEAMS", "인사팀,HR팀,인재개발팀,교육팀")
    return {t.strip() for t in raw.split(",") if t.strip()}


@router.get("/staff")
def staff_login(empId: str = Query(..., description="사번"),
                db: Session = Depends(get_db)):
    """이 사번이 담당자로 들어와도 되는 사람인가.

    지금까지 담당자 화면은 사번을 받기만 하고 아무것도 확인하지 않았다.
    누구든 아무 사번이나 넣으면 전원의 평가 원문과 메일 주소를 볼 수 있었다.
    남의 인사 자료가 걸린 화면이라 그대로 두면 안 된다.

    비밀번호는 여기서 보지 않는다 — 회사 계정 체계가 붙기 전까지의 임시
    관문이고, '인사팀 사람만' 이라는 최소한의 선부터 긋는다.
    """
    key = (empId or "").strip().upper()
    if not key:
        raise HTTPException(400, "사번을 입력해 주십시오")

    row = (db.query(RosterEntry)
             .filter(RosterEntry.person_id == key).one_or_none())
    if row is None:
        raise HTTPException(404, f"명부에 없는 사번입니다 — {key}")
    if (row.status or "active") != "active":
        raise HTTPException(403, f"{row.name} 님은 재직 중이 아닙니다")
    if (row.team or "") not in _staff_teams():
        raise HTTPException(
            403, f"{row.name} 님은 담당자 권한이 없습니다 "
                 f"({row.team or '소속 미상'}) — 구성원으로 들어와 주십시오")

    return {"ok": True, "employee_id": row.person_id, "name": row.name,
            "position": row.position, "team": row.team,
            "division": row.department,
            # 화면이 '윤채린 과장' 으로 부른다. 이름만 있으면 누구인지 흐리다.
            "display": f"{row.name} {row.position}".strip()}


@router.post("/reset")
def reset_to_seed(db: Session = Depends(get_db)):
    """명부를 저장소에 든 기본 파일로 되돌린다.

    자동 적재는 **채우기만** 한다 — 사람이 고쳐 둔 값을 재시작이 덮으면
    안 되기 때문이다. 그 대신 명부에서 사람을 **뺀** 경우에는 배포한
    서버에 옛 행이 그대로 남는다. 지우는 것은 조용히 일어나면 안 되므로
    여기서 사람이 눌러서 한다.
    """
    if not os.path.exists(SEED_CSV):
        raise HTTPException(404, "저장소에 기본 명부가 없습니다")
    with open(SEED_CSV, "rb") as fh:
        got = load_csv(db, fh.read(), replace=True)
    print(f"[명부] 기본 명부로 되돌렸습니다 — {got['total']}명")
    return got


@router.get("")
def find(employee_id: Optional[str] = None, name: Optional[str] = None,
         alias: Optional[str] = None, all: bool = False,
         db: Session = Depends(get_db)):
    """조회. **동명이인은 전부 돌려준다** — 한 명을 골라 주지 않는다."""
    q = db.query(RosterEntry)
    if employee_id:
        q = q.filter(RosterEntry.person_id == employee_id)
    if name:
        q = q.filter(RosterEntry.name == name)
    if alias:
        q = q.filter(RosterEntry.alias == alias)
    if not (employee_id or name or alias or all):
        raise HTTPException(400, "employee_id · name · alias 중 하나를 주시거나 "
                                 "all=1 로 전수 조회하십시오")
    return {"employees": [_out(r) for r in q.all()]}


@router.get("/export")
def export_csv(db: Session = Depends(get_db)):
    """지금 명부를 CSV 로 내려받는다.

    자동 등록된 사람까지 담긴다. 배포한 서버가 읽기 전용이라 파일에 못 적은
    경우, 이것을 받아 저장소의 seed/employees.csv 를 갈아 두면 다음 배포부터
    그대로 올라간다.
    """
    from fastapi.responses import Response
    order = ["employee_id", "name_ko", "alias_en", "email", "division", "team",
             "position", "employment_status", "hire_date", "manager_id",
             "location", "note"]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(order)
    for r in db.query(RosterEntry).order_by(RosterEntry.person_id).all():
        w.writerow([r.person_id, r.name, r.alias or "", r.email or "",
                    r.department or "", r.team or "", r.position or "",
                    r.status or "active", "", r.manager_id or "", "",
                    r.note or ""])
    # 엑셀이 한글을 깨지 않게 BOM 을 붙인다
    body = ("﻿" + buf.getvalue()).encode("utf-8")
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             'attachment; filename="employees.csv"'})


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    """명부가 들어와 있는지, 몇 명이 발송 가능한지."""
    rows = db.query(RosterEntry).all()
    excluded = [{"person_id": r.person_id, "name": r.name,
                 "reason": ("재직 상태 " + r.status) if r.status != "active"
                           else "이메일 없음"}
                for r in rows if not r.dispatchable]
    dupes = {}
    for r in rows:
        dupes.setdefault(r.name, []).append(r.person_id)
    return {
        "total": len(rows),
        "dispatchable": len(rows) - len(excluded),
        "excluded": excluded,
        "duplicate_names": {k: v for k, v in dupes.items() if len(v) > 1},
    }


SETUP_PAGE = """<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>사원 명부 넣기</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/wanteddev/wanted-sans@v1.0.3/packages/wanted-sans/fonts/webfonts/variable/complete/WantedSansVariable.min.css">
<style>
:root{--red:#EA002C;--ink:#1B1B1D;--ink2:#48484D;--muted:#78787E;
      --line:#E4E4E7;--line2:#F0F0F2;--pearl:#FAFAFC;--page:#F5F5F7}
*{box-sizing:border-box}
body{font-family:'Wanted Sans','Noto Sans KR','Malgun Gothic',sans-serif;
     background:var(--page);color:var(--ink);margin:0;padding:40px 16px 80px;
     line-height:1.65;-webkit-font-smoothing:antialiased}
.s{max-width:660px;margin:0 auto;background:#fff;border:1px solid var(--line);
   border-radius:14px;padding:34px 38px;
   box-shadow:0 1px 3px rgba(0,0,0,.04),0 8px 28px rgba(0,0,0,.05)}
.eyebrow{display:inline-block;font-size:11.5px;font-weight:700;letter-spacing:.06em;
   color:var(--red);background:#FDE9ED;padding:4px 12px;border-radius:999px;
   margin-bottom:12px}
h1{font-size:24px;font-weight:700;letter-spacing:-.02em;margin:0 0 6px}
p.m{color:var(--muted);font-size:13.5px;margin:0 0 22px}
.drop{border:1.5px dashed var(--line);background:var(--pearl);border-radius:11px;
      padding:26px;text-align:center}
input[type=file]{font:inherit;font-size:13px}
button{font:inherit;font-weight:600;background:var(--red);color:#fff;border:0;
       border-radius:999px;padding:9px 22px;cursor:pointer;margin-top:12px}
button.ghost{background:#fff;color:var(--ink2);border:1px solid var(--line)}
button:disabled{opacity:.5;cursor:progress}
h2{font-size:14px;font-weight:700;margin:28px 0 6px;padding-top:20px;
   border-top:1px solid var(--line2)}
table{border-collapse:separate;border-spacing:0;width:100%;font-size:13px;
      margin-top:16px;border:1px solid var(--line);border-radius:11px;
      overflow:hidden}
th{font-size:11.5px;color:var(--muted);font-weight:600;background:var(--pearl);
   text-align:left;padding:9px 12px;border-bottom:1px solid var(--line)}
td{padding:10px 12px;border-bottom:1px solid var(--line2)}
tr:last-child td{border-bottom:0}
.warn{color:#A80020;font-weight:600}
.ok{color:#14663D;font-weight:700}
pre{background:var(--pearl);border:1px solid var(--line);padding:12px;
    font-size:12px;white-space:pre-wrap;border-radius:9px}
.foot{font-size:12.5px;color:var(--muted);margin-top:28px;padding-top:18px;
      border-top:1px solid var(--line2)}
.foot a{color:var(--red);font-weight:600;text-decoration:none;margin-right:14px}
</style></head><body><div class=s>
<div class=eyebrow>HR 리포트 엔진</div>
<h1>사원 명부 넣기</h1>
<p class=m>평가지에는 이름만 있습니다. 사번·직급·이메일은 이 명부에서 옵니다.<br>
명부에서 못 찾은 사람은 리포트가 <b>보류</b>로 서고 발송도 막힙니다.</p>
<form id=f class=drop>
  <div>employees.csv 를 고르세요</div>
  <input type=file name=file accept=".csv" required>
  <br><button type=submit>넣기</button>
</form>

<h2>명부에서 사람을 뺐는데 서버에 그대로라면</h2>
<p class=m style="margin-bottom:10px">서버가 다시 뜰 때는 <b>모자란 사람만 채웁니다</b> —
고쳐 둔 값을 덮지 않기 위해서입니다. 그래서 <b>뺀 사람은 안 지워집니다.</b>
아래 단추는 명부를 저장소에 든 기본 파일과 <b>똑같이</b> 맞춥니다
(화면에서 고쳐 둔 것이 있으면 같이 사라집니다).</p>
<button id=rst class=ghost type=button>기본 명부로 되돌리기</button>

<div id=out></div>
<div class=foot>
  <a href="/">← 처음으로</a><a href="/list">만들어진 리포트</a>
  <a href="/roster/summary">지금 상태(JSON)</a></div>
</div><script>
const f=document.getElementById('f'),out=document.getElementById('out');
const rst=document.getElementById('rst');
rst.onclick=async()=>{
  if(!confirm('명부를 기본 파일과 똑같이 맞춥니다.\\n'+
              '화면에서 고쳐 두신 것이 있으면 사라집니다. 진행할까요?')) return;
  rst.disabled=true; out.innerHTML='<p>되돌리는 중…</p>';
  const r=await fetch('/roster/reset',{method:'POST'});
  const d=await r.json(); rst.disabled=false;
  if(!r.ok){ out.innerHTML='<p class=warn>'+(d.detail||'실패')+'</p>'; return; }
  await report(d);
};
f.onsubmit=async e=>{
  e.preventDefault(); out.innerHTML='<p>넣는 중…</p>';
  const r=await fetch('/roster/import',{method:'POST',body:new FormData(f)});
  const d=await r.json();
  if(!r.ok){ out.innerHTML='<p class=warn>'+(d.detail||'실패')+'</p>'; return; }
  await report(d);
};
async function report(d){
  let h='<p class=ok>전체 '+d.total+'명 · 발송 가능 '+d.dispatchable+
        '명 · 제외 '+d.excluded+'명</p>';
  const s=await (await fetch('/roster/summary')).json();
  if(s.excluded.length){
    h+='<table><tr><th>제외된 사람</th><th>이유</th></tr>'+
      s.excluded.map(x=>'<tr><td>'+x.name+' ('+x.person_id+')</td><td>'+
      x.reason+'</td></tr>').join('')+'</table>';
  }
  const dup=Object.entries(s.duplicate_names||{});
  if(dup.length){
    h+='<table><tr><th>같은 이름</th><th>사번</th></tr>'+
      dup.map(([n,ids])=>'<tr><td>'+n+'</td><td>'+ids.join(', ')+
      '</td></tr>').join('')+'</table>'+
      '<p style="font-size:12.5px;color:#78787E">평가지에 별칭이 함께 적혀 '+
      '있으면 자동으로 갈립니다. 없으면 리포트가 보류로 서고, 검수 화면에서 '+
      '담당자가 본인을 지정해야 풀립니다 (R-15).</p>';
  }
  if(d.skipped.length) h+='<pre>'+d.skipped.map(x=>x.line+'줄 — '+x.reason).join('\\n')+'</pre>';
  out.innerHTML=h;
}
</script></body></html>"""


@router.get("/setup", response_class=HTMLResponse)
def setup_page():
    """브라우저에서 명부를 넣는 화면.

    배포한 서버에는 명부가 비어 있다. 터미널을 못 쓰는 사람도 넣을 수 있어야
    하므로 화면 하나를 둔다.
    """
    return HTMLResponse(SETUP_PAGE)


@router.delete("")
def clear(db: Session = Depends(get_db)):
    n = db.query(RosterEntry).delete()
    db.commit()
    return {"deleted": n}


# --------------------------------------------------------------------------
def _out(r: RosterEntry) -> dict:
    return {"employee_id": r.person_id, "name_ko": r.name, "alias_en": r.alias,
            "email": r.email, "division": r.department, "team": r.team,
            "position": r.position, "employment_status": r.status,
            "dispatchable": r.dispatchable}


def _decode(raw: bytes) -> str:
    """엑셀에서 저장한 CSV 는 BOM 이 붙거나 CP949 로 나온다. 둘 다 받는다."""
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _map_headers(headers) -> dict:
    out = {}
    for h in headers:
        key = (h or "").strip()
        low = key.lower().replace(" ", "").replace("_", "")
        col = COLUMNS.get(key.lower()) or ALIASES.get(key) or ALIASES.get(low)
        if col:
            out[h] = col
    return out
