"""AI 재작성 다리 — API 키 없이 Claude Code 로 생성물을 받는다.

앱은 언어 모델을 직접 부르지 않아도 된다. 원래 설계가 그렇게 되어 있다.
규칙은 '무엇을 써야 하는지'만 큐에 쌓고(handoff), 생성은 바깥에서 한다.
API 키를 넣으면 `generation/worker.py` 가 그 바깥이 되고, 키가 없으면
이 파일이 그 자리를 대신한다 — 사람이 Claude Code 에게 시키는 것이다.

    py -3.10 ai_bridge.py export     handoff_ai/요청.md 와 요청.json 을 만든다
    (Claude Code 에게 "handoff_ai 폴더 처리해줘" 라고 말한다)
    py -3.10 ai_bridge.py import     handoff_ai/응답.json 을 카드에 반영한다

넘기는 재료는 이미 R-11 이 단서를 걷어낸 것이라, 이 파일이 만드는 요청서에는
응답자를 특정할 수 있는 대목이 남지 않는다. 리포트에 실릴 문장을 사람이
눈으로 확인하고 반영하는 셈이라, 검수 관문(R-16)의 취지에도 맞는다.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import List

from database import SessionLocal
from generation import prompts
from generation.runner import _accept
from models import Card, Handoff
from pipeline.rules.base import EVIDENCE_REQUIRED
from pipeline.rules.report import r16_verify_generated
from routers.reports import build_report

DIR = Path(__file__).parent / "handoff_ai"
REQ_MD = DIR / "요청.md"
REQ_JSON = DIR / "요청.json"
RES_JSON = DIR / "응답.json"

# 목으로 채워진 것만 다시 만든다. 사람이 이미 손본 것을 덮어쓰지 않는다.
def _needs_ai(h: Handoff) -> bool:
    result = h.result or {}
    return (h.status == "pending") or (result.get("engine") == "mock")


# --------------------------------------------------------------------------
def export(upload_id: int = 0, limit: int = 60) -> int:
    DIR.mkdir(exist_ok=True)
    db = SessionLocal()
    try:
        # 범위를 안 주면 가장 최근 업로드만 본다. 그동안 쌓인 것을 전부 내보내면
        # 요청서가 수백 건이 되어 한 번에 처리할 수 없다.
        cards = db.query(Card)
        if not upload_id:
            latest = db.query(Card).order_by(Card.id.desc()).first()
            upload_id = latest.upload_id if latest else 0
        card_ids = {c.id for c in cards.filter(Card.upload_id == upload_id).all()}
        if not card_ids:
            print("카드가 없습니다. 평가지를 먼저 올리십시오.")
            return 0

        rows = [h for h in db.query(Handoff).order_by(Handoff.id).all()
                if h.card_id in card_ids and _needs_ai(h)][:limit]
        if not rows:
            print("다시 만들 것이 없습니다. (목으로 생성된 항목이 없음)")
            return 0
        print(f"업로드 {upload_id} · 카드 {len(card_ids)}장")

        tasks, blocks = [], []
        for h in rows:
            card = db.get(Card, h.card_id)
            if card is None:
                continue
            payload = h.payload or {}
            label, user_prompt, schema = prompts.build(h.task, payload)
            tasks.append({"id": h.id, "rule_id": h.rule_id, "task": h.task,
                          "label": label, "대상": card.person_name,
                          "지시문": user_prompt, "스키마": schema})
            blocks.append(
                f"### 작업 {h.id} · {label}  ({h.rule_id} / {card.person_name})\n\n"
                f"{user_prompt}\n")

        REQ_JSON.write_text(json.dumps({"작업": tasks}, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        REQ_MD.write_text(_guide(len(tasks)) + "\n\n---\n\n" + "\n\n---\n\n".join(blocks),
                          encoding="utf-8")
        print(f"작업 {len(tasks)}건을 내보냈습니다.")
        print(f"  요청서: {REQ_MD}")
        print(f"  이제 Claude Code 에게 이렇게 말하십시오 — \"handoff_ai 폴더 처리해줘\"")
        return len(tasks)
    finally:
        db.close()


def _guide(n: int) -> str:
    return f"""# AI 재작성 요청 ({n}건)

이 파일은 사람이 아니라 **Claude Code 에게 주는 요청서**입니다.

## 할 일

아래 각 작업의 지시문을 읽고 문장을 만든 다음, `handoff_ai/응답.json` 에
이 형식으로 저장하십시오. 작업 번호는 반드시 그대로 씁니다.

```json
[
  {{"id": 12, "text": "첫 줄\\n둘째 줄",
   "evidence": [{{"quote": "원문에서 그대로 옮긴 구절", "why": "판단 근거"}}]}},
  {{"id": 13, "text": "...", "evidence": [...]}}
]
```

- `text` 안의 줄바꿈은 `\\n` 입니다. `<b>...</b>` 로 강조할 수 있습니다.
- 지시문이 `parts`·`closing` 같은 추가 필드를 요구하면 그 키도 함께 넣으십시오
  (`handoff_ai/요청.json` 의 `스키마` 항목이 정확한 형식입니다).
- **응답에 없는 사실을 지어내지 마십시오.** 근거가 없으면 그 작업은 건너뛰고
  `id` 를 응답에서 빼면 됩니다.

## 끝나면

    py -3.10 ai_bridge.py import

리포트가 다시 만들어집니다."""


# --------------------------------------------------------------------------
def load() -> int:
    if not RES_JSON.exists():
        print(f"{RES_JSON} 가 없습니다. 먼저 export 를 하고 Claude Code 에게 시키십시오.")
        return 0

    data = json.loads(RES_JSON.read_text(encoding="utf-8"))
    if isinstance(data, dict):                       # {"응답": [...]} 도 받아 준다
        data = data.get("응답") or data.get("results") or []

    db = SessionLocal()
    touched: set = set()
    skipped: List[str] = []
    ok_n = rebuilt = 0
    try:
        for item in data:
            h = db.get(Handoff, int(item.get("id", 0)))
            if h is None:
                skipped.append(f"작업 {item.get('id')} — 큐에 없음")
                continue
            card = db.get(Card, h.card_id)
            if card is None:
                skipped.append(f"작업 {h.id} — 카드 없음")
                continue

            result = {k: v for k, v in item.items() if k != "id"}
            result.setdefault("evidence", [])
            result["engine"] = "claude-code"

            ok, reason = r16_verify_generated(
                result, card.card_json, require_evidence=h.rule_id in EVIDENCE_REQUIRED)
            if not ok:
                h.status = "rejected"
                h.reject_reason = reason
                skipped.append(f"작업 {h.id} — {reason}")
                continue
            if reason:
                result["unverified"] = reason

            h.result = result
            h.status = "returned"
            h.reject_reason = None
            _accept(db, h, card, result, "Claude Code")
            db.commit()
            touched.add(card.id)
            ok_n += 1

        for card_id in touched:
            card = db.get(Card, card_id)
            if card is not None:
                build_report(db, card)
                rebuilt += 1
        db.commit()
    finally:
        db.close()

    print(f"반영 {ok_n}건 · 리포트 재생성 {rebuilt}건")
    for s in skipped:
        print(f"  건너뜀: {s}")
    return ok_n


# --------------------------------------------------------------------------
if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    arg = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 0
    if cmd == "export":
        export(upload_id=arg)
    elif cmd in ("import", "load"):
        load()
    else:
        print(__doc__)
