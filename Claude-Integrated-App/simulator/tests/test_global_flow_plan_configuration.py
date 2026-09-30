"""Focused checks for click-backed global Plan resource configuration."""
from __future__ import annotations

import pytest

from mph_sim import datasets
from mph_sim.flow_config import disabled_process_lines
from ui.flow_configuration import (
    _scheduled_change_groups,
    add_planning_resource_period_change,
    planning_flow_snapshot,
    set_planning_resource_available,
    set_planning_resource_rate,
)
from ui.flow_svg import redesign_adapter, redesign_viewer
from ui.process_mimic import (
    MimicOpts,
    build_process_mimic_state,
    configured_mimic_geometry,
)


def _resource(resources, kind: str, label: str):
    return next(item for item in resources
                if item.kind == kind and item.label == label)


def test_plan_uses_global_monitor_handoff_and_resolves_visual_aliases():
    scenario = datasets.end_to_end()
    metadata, resources, click_map = planning_flow_snapshot(scenario)
    handoff = redesign_viewer.handoff_snapshot(metadata)

    assert metadata["planningMode"] is True
    assert metadata["displayMode"] == "routing-availability"
    assert {item["id"] for item in handoff["machines"]} >= {
        "M16-RC-A", "M16-RC-B",
    }
    # Both visual occurrences resolve to the one production reclaimer.
    assert click_map[("machine", "M16-RC-A")] == "MACHINE|RC01-A"
    assert click_map[("machine", "M16-RC-B")] == "MACHINE|RC01-A"

    route = next(item for item in handoff["routes"]
                 if item["id"] == "L1 PRODUCT")
    assert route["memberLinkIds"] == [
        "L-LT-SL11-P1", "L-LT-SL11-P2",
    ]
    resource = _resource(resources, "CONVEYOR", "L1 PRODUCT")
    assert resource.canonical_ids == tuple(route["memberLinkIds"])


def test_plan_and_monitor_share_the_same_global_visual_asset_set():
    """Planning must decorate, not replace, the Monitor flow schematic."""
    scenario = datasets.end_to_end()
    planning_metadata, _, _ = planning_flow_snapshot(scenario)
    planning_handoff = redesign_viewer.handoff_snapshot(planning_metadata)

    monitor_geometry = configured_mimic_geometry(
        scenario, "CHAIN", show_future=True)
    monitor_state = build_process_mimic_state(
        scenario, basis="initial", bucket=0)
    monitor_metadata = redesign_adapter.build_snapshot(
        scenario,
        monitor_state,
        monitor_geometry,
        MimicOpts(
            show_flows=False,
            show_tags=True,
            show_evidence=False,
        ),
        basis="initial",
    )
    monitor_handoff = redesign_viewer.handoff_snapshot(monitor_metadata)

    assert planning_metadata["view"]["id"] == "CHAIN"
    for collection in (
        "piles", "machines", "routes", "processLines", "destinations",
    ):
        assert {
            item["id"] for item in planning_handoff[collection]
        } == {
            item["id"] for item in monitor_handoff[collection]
        }, collection


def test_every_editable_visual_resource_has_a_stable_click_round_trip():
    scenario = datasets.end_to_end()
    _, resources, click_map = planning_flow_snapshot(scenario)
    editable = [resource for resource in resources if resource.configurable]

    assert {resource.kind for resource in editable} >= {
        "MACHINE", "CONVEYOR", "PROCESS LINE", "RECEIVING PLANT",
    }
    for resource in editable:
        visual_key = (resource.visual_kind, resource.visual_id)
        assert click_map[visual_key] == resource.key
        focused, _, _ = planning_flow_snapshot(scenario, resource)
        assert focused["initialSelection"] == {
            "kind": resource.visual_kind,
            "id": resource.visual_id,
        }


def test_disabled_resource_statuses_and_rates_reach_global_drawing():
    scenario = datasets.end_to_end()
    _, resources, _ = planning_flow_snapshot(scenario)
    selected = {
        "machines": _resource(resources, "MACHINE", "RC01-A"),
        "routes": _resource(resources, "CONVEYOR", "L1 PRODUCT"),
        "processLines": _resource(
            resources, "PROCESS LINE", "LT-WASH-L1"),
        "destinations": _resource(
            resources, "RECEIVING PLANT", "PAP-1"),
    }
    for resource in selected.values():
        set_planning_resource_available(scenario, resource, False)

    metadata, _, click_map = planning_flow_snapshot(scenario)
    handoff = redesign_viewer.handoff_snapshot(metadata)
    for collection, resource in selected.items():
        row = next(
            item for item in handoff[collection]
            if item["id"] == resource.visual_id
        )
        assert row["configurable"] is True
        assert row["baselineState"] == "UNAVAILABLE"
        assert row["baseRateTph"] is not None
        assert float(row["baseRateTph"]) > 0
        assert click_map[(resource.visual_kind, resource.visual_id)] == \
            resource.key


def test_whole_run_state_and_rate_mutate_canonical_resources_only():
    scenario = datasets.end_to_end()
    _, resources, _ = planning_flow_snapshot(scenario)

    machine = _resource(resources, "MACHINE", "RC01-A")
    set_planning_resource_available(scenario, machine, False)
    set_planning_resource_rate(scenario, machine, 1_900)
    assert scenario.machine("RC01-A").available is False
    assert scenario.machine("RC01-A").rate_max_tph == pytest.approx(1_900)

    conveyor = _resource(resources, "CONVEYOR", "L1 PRODUCT")
    set_planning_resource_available(scenario, conveyor, False)
    set_planning_resource_rate(scenario, conveyor, 850)
    assert all(not scenario.link(link_id).available
               for link_id in conveyor.canonical_ids)
    assert all(scenario.link(link_id).capacity_tph == pytest.approx(850)
               for link_id in conveyor.canonical_ids)

    line = _resource(resources, "PROCESS LINE", "LT-WASH-L1")
    set_planning_resource_available(scenario, line, False)
    set_planning_resource_rate(scenario, line, 510)
    assert "LT-WASH-L1" in disabled_process_lines(scenario)
    assert scenario.process_line_capacity_tph("LT-WASH-L1") == \
        pytest.approx(510)


