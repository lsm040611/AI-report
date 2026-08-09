# -*- coding: utf-8 -*-
"""구글에서 발송 권한을 한 번 받아 둔다.

    py -3.10 tools/gmail_setup.py

브라우저가 열리고, '허용'을 누르면 값 세 개가 화면에 나온다. 그 값을 Render 에
넣으면 웹사이트에서 메일이 나간다. 이 과정은 **한 번만** 하면 된다.

앞서 구글 클라우드에서 클라이언트 ID·비밀을 받아 두셔야 한다 —
docs/메일_Gmail설정.md 에 화면별로 적어 두었다.
"""
from __future__ import annotations

import http.server
import io
import json
import os
import secrets
import socket
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/gmail.send"

DONE_PAGE = """<!doctype html><meta charset=utf-8>
<title>다 됐습니다</title>
<body style="font-family:'Malgun Gothic',sans-serif;background:#F0E8D6;
             padding:60px 20px;text-align:center;color:#231D18">
<div style="max-width:460px;margin:0 auto;background:#fff;border-radius:8px;
            border-top:6px solid #DA1B33;padding:36px">
<h2 style="margin:0 0 10px">권한을 받았습니다</h2>
<p style="color:#6E655C;font-size:14px;line-height:1.7">
이 창을 닫고 <b>검은 창(터미널)</b> 으로 돌아가십시오.<br>
거기에 넣을 값 세 개가 적혀 있습니다.</p>
</div></body>"""


def main() -> None:
    print("=" * 62)
    print("  Gmail 발송 권한 받기 — 한 번만 하면 됩니다")
    print("=" * 62)

    cid = (os.getenv("HR_GMAIL_CLIENT_ID") or "").strip()
    csec = (os.getenv("HR_GMAIL_CLIENT_SECRET") or "").strip()
    if not cid:
        print("\n구글 클라우드에서 받은 값을 붙여넣으십시오.")
        print("(docs/메일_Gmail설정.md 의 5단계에 있습니다)\n")
        cid = input("  클라이언트 ID     : ").strip()
        csec = input("  클라이언트 보안 비밀 : ").strip()
    if not cid or not csec:
        print("\n두 값이 다 있어야 합니다. 다시 실행해 주십시오.")
        sys.exit(1)

    port = _free_port()
    redirect = f"http://localhost:{port}"
    state = secrets.token_urlsafe(16)
    got: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                                   # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] == state:
                got["code"] = q.get("code", [""])[0]
                got["error"] = q.get("error", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(DONE_PAGE.encode("utf-8"))

        def log_message(self, *a):                          # 조용히
            pass

    server = http.server.HTTPServer(("localhost", port), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()

    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cid,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",     # 이게 있어야 다시 쓸 수 있는 값을 준다
        "prompt": "consent",          # 두 번째부터도 새 값을 받도록
        "state": state,
    })

    print(f"\n브라우저가 열립니다. 계정을 고르고 '허용'을 누르십시오.")
    print(f"안 열리면 아래 주소를 직접 여십시오:\n\n{url}\n")
    try:
        webbrowser.open(url)
    except Exception:                                       # noqa: BLE001
        pass

    print("기다리는 중…")
    for _ in range(600):                                    # 최대 5분
        if got:
            break
        threading.Event().wait(0.5)
    server.server_close()

    if got.get("error"):
        print(f"\n거절되었습니다 — {got['error']}")
        print("동의 화면의 '테스트 사용자'에 본인 계정이 있는지 확인하십시오.")
        sys.exit(1)
    if not got.get("code"):
        print("\n시간이 지났습니다. 다시 실행해 주십시오.")
        sys.exit(1)

    body = urllib.parse.urlencode({
        "code": got["code"], "client_id": cid, "client_secret": csec,
        "redirect_uri": redirect, "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            out = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:                                # noqa: BLE001
        print(f"\n값을 받지 못했습니다 — {exc}")
        sys.exit(1)

    refresh = out.get("refresh_token")
    if not refresh:
        print("\n다시 쓸 수 있는 값(refresh token)이 오지 않았습니다.")
        print("구글 계정 > 보안 > 타사 앱 에서 이 앱의 권한을 지우고 다시 하십시오.")
        sys.exit(1)

    print("\n" + "=" * 62)
    print("  다 됐습니다. 아래 세 개를 Render 에 넣으십시오.")
    print("  (서비스 > Environment > Add Environment Variable)")
    print("=" * 62)
    print(f"\nHR_GMAIL_CLIENT_ID\n  {cid}")
    print(f"\nHR_GMAIL_CLIENT_SECRET\n  {csec}")
    print(f"\nHR_GMAIL_REFRESH_TOKEN\n  {refresh}")
    print("\n" + "-" * 62)

    _save_local(cid, csec, refresh)
    print("내 컴퓨터에서도 쓰도록 gmail.env 에 적어 두었습니다 "
          "(깃에 올라가지 않습니다).")
    print("\n확인: 서버에 넣은 뒤 주소/mailcheck 를 열어 보십시오.")


def _save_local(cid: str, csec: str, refresh: str) -> None:
    path = os.path.join(ROOT, "gmail.env")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# Gmail HTTPS 발송. 깃에 올라가지 않습니다 (.gitignore 의 *.env).\n")
        fh.write("# tools/gmail_setup.py 가 만들었습니다.\n\n")
        fh.write(f"HR_GMAIL_CLIENT_ID={cid}\n")
        fh.write(f"HR_GMAIL_CLIENT_SECRET={csec}\n")
        fh.write(f"HR_GMAIL_REFRESH_TOKEN={refresh}\n")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


if __name__ == "__main__":
    main()
