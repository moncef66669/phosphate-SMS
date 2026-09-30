"""Demand edits retain timed intent and commit only a fully staged scenario."""
from copy import deepcopy

import pytest

from mph_sim import datasets
from mph_sim.domain import DemandEntry, ExogenousFlow, Grade
from ui.pages_data import (
    _daily_demand_rows_by_site, _grade_references,
    _stage_weekly_demand_change, _weekly_campaign_entries,
)


def _rows(scenario):
    return [row for _, _, _, group in _daily_demand_rows_by_site(scenario) for row in group]


def _grades(scenario):
    return {**{grade.id: grade.id for grade in scenario.grades}, "—": ""}


def test_non_midnight_horizon_shows_actual_clock_hours_and_round_trips():
    scenario = datasets.end_to_end()
    scenario.sim.start_iso = "2026-09-08T06:00:00"
    rows = _rows(scenario)
    first = next(row for row in rows if row["Receiving unit"] == "PAP-1")
    assert first["Start hour"] == 6.0
    assert first["End hour"] == 24.0
    entries = _weekly_campaign_entries(scenario, rows, _grades(scenario))
    assert {(e.dest_id, e.bucket): (e.rate_tph, e.requested_grade) for e in entries} == {
        (e.dest_id, e.bucket): (e.rate_tph, e.requested_grade) for e in scenario.demand}
    first["Start hour"] = 0.0
    with pytest.raises(ValueError, match="require 6"):
        _weekly_campaign_entries(scenario, rows, _grades(scenario))


def test_reapplying_campaign_keeps_hourly_peaks_and_zero_hours():
    scenario = datasets.end_to_end()
    for entry in scenario.demand:
        if entry.dest_id == "PAP-1" and entry.bucket < 24:
            entry.rate_tph = 0.0 if entry.bucket < 12 else 3_000.0
    entries = _weekly_campaign_entries(scenario, _rows(scenario), _grades(scenario))
    assert [entry.rate_tph for entry in entries if entry.dest_id == "PAP-1" and entry.bucket < 24] == [0.0] * 12 + [3_000.0] * 12


def test_campaign_rejects_raw_grade_for_washed_destination():
    scenario = datasets.end_to_end()
    rows = _rows(scenario)
    next(row for row in rows if row["Receiving unit"] == "PAP-1")["Requested grade"] = "RAW-BT"
    with pytest.raises(ValueError, match="only accepts the G10"):
        _weekly_campaign_entries(scenario, rows, _grades(scenario))


def test_target_sync_failure_cannot_partially_commit_demand(monkeypatch):
    scenario = datasets.end_to_end()
    snapshot = deepcopy(scenario)
    rows = _rows(scenario)
    rows[0]["Campaign quantity [t]"] += 100
    def fail(staged, **kwargs):
        staged.config["PARTIAL_CHANGE"] = True
        raise ValueError("bad target")
    monkeypatch.setattr(datasets, "synchronize_weekly_demand_targets", fail)
    with pytest.raises(ValueError, match="bad target"):
        _stage_weekly_demand_change(scenario, rows, _grades(scenario))
    assert scenario == snapshot


def test_weekly_apply_preserves_c2_unless_operator_explicitly_resets_it():
    scenario = datasets.end_to_end()
    target = scenario.config["CORRIDOR_DISPATCH_TARGET_T"]["C2"]
    assert target > 0
    kept = _stage_weekly_demand_change(scenario, _rows(scenario), _grades(scenario))
    assert kept.config["CORRIDOR_DISPATCH_TARGET_T"]["C2"] == target
    reset = _stage_weekly_demand_change(
        scenario, _rows(scenario), _grades(scenario), preserve_c2_allocation=False)
    assert reset.config["CORRIDOR_DISPATCH_TARGET_T"]["C2"] == 0
    assert scenario.config["CORRIDOR_DISPATCH_TARGET_T"]["C2"] == target


def test_grade_deletion_guards_designated_external_and_blend_references():
    scenario = datasets.end_to_end()
    scenario.grades.append(Grade("SPECIAL", "Special", material_class="washed"))
    scenario.space("HP-01").designated_grade = "SPECIAL"
    scenario.exogenous.append(ExogenousFlow("CUSTOM-IN", "HP-01", 10.0, "SPECIAL"))
    scenario.config["CHEVRON_STACKING"]["result_grades"]["HP-02"] = "SPECIAL"
    refs = _grade_references(scenario, "SPECIAL")
    assert "stacking designation for HP-01" in refs
    assert "external flow CUSTOM-IN" in refs
    assert "result of blend HP-02" in refs
