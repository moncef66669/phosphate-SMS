"""DCS/HMI-style process mimic.

Architecture (pure, testable):
    build_process_mimic_state(scenario, plan, simulation, basis, bucket)
        -> immutable MimicState (wraps the Stockyard-Monitor view-model,
           adds per-link flows, active job paths, machine rail positions and
           same-pile concurrency status)
    process_mimic_figure(state, geometry, options) -> plotly Figure
    render_process_mimic(...)  -> Streamlit layer (toolbar, details panel)
    render_global_flow(...)    -> page 15 "Global Stockyard Layout & Flow"

The drawing code never mutates the scenario/plan/simulation. Yard geometry
lives in `ui.layout_geometry` (configuration), symbols in `ui.hmi_symbols`.

Machines sit ON their configured rails inside the working corridor between
the pile banks they serve (physical topology per the user-supplied layout;
metric positions schematic). Same-pile concurrent stacking+reclaiming is
rendered with both work zones, the separation buffer and its status
(green/amber/red) — USER-CONFIRMED operating rule, 15-Jul-2026.

This is an engineering planning/monitoring mimic — NOT a certified control
or safety HMI. It issues no commands; PLC interlocks and the independent
ACS are outside this layer.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import plotly.graph_objects as go

from mph_sim.blending import (chevron_enabled_for_space,
                              chevron_recipe_label, chevron_settings)
from mph_sim.coordinates import (format_repere, format_repere_range,
                                 x_interval_to_reperes, x_to_repere)
from mph_sim.domain import PlanResult, Scenario
from mph_sim.flow_config import (disabled_process_lines,
                                 process_line_operating_state)

from . import hmi_symbols as hs
from .layout_geometry import (GEOMETRY_REVISION, RailGeo, SITE_PREFIX,
                              ConveyorGeo, DisplayGradeComponent, PileGeo,
                              ProcessLineGeo, SiteGeometry, geometry_for,
                              site_tag)
from .stockyard_monitor import (MonitorState, PileVM, build_monitor_state,
                                grade_color)

ACS_NOTE = ("planning/monitoring mimic — no live control · no commands · "
            "PLC interlocks & independent ACS/permissives are outside this "
            "layer (ACS status shown only when available: not on file)")

POSITION_NOTE = ("REPÈRE POSITION MODELLED — R0–R1000 convention "
                 "USER-CONFIRMED; metric calibration ASSUMED "
                 "(no approved GA/plot plan)")

#: Suffix marking an asset-selector entry that is drawn geometry only and is
#: NOT in the scenario machine register (no solver jobs, no live state).
DISPLAY_BADGE = "  ·  [DISPLAY/REFERENCE — not in planning model]"

# One shared sidebar hour controls every process-mimic view.  The app mounts
# the widget; renderers only read this key, avoiding duplicate widgets and
# keeping Overview/Global/site drill-downs synchronised.
PROCESS_MIMIC_HOUR_KEY = "process_mimic_hour_v1"
PROCESS_MIMIC_BASIS_KEY = "process_mimic_basis_v1"
PROCESS_MIMIC_INITIAL_STATE = "Initial state"
PROCESS_MIMIC_PLAN_TIMELINE = "Plan timeline"
PROCESS_MIMIC_FINAL_STATE = "Final plan"
PROCESS_MIMIC_SIMULATION_STATE = "Current simulation"

# The Claude-designed sheet uses a fixed inspection frame: the SVG itself
# pans/zooms inside this viewport and progressively reveals labels by zoom.
SVG_MIMIC_VIEWER_HEIGHT = 660
REDESIGN_MIMIC_VIEWER_HEIGHT = 760


def process_mimic_sidebar_states(plan, sim) -> List[str]:
    """Available sidebar states, in the operator's inspection order."""
    states = [PROCESS_MIMIC_INITIAL_STATE]
    if plan is not None:
        states.extend([
            PROCESS_MIMIC_PLAN_TIMELINE,
            PROCESS_MIMIC_FINAL_STATE,
        ])
    if sim is not None:
        states.append(PROCESS_MIMIC_SIMULATION_STATE)
    return states


def process_mimic_display_hour_limit(sc: Scenario) -> int:
    """Whole-hour replay limit for the scenario's full physical horizon.

    The view-model contains native bucket snapshots rather than a continuous
    trajectory.  Exposing whole hours keeps the replay honest for both the
    legacy one-day scenarios and the seven-day hourly S0 plan.
    """
    horizon_h = max(
        0.0, float(sc.sim.horizon_buckets) * float(sc.sim.dt_h))
    return max(0, int(horizon_h + 1e-9))


def process_mimic_bucket_for_hour(sc: Scenario, display_hour: float) -> int:
    """Map a sidebar hour to the nearest stored native bucket boundary."""
    horizon_buckets = max(0, int(sc.sim.horizon_buckets))
    dt_h = max(float(sc.sim.dt_h), 1e-9)
    horizon_h = horizon_buckets * dt_h
    hour = max(0.0, min(float(display_hour), horizon_h))
    nearest_bucket = int(hour / dt_h + 0.5)
    return max(0, min(horizon_buckets, nearest_bucket))


def mimic_figure_key(base_key: str, view: str, nonce: int) -> str:
    """Return a browser-widget key that changes with display geometry."""
    return f"{base_key}_fig_{view}_{GEOMETRY_REVISION}_{nonce}"


def asset_status_legend_html() -> str:
    """Compact, accessible state code shared by belts and machine lamps."""
    chips = []
    for key in ("ACTIVE", "IDLE", "UNAVAILABLE", "FUTURE"):
        style = hs.ASSET_STATUS_STYLES[key]
        pattern = " is-future" if style["dash"] else ""
        chips.append(
            f'<span class="asset-state-chip{pattern}">'
            f'<i style="color:{style["color"]}">{style["icon"]}</i>'
            f'<b>{style["label"]}</b></span>')
    return (
        '<div class="asset-state-legend">'
        '<span class="asset-state-title">Conveyors · stackers · reclaimers</span>'
        + "".join(chips)
        + '<span class="asset-state-note">belt/chevrons = conveyor state · '
          'lamp = stacker/reclaimer state</span></div>'
    )


def _display_fill_pct(pg: PileGeo) -> float:
    """Clamped fill for reference piles with a separately supplied t0 mass."""
    if pg.display_mass_t is None or pg.capacity_t is None or pg.capacity_t <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * pg.display_mass_t / pg.capacity_t))


def _display_component_repere_range(
        pg: PileGeo,
        component: DisplayGradeComponent) -> Tuple[int, int]:
    """Return the component's schematic R0–R1000 display range.

    The range follows the left-to-right fill drawn by the mimic. It is a
    display location, not a surveyed physical coordinate.
    """
    occupied_fraction = _display_fill_pct(pg) / 100.0
    r0 = round(1000 * occupied_fraction * component.frac_from)
    r1 = round(1000 * occupied_fraction * component.frac_to)
    return max(0, min(1000, r0)), max(0, min(1000, r1))


def _display_component_hover(
        pg: PileGeo,
        component: DisplayGradeComponent) -> str:
    """Concise reference-pile hover: profile, tonnes and display location."""
    component_mass = (
        pg.display_mass_t * component.mass_fraction
        if pg.display_mass_t is not None else None)
    r0, r1 = _display_component_repere_range(pg, component)
    grade_name = component.grade_name or component.grade_label \
        or component.grade_id
    tonnage = (f"{component_mass:,.0f} t"
               if component_mass is not None else "not loaded")
    return (
        f"<b>{grade_name}</b><br>"
        f"Tonnage: {tonnage}<br>"
        f"Location: {pg.canvas_label or pg.pile_id} · "
        f"{format_repere_range(r0, r1)}"
    )


def _short_grade_token(grade_id: str) -> str:
    """Compact grade token used by labels and two-line hover summaries."""
    return {
        "RAW-BT": "BT",
        "RAW-BT-HIGH-CD": "H-Cd",
        "RAW-BT-LOW-CD": "L-Cd",
    }.get(grade_id, grade_id)


def _compact_mass_text(mass_t: float) -> str:
    """Short, stable quantity for labels inside narrow stockpile bands."""
    if mass_t >= 1000:
        mass_kt = mass_t / 1000.0
        decimals = 0 if abs(mass_kt - round(mass_kt)) < 0.05 else 1
        return f"{mass_kt:,.{decimals}f} kt"
    return f"{mass_t:,.0f} t"


def _profile_band_text(grade_id: str, mass_t: float) -> str:
    """Visible profile identity and tonnes; every other detail stays off-canvas."""
    return f"<b>{_short_grade_token(grade_id)}</b> · {_compact_mass_text(mass_t)}"


def _compact_pile_quantity_text(mass_t: Optional[float],
                                grade_ids: List[str]) -> str:
    """Minimal mass/grade label inside a single-profile stockpile."""
    parts = []
    if mass_t is not None:
        parts.append(_compact_mass_text(mass_t))
    tokens = [_short_grade_token(gid) for gid in grade_ids if gid]
    if tokens:
        parts.append("/".join(tokens))
    return " · ".join(parts)


def _banded_pile_quantity_text(mass_t: Optional[float],
                               fill_pct: Optional[float]) -> str:
    """Total mass/fill label inside a multi-band stockpile.

    The per-band grade tonnages are drawn inside their own bands, so the pile
    name is rendered separately above the lane and every evidence detail stays
    in the hover text and the asset-details panel.
    """
    parts = []
    if mass_t is not None:
        parts.append(_compact_mass_text(mass_t))
    if fill_pct is not None:
        parts.append(f"{fill_pct:.0f}%")
    return " · ".join(parts)


def _pile_name_annotation(fig, pg, compact: bool, *,
                          prefix: str = "", suffix: str = "",
                          color: str = hs.LABEL):
    """Draw the stockpile identifier above its outline as unboxed plain text."""
    hs._ann(
        fig, pg.x0 + (0.15 if compact else 0.25),
        pg.y1 + (0.42 if compact else 0.62),
        f"{prefix}{pg.canvas_label or pg.pile_id}{suffix}",
        size=6.5 if compact else 8.5,
        xanchor="left", yanchor="bottom", color=color)


# ================================ view-model =====================================
@dataclass
class FlowVM:
    link_id: str
    tph: float
    cap_tph: float
    running: bool
    tripped: bool
    grade: str = ""

    @property
    def utilisation_pct(self) -> float:
        return 100.0 * self.tph / self.cap_tph if self.cap_tph else 0.0


@dataclass
class JobVM:
    id: str
    machine: str
    kind: str
    space: str
    dest_id: str
    grade: str
    rate_tph: float
    path: List[str]
    x0: float = 0.0
    x1: float = 0.0
    repere_from: int = 0
    repere_to: int = 1000
    tonnage_t: float = 0.0

    @property
    def repere_label(self) -> str:
        return format_repere_range(self.repere_from, self.repere_to)


@dataclass
class MachinePosVM:
    x_norm: Optional[float]           # 0..1 along the served pile, or None
    note: str = POSITION_NOTE
    travelling: bool = False
    work_frac: Optional[Tuple[float, float]] = None   # job StartX..StopX
    x_m: Optional[float] = None
    repere: Optional[int] = None
    space: str = ""
    work_repere: Optional[Tuple[int, int]] = None


@dataclass
class ConcurrencyVM:
    """Same-pile simultaneous stacking + reclaiming at the selected bucket."""
    space: str
    stack_jobs: List[JobVM]
    out_jobs: List[JobVM]
    sep_m: float                      # gap between the two work zones [m]
    req_m: float                      # configured minimum separation [m]
    buffer_m: float                   # work-zone buffer each side [m]
    status: str                       # ok | warning | violation
    allowed: Optional[bool]           # site policy (None = TBC)
    evidence: str = ""


