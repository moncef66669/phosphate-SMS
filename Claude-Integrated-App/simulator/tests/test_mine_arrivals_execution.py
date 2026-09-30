"""Physical mine-supply accounting at finite receiving buffers."""
import copy

import pytest

from mph_sim import datasets, mine_arrivals, washing
from mph_sim.domain import (ExogenousFlow, Grade, PlanResult, Scenario,
                            SimParams, StockSpace)
from mph_sim.simulator import Simulator


def _case(*, opening=60.0, capacity=100.0, typed=False, reverse=False,
          horizon=1, step_h=1.0):
    sc = Scenario("mine execution", sim=SimParams(
        horizon_buckets=horizon, dt_h=step_h))
    sc.grades = [
        Grade("RAW-A", "Raw A", {"BPL": 50.0, "moisture": 10.0},
              material_class="raw_screened"),
        Grade("RAW-B", "Raw B", {"BPL": 70.0, "moisture": 20.0},
              material_class="raw_screened"),
    ]
    sc.spaces = [StockSpace(
        "INLET", "MINE", "mine-yard", "open", capacity, opening,
        "RAW-A", material_class="raw_screened")]
    sc.exogenous = [
        ExogenousFlow("MINE-ARRIVAL:MINE:1:RAW-A:0", "INLET", 60.0,
                       "RAW-A", end_bucket=horizon - 1),
        ExogenousFlow("MINE-ARRIVAL:MINE:1:RAW-B:0", "INLET", 20.0,
                       "RAW-B", end_bucket=horizon - 1),
    ]
    if reverse:
        sc.exogenous.reverse()
    plan = PlanResult("FEASIBLE")
    if typed:
        record = dict(grade_id="RAW-A", quality=dict(sc.grades[0].quality),
                      material_class="raw_screened", treatment_id="",
                      source_grade_id="RAW-A")
        mid = washing._register(sc, plan.material_catalog, record)
        plan.material_inventory = {"INLET": {mid: [opening]}}
    return sc, plan


def _execute(sc, plan, legacy=False):
    sim = Simulator(sc, plan, auto_replan=False)
    if legacy:
        while sim.bucket < sim.T:
            sim._step_bucket_legacy()
    else:
        sim.run_to_end()
    return sim


def _totals(sim, grade):
    rows = [row for row in sim.actual_mine_arrivals
            if row["grade_id"] == grade]
    return {key: sum(row[key] for row in rows)
            for key in ("scheduled_t", "accepted_t", "rejected_t")}


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("typed", [False, True])
def test_capacity_shares_preserve_mix_and_close_physical_balance(legacy, typed):
    sc, plan = _case(typed=typed)
    sim = _execute(sc, plan, legacy)

    assert _totals(sim, "RAW-A") == pytest.approx(dict(
        scheduled_t=60.0, accepted_t=30.0, rejected_t=30.0))
    assert _totals(sim, "RAW-B") == pytest.approx(dict(
        scheduled_t=20.0, accepted_t=10.0, rejected_t=10.0))
    assert sim.inv["INLET"] == 100.0
    assert sim.inv["INLET"] - sc.space("INLET").mass_t == pytest.approx(
        sum(row["accepted_t"] for row in sim.actual_mine_arrivals))
    for row in sim.actual_mine_arrivals:
        assert row["scheduled_t"] == pytest.approx(
            row["accepted_t"] + row["rejected_t"])
        assert row["mass_basis"] == "physical_t"
        assert row["bucket"] == 0 and row["day"] == 1
    assert len([record for record in sim.log if record.kind == "WARNING"]) == 2
    # BPL is on dry solids: 90 physical tonnes at 10% moisture and
    # 10 physical tonnes at 20% moisture give 81 and 8 dry tonnes.
    assert sim.space_quality["INLET"]["BPL"] == pytest.approx(
        (81 * 50 + 8 * 70) / 89)
    if typed:
        assert sum(sim.material_mass["INLET"].values()) == pytest.approx(100.0)
        by_grade = {
            sim.material_catalog[mid]["grade_id"]: mass
            for mid, mass in sim.material_mass["INLET"].items()}
        assert by_grade == pytest.approx({"RAW-A": 90.0, "RAW-B": 10.0})


