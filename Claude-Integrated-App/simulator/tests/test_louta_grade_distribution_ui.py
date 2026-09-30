# -*- coding: utf-8 -*-
"""Louta assumed multi-profile product-pile distribution UI."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mph_sim import datasets                                      # noqa: E402
from ui.layout_geometry import louta_geometry                     # noqa: E402
from ui.process_mimic import (MimicOpts,                          # noqa: E402
                              build_process_mimic_state,
                              grade_profile_distribution_figure,
                              grade_profile_distribution_rows,
                              process_mimic_figure)
from ui.stockyard_monitor import grade_color                      # noqa: E402


@pytest.fixture(scope="module")
def s3():
    return datasets.end_to_end()


@pytest.fixture(scope="module")
def louta_state(s3):
    return build_process_mimic_state(s3, basis="initial", bucket=0)


def _pile(state, pile_id):
    return next(p for p in state.monitor.sites["LOUTA"].piles
                if p.id == pile_id)


def test_assumed_distribution_is_not_presented_as_inventory_segments(
        louta_state):
    p1 = _pile(louta_state, "LT-P1")
    p2 = _pile(louta_state, "LT-P2")
    assert [(s.grade, s.mass_t, s.actual) for s in p1.segments] == [
        ("TBT", pytest.approx(57_453.6), False),
        ("BT", pytest.approx(38_302.4), False),
    ]
    assert [(s.grade, s.mass_t, s.actual) for s in p2.segments] == [
        ("BT", pytest.approx(57_453.6), False),
        ("MT", pytest.approx(38_302.4), False),
    ]
    assert all("ASSUMED" in s.basis for p in (p1, p2)
               for s in p.segments)
    assert all("grade-profile mass distribution" in " ".join(p.warnings)
               for p in (p1, p2))


def test_louta_mimic_labels_each_profile_tonnage_and_keeps_hover_short(
        s3, louta_state):
    fig = process_mimic_figure(louta_state, louta_geometry(s3))
    labels = "\n".join(str(a.text) for a in fig.layout.annotations if a.text)
    for expected in (
        "LT-P1",
        "<b>TBT</b> · 57.5 kt",
        "<b>BT</b> · 38.3 kt",
        "LT-P2",
        "<b>BT</b> · 57.5 kt",
        "<b>MT</b> · 38.3 kt",
    ):
        assert expected in labels

    hover = [
        str(text)
        for trace in fig.data
        for text in (trace.hovertext
                     if isinstance(trace.hovertext, (list, tuple)) else [])
        if str(text).startswith("<b>LT-P")]
    assert hover == [
        "<b>LT-P1</b><br>95,756 / 191,512 t · 50%",
        "<b>LT-P1 · TBT</b><br>57,454 t · 60%",
        "<b>LT-P1 · BT</b><br>38,302 t · 40%",
        "<b>LT-P2</b><br>95,756 / 191,512 t · 50%",
        "<b>LT-P2 · BT</b><br>57,454 t · 60%",
        "<b>LT-P2 · MT</b><br>38,302 t · 40%",
    ]
    assert all(text.count("<br>") == 1 for text in hover)


def test_distribution_chart_has_exact_louta_profile_shares(
        s3, louta_state):
    rows = grade_profile_distribution_rows(
        louta_state, louta_geometry(s3))
    shares = {
        (row["pile_id"], row["profile_id"]): row["mass_fraction"]
        for row in rows
    }
    # all multi-profile stages: reference-only 102MG mine storage, then
    # model-backed homogenized ore and product planning lines
    assert shares == {
        ("102MG-SP-01", "RAW-BT"): pytest.approx(0.5),
        ("102MG-SP-01", "RAW-BT-HIGH-CD"): pytest.approx(0.5),
        ("102MG-SP-02", "RAW-BT"): pytest.approx(0.5),
        ("102MG-SP-02", "RAW-BT-LOW-CD"): pytest.approx(0.5),
        ("110LG-OL1", "RAW-BT"): pytest.approx(0.5),
        ("110LG-OL1", "RAW-BT-HIGH-CD"): pytest.approx(0.5),
        ("110LG-OL2", "RAW-BT"): pytest.approx(0.5),
        ("110LG-OL2", "RAW-BT-LOW-CD"): pytest.approx(0.5),
        ("LT-P1", "TBT"): pytest.approx(0.6),
        ("LT-P1", "BT"): pytest.approx(0.4),
        ("LT-P2", "BT"): pytest.approx(0.6),
        ("LT-P2", "MT"): pytest.approx(0.4),
    }
    ore_masses = [row["mass_t"] for row in rows
                  if row["pile_id"].startswith("110LG")]
    assert ore_masses == pytest.approx([61_784.25] * 4)
    assert all("ASSUMED" in row["basis"] and not row["actual"]
               for row in rows if row["pile_id"].startswith("110LG"))
    fig = grade_profile_distribution_figure(rows)
    assert fig is not None
    assert {trace.name for trace in fig.data} == {
        "G", "H-Cd", "L-Cd", "TBT", "BT", "MT"}
    assert fig.layout.barmode == "stack"
    assert list(fig.layout.xaxis.range) == [0, 100]
    text_by_profile = {trace.name: list(trace.text) for trace in fig.data}
    assert text_by_profile == {
        "G": ["25 kt", "25 kt", "61.8 kt", "61.8 kt", "", ""],
        "H-Cd": ["25 kt", "", "61.8 kt", "", "", ""],
        "L-Cd": ["", "25 kt", "", "61.8 kt", "", ""],
        "TBT": ["", "", "", "", "57.5 kt", ""],
        "BT": ["", "", "", "", "38.3 kt", "57.5 kt"],
        "MT": ["", "", "", "", "", "38.3 kt"],
    }
    for trace in fig.data:
        assert trace.hovertemplate.count("<br>") == 1
        assert "customdata[1]" not in trace.hovertemplate


def test_grade_filter_dims_only_non_selected_profile_bands(
        s3, louta_state):
    fig = process_mimic_figure(
        louta_state, louta_geometry(s3),
        MimicOpts(grade_filter=["BT"]))
    fills = [getattr(shape, "fillcolor", None)
             for shape in fig.layout.shapes]
    assert grade_color("BT") in fills
    assert "#dfe4e7" in fills


def test_raw_and_product_profiles_never_cross_material_states(s3):
    """Ore piles carry ONLY shared RAW-BT-* profiles; product lines ONLY the
    washed TBT/BT/MT demonstration vectors. A washed grade vector must never
    describe raw ore, and vice versa."""
    geo = louta_geometry(s3)
    for pile_id in ("102MG-SP-01", "102MG-SP-02"):
        pile = next(p for p in geo.piles if p.pile_id == pile_id)
        assert not pile.modelled           # display/reference, never a space
        assert pile.display_grade_components
        assert all(component.grade_id.startswith("RAW-BT-")
                   for component in pile.display_grade_components), pile_id
        # the Louta ore profiles keep their own 15 % source moisture
        assert all(component.quality["moisture"] == 15.0
                   for component in pile.display_grade_components)
    state = build_process_mimic_state(s3, basis="initial", bucket=0)
    for pile_id in ("110LG-OL1", "110LG-OL2"):
        pile = next(p for p in geo.piles if p.pile_id == pile_id)
        assert pile.modelled
        assert not pile.display_grade_components
        segments = _pile(state, pile_id).segments
        assert segments and all(
            segment.grade.startswith("RAW-BT-") for segment in segments)
        assert all(segment.quality["moisture"] == 15.0
                   for segment in segments)
    for pile_id in ("LT-P1", "LT-P2"):
        pile = next(p for p in geo.piles if p.pile_id == pile_id)
        assert pile.modelled
        assert not pile.display_grade_components
        assert not pile.display_grade_id
    product = {seg.grade
               for pile_id in ("LT-P1", "LT-P2")
               for seg in (_pile(state, pile_id).segments or [])}
    assert product <= {"TBT", "BT", "MT"}
    assert not any(g.startswith("RAW-BT-") for g in product)
