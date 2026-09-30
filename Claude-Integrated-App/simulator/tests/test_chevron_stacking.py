import pytest

from mph_sim import datasets
from mph_sim.blending import (BLEND_MODE_MATRIX, BLEND_MODE_OPTIMIZED,
                              CAMPAIGN_BUILDING, CAMPAIGN_COMPLETE,
                              CAMPAIGN_NOT_STARTED, CHEVRON_CONFIG_KEY,
                              chevron_campaign_is_complete,
                              chevron_campaign_state, chevron_config_errors,
                              chevron_effective_grade, chevron_ready_bucket,
                              chevron_ready_minute, chevron_blended_quality,
                              chevron_receiving_quality_checks,
                              chevron_enabled_for_space,
                              chevron_mode, chevron_recipe,
                              chevron_job_metadata,
                              chevron_pass_allocation,
                              blended_quality_for_components,
                              resolve_optimized_blends,
                              update_chevron_campaign_state)
from mph_sim.domain import Job
from mph_sim.optimizer import (_blend_campaign_records,
                               _prepare_blend_design_scenario)
from mph_sim.planning_time import resample_scenario_window
from mph_sim.validation import validate_scenario
from ui.theme import (BLEND_PURPLE, _gantt_hover_summary, gantt_figure,
                      jobs_to_gantt_df)


def _single_matrix_policy(scenario):
    """Give recipe-specific tests an explicit fixture independent of defaults."""
    scenario.config[CHEVRON_CONFIG_KEY]["global_mode"] = BLEND_MODE_MATRIX
    scenario.config[CHEVRON_CONFIG_KEY]["matrix_catalog"] = [{
        "id": "TEST-50-50", "name": "Equal TBT / low-Cd shares",
        "components": [
            {"grade_id": "TBT", "target_fraction": 0.5},
            {"grade_id": "BT-LOW-CD", "target_fraction": 0.5},
        ],
    }]
    return scenario


def _legacy_fixed_policy(scenario):
    """Keep fixed-campaign compatibility tests independent of v7 defaults."""
    _single_matrix_policy(scenario)
    scenario.config[CHEVRON_CONFIG_KEY].update({
        "model_version": 6, "quantity_mode": "FIXED",
        "global_result_grade": "TBT", "global_batch_t": 12000,
        "global_ready_minute": 2880, "passes_per_cycle": 20,
        "global_minimum_component_fraction": 0.05,
    })
    return scenario


def test_m16_construction_blend_is_optional_and_optimized_by_default():
    scenario = datasets.end_to_end()

    assert chevron_job_metadata(
        scenario, "HP-07", "TBT")["method"] == "direct"
    assert scenario.config[CHEVRON_CONFIG_KEY]["enabled"] is True
    assert scenario.config[CHEVRON_CONFIG_KEY]["global_mode"] == BLEND_MODE_OPTIMIZED

    # Enabling a pile makes both matrices eligible without choosing one.
    scenario.config[CHEVRON_CONFIG_KEY]["enabled"] = True
    scenario.config[CHEVRON_CONFIG_KEY]["enabled_spaces"] = ["HP-07"]
    scenario.config[CHEVRON_CONFIG_KEY]["global_mode"] = BLEND_MODE_MATRIX
    assert len(scenario.config[CHEVRON_CONFIG_KEY]["matrix_catalog"]) == 2
    assert chevron_recipe(scenario, "HP-07") == []
    assert chevron_job_metadata(
        scenario, "HP-07", "TBT")["method"] == "direct"

    scenario.config[CHEVRON_CONFIG_KEY]["proposed_matrix_by_space"] = {
        "HP-07": "MPH-HUMID-01"}
    metadata = chevron_job_metadata(scenario, "HP-07", "TBT")
    assert metadata["method"] == "chevron"
    assert metadata["blend_mode"] == "blend matrix"
    assert metadata["chevron_passes_per_cycle"] == 0
    assert metadata["chevron_grade_passes"] == 0
    assert chevron_pass_allocation(scenario, "HP-07") == []


