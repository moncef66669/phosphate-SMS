from collections import defaultdict

from mph_sim.blending import CHEVRON_CONFIG_KEY
from mph_sim.domain import (
    CalendarEntry,
    DemandEntry,
    Destination,
    Grade,
    Link,
    Machine,
    Node,
    ObjectiveWeights,
    Scenario,
    SimParams,
    Site,
    SpecRange,
    StockSpace,
)
from mph_sim.optimizer import SolveOptions, solve_plan


def _optional_component_scenario(
        *, outage_a: bool, ready_minute: int = 300,
        horizon_buckets: int = 5, demand: bool = False) -> Scenario:
    """Small routed campaign whose optional A source can be timed out."""
    calendars = []
    if outage_a:
        calendars.append(CalendarEntry(
            "RA", 0, horizon_buckets - 1,
            start_minute=0, end_minute=horizon_buckets * 60))

    blend_config = {
        "model_version": 5,
        "enabled": True,
        "enabled_spaces": ["HP-07"],
        "passes_per_cycle": 20,
        "mode_by_space": {"HP-07": "OPTIMIZED"},
        "recipes": {},
        "share_bounds_by_space": {
            "HP-07": [
                {"grade_id": "A", "min_fraction": 0.0,
                 "max_fraction": 1.0},
                {"grade_id": "B", "min_fraction": 0.0,
                 "max_fraction": 0.5},
                {"grade_id": "C", "min_fraction": 0.0,
                 "max_fraction": 0.5},
            ],
        },
        "minimum_component_fraction_by_space": {"HP-07": 0.05},
        "proposed_recipes_by_space": {},
        "result_grades": {"HP-07": "R"},
        "batch_t_by_space": {"HP-07": 100},
        "ready_minute_by_space": {"HP-07": ready_minute},
        "campaign_states_by_space": {},
    }
    return Scenario(
        "optional blend component timed outage",
        sites=[
            Site("M", "Mine", "mine"),
            Site("MPH", "MPH", "hub"),
        ],
        grades=[
            Grade("A", "A", {"BPL": 90}, material_class="washed",
                  product_moisture_pct=0),
            Grade("B", "B", {"BPL": 70}, material_class="washed",
                  product_moisture_pct=0),
            Grade("C", "C", {"BPL": 60}, material_class="washed",
                  product_moisture_pct=0),
            Grade("R", "R", {"BPL": 80}, material_class="washed",
                  product_moisture_pct=0),
        ],
        spaces=[
            StockSpace("SRC-A", "M", "yard", "open", 200, 100, "A",
                       material_class="washed", node_id="SA"),
            StockSpace("SRC-B", "M", "yard", "open", 200, 100, "B",
                       material_class="washed", node_id="SB"),
            StockSpace("SRC-C", "M", "yard", "open", 200, 100, "C",
                       material_class="washed", node_id="SC"),
            StockSpace("HP-07", "MPH", "M16-humid", "open", 100, 0,
                       "R", state="empty", material_class="washed",
                       node_id="HP"),
        ],
        machines=[
            Machine("RA", "M", "reclaimer", 0, 100, ["SRC-A"]),
            Machine("RB", "M", "reclaimer", 0, 100, ["SRC-B"]),
            Machine("RC", "M", "reclaimer", 0, 100, ["SRC-C"]),
            Machine("ST", "MPH", "stacker", 0, 100, ["HP-07"]),
            Machine("RHP", "MPH", "reclaimer", 0, 100, ["HP-07"]),
        ],
        nodes=[
            Node("SA", "M", "space"),
            Node("SB", "M", "space"),
            Node("SC", "M", "space"),
            Node("HEAD", "M", "tower"),
            Node("REC", "MPH", "tower"),
            Node("HP", "MPH", "space"),
            Node("DEST", "MPH", "dest"),
        ],
        links=[
            Link("A-HEAD", "SA", "HEAD", 100),
            Link("B-HEAD", "SB", "HEAD", 100),
            Link("C-HEAD", "SC", "HEAD", 100),
            Link("C1", "HEAD", "REC", 100, transit_buckets=1),
            Link("REC-HP", "REC", "HP", 100),
            Link("HP-DEST", "HP", "DEST", 100),
        ],
        destinations=[Destination(
            "D", "MPH", "DEST", requested_grade="R",
            spec=[SpecRange("BPL", pmin=80, target=80, hard=False)])],
        demand=(
            [DemandEntry("D", 4, 50), DemandEntry("D", 5, 50)]
            if demand else []),
        calendars=calendars,
        weights=ObjectiveWeights(w5_moves=0),
        sim=SimParams(
            dt_h=1, horizon_buckets=horizon_buckets,
            horizon_minutes=horizon_buckets * 60,
            max_solve_s=10),
        config={
            "ALLOW_CONTROLLED_FLOW_SPILL": False,
            "FLOW_TONNE_PENALTY": 1,
            CHEVRON_CONFIG_KEY: blend_config,
        },
    )