@dataclass
class MimicState:
    monitor: MonitorState
    basis: str
    bucket: int
    timestamp: str
    flows: Dict[str, FlowVM]
    jobs: List[JobVM]
    machine_pos: Dict[str, MachinePosVM]
    down_machines: frozenset = frozenset()   # failure windows at this bucket
    space_len: Dict[str, float] = field(default_factory=dict)
    space_bounds: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    space_repere_ends: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    concurrency: Dict[str, ConcurrencyVM] = field(default_factory=dict)
    chevron_enabled: bool = False
    chevron_passes_per_cycle: int = 0
    chevron_recipe_labels: Dict[str, str] = field(default_factory=dict)
    chevron_traversal_labels: Dict[str, str] = field(default_factory=dict)
    blend_location_status: Dict[str, str] = field(default_factory=dict)
    blend_location_labels: Dict[str, str] = field(default_factory=dict)
    blend_campaigns: Dict[str, Dict[str, object]] = field(default_factory=dict)
    acs_note: str = ACS_NOTE


def _space_len(sc: Scenario, sid: str) -> float:
    sp = next((s for s in sc.spaces if s.id == sid), None)
    if sp is None or sp.x_to <= sp.x_from:
        return 300.0
    return sp.x_to - sp.x_from


def build_process_mimic_state(sc: Scenario, plan: Optional[PlanResult] = None,
                              sim=None, basis: str = "auto",
                              bucket: Optional[int] = None) -> MimicState:
    """Immutable HMI snapshot. Never mutates its inputs."""
    ms = build_monitor_state(sc, plan, sim, basis=basis, bucket=bucket)
    b, basis = ms.bucket, ms.basis
    links = {l.id: l for l in sc.links}

    # outage windows at the selected bucket (simulator history, additive)
    down_machines: set = set()
    tripped_links: set = set()
    if basis == "sim" and sim is not None:
        ds = getattr(sim, "down_series", [])
        if b < len(ds):
            down_machines = set(ds[b])
        ls = getattr(sim, "link_down_series", [])
        if b < len(ls):
            tripped_links = set(ls[b])

    # ---- active jobs at the selected bucket -------------------------------------
    jobs: List[JobVM] = []
    if basis == "plan" and plan is not None:
        for j in plan.jobs:
            if j.start_bucket <= b <= j.end_bucket and \
                    j.status not in ("CANCELLED", "REJECTED"):
                sp = sc.space(j.space)
                r0, r1 = x_interval_to_reperes(sp, j.start_x, j.stop_x)
                jobs.append(JobVM(j.id, j.machine, j.kind, j.space, j.dest_id,
                                  j.grade, j.rate_tph, list(j.path),
                                  j.start_x, j.stop_x, r0, r1, j.tonnage_t))
    elif basis == "sim" and sim is not None:
        for a in sim.activity_log:
            if a["bucket"] != b:
                continue
            j = sim.jobs.get(str(a["job"]))
            sp = sc.space(str(a["space"]))
            x0 = j.start_x if j else sp.x_from
            x1 = j.stop_x if j else sp.x_to
            r0, r1 = x_interval_to_reperes(sp, x0, x1)
            jobs.append(JobVM(str(a["job"]), str(a["machine"]),
                              str(a["kind"]), str(a["space"]),
                              str(a["dest_id"]), str(a["grade"]),
                              float(a["rate_tph"]),
                              list(j.path) if j is not None else [],
                              x0, x1, r0, r1,
                              j.tonnage_t if j is not None else
                              float(a.get("moved_t", 0.0))))

    # ---- per-link flows ----------------------------------------------------------
    flows: Dict[str, FlowVM] = {}
    for lid, l in links.items():
        tph, grade = 0.0, ""
        if basis == "sim" and sim is not None:
            ser = sim.link_load.get(lid, [])
            if b < len(ser):
                tph = float(ser[b])
        elif basis == "plan":
            for j in jobs:
                if lid in j.path:
                    tph += j.rate_tph
                    grade = grade or j.grade
        if basis == "sim" and tph > 1e-6:
            grades = [j.grade for j in jobs if lid in j.path and j.grade]
            grade = grades[0] if grades else ""
        # An unavailable optional/future route is a configured lifecycle
        # state, NOT a tripped conveyor. In particular, PAP-2 remains visible
        # as FUTURE until selected in Configure and must not raise a red alarm.
        lifecycle_off = (
            not l.available
            and getattr(l, "lifecycle", "EXISTING")
            in {"OPTIONAL_PROPOSED", "FUTURE"}
        )
        tripped = ((not l.available) and not lifecycle_off) \
            or lid in tripped_links
        flows[lid] = FlowVM(lid, tph, l.capacity_tph,
                            running=(tph > 1e-6 and not tripped),
                            tripped=tripped, grade=grade)

    # ---- machine positions along their pile/rail ---------------------------------
    mpos: Dict[str, MachinePosVM] = {}
    job_by_machine = {j.machine: j for j in jobs}
    for m in sc.machines:
        j = job_by_machine.get(m.id)
        pos: Optional[float]
        if basis == "sim" and sim is not None:
            pos = sim.machine_pos.get(m.id, m.position_x)
        elif basis == "plan" and j is not None:
            pos = (j.x0 + j.x1) / 2 if (j.x0 or j.x1) else m.position_x
        else:
            pos = m.position_x
        ref = j.space if j is not None else (
            m.reachable_spaces[0] if m.reachable_spaces else "")
        sp = next((s for s in sc.spaces if s.id == ref), None)
        ln = _space_len(sc, ref) if ref else 300.0
        x_origin = sp.x_from if sp is not None else 0.0
        x_norm = (max(0.0, min(1.0, (pos - x_origin) / ln))
                  if pos is not None else None)
        wf = None
        wr = None
        if j is not None and (j.x0 or j.x1) and j.x1 > j.x0:
            wf = (max(0.0, min(1.0, (j.x0 - x_origin) / ln)),
                  max(0.0, min(1.0, (j.x1 - x_origin) / ln)))
            wr = (j.repere_from, j.repere_to)
        travelling = False
        if basis == "plan" and plan is not None and j is None:
            travelling = any(jj.machine == m.id and jj.start_bucket == b + 1
                             for jj in plan.jobs
                             if jj.status not in ("CANCELLED", "REJECTED"))
        repere = (int(x_to_repere(sp, pos))
                  if sp is not None and pos is not None else None)
        mpos[m.id] = MachinePosVM(
            x_norm, POSITION_NOTE, travelling, wf,
            x_m=pos, repere=repere, space=ref, work_repere=wr)

    # ---- same-pile concurrency status ---------------------------------------------
    site_by_id = {s.id: s for s in sc.sites}
    space_site = {s.id: s.site for s in sc.spaces}
    by_space: Dict[str, List[JobVM]] = {}
    for j in jobs:
        by_space.setdefault(j.space, []).append(j)
    concurrency: Dict[str, ConcurrencyVM] = {}
    for spid, js in by_space.items():
        st_jobs = [j for j in js if j.kind == "stack"]
        out_jobs = [j for j in js if j.kind in ("reclaim", "dispatch")]
        if not st_jobs or not out_jobs:
            continue
        site = site_by_id.get(space_site.get(spid, ""), None)
        min_sep = getattr(site, "min_machine_separation_m", 50.0)
        buf = getattr(site, "work_zone_buffer_m", 25.0)
        allowed = getattr(site, "same_pile_concurrency_allowed", None)
        sep = min(j.x0 for j in st_jobs) - max(j.x1 for j in out_jobs)
        if sep < min_sep:
            status = "violation"
        elif sep < min_sep + 2 * buf:
            status = "warning"
        else:
            status = "ok"
        concurrency[spid] = ConcurrencyVM(
            spid, st_jobs, out_jobs, sep_m=sep, req_m=min_sep, buffer_m=buf,
            status=status, allowed=allowed,
            evidence=getattr(site, "concurrency_evidence", ""))

    ts = sc.sim.bucket_dt(b).strftime("%d %b %Y %H:%M")
    chevron_cfg = chevron_settings(sc)
    chevron_labels = {
        space.id: chevron_recipe_label(sc, space.id)
        for space in sc.spaces
        if chevron_enabled_for_space(sc, space.id)
        or str(chevron_cfg["campaign_states_by_space"].get(
            space.id, {}).get("status", "")) in {"BUILDING", "COMPLETE"}
    }
    # Optimized compositions are solved on a private planning copy.  Surface
    # the resulting matrix on the plan mimic without mutating the operator's
    # editable scenario or fabricating it before a plan exists.
    plan_blend_campaigns = (
        deepcopy(getattr(plan, "blend_campaigns", {}))
        if plan is not None else {})
    traversal_estimates_by_space: Dict[str, list] = {}
    for job in getattr(plan, "jobs", []) or []:
        if str(getattr(job, "method", "")).lower() != "chevron":
            continue
        target = str(getattr(job, "target_space", "") or getattr(job, "space", ""))
        traversal_estimates_by_space.setdefault(target, []).append(
            dict(getattr(job, "chevron_pass_estimate", {}) or {}))
    traversal_labels = {}
    for target, estimates in traversal_estimates_by_space.items():
        if all(estimate.get("status") == "DERIVED_FROM_FLOW" and
               estimate.get("equivalent_passes") is not None for estimate in estimates):
            total_passes = sum(float(estimate["equivalent_passes"]) for estimate in estimates)
            traversal_labels[target] = f"estimated traversals {total_passes:,.1f}"
        elif chevron_cfg.get("quantity_mode") == "DEMAND_DRIVEN" or any(estimates):
            traversal_labels[target] = "traversal estimate unavailable"
    if plan is not None:
        for space_id, campaign in plan_blend_campaigns.items():
            composition = campaign.get("composition", []) \
                if isinstance(campaign, dict) else []
            label = " + ".join(
                f"{row.get('grade_id', '')} "
                f"{100 * float(row.get('target_fraction', 0.0) or 0.0):.1f}%"
                for row in composition if isinstance(row, dict))
            if label:
                chevron_labels[str(space_id)] = label
    # Identify every configured physical blend point even before it is enabled
    # or contains material.  The monitor must answer "where is blending done?"
    # independently of whether a campaign happens to be active in this bucket.
    configured_blend_spaces = set()
    for key in ("recipes", "share_bounds_by_space", "mode_by_space",
                "campaign_states_by_space"):
        configured_blend_spaces.update(
            str(space_id) for space_id in dict(
                chevron_cfg.get(key, {}) or {}).keys())
    configured_blend_spaces.update(
        str(space_id) for space_id in chevron_cfg["enabled_spaces"])
    configured_blend_spaces.update(str(space_id)
                                   for space_id in plan_blend_campaigns)
    valid_space_ids = {space.id for space in sc.spaces}
    configured_blend_spaces &= valid_space_ids
    blend_location_labels: Dict[str, str] = {}
    blend_location_status: Dict[str, str] = {}
    for space_id in sorted(configured_blend_spaces):
        blend_location_labels[space_id] = (
            chevron_labels.get(space_id)
            or chevron_recipe_label(sc, space_id)
            or "Recipe not set")
        plan_campaign = plan_blend_campaigns.get(space_id, {})
        campaign_state = chevron_cfg["campaign_states_by_space"].get(
            space_id, {})
        plan_status = str(
            plan_campaign.get("campaign_status", "")
            or plan_campaign.get("decision_status", "")
            if isinstance(plan_campaign, dict) else "").strip().upper()
        physical_status = str(
            campaign_state.get("status", "")
            if isinstance(campaign_state, dict) else "").strip().upper()
        if plan_status:
            status = plan_status
        elif physical_status and physical_status != "NOT_STARTED":
            status = physical_status
        elif space_id in chevron_cfg["enabled_spaces"]:
            status = "ENABLED · AWAITING PLAN"
        else:
            status = "CONFIGURED · DISABLED"
        blend_location_status[space_id] = status
    return MimicState(monitor=ms, basis=basis, bucket=b, timestamp=ts,
                      flows=flows, jobs=jobs, machine_pos=mpos,
                      down_machines=frozenset(down_machines),
                      space_len={s.id: _space_len(sc, s.id)
                                 for s in sc.spaces},
                      space_bounds={s.id: (s.x_from, s.x_to)
                                    for s in sc.spaces},
                      space_repere_ends={
                          s.id: (getattr(s, "repere_at_x_from", 0),
                                 getattr(s, "repere_at_x_to", 1000))
                          for s in sc.spaces},
                      concurrency=concurrency,
                      chevron_enabled=bool(
                          chevron_cfg["enabled"] or chevron_labels),
                      chevron_passes_per_cycle=int(
                          chevron_cfg["passes_per_cycle"]),
                      chevron_recipe_labels=chevron_labels,
                      chevron_traversal_labels=traversal_labels,
                      blend_location_status=blend_location_status,
                      blend_location_labels=blend_location_labels,
                      blend_campaigns=plan_blend_campaigns)


