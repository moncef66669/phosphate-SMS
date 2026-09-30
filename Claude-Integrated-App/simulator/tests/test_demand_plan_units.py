"""Focused tests for the concise S0 demand plan by receiving unit."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mph_sim import datasets  # noqa: E402
from mph_sim.domain import DemandEntry  # noqa: E402
from mph_sim.flow_config import (set_destination_line_enabled,  # noqa: E402
                                 destination_line_operating_state,
                                 set_destination_line_operating_state,
                                 set_process_line_enabled)
from mph_sim.persistence import scenario_from_json, scenario_to_json  # noqa: E402
from mph_sim.validation import validate_scenario  # noqa: E402
from ui.pages_data import (  # noqa: E402
    _demand_entries_for_quantities,
    _demand_plan_rows,
    _demand_quantity_t,
    _scaled_demand_rates,
)


def test_s0_demand_plan_has_one_grade_and_quantity_row_per_unit():
    scenario = datasets.end_to_end()

    rows = _demand_plan_rows(scenario)
    assert len(rows) == len(scenario.destinations)
    assert {row["Receiving unit"] for row in rows} == {
        destination.id for destination in scenario.destinations
    }
    assert len({row["Receiving unit"] for row in rows}) == len(rows)

    by_unit = {row["Receiving unit"]: row for row in rows}
    assert by_unit["PAP-1"]["Requested grade"] == "G10"
    assert by_unit["PAP-1"]["Quantity [t]"] == pytest.approx(252_000)
    assert by_unit["PAP-1"]["Average rate [t/h]"] == pytest.approx(1_500)
    assert by_unit["DRY-FEED"]["Requested grade"] == "TBT"
    assert by_unit["DRY-FEED"]["Quantity [t]"] == pytest.approx(134_400)
    assert by_unit["DRY-FEED"]["Average rate [t/h]"] == pytest.approx(800)
    assert by_unit["PAP-1"]["Requirement"] == "Final delivery"
    assert by_unit["PAP-1"]["Route"] == "Modelled"
    assert by_unit["PAP-2"]["Requested grade"] == "G10"
    assert by_unit["PAP-2"]["Quantity [t]"] == pytest.approx(252_000)
    assert by_unit["PAP-2"]["Average rate [t/h]"] == pytest.approx(1_500)
    assert by_unit["PAP-2"]["Route"] == "Future"
    assert sum(
        scenario.demand_tph("PAP-2", bucket) * scenario.sim.dt_h
        for bucket in range(scenario.sim.horizon_buckets)
    ) == pytest.approx(0)

    assert by_unit["BG-TRAIN-PROD"]["Requirement"] == "Final delivery"
    assert by_unit["BG-TRAIN-PROD"]["Requested grade"] == "TBT"
    assert by_unit["BG-TRAIN-PROD"]["Quantity [t]"] == pytest.approx(168_000)
    assert by_unit["BG-TRAIN-PROD"]["Average rate [t/h]"] == \
        pytest.approx(1_000)

    assert by_unit["LOU-WASH-L1"]["Requirement"] == "Washing feed"
    assert by_unit["LOU-WASH-L1"]["Requested grade"] == "RAW-BT"
    assert by_unit["LOU-WASH-L1"]["Quantity [t]"] == pytest.approx(5_864)
    assert by_unit["LOU-WASH-L1"]["Route"] == "Modelled"

    assert by_unit["LOU-WASH-L2"]["Requirement"] == "Washing feed"
    assert by_unit["LOU-WASH-L2"]["Requested grade"] == "RAW-BT-LOW-CD"
    assert by_unit["LOU-WASH-L2"]["Quantity [t]"] == pytest.approx(5_908)
    assert by_unit["LOU-WASH-L2"]["Route"] == "Modelled"

    for line_id, grade_id, quantity_t in (
            ("BEN-WASH-L1", "RAW-TBT", 8_085),
            ("BEN-WASH-L2", "RAW-BT-LOW-CD", 7_605),
            ("BEN-WASH-L3", "RAW-TBT", 8_085)):
        assert by_unit[line_id]["Requirement"] == "Washing feed"
        assert by_unit[line_id]["Requested grade"] == grade_id
        assert by_unit[line_id]["Quantity [t]"] == pytest.approx(quantity_t)
        assert by_unit[line_id]["Route"] == "Modelled"

    assert _demand_quantity_t(scenario, "PAP-1") == pytest.approx(252_000)
    assert _demand_quantity_t(scenario, "DRY-FEED") == pytest.approx(134_400)
    assert _demand_quantity_t(
        scenario, "LOU-WASH-L1") == pytest.approx(5_864)
    assert _demand_quantity_t(
        scenario, "LOU-WASH-L2") == pytest.approx(5_908)
    assert _demand_quantity_t(
        scenario, "BEN-WASH-L1") == pytest.approx(8_085)


def test_scaling_preserves_a_nonzero_hourly_profile_shape():
    scenario = datasets.end_to_end()
    scenario.sim.horizon_buckets = 4
    scenario.sim.dt_h = 0.5
    destination = scenario.destination("PAP-1")
    destination.demand_default_tph = 0
    scenario.demand = [
        DemandEntry("PAP-1", bucket, rate)
        for bucket, rate in enumerate((100.0, 200.0, 300.0, 400.0))
    ]

    assert _demand_quantity_t(scenario, "PAP-1") == pytest.approx(500)
    assert _scaled_demand_rates(
        scenario, "PAP-1", 1_000
    ) == pytest.approx([200, 400, 600, 800])

    entries = _demand_entries_for_quantities(
        scenario, {"PAP-1": 1_000}
    )
    pap_entries = sorted(
        (entry for entry in entries if entry.dest_id == "PAP-1"),
        key=lambda entry: entry.bucket,
    )
    assert [entry.bucket for entry in pap_entries] == [0, 1, 2, 3]
    assert [entry.rate_tph for entry in pap_entries] == pytest.approx(
        [200, 400, 600, 800]
    )
    assert sum(
        entry.rate_tph * scenario.sim.dt_h for entry in pap_entries
    ) == pytest.approx(1_000)


def test_new_quantity_on_a_zero_profile_is_distributed_flat():
    scenario = datasets.end_to_end()
    destination = scenario.destination("BG-TRAIN-RAW")
    assert destination.demand_default_tph == 0
    assert _demand_quantity_t(scenario, destination.id) == pytest.approx(0)

    rates = _scaled_demand_rates(scenario, destination.id, 2_400)
    hourly_rate = 2_400 / scenario.sim.horizon_buckets
    assert rates == pytest.approx(
        [hourly_rate] * scenario.sim.horizon_buckets)

    entries = _demand_entries_for_quantities(
        scenario, {destination.id: 2_400}
    )
    train_entries = [
        entry for entry in entries if entry.dest_id == destination.id
    ]
    assert len(train_entries) == scenario.sim.horizon_buckets
    assert [entry.bucket for entry in train_entries] == list(
        range(scenario.sim.horizon_buckets))
    assert [entry.rate_tph for entry in train_entries] == pytest.approx(
        [hourly_rate] * scenario.sim.horizon_buckets
    )


def test_disabled_washing_line_keeps_demand_and_is_labelled_disabled():
    scenario = datasets.end_to_end()
    quantity_before = _demand_quantity_t(scenario, "BEN-WASH-L2")

    set_process_line_enabled(scenario, "BG-WASH-L2", False)

    row = next(
        item for item in _demand_plan_rows(scenario)
        if item["Receiving unit"] == "BEN-WASH-L2"
    )
    assert row["Route"] == "Disabled"
    assert row["Quantity [t]"] == pytest.approx(quantity_before)


def test_planning_profiles_are_explicit_complete_demand_bundles():
    scenario = datasets.end_to_end()
    scenario.demand = [DemandEntry("PAP-1", 0, 1.0)]
    set_destination_line_enabled(scenario, "PAP-1", False)

    expected = {
        "BALANCED": (0.0, 0.0, 8_000.0, True),
        "BG_TRAIN": (84_000.0, 0.0, 0.0, False),
        "FULL_FLOW": (168_000.0, 74_200.0, 8_000.0, True),
        "BLEND_SHOWCASE": (168_000.0, 74_200.0, 8_000.0, True),
    }
    for profile_id, (
            train_t, c2_t, c1_t, louta_on) in expected.items():
        datasets.apply_planning_demand_profile(scenario, profile_id)
        assert len(scenario.demand) == (
            len(scenario.destinations) * scenario.sim.horizon_buckets)
        assert _demand_quantity_t(
            scenario, "BG-TRAIN-PROD") == pytest.approx(train_t)
        assert _demand_quantity_t(
            scenario, "PAP-1") == pytest.approx(252_000)
        assert _demand_quantity_t(
            scenario, "PAP-2") == pytest.approx(252_000)
        assert destination_line_operating_state(
            scenario, "PAP-2") == "FUTURE"
        assert all(
            not scenario.link(link_id).available
            for link_id in ("L-RTE-A-PAP", "L-RTE-B-PAP")
        )
        rows = {
            row["Receiving unit"]: row
            for row in _demand_plan_rows(scenario)
        }
        assert rows["PAP-1"]["Route"] == "Disabled"
        assert scenario.config["CORRIDOR_DISPATCH_TARGET_T"]["C2"] == \
            pytest.approx(c2_t)
        assert scenario.config["CORRIDOR_DISPATCH_TARGET_T"]["C1"] == \
            pytest.approx(c1_t)
        assert scenario.config["LOU_S0_STOCKED_ROUTE_ENABLED"] is louta_on
        assert scenario.config["ACTIVE_PLANNING_DEMAND_PROFILE"] == profile_id
        assert not [
            issue for issue in validate_scenario(scenario)
            if issue.severity == "ERROR"
        ]

    assert list(datasets.PLANNING_DEMAND_PROFILES) == [
        "BALANCED", "BG_TRAIN", "FULL_FLOW", "BLEND_SHOWCASE"]
    assert datasets.PLANNING_DEMAND_PROFILES["FULL_FLOW"]["label"] == \
        "Full-flow all-machine demonstration"


@pytest.mark.parametrize("bad_quantity", [-1, float("-inf"), float("nan")])
def test_invalid_or_negative_quantity_is_rejected(bad_quantity):
    scenario = datasets.end_to_end()

    with pytest.raises(ValueError, match="non-negative"):
        _scaled_demand_rates(scenario, "PAP-1", bad_quantity)
    with pytest.raises(ValueError, match="non-negative"):
        _demand_entries_for_quantities(
            scenario, {"PAP-1": bad_quantity}
        )


def test_demand_contract_survives_json_round_trip_and_old_json_defaults():
    scenario = datasets.end_to_end()
    scenario.destination("PAP-1").requested_grade = "BT"
    set_destination_line_enabled(scenario, "PAP-1", False)

    restored = scenario_from_json(scenario_to_json(scenario))
    assert restored.destination("PAP-1").requested_grade == "BT"
    assert _demand_quantity_t(restored, "PAP-1") == pytest.approx(252_000)
    assert all(
        not restored.link(link_id).available
        for link_id in ("L-RTE-A-PAP", "L-RTE-B-PAP")
    )
    assert {
        row["Receiving unit"]: row
        for row in _demand_plan_rows(restored)
    }["PAP-1"]["Route"] == "Disabled"
    assert restored.destination("DRY-FEED").requested_grade == "TBT"
    assert destination_line_operating_state(restored, "PAP-2") == "FUTURE"
    assert _demand_quantity_t(restored, "PAP-2") == pytest.approx(252_000)
    assert sum(
        restored.demand_tph("PAP-2", bucket) * restored.sim.dt_h
        for bucket in range(restored.sim.horizon_buckets)
    ) == pytest.approx(0)
    set_destination_line_operating_state(restored, "PAP-2", "AVAILABLE")
    assert sum(
        restored.demand_tph("PAP-2", bucket) * restored.sim.dt_h
        for bucket in range(restored.sim.horizon_buckets)
    ) == pytest.approx(252_000)
    assert restored.destination("LOU-WASH-L1").demand_kind == "process_feed"
    assert restored.destination("LOU-WASH-L1").process_stage == "LOU-WASH-L1"
    assert restored.destination("LOU-WASH-L2").demand_kind == "process_feed"
    assert restored.destination("LOU-WASH-L2").process_stage == "LOU-WASH-L2"
    assert restored.destination("BEN-WASH-L1").demand_kind == "process_feed"
    assert restored.destination("BEN-WASH-L1").process_stage == "BEN-WASH-L1"

    legacy_payload = json.loads(scenario_to_json(scenario))
    for destination in legacy_payload["destinations"]:
        destination.pop("requested_grade", None)
        destination.pop("demand_kind", None)
        destination.pop("process_stage", None)
    legacy = scenario_from_json(json.dumps(legacy_payload))
    assert all(
        destination.requested_grade == ""
        and destination.demand_kind == "delivery"
        and destination.process_stage == ""
        for destination in legacy.destinations
    )
