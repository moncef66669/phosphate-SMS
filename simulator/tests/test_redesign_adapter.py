"""Focused contracts for the redesigned-viewer data adapter."""
from __future__ import annotations

import json

from mph_sim import datasets
from mph_sim.domain import Job, PlanResult
from ui.flow_svg.redesign_adapter import build_snapshot
from ui.layout_geometry import geometry_for
from ui.process_mimic import MimicOpts, build_process_mimic_state


def _assets(snapshot: dict, kind: str) -> list[dict]:
    return [asset for asset in snapshot["assets"]
            if asset["kind"] == kind]


def _asset(snapshot: dict, kind: str, asset_id: str) -> dict:
    return next(asset for asset in _assets(snapshot, kind)
                if asset["id"] == asset_id)


def test_snapshot_is_json_safe_and_uses_production_geometry_ids():
    sc = datasets.end_to_end()
    state = build_process_mimic_state(
        sc, None, None, basis="initial", bucket=0)
    geo = geometry_for(sc, "CHAIN")

    snapshot = build_snapshot(sc, state, geo, MimicOpts())

    # Strict JSON rejects NaN/Infinity, which browsers cannot consume safely.
    json.dumps(snapshot, allow_nan=False)
    assert snapshot["schemaVersion"] == 1
    assert snapshot["site"] == "CHAIN"
    assert snapshot["view"]["sites"] == ["LOUTA", "BENGUERIR", "MPH"]
    asset_ids = [item["id"] for item in snapshot["assets"]]
    assert len(asset_ids) == len(set(asset_ids))

    assert {item["id"] for item in _assets(snapshot, "pile")} == {
        pile.pile_id for pile in geo.piles
    }
    assert {item["id"] for item in _assets(snapshot, "machine")} == {
        machine.machine_id for machine in geo.machines
    }
    assert {item["id"] for item in _assets(snapshot, "rail")} == {
        rail.rail_id for rail in geo.rails
    }
    assert {item["id"] for item in _assets(snapshot, "route")} == {
        conveyor.cid for conveyor in geo.conveyors
    }
    assert {item["id"] for item in _assets(snapshot, "processLine")} == {
        line.line_id for line in geo.process_lines
    }

    all_ids = {item["id"] for item in snapshot["assets"]}
    for mock_only_id in (
        "BG-MINE-P1", "BG-RAW-P1", "BG-MINE-RC-1",
        "M16-ST-A", "M16-RC-A", "LO-HOP-1", "BAY 1 SPUR",
        "LINE L1",
    ):
        assert mock_only_id not in all_ids

    assert {"BG-MINE-1", "BG-RAW-1", "ST01-A", "RC01-A", "ST3",
            "RC2", "RC3"} <= all_ids
    assert _asset(snapshot, "pile", "102MG-SP-01")["displayOnly"] is True
    assert _asset(snapshot, "processLine", "LT-WASH-L1")["label"] == "LINE L1"


def test_shared_physical_machines_stay_unique_with_their_real_served_sets():
    sc = datasets.end_to_end()
    state = build_process_mimic_state(
        sc, None, None, basis="initial", bucket=0)
    snapshot = build_snapshot(
        sc, state, geometry_for(sc, "CHAIN"), MimicOpts())

    machines = _assets(snapshot, "machine")
    machine_ids = [machine["id"] for machine in machines]
    assert len(machine_ids) == len(set(machine_ids))

    expected_served = {
        "110MG-RC-01": {"102MG-SP-01", "102MG-SP-02"},
        "110LG-ST-01": {"110LG-OL1", "110LG-OL2"},
        "130LG-ST-01": {"LT-P1", "LT-P2"},
        "BG-MINE-BWR": {"BG-MINE-1", "BG-MINE-2"},
        "BG-RAW-BWR": {"BG-RAW-1", "BG-RAW-2"},
        "ST3": {"BG-P1", "BG-P2"},
    }
    for machine_id, served in expected_served.items():
        machine = _asset(snapshot, "machine", machine_id)
        assert set(machine["servedPileIds"]) == served
        assert machine_ids.count(machine_id) == 1

    # The redesign must not split one shared bucket-wheel reclaimer or the
    # twin-boom stacker into fictitious per-pile machines.
    assert not {"BG-MINE-RC-1", "BG-MINE-RC-2",
                "BG-RAW-RC-1", "BG-RAW-RC-2",
                "BG-ST-3", "BG-ST-4"} & set(machine_ids)


def test_active_plan_state_and_grouped_route_capacity_are_not_invented():
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
    snapshot = build_snapshot(
        sc, state, geometry_for(sc, "MPH"),
        MimicOpts(selected_job="J-R"),
        basis="Plan timeline",
    )

    machine = _asset(snapshot, "machine", "RC01-A")
    assert machine["state"] == "Reclaiming"
    assert machine["jobId"] == "J-R"
    assert machine["targetId"] == "HP-05"
    assert machine["destinationId"] == "PAP-1"
    assert machine["rateTph"] == 1200
    assert machine["selected"] is True
    assert len(machine["details"]) == 4

    reclaim_group = _asset(snapshot, "route", "M16A-A-T12")
    assert reclaim_group["flowTph"] == 1200
    assert reclaim_group["capacityTph"] is None
    assert reclaim_group["selected"] is True

    pap_route = _asset(snapshot, "route", "H-PAP")
    assert pap_route["flowTph"] == 1200
    assert pap_route["capacityTph"] is None
    assert pap_route["selected"] is True

    pap1 = _asset(snapshot, "destination", "PAP-1")
    assert pap1["lifecycleState"] == "Available"
    assert pap1["demandRateTph"] == 1500
    assert pap1["activeDeliveryRateTph"] == 1200

    pap2 = _asset(snapshot, "destination", "PAP-2")
    assert pap2["lifecycleState"] == "Future"
    assert pap2["demandRateTph"] == 0
    assert pap2["configuredDemandRateTph"] == 1500
