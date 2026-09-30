"""Purpose-backed S0 equipment and Louta process-chain regressions.

The baseline must never manufacture work to make an equipment row look
active.  Current assets without work instead carry a structured disposition;
the six live Louta assets are exercised only by the explicit stocked-route
policy and its material-conservation constraints.
"""

from __future__ import annotations

import copy

import pytest

from mph_sim import datasets
from mph_sim.domain import Destination
from mph_sim.optimizer import build_candidates, solve_plan
from mph_sim.routes import RouteGraph, space_node
from mph_sim.validation import validate_scenario
from ui.theme import MACHINE_BLOCKING_DATA


FINAL_SITE = "MPH"
MINE_SITES = {"LOUTA", "BENGUERIR"}
LOUTA_PRODUCT_RECLAIMERS = {"140LG-RC-01", "140LG-RC-02"}
LOUTA_CURRENT_MACHINES = {
    "110LG-ST-01",
    "120LG-RC-01",
    "120LG-RC-02",
    "130LG-ST-01",
    "140LG-RC-01",
    "140LG-RC-02",
}
LOUTA_SOLVER_SPACES = {
    "LT-MINE-FEED-GLOBAL",
    "LT-MINE-FEED-LOW-CD",
    "110LG-OL1",
    "110LG-OL2",
    "LT-WASH-L1-WIP",
    "LT-WASH-L2-WIP",
    "LT-P1",
    "LT-P2",
}


@pytest.fixture(scope="module")
def fresh_s3():
    """A new built-in scenario, not a migrated or mutated UI session."""
    return datasets.end_to_end()


@pytest.fixture(scope="module")
def louta_s3():
    """The site-focused Louta campaign used by the Louta regressions."""
    scenario = datasets.end_to_end()
    datasets.apply_planning_demand_profile(
        scenario, datasets.DEFAULT_PLANNING_DEMAND_PROFILE)
    return scenario


@pytest.fixture(scope="module")
def full_budget_plan(louta_s3):
    """One Louta-campaign solve shared by every Louta assertion."""
    result = solve_plan(louta_s3)
    assert result.status in {"OPTIMAL", "FEASIBLE"}, result.reasons
    return result


def _candidate_machines(scenario) -> set[str]:
    candidates, _ = build_candidates(
        scenario, RouteGraph.from_scenario(scenario))
    return {candidate.machine for candidate in candidates}


def test_fresh_s3_uses_terminal_supply_ceiling_not_equality_target(fresh_s3):
    config = fresh_s3.config

    assert config["M16_TERMINAL_SUPPLY_POSITION_POLICY"] == (
        "not_above_initial_stock_plus_initial_in_transit")
    assert config["M16_TERMINAL_SUPPLY_POSITION_MAX_T"] > 0
    assert "M16_TERMINAL_SUPPLY_POSITION_TARGET_T" not in config
    assert config["MACHINE_COVERAGE_REWARD"] == 0
    assert config["FLOW_TONNE_PENALTY"] > 0
    assert config["LOU_S0_STOCKED_ROUTE_ENABLED"] is True
    assert config["CORRIDOR_DISPATCH_TARGET_T"]["C1"] == pytest.approx(60_002)
    assert config["CORRIDOR_DISPATCH_TARGET_T"]["C2"] == pytest.approx(60_000)
    assert config["LOU_C1_STOCKED_ROUTE_TARGET_T"] == pytest.approx(60_002)
    assert set(config["LOU_WASH_LINE_OUTPUT_TARGET_T"].values()) == {30_001.0}
    assert all(
        config["PROCESS_STAGE_OUTPUT_TARGET_T"][stage] > 0
        for stage in ("BEN-WASH-L1", "BEN-WASH-L2", "BEN-WASH-L3")
    )


