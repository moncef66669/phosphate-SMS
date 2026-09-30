"""Minute execution timing layered on top of bucket-level optimisation.

The material optimiser remains authoritative for allocation and reserves
inclusive ``Job.start_bucket`` / ``Job.end_bucket`` windows.  This module is a
deterministic second-stage refiner/validator only: it may compress exact
per-bucket tonnes into integer-minute windows, but it never moves material
between solver buckets or changes solver feasibility/KPIs.

Legacy jobs without ``bucket_flows`` deliberately remain on their complete
bucket envelopes and are labelled ``BUCKET_ONLY``.
"""
from __future__ import annotations

import datetime as dtm
import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .domain import (Job, JobBucketFlow, JobExecutionWindow, Scenario)
from .movement import estimate_transition, work_midpoint_x


VALIDATED = "VALIDATED"
BUCKET_ONLY = "BUCKET_ONLY"
UNVALIDATED = "UNVALIDATED"


@dataclass(frozen=True)
class TimingIssue:
    severity: str
    code: str
    message: str
    job_id: str = ""
    bucket: Optional[int] = None


@dataclass
class TimingRefinementResult:
    issues: List[TimingIssue] = field(default_factory=list)
    fallback_buckets: List[int] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not any(issue.severity == "ERROR" for issue in self.issues)


def bucket_minutes(sc: Scenario) -> int:
    """Native solver bucket length as a positive whole number of minutes."""
    raw = float(sc.sim.dt_h) * 60.0
    rounded = int(round(raw))
    if rounded <= 0 or abs(raw - rounded) > 1e-9:
        raise ValueError(
            f"minute overlay requires a whole-minute native bucket; "
            f"dt_h={sc.sim.dt_h:g}")
    return rounded


def minute_dt(sc: Scenario, minute: int) -> dtm.datetime:
    return dtm.datetime.fromisoformat(sc.sim.start_iso) + \
        dtm.timedelta(minutes=int(minute))


def solver_window_minutes(sc: Scenario, job: Job) -> Tuple[int, int]:
    """Half-open authoritative solver reservation ``[start, end)``."""
    width = bucket_minutes(sc)
    return job.start_bucket * width, (job.end_bucket + 1) * width


def effective_execution_rate_cap(sc: Scenario, job: Job) -> float:
    """Physical/effective machine ceiling for a job's minute overlay.

    Legacy jobs have a zero field and therefore use the scenario machine
    maximum.  A populated solve-specific cap may only make that ceiling lower,
    never override the physical machine maximum.
    """
    machine_cap = float(sc.machine(job.machine).rate_max_tph)
    solve_cap = float(getattr(job, "execution_rate_cap_tph", 0.0) or 0.0)
    return min(machine_cap, solve_cap) if solve_cap > 0 else machine_cap


def window_start_dt(sc: Scenario, window: JobExecutionWindow) -> dtm.datetime:
    return minute_dt(sc, window.start_minute)


def window_end_dt(sc: Scenario, window: JobExecutionWindow) -> dtm.datetime:
    return minute_dt(sc, window.start_minute + window.duration_min)


def legacy_bucket_window(sc: Scenario, job: Job) -> JobExecutionWindow:
    """Non-minute-refined fallback preserving the full solver envelope."""
    start, end = solver_window_minutes(sc, job)
    duration = max(end - start, 1)
    derived_rate = job.tonnage_t * 60.0 / duration \
        if job.tonnage_t > 0 else 0.0
    return JobExecutionWindow(
        solver_start_bucket=job.start_bucket,
        solver_end_bucket=job.end_bucket,
        start_minute=start,
        duration_min=duration,
        tonnage_t=float(job.tonnage_t),
        rate_tph=float(job.rate_tph or derived_rate),
        basis="LEGACY_BUCKET_ENVELOPE",
        status=BUCKET_ONLY,
        note="No exact per-bucket/path allocation; solver bucket envelope "
             "remains authoritative.")


def validated_execution_windows(job: Job) -> List[JobExecutionWindow]:
    """Return sorted minute windows only when every window is validated."""
    windows = sorted(
        list(job.execution_windows),
        key=lambda w: (w.start_minute, w.solver_start_bucket))
    return windows if windows and all(w.status == VALIDATED for w in windows) \
        else []


def display_execution_window(
        sc: Scenario, job: Job) -> Tuple[JobExecutionWindow, str]:
    """One safe display interval plus an explicit timing status.

    A single validated execution window drives minute chart timestamps/rate.
    Multiple validated segments cannot be represented truthfully by one bar,
    so the chart retains the solver envelope and discloses the segment count.
    """
    windows = validated_execution_windows(job)
    if len(windows) == 1:
        return windows[0], VALIDATED
    fallback = legacy_bucket_window(sc, job)
    if windows:
        return fallback, f"VALIDATED_SEGMENTS ({len(windows)})"
    statuses = {
        window.status for window in job.execution_windows}
    if UNVALIDATED in statuses:
        status = UNVALIDATED
    elif BUCKET_ONLY in statuses:
        status = BUCKET_ONLY
    elif statuses:
        status = sorted(statuses)[0]
    else:
        status = BUCKET_ONLY
    return fallback, status


def _flows_for_window(job: Job, window: JobExecutionWindow) \
        -> List[JobBucketFlow]:
    return [
        flow for flow in job.bucket_flows
        if window.solver_start_bucket <= flow.bucket
        <= window.solver_end_bucket
    ]


