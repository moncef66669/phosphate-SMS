"""KPI calculation — layer 6. Pure functions over plan + simulation history."""
from __future__ import annotations

from typing import Dict, List, Optional
import math

from .continuity import (compute_continuity_metrics,
                         continuity_policy_errors,
                         parse_continuity_policies)
from .domain import PlanResult, Scenario
from .simulator import Simulator


def _spec_ok(q: Dict[str, float], specs) -> bool:
    for s in specs:
        v = q.get(s.param)
        if v is None or not math.isfinite(v):
            return False
        if s.pmin is not None and v < s.pmin - 1e-9:
            return False
        if s.pmax is not None and v > s.pmax + 1e-9:
            return False
    return True


def job_mass_in_bucket(job, bucket: int, dt_h: float) -> float:
    """Read exact physical flow; average rates are a legacy fallback only."""
    if job.bucket_flows:
        return sum(float(f.tonnage_t) for f in job.bucket_flows if f.bucket == bucket)
    if job.start_bucket <= bucket <= job.end_bucket:
        return float(job.rate_tph) * dt_h
    return 0.0


def planned_delivery_quality(sc, plan, dest, bucket, jobs=None):
    """Mass-accurate planned quality, including the selected construction recipe.

    An absent assay in any positive component makes that parameter unknown.
    Existing non-campaign piles still use the planner's aggregate snapshot;
    this is not a prediction of evolving spatial reclaim-face chemistry.
    """
    parts = []
    for j in (plan.jobs if jobs is None else jobs):
        serves = j.dest_id == dest.id and j.kind == "reclaim"
        if dest.demand_kind == "process_feed":
            serves = bool(j.source_space and j.process_stage == dest.process_stage)
            requested = sc.demand_grade(dest.id, bucket)
            serves = serves and (bool(j.material_flows) or not requested or requested == j.grade)
        if not serves:
            continue
        if j.material_flows:
            record = plan.blend_campaigns.get(j.source_space or j.space)
            for flow in j.material_flows:
                if flow["bucket"] != bucket or flow["tonnage_t"] <= 0:
                    continue
                if dest.demand_kind == "process_feed":
                    requested = sc.demand_grade(dest.id, bucket)
                    if requested and requested != flow["grade"]:
                        continue
                q = dict(record.get("resulting_quality", {})) if record else dict(flow["quality"])
                dry = 1 - float(q.get("moisture", 0)) / 100
                parts.append((float(flow["tonnage_t"]), dry, q))
            continue
        mass = job_mass_in_bucket(j, bucket, sc.sim.dt_h)
        if mass <= 0:
            continue
        sid = j.source_space or j.space
        record = plan.blend_campaigns.get(sid)
        if record:
            if bucket * sc.sim.dt_h * 60 < record.get("ready_minute", 0):
                q = {}
            else:
                q = dict(record.get("resulting_quality", {}))
        else:
            q = dict(sc.quality_of(sid))
        grade = sc.grade(j.grade or sc.space(sid).grade)
        moisture = q.get("moisture")
        if moisture is None and grade is not None:
            moisture = (grade.raw_moisture_pct if grade.material_class.startswith("raw")
                        else grade.product_moisture_pct)
        # Legacy assay vectors did not include moisture. Equal dry fractions
        # preserve their existing declared snapshot basis.
        dry = 1.0 - float(moisture or 0.0) / 100.0
        parts.append((mass, dry, q))
    if not parts:
        return {}
    params = set.intersection(*(set(q) for _, _, q in parts))
    result = {}
    for param in params:
        if any(not math.isfinite(q[param]) for _, _, q in parts):
            continue
        den = sum(m * (1 if param == "moisture" else dry) for m, dry, _ in parts)
        if den > 0:
            result[param] = sum(m * (1 if param == "moisture" else dry) * q[param]
                                for m, dry, q in parts) / den
    return result


