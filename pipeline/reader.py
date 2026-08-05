"""엑셀 원본 읽기. 서식을 잃지 않는 것이 이 모듈의 존재 이유.

pandas.read_excel 은 셀 서식을 전부 버리므로 R-05(강조 서식 -> 의미)를
구현할 수 없다. openpyxl 의 rich_text 경로로 셀 안의 조각(run)별
색·굵기·밑줄을 읽어 계약의 emphasis 어휘로 변환한다.

R-05 매핑
    빨강            -> issue_expression      (문제 표현)
    굵게 + 밑줄     -> corrected_expression  (교정 표현)
    굵게            -> key_concept           (핵심 개념)
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, List, Optional

from openpyxl import load_workbook

try:                                    # openpyxl 3.1+
    from openpyxl.cell.rich_text import CellRichText
except ImportError:                     # pragma: no cover
    CellRichText = ()                   # type: ignore[assignment]


@dataclass
class Cell:
    coord: str
    value: Any
    runs: List[dict] = field(default_factory=list)   # [{"text","emphasis"}]

    @property
    def text(self) -> str:
        if self.runs:
            return "".join(r["text"] for r in self.runs)
        return "" if self.value is None else str(self.value)


@dataclass
class Sheet:
    name: str
    grid: List[List[Cell]]

    def row(self, i: int) -> List[Cell]:
        return self.grid[i] if 0 <= i < len(self.grid) else []

    @property
    def height(self) -> int:
        return len(self.grid)

    @property
    def width(self) -> int:
        return max((len(r) for r in self.grid), default=0)


def read_workbook(path: str) -> List[Sheet]:
    """원본은 절대 수정하지 않는다. 읽기 전용으로만 연다."""
    try:
        wb = load_workbook(path, data_only=True, rich_text=True)
    except TypeError:
        # openpyxl 3.0 이하 — 부분 서식을 읽을 수 없다. 셀 전체 서식만 살린다.
        wb = load_workbook(path, data_only=True)

    sheets = []
    for ws in wb.worksheets:
        grid = []
        for row in ws.iter_rows():
            grid.append([Cell(c.coordinate, _plain(c.value), _to_runs(c)) for c in row])
        sheets.append(Sheet(ws.title, grid))
    wb.close()
    return sheets


def _plain(value):
    """`.value` 에는 저장 가능한 형태만 남긴다.

    서식이 섞인 셀의 원래 값은 openpyxl 의 CellRichText — 파이썬 객체다.
    이게 카드에 그대로 실리면 DB(JSON 컬럼)에 저장할 때 터진다.
    서식 정보는 `.runs` 가 이미 갖고 있으므로, `.value` 는 평문이면 충분하다.
    """
    if CellRichText and isinstance(value, CellRichText):
        return "".join(b if isinstance(b, str) else (getattr(b, "text", "") or "")
                       for b in value)
    return value


def _to_runs(cell) -> List[dict]:
    """셀 -> runs. 서식이 전혀 없으면 빈 리스트(= 단일 평문)를 준다."""
    value = cell.value
    if value is None:
        return []

    if CellRichText and isinstance(value, CellRichText):
        runs = []
        for block in value:
            if isinstance(block, str):
                if block:
                    runs.append({"text": block, "emphasis": None})
                continue
            text = getattr(block, "text", "") or ""
            if not text:
                continue
            runs.append({"text": text,
                         "emphasis": _emphasis(getattr(block, "font", None))})
        return _merge(runs)

    if isinstance(value, str):
        emph = _emphasis(cell.font)          # 셀 전체에 걸린 서식
        return [{"text": value, "emphasis": emph}] if emph else []

    return []


def _emphasis(font) -> Optional[str]:
    if font is None:
        return None
    bold = bool(getattr(font, "b", False) or getattr(font, "bold", False))
    under = getattr(font, "u", None) or getattr(font, "underline", None)
    if _is_red(getattr(font, "color", None)):
        return "issue_expression"
    if bold and under:
        return "corrected_expression"
    if bold:
        return "key_concept"
    return None


def _is_red(color) -> bool:
    rgb = getattr(color, "rgb", None)
    if not isinstance(rgb, str) or len(rgb) < 6:
        return False
    try:
        r, g, b = (int(rgb[-6:][i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return False
    return r > 140 and r > g * 1.6 and r > b * 1.6


def _merge(runs: List[dict]) -> List[dict]:
    """인접한 같은 emphasis 조각을 합쳐 노이즈를 줄인다."""
    out: List[dict] = []
    for r in runs:
        if out and out[-1]["emphasis"] == r["emphasis"]:
            out[-1]["text"] += r["text"]
        else:
            out.append(dict(r))
    return out


def cell_is_blank(c: Optional[Cell]) -> bool:
    return c is None or c.value is None or not str(c.value).strip()


def looks_numeric(v: Any) -> bool:
    if isinstance(v, bool) or isinstance(v, (dt.date, dt.datetime)):
        return False
    if isinstance(v, (int, float)):
        return True
    try:
        float(str(v).strip())
        return True
    except (TypeError, ValueError):
        return False