def _window_output_t(job: Job, window: JobExecutionWindow) -> float:
    """Material made available by one exact process-stage window."""
    flows = _flows_for_window(job, window)
    output_t = sum(
        float(getattr(flow, "output_tonnage_t", 0.0) or 0.0)
        for flow in flows)
    return output_t if output_t > 0 else sum(
        float(flow.tonnage_t) for flow in flows)


def _shared_groups(sc: Scenario) -> Dict[str, List[str]]:
    groups: Dict[str, List[str]] = {}
    for link in sc.links:
        if link.shared_group:
            groups.setdefault(link.shared_group, []).append(link.id)
    return groups


def _conflict_groups(sc: Scenario) -> Dict[str, List[str]]:
    groups: Dict[str, List[str]] = {}
    for link in sc.links:
        if link.conflict_group:
            groups.setdefault(link.conflict_group, []).append(link.id)
    return groups


def _exclusive_machine_groups(sc: Scenario) -> Dict[str, List[str]]:
    """Stable group id -> configured mutually exclusive machine ids."""
    groups: Dict[str, List[str]] = {}
    for site in sc.sites:
        for index, machines in enumerate(
                getattr(site, "exclusive_machine_groups", []) or []):
            unique = sorted(set(machines))
            if len(unique) > 1:
                groups[f"{site.id}:{index}"] = unique
    return groups


def _profile_resource_mass(
        sc: Scenario, flows: Sequence[JobBucketFlow]) \
        -> Tuple[Dict[str, float], Dict[str, float], Dict[str, set]]:
    """Mass carried by links/shared groups and active conflict-group links."""
    link_mass: Dict[str, float] = {}
    shared_mass: Dict[str, float] = {}
    conflict_links: Dict[str, set] = {}
    shared = _shared_groups(sc)
    link_shared = {
        link_id: group for group, ids in shared.items() for link_id in ids}
    link_conflict = {
        link.id: link.conflict_group for link in sc.links
        if link.conflict_group}

    for flow in flows:
        for link_id in flow.path:
            link_mass[link_id] = link_mass.get(link_id, 0.0) + flow.tonnage_t
            group = link_conflict.get(link_id)
            if group:
                conflict_links.setdefault(group, set()).add(link_id)
        # CP-SAT shared-group capacity counts one candidate stream once even
        # when its path contains several links from the same group.
        for group in {
                link_shared[link_id] for link_id in flow.path
                if link_id in link_shared}:
            shared_mass[group] = shared_mass.get(group, 0.0) + flow.tonnage_t
    return link_mass, shared_mass, conflict_links