@pytest.mark.parametrize("legacy", [False, True])
def test_arrival_order_cannot_change_accepted_grades_or_quality(legacy):
    first = _execute(*_case(typed=True), legacy)
    second = _execute(*_case(typed=True, reverse=True), legacy)
    assert first.actual_mine_arrivals == second.actual_mine_arrivals
    assert first.material_mass == second.material_mass
    assert first.space_quality == second.space_quality


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("opening", [100.0, 101.0])
def test_full_buffer_rejects_supply_without_resizing_or_deleting_stock(legacy, opening):
    sc, plan = _case(opening=opening, typed=True, horizon=2)
    before = copy.deepcopy(sc.spaces)
    sim = _execute(sc, plan, legacy)
    assert sim.inv["INLET"] == opening
    assert sim.sc.spaces == before == sc.spaces
    assert sum(sim.material_mass["INLET"].values()) == pytest.approx(opening)
    assert _totals(sim, "RAW-A") == pytest.approx(dict(
        scheduled_t=120.0, accepted_t=0.0, rejected_t=120.0))
    assert _totals(sim, "RAW-B") == pytest.approx(dict(
        scheduled_t=40.0, accepted_t=0.0, rejected_t=40.0))
    assert {row["bucket"] for row in sim.actual_mine_arrivals} == {0, 1}


def test_partial_minute_advances_account_for_each_physical_tonne_once():
    sc, plan = _case(opening=60.25, typed=True)
    sim = Simulator(sc, plan, auto_replan=False)
    sim.advance_minutes(10)
    sim.advance_minutes(20)
    sim.advance_minutes(30)
    assert sim.current_minute == 60
    assert len(sim.actual_mine_arrivals) == 6
    assert sim.inv["INLET"] == 100.0
    assert sum(row["scheduled_t"] for row in sim.actual_mine_arrivals) == pytest.approx(80)
    assert sum(row["accepted_t"] for row in sim.actual_mine_arrivals) == pytest.approx(39.75)
    assert sum(row["rejected_t"] for row in sim.actual_mine_arrivals) == pytest.approx(40.25)
    assert _totals(sim, "RAW-A")["accepted_t"] == pytest.approx(39.75 * .75)
    assert sum(sim.material_mass["INLET"].values()) == pytest.approx(100.0)


@pytest.mark.parametrize("legacy", [False, True])
def test_receiving_buffers_have_independent_capacity_limits(legacy):
    sc, plan = _case(opening=95.0)
    sc.spaces.append(StockSpace("SECOND", "MINE", "mine-yard", "open",
                                200.0, 0.0, "RAW-B",
                                material_class="raw_screened"))
    sc.exogenous[1].target_space = "SECOND"
    sim = _execute(sc, plan, legacy)
    assert sim.inv == {"INLET": 100.0, "SECOND": 20.0}
    assert _totals(sim, "RAW-A")["accepted_t"] == 5.0
    assert _totals(sim, "RAW-B")["rejected_t"] == 0.0


@pytest.mark.parametrize("legacy", [False, True])
def test_subhourly_arrivals_use_physical_tph_conversion(legacy):
    sc, plan = _case(opening=0.0, step_h=.25, horizon=4, typed=True)
    sim = _execute(sc, plan, legacy)
    assert sim.inv["INLET"] == 80.0
    assert _totals(sim, "RAW-A")["scheduled_t"] == 60.0
    assert _totals(sim, "RAW-B")["scheduled_t"] == 20.0
    assert not sim.log
    assert sim.actual_mine_arrivals[-1]["end_minute"] == 60


def test_ledger_retains_generated_civil_day_for_midnight_spanning_bucket():
    sc, plan = _case(opening=0.0)
    sc.sim.start_iso = "2026-07-13T23:30:00"
    sc.exogenous[1].id = "MINE-ARRIVAL:MINE:2:RAW-B:0"
    sim = _execute(sc, plan)
    assert {row["grade_id"]: row["day"] for row in sim.actual_mine_arrivals} == {
        "RAW-A": 1, "RAW-B": 2}


@pytest.mark.parametrize("legacy", [False, True])
def test_unmanaged_exogenous_flows_keep_their_existing_allocation(legacy):
    sc, plan = _case()
    for flow in sc.exogenous:
        flow.id = flow.id.replace("MINE-ARRIVAL:", "PAYLOADER:")
    sim = _execute(sc, plan, legacy)
    assert sim.inv["INLET"] == 100.0
    assert sim.space_quality["INLET"]["BPL"] == 50.0
    assert not sim.actual_mine_arrivals