def test_m16_constructed_pile_quality_is_recipe_weighted():
    scenario = _single_matrix_policy(datasets.end_to_end())
    scenario.config[CHEVRON_CONFIG_KEY]["enabled"] = True
    scenario.config[CHEVRON_CONFIG_KEY]["enabled_spaces"] = ["HP-07"]
    quality = chevron_blended_quality(scenario, "HP-07")
    for parameter in set(scenario.grade("TBT").quality) | set(
            scenario.grade("BT-LOW-CD").quality):
        expected = (
            scenario.grade("TBT").quality.get(parameter, 0.0)
            + scenario.grade("BT-LOW-CD").quality.get(parameter, 0.0)) / 2.0
        assert quality.get(parameter, 0.0) == expected


def test_blending_showcase_is_enabled_and_on_spec_for_dry_feed():
    scenario = datasets.end_to_end()

    datasets.apply_planning_demand_profile(scenario, "BLEND_SHOWCASE")

    assert chevron_enabled_for_space(scenario, "HP-07") is True
    assert len(scenario.config[CHEVRON_CONFIG_KEY]["matrix_catalog"]) == 2
    assert chevron_recipe(scenario, "HP-07") == []
    scenario.config[CHEVRON_CONFIG_KEY]["global_mode"] = BLEND_MODE_MATRIX
    scenario.config[CHEVRON_CONFIG_KEY]["proposed_matrix_by_space"] = {
        "HP-07": "MPH-HUMID-01"}
    assert chevron_recipe(scenario, "HP-07") == [
        {"grade_id": "TBT", "target_fraction": 0.10},
        {"grade_id": "BT-LOW-CD", "target_fraction": 0.90},
    ]
    quality = chevron_blended_quality(scenario, "HP-07")
    assert quality["BPL"] == pytest.approx(68.85)
    assert quality["Cd"] == pytest.approx(20.213)
    dry_checks = [
        check for check in chevron_receiving_quality_checks(
            scenario, "HP-07")
        if check["destination"] == "DRY-FEED"
    ]
    assert dry_checks
    assert all(check["on_spec"] for check in dry_checks)

    datasets.apply_planning_demand_profile(scenario, "FULL_FLOW")
    assert chevron_enabled_for_space(scenario, "HP-07") is True
    assert "BLENDING_SHOWCASE" not in scenario.config


def test_blend_quality_uses_dry_solids_for_chemistry_and_wet_mass_for_moisture():
    scenario = datasets.end_to_end()
    tbt = scenario.grade("TBT")
    low_cd = scenario.grade("BT-LOW-CD")
    tbt.product_moisture_pct = 10.0
    low_cd.product_moisture_pct = 20.0
    tbt.quality["moisture"] = 10.0
    low_cd.quality["moisture"] = 20.0
    recipe = [
        {"grade_id": "TBT", "target_fraction": 0.5},
        {"grade_id": "BT-LOW-CD", "target_fraction": 0.5},
    ]

    quality = blended_quality_for_components(scenario, recipe)

    expected_bpl = (
        0.5 * 0.9 * tbt.quality["BPL"]
        + 0.5 * 0.8 * low_cd.quality["BPL"]
    ) / (0.5 * 0.9 + 0.5 * 0.8)
    assert quality["BPL"] == pytest.approx(expected_bpl)
    assert quality["moisture"] == 15.0


def test_disabled_or_non_recipe_stack_is_not_labelled_chevron():
    scenario = _single_matrix_policy(datasets.end_to_end())
    scenario.config[CHEVRON_CONFIG_KEY]["enabled"] = False
    scenario.config[CHEVRON_CONFIG_KEY]["enabled_spaces"] = []
    assert chevron_job_metadata(scenario, "HP-07", "BT")["method"] == "direct"

    scenario.config[CHEVRON_CONFIG_KEY]["enabled"] = True
    scenario.config[CHEVRON_CONFIG_KEY]["enabled_spaces"] = ["HP-07"]
    assert chevron_job_metadata(
        scenario, "HP-07", "BT")["method"] == "direct"


def test_blending_can_be_enabled_per_stockpile():
    scenario = datasets.end_to_end()
    scenario.config[CHEVRON_CONFIG_KEY]["enabled_spaces"] = ["HP-07"]
    scenario.config[CHEVRON_CONFIG_KEY]["enabled"] = True

    assert chevron_enabled_for_space(scenario, "HP-07") is True
    assert chevron_enabled_for_space(scenario, "HP-08") is False
    assert chevron_job_metadata(scenario, "HP-08", "BT")["method"] \
        == "direct"