def validate_execution_windows(
        sc: Scenario, jobs: Iterable[Job]) -> List[TimingIssue]:
    """Validate already-marked minute windows against physical resources.

    Only ``VALIDATED`` windows are examined.  Capacity checks use a
    deterministic one-minute sweep.  This validator is not the material
    optimiser and cannot make an unvalidated window dispatchable.
    """
    jobs = list(jobs)
    width = bucket_minutes(sc)
    links = {link.id: link for link in sc.links}
    shared = _shared_groups(sc)
    shared_cap = {
        group: min(links[link_id].capacity_tph for link_id in ids)
        for group, ids in shared.items()}
    issues: List[TimingIssue] = []
    seen: set = set()

    machine_use: Dict[Tuple[int, str], Tuple[str, int]] = {}
    machine_windows: Dict[
        str, List[Tuple[int, int, Job, JobExecutionWindow, float]]
    ] = {}
    link_load: Dict[Tuple[int, str], float] = {}
    shared_load: Dict[Tuple[int, str], float] = {}
    conflict_use: Dict[Tuple[int, str], set] = {}
    exclusive_use: Dict[Tuple[int, str], str] = {}
    exclusive_groups = _exclusive_machine_groups(sc)
    exclusive_of: Dict[str, List[str]] = {}
    for group, machine_ids in exclusive_groups.items():
        for machine_id in machine_ids:
            exclusive_of.setdefault(machine_id, []).append(group)

    def add(severity: str, code: str, message: str, job: Job,
            minute: Optional[int] = None):
        bucket = None if minute is None else minute // width
        key = (severity, code, job.id, bucket, message)
        if key not in seen:
            seen.add(key)
            issues.append(TimingIssue(
                severity, code, message, job.id, bucket))

    for job in jobs:
        try:
            machine = sc.machine(job.machine)
        except StopIteration:
            add("ERROR", "UNKNOWN_MACHINE",
                f"execution window references unknown machine {job.machine}",
                job)
            continue
        if not machine.available:
            add("ERROR", "MACHINE_UNAVAILABLE",
                f"execution window references unavailable machine "
                f"{job.machine}", job)
        windows = validated_execution_windows(job)
        seen_job_buckets: Dict[int, int] = {}
        for window_index, window in enumerate(windows, start=1):
            for covered_bucket in range(
                    window.solver_start_bucket,
                    window.solver_end_bucket + 1):
                prior = seen_job_buckets.get(covered_bucket)
                if prior is not None:
                    add("ERROR", "DUPLICATE_BUCKET_WINDOW",
                        f"job {job.id} has multiple validated execution "
                        f"windows for solver bucket {covered_bucket} "
                        f"(windows {prior} and {window_index})",
                        job, covered_bucket * width)
                else:
                    seen_job_buckets[covered_bucket] = window_index
            env_start = window.solver_start_bucket * width
            env_end = (window.solver_end_bucket + 1) * width
            win_end = window.start_minute + window.duration_min
            if window.duration_min <= 0 or \
                    window.start_minute < env_start or win_end > env_end:
                add("ERROR", "OUTSIDE_SOLVER_ENVELOPE",
                    "minute execution window lies outside its authoritative "
                    "solver bucket reservation", job)
                continue
            if window.start_minute < 0 or not isinstance(
                    window.start_minute, int) or not isinstance(
                        window.duration_min, int):
                add("ERROR", "BAD_MINUTE_WINDOW",
                    "start and duration must be non-negative integer minutes",
                    job)
                continue

            flows = _flows_for_window(job, window)
            if not flows:
                add("ERROR", "MISSING_BUCKET_PROFILE",
                    "validated timing requires exact per-bucket/path tonnes",
                    job)
                continue
            profile_t = sum(flow.tonnage_t for flow in flows)
            tol = max(0.1, 1e-6 * max(profile_t, window.tonnage_t, 1.0))
            if abs(profile_t - window.tonnage_t) > tol:
                add("ERROR", "WINDOW_MASS_MISMATCH",
                    f"window mass {window.tonnage_t:g} t differs from exact "
                    f"profile {profile_t:g} t", job)
                continue
            expected = window.rate_tph * window.duration_min / 60.0
            if abs(expected - window.tonnage_t) > tol:
                add("ERROR", "RATE_DURATION_MISMATCH",
                    "tonnage must equal execution rate × duration", job)
            if window.rate_tph > machine.rate_max_tph + 1e-6:
                add("ERROR", "MACHINE_RATE",
                    f"{window.rate_tph:g} t/h exceeds {job.machine} maximum "
                    f"{machine.rate_max_tph:g} t/h", job)
            effective_cap = effective_execution_rate_cap(sc, job)
            if window.rate_tph > effective_cap + 1e-6:
                add("ERROR", "EFFECTIVE_MACHINE_RATE_CAP",
                    f"{window.rate_tph:g} t/h exceeds {job.machine} "
                    f"solve-specific execution cap {effective_cap:g} t/h",
                    job)
            if machine.rate_min_tph > 0 and \
                    window.rate_tph + 1e-6 < machine.rate_min_tph:
                add("ERROR", "MACHINE_MIN_RATE",
                    f"{window.rate_tph:g} t/h is below {job.machine} stable "
                    f"minimum {machine.rate_min_tph:g} t/h", job)

            for exclusive_group in exclusive_of.get(job.machine, []):
                key = (window.solver_start_bucket, exclusive_group)
                owner = exclusive_use.get(key)
                if owner is not None and owner != job.machine:
                    add("ERROR", "EXCLUSIVE_MACHINE_GROUP",
                        f"{job.machine} and {owner} are mutually exclusive "
                        f"within solver bucket "
                        f"{window.solver_start_bucket}", job,
                        window.solver_start_bucket * width)
                else:
                    exclusive_use[key] = job.machine

            link_mass, shared_mass, conflict_links = \
                _profile_resource_mass(sc, flows)
            link_rates = {
                link_id: mass * 60.0 / window.duration_min
                for link_id, mass in link_mass.items()}
            shared_rates = {
                group: mass * 60.0 / window.duration_min
                for group, mass in shared_mass.items()}
            machine_windows.setdefault(job.machine, []).append((
                window.start_minute,
                win_end,
                job,
                window,
                work_midpoint_x(
                    sc, job.space, job.start_x, job.stop_x)))

            for link_id in link_mass:
                link = links.get(link_id)
                if link is None:
                    add("ERROR", "UNKNOWN_LINK",
                        f"bucket profile references unknown link {link_id}",
                        job)
                elif not link.available:
                    add("ERROR", "LINK_UNAVAILABLE",
                        f"bucket profile references unavailable link {link_id}",
                        job)

            for minute in range(window.start_minute, win_end):
                owner = machine_use.get((minute, job.machine))
                if owner is not None:
                    add("ERROR", "MACHINE_OVERLAP",
                        f"{job.machine} window {window_index} overlaps "
                        f"{owner[0]} window {owner[1]}", job, minute)
                machine_use[(minute, job.machine)] = (
                    job.id, window_index)

                absolute_bucket = minute // width
                if any(
                        cal.scope in ("GLOBAL", job.machine)
                        and cal.start_bucket <= absolute_bucket <= cal.end_bucket
                        for cal in sc.calendars):
                    add("ERROR", "WORK_CALENDAR",
                        f"{job.machine} minute window intersects a blocked "
                        "solver bucket", job, minute)

                for link_id, rate in link_rates.items():
                    key = (minute, link_id)
                    link_load[key] = link_load.get(key, 0.0) + rate
                    link = links.get(link_id)
                    if link is not None and \
                            link_load[key] > link.capacity_tph + 1e-6:
                        add("ERROR", "LINK_CAPACITY",
                            f"{link_id} instantaneous load "
                            f"{link_load[key]:g} t/h exceeds "
                            f"{link.capacity_tph:g} t/h", job, minute)
                for group, rate in shared_rates.items():
                    key = (minute, group)
                    shared_load[key] = shared_load.get(key, 0.0) + rate
                    if shared_load[key] > shared_cap[group] + 1e-6:
                        add("ERROR", "SHARED_CAPACITY",
                            f"{group} instantaneous load "
                            f"{shared_load[key]:g} t/h exceeds shared "
                            f"capacity {shared_cap[group]:g} t/h", job, minute)
                for group, active_links in conflict_links.items():
                    key = (minute, group)
                    conflict_use.setdefault(key, set()).update(active_links)
                    if len(conflict_use[key]) > 1:
                        add("ERROR", "CONFLICT_GROUP",
                            f"mutually exclusive links in {group} overlap",
                            job, minute)

    # Validate that the idle interval between consecutive work windows can
    # actually contain the configured non-material preparation activity.
    for machine_id, entries in machine_windows.items():
        ordered = sorted(entries, key=lambda item: (
            item[0], item[1], item[2].id))
        for previous, current in zip(ordered, ordered[1:]):
            _, previous_end, previous_job, _, previous_x = previous
            current_start, _, current_job, current_window, current_x = current
            if previous_job.space == current_job.space:
                continue
            estimate = estimate_transition(
                sc.machine(machine_id), previous_x, current_x,
                space_changed=True)
            available = current_start - previous_end
            if available < estimate.total_min:
                add(
                    "ERROR", "MACHINE_POSITIONING_GAP",
                    f"{machine_id} needs {estimate.total_min} min to position "
                    f"from {previous_job.space} to {current_job.space}, but "
                    f"only {max(available, 0)} min is reserved",
                    current_job, current_start)
                continue
            recorded_start = getattr(
                current_window, "positioning_start_minute", None)
            recorded_duration = int(getattr(
                current_window, "positioning_duration_min", 0) or 0)
            if recorded_start is None:
                positioning_start = current_start - estimate.total_min
                positioning_end = current_start
            else:
                positioning_start = int(recorded_start)
                positioning_end = positioning_start + recorded_duration
                if recorded_duration != estimate.total_min:
                    add(
                        "ERROR", "POSITIONING_DURATION",
                        f"{machine_id} reserved {recorded_duration} min for "
                        f"positioning, but {estimate.total_min} min is "
                        "required",
                        current_job, positioning_start)
                    continue
            if positioning_start < previous_end or \
                    positioning_end > current_start:
                add(
                    "ERROR", "MACHINE_POSITIONING_GAP",
                    f"{machine_id} positioning reservation is outside the "
                    "idle interval between consecutive work windows",
                    current_job, positioning_start)
                continue
            positioning_valid = True
            for minute in range(positioning_start, positioning_end):
                owner = machine_use.get((minute, machine_id))
                if owner is not None:
                    add(
                        "ERROR", "POSITIONING_MACHINE_OVERLAP",
                        f"{machine_id} positioning interval overlaps "
                        f"material work {owner[0]}",
                        current_job, minute)
                    positioning_valid = False
                    break
                absolute_bucket = minute // width
                if any(
                        cal.scope in ("GLOBAL", machine_id)
                        and cal.start_bucket <= absolute_bucket
                        <= cal.end_bucket
                        for cal in sc.calendars):
                    add(
                        "ERROR", "POSITIONING_WORK_CALENDAR",
                        f"{machine_id} positioning interval intersects a "
                        "blocked solver bucket",
                        current_job, minute)
                    positioning_valid = False
                    break
            if positioning_valid:
                for minute in range(positioning_start, positioning_end):
                    machine_use[(minute, machine_id)] = (
                        f"POSITIONING:{current_job.id}", 0)

    stage_windows: Dict[str, List[Tuple[JobExecutionWindow, float, str]]] = {}
    for job in jobs:
        if not job.process_stage:
            continue
        for window in validated_execution_windows(job):
            stage_windows.setdefault(job.process_stage, []).append((
                window, _window_output_t(job, window),
                getattr(job, "target_space", "") or ""))
    dependent_windows: Dict[
            Tuple[str, str], List[Tuple[int, Job, JobExecutionWindow]]] = {}
    for job in jobs:
        if job.depends_on_stage:
            key = (
                job.depends_on_stage,
                getattr(job, "source_space", "") or "")
            for window in validated_execution_windows(job):
                dependent_windows.setdefault(key, []).append((
                    window.start_minute, job, window))
    for (stage, source_space), entries in dependent_windows.items():
        consumed_t = 0.0
        for _start, job, window in sorted(
                entries, key=lambda item: (
                    item[0], item[2].solver_start_bucket, item[1].id)):
            predecessors = [
                (predecessor, output_t)
                for predecessor, output_t, target_space
                in stage_windows.get(stage, [])
                if (not source_space or target_space == source_space)
                and predecessor.start_minute + predecessor.duration_min
                + int(job.dependency_delay_min) <= window.start_minute
            ]
            if not predecessors:
                add("ERROR", "MISSING_PROCESS_DEPENDENCY",
                    f"{job.process_stage or job.id} has no earlier "
                    f"{stage} output past its residence delay", job,
                    window.start_minute)
                continue
            consumed_t += float(window.tonnage_t)
            matured_t = sum(output_t for _predecessor, output_t in predecessors)
            tolerance = max(0.1, 1e-6 * max(matured_t, consumed_t, 1.0))
            if consumed_t > matured_t + tolerance:
                add("ERROR", "PROCESS_RESIDENCE_DELAY",
                    f"{job.process_stage or job.id} consumes "
                    f"{consumed_t:g} t but only {matured_t:g} t of "
                    f"{stage} output has completed the "
                    f"{job.dependency_delay_min}-minute residence delay", job,
                    window.start_minute)
    return issues