# ================================ figure =========================================
@dataclass
class MimicOpts:
    show_flows: bool = True
    show_tags: bool = True
    show_evidence: bool = True
    alarms_only: bool = False
    active_flows_only: bool = False   # dim idle conveyors/routes
    grade_filter: List[str] = field(default_factory=list)
    selected_job: str = ""
    selected_asset: str = ""
    phase: int = 0


def machine_draw_pos(rail: RailGeo, pile_geo: Optional[PileGeo],
                     x_norm: Optional[float], parking_frac: float = 0.04,
                     scale: float = 1.0):
    """(gx, gy, tipx, tipy, boom_side): the gantry stays ON its horizontal
    rail inside the working corridor (clamped to the travel limits); the
    boom tip maps x_norm (0..1 along the served pile) INTO the flanking
    pile band — up (+1) or down (-1) depending on which side the pile is."""
    lo = rail.x0 + (rail.x1 - rail.x0) * rail.travel[0]
    hi = rail.x0 + (rail.x1 - rail.x0) * rail.travel[1]
    if pile_geo is not None and x_norm is not None:
        gx = pile_geo.x0 + (pile_geo.x1 - pile_geo.x0) * \
            min(max(x_norm, 0.0), 1.0)
    else:
        f = min(max(parking_frac, rail.travel[0]), rail.travel[1])
        gx = rail.x0 + (rail.x1 - rail.x0) * f
    gx = min(max(gx, lo), hi)
    gy = rail.y
    if pile_geo is not None:
        if pile_geo.y0 >= rail.y:          # pile band above the rail
            side = 1
            tipy = pile_geo.y0 + (pile_geo.y1 - pile_geo.y0) * 0.35
        else:                               # pile band below the rail
            side = -1
            tipy = pile_geo.y1 - (pile_geo.y1 - pile_geo.y0) * 0.35
        tipx = min(max(gx, pile_geo.x0 + 0.4), pile_geo.x1 - 0.4)
    else:
        side = 1
        tipx, tipy = gx + 1.8 * scale, gy + 0.6 * scale
    return gx, gy, tipx, tipy, side


def _pile_vm(ms: MonitorState, pid: str) -> Optional[PileVM]:
    for sv in ms.sites.values():
        for p in sv.piles:
            if p.id == pid:
                return p
    return None


def _machine_vm(ms: MonitorState, mid: str):
    for sv in ms.sites.values():
        for m in sv.machines:
            if m.id == mid:
                return m
    return None


def _conv_state(st: MimicState, cg: ConveyorGeo,
                opts: MimicOpts) -> Tuple[str, Optional[float],
                                          Optional[float], str, bool]:
    """(state, tph, cap, grade_col, selected) resolved from bound links, or
    from the bound MACHINE's active jobs for per-machine routes (e.g. the
    documented Louta reclaim routes 140LG-CV-03/RL1 · 140LG-CV-04/RL2)."""
    if cg.future:
        return "future", None, None, "", False
    if not cg.planning_enabled:
        return "idle", None, None, "", False
    if cg.machine_id:
        mjobs = [j for j in st.jobs if j.machine == cg.machine_id]
        tph = sum(j.rate_tph for j in mjobs)
        vm = _machine_vm(st.monitor, cg.machine_id)
        cap = vm.rate_max_tph if vm is not None else None
        tripped = (cg.machine_id in st.down_machines
                   or (vm is not None and vm.status == "Unavailable"))
        sel_job = next((j for j in st.jobs if j.id == opts.selected_job), None)
        selected = (opts.selected_asset in (cg.cid, cg.machine_id)
                    or bool(sel_job and sel_job.machine == cg.machine_id))
        state = "tripped" if tripped else ("running" if tph > 1e-6 else "idle")
        grade = next((j.grade for j in mjobs if j.grade), "")
        return state, tph, cap, (grade_color(grade) if grade else ""), selected
    fs = [st.flows[li] for li in cg.link_ids if li in st.flows]
    if not fs:
        return "idle", None, None, "", False
    tph = sum(f.tph for f in fs)
    # a single bound link has a real capacity; for grouped per-pile links
    # (e.g. T01/T02 reception, reclaim collection) the sum is NOT one
    # physical belt capacity — show plain flow, no misleading utilisation
    cap = fs[0].cap_tph if len(fs) == 1 else None
    tripped = all(f.tripped for f in fs)
    sel_job = next((j for j in st.jobs if j.id == opts.selected_job), None)
    selected = (
        opts.selected_asset == cg.cid
        or bool(sel_job and any(li in sel_job.path for li in cg.link_ids))
    )
    state = "tripped" if tripped else ("running" if tph > 1e-6 else "idle")
    grade = next((f.grade for f in fs if f.grade), "")
    return state, tph, cap, (grade_color(grade) if grade else ""), selected


def _repere_ruler(fig, pg: PileGeo, compact: bool,
                   repere_ends: Tuple[int, int] = (0, 1000)):
    """Light pile ruler: five major marks over the 1,000 repère intervals."""
    r_at_left, r_at_right = repere_ends
    ticks = (0.0, 0.5, 1.0) if compact else (0.0, 0.25, 0.5, 0.75, 1.0)
    width = pg.x1 - pg.x0
    y0 = pg.y0 - (0.16 if compact else 0.23)
    for frac in ticks:
        xx = pg.x0 + width * frac
        repere = int(round(r_at_left + frac * (r_at_right - r_at_left)))
        fig.add_shape(type="line", x0=xx, x1=xx,
                      y0=pg.y0 - 0.02, y1=pg.y0 + 0.14,
                      line=dict(color=hs.STATIC_LIGHT, width=0.7))
        hs._ann(fig, xx, y0, format_repere(repere),
                size=4.8 if compact else 6.5,
                color=hs.STATIC_LIGHT,
                bg="rgba(236,239,241,0.68)")


def _concurrency_overlay(fig, pg: PileGeo, cvm: ConcurrencyVM, L: float,
                         compact: bool, x_from: float = 0.0):
    """Both work zones + safety buffer for same-pile stack & reclaim."""
    col = {"ok": hs.RUN_GREEN, "warning": hs.WARN_AMBER,
           "violation": hs.FAULT_RED}[cvm.status]
    w = pg.x1 - pg.x0

    def fx(x_m: float) -> float:
        return pg.x0 + w * min(
            max((x_m - x_from) / max(L, 1.0), 0.0), 1.0)
    # reclaim zone — lower strip
    r0 = fx(min(j.x0 for j in cvm.out_jobs))
    r1 = fx(max(j.x1 for j in cvm.out_jobs))
    fig.add_shape(type="rect", x0=r0, x1=r1, y0=pg.y0 + 0.15,
                  y1=pg.y0 + 0.32 * (pg.y1 - pg.y0),
                  line=dict(color=col, width=1.6),
                  fillcolor="rgba(255,255,255,0.25)")
    # stacking zone — upper strip
    s0 = fx(min(j.x0 for j in cvm.stack_jobs))
    s1 = fx(max(j.x1 for j in cvm.stack_jobs))
    fig.add_shape(type="rect", x0=s0, x1=s1,
                  y0=pg.y1 - 0.32 * (pg.y1 - pg.y0), y1=pg.y1 - 0.15,
                  line=dict(color=col, width=1.6),
                  fillcolor="rgba(255,255,255,0.25)")
    # protected buffer band between the zones (hatched = no-entry)
    b0, b1 = min(r1, s0), max(r1, s0)
    if b1 - b0 > 0.3:
        yc0, yc1 = pg.y0 + 0.34 * (pg.y1 - pg.y0), \
            pg.y1 - 0.34 * (pg.y1 - pg.y0)
        fig.add_shape(type="rect", x0=b0, x1=b1, y0=yc0, y1=yc1,
                      line=dict(color=col, width=1.0, dash="dot"),
                      fillcolor="rgba(160,168,176,0.18)")
        n = max(int((b1 - b0) / 1.2), 2)
        for i in range(n):                 # hatching: protected/no-entry
            xx = b0 + (b1 - b0) * i / max(n - 1, 1)
            fig.add_shape(type="line", x0=xx, x1=min(xx + 0.8, b1),
                          y0=yc0, y1=yc1,
                          line=dict(color=col, width=0.7))
    icon = {"ok": "✓", "warning": "⚠", "violation": "⛔"}[cvm.status]
    hs._ann(fig, (pg.x0 + pg.x1) / 2, pg.y1 + (0.7 if compact else 0.9),
            f"{icon} concurrent stack+reclaim · sep {cvm.sep_m:,.0f} m "
            f"(min {cvm.req_m:,.0f} m + buffer {cvm.buffer_m:,.0f} m — "
            "ASSUMED)", size=7.5 if compact else 8.5, color=col,
            bg="rgba(236,239,241,0.9)")
    if not compact:
        hs._ann(fig, (r0 + r1) / 2, pg.y0 + 0.16 * (pg.y1 - pg.y0),
                " · ".join(f"{j.id} {j.repere_label}"
                           for j in cvm.out_jobs) + " ◀",
                size=7.5, color=hs.LABEL)
        hs._ann(fig, (s0 + s1) / 2, pg.y1 - 0.16 * (pg.y1 - pg.y0),
                "▶ " + " · ".join(f"{j.id} {j.repere_label}"
                                  for j in cvm.stack_jobs),
                size=7.5, color=hs.LABEL)


class _BatchedFigure:
    """Collect Plotly primitives and validate the layout only once.

    A composite yard contains hundreds of shapes and annotations. Calling
    ``go.Figure.add_shape``/``add_annotation`` for every primitive makes
    Plotly repeatedly copy and validate the growing layout tuple, which is
    quadratic and left the post-solve overview apparently blank for many
    seconds. The HMI drawing helpers only need this small append/update API,
    so defer construction of the real Figure until the drawing is complete.
    """

    def __init__(self) -> None:
        self._traces: List[object] = []
        self._shapes: List[Dict] = []
        self._annotations: List[Dict] = []
        self._layout: Dict = {}
        self._xaxis: Dict = {}
        self._yaxis: Dict = {}

    def add_trace(self, trace):
        self._traces.append(trace)
        return self

    def add_shape(self, **shape):
        self._shapes.append(shape)
        return self

    def add_annotation(self, **annotation):
        self._annotations.append(annotation)
        return self

    def update_xaxes(self, **values):
        self._xaxis.update(values)
        return self

    def update_yaxes(self, **values):
        self._yaxis.update(values)
        return self

    def update_layout(self, **values):
        self._layout.update(values)
        return self

    def build(self) -> go.Figure:
        layout = dict(self._layout)
        layout["shapes"] = self._shapes
        layout["annotations"] = self._annotations
        layout["xaxis"] = self._xaxis
        layout["yaxis"] = self._yaxis
        return go.Figure(data=self._traces, layout=layout)


