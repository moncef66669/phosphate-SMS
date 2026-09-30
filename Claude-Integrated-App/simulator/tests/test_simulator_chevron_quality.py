import pytest

from mph_sim.blending import (CHEVRON_CONFIG_KEY,
                              blended_quality_for_components)
from mph_sim.domain import (BeltProfileEntry, Grade, Job, Link, Machine,
                            Node, PlanResult, Scenario, SimParams, Site,
                            StockSpace)
from mph_sim.simulator import Simulator


def _stacking_scenario(*, carried: bool = False) -> Scenario:
    config = {}
    if carried:
        config[CHEVRON_CONFIG_KEY] = {
            "model_version": 5,
            "enabled": False,
            "enabled_spaces": [],
            "recipes": {},
            "campaign_states_by_space": {
                "PILE": {
                    "status": "COMPLETE",
                    "stacked_t_by_grade": {"A": 100, "B": 100},
                    "resolved_recipe": [
                        {"grade_id": "A", "target_fraction": 0.5},
                        {"grade_id": "B", "target_fraction": 0.5},
                    ],
                    "resolution_source": "OPTIMIZED",
                    "remaining_inventory_t": 200,
                },
            },
        }
    return Scenario(
        "simulator chevron quality basis",
        sites=[Site("MPH", "MPH", "hub")],
        grades=[
            Grade(
                "A", "A", {"BPL": 60.0, "Cd": 30.0, "moisture": 10.0},
                material_class="washed", product_moisture_pct=10.0),
            Grade(
                "B", "B", {"BPL": 80.0, "Cd": 10.0, "moisture": 30.0},
                material_class="washed", product_moisture_pct=30.0),
        ],
        spaces=[StockSpace(
            "PILE", "MPH", "M16-humid", "open", 200,
            200 if carried else 0, "A", material_class="washed",
            node_id="PILE-NODE")],
        machines=[Machine("ST", "MPH", "stacker", 0, 100, ["PILE"])],
        nodes=[Node("ARRIVAL", "MPH", "tower"),
               Node("PILE-NODE", "MPH", "space")],
        links=[Link("BELT", "ARRIVAL", "PILE-NODE", 100,
                    transit_buckets=1)],
        belt_profile=([] if carried else [
            BeltProfileEntry("BELT", 0, 100, "A"),
            BeltProfileEntry("BELT", 1, 100, "B"),
        ]),
        sim=SimParams(dt_h=1, horizon_buckets=2, horizon_minutes=120),
        config=config,
    )


def _stack_jobs(method: str, blend_mode: str = ""):
    recipe = [
        {"grade_id": "A", "target_fraction": 0.5},
        {"grade_id": "B", "target_fraction": 0.5},
    ]
    return [
        Job("JA", "ST", "stack", "PILE", grade="A", rate_tph=100,
            tonnage_t=100, start_bucket=0, end_bucket=0, path=["BELT"],
            method=method, blend_mode=blend_mode,
            blend_recipe=recipe if method == "chevron" else []),
        Job("JB", "ST", "stack", "PILE", grade="B", rate_tph=100,
            tonnage_t=100, start_bucket=1, end_bucket=1, path=["BELT"],
            method=method, blend_mode=blend_mode,
            blend_recipe=recipe if method == "chevron" else []),
    ]


def test_executed_chevron_quality_matches_blend_design_mass_basis():
    scenario = _stacking_scenario()
    expected = blended_quality_for_components(scenario, [
        {"grade_id": "A", "target_fraction": 0.5},
        {"grade_id": "B", "target_fraction": 0.5},
    ])
    simulator = Simulator(
        scenario,
        PlanResult("FEASIBLE", jobs=_stack_jobs(
            "chevron", "optimized shares")),
        auto_replan=False,
    )

    simulator.run_to_end()

    assert simulator.inv["PILE"] == pytest.approx(200)
    assert simulator.space_quality["PILE"] == pytest.approx(expected)
    # A wet-tonne average would be 70.0; dry-solids weighting is 68.75.
    assert simulator.space_quality["PILE"]["BPL"] == pytest.approx(68.75)
    assert simulator.space_quality["PILE"]["moisture"] == pytest.approx(20.0)


def test_direct_stacking_uses_known_dry_solids_basis():
    scenario = _stacking_scenario()
    simulator = Simulator(
        scenario, PlanResult("FEASIBLE", jobs=_stack_jobs("direct")),
        auto_replan=False)

    simulator.run_to_end()

    assert simulator.space_quality["PILE"]["BPL"] == pytest.approx(68.75)
    assert simulator.space_quality["PILE"]["Cd"] == pytest.approx(21.25)
    assert simulator.space_quality["PILE"]["moisture"] == pytest.approx(20.0)


def test_carried_completed_campaign_is_restored_on_canonical_basis():
    scenario = _stacking_scenario(carried=True)
    expected = blended_quality_for_components(scenario, [
        {"grade_id": "A", "target_fraction": 0.5},
        {"grade_id": "B", "target_fraction": 0.5},
    ])

    simulator = Simulator(
        scenario, PlanResult("FEASIBLE"), auto_replan=False)

    assert simulator.space_quality["PILE"] == pytest.approx(expected)
    assert simulator.space_quality["PILE"]["BPL"] == pytest.approx(68.75)
    assert simulator.space_quality["PILE"]["moisture"] == pytest.approx(20.0)
