"""Controlled three-SMS information-sharing experiment.

This is an explicit synthetic reduction of the project, not the Rev-AC plant
dataset or an estimate of measured operator performance. Two mine SMS receive
identical fixed volume nominations. Their deterministic dispatchers see local
stock, rate, calendar and their own nomination; they cannot see M16's receiving
outage. M16 sees its local demand, piles and current corridor arrivals. A common
physical replay audits both these independently authored jobs and CP-SAT jobs.

The experiment distinguishes an avoidable communication/coordination failure
from 'human intelligence'. A paced local sensitivity and a no-outage control
prevent an intentionally weak, front-loaded rule being the only comparator.
"""
from __future__ import annotations

import copy
import math
from collections import defaultdict
from typing import Callable, Dict, List, Optional

from .benchmark import (BenchmarkArm, BenchmarkOptions, BenchmarkResult,
                        SUCCESS_STATUSES, benchmark_provenance, finish_comparison,
                        scenario_fingerprint)
from .domain import (Assumption, CalendarEntry, DemandEntry, Destination, Grade,
                     Job, JobBucketFlow, Link, Machine, Node, PlanResult,
                     Scenario, SimParams, Site, SpecRange, StockSpace)
from .kpis import compute_kpis
from .optimizer import SolveOptions, solve_plan


DEMO_CONFIG = "COORDINATION_BENCHMARK"
LOCAL_PROTOCOLS = ("local_front_loaded", "local_paced", "local_staggered")


