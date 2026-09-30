"""Solid-line SVG renderer for the EXISTING yard arrangement.

Why SVG and not Plotly
----------------------
The arrangement in ``ui.layout_geometry`` is good; what reads as thin is the
rendering. Plotly's ``shapes``/``annotations`` cannot do stroke hierarchy,
markers, hatch patterns, or label chips with real backgrounds and text
metrics — every element ends up the same hairline weight. Hand-authored SVG
does all of that, and the output is a plain string, so this renderer is
portable to any front end (Dash, NiceGUI, FastAPI + HTML, even a PDF plate)
if the app ever leaves Streamlit.

What is preserved exactly
-------------------------
Every position. This module CONSUMES ``SiteGeometry`` — ``PileGeo``,
``RailGeo``, ``ConveyorGeo.pts``, ``EquipGeo``, ``ZoneGeo``,
``ProcessLineGeo``, ``ChipGeo``, ``StampGeo`` — and applies one affine
transform. Nothing about the layout is recomputed. Grade colours, the
evidence classes, the repère convention and every disclaimer are unchanged.

What changes is weight
----------------------
* every filled body carries a darker outline, so shapes read as objects;
* belts are a thick dark stroke with a lighter core and chevrons along the
  path, instead of a hairline;
* machines are portal gantries — legs, beam, tapered boom, counterweight,
  status lamp — instead of a glyph;
* stockpiles are solid mounds with a ridge line and a capacity ceiling;
* future is a hatch pattern, not only a dashed outline;
* labels sit on solid chips, so text over the drawing is always legible;
* a drawing frame and title block, with the vertical exaggeration STATED.

Coordinates
-----------
``SiteGeometry.bounds`` is (x0, y0, x1, y1), metre-canonical and y-UP. SVG is
y-DOWN, so ``Y = (bounds[3] - y) * sy``. Getting that sign wrong flips the
whole sheet. A stockyard is long and shallow, so the sheet applies an explicit
vertical exaggeration (as a geological section does) and prints it in the
title block rather than pretending to be true scale.
"""
from __future__ import annotations

import html
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from . import tokens as t
from .bridge import format_repere, format_repere_range, grade_color

PX_PER_UNIT = 10.0          # viewBox px per geometry unit (horizontal)
# The yard is long and shallow, so the sheet already exaggerates the vertical.
# It is pushed a little further here to open a clear lane between each pile row
# and its rails, so a machine body sits on its rail instead of on the pile.
VERT_EXAG = 2.1

# --------------------------------------------------------- pile normalisation
# The source geometry draws piles at wildly different sizes for the same volume
# (in the composite CHAIN view a Benguerir product pile is 36 × 3.0 units while
# a Louta product pile of the SAME 191,512 t is 13 × 1.2). That is not the
# validated ARRANGEMENT — it is inconsistent draw scale. So every pile is
# re-drawn as a uniform lane CENTRED ON ITS GEOMETRY CENTRE: order, adjacency
# and machine docking are preserved (centres do not move), only the box size is
# equalised, and volume is carried by the fill bar + tonnage label. Rails and
# conveyors that were pinned to the old pile extents are remapped to follow, so
# nothing dangles (see ``NormGeo``).
NORM_W = 13.6               # uniform pile length (units) — < the 15u min gap
NORM_H = 1.4                # uniform pile depth (units)
RAIL_MARGIN = 1.6           # rail overhang beyond its flanked piles

# Display-only pile groupings: a set of piles that read as ONE titled bunker
# block. The members keep their fill/grade/tonnage but drop their redundant
# individual ids in favour of the block title. Never a model grouping —
# reachability, jobs and inventory stay per pile.
PILE_GROUPS = [
    (("BG-RAW-1", "BG-RAW-2"), "MINE ORE"),
]

# Benguerir's washed product is drawn with exactly the same treatment as
# Louta's — solid grade-piles flanked by reclaimer/stacker/reclaimer, sitting in
# the site zone. An earlier build wrapped the Benguerir pair in a special
# "WASHED-PRODUCT STORAGE" box that Louta has no counterpart for; that broke
# parity, so no per-site storage container is drawn. Both sites now read the
# same way straight from the geometry.
PAD = 26.0                  # drawing-frame padding, viewBox px
# Destination boxes and the right-hand evidence chips are centred on geometry
# points that sit at, or just past, the right edge of ``bounds``; without extra
# room on that side they clip (the production drawing clips them too).
PAD_R = 118.0
TITLE_H = 72.0              # title block height, viewBox px

FONT = "'IBM Plex Sans','Segoe UI',Roboto,Helvetica,Arial,sans-serif"
MONO = "'IBM Plex Mono',Consolas,'Courier New',monospace"


# ------------------------------------------------------------------ palette --
@dataclass(frozen=True)
class Sk:
    sheet: str
    panel: str
    body: str
    outline: str
    ink: str
    dim: str
    hair: str
    chip: str
    ok: str
    caution: str
    alarm: str
    info: str
    idle: str
    future: str


LIGHT = Sk(sheet="#eef1f3", panel="#e2e7ea", body="#cfd7dd", outline="#39454e",
           ink="#1f2a31", dim="#5d6c77", hair="#aab6bf", chip="#ffffff",
           ok=t.OK, caution=t.CAUTION, alarm=t.ALARM, info="#2d6ea8",
           idle="#8b98a3", future=t.FUTURE)
DARK = Sk(sheet="#131a20", panel="#1b242b", body="#2a353e", outline="#7d8f9c",
          ink="#dde7ee", dim="#8fa1ae", hair="#3a4750", chip="#1e272e",
          ok="#4fae5c", caution="#dda63a", alarm="#e0685c", info="#5f9fd8",
          idle="#78868f", future="#a48fe0")

STATE_COL = {"Stacking": "ok", "Reclaiming": "ok", "Travelling": "info",
             "Idle": "idle", "Maintenance": "caution",
             "Unavailable": "alarm", "Fault": "alarm", "Future": "future"}
STATE_GLYPH = {"Stacking": "▶", "Reclaiming": "◀", "Travelling": "↔",
               "Idle": "·", "Maintenance": "⚒", "Unavailable": "✕",
               "Fault": "✕", "Future": "◇"}


# row harmonisation (single-site views only — see NormGeo)
ROW_PITCH = 7.0             # uniform centre-to-centre pitch between pile rows
STAGE_THRESHOLD = 14.0      # a row gap wider than this is an inter-STAGE break
                            # (holds the washing/filtration equipment) and is
                            # PRESERVED — only within-stage pitch is uniformised
ROW_TOL = 0.6               # piles within this y band are one row
RAIL_CLEARANCE = 1.8        # a dedicated end-rail sits this far from its pile

# COMPOSITE-only block compaction. Single-site views harmonise every row to one
# pitch; the CHAIN composite keeps geography, so a block authored with loose
# spacing (Benguerir's washed product) reads wider than its tightly-authored
# Louta twin. Here that block's rows are re-spaced to span exactly as tall as a
# named reference block (Louta's product), anchored at the block's top row so
# nothing above moves; the conveyors follow via the usual _feats remap. The
# target is DERIVED from the reference each render, so it tracks geometry edits
# instead of drifting. Applied only when NOT harmonising (see NormGeo).
COMPACT_FALLBACK_PITCH = 1.4    # used only if the reference block is absent
COMPACT_BLOCKS = [
    # Benguerir washed product, matched to Louta's washed product
    {"piles": ("BG-P1", "BG-P2"),
     "rails": ("BG-RL-P1", "BG-SL", "BG-RL-P2"),
     "match": ("140LG-RL1", "LT-P1", "130LG-R", "LT-P2", "140LG-RL2")},
]


class _Feature:
    """A geometry element a conveyor point can be dragged along. ``remap``
    moves the point to the element's NEW x-span and NEW y-centre, keeping the
    point's residual offset, so a belt pinned to a pile edge or a rail end
    follows when that element is resized or its row is re-spaced."""
    __slots__ = ("ox0", "ox1", "nx0", "nx1", "yc", "nyc", "yscale", "is_pile")

    def __init__(self, ox0, ox1, nx0, nx1, yc, nyc, yscale, is_pile):
        self.ox0, self.ox1 = ox0, ox1
        self.nx0, self.nx1 = nx0, nx1
        self.yc, self.nyc, self.yscale = yc, nyc, yscale
        self.is_pile = is_pile

    def captures(self, x, y) -> bool:
        m = 2.2
        yt = 1.9 if self.is_pile else 1.6
        return (self.ox0 - m <= x <= self.ox1 + m
                and abs(y - self.yc) <= yt)

    def remap(self, x, y):
        span = self.ox1 - self.ox0
        frac = (x - self.ox0) / span if span else 0.0
        nx = self.nx0 + frac * (self.nx1 - self.nx0)
        ny = self.nyc + (y - self.yc) * self.yscale
        return nx, ny


