"""SMS / ASP adapter interfaces — layer 9.

The MVP's ASP plan/evaluation adapter is SIMULATED only.
`RabbitMqAspAdapter` is a stub for AspPlanItem / AspEvaluatePlanResponse /
JobUpdated. Read-only SpaceValue inventory acquisition is implemented
separately in `rabbitmq_inventory.py` but remains uncommissioned. The eight
ASP validation rules are re-implemented with the vendor severities from the FRS
(Q80130-MZI-M16-TAK-45000183621-DG):

    ERROR   : parameter sanity, work area, inventory availability,
              same-device overlap, work calendar
    WARNING : device availability/busy, spatial conflict (1-D min distance)
    INFO    : FIFO

>>> APPROXIMATION NOTICE: this is a re-implementation from the FRS text, NOT
the vendor's code. Exact behaviour requires the RabbitMQ ICD and the finally
configured telegram schema / Material-Grade-SQP setup (open item H1 + ICD).
"""
from __future__ import annotations

import abc
import datetime as dtm
from collections import Counter
from dataclasses import dataclass, field
from math import isfinite
from typing import Dict, List, Optional

from .asp_inventory import InventoryProjection
from .domain import Job, JobExecutionWindow, Scenario
from .timing import (VALIDATED, effective_execution_rate_cap,
                     minute_dt, validated_execution_windows, window_start_dt)


@dataclass
class AspPlanItem:
    """Vendor plan-item telegram fields (report §15.6/§15.8 mapping)."""
    JobId: str
    Device: str
    JobType: str            # Stacking | Reclaiming | Transport | ...
    Method: str             # cone-shell / chevron / bench ...
    Pile: str
    Material: str
    Grade: str
    ReferenceLocation: str  # ASP free-text source/destination Location
    StartTime: str          # ISO
    StartX: float
    StopX: float
    PlannedTonnage: float
    PlannedFlowRate: float
    Comment: str = ""


def _dur_h(it: "AspPlanItem") -> float:
    """Job duration in hours, clamped to one year (guards bad-rate items)."""
    if it.PlannedFlowRate <= 0:
        return 8760.0
    return min(it.PlannedTonnage / it.PlannedFlowRate, 8760.0)


def _utc_time(value: str) -> dtm.datetime:
    """Compare all plan times consistently; legacy naive times share UTC basis."""
    parsed = dtm.datetime.fromisoformat(value)
    return (parsed.replace(tzinfo=dtm.timezone.utc) if parsed.tzinfo is None
            else parsed.astimezone(dtm.timezone.utc))


@dataclass
class AspIssue:
    severity: str           # ERROR | WARNING | INFO
    rule: str
    message: str
    item_id: str = ""


def _job_to_plan_item(
        sc: Scenario, j: Job, window: Optional[JobExecutionWindow] = None,
        item_id: Optional[str] = None, window_index: int = 0,
        window_count: int = 0) -> AspPlanItem:
    sp = sc.space(j.space)
    grade = sc.grade(j.grade)
    jt = {"stack": "Stacking", "reclaim": "Reclaiming",
          "dispatch": "Reclaiming"}.get(j.kind, "Other")
    ref = j.dest_id if j.kind in ("reclaim", "dispatch") else \
        (j.path[0] if j.path else "reception")
    canonical_start = getattr(j, "start_minute", None)
    start_time = (
        minute_dt(sc, int(canonical_start))
        if canonical_start is not None
        else sc.sim.bucket_dt(j.start_bucket))
    tonnage_t = j.tonnage_t
    rate_tph = j.rate_tph
    comment = j.comment or f"{j.origin} item"
    execution_cap = effective_execution_rate_cap(sc, j)
    comment += f" · execution cap {execution_cap:g} t/h"
    if window is not None:
        start_time = window_start_dt(sc, window)
        tonnage_t = window.tonnage_t
        rate_tph = window.rate_tph
        comment += (
            f" · parent job {j.id} · VALIDATED minute overlay "
            f"{window_index}/{window_count} · solver buckets "
            f"{window.solver_start_bucket}–{window.solver_end_bucket}")
    return AspPlanItem(
        JobId=item_id or j.id, Device=j.machine, JobType=jt,
        Method=j.method if j.kind == "stack" else "bench",
        Pile=j.space,
        Material=grade.name if grade else (j.grade or sp.grade),
        Grade=j.grade or sp.grade,
        ReferenceLocation=str(ref),
        StartTime=start_time.isoformat(),
        StartX=j.start_x, StopX=j.stop_x,
        PlannedTonnage=tonnage_t,
        PlannedFlowRate=rate_tph,
        Comment=comment)


