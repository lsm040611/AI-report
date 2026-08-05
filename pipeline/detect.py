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

NAME_HINTS = ["이름", "성명", "참가자", "name", "피평가자", "대상자", "수강생"]
NOTE_HINTS = ["비고", "note", "remark", "특이사항"]
EMAIL_HINTS = ["이메일", "메일", "email", "e-mail", "mail"]
# 주소는 라벨이 없어도 모양으로 알아본다 — 라벨이 '연락처'일 수도 있기 때문이다
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

# 관계 열만은 부분 일치로 잡지 않는다.
# "관계"를 부분 일치로 두면 '이해관계 파악' 같은 역량명이 관계 열로 오인되어
# 점수 열이 통째로 사라진다. 실제로 그렇게 한 역량이 리포트에서 누락됐었다.
RELATION_EXACT = {
    "관계", "평가자관계", "평가자구분", "평가자유형", "응답자유형", "응답자구분",
    "구분", "relation", "relationship", "raterrelation", "ratertype",
}
SUMMARY_HINTS = ["평균", "average", "총점", "합계", "total", "종합"]
QUESTION_ID = re.compile(r"^Q\s*\d+$", re.IGNORECASE)

EMAIL = "email"            # 리포트를 보낼 주소
SCORE = "score"
SUMMARY = "summary"        # 원본 평균란 — 역량이 아니므로 점수에 섞지 않는다
NARRATIVE = "narrative"
NAME = "name"
RELATION = "relation"
NOTE = "note"
META = "meta"
UNKNOWN = "unknown"


@dataclass
class Column:
    index: int
    header: str                   # 첫 줄 — 역량명
    kind: str
    confidence: str = "high"      # high | medium | low
    scale: Optional[dict] = None
    desc: str = ""                # 둘째 줄부터 — 정의문

    @property
    def full_label(self) -> str:
        return f"{self.header} {self.desc}".strip()


def split_header(raw: str):
    """머리글 셀을 [역량명 / 정의문] 으로 나눈다.

    평가지 머리글은 한 칸 안에서 줄을 바꿔 두 줄로 쓰는 일이 많다.
        Accuracy
        Grammar & Vocabulary
    줄바꿈째로 역량명을 잡으면 회차마다 둘째 줄이 달라졌을 때
    ('문법·어휘 정확성' → 'Grammar & Vocabulary') 같은 역량이 다른 역량이 되어
    성장 비교가 통째로 어긋난다. 첫 줄만 이름으로 쓴다.
    """
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return "", ""
    parts = [p.strip() for p in text.split("\n") if p.strip()]
    return parts[0], " ".join(parts[1:])


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

    @property
    def summary_column(self) -> Optional[Column]:
        cols = self.by_kind(SUMMARY)
        return cols[0] if cols else None


def detect(sheet: Sheet) -> DetectedSchema:
    header_row = _find_header_row(sheet)
    meta = _read_meta_block(sheet, header_row)
    data_rows = [r for r in range(header_row + 1, sheet.height)
                 if not _row_is_blank(sheet, r)]

    columns = _classify_columns(sheet, header_row, data_rows,
                               find_scale_hint(sheet))
    schema = DetectedSchema(sheet.name, header_row, data_rows, columns, meta)

    # 데이터 방향 판정: 관계 열이 있고 이름이 반복되면 N응답 -> 1인
    rel = schema.by_kind(RELATION)
    name_col = schema.name_column
    names = []
    if name_col:
        for r in data_rows:
            row = sheet.row(r)
            if name_col.index < len(row):
                names.append(row[name_col.index].text.strip())
    names = [n for n in names if n]
    if rel and names and len(set(names)) < len(names):
        schema.direction = "aggregated_responses"

    if not name_col:
        schema.warnings.append(f"[{sheet.name}] 이름 열을 찾지 못함 — 담당자 지정 필요")
    elif name_col.confidence != "high":
        # 라벨로 못 찾아 '짧은 문자열 열'을 이름으로 추측한 경우.
        # 요약표(항목/값)를 사람 목록으로 오인할 수 있으므로 반드시 올린다.
        sample = ", ".join(names[:3])
        schema.warnings.append(
            f"[{sheet.name}] 이름 열을 라벨로 찾지 못해 '{name_col.header or chr(65 + name_col.index)}' "
            f"열을 이름으로 추측했습니다 (예: {sample}) — 담당자 확인 필요")
    if not schema.by_kind(SCORE):
        schema.warnings.append(f"[{sheet.name}] 점수 열을 찾지 못함 — 담당자 지정 필요")
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


