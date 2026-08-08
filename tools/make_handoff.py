# -*- coding: utf-8 -*-
"""UI 트랙에 보낼 산출물을 만든다 — 통합 명세 체크리스트 4·5·6번.

    py -3.10 tools/make_handoff.py

`docs/engine_handoff/` 아래에 이것들을 떨군다.

    cards/누적교육.json  단발특강.json  진단서베이.json   ← 4번: 실제 출력 샘플
    api.json                                            ← 5번: OpenAPI 전문
    contract.json                                       ← 5번: 고정 어휘
    report_body/{이름}.html  report_body/report.css      ← 6번: 본문 렌더 산출물
    evidence/{이름}.json                                 ← §2 문장↔근거 색인

전부 **실제로 엔진을 돌려서** 나온 것이다. 손으로 쓴 예시가 아니다 —
문서와 구현이 어긋나는 것을 막으려면 산출물이 구현에서 나와야 한다.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "docs", "engine_handoff")
FIXTURES = os.path.join(ROOT, "fixtures")

# 어느 파일이 어느 유형인지. 담당자가 검증 화면에서 확정하는 값이라고 보면 된다.
PLAN = [
    ("20260519_1차수_A조.xlsx", "누적교육", "협상 스킬 심화"),
    ("20260602_2차수_A조.xlsx", "누적교육", None),      # 같은 과정에 이어 붙는다
    ("20260625_특강.xlsx", "단발특강", None),
    ("20260710_360진단.xlsx", "진단서베이", None),
]


def main() -> None:
    wd = tempfile.mkdtemp(prefix="handoff_")
    os.environ["HR_DB_URL"] = f"sqlite:///{wd}/h.db"
    os.environ["HR_STORAGE"] = os.path.join(wd, "storage")
    os.environ.setdefault("ANTHROPIC_API_KEY", "")   # 목 모드 — 돈 안 쓴다

    from database import Base, engine
    from fastapi.testclient import TestClient
    import main as app_main

    Base.metadata.create_all(engine)
    client = TestClient(app_main.app)

    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    for sub in ("cards", "report_body", "evidence"):
        os.makedirs(os.path.join(OUT, sub), exist_ok=True)

    made = {}
    for fname, stype, title in PLAN:
        path = os.path.join(FIXTURES, fname)
        if not os.path.exists(path):
            print(f"  건너뜀 — {fname} 없음")
            continue
        with open(path, "rb") as fh:
            a = client.post("/uploads/analyze",
                            files={"file": (fname, fh)}).json()

        m = a["courseMatch"]
        course = ({"mode": "create", "newTitle": title} if title else
                  {"mode": "link", "courseId": m["suggestedCourseId"]}
                  if m["mode"] == "link" else
                  {"mode": "create", "newTitle": m["suggestedTitle"]})
        body = {"confirmedSourceType": stype, "confirmedCourse": course,
                "operator": "엔진 트랙"}
        if a.get("wave"):
            body["confirmedWave"] = a["wave"]["suggested"]

        r = client.post(f"/uploads/{a['draftId']}/commit", json=body)
        if r.status_code != 200:
            print(f"  실패 — {fname}: {r.status_code} {r.text[:160]}")
            continue
        c = r.json()
        print(f"  {fname:<26} {stype:<8} 카드 {len(c['cards'])}장 "
              f"→ {c['courseId']}")

        # ── 4번: 카드 JSON 샘플 (유형당 한 장이면 충분하다)
        if stype not in made:
            cid = next((x["cardId"] for x in c["cards"] if x["sendable"]),
                       c["cards"][0]["cardId"] if c["cards"] else None)
            if cid:
                card = client.get(f"/cards/{cid}").json()
                _dump(f"cards/{stype}.json", card)
                made[stype] = cid

        # ── 6번: 리포트 본문 + 근거
        for rep in (c.get("reports") or []):
            if not rep.get("report_id"):
                continue
            rid = rep["report_id"]
            b = client.get(f"/reports/{rid}/body").json()
            name = _safe(rep["name"])
            _write(f"report_body/{name}.html", _preview(b))
            _dump(f"evidence/{name}.json",
                  client.get(f"/reports/{rid}/evidence").json())
            _write("report_body/report.css", b["css"])

    # ── 5번: API 시그니처 + 고정 어휘
    _dump("api.json", client.get("/openapi.json").json())
    _dump("contract.json", client.get("/contract").json())
    _dump("insights_sample.json",
          {c["courseId"]: client.get(f"/insights/course/{c['courseId']}").json()
           for c in client.get("/insights/courses").json()["courses"]})

    print(f"\n산출물 → {OUT}")
    for root, _, files in os.walk(OUT):
        for f in sorted(files):
            p = os.path.join(root, f)
            print(f"  {os.path.relpath(p, OUT):<46} {os.path.getsize(p):>8,} B")


def _preview(body: dict) -> str:
    """UI 의 본문 자리에 그대로 넣었을 때의 모습. 사이드바 폭까지 흉내 낸다."""
    nav = "".join(f'<li><a href="#{t["id"]}">{t["label"]}</a></li>'
                  for t in body["toc"])
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<title>리포트 본문 렌더 산출물 — 미리보기</title>
<style>{body["css"]}
body{{margin:0;display:flex;gap:24px;align-items:flex-start;background:#EDE8DF}}
.side{{width:280px;position:sticky;top:0;padding:28px 20px;font:14px/1.7 system-ui}}
.side ol{{padding-left:18px;margin:8px 0 0}}
.side a{{color:inherit;text-decoration:none}}
.body{{max-width:{body["maxWidth"]}px;flex:1}}
.hint{{font-size:12px;color:#7a7a7a;line-height:1.6}}
</style></head><body>
<div class="side"><b>목차 (스크롤 스파이)</b><ol>{nav}</ol>
<p class="hint">이 사이드바는 미리보기용입니다. 실제로는 UI 가 그립니다.<br><br>
엔진이 주는 것은 오른쪽 본문(<code>body.html</code>)과 <code>body.css</code> 뿐입니다.
묶음마다 <code>data-section</code>, AI 문장마다 <code>data-sentence-id</code> 가
붙어 있습니다.</p></div>
<div class="body"><div class="sheet">{body["html"]}{body["footerHtml"]}</div></div>
</body></html>"""


def _safe(name: str) -> str:
    return "".join(ch for ch in str(name) if ch.isalnum() or ch in "가-힣_-") or "card"


def _dump(rel: str, obj) -> None:
    _write(rel, json.dumps(obj, ensure_ascii=False, indent=2))


def _write(rel: str, text: str) -> None:
    path = os.path.join(OUT, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


if __name__ == "__main__":
    main()