def compute_kpis(sc: Scenario, plan: PlanResult,
                 sim: Optional[Simulator] = None) -> Dict[str, object]:
    """KPI dictionary. If sim is given, actuals are used; otherwise plan values.

    A partially-run simulation is evaluated over the EXECUTED buckets only
    (judging un-simulated future buckets as "undelivered" would be misleading).
    """
    dt = sc.sim.dt_h
    offset = max(int(plan.horizon_offset), 0) if sim is None else 0
    T = max(sc.sim.horizon_buckets - offset, 0)
    if sim is not None:
        elapsed_h = min(max(getattr(sim, "current_minute", 0) / 60,
                           sim.bucket * dt), sc.sim.horizon_buckets * dt)
        T = min(math.ceil(elapsed_h / dt - 1e-10), sc.sim.horizon_buckets)
        durations = [min(dt, max(elapsed_h - t * dt, 0)) for t in range(T)]
    else:
        lengths = [len(v) for v in plan.delivered.values()]
        if lengths:
            T = min(T, max(lengths))
        durations = [dt] * T
        elapsed_h = T * dt
    def rate(series, t):
        return float(series[t]) if 0 <= t < len(series) else 0.0
    k: Dict[str, object] = {}
    k["evaluated_buckets"] = T
    k["evaluated_hours"] = elapsed_h
    k["horizon_offset"] = offset
    for key, value in plan.breakdown.items():
        if key.startswith("washing_"):
            k[key] = value
    for mode in ("simple", "full"):
        if sim is not None:
            flows = [row for row in sim.actual_washing_flows if row["treatment_id"] == mode]
            feed = sum(row["tonnage_t"] * (1 - row["quality"]["moisture"] / 100) for row in flows)
            product = sum(row["output_tonnage_t"] * (1 - row["output_quality"]["moisture"] / 100) for row in flows)
            k.update({f"washing_{mode}_feed_t": sum(row["tonnage_t"] for row in flows),
                      f"washing_{mode}_product_t": sum(row["output_tonnage_t"] for row in flows),
                      f"washing_{mode}_feed_dry_t": feed,
                      f"washing_{mode}_product_dry_t": product,
                      f"washing_{mode}_loss_dry_t": feed - product})
        else:
            feed = plan.breakdown.get(f"washing_{mode}_feed_dry_t", 0)
            product = plan.breakdown.get(f"washing_{mode}_product_dry_t", 0)
        k[f"washing_{mode}_actual_dry_yield_pct"] = 100 * product / feed if feed else None
    if sim is not None:
        from . import washing
        cfg = washing.settings(sc)
        k["washing_raw_feed_dry_t"] = sum(k[f"washing_{mode}_feed_dry_t"] for mode in ("simple", "full"))
        k["washing_treatment_cost"] = sum(
            k[f"washing_{mode}_product_dry_t"]
            * float(cfg["modes"][mode]["cost_per_tsm_t"] or 0)
            for mode in ("simple", "full"))

    # ---- demand service ---------------------------------------------------
    per_dest = {}
    tot_dem = tot_del = 0.0
    interruptions = 0
    interruption_hours = 0.0
    external_dem = external_on_time = external_delivered = 0.0
    priority_dem = priority_served = 0.0
    delivered_mass_by_destination: Dict[str, List[float]] = {}
    for d in sc.destinations:
        dem = [sc.demand_tph(d.id, t + offset) * durations[t] for t in range(T)]
        if sim is not None:
            dem = [sim.demand_tph(d.id, t) * durations[t] for t in range(T)]
            dlv = [rate(sim.delivered[d.id], t) * dt for t in range(T)]
        else:
            dlv = [rate(plan.delivered.get(d.id, []), t) * dt for t in range(T)]
        # Planner and simulator delivery series are native-bucket average
        # rates.  Multiplying by the native dt recovers physical tonnes; for a
        # partially executed simulation bucket, that series already contains
        # only the mass moved so far.
        delivered_mass_by_destination[d.id] = dlv
        dsum, lsum = sum(dem), sum(dlv)
        if d.demand_kind == "process_feed":
            campaigns = {}
            for t in range(T):
                absolute = t + offset
                key = (sc.sim.bucket_dt(absolute).date(), sc.demand_grade(d.id, absolute),
                       sc.demand_priority(d.id, absolute))
                totals = campaigns.setdefault(key, [0.0, 0.0])
                totals[0] += dem[t]
                totals[1] += dlv[t]
            credited = sum(min(demand, delivered) for demand, delivered in campaigns.values())
        else:
            credited = sum(min(a, b) for a, b in zip(dem, dlv))
        per_dest[d.id] = {
            "demand_t": dsum, "delivered_t": lsum,
            "on_time_t": credited,
            "unmet_t": max(dsum - credited, 0.0),
            "service_pct": 100.0 * credited / dsum if dsum > 0 else 100.0,
            "service_basis": "daily grade campaign" if d.demand_kind == "process_feed" else "due bucket",
        }
        tot_dem += dsum
        tot_del += credited
        if d.demand_kind == "process_feed":
            priority_dem += sum(key[2] * values[0] for key, values in campaigns.items())
            priority_served += sum(key[2] * min(values) for key, values in campaigns.items())
        else:
            priority_dem += sum(sc.demand_priority(d.id, t + offset) * dem[t] for t in range(T))
            priority_served += sum(sc.demand_priority(d.id, t + offset) * min(dem[t], dlv[t]) for t in range(T))
        if d.demand_kind != "process_feed":
            external_dem += dsum
            external_on_time += credited
            external_delivered += lsum
        if getattr(d, "demand_kind", "delivery") == "process_feed":
            # Process-feed demand is a horizon total; the planner chooses the
            # operating buckets, so zero in a particular hour is not a feed
            # interruption.
            pass  # Campaign shortfall is captured by service, not hourly interruptions.
        else:
            interruptions += sum(
                1 for t in range(T) if dem[t] > 0 and dlv[t] <= 0)
            interruption_hours += sum(durations[t] for t in range(T)
                                      if dem[t] > 0 and dlv[t] <= 0)
    k["service_pct"] = 100.0 * tot_del / tot_dem if tot_dem > 0 else 100.0
    k["unmet_t"] = max(tot_dem - tot_del, 0.0)
    k["per_destination"] = per_dest
    k["feed_interruptions_buckets"] = interruptions
    k["feed_interruptions_h"] = interruption_hours
    # Explicit policy-scoped continuity accounting.  Keep the legacy keys
    # above untouched: historic Monitor/export consumers expect them to count
    # every non-process destination, including batch deliveries.  The new keys
    # score only destinations declared ``continuous_feed`` and distinguish
    # physical gap duration from interruption episode frequency.
    raw_continuity = sc.config.get("CONTINUITY_POLICIES", {})
    policy_errors = continuity_policy_errors(
        raw_continuity, (destination.id for destination in sc.destinations))
    policies = (
        {} if policy_errors else
        parse_continuity_policies(
            raw_continuity,
            (destination.id for destination in sc.destinations)))
    demand_profile_length = max((T if sim is not None else offset + T), 0)
    continuity_offset = 0 if sim is not None else offset
    continuity = compute_continuity_metrics(
        policies,
        {
            destination.id: [
                (sim.demand_tph(destination.id, bucket)
                 if sim is not None else
                 sc.demand_tph(destination.id, bucket))
                for bucket in range(demand_profile_length)
            ]
            for destination in sc.destinations
        },
        delivered_mass_by_destination,
        durations,
        offset=continuity_offset,
        initial_gap_by_destination=(
            None if sim is not None
            else getattr(plan, "initial_feed_gap", {})),
        initial_open_gap_minutes=(
            None if sim is not None
            else getattr(plan, "initial_open_feed_gap_minutes", {})),
    )
    k["continuity_policy_errors"] = policy_errors
    k["continuous_feed_policy_count"] = len(
        continuity["per_destination"])
    k["continuous_feed_gap_minutes"] = continuity["gap_minutes"]
    k["continuous_feed_gap_hours"] = continuity["gap_hours"]
    k["continuous_feed_interruption_count"] = continuity["episode_count"]
    k["continuous_feed_max_episode_minutes"] = \
        continuity["max_episode_minutes"]
    k["continuous_feed_terminal_gap"] = continuity["terminal_gap"]
    k["continuous_feed_terminal_gap_by_destination"] = \
        continuity["terminal_gap_by_destination"]
    k["continuous_feed_per_destination"] = continuity["per_destination"]
    k["priority_weighted_service_pct"] = 100 * priority_served / priority_dem if priority_dem else 100.0
    k["final_product_demand_t"] = external_dem
    k["final_product_on_time_t"] = external_on_time
    k["final_product_delivered_t"] = external_delivered
    k["final_product_unmet_t"] = external_dem - external_on_time
    k["final_product_service_pct"] = 100 * external_on_time / external_dem if external_dem else 100.0

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
    qb: Dict[tuple, Dict[str, object]] = {}   # (dest, requested grade, param)
    quality_mass = on_spec_mass = off_spec_mass = unknown_mass = 0.0
    final_on_spec_on_time = 0.0
    specified_demand = 0.0
    delivery_jobs = {
        d.id: [j for j in plan.jobs if
               (j.dest_id == d.id and j.kind == "reclaim") or
               (d.demand_kind == "process_feed" and j.source_space and
                j.process_stage == d.process_stage)] for d in sc.destinations}
    for d in sc.destinations:
        specs_by_t = [sc.demand_spec(d.id, t + offset) for t in range(T)]
        if not any(specs_by_t):
            continue
        if d.demand_kind != "process_feed":
            specified_demand += sum(
                sc.demand_tph(d.id, t + offset) * durations[t]
                for t in range(T) if specs_by_t[t])
        masses = [rate((sim.delivered if sim else plan.delivered).get(d.id, []), t) * dt for t in range(T)]
        qualities = [dict(sim.delivered_q[d.id][t]) if sim else
                     planned_delivery_quality(sc, plan, d, t + offset, delivery_jobs[d.id])
                     for t in range(T)]
        if sc.config.get("QUALITY_WINDOW_AGGREGATION_ENABLED", False):
            raw = [dict(q) for q in qualities]
            signature = lambda specs: tuple(
                (s.param, s.pmin, s.pmax, s.target, s.hard,
                 s.averaging_h, s.margin) for s in specs)
            first = 0
            while first < T:
                segment_signature = signature(specs_by_t[first])
                segment_end = first + 1
                while segment_end < T and \
                        signature(specs_by_t[segment_end]) == segment_signature:
                    segment_end += 1
                for spec in specs_by_t[first]:
                    width = max(int(round(spec.averaging_h / dt)), 1)
                    for window_start in range(first, segment_end, width):
                        window_end = min(window_start + width, segment_end)
                        active = [t for t in range(window_start, window_end)
                                  if masses[t] > 0]
                        known = active and all(
                            spec.param in raw[t] and
                            math.isfinite(raw[t][spec.param]) for t in active)
                        weights = {
                            t: masses[t] * (
                                1 if spec.param == "moisture" else
                                1 - raw[t].get("moisture", 0) / 100)
                            for t in active}
                        den = sum(weights.values())
                        for t in active:
                            qualities[t].pop(spec.param, None)
                            if known and den > 0:
                                qualities[t][spec.param] = sum(
                                    raw[b][spec.param] * weights[b]
                                    for b in active) / den
                first = segment_end
        for t in range(T):
            mass, q = masses[t], qualities[t]
            if mass <= 0:
                continue
            specs = specs_by_t[t]
            if not specs:
                continue
            requested_grade = sc.demand_grade(d.id, t + offset)
            grade = sc.grade(requested_grade)
            evidence = (grade.evidence if grade is not None and
                        getattr(grade, "is_specification", False)
                        else d.evidence)
            q_buckets += 1
            if _spec_ok(q, specs):
                onspec_buckets += 1
            if d.demand_kind != "process_feed":
                quality_mass += mass
                complete = all(
                    s.param in q and math.isfinite(q[s.param]) for s in specs)
                if not complete:
                    unknown_mass += mass
                elif _spec_ok(q, specs):
                    on_spec_mass += mass
                    demand = (sim.demand_tph(d.id, t) if sim is not None
                              else sc.demand_tph(d.id, t + offset)) * durations[t]
                    final_on_spec_on_time += min(mass, demand)
                else:
                    off_spec_mass += mass
            for s in specs:
                v = q.get(s.param)
                known = v is not None and math.isfinite(v)
                dev = 0.0
                if known and s.pmin is not None and v < s.pmin:
                    dev = s.pmin - v
                if known and s.pmax is not None and v > s.pmax:
                    dev = max(dev, v - s.pmax)
                qual_dev[s.param] = qual_dev.get(s.param, 0.0) + dev * mass
                if known and s.param == "BPL" and s.target is not None and v > s.target:
                    giveaway_t_unit += (v - s.target) * mass
                row = qb.setdefault((d.id, requested_grade, s.param), {
                    "destination": d.id,
                    "requested_grade": requested_grade,
                    "parameter": s.param,
                    "limit_min": s.pmin, "limit_max": s.pmax,
                    "hard": s.hard,
                    "evidence": evidence.status,
                    "source": evidence.source,
                    "evaluated": 0, "compliant": 0, "violations": 0,
                    "unknown": 0,
                    "sum_dev": 0.0, "max_dev": 0.0})
                row["evaluated"] += 1
                if not known:
                    row["unknown"] += 1
                elif dev > 1e-9:
                    row["violations"] += 1
                    row["sum_dev"] += dev
                    row["max_dev"] = max(row["max_dev"], dev)
                else:
                    row["compliant"] += 1
    k["onspec_pct"] = 100.0 * onspec_buckets / q_buckets if q_buckets else 100.0
    k["on_spec_mass_t"] = on_spec_mass
    k["off_spec_mass_t"] = off_spec_mass
    k["quality_unknown_t"] = unknown_mass
    k["quality_evaluated_t"] = quality_mass
    k["on_spec_mass_pct"] = 100 * on_spec_mass / quality_mass if quality_mass else None
    k["final_product_on_spec_on_time_t"] = final_on_spec_on_time
    k["quality_specified_demand_t"] = specified_demand
    k["quality_unspecified_demand_t"] = external_dem - specified_demand
    k["quality_basis"] = "actual delivered assays" if sim else "exact job flows and selected blend; other piles use aggregate planning snapshots"
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
        ser = ser[:T + 1]
        if sim is not None and len(ser) <= T:
            ser = ser + [float(sim.inv[s.id])]
        inv_end[s.id] = ser[-1]
        if s.safety_stock_t > 0:
            below = sum(1 for v in ser if v < s.safety_stock_t - 1e-6)
            viol += below
            min_margin[s.id] = min(v - s.safety_stock_t for v in ser)
    k["safety_stock_violation_buckets"] = viol
    k["safety_stock_min_margin_t"] = min_margin
    k["inventory_end_t"] = inv_end
    k["inventory_total_end_t"] = sum(inv_end.values())
    k["safety_shortfall_t_h"] = sum(
        max(s.safety_stock_t - v, 0) * durations[t] for s in sc.spaces
        for t, v in enumerate(series.get(s.id, [s.mass_t])[:T]))
    k["safety_stock_deficit_t_h"] = k["safety_shortfall_t_h"]
    k["capacity_excess_t_h"] = sum(
        max(v - s.capacity_t, 0) * durations[t] for s in sc.spaces
        for t, v in enumerate(series.get(s.id, [s.mass_t])[:T]))

    # ---- throughput / utilisation -------------------------------------------
    if sim is not None:
        moved_total = sum(sim.moved_by_job.values())
        util = {m.id: 100.0 * sum(sim.machine_active[m.id][:T]) / T if T else 0.0 for m in sc.machines}
        route_util = {}
        for l in sc.links:
            loads = sim.link_load[l.id]
            route_util[l.id] = 100.0 * sum(loads[:T]) * dt / elapsed_h / l.capacity_tph \
                if l.capacity_tph and elapsed_h else 0.0
        k["travel_m"] = dict(sim.travel_m)
        k["starts_stops"] = dict(sim.starts_stops)
        k["changeovers"] = sum(sim.starts_stops.values())
        k["replans"] = sim.replans
        k["replan_solve_s"] = list(sim.replan_solve_s)
    else:
        moved_total = sum(j.tonnage_t for j in plan.jobs)
        busy = {}
        route_load = {l.id: [0.0] * T for l in sc.links}
        for j in plan.jobs:
            buckets = {f.bucket for f in j.bucket_flows if f.tonnage_t > 0} if j.bucket_flows else set(range(j.start_bucket, j.end_bucket + 1))
            busy.setdefault(j.machine, set()).update(t for t in buckets if offset <= t < T + offset)
            for absolute_t in buckets:
                t = absolute_t - offset
                if not 0 <= t < T:
                    continue
                flows = [(f.path, f.tonnage_t) for f in j.bucket_flows if f.bucket == absolute_t] if j.bucket_flows else [(j.path, job_mass_in_bucket(j, absolute_t, dt))]
                for path, tonnes in flows:
                    for lid in set(path):
                        if lid in route_load:
                            route_load[lid][t] += tonnes / dt
        util = {m.id: 100.0 * len(busy.get(m.id, set())) / T if T else 0.0 for m in sc.machines}
        route_util = {l.id: 100 * sum(route_load[l.id]) / (T * l.capacity_tph) if T and l.capacity_tph else 0 for l in sc.links}
        k["route_peak_utilisation_pct"] = {l.id: 100 * max(route_load[l.id], default=0) / l.capacity_tph if l.capacity_tph else 0 for l in sc.links}
        k["travel_m"] = {}
        k["starts_stops"] = {}
        k["changeovers"] = plan.breakdown.get("moves", 0.0)
        k["replans"] = 0
        k["replan_solve_s"] = []
    k["throughput_t"] = moved_total
    k["handled_t"] = moved_total
    k["handling_t_per_delivered_t"] = moved_total / external_delivered if external_delivered else None
    k["unstacked_t"] = sum(plan.surge_unstacked[:T]) if sim is None else float(getattr(sim, "unstacked_t", 0.0))
    k["minute_timing_errors"] = plan.breakdown.get("minute_timing_errors", 0.0)
    k["machine_utilisation_pct"] = util
    k["route_utilisation_pct"] = route_util
    if sim is not None:
        k["route_peak_utilisation_pct"] = {
            l.id: 100 * max(sim.link_load[l.id][:T], default=0) / l.capacity_tph
            if l.capacity_tph else 0.0 for l in sc.links}
    k["travel_total_m"] = sum(k["travel_m"].values())
    k["solver_wall_s"] = plan.solver_wall_s
    k["plan_status"] = plan.status
    # A solver status is NOT a service statement.  FEASIBLE means "a plan
    # satisfying every hard constraint was found", and the S0 week is routinely
    # FEASIBLE with several hundred thousand tonnes of demand unserved.  Publish
    # the combined statement next to the status so no view can imply otherwise.
    k["demand_fully_served"] = bool(k["unmet_t"] <= 1e-6)
    k["plan_status_statement"] = (
        f"{plan.status} · "
        + ("all requested demand served"
           if k["demand_fully_served"] else
           f"{k['unmet_t']:,.0f} t of {tot_dem:,.0f} t requested demand "
           f"NOT served ({k['service_pct']:.1f}% service)"))

    # ---- schedule fragmentation & executed machine transitions --------------
    # Two different questions, reported side by side because their answers
    # differ whenever the minute schedule breaks an hourly activity run into
    # separate material bursts:
    #   scheduled_* — how many planning BUCKETS changed state (CP-SAT decision)
    #   executed_*  — how many times the machine actually starts, stops, changes
    #                 over and stands HELD in the placed MINUTE schedule
    fragmentation_keys = (
        "scheduled_starts", "scheduled_stops",
        "scheduled_group_changeovers", "machine_state_transition_count",
        "executed_starts", "executed_stops", "executed_changeovers",
        "executed_state_transition_count", "executed_physical_runs",
        "executed_material_segments", "executed_active_minutes",
        "executed_hold_minutes", "executed_hold_intervals",
        "executed_changeover_minutes", "executed_idle_minutes",
        "executed_sub_15_min_segments", "executed_sub_30_min_segments",
        "physical_runs", "physical_runs_spanning_planning_days",
        "parent_jobs", "allocation_slices", "below_parcel_floor_slices",
        "below_parcel_floor_t", "smallest_allocation_slice_t",
        "short_parcel_buckets", "minute_timing_stable_run_windows",
        "minute_timing_stable_run_minutes", "campaign_policy_enabled",
        # Held state and the hourly/executed reconciliation. The residual is
        # an acceptance figure: it must be zero, otherwise the two layers
        # disagree about the schedule rather than about how to name a gap.
        "scheduled_hold_buckets", "held_machine_h",
        "hourly_starts", "hourly_stops", "hourly_changeovers",
        "hourly_transition_count", "hourly_hold_buckets",
        "executed_changeover_reclassified_as_stop_start",
        "transition_hourly_total", "transition_executed_total",
        "transition_reclassified_changeovers",
        "transition_reconciliation_residual",
        "transition_window_boundary_delta",
    )
    k["schedule_fragmentation"] = {
        key: plan.breakdown[key]
        for key in fragmentation_keys if key in plan.breakdown}
    k["continuous_feed_token_delivery_buckets"] = continuity.get(
        "token_delivery_buckets", 0)
    k["continuous_feed_token_delivery_t"] = continuity.get(
        "token_delivery_t", 0.0)

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
    # A failed replan silently retains the previous plan, which inflates
    # plan_stability_pct to 100% for the worst possible reason: the plan never
    # changed because it could not be recomputed. Report the attempts and the
    # failures together so the stability figure is never read on its own.
    k["replan_attempts"] = int(getattr(sim, "replans", 0) or 0) if sim else 0
    k["replan_failures"] = int(
        getattr(sim, "replan_failures", 0) or 0) if sim else 0
    k["plan_is_current"] = bool(
        k["replan_attempts"] == 0 or k["replan_failures"] == 0)
    #: Replans that only succeeded after the frozen near-term jobs were
    #: dropped. The plan is current, but the commitment behind it was not
    #: kept, so a non-zero count is a warning and not a failure.
    k["replan_freeze_relaxed"] = int(
        getattr(sim, "replan_freeze_relaxed", 0) or 0) if sim else 0
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
