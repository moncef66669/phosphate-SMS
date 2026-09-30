"""Canonical-minute helpers for mixed-resolution planning.

The optimisation model itself remains a uniform-grid CP-SAT model.  A mixed
plan is produced safely by solving the native (normally hourly) tactical
horizon and a physically equivalent, explicitly resampled near-term prefix.
This module owns the resampling and result merge; it deliberately contains no
solver dependency so the transformations are independently testable.
"""
from __future__ import annotations

import copy
import math
from dataclasses import replace
from typing import Dict, Iterable, List, Optional, Tuple

from .domain import (BeltProfileEntry, CalendarEntry, DemandEntry,
                     ExogenousFlow, FixedJob, Job, JobBucketFlow, PlanResult,
                     Scenario)


def _overlap_to_local_buckets(
        start_minute: int, end_minute: int, window_start_minute: int,
        window_end_minute: int, step_min: int) -> Optional[Tuple[int, int]]:
    """Map a half-open physical interval to all intersecting local buckets."""
    start = max(int(start_minute), int(window_start_minute))
    end = min(int(end_minute), int(window_end_minute))
    if end <= start:
        return None
    local_start = (start - window_start_minute) // step_min
    local_end_exclusive = int(math.ceil(
        (end - window_start_minute) / float(step_min)))
    return local_start, local_end_exclusive - 1


