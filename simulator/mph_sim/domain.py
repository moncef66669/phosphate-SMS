"""Domain models (planning digital twin) — layer 1.

Every engineering value carries an Evidence tag so the UI can distinguish
CONFIRMED document values from ASSUMED placeholders (report §16 vocabulary).
Plain dataclasses (no external deps); validation lives in validation.py.
Units: mass [t], rate [t/h], time in integer buckets of dt_h hours,
quality parameters in the unit registered in Scenario.quality_params.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional
import datetime as _dt

EVIDENCE_STATUSES = ("CONFIRMED", "DERIVED", "INFERRED", "ASSUMED", "TBC")
CONFIDENCES = ("H", "M", "L")

#: asset/configuration lifecycle — an OPTIONAL_PROPOSED asset is a documented
#: but unselected proposal and must never silently become an
#: installed/operational asset. Source symbols alone do not determine project
#: lifecycle; an explicit project-team current-state instruction may override
#: an older document notation. `planning_enabled` stays a SEPARATE flag:
#: lifecycle says what the asset IS, planning_enabled says whether the
#: planner may use it in the loaded scenario/option configuration.
LIFECYCLES = ("EXISTING", "SELECTED", "OPTIONAL_PROPOSED", "FUTURE",
              "REFERENCE_ONLY", "OUT_OF_SCOPE")

# Receiving-line operating state is scenario configuration, not a property of
# the generic Destination record.  A FUTURE line keeps its configured demand
# template for later activation, while ``Scenario.demand_tph`` exposes zero
# active demand to planning and simulation.
DESTINATION_LINE_STATE_CONFIG = "DESTINATION_LINE_OPERATING_STATE"
DESTINATION_LINE_STATES = ("AVAILABLE", "FUTURE")


@dataclass
class Evidence:
    source: str = "placeholder"
    status: str = "ASSUMED"          # CONFIRMED | DERIVED | INFERRED | ASSUMED | TBC
    confidence: str = "L"            # H | M | L
    note: str = ""


@dataclass
class QualityParam:
    name: str                        # e.g. BPL, Cd, MgO, SiO2, moisture
    unit: str                        # %, ppm, ...
    description: str = ""


@dataclass
class Site:
    id: str
    name: str
    role: str                        # mine | hub
    sms_endpoint: str = "simulated"  # future RabbitMQ endpoint (adapter layer)
    evidence: Evidence = field(default_factory=Evidence)
    # ---- same-pile stacking+reclaiming concurrency policy --------------------
    # True  = permitted subject to spatial/collision/technical constraints
    #         (USER-CONFIRMED for MPH M16, 15-Jul-2026)
    # None  = TBC — treated as permitted (no categorical prohibition), flagged
    # False = site configuration forbids it (conditional, config-driven —
    #         never a hard-coded A/B double-buffering rule)
    same_pile_concurrency_allowed: Optional[bool] = None
    concurrency_evidence: str = ""
    # spatial parameters for concurrent work zones — numerical values TBC
    # until the YardMaster/ASP configuration, ACS specification or machine
    # supplier documentation are on file (demonstration values = ASSUMED)
    min_machine_separation_m: float = 50.0   # ASP UC-28 default — value TBC
    work_zone_buffer_m: float = 25.0         # ASSUMED demonstration value
    travel_clearance_m: float = 10.0         # ASSUMED demonstration value
    machine_envelope_m: float = 15.0         # ASSUMED demonstration value
    concurrent_route_allowed: bool = True    # routes/gates may run together
    # ---- corridor-feeder / route-confluence exclusivity ----------------------
    # Each inner list names machines of which AT MOST ONE may be active in any
    # bucket (e.g. the two Louta product reclaimers feeding C1: either may be
    # selected, both never run together until the route-confluence
    # configuration explicitly permits simultaneous partial-rate reclaim).
    # Config data, never a hard-coded rule; empty = no restriction.
    exclusive_machine_groups: List[List[str]] = field(default_factory=list)
    exclusivity_evidence: str = ""
    # documented material-state property profiles (display metadata, never a
    # silent replacement of the shared grade vectors), e.g. Benguerir B02:
    #   {"raw": {...}, "washed_filtered": {...}, "settled_product": {...}}
    material_profiles: Dict[str, Dict[str, str]] = field(default_factory=dict)


@dataclass
class Segment:
    """1-m-style inventory segment (coarse in the MVP; mirrors ASP SpaceValue)."""
    space_id: str
    x_from: float
    x_to: float
    mass_t: float
    grade: str
    quality: Dict[str, float] = field(default_factory=dict)
    age_days: float = 0.0


@dataclass
class PileZone:
    """One longitudinal grade zone of a stockpile (experimental feature).

    Zones are REQUIRED INPUTS, never derived: zone count, x-boundaries,
    grade designation, masses and the active reclaim face all come from
    engineering data (zone/campaign rules are TBC for every real site —
    S3-A2/S3-A7 — so no built-in scenario defines zones). The solver uses
    zones only when ``Scenario.config["MULTI_GRADE_ZONES_ENABLED"]`` is
    true AND zones exist for a pile; otherwise the pile keeps the baseline
    one-dominant-grade model unchanged.

    Contamination rule: ``grade`` is the zone's designated grade — only
    that grade may be stacked into it; ``grade == ""`` marks a free/empty
    zone that accepts any grade (and adopts it). Grade-compatibility
    matrices beyond same-grade/empty are a missing engineering input and
    are deliberately NOT invented here.

    Reclaim sequencing: only zones with ``reclaim_face=True`` may be
    reclaimed in a solve — the face designation is an input (the reclaim
    sequence rule itself is TBC), never inferred.
    """
    space_id: str                    # parent StockSpace id
    zone_id: str                     # unique within the parent (e.g. Z1)
    x_from: float
    x_to: float
    grade: str = ""                  # designated grade ("" = free/empty zone)
    mass_t: float = 0.0
    capacity_t: float = 0.0
    quality: Dict[str, float] = field(default_factory=dict)
    reclaim_face: bool = False       # True = active face, reclaim allowed
    safety_stock_t: float = 0.0      # pile-level safety stock is NOT
    #                                # auto-allocated to zones — explicit input
    evidence: Evidence = field(default_factory=Evidence)

    def sub_space_id(self) -> str:
        return f"{self.space_id}#{self.zone_id}"


@dataclass
class StockSpace:
    id: str
    site: str
    area: str                        # M16-humid | M16-dry | M17-open | M17-closed | mine-yard
    kind: str                        # open | closed
    capacity_t: float
    mass_t: float
    grade: str                       # dominant / designated grade id
    quality: Dict[str, float] = field(default_factory=dict)
    state: str = "mixed"             # empty|building|full|reclaiming|mixed|reserved
    reserved_t: float = 0.0
    designated_grade: str = ""       # anti-contamination: only this grade may be stacked ("" = free)
    x_from: float = 0.0              # rail coordinate range (geometry ASSUMED unless stated)
    x_to: float = 300.0
    safety_stock_t: float = 0.0
    node_id: str = ""                # route-graph node (default: SPACE:<id>)
    material_class: str = ""          # raw_screened | washed | dry | sulphur
    profile_id: str = ""              # stage-specific MaterialProfile id
    evidence: Evidence = field(default_factory=Evidence)
    # Operational pile coordinates.  Physical x remains canonical for
    # YardMaster/ASP, travel and separation; repères expose the plant's
    # pile-relative R0..R1000 convention.  Reversing the endpoint values
    # reverses the operational direction without changing physical geometry.
    repere_at_x_from: int = 0
    repere_at_x_to: int = 1000

    def free_t(self) -> float:
        return max(self.capacity_t - self.mass_t, 0.0)


@dataclass
class Grade:
    id: str
    name: str
    quality: Dict[str, float] = field(default_factory=dict)
    rock_value: float = 0.0          # relative value / t (economics placeholder)
    material_class: str = ""         # never reuse a raw grade as washed product
    profile_id: str = ""             # optional primary profile; family links live on MaterialProfile
    evidence: Evidence = field(default_factory=Evidence)


@dataclass
class MaterialProfile:
    """Stage-specific material and quality basis.

    Numeric grade vectors stay on :class:`Grade` for optimisation. This
    profile carries documented ranges, physical state and missing-assay
    boundaries so raw/screened ore is never silently treated as washed
    product. String ranges deliberately preserve values such as ``10–14``
    and ``TBC`` without inventing a midpoint.
    """
    id: str
    site: str
    stage: str
    material_class: str
    label: str
    grade_ids: List[str] = field(default_factory=list)
    quality_ranges: Dict[str, str] = field(default_factory=dict)
    physical_properties: Dict[str, str] = field(default_factory=dict)
    transformation_note: str = ""
    evidence: Evidence = field(default_factory=Evidence)


@dataclass
class Machine:
    id: str
    site: str
    kind: str                        # stacker | reclaimer | tripper
    rate_min_tph: float
    rate_max_tph: float
    reachable_spaces: List[str] = field(default_factory=list)
    rail: str = ""
    travel_speed_m_min: float = 30.0     # ASSUMED unless plot plan/GA available
    setup_min: float = 10.0              # ASSUMED changeover/setup
    min_separation_m: float = 50.0       # ASP UC-28 configurable minimum distance
    start_stop_limit_per_h: float = 0.0  # 0 = unconstrained (sulphur: 8)
    available: bool = True
    position_x: float = 0.0
    work_area: Dict[str, List[float]] = field(default_factory=dict)  # space_id -> [x_from, x_to]
    evidence: Evidence = field(default_factory=Evidence)
    # ---- asset lifecycle / option handling (18-Jul-2026, B02 BoD) ------------
    physical_tag: str = ""            # documented engineering tag (e.g. ST3)
    lifecycle: str = "EXISTING"       # see LIFECYCLES
    planning_enabled: bool = True     # False -> planner must NEVER assign jobs
    rate_design_tph: float = 0.0      # documented DESIGN capacity; planning
    #                                 # uses rate_max_tph (= NOMINAL) unless an
    #                                 # explicit design-envelope dataset is built


@dataclass
class Node:
    id: str
    site: str
    kind: str                        # space | tower | gate | dest | source
    label: str = ""
    x: float = 0.0                   # layout hint for graph drawing only
    y: float = 0.0


@dataclass
class Link:
    """Directed conveyor/route link — the REAL network model (report §14.2).

    Deliberately richer than the ASP 'Route' (Device+JobType+free-text Location).
    """
    id: str
    src: str                         # node id
    dst: str                         # node id
    capacity_tph: float
    min_stable_tph: float = 0.0
    transit_buckets: int = 0         # transport delay in whole buckets (corridors)
    available: bool = True
    shared_group: str = ""           # links sharing a physical resource (sum-capacity group)
    conflict_group: str = ""         # mutually exclusive links (one active per bucket)
    gate_id: str = ""
    gate_position: str = ""
    changeover_min: float = 0.0      # gate/route transition time
    evidence: Evidence = field(default_factory=Evidence)
    # ---- route classification / option handling (18-Jul-2026, B02 BoD) -------
    lifecycle: str = "EXISTING"       # see LIFECYCLES
    capacity_design_tph: float = 0.0  # documented DESIGN capacity (display);
    #                                 # capacity_tph = NOMINAL planning value
    material_class: str = ""          # "raw" | "washed" | "" (both/unspecified)
    #                                 # raw and washed material must never use
    #                                 # an incompatible route


@dataclass
class SpecRange:
    param: str
    pmin: Optional[float] = None
    pmax: Optional[float] = None
    target: Optional[float] = None
    hard: bool = False               # hard = must hold, soft = slack penalised by w2
    averaging_h: float = 1.0         # compliance window (MVP enforces per bucket)
    margin: float = 0.0              # safety margin applied inside min/max


@dataclass
class Destination:
    id: str
    site: str
    node_id: str
    priority: int = 1
    spec: List[SpecRange] = field(default_factory=list)
    demand_default_tph: float = 0.0
    # Requested material grade. For an ordinary delivery it is the receiving
    # campaign grade; for ``process_feed`` it is the source/feed grade consumed
    # by the matching ProcessTransfer.
    requested_grade: str = ""
    evidence: Evidence = field(default_factory=Evidence)
    # ``delivery`` is a final receiving interface served by ordinary reclaim
    # jobs. ``process_feed`` is a washing/process-line requirement served by
    # the input tonnage of an explicit ProcessTransfer.
    demand_kind: str = "delivery"
    process_stage: str = ""


@dataclass
class DemandEntry:
    dest_id: str
    bucket: int
    rate_tph: float


@dataclass
class CalendarEntry:
    scope: str                       # "GLOBAL" or machine id
    start_bucket: int
    end_bucket: int                  # inclusive
    reason: str = "maintenance"


@dataclass
class ExogenousFlow:
    """Flows the layer does not command (manual payloaders, unmodelled feeds)."""
    id: str
    target_space: str
    rate_tph: float
    grade: str
    source_space: str = ""           # optional space drained by this flow (payloader open->closed)
    start_bucket: int = 0
    end_bucket: int = 10 ** 6
    quality: Dict[str, float] = field(default_factory=dict)
    evidence: Evidence = field(default_factory=Evidence)


@dataclass
class ProcessTransfer:
    """One commanded material transfer between solver inventory spaces.

    ``machine`` identifies the equipment that physically controls the move.
    ``source_space`` and ``target_space`` make the material balance explicit;
    the target receives ``yield_factor`` times the source tonnage and the
    remainder is reported as process reject/loss.  ``residence_min`` is a
    process dependency used by the minute execution scheduler, not a hidden
    bucket rounding rule.

    The route is an ordered list of existing :class:`Link` ids.  It is
    deliberately explicit because a washing or stacking transfer must never
    be inferred from visual proximity.
    """
    id: str
    machine: str
    source_space: str
    target_space: str
    path: List[str] = field(default_factory=list)
    operation_kind: str = "transfer"  # stack | reclaim | transfer
    process_stage: str = ""
    output_grade: str = ""
    yield_factor: float = 1.0
    residence_min: int = 0
    depends_on_stage: str = ""
    dependency_delay_min: int = 0
    purpose: str = ""
    evidence: Evidence = field(default_factory=Evidence)


@dataclass
class BeltProfileEntry:
    """Material already in transit on a delayed link at horizon start (report §15.3 eq. 5)."""
    link_id: str
    arrive_bucket: int               # 0-based bucket in which it reaches the link dst
    mass_t: float
    grade: str
    quality: Dict[str, float] = field(default_factory=dict)


@dataclass
class FixedJob:
    """Operator-fixed job: the optimiser must honour it (H3 governance)."""
    id: str
    machine: str
    kind: str                        # stack | reclaim | dispatch
    space: str
    dest_id: str = ""                # for reclaim/dispatch
    rate_tph: float = 0.0
    start_bucket: int = 0
    end_bucket: int = 0              # inclusive
    note: str = ""
    grade: str = ""
    start_repere: Optional[int] = None
    stop_repere: Optional[int] = None


@dataclass
class ObjectiveWeights:
    """w1..w7 per report §15.2. w1 must dominate (unmet-demand slack)."""
    w1_unmet: float = 1000.0
    w2_offspec: float = 200.0
    w3_process_cost: float = 0.0     # needs GradeCostModel curves (NOT received — see register)
    w4_giveaway: float = 5.0
    w5_moves: float = 2.0
    w6_safety: float = 20.0
    w7_utilisation: float = 0.0


@dataclass
class SimParams:
    dt_h: float = 1.0
    horizon_buckets: int = 24
    frozen_buckets: int = 2          # jobs inside this window survive a replan
    seed: int = 42
    start_iso: str = "2026-07-13T00:00:00"
    max_solve_s: float = 20.0
    deviation_tolerance: float = 0.10  # relative moved-mass shortfall triggering replan
    replan_on_deviation: bool = True

    def bucket_dt(self, bucket: int) -> _dt.datetime:
        return _dt.datetime.fromisoformat(self.start_iso) + _dt.timedelta(hours=self.dt_h * bucket)


@dataclass
class Assumption:
    id: str
    text: str
    module: str                      # affected site/module
    status: str = "OPEN"             # OPEN | CONFIRMED | RETIRED
    source: str = "—"
    confidence: str = "L"
    placeholder: str = ""            # current placeholder value in the dataset
    impact: str = ""                 # simulation impact
    owner: str = "OCP project team"
    action: str = ""


@dataclass
class Scenario:
    name: str
    description: str = ""
    quality_params: List[QualityParam] = field(default_factory=list)
    sites: List[Site] = field(default_factory=list)
    grades: List[Grade] = field(default_factory=list)
    material_profiles: List[MaterialProfile] = field(default_factory=list)
    spaces: List[StockSpace] = field(default_factory=list)
    segments: List[Segment] = field(default_factory=list)
    # experimental multi-grade zoning inputs (see PileZone) — empty unless
    # real zone/campaign data exists; gated by MULTI_GRADE_ZONES_ENABLED
    pile_zones: List[PileZone] = field(default_factory=list)
    machines: List[Machine] = field(default_factory=list)
    nodes: List[Node] = field(default_factory=list)
    links: List[Link] = field(default_factory=list)
    destinations: List[Destination] = field(default_factory=list)
    demand: List[DemandEntry] = field(default_factory=list)
    calendars: List[CalendarEntry] = field(default_factory=list)
    exogenous: List[ExogenousFlow] = field(default_factory=list)
    process_transfers: List[ProcessTransfer] = field(default_factory=list)
    belt_profile: List[BeltProfileEntry] = field(default_factory=list)
    fixed_jobs: List[FixedJob] = field(default_factory=list)
    weights: ObjectiveWeights = field(default_factory=ObjectiveWeights)
    sim: SimParams = field(default_factory=SimParams)
    assumptions: List[Assumption] = field(default_factory=list)
    notes: str = ""
    # scenario option/configuration switches (documented per scenario), e.g.
    #   BEN_B02_OPTION_ENABLED  - legacy sensitivity switch for the current
    #                             Benguerir B02 configuration (baseline True)
    #   BEN_RAW_TO_C2_ENABLED   - conditional raw-ore -> C2 route active
    #   CAPACITY_ENVELOPE       - "nominal" (default) | "design"
    config: Dict[str, object] = field(default_factory=dict)

    # ---- lookups -------------------------------------------------------
    def space(self, sid: str) -> StockSpace:
        return next(s for s in self.spaces if s.id == sid)

    def machine(self, mid: str) -> Machine:
        return next(m for m in self.machines if m.id == mid)

    def grade(self, gid: str) -> Optional[Grade]:
        return next((g for g in self.grades if g.id == gid), None)

    def material_profile(self, pid: str) -> Optional[MaterialProfile]:
        return next((p for p in self.material_profiles if p.id == pid), None)

    def destination(self, did: str) -> Destination:
        return next(d for d in self.destinations if d.id == did)

    def link(self, lid: str) -> Link:
        return next(l for l in self.links if l.id == lid)

    def demand_tph(self, dest_id: str, bucket: int) -> float:
        line_states = self.config.get(DESTINATION_LINE_STATE_CONFIG, {})
        if isinstance(line_states, dict) and \
                str(line_states.get(dest_id, "")).strip().upper() == "FUTURE":
            return 0.0
        val = None
        for e in self.demand:
            if e.dest_id == dest_id and e.bucket == bucket:
                val = e.rate_tph
        if val is None:
            val = self.destination(dest_id).demand_default_tph
        return val

    def quality_of(self, space_id: str) -> Dict[str, float]:
        sp = self.space(space_id)
        if sp.quality:
            return sp.quality
        g = self.grade(sp.grade)
        return dict(g.quality) if g else {}

    def machine_blocked(self, mid: str, bucket: int) -> bool:
        for c in self.calendars:
            if c.scope in ("GLOBAL", mid) and c.start_bucket <= bucket <= c.end_bucket:
                return True
        return False


# ---- planning / execution artefacts ------------------------------------

JOB_KINDS = ("stack", "reclaim", "dispatch")


@dataclass
class JobBucketFlow:
    """Exact solver allocation for one job, native bucket and route path.

    ``tonnage_t`` is material allocated to this path in ``bucket``.  A job can
    have several rows for one bucket when the optimiser splits flow over
    parallel paths.  The current optimiser predates this field, so an empty
    list explicitly means "legacy aggregate only" rather than an invented
    per-bucket distribution.
    """
    bucket: int
    tonnage_t: float
    path: List[str] = field(default_factory=list)
    output_tonnage_t: float = 0.0


@dataclass
class JobExecutionWindow:
    """Validated post-solve execution timing; never a solver decision.

    Minutes are integer offsets from ``Scenario.sim.start_iso``.  Solver
    buckets remain the authoritative reservation envelope.  Only windows with
    ``status == "VALIDATED"`` may drive minute timestamps/rates in execution
    views or ASP mapping.
    """
    solver_start_bucket: int
    solver_end_bucket: int
    start_minute: int
    duration_min: int
    tonnage_t: float
    rate_tph: float
    basis: str = "POST_SOLVE_REFINED"
    status: str = "UNVALIDATED"       # VALIDATED | BUCKET_ONLY | UNVALIDATED
    note: str = ""
    # Optional non-material preparation reserved before this work window.
    # It may finish before material work starts (the machine can position
    # early and wait) but must stay after the preceding work window.
    positioning_start_minute: Optional[int] = None
    positioning_duration_min: int = 0


@dataclass
class Job:
    id: str
    machine: str
    kind: str                        # stack | reclaim | dispatch
    space: str                       # source space (reclaim/dispatch) or target space (stack)
    dest_id: str = ""                # destination id (reclaim/dispatch); "" for stack
    grade: str = ""
    rate_tph: float = 0.0
    tonnage_t: float = 0.0
    start_bucket: int = 0
    end_bucket: int = 0              # inclusive
    path: List[str] = field(default_factory=list)   # link ids used
    method: str = "chevron"          # stacking pattern -> ASP Method
    origin: str = "AUTO"             # AUTO | HUMAN
    locked: bool = False
    status: str = "PLANNED"          # PLANNED|DISPATCHED|RUNNING|FINISHED|CANCELLED|REJECTED
    start_x: float = 0.0
    stop_x: float = 0.0
    comment: str = ""
    # Effective machine ceiling used by this solve/execution overlay.
    # Populated from Machine.rate_max_tph × SolveOptions.machine_rate_factor
    # (and any lower positive fixed-job rate).  Zero keeps legacy jobs
    # backward-compatible by falling back to the scenario machine maximum.
    execution_rate_cap_tph: float = 0.0
    # Exact per-bucket/path solver allocation.  Optimiser extraction must
    # populate this before a minute execution overlay can be validated.
    bucket_flows: List[JobBucketFlow] = field(default_factory=list)
    # Optional second-stage execution overlay.  It never changes CP-SAT
    # inventory, demand, capacity or exclusivity constraints.
    execution_windows: List[JobExecutionWindow] = field(default_factory=list)
    # Explicit material-flow semantics for internal process transfers.
    # Legacy destination jobs retain ``space`` / ``dest_id`` and leave these
    # fields empty; operator views can therefore present one unambiguous
    # source/target contract without reinterpreting old jobs.
    source_space: str = ""
    target_space: str = ""
    output_grade: str = ""
    output_tonnage_t: float = 0.0
    process_stage: str = ""
    depends_on_stage: str = ""
    dependency_delay_min: int = 0
    residence_min: int = 0
    purpose: str = ""
    transfer_id: str = ""


@dataclass
class PlanResult:
    status: str                      # OPTIMAL | FEASIBLE | INFEASIBLE | NO_SOLVER | ERROR
    jobs: List[Job] = field(default_factory=list)
    unmet: Dict[str, List[float]] = field(default_factory=dict)      # dest -> per-bucket tonnes
    offspec: Dict[str, List[float]] = field(default_factory=dict)    # dest -> per-bucket slack (t·unit)
    safety_short: Dict[str, List[float]] = field(default_factory=dict)  # space -> per-bucket tonnes
    surge_unstacked: List[float] = field(default_factory=list)       # emergency slack per bucket
    delivered: Dict[str, List[float]] = field(default_factory=dict)  # dest -> per-bucket tph
    inventory: Dict[str, List[float]] = field(default_factory=dict)  # space -> per-bucket tonnes (t index 0..T)
    process_reject: Dict[str, List[float]] = field(default_factory=dict)
    machine_dispositions: Dict[str, Dict[str, object]] = field(default_factory=dict)
    objective: float = 0.0
    breakdown: Dict[str, float] = field(default_factory=dict)
    solver: str = "cp-sat"
    solver_wall_s: float = 0.0
    reasons: List[str] = field(default_factory=list)                 # reason codes for gaps
    relaxations: List[str] = field(default_factory=list)             # suggested relaxations
    horizon_offset: int = 0          # first bucket this plan covers (for replans)


@dataclass
class JobUpdate:
    """Simulated SMS 'JobUpdated' actuals feedback (report §15.8)."""
    job_id: str
    bucket: int
    status: str
    moved_t: float
    effective_rate_tph: float
    machine_position_x: float = 0.0
    quality: Dict[str, float] = field(default_factory=dict)
    forecast_end_bucket: int = 0
    note: str = ""


@dataclass
class SimEvent:
    """Injected disturbance."""
    id: str
    etype: str
    bucket: int
    target: str = ""                 # machine / link / dest / space / job id
    magnitude: float = 0.0           # rate factor, tph delta, tonnes, ...
    duration_buckets: int = 1
    params: Dict[str, float] = field(default_factory=dict)
    note: str = ""


EVENT_TYPES = (
    "machine_failure", "conveyor_trip", "route_unavailable", "demand_change",
    "delayed_mine_dispatch", "offspec_assay", "inventory_discrepancy",
    "operator_reject", "reduced_rate", "downstream_interruption", "comms_loss",
)


@dataclass
class LogRecord:
    bucket: int
    kind: str                        # EVENT | DEVIATION | REPLAN | DISPATCH | INFO
    message: str
    asset: str = ""
    detail: Dict[str, object] = field(default_factory=dict)


def to_dict(obj) -> dict:
    return asdict(obj)
