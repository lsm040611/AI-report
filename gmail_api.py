"""Gmail 을 **HTTPS 로** 보낸다.

호스팅이 SMTP 포트(25·465·587)를 막아도 이 길은 막히지 않는다. 웹사이트를
여는 것과 같은 443 포트를 쓰기 때문이다. Render 무료 요금제에서 확인했다 —
SMTP 는 세 포트 모두 'Network is unreachable', HTTPS 는 정상.

외부 메일 업체를 끼지 않는다. 형이 이미 쓰는 지메일 계정 그대로다.
필요한 것은 구글에서 한 번 받아 두는 값 세 개뿐이다.

    HR_GMAIL_CLIENT_ID      구글 클라우드 OAuth 클라이언트 ID
    HR_GMAIL_CLIENT_SECRET  같은 화면의 비밀
    HR_GMAIL_REFRESH_TOKEN  tools/gmail_setup.py 가 한 번 받아 준다

라이브러리를 새로 깔지 않는다. 표준 라이브러리(urllib)만 쓴다 — 팀원 전원이
비개발자라 설치할 것이 늘어나는 쪽이 더 비싸다.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional, Tuple

TOKEN_URL = "https://oauth2.googleapis.com/token"
SEND_URL = ("https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
            "?alt=json")
SCOPE = "https://www.googleapis.com/auth/gmail.send"

# 접속표는 한 시간쯤 산다. 매번 새로 받으면 발송 한 통마다 왕복이 하나 늘고,
# 구글 쪽 한도도 괜히 깎인다.
_token: Optional[str] = None
_token_until: float = 0.0
_lock = threading.Lock()


def configured() -> bool:
    return all(os.getenv(k, "").strip() for k in
               ("HR_GMAIL_CLIENT_ID", "HR_GMAIL_CLIENT_SECRET",
                "HR_GMAIL_REFRESH_TOKEN"))


def _post(url: str, data: bytes, headers: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:400]
        raise RuntimeError(f"HTTP {exc.code} — {body}") from None


def access_token(force: bool = False) -> str:
    """새 접속표를 받아 온다. 살아 있으면 그대로 쓴다."""
    global _token, _token_until
    with _lock:
        if not force and _token and time.monotonic() < _token_until:
            return _token

        body = urllib.parse.urlencode({
            "client_id": os.getenv("HR_GMAIL_CLIENT_ID", "").strip(),
            "client_secret": os.getenv("HR_GMAIL_CLIENT_SECRET", "").strip(),
            "refresh_token": os.getenv("HR_GMAIL_REFRESH_TOKEN", "").strip(),
            "grant_type": "refresh_token",
        }).encode()
        out = _post(TOKEN_URL, body,
                    {"Content-Type": "application/x-www-form-urlencoded"})
        tok = out.get("access_token")
        if not tok:
            raise RuntimeError(f"접속표를 받지 못했습니다 — {out}")
        _token = tok
        # 만료 조금 전에 새로 받는다. 경계에서 실패하는 것이 제일 성가시다.
        _token_until = time.monotonic() + max(60, int(out.get("expires_in", 3600)) - 120)
        return tok


def check() -> dict:
    """**보내지 않고** 접속표만 받아 본다. 설정이 맞는지 여기서 갈린다."""
    if not configured():
        missing = [k for k in ("HR_GMAIL_CLIENT_ID", "HR_GMAIL_CLIENT_SECRET",
                               "HR_GMAIL_REFRESH_TOKEN")
                   if not os.getenv(k, "").strip()]
        return {"ok": False, "step": "설정", "transport": "gmail-api",
                "reason": f"{', '.join(missing)} 이(가) 없습니다",
                "hint": "tools/gmail_setup.py 를 한 번 돌려 값을 받으십시오"}
    try:
        access_token(force=True)
        return {"ok": True, "step": "완료", "transport": "gmail-api",
                "reason": "구글에서 발송 권한을 확인했습니다"}
    except Exception as exc:                     # noqa: BLE001
        msg = str(exc)
        return {"ok": False, "step": "인증", "transport": "gmail-api",
                "reason": msg, "hint": _hint(msg)}


def _hint(msg: str) -> str:
    low = msg.lower()
    if "invalid_grant" in low:
        return ("로그인 정보가 만료됐거나 취소됐습니다. 동의 화면이 '테스트' "
                "상태면 7일 뒤 만료됩니다 — '프로덕션'으로 게시하시거나, "
                "tools/gmail_setup.py 를 다시 돌려 새로 받으십시오.")
    if "invalid_client" in low or "unauthorized_client" in low:
        return "클라이언트 ID·비밀이 맞지 않습니다. 구글 클라우드에서 다시 복사하십시오."
    if "access_denied" in low:
        return "동의 화면에서 본인이 '테스트 사용자'로 등록돼 있는지 확인하십시오."
    if "insufficient" in low or "scope" in low:
        return f"권한 범위가 모자랍니다. {SCOPE} 를 허용해야 합니다."
    return "위 메시지를 그대로 알려 주시면 원인을 짚겠습니다."


def send_raw(mime_bytes: bytes) -> Tuple[bool, str]:
    """이미 만들어진 메일(MIME)을 그대로 보낸다.

    본문·첨부·수신자 규칙은 mailer.py 가 이미 정해 두었다. 여기서는
    운반만 한다 — 두 군데서 메일을 조립하면 SMTP 로 보낼 때와 내용이
    달라진다.
    """
    raw = base64.urlsafe_b64encode(mime_bytes).decode("ascii")
    body = json.dumps({"raw": raw}).encode("utf-8")
    for attempt in (1, 2):
        try:
            token = access_token(force=(attempt == 2))
            out = _post(SEND_URL, body, {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            })
            return True, out.get("id", "")
        except RuntimeError as exc:
            # 접속표가 그새 만료된 경우가 있다. 한 번만 새로 받아 다시 던진다.
            if attempt == 1 and "401" in str(exc):
                continue
            return False, str(exc)
    return False, "알 수 없는 실패"
