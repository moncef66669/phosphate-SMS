"""Domain models (planning digital twin) — layer 1.

Every engineering value carries an Evidence tag so the UI can distinguish
CONFIRMED document values from ASSUMED placeholders (report §16 vocabulary).
Plain dataclasses (no external deps); validation lives in validation.py.
Units: mass [t], rate [t/h], time in integer buckets of dt_h hours,
quality parameters in the unit registered in Scenario.quality_params.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, Iterable, List, Optional, Tuple
import datetime as _dt
import math

EVIDENCE_STATUSES = ("CONFIRMED", "DERIVED", "INFERRED", "ASSUMED", "TBC")
CONFIDENCES = ("H", "M", "L")

# Empty legacy stock assays mean "use the grade catalogue". Live snapshots
# separately identify positive stock whose chemistry is explicitly unknown.
UNKNOWN_STOCK_QUALITY_KEY = "EXPLICIT_UNKNOWN_STOCK_QUALITY"
UNKNOWN_BELT_QUALITY_KEY = "EXPLICIT_UNKNOWN_BELT_QUALITY_INDICES"

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
DESTINATION_LINE_STATES = ("AVAILABLE", "DISABLED", "FUTURE")


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
    # Explicit chemistry lineage for transformed grades.  For washed
    # phosphate this identifies the raw Cd profile; it is deliberately not a
    # site identifier because Louta and Benguerir share the grade catalogue.
    source_grade_id: str = ""
    # Stage-aware Benguerir beneficiation basis.  All solver inventory,
    # machine and conveyor quantities remain physical/as-handled tonnes.
    # ``beneficiation_yield`` converts dry feed solids to zero-moisture
    # merchant product (TSM); moistures are wet-basis percentages.  ``None``
    # deliberately means that no conversion profile is configured, preserving
    # legacy transfer behaviour for other materials and routes.
    beneficiation_yield: Optional[float] = None
    raw_moisture_pct: Optional[float] = None
    product_moisture_pct: Optional[float] = None
    # Receiving specifications are not physical stock material identities.
    is_specification: bool = False
    # A specification grade carries the acceptance envelope selected by a
    # demand campaign. It never replaces the actual source-material assay.
    receiving_spec: List["SpecRange"] = field(default_factory=list)
    # Verbatim source-table ranges preserve fields that are redundant with,
    # or not yet executable in, the numerical quality model (for example a
    # reported P2O5 range alongside its BPL equivalent).
    source_quality_ranges: Dict[str, str] = field(default_factory=dict)
    # Empty means generally selectable; otherwise the specification may only
    # be requested at the named receiving interfaces.
    receiving_destinations: List[str] = field(default_factory=list)

    def raw_physical_to_tsm(self, physical_t: float) -> Optional[float]:
        """Return recoverable zero-moisture merchant tonnes from raw rock.

        The beneficiation yield is defined on dry feed solids, while
        ``physical_t`` is the wet/as-handled raw-rock mass.
        """
        beneficiation_yield = getattr(self, "beneficiation_yield", None)
        raw_moisture_pct = getattr(self, "raw_moisture_pct", None)
        if beneficiation_yield is None or raw_moisture_pct is None:
            return None
        return (float(physical_t)
                * (1.0 - float(raw_moisture_pct) / 100.0)
                * float(beneficiation_yield))

    def raw_physical_to_wet_product(
            self, physical_t: float) -> Optional[float]:
        """Return washed-product physical mass from raw physical mass.

        Water remaining in the washed product is physical mass and therefore
        must be restored after applying the dry-feed-to-TSM yield.
        """
        tsm_t = self.raw_physical_to_tsm(physical_t)
        product_moisture_pct = getattr(self, "product_moisture_pct", None)
        if tsm_t is None or product_moisture_pct is None:
            return None
        dry_fraction = 1.0 - float(product_moisture_pct) / 100.0
        if dry_fraction <= 0:
            return None
        return tsm_t / dry_fraction

    def wet_product_physical_to_tsm(
            self, physical_t: float) -> Optional[float]:
        """Return TSM contained in washed, still-wet product."""
        product_moisture_pct = getattr(self, "product_moisture_pct", None)
        if product_moisture_pct is None:
            return None
        return float(physical_t) * (
            1.0 - float(product_moisture_pct) / 100.0)


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
    averaging_h: float = 1.0         # physical compliance window; refined
    #                                # grids preserve this duration explicitly
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
    # Optional campaign grade for this demand bucket.  Blank preserves the
    # legacy contract and falls back to ``Destination.requested_grade``.
    # Keeping grade on the rate entry allows one receiving unit to request
    # several grade campaigns in different buckets of the same day (or across
    # different days) without cloning destinations.
    requested_grade: str = ""
    # A dated campaign can override its receiving unit's priority. None keeps
    # imported/legacy profiles inheriting Destination.priority.
    priority: Optional[int] = None


@dataclass
class CalendarEntry:
    scope: str                       # GLOBAL, machine id, LINK:/SITE:/LINE:
    start_bucket: int
    end_bucket: int                  # inclusive
    reason: str = "maintenance"
    # Canonical half-open physical interval from ``SimParams.start_iso``.
    # When both values are absent the legacy inclusive bucket window remains
    # authoritative.  Keeping these fields optional preserves imported plans
    # and the positional CalendarEntry(scope, start, end, reason) contract.
    start_minute: Optional[int] = None
    end_minute: Optional[int] = None
    # Resource-change action.  Existing/imported calendar rows omit this
    # field and therefore retain their established outage semantics.
    # ``RATE_CAP`` is an absolute physical/as-handled t/h ceiling; it never
    # forces production and never changes the engineering/base capacity.
    action: str = "UNAVAILABLE"      # UNAVAILABLE | RATE_CAP
    rate_tph: Optional[float] = None


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
    # Empty keeps the legacy direct physical-mass yield. ``beneficiation``
    # resolves the output factor from the output Grade's stage-aware profile;
    # ``drying`` removes the configured product moisture so output physical
    # tonnes equal TSM.
    mass_conversion: str = ""
    # Optional process ceiling documented on a dry-solids feed basis.  For a
    # beneficiation transfer the candidate builder converts it to the current
    # raw physical rate using the configured grade raw moisture.  Conveyor and
    # machine capacities themselves remain physical/as-handled t/h.
    dry_feed_capacity_tph: float = 0.0
    residence_min: int = 0
    depends_on_stage: str = ""
    dependency_delay_min: int = 0
    purpose: str = ""
    evidence: Evidence = field(default_factory=Evidence)
    treatment_family: str = ""       # washing opts into the configured alternatives


@dataclass
class BeltProfileEntry:
    """Material already in transit on a delayed link at horizon start (report §15.3 eq. 5)."""
    link_id: str
    arrive_bucket: int               # 0-based bucket in which it reaches the link dst
    mass_t: float
    grade: str
    quality: Dict[str, float] = field(default_factory=dict)
    material_id: str = ""


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
    # Optional canonical half-open commitment interval.  When present, mixed
    # resampling uses these minute offsets instead of widening the commitment
    # to the legacy start/end buckets.
    start_minute: Optional[int] = None
    end_minute: Optional[int] = None
    #: True only for commitments the PLANNER made to itself -- the frozen
    #: near-term jobs a replan carries forward. An operator instruction (the
    #: default) binds the rate exactly, and the plan reports infeasibility
    #: rather than quietly delivering less than a human asked for. The
    #: planner's own prior commitment instead yields to a changed world: held
    #: exactly when it still can be, paid for in the objective when it cannot,
    #: because a stale plan is worse than a revised one.
    advisory_rate: bool = False


# CP-SAT stores objective coefficients as integers. The direct tonne-scaled
# terms multiply an editable weight by WSCALE=10 in optimizer.py, while the
# fixed emergency spill/unstacked coefficient is 100,000. This cap therefore
# keeps each direct configured coefficient below that emergency coefficient.
OBJECTIVE_WEIGHT_MAX = 9_999.0
ACTIVE_OBJECTIVE_WEIGHT_FIELDS = (
    "w1_unmet",
    "w2_offspec",
    "w4_giveaway",
    "w5_moves",
    "w6_safety",
    "w7_utilisation",
)


@dataclass
class ObjectiveWeights:
    """w1..w7 planning priorities.

    A larger w1 is a useful demand-first heuristic, not a mathematical
    lexicographic guarantee: units and the number of applicable terms differ.
    """
    w1_unmet: float = 1000.0
    w2_offspec: float = 200.0
    w3_process_cost: float = 0.0     # needs GradeCostModel curves (NOT received — see register)
    w4_giveaway: float = 5.0
    w5_moves: float = 2.0
    w6_safety: float = 20.0
    w7_utilisation: float = 0.0


@dataclass
class SimParams:
    # ``dt_h`` / ``horizon_buckets`` remain the native, uniform solver grid
    # used by legacy scenarios, imports and the tactical (far-horizon) plan.
    # The minute fields below are the canonical physical-time configuration
    # for rolling/mixed-resolution planning.  ``horizon_minutes == 0`` is an
    # intentional compatibility sentinel: derive the physical horizon from
    # the native grid rather than silently turning a custom four-bucket test
    # or imported scenario into a 24-hour run.
    dt_h: float = 1.0
    horizon_buckets: int = 24
    frozen_buckets: int = 2          # jobs inside this window survive a replan
    mixed_resolution_enabled: bool = False
    horizon_minutes: int = 0
    near_term_step_min: int = 15
    near_term_horizon_min: int = 240
    far_term_step_min: int = 60
    replan_interval_min: int = 15
    commit_window_min: int = 30
    seed: int = 42
    start_iso: str = "2026-07-13T00:00:00"
    max_solve_s: float = 20.0
    deviation_tolerance: float = 0.10  # relative moved-mass shortfall triggering replan
    replan_on_deviation: bool = True

    def bucket_dt(self, bucket: int) -> _dt.datetime:
        return _dt.datetime.fromisoformat(self.start_iso) + _dt.timedelta(hours=self.dt_h * bucket)

    def native_bucket_minutes(self) -> int:
        """Return the legacy/native bucket width as whole physical minutes."""
        return int(round(float(self.dt_h) * 60.0))

    def effective_horizon_minutes(self) -> int:
        """Canonical physical horizon without changing legacy scenarios."""
        configured = int(self.horizon_minutes or 0)
        if configured > 0:
            return configured
        return self.native_bucket_minutes() * int(self.horizon_buckets)

    def uniform_grid_errors(self) -> List[str]:
        """Validate the physical invariants of the native solver grid."""
        errors: List[str] = []
        try:
            dt_h = float(self.dt_h)
        except (TypeError, ValueError):
            return ["dt_h must be a finite positive number"]
        if not math.isfinite(dt_h) or dt_h <= 0:
            errors.append("dt_h must be a finite positive number")
            return errors
        exact_minutes = dt_h * 60.0
        rounded_minutes = int(round(exact_minutes))
        if rounded_minutes <= 0 or not math.isclose(
                exact_minutes, rounded_minutes, abs_tol=1e-9):
            errors.append(
                "dt_h must represent a positive whole number of minutes")
        try:
            buckets = int(self.horizon_buckets)
        except (TypeError, ValueError):
            errors.append("horizon_buckets must be a positive integer")
            return errors
        if buckets != self.horizon_buckets or buckets <= 0:
            errors.append("horizon_buckets must be a positive integer")
        configured = int(self.horizon_minutes or 0)
        if configured < 0:
            errors.append("horizon_minutes cannot be negative")
        elif configured > 0 and rounded_minutes > 0 and buckets > 0 and \
                configured != rounded_minutes * buckets:
            errors.append(
                "horizon_minutes must equal horizon_buckets × dt_h")
        return errors

    def mixed_resolution_errors(self) -> List[str]:
        """Validate the currently supported near/far planning grid.

        The first implementation refines an independently solved near-term
        prefix and retains the existing native solve as the tactical tail.
        Consequently the far step must equal the native bucket width and the
        near/far boundary must lie on both grids.  Rejecting an unsafe grid is
        preferable to changing transport, calendar or demand durations.
        """
        errors: List[str] = []
        native = self.native_bucket_minutes()
        horizon = self.effective_horizon_minutes()
        near_step = int(self.near_term_step_min)
        near_horizon = int(self.near_term_horizon_min)
        far_step = int(self.far_term_step_min)
        replan = int(self.replan_interval_min)
        commit = int(self.commit_window_min)
        if native <= 0:
            errors.append("native bucket duration must be positive")
        if horizon <= 0:
            errors.append("horizon_minutes must be positive")
        if near_step <= 0 or far_step <= 0:
            errors.append("near and far planning steps must be positive")
        if near_horizon <= 0 or near_horizon > horizon:
            errors.append("near-term horizon must be within the total horizon")
        if far_step != native:
            errors.append(
                "far_term_step_min must equal the native dt_h bucket width")
        if near_step > far_step:
            errors.append("near-term step cannot exceed the far-term step")
        if near_step > 0 and far_step % near_step:
            errors.append("far-term step must be a multiple of the near-term step")
        if near_step > 0 and near_horizon % near_step:
            errors.append("near-term horizon must align to the near-term step")
        if far_step > 0 and (
                near_horizon % far_step or horizon % far_step):
            errors.append("near/far boundary and horizon must align to the far grid")
        if replan <= 0 or (near_step > 0 and replan % near_step):
            errors.append("replan interval must be a positive multiple of the near step")
        if commit < 0 or (near_step > 0 and commit % near_step):
            errors.append("commit window must be a multiple of the near step")
        return errors

    def bucket_start_minute(self, bucket: int) -> int:
        """Physical minute offset for a native compatibility bucket."""
        return int(bucket) * self.native_bucket_minutes()

    def minute_dt(self, minute: int) -> _dt.datetime:
        """Timestamp for a canonical minute offset from ``start_iso``."""
        return (_dt.datetime.fromisoformat(self.start_iso)
                + _dt.timedelta(minutes=int(minute)))


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

    def transfer_output_factor(self, transfer: ProcessTransfer) -> float:
        """Physical output tonnes per physical input tonne for a transfer.

        Conversion is opt-in so imported and non-beneficiation scenarios keep
        their existing ``yield_factor`` semantics.
        """
        conversion = str(getattr(transfer, "mass_conversion", "") or "")
        if not conversion:
            return float(transfer.yield_factor)
        grade_id = transfer.output_grade
        if not grade_id:
            try:
                grade_id = self.space(transfer.target_space).grade
            except StopIteration:
                grade_id = ""
        grade = self.grade(grade_id)
        if grade is None:
            return float(transfer.yield_factor)
        if conversion == "beneficiation":
            output_t = grade.raw_physical_to_wet_product(1.0)
            return (float(output_t) if output_t is not None
                    else float(transfer.yield_factor))
        if conversion == "drying":
            output_t = grade.wet_product_physical_to_tsm(1.0)
            return (float(output_t) if output_t is not None
                    else float(transfer.yield_factor))
        return float(transfer.yield_factor)

    def physical_tsm_info(
            self, physical_t: float, material_class: str,
            grade_id: str = "") -> Tuple[Optional[float], str]:
        """Return stage-correct TSM information for physical inventory.

        ``grade_id`` may be an explicit merchant-grade override from a job or
        process transfer.  This is essential for raw ``RAW-BT-*`` spaces:
        their eventual output campaign is not inherent in the raw grade, so a
        caller without that context receives ``None`` instead of a guess.
        """
        stage = str(material_class or "").strip().lower()
        if stage == "dry":
            return float(physical_t), "TSM"
        grade = self.grade(grade_id) if grade_id else None
        if grade is None:
            return None, ""
        if stage in ("raw", "raw_screened"):
            value = grade.raw_physical_to_tsm(physical_t)
            return value, "expected TSM" if value is not None else ""
        if stage == "washed":
            value = grade.wet_product_physical_to_tsm(physical_t)
            return value, "TSM equivalent" if value is not None else ""
        return None, ""

    def space_tsm_info(
            self, space: StockSpace, physical_t: Optional[float] = None,
            grade_id: str = "") -> Tuple[Optional[float], str]:
        """Convenience wrapper using a stock space's physical state/mass."""
        mass = space.mass_t if physical_t is None else float(physical_t)
        return self.physical_tsm_info(
            mass, space.material_class, grade_id or space.grade)

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
        # Rows are editable/importable and the established contract is that
        # the last matching row wins.  Search backwards so a weekly profile
        # does not scan all 1,680 entries when the requested row is near the
        # end, while preserving that contract exactly.
        for e in reversed(self.demand):
            if e.dest_id == dest_id and e.bucket == bucket:
                return e.rate_tph
        return self.destination(dest_id).demand_default_tph

    def demand_grade(self, dest_id: str, bucket: int) -> str:
        """Return the requested campaign grade at one planning bucket.

        Imported/legacy scenarios store the grade only on ``Destination``.
        New weekly plans may override it on each ``DemandEntry``.  As with
        :meth:`demand_tph`, a later matching row wins; a blank row remains a
        deliberate request to use the destination default rather than an
        unknown grade.
        """
        for entry in reversed(self.demand):
            if entry.dest_id == dest_id and entry.bucket == bucket:
                candidate = str(getattr(
                    entry, "requested_grade", "") or "").strip()
                # Blank is meaningful: it explicitly asks for the
                # destination default and must not expose an older duplicate
                # row's grade.
                return candidate or str(
                    self.destination(dest_id).requested_grade or "").strip()
        return str(self.destination(dest_id).requested_grade or "").strip()

    def demand_priority(self, dest_id: str, bucket: int) -> int:
        """Effective positive-integer priority; the last bucket row wins."""
        priority = self.destination(dest_id).priority
        for entry in reversed(self.demand):
            if entry.dest_id == dest_id and entry.bucket == bucket:
                override = getattr(entry, "priority", None)
                if override is not None:
                    priority = override
                break
        try:
            value = float(priority)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"{dest_id}@{bucket}: priority must be a positive integer") from None
        if isinstance(priority, bool) or not math.isfinite(value) or value < 1 or not value.is_integer():
            raise ValueError(f"{dest_id}@{bucket}: priority must be a positive integer")
        return int(value)

    def demand_spec(self, dest_id: str, bucket: int) -> List[SpecRange]:
        """Return the receiving limits active for one demand campaign.

        A requested specification grade (for example a train-export profile)
        overrides the receiving unit's fallback limits for that bucket. A
        physical grade keeps the destination-level specification unchanged.
        """
        destination = self.destination(dest_id)
        requested = self.demand_grade(dest_id, bucket)
        grade = self.grade(requested) if requested else None
        if grade is not None and getattr(grade, "is_specification", False) \
                and getattr(grade, "receiving_spec", None):
            return list(grade.receiving_spec)
        return list(destination.spec)

    def quality_of(self, space_id: str) -> Dict[str, float]:
        sp = self.space(space_id)
        if sp.quality:
            return sp.quality
        unknown = self.config.get(UNKNOWN_STOCK_QUALITY_KEY, [])
        if isinstance(unknown, (list, tuple, set)) and space_id in unknown:
            return {}
        g = self.grade(sp.grade)
        return dict(g.quality) if g else {}

    def calendar_interval_minutes(
            self, entry: CalendarEntry) -> Tuple[int, int]:
        """Return one calendar interval as canonical half-open minutes.

        Exact minute fields take precedence only when the pair is complete.
        An incomplete pair is left to validation and safely falls back to the
        legacy inclusive bucket representation here.
        """
        if entry.start_minute is not None and entry.end_minute is not None:
            return int(entry.start_minute), int(entry.end_minute)
        step = self.sim.native_bucket_minutes()
        return (int(entry.start_bucket) * step,
                (int(entry.end_bucket) + 1) * step)

    def calendar_scope_blocked(
            self, scope: str, bucket: Optional[int] = None, *,
            minute: Optional[int] = None,
            end_minute: Optional[int] = None) -> bool:
        """Whether ``scope`` is unavailable at a point or over an interval.

        ``bucket`` is interpreted as the complete physical bucket, so an
        exact contingency intersecting an hourly tactical bucket blocks that
        hour conservatively.  ``minute`` is a point unless ``end_minute`` is
        supplied, in which case the queried interval is half-open.
        """
        if bucket is None and minute is None:
            raise ValueError("bucket or minute is required")
        if minute is None:
            step = self.sim.native_bucket_minutes()
            query_start = int(bucket) * step
            query_end = query_start + step
        else:
            query_start = int(minute)
            query_end = (int(end_minute) if end_minute is not None
                         else query_start + 1)
        for entry in self.calendars:
            if entry.scope not in ("GLOBAL", scope):
                continue
            if str(getattr(entry, "action", "UNAVAILABLE") or
                   "UNAVAILABLE").strip().upper() != "UNAVAILABLE":
                continue
            start, end = self.calendar_interval_minutes(entry)
            if query_start < end and start < query_end:
                return True
        return False

    def resource_rate_cap_tph(
            self, scopes: Iterable[str] | str,
            bucket: Optional[int] = None, *, minute: Optional[int] = None,
            end_minute: Optional[int] = None) -> Optional[float]:
        """Return the tightest active timed rate ceiling for resources.

        Rate changes are absolute physical/as-handled t/h caps.  Querying a
        coarse bucket is deliberately conservative: any exact window that
        intersects it applies to that whole solve bucket.  Fine-grid mixed
        planning retains the exact half-open minute interval.
        """
        if bucket is None and minute is None:
            raise ValueError("bucket or minute is required")
        requested = {scopes} if isinstance(scopes, str) else set(scopes)
        if minute is None:
            step = self.sim.native_bucket_minutes()
            query_start = int(bucket) * step
            query_end = query_start + step
        else:
            query_start = int(minute)
            query_end = (int(end_minute) if end_minute is not None
                         else query_start + 1)
        caps: List[float] = []
        for entry in self.calendars:
            if entry.scope not in requested:
                continue
            if str(getattr(entry, "action", "UNAVAILABLE") or
                   "UNAVAILABLE").strip().upper() != "RATE_CAP":
                continue
            rate = getattr(entry, "rate_tph", None)
            if rate is None:
                continue
            start, end = self.calendar_interval_minutes(entry)
            if query_start < end and start < query_end:
                caps.append(float(rate))
        return min(caps) if caps else None

    def effective_resource_rate_tph(
            self, scopes: Iterable[str] | str, base_rate_tph: float,
            bucket: Optional[int] = None, *, minute: Optional[int] = None,
            end_minute: Optional[int] = None) -> float:
        """Return the base engineering rate limited by active timed caps."""
        requested = {scopes} if isinstance(scopes, str) else set(scopes)
        if self.any_calendar_scope_blocked(
                requested, bucket, minute=minute, end_minute=end_minute):
            return 0.0
        cap = self.resource_rate_cap_tph(
            requested, bucket, minute=minute, end_minute=end_minute)
        return min(float(base_rate_tph), cap) if cap is not None \
            else float(base_rate_tph)

    def process_line_capacity_tph(self, line_id: str) -> Optional[float]:
        """Return one configured whole-run physical process-line ceiling.

        The value is separate from a transfer's dry-feed design datum: the
        planning cap is always physical/as-handled tonnes per hour.
        """
        raw = self.config.get("PROCESS_LINE_CAPACITY_TPH", {})
        if not isinstance(raw, dict) or line_id not in raw:
            return None
        try:
            return float(raw[line_id])
        except (TypeError, ValueError):
            return None

    def any_calendar_scope_blocked(
            self, scopes: Iterable[str], bucket: Optional[int] = None, *,
            minute: Optional[int] = None,
            end_minute: Optional[int] = None) -> bool:
        """Return true when any activity scope is blocked by its calendar."""
        return any(self.calendar_scope_blocked(
            scope, bucket, minute=minute, end_minute=end_minute)
            for scope in scopes)

    def effective_resource_available(
            self, scope: str, base_available: bool = True,
            bucket: Optional[int] = None, *, minute: Optional[int] = None,
            end_minute: Optional[int] = None) -> bool:
        """Combine a resource's whole-run state with timed outage windows."""
        if not bool(base_available):
            return False
        return not self.calendar_scope_blocked(
            scope, bucket, minute=minute, end_minute=end_minute)

    def machine_blocked(
            self, mid: str, bucket: int, *, minute: Optional[int] = None,
            end_minute: Optional[int] = None) -> bool:
        """Backward-compatible machine calendar lookup."""
        return self.calendar_scope_blocked(
            mid, bucket, minute=minute, end_minute=end_minute)


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
    # ---- physical-run identity (post-solve, derived from THIS window) ------
    # Run identity belongs to the execution window, not to the parent Job.
    # One allocation job can hold windows in SEVERAL physical runs: two of its
    # buckets may be separated by a real stop, in which case its windows sit in
    # different runs and must not share a run id.  Populated by
    # ``optimizer.annotate_physical_runs``; empty on an un-annotated plan.
    physical_run_id: str = ""
    #: allocation jobs contributing to *this window's* run
    physical_run_job_count: int = 1
    #: minutes this window's run is deliberately held running, no material
    physical_run_hold_min: int = 0
    #: this window continues a run that began on an earlier civil day
    continues_previous_planning_day: bool = False
    #: How far this window's rate can be trusted as a real operating rate.
    #:   STABLE_RATE      - at or above the machine's declared minimum rate
    #:   NO_STABLE_MINIMUM - the machine declares no minimum rate, so nothing
    #:                       in the model validates how slowly it may run
    #:   BELOW_MIN_PARCEL  - the parcel is under the configured minimum parcel;
    #:                       the duration is the physical minimum and is NOT
    #:                       widened, because widening a token parcel makes an
    #:                       implausible rate look like a normal run
    rate_credibility: str = "STABLE_RATE"


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
    # Canonical physical reservation interval [start_minute, end_minute).
    # These fields allow a mixed-resolution prefix and hourly tactical tail to
    # coexist without pretending that every decision lasts a native bucket.
    # ``None`` identifies an older imported plan; consumers may then derive
    # the interval from start/end_bucket and the scenario's native grid.
    start_minute: Optional[int] = None
    end_minute: Optional[int] = None
    path: List[str] = field(default_factory=list)   # link ids used
    # Candidate routing discriminator.  Some physical links can feed the same
    # pile through different receiving exits; retaining it makes an extracted
    # tactical job map back to exactly one near-term candidate.
    exit_node: str = ""
    method: str = "direct"           # stacking pattern -> ASP Method
    # A chevron job is one quality campaign contributing thin layers to a
    # configured multi-grade pile recipe. The recipe shares are physical
    # stacking mass; chemistry is evaluated on the appropriate dry/TSM basis.
    blend_mode: str = ""
    blend_recipe: List[Dict[str, object]] = field(default_factory=list)
    # Selected lot metadata travels with each job, so a multi-window plan does
    # not apply its final lot's quantity/identity to earlier physical work.
    blend_campaign: Dict[str, object] = field(default_factory=dict)
    chevron_passes_per_cycle: int = 0
    chevron_grade_passes: int = 0
    # Flow-derived traversal estimate, including an explicit unavailable status
    # when stacking speed/geometry is missing; never a fixed repeatable cycle.
    chevron_pass_estimate: Dict[str, object] = field(default_factory=dict)
    origin: str = "AUTO"             # AUTO | HUMAN
    locked: bool = False
    status: str = "PLANNED"          # PLANNED|DISPATCHED|RUNNING|FINISHED|CANCELLED|REJECTED
    start_x: float = 0.0
    stop_x: float = 0.0
    comment: str = ""
    # Effective machine ceiling used by this solve/execution overlay.
    # Populated from the lower of Machine.rate_max_tph × rate factor and the
    # candidate path/process ceiling (then any lower fixed-job rate). Zero
    # keeps legacy jobs
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
    # Receiving certification assigned at the final interface.  This is
    # deliberately separate from ``grade`` / ``output_grade`` so the source
    # stream and treatment lineage are never overwritten by a sales or plant
    # specification label (for example G10 at PAP).
    certified_grade: str = ""
    output_tonnage_t: float = 0.0
    process_stage: str = ""
    depends_on_stage: str = ""
    dependency_delay_min: int = 0
    residence_min: int = 0
    purpose: str = ""
    transfer_id: str = ""
    mass_conversion: str = ""
    # ---- schedule identity -------------------------------------------------
    # A Job is an ALLOCATION CONTAINER: one activity group, one rolling
    # planning window, one contiguous block of active buckets.  It is not
    # necessarily one physical run, and a run is what an operator schedules.
    # These fields, populated by ``optimizer.annotate_physical_runs``, keep
    # the three levels distinguishable instead of conflated:
    #   * ``physical_run_id``   — the machine/activity-group run this job is
    #     part of, derived from the MINUTE schedule.  Several jobs share it
    #     when nothing physically stopped between them, including across a
    #     rolling planning-day boundary.
    #   * ``allocation_slice_count`` — per-bucket allocation rows inside this
    #     job.  Slices are accounting, not separate jobs.
    #   * ``continues_previous_planning_day`` — this job exists only because
    #     the rolling solve window ended, not because work stopped.
    #   Run identity is authoritative on each JobExecutionWindow, because one
    #   allocation job can span several physical runs when two of its buckets
    #   are separated by a real stop.  The job-level fields below describe the
    #   FIRST run only and exist for legacy consumers; ``physical_run_ids``
    #   lists every run this job contributes to.
    physical_run_id: str = ""
    physical_run_ids: List[str] = field(default_factory=list)
    physical_run_count: int = 0
    physical_run_job_count: int = 1
    physical_run_start_minute: Optional[int] = None
    physical_run_end_minute: Optional[int] = None
    physical_run_hold_minutes: int = 0
    allocation_slice_count: int = 1
    continues_previous_planning_day: bool = False
    treatment_id: str = ""
    # Exact component accounting inside one physical allocation job. Each
    # row carries bucket, material identities, input/output tonnes and assays.
    material_flows: List[Dict[str, object]] = field(default_factory=list)