def test_inconsistent_louta_policy_is_rejected_before_solving(fresh_s3):
    scenario = copy.deepcopy(fresh_s3)
    datasets.apply_planning_demand_profile(
        scenario, datasets.DEFAULT_PLANNING_DEMAND_PROFILE)
    scenario.config["CORRIDOR_DISPATCH_TARGET_T"]["C1"] = 60_001.0

    assert "LOUTA_POLICY_MASS_MISMATCH" in {
        issue.code for issue in validate_scenario(scenario)}
    result = solve_plan(scenario)

    assert result.status == "ERROR"
    assert result.reasons
    assert result.reasons[0].startswith("LOUTA_POLICY_MASS_MISMATCH")

    assumption = next(item for item in fresh_s3.assumptions
                      if item.id == "S3-A27")
    assert "may not exceed" in assumption.text
    assert "MACHINE_COVERAGE_REWARD is zero" in assumption.text
    assert "no job is generated merely to activate a row" in assumption.text


def test_hot_loaded_pre_policy_s3_invalidates_its_old_plan():
    stale = datasets.end_to_end()
    stale.config["BUILTIN_BASELINE_REVISION"] = \
        "2026-07-26-washing-residence-metadata-v13"
    stale.config.pop("M16_TERMINAL_SUPPLY_POSITION_POLICY")
    stale.config.pop("M16_TERMINAL_SUPPLY_POSITION_MAX_T")
    stale.config.pop("M16_TERMINAL_SUPPLY_POSITION_BASIS")
    stale.config.pop("MACHINE_COVERAGE_REWARD")
    stale.config.pop("ACTIVE_PLANNING_DEMAND_PROFILE")
    stale.config.pop("PLANNING_DEMAND_PROFILE_BASIS")
    stale.destination("BG-TRAIN-PROD").demand_default_tph = 0

    assert datasets.migrate_loaded_builtin(stale) == "solver"
    assert stale.config["M16_TERMINAL_SUPPLY_POSITION_MAX_T"] > 0
    assert stale.config["MACHINE_COVERAGE_REWARD"] == 0
    assert stale.config["LOU_S0_STOCKED_ROUTE_ENABLED"] is True
    assert stale.config["CORRIDOR_DISPATCH_TARGET_T"]["C1"] == \
        pytest.approx(60_002)
    assert stale.config["CORRIDOR_DISPATCH_TARGET_T"]["C2"] == \
        pytest.approx(60_000)
    assert stale.config["LOU_C1_STOCKED_ROUTE_TARGET_T"] == \
        pytest.approx(60_002)
    assert stale.config["ACTIVE_PLANNING_DEMAND_PROFILE"] == \
        "INTEGRATED_WEEKLY"
    assert stale.destination("BG-TRAIN-PROD").demand_default_tph == \
        pytest.approx((24_000 / 7) / 24)


def test_final_reclaim_candidates_do_not_bypass_corridor_staging(fresh_s3):
    graph = RouteGraph.from_scenario(fresh_s3)
    candidates, _ = build_candidates(fresh_s3, graph)
    mine_reclaimers = {
        machine.id for machine in fresh_s3.machines
        if machine.site in MINE_SITES and machine.kind == "reclaimer"
    }
    final_destinations = {
        destination.id for destination in fresh_s3.destinations
        if destination.site == FINAL_SITE
    }

    invalid = [
        candidate for candidate in candidates
        if candidate.kind == "reclaim"
        and candidate.machine in mine_reclaimers
        and candidate.dest_id in final_destinations
    ]
    assert not invalid


def test_missing_route_is_only_a_warning_when_demand_is_positive():
    inactive = datasets.end_to_end()
    inactive_issue = next(
        issue for issue in validate_scenario(inactive)
        if issue.entity == "BG-TRAIN-RAW")
    assert inactive_issue.severity == "INFO"
    assert inactive_issue.code == "NO_FEED_PATH_INACTIVE"

    active = datasets.end_to_end()
    # Dated hourly rows are authoritative in S0; changing only the legacy
    # destination default must not override the weekly demand calendar.
    for entry in active.demand:
        if entry.dest_id == "BG-TRAIN-RAW":
            entry.rate_tph = 500
    active_issue = next(
        issue for issue in validate_scenario(active)
        if issue.entity == "BG-TRAIN-RAW")
    assert active_issue.severity == "WARNING"
    assert active_issue.code == "NO_FEED_PATH"


