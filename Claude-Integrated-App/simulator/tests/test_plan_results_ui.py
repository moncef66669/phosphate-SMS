"""Focused coverage for explicit plan-generation KPIs in Monitor."""
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

from conftest import nav_to  # noqa: E402
from mph_sim import datasets  # noqa: E402
from mph_sim.domain import Job, PlanResult  # noqa: E402
from mph_sim.optimizer import BIG_PENALTY  # noqa: E402
from mph_sim.validation import validate_scenario  # noqa: E402
from ui import common  # noqa: E402
from ui.pages_overview import (  # noqa: E402
    continuous_feed_delivery_summary,
    operational_priority_items,
    plan_generation_check_rows,
    plan_generation_kpi_rows,
    preplan_kpis,
)


def _recorded_plan() -> PlanResult:
    return PlanResult(
        status="FEASIBLE",
        objective=12_345.0,
        solver_wall_s=1.25,
        delivered={"MOLTEN-S": [800.0] * 24},
        breakdown={
            "unmet_t": 25.0,
            "offspec_t_unit": 12.5,
            "safety_short_t": 80.0,
            "moves": 6.0,
            "continuity_gap_minutes": 45.0,
            "continuity_episode_count": 2.0,
            "work_link_energy_kwh": 321.5,
            "scheduled_starts": 3.0,
            "scheduled_stops": 2.0,
            "scheduled_group_changeovers": 1.0,
            "active_machine_h": 12.5,
            "handled_t": 1_350.0,
            "surge_t": 3.0,
            "spill_t": 0.0,
            "planning_eligible_machines": 2.0,
            "planning_used_machines": 1.0,
            "planning_machine_coverage_pct": 50.0,
            "minute_timing_validated_windows": 4.0,
            "minute_timing_errors": 1.0,
            "minute_timing_fallback_buckets": 2.0,
        },
    )


def test_plan_generation_tables_use_recorded_solver_values_and_units():
    scn = datasets.sulphur_minimal()
    scn.config["FLOW_TONNE_PENALTY"] = 3
    scn.config["MACHINE_COVERAGE_REWARD"] = 7
    scn.config.update({
        "CONTINUITY_GAP_MINUTE_PENALTY": 11,
        "CONTINUITY_EPISODE_PENALTY": 101,
        "ENERGY_OBJECTIVE_ENABLED": True,
        "ENERGY_OBJECTIVE_CENTIKWH_PENALTY": 2,
        "EQUIPMENT_START_PENALTY": 21,
        "EQUIPMENT_STOP_PENALTY": 12,
        "EQUIPMENT_CHANGEOVER_PENALTY": 23,
        "ACTIVE_BUCKET_PENALTY": 4,
    })
    plan = _recorded_plan()
    plan.jobs = [Job(
        id="J-PLAN", machine="R1", kind="dispatch", space="S1",
        tonnage_t=1_200.0,
    )]
    kpis = {
        "unmet_t": 99.0,
        "giveaway_t_unit": 7.5,
        "throughput_t": 1_200.0,
    }

    drivers = {
        row["Objective driver"]: row
        for row in plan_generation_kpi_rows(scn, plan, kpis)
    }
    assert drivers["Unmet demand"]["Plan result"] == 25.0
    assert drivers["Soft-quality deviation"]["Unit"] == "t·quality-unit"
    assert drivers["Safety-stock shortfall"]["Plan result"] == 80.0
    assert drivers["Safety-stock shortfall"]["Unit"] == "t·bucket"
    assert drivers["Process cost"]["Plan result"] == "inactive"
    assert drivers["Handled-flow reward basis"]["Plan result"] == 1_350.0
    assert drivers["Handled-flow reward basis"]["Effect"] == "Reward"
    assert drivers["Continuous-feed gap duration"]["Plan result"] == 45.0
    assert drivers["Continuous-feed gap duration"]["Unit"] == "min"
    assert "CONTINUITY_GAP_MINUTE_PENALTY = 11" in drivers[
        "Continuous-feed gap duration"]["Configured priority"]
    assert drivers["Feed-interruption episodes"]["Plan result"] == 2.0
    assert drivers["Productive work + route energy"]["Plan result"] == 321.5
    assert drivers["Productive work + route energy"]["Effect"] == "Penalty"
    assert "positioning" in drivers[
        "Productive work + route energy"]["Meaning"]
    assert drivers["Scheduled machine starts"]["Plan result"] == 3.0
    assert drivers["Scheduled machine stops"]["Plan result"] == 2.0
    assert drivers["Scheduled group changeovers"]["Plan result"] == 1.0
    assert "w₅ × EQUIPMENT_START_PENALTY" in drivers[
        "Scheduled machine starts"]["Configured priority"]
    assert drivers["Scheduled active machine time"]["Plan result"] == 12.5
    assert drivers["Scheduled active machine time"]["Unit"] == "machine·h"
    assert drivers["BPL give-away"]["Meaning"].endswith(
        "before destination blending")
    assert drivers["Unstacked-arrival emergency slack"]["Plan result"] == 3.0
    assert drivers["Unstacked-arrival emergency slack"][
        "Configured priority"] == f"fixed penalty = {BIG_PENALTY:g}"
    assert drivers["Space-overflow emergency slack"]["Plan result"] == 0.0
    assert drivers["Commanded-flow tie-breaker"]["Plan result"] == 1_350.0
    assert drivers["Commanded-flow tie-breaker"]["Effect"] == "Penalty"
    assert drivers["Machine-coverage tie-breaker"]["Plan result"] == 1
    assert drivers["Machine-coverage tie-breaker"]["Effect"] == "Reward"

    checks = {
        row["Plan-generation check"]: row
        for row in plan_generation_check_rows(plan)
    }
    assert checks["Planning machine coverage"]["Result"] == "50.0% (1/2)"
    assert checks["Unstacked corridor arrivals"]["Result"] == 3.0
    assert checks["Minute timing errors"]["Result"] == 1