def make_coordination_scenario(receiver_outage: bool = True,
                               receiving_capacity_tph: float = 100.0) -> Scenario:
    """Twelve-hour controlled case with equal, fixed C1/C2 nominations.

    Every number, including grade assays and scaled capacities, is an explicit
    demonstration assumption. It is deliberately small enough to audit by hand.
    ``receiver_outage=False`` is the mandatory useful control: good local pacing
    should match coordinated service when the nominal interface is sufficient.
    """
    if not math.isfinite(receiving_capacity_tph) or receiving_capacity_tph <= 0:
        raise ValueError("Receiving capacity must be finite and positive")
    sc = Scenario(
        name="Controlled three-SMS coordination experiment",
        description="Synthetic 12-hour coordination experiment; 600 t final demand, "
                    "100 t initial hub buffer, 250 t nominations per mine.",
        sim=SimParams(dt_h=1.0, horizon_buckets=12, max_solve_s=3.0,
                      start_iso="2026-09-08T00:00:00"))
    sc.sites = [Site("LOUTA", "Louta (scaled experiment)", "mine"),
                Site("BENGUERIR", "Benguerir (scaled experiment)", "mine"),
                Site("MPH", "M16 (scaled experiment)", "hub",
                     same_pile_concurrency_allowed=True,
                     min_machine_separation_m=0.0, work_zone_buffer_m=0.0)]
    sc.grades = [Grade("DEMO-LOW", "Illustrative 68 BPL", {"BPL": 68.0},
                       material_class="washed"),
                 Grade("DEMO-HIGH", "Illustrative 74 BPL", {"BPL": 74.0},
                       material_class="washed")]
    for suffix, site, grade, corridor in (
            ("L", "LOUTA", "DEMO-LOW", "C1"),
            ("B", "BENGUERIR", "DEMO-HIGH", "C2")):
        mine, hub = f"DEMO-MINE-{suffix}", f"DEMO-HUB-{suffix}"
        sc.spaces.extend([
            StockSpace(mine, site, "mine-yard", "open", 1000, 600, grade,
                       designated_grade=grade, x_from=0, x_to=100,
                       node_id=f"SPACE:{mine}", material_class="washed"),
            StockSpace(hub, "MPH", "M16-humid", "closed", 1000, 50, grade,
                       designated_grade=grade, x_from=0, x_to=100,
                       node_id=f"SPACE:{hub}", material_class="washed")])
        sc.machines.extend([
            Machine(f"DEMO-{suffix}-RC", site, "reclaimer", 0, 100, [mine],
                    setup_min=0, position_x=50),
            Machine(f"DEMO-{suffix}-ST", "MPH", "stacker", 0, 100, [hub],
                    setup_min=0, position_x=50),
            Machine(f"DEMO-HUB-{suffix}-RC", "MPH", "reclaimer", 0, 100, [hub],
                    setup_min=0, position_x=50)])
        sc.nodes.extend([Node(f"SPACE:{mine}", site, "space"),
                         Node(f"DEMO-OUT-{suffix}", site, "tower"),
                         Node(f"SPACE:{hub}", "MPH", "space")])
        sc.links.extend([
            Link(f"DEMO-MINE-{suffix}-OUT", f"SPACE:{mine}",
                 f"DEMO-OUT-{suffix}", 100),
            Link(corridor, f"DEMO-OUT-{suffix}", "DEMO-ARRIVAL", 100,
                 transit_buckets=1),
            Link(f"DEMO-STACK-{suffix}", "DEMO-RECEIVING", f"SPACE:{hub}", 100),
            Link(f"DEMO-RECLAIM-{suffix}", f"SPACE:{hub}", "DEMO-DEST", 100)])
    sc.nodes.extend([Node("DEMO-ARRIVAL", "MPH", "tower"),
                     Node("DEMO-RECEIVING", "MPH", "tower"),
                     Node("DEMO-DEST", "MPH", "dest")])
    sc.links.append(Link("DEMO-SHARED-IN", "DEMO-ARRIVAL", "DEMO-RECEIVING",
                         receiving_capacity_tph))
    sc.destinations = [Destination(
        "DEMO-PRODUCT", "MPH", "DEMO-DEST", priority=1,
        spec=[SpecRange("BPL", 71.0, 71.0, 71.0, hard=True)])]
    sc.demand = [DemandEntry("DEMO-PRODUCT", t, 100.0 if 5 <= t <= 10 else 0.0)
                 for t in range(12)]
    if receiver_outage:
        sc.calendars.append(CalendarEntry(
            "LINK:DEMO-SHARED-IN", 2, 3,
            "Assumed receiving maintenance known to M16 SMS"))
    sc.config["CORRIDOR_DISPATCH_TARGET_T"] = {"C1": 250.0, "C2": 250.0}
    sc.config[DEMO_CONFIG] = {
        "version": 1, "receiver_outage": receiver_outage,
        "nomination_per_mine_t": 250.0, "local_release_window_buckets": 5,
        "nominal_release_rate_tph": 100.0,
        "receiving_holding_capacity_t": 500.0,
        "basis": "SYNTHETIC ASSUMPTIONS; no actual OCP performance or cost data"}
    sc.assumptions.append(Assumption(
        "BC-DEMO-01", "All capacities, stocks, assays, nominations and outage "
        "times in this reduced coordination experiment are illustrative.",
        "Business case", source="Controlled experimental design", confidence="L",
        impact="Demonstrates a coordination mechanism; not a site benefit forecast."))
    return sc


def _mine_release(local_stock_t: float, nomination_remaining_t: float,
                  local_capacity_t: float, bucket: int, protocol: str,
                  release_window: int, slot: int) -> float:
    """Single-SMS policy: no scenario, remote calendar or remote inventory input."""
    if protocol == "local_paced":
        # Evenly pace each known volume nomination across its release window.
        rate = nomination_remaining_t / max(release_window - bucket, 1)
    elif protocol == "local_staggered":
        # Prior agreed alternating C1/C2 slots; no remote live information.
        rate = local_capacity_t if bucket % 2 == slot else 0.0
    elif protocol == "local_front_loaded":
        rate = local_capacity_t
    else:
        raise ValueError(f"Unknown independent operator protocol: {protocol}")
    return max(min(local_stock_t, nomination_remaining_t, local_capacity_t, rate), 0.0)


