"""Canvas restyling for the process graphic.

The graphic itself is produced by the PRODUCTION ``process_mimic_figure`` —
there is no forked drawing code here, so a future geometry correction in
``simulator/`` appears in this preview automatically.

Two skins:

``Document``      the ISA-101-correct default: light-medium grey plot area,
                  desaturated margin, console typography. Only layout-level
                  properties are touched, so the drawing is byte-for-byte the
                  production rendering.

``Control room``  optional dark skin. Because the production drawing hard-codes
                  a light palette, this applies a BOUNDED colour transform:
                  an explicit table for the known palette constants, plus a
                  general luminance rule for anything unmapped (very light ->
                  darkened, very dark -> lightened, mid-tones such as
                  grade fills left alone). Unmapped colours therefore degrade
                  predictably instead of disappearing. This skin is a
                  presentation experiment — the Document skin is the reference.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

from . import tokens as t

DOCUMENT = "Document"
CONTROL_ROOM = "Control room"
MODES = [DOCUMENT, CONTROL_ROOM]

_HEX3 = re.compile(r"^#([0-9a-fA-F]{3})$")
_HEX6 = re.compile(r"^#([0-9a-fA-F]{6})$")
_RGBA = re.compile(r"^rgba?\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*,\s*([-\d.]+)"
                   r"(?:\s*,\s*([-\d.]+))?\s*\)$")

_NAMED = {"white": (255, 255, 255, 1.0), "black": (0, 0, 0, 1.0),
          "#fff": (255, 255, 255, 1.0), "#ffffff": (255, 255, 255, 1.0)}

# Explicit mapping for the production palette (ui.hmi_symbols / ui.theme).
# Structural greys invert; state colours are only brightened enough to keep
# their contrast on a dark backdrop — their MEANING must not change.
_TABLE = {
    "#eceff1": t.CANVAS_DARK,            # HMI_BG
    "#e4e8ea": t.CANVAS_DARK_PAPER,      # HMI_PAPER
    "#ffffff": "#1b232a",
    "#fff": "#1b232a",
    "white": "#1b232a",
    "#f3f6f9": "#1b232a",                # theme.BG
    "#4a4f54": "#9fb0bd",                # STATIC
    "#8a949e": "#7d8b97",                # STATIC_LIGHT / IDLE_GREY
    "#9aa4ac": "#818e99",                # TBC_GREY
    "#37424a": t.CANVAS_DARK_INK,        # LABEL
    "#17364f": "#dbe6f0",
    "#1f4e79": "#7fb2e0",                # NAVY
    "#2e6da4": "#6ea8dc",                # BLUE
    "#4d5b66": "#93a4b1",                # TEXT_GREY
    "#2e7d32": "#4fae5c",                # RUN_GREEN
    "#b97a14": "#dda63a",                # WARN_AMBER
    "#b23b3b": "#e0685c",                # FAULT_RED
    "#1f6fb3": "#5f9fd8",                # SEL_BLUE
    "#6f56a8": "#a48fe0",                # FUTURE_VIOLET
}


def _parse(c: str) -> Optional[Tuple[int, int, int, float]]:
    if not isinstance(c, str):
        return None
    s = c.strip().lower()
    if s in _NAMED:
        return _NAMED[s]
    m = _HEX3.match(s)
    if m:
        h = m.group(1)
        return (int(h[0] * 2, 16), int(h[1] * 2, 16), int(h[2] * 2, 16), 1.0)
    m = _HEX6.match(s)
    if m:
        h = m.group(1)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0)
    m = _RGBA.match(s)
    if m:
        r, g, b = (int(round(float(m.group(i)))) for i in (1, 2, 3))
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return (r, g, b, a)
    return None


def _emit(r: int, g: int, b: int, a: float) -> str:
    r, g, b = (max(0, min(255, int(round(v)))) for v in (r, g, b))
    if a >= 0.999:
        return f"#{r:02x}{g:02x}{b:02x}"
    return f"rgba({r},{g},{b},{a:.3f})"


def _lum(r: int, g: int, b: int) -> float:
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def _dark_map(col):
    """Map one colour into the control-room skin. Returns None to keep it."""
    if not isinstance(col, str) or not col.strip():
        return None
    key = col.strip().lower()
    if key in _TABLE:
        return _TABLE[key]
    parsed = _parse(col)
    if parsed is None:
        return None
    r, g, b, a = parsed
    L = _lum(r, g, b)
    if L > 0.86:                       # near-white fills / backgrounds
        return _emit(r * 0.16 + 22, g * 0.16 + 26, b * 0.16 + 31, a)
    if L < 0.22:                       # near-black line work / text
        return _emit(min(255, r + 150), min(255, g + 156), min(255, b + 160), a)
    return None                        # mid-tones (grade fills) untouched


def _walk(obj, keys, fn) -> None:
    for k in keys:
        try:
            cur = obj[k] if isinstance(obj, dict) else getattr(obj, k, None)
        except (KeyError, TypeError, ValueError):
            continue
        if cur is None:
            continue
        new = fn(cur)
        if new is None or new == cur:
            continue
        try:
            if isinstance(obj, dict):
                obj[k] = new
            else:
                setattr(obj, k, new)
        except (TypeError, ValueError):
            pass


def _to_control_room(fig) -> None:
    """Bounded colour transform over shapes, annotations and traces."""
    for sh in (fig.layout.shapes or ()):
        _walk(sh, ("fillcolor",), _dark_map)
        if sh.line is not None:
            _walk(sh.line, ("color",), _dark_map)
    for an in (fig.layout.annotations or ()):
        _walk(an, ("bgcolor", "bordercolor", "arrowcolor"), _dark_map)
        if an.font is not None:
            _walk(an.font, ("color",), _dark_map)
    for tr in fig.data:
        for holder in ("marker", "line", "textfont", "fillcolor"):
            if holder == "fillcolor":
                _walk(tr, ("fillcolor",), _dark_map)
                continue
            sub = getattr(tr, holder, None)
            if sub is None:
                continue
            _walk(sub, ("color",), _dark_map)
            inner = getattr(sub, "line", None)
            if inner is not None:
                _walk(inner, ("color",), _dark_map)
        hl = getattr(tr, "hoverlabel", None)
        if hl is not None:
            _walk(hl, ("bgcolor", "bordercolor"), _dark_map)


def restyle(fig, mode: str = DOCUMENT, *, height: int = 660,
            base_font_px: int = 11):
    """Apply the console canvas skin. Mutates and returns ``fig``."""
    dark = mode == CONTROL_ROOM
    if dark:
        _to_control_room(fig)
        plot, paper, ink, grid = (t.CANVAS_DARK, t.CANVAS_DARK_PAPER,
                                  t.CANVAS_DARK_INK, "#25303a")
    else:
        plot, paper, ink, grid = (t.CANVAS, t.CANVAS_PAPER, t.CANVAS_INK,
                                  "#d3dade")
    fig.update_layout(
        height=height,
        paper_bgcolor=paper,
        plot_bgcolor=plot,
        margin=dict(l=6, r=6, t=6, b=6),
        font=dict(family="IBM Plex Sans, Segoe UI, Roboto, sans-serif",
                  size=base_font_px, color=ink),
        hoverlabel=dict(
            bgcolor="#1b232a" if dark else "#ffffff",
            bordercolor=grid,
            font=dict(family="IBM Plex Mono, Consolas, monospace", size=11,
                      color=ink)),
        showlegend=False,
        dragmode="pan",
    )
    fig.update_xaxes(showgrid=False, zeroline=False)
    fig.update_yaxes(showgrid=False, zeroline=False)
    return fig


def chart(fig, *, height: int = 300, show_legend: bool = False,
          grid: bool = True):
    """Skin for ordinary console charts (Gantt, matrices, trends).

    These are console furniture, not the process graphic, so they follow the
    dark chrome rather than the ISA-101 drawing canvas.
    """
    fig.update_layout(
        height=height,
        paper_bgcolor=t.INK_700,
        plot_bgcolor=t.INK_700,
        margin=dict(l=8, r=8, t=8, b=8),
        font=dict(family="IBM Plex Sans, Segoe UI, sans-serif", size=10,
                  color=t.TXT_300),
        showlegend=show_legend,
        legend=dict(orientation="h", y=1.02, yanchor="bottom",
                    font=dict(size=9), bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(bgcolor=t.INK_600, bordercolor=t.INK_400,
                        font=dict(family="IBM Plex Mono, monospace",
                                  size=10, color=t.TXT_100)),
    )
    axis = dict(showgrid=grid, gridcolor=t.INK_500, zeroline=False,
                linecolor=t.INK_400, tickfont=dict(size=9,
                                                   color=t.TXT_500),
                title_font=dict(size=9, color=t.TXT_500))
    fig.update_xaxes(**axis)
    fig.update_yaxes(**axis)
    return fig


#: Plotly config shared by every console graphic.
PLOT_CONFIG = {
    "scrollZoom": True,
    "displaylogo": False,
    "doubleClick": "reset",
    "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d"],
    "toImageButtonOptions": {"format": "png", "scale": 2,
                             "filename": "stockyard-layout-and-flow"},
}
