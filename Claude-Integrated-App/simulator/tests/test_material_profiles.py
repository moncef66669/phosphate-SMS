# -*- coding: utf-8 -*-
"""Mine/screened versus washed-phosphate profile acceptance tests."""
import copy
import json

from mph_sim import datasets
from mph_sim.domain import Grade
from mph_sim.grade_ids import (RAW_BT_GRADE_IDS, RAW_GRADE_IDS,
                               WASHED_GRADE_IDS)
from mph_sim.persistence import scenario_from_json, scenario_tables, scenario_to_json
from mph_sim.validation import validate_scenario
from ui.layout_geometry import geometry_for


def test_s3_has_stage_specific_material_profiles():
    sc = datasets.end_to_end()
    ids = {p.id for p in sc.material_profiles}
    assert ids == {
        "M16-WASHED-HUMID",
        "BEN-RAW-SCREENED",
        "BEN-WASHED-FILTERED",
        "BEN-SETTLED-PRODUCT",
        "LOU-MINE-PHOSPHATE",
        "LOU-WASHED-PRODUCT",
    }


def test_benguerir_raw_and_washed_profiles_never_share_material_class():
    sc = datasets.end_to_end()
    raw = sc.material_profile("BEN-RAW-SCREENED")
    washed = sc.material_profile("BEN-SETTLED-PRODUCT")
    assert raw.material_class == "raw_screened"
    assert washed.material_class == "washed"
    assert set(raw.grade_ids) == {"RAW-TBC", *RAW_GRADE_IDS}
    assert set(washed.grade_ids) == set(WASHED_GRADE_IDS)
    assert "shared raw catalogue" in raw.quality_ranges["BPL"]
    assert "live pile assay TBC" in raw.quality_ranges["BPL"]
    assert "washed-product assay table required" in washed.quality_ranges["BPL"]


def test_documented_benguerir_physical_and_moisture_states_are_preserved():
    sc = datasets.end_to_end()
    raw = sc.material_profile("BEN-RAW-SCREENED")
    filtered = sc.material_profile("BEN-WASHED-FILTERED")
    settled = sc.material_profile("BEN-SETTLED-PRODUCT")
    assert raw.quality_ranges["moisture"] == "10–14 % H2O"
    assert raw.physical_properties["PSD"] == "<15 mm"
    assert filtered.quality_ranges["moisture"] == "23 % H2O"
    assert settled.quality_ranges["moisture"] == "15–20 % H2O"


def test_modelled_piles_reference_the_correct_washed_profile():
    sc = datasets.end_to_end()
    expected = {
        "BG-P1": "BEN-SETTLED-PRODUCT",
        "BG-P2": "BEN-SETTLED-PRODUCT",
        "LT-P1": "LOU-WASHED-PRODUCT",
        "LT-P2": "LOU-WASHED-PRODUCT",
    }
    for sid, pid in expected.items():
        sp = sc.space(sid)
        assert sp.material_class == "washed"
        assert sp.profile_id == pid
    assert all(sc.space(f"HP-{i:02d}").profile_id == "M16-WASHED-HUMID"
               for i in range(1, 9))


def test_grade_metadata_and_quality_parameter_register_are_explicit():
    sc = datasets.end_to_end()
    assert sc.grade("RAW-TBC").material_class == "raw_screened"
    assert sc.grade("RAW-TBC").quality == {}
    global_raw = sc.grade("RAW-BT")
    assert global_raw.material_class == "raw_screened"
    assert global_raw.profile_id == ""
    assert global_raw.name == "BT"
    assert global_raw.quality == {
        "BPL": 56.06,
        "Cd": 21.0,
        "MgO": 1.49,
        "SiO2": 13.71,
        "CO2": 8.21,
        "moisture": 15.0,
    }
    assert sc.grade("RAW-BT-HIGH-CD").quality["Cd"] == 25.0
    assert sc.grade("RAW-BT-LOW-CD").quality["Cd"] == 13.0
    assert all(sc.grade(grade_id).profile_id == ""
               for grade_id in RAW_BT_GRADE_IDS)
    assert not [
        grade for grade in sc.grades
        if grade.id.startswith(("LOU-", "BEN-", "MINE-"))
        or grade.name.lower().startswith(("louta ", "benguerir ", "mine "))
    ]
    assert all(sc.grade(g).material_class == "washed"
               for g in ("TBT", "BT", "MT"))
    assert {"CO2", "CaO_CO2"} <= {p.name for p in sc.quality_params}


