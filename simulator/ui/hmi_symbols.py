"""Reusable high-performance-HMI vector symbols (pure Plotly, no Streamlit).

Design language (high-performance HMI):
  - neutral light-grey canvas, dark-grey static structures
  - GREEN only for running/healthy flow · AMBER warning/assumed/degraded ·
    RED only trip/fault/hard violation · BLUE selection/planning focus ·
    GREY stopped/idle · dashed grey = TBC / reference-only
  - every colour signal is duplicated by text, icon or line pattern

All functions APPEND shapes/annotations/traces to the given figure and never
mutate any model object. Coordinates are schematic site coordinates.
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

import plotly.graph_objects as go

XY = Tuple[float, float]

# ---- HMI palette ---------------------------------------------------------------
HMI_BG = "#eceff1"          # canvas
HMI_PAPER = "#e4e8ea"
STATIC = "#4a4f54"          # static structures
STATIC_LIGHT = "#8a949e"
RUN_GREEN = "#2e7d32"
WARN_AMBER = "#b97a14"
FAULT_RED = "#b23b3b"
SEL_BLUE = "#1f6fb3"
IDLE_GREY = "#8a949e"
TBC_GREY = "#9aa4ac"
LABEL = "#37424a"
FUTURE_VIOLET = "#6f56a8"  # static lifecycle, never selection/live state

# Static Benguerir process-line lifecycle styling.  This is deliberately
# separate from STATE_LAMP: OPERATING/FUTURE describes the configured plant
# line, not its current PLC running state.  Text + icon + line pattern make
# the meaning independent of colour.
PROCESS_LINE_STATUS_STYLES = {
    "OPERATING": {
        "label": "OPERATING", "icon": "●", "line": RUN_GREEN,
        "fill": "rgba(46,125,50,0.12)", "dash": None,
    },
    "DISABLED": {
        "label": "DISABLED", "icon": "✕", "line": FAULT_RED,
        "fill": "rgba(178,59,59,0.10)", "dash": "dash",
    },
    "FUTURE": {
        "label": "FUTURE", "icon": "◇", "line": FUTURE_VIOLET,
        "fill": "rgba(111,86,168,0.10)", "dash": "dot",
    },
}

# Shared operator-facing equipment-state code.  The mimic uses the same
# semantics for conveyor belts and the status lamps on stackers/reclaimers.
# Icons and the FUTURE line pattern duplicate the colour signal for
# accessibility.
ASSET_STATUS_STYLES = {
    "ACTIVE": {
        "label": "ACTIVE", "icon": "●", "color": RUN_GREEN,
        "dash": None,
    },
    "IDLE": {
        "label": "IDLE", "icon": "·", "color": IDLE_GREY,
        "dash": None,
    },
    "UNAVAILABLE": {
        "label": "UNAVAILABLE", "icon": "✕", "color": FAULT_RED,
        "dash": None,
    },
    "FUTURE": {
        "label": "FUTURE", "icon": "◇", "color": FUTURE_VIOLET,
        "dash": "dot",
    },
}

STATE_LAMP = {                       # machine/asset state -> lamp colour + icon
    "Stacking": (RUN_GREEN, "▶"),
    "Reclaiming": (RUN_GREEN, "◀"),
    "Travelling": (SEL_BLUE, "↔"),
    "Idle": (IDLE_GREY, "·"),
    "Maintenance": (WARN_AMBER, "🛠"),
    "Unavailable": (FAULT_RED, "✖"),
    "Fault": (FAULT_RED, "✖"),
    "Future": (FUTURE_VIOLET, "◇"),
}


def _ann(fig, x, y, text, size=10, color=LABEL, angle=0, bg=None, **kw):
    fig.add_annotation(x=x, y=y, text=text, showarrow=False, textangle=angle,
                       font=dict(size=size, color=color),
                       bgcolor=bg, **kw)


def lamp(fig, x, y, state: str, r: float = 0.55):
    col, icon = STATE_LAMP.get(state, (IDLE_GREY, "·"))
    fig.add_shape(type="circle", x0=x - r, x1=x + r, y0=y - r, y1=y + r,
                  fillcolor=col, line=dict(color="#ffffff", width=1))
    _ann(fig, x, y, icon, size=8, color="#ffffff")


# ================================ machines =======================================
def stacker(fig, gx: float, gy: float, tipx: float, tipy: float,
            state: str, tag: str, s: float = 1.0, dash: Optional[str] = None,
            selected: bool = False, label_side: int = 0):
    """Travelling stacker: gantry on rail, mast, luffing boom with belt line,
    discharge head (down triangle), status lamp, tag."""
    line = dict(color=SEL_BLUE if selected else STATIC,
                width=2.4 if selected else 1.6, dash=dash)
    # gantry on rail
    fig.add_shape(type="rect", x0=gx - 1.1 * s, x1=gx + 1.1 * s,
                  y0=gy - 0.45 * s, y1=gy + 0.45 * s,
                  fillcolor=STATIC if not dash else "rgba(0,0,0,0)",
                  line=line)
    # mast
    mtop = gy + 1.7 * s
    fig.add_shape(type="line", x0=gx, x1=gx, y0=gy + 0.45 * s, y1=mtop,
                  line=line)
    # boom + parallel belt line on the boom
    fig.add_shape(type="line", x0=gx, x1=tipx, y0=mtop, y1=tipy + 0.9 * s,
                  line=line)
    fig.add_shape(type="line", x0=gx, x1=tipx, y0=mtop - 0.22 * s,
                  y1=tipy + 0.68 * s,
                  line=dict(color=line["color"], width=0.9, dash=dash))
    # discharge head — filled triangle pointing DOWN at the working point
    fig.add_shape(type="path",
                  path=(f"M {tipx - 0.45 * s},{tipy + 0.9 * s} "
                        f"L {tipx + 0.45 * s},{tipy + 0.9 * s} "
                        f"L {tipx},{tipy + 0.15 * s} Z"),
                  fillcolor=line["color"], line=dict(width=0))
    # direction chevron along the boom
    _ann(fig, (gx + tipx) / 2, (mtop + tipy + 0.9 * s) / 2 + 0.25 * s, "❯",
         size=9, color=line["color"],
         angle=-math.degrees(math.atan2((tipy + 0.9 * s) - mtop, tipx - gx)))
    lamp(fig, gx + 1.15 * s, mtop, state, r=0.5 * s)
    if tag:
        _machine_label(fig, gx, gy, tag, s, label_side, selected)


def _machine_label(fig, gx, gy, tag, s, label_side, selected):
    size = 9 if s >= 0.7 else 7.5
    col = SEL_BLUE if selected else LABEL
    if label_side < 0:
        _ann(fig, gx - 1.5 * s, gy, f"<b>{tag}</b>", size=size, color=col,
             xanchor="right", bg="rgba(236,239,241,0.8)")
    elif label_side > 0:
        _ann(fig, gx + 1.5 * s, gy, f"<b>{tag}</b>", size=size, color=col,
             xanchor="left", bg="rgba(236,239,241,0.8)")
    else:
        _ann(fig, gx, gy - 0.95 * s - 0.35, f"<b>{tag}</b>", size=size,
             color=col, bg="rgba(236,239,241,0.8)")


def twin_boom_stacker(fig, gx: float, gy: float,
                      tip_up: Tuple[float, float],
                      tip_dn: Tuple[float, float],
                      state: str, tag: str, s: float = 1.0,
                      dash: Optional[str] = None, selected: bool = False,
                      label_side: int = 0):
    """ONE twin-/double-boom stacker (e.g. Benguerir B02 'Twice boom stacker
    ST3**'): a single gantry + mast with TWO luffing booms and discharge
    heads reaching the pile bank on each side. Never drawn as two machines."""
    line = dict(color=SEL_BLUE if selected else STATIC,
                width=2.4 if selected else 1.6, dash=dash)
    fig.add_shape(type="rect", x0=gx - 1.2 * s, x1=gx + 1.2 * s,
                  y0=gy - 0.45 * s, y1=gy + 0.45 * s,
                  fillcolor=STATIC if not dash else "rgba(0,0,0,0)",
                  line=line)
    mtop = gy + 1.5 * s
    fig.add_shape(type="line", x0=gx, x1=gx, y0=gy + 0.45 * s, y1=mtop,
                  line=line)
    for (tipx, tipy) in (tip_up, tip_dn):
        head_dy = 0.9 * s if tipy >= gy else -0.15 * s
        fig.add_shape(type="line", x0=gx, x1=tipx, y0=mtop,
                      y1=tipy + head_dy, line=line)
        fig.add_shape(type="path",
                      path=(f"M {tipx - 0.42 * s},{tipy + head_dy} "
                            f"L {tipx + 0.42 * s},{tipy + head_dy} "
                            f"L {tipx},{tipy + head_dy - 0.7 * s} Z"),
                      fillcolor=line["color"], line=dict(width=0))
    lamp(fig, gx + 1.3 * s, mtop, state, r=0.5 * s)
    if tag:
        _machine_label(fig, gx, gy, tag, s, label_side, selected)


def reclaimer(fig, gx: float, gy: float, tipx: float, tipy: float,
              state: str, tag: str, s: float = 1.0,
              dash: Optional[str] = None, selected: bool = False,
              label_side: int = 0):
    """Bucket-wheel reclaimer: gantry, boom, bucket wheel with spokes,
    reclaim-direction chevron (material flows tip -> gantry), lamp, tag."""
    line = dict(color=SEL_BLUE if selected else STATIC,
                width=2.4 if selected else 1.6, dash=dash)
    fig.add_shape(type="rect", x0=gx - 1.1 * s, x1=gx + 1.1 * s,
                  y0=gy - 0.45 * s, y1=gy + 0.45 * s,
                  fillcolor=STATIC if not dash else "rgba(0,0,0,0)",
                  line=line)
    btop = gy + 1.1 * s
    fig.add_shape(type="line", x0=gx, x1=tipx, y0=btop, y1=tipy, line=line)
    # bucket wheel: circle + 3 spokes
    r = 0.62 * s
    fig.add_shape(type="circle", x0=tipx - r, x1=tipx + r,
                  y0=tipy - r, y1=tipy + r,
                  fillcolor="#ffffff", line=line)
    for a in (0, 60, 120):
        dx = r * math.cos(math.radians(a))
        dy = r * math.sin(math.radians(a))
        fig.add_shape(type="line", x0=tipx - dx, x1=tipx + dx,
                      y0=tipy - dy, y1=tipy + dy,
                      line=dict(color=line["color"], width=0.9, dash=dash))
    # reclaim direction: chevron pointing back toward the gantry
    _ann(fig, (gx + tipx) / 2, (btop + tipy) / 2 + 0.28 * s, "❮",
         size=9, color=line["color"],
         angle=-math.degrees(math.atan2(tipy - btop, tipx - gx)))
    lamp(fig, gx - 1.15 * s, btop + 0.4 * s, state, r=0.5 * s)
    if tag:
        _machine_label(fig, gx, gy, tag, s, label_side, selected)


# ================================ stockpile lane ==================================
def stock_lane(fig, x0, y0, x1, y1, fill_pct: float, grade_col: str,
               border_col: str, border_dash: Optional[str], border_w: float,
               segments: Optional[List[Tuple[float, float, str]]] = None,
               safety_frac: Optional[float] = None,
               work_frac: Optional[Tuple[float, float]] = None,
               selected: bool = False):
    """Stockpile lane: capacity outline + grade fill + optional longitudinal
    segments [(f0,f1,color)] + safety-stock tick + working-limit bracket."""
    if selected:
        fig.add_shape(type="rect", x0=x0 - 0.25, x1=x1 + 0.25,
                      y0=y0 - 0.25, y1=y1 + 0.25,
                      line=dict(color=SEL_BLUE, width=3))
    fig.add_shape(type="rect", x0=x0, x1=x1, y0=y0, y1=y1,
                  fillcolor="#f6f8f9",
                  line=dict(color=border_col, width=border_w,
                            dash=border_dash), layer="below")
    w = x1 - x0
    fx = x0 + w * max(0.0, min(fill_pct, 100.0)) / 100.0
    if fx > x0 + 1e-9:
        if segments:
            for f0, f1, col in segments:
                fig.add_shape(type="rect", x0=x0 + w * f0 * fill_pct / 100.0,
                              x1=x0 + w * f1 * fill_pct / 100.0,
                              y0=y0 + 0.12, y1=y1 - 0.12,
                              fillcolor=col, line=dict(width=0),
                              layer="below")
        else:
            fig.add_shape(type="rect", x0=x0, x1=fx, y0=y0 + 0.12,
                          y1=y1 - 0.12, fillcolor=grade_col,
                          line=dict(width=0), layer="below")
    if safety_frac is not None and safety_frac > 0:
        sx = x0 + w * min(safety_frac, 1.0)
        fig.add_shape(type="line", x0=sx, x1=sx, y0=y0 - 0.18, y1=y1 + 0.18,
                      line=dict(color=FAULT_RED, width=1.4, dash="dot"))
    if work_frac is not None:
        wx0, wx1 = (x0 + w * max(0.0, min(f, 1.0)) for f in work_frac)
        yb = y1 + 0.28
        fig.add_shape(type="line", x0=wx0, x1=wx1, y0=yb, y1=yb,
                      line=dict(color=SEL_BLUE, width=2.2))
        for xx in (wx0, wx1):
            fig.add_shape(type="line", x0=xx, x1=xx, y0=yb - 0.18,
                          y1=yb + 0.18, line=dict(color=SEL_BLUE, width=2.2))


# ================================ conveyor ========================================
def _offset_polyline(pts: Sequence[XY], off: float) -> List[XY]:
    """Polyline offset by per-segment normals (schematic quality)."""
    out = []
    for i, (x, y) in enumerate(pts):
        j = min(i, len(pts) - 2)
        dx = pts[j + 1][0] - pts[j][0]
        dy = pts[j + 1][1] - pts[j][1]
        n = math.hypot(dx, dy) or 1.0
        out.append((x - off * dy / n, y + off * dx / n))
    return out


def _point_at(pts: Sequence[XY], frac: float) -> Tuple[float, float, float]:
    """(x, y, angle°) at fractional length along polyline."""
    segs = []
    total = 0.0
    for a, b in zip(pts[:-1], pts[1:]):
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        segs.append((a, b, d))
        total += d
    target = max(total * min(max(frac, 0.0), 1.0), 0.0)
    run = 0.0
    for a, b, d in segs:
        if run + d >= target - 1e-12:
            f = (target - run) / d if d else 0.0
            ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
            return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, ang
        run += d
    a, b, _ = segs[-1]
    return b[0], b[1], math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))


def conveyor(fig, pts: Sequence[XY], state: str = "idle",
             tag: str = "", sub: str = "", tph: Optional[float] = None,
             cap: Optional[float] = None, grade_col: Optional[str] = None,
             tbc: bool = False, selected: bool = False, phase: int = 0,
             belt_off: float = 0.16, chevron_every: float = 4.0,
             label_frac: float = 0.5, label_dy: float = 0.75,
             lab_size: float = 8.5,
             show_utilisation_pct: bool = True):
    """Industrial conveyor: double belt line + pulley markers + direction.

    ``unavailable`` is a real outage and therefore red. ``dimmed`` is only a
    presentation filter; it must never be mistaken for an equipment outage.
    """
    col = {
        "running": RUN_GREEN,
        "idle": IDLE_GREY,
        "tripped": FAULT_RED,
        "unavailable": FAULT_RED,
        "future": FUTURE_VIOLET,
        "dimmed": STATIC_LIGHT,
    }.get(state, IDLE_GREY)
    if selected:
        col = SEL_BLUE
    dash = "dot" if state == "future" else ("dash" if tbc else None)
    for off in (belt_off, -belt_off):
        o = _offset_polyline(pts, off)
        fig.add_trace(go.Scatter(
            x=[p[0] for p in o], y=[p[1] for p in o], mode="lines",
            line=dict(color=col, width=2.2 if state == "running" or selected
                      else 1.3, dash=dash),
            hoverinfo="skip", showlegend=False))
    # pulley/drive markers at the ends
    for px, py in (pts[0], pts[-1]):
        fig.add_shape(type="circle", x0=px - 0.22, x1=px + 0.22,
                      y0=py - 0.22, y1=py + 0.22, fillcolor="#ffffff",
                      line=dict(color=col, width=1.1, dash=dash))
    # direction chevrons (phase-shifted by time bucket to suggest motion);
    # TBC routes stay plain dashed — no implied flow direction
    if not tbc and state != "future":
        total = sum(math.hypot(b[0] - a[0], b[1] - a[1])
                    for a, b in zip(pts[:-1], pts[1:]))
        n = max(int(total / chevron_every), 1)
        for i in range(n):
            f = ((i + 0.5) / n + 0.18 * (phase % 3) / 3) % 1.0
            cx, cy, ang = _point_at(pts, f)
            _ann(fig, cx, cy, "❯", size=9 if state == "running" else 7,
                 color=(grade_col or col) if state == "running" else col,
                 angle=-ang)
    if tag or tph is not None:
        lx, ly, _ = _point_at(pts, label_frac)
        parts = [f"<b>{tag}</b>"] if tag else []
        if tph is not None and cap:
            flow = f"{tph:,.0f}/{cap:,.0f} t/h"
            if show_utilisation_pct:
                flow += f" ({100 * tph / cap:.0f} %)"
            parts.append(flow)
        elif tph is not None:
            parts.append(f"{tph:,.0f} t/h")
        elif cap:
            parts.append(f"cap {cap:,.0f} t/h")
        if sub:
            parts.append(sub)
        if state == "tripped":
            parts.append("⛔ TRIPPED")
        elif state == "unavailable":
            parts.append("✕ UNAVAILABLE")
        elif state == "future":
            parts.append("◇ FUTURE")
        if tbc:
            parts.append("REFERENCE ROUTE")
        _ann(fig, lx, ly + label_dy, " · ".join(parts), size=lab_size,
             color=(FAULT_RED if state in ("tripped", "unavailable")
                    else FUTURE_VIOLET if state == "future" else LABEL),
             bg="rgba(236,239,241,0.85)")


# ================================ transfer & routing =============================
def transfer_tower(fig, x, y, label="", s: float = 1.0, tbc: bool = False):
    dash = "dash" if tbc else None
    fig.add_shape(type="rect", x0=x - 0.8 * s, x1=x + 0.8 * s,
                  y0=y - 1.0 * s, y1=y + 1.0 * s, fillcolor="#f6f8f9",
                  line=dict(color=STATIC, width=1.5, dash=dash))
    for sgn in (1, -1):
        fig.add_shape(type="line", x0=x - 0.8 * s, x1=x + 0.8 * s,
                      y0=y - sgn * 1.0 * s, y1=y + sgn * 1.0 * s,
                      line=dict(color=STATIC_LIGHT, width=0.8, dash=dash))
    if label:
        _ann(fig, x, y - 1.45 * s, label, size=9,
             bg="rgba(236,239,241,0.8)")


def clamshell_gate(fig, x, y, label="", state: str = "idle", s: float = 1.0):
    col = {"running": RUN_GREEN, "idle": IDLE_GREY,
           "tripped": FAULT_RED}.get(state, IDLE_GREY)
    fig.add_shape(type="path",
                  path=(f"M {x},{y + 0.55 * s} L {x + 0.55 * s},{y} "
                        f"L {x},{y - 0.55 * s} L {x - 0.55 * s},{y} Z"),
                  fillcolor="#ffffff", line=dict(color=col, width=1.4))
    for sgn in (1, -1):        # two clamshell flap lines
        fig.add_shape(type="line", x0=x, x1=x + sgn * 0.4 * s,
                      y0=y, y1=y - 0.4 * s,
                      line=dict(color=col, width=1.1))
    if label:
        _ann(fig, x, y + 0.95 * s, label, size=8.5)


def diverter(fig, x, y, label="", s: float = 1.0, tbc: bool = False):
    dash = "dash" if tbc else None
    fig.add_shape(type="path",
                  path=(f"M {x - 0.5 * s},{y} L {x + 0.5 * s},{y + 0.45 * s} "
                        f"M {x - 0.5 * s},{y} L {x + 0.5 * s},{y - 0.45 * s}"),
                  line=dict(color=STATIC, width=1.6, dash=dash))
    if label:
        _ann(fig, x, y + 0.85 * s, label, size=8.5)


def instrument(fig, x, y, kind: str, s: float = 0.55, tbc: bool = False):
    """sampler 'S' · belt scale 'BS' · online analyser 'A' — circle symbols."""
    letter = {"sampler": "S", "scale": "BS", "analyser": "A"}.get(kind, "?")
    dash = "dash" if tbc else None
    fig.add_shape(type="circle", x0=x - s, x1=x + s, y0=y - s, y1=y + s,
                  fillcolor="#ffffff",
                  line=dict(color=STATIC, width=1.1, dash=dash))
    _ann(fig, x, y, letter, size=7.5, color=STATIC)


def hopper(fig, x, y, label="", s: float = 0.8, state: str = "idle",
           tbc: bool = False):
    """Compact mine-hopper silhouette; ratings deliberately remain off-canvas."""
    col = (RUN_GREEN if state == "running" else
           FAULT_RED if state == "tripped" else STATIC)
    dash = "dash" if tbc else None
    fig.add_shape(
        type="path",
        path=(
            f"M {x - 0.9 * s},{y + 0.55 * s} "
            f"L {x + 0.9 * s},{y + 0.55 * s} "
            f"L {x + 0.48 * s},{y - 0.15 * s} "
            f"L {x + 0.15 * s},{y - 0.15 * s} "
            f"L {x + 0.08 * s},{y - 0.62 * s} "
            f"L {x - 0.08 * s},{y - 0.62 * s} "
            f"L {x - 0.15 * s},{y - 0.15 * s} "
            f"L {x - 0.48 * s},{y - 0.15 * s} Z"),
        fillcolor="#f6f8f9",
        line=dict(color=col, width=1.5, dash=dash),
    )
    if label:
        _ann(fig, x, y + 0.05 * s, f"<b>{label}</b>", size=7.2,
             color=LABEL)


def junction(fig, x, y, s: float = 0.28):
    fig.add_shape(type="circle", x0=x - s, x1=x + s, y0=y - s, y1=y + s,
                  fillcolor=STATIC, line=dict(width=0))


def corridor_interface(fig, x, y, label, s: float = 1.0,
                       incoming: bool = True):
    fig.add_shape(type="rect", x0=x - 1.2 * s, x1=x + 1.2 * s,
                  y0=y - 0.6 * s, y1=y + 0.6 * s, fillcolor="#f6f8f9",
                  line=dict(color=STATIC, width=1.5))
    _ann(fig, x, y, ("⮞⮞ " if incoming else "") + f"<b>{label}</b>"
         + ("" if incoming else " ⮞⮞"), size=9, color=STATIC)


def zone_box(fig, x0, y0, x1, y1, label, style: str = "scope",
             tbc: bool = False):
    """Logical scope (SMS) or process-stage container. Scope labels sit
    ABOVE the box (outside); stage labels inside the box top."""
    col = STATIC_LIGHT if style == "scope" else TBC_GREY
    fig.add_shape(type="rect", x0=x0, x1=x1, y0=y0, y1=y1,
                  line=dict(color=col, width=1.1,
                            dash="dot" if style == "scope" else
                            ("dash" if tbc else None)),
                  fillcolor="rgba(0,0,0,0)", layer="below")
    if not label:
        # a grouping box may be deliberately unlabelled (e.g. the M16 hangar
        # bays, whose identity chip sits inside the bay) — draw no caption
        return
    ly = y1 + 0.55 if style == "scope" else y1 - 0.55
    _ann(fig, x0 + 0.3 if style == "scope" else (x0 + x1) / 2, ly,
         f"<b>{label}</b>",
         size=9 if style == "scope" else 8,
         color=WARN_AMBER if tbc else STATIC_LIGHT,
         xanchor="left" if style == "scope" else "center",
         bg="rgba(236,239,241,0.75)" if style != "scope" else None)


def process_line_card(fig, x0: float, y0: float, x1: float, y1: float,
                      line_id: str, label: str, status: str,
                      compact: bool = False,
                      residence_minutes: Optional[float] = None):
    """Draw one static process-line lifecycle card.

    Residence time is intentionally not repeated inside every line card.  The
    process-mimic renderer writes it once at washing-block level.
    """
    style = PROCESS_LINE_STATUS_STYLES[status]
    fig.add_shape(
        type="rect", name=f"process-line:{line_id}",
        x0=x0, y0=y0, x1=x1, y1=y1,
        fillcolor=style["fill"],
        line=dict(color=style["line"], width=1.5,
                  dash=style["dash"]),
    )
    text = f"<b>{label}</b> · {style['icon']} {style['label']}"
    _ann(fig, (x0 + x1) / 2, (y0 + y1) / 2, text,
         size=6.6 if compact else 7.4, color=style["line"])


def tag_chip(fig, x, y, tag, note="function under review"):
    """Confirmed-but-unassigned equipment tag — never given connectivity."""
    _ann(fig, x, y, f"{tag}", size=8, color=WARN_AMBER,
         bg="rgba(253,246,234,0.9)", bordercolor=WARN_AMBER, borderwidth=0.8,
         borderpad=2, hovertext=f"{tag} — {note}")


def stamp(fig, x, y, text="SCHEMATIC — NOT TO SCALE"):
    _ann(fig, x, y, text, size=9, color=WARN_AMBER,
         bg="rgba(253,246,234,0.92)", bordercolor=WARN_AMBER,
         borderwidth=1, borderpad=3)
