"""Consolidation moves buffered tonnes; it cannot delete a required residual."""
import math

import pytest
from ortools.sat.python import cp_model

from mph_sim.campaigns import default_campaign_policy_config
from mph_sim.domain import (Grade, Link, Machine, Node, ObjectiveWeights,
                            ProcessTransfer, Scenario, SimParams, Site, StockSpace)
from mph_sim.objective_config import operational_objective_errors
from mph_sim.optimizer import solve_plan
from mph_sim.parcel_consolidation import consolidate_parcels


def _buffered_campaign(quantities=(1, 249, 250), blocked=()):
    model = cp_model.CpModel()
    flows, flags = [], []
    total = sum(quantities)
    inventory = [model.new_int_var(0, 1000, f"inv_{t}")
                 for t in range(4)]
    model.add(inventory[0] == 0)
    for t in range(3):
        x = model.new_int_var(0, 500, f"x_{t}")
        active = model.new_bool_var(f"active_{t}")
        short = model.new_bool_var(f"short_{t}")
        model.add(x <= 500 * active)
        model.add(x >= active)
        model.add(short <= active)
        model.add(x >= 250 * active - 249 * short)
        model.add(x <= 249).only_enforce_if(short)
        if t in blocked:
            model.add(x == 0)
        # Material may wait in the pile, never arrive retroactively.
        model.add(inventory[t + 1] == inventory[t] + x
                  - (total if t == 2 else 0))
        flows.append(x)
        flags.append(short)
    model.add(sum(flows) == total)
    model.add(inventory[-1] == 0)
    seed = model.clone()
    for x, value in zip(flows, quantities):
        seed.add(x == value)
    incumbent = cp_model.CpSolver()
    assert incumbent.solve(seed) == cp_model.OPTIMAL
    return model, incumbent, flows, flags, inventory


def _polish(model, incumbent, flows, flags, inventory, **overrides):
    arguments = dict(
        primary_expression=flows[0], parcel_flags=flags,
        fixed_expressions=[sum(flows), inventory[-1]],
        wall_s=2, deterministic_s=1, seed=7)
    arguments.update(overrides)
    return consolidate_parcels(model, incumbent, **arguments)


def test_residuals_batch_without_changing_quantities_or_worsening_objective():
    model, initial, flows, flags, inventory = _buffered_campaign()
    before_proto = str(model.proto)
    result = _polish(model, initial, flows, flags, inventory)
    assert result.accepted
    assert (result.before, result.after) == (2, 0)
    assert sum(result.solver.value(x) for x in flows) == 500
    assert result.solver.value(flows[0]) <= initial.value(flows[0])
    assert result.solver.value(inventory[-1]) == 0
    assert all(result.solver.value(i) >= 0 for i in inventory)
    assert str(model.proto) == before_proto, "the primary model is untouched"


def test_necessary_one_tonne_delivery_is_not_lost_or_made_infeasible():
    model, initial, flows, flags, inventory = _buffered_campaign((0, 0, 1))
    result = _polish(model, initial, flows, flags, inventory)
    assert not result.accepted
    assert result.solver is initial
    assert result.before == result.after == 1
    assert sum(result.solver.value(x) for x in flows) == 1


def test_hourly_service_protection_keeps_a_required_micro_command_visible():
    model, initial, flows, flags, inventory = _buffered_campaign()
    # An hourly receiving-unit commitment cannot be silently moved. Protect
    # the original early bucket as an additional service contract.
    result = _polish(model, initial, flows, flags, inventory,
                     fixed_expressions=[sum(flows), inventory[-1], flows[0]])
    assert result.after >= 1
    assert result.solver.value(flows[0]) == 1


def test_batching_still_honours_a_blocked_calendar_bucket():
    model, initial, flows, flags, inventory = _buffered_campaign(
        (0, 1, 499), blocked=(0,))
    result = _polish(model, initial, flows, flags, inventory)
    assert result.accepted
    assert result.after == 0
    assert result.solver.value(flows[0]) == 0


