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
from dataclasses import dataclass, field, replace
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .campaigns import resolve_campaign_policy
from .domain import (Job, JobBucketFlow, JobExecutionWindow, Scenario)
from .flow_config import PROCESS_LINE_STAGE_MAP
from .movement import estimate_transition, work_midpoint_x


VALIDATED = "VALIDATED"
BUCKET_ONLY = "BUCKET_ONLY"
UNVALIDATED = "UNVALIDATED"
NATIVE_BUCKET_RATE = "NATIVE_BUCKET_RATE_VALIDATED"
#: Rate-credibility labels for a minute window. See
#: ``JobExecutionWindow.rate_credibility``.
RATE_STABLE = "STABLE_RATE"
RATE_NO_MINIMUM = "NO_STABLE_MINIMUM"
RATE_BELOW_PARCEL = "BELOW_MIN_PARCEL"
#: A window lengthened by :func:`_expand_to_stable_runs` so a campaign that
#: the solver allocated across adjacent buckets executes as ONE continuous
#: run at a stable rate, instead of a train of maximum-rate bursts separated
#: by blank minutes.  Mass, bucket and route are untouched.
STABLE_RUN = "STABLE_RUN_CONTINUOUS"


def _job_line_capacity_keys(job: Job) -> set[Tuple[str, str]]:
    """Line-cap groups matching the optimiser's physical-flow semantics."""
    keys: set[Tuple[str, str]] = set()
    if job.process_stage:
        keys.add((f"LINE:{job.process_stage}", job.process_stage))
        keys.update(
            (f"LINE:{line_id}", job.process_stage)
            for line_id, stages in PROCESS_LINE_STAGE_MAP.items()
            if job.process_stage in stages)
    if job.dest_id:
        keys.add((f"LINE:{job.dest_id}", job.dest_id))
    return keys


def _line_capacity_at_minute(
        sc: Scenario, scope: str, minute: int) -> Optional[float]:
    if sc.calendar_scope_blocked(scope, minute=minute):
        return 0.0
    line_id = scope[5:] if scope.startswith("LINE:") else scope
    base = sc.process_line_capacity_tph(line_id)
    timed = sc.resource_rate_cap_tph(scope, minute=minute)
    if base is None:
        return timed
    return min(base, timed) if timed is not None else base


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
    #: Windows lengthened into continuous stable-rate runs, and the total
    #: minutes added.  Reported so the difference between "how fast could
    #: this run" and "how this campaign is actually run" stays auditable.
    expanded_windows: int = 0
    expanded_minutes: int = 0

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
    """Half-open authoritative physical reservation ``[start, end)``.

    Mixed-resolution jobs carry canonical minute bounds because several
    independent 15-minute decisions may legitimately share one legacy/native
    hour.  Older plans continue to derive the same interval from bucket
    indices.
    """
    start_minute = getattr(job, "start_minute", None)
    end_minute = getattr(job, "end_minute", None)
    if start_minute is not None and end_minute is not None:
        start, end = int(start_minute), int(end_minute)
        if start >= 0 and end > start:
            return start, end
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


def _window_material_t(job, window, output=False):
    rows = [row for row in job.material_flows
            if window.solver_start_bucket <= row["bucket"] <= window.solver_end_bucket]
    total = sum(float(row["tonnage_t"]) for row in rows)
    factor = float(window.tonnage_t) / total if total else 0.
    values = {}
    for row in rows:
        mid = row["output_material_id"] if output else row["material_id"]
        amount = float(row["output_tonnage_t"] if output else row["tonnage_t"]) * factor
        values[mid] = values.get(mid, 0.) + amount
    return values


