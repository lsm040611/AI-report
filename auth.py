"""접근 차단. 인터넷에 올릴 때 이것이 첫 번째 관문이다.

이 서버가 다루는 것은 사람의 인사 평가다. 주소만 알면 아무나 들어와 남의
리포트를 열어 보는 상태로 인터넷에 올려서는 안 된다.

그래서 **비밀번호가 없으면 바깥 요청을 아예 받지 않는다.** 설정을 깜빡한 채
배포하면 서비스가 조용히 열리는 것이 아니라 눈에 띄게 막힌다 — 안전한 쪽이
기본값이어야 한다.

    HR_AUTH_PASS 없음  →  내 컴퓨터에서만 열림 (지금까지와 똑같이 동작)
    HR_AUTH_PASS 있음  →  브라우저가 아이디·비밀번호를 물어본다
"""
from __future__ import annotations

import base64
import hmac
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, PlainTextResponse

# 로그인 없이 통과시키는 길.
#   /health     호스팅이 서버 생사를 확인한다
#   /mailcheck  메일 설정이 들어갔는지·포트가 열렸는지 확인한다
# 둘 다 **자료를 담지 않는다.** 켜져 있는가, 닿는가까지만 말한다.
OPEN_PATHS = ("/health", "/mailcheck")

# 내 컴퓨터에서 온 요청으로 보는 주소
LOCAL_HOSTS = ("127.0.0.1", "::1", "localhost", "testclient")


def _user() -> str:
    return os.getenv("HR_AUTH_USER", "hrd")


def _password() -> str:
    return (os.getenv("HR_AUTH_PASS") or "").strip()


def enabled() -> bool:
    return bool(_password())


def _is_local(request) -> bool:
    host = (request.client.host if request.client else "") or ""
    return host in LOCAL_HOSTS


def _ok(header: str) -> bool:
    """Basic 인증 헤더 대조. 글자 수로 정답을 짐작하지 못하게 상수 시간 비교."""
    if not header.lower().startswith("basic "):
        return False
    try:
        raw = base64.b64decode(header[6:].strip()).decode("utf-8")
    except Exception:                                  # noqa: BLE001
        return False
    user, _, pw = raw.partition(":")
    return (hmac.compare_digest(user, _user())
            and hmac.compare_digest(pw, _password()))


class Gate(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.url.path in OPEN_PATHS:
            return await call_next(request)

        if not enabled():
            if _is_local(request):
                return await call_next(request)
            return JSONResponse(status_code=503, content={
                "detail": "비밀번호가 설정되지 않아 바깥에서는 열 수 없습니다.",
                "how": "배포 환경에 HR_AUTH_PASS 를 넣고 다시 시작하십시오.",
                "why": "인사 평가 자료라 주소만 아는 사람이 열어서는 안 됩니다.",
            })

        if _ok(request.headers.get("authorization", "")):
            return await call_next(request)

        return PlainTextResponse(
            "로그인이 필요합니다.", status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="HR Report Engine", '
                                         'charset="UTF-8"'})


def install(app) -> None:
    app.add_middleware(Gate)


def banner() -> str:
    if enabled():
        return f"접근={_user()} 계정으로 로그인 필요"
    return "접근=내 컴퓨터에서만 (HR_AUTH_PASS 미설정)"
