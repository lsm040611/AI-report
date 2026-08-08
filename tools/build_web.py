# -*- coding: utf-8 -*-
"""UI 프로토타입 + 연결 패치 → 서버가 내주는 한 장짜리 페이지.

    py -3.10 tools/build_web.py "…\\교육 평가 리포트 웹페이지_0806V5.html"

원본 프로토타입은 디자인 툴이 만든 번들이다. 그 안에는 화면 템플릿, 로직,
디자인 시스템, 그리고 **한글 폰트 9.5MB** 가 들어 있다. 여기서 하는 일은 셋.

  1. 폰트 TTF 를 뺀다 — 같은 서체의 woff2 가 이미 들어 있어 화면은 그대로다.
     10MB → 600KB. 무료 서버에서 이 차이는 체감된다.
  2. web/patch.js 를 로직 뒤에 붙인다 — setTimeout 흉내를 실제 호출로 바꾼다.
  3. 결과를 web/index.html 로 저장한다. 서버가 /app 에서 내준다.

**원본은 고치지 않는다.** UI 트랙이 프로토타입을 새로 뽑으면 이 스크립트를
다시 돌리면 된다. 패치는 함수 이름에만 기대고 있어서 화면이 바뀌어도 대개 붙는다.
"""
from __future__ import annotations

import base64
import gzip
import io
import json
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "web", "index.html")
PATCH = os.path.join(ROOT, "web", "patch.js")

# 원본을 못 찾을 때 뒤져 볼 곳
GUESSES = [
    os.path.join(ROOT, "web", "prototype.html"),
    os.path.expanduser(r"~\OneDrive\문서\카카오톡 받은 파일"
                       r"\교육 평가 리포트 웹페이지_0806V5.html"),
]

# 뺄 자산. woff2 로 같은 서체가 이미 들어 있어 화면은 달라지지 않는다.
DROP_MIME = ("font/ttf", "font/otf", "application/x-font-ttf")


def main() -> None:
    src = sys.argv[1] if len(sys.argv) > 1 else _find()
    if not src or not os.path.exists(src):
        print("프로토타입 HTML 을 찾지 못했습니다.")
        print("경로를 인자로 주시거나 web/prototype.html 로 복사해 두십시오.")
        print("  py -3.10 tools/build_web.py \"C:\\...\\교육 평가 리포트 웹페이지_0806V5.html\"")
        sys.exit(1)

    raw = open(src, encoding="utf-8").read()
    print(f"원본 {os.path.basename(src)}  {len(raw):,}자")

    raw, dropped = _strip_fonts(raw)
    print(f"  폰트 {dropped['count']}개 뺌 ({dropped['bytes']:,} 바이트) → {len(raw):,}자")

    raw = _apply_patch(raw)
    _verify(raw)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(raw)
    print(f"\n완성 → {os.path.relpath(OUT, ROOT)}  {os.path.getsize(OUT):,} 바이트")
    print("서버를 켜고 /app 으로 들어가시면 됩니다.")


def _verify(raw: str) -> None:
    """패치가 **실행되는 자리**에 들어갔는지 확인한다.

    지난번에는 JSON 문자열 바깥에 떨어졌는데, 파일은 멀쩡히 만들어지고
    페이지도 열렸다. 아무 일도 안 일어날 뿐이었다. 그래서 여기서 막는다.
    """
    m = re.search(r'<script type="__bundler/template">(.*?)</script>', raw, re.S)
    if not m:
        print("  ! 템플릿 블록을 찾지 못했습니다."); sys.exit(1)
    try:
        page = json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        print(f"  ! 패치를 넣고 나서 템플릿 JSON 이 깨졌습니다 — {exc}")
        sys.exit(1)

    if MARKER not in page:
        print("  ! 패치가 페이지 안에 없습니다 — 넣은 자리가 틀렸습니다.")
        sys.exit(1)

    s = re.search(r'<script type="text/x-dc"[^>]*>(.*?)</script>', page, re.S)
    if not s or MARKER not in s.group(1):
        print("  ! 패치가 로직 스크립트 밖에 있습니다 — 실행되지 않습니다.")
        sys.exit(1)

    logic = s.group(1)
    if logic.count("{") != logic.count("}"):
        print("  ! 로직 스크립트의 중괄호가 맞지 않습니다.")
        sys.exit(1)
    for need in ("renderVals", "realUpload", "realSend", "paintInsight"):
        if need not in logic:
            print(f"  ! 패치에 {need} 가 없습니다."); sys.exit(1)
    print("  검증 — 패치가 로직 스크립트 안에서 실행됩니다")