@pytest.mark.parametrize("legacy", [False, True])
def test_typed_pool_mismatch_fails_before_mine_arrival_changes_inventory(legacy):
    sc, plan = _case(typed=True)
    sim = Simulator(sc, plan, auto_replan=False)
    mid = next(iter(sim.material_mass["INLET"]))
    sim.material_mass["INLET"][mid] -= 1
    before = copy.deepcopy(sim.material_mass)
    with pytest.raises(ValueError, match="material accounting differs"):
        sim._step_bucket_legacy() if legacy else sim.step()
    assert sim.inv["INLET"] == 60.0
    assert sim.material_mass == before
    assert not sim.actual_mine_arrivals


def test_managed_arrivals_cannot_drain_an_internal_donor():
    sc, plan = _case()
    sc.exogenous[0].source_space = "INLET"
    sim = Simulator(sc, plan, auto_replan=False)
    with pytest.raises(ValueError, match="external supply"):
        sim.step()
    assert sim.inv["INLET"] == 60.0


@pytest.mark.parametrize("invalid_rate", [-1.0, float("nan"), float("inf")])
def test_invalid_managed_rate_fails_before_inventory_changes(invalid_rate):
    sc, plan = _case()
    sc.exogenous[0].rate_tph = invalid_rate
    sim = Simulator(sc, plan, auto_replan=False)
    with pytest.raises(ValueError, match="finite and nonnegative"):
        sim.step()
    assert sim.inv["INLET"] == 60.0


def test_configuration_refresh_errors_surface_at_simulator_construction():
    sc, plan = _case()
    sc.config[mine_arrivals.CONFIG_KEY] = {"version": -1}
    with pytest.raises(ValueError, match="Mine arrivals require"):
        Simulator(sc, plan, auto_replan=False)


def test_configuration_refreshes_only_simulators_copied_full_horizon():
    sc = datasets.end_to_end()
    sc.sim.horizon_buckets = 24
    sc.config[washing.CONFIG_KEY] = {**washing.defaults(), "enabled": True}
    mine_arrivals.install_defaults(sc)
    before = copy.deepcopy(sc.exogenous)
    settings = sc.config[mine_arrivals.CONFIG_KEY]
    row = next(row for row in settings["days"]
               if row["day"] == 1 and row["site"] == "BENGUERIR")
    row["volumes_t"]["RAW-TBT"] += 240
    sim = Simulator(sc, PlanResult("FEASIBLE"), auto_replan=False)
    generated_t = sum(flow.rate_tph * sc.sim.dt_h
                      * (flow.end_bucket - flow.start_bucket + 1)
                      for flow in sim.sc.exogenous
                      if flow.id.startswith("MINE-ARRIVAL:BENGUERIR:1:RAW-TBT:"))
    assert generated_t == row["volumes_t"]["RAW-TBT"]
    assert sim.sc.exogenous != before
    assert sc.exogenous == before
    assert sim.sc.spaces == sc.spaces


def test_builtin_full_source_buffers_explicitly_reject_first_bucket_supply():
    sc = datasets.end_to_end()
    sc.sim.horizon_buckets = 1
    sc.config[washing.CONFIG_KEY] = {**washing.defaults(), "enabled": True}
    mine_arrivals.install_defaults(sc)
    prepared, _ = washing.prepare_materials(sc, [])
    plan = PlanResult(
        "FEASIBLE", material_catalog=prepared.config["_WASHING_MODEL_CATALOG"],
        material_inventory={sid: {mid: [mass] for mid, mass in pools.items()}
                            for sid, pools in prepared.config["_WASHING_MODEL_INITIAL"].items()})
    sim = _execute(sc, plan)
    assert sim.actual_mine_arrivals
    assert sum(row["scheduled_t"] for row in sim.actual_mine_arrivals) == 1500
    assert sum(row["accepted_t"] for row in sim.actual_mine_arrivals) == 0
    assert sum(row["rejected_t"] for row in sim.actual_mine_arrivals) == 1500
    for source_ids in mine_arrivals.SITE_SOURCE_SPACES.values():
        for sid in source_ids:
            assert sim.inv[sid] == sc.space(sid).mass_t == sc.space(sid).capacity_t
            assert sum(sim.material_mass[sid].values()) == pytest.approx(sim.inv[sid])