def _legacy(job: Job, sc: Scenario):
    job.execution_windows = [legacy_bucket_window(sc, job)]


def _profile_errors(sc: Scenario, job: Job) -> List[TimingIssue]:
    issues: List[TimingIssue] = []
    if not job.bucket_flows:
        return issues
    if not any(machine.id == job.machine for machine in sc.machines):
        issues.append(TimingIssue(
            "ERROR", "UNKNOWN_MACHINE",
            f"bucket profile references unknown machine {job.machine}",
            job.id, None))
        return issues
    links = {link.id for link in sc.links}
    total = 0.0
    output_total = 0.0
    for flow in job.bucket_flows:
        total += flow.tonnage_t
        output_total += (
            flow.output_tonnage_t
            if flow.output_tonnage_t > 0 else flow.tonnage_t)
        if flow.tonnage_t <= 0:
            issues.append(TimingIssue(
                "ERROR", "BAD_BUCKET_PROFILE",
                "bucket-flow tonnage must be positive", job.id, flow.bucket))
        if not (job.start_bucket <= flow.bucket <= job.end_bucket):
            issues.append(TimingIssue(
                "ERROR", "PROFILE_OUTSIDE_JOB",
                "bucket-flow row lies outside the Job solver envelope",
                job.id, flow.bucket))
        unknown = [link_id for link_id in flow.path if link_id not in links]
        if unknown:
            issues.append(TimingIssue(
                "ERROR", "UNKNOWN_LINK",
                f"bucket-flow path contains unknown links: "
                f"{', '.join(unknown)}", job.id, flow.bucket))
    tol = max(0.1, 1e-6 * max(total, job.tonnage_t, 1.0))
    if abs(total - job.tonnage_t) > tol:
        issues.append(TimingIssue(
            "ERROR", "JOB_PROFILE_MASS_MISMATCH",
            f"bucket-flow total {total:g} t differs from job total "
            f"{job.tonnage_t:g} t", job.id, None))
    if job.transfer_id:
        output_tol = max(
            0.1, 1e-6 * max(output_total, job.output_tonnage_t, 1.0))
        if abs(output_total - job.output_tonnage_t) > output_tol:
            issues.append(TimingIssue(
                "ERROR", "JOB_OUTPUT_PROFILE_MASS_MISMATCH",
                f"bucket-flow output total {output_total:g} t differs from "
                f"job output {job.output_tonnage_t:g} t", job.id, None))
    return issues


