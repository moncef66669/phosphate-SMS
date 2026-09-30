# -*- coding: utf-8 -*-
"""Focused acceptance tests for Configure > Grade profiles.

These tests exercise the operator page and its pure staging helpers only.  They
deliberately avoid generating a plan so routine grade-editor changes stay fast
to validate.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from conftest import nav_to
from mph_sim import datasets
from mph_sim.grade_ids import RAW_BT_GRADE_IDS
from ui import common
from ui.pages_data import (
    _grade_profile_frame,
    _key_value_frame,
    _quality_column_map,
    _spec_frame,
    _stage_destination_specs,
    _stage_grade_profile_rows,
    _stage_material_profile,
)


ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "app.py")


def _fresh_grade_page() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert not at.exception, at.exception
    nav_to(at, common.CONFIGURE_GRADE_PROFILES_PAGE)
    assert not at.exception, at.exception
    return at


def _grade_editor(at: AppTest):
    return next(
        frame for frame in at.main.dataframe
        if str(frame.key).startswith("grade_vectors_editor_")
    )


def test_page17_is_focused_unit_aware_and_has_one_shared_raw_catalogue():
    at = _fresh_grade_page()

    assert at.sidebar.radio[0].value == "Configure"
    assert at.sidebar.radio[1].value == common.CONFIGURE_GRADE_PROFILES_PAGE
    assert [tab.label for tab in at.main.tabs] == [
        "Chemical grades",
        "Material profiles",
        "Receiving limits",
    ]
    assert any(
        "quality parameter register" in expander.label.lower()
        for expander in at.main.expander
    )

    editor = _grade_editor(at)
    frame = editor.value
    assert {
        "Grade ID",
        "Grade name",
        "Material state",
        "Basis",
        "Stockpiles using grade",
        "With pile-specific assay",
        "BPL [%]",
        "Cd [ppm]",
        "MgO [%]",
        "SiO2 [%]",
        "CO2 [%]",
        "CaO_CO2 [ratio]",
        "moisture [%]",
    } == set(frame.columns)

    grade_ids = frame["Grade ID"].tolist()
    for grade_id in RAW_BT_GRADE_IDS:
        assert grade_ids.count(grade_id) == 1
    assert not [
        grade_id for grade_id in grade_ids
        if str(grade_id).startswith(("LOU-RAW-", "BEN-RAW-", "MINE-RAW-"))
    ]

    # The retired all-model table editor must not leak back into this focused
    # operator page.
    editor_keys = {
        str(item.key) for item in at.main.dataframe if item.key is not None
    }
    assert not [key for key in editor_keys if key.startswith("edit_")]


def test_grade_profile_frame_keeps_raw_tbc_chemistry_blank():
    scenario = datasets.end_to_end()
    frame = _grade_profile_frame(scenario)
    row = frame.loc[frame["Grade ID"] == "RAW-TBC"].iloc[0]

    quality_columns = list(_quality_column_map(scenario).values())
    assert quality_columns
    assert row[quality_columns].isna().all()


def test_grade_rows_stage_one_cell_with_operator_override_provenance():
    original = datasets.end_to_end()
    original_snapshot = deepcopy(original)
    staged = deepcopy(original)
    edited = _grade_profile_frame(original)
    bpl_column = _quality_column_map(original)["BPL"]
    grade_id = "RAW-BT-GLOBAL"
    assert original.grade(grade_id).evidence.status == "CONFIRMED"
    edited.loc[edited["Grade ID"] == grade_id, bpl_column] = 56.5

    assert _stage_grade_profile_rows(staged, edited) == []

    updated = staged.grade(grade_id)
    assert updated.quality == {
        **original.grade(grade_id).quality,
        "BPL": 56.5,
    }
    assert updated.id == original.grade(grade_id).id
    assert updated.material_class == original.grade(grade_id).material_class
    assert updated.evidence.status == "ASSUMED"
    assert updated.evidence.confidence == "L"
    assert updated.evidence.source.startswith("Operator override")
    assert "Operator-edited fields: quality" in updated.evidence.note
    assert "Previous basis:" in updated.evidence.note
    for before in original.grades:
        if before.id != grade_id:
            assert staged.grade(before.id) == before

    audit = staged.config["GRADE_PROFILE_OVERRIDES"][-1]
    assert audit["entity_type"] == "grade"
    assert audit["entity"] == grade_id
    assert audit["fields"] == ["quality"]
    assert audit["at"]
    assert original == original_snapshot


def test_grade_rows_reject_bad_shape_and_bad_numeric_values():
    original = datasets.end_to_end()
    base = _grade_profile_frame(original)
    bpl_column = _quality_column_map(original)["BPL"]
    cd_column = _quality_column_map(original)["Cd"]

    duplicate = pd.concat(
        [base, base.loc[base["Grade ID"] == "TBT"]],
        ignore_index=True,
    )
    missing = base.loc[base["Grade ID"] != "BT"].copy()
    nonfinite = base.copy()
    nonfinite.loc[nonfinite["Grade ID"] == "TBT", bpl_column] = float("inf")
    negative = base.copy()
    negative.loc[negative["Grade ID"] == "TBT", cd_column] = -1.0
    over_percent = base.copy()
    over_percent.loc[
        over_percent["Grade ID"] == "TBT", bpl_column
    ] = 100.1

    cases = [
        ("duplicate", duplicate),
        ("Missing grade rows", missing),
        ("finite", nonfinite),
        ("negative", negative),
        ("exceed 100", over_percent),
    ]
    for expected_message, edited in cases:
        unchanged = deepcopy(original)
        staged = deepcopy(original)
        errors = _stage_grade_profile_rows(staged, edited)
        assert errors
        assert expected_message in " | ".join(errors)
        # Rejected staging is isolated from the loaded scenario; the page
        # commits only an error-free deep copy.
        assert original == unchanged


def test_material_profile_and_receiving_spec_staging_validate_structure():
    original = datasets.end_to_end()

    # A normal text-only correction preserves structural identity while
    # explicitly recording that the edited profile is now an assumption.
    staged_profile = deepcopy(original)
    profile = original.material_profile("LOU-MINE-PHOSPHATE")
    assert profile.evidence.status == "CONFIRMED"
    errors = _stage_material_profile(
        staged_profile,
        profile.id,
        label=profile.label + " (reviewed)",
        grade_ids=list(profile.grade_ids),
        quality_ranges=_key_value_frame(
            profile.quality_ranges, "Parameter", "Documented range"),
        physical_properties=_key_value_frame(
            profile.physical_properties, "Property", "Documented value"),
        transformation_note=profile.transformation_note,
    )
    assert errors == []
    updated = staged_profile.material_profile(profile.id)
    assert (updated.id, updated.site, updated.stage, updated.material_class) == (
        profile.id, profile.site, profile.stage, profile.material_class)
    assert updated.evidence.status == "ASSUMED"
    assert updated.evidence.confidence == "L"
    assert updated.evidence.source.startswith("Operator override")
    assert "Operator-edited fields: label" in updated.evidence.note
    assert "Previous basis:" in updated.evidence.note
    profile_audit = staged_profile.config["GRADE_PROFILE_OVERRIDES"][-1]
    assert profile_audit["entity_type"] == "material_profile"
    assert profile_audit["entity"] == profile.id
    assert profile_audit["fields"] == ["label"]
    assert original.material_profile(profile.id).label == profile.label

    invalid_profile = deepcopy(original)
    errors = _stage_material_profile(
        invalid_profile,
        profile.id,
        label=profile.label,
        grade_ids=["TBT"],  # washed grade in a raw/screened profile
        quality_ranges=_key_value_frame(
            profile.quality_ranges, "Parameter", "Documented range"),
        physical_properties=_key_value_frame(
            profile.physical_properties, "Property", "Documented value"),
        transformation_note=profile.transformation_note,
    )
    assert any("washed, not raw_screened" in error for error in errors)

    empty_family = deepcopy(original)
    errors = _stage_material_profile(
        empty_family,
        profile.id,
        label=profile.label,
        grade_ids=[],
        quality_ranges=_key_value_frame(
            profile.quality_ranges, "Parameter", "Documented range"),
        physical_properties=_key_value_frame(
            profile.physical_properties, "Property", "Documented value"),
        transformation_note=profile.transformation_note,
    )
    assert any("select at least one allowed grade" in error for error in errors)
    assert empty_family.material_profile(profile.id) == profile

    staged_specs = deepcopy(original)
    destination = original.destination("PAP-1")
    valid_specs = _spec_frame(destination, original)
    assert list(valid_specs.columns) == [
        "Parameter",
        "Unit",
        "Min",
        "Max",
        "Target",
        "Hard limit",
        "Averaging [h]",
        "Margin",
    ]
    assert valid_specs.loc[
        valid_specs["Parameter"] == "BPL", "Unit"
    ].iloc[0] == "%"
    assert valid_specs.loc[
        valid_specs["Parameter"] == "Cd", "Unit"
    ].iloc[0] == "ppm"
    valid_specs.loc[valid_specs["Parameter"] == "BPL", "Target"] = 72.0
    assert _stage_destination_specs(
        staged_specs, destination.id, valid_specs) == []
    updated_bpl = next(
        spec for spec in staged_specs.destination(destination.id).spec
        if spec.param == "BPL"
    )
    original_bpl = next(
        spec for spec in original.destination(destination.id).spec
        if spec.param == "BPL"
    )
    assert updated_bpl.target == 72.0
    assert original_bpl.target == 71.0
    spec_audit = staged_specs.config["GRADE_PROFILE_OVERRIDES"][-1]
    assert spec_audit["entity_type"] == "receiving_limits"
    assert spec_audit["entity"] == destination.id
    assert spec_audit["fields"] == ["spec"]

    duplicate_specs = pd.concat(
        [valid_specs, valid_specs.iloc[[0]]], ignore_index=True)
    rejected = deepcopy(original)
    errors = _stage_destination_specs(
        rejected, destination.id, duplicate_specs)
    assert any("duplicate receiving limit" in error for error in errors)
    assert rejected.destination(destination.id).spec == destination.spec

    boundless_specs = valid_specs.copy()
    boundless_specs.loc[
        boundless_specs["Parameter"] == "BPL", ["Min", "Max", "Target"]
    ] = None
    rejected = deepcopy(original)
    errors = _stage_destination_specs(
        rejected, destination.id, boundless_specs)
    assert any(
        "enter at least a minimum, maximum or target" in error
        for error in errors
    )
    assert rejected.destination(destination.id).spec == destination.spec
