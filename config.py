"""환경 설정 한 곳.

키가 없어도 전 구간이 돌아가야 한다는 것이 이 파일의 유일한 원칙이다.
ANTHROPIC_API_KEY 가 없으면 생성 단계가 자동으로 목(mock) 생성기로 떨어진다.
"""
from __future__ import annotations

import os


def _flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "off", "")


# --- 저장소 ---------------------------------------------------------------
DB_URL = os.getenv("HR_DB_URL", "sqlite:///./hr_report.db")
STORAGE_DIR = os.getenv("HR_STORAGE", "./storage")

# --- 검수 관문 ------------------------------------------------------------
# True  : 데모/테스트 모드. hold 를 자동 승인하고 업로드 한 번에 리포트까지 만든다.
# False : 운영 모드. 계약대로 담당자가 승인해야 다음 단계로 넘어간다.
AUTO_APPROVE = _flag("HR_AUTO_APPROVE", "1")

# --- 생성 ----------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL = os.getenv("HR_MODEL", "claude-opus-5")
EFFORT = os.getenv("HR_EFFORT", "medium")
USE_LLM = bool(ANTHROPIC_API_KEY)


def mode_banner() -> str:
    return (
        f"생성={'Claude API (' + MODEL + ')' if USE_LLM else '목 모드 (ANTHROPIC_API_KEY 없음)'} · "
        f"검수관문={'자동 승인' if AUTO_APPROVE else '담당자 승인 필요'}"
    )