def process_mimic_figure(st: MimicState, geo: SiteGeometry,
                         opts: Optional[MimicOpts] = None) -> go.Figure:
    opts = opts or MimicOpts()
    ms = st.monitor
    fig = _BatchedFigure()
    compact = geo.site_id in ("CHAIN", "GLOBAL")

    # ---- zones -------------------------------------------------------------------
    for z in geo.zones:
        hs.zone_box(fig, z.x0, z.y0, z.x1, z.y1, z.label, z.style, z.tbc)

    # ---- selected machine -> configured reachability (never visual distance) ----
    sel_mvm = _machine_vm(ms, opts.selected_asset) if opts.selected_asset \
        else None
    reach = set(sel_mvm.reachable) if sel_mvm is not None else set()
    sel_mg = next((m for m in geo.machines
                   if m.machine_id == opts.selected_asset), None)
    sel_rail = sel_mg.rail_id if sel_mg is not None else ""

    # ---- stockpile lanes ------------------------------------------------------------
    hover_x, hover_y, hover_t = [], [], []
    sel_job = next((j for j in st.jobs if j.id == opts.selected_job), None)
    for pg in geo.piles:
        if not pg.modelled:
            display_fill = _display_fill_pct(pg)
            display_segments = None
            if pg.display_grade_components:
                display_segments = [
                    (
                        component.frac_from,
                        component.frac_to,
                        grade_color(component.grade_id)
                        if (not opts.grade_filter or
                            component.grade_id in opts.grade_filter)
                        else "#dfe4e7",
                    )
                    for component in pg.display_grade_components
                ]
            hs.stock_lane(fig, pg.x0, pg.y0, pg.x1, pg.y1, display_fill,
                          grade_color(pg.display_grade_id),
                          hs.TBC_GREY, "dash", 1.2,
                          segments=display_segments)
            _repere_ruler(fig, pg, compact)
            if opts.show_tags and pg.label:
                _pile_name_annotation(fig, pg, compact, color=hs.STATIC_LIGHT)
                grade_ids = (
                    [component.grade_id
                     for component in pg.display_grade_components]
                    if pg.display_grade_components else
                    ([pg.display_grade_id] if pg.display_grade_id else []))
                pile_text = (
                    _banded_pile_quantity_text(
                        pg.display_mass_t,
                        display_fill if pg.capacity_t is not None else None)
                    if pg.display_grade_components and pg.label_with_total else
                    "" if pg.display_grade_components else
                    _compact_pile_quantity_text(
                        pg.display_mass_t, grade_ids))
                if pile_text:
                    hs._ann(fig, pg.x0 + 0.4,
                            pg.y1 - (0.22 if compact else 0.45),
                            pile_text,
                            size=6.2 if compact else 8,
                            xanchor="left", color=hs.STATIC_LIGHT,
                            bg="rgba(246,248,249,0.82)")
                if pg.display_grade_components and \
                        pg.display_mass_t is not None:
                    width = pg.x1 - pg.x0
                    for component in pg.display_grade_components:
                        component_mass = (
                            pg.display_mass_t * component.mass_fraction)
                        band_x = (
                            pg.x0 + width * display_fill / 100.0 *
                            (component.frac_from + component.frac_to) / 2.0)
                        hs._ann(
                            fig, band_x,
                            pg.y0 + (0.22 if compact else 0.65),
                            _profile_band_text(
                                component.grade_id, component_mass),
                            size=5.8 if compact else 8,
                            color=hs.LABEL,
                            bg="rgba(246,248,249,0.74)")
            if pg.display_grade_components:
                width = pg.x1 - pg.x0
                for component in pg.display_grade_components:
                    hover_x.append(
                        pg.x0 + width * display_fill / 100.0 *
                        (component.frac_from + component.frac_to) / 2.0)
                    hover_y.append((pg.y0 + pg.y1) / 2)
                    hover_t.append(_display_component_hover(pg, component))
            else:
                hover_x.append((pg.x0 + pg.x1) / 2)
                hover_y.append((pg.y0 + pg.y1) / 2)
                pile_quantity = (
                    f"{pg.display_mass_t:,.0f}"
                    + (f" / {pg.capacity_t:,.0f}"
                       if pg.capacity_t is not None else "")
                    + f" t · {display_fill:.0f}%"
                    if pg.display_mass_t is not None else
                    "capacity / contents not loaded")
                # keep the drawing-supported name reachable: the canvas label
                # is the tag, so the source name belongs in the hover
                drawing_name = (f" · {pg.label}"
                                if pg.label and pg.label != pg.pile_id
                                else "")
                hover_t.append(
                    f"<b>{pg.pile_id}</b>{drawing_name}<br>{pile_quantity}")
            continue
        p = _pile_vm(ms, pg.pile_id)
        if p is None:
            continue
        if p.offspec_hard:
            bcol, bw = hs.FAULT_RED, 2.6
        elif p.below_safety:
            bcol, bw = hs.WARN_AMBER, 2.6
        elif p.active:
            bcol, bw = hs.RUN_GREEN, 2.2
        else:
            bcol, bw = hs.STATIC, 1.2
        healthy = not (p.offspec_hard or p.below_safety)
        if opts.alarms_only and healthy:
            bcol, bw = "#c3cad0", 1.0
        dash = "dash" if (pg.tbc or p.evidence_status in ("ASSUMED", "TBC")) \
            else None
        profile_segments = (
            p.segments
            if p.segments and len({segment.grade for segment in p.segments}) > 1
            else [])
        represented_grades = ({s.grade for s in p.segments}
                              if p.segments else {p.grade})
        gcol = grade_color(p.grade)
        if opts.grade_filter and not represented_grades.intersection(
                opts.grade_filter):
            gcol = "#dfe4e7"
        segs = None
        if p.segments:
            segs = [(s.frac_from, s.frac_to,
                     grade_color(s.grade)
                     if not opts.grade_filter or s.grade in opts.grade_filter
                     else "#dfe4e7")
                    for s in p.segments]
        cvm = st.concurrency.get(pg.pile_id)
        wf = None
        if cvm is None:
            for j in st.jobs:
                if j.space == pg.pile_id and (j.x0 or j.x1) and j.x1 > j.x0:
                    wf = st.machine_pos.get(j.machine,
                                            MachinePosVM(None)).work_frac
                    break
        hs.stock_lane(
            fig, pg.x0, pg.y0, pg.x1, pg.y1, p.fill_pct, gcol, bcol, dash, bw,
            segments=segs,
            safety_frac=(p.safety_stock_t / p.capacity_t
                         if p.safety_stock_t > 0 else None),
            work_frac=wf,
            selected=(opts.selected_asset == pg.pile_id
                      or (sel_job is not None and sel_job.space == pg.pile_id)))
        _repere_ruler(
            fig, pg, compact,
            st.space_repere_ends.get(pg.pile_id, (0, 1000)))
        if cvm is not None:      # simultaneous inflow + outflow on this pile
            x_from = st.space_bounds.get(
                pg.pile_id, (0.0, st.space_len.get(pg.pile_id, 300.0)))[0]
            _concurrency_overlay(fig, pg, cvm,
                                 st.space_len.get(pg.pile_id, 300.0), compact,
                                 x_from=x_from)
        elif wf is not None and opts.show_tags:
            work_job = next(
                (j for j in st.jobs
                 if j.space == pg.pile_id and j.x1 > j.x0), None)
            if work_job is not None:
                wx = pg.x0 + (pg.x1 - pg.x0) * (
                    (wf[0] + wf[1]) / 2)
                hs._ann(fig, wx, pg.y1 + (0.45 if compact else 0.58),
                        work_job.repere_label,
                        size=6.2 if compact else 8,
                        color=hs.SEL_BLUE,
                        bg="rgba(236,244,251,0.9)")
        # reachability of the SELECTED machine (configured, never proximity)
        if sel_mvm is not None:
            if pg.pile_id in reach:
                fig.add_shape(type="rect", x0=pg.x0 - 0.35, x1=pg.x1 + 0.35,
                              y0=pg.y0 - 0.35, y1=pg.y1 + 0.35,
                              line=dict(color=hs.SEL_BLUE, width=1.6,
                                        dash="dot"))
            else:
                fig.add_shape(type="rect", x0=pg.x0, x1=pg.x1,
                              y0=pg.y0, y1=pg.y1, line=dict(width=0),
                              fillcolor="rgba(236,239,241,0.62)")
        if opts.show_tags:
            icon = ("⛔ " if p.offspec_hard else
                    "⚠ " if p.below_safety else "")
            grade_ids = [s.grade for s in profile_segments] \
                if profile_segments else \
                ([p.grade] if p.grade else [])
            label = (
                _banded_pile_quantity_text(p.mass_t, p.fill_pct)
                if profile_segments and pg.label_with_total else
                "" if profile_segments else
                _compact_pile_quantity_text(p.mass_t, grade_ids))
            extra = " · ⇄" if cvm is not None else ""
            _pile_name_annotation(
                fig, pg, compact, prefix=icon, suffix=extra,
                color=hs.FAULT_RED if p.offspec_hard else
                (hs.WARN_AMBER if p.below_safety else hs.LABEL))
            label_y = pg.y1 - (
                0.2 if compact and profile_segments else 0.9)
            if label:
                hs._ann(fig, pg.x0 + 0.4, label_y,
                        label,
                        size=8 if compact else 9.5, xanchor="left",
                        color=hs.FAULT_RED if p.offspec_hard else
                        (hs.WARN_AMBER if p.below_safety else hs.LABEL),
                        bg="rgba(246,248,249,0.82)")
            if profile_segments:
                width = pg.x1 - pg.x0
                for segment in profile_segments:
                    band_x = (
                        pg.x0 + width * p.fill_pct / 100.0 *
                        (segment.frac_from + segment.frac_to) / 2.0)
                    hs._ann(
                        fig, band_x, pg.y0 + (0.24 if compact else 0.72),
                        _profile_band_text(segment.grade, segment.mass_t),
                        size=5.8 if compact else 8.5,
                        color=hs.LABEL,
                        bg="rgba(246,248,249,0.74)")
        hover_x.append((pg.x0 + pg.x1) / 2)
        hover_y.append((pg.y0 + pg.y1) / 2)
        hover_t.append(
            f"<b>{pg.pile_id}</b><br>"
            f"{p.mass_t:,.0f} / {p.capacity_t:,.0f} t · "
            f"{p.fill_pct:.0f}%"
            + (f"<br>{pg.hover_note}" if pg.hover_note else ""))
        if profile_segments:
            width = pg.x1 - pg.x0
            for segment in profile_segments:
                hover_x.append(
                    pg.x0 + width * p.fill_pct / 100.0 *
                    (segment.frac_from + segment.frac_to) / 2.0)
                hover_y.append((pg.y0 + pg.y1) / 2)
                hover_t.append(
                    f"<b>{pg.pile_id} · {segment.grade}</b><br>"
                    f"{segment.mass_t:,.0f} t · "
                    f"{segment.mass_t / max(p.mass_t, 1):.0%}")

    # ---- rails (horizontal machine lines) ---------------------------------------
    for r in geo.rails:
        r_sel = bool(sel_rail) and r.rail_id == sel_rail
        for off in (-0.12, 0.12):
            fig.add_shape(type="line", x0=r.x0, x1=r.x1, y0=r.y + off,
                          y1=r.y + off,
                          line=dict(color=hs.SEL_BLUE if r_sel
                                    else hs.STATIC_LIGHT,
                                    width=1.7 if r_sel else 1.0,
                                    dash="dash" if r.tbc else None))
        if opts.show_tags and r.label and not compact:
            if r.label_pos == "above":
                hs._ann(fig, r.x0 + 0.3, r.y + 0.78, r.label, size=8,
                        xanchor="left",
                        color=hs.SEL_BLUE if r_sel else hs.STATIC_LIGHT,
                        bg="rgba(236,239,241,0.8)")
            else:
                hs._ann(fig, r.x0 - 0.5, r.y, r.label, size=8,
                        xanchor="right",
                        color=hs.SEL_BLUE if r_sel else hs.STATIC_LIGHT,
                        bg="rgba(236,239,241,0.8)")

    # ---- conveyors -----------------------------------------------------------------
    for cg in geo.conveyors:
        state, tph, cap, gcol, selected = _conv_state(st, cg, opts)
        draw_state = state
        if opts.alarms_only and state not in ("tripped",):
            draw_state = "dimmed"
        elif opts.active_flows_only and state == "idle":
            draw_state = "dimmed"
        show_lbl = opts.show_tags and (cg.tag or cg.sub) and not cg.compact
        hs.conveyor(fig, cg.pts, state=draw_state,
                    tag=cg.tag if show_lbl else "",
                    sub=cg.sub if show_lbl else "",
                    tph=(tph if (opts.show_flows and show_lbl and
                                 tph is not None and cg.planning_enabled)
                         else None),
                    cap=cap, grade_col=gcol or None, tbc=cg.tbc,
                    selected=selected, phase=opts.phase,
                    label_frac=cg.label_frac, label_dy=cg.label_dy,
                    lab_size=7.5 if compact else 8.5,
                    show_utilisation_pct=cg.cid not in ("C1", "C2"))
        if cg.planning_enabled:
            mx, my, _ = hs._point_at(cg.pts, 0.5)
            hover_x.append(mx)
            hover_y.append(my)
            extra = ""
            cvm_c = next((c for c in ms.corridors if c.id == cg.cid), None)
            if cvm_c is not None:
                extra = (f"<br>transit time = {cvm_c.transit_h:g} h · "
                          "in transit "
                          + (f"{cvm_c.in_transit_t / 1000:,.1f} kt"
                             if cvm_c.in_transit_t is not None else "—")
                         + f" · next arrival {cvm_c.next_arrival}")
            hover_title = cg.tag or cg.cid
            hover_t.append(
                f"<b>{hover_title}</b> — "
                + (f"route of machine {cg.machine_id}" if cg.machine_id
                   else f"links: {', '.join(cg.link_ids)}") + "<br>"
                + (f"{tph:,.0f} / {cap:,.0f} t/h" if tph is not None and cap
                   else "") + "<br>state: "
                + ("unavailable" if state == "tripped" else state) + extra)

    # ---- equipment nodes -------------------------------------------------------------
    for e in geo.equipment:
        if e.kind == "tower":
            hs.transfer_tower(fig, e.x, e.y, e.label if opts.show_tags else "",
                              s=e.scale, tbc=e.tbc)
        elif e.kind == "gate":
            # a gate bank discharges whenever ANY of its bound links carries
            # flow — checking only link_ids[0] left a bank drawn idle while the
            # other destination of the same bank was running
            flows = [st.flows[li] for li in e.link_ids if li in st.flows]
            if not flows:
                gstate = "idle"
            elif any(f.running for f in flows):
                gstate = "running"
            elif flows and all(f.tripped for f in flows):
                gstate = "tripped"
            else:
                gstate = "idle"
            hs.clamshell_gate(fig, e.x, e.y,
                              e.label if opts.show_tags else "", gstate,
                              s=e.scale)
        elif e.kind == "hopper":
            flows = [st.flows[li] for li in e.link_ids if li in st.flows]
            hstate = (
                "running" if any(flow.running for flow in flows) else
                "tripped" if flows and all(flow.tripped for flow in flows)
                else "idle"
            )
            hs.hopper(fig, e.x, e.y, e.label if opts.show_tags else "",
                      s=e.scale, state=hstate, tbc=e.tbc)
        elif e.kind in ("sampler", "scale", "analyser"):
            if opts.show_evidence or not e.tbc:
                hs.instrument(fig, e.x, e.y, e.kind, tbc=e.tbc)
        elif e.kind == "junction":
            hs.junction(fig, e.x, e.y)
        elif e.kind in ("corridor_in", "corridor_out"):
            hs.corridor_interface(fig, e.x, e.y, e.label, s=e.scale,
                                  incoming=(e.kind == "corridor_in"))
        elif e.kind == "destbox":
            derived = bool(e.tbc)
            fig.add_shape(type="rect", x0=e.x - e.w / 2, x1=e.x + e.w / 2,
                          y0=e.y - e.h / 2, y1=e.y + e.h / 2,
                          fillcolor="rgba(253,246,234,0.85)" if derived
                          else "#f6f8f9",
                          line=dict(color=hs.WARN_AMBER if derived
                                    else hs.STATIC,
                                    width=1.5,
                                    dash="dash" if derived else None))
            hs._ann(fig, e.x, e.y, f"<b>{e.label}</b>",
                    size=8.5 if compact else 10,
                    color=hs.WARN_AMBER if derived else hs.STATIC)
        elif e.kind == "diverter":
            hs.diverter(fig, e.x, e.y, e.label if opts.show_tags else "",
                        tbc=e.tbc)

    # ---- process-line cards ------------------------------------------------------
    # Lifecycle/configuration cards stay separate from mobile-machine geometry.
    # Some stages (Louta L1/L2) are backed by explicit process transfers and WIP
    # spaces; the card itself intentionally does not invent a mobile Machine.
    for line in geo.process_lines:
        hs.process_line_card(
            fig, line.x0, line.y0, line.x1, line.y1,
            line.line_id, line.label, line.status, compact=compact,
            residence_minutes=line.residence_minutes)
        hover_x.append((line.x0 + line.x1) / 2)
        hover_y.append((line.y0 + line.y1) / 2)
        hover_t.append(
            f"<b>{line.label}</b> — {line.status}<br>"
            "configuration status, not live equipment state<br>"
            + f"evidence: {line.evidence_status}<br>{line.note}")

    # One residence-time caption per washing block.  The line cards carry only
    # lifecycle state; repeating the same 30-minute assumption in every card
    # made the schematic noisy and obscured the process-line labels.
    line_groups: Dict[str, List[ProcessLineGeo]] = {}
    for line in geo.process_lines:
        group = line.line_id.split("-WASH-", 1)[0]
        line_groups.setdefault(group, []).append(line)
    for lines in line_groups.values():
        minutes = {
            float(line.residence_minutes)
            for line in lines if line.residence_minutes is not None
        }
        if len(minutes) != 1:
            continue
        min_y = min(line.y0 for line in lines)
        y_spread = max(line.y0 for line in lines) - min_y
        note_y = min_y - (1.1 if y_spread < 0.2 else 0.55)
        note_x = (
            min(line.x0 for line in lines)
            + max(line.x1 for line in lines)
        ) / 2
        residence = next(iter(minutes))
        hs._ann(
            fig, note_x, note_y,
            f"<b>{residence:g} min residence</b>",
            size=6.8 if compact else 8.0,
            color=hs.STATIC_LIGHT,
        )

    # ---- machines ------------------------------------------------------------------
    disp_anchor = getattr(geo, "_display_anchor", {})
    for mg in geo.machines:
        rail = geo.rail(mg.rail_id) if mg.rail_id else None
        if mg.display:
            # A display machine has no job and no live position. When the
            # configuration names the pile it works on, draw it ON that pile
            # at its reference/home repère so the association is visible; the
            # repère is a display reference, never a measured position.
            # a single served pile anchors the display machine on that pile;
            # a multi-pile machine (M16 bay lines) keeps its rail parking
            # position — the served set is documented in the hover instead
            served_ids = mg.served()
            served = (geo.pile(served_ids[0])
                      if len(served_ids) == 1 else None)
            home_norm = (min(max(mg.home_repere / 1000.0, 0.0), 1.0)
                         if served is not None else None)
            tip = None
            if mg.machine_id in disp_anchor:
                gx, gy = disp_anchor[mg.machine_id]
            elif rail is not None:
                gx, gy, tipx, tipy, _ = machine_draw_pos(
                    rail, served, home_norm, mg.parking_frac, mg.scale)
                if served is not None:
                    tip = (tipx, tipy)
            else:
                continue
            draw = hs.stacker if mg.kind == "stacker" else hs.reclaimer
            tipx, tipy = tip if tip is not None else \
                (gx + 2.0 * mg.scale, gy + 0.4)
            display_state = "Future" if mg.future else "Idle"
            draw(fig, gx, gy, tipx, tipy,
                 display_state, mg.label, s=mg.scale,
                 dash="dot" if mg.future else "dash",
                 label_side=mg.label_side)
            if opts.show_evidence and not compact:
                # subtle reference badge only — the tag/position/scope
                # details live in the hover text and the asset-details
                # panel, not stamped over the drawing per machine
                hs._ann(fig, gx, gy - 1.6 * mg.scale - 0.75,
                        "FUTURE" if mg.future else "REF",
                        size=7,
                        color=(hs.FUTURE_VIOLET if mg.future
                               else hs.STATIC_LIGHT),
                        bg="rgba(236,239,241,0.8)")
            hover_x.append(gx)
            hover_y.append(gy)
            scope_line = (f"<br>package scope: {mg.package_scope or '—'} · "
                         f"planning scope: {mg.planning_scope or '—'}"
                         if (mg.package_scope or mg.planning_scope) else "")
            home_line = (
                f"<br>reference position: {mg.served_pile} · "
                f"{format_repere(mg.home_repere)} (home/display reference — "
                "no job, no measured position)"
                if served is not None else
                f"<br>serves: {', '.join(mg.served())}"
                if mg.served() else "")
            hover_t.append(
                f"<b>{mg.label}</b><br>{mg.note}{scope_line}{home_line}")
            continue
        vm = _machine_vm(ms, mg.machine_id)
        if vm is None or rail is None:
            continue
        pv = st.machine_pos.get(mg.machine_id, MachinePosVM(None))
        status = vm.status
        if mg.machine_id in st.down_machines:
            status = "Unavailable"           # failure window (sim history)
        elif pv.travelling and status == "Idle":
            status = "Travelling"
        pg = geo.pile(vm.target_space) if vm.target_space else None
        relocated = ""
        use_rail = rail
        if pg is not None and pg.pile_id not in rail.flanks:
            # No site has a documented cross-line relocation: every machine
            # translates along its OWN line only. At M16 the line's flanked
            # bays now equal the machine's configured reach (PFD-0001[3] +
            # bay-adjacency rule), so this branch means geometry and
            # reachability disagree — the machine stays parked on its line and
            # the mismatch is disclosed instead of being drawn away.
            relocated = (f" · serving {pg.pile_id} — NOT flanked by "
                         f"{rail.rail_id}: cross-line reach ASSUMED (no GA)")
            pg = None
        gx, gy, tipx, tipy, side = machine_draw_pos(
            use_rail, pg, pv.x_norm, mg.parking_frac, mg.scale)
        if opts.alarms_only and status in ("Idle", "Stacking", "Reclaiming",
                                           "Travelling"):
            status_draw = "Idle"
        else:
            status_draw = status
        selected = (opts.selected_asset == mg.machine_id
                    or (sel_job is not None
                        and sel_job.machine == mg.machine_id))
        optional = vm.lifecycle == "OPTIONAL_PROPOSED"
        dash = "dash" if (optional or vm.evidence_status
                          in ("ASSUMED", "TBC")) else None
        canvas_label = mg.label
        if pv.repere is not None:
            canvas_label += f" · {format_repere(pv.repere)}"
        if mg.twin_boom:
            # ONE twin-boom machine (e.g. B02 ST3): booms reach BOTH flanked
            # pile banks from a single gantry — never drawn as two machines
            ups = [geo.pile(p) for p in use_rail.flanks
                   if geo.pile(p) is not None and geo.pile(p).y0 >= use_rail.y]
            dns = [geo.pile(p) for p in use_rail.flanks
                   if geo.pile(p) is not None and geo.pile(p).y1 <= use_rail.y]
            pu = ups[0] if ups else None
            pd = dns[0] if dns else None
            tip_u = ((min(max(gx, pu.x0 + 0.5), pu.x1 - 0.5),
                      pu.y0 + 0.35 * (pu.y1 - pu.y0)) if pu else
                     (gx + 1.6 * mg.scale, gy + 0.8))
            tip_d = ((min(max(gx, pd.x0 + 0.5), pd.x1 - 0.5),
                      pd.y1 - 0.35 * (pd.y1 - pd.y0)) if pd else
                     (gx - 1.6 * mg.scale, gy - 0.8))
            hs.twin_boom_stacker(fig, gx, gy, tip_u, tip_d, status_draw,
                                 canvas_label if opts.show_tags else "",
                                 s=mg.scale, dash=dash, selected=selected,
                                 label_side=mg.label_side)
        else:
            draw = hs.stacker if vm.kind == "stacker" else hs.reclaimer
            draw(fig, gx, gy, tipx, tipy, status_draw,
                 canvas_label if opts.show_tags else "", s=mg.scale, dash=dash,
                 selected=selected, label_side=mg.label_side)
        if optional and opts.show_evidence:
            hs._ann(fig, gx, gy - 1.05 * mg.scale - 0.55,
                    "OPTIONAL" + ("" if vm.planning_enabled
                                  else " · no solver jobs"),
                    size=7, color=hs.WARN_AMBER,
                    bg="rgba(253,246,234,0.9)", bordercolor=hs.WARN_AMBER,
                    borderwidth=0.8, borderpad=1.5)
        # resource conflict marker: shares the selected machine's rail
        if sel_rail and mg.rail_id == sel_rail and \
                mg.machine_id != opts.selected_asset:
            hs._ann(fig, gx, gy - 1.3 * mg.scale - 0.95, "⚠ shares rail",
                    size=7, color=hs.WARN_AMBER,
                    bg="rgba(253,246,234,0.9)")
        hover_x.append(gx)
        hover_y.append(gy)
        hover_t.append(
            f"<b>{mg.label}</b> ({vm.kind})"
            + (f" — physical tag {mg.physical_tag}" if mg.physical_tag
               else "")
            + (f"<br>machine line {mg.rail_id} · serves "
               + ", ".join(mg.served()) if mg.served() else "")
            + f"<br>state: {status}"
            + (f" · job {vm.job_id} → {vm.target_space or vm.dest_id}"
               if vm.job_id else "")
            + (f"<br>rate {vm.rate_tph:,.0f} / {vm.rate_max_tph:,.0f} t/h"
               if vm.rate_tph else
               f"<br>max rate {vm.rate_max_tph:,.0f} t/h")
            + (f"<br>position: {pv.space} · {format_repere(pv.repere)}"
               + (f" · x={pv.x_m:g} m internal"
                  if pv.x_m is not None else "")
               if pv.repere is not None else "")
            + (f"<br>work range: "
               f"{format_repere_range(*pv.work_repere)}"
               if pv.work_repere is not None else "")
            + (f"<br>nominal design: {vm.rate_design_tph:,.0f} t/h"
               if vm.rate_design_tph else "")
            + (f"<br>lifecycle: {vm.lifecycle}"
               + ("" if vm.planning_enabled else " · planning DISABLED")
               if vm.lifecycle != "EXISTING" else "")
            + f"<br>{pv.note}{relocated}")

    # ---- chips / stamps / footer ------------------------------------------------------
    for ch in geo.chips:
        if opts.show_evidence:
            if getattr(ch, "kind", "gap") == "info":
                # informational reference (source facts, ratings) — grey,
                # so amber stays reserved for genuine engineering gaps
                hs._ann(fig, ch.x, ch.y, ch.tag, size=8,
                        color=hs.STATIC_LIGHT, bg="rgba(236,239,241,0.85)",
                        bordercolor=hs.STATIC_LIGHT, borderwidth=0.6,
                        borderpad=1.5)
            else:
                hs.tag_chip(fig, ch.x, ch.y, ch.tag, ch.note)
    if opts.show_evidence:
        for sp in geo.stamps:
            hs.stamp(fig, sp.x, sp.y, sp.text)
    x0, y0, x1, y1 = geo.bounds
    hs._ann(fig, (x0 + x1) / 2, y0 + 0.45, st.acs_note, size=7.5,
            color=hs.STATIC_LIGHT, bg="rgba(236,239,241,0.92)")

    # ---- hover trace -------------------------------------------------------------------
    fig.add_trace(go.Scatter(x=hover_x, y=hover_y, mode="markers",
                             marker=dict(size=14, opacity=0.001),
                             hovertext=hover_t, hoverinfo="text",
                             showlegend=False))
    fig.update_xaxes(visible=False, range=[x0, x1])
    fig.update_yaxes(visible=False, range=[y0, y1])
    fig.update_layout(
        height=geo.height_px, plot_bgcolor=hs.HMI_BG,
        paper_bgcolor=hs.HMI_PAPER, margin=dict(l=6, r=6, t=8, b=4),
        dragmode="pan", hoverlabel=dict(font_size=11, align="left"),
        showlegend=False)
    return fig.build()


