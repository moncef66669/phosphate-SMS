"""Continuous-feed policy parsing and physical-duration KPI contracts."""
from __future__ import annotations

import pytest

from mph_sim.continuity import (
    ContinuityPolicy,
    compute_continuity_metrics,
    continuity_policy_errors,
    parse_continuity_policies,
)
from mph_sim.domain import Destination, PlanResult, Scenario, SimParams, Site
from mph_sim.kpis import compute_kpis
from mph_sim.simulator import Simulator


def test_policy_parser_normalises_modes_and_rejects_bad_physical_values():
    raw = {
        "PLANT": {
            "mode": " CONTINUOUS_FEED ",
            "minimum_tph": 125.5,
            "max_gap_min": 30,
        },
        "TRAIN": {"mode": "batch_delivery"},
    }

    policies = parse_continuity_policies(raw, {"PLANT", "TRAIN"})

    assert policies["PLANT"] == ContinuityPolicy(
        mode="continuous_feed", minimum_tph=125.5, max_gap_min=30)
    assert policies["PLANT"].scored is True
    assert policies["TRAIN"].scored is False

    invalid = {
        "UNKNOWN": {
            "mode": "always_on",
            "minimum_tph": -1,
            "max_gap_min": 2.5,
        }
    }
    errors = continuity_policy_errors(invalid, {"PLANT"})
    assert len(errors) == 4
    assert any("configured destination" in error for error in errors)
    assert any(".mode" in error for error in errors)
    assert any(".minimum_tph" in error for error in errors)
    assert any(".max_gap_min" in error for error in errors)
    with pytest.raises(ValueError, match="CONTINUITY_POLICIES"):
        parse_continuity_policies(invalid, {"PLANT"})


def test_metrics_use_offset_physical_duration_and_count_gap_runs():
    policies = parse_continuity_policies({
        "PLANT": {
            "mode": "continuous_feed", "minimum_tph": 0,
            "max_gap_min": 45,
        },
        "TRAIN": {"mode": "batch_delivery"},
        "WASH": {"mode": "flexible_process_campaign"},
    })
    metrics = compute_continuity_metrics(
        policies,
        {
            # The evaluated local window starts at canonical index 1.
            "PLANT": [0, 100, 100, 0, 100, 100],
            "TRAIN": [100] * 6,
            "WASH": [100] * 6,
        },
        {
            "PLANT": [100, 0, 0, 0, 100],
            # These gaps must be excluded by policy mode.
            "TRAIN": [0, 0, 0, 0, 0],
            "WASH": [0, 0, 0, 0, 0],
        },
        [1.0, 0.5, 1.0, 1.0, 1.0],
        offset=1,
    )

    # One 30-minute gap, a zero-demand reset, then one 60-minute gap.
    assert metrics["gap_minutes"] == pytest.approx(90)
    assert metrics["gap_hours"] == pytest.approx(1.5)
    assert metrics["episode_count"] == 2
    assert metrics["max_episode_minutes"] == pytest.approx(60)
    assert metrics["terminal_gap"] is False
    assert set(metrics["per_destination"]) == {"PLANT"}
    assert metrics["per_destination"]["PLANT"]["max_gap_exceeded"] is True


def test_minimum_rate_threshold_is_capped_by_demand_and_zero_means_one_tonne():
    rate_policy = {"D": ContinuityPolicy(
        mode="continuous_feed", minimum_tph=50)}
    rate_metrics = compute_continuity_metrics(
        rate_policy,
        {"D": [100, 40]},
        # Thresholds are 12.5 t and 20 t for these physical durations.
        {"D": [12.5, 19.5]},
        [0.25, 0.5],
    )
    assert rate_metrics["gap_minutes"] == pytest.approx(30)
    assert rate_metrics["episode_count"] == 1

    zero_policy = {"D": ContinuityPolicy(
        mode="continuous_feed", minimum_tph=0)}
    zero_metrics = compute_continuity_metrics(
        zero_policy,
        {"D": [100, 100]},
        {"D": [0.5, 1.0]},
        [0.25, 0.25],
    )
    assert zero_metrics["gap_minutes"] == pytest.approx(15)
    assert zero_metrics["episode_count"] == 1


