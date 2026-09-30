"""Sensitivity variants preserve comparable inputs and explicit assumptions."""
from copy import deepcopy
from collections import defaultdict

import pytest

from mph_sim import datasets
from mph_sim.domain import DemandEntry
from mph_sim.persistence import scenario_from_json, scenario_to_json
from mph_sim.scenarios import BUSINESS_CASE_PRESETS, make_business_case_scenario
from mph_sim.validation import validate_scenario


def _campaign_totals(scenario):
    totals = defaultdict(float)
    for destination in scenario.destinations:
        for bucket in range(scenario.sim.horizon_buckets):
            key = (destination.id, scenario.sim.bucket_dt(bucket).date(),
                   scenario.demand_grade(destination.id, bucket))
            totals[key] += scenario.demand_tph(destination.id, bucket) * scenario.sim.dt_h
    return dict(totals)


@pytest.mark.parametrize("preset", list(BUSINESS_CASE_PRESETS))
def test_variants_are_valid_copies_with_round_trip_manifests(preset):
    base = datasets.end_to_end()
    snapshot = deepcopy(base)
    variant = make_business_case_scenario(base, preset)
    assert base == snapshot
    assert variant is not base
    assert not [issue for issue in validate_scenario(variant) if issue.severity == "ERROR"]
    manifest = variant.config["BUSINESS_CASE_SCENARIO"]
    assert manifest["preset_id"] == preset
    assert manifest["basis"] == ("CURRENT INPUTS" if preset == "BASELINE" else "ASSUMED SENSITIVITY")
    restored = scenario_from_json(scenario_to_json(variant))
    assert restored.config["BUSINESS_CASE_SCENARIO"] == manifest
    assert variant.weights == base.weights


def test_peak_preserves_each_daily_grade_total_and_existing_process_targets():
    base = datasets.end_to_end()
    variant = make_business_case_scenario(base, "PEAK_DEMAND")
    assert _campaign_totals(variant) == pytest.approx(_campaign_totals(base))
    assert variant.demand_tph("PAP-1", 18) > base.demand_tph("PAP-1", 18)
    assert variant.demand_tph("PAP-1", 8) < base.demand_tph("PAP-1", 8)
    assert variant.config["PROCESS_STAGE_OUTPUT_TARGET_T"] == base.config["PROCESS_STAGE_OUTPUT_TARGET_T"]
    assert variant.config["CORRIDOR_DISPATCH_TARGET_T"] == base.config["CORRIDOR_DISPATCH_TARGET_T"]
    # An unavailable future unit's configured template is retained verbatim.
    assert [entry for entry in variant.demand if entry.dest_id == "PAP-2"] == [
        entry for entry in base.demand if entry.dest_id == "PAP-2"]


def test_low_stock_scales_segments_but_preserves_reserves_and_custom_history():
    base = datasets.end_to_end()
    base.config["CUSTOM_NOTE"] = "preserve me"
    base.space("HP-01").reserved_t = 2_000
    variant = make_business_case_scenario(base, "LOW_LOCAL_STOCK", {"stock_fraction": 0.1})
    assert variant.space("HP-01").mass_t == pytest.approx(base.space("HP-01").mass_t * 0.1)
    assert variant.space("HP-01").reserved_t == 2_000
    assert variant.space("HP-01").safety_stock_t == base.space("HP-01").safety_stock_t
    assert variant.space("LT-P1").mass_t == base.space("LT-P1").mass_t
    for segment, original in zip(variant.segments, base.segments):
        if original.space_id.startswith("HP-"):
            assert segment.mass_t == pytest.approx(original.mass_t * 0.1)
    assert variant.config["CUSTOM_NOTE"] == "preserve me"
    assert variant.config["BUSINESS_CASE_SCENARIO"]["changes"]


def test_variants_reject_invalid_inputs_and_preserve_baseline():
    base = datasets.end_to_end()
    original = deepcopy(base)
    cases = [
        ("LOW_LOCAL_STOCK", {"stock_fraction": float("nan")}),
        ("LOW_LOCAL_STOCK", {"stock_fraction": -0.1}),
        ("RECLAIMER_OUTAGE", {"machine_id": "missing"}),
        ("RECLAIMER_OUTAGE", {"start_hour": 167.0, "duration_hours": 4.0}),
        ("QUALITY_STRESS", {"cd_decrease": 10_000}),
        ("QUALITY_STRESS", {"destination_id": "missing"}),
        ("PEAK_DEMAND", {"undocumented_parameter": 2}),
    ]
    for preset, overrides in cases:
        with pytest.raises(ValueError):
            make_business_case_scenario(base, preset, overrides)
        assert base == original


def test_outage_keeps_existing_calendars_and_uses_physical_time():
    base = datasets.end_to_end()
    variant = make_business_case_scenario(base, "RECLAIMER_OUTAGE", {
        "start_hour": 8.5, "duration_hours": 2.25})
    assert variant.calendars[:-1] == base.calendars
    outage = variant.calendars[-1]
    assert (outage.start_minute, outage.end_minute) == (510, 645)
    assert (outage.start_bucket, outage.end_bucket) == (8, 10)
    assert variant.machine("RC01-A").rate_max_tph == base.machine("RC01-A").rate_max_tph


def test_low_stock_blocks_an_active_construction_ledger():
    base = datasets.end_to_end()
    base.config["CHEVRON_STACKING"]["campaign_states_by_space"] = {
        "HP-01": {"status": "BUILDING", "stacked_t_by_grade": {"TBT": 100.0}}}
    with pytest.raises(ValueError, match="before starting a blend campaign"):
        make_business_case_scenario(base, "LOW_LOCAL_STOCK")


def test_legacy_blend_migration_is_once_only_and_archives_old_inputs():
    scenario = datasets.end_to_end()
    scenario.config["BUILTIN_BASELINE_REVISION"] = "obsolete"
    legacy = {"model_version": 3, "enabled": True,
              "custom_operator_recipe": {"TBT": 0.6, "BT": 0.4}}
    scenario.config["CHEVRON_STACKING"] = deepcopy(legacy)
    assert datasets.migrate_loaded_builtin(scenario) == "solver"
    assert scenario.config["LEGACY_BLEND_POLICY_ARCHIVE"] == [legacy]
    assert scenario.config["CHEVRON_STACKING"]["model_version"] == 7
    migrated = deepcopy(scenario)
    assert datasets.migrate_loaded_builtin(scenario) == ""
    assert scenario == migrated


@pytest.mark.parametrize("factory", [datasets.mph_phosphate, datasets.end_to_end])
def test_fresh_factory_is_already_current_and_migration_changes_nothing(factory):
    scenario = factory()
    before = deepcopy(scenario)
    assert scenario.config["CHEVRON_STACKING"]["model_version"] == 7
    assert scenario.config["CHEVRON_STACKING"]["enabled"]
    assert datasets.migrate_loaded_builtin(scenario) == ""
    assert scenario == before
