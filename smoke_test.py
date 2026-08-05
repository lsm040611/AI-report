"""전 구간 점검. 서버를 띄우지 않고 파이프라인 전체를 한 번 돌린다.

    py -3.10 make_fixtures.py
    py -3.10 smoke_test.py

fixtures/ 의 엑셀 4종을 업로드해서 리포트 HTML 까지 만들고,
out/ 에 저장한다. 브라우저로 열어 확인하면 된다.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("HR_DB_URL", "sqlite:///./smoke.db")
os.environ.setdefault("HR_AUTO_APPROVE", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
OUT = os.path.join(HERE, "out")

# 매번 깨끗한 상태에서 시작한다
for stale in ("smoke.db",):
    p = os.path.join(HERE, stale)
    if os.path.exists(p):
        os.remove(p)

from fastapi.testclient import TestClient      # noqa: E402

from config import mode_banner                 # noqa: E402
from main import app                           # noqa: E402

client = TestClient(app)


def main() -> int:
    if not os.path.isdir(FIX):
        print("fixtures/ 가 없습니다. 먼저: py -3.10 make_fixtures.py")
        return 1
    os.makedirs(OUT, exist_ok=True)
    print(f"모드 — {mode_banner()}\n")

    rules = client.get("/rules").json()
    print(f"등록된 규칙 {len(rules)}개: {', '.join(r['id'] for r in rules)}\n")

    failures = 0
    files = sorted(f for f in os.listdir(FIX) if f.endswith(".xlsx"))

    for name in files:
        print(f"── {name}")
        with open(os.path.join(FIX, name), "rb") as fh:
            r = client.post(
                "/uploads",
                files={"file": (name, fh,
                                "application/vnd.openxmlformats-officedocument."
                                "spreadsheetml.sheet")})
        if r.status_code != 200:
            print(f"   ✗ 업로드 실패 {r.status_code}: {r.text[:300]}")
            failures += 1
            continue

        d = r.json()
        gen = d.get("generation") or {}
        print(f"   카드 {d['cards']}장 · 유형 {d['by_source_type']} · "
              f"문항정의 {d['question_defs']}개 · 생성 {gen.get('accepted', 0)}건 수락"
              f"{'/' + str(gen['rejected']) + '건 거부' if gen.get('rejected') else ''}")

        for w in d.get("warnings", []):
            print(f"   ! {w}")
        for rej in gen.get("rejects", []):
            print(f"   ! R-16 거부 [{rej['rule_id']}] {rej['person']}: {rej['reason']}")

        for item in d.get("reports", []):
            if not item.get("report_id"):
                print(f"   · {item['name']}: 차단 — {item['blocked_by']}")
                continue
            h = client.get(item["html"])
            if h.status_code != 200:
                print(f"   ✗ {item['name']} HTML 실패 {h.status_code}")
                failures += 1
                continue
            path = os.path.join(OUT, f"report_{item['name']}.html")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(h.text)
            print(f"   ✓ {item['name']}  →  out/{os.path.basename(path)}"
                  f"  ({len(h.text) / 1024:.0f}KB)")
        print()

    table = client.get("/reports/dispatch-table/list").json()
    print(f"발송 매핑표: {table['count']}건")
    for row in table["rows"]:
        mark = "○" if row["sendable"] else "×"
        quote = "" if row["quote_allowed"] else " (원문 인용 차단)"
        print(f"   {mark} {row['name']:<6} {row['course'] or '-'}{quote}")

    print("\n실패 " + (f"{failures}건" if failures else "없음") +
          f" · 결과물: {OUT}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