def _composition(result):
    return {
        row["grade_id"]: int(row["tonnage_t"])
        for row in result.blend_campaigns["HP-07"]["composition"]
    }


def _two_competing_piles_scenario() -> Scenario:
    bounds = [
        {"grade_id": "A", "min_fraction": 0.0, "max_fraction": 1.0},
        {"grade_id": "B", "min_fraction": 0.0, "max_fraction": 1.0},
    ]
    blend_config = {
        "model_version": 5,
        "enabled": True,
        "enabled_spaces": ["HP-07", "HP-08"],
        "passes_per_cycle": 20,
        "mode_by_space": {
            "HP-07": "OPTIMIZED",
            "HP-08": "OPTIMIZED",
        },
        "recipes": {},
        "share_bounds_by_space": {
            "HP-07": [dict(row) for row in bounds],
            "HP-08": [dict(row) for row in bounds],
        },
        "minimum_component_fraction_by_space": {
            "HP-07": 0.05,
            "HP-08": 0.05,
        },
        "proposed_recipes_by_space": {},
        "result_grades": {"HP-07": "R7", "HP-08": "R8"},
        "batch_t_by_space": {"HP-07": 100, "HP-08": 100},
        "ready_minute_by_space": {"HP-07": 180, "HP-08": 180},
        "campaign_states_by_space": {},
    }
    return Scenario(
        "two optimized piles competing for inventory and stacking capacity",
        sites=[
            Site("M", "Mine", "mine"),
            Site("MPH", "MPH", "hub"),
        ],
        grades=[
            Grade("A", "A", {"BPL": 80}, material_class="washed",
                  product_moisture_pct=0),
            Grade("B", "B", {"BPL": 60}, material_class="washed",
                  product_moisture_pct=0),
            Grade("R7", "R7", {"BPL": 79}, material_class="washed",
                  product_moisture_pct=0),
            Grade("R8", "R8", {"BPL": 61}, material_class="washed",
                  product_moisture_pct=0),
        ],
        spaces=[
            StockSpace("SRC-A", "M", "yard", "open", 100, 100, "A",
                       material_class="washed", node_id="SA"),
            StockSpace("SRC-B", "M", "yard", "open", 100, 100, "B",
                       material_class="washed", node_id="SB"),
            StockSpace("HP-07", "MPH", "M16-humid", "open", 100, 0,
                       "R7", state="empty", material_class="washed",
                       node_id="HP7"),
            StockSpace("HP-08", "MPH", "M16-humid", "open", 100, 0,
                       "R8", state="empty", material_class="washed",
                       node_id="HP8"),
        ],
        machines=[
            Machine("RA", "M", "reclaimer", 0, 100, ["SRC-A"]),
            Machine("RB", "M", "reclaimer", 0, 100, ["SRC-B"]),
            Machine("ST7", "MPH", "stacker", 0, 100, ["HP-07"]),
            Machine("ST8", "MPH", "stacker", 0, 100, ["HP-08"]),
        ],
        nodes=[
            Node("SA", "M", "space"),
            Node("SB", "M", "space"),
            Node("HEAD", "M", "tower"),
            Node("REC", "MPH", "tower"),
            Node("HP7", "MPH", "space"),
            Node("HP8", "MPH", "space"),
            Node("D7N", "MPH", "dest"),
            Node("D8N", "MPH", "dest"),
        ],
        links=[
            Link("A-HEAD", "SA", "HEAD", 100),
            Link("B-HEAD", "SB", "HEAD", 100),
            Link("C1", "HEAD", "REC", 100, transit_buckets=1),
            Link("REC-HP7", "REC", "HP7", 100,
                 shared_group="M16-STACK-IN"),
            Link("REC-HP8", "REC", "HP8", 100,
                 shared_group="M16-STACK-IN"),
            Link("HP7-D7", "HP7", "D7N", 100),
            Link("HP8-D8", "HP8", "D8N", 100),
        ],
        destinations=[
            Destination(
                "D7", "MPH", "D7N", requested_grade="R7",
                spec=[SpecRange("BPL", pmin=79, hard=True)]),
            Destination("D8", "MPH", "D8N", requested_grade="R8"),
        ],
        weights=ObjectiveWeights(w5_moves=0),
        sim=SimParams(
            dt_h=1, horizon_buckets=3, horizon_minutes=180,
            max_solve_s=10),
        config={
            "ALLOW_CONTROLLED_FLOW_SPILL": False,
            "FLOW_TONNE_PENALTY": 1,
            CHEVRON_CONFIG_KEY: blend_config,
        },
    )