class NormGeo:
    """Uniform-lane normalisation for a ``SiteGeometry``. Read-only over the
    source geometry.

    Always: every pile is drawn as a uniform lane centred on its geometry
    centre, and rails/conveyors follow.

    ``harmonize`` (single-site views only): pile ROWS are additionally re-spaced
    onto a uniform grid so every block has the same vertical rhythm — Louta's
    cramped rows open up, Benguerir's loose ones tighten, and the big empty
    stage gaps collapse to a constant. Rows are RIGID: each pile row keeps
    NORM_H, and its rails snap to a fixed clearance (between-rails to the row
    midpoint), so a machine can never be dragged onto its pile. The composite
    CHAIN view is never harmonised — its y carries geography.
    """

    def __init__(self, geo, harmonize: bool = False) -> None:
        self.harmonize = harmonize
        old_c = {p.pile_id: (p.y0 + p.y1) / 2 for p in geo.piles}
        old_h = {p.pile_id: (p.y1 - p.y0) for p in geo.piles}

        # ---- rows (top-to-bottom) and their harmonised centres -------------
        reps: List[float] = []
        for c in sorted(set(old_c.values()), reverse=True):
            if not reps or abs(reps[-1] - c) > ROW_TOL:
                reps.append(c)
        new_row: Dict[float, float] = {}
        if harmonize and len(reps) >= 2:
            new_row[reps[0]] = reps[0]                 # anchor the top row
            for prev, r in zip(reps, reps[1:]):
                gap = prev - r
                # uniform pitch WITHIN a stage; a wide inter-stage gap (which
                # carries the washing/filtration equipment) is kept as-is
                step = gap if gap > STAGE_THRESHOLD else ROW_PITCH
                new_row[r] = new_row[prev] - step
        else:
            new_row = {r: r for r in reps}
        self._knots = sorted((r, new_row[r]) for r in reps)     # (old, new) asc

        def row_of(c: float) -> float:
            return min(reps, key=lambda r: abs(r - c)) if reps else c

        # ---- composite block compaction (CHAIN only) -----------------------
        # Pull a loosely-authored block onto COMPACT_PITCH, anchored at its top
        # row, so it reads as tight as its single-site-harmonised twin. Rails
        # and piles share one old->new level map so machines stay on their rows.
        rail_y_raw = {r.rail_id: r.y for r in geo.rails}
        y_of = dict(old_c, **rail_y_raw)         # piles by centre, rails by y
        compact_c: Dict[str, float] = {}
        compact_rail_y: Dict[str, float] = {}
        if not harmonize:
            for blk in COMPACT_BLOCKS:
                pids = [p for p in blk["piles"] if p in old_c]
                rids = [r for r in blk["rails"] if r in rail_y_raw]
                if len(pids) < 2:
                    continue
                levels = sorted({y_of[m] for m in pids + rids}, reverse=True)
                # target pitch = reference block's span / this block's spans, so
                # the compacted block ends up exactly as tall as its twin
                ref = [y_of[m] for m in blk.get("match", ()) if m in y_of]
                pitch = ((max(ref) - min(ref)) / (len(levels) - 1)
                         if len(ref) >= 2 and len(levels) > 1
                         else COMPACT_FALLBACK_PITCH)
                top = levels[0]
                remap = {lv: top - i * pitch for i, lv in enumerate(levels)}
                compact_c.update({p: remap[old_c[p]] for p in pids})
                compact_rail_y.update({r: remap[rail_y_raw[r]] for r in rids})

        # harmonised: snap each pile to its row's new centre. Otherwise keep the
        # exact geometry centre (no clustering — CHAIN has rows <ROW_TOL apart),
        # except members of a compacted block.
        new_c = ({pid: new_row[row_of(c)] for pid, c in old_c.items()}
                 if harmonize
                 else {pid: compact_c.get(pid, c) for pid, c in old_c.items()})

        # ---- pile boxes (uniform lane at the harmonised centre) ------------
        self.box: Dict[str, Tuple[float, float, float, float]] = {}
        feats: List[_Feature] = []
        for pg in geo.piles:
            cx = (pg.x0 + pg.x1) / 2
            ny = new_c[pg.pile_id]
            self.box[pg.pile_id] = (cx - NORM_W / 2, ny - NORM_H / 2,
                                    cx + NORM_W / 2, ny + NORM_H / 2)
            yscale = (NORM_H / old_h[pg.pile_id]) if old_h[pg.pile_id] else 1.0
            feats.append(_Feature(pg.x0, pg.x1, cx - NORM_W / 2,
                                  cx + NORM_W / 2, old_c[pg.pile_id], ny,
                                  yscale, True))

        # ---- rails: x-span from flanked lanes, y snapped to clearance -------
        self.span: Dict[str, Tuple[float, float]] = {}
        self.rail_y: Dict[str, float] = {}
        for r in geo.rails:
            boxes = [self.box[p] for p in r.flanks if p in self.box]
            if boxes:
                x0 = min(b[0] for b in boxes) - RAIL_MARGIN
                x1 = max(b[2] for b in boxes) + RAIL_MARGIN
            else:
                x0, x1 = r.x0, r.x1
            self.span[r.rail_id] = (x0, x1)
            fp = [p for p in r.flanks if p in new_c]
            rows_hit = sorted({new_c[p] for p in fp})
            if r.rail_id in compact_rail_y:
                ny = compact_rail_y[r.rail_id]      # compacted block member
            elif not harmonize or not fp:
                ny = r.y if not harmonize else self.yr(r.y)
            elif len(rows_hit) == 1:
                # rail runs ALONG one row (e.g. two side-by-side hangars) —
                # offset to a fixed clearance on its original side
                sign = 1.0 if r.y > old_c[fp[0]] else -1.0
                ny = rows_hit[0] + sign * RAIL_CLEARANCE
            else:
                # rail sits BETWEEN rows — midpoint of the rows it serves
                ny = sum(rows_hit) / len(rows_hit)
            self.rail_y[r.rail_id] = ny
            feats.append(_Feature(r.x0, r.x1, x0, x1, r.y, ny, 1.0, False))
        self._feats = sorted(feats, key=lambda f: (f.is_pile,))

        # harmonised vertical extent, so the sheet crops to the compressed
        # content instead of leaving the old empty band at the bottom
        ys = [v for b in self.box.values() for v in (b[1], b[3])]
        ys += list(self.rail_y.values())
        if harmonize and ys:
            self.ymin, self.ymax = min(ys) - 3.5, max(ys) + 3.5
        else:
            self.ymin, self.ymax = geo.bounds[1], geo.bounds[3]

    def yr(self, y: float) -> float:
        """Piecewise-linear row remap for elements with no pile/rail anchor
        (zones, equipment, chips, stamps). Identity when not harmonising."""
        ks = self._knots
        if not self.harmonize or len(ks) < 2:
            return y
        if y <= ks[0][0]:
            (o0, n0), (o1, n1) = ks[0], ks[1]
        elif y >= ks[-1][0]:
            (o0, n0), (o1, n1) = ks[-2], ks[-1]
        else:
            o0, n0 = ks[0]
            for (a, na), (b, nb) in zip(ks, ks[1:]):
                if a <= y <= b:
                    o0, n0, o1, n1 = a, na, b, nb
                    break
            else:
                return y
        s = (n1 - n0) / (o1 - o0) if o1 != o0 else 1.0
        return n0 + (y - o0) * s

    def pile(self, pid: str, fallback) -> Tuple[float, float, float, float]:
        return self.box.get(pid, fallback)

    def rail(self, rid: str, fallback) -> Tuple[float, float]:
        return self.span.get(rid, fallback)

    def railY(self, rid: str, fallback_y: float) -> float:
        return self.rail_y.get(rid, self.yr(fallback_y))

    def point(self, x: float, y: float) -> Tuple[float, float]:
        best = None
        bestd = 1e9
        for f in self._feats:
            if f.captures(x, y):
                d = abs(y - f.yc)
                if d < bestd:
                    best, bestd = f, d
        return best.remap(x, y) if best is not None else (x, self.yr(y))


import re as _re
_TBC_RE = _re.compile(r"\s*[·—/–-]?\s*\bTBC\b\s*[·—/–-]?\s*")