def test_receiving_plant_outage_retains_demand_and_rate_is_separate():
    scenario = datasets.end_to_end()
    _, resources, _ = planning_flow_snapshot(scenario)
    plant = _resource(resources, "RECEIVING PLANT", "PAP-1")
    demand_before = [
        scenario.demand_tph("PAP-1", bucket)
        for bucket in range(scenario.sim.horizon_buckets)
    ]

    set_planning_resource_available(scenario, plant, False)
    set_planning_resource_rate(scenario, plant, 1_250)

    assert [scenario.demand_tph("PAP-1", bucket)
            for bucket in range(scenario.sim.horizon_buckets)] == demand_before
    assert scenario.process_line_capacity_tph("PAP-1") == \
        pytest.approx(1_250)


def test_exact_period_outage_and_rate_caps_apply_to_every_route_member():
    scenario = datasets.end_to_end()
    _, resources, _ = planning_flow_snapshot(scenario)
    conveyor = _resource(resources, "CONVEYOR", "L1 PRODUCT")

    add_planning_resource_period_change(
        scenario, conveyor, action="UNAVAILABLE",
        start_minute=90, end_minute=135, reason="belt inspection")
    add_planning_resource_period_change(
        scenario, conveyor, action="RATE_CAP",
        start_minute=180, end_minute=240, rate_tph=700)

    assert len(scenario.calendars) == 2 * len(conveyor.scopes)
    for scope in conveyor.scopes:
        assert scenario.calendar_scope_blocked(scope, minute=100)
        assert not scenario.calendar_scope_blocked(scope, minute=150)
        assert scenario.resource_rate_cap_tph(
            scope, minute=200) == pytest.approx(700)


def test_parallel_visual_conveyor_rate_remains_an_aggregate_ceiling():
    scenario = datasets.end_to_end()
    _, resources, _ = planning_flow_snapshot(scenario)
    collector = _resource(resources, "CONVEYOR", "M16 OUT COLLECTOR")

    assert collector.aggregation_mode == "parallel"
    assert collector.base_rate_tph == pytest.approx(3_000)
    set_planning_resource_rate(scenario, collector, 2_400)
    assert sum(scenario.link(link_id).capacity_tph
               for link_id in collector.canonical_ids) == pytest.approx(2_400)

    add_planning_resource_period_change(
        scenario, collector, action="RATE_CAP",
        start_minute=60, end_minute=120, rate_tph=1_800)
    assert sum(
        scenario.resource_rate_cap_tph(scope, minute=90) or 0
        for scope in collector.scopes
    ) == pytest.approx(1_800)


def test_grouped_conveyor_change_is_one_operator_event():
    scenario = datasets.end_to_end()
    _, resources, _ = planning_flow_snapshot(scenario)
    collector = _resource(resources, "CONVEYOR", "M16 OUT COLLECTOR")
    add_planning_resource_period_change(
        scenario, collector, action="RATE_CAP",
        start_minute=60, end_minute=120, rate_tph=1_800,
        reason="planned corridor derate")

    rows = _scheduled_change_groups(scenario, resources)
    collector_rows = [row for row in rows
                      if row["Resource"] == "M16 OUT COLLECTOR"]
    assert len(collector_rows) == 1
    assert collector_rows[0]["Effect"] == \
        "Rate cap 1,800 physical t/h"
    assert len(collector_rows[0]["_entries"]) == len(collector.scopes)


def test_whole_run_maintenance_is_explicit_in_planning_snapshot():
    scenario = datasets.end_to_end()
    _, resources, _ = planning_flow_snapshot(scenario)
    machine = _resource(resources, "MACHINE", "RC01-A")
    add_planning_resource_period_change(
        scenario, machine, action="UNAVAILABLE", start_minute=0,
        end_minute=scenario.sim.effective_horizon_minutes(),
        reason="whole-run maintenance")

    metadata, refreshed, _ = planning_flow_snapshot(scenario, machine)
    refreshed_machine = _resource(refreshed, "MACHINE", "RC01-A")
    assert refreshed_machine.baseline_state == "MAINTENANCE"
    assert "initialSite" not in metadata
    handoff_machine = next(
        item for item in redesign_viewer.handoff_snapshot(metadata)["machines"]
        if item["id"] == "M16-RC-A")
    assert handoff_machine["baselineState"] == "MAINTENANCE"


def test_planning_document_has_persistent_state_rate_and_clock_vocabulary():
    scenario = datasets.end_to_end()
    _, resources, _ = planning_flow_snapshot(scenario)
    machine = _resource(resources, "MACHINE", "RC01-A")
    add_planning_resource_period_change(
        scenario, machine, action="RATE_CAP",
        start_minute=60, end_minute=120, rate_tph=1_800)
    metadata, _, _ = planning_flow_snapshot(scenario, machine)
    document = redesign_viewer.html(
        metadata=metadata, height=760, dark=False)

    for marker in (
        "Configured state", "Configured cap", "Timed changes",
        "planningCompactLabel", "baselineColor", "CLOCK",
        "routing-availability", "routeBadge", "resource-summary",
    ):
        assert marker in document
    assert '"planningMode":true' in document
    assert '"displayMode":"routing-availability"' in document
    assert '"initialSelection":{"kind":"machine"' in document
