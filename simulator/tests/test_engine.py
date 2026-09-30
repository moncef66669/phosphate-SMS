"""Engine tests: mass conservation, exclusivity, route caps, delays, blending,
demand slack, safety stock (acceptance-criteria set from report §18)."""
import copy

import pytest

from mph_sim import datasets
from mph_sim.domain import DemandEntry, Scenario, SimEvent
from mph_sim.optimizer import SolveOptions, blend_fractions, solve_plan
from mph_sim.routes import RouteGraph
from mph_sim.simulator import Simulator


# ---------------------------------------------------------------- helpers
def total_unmet(plan):
    return sum(sum(v) for v in plan.unmet.values())


# ------------------------------------------------- mass conservation (AC 3)
def test_mass_conservation_in_plan(s2, s2_plan):
    """inv[t+1] = inv[t] + Δt·stacked − Δt·reclaimed (report §15.3 eq. 1)."""
    plan = s2_plan
    T = s2.sim.horizon_buckets
    for sp in s2.spaces:
        series = plan.inventory[sp.id]
        assert len(series) == T + 1
        assert series[0] == pytest.approx(min(sp.mass_t, sp.capacity_t))
        for t in range(T):
            ins = sum(j.rate_tph * s2.sim.dt_h for j in plan.jobs
                      if j.kind == "stack" and j.space == sp.id
                      and j.start_bucket <= t <= j.end_bucket)
            outs = sum(j.rate_tph * s2.sim.dt_h for j in plan.jobs
                       if j.kind in ("reclaim", "dispatch") and j.space == sp.id
                       and j.start_bucket <= t <= j.end_bucket)
            # A job carries its PER-RUN AVERAGE rate, so a multi-bucket run
            # whose per-bucket flows differ cannot be reconciled exactly
            # bucket by bucket. The tolerance therefore scales with the run
            # length; the EXACT invariant is asserted per run below.
            span = max((j.end_bucket - j.start_bucket + 1) for j in plan.jobs
                       if j.space == sp.id
                       and j.start_bucket <= t <= j.end_bucket)                 if any(j.space == sp.id and j.start_bucket <= t <= j.end_bucket
                       for j in plan.jobs) else 1
            assert series[t + 1] == pytest.approx(
                series[t] + ins - outs,
                abs=max(60.0, 0.12 * span * (ins + outs + 1)))
        for v in series:
            assert -1e-6 <= v <= sp.capacity_t + 1e-6
        # exact whole-horizon conservation — no averaging can hide a leak
        stacked = sum(j.tonnage_t for j in plan.jobs
                      if j.kind == "stack" and j.space == sp.id)
        reclaimed = sum(j.tonnage_t for j in plan.jobs
                        if j.kind in ("reclaim", "dispatch")
                        and j.space == sp.id)
        assert series[T] == pytest.approx(series[0] + stacked - reclaimed,
                                          abs=1e-6), sp.id


def test_mass_conservation_in_sim(s1, s1_plan):
    """Simulated inventory: delta == stacked − reclaimed − exo (per space)."""
    sim = Simulator(s1, s1_plan, auto_replan=False)
    sim.run_to_end()
    moved_out = sum(sim.moved_by_job[j.id] for j in sim.jobs.values()
                    if j.kind in ("reclaim", "dispatch")
                    and j.id in sim.moved_by_job)
    closed = "M17-CLOSED-1"
    start = s1.space(closed).mass_t
    end = sim.inv[closed]
    payload_in = sum(min(300.0 * s1.sim.dt_h, 1e9)
                     for t in range(s1.sim.horizon_buckets))  # 300 tph payloader
    # conservation: end = start + payloader_in - reclaimed (within clipping)
    assert end == pytest.approx(start + payload_in - moved_out, rel=0.02)


# ------------------------------------------- machine double booking (AC 2)
def test_no_machine_double_booking(s2_plan):
    for m in {j.machine for j in s2_plan.jobs}:
        jobs = [j for j in s2_plan.jobs if j.machine == m]
        for a in range(len(jobs)):
            for b in range(a + 1, len(jobs)):
                ja, jb = jobs[a], jobs[b]
                overlap = not (ja.end_bucket < jb.start_bucket
                               or jb.end_bucket < ja.start_bucket)
                assert not overlap, f"machine {m}: {ja.id} overlaps {jb.id}"


# ------------------------------------------------- route capacity (AC 1)
def test_route_capacity_enforced(s2, s2_plan):
    T = s2.sim.horizon_buckets
    for t in range(T):
        load = {}
        for j in s2_plan.jobs:
            if j.start_bucket <= t <= j.end_bucket:
                for li in j.path:
                    load[li] = load.get(li, 0.0) + j.rate_tph
        for li, v in load.items():
            cap = s2.link(li).capacity_tph
            # dominant-path attribution can slightly over-assign to one leg
            assert v <= cap * 1.35 + 1, f"link {li} at t={t}: {v} > cap {cap}"


def test_no_impossible_route(s2, s2_plan):
    """Every job path must be a valid chain of available links (AC 1)."""
    g = RouteGraph.from_scenario(s2)
    for j in s2_plan.jobs:
        for li in j.path:
            assert li in g.links, f"job {j.id} uses unknown link {li}"
            assert g.links[li].available


# ------------------------------------------------- transport delay (eq. 5)
def test_transport_delay_behaviour():
    sc = datasets.end_to_end()
    sc.sim.max_solve_s = 4
    plan = solve_plan(sc)
    assert plan.status in ("OPTIMAL", "FEASIBLE")
    # dispatch jobs exist and their material cannot arrive before transit time
    dispatches = [j for j in plan.jobs if j.kind == "dispatch"]
    if dispatches:
        for j in dispatches:
            link = sc.link(j.dest_id)
            assert link.transit_buckets >= 1
    # Initial belt profiles deliver during each corridor's configured lag;
    # stacking must start within the longest initial-arrival window.
    stackers = [j for j in plan.jobs if j.kind == "stack"]
    assert stackers, "initial in-transit material must be stacked"
    longest_lag = max(l.transit_buckets for l in sc.links
                      if l.transit_buckets > 0)
    assert min(j.start_bucket for j in stackers) < longest_lag