def _strip_tbc(s):
    """Remove every TBC mention from a DRAWN label (signals/comments off, on
    request). Hovers keep the underlying data."""
    if not s or "TBC" not in str(s):
        return s
    out = _TBC_RE.sub(" ", str(s))
    return _re.sub(r"\s+", " ", out).strip(" ·—–-/") or ""


def e(v) -> str:
    return html.escape(str(v), quote=True)


def _n(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


# ------------------------------------------------------------------ canvas ---
class Sheet:
    """Accumulates SVG in painter order: panels → bodies → lines → labels."""

    LAYERS = ("panel", "body", "line", "symbol", "label", "top")

    def __init__(self, geo, sk: Sk, vert_exag: float = VERT_EXAG,
                 y_bounds: Optional[Tuple[float, float]] = None) -> None:
        self.sk = sk
        self.bx0, self.by0, self.bx1, self.by1 = geo.bounds
        if y_bounds is not None:               # harmonised vertical extent
            self.by0, self.by1 = y_bounds
        self.sx = PX_PER_UNIT
        self.sy = PX_PER_UNIT * vert_exag
        self.ve = vert_exag
        self.w = (self.bx1 - self.bx0) * self.sx
        self.h = (self.by1 - self.by0) * self.sy
        self.parts: Dict[str, List[str]] = {k: [] for k in self.LAYERS}

    # -- transform --------------------------------------------------------
    def X(self, x: float) -> float:
        return (x - self.bx0) * self.sx + PAD

    def Y(self, y: float) -> float:
        return (self.by1 - y) * self.sy + PAD

    def W(self, dx: float) -> float:
        return dx * self.sx

    def H(self, dy: float) -> float:
        return dy * self.sy

    # -- emit -------------------------------------------------------------
    def add(self, layer: str, markup: str) -> None:
        self.parts[layer].append(markup)

    def rect(self, layer, x, y, w, h, *, fill="none", stroke="none",
             sw=1.0, rx=0.0, dash="", op=1.0, cls="") -> None:
        d = f' stroke-dasharray="{dash}"' if dash else ""
        c = f' class="{cls}"' if cls else ""
        o = f' opacity="{_n(op)}"' if op < 1 else ""
        self.add(layer, f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" '
                        f'height="{_n(h)}" rx="{_n(rx)}" fill="{fill}" '
                        f'stroke="{stroke}" stroke-width="{_n(sw)}"'
                        f'{d}{o}{c}/>')

    def path(self, layer, d, *, fill="none", stroke="none", sw=1.0, dash="",
             cap="butt", join="miter", op=1.0) -> None:
        da = f' stroke-dasharray="{dash}"' if dash else ""
        o = f' opacity="{_n(op)}"' if op < 1 else ""
        self.add(layer, f'<path d="{d}" fill="{fill}" stroke="{stroke}" '
                        f'stroke-width="{_n(sw)}" stroke-linecap="{cap}" '
                        f'stroke-linejoin="{join}"{da}{o}/>')

    def line(self, layer, x1, y1, x2, y2, *, stroke=None, sw=1.0, dash="",
             cap="butt", op=1.0) -> None:
        self.path(layer, f"M{_n(x1)},{_n(y1)} L{_n(x2)},{_n(y2)}",
                  stroke=stroke or self.sk.outline, sw=sw, dash=dash, cap=cap,
                  op=op)

    def poly(self, layer, pts, *, fill="none", stroke="none", sw=1.0,
             dash="", op=1.0) -> None:
        d = "M" + " L".join(f"{_n(x)},{_n(y)}" for x, y in pts) + " Z"
        self.path(layer, d, fill=fill, stroke=stroke, sw=sw, dash=dash, op=op)

    def text(self, layer, x, y, s, *, size=11, fill=None, anchor="middle",
             weight=400, mono=False, baseline="middle", op=1.0,
             ls=0.0, lod="1", halo="") -> None:
        """``lod`` is the zoom tier at which this label appears (1 = always).
        Text is what overcrowds a schematic at fit-zoom, so detail tiers are
        hidden until the viewer is zoomed in far enough to have room.

        ``halo`` draws a thin contrasting outline behind the glyphs (via
        ``paint-order``) so a label sitting directly on the coloured stock
        stays legible where it runs from the fill onto the empty pad."""
        s = _strip_tbc(s)
        if s in (None, ""):
            return
        o = f' opacity="{_n(op)}"' if op < 1 else ""
        l = f' letter-spacing="{_n(ls)}"' if ls else ""
        h = (f' paint-order="stroke" stroke="{halo}" stroke-width="2.4" '
             f'stroke-linejoin="round"' if halo else "")
        self.add(layer,
                 f'<text class="lod{lod}" x="{_n(x)}" y="{_n(y)}" '
                 f'font-size="{_n(size)}" '
                 f'font-family="{MONO if mono else FONT}" '
                 f'font-weight="{weight}" fill="{fill or self.sk.ink}" '
                 f'text-anchor="{anchor}" dominant-baseline="{baseline}"'
                 f'{h}{l}{o}>{e(s)}</text>')

    def chip(self, layer, x, y, s, *, size=10.5, fill=None, stroke=None,
             ink=None, anchor="middle", mono=False, pad=5.0, weight=500,
             dash="", lod="1") -> None:
        """Text on a solid plate — labels over a drawing must stay legible."""
        s = _strip_tbc(s)
        if not s:
            return
        self.add(layer, f'<g class="lod{lod}">')
        wch = 0.56 if mono else 0.52
        w = len(str(s)) * size * wch + pad * 2
        h = size + pad * 1.25
        x0 = {"middle": x - w / 2, "start": x, "end": x - w}[anchor]
        self.rect(layer, x0, y - h / 2, w, h, fill=fill or self.sk.chip,
                  stroke=stroke or self.sk.hair, sw=0.9, rx=1.5, dash=dash)
        self.text(layer, x0 + w / 2, y + 0.4, s, size=size,
                  fill=ink or self.sk.ink, mono=mono, weight=weight, lod=lod)
        self.add(layer, "</g>")
        return w

    def title(self, layer, x, y, s, *, size=10, fill=None, anchor="start",
              ls=0.9, lod="1") -> None:
        self.text(layer, x, y, s, size=size, fill=fill or self.sk.dim,
                  anchor=anchor, weight=700, ls=ls, lod=lod)

    def tip(self, layer, x, y, w, h, text: str) -> None:
        """Invisible hover target carrying a native SVG tooltip."""
        self.add(layer, f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" '
                        f'height="{_n(h)}" fill="transparent" '
                        f'pointer-events="all"><title>{e(text)}</title>'
                        f'</rect>')

    # -- document ---------------------------------------------------------
    def render(self, px_width: Optional[float] = None
               ) -> Tuple[str, float, float]:
        vw = self.w + PAD + PAD_R
        vh = self.h + PAD * 2 + TITLE_H
        body = "".join("".join(self.parts[k]) for k in self.LAYERS)
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {_n(vw)} {_n(vh)}" '
            f'width="{_n(px_width) if px_width else "100%"}" '
            f'preserveAspectRatio="xMidYMin meet" '
            f'style="display:block;background:{self.sk.sheet}">'
            f"{_defs(self.sk)}{body}</svg>")
        return svg, vw, vh


def _defs(sk: Sk) -> str:
    return (
        "<defs>"
        f'<pattern id="hTbc" width="7" height="7" patternUnits="userSpaceOnUse"'
        f' patternTransform="rotate(45)">'
        f'<rect width="7" height="7" fill="{sk.panel}"/>'
        f'<line x1="0" y1="0" x2="0" y2="7" stroke="{sk.caution}" '
        f'stroke-width="1.6" opacity="0.55"/></pattern>'
        f'<pattern id="hTrip" width="6" height="6" '
        f'patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        f'<rect width="6" height="6" fill="{sk.panel}"/>'
        f'<line x1="0" y1="0" x2="0" y2="6" stroke="{sk.alarm}" '
        f'stroke-width="2"/></pattern>'
        f'<pattern id="hFut" width="8" height="8" '
        f'patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
        f'<rect width="8" height="8" fill="{sk.panel}"/>'
        f'<line x1="0" y1="0" x2="0" y2="8" stroke="{sk.future}" '
        f'stroke-width="1.4" opacity="0.5"/></pattern>'
        "</defs>")


