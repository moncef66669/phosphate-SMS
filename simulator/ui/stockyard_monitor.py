"""Stockyard Monitor — dynamic, data-driven phosphate yard visualization.

Layers in this module:
  1. deterministic grade palette (grade_color)
  2. immutable view-model (build_monitor_state) — PURE, no Streamlit:
     reads Scenario / PlanResult / Simulator history, never mutates them
  3. Plotly site schematics (site_figure) — geometry is SCHEMATIC ONLY. At
     M16 the eight hangars and their four-bay / machine-line sequence are
     confirmed (PFD-0001[3]); the hangar names inside a bay are inferred and
     the physical bay arrangement is drawn by the process mimic, not here
  4. Streamlit glue (render_monitor)

Visual channels (kept separate on purpose):
  fill colour   = grade / product profile
  border        = operating state (green active · amber warning ·
                  red hard violation · grey idle) — dashed = ASSUMED/TBC
  icons/labels  = textual duplication so nothing depends on colour alone
"""
from __future__ import annotations

import datetime as _dt
import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import plotly.graph_objects as go

from mph_sim.coordinates import (format_repere, job_repere_label, x_to_repere)
from mph_sim.domain import PlanResult, Scenario
from mph_sim.flow_config import destination_line_operating_state

from .theme import (AMBER, BLUE, DGREEN, DORANGE, GREEN, GREY, NAVY, RED,
                    TEXT_GREY, operator_text, plotly_layout)

PHOSPHATE_SITES = ["LOUTA", "BENGUERIR", "MPH"]   # display order
SITE_TITLES = {
    "LOUTA": "LOUTA mine stockyard",
    "BENGUERIR": "BENGUERIR mine stockyard",
    "MPH": "MPH M16 phosphate hub",
}

# ---- deterministic grade palette --------------------------------------------
GRADE_COLORS = {          # fixed brief colours
    "TBT": "#1f7a8c",     # teal/blue
    "BT": "#d98b2b",      # amber
    "MT": "#6a3fa0",      # violet
    "RAW-BT-GLOBAL": "#3a7ca5",
    "RAW-BT-HIGH-CD": "#7a4e6d",
    "RAW-BT-LOW-CD": "#a05a3f",
    "": "#b8c2cb",        # empty -> neutral grey
}
_FALLBACK = ["#3a7ca5", "#8c5e2a", "#5c7a4e", "#7a4e6d", "#4e6d7a", "#a05a3f"]
UNKNOWN_GREY = "#8d979f"


def grade_color(grade_id: Optional[str]) -> str:
    """Stable colour for any grade id; unknown grades get a deterministic
    fallback (same id -> same colour, forever)."""
    g = (grade_id or "").strip()
    if g in GRADE_COLORS:
        return GRADE_COLORS[g]
    if not g:
        return GRADE_COLORS[""]
    h = int(hashlib.md5(g.encode()).hexdigest(), 16)
    return _FALLBACK[h % len(_FALLBACK)]


# ---- expected coverage (audited against the ACTIVE scenario) ------------------
EXPECTED_COVERAGE = {
    "MPH": {"spaces": {f"HP-{i:02d}" for i in range(1, 9)},
            "machines": {"ST01-A", "ST01-B", "ST01-C", "RC01-A", "RC01-B"}},
    "BENGUERIR": {
        "spaces": {
            "BG-MINE-1", "BG-MINE-2",
            "BG-RAW-1", "BG-RAW-2",
            "BG-P1", "BG-P2",
        },
        "machines": {
            "BG-MINE-ST-1", "BG-MINE-ST-2", "BG-MINE-BWR",
            "BG-RAW-ST-1", "BG-RAW-ST-2", "BG-RAW-BWR",
            "ST3", "RC2", "RC3",
        },
    },
    "LOUTA": {
        "spaces": {
            "110LG-OL1", "110LG-OL2",
            "LT-P1", "LT-P2",
        },
        "machines": {
            "110LG-ST-01", "120LG-RC-01", "120LG-RC-02",
            "130LG-ST-01", "140LG-RC-01", "140LG-RC-02",
        },
    },
}
# report-mentioned equipment NOT represented in the optimisation model —
# shown in the coverage panel, never silently added to the model
REPORT_UNMODELLED = [
    ("BENGUERIR", "B01 2023 screening device tags "
     "(04BG-DT-01 · 04BG-RC-01, 2,200 t/h stacker)",
     "TM1/TSM2/TSM3 are represented as finite planning interfaces and the "
     "six-pile material chain is solver-controlled. The exact 04BG device "
     "tag mapping and phase relationship are still unreconciled, so these "
     "tagged devices are not asserted as additional independent machines."),
    ("LOUTA", "102MG screened-product storage — MINE STOCKPILE-01/-02 + "
     "dedicated stackers 102MG-ST-01/-02 + shared reclaimer 110MG-RC-01",
     "MODELLING_GAP[MISSING_ROUTE_RATE_REACH] · PRE-beneficiation mine "
     "storage: L01 Rev-3 documents the topology, 2,200 t/h stackers and "
     "110MG-CB-01 toward beneficiation. Actual capacities/live mass, "
     "reclaimer rate, metric reach/positions and executable L01→L02 route "
     "integration are not loaded. S3-A25 assumes 100,000 t display "
     "capacity + 50,000 t t0 mass per pile (50%) with 50/50 raw-profile "
     "bands. Display/reference only; 102MG never feeds C1 directly and the "
     "494,274 t homogenized ore-storage capacity is NOT theirs"),
    ("MPH", "M16A-D-ST01 (ST01-D*) / M16A-C-RC01 (RC01-C*) + 4 × 125,000 t "
     "future hangars (PFD-0001[3] FUTURE EXTENSION · PRO-0001 §2.1)",
     "future configuration items — Phase 1 model excludes them; drawn only "
     "behind the mimic's Future-expansion toggle and never solver resources"),
]


# ================================ view-model ==================================
@dataclass
class SegVM:
    frac_from: float
    frac_to: float
    grade: str
    mass_t: float
    actual: bool = True        # True = real Segment data, False = fallback
    quality: Dict[str, float] = field(default_factory=dict)
    basis: str = ""            # actual segment | explicit zone | assumed mix


@dataclass
class PileVM:
    id: str
    site: str
    mass_t: float
    capacity_t: float
    fill_pct: float
    grade: str
    grade_label: str
    designated_grade: str
    state: str
    safety_stock_t: float
    below_safety: bool
    offspec_hard: List[str]
    quality: Dict[str, float]
    evidence_status: str
    evidence_source: str
    segments: Optional[List[SegVM]]
    mixed: bool
    inbound_tph: float = 0.0
    outbound_tph: float = 0.0
    active: bool = False
    warnings: List[str] = field(default_factory=list)


