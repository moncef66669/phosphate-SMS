# -*- coding: utf-8 -*-
"""Focused UI contracts for compact washing and corridor annotations.

The detailed timing/evidence remains available in geometry metadata and hover.
These tests only constrain what is printed directly on the process mimic.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mph_sim import datasets  # noqa: E402
from ui import process_mimic as mimic_ui  # noqa: E402
from ui.layout_geometry import geometry_for  # noqa: E402


@pytest.fixture(scope="module")
def s3():
    return datasets.end_to_end()


def _initial_figure(s3, view):
    state = mimic_ui.build_process_mimic_state(
        s3, basis="initial", bucket=0)
    return mimic_ui.process_mimic_figure(
        state, geometry_for(s3, view))


def _annotation_at(fig, x, y):
    matches = [
        str(annotation.text)
        for annotation in fig.layout.annotations
        if annotation.text
        and float(annotation.x) == pytest.approx(x)
        and float(annotation.y) == pytest.approx(y)
    ]
    assert len(matches) == 1, (
        f"expected one annotation at ({x}, {y}), got {matches}")
    return matches[0]


def _hover_texts(fig):
    values = []
    for trace in fig.data:
        hover = trace.hovertext
        if hover is None:
            continue
        if isinstance(hover, (list, tuple)):
            values.extend(str(value) for value in hover if value)
        else:
            values.append(str(hover))
    return values


def test_each_washing_block_prints_one_compact_residence(s3):
    """Residence is written once below each washing block, not per line."""
    for view in ("LOUTA", "BENGUERIR", "GLOBAL", "CHAIN"):
        geo = geometry_for(s3, view)
        fig = _initial_figure(s3, view)
        lines = [
            line for line in geo.process_lines
            if line.line_id.startswith(("LT-WASH-", "BG-WASH-"))
        ]
        assert lines, view
        for line in lines:
            text = _annotation_at(
                fig, (line.x0 + line.x1) / 2, (line.y0 + line.y1) / 2)
            assert "residence" not in text.lower(), (
                view, line.line_id, text)

        groups = {
            line.line_id.split("-WASH-", 1)[0] for line in lines
        }
        residence = [
            annotation
            for annotation in fig.layout.annotations
            if annotation.text
            and "30 min residence" in str(annotation.text)
        ]
        assert len(residence) == len(groups), (view, residence, groups)
        for group in groups:
            grouped_lines = [
                line for line in lines
                if line.line_id.split("-WASH-", 1)[0] == group
            ]
            centre_x = (
                min(line.x0 for line in grouped_lines)
                + max(line.x1 for line in grouped_lines)
            ) / 2
            matching = [
                annotation for annotation in residence
                if float(annotation.x) == pytest.approx(centre_x)
            ]
            assert len(matching) == 1, (view, group, matching)
            assert float(matching[0].y) < min(
                line.y0 for line in grouped_lines), (view, group, matching[0])


def test_washing_timing_strip_is_not_mounted_above_the_graph():
    """Residence belongs below each washing block, not in a graph header."""
    assert not hasattr(mimic_ui, "washing_time_strip_html")
    render_source = inspect.getsource(mimic_ui.render_process_mimic)
    assert "washing_time_strip" not in render_source
    assert "Washing-line timing" not in render_source


def test_corridor_canvas_labels_are_compact_and_details_stay_in_hover(s3):
    fig = _initial_figure(s3, "GLOBAL")
    annotations = [
        str(annotation.text)
        for annotation in fig.layout.annotations
        if annotation.text
    ]

    for corridor, transit_h in (("C1", 2), ("C2", 4)):
        labels = [
            text for text in annotations
            if text.startswith(f"<b>{corridor}")
            and "t/h" in text
        ]
        assert len(labels) == 1, (corridor, labels)
        label = labels[0]
        assert "0/2,600 t/h" in label
        assert f"{transit_h} h" in label
        assert label.count("2,600 t/h") == 1
        assert "%" not in label
        assert "in transit" not in label.lower()
        assert "next arrival" not in label.lower()

    canvas = " ".join(annotations).lower()
    assert "in transit" not in canvas
    assert "next arrival" not in canvas

    hover_texts = _hover_texts(fig)
    hover = " ".join(hover_texts).lower()
    for corridor in ("C1", "C2"):
        corridor_hover = [
            text.lower() for text in hover_texts
            if f"<b>{corridor}</b>" in text
            and "transit time" in text.lower()
        ]
        assert len(corridor_hover) == 1, (corridor, corridor_hover)
        assert "in transit" in corridor_hover[0]
        assert "next arrival" in corridor_hover[0]
    assert "in transit" in hover and "next arrival" in hover