# ---------------------------------------------------------------- elements ---
def _draw_zones(sh: Sheet, geo, opts, norm) -> None:
    sk = sh.sk
    piles = getattr(geo, "piles", [])
    for z in geo.zones:
        # A harmonised zone HUGS the piles it contains (recomputed from their
        # re-spaced boxes, like the Mine Ore group) so it can't stay tall over
        # tightened rows. A pile-free stage box falls back to the row remap.
        members = [p for p in piles
                   if z.x0 - 1 <= (p.x0 + p.x1) / 2 <= z.x1 + 1
                   and z.y0 - 1 <= (p.y0 + p.y1) / 2 <= z.y1 + 1
                   and p.pile_id in norm.box]
        if norm.harmonize and members:
            bx = [norm.box[p.pile_id] for p in members]
            # tight to the piles: just enough for the outside space-name above
            # and the repère ruler below; extra headroom only when this zone
            # actually draws a title (a scope container)
            draws_title = z.style == "scope" and bool(z.label)
            zy0 = min(b[1] for b in bx) - 0.9
            zy1 = max(b[3] for b in bx) + (1.9 if draws_title else 1.0)
        else:
            zy1, zy0 = norm.yr(z.y1), norm.yr(z.y0)
            if zy1 - zy0 < 3.0:
                mid = (zy1 + zy0) / 2
                zy1, zy0 = mid + 1.5, mid - 1.5
        x, y = sh.X(z.x0), sh.Y(zy1)
        w, h = sh.W(z.x1 - z.x0), sh.H(zy1 - zy0)
        sh.rect("panel", x, y, w, h,
                fill=sk.panel,
                stroke=sk.hair, sw=1.2, rx=2)
        if z.label:
            # Only the SITE container title (style="scope") is drawn — trimmed
            # of its parenthetical, which lives on hover. The process-stage
            # titles overprinted the packed sub-yards and the machines beneath
            # them are already labelled, so they become hover-only ("remove the
            # unnecessary ones"). The zone box + tooltip still names the stage.
            if z.style == "scope":
                label = z.label.upper()
                short = label.split("(")[0].split("—")[0].strip() or label
                sh.title("label", x + 8, y + 11, short, size=9.5, lod="1")
            sh.tip("top", x, y, w, 14, z.label)


def _pile_bands(pg, vm) -> List[Tuple[float, float, str, str, float]]:
    if vm is not None and vm.segments:
        return [(s.frac_from, s.frac_to, s.grade, s.grade or "—", s.mass_t)
                for s in vm.segments]
    if pg.display_grade_components:
        return [(c.frac_from, c.frac_to, c.grade_id,
                 c.grade_label or c.grade_id,
                 (pg.display_mass_t or 0.0) * c.mass_fraction)
                for c in pg.display_grade_components]
    gid = (vm.grade if vm is not None else "") or pg.display_grade_id
    mass = vm.mass_t if vm is not None else (pg.display_mass_t or 0.0)
    return [(0.0, 1.0, gid, gid or "—", mass)]


def _toe(x0: float, x1: float, ytop: float, ybot: float,
         chamfer: float) -> List:
    """Plan-view stockpile outline: a footprint with chamfered toes."""
    c = min(chamfer, (x1 - x0) / 2.0)
    return [(x0 + c, ytop), (x1 - c, ytop), (x1, (ytop + ybot) / 2),
            (x1 - c, ybot), (x0 + c, ybot), (x0, (ytop + ybot) / 2)]


def _draw_pile(sh: Sheet, state, pg, vm, opts, norm,
               suppress_id: bool = False) -> None:
    """PLAN view. The yard is drawn from above, so a pile's x-extent is its
    length along the repère axis and material fills ALONG it — not upward.
    Encoding fill as height (an elevation idiom) on a plan-view layout is what
    makes a stockyard mimic hard to read.

    The box comes from ``norm``: a uniform lane centred on the pile's geometry
    centre, so equal-volume piles read equal-size regardless of the source
    draw scale."""
    sk = sh.sk
    bx0, by0, bx1, by1 = norm.pile(pg.pile_id, (pg.x0, pg.y0, pg.x1, pg.y1))
    x0, x1 = sh.X(bx0), sh.X(bx1)
    ytop, ybase = sh.Y(by1), sh.Y(by0)
    band_h = ybase - ytop
    ytop_m, ybot_m = ytop + 2.6, ybase - 2.6             # material band

    mass = vm.mass_t if vm is not None else (pg.display_mass_t or 0.0)
    cap = (vm.capacity_t if vm is not None else pg.capacity_t) or 0.0
    fill = min(1.0, mass / cap) if cap else 0.0

    dimmed = bool(opts.grade_filter) and (
        (vm.grade if vm else "") not in opts.grade_filter)
    alarm = vm is not None and (vm.below_safety or bool(vm.offspec_hard))
    if opts.alarms_only and not alarm:
        dimmed = True
    focus = opts.selected_asset == pg.pile_id
    ghost = pg.future or not pg.modelled
    op = 0.5 if dimmed else 1.0

    # ---- bunker / pad footprint ----------------------------------------
    sh.rect("body", x0, ytop, x1 - x0, band_h,
            fill="url(#hFut)" if pg.future else sk.body,
            stroke=sk.outline, sw=1.8, rx=1.5,
            dash="6 3" if ghost else "", op=op)

    # ---- material, banded by grade, filling ALONG the pad -----------------
    # Same semantics as the production lane (``hmi_symbols.stock_lane``):
    # segment fractions are scaled by the fill, so the material occupies the
    # first `fill` of the footprint and the bands sit inside it.
    span = (x1 - x0) - 4.0
    bands = _pile_bands(pg, vm)
    # grade label sits on EACH grade band (not one label for the whole pile),
    # so a multi-grade stock reads its composition; tonnage/quality are on hover
    if ghost or dimmed:
        gcol, ghalo = sk.ink, "#ffffff"
    else:
        gcol, ghalo = "#ffffff", "#12181d"
    for f0, f1, gid, glabel, smass in bands:
        bx0 = x0 + 2.0 + span * f0 * fill
        bx1 = x0 + 2.0 + span * f1 * fill
        if bx1 - bx0 < 1.0:
            continue
        col = sk.hair if dimmed else grade_color(gid)
        sh.poly("body", _toe(bx0, bx1, ytop_m, ybot_m,
                             min(6.0, (bx1 - bx0) * 0.12)),
                fill=col, stroke=sk.outline, sw=1.3, op=op)
        if opts.show_tags and glabel and (bx1 - bx0) > 20:
            sh.text("label", (bx0 + bx1) / 2, (ytop_m + ybot_m) / 2,
                    glabel[:8], size=7.5, fill=gcol, anchor="middle",
                    weight=700, mono=True, lod=2, halo=ghalo)
    # free capacity reads as the empty remainder of the pad
    fx = x0 + 2.0 + span * fill
    if x1 - 2.0 - fx > 4:
        sh.line("line", fx + 2, (ytop_m + ybot_m) / 2, x1 - 3,
                (ytop_m + ybot_m) / 2, stroke=sk.hair, sw=1.3, dash="3 4")
    # safety-stock tick, same convention as the production lane
    if vm is not None and vm.safety_stock_t > 0 and cap:
        sx = x0 + 2.0 + span * min(1.0, vm.safety_stock_t / cap)
        sh.line("line", sx, ytop_m - 2, sx, ybot_m + 2, stroke=sk.alarm,
                sw=1.6, dash="2 2")

    # ---- repère ruler, drawn OUTSIDE the pad so it never eats material ---
    ruler_y = ybase + 5.0
    sh.line("line", x0 + 2, ruler_y, x1 - 2, ruler_y, stroke=sk.dim, sw=1.1)
    for r in (0, 250, 500, 750, 1000):
        rx = x0 + 2.0 + span * (r / 1000.0)
        major = r in (0, 500, 1000)
        sh.line("line", rx, ruler_y - (4.5 if major else 2.8), rx, ruler_y,
                stroke=sk.dim, sw=1.1)
        if opts.show_tags:
            sh.text("label", rx, ruler_y + 6.5, f"R{r}", size=8, fill=sk.dim,
                    mono=True, weight=600, lod=2 if major else 3)

    # ---- pile label: PLAIN TEXT on the coloured stock, no white chip -----
    # Review note: the coloured rectangle is the label's own backing, so grade
    # + tonnage sit directly on it. White over the material, ink over an empty
    # pad. Grouped piles (e.g. the Mine Ore block) drop their redundant id and
    # show only grade + tonnage.
    # space NAME sits OUTSIDE the pad (small, above-left); a grouped pile
    # (e.g. the Mine Ore block) drops its redundant id. The grade labels are
    # drawn per-band above; tonnage/quality are on hover.
    if opts.show_tags and not suppress_id:
        sh.text("label", x0 + 1, ytop - 3.2, pg.pile_id, size=7.5,
                fill=sk.dim, anchor="start", weight=650, mono=True, lod=2)

    if focus:
        sh.rect("top", x0 - 3, ytop - 3, x1 - x0 + 6, band_h + 6, fill="none",
                stroke=sk.info, sw=2.4, rx=2)

    # ---- evidence badges -------------------------------------------------
    if opts.show_evidence:
        bx = x1 - 8
        for glyph, tone, why in _pile_badges(pg, vm):
            sh.text("label", bx, ytop + 9, glyph, size=11, fill=tone,
                    weight=700, lod=2)
            sh.tip("top", bx - 7, ytop + 2, 14, 14, f"{pg.pile_id} — {why}")
            bx -= 13

    # ---- concurrency work zones -----------------------------------------
    cvm = state.concurrency.get(pg.pile_id)
    if cvm is not None and opts.show_flows:
        tone = {"violation": sk.alarm, "warning": sk.caution,
                "ok": sk.ok}[cvm.status]
        for jl, mark in ((cvm.stack_jobs, "STK"), (cvm.out_jobs, "RCL")):
            for j in jl:
                a = x0 + (x1 - x0) * (j.repere_from / 1000.0)
                b = x0 + (x1 - x0) * (j.repere_to / 1000.0)
                sh.rect("top", a, ytop - 4.5, max(b - a, 3), 3.4, fill=tone,
                        stroke="none")
                sh.text("top", (a + b) / 2, ytop - 9, mark, size=8, fill=tone,
                        weight=700)
        sh.tip("top", x0, ytop - 12, x1 - x0, 12,
               f"{pg.pile_id} CONCURRENT — separation {cvm.sep_m:,.0f} m "
               f"(min {cvm.req_m:,.0f} m ASSUMED) — {cvm.status.upper()}")

    # ---- hover -----------------------------------------------------------
    if vm is not None:
        q = " · ".join(f"{k} {v:g}" for k, v in vm.quality.items()) or "—"
        sh.tip("top", x0, ytop, x1 - x0, band_h,
               f"{pg.pile_id} · {vm.site}\n{vm.state}\n"
               f"{mass:,.0f} / {cap:,.0f} t  ({fill * 100:.0f} %)\n"
               f"grade {vm.grade_label}\nquality {q}\n"
               f"in {vm.inbound_tph:,.0f} · out {vm.outbound_tph:,.0f} t/h\n"
               f"evidence {vm.evidence_status}")
    else:
        sh.tip("top", x0, ytop, x1 - x0, band_h,
               f"{pg.pile_id}\nDISPLAY / REFERENCE geometry — not a solver "
               f"space\n{pg.display_note or ''}")


