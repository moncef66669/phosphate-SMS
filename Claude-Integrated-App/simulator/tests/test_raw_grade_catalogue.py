"""Focused coverage for the documented mine-neutral raw-grade catalogue."""

from copy import deepcopy

from mph_sim.datasets import end_to_end
from mph_sim.grade_ids import (
    B02_RAW_GRADE_IDS,
    RAW_GRADE_IDS,
    canonicalize_scenario_grade_ids,
)
from mph_sim.persistence import scenario_from_json, scenario_to_json


EXPECTED_B02_RAW_QUALITY = {
    "RAW-MT": {
        "BPL": 58.93, "Cd": 13.0, "MgO": 1.10, "SiO2": 10.13,
        "CO2": 6.54, "moisture": 12.0,
    },
    "RAW-TBT": {
        "BPL": 54.10, "Cd": 16.0, "MgO": 1.52, "SiO2": 14.39,
        "CO2": 8.32, "moisture": 12.0,
    },
}


def test_b02_raw_chemistry_extends_the_shared_catalogue_without_average():
    scenario = end_to_end()

    assert B02_RAW_GRADE_IDS == ("RAW-MT", "RAW-TBT")
    assert RAW_GRADE_IDS == (
        "RAW-MT", "RAW-BT", "RAW-BT-HIGH-CD", "RAW-BT-LOW-CD",
        "RAW-TBT",
    )
    assert "RAW-AVERAGE" not in {grade.id for grade in scenario.grades}

    for grade_id, expected_quality in EXPECTED_B02_RAW_QUALITY.items():
        grade = scenario.grade(grade_id)
        assert grade is not None
        assert grade.material_class == "raw_screened"
        assert grade.profile_id == ""
        assert grade.quality == expected_quality
        assert grade.evidence.status == "CONFIRMED"
        if grade_id == "RAW-TBT":
            assert "project-team supplied TBT assay" in grade.evidence.source
            assert "5% H2O value is intentionally excluded" in grade.evidence.note
        else:
            assert "Q80130-BEN-B02-PR-BOD-0001[0]" in grade.evidence.source
            assert "§5.2.1" in grade.evidence.source

    for profile_id in ("BEN-RAW-SCREENED", "LOU-MINE-PHOSPHATE"):
        assert scenario.material_profile(profile_id).grade_ids == list(
            RAW_GRADE_IDS) + ([] if profile_id.startswith("LOU-") else ["RAW-TBC"])
    assert scenario.config["BG_RAW_DISPLAY_GRADE_CATALOG"] == list(
        RAW_GRADE_IDS)


def test_raw_grade_catalogue_round_trip_preserves_active_tbt_route():
    scenario = end_to_end()
    active_output_grades = {
        transfer.output_grade for transfer in scenario.process_transfers
    }
    assert "RAW-TBT" in active_output_grades
    assert "RAW-MT" not in active_output_grades

    restored = scenario_from_json(scenario_to_json(scenario))
    for grade_id, expected_quality in EXPECTED_B02_RAW_QUALITY.items():
        assert restored.grade(grade_id).quality == expected_quality
    assert restored.config["BG_RAW_DISPLAY_GRADE_CATALOG"] == list(
        RAW_GRADE_IDS)


def test_deleted_nominal_bt_import_prefers_the_canonical_raw_bt_definition():
    scenario = end_to_end()
    canonical = deepcopy(scenario.grade("RAW-BT"))
    obsolete = deepcopy(canonical)
    obsolete.id = "RAW-BT-NOMINAL"
    obsolete.name = "obsolete duplicate"
    obsolete.quality["BPL"] = 1.0
    scenario.grades.insert(0, obsolete)
    scenario.material_profile("BEN-RAW-SCREENED").grade_ids.insert(
        0, "RAW-BT-NOMINAL")

    assert canonicalize_scenario_grade_ids(scenario)
    assert "RAW-BT-NOMINAL" not in {grade.id for grade in scenario.grades}
    raw_bt = [grade for grade in scenario.grades if grade.id == "RAW-BT"]
    assert len(raw_bt) == 1
    assert raw_bt[0].quality == canonical.quality
    assert scenario.material_profile(
        "BEN-RAW-SCREENED").grade_ids.count("RAW-BT") == 1