def job_to_plan_items(sc: Scenario, j: Job) -> List[AspPlanItem]:
    """Map one solver job to validated execution items.

    Exact minute timestamps/rates are emitted only when every attached
    execution window is validated.  A multi-bucket profile becomes one ASP
    item per native bucket, retaining the parent job in each comment and a
    deterministic ``-Wnn`` suffix.  Legacy jobs without an exact bucket
    profile keep the original single bucket-envelope item.  Exact-profile
    jobs with missing or non-validated execution timing emit no ASP item:
    they require targeted replanning rather than a misleading aggregate
    fallback.
    """
    if j.bucket_flows and (
            not j.execution_windows
            or any(window.status != VALIDATED
                   for window in j.execution_windows)):
        return []
    windows = validated_execution_windows(j)
    if not windows:
        return [_job_to_plan_item(sc, j)]
    count = len(windows)
    return [
        _job_to_plan_item(
            sc, j, window,
            item_id=(j.id if count == 1 else f"{j.id}-W{index:02d}"),
            window_index=index, window_count=count)
        for index, window in enumerate(windows, start=1)
    ]


def job_to_plan_item(sc: Scenario, j: Job) -> AspPlanItem:
    """Backward-compatible one-item mapping.

    A single validated window drives exact timing.  Multi-window jobs must use
    :func:`job_to_plan_items`; returning the legacy aggregate here avoids
    silently collapsing several minute segments into a false continuous bar.
    """
    windows = validated_execution_windows(j)
    if len(windows) == 1:
        return _job_to_plan_item(
            sc, j, windows[0], item_id=j.id,
            window_index=1, window_count=1)
    return _job_to_plan_item(sc, j)


# ---------------------------------------------------------------- interfaces
class AspAdapter(abc.ABC):
    """Integration surface towards ONE SMS/ASP instance.

    Louta, Benguerir and MPH run identical Innomotics/YardMaster SMS
    solutions (user-confirmed), so ONE adapter implementation serves all
    three — each SMS instance gets its own independently configured adapter
    object (site_id + endpoint/scope), never a copy of the class."""

    site_id: str = ""
    sms_id: str = ""

    @abc.abstractmethod
    def submit_plan_items(self, items: List[AspPlanItem]) -> List[AspIssue]:
        """AspPlanItem(s) -> AspEvaluatePlanResponse issues (2nd feasibility gate)."""

    @abc.abstractmethod
    def poll_job_updates(self) -> List[dict]:
        """JobUpdated actuals telegrams."""

    @abc.abstractmethod
    def request_space_values(self) -> List[dict]:
        """SpaceValueRequest/Response inventory cache (1-m segments)."""

    def health(self) -> dict:
        """Connection/health state — never implies live connectivity."""
        return {"mode": "unknown", "ok": False, "site": self.site_id}


