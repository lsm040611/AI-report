"""UI ↔ 엔진 경계에서 **값이 고정된 것들**만 모은 곳.

UI 트랙이 통합 명세 v1 §5 에서 콕 집어 요청한 것이 이것이다 —
"issueCode 를 고정 집합으로 확정해 달라. 신규 코드가 UI 대응 없이 들어오면 안 된다."

그래서 규칙이 아니라 **어휘**를 여기 둔다. 코드를 새로 만들 때는 반드시 이 파일에
먼저 추가하고, `GET /contract` 로 UI 가 확인할 수 있게 한다. 엔진 안에서만 쓰는
내부 코드는 여기 올리지 않는다 — 올라온 것은 전부 UI 가 화면을 가진다는 뜻이다.
"""
from __future__ import annotations

from typing import Dict, List

SCHEMA_VERSION = "0.5"
CONTRACT_VERSION = "1.0"

# --------------------------------------------------------------------------
# 리포트 유형 3종. 화면 라벨은 UI 소관이라 여기서 정하지 않는다.
# --------------------------------------------------------------------------
SOURCE_TYPES = ("누적교육", "단발특강", "진단서베이")

# --------------------------------------------------------------------------
# 검수 플래그 severity 4단계 — pipeline/rules/base.py 와 같은 값이어야 한다.
# UI 배지가 1:1 로 매핑되므로 이 넷 외의 값은 화면이 그릴 수 없다.
# --------------------------------------------------------------------------
FLAG_SEVERITIES: Dict[str, str] = {
    "notice": "참고. 통과하며 사유만 표기한다",
    "review": "확인 필요. 담당자가 확인하기 전에는 승인할 수 없다",
    "hold": "보류. 승인 또는 제외를 반드시 선택해야 한다",
    "block_direct_quote": "요약만 노출. 승인은 자유이나 원문 인용 경로가 막힌다",
}

# --------------------------------------------------------------------------
# 행 단위 검증 코드 — **이 목록이 전부다.**
#   field  : UI 가 수정 모달의 입력 필드를 고르는 데 쓴다
#   input  : text | number | choice  (choice 는 candidates 중 고르기)
#   default_severity : 엔진이 특별한 사정이 없으면 붙이는 등급
# --------------------------------------------------------------------------
ISSUE_CODES: Dict[str, dict] = {
    "EMPID_FORMAT": {
        "field": "empId", "input": "text", "default_severity": "error",
        "pattern": r"^[A-Za-z]{1,3}-?\d{3,}$|^\d{4,}$",
        "label": "사번 형식 오류",
    },
    "EMPID_MISSING": {
        "field": "empId", "input": "text", "default_severity": "warning",
        "label": "사번 없음",
    },
    "SCORE_OUT_OF_RANGE": {
        "field": "score", "input": "number", "default_severity": "error",
        "label": "점수가 척도 범위를 벗어남",
    },
    "SCORE_NOT_NUMERIC": {
        "field": "score", "input": "number", "default_severity": "error",
        "label": "점수 칸에 숫자가 아닌 값",
    },
    "SCORE_MISSING": {
        "field": "score", "input": "number", "default_severity": "warning",
        "label": "점수 누락",
    },
    "AVERAGE_MISMATCH": {
        "field": "score", "input": "number", "default_severity": "warning",
        "label": "원본 평균과 재계산 평균 불일치",
    },
    "DUPLICATE_NAME": {
        "field": "duplicate", "input": "choice", "default_severity": "warning",
        "label": "동명이인 후보",
    },
    "NAME_MISSING": {
        "field": "name", "input": "text", "default_severity": "error",
        "label": "이름 없음",
    },
    "EMAIL_FORMAT": {
        "field": "email", "input": "text", "default_severity": "warning",
        "label": "이메일 형식 오류",
    },
    "EMAIL_MISSING": {
        "field": "email", "input": "text", "default_severity": "warning",
        "label": "이메일 없음 — 발송 대상에서 빠진다",
    },
    "SCALE_UNKNOWN": {
        "field": "score", "input": "number", "default_severity": "warning",
        "label": "척도를 찾지 못해 범위 검사를 건너뜀",
    },
}

# UI 가 행을 색으로 가르는 3단계. issueCode 의 default_severity 가 이 값이다.
ROW_SEVERITIES = ("error", "warning", "ok")

# --------------------------------------------------------------------------
# 리포트 본문 섹션 — 순서와 id 가 고정이다 (통합 명세 §2-③).
# 조건부 섹션은 재료가 없으면 **아예 만들지 않는다.** 빈 섹션 금지.
# --------------------------------------------------------------------------
SECTIONS: List[dict] = [
    {"id": "items", "label": "항목별 평가", "conditional": False},
    {"id": "feedback", "label": "서술 피드백", "conditional": False},
    {"id": "compare", "label": "성장 비교", "conditional": True,
     "condition": "동일 과정 이전 회차 카드가 있을 때만"},
    {"id": "next", "label": "다음 학습 제안", "conditional": False},
]
SECTION_IDS = tuple(s["id"] for s in SECTIONS)

# --------------------------------------------------------------------------
# 강조 3종 — 색이 아니라 뜻으로 내보낸다. 색은 UI 디자인 토큰이 정한다.
# --------------------------------------------------------------------------
EMPHASIS_KINDS: Dict[str, str] = {
    "issue_expression": "고칠 표현",
    "corrected_expression": "권장 표현",
    "key_concept": "핵심 개념",
}

# 본문에서 위 뜻을 실어 나르는 클래스명. 엔진은 인라인 색을 넣지 않는다.
EMPHASIS_CLASS = {k: f"em-{k.replace('_', '-')}" for k in EMPHASIS_KINDS}


def describe() -> dict:
    """`GET /contract` 응답. UI 가 이 한 번의 호출로 어휘 전체를 받아 간다."""
    return {
        "contractVersion": CONTRACT_VERSION,
        "cardSchemaVersion": SCHEMA_VERSION,
        "sourceTypes": list(SOURCE_TYPES),
        "flagSeverities": FLAG_SEVERITIES,
        "rowSeverities": list(ROW_SEVERITIES),
        "issueCodes": ISSUE_CODES,
        "sections": SECTIONS,
        "emphasisKinds": EMPHASIS_KINDS,
        "emphasisClasses": EMPHASIS_CLASS,
        "notes": [
            "issueCodes 에 없는 코드는 엔진이 내보내지 않습니다.",
            "담당자가 확정한 sourceType·course 는 엔진이 재판정하지 않습니다.",
            "courseId 는 엔진이 발급합니다 (POST /courses 또는 commit 의 mode=create).",
            "본문 HTML 에 인라인 색상을 넣지 않습니다 — emphasisClasses 로만 표시합니다.",
        ],
    }
