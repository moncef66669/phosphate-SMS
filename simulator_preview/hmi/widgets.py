"""Console primitives: chips, condition ribbon, KPI tiles, panels, tables.

Pure HTML/CSS builders plus thin Streamlit emitters. Everything here is
presentation only — no model is read or mutated.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

import streamlit as st

from . import tokens as t

# --------------------------------------------------------------- helpers -----


def esc(v) -> str:
    return html.escape(str(v), quote=True)


def fmt_t(v: Optional[float]) -> str:
    """Tonnes with an engineering magnitude suffix."""
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1_000_000:
        return f"{v / 1_000_000:,.2f} Mt"
    if a >= 1_000:
        return f"{v / 1_000:,.1f} kt"
    return f"{v:,.0f} t"


def fmt_pct(v: Optional[float], dp: int = 1) -> str:
    return "—" if v is None else f"{v:.{dp}f}"


# ----------------------------------------------------------------- chips -----
def chip(text: str, *, value: str = "", tone: str = "", icon: str = "",
         mono: bool = False, lead: bool = False) -> str:
    cls = " ".join(c for c in ("cx-chip", tone, "mono" if mono else "",
                              "lead" if lead else "") if c)
    inner = f'<i>{esc(icon)}</i>' if icon else ""
    inner += esc(text)
    if value != "":
        inner += f"&nbsp;<b>{esc(value)}</b>"
    return f'<span class="{cls}">{inner}</span>'


def chips(items: Iterable[str]) -> str:
    return '<span class="cx-legend">' + "".join(items) + "</span>"


# ------------------------------------------------------------- title bar -----
def titlebar(*, product: str, subtitle: str, right_chips: Sequence[str],
             clock_top: str, clock_bottom: str) -> None:
    st.markdown(
        '<div class="cx-title">'
        f'<div class="cx-mark"><b>{esc(product)}</b>'
        f'<span>{esc(subtitle)}</span></div>'
        '<div class="cx-title-sep"></div>'
        + "".join(right_chips)
        + '<div class="cx-title-right">'
        f'<div class="cx-clock"><b>{esc(clock_top)}</b><br>'
        f'{esc(clock_bottom)}</div></div>'
        "</div>", unsafe_allow_html=True)


# ------------------------------------------------------- condition ribbon ----
@dataclass
class RibbonCell:
    label: str
    count: int
    tone: str = "NORMAL"        # ALARM | CAUTION | ADVISORY | NORMAL


def condition_ribbon(cells: Sequence[RibbonCell], message: str = "") -> None:
    out = ['<div class="cx-ribbon">']
    for c in cells:
        hot = ""
        if c.count:
            hot = ("hot" if c.tone == "ALARM"
                   else "warm" if c.tone == "CAUTION" else "")
        col = t.SEVERITY.get(c.tone, t.NEUTRAL) if c.count else t.NEUTRAL
        out.append(
            f'<div class="cx-ribbon-cell {hot}">'
            f'<span class="cx-ribbon-lamp" style="background:{col}"></span>'
            f'<span><span class="cx-ribbon-n">{c.count}</span><br>'
            f'<span class="cx-ribbon-l">{esc(c.label)}</span></span></div>')
    if message:
        out.append(f'<div class="cx-ribbon-cell">'
                   f'<span class="cx-ribbon-msg">{esc(message)}</span></div>')
    out.append("</div>")
    st.markdown("".join(out), unsafe_allow_html=True)


# --------------------------------------------------------------- KPI tile ----
@dataclass
class Kpi:
    label: str
    value: str
    unit: str = ""
    sub: str = ""
    tone: str = ""              # ok | caut | alarm | info | ""
    bar_pct: Optional[float] = None


def kpi_strip(items: Sequence[Kpi]) -> None:
    out = ['<div class="cx-kpis">']
    for k in items:
        bar = ""
        if k.bar_pct is not None:
            w = max(0.0, min(100.0, k.bar_pct))
            bar = (f'<div class="cx-bar {k.tone}">'
                   f'<span style="width:{w:.1f}%"></span></div>')
        unit = f"<small>{esc(k.unit)}</small>" if k.unit else ""
        sub = f'<div class="cx-kpi-s">{esc(k.sub)}</div>' if k.sub else ""
        out.append(
            f'<div class="cx-kpi {k.tone}">'
            f'<div class="cx-kpi-l">{esc(k.label)}</div>'
            f'<div class="cx-kpi-v">{esc(k.value)}{unit}</div>'
            f"{sub}{bar}</div>")
    out.append("</div>")
    st.markdown("".join(out), unsafe_allow_html=True)


# ---------------------------------------------------------------- panels -----
def panel_head(title: str, note: str = "") -> None:
    st.markdown(f'<div class="cx-panel-h"><b>{esc(title)}</b>'
                f'<span>{esc(note)}</span></div>', unsafe_allow_html=True)


def kv_table(rows: Sequence[Tuple[str, str]], *,
             severities: Optional[dict] = None,
             text_cols: Optional[set] = None) -> None:
    """Inspector table. ``severities`` maps a key to ok|caut|alarm."""
    severities = severities or {}
    text_cols = text_cols or set()
    out = ['<table class="cx-kv">']
    for k, v in rows:
        sev = severities.get(k, "")
        cls = f' class="sev-{sev}"' if sev else ""
        vcls = "v txt" if k in text_cols else "v"
        out.append(f'<tr{cls}><td class="k">{esc(k)}</td>'
                   f'<td class="{vcls}">{esc(v)}</td></tr>')
    out.append("</table>")
    st.markdown("".join(out), unsafe_allow_html=True)


def condition_list(rows: Sequence[Tuple[str, str, str]],
                   empty: str = "no active conditions") -> None:
    """rows = (severity, tag, text)."""
    if not rows:
        st.markdown(f'<div class="cx-list"><div class="cx-empty">'
                    f'{esc(empty)}</div></div>', unsafe_allow_html=True)
        return
    out = ['<div class="cx-list">']
    for sev, tag, text in rows:
        out.append(f'<div class="cx-row {esc(sev)}">'
                   f'<span class="cx-row-tag">{esc(tag)}</span>'
                   f'<span class="cx-row-txt">{esc(text)}</span>'
                   f'<span class="cx-row-sev">{esc(sev)}</span></div>')
    out.append("</div>")
    st.markdown("".join(out), unsafe_allow_html=True)


def sparkline(values: Sequence[float], *, width: int = 190, height: int = 30,
              tone: str = t.INFO, marker_at: Optional[int] = None,
              baseline: Optional[float] = None) -> str:
    """Inline SVG trend. Used for per-pile inventory over the plan horizon."""
    vals = [float(v) for v in values if v is not None]
    if len(vals) < 2:
        return f'<div class="cx-note">trend needs a plan — {len(vals)} point(s)</div>'
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    n = len(vals)
    def px(i, v):
        return (2 + i * (width - 4) / (n - 1),
                height - 3 - (v - lo) / span * (height - 6))
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in
                   (px(i, v) for i, v in enumerate(vals)))
    base = ""
    if baseline is not None and lo <= baseline <= hi:
        _, by = px(0, baseline)
        base = (f'<line x1="2" x2="{width - 2}" y1="{by:.1f}" y2="{by:.1f}" '
                f'stroke="{t.CAUTION}" stroke-width="1" '
                'stroke-dasharray="3 2" />')
    dot = ""
    if marker_at is not None and 0 <= marker_at < n:
        mx, my = px(marker_at, vals[marker_at])
        dot = (f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="2.6" '
               f'fill="{t.ACCENT}" />')
    return (f'<svg width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" '
            f'style="display:block">'
            f'<rect width="{width}" height="{height}" fill="{t.INK_600}" />'
            f'{base}<polyline points="{pts}" fill="none" stroke="{tone}" '
            f'stroke-width="1.6" stroke-linejoin="round" />{dot}</svg>')


def frame_head(title: str, extras: Sequence[str] = ()) -> None:
    st.markdown('<div class="cx-frame-h">'
                f'<b>{esc(title)}</b>'
                '<span class="sp"></span>' + "".join(extras) + "</div>",
                unsafe_allow_html=True)


def note(markup: str) -> None:
    st.markdown(f'<div class="cx-note">{markup}</div>',
                unsafe_allow_html=True)


def footer(blocks: Sequence[str]) -> None:
    st.markdown('<div class="cx-foot">' + "".join(
        f"<div>{b}</div>" for b in blocks) + "</div>",
        unsafe_allow_html=True)