class RabbitMqAspAdapter(AspAdapter):
    """FUTURE adapter — real RabbitMQ telegrams. NOT implemented in the MVP.

    One instance per SMS (Louta / Benguerir / MPH share the profile).
    Blocked on: telegram ICD + credentials per site (hypothesis H1), plan
    co-authoring governance (H3). Site endpoint, virtual host, exchange/
    queue/routing keys and permissions are TBC per site. See the report §17.
    """

    def __init__(self, site_id: str = "", sms_id: str = "",
                 endpoint: str = "", config: Optional[dict] = None):
        self.site_id = site_id
        self.sms_id = sms_id or (f"{site_id}-SMS" if site_id else "")
        self.endpoint = endpoint            # blank/TBC — never a credential
        self.config = dict(config or {})    # vhost/exchange/queue names: TBC

    def submit_plan_items(self, items):
        raise NotImplementedError("RabbitMQ ICD not yet available (H1) — "
                                  "use SimulatedAspAdapter")

    def poll_job_updates(self):
        raise NotImplementedError("RabbitMQ ICD not yet available (H1)")

    def request_space_values(self):
        raise NotImplementedError("RabbitMQ ICD not yet available (H1)")

    def health(self) -> dict:
        return {"mode": "not-configured", "ok": False, "site": self.site_id,
                "detail": "RabbitMQ ICD/credentials TBC — stub only"}


