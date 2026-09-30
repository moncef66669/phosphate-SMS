"""Fairness, information boundaries and physical accounting of comparisons."""
import copy

import pytest

from mph_sim.benchmark import (BenchmarkArm, BenchmarkOptions, BenchmarkResult,
                               finish_comparison, local_source_commitments,
                               run_benchmark, scenario_fingerprint,
                               value_sensitivity)
from mph_sim.coordination_benchmark import (independent_sms_plan,
                                           make_coordination_scenario,
                                           replay_coordination_plan,
                                           run_coordination_benchmark)
from mph_sim.domain import (Destination, Grade, Link, Machine, Node, PlanResult,
                            Scenario, Segment, SimParams, Site, SpecRange,
                            StockSpace)


@pytest.fixture(scope="module")
def coordination_result():
    return run_coordination_benchmark(options=BenchmarkOptions(max_solve_s=1))


def test_common_physics_and_fixed_supply_for_all_arms(coordination_result):
    for arm in coordination_result.arms.values():
        assert arm.valid, (arm.id, arm.plan.status, arm.physical_errors, arm.plan.reasons)
        assert arm.kpis["physical_mass_balance_residual_t"] == pytest.approx(0)
        assert arm.kpis["final_product_demand_t"] == pytest.approx(600)
        assert arm.kpis["quality_unknown_t"] == 0
        assert arm.kpis["on_spec_mass_pct"] == pytest.approx(100)
        for corridor in ("C1", "C2"):
            assert sum(j.tonnage_t for j in arm.plan.jobs
                       if j.kind == "dispatch" and j.dest_id == corridor) == pytest.approx(250)


def test_headline_uses_strongest_protocol_and_conforming_terminal_product(coordination_result):
    result = coordination_result
    assert result.best_local_id in ("local_paced", "local_staggered")
    assert result.arms["local_front_loaded"].kpis["final_product_on_time_t"] == pytest.approx(600)
    assert result.arms[result.best_local_id].kpis["final_product_on_time_t"] == pytest.approx(600)
    assert result.arms["coordinated"].kpis["final_product_on_time_t"] == pytest.approx(600)
    assert result.deltas["final_product_on_time_t"] == pytest.approx(0)
    assert result.deltas["final_product_on_spec_on_time_t"] == pytest.approx(0)
    assert result.deltas["unstacked_t"] == pytest.approx(0)
    assert result.deltas["receiving_queue_peak_t"] == pytest.approx(-200)
    assert result.deltas["receiving_queue_t_h"] == pytest.approx(-750)


def test_no_outage_control_does_not_manufacture_a_model_benefit():
    result = run_coordination_benchmark(
        make_coordination_scenario(receiver_outage=False), BenchmarkOptions(max_solve_s=1))
    assert all(arm.valid for arm in result.arms.values())
    assert result.arms["local_paced"].kpis["final_product_on_time_t"] == pytest.approx(600)
    assert result.arms["local_staggered"].kpis["final_product_on_time_t"] == pytest.approx(600)
    assert result.deltas["final_product_on_time_t"] == pytest.approx(0)


@pytest.mark.parametrize("protocol", ["local_front_loaded", "local_paced", "local_staggered"])
def test_mine_jobs_do_not_read_remote_calendar_or_stock(protocol):
    original = make_coordination_scenario(False)
    changed = make_coordination_scenario(True)
    changed.space("DEMO-HUB-L").mass_t = 0
    changed.space("DEMO-HUB-B").mass_t = 0
    changed.destination("DEMO-PRODUCT").priority = 10
    def dispatch_signature(sc):
        return [(j.machine, j.space, j.start_bucket, j.tonnage_t, j.dest_id)
                for j in independent_sms_plan(sc, protocol).jobs if j.kind == "dispatch"]
    assert dispatch_signature(original) == dispatch_signature(changed)


def test_independent_author_never_calls_optimizer(monkeypatch):
    import mph_sim.coordination_benchmark as module
    def forbidden(*args, **kwargs):
        raise AssertionError("independent operator attempted to optimize")
    monkeypatch.setattr(module, "solve_plan", forbidden)
    assert independent_sms_plan(make_coordination_scenario()).jobs


