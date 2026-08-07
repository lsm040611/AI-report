"""API 키를 넣은 뒤 **한 번만** 실제로 호출해 본다.

키를 넣자마자 평가지를 통째로 올리면, 잘못됐을 때 무엇이 문제인지 알기
어렵고 요금도 여러 번 나간다. 이 파일은 진짜 카드 하나로 요청 한 건만
보내고, 나온 문장과 쓴 토큰을 그대로 보여 준다.

    py -3.10 check_api.py
"""
from __future__ import annotations

import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from config import EFFORT, MODEL, USE_LLM
from generation import prompts, worker

# 실제 진단서베이에서 나온 모양의 재료. 원문을 보내지 않으려고 짧게 지어냈다.
PAYLOAD = {
    "label": "[주관식2] 이 리더에게 바라는 변화",
    "role": "change_request",
    "min_common": 2,
    "items": [
        "회의에서 결론을 먼저 말해주면 좋겠습니다",
        "회의가 길어질 때가 있어 결론부터 들으면 좋겠어요",
        "결론을 먼저 짚어주시면 이해가 빠를 것 같습니다",
    ],
}


def main() -> int:
    if not USE_LLM:
        print("키가 없습니다. api.env 의 ANTHROPIC_API_KEY 를 채우고 다시 실행하십시오.")
        return 1

    print(f"모델   : {MODEL}")
    print(f"effort : {EFFORT}")
    print("호출합니다… (10~30초)\n")

    out = worker.generate("R-11", "rewrite_neutral_third_person", PAYLOAD)

    if out.get("engine") == "mock":
        print("실패 — 목 모드로 떨어졌습니다.")
        print(f"  이유: {out.get('error')}")
        print("\n자주 있는 원인")
        print("  · 크레딧이 없음        → 콘솔에서 충전")
        print("  · 키가 잘못됨          → api.env 의 값을 다시 확인")
        print("  · 조직 권한/모델 접근  → 콘솔 > API keys 에서 확인")
        return 1

    print("=" * 66)
    print("생성된 문장")
    print("=" * 66)
    print(out.get("text", "").strip() or "(비어 있음)")

    print("\n" + "=" * 66)
    print("근거 (검수용 — 리포트에는 실리지 않습니다)")
    print("=" * 66)
    for ev in out.get("evidence", []):
        print(f"  · {ev.get('quote', '')[:60]}")
        print(f"      {ev.get('why', '')}")

    usage = out.get("usage") or {}
    if usage:
        print("\n" + "=" * 66)
        print("이번 호출에 쓴 토큰")
        print("=" * 66)
        for key in ("input_tokens", "output_tokens",
                    "cache_creation_input_tokens", "cache_read_input_tokens"):
            if usage.get(key) is not None:
                print(f"  {key:<28} {usage[key]:,}")

    print("\n성공. 이제 평가지를 올리면 서술형이 자동으로 생성됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
