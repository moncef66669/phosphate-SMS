"""NEW flow-diagram renderer — a different drawing, not a re-skin.

This does not call ``ui.process_mimic.process_mimic_figure``. It reads the same
inputs (``MimicState`` + ``SiteGeometry`` + ``Scenario``) and draws them in a
new visual language. The production drawing stays available side by side as the
reference rendering.

What is different, and why
--------------------------
1. **A process-stream spine.** One left-to-right train — ARRIVAL ▸ RECEPTION ▸
   STORAGE ▸ RECLAIM ▸ DELIVERY — with the connectors drawn as bands whose
   THICKNESS is the modelled capacity and whose FILL is the actual flow. The
   production drawing renders every conveyor at the same weight, so a
   2,600 t/h main stream and a TBC note look equally important; here the
   hierarchy is geometric and "where is material moving" is answerable at a
   glance.
2. **A fixed label column.** Every tag, mass and rate readout lives in a
   reserved left gutter, never over the drawing. That removes the label
   collisions the production canvas has (e.g. the route-confluence caption
   over the diverter chain).
3. **Stockpiles as cross-sections, not progress bars.** A pile is a profile
   whose HEIGHT is fill % and whose horizontal bands are the grade
   composition at their repère positions — so a half-full pile looks
   half-full, and a multi-grade pile shows where each grade sits.
4. **One repère axis per bay instead of one per pile.** Same information,
   roughly a third of the vertical space, which is what buys room for the
   cross-sections.
5. **Machine lines between bay cards.** The validated alternation
   (ST01-A | Bay 1 | RC01-A | Bay 2 | …) becomes the literal reading order,
   each machine a carriage at its repère with its work zone bracketed and an
   arrow to the pile it is actually serving.
6. **Prose leaves the canvas.** Evidence and TBC text become corner badges;
   the wording goes to the inspector. Nothing is dropped — the badge legend
   maps every glyph back to its evidence class.

Preserved deliberately: grade colours, the evidence classes, the repère
convention, and every disclaimer. Colour still means the same things.

Layer semantics, stated so a dead control cannot hide:
  Flow          band fills, work zones, job lines and pile in/out readouts
  Tags          equipment tags and grade-band labels
  Evidence      TBC/ASSUMED/display-only badges and dashed outlines
  Alarms only   dims normal-state piles and machines (the spine is a summary
                strip and always stays readable)
  Active only   dims idle bands and parked machines
  Future exp.   handled upstream by ``geometry_for(show_future=...)``
  Job highlight outlines the machine running the job, its work zone, the job
                line in the gutter, and the stockpile it is working
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

import plotly.graph_objects as go

from . import tokens as t
from .bridge import format_repere, format_repere_range, grade_color

# ------------------------------------------------------------------ layout ---
X0, X1 = 2.0, 98.0                 # drawing extent (unit space)
LBL_X0, LBL_X1 = 2.0, 18.2         # label gutter
SEC_X0, SEC_X1 = 19.6, 91.0        # section / repère band
BDG_X0 = 92.0                      # evidence badges

SPINE_Y0, SPINE_Y1 = 1.0, 13.6
STACK_Y0 = 18.4                    # first card/track row

PILE_H = 12.0                      # one pile row
TRACK_H = 7.6                      # one machine-line row
CARD_PAD_TOP = 5.4                 # repère axis + bay title
CARD_PAD_BOT = 1.6
SITE_HDR_H = 5.0

# The console renders ~100 x-units across ~1180 px and ~4.5 px per y-unit, so
# one y-unit is roughly a third of an x-unit on screen. Vertical extents of
# symbols are written in y-units directly and were sized against that ratio;
# `PROFILE_H` is the tallest a full stockpile may draw inside its row.
PROFILE_H = 7.6
LINE_STEP = 2.2                    # legible baseline spacing in the gutter
ROW_GAP = 1.2                      # same breathing room after tracks as cards


@dataclass(frozen=True)
class Focus:
    """What the operator has selected, resolved to drawable identities."""
    machine: str = ""      # machine running the selected job
    space: str = ""        # stockpile the selected job is working
    asset: str = ""        # explicitly selected asset (ASSET FOCUS)
    job: str = ""

    def is_machine(self, mid: str) -> bool:
        return bool(mid) and mid in (self.machine, self.asset)

    def is_space(self, pid: str) -> bool:
        return bool(pid) and pid in (self.space, self.asset)


PROFILE_RAMP = 0.075               # stockpile end slope (fraction of width)
MIN_PROFILE = 0.30                 # an empty pile still shows its floor


def _sec_x(frac: float) -> float:
    return SEC_X0 + (SEC_X1 - SEC_X0) * min(max(frac, 0.0), 1.0)


def _rep_x(repere: float) -> float:
    return _sec_x(repere / 1000.0)


# ------------------------------------------------------------------ palette --
@dataclass
class Skin:
    bg: str
    panel: str
    card: str
    ink: str
    dim: str
    hair: str
    rule: str
    ok: str = t.OK
    caution: str = t.CAUTION
    alarm: str = t.ALARM
    info: str = t.INFO
    neutral: str = t.NEUTRAL
    future: str = t.FUTURE
    accent: str = t.ACCENT


LIGHT = Skin(bg=t.CANVAS, panel="#dfe4e8", card="#f4f6f7", ink="#2c363d",
             dim="#6b7a85", hair="#c3ccd2", rule="#9aa7b0")
DARK = Skin(bg=t.CANVAS_DARK, panel="#1b232a", card="#1a222a",
            ink="#ccd8e2", dim="#8698a5", hair="#2c3842", rule="#41505c",
            ok="#4fae5c", caution="#dda63a", alarm="#e0685c", info="#5f9fd8",
            neutral="#7d8b97", future="#a48fe0")

STATE_TONE = {"Stacking": "ok", "Reclaiming": "ok", "Travelling": "info",
              "Idle": "neutral", "Maintenance": "caution",
              "Unavailable": "alarm", "Fault": "alarm", "Future": "future"}
STATE_ICON = {"Stacking": "▶", "Reclaiming": "◀", "Travelling": "↔",
              "Idle": "·", "Maintenance": "⚒", "Unavailable": "✕",
              "Fault": "✕", "Future": "◇"}


def _tone(sk: Skin, name: str) -> str:
    return getattr(sk, name, sk.neutral)


# ------------------------------------------------------------- primitives ----
class Sheet:
    """Thin Plotly accumulator: shapes/annotations batched, hover as traces."""

    def __init__(self, sk: Skin) -> None:
        self.sk = sk
        self.shapes: List[dict] = []
        self.anns: List[dict] = []
        self.hx: List[float] = []
        self.hy: List[float] = []
        self.ht: List[str] = []

    # -- geometry ---------------------------------------------------------
    def rect(self, x0, y0, x1, y1, *, fill=None, line=None, width=1.0,
             dash=None, layer="above") -> None:
        self.shapes.append(dict(
            type="rect", x0=x0, y0=y0, x1=x1, y1=y1, layer=layer,
            fillcolor=fill or "rgba(0,0,0,0)",
            line=dict(color=line or "rgba(0,0,0,0)", width=width, dash=dash)))

    def line(self, x0, y0, x1, y1, *, color=None, width=1.0, dash=None,
             layer="above") -> None:
        self.shapes.append(dict(
            type="line", x0=x0, y0=y0, x1=x1, y1=y1, layer=layer,
            line=dict(color=color or self.sk.rule, width=width, dash=dash)))

    def poly(self, pts: Sequence[Tuple[float, float]], *, fill=None,
             line=None, width=1.0, dash=None, layer="above") -> None:
        path = "M " + " L ".join(f"{x:.3f},{y:.3f}" for x, y in pts) + " Z"
        self.shapes.append(dict(
            type="path", path=path, layer=layer,
            fillcolor=fill or "rgba(0,0,0,0)",
            line=dict(color=line or "rgba(0,0,0,0)", width=width, dash=dash)))

    # -- text -------------------------------------------------------------
    def text(self, x, y, s, *, size=9, color=None, anchor="center",
             vanchor="middle", bold=False, angle=0, mono=False) -> None:
        if not s:
            return
        fam = ("IBM Plex Mono, Consolas, monospace" if mono
               else "IBM Plex Sans, Segoe UI, sans-serif")
        self.anns.append(dict(
            x=x, y=y, text=f"<b>{s}</b>" if bold else s, showarrow=False,
            xanchor=anchor, yanchor=vanchor, textangle=angle,
            font=dict(size=size, color=color or self.sk.ink, family=fam)))

    # -- hover ------------------------------------------------------------
    def hover(self, x, y, text: str) -> None:
        self.hx.append(x)
        self.hy.append(y)
        self.ht.append(text)


# --------------------------------------------------------------- topology ----
@dataclass
class Spine:
    arrival: List = field(default_factory=list)
    reception: List = field(default_factory=list)
    stack: List = field(default_factory=list)
    reclaim: List = field(default_factory=list)
    route: List = field(default_factory=list)
    deliver: List = field(default_factory=list)
    recept_nodes: List[str] = field(default_factory=list)
    route_nodes: List[str] = field(default_factory=list)


def classify(sc, visible_spaces: Optional[Set[str]] = None,
             visible_sites: Optional[Set[str]] = None) -> Spine:
    """Split the route graph around the storage layer.

    Direction is resolved by reachability, not by naming, so this holds for any
    scenario: a non-storage link is ARRIVAL when its destination can still
    reach a stockpile, and ROUTE when its source is already downstream of one.

    ``visible_spaces`` scopes the result to the stockpiles the current view
    actually draws, so a site view's spine shows that site's stream rather than
    the whole three-site chain.
    """
    all_space_nodes = {(sp.node_id or f"SPACE:{sp.id}") for sp in sc.spaces}
    if visible_spaces:
        space_nodes = {(sp.node_id or f"SPACE:{sp.id}") for sp in sc.spaces
                       if sp.id in visible_spaces} or all_space_nodes
    else:
        space_nodes = all_space_nodes
    dest_nodes = {d.node_id for d in sc.destinations}

    fwd: Dict[str, List[str]] = {}
    bwd: Dict[str, List[str]] = {}
    for l in sc.links:
        fwd.setdefault(l.src, []).append(l.dst)
        bwd.setdefault(l.dst, []).append(l.src)

    def reach(seeds: Set[str], adj: Dict[str, List[str]]) -> Set[str]:
        seen, stack = set(seeds), list(seeds)
        while stack:
            n = stack.pop()
            for m in adj.get(n, ()):
                if m not in seen:
                    seen.add(m)
                    stack.append(m)
        return seen

    to_space = reach(space_nodes, bwd)      # nodes that can reach storage
    from_space = reach(space_nodes, fwd)    # nodes storage can reach

    # A site view must not absorb the rest of the chain: everything downstream
    # of Louta is technically "reachable from storage", including all of M16.
    # Node site membership is the boundary.
    node_site = {n.id: n.site for n in getattr(sc, "nodes", [])}

    def in_view(node: str) -> bool:
        if not visible_sites:
            return True
        s = node_site.get(node)
        return s is None or s in visible_sites

    sp = Spine()
    for l in sc.links:
        if l.dst in space_nodes:
            sp.stack.append(l)
        elif l.src in space_nodes:
            (sp.deliver if l.dst in dest_nodes else sp.reclaim).append(l)
        elif l.dst in to_space and l.src in to_space and in_view(l.dst):
            sp.arrival.append(l)
        elif l.src in from_space and in_view(l.src):
            (sp.deliver if l.dst in dest_nodes else sp.route).append(l)
        # links touching neither side of the VISIBLE storage layer belong to
        # another site's stream and are deliberately left out of this view.

    # the reception / take-off equipment shown as chips inside the blocks
    sp.recept_nodes = sorted({l.dst for l in sp.arrival} & to_space
                             - space_nodes)
    sp.route_nodes = sorted({l.dst for l in sp.reclaim} - space_nodes
                            - dest_nodes)
    return sp


def group_capacity(sc, links: Sequence) -> float:
    """Sum capacity, counting each shared-capacity group ONCE.

    Without this the M16 reception envelope would read 8 × 2,600 t/h; the
    model says the eight stacking legs share one 2,600 t/h path.
    """
    total = 0.0
    seen: Set[str] = set()
    for l in links:
        if l.shared_group:
            if l.shared_group in seen:
                continue
            seen.add(l.shared_group)
            members = [m for m in sc.links if m.shared_group == l.shared_group]
            total += min(m.capacity_tph for m in members)
        else:
            total += l.capacity_tph
    return total


def group_flow(state, links: Sequence) -> float:
    return sum(state.flows[l.id].tph for l in links if l.id in state.flows)


# ------------------------------------------------------------------- bays -----
@dataclass
class Bay:
    key: str
    label: str
    pile_ids: List[str]
    y: float
    site: str = ""


@dataclass
class Track:
    rail_id: str
    label: str
    kind: str
    y: float
    machines: List[str]
    serves: List[str]
    site: str = ""
    tbc: bool = False


def build_rows(geo, ms) -> Tuple[List, Dict[str, str]]:
    """Interleave bay cards and machine-line tracks in geometric order.

    A "bay" is the set of piles flanked by the same machine lines — which for
    M16 recovers the two-hangar bays, and elsewhere degrades to one pile per
    bay without special-casing any site.
    """
    site_of: Dict[str, str] = {}
    for sid, sv in ms.sites.items():
        for p in sv.piles:
            site_of[p.id] = sid

    flanked: Dict[str, Set[str]] = {}
    for r in geo.rails:
        for pid in r.flanks:
            flanked.setdefault(pid, set()).add(r.rail_id)

    pile_y = {pg.pile_id: (pg.y0 + pg.y1) / 2.0 for pg in geo.piles}

    bays: Dict[str, Bay] = {}
    for pg in geo.piles:
        key = "|".join(sorted(flanked.get(pg.pile_id, {pg.pile_id})))
        y = pile_y[pg.pile_id]
        b = bays.get(key)
        if b is None:
            bays[key] = Bay(key, "", [pg.pile_id], y,
                            site_of.get(pg.pile_id, ""))
        else:
            b.pile_ids.append(pg.pile_id)
            b.y = max(b.y, y)

    # Source geometry uses Plotly's y-up convention; the sheet reads top-down,
    # so order DESCENDING by y. Getting this backwards silently renumbered the
    # bays (HP-04 became "BAY 1") and mislabelled which bays a line serves.
    ordered_bays = sorted(bays.values(), key=lambda b: -b.y)
    for i, b in enumerate(ordered_bays, start=1):
        b.label = f"BAY {i}" if len(b.pile_ids) > 1 else b.pile_ids[0]
        b.pile_ids.sort(key=lambda pid: -pile_y.get(pid, 0.0))

    machines_on: Dict[str, List[str]] = {}
    for mg in geo.machines:
        machines_on.setdefault(mg.rail_id, []).append(mg.machine_id)

    tracks = []
    for r in geo.rails:
        served_bays = [b.label for b in ordered_bays
                       if any(p in r.flanks for p in b.pile_ids)]
        tracks.append(Track(
            r.rail_id, r.label or r.rail_id, r.kind, r.y,
            machines_on.get(r.rail_id, []), served_bays,
            site_of.get(r.flanks[0], "") if r.flanks else "", r.tbc))

    rows: List = sorted(ordered_bays + tracks,
                        key=lambda o: (-o.y, 0 if isinstance(o, Track) else 1))
    return rows, site_of


# ============================== the renderer =================================
def build(sc, state, geo, opts, *, dark: bool = False,
          ppu: float = 4.5) -> go.Figure:
    """Render the new diagram. Its HEIGHT is derived from its content (``ppu``
    pixels per layout unit) rather than fixed, so a row stays legible whether
    the view holds one bay or thirteen."""
    sk = DARK if dark else LIGHT
    sh = Sheet(sk)
    ms = state.monitor
    piles_geo = {p.pile_id: p for p in geo.piles}
    pile_vm = {p.id: p for sv in ms.sites.values() for p in sv.piles}
    mach_vm = {m.id: m for sv in ms.sites.values() for m in sv.machines}
    mach_geo = {m.machine_id: m for m in geo.machines}
    jobs_by_machine: Dict[str, List] = {}
    for j in state.jobs:
        jobs_by_machine.setdefault(j.machine, []).append(j)

    visible = {p.pile_id for p in geo.piles} & {sp.id for sp in sc.spaces}
    rows, site_of = build_rows(geo, ms)
    vis_sites = {s for pid, s in site_of.items() if s and pid in visible}
    spine = classify(sc, visible, vis_sites)
    multi_site = len({s for s in site_of.values() if s}) > 1

    # Job path highlight: the selected job resolves to the machine running it
    # and the stockpile it is working, and both are outlined.
    jv = next((j for j in state.jobs if j.id == opts.selected_job), None)
    sel = Focus(machine=jv.machine if jv else "", space=jv.space if jv else "",
                asset=opts.selected_asset, job=opts.selected_job)

    # ---------------------------------------------------------------- spine --
    _draw_spine(sh, sc, state, spine, opts, visible)

    # ------------------------------------------------------- bay/track stack -
    y = STACK_Y0
    cur_site = None
    for row in rows:
        if multi_site and row.site and row.site != cur_site:
            cur_site = row.site
            y = _draw_site_header(sh, sc, state, row.site, ms, y)
        if isinstance(row, Track):
            y = _draw_track(sh, sc, state, geo, row, y, mach_vm, mach_geo,
                            jobs_by_machine, opts, piles_geo, sel) + ROW_GAP
        else:
            y = _draw_bay(sh, sc, state, row, y, piles_geo, pile_vm, opts,
                          sel)
    y_end = y + 1.0

    _draw_footer(sh, state, geo, y_end, opts)
    y_end += 4.4

    height = int(min(2400, max(380, round(y_end * ppu))))
    fig = go.Figure()
    if sh.hx:
        fig.add_trace(go.Scatter(
            x=sh.hx, y=sh.hy, mode="markers", hoverinfo="text", text=sh.ht,
            marker=dict(size=13, color="rgba(0,0,0,0)"),
            showlegend=False, cliponaxis=False))
    fig.update_layout(
        shapes=sh.shapes, annotations=sh.anns,
        height=height, paper_bgcolor=sk.panel, plot_bgcolor=sk.bg,
        margin=dict(l=4, r=4, t=4, b=4), showlegend=False, dragmode="pan",
        font=dict(family="IBM Plex Sans, Segoe UI, sans-serif", size=9,
                  color=sk.ink),
        hoverlabel=dict(bgcolor=sk.card, bordercolor=sk.rule,
                        font=dict(family="IBM Plex Mono, monospace", size=11,
                                  color=sk.ink)),
        # Aspect is deliberately NOT locked: a stockyard elevation is a long,
        # shallow object, and locking it would either shrink the piles to
        # slivers or make the sheet metres tall. Only rectangles, polygons and
        # lines are drawn, so anisotropy distorts nothing that carries meaning.
        xaxis=dict(range=[0, 100], visible=False, fixedrange=False,
                   constrain="domain"),
        yaxis=dict(range=[y_end, 0], visible=False, fixedrange=False),
    )
    return fig


# ------------------------------------------------------------------ spine ----
_SPINE_COLS = [
    ("ARRIVAL", 2.0, 15.4),
    ("RECEPTION", 22.6, 38.0),
    ("STORAGE", 45.2, 60.6),
    ("RECLAIM", 67.8, 80.2),
    ("DELIVERY", 85.4, 98.0),
]


def _band(sh: Sheet, x0: float, x1: float, ycen: float, cap: float,
          flow: float, *, max_cap: float, state_tone: str, label: str,
          hover: str, dim: bool = False, dash: Optional[str] = None,
          show_flow: bool = True) -> None:
    """Connector whose thickness is capacity and whose fill is flow.

    With the Flow layer off the band keeps its capacity thickness and loses
    its fill — the geometry of the stream stays, the live reading goes.
    """
    sk = sh.sk
    thick = 0.9 + 3.6 * math.sqrt(min(cap, max_cap) / max_cap) if max_cap \
        else 0.9
    y0, y1 = ycen - thick / 2, ycen + thick / 2
    edge = _tone(sk, state_tone) if show_flow else sk.rule
    sh.rect(x0, y0, x1, y1, fill=sk.card, line=sk.hair if dim else edge,
            width=1.0, dash=dash)
    frac = 0.0 if not cap else min(1.0, flow / cap)
    if frac > 0 and show_flow:
        sh.rect(x0, y0, x0 + (x1 - x0) * frac, y1,
                fill=edge, line="rgba(0,0,0,0)")
    # direction arrow head
    mid = (y0 + y1) / 2
    sh.poly([(x1, mid), (x1 - 1.5, y0 - 0.35), (x1 - 1.5, y1 + 0.35)],
            fill=sk.hair if dim else edge, line="rgba(0,0,0,0)")
    sh.text((x0 + x1) / 2, y0 - 1.15,
            label if show_flow else f"cap {cap:,.0f}", size=8,
            color=sk.dim if dim else sk.ink, mono=True)
    sh.hover((x0 + x1) / 2, ycen, hover)


def _spine_block(sh: Sheet, title: str, x0: float, x1: float,
                 value: str, sub: str, chips: Sequence[str], tone: str,
                 hover: str) -> None:
    sk = sh.sk
    sh.rect(x0, SPINE_Y0, x1, SPINE_Y1, fill=sk.card, line=sk.rule, width=1.1)
    sh.rect(x0, SPINE_Y0, x1, SPINE_Y0 + 0.55, fill=_tone(sk, tone),
            line="rgba(0,0,0,0)")
    sh.text((x0 + x1) / 2, SPINE_Y0 + 1.9, title, size=8.5, bold=True,
            color=sk.dim)
    sh.text((x0 + x1) / 2, SPINE_Y0 + 4.4, value, size=14, bold=True,
            color=sk.ink, mono=True)
    sh.text((x0 + x1) / 2, SPINE_Y0 + 6.6, sub, size=7.6, color=sk.dim)
    cy = SPINE_Y0 + 8.5
    shown = list(chips[:3])
    if len(chips) > 3:
        shown[2] = f"+{len(chips) - 2} more"
    for c in shown:
        sh.rect(x0 + 0.9, cy - 0.95, x1 - 0.9, cy + 0.95, fill=sk.bg,
                line=sk.hair, width=0.8)
        sh.text((x0 + x1) / 2, cy, c[:26], size=7.2, color=sk.dim, mono=True)
        cy += 2.2
    sh.hover((x0 + x1) / 2, (SPINE_Y0 + SPINE_Y1) / 2, hover)


def _draw_spine(sh: Sheet, sc, state, sp: Spine, opts,
                visible: Set[str]) -> None:
    sk = sh.sk

    def agg(links):
        return group_flow(state, links), group_capacity(sc, links)

    f_arr, c_arr = agg(sp.arrival)
    f_stk, c_stk = agg(sp.stack)
    f_rcl, c_rcl = agg(sp.reclaim)
    f_rte, c_rte = agg(sp.route)
    f_dlv, c_dlv = agg(sp.deliver)
    max_cap = max(c_arr, c_stk, c_rcl, c_rte, c_dlv, 1.0)

    vis_piles = [p for sv in state.monitor.sites.values() for p in sv.piles
                 if not visible or p.id in visible]
    mass = sum(p.mass_t for p in vis_piles)
    capt = sum(p.capacity_t for p in vis_piles)
    vis_sites = {p.site for p in vis_piles}
    machines = [m for sv in state.monitor.sites.values() for m in sv.machines
                if not vis_sites or m.site in vis_sites]
    n_active_m = sum(1 for m in machines if m.job_id)
    n_m = len(machines)

    reachable_dests = {l.dst for l in sp.deliver}
    dests = sorted((d for d in sc.destinations
                    if not reachable_dests or d.node_id in reachable_dests),
                   key=lambda d: d.id)
    dem = sum(sc.demand_tph(d.id, state.bucket) for d in dests)

    def tone_of(flow, cap):
        if cap and flow > cap + 1e-6:
            return "alarm"
        return "ok" if flow > 0 else "neutral"

    NO_LINK = ("—", "not modelled in this view", "neutral")

    blocks = [
        ("ARRIVAL", *(NO_LINK[:2] if not sp.arrival else
                      (f"{f_arr:,.0f}", "t/h inbound")),
         [l.id for l in sp.arrival],
         NO_LINK[2] if not sp.arrival else tone_of(f_arr, c_arr),
         ("ARRIVAL — no modelled inbound link for the stockpiles in this "
          "view (e.g. an exogenous washing/beneficiation inflow); see the "
          "assumption register" if not sp.arrival else
          "ARRIVAL — corridors / simulated feed<br>"
          + "<br>".join(f"{l.id}: {state.flows[l.id].tph:,.0f} / "
                        f"{l.capacity_tph:,.0f} t/h"
                        for l in sp.arrival if l.id in state.flows))),
        ("RECEPTION", *(NO_LINK[:2] if not sp.stack else
                        (f"{f_stk:,.0f}", f"of {c_stk:,.0f} t/h envelope")),
         sp.recept_nodes,
         NO_LINK[2] if not sp.stack else tone_of(f_stk, c_stk),
         ("RECEPTION → stacking is NOT modelled for these stockpiles — "
          "no stacking link exists in the scenario graph"
          if not sp.stack else
          "RECEPTION → stacking<br>shared-capacity groups are counted once:"
          f"<br>envelope {c_stk:,.0f} t/h · flow {f_stk:,.0f} t/h")),
        ("STORAGE", f"{mass / 1000:,.0f} kt",
         f"{100.0 * mass / capt if capt else 0:.0f} % of {capt / 1000:,.0f} kt",
         [f"{n_active_m}/{n_m} machines busy",
          f"{len(state.concurrency)} concurrent piles"], "info",
         f"STORAGE — {len(vis_piles)} modelled stockpiles in this view"
         f"<br>{mass:,.0f} t on ground of {capt:,.0f} t capacity"),
        ("RECLAIM", f"{f_rcl:,.0f}", f"of {c_rcl:,.0f} t/h",
         [l.id for l in sp.reclaim], tone_of(f_rcl, c_rcl),
         "RECLAIM take-off<br>"
         + "<br>".join(f"{l.id}: {state.flows[l.id].tph:,.0f} / "
                       f"{l.capacity_tph:,.0f} t/h"
                       for l in sp.reclaim if l.id in state.flows)),
        ("DELIVERY", f"{f_dlv:,.0f}", f"t/h · demand {dem:,.0f} t/h",
         [d.id for d in dests], tone_of(f_dlv, 0),
         "DELIVERY<br>"
         + "<br>".join(f"{d.id}: demand "
                       f"{sc.demand_tph(d.id, state.bucket):,.0f} t/h"
                       for d in dests)),
    ]
    for (name, x0, x1), (title, val, sub, chips, tone, hov) in zip(
            _SPINE_COLS, blocks):
        _spine_block(sh, title, x0, x1, val, sub, [str(c) for c in chips],
                     tone, hov)

    ycen = (SPINE_Y0 + SPINE_Y1) / 2
    dim = opts.active_flows_only
    for (lx, rx), (cap, flow, links, lbl) in zip(
            [(15.4, 22.6), (38.0, 45.2), (60.6, 67.8), (80.2, 85.4)],
            [(c_arr, f_arr, sp.arrival, "inbound"),
             (c_stk, f_stk, sp.stack, "stack"),
             (c_rcl, f_rcl, sp.reclaim, "reclaim"),
             (c_rte or c_dlv, f_rte or f_dlv, sp.route or sp.deliver,
              "route")]):
        if not links:
            # An absent stage must not be drawn as a zero-flow stream: draw a
            # broken connector and say so.
            sh.line(lx, ycen, rx, ycen, color=sk.hair, width=1.0, dash="dot")
            sh.text((lx + rx) / 2, ycen - 1.3, "not modelled", size=7.2,
                    color=sk.dim)
            continue
        tbc = any(l.evidence.status in ("TBC", "ASSUMED") for l in links)
        _band(sh, lx, rx, ycen, cap, flow, max_cap=max_cap,
              state_tone=("alarm" if cap and flow > cap + 1e-6
                          else "ok" if flow > 0 else "neutral"),
              label=f"{flow:,.0f}/{cap:,.0f}",
              hover=f"{lbl}: {len(links)} link(s)<br>"
                    f"{flow:,.0f} of {cap:,.0f} t/h"
                    + ("<br>evidence DERIVED/TBC on at least one link"
                       if tbc else ""),
              dim=dim and flow <= 0,
              dash="dot" if tbc and opts.show_evidence else None,
              show_flow=opts.show_flows)

    sh.text(X0, SPINE_Y1 + 1.5, "PROCESS STREAM — capacity is band thickness, "
            "flow is band fill", size=8, color=sh.sk.dim, anchor="left")


# ------------------------------------------------------------ site header ----
def _draw_site_header(sh: Sheet, sc, state, site: str, ms, y: float) -> float:
    sk = sh.sk
    sh.rect(X0, y, X1, y + SITE_HDR_H - 1.0, fill=sk.panel, line=sk.rule,
            width=1.0)
    sv = ms.sites.get(site)
    title = sv.title if sv else site
    sh.text(LBL_X0 + 0.8, y + (SITE_HDR_H - 1.0) / 2, title[:64], size=10,
            bold=True, anchor="left", color=sk.ink)
    corr = [c for c in ms.corridors if c.from_site == site]
    if corr:
        txt = " · ".join(
            f"{c.id} {c.capacity_tph:,.0f} t/h · transit {c.transit_h:g} h"
            for c in corr)
        sh.text(X1 - 0.8, y + (SITE_HDR_H - 1.0) / 2, txt, size=7.8,
                anchor="right", color=sk.dim, mono=True)
    return y + SITE_HDR_H


# --------------------------------------------------------------- bay card ----
def _profile_top(frac: float, ybase: float, h: float) -> float:
    if frac < PROFILE_RAMP:
        k = frac / PROFILE_RAMP
    elif frac > 1.0 - PROFILE_RAMP:
        k = (1.0 - frac) / PROFILE_RAMP
    else:
        k = 1.0
    return ybase - h * max(0.0, min(1.0, k))


def _profile_slice(sh: Sheet, f0: float, f1: float, ybase: float, h: float,
                   fill: str, line: str, dash=None) -> None:
    n = 7
    pts = [(_sec_x(f0), ybase)]
    for i in range(n + 1):
        f = f0 + (f1 - f0) * i / n
        pts.append((_sec_x(f), _profile_top(f, ybase, h)))
    pts.append((_sec_x(f1), ybase))
    sh.poly(pts, fill=fill, line=line, width=0.8, dash=dash)


def _pile_segments(pg, vm) -> List[Tuple[float, float, str, str, float]]:
    """(frac_from, frac_to, grade_id, label, mass_t) across the pile."""
    if vm is not None and vm.segments:
        return [(s.frac_from, s.frac_to, s.grade, s.grade or "—", s.mass_t)
                for s in vm.segments]
    if pg is not None and pg.display_grade_components:
        return [(c.frac_from, c.frac_to, c.grade_id,
                 c.grade_label or c.grade_id,
                 (pg.display_mass_t or 0.0) * c.mass_fraction)
                for c in pg.display_grade_components]
    gid = (vm.grade if vm is not None else "") or \
        (pg.display_grade_id if pg is not None else "")
    mass = vm.mass_t if vm is not None else (pg.display_mass_t or 0.0)
    return [(0.0, 1.0, gid, gid or "—", mass)]


def _draw_bay(sh: Sheet, sc, state, bay: Bay, y: float, piles_geo, pile_vm,
              opts, sel: Focus) -> float:
    sk = sh.sk
    n = len(bay.pile_ids)
    card_h = CARD_PAD_TOP + PILE_H * n + CARD_PAD_BOT
    sh.rect(LBL_X0 - 0.6, y, X1, y + card_h, fill=sk.card, line=sk.hair,
            width=1.0, layer="below")

    # ---- bay title + ONE shared repère axis for the whole bay ----------
    sh.text(LBL_X0, y + 2.0, bay.label, size=10, bold=True, anchor="left",
            color=sk.ink)
    sh.text(LBL_X0, y + 4.2, f"{len(bay.pile_ids)} hangar"
            f"{'s' if len(bay.pile_ids) > 1 else ''}", size=7.2,
            anchor="left", color=sk.dim)
    axis_y = y + 4.4
    sh.line(SEC_X0, axis_y, SEC_X1, axis_y, color=sk.rule, width=1.0)
    for r in (0, 250, 500, 750, 1000):
        x = _rep_x(r)
        sh.line(x, axis_y - 0.9, x, axis_y + 0.5, color=sk.rule, width=0.9)
        sh.text(x, axis_y - 2.0, f"R{r}", size=7.0, color=sk.dim, mono=True)

    row_y = y + CARD_PAD_TOP
    for pid in bay.pile_ids:
        pg = piles_geo.get(pid)
        vm = pile_vm.get(pid)
        _draw_pile(sh, sc, state, pid, pg, vm, row_y, opts, sel)
        row_y += PILE_H
    return y + card_h + ROW_GAP


def _draw_pile(sh: Sheet, sc, state, pid: str, pg, vm, y: float, opts,
               sel: Focus) -> None:
    sk = sh.sk
    base = y + PILE_H - 1.6
    h_max = PROFILE_H

    mass = vm.mass_t if vm is not None else (pg.display_mass_t or 0.0
                                            if pg else 0.0)
    cap = vm.capacity_t if vm is not None else ((pg.capacity_t or 0.0)
                                                if pg else 0.0)
    fill = (mass / cap) if cap else 0.0
    h = max(MIN_PROFILE, h_max * min(1.0, fill))

    dim = bool(opts.grade_filter) and (
        (vm.grade if vm else "") not in opts.grade_filter)
    alarm = vm is not None and (vm.below_safety or vm.offspec_hard)
    if opts.alarms_only and not alarm:
        dim = True
    focused = sel.is_space(pid)

    # ---- floor line (full pile footprint, always visible) ---------------
    sh.line(SEC_X0, base, SEC_X1, base, color=sk.rule, width=1.2)

    # ---- grade profile --------------------------------------------------
    segs = _pile_segments(pg, vm)
    dash = "dot" if (pg is not None and (pg.tbc or not pg.modelled)) else None
    for f0, f1, gid, glabel, smass in segs:
        col = grade_color(gid)
        if dim:
            col = sk.hair
        _profile_slice(sh, f0, f1, base, h, col,
                       sk.rule if not focused else sk.info, dash)
        if opts.show_tags and (f1 - f0) > 0.10 and h > 1.8:
            sh.text(_sec_x((f0 + f1) / 2), base - h * 0.5,
                    glabel[:12], size=7.4,
                    color="#ffffff" if not dim else sk.dim)
        if smass and (f1 - f0) > 0.10:
            sh.hover(_sec_x((f0 + f1) / 2), base - h * 0.5,
                     f"<b>{pid}</b> band {format_repere(int(f0 * 1000))}–"
                     f"{format_repere(int(f1 * 1000))}<br>grade {glabel}"
                     f"<br>{smass:,.0f} t")

    # surface line: makes the silhouette read even on a short profile
    if h > MIN_PROFILE:
        top = [(_sec_x(i / 24.0), _profile_top(i / 24.0, base, h))
               for i in range(25)]
        for a, b in zip(top, top[1:]):
            sh.line(a[0], a[1], b[0], b[1],
                    color=sk.info if focused else sk.ink, width=1.1)
    if focused:
        sh.rect(SEC_X0 - 0.4, base - h_max - 0.8, SEC_X1 + 0.4, base + 0.8,
                line=sk.info, width=1.8)

    # ---- capacity ceiling ----------------------------------------------
    sh.line(SEC_X0, base - h_max, SEC_X1, base - h_max, color=sk.hair,
            width=0.9, dash="dash")
    sh.text(SEC_X0 - 0.5, base - h_max, "cap", size=6.4, color=sk.dim,
            anchor="right")

    # ---- label gutter ---------------------------------------------------
    tone = ("alarm" if (vm and vm.offspec_hard) else
            "caution" if (vm and vm.below_safety) else
            "ok" if (vm and vm.active) else "neutral")
    sh.rect(LBL_X0, y + 0.6, LBL_X0 + 0.5, y + PILE_H - 1.0,
            fill=_tone(sk, tone), line="rgba(0,0,0,0)")
    gy = y + 1.7
    sh.text(LBL_X0 + 1.1, gy, pid, size=9.4, bold=True, anchor="left",
            color=sk.ink)
    gy += LINE_STEP
    sh.text(LBL_X0 + 1.1, gy, f"{mass / 1000:,.1f} / {cap / 1000:,.0f} kt",
            size=7.8, anchor="left", color=sk.dim, mono=True)
    gy += LINE_STEP
    sh.text(LBL_X0 + 1.1, gy, f"{fill * 100:.0f} % full  ·  "
            f"{(vm.grade if vm else '') or '—'}", size=7.6, anchor="left",
            color=sk.dim, mono=True)
    if opts.show_flows and vm is not None and (vm.inbound_tph
                                               or vm.outbound_tph):
        gy += LINE_STEP
        sh.text(LBL_X0 + 1.1, gy,
                f"▼{vm.inbound_tph:,.0f}  ▲{vm.outbound_tph:,.0f} t/h",
                size=7.6, anchor="left", color=_tone(sk, "ok"), mono=True)

    # ---- fill gauge in the gutter --------------------------------------
    gx0, gx1 = LBL_X0 + 1.1, LBL_X1 - 0.4
    gby = y + PILE_H - 2.0
    sh.rect(gx0, gby, gx1, gby + 0.9, fill=sk.bg, line=sk.hair, width=0.6)
    if fill > 0:
        sh.rect(gx0, gby, gx0 + (gx1 - gx0) * min(1.0, fill), gby + 0.9,
                fill=_tone(sk, tone), line="rgba(0,0,0,0)")
    if vm is not None and vm.safety_stock_t > 0 and cap:
        sx = gx0 + (gx1 - gx0) * min(1.0, vm.safety_stock_t / cap)
        sh.line(sx, gby - 0.5, sx, gby + 1.4, color=_tone(sk, "caution"),
                width=1.3)

    # ---- concurrency ----------------------------------------------------
    cvm = state.concurrency.get(pid)
    if cvm is not None:
        ctone = {"violation": "alarm", "warning": "caution",
                 "ok": "ok"}[cvm.status]
        for jl, mark in ((cvm.stack_jobs, "STACK"), (cvm.out_jobs, "RECLAIM")):
            for j in jl:
                x0, x1 = _rep_x(j.repere_from), _rep_x(j.repere_to)
                sh.rect(x0, base - h_max - 2.0, x1, base - h_max - 0.9,
                        fill=_tone(sk, ctone), line="rgba(0,0,0,0)")
                sh.text((x0 + x1) / 2, base - h_max - 3.0, mark, size=6.8,
                        color=_tone(sk, ctone))
        sh.hover(_sec_x(0.5), base - h_max - 1.4,
                 f"<b>{pid}</b> CONCURRENT<br>separation {cvm.sep_m:,.0f} m "
                 f"(min {cvm.req_m:,.0f} m ASSUMED)<br>status "
                 f"{cvm.status.upper()}")

    # ---- evidence badges -----------------------------------------------
    if opts.show_evidence:
        badges = []
        if pg is not None and not pg.modelled:
            badges.append(("▢", "display-only — not a solver space", "future"))
        if pg is not None and pg.tbc:
            badges.append(("▲", "TBC", "caution"))
        if vm is not None and vm.evidence_status == "ASSUMED":
            badges.append(("◐", "ASSUMED", "caution"))
        if vm is not None and vm.below_safety:
            badges.append(("!", "below safety stock", "caution"))
        if vm is not None and vm.offspec_hard:
            badges.append(("✕", "hard quality limit", "alarm"))
        bx = BDG_X0
        for glyph, why, tn in badges[:4]:
            sh.text(bx, y + 2.4, glyph, size=9.5, color=_tone(sk, tn))
            sh.hover(bx, y + 2.4, f"<b>{pid}</b> — {why}")
            bx += 1.7

    # ---- hover ----------------------------------------------------------
    if vm is not None:
        q = " · ".join(f"{k} {v:g}" for k, v in vm.quality.items()) or "—"
        sh.hover(_sec_x(0.5), base - h / 2,
                 f"<b>{pid}</b> · {vm.site}<br>{vm.state}<br>"
                 f"{mass:,.0f} / {cap:,.0f} t  ({fill * 100:.0f} %)<br>"
                 f"grade {vm.grade_label}<br>quality {q}<br>"
                 f"in {vm.inbound_tph:,.0f} · out {vm.outbound_tph:,.0f} t/h"
                 f"<br>evidence {vm.evidence_status}")
    elif pg is not None:
        sh.hover(_sec_x(0.5), base - h / 2,
                 f"<b>{pg.pile_id}</b><br>DISPLAY/REFERENCE geometry"
                 f"<br>{pg.display_note or 'not a solver space'}")


# ------------------------------------------------------------ machine line ---
def _draw_track(sh: Sheet, sc, state, geo, tr: Track, y: float, mach_vm,
                mach_geo, jobs_by_machine, opts, piles_geo,
                sel: Focus) -> float:
    sk = sh.sk
    # Row height grows with the gutter content: a line carrying two active
    # machines needs two job lines, and a fixed height would clip them.
    n_job_lines = max(1, sum(len(jobs_by_machine.get(m, ()))
                             for m in tr.machines))
    row_h = max(TRACK_H, 4.0 + 2.0 * n_job_lines + 1.4)
    ty = y + row_h / 2.0
    dash = "dot" if tr.tbc else None

    # rail: two hairlines read as a track, travel limits ticked
    sh.line(SEC_X0, ty - 0.45, SEC_X1, ty - 0.45, color=sk.rule, width=1.0,
            dash=dash)
    sh.line(SEC_X0, ty + 0.45, SEC_X1, ty + 0.45, color=sk.rule, width=1.0,
            dash=dash)
    for x in (SEC_X0, SEC_X1):
        sh.line(x, ty - 1.5, x, ty + 1.5, color=sk.rule, width=1.0)

    serves = (" + ".join(tr.serves)) or "—"
    sh.text(LBL_X0 + 1.1, y + 1.5, tr.rail_id, size=8.6, bold=True,
            anchor="left", color=sk.dim, mono=True)
    sh.text(LBL_X0 + 1.1, y + 3.6, f"serves {serves}", size=7.4,
            anchor="left", color=sk.dim)

    for mid in tr.machines:
        vm = mach_vm.get(mid)
        mg = mach_geo.get(mid)
        display = mg is not None and mg.display and vm is None
        status = "Future" if (mg is not None and mg.future) else (
            "Unavailable" if mid in state.down_machines else
            (vm.status if vm is not None else "Idle"))
        tone = STATE_TONE.get(status, "neutral")
        col = _tone(sk, tone)
        pv = state.machine_pos.get(mid)
        rep = (pv.repere if pv is not None and pv.repere is not None
               else (mg.home_repere if mg is not None else 500))
        cx = _rep_x(rep)
        focused = sel.is_machine(mid)
        running = vm is not None and bool(vm.job_id)
        abnormal = status in ("Unavailable", "Maintenance", "Fault")
        dimmed = ((opts.active_flows_only and not running)
                  or (opts.alarms_only and not abnormal))

        # carriage
        w, hh = 1.4, 1.15
        sh.rect(cx - w, ty - hh, cx + w, ty + hh,
                fill=sk.card if display else sk.panel,
                line=sk.info if focused else col,
                width=1.9 if focused else 1.2,
                dash="dot" if display else None)
        # boom points at the material: stackers discharge up-sheet, reclaimers
        # draw from down-sheet. Direction, not decoration.
        up = tr.kind != "reclaimer"
        tipy = ty - 2.9 if up else ty + 2.9
        sh.line(cx, ty - hh if up else ty + hh, cx, tipy, color=col,
                width=1.5, dash="dot" if display else None)
        sh.poly([(cx - 0.75, tipy), (cx + 0.75, tipy),
                 (cx, tipy + (1.1 if up else -1.1))], fill=col,
                line="rgba(0,0,0,0)")
        # status lamp + tag
        sh.rect(cx - 0.36, ty - 0.5, cx + 0.36, ty + 0.5, fill=col,
                line=sk.card, width=0.6)
        # ONE caption on the canvas, on the side away from the boom: tag,
        # state icon and the rate it is actually running. Anything longer goes
        # to the gutter or the hover — two stacked canvas labels collided at
        # this row height, and a collision is worse than a click.
        side = -1 if up else 1
        jobs = jobs_by_machine.get(mid, [])
        rate = f" {jobs[0].rate_tph:,.0f} t/h" if jobs else ""
        if opts.show_tags and not dimmed:
            sh.text(cx, ty + side * 2.4,
                    f"{mid} {STATE_ICON.get(status, '·')}{rate}",
                    size=7.6, color=sk.ink if not display else sk.dim,
                    mono=True)

        # work zone: the span the machine is actually working this bucket.
        # This is live flow, so the Flow layer governs it.
        if opts.show_flows and pv is not None and pv.work_repere is not None:
            wx0, wx1 = _rep_x(pv.work_repere[0]), _rep_x(pv.work_repere[1])
            wy = ty - 2.0 if up else ty + 1.1
            sh.rect(wx0, wy, wx1, wy + 0.9,
                    fill=sk.info if focused else col,
                    line=sk.info if focused else "rgba(0,0,0,0)",
                    width=1.6 if focused else 0.0)
        # the job itself reads in the gutter, beside the line it belongs to
        gy = y + 5.7
        for j in (jobs if opts.show_flows else ()):
            hit = sel.job and j.id == sel.job
            sh.text(LBL_X0 + 1.1, gy,
                    ("▸ " if hit else "")
                    + f"{j.kind.upper()} → {j.space} "
                    f"{format_repere_range(j.repere_from, j.repere_to)}",
                    size=7.4, color=sk.info if hit else col, anchor="left",
                    mono=True, bold=bool(hit))
            gy += 2.0
        if not jobs and opts.show_tags:
            sh.text(LBL_X0 + 1.1, gy, f"parked {format_repere(rep)}",
                    size=7.2, color=sk.dim, anchor="left", mono=True)

        reach = ", ".join(vm.reachable) if vm is not None else (
            ", ".join(mg.served()) if mg is not None else "—")
        sh.hover(cx, ty, (
            f"<b>{mid}</b> · {tr.rail_id}<br>"
            + ("DISPLAY/REFERENCE — not in the planning model<br>"
               if display else "")
            + f"state {status}<br>position {format_repere(rep)}<br>"
            + (f"work range {format_repere_range(*pv.work_repere)}<br>"
               if pv is not None and pv.work_repere is not None else "")
            + (f"rate {vm.rate_tph:,.0f} / {vm.rate_max_tph:,.0f} t/h<br>"
               if vm is not None else "")
            + f"reach (configured) {reach}"))

    if opts.show_evidence and tr.tbc:
        sh.text(BDG_X0, ty, "▲", size=9, color=_tone(sk, "caution"))
        sh.hover(BDG_X0, ty, f"<b>{tr.rail_id}</b> — line geometry TBC")
    return y + row_h


# ----------------------------------------------------------------- footer ----
def _draw_footer(sh: Sheet, state, geo, y: float, opts) -> None:
    sk = sh.sk
    sh.line(X0, y, X1, y, color=sk.hair, width=1.0)
    sh.text(X0, y + 1.3,
            "SCHEMATIC — NOT TO SCALE · repère R0–R1000 USER-CONFIRMED, "
            "metric calibration ASSUMED (no approved GA/plot plan)",
            size=7.6, color=sk.dim, anchor="left")
    sh.text(X0, y + 2.8,
            "pile height = fill % · horizontal bands = grade at its repère · "
            "dashed ceiling = capacity · gutter tick = safety stock · "
            "band thickness = capacity, band fill = flow",
            size=7.6, color=sk.dim, anchor="left")
    if opts.show_evidence:
        sh.text(X1, y + 1.3,
                "▲ TBC   ◐ ASSUMED   ▢ display-only   ! below safety   "
                "✕ hard limit", size=7.6, color=sk.dim, anchor="right")
    sh.text(X1, y + 2.8,
            "planning / monitoring — no live control · no commands · PLC "
            "interlocks and the independent ACS are outside this layer",
            size=7.6, color=sk.dim, anchor="right")