class SimulatedAspAdapter(AspAdapter):
    """Offline stand-in re-implementing the 8 ASP validation rules.

    Pass a site-specific scenario projection (see
    `integration.scenario_site_projection`) plus its `site_id` to scope the
    gate to one SMS instance's machines, piles, segments and calendars."""

    def __init__(self, sc: Scenario, min_distance_m: float = 50.0,
                 current_pile_only: bool = True, site_id: str = ""):
        self.sc = sc
        self.site_id = site_id
        self.sms_id = f"{site_id}-SMS" if site_id else ""
        self.min_distance_m = min_distance_m
        self.current_pile_only = current_pile_only
        self._updates: List[dict] = []

    def health(self) -> dict:
        return {"mode": "offline-simulated", "ok": True, "site": self.site_id,
                "detail": "no live ASP plan connection — simulated gate only"}

    # ---- validation (the 2nd gate) ------------------------------------
    def submit_plan_items(self, items: List[AspPlanItem]) -> List[AspIssue]:
        """Validate a complete proposed batch against a fresh inventory snapshot.

        Reserve reclaims in chronological order and credit stacks only on
        completion. Items with any ERROR never modify projected inventory.
        This is a conservative planning gate, not live job authorization.
        """
        issues: List[AspIssue] = []
        sane = []
        counts = Counter(it.JobId for it in items)
        for it in items:
            errors = self._rule_sanity(it)
            issues += errors
            if counts[it.JobId] > 1:
                issues.append(AspIssue("ERROR", "ParameterSanity",
                                       "Duplicate plan item identifier", it.JobId))
            if errors:
                continue
            sane.append(it)
            issues += self._rule_work_area(it)
            issues += self._rule_calendar(it)
        sane.sort(key=lambda it: (_utc_time(it.StartTime), it.JobId))
        issues += self._rule_overlap(sane)
        issues += self._rule_availability(sane)
        issues += self._rule_spatial(sane)
        blocked = {issue.item_id for issue in issues if issue.severity == "ERROR"}
        projection = InventoryProjection(self.sc)
        for it in sane:
            if it.JobId in blocked:
                continue
            start = _utc_time(it.StartTime)
            projection.advance(start)
            end = start + dtm.timedelta(hours=_dur_h(it))
            for message in projection.apply(it, end):
                issues.append(AspIssue("ERROR", "InventoryAvailability",
                                       f"Pile {it.Pile}: {message}", it.JobId))
            for message in sorted(projection.notes.get(it.Pile, ())):
                issues.append(AspIssue("INFO", "InventoryBasis", message, it.JobId))
        issues += self._rule_fifo(sane)
        return issues

    def poll_job_updates(self) -> List[dict]:
        out, self._updates = self._updates, []
        return out

    def push_update(self, u: dict):
        self._updates.append(u)

    def request_space_values(self) -> List[dict]:
        vals = []
        for s in self.sc.spaces:
            vals.append({"pile": s.id, "x_from": s.x_from, "x_to": s.x_to,
                         "mass_t": s.mass_t, "grade": s.grade,
                         "quality": dict(self.sc.quality_of(s.id))})
        return vals

    # ---- ERROR rules -----------------------------------------------------
    def _rule_sanity(self, it: AspPlanItem) -> List[AspIssue]:
        out = []
        e = lambda msg: out.append(AspIssue("ERROR", "ParameterSanity", msg, it.JobId))
        if not it.JobId:
            e("Missing plan item identifier")
        try:
            _utc_time(it.StartTime)
        except (ValueError, TypeError):
            e("Invalid start timestamp")
        values = (it.StartX, it.StopX, it.PlannedTonnage, it.PlannedFlowRate)
        if not all(isinstance(v, (int, float)) and isfinite(v) for v in values):
            e("Positions, tonnage and flow rate must be finite numbers")
            return out
        if not any(s.id == it.Pile for s in self.sc.spaces):
            e(f"Invalid pile ID: {it.Pile}")
        dev = next((m for m in self.sc.machines if m.id == it.Device), None)
        if dev is None:
            e(f"Invalid device: {it.Device}")
        if it.PlannedFlowRate <= 0:
            e(f"Invalid or missing rate (tph={it.PlannedFlowRate})")
        elif dev is not None and it.PlannedFlowRate > dev.rate_max_tph * 1.001:
            e(f"Rate {it.PlannedFlowRate} tph exceeds device maximum "
              f"{dev.rate_max_tph} tph")
        if it.StopX == it.StartX:
            e(f"Invalid range: fromX={it.StartX} == toX={it.StopX}")
        if it.PlannedTonnage <= 0:
            e("Planned tonnage must be positive")
        if it.PlannedFlowRate > 0 and it.PlannedTonnage > 0:
            try:
                end = _utc_time(it.StartTime) + dtm.timedelta(
                    hours=it.PlannedTonnage / it.PlannedFlowRate)
                if end <= _utc_time(it.StartTime) or \
                        it.PlannedTonnage / it.PlannedFlowRate > 8760:
                    e("Invalid or unsupported job duration")
            except (ValueError, TypeError, OverflowError):
                e("Invalid job end timestamp")
        return out

    def _rule_work_area(self, it: AspPlanItem) -> List[AspIssue]:
        dev = next((m for m in self.sc.machines if m.id == it.Device), None)
        sp = next((s for s in self.sc.spaces if s.id == it.Pile), None)
        if dev is None or sp is None:
            return []
        if it.Pile not in dev.reachable_spaces:
            return [AspIssue("ERROR", "WorkArea",
                             f"No work area configured for {it.Device} on {it.Pile}",
                             it.JobId)]
        wa = dev.work_area.get(it.Pile, [sp.x_from, sp.x_to])
        lo, hi = sorted((it.StartX, it.StopX))
        if lo < wa[0] - 1e-6 or hi > wa[1] + 1e-6:
            return [AspIssue("ERROR", "WorkArea",
                             f"Operation outside working area for {it.Device} on "
                             f"{it.Pile} ({wa[0]}–{wa[1]} m)", it.JobId)]
        return []

    def _rule_inventory(self, it: AspPlanItem) -> List[AspIssue]:
        """Single-item compatibility helper; batch callers use one shared ledger."""
        if self._rule_sanity(it) or self._rule_work_area(it):
            return []
        projection = InventoryProjection(self.sc)
        end = _utc_time(it.StartTime) + dtm.timedelta(hours=_dur_h(it))
        return [AspIssue("ERROR", "InventoryAvailability", message, it.JobId)
                for message in projection.apply(it, end)]

    def _rule_overlap(self, items: List[AspPlanItem]) -> List[AspIssue]:
        out = []
        by_dev: Dict[str, List[AspPlanItem]] = {}
        for it in items:
            by_dev.setdefault(it.Device, []).append(it)
        for dev, its in by_dev.items():
            import datetime as dtm
            parsed = []
            for i in its:
                t0 = _utc_time(i.StartTime)
                t1 = t0 + dtm.timedelta(hours=_dur_h(i))
                parsed.append((i, t0, t1))
            for a in range(len(parsed)):
                for b in range(a + 1, len(parsed)):
                    ia, a0, a1 = parsed[a]
                    ib, b0, b1 = parsed[b]
                    if a0 < b1 and b0 < a1:
                        out.append(AspIssue("ERROR", "SameDeviceOverlap",
                                            f"Device {dev} has overlapping jobs "
                                            f"{ia.JobId} / {ib.JobId}", ib.JobId))
        return out

    def _rule_calendar(self, it: AspPlanItem) -> List[AspIssue]:
        import datetime as dtm
        item_start = _utc_time(it.StartTime)
        item_end = item_start + dtm.timedelta(hours=_dur_h(it))
        origin = _utc_time(self.sc.sim.start_iso)
        for c in self.sc.calendars:
            if str(getattr(c, "action", "UNAVAILABLE") or
                   "UNAVAILABLE").strip().upper() != "UNAVAILABLE":
                continue
            start_minute, end_minute = self.sc.calendar_interval_minutes(c)
            blocked_start = origin + dtm.timedelta(minutes=start_minute)
            blocked_end = origin + dtm.timedelta(minutes=end_minute)
            if c.scope in ("GLOBAL", it.Device) and \
                    item_start < blocked_end and item_end > blocked_start:
                return [AspIssue(
                    "ERROR", "WorkCalendar",
                    f"{c.reason}: {c.scope} blocked on work calendar "
                    f"([{start_minute}, {end_minute}) min)", it.JobId)]
        return []

    # ---- WARNING rules -----------------------------------------------------
    def _rule_availability(self, items: List[AspPlanItem]) -> List[AspIssue]:
        out = []
        for it in items:
            dev = next((m for m in self.sc.machines if m.id == it.Device), None)
            if dev is not None and not dev.available:
                out.append(AspIssue("WARNING", "DeviceAvailability",
                                    f"Device {it.Device} is unavailable "
                                    "(offline or failed)", it.JobId))
        return out

    def _rule_spatial(self, items: List[AspPlanItem]) -> List[AspIssue]:
        out = []
        import datetime as dtm

        def interval(i):
            t0 = _utc_time(i.StartTime)
            t1 = t0 + dtm.timedelta(hours=_dur_h(i))
            return t0, t1
        for a in range(len(items)):
            for b in range(a + 1, len(items)):
                ia, ib = items[a], items[b]
                if ia.Device == ib.Device:
                    continue
                if self.current_pile_only and ia.Pile != ib.Pile:
                    continue
                a0, a1 = interval(ia)
                b0, b1 = interval(ib)
                if not (a0 < b1 and b0 < a1):
                    continue
                ax0, ax1 = sorted((ia.StartX, ia.StopX))
                bx0, bx1 = sorted((ib.StartX, ib.StopX))
                gap = max(bx0 - ax1, ax0 - bx1, 0.0)
                if gap < self.min_distance_m:
                    out.append(AspIssue("WARNING", "SpatialConflict",
                                        f"Spatial conflict: operations too close "
                                        f"({gap:.0f} m < {self.min_distance_m:.0f} m) "
                                        f"{ia.JobId}/{ib.JobId} on {ia.Pile}", ib.JobId))
        return out

    # ---- INFO rule -----------------------------------------------------------
    def _rule_fifo(self, items: List[AspPlanItem]) -> List[AspIssue]:
        out = []
        ages: Dict[str, float] = {}
        for seg in self.sc.segments:
            ages[seg.space_id] = max(ages.get(seg.space_id, 0.0), seg.age_days)
        for it in items:
            if it.JobType != "Reclaiming":
                continue
            sp = next((s for s in self.sc.spaces if s.id == it.Pile), None)
            if sp is None:
                continue
            my_age = ages.get(it.Pile, 0.0)
            for other in self.sc.spaces:
                if other.id != it.Pile and other.grade == sp.grade \
                        and other.mass_t > 0 and ages.get(other.id, 0.0) > my_age + 1:
                    out.append(AspIssue("INFO", "FIFO",
                                        f"Older material of grade {sp.grade} available "
                                        f"on pile {other.id} "
                                        f"({ages.get(other.id, 0):.0f} d vs "
                                        f"{my_age:.0f} d)", it.JobId))
                    break
        return out
