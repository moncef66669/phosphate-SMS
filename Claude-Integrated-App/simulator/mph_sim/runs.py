"""Physical runs, holds and executed machine transitions — one accounting.

The optimiser decides *hourly* activity, so its start/stop/changeover counters
answer the question "in how many buckets did this machine's activity group
change?".  Execution happens by the minute, and a bucket that is only partly
filled leaves blank minutes.  Before this module the two views disagreed
silently: a contiguous run of partly filled hours reported one start and one
stop while the minute schedule contained dozens of separate material bursts.
A Gantt drawn from the minute schedule then showed blank gaps that the KPIs
implicitly claimed were continuous production.

This module derives the *executed* picture from the minute schedule and gives
every blank gap an explicit name:

``ACTIVE``
    the machine is moving material (a job execution window).
``HOLD``
    the machine is deliberately kept running between two material bursts of
    the **same** activity group, with the gap no longer than the configured
    ``max_hold_min``.  This is an explicit state, not an assumption that the
    belt was full.
``CHANGEOVER``
    the machine moves between two *different* activity groups.  The gap is
    covered by the reserved positioning/preparation time, or by
    ``max_hold_min`` where no positioning was reserved.
``IDLE``
    any longer gap.  The machine genuinely stopped: the executed accounting
    counts one stop and one later start, whatever the hourly model counted.

A *physical run* is one maximal ACTIVE/HOLD chain of a single activity group
on one machine.  It is the object an operator recognises as "a job", and it
may legitimately span several allocation slices, several parent solver jobs,
and the rolling planning-day boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .campaigns import resolve_campaign_policy

#: Gap classifications, in the order they are reported.
GAP_HOLD = "HOLD"
GAP_CHANGEOVER = "CHANGEOVER"
GAP_IDLE = "IDLE"


@dataclass
class Segment:
    """One material burst: an execution window, or a whole bucket envelope."""

    machine: str
    group: Tuple[object, ...]
    job_id: str
    start_minute: int
    end_minute: int
    tonnage_t: float
    start_bucket: int
    end_bucket: int
    timing_status: str = ""
    #: the JobExecutionWindow this burst came from, when the minute overlay
    #: validated one.  Run identity is stamped back onto it, because a single
    #: allocation job can hold windows in SEVERAL physical runs.
    window: Optional[object] = None

    @property
    def duration_min(self) -> int:
        return max(int(self.end_minute) - int(self.start_minute), 0)


@dataclass
class Hold:
    """An explicit non-material interval between two segments."""

    machine: str
    kind: str                 # HOLD | CHANGEOVER | IDLE
    start_minute: int
    end_minute: int
    from_group: Optional[Tuple[object, ...]] = None
    to_group: Optional[Tuple[object, ...]] = None
    reserved_positioning_min: int = 0

    @property
    def duration_min(self) -> int:
        return max(int(self.end_minute) - int(self.start_minute), 0)


@dataclass
class PhysicalRun:
    """One maximal ACTIVE/HOLD chain of a single activity group."""

    id: str
    machine: str
    group: Tuple[object, ...]
    segments: List[Segment] = field(default_factory=list)
    holds: List[Hold] = field(default_factory=list)
    continues_initial_state: bool = False

    @property
    def start_minute(self) -> int:
        return min(segment.start_minute for segment in self.segments)

    @property
    def end_minute(self) -> int:
        return max(segment.end_minute for segment in self.segments)

    @property
    def tonnage_t(self) -> float:
        return sum(segment.tonnage_t for segment in self.segments)

    @property
    def active_minutes(self) -> int:
        return sum(segment.duration_min for segment in self.segments)

    @property
    def hold_minutes(self) -> int:
        return sum(hold.duration_min for hold in self.holds)

    @property
    def elapsed_minutes(self) -> int:
        return max(self.end_minute - self.start_minute, 0)

    @property
    def job_ids(self) -> List[str]:
        seen: List[str] = []
        for segment in self.segments:
            if segment.job_id not in seen:
                seen.append(segment.job_id)
        return seen

    def spans_days(self, day_minutes: int = 1_440) -> int:
        """Civil-day boundaries crossed inside this one physical run."""
        if day_minutes <= 0:
            return 0
        return max(self.end_minute - 1, self.start_minute) // day_minutes \
            - self.start_minute // day_minutes


def _job_group(job) -> Tuple[object, ...]:
    # Imported here to keep this module importable from the optimiser.
    from .optimizer import job_group_key
    return job_group_key(job)


def job_segments(sc, jobs: Iterable) -> List[Segment]:
    """Material bursts for every job, from validated minute windows.

    A job whose minute overlay did not validate contributes its authoritative
    solver bucket envelope instead — never a guessed narrower interval.
    """
    from .timing import (bucket_minutes, solver_window_minutes,
                         validated_execution_windows)

    width = bucket_minutes(sc)
    segments: List[Segment] = []
    for job in jobs:
        group = _job_group(job)
        windows = validated_execution_windows(job)
        if windows:
            for window in windows:
                segments.append(Segment(
                    machine=job.machine, group=group, job_id=job.id,
                    start_minute=int(window.start_minute),
                    end_minute=int(window.start_minute)
                    + int(window.duration_min),
                    tonnage_t=float(window.tonnage_t),
                    start_bucket=int(window.solver_start_bucket),
                    end_bucket=int(window.solver_end_bucket),
                    timing_status=str(window.status),
                    window=window,
                ))
            continue
        start, end = solver_window_minutes(sc, job)
        segments.append(Segment(
            machine=job.machine, group=group, job_id=job.id,
            start_minute=int(start), end_minute=int(end),
            tonnage_t=float(job.tonnage_t or 0.0),
            start_bucket=int(job.start_bucket),
            end_bucket=int(job.end_bucket),
            timing_status="BUCKET_ONLY",
        ))
    segments.sort(key=lambda segment: (
        segment.machine, segment.start_minute, segment.end_minute,
        segment.job_id))
    return segments


def _reserved_positioning_min(sc, jobs: Iterable) -> Dict[str, int]:
    """Positioning minutes the timing layer reserved before each job."""
    reserved: Dict[str, int] = {}
    for job in jobs:
        total = 0
        for window in getattr(job, "execution_windows", ()) or ():
            if window.positioning_start_minute is not None:
                total = max(total, int(window.positioning_duration_min or 0))
        if total:
            reserved[job.id] = total
    return reserved


def assigned_buckets(sc, jobs: Iterable,
                     hold_buckets: Optional[Mapping] = None)         -> Dict[str, set]:
    """Buckets in which the hourly plan keeps each machine running.

    That is every bucket where the machine moves material plus every bucket it
    explicitly pays to hold (``PlanResult.machine_hold_buckets``).  The minute
    layer consults this so a blank interval the hourly model already priced as
    continuous machine operation is reported as HELD, not as a stop and a
    later start.  It is the link that makes the two accountings agree instead
    of merely tolerating a difference.
    """
    result: Dict[str, set] = {}
    for job in jobs:
        rows = list(getattr(job, "bucket_flows", ()) or ())
        buckets = ({int(flow.bucket) for flow in rows} if rows
                   else set(range(int(job.start_bucket),
                                  int(job.end_bucket) + 1)))
        result.setdefault(job.machine, set()).update(buckets)
    for machine_id, held in (hold_buckets or {}).items():
        result.setdefault(str(machine_id), set()).update(
            int(bucket) for bucket in held or ())
    return result


def _buckets_touched(start_minute: int, end_minute: int, width: int) -> set:
    """Buckets overlapped by the half-open minute interval."""
    if width <= 0 or end_minute <= start_minute:
        return set()
    return set(range(start_minute // width,
                     (end_minute - 1) // width + 1))


def classify_gap(sc, previous: Segment, current: Segment,
                 reserved_positioning_min: int = 0, *,
                 machine_assignment: Optional[Mapping] = None,
                 bucket_minutes: Optional[int] = None) -> str:
    """Name the blank interval between two consecutive segments.

    A same-group blank interval is HELD when the hourly plan keeps the machine
    running right across it — every bucket it touches is one this machine works
    or explicitly holds.  Failing that, the configured ``max_hold_min``
    allowance applies, and anything longer is a real stop plus a later start.
    A changeover additionally tolerates whatever positioning time the minute
    overlay actually reserved.

    HELD is never a claim that material flowed: its minutes are reported
    separately as ``executed_hold_minutes``, never as active time.
    """
    policy = resolve_campaign_policy(sc, current.machine)
    gap = max(current.start_minute - previous.end_minute, 0)
    if gap <= 0:
        return ""
    if tuple(previous.group) == tuple(current.group):
        if machine_assignment is not None:
            width = int(bucket_minutes or 0)
            if width <= 0:
                from .timing import bucket_minutes as _width
                width = _width(sc)
            covered = machine_assignment.get(current.machine, set())
            touched = _buckets_touched(
                previous.end_minute, current.start_minute, width)
            if touched and touched <= set(covered):
                return GAP_HOLD
        return GAP_HOLD if gap <= policy.max_hold_min else GAP_IDLE
    allowance = max(int(policy.max_hold_min),
                    int(reserved_positioning_min or 0))
    return GAP_CHANGEOVER if gap <= allowance else GAP_IDLE


def physical_runs(
        sc, jobs: Iterable, *,
        initial_machine_groups: Optional[Mapping] = None,
        machine_hold_buckets: Optional[Mapping] = None) \
        -> List[PhysicalRun]:
    """Group material bursts into the runs an operator would recognise.

    Segments of one activity group separated only by a HOLD gap are one run,
    including across a rolling planning-day boundary: the machine never
    stopped, so the schedule must not present two jobs.
    """
    jobs = list(jobs)
    segments = job_segments(sc, jobs)
    reserved = _reserved_positioning_min(sc, jobs)
    assignment = assigned_buckets(sc, jobs, machine_hold_buckets)
    initial = {
        str(machine_id): tuple(group)
        for machine_id, group in (initial_machine_groups or {}).items()
        if isinstance(group, (list, tuple)) and group}

    by_machine: Dict[str, List[Segment]] = {}
    for segment in segments:
        by_machine.setdefault(segment.machine, []).append(segment)

    runs: List[PhysicalRun] = []
    for machine_id in sorted(by_machine):
        rows = by_machine[machine_id]
        current: Optional[PhysicalRun] = None
        index = 0
        for segment in rows:
            if current is not None:
                gap_kind = classify_gap(
                    sc, current.segments[-1], segment,
                    reserved.get(segment.job_id, 0),
                    machine_assignment=assignment)
                if gap_kind == GAP_HOLD:
                    current.holds.append(Hold(
                        machine=machine_id, kind=GAP_HOLD,
                        start_minute=current.segments[-1].end_minute,
                        end_minute=segment.start_minute,
                        from_group=current.group, to_group=segment.group))
                    current.segments.append(segment)
                    continue
                if gap_kind == "" and tuple(current.group) == \
                        tuple(segment.group):
                    current.segments.append(segment)
                    continue
            index += 1
            current = PhysicalRun(
                id=f"{machine_id}#R{index:03d}",
                machine=machine_id, group=tuple(segment.group),
                segments=[segment],
                continues_initial_state=(
                    index == 1
                    and initial.get(machine_id) == tuple(segment.group)),
            )
            runs.append(current)
    runs.sort(key=lambda run: (run.start_minute, run.machine, run.id))
    return runs


def executed_transition_metrics(
        sc, jobs: Iterable, *,
        horizon_start_minute: int, horizon_end_minute: int,
        initial_machine_groups: Optional[Mapping] = None,
        machine_hold_buckets: Optional[Mapping] = None) \
        -> Dict[str, float]:
    """Starts, stops, changeovers, hold and idle minutes as executed.

    These are *not* a re-derivation of the hourly counters with a different
    rounding: they answer a different question, and the answer is larger
    whenever the minute schedule breaks an hourly run into bursts.  Both sets
    of numbers are reported so the difference is visible instead of implied.
    """
    jobs = list(jobs)
    runs = physical_runs(
        sc, jobs, initial_machine_groups=initial_machine_groups,
        machine_hold_buckets=machine_hold_buckets)
    reserved = _reserved_positioning_min(sc, jobs)
    assignment = assigned_buckets(sc, jobs, machine_hold_buckets)
    from .timing import bucket_minutes as _bucket_minutes
    width = _bucket_minutes(sc)

    starts = stops = changeovers = 0
    hold_minutes = changeover_minutes = idle_minutes = 0
    active_minutes = 0
    reclassified = 0
    gaps: List[Hold] = []

    by_machine: Dict[str, List[PhysicalRun]] = {}
    for run in runs:
        by_machine.setdefault(run.machine, []).append(run)
        active_minutes += run.active_minutes
        hold_minutes += run.hold_minutes
        gaps.extend(run.holds)

    for machine_id in sorted(by_machine):
        machine_runs = sorted(
            by_machine[machine_id], key=lambda run: run.start_minute)
        first = machine_runs[0]
        first_bucket = int(horizon_start_minute) // max(width, 1)
        leading_assigned = first_bucket in set(assignment.get(machine_id, ()))
        if leading_assigned and not first.continues_initial_state:
            # Assigned from the first bucket: the leading blank is inside the
            # machine's own assigned bucket, so it is held, not idle.
            starts += 1
            hold_minutes += max(first.start_minute - horizon_start_minute, 0)
        elif first.continues_initial_state:
            # The machine was already running this activity group before the
            # window opened, so the leading interval is neither a start nor
            # idle time: it is a continuation of the carried state.
            hold_minutes += max(first.start_minute - horizon_start_minute, 0)
        else:
            starts += 1
            idle_minutes += max(first.start_minute - horizon_start_minute, 0)
        for previous, nxt in zip(machine_runs, machine_runs[1:]):
            kind = classify_gap(
                sc, previous.segments[-1], nxt.segments[0],
                reserved.get(nxt.segments[0].job_id, 0),
                machine_assignment=assignment)
            gap = max(nxt.start_minute - previous.end_minute, 0)
            gaps.append(Hold(
                machine=machine_id, kind=kind or GAP_CHANGEOVER,
                start_minute=previous.end_minute,
                end_minute=nxt.start_minute,
                from_group=previous.group, to_group=nxt.group,
                reserved_positioning_min=reserved.get(
                    nxt.segments[0].job_id, 0)))
            if kind == GAP_CHANGEOVER or (
                    kind == "" and tuple(previous.group) != tuple(nxt.group)):
                changeovers += 1
                changeover_minutes += gap
            else:
                stops += 1
                starts += 1
                idle_minutes += gap
                # A reclassification is ONLY when the hourly model priced this
                # as one changeover, which requires it to have seen continuous
                # activity: every bucket from the previous segment's to the
                # next segment's must be assigned to this machine. If any is
                # unassigned the hourly model already counted a stop and a
                # start, and there is nothing to reclassify.
                covered = set(assignment.get(machine_id, ()))
                span = range(int(previous.segments[-1].end_bucket),
                             int(nxt.segments[0].start_bucket) + 1)
                if (tuple(previous.group) != tuple(nxt.group)
                        and set(span) <= covered):
                    reclassified += 1
        last = machine_runs[-1]
        # Trailing convention, matched to the hourly model: a machine the plan
        # still assigns in the horizon's final bucket has not stopped INSIDE
        # the horizon, whatever minute its last material window ends. Its stop
        # lies outside the window and neither layer counts it. The blank
        # minutes are still reported, as held or idle on their own evidence.
        final_bucket = max(
            (int(horizon_end_minute) - 1) // max(width, 1), 0)
        still_assigned = final_bucket in set(
            assignment.get(machine_id, ()))
        trailing = max(horizon_end_minute - last.end_minute, 0)
        if not still_assigned and last.end_minute < horizon_end_minute:
            stops += 1
            idle_minutes += trailing
        elif still_assigned:
            hold_minutes += trailing
        else:
            idle_minutes += trailing

    return {
        "executed_starts": float(starts),
        "executed_stops": float(stops),
        "executed_changeovers": float(changeovers),
        "executed_state_transition_count": float(
            starts + stops + changeovers),
        "executed_physical_runs": float(len(runs)),
        "executed_material_segments": float(
            sum(len(run.segments) for run in runs)),
        "executed_active_minutes": float(active_minutes),
        "executed_hold_minutes": float(hold_minutes),
        "executed_changeover_minutes": float(changeover_minutes),
        "executed_idle_minutes": float(idle_minutes),
        "executed_hold_intervals": float(
            sum(1 for gap in gaps if gap.kind == GAP_HOLD)),
        "executed_changeover_reclassified_as_stop_start": float(reclassified),
        "executed_sub_15_min_segments": float(sum(
            1 for run in runs for segment in run.segments
            if 0 < segment.duration_min < 15)),
        "executed_sub_30_min_segments": float(sum(
            1 for run in runs for segment in run.segments
            if 0 < segment.duration_min < 30)),
    }


def hourly_transition_metrics(
        sc, jobs: Iterable, *,
        horizon_start_bucket: int, horizon_end_bucket: int,
        machine_hold_buckets: Optional[Mapping] = None,
        initial_machine_groups: Optional[Mapping] = None) -> Dict[str, float]:
    """Recompute the HOURLY counters from the extracted allocation.

    This mirrors the CP-SAT state machine exactly — a machine is *on* in a
    bucket when it moves material or the plan pays to hold it, and a held
    bucket carries the previous group — so it is an independent check on the
    solver's own ``scheduled_*`` numbers rather than a restatement of them.
    """
    jobs = list(jobs)
    material: Dict[str, Dict[int, Tuple[object, ...]]] = {}
    for job in jobs:
        group = _job_group(job)
        rows = list(getattr(job, "bucket_flows", ()) or ())
        buckets = ([int(flow.bucket) for flow in rows] if rows
                   else list(range(int(job.start_bucket),
                                   int(job.end_bucket) + 1)))
        for bucket in buckets:
            material.setdefault(job.machine, {})[bucket] = group
    held = {
        str(machine_id): {int(bucket) for bucket in (buckets or ())}
        for machine_id, buckets in (machine_hold_buckets or {}).items()}
    initial = {
        str(machine_id): tuple(group)
        for machine_id, group in (initial_machine_groups or {}).items()
        if isinstance(group, (list, tuple)) and group}

    starts = stops = changeovers = 0
    active_buckets = hold_buckets = 0
    for machine_id in sorted(set(material) | set(held)):
        groups = material.get(machine_id, {})
        holds = held.get(machine_id, set())
        previous_group = initial.get(machine_id)
        previous_on = previous_group is not None
        for bucket in range(int(horizon_start_bucket),
                            int(horizon_end_bucket)):
            group = groups.get(bucket)
            is_held = bucket in holds and group is None
            on = group is not None or is_held
            if group is not None:
                active_buckets += 1
            elif is_held:
                hold_buckets += 1
            current_group = (
                group if group is not None
                else (previous_group if is_held else None))
            if on and not previous_on:
                starts += 1
            elif previous_on and not on:
                stops += 1
            elif on and previous_on and current_group is not None \
                    and previous_group is not None \
                    and tuple(current_group) != tuple(previous_group):
                changeovers += 1
            previous_on = on
            previous_group = current_group if on else None
    return {
        "hourly_starts": float(starts),
        "hourly_stops": float(stops),
        "hourly_changeovers": float(changeovers),
        "hourly_transition_count": float(starts + stops + changeovers),
        "hourly_active_buckets": float(active_buckets),
        "hourly_hold_buckets": float(hold_buckets),
    }


def reconcile_transitions(hourly: Mapping, executed: Mapping) -> Dict[str, float]:
    """Close the hourly/executed gap with an exact identity, not a tolerance.

    The hourly model has no notion of how LONG a changeover takes: an A->B
    group change in adjacent buckets always costs exactly one changeover.  The
    minute schedule knows the real positioning gap, and when that gap exceeds
    the changeover allowance it is a genuine stop followed by a genuine start.
    Each such reclassification turns one hourly event into two executed events,
    so::

        executed_transitions - hourly_transitions
            == changeover_reclassified_as_stop_start

    ``transition_reconciliation_residual`` is whatever that identity fails to
    explain.  It must be zero; a non-zero value means the two layers disagree
    about the schedule itself, not merely about how to name an interval.
    """
    reclassified = float(executed.get(
        "executed_changeover_reclassified_as_stop_start", 0.0))
    hourly_total = float(hourly.get("hourly_transition_count", 0.0))
    executed_total = float(executed.get(
        "executed_state_transition_count", 0.0))
    return {
        "transition_reclassified_changeovers": reclassified,
        "transition_hourly_total": hourly_total,
        "transition_executed_total": executed_total,
        "transition_reconciliation_residual": (
            executed_total - hourly_total - reclassified),
    }


def window_boundary_delta(scheduled: Mapping, hourly: Mapping) -> float:
    """How much the per-window event sum exceeds the merged-week walk.

    In a rolling solve ``scheduled_*`` is the sum of each window's own counters.
    A window ends without a falling edge for a machine that is still running,
    and the next window re-derives that machine's entry from carried state, so
    the sum need not equal a single walk over the merged week. Reporting the
    difference keeps it a disclosed convention rather than an unexplained gap.
    """
    scheduled_total = (float(scheduled.get("scheduled_starts", 0.0))
                       + float(scheduled.get("scheduled_stops", 0.0))
                       + float(scheduled.get(
                           "scheduled_group_changeovers", 0.0)))
    return scheduled_total - float(hourly.get("hourly_transition_count", 0.0))


def micro_parcel_metrics(sc, jobs: Iterable) -> Dict[str, float]:
    """Count allocation slices below the configured parcel floor.

    Reported rather than hidden: a genuinely necessary short parcel must stay
    visible, and a plan full of them is a planning defect an operator should
    see immediately.
    """
    jobs = list(jobs)
    below = 0
    below_t = 0.0
    smallest = None
    slices = 0
    for job in jobs:
        policy = resolve_campaign_policy(sc, job.machine)
        try:
            stable = float(sc.machine(job.machine).rate_min_tph) * \
                float(sc.sim.dt_h)
        except (StopIteration, AttributeError, TypeError, ValueError):
            stable = 0.0
        floor_t = policy.parcel_floor_t(stable)
        rows: Sequence = list(getattr(job, "bucket_flows", ()) or ())
        if rows:
            per_bucket: Dict[int, float] = {}
            for flow in rows:
                per_bucket[int(flow.bucket)] = per_bucket.get(
                    int(flow.bucket), 0.0) + float(flow.tonnage_t or 0.0)
            values = list(per_bucket.values())
        else:
            values = [float(job.tonnage_t or 0.0)]
        for value in values:
            slices += 1
            smallest = value if smallest is None else min(smallest, value)
            if floor_t > 0 and value + 1e-9 < floor_t:
                below += 1
                below_t += value
    return {
        "allocation_slices": float(slices),
        "below_parcel_floor_slices": float(below),
        "below_parcel_floor_t": float(below_t),
        "smallest_allocation_slice_t": float(
            0.0 if smallest is None else smallest),
    }