def test_failed_polish_retains_incumbent(monkeypatch):
    model, initial, flows, flags, inventory = _buffered_campaign()
    real_solver = cp_model.CpSolver

    class FailingSolver(real_solver):
        def solve(self, candidate, *args, **kwargs):
            candidate.add(False)
            return super().solve(candidate, *args, **kwargs)

    monkeypatch.setattr(cp_model, "CpSolver", FailingSolver)
    result = _polish(model, initial, flows, flags, inventory)
    assert result.status == "INFEASIBLE"
    assert result.solver is initial
    assert not result.accepted


def test_no_budget_never_starts_another_solve():
    model, initial, flows, flags, inventory = _buffered_campaign()
    result = _polish(model, initial, flows, flags, inventory, wall_s=0)
    assert not result.attempted
    assert result.solver is initial


@pytest.mark.parametrize("raise_error", [False, True])
def test_primary_objective_and_incumbent_survive_polish_failures(
        monkeypatch, raise_error):
    sc = Scenario(
        "necessary one tonne target", sites=[Site("TEST", "Test", "mine")],
        grades=[Grade("G", "G")],
        sim=SimParams(horizon_buckets=3, max_solve_s=1, seed=7),
        config={"PROCESS_STAGE_OUTPUT_TARGET_T": {"STACK": 1},
                "PARCEL_CONSOLIDATION_ENABLED": True,
                "PARCEL_CONSOLIDATION_WALL_TIME_S": 1},
    )
    sc.spaces = [StockSpace("RAW", "TEST", "raw", "open", 100, 50, "G"),
                 StockSpace("PRODUCT", "TEST", "product", "open", 100, 0, "G")]
    sc.machines = [Machine("ST", "TEST", "stacker", 0, 50,
                           reachable_spaces=["PRODUCT"])]
    sc.nodes = [Node("SPACE:RAW", "TEST", "space", "Raw"),
                Node("SPACE:PRODUCT", "TEST", "space", "Product")]
    sc.links = [Link("RAW-PRODUCT", "SPACE:RAW", "SPACE:PRODUCT", 50)]
    sc.process_transfers = [ProcessTransfer(
        "STACK", "ST", "RAW", "PRODUCT", ["RAW-PRODUCT"],
        operation_kind="stack", process_stage="STACK", output_grade="G")]
    sc.config["CAMPAIGN_POLICY"] = default_campaign_policy_config()
    if raise_error:
        def fail(*args, **kwargs):
            raise RuntimeError("injected consolidation failure")
        monkeypatch.setattr(
            "mph_sim.parcel_consolidation.consolidate_parcels", fail)
    result = solve_plan(sc)
    assert result.status == "OPTIMAL"
    assert sum(j.tonnage_t for j in result.jobs) == 1
    assert result.breakdown["parcel_consolidation_accepted_windows"] == 0
    assert result.breakdown["below_parcel_floor_slices"] == 1
    assert result.objective > 1, "the secondary objective is just one flag"
    assert result.solver_best_bound == result.objective
    assert result.solver_relative_gap_pct == 0
    if raise_error:
        assert any("ERROR/RuntimeError" in r for r in result.reasons)


@pytest.mark.parametrize("key,value", [
    ("PARCEL_CONSOLIDATION_ENABLED", "yes"),
    ("PARCEL_CONSOLIDATION_WALL_TIME_S", math.nan),
    ("PARCEL_CONSOLIDATION_WALL_TIME_S", True),
    ("PARCEL_CONSOLIDATION_DETERMINISTIC_TIME_S", 0),
])
def test_invalid_consolidation_configuration_is_rejected(key, value):
    sc = Scenario("bad consolidation", weights=ObjectiveWeights(),
                  config={key: value})
    assert any(key in error for error in operational_objective_errors(sc))