def test_replay_reconstructs_metrics_instead_of_trusting_arm_totals():
    scenario = make_coordination_scenario()
    plan = independent_sms_plan(scenario)
    plan.delivered = {"DEMO-PRODUCT": [999999] * 12}
    plan.inventory = {space.id: [999999] * 13 for space in scenario.spaces}
    plan.surge_unstacked = [0] * 12
    replayed, errors = replay_coordination_plan(scenario, plan)
    assert not errors
    assert sum(replayed.delivered["DEMO-PRODUCT"]) == pytest.approx(600)
    assert sum(replayed.surge_unstacked) == pytest.approx(0)
    assert replayed.breakdown["receiving_queue_t_h"] == pytest.approx(800)
    assert replayed.inventory["DEMO-MINE-L"][-1] == pytest.approx(350)


def test_replay_rejects_over_capacity_and_missing_physical_profile():
    scenario = make_coordination_scenario()
    plan = independent_sms_plan(scenario)
    dispatch = next(job for job in plan.jobs if job.kind == "dispatch")
    dispatch.tonnage_t = 200
    dispatch.bucket_flows[0].tonnage_t = 200
    _, errors = replay_coordination_plan(scenario, plan)
    assert any("capacity" in error for error in errors)
    assert any("nomination" in error for error in errors)
    dispatch.bucket_flows = []
    _, errors = replay_coordination_plan(scenario, plan)
    assert any("exact bucket flows" in error for error in errors)


def test_replay_rejects_same_bucket_pass_through():
    scenario = make_coordination_scenario()
    plan = independent_sms_plan(scenario)
    reclaim = next(job for job in plan.jobs if job.kind == "reclaim")
    reclaim.start_bucket = reclaim.end_bucket = 0
    reclaim.tonnage_t = 100
    reclaim.bucket_flows[0].bucket = 0
    reclaim.bucket_flows[0].tonnage_t = 100
    _, errors = replay_coordination_plan(scenario, plan)
    assert any("start inventory" in error for error in errors)


def test_coordination_comparison_does_not_mutate_live_scenario():
    scenario = make_coordination_scenario()
    before = scenario_fingerprint(scenario)
    run_coordination_benchmark(scenario, BenchmarkOptions(max_solve_s=0.2))
    assert scenario_fingerprint(scenario) == before


def _source_scenario():
    scenario = Scenario("local source experiment", sim=SimParams(horizon_buckets=2))
    scenario.sites = [Site("A", "A", "hub"), Site("B", "B", "hub")]
    scenario.grades = [Grade("G", "G", {"BPL": 71})]
    for site in ("A", "B"):
        scenario.destinations.append(Destination(
            f"{site}-DEST", site, f"{site}-OUT", demand_default_tph=10,
            spec=[SpecRange("BPL", 70, 75, 71)]))
        scenario.nodes.append(Node(f"{site}-OUT", site, "dest"))
        sources = []
        for idx, quality in ((1, 74), (2, 71)):
            sid = f"{site}-{idx}"
            sources.append(sid)
            scenario.spaces.append(StockSpace(
                sid, site, "M16-humid", "closed", 100, 50, "G",
                quality={"BPL": quality}, x_from=(idx - 1) * 100,
                x_to=idx * 100))
            scenario.nodes.append(Node(f"SPACE:{sid}", site, "space"))
            scenario.links.append(Link(f"{sid}-OUT", f"SPACE:{sid}", f"{site}-OUT", 100))
            scenario.segments.append(Segment(sid, 0, 100, 50, "G", age_days=3 - idx))
        scenario.machines.append(Machine(f"{site}-RC", site, "reclaimer", 0, 100,
                                         sources, position_x=50, setup_min=0))
    return scenario


def test_source_policy_is_site_local_and_honours_quality():
    scenario = _source_scenario()
    fifo, decisions = local_source_commitments(scenario, "local_fifo")
    assert decisions[0]["selected_sources"] == ["A-1"]
    quality, q_decisions = local_source_commitments(scenario, "local_quality")
    assert q_decisions[0]["selected_sources"] == ["A-2"]
    scenario.space("B-1").mass_t = 0
    scenario.space("B-2").quality = {"BPL": 10}
    scenario.destination("B-DEST").demand_default_tph = 10000
    _, changed = local_source_commitments(scenario, "local_quality")
    assert changed[0] == q_decisions[0]


