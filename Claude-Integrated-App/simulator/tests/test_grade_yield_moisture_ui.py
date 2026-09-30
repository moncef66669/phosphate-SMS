# -*- coding: utf-8 -*-
"""Focused contracts for the integrated grade mass-conversion tab."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from conftest import nav_to
from mph_sim import datasets
from ui import common
from ui.pages_data import (
    _grade_yield_moisture_frame,
    _stage_grade_yield_moisture_rows,
)


ROOT = Path(__file__).resolve().parents[1]
APP = str(ROOT / "app.py")


def _fresh_page() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert not at.exception, at.exception
    nav_to(at, common.CONFIGURE_GRADE_PROFILES_PAGE)
    assert not at.exception, at.exception
    return at


def _page_text(at: AppTest) -> str:
    values = []
    for kind in ("subheader", "caption", "info", "markdown", "success"):
        values.extend(str(item.value) for item in getattr(at.main, kind))
    return "\n".join(values)


def test_tab_is_integrated_compact_and_does_not_expose_cut_choices():
    at = _fresh_page()

    assert at.sidebar.radio[0].value == "Configure"
    assert at.sidebar.radio[1].value == common.CONFIGURE_GRADE_PROFILES_PAGE
    assert [tab.label for tab in at.main.tabs] == [
        "Chemical grades",
        "Yield & moisture",
        "Material profiles",
        "Train export grades",
        "Receiving limits",
    ]
    assert "Grade configuration" in _page_text(at)
    assert "Beneficiation yield & moisture" in _page_text(at)
    assert "Benguerir beneficiation" in _page_text(at)
    assert "Louta retains its separate documented washing cases" in \
        _page_text(at)
    assert "12% raw moisture" in _page_text(at)
    assert "17.5% wet-product moisture" in _page_text(at)
    assert "not measured live values" in _page_text(at)
    assert "160" not in _page_text(at)
    assert "200" not in _page_text(at)

    editors = [
        frame for frame in at.main.dataframe
        if str(frame.key).startswith("grade_yield_moisture_editor_")
    ]
    assert len(editors) == 1
    frame = editors[0].value
    assert list(frame.columns) == [
        "Grade ID",
        "Grade name",
        "Raw feed grade",
        "Beneficiation yield [%]",
        "Raw moisture [%]",
        "Wet-product moisture [%]",
        "Raw → TSM factor [%]",
        "Wet product [t/TSM]",
    ]
    assert frame["Grade ID"].tolist() == [
        "TBT", "BT", "MT",
        "TBT-HIGH-CD", "BT-HIGH-CD", "MT-HIGH-CD",
        "TBT-LOW-CD", "BT-LOW-CD", "MT-LOW-CD",
    ]
    assert frame.set_index("Grade ID")["Raw feed grade"].to_dict() == {
        "TBT": "RAW-BT",
        "BT": "RAW-BT",
        "MT": "RAW-BT",
        "TBT-HIGH-CD": "RAW-BT-HIGH-CD",
        "BT-HIGH-CD": "RAW-BT-HIGH-CD",
        "MT-HIGH-CD": "RAW-BT-HIGH-CD",
        "TBT-LOW-CD": "RAW-BT-LOW-CD",
        "BT-LOW-CD": "RAW-BT-LOW-CD",
        "MT-LOW-CD": "RAW-BT-LOW-CD",
    }
    apply_buttons = [
        button for button in at.main.button
        if button.label == "Apply yield & moisture"
    ]
    assert len(apply_buttons) == 1


def test_frame_derives_stage_conversions_from_shared_grade_inputs():
    scenario = datasets.end_to_end()
    frame = _grade_yield_moisture_frame(scenario).set_index("Grade ID")

    for grade_id in (
        "TBT", "BT", "MT",
        "TBT-HIGH-CD", "BT-HIGH-CD", "MT-HIGH-CD",
        "TBT-LOW-CD", "BT-LOW-CD", "MT-LOW-CD",
    ):
        grade = scenario.grade(grade_id)
        expected_factor = (
            grade.beneficiation_yield
            * (1.0 - grade.raw_moisture_pct / 100.0)
            * 100.0
        )
        expected_wet_per_tsm = 1.0 / (
            1.0 - grade.product_moisture_pct / 100.0)
        assert frame.loc[grade_id, "Raw → TSM factor [%]"] == \
            pytest.approx(expected_factor)
        assert frame.loc[grade_id, "Wet product [t/TSM]"] == \
            pytest.approx(expected_wet_per_tsm)


def test_staging_updates_only_conversion_fields_and_records_audit():
    original = datasets.end_to_end()
    staged = deepcopy(original)
    edited = _grade_yield_moisture_frame(original)
    edited.loc[edited["Grade ID"] == "BT", "Beneficiation yield [%]"] = 67.0
    edited.loc[edited["Grade ID"] == "BT", "Raw moisture [%]"] = 11.5
    edited.loc[
        edited["Grade ID"] == "BT", "Wet-product moisture [%]"
    ] = 18.0

    assert _stage_grade_yield_moisture_rows(staged, edited) == []
    updated = staged.grade("BT")
    assert updated.beneficiation_yield == pytest.approx(0.67)
    assert updated.raw_moisture_pct == pytest.approx(11.5)
    assert updated.product_moisture_pct == pytest.approx(18.0)
    assert updated.quality == original.grade("BT").quality
    assert original.grade("BT").beneficiation_yield != pytest.approx(0.67)
    audit = staged.config["GRADE_PROFILE_OVERRIDES"][-1]
    assert audit == {
        **audit,
        "entity_type": "grade_mass_conversion",
        "entity": "BT",
        "fields": [
            "beneficiation_yield",
            "raw_moisture_pct",
            "product_moisture_pct",
        ],
    }


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("Beneficiation yield [%]", 100.1,
         "must be above 0 and at most 100"),
        ("Raw moisture [%]", -0.1, "must be at least 0 and below 100"),
        ("Wet-product moisture [%]", 100.0,
         "must be at least 0 and below 100"),
    ],
)
def test_staging_rejects_invalid_percentages(column, value, message):
    original = datasets.end_to_end()
    staged = deepcopy(original)
    edited = _grade_yield_moisture_frame(original)
    edited.loc[edited["Grade ID"] == "BT", column] = value

    errors = _stage_grade_yield_moisture_rows(staged, edited)
    assert any(message in error for error in errors)
    assert staged.grade("BT") == original.grade("BT")