def _pile_badges(pg, vm) -> List[Tuple[str, str, str]]:
    out = []
    if not pg.modelled:
        out.append(("▢", t.FUTURE, "display-only — not a solver space"))
    if vm is not None and vm.evidence_status == "ASSUMED":
        out.append(("◐", t.CAUTION, "ASSUMED"))
    if vm is not None and vm.below_safety:
        out.append(("!", t.CAUTION, "below safety stock"))
    if vm is not None and vm.offspec_hard:
        out.append(("✕", t.ALARM, "hard quality limit breached"))
    return out[:4]


def _draw_rail(sh: Sheet, rl, opts, norm) -> None:
    """A track, not a line: two rails, sleepers, end stops."""
    sk = sh.sk
    y = sh.Y(norm.railY(rl.rail_id, rl.y))
    rx0, rx1 = norm.rail(rl.rail_id, (rl.x0, rl.x1))
    x0, x1 = sh.X(rx0), sh.X(rx1)
    dash = ""
    sh.line("line", x0, y - 2.6, x1, y - 2.6, stroke=sk.outline, sw=1.6,
            dash=dash)
    sh.line("line", x0, y + 2.6, x1, y + 2.6, stroke=sk.outline, sw=1.6,
            dash=dash)
    step = max(44.0, (x1 - x0) / 22.0)
    n = int((x1 - x0) / step)
    for i in range(n + 1):
        sx = x0 + i * step
        sh.line("line", sx, y - 2.6, sx, y + 2.6, stroke=sk.hair, sw=1.0)
    for ex in (x0, x1):
        sh.line("line", ex, y - 6.5, ex, y + 6.5, stroke=sk.outline, sw=2.2)
    if opts.show_tags:
        # outside the travel limits, so a machine can never sit on it
        sh.chip("label", x0 - 7, y, rl.rail_id, size=9, anchor="end",
                mono=True, weight=600, ink=sk.dim, pad=4.0, lod=3)


def _draw_machine(sh: Sheet, state, geo, mg, vm, rl, opts, norm) -> None:
    """PLAN view of a rail-mounted machine: a travelling body on the rail with
    a boom reaching across into the pile it is serving."""
    import types as _types
    sk = sh.sk
    mid = mg.machine_id
    display = mg.display and vm is None
    status = ("Future" if mg.future else
              "Unavailable" if mid in state.down_machines else
              (vm.status if vm is not None else "Idle"))
    pv0 = state.machine_pos.get(mid)
    if status == "Idle" and pv0 is not None and pv0.travelling:
        status = "Travelling"          # same derivation as the production mimic
    col = getattr(sk, STATE_COL.get(status, "idle"))
    pv = state.machine_pos.get(mid)
    rep = (pv.repere if pv is not None and pv.repere is not None
           else mg.home_repere)

    # Placement is the PRODUCTION placement: the machine sits over the pile
    # it is serving, at its repère within THAT pile, and parks on its own line
    # when it has no job. Re-deriving this from the rail span would move every
    # machine — the arrangement is what the project already validated.
    from ui.process_mimic import machine_draw_pos

    focus = opts.selected_asset == mid
    running = vm is not None and bool(vm.job_id)
    if opts.active_flows_only and not running:
        return
    if opts.alarms_only and status not in ("Unavailable", "Maintenance",
                                           "Fault"):
        return

    served_pg = None
    if vm is not None and vm.target_space:
        served_pg = geo.pile(vm.target_space)
        if served_pg is not None and served_pg.pile_id not in rl.flanks:
            served_pg = None          # never draw a cross-line relocation
    if served_pg is None and mg.served():
        home = geo.pile(mg.served()[0])
        served_pg = home if (home is not None
                             and home.pile_id in rl.flanks) else None

    # Feed ``machine_draw_pos`` the NORMALISED rail span and served-pile box, so
    # the machine still lands on its rail at its repère within the pile — the
    # production placement logic is unchanged, only the geometry it reads is the
    # equalised one, so machine and pile stay locked together after the resize.
    nrx0, nrx1 = norm.rail(rl.rail_id, (rl.x0, rl.x1))
    rail_shim = _types.SimpleNamespace(x0=nrx0, x1=nrx1, travel=rl.travel,
                                       y=norm.railY(rl.rail_id, rl.y))
    pile_shim = served_pg
    if served_pg is not None:
        b = norm.pile(served_pg.pile_id,
                      (served_pg.x0, served_pg.y0, served_pg.x1, served_pg.y1))
        pile_shim = _types.SimpleNamespace(x0=b[0], y0=b[1], x1=b[2], y1=b[3])

    gx, gy, tipx, tipy, side = machine_draw_pos(
        rail_shim, pile_shim, pv.x_norm if pv is not None else None,
        mg.parking_frac, mg.scale)
    cx, ry = sh.X(gx), sh.Y(gy)
    y_tip = sh.Y(tipy)
    sgn = 1.0 if y_tip > ry else -1.0

    dash = "5 3" if display else ""
    body = sk.panel if display else sk.body
    line_col = sk.info if focus else sk.outline
    lw = 2.6 if focus else 1.8

    bw, bh = machine_glyph(sh, cx, ry, y_tip, rl.kind, col=col, body=body,
                           line_col=line_col, lw=lw, dash=dash)
    sh.text("symbol", cx, ry + 0.3, STATE_GLYPH.get(status, "·"), size=7.5,
            fill="#ffffff", weight=700)

    if opts.show_tags:
        # Machine NAME only, tiny, on the side AWAY from the served pile (the
        # boom points at the pile, the name goes opposite) so it never sits on
        # the stockpile. Position (repère) and rate are on hover.
        name_y = ry - sgn * (bh / 2 + 6)
        sh.text("label", cx, name_y, mid, size=6.5, mono=True, weight=650,
                fill=sk.dim if display else col, anchor="middle", lod=2,
                halo=sk.chip)

    # work zone: the span of the pile this machine is working this bucket
    if opts.show_flows and pv is not None and pv.work_repere is not None:
        a = sh.X(rl.x0 + (rl.x1 - rl.x0) * pv.work_repere[0] / 1000.0)
        b = sh.X(rl.x0 + (rl.x1 - rl.x0) * pv.work_repere[1] / 1000.0)
        sh.rect("line", min(a, b), ry + sgn * (bh / 2 + 2),
                abs(b - a) or 3, 4.0, fill=col, stroke="none")

    reach = (", ".join(vm.reachable) if vm is not None
             else ", ".join(mg.served()) or "—")
    sh.tip("top", cx - 26, min(ry, y_tip) - 18, 52, abs(y_tip - ry) + 44,
           f"{mid} · line {mg.rail_id}\n"
           + ("DISPLAY / REFERENCE — not in the planning model\n"
              if display else "")
           + f"state {status}\nposition {format_repere(rep)}\n"
           + (f"work range {format_repere_range(*pv.work_repere)}\n"
              if pv is not None and pv.work_repere is not None else "")
           + (f"rate {vm.rate_tph:,.0f} / {vm.rate_max_tph:,.0f} t/h\n"
              if vm is not None else "")
           + f"reach (configured) {reach}")