def _material_dependencies(sc, job, window, stage_entries, consumed):
    """Independent readiness by actual stream, including opening live stock."""
    sid = job.source_space or job.space
    checks = []
    for mid, amount in _window_material_t(job, window).items():
        key = (job.depends_on_stage, sid, mid)
        opening = float(sc.config.get("_WASHING_MODEL_INITIAL", {}).get(sid, {}).get(mid, 0.))
        pending = sum(float(batch["tonnage_t"]) for batch in
                      sc.config.get("_WASHING_MODEL_PENDING", {}).get(sid, {}).get(mid, [])
                      if batch["ready_minute"] > window.start_minute)
        matured = max(opening - pending, 0.) + sum(
            outputs.get(mid, 0.) for completion, _mass, target, outputs in stage_entries
            if target == sid and completion + job.dependency_delay_min <= window.start_minute)
        checks.append((key, consumed.get(key, 0.) + amount, matured, amount))
    return checks


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
    line_load: Dict[Tuple[int, str, str], float] = {}
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

                minute_machine_cap = sc.effective_resource_rate_tph(
                    job.machine, machine.rate_max_tph, minute=minute)
                if window.rate_tph > minute_machine_cap + 1e-6:
                    add("ERROR", "TIMED_MACHINE_RATE_CAP",
                        f"{job.machine} instantaneous rate "
                        f"{window.rate_tph:g} t/h exceeds active cap "
                        f"{minute_machine_cap:g} t/h", job, minute)

                if sc.calendar_scope_blocked(job.machine, minute=minute):
                    add("ERROR", "WORK_CALENDAR",
                        f"{job.machine} minute window intersects a blocked "
                        "calendar interval", job, minute)

                for link_id, rate in link_rates.items():
                    key = (minute, link_id)
                    link_load[key] = link_load.get(key, 0.0) + rate
                    link = links.get(link_id)
                    link_cap = (sc.effective_resource_rate_tph(
                        f"LINK:{link_id}", link.capacity_tph, minute=minute)
                                if link is not None else 0.0)
                    if link is not None and \
                            link_load[key] > link_cap + 1e-6:
                        add("ERROR", "LINK_CAPACITY",
                            f"{link_id} instantaneous load "
                            f"{link_load[key]:g} t/h exceeds "
                            f"active cap {link_cap:g} t/h", job, minute)
                for scope, group in _job_line_capacity_keys(job):
                    line_cap = _line_capacity_at_minute(sc, scope, minute)
                    if line_cap is None:
                        continue
                    key = (minute, scope, group)
                    line_load[key] = line_load.get(key, 0.0) \
                        + window.rate_tph
                    if line_load[key] > line_cap + 1e-6:
                        add("ERROR", "LINE_CAPACITY",
                            f"{scope[5:]} instantaneous load "
                            f"{line_load[key]:g} t/h exceeds active cap "
                            f"{line_cap:g} t/h", job, minute)
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
                if sc.calendar_scope_blocked(machine_id, minute=minute):
                    add(
                        "ERROR", "POSITIONING_WORK_CALENDAR",
                        f"{machine_id} positioning interval intersects a "
                        "blocked calendar interval",
                        current_job, minute)
                    positioning_valid = False
                    break
            if positioning_valid:
                for minute in range(positioning_start, positioning_end):
                    machine_use[(minute, machine_id)] = (
                        f"POSITIONING:{current_job.id}", 0)

    # Only dispatchable upstream jobs may certify production for dependent
    # work. A rejected positioning window is a planned allocation, not proof
    # that any material was produced at its bucket boundary. The adapter
    # rejects the whole parent job when any window is unresolved, so count
    # output only when every window on that job is validated.
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
    # Include newly discovered resource errors, not just stale status flags:
    # the export gate calls this validator without rerunning the refiner.
    blocked_producers = {issue.job_id for issue in issues
                         if issue.severity == "ERROR" and issue.job_id}
    for _dependency_pass in range(len(jobs) + 1):
        stage_windows = {}
        material_consumed = {}
        for producer in jobs:
            if not producer.process_stage or producer.id in blocked_producers:
                continue
            for window in validated_execution_windows(producer):
                completion = window.start_minute + window.duration_min
                stage_windows.setdefault(producer.process_stage, []).append((
                    completion, _window_output_t(producer, window),
                    getattr(producer, "target_space", "") or "",
                    _window_material_t(producer, window, output=True)))
        for (stage, source_space), entries in dependent_windows.items():
            consumed_t = 0.0
            for _start, job, window in sorted(
                    entries, key=lambda item: (
                        item[0], item[2].solver_start_bucket, item[1].id)):
                predecessors = [
                    (completion_minute, output_t)
                    for completion_minute, output_t, target_space, _outputs
                    in stage_windows.get(stage, [])
                    if (not source_space or target_space == source_space)
                    and completion_minute + int(job.dependency_delay_min)
                    <= window.start_minute
                ]
                if job.material_flows:
                    for key, required, ready, amount in _material_dependencies(
                            sc, job, window, stage_windows.get(stage, []), material_consumed):
                        material_consumed[key] = material_consumed.get(key, 0.) + amount
                        if required > ready + max(.1, 1e-6 * max(required, ready, 1.)):
                            add("ERROR", "PROCESS_RESIDENCE_DELAY",
                                f"material {key[2]} consumes {required:g} t but only {ready:g} t is mature",
                                job, window.start_minute)
                    continue
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
        blocked = {issue.job_id for issue in issues
                   if issue.severity == "ERROR" and issue.job_id}
        if blocked.issubset(blocked_producers):
            break
        blocked_producers.update(blocked)
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
        # A zero process output is a legitimate integer-yield result for a
        # very small input slice.  Falling back to input tonnes here invented
        # one tonne of output, rejected the producer profile and then caused a
        # false downstream PROCESS_RESIDENCE_DELAY cascade.  Only transport
        # jobs (no transfer_id) use input mass as their output mass.
        output_total += (
            flow.output_tonnage_t if job.transfer_id
            else (flow.output_tonnage_t
                  if flow.output_tonnage_t > 0 else flow.tonnage_t))
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


