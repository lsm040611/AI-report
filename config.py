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
# 호스팅에서 영구 디스크를 붙이면 여기에 마운트된다. 붙이지 않았으면 없는 경로다.
DISK = os.getenv("HR_DISK", "/var/data")


def _data_dir() -> str:
    """자료를 어디에 둘지 **직접 보고** 정한다.

    영구 디스크가 붙어 있으면 거기, 아니면 지금 폴더. 사람이 설정 두 칸을
    고쳐 맞추게 하면 반드시 잊는다 — 잊는 쪽이 기본값이면 안 된다.
    무료로 배포하면 디스크가 없으니 알아서 지금 폴더를 쓰고, 나중에 요금제를
    올려 디스크를 붙이면 그때부터 알아서 디스크를 쓴다. 고칠 것이 없다.
    """
    return DISK if os.path.isdir(DISK) and os.access(DISK, os.W_OK) else "."


def _writable(path: str) -> bool:
    """이 경로에 파일을 만들 수 있는가. 폴더가 아직 없으면 부모를 본다."""
    probe = path if os.path.isdir(path) else os.path.dirname(path) or "."
    return os.path.isdir(probe) and os.access(probe, os.W_OK)


def _fallback(what: str, given: str, instead: str) -> str:
    """쓸 수 없는 경로를 받았을 때, 죽지 말고 대신 쓸 곳을 알리고 계속 간다.

    설정이 한 칸 어긋났다고 서버가 통째로 안 뜨면, 로그를 읽을 줄 모르는
    사람에게는 원인을 알 방법이 없다. 굴러가되 무엇을 어떻게 바꿨는지
    로그 맨 위에 크게 남긴다.
    """
    print(f"[설정] {what} 로 지정된 '{given}' 에 쓸 수 없어 "
          f"'{instead}' 를 대신 씁니다. "
          f"(영구 디스크를 붙이지 않은 배포라면 정상입니다)")
    return instead


def _db_url() -> str:
    """DB 주소. 배포처가 주는 이름(DATABASE_URL)도 함께 받는다.

    렌더 같은 호스팅은 데이터베이스를 붙이면 `DATABASE_URL` 을 자동으로 꽂아
    준다. 그런데 그 값이 `postgres://` 로 시작하는데 SQLAlchemy 2.0 은 이
    표기를 모른다 — 여기서 한 번 바꿔 주지 않으면 배포하자마자 죽는다.
    """
    # '/var/data' 면 슬래시가 넷이 되어 절대경로가 된다 (sqlite 표기 규칙)
    default = f"sqlite:///{_data_dir()}/hr_report.db"
    url = (os.getenv("HR_DB_URL") or os.getenv("DATABASE_URL") or "").strip()
    if not url:
        return default
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    if url.startswith("sqlite:///") and not _writable(url[len("sqlite:///"):]):
        return _fallback("HR_DB_URL", url, default)
    return url


def _storage_dir() -> str:
    default = os.path.join(_data_dir(), "storage")
    given = (os.getenv("HR_STORAGE") or "").strip()
    if not given:
        return default
    return given if _writable(given) else _fallback("HR_STORAGE", given, default)


DB_URL = _db_url()
STORAGE_DIR = _storage_dir()

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
