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
from contextlib import contextmanager
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
# re-drawn from a common lane width CENTRED ON ITS GEOMETRY CENTRE: order,
# adjacency and machine docking are preserved (centres do not move). A
# geometry-owned ``visual_width_factor`` may retain a confirmed relative pad
# size (the larger Benguerir mine pair); volume remains carried by the fill bar
# + tonnage label. Rails and conveyors pinned to old extents are remapped to
# follow, so nothing dangles (see ``NormGeo``).
NORM_W = 13.6               # uniform pile length (units) — < the 15u min gap
NORM_H = 1.4                # uniform pile depth (units)
RAIL_MARGIN = 1.6           # rail overhang beyond its flanked piles

# Display-only pile groupings: a set of piles that read as ONE titled bunker
# block. The members keep their fill/grade/tonnage but drop their redundant
# individual ids in favour of the block title. Never a model grouping —
# reachability, jobs and inventory stay per pile.
PILE_GROUPS = [
    (("BG-MINE-1", "BG-MINE-2"), "MINE ORE"),
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


LIGHT = Sk(sheet="#f4f5f6", panel="#e7eaec", body="#f7f8f9", outline="#4e565d",
           ink="#2f353b", dim="#98a0a7", hair="#d7dbde", chip="#ffffff",
           ok=t.OK, caution=t.CAUTION, alarm=t.ALARM, info="#1f3a5f",
           idle="#8b98a3", future=t.FUTURE)
DARK = Sk(sheet="#131a20", panel="#1b242b", body="#2a353e", outline="#7d8f9c",
          ink="#dde7ee", dim="#8fa1ae", hair="#3a4750", chip="#1e272e",
          ok="#4fae5c", caution="#dda63a", alarm="#e0685c", info="#5f9fd8",
          idle="#78868f", future="#a48fe0")

STATE_COL = {"Stacking": "ok", "Reclaiming": "ok", "Travelling": "info",
             "Idle": "idle", "Maintenance": "caution",
             "Unavailable": "alarm", "Fault": "alarm", "Future": "future",
             "Planning disabled": "idle"}
STATE_GLYPH = {"Stacking": "▶", "Reclaiming": "◀", "Travelling": "↔",
               "Idle": "·", "Maintenance": "⚒", "Unavailable": "✕",
               "Fault": "✕", "Future": "◇", "Planning disabled": "–"}


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

    Always: every pile is drawn from the common lane width (times its optional
    geometry-owned width factor), centred on its geometry centre, and
    rails/conveyors follow.

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
            factor = max(0.25, float(
                getattr(pg, "visual_width_factor", 1.0) or 1.0))
            draw_w = NORM_W * factor
            self.box[pg.pile_id] = (cx - draw_w / 2, ny - NORM_H / 2,
                                    cx + draw_w / 2, ny + NORM_H / 2)
            yscale = (NORM_H / old_h[pg.pile_id]) if old_h[pg.pile_id] else 1.0
            feats.append(_Feature(pg.x0, pg.x1, cx - draw_w / 2,
                                  cx + draw_w / 2, old_c[pg.pile_id], ny,
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
_TIP_TBC_RE = _re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z0-9]+[_/-])*TBC"
    r"(?:[_/-][A-Za-z0-9]+)*(?![A-Za-z0-9])",
    _re.IGNORECASE,
)


def _strip_tbc(s):
    """Remove every TBC mention from a DRAWN label (signals/comments off, on
    request). Hovers keep the underlying data."""
    if not s or "TBC" not in str(s):
        return s
    out = _TBC_RE.sub(" ", str(s))
    return _re.sub(r"\s+", " ", out).strip(" ·—–-/") or ""


def _public_tip_text(s) -> str:
    """Keep tooltip meaning while replacing internal evidence shorthand."""
    return _TIP_TBC_RE.sub("pending", str(s or ""))


def e(v) -> str:
    return html.escape(str(v), quote=True)