def test_continuous_feed_delivery_summary_prefers_duration_and_episodes():
    summary = continuous_feed_delivery_summary({
        "continuous_feed_gap_hours": 1.25,
        "continuous_feed_interruption_count": 3,
        "continuous_feed_max_episode_minutes": 40,
        "feed_interruptions_h": 9.0,
    })

    assert summary == {
        "gap_hours": 1.25,
        "episode_count": 3,
        "max_episode_minutes": 40.0,
        "legacy": False,
    }
    legacy = continuous_feed_delivery_summary({"feed_interruptions_h": 2.0})
    assert legacy["gap_hours"] == 2.0
    assert legacy["episode_count"] is None
    assert legacy["legacy"] is True


def test_operational_priority_items_default_energy_objective_to_off():
    items = dict(operational_priority_items(datasets.sulphur_minimal()))

    assert items["Feed-gap minute penalty"] == "10"
    assert items["Interruption episode penalty"] == "100"
    assert items["Work/link energy objective"] == "off"
    assert items["Machine start penalty"] == "20"


def test_machine_coverage_is_not_applicable_when_no_machine_is_eligible():
    plan = _recorded_plan()
    plan.breakdown.update({
        "planning_eligible_machines": 0.0,
        "planning_used_machines": 0.0,
        # Legacy optimiser output used 100% for 0/0.  The results UI must not.
        "planning_machine_coverage_pct": 100.0,
    })

    checks = {
        row["Plan-generation check"]: row
        for row in plan_generation_check_rows(plan)
    }
    assert checks["Planning machine coverage"]["Result"] == "N/A (0/0)"


def test_preplan_kpis_are_derived_from_scenario_inputs():
    scn = datasets.sulphur_minimal()

    baseline = preplan_kpis(scn)

    assert baseline["requested_demand_t"] == sum(
        entry.rate_tph * scn.sim.dt_h for entry in scn.demand
        if 0 <= entry.bucket < scn.sim.horizon_buckets
        and entry.rate_tph > 0
    )
    assert baseline["opening_inventory_t"] == sum(
        space.mass_t for space in scn.spaces
    )
    assert baseline["demand_destinations"] == len({
        entry.dest_id for entry in scn.demand
        if 0 <= entry.bucket < scn.sim.horizon_buckets
        and entry.rate_tph > 0
    })
    assert baseline["protected_spaces"] >= baseline["spaces_below_safety"]
    assert baseline["peak_demand_tph"] >= baseline["average_demand_tph"]
    assert baseline["planning_machines"] >= baseline["ready_machines"]
    assert 0 <= baseline["storage_headroom_pct"] <= 100


def test_monitor_shows_input_kpis_before_plan_generation():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    at.session_state["plan"] = None

    nav_to(at, common.MONITOR_LANDING_PAGE)

    assert not at.exception
    labels = [metric.label for metric in at.main.metric]
    assert labels[:8] == [
        "Requested demand",
        "Opening inventory",
        "Opening stock risk",
        "Demand satisfaction",
        "Average demand rate",
        "Peak demand rate",
        "Equipment ready",
        "Storage headroom",
    ]
    assert labels[8:14] == [
        "Specific electricity",
        "Reference",
        "Reference gap",
        "Positioning hours",
        "Machine conflicts",
        "Handling factor",
    ]
    assert labels[14:20] == [
        "Equipment used",
        "Productive utilisation",
        "Scheduled occupation",
        "Idle capacity",
        "Positioning / empty travel",
        "Conflict-loss exposure",
    ]
    captions = "\n".join(str(item.value) for item in at.main.caption)
    assert "Calculated from the active scenario inputs" in captions


