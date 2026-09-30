"""Dated priorities survive editing, persistence and resolution changes."""
from copy import deepcopy
import json

import pytest

from mph_sim import datasets
from mph_sim.domain import DemandEntry
from mph_sim.persistence import scenario_from_json, scenario_to_json
from mph_sim.optimizer import SolveOptions, solve_plan
from mph_sim.planning_time import resample_scenario_window
from mph_sim.scenarios import make_business_case_scenario
from mph_sim.validation import validate_scenario
from ui.pages_data import _daily_demand_rows_by_site, _weekly_campaign_entries


def _rows(scenario):
    return [row for _, _, _, group in _daily_demand_rows_by_site(scenario) for row in group]


def _grade_map(scenario):
    return {**{grade.id: grade.id for grade in scenario.grades}, "—": ""}


def test_priority_override_inheritance_and_legacy_json():
    scenario = datasets.end_to_end()
    scenario.destination("PAP-1").priority = 3
    assert scenario.demand_priority("PAP-1", 0) == 3
    scenario.demand.append(DemandEntry("PAP-1", 0, 1_500, "G10", priority=7))
    assert scenario.demand_priority("PAP-1", 0) == 7
    scenario.demand.append(DemandEntry("PAP-1", 0, 1_500, "G10", priority=None))
    assert scenario.demand_priority("PAP-1", 0) == 3
    payload = json.loads(scenario_to_json(scenario))
    for entry in payload["demand"]:
        entry.pop("priority", None)
    restored = scenario_from_json(json.dumps(payload))
    assert restored.demand_priority("PAP-1", 0) == 3


def test_same_grade_different_priorities_remain_separate_dated_campaigns():
    scenario = datasets.end_to_end()
    for entry in scenario.demand:
        if entry.dest_id == "PAP-1" and entry.bucket < 24:
            entry.priority = 2 if entry.bucket < 12 else 9
    first_date = scenario.sim.bucket_dt(0).date().isoformat()
    rows = _rows(scenario)
    campaigns = [row for row in rows if row["Date"] == first_date and row["Receiving unit"] == "PAP-1"]
    assert [(row["Start hour"], row["End hour"], row["Priority"]) for row in campaigns] == [(0, 12, 2), (12, 24, 9)]
    scenario.demand = _weekly_campaign_entries(scenario, rows, _grade_map(scenario))
    restored = scenario_from_json(scenario_to_json(scenario))
    assert [restored.demand_priority("PAP-1", bucket) for bucket in range(24)] == [2] * 12 + [9] * 12


@pytest.mark.parametrize("priority", [0, -1, 1.5, float("nan"), float("inf"), True, "bad"])
def test_invalid_campaign_priority_rejected_by_import_validation_and_editor(priority):
    scenario = datasets.end_to_end()
    rows = _rows(scenario)
    rows[0]["Priority"] = priority
    if not isinstance(priority, float) or priority == priority:
        with pytest.raises(ValueError, match="priority"):
            _weekly_campaign_entries(scenario, rows, _grade_map(scenario))
    # Blank cells (NaN) deliberately inherit in the editor, while a literal
    # NaN stored in a model entry is invalid imported data.
    scenario.demand[0].priority = priority
    assert any(issue.code == "BAD_DEMAND_PRIORITY" for issue in validate_scenario(scenario))


def test_overlapping_priorities_cannot_silently_overwrite_one_bucket():
    scenario = datasets.end_to_end()
    rows = _rows(scenario)
    duplicate = deepcopy(next(row for row in rows if row["Receiving unit"] == "PAP-1"))
    duplicate["Priority"] = 9
    rows.append(duplicate)
    with pytest.raises(ValueError, match="overlap"):
        _weekly_campaign_entries(scenario, rows, _grade_map(scenario))


def test_resampling_and_peak_sensitivity_preserve_effective_campaign_priority():
    scenario = datasets.end_to_end()
    for entry in scenario.demand:
        if entry.dest_id == "PAP-1" and entry.bucket < 12:
            entry.priority = 7
    fine = resample_scenario_window(scenario, window_start_minute=0,
                                   window_duration_min=1_440, step_min=15)
    assert [fine.demand_priority("PAP-1", bucket) for bucket in range(96)] == [7] * 48 + [scenario.destination("PAP-1").priority] * 48
    peak = make_business_case_scenario(scenario, "PEAK_DEMAND")
    assert all(peak.demand_priority("PAP-1", bucket) == scenario.demand_priority("PAP-1", bucket) for bucket in range(168))


@pytest.mark.parametrize("preferred", ["WASH-1-FEED", "OTHER-FEED"])
def test_reversing_campaign_priority_reverses_scarce_material_allocation(preferred):
    from test_process_feed_demand import _tiny_wash_scenario
    scenario = _tiny_wash_scenario()
    scenario.space("RAW-PILE").mass_t = 100
    other_machine = deepcopy(scenario.machines[0])
    other_machine.id = "OTHER-MACHINE"
    scenario.machines.append(other_machine)
    other_transfer = deepcopy(scenario.process_transfers[0])
    other_transfer.id, other_transfer.machine = "OTHER-TRANSFER", other_machine.id
    other_transfer.process_stage = "OTHER-WASH"
    scenario.process_transfers.append(other_transfer)
    other_destination = deepcopy(scenario.destinations[0])
    other_destination.id, other_destination.process_stage = "OTHER-FEED", "OTHER-WASH"
    scenario.destinations.append(other_destination)
    scenario.demand = [DemandEntry(destination.id, 0, 100,
                                  priority=9 if destination.id == preferred else 1)
                       for destination in scenario.destinations]
    assert {destination.priority for destination in scenario.destinations} == {1}
    plan = solve_plan(scenario, SolveOptions(max_time_s=0.3))
    assert plan.status in {"FEASIBLE", "OPTIMAL"}
    assert sum(plan.delivered[preferred]) == pytest.approx(100)
    for destination in scenario.destinations:
        if destination.id != preferred:
            assert sum(plan.delivered[destination.id]) == 0