def test_washed_grade_vectors_follow_tbt_bt_mt_quality_order():
    sc = datasets.end_to_end()
    tbt, bt, mt = (sc.grade(grade_id) for grade_id in ("TBT", "BT", "MT"))

    assert tbt.quality["BPL"] < bt.quality["BPL"] < mt.quality["BPL"]
    assert tbt.rock_value < bt.rock_value < mt.rock_value
    for deleterious in ("Cd", "MgO", "SiO2"):
        assert (
            tbt.quality[deleterious]
            > bt.quality[deleterious]
            > mt.quality[deleterious]
        )
    assert {grade.quality["moisture"] for grade in (tbt, bt, mt)} == {12.0}


def test_site_material_profiles_share_the_mine_neutral_raw_grade_catalogue():
    sc = datasets.end_to_end()
    louta = sc.material_profile("LOU-MINE-PHOSPHATE")
    benguerir = sc.material_profile("BEN-RAW-SCREENED")
    assert louta.grade_ids == list(RAW_GRADE_IDS)
    assert set(RAW_BT_GRADE_IDS) <= set(benguerir.grade_ids)
    assert "54.10–58.93" in louta.quality_ranges["BPL"]
    assert "Q80130-LOU-L02-PR-BOD-0001" in louta.evidence.source
    assert sc.config["BG_RAW_DISPLAY_GRADE_IDS"] == {
        "BG-RAW-1": "RAW-BT",
        "BG-RAW-2": "RAW-BT",
    }
    components = sc.config["BG_RAW_DISPLAY_GRADE_COMPONENTS"]
    assert [
        (row["grade_id"], row["mass_fraction"])
        for row in components["BG-RAW-1"]
    ] == [
        ("RAW-BT", 0.5),
        ("RAW-BT-HIGH-CD", 0.5),
    ]
    assert [
        (row["grade_id"], row["mass_fraction"])
        for row in components["BG-RAW-2"]
    ] == [
        ("RAW-BT", 0.5),
        ("RAW-BT-LOW-CD", 0.5),
    ]
    assert "shared by Benguerir and Louta" in \
        sc.config["BG_RAW_DISPLAY_GRADE_BASIS"]
    assert "not a live" in sc.config["BG_RAW_DISPLAY_GRADE_BASIS"]
    assert "schematic" in \
        sc.config["BG_RAW_DISPLAY_GRADE_COMPOSITION_BASIS"]

    sc.spaces = [space for space in sc.spaces
                 if space.id not in {"BG-RAW-1", "BG-RAW-2"}]
    piles = {
        pile.pile_id: pile
        for pile in geometry_for(sc, "BENGUERIR").piles
        if pile.pile_id.startswith("BG-RAW")
    }
    assert piles["BG-RAW-1"].display_quality == {
        "BPL": 55.585,
        "CO2": 8.31,
        "Cd": 23.0,
        "MgO": 1.495,
        "SiO2": 14.995000000000001,
    }
    assert piles["BG-RAW-2"].display_quality == {
        "BPL": 57.17,
        "CO2": 8.48,
        "Cd": 17.0,
        "MgO": 1.31,
        "SiO2": 11.965,
    }


def test_benguerir_display_composite_is_mass_weighted_not_simple_average():
    sc = copy.deepcopy(datasets.end_to_end())
    # Built-in scheduler piles obtain their live grade and chemistry from the
    # scenario view-model, so geometry deliberately clears the legacy static
    # display overlay when it binds a matching StockSpace.  Remove this one
    # binding to exercise the reference-pile composition adapter itself.
    sc.spaces = [space for space in sc.spaces if space.id != "BG-RAW-1"]
    sc.config["BG_RAW_DISPLAY_GRADE_COMPONENTS"]["BG-RAW-1"] = [
        {
            "component_id": "C1",
            "mass_fraction": 0.25,
            "grade_id": "RAW-BT",
        },
        {
            "component_id": "C2",
            "mass_fraction": 0.75,
            "grade_id": "RAW-BT-HIGH-CD",
        },
    ]
    pile = next(
        pile for pile in geometry_for(sc, "BENGUERIR").piles
        if pile.pile_id == "BG-RAW-1"
    )
    assert pile.display_quality["BPL"] == 0.25 * 56.06 + 0.75 * 55.11
    assert pile.display_quality["Cd"] == 0.25 * 21.0 + 0.75 * 25.0
    assert [component.mass_fraction
            for component in pile.display_grade_components] == [0.25, 0.75]
    assert pile.display_grade_components[0].frac_to == 0.25
    assert pile.display_grade_components[1].frac_from == 0.25


def test_material_profiles_roundtrip_and_export_table():
    sc = datasets.end_to_end()
    sc2 = scenario_from_json(scenario_to_json(sc))
    assert sc2.material_profile("BEN-RAW-SCREENED").quality_ranges == \
        sc.material_profile("BEN-RAW-SCREENED").quality_ranges
    tables = scenario_tables(sc2)
    assert "material_profiles" in tables
    assert len(tables["material_profiles"]) == 6
    blob = "\n".join(
        frame.to_csv(index=False) for frame in tables.values())
    assert "LOU-RAW-BT" not in blob
    assert "MINE-RAW-TBC" not in blob