def _n(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


# ------------------------------------------------------------------ canvas ---
class Sheet:
    """Accumulates SVG in painter order: panels → bodies → lines → labels."""

    LAYERS = ("panel", "body", "line", "symbol", "label", "top")

    def __init__(self, geo, sk: Sk, vert_exag: float = VERT_EXAG,
                 y_bounds: Optional[Tuple[float, float]] = None,
                 title_h: float = TITLE_H) -> None:
        self.sk = sk
        self.bx0, self.by0, self.bx1, self.by1 = geo.bounds
        if y_bounds is not None:               # harmonised vertical extent
            self.by0, self.by1 = y_bounds
        self.sx = PX_PER_UNIT
        self.sy = PX_PER_UNIT * vert_exag
        self.ve = vert_exag
        self.title_h = max(0.0, float(title_h))
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

    @contextmanager
    def asset(self, asset_id: str, kind: str, *, site: str = "",
              state: str = "", grade: str = "", label: str = ""):
        """Wrap one asset without disturbing the established painter layers.

        A pile or machine is deliberately painted into several SVG layers
        (body, line, label and hit target). Moving those fragments into one
        monolithic group would alter z-order and make routes cover labels.
        Instead, each non-empty layer slice receives the same canonical
        metadata group. Only the top/hit-target slice enters keyboard tab
        order, so one physical asset remains one keyboard stop.
        """
        starts = {layer: len(self.parts[layer]) for layer in self.LAYERS}
        yield

        aid = str(asset_id or "").strip()
        akind = str(kind or "equipment").strip()
        if not aid:
            return
        cls_kind = _re.sub(r"[^a-z0-9_-]+", "-", akind.lower()).strip("-")
        metadata = (
            f'class="asset asset--{e(cls_kind or "equipment")}" '
            f'data-asset-id="{e(aid)}" data-asset-kind="{e(akind)}"'
        )
        if site:
            metadata += f' data-site="{e(site)}"'
        if state:
            metadata += f' data-state="{e(state)}"'
        if grade:
            metadata += f' data-grade="{e(grade)}"'
        accessible = label or f"{aid}, {akind}"
        if state:
            accessible += f", {state}"

        for layer in self.LAYERS:
            start = starts[layer]
            if len(self.parts[layer]) == start:
                continue
            contents = self.parts[layer][start:]
            focus = (
                f' tabindex="0" role="img" aria-label="{e(accessible)}"'
                if layer == "top" else ""
            )
            self.parts[layer][start:] = [
                f"<g {metadata}{focus}>",
                *contents,
                "</g>",
            ]

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
        """Invisible hit target for native and viewer-enhanced tooltips."""
        public = _public_tip_text(text)
        attr = e(public).replace("\n", "&#10;")
        self.add(layer, f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" '
                        f'height="{_n(h)}" fill="transparent" '
                        f'class="hover-target" data-tooltip="{attr}" '
                        f'aria-label="{attr}" pointer-events="all">'
                        f'<title>{e(public)}</title>'
                        f'</rect>')

    def tip_path(self, layer, d: str, text: str, width: float = 18.0) -> None:
        """Accurate hover target following a routed line instead of its box."""
        public = _public_tip_text(text)
        attr = e(public).replace("\n", "&#10;")
        self.add(
            layer,
            f'<path d="{d}" fill="none" stroke="transparent" '
            f'stroke-width="{_n(width)}" stroke-linecap="round" '
            f'stroke-linejoin="round" class="hover-target" '
            f'data-tooltip="{attr}" aria-label="{attr}" '
            f'pointer-events="stroke"><title>{e(public)}</title></path>',
        )

    # -- document ---------------------------------------------------------
    def render(self, px_width: Optional[float] = None
               ) -> Tuple[str, float, float]:
        vw = self.w + PAD + PAD_R
        vh = self.h + PAD * 2 + self.title_h
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
        "<style>"
        ".asset[tabindex]:focus{outline:none}"
        f".asset[tabindex]:focus .hover-target{{stroke:{sk.info};"
        "stroke-opacity:.9}}"
        "</style>"
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
            # Tight to the piles, while reserving one compact heading band.
            # The selected redesign uses concise stage headings to make the
            # physical flow readable without restoring long engineering prose.
            draws_title = bool(z.label)
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
            label = z.label.upper()
            short = label.split("(")[0].split("—")[0].split("·")[0].strip()
            short = short or label
            sh.title(
                "label", x + 8, y + 11, short,
                size=9.5 if z.style == "scope" else 8.2,
                fill=sk.ink if z.style == "scope" else sk.dim,
                lod="1",
            )
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
        sh.tip(
            "top",
            x0,
            ytop - 12,
            x1 - x0,
            12,
            f"{pg.pile_id} concurrent work\n"
            f"status {cvm.status}\n"
            f"separation {cvm.sep_m:,.0f} / minimum {cvm.req_m:,.0f} m",
        )

    # ---- hover: deliberately short, operator-useful information only -----
    repere_ends = state.space_repere_ends.get(pg.pile_id, (0, 1000))
    position = format_repere_range(*repere_ends)
    if vm is not None:
        band_grades = list(dict.fromkeys(
            glabel or gid
            for _, _, gid, glabel, _ in bands
            if glabel or gid
        ))
        grade = (
            " / ".join(band_grades)
            if len(band_grades) > 1
            else vm.grade_label or vm.grade
            or (band_grades[0] if band_grades else "—")
        )
    else:
        component_grades = [
            component.grade_label or component.grade_id
            for component in pg.display_grade_components
            if component.grade_label or component.grade_id
        ]
        grade = (
            " / ".join(dict.fromkeys(component_grades))
            or pg.display_grade_label
            or pg.display_grade_id
            or "—"
        )
    quantity = (
        f"{mass:,.0f} t ({fill * 100:.0f}% of {cap:,.0f} t)"
        if cap else f"{mass:,.0f} t"
    )
    sh.tip("top", x0, ytop, x1 - x0, band_h,
           f"{pg.pile_id}\n"
           f"tonnage {quantity}\n"
           f"grade {grade}\n"
           f"position {position}")


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
    travel = format_repere_range(
        int(round(1000 * rl.travel[0])),
        int(round(1000 * rl.travel[1])),
    )
    sh.tip_path(
        "top",
        f"M{_n(x0)},{_n(y)} L{_n(x1)},{_n(y)}",
        f"{rl.rail_id}\n"
        f"type {rl.kind.replace('_', ' ')} machine line\n"
        f"serves {', '.join(rl.flanks) or '—'}\n"
        f"travel {travel}",
        width=14.0,
    )


def _machine_status(state, mg, vm) -> str:
    """Canonical display state shared by drawing and asset metadata."""
    mid = mg.machine_id
    status = ("Future" if mg.future else
              "Unavailable" if mid in state.down_machines else
              (vm.status if vm is not None else "Idle"))
    planning_disabled = bool(
        vm is not None and not getattr(vm, "planning_enabled", True))
    if status == "Idle" and planning_disabled:
        status = "Planning disabled"
    pv = state.machine_pos.get(mid)
    if status == "Idle" and pv is not None and pv.travelling:
        status = "Travelling"
    return status


def _draw_machine(sh: Sheet, state, geo, mg, vm, rl, opts, norm) -> None:
    """PLAN view of a rail-mounted machine: a travelling body on the rail with
    a boom reaching across into the pile it is serving."""
    import types as _types
    sk = sh.sk
    mid = mg.machine_id
    display = mg.display and vm is None
    status = _machine_status(state, mg, vm)
    planning_disabled = bool(
        vm is not None and not getattr(vm, "planning_enabled", True))
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

    dash = "5 3" if (display or planning_disabled) else ""
    body = sk.panel if (display or planning_disabled) else sk.body
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

    public_status = "Reference only" if display else status
    job = ""
    if vm is not None and vm.job_id:
        target = vm.target_space or "—"
        if vm.dest_id:
            target += f" → {vm.dest_id}"
        job = f" · job {vm.job_id} → {target}"
    position = f"position {format_repere(rep)}"
    if pv is not None and pv.work_repere is not None:
        position += f" · work {format_repere_range(*pv.work_repere)}"
    lines = [
        mid,
        f"state {public_status}{job}",
        position,
    ]
    if vm is not None:
        lines.append(f"rate {vm.rate_tph:,.0f} / {vm.rate_max_tph:,.0f} t/h")
    sh.tip(
        "top",
        cx - 26,
        min(ry, y_tip) - 18,
        52,
        abs(y_tip - ry) + 44,
        "\n".join(lines),
    )


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


def _draw_conveyor(sh: Sheet, cg, cstate: str, tph, cap, selected: bool,
                   opts, norm) -> None:
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
    active = cstate in ("active", "running")
    core = {"active": sk.ok, "running": sk.ok,
            "tripped": sk.alarm, "future": sk.future,
            "idle": sk.idle}.get(cstate, sk.idle)
    dim = opts.active_flows_only and not active
    op = 0.35 if dim else 1.0
    dash = "7 4" if (future or not cg.planning_enabled) else ""

    casing = sk.info if selected else sk.outline
    sh.path("line", d, stroke=casing, sw=8.2 if selected else 6.2,
            cap="round", join="round",
            op=op)
    sh.path("line", d, stroke=core, sw=3.2, cap="round", join="round",
            dash=dash, op=op)
    if active and opts.show_flows and not dim:
        _chevrons(sh, pts, "#ffffff")

    if opts.show_tags and cg.tag:
        i = max(0, min(len(pts) - 2, int(len(pts) * cg.label_frac) - 1))
        mx = (pts[i][0] + pts[i + 1][0]) / 2
        my = (pts[i][1] + pts[i + 1][1]) / 2 - sh.H(cg.label_dy) * 1.35
        label = cg.tag
        if tph is not None and cap:
            label = f"{cg.tag} · {tph:,.0f}/{cap:,.0f} t/h"
        sh.chip("label", mx, my, label, size=9.5, mono=True,
                stroke=core if active else sk.hair, lod=3)

    route_lines = [
        cg.tag or cg.cid,
        "type conveyor / route",
        f"state {cstate}",
    ]
    if tph is not None and cap:
        route_lines.append(f"flow {tph:,.0f} / {cap:,.0f} t/h")
    sh.tip_path("top", d, "\n".join(route_lines), width=16.0)


def _equipment_linked_state(state, eq) -> str:
    """Small shared state resolver for equipment drawing and metadata."""
    linked = [state.flows[link_id] for link_id in eq.link_ids
              if link_id in state.flows]
    linked_state = "configured" if not linked else "idle"
    if linked and any(flow.running for flow in linked):
        linked_state = "running"
    elif linked and all(flow.tripped for flow in linked):
        linked_state = "tripped"
    return linked_state


def _draw_equipment(sh: Sheet, state, eq, opts, norm) -> None:
    sk = sh.sk
    x, y = sh.X(eq.x), sh.Y(norm.yr(eq.y))
    s = 1.0 * eq.scale
    stroke = sk.outline
    dash = ""
    fill = sk.body
    k = eq.kind
    linked = [state.flows[link_id] for link_id in eq.link_ids
              if link_id in state.flows]
    linked_state = _equipment_linked_state(state, eq)
    if k in ("gate", "hopper"):
        stroke = {
            "running": sk.ok,
            "tripped": sk.alarm,
            "idle": sk.outline,
        }[linked_state]

    if k == "tower":
        w, h = 22 * s, 30 * s
        tip_box = (x - w / 2 - 3, y - h / 2 - 10, w + 6, h + 10)
        sh.rect("symbol", x - w / 2, y - h / 2, w, h, fill=fill,
                stroke=stroke, sw=1.8, dash=dash)
        sh.poly("symbol", [(x - w / 2 - 3, y - h / 2),
                           (x + w / 2 + 3, y - h / 2), (x, y - h / 2 - 10)],
                fill=sk.body, stroke=stroke, sw=1.8)
        for i in range(1, 3):
            sh.line("symbol", x - w / 2, y - h / 2 + i * h / 3,
                    x + w / 2, y - h / 2 + i * h / 3, stroke=sk.hair, sw=0.9)
    elif k == "hopper":
        w, h = 22 * s, 17 * s
        tip_box = (x - w / 2 - 2, y - h / 2 - 2, w + 4, h + 4)
        sh.poly("symbol", [
            (x - w / 2, y - h / 2),
            (x + w / 2, y - h / 2),
            (x + w * 0.30, y + h * 0.22),
            (x - w * 0.30, y + h * 0.22),
        ], fill=fill, stroke=stroke, sw=1.7,
                dash="4 2" if eq.tbc else "")
        sh.poly("symbol", [
            (x - w * 0.18, y + h * 0.22),
            (x + w * 0.18, y + h * 0.22),
            (x + w * 0.08, y + h / 2),
            (x - w * 0.08, y + h / 2),
        ], fill=sk.body, stroke=stroke, sw=1.4,
                dash="4 2" if eq.tbc else "")
        if eq.label:
            sh.text("label", x, y - 0.5, eq.label, size=7.2,
                    fill=sk.ink, weight=700, mono=True, halo=sk.chip)
    elif k == "destbox":
        w = sh.W(eq.w) or max(96.0, len(eq.label) * 7.2 + 26)
        h = sh.H(eq.h) or 46
        tip_box = (x - w / 2, y - h / 2, w, h)
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
        tip_box = (x - w / 2 - 2, y - h / 2 - 2, w + 4, h + 10)
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
        tip_box = (x - r, y - r, 2 * r, 2 * r)
        sh.poly("symbol", [(x - r, y - r * 0.7), (x + r, y - r * 0.7),
                           (x, y + r * 0.9)], fill=fill, stroke=stroke,
                sw=1.6, dash=dash)
    elif k in ("sampler", "scale", "analyser"):
        r = 7.5 * s
        tip_box = (x - r, y - r, 2 * r, 2 * r)
        letter = {"sampler": "S", "scale": "BS", "analyser": "A"}[k]
        sh.add("symbol", f'<circle cx="{_n(x)}" cy="{_n(y)}" r="{_n(r)}" '
                         f'fill="{fill}" stroke="{stroke}" stroke-width="1.5" '
                         f'stroke-dasharray="{dash}"/>')
        sh.text("symbol", x, y + 0.4, letter, size=8, weight=700,
                fill=sk.ink)
    elif k in ("corridor_in", "corridor_out"):
        w, h = 26 * s, 15 * s
        tip_box = (x - w / 2, y - h / 2, w, h)
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
        r = 7 * s
        tip_box = (x - r, y - r, 2 * r, 2 * r)
        sh.add("symbol", f'<circle cx="{_n(x)}" cy="{_n(y)}" r="{_n(4 * s)}" '
                         f'fill="{stroke}" stroke="none"/>')

    type_labels = {
        "tower": "transfer tower",
        "hopper": "hopper",
        "destbox": "destination / process block",
        "gate": "gate",
        "diverter": "route selector",
        "sampler": "sampler",
        "scale": "belt scale",
        "analyser": "analyser",
        "corridor_in": "corridor inlet",
        "corridor_out": "corridor outlet",
        "junction": "junction",
    }
    tip_lines = [
        eq.label or type_labels.get(k, k.replace("_", " ")),
        f"state {linked_state}",
        f"type {type_labels.get(k, k.replace('_', ' '))}",
    ]
    if k == "destbox" and linked:
        tip_lines.append(
            f"connected flow {sum(flow.tph for flow in linked):,.0f} t/h"
        )
    sh.tip("top", *tip_box, "\n".join(tip_lines))


def _draw_process_lines(sh: Sheet, geo, opts, norm) -> None:
    sk = sh.sk
    for pl in geo.process_lines:
        py1, py0 = norm.yr(pl.y1), norm.yr(pl.y0)
        x, y = sh.X(pl.x0), sh.Y(py1)
        w, h = sh.W(pl.x1 - pl.x0), sh.H(py1 - py0)
        operating = pl.status == "OPERATING"
        disabled = pl.status == "DISABLED"
        col = sk.ok if operating else (sk.alarm if disabled else sk.future)
        state_glyph = "●" if operating else ("✕" if disabled else "◇")
        sh.rect("body", x, y, w, h,
                fill=sk.chip if (operating or disabled) else "url(#hFut)",
                stroke=col, sw=1.8, rx=2,
                dash="" if (operating or disabled) else "5 3")
        if h < 22:
            # short card (e.g. the three Benguerir washing lines) — one row, or
            # the status header and the line name overprint each other
            sh.text("label", x + w / 2, y + h / 2,
                    f"{state_glyph} {pl.label}",
                    size=min(8.5, h * 0.55), fill=col,
                    weight=700, halo=sk.chip)
        else:
            sh.rect("body", x, y, w, 11, fill=col, stroke="none")
            sh.text("label", x + w / 2, y + 5.8,
                    f"{state_glyph} {pl.status}", size=8,
                    fill="#ffffff", weight=700, ls=0.6)
            sh.text("label", x + w / 2, y + h / 2 + 4, pl.label, size=10,
                    weight=600)
        tip_lines = [
            pl.label,
            f"type process line · {pl.line_id}",
            f"status {pl.status} (configured)",
        ]
        if pl.residence_minutes is not None:
            tip_lines.append(f"minimum residence {pl.residence_minutes:g} min")
        sh.tip("top", x, y, w, h, "\n".join(tip_lines))


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
        sh.tip("top", cx - 40, cy - 8, 80, 16,
               f"{ch.tag}\ntype diagram note")


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


def _site_hint(asset_id: str) -> str:
    """Conservative site fallback for geometry-only/reference assets."""
    aid = str(asset_id or "").upper()
    if (aid.startswith(("BG-", "BEN-"))
            or aid in {"ST3", "RC2", "RC3", "BG-TRAIN"}):
        return "BENGUERIR"
    if aid.startswith(("HP-", "M16", "ST01-", "RC01-", "PAP-", "DRY-")):
        return "MPH"
    if aid.startswith(("LT-", "LOU-", "102MG", "110MG", "110LG",
                       "120LG", "130LG", "140LG")):
        return "LOUTA"
    return ""


def _site_at(geo, x: float, y: float) -> str:
    """Infer a site from the smallest labelled scope containing a point."""
    candidates = []
    for zone in getattr(geo, "zones", []):
        if not (zone.x0 <= x <= zone.x1 and zone.y0 <= y <= zone.y1):
            continue
        label = str(zone.label or "").upper()
        site = next((sid for sid in ("LOUTA", "BENGUERIR", "MPH")
                     if sid in label), "")
        if site:
            candidates.append(((zone.x1 - zone.x0) * (zone.y1 - zone.y0),
                               site))
    if candidates:
        return min(candidates)[1]
    site_id = str(getattr(geo, "site_id", "") or "")
    return site_id if site_id in {"LOUTA", "BENGUERIR", "MPH"} else ""


def _asset_slug(value: str) -> str:
    value = _strip_tbc(value or "")
    slug = _re.sub(r"[^A-Za-z0-9]+", "-", str(value).upper()).strip("-")
    return slug


def _equipment_identity(eq, index: int, site: str) -> Tuple[str, str]:
    """Stable presentation identity for ``EquipGeo``, which has no id field."""
    kind = str(eq.kind or "equipment")
    label = str(eq.label or "")
    clean_label = _asset_slug(label)
    links = list(eq.link_ids or [])

    if kind == "destbox":
        destination_id = next(
            (destination for destination in
             ("PAP-1", "PAP-2", "DRY-FEED", "BG-TRAIN")
             if clean_label == destination
             or clean_label.startswith(destination + "-")),
            "",
        )
        if destination_id:
            return destination_id, "destination"
    if kind == "hopper":
        hopper_ids = {
            "TM1": "BG-HOP-TM1",
            "TSM2": "BG-HOP-TSM2",
            "TSM3": "BG-HOP-TSM3",
        }
        hopper_id = hopper_ids.get(
            clean_label,
            f"{site[:3]}-HOP-{clean_label}"
            if clean_label else f"HOPPER-{index + 1:02d}",
        )
        # The linked feed route already owns the canonical model id.  Keep the
        # physical hopper as a distinct visual asset while retaining that id
        # as the readable prefix.
        return f"{hopper_id}:HOPPER", "hopper"
    if kind in ("corridor_in", "corridor_out"):
        route = next((link for link in links if link in {"C1", "C2"}),
                     links[0] if links else clean_label or f"{index + 1:02d}")
        endpoint = "MPH" if kind == "corridor_in" else (site or "SOURCE")
        return f"IF-{_asset_slug(route)}-{endpoint}", "corridor"

    if clean_label:
        return clean_label, "equipment"
    if links:
        suffix = _asset_slug(links[0])
        if len(links) > 1:
            suffix += f"-PLUS-{len(links) - 1}"
        return f"{kind.upper()}-{suffix}", "equipment"
    return f"{kind.upper()}-{index + 1:02d}", "equipment"


# ================================== build ====================================
def build(sc, state, geo, opts, *, dark: bool = False,
          basis: str = "", vert_exag: float = VERT_EXAG,
          px_width: Optional[float] = None,
          harmonize: Optional[bool] = None,
          y_band: Optional[Tuple[float, float]] = None,
          viewer_chrome: bool = True) -> Tuple[str, float]:
    """Return (svg_markup, aspect_ratio_height_over_width).

    ``harmonize`` re-spaces pile rows onto a uniform grid; it defaults ON for
    single-site views and OFF for the composite CHAIN (whose y is geography).
    ``y_band`` restricts drawing to a horizontal slice of the yard.
    """
    from ui.process_mimic import _conv_state
    from mph_sim.flow_config import destination_line_operating_state

    if harmonize is None:
        harmonize = getattr(geo, "site_id", "") in ("LOUTA", "BENGUERIR", "MPH")
    sk = DARK if dark else LIGHT
    norm = NormGeo(geo, harmonize)
    sh = Sheet(
        geo, sk, vert_exag, y_bounds=(norm.ymin, norm.ymax),
        title_h=TITLE_H if viewer_chrome else 0.0,
    )
    ms = state.monitor
    pile_vm = {p.id: p for sv in ms.sites.values() for p in sv.piles}
    mach_vm = {m.id: m for sv in ms.sites.values() for m in sv.machines}
    rails = {r.rail_id: r for r in geo.rails}
    space_site = {space.id: space.site for space in sc.spaces}
    machine_site = {machine.id: machine.site for machine in sc.machines}
    node_site = {node.id: node.site for node in sc.nodes}
    link_site = {}
    link_origin_site = {}
    for link in sc.links:
        sites = {node_site.get(link.src, ""), node_site.get(link.dst, "")}
        sites.discard("")
        link_site[link.id] = next(iter(sites)) if len(sites) == 1 else "GLOBAL"
        link_origin_site[link.id] = node_site.get(link.src, "")
    rail_site = {}
    for machine_geo in geo.machines:
        rail_site.setdefault(
            machine_geo.rail_id,
            machine_site.get(machine_geo.machine_id)
            or _site_hint(machine_geo.machine_id),
        )

    def keep(y: float) -> bool:
        return y_band is None or (y_band[0] <= y <= y_band[1])

    if viewer_chrome:
        _draw_frame(sh, geo, state, basis or state.basis, opts)
    if y_band is None:
        for zone_index, zone in enumerate(geo.zones):
            zone_site = (
                _site_hint(zone.label)
                or _site_at(
                    geo, (zone.x0 + zone.x1) / 2,
                    (zone.y0 + zone.y1) / 2,
                )
            )
            zone_kind = "site" if zone.style == "scope" else "zone"
            zone_id = (
                f"SITE:{zone_site}" if zone_kind == "site" and zone_site
                else f"ZONE:{zone_site or 'GLOBAL'}:"
                     f"{_asset_slug(zone.label) or zone_index + 1}"
            )
            with sh.asset(
                    zone_id, zone_kind, site=zone_site,
                    state="Configured", label=zone.label or zone_id):
                zone_geo = type(
                    "ZoneSlice", (), {
                        "zones": [zone],
                        "piles": getattr(geo, "piles", []),
                    },
                )()
                _draw_zones(sh, zone_geo, opts, norm)
        for process_line in geo.process_lines:
            line_site = (_site_hint(process_line.line_id)
                         or _site_at(
                             geo,
                             (process_line.x0 + process_line.x1) / 2,
                             (process_line.y0 + process_line.y1) / 2,
                         ))
            with sh.asset(
                    process_line.line_id, "processLine", site=line_site,
                    state=process_line.status, label=process_line.label):
                line_geo = type(
                    "ProcessLineSlice",
                    (),
                    {"process_lines": [process_line]},
                )()
                _draw_process_lines(sh, line_geo, opts, norm)
    else:
        for zone_index, z in enumerate(geo.zones):
            if keep((z.y0 + z.y1) / 2):
                zone_site = (
                    _site_hint(z.label)
                    or _site_at(
                        geo, (z.x0 + z.x1) / 2,
                        (z.y0 + z.y1) / 2,
                    )
                )
                zone_kind = "site" if z.style == "scope" else "zone"
                zone_id = (
                    f"SITE:{zone_site}"
                    if zone_kind == "site" and zone_site
                    else f"ZONE:{zone_site or 'GLOBAL'}:"
                         f"{_asset_slug(z.label) or zone_index + 1}"
                )
                with sh.asset(
                        zone_id, zone_kind, site=zone_site,
                        state="Configured", label=z.label or zone_id):
                    _draw_zones(
                        sh,
                        type("G", (), {
                            "zones": [z],
                            "piles": getattr(geo, "piles", []),
                        })(),
                        opts,
                        norm,
                    )

    for cg in geo.conveyors:
        if not keep(sum(p[1] for p in cg.pts) / max(len(cg.pts), 1)):
            continue
        cstate, tph, cap, _, selected = _conv_state(state, cg, opts)
        sites = {link_site.get(link_id, "") for link_id in cg.link_ids}
        sites.discard("")
        conveyor_site = (
            machine_site.get(cg.machine_id, "")
            or (next(iter(sites)) if len(sites) == 1 else
                "GLOBAL" if sites else "")
            or _site_hint(cg.cid)
            or (_site_at(geo, *cg.pts[0]) if cg.pts else "")
        )
        grades = []
        if cg.machine_id:
            grades = [job.grade for job in state.jobs
                      if job.machine == cg.machine_id and job.grade]
        else:
            grades = [state.flows[link_id].grade for link_id in cg.link_ids
                      if link_id in state.flows
                      and state.flows[link_id].grade]
        route_grade = ", ".join(dict.fromkeys(grades))
        with sh.asset(
                cg.cid, "route", site=conveyor_site, state=cstate,
                grade=route_grade, label=cg.tag or cg.cid):
            _draw_conveyor(sh, cg, cstate, tph, cap, selected, opts, norm)

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
            vm = pile_vm.get(pg.pile_id)
            pile_state = (
                "Future" if pg.future else
                vm.state if vm is not None else
                "Reference" if not pg.modelled else "Configured"
            )
            pile_grades = ", ".join(dict.fromkeys(
                gid for _, _, gid, _, _ in _pile_bands(pg, vm) if gid
            ))
            with sh.asset(
                    pg.pile_id, "pile",
                    site=space_site.get(pg.pile_id)
                    or _site_hint(pg.pile_id)
                    or _site_at(geo, (pg.x0 + pg.x1) / 2,
                                (pg.y0 + pg.y1) / 2),
                    state=pile_state, grade=pile_grades,
                    label=pg.canvas_label or pg.label or pg.pile_id):
                _draw_pile(
                    sh, state, pg, vm, opts, norm,
                    suppress_id=pg.pile_id in grouped,
                )

    for rl in geo.rails:
        if keep(rl.y):
            rail_state = "Reference" if rl.tbc else "Configured"
            with sh.asset(
                    rl.rail_id, "rail",
                    site=rail_site.get(rl.rail_id)
                    or _site_hint(rl.rail_id)
                    or _site_at(geo, (rl.x0 + rl.x1) / 2, rl.y),
                    state=rail_state, label=rl.label or rl.rail_id):
                _draw_rail(sh, rl, opts, norm)

    for mg in geo.machines:
        rl = rails.get(mg.rail_id)
        if rl is None or not keep(rl.y):
            continue
        vm = mach_vm.get(mg.machine_id)
        machine_state = (
            "Reference" if mg.display and vm is None
            else _machine_status(state, mg, vm)
        )
        with sh.asset(
                mg.machine_id, "machine",
                site=machine_site.get(mg.machine_id)
                or _site_hint(mg.machine_id)
                or rail_site.get(mg.rail_id, ""),
                state=machine_state, label=mg.label or mg.machine_id):
            _draw_machine(sh, state, geo, mg, vm, rl, opts, norm)

    for equipment_index, eq in enumerate(geo.equipment):
        if keep(eq.y):
            sites = {link_site.get(link_id, "") for link_id in eq.link_ids}
            sites.discard("")
            if eq.kind == "corridor_in":
                equipment_site = "MPH"
            elif eq.kind == "corridor_out" and eq.link_ids:
                equipment_site = (
                    link_origin_site.get(eq.link_ids[0], "")
                    or _site_hint(eq.label)
                )
            else:
                equipment_site = (
                    next(iter(sites)) if len(sites) == 1 else
                    "GLOBAL" if sites else
                    _site_hint(eq.label) or _site_at(geo, eq.x, eq.y)
                )
            equipment_id, equipment_kind = _equipment_identity(
                eq, equipment_index, equipment_site)
            equipment_state = _equipment_linked_state(state, eq)
            if equipment_kind == "destination":
                destination_id = (
                    "BG-TRAIN-PROD"
                    if equipment_id == "BG-TRAIN" else equipment_id
                )
                try:
                    equipment_state = destination_line_operating_state(
                        sc, destination_id).title()
                except (KeyError, StopIteration):
                    pass
            with sh.asset(
                    equipment_id, equipment_kind, site=equipment_site,
                    state=equipment_state, label=eq.label or equipment_id):
                _draw_equipment(sh, state, eq, opts, norm)

    if y_band is None:
        _draw_chips(sh, geo, opts, norm)
        if viewer_chrome:
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