def test_source_policy_preserves_blend_and_replenishment_flexibility():
    scenario = _source_scenario()
    scenario.destination("A-DEST").spec = [SpecRange("BPL", 72, 73, hard=True)]
    _, decisions = local_source_commitments(scenario, "local_quality")
    assert set(decisions[0]["selected_sources"]) == {"A-1", "A-2"}
    scenario.destination("A-DEST").spec = []
    scenario.destination("A-DEST").demand_default_tph = 1000
    _, decisions = local_source_commitments(scenario, "local_fifo")
    assert set(decisions[0]["selected_sources"]) == {"A-1", "A-2"}


def test_full_source_comparison_keeps_inputs_and_provenance():
    scenario = _source_scenario()
    before = scenario_fingerprint(scenario)
    result = run_benchmark(scenario, BenchmarkOptions(max_solve_s=0.2))
    assert all(arm.valid for arm in result.arms.values())
    assert scenario_fingerprint(scenario) == before == result.input_fingerprint
    assert result.experiment == "local_source_portfolios"
    assert "global CP-SAT scheduler" in " ".join(result.assumptions)
    exported = result.to_dict()
    assert "plan" not in exported["arms"]["coordinated"]
    assert exported["provenance"]["solver_budget_per_window_deterministic_s"] == 0.2
    assert len(exported["provenance"]["source_sha256"]["benchmark.py"]) == 64


def test_failed_or_negative_results_do_not_become_a_positive_claim():
    def arm(key, tonnes, status="FEASIBLE"):
        return BenchmarkArm(key, key, PlanResult(status=status),
                            {"final_product_on_time_t": tonnes, "on_spec_mass_t": tonnes})
    failed = BenchmarkResult("S", "hash", "test", {
        "local_paced": arm("local_paced", 10),
        "coordinated": arm("coordinated", 0, "UNKNOWN")})
    assert not finish_comparison(failed).deltas
    negative = BenchmarkResult("S", "hash", "test", {
        "local_paced": arm("local_paced", 10),
        "coordinated": arm("coordinated", 5)})
    finish_comparison(negative)
    assert negative.deltas["final_product_on_time_t"] == -5
    assert any("less on-time" in warning for warning in negative.warnings)


def test_value_sensitivity_requires_verified_quality_and_explicit_annualization(coordination_result):
    value = value_sensitivity(coordination_result, contribution_per_t=10)
    assert value["contribution_per_period"] == pytest.approx(0)
    assert value["annual_contribution"] == 0
    assert value["simple_payback_years"] is None
    unknown = copy.deepcopy(coordination_result)
    unknown.deltas.pop("final_product_on_spec_on_time_t")
    assert value_sensitivity(unknown, 10)["available"] is False
    with pytest.raises(ValueError):
        value_sensitivity(coordination_result, float("nan"))


def test_financial_sensitivity_preserves_negative_verified_tonnes(coordination_result):
    negative = copy.deepcopy(coordination_result)
    negative.deltas["final_product_on_spec_on_time_t"] = -100
    value = value_sensitivity(negative, 10, annual_periods=100, annual_cost=1000)
    assert value["annual_net_benefit"] == pytest.approx(-101000)
    assert value["simple_payback_years"] is None
    assert value["break_even_contribution_per_t"] is None


def test_holding_buffer_cannot_disappear_or_overflow_silently():
    scenario = make_coordination_scenario()
    from mph_sim.coordination_benchmark import DEMO_CONFIG
    scenario.config[DEMO_CONFIG]["receiving_holding_capacity_t"] = 50.0
    plan = independent_sms_plan(scenario)
    replayed, errors = replay_coordination_plan(scenario, plan)
    assert any("holding capacity" in error for error in errors)
    assert sum(replayed.surge_unstacked) == 0
    assert replayed.breakdown["physical_mass_balance_residual_t"] == pytest.approx(0)


def test_timing_incomplete_full_plans_cannot_support_a_benefit(monkeypatch):
    import mph_sim.benchmark as module
    scenario = _source_scenario()
    def unresolved(*args, **kwargs):
        return PlanResult(status="FEASIBLE", breakdown={"minute_timing_errors": 2})
    monkeypatch.setattr(module, "solve_plan", unresolved)
    result = run_benchmark(scenario)
    assert not any(arm.valid for arm in result.arms.values())
    assert not result.deltas
    assert result.best_local_id == ""
