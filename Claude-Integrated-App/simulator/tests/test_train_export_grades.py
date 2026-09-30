"""Train-export grades are demand-side receiving contracts, not stock IDs."""
from __future__ import annotations

import pytest

from mph_sim import datasets
from mph_sim.grade_catalog import apply_saved_grade_catalog, save_grade_catalog
from mph_sim.domain import (
    DemandEntry,
    Destination,
    Grade,
    Job,
    Link,
    Machine,
    Node,
    PlanResult,
    QualityParam,
    Scenario,
    SimParams,
    Site,
    SpecRange,
    StockSpace,
)
from mph_sim.kpis import compute_kpis
from mph_sim.optimizer import SolveOptions, solve_plan
from mph_sim.persistence import scenario_from_json, scenario_to_json
from mph_sim.simulator import Simulator
from mph_sim.validation import validate_scenario
from ui.pages_data import (_grade_display_maps, _train_export_spec_frame,
                           _weekly_campaign_entries)


def test_gantour_export_profiles_are_transcribed_as_hard_train_specs():
    scenario = datasets.end_to_end()
    assert tuple(grade_id for grade_id in datasets.TRAIN_EXPORT_GRADE_IDS) == (
        "BG-22", "BG10", "BG11", "BG-AD")

    bg22 = scenario.grade("BG-22")
    assert bg22.is_specification
    assert bg22.material_class == "washed"
    assert bg22.receiving_destinations == ["BG-TRAIN-PROD"]
    assert bg22.source_quality_ranges == {
        "P2O5": "31.1–32.0 %", "BPL": "68.0–70.0 %",
        "CO2": "5.0–6.50 %", "SiO2": "< 4.5 %",
        "H2O": "≤ 3.50 %", "MgO": "0.45–0.65 %",
        "Cd": "< 7 ppm",
    }
    assert all(spec.hard for spec in bg22.receiving_spec)
    assert {spec.param for spec in bg22.receiving_spec} == {
        "BPL", "CO2", "SiO2", "moisture", "MgO", "Cd"}
    assert "P2O5" not in {spec.param for spec in bg22.receiving_spec}

    bg11 = scenario.grade("BG11")
    assert {spec.param for spec in bg11.receiving_spec} == {
        "BPL", "SiO2", "SiO2_R", "F"}
    bg_ad = scenario.grade("BG-AD")
    assert {spec.param for spec in bg_ad.receiving_spec} == {
        "BPL", "CO2", "SiO2", "MgO", "Cd", "moisture", "P2O5_SC"}
    assert {param.name for param in scenario.quality_params} >= {
        "SiO2_R", "F", "P2O5_SC"}
    assert not [issue for issue in validate_scenario(scenario)
                if issue.severity == "ERROR"]


def test_train_export_table_uses_one_standard_wide_row_per_grade():
    frame = _train_export_spec_frame(datasets.end_to_end())

    assert frame["Grade ID"].tolist() == list(
        datasets.TRAIN_EXPORT_GRADE_IDS)
    assert frame["Grade ID"].is_unique
    assert list(frame.columns) == [
        "Grade ID", "Material state", "BPL [%]", "Cd [ppm]", "MgO [%]",
        "SiO2 [%]", "SiO2_R [%]", "F [%]", "P2O5_SC [%]", "CO2 [%]",
        "moisture [%]", "P2O5 [%]", "Destination", "Evidence",
    ]
    by_grade = frame.set_index("Grade ID")
    assert by_grade.loc["BG-22", "BPL [%]"] == "68.0\u201370.0"
    assert by_grade.loc["BG-22", "Cd [ppm]"] == "< 7"
    assert by_grade.loc["BG10", "P2O5 [%]"] == "29.7\u201330.9"
    assert by_grade.loc["BG11", "SiO2_R [%]"] == "2.8\u20133.6"
    assert by_grade.loc["BG-AD", "P2O5_SC [%]"] == "8.79\u20138.93"
    assert by_grade.loc["BG11", "CO2 [%]"] == ""
    assert set(frame["Destination"]) == {"BG-TRAIN-PROD"}


