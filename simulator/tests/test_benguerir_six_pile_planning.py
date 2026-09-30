"""Focused solver-contract checks for the six-pile Benguerir chain."""
from __future__ import annotations

from collections import Counter

import pytest

from mph_sim import datasets
from mph_sim.flow_config import (
    PROCESS_LINE_STAGE_MAP,
    set_destination_line_enabled,
    set_process_line_enabled,
)
from mph_sim.optimizer import SolveOptions, build_candidates, solve_plan
from mph_sim.routes import RouteGraph
from mph_sim.validation import validate_scenario


PHYSICAL_PILES = {
    "BG-MINE-1": 200_000,
    "BG-MINE-2": 200_000,
    "BG-RAW-1": 200_000,
    "BG-RAW-2": 200_000,
    "BG-P1": 191_512,
    "BG-P2": 191_512,
}
HOPPERS = {"BG-HOP-TM1", "BG-HOP-TSM2", "BG-HOP-TSM3"}
MACHINES = {
    "BG-MINE-ST-1", "BG-MINE-ST-2", "BG-MINE-BWR",
    "BG-RAW-ST-1", "BG-RAW-ST-2", "BG-RAW-BWR",
    "ST3", "RC2", "RC3",
}
LINES = {"BEN-WASH-L1", "BEN-WASH-L2", "BEN-WASH-L3"}


@pytest.fixture()
def scenario():
    return datasets.end_to_end()


def test_six_physical_piles_three_hoppers_and_three_lines_are_executable(
        scenario):
    spaces = {space.id: space for space in scenario.spaces}
    assert {
        pile_id: spaces[pile_id].capacity_t
        for pile_id in PHYSICAL_PILES
    } == PHYSICAL_PILES
    assert HOPPERS <= set(spaces)
    assert {
        machine.id for machine in scenario.machines
        if machine.site == "BENGUERIR"
    } == MACHINES

    destinations = {item.id: item for item in scenario.destinations}
    assert LINES <= set(destinations)
    assert all(
        destinations[line_id].demand_kind == "process_feed"
        for line_id in LINES
    )
    transfers = [
        transfer for transfer in scenario.process_transfers
        if transfer.process_stage.startswith("BEN-")
    ]
    assert len(transfers) == 15
    assert {transfer.process_stage for transfer in transfers} == {
        stage
        for line_id in ("BG-WASH-L1", "BG-WASH-L2", "BG-WASH-L3")
        for stage in PROCESS_LINE_STAGE_MAP[line_id]
    }

    candidates, _ = build_candidates(
        scenario, RouteGraph.from_scenario(scenario))
    assert {candidate.process_stage for candidate in candidates
            if candidate.process_stage.startswith("BEN-")} == {
        transfer.process_stage for transfer in transfers
    }
    assert not [
        issue for issue in validate_scenario(scenario)
        if issue.severity == "ERROR"
    ]


def test_benguerir_line_switch_removes_only_that_campaign(scenario):
    retained_demand = scenario.destination(
        "BEN-WASH-L2").demand_default_tph
    set_process_line_enabled(scenario, "BG-WASH-L2", False)

    candidates, _ = build_candidates(
        scenario, RouteGraph.from_scenario(scenario))
    active_stages = {
        candidate.process_stage for candidate in candidates
        if candidate.process_stage.startswith("BEN-")
    }
    assert not set(PROCESS_LINE_STAGE_MAP["BG-WASH-L2"]) & active_stages
    assert set(PROCESS_LINE_STAGE_MAP["BG-WASH-L1"]) <= active_stages
    assert set(PROCESS_LINE_STAGE_MAP["BG-WASH-L3"]) <= active_stages
    assert scenario.destination(
        "BEN-WASH-L2").demand_default_tph == retained_demand