def _target_duration(
        sc: Scenario, job: Job, flows: Sequence[JobBucketFlow],
        width: int) -> int:
    total = sum(flow.tonnage_t for flow in flows)
    required = total * 60.0 / max(
        effective_execution_rate_cap(sc, job), 1e-9)
    link_mass, shared_mass, _ = _profile_resource_mass(sc, flows)
    links = {link.id: link for link in sc.links}
    for link_id, mass in link_mass.items():
        if link_id in links:
            required = max(
                required, mass * 60.0 / max(links[link_id].capacity_tph, 1e-9))
    shared = _shared_groups(sc)
    for group, mass in shared_mass.items():
        cap = min(links[link_id].capacity_tph for link_id in shared[group])
        required = max(required, mass * 60.0 / max(cap, 1e-9))
    return max(1, min(int(math.ceil(required - 1e-12)), width))


def _make_window(
        job: Job, bucket: int, flows: Sequence[JobBucketFlow],
        duration: int, basis: str, width: int,
        start_minute: Optional[int] = None) -> JobExecutionWindow:
    tonnage = sum(flow.tonnage_t for flow in flows)
    return JobExecutionWindow(
        solver_start_bucket=bucket,
        solver_end_bucket=bucket,
        start_minute=(bucket * width if start_minute is None
                      else int(start_minute)),
        duration_min=int(duration),
        tonnage_t=float(tonnage),
        rate_tph=float(tonnage * 60.0 / max(duration, 1)),
        basis=basis,
        status=VALIDATED,
        note="Post-solve timing only; allocation and resource reservation "
             "remain bucket-authoritative.")