def stable_rate_max_duration(
        sc: Scenario, job: Job, tonnage_t: float, width: int) -> int:
    """Longest window that still runs at or above the stable minimum rate.

    A machine with no declared ``rate_min_tph`` has no physical floor in the
    model, so the whole bucket is admissible; the campaign policy then decides
    how far the window is actually lengthened.
    """
    try:
        minimum = float(sc.machine(job.machine).rate_min_tph)
    except (StopIteration, AttributeError, TypeError, ValueError):
        minimum = 0.0
    if minimum <= 0 or tonnage_t <= 0:
        return int(width)
    return max(1, min(
        int(math.floor(tonnage_t * 60.0 / minimum + 1e-9)), int(width)))


def _window_group_key(job: Job) -> Tuple[object, ...]:
    from .optimizer import job_group_key
    return job_group_key(job)


def _expand_to_stable_runs(
        sc: Scenario, jobs: Sequence[Job], width: int) -> Dict[str, int]:
    """Lengthen validated windows into continuous, stable-rate runs.

    The refiner above compresses each bucket's tonnes to the *shortest* safe
    duration at the maximum feasible rate.  That is the correct answer to
    "how fast could this be done", and the wrong answer to "how is this
    campaign actually run": a steady 500 t allocation on a 2,500 t/h reclaimer
    became a 12-minute burst plus 48 blank minutes, repeated for every hour of
    the campaign.  The schedule then showed dozens of short bars where one
    continuous run exists, and the hourly start/stop counters — which saw one
    uninterrupted activity run — disagreed with it.

    This pass changes no mass, no bucket, no route and no solver decision.  It
    only asks how long each already-allocated parcel should occupy its own
    bucket, bounded by

    * the machine's stable minimum rate (a longer window means a lower rate,
      never below ``Machine.rate_min_tph``);
    * the parcel's own solver bucket envelope;
    * minutes already occupied on that machine, including reserved
      positioning, and the positioning time the next window needs;
    * the configured campaign policy: ``min_run_min`` for an isolated parcel,
      and the whole bucket when the same activity group is also allocated in
      an adjacent bucket so the run is genuinely continuous.

    Growth is forward-first and start minutes move earlier only when no
    positioning is reserved against them.  The complete result is then
    re-validated by :func:`validate_execution_windows`; anything that fails is
    reverted, so this can improve a schedule but never invalidate one.
    """
    entries: Dict[str, List[Tuple[Job, JobExecutionWindow, float]]] = {}
    for job in jobs:
        for window in job.execution_windows:
            if window.status != VALIDATED:
                continue
            entries.setdefault(job.machine, []).append((
                job, window,
                work_midpoint_x(sc, job.space, job.start_x, job.stop_x)))
    if not entries:
        return {"expanded_windows": 0, "expanded_minutes": 0}

    # Which (machine, group, bucket) cells are allocated: an adjacent cell of
    # the same activity group means this parcel belongs to a continuous run.
    occupied_cells = {
        (job.machine, _window_group_key(job), window.solver_start_bucket)
        for job in jobs for window in job.execution_windows
        if window.status == VALIDATED}

    snapshot: Dict[Tuple[str, int], Tuple[int, int, float, str, str]] = {}
    for job in jobs:
        for window in job.execution_windows:
            snapshot[(job.id, window.solver_start_bucket)] = (
                window.start_minute, window.duration_min, window.rate_tph,
                window.basis, window.note)

    changed_by_machine: Dict[str, List[Tuple[str, int]]] = {}
    expanded_minutes = 0

    for machine_id in sorted(entries):
        rows = sorted(
            entries[machine_id],
            key=lambda row: (row[1].start_minute, row[1].solver_start_bucket,
                             row[0].id))
        policy = resolve_campaign_policy(sc, machine_id)
        try:
            machine = sc.machine(machine_id)
        except StopIteration:
            continue
        for index, (job, window, work_x) in enumerate(rows):
            start = int(window.start_minute)
            duration = int(window.duration_min)
            end = start + duration
            envelope_start = window.solver_start_bucket * width
            envelope_end = (window.solver_end_bucket + 1) * width

            # Forward limit: the next occupied minute on this machine, less
            # whatever positioning the next material window needs.
            forward_limit = envelope_end
            if index + 1 < len(rows):
                next_job, next_window, next_x = rows[index + 1]
                limit = int(next_window.start_minute)
                if next_window.positioning_start_minute is not None:
                    limit = min(
                        limit, int(next_window.positioning_start_minute))
                elif next_job.space != job.space:
                    limit -= estimate_transition(
                        machine, work_x, next_x, space_changed=True).total_min
                forward_limit = min(forward_limit, limit)
            # Backward limit: the previous occupied minute, plus this window's
            # own positioning requirement when nothing is reserved yet.
            backward_limit = envelope_start
            if window.positioning_start_minute is not None:
                backward_limit = max(backward_limit, start)
            elif index > 0:
                previous_job, previous_window, previous_x = rows[index - 1]
                limit = int(previous_window.start_minute) + \
                    int(previous_window.duration_min)
                if previous_job.space != job.space:
                    limit += estimate_transition(
                        machine, previous_x, work_x,
                        space_changed=True).total_min
                backward_limit = max(backward_limit, limit)

            available = max(forward_limit - backward_limit, 0)
            if available <= duration:
                continue

            group = _window_group_key(job)
            bucket = window.solver_start_bucket
            continuous = (
                (machine_id, group, bucket - 1) in occupied_cells
                or (machine_id, group, window.solver_end_bucket + 1)
                in occupied_cells)
            # A parcel below the configured minimum is NOT widened. Stretching
            # 1 t over a 20- or 60-minute window yields 3 or 1 t/h, which is
            # not a credible operating rate and would dress an implausible
            # allocation as a normal run. Such a parcel keeps its physical
            # minimum duration and is labelled; the surrounding minutes are
            # classified as held or idle on their own evidence.
            if window.rate_credibility == RATE_BELOW_PARCEL:
                continue
            target = int(width) if (continuous and policy.prefer_continuous) \
                else int(policy.min_run_min)
            if target <= duration:
                continue
            ceiling = stable_rate_max_duration(
                sc, job, float(window.tonnage_t), width)
            new_duration = max(duration, min(target, ceiling, available))
            if new_duration <= duration:
                continue

            # Grow forward first, then backward, so consecutive buckets of one
            # campaign meet at their shared bucket boundary.
            new_end = min(start + new_duration, forward_limit)
            new_start = max(new_end - new_duration, backward_limit)
            new_end = new_start + new_duration
            if new_end > forward_limit or new_start < backward_limit:
                continue
            if new_start < envelope_start or new_end > envelope_end:
                continue
            expanded_minutes += new_duration - duration
            window.start_minute = int(new_start)
            window.duration_min = int(new_duration)
            window.rate_tph = float(window.tonnage_t) * 60.0 / new_duration
            window.basis = STABLE_RUN
            window.note = (
                "Lengthened to a continuous stable-rate run inside its own "
                "solver bucket; mass, bucket and route unchanged."
                if continuous else
                "Lengthened to the configured minimum run duration inside "
                "its own solver bucket; mass, bucket and route unchanged.")
            changed_by_machine.setdefault(machine_id, []).append(
                (job.id, window.solver_start_bucket))
            rows[index] = (job, window, work_x)

    if not changed_by_machine:
        return {"expanded_windows": 0, "expanded_minutes": 0}

    def restore(keys: Iterable[Tuple[str, int]]) -> None:
        wanted = set(keys)
        for job in jobs:
            for window in job.execution_windows:
                key = (job.id, window.solver_start_bucket)
                if key not in wanted or key not in snapshot:
                    continue
                (window.start_minute, window.duration_min, window.rate_tph,
                 window.basis, window.note) = snapshot[key]

    # Keep only machines whose expansion survives full physical validation.
    for _attempt in range(len(changed_by_machine) + 1):
        issues = validate_execution_windows(sc, jobs)
        errors = [issue for issue in issues if issue.severity == "ERROR"]
        if not errors:
            break
        blamed_jobs = {issue.job_id for issue in errors if issue.job_id}
        culprits = {
            machine_id for machine_id, keys in changed_by_machine.items()
            if any(job_id in blamed_jobs for job_id, _bucket in keys)}
        if not culprits:
            culprits = set(changed_by_machine)
        for machine_id in sorted(culprits):
            restore(changed_by_machine.pop(machine_id, ()))
        if not changed_by_machine:
            break
    else:  # pragma: no cover - bounded loop exhausted
        for machine_id in list(changed_by_machine):
            restore(changed_by_machine.pop(machine_id, ()))

    kept = sum(len(keys) for keys in changed_by_machine.values())
    if not kept:
        return {"expanded_windows": 0, "expanded_minutes": 0}
    minutes = 0
    for job in jobs:
        for window in job.execution_windows:
            key = (job.id, window.solver_start_bucket)
            if key in snapshot and window.basis == STABLE_RUN:
                minutes += window.duration_min - snapshot[key][1]
    return {"expanded_windows": kept, "expanded_minutes": minutes}