def test_optional_component_is_removed_by_timed_machine_outage():
    available = solve_plan(
        _optional_component_scenario(outage_a=False),
        SolveOptions(max_time_s=10, max_wall_time_s=20),
    )
    assert available.status == "OPTIMAL"
    assert _composition(available) == {"A": 50, "B": 50}

    result = solve_plan(
        _optional_component_scenario(outage_a=True),
        SolveOptions(max_time_s=10, max_wall_time_s=20),
    )
    assert result.status == "OPTIMAL"
    assert _composition(result) == {"B": 50, "C": 50}
    assert not any(
        job.kind in {"dispatch", "stack"} and job.grade == "A"
        for job in result.jobs
    )

    stack_t = defaultdict(float)
    stack_jobs = [
        job for job in result.jobs
        if job.kind == "stack" and job.space == "HP-07"
    ]
    for job in stack_jobs:
        stack_t[job.grade] += job.tonnage_t
        assert job.method == "chevron"
        assert job.blend_mode == "optimized shares"
        assert {
            row["grade_id"]: int(row["tonnage_t"])
            for row in job.blend_recipe
        } == _composition(result)

    assert dict(stack_t) == {"B": 50.0, "C": 50.0}
    assert sum(stack_t.values()) == 100
    assert result.inventory["HP-07"][-1] == 100
    assert sum(result.surge_unstacked) == 0


def test_shared_matrix_catalog_selects_one_feasible_recipe_per_campaign():
    scenario = _optional_component_scenario(outage_a=True)
    scenario.config[CHEVRON_CONFIG_KEY] = {
        "model_version": 6,
        "enabled": True,
        "enabled_spaces": ["HP-07"],
        "passes_per_cycle": 20,
        "global_mode": "MATRIX",
        "matrix_catalog": [
            {"id": "A-B", "name": "A and B", "components": [
                {"grade_id": "A", "target_fraction": .5},
                {"grade_id": "B", "target_fraction": .5},
            ]},
            {"id": "B-C", "name": "B and C", "components": [
                {"grade_id": "B", "target_fraction": .5},
                {"grade_id": "C", "target_fraction": .5},
            ]},
        ],
        "optimized_share_bounds": [],
        "global_minimum_component_fraction": .05,
        "global_result_grade": "R",
        "global_batch_t": 100,
        "global_ready_minute": 300,
        "proposed_recipes_by_space": {},
        "proposed_matrix_by_space": {},
        "campaign_states_by_space": {},
        "point_labels": {"HP-07": "test pile"},
    }

    result = solve_plan(
        scenario, SolveOptions(max_time_s=10, max_wall_time_s=20))

    assert result.status == "OPTIMAL", result.reasons
    assert _composition(result) == {"B": 50, "C": 50}
    assert result.blend_campaigns["HP-07"]["selected_matrix"] == "B-C"
    assert all(job.blend_mode == "blend matrix" for job in result.jobs
               if job.kind == "stack" and job.space == "HP-07")


def test_optimized_recipe_requires_lookahead_through_ready_minute():
    result = solve_plan(
        _optional_component_scenario(
            outage_a=False, ready_minute=360, horizon_buckets=5),
        SolveOptions(max_time_s=10, max_wall_time_s=20),
    )

    assert result.status == "ERROR"
    assert result.solver == "cp-sat-blend-schedule"
    assert any(
        "requires schedule lookahead through ready minute 360" in reason
        for reason in result.reasons
    )
    assert result.blend_campaigns == {}
    assert result.jobs == []


