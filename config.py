"""환경 설정 한 곳.

키가 없어도 전 구간이 돌아가야 한다는 것이 이 파일의 유일한 원칙이다.
ANTHROPIC_API_KEY 가 없으면 생성 단계가 자동으로 목(mock) 생성기로 떨어진다.
"""
from __future__ import annotations

import os

import localenv

# 폴더 안의 *.env 를 먼저 읽는다. 그래야 서버 켤 때마다 키를 손으로 넣지 않아도 된다.
localenv.load()


def _flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "off", "")


# --- 저장소 ---------------------------------------------------------------
def _db_url() -> str:
    """DB 주소. 배포처가 주는 이름(DATABASE_URL)도 함께 받는다.

    렌더 같은 호스팅은 데이터베이스를 붙이면 `DATABASE_URL` 을 자동으로 꽂아
    준다. 그런데 그 값이 `postgres://` 로 시작하는데 SQLAlchemy 2.0 은 이
    표기를 모른다 — 여기서 한 번 바꿔 주지 않으면 배포하자마자 죽는다.
    """
    url = (os.getenv("HR_DB_URL") or os.getenv("DATABASE_URL") or "").strip()
    if not url:
        return "sqlite:///./hr_report.db"
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


DB_URL = _db_url()
STORAGE_DIR = os.getenv("HR_STORAGE", "./storage")

# --- 검수 관문 ------------------------------------------------------------
# True  : 데모/테스트 모드. hold 를 자동 승인하고 업로드 한 번에 리포트까지 만든다.
# False : 운영 모드. 계약대로 담당자가 승인해야 다음 단계로 넘어간다.
AUTO_APPROVE = _flag("HR_AUTO_APPROVE", "1")

# --- 생성 ----------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
# 리포트 문장 생성용 모델. 짧은 한국어 재작성이 대부분이라 Sonnet 으로 충분하고,
# 같은 예산으로 훨씬 여러 번 돌려 볼 수 있다. HR_MODEL 로 바꿀 수 있다.
MODEL = os.getenv("HR_MODEL", "claude-sonnet-5")
EFFORT = os.getenv("HR_EFFORT", "medium")
USE_LLM = bool(ANTHROPIC_API_KEY)


def mode_banner() -> str:
    import auth
    store = "SQLite 파일" if DB_URL.startswith("sqlite") else "외부 DB"
    return (
        f"생성={'Claude API (' + MODEL + ')' if USE_LLM else '목 모드 (ANTHROPIC_API_KEY 없음)'} · "
        f"검수관문={'자동 승인' if AUTO_APPROVE else '담당자 승인 필요'} · "
        f"저장={store} · {auth.banner()}"
    )