def test_demand_profiles_split_sites_and_keep_full_flow_combined(scenario):
    expected = {
        "BALANCED": (True, False, 8_000.0, 0),
        "BG_TRAIN": (False, True, 0.0, 72),
        "FULL_FLOW": (True, True, 8_000.0, 72),
    }
    for profile_id, (louta_on, bg_on, c1_t, entries) in expected.items():
        datasets.apply_planning_demand_profile(scenario, profile_id)
        assert scenario.config["LOU_S0_PROCESS_ACTIVE"] is louta_on
        assert scenario.config["BEN_S0_PROCESS_ACTIVE"] is bg_on
        assert scenario.config["CORRIDOR_DISPATCH_TARGET_T"]["C1"] == c1_t
        assert len(scenario.demand) == entries
        assert not [
            issue for issue in validate_scenario(scenario)
            if issue.severity == "ERROR"
        ]


def test_default_benguerir_feed_entries_match_integer_campaigns(scenario):
    by_line = {
        line_id: sum(
            entry.rate_tph * scenario.sim.dt_h
            for entry in scenario.demand
            if entry.dest_id == line_id
        )
        for line_id in LINES
    }
    assert by_line == {
        "BEN-WASH-L1": 2_640,
        "BEN-WASH-L2": 2_662,
        "BEN-WASH-L3": 2_640,
    }


def test_hot_reload_adds_chain_without_resetting_pap_or_machine_availability(
        scenario):
    set_destination_line_enabled(scenario, "PAP-1", False)
    scenario.machine("RC2").available = False
    scenario.spaces = [
        space for space in scenario.spaces if space.id != "BG-MINE-2"
    ]
    scenario.config["BUILTIN_BASELINE_REVISION"] = "pre-six-pile"

    assert datasets.migrate_loaded_builtin(scenario) == "solver"
    assert set(PHYSICAL_PILES) <= {
        space.id for space in scenario.spaces
    }
    assert scenario.machine("RC2").available is False
    assert all(
        not scenario.link(link_id).available
        for link_id in ("L-RTE-A-PAP", "L-RTE-B-PAP")
    )
    assert len([
        entry for entry in scenario.demand if entry.dest_id in LINES
    ]) == 72
    assert not [
        issue for issue in validate_scenario(scenario)
        if issue.severity == "ERROR"
    ]


def test_default_benguerir_campaign_has_a_feasible_machine_schedule():
    result = solve_plan(
        datasets.end_to_end(), SolveOptions(max_time_s=3))
    assert result.status in {"OPTIMAL", "FEASIBLE"}, result.reasons
    assert all(
        sum(result.unmet[destination_id]) == 0
        for destination_id in {"BG-TRAIN-PROD", *LINES}
    )
    assert result.breakdown["unmet_t"] == 0
    assert result.breakdown["spill_t"] == 0
    assert sum(result.surge_unstacked) == 0
    jobs_by_machine = Counter(job.machine for job in result.jobs)
    assert {
        "BG-MINE-ST-1", "BG-MINE-ST-2", "BG-MINE-BWR",
        "BG-RAW-ST-1", "BG-RAW-ST-2", "BG-RAW-BWR", "ST3",
    } <= set(jobs_by_machine)
    assert {"RC2", "RC3"} & set(jobs_by_machine)


def test_full_flow_is_a_clean_all_benguerir_machine_schedule():
    scenario = datasets.end_to_end()
    datasets.apply_planning_demand_profile(scenario, "FULL_FLOW")

    result = solve_plan(scenario, SolveOptions(max_time_s=3))

    assert result.status in {"OPTIMAL", "FEASIBLE"}, result.reasons
    assert result.breakdown["unmet_t"] == 0
    assert result.breakdown["spill_t"] == 0
    assert sum(result.surge_unstacked) == 0
    jobs_by_machine = Counter(job.machine for job in result.jobs)
    eligible = {
        machine.id for machine in scenario.machines
        if machine.available and machine.planning_enabled
    }
    assert set(jobs_by_machine) >= eligible
    assert len(eligible) == 20
    assert sum(
        job.tonnage_t for job in result.jobs if job.dest_id == "C2"
    ) == pytest.approx(10_600)
    assert sum(
        job.tonnage_t
        for job in result.jobs if job.dest_id == "BG-TRAIN-PROD"
    ) == pytest.approx(24_000)
