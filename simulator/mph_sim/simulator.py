"""Discrete-time closed-loop simulation engine — layer 5.

Models the report's loop: plan -> validate -> dispatch -> execute -> feedback
(JobUpdated) -> detect deviation -> replan (§15.8). Runs entirely offline on
the scenario snapshot; supports the 11 required disturbance event types.
Fully deterministic for a given (scenario, plan, event list) - the stored
seed is threaded through the solver for deterministic replay.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .domain import (BeltProfileEntry, FixedJob, Job, JobUpdate, LogRecord,
                     PlanResult, Scenario, SimEvent)
from .coordinates import x_interval_to_reperes
from .optimizer import SolveOptions, solve_plan


@dataclass
class BeltParcel:
    link_id: str
    arrive_bucket: int
    mass_t: float
    grade: str
    quality: Dict[str, float] = field(default_factory=dict)


class Simulator:
    def __init__(self, scenario: Scenario, plan: PlanResult,
                 events: Optional[List[SimEvent]] = None,
                 auto_replan: bool = True):
        self.sc = copy.deepcopy(scenario)
        self.plan = plan
        self.auto_replan = auto_replan
        self.bucket = 0
        self.T = scenario.sim.horizon_buckets
        self.dt = scenario.sim.dt_h

        self.jobs: Dict[str, Job] = {j.id: copy.deepcopy(j) for j in plan.jobs}
        self.events: List[SimEvent] = sorted(copy.deepcopy(events or []),
                                             key=lambda e: e.bucket)
        self.log: List[LogRecord] = []
        self.job_updates: List[JobUpdate] = []

        # live state ------------------------------------------------------
        self.inv: Dict[str, float] = {s.id: s.mass_t for s in self.sc.spaces}
        self.space_quality: Dict[str, Dict[str, float]] = {
            s.id: dict(self.sc.quality_of(s.id)) for s in self.sc.spaces}
        self.machine_down_until: Dict[str, int] = {}
        self.machine_rate_factor: Dict[str, float] = {}
        self.link_down_until: Dict[str, int] = {}
        self.demand_override: Dict[Tuple[str, int], float] = {}
        self.comms_down_until: int = -1
        self.machine_pos: Dict[str, float] = {m.id: m.position_x
                                              for m in self.sc.machines}
        # belt parcels without an explicit quality vector default to their
        # declared grade's vector (same convention as Scenario.quality_of);
        # an empty dict would otherwise blend in as ZERO quality and dilute
        # every pile it is stacked onto (defect fixed 14-Jul-2026)
        def _parcel_quality(e) -> dict:
            if e.quality:
                return dict(e.quality)
            g = self.sc.grade(e.grade)
            return dict(g.quality) if g else {}

        self.belts: List[BeltParcel] = [
            BeltParcel(e.link_id, e.arrive_bucket, e.mass_t, e.grade,
                       _parcel_quality(e))
            for e in self.sc.belt_profile]
        self.moved_by_job: Dict[str, float] = {}
        self.banned: List[Tuple[str, str, str, int, int]] = []

        # per-bucket records ------------------------------------------------
        self.delivered: Dict[str, List[float]] = {d.id: [0.0] * self.T
                                                  for d in self.sc.destinations}
        self.delivered_q: Dict[str, List[Dict[str, float]]] = {
            d.id: [dict() for _ in range(self.T)] for d in self.sc.destinations}
        self.inv_series: Dict[str, List[float]] = {s.id: [s.mass_t]
                                                   for s in self.sc.spaces}
        # per-bucket history for the Stockyard Monitor (additive; index 0 =
        # initial state, index t+1 = state after bucket t, like inv_series)
        self.quality_series: Dict[str, List[Dict[str, float]]] = {
            s.id: [dict(self.sc.quality_of(s.id))] for s in self.sc.spaces}
        # executed activity per bucket: what actually moved (survives replans)
        self.activity_log: List[Dict[str, object]] = []
        # outage history for the process mimic (additive): down_series[t] =
        # machines in a failure window during bucket t; link_down_series[t]
        # likewise for tripped conveyors/routes
        self.down_series: List[List[str]] = []
        self.link_down_series: List[List[str]] = []
        self.machine_active: Dict[str, List[int]] = {m.id: [0] * self.T
                                                     for m in self.sc.machines}
        self.link_load: Dict[str, List[float]] = {l.id: [0.0] * self.T
                                                  for l in self.sc.links}
        self.travel_m: Dict[str, float] = {m.id: 0.0 for m in self.sc.machines}
        self.starts_stops: Dict[str, int] = {m.id: 0 for m in self.sc.machines}
        self._prev_space: Dict[str, str] = {}
        self.replans = 0
        self.replan_solve_s: List[float] = []
        self.plan_history: List[Tuple[int, List[Job], List[Job], str]] = []
        self.pending_replan_reason: str = ""

    # ------------------------------------------------------------------ util
    def _log(self, kind: str, msg: str, asset: str = "", **detail):
        self.log.append(LogRecord(self.bucket, kind, msg, asset, dict(detail)))

    def demand_tph(self, dest_id: str, bucket: int) -> float:
        if (dest_id, bucket) in self.demand_override:
            return self.demand_override[(dest_id, bucket)]
        return self.sc.demand_tph(dest_id, bucket)

    def machine_ok(self, mid: str) -> bool:
        if self.bucket < self.machine_down_until.get(mid, -1):
            return False
        return self.sc.machine(mid).available and \
            not self.sc.machine_blocked(mid, self.bucket)

    def link_ok(self, lid: str) -> bool:
        if self.bucket < self.link_down_until.get(lid, -1):
            return False
        return self.sc.link(lid).available

    def rate_factor(self, mid: str) -> float:
        return self.machine_rate_factor.get(mid, 1.0)

    def comms_ok(self) -> bool:
        return self.bucket >= self.comms_down_until

    # -------------------------------------------------------------- events
    def inject(self, ev: SimEvent):
        self.events.append(ev)
        self.events.sort(key=lambda e: e.bucket)

    def _apply_events(self):
        for ev in [e for e in self.events if e.bucket == self.bucket]:
            t_end = self.bucket + max(ev.duration_buckets, 1)
            if ev.etype == "machine_failure":
                self.machine_down_until[ev.target] = t_end
                self.pending_replan_reason = f"machine_failure:{ev.target}"
            elif ev.etype in ("conveyor_trip", "route_unavailable"):
                self.link_down_until[ev.target] = t_end
                self.pending_replan_reason = f"{ev.etype}:{ev.target}"
            elif ev.etype == "demand_change":
                for t in range(self.bucket, min(t_end, self.T)):
                    self.demand_override[(ev.target, t)] = ev.magnitude
                self.pending_replan_reason = f"demand_change:{ev.target}"
            elif ev.etype == "delayed_mine_dispatch":
                for p in self.belts:
                    if p.link_id == ev.target and p.arrive_bucket >= self.bucket:
                        p.arrive_bucket += int(ev.magnitude)
                self.pending_replan_reason = f"delayed_mine_dispatch:{ev.target}"
            elif ev.etype == "offspec_assay":
                q = self.space_quality.setdefault(ev.target, {})
                for k, v in (ev.params or {}).items():
                    q[k] = v
                self.pending_replan_reason = f"offspec_assay:{ev.target}"
            elif ev.etype == "inventory_discrepancy":
                self.inv[ev.target] = max(self.inv.get(ev.target, 0.0)
                                          + ev.magnitude, 0.0)
                self.pending_replan_reason = f"inventory_discrepancy:{ev.target}"
            elif ev.etype == "operator_reject":
                j = self.jobs.get(ev.target)
                if j and j.status in ("PLANNED", "RUNNING", "DISPATCHED"):
                    j.status = "REJECTED"
                    self.banned.append((j.machine, j.space, j.dest_id,
                                        self.bucket, j.end_bucket))
                    self.pending_replan_reason = f"operator_reject:{ev.target}"
            elif ev.etype == "reduced_rate":
                self.machine_rate_factor[ev.target] = ev.magnitude
                self.pending_replan_reason = f"reduced_rate:{ev.target}"
            elif ev.etype == "downstream_interruption":
                for t in range(self.bucket, min(t_end, self.T)):
                    self.demand_override[(ev.target, t)] = 0.0
                self.pending_replan_reason = f"downstream_interruption:{ev.target}"
            elif ev.etype == "comms_loss":
                self.comms_down_until = t_end
            self._log("EVENT", f"{ev.etype} on {ev.target or '—'} "
                      f"(magnitude={ev.magnitude}, {ev.duration_buckets} buckets)",
                      ev.target, etype=ev.etype)

    # -------------------------------------------------------------- stepping
    def step(self) -> bool:
        """Advance one bucket. Returns False when the horizon is exhausted."""
        if self.bucket >= self.T:
            return False
        t = self.bucket
        self._apply_events()
        self.down_series.append(
            [m for m, du in self.machine_down_until.items() if t < du])
        self.link_down_series.append(
            [l for l, du in self.link_down_until.items() if t < du])

        # arrivals available this bucket, per (exit node, grade)
        arr: Dict[Tuple[str, str], BeltParcel] = {}
        for p in self.belts:
            if p.arrive_bucket == t and p.mass_t > 0:
                key = (self.sc.link(p.link_id).dst, p.grade)
                if key in arr:
                    tot = arr[key].mass_t + p.mass_t
                    for k in set(arr[key].quality) | set(p.quality):
                        arr[key].quality[k] = (arr[key].quality.get(k, 0) * arr[key].mass_t
                                               + p.quality.get(k, 0) * p.mass_t) / tot
                    arr[key].mass_t = tot
                else:
                    arr[key] = BeltParcel(p.link_id, t, p.mass_t, p.grade,
                                          dict(p.quality))

        deviation = False
        for j in sorted(self.jobs.values(), key=lambda x: x.id):
            if not (j.start_bucket <= t <= j.end_bucket) or \
                    j.status in ("CANCELLED", "REJECTED", "FINISHED"):
                continue
            if j.status == "PLANNED":
                j.status = "RUNNING"
                self._log("DISPATCH", f"job {j.id} started ({j.kind} {j.space}"
                          + (f" -> {j.dest_id}" if j.dest_id else "") + ")", j.machine)
            planned_t = j.rate_tph * self.dt
            moved = 0.0
            note = ""
            if not self.machine_ok(j.machine):
                note = "machine unavailable"
            elif any(not self.link_ok(li) for li in j.path):
                note = "route link unavailable"
            else:
                eff_rate = min(j.rate_tph * self.rate_factor(j.machine),
                               self.sc.machine(j.machine).rate_max_tph
                               * self.rate_factor(j.machine))
                cap_t = eff_rate * self.dt
                if j.kind in ("reclaim", "dispatch"):
                    moved = min(cap_t, self.inv.get(j.space, 0.0))
                    self.inv[j.space] = self.inv.get(j.space, 0.0) - moved
                    q = dict(self.space_quality.get(j.space, {}))
                    if j.kind == "reclaim":
                        self._deliver(j.dest_id, t, moved, q)
                    else:  # dispatch onto corridor
                        lid = j.path[-1] if j.path else ""
                        L = self.sc.link(lid).transit_buckets if lid else 0
                        self.belts.append(BeltParcel(lid, t + L, moved,
                                                     j.grade or self.sc.space(j.space).grade, q))
                elif j.kind == "stack":
                    exit_node = None
                    for li in j.path:
                        l = self.sc.link(li)
                        if l.transit_buckets > 0:
                            exit_node = l.dst
                    if exit_node is None and j.path:
                        exit_node = self.sc.link(j.path[0]).src
                    key = (exit_node, j.grade)
                    parcel = arr.get(key)
                    avail = parcel.mass_t if parcel else 0.0
                    free = self.sc.space(j.space).capacity_t - self.inv.get(j.space, 0.0)
                    moved = max(min(cap_t, avail, free), 0.0)
                    if parcel:
                        parcel.mass_t -= moved
                    if moved > 0:
                        old = self.inv.get(j.space, 0.0)
                        newq = {}
                        oq = self.space_quality.get(j.space, {})
                        pq = parcel.quality if parcel else {}
                        for k in set(oq) | set(pq):
                            newq[k] = (oq.get(k, 0) * old + pq.get(k, 0) * moved) \
                                / max(old + moved, 1e-9)
                        self.space_quality[j.space] = newq
                        self.inv[j.space] = old + moved
            for li in j.path:
                if li in self.link_load:
                    self.link_load[li][t] += moved / self.dt
            self.moved_by_job[j.id] = self.moved_by_job.get(j.id, 0.0) + moved
            self.activity_log.append({
                "bucket": t, "job": j.id, "machine": j.machine,
                "kind": j.kind, "space": j.space, "dest_id": j.dest_id,
                "grade": j.grade, "moved_t": round(moved, 1),
                "rate_tph": round(moved / self.dt, 1), "note": note})
            self.machine_active[j.machine][t] = 1

            # travel & starts
            prev = self._prev_space.get(j.machine)
            sp = self.sc.space(j.space)
            target_x = ((j.start_x + j.stop_x) / 2
                        if j.stop_x > j.start_x
                        else (sp.x_from + sp.x_to) / 2)
            # Same-pile jobs may be at different repères.  Move to the
            # current work-range midpoint instead of the generic pile centre.
            if prev != j.space or t == j.start_bucket:
                self.travel_m[j.machine] += abs(
                    self.machine_pos[j.machine] - target_x)
                self.machine_pos[j.machine] = target_x
                self.starts_stops[j.machine] += 1
                self._prev_space[j.machine] = j.space

            if t == j.end_bucket and j.status == "RUNNING":
                j.status = "FINISHED"
            if self.comms_ok():
                self.job_updates.append(JobUpdate(
                    job_id=j.id, bucket=t, status=j.status, moved_t=round(moved, 1),
                    effective_rate_tph=round(moved / self.dt, 1),
                    machine_position_x=self.machine_pos[j.machine],
                    quality=dict(self.space_quality.get(j.space, {})),
                    forecast_end_bucket=j.end_bucket, note=note))
                if moved < planned_t * (1 - self.sc.sim.deviation_tolerance):
                    deviation = True
                    self._log("DEVIATION", f"job {j.id}: moved {moved:.0f} t vs planned "
                              f"{planned_t:.0f} t ({note or 'shortfall'})", j.machine,
                              job=j.id, moved=moved, planned=planned_t)
            elif note or moved < planned_t * (1 - self.sc.sim.deviation_tolerance):
                self._log("INFO", f"comms loss: deviation on {j.id} NOT reported "
                          "(stale data)", j.machine)

        # exogenous flows (manual payloaders, unmodelled washing output, ...)
        for e in self.sc.exogenous:
            if e.start_bucket <= t <= e.end_bucket:
                amt = e.rate_tph * self.dt
                if e.source_space:
                    amt = min(amt, self.inv.get(e.source_space, 0.0))
                    self.inv[e.source_space] -= amt
                free = self.sc.space(e.target_space).capacity_t \
                    - self.inv.get(e.target_space, 0.0)
                amt = min(amt, max(free, 0.0))
                if amt > 0:
                    # incoming material updates pile quality by mass-weighted
                    # blending (defect fixed 18-Jul-2026: inflow previously
                    # added mass while silently keeping the old quality).
                    # A flow without an explicit vector uses its declared
                    # grade's vector — same convention as belt parcels; a
                    # source-space transfer carries the source pile quality.
                    if e.source_space:
                        fq = dict(self.space_quality.get(e.source_space, {}))
                    elif e.quality:
                        fq = dict(e.quality)
                    else:
                        g = self.sc.grade(e.grade)
                        fq = dict(g.quality) if g else {}
                    old = self.inv.get(e.target_space, 0.0)
                    oq = self.space_quality.get(e.target_space, {})
                    if fq:
                        newq = {}
                        for k in set(oq) | set(fq):
                            newq[k] = (oq.get(k, 0) * old + fq.get(k, 0) * amt) \
                                / max(old + amt, 1e-9)
                        self.space_quality[e.target_space] = newq
                    self.inv[e.target_space] = old + amt

        # unstacked arrivals left over -> lost feed alarm (belts cannot hold)
        for key, parcel in arr.items():
            if parcel.mass_t > 1.0:
                self._log("DEVIATION", f"{parcel.mass_t:.0f} t of {key[1]} arrived at "
                          f"{key[0]} and was NOT stacked (no stacker capacity)", key[0])
                deviation = True
        self.belts = [p for p in self.belts if not (p.arrive_bucket <= t)]

        for s in self.sc.spaces:
            self.inv_series[s.id].append(self.inv[s.id])
            self.quality_series[s.id].append(
                dict(self.space_quality.get(s.id, {})))

        self.bucket += 1
        if deviation and self.comms_ok() and self.auto_replan \
                and self.sc.sim.replan_on_deviation and self.bucket < self.T:
            self.replan(reason=self.pending_replan_reason or "execution deviation")
        elif self.pending_replan_reason and self.auto_replan and self.bucket < self.T:
            self.replan(reason=self.pending_replan_reason)
        self.pending_replan_reason = ""
        return self.bucket < self.T

    def run_to_end(self, max_steps: int = 10 ** 4):
        n = 0
        while self.step() and n < max_steps:
            n += 1

    # -------------------------------------------------------------- delivery
    def _deliver(self, dest_id: str, t: int, moved: float, quality: Dict[str, float]):
        if dest_id not in self.delivered:
            return
        prev = self.delivered[dest_id][t] * self.dt
        tot = prev + moved
        if tot > 0:
            dq = self.delivered_q[dest_id][t]
            for k in set(dq) | set(quality):
                dq[k] = (dq.get(k, 0) * prev + quality.get(k, 0) * moved) / tot
        self.delivered[dest_id][t] = tot / self.dt

    # -------------------------------------------------------------- replan
    def snapshot_scenario(self) -> Scenario:
        """Scenario reflecting live state, for a replan from the current bucket."""
        snap = copy.deepcopy(self.sc)
        for s in snap.spaces:
            s.mass_t = self.inv[s.id]
            s.quality = dict(self.space_quality.get(s.id, {}))
        from .domain import CalendarEntry
        for m in snap.machines:
            down_until = self.machine_down_until.get(m.id, -1)
            if self.bucket < down_until:
                # block only the remaining outage window — the machine returns
                snap.calendars.append(CalendarEntry(
                    m.id, self.bucket, down_until - 1, "failure outage"))
        for l in snap.links:
            # links have no calendar in the plan model: conservatively
            # unavailable for the remaining horizon while down (documented)
            if self.bucket < self.link_down_until.get(l.id, -1):
                l.available = False
        snap.belt_profile = [BeltProfileEntry(p.link_id, p.arrive_bucket, p.mass_t,
                                              p.grade, dict(p.quality))
                             for p in self.belts if p.arrive_bucket >= self.bucket]
        for (d, t), v in self.demand_override.items():
            snap.demand = [e for e in snap.demand
                           if not (e.dest_id == d and e.bucket == t)]
            from .domain import DemandEntry
            snap.demand.append(DemandEntry(d, t, v))
        snap.sim.horizon_buckets = self.T - self.bucket
        # frozen horizon + human/locked jobs become fixed jobs.
        # windows are clipped around calendar/outage blocks so a frozen job can
        # never contradict a machine outage (that would be INFEASIBLE by design).
        frozen_end = self.bucket + snap.sim.frozen_buckets - 1

        def first_free(mid: str, b0: int, b1: int):
            """First sub-window of [b0,b1] not blocked for machine mid, or None."""
            t = b0
            while t <= b1:
                blocked = any(c.scope in ("GLOBAL", mid) and
                              c.start_bucket <= t <= c.end_bucket
                              for c in snap.calendars)
                if not blocked:
                    t2 = t
                    while t2 + 1 <= b1 and not any(
                            c.scope in ("GLOBAL", mid) and
                            c.start_bucket <= t2 + 1 <= c.end_bucket
                            for c in snap.calendars):
                        t2 += 1
                    return t, t2
                t += 1
            return None

        snap.fixed_jobs = list(snap.fixed_jobs)
        for j in self.jobs.values():
            if j.status in ("CANCELLED", "REJECTED", "FINISHED"):
                continue
            keep = (j.origin == "HUMAN" or j.locked or
                    (j.start_bucket <= frozen_end and j.end_bucket >= self.bucket))
            if not keep:
                continue
            win = first_free(j.machine, max(j.start_bucket, self.bucket),
                             min(j.end_bucket, self.T - 1))
            if win is None:
                continue    # machine blocked for the whole window — cannot freeze
            r0, r1 = x_interval_to_reperes(
                snap.space(j.space), j.start_x, j.stop_x)
            snap.fixed_jobs.append(FixedJob(
                id=f"FROZEN-{j.id}", machine=j.machine, kind=j.kind,
                space=j.space, dest_id=j.dest_id, rate_tph=j.rate_tph,
                start_bucket=win[0], end_bucket=win[1],
                note="frozen-horizon / operator lock", grade=j.grade,
                start_repere=r0, stop_repere=r1))
        return snap

    def replan(self, reason: str = "deviation"):
        old_jobs = [copy.deepcopy(j) for j in self.jobs.values()]
        snap = self.snapshot_scenario()
        opts = SolveOptions(horizon_offset=self.bucket, banned=list(self.banned),
                            machine_rate_factor=dict(self.machine_rate_factor))
        new_plan = solve_plan(snap, opts)
        self.replans += 1
        self.replan_solve_s.append(new_plan.solver_wall_s)
        if new_plan.status in ("OPTIMAL", "FEASIBLE"):
            kept = {jid: j for jid, j in self.jobs.items()
                    if j.status in ("FINISHED", "CANCELLED", "REJECTED")
                    or j.end_bucket < self.bucket}
            # running frozen jobs stay under their old ids
            for jid, j in self.jobs.items():
                if jid in kept:
                    continue
                if j.origin == "HUMAN" or j.locked or j.status == "RUNNING":
                    trimmed = copy.deepcopy(j)
                    kept[jid] = trimmed
            for j in new_plan.jobs:
                dup = any(k.machine == j.machine and k.space == j.space
                          and k.dest_id == j.dest_id
                          and not (j.end_bucket < k.start_bucket
                                   or j.start_bucket > k.end_bucket)
                          and k.status in ("RUNNING", "PLANNED")
                          for k in kept.values())
                if not dup:
                    kept[j.id] = j
            self.jobs = kept
            self.plan = new_plan
            cancelled = len([j for j in old_jobs
                             if j.status in ("PLANNED",) and j.id not in kept])
            self._log("REPLAN", f"replan #{self.replans} ({reason}): "
                      f"{len(new_plan.jobs)} new jobs, {cancelled} cancelled/moved, "
                      f"solve {new_plan.solver_wall_s:.2f}s, status {new_plan.status}",
                      detail_reason=reason)
        else:
            self._log("REPLAN", f"replan #{self.replans} ({reason}) FAILED: "
                      f"{new_plan.status} — {'; '.join(new_plan.reasons)}",
                      detail_reason=reason)
        self.plan_history.append((self.bucket, old_jobs,
                                  [copy.deepcopy(j) for j in self.jobs.values()],
                                  reason))
