"""A blend opt-in is future permission, never relabeling opening material."""
from copy import deepcopy

import pytest

from mph_sim import blending, datasets, prewash_blending, washing
from mph_sim.domain import PlanResult
from mph_sim.optimizer import build_candidates
from mph_sim.routes import RouteGraph
from mph_sim.simulator import Simulator


def test_all_builtin_stockpiles_opt_in_but_only_empty_mph_piles_construct():
    sc = datasets.end_to_end()
    cfg = blending.chevron_settings(sc)
    assert sc.config["BLENDING_DEFAULTS_REVISION"] == 1
    assert cfg["global_mode"] == "OPTIMIZED"
    assert cfg["enabled_spaces"] == {f"HP-{i:02d}" for i in range(1, 9)}
    assert cfg["active_spaces"] == {"HP-07", "HP-08"}
    assert blending.chevron_config_errors(sc) == []
    assert prewash_blending.configuration_errors(sc) == []
    for point_id, point in prewash_blending.settings(sc)["points"].items():
        assert point["share_mode"] == "OPTIMIZED"
        assert point["allowed_treatment_modes"] == ["simple", "full"]
        assert set(point["enabled_source_spaces"]) == {
            row["source_space"] for row in point["optimized_components"]}


def test_occupied_stockpile_keeps_ordinary_reclaim_and_assays_with_washing():
    sc = datasets.end_to_end()
    cfg = washing.defaults()
    cfg["enabled"] = True
    sc = washing.stage_configuration(sc, cfg)
    before = deepcopy(sc)
    candidates, _ = build_candidates(sc, RouteGraph.from_scenario(sc))
    prepared, expanded = washing.prepare_materials(sc, candidates)
    original_quality = sc.quality_of("HP-01")
    reclaim = [c for c in expanded if c.kind == "reclaim" and c.space == "HP-01"]
    assert reclaim
    assert all(c.grade != "BLEND-HP-01" for c in reclaim)
    opening_materials = prepared.config["_WASHING_MODEL_INITIAL"]["HP-01"]
    opening_reclaim = [c for c in reclaim if c.material_id in opening_materials]
    assert opening_reclaim
    assert all(c.quality_override == original_quality for c in opening_reclaim)
    disabled = deepcopy(sc)
    disabled.config["CHEVRON_STACKING"]["enabled_spaces"] = ["HP-07", "HP-08"]
    ordinary, _ = build_candidates(disabled, RouteGraph.from_scenario(disabled))
    _, ordinary_expanded = washing.prepare_materials(disabled, ordinary)
    assert reclaim == [c for c in ordinary_expanded
                       if c.kind == "reclaim" and c.space == "HP-01"]
    empty_reclaim = [c for c in expanded
                     if c.kind == "reclaim" and c.space == "HP-07"]
    assert empty_reclaim and all(c.grade == "BLEND-HP-07" for c in empty_reclaim)
    assert sc == before
    assert prepared.space("HP-01").mass_t == before.space("HP-01").mass_t


def test_ordinary_opening_inventory_is_not_held_by_blend_execution_gate():
    sc = datasets.end_to_end()
    sim = Simulator(sc, PlanResult(status="FEASIBLE"), auto_replan=False)
    assert sim._blend_withdrawal_issue("HP-01", 0) == ""
    assert "incomplete" in sim._blend_withdrawal_issue("HP-07", 0)


def test_permission_becomes_eligible_only_at_empty_window_opening():
    sc = datasets.end_to_end()
    space = sc.space("HP-01")
    initial_grade = space.grade
    assert not blending.chevron_enabled_for_space(sc, space.id)
    space.mass_t = 0.5  # Not even a sub-tonne residual may be relabelled.
    assert not blending.chevron_enabled_for_space(sc, space.id)
    space.mass_t = 0
    assert blending.chevron_enabled_for_space(sc, space.id)
    assert space.grade == initial_grade


@pytest.mark.parametrize("status", ["BUILDING", "COMPLETE"])
def test_started_physical_campaign_remains_managed_when_occupied(status):
    sc = datasets.end_to_end()
    cfg = sc.config["CHEVRON_STACKING"]
    cfg["campaign_states_by_space"]["HP-01"] = {
        "status": status, "batch_t": 1000,
        "resolved_recipe": [
            {"grade_id": "TBT", "tonnage_t": 500, "target_fraction": .5},
            {"grade_id": "BT-LOW-CD", "tonnage_t": 500, "target_fraction": .5}],
        "stacked_t_by_grade": {"TBT": 500},
    }
    assert blending.chevron_enabled_for_space(sc, "HP-01")


def test_presets_do_not_reset_operator_permissions_mode_or_physical_lot():
    sc = datasets.end_to_end()
    cfg = sc.config["CHEVRON_STACKING"]
    cfg["enabled_spaces"] = ["HP-07"]
    cfg["global_mode"] = "MATRIX"
    cfg["matrix_catalog"][0]["name"] = "Operator's recipe"
    cfg["campaign_states_by_space"]["HP-07"] = {
        "status": "BUILDING", "batch_t": 1000,
        "resolved_recipe": [
            {"grade_id": "TBT", "tonnage_t": 500},
            {"grade_id": "BT-LOW-CD", "tonnage_t": 500}],
        "stacked_t_by_grade": {"TBT": 500},
    }
    before = deepcopy(cfg)
    for profile in ("BLEND_SHOWCASE", "BG_TRAIN", "FULL_FLOW"):
        datasets.apply_planning_demand_profile(sc, profile)
        assert sc.config["CHEVRON_STACKING"] == before


def test_requested_defaults_preserve_assays_inventory_and_campaign_state():
    sc = datasets.end_to_end()
    sc.config["CHEVRON_STACKING"].update(
        enabled=False, enabled_spaces=[], global_mode="MATRIX")
    points = sc.config["PRE_WASHING_BLENDING"]["points"]
    for point in points.values():
        point.update(enabled_source_spaces=[], share_mode="MATRIX",
                     allowed_treatment_modes=["full"])
    grades, spaces = deepcopy(sc.grades), deepcopy(sc.spaces)
    matrices = deepcopy(sc.config["CHEVRON_STACKING"]["matrix_catalog"])
    datasets.apply_all_enabled_blending_defaults(sc)
    assert sc.grades == grades and sc.spaces == spaces
    assert sc.config["CHEVRON_STACKING"]["matrix_catalog"] == matrices
    assert blending.chevron_config_errors(sc) == []
    assert set(prewash_blending.enabled_points(sc)) == {"BEN-PREWASH", "LOU-PREWASH"}