def machine_glyph(sh: Sheet, cx: float, ry: float, y_tip: float,
                  kind: str, *, col: str, body: str, line_col: str,
                  lw: float = 1.8, dash: str = "") -> None:
    """Draw a stacker or a reclaimer. The two are deliberately DIFFERENT in
    silhouette, not just in the head, so kind is readable without a legend:

      STACKER    square frame · single boom · discharge chute at the tip ·
                 feed arrow INTO the frame from the rail (material arrives by
                 belt and is thrown onto the pile)
      RECLAIMER  frame with a slew ring · bucket wheel at the tip ·
                 COUNTERWEIGHT BOOM opposite the digging boom ·
                 take-off arrow OUT of the frame to the rail

    Colour still means state only.
    """
    # ``y_tip`` gives only the DIRECTION to the served pile — the boom is a
    # compact stub so the machine sits on its own rail lane instead of reaching
    # across and hiding the stockpile it serves (full reach is on hover).
    sgn = 1.0 if y_tip > ry else -1.0
    reclaimer = kind == "reclaimer"
    bw, bh = (34.0, 16.0) if reclaimer else (31.0, 15.0)
    stub = bh / 2 + 8.0                      # short directional boom

    # ---- travelling frame on the rail ---------------------------------
    if reclaimer:
        sh.rect("symbol", cx - bw / 2, ry - bh / 2, bw, bh, fill=body,
                stroke=line_col, sw=lw, rx=7.0, dash=dash)
        sh.add("symbol", f'<circle cx="{_n(cx)}" cy="{_n(ry)}" '
                         f'r="{_n(bh * 0.4)}" fill="none" '
                         f'stroke="{line_col}" stroke-width="{_n(lw * 0.8)}"/>')
    else:
        sh.rect("symbol", cx - bw / 2, ry - bh / 2, bw, bh, fill=body,
                stroke=line_col, sw=lw, rx=1.5, dash=dash)
        sh.line("symbol", cx - bw / 2, ry - bh / 2, cx + bw / 2, ry + bh / 2,
                stroke=line_col, sw=lw * 0.55)
        sh.line("symbol", cx - bw / 2, ry + bh / 2, cx + bw / 2, ry - bh / 2,
                stroke=line_col, sw=lw * 0.55)

    # ---- bogies on both rails -----------------------------------------
    for dx in (-bw / 2 + 6, bw / 2 - 6):
        for sy in (-1, 1):
            sh.rect("symbol", cx + dx - 4, ry + sy * bh / 2 - 3, 8, 6,
                    fill=line_col, stroke="none", rx=1)

    # ---- short directional boom + head --------------------------------
    y_root = ry + sgn * bh / 2
    y_tipc = ry + sgn * stub
    sh.poly("symbol", [(cx - 5, y_root), (cx + 5, y_root),
                       (cx + 2.6, y_tipc), (cx - 2.6, y_tipc)],
            fill=body, stroke=line_col, sw=lw, dash=dash)
    if reclaimer:
        sh.add("symbol", f'<circle cx="{_n(cx)}" cy="{_n(y_tipc)}" r="5.5" '
                         f'fill="{body}" stroke="{line_col}" '
                         f'stroke-width="{_n(lw)}"/>')
        for k in range(6):
            a = k * math.pi / 3
            sh.line("symbol", cx, y_tipc, cx + 5.5 * math.cos(a),
                    y_tipc + 5.5 * math.sin(a), stroke=line_col, sw=0.9)
    else:
        sh.poly("symbol", [(cx - 5, y_tipc - sgn * 3), (cx + 5, y_tipc - sgn * 3),
                           (cx, y_tipc + sgn * 5)], fill=body,
                stroke=line_col, sw=lw)

    # ---- status lamp ---------------------------------------------------
    sh.add("symbol", f'<circle cx="{_n(cx)}" cy="{_n(ry)}" r="5.6" '
                     f'fill="{col}" stroke="{sh.sk.chip}" stroke-width="1.4"/>')
    return bw, bh


def _chevrons(sh: Sheet, pts: Sequence[Tuple[float, float]], col: str,
              spacing: float = 34.0, size: float = 4.6) -> None:
    """Direction marks stepped along the belt path."""
    segs = []
    total = 0.0
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        d = math.hypot(bx - ax, by - ay)
        if d > 0.01:
            segs.append(((ax, ay), (bx, by), d))
            total += d
    if total < spacing:
        spacing = max(total / 2.0, 12.0)
    walked = spacing * 0.6
    for (ax, ay), (bx, by), d in segs:
        ux, uy = (bx - ax) / d, (by - ay) / d
        px, py = -uy, ux
        s = walked
        while s < d:
            mx, my = ax + ux * s, ay + uy * s
            sh.poly("line",
                    [(mx + ux * size, my + uy * size),
                     (mx - ux * size * 0.5 + px * size * 0.85,
                      my - uy * size * 0.5 + py * size * 0.85),
                     (mx - ux * size * 0.5 - px * size * 0.85,
                      my - uy * size * 0.5 - py * size * 0.85)],
                    fill=col, stroke="none")
            s += spacing
        walked = max(0.0, spacing - (d - (s - spacing)))


def _draw_conveyor(sh: Sheet, cg, cstate: str, tph, cap, opts, norm) -> None:
    """Belt: thick dark casing, lighter core, chevrons for direction.

    Each point is dragged through ``norm`` so a belt that was pinned to a pile
    edge or a rail end follows that element when it is resized — otherwise the
    Benguerir reclaim belts (pinned to the old wide rail ends) would dangle."""
    sk = sh.sk
    pts = [(sh.X(nx), sh.Y(ny))
           for nx, ny in (norm.point(x, y) for x, y in cg.pts)]
    if len(pts) < 2:
        return
    d = "M" + " L".join(f"{_n(x)},{_n(y)}" for x, y in pts)
    future = cg.future
    core = {"active": sk.ok, "tripped": sk.alarm, "future": sk.future,
            "idle": sk.idle}.get(cstate, sk.idle)
    dim = opts.active_flows_only and cstate != "active"
    op = 0.35 if dim else 1.0
    dash = "7 4" if (future or not cg.planning_enabled) else ""

    sh.path("line", d, stroke=sk.outline, sw=6.2, cap="round", join="round",
            op=op)
    sh.path("line", d, stroke=core, sw=3.2, cap="round", join="round",
            dash=dash, op=op)
    if cstate == "active" and opts.show_flows and not dim:
        _chevrons(sh, pts, "#ffffff")

    if opts.show_tags and cg.tag:
        i = max(0, min(len(pts) - 2, int(len(pts) * cg.label_frac) - 1))
        mx = (pts[i][0] + pts[i + 1][0]) / 2
        my = (pts[i][1] + pts[i + 1][1]) / 2 - sh.H(cg.label_dy) * 1.35
        label = cg.tag
        if tph is not None and cap:
            label = f"{cg.tag} · {tph:,.0f}/{cap:,.0f} t/h"
        sh.chip("label", mx, my, label, size=9.5, mono=True,
                stroke=core if cstate == "active" else sk.hair, lod=3)

    sh.tip("top", min(p[0] for p in pts) - 4, min(p[1] for p in pts) - 4,
           abs(max(p[0] for p in pts) - min(p[0] for p in pts)) + 8,
           abs(max(p[1] for p in pts) - min(p[1] for p in pts)) + 8,
           f"{cg.tag or cg.cid}\nstate {cstate}\n"
           + (f"{tph:,.0f} / {cap:,.0f} t/h\n" if tph is not None and cap
              else "")
           + ("links " + ", ".join(cg.link_ids) if cg.link_ids
              else "display-only conveyor"))