@dataclass
class PlanResult:
    status: str                      # OPTIMAL | FEASIBLE | INFEASIBLE | NO_SOLVER | ERROR
    objective_pass_records: List[Dict[str, object]] = field(default_factory=list, kw_only=True)
    material_catalog: Dict[str, Dict[str, object]] = field(default_factory=dict, kw_only=True)
    material_inventory: Dict[str, Dict[str, List[float]]] = field(default_factory=dict, kw_only=True)
    material_pending: Dict[str, Dict[str, List[Dict[str, float]]]] = field(default_factory=dict, kw_only=True)
    jobs: List[Job] = field(default_factory=list)
    unmet: Dict[str, List[float]] = field(default_factory=dict)      # dest -> per-bucket tonnes
    offspec: Dict[str, List[float]] = field(default_factory=dict)    # dest -> per-bucket slack (t·unit)
    safety_short: Dict[str, List[float]] = field(default_factory=dict)  # space -> per-bucket tonnes
    surge_unstacked: List[float] = field(default_factory=list)       # emergency slack per bucket
    delivered: Dict[str, List[float]] = field(default_factory=dict)  # dest -> per-bucket tph
    # Policy-scoped continuous-feed state. Values are 0/1 on a uniform solve;
    # a mixed-resolution merge may store a native-bucket fraction for
    # ``feed_gap`` and an episode count for ``feed_interruption_start`` so the
    # exact fine-grid duration/frequency is not lost during aggregation.
    feed_gap: Dict[str, List[float]] = field(default_factory=dict)
    feed_interruption_start: Dict[str, List[float]] = field(default_factory=dict)
    inventory: Dict[str, List[float]] = field(default_factory=dict)  # space -> per-bucket tonnes (t index 0..T)
    process_reject: Dict[str, List[float]] = field(default_factory=dict)
    # Whole-batch pile compositions selected by the blend-design model.
    # Percentages are derived from integer physical tonnes.  Matrix-mode
    # campaigns are included too so Monitor/export can compare configured and
    # optimized decisions through one stable contract.
    blend_campaigns: Dict[str, Dict[str, object]] = field(default_factory=dict)
    machine_dispositions: Dict[str, Dict[str, object]] = field(default_factory=dict)
    #: machine id -> ABSOLUTE buckets the plan pays to keep the machine running
    #: through without moving material. The minute layer reads this so a blank
    #: interval the hourly model priced as a hold is reported as a hold, not as
    #: a stop and a later start.
    machine_hold_buckets: Dict[str, List[int]] = field(default_factory=dict)
    objective: float = 0.0
    breakdown: Dict[str, float] = field(default_factory=dict)
    solver: str = "cp-sat"
    solver_wall_s: float = 0.0
    solver_best_bound: float = 0.0
    solver_relative_gap_pct: float = 0.0
    solver_branches: int = 0
    solver_conflicts: int = 0
    solver_deterministic_time: float = 0.0
    solver_optimality_scope: str = "whole_plan"
    reasons: List[str] = field(default_factory=list)                 # reason codes for gaps
    relaxations: List[str] = field(default_factory=list)             # suggested relaxations
    horizon_offset: int = 0          # first bucket this plan covers (for replans)
    # Boundary state prevents rolling windows/replans from manufacturing a
    # stop/start or a second feed-interruption episode at every solve edge.
    initial_machine_groups: Dict[str, Tuple[object, ...]] = field(
        default_factory=dict)
    terminal_machine_groups: Dict[str, Tuple[object, ...]] = field(
        default_factory=dict)
    terminal_feed_gap: Dict[str, bool] = field(default_factory=dict)
    terminal_open_feed_gap_minutes: Dict[str, float] = field(
        default_factory=dict)
    initial_feed_gap: Dict[str, bool] = field(default_factory=dict)
    initial_open_feed_gap_minutes: Dict[str, float] = field(
        default_factory=dict)


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
    # Optional exact execution time.  Legacy callers continue to provide only
    # ``bucket`` / ``duration_buckets``; the simulator resolves those to the
    # corresponding minute boundaries.  When supplied, ``at_iso`` takes
    # precedence over ``minute``, and ``duration_minutes`` takes precedence
    # over ``duration_buckets``.
    minute: Optional[int] = None
    duration_minutes: Optional[int] = None
    at_iso: str = ""


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
