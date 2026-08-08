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
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(raw)
    print(f"\n완성 → {os.path.relpath(OUT, ROOT)}  {os.path.getsize(OUT):,} 바이트")
    print("서버를 켜고 /app 으로 들어가시면 됩니다.")


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


def _apply_patch(raw: str) -> str:
    """로직 스크립트 끝에 패치를 붙인다.

    번들 안에서 로직은 `<script type="text/x-dc">` 한 덩이다. 그 끝에 붙이면
    클래스 정의가 끝난 뒤에 실행되므로 프로토타입 메서드를 덮어쓸 수 있다.
    """
    patch = open(PATCH, encoding="utf-8").read()
    marker = "/* ── 엔진 연결 패치 (tools/build_web.py) ── */"
    if marker in raw:
        print("  이미 패치된 파일입니다 — 원본을 다시 받아서 돌리십시오.")
        sys.exit(1)

    m = re.search(r'(<script type=\\"text/x-dc\\"[^>]*>)(.*?)(<\\?/script>|'
                  r'<\\/script>|</script>)', raw, re.S)
    if not m:
        # 템플릿이 통째로 JSON 문자열로 이스케이프돼 있으면 여기로 온다
        return _apply_patch_escaped(raw, patch, marker)

    end = m.end(2)
    print("  패치를 로직 끝에 붙였습니다 (평문 구간)")
    return raw[:end] + "\n" + marker + "\n" + patch + "\n" + raw[end:]


def _apply_patch_escaped(raw: str, patch: str, marker: str) -> str:
    """번들이 로직을 이스케이프된 JSON 문자열로 품고 있는 경우.

    `<script type=\"text/x-dc\" …>` 처럼 따옴표가 escape 된 채 들어 있다.
    그 자리에 넣으려면 패치도 같은 방식으로 escape 해야 한다.
    """
    m = re.search(r'<script type=\\"text/x-dc\\"[^>]*?>', raw)
    if not m:
        print("  로직 스크립트를 찾지 못했습니다 — 번들 형식이 바뀐 듯합니다.")
        sys.exit(1)

    close = raw.find("<\\/script>", m.end())
    if close < 0:
        close = raw.find("<\\u002Fscript>", m.end())
    if close < 0:
        print("  로직 스크립트의 끝을 찾지 못했습니다.")
        sys.exit(1)

    blob = json.dumps(marker + "\n" + patch)[1:-1]      # 앞뒤 따옴표만 벗긴다
    blob = blob.replace("</", "<\\u002F")               # 조기 종료 방지
    print("  패치를 로직 끝에 붙였습니다 (이스케이프 구간)")
    return raw[:close] + "\\n" + blob + "\\n" + raw[close:]


if __name__ == "__main__":
    main()