def _job(suffix: str, kind: str, t: int, amount: float, *, source: str,
         machine: str, grade: str, path: list, dest: str = "") -> Job:
    return Job(
        id=f"LOCAL-{kind}-{suffix}-{t:02d}", machine=machine, kind=kind,
        space=source, dest_id=dest, grade=grade, rate_tph=amount,
        tonnage_t=amount, start_bucket=t, end_bucket=t, path=list(path),
        origin="HUMAN", start_x=50.0, stop_x=50.0,
        execution_rate_cap_tph=100.0,
        bucket_flows=[JobBucketFlow(t, amount, list(path))],
        comment="Deterministic independent-SMS experimental policy; not measured operator data")


def independent_sms_plan(sc: Scenario, protocol: str = "local_paced") -> PlanResult:
    """Author jobs without calling any optimization engine.

    Upstream dispatches have no access to the hub's calendar or stock. The hub
    fairly shares its available receiver between current parcels and uses its
    own start-of-bucket stock for a known, on-spec 50/50 product blend. Arrivals
    exceeding the live receiving interface remain in a finite assumed buffer.
    """
    config = sc.config.get(DEMO_CONFIG)
    if not isinstance(config, dict) or config.get("version") != 1:
        raise ValueError("Independent dispatcher supports only the controlled coordination scenario")
    if protocol not in LOCAL_PROTOCOLS:
        raise ValueError(f"Unknown independent operator protocol: {protocol}")
    inv = {s.id: s.mass_t for s in sc.spaces}
    remaining = {"C1": float(config["nomination_per_mine_t"]),
                 "C2": float(config["nomination_per_mine_t"])}
    arrivals = defaultdict(lambda: defaultdict(float))
    holding = defaultdict(float)
    jobs = []
    for t in range(sc.sim.horizon_buckets):
        start_inv = dict(inv)
        # Independent mine SMS: the helper receives a deliberately narrow
        # observation contract. All receiving knowledge stays below this loop.
        for slot, (suffix, corridor) in enumerate((("L", "C1"), ("B", "C2"))):
            mine = f"DEMO-MINE-{suffix}"
            machine = f"DEMO-{suffix}-RC"
            scopes = {machine, f"SITE:{sc.machine(machine).site}",
                      f"LINK:DEMO-MINE-{suffix}-OUT", f"LINK:{corridor}"}
            capacity = sc.effective_resource_rate_tph(scopes, 100, bucket=t)
            amount = _mine_release(
                inv[mine], remaining[corridor], capacity, t, protocol,
                int(config["local_release_window_buckets"]), slot)
            if amount <= 1e-9:
                continue
            inv[mine] -= amount
            remaining[corridor] -= amount
            grade = sc.space(mine).grade
            lag = sc.link(corridor).transit_buckets
            arrivals[t + lag][suffix] += amount
            jobs.append(_job(suffix, "dispatch", t, amount, source=mine,
                             machine=machine, grade=grade, dest=corridor,
                             path=[f"DEMO-MINE-{suffix}-OUT", corridor]))
        # M16's receiving operator sees local calendar and physically arriving
        # parcels, not mine inventories or future release decisions.
        for suffix, mass in arrivals[t].items():
            holding[suffix] += mass
        arriving = defaultdict(float, holding)
        receiving = sc.effective_resource_rate_tph(
            {"LINK:DEMO-SHARED-IN", "SITE:MPH"},
            sc.link("DEMO-SHARED-IN").capacity_tph, bucket=t)
        total = sum(arriving.values())
        for suffix in ("L", "B"):
            hub = f"DEMO-HUB-{suffix}"
            machine = f"DEMO-{suffix}-ST"
            scopes = {machine, "SITE:MPH", f"LINK:DEMO-STACK-{suffix}"}
            local_cap = sc.effective_resource_rate_tph(scopes, 100, bucket=t)
            fair_share = receiving * arriving[suffix] / total if total > 0 else 0
            amount = min(arriving[suffix], fair_share, local_cap,
                         sc.space(hub).capacity_t - start_inv[hub])
            if amount > 1e-9:
                holding[suffix] -= amount
                inv[hub] += amount
                jobs.append(_job(suffix, "stack", t, amount, source=hub,
                                 machine=machine, grade=sc.space(hub).grade,
                                 path=["DEMO-SHARED-IN", f"DEMO-STACK-{suffix}"]))
        # The receiving specification is known locally, and both reclaimers
        # can contribute. No deliberately poor or off-spec choice is made.
        required_half = sc.demand_tph("DEMO-PRODUCT", t) / 2.0
        half = min(required_half, start_inv["DEMO-HUB-L"], start_inv["DEMO-HUB-B"])
        for suffix in ("L", "B"):
            half = min(half, sc.effective_resource_rate_tph(
                {f"DEMO-HUB-{suffix}-RC", "SITE:MPH", f"LINK:DEMO-RECLAIM-{suffix}"},
                100, bucket=t))
        for suffix in ("L", "B"):
            if half <= 1e-9:
                continue
            hub = f"DEMO-HUB-{suffix}"
            inv[hub] -= half
            jobs.append(_job(suffix, "reclaim", t, half, source=hub,
                             machine=f"DEMO-HUB-{suffix}-RC", grade=sc.space(hub).grade,
                             path=[f"DEMO-RECLAIM-{suffix}"], dest="DEMO-PRODUCT"))
    return PlanResult(status="FEASIBLE", jobs=jobs, solver=f"independent-sms-{protocol}",
                      reasons=["Jobs authored by deterministic site-local rules without a solver"])