def resample_scenario_window(
        scenario: Scenario, *, window_start_minute: int,
        window_duration_min: int, step_min: int,
        commit_window_min: Optional[int] = None) -> Scenario:
    """Return a uniform fine-grid scenario for one physical-time window.

    Every legacy bucket-coded input is converted through physical minutes:
    demand, calendars, exogenous flows, fixed work, corridor delays and the
    initial in-transit profile.  Rates stay in t/h and masses stay in tonnes.
    The input scenario is never mutated.
    """
    native_min = scenario.sim.native_bucket_minutes()
    duration = int(window_duration_min)
    step = int(step_min)
    window_start = int(window_start_minute)
    if native_min <= 0 or step <= 0 or duration <= 0:
        raise ValueError("planning durations must be positive whole minutes")
    if duration % step:
        raise ValueError("window duration must be divisible by its planning step")
    if native_min % step or window_start % step:
        raise ValueError(
            "native grid and window origin must align to the planning step")
    window_end = window_start + duration
    refined = copy.deepcopy(scenario)

    # Demand is stored as a rate profile but CP-SAT balances whole tonnes.
    # Preserve each source bucket's exact rounded mass, distributing that mass
    # deterministically over its fine slots.  Merely copying a rate such as
    # 110 t/h would round 27.5 t to 28 t four times and invent 2 t/h.
    demand: List[DemandEntry] = []
    for destination in scenario.destinations:
        fine_masses = [0] * (duration // step)
        fine_grades = [""] * (duration // step)
        fine_priorities = [destination.priority] * (duration // step)
        first_native = window_start // native_min
        last_native = int(math.ceil(window_end / float(native_min)))
        for native_bucket in range(first_native, last_native):
            source_start = native_bucket * native_min
            source_end = source_start + native_min
            overlap_start = max(source_start, window_start)
            overlap_end = min(source_end, window_end)
            if overlap_end <= overlap_start:
                continue
            first_slot = (overlap_start - window_start) // step
            slot_count = (overlap_end - overlap_start) // step
            if slot_count <= 0:
                continue
            rate_tph = float(scenario.demand_tph(
                destination.id, native_bucket))
            physical_mass = int(round(
                rate_tph * (overlap_end - overlap_start) / 60.0))
            base, extra = divmod(max(physical_mass, 0), slot_count)
            for offset in range(slot_count):
                local_slot = first_slot + offset
                fine_masses[local_slot] = (
                    base + (1 if offset < extra else 0))
                fine_grades[local_slot] = scenario.demand_grade(
                    destination.id, native_bucket)
                fine_priorities[local_slot] = scenario.demand_priority(
                    destination.id, native_bucket)
        for local_bucket, mass_t in enumerate(fine_masses):
            demand.append(DemandEntry(
                dest_id=destination.id,
                bucket=local_bucket,
                rate_tph=float(mass_t) * 60.0 / step,
                requested_grade=fine_grades[local_bucket],
                priority=fine_priorities[local_bucket]))
    refined.demand = demand

    calendars: List[CalendarEntry] = []
    for entry in scenario.calendars:
        exact_start = getattr(entry, "start_minute", None)
        exact_end = getattr(entry, "end_minute", None)
        has_exact_window = exact_start is not None and exact_end is not None
        interval_start = (
            int(exact_start) if has_exact_window
            else entry.start_bucket * native_min)
        interval_end = (
            int(exact_end) if has_exact_window
            else (entry.end_bucket + 1) * native_min)
        mapped = _overlap_to_local_buckets(
            interval_start, interval_end,
            window_start, window_end, step)
        if mapped is not None:
            changes = {
                "start_bucket": mapped[0],
                "end_bucket": mapped[1],
            }
            if has_exact_window:
                # The refined scenario has its own start_iso at the rolling
                # window origin, so retain the physical interval exactly but
                # express its canonical minutes relative to that new origin.
                changes.update({
                    "start_minute": max(interval_start, window_start)
                    - window_start,
                    "end_minute": min(interval_end, window_end)
                    - window_start,
                })
            calendars.append(replace(entry, **changes))
    refined.calendars = calendars

    exogenous: List[ExogenousFlow] = []
    for entry in scenario.exogenous:
        entry_start = entry.start_bucket * native_min
        entry_end = (entry.end_bucket + 1) * native_min
        first_native = max(entry.start_bucket, window_start // native_min)
        last_native = min(
            entry.end_bucket + 1,
            int(math.ceil(window_end / float(native_min))))
        for native_bucket in range(first_native, last_native):
            overlap_start = max(
                native_bucket * native_min, entry_start, window_start)
            overlap_end = min(
                (native_bucket + 1) * native_min, entry_end, window_end)
            if overlap_end <= overlap_start:
                continue
            first_slot = (overlap_start - window_start) // step
            slot_count = (overlap_end - overlap_start) // step
            physical_mass = int(round(
                float(entry.rate_tph)
                * (overlap_end - overlap_start) / 60.0))
            base, extra = divmod(max(physical_mass, 0), slot_count)
            for offset in range(slot_count):
                mass_t = base + (1 if offset < extra else 0)
                local_bucket = first_slot + offset
                exogenous.append(replace(
                    entry,
                    id=f"{entry.id}@{local_bucket}",
                    rate_tph=float(mass_t) * 60.0 / step,
                    start_bucket=local_bucket,
                    end_bucket=local_bucket))
    refined.exogenous = exogenous

    fixed_jobs: List[FixedJob] = []
    for entry in scenario.fixed_jobs:
        exact_start = getattr(entry, "start_minute", None)
        exact_end = getattr(entry, "end_minute", None)
        interval_start = (
            int(exact_start) if exact_start is not None
            else entry.start_bucket * native_min)
        interval_end = (
            int(exact_end) if exact_end is not None
            else (entry.end_bucket + 1) * native_min)
        mapped = _overlap_to_local_buckets(
            interval_start, interval_end,
            window_start, window_end, step)
        if mapped is not None:
            fixed_jobs.append(replace(
                entry, start_bucket=mapped[0], end_bucket=mapped[1],
                start_minute=max(interval_start, window_start) - window_start,
                end_minute=min(interval_end, window_end) - window_start))
    refined.fixed_jobs = fixed_jobs

    for old_link, new_link in zip(scenario.links, refined.links):
        delay_min = int(old_link.transit_buckets) * native_min
        new_link.transit_buckets = (
            int(math.ceil(delay_min / float(step))) if delay_min > 0 else 0)

    belt_profile: List[BeltProfileEntry] = []
    slots_per_native = native_min // step
    for entry in scenario.belt_profile:
        # A BeltProfileEntry is the aggregate mass arriving during its native
        # source bucket, not an instantaneous boundary pulse.  Placing the
        # whole hourly mass in one 15-minute slot can exceed the receiving
        # stacker's physical slot capacity and makes an otherwise identical
        # fine prefix infeasible.  Split the once-rounded mass exactly across
        # the source bucket, then retain only slots inside this rolling window.
        total_mass = max(int(round(float(entry.mass_t))), 0)
        base_mass, extra = divmod(total_mass, slots_per_native)
        source_start = int(entry.arrive_bucket) * native_min
        for offset in range(slots_per_native):
            slot_minute = source_start + offset * step
            if not window_start <= slot_minute < window_end:
                continue
            slot_mass = base_mass + (1 if offset < extra else 0)
            if slot_mass <= 0:
                continue
            belt_profile.append(replace(
                entry,
                arrive_bucket=(slot_minute - window_start) // step,
                mass_t=float(slot_mass)))
    refined.belt_profile = belt_profile

    refined.sim.dt_h = step / 60.0
    refined.sim.horizon_buckets = duration // step
    refined.sim.horizon_minutes = duration
    refined.sim.mixed_resolution_enabled = False
    refined.sim.frozen_buckets = int(math.ceil(
        max(int(commit_window_min or 0), 0) / float(step)))
    refined.sim.start_iso = scenario.sim.minute_dt(window_start).isoformat()
    # A finer decision grid must not turn a one-hour receiving-quality average
    # into four independent quarter-hour acceptance tests.  The optimiser
    # reads this flag and enforces each SpecRange.averaging_h over a physical
    # tumbling window on the refined grid.
    refined.config["QUALITY_WINDOW_AGGREGATION_ENABLED"] = True
    refined.config["PROCESS_YIELD_CARRY_WINDOW_BUCKETS"] = max(
        native_min // step, 1)
    refined.config["PROCESS_ZERO_CARRY_WINDOW_BUCKETS"] = max(
        native_min // step, 1)
    refined.config["MIXED_PREFIX_NATIVE_WINDOW_BUCKETS"] = max(
        native_min // step, 1)
    # Slightly above the default start/stop coefficient (w5*WSCALE = 20), so
    # a machine is not kept nominally active at a few t/h merely to avoid one
    # extra transition.  Physical min-rate, positioning and move constraints
    # still decide whether a compact campaign is feasible.
    refined.config.setdefault("ACTIVE_BUCKET_PENALTY", 25)
    return refined


def resample_banned_window(
        banned: Iterable[Tuple[str, str, str, int, int]], *,
        native_step_min: int, window_start_minute: int,
        window_duration_min: int, fine_step_min: int
        ) -> List[Tuple[str, str, str, int, int]]:
    """Convert operator-rejected native bucket ranges onto a fine window."""
    window_end = window_start_minute + window_duration_min
    result: List[Tuple[str, str, str, int, int]] = []
    for machine, space, destination, start_bucket, end_bucket in banned:
        mapped = _overlap_to_local_buckets(
            int(start_bucket) * native_step_min,
            (int(end_bucket) + 1) * native_step_min,
            window_start_minute, window_end, fine_step_min)
        if mapped is not None:
            result.append((machine, space, destination, mapped[0], mapped[1]))
    return result


def prefix_flow_totals(
        jobs: Iterable[Job], *, end_bucket_exclusive: int,
        process_output: bool = False) -> Dict[str, float]:
    """Return exact prefix totals by process-stage or dispatch-link id."""
    totals: Dict[str, float] = {}
    for job in jobs:
        key = job.process_stage if process_output else (
            job.dest_id if job.kind == "dispatch" else "")
        if not key:
            continue
        if job.bucket_flows:
            value = sum(
                (flow.output_tonnage_t if process_output
                 else flow.tonnage_t)
                for flow in job.bucket_flows
                if flow.bucket < end_bucket_exclusive)
        else:
            overlap = max(
                min(job.end_bucket + 1, end_bucket_exclusive)
                - job.start_bucket, 0)
            buckets = max(job.end_bucket - job.start_bucket + 1, 1)
            basis = (job.output_tonnage_t if process_output
                     else job.tonnage_t)
            value = float(basis) * overlap / buckets
        totals[key] = totals.get(key, 0.0) + float(value)
    return totals


def configure_prefix_boundary(
        refined: Scenario, tactical: PlanResult, *,
        tactical_boundary_index: int,
        absolute_boundary_bucket: int,
        absolute_start_bucket: int = 0) -> None:
    """Tie the fine prefix to the tactical plan's physical boundary state."""
    exact_inventory: Dict[str, float] = {}
    for space in refined.spaces:
        series = tactical.inventory.get(space.id, [])
        if tactical_boundary_index < len(series):
            exact_inventory[space.id] = float(
                round(series[tactical_boundary_index]))
    if exact_inventory:
        refined.config["TERMINAL_INVENTORY_EXACT_T"] = exact_inventory
        refined.config.pop("TERMINAL_INVENTORY_MIN_T", None)

    configured_stages = refined.config.get(
        "PROCESS_STAGE_OUTPUT_TARGET_T", {})
    if isinstance(configured_stages, dict):
        stage_totals = prefix_flow_totals(
            tactical.jobs,
            end_bucket_exclusive=absolute_boundary_bucket,
            process_output=True)
        refined.config["PROCESS_STAGE_OUTPUT_TARGET_T"] = {
            str(stage): round(float(stage_totals.get(str(stage), 0.0)))
            for stage in configured_stages
        }

    configured_corridors = refined.config.get(
        "CORRIDOR_DISPATCH_TARGET_T", {})
    if isinstance(configured_corridors, dict):
        corridor_totals = prefix_flow_totals(
            tactical.jobs,
            end_bucket_exclusive=absolute_boundary_bucket,
            process_output=False)
        refined.config["CORRIDOR_DISPATCH_TARGET_T"] = {
            str(link_id): round(float(corridor_totals.get(str(link_id), 0.0)))
            for link_id in configured_corridors
        }

    # This validation policy compares full-horizon Louta output and corridor
    # campaign totals.  A prefix may legitimately build or drain its buffer,
    # so equality at the intermediate boundary is not a physical requirement.
    refined.config["LOU_S0_STOCKED_ROUTE_ENABLED"] = False

    # Hierarchical coupling: preserve the tactical route/material decision in
    # every native hour, while the fine model remains free to place and rate
    # that mass in any of the four 15-minute slots.  This both guarantees the
    # exact tactical inventory boundary is reachable and leaves genuine
    # sub-hour dispatch decisions to the near-term optimiser.
    flow_targets: Dict[Tuple, float] = {}
    for job in tactical.jobs:
        for flow in job.bucket_flows:
            if not (absolute_start_bucket <= flow.bucket
                    < absolute_boundary_bucket):
                continue
            key = (
                int(flow.bucket), job.machine, job.kind, job.space,
                job.dest_id, job.grade, job.transfer_id,
                job.exit_node, tuple(flow.path))
            flow_targets[key] = (
                flow_targets.get(key, 0.0) + float(flow.tonnage_t))
    refined.config["MIXED_PREFIX_NATIVE_FLOW_TARGETS"] = [
        {
            "native_bucket": bucket,
            "machine": machine,
            "kind": kind,
            "space": space,
            "dest_id": destination,
            "grade": grade,
            "transfer_id": transfer_id,
            "exit_node": exit_node,
            "path": list(path),
            "tonnage_t": round(tonnage),
        }
        for (bucket, machine, kind, space, destination, grade, transfer_id,
             exit_node, path), tonnage in sorted(flow_targets.items())
    ]
    refined.config["MIXED_PREFIX_ABSOLUTE_START_BUCKET"] = int(
        absolute_start_bucket)


def align_tactical_job_minutes(
        plan: PlanResult, *, native_horizon_offset: int,
        absolute_start_minute: int, native_step_min: int) -> None:
    """Anchor a native tactical plan to an exact rolling-horizon minute."""
    nominal_start = native_horizon_offset * native_step_min
    shift = absolute_start_minute - nominal_start
    if shift == 0:
        return
    for job in plan.jobs:
        job.start_minute = (
            absolute_start_minute
            + (job.start_bucket - native_horizon_offset) * native_step_min)
        job.end_minute = (
            absolute_start_minute
            + (job.end_bucket - native_horizon_offset + 1)
            * native_step_min)
        for window in job.execution_windows:
            window.start_minute += shift
            if window.positioning_start_minute is not None:
                window.positioning_start_minute += shift


def _canonicalise_near_job(
        job: Job, *, absolute_start_minute: int, fine_step_min: int,
        native_step_min: int) -> Job:
    result = copy.deepcopy(job)
    local_start = job.start_bucket * fine_step_min
    local_end = (job.end_bucket + 1) * fine_step_min
    result.start_minute = absolute_start_minute + local_start
    result.end_minute = absolute_start_minute + local_end
    result.start_bucket = result.start_minute // native_step_min
    result.end_bucket = max(
        int(math.ceil(result.end_minute / float(native_step_min))) - 1,
        result.start_bucket)
    if result.origin != "HUMAN":
        result.id = f"N{fine_step_min}-{result.id}"

    grouped: Dict[Tuple[int, Tuple[str, ...]], JobBucketFlow] = {}
    for flow in result.bucket_flows:
        flow_start = absolute_start_minute + flow.bucket * fine_step_min
        native_bucket = flow_start // native_step_min
        key = native_bucket, tuple(flow.path)
        if key not in grouped:
            grouped[key] = JobBucketFlow(
                bucket=native_bucket, tonnage_t=0.0,
                path=list(flow.path), output_tonnage_t=0.0)
        grouped[key].tonnage_t += float(flow.tonnage_t)
        grouped[key].output_tonnage_t += float(flow.output_tonnage_t)
    result.bucket_flows = sorted(
        grouped.values(), key=lambda flow: (flow.bucket, tuple(flow.path)))

    for window in result.execution_windows:
        window.start_minute += absolute_start_minute
        if window.positioning_start_minute is not None:
            window.positioning_start_minute += absolute_start_minute
        window.solver_start_bucket = result.start_bucket
        window.solver_end_bucket = result.end_bucket
    return result


def _trim_tactical_job(
        job: Job, *, boundary_bucket: int,
        native_step_min: int) -> Optional[Job]:
    if job.end_bucket < boundary_bucket:
        return None
    result = copy.deepcopy(job)
    if result.bucket_flows:
        result.bucket_flows = [
            flow for flow in result.bucket_flows
            if flow.bucket >= boundary_bucket]
        if not result.bucket_flows:
            return None
        result.start_bucket = min(
            flow.bucket for flow in result.bucket_flows)
        result.end_bucket = max(
            flow.bucket for flow in result.bucket_flows)
        result.tonnage_t = sum(
            float(flow.tonnage_t) for flow in result.bucket_flows)
        result.output_tonnage_t = sum(
            float(flow.output_tonnage_t) for flow in result.bucket_flows)
    elif result.start_bucket < boundary_bucket:
        old_buckets = max(job.end_bucket - job.start_bucket + 1, 1)
        new_buckets = job.end_bucket - boundary_bucket + 1
        result.tonnage_t *= new_buckets / old_buckets
        result.output_tonnage_t *= new_buckets / old_buckets
        result.start_bucket = boundary_bucket

    source_start_minute = (
        job.start_minute if job.start_minute is not None
        else job.start_bucket * native_step_min)
    result.start_minute = (
        source_start_minute
        + (result.start_bucket - job.start_bucket) * native_step_min)
    result.end_minute = (
        source_start_minute
        + (result.end_bucket - job.start_bucket + 1) * native_step_min)
    duration_h = (result.end_minute - result.start_minute) / 60.0
    if duration_h > 0:
        result.rate_tph = round(result.tonnage_t / duration_h, 1)
    result.execution_windows = [
        window for window in result.execution_windows
        if window.start_minute >= result.start_minute]
    return result


def _aggregate_fine_rate_series(
        values: List[float], *, fine_step_min: int,
        native_step_min: int, native_count: int) -> List[float]:
    per_native = native_step_min // fine_step_min
    return [
        sum(float(value) * fine_step_min / native_step_min
            for value in values[i * per_native:(i + 1) * per_native])
        for i in range(native_count)
    ]


def _aggregate_fine_mass_series(
        values: List[float], *, fine_step_min: int,
        native_step_min: int, native_count: int) -> List[float]:
    per_native = native_step_min // fine_step_min
    return [
        sum(float(value)
            for value in values[i * per_native:(i + 1) * per_native])
        for i in range(native_count)
    ]


def merge_mixed_resolution_results(
        tactical: PlanResult, near: PlanResult, *,
        absolute_start_minute: int, near_horizon_min: int,
        fine_step_min: int, native_step_min: int,
        total_horizon_min: int, replan_interval_min: int,
        commit_window_min: int) -> PlanResult:
    """Merge an authoritative fine prefix with an hourly tactical tail."""
    result = copy.deepcopy(tactical)
    absolute_boundary_minute = absolute_start_minute + near_horizon_min
    boundary_bucket = absolute_boundary_minute // native_step_min
    prefix_native_count = near_horizon_min // native_step_min
    result.jobs = [
        _canonicalise_near_job(
            job, absolute_start_minute=absolute_start_minute,
            fine_step_min=fine_step_min,
            native_step_min=native_step_min)
        for job in near.jobs
    ]
    result.jobs.extend(
        tail for tail in (
            _trim_tactical_job(
                job, boundary_bucket=boundary_bucket,
                native_step_min=native_step_min)
            for job in tactical.jobs)
        if tail is not None)
    result.jobs.sort(key=lambda job: (
        job.start_minute if job.start_minute is not None
        else job.start_bucket * native_step_min,
        job.machine, job.id))

    for destination, values in near.delivered.items():
        if destination in result.delivered:
            result.delivered[destination][:prefix_native_count] = (
                _aggregate_fine_rate_series(
                    values, fine_step_min=fine_step_min,
                    native_step_min=native_step_min,
                    native_count=prefix_native_count))
    for attr in ("unmet", "offspec", "process_reject"):
        target = getattr(result, attr)
        source = getattr(near, attr)
        for key, values in source.items():
            if key in target:
                target[key][:prefix_native_count] = _aggregate_fine_mass_series(
                    values, fine_step_min=fine_step_min,
                    native_step_min=native_step_min,
                    native_count=prefix_native_count)
            else:
                target[key] = _aggregate_fine_mass_series(
                    values, fine_step_min=fine_step_min,
                    native_step_min=native_step_min,
                    native_count=prefix_native_count)
    for destination, values in near.feed_gap.items():
        prefix = _aggregate_fine_rate_series(
            values, fine_step_min=fine_step_min,
            native_step_min=native_step_min,
            native_count=prefix_native_count)
        if destination in result.feed_gap:
            result.feed_gap[destination][:prefix_native_count] = prefix
        else:
            result.feed_gap[destination] = prefix
    for destination, values in near.feed_interruption_start.items():
        prefix = _aggregate_fine_mass_series(
            values, fine_step_min=fine_step_min,
            native_step_min=native_step_min,
            native_count=prefix_native_count)
        if destination in result.feed_interruption_start:
            result.feed_interruption_start[
                destination][:prefix_native_count] = prefix
        else:
            result.feed_interruption_start[destination] = prefix
    if result.surge_unstacked:
        result.surge_unstacked[:prefix_native_count] = (
            _aggregate_fine_mass_series(
                near.surge_unstacked, fine_step_min=fine_step_min,
                native_step_min=native_step_min,
                native_count=prefix_native_count))
    per_native = native_step_min // fine_step_min
    boundary_inventory_max_abs_error_t = 0.0
    for space_id, values in near.inventory.items():
        if space_id in result.inventory:
            sampled = [
                float(values[index * per_native])
                for index in range(prefix_native_count + 1)]
            if prefix_native_count < len(result.inventory[space_id]):
                boundary_inventory_max_abs_error_t = max(
                    boundary_inventory_max_abs_error_t,
                    abs(sampled[-1] - float(
                        result.inventory[space_id][prefix_native_count])))
            result.inventory[space_id][:prefix_native_count + 1] = sampled

    result.status = near.status if near.status == "OPTIMAL" else tactical.status
    result.solver = "cp-sat-mixed"
    result.solver_wall_s = round(
        tactical.solver_wall_s + near.solver_wall_s, 3)
    result.solver_branches = int(getattr(
        tactical, "solver_branches", 0)) + int(getattr(
            near, "solver_branches", 0))
    result.solver_conflicts = int(getattr(
        tactical, "solver_conflicts", 0)) + int(getattr(
            near, "solver_conflicts", 0))
    result.solver_deterministic_time = float(getattr(
        tactical, "solver_deterministic_time", 0.0)) + float(getattr(
            near, "solver_deterministic_time", 0.0))
    # The merged job set has no single CP-SAT relaxation: its near prefix and
    # tactical reference were solved on different grids. Retain the tactical
    # objective/bound for backward compatibility, but scope it explicitly and
    # expose both component certificates below.
    result.solver_relative_gap_pct = float(getattr(
        tactical, "solver_relative_gap_pct", 0.0))
    result.solver_optimality_scope = "mixed_component_reference"
    result.horizon_offset = tactical.horizon_offset
    result.initial_machine_groups = dict(getattr(
        near, "initial_machine_groups", {}))
    result.initial_feed_gap = dict(getattr(
        near, "initial_feed_gap", {}))
    result.initial_open_feed_gap_minutes = dict(getattr(
        near, "initial_open_feed_gap_minutes", {}))
    has_tactical_tail = (
        prefix_native_count < total_horizon_min // native_step_min)
    boundary_source = tactical if has_tactical_tail else near
    result.terminal_machine_groups = dict(getattr(
        boundary_source, "terminal_machine_groups", {}))
    result.terminal_feed_gap = dict(getattr(
        boundary_source, "terminal_feed_gap", {}))
    result.terminal_open_feed_gap_minutes = dict(getattr(
        boundary_source, "terminal_open_feed_gap_minutes", {}))
    result.breakdown.update({
        "planning_mixed_resolution": 1.0,
        "planning_horizon_min": float(total_horizon_min),
        "planning_near_horizon_min": float(near_horizon_min),
        "planning_near_step_min": float(fine_step_min),
        "planning_far_step_min": float(native_step_min),
        "planning_replan_interval_min": float(replan_interval_min),
        "planning_commit_window_min": float(commit_window_min),
        "planning_near_solver_wall_s": float(near.solver_wall_s),
        "planning_tactical_solver_wall_s": float(tactical.solver_wall_s),
        "planning_boundary_inventory_max_abs_error_t": float(
            boundary_inventory_max_abs_error_t),
        "planning_near_objective": float(near.objective),
        "planning_near_best_objective_bound": float(getattr(
            near, "solver_best_bound", 0.0)),
        "planning_near_relative_gap_pct": float(getattr(
            near, "solver_relative_gap_pct", 0.0)),
        "planning_tactical_objective": float(tactical.objective),
        "planning_tactical_best_objective_bound": float(getattr(
            tactical, "solver_best_bound", 0.0)),
        "planning_tactical_relative_gap_pct": float(getattr(
            tactical, "solver_relative_gap_pct", 0.0)),
        "planning_objective_is_tactical_reference": 1.0,
        "planning_merged_optimality_certificate_available": 0.0,
        "solver_relative_gap_pct": result.solver_relative_gap_pct,
        "solver_branches": float(result.solver_branches),
        "solver_conflicts": float(result.solver_conflicts),
        "solver_deterministic_time": result.solver_deterministic_time,
        # This diagnostic belongs to the fine solve (the tactical solve has no
        # hierarchical prefix constraints), so carry it into the merged plan
        # explicitly instead of retaining the tactical default of zero.
        "hierarchical_native_flow_targets": float(
            near.breakdown.get("hierarchical_native_flow_targets", 0.0)),
    })
    # Recompute the transparent headline quantities for the merged prefix.
    result.breakdown["unmet_t"] = sum(
        sum(values) for values in result.unmet.values())
    result.breakdown["offspec_t_unit"] = sum(
        sum(values) for values in result.offspec.values())
    result.breakdown["process_reject_t"] = sum(
        sum(values) for values in result.process_reject.values())
    # Preserve exact fine-prefix feed duration/frequency, then continue each
    # open episode through the untouched tactical tail.  Native prefix values
    # above are fractions/counts specifically so downstream exports retain
    # these exact totals after resolution aggregation.
    gap_minutes = float(near.breakdown.get(
        "continuity_gap_minutes", 0.0))
    episode_count = float(near.breakdown.get(
        "continuity_episode_count", 0.0))
    maximum_episode = float(near.breakdown.get(
        "continuity_max_episode_minutes", 0.0))
    terminal_gap = dict(getattr(near, "terminal_feed_gap", {}))
    terminal_open = dict(getattr(
        near, "terminal_open_feed_gap_minutes", {}))
    for destination, values in tactical.feed_gap.items():
        previous = bool(terminal_gap.get(destination, False))
        open_minutes = float(terminal_open.get(destination, 0.0)) \
            if previous else 0.0
        for raw_gap in values[prefix_native_count:]:
            current = float(raw_gap) > 0.5
            if current:
                gap_minutes += native_step_min
                if not previous:
                    episode_count += 1.0
                    open_minutes = 0.0
                open_minutes += native_step_min
                maximum_episode = max(maximum_episode, open_minutes)
            else:
                open_minutes = 0.0
            previous = current
        terminal_gap[destination] = previous
        terminal_open[destination] = open_minutes
    result.terminal_feed_gap = terminal_gap
    result.terminal_open_feed_gap_minutes = terminal_open
    result.breakdown.update({
        "continuity_gap_minutes": gap_minutes,
        "continuity_episode_count": episode_count,
        "continuity_max_episode_minutes": maximum_episode,
        "continuous_feed_gap_minutes": gap_minutes,
        "continuous_feed_interruption_count": episode_count,
        "continuous_feed_max_episode_minutes": maximum_episode,
    })
    return result