def test_legacy_raw_grade_ids_are_canonicalized_on_json_import_and_export():
    payload = json.loads(scenario_to_json(datasets.end_to_end()))
    aliases = {
        "RAW-TBC": "MINE-RAW-pending",
        "RAW-BT": "LOU-RAW-BT-GLOBAL",
        "RAW-BT-HIGH-CD": "LOU-RAW-BT-HIGH-CD",
        "RAW-BT-LOW-CD": "LOU-RAW-BT-LOW-CD",
    }

    def legacy(value):
        if isinstance(value, dict):
            return {key: legacy(item) for key, item in value.items()}
        if isinstance(value, list):
            return [legacy(item) for item in value]
        return aliases.get(value, value)

    legacy_payload = legacy(payload)
    for grade in legacy_payload["grades"]:
        if grade["id"].startswith("LOU-RAW-BT-"):
            grade["name"] = grade["name"].replace("Raw BT", "Louta raw BT")
            grade["profile_id"] = "LOU-MINE-PHOSPHATE"
    restored = scenario_from_json(json.dumps(legacy_payload))
    assert {grade.id for grade in restored.grades} >= {
        "RAW-TBC", *RAW_BT_GRADE_IDS}
    emitted = scenario_to_json(restored)
    assert "LOU-RAW-BT" not in emitted
    assert "MINE-RAW" not in emitted
    assert all(restored.grade(grade_id) for grade_id in RAW_BT_GRADE_IDS)
    assert restored.grade("RAW-BT").name == "BT"
    assert restored.grade("RAW-BT").profile_id == ""


def test_hot_loaded_builtin_migrates_legacy_raw_ids_and_invalidates_plan():
    scenario = datasets.end_to_end()
    reverse = {
        "RAW-TBC": "MINE-RAW-TBC",
        "RAW-BT": "LOU-RAW-BT-GLOBAL",
        "RAW-BT-HIGH-CD": "LOU-RAW-BT-HIGH-CD",
        "RAW-BT-LOW-CD": "LOU-RAW-BT-LOW-CD",
    }
    scenario.config["BUILTIN_BASELINE_REVISION"] = "before-shared-raw-grades"
    for grade in scenario.grades:
        if grade.id in reverse:
            grade.id = reverse[grade.id]
            if grade.id.startswith("LOU-"):
                grade.name = grade.name.replace("Raw BT", "Louta raw BT")
                grade.profile_id = "LOU-MINE-PHOSPHATE"
    for profile in scenario.material_profiles:
        profile.grade_ids = [reverse.get(gid, gid) for gid in profile.grade_ids]
    for space in scenario.spaces:
        space.grade = reverse.get(space.grade, space.grade)
        space.designated_grade = reverse.get(
            space.designated_grade, space.designated_grade)
    for transfer in scenario.process_transfers:
        transfer.output_grade = reverse.get(
            transfer.output_grade, transfer.output_grade)

    assert datasets.migrate_loaded_builtin(scenario) == "solver"
    assert {grade.id for grade in scenario.grades} >= {
        "RAW-TBC", *RAW_BT_GRADE_IDS}
    assert not [grade for grade in scenario.grades
                if grade.id.startswith(("LOU-", "MINE-"))]
    assert scenario.grade("RAW-BT").name == "BT"
    assert scenario.grade("RAW-BT").profile_id == ""
    assert all(
        not space.grade.startswith(("LOU-", "MINE-"))
        for space in scenario.spaces)


def test_validation_blocks_raw_washed_grade_mixup():
    sc = copy.deepcopy(datasets.end_to_end())
    sc.space("BG-P1").material_class = "raw_screened"
    codes = {(i.code, i.entity) for i in validate_scenario(sc)
             if i.severity == "ERROR"}
    assert ("PROFILE_CLASS_MISMATCH", "BG-P1") in codes
    assert ("GRADE_CLASS_MISMATCH", "BG-P1") in codes


def test_validation_blocks_grade_outside_profile_family():
    sc = copy.deepcopy(datasets.end_to_end())
    sc.grades.append(Grade("OTHER-WASHED", "Other washed grade", {},
                           material_class="washed"))
    sc.space("BG-P1").grade = "OTHER-WASHED"
    codes = {(i.code, i.entity) for i in validate_scenario(sc)
             if i.severity == "ERROR"}
    assert ("PROFILE_GRADE_MISMATCH", "BG-P1") in codes


def test_baselines_have_no_material_profile_validation_errors():
    for sc in (datasets.mph_phosphate(), datasets.end_to_end()):
        errors = [i for i in validate_scenario(sc) if i.severity == "ERROR"]
        assert not errors
