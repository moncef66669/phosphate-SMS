"""End-to-end assay propagation across ordinary physical inventory flows."""
import pytest

from mph_sim.domain import (BeltProfileEntry, ExogenousFlow, Job, PlanResult,
                            SpecRange, UNKNOWN_STOCK_QUALITY_KEY, UNKNOWN_BELT_QUALITY_KEY)
from mph_sim.blending import chevron_blended_quality
from mph_sim.kpis import compute_kpis, planned_delivery_quality
from mph_sim.optimizer import SolveOptions, solve_plan
from mph_sim.persistence import load_scenario, save_scenario
from mph_sim.simulator import Simulator
from test_process_feed_demand import _tiny_wash_scenario
from test_simulator_chevron_quality import _stacking_scenario, _stack_jobs


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("initial_mass", [0, 100])
def test_exogenous_missing_assay_survives_mixing_and_empty_stock_is_ignored(
        legacy, initial_mass):
    scenario = _tiny_wash_scenario()
    scenario.space("RAW-PILE").quality = {"BPL": 80, "moisture": 30}
    scenario.space("WASHED-WIP").mass_t = initial_mass
    scenario.space("WASHED-WIP").quality = {"BPL": 60, "Cd": 30, "moisture": 10}
    scenario.exogenous = [ExogenousFlow(
        "TRANSFER", "WASHED-WIP", 100, "RAW-A", source_space="RAW-PILE")]
    replay = Simulator(scenario, PlanResult("FEASIBLE"), auto_replan=False)
    replay._step_bucket_legacy() if legacy else replay.run_to_end()

    quality = replay.space_quality["WASHED-WIP"]
    assert "Cd" not in quality
    assert quality["BPL"] == pytest.approx(68.75 if initial_mass else 80)
    assert quality["moisture"] == pytest.approx(20 if initial_mass else 30)
    assert replay.inv["WASHED-WIP"] == initial_mass + 100


@pytest.mark.parametrize("legacy", [False, True])
def test_process_output_keeps_transformed_grade_without_certifying_unknown_assay(legacy):
    scenario = _tiny_wash_scenario()
    scenario.space("RAW-PILE").quality = {"BPL": 20, "Cd": 90, "moisture": 10}
    scenario.grade("PRODUCT").quality = {"BPL": 80, "moisture": 30}
    scenario.space("WASHED-WIP").mass_t = 80
    scenario.space("WASHED-WIP").quality = {"BPL": 60, "Cd": 30, "moisture": 10}
    job = Job("PROCESS", "WASH-FEEDER", "reclaim", "RAW-PILE",
              grade="RAW-A", rate_tph=100, tonnage_t=100,
              path=["RAW-TO-WASH"], transfer_id="WASH-TRANSFER",
              source_space="RAW-PILE", target_space="WASHED-WIP",
              process_stage="WASH-1", output_grade="PRODUCT", output_tonnage_t=80)
    replay = Simulator(scenario, PlanResult("FEASIBLE", jobs=[job]), auto_replan=False)
    replay._step_bucket_legacy() if legacy else replay.run_to_end()

    assert replay.inv["WASHED-WIP"] == 160
    assert replay.space_quality["WASHED-WIP"]["BPL"] == pytest.approx(68.75)
    assert replay.space_quality["WASHED-WIP"]["moisture"] == pytest.approx(20)
    assert "Cd" not in replay.space_quality["WASHED-WIP"]


@pytest.mark.parametrize("method", ["direct", "chevron"])
def test_merged_parcels_cannot_reintroduce_an_unknown_assay_when_stacked(method):
    scenario = _stacking_scenario()
    scenario.belt_profile = [
        BeltProfileEntry("BELT", 0, 50, "A", {"BPL": 60, "Cd": 30, "moisture": 10}),
        BeltProfileEntry("BELT", 0, 50, "A", {"BPL": 80, "moisture": 30}),
    ]
    replay = Simulator(scenario, PlanResult("FEASIBLE", jobs=_stack_jobs(method)[:1]),
                       auto_replan=False)
    replay.run_to_end()

    assert replay.inv["PILE"] == 100
    assert "Cd" not in replay.space_quality["PILE"]
    assert replay.space_quality["PILE"]["BPL"] == pytest.approx(68.75)
    assert replay.space_quality["PILE"]["moisture"] == pytest.approx(20)