@dataclass
class MachineVM:
    id: str
    site: str
    kind: str                  # stacker | reclaimer
    status: str                # Idle|Stacking|Reclaiming|Maintenance|Unavailable
    job_id: str = ""
    target_space: str = ""
    dest_id: str = ""
    rate_tph: float = 0.0
    position_x: Optional[float] = None
    reachable: List[str] = field(default_factory=list)
    evidence_status: str = "ASSUMED"
    rate_max_tph: float = 0.0
    lifecycle: str = "EXISTING"        # OPTIONAL_PROPOSED = documented option
    planning_enabled: bool = True      # False -> planner never assigns jobs
    rate_design_tph: float = 0.0       # documented DESIGN capacity (display)
    physical_tag: str = ""


@dataclass
class CorridorVM:
    id: str
    from_site: str
    capacity_tph: float
    transit_h: float
    in_transit_t: Optional[float]
    next_arrival: str
    dispatch_tph: Optional[float]


@dataclass
class SiteVM:
    id: str
    title: str
    schematic_note: str
    piles: List[PileVM]
    machines: List[MachineVM]
    inflow_label: str
    outlets: List[Tuple[str, bool, str]]   # (label, active, note)
    banner: str = ""


@dataclass
class MonitorState:
    basis: str                 # initial | plan | sim
    bucket: int
    max_bucket: int
    scenario_name: str
    sites: Dict[str, SiteVM]
    corridors: List[CorridorVM]
    gaps: List[Tuple[str, str, str]]
    discrepancies: List[str]
    message: Optional[str]
    generated_at: str = ""


def _hard_violations(sc: Scenario, quality: Dict[str, float]) -> List[str]:
    out = []
    for d in sc.destinations:
        for s in d.spec:
            if not s.hard:
                continue
            v = quality.get(s.param)
            if v is None:
                continue
            if (s.pmin is not None and v < s.pmin - 1e-9) or \
                    (s.pmax is not None and v > s.pmax + 1e-9):
                out.append(f"{s.param} vs {d.id}")
    return out


def _segments_vm(sc: Scenario, pile_id: str, mass: float) -> Optional[List[SegVM]]:
    # experimental multi-grade zones: EXPLICIT zone inputs take precedence
    # over inventory segments — they are the declared grade layout of the
    # pile. Zone data reflects the INITIAL basis only (the plan/simulation
    # bases keep the aggregate dominant-grade view — zone tracking through
    # plan/sim is not implemented; the caller adds that caveat).
    from mph_sim.zones import zones_enabled, zones_for
    try:
        pile = sc.space(pile_id)
        pile_len = max(pile.x_to - pile.x_from, 1e-9)
    except StopIteration:
        pile = None
        pile_len = 1.0
    if zones_enabled(sc):
        zs = zones_for(sc, pile_id)
        if zs and pile is not None:
            out = []
            for z in sorted(zs, key=lambda z_: z_.x_from):
                f0 = max(0.0, min(1.0,
                         (z.x_from - pile.x_from) / pile_len))
                f1 = max(0.0, min(1.0,
                         (z.x_to - pile.x_from) / pile_len))
                grade = sc.grade(z.grade) if z.grade else None
                out.append(SegVM(f0, f1,
                                 z.grade or "", z.mass_t, actual=True,
                                 quality=(dict(z.quality) if z.quality else
                                          dict(grade.quality) if grade else {}),
                                 basis="explicit pile zone"))
            return out
    segs = [g for g in sc.segments if g.space_id == pile_id]
    if segs and pile is not None:
        tot = sum(g.mass_t for g in segs)
        if tot <= 0 or not (0.6 <= tot / max(mass, 1) <= 1.4):
            return None        # stale segment snapshot — do NOT fabricate
        out = []
        for g in sorted(segs, key=lambda g: g.x_from):
            f0 = max(0.0, min(1.0,
                     (g.x_from - pile.x_from) / pile_len))
            f1 = max(0.0, min(1.0,
                     (g.x_to - pile.x_from) / pile_len))
            grade = sc.grade(g.grade) if g.grade else None
            out.append(SegVM(
                f0, f1, g.grade, g.mass_t, actual=True,
                quality=(dict(g.quality) if g.quality else
                         dict(grade.quality) if grade else {}),
                basis="inventory segment"))
        return out

    # Configured grade-profile distributions are mass-share compositions, not
    # surveyed longitudinal segments. They are used only when no explicit
    # zone or live/inventory segment snapshot exists, so operational data
    # always wins over the demonstration fallback.
    cfg = getattr(sc, "config", {}) or {}
    by_pile = cfg.get("PILE_GRADE_DISTRIBUTIONS", {})
    rows = by_pile.get(pile_id, []) if isinstance(by_pile, dict) else []
    if not isinstance(rows, list) or not rows:
        return None
    parsed = []
    for row in rows:
        if not isinstance(row, dict):
            return None
        try:
            share = float(row.get("mass_fraction", 0.0))
        except (TypeError, ValueError):
            return None
        grade_id = str(row.get("grade_id", "") or "")
        grade = sc.grade(grade_id)
        if share <= 0 or grade is None:
            return None
        parsed.append((share, grade_id, grade))
    total = sum(share for share, _, _ in parsed)
    if abs(total - 1.0) > 1e-9:
        return None
    basis_by_pile = cfg.get("PILE_GRADE_DISTRIBUTION_BASIS", {})
    basis = (str(basis_by_pile.get(pile_id, "") or "")
             if isinstance(basis_by_pile, dict) else str(basis_by_pile or ""))
    acc = 0.0
    out = []
    for share, grade_id, grade in parsed:
        f0 = acc
        acc += share
        out.append(SegVM(
            f0, acc, grade_id, mass * share, actual=False,
            quality=dict(grade.quality),
            basis=basis or "ASSUMED grade-profile mass distribution"))
    return out


def _jobs_active(plan: Optional[PlanResult], bucket: int):
    if plan is None:
        return []
    return [j for j in plan.jobs
            if j.start_bucket <= bucket <= j.end_bucket
            and j.status not in ("CANCELLED", "REJECTED")]


