"""Planning / optimisation engine — layer 4.

Bucketised MILP on CP-SAT (OR-Tools), implementing report §15.1–15.4:
  * mass flows x[machine, space, dest, path, t] are the primary scheduling
    variables; fixed blend matrices remain inputs, while optimized pile
    matrices and their grade-specific stacking flows are solved together;
  * one activity boolean per (machine, space, destination) GROUP — a machine
    works one job at a time, but its stream may split over parallel route
    legs (e.g. the two H01–H04 conveyors to PAP);
  * inventory mass balance with Δt, initial + terminal conditions (eq. 1);
  * exact demand balance `delivered + unmet = demand` with dominant w1
    (eq. 4), preventing zero-demand or surplus delivery;
  * per-bucket mass-weighted quality constraints, hard or soft (eq. 3);
  * corridor transport delay with initial belt profile (eq. 5);
  * machine exclusivity, reachability, work calendars, travel/setup gaps,
    min-stable rates, route-link capacities, shared/conflict groups,
    stockpile capacity, safety stock (soft), move/start-stop penalties,
    operator-fixed jobs, banned tuples (operator rejections).

Integer scaling: masses in whole tonnes / bucket; quality unit ×100.
The engine is UI-agnostic: Scenario in -> PlanResult out.
"""
from __future__ import annotations

import copy
import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple

from .coordinates import (format_repere_range, repere_interval_to_x)
from .blending import (BLEND_MODE_MATRIX, BLEND_MODE_OPTIMIZED, CAMPAIGN_BUILDING,
                       CAMPAIGN_COMPLETE, CAMPAIGN_DEPLETED,
                       CHEVRON_CONFIG_KEY,
                       chevron_batch_allocation,
                       chevron_catalog_allocations,
                       chevron_blended_quality, chevron_campaign_is_complete,
                       chevron_campaign_state, chevron_effective_grade,
                       chevron_enabled_for_space,
                       chevron_component_bounds,
                       chevron_component_grade_ids,
                       chevron_config_errors,
                       chevron_construction_batch_t,
                       chevron_job_metadata,
                       chevron_is_demand_driven,
                       chevron_minimum_component_fraction,
                       chevron_matrix_catalog,
                       chevron_mode, chevron_ready_minute, chevron_recipe,
                       chevron_receiving_quality_checks,
                       chevron_result_grade, chevron_settings,
                       _reachable_receiving_specs,
                       reopen_depleted_chevron_campaigns,
                       update_chevron_job_passes,
                       update_chevron_campaign_state)
from .domain import (ACTIVE_OBJECTIVE_WEIGHT_FIELDS, OBJECTIVE_WEIGHT_MAX,
                     BeltProfileEntry, FixedJob, Job, JobBucketFlow,
                     PlanResult, Scenario, Segment)
from .campaigns import (ABSOLUTE_MIN_PARCEL_T, campaign_policy_enabled,
                        resolve_campaign_policy, short_parcel_penalty)
from .continuity import (compute_continuity_metrics,
                         continuity_gap_threshold_t,
                         parse_continuity_policies)
from .runs import (executed_transition_metrics, hourly_transition_metrics,
                   micro_parcel_metrics, physical_runs, reconcile_transitions,
                   window_boundary_delta)
from .energy import (ENERGY_OBJECTIVE_SCALE,
                     work_route_energy_coefficient)
from .flow_config import (PROCESS_LINE_STAGE_MAP,
                          effective_corridor_targets,
                          effective_process_stage_targets,
                          process_stage_enabled)
from .movement import estimate_space_transition, estimate_transition, work_midpoint_x
from .objective_config import (operational_objective_errors,
                               operational_penalty, scheduling_values,
                               isolated_source_target_shortages)
from .scheduling_objective import add_minimum_job_volume, refine_objectives
from . import dynamic_blending, prewash_blending, washing
from .routes import (RouteGraph, Path, reclaim_candidates, stack_candidates,
                     space_node)
from .timing import refine_execution_timing, validate_execution_windows

QSCALE = 100          # quality unit scale (BPL 71.0 -> 7100)
WSCALE = 10           # objective weight scale to integers
BIG_PENALTY = 10 ** 5  # emergency slacks (spill / unstacked arrivals) — must
#                        dominate w1·WSCALE (default 10^4/t) without exploding
#                        the objective bound
QSLACK_MAX = 10 ** 8   # tight domains matter for CP-SAT propagation



def _positive_scaled_weight(value: float, scale: float) -> int:
    """Return an effective integer coefficient for a configured priority.

    Zero means disabled. Any positive priority remains active even when its
    scaled value is below one integer coefficient, rather than being silently
    rounded to a disabled term.
    """
    value = float(value)
    if value <= 0:
        return 0
    return max(int(round(value * scale)), 1)


def _objective_weight_coefficients(weights) -> Dict[str, int]:
    """Return effective base coefficients for the active objective terms."""
    return {
        "w1_unmet": _positive_scaled_weight(weights.w1_unmet, WSCALE),
        "w2_offspec": _positive_scaled_weight(
            weights.w2_offspec, WSCALE / QSCALE),
        "w5_moves": _positive_scaled_weight(weights.w5_moves, WSCALE),
        "w6_safety": _positive_scaled_weight(weights.w6_safety, WSCALE),
        # X is measured in whole physical tonnes, so this reward has one
        # integer coefficient of resolution per configured weight unit.
        "w7_utilisation": _positive_scaled_weight(
            weights.w7_utilisation, WSCALE / 10),
    }


def _invalid_objective_weights(weights) -> List[str]:
    invalid = []
    for field_name in ACTIVE_OBJECTIVE_WEIGHT_FIELDS:
        try:
            value = float(getattr(weights, field_name))
        except (AttributeError, TypeError, ValueError):
            invalid.append(field_name)
            continue
        if (value != value or value in (float("inf"), float("-inf")) or
                value < 0 or value > OBJECTIVE_WEIGHT_MAX or
                not value.is_integer()):
            invalid.append(field_name)
    return invalid


def _initial_state_errors(sc: Scenario, opts: "SolveOptions") -> List[str]:
    """Validate public rolling-boundary inputs before any model arithmetic."""
    errors: List[str] = []
    machine_groups = opts.initial_machine_groups
    feed_flags = opts.initial_feed_gap
    open_minutes = opts.initial_open_feed_gap_minutes
    if not isinstance(machine_groups, Mapping):
        errors.append("initial_machine_groups must be a mapping")
        machine_groups = {}
    if not isinstance(feed_flags, Mapping):
        errors.append("initial_feed_gap must be a mapping")
        feed_flags = {}
    if not isinstance(open_minutes, Mapping):
        errors.append("initial_open_feed_gap_minutes must be a mapping")
        open_minutes = {}

    machine_ids = {machine.id for machine in sc.machines}
    destination_ids = {destination.id for destination in sc.destinations}
    for machine_id, group in machine_groups.items():
        if str(machine_id) not in machine_ids:
            errors.append(
                f"initial_machine_groups[{machine_id}] has unknown machine")
        if not isinstance(group, (list, tuple)) or len(group) != 7:
            errors.append(
                f"initial_machine_groups[{machine_id}] must be a 7-field "
                "activity-group key")
        elif str(group[1]) != str(machine_id):
            errors.append(
                f"initial_machine_groups[{machine_id}] belongs to machine "
                f"{group[1]}")
    for destination_id, flag in feed_flags.items():
        if str(destination_id) not in destination_ids:
            errors.append(
                f"initial_feed_gap[{destination_id}] has unknown destination")
        if not isinstance(flag, bool):
            errors.append(
                f"initial_feed_gap[{destination_id}] must be true or false")
    for destination_id, raw_minutes in open_minutes.items():
        if str(destination_id) not in destination_ids:
            errors.append(
                "initial_open_feed_gap_minutes"
                f"[{destination_id}] has unknown destination")
        try:
            minutes = float(raw_minutes)
        except (TypeError, ValueError, OverflowError):
            minutes = float("nan")
        if (isinstance(raw_minutes, bool) or not math.isfinite(minutes)
                or minutes < 0):
            errors.append(
                "initial_open_feed_gap_minutes"
                f"[{destination_id}] must be finite and nonnegative")
        elif minutes > 0 and feed_flags.get(destination_id) is not True:
            errors.append(
                "initial_open_feed_gap_minutes"
                f"[{destination_id}] is positive but initial_feed_gap is "
                "not true")
    return errors


@dataclass
class Cand:
    """One allocatable (machine, space, destination-or-corridor, path) tuple."""
    kind: str                 # reclaim | stack | dispatch
    machine: str
    space: str
    dest_id: str = ""         # destination id (reclaim) / corridor link id (dispatch)
    grade: str = ""           # stacking grade (stack cands are per grade)
    path: Optional[Path] = None
    exit_node: str = ""       # dispatch: corridor exit (arrival) node
    transfer_id: str = ""      # explicit Scenario.process_transfers row
    source_space: str = ""
    target_space: str = ""
    operation_kind: str = ""   # stack | reclaim for process transfers
    output_grade: str = ""
    mass_conversion: str = ""
    yield_factor: float = 1.0
    process_stage: str = ""
    residence_min: int = 0
    depends_on_stage: str = ""
    dependency_delay_min: int = 0
    purpose: str = ""
    material_id: str = ""
    output_material_id: str = ""
    quality_override: Optional[Dict[str, float]] = None
    treatment_id: str = ""

    def gkey(self) -> Tuple:
        # Components of the same physical command share equipment capacity
        # and one activity. Washing modes remain mutually exclusive commands.
        group_grade = self.grade
        if self.material_id and not (self.kind == "stack" and
                                     "Chevron component" in self.purpose):
            group_grade = "TREAT:" + self.treatment_id if self.treatment_id else ""
            if not self.treatment_id and self.depends_on_stage:
                group_grade = f"MATURE:{self.depends_on_stage}:{self.dependency_delay_min}"
        return (self.kind, self.machine, self.space, self.dest_id,
                group_grade, self.exit_node, self.transfer_id)

    def input_space(self) -> str:
        if self.kind == "process":
            return self.source_space
        return self.space if self.kind in ("reclaim", "dispatch") else ""

    def output_space(self) -> str:
        if self.kind == "process":
            return self.target_space
        return self.space if self.kind == "stack" else ""

    def display_kind(self) -> str:
        return self.operation_kind if self.kind == "process" else self.kind


@dataclass
class SolveOptions:
    horizon_offset: int = 0                    # first bucket index (for replans)
    # Exact canonical replan origin.  ``None`` preserves legacy bucket-start
    # behaviour; mixed rolling plans may start at e.g. minute 630 (10:30).
    start_minute_offset: Optional[int] = None
    banned: List[Tuple[str, str, str, int, int]] = field(default_factory=list)
    # (machine, space, dest_id, from_bucket, to_bucket) tuples forced to zero
    machine_rate_factor: Dict[str, float] = field(default_factory=dict)
    max_time_s: Optional[float] = None
    # Actual wall-clock guard.  ``max_time_s`` is retained as the deterministic
    # CP-SAT search budget for reproducibility; long weekly runs additionally
    # need a genuine operator-facing elapsed-time ceiling.
    max_wall_time_s: Optional[float] = None
    log: bool = False
    # Physical state immediately before the first model bucket.  Rolling
    # windows and replans use it to avoid counting artificial boundary
    # starts/changeovers or duplicate interruption episodes.
    initial_machine_groups: Dict[str, Tuple[object, ...]] = field(
        default_factory=dict)
    initial_feed_gap: Dict[str, bool] = field(default_factory=dict)
    initial_open_feed_gap_minutes: Dict[str, float] = field(
        default_factory=dict)


def job_group_key(job: Job) -> Tuple[object, ...]:
    """Reconstruct the optimizer activity group represented by a job."""
    if str(getattr(job, "transfer_id", "") or ""):
        return (
            "process", job.machine, job.space, "",
            ("TREAT:" + job.treatment_id if job.treatment_id else
             f"MATURE:{job.depends_on_stage}:{job.dependency_delay_min}" if job.depends_on_stage else "") if job.material_flows else job.grade, "",
            job.transfer_id)
    kind = str(job.kind or "")
    grade = job.grade if kind in {"stack", "dispatch"} else ""
    if job.material_flows and kind in ("stack", "dispatch") and not (
            kind == "stack" and "Chevron component" in job.purpose):
        grade = ""
    if job.material_flows and kind in ("reclaim", "dispatch") and job.depends_on_stage:
        grade = f"MATURE:{job.depends_on_stage}:{job.dependency_delay_min}"
    exit_node = job.exit_node if kind in {"stack", "dispatch"} else ""
    return (
        kind, job.machine, job.space, job.dest_id, grade, exit_node, "")


def _job_time_bounds(job: Job, bucket_minutes: int) -> Tuple[int, int]:
    start = getattr(job, "start_minute", None)
    end = getattr(job, "end_minute", None)
    if start is None:
        start = int(job.start_bucket) * bucket_minutes
    if end is None:
        end = (int(job.end_bucket) + 1) * bucket_minutes
    return int(start), int(end)


def _job_operational_metrics(
        sc: Scenario, result: PlanResult, *,
        horizon_start_minute: int,
        horizon_end_minute: int) -> Dict[str, float]:
    """Recompute transition/energy totals after a multi-grid job merge."""
    native_minutes = max(sc.sim.native_bucket_minutes(), 1)
    intervals: Dict[str, List[Tuple[int, int, Tuple[object, ...]]]] = {}
    handled_t = 0.0
    energy_centi_kwh = 0.0
    for job in result.jobs:
        start, end = _job_time_bounds(job, native_minutes)
        start = max(start, horizon_start_minute)
        end = min(end, horizon_end_minute)
        if end > start:
            intervals.setdefault(job.machine, []).append(
                (start, end, job_group_key(job)))
        mass_t = max(float(getattr(job, "tonnage_t", 0.0) or 0.0), 0.0)
        handled_t += mass_t
        flows = list(getattr(job, "bucket_flows", ()) or ())
        if flows:
            for flow in flows:
                flow_t = max(float(flow.tonnage_t or 0.0), 0.0)
                links = flow.path or getattr(job, "path", ()) or ()
                energy_centi_kwh += flow_t * work_route_energy_coefficient(
                    sc, job.kind, links)
        else:
            energy_centi_kwh += mass_t * work_route_energy_coefficient(
                sc, job.kind, getattr(job, "path", ()) or ())

    starts = stops = changeovers = 0
    active_minutes = 0
    initial_groups = dict(getattr(
        result, "initial_machine_groups", {}) or {})
    machine_ids = set(intervals) | set(initial_groups)
    for machine_id in machine_ids:
        rows = sorted(intervals.get(machine_id, ()), key=lambda row: row[:2])
        previous_group = initial_groups.get(machine_id)
        if isinstance(previous_group, list):
            previous_group = tuple(previous_group)
        cursor = horizon_start_minute
        for start, end, group in rows:
            if start > cursor and previous_group is not None:
                stops += 1
                previous_group = None
            if previous_group is None:
                starts += 1
            elif tuple(previous_group) != group:
                changeovers += 1
            # Same-group extraction runs can split at a rate/fixed-job
            # boundary without representing a physical state change.
            active_minutes += max(end - max(start, cursor), 0)
            cursor = max(cursor, end)
            previous_group = group
        if previous_group is not None and cursor < horizon_end_minute:
            stops += 1

    transitions = float(starts + stops + changeovers)
    return {
        "scheduled_starts": float(starts),
        "scheduled_stops": float(stops),
        "scheduled_group_changeovers": float(changeovers),
        "machine_start_count": float(starts),
        "machine_stop_count": float(stops),
        "machine_changeover_count": float(changeovers),
        "machine_state_transition_count": transitions,
        "moves": transitions,
        "active_machine_h": active_minutes / 60.0,
        "handled_t": handled_t,
        "work_link_energy_kwh": energy_centi_kwh / ENERGY_OBJECTIVE_SCALE,
    }


def annotate_physical_runs(
        sc: Scenario, result: PlanResult) -> List[object]:
    """Label every job with the physical run and slice identity it belongs to.

    A parent solver job is an *allocation container*: it exists per rolling
    planning window, per activity group and per contiguous block of active
    buckets.  Operators do not schedule allocation containers, they schedule
    runs: one machine, one activity group, continuous in time.  One physical
    run may therefore span several parent jobs — most obviously across the
    rolling civil-day boundary, where the previous model split one continuous
    campaign into two jobs purely because the solve window ended.

    This never merges anything physical: two jobs join one run only when the
    minute schedule shows no real stop between them (see
    :func:`mph_sim.runs.classify_gap`).  Low-tonnage work keeps its own slice
    row and is never dropped.

    Identity is stamped on each **execution window**, not only on the parent
    job.  The relationship is many-to-many: one run can span several jobs (a
    campaign through the rolling civil-day boundary) and one job can span
    several runs (two of its buckets separated by a real stop).  Labelling only
    the job would give every bar of such a job the first run's id, which is
    wrong for every later bar.  The job-level fields describe the first run and
    remain for legacy consumers; ``Job.physical_run_ids`` lists them all.
    """
    runs = physical_runs(
        sc, result.jobs,
        initial_machine_groups=getattr(
            result, "initial_machine_groups", {}) or {},
        machine_hold_buckets=getattr(
            result, "machine_hold_buckets", {}) or {})
    day_minutes = 1_440
    by_job: Dict[str, List[object]] = {}
    for run in runs:
        for job_id in run.job_ids:
            if run not in by_job.setdefault(job_id, []):
                by_job[job_id].append(run)
        for segment in run.segments:
            window = getattr(segment, "window", None)
            if window is None:
                continue
            window.physical_run_id = run.id
            window.physical_run_job_count = len(run.job_ids)
            window.physical_run_hold_min = run.hold_minutes
            window.continues_previous_planning_day = bool(
                run.start_minute // day_minutes
                < segment.start_minute // day_minutes)

    for job in result.jobs:
        owning = by_job.get(job.id, [])
        run = owning[0] if owning else None
        job.physical_run_ids = [item.id for item in owning]
        job.physical_run_count = len(owning)
        job.physical_run_id = run.id if run is not None else ""
        job.physical_run_job_count = (
            len(run.job_ids) if run is not None else 1)
        job.physical_run_start_minute = (
            run.start_minute if run is not None else None)
        job.physical_run_end_minute = (
            run.end_minute if run is not None else None)
        job.physical_run_hold_minutes = (
            run.hold_minutes if run is not None else 0)
        job.allocation_slice_count = len(
            list(getattr(job, "bucket_flows", ()) or ())) or 1
        # This job exists only because the rolling solve window ended when it
        # is a later job of a run that began on an earlier civil day.
        job_start = _job_time_bounds(
            job, max(sc.sim.native_bucket_minutes(), 1))[0]
        job.continues_previous_planning_day = bool(
            run is not None
            and run.job_ids.index(job.id) > 0
            and run.start_minute // day_minutes < job_start // day_minutes)
    return runs


def _executed_schedule_breakdown(
        sc: Scenario, result: PlanResult, jobs: List[Job], *,
        horizon_start_minute: int, horizon_end_minute: int) \
        -> Dict[str, float]:
    """Executed transition/fragmentation metrics for one solved window."""
    result.jobs = jobs
    runs = annotate_physical_runs(sc, result)
    metrics = executed_transition_metrics(
        sc, jobs,
        horizon_start_minute=int(horizon_start_minute),
        horizon_end_minute=int(horizon_end_minute),
        initial_machine_groups=getattr(
            result, "initial_machine_groups", {}) or {},
        machine_hold_buckets=getattr(
            result, "machine_hold_buckets", {}) or {})
    metrics.update(micro_parcel_metrics(sc, jobs))
    native = max(sc.sim.native_bucket_minutes(), 1)
    hourly = hourly_transition_metrics(
        sc, jobs,
        horizon_start_bucket=int(horizon_start_minute) // native,
        horizon_end_bucket=int(horizon_end_minute) // native,
        machine_hold_buckets=getattr(
            result, "machine_hold_buckets", {}) or {},
        initial_machine_groups=getattr(
            result, "initial_machine_groups", {}) or {})
    metrics.update(hourly)
    metrics.update(reconcile_transitions(hourly, metrics))
    metrics["transition_window_boundary_delta"] = window_boundary_delta(
        result.breakdown, hourly)
    metrics["physical_runs"] = float(len(runs))
    metrics["physical_runs_spanning_planning_days"] = float(sum(
        1 for run in runs if run.spans_days()))
    metrics["parent_jobs"] = float(len(jobs))
    return metrics


def _louta_policy_mass_error(sc: Scenario) -> str:
    """Reject a fixed-terminal S0 policy that must spill internal output."""
    if not bool(sc.config.get("LOU_S0_STOCKED_ROUTE_ENABLED", False)):
        return ""
    corridor = effective_corridor_targets(sc)
    stages = effective_process_stage_targets(sc)
    try:
        c1_t = float(corridor.get("C1", 0.0) or 0.0)
        wash_t = sum(float(stages.get(stage, 0.0) or 0.0)
                     for stage in ("LOU-WASH-L1", "LOU-WASH-L2"))
    except (TypeError, ValueError):
        return "Louta stocked-route targets must be numeric."
    if abs(c1_t - wash_t) > 1e-6:
        return (
            f"Louta stocked-route mass mismatch: C1 is {c1_t:g} t but "
            f"L1 + L2 washing output is {wash_t:g} t.")
    return ""


def _fixed_matches_group(sc: Scenario, fj: FixedJob, gk: Tuple) -> bool:
    """Whether an operator-fixed instruction selects this candidate group."""
    kind = gk[0]
    if washing.enabled(sc) and kind == "process":
        transfer = next((item for item in sc.process_transfers if item.id == gk[6]), None)
        if transfer is not None:
            kind = transfer.operation_kind
    if kind != fj.kind or gk[1] != fj.machine or gk[2] != fj.space:
        return False
    if fj.dest_id:
        if gk[3] != fj.dest_id:
            return False
    elif gk[0] != "stack" and gk[3]:
        return False
    if fj.grade:
        if washing.enabled(sc) and (not gk[4] or gk[4].startswith(("TREAT:", "MATURE:"))):
            return True  # Exact component eligibility is enforced below.
        group_grade = (gk[4] or (
            chevron_effective_grade(sc, gk[2])
            if gk[0] == "reclaim" else sc.space(gk[2]).grade))
        if group_grade != fj.grade:
            return False
    return True


def _fixed_for_bucket(sc: Scenario, gk: Tuple,
                      absolute_bucket: int) -> Optional[FixedJob]:
    """Return the deterministic fixed instruction active for a group/bucket."""
    return next((
        fj for fj in sc.fixed_jobs
        if _fixed_matches_group(sc, fj, gk)
        and fj.start_bucket <= absolute_bucket <= fj.end_bucket
    ), None)


def _build_base_candidates(sc: Scenario, graph: RouteGraph) -> Tuple[List[Cand], Dict[str, List[Cand]]]:
    """All candidate tuples + stacking candidates grouped by corridor exit node."""
    cands: List[Cand] = []
    for (m, s, d, p) in reclaim_candidates(sc, graph):
        cands.append(Cand(
            "reclaim", m, s, dest_id=d, path=p,
            purpose=f"Serve configured destination demand at {d}"))

    # Corridor dispatch: any reclaimer whose space has a path to a delayed
    # link's source, except through an internal process-transfer leg. Without
    # this boundary an upstream ore reclaimer could incorrectly "dispatch"
    # through the washing and product-stacking graph as if those transformations
    # were ordinary conveyors.
    process_only_links = {
        link_id
        for transfer in (getattr(sc, "process_transfers", []) or [])
        for link_id in transfer.path
    }
    delayed = [l for l in sc.links if l.transit_buckets > 0]
    corridor_targets = effective_corridor_targets(sc)
    for l in delayed:
        for m in sc.machines:
            if m.kind != "reclaimer" or not m.available or \
                    not getattr(m, "planning_enabled", True):
                continue
            for sid in m.reachable_spaces:
                try:
                    sp = sc.space(sid)
                except StopIteration:
                    continue
                p = graph.best_path(space_node(sc, sid), l.src)
                if p is not None and not process_only_links.intersection(p.links):
                    full = Path(links=p.links + [l.id],
                                transit_buckets=p.transit_buckets + l.transit_buckets,
                                capacity_tph=min(p.capacity_tph, l.capacity_tph))
                    target = float(corridor_targets.get(l.id, 0.0) or 0.0)
                    purpose = (
                        f"Meet configured {l.id} corridor allocation "
                        f"({target:g} t)"
                        if target > 0 else
                        f"Replenish downstream inventory through {l.id}"
                    )
                    cands.append(Cand(
                        "dispatch", m.id, sid, dest_id=l.id,
                        grade=sp.grade, path=full, exit_node=l.dst,
                        purpose=purpose))

    # stacking of arrivals at each corridor exit node, per grade
    stack_by_exit: Dict[str, List[Cand]] = {}
    grades = [g.id for g in sc.grades
              if not getattr(g, "is_specification", False)] or [""]
    raw_storage_compatibility = sc.config.get(
        "STORAGE_GRADE_COMPATIBILITY", {})
    storage_compatibility = (
        raw_storage_compatibility
        if isinstance(raw_storage_compatibility, dict) else {})

    def storage_grades_for(incoming_grade: str) -> set[str]:
        configured = storage_compatibility.get(incoming_grade, ())
        if isinstance(configured, str):
            configured = (configured,)
        if not isinstance(configured, (list, tuple, set)):
            configured = ()
        return {incoming_grade, *(str(value) for value in configured)}

    for l in delayed:
        sc_cands = stack_candidates(sc, graph, l.dst)
        lst = []
        for (m, sid, p) in sc_cands:
            sp = sc.space(sid)
            for g in grades:
                compatible_grades = storage_grades_for(g)
                # anti-contamination: stack g only if space designated for g,
                # an explicitly configured parent family, currently holds a
                # compatible family, or is empty and undesignated.  Chemical
                # variants remain distinct Grade rows; this mapping only says
                # which shared merchant-family pile may physically receive
                # them and is never inferred from an id naming convention.
                allowed = (
                    sp.designated_grade in compatible_grades or
                    (sp.designated_grade == "" and (
                        sp.mass_t <= 1 or sp.grade in compatible_grades)))
                campaign_status = str(
                    chevron_campaign_state(sc, sid).get("status", ""))
                if campaign_status == CAMPAIGN_COMPLETE:
                    # A completed blended pile is closed to further stacking;
                    # its remaining inventory keeps the constructed identity.
                    allowed = False
                elif chevron_enabled_for_space(sc, sid):
                    allowed = g in set(chevron_component_grade_ids(sc, sid))
                elif campaign_status == CAMPAIGN_BUILDING:
                    # Disabling a campaign pauses it; it must not silently
                    # revert to ordinary same-grade storage while half built.
                    allowed = False
                if allowed:
                    lst.append(Cand(
                        "stack", m, sid, grade=g, path=p, exit_node=l.dst,
                        purpose="Place required corridor arrivals in receiving stock"))
        stack_by_exit[l.dst] = lst
        cands.extend(lst)

    # Explicit internal material transfers.  These rows are the only way a
    # solver machine may move material between two storage/process spaces;
    # geometry proximity never creates a candidate.
    for transfer in getattr(sc, "process_transfers", []) or []:
        if not process_stage_enabled(sc, transfer.process_stage) or (
                transfer.depends_on_stage
                and not process_stage_enabled(sc, transfer.depends_on_stage)):
            continue
        # Cross-feed alternatives are dormant topology until their
        # pre-washing point is enabled for this stage/source. Keeping dormant
        # alternatives out of candidate generation preserves the original S0
        # search space and prevents an unrequested route from satisfying an
        # exact process-output target.
        if "BLEND-ALT" in str(transfer.id).upper() and \
                transfer.source_space not in \
                prewash_blending.source_spaces_for_stage(
                    sc, transfer.process_stage):
            continue
        try:
            machine = sc.machine(transfer.machine)
            source = sc.space(transfer.source_space)
            target = sc.space(transfer.target_space)
            links = [sc.link(link_id) for link_id in transfer.path]
        except StopIteration:
            continue
        if not machine.available or not getattr(
                machine, "planning_enabled", True) or not links:
            continue
        work_space = (target.id if transfer.operation_kind == "stack"
                      else source.id)
        if work_space not in machine.reachable_spaces or \
                any(not link.available for link in links):
            continue
        # Validation checks route continuity.  Candidate construction remains
        # defensive for imported scenarios that have not yet been validated.
        if links[0].src != space_node(sc, source.id) or \
                links[-1].dst != space_node(sc, target.id) or \
                any(left.dst != right.src
                    for left, right in zip(links, links[1:])):
            continue
        physical_process_cap = None
        if str(getattr(transfer, "mass_conversion", "") or "") == \
                "beneficiation" and float(getattr(
                    transfer, "dry_feed_capacity_tph", 0.0) or 0.0) > 0:
            output_grade = sc.grade(transfer.output_grade or target.grade)
            if output_grade is not None and \
                    output_grade.raw_moisture_pct is not None:
                dry_fraction = (
                    1.0 - float(output_grade.raw_moisture_pct) / 100.0)
                if dry_fraction > 0:
                    physical_process_cap = float(
                        transfer.dry_feed_capacity_tph) / dry_fraction
        path = Path(
            links=list(transfer.path),
            transit_buckets=sum(link.transit_buckets for link in links),
            capacity_tph=min(
                [link.capacity_tph for link in links]
                + ([physical_process_cap]
                   if physical_process_cap is not None else [])))
        cands.append(Cand(
            "process", machine.id, work_space,
            grade=source.grade, path=path,
            transfer_id=transfer.id,
            source_space=source.id, target_space=target.id,
            operation_kind=transfer.operation_kind,
            output_grade=transfer.output_grade or target.grade,
            mass_conversion=str(
                getattr(transfer, "mass_conversion", "") or ""),
            # The optimiser always balances physical/as-handled tonnes.  An
            # explicitly configured beneficiation transfer derives its wet
            # product output factor from the shared output-grade profile;
            # every other transfer retains its legacy direct yield.
            yield_factor=sc.transfer_output_factor(transfer),
            process_stage=transfer.process_stage,
            residence_min=int(transfer.residence_min),
            depends_on_stage=transfer.depends_on_stage,
            dependency_delay_min=int(transfer.dependency_delay_min),
            purpose=transfer.purpose))
    return cands, stack_by_exit


def _material_candidates(sc, graph):
    candidates, stacking = _build_base_candidates(sc, graph)
    if not washing.enabled(sc):
        return sc, candidates, stacking
    prepared, candidates = washing.prepare_materials(sc, candidates)
    stacking = {exit_node: [candidate for candidate in candidates
                            if candidate.kind == "stack" and candidate.exit_node == exit_node]
                for exit_node in stacking}
    return prepared, candidates, stacking


def build_candidates(sc: Scenario, graph: RouteGraph) -> Tuple[List[Cand], Dict[str, List[Cand]]]:
    _, candidates, stacking = _material_candidates(sc, graph)
    return candidates, stacking


def _candidate_serves_destination(
        sc: Scenario, cand: Cand, destination,
        bucket: Optional[int] = None,
        requested_grade: Optional[str] = None) -> bool:
    """Whether a candidate fulfils one destination campaign.

    ``Destination.requested_grade`` is the legacy/default campaign.  Weekly
    plans carry the grade on each demand bucket, so candidate eligibility must
    be evaluated at that bucket instead of once for the whole horizon.
    Process-feed demand matches the transfer input grade; final delivery
    matches the reclaimed stock material grade.  A blank requested grade keeps
    the historical unrestricted behaviour.
    """
    if requested_grade is None:
        requested = (
            sc.demand_grade(destination.id, bucket)
            if bucket is not None
            else getattr(destination, "requested_grade", "") or "")
    else:
        requested = requested_grade
    requested = str(requested or "").strip()
    if getattr(destination, "demand_kind", "delivery") == "process_feed":
        if cand.kind != "process" or \
                cand.process_stage != getattr(destination, "process_stage", ""):
            return False
        # An enabled pre-washing blend campaign is specified by physical
        # source-stockpile shares.  The destination's legacy requested grade
        # remains useful as the no-blend baseline, but must not filter out the
        # other configured component once this policy is active.
        blend_sources = prewash_blending.source_spaces_for_stage(
            sc, cand.process_stage)
        if cand.source_space in blend_sources:
            return True
        if requested:
            try:
                return (cand.grade if cand.material_id else sc.space(cand.source_space).grade) == requested
            except StopIteration:
                return False
        return True
    if cand.kind != "reclaim" or cand.dest_id != destination.id:
        return False
    if not requested:
        return True
    if chevron_is_demand_driven(sc) and (
            chevron_enabled_for_space(sc, cand.space) or
            chevron_campaign_is_complete(sc, cand.space)):
        # Output identity follows receiving qualification of the actual mix.
        # A pile's opening label cannot restrict which grade a new blend makes.
        return True
    requested_definition = sc.grade(requested)
    if requested_definition is not None and getattr(requested_definition, "is_specification", False):
        return True  # actual receiving chemistry, not a source-label equality
    try:
        actual_grade = cand.grade if cand.material_id else chevron_effective_grade(sc, cand.space)
        return actual_grade == requested
    except StopIteration:
        return False


def _candidate_quality_space(cand: Cand) -> str:
    return cand.source_space if cand.kind == "process" else cand.space


def _candidate_effective_quality(sc: Scenario, cand: Cand) -> Dict[str, float]:
    """Quality used consistently by CP-SAT, warm starts and reporting."""
    space_id = _candidate_quality_space(cand)
    if cand.quality_override is not None and not chevron_enabled_for_space(sc, space_id):
        return dict(cand.quality_override)
    if cand.kind == "reclaim":
        return chevron_blended_quality(sc, space_id)
    return dict(sc.quality_of(space_id))


def _candidate_calendar_scopes(sc: Scenario, cand: Cand) -> set[str]:
    """Calendar resources touched by one allocatable route candidate."""
    scopes = {cand.machine}
    try:
        scopes.add(f"SITE:{sc.machine(cand.machine).site}")
    except StopIteration:
        pass
    for space_id in {
            cand.space, cand.source_space, cand.target_space} - {""}:
        try:
            scopes.add(f"SITE:{sc.space(space_id).site}")
        except StopIteration:
            pass
    if cand.path:
        scopes.update(f"LINK:{link_id}" for link_id in cand.path.links)
    if cand.process_stage:
        scopes.add(f"LINE:{cand.process_stage}")
        scopes.update(
            f"LINE:{line_id}"
            for line_id, stages in PROCESS_LINE_STAGE_MAP.items()
            if cand.process_stage in stages)
    if cand.dest_id:
        scopes.add(f"LINE:{cand.dest_id}")
        try:
            destination = sc.destination(cand.dest_id)
        except StopIteration:
            pass
        else:
            if destination.process_stage:
                scopes.add(f"LINE:{destination.process_stage}")
    return scopes


def _candidate_calendar_blocked(
        sc: Scenario, cand: Cand, absolute_bucket: int) -> bool:
    """Whether any machine/site/link/line resource is unavailable."""
    return sc.any_calendar_scope_blocked(
        _candidate_calendar_scopes(sc, cand), bucket=absolute_bucket)


def _line_capacity_keys(cand: Cand) -> set[Tuple[str, str]]:
    """Return independently capped line groups touched by a candidate.

    A physical washing line owns several sequential process stages.  Its
    physical rate is therefore applied to each mapped stage independently,
    rather than incorrectly adding the same material again at every stage.
    Final receiving plants are grouped by destination id.
    """
    keys: set[Tuple[str, str]] = set()
    if cand.process_stage:
        keys.add((f"LINE:{cand.process_stage}", cand.process_stage))
        keys.update(
            (f"LINE:{line_id}", cand.process_stage)
            for line_id, stages in PROCESS_LINE_STAGE_MAP.items()
            if cand.process_stage in stages)
    if cand.dest_id:
        keys.add((f"LINE:{cand.dest_id}", cand.dest_id))
    return keys


def _line_effective_capacity_tph(
        sc: Scenario, scope: str, absolute_bucket: int) -> Optional[float]:
    """Whole-run physical line cap tightened by a timed RATE_CAP window."""
    line_id = scope[5:] if scope.startswith("LINE:") else scope
    base = sc.process_line_capacity_tph(line_id)
    timed = sc.resource_rate_cap_tph(scope, bucket=absolute_bucket)
    if base is None:
        return timed
    return min(base, timed) if timed is not None else base


def _candidate_effective_capacity_tph(
        sc: Scenario, cand: Cand, absolute_bucket: int,
        machine_rate_factor: float = 1.0) -> float:
    """Per-candidate physical ceiling including every timed rate change."""
    machine = sc.machine(cand.machine)
    cap = sc.effective_resource_rate_tph(
        cand.machine, machine.rate_max_tph * machine_rate_factor,
        bucket=absolute_bucket)
    if cand.path:
        for link_id in cand.path.links:
            link = sc.link(link_id)
            cap = min(cap, sc.effective_resource_rate_tph(
                f"LINK:{link_id}", link.capacity_tph,
                bucket=absolute_bucket))
    for scope, _group in _line_capacity_keys(cand):
        line_cap = _line_effective_capacity_tph(
            sc, scope, absolute_bucket)
        if line_cap is not None:
            cap = min(cap, line_cap)
    return max(float(cap), 0.0)


def _split_integer_target(total: float, weights: List[float]) -> List[int]:
    """Split one exact whole-tonne target without losing horizon mass."""
    target = max(int(round(float(total))), 0)
    if not weights:
        return []
    clean = [max(float(weight), 0.0) for weight in weights]
    denominator = sum(clean)
    if denominator <= 0:
        clean = [1.0] * len(clean)
        denominator = float(len(clean))
    raw = [target * weight / denominator for weight in clean]
    allocated = [int(value) for value in raw]
    remainder = target - sum(allocated)
    order = sorted(
        range(len(raw)), key=lambda index: (-(raw[index] - allocated[index]),
                                             index))
    for index in order[:remainder]:
        allocated[index] += 1
    return allocated


def _weekly_window_target_profiles(
        sc: Scenario, windows: List[Tuple[int, int]]) -> Tuple[
            Dict[str, List[int]], Dict[str, List[int]]]:
    """Allocate horizon targets to daily windows, exactly and transparently.

    Process-chain targets follow the matching line's process-feed campaign.
    A corridor without a direct campaign driver is spread by physical window
    duration.  C1 is special only in that its source is the sum of the two
    enabled Louta washed-output stages, so its window target is that same sum.
    """
    dt = float(sc.sim.dt_h)
    feed_mass_by_destination: Dict[str, List[int]] = {}
    recovered_mass_by_destination: Dict[str, List[int]] = {}
    for destination in sc.destinations:
        if getattr(destination, "demand_kind", "delivery") != "process_feed":
            continue
        feed_windows: List[int] = []
        recovered_windows: List[int] = []
        transfers = [
            transfer for transfer in sc.process_transfers
            if transfer.process_stage == destination.process_stage
        ]
        for start, end in windows:
            feed_t = 0
            # The yield model carries fractional output within one civil-day
            # campaign and floors only that aggregate.  Reproduce that exact
            # integer contract here instead of apportioning washed output by
            # raw feed.  This matters when daily requested grades have
            # different beneficiation factors.
            campaign_input: Dict[Tuple[str, str], int] = {}
            for bucket in range(start, end):
                mass_t = int(round(
                    sc.demand_tph(destination.id, bucket) * dt))
                if mass_t <= 0:
                    continue
                feed_t += mass_t
                grade = sc.demand_grade(destination.id, bucket)
                transfer = next((
                    item for item in transfers
                    if not grade or sc.space(item.source_space).grade == grade
                ), None)
                if transfer is not None:
                    key = (grade, transfer.id)
                    campaign_input[key] = (
                        campaign_input.get(key, 0) + mass_t)
            recovered_t = 0
            by_id = {transfer.id: transfer for transfer in transfers}
            for (_grade, transfer_id), input_t in campaign_input.items():
                factor = max(min(sc.transfer_output_factor(
                    by_id[transfer_id]), 1.0), 0.0)
                coefficient = int(round(factor * 10_000))
                recovered_t += (input_t * coefficient) // 10_000
            feed_windows.append(feed_t)
            recovered_windows.append(recovered_t)
        feed_mass_by_destination[destination.id] = feed_windows
        recovered_mass_by_destination[destination.id] = recovered_windows

    stage_driver: Dict[str, Tuple[str, bool]] = {}
    for destination in sc.destinations:
        if getattr(destination, "demand_kind", "delivery") != "process_feed":
            continue
        for _line_id, stages in PROCESS_LINE_STAGE_MAP.items():
            if destination.process_stage in stages:
                yield_stage_index = stages.index(destination.process_stage)
                for stage_index, stage in enumerate(stages):
                    # Upstream handling stages move raw physical feed.  The
                    # beneficiation stage itself is constrained on Y (its
                    # recovered physical output), as are downstream stages.
                    stage_driver[stage] = (
                        destination.id, stage_index >= yield_stage_index)

    configured_stages = dict(
        sc.config.get("PROCESS_STAGE_OUTPUT_TARGET_T", {}) or {})
    stage_profiles: Dict[str, List[int]] = {}
    duration_weights = [float(end - start) for start, end in windows]
    for stage, target in configured_stages.items():
        driver, uses_recovered_output = stage_driver.get(
            str(stage), ("", False))
        weights = (
            recovered_mass_by_destination.get(driver, duration_weights)
            if uses_recovered_output else
            feed_mass_by_destination.get(driver, duration_weights)
        )
        stage_profiles[str(stage)] = _split_integer_target(target, weights)

    configured_corridors = dict(
        sc.config.get("CORRIDOR_DISPATCH_TARGET_T", {}) or {})
    corridor_profiles = {
        str(link_id): _split_integer_target(target, duration_weights)
        for link_id, target in configured_corridors.items()
    }
    if "C1" in corridor_profiles:
        louta_stages = ("LOU-WASH-L1", "LOU-WASH-L2")
        c1 = [sum(stage_profiles.get(stage, [0] * len(windows))[index]
                  for stage in louta_stages)
              for index in range(len(windows))]
        # Preserve the configured horizon equality check.  A mismatch is
        # rejected by _louta_policy_mass_error before rolling starts.
        if sum(c1) == int(round(float(configured_corridors.get("C1", 0)))):
            corridor_profiles["C1"] = c1
    return stage_profiles, corridor_profiles


def _carry_segment_and_machine_state(
        sc: Scenario, result: PlanResult) -> None:
    """Carry more than aggregate inventory between rolling windows.

    The optimiser's authoritative inventory is aggregate physical tonnes, but
    the digital-twin snapshot also owns grade/position segments and current
    equipment positions.  Preserve segment boundaries and grade layers while
    reconciling their masses to the terminal inventory, and move each machine
    to the physical work point of its last scheduled window.
    """
    segments_by_space: Dict[str, List[Segment]] = {}
    for segment in sc.segments:
        segments_by_space.setdefault(segment.space_id, []).append(segment)
    rebuilt: List[Segment] = []
    for space in sc.spaces:
        terminal = float(result.inventory.get(
            space.id, [space.mass_t])[-1])
        space.mass_t = terminal
        space.state = (
            "empty" if terminal <= 0 else
            "full" if terminal >= space.capacity_t - 1e-6 else "mixed")
        layers = segments_by_space.get(space.id, [])
        prior_total = sum(max(float(layer.mass_t), 0.0) for layer in layers)
        if layers and prior_total > 0:
            scale = terminal / prior_total
            for layer in layers:
                clone = copy.deepcopy(layer)
                clone.mass_t = max(float(layer.mass_t), 0.0) * scale
                rebuilt.append(clone)
        elif terminal > 0:
            rebuilt.append(Segment(
                space.id, space.x_from, space.x_to, terminal,
                space.grade, quality=dict(sc.quality_of(space.id))))
    sc.segments = rebuilt
    if result.material_inventory:
        sc.config["_WASHING_MODEL_CATALOG"] = copy.deepcopy(result.material_catalog)
        sc.config["_WASHING_MODEL_INITIAL"] = {
            sid: {mid: series[-1] for mid, series in materials.items()}
            for sid, materials in result.material_inventory.items()}
        sc.config["_WASHING_MODEL_PENDING"] = copy.deepcopy(result.material_pending)

    last_job_by_machine: Dict[str, Job] = {}
    for job in result.jobs:
        previous = last_job_by_machine.get(job.machine)
        job_end = (job.end_minute if job.end_minute is not None
                   else (job.end_bucket + 1) * sc.sim.native_bucket_minutes())
        previous_end = (
            -1 if previous is None else
            previous.end_minute if previous.end_minute is not None else
            (previous.end_bucket + 1) * sc.sim.native_bucket_minutes())
        if previous is None or job_end >= previous_end:
            last_job_by_machine[job.machine] = job
    initial_spaces = dict(sc.config.get("MACHINE_INITIAL_SPACE", {}) or {})
    for machine_id, job in last_job_by_machine.items():
        try:
            sc.machine(machine_id).position_x = work_midpoint_x(
                sc, job.space, job.start_x, job.stop_x)
            initial_spaces[machine_id] = job.space
        except StopIteration:
            pass
    sc.config["MACHINE_INITIAL_SPACE"] = initial_spaces


def _carry_future_belt_profile(
        sc: Scenario, result: PlanResult, window_end: int) -> List[BeltProfileEntry]:
    """Return all physical parcels still in transit after a solve window."""
    pending = [
        copy.deepcopy(entry) for entry in sc.belt_profile
        if int(entry.arrive_bucket) >= int(window_end)
    ]
    for job in result.jobs:
        if job.kind != "dispatch" or not job.dest_id:
            continue
        try:
            lag = int(sc.link(job.dest_id).transit_buckets)
        except StopIteration:
            continue
        for flow in job.bucket_flows:
            arrival = int(flow.bucket) + lag
            if arrival >= window_end and flow.tonnage_t > 0:
                if job.material_flows:
                    for part in job.material_flows:
                        if part["bucket"] == flow.bucket and part["tonnage_t"] > 0:
                            pending.append(BeltProfileEntry(
                                job.dest_id, arrival, float(part["tonnage_t"]), part["grade"],
                                quality=copy.deepcopy(part["quality"]), material_id=part["material_id"]))
                    continue
                pending.append(BeltProfileEntry(
                    job.dest_id, arrival, float(flow.tonnage_t), job.grade))
    return pending


def _merge_weekly_windows(
        sc: Scenario, windows: List[Tuple[int, int]],
        results: List[PlanResult], *, hard_wall_s: float,
        elapsed_s: float, failed_index: Optional[int] = None,
        horizon_offset: int = 0) -> PlanResult:
    """Merge rolling results onto the original native weekly grid."""
    graph = RouteGraph.from_scenario(sc)
    candidates, _ = build_candidates(sc, graph)
    status = "FEASIBLE"
    if failed_index is not None:
        status = results[-1].status if results else "UNKNOWN"
    elif results and all(result.status == "OPTIMAL" for result in results):
        status = "OPTIMAL"
    merged = PlanResult(
        status=status,
        solver=("cp-sat-weekly-rolling-partial" if failed_index is not None
                else "cp-sat-weekly-rolling"),
        solver_wall_s=round(float(elapsed_s), 3),
        solver_optimality_scope="conditional_rolling_windows",
        horizon_offset=int(horizon_offset),
    )
    successful_count = (
        int(failed_index) if failed_index is not None else len(results))
    successful_results = results[:successful_count]
    total_width = sum(end - start for start, end in windows)
    planned_width = sum(
        end - start for start, end in windows[:successful_count])
    merged.jobs = sorted(
        [job for result in successful_results for job in result.jobs],
        key=lambda job: (job.start_bucket, job.machine, job.id))
    merged.objective_pass_records = [
        dict(record, planning_window=index + 1)
        for index, result in enumerate(successful_results)
        for record in getattr(result, "objective_pass_records", [])]
    for result in successful_results:
        merged.material_catalog.update(copy.deepcopy(result.material_catalog))
    if successful_results and any(result.material_inventory for result in successful_results):
        for sid in sorted({sid for result in successful_results for sid in result.material_inventory}):
            merged.material_inventory[sid] = {}
            mids = sorted({mid for result in successful_results
                           for mid in result.material_inventory.get(sid, {})})
            for mid in mids:
                values = []
                for index, result in enumerate(successful_results):
                    width = windows[index][1] - windows[index][0]
                    series = result.material_inventory.get(sid, {}).get(mid, [0.] * (width + 1))
                    values.extend(series if index == 0 else series[1:])
                merged.material_inventory[sid][mid] = values
    if successful_results:
        first_result = successful_results[0]
        last_result = successful_results[-1]
        merged.material_pending = copy.deepcopy(last_result.material_pending)
        merged.initial_machine_groups = dict(
            getattr(first_result, "initial_machine_groups", {}))
        merged.initial_feed_gap = dict(
            getattr(first_result, "initial_feed_gap", {}))
        merged.initial_open_feed_gap_minutes = dict(getattr(
            first_result, "initial_open_feed_gap_minutes", {}))
        merged.terminal_machine_groups = dict(
            getattr(last_result, "terminal_machine_groups", {}))
        merged.terminal_feed_gap = dict(
            getattr(last_result, "terminal_feed_gap", {}))
        merged.terminal_open_feed_gap_minutes = dict(getattr(
            last_result, "terminal_open_feed_gap_minutes", {}))

    series_fields = (
        "unmet", "offspec", "safety_short", "delivered", "process_reject",
        "feed_gap", "feed_interruption_start")
    for field_name in series_fields:
        target = getattr(merged, field_name)
        keys = sorted({
            key for result in successful_results
            for key in getattr(result, field_name)})
        if field_name in ("unmet", "delivered", "offspec"):
            keys = sorted(set(keys) | {
                destination.id for destination in sc.destinations})
        elif field_name == "safety_short":
            keys = sorted(set(keys) | {
                space.id for space in sc.spaces
                if space.safety_stock_t > 0})
        for key in keys:
            values: List[float] = []
            for index, (start, end) in enumerate(windows):
                width = end - start
                if index < successful_count:
                    source = getattr(
                        successful_results[index], field_name).get(key, [])
                    values.extend(float(value) for value in source[:width])
                    if len(source) < width:
                        values.extend([0.0] * (width - len(source)))
                elif field_name == "unmet" and any(
                        destination.id == key
                        for destination in sc.destinations):
                    values.extend(float(int(round(
                        sc.demand_tph(key, bucket) * sc.sim.dt_h)))
                                  for bucket in range(start, end))
                else:
                    values.extend([0.0] * width)
            target[key] = values[:total_width]
    merged.surge_unstacked = []
    for index, (start, end) in enumerate(windows):
        width = end - start
        source = (successful_results[index].surge_unstacked
                  if index < successful_count else [])
        merged.surge_unstacked.extend(
            float(value) for value in source[:width])
        if len(source) < width:
            merged.surge_unstacked.extend([0.0] * (width - len(source)))

    for space in sc.spaces:
        values: List[float] = [float(space.mass_t)]
        if successful_results and washing.enabled(sc):
            # Use the same canonical whole-tonne opening as the typed model.
            # Replay retains measured fractional stock independently.
            opening = successful_results[0].inventory.get(space.id, [])
            if opening:
                values[0] = float(opening[0])
        for index, (start, end) in enumerate(windows):
            width = end - start
            source = (successful_results[index].inventory.get(space.id, [])
                      if index < successful_count else [])
            continuation = [float(value) for value in source[1:width + 1]]
            values.extend(continuation)
            if len(continuation) < width:
                values.extend([values[-1]] * (width - len(continuation)))
        merged.inventory[space.id] = values[:total_width + 1]

    merged.objective = sum(
        float(result.objective) for result in successful_results)
    merged.solver_best_bound = sum(
        float(getattr(result, "solver_best_bound", 0.0))
        for result in successful_results)
    merged.solver_relative_gap_pct = (
        100.0 * abs(merged.objective - merged.solver_best_bound)
        / max(abs(merged.objective), 1.0)
        if successful_results else 0.0)
    merged.solver_branches = sum(
        int(getattr(result, "solver_branches", 0))
        for result in successful_results)
    merged.solver_conflicts = sum(
        int(getattr(result, "solver_conflicts", 0))
        for result in successful_results)
    merged.solver_deterministic_time = sum(
        float(getattr(result, "solver_deterministic_time", 0.0))
        for result in successful_results)
    merged.breakdown = {
        "planning_weekly_rolling": 1.0,
        "planning_weekly_window_count": float(len(windows)),
        "planning_weekly_completed_windows": float(
            len(results) - (1 if failed_index is not None and results else 0)),
        "planning_weekly_total_hard_wall_s": float(hard_wall_s),
        "planning_weekly_elapsed_s": float(elapsed_s),
        "planning_weekly_planned_buckets": float(planned_width),
        "planning_weekly_unplanned_buckets": float(
            total_width - planned_width),
        "solver_best_objective_bound": merged.solver_best_bound,
        "solver_relative_gap_pct": merged.solver_relative_gap_pct,
        "solver_branches": float(merged.solver_branches),
        "solver_conflicts": float(merged.solver_conflicts),
        "solver_deterministic_time": merged.solver_deterministic_time,
    }
    for index, result in enumerate(results, start=1):
        merged.breakdown[f"planning_weekly_window_{index:02d}_wall_s"] = \
            float(result.solver_wall_s)
        merged.breakdown[f"planning_weekly_window_{index:02d}_status_code"] = \
            float({"OPTIMAL": 2, "FEASIBLE": 1}.get(result.status, 0))
    merged.breakdown["unmet_t"] = sum(
        sum(values) for values in merged.unmet.values())
    merged.breakdown["priority_weighted_unmet_t"] = sum(
        sc.demand_priority(destination_id, bucket + horizon_offset) * value
        for destination_id, values in merged.unmet.items()
        for bucket, value in enumerate(values))
    merged.breakdown["offspec_t_unit"] = sum(
        sum(values) for values in merged.offspec.values())
    merged.breakdown["safety_short_t"] = sum(
        sum(values) for values in merged.safety_short.values())
    for metric in (
            "scheduled_starts", "scheduled_stops",
            "scheduled_group_changeovers", "machine_start_count",
            "machine_stop_count", "machine_changeover_count",
            "machine_state_transition_count", "active_machine_h",
            "handled_t", "work_link_energy_kwh",
            "continuity_gap_minutes", "continuity_episode_count",
            "continuous_feed_gap_minutes",
            "continuous_feed_interruption_count"):
        merged.breakdown[metric] = sum(
            float(result.breakdown.get(metric, 0.0))
            for result in successful_results)
    merged.breakdown["moves"] = merged.breakdown[
        "machine_state_transition_count"]
    for metric in (
            "continuity_max_episode_minutes",
            "continuous_feed_max_episode_minutes"):
        merged.breakdown[metric] = max(
            (float(result.breakdown.get(metric, 0.0))
             for result in successful_results), default=0.0)
    merged.breakdown["energy_objective_enabled"] = float(bool(
        sc.config.get("ENERGY_OBJECTIVE_ENABLED", False)))
    merged.breakdown["surge_t"] = sum(merged.surge_unstacked)
    merged.breakdown["process_reject_t"] = sum(
        sum(values) for values in merged.process_reject.values())
    for metric in (
            "spill_t", "minute_timing_validated_windows",
            "minute_timing_errors", "minute_timing_fallback_buckets",
            "minute_timing_stable_run_windows",
            "minute_timing_stable_run_minutes",
            "short_parcel_buckets", "campaign_dwell_constraints",
            "process_stage_target_shortfall_t",
            "parcel_consolidation_attempted_windows",
            "parcel_consolidation_accepted_windows",
            "parcel_consolidation_before_buckets",
            "parcel_consolidation_after_buckets",
            "parcel_consolidation_wall_s",
            "parcel_consolidation_primary_before",
            "parcel_consolidation_primary_after",
            "positioning_reservation_constraints",
            "blend_construction_offspec_t_unit",
            "blend_construction_giveaway_t_unit"):
        merged.breakdown[metric] = sum(
            float(result.breakdown.get(metric, 0.0))
            for result in successful_results)
    for metric in ("washing_raw_feed_dry_t", "washing_treatment_cost",
                   "washing_simple_feed_t", "washing_simple_product_t", "washing_simple_feed_dry_t", "washing_simple_product_dry_t", "washing_simple_loss_dry_t",
                   "washing_full_feed_t", "washing_full_product_t", "washing_full_feed_dry_t", "washing_full_product_dry_t", "washing_full_loss_dry_t",
                   "priority_weighted_qualified_unmet_t", "unqualified_dispatch_t", "weighted_objective_comparison",
                   "objective_secondary_attempted_passes", "objective_secondary_accepted_passes",
                   "planning_stage_shortfall_t", "planning_corridor_shortfall_t",
                   "minimum_job_volume_constraints"):
        merged.breakdown[metric] = sum(float(result.breakdown.get(metric, 0))
                                       for result in successful_results)
    # Per-window (civil-day) diagnostics.
    #
    # The weekly status reads OPTIMAL only when EVERY window is optimal, but a
    # sequence of daily optima is NOT a weekly optimum: the decomposition
    # cannot move work between days, so a locally perfect Monday can strand
    # demand on Tuesday. That is how a weekly "OPTIMAL" plan can deliver less
    # than a weekly "FEASIBLE" one. These rows expose the per-day split so the
    # single weekly badge is not read as a global optimality claim.
    for index, result in enumerate(results):
        window_breakdown = result.breakdown or {}
        merged.breakdown[f"window_optimal[{index}]"] = float(
            result.status == "OPTIMAL")
        for metric in ("priority_weighted_qualified_unmet_t", "unmet_t",
                       "spill_t", "surge_t"):
            merged.breakdown[f"window_{metric}[{index}]"] = float(
                window_breakdown.get(metric, 0.0) or 0.0)
    merged.breakdown["windows_total"] = float(len(results))
    merged.breakdown["windows_optimal"] = float(
        sum(1 for result in results if result.status == "OPTIMAL"))
    merged.breakdown["objective_service_first"] = float(sc.config.get("OBJECTIVE_MODE") == "service_first")
    merged.breakdown["washing_treatments_enabled"] = float(washing.enabled(sc))
    merged.breakdown["minimum_job_volume_t"] = float(sc.config.get("MIN_JOB_VOLUME_T", 0))
    merged.breakdown["campaign_policy_enabled"] = float(
        bool(campaign_policy_enabled(sc)))
    # Held buckets are absolute, so the union across windows is the week's
    # held set. The minute layer needs it before the executed accounting runs.
    for result in successful_results:
        for machine_id, held in getattr(
                result, "machine_hold_buckets", {}).items():
            merged.machine_hold_buckets.setdefault(
                str(machine_id), []).extend(int(b) for b in held or ())
    for machine_id in merged.machine_hold_buckets:
        merged.machine_hold_buckets[machine_id] = sorted(
            set(merged.machine_hold_buckets[machine_id]))
    for metric in ("held_machine_h", "scheduled_hold_buckets"):
        merged.breakdown[metric] = sum(
            float(result.breakdown.get(metric, 0.0))
            for result in successful_results)
    # Executed transition accounting and physical-run identity must be derived
    # from the MERGED minute schedule, never summed per rolling window: a
    # campaign that runs through midnight is one physical run with one start,
    # and summing the windows would invent a stop and a start at every civil
    # day boundary.
    native_minutes = max(sc.sim.native_bucket_minutes(), 1)
    merged.breakdown.update(_executed_schedule_breakdown(
        sc, merged, merged.jobs,
        horizon_start_minute=int(horizon_offset) * native_minutes,
        horizon_end_minute=(
            int(horizon_offset) + max(planned_width, 0)) * native_minutes))
    # The terminal M16 supply-position policy is enforced only in the final
    # rolling window.  Preserve its whole-horizon KPI in the merged result so
    # Monitor and exported results expose the same ceiling, realised position,
    # and in-transit component as a uniform solve.
    terminal_metrics = (
        "terminal_supply_position_max_t",
        "terminal_supply_position_t",
        "terminal_corridor_in_transit_t",
    )
    if successful_count == len(windows) and successful_results:
        final_breakdown = successful_results[-1].breakdown
        for metric in terminal_metrics:
            if metric in final_breakdown:
                merged.breakdown[metric] = float(final_breakdown[metric])
    detail_metrics = sorted({
        key for result in successful_results for key in result.breakdown
        if key.startswith("spill_t[") or key.startswith("surge_t[")
        or key.startswith("process_stage_target_shortfall_t[")
        or key.startswith("planning_stage_shortfall_t[")
        or key.startswith("planning_corridor_shortfall_t[")})
    for metric in detail_metrics:
        merged.breakdown[metric] = sum(
            float(result.breakdown.get(metric, 0.0))
            for result in successful_results)
    candidate_machines = {candidate.machine for candidate in candidates}
    merged.breakdown["planning_eligible_machines"] = float(sum(
        1 for machine in sc.machines
        if machine.id in candidate_machines and machine.available
        and getattr(machine, "planning_enabled", True)))
    used = {job.machine for job in merged.jobs}
    merged.breakdown["planning_used_machines"] = float(len(used))
    eligible = merged.breakdown["planning_eligible_machines"]
    merged.breakdown["planning_machine_coverage_pct"] = (
        100.0 * len(used) / eligible if eligible else 100.0)

    for result in results:
        for reason in result.reasons:
            if reason not in merged.reasons:
                merged.reasons.append(reason)
        for relaxation in result.relaxations:
            if relaxation not in merged.relaxations:
                merged.relaxations.append(relaxation)
    window_summary = "; ".join(
        f"W{index + 1}={result.status}/{result.solver_wall_s:.1f}s"
        for index, result in enumerate(results))
    if window_summary:
        merged.reasons.append("WEEKLY_WINDOW_STATUS " + window_summary)
    if failed_index is not None:
        start, end = windows[failed_index]
        if results[failed_index].status == "PARTIAL":
            merged.breakdown[
                "planning_weekly_rejected_stage_shortfall_t"] = float(
                    results[failed_index].breakdown.get(
                        "process_stage_target_shortfall_t", 0.0))
            merged.breakdown["planning_weekly_rejected_timing_errors"] = float(
                results[failed_index].breakdown.get("minute_timing_errors", 0))
        failure_detail = (
            ("minute execution unresolved; candidate rejected"
             if results[failed_index].breakdown.get("minute_timing_errors", 0) > 0 else
             "stage-target search slack remained; candidate rejected")
            if results[failed_index].status == "PARTIAL"
            else "no feasible incumbent")
        merged.reasons.insert(
            0, "WEEKLY_WINDOW_FAILED "
            f"buckets {start}-{end - 1}: {failure_detail}; "
            "returned an explicitly partial weekly result")

    _derive_machine_dispositions(sc, merged, candidates)
    return merged


_BLEND_DESIGN_PREPARED_KEY = "_OPTIMIZED_BLEND_DESIGN_PREPARED"


def _optimized_blend_routing_envelope(
        sc: Scenario) -> Tuple[Dict[str, List[str]], Dict[str, int]]:
    """Return routeable input grades and smallest executable stack runs.

    The composition pre-model must not propose chemistry that the operational
    network cannot deliver.  Candidate generation already applies equipment,
    storage-family and route configuration; matching it to dispatches and
    in-transit belt parcels closes the gap between the blend form and backend.
    Timed calendars and competing flows remain enforced by the scheduling
    model after a composition is selected.
    """
    graph = RouteGraph.from_scenario(sc)
    candidates, _stack_by_exit = build_candidates(sc, graph)
    incoming_pairs = {
        (str(candidate.exit_node), str(candidate.grade))
        for candidate in candidates
        if candidate.kind == "dispatch" and candidate.exit_node
    }
    for parcel in sc.belt_profile:
        try:
            incoming_pairs.add((
                str(sc.link(parcel.link_id).dst), str(parcel.grade)))
        except StopIteration:
            continue

    routeable: Dict[str, List[str]] = {}
    minimum_runs: Dict[str, int] = {}
    dt = max(sc.sim.native_bucket_minutes(), 1) / 60.0
    settings = chevron_settings(sc)
    for space_id in settings["active_spaces"]:
        if not chevron_is_demand_driven(sc) and chevron_mode(sc, space_id) != BLEND_MODE_OPTIMIZED and not (
                chevron_mode(sc, space_id) == BLEND_MODE_MATRIX
                and len(chevron_matrix_catalog(sc)) > 1
                and not chevron_campaign_state(sc, space_id).get(
                    "resolved_recipe")):
            continue
        matching = [
            candidate for candidate in candidates
            if candidate.display_kind() == "stack"
            and candidate.output_space() == space_id
            and (str(candidate.exit_node), str(candidate.grade))
            in incoming_pairs
        ]
        routeable[space_id] = sorted({
            str(candidate.grade) for candidate in matching
        })
        if matching:
            minimum_runs[space_id] = min(
                max(int(round(
                    sc.machine(candidate.machine).rate_min_tph * dt)), 1)
                for candidate in matching)
    return routeable, minimum_runs


def _prepare_blend_design_scenario(
        sc: Scenario) -> Tuple[Scenario, List[str]]:
    """Validate optimized policies on a private scheduling copy.

    Composition is intentionally *not* resolved here. The uniform CP-SAT
    model owns both the per-grade blend tonnes and the physical stack flows,
    so inventory, calendars, routes and equipment capacity participate in the
    same feasibility decision. The marker prevents redundant copy/validation
    inside weekly and mixed-resolution wrapper calls.
    """
    if bool(sc.config.get(_BLEND_DESIGN_PREPARED_KEY, False)):
        return sc, []
    try:
        errors = list(chevron_config_errors(sc))
    except (TypeError, ValueError, OverflowError) as exc:
        return sc, [f"Invalid blend configuration: {exc}"]
    if errors:
        return sc, errors
    settings = chevron_settings(sc)
    needs_resolution = chevron_is_demand_driven(sc) or any(
        chevron_mode(sc, space_id) == BLEND_MODE_OPTIMIZED or (
            chevron_mode(sc, space_id) == BLEND_MODE_MATRIX
            and len(chevron_matrix_catalog(sc)) > 1
            and not chevron_campaign_state(sc, space_id).get(
                "resolved_recipe"))
        for space_id in settings["enabled_spaces"])
    if not needs_resolution:
        return sc, []
    prepared = copy.deepcopy(sc)
    raw_blend = prepared.config.get(CHEVRON_CONFIG_KEY, {})
    raw_blend = dict(raw_blend) if isinstance(raw_blend, dict) else {}
    proposed = dict(raw_blend.get("proposed_recipes_by_space", {}) or {})
    proposed_matrices = dict(raw_blend.get(
        "proposed_matrix_by_space", {}) or {})
    for space_id in chevron_settings(prepared)["enabled_spaces"]:
        if not chevron_campaign_state(prepared, space_id).get(
                "resolved_recipe") and (chevron_is_demand_driven(sc) or
                chevron_mode(prepared, space_id) == BLEND_MODE_OPTIMIZED
                or (chevron_mode(prepared, space_id) == BLEND_MODE_MATRIX
                    and len(chevron_matrix_catalog(prepared)) > 1)):
            # A prior plan proposal is not a physical commitment. Re-open all
            # bounded component grades so a new plan can respond to the latest
            # inventories, outages and rates.
            proposed.pop(space_id, None)
            proposed_matrices.pop(space_id, None)
    if chevron_is_demand_driven(sc):
        campaigns = dict(raw_blend.get("proposed_campaigns_by_space", {}) or {})
        for space_id in settings["enabled_spaces"]:
            if not chevron_campaign_state(prepared, space_id).get("resolved_recipe"):
                campaigns.pop(space_id, None)
        raw_blend["proposed_campaigns_by_space"] = campaigns
    raw_blend["proposed_recipes_by_space"] = proposed
    raw_blend["proposed_matrix_by_space"] = proposed_matrices
    prepared.config[CHEVRON_CONFIG_KEY] = raw_blend
    routeable, minimum_runs = _optimized_blend_routing_envelope(prepared)
    errors = list(chevron_config_errors(prepared))
    for space_id in sorted(settings["enabled_spaces"]):
        if chevron_is_demand_driven(sc):
            # Permission to blend is optional: unavailable inputs disable that
            # choice through flow constraints instead of invalidating all demand.
            continue
        if chevron_mode(prepared, space_id) != BLEND_MODE_OPTIMIZED:
            continue
        supplied = set(routeable.get(space_id, ()))
        bounds = chevron_component_bounds(prepared, space_id)
        required_but_unavailable = [
            str(row["grade_id"]) for row in bounds
            if float(row.get("min_fraction", 0.0)) > 0
            and str(row["grade_id"]) not in supplied]
        if required_but_unavailable:
            errors.append(
                f"{space_id}: positive minimum share cannot be supplied for "
                + ", ".join(required_but_unavailable) + ".")
        supplied_bounds = [
            row for row in bounds
            if str(row["grade_id"]) in supplied
            and float(row.get("max_fraction", 0.0)) > 0]
        if len(supplied_bounds) < 2:
            errors.append(
                f"{space_id}: optimized blending needs at least two qualities "
                "with enabled inbound routes.")
        elif sum(float(row.get("max_fraction", 0.0))
                 for row in supplied_bounds) < 1.0 - 1e-9:
            errors.append(
                f"{space_id}: enabled inbound routes cannot supply 100% of "
                "the blend within the configured maximum shares.")
    prepared.config["_OPTIMIZED_BLEND_ROUTEABLE_BY_SPACE"] = routeable
    prepared.config["_OPTIMIZED_BLEND_MINIMUM_RUN_T_BY_SPACE"] = minimum_runs
    prepared.config[_BLEND_DESIGN_PREPARED_KEY] = True
    return prepared, errors


def _blend_campaign_records(sc: Scenario, jobs=()) -> Dict[str, Dict[str, object]]:
    """Serializable configured/resolved pile blend decisions for the result."""
    records: Dict[str, Dict[str, object]] = {}
    settings = chevron_settings(sc)
    recorded_spaces = set(settings["active_spaces"]) | {
        str(space_id)
        for space_id, state in settings["campaign_states_by_space"].items()
        if str(state.get("status", "")) in {
            CAMPAIGN_BUILDING, CAMPAIGN_COMPLETE}
    }
    for space_id in sorted(recorded_spaces):
        allocation = chevron_batch_allocation(sc, space_id)
        if len(allocation) < 2:
            continue
        state = chevron_campaign_state(sc, space_id)
        physically_locked = bool(state.get("resolved_recipe"))
        raw_policy = sc.config.get(CHEVRON_CONFIG_KEY, {})
        raw_policy = raw_policy if isinstance(raw_policy, Mapping) else {}
        selected_by_space = raw_policy.get("proposed_matrix_by_space", {})
        selected_by_space = (
            selected_by_space if isinstance(selected_by_space, Mapping)
            else {})
        records[space_id] = {
            "mode": chevron_mode(sc, space_id),
            "decision_status": (
                str(state.get("status", "NOT_STARTED"))
                if physically_locked else "PROPOSED"),
            "campaign_status": str(state.get("status", "NOT_STARTED")),
            "state_basis": "ACTUAL" if physically_locked else "PLANNED",
            "result_grade": chevron_result_grade(sc, space_id),
            "selected_matrix": str(
                selected_by_space.get(space_id, "") or ""),
            "batch_t": float(sum(
                float(row["tonnage_t"]) for row in allocation)),
            "ready_minute": int(chevron_ready_minute(sc, space_id)),
            "composition": [dict(row) for row in allocation],
            "resulting_quality": dict(
                chevron_blended_quality(sc, space_id)),
            "receiving_checks": chevron_receiving_quality_checks(
                sc, space_id),
            "quality_basis": (
                "mass-weighted dry solids for chemistry; physical mass for "
                "moisture; downstream receiving specifications"),
        }
        if chevron_is_demand_driven(sc):
            metadata = dict(raw_policy.get("proposed_campaigns_by_space", {}).get(space_id, {}) or {})
            records[space_id]["total_t"] = records[space_id]["batch_t"]
            records[space_id]["quantity_mode"] = "DEMAND_DRIVEN"
            records[space_id]["campaign_id"] = metadata.get("campaign_id", state.get("campaign_id", ""))
            records[space_id]["planned_from_minute"] = metadata.get("planned_from_minute")
            records[space_id]["served_grades"] = list(metadata.get("served_grades", []))
            records[space_id]["served_destinations"] = list(metadata.get("served_destinations", []))
            records[space_id]["served_receiving_intervals"] = copy.deepcopy(
                metadata.get("served_receiving_intervals", []))
            quality = records[space_id]["resulting_quality"]
            checks = []
            for interval in metadata.get("served_receiving_intervals", []):
                for spec in interval["specs"]:
                    value = quality.get(spec["parameter"])
                    lower, upper = spec["minimum"], spec["maximum"]
                    checks.append({
                        **spec, "destination": interval["destination"],
                        "requested_grade": interval["requested_grade"],
                        "buckets": list(interval["buckets"]), "value": value,
                        "on_spec": value is not None and
                            (lower is None or value >= lower - 1e-9) and
                            (upper is None or value <= upper + 1e-9),
                    })
            records[space_id]["receiving_checks"] = checks
            records[space_id]["pass_basis"] = "FLOW_DERIVED"
            records[space_id]["pass_estimates_by_job"] = {
                job.id: dict(job.chevron_pass_estimate) for job in jobs
                if job.kind == "stack" and (job.target_space or job.space) == space_id
                and job.chevron_pass_estimate}
    return records


def _attach_blend_campaigns(
        result: PlanResult, sc: Scenario) -> PlanResult:
    # Solver-extracted records are authoritative: their composition came from
    # the joint blend/schedule variables on a private planning copy. Merge in
    # configured/frozen records without letting an outer wrapper reconstruct
    # stale pre-solve proposals over them.
    records = _blend_campaign_records(sc, result.jobs)
    records.update(dict(result.blend_campaigns or {}))
    result.blend_campaigns = records
    result.breakdown["optimized_blend_campaigns"] = float(sum(
        record.get("mode") == BLEND_MODE_OPTIMIZED
        for record in result.blend_campaigns.values()))
    return result


def solve_weekly_rolling_plan(
        sc: Scenario, opts: Optional[SolveOptions] = None,
        progress_cb=None) -> PlanResult:
    """Solve a long hourly horizon as state-coupled civil-day windows.

    This is not seven independent plans: terminal physical inventory, grade
    layers, equipment positions and delayed corridor parcels become the next
    window's initial state.  Absolute bucket offsets keep dated demand and all
    machine/link/line/site contingencies aligned to the original week.
    """
    opts = opts or SolveOptions()
    state_errors = _initial_state_errors(sc, opts)
    if state_errors:
        return PlanResult(
            status="ERROR", solver="cp-sat-weekly-rolling",
            reasons=["BAD_INITIAL_OBJECTIVE_STATE " + message
                     for message in state_errors])
    washing_errors = washing.configuration_errors(sc)
    if washing_errors:
        return PlanResult(status="ERROR", reasons=washing_errors)
    sc, blend_errors = _prepare_blend_design_scenario(sc)
    if blend_errors:
        return PlanResult(
            status="ERROR", solver="cp-sat-blend-design",
            reasons=["BLEND_DESIGN " + message for message in blend_errors])
    native_min = sc.sim.native_bucket_minutes()
    if native_min <= 0 or 1_440 % native_min:
        return PlanResult(
            status="ERROR", solver="cp-sat-weekly-rolling",
            reasons=["WEEKLY_GRID daily rolling requires a whole-minute "
                     "planning step that divides 24 hours"])
    T = int(sc.sim.horizon_buckets)
    horizon_offset = int(opts.horizon_offset)
    windows: List[Tuple[int, int]] = []
    window_start = horizon_offset
    prior_date = None
    for absolute_bucket in range(horizon_offset, horizon_offset + T):
        bucket_date = sc.sim.bucket_dt(absolute_bucket).date()
        if prior_date is not None and bucket_date != prior_date:
            windows.append((window_start, absolute_bucket))
            window_start = absolute_bucket
        prior_date = bucket_date
    windows.append((window_start, horizon_offset + T))

    # A variable composition must see every source outage, derate, inventory
    # draw and shared-resource conflict through its construction deadline. A
    # one-day myopic window could otherwise choose day-1 tonnes that cannot be
    # completed on day 2. Coalesce the opening rolling windows through the
    # latest unresolved optimized-pile readiness boundary; all such piles are
    # then selected and scheduled in one joint CP-SAT model.
    unresolved_ready_minutes = [
        chevron_ready_minute(sc, space_id)
        for space_id in chevron_settings(sc)["active_spaces"]
        if chevron_mode(sc, space_id) == BLEND_MODE_OPTIMIZED
        and not chevron_campaign_state(sc, space_id).get("resolved_recipe")
    ]
    if unresolved_ready_minutes:
        joint_end = int(math.ceil(
            max(unresolved_ready_minutes) / float(native_min)))
        joint_end = max(horizon_offset, min(
            joint_end, horizon_offset + T))
        if joint_end > horizon_offset:
            tail_windows: List[Tuple[int, int]] = []
            for start, end in windows:
                if end <= joint_end:
                    continue
                tail_windows.append((max(start, joint_end), end))
            windows = [(horizon_offset, joint_end)] + tail_windows
    if len(windows) <= 1:
        return _attach_blend_campaigns(
            _solve_uniform_plan(sc, opts, progress_cb=progress_cb), sc)

    policy_error = _louta_policy_mass_error(sc)
    if policy_error:
        return PlanResult(
            status="ERROR", solver="cp-sat-weekly-rolling",
            reasons=[f"LOUTA_POLICY_MASS_MISMATCH {policy_error}"])
    stage_profiles, corridor_profiles = _weekly_window_target_profiles(
        sc, windows)
    configured_wall = float(sc.config.get(
        "WEEKLY_TOTAL_WALL_TIME_S", 84.0) or 84.0)
    hard_wall_s = max(float(
        opts.max_wall_time_s if opts.max_wall_time_s is not None
        else configured_wall), 0.1)
    deterministic_total = max(float(
        opts.max_time_s if opts.max_time_s is not None
        else sc.sim.max_solve_s), 0.01)
    # One deterministic unit has historically taken several wall seconds on
    # this model.  A bounded one-unit daily search is enough to find an
    # incumbent while the explicit wall allocation prevents runaway weeks.
    deterministic_per_window = min(
        max(deterministic_total / len(windows), 1.0), 2.0)
    chevron_active = bool(chevron_settings(sc)["active_spaces"])
    prewash_active = bool(prewash_blending.enabled_points(sc))
    if chevron_active or prewash_active:
        # Chevron construction and pre-washing source-share campaigns both
        # add coupled grade-specific balances. Give each civil-day solve
        # enough deterministic search to establish its first incumbent while
        # retaining the operator wall-time guard.
        complex_floor = 16.0 if chevron_active and prewash_active else 8.0
        deterministic_per_window = max(
            deterministic_total / len(windows), complex_floor)

    working = copy.deepcopy(sc)
    working.sim.mixed_resolution_enabled = False
    rolling_candidates, _ = build_candidates(
        sc, RouteGraph.from_scenario(sc))
    results: List[PlanResult] = []
    rolling_machine_groups = dict(opts.initial_machine_groups)
    rolling_feed_gap = dict(opts.initial_feed_gap)
    rolling_open_feed_gap_minutes = dict(
        opts.initial_open_feed_gap_minutes)
    wall_start = time.time()
    failed_index: Optional[int] = None
    # Reproducible mode: whether a later civil day is solved AT ALL must not
    # depend on how long the earlier days happened to take. Under load this
    # gate silently drops a window and returns a shorter plan, which is the
    # same class of defect as a wall-paced solve - just at window granularity.
    # The guard stays active in fast mode, where latency is the contract.
    reproducible_windows = (
        scheduling_values(sc).get("SOLVE_MODE") == "reproducible")
    for index, (start, end) in enumerate(windows):
        elapsed = time.time() - wall_start
        remaining_wall = (
            float("inf") if reproducible_windows else hard_wall_s - elapsed)
        remaining_windows = len(windows) - index
        if remaining_wall <= 0:
            results.append(PlanResult(
                status="UNKNOWN", solver="cp-sat",
                reasons=["WEEKLY_HARD_WALL_EXHAUSTED before window start"]))
            failed_index = index
            break
        if progress_cb:
            progress_cb(
                f"weekly window {index + 1}/{len(windows)} "
                f"(buckets {start}-{end - 1})")
        reopen_depleted_chevron_campaigns(working)
        day = copy.deepcopy(working)
        day.sim.horizon_buckets = end - start
        day.sim.horizon_minutes = (end - start) * native_min
        # Planner-controlled transfers must be rescheduled rather than dumped
        # merely to repair a weak incumbent.  Uncontrolled exogenous inflow
        # retains a bounded emergency overflow path in the uniform model.
        day.config["ALLOW_CONTROLLED_FLOW_SPILL"] = False
        boundary_fraction = float(end - horizon_offset) / float(T)
        exact_targets = sc.config.get("TERMINAL_INVENTORY_EXACT_T", {})
        if isinstance(exact_targets, dict):
            day.config["TERMINAL_INVENTORY_EXACT_T"] = {
                space_id: (
                    float(sc.space(space_id).mass_t)
                    + (float(target) - float(sc.space(space_id).mass_t))
                    * boundary_fraction)
                for space_id, target in exact_targets.items()
                if any(space.id == space_id for space in sc.spaces)
            }
        minimum_targets = sc.config.get("TERMINAL_INVENTORY_MIN_T", {})
        if isinstance(minimum_targets, dict):
            day.config["TERMINAL_INVENTORY_MIN_T"] = {}
            final_only_minima = set(sc.config.get(
                "FINAL_ONLY_TERMINAL_INVENTORY_MIN_SPACES", []) or [])
            for space_id, target in minimum_targets.items():
                if not any(space.id == space_id for space in sc.spaces):
                    continue
                if index < len(windows) - 1 and space_id in final_only_minima:
                    continue
                initial = float(sc.space(space_id).mass_t)
                final_minimum = float(target)
                day.config["TERMINAL_INVENTORY_MIN_T"][space_id] = (
                    initial + (final_minimum - initial) * boundary_fraction
                    if final_minimum > initial else final_minimum)
        if index < len(windows) - 1:
            # The M16 supply-position ceiling is a whole-horizon policy, not a
            # daily reset condition. Repeating it at every rolling boundary
            # can manufacture spill by treating harmless intermediate stock
            # as excess. Inventory targets above follow an explicit linear
            # boundary trajectory so the final target remains reachable.
            day.config.pop("M16_TERMINAL_SUPPLY_POSITION_MAX_T", None)
        day.config["PROCESS_STAGE_OUTPUT_TARGET_T"] = {
            stage: values[index] for stage, values in stage_profiles.items()}
        day.config["CORRIDOR_DISPATCH_TARGET_T"] = {
            link_id: values[index]
            for link_id, values in corridor_profiles.items()}
        day.config["LOU_WASH_LINE_OUTPUT_TARGET_T"] = {
            stage: day.config["PROCESS_STAGE_OUTPUT_TARGET_T"].get(stage, 0)
            for stage in ("LOU-WASH-L1", "LOU-WASH-L2")}
        day.config["LOU_C1_STOCKED_ROUTE_TARGET_T"] = float(
            day.config["CORRIDOR_DISPATCH_TARGET_T"].get("C1", 0))
        window_day_equivalent = max(
            (end - start) * native_min / 1_440.0, 1.0)
        remaining_day_equivalent = max(sum(
            (window_end - window_start) * native_min / 1_440.0
            for window_start, window_end in windows[index:]),
            window_day_equivalent)
        # The per-window wall cap must be a hang guard in reproducible mode,
        # not a pacing device. Leaving the configured WEEKLY_WINDOW_WALL_TIME_S
        # (55 s here) in force cut a window short whenever the machine was
        # busy, and the plan changed with it - reproducible runs diverged 1 in
        # 3 under load even though ``remaining_wall`` was already unbounded.
        window_wall_cap = float(
            sc.config.get("WEEKLY_WINDOW_WALL_TIME_S", 12.0) or 12.0)
        if reproducible_windows:
            # x3, deliberately modest. Measured 29-Sep-2026 on FULL_FLOW: the
            # configured 55 s cap truncates about half of a window's search
            # (a window needs ~106 s of wall to exhaust its deterministic
            # budget), and that load-dependent truncation is what made
            # "reproducible" runs diverge 1 in 3. But raising the guard far
            # enough to never bind made one solve take 6.5 HOURS, because the
            # refinement passes are separately unbounded in this mode.
            #
            # x3 (165 s here) clears the measured need without inviting that.
            # If a scenario still truncates, LOWER max_time_s - the
            # deterministic budget is the right lever for reproducible runs,
            # not a bigger wall guard.
            window_wall_cap *= 3.0
        per_window_wall = max(
            min(remaining_wall * window_day_equivalent
                / remaining_day_equivalent,
                window_wall_cap * window_day_equivalent),
            0.1)
        if index == 0 and unresolved_ready_minutes and \
                end * native_min >= max(unresolved_ready_minutes):
            per_window_wall = max(per_window_wall, min(
                remaining_wall,
                float(sc.config.get(
                    "OPTIMIZED_BLEND_JOINT_WALL_TIME_S", 40.0) or 40.0)))
        window_banned = list(opts.banned)
        if index < len(windows) - 1:
            # Avoid knowingly handing the next myopic window a controlled
            # arrival it had no opportunity to reserve stacking capacity for.
            # Existing/imported belt parcels are still carried explicitly;
            # ordinary corridor work is simply scheduled early enough to
            # arrive and be stacked inside its allocating civil day.
            for candidate in rolling_candidates:
                if candidate.kind != "dispatch":
                    continue
                try:
                    lag = max(int(sc.link(
                        candidate.dest_id).transit_buckets), 0)
                except StopIteration:
                    lag = 0
                late_start = max(end - lag, start)
                if late_start < end:
                    window_banned.append((
                        candidate.machine, candidate.space,
                        candidate.dest_id, late_start, end - 1))
        day_result = _solve_uniform_plan(
            day, SolveOptions(
                horizon_offset=start,
                start_minute_offset=start * native_min,
                banned=window_banned,
                machine_rate_factor=dict(opts.machine_rate_factor),
                max_time_s=(
                    deterministic_per_window * window_day_equivalent),
                max_wall_time_s=per_window_wall,
                log=opts.log,
                initial_machine_groups=dict(rolling_machine_groups),
                initial_feed_gap=dict(rolling_feed_gap),
                initial_open_feed_gap_minutes=dict(
                    rolling_open_feed_gap_minutes)),
            progress_cb=None)
        results.append(day_result)
        if day_result.status not in ("OPTIMAL", "FEASIBLE"):
            failed_index = index
            break
        rolling_machine_groups = dict(
            day_result.terminal_machine_groups)
        rolling_feed_gap = dict(day_result.terminal_feed_gap)
        rolling_open_feed_gap_minutes = dict(
            day_result.terminal_open_feed_gap_minutes)
        if day_result.blend_campaigns:
            raw_blend = working.config.get(CHEVRON_CONFIG_KEY, {})
            raw_blend = dict(raw_blend) if isinstance(raw_blend, dict) else {}
            proposals = dict(raw_blend.get(
                "proposed_recipes_by_space", {}) or {})
            for space_id, record in day_result.blend_campaigns.items():
                composition = list(record.get("composition", ()) or ())
                if composition:
                    proposals[space_id] = [
                        dict(component) for component in composition]
            raw_blend["proposed_recipes_by_space"] = proposals
            if chevron_is_demand_driven(working):
                campaigns = dict(raw_blend.get("proposed_campaigns_by_space", {}) or {})
                campaigns.update(copy.deepcopy(day_result.blend_campaigns))
                raw_blend["proposed_campaigns_by_space"] = campaigns
            working.config[CHEVRON_CONFIG_KEY] = raw_blend
        _carry_segment_and_machine_state(working, day_result)
        update_chevron_campaign_state(
            working,
            day_result.jobs,
            final_inventory_by_space=day_result.inventory,
            completed_through_minute=end * native_min,
            include_planned=True,
        )
        working.belt_profile = _carry_future_belt_profile(
            day, day_result, end)

    elapsed = time.time() - wall_start
    merged = _merge_weekly_windows(
        sc, windows, results, hard_wall_s=hard_wall_s,
        elapsed_s=elapsed, failed_index=failed_index,
        horizon_offset=horizon_offset)
    # A pile may host a different blend after the previous one has drained.
    # Keep per-lot history, not merely the last window's recipe for every job.
    histories = {}
    for index, result in enumerate(results):
        if result.status not in ("OPTIMAL", "FEASIBLE"):
            continue
        for space_id, record in result.blend_campaigns.items():
            entry = copy.deepcopy(record)
            entry.setdefault("planned_from_minute", windows[index][0] * native_min)
            history = histories.setdefault(space_id, [])
            if history and entry.get("campaign_id") and entry.get("campaign_id") == history[-1].get("campaign_id"):
                history[-1].update(entry)
            else:
                history.append(entry)
    for space_id, history in histories.items():
        merged.blend_campaigns[space_id] = {
            **copy.deepcopy(history[-1]), "campaign_history": history}
    if unresolved_ready_minutes:
        merged.breakdown["joint_blend_schedule_campaigns"] = float(sum(
            1 for space_id in chevron_settings(sc)["active_spaces"]
            if chevron_mode(sc, space_id) == BLEND_MODE_OPTIMIZED
            and not chevron_campaign_state(sc, space_id).get(
                "resolved_recipe")))
        merged.breakdown["joint_blend_lookahead_h"] = float(
            (windows[0][1] - windows[0][0]) * native_min / 60.0)
    return _attach_blend_campaigns(merged, working)


def solve_plan(sc: Scenario, opts: Optional[SolveOptions] = None,
               progress_cb=None) -> PlanResult:
    try:
        return _solve_plan_impl(sc, opts, progress_cb=progress_cb)
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        if washing.enabled(sc):
            return PlanResult(status="ERROR", reasons=[f"WASHING_MODEL_INPUT {exc}"])
        raise


def _solve_plan_impl(sc: Scenario, opts: Optional[SolveOptions] = None,
               progress_cb=None) -> PlanResult:
    """Solve on the configured planning time grid.

    Legacy/custom scenarios retain the native uniform model.  A scenario that
    explicitly enables mixed resolution receives an authoritative fine-grid
    near-term prefix and the native tactical tail; no caller/UI fork is
    required.
    """
    opts = opts or SolveOptions()
    # Rebuild configured external supply on the active absolute grid. Imported
    # scenarios without this feature retain their original boundary flows.
    from . import mine_arrivals
    if mine_arrivals.CONFIG_KEY in sc.config:
        sc = copy.deepcopy(sc)
        try:
            mine_arrivals.refresh(sc, horizon_offset=opts.horizon_offset)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            return PlanResult(status="ERROR", reasons=[f"MINE_ARRIVALS_INPUT {exc}"])
    errors = washing.configuration_errors(sc)
    if errors:
        return PlanResult(status="ERROR", reasons=errors)
    if washing.enabled(sc) and sc.sim.mixed_resolution_enabled:
        return PlanResult(status="ERROR", reasons=["WASHING_GRID: grade-resolved washing requires native uniform/weekly planning; mixed resolution is not supported yet."])
    state_errors = _initial_state_errors(sc, opts)
    if state_errors:
        return PlanResult(
            status="ERROR", solver="cp-sat",
            reasons=["BAD_INITIAL_OBJECTIVE_STATE " + message
                     for message in state_errors])
    sc, blend_errors = _prepare_blend_design_scenario(sc)
    if blend_errors:
        return PlanResult(
            status="ERROR", solver="cp-sat-blend-design",
            reasons=["BLEND_DESIGN " + message for message in blend_errors])
    if bool(getattr(sc.sim, "mixed_resolution_enabled", False)):
        result = solve_mixed_resolution_plan(
            sc, opts, progress_cb=progress_cb)
        return _attach_blend_campaigns(result, sc)
    if bool(sc.config.get("WEEKLY_ROLLING_SOLVE_ENABLED", False)) and \
            int(sc.sim.horizon_buckets) * sc.sim.native_bucket_minutes() > 1_440:
        result = solve_weekly_rolling_plan(
            sc, opts, progress_cb=progress_cb)
        return _attach_blend_campaigns(result, sc)
    return _attach_blend_campaigns(
        _solve_uniform_plan(sc, opts, progress_cb=progress_cb), sc)


def solve_mixed_resolution_plan(
        sc: Scenario, opts: Optional[SolveOptions] = None,
        progress_cb=None) -> PlanResult:
    """Solve a 15-minute near-term prefix plus an hourly tactical tail.

    The tactical solve establishes a feasible whole-horizon material plan and
    boundary inventory.  The near prefix is then re-optimised on an explicitly
    resampled physical-time grid and tied back to that boundary.  The returned
    PlanResult remains compatible with native-bucket reporting while every job
    also carries canonical minute bounds.
    """
    opts = opts or SolveOptions()
    state_errors = _initial_state_errors(sc, opts)
    if state_errors:
        return PlanResult(
            status="ERROR", solver="cp-sat-mixed",
            reasons=["BAD_INITIAL_OBJECTIVE_STATE " + message
                     for message in state_errors])
    sc, blend_errors = _prepare_blend_design_scenario(sc)
    if blend_errors:
        return PlanResult(
            status="ERROR", solver="cp-sat-blend-design",
            reasons=["BLEND_DESIGN " + message for message in blend_errors])
    mixed_wall_start = time.time()
    errors = sc.sim.mixed_resolution_errors()
    if errors:
        return PlanResult(
            status="ERROR", solver="cp-sat-mixed",
            horizon_offset=opts.horizon_offset,
            reasons=["MIXED_RESOLUTION_CONFIG " + "; ".join(errors)])

    from .planning_time import (
        align_tactical_job_minutes, configure_prefix_boundary,
        merge_mixed_resolution_results,
        resample_banned_window, resample_scenario_window)

    native_min = sc.sim.native_bucket_minutes()
    absolute_start_min = (
        int(opts.start_minute_offset)
        if opts.start_minute_offset is not None
        else int(opts.horizon_offset) * native_min)
    if absolute_start_min < 0 or (
            absolute_start_min % int(sc.sim.near_term_step_min)):
        return PlanResult(
            status="ERROR", solver="cp-sat-mixed",
            horizon_offset=opts.horizon_offset,
            reasons=[
                "MIXED_RESOLUTION_START start_minute_offset must be a "
                "non-negative multiple of near_term_step_min"])
    native_offset = absolute_start_min // native_min
    available_min = int(sc.sim.horizon_buckets) * native_min
    total_min = min(sc.sim.effective_horizon_minutes(), available_min)
    near_min = min(int(sc.sim.near_term_horizon_min), total_min)
    # The mixed boundary is always native-grid aligned.  A shortened rolling
    # horizon can be smaller than the configured prefix but never fractional.
    near_min -= near_min % native_min
    if near_min <= 0 or int(sc.sim.near_term_step_min) == native_min:
        return _solve_uniform_plan(sc, opts, progress_cb=progress_cb)

    budget = float(opts.max_time_s or sc.sim.max_solve_s)
    # The hourly tactical model normally finds an incumbent quickly; give it
    # enough deterministic search to establish the boundary, then direct the
    # extra operator budget to the more constrained fine-prefix solve.  A cap
    # also avoids making an 8-second planning request much slower than the
    # previous hourly run before refinement even begins.
    tactical_budget = max(
        min(budget * 0.65, 3.0 if budget >= 4.0 else budget), 0.2)
    near_budget = max(budget - tactical_budget, 0.2)

    def _progress(prefix: str):
        if progress_cb is None:
            return None
        return lambda stage: progress_cb(f"{prefix}: {stage}")

    tactical_opts = SolveOptions(
        horizon_offset=native_offset,
        start_minute_offset=absolute_start_min,
        banned=list(opts.banned),
        machine_rate_factor=dict(opts.machine_rate_factor),
        max_time_s=tactical_budget,
        max_wall_time_s=None,
        log=opts.log,
        initial_machine_groups=dict(opts.initial_machine_groups),
        initial_feed_gap=dict(opts.initial_feed_gap),
        initial_open_feed_gap_minutes=dict(
            opts.initial_open_feed_gap_minutes))
    tactical_sc = sc
    canonical_bucket_count = total_min // native_min
    if canonical_bucket_count != sc.sim.horizon_buckets:
        tactical_sc = copy.deepcopy(sc)
        tactical_sc.sim.horizon_buckets = canonical_bucket_count
        tactical_sc.sim.horizon_minutes = total_min
        tactical_sc.sim.mixed_resolution_enabled = False
    weekly_tactical = bool(tactical_sc.config.get(
        "WEEKLY_ROLLING_SOLVE_ENABLED", False)) and \
        int(tactical_sc.sim.horizon_buckets) * native_min > 1_440
    mixed_hard_wall_s = opts.max_wall_time_s
    if mixed_hard_wall_s is None and weekly_tactical:
        mixed_hard_wall_s = float(tactical_sc.config.get(
            "MIXED_TOTAL_WALL_TIME_S",
            tactical_sc.config.get("WEEKLY_TOTAL_WALL_TIME_S", 84.0))
                                  or 84.0)
    if mixed_hard_wall_s is not None:
        mixed_hard_wall_s = max(float(mixed_hard_wall_s), 0.1)
        tactical_fraction = max(min(float(tactical_sc.config.get(
            "MIXED_TACTICAL_WALL_FRACTION", 0.8) or 0.8), 0.95), 0.1)
        tactical_opts.max_wall_time_s = max(
            mixed_hard_wall_s * tactical_fraction, 0.1)
    tactical = (
        solve_weekly_rolling_plan(
            tactical_sc, tactical_opts,
            progress_cb=_progress("tactical"))
        if weekly_tactical
        else _solve_uniform_plan(
            tactical_sc, tactical_opts, progress_cb=_progress("tactical")))
    align_tactical_job_minutes(
        tactical, native_horizon_offset=native_offset,
        absolute_start_minute=absolute_start_min,
        native_step_min=native_min)
    if tactical.status not in ("OPTIMAL", "FEASIBLE"):
        tactical.solver = "cp-sat-mixed"
        tactical.breakdown.update({
            "planning_mixed_resolution": 1.0,
            "planning_horizon_min": float(total_min),
            "planning_near_horizon_min": float(near_min),
            "planning_near_step_min": float(sc.sim.near_term_step_min),
            "planning_far_step_min": float(sc.sim.far_term_step_min),
        })
        if mixed_hard_wall_s is not None:
            tactical.breakdown.update({
                "planning_mixed_total_hard_wall_s": mixed_hard_wall_s,
                "planning_mixed_elapsed_s": time.time() - mixed_wall_start,
            })
        return tactical

    try:
        refined = resample_scenario_window(
            tactical_sc,
            window_start_minute=absolute_start_min,
            window_duration_min=near_min,
            step_min=int(sc.sim.near_term_step_min),
            commit_window_min=int(sc.sim.commit_window_min))
        if tactical.blend_campaigns:
            raw_blend = refined.config.get(CHEVRON_CONFIG_KEY, {})
            raw_blend = dict(raw_blend) if isinstance(raw_blend, dict) else {}
            proposals = dict(raw_blend.get(
                "proposed_recipes_by_space", {}) or {})
            prefix_campaigns = {}
            for space_id, record in tactical.blend_campaigns.items():
                history = record.get("campaign_history", [])
                if history:
                    record = history[0]
                prefix_campaigns[space_id] = record
                composition = list(record.get("composition", ()) or ())
                if composition:
                    proposals[space_id] = [
                        dict(component) for component in composition]
            raw_blend["proposed_recipes_by_space"] = proposals
            if chevron_is_demand_driven(refined):
                campaigns = dict(raw_blend.get("proposed_campaigns_by_space", {}) or {})
                campaigns.update(copy.deepcopy(prefix_campaigns))
                raw_blend["proposed_campaigns_by_space"] = campaigns
            refined.config[CHEVRON_CONFIG_KEY] = raw_blend
            # The fine prefix refines timing of the feasible tactical matrix;
            # it must not independently select a contradictory composition.
            refined.config["_FIX_OPTIMIZED_BLEND_PROPOSALS"] = True
        boundary_index = near_min // native_min
        absolute_boundary_bucket = native_offset + boundary_index
        configure_prefix_boundary(
            refined, tactical,
            tactical_boundary_index=boundary_index,
            absolute_boundary_bucket=absolute_boundary_bucket,
            absolute_start_bucket=native_offset)
    except (TypeError, ValueError) as exc:
        tactical.solver = "cp-sat-mixed-fallback"
        tactical.reasons.append(
            f"MIXED_RESOLUTION_RESAMPLE_FAILED: {exc}; using tactical grid")
        if mixed_hard_wall_s is not None:
            tactical.breakdown.update({
                "planning_mixed_total_hard_wall_s": mixed_hard_wall_s,
                "planning_mixed_elapsed_s": time.time() - mixed_wall_start,
            })
        return tactical

    remaining_wall_s: Optional[float] = None
    if mixed_hard_wall_s is not None:
        remaining_wall_s = mixed_hard_wall_s - (
            time.time() - mixed_wall_start)
        if remaining_wall_s <= 0.1:
            tactical.solver = "cp-sat-mixed-fallback"
            tactical.reasons.append(
                "MIXED_RESOLUTION_HARD_WALL_EXHAUSTED: retained feasible "
                "weekly tactical plan before fine-prefix solve")
            tactical.breakdown.update({
                "planning_mixed_resolution": 1.0,
                "planning_mixed_fallback": 1.0,
                "planning_mixed_total_hard_wall_s": mixed_hard_wall_s,
                "planning_mixed_elapsed_s": time.time() - mixed_wall_start,
                "planning_tactical_solver_wall_s": float(
                    tactical.solver_wall_s),
            })
            return tactical

    fine_banned = resample_banned_window(
        opts.banned,
        native_step_min=native_min,
        window_start_minute=absolute_start_min,
        window_duration_min=near_min,
        fine_step_min=int(sc.sim.near_term_step_min))
    fine_opts = SolveOptions(
        horizon_offset=0,
        start_minute_offset=absolute_start_min,
        banned=fine_banned,
        machine_rate_factor=dict(opts.machine_rate_factor),
        max_time_s=near_budget,
        max_wall_time_s=remaining_wall_s,
        log=opts.log,
        initial_machine_groups=dict(opts.initial_machine_groups),
        initial_feed_gap=dict(opts.initial_feed_gap),
        initial_open_feed_gap_minutes=dict(
            opts.initial_open_feed_gap_minutes))
    near = _solve_uniform_plan(
        refined, fine_opts, progress_cb=_progress("near term"))
    if near.status not in ("OPTIMAL", "FEASIBLE"):
        tactical.solver = "cp-sat-mixed-fallback"
        tactical.reasons.append(
            "MIXED_RESOLUTION_NEAR_TERM_"
            f"{near.status}: retained feasible tactical plan")
        tactical.reasons.extend(
            reason for reason in near.reasons
            if reason not in tactical.reasons)
        tactical.breakdown.update({
            "planning_mixed_resolution": 1.0,
            "planning_mixed_fallback": 1.0,
            "planning_horizon_min": float(total_min),
            "planning_near_horizon_min": float(near_min),
            "planning_near_step_min": float(sc.sim.near_term_step_min),
            "planning_far_step_min": float(sc.sim.far_term_step_min),
            "planning_near_solver_wall_s": float(near.solver_wall_s),
            "planning_tactical_solver_wall_s": float(
                tactical.solver_wall_s),
        })
        if mixed_hard_wall_s is not None:
            tactical.breakdown.update({
                "planning_mixed_total_hard_wall_s": mixed_hard_wall_s,
                "planning_mixed_elapsed_s": time.time() - mixed_wall_start,
            })
        return tactical

    merged = merge_mixed_resolution_results(
        tactical, near,
        absolute_start_minute=absolute_start_min,
        near_horizon_min=near_min,
        fine_step_min=int(sc.sim.near_term_step_min),
        native_step_min=native_min,
        total_horizon_min=total_min,
        replan_interval_min=int(sc.sim.replan_interval_min),
        commit_window_min=int(sc.sim.commit_window_min))
    merged.breakdown.update(_job_operational_metrics(
        sc, merged,
        horizon_start_minute=absolute_start_min,
        horizon_end_minute=absolute_start_min + total_min))
    merged.breakdown.update(_executed_schedule_breakdown(
        sc, merged, merged.jobs,
        horizon_start_minute=absolute_start_min,
        horizon_end_minute=absolute_start_min + total_min))
    if tactical.blend_campaigns:
        merged.blend_campaigns = {
            space_id: dict(record)
            for space_id, record in tactical.blend_campaigns.items()}
    if mixed_hard_wall_s is not None:
        merged.breakdown.update({
            "planning_mixed_total_hard_wall_s": mixed_hard_wall_s,
            "planning_mixed_elapsed_s": time.time() - mixed_wall_start,
        })
    return merged


def _solve_uniform_plan(sc: Scenario, opts: Optional[SolveOptions] = None,
                        progress_cb=None) -> PlanResult:
    """`progress_cb(stage: str)` is a pure UX notification hook fired at
    stage boundaries (model build / warm start / CP-SAT / extraction).
    It has NO effect on the model or the deterministic result."""
    opts = opts or SolveOptions()
    if chevron_is_demand_driven(sc):
        sc = copy.deepcopy(sc)
        reopen_depleted_chevron_campaigns(sc)
    initial_state_errors = _initial_state_errors(sc, opts)
    if initial_state_errors:
        return PlanResult(
            status="ERROR", solver="cp-sat",
            reasons=["BAD_INITIAL_OBJECTIVE_STATE " + message
                     for message in initial_state_errors])
    grid_errors = sc.sim.uniform_grid_errors()
    if grid_errors:
        return PlanResult(
            status="ERROR", solver="cp-sat",
            reasons=["BAD_TIME_GRID " + "; ".join(grid_errors)])
    invalid_weights = _invalid_objective_weights(sc.weights)
    if invalid_weights:
        return PlanResult(
            status="ERROR",
            reasons=[
                "BAD_OBJECTIVE_WEIGHT active objective weights must be "
                f"finite whole numbers between 0 and "
                f"{OBJECTIVE_WEIGHT_MAX:g}: "
                + ", ".join(invalid_weights)
            ],
        )
    operational_errors = operational_objective_errors(sc)
    if operational_errors:
        return PlanResult(
            status="ERROR", solver="cp-sat",
            reasons=["BAD_OPERATIONAL_OBJECTIVE " + message
                     for message in operational_errors])
    errors = washing.configuration_errors(sc)
    if errors:
        return PlanResult(status="ERROR", reasons=errors)
    stock_contradictions = [s for s in isolated_source_target_shortages(
        sc, horizon_offset=opts.horizon_offset) if s["hard"]]
    if stock_contradictions:
        return PlanResult(status="INFEASIBLE", solver="preflight",
                          reasons=[f"HARD_SOURCE_TARGET_CONTRADICTION[{s['source']}] "
                                   f"target {s['target_t']} t, available {s['available_t']} t; "
                                   "no replenishment route. Review the explicit commitment, not its weight."
                                   for s in stock_contradictions])
    policy_error = _louta_policy_mass_error(sc)
    if policy_error:
        return PlanResult(
            status="ERROR",
            reasons=[f"LOUTA_POLICY_MASS_MISMATCH {policy_error}"])

    def _stage(label: str):
        if progress_cb:
            try:
                progress_cb(label)
            except Exception:
                pass    # a UI hiccup must never kill a solve

    try:
        from ortools.sat.python import cp_model
    except ImportError:  # pragma: no cover
        return PlanResult(status="NO_SOLVER",
                          reasons=["OR-Tools not installed — pip install ortools"])

    # Keep the wall-clock origin distinct from bucket/window loop variables.
    # Python loop targets remain in function scope, so reusing ``t0`` for a
    # quality window would otherwise turn elapsed time into an epoch-sized
    # number in the result.
    wall_start = time.time()
    _stage("building route & constraint model")
    # experimental multi-grade zones: expand zoned piles into per-zone
    # sub-spaces (identity when the flag is off or no zone inputs exist —
    # baseline behaviour and deterministic signatures are unchanged)
    from .zones import expand_pile_zones, zones_enabled
    if zones_enabled(sc):
        try:
            sc = expand_pile_zones(sc)
        except ValueError as exc:
            return PlanResult(status="ERROR", reasons=[f"ZONE_INPUTS {exc}"])
    T = sc.sim.horizon_buckets
    dt = sc.sim.dt_h
    # Weekly plans carry one demand row per destination and native bucket.
    # Looking those rows up through ``Scenario.demand_tph/grade`` inside the
    # candidate x destination x bucket loops turns model construction into an
    # avoidable quadratic scan (more than 100,000 scans of a 1,680-row list in
    # S0).  Build the authoritative last-row-wins profile once for this solve.
    # Keep it local: UI edits mutate ``Scenario.demand`` in place, so a cache
    # stored on the scenario would be vulnerable to stale values.
    demand_rate_profile: Dict[Tuple[str, int], float] = {}
    demand_grade_profile: Dict[Tuple[str, int], str] = {}
    demand_priority_profile: Dict[Tuple[str, int], int] = {}
    for entry in sc.demand:
        key = (entry.dest_id, int(entry.bucket))
        demand_rate_profile[key] = float(entry.rate_tph)
        # A matching blank row deliberately selects the destination default;
        # it must also overwrite an earlier duplicate's explicit grade.
        demand_grade_profile[key] = str(
            getattr(entry, "requested_grade", "") or "").strip()
        if any(destination.id == entry.dest_id for destination in sc.destinations):
            try:
                demand_priority_profile[key] = sc.demand_priority(entry.dest_id, entry.bucket)
            except (TypeError, ValueError, OverflowError) as exc:
                return PlanResult(status="ERROR", reasons=[f"BAD_DEMAND_PRIORITY {exc}"])

    destination_by_id = {
        destination.id: destination for destination in sc.destinations}
    continuity_policies = parse_continuity_policies(
        sc.config.get("CONTINUITY_POLICIES", {}),
        destination_by_id)

    def demand_rate_at(destination_id: str, absolute_bucket: int) -> float:
        line_states = sc.config.get(
            "DESTINATION_LINE_OPERATING_STATE", {})
        if isinstance(line_states, dict) and str(line_states.get(
                destination_id, "")).strip().upper() == "FUTURE":
            return 0.0
        return demand_rate_profile.get(
            (destination_id, int(absolute_bucket)),
            float(destination_by_id[destination_id].demand_default_tph))

    def demand_grade_at(destination_id: str, absolute_bucket: int) -> str:
        configured = demand_grade_profile.get(
            (destination_id, int(absolute_bucket)), "")
        if configured:
            return configured
        return str(destination_by_id[
            destination_id].requested_grade or "").strip()

    def demand_specs_at(destination_id: str,
                        absolute_bucket: int):
        """Receiving limits selected by the active demand-grade campaign."""
        destination = destination_by_id[destination_id]
        grade = sc.grade(demand_grade_at(destination_id, absolute_bucket))
        if grade is not None and getattr(grade, "is_specification", False) \
                and getattr(grade, "receiving_spec", None):
            return list(grade.receiving_spec)
        return list(destination.spec)

    def demand_priority_at(destination_id: str, absolute_bucket: int) -> int:
        return demand_priority_profile.get(
            (destination_id, int(absolute_bucket)),
            max(int(destination_by_id[destination_id].priority), 1))

    def allocation_grade_at(destination_id: str,
                            absolute_bucket: int) -> str:
        """Grade constraint carried by an explicit demand campaign.

        Legacy scenarios use ``Destination.requested_grade`` as an output
        campaign label while allowing source-pile blending against the unit's
        quality limits.  A dated ``DemandEntry`` is the explicit contract that
        selects a source grade for weekly planning.  Process-feed destinations
        remain grade-specific even without an override because their requested
        grade identifies the physical upstream material.
        """
        destination = destination_by_id[destination_id]
        key = (destination_id, int(absolute_bucket))
        if key in demand_grade_profile:
            return (demand_grade_profile[key] or str(
                destination.requested_grade or "").strip())
        if getattr(destination, "demand_kind", "delivery") == "process_feed":
            return str(destination.requested_grade or "").strip()
        return ""

    graph = RouteGraph.from_scenario(sc)
    sc, cands, stack_by_exit = _material_candidates(sc, graph)
    # Candidate eligibility is campaign-grade dependent.  Keep a per-bucket
    # map for constraints and a union for diagnostics/reporting.
    structurally_eligible_cis = {
        destination.id: [
            ci for ci, cand in enumerate(cands)
            if _candidate_serves_destination(
                sc, cand, destination, requested_grade="")
        ]
        for destination in sc.destinations
    }
    demand_cis_by_bucket = {
        (destination.id, t): [
            ci for ci in structurally_eligible_cis[destination.id]
            if _candidate_serves_destination(
                sc, cands[ci], destination,
                requested_grade=allocation_grade_at(
                    destination.id, t + opts.horizon_offset))
        ]
        for destination in sc.destinations
        for t in range(T)
    }
    demand_cis = {
        destination.id: sorted({
            ci
            for t in range(T)
            for ci in demand_cis_by_bucket[(destination.id, t)]
        })
        for destination in sc.destinations
    }
    m = cp_model.CpModel()

    def itn(x: float) -> int:
        return int(round(x))

    def transition_exceeds_bucket(
            machine_id: str, from_space: str, to_space: str) -> bool:
        """Legacy bucket guard; exact preparation is refined minute-by-minute."""
        estimate = estimate_space_transition(
            sc, machine_id, from_space, to_space)
        return estimate.total_min > dt * 60.0 * 0.9

    # ---------- grouping: one activity bool per (machine,space,dest) ---------
    group_keys: List[Tuple] = []
    group_members: Dict[Tuple, List[int]] = {}
    for ci, c in enumerate(cands):
        k = c.gkey()
        if k not in group_members:
            group_members[k] = []
            group_keys.append(k)
        group_members[k].append(ci)
    g_of = {ci: c.gkey() for ci, c in enumerate(cands)}

    # ---------- variables ------------------------------------------------------
    X: Dict[Tuple[int, int], cp_model.IntVar] = {}    # (cand_idx, t) -> tonnes
    # Process-transfer output after the configured mass yield.  For 100 %
    # transfers this aliases X; for washing it is the deterministic floor of
    # input × documented recovery and the difference is reported as reject.
    Y: Dict[Tuple[int, int], object] = {}
    process_yield_coeff: Dict[int, Tuple[int, int]] = {}
    yield_carry_buckets = max(int(sc.config.get(
        "PROCESS_YIELD_CARRY_WINDOW_BUCKETS", 0) or 0), 0)
    AG: Dict[Tuple[Tuple, int], cp_model.IntVar] = {}  # (group key, t) -> active
    for gk in group_keys:
        for t in range(T):
            AG[(gk, t)] = m.new_bool_var(f"g_{hash(gk) & 0xffffff}_{t}")
    for ci, c in enumerate(cands):
        mach = sc.machine(c.machine)
        rf = opts.machine_rate_factor.get(c.machine, 1.0)
        path_cap = c.path.capacity_tph if c.path else mach.rate_max_tph
        cap = itn(min(mach.rate_max_tph * rf, path_cap) * dt)
        for t in range(T):
            x = m.new_int_var(0, max(cap, 0), f"x_{ci}_{t}")
            m.add(x <= cap * AG[(g_of[ci], t)])
            X[(ci, t)] = x
            if c.kind == "process":
                yscale = 10_000
                ycoef = int(round(max(min(c.yield_factor, 1.0), 0.0)
                                  * yscale))
                if ycoef == yscale:
                    Y[(ci, t)] = x
                else:
                    y = m.new_int_var(0, max(cap, 0), f"y_{ci}_{t}")
                    # y = floor(x × ycoef / yscale), an exact integer
                    # transformation with at most one tonne of discretisation
                    # per active bucket.
                    Y[(ci, t)] = y
                    process_yield_coeff[ci] = (ycoef, yscale)
                    if not yield_carry_buckets:
                        m.add(yscale * y <= ycoef * x)
                        m.add(yscale * y >= ycoef * x - (yscale - 1))

    # A rolling window can open before any fully assayed material is capable
    # of reaching PAP. Leaving thousands of material-specific PAP variables
    # free in that case makes CP-SAT rediscover the inevitable zero delivery
    # through inventory and quality propagation. With simultaneous chevron
    # and pre-washing blends that search can consume the entire first-window
    # guard. Prove the zero bound up front, but only when there is no complete
    # opening, pending, direct-process, in-transit, or same-window corridor
    # source. Demand remains unchanged, so the ordinary unmet series reports
    # the unavailable service rather than hiding it.
    if washing.enabled(sc):
        washing_cfg_for_reach = washing.settings(sc)
        pap_ids_for_reach = set(
            washing_cfg_for_reach.get("pap_destinations", []))
        required_assays = set(
            washing_cfg_for_reach.get("g10_limits", {}))
        catalogue_for_reach = sc.config.get("_WASHING_MODEL_CATALOG", {})

        def complete_assay(material_id):
            quality = catalogue_for_reach.get(
                material_id, {}).get("quality", {})
            try:
                return all(
                    parameter in quality
                    and math.isfinite(float(quality[parameter]))
                    for parameter in required_assays)
            except (TypeError, ValueError, OverflowError):
                return False

        initial_materials = sc.config.get("_WASHING_MODEL_INITIAL", {})
        pending_materials = sc.config.get("_WASHING_MODEL_PENDING", {})
        window_start_minute = int(
            opts.start_minute_offset
            if opts.start_minute_offset is not None
            else opts.horizon_offset * dt * 60)
        window_end_minute = window_start_minute + int(
            round(T * dt * 60))
        for destination_id in pap_ids_for_reach:
            pap_cis = demand_cis.get(destination_id, [])
            receiving_spaces = {
                cands[ci].input_space() for ci in pap_cis}
            reachable = any(
                float(mass or 0) > 0 and complete_assay(material_id)
                for space_id in receiving_spaces
                for material_id, mass in initial_materials.get(
                    space_id, {}).items())
            if not reachable:
                reachable = any(
                    complete_assay(material_id)
                    and any(
                        float(batch.get("tonnage_t", 0) or 0) > 0
                        and window_start_minute < int(
                            batch.get("ready_minute", 0) or 0)
                        <= window_end_minute
                        for batch in batches)
                    for space_id in receiving_spaces
                    for material_id, batches in pending_materials.get(
                        space_id, {}).items())
            if not reachable:
                reachable = any(
                    candidate.kind == "process"
                    and candidate.output_space() in receiving_spaces
                    and complete_assay(candidate.output_material_id)
                    for candidate in cands)
            if not reachable:
                stack_inputs = {
                    (candidate.exit_node, candidate.material_id)
                    for candidate in cands
                    if candidate.kind == "stack"
                    and candidate.space in receiving_spaces
                    and complete_assay(candidate.material_id)
                }
                reachable = any(
                    (sc.link(parcel.link_id).dst, parcel.material_id)
                    in stack_inputs
                    and opts.horizon_offset <= parcel.arrive_bucket
                    < opts.horizon_offset + T
                    for parcel in sc.belt_profile)
                if not reachable:
                    reachable = any(
                        candidate.kind == "dispatch"
                        and (candidate.exit_node, candidate.material_id)
                        in stack_inputs
                        and sc.link(candidate.dest_id).transit_buckets < T
                        for candidate in cands)
            if not reachable:
                for ci in pap_cis:
                    for t in range(T):
                        m.add(X[(ci, t)] == 0)

    # ---------- pre-washing feed blend campaigns -----------------------------
    # The physical route alternatives are explicit ProcessTransfer rows. This
    # block adds only the policy contract: each active line/day draws every
    # configured component within its mass-share limits, and all components
    # pass through one common washing mode.  The latter prevents a nominal
    # "pre-wash blend" from being represented as two separately treated
    # products mixed only after washing.
    prewash_errors = prewash_blending.configuration_errors(sc)
    if prewash_errors:
        return PlanResult(
            status="ERROR", solver="cp-sat",
            reasons=[f"BAD_PREWASH_BLEND {message}"
                     for message in prewash_errors])
    prewash_scale = 10_000
    for point_id, point in prewash_blending.enabled_points(sc).items():
        # The site owns one share-selection mode. Available lines may use any
        # recipe whose required stockpiles have opted in. Legacy imported
        # policies without source opt-ins retain their original line subset.
        components = prewash_blending.candidate_components(point)
        point_mode = prewash_blending.point_mode(point)
        catalog = prewash_blending.eligible_matrix_catalog(point)
        allowed_modes = set(point.get(
            "allowed_treatment_modes", ["simple", "full"]))
        source_spaces = {
            str(component["source_space"]) for component in components}
        for stage in prewash_blending.effective_process_stages(point):
            stage_cis = [
                ci for ci, candidate in enumerate(cands)
                if candidate.kind == "process"
                and candidate.process_stage == stage
                and candidate.source_space in source_spaces
            ]
            if not stage_cis:
                continue
            for ci in stage_cis:
                if cands[ci].treatment_id and \
                        cands[ci].treatment_id not in allowed_modes:
                    for t in range(T):
                        m.add(X[(ci, t)] == 0)
            day_buckets: Dict[str, List[int]] = {}
            for t in range(T):
                absolute = t + opts.horizon_offset
                day = sc.sim.bucket_dt(absolute).date().isoformat()
                day_buckets.setdefault(day, []).append(t)
            for day, tids in day_buckets.items():
                total = sum(X[(ci, t)] for ci in stage_cis for t in tids)
                daily_cap = sum(
                    max(itn(min(
                        sc.machine(cands[ci].machine).rate_max_tph
                        * opts.machine_rate_factor.get(
                            cands[ci].machine, 1.0),
                        cands[ci].path.capacity_tph
                        if cands[ci].path else
                        sc.machine(cands[ci].machine).rate_max_tph
                    ) * dt), 0)
                    for ci in stage_cis for _t in tids)
                source_totals = {
                    source: sum(
                        X[(ci, t)] for ci in stage_cis for t in tids
                        if cands[ci].source_space == source)
                    for source in source_spaces
                }
                minimum_input_t = max(itn(float(point.get(
                    "min_input_grade_t", 0.0))), 0)
                if minimum_input_t:
                    for source, source_total in source_totals.items():
                        used = m.new_bool_var(
                            f"prewash_input_used_{point_id}_{stage}_{day}_{source}")
                        m.add(source_total >= minimum_input_t * used)
                        m.add(source_total <= max(daily_cap, 1) * used)
                    # Mine arrivals retain grade-resolved pools inside each
                    # source stockpile. The operating rule is per input grade,
                    # not merely per pile: prohibit trace 1 t assay components
                    # from being used to tune a daily blend.
                    material_totals = {
                        (source, material_id): sum(
                            X[(ci, t)] for ci in stage_cis for t in tids
                            if cands[ci].source_space == source
                            and cands[ci].material_id == material_id)
                        for source in source_spaces
                        for material_id in {
                            cands[ci].material_id for ci in stage_cis
                            if cands[ci].source_space == source
                            and cands[ci].material_id
                        }
                    }
                    for (source, material_id), material_total in material_totals.items():
                        used = m.new_bool_var(
                            f"prewash_grade_used_{point_id}_{stage}_{day}_"
                            f"{source}_{material_id}")
                        m.add(material_total >= minimum_input_t * used)
                        m.add(material_total <= max(daily_cap, 1) * used)
                if point_mode == prewash_blending.BLEND_MODE_MATRIX:
                    selected_matrices = {
                        str(entry["id"]): m.new_bool_var(
                            f"prewash_matrix_{point_id}_{stage}_{day}_"
                            f"{entry['id']}")
                        for entry in catalog
                    }
                    m.add(sum(selected_matrices.values()) <= 1)
                    m.add(total <= max(daily_cap, 1) * sum(
                        selected_matrices.values()))
                    for entry in catalog:
                        by_source = {
                            str(row["source_space"]): int(round(float(
                                row["target_fraction"]) * prewash_scale))
                            for row in entry["components"]
                        }
                        selector = selected_matrices[str(entry["id"])]
                        for source in source_spaces:
                            source_total = source_totals[source]
                            m.add(
                                prewash_scale * source_total
                                == by_source.get(source, 0) * total
                            ).only_enforce_if(selector)
                else:
                    for component in components:
                        source = str(component["source_space"])
                        source_total = source_totals[source]
                        lower = int(round(float(
                            component["min_fraction"]) * prewash_scale))
                        upper = int(round(float(
                            component["max_fraction"]) * prewash_scale))
                        m.add(prewash_scale * source_total >= lower * total)
                        m.add(prewash_scale * source_total <= upper * total)

                treatment_modes = sorted({
                    cands[ci].treatment_id for ci in stage_cis
                    if cands[ci].treatment_id in allowed_modes
                })
                if treatment_modes:
                    selected_modes = {
                        mode: m.new_bool_var(
                            f"prewash_mode_{point_id}_{stage}_{day}_{mode}")
                        for mode in treatment_modes
                    }
                    m.add(sum(selected_modes.values()) <= 1)
                    for ci in stage_cis:
                        mode = cands[ci].treatment_id
                        if mode not in selected_modes:
                            continue
                        for t in tids:
                            m.add(X[(ci, t)] == 0).only_enforce_if(
                                selected_modes[mode].Not())

    # One blend campaign is a physically constructed stockpile, not an inlet
    # mixing switch. In optimized mode the exact grade tonnes are variables in
    # this same route-and-schedule model. A matrix is therefore accepted only
    # when the configured inventory, calendars, routes and equipment can stack
    # it by the readiness boundary.
    blend_settings = chevron_settings(sc)
    managed_blend_spaces = set(blend_settings["active_spaces"]) | {
        str(space_id)
        for space_id, state in blend_settings["campaign_states_by_space"].items()
        if str(state.get("status", "")) in {
            CAMPAIGN_BUILDING, CAMPAIGN_COMPLETE, CAMPAIGN_DEPLETED}
    }
    step_min = max(int(round(dt * 60.0)), 1)
    window_start_min = (
        int(opts.start_minute_offset)
        if opts.start_minute_offset is not None
        else int(opts.horizon_offset) * step_min)
    window_end_min = window_start_min + T * step_min
    routeable_by_space = dict(sc.config.get(
        "_OPTIMIZED_BLEND_ROUTEABLE_BY_SPACE", {}) or {})
    fixed_optimized_proposals = bool(sc.config.get(
        "_FIX_OPTIMIZED_BLEND_PROPOSALS", False))
    blend_target_vars_by_space: Dict[
        str, Dict[str, cp_model.IntVar]] = {}
    blend_matrix_selection_vars_by_space: Dict[
        str, Dict[str, cp_model.IntVar]] = {}
    blend_quality_slack_terms: List[Tuple[int, cp_model.IntVar]] = []
    blend_target_deviation_terms: List[Tuple[int, cp_model.IntVar]] = []
    blend_component_use_terms: List[cp_model.IntVar] = []
    variable_blend_space_ids: set[str] = set()
    blend_model_errors: List[str] = []
    blend_quality_scale = QSCALE
    blend_quality_slacks_by_receiving_space = {}
    blend_dry_scale = 1_000
    dynamic_blend_decisions = {}

    for space_id in sorted(managed_blend_spaces):
        recipe = chevron_recipe(sc, space_id)
        allocation = chevron_batch_allocation(sc, space_id)
        state = chevron_campaign_state(sc, space_id)
        status = str(state.get("status", ""))
        construction_enabled = space_id in blend_settings["active_spaces"]
        if chevron_is_demand_driven(sc) and status not in {
                CAMPAIGN_COMPLETE, CAMPAIGN_DEPLETED} and construction_enabled:
            decision = dynamic_blending.add_dynamic_pile(
                m, sc, space_id, cands, X, T, step_min=step_min,
                window_start_min=window_start_min,
                fixed_proposals=fixed_optimized_proposals)
            dynamic_blend_decisions[space_id] = decision
            blend_target_vars_by_space[space_id] = decision["targets"]
            blend_matrix_selection_vars_by_space[space_id] = decision["selectors"]
            blend_component_use_terms.extend(decision["used"].values())
            variable_blend_space_ids.add(space_id)
            slacks, deviations, by_destination = dynamic_blending.add_receiving_quality(
                m, sc, space_id, decision, cands, X, T, opts.horizon_offset,
                demand_specs_at, QSCALE)
            blend_quality_slack_terms.extend(slacks)
            blend_target_deviation_terms.extend(deviations)
            for destination_id, values in by_destination.items():
                blend_quality_slacks_by_receiving_space[destination_id, space_id] = values
            continue
        ready_minute = chevron_ready_minute(sc, space_id)
        stack_indices = [
            ci for ci, cand in enumerate(cands)
            if cand.display_kind() == "stack" and cand.output_space() == space_id
        ]
        reclaim_indices = [
            ci for ci, cand in enumerate(cands)
            if cand.kind == "reclaim" and cand.space == space_id
        ]

        # A completed campaign remains a blend even when future construction
        # is disabled. It is closed to stacking and cannot be reclaimed before
        # the configured exact-minute availability boundary.
        if status == CAMPAIGN_COMPLETE:
            for ci in stack_indices:
                for t in range(T):
                    m.add(X[(ci, t)] == 0)
            reclaim_from = max(min(
                int(math.ceil((ready_minute - window_start_min)
                              / float(step_min))), T), 0)
            for ci in reclaim_indices:
                for t in range(reclaim_from):
                    m.add(X[(ci, t)] == 0)
            continue

        # A depleted campaign needs an explicit reset; a paused BUILDING
        # campaign preserves its material but schedules no further movement.
        if status == CAMPAIGN_DEPLETED or not construction_enabled:
            for ci in stack_indices + reclaim_indices:
                for t in range(T):
                    m.add(X[(ci, t)] == 0)
            continue

        prior = {
            str(grade_id): max(int(math.ceil(float(tonnage) - 1e-9)), 0)
            for grade_id, tonnage in dict(
                state.get("stacked_t_by_grade", {})).items()
        }
        has_physical_recipe = bool(state.get("resolved_recipe"))
        catalog_allocations = chevron_catalog_allocations(sc)
        catalog_selection = (
            chevron_mode(sc, space_id) == BLEND_MODE_MATRIX
            and len(catalog_allocations) > 1
            and not has_physical_recipe
            and not recipe)
        variable_composition = (
            not has_physical_recipe and (
                (chevron_mode(sc, space_id) == BLEND_MODE_OPTIMIZED
                 and not fixed_optimized_proposals)
                or catalog_selection))

        if variable_composition:
            if window_end_min < ready_minute:
                blend_model_errors.append(
                    f"{space_id}: unresolved blend composition requires schedule "
                    f"lookahead through ready minute {ready_minute}; this "
                    f"window ends at minute {window_end_min}.")
                continue
            batch_t = chevron_construction_batch_t(sc, space_id)
            routeable = {
                str(grade_id) for grade_id in
                routeable_by_space.get(space_id, ())}
            target_by_grade: Dict[str, object] = {}
            target_upper_by_grade: Dict[str, int] = {}
            used_by_grade: Dict[str, cp_model.IntVar] = {}
            if catalog_selection:
                selectors = {
                    str(entry["id"]): m.new_bool_var(
                        f"blend_matrix_{space_id}_{entry['id']}")
                    for entry in catalog_allocations
                }
                m.add(sum(selectors.values()) == 1)
                all_grades = sorted({
                    str(component["grade_id"])
                    for entry in catalog_allocations
                    for component in entry["components"]
                })
                for grade_id in all_grades:
                    tonnes_by_matrix = {
                        str(entry["id"]): next((
                            int(component["tonnage_t"])
                            for component in entry["components"]
                            if str(component["grade_id"]) == grade_id), 0)
                        for entry in catalog_allocations
                    }
                    upper = max(tonnes_by_matrix.values(), default=0)
                    target = m.new_int_var(
                        0, upper, f"blend_t_{space_id}_{grade_id}")
                    m.add(target == sum(
                        tonnes_by_matrix[matrix_id] * selector
                        for matrix_id, selector in selectors.items()))
                    if grade_id not in routeable:
                        m.add(target == 0)
                    target_by_grade[grade_id] = target
                    target_upper_by_grade[grade_id] = upper
                blend_matrix_selection_vars_by_space[space_id] = selectors
            else:
                bounds = chevron_component_bounds(sc, space_id)
                for component in bounds:
                    grade_id = str(component["grade_id"])
                    minimum = float(component.get("min_fraction", 0.0))
                    maximum = float(component.get("max_fraction", 1.0))
                    lower = max(
                        int(math.ceil(minimum * batch_t - 1e-9)),
                        prior.get(grade_id, 0), 0)
                    upper = min(
                        int(math.floor(maximum * batch_t + 1e-9)), batch_t)
                    if grade_id not in routeable:
                        upper = 0
                    grade_indices = [
                        ci for ci in stack_indices
                        if cands[ci].grade == grade_id]
                    executable_minimum = min((
                        max(itn(sc.machine(
                            cands[ci].machine).rate_min_tph * dt), 1)
                        for ci in grade_indices), default=1)
                    active_minimum = max(
                        int(math.ceil(chevron_minimum_component_fraction(
                            sc, space_id) * batch_t - 1e-9)),
                        executable_minimum, 1)
                    if upper < lower:
                        blend_model_errors.append(
                            f"{space_id}/{grade_id}: route availability, "
                            "carried tonnes and share bounds have no feasible "
                            "allocation.")
                        continue
                    target = m.new_int_var(
                        lower, upper, f"blend_t_{space_id}_{grade_id}")
                    used = m.new_bool_var(
                        f"blend_used_{space_id}_{grade_id}")
                    m.add(target <= batch_t * used)
                    m.add(target >= active_minimum * used)
                    target_by_grade[grade_id] = target
                    target_upper_by_grade[grade_id] = upper
                    used_by_grade[grade_id] = used
            unknown_prior = [
                grade_id for grade_id, tonnage in prior.items()
                if tonnage > 0 and grade_id not in target_by_grade]
            if unknown_prior:
                blend_model_errors.append(
                    f"{space_id}: carried stacking contains ineligible quality "
                    + ", ".join(sorted(unknown_prior)) + ".")
            if len(target_by_grade) < 2 or batch_t <= 0:
                blend_model_errors.append(
                    f"{space_id}: blending requires at least two "
                    "routeable qualities and a positive batch.")
                continue
            m.add(sum(target_by_grade.values()) == batch_t)
            if used_by_grade:
                m.add(sum(used_by_grade.values()) >= 2)
                blend_component_use_terms.extend(used_by_grade.values())
            blend_target_vars_by_space[space_id] = {
                grade_id: target for grade_id, target in target_by_grade.items()}
            variable_blend_space_ids.add(space_id)

            # Receiving limits constrain the selected stockpile-average quality
            # directly. Chemistry is dry-solids weighted; moisture is weighted
            # by physical wet-product tonnes. Soft deviations use the same
            # operator weights as normal destination-quality slack.
            result_grade = chevron_result_grade(sc, space_id)
            destinations = _reachable_receiving_specs(
                sc, space_id, result_grade)
            quality_slack_upper = max(batch_t * 20_000, 1)
            giveaway_upper = max(batch_t * 200, 1)
            for destination in destinations:
                priority = max(int(getattr(destination, "priority", 1)), 1)
                active_specs = []
                seen_specs = set()
                for bucket in range(T):
                    absolute_bucket = bucket + opts.horizon_offset
                    # A constructed blend must be receivable whenever it is
                    # later reclaimed; the quality contract is not conditional
                    # on this solve window containing positive demand.  Gating
                    # the recipe by demand allowed an idle-window campaign to
                    # freeze an arbitrary off-spec matrix that subsequent
                    # rolling windows could no longer repair.
                    for spec in demand_specs_at(
                            destination.id, absolute_bucket):
                        signature = (
                            spec.param, spec.pmin, spec.pmax, spec.target,
                            spec.hard, spec.averaging_h, spec.margin)
                        if signature not in seen_specs:
                            active_specs.append(spec)
                            seen_specs.add(signature)
                for spec in active_specs:
                    dry_coefficients: Dict[str, int] = {}
                    grade_values: Dict[str, int] = {}
                    missing: List[str] = []
                    for grade_id, target in target_by_grade.items():
                        if target_upper_by_grade[grade_id] <= 0:
                            continue
                        grade = sc.grade(grade_id)
                        if grade is None or spec.param not in grade.quality:
                            missing.append(grade_id)
                            continue
                        if spec.param == "moisture":
                            dry_fraction = 1.0
                        else:
                            moisture = getattr(
                                grade, "product_moisture_pct", None)
                            if moisture is None:
                                moisture = grade.quality.get("moisture", 0.0)
                            dry_fraction = max(
                                1.0 - float(moisture or 0.0) / 100.0, 0.0)
                        dry_coefficient = int(round(
                            dry_fraction * blend_dry_scale))
                        dry_coefficients[grade_id] = dry_coefficient
                        grade_values[grade_id] = int(round(
                            float(grade.quality[spec.param])
                            * blend_quality_scale * dry_coefficient))
                    if missing:
                        blend_model_errors.append(
                            f"{space_id}: {destination.id} constrains "
                            f"{spec.param}, missing on "
                            + ", ".join(sorted(missing)) + ".")
                        continue
                    denominator = sum(
                        dry_coefficients[grade_id] * target_by_grade[grade_id]
                        for grade_id in dry_coefficients)
                    lhs = sum(
                        grade_values[grade_id] * target_by_grade[grade_id]
                        for grade_id in grade_values)
                    if spec.pmin is not None:
                        bound = int(round(
                            (float(spec.pmin) + float(spec.margin))
                            * blend_quality_scale))
                        if spec.hard:
                            m.add(lhs >= bound * denominator)
                        else:
                            slack = m.new_int_var(
                                0, quality_slack_upper,
                                f"blend_qmin_{space_id}_{destination.id}_"
                                f"{spec.param}")
                            m.add(lhs + blend_dry_scale * slack
                                  >= bound * denominator)
                            blend_quality_slack_terms.append((priority, slack))
                            blend_quality_slacks_by_receiving_space.setdefault((destination.id, space_id), []).append(slack)
                    if spec.pmax is not None:
                        bound = int(round(max(
                            float(spec.pmax) - float(spec.margin), 0.0)
                            * blend_quality_scale))
                        if spec.hard:
                            m.add(lhs <= bound * denominator)
                        else:
                            slack = m.new_int_var(
                                0, quality_slack_upper,
                                f"blend_qmax_{space_id}_{destination.id}_"
                                f"{spec.param}")
                            m.add(lhs <= bound * denominator
                                  + blend_dry_scale * slack)
                            blend_quality_slack_terms.append((priority, slack))
                            blend_quality_slacks_by_receiving_space.setdefault((destination.id, space_id), []).append(slack)
                    if spec.param == "BPL" and spec.target is not None:
                        target_quality = int(round(
                            float(spec.target) * blend_quality_scale))
                        deviation = m.new_int_var(
                            0, giveaway_upper,
                            f"blend_target_{space_id}_{destination.id}_"
                            f"{spec.param}")
                        # Positive BPL giveaway is in whole quality-unit tonnes,
                        # matching the ordinary w4 term's physical scale.
                        normalizer = blend_dry_scale * blend_quality_scale
                        m.add(normalizer * deviation
                              >= lhs - target_quality * denominator)
                        blend_target_deviation_terms.append(
                            (priority, deviation))
        else:
            if len(recipe) < 2 or not allocation:
                continue
            target_by_grade = {
                str(component["grade_id"]): int(component["tonnage_t"])
                for component in allocation}

        # A construction bucket is admitted only when it finishes no later
        # than the exact ready minute. Reclaim starts on the first bucket whose
        # start is at or after that minute.
        construction_end = max(min(
            int(math.floor((ready_minute - window_start_min)
                           / float(step_min))), T), 0)
        reclaim_from_exact = max(min(
            int(math.ceil((ready_minute - window_start_min)
                          / float(step_min))), T), 0)
        crosses_ready = window_end_min >= ready_minute
        for grade_id, target_t in target_by_grade.items():
            grade_indices = [
                ci for ci in stack_indices if cands[ci].grade == grade_id]
            built_here = sum(
                X[(ci, t)] for ci in grade_indices
                for t in range(construction_end))
            prior_t = prior.get(grade_id, 0)
            m.add(prior_t + built_here <= target_t)
            minimum_future_run = min((
                max(itn(sc.machine(cands[ci].machine).rate_min_tph * dt), 1)
                for ci in grade_indices), default=1)
            residual = m.new_int_var(
                0, chevron_construction_batch_t(sc, space_id),
                f"blend_remaining_{space_id}_{grade_id}")
            finished_here = m.new_bool_var(
                f"blend_finished_{space_id}_{grade_id}")
            m.add(residual == target_t - prior_t - built_here)
            m.add(residual == 0).only_enforce_if(finished_here)
            m.add(residual >= minimum_future_run).only_enforce_if(
                finished_here.Not())
            if crosses_ready:
                m.add(prior_t + built_here == target_t)
            else:
                # Linear cumulative progress requirement. Unlike a pre-solved
                # recipe, the selected target remains free to adapt while no
                # physical stacking has frozen it.
                elapsed_to_window_end = min(
                    max(window_end_min, 0), ready_minute)
                m.add(ready_minute * (prior_t + built_here)
                      >= elapsed_to_window_end * target_t)
        target_grades = set(target_by_grade)
        for ci in stack_indices:
            if cands[ci].grade not in target_grades:
                for t in range(T):
                    m.add(X[(ci, t)] == 0)
                continue
            for t in range(construction_end, T):
                m.add(X[(ci, t)] == 0)
        # An incomplete campaign is never reclaimed in a myopic pre-deadline
        # window. A solve crossing readiness can reclaim from the first safe
        # grid boundary after exact grade-specific completion.
        reclaim_from = reclaim_from_exact if crosses_ready else T
        for ci in reclaim_indices:
            for t in range(reclaim_from):
                m.add(X[(ci, t)] == 0)

    if blend_model_errors:
        return PlanResult(
            status="ERROR", solver="cp-sat-blend-schedule",
            horizon_offset=opts.horizon_offset,
            reasons=["BLEND_SCHEDULE " + message
                     for message in blend_model_errors])

    # A dispatch near a rolling/window boundary may arrive after this model's
    # last bucket.  Such terminal in-transit material is legitimate only when
    # the receiving yard has at least one compatible stacking candidate for
    # its grade.  Otherwise a daily solve can hide an impossible grade beyond
    # its boundary and force the next solve to report emergency surge (for
    # example BT-LOW-CD sent to an M16 yard configured only for BT/TBT/MT).
    # In-horizon arrivals already have the stronger stack-conservation rule
    # below; this closes only the terminal blind spot.
    stackable_exit_grades = {
        (candidate.exit_node, candidate.grade)
        for candidates_at_exit in stack_by_exit.values()
        for candidate in candidates_at_exit
    }
    for ci, cand in enumerate(cands):
        if cand.kind != "dispatch" or (
                cand.exit_node, cand.grade) in stackable_exit_grades:
            continue
        try:
            lag = max(int(sc.link(cand.dest_id).transit_buckets), 0)
        except StopIteration:
            lag = 0
        for t in range(max(T - lag, 0), T):
            m.add(X[(ci, t)] == 0)

    # A route candidate that belongs to a receiving demand but carries the
    # wrong campaign grade must not run as an uncredited flow.  Without these
    # guards it could still consume inventory (or earn utilisation reward)
    # while being absent from the delivered-demand expression.
    for ci, cand in enumerate(cands):
        relevant_destinations = [
            destination for destination in sc.destinations
            if _candidate_serves_destination(
                sc, cand, destination, requested_grade="")
        ]
        if not relevant_destinations:
            continue
        for t in range(T):
            if not any(
                    ci in demand_cis_by_bucket[(destination.id, t)]
                    for destination in relevant_destinations):
                m.add(X[(ci, t)] == 0)
    # Carry fractional process output inside each native physical window.
    # This makes one hourly transfer and four 15-minute decisions produce the
    # same whole-tonne output instead of losing up to one rounding tonne in
    # every fine bucket.
    if yield_carry_buckets:
        for ci, (ycoef, yscale) in process_yield_coeff.items():
            for block_start in range(0, T, yield_carry_buckets):
                block_end = min(block_start + yield_carry_buckets, T)
                for t in range(block_start, block_end):
                    cumulative_x = sum(
                        X[(ci, u)] for u in range(block_start, t + 1))
                    cumulative_y = sum(
                        Y[(ci, u)] for u in range(block_start, t + 1))
                    m.add(yscale * cumulative_y <= ycoef * cumulative_x)
                    m.add(yscale * cumulative_y >=
                          ycoef * cumulative_x - (yscale - 1))

    # Hierarchical mixed-resolution coupling.  The hourly tactical plan owns
    # route/material quantities; this model owns their sub-hour placement.
    # Every candidate/path gets one exact mass total per native window,
    # including an explicit zero when the tactical plan did not select it.
    hierarchical_rows = sc.config.get(
        "MIXED_PREFIX_NATIVE_FLOW_TARGETS", None)
    hierarchical_target_count = 0
    if isinstance(hierarchical_rows, list):
        window_buckets = max(int(sc.config.get(
            "MIXED_PREFIX_NATIVE_WINDOW_BUCKETS", 1) or 1), 1)
        absolute_start_bucket = int(sc.config.get(
            "MIXED_PREFIX_ABSOLUTE_START_BUCKET", 0) or 0)
        block_count = (T + window_buckets - 1) // window_buckets
        target_groups: List[Tuple[Tuple[int, ...], int, int]] = []
        covered_candidate_blocks: set[Tuple[int, int]] = set()

        def _row_matches_candidate(row: dict, cand: Cand) -> bool:
            if str(row.get("machine", "")) != cand.machine or \
                    str(row.get("kind", "")) != cand.display_kind() or \
                    str(row.get("space", "")) != cand.space:
                return False
            transfer_id = str(row.get("transfer_id", "") or "")
            if transfer_id:
                if cand.transfer_id != transfer_id:
                    return False
            elif cand.kind == "process" or str(
                    row.get("dest_id", "") or "") != cand.dest_id:
                return False
            if cand.display_kind() == "stack" and str(
                    row.get("grade", "") or "") != cand.grade:
                return False
            if str(row.get("exit_node", "") or "") != cand.exit_node:
                return False
            return tuple(row.get("path", []) or []) == tuple(
                cand.path.links if cand.path else [])

        for row in hierarchical_rows:
            if not isinstance(row, dict):
                continue
            block = int(row.get("native_bucket", absolute_start_bucket)) \
                - absolute_start_bucket
            if not 0 <= block < block_count:
                continue
            matches = [
                ci for ci, cand in enumerate(cands)
                if _row_matches_candidate(row, cand)]
            if not matches:
                return PlanResult(
                    status="ERROR", solver="cp-sat",
                    reasons=[
                        "MIXED_PREFIX_TARGET_MATCH: tactical flow must match "
                        "at least one fine candidate (matched 0): "
                        f"{row}"])
            # Multiple candidates can be physically indistinguishable (for
            # example, duplicate stack alternatives that share the same exit
            # and path).  Couple their aggregate mass rather than arbitrarily
            # selecting one representation.
            match_tuple = tuple(matches)
            target_groups.append((
                match_tuple, block,
                itn(float(row.get("tonnage_t", 0) or 0))))
            covered_candidate_blocks.update(
                (ci, block) for ci in match_tuple)
            hierarchical_target_count += 1
        for matches, block, target in target_groups:
            t0 = block * window_buckets
            t1 = min(t0 + window_buckets, T)
            m.add(sum(X[(ci, t)] for ci in matches
                      for t in range(t0, t1)) == target)
        for ci in range(len(cands)):
            for block in range(block_count):
                if (ci, block) in covered_candidate_blocks:
                    continue
                t0 = block * window_buckets
                t1 = min(t0 + window_buckets, T)
                m.add(sum(X[(ci, t)] for t in range(t0, t1)) == 0)
    # per-group total flow: machine rate ceiling + min stable when active,
    # plus the configured smallest sensible parcel.
    #
    # Without a parcel floor the rate floor collapses to ``tot >= 1 t`` for
    # every machine whose ``rate_min_tph`` is zero — which is true of every
    # stacker in these scenarios.  The solver could therefore keep a group
    # "active" for one physical tonne in a whole hour and pay nothing for it,
    # and the minute overlay then drew a one-minute, one-tonne stacking run.
    # A parcel below the floor is still representable, because a constrained
    # plan can genuinely need one, but it must set ``short_parcel`` and pay
    # ``SHORT_PARCEL_PENALTY``; the count is exported, never discarded.
    #
    # Reify the exception in both directions. An active below-floor bucket
    # must set the flag, and a flagged bucket must actually be below floor;
    # otherwise a time-limited incumbent can report/price a phantom short
    # parcel even though the extracted tonnes meet the policy.
    short_parcel: Dict[Tuple[Tuple, int], object] = {}
    for gk, members in group_members.items():
        mach = sc.machine(gk[1])
        rf = opts.machine_rate_factor.get(gk[1], 1.0)
        policy = resolve_campaign_policy(sc, gk[1])
        stable_bucket_t = float(mach.rate_min_tph) * dt
        parcel_floor_t = policy.parcel_floor_t(stable_bucket_t)
        for t in range(T):
            absolute_bucket = t + opts.horizon_offset
            effective_machine_rate = sc.effective_resource_rate_tph(
                gk[1], mach.rate_max_tph * rf,
                bucket=absolute_bucket)
            hi = itn(effective_machine_rate * dt)
            lo = min(itn(mach.rate_min_tph * dt), hi)
            tot = sum(X[(ci, t)] for ci in members)
            m.add(tot <= hi * AG[(gk, t)])
            # The parcel floor can never exceed what the machine may move in
            # this bucket, or the group would simply be unusable.
            floor_t = min(max(itn(parcel_floor_t), lo), hi)
            absolute_floor = min(max(lo, itn(ABSOLUTE_MIN_PARCEL_T)), hi)
            if floor_t > absolute_floor and not policy.allow_short_parcel:
                # Hard floor: an active bucket must carry a full parcel. This
                # can cost service or feasibility, and the planner reports
                # that as unmet demand rather than concealing it.
                m.add(tot >= floor_t * AG[(gk, t)])
            elif floor_t > absolute_floor:
                short = m.new_bool_var(
                    f"shortparcel_{hash(gk) & 0xffffff}_{t}")
                m.add(short <= AG[(gk, t)])
                m.add(tot >= floor_t * AG[(gk, t)]
                      - (floor_t - absolute_floor) * short)
                m.add(tot >= absolute_floor * AG[(gk, t)])
                m.add(tot <= floor_t - 1).only_enforce_if(short)
                short_parcel[(gk, t)] = short
            elif lo > 0:
                m.add(tot >= lo * AG[(gk, t)])
            else:
                m.add(tot >= AG[(gk, t)])   # active implies non-zero flow

    # machine exclusivity + calendars + availability
    # Match the same boundaries used by job extraction. A minimum applies to
    # the accumulated run, so e.g. two consecutive 50 t buckets satisfy 100 t.
    job_signatures = {}
    if float(sc.config.get("MIN_JOB_VOLUME_T", 0.0)) > 0:
        for gk, members in group_members.items():
            for t in range(T):
                fixed = _fixed_for_bucket(sc, gk, t + opts.horizon_offset)
                job_signatures[(gk, t)] = (
                    tuple(round(_candidate_effective_capacity_tph(
                        sc, cands[ci], t + opts.horizon_offset,
                        opts.machine_rate_factor.get(gk[1], 1.0)), 9)
                          for ci in members), fixed.id if fixed else None)
    minimum_job_constraints = add_minimum_job_volume(
        m, groups=group_members, activity=AG, flows=X, horizon=T,
        minimum_t=sc.config.get("MIN_JOB_VOLUME_T", 0.0), signatures=job_signatures)
    mach_groups: Dict[str, List[Tuple]] = {}
    for gk in group_keys:
        mach_groups.setdefault(gk[1], []).append(gk)
    # Coverage is a secondary, demand-driven planning objective.  A machine
    # counts as used only when it is a real, available solver resource with at
    # least one feasible candidate and a positive-flow activity.  Reference,
    # future and planning-disabled display assets never enter this set.
    machine_used: Dict[str, cp_model.IntVar] = {}
    for mid, gks in mach_groups.items():
        machine = sc.machine(mid)
        if not machine.available or not getattr(machine, "planning_enabled", True):
            continue
        used = m.new_bool_var(f"used_{mid}")
        activities = [AG[(gk, t)] for gk in gks for t in range(T)]
        m.add(sum(activities) >= used)
        m.add(sum(activities) <= len(activities) * used)
        machine_used[mid] = used
    for mid, gks in mach_groups.items():
        for t in range(T):
            m.add(sum(AG[(gk, t)] for gk in gks) <= 1)
            if sc.machine_blocked(mid, t + opts.horizon_offset) or \
                    not sc.machine(mid).available:
                for gk in gks:
                    m.add(AG[(gk, t)] == 0)

    # Exact scheduled equipment events.  The previous implementation summed
    # per-space absolute differences, so one A->B change was reported and
    # penalised twice while a same-pile destination/grade change was missed.
    # These variables distinguish machine starts, machine stops and one
    # activity-group changeover.  Paths remain within an activity group and
    # are priced separately by the route-energy term.
    # An explicit HELD state: the machine stays running through a bucket
    # without moving material.  Without it the only way to bridge a bucket
    # without paying a stop and a later start was to command a token parcel,
    # and the relaxable parcel floor made exactly that the cheapest option —
    # the audited S0 week carried five 1 t "bridge" buckets in the middle of
    # otherwise substantial campaigns, and banning one of them simply moved
    # the trick elsewhere (5 -> 19 below-floor parcels). Holding is a physical
    # state an operator recognises, so the model now represents it directly
    # instead of disguising it as production.
    machine_hold: Dict[Tuple[str, int], object] = {}
    machine_material_on: Dict[Tuple[str, int], object] = {}
    machine_on: Dict[Tuple[str, int], object] = {}
    machine_state: Dict[Tuple[str, int], object] = {}
    machine_same_state: Dict[Tuple[str, int], object] = {}
    machine_starts: Dict[Tuple[str, int], object] = {}
    machine_stops: Dict[Tuple[str, int], object] = {}
    machine_changeovers: Dict[Tuple[str, int], object] = {}

    def add_rising_edge(name: str, current, previous):
        edge = m.new_bool_var(name)
        m.add(edge >= current - previous)
        m.add(edge <= current)
        m.add(edge <= 1 - previous)
        return edge

    def add_falling_edge(name: str, current, previous):
        edge = m.new_bool_var(name)
        m.add(edge >= previous - current)
        m.add(edge <= previous)
        m.add(edge <= 1 - current)
        return edge

    for mid, gks in mach_groups.items():
        # One compact state captures the mutually exclusive activity group:
        # 0 is idle and stable 1..N values identify this machine's groups.
        # This replaces one rising-edge Bool (and three constraints) per group
        # and bucket while preserving exact physical transition semantics.
        group_state = {gk: index + 1 for index, gk in enumerate(gks)}
        configured_previous = opts.initial_machine_groups.get(mid)
        previous_group = (
            tuple(configured_previous)
            if isinstance(configured_previous, (list, tuple))
            and configured_previous else None)
        # A carried group may be physically valid but absent from this
        # window's candidates.  Give it a distinct sentinel state so a return
        # to any currently modelled active group is exactly one changeover.
        previous_state: object = (
            group_state.get(previous_group, len(gks) + 1)
            if previous_group is not None else 0)
        previous_on: object = 1 if previous_group is not None else 0
        policy = resolve_campaign_policy(sc, mid)
        # Holding is only meaningful BETWEEN two material buckets of one
        # campaign, so it is never offered in the window's first bucket (there
        # is no in-window state to carry) and never while the machine is
        # blocked or unavailable.  ``max_hold_min == 0`` disables it entirely.
        bucket_hold_limit = int(policy.max_hold_min) // max(int(round(dt * 60)), 1)
        hold_allowed = bucket_hold_limit > 0
        for t in range(T):
            material_on = sum(AG[(gk, t)] for gk in gks)
            machine_material_on[(mid, t)] = material_on
            if (hold_allowed and t > 0
                    and not sc.machine_blocked(mid, t + opts.horizon_offset)
                    and sc.machine(mid).available):
                hold: object = m.new_bool_var(f"hold_{mid}_{t}")
                # A machine cannot be held and moving material at once.
                m.add(hold + material_on <= 1)
                m.add(hold <= previous_on)
            else:
                hold = 0
            machine_hold[(mid, t)] = hold
            current_on = material_on + hold
            machine_on[(mid, t)] = current_on
            current_state = m.new_int_var(
                0, len(gks), f"state_{mid}_{t}")
            if isinstance(hold, int):
                m.add(current_state == sum(
                    group_state[gk] * AG[(gk, t)] for gk in gks))
            else:
                # Working: the state is this bucket's group. Held: the machine
                # keeps the group it was already on, so a hold is neither a
                # changeover nor a stop.
                m.add(current_state == sum(
                    group_state[gk] * AG[(gk, t)] for gk in gks)
                      ).only_enforce_if(hold.Not())
                m.add(current_state == previous_state).only_enforce_if(hold)
            machine_state[(mid, t)] = current_state
            start = add_rising_edge(
                f"start_{mid}_{t}", current_on, previous_on)
            stop = add_falling_edge(
                f"stop_{mid}_{t}", current_on, previous_on)
            machine_starts[(mid, t)] = start
            machine_stops[(mid, t)] = stop
            same_state = m.new_bool_var(f"same_state_{mid}_{t}")
            m.add(current_state == previous_state).only_enforce_if(
                same_state)
            m.add(current_state != previous_state).only_enforce_if(
                same_state.Not())
            machine_same_state[(mid, t)] = same_state
            changeover = m.new_bool_var(f"changeover_{mid}_{t}")
            # The five exhaustive cases are idle->idle, same active group,
            # idle->active, active->idle and active A->B.  Exact start/stop
            # edges plus state equality therefore define one changeover iff
            # both buckets are active with different groups.
            m.add(start + stop + same_state + changeover == 1)
            machine_changeovers[(mid, t)] = changeover
            previous_state = current_state
            previous_on = current_on

        if hold_allowed:
            for t in range(T):
                # No trailing/infinite idle reservation; holding requires
                # later material and obeys the configured elapsed-time cap.
                later = [machine_material_on[(mid, u)]
                         for u in range(t + 1, min(t + bucket_hold_limit + 1, T))]
                hold = machine_hold[(mid, t)]
                if not isinstance(hold, int):
                    m.add(sum(later) >= hold)
                span = [machine_hold[(mid, u)]
                        for u in range(t, min(t + bucket_hold_limit + 1, T))]
                m.add(sum(span) <= bucket_hold_limit)

    # Minimum dwell: once a machine has entered an activity group it stays in
    # it for at least ``min_dwell_buckets`` buckets.  This is the campaign
    # length constraint; it is disabled by default (``1``) because no approved
    # campaign rule exists for this plant, and it is skipped wherever it could
    # conflict with a hard input — a calendar block, an unavailable machine or
    # the end of the window — so enabling it cannot manufacture infeasibility
    # out of the scenario's own contingencies.
    dwell_constraints = 0
    for mid, gks in mach_groups.items():
        policy = resolve_campaign_policy(sc, mid)
        span = max(int(policy.min_dwell_buckets), 1)
        if span <= 1:
            continue
        for t in range(T):
            horizon = range(t + 1, min(t + span, T))
            if not horizon:
                continue
            if any(sc.machine_blocked(mid, b + opts.horizon_offset)
                   for b in range(t, min(t + span, T))):
                continue
            for later in horizon:
                # Having just started or changed over at t, the machine may
                # neither stop nor change group again before the dwell ends.
                m.add(machine_starts[(mid, t)]
                      + machine_stops[(mid, later)] <= 1)
                m.add(machine_starts[(mid, t)]
                      + machine_changeovers[(mid, later)] <= 1)
                m.add(machine_changeovers[(mid, t)]
                      + machine_stops[(mid, later)] <= 1)
                m.add(machine_changeovers[(mid, t)]
                      + machine_changeovers[(mid, later)] <= 1)
                dwell_constraints += 4

    # Planned contingencies may address resources below or above a machine:
    # an entire site, one route link, or a process/destination line.  Apply
    # these to path-specific flow variables so an unaffected parallel route
    # remains usable while every candidate touching the blocked resource is
    # forced to zero.
    for ci, cand in enumerate(cands):
        for t in range(T):
            if _candidate_calendar_blocked(
                    sc, cand, t + opts.horizon_offset):
                m.add(X[(ci, t)] == 0)

    # corridor-feeder / route-confluence exclusivity (config data on Site):
    # at most ONE machine of each configured group active per bucket — either
    # feeder may be selected, both never run together until the confluence
    # configuration explicitly permits simultaneous partial-rate reclaim
    excl_groups: List[List[str]] = []
    for site in sc.sites:
        for grp in getattr(site, "exclusive_machine_groups", []) or []:
            grp_ = [mid for mid in grp if mid in mach_groups]
            if len(grp_) > 1:
                excl_groups.append(grp_)
    for gi, grp in enumerate(excl_groups):
        for t in range(T):
            m.add(sum(AG[(gk, t)] for mid in grp
                      for gk in mach_groups[mid]) <= 1)

    # banned tuples (operator rejections / replan exclusions)
    for (bm, bs, bd, b0, b1) in opts.banned:
        for gk in group_keys:
            if gk[1] == bm and (not bs or gk[2] == bs) and (not bd or gk[3] == bd):
                for t in range(max(b0 - opts.horizon_offset, 0),
                               min(b1 - opts.horizon_offset, T - 1) + 1):
                    m.add(AG[(gk, t)] == 0)

    # Reserve preparation in the current bucket after an adjacent pile change.
    # A full material bucket followed by another full bucket cannot also
    # contain translation/setup. Exact timing still chooses the minute slot.
    # Work cuts are extracted after solving: bound their possible midpoints
    # instead of pretending the two pile centres are known work locations.
    work_bounds: Dict[Tuple[str, str], Tuple[float, float]] = {}
    for gk in group_keys:
        mid, sid = gk[1], gk[2]
        space = sc.space(sid)
        midpoint = (space.x_from + space.x_to) / 2.0
        kind = cands[group_members[gk][0]].display_kind()
        bounds = ((midpoint, space.x_to) if kind == "stack"
                  else (space.x_from, midpoint + 0.05))
        if (mid, sid) in work_bounds:
            old = work_bounds[(mid, sid)]
            bounds = (min(old[0], bounds[0]), max(old[1], bounds[1]))
        for fixed in sc.fixed_jobs:
            if fixed.machine == mid and fixed.space == sid and \
                    fixed.start_repere is not None and fixed.stop_repere is not None:
                x0, x1 = repere_interval_to_x(space, fixed.start_repere, fixed.stop_repere)
                work_x = (x0 + x1) / 2.0
                bounds = (min(bounds[0], work_x), max(bounds[1], work_x))
        work_bounds[(mid, sid)] = bounds
    prep_cache: Dict[Tuple[str, str, str], int] = {}

    def preparation_minutes(mid: str, previous: str, current: str) -> int:
        if previous == current:
            return 0
        key = (mid, previous, current)
        if key not in prep_cache:
            prep_cache[key] = max(estimate_transition(
                sc.machine(mid), left, right, space_changed=True).total_min
                for left in work_bounds[(mid, previous)]
                for right in work_bounds[(mid, current)])
        return prep_cache[key]

    def prepared_mass_cap(gk: Tuple, t: int, preparation_min: int) -> int:
        cand = cands[group_members[gk][0]]
        rate = _candidate_effective_capacity_tph(
            sc, cand, t + opts.horizon_offset,
            opts.machine_rate_factor.get(cand.machine, 1.0))
        fixed = _fixed_for_bucket(sc, gk, t + opts.horizon_offset)
        if fixed is not None and fixed.rate_tph > 0:
            rate = min(rate, fixed.rate_tph)
        # Match extraction's effective ceiling and floor mass so its whole-
        # minute duration fits in the preparation-reduced physical window.
        rate = itn(rate * dt) / max(dt, 1e-9)
        return max(int(math.floor(rate * max(step_min - preparation_min, 0)
                                  / 60.0 + 1e-9)), 0)

    initial_spaces = sc.config.get("MACHINE_INITIAL_SPACE", {}) or {}
    initial_preparation: Dict[Tuple[str, str], int] = {}
    if isinstance(initial_spaces, dict):
        for (mid, sid), bounds in work_bounds.items():
            if mid in initial_spaces and str(initial_spaces[mid]) != sid:
                initial_preparation[(mid, sid)] = max(estimate_transition(
                    sc.machine(mid), sc.machine(mid).position_x, work_x,
                    space_changed=True).total_min for work_x in bounds)
    positioning_reservation_count = 0
    positioning_link_users = {}
    for ci, candidate in enumerate(cands):
        for link_id in candidate.path.links if candidate.path else ():
            positioning_link_users.setdefault(link_id, []).append(ci)
    positioning_shared_groups = graph.shared_groups()

    def reserve_positioning_route(gk, t, prep, condition):
        """Conservative coarse-bucket reservation for shared-route preparation.

        Bounding only the changed machine's tonnes is insufficient: a
        saturated shared route may force a full-hour concurrent fallback,
        erasing the machine's preparation gap. Reserve that route's elapsed
        time too. This is conservative, not a minute-scheduling optimum.
        """
        links = {link_id for ci in group_members[gk]
                 for link_id in (cands[ci].path.links if cands[ci].path else ())}
        resources = [(link_id, positioning_link_users[link_id],
                      sc.effective_resource_rate_tph(f"LINK:{link_id}", sc.link(link_id).capacity_tph,
                                                     bucket=t + opts.horizon_offset)) for link_id in sorted(links)]
        for name, shared_links in positioning_shared_groups.items():
            if links.intersection(shared_links):
                users = sorted({ci for lid in shared_links for ci in positioning_link_users.get(lid, ())})
                resources.append((name, users, min(sc.link(lid).capacity_tph for lid in shared_links)))
        count = 0
        for _name, users, rate in resources:
            # A private leg already has the machine mass cap above. The gap
            # matters here when another allocation can occupy this resource.
            if not any(ci not in group_members[gk] for ci in users):
                continue
            full = itn(rate * dt)
            prepared = max(int(math.floor(rate * max(step_min - prep, 0) / 60 + 1e-9)), 0)
            m.add(sum(X[(ci, t)] for ci in users) <= prepared + full * condition)
            count += 1
        return count

    for mid, gks in mach_groups.items():
        spaces = sorted({gk[2] for gk in gks})
        at = {}
        for s in spaces:
            for t in range(T):
                at[(s, t)] = sum(AG[(gk, t)] for gk in gks if gk[2] == s)
        for t in range(1, T):
            for gk in gks:
                current_total = sum(X[(ci, t)] for ci in group_members[gk])
                hi = itn(sc.machine(mid).rate_max_tph
                         * opts.machine_rate_factor.get(mid, 1.0) * dt)
                for previous_space in spaces:
                    if previous_space == gk[2]:
                        continue
                    prep = preparation_minutes(mid, previous_space, gk[2])
                    if prep <= 0:
                        continue
                    cap = prepared_mass_cap(gk, t, prep)
                    m.add(current_total <= cap + hi * (
                        2 - at[(previous_space, t - 1)] - AG[(gk, t)]))
                    positioning_reservation_count += 1
                    positioning_reservation_count += reserve_positioning_route(
                        gk, t, prep, 2 - at[(previous_space, t - 1)] - AG[(gk, t)])
        for gk in gks:
            prep = initial_preparation.get((mid, gk[2]), 0)
            if prep > 0:
                m.add(sum(X[(ci, 0)] for ci in group_members[gk])
                      <= prepared_mass_cap(gk, 0, prep) * AG[(gk, 0)])
                positioning_reservation_count += 1
                positioning_reservation_count += reserve_positioning_route(gk, 0, prep, 1 - AG[(gk, 0)])

    # ---------- corridor arrivals (transport delay, eq. 5) --------------------
    delayed_links = [l for l in sc.links if l.transit_buckets > 0]
    arrivals: Dict[Tuple[str, str, int], object] = {}   # (exit_node, grade, t)
    controlled_arrivals: Dict[Tuple[str, str, int], object] = {}
    for l in delayed_links:
        L = l.transit_buckets
        for g in (list(sc.config["_WASHING_MODEL_CATALOG"]) if washing.enabled(sc)
                  else [gr.id for gr in sc.grades
                        if not getattr(gr, "is_specification", False)] or [""]):
            for t in range(T):
                terms = []
                const = 0
                for e in sc.belt_profile:
                    if e.link_id == l.id and (e.material_id if washing.enabled(sc) else e.grade) == g and \
                            e.arrive_bucket - opts.horizon_offset == t:
                        const += itn(e.mass_t)
                td = t - L
                if td >= 0:
                    for ci, c in enumerate(cands):
                        if c.kind == "dispatch" and c.dest_id == l.id and \
                                (c.material_id if c.material_id else sc.space(c.space).grade) == g:
                            terms.append(X[(ci, td)])
                if terms or const:
                    contribution = (sum(terms) + const) if terms else const
                    key = (l.dst, g, t)
                    # C1 and C2 converge on the same M16 reception tower.
                    # Accumulate both corridors; assigning by key here used to
                    # overwrite the first corridor with the second.
                    arrivals[key] = arrivals.get(key, 0) + contribution
                    if terms:
                        controlled = sum(terms)
                        controlled_arrivals[key] = \
                            controlled_arrivals.get(key, 0) + controlled

    # arrivals must be stacked (mass conservation at the receiving tower);
    # any un-stackable arrival goes to a heavily-penalised surge slack
    surge_slacks: Dict[int, List] = {t: [] for t in range(T)}
    surge_map: Dict[Tuple[str, str, int], object] = {}
    grades_all = (list(sc.config["_WASHING_MODEL_CATALOG"]) if washing.enabled(sc)
                  else [gr.id for gr in sc.grades
                        if not getattr(gr, "is_specification", False)] or [""])
    for exit_node in stack_by_exit:
        for g in grades_all:
            for t in range(T):
                arr = arrivals.get((exit_node, g, t), 0)
                stack_terms = [X[(ci, t)] for ci, c in enumerate(cands)
                               if c.kind == "stack" and c.exit_node == exit_node
                               and (c.material_id or c.grade) == g]
                controlled = controlled_arrivals.get((exit_node, g, t), 0)
                if not (isinstance(controlled, int) and controlled == 0):
                    # A dispatch selected by this plan is controllable and may
                    # leave its source only when the receiving yard can stack
                    # it on arrival.  Emergency surge remains available for
                    # material that was already in transit at the planning
                    # boundary, but never as an alternative to scheduling the
                    # matching receiving operation.
                    m.add(sum(stack_terms) >= controlled)
                if isinstance(arr, int) and arr == 0:
                    for st_ in stack_terms:
                        m.add(st_ == 0)
                else:
                    sv = m.new_int_var(0, 10 ** 7, f"sg_{exit_node}_{g}_{t}")
                    m.add(sum(stack_terms) + sv == arr)
                    surge_slacks[t].append(sv)
                    surge_map[(exit_node, g, t)] = sv

    # ---------- inventory mass balance (eq. 1) --------------------------------
    inv: Dict[Tuple[str, int], cp_model.IntVar] = {}
    spill: List[cp_model.IntVar] = []
    spill_map: Dict[Tuple[str, int], object] = {}
    no_passthrough_spaces = set(
        sc.config.get("PROCESS_NO_PASSTHROUGH_SPACES", []) or [])
    if washing.enabled(sc):
        # Typed material cannot be received and withdrawn from a pile in the
        # same planning bucket. Minute execution otherwise has no causal order
        # that can guarantee the planned stream exists before its reclaim.
        no_passthrough_spaces.update(space.id for space in sc.spaces)
    for sp in sc.spaces:
        cap = itn(sp.capacity_t)
        for t in range(T + 1):
            inv[(sp.id, t)] = m.new_int_var(0, cap, f"inv_{sp.id}_{t}")
        m.add(inv[(sp.id, 0)] == min(itn(sp.mass_t), cap))
        for t in range(T):
            outs = [X[(ci, t)] for ci, c in enumerate(cands)
                    if c.input_space() == sp.id]
            ins = [
                (Y[(ci, t)] if c.kind == "process" else X[(ci, t)])
                for ci, c in enumerate(cands)
                if c.output_space() == sp.id
            ]
            exo_in = exo_out = 0
            for e in sc.exogenous:
                if e.start_bucket <= t + opts.horizon_offset <= e.end_bucket:
                    if e.target_space == sp.id:
                        exo_in += itn(e.rate_tph * dt)
                    if e.source_space == sp.id:
                        exo_out += itn(e.rate_tph * dt)
            sv = m.new_int_var(0, 10 ** 7, f"spill_{sp.id}_{t}")
            spill.append(sv)
            spill_map[(sp.id, t)] = sv
            if not bool(sc.config.get("ALLOW_CONTROLLED_FLOW_SPILL", True)):
                # Only material entering from outside the commanded model may
                # use emergency overflow.  A controlled stack/process flow is
                # a decision and must instead respect storage capacity.
                m.add(sv <= max(exo_in, 0))
            if sp.id in no_passthrough_spaces:
                # Material produced during bucket t becomes available at the
                # next boundary; downstream equipment may consume only the
                # start-of-bucket buffer.  This prevents an hourly model from
                # implying output before the configured sub-hour residence.
                m.add(sum(outs) <= inv[(sp.id, t)])
            m.add(inv[(sp.id, t + 1)] ==
                  inv[(sp.id, t)] + sum(ins) + exo_in - sum(outs) - exo_out - sv)

    # Grade-resolved balances sit UNDER the existing physical inventory.
    # Modes/grades do not duplicate pile, route or equipment capacities.
    material_inv = {}
    material_spill = {}
    if washing.enabled(sc):
        catalogue = sc.config["_WASHING_MODEL_CATALOG"]
        seeds = sc.config["_WASHING_MODEL_INITIAL"]
        possible = sc.config["_WASHING_MODEL_POSSIBLE"]
        exogenous_material = sc.config["_WASHING_MODEL_EXOGENOUS"]
        for sp in sc.spaces:
            # Constructed chevrons retain their existing recipe/completion
            # constraints; their average is not selectively reclaimable lots.
            if sp.id in managed_blend_spaces:
                continue
            mids = possible[sp.id]
            for mid in mids:
                for t in range(T + 1):
                    material_inv[(sp.id, mid, t)] = m.new_int_var(
                        0, itn(sp.capacity_t), f"material_{sp.id}_{mid}_{t}")
                m.add(material_inv[(sp.id, mid, 0)] == itn(seeds[sp.id].get(mid, 0)))
            for t in range(T + 1):
                m.add(sum(material_inv[(sp.id, mid, t)] for mid in mids) == inv[(sp.id, t)])
            for t in range(T):
                spill_parts = []
                for mid in mids:
                    outs = [X[(ci, t)] for ci, c in enumerate(cands)
                            if c.input_space() == sp.id and c.material_id == mid]
                    ins = [Y[(ci, t)] if c.kind == "process" else X[(ci, t)]
                           for ci, c in enumerate(cands)
                           if c.output_space() == sp.id and c.output_material_id == mid]
                    exo_in = exo_out = 0
                    for index, flow in enumerate(sc.exogenous):
                        if (flow.start_bucket <= t + opts.horizon_offset <= flow.end_bucket
                                and exogenous_material[str(index)] == mid):
                            if flow.target_space == sp.id:
                                exo_in += itn(flow.rate_tph * dt)
                            if flow.source_space == sp.id:
                                exo_out += itn(flow.rate_tph * dt)
                    part = m.new_int_var(0, 10 ** 7, f"material_spill_{sp.id}_{mid}_{t}")
                    material_spill[(sp.id, mid, t)] = part
                    spill_parts.append(part)
                    opening = material_inv[(sp.id, mid, t)]
                    if sp.id in no_passthrough_spaces:
                        m.add(sum(outs) <= opening)
                    m.add(material_inv[(sp.id, mid, t + 1)] ==
                          opening + sum(ins) + exo_in - sum(outs) - exo_out - part)
                m.add(sum(spill_parts) == spill_map[(sp.id, t)])
        # A washing-line dry-feed ceiling is shared by all its blend inputs,
        # not independently granted to each candidate/grade.
        transfers = {transfer.id: transfer for transfer in sc.process_transfers}
        for gk, members in group_members.items():
            first = cands[members[0]]
            if not first.treatment_id:
                continue
            cap = float(transfers[first.transfer_id].dry_feed_capacity_tph or 0)
            if cap:
                for t in range(T):
                    m.add(sum(itn(1000 * (1 - cands[ci].quality_override["moisture"] / 100))
                              * X[(ci, t)] for ci in members) <= itn(cap * dt * 1000))

    # Process-interface buffers may pass material within a native planning
    # bucket, but may not hide carry-over between buckets.  The exact
    # sub-hour ordering is validated by the minute dependency scheduler.
    zero_carry_interval = max(int(sc.config.get(
        "PROCESS_ZERO_CARRY_WINDOW_BUCKETS", 1) or 1), 1)
    for sid in sc.config.get("PROCESS_ZERO_CARRY_SPACES", []) or []:
        if any(sp.id == sid for sp in sc.spaces):
            for t in range(0, T + 1, zero_carry_interval):
                m.add(inv[(sid, t)] == 0)

    # Transparent terminal inventory targets.  S0 uses these as replenishment
    # purposes for stocked routes; they are configuration data, never implicit
    # optimizer behaviour.
    # A terminal minimum is HARD by default. Spaces listed in
    # ``TERMINAL_INVENTORY_MIN_SOFT_SPACES`` instead get a penalised shortfall,
    # which expresses "restock to here when it costs no served demand": a hard
    # floor outranks demand and, against a supply-limited week, simply makes
    # the model INFEASIBLE rather than trading off sensibly.
    terminal_min_shortfall: Dict[str, cp_model.IntVar] = {}
    soft_minimum_spaces = set(
        sc.config.get("TERMINAL_INVENTORY_MIN_SOFT_SPACES", []) or [])
    for sid, target in (
            sc.config.get("TERMINAL_INVENTORY_MIN_T", {}) or {}).items():
        if not any(sp.id == sid for sp in sc.spaces):
            continue
        floor_t = itn(float(target))
        if sid in soft_minimum_spaces:
            deficit = m.new_int_var(
                0, max(floor_t, 1), f"terminal_min_shortfall_{sid}")
            m.add(inv[(sid, T)] + deficit >= floor_t)
            terminal_min_shortfall[sid] = deficit
        else:
            m.add(inv[(sid, T)] >= floor_t)
    for sid, target in (
            sc.config.get("TERMINAL_INVENTORY_EXACT_T", {}) or {}).items():
        if any(sp.id == sid for sp in sc.spaces):
            m.add(inv[(sid, T)] == itn(float(target)))

    # Explicit process-output campaign targets (e.g. Louta L1/L2 stocked
    # route contribution). They give washing/stacking work a real planning
    # purpose and make the policy auditable in Configure. "Target" is exact:
    # allowing an unlabelled excess would create work with no declared purpose.
    stage_target_shortfall: Dict[str, cp_model.IntVar] = {}
    planning_stages = set(sc.config.get("PLANNING_PROCESS_STAGE_TARGETS", []))
    planning_corridors = set(sc.config.get("PLANNING_CORRIDOR_TARGETS", []))
    planning_stage_shortfall = {}
    planning_corridor_shortfall = {}
    stage_shortfall_limits = dict(sc.config.get(
        "PROCESS_STAGE_TARGET_SHORTFALL_MAX_T", {}) or {})
    for stage, target in effective_process_stage_targets(sc).items():
        stage_outputs = [
            Y[(ci, t)]
            for ci, cand in enumerate(cands)
            if cand.kind == "process" and cand.process_stage == stage
            for t in range(T)
        ]
        if stage_outputs:
            target_t = itn(float(target))
            if stage in planning_stages:
                deficit = m.new_int_var(0, target_t, f"planning_stage_shortfall_{stage}")
                m.add(sum(stage_outputs) + deficit == target_t)
                planning_stage_shortfall[stage] = deficit
                continue
            shortfall_cap = min(max(itn(float(
                stage_shortfall_limits.get(stage, 0) or 0)), 0), target_t)
            if shortfall_cap:
                deficit = m.new_int_var(
                    0, shortfall_cap, f"stage_target_shortfall_{stage}")
                m.add(sum(stage_outputs) + deficit == target_t)
                stage_target_shortfall[stage] = deficit
            else:
                m.add(sum(stage_outputs) == target_t)

    # Process-stage residence is a material-availability rule, not merely a
    # post-solve display offset.  A transfer produced during native bucket u is
    # conservatively available after that bucket has closed plus the declared
    # residence delay.  The minute overlay may then place the dependent work
    # anywhere inside the first eligible bucket without consuming immature
    # tonnes.  Initial source inventory is treated as already mature at t0.
    dependency_groups: Dict[Tuple[str, str, str], List[int]] = {}
    for ci, cand in enumerate(cands):
        if cand.depends_on_stage:
            dependency_groups.setdefault(
                (cand.depends_on_stage, cand.source_space, cand.material_id), []).append(ci)
    bucket_width_min = max(itn(dt * 60), 1)
    for (stage, source_space, material_id), dep_cis in dependency_groups.items():
        upstream_cis = [
            ci for ci, cand in enumerate(cands)
            if cand.kind == "process"
            and cand.process_stage == stage
            and cand.target_space == source_space
            and (not material_id or cand.output_material_id == material_id)
        ]
        if not upstream_cis:
            continue
        delay_min = max(
            max(int(cands[ci].dependency_delay_min), 0)
            for ci in dep_cis)
        lag_buckets = 1 + (
            delay_min + bucket_width_min - 1) // bucket_width_min
        initial_mature_t = (itn(sc.config["_WASHING_MODEL_INITIAL"][source_space].get(material_id, 0))
                            if material_id else itn(sc.space(source_space).mass_t))
        for t in range(T):
            opening_ready = initial_mature_t
            if material_id:
                start_minute = (opts.horizon_offset + t) * bucket_width_min
                pending = sc.config.get("_WASHING_MODEL_PENDING", {}).get(source_space, {}).get(material_id, [])
                opening_ready -= sum(itn(part["tonnage_t"]) for part in pending
                                     if part["ready_minute"] > start_minute)
            consumed = sum(
                X[(ci, u)] for ci in dep_cis for u in range(t + 1))
            last_mature_bucket = t - lag_buckets
            produced = (
                sum(
                    Y[(ci, u)]
                    for ci in upstream_cis
                    for u in range(last_mature_bucket + 1))
                if last_mature_bucket >= 0 else 0)
            m.add(consumed <= max(opening_ready, 0) + produced)

    # Corridor/source targets are exact for the same reason: a tonne above the
    # configured stocked-route contribution would be an undeclared movement.
    for link_id, target in effective_corridor_targets(sc).items():
        dispatch_terms = [
            X[(ci, t)]
            for ci, cand in enumerate(cands)
            if cand.kind == "dispatch" and cand.dest_id == link_id
            for t in range(T)
        ]
        if dispatch_terms:
            if link_id in planning_corridors:
                target_t = itn(float(target))
                deficit = m.new_int_var(0, target_t, f"planning_corridor_shortfall_{link_id}")
                m.add(sum(dispatch_terms) + deficit == target_t)
                planning_corridor_shortfall[link_id] = deficit
            else:
                m.add(sum(dispatch_terms) == itn(float(target)))

    # Optional steady-state replenishment policy for the three-site
    # demonstration.  It restores the M16 *supply position* rather than
    # creating arbitrary mine movements:
    #
    #   terminal M16 stock + terminal corridor in-transit
    #       <= initial M16 stock + initial corridor in-transit.
    #
    # Therefore every mine dispatch is justified by downstream consumption.
    # Material dispatched too late to arrive is retained explicitly as
    # terminal in-transit instead of disappearing beyond the horizon.
    terminal_supply_max = sc.config.get(
        "M16_TERMINAL_SUPPLY_POSITION_MAX_T")
    terminal_in_transit_terms = []
    terminal_initial_belt_t = 0
    terminal_supply_expr = None
    if terminal_supply_max is not None:
        mph_spaces = [sp for sp in sc.spaces if sp.site == "MPH"]
        for ci, c in enumerate(cands):
            if c.kind != "dispatch":
                continue
            try:
                lag = sc.link(c.dest_id).transit_buckets
            except StopIteration:
                continue
            for t in range(T):
                if t + lag >= T:
                    terminal_in_transit_terms.append(X[(ci, t)])
        terminal_initial_belt_t = sum(
            itn(e.mass_t) for e in sc.belt_profile
            if e.arrive_bucket - opts.horizon_offset >= T
        )
        terminal_supply_expr = (
            sum(inv[(sp.id, T)] for sp in mph_spaces)
            + sum(terminal_in_transit_terms)
            + terminal_initial_belt_t
        )
        m.add(terminal_supply_expr <= itn(float(terminal_supply_max)))

    # ---------- demand with unmet slack (eq. 4) --------------------------------
    unmet: Dict[Tuple[str, int], cp_model.IntVar] = {}
    delivered: Dict[Tuple[str, int], object] = {}
    for d in sc.destinations:
        expressions = []
        demands = []
        for t in range(T):
            dem = itn(demand_rate_at(
                d.id, t + opts.horizon_offset) * dt)
            cis = demand_cis_by_bucket[(d.id, t)]
            expr = sum(X[(ci, t)] for ci in cis)
            delivered[(d.id, t)] = expr
            expressions.append(expr)
            demands.append(max(dem, 0))
        if getattr(d, "demand_kind", "delivery") == "process_feed":
            # A washing campaign is an exact DAILY feed request (apart from
            # explicit unmet slack).  The
            # optimiser remains free to choose operating hours inside each
            # civil day, but work cannot be borrowed from a different day (or
            # a different requested grade) in a weekly plan.
            campaigns: Dict[Tuple[str, str, int], List[int]] = {}
            for t in range(T):
                absolute_bucket = t + opts.horizon_offset
                key = (
                    sc.sim.bucket_dt(absolute_bucket).date().isoformat(),
                    demand_grade_at(d.id, absolute_bucket),
                    demand_priority_at(d.id, absolute_bucket),
                )
                campaigns.setdefault(key, []).append(t)
            for campaign_index, tids in enumerate(campaigns.values()):
                total_demand = sum(demands[t] for t in tids)
                if total_demand <= 0:
                    m.add(sum(expressions[t] for t in tids) == 0)
                    for t in tids:
                        unmet[(d.id, t)] = 0
                    continue
                first_t = tids[0]
                u = m.new_int_var(
                    0, total_demand,
                    f"u_{d.id}_campaign_{campaign_index}")
                m.add(sum(expressions[t] for t in tids) + u == total_demand)
                for t in tids:
                    unmet[(d.id, t)] = u if t == first_t else 0
        else:
            for t, (expr, dem) in enumerate(zip(expressions, demands)):
                u = m.new_int_var(0, dem, f"u_{d.id}_{t}")
                # Final-unit demand is exact: no unrequested over-delivery.
                m.add(expr + u == dem)
                unmet[(d.id, t)] = u

    # ---------- policy-scoped continuous-feed interruptions -----------------
    # A gap is exact, not a loose implication: when demand is active it is 1
    # iff delivered physical mass is below the policy threshold.  Explicit
    # policy modes keep batch deliveries and flexible process campaigns out of
    # a KPI/objective whose operational meaning applies only to continuous
    # receiving plants.
    feed_gap: Dict[Tuple[str, int], object] = {}
    feed_episode_start: Dict[Tuple[str, int], object] = {}
    for destination_id, policy in continuity_policies.items():
        if not policy.scored:
            continue
        previous_gap: object = int(bool(
            opts.initial_feed_gap.get(destination_id, False)))
        gap_series = []
        for t in range(T):
            demand_tph = max(demand_rate_at(
                destination_id, t + opts.horizon_offset), 0.0)
            demand_t = max(itn(demand_tph * dt), 0)
            if demand_t <= 0:
                gap: object = 0
                episode: object = 0
            else:
                # Exactly the threshold the KPI layer and every export use.
                # A token delivery must not buy a zero-gap objective term.
                threshold_t = min(max(int(math.ceil(
                    continuity_gap_threshold_t(
                        policy, demand_tph, dt, demand_t) - 1e-9)), 1),
                    demand_t)
                gap = m.new_bool_var(f"feed_gap_{destination_id}_{t}")
                m.add(delivered[(destination_id, t)] <= threshold_t - 1) \
                    .only_enforce_if(gap)
                m.add(delivered[(destination_id, t)] >= threshold_t) \
                    .only_enforce_if(gap.Not())
                episode = add_rising_edge(
                    f"feed_episode_{destination_id}_{t}",
                    gap, previous_gap)
            feed_gap[(destination_id, t)] = gap
            feed_episode_start[(destination_id, t)] = episode
            gap_series.append(gap)
            # A zero-demand bucket closes an interruption episode.
            previous_gap = gap

        if policy.max_gap_min > 0 and gap_series:
            step_minutes = max(int(round(dt * 60.0)), 1)
            maximum_consecutive = int(
                policy.max_gap_min // step_minutes)
            window = maximum_consecutive + 1
            for start in range(0, len(gap_series) - window + 1):
                m.add(sum(gap_series[start:start + window])
                      <= maximum_consecutive)
            if bool(opts.initial_feed_gap.get(destination_id, False)):
                elapsed_open = max(float(
                    opts.initial_open_feed_gap_minutes.get(
                        destination_id, 0.0)), 0.0)
                remaining = max(int(math.floor(
                    (policy.max_gap_min - elapsed_open) /
                    float(step_minutes))), 0)
                prefix_width = min(remaining + 1, len(gap_series))
                if prefix_width:
                    m.add(sum(gap_series[:prefix_width]) <= remaining)

    # ---------- quality constraints (eq. 3) -------------------------------------
    qslack: List[cp_model.IntVar] = []
    # Per-bucket reporting series.  Under physical-window aggregation the
    # window slack is recorded at its first bucket and the remaining entries
    # are zero, preserving the existing PlanResult shape.
    qslack_by_dest: Dict[str, List[object]] = {}
    qslack_map: Dict[Tuple, object] = {}
    qslack_spec_map: Dict[Tuple, object] = {}
    aggregate_quality = bool(sc.config.get(
        "QUALITY_WINDOW_AGGREGATION_ENABLED", False))
    unknown_quality_candidates: set[Tuple[str, str, str]] = set()
    ordinary_quality_scale = QSCALE * (100 if washing.enabled(sc) else 1)
    ordinary_basis_scale = 1000 if washing.enabled(sc) else 1
    ordinary_slack_normalizer = ordinary_basis_scale * (ordinary_quality_scale // QSCALE)

    def assay_basis(candidate, spec):
        if not washing.enabled(sc) or spec.param == "moisture" or candidate.space in variable_blend_space_ids:
            return ordinary_basis_scale
        quality = _candidate_effective_quality(sc, candidate)
        return itn(ordinary_basis_scale * (1 - float(quality.get("moisture", 0)) / 100))

    def _quality_coefficient(candidate: Cand, spec) -> int:
        """Linear coefficient for ordinary destination-quality aggregation.

        An unresolved optimized pile has quality controlled by its blend-tonne
        variables above. Multiplying that variable quality by an also-variable
        reclaim flow would be bilinear. Use an on-spec neutral coefficient in
        the downstream aggregate and enforce the true selected pile quality at
        construction instead. Other source qualities remain fully explicit.
        """
        if candidate.kind != "reclaim" or \
                candidate.space not in variable_blend_space_ids:
            value = _candidate_effective_quality(sc, candidate).get(spec.param)
            try:
                return itn(float(value) * ordinary_quality_scale) if math.isfinite(float(value)) else 0
            except (TypeError, ValueError, OverflowError):
                return 0  # The corresponding candidate is explicitly excluded below.
        lower = (None if spec.pmin is None else
                 float(spec.pmin) + float(spec.margin))
        upper = (None if spec.pmax is None else
                 max(float(spec.pmax) - float(spec.margin), 0.0))
        preferred = (None if spec.target is None else float(spec.target))
        if preferred is None:
            if lower is not None and upper is not None:
                preferred = (lower + upper) / 2.0
            elif lower is not None:
                preferred = lower
            elif upper is not None:
                preferred = upper
            else:
                preferred = 0.0
        if lower is not None:
            preferred = max(preferred, lower)
        if upper is not None:
            preferred = min(preferred, upper)
        return itn(preferred * ordinary_quality_scale)

    def _spec_signature(specs):
        return tuple((s.param, s.pmin, s.pmax, s.target, s.hard,
                      s.averaging_h, s.margin) for s in specs)

    for d in sc.destinations:
        # Do not average across a change in export grade: each contiguous
        # demand campaign owns its own receiving envelope.
        segments = []
        segment_start = 0
        segment_specs = demand_specs_at(d.id, opts.horizon_offset)
        segment_signature = _spec_signature(segment_specs)
        for t in range(1, T):
            specs = demand_specs_at(d.id, t + opts.horizon_offset)
            signature = _spec_signature(specs)
            if signature != segment_signature:
                segments.append((segment_start, t, segment_specs))
                segment_start, segment_specs = t, specs
                segment_signature = signature
        segments.append((segment_start, T, segment_specs))

        for segment_start, segment_end, specs in segments:
            for spec in specs:
                if spec.pmin is not None or spec.pmax is not None:
                    for ci in demand_cis[d.id]:
                        candidate = cands[ci]
                        if candidate.kind == "reclaim" and \
                                candidate.space in variable_blend_space_ids:
                            continue  # Selected component assays are constrained above.
                        assay = _candidate_effective_quality(sc, candidate).get(spec.param)
                        try:
                            known = assay is not None and math.isfinite(float(assay))
                        except (TypeError, ValueError, OverflowError):
                            known = False
                        if not known:
                            # Unknown assays never act as zero-valued diluent.
                            unknown_quality_candidates.add((
                                d.id, _candidate_quality_space(candidate), spec.param))
                            for t in range(segment_start, segment_end):
                                if ci in demand_cis_by_bucket[(d.id, t)]:
                                    m.add(X[(ci, t)] == 0)
                window_buckets = (
                    max(int(round(float(spec.averaging_h) / dt)), 1)
                    if aggregate_quality else 1)
                for t0 in range(segment_start, segment_end, window_buckets):
                    t1 = min(t0 + window_buckets, segment_end)
                    tids = range(t0, t1)
                    dexpr = sum(delivered[(d.id, t)] for t in tids)
                    if isinstance(dexpr, int) and dexpr == 0:
                        continue
                    lhs = sum(
                        _quality_coefficient(cands[ci], spec)
                        * assay_basis(cands[ci], spec)
                        * X[(ci, t)]
                        for t in tids
                        for ci in demand_cis_by_bucket[(d.id, t)]
                    )
                    if washing.enabled(sc):
                        dexpr = sum(assay_basis(cands[ci], spec) * X[(ci, t)]
                                    for t in tids for ci in demand_cis_by_bucket[(d.id, t)])
                    if spec.pmin is not None:
                        bound = itn((spec.pmin + spec.margin) * ordinary_quality_scale)
                        if spec.hard:
                            m.add(lhs >= bound * dexpr)
                        else:
                            s = m.new_int_var(
                                0, QSLACK_MAX,
                                f"qmin_{d.id}_{spec.param}_{t0}_{t1}")
                            m.add(lhs + ordinary_slack_normalizer * s >= bound * dexpr)
                            qslack.append(s)
                            series = qslack_by_dest.setdefault(d.id, [0] * T)
                            series[t0] = series[t0] + s
                            key = ("min", d.id, spec.param, t0, t1)
                            qslack_map[key] = s
                            qslack_spec_map[key] = spec
                    if spec.pmax is not None:
                        bound = itn(max(spec.pmax - spec.margin, 0) * ordinary_quality_scale)
                        if spec.hard:
                            m.add(lhs <= bound * dexpr)
                        else:
                            s = m.new_int_var(
                                0, QSLACK_MAX,
                                f"qmax_{d.id}_{spec.param}_{t0}_{t1}")
                            m.add(lhs <= bound * dexpr + ordinary_slack_normalizer * s)
                            qslack.append(s)
                            series = qslack_by_dest.setdefault(d.id, [0] * T)
                            series[t0] = series[t0] + s
                            key = ("max", d.id, spec.param, t0, t1)
                            qslack_map[key] = s
                            qslack_spec_map[key] = spec

    # ---------- link capacities / shared / conflict groups ----------------------
    # Qualified final service is separate from physical delivery: material
    # violating a configured receiving interval earns no on-spec service
    # credit. Soft limits remain soft and forced off-spec output stays visible.
    qualified_unmet = {}
    quality_bad = {}
    if sc.config.get("OBJECTIVE_MODE", "weighted") == "service_first":
        def quality_bad_flag(slack):
            if slack.index not in quality_bad:
                bad = m.new_bool_var(f"quality_bad_{slack.index}")
                m.add(slack == 0).only_enforce_if(bad.Not())
                m.add(slack >= 1).only_enforce_if(bad)
                quality_bad[slack.index] = bad
            return quality_bad[slack.index]

        for d in sc.destinations:
            if getattr(d, "demand_kind", "delivery") == "process_feed":
                continue
            for t in range(T):
                cap = max(itn(demand_rate_at(d.id, t + opts.horizon_offset) * dt), 0)
                admitted = []
                for ci in demand_cis_by_bucket[(d.id, t)]:
                    flow = X[(ci, t)]
                    pile_slacks = blend_quality_slacks_by_receiving_space.get((d.id, cands[ci].space), [])
                    if pile_slacks:
                        bad = m.new_bool_var(f"blend_delivery_bad_{d.id}_{ci}_{t}")
                        m.add_max_equality(bad, [quality_bad_flag(s) for s in pile_slacks])
                        good_flow = m.new_int_var(0, cap, f"qualified_blend_{ci}_{t}")
                        m.add(good_flow == flow).only_enforce_if(bad.Not())
                        m.add(good_flow == 0).only_enforce_if(bad)
                        admitted.append(good_flow)
                    else:
                        admitted.append(flow)
                window_slacks = [slack for (_kind, did, _param, t0, t1), slack in qslack_map.items()
                                 if did == d.id and t0 <= t < t1]
                if window_slacks:
                    bad = m.new_bool_var(f"delivery_bad_{d.id}_{t}")
                    m.add_max_equality(bad, [quality_bad_flag(s) for s in window_slacks])
                    eligible = m.new_int_var(0, cap, f"qualified_delivery_{d.id}_{t}")
                    m.add(eligible == sum(admitted)).only_enforce_if(bad.Not())
                    m.add(eligible == 0).only_enforce_if(bad)
                else:
                    eligible = sum(admitted)
                qualified_unmet[(d.id, t)] = unmet[(d.id, t)] + delivered[(d.id, t)] - eligible

    link_users: Dict[str, List[int]] = {}
    for ci, c in enumerate(cands):
        if c.path:
            for li in c.path.links:
                link_users.setdefault(li, []).append(ci)
    for lid, users in link_users.items():
        l = sc.link(lid)
        for t in range(T):
            effective_link_rate = sc.effective_resource_rate_tph(
                f"LINK:{lid}", l.capacity_tph,
                bucket=t + opts.horizon_offset)
            cap = itn(effective_link_rate * dt) if l.available else 0
            m.add(sum(X[(ci, t)] for ci in users) <= cap)

    # Process/plant line rates are physical/as-handled t/h.  Physical washing
    # line ids expand to one independent constraint per mapped process stage;
    # destination ids cap the aggregate delivery into that receiving plant.
    line_users: Dict[Tuple[str, str], List[int]] = {}
    for ci, cand in enumerate(cands):
        for key in _line_capacity_keys(cand):
            line_users.setdefault(key, []).append(ci)
    for (scope, _stage_or_destination), users in line_users.items():
        for t in range(T):
            cap_tph = _line_effective_capacity_tph(
                sc, scope, t + opts.horizon_offset)
            if cap_tph is not None:
                m.add(sum(X[(ci, t)] for ci in users)
                      <= itn(cap_tph * dt))
    for gname, lids in graph.shared_groups().items():
        caps = [sc.link(li).capacity_tph for li in lids]
        gcap = itn(min(caps) * dt)
        users = sorted({ci for li in lids for ci in link_users.get(li, [])})
        if users:
            for t in range(T):
                m.add(sum(X[(ci, t)] for ci in users) <= gcap)
    conf_map: Dict[Tuple[str, str, int], object] = {}
    for gname, lids in graph.conflict_groups().items():
        for t in range(T):
            actives = []
            for li in lids:
                users = link_users.get(li, [])
                if not users:
                    continue
                b = m.new_bool_var(f"lact_{gname}_{li}_{t}")
                cap = itn(sc.link(li).capacity_tph * dt)
                m.add(sum(X[(ci, t)] for ci in users) <= cap * b)
                actives.append(b)
                conf_map[(gname, li, t)] = b
            if len(actives) > 1:
                m.add(sum(actives) <= 1)

    # ---------- safety stock (soft, w6) -------------------------------------------
    short: Dict[Tuple[str, int], cp_model.IntVar] = {}
    for sp in sc.spaces:
        if sp.safety_stock_t <= 0:
            continue
        tgt = itn(sp.safety_stock_t)
        for t in range(1, T + 1):
            s = m.new_int_var(0, tgt, f"short_{sp.id}_{t}")
            m.add(inv[(sp.id, t)] + s >= tgt)
            short[(sp.id, t)] = s

    # ---------- same-pile stack/reclaim concurrency (conditional) ---------------
    # USER-CONFIRMED operating rule (project team, 15-Jul-2026, MPH M16):
    # stacking and reclaiming MAY occur simultaneously on the same stockpile;
    # the scheduler must prevent collisions/overlapping work zones — never
    # prohibit same-pile concurrency categorically. This model never had a
    # categorical prohibition (the mass balance always admitted simultaneous
    # in/out); this block adds the missing CONSERVATIVE spatial room condition
    # plus the per-site configuration switch. Extraction assigns the actual
    # non-overlapping work zones (reclaim face at the low-x end, stacking at
    # the high-x end). Numerical separations are site parameters (values TBC —
    # demonstration defaults ASSUMED).
    #   room rule when both active on pile p at t (uniform-density basis):
    #       stack_t + out_t <= capacity * (1 - gap/length)
    #   with gap = min_machine_separation_m + 2 * work_zone_buffer_m.
    stack_gks_by_space: Dict[str, List[Tuple]] = {}
    out_gks_by_space: Dict[str, List[Tuple]] = {}
    for gk in group_keys:
        member = cands[group_members[gk][0]]
        d_ = (stack_gks_by_space
              if member.display_kind() == "stack"
              else out_gks_by_space)
        d_.setdefault(gk[2], []).append(gk)
    stack_cis_by_space: Dict[str, List[int]] = {}
    out_cis_by_space: Dict[str, List[int]] = {}
    for ci, c in enumerate(cands):
        d_ = (stack_cis_by_space
              if c.display_kind() == "stack"
              else out_cis_by_space)
        d_.setdefault(c.space, []).append(ci)
    site_by_id = {s.id: s for s in sc.sites}
    conc_vars: Dict[Tuple[str, int], Tuple] = {}   # (space,t) -> (s_any,o_any,both)
    for sp in sc.spaces:
        sgs = stack_gks_by_space.get(sp.id)
        ogs = out_gks_by_space.get(sp.id)
        if not sgs or not ogs:
            continue                      # concurrency impossible here anyway
        site = site_by_id.get(sp.site)
        allowed = getattr(site, "same_pile_concurrency_allowed", None)
        gap_m = (getattr(site, "min_machine_separation_m", 50.0)
                 + 2.0 * getattr(site, "work_zone_buffer_m", 25.0))
        L = max(sp.x_to - sp.x_from, 1.0)
        usable = itn(max(sp.capacity_t * (1.0 - gap_m / L), 0.0))
        cap = itn(sp.capacity_t)
        # the room rule can only ever bind when the combined machine rates
        # could exceed the usable share — otherwise it is omitted statically
        # (same conservative guarantee, far fewer constraints)
        machs = sorted({gk[1] for gk in sgs} | {gk[1] for gk in ogs})
        max_flow = itn(sum(sc.machine(mid).rate_max_tph for mid in machs) * dt)
        need_room = usable < max_flow
        # provable-slack pruning: inv[p,t] >= init - t*(max_out + exo_out)
        # (worst case), so while that bound still exceeds max_flow the
        # pass-through constraint cannot bind and is safely omitted
        exo_out_rate = max((e.rate_tph for e in sc.exogenous
                            if e.source_space == sp.id), default=0.0)
        drain = max_flow + itn(exo_out_rate * dt)
        init = min(itn(sp.mass_t), cap)
        for t in range(T):
            passthrough_slack = (init - t * drain) >= max_flow
            if allowed is False:          # site-configured (NOT categorical)
                for gs in sgs:
                    for go in ogs:
                        m.add(AG[(gs, t)] + AG[(go, t)] <= 1)
                conc_vars[(sp.id, t)] = ("forbidden", None, None)
                continue
            if passthrough_slack and not need_room:
                conc_vars[(sp.id, t)] = ("implied", None, None)
                continue
            # compact intermediate totals keep the added constraints short
            o_tot = m.new_int_var(0, max_flow, f"ot_{sp.id}_{t}")
            m.add(o_tot == sum(X[(ci, t)] for ci in out_cis_by_space[sp.id]))
            s_tot = None
            if need_room:
                s_tot = m.new_int_var(0, max_flow, f"st_{sp.id}_{t}")
                m.add(s_tot == sum(X[(ci, t)]
                                   for ci in stack_cis_by_space[sp.id]))
            for gs in sgs:
                # no same-bucket pass-through while this stack activity runs:
                # the reclaim face may only consume START-of-bucket inventory,
                # never material being stacked in the same hour (that would
                # put both machines on the same face — an overlapping-work-
                # zone collision). Sequential stack -> reclaim in later
                # buckets stays permitted (face handover). Pure linear
                # big-M form with the TIGHTEST valid M (max_flow bounds
                # o_tot), no auxiliary booleans — the hint stays complete.
                if not passthrough_slack:
                    m.add(o_tot <= inv[(sp.id, t)]
                          + max_flow * (1 - AG[(gs, t)]))
                if need_room:
                    # conservative spatial room (uniform-density basis),
                    # binding only when a stack AND an out activity coexist
                    big = 2 * max_flow
                    for go in ogs:
                        m.add(s_tot + o_tot <= usable
                              + big * (2 - AG[(gs, t)] - AG[(go, t)]))
            conc_vars[(sp.id, t)] = ("conditional", o_tot, s_tot)

    # ---------- operator-fixed jobs --------------------------------------------------
    fixed_missing = []
    #: Tonnes a frozen commitment asked for and the window could not deliver.
    #: See the rate handling below for why this is a shortfall and not an
    #: equality.
    frozen_rate_shortfall: List[cp_model.IntVar] = []
    for fj in sc.fixed_jobs:
        gmatch = None
        for gk in group_keys:
            if _fixed_matches_group(sc, fj, gk) and (
                    not washing.enabled(sc) or not fj.grade or
                    any(cands[ci].grade == fj.grade for ci in group_members[gk])):
                gmatch = gk
                break
        if gmatch is None:
            fixed_missing.append(fj.id)
            continue
        for t in range(max(fj.start_bucket - opts.horizon_offset, 0),
                       min(fj.end_bucket - opts.horizon_offset, T - 1) + 1):
            m.add(AG[(gmatch, t)] == 1)
            if washing.enabled(sc) and fj.grade:
                for ci in group_members[gmatch]:
                    if cands[ci].grade != fj.grade:
                        m.add(X[(ci, t)] == 0)
            if fj.rate_tph > 0:
                target = itn(fj.rate_tph * dt)
                flow = sum(X[(ci, t)] for ci in group_members[gmatch])
                if not getattr(fj, "advisory_rate", False):
                    # An operator asked for this rate. Honour it exactly, or
                    # report infeasibility -- never silently deliver less than
                    # a human instructed.
                    m.add(flow == target)
                else:
                    # A replan freeze is the planner's own prior commitment. It
                    # is NOT a promise the rest of the plant can reproduce a
                    # rate to the tonne: between replans work-in-progress
                    # redistributes and sources drain, and a stacking
                    # commitment is only as good as the arrivals feeding it. As
                    # an equality this made the whole civil-day window
                    # INFEASIBLE rather than merely different -- measured
                    # 29-Sep-2026 on FULL_FLOW, where two frozen stack jobs
                    # pinned to 831 and 2,323 t/h sank the first window and
                    # left the previous plan in force indefinitely.
                    #
                    # So: never exceed what was committed, and charge any
                    # shortfall to the objective.
                    gap = m.new_int_var(0, target,
                                        f"frozen_rate_shortfall_{fj.id}_{t}")
                    m.add(flow + gap == target)
                    frozen_rate_shortfall.append(gap)

    _stage("generating warm start")
    # ---------- deterministic greedy warm start (complete solution hint) --------
    # Reproducible plans need deterministic search; deterministic search needs a
    # good start. This greedy "dispatcher pass" constructs a COMPLETE, exactly
    # feasible assignment for EVERY variable family (activities, flows,
    # inventory chain, slacks, conflict booleans, move indicators) so CP-SAT's
    # first solution is already an operable plan which it then improves.
    hint_ag: Dict[Tuple[Tuple, int], int] = {}
    hint_x: Dict[Tuple[int, int], int] = {}
    used_mach: Dict[Tuple[str, int], bool] = {}
    link_load_h: Dict[Tuple[str, int], int] = {}
    line_load_h: Dict[Tuple[str, str, int], int] = {}
    shared_load_h: Dict[Tuple[str, int], int] = {}
    conflict_used: Dict[Tuple[str, int], str] = {}   # (conflict group, t) -> link
    prev_space_h: Dict[str, str] = {}
    inv_h = {sp.id: min(itn(sp.mass_t), itn(sp.capacity_t)) for sp in sc.spaces}
    link_conflict = {l.id: l.conflict_group for l in sc.links if l.conflict_group}
    link_shared = {l.id: l.shared_group for l in sc.links if l.shared_group}
    shared_cap = {g: itn(min(sc.link(li).capacity_tph for li in lids) * dt)
                  for g, lids in graph.shared_groups().items()}
    banned_set = set()
    for (bm, bs, bd, b0, b1) in opts.banned:
        for gk in group_keys:
            if gk[1] == bm and (not bs or gk[2] == bs) and (not bd or gk[3] == bd):
                for t in range(max(b0 - opts.horizon_offset, 0),
                               min(b1 - opts.horizon_offset, T - 1) + 1):
                    banned_set.add((gk, t))

    def blocked(mid: str, t: int) -> bool:
        return sc.machine_blocked(mid, t + opts.horizon_offset) or \
            not sc.machine(mid).available

    # exclusivity groups in the greedy pass (keeps the hint exactly feasible)
    excl_of: Dict[str, List[int]] = {}
    for gi, grp in enumerate(excl_groups):
        for mid in grp:
            excl_of.setdefault(mid, []).append(gi)
    excl_used: Dict[Tuple[int, int], str] = {}   # (group idx, t) -> machine

    def excl_blocked(mid: str, t: int) -> bool:
        return any(excl_used.get((gi, t), mid) != mid
                   for gi in excl_of.get(mid, ()))

    def path_room(
            ci: int, t: int,
            pending_line: Optional[Dict[Tuple[str, str], int]] = None) -> int:
        """Max extra tonnes this candidate's path can carry at t."""
        c = cands[ci]
        if _candidate_calendar_blocked(
                sc, c, t + opts.horizon_offset):
            return 0
        room = 10 ** 9
        if c.path:
            for li in c.path.links:
                l = sc.link(li)
                if not l.available:
                    return 0
                g = link_conflict.get(li)
                if g and conflict_used.get((g, t), li) != li:
                    return 0
                link_rate = sc.effective_resource_rate_tph(
                    f"LINK:{li}", l.capacity_tph,
                    bucket=t + opts.horizon_offset)
                room = min(room, itn(link_rate * dt)
                           - link_load_h.get((li, t), 0))
            for g in {link_shared[li] for li in c.path.links if li in link_shared}:
                room = min(room, shared_cap[g] - shared_load_h.get((g, t), 0))
        for scope, group in _line_capacity_keys(c):
            line_rate = _line_effective_capacity_tph(
                sc, scope, t + opts.horizon_offset)
            if line_rate is None:
                continue
            provisional = (pending_line or {}).get((scope, group), 0)
            room = min(
                room, itn(line_rate * dt)
                - line_load_h.get((scope, group, t), 0) - provisional)
        return max(room, 0)

    def commit_path(ci: int, t: int, v: int):
        c = cands[ci]
        if c.path:
            for li in c.path.links:
                link_load_h[(li, t)] = link_load_h.get((li, t), 0) + v
                g = link_conflict.get(li)
                if g:
                    conflict_used[(g, t)] = li
            for g in {link_shared[li] for li in c.path.links if li in link_shared}:
                shared_load_h[(g, t)] = shared_load_h.get((g, t), 0) + v
        for scope, group in _line_capacity_keys(c):
            key = (scope, group, t)
            line_load_h[key] = line_load_h.get(key, 0) + v

    def assign_flow(gk: Tuple, t: int, amount: int) -> int:
        """Activate group at t; split `amount` over feasible member paths."""
        if (gk, t) in banned_set or blocked(gk[1], t) or \
                used_mach.get((gk[1], t)) or excl_blocked(gk[1], t):
            return 0
        ps = prev_space_h.get(gk[1])
        if ps is not None and transition_exceeds_bucket(
                gk[1], ps, gk[2]):
            return 0
        mach = sc.machine(gk[1])
        rf = opts.machine_rate_factor.get(gk[1], 1.0)
        machine_rate = sc.effective_resource_rate_tph(
            gk[1], mach.rate_max_tph * rf,
            bucket=t + opts.horizon_offset)
        hi = itn(machine_rate * dt)
        lo = min(itn(mach.rate_min_tph * dt), hi)
        policy = resolve_campaign_policy(sc, gk[1])
        parcel_floor = min(max(itn(policy.parcel_floor_t(
            float(mach.rate_min_tph) * dt)), lo), hi)
        minimum_hint_parcel = max(
            parcel_floor if not policy.allow_short_parcel else lo, 1)
        rem = min(amount, hi)
        prep = (preparation_minutes(gk[1], ps, gk[2])
                if ps is not None and ps != gk[2] else
                initial_preparation.get((gk[1], gk[2]), 0) if t == 0 else 0)
        if prep > 0:
            rem = min(rem, prepared_mass_cap(gk, t, prep))
        placed = 0
        plan = []
        pending_line: Dict[Tuple[str, str], int] = {}
        for ci in group_members[gk]:
            if rem <= 0:
                break
            ccap = itn(_candidate_effective_capacity_tph(
                sc, cands[ci], t + opts.horizon_offset, rf) * dt)
            v = min(ccap, rem, path_room(ci, t, pending_line))
            if v <= 0:
                continue
            plan.append((ci, v))
            for key in _line_capacity_keys(cands[ci]):
                pending_line[key] = pending_line.get(key, 0) + v
            rem -= v
            placed += v
        # A hard parcel floor is also a CP-SAT constraint. Do not seed an
        # active group with an illegal token flow and leave the solver to
        # repair a contradictory warm start under a short window budget.
        if placed < minimum_hint_parcel:
            return 0
        for ci, v in plan:
            hint_x[(ci, t)] = v
            commit_path(ci, t, v)
        hint_ag[(gk, t)] = 1
        used_mach[(gk[1], t)] = True
        for gi in excl_of.get(gk[1], ()):
            excl_used[(gi, t)] = gk[1]
        return placed

    # constant arrival mass per (exit node, grade, t) — belt profile only
    arr_const: Dict[Tuple[str, str, int], int] = {}
    for l in [l_ for l_ in sc.links if l_.transit_buckets > 0]:
        for e in sc.belt_profile:
            if e.link_id == l.id:
                k = (l.dst, e.grade, e.arrive_bucket - opts.horizon_offset)
                if 0 <= k[2] < T:
                    arr_const[k] = arr_const.get(k, 0) + itn(e.mass_t)

    fixed_windows = []   # (gk, t0, t1, rate_t) resolved fixed jobs
    for fj in sc.fixed_jobs:
        for gk in group_keys:
            if _fixed_matches_group(sc, fj, gk):
                fixed_windows.append(
                    (gk, max(fj.start_bucket - opts.horizon_offset, 0),
                     min(fj.end_bucket - opts.horizon_offset, T - 1),
                     itn(fj.rate_tph * dt)))
                break

    # Hard stocked-route/process policy must be represented in the hint too.
    # The original dispatcher below is intentionally destination-demand first;
    # without this pass it hints every process/dispatch target at zero and the
    # otherwise useful complete hint is infeasible.  Build a deterministic,
    # configuration-driven process chain without changing any model constraint:
    #
    #   targeted process -> declared dependent transfer -> replenishing input
    #   stack, plus configured corridor dispatch -> delayed receiving stack.
    #
    # Static reservations mirror the solver's machine, route-confluence,
    # link/shared-capacity and site same-pile rules.  If a non-baseline scenario
    # cannot fit an action, the pass simply leaves that action unhinted; CP-SAT
    # remains authoritative.
    policy_actions: Dict[int, List[Tuple[int, int]]] = {}
    policy_arrivals: Dict[Tuple[str, str, int], int] = {}
    hint_y: Dict[Tuple[int, int], int] = {}
    reserved_machine: Dict[Tuple[str, int], int] = {}
    reserved_mass: Dict[Tuple[str, int], int] = {}
    reserved_excl: Dict[Tuple[int, int], str] = {}
    reserved_link: Dict[Tuple[str, int], int] = {}
    reserved_line: Dict[Tuple[str, str, int], int] = {}
    reserved_shared: Dict[Tuple[str, int], int] = {}
    reserved_conflict: Dict[Tuple[str, int], str] = {}
    reserved_pile_mode: Dict[Tuple[str, int], str] = {}
    site_by_space = {
        sp.id: next((site for site in sc.sites if site.id == sp.site), None)
        for sp in sc.spaces
    }

    def cand_cap(ci: int, t: Optional[int] = None) -> int:
        cand = cands[ci]
        rf = opts.machine_rate_factor.get(cand.machine, 1.0)
        buckets = range(T) if t is None else (t,)
        return max((itn(_candidate_effective_capacity_tph(
            sc, cand, bucket + opts.horizon_offset, rf) * dt)
                    for bucket in buckets), default=0)

    def cand_lo(ci: int, t: Optional[int] = None) -> int:
        cand = cands[ci]
        mach = sc.machine(cand.machine)
        return min(itn(mach.rate_min_tph * dt),
                   cand_cap(ci, t))

    def process_output(ci: int, amount: int) -> int:
        cand = cands[ci]
        if cand.kind != "process":
            return amount
        coef = int(round(max(min(cand.yield_factor, 1.0), 0.0) * 10_000))
        return amount if coef == 10_000 else (amount * coef) // 10_000

    hint_process_input: Dict[Tuple[int, int], int] = {}

    def hint_process_output(ci: int, amount: int, bucket: int) -> int:
        """Warm-hint output with the same fractional carry as the model."""
        if not yield_carry_buckets:
            return process_output(ci, amount)
        cand = cands[ci]
        if cand.kind != "process":
            return amount
        coef = int(round(max(min(cand.yield_factor, 1.0), 0.0) * 10_000))
        if coef == 10_000:
            return amount
        block = bucket // yield_carry_buckets
        key = (ci, block)
        before = hint_process_input.get(key, 0)
        after = before + amount
        hint_process_input[key] = after
        return (after * coef) // 10_000 - (before * coef) // 10_000

    def flow_chunks(total: int, cap: int, lo: int) -> List[int]:
        """Full-capacity deterministic chunks with a valid final minimum."""
        if total <= 0 or cap <= 0:
            return []
        lo = max(min(lo, cap), 1)
        chunks = []
        remaining = total
        while remaining > cap:
            take = cap
            if 0 < remaining - take < lo:
                take = remaining - lo
            if take < lo:
                return []
            chunks.append(take)
            remaining -= take
        if remaining:
            if remaining < lo:
                return []
            chunks.append(remaining)
        return chunks

    def can_reserve(ci: int, t: int, amount: int) -> bool:
        if not (0 <= t < T):
            return False
        cand = cands[ci]
        gk = g_of[ci]
        cap = cand_cap(ci, t)
        lo = cand_lo(ci, t)
        if amount <= 0 or amount > cap or amount < max(lo, 1):
            return False
        if (gk, t) in banned_set or blocked(cand.machine, t) or \
                _candidate_calendar_blocked(
                    sc, cand, t + opts.horizon_offset):
            return False
        if (cand.machine, t) in reserved_machine:
            return False
        if t == 0 and amount > prepared_mass_cap(
                gk, t, initial_preparation.get((cand.machine, cand.space), 0)):
            return False
        for gi in excl_of.get(cand.machine, ()):
            if reserved_excl.get((gi, t), cand.machine) != cand.machine:
                return False
        # Mirror the coarse adjacent-bucket travel guard in the warm start.
        for (other_machine, tt), other_ci in reserved_machine.items():
            if other_machine != cand.machine or tt == t:
                continue
            if abs(t - tt) == 1 and transition_exceeds_bucket(
                    cand.machine, cand.space, cands[other_ci].space):
                return False
            if tt == t - 1 and cands[other_ci].space != cand.space and \
                    amount > prepared_mass_cap(gk, t, preparation_minutes(
                        cand.machine, cands[other_ci].space, cand.space)):
                return False
            if tt == t + 1 and cands[other_ci].space != cand.space and \
                    reserved_mass[(cand.machine, tt)] > prepared_mass_cap(
                        g_of[other_ci], tt, preparation_minutes(
                            cand.machine, cand.space, cands[other_ci].space)):
                return False
        if cand.path:
            for li in cand.path.links:
                link = sc.link(li)
                link_rate = sc.effective_resource_rate_tph(
                    f"LINK:{li}", link.capacity_tph,
                    bucket=t + opts.horizon_offset)
                if not link.available or (
                        reserved_link.get((li, t), 0) + amount
                        > itn(link_rate * dt)):
                    return False
                conflict = link_conflict.get(li)
                if conflict and reserved_conflict.get(
                        (conflict, t), li) != li:
                    return False
            for shared in {
                    link_shared[li] for li in cand.path.links
                    if li in link_shared}:
                if (reserved_shared.get((shared, t), 0) + amount
                        > shared_cap[shared]):
                    return False
        for scope, group in _line_capacity_keys(cand):
            line_rate = _line_effective_capacity_tph(
                sc, scope, t + opts.horizon_offset)
            if line_rate is not None and (
                    reserved_line.get((scope, group, t), 0) + amount
                    > itn(line_rate * dt)):
                return False
        site = site_by_space.get(cand.space)
        if getattr(site, "same_pile_concurrency_allowed", None) is False:
            mode = "stack" if cand.display_kind() == "stack" else "out"
            prior = reserved_pile_mode.get((cand.space, t))
            if prior is not None and prior != mode:
                return False
        return True

    def reserve(ci: int, t: int, amount: int):
        cand = cands[ci]
        reserved_machine[(cand.machine, t)] = ci
        reserved_mass[(cand.machine, t)] = amount
        for gi in excl_of.get(cand.machine, ()):
            reserved_excl[(gi, t)] = cand.machine
        if cand.path:
            for li in cand.path.links:
                reserved_link[(li, t)] = \
                    reserved_link.get((li, t), 0) + amount
                conflict = link_conflict.get(li)
                if conflict:
                    reserved_conflict[(conflict, t)] = li
            for shared in {
                    link_shared[li] for li in cand.path.links
                    if li in link_shared}:
                reserved_shared[(shared, t)] = \
                    reserved_shared.get((shared, t), 0) + amount
        for scope, group in _line_capacity_keys(cand):
            key = (scope, group, t)
            reserved_line[key] = reserved_line.get(key, 0) + amount
        site = site_by_space.get(cand.space)
        if getattr(site, "same_pile_concurrency_allowed", None) is False:
            reserved_pile_mode[(cand.space, t)] = (
                "stack" if cand.display_kind() == "stack" else "out")
        policy_actions.setdefault(t, []).append((ci, amount))

    def first_reservable(ci: int, amount: int, start: int = 0,
                         stop: Optional[int] = None) -> Optional[int]:
        end = T if stop is None else min(stop, T)
        return next((t for t in range(max(start, 0), end)
                     if can_reserve(ci, t, amount)), None)

    # 1) Exact useful output chunks for every configured process stage.
    stage_queues: Dict[str, List[Tuple[int, int, int]]] = {}
    for stage, raw_target in sorted(
            effective_process_stage_targets(sc).items()):
        target = max(itn(float(raw_target)), 0)
        stage_cis = sorted(
            (ci for ci, cand in enumerate(cands)
             if cand.kind == "process" and cand.process_stage == stage),
            key=lambda ci: (cands[ci].transfer_id, cands[ci].machine))
        if target <= 0 or not stage_cis:
            continue
        # Pass 2 emits dependent transfers from their upstream actions.  Do
        # not queue them here as well or exact stage targets are doubled.
        if all(cands[ci].depends_on_stage for ci in stage_cis):
            continue
        # Prefer the candidate with the largest useful output envelope.  Stages
        # with parallel alternatives remain deterministic without inventing a
        # split policy that configuration did not request.
        ci = max(stage_cis, key=lambda idx: (process_output(
            idx, cand_cap(idx)), -idx))
        cap = cand_cap(ci)
        lo = max(cand_lo(ci), 1)
        out_hi = process_output(ci, cap)
        out_lo = process_output(ci, lo)
        if out_hi <= 0:
            continue
        count = max((target + out_hi - 1) // out_hi, 1)
        useful_total = max(target, count * out_lo)
        if useful_total > count * out_hi:
            continue
        outputs = [out_lo] * count
        remainder = useful_total - count * out_lo
        for idx in range(count):
            add = min(remainder, out_hi - out_lo)
            outputs[idx] += add
            remainder -= add
        coef = int(round(max(min(cands[ci].yield_factor, 1.0), 0.0)
                         * 10_000))
        inputs = [
            out if coef == 10_000 else (out * 10_000 + coef - 1) // coef
            for out in outputs
        ]
        if all(lo <= amount <= cap for amount in inputs):
            stage_queues[stage] = [
                (ci, amount, process_output(ci, amount))
                for amount in inputs
            ]

    # Round-robin stages so exclusive parallel line reclaimers alternate.
    scheduled_stage: Dict[str, List[Tuple[int, int, int, int]]] = {}
    stage_cursor = {stage: 0 for stage in stage_queues}
    while any(stage_queues.values()):
        progressed = False
        for stage in sorted(stage_queues):
            queue = stage_queues[stage]
            if not queue:
                continue
            ci, amount, output = queue[0]
            t = first_reservable(ci, amount, stage_cursor[stage])
            if t is None:
                continue
            reserve(ci, t, amount)
            scheduled_stage.setdefault(stage, []).append(
                (t, ci, amount, output))
            queue.pop(0)
            stage_cursor[stage] = t + 1
            progressed = True
        if not progressed:
            break

    # 2) Follow declared stage dependencies after the configured delay.
    replenished_output: Dict[str, int] = {}
    for stage, actions in sorted(scheduled_stage.items()):
        for t, upstream_ci, _amount, output in actions:
            upstream = cands[upstream_ci]
            dep_cis = sorted(
                (ci for ci, cand in enumerate(cands)
                 if cand.kind == "process"
                 and cand.depends_on_stage == stage
                 and cand.source_space == upstream.target_space),
                key=lambda ci: (cands[ci].transfer_id, cands[ci].machine))
            if not dep_cis:
                continue
            dep_ci = dep_cis[0]
            dep = cands[dep_ci]
            lag = max(
                1 + (
                    max(dep.dependency_delay_min, 0)
                    + max(itn(dt * 60), 1) - 1
                ) // max(itn(dt * 60), 1),
                1 if dep.source_space in no_passthrough_spaces else 0)
            td = first_reservable(dep_ci, output, t + lag)
            if td is None:
                continue
            reserve(dep_ci, td, output)
            replenished_output[dep.target_space] = \
                replenished_output.get(dep.target_space, 0) + \
                process_output(dep_ci, output)

    # 3) Replenish every targeted process source through its explicit upstream
    # transfer.  Terminal-min policies then start from a materially balanced
    # hint instead of relying on CP-SAT to discover the chain from zero.
    source_consumption: Dict[str, int] = {}
    scheduled_output: Dict[str, int] = {}
    for actions in scheduled_stage.values():
        for _t, ci, amount, output in actions:
            source = cands[ci].source_space
            source_consumption[source] = \
                source_consumption.get(source, 0) + amount
            target = cands[ci].target_space
            scheduled_output[target] = \
                scheduled_output.get(target, 0) + output
    for source, total in sorted(source_consumption.items()):
        total = max(
            total - scheduled_output.get(source, 0)
            - replenished_output.get(source, 0),
            0,
        )
        upstream_cis = sorted(
            (ci for ci, cand in enumerate(cands)
             if cand.kind == "process" and cand.target_space == source
             and not cand.depends_on_stage),
            key=lambda ci: (cands[ci].transfer_id, cands[ci].machine))
        if not upstream_cis:
            continue
        ci = upstream_cis[0]
        for amount in flow_chunks(total, cand_cap(ci), cand_lo(ci)):
            t = first_reservable(ci, amount)
            if t is None:
                break
            reserve(ci, t, amount)

    # 4) Dispatch configured corridor tonnage in source-balanced chunks and
    # reserve an equal receiving-stack action at the delayed arrival bucket.
    for link_id, raw_target in sorted(effective_corridor_targets(sc).items()):
        remaining = max(itn(float(raw_target)), 0)
        dispatch_cis = sorted(
            (ci for ci, cand in enumerate(cands)
             if cand.kind == "dispatch" and cand.dest_id == link_id),
            key=lambda ci: (
                0 if replenished_output.get(cands[ci].space, 0) > 0 else 1,
                cands[ci].space, cands[ci].machine))
        # one deterministic candidate per physical source avoids duplicate
        # alternatives when several machines can reach the same pile
        selected: Dict[str, int] = {}
        for ci in dispatch_cis:
            selected.setdefault(cands[ci].space, ci)
        for source, ci in selected.items():
            if remaining <= 0:
                break
            backed = replenished_output.get(source, 0)
            quota = min(remaining, backed if backed > 0 else remaining)
            cand = cands[ci]
            try:
                lag = sc.link(link_id).transit_buckets
            except StopIteration:
                continue
            prior_arrivals = [
                tt for (exit_node, _grade, tt), amount in arr_const.items()
                if exit_node == cand.exit_node and amount > 0
            ]
            arrival_start = max(prior_arrivals, default=-1) + 1
            dispatch_start = max(arrival_start - lag, 0)
            for amount in flow_chunks(quota, cand_cap(ci), cand_lo(ci)):
                paired = False
                for t in range(dispatch_start, max(T - lag, dispatch_start)):
                    arrival_t = t + lag
                    if not can_reserve(ci, t, amount):
                        continue
                    stack_cis = sorted({
                        group_members[gk][0]
                        for gk in group_keys
                        if gk[0] == "stack" and gk[5] == cand.exit_node
                        and gk[4] == cand.grade
                    }, key=lambda idx: (
                        cands[idx].machine, cands[idx].space))
                    stack_ci = next((
                        sci for sci in stack_cis
                        if can_reserve(sci, arrival_t, amount)), None)
                    if stack_ci is None:
                        continue
                    reserve(ci, t, amount)
                    reserve(stack_ci, arrival_t, amount)
                    key = (cand.exit_node, cand.material_id or cand.grade, arrival_t)
                    policy_arrivals[key] = \
                        policy_arrivals.get(key, 0) + amount
                    paired = True
                    break
                if not paired:
                    break
            remaining -= quota

    delivered_h: Dict[Tuple[str, int], int] = {}
    stacked_h: Dict[Tuple[str, str, int], int] = {}   # (exit, grade, t) -> placed
    ins_h: Dict[Tuple[str, int], int] = {}
    outs_h: Dict[Tuple[str, int], int] = {}
    rr = 0
    for t in range(T):
        active_space: Dict[str, str] = {}
        # 0) operator-fixed jobs take precedence
        for (gk, fw0, fw1, rate_t) in fixed_windows:
            if fw0 <= t <= fw1 and not used_mach.get((gk[1], t)):
                amt = rate_t if rate_t > 0 else \
                    max(itn(sc.machine(gk[1]).rate_min_tph * dt), 1)
                placed = assign_flow(gk, t, amt)
                if placed > 0:
                    active_space[gk[1]] = gk[2]
                    if gk[0] == "reclaim":
                        delivered_h[(gk[3], t)] = \
                            delivered_h.get((gk[3], t), 0) + placed
                        outs_h[(gk[2], t)] = outs_h.get((gk[2], t), 0) + placed
                    elif gk[0] == "stack":
                        ins_h[(gk[2], t)] = ins_h.get((gk[2], t), 0) + placed
        # 1) configuration-backed internal process / corridor policy
        for ci, amount in policy_actions.get(t, ()):
            cand = cands[ci]
            gk = g_of[ci]
            placed = assign_flow(gk, t, amount)
            if placed <= 0:
                continue
            active_space[cand.machine] = cand.space
            if cand.kind == "process":
                output = hint_process_output(ci, placed, t)
                hint_y[(ci, t)] = output
                outs_h[(cand.source_space, t)] = \
                    outs_h.get((cand.source_space, t), 0) + placed
                ins_h[(cand.target_space, t)] = \
                    ins_h.get((cand.target_space, t), 0) + output
                for destination in sc.destinations:
                    if _candidate_serves_destination(
                            sc, cand, destination,
                            requested_grade=allocation_grade_at(
                                destination.id, t + opts.horizon_offset)):
                        key = (destination.id, t)
                        delivered_h[key] = delivered_h.get(key, 0) + placed
            elif cand.kind == "dispatch":
                outs_h[(cand.space, t)] = \
                    outs_h.get((cand.space, t), 0) + placed
            elif cand.kind == "stack":
                ins_h[(cand.space, t)] = \
                    ins_h.get((cand.space, t), 0) + placed
                stacked_h[(cand.exit_node, cand.grade, t)] = \
                    stacked_h.get((cand.exit_node, cand.grade, t), 0) + placed
        # 2) stack constant arrivals round-robin over free stackers
        for (exit_node, g, tt), arr in sorted(arr_const.items()):
            if tt != t or arr <= 0:
                continue
            remaining = arr - stacked_h.get((exit_node, g, t), 0)
            sgs = sorted([gk for gk in group_keys if gk[0] == "stack"
                          and gk[5] == exit_node and gk[4] == g],
                         key=lambda gk: (gk[1], gk[2]))
            for k in range(len(sgs)):
                if remaining <= 0:
                    break
                gk = sgs[(rr + k) % max(len(sgs), 1)]
                free = itn(sc.space(gk[2]).capacity_t) - inv_h.get(gk[2], 0) \
                    - ins_h.get((gk[2], t), 0)
                placed = assign_flow(gk, t, min(remaining, max(free, 0)))
                if placed > 0:
                    active_space[gk[1]] = gk[2]
                    stacked_h[(exit_node, g, t)] = \
                        stacked_h.get((exit_node, g, t), 0) + placed
                    ins_h[(gk[2], t)] = ins_h.get((gk[2], t), 0) + placed
                    remaining -= placed
                    rr += 1
        # 3) reclaim to destinations by priority, best-quality-fit space first
        for d in sorted(sc.destinations, key=lambda d_: -demand_priority_at(
                d_.id, t + opts.horizon_offset)):
            if getattr(d, "demand_kind", "delivery") == "process_feed":
                continue
            need = itn(demand_rate_at(d.id, t + opts.horizon_offset) * dt) \
                - delivered_h.get((d.id, t), 0)
            if need <= 0:
                continue
            # Keep the warm start on the same source-grade contract as the
            # CP-SAT candidate set.  ``demand_grade_at`` also returns the
            # destination's legacy display/output label when no dated demand
            # row exists; treating that label as a source restriction makes
            # the S2 blend problem artificially TBT-only and leaves avoidable
            # unmet demand (and a few tonnes of overflow) in the first
            # feasible solution.  ``allocation_grade_at`` is deliberately
            # blank for those legacy delivery profiles, while remaining
            # grade-specific for explicit weekly campaigns and process feed.
            requested_grade = allocation_grade_at(
                d.id, t + opts.horizon_offset)
            rgs = [
                gk for gk in group_keys
                if gk[0] == "reclaim" and gk[3] == d.id
                and _candidate_serves_destination(
                    sc, cands[group_members[gk][0]], d,
                    requested_grade=requested_grade)
            ]

            def fit(gk):
                q = _candidate_effective_quality(
                    sc, cands[group_members[gk][0]])
                pen = 0.0
                for s_ in demand_specs_at(
                        d.id, t + opts.horizon_offset):
                    v = q.get(s_.param)
                    if v is None:
                        pen += 1_000_000
                        continue
                    if s_.pmin is not None and v < s_.pmin:
                        pen += (s_.pmin - v) * 10
                    if s_.pmax is not None and v > s_.pmax:
                        pen += (v - s_.pmax) * 10
                return (pen, -inv_h.get(gk[2], 0), gk[1], gk[2])
            for gk in sorted(rgs, key=fit):
                if need <= 0:
                    break
                hard_bad = False
                q = _candidate_effective_quality(
                    sc, cands[group_members[gk][0]])
                for s_ in demand_specs_at(
                        d.id, t + opts.horizon_offset):
                    value = q.get(s_.param)
                    if s_.hard and (value is None or
                            (s_.pmin is not None and value < s_.pmin) or
                            (s_.pmax is not None and value > s_.pmax)):
                        hard_bad = True
                if hard_bad:
                    continue
                avail = inv_h.get(gk[2], 0) - outs_h.get((gk[2], t), 0)
                if avail <= 1:
                    continue
                placed = assign_flow(gk, t, min(need, avail))
                if placed > 0:
                    active_space[gk[1]] = gk[2]
                    delivered_h[(d.id, t)] = delivered_h.get((d.id, t), 0) + placed
                    outs_h[(gk[2], t)] = outs_h.get((gk[2], t), 0) + placed
                    need -= placed
        prev_space_h.clear()
        prev_space_h.update(active_space)
        # end-of-bucket inventory roll (matches the model equation exactly)
        for sp in sc.spaces:
            exo_in = exo_out = 0
            for e in sc.exogenous:
                if e.start_bucket <= t + opts.horizon_offset <= e.end_bucket:
                    if e.target_space == sp.id:
                        exo_in += itn(e.rate_tph * dt)
                    if e.source_space == sp.id:
                        exo_out += itn(e.rate_tph * dt)
            nxt = inv_h[sp.id] + ins_h.get((sp.id, t), 0) + exo_in \
                - outs_h.get((sp.id, t), 0) - exo_out
            spill_h = max(nxt - itn(sp.capacity_t), 0)
            m.add_hint(spill_map[(sp.id, t)], spill_h)
            inv_h[sp.id] = max(nxt - spill_h, 0)
            m.add_hint(inv[(sp.id, t + 1)], inv_h[sp.id])

    # complete the hint for every remaining variable family
    for gk in group_keys:
        for t in range(T):
            m.add_hint(AG[(gk, t)], hint_ag.get((gk, t), 0))
    for mid, used in machine_used.items():
        m.add_hint(used, int(any(
            hint_ag.get((gk, t), 0)
            for gk in mach_groups.get(mid, ()) for t in range(T))))
    for ci in range(len(cands)):
        for t in range(T):
            m.add_hint(X[(ci, t)], hint_x.get((ci, t), 0))
            if (ci, t) in Y and Y[(ci, t)] is not X[(ci, t)]:
                m.add_hint(Y[(ci, t)], hint_y.get((ci, t), 0))
    for d in sc.destinations:
        if getattr(d, "demand_kind", "delivery") == "process_feed":
            campaigns: Dict[Tuple[str, str, int], List[int]] = {}
            for t in range(T):
                absolute_bucket = t + opts.horizon_offset
                key = (
                    sc.sim.bucket_dt(absolute_bucket).date().isoformat(),
                    demand_grade_at(d.id, absolute_bucket),
                    demand_priority_at(d.id, absolute_bucket),
                )
                campaigns.setdefault(key, []).append(t)
            for tids in campaigns.values():
                slack = unmet[(d.id, tids[0])]
                if isinstance(slack, int):
                    continue
                dem = sum(
                    itn(demand_rate_at(
                        d.id, t + opts.horizon_offset) * dt)
                    for t in tids)
                got = sum(
                    delivered_h.get((d.id, t), 0) for t in tids)
                m.add_hint(slack, max(dem - got, 0))
        else:
            for t in range(T):
                dem = itn(demand_rate_at(
                    d.id, t + opts.horizon_offset) * dt)
                got = delivered_h.get((d.id, t), 0)
                m.add_hint(unmet[(d.id, t)], max(dem - got, 0))
    for (kind_, did, param, t0, t1), svar in qslack_map.items():
        spec = qslack_spec_map[(kind_, did, param, t0, t1)]
        got = sum(
            delivered_h.get((did, t), 0) for t in range(t0, t1))
        lhs = 0
        for t in range(t0, t1):
            for ci in demand_cis_by_bucket[(did, t)]:
                if (ci, t) in hint_x:
                    lhs += _quality_coefficient(
                        cands[ci], spec) * hint_x[(ci, t)]
        if kind_ == "min":
            bound = itn((spec.pmin + spec.margin) * QSCALE)
            m.add_hint(svar, min(max(bound * got - lhs, 0), QSLACK_MAX))
        else:
            bound = itn(max(spec.pmax - spec.margin, 0) * QSCALE)
            m.add_hint(svar, min(max(lhs - bound * got, 0), QSLACK_MAX))
    for (exit_node, g, t), svar in surge_map.items():
        arrived = (arr_const.get((exit_node, g, t), 0)
                   + policy_arrivals.get((exit_node, g, t), 0))
        m.add_hint(
            svar, max(arrived - stacked_h.get((exit_node, g, t), 0), 0))
    for (gname, li, t), b in conf_map.items():
        m.add_hint(b, 1 if link_load_h.get((li, t), 0) > 0 else 0)
    for mid, gks in mach_groups.items():
        group_state = {gk: index + 1 for index, gk in enumerate(gks)}
        configured_previous = opts.initial_machine_groups.get(mid)
        previous_group = (
            tuple(configured_previous)
            if isinstance(configured_previous, (list, tuple))
            and configured_previous else None)
        previous_state = (
            group_state.get(previous_group, len(gks) + 1)
            if previous_group is not None else 0)
        previous_on = int(previous_group is not None)
        for t in range(T):
            current_group = next((
                gk for gk in gks if hint_ag.get((gk, t), 0)), None)
            current_on = int(current_group is not None)
            current_state = group_state.get(current_group, 0)
            m.add_hint(machine_state[(mid, t)], current_state)
            m.add_hint(
                machine_starts[(mid, t)],
                int(current_on and not previous_on))
            m.add_hint(
                machine_stops[(mid, t)],
                int(previous_on and not current_on))
            m.add_hint(
                machine_same_state[(mid, t)],
                int(current_state == previous_state))
            m.add_hint(
                machine_changeovers[(mid, t)],
                int(current_on and previous_on
                    and current_group != previous_group))
            hold_var = machine_hold.get((mid, t))
            if hold_var is not None and not isinstance(hold_var, int):
                # The greedy warm start never holds; CP-SAT may improve on it.
                m.add_hint(hold_var, 0)
            previous_state = current_state
            previous_group = current_group
            previous_on = current_on
    for destination_id, policy in continuity_policies.items():
        if not policy.scored:
            continue
        previous_gap = bool(opts.initial_feed_gap.get(
            destination_id, False))
        for t in range(T):
            gap_var = feed_gap[(destination_id, t)]
            episode_var = feed_episode_start[(destination_id, t)]
            demand_tph = max(demand_rate_at(
                destination_id, t + opts.horizon_offset), 0.0)
            demand_t = max(itn(demand_tph * dt), 0)
            got_t = delivered_h.get((destination_id, t), 0)
            threshold_t = max(int(math.ceil(
                continuity_gap_threshold_t(
                    policy, demand_tph, dt, demand_t) - 1e-9)), 1)
            current_gap = bool(
                demand_t > 0 and got_t < min(threshold_t, demand_t))
            if not isinstance(gap_var, int):
                m.add_hint(gap_var, int(current_gap))
            if not isinstance(episode_var, int):
                m.add_hint(
                    episode_var, int(current_gap and not previous_gap))
            previous_gap = current_gap
    # safety-stock shortfall hints: re-walk the hinted inventory chain
    inv_walk = {sp.id: [min(itn(sp.mass_t), itn(sp.capacity_t))]
                for sp in sc.spaces}
    for t in range(T):
        for sp in sc.spaces:
            exo_in = exo_out = 0
            for e in sc.exogenous:
                if e.start_bucket <= t + opts.horizon_offset <= e.end_bucket:
                    if e.target_space == sp.id:
                        exo_in += itn(e.rate_tph * dt)
                    if e.source_space == sp.id:
                        exo_out += itn(e.rate_tph * dt)
            nxt = inv_walk[sp.id][-1] + ins_h.get((sp.id, t), 0) + exo_in \
                - outs_h.get((sp.id, t), 0) - exo_out
            nxt = max(min(nxt, itn(sp.capacity_t)), 0)
            inv_walk[sp.id].append(nxt)
    for (sid, tt), svar in short.items():
        tgt = itn(sc.space(sid).safety_stock_t)
        m.add_hint(svar, max(tgt - inv_walk[sid][tt], 0))
    for stage, deficit in stage_target_shortfall.items():
        hinted_output = sum(
            hint_y.get((ci, t), hint_x.get((ci, t), 0))
            for ci, candidate in enumerate(cands)
            if candidate.kind == "process" and candidate.process_stage == stage
            for t in range(T))
        target_t = itn(float(effective_process_stage_targets(sc)[stage]))
        cap_t = itn(float(stage_shortfall_limits[stage]))
        m.add_hint(deficit, min(max(target_t - hinted_output, 0), cap_t))
    # same-pile concurrency intermediate totals (hinted from the greedy walk)
    for (spid, t), (_, o_tot, s_tot) in conc_vars.items():
        if o_tot is not None:
            m.add_hint(o_tot, outs_h.get((spid, t), 0))
        if s_tot is not None:
            m.add_hint(s_tot, ins_h.get((spid, t), 0))

    # ---------- objective ---------------------------------------------------------------
    w = sc.weights
    weight_coef = _objective_weight_coefficients(w)
    terms = []
    terms += [weight_coef["w1_unmet"]
              * demand_priority_at(destination_id, bucket + opts.horizon_offset) * u
              for (destination_id, bucket), u in unmet.items()]
    terms += [weight_coef["w2_offspec"] * s for s in qslack]
    terms += [weight_coef["w2_offspec"] * priority * slack
              for priority, slack in blend_quality_slack_terms]
    # Deterministic low-order tie-break: when several recipes have identical
    # service and quality cost, avoid gratuitous trace components.  This does
    # not override off-spec penalties; it only selects the smallest admissible
    # component set among otherwise equivalent blend matrices.
    terms += blend_component_use_terms
    quality_terms_end = len(terms)
    equipment_weight = weight_coef["w5_moves"]
    if equipment_weight:
        terms += [
            equipment_weight
            * operational_penalty(sc, "EQUIPMENT_START_PENALTY") * event
            for event in machine_starts.values()]
        terms += [
            equipment_weight
            * operational_penalty(sc, "EQUIPMENT_STOP_PENALTY") * event
            for event in machine_stops.values()]
        terms += [
            equipment_weight
            * operational_penalty(
                sc, "EQUIPMENT_CHANGEOVER_PENALTY") * event
            for event in machine_changeovers.values()]
        terms += [
            itn(equipment_weight
                * operational_penalty(sc, "EQUIPMENT_HOLD_BUCKET_PENALTY") * dt) * hold
            for hold in machine_hold.values()
            if not isinstance(hold, int)]
        # Fragmentation cost: a bucket that runs below the configured parcel
        # floor. Priced with the same operator control as a start or a
        # changeover, because it is the same kind of decision.
    equipment_terms = terms[quality_terms_end:]
    # Separate controls: changing the equipment multiplier must NOT change
    # the cost of crossing an assumed parcel threshold.
    parcel_penalty = short_parcel_penalty(sc)
    if parcel_penalty and short_parcel:
        terms += [WSCALE * parcel_penalty * flag for flag in short_parcel.values()]
    planning_target_multiplier = max(int(sc.config.get(
        "PLANNING_TARGET_SHORTFALL_PENALTY_MULTIPLIER", 1) or 1), 1)
    terms += [planning_target_multiplier * weight_coef["w1_unmet"] * deficit
              for deficit in planning_stage_shortfall.values()]
    terms += [planning_target_multiplier * weight_coef["w1_unmet"] * deficit
              for deficit in planning_corridor_shortfall.values()]
    # Reserve preference is tonne-hours, not a bucket-count-dependent price.
    terms += [itn(weight_coef["w6_safety"] * dt) * s for s in short.values()]
    terms += [BIG_PENALTY * s for svs in surge_slacks.values() for s in svs]
    terms += [BIG_PENALTY * s for s in spill]
    # Search-only target slack lets CP-SAT start from a non-token incumbent.
    # It is expensive and visible, and any nonzero final value rejects the
    # candidate below. Unconfigured stages remain exact throughout.
    terms += [BIG_PENALTY * deficit
              for deficit in stage_target_shortfall.values()]
    # Small positive cost on every commanded tonne removes arbitrary
    # dispatch/transfer ties.  It is deliberately subordinate to unmet demand
    # and safety penalties; configured targets still force their exact useful
    # flow.
    flow_tonne_penalty = max(
        int(sc.config.get("FLOW_TONNE_PENALTY", 0) or 0), 0)
    if flow_tonne_penalty:
        terms += [flow_tonne_penalty * x for x in X.values()]
    active_bucket_penalty = max(
        int(sc.config.get("ACTIVE_BUCKET_PENALTY", 0) or 0), 0)
    if active_bucket_penalty:
        # Fine planning should not reserve six 15-minute slots for work that
        # can safely run in one.  This is a low-order tie-breaker on elapsed
        # machine occupation; tonnes, demand and safety remain authoritative.
        terms += [active_bucket_penalty * active for active in AG.values()]
    continuity_gap_penalty = operational_penalty(
        sc, "CONTINUITY_GAP_MINUTE_PENALTY")
    continuity_episode_penalty = operational_penalty(
        sc, "CONTINUITY_EPISODE_PENALTY")
    bucket_minutes = max(int(round(dt * 60.0)), 1)
    continuity_terms_start = len(terms)
    if continuity_gap_penalty:
        terms += [
            continuity_gap_penalty * bucket_minutes
            * demand_priority_at(destination_id,
                                 bucket + opts.horizon_offset) * gap
            for (destination_id, bucket), gap in feed_gap.items()]
    if continuity_episode_penalty:
        terms += [
            continuity_episode_penalty
            * demand_priority_at(destination_id,
                                 bucket + opts.horizon_offset) * episode
            for (destination_id, bucket), episode
            in feed_episode_start.items()]
    continuity_terms = terms[continuity_terms_start:]
    energy_coefficients = {
        ci: work_route_energy_coefficient(
            sc, candidate.display_kind(),
            candidate.path.links if candidate.path else ())
        for ci, candidate in enumerate(cands)
    }
    if bool(sc.config.get("ENERGY_OBJECTIVE_ENABLED", False)):
        energy_penalty = operational_penalty(
            sc, "ENERGY_OBJECTIVE_CENTIKWH_PENALTY")
        if energy_penalty:
            terms += [
                energy_penalty * energy_coefficients[ci] * x
                for (ci, _bucket), x in X.items()]
    # Secondary tie-breaker only: prefer assigning justified demand/
    # replenishment work across planning-eligible machines. Exact destination
    # demand and the terminal supply-position ceiling above prevent this
    # reward from manufacturing surplus tonnes just to activate equipment.
    coverage_reward = max(
        int(sc.config.get("MACHINE_COVERAGE_REWARD", 0) or 0), 0)
    if coverage_reward:
        terms += [-coverage_reward * used for used in machine_used.values()]
    if w.w4_giveaway > 0:
        blend_target_coef = _positive_scaled_weight(
            w.w4_giveaway, WSCALE / 10)
        terms += [blend_target_coef * priority * deviation
                  for priority, deviation in blend_target_deviation_terms]
        for ci, c in enumerate(cands):
            if c.kind != "reclaim":
                continue
            if c.space in variable_blend_space_ids:
                continue  # Actual blend excess is already priced by its variables.
            give = max(_candidate_effective_quality(
                sc, c).get("BPL", 0.0), 0.0)
            for t in range(T):
                tgt = next((s.target for s in demand_specs_at(
                    c.dest_id, t + opts.horizon_offset)
                    if s.param == "BPL" and s.target), None)
                if tgt is None:
                    continue
                coef = _positive_scaled_weight(
                    w.w4_giveaway * max(give - tgt, 0.0), WSCALE / 10)
                if coef > 0:
                    terms.append(coef * X[(ci, t)])
    if w.w7_utilisation > 0:
        for key, x in X.items():
            terms.append(-weight_coef["w7_utilisation"] * x)
    scheduling = scheduling_values(sc)
    service_first = scheduling["OBJECTIVE_MODE"] == "service_first"
    washing_cfg = washing.settings(sc)
    g10_pap_destinations = (
        set(washing_cfg["pap_destinations"]) if washing.enabled(sc) else set())
    raw_feed_expression = sum(
        itn(1000 * (1 - c.quality_override["moisture"] / 100)) * X[(ci, t)]
        for ci, c in enumerate(cands) if c.treatment_id for t in range(T))
    washing_cost_expression = sum(
        itn(float(washing_cfg["modes"][c.treatment_id]["cost_per_tsm_t"] or 0)
            * (1 - c.quality_override["moisture"] / 100)
            * float(washing_cfg["modes"][c.treatment_id]["dry_solids_yield"])
            * 1000) * X[(ci, t)]
        for ci, c in enumerate(cands) if c.treatment_id for t in range(T))
    terms.append(washing_cost_expression)
    qualified_service_expression = sum(
        demand_priority_at(did, t + opts.horizon_offset) * missing
        for (did, t), missing in qualified_unmet.items())
    include_process_service = bool(sc.config.get(
        "SERVICE_FIRST_INCLUDES_PROCESS_FEED", False))
    if include_process_service:
        qualified_service_expression += sum(
            demand_priority_at(did, t + opts.horizon_offset) * missing
            for (did, t), missing in unmet.items()
            if sc.destination(did).demand_kind == "process_feed")
    unqualified_dispatch_expression = sum(
        missing - unmet[key] for key, missing in qualified_unmet.items())
    emergency_expression = sum(spill) + sum(
        s for svs in surge_slacks.values() for s in svs) + sum(stage_target_shortfall.values())
    # This multiplier is derived from a finite bound, NOT an arbitrary large
    # weight: one emergency tonne outweighs ALL final service in this window.
    service_bound = sum(
        max(itn(demand_rate_at(d.id, t + opts.horizon_offset) * dt), 0)
        * demand_priority_at(d.id, t + opts.horizon_offset)
        for d in sc.destinations
        if include_process_service or d.demand_kind != "process_feed"
        for t in range(T))
    primary_expression = (
        (service_bound + 1) * emergency_expression + qualified_service_expression
        if service_first else sum(terms))
    m.minimize(primary_expression)

    _stage("solving CP-SAT")
    budget = float(opts.max_time_s or sc.sim.max_solve_s)
    solver = cp_model.CpSolver()
    # Deterministic replay (acceptance criterion #11): single worker, fixed seed
    # and a DETERMINISTIC-time budget. A wall-clock cutoff would stop the search
    # at a machine-load-dependent point and change the returned plan.
    #
    # SOLVE_MODE selects which guarantee the caller is buying. ``reproducible``
    # forces one worker: a CP-SAT portfolio races several strategies and returns
    # whichever wins, which OS thread scheduling decides and no seed can pin.
    reproducible = scheduling["SOLVE_MODE"] == "reproducible"
    solver.parameters.num_workers = 1 if reproducible else max(int(
        sc.config.get("CP_SAT_WORKERS", 1) or 1), 1)
    solver.parameters.max_deterministic_time = budget
    # hang-guard ONLY — never a pacing device: any wall cutoff that fires
    # before the deterministic budget truncates at a machine-load-dependent
    # point and silently breaks plan reproducibility (observed twice,
    # 15/16-Jul-2026, when solves ran under parallel CPU load)
    wall_budget = float(
        opts.max_wall_time_s if opts.max_wall_time_s is not None
        # In reproducible mode the guard must never bind before the
        # deterministic budget, so the service_first 55 s pacing cap is not
        # applied and no wall reserve is carved out below: later passes are
        # given DETERMINISTIC allowances instead.
        else budget * 100 if reproducible
        else min(budget * 100, 55.0) if service_first else budget * 100)
    solver.parameters.max_time_in_seconds = max(wall_budget, 0.1)
    if reproducible:
        pass
    elif sc.config.get("PARCEL_CONSOLIDATION_ENABLED", False) and short_parcel:
        # Both passes share the caller's wall limit. Reserve bounded polish
        # time instead of allowing an unbounded second solve after the guard.
        reserve = min(float(sc.config.get(
            "PARCEL_CONSOLIDATION_WALL_TIME_S", 15.0)), wall_budget / 2)
        solver.parameters.max_time_in_seconds = max(wall_budget - reserve, 0.1)
    elif service_first:
        reserve = min(float(scheduling["OBJECTIVE_SECONDARY_WALL_TIME_S"]), wall_budget / 2)
        solver.parameters.max_time_in_seconds = max(wall_budget - reserve, 0.1)
    solver.parameters.random_seed = int(sc.sim.seed)
    if (sc.config.get("_WASHING_PHYSICAL_SEED_FIRST_FEASIBLE", False)
            and not washing.enabled(sc)):
        solver.parameters.stop_after_first_solution = True
    if washing.enabled(sc):
        # The legacy dispatcher walks one grade per pile. Its suggested tonnes
        # can contradict the new per-material balances, and cannot be trusted
        # as a washing incumbent. Search the actual model without those hints.
        m.clear_hints()
        # Grade pools introduce many equivalent encodings; default probing
        # can spend the entire short daily guard before search even begins.
        # Disabling that presolve heuristic does not change the hard model.
        solver.parameters.cp_model_probing_level = 0
    # NOTE: this branch previously set ``repair_hint`` so CP-SAT would repair a
    # partial greedy hint rather than treat it as a complete incumbent. That
    # parameter runs a fixed search over the hinted variables, and when presolve
    # leaves nothing to branch on the native solver *aborts the process*
    # ("Check failed: heuristics.fixed_search != nullptr") — uncatchable from
    # Python. It is a warm-start optimisation, not a correctness feature, so it
    # stays off; an inconsistent hint is simply ignored by the solver instead.
    solver.parameters.log_search_progress = bool(opts.log)
    search_logs = []
    if opts.log and washing.enabled(sc):
        solver.parameters.log_to_stdout = False
        solver.log_callback = search_logs.append
    bootstrap_solver = None
    bootstrap_deterministic_time = 0.
    washing_bootstrap_note = ""
    primary_seed_branches = primary_seed_conflicts = 0
    if washing.enabled(sc) and len(cands) > 50:
        # Find a physics-feasible seed without the large qualified-service
        # objective. This uses the SAME hard model: no commitments, assays,
        # capacities or material balances are relaxed for the bootstrap.
        _stage("finding a material-balanced washing incumbent")
        from .washing_seed import physical_seed
        seed_started = time.monotonic()
        # Establish the always-auditable emergency incumbent first. This is
        # usually proven in presolve and prevents the richer physical mapper
        # from consuming most of a rolling window before any solution exists.
        verified_zero_model = m.clone()
        verified_zero_model.clear_hints()
        verified_zero_model.clear_objective()
        for variable in X.values():
            verified_zero_model.add(
                verified_zero_model.get_int_var_from_proto_index(
                    variable.index) == 0)
        verified_zero_solver = cp_model.CpSolver()
        verified_zero_solver.parameters.num_workers = 1
        verified_zero_solver.parameters.random_seed = int(sc.sim.seed)
        verified_zero_solver.parameters.max_deterministic_time = min(1., budget)
        verified_zero_solver.parameters.max_time_in_seconds = min(
            3., max(wall_budget / 4, .1))
        verified_zero_solver.parameters.stop_after_first_solution = True
        verified_zero_status = verified_zero_solver.solve(
            verified_zero_model)
        verified_zero_available = verified_zero_status in (
            cp_model.OPTIMAL, cp_model.FEASIBLE)
        seed_options = copy.deepcopy(opts)
        chevron_seed = bool(sc.config.get(
            "CHEVRON_STACKING", {}).get("enabled", False))
        # A source-share pre-washing campaign is also a coupled blend search.
        # Two-sided receiving intervals (notably Cd and CO2) can require the
        # blend to qualify even when neither source does independently, so the
        # former 2 s single-stream bootstrap routinely expired before finding
        # an incumbent. Reuse the established complex-blend budget; this does
        # not relax any hard constraint or alter the authoritative model.
        prewash_seed = bool(prewash_blending.enabled_points(sc))
        complex_blend_seed = chevron_seed or prewash_seed
        seed_options.max_time_s = min(
            4. if complex_blend_seed else 2., budget)
        seed_options.max_wall_time_s = min(
            20. if complex_blend_seed else 6., wall_budget / 2)
        seed_options.log = False
        flow_seed = physical_seed(sc, cands, _solve_uniform_plan, seed_options)
        if opts.log:
            print("Washing physical seed:",
                  ("mapped " + str(flow_seed[3]) if flow_seed is not None
                   else sc.config.get("_WASHING_SEED_DIAGNOSTIC")))
        feasibility_model = m.clone()
        feasibility_model.clear_hints()
        # Optimized pre-wash blending must retain the service objective here.
        # Clearing it and stopping at the first feasible solution selected the
        # verified all-idle emergency state, which is physically valid but a
        # useless warm start for an integrated operating week. The bootstrap
        # remains non-authoritative; it now spends a bounded part of the window
        # improving service before the full solve receives its incumbent.
        # Seed-only restriction: prohibit invented overflow only when the
        # verified aggregate precursor itself needed none. Grade-specific mine
        # arrivals can legitimately force bounded emergency overflow; in that
        # case the authoritative typed model must remain free to allocate the
        # same physical spill among assay pools.
        mapped_seed_metrics = flow_seed[3] if flow_seed is not None else {}
        if float(mapped_seed_metrics.get("spill_t", 0.0) or 0.0) <= 1e-6:
            feasibility_model.add(sum(spill) == 0)
        # Do not hard-fix PAP quality slack in the bootstrap. A legacy seed
        # has only grade labels and may initially choose Simple washing for a
        # TBT flow that needs Full washing to certify G10. The authoritative
        # model already prices every assay exception and remains free to
        # switch treatment mode; hardening those slacks made an otherwise
        # useful positive-flow seed infeasible and forced the all-idle fallback.
        if flow_seed is not None:
            seeded_x, seeded_y, _seeded_inventory, legacy_seed_metrics = flow_seed
            hinted_indices = set()

            def add_seed_hint(variable, value):
                if variable.index in hinted_indices:
                    return
                domain = m.proto.variables[variable.index].domain
                bounded = max(int(domain[0]), min(
                    int(round(value)), int(domain[-1])))
                feasibility_model.add_hint(
                    feasibility_model.get_int_var_from_proto_index(
                        variable.index), bounded)
                hinted_indices.add(variable.index)

            for key, value in seeded_x.items():
                if key in X:
                    add_seed_hint(X[key], value)
            for key, value in seeded_y.items():
                if key in Y:
                    add_seed_hint(Y[key], value)
            seeded_treatment_modes = dict(
                legacy_seed_metrics.get(
                    "treatment_modes_by_transfer", {}) or {})
            for (ci, _t), variable in X.items():
                candidate = cands[ci]
                selected_mode = seeded_treatment_modes.get(
                    candidate.transfer_id)
                if (candidate.treatment_id and selected_mode
                        and candidate.treatment_id != selected_mode):
                    feasibility_model.add(variable == 0)
            prewash_stages = {
                str(stage)
                for point in prewash_blending.enabled_points(sc).values()
                for stage in prewash_blending.effective_process_stages(point)
            }
            prewash_related_stages = set(prewash_stages)
            for stages in PROCESS_LINE_STAGE_MAP.values():
                if prewash_stages.intersection(stages):
                    prewash_related_stages.update(stages)
            if any(stage.startswith("LOU-WASH-")
                   for stage in prewash_stages):
                prewash_related_stages.add("LOU-ORE-STACK")
            pap_machines = {candidate.machine for candidate in cands
                            if candidate.kind == "reclaim" and
                            candidate.dest_id in washing_cfg["pap_destinations"]}
            for key, variable in X.items():
                ci, _t = key
                # Production/transport is seeded; final receivers remain
                # free to compete for shared reclaimers. Fixing the legacy
                # hourly DRY feed would silently occupy PAP's machine in
                # every bucket and preserve a zero-G10-service incumbent.
                seed_kind = cands[ci].kind
                fix_seed = (seed_kind not in ("reclaim", "dispatch", "stack") or
                            seed_kind == "reclaim" and cands[ci].machine not in pap_machines)
                if cands[ci].process_stage in prewash_related_stages:
                    fix_seed = False
                if fix_seed:
                    feasibility_model.add(variable == seeded_x.get(key, 0))
            for key, variable in Y.items():
                ci, _t = key
                if cands[ci].process_stage not in prewash_related_stages:
                    feasibility_model.add(variable == seeded_y.get(key, 0))
        else:
            # A failed label-only seed is not evidence that the typed model
            # is infeasible. Retain a bounded feasibility search over the
            # ORIGINAL hard constraints, without the mapped-seed restrictions
            # above (in particular, legitimate emergency spill may be needed).
            # This can establish a verified incumbent before the expensive
            # qualified-service objective search, even when mapping failed.
            feasibility_model = m.clone()
            feasibility_model.clear_hints()
        if verified_zero_available and flow_seed is None:
            # Start the bounded service bootstrap from a known feasible point
            # without making the no-command state the answer.  This avoids
            # spending the entire bootstrap merely rediscovering feasibility;
            # CP-SAT is free to change every hinted variable while optimizing
            # the retained service objective.
            for index, variable in enumerate(m.proto.variables):
                feasibility_model.add_hint(
                    feasibility_model.get_int_var_from_proto_index(index),
                    verified_zero_solver.value(
                        verified_zero_model.get_int_var_from_proto_index(
                            index)))
        seed_solver = cp_model.CpSolver()
        seed_solver.parameters.num_workers = 1
        seed_solver.parameters.random_seed = int(sc.sim.seed)
        legacy_seed_deterministic = (legacy_seed_metrics["deterministic_time"]
                                     if flow_seed is not None else 0.)
        seed_solver.parameters.max_deterministic_time = min(
            1. if flow_seed is not None else 3.,
            max(budget - legacy_seed_deterministic, .1))
        seed_solver.parameters.max_time_in_seconds = min(
            5. if flow_seed is not None else
            20. if complex_blend_seed else 2., wall_budget / 2)
        seed_solver.parameters.stop_after_first_solution = False
        seed_solver.parameters.cp_model_probing_level = 0
        # repair_hint intentionally left off — see the note at the main solve.
        seed_logs = []
        if opts.log:
            seed_solver.parameters.log_search_progress = True
            seed_solver.parameters.log_to_stdout = False
            seed_solver.log_callback = seed_logs.append
        seed_status = seed_solver.solve(feasibility_model) if feasibility_model is not None else cp_model.UNKNOWN
        seed_mode = ("mapped physical seed" if flow_seed is not None
                     else "unfixed typed feasibility")
        if (seed_status == cp_model.INFEASIBLE and flow_seed is not None
                and not verified_zero_available):
            # A label-only schedule is a search suggestion, never an
            # authoritative restriction. Grade splitting, moisture-dependent
            # capacities and exact typed yields can make its fixed projection
            # inconsistent even though the full model is feasible. Retry the
            # original hard model with the mapped positive flows as repairable
            # hints; CP-SAT may alter every hinted value.
            repair_model = m.clone()
            repair_model.clear_hints()

            repair_hint_indices = set()

            def add_repair_hint(variable, value):
                if variable.index in repair_hint_indices:
                    return
                domain = m.proto.variables[variable.index].domain
                bounded = max(int(domain[0]), min(int(round(value)),
                                                   int(domain[-1])))
                repair_model.add_hint(
                    repair_model.get_int_var_from_proto_index(variable.index),
                    bounded)
                repair_hint_indices.add(variable.index)

            for key, value in seeded_x.items():
                if value > 0 and key in X:
                    add_repair_hint(X[key], value)
            for key, value in seeded_y.items():
                if value > 0 and key in Y:
                    add_repair_hint(Y[key], value)
            feasibility_model = repair_model
            seed_solver = cp_model.CpSolver()
            seed_solver.parameters.num_workers = 1
            seed_solver.parameters.random_seed = int(sc.sim.seed)
            seed_solver.parameters.max_deterministic_time = min(
                3., max(budget - legacy_seed_deterministic, .1))
            seed_solver.parameters.max_time_in_seconds = min(
                20. if complex_blend_seed else 2., wall_budget / 2)
            seed_solver.parameters.stop_after_first_solution = False
            seed_solver.parameters.cp_model_probing_level = 0
            # repair_hint intentionally left off — see the note at the main
            # solve. The mapped positive flows are still supplied as hints.
            seed_logs = []
            if opts.log:
                seed_solver.parameters.log_search_progress = True
                seed_solver.parameters.log_to_stdout = False
                seed_solver.log_callback = seed_logs.append
            seed_status = seed_solver.solve(feasibility_model)
            seed_mode = "repairable physical hint"
        if seed_status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            # Forecast-driven stage/corridor targets and final demand already
            # own explicit shortfall variables. Therefore a no-command plan is
            # a legitimate emergency incumbent whenever fixed work and hard
            # inventory conditions permit it: outside arrivals may use only
            # their bounded overflow, while every unserved tonne remains in
            # the result. Verify that state against the SAME hard model so an
            # expensive typed search never returns UNKNOWN without a usable
            # fallback merely because it could not discover the origin.
            if verified_zero_available:
                feasibility_model = verified_zero_model
                seed_solver = verified_zero_solver
                seed_status = verified_zero_status
                seed_mode = "verified no-command fallback"
        washing_bootstrap_note = (
            "WASHING_BOOTSTRAP "
            + seed_mode
            + f": {seed_solver.status_name(seed_status)}"
            + ("; physical seed unavailable: " + str(sc.config.get(
                "_WASHING_SEED_DIAGNOSTIC", "no compatible mapping"))
               if flow_seed is None else ""))
        if opts.log and feasibility_model is not None:
            print("Washing feasibility bootstrap: " + seed_solver.response_stats())
            print("\n".join(line for line in seed_logs[-10:] if "constraint #" in line or "INFEASIBLE" in line))
            if seed_status == cp_model.INFEASIBLE and flow_seed is not None:
                assigned = {variable.index: seeded_x.get(key, 0) for key, variable in X.items()}
                assigned.update({variable.index: seeded_y.get(key, 0) for key, variable in Y.items()})
                assigned.update({variable.index: int(any(seeded_x.get((ci, t), 0)
                                    for ci in group_members[gk])) for (gk, t), variable in AG.items()})
                assigned.update({variable.index: _seeded_inventory.get(key, 0)
                                 for key, variable in material_inv.items()})
                assigned.update({variable.index: sum(value for (sid, _mid, tt), value in _seeded_inventory.items()
                                                     if sid == key[0] and tt == key[1])
                                 for key, variable in inv.items()})
                shown = 0
                for index, constraint in enumerate(m.proto.constraints):
                    if not constraint.has_linear():
                        continue
                    literals = list(constraint.enforcement_literal)
                    if not all((literal if literal >= 0 else -literal - 1) in assigned for literal in literals):
                        continue
                    if any((assigned[literal] == 0 if literal >= 0 else assigned[-literal - 1] == 1)
                           for literal in literals):
                        continue
                    if not all(variable in assigned for variable in constraint.linear.vars):
                        continue
                    value = sum(assigned[variable] * coefficient for variable, coefficient
                                in zip(constraint.linear.vars, constraint.linear.coeffs))
                    bounds = list(constraint.linear.domain)
                    if not any(lo <= value <= hi for lo, hi in zip(bounds[::2], bounds[1::2])):
                        print("Washing seed fixed residual:", index, value, bounds,
                              [(m.proto.variables[variable].name, assigned[variable], coefficient)
                               for variable, coefficient in zip(constraint.linear.vars, constraint.linear.coeffs)])
                        shown += 1
                        if shown == 5:
                            break
        seed_elapsed = time.monotonic() - seed_started
        bootstrap_deterministic_time = (float(seed_solver.response_proto.deterministic_time)
                                        if feasibility_model is not None else 0.)
        if flow_seed is not None:
            bootstrap_deterministic_time += legacy_seed_metrics["deterministic_time"]
            primary_seed_branches = legacy_seed_metrics["branches"] + seed_solver.num_branches
            primary_seed_conflicts = legacy_seed_metrics["conflicts"] + seed_solver.num_conflicts
        if not reproducible:
            # Charging the bootstrap's WALL time against the main solve makes
            # the main solve's length machine-load dependent. The deterministic
            # subtraction on the next line is the reproducible equivalent and
            # is applied in both modes.
            solver.parameters.max_time_in_seconds = max(
                solver.parameters.max_time_in_seconds - seed_elapsed, .1)
        solver.parameters.max_deterministic_time = max(budget - bootstrap_deterministic_time, .01)
        if seed_status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            bootstrap_solver = seed_solver
            if (flow_seed is not None
                    and seed_mode == "verified no-command fallback"):
                # The typed bootstrap could not validate a complete incumbent
                # inside its bounded slice, but the mapper still supplied a
                # positive, material-balanced route schedule. Prefer those
                # repairable work hints to a complete all-zero hint; otherwise
                # the authoritative search spends the remaining window proving
                # around the emergency idle state and never explores treatment.
                m.clear_hints()
                hinted = set()
                for values, variables in ((seeded_x, X), (seeded_y, Y)):
                    for key, value in values.items():
                        variable = variables.get(key)
                        if variable is None or variable.index in hinted:
                            continue
                        domain = m.proto.variables[variable.index].domain
                        bounded = max(int(domain[0]), min(
                            int(round(value)), int(domain[-1])))
                        m.add_hint(variable, bounded)
                        hinted.add(variable.index)
                # A partial hint is guidance, not a fixed solution. Enabling
                # OR-Tools' repaired-hint mode together with the parallel
                # portfolio can hit an internal fixed-search assertion in a
                # late rolling window. Normal search can still use the hint
                # and avoids that native crash.
            else:
                for index, variable in enumerate(m.proto.variables):
                    m.add_hint(m.get_int_var_from_proto_index(index),
                               seed_solver.value(feasibility_model.get_int_var_from_proto_index(index)))
    if opts.log:
        hinted = set(m.proto.solution_hint.vars)
        missing_hint_names = [
            variable.name for index, variable in enumerate(m.proto.variables)
            if index not in hinted and len(variable.domain) > 2
            or (index not in hinted and variable.domain[0] != variable.domain[-1])
        ]
        if missing_hint_names:
            print("Unhinted model variables: "
                  + ", ".join(missing_hint_names[:100]))
    status = solver.solve(m)
    if search_logs:
        print("\n".join(search_logs[-20:]))
    primary_bound = float(solver.best_objective_bound)
    primary_branches = int(solver.num_branches) + primary_seed_branches
    primary_conflicts = int(solver.num_conflicts) + primary_seed_conflicts
    primary_deterministic_time = float(
        solver.response_proto.deterministic_time) + bootstrap_deterministic_time
    if status == cp_model.UNKNOWN and bootstrap_solver is not None:
        # A feasible seed is usable, but it is not an optimal service result.
        solver = bootstrap_solver
        status = cp_model.FEASIBLE
    consolidation = None
    wall = time.time() - wall_start
    sname = {cp_model.OPTIMAL: "OPTIMAL", cp_model.FEASIBLE: "FEASIBLE",
             cp_model.INFEASIBLE: "INFEASIBLE", cp_model.MODEL_INVALID: "ERROR",
             cp_model.UNKNOWN: "UNKNOWN"}.get(status, "ERROR")

    incumbent_available = sname in ("OPTIMAL", "FEASIBLE")
    objective_value = (
        float(solver.value(primary_expression)) if incumbent_available else 0.0)
    best_bound = (
        primary_bound if incumbent_available else 0.0)
    relative_gap_pct = (
        100.0 * abs(objective_value - best_bound)
        / max(abs(objective_value), 1.0)
        if incumbent_available else 0.0)
    res = PlanResult(
        status=sname, solver="cp-sat", solver_wall_s=round(wall, 3),
        solver_optimality_scope=("primary_emergency_and_qualified_service" if service_first else "whole_plan"),
        solver_best_bound=best_bound,
        solver_relative_gap_pct=relative_gap_pct,
        solver_branches=(primary_branches + (
            consolidation.branches if consolidation else 0)),
        solver_conflicts=(primary_conflicts + (
            consolidation.conflicts if consolidation else 0)),
        solver_deterministic_time=(primary_deterministic_time + (
            consolidation.deterministic_time if consolidation else 0)),
        horizon_offset=opts.horizon_offset,
        initial_machine_groups={
            machine_id: tuple(group)
            for machine_id, group in opts.initial_machine_groups.items()
            if isinstance(group, (list, tuple)) and group},
        initial_feed_gap=dict(opts.initial_feed_gap),
        initial_open_feed_gap_minutes=dict(
            opts.initial_open_feed_gap_minutes))
    if fixed_missing:
        res.reasons.append("FIXED_JOB_UNMATCHED: no candidate tuple for fixed job(s) "
                           + ", ".join(fixed_missing) + " — check reachability and routes")
    if washing_bootstrap_note:
        res.reasons.append(washing_bootstrap_note)
    if sname in ("INFEASIBLE", "ERROR", "UNKNOWN"):
        hard_continuity = any(
            policy.scored and policy.max_gap_min > 0
            for policy in continuity_policies.values())
        if hard_continuity:
            res.reasons.append(
                f"SOLVER_{sname}: check fixed jobs, calendars, route "
                "availability and hard continuous-feed max-gap policies; "
                "unmet-tonne slack does not relax a configured max gap")
        else:
            res.reasons.append(
                f"SOLVER_{sname}: check fixed jobs vs calendars, and route "
                "availability, exact production/terminal-stock commitments "
                "and configured minimum job volume; unmet final-order slack "
                "does not relax those hard constraints")
        if float(sc.config.get("MIN_JOB_VOLUME_T", 0.0)) > 0:
            res.reasons.append(
                f"MIN_JOB_VOLUME_T={sc.config['MIN_JOB_VOLUME_T']:g}: "
                "hard complete-job minimum; disable with 0 when reviewing conflicts")
        res.relaxations += ["Remove or shorten operator-fixed jobs",
                            "Re-enable unavailable machines/links",
                            "Reduce frozen-horizon length"]
        if hard_continuity:
            res.relaxations.append(
                "Increase the continuous-feed max_gap_min or set it to 0 "
                "until an enforceable outage ceiling is approved")
        return res

    base_sc = sc
    def extract_timed_jobs(candidate_solver):
        sc = copy.deepcopy(base_sc) if blend_target_vars_by_space else base_sc
        # Materialize the jointly solved whole-tonne matrix on this private
        # planning scenario before extracting jobs. This gives every contributing
        # stack job the same auditable matrix and lets later rolling windows treat
        # a physically completed construction campaign as a fixed-quality pile.
        if blend_target_vars_by_space:
            raw_blend_config = sc.config.get(CHEVRON_CONFIG_KEY, {})
            raw_blend_config = (
                dict(raw_blend_config)
                if isinstance(raw_blend_config, dict) else {})
            proposals = dict(raw_blend_config.get(
                "proposed_recipes_by_space", {}) or {})
            proposed_matrices = dict(raw_blend_config.get(
                "proposed_matrix_by_space", {}) or {})
            proposed_campaigns = dict(raw_blend_config.get(
                "proposed_campaigns_by_space", {}) or {})
            for space_id, targets in blend_target_vars_by_space.items():
                batch_t = (int(candidate_solver.value(
                    dynamic_blend_decisions[space_id]["total"]))
                    if space_id in dynamic_blend_decisions
                    else chevron_construction_batch_t(sc, space_id))
                if batch_t <= 0:
                    proposals.pop(space_id, None)
                    proposed_matrices.pop(space_id, None)
                    proposed_campaigns.pop(space_id, None)
                    continue
                allocation = []
                for grade_id, target in targets.items():
                    tonnage_t = int(candidate_solver.value(target))
                    if tonnage_t <= 0:
                        continue
                    allocation.append({
                        "grade_id": grade_id,
                        "target_fraction": tonnage_t / float(batch_t),
                        "tonnage_t": tonnage_t,
                    })
                proposals[space_id] = allocation
                selectors = blend_matrix_selection_vars_by_space.get(
                    space_id, {})
                selected_matrix = next((
                    matrix_id for matrix_id, selector in selectors.items()
                    if int(candidate_solver.value(selector)) == 1), "")
                if not selected_matrix and (fixed_optimized_proposals or
                        chevron_campaign_state(sc, space_id).get("resolved_recipe")):
                    selected_matrix = str(chevron_campaign_state(sc, space_id).get(
                        "selected_matrix", "") or proposed_matrices.get(space_id, ""))
                if selected_matrix:
                    proposed_matrices[space_id] = selected_matrix
                else:
                    proposed_matrices.pop(space_id, None)
                if space_id in dynamic_blend_decisions:
                    decision = dynamic_blend_decisions[space_id]
                    fixed_campaign = (dict(proposed_campaigns.get(space_id, {}))
                                      if fixed_optimized_proposals else {})
                    completion = next((
                        window_start_min + t * step_min
                        for t, flag in decision["ready"].items()
                        if candidate_solver.value(flag)), None)
                    result_grades = sorted({
                        demand_grade_at(cands[ci].dest_id, opts.horizon_offset + t)
                        for ci in decision["reclaim_indices"] for t in range(T)
                        if candidate_solver.value(X[ci, t]) > 0
                        and demand_grade_at(cands[ci].dest_id, opts.horizon_offset + t)
                    })
                    intervals = {}
                    for ci in decision["reclaim_indices"]:
                        destination_id = cands[ci].dest_id
                        for t in range(T):
                            if candidate_solver.value(X[ci, t]) <= 0:
                                continue
                            absolute_bucket = opts.horizon_offset + t
                            requested = demand_grade_at(destination_id, absolute_bucket)
                            specs = demand_specs_at(destination_id, absolute_bucket)
                            signature = (destination_id, requested, tuple(
                                (s.param, s.pmin, s.pmax, s.target, s.hard, s.margin)
                                for s in specs))
                            interval = intervals.setdefault(signature, {
                                "destination": destination_id, "requested_grade": requested,
                                "buckets": [], "specs": [{
                                    "parameter": s.param,
                                    "minimum": None if s.pmin is None else s.pmin + s.margin,
                                    "maximum": None if s.pmax is None else max(s.pmax - s.margin, 0),
                                    "target": s.target, "hard": bool(s.hard),
                                } for s in specs],
                            })
                            if absolute_bucket not in interval["buckets"]:
                                interval["buckets"].append(absolute_bucket)
                    proposed_campaigns[space_id] = {
                        "campaign_id": (
                            chevron_campaign_state(sc, space_id).get("campaign_id")
                            or (proposed_campaigns.get(space_id, {}).get("campaign_id")
                                if fixed_optimized_proposals else "")
                            or f"{space_id}@{window_start_min}"),
                        "planned_from_minute": window_start_min,
                        "batch_t": batch_t, "total_t": batch_t,
                        "ready_minute": (completion if completion is not None
                                         else fixed_campaign.get("ready_minute")),
                        "result_grade": (result_grades[0] if len(result_grades) == 1
                                         else f"BLEND-{space_id}" if result_grades
                                         else fixed_campaign.get("result_grade", f"BLEND-{space_id}")),
                        "served_grades": result_grades or fixed_campaign.get("served_grades", []),
                        "served_receiving_intervals": list(intervals.values()) or fixed_campaign.get(
                            "served_receiving_intervals", []),
                        "served_destinations": sorted({
                            cands[ci].dest_id for ci in decision["reclaim_indices"]
                            if any(candidate_solver.value(X[ci, t]) > 0 for t in range(T))})
                            or fixed_campaign.get("served_destinations", []),
                        "selected_matrix": selected_matrix,
                    }
            raw_blend_config["proposed_recipes_by_space"] = proposals
            raw_blend_config["proposed_matrix_by_space"] = proposed_matrices
            raw_blend_config["proposed_campaigns_by_space"] = proposed_campaigns
            sc.config[CHEVRON_CONFIG_KEY] = raw_blend_config
    
    
        _stage("preparing results")
        # ---------- extraction (one job per group-run) ----------------------------------
        val = candidate_solver.value
        jobs: List[Job] = []
        jid = 0
        for gk in group_keys:
            members = group_members[gk]
            c0 = cands[members[0]]
            t = 0
            while t < T:
                tot_t = sum(val(X[(ci, t)]) for ci in members)
                if val(AG[(gk, t)]) == 1 and tot_t > 0:
                    t1 = t
                    tot = 0
                    output_tot = 0
                    link_flow: Dict[Tuple[str, ...], int] = {}
                    bucket_flows: List[JobBucketFlow] = []
                    fixed_job = _fixed_for_bucket(
                        sc, gk, t + opts.horizon_offset)
                    fixed_id = fixed_job.id if fixed_job is not None else None
                    rate_signature = tuple(round(
                        _candidate_effective_capacity_tph(
                            sc, cands[ci], t + opts.horizon_offset,
                            opts.machine_rate_factor.get(gk[1], 1.0)), 9)
                        for ci in members)
                    while t1 < T and val(AG[(gk, t1)]) == 1 and \
                            sum(val(X[(ci, t1)]) for ci in members) > 0 and \
                            tuple(round(_candidate_effective_capacity_tph(
                                sc, cands[ci], t1 + opts.horizon_offset,
                                opts.machine_rate_factor.get(gk[1], 1.0)), 9)
                                  for ci in members) == rate_signature and \
                            ((_fixed_for_bucket(
                                sc, gk, t1 + opts.horizon_offset).id
                              if _fixed_for_bucket(
                                  sc, gk, t1 + opts.horizon_offset) is not None
                              else None) == fixed_id):
                        for ci in members:
                            v = val(X[(ci, t1)])
                            if v > 0:
                                output_v = (val(Y[(ci, t1)])
                                            if (ci, t1) in Y else v)
                                path = (list(cands[ci].path.links)
                                        if cands[ci].path else [])
                                bucket_flows.append(JobBucketFlow(
                                    bucket=t1 + opts.horizon_offset,
                                    tonnage_t=float(v),
                                    path=path,
                                    output_tonnage_t=float(output_v)))
                                if cands[ci].path:
                                    key = tuple(cands[ci].path.links)
                                    link_flow[key] = link_flow.get(key, 0) + v
                                output_tot += output_v
                            tot += v
                        t1 += 1
                    sp = sc.space(gk[2])
                    # x-cut convention: reclaim cuts sized against the START-OF-RUN
                    # inventory the solver itself computed (uniform-density
                    # approximation shared with the ASP inventory rule) — NOT the
                    # scenario snapshot, which is stale for piles filled by
                    # in-horizon stacking (e.g. initially-empty HP-07/08) and
                    # would draw a full-lane reclaim face colliding with the
                    # concurrent stacking zone. Stack jobs are sized against
                    # capacity (they add material at the high-x end).
                    display_kind = c0.display_kind()
                    basis = sp.capacity_t if display_kind == "stack" \
                        else max(float(val(inv[(sp.id, t)])), 1.0)
                    # 2 % headroom + ceil-rounding so the cut can never be a few
                    # centimetres short of the planned tonnage (uniform density)
                    import math as _math
                    frac = min(tot / max(basis, 1) * 1.02, 1.0)
                    cut = _math.ceil((sp.x_from + frac * (sp.x_to - sp.x_from)) * 10) / 10
                    # work-zone convention for SAFE same-pile concurrency
                    # (USER-CONFIRMED rule, 15-Jul-2026): reclaim/dispatch work the
                    # LOW-x face; stacking builds from the HIGH-x end, so
                    # concurrent zones sit at opposite ends with the separation
                    # room guaranteed by the in-model condition above
                    zone_len = max(cut - sp.x_from, 1.0)
                    auto_start_x = (
                        round(max(sp.x_to - zone_len, sp.x_from), 1)
                        if display_kind == "stack" else round(sp.x_from, 1))
                    auto_stop_x = (
                        round(sp.x_to, 1) if display_kind == "stack"
                        else max(cut, round(sp.x_from + 1.0, 1)))
                    if fixed_job is not None and \
                            fixed_job.start_repere is not None and \
                            fixed_job.stop_repere is not None:
                        rx0, rx1 = repere_interval_to_x(
                            sp, fixed_job.start_repere, fixed_job.stop_repere)
                        start_x, stop_x = round(rx0, 3), round(rx1, 3)
                        range_note = " · " + format_repere_range(
                            fixed_job.start_repere, fixed_job.stop_repere)
                    else:
                        start_x, stop_x = auto_start_x, auto_stop_x
                        range_note = ""
                    # dominant path for execution; all used links recorded for routing
                    dom = max(link_flow.items(), key=lambda kv: kv[1])[0] \
                        if link_flow else tuple()
                    used_links = sorted({li for k in link_flow for li in k})
                    # Persist the exact effective ceiling represented by CP-SAT's
                    # integer tonnes/bucket constraint (including its rounding),
                    # not the unrounded floating-point factor.
                    execution_rate_cap = min(
                        itn(_candidate_effective_capacity_tph(
                            sc, c0, bucket + opts.horizon_offset,
                            opts.machine_rate_factor.get(gk[1], 1.0)) * dt)
                        / max(dt, 1e-9)
                        for bucket in range(t, t1))
                    if fixed_job is not None and fixed_job.rate_tph > 0:
                        execution_rate_cap = min(
                            execution_rate_cap,
                            itn(fixed_job.rate_tph * dt) / max(dt, 1e-9))
                    jid += 1
                    target_space = (
                        c0.target_space if c0.kind == "process"
                        else gk[2] if display_kind == "stack" else "")
                    job_grade = ((fixed_job.grade if fixed_job is not None else "")
                                 or gk[4] or sp.grade)
                    if display_kind == "reclaim":
                        job_grade = chevron_effective_grade(
                            sc, gk[2]) or job_grade
                    material_flows = []
                    if c0.material_id:
                        catalogue = sc.config["_WASHING_MODEL_CATALOG"]
                        for ci in members:
                            candidate = cands[ci]
                            for bucket in range(t, t1):
                                amount = float(_val_expr(candidate_solver, X[(ci, bucket)]))
                                if amount <= 0:
                                    continue
                                output = float(_val_expr(candidate_solver, Y[(ci, bucket)])) if candidate.kind == "process" else amount
                                source_record = catalogue[candidate.material_id]
                                output_record = catalogue[candidate.output_material_id]
                                material_flows.append(dict(
                                    bucket=bucket + opts.horizon_offset,
                                    path=list(candidate.path.links),
                                    material_id=candidate.material_id,
                                    output_material_id=candidate.output_material_id,
                                    grade=source_record["grade_id"],
                                    output_grade=output_record["grade_id"],
                                    tonnage_t=amount, output_tonnage_t=output,
                                    quality=_candidate_effective_quality(sc, candidate),
                                    output_quality=copy.deepcopy(output_record["quality"]),
                                    treatment_id=candidate.treatment_id,
                                    certified_grade=(
                                        "G10" if candidate.kind == "reclaim"
                                        and candidate.dest_id in
                                        g10_pap_destinations
                                        else ""),
                                    material_class=source_record["material_class"],
                                    output_material_class=output_record["material_class"]))
                        job_grade = material_flows[0]["grade"]
                    blend_meta = (
                        chevron_job_metadata(sc, target_space, job_grade)
                        if display_kind == "stack" else
                        {
                            "method": "reclaim",
                            "blend_mode": "pre-constructed stockpile blend",
                            "blend_recipe": chevron_recipe(sc, gk[2]),
                            "chevron_passes_per_cycle": 0,
                            "chevron_grade_passes": 0,
                        }
                        if display_kind == "reclaim" and (
                            chevron_enabled_for_space(sc, gk[2]) or
                            chevron_campaign_is_complete(sc, gk[2])) else
                        {"method": "bench", "blend_mode": "",
                         "blend_recipe": [], "chevron_passes_per_cycle": 0,
                         "chevron_grade_passes": 0})
                    jobs.append(Job(
                        id=(fixed_job.id if fixed_job is not None
                            else f"J{opts.horizon_offset:03d}-{jid:03d}"),
                        machine=gk[1], kind=display_kind, space=gk[2],
                        dest_id=("" if c0.kind == "process" else gk[3]),
                        grade=job_grade,
                        rate_tph=round(tot / ((t1 - t) * dt), 1),
                        tonnage_t=float(tot),
                        start_bucket=t + opts.horizon_offset,
                        end_bucket=t1 - 1 + opts.horizon_offset,
                        start_minute=itn(
                            (t + opts.horizon_offset) * dt * 60.0),
                        end_minute=itn(
                            (t1 + opts.horizon_offset) * dt * 60.0),
                        path=list(dom),
                        exit_node=c0.exit_node,
                        method=str(blend_meta["method"]),
                        blend_mode=str(blend_meta["blend_mode"]),
                        blend_recipe=list(blend_meta["blend_recipe"]),
                        blend_campaign=copy.deepcopy(sc.config.get(
                            CHEVRON_CONFIG_KEY, {}).get("proposed_campaigns_by_space", {}).get(
                                target_space if display_kind == "stack" else gk[2], {}))
                            if blend_meta["blend_recipe"] and chevron_is_demand_driven(sc) else {},
                        chevron_passes_per_cycle=int(
                            blend_meta["chevron_passes_per_cycle"]),
                        chevron_grade_passes=int(
                            blend_meta["chevron_grade_passes"]),
                        origin="HUMAN" if fixed_job is not None else "AUTO",
                        locked=fixed_job is not None,
                        start_x=start_x, stop_x=stop_x,
                        execution_rate_cap_tph=float(execution_rate_cap),
                        bucket_flows=sorted(
                            bucket_flows,
                            key=lambda flow: (flow.bucket, tuple(flow.path))),
                        source_space=(c0.source_space if c0.kind == "process"
                                      else gk[2]
                                      if display_kind in ("reclaim", "dispatch")
                                      else ""),
                        target_space=(c0.target_space if c0.kind == "process"
                                      else gk[2] if display_kind == "stack"
                                      else gk[3]),
                        output_grade=(c0.output_grade if c0.kind == "process"
                                      else "G10" if c0.kind == "reclaim"
                                      and gk[3] in g10_pap_destinations
                                      else (gk[4] or sp.grade)),
                        certified_grade=(
                            "G10" if c0.kind == "reclaim"
                            and gk[3] in g10_pap_destinations
                            else ""),
                        output_tonnage_t=float(
                            output_tot if c0.kind == "process" else tot),
                        process_stage=c0.process_stage,
                        depends_on_stage=c0.depends_on_stage,
                        dependency_delay_min=c0.dependency_delay_min,
                        residence_min=c0.residence_min,
                        purpose=c0.purpose,
                        transfer_id=c0.transfer_id,
                        mass_conversion=c0.mass_conversion,
                        treatment_id=c0.treatment_id,
                        material_flows=material_flows,
                        comment=(f"{'HUMAN' if fixed_job is not None else 'AUTO'} "
                                 f"{display_kind} "
                                 f"{c0.source_space or gk[2]}"
                                 f" -> {c0.target_space or gk[3]}"
                                + (f" via {len(used_links)} links" if len(link_flow) > 1
                                   else "")
                                 + range_note
                                 + (f" · {washing_cfg['modes'][c0.treatment_id]['name']} · DEMO" if c0.treatment_id else ""))))
                    t = t1
                else:
                    t += 1
        timing_refinement = refine_execution_timing(sc, jobs)
        update_chevron_job_passes(sc, jobs)
        independent_issues = validate_execution_windows(sc, jobs)
        timing_refinement.issues.extend(issue for issue in independent_issues
                                        if issue not in timing_refinement.issues)
        return jobs, timing_refinement, sc


    execution_cache = {}
    def execution(candidate_solver):
        if candidate_solver not in execution_cache:
            candidate_jobs, candidate_timing, candidate_sc = extract_timed_jobs(candidate_solver)
            held = {}
            for (mid, t), hold in machine_hold.items():
                if _val_expr(candidate_solver, hold):
                    held.setdefault(mid, []).append(t + opts.horizon_offset)
            metrics = executed_transition_metrics(
                candidate_sc, candidate_jobs,
                horizon_start_minute=opts.horizon_offset * max(int(round(dt * 60)), 1),
                horizon_end_minute=(opts.horizon_offset + T) * max(int(round(dt * 60)), 1),
                initial_machine_groups=opts.initial_machine_groups,
                machine_hold_buckets=held)
            execution_cache[candidate_solver] = (candidate_jobs, candidate_timing, candidate_sc, metrics)
        return execution_cache[candidate_solver]

    def accept_execution_candidate(candidate_solver, incumbent):
        candidate_jobs, candidate_timing, _, candidate_metrics = execution(candidate_solver)
        if not candidate_timing.valid or any(
                window.status != "VALIDATED"
                for job in candidate_jobs for window in job.execution_windows):
            return "MINUTE_EXECUTION"
        baseline_metrics = execution(incumbent)[3]
        incumbent_jobs, incumbent_timing, _, _ = execution(incumbent)
        if not incumbent_timing.valid or any(
                window.status != "VALIDATED" for job in incumbent_jobs for window in job.execution_windows):
            # Counters on a rejected hourly fallback are not executable
            # operating promises. First establish a VALID minute incumbent.
            return ""
        for metric in ("executed_starts", "executed_stops", "executed_changeovers"):
            if candidate_metrics[metric] > baseline_metrics[metric]:
                return metric.upper()
        hold_cost = operational_penalty(base_sc, "EQUIPMENT_HOLD_BUCKET_PENALTY")
        def operating_cost(metrics):
            return sum(operational_penalty(base_sc, key) * metrics[metric]
                       for key, metric in (
                           ("EQUIPMENT_START_PENALTY", "executed_starts"),
                           ("EQUIPMENT_STOP_PENALTY", "executed_stops"),
                           ("EQUIPMENT_CHANGEOVER_PENALTY", "executed_changeovers"))) + hold_cost * metrics["executed_hold_minutes"] / 60
        if operating_cost(candidate_metrics) > operating_cost(baseline_metrics) + 1e-9:
            return "MINUTE_OPERATING_COST"
        return ""

    def accept_quality_candidate(candidate_solver, incumbent):
        candidate_jobs, candidate_timing, _, _ = execution(candidate_solver)
        return ("MINUTE_EXECUTION" if not candidate_timing.valid or any(
            window.status != "VALIDATED" for job in candidate_jobs for window in job.execution_windows) else "")

    def repair_execution_constraints(candidate_model, candidate_solver):
        candidate_jobs, timing, _, _ = execution(candidate_solver)
        roots = [(issue.job_id, issue.bucket) for issue in timing.issues
                 if issue.severity == "ERROR" and issue.code == "MACHINE_POSITIONING_GAP"
                 and issue.job_id and issue.bucket is not None]
        cuts = 0
        for jid, bucket in sorted(set(roots)):
            job = next((job for job in candidate_jobs if job.id == jid), None)
            if job is None:
                continue
            t = bucket - opts.horizon_offset
            if not 0 <= t < T:
                continue
            groups = [gk for gk in group_keys
                      if gk[1] == job.machine and gk[2] == job.space
                      and cands[group_members[gk][0]].display_kind() == job.kind
                      and _val_expr(candidate_solver, AG[(gk, t)])]
            for gk in groups:
                previous = next((other for other in mach_groups[gk[1]]
                                 if t > 0 and _val_expr(candidate_solver, AG[(other, t - 1)])), None)
                if previous is not None:
                    # Exclude the incompatible adjacent assignment, not the
                    # machine or campaign altogether. Other hours/routes and
                    # the incumbent's service protections remain available.
                    candidate_model.add_bool_or([AG[(gk, t)].Not(), AG[(previous, t - 1)].Not()])
                else:
                    candidate_model.add(AG[(gk, t)] == 0)
                cuts += 1
        return cuts

    objective_passes = None
    if service_first:
        _stage("refining protected continuity and operating objectives")
        secondary_started = time.monotonic()
        primary_service_ceiling = int(_val_expr(solver, qualified_service_expression)) + int(
            math.floor(float(scheduling["SERVICE_DEGRADATION_T"])))
        repair_pass = None
        incumbent_jobs, incumbent_timing, _, _ = execution(solver)
        if not incumbent_timing.valid or any(
                window.status != "VALIDATED" for job in incumbent_jobs for window in job.execution_windows):
            _stage("repairing rejected primary minute execution")
            repair_pass = refine_objectives(
                m, solver, levels=[("qualified_service", qualified_service_expression,
                                   primary_service_ceiling - int(_val_expr(solver, qualified_service_expression))),
                                  ("execution_repair", sum(terms), 0)],
                initial_protection=[(emergency_expression, int(_val_expr(solver, emergency_expression)))],
                wall_s=(wall_budget if reproducible else
                        min(max(wall_budget - (time.time() - wall_start), 0),
                            float(scheduling["OBJECTIVE_SECONDARY_WALL_TIME_S"]) / 2)),
                deterministic_s=float(scheduling["OBJECTIVE_SECONDARY_DETERMINISTIC_TIME_S"]) / 2,
                seed=sc.sim.seed, accept_candidate=accept_execution_candidate,
                repair_constraints=repair_execution_constraints, accept_equal=True,
                deterministic_only=reproducible)
            solver = repair_pass.solver
        final_unmet_terms = [
            weight_coef["w1_unmet"] * demand_priority_at(did, t + opts.horizon_offset) * u
            for (did, t), u in unmet.items() if sc.destination(did).demand_kind != "process_feed"]
        preference_expression = sum(terms) - sum(final_unmet_terms) - sum(continuity_terms) - sum(equipment_terms)
        levels = [
            ("qualified_service", qualified_service_expression,
             primary_service_ceiling - int(_val_expr(solver, qualified_service_expression))),
            # Do not dispatch uncredited off-spec material merely to purchase
            # forecast replenishment or keep a machine on. Fixed jobs/soft
            # exceptions remain possible and visible. Quality takes precedence
            # over cycling; later scheduling candidates protect both.
            ("unqualified_dispatch", unqualified_dispatch_expression, 0),
            ("continuity", sum(continuity_terms), 0),
            ("operations", sum(equipment_terms), 0),
            ("preferences", preference_expression, 0)]
        if planning_corridor_shortfall:
            # Corridor replenishment, priced for service_first mode.
            #
            # CORRIDOR_DISPATCH_TARGET_T is the declared tonnage each mine
            # should send to the hub. It is penalised inside ``sum(terms)``,
            # which this mode discards, so the intention carried NO objective
            # weight: measured 28-Sep-2026, the mines produced 93,800 t/week
            # and dispatched only 16,529 t while M16 drew itself down
            # 33,408 t. Nothing pulled the corridors, and the dryer went short
            # of material that existed and qualified.
            #
            # Ranked BELOW service, quality and continuity and ABOVE equipment
            # preferences: replenish the hub with capacity that serving
            # customers did not need, never by withholding a delivered tonne.
            levels.insert(
                next((position for position, level in enumerate(levels)
                      if level[0] == "operations"), len(levels)),
                ("corridor_replenishment",
                 sum(planning_corridor_shortfall.values()), 0))
        if terminal_min_shortfall:
            # Replenishment sits BELOW service, quality and continuity and
            # ABOVE equipment preferences: both mines rebuild their operating
            # stock with whatever capacity serving demand did not need, and
            # never by withholding a delivered tonne.
            levels.insert(
                next((position for position, level in enumerate(levels)
                      if level[0] == "operations"), len(levels)),
                ("replenishment", sum(terminal_min_shortfall.values()), 0))
        if frozen_rate_shortfall:
            # Honour near-term commitments with capacity that serving demand
            # did not need. Above equipment preferences, because breaking a
            # commitment disturbs work already under way; below service, because
            # a commitment made an hour ago does not outrank a customer now.
            levels.insert(
                next((position for position, level in enumerate(levels)
                      if level[0] == "operations"), len(levels)),
                ("frozen_commitment", sum(frozen_rate_shortfall), 0))
        if washing.enabled(sc):
            levels[2:2] = [("raw_feed", raw_feed_expression, 0),
                           ("treatment_cost", washing_cost_expression, 0)]
        objective_passes = refine_objectives(
            m, solver, levels=levels,
            initial_protection=[(emergency_expression, int(_val_expr(solver, emergency_expression)))],
            wall_s=(wall_budget if reproducible else
                    min(max(wall_budget - (time.time() - wall_start), 0),
                        max(float(scheduling["OBJECTIVE_SECONDARY_WALL_TIME_S"]) - (time.monotonic() - secondary_started), 0))),
            deterministic_s=max(float(scheduling["OBJECTIVE_SECONDARY_DETERMINISTIC_TIME_S"])
                                - (repair_pass.deterministic_time if repair_pass else 0), 0),
            seed=sc.sim.seed, accept_candidate=accept_execution_candidate,
            repair_constraints=repair_execution_constraints,
            level_acceptance={"unqualified_dispatch": accept_quality_candidate,
                              "raw_feed": accept_quality_candidate,
                              "treatment_cost": accept_quality_candidate},
            deterministic_only=reproducible)
        if repair_pass:
            objective_passes.records = repair_pass.records + objective_passes.records
            objective_passes.branches += repair_pass.branches
            objective_passes.conflicts += repair_pass.conflicts
            objective_passes.deterministic_time += repair_pass.deterministic_time
        solver = objective_passes.solver

    if (status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
            and sc.config.get("PARCEL_CONSOLIDATION_ENABLED", False)
            and short_parcel):
        from .parcel_consolidation import (ConsolidationResult,
                                           consolidate_parcels)

        # Batch within existing physical buffers, never by discarding tonnes,
        # changing the selected route/grade totals or reducing service. Final
        # unit deliveries stay fixed in every bucket; process-feed campaigns
        # retain their daily totals but may use their allowed flexible hours.
        fixed = [sum(X[(ci, t)] for t in range(T))
                 for ci in range(len(cands))]
        fixed += [sum(Y[(ci, t)] for t in range(T))
                  for ci in process_yield_coeff]
        fixed += [inv[(space.id, T)] for space in sc.spaces]
        fixed += list(unmet.values()) + list(qslack)
        fixed += list(short.values()) + list(spill)
        fixed += [s for values in surge_slacks.values() for s in values]
        fixed += list(stage_target_shortfall.values())
        fixed += list(planning_stage_shortfall.values()) + list(planning_corridor_shortfall.values())
        fixed += list(frozen_rate_shortfall)
        if service_first:
            fixed += [qualified_service_expression, sum(continuity_terms), sum(equipment_terms)]
        fixed += [slack for _priority, slack in blend_quality_slack_terms]
        fixed += [delivered[(destination.id, t)]
                  for destination in sc.destinations
                  if getattr(destination, "demand_kind", "delivery")
                  != "process_feed" for t in range(T)]
        remaining_wall = (wall_budget if reproducible else
                          max(wall_budget - (time.time() - wall_start), 0.0))
        _stage("consolidating buffered residual parcels")
        polish_started = time.monotonic()
        try:
            consolidation = consolidate_parcels(
                m, solver, primary_expression=sum(terms),
                parcel_flags=short_parcel.values(), fixed_expressions=fixed,
                wall_s=min(remaining_wall, float(sc.config.get(
                    "PARCEL_CONSOLIDATION_WALL_TIME_S", 15.0))),
                deterministic_s=float(sc.config.get(
                    "PARCEL_CONSOLIDATION_DETERMINISTIC_TIME_S", 4.0)),
                seed=sc.sim.seed, accept_candidate=accept_execution_candidate,
                deterministic_only=reproducible)
        except Exception as exc:
            # Optional refinement must never discard a valid primary plan.
            count = sum(int(solver.value(flag))
                        for flag in short_parcel.values())
            value = int(solver.value(sum(terms)))
            consolidation = ConsolidationResult(
                solver, attempted=True, status=f"ERROR/{type(exc).__name__}",
                before=count, after=count,
                wall_s=time.monotonic() - polish_started,
                primary_before=value, primary_after=value)
        if consolidation.accepted:
            solver = consolidation.solver
            # Secondary OPTIMAL is not proof of primary global optimality.
            # The original primary bound remains the valid reporting bound.
            status = (cp_model.OPTIMAL if status == cp_model.OPTIMAL
                      else cp_model.FEASIBLE)
    jobs, timing_refinement, sc, _minute_metrics = execution(solver)
    val = solver.value
    res.jobs = jobs
    if blend_target_vars_by_space:
        res.blend_campaigns = _blend_campaign_records(sc, jobs)
    objective_value = float(_val_expr(solver, primary_expression))
    res.solver_wall_s = round(time.time() - wall_start, 3)
    res.solver_branches += ((objective_passes.branches if objective_passes else 0)
                           + (consolidation.branches if consolidation else 0))
    res.solver_conflicts += ((objective_passes.conflicts if objective_passes else 0)
                            + (consolidation.conflicts if consolidation else 0))
    res.solver_deterministic_time += (
        (objective_passes.deterministic_time if objective_passes else 0)
        + (consolidation.deterministic_time if consolidation else 0))
    res.solver_relative_gap_pct = 100 * abs(objective_value - primary_bound) / max(abs(objective_value), 1)
    if objective_value > primary_bound + 1e-9 and res.status == "OPTIMAL":
        res.status = "FEASIBLE"
    res.objective_pass_records = objective_passes.records if objective_passes else []

    for d in sc.destinations:
        res.unmet[d.id] = [
            float(_val_expr(solver, unmet[(d.id, t)]))
            for t in range(T)
        ]
        res.delivered[d.id] = [float(_val_expr(solver, delivered[(d.id, t)])) / dt
                               for t in range(T)]
    for destination_id, policy in continuity_policies.items():
        if not policy.scored:
            continue
        res.feed_gap[destination_id] = [
            float(_val_expr(solver, feed_gap[(destination_id, t)]))
            for t in range(T)]
        res.feed_interruption_start[destination_id] = [
            float(_val_expr(
                solver, feed_episode_start[(destination_id, t)]))
            for t in range(T)]
    demand_profile_end = max(opts.horizon_offset + T, 0)
    continuity_metrics = compute_continuity_metrics(
        continuity_policies,
        {
            destination_id: [
                demand_rate_at(destination_id, bucket)
                for bucket in range(demand_profile_end)]
            for destination_id in continuity_policies
        },
        {
            destination_id: [
                rate_tph * dt
                for rate_tph in res.delivered.get(destination_id, ())]
            for destination_id in continuity_policies
        },
        [dt] * T,
        offset=opts.horizon_offset,
        initial_gap_by_destination=opts.initial_feed_gap,
        initial_open_gap_minutes=opts.initial_open_feed_gap_minutes,
    )
    res.terminal_feed_gap = dict(
        continuity_metrics["terminal_gap_by_destination"])
    res.terminal_open_feed_gap_minutes = dict(
        continuity_metrics["terminal_open_gap_minutes"])
    if T:
        for machine_id, groups in mach_groups.items():
            terminal_group = next((
                group for group in groups
                if val(AG[(group, T - 1)])), None)
            if terminal_group is not None:
                res.terminal_machine_groups[machine_id] = tuple(
                    terminal_group)
    for d_id, slacks in qslack_by_dest.items():
        res.offspec[d_id] = [
            float(_val_expr(solver, s)) / QSCALE for s in slacks]
    for sp in sc.spaces:
        res.inventory[sp.id] = [float(val(inv[(sp.id, t)])) for t in range(T + 1)]
        if sp.safety_stock_t > 0:
            res.safety_short[sp.id] = [float(val(short[(sp.id, t)]))
                                       for t in range(1, T + 1)]
    if washing.enabled(sc):
        res.material_catalog = copy.deepcopy(sc.config["_WASHING_MODEL_CATALOG"])
        for sp in sc.spaces:
            if sp.id in managed_blend_spaces:
                continue
            res.material_inventory[sp.id] = {
                mid: [float(val(material_inv[(sp.id, mid, t)])) for t in range(T + 1)]
                for mid in sc.config["_WASHING_MODEL_POSSIBLE"][sp.id]}
        end_minute = (opts.horizon_offset + T) * max(int(round(dt * 60)), 1)
        res.material_pending = {
            sid: {mid: [copy.deepcopy(part) for part in parts if part["ready_minute"] > end_minute]
                  for mid, parts in materials.items()}
            for sid, materials in sc.config.get("_WASHING_MODEL_PENDING", {}).items()}
        for job in jobs:
            if not job.treatment_id:
                continue
            for flow in job.material_flows:
                windows = [window for window in job.execution_windows
                           if window.solver_start_bucket <= flow["bucket"] <= window.solver_end_bucket
                           and window.status == "VALIDATED"]
                ready = max((window.start_minute + window.duration_min for window in windows),
                            default=(flow["bucket"] + 1) * max(int(round(dt * 60)), 1)) + job.residence_min
                if ready > end_minute:
                    res.material_pending.setdefault(job.target_space, {}).setdefault(
                        flow["output_material_id"], []).append(dict(ready_minute=ready, tonnage_t=flow["output_tonnage_t"]))
    for stage in sorted({
            cand.process_stage for cand in cands
            if cand.kind == "process" and cand.process_stage}):
        res.process_reject[stage] = [
            float(sum(
                val(X[(ci, t)]) - val(Y[(ci, t)])
                for ci, cand in enumerate(cands)
                if cand.kind == "process"
                and cand.process_stage == stage
                and (ci, t) in Y
            ))
            for t in range(T)
        ]
    res.surge_unstacked = [float(sum(val(s) for s in surge_slacks[t]))
                           for t in range(T)]
    res.objective = objective_value
    scheduled_starts = float(sum(
        val(event) for event in machine_starts.values()))
    scheduled_stops = float(sum(
        val(event) for event in machine_stops.values()))
    scheduled_changeovers = float(sum(
        val(event) for event in machine_changeovers.values()))
    state_transition_count = (
        scheduled_starts + scheduled_stops + scheduled_changeovers)
    active_machine_h = float(sum(
        _val_expr(solver, active)
        for active in machine_material_on.values()) * dt)
    held_machine_h = float(sum(
        _val_expr(solver, hold) for hold in machine_hold.values()) * dt)
    for (machine_id, bucket), hold in sorted(
            machine_hold.items(), key=lambda item: item[0]):
        if _val_expr(solver, hold):
            res.machine_hold_buckets.setdefault(machine_id, []).append(
                bucket + opts.horizon_offset)
    handled_t = float(sum(val(x) for x in X.values()))
    work_link_energy_kwh = float(sum(
        energy_coefficients[ci] * val(x)
        for (ci, _bucket), x in X.items()) / ENERGY_OBJECTIVE_SCALE)
    res.breakdown = {
        "unmet_t": sum(sum(v) for v in res.unmet.values()),
        "offspec_t_unit": sum(sum(v) for v in res.offspec.values()),
        "blend_construction_offspec_t_unit": float(sum(
            val(slack) / QSCALE for _priority, slack in blend_quality_slack_terms)),
        "blend_construction_giveaway_t_unit": float(sum(
            val(deviation) for _priority, deviation in blend_target_deviation_terms)),
        "priority_weighted_unmet_t": float(sum(
            demand_priority_at(destination_id, bucket + opts.horizon_offset) * value
            for destination_id, values in res.unmet.items()
            for bucket, value in enumerate(values))),
        "safety_short_t": sum(sum(v) for v in res.safety_short.values()),
        # Compatibility ``moves`` now has an exact event meaning.  One A->B
        # group change contributes one, not two per-space differences.
        "moves": state_transition_count,
        "scheduled_starts": scheduled_starts,
        "scheduled_stops": scheduled_stops,
        "scheduled_group_changeovers": scheduled_changeovers,
        "machine_start_count": scheduled_starts,
        "machine_stop_count": scheduled_stops,
        "machine_changeover_count": scheduled_changeovers,
        "machine_state_transition_count": state_transition_count,
        "active_machine_h": active_machine_h,
        "held_machine_h": held_machine_h,
        "scheduled_hold_buckets": float(sum(
            _val_expr(solver, hold) for hold in machine_hold.values())),
        "handled_t": handled_t,
        "work_link_energy_kwh": work_link_energy_kwh,
        "energy_objective_enabled": float(bool(
            sc.config.get("ENERGY_OBJECTIVE_ENABLED", False))),
        "solver_best_objective_bound": res.solver_best_bound,
        "solver_relative_gap_pct": res.solver_relative_gap_pct,
        "solver_branches": float(res.solver_branches),
        "solver_conflicts": float(res.solver_conflicts),
        "solver_deterministic_time": res.solver_deterministic_time,
        "continuity_gap_minutes": float(
            continuity_metrics["gap_minutes"]),
        "continuity_episode_count": float(
            continuity_metrics["episode_count"]),
        "continuity_max_episode_minutes": float(
            continuity_metrics["max_episode_minutes"]),
        "continuous_feed_gap_minutes": float(
            continuity_metrics["gap_minutes"]),
        "continuous_feed_interruption_count": float(
            continuity_metrics["episode_count"]),
        "continuous_feed_max_episode_minutes": float(
            continuity_metrics["max_episode_minutes"]),
        "surge_t": sum(res.surge_unstacked),
        "spill_t": float(sum(val(s) for s in spill)),
        "process_reject_t": float(
            sum(sum(series) for series in res.process_reject.values())),
        "planning_eligible_machines": float(len(machine_used)),
        "planning_used_machines": float(
            sum(val(used) for used in machine_used.values())),
        "minute_timing_validated_windows": float(sum(
            1 for job in jobs for window in job.execution_windows
            if window.status == "VALIDATED")),
        "minute_timing_errors": float(sum(
            1 for issue in timing_refinement.issues
            if issue.severity == "ERROR")),
        "minute_timing_fallback_buckets": float(
            len(timing_refinement.fallback_buckets)),
        "minute_timing_stable_run_windows": float(
            timing_refinement.expanded_windows),
        "minute_timing_stable_run_minutes": float(
            timing_refinement.expanded_minutes),
        "short_parcel_buckets": float(sum(
            val(short) for short in short_parcel.values())),
        "process_stage_target_shortfall_t": float(sum(
            val(deficit) for deficit in stage_target_shortfall.values())),
        "parcel_floor_constrained_groups": float(len(set(
            gk for (gk, _t) in short_parcel))),
        "campaign_policy_enabled": float(bool(campaign_policy_enabled(sc))),
        "campaign_dwell_constraints": float(dwell_constraints),
        "hierarchical_native_flow_targets": float(
            hierarchical_target_count),
        "positioning_reservation_constraints": float(positioning_reservation_count),
        "joint_blend_schedule_campaigns": float(
            len(blend_target_vars_by_space)),
        "joint_blend_selected_components": float(sum(
            len(record.get("composition", ()))
            for record in res.blend_campaigns.values()
            if record.get("mode") == BLEND_MODE_OPTIMIZED)),
    }
    if consolidation is not None:
        res.breakdown.update({
            "parcel_consolidation_attempted_windows": float(consolidation.attempted),
            "parcel_consolidation_accepted_windows": float(consolidation.accepted),
            "parcel_consolidation_before_buckets": float(consolidation.before),
            "parcel_consolidation_after_buckets": float(consolidation.after),
            "parcel_consolidation_wall_s": float(consolidation.wall_s),
            "parcel_consolidation_primary_before": float(consolidation.primary_before),
            "parcel_consolidation_primary_after": float(consolidation.primary_after),
        })
        if consolidation.attempted:
            res.reasons.append(
                f"PARCEL_CONSOLIDATION {consolidation.status}: "
                f"{consolidation.before} -> {consolidation.after} short command "
                "buckets; route/grade totals, service, terminal inventories "
                "and the original objective protected")
    res.breakdown.update({
        "objective_service_first": float(service_first),
        "priority_weighted_qualified_unmet_t": float(_val_expr(solver, qualified_service_expression)),
        "unqualified_dispatch_t": float(_val_expr(solver, unqualified_dispatch_expression)),
        "weighted_objective_comparison": float(_val_expr(solver, sum(terms))),
        "minimum_job_volume_t": float(sc.config.get("MIN_JOB_VOLUME_T", 0.0)),
        "minimum_job_volume_constraints": float(minimum_job_constraints),
        "objective_secondary_attempted_passes": float(len(res.objective_pass_records)),
        "objective_secondary_accepted_passes": float(sum(r["accepted"] for r in res.objective_pass_records)),
        "planning_stage_shortfall_t": float(sum(val(v) for v in planning_stage_shortfall.values())),
        "planning_corridor_shortfall_t": float(sum(val(v) for v in planning_corridor_shortfall.values())),
        "washing_treatments_enabled": float(washing.enabled(sc)),
        "washing_raw_feed_dry_t": float(_val_expr(solver, raw_feed_expression)) / 1000,
        "washing_treatment_cost": float(_val_expr(solver, washing_cost_expression)) / 1000,
    })
    if washing.enabled(sc):
        for mode_key in ("simple", "full"):
            flows = [flow for job in res.jobs for flow in job.material_flows
                     if flow["treatment_id"] == mode_key]
            feed = sum(flow["tonnage_t"] for flow in flows)
            product = sum(flow["output_tonnage_t"] for flow in flows)
            feed_dry = sum(flow["tonnage_t"] * (1 - flow["quality"]["moisture"] / 100) for flow in flows)
            product_dry = sum(flow["output_tonnage_t"] * (1 - flow["output_quality"]["moisture"] / 100) for flow in flows)
            res.breakdown.update({f"washing_{mode_key}_feed_t": feed,
                                  f"washing_{mode_key}_product_t": product,
                                  f"washing_{mode_key}_feed_dry_t": feed_dry,
                                  f"washing_{mode_key}_product_dry_t": product_dry,
                                  f"washing_{mode_key}_loss_dry_t": feed_dry - product_dry})
    for record in res.objective_pass_records:
        res.reasons.append(
            f"OBJECTIVE_PASS[{record['name']}] {record['status']}: "
            f"{record['before']} -> {record['after']}; "
            "higher-priority incumbents protected; bounded search, not an optimality certificate")
    for label, variables in (("stage", planning_stage_shortfall), ("corridor", planning_corridor_shortfall)):
        for key, variable in variables.items():
            amount = float(val(variable))
            res.breakdown[f"planning_{label}_shortfall_t[{key}]"] = amount
            if amount:
                res.reasons.append(f"PLANNING_TARGET_SHORTFALL[{label}/{key}] {amount:g} t; "
                                   "derived replenishment target is not a hard commitment")
    if not timing_refinement.valid or any(
            window.status != "VALIDATED" for job in jobs for window in job.execution_windows):
        res.reasons.append(f"MINUTE_EXECUTION_UNVALIDATED: hourly solver {sname}; "
                           "candidate is not an executable plan; previous usable plan must be retained")
        res.status = "PARTIAL"
        res.reasons.extend(f"MINUTE_TIMING[{issue.code}] {issue.job_id}/bucket {issue.bucket}: {issue.message}"
                           for issue in timing_refinement.issues if issue.severity == "ERROR")
        res.relaxations.append("Repair positioning/dependency timing before accepting this hourly allocation")
    for space in sc.spaces:
        space_spill = float(sum(
            val(spill_map[(space.id, t)]) for t in range(T)))
        if space_spill > 0:
            res.breakdown[f"spill_t[{space.id}]"] = space_spill
    for stage, deficit in stage_target_shortfall.items():
        amount = float(val(deficit))
        if amount > 0:
            res.breakdown[f"process_stage_target_shortfall_t[{stage}]"] = amount
            res.reasons.append(
                f"PROCESS_STAGE_TARGET_SHORTFALL[{stage}] {amount:.0f} t "
                f"in buckets {opts.horizon_offset}-"
                f"{opts.horizon_offset + T - 1}: search candidate misses "
                "the exact campaign target and is not accepted as a plan")
    if res.breakdown["process_stage_target_shortfall_t"] > 0:
        # This slack helps CP-SAT establish an incumbent without token flows.
        # It is a SEARCH device, not authority to miss configured campaigns.
        # The caller keeps the previous usable plan on PARTIAL status.
        res.status = "PARTIAL"
        res.relaxations.append(
            "Review campaign targets and the assumed minimum parcel; "
            "the candidate was rejected because a stage target was missed")
    surge_by_exit_grade: Dict[Tuple[str, str], float] = {}
    for (exit_node, grade, _t), variable in surge_map.items():
        amount = float(val(variable))
        if amount > 0:
            key = (exit_node, grade)
            surge_by_exit_grade[key] = surge_by_exit_grade.get(key, 0.0) + amount
    for (exit_node, grade), amount in surge_by_exit_grade.items():
        res.breakdown[f"surge_t[{exit_node}|{grade}]"] = amount
    eligible_count = int(res.breakdown["planning_eligible_machines"])
    used_count = int(res.breakdown["planning_used_machines"])
    res.breakdown["planning_machine_coverage_pct"] = (
        100.0 * used_count / eligible_count if eligible_count else 100.0)
    if terminal_supply_expr is not None:
        res.breakdown["terminal_supply_position_max_t"] = float(
            terminal_supply_max)
        res.breakdown["terminal_supply_position_t"] = float(
            _val_expr(solver, terminal_supply_expr))
        res.breakdown["terminal_corridor_in_transit_t"] = float(
            sum(val(term) for term in terminal_in_transit_terms)
            + terminal_initial_belt_t)
    # Executed accounting from the minute schedule.  The hourly
    # ``scheduled_*`` counters above answer "how many buckets changed state";
    # these answer "how many times does this machine actually start, stop,
    # change over and stand held" once the minute windows are placed.  Both
    # are reported because they are genuinely different questions and their
    # difference is the fragmentation the operator sees.
    solve_bucket_minutes = max(int(round(dt * 60.0)), 1)
    res.breakdown.update(_executed_schedule_breakdown(
        sc, res, jobs,
        horizon_start_minute=opts.horizon_offset * solve_bucket_minutes,
        horizon_end_minute=(opts.horizon_offset + T) * solve_bucket_minutes))
    _derive_reasons(
        sc, res, cands, dt,
        horizon_offset=opts.horizon_offset, horizon_buckets=T)
    _derive_machine_dispositions(sc, res, cands)
    res.reasons.extend(
        f"UNKNOWN_ASSAY[{destination_id}] {space_id}: {parameter} is "
        "unmeasured; excluded from quality-constrained delivery."
        for destination_id, space_id, parameter
        in sorted(unknown_quality_candidates))
    return res


def _val_expr(solver, expr):
    if isinstance(expr, int):
        return expr
    return solver.value(expr)


def _derive_reasons(
        sc: Scenario, res: PlanResult, cands: List[Cand], dt: float, *,
        horizon_offset: int = 0,
        horizon_buckets: Optional[int] = None):
    """Heuristic reason codes + suggested relaxations for gaps (report ask)."""
    bucket_count = int(
        sc.sim.horizon_buckets if horizon_buckets is None
        else horizon_buckets)
    absolute_buckets = range(
        int(horizon_offset), int(horizon_offset) + bucket_count)
    for d_id, series in res.unmet.items():
        tot = sum(series)
        if tot <= 0:
            continue
        d = sc.destination(d_id)
        process_feed = getattr(d, "demand_kind", "delivery") == "process_feed"
        active_grades = {
            sc.demand_grade(d_id, t)
            for t in absolute_buckets
            if sc.demand_tph(d_id, t) > 0
        } or {getattr(d, "requested_grade", "") or ""}
        feed_cands = [
            c for c in cands
            if any(_candidate_serves_destination(
                sc, c, d, requested_grade=grade)
                for grade in active_grades)
        ]
        if not feed_cands:
            route_kind = (
                f"process transfer for stage {d.process_stage}"
                if process_feed else
                "(machine, space, path) tuple"
            )
            res.reasons.append(
                f"UNMET_DEMAND[{d_id}] {tot:.0f} t: NO_ROUTE_OR_MACHINE — "
                f"no {route_kind} can feed this destination")
            res.relaxations.append(
                f"{d_id}: add an executable process transfer"
                if process_feed else
                f"{d_id}: add reachability or route links to node {d.node_id}")
            continue
        mach_cap = sum(sc.machine(mid).rate_max_tph
                       for mid in {c.machine for c in feed_cands})
        path_cap = sum({tuple(c.path.links): c.path.capacity_tph
                        for c in feed_cands if c.path}.values())
        avg_dem = sum(
            sc.demand_tph(d_id, t) for t in absolute_buckets) / max(
                bucket_count, 1)
        source_spaces = {
            c.source_space if process_feed else c.space
            for c in feed_cands
        }
        inv_avail = sum(sc.space(sid).mass_t for sid in source_spaces)
        need_t = avg_dem * bucket_count * dt
        cause = []
        if avg_dem > mach_cap:
            cause.append(f"machine capacity {mach_cap:.0f} tph < demand {avg_dem:.0f} tph")
        if avg_dem > path_cap:
            cause.append(f"route bottleneck {path_cap:.0f} tph < demand {avg_dem:.0f} tph")
        if inv_avail < need_t:
            cause.append(f"available inventory {inv_avail:.0f} t < required {need_t:.0f} t")
        hard_specs = sorted({
            s.param
            for bucket in absolute_buckets
            for s in sc.demand_spec(d.id, bucket)
            if s.hard
        })
        if hard_specs and not cause:
            cause.append(f"hard quality spec on {'/'.join(hard_specs)} restricts usable blend")
        if not cause:
            cause.append("competing destinations / calendars / conflicts within horizon")
        res.reasons.append(f"UNMET_DEMAND[{d_id}] {tot:.0f} t: " + "; ".join(cause))
        res.relaxations.append(f"{d_id}: " + "; ".join(
            {"machine": "assign more machines or raise rates",
             "route": "raise link capacity / enable parallel path",
             "available": "increase mine dispatch or initial stock",
             "hard": "convert hard spec to soft (w2) or widen the band"}.get(
                c.split(" ")[0], "review demand profile") for c in cause))
    if res.breakdown.get("surge_t", 0) > 0:
        res.reasons.append(f"UNSTACKED_ARRIVALS {res.breakdown['surge_t']:.0f} t: corridor "
                           "arrivals exceed stacking capacity — belts cannot hold material")
        res.relaxations.append("Reduce mine dispatch, free a stacker, or add stack routes")
    if res.breakdown.get("spill_t", 0) > 0:
        res.reasons.append(f"SPACE_OVERFLOW {res.breakdown['spill_t']:.0f} t: exogenous "
                           "inflow exceeds space capacity")


def _derive_machine_dispositions(
        sc: Scenario, res: PlanResult, cands: List[Cand]) -> None:
    """One structured, evidence-neutral disposition per solver machine."""
    jobs_by_machine: Dict[str, List[Job]] = {}
    cands_by_machine: Dict[str, List[Cand]] = {}
    for job in res.jobs:
        jobs_by_machine.setdefault(job.machine, []).append(job)
    for cand in cands:
        cands_by_machine.setdefault(cand.machine, []).append(cand)

    exclusive_peers: Dict[str, set] = {}
    for site in sc.sites:
        for group in getattr(site, "exclusive_machine_groups", []) or []:
            for machine_id in group:
                exclusive_peers.setdefault(machine_id, set()).update(
                    peer for peer in group if peer != machine_id)

    policy_enabled = bool(
        sc.config.get("LOU_S0_STOCKED_ROUTE_ENABLED", False))
    for machine in sc.machines:
        machine_jobs = jobs_by_machine.get(machine.id, [])
        machine_cands = cands_by_machine.get(machine.id, [])
        input_spaces = sorted({
            cand.input_space() for cand in machine_cands
            if cand.input_space()})
        output_spaces = sorted({
            cand.output_space() for cand in machine_cands
            if cand.output_space()})
        inventory_t = sum(
            res.inventory.get(space_id, [sc.space(space_id).mass_t])[0]
            for space_id in input_spaces)
        free_capacity_t = sum(
            max(sc.space(space_id).capacity_t
                - res.inventory.get(
                    space_id, [sc.space(space_id).mass_t])[-1], 0.0)
            for space_id in output_spaces)
        routes = sorted({
            " → ".join(cand.path.links)
            for cand in machine_cands if cand.path and cand.path.links})
        triggers = sorted({
            cand.purpose for cand in machine_cands if cand.purpose})
        base = {
            "solver_controlled": bool(
                machine.available
                and getattr(machine, "planning_enabled", True)),
            "reachable_spaces": list(machine.reachable_spaces),
            "route": " | ".join(routes),
            "inventory_t": float(inventory_t),
            "free_capacity_t": float(free_capacity_t),
            "job_trigger": " | ".join(triggers),
        }
        if machine_jobs:
            tonnes = sum(job.tonnage_t for job in machine_jobs)
            base.update({
                "code": "SCHEDULED",
                "reason": (f"{len(machine_jobs)} purposeful job(s), "
                           f"{tonnes:,.0f} t input"),
                "classification": "jobs generated",
            })
        elif not machine.available:
            base.update({
                "code": "UNAVAILABLE",
                "reason": (
                    "machine disabled/unavailable in the current scenario "
                    "configuration"),
                "classification": "legitimate idle",
            })
        elif not getattr(machine, "planning_enabled", True):
            base.update({
                "code": "OUTSIDE_PLANNING_SCOPE",
                "reason": "equipment is not enabled for solver scheduling",
                "classification": "modelling gap",
            })
        elif not machine_cands:
            base.update({
                "code": "MISSING_ROUTE_DATA",
                "reason": "no valid machine, stockpile and route candidate",
                "classification": "modelling gap",
            })
        elif input_spaces and inventory_t <= 0:
            base.update({
                "code": "NO_AVAILABLE_INVENTORY",
                "reason": "no source inventory is available",
                "classification": "legitimate idle",
            })
        elif output_spaces and free_capacity_t <= 0:
            base.update({
                "code": "NO_FREE_CAPACITY",
                "reason": "all reachable targets are full",
                "classification": "legitimate idle",
            })
        elif any(peer in jobs_by_machine
                 for peer in exclusive_peers.get(machine.id, set())):
            base.update({
                "code": "STANDBY_SHARED_ROUTE",
                "reason": "a mutually exclusive peer supplies the shared route",
                "classification": "legitimate idle",
            })
        elif machine.site == "LOUTA" and not policy_enabled:
            base.update({
                "code": "NO_DEMAND_OR_REPLENISHMENT_TARGET",
                "reason": "no stocked-route source allocation is configured",
                "classification": "legitimate idle",
            })
        else:
            base.update({
                "code": "NOT_SELECTED_BY_PLAN",
                "reason": "demand and configured targets are met without it",
                "classification": "legitimate idle",
            })
        res.machine_dispositions[machine.id] = base


def blend_fractions(res: PlanResult, dest_id: str, bucket: int) -> Dict[str, float]:
    """Derived reporting quantity f = x / delivered (report §15.1) — from jobs."""
    flows: Dict[str, float] = {}
    for j in res.jobs:
        if j.kind == "reclaim" and j.dest_id == dest_id and \
                j.start_bucket <= bucket <= j.end_bucket:
            flows[j.space] = flows.get(j.space, 0.0) + j.rate_tph
    tot = sum(flows.values())
    return {s: v / tot for s, v in flows.items()} if tot else {}