def test_invalid_chevron_recipe_blocks_input_validation():
    scenario = datasets.end_to_end()
    scenario.config[CHEVRON_CONFIG_KEY] = {
        "enabled": True,
        "passes_per_cycle": 1,
        "recipes": {
            "HP-07": [
                {"grade_id": "BT", "target_fraction": 0.7},
                {"grade_id": "DOES-NOT-EXIST", "target_fraction": 0.2},
            ],
        },
    }
    errors = chevron_config_errors(scenario)
    assert any("total 100%" in error for error in errors)
    assert any("unknown grade" in error for error in errors)
    issues = validate_scenario(scenario)
    assert any(issue.code == "BAD_CHEVRON_RECIPE" for issue in issues)


def test_chevron_recipe_is_visible_in_stack_job_hover_and_export():
    scenario = _legacy_fixed_policy(datasets.end_to_end())
    scenario.config[CHEVRON_CONFIG_KEY]["enabled"] = True
    scenario.config[CHEVRON_CONFIG_KEY]["enabled_spaces"] = ["HP-07"]
    metadata = chevron_job_metadata(scenario, "HP-07", "TBT")
    job = Job(
        "J-CHEV", "ST01-B", "stack", "HP-07", grade="TBT",
        rate_tph=1000, tonnage_t=1000, start_bucket=0, end_bucket=0,
        method=metadata["method"], blend_mode=metadata["blend_mode"],
        blend_recipe=metadata["blend_recipe"],
        chevron_passes_per_cycle=metadata["chevron_passes_per_cycle"],
        chevron_grade_passes=metadata["chevron_grade_passes"],
    )
    frame = jobs_to_gantt_df(scenario, [job])
    row = frame.iloc[0]
    assert row.stacking_method == "chevron"
    assert row.blend_recipe == "TBT 50% + BT-LOW-CD 50%"
    hover = _gantt_hover_summary(row)
    assert "Chevron layers" in hover
    assert "this quality 10/20 passes per cycle" in hover
    figure = gantt_figure(frame, color_by="kind", group_by="machine")
    [blend_trace] = [trace for trace in figure.data
                     if trace.name == "blending"]
    assert blend_trace.marker.color == BLEND_PURPLE


def test_ready_time_is_canonical_across_grid_refinement_and_legacy_fallback():
    scenario = _legacy_fixed_policy(datasets.end_to_end())
    config = scenario.config[CHEVRON_CONFIG_KEY]
    config["global_ready_minute"] = 48 * 60

    assert chevron_ready_minute(scenario, "HP-07") == 48 * 60
    refined = resample_scenario_window(
        scenario, window_start_minute=0, window_duration_min=240,
        step_min=15)
    assert chevron_ready_minute(refined, "HP-07") == 48 * 60
    assert chevron_ready_bucket(refined, "HP-07") == 48 * 4


def test_campaign_progress_is_actual_grade_specific_idempotent_and_not_time_based():
    scenario = _legacy_fixed_policy(datasets.end_to_end())
    config = scenario.config[CHEVRON_CONFIG_KEY]
    config["enabled"] = True
    config["enabled_spaces"] = ["HP-07"]
    scenario.space("HP-07").mass_t = 12_000

    # Neither the ready time nor aggregate inventory manufactures completion.
    update_chevron_campaign_state(
        scenario, final_inventory_by_space={"HP-07": 12_000})
    assert chevron_campaign_state(scenario, "HP-07")["status"] \
        == CAMPAIGN_NOT_STARTED

    tbt = Job(
        "J-TBT", "ST01-B", "stack", "HP-07", grade="TBT",
        tonnage_t=6_000, status="RUNNING", method="chevron",
        blend_mode="chevron stacking", end_minute=60)
    low_cd = Job(
        "J-LOW", "ST01-B", "stack", "HP-07", grade="BT-LOW-CD",
        tonnage_t=6_000, status="FINISHED", method="chevron",
        blend_mode="chevron stacking", end_minute=120)

    update_chevron_campaign_state(
        scenario, [tbt], actual_tonnage_by_job={"J-TBT": 3_000})
    first = chevron_campaign_state(scenario, "HP-07")
    assert first["status"] == CAMPAIGN_BUILDING
    assert first["stacked_t_by_grade"] == {"TBT": 3_000.0}
    update_chevron_campaign_state(
        scenario, [tbt], actual_tonnage_by_job={"J-TBT": 3_000})
    assert chevron_campaign_state(scenario, "HP-07")[
        "stacked_t_by_grade"] == {"TBT": 3_000.0}

    update_chevron_campaign_state(
        scenario, [tbt], actual_tonnage_by_job={"J-TBT": 6_000})
    update_chevron_campaign_state(
        scenario, [low_cd], final_inventory_by_space={"HP-07": 12_000})
    complete = chevron_campaign_state(scenario, "HP-07")
    assert complete["status"] == CAMPAIGN_COMPLETE
    assert complete["stacked_t_by_grade"] == {
        "TBT": 6_000.0, "BT-LOW-CD": 6_000.0}
    assert chevron_campaign_is_complete(scenario, "HP-07") is True