def build_monitor_state(sc: Scenario, plan: Optional[PlanResult] = None,
                        sim=None, basis: str = "auto",
                        bucket: Optional[int] = None) -> MonitorState:
    """Immutable snapshot for the monitor. Never mutates its inputs."""
    if basis == "auto":
        basis = "sim" if sim is not None else ("plan" if plan is not None
                                               else "initial")
    if basis == "sim" and sim is None:
        basis = "plan" if plan is not None else "initial"
    if basis == "plan" and plan is None:
        basis = "initial"

    T = sc.sim.horizon_buckets
    # Plan inventory is a boundary series of length T+1. Bucket T is the
    # true terminal state after the final work bucket, with no active jobs.
    max_bucket = {"initial": 0, "plan": T,
                  "sim": (sim.bucket if sim else 0)}[basis]
    b = max(0, min(bucket if bucket is not None else max_bucket, max_bucket))

    phosphate_sites = [s.id for s in sc.sites
                       if s.id in PHOSPHATE_SITES
                       and any(sp.site == s.id and not sp.area.startswith("M17")
                               for sp in sc.spaces)]
    if not phosphate_sites:
        return MonitorState(basis, b, max_bucket, sc.name, {}, [], [], [],
                            "No phosphate stockyards in this sulphur scenario.",
                            _dt.datetime.now().strftime("%H:%M:%S"))

    def _corridor_hours(link_id: str) -> float:
        try:
            return sc.link(link_id).transit_buckets * sc.sim.dt_h
        except (KeyError, StopIteration):
            return 0.0

    node_site = {n.id: n.site for n in sc.nodes}

    # ---- per-bucket mass / quality / activity per basis ---------------------
    def pile_mass(sid: str, sp) -> float:
        if basis == "plan" and sid in plan.inventory:
            ser = plan.inventory[sid]
            return float(ser[min(b, len(ser) - 1)])
        if basis == "sim":
            ser = sim.inv_series.get(sid, [sp.mass_t])
            return float(ser[min(b, len(ser) - 1)])
        return float(sp.mass_t)

    def pile_quality(sid: str) -> Dict[str, float]:
        if basis == "sim":
            ser = sim.quality_series.get(sid)
            if ser:
                return dict(ser[min(b, len(ser) - 1)])
        return dict(sc.quality_of(sid))     # plan model uses the snapshot

    if basis == "sim":
        acts = [a for a in sim.activity_log if a["bucket"] == b]
    else:
        acts = []
    jobs_b = _jobs_active(plan, b) if basis == "plan" else []

    def pile_rates(sid: str) -> Tuple[float, float]:
        inbound = outbound = 0.0
        if basis == "sim":
            for a in acts:
                if a["space"] != sid:
                    continue
                if a["kind"] == "stack":
                    inbound += a["rate_tph"]
                else:
                    outbound += a["rate_tph"]
        elif basis == "plan":
            for j in jobs_b:
                if j.space != sid:
                    continue
                if j.kind == "stack":
                    inbound += j.rate_tph
                else:
                    outbound += j.rate_tph
        return inbound, outbound

    def machine_job(mid: str) -> Tuple[str, str, str, str, float]:
        """(status, job_id, space, dest, rate) for machine at bucket b."""
        if basis == "sim":
            for a in acts:
                if a["machine"] == mid:
                    kind = "Stacking" if a["kind"] == "stack" else "Reclaiming"
                    return kind, str(a["job"]), str(a["space"]), \
                        str(a["dest_id"]), float(a["rate_tph"])
        elif basis == "plan":
            for j in jobs_b:
                if j.machine == mid:
                    kind = "Stacking" if j.kind == "stack" else "Reclaiming"
                    return kind, j.id, j.space, j.dest_id, j.rate_tph
        return "Idle", "", "", "", 0.0

    # ---- build sites ----------------------------------------------------------
    sites: Dict[str, SiteVM] = {}
    for site_id in [s for s in PHOSPHATE_SITES if s in phosphate_sites]:
        piles: List[PileVM] = []
        for sp in sc.spaces:
            if (sp.site != site_id or sp.area.startswith("M17")
                    or sp.area in {"mine-interface", "process-interface"}):
                continue
            mass = pile_mass(sp.id, sp)
            q = pile_quality(sp.id)
            fill = max(0.0, min(100.0, 100.0 * mass / max(sp.capacity_t, 1)))
            inbound, outbound = pile_rates(sp.id)
            segs = _segments_vm(sc, sp.id, mass) if basis != "sim" else None
            seg_grades = {g.grade for g in segs} if segs else set()
            mixed = (sp.state == "mixed" and len(seg_grades) > 1) or \
                len(seg_grades) > 1
            below = sp.safety_stock_t > 0 and mass < sp.safety_stock_t - 1e-6
            hard = _hard_violations(sc, q) if mass > 0 else []
            warnings = []
            if below:
                warnings.append(f"below safety stock ({mass:,.0f} t < "
                                f"{sp.safety_stock_t:,.0f} t)")
            if hard:
                warnings.append("HARD spec violation: " + ", ".join(hard))
            if sp.evidence.status in ("ASSUMED", "TBC"):
                warnings.append(f"data {sp.evidence.status}")
            from mph_sim.zones import zones_enabled as _zen, zones_for as _zf
            if _zen(sc) and _zf(sc, sp.id):
                warnings.append(
                    "multi-grade zones (experimental): slices show the "
                    "EXPLICIT zone inputs — initial basis; the "
                    f"{basis} view's mass/quality remain the pile "
                    "aggregate (zone tracking through plan/simulation "
                    "not implemented)")
            elif segs and any(not seg.actual for seg in segs):
                warnings.append(
                    "ASSUMED grade-profile mass distribution: coloured "
                    "bands are proportional composition, not surveyed "
                    "spatial order; planning uses the aggregate "
                    "mass-weighted pile quality")
            grade_label = sp.grade if mass > 1 else (
                f"designated {sp.designated_grade}" if sp.designated_grade
                else "empty")
            piles.append(PileVM(
                id=sp.id, site=site_id, mass_t=mass, capacity_t=sp.capacity_t,
                fill_pct=fill, grade=sp.grade if mass > 1 else "",
                grade_label=grade_label + (" · mixed" if mixed else ""),
                designated_grade=sp.designated_grade, state=sp.state,
                safety_stock_t=sp.safety_stock_t, below_safety=below,
                offspec_hard=hard, quality=q,
                evidence_status=sp.evidence.status,
                evidence_source=sp.evidence.source,
                segments=segs, mixed=mixed,
                inbound_tph=inbound, outbound_tph=outbound,
                active=(inbound + outbound) > 0, warnings=warnings))

        machines: List[MachineVM] = []
        m17_ids = {sp.id for sp in sc.spaces if sp.area.startswith("M17")}
        for m in sc.machines:
            if m.site != site_id or m.kind not in ("stacker", "reclaimer"):
                continue
            if m.reachable_spaces and set(m.reachable_spaces) <= m17_ids:
                continue        # sulphur (Domain B) equipment
            status, job_id, space, dest, rate = machine_job(m.id)
            if not m.available:
                status = "Unavailable"
            elif sim is not None and basis == "sim" and b == sim.bucket \
                    and sim.bucket < sim.machine_down_until.get(m.id, -1):
                status = "Unavailable"
            elif status == "Idle" and sc.machine_blocked(m.id, b):
                status = "Maintenance"
            machines.append(MachineVM(
                id=m.id, site=site_id, kind=m.kind, status=status,
                job_id=job_id, target_space=space, dest_id=dest,
                rate_tph=rate,
                position_x=(sim.machine_pos.get(m.id) if (sim and basis == "sim")
                            else m.position_x),
                reachable=list(m.reachable_spaces),
                evidence_status=m.evidence.status,
                rate_max_tph=m.rate_max_tph,
                lifecycle=getattr(m, "lifecycle", "EXISTING"),
                planning_enabled=getattr(m, "planning_enabled", True),
                rate_design_tph=getattr(m, "rate_design_tph", 0.0),
                physical_tag=getattr(m, "physical_tag", "")))

        if site_id == "MPH":
            inflow = ("⇐ reception from C1 (Louta) & C2 (Benguerir) · "
                      "T01/T02 ≈2,600 t/h → T03/T04 diverters → the three "
                      "stacker lines")
            pap2_state = destination_line_operating_state(sc, "PAP-2")
            pap2_available = pap2_state == "AVAILABLE"
            outlets = [("PAP-1 (wet process) — via gate bank "
                        "M16A-A-H01…H04 or M16A-B-H01…H04", True,
                        "humid feed — dry NEVER to PAP; 6 × 1,500 t/h PAP "
                        "conveyors (3 running)"),
                       (f"PAP-2 (wet process) — {pap2_state}",
                        pap2_available,
                        "same feed duty as PAP-1; selectable in Configure"),
                       ("DRY-FEED → drying → dry store → rail → SPH", True,
                        "2 × 1,299 t/h to the drying unit (OUT OF SCOPE); "
                        "PAP/drying route confluence TBC")]
            note = ("8 × 125,000 t hangars in FOUR two-hangar bays with the "
                    "machine lines between them (M16A-A-T06 · M16A-A-T11 · "
                    "M16A-B-T06 · M16A-B-T11 · M16A-C-T06) — sequence "
                    "CONFIRMED by PFD-0001[3]; each machine serves only its "
                    "adjacent bays · hangar numbering inside a bay INFERRED · "
                    "in-lane geometry schematic")
            banner = ""
        elif site_id == "BENGUERIR":
            cfg = getattr(sc, "config", {}) or {}
            raw_on = bool(cfg.get("BEN_RAW_TO_C2_ENABLED", False))
            inflow = ("⇐ TM1/TSM2/TSM3 finite mine-hopper interfaces → "
                      "2 mine-ore piles → 2 screened/raw piles → "
                      "3 washing campaigns → ST3")

            def _train_on(did: str) -> bool:
                d = next((d_ for d_ in sc.destinations if d_.id == did), None)
                return bool(d and (d.demand_default_tph > 0 or any(
                    e.dest_id == did and e.rate_tph > 0 for e in sc.demand)))
            outlets = [
                (f"washed product → C2 → M16 (2,600 t/h nom · "
                 f"transit time = {_corridor_hours('C2'):g} h)", True,
                 ""),
                ("raw ore → C2 (conditional)", raw_on,
                 "requires phase-1 250 m³ hopper mod and/or RB9**/RB10** — "
                 + ("ENABLED" if raw_on else "disabled")),
                ("raw ore → train (1,000/1,200 t/h)",
                 _train_on("BG-TRAIN-RAW"),
                 "editable demonstration demand; one shared station assumed"),
                ("washed product → train (1,000/1,200 t/h)",
                 _train_on("BG-TRAIN-PROD"),
                 "editable demonstration demand; one shared station assumed"),
            ]
            note = "SCHEMATIC — NOT TO SCALE"
            banner = (
                "Six physical piles and nine mobile machines are "
                "solver-controlled. BG-MINE-1/-2 capacity (200,000 t each), "
                "their machine identities and the three-line campaign "
                "allocation are explicit simulation assumptions. B02 "
                "confirms ST3/RC2/RC3; '**' is source notation only.")
        else:  # LOUTA
            inflow = ("⇐ finite S0 mine-feed boundary buffers → 110LG ore "
                      "stacking → L1/L2 washing")
            outlets = [(f"C1 → M16 (2,600 t/h · transit time = "
                        f"{_corridor_hours('C1'):g} h)", True, "")]
            note = "SCHEMATIC — NOT TO SCALE"
            banner = ("Process configuration CONFIRMED by the Louta process "
                      "description: 102MG mine storage (2 lines) → 2 "
                      "homogenized ore stockpiles (1 shared stacker "
                      "110LG-ST-01 · 2,100 t/h wet chevron · 2 bridge "
                      "reclaimers 120LG-RC-01/-02 · 3,000 t/h wet) → washing "
                      "L1 + L2 (3 Mt tsm/year each) → product storage (1 "
                      "shared stacker 130LG-ST-01 · reclaimers "
                      "140LG-RC-01/-02 · 3,000 t/h) → C1. The 110/120LG "
                      "ore stage, L1/L2 process transfers, 130/140LG product "
                      "stage and C1 dispatch are solver-controlled. ⚠ Still "
                      "TBC: metric layout/GA, individual pile capacities "
                      "(494,274 t ore "
                      "and 383,024 t product totals split equally = DERIVED), "
                      "reclaimer↔pile pairing, live masses and grade bands "
                      "(50% fills and profile shares ASSUMED) · physical "
                      "product-pile count not stated — LT-P1/P2 are planning "
                      "lines · 102MG remains reference-only because route, "
                      "rate and reach are missing · 'TO DIGUE (OPTION-2)' "
                      "excluded from Phase 1")
        sites[site_id] = SiteVM(site_id, SITE_TITLES[site_id], note, piles,
                                machines, inflow, outlets, banner)

    # ---- corridors --------------------------------------------------------------
    corridors: List[CorridorVM] = []
    for l in sc.links:
        if l.transit_buckets <= 0:
            continue
        from_site = node_site.get(l.src, "?")
        if from_site not in phosphate_sites and from_site != "MPH":
            continue
        if basis == "sim" and sim is not None and b == sim.bucket:
            parcels = [p for p in sim.belts if p.link_id == l.id
                       and p.arrive_bucket >= b]
            in_transit = sum(p.mass_t for p in parcels)
            nxt = min((p.arrive_bucket for p in parcels), default=None)
            dispatch = sum(a["rate_tph"] for a in acts
                           if a["kind"] == "dispatch" and a["dest_id"] == l.id)
        else:
            entries = [e for e in sc.belt_profile if e.link_id == l.id
                       and e.arrive_bucket >= b]
            in_transit = sum(e.mass_t for e in entries) if entries else (
                None if basis == "sim" else 0.0)
            nxt = min((e.arrive_bucket for e in entries), default=None)
            dispatch = sum(j.rate_tph for j in jobs_b
                           if j.kind == "dispatch" and j.dest_id == l.id) \
                if basis == "plan" else None
        corridors.append(CorridorVM(
            id=l.id, from_site=from_site, capacity_tph=l.capacity_tph,
            transit_h=l.transit_buckets * sc.sim.dt_h,
            in_transit_t=in_transit,
            next_arrival=(sc.sim.bucket_dt(nxt).strftime("%d %b %H:%M")
                          if nxt is not None else "—"),
            dispatch_tph=dispatch))

    # ---- coverage audit -----------------------------------------------------------
    discrepancies: List[str] = []
    for site_id, exp in EXPECTED_COVERAGE.items():
        if site_id not in sites:
            continue
        got_sp = {p.id for p in sites[site_id].piles}
        got_m = {m.id for m in sites[site_id].machines}
        for missing in sorted(exp["spaces"] - got_sp):
            discrepancies.append(f"{site_id}: expected space {missing} "
                                 "not in scenario")
        for extra in sorted(got_sp - exp["spaces"]):
            discrepancies.append(f"{site_id}: unexpected space {extra} "
                                 "(update EXPECTED_COVERAGE?)")
        for missing in sorted(exp["machines"] - got_m):
            discrepancies.append(f"{site_id}: expected machine {missing} "
                                 "not in scenario")
        for extra in sorted(got_m - exp["machines"]):
            discrepancies.append(f"{site_id}: unexpected machine {extra} "
                                 "(update EXPECTED_COVERAGE?)")
    gaps = [g for g in REPORT_UNMODELLED if g[0] in sites]

    return MonitorState(
        basis=basis, bucket=b, max_bucket=max_bucket, scenario_name=sc.name,
        sites=sites, corridors=corridors, gaps=gaps,
        discrepancies=discrepancies, message=None,
        generated_at=_dt.datetime.now().strftime("%H:%M:%S"))