def _draw_equipment(sh: Sheet, state, eq, opts, norm) -> None:
    sk = sh.sk
    x, y = sh.X(eq.x), sh.Y(norm.yr(eq.y))
    s = 1.0 * eq.scale
    stroke = sk.outline
    dash = ""
    fill = sk.body
    k = eq.kind

    if k == "tower":
        w, h = 22 * s, 30 * s
        sh.rect("symbol", x - w / 2, y - h / 2, w, h, fill=fill,
                stroke=stroke, sw=1.8, dash=dash)
        sh.poly("symbol", [(x - w / 2 - 3, y - h / 2),
                           (x + w / 2 + 3, y - h / 2), (x, y - h / 2 - 10)],
                fill=sk.body, stroke=stroke, sw=1.8)
        for i in range(1, 3):
            sh.line("symbol", x - w / 2, y - h / 2 + i * h / 3,
                    x + w / 2, y - h / 2 + i * h / 3, stroke=sk.hair, sw=0.9)
    elif k == "destbox":
        w = sh.W(eq.w) or max(96.0, len(eq.label) * 7.2 + 26)
        h = sh.H(eq.h) or 46
        sh.rect("body", x - w / 2, y - h / 2, w, h, fill=fill, stroke=stroke,
                sw=2.0, dash=dash, rx=2)
        sh.rect("body", x - w / 2, y - h / 2, w, 12, fill=stroke,
                stroke="none")
        # on "top": an evidence chip can sit on the same geometry point and
        # a destination header must not lose to it
        sh.text("top", x, y - h / 2 + 6.5, "DESTINATION", size=8,
                fill=sk.chip, weight=700, ls=0.8)
        sh.text("top", x, y + 3, eq.label, size=11, weight=650)
    elif k == "gate":
        w, h = 11 * s, 13 * s
        sh.poly("symbol", [(x - w / 2, y - h / 2), (x + w / 2, y - h / 2),
                           (x + w / 4, y + h / 2), (x - w / 4, y + h / 2)],
                fill=fill, stroke=stroke, sw=1.5, dash=dash)
        sh.line("symbol", x, y + h / 2, x, y + h / 2 + 6, stroke=stroke,
                sw=1.5)
        if opts.show_tags and eq.label:
            sh.text("label", x, y - h / 2 - 7, eq.label, size=8.5,
                    fill=sk.dim, mono=True, weight=600, lod=3)
    elif k == "diverter":
        r = 9 * s
        sh.poly("symbol", [(x - r, y - r * 0.7), (x + r, y - r * 0.7),
                           (x, y + r * 0.9)], fill=fill, stroke=stroke,
                sw=1.6, dash=dash)
    elif k in ("sampler", "scale", "analyser"):
        r = 7.5 * s
        letter = {"sampler": "S", "scale": "BS", "analyser": "A"}[k]
        sh.add("symbol", f'<circle cx="{_n(x)}" cy="{_n(y)}" r="{_n(r)}" '
                         f'fill="{fill}" stroke="{stroke}" stroke-width="1.5" '
                         f'stroke-dasharray="{dash}"/>')
        sh.text("symbol", x, y + 0.4, letter, size=8, weight=700,
                fill=sk.ink)
    elif k in ("corridor_in", "corridor_out"):
        w, h = 26 * s, 15 * s
        d = 1 if k == "corridor_in" else -1
        sh.poly("symbol", [(x - w / 2, y - h / 2), (x + w / 2 - d * 7,
                                                    y - h / 2),
                           (x + w / 2, y), (x + w / 2 - d * 7, y + h / 2),
                           (x - w / 2, y + h / 2)], fill=fill, stroke=stroke,
                sw=1.6, dash=dash)
        if opts.show_tags and eq.label:
            sh.chip("label", x, y - 15, eq.label, size=8.5, weight=650,
                    pad=4.0, lod=2)
    else:                                   # junction and anything new
        sh.add("symbol", f'<circle cx="{_n(x)}" cy="{_n(y)}" r="{_n(4 * s)}" '
                         f'fill="{stroke}" stroke="none"/>')

    if eq.label and k not in ("destbox", "gate", "corridor_in",
                              "corridor_out"):
        sh.tip("top", x - 14, y - 18, 28, 36, eq.label)


def _draw_process_lines(sh: Sheet, geo, opts, norm) -> None:
    sk = sh.sk
    for pl in geo.process_lines:
        py1, py0 = norm.yr(pl.y1), norm.yr(pl.y0)
        x, y = sh.X(pl.x0), sh.Y(py1)
        w, h = sh.W(pl.x1 - pl.x0), sh.H(py1 - py0)
        operating = pl.status == "OPERATING"
        col = sk.ok if operating else sk.future
        sh.rect("body", x, y, w, h, fill=sk.chip if operating
                else "url(#hFut)", stroke=col, sw=1.8, rx=2,
                dash="" if operating else "5 3")
        if h < 22:
            # short card (e.g. the three Benguerir washing lines) — one row, or
            # the status header and the line name overprint each other
            sh.text("label", x + w / 2, y + h / 2, f"{'●' if operating else '◇'} "
                    f"{pl.label}", size=min(8.5, h * 0.55), fill=col,
                    weight=700, halo=sk.chip)
        else:
            sh.rect("body", x, y, w, 11, fill=col, stroke="none")
            sh.text("label", x + w / 2, y + 5.8,
                    f"{'●' if operating else '◇'} {pl.status}", size=8,
                    fill="#ffffff", weight=700, ls=0.6)
            sh.text("label", x + w / 2, y + h / 2 + 4, pl.label, size=10,
                    weight=600)
        sh.tip("top", x, y, w, h,
               f"{pl.label}\nstatus {pl.status} (static configuration, not "
               f"live equipment state)\nevidence {pl.evidence_status}\n"
               f"{pl.note or pl.source}")


def _draw_chips(sh: Sheet, geo, opts, norm) -> None:
    sk = sh.sk
    for ch in geo.chips:
        gap = ch.kind == "gap"
        # TBC comments are removed on request
        if "TBC" in (ch.tag or "") or "TBC" in (ch.note or ""):
            continue
        if gap and not opts.show_evidence:
            continue
        cx, cy = sh.X(ch.x), sh.Y(norm.yr(ch.y))
        sh.chip("label", cx, cy, ch.tag, size=9.5,
                fill="#fdf6e6" if gap else sk.chip,
                stroke=sk.caution if gap else sk.hair,
                ink=sk.ink, mono=False, weight=500,
                lod=3 if (gap or len(ch.tag) > 30) else 1)
        sh.tip("top", cx - 40, cy - 8, 80, 16, f"{ch.tag}\n{ch.note}")


def _draw_frame(sh: Sheet, geo, state, basis: str, opts) -> None:
    """Drawing frame + title block. States the vertical exaggeration."""
    sk = sh.sk
    vw = sh.w + PAD + PAD_R
    vh = sh.h + PAD * 2 + TITLE_H
    sh.rect("panel", 6, 6, vw - 12, vh - 12, fill="none", stroke=sk.outline,
            sw=2.0)
    ty = sh.h + PAD * 2 - 4
    sh.line("top", 6, ty, vw - 6, ty, stroke=sk.outline, sw=1.6)

    sh.title("top", 18, ty + 17, geo.title.upper()[:70], size=11,
             fill=sk.ink)
    sh.text("top", 18, ty + 33,
            geo.source[:110] if geo.source else "", size=9, fill=sk.dim,
            anchor="start")

    cells = [
        ("BASIS", basis),
        ("BUCKET", f"{state.bucket} / {state.monitor.max_bucket}"),
        ("TIME", state.timestamp),
        ("SCALE", f"SCHEMATIC — NOT TO SCALE · V.E. ×{sh.ve:g}"),
    ]
    x = vw - 18
    for label, value in reversed(cells):
        w = max(len(str(value)) * 5.9 + 18, 84)
        x -= w
        sh.rect("top", x, ty + 6, w, 30, fill="none", stroke=sk.hair, sw=1.0)
        sh.title("top", x + 7, ty + 15, label, size=7.5)
        sh.text("top", x + 7, ty + 28, str(value)[:40], size=9,
                fill=sk.ink, anchor="start", mono=True)


