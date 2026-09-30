"""Preset feed ceilings use the same planning horizon as their inputs."""
from copy import deepcopy
import math

import pytest

from mph_sim import datasets, prewash_blending, washing


def _preset(profile, hours):
    sc = datasets.end_to_end()
    sc.sim.horizon_buckets = hours
    sc.sim.horizon_minutes = hours * 60
    datasets.apply_planning_demand_profile(sc, profile)
    return sc


@pytest.mark.parametrize("hours", [24, 48, 168])
@pytest.mark.parametrize("profile", [datasets.DEFAULT_PLANNING_DEMAND_PROFILE])
def test_shortened_preset_gets_yield_aware_feed_ceilings(profile, hours):
    sc = _preset(profile, hours)
    cfg = washing.defaults()
    cfg["enabled"] = True
    staged = washing.stage_configuration(sc, cfg)
    assert "yield-aware ceilings" in staged.config["_WASHING_DERIVED_POLICY_NOTE"]
    targets = staged.config["PROCESS_STAGE_OUTPUT_TARGET_T"]
    assert not any(stage.startswith(("BEN-MINE-STACK", "BEN-MINE-RECLAIM", "BEN-RAW-STACK"))
                   for stage in targets)
    for destination in staged.destinations:
        if destination.demand_kind != "process_feed" or not targets.get(destination.process_stage):
            continue
        sources = prewash_blending.source_spaces_for_stage(staged, destination.process_stage)
        factors = [washing.output_factor(
            staged.quality_of(source),
            washing.treated_quality(staged.quality_of(source), mode),
            mode["dry_solids_yield"])
            for source in sources for mode in cfg["modes"].values()]
        if not factors:
            continue
        available_feed = sum(row.rate_tph * staged.sim.dt_h for row in staged.demand
                             if row.dest_id == destination.id)
        assert available_feed == math.ceil(targets[destination.process_stage] / min(factors) - 1e-9)
    before = deepcopy(staged)
    washing.reconcile_derived_policy(staged)
    assert staged == before


def test_short_horizon_operator_feed_commitment_is_not_reinterpreted():
    sc = _preset(datasets.DEFAULT_PLANNING_DEMAND_PROFILE, 24)
    feed_ids = {d.id for d in sc.destinations if d.demand_kind == "process_feed"}
    next(row for row in sc.demand if row.dest_id in feed_ids and row.rate_tph > 0).rate_tph += 123
    before_rows = deepcopy(sc.demand)
    before_targets = deepcopy(sc.config["PROCESS_STAGE_OUTPUT_TARGET_T"])
    cfg = washing.defaults()
    cfg["enabled"] = True
    staged = washing.stage_configuration(sc, cfg)
    assert "Custom raw-feed/production commitments retained" in staged.config["_WASHING_DERIVED_POLICY_NOTE"]
    assert staged.demand == before_rows
    assert staged.config["PROCESS_STAGE_OUTPUT_TARGET_T"] == before_targets
