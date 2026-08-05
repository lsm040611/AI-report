"""역량 레이더 차트 — 인라인 SVG.

자바스크립트 차트 라이브러리를 쓰지 않는 이유는 하나다. 이 리포트는 결국
브라우저 인쇄나 headless Chrome 으로 PDF가 된다. 그 경로에서 스크립트로 그린
그림은 비어 나오는 일이 잦다. 좌표를 직접 계산해 SVG 로 박아 두면 인쇄·PDF·
메일 본문 어디서나 같은 그림이 나온다.

    svg = radar_svg(axes, series, scale)

축 순서는 호출자가 정한다 — 원본 엑셀의 헤더 순서를 그대로 쓰기 위해서다.
"""
from __future__ import annotations

import html as _html
import math
from typing import List, Optional, Sequence

W, H = 380, 330
CX, CY = 190, 158
R = 104
LABEL_GAP = 20


def radar_svg(axes: Sequence[dict], series: Sequence[dict],
              scale: dict, rings: int = 4) -> str:
    """레이더 차트 SVG 문자열.

    axes   : [{"name": "Accuracy", "delta": 0.5|None, "desc": "…"}]
    series : [{"label": "2차수", "values": [4.0, None, …], "color": "var(--sk-red)",
               "dash": False, "fill": 0.10}]
    scale  : {"min": 1, "max": 5}
    """
    axes = list(axes)
    n = len(axes)
    if n < 3:
        return ""                      # 축이 셋 미만이면 레이더가 의미 없다

    lo = float(scale.get("min", 1))
    hi = float(scale.get("max", 5))
    if hi <= lo:
        return ""

    parts: List[str] = [
        f'<svg viewBox="0 0 {W} {H}" role="img" '
        f'style="width:100%;height:auto;display:block" '
        f'aria-label="역량 레이더 차트">'
    ]

    # ── 그물망 ────────────────────────────────────────────────
    for k in range(rings, 0, -1):
        r = R * k / rings
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in
                       (_pt(i, n, r) for i in range(n)))
        parts.append(f'<polygon points="{pts}" fill="{"#FFFFFF" if k == rings else "none"}" '
                     f'stroke="var(--line)" stroke-width="1"/>')

    for i in range(n):
        x, y = _pt(i, n, R)
        parts.append(f'<line x1="{CX}" y1="{CY}" x2="{x:.1f}" y2="{y:.1f}" '
                     f'stroke="var(--line-2)" stroke-width="1"/>')

    # ── 데이터 다각형 ─────────────────────────────────────────
    for s in series:
        vals = list(s.get("values") or [])
        color = s.get("color", "var(--sk-red)")
        dash = ' stroke-dasharray="4 3"' if s.get("dash") else ""
        pts = [_pt(i, n, R * _ratio(v, lo, hi)) if _num(v) else None
               for i, v in enumerate(vals[:n])]
        pts += [None] * (n - len(pts))

        if all(p is not None for p in pts):
            poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
            fill = s.get("fill")
            if fill:
                parts.append(f'<polygon points="{poly}" fill="{color}" '
                             f'fill-opacity="{fill}" stroke="none"/>')
            parts.append(f'<polygon points="{poly}" fill="none" stroke="{color}" '
                         f'stroke-width="2" stroke-linejoin="round"{dash}/>')
        else:
            # 결측 축이 있으면 그 축에 닿는 변은 잇지 않는다.
            # 없는 점을 0으로 찍어 이으면 '아주 낮은 점수'처럼 보인다.
            for i in range(n):
                a, b = pts[i], pts[(i + 1) % n]
                if a and b:
                    parts.append(
                        f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" '
                        f'x2="{b[0]:.1f}" y2="{b[1]:.1f}" stroke="{color}" '
                        f'stroke-width="2" stroke-linecap="round"{dash}/>')

        for p in pts:
            if p:
                parts.append(f'<circle cx="{p[0]:.1f}" cy="{p[1]:.1f}" r="3.4" '
                             f'fill="{color}"/>')

    # ── 축 라벨 + 증감 배지 ───────────────────────────────────
    for i, ax in enumerate(axes):
        x, y = _pt(i, n, R + LABEL_GAP)
        anchor, dy = _anchor(i, n)
        name = _esc(ax.get("name") or "")
        parts.append(f'<text x="{x:.1f}" y="{y + dy:.1f}" text-anchor="{anchor}" '
                     f'font-size="11.5" font-weight="700" fill="var(--ink)">{name}</text>')

        sub = _badge(ax)
        if sub:
            text, color = sub
            parts.append(f'<text x="{x:.1f}" y="{y + dy + 13:.1f}" text-anchor="{anchor}" '
                         f'font-size="10.5" font-weight="700" fill="{color}">{text}</text>')

    parts.append("</svg>")
    return "".join(parts)


def legend_html(series: Sequence[dict]) -> str:
    """차트 아래 범례. 실선/점선 구분을 색만으로 두지 않는다."""
    items = []
    for s in series:
        dash = "3 2" if s.get("dash") else ""
        items.append(
            f'<span class="it"><svg width="22" height="8" aria-hidden="true">'
            f'<line x1="1" y1="4" x2="21" y2="4" stroke="{s.get("color")}" '
            f'stroke-width="2" stroke-dasharray="{dash}"/></svg>{_esc(s.get("label", ""))}</span>')
    return f'<div class="legend radar-legend">{"".join(items)}</div>'


# --------------------------------------------------------------------------
def _pt(i: int, n: int, r: float):
    ang = -math.pi / 2 + (2 * math.pi * i / n)
    return CX + r * math.cos(ang), CY + r * math.sin(ang)


def _anchor(i: int, n: int):
    """라벨이 축 바깥으로 밀려나도록 정렬과 세로 보정을 정한다."""
    ang = -math.pi / 2 + (2 * math.pi * i / n)
    cos, sin = math.cos(ang), math.sin(ang)
    anchor = "middle" if abs(cos) < 0.25 else ("start" if cos > 0 else "end")
    dy = 0 if abs(sin) < 0.25 else (10 if sin > 0 else -2)
    return anchor, dy


def _ratio(v, lo: float, hi: float) -> float:
    return max(0.0, min(1.0, (float(v) - lo) / (hi - lo)))


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _badge(ax: dict) -> Optional[tuple]:
    if ax.get("missing"):
        return "미평가", "var(--faint)"
    d = ax.get("delta")
    if d is None:
        return ("🆕 신규", "var(--warn)") if ax.get("is_new") else None
    if abs(d) < 1e-9:
        return None                    # 변화 없으면 배지를 띄우지 않는다
    return (f"▲ +{d:.1f}", "var(--up)") if d > 0 else (f"▼ {abs(d):.1f}", "var(--down)")


def _esc(s) -> str:
    return _html.escape(str(s or ""), quote=True)