def test_non_grid_ready_time_defers_reclaim_to_next_hour():
    result = solve_plan(
        _optional_component_scenario(
            outage_a=False, ready_minute=270,
            horizon_buckets=6, demand=True),
        SolveOptions(max_time_s=10, max_wall_time_s=20),
    )

    assert result.status == "OPTIMAL"
    reclaim_jobs = [
        job for job in result.jobs
        if job.kind == "reclaim" and job.space == "HP-07"
    ]
    assert reclaim_jobs
    assert all(job.start_bucket >= 5 for job in reclaim_jobs)
    assert all(job.start_minute >= 300 for job in reclaim_jobs)
    assert result.delivered["D"][4] == 0
    assert result.unmet["D"][4] == 50
    assert result.delivered["D"][5] == 50


def test_weekly_windows_coalesce_through_cross_day_blend_readiness():
    scenario = _optional_component_scenario(
        outage_a=False, ready_minute=27 * 60, horizon_buckets=30)
    scenario.config["WEEKLY_ROLLING_SOLVE_ENABLED"] = True
    scenario.config["WEEKLY_TOTAL_WALL_TIME_S"] = 20
    scenario.config["WEEKLY_WINDOW_WALL_TIME_S"] = 10
    scenario.machine("RA").rate_max_tph = 3
    scenario.destination("D").spec[0].pmin = 90
    scenario.destination("D").spec[0].target = 90
    # The high-quality component remains available on day 1, then its source
    # machine is unavailable only after the civil-day boundary.  A myopic
    # 24-hour recipe decision would not see this lost day-2 capacity.
    scenario.calendars.append(CalendarEntry(
        "RA", 24, 26, start_minute=24 * 60, end_minute=27 * 60))
    progress = []

    result = solve_plan(
        scenario,
        SolveOptions(max_time_s=10, max_wall_time_s=20),
        progress_cb=progress.append,
    )

    assert result.status == "OPTIMAL"
    assert result.solver == "cp-sat-weekly-rolling"
    assert _composition(result) == {"A": 72, "B": 28}
    assert any(
        "weekly window 1/2 (buckets 0-26)" in message
        for message in progress
    )
    assert any(
        "weekly window 2/2 (buckets 27-29)" in message
        for message in progress
    )
    assert not any(
        job.machine == "RA" and job.start_bucket >= 24
        for job in result.jobs
    )
    stack_t = defaultdict(float)
    for job in result.jobs:
        if job.kind == "stack" and job.space == "HP-07":
            stack_t[job.grade] += job.tonnage_t
    assert dict(stack_t) == {"A": 72.0, "B": 28.0}


def test_two_optimized_piles_share_inventory_and_stacking_capacity():
    result = solve_plan(
        _two_competing_piles_scenario(),
        SolveOptions(max_time_s=10, max_wall_time_s=20),
    )

    assert result.status == "OPTIMAL"
    compositions = {
        space_id: {
            row["grade_id"]: int(row["tonnage_t"])
            for row in result.blend_campaigns[space_id]["composition"]
        }
        for space_id in ("HP-07", "HP-08")
    }
    assert compositions == {
        "HP-07": {"A": 95, "B": 5},
        "HP-08": {"A": 5, "B": 95},
    }

    stack_t = defaultdict(float)
    shared_load_t = defaultdict(float)
    for job in result.jobs:
        if job.kind != "stack" or job.space not in compositions:
            continue
        stack_t[(job.space, job.grade)] += job.tonnage_t
        assert job.method == "chevron"
        assert job.blend_mode == "optimized shares"
        assert {
            row["grade_id"]: int(row["tonnage_t"])
            for row in job.blend_recipe
        } == compositions[job.space]
        for flow in job.bucket_flows:
            shared_load_t[flow.bucket] += flow.tonnage_t

    assert dict(stack_t) == {
        ("HP-07", "A"): 95.0,
        ("HP-07", "B"): 5.0,
        ("HP-08", "A"): 5.0,
        ("HP-08", "B"): 95.0,
    }
    assert dict(shared_load_t) == {1: 100.0, 2: 100.0}
    assert all(load <= 100 for load in shared_load_t.values())
    assert result.inventory["SRC-A"][-1] == 0
    assert result.inventory["SRC-B"][-1] == 0
    assert result.inventory["HP-07"][-1] == 100
    assert result.inventory["HP-08"][-1] == 100
    assert sum(result.surge_unstacked) == 0
