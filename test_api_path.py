"""API 호출 경로 점검 — **키 없이** 돌린다.

목 모드에서는 `_call()` 이 한 번도 실행되지 않는다. 그래서 요청 모양이 틀려도
키를 넣기 전까지는 드러나지 않는다 — 실제로 스키마에 받지 않는 항목이 남아
있었고, max_tokens 가 모자라 응답이 잘릴 수 있는 상태였다.

여기서는 가짜 클라이언트가 요청을 붙잡아, 진짜 키를 쓰지 않고 다음을 확인한다.

    py -3.10 test_api_path.py
"""
from __future__ import annotations

import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from generation import prompts, worker

_fails: list = []
_captured: dict = {}


def check(label: str, got, want=None, ok: bool = None):
    passed = ok if ok is not None else (got == want)
    print(f"  {'OK  ' if passed else '실패 '}{label}  {got}")
    if not passed:
        _fails.append(label)


class _Block:
    def __init__(self, type_: str, text: str = ""):
        self.type, self.text = type_, text


class _Resp:
    def __init__(self, content, stop_reason):
        self.content, self.stop_reason = content, stop_reason


def _client(reply: str, stop: str = "end_turn", old_sdk: bool = False):
    """가짜 Anthropic 클라이언트. 요청을 _captured 에 남긴다."""
    class Msgs:
        def create(self, **kw):
            if old_sdk and kw.get("fallbacks"):
                raise TypeError("unexpected keyword argument 'fallbacks'")
            _captured.clear()
            _captured.update(kw)
            # 이 모델은 생각이 기본으로 켜져 있어 thinking 블록이 먼저 온다
            return _Resp([_Block("thinking"), _Block("text", reply)], stop)

    class Beta:
        messages = Msgs()

    class Client:
        beta = Beta()
        messages = Msgs()

    return Client()


def _use(client):
    worker._client_or_none = lambda: client        # noqa: SLF001


GOOD = json.dumps({"text": "결론을 먼저 말합니다.",
                   "evidence": [{"quote": "결론부터", "why": "원문"}]},
                  ensure_ascii=False)

# 규칙이 실제로 큐에 올리는 여섯 가지 작업
TASKS = {
    "rewrite_neutral_third_person": {"items": ["결론부터 말해주세요"], "min_common": 2},
    "translate_en_to_ko": {"source_text": "Be concise.", "preserve_verbatim": []},
    "curate_memorize": {"pairs": [], "emphasis": []},
    "curate_gap_comment": {"pairs": [], "emphasis": []},
    "propose_competency_mapping": {"area_name": "발표력", "known": ["전달력"]},
    "classify_emphasis": {"marked": [{"text": "음...", "format": "굵게"}], "role": "gap"},
}

# 구조화 출력이 받지 않는 항목. 남겨 두면 키를 넣은 뒤에야 드러난다.
UNSUPPORTED = ("minItems", "maxItems", "minLength", "maxLength",
               "minimum", "maximum", "multipleOf", "pattern")


def _scan(schema: dict, path: str, out: list) -> None:
    if not isinstance(schema, dict):
        return
    for key in UNSUPPORTED:
        if key in schema:
            out.append(f"{path}.{key}")
    if schema.get("type") == "object" and schema.get("additionalProperties") is not False:
        out.append(f"{path}: additionalProperties=false 누락")
    props = schema.get("properties") or {}
    missing = set(props) - set(schema.get("required") or [])
    if props and missing:
        out.append(f"{path}: required 에 빠진 항목 {sorted(missing)}")
    for name, sub in props.items():
        _scan(sub, f"{path}.{name}", out)
    if "items" in schema:
        _scan(schema["items"], f"{path}[]", out)


def main() -> int:
    print("\n── 출력 스키마가 구조화 출력 규격을 지키는가")
    problems: list = []
    for name in ("OUTPUT_SCHEMA", "MEMORIZE_SCHEMA", "EMPHASIS_SCHEMA"):
        _scan(getattr(prompts, name), name, problems)
    check("스키마 위반", problems or "없음", ok=not problems)

    # engine 표기는 환경변수(USE_LLM)를 보므로 키 없는 테스트에서는 항상 "mock"
    # 이다. 여기서 볼 것은 표기가 아니라 **요청이 실제로 나갔는가** 이므로,
    # 가짜 클라이언트가 붙잡은 요청으로 판정한다.
    print("\n── 여섯 작업이 모두 요청을 만들어 내는가")
    _use(_client(GOOD))
    for task, payload in TASKS.items():
        _captured.clear()
        out = worker.generate("R-XX", task, payload)
        sent = bool(_captured.get("model")) and bool(_captured.get("output_config"))
        check(task, f"요청 전송={sent} · 본문={(out.get('text') or '')[:12]!r}",
              ok=sent and bool(out.get("text")))

    print("\n── 요청의 실제 모양")
    oc = _captured.get("output_config") or {}
    check("max_tokens (생각+응답을 합쳐 자른다)", _captured.get("max_tokens"),
          ok=(_captured.get("max_tokens") or 0) >= 16000)
    check("출력 형식", (oc.get("format") or {}).get("type"), "json_schema")
    check("effort", oc.get("effort"), ok=bool(oc.get("effort")))

    print("\n── 생각 블록을 건너뛰고 text 만 읽는가")
    out = worker.generate("R-11", "rewrite_neutral_third_person",
                          TASKS["rewrite_neutral_third_person"])
    check("본문", out.get("text"), "결론을 먼저 말합니다.")

    print("\n── 실패 이유를 구분해서 알려 주는가")
    for stop, word in (("refusal", "거절"), ("max_tokens", "잘렸")):
        _use(_client(GOOD, stop=stop))
        out = worker.generate("R-11", "rewrite_neutral_third_person",
                              TASKS["rewrite_neutral_third_person"])
        check(stop, out.get("error"), ok=word in (out.get("error") or ""))

    print("\n── 낡은 SDK 면 일반 경로로 한 번만 더 시도하는가")
    _use(_client(GOOD, old_sdk=True))
    _captured.clear()
    out = worker.generate("R-11", "rewrite_neutral_third_person",
                          TASKS["rewrite_neutral_third_person"])
    check("fallbacks 를 빼고 다시 보냄", _captured.get("fallbacks", "빠짐"),
          ok=_captured.get("fallbacks") is None and bool(out.get("text")))

    print("\n── 깨진 응답이 와도 리포트는 계속 나오는가")
    _use(_client("이건 JSON 이 아닙니다"))
    out = worker.generate("R-11", "rewrite_neutral_third_person",
                          TASKS["rewrite_neutral_third_person"])
    check("목으로 내려감", out.get("engine"), "mock")

    print()
    if _fails:
        print(f"실패 {len(_fails)}건: {_fails}")
        return 1
    print("전부 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
