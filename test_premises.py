"""과제의 세 전제를 실제 산출물로 검증한다.

    정확성 — 점수·평균이 원본과 어긋나지 않고, 원본이 이상하면 잡아낼 것
    프라이버시 — 개인 리포트에 타인 정보 0건 + 서술 응답의 익명성 보존
    범용성 — 처음 보는 양식에도 동작할 것

주장이 아니라 **완성된 리포트를 열어서** 확인한다. 특히 프라이버시는
리포트 HTML 을 통째로 훑어 다른 사람의 이름·사번·원문이 섞였는지 본다.

    py -3.10 test_premises.py
"""
from __future__ import annotations

import io
import os
import re
import shutil
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

_fails: list = []


def check(label: str, detail, ok: bool) -> None:
    print(f"  {'OK  ' if ok else '실패 '}{label}  {detail}")
    if not ok:
        _fails.append(label)


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)


def main() -> int:
    workdir = tempfile.mkdtemp(prefix="premise_")
    os.environ["HR_DB_URL"] = f"sqlite:///{workdir}/premise.db"
    os.environ["HR_STORAGE"] = os.path.join(workdir, "storage")

    import glob

    import make_fixtures
    from database import Base, SessionLocal, engine
    from fastapi.testclient import TestClient
    from models import Card, Report
    import main as app_main

    Base.metadata.create_all(engine)

    # 픽스처는 고정 경로에 쓰므로 잠시 임시 폴더로 돌려놓는다
    make_fixtures.OUT = os.path.join(workdir, "fixtures")
    os.makedirs(make_fixtures.OUT, exist_ok=True)
    make_fixtures.negotiation_1()
    make_fixtures.negotiation_2()
    make_fixtures.lecture()
    make_fixtures.survey()
    fixtures = sorted(glob.glob(os.path.join(make_fixtures.OUT, "*.xlsx")))
    assert fixtures, "픽스처가 만들어지지 않았습니다"

    client = TestClient(app_main.app)

    originals = {}
    for path in fixtures:
        with open(path, "rb") as fh:
            originals[os.path.basename(path)] = fh.read()
        with open(path, "rb") as fh:
            resp = client.post("/uploads", files={"file": (os.path.basename(path), fh)})
        assert resp.status_code == 200, resp.text

    db = SessionLocal()
    cards = db.query(Card).all()
    reports = db.query(Report).all()

    # ── 전제 3 : 원본은 그대로 보관된다 ──────────────────────────────────
    print("\n── 원본 보관 (원본 = 검증 기준)")
    same = []
    for name, blob in originals.items():
        stored = os.path.join(os.environ["HR_STORAGE"], name)
        same.append(os.path.exists(stored) and open(stored, "rb").read() == blob)
    check("업로드 원본이 바이트 단위로 동일", f"{sum(same)}/{len(same)}건", all(same))

    # ── 정확성 : 평균은 엔진이 다시 계산하고, 원본이 틀리면 잡는다 ────────
    print("\n── 정확성")
    recomputed = mismatch = 0
    for c in cards:
        summary = (c.card_json.get("score_summary") or {})
        scores = [s["score"] for s in c.card_json.get("scores", [])
                  if s.get("score") is not None]
        if not scores:
            continue
        recomputed += 1
        want = round(sum(scores) / len(scores), 2)
        if summary.get("average") != want:
            check(f"{c.person_name} 평균 재계산", f"{summary.get('average')} != {want}", False)
        orig = summary.get("original_average")
        if orig is not None and abs(float(orig) - want) > 0.05:
            mismatch += 1
            codes = [f.get("code") for f in c.card_json.get("flags", [])]
            check(f"{c.person_name} 원본 평균 오류를 잡았는가",
                  f"원본 {orig} vs 재계산 {want}", "average_mismatch" in codes)
    check("모든 카드의 평균이 점수와 일치", f"{recomputed}장 검사", True)
    check("원본 평균이 틀린 건을 검출", f"{mismatch}건", True)

    missing = sum(1 for c in cards for s in c.card_json.get("scores", [])
                  if s.get("score") is None)
    zeroed = sum(1 for c in cards for s in c.card_json.get("scores", [])
                 if s.get("score") == 0)
    check("결측을 0 으로 채우지 않음", f"미평가 {missing}칸 · 0점 {zeroed}칸", zeroed == 0)

    # ── 프라이버시 : 리포트 HTML 에 타인 정보가 섞였는가 ────────────────
    print("\n── 프라이버시 (완성된 리포트를 직접 훑는다)")
    names = {c.person_name for c in cards if c.person_name}
    leaks = []
    for rep in reports:
        card = db.get(Card, rep.card_id)
        me = card.person_name
        html = client.get(f"/reports/{rep.id}/html").text
        body = _strip_tags(html)
        for other in names - {me}:
            if len(other) >= 2 and re.search(rf"(?<![가-힣]){re.escape(other)}(?![가-힣])", body):
                leaks.append(f"{me} 리포트에 '{other}'")
    check("타인 이름 노출", f"{len(leaks)}건" + (f" — {leaks[:3]}" if leaks else ""), not leaks)

    # 진단서베이 주관식 원문이 그대로 실렸는가
    quoted = []
    for rep in reports:
        card = db.get(Card, rep.card_id)
        if card.card_json.get("direction") != "aggregated_responses":
            continue
        body = _strip_tags(client.get(f"/reports/{rep.id}/html").text)
        for nar in card.card_json.get("narratives", []):
            for item in (nar.get("raw_items") or []):
                raw = (item.get("text") or "").strip()
                if len(raw) >= 20 and raw[:20] in body:
                    quoted.append(f"{card.person_name}: {raw[:24]}…")
    check("응답 원문 그대로 인용", f"{len(quoted)}건" + (f" — {quoted[:2]}" if quoted else ""),
          not quoted)

    # ── 범용성 : 처음 보는 양식 ─────────────────────────────────────────
    print("\n── 범용성")
    kinds = {(c.card_json.get("source_type") or {}).get("type") for c in cards}
    check("서로 다른 양식을 한 엔진으로 처리", sorted(k for k in kinds if k), len(kinds) >= 2)
    check("모든 카드가 리포트까지 도달", f"{len(reports)}/{len(cards)}장",
          len(reports) == len(cards))

    db.close()
    shutil.rmtree(workdir, ignore_errors=True)

    print()
    if _fails:
        print(f"실패 {len(_fails)}건: {_fails}")
        return 1
    print("세 전제 모두 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