@pytest.mark.parametrize("unknown_first", [False, True])
def test_delivery_aggregation_preserves_unknown_and_uses_stage_moisture(unknown_first):
    scenario = _tiny_wash_scenario()
    scenario.grade("RAW-A").material_class = "raw_screened"
    scenario.grade("RAW-A").raw_moisture_pct = 10
    scenario.grade("RAW-A").product_moisture_pct = 80  # Must not use raw product profile.
    scenario.grade("PRODUCT").material_class = "washed"
    scenario.grade("PRODUCT").product_moisture_pct = 30
    replay = Simulator(scenario, PlanResult("FEASIBLE"), auto_replan=False)
    deliveries = [(100, {"BPL": 60, "Cd": 30}, "RAW-A"),
                  (100, {"BPL": 80}, "PRODUCT")]
    if unknown_first:
        deliveries.reverse()
    for mass, quality, grade in deliveries:
        replay._deliver("WASH-1-FEED", 0, mass, quality, grade)

    quality = replay.delivered_q["WASH-1-FEED"][0]
    assert "Cd" not in quality
    assert quality["BPL"] == pytest.approx(68.75)
    assert quality["moisture"] == pytest.approx(20)
    assert replay.delivered["WASH-1-FEED"][0] == 200


def test_legacy_unmeasured_moisture_retains_equal_mass_basis_without_zero_assays():
    replay = Simulator(_tiny_wash_scenario(), PlanResult("FEASIBLE"), auto_replan=False)
    replay._deliver("WASH-1-FEED", 0, 100, {"BPL": 60, "Cd": 30})
    replay._deliver("WASH-1-FEED", 0, 100, {"BPL": 80})
    assert replay.delivered_q["WASH-1-FEED"][0] == {"BPL": 70}


def test_nonfinite_assay_is_unknown_and_zero_mass_does_not_clear_delivery():
    replay = Simulator(_tiny_wash_scenario(), PlanResult("FEASIBLE"), auto_replan=False)
    replay._deliver("WASH-1-FEED", 0, 100, {"BPL": 60, "Cd": 30})
    replay._deliver("WASH-1-FEED", 0, 0, {})
    assert replay.delivered_q["WASH-1-FEED"][0]["Cd"] == 30
    replay._deliver("WASH-1-FEED", 0, 100, {"BPL": 80, "Cd": float("nan")})
    assert replay.delivered_q["WASH-1-FEED"][0] == {"BPL": 70}


def test_entirely_unknown_live_stock_survives_saved_restart_kpis_and_replan(tmp_path):
    scenario = _tiny_wash_scenario()
    scenario.grade("RAW-A").quality = {"BPL": 60, "Cd": 30}
    scenario.destination("WASH-1-FEED").spec = [SpecRange("Cd", pmax=40, hard=True)]
    replay = Simulator(scenario, PlanResult("FEASIBLE"), auto_replan=False)
    replay.space_quality["RAW-PILE"] = {}  # Live positive stock is now unassayed.
    path = tmp_path / "unknown-stock.json"
    save_scenario(replay.snapshot_scenario(), path)
    restored = load_scenario(path)

    assert restored.quality_of("RAW-PILE") == {}
    assert restored.config[UNKNOWN_STOCK_QUALITY_KEY] == ["RAW-PILE"]
    restarted = Simulator(restored, PlanResult("FEASIBLE"), auto_replan=False)
    assert restarted.space_quality["RAW-PILE"] == {}
    result = solve_plan(restored, SolveOptions(max_time_s=0.2))
    assert result.status in {"FEASIBLE", "OPTIMAL"}
    assert sum(result.delivered["WASH-1-FEED"]) == 0
    assert any("UNKNOWN_ASSAY" in reason for reason in result.reasons)

    # An imported delivery of that stock must remain unqualified in reporting.
    destination = restored.destination("WASH-1-FEED")
    destination.demand_kind = "delivery"
    job = Job("DELIVERY", "WASH-FEEDER", "reclaim", "RAW-PILE",
              dest_id=destination.id, grade="RAW-A", rate_tph=100, tonnage_t=100,
              path=["RAW-TO-WASH"])
    plan = PlanResult("FEASIBLE", jobs=[job], delivered={destination.id: [100]})
    assert planned_delivery_quality(restored, plan, destination, 0) == {}
    kpis = compute_kpis(restored, plan)
    assert kpis["quality_unknown_t"] == 100
    assert kpis["final_product_on_spec_on_time_t"] == 0


