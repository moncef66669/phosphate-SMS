"""KPI calculation — layer 6. Pure functions over plan + simulation history."""
from __future__ import annotations

from typing import Dict, List, Optional

from .domain import PlanResult, Scenario
from .simulator import Simulator


def _spec_ok(q: Dict[str, float], specs) -> bool:
    for s in specs:
        v = q.get(s.param)
        if v is None:
            continue
        if s.pmin is not None and v < s.pmin - 1e-9:
            return False
        if s.pmax is not None and v > s.pmax + 1e-9:
            return False
    return True


def compute_kpis(sc: Scenario, plan: PlanResult,
                 sim: Optional[Simulator] = None) -> Dict[str, object]:
    """KPI dictionary. If sim is given, actuals are used; otherwise plan values.

    A partially-run simulation is evaluated over the EXECUTED buckets only
    (judging un-simulated future buckets as "undelivered" would be misleading).
    """
    T = sc.sim.horizon_buckets
    if sim is not None and 0 < sim.bucket < T:
        T = sim.bucket
    dt = sc.sim.dt_h
    k: Dict[str, object] = {}
    k["evaluated_buckets"] = T

    # ---- demand service ---------------------------------------------------
    per_dest = {}
    tot_dem = tot_del = 0.0
    interruptions = 0
    for d in sc.destinations:
        dem = [sc.demand_tph(d.id, t) * dt for t in range(T)]
        if sim is not None:
            dem = [sim.demand_tph(d.id, t) * dt for t in range(T)]
            dlv = [sim.delivered[d.id][t] * dt for t in range(T)]
        else:
            dlv = [plan.delivered.get(d.id, [0.0] * T)[t] * dt for t in range(T)]
        dsum, lsum = sum(dem), sum(dlv)
        per_dest[d.id] = {
            "demand_t": dsum, "delivered_t": lsum,
            "unmet_t": max(dsum - lsum, 0.0),
            "service_pct": 100.0 * min(lsum / dsum, 1.0) if dsum > 0 else 100.0,
        }
        tot_dem += dsum
        tot_del += min(lsum, dsum)
        if getattr(d, "demand_kind", "delivery") == "process_feed":
            # Process-feed demand is a horizon total; the planner chooses the
            # operating buckets, so zero in a particular hour is not a feed
            # interruption.
            interruptions += int(dsum > 0 and lsum <= 0)
        else:
            interruptions += sum(
                1 for t in range(T) if dem[t] > 0 and dlv[t] <= 0)
    k["service_pct"] = 100.0 * tot_del / tot_dem if tot_dem > 0 else 100.0
    k["unmet_t"] = max(tot_dem - tot_del, 0.0)
    k["per_destination"] = per_dest
    k["feed_interruptions_buckets"] = interruptions

    # ---- quality ----------------------------------------------------------
    # On-spec definition: share of (destination x bucket) pairs WITH DELIVERED
    # MATERIAL whose mass-weighted blend quality lies inside every configured
    # limit. Buckets with zero delivery are NOT evaluated (they are feed gaps,
    # captured by the demand-service KPI, not off-spec quality).
    # Soft limits are penalised in the objective (w2) but never block the
    # plan; hard limits are enforced by the optimiser, so a hard violation in
    # actuals indicates an execution deviation.
    onspec_buckets = 0
    q_buckets = 0
    qual_dev: Dict[str, float] = {}
    giveaway_t_unit = 0.0
    qb: Dict[tuple, Dict[str, object]] = {}   # (dest, param) -> breakdown row
    for d in sc.destinations:
        if not d.spec:
            continue
        for t in range(T):
            if sim is not None:
                mass = sim.delivered[d.id][t] * dt
                q = sim.delivered_q[d.id][t]
            else:
                mass = plan.delivered.get(d.id, [0.0] * T)[t] * dt
                q = {}
                if mass > 0:
                    # reconstruct planned blend quality from jobs
                    tot = 0.0
                    for j in plan.jobs:
                        if j.kind == "reclaim" and j.dest_id == d.id and \
                                j.start_bucket <= t <= j.end_bucket:
                            sq = sc.quality_of(j.space)
                            w = j.rate_tph * dt
                            for p_, v in sq.items():
                                q[p_] = q.get(p_, 0.0) + v * w
                            tot += w
                    q = {p_: v / tot for p_, v in q.items()} if tot else {}
            if mass <= 0:
                continue
            q_buckets += 1
            if _spec_ok(q, d.spec):
                onspec_buckets += 1
            for s in d.spec:
                v = q.get(s.param)
                if v is None:
                    continue
                dev = 0.0
                if s.pmin is not None and v < s.pmin:
                    dev = s.pmin - v
                if s.pmax is not None and v > s.pmax:
                    dev = max(dev, v - s.pmax)
                qual_dev[s.param] = qual_dev.get(s.param, 0.0) + dev * mass
                if s.target is not None and v > s.target:
                    giveaway_t_unit += (v - s.target) * mass
                row = qb.setdefault((d.id, s.param), {
                    "destination": d.id, "parameter": s.param,
                    "limit_min": s.pmin, "limit_max": s.pmax,
                    "hard": s.hard,
                    "evidence": d.evidence.status,
                    "source": d.evidence.source,
                    "evaluated": 0, "compliant": 0, "violations": 0,
                    "sum_dev": 0.0, "max_dev": 0.0})
                row["evaluated"] += 1
                if dev > 1e-9:
                    row["violations"] += 1
                    row["sum_dev"] += dev
                    row["max_dev"] = max(row["max_dev"], dev)
                else:
                    row["compliant"] += 1
    k["onspec_pct"] = 100.0 * onspec_buckets / q_buckets if q_buckets else 100.0
    k["quality_deviation_t_unit"] = qual_dev
    k["giveaway_t_unit"] = giveaway_t_unit
    breakdown = []
    for row in qb.values():
        row["compliance_pct"] = 100.0 * row["compliant"] / row["evaluated"]             if row["evaluated"] else 100.0
        row["avg_dev"] = row["sum_dev"] / row["violations"]             if row["violations"] else 0.0
        breakdown.append(row)
    breakdown.sort(key=lambda r: (-r["violations"], -r["sum_dev"]))
    k["quality_breakdown"] = breakdown
    # principal off-spec driver: most violating (destination, parameter) pair
    k["onspec_principal"] = (
        {kk: breakdown[0][kk] for kk in ("destination", "parameter", "hard",
                                         "violations", "evidence")}
        if breakdown and breakdown[0]["violations"] > 0 else None)

    # ---- inventory & safety stock ------------------------------------------
    viol = 0
    min_margin = {}
    inv_end = {}
    series = sim.inv_series if sim is not None else plan.inventory
    for s in sc.spaces:
        ser = series.get(s.id, [s.mass_t])
        inv_end[s.id] = ser[-1]
        if s.safety_stock_t > 0:
            below = sum(1 for v in ser if v < s.safety_stock_t - 1e-6)
            viol += below
            min_margin[s.id] = min(v - s.safety_stock_t for v in ser)
    k["safety_stock_violation_buckets"] = viol
    k["safety_stock_min_margin_t"] = min_margin
    k["inventory_end_t"] = inv_end
    k["inventory_total_end_t"] = sum(inv_end.values())

    # ---- throughput / utilisation -------------------------------------------
    if sim is not None:
        moved_total = sum(sim.moved_by_job.values())
        util = {m.id: 100.0 * sum(sim.machine_active[m.id]) / T for m in sc.machines}
        route_util = {}
        for l in sc.links:
            loads = sim.link_load[l.id]
            route_util[l.id] = 100.0 * (sum(loads) / T) / l.capacity_tph \
                if l.capacity_tph else 0.0
        k["travel_m"] = dict(sim.travel_m)
        k["starts_stops"] = dict(sim.starts_stops)
        k["changeovers"] = sum(sim.starts_stops.values())
        k["replans"] = sim.replans
        k["replan_solve_s"] = list(sim.replan_solve_s)
    else:
        moved_total = sum(j.tonnage_t for j in plan.jobs)
        busy: Dict[str, int] = {}
        for j in plan.jobs:
            busy[j.machine] = busy.get(j.machine, 0) + (j.end_bucket - j.start_bucket + 1)
        util = {m.id: 100.0 * busy.get(m.id, 0) / T for m in sc.machines}
        route_util = {}
        k["travel_m"] = {}
        k["starts_stops"] = {}
        k["changeovers"] = plan.breakdown.get("moves", 0.0)
        k["replans"] = 0
        k["replan_solve_s"] = []
    k["throughput_t"] = moved_total
    k["machine_utilisation_pct"] = util
    k["route_utilisation_pct"] = route_util
    k["solver_wall_s"] = plan.solver_wall_s
    k["plan_status"] = plan.status

    # ---- plan stability & planned-vs-actual ---------------------------------
    if sim is not None and sim.plan_history:
        # fraction of the ORIGINAL plan that survived (executed or still active)
        first = {j.id for j in sim.plan_history[0][1]}
        survived = {j.id for j in sim.jobs.values()
                    if j.status not in ("CANCELLED", "REJECTED")}
        k["plan_stability_pct"] = 100.0 * len(first & survived) / len(first) \
            if first else 100.0
    else:
        k["plan_stability_pct"] = 100.0
    pva = []
    if sim is not None:
        for j in sim.jobs.values():
            moved = sim.moved_by_job.get(j.id, 0.0)
            if j.status in ("FINISHED", "RUNNING", "CANCELLED", "REJECTED") \
                    or moved > 0:
                pva.append({"job": j.id, "machine": j.machine, "kind": j.kind,
                            "planned_t": j.tonnage_t, "actual_t": round(moved, 1),
                            "deviation_t": round(moved - j.tonnage_t, 1),
                            "status": j.status})
    k["planned_vs_actual"] = pva
    return k
