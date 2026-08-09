"""오래 걸리는 일의 진행과 남은 시간.

생성도 발송도 1분 안팎 걸린다. 그동안 화면이 아무 말도 안 하면 멈춘 것처럼
보이고, 사람은 새로 고치거나 다시 누른다 — 메일은 그러면 두 번 간다.

계산은 단순하다. 한 건이 대체로 비슷하게 걸리므로, 지금까지의 평균에 남은
건수를 곱한다. 한 건도 안 끝났으면 **모른다고 말한다** — 아무 숫자나 보여
주는 것보다 "곧 시작합니다"가 정직하다.
"""
from __future__ import annotations

import time


def mmss(sec) -> str:
    s = int(round(sec or 0))
    if s < 60:
        return f"{s}초"
    return f"{s // 60}분 {s % 60}초" if s % 60 else f"{s // 60}분"


def eta(job: dict, done: int, total: int, done_verb: str = "걸렸습니다") -> dict:
    """진행 중인 작업의 경과·남은 시간을 화면에 실을 형태로."""
    started = job.get("startedAt")
    if job.get("state") == "done":
        el = job.get("elapsed")
        return {"elapsedSec": el, "etaSec": 0,
                "etaText": f"{mmss(el)} {done_verb}" if el else "완료"}
    if job.get("state") == "error":
        return {"elapsedSec": None, "etaSec": None, "etaText": None}
    if not started:
        return {"elapsedSec": None, "etaSec": None, "etaText": None}

    elapsed = round(time.monotonic() - started, 1)
    if not done or not total:
        return {"elapsedSec": elapsed, "etaSec": None, "etaText": "곧 시작합니다"}
    left = max(0, total - done)
    remain = round(elapsed / done * left)
    return {"elapsedSec": elapsed, "etaSec": remain,
            "etaText": f"약 {mmss(remain)} 남음" if left else "마무리 중"}