def _campaign_spec_scenario() -> Scenario:
    scenario = Scenario(
        "campaign specification",
        sites=[Site("X", "X", "stockyard")],
        grades=[
            Grade("SOURCE", "Physical source", {"BPL": 70.0},
                  material_class="washed"),
            Grade("PASS", "Passing contract", material_class="washed",
                  is_specification=True,
                  receiving_spec=[SpecRange("BPL", 69.0, 71.0, hard=True)],
                  receiving_destinations=["TRAIN"]),
            Grade("FAIL", "Failing contract", material_class="washed",
                  is_specification=True,
                  receiving_spec=[SpecRange("BPL", 72.0, 73.0, hard=True)],
                  receiving_destinations=["TRAIN"]),
        ],
        quality_params=[QualityParam("BPL", "%")],
        sim=SimParams(horizon_buckets=2, dt_h=1.0, max_solve_s=1.0),
        config={"FLOW_TONNE_PENALTY": 1},
    )
    scenario.spaces = [StockSpace(
        "PILE", "X", "yard", "open", 1_000.0, 500.0, "SOURCE",
        material_class="washed")]
    scenario.machines = [Machine(
        "RC", "X", "reclaimer", 0.0, 100.0,
        reachable_spaces=["PILE"])]
    scenario.nodes = [
        Node("SPACE:PILE", "X", "space", "Pile"),
        Node("DEST:TRAIN", "X", "dest", "Train"),
    ]
    scenario.links = [Link(
        "PILE-TRAIN", "SPACE:PILE", "DEST:TRAIN", 100.0)]
    scenario.destinations = [Destination(
        "TRAIN", "X", "DEST:TRAIN", requested_grade="SOURCE")]
    scenario.demand = [
        DemandEntry("TRAIN", 0, 100.0, requested_grade="PASS"),
        DemandEntry("TRAIN", 1, 100.0, requested_grade="FAIL"),
    ]
    return scenario


def test_campaign_grade_selects_quality_envelope_without_source_label_match():
    scenario = _campaign_spec_scenario()
    assert scenario.demand_spec("TRAIN", 0)[0].pmin == 69.0
    assert scenario.demand_spec("TRAIN", 1)[0].pmin == 72.0

    result = solve_plan(scenario, SolveOptions(max_time_s=1.0))
    assert result.status in ("OPTIMAL", "FEASIBLE", "PARTIAL")
    assert result.delivered["TRAIN"][0] == pytest.approx(100.0)
    assert result.delivered["TRAIN"][1] == pytest.approx(0.0)
    assert result.unmet["TRAIN"][1] == pytest.approx(100.0)


def test_execution_hold_uses_the_bucket_specific_export_contract():
    scenario = _campaign_spec_scenario()
    simulator = Simulator(
        scenario, PlanResult("FEASIBLE"), auto_replan=False)
    assert simulator._hard_delivery_quality_issue(
        "TRAIN", {"BPL": 70.0}, 0) == ""
    assert "outside hard receiving interval" in \
        simulator._hard_delivery_quality_issue(
            "TRAIN", {"BPL": 70.0}, 1)


def test_kpis_report_the_selected_export_contract_and_its_evidence():
    scenario = _campaign_spec_scenario()
    plan = PlanResult(
        "FEASIBLE",
        jobs=[Job(
            "J", "RC", "reclaim", "PILE", dest_id="TRAIN",
            grade="SOURCE", rate_tph=100.0, tonnage_t=100.0,
            start_bucket=0, end_bucket=0)],
        delivered={"TRAIN": [100.0, 0.0]},
        inventory={"PILE": [500.0, 400.0, 400.0]},
    )
    metrics = compute_kpis(scenario, plan)
    row = metrics["quality_breakdown"][0]
    assert row["requested_grade"] == "PASS"
    assert row["destination"] == "TRAIN"
    assert row["parameter"] == "BPL"
    assert row["compliant"] == 1


