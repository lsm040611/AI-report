# -*- coding: utf-8 -*-
"""UI ↔ 엔진 계약 시험 — 통합 지점 ①~⑤ 를 순서대로 밟는다.

    py -3.10 test_ui_contract.py

프론트를 붙이기 전에 이것부터 통과해야 한다. 여기서 잡히는 것은
"엔진이 UI 가 그릴 수 없는 값을 내보내지 않는가" 하나다 — 계약에 없는
issueCode, 계약에 없는 severity, 목차에 있는데 본문에 없는 섹션, 본문에
있는데 조회되지 않는 문장 id.

목(mock) 모드로 돈다. API 키를 쓰지 않으므로 비용이 들지 않는다.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_wd = tempfile.mkdtemp(prefix="uicontract_")
os.environ["HR_DB_URL"] = f"sqlite:///{_wd}/t.db"
os.environ["HR_STORAGE"] = os.path.join(_wd, "storage")
os.environ["ANTHROPIC_API_KEY"] = ""

from contract import FLAG_SEVERITIES, ISSUE_CODES, SECTION_IDS   # noqa: E402
from database import Base, engine                                # noqa: E402
from fastapi.testclient import TestClient                        # noqa: E402
import main as app_main                                          # noqa: E402

Base.metadata.create_all(engine)
client = TestClient(app_main.app)
FIXTURES = os.path.join(ROOT, "fixtures")

_ok = _fail = 0


def check(label, cond, detail="") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  OK  {label}" + (f"  {detail}" if detail else ""))
    else:
        _fail += 1
        print(f"  실패 {label}  {detail}")


PLAN = [
    ("20260519_1차수_A조.xlsx", "누적교육", "협상 스킬 심화"),
    ("20260602_2차수_A조.xlsx", "누적교육", None),
    ("20260625_특강.xlsx", "단발특강", None),
    ("20260710_360진단.xlsx", "진단서베이", None),
]


def analyze(fname: str) -> dict:
    with open(os.path.join(FIXTURES, fname), "rb") as fh:
        r = client.post("/uploads/analyze", files={"file": (fname, fh)})
    assert r.status_code == 200, r.text[:300]
    return r.json()


def commit(draft: dict, stype: str, title=None) -> dict:
    m = draft["courseMatch"]
    course = ({"mode": "create", "newTitle": title} if title else
              {"mode": "link", "courseId": m["suggestedCourseId"]}
              if m["mode"] == "link" else
              {"mode": "create", "newTitle": m["suggestedTitle"]})
    body = {"confirmedSourceType": stype, "confirmedCourse": course,
            "operator": "시험"}
    if draft.get("wave"):
        body["confirmedWave"] = draft["wave"]["suggested"]
    r = client.post(f"/uploads/{draft['draftId']}/commit", json=body)
    assert r.status_code == 200, r.text[:300]
    return r.json()


# ══════════════════════════════════════════════════════════════
def case_contract():
    """고정 어휘는 한 번의 호출로 전부 받아 갈 수 있는가."""
    c = client.get("/contract").json()
    check("issueCodes 노출", set(c["issueCodes"]) == set(ISSUE_CODES))
    check("severity 4종", set(c["flagSeverities"]) == set(FLAG_SEVERITIES))
    check("섹션 id 4종", [s["id"] for s in c["sections"]] == list(SECTION_IDS))
    check("emphasis 는 색이 아니라 뜻",
          all(v.startswith("em-") for v in c["emphasisClasses"].values()))


def case_analyze_makes_no_cards():
    """① 판정은 카드를 만들지 않는다 — 담당자가 취소해도 남는 것이 없어야 한다."""
    before = len(client.get("/insights/courses").json()["courses"])
    a = analyze(PLAN[0][0])
    after = len(client.get("/insights/courses").json()["courses"])
    check("과정이 늘지 않음", before == after)
    check("근거가 한국어 문장", a["sourceType"]["evidence"].startswith("판정 근거"))
    check("과정 제안에도 근거", a["courseMatch"]["evidence"].startswith("제안 근거"))
    check("다시 꺼낼 수 있음",
          client.get(f"/uploads/drafts/{a['draftId']}").status_code == 200)

    bad = [r["issueCode"] for r in a["rows"] if r["issueCode"] not in ISSUE_CODES]
    check("계약에 없는 issueCode 없음", not bad, bad)
    wrong = [r for r in a["rows"]
             if r["field"] != ISSUE_CODES[r["issueCode"]]["field"]]
    check("issueCode 와 field 가 계약대로", not wrong, wrong[:2])
    return a


def case_commit_respects_operator(a: dict):
    """② 담당자 확정값을 엔진이 뒤집지 않는가."""
    engine_said = a["sourceType"]["type"]
    other = next(t for t in ("누적교육", "단발특강") if t != engine_said)
    c = commit(a, other, "확정 시험 과정")
    cards = client.get(f"/uploads/{c['uploadId']}/cards").json()
    check("엔진이 재판정하지 않음",
          all(x["source_type"] == other for x in cards),
          f"엔진={engine_said} 확정={other}")
    check("확정과 동시에 승인됨", all(x["type_confirmed"] for x in cards))

    badsev = [f["severity"] for f in c["flags"]
              if f["severity"] not in FLAG_SEVERITIES]
    check("계약에 없는 severity 없음", not badsev, badsev)
    check("courseId 를 엔진이 발급", bool(c["courseId"]))
    r = client.post(f"/uploads/{a['draftId']}/commit",
                    json={"confirmedSourceType": other,
                          "confirmedCourse": {"mode": "create",
                                              "newTitle": "또"}})
    check("같은 판정으로 두 번 커밋 불가", r.status_code == 409)


def case_row_fix_keeps_original():
    """② 수정 값은 원본이 아니라 카드에만 반영되고, 이력이 남는가."""
    a = analyze(PLAN[0][0])
    m = a["courseMatch"]
    r = client.post(f"/uploads/{a['draftId']}/commit", json={
        "confirmedSourceType": "누적교육",
        "confirmedCourse": {"mode": "create", "newTitle": "수정 시험 과정"},
        "rowFixes": [{"rowNumber": 7, "field": "empId", "value": "EMP-9999"}],
        "operator": "시험", "generate": False})
    assert r.status_code == 200, r.text[:200]
    cards = client.get(f"/uploads/{r.json()['uploadId']}/cards").json()
    full = [client.get(f"/cards/{x['id']}").json()["card"] for x in cards]
    fixed = [c for c in full
             if (c.get("provenance") or {}).get("operator_fixes")]
    check("수정 이력이 남음", bool(fixed),
          fixed[0]["provenance"]["operator_fixes"] if fixed else "없음")
    if fixed:
        rec = fixed[0]["provenance"]["operator_fixes"][0]
        check("무엇을 무엇으로 바꿨는지 남음",
              "from" in rec and "to" in rec and rec["to"] == "EMP-9999", rec)


def case_gate_is_reachable():
    """① 담당자가 실제로 통과할 수 있는 양의 경고만 나오는가.

    화면의 '리포트 생성' 버튼은 오류 0 + 경고 전건 확인이라야 열린다.
    '원래 그런 파일'인데 경고가 쏟아지면 담당자는 영원히 못 넘어간다.
    실제로 두 번 그랬다 — 진단서베이의 반복 이름을 동명이인으로 잡았고,
    점수 전부 빈 행에 항목 수만큼 경고를 냈다.
    """
    for fname, stype, _ in PLAN:
        if not os.path.exists(os.path.join(FIXTURES, fname)):
            continue
        a = analyze(fname)
        rows = a["rows"]
        errs = [r for r in rows if r["severity"] == "error"]
        warns = [r for r in rows if r["severity"] == "warning"]
        n = a["summary"]["recognized"]
        check(f"{fname[:22]} — 경고가 행 수를 넘지 않음",
              len(warns) <= max(2, n // 3),
              f"인식 {n}행에 경고 {len(warns)}건")
        if stype == "진단서베이":
            dupes = [r for r in rows if r["issueCode"] == "DUPLICATE_NAME"]
            check("진단서베이의 반복 이름을 동명이인으로 잡지 않음", not dupes,
                  f"{len(dupes)}건")
        per_row = {}
        for r in rows:
            per_row[r["rowNumber"]] = per_row.get(r["rowNumber"], 0) + 1
        piled = [k for k, v in per_row.items() if v >= 3]
        check(f"{fname[:22]} — 한 행에 경고가 쌓이지 않음", not piled, piled)
        check(f"{fname[:22]} — 오류 없이 넘어감", not errs,
              [e["issueCode"] for e in errs])


def case_every_sample_reaches_report():
    """어떤 샘플을 올려도 리포트까지 도달하는가. 화면 연결의 최소 조건이다."""
    for fname, stype, title in PLAN:
        if not os.path.exists(os.path.join(FIXTURES, fname)):
            continue
        got = commit(analyze(fname), stype, (title or "") + " 도달시험" if title else None)
        made = [r for r in (got.get("reports") or []) if r.get("report_id")]
        check(f"{fname[:22]} — 리포트 {len(made)}편", bool(made),
              got.get("warnings"))


def case_report_body(upload: dict):
    """③ 본문 조각이 UI 계약대로 나오는가."""
    rid = next(x["report_id"] for x in upload["reports"] if x.get("report_id"))
    b = client.get(f"/reports/{rid}/body").json()

    check("본문 폭 760", b["maxWidth"] == 760)
    check("<html> 껍데기 없음", "<html" not in b["html"])
    check("목차의 섹션이 본문에 다 있음",
          all(f'data-section="{t["id"]}"' in b["html"] for t in b["toc"]))
    check("본문의 섹션이 목차에 다 있음",
          set(_sections(b["html"])) == {t["id"] for t in b["toc"]})
    check("섹션 순서가 계약대로",
          _sections(b["html"]) == [s for s in SECTION_IDS
                                   if s in _sections(b["html"])])
    check("빈 섹션 없음", "준비 중" not in b["html"])
    check("인라인 색상 없음", "color:" not in b["html"], _first_color(b["html"]))

    ev = client.get(f"/reports/{rid}/evidence").json()["items"]
    ids = {e["sentenceId"] for e in ev}
    in_html = set(_sentence_ids(b["html"]))
    check("본문 문장 id 가 전부 조회됨", in_html <= ids, sorted(in_html - ids))
    check("근거에 출처가 붙음", all(e["sourceRef"] for e in ev))
    check("없는 문장은 404",
          client.get(f"/reports/{rid}/evidence?sentence_id=s999").status_code == 404)
    return rid


def case_compare_section_is_conditional():
    """③ 이전 회차가 없으면 compare 를 만들지 않는가 (빈 섹션 금지)."""
    seen = {}
    for c in client.get("/insights/courses").json()["courses"]:
        d = client.get(f"/insights/course/{c['courseId']}").json()
        seen[c["courseId"]] = len(d.get("trend") or []) if d["kind"] == "누적교육" else 0

    single = [cid for cid, n in seen.items() if n <= 1]
    check("1회차뿐인 과정이 시험 대상에 있음", bool(single), list(seen.items()))


def case_pdf(rid: int, upload: dict):
    """④ 파일명 규칙과 발송 게이트."""
    r = client.get(f"/reports/{rid}/pdf")
    check("PDF 응답", r.status_code == 200)
    p = client.post(f"/reports/pdf/prepare?upload_id={upload['uploadId']}").json()
    check("사전 생성 목록", bool(p["items"]))
    check("발송 불가 카드는 pdfUrl 이 비어 있음",
          all((i["pdfUrl"] is None) == (i["blockedBy"] is not None)
              for i in p["items"]))
    check("파일명 4토막", all(i["filename"].count("_") >= 2 and
                            i["filename"].endswith(".pdf") for i in p["items"]),
          p["items"][0]["filename"] if p["items"] else "")


def case_insights():
    """⑤ 유형별 분석 + 근거 · 과정 횡단 지표 없음."""
    courses = client.get("/insights/courses").json()["courses"]
    check("과정 목록", bool(courses))
    paths = [getattr(r, "path", "") for r in app_main.app.routes]
    check("과정 횡단 통합 엔드포인트 없음",
          not any(p.startswith("/insights/all") for p in paths))

    kinds = set()
    for c in courses:
        d = client.get(f"/insights/course/{c['courseId']}").json()
        kinds.add(d["kind"])
        check(f"{c['title'][:14]} — 인사이트에 근거가 다 붙음",
              all(i.get("basis") for i in d["insights"]))
        if d["kind"] == "단발특강":
            check("단발특강에는 추이가 없음", d["trend"] is None)
        if d["kind"] == "누적교육":
            check("비교 못 한 항목을 따로 알려 줌", "areasNotCompared" in d)
    check("세 유형이 다 나옴", kinds == {"누적교육", "단발특강", "진단서베이"}, kinds)


# ══════════════════════════════════════════════════════════════
def _sections(html: str):
    import re
    return re.findall(r'data-section="([a-z]+)"', html)


def _sentence_ids(html: str):
    import re
    return re.findall(r'data-sentence-id="(s\d+)"', html)


def _first_color(html: str) -> str:
    i = html.find("color:")
    return html[max(0, i - 60):i + 20] if i >= 0 else ""


def main() -> None:
    print("── ⓪ 고정 어휘")
    case_contract()

    print("\n── ① 판정 (POST /uploads/analyze)")
    a = case_analyze_makes_no_cards()

    print("\n── ① 담당자가 넘어갈 수 있는가 (검증 게이트)")
    case_gate_is_reachable()

    print("\n── ② 카드 생성 (POST /uploads/{draftId}/commit)")
    case_commit_respects_operator(a)
    case_row_fix_keeps_original()
    case_every_sample_reaches_report()

    print("\n── 세 유형을 다 태운다")
    uploads = []
    for fname, stype, title in PLAN:
        if not os.path.exists(os.path.join(FIXTURES, fname)):
            print(f"  건너뜀 — {fname} 없음")
            continue
        got = commit(analyze(fname), stype, title)
        uploads.append(got)
        print(f"  {fname:<26} {stype:<8} 카드 {len(got['cards'])}장")

    print("\n── ③ 리포트 본문 · 근거")
    rid = case_report_body(uploads[0])
    case_compare_section_is_conditional()

    print("\n── ④ PDF")
    case_pdf(rid, uploads[0])

    print("\n── ⑤ 인사이트")
    case_insights()

    print(f"\n{'전부 통과' if not _fail else f'실패 {_fail}건'} "
          f"(검사 {_ok + _fail}건)")
    sys.exit(1 if _fail else 0)


if __name__ == "__main__":
    main()