def test_process_feed_validation_requires_stage_and_matching_source_grade():
    scenario = datasets.end_to_end()
    transfer = next(
        item for item in scenario.process_transfers
        if item.process_stage == "LOU-WASH-L1")
    source = scenario.space(transfer.source_space)
    destination = Destination(
        "TEST-WASH-FEED",
        "LOUTA",
        space_node(scenario, transfer.target_space),
        demand_default_tph=100.0,
        requested_grade=source.grade,
        demand_kind="process_feed",
        process_stage=transfer.process_stage,
    )
    scenario.destinations.append(destination)

    matching_issues = [
        issue for issue in validate_scenario(scenario)
        if issue.entity == destination.id]
    assert not matching_issues

    destination.requested_grade = next(
        grade.id for grade in scenario.grades if grade.id != source.grade)
    mismatch_issue = next(
        issue for issue in validate_scenario(scenario)
        if issue.entity == destination.id)
    assert mismatch_issue.severity == "WARNING"
    assert mismatch_issue.code == "NO_PROCESS_FEED_ROUTE"

    destination.requested_grade = source.grade
    scenario.link(transfer.path[-1]).available = False
    unavailable_route_issue = next(
        issue for issue in validate_scenario(scenario)
        if issue.entity == destination.id)
    assert unavailable_route_issue.severity == "WARNING"
    assert unavailable_route_issue.code == "NO_PROCESS_FEED_ROUTE"

    scenario.link(transfer.path[-1]).available = True
    destination.process_stage = ""
    missing_stage_issue = next(
        issue for issue in validate_scenario(scenario)
        if issue.entity == destination.id)
    assert missing_stage_issue.severity == "ERROR"
    assert missing_stage_issue.code == "NO_PROCESS_STAGE"


def test_unmodelled_zero_process_feed_demand_is_informational():
    scenario = datasets.end_to_end()
    transfer = next(
        item for item in scenario.process_transfers
        if item.process_stage == "LOU-WASH-L1")
    destination = Destination(
        "TEST-FUTURE-WASH-FEED",
        "BENGUERIR",
        space_node(scenario, transfer.target_space),
        demand_default_tph=0.0,
        requested_grade=scenario.space(transfer.source_space).grade,
        demand_kind="process_feed",
        process_stage="UNMODELLED-WASH-LINE",
    )
    scenario.destinations.append(destination)

    issue = next(
        item for item in validate_scenario(scenario)
        if item.entity == destination.id)
    assert issue.severity == "INFO"
    assert issue.code == "NO_PROCESS_FEED_ROUTE_INACTIVE"


def test_exact_demand_prevents_overdelivery_in_every_bucket(
        louta_s3, full_budget_plan):
    """Delivered mass plus explicit unmet mass equals, not exceeds, demand."""
    saw_zero_demand_bucket = False
    dt_h = louta_s3.sim.dt_h
    horizon = louta_s3.sim.horizon_buckets

    for destination in louta_s3.destinations:
        if getattr(destination, "demand_kind", "delivery") == "process_feed":
            demand_t = sum(
                float(round(
                    louta_s3.demand_tph(destination.id, bucket) * dt_h))
                for bucket in range(horizon)
            )
            delivered_t = sum(
                full_budget_plan.delivered[destination.id][bucket] * dt_h
                for bucket in range(horizon)
            )
            unmet_t = sum(full_budget_plan.unmet[destination.id])
            assert delivered_t + unmet_t >= demand_t - 1e-6
            continue
        for bucket in range(horizon):
            # The CP-SAT model represents whole tonnes per native bucket.
            demand_t = float(round(
                louta_s3.demand_tph(destination.id, bucket) * dt_h))
            delivered_t = (
                full_budget_plan.delivered[destination.id][bucket] * dt_h)
            unmet_t = full_budget_plan.unmet[destination.id][bucket]

            assert delivered_t + unmet_t == pytest.approx(
                demand_t, abs=1e-6)
            assert delivered_t <= demand_t + 1e-6
            if demand_t == 0:
                saw_zero_demand_bucket = True
                assert delivered_t == pytest.approx(0.0, abs=1e-6)
                assert unmet_t == pytest.approx(0.0, abs=1e-6)

    assert saw_zero_demand_bucket, (
        "S3 must retain at least one zero-demand interface as the explicit "
        "overdelivery regression case")


