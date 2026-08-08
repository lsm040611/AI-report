# -*- coding: utf-8 -*-
"""사원 마스터 CSV 를 명부에 넣는다.

    py -3.10 tools/load_roster.py "…\\files_백엔드_사원명단\\employees.csv"

인자를 안 주면 카톡 받은 파일에서 employees.csv 를 찾아본다.
서버가 켜져 있지 않아도 되고, 같은 DB 를 직접 연다.

이걸 넣어야 카드에 사번과 이메일이 붙는다. 넣기 전에는 평가지에 이름만 있어서
"이 사람이 누구인지" 답할 방법이 없다 — R-15 가 명부 미입력 플래그를 단다.
"""
from __future__ import annotations

import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

GUESSES = [
    os.path.expanduser(r"~\OneDrive\문서\카카오톡 받은 파일"
                       r"\files_백엔드_사원명단\employees.csv"),
    os.path.join(ROOT, "employees.csv"),
]


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else _find()
    if not path or not os.path.exists(path):
        print("employees.csv 를 찾지 못했습니다. 경로를 인자로 주십시오.")
        print('  py -3.10 tools/load_roster.py "C:\\...\\employees.csv"')
        sys.exit(1)

    from database import Base, engine
    from fastapi.testclient import TestClient
    import main as app_main

    Base.metadata.create_all(engine)
    client = TestClient(app_main.app)

    with open(path, "rb") as fh:
        r = client.post("/roster/import",
                        files={"file": (os.path.basename(path), fh)})
    if r.status_code != 200:
        print(f"실패 — {r.status_code} {r.text[:300]}")
        sys.exit(1)

    d = r.json()
    print(f"{os.path.basename(path)}")
    print(f"  새로 넣음 {d['added']}명 · 갱신 {d['updated']}명 · 전체 {d['total']}명")
    print(f"  발송 가능 {d['dispatchable']}명 · 제외 {d['excluded']}명")
    for s in d["skipped"]:
        print(f"  건너뜀 {s['line']}줄 — {s['reason']}")

    s = client.get("/roster/summary").json()
    if s["excluded"]:
        print("\n발송에서 빠지는 사람")
        for e in s["excluded"]:
            print(f"  {e['name']} ({e['person_id']}) — {e['reason']}")
    if s["duplicate_names"]:
        print("\n같은 이름이 둘 이상")
        for name, ids in s["duplicate_names"].items():
            print(f"  {name} — {', '.join(ids)}")
        print("  (별칭이 있으면 R-15 가 자동으로 가르고, 없으면 담당자에게 묻습니다)")


def _find() -> str:
    for g in GUESSES:
        if os.path.exists(g):
            return g
    folder = os.path.expanduser(r"~\OneDrive\문서\카카오톡 받은 파일")
    for root, _, files in os.walk(folder):
        if "employees.csv" in files:
            return os.path.join(root, "employees.csv")
        if root.count(os.sep) - folder.count(os.sep) > 2:
            continue
    return ""


if __name__ == "__main__":
    main()
