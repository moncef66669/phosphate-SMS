# -*- coding: utf-8 -*-
"""Focused regressions for the operator state code and timing metadata.

These checks deliberately stay in the display/configuration layer. Both mine
chains enforce residence through process-stage dependencies, so none of the
tests solve or re-baseline a plan.
"""
from __future__ import annotations

import json
from pathlib import Path

import plotly.graph_objects as go
import pytest

from mph_sim import datasets
from ui import hmi_symbols as hs
from ui.layout_geometry import geometry_for, m16_geometry
from ui.process_mimic import (build_process_mimic_state,
                              process_mimic_figure)
from ui.stockyard_monitor import build_monitor_state


def _belt_style(state: str):
    fig = go.Figure()
    hs.conveyor(fig, [(0.0, 0.0), (10.0, 0.0)], state=state,
                tag="TEST")
    assert len(fig.data) == 2
    return {
        (trace.line.color, trace.line.dash)
        for trace in fig.data
    }


def test_asset_status_code_has_all_four_accessible_states():
    styles = hs.ASSET_STATUS_STYLES
    assert tuple(styles) == ("ACTIVE", "IDLE", "UNAVAILABLE", "FUTURE")
    assert {
        name: (style["color"], style["icon"], style["dash"])
        for name, style in styles.items()
    } == {
        "ACTIVE": (hs.RUN_GREEN, "●", None),
        "IDLE": (hs.IDLE_GREY, "·", None),
        "UNAVAILABLE": (hs.FAULT_RED, "✕", None),
        "FUTURE": (hs.FUTURE_VIOLET, "◇", "dot"),
    }
    assert all(style["label"] == name for name, style in styles.items())


@pytest.mark.parametrize("state", ("tripped", "unavailable"))
def test_real_conveyor_unavailability_is_red(state):
    assert _belt_style(state) == {(hs.FAULT_RED, None)}


def test_filtered_conveyor_is_grey_not_a_false_outage():
    assert _belt_style("dimmed") == {(hs.STATIC_LIGHT, None)}


def test_future_conveyor_is_violet_and_dotted():
    assert _belt_style("future") == {(hs.FUTURE_VIOLET, "dot")}


def test_future_m16_machines_are_explicit_and_render_violet_lamps():
    sc = datasets.mph_phosphate()
    geo = m16_geometry(sc, show_future=True)
    future = [machine for machine in geo.machines if machine.future]
    assert {machine.machine_id for machine in future} == {
        "RC01-C*", "ST01-D*",
    }
    assert all(machine.display for machine in future)

    state = build_process_mimic_state(sc)
    fig = process_mimic_figure(state, geo)
    violet_lamps = [
        shape for shape in fig.layout.shapes
        if shape.type == "circle" and shape.fillcolor == hs.FUTURE_VIOLET
    ]
    assert len(violet_lamps) >= len(future)
    annotations = [str(a.text) for a in fig.layout.annotations if a.text]
    assert annotations.count("◇") >= len(future)


def test_washing_lines_keep_assumed_line_and_documented_scrubber_times():
    sc = datasets.end_to_end()
    for line in geometry_for(sc, "LOUTA").process_lines:
        assert line.treatment_time == (
            "line residence 30 min (ASSUMED) · "
            "documented scrubber step 2.5–3 min"
        )
        assert "project-team assumption" in line.treatment_time_source
        assert "§8.2.1 p.21" in line.treatment_time_source

    for line in geometry_for(sc, "BENGUERIR").process_lines:
        assert line.treatment_time == (
            "line residence 30 min (ASSUMED) · "
            "documented scrubber step 3 min"
        )
        assert "project-team assumption" in line.treatment_time_source
        assert "§7.1 p.19" in line.treatment_time_source
        assert "§7.3.1 p.25" in line.treatment_time_source


def test_washing_residence_contract_is_site_specific():
    sc = datasets.end_to_end()
    assert sc.config["WASHING_LINE_RESIDENCE_TIME_H"] == {
        "LOUTA": 0.5,
        "BENGUERIR": 0.5,
    }
    assert sc.config["WASHING_LINE_RESIDENCE_TIME_MODELLED"] == {
        "LOUTA": True,
        "BENGUERIR": True,
    }

    assumption = next(a for a in sc.assumptions if a.id == "S3-A26")
    assert "30 min (0.5 h)" in assumption.text
    assert "explicit ≥30 min stage dependency" in assumption.text
    assert "Benguerir applies the same explicit 30-minute dependency" in \
        assumption.text
    assert assumption.placeholder == (
        "WASHING_LINE_RESIDENCE_TIME_MODELLED = "
        "{LOUTA: True, BENGUERIR: True}"
    )
    assert assumption.impact == (
        "validated minute windows and product-stack earliest start for both "
        "mine washing chains"
    )


def test_exported_s3_snapshot_carries_washing_residence_assumption():
    snapshot_path = (
        Path(__file__).resolve().parents[1]
        / "sample_data" / "s3" / "scenario.json"
    )
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["config"]["WASHING_LINE_RESIDENCE_TIME_H"] == {
        "LOUTA": 0.5,
        "BENGUERIR": 0.5,
    }
    assert snapshot["config"]["WASHING_LINE_RESIDENCE_TIME_MODELLED"] == {
        "LOUTA": True,
        "BENGUERIR": True,
    }
    assumption = next(
        item for item in snapshot["assumptions"] if item["id"] == "S3-A26"
    )
    assert "30 min (0.5 h)" in assumption["text"]
    assert "explicit ≥30 min stage dependency" in assumption["text"]
    assert "Benguerir applies the same explicit 30-minute dependency" in \
        assumption["text"]


def test_hot_loaded_s3_migration_restores_washing_residence_metadata():
    stale = datasets.end_to_end()
    stale.config["BUILTIN_BASELINE_REVISION"] = "stale-before-wash-residence"
    stale.config.pop("WASHING_LINE_RESIDENCE_TIME_H")
    stale.config.pop("WASHING_LINE_RESIDENCE_TIME_BASIS")
    stale.config.pop("WASHING_LINE_RESIDENCE_TIME_MODELLED")
    stale.assumptions = [
        assumption for assumption in stale.assumptions
        if assumption.id != "S3-A26"
    ]

    assert datasets.migrate_loaded_builtin(stale) == "metadata"
    assert stale.config["WASHING_LINE_RESIDENCE_TIME_H"] == {
        "LOUTA": 0.5,
        "BENGUERIR": 0.5,
    }
    assert stale.config["WASHING_LINE_RESIDENCE_TIME_MODELLED"] == {
        "LOUTA": True,
        "BENGUERIR": True,
    }
    assert any(assumption.id == "S3-A26" for assumption in stale.assumptions)


def test_corridor_operator_labels_use_concise_transit_time_only():
    sc = datasets.end_to_end()
    geo = geometry_for(sc, "GLOBAL")
    corridor_sub = {
        conveyor.cid: conveyor.sub
        for conveyor in geo.conveyors
        if conveyor.cid in ("C1", "C2")
    }
    assert corridor_sub["C1"] == "transit 2 h"
    assert corridor_sub["C2"] == "transit 4 h"

    monitor = build_monitor_state(sc)
    outlet_text = " ".join(
        label
        for site in ("LOUTA", "BENGUERIR")
        for label, _enabled, _note in monitor.sites[site].outlets
    )
    assert "transit time = 2 h" in outlet_text
    assert "transit time = 4 h" in outlet_text

    operator_text = " ".join(corridor_sub.values()) + " " + outlet_text
    for stale_word in ("provisional", "nearer", "farther"):
        assert stale_word not in operator_text.lower()