def _classify_columns(sheet: Sheet, header_row: int, data_rows: List[int],
                      scale_hint: Optional[int] = None) -> List[Column]:
    header = sheet.row(header_row)
    columns: List[Column] = []

    for idx in range(sheet.width):
        raw_label = header[idx].text if idx < len(header) else ""
        label, desc = split_header(raw_label)
        values, texts = [], []
        for r in data_rows:
            row = sheet.row(r)
            if idx >= len(row):
                continue
            v, t = row[idx].value, row[idx].text
            if v is not None and str(v).strip():
                values.append(v)
            if t.strip():
                texts.append(t)

        # 분류는 두 줄을 합쳐서 본다 — 역할 힌트가 둘째 줄에 있을 수 있다
        kind, conf, scale = _classify_one(f"{label} {desc}".strip(), values, texts,
                                          scale_hint)
        columns.append(Column(idx, label, kind, conf, scale, desc))

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


def _classify_one(label: str, values: List, texts: List[str],
                  scale_hint: Optional[int] = None):
    """값의 성질을 먼저 보고, 그 다음에 라벨을 본다.

    순서가 중요하다. 라벨 힌트를 먼저 보면 역량명에 우연히 힌트 단어가 섞였을 때
    점수 열이 다른 종류로 오분류되어 조용히 사라진다.
    """
    low = label.lower()
    flat = re.sub(r"[\s·・/_-]", "", low)

    if not values:
        # 값이 없으면 라벨로만 판단할 수밖에 없다
        if any(h in low for h in NAME_HINTS):
            return NAME, "medium", None
        if flat in RELATION_EXACT:
            return RELATION, "medium", None
        return UNKNOWN, "low", None

    numeric_ratio = sum(1 for v in values if looks_numeric(v)) / len(values)
    if numeric_ratio >= 0.8:
        nums = [float(v) for v in values if looks_numeric(v)]
        # 평균란은 역량이 아니다. 점수에 섞으면 자기 자신이 평균에 들어간다.
        if any(h in low for h in SUMMARY_HINTS):
            return SUMMARY, "high", _scale_of(nums, scale_hint)
        conf = "high" if (label or QUESTION_ID.match(low)) else "medium"
        return SCORE, conf, _scale_of(nums, scale_hint)

    # 여기부터는 문자열 열이다.
    # 이메일이 먼저다 — 주소는 20자 안팎이라 그냥 두면 아래 길이 규칙에서
    # 서술 칸으로 잡혀 통째로 사라진다. 실제로 그렇게 사라지고 있었다.
    if texts and sum(1 for t in texts if EMAIL_RE.match(t.strip())) / len(texts) >= 0.6:
        return EMAIL, "high", None
    if any(h in low for h in EMAIL_HINTS):
        return EMAIL, "medium", None

    if any(h in low for h in NAME_HINTS):
        return NAME, "high", None
    if flat in RELATION_EXACT:
        return RELATION, "high", None
    if any(h in low for h in NOTE_HINTS):
        return NOTE, "high", None

    avg_len = sum(len(t) for t in texts) / max(len(texts), 1)
    if avg_len >= 25:
        return NARRATIVE, "high", None
    if avg_len >= 12:
        return NARRATIVE, "medium", None
    return META, "medium", None


# 실무에서 쓰이는 척도 상한. 관측 최댓값을 담는 가장 작은 것을 고른다.
SCALE_CAPS = (5, 7, 10, 100)


