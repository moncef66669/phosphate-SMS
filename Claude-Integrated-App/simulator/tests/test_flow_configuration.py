"""Focused contracts for the model-backed flow-availability controls.

These tests deliberately stop at candidate construction and UI rendering.
They do not run the optimization model.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

from conftest import nav_to  # noqa: E402
from mph_sim import datasets  # noqa: E402
from mph_sim.domain import PlanResult  # noqa: E402
from mph_sim.flow_config import (destination_line_link_ids,  # noqa: E402
                                 destination_line_operating_state,
                                 destination_line_status,
                                 disabled_process_lines,
                                 effective_corridor_targets,
                                 process_line_operating_state,
                                 set_destination_line_enabled,
                                 set_destination_line_operating_state,
                                 set_process_line_enabled,
                                 set_process_line_operating_state)
from mph_sim.optimizer import build_candidates  # noqa: E402
from mph_sim.routes import RouteGraph  # noqa: E402
from ui import common, hmi_symbols as hs  # noqa: E402
from ui.flow_configuration import (flow_config_assets,  # noqa: E402
                                   flow_configuration_figure,
                                   set_flow_asset_enabled,
                                   _selected_key_from_event)
from ui.layout_geometry import geometry_for  # noqa: E402


APP = str(ROOT / "app.py")


@pytest.fixture
def scenario():
    return datasets.end_to_end()


def _assets_by_key(scenario, section: str):
    return {
        asset.key: asset
        for asset in flow_config_assets(
            scenario, geometry_for(scenario, section))
    }


def test_disabling_louta_line_removes_its_two_stages_and_halves_c1(scenario):
    datasets.apply_planning_demand_profile(scenario, "BALANCED")
    configured_corridors = dict(
        scenario.config["CORRIDOR_DISPATCH_TARGET_T"])
    configured_stages = dict(
        scenario.config["PROCESS_STAGE_OUTPUT_TARGET_T"])

    set_process_line_enabled(scenario, "LT-WASH-L1", False)
    candidates, _ = build_candidates(
        scenario, RouteGraph.from_scenario(scenario))
    active_stages = {
        candidate.process_stage
        for candidate in candidates
        if candidate.kind == "process"
    }

    assert "LOU-WASH-L1" not in active_stages
    assert "LOU-PRODUCT-STACK-L1" not in active_stages
    assert {"LOU-WASH-L2", "LOU-PRODUCT-STACK-L2"} <= active_stages
    assert effective_corridor_targets(scenario)["C1"] == pytest.approx(4_000)
    # Disabling is reversible: configured targets are retained unchanged.
    assert scenario.config["CORRIDOR_DISPATCH_TARGET_T"] == \
        configured_corridors
    assert scenario.config["PROCESS_STAGE_OUTPUT_TARGET_T"] == \
        configured_stages

    set_process_line_enabled(scenario, "LT-WASH-L1", True)
    restored, _ = build_candidates(
        scenario, RouteGraph.from_scenario(scenario))
    restored_stages = {
        candidate.process_stage
        for candidate in restored
        if candidate.kind == "process"
    }
    assert {"LOU-WASH-L1", "LOU-PRODUCT-STACK-L1"} <= restored_stages
    assert effective_corridor_targets(scenario)["C1"] == \
        pytest.approx(8_000)


def test_process_line_supports_available_disabled_and_future(scenario):
    line_id = "LT-WASH-L1"
    assert process_line_operating_state(scenario, line_id) == "AVAILABLE"

    set_process_line_operating_state(scenario, line_id, "DISABLED")
    assert process_line_operating_state(scenario, line_id) == "DISABLED"
    assert scenario.config["PROCESS_LINE_ENABLED"][line_id] is False

    set_process_line_operating_state(scenario, line_id, "FUTURE")
    assert process_line_operating_state(scenario, line_id) == "FUTURE"
    assert line_id in disabled_process_lines(scenario)

    set_process_line_operating_state(scenario, line_id, "AVAILABLE")
    assert process_line_operating_state(scenario, line_id) == "AVAILABLE"
    assert scenario.config["PROCESS_LINE_ENABLED"][line_id] is True


def test_machine_and_grouped_route_availability_round_trip(scenario):
    assets = _assets_by_key(scenario, "LOUTA")

    machine = assets["MACHINE|120LG-RC-01"]
    assert machine.configurable and machine.enabled
    set_flow_asset_enabled(scenario, machine, False)
    assert scenario.machine("120LG-RC-01").available is False
    assert _assets_by_key(
        scenario, "LOUTA")[machine.key].status == "DISABLED"
    set_flow_asset_enabled(scenario, machine, True)
    assert scenario.machine("120LG-RC-01").available is True

    route = assets["ROUTE|SL11"]
    assert route.configurable
    assert route.members == ("L-LT-SL11-P1", "L-LT-SL11-P2")
    assert all(scenario.link(link_id).available
               for link_id in route.members)
    set_flow_asset_enabled(scenario, route, False)
    assert all(not scenario.link(link_id).available
               for link_id in route.members)
    disabled = _assets_by_key(scenario, "LOUTA")[route.key]
    assert disabled.enabled is False
    assert disabled.status == "DISABLED"
    set_flow_asset_enabled(scenario, disabled, True)
    assert all(scenario.link(link_id).available
               for link_id in route.members)
    assert _assets_by_key(
        scenario, "LOUTA")[route.key].status == "ENABLED"


def test_pap_destination_line_switch_uses_real_links_and_retains_demand(
        scenario):
    link_ids = destination_line_link_ids(scenario, "PAP-1")
    assert link_ids == ("L-RTE-A-PAP", "L-RTE-B-PAP")
    assert destination_line_status(scenario, "PAP-1") == "ENABLED"
    demand_before = (
        scenario.destination("PAP-1").demand_default_tph,
        tuple(
            (entry.bucket, entry.rate_tph)
            for entry in scenario.demand
            if entry.dest_id == "PAP-1"
        ),
    )
    enabled_candidates, _ = build_candidates(
        scenario, RouteGraph.from_scenario(scenario))
    enabled_pap = [
        candidate for candidate in enabled_candidates
        if candidate.dest_id == "PAP-1"
    ]
    assert enabled_pap

    set_destination_line_enabled(scenario, "PAP-1", False)

    assert destination_line_status(scenario, "PAP-1") == "DISABLED"
    assert all(not scenario.link(link_id).available for link_id in link_ids)
    assert (
        scenario.destination("PAP-1").demand_default_tph,
        tuple(
            (entry.bucket, entry.rate_tph)
            for entry in scenario.demand
            if entry.dest_id == "PAP-1"
        ),
    ) == demand_before
    disabled_candidates, _ = build_candidates(
        scenario, RouteGraph.from_scenario(scenario))
    assert not [
        candidate for candidate in disabled_candidates
        if candidate.dest_id == "PAP-1"
    ]

    set_destination_line_enabled(scenario, "PAP-1", True)
    restored_candidates, _ = build_candidates(
        scenario, RouteGraph.from_scenario(scenario))
    assert destination_line_status(scenario, "PAP-1") == "ENABLED"
    assert len([
        candidate for candidate in restored_candidates
        if candidate.dest_id == "PAP-1"
    ]) == len(enabled_pap)


def test_pap2_future_available_state_gates_routes_not_its_template(scenario):
    link_ids = destination_line_link_ids(scenario, "PAP-2")
    assert link_ids == ("L-RTE-A-PAP2", "L-RTE-B-PAP2")
    assert destination_line_operating_state(scenario, "PAP-2") == "FUTURE"
    assert destination_line_status(scenario, "PAP-2") == "FUTURE"
    assert scenario.destination("PAP-2").demand_default_tph == \
        pytest.approx(scenario.destination("PAP-1").demand_default_tph)
    assert sum(
        scenario.demand_tph("PAP-2", bucket) * scenario.sim.dt_h
        for bucket in range(scenario.sim.horizon_buckets)
    ) == pytest.approx(0)
    assert all(
        not scenario.link(link_id).available
        and scenario.link(link_id).lifecycle == "FUTURE"
        for link_id in link_ids
    )
    future_candidates, _ = build_candidates(
        scenario, RouteGraph.from_scenario(scenario))
    assert not [
        candidate for candidate in future_candidates
        if candidate.dest_id == "PAP-2"
    ]

    set_destination_line_operating_state(scenario, "PAP-2", "AVAILABLE")

    assert destination_line_operating_state(scenario, "PAP-2") == "AVAILABLE"
    assert destination_line_status(scenario, "PAP-2") == "ENABLED"
    assert sum(
        scenario.demand_tph("PAP-2", bucket) * scenario.sim.dt_h
        for bucket in range(scenario.sim.horizon_buckets)
    ) == pytest.approx(7 * 36_000)
    assert all(
        scenario.link(link_id).available
        and scenario.link(link_id).lifecycle == "SELECTED"
        for link_id in link_ids
    )
    available_candidates, _ = build_candidates(
        scenario, RouteGraph.from_scenario(scenario))
    assert {
        candidate.machine for candidate in available_candidates
        if candidate.dest_id == "PAP-2"
    } == {"RC01-A", "RC01-B"}

    set_destination_line_operating_state(scenario, "PAP-2", "FUTURE")
    assert sum(
        scenario.demand_tph("PAP-2", bucket) * scenario.sim.dt_h
        for bucket in range(scenario.sim.horizon_buckets)
    ) == pytest.approx(0)
    assert scenario.destination("PAP-2").demand_default_tph == \
        pytest.approx(1_500)


def test_destination_line_supports_disabled_separately_from_future(scenario):
    demand_t = sum(
        scenario.demand_tph("PAP-1", bucket) * scenario.sim.dt_h
        for bucket in range(scenario.sim.horizon_buckets)
    )
    set_destination_line_operating_state(scenario, "PAP-1", "DISABLED")
    assert destination_line_operating_state(scenario, "PAP-1") == "DISABLED"
    assert destination_line_status(scenario, "PAP-1") == "DISABLED"
    assert sum(
        scenario.demand_tph("PAP-1", bucket) * scenario.sim.dt_h
        for bucket in range(scenario.sim.horizon_buckets)
    ) == pytest.approx(demand_t)

    set_destination_line_operating_state(scenario, "PAP-1", "FUTURE")
    assert destination_line_operating_state(scenario, "PAP-1") == "FUTURE"
    assert sum(
        scenario.demand_tph("PAP-1", bucket) * scenario.sim.dt_h
        for bucket in range(scenario.sim.horizon_buckets)
    ) == pytest.approx(0)


def test_benguerir_process_cards_and_st3_are_configurable(scenario):
    assets = _assets_by_key(scenario, "BENGUERIR")

    for line_id in ("BG-WASH-L1", "BG-WASH-L2", "BG-WASH-L3"):
        line = assets[f"LINE|{line_id}"]
        assert line.configurable is True
        assert line.enabled is True
        assert line.status == "ENABLED"

    line = assets["LINE|BG-WASH-L2"]
    demand_before = scenario.destination("BEN-WASH-L2").demand_default_tph
    set_flow_asset_enabled(scenario, line, False)
    assert _assets_by_key(
        scenario, "BENGUERIR")[line.key].status == "DISABLED"
    candidates, _ = build_candidates(
        scenario, RouteGraph.from_scenario(scenario))
    assert not {
        candidate.process_stage for candidate in candidates
    } & {
        "BEN-MINE-STACK-L2", "BEN-MINE-RECLAIM-L2",
        "BEN-RAW-STACK-L2", "BEN-WASH-L2",
        "BEN-PRODUCT-STACK-L2",
    }
    assert scenario.destination(
        "BEN-WASH-L2").demand_default_tph == demand_before
    set_flow_asset_enabled(
        scenario,
        _assets_by_key(scenario, "BENGUERIR")[line.key],
        True,
    )

    st3 = assets["MACHINE|ST3"]
    assert st3.configurable is True
    assert st3.status == "ENABLED"
    set_flow_asset_enabled(scenario, st3, False)
    assert scenario.machine("ST3").available is False
    assert _assets_by_key(
        scenario, "BENGUERIR")[st3.key].status == "DISABLED"


def test_configuration_figure_exposes_hit_keys_and_disabled_line_style(
        scenario):
    set_process_line_enabled(scenario, "LT-WASH-L1", False)
    selected_key = "LINE|LT-WASH-L1"
    figure, assets = flow_configuration_figure(
        scenario, "LOUTA", selected_key=selected_key)

    hit_trace = next(
        trace for trace in figure.data
        if trace.name == "configuration hit targets"
    )
    hit_keys = {
        str(custom[0])
        for custom in hit_trace.customdata
        if custom
    }
    assert hit_keys == {asset.key for asset in assets}
    assert selected_key in hit_keys

    line_shape = next(
        shape for shape in figure.layout.shapes
        if shape.name == "process-line:LT-WASH-L1"
    )
    disabled_style = hs.PROCESS_LINE_STATUS_STYLES["DISABLED"]
    assert line_shape.line.color == disabled_style["line"]
    assert line_shape.line.dash == disabled_style["dash"]
    assert line_shape.fillcolor == disabled_style["fill"]
    assert any(
        "LINE L1" in str(annotation.text)
        and "DISABLED" in str(annotation.text)
        for annotation in figure.layout.annotations
    )
    line_asset = next(
        asset for asset in assets
        if asset.key == selected_key
    )
    assert line_asset.configurable is True
    assert line_asset.status == "DISABLED"
    set_flow_asset_enabled(scenario, line_asset, True)
    restored = _assets_by_key(scenario, "LOUTA")[selected_key]
    assert restored.configurable is True
    assert restored.status == "ENABLED"


def test_plotly_selection_payload_resolves_to_the_asset_key():
    event = {
        "selection": {
            "points": [{"customdata": ["ROUTE|SL11"]}],
        },
    }
    assert _selected_key_from_event(event) == "ROUTE|SL11"


def test_advanced_route_diagram_loads_only_when_requested_without_solving():
    page = next(
        label for label in common.ALL_PAGE_LABELS
        if label.split(" ", 1)[0] == "4"
    )
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    nav_to(at, page)

    assert not at.exception, at.exception
    assert not [
        radio for radio in at.main.radio
        if radio.label == "Flow section"
    ]
    loader = next(
        toggle for toggle in at.main.toggle
        if toggle.label == "Load route diagram"
    )
    loader.set_value(True)
    at.run()

    section = next(
        radio for radio in at.main.radio
        if radio.label == "Flow section"
    )
    assert section.value == "LOUTA"
    assert section.options == ["Louta", "Benguerir", "MPH"]
    selector = next(
        item for item in at.main.selectbox
        if item.label == "Selected diagram element"
    )
    assert selector.value == "LINE|LT-WASH-L1"

    specs = [
        json.loads(element.proto.spec)
        for element in at.get("plotly_chart")
    ]
    configuration = next(
        spec for spec in specs
        if any(
            trace.get("name") == "configuration hit targets"
            for trace in spec.get("data", [])
        )
    )
    hit_trace = next(
        trace for trace in configuration["data"]
        if trace.get("name") == "configuration hit targets"
    )
    assert ["LINE|LT-WASH-L1"] in hit_trace["customdata"]
    assert any(
        button.label == "Disable selected" and not button.disabled
        for button in at.main.button
    )

    # Availability edits are planning inputs: a visible plan must never stay
    # attached to the now-changed scenario.
    at.session_state["plan"] = PlanResult(status="FEASIBLE")
    disable = next(
        button for button in at.main.button
        if button.label == "Disable selected"
    )
    disable.click()
    at.run()
    assert not at.exception, at.exception
    assert at.session_state["plan"] is None
    assert at.session_state["scenario"].config[
        "PROCESS_LINE_ENABLED"]["LT-WASH-L1"] is False
    assert any(
        "previous plan cleared" in str(message.value)
        for message in at.main.success
    )