# ================================ drawing ======================================
_STATUS_BORDER = {"active": GREEN, "warning": AMBER, "critical": RED,
                  "idle": "#5b7fa6"}
_STATUS_ICON = {"Stacking": "▶", "Reclaiming": "◀", "Idle": "·",
                "Maintenance": "🛠", "Unavailable": "✖"}


def _pile_border(p: PileVM) -> Tuple[str, Optional[str], float]:
    """(colour, dash, width) — status channel, separate from grade fill."""
    if p.offspec_hard:
        col = RED
    elif p.below_safety:
        col = AMBER
    elif p.active:
        col = GREEN
    else:
        col = _STATUS_BORDER["idle"]
    dash = "dash" if p.evidence_status in ("ASSUMED", "TBC") else None
    width = 3 if (p.offspec_hard or p.below_safety or p.active) else 1.6
    return col, dash, width


def _pile_hover(p: PileVM, params: List[str]) -> str:
    lines = [f"<b>{p.id}</b> — {p.grade_label}",
             f"{p.mass_t:,.0f} / {p.capacity_t:,.0f} t ({p.fill_pct:.0f} %)",
             f"state: {p.state} · evidence: {p.evidence_status}"]
    qd = " · ".join(f"{k} {p.quality[k]:g}" for k in params if k in p.quality)
    if qd:
        lines.append(qd)
    if p.safety_stock_t > 0:
        lines.append(f"safety stock {p.safety_stock_t:,.0f} t"
                     + (" — BELOW" if p.below_safety else " ✓"))
    if p.inbound_tph or p.outbound_tph:
        lines.append(f"in {p.inbound_tph:,.0f} t/h · out {p.outbound_tph:,.0f} t/h")
    if p.segments:
        if all(seg.actual for seg in p.segments):
            lines.append(f"{len(p.segments)} inventory segments (actual data)")
        else:
            split = " / ".join(
                f"{seg.grade} {seg.mass_t / max(p.mass_t, 1):.0%}"
                for seg in p.segments)
            lines.append("assumed grade-profile distribution: " + split)
    elif p.mixed:
        lines.append("mixed — dominant-grade fallback (no usable segments)")
    if p.warnings:
        lines.append("⚠ " + " · ".join(p.warnings))
    lines.append(f"source: {p.evidence_source[:60]}")
    return "<br>".join(lines)