def test_default_full_budget_plan_reports_unmet_without_emergency_mass_slack(
        full_budget_plan):
    # Seven days of final-unit demand exceed the finite, unreplenished demo
    # inventory.  This is explicit unmet demand, not overflow or invented mass.
    #
    # 14-Sep-2026: was 274,560 t pre-change, then 245,752 t.  The campaign /
    # parcel policy, the corrected continuous-feed service test, the explicit
    # held-running state and the measured short-parcel penalty changed which
    # buckets the planner activates.  On the same inputs, seed and solver budget
    # it now serves 20,026 t MORE of the same demand than the pre-change model.
    # Fragmentation fell at the same time (see
    # tests/test_schedule_fragmentation.py), so this is not a smoothness /
    # service trade-off on this profile.
    assert full_budget_plan.breakdown["unmet_t"] == pytest.approx(
        254_534.0, abs=1e-6)
    assert full_budget_plan.breakdown["surge_t"] == pytest.approx(
        0.0, abs=1e-6)
    assert full_budget_plan.breakdown["spill_t"] == pytest.approx(
        0.0, abs=1e-6)


def test_default_plan_has_mass_preserving_minute_execution_windows(
        full_budget_plan):
    assert full_budget_plan.breakdown[
        "minute_timing_validated_windows"] > 0
    unvalidated = []
    for job in full_budget_plan.jobs:
        assert sum(flow.tonnage_t for flow in job.bucket_flows) == \
            pytest.approx(job.tonnage_t, abs=1e-6)
        assert job.execution_windows
        unvalidated.extend(
            window for window in job.execution_windows
            if window.status != "VALIDATED")
    assert all(
        "positioning" in window.note.lower()
        for window in unvalidated)
    assert full_budget_plan.breakdown["minute_timing_errors"] == \
        pytest.approx(float(len(unvalidated)))
    windows = [
        window for job in full_budget_plan.jobs
        for window in job.execution_windows
    ]
    assert any(window.duration_min < 60 for window in windows)
    assert full_budget_plan.breakdown[
        "minute_timing_fallback_buckets"] == pytest.approx(0.0)


def test_every_planning_eligible_machine_has_a_structured_disposition(
        louta_s3, full_budget_plan):
    eligible = _candidate_machines(louta_s3)
    used = {job.machine for job in full_budget_plan.jobs}
    dispositions = full_budget_plan.machine_dispositions

    assert used <= eligible
    assert eligible <= set(dispositions)
    assert full_budget_plan.breakdown[
        "planning_eligible_machines"] == pytest.approx(float(len(eligible)))
    assert full_budget_plan.breakdown[
        "planning_used_machines"] == pytest.approx(float(len(used)))
    assert full_budget_plan.breakdown[
        "planning_machine_coverage_pct"] == pytest.approx(
            100.0 * len(used) / len(eligible))

    allowed = {"jobs generated", "legitimate idle", "modelling gap"}
    for machine_id in eligible:
        disposition = dispositions[machine_id]
        assert disposition["classification"] in allowed
        assert disposition["reason"]
        assert isinstance(disposition["reachable_spaces"], list)
        assert isinstance(disposition["solver_controlled"], bool)
        if machine_id in used:
            assert disposition["classification"] == "jobs generated"
        else:
            assert disposition["classification"] in {
                "legitimate idle", "modelling gap"}

    disabled = {
        machine.id for machine in louta_s3.machines
        if not machine.available or not machine.planning_enabled
    }
    non_current = {
        machine.id for machine in louta_s3.machines
        if any(marker in machine.lifecycle.upper()
               for marker in ("FUTURE", "REFERENCE", "DISPLAY"))
    }
    display_reference = set(MACHINE_BLOCKING_DATA)
    future_display = set(datasets.M16_FUTURE_LINES)
    forbidden = disabled | non_current | display_reference | future_display

    assert used.isdisjoint(forbidden)
    assert all(job.machine in eligible for job in full_budget_plan.jobs)
    assert all(job.purpose for job in full_budget_plan.jobs)


