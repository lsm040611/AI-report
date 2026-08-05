"""표현 계층 — 정규화 카드를 사람이 읽는 HTML 로 바꾼다."""
from .adapter import to_presentation_card
from .template import CSS, render

__all__ = ["to_presentation_card", "render", "CSS"]