def _draw_stamps_and_legend(sh: Sheet, geo, opts, norm) -> None:
    sk = sh.sk
    for st in geo.stamps:
        sh.chip("top", sh.X(st.x), sh.Y(norm.yr(st.y)), st.text, size=9.5,
                fill=sk.chip, stroke=sk.caution, weight=600, lod=3)
    items = [("ACTIVE", sk.ok), ("IDLE", sk.idle), ("UNAVAILABLE", sk.alarm),
             ("FUTURE", sk.future)]
    x = 20
    y = sh.h + PAD * 2 + 46          # inside the title strip, below the title
    for label, col in items:
        sh.rect("top", x, y - 4.5, 9, 9, fill=col, stroke=sk.outline, sw=0.8)
        sh.text("top", x + 13, y + 0.4, label, size=8, fill=sk.dim,
                anchor="start", weight=600)
        x += 20 + len(label) * 5.2
    if opts.show_evidence:
        sh.text("top", x + 8, y + 0.4,
                "◐ ASSUMED   ▢ display-only   ! below safety   "
                "✕ hard limit", size=8, fill=sk.dim,
                anchor="start")


# ================================== build ====================================
def build(sc, state, geo, opts, *, dark: bool = False,
          basis: str = "", vert_exag: float = VERT_EXAG,
          px_width: Optional[float] = None,
          harmonize: Optional[bool] = None,
          y_band: Optional[Tuple[float, float]] = None) -> Tuple[str, float]:
    """Return (svg_markup, aspect_ratio_height_over_width).

    ``harmonize`` re-spaces pile rows onto a uniform grid; it defaults ON for
    single-site views and OFF for the composite CHAIN (whose y is geography).
    ``y_band`` restricts drawing to a horizontal slice of the yard.
    """
    from ui.process_mimic import _conv_state

    if harmonize is None:
        harmonize = getattr(geo, "site_id", "") in ("LOUTA", "BENGUERIR", "MPH")
    sk = DARK if dark else LIGHT
    norm = NormGeo(geo, harmonize)
    sh = Sheet(geo, sk, vert_exag, y_bounds=(norm.ymin, norm.ymax))
    ms = state.monitor
    pile_vm = {p.id: p for sv in ms.sites.values() for p in sv.piles}
    mach_vm = {m.id: m for sv in ms.sites.values() for m in sv.machines}
    rails = {r.rail_id: r for r in geo.rails}

    def keep(y: float) -> bool:
        return y_band is None or (y_band[0] <= y <= y_band[1])

    _draw_frame(sh, geo, state, basis or state.basis, opts)
    if y_band is None:
        _draw_zones(sh, geo, opts, norm)
        _draw_process_lines(sh, geo, opts, norm)
    else:
        for z in geo.zones:
            if keep((z.y0 + z.y1) / 2):
                _draw_zones(sh, type("G", (), {"zones": [z]})(), opts, norm)

    for cg in geo.conveyors:
        if not keep(sum(p[1] for p in cg.pts) / max(len(cg.pts), 1)):
            continue
        cstate, tph, cap, _, _ = _conv_state(state, cg, opts)
        _draw_conveyor(sh, cg, cstate, tph, cap, opts, norm)

    # display-only pile groupings (e.g. the Mine Ore block): draw a titled
    # frame round the members and drop their redundant ids
    grouped: Dict[str, str] = {}
    present_ids = {p.pile_id for p in geo.piles}
    for members, title in PILE_GROUPS:
        mem = [m for m in members if m in present_ids and m in norm.box]
        if not mem:
            continue
        boxes = [norm.box[m] for m in mem]
        gx0 = min(b[0] for b in boxes) - 1.4
        gy0 = min(b[1] for b in boxes) - 1.6
        gx1 = max(b[2] for b in boxes) + 1.4
        gy1 = max(b[3] for b in boxes) + 1.6
        if y_band is not None and not keep((gy0 + gy1) / 2):
            continue
        for m in mem:
            grouped[m] = title
        sh.rect("line", sh.X(gx0), sh.Y(gy1), sh.W(gx1 - gx0),
                sh.H(gy1 - gy0), fill="none", stroke=sk.outline, sw=1.6,
                rx=3)
        sh.chip("label", sh.X(gx0) + 3, sh.Y(gy1) - 1, title, size=9.5,
                anchor="start", weight=700, ink=sk.ink, stroke=sk.outline,
                pad=4.0, lod="1")

    for pg in geo.piles:
        if keep((pg.y0 + pg.y1) / 2):
            _draw_pile(sh, state, pg, pile_vm.get(pg.pile_id), opts, norm,
                       suppress_id=pg.pile_id in grouped)

    for rl in geo.rails:
        if keep(rl.y):
            _draw_rail(sh, rl, opts, norm)

    for mg in geo.machines:
        rl = rails.get(mg.rail_id)
        if rl is None or not keep(rl.y):
            continue
        _draw_machine(sh, state, geo, mg, mach_vm.get(mg.machine_id), rl,
                      opts, norm)

    for eq in geo.equipment:
        if keep(eq.y):
            _draw_equipment(sh, state, eq, opts, norm)

    if y_band is None:
        _draw_chips(sh, geo, opts, norm)
        _draw_stamps_and_legend(sh, geo, opts, norm)

    svg, vw, vh = sh.render(px_width)
    return svg, vh / vw


# ================================= legend ====================================
#: (label, machine kind, state) shown on the symbol/state key.
LEGEND_CELLS = [
    ("stacker · idle", "stacker", "Idle"),
    ("stacker · stacking", "stacker", "Stacking"),
    ("reclaimer · idle", "reclaimer", "Idle"),
    ("reclaimer · reclaiming", "reclaimer", "Reclaiming"),
    ("travelling", "stacker", "Travelling"),
    ("maintenance", "reclaimer", "Maintenance"),
    ("unavailable", "stacker", "Unavailable"),
    ("future", "reclaimer", "Future"),
]

CELL_W = 132.0
CELL_H = 108.0


def legend_svg(dark: bool = False, px_width: Optional[float] = None) -> str:
    """Symbol + state key, drawn with the SAME ``machine_glyph`` as the sheet.

    A legend that redraws its own symbols drifts from the drawing; this one
    cannot, because it calls the same function.
    """
    sk = DARK if dark else LIGHT

    class _G:                      # minimal geo stub: the key has no yard
        bounds = (0.0, 0.0, len(LEGEND_CELLS) * CELL_W / PX_PER_UNIT,
                  CELL_H / (PX_PER_UNIT * 1.0))

    sh = Sheet(_G(), sk, vert_exag=1.0)
    vw = len(LEGEND_CELLS) * CELL_W + 16
    vh = CELL_H + 30

    sh.parts["panel"].append(
        f'<rect x="2" y="2" width="{_n(vw - 4)}" height="{_n(vh - 4)}" '
        f'rx="3" fill="{sk.sheet}" stroke="{sk.outline}" stroke-width="1.6"/>')
    sh.title("top", 12, 15, "SYMBOL & STATE KEY — colour is state, "
             "silhouette is machine type", size=9, fill=sk.dim)

    for i, (label, kind, state) in enumerate(LEGEND_CELLS):
        cx = 8 + CELL_W * i + CELL_W / 2
        ry = 62.0
        col = getattr(sk, STATE_COL.get(state, "idle"))
        future = state == "Future"
        sh.line("line", 8 + CELL_W * i + 12, ry, 8 + CELL_W * (i + 1) - 12, ry,
                stroke=sk.outline, sw=1.4, dash="6 3" if future else "")
        machine_glyph(sh, cx, ry, ry + 34, kind, col=col, body=sk.body,
                      line_col=sk.outline, lw=1.8,
                      dash="5 3" if future else "")
        sh.text("symbol", cx, ry + 0.4, STATE_GLYPH.get(state, "·"), size=9,
                fill="#ffffff", weight=700)
        sh.text("label", cx, vh - 9, label, size=9, fill=sk.dim, weight=600)
        if i:
            sh.line("panel", 8 + CELL_W * i, 22, 8 + CELL_W * i, vh - 20,
                    stroke=sk.hair, sw=1.0)

    body = "".join("".join(sh.parts[k]) for k in Sheet.LAYERS)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {_n(vw)} {_n(vh)}" '
            f'width="{_n(px_width) if px_width else "100%"}" '
            f'preserveAspectRatio="xMidYMid meet" '
            f'style="display:block;background:{sk.sheet}">'
            f"{_defs(sk)}{body}</svg>"), vh / vw
