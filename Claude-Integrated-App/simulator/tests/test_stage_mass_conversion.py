"""Focused stage-aware physical-mass / TSM conversion regressions."""

from __future__ import annotations

import pytest

from mph_sim.datasets import end_to_end
from mph_sim.domain import (
    Grade, Job, JobBucketFlow, Link, Machine, PlanResult, ProcessTransfer,
    Scenario, SimParams, Site, StockSpace,
)
from mph_sim.optimizer import build_candidates
from mph_sim.routes import RouteGraph
from mph_sim.simulator import Simulator


def test_grade_profile_converts_raw_physical_wet_product_and_tsm():
    grade = Grade(
        "BT", "BT", beneficiation_yield=0.6663,
        raw_moisture_pct=12.0, product_moisture_pct=17.5)

    assert grade.raw_physical_to_tsm(1_000) == pytest.approx(586.344)
    assert grade.raw_physical_to_wet_product(1_000) == pytest.approx(710.72)
    assert grade.wet_product_physical_to_tsm(710.72) == \
        pytest.approx(586.344)

    scenario = Scenario("mass", grades=[grade])
    raw_tsm, raw_label = scenario.physical_tsm_info(
        1_000, "raw_screened", "BT")
    assert raw_tsm == pytest.approx(586.344)
    assert raw_label == "expected TSM"
    wet_tsm, wet_label = scenario.physical_tsm_info(
        710.72, "washed", "BT")
    assert wet_tsm == pytest.approx(586.344)
    assert wet_label == "TSM equivalent"
    dry_tsm, dry_label = scenario.physical_tsm_info(586.344, "dry")
    assert dry_tsm == pytest.approx(586.344)
    assert dry_label == "TSM"
    # A raw catalogue ID does not identify its eventual merchant campaign.
    assert scenario.physical_tsm_info(1_000, "raw_screened") == (None, "")


def test_b02_dry_feed_ceiling_tracks_configured_raw_moisture():
    scenario = end_to_end()

    def wash_candidate(transfer_id: str):
        graph = RouteGraph(scenario.nodes, scenario.links)
        candidates, _ = build_candidates(scenario, graph)
        return next(c for c in candidates if c.transfer_id == transfer_id)

    candidate = wash_candidate("BG-L1-WASH")
    assert candidate.path.capacity_tph == pytest.approx(671.0 / 0.88)
    assert candidate.yield_factor == pytest.approx(
        0.88 * 0.6264 / 0.825)

    scenario.grade("TBT").raw_moisture_pct = 14.0
    wetter = wash_candidate("BG-L1-WASH")
    assert wetter.path.capacity_tph == pytest.approx(671.0 / 0.86)
    assert wetter.yield_factor == pytest.approx(
        0.86 * 0.6264 / 0.825)


def test_legacy_transfer_keeps_direct_yield_without_opt_in_profile():
    grade = Grade(
        "PRODUCT", "Product", beneficiation_yield=0.75,
        raw_moisture_pct=10.0, product_moisture_pct=20.0)
    scenario = Scenario("legacy", grades=[grade])
    legacy = ProcessTransfer(
        "LEGACY", "M", "RAW", "OUT", output_grade="PRODUCT",
        yield_factor=0.8)
    converted = ProcessTransfer(
        "BEN", "M", "RAW", "OUT", output_grade="PRODUCT",
        yield_factor=0.8, mass_conversion="beneficiation")

    assert scenario.transfer_output_factor(legacy) == pytest.approx(0.8)
    assert scenario.transfer_output_factor(converted) == pytest.approx(
        0.9 * 0.75 / 0.8)


def test_simulator_replays_process_input_and_physical_output_mass():
    scenario = Scenario(
        "execution", sim=SimParams(horizon_buckets=1, dt_h=1.0))
    scenario.sites = [Site("TEST", "Test", "mine")]
    scenario.grades = [Grade("PRODUCT", "Product", quality={"BPL": 68.0})]
    scenario.spaces = [
        StockSpace(
            "RAW", "TEST", "mine-yard", "open",
            capacity_t=1_000, mass_t=200, grade="",
            material_class="raw_screened"),
        StockSpace(
            "WET", "TEST", "product", "open",
            capacity_t=1_000, mass_t=0,
            grade="PRODUCT", material_class="washed"),
    ]
    scenario.machines = [
        Machine("WASH", "TEST", "reclaimer", 0, 100, ["RAW"])]
    scenario.links = [Link("WASH-LINK", "SPACE:RAW", "SPACE:WET", 100)]
    job = Job(
        "J-WASH", "WASH", "reclaim", "RAW", grade="RAW",
        rate_tph=100, tonnage_t=100, start_bucket=0, end_bucket=0,
        path=["WASH-LINK"], source_space="RAW", target_space="WET",
        output_grade="PRODUCT", output_tonnage_t=84,
        process_stage="WASH", transfer_id="TRANSFER",
        mass_conversion="beneficiation",
        bucket_flows=[JobBucketFlow(
            0, 100, ["WASH-LINK"], output_tonnage_t=84)],
    )

    simulator = Simulator(
        scenario, PlanResult("FEASIBLE", jobs=[job]), auto_replan=False)
    assert simulator.step() is False  # final configured bucket was executed

    assert simulator.inv["RAW"] == pytest.approx(100)
    assert simulator.inv["WET"] == pytest.approx(84)
    assert simulator.activity_log[0]["moved_t"] == pytest.approx(100)
    assert simulator.activity_log[0]["output_t"] == pytest.approx(84)