def test_s0_policy_generates_purposeful_work_for_all_six_current_louta_assets(
        louta_s3, full_budget_plan):
    jobs = [
        job for job in full_budget_plan.jobs
        if job.machine in LOUTA_CURRENT_MACHINES
    ]

    assert {job.machine for job in jobs} == LOUTA_CURRENT_MACHINES
    assert all(job.purpose for job in jobs)
    assert all(job.source_space and job.target_space for job in jobs)
    assert all(job.path and job.tonnage_t > 0 for job in jobs)
    assert all(
        full_budget_plan.machine_dispositions[machine_id][
            "classification"] == "jobs generated"
        for machine_id in LOUTA_CURRENT_MACHINES
    )
    assert {"102MG-ST-01", "102MG-ST-02"}.isdisjoint(
        {machine.id for machine in louta_s3.machines})
    assert {"102MG-ST-01", "102MG-ST-02"}.isdisjoint(
        {job.machine for job in full_budget_plan.jobs})
    assert LOUTA_SOLVER_SPACES == {
        space.id for space in louta_s3.spaces if space.site == "LOUTA"}


def test_louta_process_chain_conserves_inventory_routes_and_dependencies(
        louta_s3, full_budget_plan):
    transfer_by_id = {
        transfer.id: transfer for transfer in louta_s3.process_transfers}
    process_jobs = [
        job for job in full_budget_plan.jobs
        if job.transfer_id in transfer_by_id
    ]
    assert process_jobs

    for job in process_jobs:
        transfer = transfer_by_id[job.transfer_id]
        assert job.machine == transfer.machine
        assert job.source_space == transfer.source_space
        assert job.target_space == transfer.target_space
        assert job.path == transfer.path
        assert job.process_stage == transfer.process_stage
        assert job.output_grade == transfer.output_grade
        assert job.output_tonnage_t <= job.tonnage_t + 1e-6
        assert sum(flow.tonnage_t for flow in job.bucket_flows) == \
            pytest.approx(job.tonnage_t)
        assert sum(flow.output_tonnage_t for flow in job.bucket_flows) == \
            pytest.approx(job.output_tonnage_t)
        assert all(flow.path == transfer.path for flow in job.bucket_flows)
        assert not job.grade.startswith(("LOU-RAW-", "MINE-RAW-"))
        assert not job.output_grade.startswith(("LOU-RAW-", "MINE-RAW-"))

    raw_job_grades = {
        value
        for job in process_jobs
        for value in (job.grade, job.output_grade)
        if value.startswith("RAW-")
    }
    assert raw_job_grades <= {
        "RAW-BT", "RAW-BT-HIGH-CD", "RAW-BT-LOW-CD", "RAW-TBC"}
    assert raw_job_grades

    outputs_by_stage = {
        stage: sum(
            job.output_tonnage_t for job in process_jobs
            if job.process_stage == stage)
        for stage in louta_s3.config["PROCESS_STAGE_OUTPUT_TARGET_T"]
    }
    assert outputs_by_stage == pytest.approx(
        louta_s3.config["PROCESS_STAGE_OUTPUT_TARGET_T"])

    exact_terminal = louta_s3.config["TERMINAL_INVENTORY_EXACT_T"]
    for space_id, expected_t in exact_terminal.items():
        assert full_budget_plan.inventory[space_id][-1] == pytest.approx(
            round(expected_t))

    wash_input_by_ore = {
        ore_id: sum(
            job.tonnage_t for job in process_jobs
            if job.process_stage.startswith("LOU-WASH-")
            and job.source_space == ore_id)
        for ore_id in ("110LG-OL1", "110LG-OL2")
    }
    restored_by_ore = {
        ore_id: sum(
            job.output_tonnage_t for job in process_jobs
            if job.process_stage == "LOU-ORE-STACK"
            and job.target_space == ore_id)
        for ore_id in wash_input_by_ore
    }
    assert restored_by_ore == pytest.approx(wash_input_by_ore)

    wash_output_by_wip = {
        wip_id: sum(
            job.output_tonnage_t for job in process_jobs
            if job.process_stage.startswith("LOU-WASH-")
            and job.target_space == wip_id)
        for wip_id in ("LT-WASH-L1-WIP", "LT-WASH-L2-WIP")
    }
    stacked_from_wip = {
        wip_id: sum(
            job.tonnage_t for job in process_jobs
            if job.process_stage.startswith("LOU-PRODUCT-STACK-")
            and job.source_space == wip_id)
        for wip_id in wash_output_by_wip
    }
    assert stacked_from_wip == pytest.approx(wash_output_by_wip)

    stacked_by_product = {
        pile_id: sum(
            job.output_tonnage_t for job in process_jobs
            if job.process_stage.startswith("LOU-PRODUCT-STACK-")
            and job.target_space == pile_id)
        for pile_id in ("LT-P1", "LT-P2")
    }
    dispatched_by_product = {
        pile_id: sum(
            job.tonnage_t for job in full_budget_plan.jobs
            if job.machine in LOUTA_PRODUCT_RECLAIMERS
            and job.source_space == pile_id)
        for pile_id in stacked_by_product
    }
    assert dispatched_by_product == pytest.approx(stacked_by_product)
    assert sum(dispatched_by_product.values()) == pytest.approx(
        louta_s3.config["CORRIDOR_DISPATCH_TARGET_T"]["C1"])

    wash_jobs = [
        job for job in process_jobs
        if job.process_stage.startswith("LOU-WASH-")]
    stack_jobs = [
        job for job in process_jobs
        if job.process_stage.startswith("LOU-PRODUCT-STACK-")]
    assert all(job.residence_min == 30 for job in wash_jobs)
    assert all(job.depends_on_stage and job.dependency_delay_min == 30
               for job in stack_jobs)

    wash_windows_by_stage = {
        stage: [
            (
                window,
                sum(
                    flow.output_tonnage_t
                    for flow in job.bucket_flows
                    if window.solver_start_bucket <= flow.bucket
                    <= window.solver_end_bucket),
                job.target_space,
            )
            for job in wash_jobs if job.process_stage == stage
            for window in job.execution_windows
            if window.status == "VALIDATED"
        ]
        for stage in {job.process_stage for job in wash_jobs}
    }
    consumed_by_dependency = {}
    for job in stack_jobs:
        for window in job.execution_windows:
            if window.status != "VALIDATED":
                assert "positioning" in window.note.lower()
                continue
            key = (job.depends_on_stage, job.source_space)
            consumed_by_dependency[key] = \
                consumed_by_dependency.get(key, 0.0) + window.tonnage_t
            matured_t = sum(
                output_t
                for predecessor, output_t, target_space
                in wash_windows_by_stage[job.depends_on_stage]
                if target_space == job.source_space
                and predecessor.start_minute + predecessor.duration_min
                + job.dependency_delay_min <= window.start_minute
            )
            assert matured_t + 0.1 >= consumed_by_dependency[key]
        for flow in job.bucket_flows:
            assert flow.tonnage_t <= \
                full_budget_plan.inventory[job.source_space][flow.bucket] + 1e-6


def test_both_louta_reclaimers_are_used_without_bucket_overlap(
        full_budget_plan):
    jobs = [
        job for job in full_budget_plan.jobs
        if job.machine in LOUTA_PRODUCT_RECLAIMERS
    ]
    assert {job.machine for job in jobs} == LOUTA_PRODUCT_RECLAIMERS

    active_by_bucket: dict[int, list[str]] = {}
    for job in jobs:
        for bucket in range(job.start_bucket, job.end_bucket + 1):
            active_by_bucket.setdefault(bucket, []).append(job.machine)

    assert active_by_bucket
    assert all(len(machines) == 1
               for machines in active_by_bucket.values())


def test_terminal_supply_position_stays_below_configured_ceiling(
        louta_s3, full_budget_plan):
    ceiling = float(
        louta_s3.config["M16_TERMINAL_SUPPLY_POSITION_MAX_T"])
    breakdown = full_budget_plan.breakdown

    assert breakdown["terminal_supply_position_max_t"] == pytest.approx(
        ceiling, abs=1e-6)
    assert breakdown["terminal_supply_position_t"] <= ceiling + 1e-6
    assert breakdown["terminal_corridor_in_transit_t"] >= 0