def site_figure(sv: SiteVM, show_reachability: bool = False,
                selected_machine: str = "", show_flows: bool = True,
                show_safety: bool = True, show_evidence: bool = True,
                grade_filter: Optional[List[str]] = None,
                quality_params: Optional[List[str]] = None,
                large: bool = False) -> go.Figure:
    """One yard schematic. Lanes are vertical-stacked horizontal bars;
    stackers on the left rail, reclaimers on the right rail (SCHEMATIC)."""
    fig = go.Figure()
    params = quality_params or ["BPL", "Cd", "MgO", "SiO2", "moisture"]
    n = len(sv.piles)
    LANE_W, LANE_H, GAP = 10.0, 0.62, 0.38
    y0 = 0.0
    reach = set()
    if show_reachability and selected_machine:
        m = next((m for m in sv.machines if m.id == selected_machine), None)
        if m:
            reach = set(m.reachable)

    hover_x, hover_y, hover_t = [], [], []
    for i, p in enumerate(sv.piles):
        y = y0 - i * (LANE_H + GAP)
        represented_grades = ({seg.grade for seg in p.segments}
                              if p.segments else {p.grade})
        dim = (grade_filter and not represented_grades.intersection(
            grade_filter)) or (reach and p.id not in reach)
        alpha = 0.25 if dim else 1.0
        # capacity outline
        bcol, bdash, bw = _pile_border(p)
        if not show_evidence:
            bdash = None
        fig.add_shape(type="rect", x0=0, x1=LANE_W, y0=y - LANE_H, y1=y,
                      line=dict(color=bcol, dash=bdash, width=bw),
                      fillcolor="#f4f6f8", opacity=alpha)
        # fill (grade colour; real segments or assumed mass-share bands)
        fill_w = LANE_W * p.fill_pct / 100.0
        if p.segments and fill_w > 0:
            for g in p.segments:
                seg_col = (grade_color(g.grade)
                           if not grade_filter or g.grade in grade_filter
                           else "#dfe4e7")
                fig.add_shape(type="rect",
                              x0=fill_w * g.frac_from, x1=fill_w * g.frac_to,
                              y0=y - LANE_H, y1=y, line_width=0,
                              fillcolor=seg_col, opacity=0.85 * alpha)
                if g.frac_to < 1:      # segment/composition boundary marker
                    fig.add_shape(type="line", x0=fill_w * g.frac_to,
                                  x1=fill_w * g.frac_to, y0=y - LANE_H, y1=y,
                                  line=dict(color="#ffffff", width=1.5))
        elif fill_w > 0.05:
            fig.add_shape(type="rect", x0=0, x1=fill_w, y0=y - LANE_H, y1=y,
                          line_width=0, fillcolor=grade_color(p.grade),
                          opacity=0.85 * alpha)
        elif p.designated_grade:       # empty designated lane: thin marker
            fig.add_shape(type="rect", x0=0, x1=LANE_W, y0=y - LANE_H, y1=y,
                          line=dict(color=grade_color(p.designated_grade),
                                    dash="dot", width=1.2), opacity=alpha)
        # safety-stock threshold tick
        if show_safety and p.safety_stock_t > 0 and p.capacity_t > 0:
            xs = LANE_W * p.safety_stock_t / p.capacity_t
            fig.add_shape(type="line", x0=xs, x1=xs, y0=y - LANE_H - 0.07,
                          y1=y + 0.07, line=dict(color=RED, width=2, dash="dot"))
        # labels: id (left) · content (in-lane) · status icon (right)
        fig.add_annotation(x=-0.25, y=y - LANE_H / 2, text=f"<b>{p.id}</b>",
                           showarrow=False, xanchor="right",
                           font=dict(size=13, color=NAVY))
        grades = ([seg.grade for seg in p.segments]
                  if p.segments else ([p.grade] if p.grade else []))
        inlane = (f"{p.mass_t / 1000:,.1f} kt ({p.fill_pct:.0f}%)"
                  + (f" · {'/'.join(grades)}" if grades else ""))
        fig.add_annotation(x=LANE_W / 2, y=y - LANE_H / 2, text=inlane,
                           showarrow=False, font=dict(size=12, color="#1a2b38"))
        icon = "⚠" if p.below_safety else ("⛔" if p.offspec_hard else
                                           ("▶◀"[0] if p.inbound_tph else
                                            ("◀" if p.outbound_tph else "")))
        badge = p.evidence_status if (show_evidence and p.evidence_status
                                      in ("ASSUMED", "TBC")) else ""
        right = " ".join(x for x in (icon, badge) if x)
        if right:
            fig.add_annotation(x=LANE_W + 0.25, y=y - LANE_H / 2, text=right,
                               showarrow=False, xanchor="left",
                               font=dict(size=11, color=AMBER if badge else bcol))
        hover_x.append(LANE_W / 2)
        hover_y.append(y - LANE_H / 2)
        hover_t.append(_pile_hover(p, params))

    # invisible hover layer for piles
    fig.add_scatter(x=hover_x, y=hover_y, mode="markers",
                    marker=dict(size=28, opacity=0.001),
                    hovertext=hover_t, hoverinfo="text", showlegend=False)

    # ---- rails + machines -----------------------------------------------------
    y_top, y_bot = 0.35, y0 - n * (LANE_H + GAP) + GAP - 0.35
    stack_x, reclaim_x = -1.9, LANE_W + 1.9
    fig.add_shape(type="line", x0=stack_x, x1=stack_x, y0=y_bot, y1=y_top,
                  line=dict(color=GREY, width=2, dash="dot"))
    fig.add_shape(type="line", x0=reclaim_x, x1=reclaim_x, y0=y_bot, y1=y_top,
                  line=dict(color=GREY, width=2, dash="dot"))
    # NOTE: this is an inventory schematic, not the physical layout — the two
    # columns group the stacking and reclaim MACHINE LINES of the site. M16 has
    # three separate stacker lines and two separate reclaim lines interleaved
    # with the hangar bays; that physical arrangement is drawn by the process
    # mimic, never implied here.
    fig.add_annotation(x=stack_x, y=y_top + 0.25, text="stacking lines",
                       showarrow=False, font=dict(size=11, color=TEXT_GREY))
    fig.add_annotation(x=reclaim_x, y=y_top + 0.25, text="reclaim lines",
                       showarrow=False, font=dict(size=11, color=TEXT_GREY))

    lane_y = {p.id: y0 - i * (LANE_H + GAP) - LANE_H / 2
              for i, p in enumerate(sv.piles)}
    mk_x, mk_y, mk_t, mk_sym, mk_col = [], [], [], [], []
    park = {"stacker": y_top - 0.05, "reclaimer": y_top - 0.05}
    for m in sv.machines:
        x = stack_x if m.kind == "stacker" else reclaim_x
        if m.target_space and m.target_space in lane_y:
            y = lane_y[m.target_space]
        else:
            y = park[m.kind]
            park[m.kind] -= 0.55       # park idle machines along the rail top
        col = {"Stacking": GREEN, "Reclaiming": GREEN,
               "Maintenance": AMBER, "Unavailable": RED}.get(m.status, GREY)
        sel = (m.id == selected_machine)
        mk_x.append(x)
        mk_y.append(y)
        mk_sym.append("triangle-right" if m.kind == "stacker" else "triangle-left")
        mk_col.append(col)
        mk_t.append(f"<b>{m.id}</b> ({m.kind})<br>status: {m.status}"
                    + (f"<br>job {m.job_id}: {m.target_space}"
                       + (f" → {m.dest_id}" if m.dest_id else "")
                       + f" @ {m.rate_tph:,.0f} t/h" if m.job_id else "")
                    + f"<br>max {m.rate_max_tph:,.0f} t/h · "
                      f"evidence {m.evidence_status}"
                    + (f"<br>position x≈{m.position_x:,.0f} m (schematic rail)"
                       if m.position_x is not None else ""))
        lbl = f"{m.id}" + (f" · {m.rate_tph:,.0f} t/h" if m.rate_tph else "")
        fig.add_annotation(x=x + (-0.35 if m.kind == "stacker" else 0.35), y=y,
                           text=lbl, showarrow=False,
                           xanchor="right" if m.kind == "stacker" else "left",
                           font=dict(size=11,
                                     color=NAVY if not sel else DORANGE))
        # flows for active jobs
        if show_flows and m.job_id and m.target_space in lane_y:
            ly = lane_y[m.target_space]
            if m.status == "Stacking":
                fig.add_annotation(x=0.15, y=ly, ax=x + 0.25, ay=y,
                                   xref="x", yref="y", axref="x", ayref="y",
                                   showarrow=True, arrowhead=2, arrowwidth=2.5,
                                   arrowcolor=GREEN)
            else:
                fig.add_annotation(x=x - 0.25, y=y, ax=LANE_W - 0.15, ay=ly,
                                   xref="x", yref="y", axref="x", ayref="y",
                                   showarrow=True, arrowhead=2, arrowwidth=2.5,
                                   arrowcolor=GREEN)
    fig.add_scatter(x=mk_x, y=mk_y, mode="markers",
                    marker=dict(size=15, symbol=mk_sym, color=mk_col,
                                line=dict(color=NAVY, width=1.5)),
                    hovertext=mk_t, hoverinfo="text", showlegend=False)

    # ---- inflow / outlets -------------------------------------------------------
    fig.add_annotation(x=stack_x - 0.4, y=y_top + 0.75, text=sv.inflow_label,
                       showarrow=False, xanchor="left",
                       font=dict(size=12, color=TEXT_GREY))
    out_x = reclaim_x + 2.3
    for i, (label, active, note) in enumerate(sv.outlets):
        oy = y_top - 0.4 - i * 1.15
        col = DGREEN if active else GREY
        fig.add_shape(type="rect", x0=out_x, x1=out_x + 4.6, y0=oy - 0.45,
                      y1=oy + 0.45, line=dict(color=col, width=2,
                                              dash=None if active else "dash"),
                      fillcolor="#ffffff")
        fig.add_annotation(x=out_x + 2.3, y=oy + 0.12, text=f"<b>{label}</b>",
                           showarrow=False, font=dict(size=11.5, color=col))
        if note:
            fig.add_annotation(x=out_x + 2.3, y=oy - 0.2, text=note,
                               showarrow=False, font=dict(size=10, color=TEXT_GREY))
        if active:
            fig.add_annotation(x=out_x - 0.1, y=oy, ax=reclaim_x + 0.15, ay=oy,
                               xref="x", yref="y", axref="x", ayref="y",
                               showarrow=True, arrowhead=2, arrowwidth=2.5,
                               arrowcolor=DGREEN)

    # ---- banner / note -----------------------------------------------------------
    if sv.banner:
        fig.add_annotation(x=(LANE_W + 6) / 2 - 2, y=y_bot - 0.55,
                           text="⚠ " + sv.banner if not sv.banner.startswith("⚠")
                           else sv.banner,
                           showarrow=False, align="left",
                           font=dict(size=11, color="#7a5410"),
                           bgcolor="#fdf3e2", bordercolor=AMBER,
                           borderwidth=1, borderpad=4)
    fig.add_annotation(x=LANE_W + 6.8, y=y_bot - 0.15, text=sv.schematic_note,
                       showarrow=False, xanchor="right",
                       font=dict(size=10.5, color=TEXT_GREY))

    height = (150 if sv.banner else 110) + int(n * (58 if not large else 72)) \
        + 40 * max(len(sv.outlets) - n, 0)
    fig.update_xaxes(visible=False, range=[-3.6, LANE_W + 9.4])
    fig.update_yaxes(visible=False,
                     range=[y_bot - (1.3 if sv.banner else 0.6), y_top + 1.15])
    return plotly_layout(fig, height=max(height, 230))