def _find() -> str:
    for g in GUESSES:
        if os.path.exists(g):
            return g
    # 카톡 폴더에서 가장 최근 판을 집는다
    folder = os.path.expanduser(r"~\OneDrive\문서\카카오톡 받은 파일")
    if os.path.isdir(folder):
        cands = [os.path.join(folder, f) for f in os.listdir(folder)
                 if f.startswith("교육 평가 리포트 웹페이지") and f.endswith(".html")]
        if cands:
            return max(cands, key=os.path.getmtime)
    return ""


def _strip_fonts(raw: str):
    """번들 목록에서 TTF 를 들어낸다. 참조하는 CSS 규칙도 같이 지운다."""
    m = re.search(r'(<script type="__bundler/manifest">)(.*?)(</script>)', raw, re.S)
    if not m:
        return raw, {"count": 0, "bytes": 0}

    manifest = json.loads(m.group(2))
    gone, saved = [], 0
    for uid, meta in list(manifest.items()):
        if meta.get("mime") in DROP_MIME:
            data = base64.b64decode(meta["data"])
            saved += len(gzip.decompress(data) if meta.get("compressed") else data)
            gone.append(uid)
            del manifest[uid]

    if not gone:
        return raw, {"count": 0, "bytes": 0}

    raw = raw[:m.start(2)] + json.dumps(manifest, ensure_ascii=False) + raw[m.end(2):]
    # 사라진 파일을 가리키는 @font-face 는 남겨 두면 404 를 부른다
    for uid in gone:
        raw = re.sub(r"@font-face\s*\{[^}]*" + re.escape(uid) + r"[^}]*\}", "", raw)
    return raw, {"count": len(gone), "bytes": saved}


MARKER = "/* ── 엔진 연결 패치 (tools/build_web.py) ── */"

# 번들 안에서 로직은 `<script type="text/x-dc">` 한 덩이다. 다만 그 태그가
# 통째로 JSON 문자열 안에 들어 있어 따옴표와 슬래시가 escape 돼 있다.
#   여는 태그  <script type=\"text/x-dc\" …>
#   닫는 태그  <\u002Fscript>
# 닫는 태그를 평문 `</script>` 로 찾으면 **템플릿 블록 전체의 끝**이 먼저 잡혀서,
# 패치가 JSON 문자열 바깥에 떨어진다. 그러면 페이지에서 실행되지 않고 조용히
# 아무 일도 안 일어난다 — 한 번 그렇게 당했다.
OPEN_ESCAPED = re.compile(r'<script type=\\"text/x-dc\\"[^>]*?>')
OPEN_PLAIN = re.compile(r'<script type="text/x-dc"[^>]*?>')
CLOSE_ESCAPED = ("<\\u002Fscript>", "<\\/script>")


def _apply_patch(raw: str) -> str:
    """로직 스크립트 **안쪽 끝**에 패치를 붙인다.

    클래스 정의가 끝난 뒤에 실행되므로 프로토타입 메서드를 덮어쓸 수 있다.
    """
    if MARKER in raw:
        print("  이미 패치된 파일입니다 — 원본을 다시 받아서 돌리십시오.")
        sys.exit(1)
    patch = open(PATCH, encoding="utf-8").read()

    m = OPEN_ESCAPED.search(raw)
    if m:
        return _insert_escaped(raw, m.end(), patch)

    m = OPEN_PLAIN.search(raw)
    if not m:
        print("  로직 스크립트를 찾지 못했습니다 — 번들 형식이 바뀐 듯합니다.")
        sys.exit(1)
    close = raw.find("</script>", m.end())
    if close < 0:
        print("  로직 스크립트의 끝을 찾지 못했습니다.")
        sys.exit(1)
    print("  패치를 로직 끝에 붙였습니다 (평문 구간)")
    return raw[:close] + "\n" + MARKER + "\n" + patch + "\n" + raw[close:]


def _insert_escaped(raw: str, after: int, patch: str) -> str:
    """JSON 문자열 안에 넣는다 — 패치도 같은 방식으로 escape 해야 한다."""
    close = -1
    for tag in CLOSE_ESCAPED:
        at = raw.find(tag, after)
        if at >= 0 and (close < 0 or at < close):
            close = at
    if close < 0:
        print("  로직 스크립트의 끝(이스케이프된 닫는 태그)을 찾지 못했습니다.")
        sys.exit(1)

    blob = json.dumps("\n" + MARKER + "\n" + patch + "\n",
                      ensure_ascii=False)[1:-1]      # 앞뒤 따옴표만 벗긴다
    blob = blob.replace("</", "<\\u002F")            # 스크립트 조기 종료 방지
    print("  패치를 로직 끝에 붙였습니다 (이스케이프 구간)")
    return raw[:close] + blob + raw[close:]


if __name__ == "__main__":
    main()