def _schedule_exact_windows(
        sc: Scenario, jobs: Sequence[Job], width: int) \
        -> Tuple[List[int], List[TimingIssue]]:
    """Place compressed windows at their earliest feasible minute.

    This is a deterministic list scheduler, not a second optimiser.  It keeps
    every window inside its native solver bucket and never changes its mass,
    route or duration.  Independent resources can overlap; constrained
    resources are checked minute by minute.

    Returns ordinary resource-fallback buckets plus explicit positioning
    conflicts. A positioning conflict does not trigger the bucket-average
    fallback because a full-bucket window would make the missing gap worse.
    """
    links = {link.id: link for link in sc.links}
    shared = _shared_groups(sc)
    shared_cap = {
        group: min(links[link_id].capacity_tph for link_id in ids)
        for group, ids in shared.items()}
    exclusive_groups = _exclusive_machine_groups(sc)
    entries_by_bucket: Dict[int, List[dict]] = {}

    for job in jobs:
        for window in job.execution_windows:
            flows = _flows_for_window(job, window)
            link_mass, shared_mass, conflict_links = \
                _profile_resource_mass(sc, flows)
            duration = max(int(window.duration_min), 1)
            entry = {
                "job": job,
                "window": window,
                "work_x": work_midpoint_x(
                    sc, job.space, job.start_x, job.stop_x),
                "stage_output_t": _window_output_t(job, window),
                "target_space": getattr(job, "target_space", "") or "",
                "link_rates": {
                    link_id: mass * 60.0 / duration
                    for link_id, mass in link_mass.items()},
                "shared_rates": {
                    group: mass * 60.0 / duration
                    for group, mass in shared_mass.items()},
                "conflict_links": conflict_links,
            }
            entries_by_bucket.setdefault(
                window.solver_start_bucket, []).append(entry)

    failed: List[int] = []
    positioning_issues: List[TimingIssue] = []
    scheduled_stage_all: Dict[
        str, List[Tuple[JobExecutionWindow, float, str]]
    ] = {}
    dependency_consumed_t: Dict[Tuple[str, str], float] = {}
    machine_last: Dict[str, dict] = {}
    machine_use: Dict[Tuple[int, str], str] = {}
    for bucket in sorted(entries_by_bucket):
        active_machines = {
            entry["job"].machine for entry in entries_by_bucket[bucket]}
        if any(len(active_machines.intersection(machine_ids)) > 1
               for machine_ids in exclusive_groups.values()):
            # This should be impossible for optimiser output because the
            # exclusivity is authoritative at bucket resolution. Do not use
            # minute staggering to disguise an invalid bucket allocation.
            failed.append(bucket)
            continue
        # Material resources remain bucket-local, while machine use is global
        # so positioning minutes between native buckets are real reservations.
        link_load: Dict[Tuple[int, str], float] = {}
        shared_load: Dict[Tuple[int, str], float] = {}
        conflict_use: Dict[Tuple[int, str], set] = {}
        machine_use_before = dict(machine_use)
        machine_last_before = {
            key: dict(value) for key, value in machine_last.items()}
        scheduled_stage_before = {
            key: list(value) for key, value in scheduled_stage_all.items()}
        dependency_consumed_before = dict(dependency_consumed_t)

        # Longest and most constrained first is deterministic and avoids
        # fragmenting the finite 60-minute placement horizon.
        entries = sorted(
            entries_by_bucket[bucket],
            key=lambda entry: (
                1 if entry["job"].depends_on_stage else 0,
                -entry["window"].duration_min,
                -(len(entry["link_rates"])
                  + len(entry["shared_rates"])
                  + len(entry["conflict_links"])),
                entry["job"].machine,
                entry["job"].id))
        bucket_start = bucket * width
        bucket_end = bucket_start + width

        def feasible(entry: dict, start: int) -> bool:
            job = entry["job"]
            window = entry["window"]
            end = start + window.duration_min
            if start < bucket_start or end > bucket_end:
                return False
            try:
                machine = sc.machine(job.machine)
            except StopIteration:
                return False
            if not machine.available or sc.machine_blocked(
                    job.machine, bucket):
                return False
            if window.rate_tph > effective_execution_rate_cap(
                    sc, job) + 1e-6:
                return False
            previous = machine_last.get(job.machine)
            if previous is not None and previous["space"] != job.space:
                if positioning_start_for(entry, start) is None:
                    return False
            if any(len(active_links) > 1 for active_links
                   in entry["conflict_links"].values()):
                return False
            if job.depends_on_stage:
                source_space = getattr(job, "source_space", "") or ""
                key = (job.depends_on_stage, source_space)
                matured_t = sum(
                    output_t
                    for predecessor, output_t, target_space
                    in scheduled_stage_all.get(job.depends_on_stage, [])
                    if (not source_space or target_space == source_space)
                    and predecessor.start_minute + predecessor.duration_min
                    + int(job.dependency_delay_min) <= start)
                required_t = (
                    dependency_consumed_t.get(key, 0.0)
                    + float(window.tonnage_t))
                tolerance = max(
                    0.1, 1e-6 * max(matured_t, required_t, 1.0))
                if required_t > matured_t + tolerance:
                    return False
            for link_id, rate in entry["link_rates"].items():
                link = links.get(link_id)
                if link is None or not link.available or \
                        rate > link.capacity_tph + 1e-6:
                    return False
            for group, rate in entry["shared_rates"].items():
                if group not in shared_cap or \
                        rate > shared_cap[group] + 1e-6:
                    return False

            for minute in range(start, end):
                if (minute, job.machine) in machine_use:
                    return False
                for link_id, rate in entry["link_rates"].items():
                    if link_load.get((minute, link_id), 0.0) + rate > \
                            links[link_id].capacity_tph + 1e-6:
                        return False
                for group, rate in entry["shared_rates"].items():
                    if shared_load.get((minute, group), 0.0) + rate > \
                            shared_cap[group] + 1e-6:
                        return False
                for group, active_links in \
                        entry["conflict_links"].items():
                    existing = conflict_use.get((minute, group), set())
                    if len(existing.union(active_links)) > 1:
                        return False
            return True

        def positioning_start_for(
                entry: dict, material_start: int) -> Optional[int]:
            """Return the latest feasible preparation slot before work.

            Positioning may finish early and be followed by waiting. Searching
            backwards preserves just-in-time placement whenever it is
            possible, while allowing a move before a blocked calendar bucket.
            """
            job = entry["job"]
            previous = machine_last.get(job.machine)
            if previous is None or previous["space"] == job.space:
                return material_start
            machine = sc.machine(job.machine)
            estimate = estimate_transition(
                machine,
                float(previous["work_x"]),
                float(entry["work_x"]),
                space_changed=True)
            if estimate.total_min <= 0:
                return material_start
            earliest = int(previous["end"])
            latest = material_start - estimate.total_min
            for candidate in range(latest, earliest - 1, -1):
                valid = True
                for minute in range(
                        candidate, candidate + estimate.total_min):
                    if (minute, job.machine) in machine_use:
                        valid = False
                        break
                    absolute_bucket = minute // width
                    if any(
                            cal.scope in ("GLOBAL", job.machine)
                            and cal.start_bucket <= absolute_bucket
                            <= cal.end_bucket
                            for cal in sc.calendars):
                        valid = False
                        break
                if valid:
                    return candidate
            return None

        def commit(entry: dict, start: int):
            job = entry["job"]
            window = entry["window"]
            window.start_minute = start
            if window.basis != "BUCKET_AVERAGE_FALLBACK":
                window.basis = "EARLIEST_FEASIBLE_MAX_RATE"
            window.note = (
                "Deterministic post-solve list schedule with machine "
                "positioning gaps; material allocation remains "
                "bucket-authoritative.")
            previous = machine_last.get(job.machine)
            if previous is not None and previous["space"] != job.space:
                estimate = estimate_transition(
                    sc.machine(job.machine),
                    float(previous["work_x"]),
                    float(entry["work_x"]),
                    space_changed=True)
                positioning_start = positioning_start_for(entry, start)
                if positioning_start is None:
                    raise RuntimeError(
                        "positioning slot disappeared between feasibility "
                        "check and deterministic commit")
                window.positioning_start_minute = positioning_start
                window.positioning_duration_min = estimate.total_min
                for minute in range(
                        positioning_start,
                        positioning_start + estimate.total_min):
                    machine_use[(minute, job.machine)] = \
                        f"POSITIONING:{job.id}"
            machine_last[job.machine] = {
                "end": start + window.duration_min,
                "space": job.space,
                "work_x": float(entry["work_x"]),
            }
            if job.process_stage:
                scheduled_stage_all.setdefault(
                    job.process_stage, []).append((
                        window, float(entry["stage_output_t"]),
                        str(entry["target_space"])))
            if job.depends_on_stage:
                key = (
                    job.depends_on_stage,
                    getattr(job, "source_space", "") or "")
                dependency_consumed_t[key] = (
                    dependency_consumed_t.get(key, 0.0)
                    + float(window.tonnage_t))
            for minute in range(start, start + window.duration_min):
                machine_use[(minute, job.machine)] = job.id
                for link_id, rate in entry["link_rates"].items():
                    key = (minute, link_id)
                    link_load[key] = link_load.get(key, 0.0) + rate
                for group, rate in entry["shared_rates"].items():
                    key = (minute, group)
                    shared_load[key] = shared_load.get(key, 0.0) + rate
                for group, active_links in \
                        entry["conflict_links"].items():
                    conflict_use.setdefault(
                        (minute, group), set()).update(active_links)

        bucket_failed = False
        for entry in entries:
            job = entry["job"]
            window = entry["window"]
            latest = bucket_end - entry["window"].duration_min
            earliest = bucket_start
            previous = machine_last.get(job.machine)
            estimate = None
            if previous is not None and previous["space"] != job.space:
                estimate = estimate_transition(
                    sc.machine(job.machine),
                    float(previous["work_x"]),
                    float(entry["work_x"]),
                    space_changed=True)
                earliest = max(
                    earliest, int(previous["end"]) + estimate.total_min)
            if earliest > latest:
                available = max(
                    latest - int(previous["end"]), 0) \
                    if previous is not None else 0
                window.status = UNVALIDATED
                window.positioning_start_minute = None
                window.positioning_duration_min = 0
                window.note = (
                    "Required machine positioning does not fit before this "
                    "bucket's material work; targeted replanning is required.")
                positioning_issues.append(TimingIssue(
                    "ERROR",
                    "MACHINE_POSITIONING_GAP",
                    f"{job.machine} needs "
                    f"{estimate.total_min if estimate else 0} min to position "
                    f"from {previous['space'] if previous else '?'} to "
                    f"{job.space}, but only {available} min is available",
                    job.id,
                    bucket))
                # Keep later transitions anchored to the authoritative
                # bucket envelope without claiming minute validation.
                machine_last[job.machine] = {
                    "end": bucket_end,
                    "space": job.space,
                    "work_x": float(entry["work_x"]),
                }
                continue
            start = next((
                minute for minute in range(earliest, latest + 1)
                if feasible(entry, minute)), None)
            if start is None:
                failed.append(bucket)
                bucket_failed = True
                break
            commit(entry, start)
        if bucket_failed:
            machine_use.clear()
            machine_use.update(machine_use_before)
            machine_last.clear()
            machine_last.update(machine_last_before)
            scheduled_stage_all.clear()
            scheduled_stage_all.update(scheduled_stage_before)
            dependency_consumed_t.clear()
            dependency_consumed_t.update(dependency_consumed_before)

    return sorted(set(failed)), positioning_issues