def test_completed_blend_identity_and_quality_survive_disabling_future_builds():
    scenario = _legacy_fixed_policy(datasets.end_to_end())
    config = scenario.config[CHEVRON_CONFIG_KEY]
    config["enabled"] = True
    config["enabled_spaces"] = ["HP-07"]
    jobs = [
        Job("J1", "ST01-B", "stack", "HP-07", grade="TBT",
            tonnage_t=6_000, status="FINISHED", method="chevron"),
        Job("J2", "ST01-B", "stack", "HP-07", grade="BT-LOW-CD",
            tonnage_t=6_000, status="FINISHED", method="chevron"),
    ]
    update_chevron_campaign_state(
        scenario, jobs, final_inventory_by_space={"HP-07": 12_000})
    config = scenario.config[CHEVRON_CONFIG_KEY]
    config["enabled"] = False
    config["enabled_spaces"] = []

    assert chevron_effective_grade(scenario, "HP-07") == "TBT"
    quality = chevron_blended_quality(scenario, "HP-07")
    for parameter in set(scenario.grade("TBT").quality) | set(
            scenario.grade("BT-LOW-CD").quality):
        expected = (
            scenario.grade("TBT").quality.get(parameter, 0.0)
            + scenario.grade("BT-LOW-CD").quality.get(parameter, 0.0)) / 2.0
        assert quality.get(parameter, 0.0) == expected
    assert "HP-07" in _blend_campaign_records(scenario)


def test_validation_ignores_disabled_drafts_and_accepts_carried_inventory():
    scenario = datasets.end_to_end()
    config = scenario.config[CHEVRON_CONFIG_KEY]
    config["enabled"] = False
    config["enabled_spaces"] = []
    config["matrix_catalog"] = [{
        "id": "DRAFT", "name": "draft", "components": [
            {"grade_id": "DELETED", "target_fraction": 1.0}]}]
    assert chevron_config_errors(scenario) == []

    scenario = _legacy_fixed_policy(datasets.end_to_end())
    config = scenario.config[CHEVRON_CONFIG_KEY]
    config["enabled"] = True
    config["enabled_spaces"] = ["HP-07"]
    scenario.space("HP-07").mass_t = 3_000
    config["campaign_states_by_space"] = {
        "HP-07": {
            "status": "BUILDING",
            "stacked_t_by_grade": {"TBT": 3_000},
        },
    }
    assert chevron_config_errors(scenario) == []

    scenario.space("HP-07").mass_t = 12_000
    config["campaign_states_by_space"]["HP-07"] = {
        "status": "COMPLETE",
        "stacked_t_by_grade": {"TBT": 6_000, "BT-LOW-CD": 6_000},
    }
    assert chevron_config_errors(scenario) == []


def test_validation_rejects_raw_recipe_and_raw_result_grades():
    scenario = _legacy_fixed_policy(datasets.end_to_end())
    config = scenario.config[CHEVRON_CONFIG_KEY]
    config["enabled"] = True
    config["enabled_spaces"] = ["HP-07"]
    config["matrix_catalog"][0]["components"][0]["grade_id"] = "RAW-BT"
    config["global_result_grade"] = "RAW-BT"

    errors = chevron_config_errors(scenario)
    assert any("blend inputs must be washed grades" in error
               for error in errors)
    assert any("resulting grade" in error and "must be a washed grade" in error
               for error in errors)