def test_legacy_empty_assay_still_uses_catalogue_and_live_reassay_clears_marker():
    scenario = _tiny_wash_scenario()
    scenario.grade("RAW-A").quality = {"BPL": 60, "Cd": 30}
    assert scenario.space("RAW-PILE").quality == {}
    assert scenario.quality_of("RAW-PILE") == {"BPL": 60, "Cd": 30}
    replay = Simulator(scenario, PlanResult("FEASIBLE"), auto_replan=False)
    replay.sc.config[UNKNOWN_STOCK_QUALITY_KEY] = ["RAW-PILE"]
    replay.space_quality["RAW-PILE"] = {"BPL": 65}
    snapshot = replay.snapshot_scenario()
    assert snapshot.config[UNKNOWN_STOCK_QUALITY_KEY] == []
    assert snapshot.quality_of("RAW-PILE") == {"BPL": 65}


def test_depleted_unknown_pile_does_not_poison_known_arrival_after_restart(tmp_path):
    scenario = _stacking_scenario()
    replay = Simulator(scenario, PlanResult("FEASIBLE"), auto_replan=False)
    replay.sc.config[UNKNOWN_STOCK_QUALITY_KEY] = ["PILE"]
    replay.inv["PILE"] = 0
    replay.space_quality["PILE"] = {}
    path = tmp_path / "empty-pile.json"
    save_scenario(replay.snapshot_scenario(), path)
    restored = load_scenario(path)
    assert restored.config[UNKNOWN_STOCK_QUALITY_KEY] == []
    restarted = Simulator(restored, PlanResult("FEASIBLE", jobs=_stack_jobs("direct")),
                          auto_replan=False)
    restarted.run_to_end()
    assert restarted.space_quality["PILE"]["BPL"] == pytest.approx(68.75)
    assert restarted.space_quality["PILE"]["Cd"] == pytest.approx(21.25)


def test_completed_campaign_cannot_restore_catalogue_assays_over_unknown_live_stock(tmp_path):
    scenario = _stacking_scenario(carried=True)
    replay = Simulator(scenario, PlanResult("FEASIBLE"), auto_replan=False)
    replay.space_quality["PILE"] = {}
    path = tmp_path / "unknown-complete-blend.json"
    save_scenario(replay.snapshot_scenario(), path)
    restored = load_scenario(path)
    assert chevron_blended_quality(restored, "PILE") == {}
    restarted = Simulator(restored, PlanResult("FEASIBLE"), auto_replan=False)
    assert restarted.space_quality["PILE"] == {}


def test_unknown_in_transit_parcel_survives_save_restart_and_stacking(tmp_path):
    scenario = _stacking_scenario()
    scenario.grade("A").product_moisture_pct = None
    replay = Simulator(scenario, PlanResult("FEASIBLE"), auto_replan=False)
    assert replay.belts[0].quality["BPL"] == 60  # Legacy declared-grade default.
    replay.belts[0].quality = {}
    path = tmp_path / "unknown-belt-parcel.json"
    save_scenario(replay.snapshot_scenario(), path)
    restored = load_scenario(path)
    assert restored.config[UNKNOWN_BELT_QUALITY_KEY] == [0]
    restarted = Simulator(restored, PlanResult("FEASIBLE", jobs=_stack_jobs("direct")[:1]),
                          auto_replan=False)
    assert restarted.belts[0].quality == {}
    assert restarted.belts[1].quality["BPL"] == 80
    restarted.step()
    assert restarted.inv["PILE"] == 100
    assert restarted.space_quality["PILE"] == {}
    assert restarted.snapshot_scenario().config[UNKNOWN_STOCK_QUALITY_KEY] == ["PILE"]
    assert restarted.snapshot_scenario().config[UNKNOWN_BELT_QUALITY_KEY] == []