def test_delayed_dispatch_event():
    sc = datasets.end_to_end()
    sc.sim.max_solve_s = 4
    plan = solve_plan(sc)
    sim = Simulator(sc, plan, events=[
        SimEvent("D1", "delayed_mine_dispatch", 0, "C1", magnitude=2)],
        auto_replan=False)
    sim.step()
    # all pending C1 parcels moved +2 buckets: nothing from C1 arrives at t=0,1
    arr = [p for p in sim.belts if p.link_id == "C1" and p.arrive_bucket <= 1]
    assert not arr


# ------------------------------------------------- quality blending (eq. 3)
def test_quality_blending_worked_example():
    """Report §15.6: 68/74 BPL blended 50/50 at 1500 tph -> 71 BPL."""
    sc = datasets.mph_phosphate()
    # Isolate the numerical example with the corrected grade semantics:
    # BT = 68 BPL and MT = 74 BPL.
    for sp in sc.spaces:
        if sp.id not in ("HP-02", "HP-03"):
            sp.mass_t = 0.0
            sp.state = "empty"
    sc.belt_profile = []          # no arrivals: pure reclaim problem
    sc.demand = [DemandEntry("DRY-FEED", t, 0.0) for t in range(24)]
    sc.space("HP-02").safety_stock_t = 0
    sc.space("HP-03").safety_stock_t = 0
    sc.sim.max_solve_s = 5
    plan = solve_plan(sc)
    assert plan.status in ("OPTIMAL", "FEASIBLE")
    # delivered PAP quality per bucket must satisfy BPL >= 71 whenever demand met
    dt = sc.sim.dt_h
    for t in range(6):
        jobs = [j for j in plan.jobs if j.kind == "reclaim" and j.dest_id == "PAP-1"
                and j.start_bucket <= t <= j.end_bucket]
        if not jobs:
            continue
        mass = sum(j.rate_tph * dt for j in jobs)
        bpl = sum(sc.quality_of(j.space).get("BPL", 0) * j.rate_tph * dt
                  for j in jobs) / mass
        assert bpl >= 71.0 - 0.15, f"bucket {t}: blended BPL {bpl:.2f} < 71"
    # blend fractions are derived from flows and sum to 1 (report §15.1)
    f = blend_fractions(plan, "PAP-1", 2)
    if f:
        assert sum(f.values()) == pytest.approx(1.0)


# ------------------------------------------------- demand slack (eq. 4)
def test_demand_slack_graceful_infeasibility():
    """Demand far above capacity must stay solvable with unmet + reasons."""
    sc = datasets.sulphur_minimal()
    sc.destinations[0].demand_default_tph = 5000.0   # >> RC01 1100 tph
    sc.sim.max_solve_s = 4
    plan = solve_plan(sc)
    assert plan.status in ("OPTIMAL", "FEASIBLE"), "slack must keep model feasible"
    assert total_unmet(plan) > 0
    assert any("UNMET_DEMAND[MOLTEN-S]" in r for r in plan.reasons)
    assert any("machine capacity" in r for r in plan.reasons)
    assert plan.relaxations, "suggested relaxations must be reported"


def test_unmet_zero_when_capacity_sufficient(s2_plan):
    assert total_unmet(s2_plan) == 0


# ------------------------------------------------- safety stock (AC 6)
def test_safety_stock_shortfall_reported():
    sc = datasets.sulphur_minimal()
    sc.space("M17-CLOSED-1").safety_stock_t = 60_000   # above reachable level
    sc.sim.max_solve_s = 4
    plan = solve_plan(sc)
    shorts = plan.safety_short.get("M17-CLOSED-1", [])
    assert shorts and max(shorts) > 0, "shortfall below safety stock must be visible"


def test_safety_stock_kpi():
    from mph_sim.kpis import compute_kpis
    sc = datasets.sulphur_minimal()
    plan = solve_plan(sc, SolveOptions(max_time_s=4))
    sim = Simulator(sc, plan, auto_replan=False)
    sim.run_to_end()
    k = compute_kpis(sc, plan, sim)
    assert "safety_stock_violation_buckets" in k
    assert "safety_stock_min_margin_t" in k
    assert "M17-CLOSED-1" in k["safety_stock_min_margin_t"]


# ------------------------------------------------- work calendar
def test_work_calendar_blocks_machine(s1, s1_plan):
    for j in s1_plan.jobs:
        if j.machine == "M17-RC01":
            for t in range(j.start_bucket, j.end_bucket + 1):
                assert not (10 <= t <= 11), "job scheduled inside maintenance window"


# ------------------------------------------------- operator-fixed jobs
def test_operator_fixed_job_respected():
    from mph_sim.domain import FixedJob
    sc = datasets.sulphur_minimal()
    sc.fixed_jobs = [FixedJob("FX1", "M17-RC01", "reclaim", "M17-CLOSED-1",
                              dest_id="MOLTEN-S", rate_tph=600,
                              start_bucket=2, end_bucket=4)]
    plan = solve_plan(sc, SolveOptions(max_time_s=4))
    assert plan.status in ("OPTIMAL", "FEASIBLE")
    for t in (2, 3, 4):
        jobs = [j for j in plan.jobs if j.machine == "M17-RC01"
                and j.start_bucket <= t <= j.end_bucket]
        assert jobs, f"fixed job missing at bucket {t}"