def _schedule_exact_windows(
        sc: Scenario, jobs: Sequence[Job], width: int, *,
        shortest_first: bool = False) \
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
                "line_keys": _job_line_capacity_keys(job),
            }
            entries_by_bucket.setdefault(
                window.solver_start_bucket, []).append(entry)

    failed: List[int] = []
    positioning_issues: List[TimingIssue] = []
    scheduled_stage_all = {}
    dependency_consumed_t = {}
    machine_last: Dict[str, dict] = {}
    # Rolling windows carry the actual rail position and last served space.
    # Treat the civil-day boundary as the earliest instant at which the next
    # window may reposition; otherwise the first job of every day appears to
    # start from nowhere and only fails when the seven windows are merged.
    if entries_by_bucket:
        boundary_minute = min(entries_by_bucket) * width
        initial_spaces = sc.config.get("MACHINE_INITIAL_SPACE", {}) or {}
        if isinstance(initial_spaces, dict):
            for machine_id, space_id in initial_spaces.items():
                try:
                    machine = sc.machine(str(machine_id))
                except StopIteration:
                    continue
                machine_last[str(machine_id)] = {
                    "end": boundary_minute,
                    "space": str(space_id),
                    "work_x": float(machine.position_x),
                }
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
        line_load: Dict[Tuple[int, str, str], float] = {}
        shared_load: Dict[Tuple[int, str], float] = {}
        conflict_use: Dict[Tuple[int, str], set] = {}
        machine_use_before = dict(machine_use)
        machine_last_before = {
            key: dict(value) for key, value in machine_last.items()}
        scheduled_stage_before = {
            key: list(value) for key, value in scheduled_stage_all.items()}
        dependency_consumed_before = dict(dependency_consumed_t)

        # Longest first is the normal deterministic policy. A bounded
        # shortest-first retry can use the leading gap while another machine
        # positions, instead of fragmenting that gap around its longer job.
        entries = sorted(
            entries_by_bucket[bucket],
            key=lambda entry: (
                1 if entry["job"].depends_on_stage else 0,
                (1 if shortest_first else -1) * entry["window"].duration_min,
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
            if not machine.available:
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
                    for completion_minute, output_t, target_space, _outputs
                    in scheduled_stage_all.get(job.depends_on_stage, [])
                    if (not source_space or target_space == source_space)
                    and completion_minute + int(job.dependency_delay_min)
                    <= start)
                required_t = (
                    dependency_consumed_t.get(key, 0.0)
                    + float(window.tonnage_t))
                tolerance = max(
                    0.1, 1e-6 * max(matured_t, required_t, 1.0))
                if job.material_flows:
                    trial_window = replace(window, start_minute=start)
                    if any(required > ready + max(.1, 1e-6 * max(required, ready, 1.))
                           for _key, required, ready, _amount in _material_dependencies(
                               sc, job, trial_window, scheduled_stage_all.get(job.depends_on_stage, []),
                               dependency_consumed_t)):
                        return False
                elif required_t > matured_t + tolerance:
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
                if window.rate_tph > sc.effective_resource_rate_tph(
                        job.machine, machine.rate_max_tph,
                        minute=minute) + 1e-6:
                    return False
                for link_id, rate in entry["link_rates"].items():
                    link_cap = sc.effective_resource_rate_tph(
                        f"LINK:{link_id}", links[link_id].capacity_tph,
                        minute=minute)
                    if link_load.get((minute, link_id), 0.0) + rate > \
                            link_cap + 1e-6:
                        return False
                for scope, group in entry["line_keys"]:
                    line_cap = _line_capacity_at_minute(sc, scope, minute)
                    if line_cap is not None and (
                            line_load.get((minute, scope, group), 0.0)
                            + window.rate_tph > line_cap + 1e-6):
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
                    if sc.calendar_scope_blocked(
                            job.machine, minute=minute):
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
                        start + window.duration_min,
                        float(entry["stage_output_t"]),
                        str(entry["target_space"]), _window_material_t(job, window, output=True)))
            if job.depends_on_stage:
                key = (
                    job.depends_on_stage,
                    getattr(job, "source_space", "") or "")
                if job.material_flows:
                    for mid, amount in _window_material_t(job, window).items():
                        material_key = (*key, mid)
                        dependency_consumed_t[material_key] = dependency_consumed_t.get(material_key, 0.) + amount
                else:
                    dependency_consumed_t[key] = (
                        dependency_consumed_t.get(key, 0.0)
                        + float(window.tonnage_t))
            for minute in range(start, start + window.duration_min):
                machine_use[(minute, job.machine)] = job.id
                for link_id, rate in entry["link_rates"].items():
                    key = (minute, link_id)
                    link_load[key] = link_load.get(key, 0.0) + rate
                for scope, group in entry["line_keys"]:
                    if _line_capacity_at_minute(sc, scope, minute) is None:
                        continue
                    key = (minute, scope, group)
                    line_load[key] = line_load.get(key, 0.0) \
                        + window.rate_tph
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
                if job.process_stage:
                    scheduled_stage_all.setdefault(
                        job.process_stage, []).append((
                            bucket_end, float(entry["stage_output_t"]),
                            str(entry["target_space"]), _window_material_t(job, window, output=True)))
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
    Independent resources may overlap. If the normal order cannot fit, one
    shortest-first order is tried and accepted only when the complete minute
    schedule passes physical validation. Otherwise every exact job in the
    first failing bucket falls back to the complete
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
        policy = resolve_campaign_policy(sc, job.machine)
        try:
            machine = sc.machine(job.machine)
        except (StopIteration, AttributeError):
            machine = None
        # Credibility is judged against the POLICY parcel figure alone. The
        # machine-derived floor (a whole bucket at its stable minimum rate) is
        # a different thing: a parcel below THAT is perfectly credible, it just
        # runs for part of the bucket at the stable minimum. Conflating the two
        # would label every partial-bucket parcel implausible.
        parcel_t_floor = float(policy.min_parcel_t)
        for bucket in sorted(by_bucket):
            flows = by_bucket[bucket]
            duration = _target_duration(sc, job, flows, width)
            window = _make_window(
                job, bucket, flows, duration, "MAX_RATE_REFINED", width)
            parcel_t = sum(flow.tonnage_t for flow in flows)
            if parcel_t_floor > 0 and parcel_t + 1e-9 < parcel_t_floor:
                window.rate_credibility = RATE_BELOW_PARCEL
            elif machine is not None and machine.rate_min_tph > 0:
                window.rate_credibility = RATE_STABLE
            else:
                window.rate_credibility = RATE_NO_MINIMUM
            job.execution_windows.append(window)

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
        if failed or positioning_issues or any(
                issue.severity == "ERROR" for issue in checked):
            # Retry one alternative order, never a combinatorial search.
            # Keep the original result unless the entire candidate schedule
            # is placeable and physically valid, including dependencies and
            # preparation. Merely reducing the error count is insufficient.
            original_windows = [
                [replace(window) for window in job.execution_windows]
                for job in exact]
            for job in exact:
                for window in job.execution_windows:
                    window.status = VALIDATED
                    window.positioning_start_minute = None
                    window.positioning_duration_min = 0
            retry_failed, retry_positioning = _schedule_exact_windows(
                sc, exact, width, shortest_first=True)
            retry_checked = validate_execution_windows(sc, exact)
            if not retry_failed and not retry_positioning and not any(
                    issue.severity == "ERROR" for issue in retry_checked):
                failed, positioning_issues, checked = [], [], retry_checked
                for job in exact:
                    for window in job.execution_windows:
                        window.note += " Validated shortest-first ordering retry."
            else:
                for job, windows in zip(exact, original_windows):
                    job.execution_windows = windows
        # A failed bucket rolls back its material-production ledger. Later
        # dependency failures in the same pass can therefore be consequences
        # of that rollback, rather than independent scheduling conflicts.
        # Repair the earliest failure and rebuild before deciding whether a
        # later bucket needs slower work. Expanding all apparent failures at
        # once can erase a valid machine-positioning gap on an unrelated line.
        candidate_fallbacks = {min(failed)} if failed else {
            issue.bucket for issue in checked
            if issue.code in fallback_codes and issue.bucket is not None}
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

    # Turn maximum-rate bursts into continuous stable-rate runs wherever the
    # allocation, the machine floor and the reserved positioning allow it.
    # This runs before the UNVALIDATED sweep below so any expansion that does
    # not survive validation is reverted rather than downgrading a window.
    expansion = _expand_to_stable_runs(sc, exact, width)
    result.expanded_windows = int(expansion["expanded_windows"])
    result.expanded_minutes = int(expansion["expanded_minutes"])

    # Removing an invalid producer can invalidate a later consumer, which may
    # itself produce material for another stage. Iterate to a fixed point;
    # each changing pass holds at least one additional window, so this is
    # bounded by the number of exact jobs plus one final validation pass.
    for _validation_pass in range(len(exact) + 1):
        final = validate_execution_windows(sc, exact)
        if not final:
            break
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
        changed_status = False
        for job in exact:
            for window in job.execution_windows:
                if (job.id, window.solver_start_bucket) in affected or \
                        job.id in invalid_jobs or \
                        window.solver_start_bucket in \
                        invalid_resource_buckets or \
                        any(jid == job.id and bucket is None
                            for jid, bucket in affected):
                    changed_status |= window.status != UNVALIDATED
                    window.status = UNVALIDATED
                    window.note = (
                        "Minute refinement failed validation; solver bucket "
                        "envelope remains authoritative.")
        result.issues.extend(final)
        if not changed_status:
            break

    # In a native-hour weekly tactical solve, a capacity-saturated bucket can
    # be physically executable only as concurrent constant-rate work.  For
    # example, 2,100 t and 500 t sharing a 2,600 t/h conveyor need 49 + 12
    # minutes if serialised at maximum rate, but are exactly feasible together
    # for 60 minutes at 2,100 and 500 t/h.  Once the complete bucket-rate
    # profile passes the same minute-by-minute validator, it is an exact
    # schedule rather than a degraded fallback.  Keep the historical fallback
    # disclosure for non-weekly scenarios and for any bucket that did not
    # validate.
    if bool(sc.config.get("WEEKLY_ROLLING_SOLVE_ENABLED", False)):
        validated_native_buckets = set()
        for bucket in fallback_buckets:
            bucket_windows = [
                window for job in exact for window in job.execution_windows
                if window.solver_start_bucket == bucket]
            if bucket_windows and all(
                    window.status == VALIDATED for window in bucket_windows):
                validated_native_buckets.add(bucket)
                for window in bucket_windows:
                    window.basis = NATIVE_BUCKET_RATE
                    window.note = (
                        "Validated concurrent native-bucket rate schedule; "
                        "allocation and resource reservation remain "
                        "bucket-authoritative.")
        fallback_buckets = [
            bucket for bucket in fallback_buckets
            if bucket not in validated_native_buckets]

    for bucket in fallback_buckets:
        result.issues.append(TimingIssue(
            "INFO", "BUCKET_AVERAGE_FALLBACK",
            "deterministic integer-minute windows could not all fit inside "
            "the native bucket; retained the full bucket at solver-average "
            "rates",
            bucket=bucket))
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
                # Schedule identity, so an export consumer can tell an
                # allocation slice from the physical run it belongs to and
                # from a job split only by a rolling planning-day boundary.
                # Taken from THIS window: one allocation job can hold windows
                # in several physical runs when two of its buckets are
                # separated by a real stop.
                "rate_credibility": getattr(
                    window, "rate_credibility", "") or "",
                "physical_run": getattr(window, "physical_run_id", "") or "",
                "physical_run_job_count": int(getattr(
                    window, "physical_run_job_count", 1) or 1),
                "physical_run_hold_min": int(getattr(
                    window, "physical_run_hold_min", 0) or 0),
                "continues_previous_planning_day": bool(getattr(
                    window, "continues_previous_planning_day", False)),
                "parent_physical_runs": " ".join(
                    getattr(job, "physical_run_ids", ()) or ()),
                "allocation_slice_count": int(getattr(
                    job, "allocation_slice_count", 1) or 1),
            }
            if sc is not None:
                row["start_time"] = window_start_dt(sc, window).isoformat()
                row["end_time"] = window_end_dt(sc, window).isoformat()
            rows.append(row)
    return rows