def _enable_optimized(scenario, rows):
    _legacy_fixed_policy(scenario)
    config = scenario.config[CHEVRON_CONFIG_KEY]
    config["enabled"] = True
    config["enabled_spaces"] = ["HP-07"]
    config["global_mode"] = BLEND_MODE_OPTIMIZED
    config["optimized_share_bounds"] = rows
    config["proposed_recipes_by_space"] = {}
    return config


def test_legacy_recipe_defaults_to_matrix_mode():
    scenario = datasets.end_to_end()
    scenario.config[CHEVRON_CONFIG_KEY].pop("global_mode")
    assert chevron_mode(scenario, "HP-07") == BLEND_MODE_MATRIX


def test_optimized_mode_decides_whole_tonnes_and_derived_percentages():
    scenario = datasets.end_to_end()
    _enable_optimized(scenario, [
        {"grade_id": "TBT", "min_fraction": 0.0, "max_fraction": 1.0},
        {"grade_id": "BT-LOW-CD", "min_fraction": 0.0,
         "max_fraction": 1.0},
        {"grade_id": "MT-LOW-CD", "min_fraction": 0.0,
         "max_fraction": 1.0},
    ])

    resolved, errors = resolve_optimized_blends(scenario)

    assert errors == []
    allocation = resolved["HP-07"]
    assert sum(int(row["tonnage_t"]) for row in allocation) == 12_000
    assert len(allocation) >= 2
    assert all(
        row["target_fraction"] == row["tonnage_t"] / 12_000
        for row in allocation)
    checks = chevron_receiving_quality_checks(scenario, "HP-07")
    assert checks
    assert all(check["on_spec"] for check in checks)


def test_optimized_share_bounds_are_enforced_and_validated():
    scenario = datasets.end_to_end()
    _enable_optimized(scenario, [
        {"grade_id": "TBT", "min_fraction": 0.20,
         "max_fraction": 0.40},
        {"grade_id": "BT-LOW-CD", "min_fraction": 0.60,
         "max_fraction": 0.80},
    ])
    assert chevron_config_errors(scenario) == []
    resolved, errors = resolve_optimized_blends(scenario)
    assert errors == []
    shares = {row["grade_id"]: row["target_fraction"]
              for row in resolved["HP-07"]}
    assert 0.20 <= shares["TBT"] <= 0.40
    assert 0.60 <= shares["BT-LOW-CD"] <= 0.80

    bad = datasets.end_to_end()
    _enable_optimized(bad, [
        {"grade_id": "TBT", "min_fraction": 0.70,
         "max_fraction": 0.80},
        {"grade_id": "BT-LOW-CD", "min_fraction": 0.50,
         "max_fraction": 0.60},
    ])
    assert any("sum(minimum)" in error
               for error in chevron_config_errors(bad))


def test_first_carried_optimized_stack_freezes_resolved_matrix():
    scenario = datasets.end_to_end()
    config = _enable_optimized(scenario, [
        {"grade_id": "TBT", "min_fraction": 0.20,
         "max_fraction": 0.40},
        {"grade_id": "BT-LOW-CD", "min_fraction": 0.60,
         "max_fraction": 0.80},
    ])
    resolved, errors = resolve_optimized_blends(scenario)
    assert errors == []
    matrix = resolved["HP-07"]
    job = Job(
        "J-AUTO-BLEND", "ST01-B", "stack", "HP-07",
        grade=matrix[0]["grade_id"], tonnage_t=100, method="chevron",
        blend_mode="optimized shares", blend_recipe=matrix,
        status="FINISHED")

    update_chevron_campaign_state(scenario, [job])
    state = chevron_campaign_state(scenario, "HP-07")
    assert state["status"] == CAMPAIGN_BUILDING
    assert state["resolution_source"] == BLEND_MODE_OPTIMIZED
    frozen = chevron_recipe(scenario, "HP-07")
    config = scenario.config[CHEVRON_CONFIG_KEY]
    config["proposed_recipes_by_space"]["HP-07"] = [
        {"grade_id": "TBT", "target_fraction": 0.5, "tonnage_t": 6_000},
        {"grade_id": "BT-LOW-CD", "target_fraction": 0.5,
         "tonnage_t": 6_000},
    ]
    assert chevron_recipe(scenario, "HP-07") == frozen