def test_fractional_rate_cap_matches_whole_tonne_planner_demand():
    metrics = compute_continuity_metrics(
        {"D": ContinuityPolicy(
            mode="continuous_feed", minimum_tph=10.5)},
        {"D": [10.5]},
        # The planner's authoritative whole-tonne demand is round(10.5) = 10.
        {"D": [10.0]},
        [1.0],
    )

    assert metrics["gap_minutes"] == 0
    assert metrics["episode_count"] == 0


def test_initial_open_gap_state_preserves_episode_and_duration_across_window():
    metrics = compute_continuity_metrics(
        {"D": ContinuityPolicy(mode="continuous_feed")},
        {"D": [100, 100]},
        {"D": [0, 100]},
        [0.25, 0.25],
        initial_gap_by_destination={"D": True},
        initial_open_gap_minutes={"D": 60},
    )

    assert metrics["episode_count"] == 0
    assert metrics["gap_minutes"] == pytest.approx(15)
    assert metrics["max_episode_minutes"] == pytest.approx(75)
    assert metrics["terminal_gap"] is False


def _kpi_scenario() -> Scenario:
    scenario = Scenario(
        "continuity KPI fixture",
        sites=[Site("X", "X", "hub")],
        sim=SimParams(horizon_buckets=2, dt_h=1.0),
        config={
            "CONTINUITY_POLICIES": {
                "PLANT": {
                    "mode": "continuous_feed",
                    "minimum_tph": 0,
                    "max_gap_min": 30,
                },
                "TRAIN": {"mode": "batch_delivery"},
                "WASH": {"mode": "flexible_process_campaign"},
            }
        },
    )
    scenario.destinations = [
        Destination("PLANT", "X", "DEST:PLANT", demand_default_tph=100),
        Destination("TRAIN", "X", "DEST:TRAIN", demand_default_tph=100),
        Destination(
            "WASH", "X", "DEST:WASH", demand_default_tph=100,
            demand_kind="process_feed"),
    ]
    return scenario


def test_compute_kpis_adds_policy_scoped_keys_without_changing_legacy_keys():
    scenario = _kpi_scenario()
    plan = PlanResult(
        "FEASIBLE",
        delivered={
            "PLANT": [100, 0],
            "TRAIN": [0, 0],
            # Flexible process feed meets its daily aggregate in bucket 2.
            "WASH": [0, 200],
        },
    )

    kpis = compute_kpis(scenario, plan)

    assert kpis["continuous_feed_policy_count"] == 1
    assert kpis["continuous_feed_gap_minutes"] == pytest.approx(60)
    assert kpis["continuous_feed_gap_hours"] == pytest.approx(1)
    assert kpis["continuous_feed_interruption_count"] == 1
    assert kpis["continuous_feed_max_episode_minutes"] == pytest.approx(60)
    assert kpis["continuous_feed_terminal_gap"] is True
    assert set(kpis["continuous_feed_per_destination"]) == {"PLANT"}
    # Compatibility accounting still includes the two zero-delivery train
    # buckets and excludes process-feed campaigns exactly as before.
    assert kpis["feed_interruptions_buckets"] == 3
    assert kpis["feed_interruptions_h"] == pytest.approx(3)


def test_compute_kpis_uses_elapsed_duration_and_actual_mass_for_partial_bucket():
    scenario = _kpi_scenario()
    scenario.config["CONTINUITY_POLICIES"] = {
        "PLANT": {
            "mode": "continuous_feed", "minimum_tph": 100,
            "max_gap_min": 0,
        }
    }
    plan = PlanResult("FEASIBLE", delivered={"PLANT": [0, 0]})
    simulator = Simulator(scenario, plan)
    simulator.current_minute = 15
    simulator.delivered["PLANT"][0] = 24  # 24 physical t moved so far

    kpis = compute_kpis(scenario, plan, simulator)

    assert kpis["continuous_feed_gap_minutes"] == pytest.approx(15)
    assert kpis["continuous_feed_interruption_count"] == 1
    simulator.delivered["PLANT"][0] = 25
    kpis = compute_kpis(scenario, plan, simulator)
    assert kpis["continuous_feed_gap_minutes"] == pytest.approx(0)
    assert kpis["continuous_feed_interruption_count"] == 0