def replay_coordination_plan(sc: Scenario, plan: PlanResult) -> tuple:
    """Common, independent bucket replay/audit for *both* experiment arms.

    Checks exact mass, inventory, machine/path/link capacities and calendars,
    finite corridor lag, local route topology, no same-bucket pass-through,
    grade segregation and hard delivered quality. Reconstructed delivery and
    held-arrival mass drive KPIs, never one arm's claimed objective or totals.
    This reducer intentionally rejects process/construction/zone scenarios.
    """
    if not sc.config.get(DEMO_CONFIG) or sc.process_transfers or sc.pile_zones:
        raise ValueError("Common replay is limited to the controlled coordination experiment")
    result = copy.deepcopy(plan)
    T, dt = sc.sim.horizon_buckets, sc.sim.dt_h
    if dt != 1.0:
        raise ValueError("Controlled replay requires one-hour native buckets")
    errors, rows = [], defaultdict(list)
    for job in plan.jobs:
        if not job.bucket_flows:
            errors.append(f"{job.id}: exact bucket flows are required")
            continue
        if abs(sum(f.tonnage_t for f in job.bucket_flows) - job.tonnage_t) > 1e-5:
            errors.append(f"{job.id}: exact flow mass differs from job tonnes")
        for flow in job.bucket_flows:
            if not 0 <= flow.bucket < T or flow.tonnage_t < -1e-9:
                errors.append(f"{job.id}: invalid bucket or negative mass")
                continue
            rows[flow.bucket].append((job, flow))
    inv = {s.id: float(s.mass_t) for s in sc.spaces}
    inventory = {sid: [mass] for sid, mass in inv.items()}
    parcels = defaultdict(lambda: defaultdict(float))
    for parcel in sc.belt_profile:
        parcels[parcel.arrive_bucket][(sc.link(parcel.link_id).dst, parcel.grade)] += parcel.mass_t
    delivered = {d.id: [0.0] * T for d in sc.destinations}
    unstacked = [0.0] * T
    holding = defaultdict(float)
    holding_series = []
    holding_capacity = float(sc.config[DEMO_CONFIG]["receiving_holding_capacity_t"])
    dispatched = defaultdict(float)
    for t in range(T):
        for key, amount in parcels[t].items():
            holding[key] += amount
        if sum(holding.values()) > holding_capacity + 1e-6:
            errors.append(f"bucket {t}: receiving holding capacity exceeded; backpressure required")
        start = dict(inv)
        incoming, outgoing = defaultdict(float), defaultdict(float)
        machines, links = defaultdict(float), defaultdict(float)
        machine_groups, stack_draw = defaultdict(set), defaultdict(float)
        q_numerator, q_mass = defaultdict(lambda: defaultdict(float)), defaultdict(float)
        for job, flow in rows[t]:
            mass, path = float(flow.tonnage_t), list(flow.path or job.path)
            try:
                machine, pile = sc.machine(job.machine), sc.space(job.space)
                route = [sc.link(lid) for lid in path]
            except StopIteration:
                errors.append(f"{job.id}: unknown machine, pile or path link")
                continue
            if job.space not in machine.reachable_spaces or machine.site != pile.site:
                errors.append(f"{job.id}: invalid reach or site ownership")
            expected_kind = "stacker" if job.kind == "stack" else "reclaimer"
            if machine.kind != expected_kind:
                errors.append(f"{job.id}: machine cannot perform {job.kind}")
            if not route or any(a.dst != b.src for a, b in zip(route, route[1:])):
                errors.append(f"{job.id}: disconnected route")
                continue
            if any(not link.available for link in route) or not machine.available:
                errors.append(f"{job.id}: unavailable physical asset")
            machines[job.machine] += mass
            machine_groups[job.machine].add((job.kind, job.space, job.dest_id, job.grade))
            for link in route:
                links[link.id] += mass
            space_node = pile.node_id or f"SPACE:{pile.id}"
            if job.kind == "stack":
                if route[-1].dst != space_node or any(link.transit_buckets for link in route):
                    errors.append(f"{job.id}: invalid receiving route")
                if job.grade != pile.designated_grade:
                    errors.append(f"{job.id}: grade segregation violation")
                stack_draw[(route[0].src, job.grade)] += mass
                incoming[pile.id] += mass
            elif job.kind in ("dispatch", "reclaim"):
                if route[0].src != space_node:
                    errors.append(f"{job.id}: route does not start at source pile")
                if job.grade and job.grade != pile.grade:
                    errors.append(f"{job.id}: job grade differs from source material")
                outgoing[pile.id] += mass
                if job.kind == "dispatch":
                    corridor = route[-1]
                    if corridor.id != job.dest_id or corridor.transit_buckets < 1:
                        errors.append(f"{job.id}: invalid delayed corridor")
                    arrival_t = t + sum(link.transit_buckets for link in route)
                    parcels[arrival_t][(corridor.dst, pile.grade)] += mass
                    dispatched[corridor.id] += mass
                else:
                    dest = sc.destination(job.dest_id)
                    if route[-1].dst != dest.node_id or machine.site != dest.site:
                        errors.append(f"{job.id}: invalid terminal route/site")
                    delivered[job.dest_id][t] += mass
                    q_mass[job.dest_id] += mass
                    for parameter, value in sc.quality_of(pile.id).items():
                        q_numerator[job.dest_id][parameter] += value * mass
            else:
                errors.append(f"{job.id}: unsupported operation {job.kind}")
        for mid, mass in machines.items():
            machine = sc.machine(mid)
            cap = sc.effective_resource_rate_tph(
                {mid, f"SITE:{machine.site}"}, machine.rate_max_tph, bucket=t)
            if mass > cap + 1e-6 or (mass > 1e-6 and mass < machine.rate_min_tph - 1e-6):
                errors.append(f"bucket {t} {mid}: machine capacity/rate violation")
            if len(machine_groups[mid]) > 1:
                errors.append(f"bucket {t} {mid}: more than one simultaneous activity")
        for lid, mass in links.items():
            link = sc.link(lid)
            cap = sc.effective_resource_rate_tph({f"LINK:{lid}"}, link.capacity_tph, bucket=t)
            if mass > cap + 1e-6:
                errors.append(f"bucket {t} {lid}: link capacity/calendar violation")
        shared, conflicts = defaultdict(list), defaultdict(list)
        for link in sc.links:
            if link.shared_group:
                shared[link.shared_group].append(link)
            if link.conflict_group and links[link.id] > 1e-6:
                conflicts[link.conflict_group].append(link.id)
        for group, members in shared.items():
            if sum(links[link.id] for link in members) > min(
                    link.capacity_tph for link in members) + 1e-6:
                errors.append(f"bucket {t}: shared route capacity violation {group}")
        for group, active in conflicts.items():
            if len(active) > 1:
                errors.append(f"bucket {t}: mutually exclusive routes {group}")
        for site in sc.sites:
            for group in site.exclusive_machine_groups:
                if sum(machines[mid] > 1e-6 for mid in group) > 1:
                    errors.append(f"bucket {t}: exclusive machines {group}")
        for key, amount in stack_draw.items():
            if amount > holding[key] + 1e-6:
                errors.append(f"bucket {t}: stacking unavailable arrival {key}")
        for key in holding:
            holding[key] -= stack_draw[key]
        holding_series.append(sum(holding.values()))
        for pile in sc.spaces:
            sid = pile.id
            if outgoing[sid] > start[sid] + 1e-6:
                errors.append(f"bucket {t} {sid}: reclaim exceeds start inventory")
            inv[sid] = start[sid] + incoming[sid] - outgoing[sid]
            if inv[sid] < -1e-6 or inv[sid] > pile.capacity_t + 1e-6:
                errors.append(f"bucket {t} {sid}: finite inventory bound violated")
            inventory[sid].append(inv[sid])
        for dest in sc.destinations:
            mass = delivered[dest.id][t]
            if mass > sc.demand_tph(dest.id, t) + 1e-6:
                errors.append(f"bucket {t} {dest.id}: unrequested delivery")
            for spec in dest.spec:
                if not spec.hard or mass <= 1e-6:
                    continue
                value = q_numerator[dest.id].get(spec.param, 0.0) / mass
                if (spec.pmin is not None and value < spec.pmin + spec.margin - 1e-6) or \
                        (spec.pmax is not None and value > spec.pmax - spec.margin + 1e-6):
                    errors.append(f"bucket {t} {dest.id}: hard quality violation {spec.param}")
    for corridor, nomination in sc.config["CORRIDOR_DISPATCH_TARGET_T"].items():
        if abs(dispatched[corridor] - float(nomination)) > 1e-5:
            errors.append(f"{corridor}: dispatched mass does not meet common fixed nomination")
    terminal = sum(sum(values) for values in delivered.values())
    initial = sum(s.mass_t for s in sc.spaces) + sum(p.mass_t for p in sc.belt_profile)
    in_transit = sum(sum(values.values()) for t, values in parcels.items() if t >= T)
    residual = initial - sum(inv.values()) - terminal - sum(holding.values()) - in_transit
    if abs(residual) > 1e-5:
        errors.append(f"Whole-experiment mass balance residual {residual:g} t")
    result.delivered, result.inventory, result.surge_unstacked = delivered, inventory, unstacked
    result.unmet = {d.id: [max(sc.demand_tph(d.id, t) - delivered[d.id][t], 0.0)
                           for t in range(T)] for d in sc.destinations}
    result.safety_short = {s.id: [max(s.safety_stock_t - mass, 0.0)
                                 for mass in inventory[s.id][1:]] for s in sc.spaces}
    result.breakdown["physical_mass_balance_residual_t"] = residual
    result.breakdown["in_transit_end_t"] = in_transit
    result.breakdown["unstacked_t"] = sum(unstacked)
    result.breakdown["receiving_queue_peak_t"] = max(holding_series, default=0.0)
    result.breakdown["receiving_queue_end_t"] = sum(holding.values())
    result.breakdown["receiving_queue_t_h"] = sum(holding_series) * dt
    return result, sorted(set(errors))