def _scale_of(nums: List[float], hint: Optional[int] = None) -> dict:
    """점수 분포에서 척도를 추론한다.

    5점과 10점만 알던 때는 7점 척도가 10점으로 잡혔다. 그러면 6.5점이
    '10점 만점에 6.5'로 그려져 실제보다 낮아 보인다. 시트에 '7점 만점' 같은
    문구가 있으면 그것을 먼저 믿고, 없으면 관측값으로 사다리를 탄다.
    """
    top = max(nums)
    if hint and top <= hint:
        cap = hint
    else:
        cap = next((c for c in SCALE_CAPS if top <= c), int(top))
    floor = 0 if min(nums) < 1 else 1
    has_half = any(abs(n - round(n)) > 1e-9 for n in nums)
    return {"min": floor, "max": cap, "step": 0.5 if has_half else 1}


# "7점 만점", "1~7점", "/10점", "10점 척도" 같은 표기
SCALE_HINT = re.compile(
    r"(?:(\d{1,3})\s*점\s*(?:만점|척도|기준))"
    r"|(?:만점\s*[:：]?\s*(\d{1,3}))"
    r"|(?:\d\s*[~\-–]\s*(\d{1,3})\s*점)"
    r"|(?:/\s*(\d{1,3})\s*점)")


def find_scale_hint(sheet: Sheet) -> Optional[int]:
    """시트 어디든 적힌 '몇 점 만점'을 찾는다.

    각주 행이나 머리부에 적어 두는 경우가 많다. 값 분포보다 신뢰도가 높다 —
    모두가 5점 이하를 받은 7점 척도는 분포만으로는 5점 척도와 구분되지 않는다.
    """
    for r in range(sheet.height):
        for cell in sheet.row(r):
            text = cell.text
            if "점" not in text:
                continue
            m = SCALE_HINT.search(text.replace(" ", ""))
            if not m:
                continue
            value = next((int(g) for g in m.groups() if g), None)
            if value and 2 <= value <= 100:
                return value
    return None


# --------------------------------------------------------------------------
# R-08 보조 : 문항 정의 시트 찾기
# --------------------------------------------------------------------------
DEF_SHEET_HINTS = ["문항", "정의", "question", "def", "항목"]


def extract_question_defs(sheets: List[Sheet]):
    """별도 시트의 [문항번호 · 역량명 · 정의문]을 뽑아 R-08 에 넘긴다.

    시트 이름이 아니라 '내용이 Q1·Q2… 로 시작하는 열이 있는가'로 판별하므로
    시트명이 처음 보는 것이어도 잡힌다.

    반환: (정의 dict, 정의를 제공한 시트 이름 집합)
    정의 시트는 데이터 시트가 아니므로, 호출자가 그 시트의 경고를 걸러 낼 수 있게
    이름도 함께 돌려준다.
    """
    defs: Dict[str, dict] = {}
    used: set = set()
    for sheet in sheets:
        header_row = _find_header_row(sheet)
        rows = [r for r in range(header_row + 1, sheet.height)
                if not _row_is_blank(sheet, r)]
        if not rows:
            continue

        qid_col = None
        for idx in range(sheet.width):
            hits = 0
            for r in rows:
                row = sheet.row(r)
                if idx < len(row) and QUESTION_ID.match(row[idx].text.strip()):
                    hits += 1
            if hits >= max(2, len(rows) // 2):
                qid_col = idx
                break
        if qid_col is None:
            continue

        # 문항번호 열 오른쪽의 텍스트 열 두 개 = 역량명 · 정의문
        text_cols = []
        for idx in range(qid_col + 1, sheet.width):
            vals = [sheet.row(r)[idx].text.strip() for r in rows
                    if idx < len(sheet.row(r))]
            vals = [v for v in vals if v]
            if vals and not all(looks_numeric(v) for v in vals):
                text_cols.append(idx)
            if len(text_cols) == 2:
                break
        if not text_cols:
            continue

        for r in rows:
            row = sheet.row(r)
            qid = row[qid_col].text.strip().upper().replace(" ", "")
            if not QUESTION_ID.match(qid):
                continue
            entry = {"area_name": row[text_cols[0]].text.strip()
                     if text_cols[0] < len(row) else None}
            if len(text_cols) > 1 and text_cols[1] < len(row):
                entry["definition"] = row[text_cols[1]].text.strip()
            defs[qid] = entry
            used.add(sheet.name)
    return defs, used
