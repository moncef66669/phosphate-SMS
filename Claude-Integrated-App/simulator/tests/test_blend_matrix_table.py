"""Contracts for the one-row-per-matrix editor and catalogue conversion."""

from copy import deepcopy

import pandas as pd
import pytest

from ui.blend_matrix_table import (
    component_columns,
    matrix_frame,
    parse_matrix_frame,
)


@pytest.fixture
def catalog():
    return [
        {
            "id": "MPH-01", "name": "Dry-feed matrix", "components": [
                {"grade_id": "TBT", "target_fraction": 0.10},
                {"grade_id": "BT-LOW-CD", "target_fraction": 0.90},
            ],
        },
        {
            "id": "MPH-02", "name": "Alternative", "components": [
                {"grade_id": "TBT", "target_fraction": 0.25},
                {"grade_id": "BT-HIGH-CD", "target_fraction": 0.75},
            ],
        },
    ]


def test_multiple_matrices_render_once_and_round_trip_without_mutation(catalog):
    original = deepcopy(catalog)
    columns = component_columns(catalog)

    assert columns == {
        "TBT [%]": {"grade_id": "TBT"},
        "BT-LOW-CD [%]": {"grade_id": "BT-LOW-CD"},
        "BT-HIGH-CD [%]": {"grade_id": "BT-HIGH-CD"},
    }
    frame = matrix_frame(catalog, columns)

    assert list(frame.columns) == ["Matrix ID", *columns]
    assert "Matrix name" not in frame.columns
    assert list(frame["Matrix ID"]) == ["MPH-01", "MPH-02"]
    assert frame.loc[0, "TBT [%]"] == 10.0
    assert frame.loc[0, "BT-LOW-CD [%]"] == 90.0
    assert frame.loc[1, "TBT [%]"] == 25.0
    assert frame.loc[1, "BT-HIGH-CD [%]"] == 75.0
    assert parse_matrix_frame(frame, columns, original_catalog=catalog) == original
    assert catalog == original


def test_source_identity_is_retained_for_matching_grade_at_two_stockpiles():
    catalog = [{
        "id": "LOU-01", "name": "Louta blend", "components": [
            {"grade_id": "RAW-BT", "source_space": "110LG-OL1",
             "target_fraction": 0.40},
            {"grade_id": "RAW-BT", "source_space": "110LG-OL2",
             "target_fraction": 0.60},
        ],
    }]
    columns = component_columns(catalog)
    assert list(columns) == [
        "RAW-BT @ 110LG-OL1 [%]", "RAW-BT @ 110LG-OL2 [%]"]

    frame = matrix_frame(catalog, columns)

    assert len(frame) == 1
    assert frame.loc[0, "RAW-BT @ 110LG-OL1 [%]"] == 40.0
    assert frame.loc[0, "RAW-BT @ 110LG-OL2 [%]"] == 60.0
    assert parse_matrix_frame(frame, columns, original_catalog=catalog) == catalog


def test_empty_catalogue_has_available_columns_and_no_draft_matrices():
    columns = component_columns([], extra_components=[
        {"source_space": "BG-RAW-1", "grade_id": "RAW-BT",
         "min_fraction": 0.05, "max_fraction": 0.95},
        {"source_space": "BG-RAW-2", "grade_id": "RAW-BT",
         "min_fraction": 0.05, "max_fraction": 0.95},
    ])
    frame = matrix_frame([], columns)

    assert list(frame.columns) == [
        "Matrix ID", "RAW-BT @ BG-RAW-1 [%]",
        "RAW-BT @ BG-RAW-2 [%]",
    ]
    assert frame.empty
    assert isinstance(frame.index, pd.RangeIndex)
    assert parse_matrix_frame(frame, columns) == []


def test_additional_columns_are_deduplicated_and_blank_components_are_unassigned(
        catalog):
    columns = component_columns(catalog, extra_components=[
        {"grade_id": "TBT"}, {"grade_id": "UNASSIGNED"},
        {"grade_id": "ZERO"},
    ])
    frame = matrix_frame(catalog, columns)
    frame["UNASSIGNED [%]"] = None
    frame["ZERO [%]"] = 0.0
    frame.loc[len(frame)] = [None] * len(frame.columns)

    assert list(columns).count("TBT [%]") == 1
    assert parse_matrix_frame(frame, columns, original_catalog=catalog) == catalog


@pytest.mark.parametrize("percentages", [
    (20.0, 90.0),
    (10.0, 80.0),
    (-10.0, 110.0),
    (100.0, 0.0),
    (None, None),
    (float("inf"), 90.0),
    (float("-inf"), 90.0),
    ("NaN", 90.0),
    ("not a percentage", 90.0),
])
def test_invalid_percentages_cannot_be_saved(catalog, percentages):
    columns = component_columns(catalog[:1])
    frame = matrix_frame(catalog[:1], columns).astype(object)
    frame.loc[0, ["TBT [%]", "BT-LOW-CD [%]"]] = percentages

    with pytest.raises(ValueError):
        parse_matrix_frame(frame, columns)


def test_duplicate_matrix_ids_are_rejected(catalog):
    columns = component_columns(catalog)
    frame = matrix_frame(catalog, columns)
    frame.loc[1, "Matrix ID"] = " MPH-01 "

    with pytest.raises(ValueError):
        parse_matrix_frame(frame, columns)


@pytest.mark.parametrize("missing_id", [None, "", "   ", float("nan")])
def test_missing_matrix_id_cannot_silently_drop_assigned_percentages(
        catalog, missing_id):
    columns = component_columns(catalog[:1])
    frame = matrix_frame(catalog[:1], columns)
    frame.loc[0, "Matrix ID"] = missing_id

    with pytest.raises(ValueError):
        parse_matrix_frame(frame, columns)


def test_internal_matrix_names_follow_ids_when_rows_are_reordered(catalog):
    columns = component_columns(catalog)
    frame = matrix_frame(catalog, columns).iloc[::-1].reset_index(drop=True)

    assert parse_matrix_frame(
        frame, columns, original_catalog=catalog) == catalog[::-1]


def test_new_matrix_ids_use_id_as_internal_name(catalog):
    columns = component_columns(catalog[:1])
    frame = matrix_frame(catalog[:1], columns)
    frame.loc[0, "Matrix ID"] = "NEW-MATRIX"

    [saved] = parse_matrix_frame(frame, columns, original_catalog=catalog)

    assert saved["id"] == "NEW-MATRIX"
    assert saved["name"] == "NEW-MATRIX"
    assert saved["components"] == catalog[0]["components"]


def test_matrix_names_default_to_ids_without_an_original_catalogue(catalog):
    columns = component_columns(catalog)
    frame = matrix_frame(catalog, columns)

    saved = parse_matrix_frame(frame, columns)

    assert [(entry["id"], entry["name"]) for entry in saved] == [
        ("MPH-01", "MPH-01"), ("MPH-02", "MPH-02")]
