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
<title>사원 명부 넣기</title><style>
body{font-family:'Malgun Gothic',sans-serif;background:#F0E8D6;color:#231D18;
     margin:0;padding:40px 16px;line-height:1.7}
.s{max-width:640px;margin:0 auto;background:#fff;border:1px solid #E2D7C0;
   border-top:6px solid #DA1B33;border-radius:3px;padding:32px 36px}
h1{font-size:24px;margin:0 0 4px}
p.m{color:#6E655C;font-size:13.5px;margin:0 0 22px}
.drop{border:2px dashed #E2D7C0;background:#FBF5E4;border-radius:6px;
      padding:24px;text-align:center}
button{font:inherit;font-weight:700;background:#DA1B33;color:#fff;border:0;
       border-radius:20px;padding:9px 22px;cursor:pointer;margin-top:12px}
table{border-collapse:collapse;width:100%;font-size:13px;margin-top:18px}
td,th{border-bottom:1px solid #EFE8D9;padding:7px 4px;text-align:left}
.warn{color:#A00E22}.ok{color:#0B6151;font-weight:700}
pre{background:#FBF5E4;border:1px solid #E2D7C0;padding:12px;font-size:12px;
    white-space:pre-wrap;border-radius:4px}
</style></head><body><div class=s>
<h1>사원 명부 넣기</h1>
<p class=m>평가지에는 이름만 있습니다. 사번과 이메일은 이 명부에서 옵니다.<br>
넣기 전에는 리포트를 만들 수는 있어도 <b>본인에게 보낼 수 없습니다.</b></p>
<form id=f class=drop>
  <div>employees.csv 를 고르세요</div>
  <input type=file name=file accept=".csv" required>
  <br><button type=submit>넣기</button>
</form>
<div id=out></div>
<p style="font-size:12.5px;color:#6E655C;margin-top:24px">
  <a href="/">← 처음으로</a> · 지금 상태: <a href="/roster/summary">/roster/summary</a></p>
</div><script>
const f=document.getElementById('f'),out=document.getElementById('out');
f.onsubmit=async e=>{
  e.preventDefault(); out.innerHTML='<p>넣는 중…</p>';
  const r=await fetch('/roster/import',{method:'POST',body:new FormData(f)});
  const d=await r.json();
  if(!r.ok){ out.innerHTML='<p class=warn>'+(d.detail||'실패')+'</p>'; return; }
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
      '<p style="font-size:12.5px;color:#6E655C">별칭이 있으면 자동으로 갈리고, '+
      '없으면 담당자에게 묻습니다 (R-15).</p>';
  }
  if(d.skipped.length) h+='<pre>'+d.skipped.map(x=>x.line+'줄 — '+x.reason).join('\\n')+'</pre>';
  out.innerHTML=h;
};
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
