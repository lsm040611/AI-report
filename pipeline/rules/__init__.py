"""정제 규칙 19개.

import 만으로 @rule 데코레이터가 실행되어 REGISTRY 가 채워진다.
`/rules` 엔드포인트가 이 등록부를 계약 규칙표와 대조하는 용도로 노출한다.
"""
from . import report, semantic, structural, survey  # noqa: F401  (등록 부작용이 목적)
from .base import (AI, BLOCK_QUOTE, CODE, EVIDENCE_REQUIRED, GENERATION_RULES,
                   HOLD, HUMAN, NOTICE, REGISTRY, REVIEW, RuleContext,
                   add_flag, auto_resolve, is_sendable, mark_applied,
                   max_severity, quote_allowed, request_handoff, rule)

__all__ = [
    "REGISTRY", "RuleContext", "rule",
    "CODE", "AI", "HUMAN",
    "NOTICE", "REVIEW", "HOLD", "BLOCK_QUOTE",
    "GENERATION_RULES", "EVIDENCE_REQUIRED",
    "add_flag", "mark_applied", "request_handoff",
    "is_sendable", "max_severity", "quote_allowed", "auto_resolve",
    "structural", "semantic", "survey", "report",
]