def grade_profile_distribution_rows(
        st: MimicState, geo: SiteGeometry) -> List[Dict[str, object]]:
    """Normalised mass-share rows for the compact distribution chart.

    Modelled piles use explicit inventory/zone segments when present, then the
    configured assumed composition fallback. Reference piles use their
    display-only composition. Single-profile piles are omitted.
    """
    rows: List[Dict[str, object]] = []
    for pg in geo.piles:
        if pg.modelled:
            pile = _pile_vm(st.monitor, pg.pile_id)
            if pile is None or not pile.segments or \
                    len({seg.grade for seg in pile.segments}) < 2:
                continue
            total = sum(seg.mass_t for seg in pile.segments) or pile.mass_t
            for seg in pile.segments:
                share = seg.mass_t / max(total, 1.0)
                quality = " · ".join(
                    f"{key} {seg.quality[key]:g}"
                    + (" ppm" if key == "Cd" else "%")
                    for key in ("BPL", "Cd", "MgO", "SiO2", "CO2",
                                "moisture")
                    if key in seg.quality)
                rows.append({
                    "pile_id": pg.pile_id,
                    "profile_id": seg.grade,
                    "profile_label": _short_grade_token(seg.grade),
                    "mass_fraction": share,
                    "mass_t": seg.mass_t,
                    "quality": quality,
                    "basis": seg.basis,
                    "actual": seg.actual,
                })
            continue
        if len(pg.display_grade_components) < 2:
            continue
        for component in pg.display_grade_components:
            mass_t = (pg.display_mass_t * component.mass_fraction
                      if pg.display_mass_t is not None else 0.0)
            quality = " · ".join(
                f"{key} {component.quality[key]:g}"
                + (" ppm" if key == "Cd" else "%")
                for key in ("BPL", "Cd", "MgO", "SiO2", "CO2",
                            "moisture")
                if key in component.quality)
            rows.append({
                "pile_id": pg.pile_id,
                "profile_id": component.grade_id,
                "profile_label": _short_grade_token(component.grade_id),
                "mass_fraction": component.mass_fraction,
                "mass_t": mass_t,
                "quality": quality,
                "basis": pg.display_grade_basis,
                "actual": False,
            })
    return rows