def refine_execution_timing(
        sc: Scenario, jobs: Iterable[Job]) -> TimingRefinementResult:
    """Attach deterministic minute windows to jobs with exact bucket profiles.

    Each exact bucket slice is compressed to the shortest safe integer
    duration at its individual maximum feasible rate, then assigned the
    earliest feasible start by a deterministic intra-bucket list scheduler.
    Independent resources may overlap.  When integer-minute windows truly
    cannot fit, every exact job in that bucket falls back to the complete
    bucket at its solver-average rate.  If even that cannot be validated, the
    windows are marked ``UNVALIDATED``.

    Jobs without exact profiles are never guessed: they stay ``BUCKET_ONLY``.
    """
    jobs = list(jobs)
    width = bucket_minutes(sc)
    result = TimingRefinementResult()
    exact: List[Job] = []

    for job in jobs:
        job.execution_windows = []
        errors = _profile_errors(sc, job)
        if not job.bucket_flows:
            _legacy(job, sc)
            continue
        if errors:
            result.issues.extend(errors)
            _legacy(job, sc)
            continue
        exact.append(job)
        by_bucket: Dict[int, List[JobBucketFlow]] = {}
        for flow in job.bucket_flows:
            by_bucket.setdefault(flow.bucket, []).append(flow)
        for bucket in sorted(by_bucket):
            flows = by_bucket[bucket]
            duration = _target_duration(sc, job, flows, width)
            job.execution_windows.append(_make_window(
                job, bucket, flows, duration, "MAX_RATE_REFINED", width))

    fallback_codes = {
        "LINK_CAPACITY", "SHARED_CAPACITY", "CONFLICT_GROUP",
        "MACHINE_OVERLAP"}
    fallback_set: set = set()
    positioning_issues: List[TimingIssue] = []
    all_exact_buckets = {
        window.solver_start_bucket
        for job in exact for window in job.execution_windows}
    # A fallback changes the global machine sequence. Re-run the complete
    # minute schedule so positioning and every later job are reconciled
    # against the rebuilt bucket instead of retaining stale placements.
    for _pass in range(len(all_exact_buckets) + 2):
        for job in exact:
            for window in job.execution_windows:
                window.status = VALIDATED
                window.positioning_start_minute = None
                window.positioning_duration_min = 0
        failed, positioning_issues = _schedule_exact_windows(
            sc, exact, width)
        checked = validate_execution_windows(sc, exact)
        candidate_fallbacks = set(failed).union({
            issue.bucket for issue in checked
            if issue.code in fallback_codes and issue.bucket is not None})
        new_fallbacks = candidate_fallbacks - fallback_set
        if not new_fallbacks:
            break
        fallback_set.update(new_fallbacks)
        for bucket in sorted(new_fallbacks):
            for job in exact:
                for index, window in enumerate(job.execution_windows):
                    if window.solver_start_bucket != bucket:
                        continue
                    flows = _flows_for_window(job, window)
                    job.execution_windows[index] = _make_window(
                        job, bucket, flows, width,
                        "BUCKET_AVERAGE_FALLBACK", width)
    result.issues.extend(positioning_issues)
    fallback_buckets = sorted(fallback_set)

    for bucket in fallback_buckets:
        result.issues.append(TimingIssue(
            "INFO", "BUCKET_AVERAGE_FALLBACK",
            "deterministic integer-minute windows could not all fit inside "
            "the native bucket; retained the full bucket at solver-average "
            "rates",
            bucket=bucket))

    final = validate_execution_windows(sc, exact)
    if final:
        affected = {
            (issue.job_id, issue.bucket)
            for issue in final if issue.severity == "ERROR"}
        bucket_resource_codes = {
            "LINK_CAPACITY", "SHARED_CAPACITY", "CONFLICT_GROUP",
            "MACHINE_OVERLAP", "EXCLUSIVE_MACHINE_GROUP",
            "DUPLICATE_BUCKET_WINDOW"}
        job_scoped_codes = {
            "MACHINE_POSITIONING_GAP", "POSITIONING_DURATION",
            "POSITIONING_MACHINE_OVERLAP", "POSITIONING_WORK_CALENDAR"}
        invalid_jobs = {
            issue.job_id for issue in final
            if issue.severity == "ERROR"
            and issue.code in job_scoped_codes}
        invalid_resource_buckets = {
            issue.bucket for issue in final
            if issue.severity == "ERROR"
            and issue.code in bucket_resource_codes
            and issue.bucket is not None}
        for job in exact:
            for window in job.execution_windows:
                if (job.id, window.solver_start_bucket) in affected or \
                        job.id in invalid_jobs or \
                        window.solver_start_bucket in \
                        invalid_resource_buckets or \
                        any(jid == job.id and bucket is None
                            for jid, bucket in affected):
                    window.status = UNVALIDATED
                    window.note = (
                        "Minute refinement failed validation; solver bucket "
                        "envelope remains authoritative.")
        result.issues.extend(final)
    result.fallback_buckets = fallback_buckets
    return result


