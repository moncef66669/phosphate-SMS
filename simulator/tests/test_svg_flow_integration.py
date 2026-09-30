"""Focused integration checks for the production SVG flow diagram."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from mph_sim import datasets
from mph_sim.domain import Job, PlanResult
from mph_sim.flow_config import set_process_line_enabled
from ui import process_mimic
from ui.flow_svg import svg_mimic, svg_viewer
from ui.layout_geometry import geometry_for
from ui.process_mimic import (
    MimicOpts,
    build_process_mimic_state,
    configured_mimic_geometry,
)

SVG_NS = "{http://www.w3.org/2000/svg}"


def _titles(svg: str) -> list[str]:
    return [
        node.text or ""
        for node in ET.fromstring(svg).iter(f"{SVG_NS}title")
    ]


def _title(svg: str, first_line: str) -> str:
    return next(
        title for title in _titles(svg)
        if title.splitlines() and title.splitlines()[0] == first_line
    )


def test_chain_sheet_keeps_the_claude_layout_and_progressive_labels():
    sc = datasets.end_to_end()
    state = build_process_mimic_state(
        sc, None, None, basis="initial", bucket=0)
    svg, aspect = svg_mimic.build(
        sc, state, geometry_for(sc, "CHAIN"), MimicOpts(),
        dark=False, harmonize=False)

    ET.fromstring(svg)
    assert 0.5 < aspect < 0.7
    for label in (
        "LOUTA", "102MG SCREENED-PRODUCT STORAGE", "MINE ORE",
        "BG-TRAIN", "PAP / DRYING", "DRY-FEED",
    ):
        assert label in svg
    assert 'class="lod1"' in svg
    assert 'class="lod2"' in svg
    assert 'class="lod3"' in svg

    document = svg_viewer.html(
        svg, height=process_mimic.SVG_MIMIC_VIEWER_HEIGHT, dark=False)
    for behavior in ("wheel", "pointerdown", "dblclick", "viewBox"):
        assert behavior in document
    assert svg_viewer.LOD_THRESHOLDS == {2: 1.25, 3: 2.0}


def test_chain_sheet_contains_every_configured_flow_element():
    sc = datasets.end_to_end()
    state = build_process_mimic_state(
        sc, None, None, basis="initial", bucket=0)
    geo = geometry_for(sc, "CHAIN")
    svg, _ = svg_mimic.build(
        sc, state, geo, MimicOpts(), dark=False, harmonize=False)

    expected_labels = (
        [pile.pile_id for pile in geo.piles]
        + [rail.rail_id for rail in geo.rails]
        + [machine.machine_id for machine in geo.machines]
        + [route.tag or route.cid for route in geo.conveyors]
        + [line.label for line in geo.process_lines]
        + [item.label for item in geo.equipment if item.label]
        + [zone.label for zone in geo.zones if zone.label]
    )
    for label in expected_labels:
        rendered_label = svg_mimic._strip_tbc(label)
        assert rendered_label in svg, label


def test_plan_machine_state_reaches_the_svg_lamp_and_hover():
    sc = datasets.mph_phosphate()
    job = Job(
        "J-R", "RC01-A", "reclaim", "HP-05",
        dest_id="PAP-1", grade="TBT", rate_tph=1200,
        tonnage_t=1200, start_bucket=0, end_bucket=0,
        path=["L-RCL-HP-05", "L-H-A", "L-RTE-A-PAP"],
    )
    plan = PlanResult("FEASIBLE", jobs=[job])
    state = build_process_mimic_state(
        sc, plan, None, basis="plan", bucket=0)

    svg, _ = svg_mimic.build(
        sc, state, geometry_for(sc, "MPH"), MimicOpts(),
        dark=False, basis="Plan timeline")

    assert "RC01-A" in svg
    assert "state Reclaiming" in svg
    assert svg_mimic.LIGHT.ok in svg
    assert "rate 1,200 /" in svg
    machine_hover = _title(svg, "RC01-A")
    assert machine_hover.splitlines() == [
        "RC01-A",
        "state Reclaiming · job J-R → HP-05 → PAP-1",
        "position R500",
        "rate 1,200 / 2,500 t/h",
    ]
    assert "state running" in svg
    assert "H01\nstate running" in svg

    initial = build_process_mimic_state(
        sc, None, None, basis="initial", bucket=0)
    idle_svg, _ = svg_mimic.build(
        sc, initial, geometry_for(sc, "MPH"), MimicOpts(), dark=False)
    assert svg.count(f'stroke="{svg_mimic.LIGHT.ok}"') > \
        idle_svg.count(f'stroke="{svg_mimic.LIGHT.ok}"')

    selected_svg, _ = svg_mimic.build(
        sc, state, geometry_for(sc, "MPH"),
        MimicOpts(selected_job="J-R"), dark=False)
    assert selected_svg.count(f'stroke="{svg_mimic.LIGHT.info}"') > \
        svg.count(f'stroke="{svg_mimic.LIGHT.info}"')


def test_planning_disabled_machine_is_not_presented_as_idle():
    sc = datasets.end_to_end()
    machine = next(m for m in sc.machines if m.id == "BG-MINE-ST-1")
    machine.planning_enabled = False
    state = build_process_mimic_state(
        sc, None, None, basis="initial", bucket=0)
    svg, _ = svg_mimic.build(
        sc, state, geometry_for(sc, "BENGUERIR"),
        MimicOpts(), dark=False)

    hover = _title(svg, "BG-MINE-ST-1")
    assert "\nstate Planning disabled" in hover
    assert len(hover.splitlines()) <= 4
    assert "no solver jobs" not in hover


def test_stockpile_hover_is_compact_and_keeps_tonnage_grade_position():
    sc = datasets.mph_phosphate()
    state = build_process_mimic_state(
        sc, None, None, basis="initial", bucket=0)
    svg, _ = svg_mimic.build(
        sc, state, geometry_for(sc, "MPH"), MimicOpts(), dark=False)
    hover = next(
        title for title in _titles(svg)
        if title.startswith("HP-01\n") and "\ntonnage " in title)

    assert hover.splitlines()[0] == "HP-01"
    assert "\ntonnage " in hover
    assert "\ngrade " in hover
    assert "\nposition R0–R1000" in hover
    assert len(hover.splitlines()) == 4
    for removed_detail in ("quality ", "\nin ", "\nevidence "):
        assert removed_detail not in hover


def test_hover_targets_cover_assets_and_follow_routed_lines():
    sc = datasets.mph_phosphate()
    state = build_process_mimic_state(
        sc, None, None, basis="initial", bucket=0)
    svg, _ = svg_mimic.build(
        sc, state, geometry_for(sc, "MPH"), MimicOpts(), dark=False)

    pile = _title(svg, "HP-01")
    machine = _title(svg, "RC01-A")
    rail = _title(svg, "M16A-A-T06")
    route = _title(svg, "→ PAP-1")
    destination = _title(svg, "PAP-1 (wet process)")

    assert pile.splitlines()[1:] == [
        "tonnage 80,000 t (64% of 125,000 t)",
        "grade TBT",
        "position R0–R1000",
    ]
    assert machine.splitlines() == [
        "RC01-A", "state Idle", "position R500",
        "rate 0 / 2,500 t/h",
    ]
    assert "type stacker machine line" in rail
    assert "serves HP-01, HP-05" in rail
    assert route.splitlines() == [
        "→ PAP-1", "type conveyor / route", "state idle",
    ]
    assert destination.splitlines() == [
        "PAP-1 (wet process)",
        "state configured",
        "type destination / process block",
    ]

    root = ET.fromstring(svg)
    route_target = next(
        node for node in root.iter(f"{SVG_NS}path")
        if node.attrib.get("class") == "hover-target"
        and node.attrib.get("data-tooltip", "").startswith("→ PAP-1\n")
    )
    assert float(route_target.attrib["stroke-width"]) >= 16
    assert route_target.attrib["pointer-events"] == "stroke"


def test_mixed_grade_and_process_line_hovers_are_exact_and_compact():
    sc = datasets.end_to_end()
    state = build_process_mimic_state(
        sc, None, None, basis="initial", bucket=0)
    svg, _ = svg_mimic.build(
        sc, state, geometry_for(sc, "LOUTA"), MimicOpts(), dark=False)

    mixed = _title(svg, "110LG-OL1")
    assert "grade RAW-BT-GLOBAL / RAW-BT-HIGH-CD" in mixed
    assert len(mixed.splitlines()) == 4

    line = _title(svg, "LINE L1")
    assert line.splitlines() == [
        "LINE L1",
        "type process line · LT-WASH-L1",
        "status OPERATING (configured)",
        "minimum residence 30 min",
    ]
    assert all(term not in line.lower()
               for term in ("evidence", "source", "document"))


def test_viewer_displays_immediate_tooltip_card_from_svg_metadata():
    sc = datasets.mph_phosphate()
    state = build_process_mimic_state(
        sc, None, None, basis="initial", bucket=0)
    svg, _ = svg_mimic.build(
        sc, state, geometry_for(sc, "MPH"), MimicOpts(), dark=False)
    document = svg_viewer.html(svg, height=720, dark=False)

    assert 'id="hovercard" role="tooltip"' in document
    assert "hover = details" in document
    assert "closest('.hover-target')" in document
    assert "getAttribute('data-tooltip')" in document
    assert "pointerleave" in document


def test_every_hover_target_has_matching_accessible_metadata_without_tbc():
    sc = datasets.end_to_end()
    state = build_process_mimic_state(
        sc, None, None, basis="initial", bucket=0)
    for view in ("CHAIN", "LOUTA", "BENGUERIR", "MPH"):
        svg, _ = svg_mimic.build(
            sc, state, geometry_for(sc, view), MimicOpts(), dark=False)
        root = ET.fromstring(svg)
        targets = [
            node for node in root.iter()
            if node.attrib.get("class") == "hover-target"
        ]
        assert targets, view
        for target in targets:
            title = target.find(f"{SVG_NS}title")
            assert title is not None
            public = title.text or ""
            assert target.attrib["data-tooltip"] == public
            assert target.attrib["aria-label"] == public
            assert "TBC" not in public.upper()


def test_disabled_process_line_is_red_in_the_monitor_drawing():
    sc = datasets.end_to_end()
    set_process_line_enabled(sc, "LT-WASH-L1", False)
    geo = configured_mimic_geometry(sc, "LOUTA")
    line = next(item for item in geo.process_lines
                if item.line_id == "LT-WASH-L1")
    assert line.status == "DISABLED"

    state = build_process_mimic_state(
        sc, None, None, basis="initial", bucket=0)
    svg, _ = svg_mimic.build(sc, state, geo, MimicOpts(), dark=False)
    assert "✕ DISABLED" in svg
    assert "status DISABLED" in svg
    assert svg_mimic.LIGHT.alarm in svg


def test_monitor_layout_opens_the_entire_chain_by_default(monkeypatch):
    captured = {}

    def fake_render(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(process_mimic, "render_process_mimic", fake_render)
    process_mimic.render_global_flow(datasets.end_to_end(), None, None)

    assert captured["views"] == ["CHAIN", "LOUTA", "BENGUERIR", "MPH"]
    assert captured["default_view"] == "CHAIN"
    assert captured["redesign"] is True
    assert captured["collapse_controls"] is True