def grade_profile_distribution_figure(
        rows: List[Dict[str, object]]) -> Optional[go.Figure]:
    """Small 100%-stacked distribution with visible profile tonnages."""
    if not rows:
        return None
    piles = list(dict.fromkeys(str(row["pile_id"]) for row in rows))
    profiles = list(dict.fromkeys(str(row["profile_id"]) for row in rows))
    row_by_key = {
        (str(row["pile_id"]), str(row["profile_id"])): row for row in rows}
    fig = go.Figure()
    for profile_id in profiles:
        values, text, custom = [], [], []
        label = profile_id
        for pile_id in piles:
            row = row_by_key.get((pile_id, profile_id))
            if row is None:
                values.append(0.0)
                text.append("")
                custom.append([0.0])
                continue
            share_pct = 100.0 * float(row["mass_fraction"])
            mass_t = float(row["mass_t"])
            values.append(share_pct)
            text.append(_compact_mass_text(mass_t)
                        if share_pct >= 8 else "")
            label = str(row["profile_label"])
            custom.append([mass_t])
        fig.add_bar(
            y=piles, x=values, name=label, orientation="h",
            marker_color=grade_color(profile_id), text=text,
            textposition="inside", insidetextanchor="middle",
            customdata=custom,
            hovertemplate=(
                "<b>%{y} · " + label + "</b><br>"
                "%{customdata[0]:,.0f} t · %{x:.0f}%"
                "<extra></extra>"))
    fig.update_layout(
        barmode="stack", height=max(180, 75 + 50 * len(piles)),
        margin=dict(l=8, r=8, t=36, b=34),
        paper_bgcolor="#ffffff", plot_bgcolor="#f6f8f9",
        legend=dict(orientation="h", yanchor="bottom", y=1.02,
                    xanchor="left", x=0),
        xaxis=dict(range=[0, 100], ticksuffix="%", title="Mass share",
                   gridcolor="#dfe4e7"),
        yaxis=dict(autorange="reversed", title=None),
        hoverlabel=dict(font_size=11, align="left"),
        showlegend=True)
    return fig


def geometry_model_mismatch(sc: Scenario, geo: SiteGeometry) -> List[str]:
    """Model entities the DRAWING expects but the scenario does not have.

    A corrected mimic over a superseded solver graph is a silent lie: the
    picture shows the new topology while the plan was solved on the old one.
    The Streamlit layer refuses to present flows in that state and says so.
    Migration (`datasets.migrate_loaded_builtin`) normally prevents it; an
    imported scenario can still be stale.
    """
    known_links = {l.id for l in sc.links}
    known_nodes = {n.id for n in sc.nodes}
    known_spaces = {space.id for space in sc.spaces}
    known_machines = {machine.id for machine in sc.machines}
    missing = {li for cg in geo.conveyors for li in cg.link_ids
               if li not in known_links}
    missing |= {li for e in geo.equipment for li in e.link_ids
                if li not in known_links}
    missing |= {
        f"space {pile.pile_id}"
        for pile in geo.piles
        if pile.modelled and pile.pile_id not in known_spaces
    }
    missing |= {
        f"machine {machine.machine_id}"
        for machine in geo.machines
        if not machine.display and machine.machine_id not in known_machines
    }
    obsolete = {n.id for n in sc.nodes if n.id == "HUB:M16-RECLAIM"}
    return sorted(missing | {f"obsolete node {n}" for n in obsolete
                             if n not in known_nodes or True})


# ================================ Streamlit layer ==================================
VIEW_LABELS = {"GLOBAL": "Global stockyard layout",
               "CHAIN": "Entire value chain", "LOUTA": "Louta",
               "BENGUERIR": "Benguerir", "MPH": "MPH M16"}

#: Reference-only lanes have no inventory series, so the state selector cannot
#: move them. Say so wherever a non-initial state is shown, otherwise a static
#: display mass under "Final plan" reads as a defect.
_REFERENCE_LANE_VIEWS = {
    "LOUTA": ("102MG mine-storage lanes", "102MG mine-storage lanes"),
}


def _reference_lane_note(view: str, basis_sel: str, short: bool) -> str:
    names = _REFERENCE_LANE_VIEWS.get(view)
    if names is None or basis_sel == "Initial state":
        return ""
    return (f" · {names[1]} remain static t0 reference" if short
            else f" · {names[0]} stay at static t0 (reference-only)")


def configured_mimic_geometry(
        sc: Scenario, view: str, *, show_future: bool = False) -> SiteGeometry:
    """Return drawing geometry with current Configure switches applied.

    Geometry remains the validated arrangement. Only process-line lifecycle
    cards are copied and marked disabled; machine and route availability are
    already resolved from the live scenario by the mimic state builder.
    """
    geo = geometry_for(sc, view, show_future=show_future)
    disabled = disabled_process_lines(sc)
    if not disabled:
        return geo
    configured = deepcopy(geo)
    for line in configured.process_lines:
        if line.line_id in disabled:
            line.status = process_line_operating_state(sc, line.line_id)
    return configured