def timing_export_rows(
        jobs: Iterable[Job], sc: Optional[Scenario] = None) -> List[dict]:
    """Flat, deterministic execution-window rows for audit/export."""
    rows = []
    for job in sorted(jobs, key=lambda item: item.id):
        windows = job.execution_windows or []
        if not windows:
            continue
        for index, window in enumerate(sorted(
                windows, key=lambda w: (w.start_minute,
                                        w.solver_start_bucket)), start=1):
            row = {
                "job_id": job.id,
                "window": index,
                "solver_buckets": (
                    f"B{window.solver_start_bucket}–"
                    f"B{window.solver_end_bucket}"),
                "solver_start_bucket": window.solver_start_bucket,
                "solver_end_bucket": window.solver_end_bucket,
                "start_minute": window.start_minute,
                "duration_min": window.duration_min,
                "end_minute": window.start_minute + window.duration_min,
                "tonnage_t": window.tonnage_t,
                "rate_tph": window.rate_tph,
                "execution_rate_cap_tph": (
                    effective_execution_rate_cap(sc, job)
                    if sc is not None
                    else job.execution_rate_cap_tph),
                "timing_basis": window.basis,
                "timing_status": window.status,
                "note": window.note,
                "positioning_start_minute":
                    window.positioning_start_minute,
                "positioning_duration_min":
                    window.positioning_duration_min,
            }
            if sc is not None:
                row["start_time"] = window_start_dt(sc, window).isoformat()
                row["end_time"] = window_end_dt(sc, window).isoformat()
            rows.append(row)
    return rows
