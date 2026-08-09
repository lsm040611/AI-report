"""생성 큐 소진기.

    pending -> (worker 호출) -> returned -> (R-16 통과 + 수락) -> accepted

자동 모드에서는 업로드 직후 이 함수가 한 번 돌면서 큐를 비운다.
운영 모드에서는 돌지 않고, 외부 워크플로가 /handoff/pending 을 가져가면 된다.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from models import Card, CompetencyMapping, Handoff
from pipeline.rules.base import (EVIDENCE_REQUIRED, NOTICE, PRIORITY_RULES,
                                 add_flag)
from pipeline.rules.report import collect_emphasis, r16_verify_generated
from pipeline.rules.structural import retag_runs

from . import worker


def drain(db: Session, upload_id: Optional[int] = None,
          operator: str = "자동 모드", auto_accept: bool = True) -> Dict[str, object]:
    q = db.query(Handoff).filter(Handoff.status == "pending")
    if upload_id is not None:
        card_ids = [c.id for c in db.query(Card).filter(Card.upload_id == upload_id).all()]
        if not card_ids:
            return {"processed": 0, "accepted": 0, "rejected": 0, "rejects": []}
        q = q.filter(Handoff.card_id.in_(card_ids))

    stats: Dict[str, object] = {"processed": 0, "accepted": 0,
                                "rejected": 0, "rejects": []}

    # R-05(강조 의미 판정)가 먼저 돌아야 한다. 그 결과가 R-17 의 재료이기 때문이다.
    pending = sorted(q.all(), key=lambda h: h.id)
    first = [h for h in pending if h.rule_id in PRIORITY_RULES]
    rest = [h for h in pending if h.rule_id not in PRIORITY_RULES]
    retagged: set = set()

    _run_wave(db, first, operator, auto_accept, retagged, stats)

    # 강조 판정이 바뀐 카드는, 뒤따르는 작업의 재료를 새로 뽑아 준다
    for h in rest:
        if h.card_id in retagged:
            card = db.get(Card, h.card_id)
            if card is not None:
                _refresh_payload(h, card)

    _run_wave(db, rest, operator, auto_accept, retagged, stats)

    db.commit()
    stats["engine"] = worker.engine_name()
    return stats


# 한 번에 몇 건까지 같이 부를지. 호출은 대부분 응답을 기다리는 시간이라 겹쳐도 되지만,
# 너무 많이 겹치면 사용량 한도(429)에 걸린다. 넉넉잡아 4 건.
CONCURRENCY = max(1, int(os.getenv("HR_CONCURRENCY", "4")))


def _run_wave(db: Session, handoffs: List[Handoff], operator: str,
              auto_accept: bool, retagged: set, stats: Dict[str, object]) -> None:
    """한 묶음을 겹쳐서 부르고, 저장은 한 건씩 순서대로 한다.

    부르는 일만 나눠 맡긴다. DB 세션은 이 스레드 것이라 다른 스레드가 만지면 안 되고,
    승인 순서도 원래대로 유지해야 결과가 매번 같다.
    """
    jobs = []
    for h in handoffs:
        card = db.get(Card, h.card_id)
        if card is not None:
            jobs.append((h, card, h.rule_id, h.task, dict(h.payload or {})))
    if not jobs:
        return

    if len(jobs) == 1:
        results = [worker.generate(*jobs[0][2:])]
    else:
        worker.warm()                      # 스레드가 저마다 클라이언트를 만들지 않도록
        with ThreadPoolExecutor(max_workers=min(CONCURRENCY, len(jobs))) as pool:
            # generate 는 예외를 밖으로 던지지 않는다 — 실패해도 목 결과가 돌아온다
            results = list(pool.map(lambda j: worker.generate(*j[2:]), jobs))

    for (h, card, *_), result in zip(jobs, results):
        _accept_or_reject(db, h, card, result, operator, auto_accept, retagged, stats)


def _accept_or_reject(db: Session, h: Handoff, card: Card, result: dict,
                      operator: str, auto_accept: bool, retagged: set,
                      stats: Dict[str, object]) -> None:
    stats["processed"] += 1
    ok, reason = r16_verify_generated(
        result, card.card_json, require_evidence=h.rule_id in EVIDENCE_REQUIRED)

    h.result = result
    if not ok:
        h.status = "rejected"
        # 워커가 이유를 알고 있으면 그쪽이 담당자에게 더 쓸모 있다
        h.reject_reason = result.get("error") or reason
        stats["rejected"] += 1
        stats["rejects"].append({"handoff_id": h.id, "rule_id": h.rule_id,
                                 "person": card.person_name,
                                 "reason": h.reject_reason})
        return

    if reason:                               # 통과했지만 대조가 어긋난 건
        result = {**result, "unverified": reason}
        h.result = result

    # 한 건이 터져도 나머지는 살린다. 예전에는 중간에 예외가 나면
    # 그때까지의 처리가 통째로 롤백되어 큐가 손도 안 댄 상태로 남았다.
    try:
        _commit_one(db, h, card, result, operator, auto_accept, retagged)
        stats["accepted"] += 1 if auto_accept else 0
    except Exception as exc:                                   # noqa: BLE001
        db.rollback()
        hid, rule_id = h.id, h.rule_id
        h = db.get(Handoff, hid)
        why = f"저장 실패 — {type(exc).__name__}: {exc}"
        if h is not None:
            h.status = "rejected"
            h.reject_reason = why
            db.commit()
        stats["rejected"] += 1
        stats["rejects"].append({"handoff_id": hid, "rule_id": rule_id,
                                 "person": card.person_name, "reason": why})


def _commit_one(db: Session, h: Handoff, card: Card, result: dict,
                operator: str, auto_accept: bool, retagged: set) -> None:
    """한 건을 반영하고 바로 커밋한다 — 실패가 앞의 성공을 되돌리지 않도록."""
    h.status = "returned"
    h.reject_reason = None
    if auto_accept:
        _accept(db, h, card, result, operator)
        if h.rule_id in PRIORITY_RULES:
            retagged.add(h.card_id)
    db.commit()


# --------------------------------------------------------------------------
def _accept(db: Session, h: Handoff, card: Card, result: dict, operator: str) -> None:
    """카드에 생성물을 반영한다. JSON 컬럼은 새 객체로 갈아끼워야 변경이 감지된다."""
    data = dict(card.card_json)
    payload = h.payload or {}
    generated = list(data.get("generated", []))
    entry = {
        "handoff_id": h.id,
        "rule_id": h.rule_id,
        "task": h.task,
        # 어느 주관식에서 나온 문장인지 — 표현 계층이 제목을 정하는 데 쓴다
        "label": payload.get("label"),
        "role": payload.get("role"),
        "text": result.get("text", ""),
        "evidence": result.get("evidence", []),
        "engine": result.get("engine"),
        # API 호출이 실패하면 목으로 조용히 떨어진다. 왜 떨어졌는지 남기지
        # 않으면, 정제·번역이 안 된 문장이 나가는데도 아무도 모른다.
        "error": result.get("error"),
        "accepted_by": operator,
        # 작업마다 딸려 오는 추가 필드(암기 문장의 parts·closing 등)를 잃지 않는다
        "extra": {k: v for k, v in result.items()
                  if k not in ("text", "evidence", "engine", "task_label", "error")},
    }

    # 같은 작업을 다시 받으면 갈아끼운다. 예전에는 무조건 덧붙여서, 목으로
    # 만든 문장 아래에 새 문장이 또 붙어 같은 이야기가 두 번 실렸다.
    keys = _task_keys(entry)
    generated = [g for g in generated if not (_task_keys(g) & keys)]
    generated.append(entry)
    data["generated"] = generated

    if h.rule_id == "R-18":
        _apply_mapping(db, data, h.payload or {}, result, operator)
    elif h.rule_id == "R-13":
        _apply_translation(data, payload, result)
    elif h.rule_id == "R-05":
        _apply_emphasis(data, payload, result)

    card.card_json = data
    h.status = "accepted"


def _task_keys(g: dict) -> set:
    """같은 작업의 생성물인지 가리는 키 — 하나라도 겹치면 같은 작업이다.

    handoff_id 만으로 짚으면 이 필드가 없던 시절에 저장된 카드가 안 걸린다.
    그래서 규칙·작업·문항 조합도 함께 본다.
    """
    keys = {("k", g.get("rule_id"), g.get("task"), g.get("label"), g.get("role"))}
    if g.get("handoff_id"):
        keys.add(("h", g["handoff_id"]))
    return keys


def _apply_emphasis(data: dict, payload: dict, result: dict) -> None:
    """R-05: 문장을 읽고 판정한 결과로 강조 의미를 다시 붙인다.

    원문 글자는 하나도 바뀌지 않는다. 어디에 어떤 뜻이 붙는지만 달라진다.
    서식과 다르게 판정된 구간은 카드에 남겨 담당자가 대조할 수 있게 한다.
    """
    spans = result.get("spans") or []
    if not spans:
        return
    label, source = payload.get("label"), (payload.get("text") or "")

    for nar in data.get("narratives", []):
        runs = nar.get("runs") or []
        text = "".join(r.get("text", "") for r in runs)
        if not ((label and nar.get("original_label") == label) or
                (source and text == source)):
            continue

        before = {(r.get("text") or "").strip(): r.get("emphasis")
                  for r in runs if r.get("emphasis")}
        new_runs = retag_runs(text, spans)
        if not new_runs:
            return

        changed = []
        for r in new_runs:
            key = (r.get("text") or "").strip()
            was = before.pop(key, "__none__")
            if was != "__none__" and was != r.get("emphasis"):
                changed.append({"text": key, "from": was, "to": r.get("emphasis")})
        for key, was in before.items():          # 판정에서 빠져 평문이 된 구간
            changed.append({"text": key, "from": was, "to": None})

        nar["runs"] = new_runs
        nar["emphasis_source"] = "ai"
        if changed:
            nar["emphasis_changed"] = changed
            add_flag(data, "emphasis_reinterpreted", NOTICE,
                     target=nar.get("original_label"),
                     detail=f"서식과 다르게 판정된 구간 {len(changed)}곳 — "
                            f"문장 뜻으로 재분류했습니다")
        return


def _refresh_payload(h: Handoff, card: Card) -> None:
    """R-05 판정 이후, 아직 처리 전인 작업의 재료를 새로 뽑는다."""
    if h.rule_id != "R-17":
        return
    payload = dict(h.payload or {})
    if "emphasis" not in payload and "pairs" not in payload:
        return
    emphasis, pairs = collect_emphasis(card.card_json)
    payload["emphasis"], payload["pairs"] = emphasis, pairs
    h.payload = payload


def _apply_translation(data: dict, payload: dict, result: dict) -> None:
    """R-13: 번역 결과를 해당 서술 칸에 되돌린다.

    이게 없으면 번역은 생성되어도 리포트에 실리지 않는다 —
    실제로 영어 평가지의 리포트가 통째로 영어로 나오던 원인이었다.
    """
    text = (result.get("text") or "").strip()
    if not text:
        return
    label = payload.get("label")
    source = (payload.get("source_text") or "").strip()

    for nar in data.get("narratives", []):
        if nar.get("translation_ko"):
            continue
        joined = "".join(r.get("text", "") for r in (nar.get("runs") or [])).strip()
        if (label and nar.get("original_label") == label) or (source and joined == source):
            nar["translation_ko"] = text
            nar["translation_engine"] = result.get("engine")
            return


def _apply_mapping(db: Session, data: dict, payload: dict,
                   result: dict, operator: str) -> None:
    """R-18: 승인된 매핑은 저장해 재사용한다 — 데이터 자산화의 기반."""
    raw = (payload.get("area_name") or "").strip()
    canonical = (result.get("text") or "").strip().splitlines()[0].strip() if result.get("text") else ""
    if not raw or not canonical:
        return

    for item in data.get("scores", []):
        if (item.get("area_name") or "").strip() == raw:
            item["canonical_area"] = canonical

    # 같은 큐 안에서 같은 역량이 여러 번 올라온다 — 진단서베이는 피평가자 3명이
    # 문항을 그대로 공유하므로 Q1 매핑 요청이 3건 생긴다. 아래 조회는 아직
    # flush 되지 않은 행을 보지 못해서, 그대로 두면 두 번째 저장에서
    # UNIQUE 제약이 터지고 **드레인 전체가 롤백**된다(생성물이 통째로 사라진다).
    db.flush()
    row = (db.query(CompetencyMapping)
             .filter(CompetencyMapping.raw_name == raw).one_or_none())
    if row:
        row.canonical, row.approved_by = canonical, operator
    else:
        db.add(CompetencyMapping(raw_name=raw, canonical=canonical,
                                 approved_by=operator))