def render_process_mimic(sc: Scenario, plan, sim,
                         views: Optional[List[str]] = None,
                         default_view: Optional[str] = None,
                         key: str = "mimic",
                         alarm_summary: bool = False,
                         drill_buttons: bool = False,
                         collapse_controls: bool = False,
                         redesign: bool = False) -> None:
    import streamlit as st

    from . import theme

    qp = {}
    try:
        qp = dict(st.query_params)
    except Exception:
        pass
    if views is None:
        views = ["CHAIN", "LOUTA", "BENGUERIR", "MPH"]
    if not any(l.id == "C1" for l in sc.links):     # M16-only scenario (S2)
        views = ["MPH"]
    sidebar_hour = None
    sidebar_basis = None
    if plan is not None:
        try:
            raw_basis = st.session_state.get(PROCESS_MIMIC_BASIS_KEY)
            if raw_basis in process_mimic_sidebar_states(plan, sim):
                sidebar_basis = raw_basis
            raw_hour = st.session_state.get(PROCESS_MIMIC_HOUR_KEY)
            if raw_hour is not None:
                sidebar_hour = max(0.0, min(
                    float(raw_hour),
                    sc.sim.horizon_buckets * sc.sim.dt_h))
        except (TypeError, ValueError):
            sidebar_hour = None
    sidebar_control = sidebar_basis is not None
    sidebar_timeline = (
        sidebar_control and sidebar_basis == PROCESS_MIMIC_PLAN_TIMELINE
        and sidebar_hour is not None
    )
    state_opts = [PROCESS_MIMIC_INITIAL_STATE]
    if plan is not None:
        state_opts.append(PROCESS_MIMIC_FINAL_STATE)
    if sim is not None:
        state_opts.append(PROCESS_MIMIC_SIMULATION_STATE)
    if not collapse_controls and plan is not None:
        state_opts.append("Inspect plan bucket")
    qp_state = {
        "initial": PROCESS_MIMIC_INITIAL_STATE,
        "final": PROCESS_MIMIC_FINAL_STATE,
        "plan": PROCESS_MIMIC_FINAL_STATE,
        "plan_bucket": "Inspect plan bucket",
        "sim": PROCESS_MIMIC_SIMULATION_STATE,
    }.get(str(qp.get("mimic_basis", "")).lower())
    default_state = (
        qp_state if qp_state in state_opts else PROCESS_MIMIC_INITIAL_STATE)
    basis_sel = default_state
    if collapse_controls and not sidebar_control and not redesign:
        basis_sel = st.radio(
            "Stockyard state",
            state_opts,
            index=state_opts.index(default_state),
            horizontal=True,
            key=f"{key}_state_view_v2",
            help=("Initial is the scenario t0 snapshot. Final plan is the "
                  "terminal inventory boundary after all plan buckets."))
    control_box = (st.expander("Mimic controls — view, time and filters",
                               expanded=False)
                   if collapse_controls else st.container(border=True))
    with control_box:
        c = st.columns([2.1, 1.7, 1.7, 1.4])
        qp_view = str(qp.get("mimic_view", "")).upper()
        if qp_view in views:
            vidx = views.index(qp_view)
        elif default_view in views:
            vidx = views.index(default_view)
        else:
            vidx = 0
        view = c[0].radio("View", views, index=vidx, horizontal=True,
                          format_func=lambda v: VIEW_LABELS[v],
                          key=f"{key}_view_sel")
        if sidebar_control:
            basis_sel = sidebar_basis
            c[1].caption(f"State **{basis_sel}**")
        elif collapse_controls and redesign:
            basis_sel = c[1].selectbox(
                "State view",
                state_opts,
                index=state_opts.index(default_state),
                key=f"{key}_state_view_v2",
                help=("Initial is the scenario t0 snapshot. Final plan is the "
                      "terminal inventory boundary after all plan buckets."))
        elif collapse_controls:
            c[1].caption(f"State **{basis_sel}**")
        else:
            basis_sel = c[1].radio(
                "State view",
                state_opts,
                index=state_opts.index(default_state),
                horizontal=True,
                key=f"{key}_state_view_v2",
                help=("Initial is t0; Final plan is inventory[T], after the "
                      "last work bucket. Inspect plan bucket is optional "
                      "engineering replay."))
        basis = {
            PROCESS_MIMIC_INITIAL_STATE: "initial",
            PROCESS_MIMIC_FINAL_STATE: "plan",
            "Inspect plan bucket": "plan",
            PROCESS_MIMIC_PLAN_TIMELINE: "plan",
            PROCESS_MIMIC_SIMULATION_STATE: "sim",
        }[basis_sel]
        if sidebar_timeline:
            max_b = sc.sim.horizon_buckets
            bucket = process_mimic_bucket_for_hour(sc, sidebar_hour)
            if bucket == max_b:
                c[2].caption(
                    f"Sidebar **t = {sidebar_hour:g} h** · terminal boundary "
                    f"**T={bucket}**")
            elif bucket == 0:
                c[2].caption(
                    f"Sidebar **t = {sidebar_hour:g} h** · initial boundary "
                    "**t0**")
            else:
                c[2].caption(
                    f"Sidebar **t = {sidebar_hour:g} h** · bucket **{bucket}**")
        elif sidebar_control and \
                basis_sel == PROCESS_MIMIC_SIMULATION_STATE:
            max_b = sim.bucket if sim is not None else 0
            bucket = max_b
            c[2].caption(f"Simulation bucket **{bucket}**")
        elif basis_sel == PROCESS_MIMIC_FINAL_STATE:
            max_b = sc.sim.horizon_buckets
            bucket = max_b
            c[2].caption(
                f"Terminal boundary **T={max_b}** · no active job at T")
        elif basis_sel == PROCESS_MIMIC_INITIAL_STATE:
            max_b = 0
            bucket = 0
            c[2].caption("Scenario snapshot **t0**")
        else:
            max_b = (sc.sim.horizon_buckets
                     if basis_sel == "Inspect plan bucket"
                     else (sim.bucket if sim else 0))
        if not sidebar_control and \
                basis_sel not in (
                    PROCESS_MIMIC_FINAL_STATE,
                    PROCESS_MIMIC_INITIAL_STATE,
                ) and max_b > 0:
            try:
                b_def = min(int(qp.get("mimic_bucket", max_b)), max_b)
            except Exception:
                b_def = max_b
            bucket = c[2].slider("Time bucket", 0, max_b, b_def,
                                 key=f"{key}_bucket_sel")
        elif not sidebar_control and \
                basis_sel not in (
                    PROCESS_MIMIC_FINAL_STATE,
                    PROCESS_MIMIC_INITIAL_STATE,
                ):
            bucket = 0
            c[2].caption("bucket slider active with a plan/simulation")
        st_ = build_process_mimic_state(sc, plan, sim, basis=basis,
                                        bucket=bucket)
        job_ids = ["—"] + [j.id for j in st_.jobs]
        jobs_by_id = {j.id: j for j in st_.jobs}

        def _job_option(job_id: str) -> str:
            if job_id == "—":
                return job_id
            job = jobs_by_id[job_id]
            return (f"{job.id} · {job.space} · {job.repere_label} · "
                    f"{job.tonnage_t / 1000:,.1f} kt")

        sel_job = c[3].selectbox("Selected job (path highlight)", job_ids,
                                 format_func=_job_option,
                                 key=f"{key}_job_sel")
        sel_job = "" if sel_job == "—" else sel_job

        # Keep filters readable on laptop-width content areas.  Seven controls
        # in one Streamlit row collapsed labels into one-character columns
        # whenever the sidebar was open.
        c2 = st.columns([2.2, 1.1, 0.9])
        grades = [g.id for g in sc.grades]
        gsel = c2[0].multiselect("Grade filter", grades, default=[],
                                 key=f"{key}_grades",
                                 format_func=theme.operator_text)
        show_flows = c2[1].toggle("Active flow", value=True,
                                  key=f"{key}_flow")
        show_tags = c2[2].toggle("Tags", value=True, key=f"{key}_tags")
        c2b = st.columns([1, 1.05, 1.35, 1.1])
        show_ev = c2b[0].toggle("Evidence", value=True, key=f"{key}_ev")
        alarms = c2b[1].toggle("Alarms only", value=False,
                               key=f"{key}_alarm")
        active_only = c2b[2].toggle("Active flows only", value=False,
                                   key=f"{key}_activeonly",
                                   help="dim idle conveyors/routes — active "
                                        "material flows stay highlighted")
        show_future = c2b[3].toggle(
            "Future exp.", value=False, key=f"{key}_future",
            help="future buildout (ST01-D*/RC01-C*) — reference "
                 "configuration, never solver inventory")
        c3 = st.columns([1.15, 1.05, 4.4])
        if c3[0].button("Fit view / reset zoom", key=f"{key}_fit"):
            st.session_state[f"{key}_zoom_nonce"] = \
                st.session_state.get(f"{key}_zoom_nonce", 0) + 1
        if c3[1].button("Reset selection", key=f"{key}_resetsel",
                        help="clear the selected job/asset highlighting"):
            for k_ in (f"{key}_asset_sel", f"{key}_job_sel"):
                st.session_state.pop(k_, None)
            st.rerun()
        raw_reference_note = _reference_lane_note(view, basis_sel, short=False)
        c3[2].caption(f"**{st_.timestamp}** · bucket {st_.bucket} · basis "
                      f"**{basis_sel}** · "
                      f"{st_.monitor.scenario_name.split(' —')[0]}"
                      f"{raw_reference_note}"
                      " · scroll = zoom · drag = pan · double-click = reset")

    if collapse_controls and not redesign:
        raw_reference_note = _reference_lane_note(view, basis_sel, short=True)
        control_hint = ("use the sidebar state/time controls"
                        if sidebar_control else
                        "open the controls above to change the view")
        st.caption(f"**{VIEW_LABELS[view]}** · {basis_sel} · bucket "
                   f"{st_.bucket}{raw_reference_note} — {control_hint}")

    if st_.monitor.message:
        st.info(theme.operator_text(st_.monitor.message))
        return

    # Render the operator's primary visual immediately after its controls.
    # Context strips belong below it; placing them first pushed the 700 px
    # chart below a laptop viewport and, while Plotly was still constructing
    # the figure, made a valid post-solve layout look as if it had vanished.
    geo = configured_mimic_geometry(
        sc, view, show_future=show_future)
    stale = geometry_model_mismatch(sc, geo)
    if stale:
        st.error(
            "**Stale scenario graph — flows are not shown.** This drawing is "
            "the corrected layout, but the loaded scenario is missing/still "
            "carrying: " + ", ".join(stale[:8])
            + ". Re-load the built-in scenario (or re-import a current "
              "snapshot) so the plan and the mimic describe the same yard.")
    opts = MimicOpts(show_flows=show_flows, show_tags=show_tags,
                     show_evidence=show_ev, alarms_only=alarms,
                     active_flows_only=active_only,
                     grade_filter=gsel, selected_job=sel_job,
                     phase=st_.bucket)
    sel_asset = st.session_state.get(f"{key}_asset_sel", "")
    if sel_asset and sel_asset != "—":
        if sel_asset.endswith(DISPLAY_BADGE):
            opts.selected_asset = sel_asset[:-len(DISPLAY_BADGE)].strip()
        else:
            opts.selected_asset = sel_asset.split(" · ")[-1]
    nonce = st.session_state.get(f"{key}_zoom_nonce", 0)
    with st.spinner("Rendering stockyard layout…"):
        from streamlit.components.v1 import html as component_html

        from .flow_svg import (
            redesign_adapter,
            redesign_viewer,
            svg_mimic,
            svg_viewer,
        )

        if redesign:
            # Claude's handoff is one global drawing with its own site tabs.
            # Always bind the complete chain so focusing Louta/Benguerir/MPH
            # never turns the other sites into invented idle placeholders.
            snapshot_geo = (
                configured_mimic_geometry(
                    sc, "CHAIN", show_future=show_future)
                if "CHAIN" in views else geo
            )
            metadata = redesign_adapter.build_snapshot(
                sc, st_, snapshot_geo, opts, basis=basis_sel)
            metadata["initialSite"] = (
                view if view in {"LOUTA", "BENGUERIR", "MPH"} else None)
            viewer_height = REDESIGN_MIMIC_VIEWER_HEIGHT
            viewer = redesign_viewer.html(
                metadata=metadata,
                height=viewer_height,
                dark=False,
            )
        else:
            mimic_svg, _ = svg_mimic.build(
                sc, st_, geo, opts, dark=False, basis=basis_sel,
                viewer_chrome=True)
            viewer_height = SVG_MIMIC_VIEWER_HEIGHT
            viewer = svg_viewer.html(
                mimic_svg, height=viewer_height, dark=False)
        # The nonce makes the existing "Fit view / reset zoom" control replace
        # the iframe document, returning the native SVG viewBox to fit.
        viewer += f"\n<!-- fit-reset:{nonce} -->"
        component_html(
            viewer,
            height=viewer_height + 8,
            scrolling=False,
        )
    if not redesign:
        with st.expander("Machine symbol and state key", expanded=False):
            legend_svg, legend_aspect = svg_mimic.legend_svg(
                False, px_width=1140)
            component_html(
                f'<div style="margin:0;background:#eef1f3">{legend_svg}</div>',
                height=int(1140 * legend_aspect) + 8,
                scrolling=False,
            )
    distribution_rows = grade_profile_distribution_rows(st_, geo)
    if distribution_rows and view in ("LOUTA", "BENGUERIR"):
        st.markdown("##### Grade-profile distribution")
        st.caption(
            "Starting pile composition by mass. Assumed bands are schematic. "
            "Raw/ore profiles and washed-product profiles are separate "
            "material states — never compare or substitute them.")
        distribution_fig = grade_profile_distribution_figure(
            distribution_rows)
        if distribution_fig is not None:
            st.plotly_chart(
                theme.operator_figure(distribution_fig), width="stretch",
                config={"displaylogo": False, "displayModeBar": False},
                key=f"{key}_grade_distribution_{view}_{GEOMETRY_REVISION}")
    if drill_buttons and len(views) > 1:
        b = st.columns(len(views))
        for i, v in enumerate(views):
            if v == view:
                continue
            if b[i].button(f"Open {VIEW_LABELS[v]} detail", key=f"{key}_go_{v}"):
                st.session_state[f"{key}_view_sel"] = v
                st.rerun()

    # ---- alarm summary strip ---------------------------------------------------
    if alarm_summary:
        n_pile_warn = sum(len(p.warnings) for sv in st_.monitor.sites.values()
                          for p in sv.piles)
        n_mach = sum(1 for sv in st_.monitor.sites.values()
                     for m in sv.machines
                     if m.status in ("Unavailable", "Maintenance"))
        n_trip = sum(1 for f in st_.flows.values() if f.tripped)
        n_conc = sum(1 for c_ in st_.concurrency.values()
                     if c_.status != "ok")
        theme.mini_kpis([
            ("Pile warnings", f"{n_pile_warn}"),
            ("Machines down/maint.", f"{n_mach}"),
            ("Tripped links", f"{n_trip}"),
            ("Concurrency alerts", f"{n_conc}"),
            ("Concurrent piles",
             f"{len(st_.concurrency)} (sep ≥ min: "
             f"{sum(1 for c_ in st_.concurrency.values() if c_.status == 'ok')})"),
        ])

    # ---- C1 feeder-selection panel (Louta product reclaim) ----------------------
    if view in ("GLOBAL", "CHAIN", "LOUTA") and \
            any(l.id == "C1" for l in sc.links):
        feeders = [m for m in sc.machines
                   if m.site == "LOUTA" and m.kind == "reclaimer"]
        if len(feeders) > 1:
            c1f = st_.flows.get("C1")
            items = []
            for m_ in feeders:
                j = next((j for j in st_.jobs if j.machine == m_.id), None)
                if j is not None:
                    txt = f"ACTIVE — {j.rate_tph:,.0f} t/h → C1"
                else:
                    vm_ = _machine_vm(st_.monitor, m_.id)
                    status = vm_.status if vm_ is not None else "Idle"
                    if m_.id in st_.down_machines:
                        status = "Unavailable"
                    txt = {"Maintenance": "MAINTENANCE — calendar window",
                           "Unavailable": "UNAVAILABLE — outage"}.get(
                        status, "available (standby)")
                items.append((site_tag("LOUTA", m_.id), txt))
            items.append(("C1 corridor",
                          (f"{c1f.tph:,.0f} / {c1f.cap_tph:,.0f} t/h"
                           if c1f is not None else "—")))
            items.append(("Feeder policy",
                          "either reclaimer (3,000 t/h nom each) · "
                          "simultaneous partial-rate operation configurable"))
            theme.mini_kpis(items)

    # ---- details panel (selector fallback — reliable in Streamlit) -------------
    ms = st_.monitor
    with st.expander("🔍 Asset details (machine / stockpile / conveyor)",
                     expanded=False):
        opts_m = [f"{site_tag(m.site, m.id)}" for sv in ms.sites.values()
                  for m in sv.machines]
        # Display/reference machines are drawn but are NOT in
        # Scenario.machines. Offer them here too — an asset visible on the
        # mimic that cannot be inspected reads as a UI defect — badged so it
        # is never mistaken for a planned resource.
        _known = {o.split(" · ")[-1] for o in opts_m}
        opts_display = [f"{mg.machine_id}{DISPLAY_BADGE}"
                        for mg in geo.machines
                        if mg.display and mg.machine_id not in _known]
        opts_m = opts_m + opts_display
        opts_p = [f"{site_tag(p.site, p.id)}" for sv in ms.sites.values()
                  for p in sv.piles]
        opts_c = [c_.cid for c_ in geo.conveyors if c_.planning_enabled]
        kind = st.radio("Asset type", ["Machine", "Stockpile", "Conveyor"],
                        horizontal=True, key=f"{key}_dkind")
        if kind == "Machine":
            sel = st.selectbox("Machine", opts_m, key=f"{key}_asset_sel")
            is_display = sel.endswith(DISPLAY_BADGE)
            mid = (sel[:-len(DISPLAY_BADGE)] if is_display
                   else sel.split(" · ")[-1])
            vm = _machine_vm(ms, mid)
            pv = st_.machine_pos.get(mid, MachinePosVM(None))
            mrec = next((m for m in sc.machines if m.id == mid), None)
            mg = next((m for m in geo.machines if m.machine_id == mid), None)
            if is_display:
                if mg:
                    theme.mini_kpis([
                        ("Asset", mg.label or mid),
                        ("Status", "DISPLAY / REFERENCE geometry — drawn "
                                   "for engineering review only"),
                        ("Line", mg.rail_id),
                        ("Type", mg.kind),
                        ("Physical tag", mg.physical_tag or "Not recorded"),
                        ("In planning model",
                         "NO — absent from Scenario.machines; no solver "
                         "jobs, no rate/position state"),
                        ("Package scope", mg.package_scope or "—"),
                        ("Planning scope", mg.planning_scope or "—"),
                        ("Evidence / note", mg.note or "—"),
                    ])
            elif vm and mrec:
                position = (
                    f"{pv.space} · {format_repere(pv.repere)}"
                    + (f" · x={pv.x_m:g} m internal" if pv.x_m is not None
                       else "")
                    if pv.repere is not None else
                    f"x={mrec.position_x:g} m · {POSITION_NOTE}")
                theme.mini_kpis([
                    ("Site / line", f"{vm.site} / "
                                    f"{mg.rail_id if mg else mrec.rail}"),
                    ("Tag", site_tag(vm.site, vm.id)
                     + (f" ({mg.physical_tag})" if mg and mg.physical_tag
                        else "")),
                    ("Type", vm.kind), ("State", vm.status),
                    ("Control mode", "planning mimic — no live control"),
                    ("Current job", vm.job_id or "—"),
                    ("Position", position),
                    ("Current work range",
                     format_repere_range(*pv.work_repere)
                     if pv.work_repere is not None else "—"),
                    ("Rate / max", f"{vm.rate_tph:,.0f} / "
                                   f"{vm.rate_max_tph:,.0f} t/h"),
                    ("Source/destination", vm.target_space or "—"
                     if vm.kind == "stacker" else
                     f"{vm.target_space or '—'} → {vm.dest_id or '—'}"),
                    ("Reachable (configured)", ", ".join(vm.reachable) or "—"),
                    ("Evidence", f"{vm.evidence_status} — see register"),
                ])
        elif kind == "Stockpile":
            sel = st.selectbox("Stockpile", opts_p, key=f"{key}_asset_sel")
            pid = sel.split(" · ")[-1]
            p = _pile_vm(ms, pid)
            cvm = st_.concurrency.get(pid)
            if p:
                sprec = next((sp for sp in sc.spaces if sp.id == p.id), None)
                theme.mini_kpis([
                    ("Site", p.site), ("Tag", site_tag(p.site, p.id)),
                    ("State", p.state),
                    ("Mass / capacity", f"{p.mass_t:,.0f} / "
                                        f"{p.capacity_t:,.0f} t"),
                    ("Fill", f"{p.fill_pct:.0f} %"),
                    ("Grade / profile", p.grade_label),
                    ("Material state", ((sprec.material_class or "—") + " · "
                                        + (sprec.profile_id or "—"))
                     if sprec else "—"),
                    ("In / out", f"{p.inbound_tph:,.0f} / "
                                 f"{p.outbound_tph:,.0f} t/h"
                     + (" — CONCURRENT" if cvm else "")),
                    ("Concurrency", (f"{cvm.status.upper()} · sep "
                                     f"{cvm.sep_m:,.0f} m ≥ min "
                                     f"{cvm.req_m:,.0f} m (ASSUMED)"
                                     if cvm else "—")),
                    ("Safety stock", f"{p.safety_stock_t:,.0f} t"
                     + (" — BELOW ⚠" if p.below_safety else "")),
                    ("Quality", " · ".join(f"{k} {v:g}"
                                           for k, v in p.quality.items())
                     or "—"),
                    ("Evidence", f"{p.evidence_status} · "
                                 f"{p.evidence_source[:48]}"),
                ])
        else:
            sel = st.selectbox("Conveyor", opts_c, key=f"{key}_asset_sel")
            cg = next((c_ for c_ in geo.conveyors if c_.cid == sel), None)
            if cg:
                state, tph, cap, _, _ = _conv_state(st_, cg, opts)
                li0 = next((sc_l for sc_l in sc.links
                            if sc_l.id == (cg.link_ids[0]
                                           if cg.link_ids else "")), None)
                theme.mini_kpis([
                    ("Tag", cg.tag or cg.cid),
                    ("Model links", ", ".join(cg.link_ids) or
                     "display-only (planning_enabled=False)"),
                    ("State", state),
                    ("Flow / capacity", (f"{tph:,.0f} / {cap:,.0f} t/h"
                                         if tph is not None and cap else "—")),
                    ("Transit", (f"{li0.transit_buckets * sc.sim.dt_h:g} h"
                                 if li0 and li0.transit_buckets else "—")),
                    ("Evidence", li0.evidence.status if li0 else "Review"),
                ])

    # ---- data gaps -----------------------------------------------------------------
    gaps = list(geo.gaps) + [g for g in ms.gaps]
    with st.expander(f"Model coverage / geometry & data gaps ({len(gaps)})",
                     expanded=False):
        st.caption("Geometry is schematic — machine-corridor TOPOLOGY per "
                   "the user layout; metric positions await an approved "
                   "GA/plot plan. Display-only assets are never solver resources.")
        import pandas as pd
        theme.show_df(pd.DataFrame(
            [{"site": g[0], "item": g[1], "status": g[2]} for g in gaps]))
        if ms.discrepancies:
            st.error("Coverage discrepancies: " + " · ".join(ms.discrepancies))


def render_global_flow(sc: Scenario, plan, sim) -> None:
    """Page 15 — Global Stockyard Layout & Flow (Monitor section)."""
    import streamlit as st
    st.subheader("Global Stockyard Layout & Flow")
    st.caption("LOUTA (Mine + Beneficiation SMS) —C1→ MPH M16 ←C2— "
               "BENGUERIR (Mine Stockyard SMS) · → PAP / DRY-FEED · "
               "every modelled stockpile on its lane, machines on their "
               "rails · planning/monitoring only — no live control")
    render_process_mimic(sc, plan, sim,
                         views=["CHAIN", "LOUTA", "BENGUERIR", "MPH"],
                         default_view="CHAIN", key="gpf",
                         alarm_summary=True, drill_buttons=False,
                         collapse_controls=True, redesign=True)