# ================================ Streamlit glue ===============================
def _legend_html(sc: Scenario) -> str:
    chips = []
    for g in sc.grades:
        chips.append(f'<span class="ev-badge" style="background:{grade_color(g.id)}">'
                     f'{operator_text(g.id)}</span>')
    chips.append(f'<span class="ev-badge" style="background:{GRADE_COLORS[""]};'
                 f'color:#333">empty</span>')
    chips.append(f'<span class="ev-badge" style="background:{UNKNOWN_GREY}">'
                 f'unknown</span>')
    border = (f'<span style="color:{GREEN}">▮ active</span> · '
              f'<span style="color:{AMBER}">▮ ⚠ below safety</span> · '
              f'<span style="color:{RED}">▮ ⛔ hard violation</span> · '
              f'<span style="color:#5b7fa6">▮ idle</span> · '
              f'<span style="color:{TEXT_GREY}">'
              '╌ dashed = reference/planning basis</span>')
    return ("<b>Fill = grade</b>&nbsp; " + " ".join(chips)
            + f"&nbsp;&nbsp;<b>Border = status</b>&nbsp; {border}"
            + f'&nbsp;&nbsp;<span style="color:{TEXT_GREY}">▶ stacking in · '
              f'◀ reclaiming out · ┊ red dots = safety threshold</span>')