def run_coordination_benchmark(
        sc: Optional[Scenario] = None, options: Optional[BenchmarkOptions] = None,
        progress_cb: Optional[Callable[[str], None]] = None) -> BenchmarkResult:
    """Independent operators vs coordinated optimizer, audited identically."""
    snapshot = copy.deepcopy(sc) if sc is not None else make_coordination_scenario()
    options = options or BenchmarkOptions()
    if not math.isfinite(options.max_solve_s) or options.max_solve_s <= 0:
        raise ValueError("max_solve_s must be finite and positive")
    result = BenchmarkResult(
        snapshot.name, scenario_fingerprint(snapshot), "independent_sms_coordination", {},
        assumptions=[
            "Synthetic 12-hour, three-SMS reduction: all stocks, rates, assays, "
            "release protocols and outages are assumed. It omits washing stages, "
            "pile construction, detailed rail travel and live integration.",
            "Identical physical plant, initial stock, 600 t dated terminal demand "
            "and fixed 250 t C1/C2 nominations in every arm; no supply advantage.",
            "Each mine rule sees only its own inventory, rate, calendar and "
            "volume nomination. It cannot see M16 inventory, receiving maintenance "
            "or the other mine's jobs. No optimizer authors local jobs.",
            "M16 knows its own future demand and receiving calendar, shares "
            "available reception fairly across current parcels, and reclaims a "
            "known on-spec 50/50 blend. No random mistakes or unsafe jobs are used.",
            "Local sensitivities: earliest release, even release over five hours, "
            "and a pre-agreed alternating C1/C2 slot protocol. The best service "
            "result is the headline comparator; no measured human timing is assumed.",
            "The coordinated optimizer additionally sees all sites' calendars and "
            "stocks and can move release times. This is an information-sharing "
            "and coordination mechanism, not a claim that operators are less capable.",
            "The same independent physical replay audits all plans and reconstructs "
            "terminal delivery, finite inventory and retained arrival backlog. "
            "The experiment assumes a finite 500 t receiving holding buffer; "
            "missed arrivals remain there for later reception, never disappear. "
            "Capacity overflow invalidates the arm and requires backpressure. "
            "Actual corridor holding feasibility/capacity is unconfirmed.",
            "The current CP-SAT model does not optimize holding-buffer decisions; "
            "its plan is evaluated by the same buffer-enabled replay and is a "
            "conservative feasible member of this wider experiment. With adequate "
            "holding, coordinated throughput may equal capable local operation; "
            "reduced staging exposure is reported separately from product benefit.",
            "Run the no-receiving-outage control and capacity sensitivities before "
            "using results in a business case. Benefits are not extrapolated to OCP."])
    result.provenance = benchmark_provenance(options)
    labels = {"local_front_loaded": "Independent SMS: earliest release",
              "local_paced": "Independent SMS: evenly paced nominations",
              "local_staggered": "Independent SMS: agreed alternating slots",
              "coordinated": "Coordinated model: shared site state"}
    for protocol in LOCAL_PROTOCOLS + ("coordinated",):
        if progress_cb:
            progress_cb(labels[protocol])
        plan = solve_plan(copy.deepcopy(snapshot), SolveOptions(max_time_s=options.max_solve_s)) \
            if protocol == "coordinated" else independent_sms_plan(snapshot, protocol)
        errors = []
        if plan.status in SUCCESS_STATUSES:
            plan, errors = replay_coordination_plan(snapshot, plan)
        kpis = compute_kpis(snapshot, plan) if plan.status in SUCCESS_STATUSES and not errors else {}
        if kpis:
            kpis["unstacked_t"] = sum(plan.surge_unstacked)
            kpis["physical_mass_balance_residual_t"] = plan.breakdown["physical_mass_balance_residual_t"]
            for key in ("receiving_queue_peak_t", "receiving_queue_end_t", "receiving_queue_t_h"):
                kpis[key] = plan.breakdown[key]
        decisions = [] if protocol == "coordinated" else [
            {"site": site, "protocol": protocol,
             "visible": "Own pile inventory, own equipment/calendar, static 250 t nomination",
             "hidden": "Other sites' inventories, jobs and receiving calendar"}
            for site in ("LOUTA", "BENGUERIR")]
        result.arms[protocol] = BenchmarkArm(
            protocol, labels[protocol], plan, kpis, decisions,
            warnings=list(plan.reasons) if plan.status not in SUCCESS_STATUSES else [],
            physical_errors=errors)
    return finish_comparison(result)