def test_zero_actual_tonnage_does_not_freeze_an_optimized_matrix():
    scenario = datasets.end_to_end()
    _enable_optimized(scenario, [
        {"grade_id": "TBT", "min_fraction": 0.20,
         "max_fraction": 0.40},
        {"grade_id": "BT-LOW-CD", "min_fraction": 0.60,
         "max_fraction": 0.80},
    ])
    resolved, errors = resolve_optimized_blends(scenario)
    assert errors == []
    job = Job(
        "J-ZERO", "ST01-B", "stack", "HP-07",
        grade=resolved["HP-07"][0]["grade_id"], tonnage_t=100,
        method="chevron", blend_mode="optimized shares",
        blend_recipe=resolved["HP-07"], status="RUNNING")

    update_chevron_campaign_state(
        scenario, [job], actual_tonnage_by_job={"J-ZERO": 0})

    state = chevron_campaign_state(scenario, "HP-07")
    assert state["status"] == CAMPAIGN_NOT_STARTED
    assert state["resolved_recipe"] == []


def test_planned_rolling_carry_does_not_physically_lock_the_matrix():
    scenario = datasets.end_to_end()
    config = _enable_optimized(scenario, [
        {"grade_id": "TBT", "min_fraction": 0.0,
         "max_fraction": 1.0},
        {"grade_id": "BT-LOW-CD", "min_fraction": 0.0,
         "max_fraction": 1.0},
    ])
    first = [
        {"grade_id": "TBT", "target_fraction": 0.25,
         "tonnage_t": 3_000},
        {"grade_id": "BT-LOW-CD", "target_fraction": 0.75,
         "tonnage_t": 9_000},
    ]
    config["proposed_recipes_by_space"]["HP-07"] = first
    job = Job(
        "J-PLAN", "ST01-B", "stack", "HP-07", grade="TBT",
        tonnage_t=100, method="chevron", blend_mode="optimized shares",
        blend_recipe=first, status="PLANNED")

    update_chevron_campaign_state(scenario, [job], include_planned=True)
    state = chevron_campaign_state(scenario, "HP-07")
    assert state["resolved_recipe"] == []

    second = [
        {"grade_id": "TBT", "target_fraction": 0.50,
         "tonnage_t": 6_000},
        {"grade_id": "BT-LOW-CD", "target_fraction": 0.50,
         "tonnage_t": 6_000},
    ]
    config["proposed_recipes_by_space"]["HP-07"] = second
    assert chevron_recipe(scenario, "HP-07") == second


def test_optional_unroutable_optimized_input_is_excluded_not_rejected():
    scenario = datasets.end_to_end()
    _enable_optimized(scenario, [
        {"grade_id": "TBT", "min_fraction": 0.0,
         "max_fraction": 1.0},
        {"grade_id": "BT-LOW-CD", "min_fraction": 0.0,
         "max_fraction": 1.0},
        # This quality exists in the catalogue, but the active M16 inbound
        # material routes do not supply it in this scenario.
        {"grade_id": "MT-LOW-CD", "min_fraction": 0.0,
         "max_fraction": 1.0},
    ])

    _prepared, errors = _prepare_blend_design_scenario(scenario)

    assert errors == []


def test_plan_rejects_a_mandatory_optimized_input_without_an_inbound_route():
    scenario = datasets.end_to_end()
    _enable_optimized(scenario, [
        {"grade_id": "TBT", "min_fraction": 0.0, "max_fraction": 0.80},
        {"grade_id": "BT-LOW-CD", "min_fraction": 0.0,
         "max_fraction": 0.80},
        {"grade_id": "MT-LOW-CD", "min_fraction": 0.10,
         "max_fraction": 0.20},
    ])

    _prepared, errors = _prepare_blend_design_scenario(scenario)

    assert any("positive minimum share cannot be supplied for MT-LOW-CD"
               in error for error in errors)