def test_context_bar_survives_older_status_summary_without_run_count():
    """A hot-reloaded app must not crash on an older imported common module."""
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    scn = datasets.sulphur_minimal()
    at.session_state["scenario_name"] = scn.name
    at.session_state["scenario"] = scn
    # Keep the plan out of the built-in migration path while exercising the
    # status bar's branch for a populated plan.
    at.session_state["plan"] = _recorded_plan()
    at.session_state["sim"] = None
    at.session_state["issues"] = validate_scenario(scn)
    current_summary = common.status_summary

    def old_summary():
        summary = current_summary()
        summary.pop("runs", None)
        return summary

    with patch.object(common, "status_summary", side_effect=old_summary):
        at.run()

    assert not at.exception


def test_monitor_plan_results_page_is_visible_and_renders_explicit_kpis():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    scn = datasets.sulphur_minimal()
    at.session_state["scenario_name"] = scn.name
    at.session_state["scenario"] = scn
    at.session_state["plan"] = _recorded_plan()
    at.session_state["sim"] = None
    at.session_state["issues"] = validate_scenario(scn)
    at.run()

    nav_to(at, common.MONITOR_RESULTS_PAGE)

    assert not at.exception
    assert at.sidebar.radio[0].value == "Monitor"
    assert at.sidebar.radio[1].value == common.MONITOR_RESULTS_PAGE
    labels = [metric.label for metric in at.main.metric]
    assert labels[:4] == [
        "Plan status",
        "Objective score",
        "Planned demand satisfaction",
        "Planning machine coverage",
    ]
    assert labels[4:10] == [
        "Specific electricity",
        "Reference",
        "Reference gap",
        "Positioning hours",
        "Machine conflicts",
        "Handling factor",
    ]
    assert labels[10:16] == [
        "Equipment used",
        "Productive utilisation",
        "Scheduled occupation",
        "Idle capacity",
        "Positioning / empty travel",
        "Conflict-loss exposure",
    ]
    assert "Continuous-feed interruption [h]" in labels
    headings = "\n".join(str(item.value) for item in at.main.markdown)
    assert "Objective drivers" in headings
    assert "Plan-generation checks" in headings
    captions = "\n".join(str(item.value) for item in at.main.caption)
    assert "wear-risk and efficiency proxies" in captions
    assert "remaining useful life" in captions


def test_monitor_labels_mixed_objective_as_a_tactical_reference():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    scn = datasets.sulphur_minimal()
    plan = _recorded_plan()
    plan.solver_optimality_scope = "mixed_component_reference"
    plan.breakdown.update({
        "planning_objective_is_tactical_reference": 1.0,
        "planning_merged_optimality_certificate_available": 0.0,
        "planning_tactical_relative_gap_pct": 4.0,
        "planning_near_relative_gap_pct": 1.0,
    })
    at.session_state["scenario_name"] = scn.name
    at.session_state["scenario"] = scn
    at.session_state["plan"] = plan
    at.session_state["sim"] = None
    at.session_state["issues"] = validate_scenario(scn)
    at.run()

    nav_to(at, common.MONITOR_RESULTS_PAGE)

    assert not at.exception
    labels = [metric.label for metric in at.main.metric]
    assert "Tactical reference score" in labels
    assert "Objective score" not in labels[:4]
    checks = {
        row["Plan-generation check"]: row
        for row in plan_generation_check_rows(plan)
    }
    assert checks["Tactical component solver gap"]["Result"] == 4.0
    assert checks["Near-term component solver gap"]["Result"] == 1.0


def test_monitor_landing_shows_configured_objective_weights():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    at.run()
    scn = datasets.sulphur_minimal()
    at.session_state["scenario_name"] = scn.name
    at.session_state["scenario"] = scn
    at.session_state["plan"] = _recorded_plan()
    at.session_state["sim"] = None
    at.session_state["issues"] = validate_scenario(scn)
    at.run()

    nav_to(at, common.MONITOR_LANDING_PAGE)

    assert not at.exception
    body = "\n".join(str(item.value) for item in at.main.markdown)
    assert "Planner objective weights" in body
    assert "Operational priorities" in body
    assert "w₁ Demand penalty" in body
    assert "w₇ Utilisation reward" in body
    assert "Feed-gap minute penalty" in body
    assert "Work/link energy objective" in body
    captions = "\n".join(str(item.value) for item in at.main.caption)
    assert "Protected tie-breakers" in captions
    assert "ASSUMED, uncalibrated policy values" in captions