def render_monitor(sc: Scenario, plan: Optional[PlanResult], sim) -> None:
    import pandas as pd
    import streamlit as st

    from . import theme

    # ---------------- toolbar ------------------------------------------------
    qp = {}
    try:
        qp = dict(st.query_params)
    except Exception:
        pass
    with st.container(border=True):
        c = st.columns([1.6, 1.5, 1.6, 1.3])
        site_opts = ["All phosphate sites", "Louta", "Benguerir", "MPH M16"]
        qp_site = {"louta": "Louta", "benguerir": "Benguerir",
                   "m16": "MPH M16"}.get(str(qp.get("mon_site", "")).lower())
        site_sel = c[0].radio("Site", site_opts,
                              index=site_opts.index(qp_site) if qp_site else 0,
                              horizontal=True, key="mon_site_sel")
        basis_opts = ["Initial scenario"]
        if plan is not None:
            basis_opts.extend(["Final plan", "Selected plan bucket"])
        if sim is not None:
            basis_opts.append("Current simulation")
        qp_basis = {"initial": "Initial scenario",
                    "final": "Final plan",
                    "plan": "Final plan",
                    "plan_bucket": "Selected plan bucket",
                    "sim": "Current simulation"}.get(
            str(qp.get("mon_basis", "")).lower())
        default_basis = (qp_basis if qp_basis in basis_opts
                         else "Initial scenario")
        basis_sel = c[1].radio("State view", basis_opts,
                               index=basis_opts.index(default_basis),
                               horizontal=True, key="mon_state_sel_v2")
        basis = {"Initial scenario": "initial",
                 "Final plan": "plan",
                 "Selected plan bucket": "plan",
                 "Current simulation": "sim"}[basis_sel]
        max_b = {"initial": 0, "plan": sc.sim.horizon_buckets,
                 "sim": (sim.bucket if sim else 0)}[basis]
        if basis_sel == "Final plan":
            bucket = sc.sim.horizon_buckets
            c[2].caption(
                f"Terminal boundary **T={bucket}** · no active job at T")
        elif max_b > 0:
            bucket = c[2].slider("Bucket (time step)", 0, max_b, max_b,
                                 key="mon_bucket",
                                 help="planning/simulation time bucket — "
                                      f"Δt = {sc.sim.dt_h:g} h")
        else:
            bucket = 0
            c[2].caption("bucket slider available once a plan or "
                         "simulation exists")
        grades_all = [g.id for g in sc.grades]
        gsel = c[3].multiselect("Grade filter", grades_all, default=[],
                                key="mon_grades",
                                format_func=operator_text,
                                help="piles of other grades are dimmed")
        c2 = st.columns([1, 1, 1, 1, 1.6, 2.2])
        show_reach = c2[0].toggle("Reachability", key="mon_reach")
        show_flows = c2[1].toggle("Flow paths", value=True, key="mon_flows")
        show_safety = c2[2].toggle("Safety stock", value=True, key="mon_safety")
        show_ev = c2[3].toggle("Evidence", value=True, key="mon_ev")
        m17_ids = {sp.id for sp in sc.spaces if sp.area.startswith("M17")}
        machines_all = [m.id for m in sc.machines
                        if m.kind in ("stacker", "reclaimer")
                        and m.site in PHOSPHATE_SITES
                        and not (m.reachable_spaces
                                 and set(m.reachable_spaces) <= m17_ids)]
        sel_machine = c2[4].selectbox(
            "Machine (reachability)", ["—"] + machines_all,
            key="mon_machine", disabled=not show_reach,
            help="uses configured reachable_spaces — never visual proximity",
            format_func=(lambda mid: "—" if mid == "—" else
                         theme.machine_display_label(sc, mid)))
        sel_machine = "" if sel_machine == "—" else sel_machine
        snapshot_meta = sc.config.get("INVENTORY_SNAPSHOT", {})
        if basis == "initial" and snapshot_meta:
            state_time = str(snapshot_meta.get("captured_at", "—"))
        elif basis == "initial":
            state_time = "scenario basis (no live timestamp)"
        else:
            state_time = sc.sim.bucket_dt(bucket).isoformat()
        c2[5].caption(f"Scenario **{sc.name.split(' —')[0]}** · basis "
                      f"**{basis_sel}** · state timestamp **{state_time}**")

    ms = build_monitor_state(sc, plan, sim, basis=basis, bucket=bucket)

    if ms.message:
        st.info(operator_text(
            ms.message + " Load S2 for the M16 hub or S3 for the "
            "complete three-site phosphate chain."))
        return

    # ---------------- three-site corridor header ------------------------------
    mines = [s for s in ("LOUTA", "BENGUERIR") if s in ms.sites]
    if mines and "MPH" in ms.sites:
        chain = " · ".join(f"{c.from_site} →{c.id}→ M16" for c in ms.corridors)
        items = [("Domain A chain", chain or "—")]
        for cvm in ms.corridors:
            items.append((f"{cvm.id} ({cvm.from_site}→M16)",
                          f"{cvm.capacity_tph:,.0f} t/h · "
                          f"transit time = {cvm.transit_h:g} h"
                          + (f" · in transit {cvm.in_transit_t:,.0f} t"
                             if cvm.in_transit_t is not None else "")
                          + f" · next arrival {cvm.next_arrival}"
                          + (f" · dispatch {cvm.dispatch_tph:,.0f} t/h"
                             if cvm.dispatch_tph is not None else "")))
        theme.mini_kpis(items)
    elif "MPH" in ms.sites and not mines:
        st.caption("Upstream corridor arrivals are **simulated inputs** in "
                   "this scenario (mines not modelled — use S3).")

    # ---------------- drawings -------------------------------------------------
    def draw(site_id: str, large: bool):
        sv = ms.sites[site_id]
        st.markdown(f"#### {sv.title}")
        theme.plot(site_figure(
            sv, show_reachability=show_reach, selected_machine=sel_machine,
            show_flows=show_flows, show_safety=show_safety,
            show_evidence=show_ev, grade_filter=gsel or None,
            quality_params=[q.name for q in sc.quality_params],
            large=large))

    focus = {"Louta": "LOUTA", "Benguerir": "BENGUERIR",
             "MPH M16": "MPH"}.get(site_sel)
    if focus:
        if focus in ms.sites:
            draw(focus, large=True)
        else:
            st.info(f"{focus} is not part of the loaded scenario "
                    f"({sc.name.split(' —')[0]}).")
    else:
        # full-width stack: half-width columns clip outlet labels
        if "MPH" in ms.sites:
            draw("MPH", large=len(ms.sites) == 1)
        for m_ in mines:
            draw(m_, large=False)

    st.markdown(_legend_html(sc), unsafe_allow_html=True)

    # ---------------- supporting panels ----------------------------------------
    piles_all = [p for sv in ms.sites.values() for p in sv.piles]
    machines_vm = [m for sv in ms.sites.values() for m in sv.machines]
    warn_rows = []
    for p_ in piles_all:
        for w in p_.warnings:
            warn_rows.append({"asset": p_.id, "site": p_.site, "warning": w})
    for m_ in machines_vm:
        if m_.status in ("Unavailable", "Maintenance"):
            warn_rows.append({"asset": m_.id, "site": m_.site,
                              "warning": f"machine {m_.status.lower()}"})
    for d in ms.discrepancies:
        warn_rows.append({"asset": "coverage", "site": "—", "warning": d})

    tabs = st.tabs(["Pile details", "Machine details", "Active jobs",
                    f"Warnings ({len(warn_rows)})",
                    f"Model coverage / data gaps ({len(ms.gaps)})"])
    with tabs[0]:
        sel_p = st.selectbox("Stockpile", [p.id for p in piles_all],
                             key="mon_pile_sel", label_visibility="collapsed")
        p_ = next(p for p in piles_all if p.id == sel_p)
        sp_ = next((sp for sp in sc.spaces if sp.id == p_.id), None)
        theme.mini_kpis([
            ("Mass / capacity", f"{p_.mass_t:,.0f} / {p_.capacity_t:,.0f} t"),
            ("Fill", f"{p_.fill_pct:.0f} %"), ("Grade", p_.grade_label),
            ("Material / profile", ((sp_.material_class or "—") + " · "
                                    + (sp_.profile_id or "—")) if sp_ else "—"),
            ("State", p_.state),
            ("Safety stock", f"{p_.safety_stock_t:,.0f} t"
             + (" — BELOW ⚠" if p_.below_safety else "")),
            ("Evidence", f"{p_.evidence_status} · {p_.evidence_source[:40]}"),
            ("In / out (bucket)", f"{p_.inbound_tph:,.0f} / "
                                  f"{p_.outbound_tph:,.0f} t/h"),
        ])
        qitems = [(q.name + f" [{q.unit}]", f"{p_.quality[q.name]:g}")
                  for q in sc.quality_params if q.name in p_.quality]
        if qitems:
            theme.mini_kpis(qitems)
        if p_.segments:
            st.caption("Longitudinal segments (actual Segment data): "
                       + " · ".join(
                           f"{operator_text(s.grade)} "
                           f"{s.mass_t / 1000:,.0f} kt"
                           for s in p_.segments))
        elif p_.mixed:
            st.caption("Mixed pile — dominant-grade fallback "
                       "(no usable segment data; boundaries not fabricated).")
    with tabs[1]:
        sel_m = st.selectbox(
            "Machine", [m.id for m in machines_vm], key="mon_mach_sel",
            label_visibility="collapsed",
            format_func=lambda mid: theme.machine_display_label(sc, mid))
        m_ = next(m for m in machines_vm if m.id == sel_m)
        machine_repere = "—"
        if m_.target_space and m_.position_x is not None:
            try:
                rpos = x_to_repere(
                    sc.space(m_.target_space), m_.position_x)
                machine_repere = (
                    f"{m_.target_space} · {format_repere(rpos)}")
            except (StopIteration, ValueError):
                pass
        theme.mini_kpis([
            ("Type", m_.kind), ("Status", m_.status),
            ("Job", m_.job_id or "—"),
            ("Target/source", m_.target_space or "—"),
            ("Rate", f"{m_.rate_tph:,.0f} / max {m_.rate_max_tph:,.0f} t/h"),
            ("Repère", machine_repere),
            ("Reachable piles", ", ".join(m_.reachable) or "—"),
            ("Evidence", m_.evidence_status),
        ])
    with tabs[2]:
        rows = []
        for sv in ms.sites.values():
            for m_ in sv.machines:
                if m_.job_id:
                    job = None
                    if sim is not None:
                        job = sim.jobs.get(m_.job_id)
                    if job is None and plan is not None:
                        job = next((j for j in plan.jobs
                                    if j.id == m_.job_id), None)
                    repere_range = (
                        job_repere_label(sc.space(job.space), job)
                        if job is not None else "—")
                    rows.append({"site": sv.id,
                                 "machine": theme.machine_display_label(sc,
                                                                          m_.id),
                                 "machine_id": m_.id,
                                 "job": m_.job_id, "action": m_.status,
                                 "pile": m_.target_space,
                                 "repère range": repere_range,
                                 "destination": m_.dest_id or "—",
                                 "rate [t/h]": m_.rate_tph})
        theme.show_df(pd.DataFrame(rows),
                      empty_message="no active jobs in the selected bucket")
    with tabs[3]:
        theme.show_df(pd.DataFrame(warn_rows),
                      empty_message="no warnings in the selected bucket")
    with tabs[4]:
        st.caption("Equipment named in the report but **not currently "
                   "modelled** (never silently added to the optimiser):")
        theme.show_df(pd.DataFrame(
            [{"site": g[0], "item": g[1], "why not modelled": g[2]}
             for g in ms.gaps]))
        if ms.discrepancies:
            st.error("Coverage discrepancies vs expected register: "
                     + " · ".join(ms.discrepancies))
        else:
            st.caption("✓ scenario matches the expected coverage register "
                       "(M16: HP-01…08 + ST01-A/B/C + RC01-A/B · Benguerir: "
                       "6 physical piles + 9 mobile machines · Louta: "
                       "4 physical piles + 6 mobile machines). Hopper, mine-"
                       "feed and WIP interfaces remain in the solver flow but "
                       "are not drawn as physical stockpiles.")
