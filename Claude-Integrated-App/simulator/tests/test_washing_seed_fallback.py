"""Failed label-only seeds must not suppress authoritative feasibility search."""
from copy import deepcopy

import pytest
from ortools.sat.python import cp_model

from mph_sim import optimizer, washing_seed
from mph_sim.domain import Destination, ExogenousFlow
from mph_sim.routes import RouteGraph
from test_prewash_blending import blend_fixture, HIGH


def _large_typed_fixture(*, required_spill=False, stable_minimum=500):
    sc = blend_fixture()
    for machine in sc.machines:
        machine.rate_min_tph = stable_minimum
    # Exercise the real >50 candidate bootstrap gate without a large horizon.
    # Extra receivers have no demand and share the existing physical outlet.
    sc.destinations.extend(Destination(f"UNUSED-{i}", "S", "D") for i in range(28))
    sc.config["OBJECTIVE_SECONDARY_WALL_TIME_S"] = 0
    sc.config["OBJECTIVE_SECONDARY_DETERMINISTIC_TIME_S"] = 0
    sc.config["ALLOW_CONTROLLED_FLOW_SPILL"] = False
    if required_spill:
        # 1,000 t opening + 6,000 t external arrival cannot fit in 5,000 t:
        # reclaim is <=1,000 t in this bucket, so >=1,000 t overflow is real.
        sc.exogenous.append(ExogenousFlow(
            "external-arrival", "A", 6000, "HIGH", start_bucket=0,
            end_bucket=0, quality=deepcopy(HIGH)))
    return sc


@pytest.mark.parametrize("required_spill, stable_minimum", [(False, 500), (True, 500), (False, 0)])
def test_failed_physical_seed_retains_verified_original_model_fallback(
        monkeypatch, required_spill, stable_minimum):
    sc = _large_typed_fixture(required_spill=required_spill, stable_minimum=stable_minimum)
    cands, _ = optimizer.build_candidates(sc, RouteGraph.from_scenario(sc))
    assert len(cands) > 50
    seed_calls = []

    def no_physical_seed(*args):
        seed_calls.append(True)
        return None

    monkeypatch.setattr(washing_seed, "physical_seed", no_physical_seed)
    native_solve = cp_model.CpSolver.solve
    solver_models = []
    statuses = []

    def stop_primary_after_verified_seed(self, model, *args, **kwargs):
        objective = str(model.proto.objective)
        solver_models.append({
            "constraints": [str(row) for row in model.proto.constraints],
            "variables": [str(row) for row in model.proto.variables],
            "objective": objective,
        })
        if objective:
            # Deterministic test double for a primary search that times out
            # before finding an incumbent. Populate a real solver response
            # so reporting remains exercised, then return UNKNOWN explicitly.
            stopped = model.clone()
            stopped.add_bool_or([])
            native_solve(self, stopped, *args, **kwargs)
            return cp_model.UNKNOWN
        status = native_solve(self, model, *args, **kwargs)
        statuses.append(status)
        return status

    monkeypatch.setattr(cp_model.CpSolver, "solve", stop_primary_after_verified_seed)
    plan = optimizer.solve_plan(
        sc, optimizer.SolveOptions(max_time_s=3, max_wall_time_s=10))

    assert seed_calls == [True]
    assert len(solver_models) == 3
    assert statuses[0] in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    assert statuses[1] in (cp_model.OPTIMAL, cp_model.FEASIBLE)
    assert not solver_models[0]["objective"]
    assert not solver_models[1]["objective"]
    assert solver_models[2]["objective"]
    # The fallback has every original hard constraint and variable domain,
    # without mapped-seed zero-spill restrictions or fixed legacy flows.
    assert solver_models[1]["constraints"] == solver_models[2]["constraints"]
    assert solver_models[1]["variables"] == solver_models[2]["variables"]
    assert any(reason.startswith("WASHING_BOOTSTRAP unfixed typed feasibility")
               for reason in plan.reasons)
    if stable_minimum:
        assert plan.status == "FEASIBLE", plan.reasons
        assert not plan.breakdown.get("minute_timing_errors", 0)
    else:
        # A mathematically feasible bootstrap is not permission to bypass the
        # existing minute/extraction audit. This zero-stable-rate fixture can
        # choose a 1 t raw command rounding to zero product; the invalid
        # extracted schedule must stay rejected rather than become accepted.
        assert plan.status == "PARTIAL", plan.reasons
        assert any("differs from job output" in reason for reason in plan.reasons)
    assert all(min(series) >= 0 for series in plan.inventory.values())
    assert all(max(series) <= sc.space(sid).capacity_t
               for sid, series in plan.inventory.items())
    if required_spill:
        assert plan.breakdown["spill_t"] >= 1000
        assert any(reason.startswith("SPACE_OVERFLOW") for reason in plan.reasons)


def test_physical_seed_replays_grade_typed_external_arrivals():
    """Mine supply must not disable the washing incumbent bootstrap."""
    sc = _large_typed_fixture(required_spill=False, stable_minimum=500)
    sc.exogenous.append(ExogenousFlow(
        "mine-arrival", "A", 100, "HIGH", start_bucket=0,
        end_bucket=0, quality=deepcopy(HIGH)))

    plan = optimizer.solve_plan(
        sc, optimizer.SolveOptions(max_time_s=3, max_wall_time_s=10))

    assert plan.status in ("OPTIMAL", "FEASIBLE"), plan.reasons
    assert any(reason.startswith(
        "WASHING_BOOTSTRAP mapped physical seed: ")
        for reason in plan.reasons), plan.reasons
    assert not any("seed not applicable to exogenous flows" in reason
                   for reason in plan.reasons)
