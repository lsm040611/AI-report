"""스키마 인식. "처음 보는 양식에도 동작해야 진짜 엔진"의 담당 모듈.

컬럼 위치를 하드코딩하지 않는다. 값의 성질로 구조를 추론하고,
확신이 없으면 unknown 으로 두고 담당자에게 넘긴다(R-12 확신도 3단계와 같은 태도).

산출물은 DetectedSchema — builder 가 이걸 보고 카드를 만든다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .reader import Sheet, cell_is_blank, looks_numeric

NAME_HINTS = ["이름", "성명", "참가자", "name", "피평가자", "대상자"]
RELATION_HINTS = ["관계", "relation", "평가자 구분", "응답자 유형"]
NOTE_HINTS = ["비고", "note", "remark", "참고"]
QUESTION_ID = re.compile(r"^Q\s*\d+$", re.IGNORECASE)

SCORE = "score"
NARRATIVE = "narrative"
NAME = "name"
RELATION = "relation"
NOTE = "note"
META = "meta"
UNKNOWN = "unknown"


@dataclass
class Column:
    index: int
    header: str
    kind: str
    confidence: str = "high"      # high | medium | low
    scale: Optional[dict] = None


@dataclass
class DetectedSchema:
    sheet: str
    header_row: int
    data_rows: List[int]
    columns: List[Column]
    meta: Dict[str, object] = field(default_factory=dict)
    direction: str = "individual_row"
    warnings: List[str] = field(default_factory=list)

    def by_kind(self, kind: str) -> List[Column]:
        return [c for c in self.columns if c.kind == kind]

    @property
    def name_column(self) -> Optional[Column]:
        cols = self.by_kind(NAME)
        return cols[0] if cols else None


def detect(sheet: Sheet) -> DetectedSchema:
    header_row = _find_header_row(sheet)
    meta = _read_meta_block(sheet, header_row)
    data_rows = [r for r in range(header_row + 1, sheet.height)
                 if not _row_is_blank(sheet, r)]

    columns = _classify_columns(sheet, header_row, data_rows)
    schema = DetectedSchema(sheet.name, header_row, data_rows, columns, meta)

    # 데이터 방향 판정: 관계 열이 있고 이름이 반복되면 N응답 -> 1인
    rel = schema.by_kind(RELATION)
    names = [sheet.row(r)[schema.name_column.index].text
             for r in data_rows if schema.name_column and
             len(sheet.row(r)) > schema.name_column.index]
    if rel and names and len(set(names)) < len(names):
        schema.direction = "aggregated_responses"

    if not schema.name_column:
        schema.warnings.append("이름 열을 찾지 못함 — 담당자 지정 필요")
    if not schema.by_kind(SCORE):
        schema.warnings.append("점수 열을 찾지 못함 — 담당자 지정 필요")
    return schema


# --------------------------------------------------------------------------
def _find_header_row(sheet: Sheet) -> int:
    """헤더 = 문자열이 가장 촘촘하고, 바로 아래에 데이터가 있는 행."""
    best, best_score = 0, -1.0
    limit = min(sheet.height, 20)
    for r in range(limit):
        row = sheet.row(r)
        labels = [c for c in row if not cell_is_blank(c) and not looks_numeric(c.value)]
        if len(labels) < 2:
            continue
        below = sheet.row(r + 1)
        filled_below = sum(1 for c in below if not cell_is_blank(c))
        score = len(labels) + filled_below * 0.5
        if score > best_score:
            best, best_score = r, score
    return best


def _read_meta_block(sheet: Sheet, header_row: int) -> Dict[str, object]:
    """헤더 위쪽의 키-값 쌍(과정명·강사·날짜 등). 키는 원본 라벨 그대로 둔다."""
    meta: Dict[str, object] = {}
    for r in range(header_row):
        cells = [c for c in sheet.row(r) if not cell_is_blank(c)]
        for i in range(0, len(cells) - 1, 2):
            key = str(cells[i].value).strip().rstrip(":：")
            if 1 <= len(key) <= 20:
                meta[key] = cells[i + 1].value
    return meta


def _row_is_blank(sheet: Sheet, r: int) -> bool:
    return all(cell_is_blank(c) for c in sheet.row(r))


def _classify_columns(sheet: Sheet, header_row: int,
                      data_rows: List[int]) -> List[Column]:
    header = sheet.row(header_row)
    columns: List[Column] = []

    for idx in range(sheet.width):
        label = header[idx].text.strip() if idx < len(header) else ""
        values = [sheet.row(r)[idx].value for r in data_rows
                  if idx < len(sheet.row(r))]
        values = [v for v in values if v is not None and str(v).strip()]
        texts = [sheet.row(r)[idx].text for r in data_rows
                 if idx < len(sheet.row(r))]
        texts = [t for t in texts if t.strip()]

        kind, conf, scale = _classify_one(label, values, texts)
        columns.append(Column(idx, label, kind, conf, scale))

    # 이름 열이 라벨로 안 잡혔으면, 왼쪽에서 첫 번째 '짧은 문자열' 열을 후보로
    if not any(c.kind == NAME for c in columns):
        for c in columns:
            if c.kind in (META, UNKNOWN):
                vals = [sheet.row(r)[c.index].text for r in data_rows
                        if c.index < len(sheet.row(r))]
                vals = [v for v in vals if v.strip()]
                if vals and all(len(v) <= 12 for v in vals):
                    c.kind, c.confidence = NAME, "medium"
                    break
    return columns


def _classify_one(label: str, values: List, texts: List[str]):
    low = label.lower()

    if any(h in low for h in NAME_HINTS):
        return NAME, "high", None
    if any(h in low for h in RELATION_HINTS):
        return RELATION, "high", None
    if any(h in low for h in NOTE_HINTS):
        return NOTE, "high", None
    if not values:
        return UNKNOWN, "low", None

    numeric_ratio = sum(1 for v in values if looks_numeric(v)) / len(values)
    if numeric_ratio >= 0.8:
        nums = [float(v) for v in values if looks_numeric(v)]
        return SCORE, "high" if (label or QUESTION_ID.match(low)) else "medium", _scale_of(nums)

    avg_len = sum(len(t) for t in texts) / max(len(texts), 1)
    if avg_len >= 25:
        return NARRATIVE, "high", None
    if avg_len >= 12:
        return NARRATIVE, "medium", None
    return META, "medium", None


def _scale_of(nums: List[float]) -> dict:
    hi = 10 if max(nums) > 5 else 5
    has_half = any(abs(n - round(n)) > 1e-9 for n in nums)
    return {"min": 1, "max": hi, "step": 0.5 if has_half else 1}