def test_export_specification_round_trips_with_nested_limits():
    restored = scenario_from_json(scenario_to_json(datasets.end_to_end()))
    original = datasets.end_to_end().grade("BG-AD")
    reloaded = restored.grade("BG-AD")
    assert reloaded.receiving_spec == original.receiving_spec
    assert reloaded.source_quality_ranges == original.source_quality_ranges
    assert reloaded.receiving_destinations == ["BG-TRAIN-PROD"]


def test_stale_builtin_migration_installs_export_specs_and_assay_register():
    scenario = datasets.end_to_end()
    scenario.grades = [grade for grade in scenario.grades
                       if grade.id not in datasets.TRAIN_EXPORT_GRADE_IDS]
    scenario.quality_params = [
        param for param in scenario.quality_params
        if param.name not in {"SiO2_R", "F", "P2O5_SC"}
    ]
    scenario.config["BUILTIN_BASELINE_REVISION"] = \
        "2026-09-15-s0-protected-objectives-v34"

    assert datasets.migrate_loaded_builtin(scenario) == "solver"
    assert {grade.id for grade in scenario.grades} >= set(
        datasets.TRAIN_EXPORT_GRADE_IDS)
    assert {param.name for param in scenario.quality_params} >= {
        "SiO2_R", "F", "P2O5_SC"}


def test_current_builtin_migration_repairs_missing_export_specs():
    scenario = datasets.end_to_end()
    scenario.grades = [
        grade for grade in scenario.grades
        if grade.id not in datasets.TRAIN_EXPORT_GRADE_IDS
    ]
    scenario.quality_params = [
        param for param in scenario.quality_params
        if param.name not in {"SiO2_R", "F", "P2O5_SC"}
    ]
    assert scenario.config["BUILTIN_BASELINE_REVISION"] == \
        datasets.BUILTIN_BASELINE_REVISION

    assert datasets.migrate_loaded_builtin(scenario) == "solver"
    assert {grade.id for grade in scenario.grades} >= set(
        datasets.TRAIN_EXPORT_GRADE_IDS)
    assert {param.name for param in scenario.quality_params} >= {
        "SiO2_R", "F", "P2O5_SC"}


def test_legacy_operator_catalogue_cannot_delete_receiving_contracts(tmp_path):
    source = datasets.end_to_end()
    legacy_physical_grades = [
        grade for grade in source.grades
        if not grade.is_specification
    ]
    catalog_path = tmp_path / "grade_catalog.json"
    save_grade_catalog(legacy_physical_grades, catalog_path)

    restored = datasets.end_to_end()
    assert apply_saved_grade_catalog(restored, catalog_path) is True
    assert {grade.id for grade in restored.grades} >= set(
        datasets.TRAIN_EXPORT_GRADE_IDS)
    assert all(restored.grade(grade_id).is_specification
               for grade_id in datasets.TRAIN_EXPORT_GRADE_IDS)


def test_weekly_editor_offers_export_profiles_only_at_the_train_receiver():
    scenario = datasets.end_to_end()
    display_to_grade, grade_to_display = _grade_display_maps(scenario)
    assert grade_to_display["BG10"] == "BG10"

    day = scenario.sim.bucket_dt(0).date().isoformat()
    base = {
        "Date": day,
        "Requested grade": "BG10",
        "Campaign quantity [t]": 1_000.0,
        "Start hour": 0.0,
        "End hour": 1.0,
        "Priority": 1,
    }
    train_rows = [{**base, "Receiving unit": "BG-TRAIN-PROD"}]
    entries = _weekly_campaign_entries(
        scenario, train_rows, display_to_grade)
    assert next(entry for entry in entries
                if entry.dest_id == "BG-TRAIN-PROD" and entry.bucket == 0
                ).requested_grade == "BG10"

    pap_rows = [{**base, "Receiving unit": "PAP-1"}]
    with pytest.raises(ValueError, match="BG-TRAIN-PROD"):
        _weekly_campaign_entries(scenario, pap_rows, display_to_grade)
