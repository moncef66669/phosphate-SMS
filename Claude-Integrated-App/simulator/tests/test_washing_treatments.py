"""Numerical contracts for the provisional washing calibration."""
from copy import deepcopy
from dataclasses import asdict
import math
from types import SimpleNamespace

import pytest

from mph_sim.domain import (Grade, Site, StockSpace, Machine, Node, Link,
                            Destination, DemandEntry, ProcessTransfer, Scenario,
                            SimParams, ObjectiveWeights)
from mph_sim.optimizer import solve_plan, SolveOptions
from mph_sim.analysis_tools import build_run_bundle
from mph_sim.kpis import planned_delivery_quality, compute_kpis
from mph_sim.persistence import scenario_from_json, scenario_to_json
from mph_sim import datasets, washing
from mph_sim.simulator import Simulator
from mph_sim.validation import validate_scenario
from mph_sim.washing_seed import _g10_seed_variant, physical_seed
from ui.pages_monitor import (pap_g10_receipt_rows, treatment_quality_rows,
                              treatment_run_rows)


RAW_GLOBAL = dict(BPL=56.06, Cd=21., MgO=1.49, SiO2=13.71, CO2=8.21, moisture=15.)
RAW_HIGH = dict(BPL=55.11, Cd=25., MgO=1.5, SiO2=16.28, CO2=8.41, moisture=15.)
RAW_LOW = dict(BPL=58.28, Cd=13., MgO=1.13, SiO2=10.22, CO2=8.75, moisture=15.)
RAW_TBT = dict(BPL=54.10, Cd=16., MgO=1.52, SiO2=14.39, CO2=8.32, moisture=12.)
RAW_MODE_BLEND = dict(BPL=56., Cd=20., MgO=1.3, SiO2=14., CO2=8.5, moisture=15.)


def fixture(quality=None, demand=1000, mass=1000, residence=0):
    # Most mechanics tests use the higher-quality low-Cd feed so they isolate
    # mass, timing and replay behavior on the simple-wash route. Dedicated
    # calibration tests below cover normal/TBT feed and full washing.
    raw = RAW_LOW if quality is None else quality
    sc = Scenario("washing-test", sites=[Site("S", "Test", "hub")],
        grades=[Grade("RAW", "Raw", quality=dict(raw), material_class="raw_screened"),
                Grade("LEGACY", "Existing product", quality={"BPL": 63., "Cd": 35., "moisture": 15.}, material_class="washed")],
        spaces=[StockSpace("A", "S", "yard", "open", 5000, mass, "RAW", node_id="A", material_class="raw_screened"),
                StockSpace("P", "S", "product", "open", 5000, 0, "LEGACY", node_id="P", material_class="washed")],
        machines=[Machine("W", "S", "reclaimer", 0, 1000, ["A"], setup_min=0),
                  Machine("R", "S", "reclaimer", 0, 1000, ["P"], setup_min=0)],
        nodes=[Node("A", "S", "space"), Node("P", "S", "space"), Node("D", "S", "dest")],
        links=[Link("A-P", "A", "P", 1000), Link("P-D", "P", "D", 1000)],
        destinations=[Destination("PAP-1", "S", "D")],
        demand=[DemandEntry("PAP-1", t, demand if t == 2 else 0) for t in range(3)],
        process_transfers=[ProcessTransfer("WASH", "W", "A", "P", ["A-P"],
            operation_kind="reclaim", process_stage="WASH", treatment_family="washing", residence_min=residence)],
        sim=SimParams(horizon_buckets=3, max_solve_s=2),
        weights=ObjectiveWeights(w4_giveaway=0, w6_safety=0),
        config={"OBJECTIVE_MODE": "service_first"})
    cfg = washing.defaults()
    cfg["enabled"] = True
    return washing.stage_configuration(sc, cfg)


def solve(sc):
    return solve_plan(sc, SolveOptions(max_time_s=2, max_wall_time_s=10))


def test_provisional_calibration_is_not_a_planning_readiness_issue():
    issues = validate_scenario(fixture())
    assert not [issue for issue in issues
                if issue.severity == "WARNING"
                and issue.code.startswith("WASHING_")]


def test_s0_washing_preset_note_is_informational_not_a_warning():
    sc = datasets.get_scenario(datasets.S3_SCENARIO_NAME)
    datasets.apply_planning_demand_profile(sc, "BLEND_SHOWCASE")
    cfg = washing.defaults()
    cfg["enabled"] = True
    sc = washing.stage_configuration(sc, cfg)

    issues = validate_scenario(sc)
    assert not [issue for issue in issues if issue.severity == "WARNING"]
    assert any(issue.severity == "INFO"
               and issue.code == "WASHING_PRESET_FEED_POLICY"
               for issue in issues)


def mode_feed(plan, mode):
    return sum(flow["tonnage_t"] for job in plan.jobs for flow in job.material_flows if flow["treatment_id"] == mode)


def assert_material_balances(plan):
    for sid, materials in plan.material_inventory.items():
        for t, physical in enumerate(plan.inventory[sid]):
            assert sum(series[t] for series in materials.values()) == pytest.approx(physical)
        for mid, series in materials.items():
            for t in range(len(series) - 1):
                ins = outs = 0
                for job in plan.jobs:
                    for flow in job.material_flows:
                        if flow["bucket"] != t + plan.horizon_offset:
                            continue
                        if job.source_space == sid and flow["material_id"] == mid:
                            outs += flow["tonnage_t"]
                        if job.target_space == sid and flow["output_material_id"] == mid:
                            ins += flow["output_tonnage_t"]
                assert series[t + 1] == pytest.approx(series[t] + ins - outs)


def test_relative_changes_and_moisture_conversion():
    simple = washing.DEFAULT_MODES["simple"]
    q = washing.treated_quality(RAW_GLOBAL, simple)
    assert q["BPL"] == pytest.approx(62.7872)
    assert q["Cd"] == pytest.approx(19.95)
    assert q["moisture"] == pytest.approx(18.)
    assert washing.output_factor(RAW_GLOBAL, q, .85) == pytest.approx(
        .85 * .85 / .82)
    drier = {**q, "moisture": 0.}
    assert washing.output_factor(RAW_GLOBAL, drier, .85) == pytest.approx(.7225)


def test_higher_quality_low_cd_feed_uses_simple_washing():
    sc = fixture()
    before = deepcopy(sc)
    plan = solve(sc)
    assert plan.status in ("OPTIMAL", "FEASIBLE"), plan.reasons
    assert sum(plan.delivered["PAP-1"]) == 881
    assert mode_feed(plan, "simple") == 1000
    assert mode_feed(plan, "full") == 0
    assert_material_balances(plan)
    assert sc == before


def test_raw_tbt_requires_full_washing_to_reach_g10():
    simple_quality = washing.treated_quality(
        RAW_TBT, washing.DEFAULT_MODES["simple"])
    full_quality = washing.treated_quality(
        RAW_TBT, washing.DEFAULT_MODES["full"])
    limits = washing.DEFAULT_LIMITS
    assert not all(lo <= simple_quality[param] <= hi
                   for param, (lo, hi) in limits.items())
    assert all(lo <= full_quality[param] <= hi
               for param, (lo, hi) in limits.items())
    assert full_quality == pytest.approx({
        "BPL": 66.543, "Cd": 12.8, "MgO": .5472,
        "SiO2": 7.195, "CO2": 6.0736, "moisture": 14.4,
    })

    plan = solve(fixture(RAW_TBT, mass=5000))
    assert plan.status in ("OPTIMAL", "FEASIBLE"), plan.reasons
    assert sum(plan.delivered["PAP-1"]) == 1000
    assert mode_feed(plan, "simple") == 0
    assert mode_feed(plan, "full") > 0


def test_weekly_seed_prefers_g10_capable_mode_over_higher_yield():
    sc = fixture(RAW_TBT)
    simple_quality = washing.treated_quality(
        RAW_TBT, washing.DEFAULT_MODES["simple"])
    full_quality = washing.treated_quality(
        RAW_TBT, washing.DEFAULT_MODES["full"])
    sc.config["_WASHING_MODEL_CATALOG"] = {
        "simple-output": {"quality": simple_quality},
        "full-output": {"quality": full_quality},
    }
    variants = [
        SimpleNamespace(
            treatment_id="simple", yield_factor=.85,
            output_material_id="simple-output"),
        SimpleNamespace(
            treatment_id="full", yield_factor=.65,
            output_material_id="full-output"),
    ]

    assert _g10_seed_variant(sc, variants).treatment_id == "full"

    sc.config["_WASHING_MODEL_CATALOG"]["simple-output"]["quality"] = \
        washing.treated_quality(RAW_LOW, washing.DEFAULT_MODES["simple"])
    assert _g10_seed_variant(sc, variants).treatment_id == "simple"


def test_label_only_seed_excludes_typed_pap_and_chevron_decisions():
    sc = fixture(RAW_TBT)
    sc.config["CHEVRON_STACKING"] = {
        "enabled": True,
        "enabled_spaces": ["P"],
        "recipes": {"P": [
            {"grade_id": "A", "target_fraction": .5},
            {"grade_id": "B", "target_fraction": .5},
        ]},
    }
    captured = {}

    def inspect_legacy(legacy, _options):
        captured["scenario"] = legacy
        return SimpleNamespace(status="UNKNOWN", breakdown={}, reasons=[])

    assert physical_seed(
        sc, [], inspect_legacy, SolveOptions(max_time_s=1)) is None
    legacy = captured["scenario"]
    assert all(entry.rate_tph == 0 for entry in legacy.demand
               if entry.dest_id == "PAP-1")
    assert legacy.config["CHEVRON_STACKING"]["enabled_spaces"] == []
    assert sc.config["CHEVRON_STACKING"]["enabled_spaces"] == ["P"]
    assert any(entry.rate_tph > 0 for entry in sc.demand
               if entry.dest_id == "PAP-1")


def test_analysis_bundle_preserves_both_washing_candidates_and_selection():
    sc = fixture()
    plan = solve(sc)
    bundle = build_run_bundle(sc, plan, compute_kpis(sc, plan))

    candidates = [row for row in bundle["alternatives"]
                  if row["treatment_id"] in {"simple", "full"}]
    assert {row["treatment_id"] for row in candidates} == {"simple", "full"}
    assert any(row["selected"] and row["treatment_id"] == "simple"
               for row in candidates)
    assert not any(row["selected"] and row["treatment_id"] == "full"
                   for row in candidates)
    source_paths = {row["source_path"] for row in bundle["measurements"]}
    assert "/config/WASHING_TREATMENTS/modes/simple/dry_solids_yield" \
        in source_paths


def test_full_washing_can_blend_with_simple_when_simple_alone_is_offspec():
    sc = fixture(RAW_MODE_BLEND)
    plan = solve(sc)
    assert plan.status in ("OPTIMAL", "FEASIBLE"), plan.reasons
    assert sum(plan.delivered["PAP-1"]) > 0
    assert mode_feed(plan, "simple") > 0
    assert mode_feed(plan, "full") > 0
    q = planned_delivery_quality(sc, plan, sc.destinations[0], 2)
    assert q["BPL"] >= 62.5
    assert q["Cd"] <= 18
    assert q["MgO"] <= .8
    assert q["SiO2"] <= 7.5
    assert 6 <= q["CO2"] <= 6.5
    assert_material_balances(plan)


def test_full_only_retains_exact_demo_yield_and_quality():
    sc = fixture(RAW_TBT, mass=5000)
    sc.config[washing.CONFIG_KEY]["modes"]["simple"]["available"] = False
    plan = solve(sc)
    assert sum(plan.delivered["PAP-1"]) == 1000
    assert mode_feed(plan, "full") > 0
    q = planned_delivery_quality(sc, plan, sc.destinations[0], 2)
    assert q["Cd"] == pytest.approx(12.8)
    assert q["SiO2"] == pytest.approx(7.195)


@pytest.mark.parametrize("same_grade_label", [False, True])
def test_optimizer_blends_actual_raw_streams_before_simple_washing(same_grade_label):
    sc = fixture(RAW_HIGH)
    if not same_grade_label:
        sc.grades.append(Grade("LOW", "Raw low Cd", quality=RAW_LOW, material_class="raw_screened"))
    sc.spaces += [StockSpace("B", "S", "yard", "open", 5000, 1000,
                            "RAW" if same_grade_label else "LOW", quality=RAW_LOW,
                            node_id="B", material_class="raw_screened"),
                  StockSpace("MIX", "S", "process", "open", 5000, 0, "RAW", node_id="MIX", material_class="raw_screened")]
    sc.nodes += [Node("B", "S", "space"), Node("MIX", "S", "space")]
    sc.links = [Link("A-MIX", "A", "MIX", 1000), Link("B-MIX", "B", "MIX", 1000),
                Link("MIX-P", "MIX", "P", 1000), Link("P-D", "P", "D", 1000)]
    sc.machines[0].reachable_spaces = ["MIX"]
    sc.machines += [Machine("RA", "S", "reclaimer", 0, 1000, ["A"], setup_min=0),
                    Machine("RB", "S", "reclaimer", 0, 1000, ["B"], setup_min=0)]
    sc.process_transfers[0].source_space = "MIX"
    sc.process_transfers[0].path = ["MIX-P"]
    sc.process_transfers[0].depends_on_stage = "RAWBLEND"
    sc.process_transfers += [ProcessTransfer("FA", "RA", "A", "MIX", ["A-MIX"], operation_kind="reclaim", process_stage="RAWBLEND"),
                            ProcessTransfer("FB", "RB", "B", "MIX", ["B-MIX"], operation_kind="reclaim", process_stage="RAWBLEND")]
    sc.sim.horizon_buckets = 5
    sc.demand = [DemandEntry("PAP-1", t, 1000 if t == 4 else 0, requested_grade="G10") for t in range(5)]
    plan = solve(sc)
    assert plan.status in ("OPTIMAL", "FEASIBLE"), plan.reasons
    assert sum(plan.delivered["PAP-1"]) == 1000
    assert mode_feed(plan, "simple") > mode_feed(plan, "full") > 0
    grades = {flow["grade"] for job in plan.jobs if job.machine == "W" for flow in job.material_flows}
    assert grades == ({"RAW"} if same_grade_label else {"RAW", "LOW"})
    assert len({flow["material_id"] for job in plan.jobs if job.machine == "W"
                for flow in job.material_flows}) == 2
    assert planned_delivery_quality(sc, plan, sc.destinations[0], 4)["Cd"] <= 18
    assert_material_balances(plan)


def test_raw_feed_minimization_protects_delivery_and_prefers_recovery():
    sc = fixture(demand=420)
    plan = solve(sc)
    assert sum(plan.delivered["PAP-1"]) == 420
    assert mode_feed(plan, "simple") == 477
    assert mode_feed(plan, "full") == 0
    assert any(row["name"] == "raw_feed" for row in plan.objective_pass_records)
    assert plan.breakdown["washing_simple_loss_dry_t"] == pytest.approx(61.05)


def test_raw_blend_can_use_simple_washing():
    blend = {p: .02 * RAW_HIGH[p] + .98 * RAW_LOW[p]
             for p in washing.PARAMETERS}
    sc = fixture(blend)
    plan = solve(sc)
    assert sum(plan.delivered["PAP-1"]) == 881
    assert mode_feed(plan, "simple") == 1000
    q = planned_delivery_quality(sc, plan, sc.destinations[0], 2)
    assert q["BPL"] == pytest.approx(65.202592)
    assert q["Cd"] == pytest.approx(12.578)


@pytest.mark.parametrize("raw", [RAW_GLOBAL, RAW_HIGH, RAW_LOW])
def test_execution_replays_treated_mass_and_dry_blended_quality(raw):
    sc = fixture(raw)
    plan = solve(sc)
    sim = Simulator(sc, plan, auto_replan=False)
    sim.run_to_end()
    assert sum(sim.delivered["PAP-1"]) == pytest.approx(sum(plan.delivered["PAP-1"]), abs=1e-5)
    expected = planned_delivery_quality(sc, plan, sc.destinations[0], 2)
    for param, value in expected.items():
        assert sim.delivered_q["PAP-1"][2][param] == pytest.approx(value, abs=1e-6)
    for sid, pools in sim.material_mass.items():
        assert sum(pools.values()) == pytest.approx(sim.inv[sid], abs=1e-5)
    actual = compute_kpis(sc, plan, sim)
    assert actual["washing_simple_feed_t"] == pytest.approx(mode_feed(plan, "simple"))
    assert actual["washing_full_feed_t"] == pytest.approx(mode_feed(plan, "full"))


def test_residence_override_delays_release_and_survives_snapshot():
    sc = fixture(demand=420)
    sc.config[washing.CONFIG_KEY]["modes"]["simple"]["residence_min"] = 90
    sc.sim.horizon_buckets = 5
    sc.demand = [DemandEntry("PAP-1", t, 420 if t == 4 else 0, requested_grade="G10") for t in range(5)]
    plan = solve(sc)
    assert sum(plan.delivered["PAP-1"]) == 420
    sim = Simulator(sc, plan, auto_replan=False)
    sim.step()
    snap = sim.snapshot_scenario()
    assert sum(sum(pools.values()) for pools in snap.config["_WASHING_MODEL_INITIAL"].values()) == pytest.approx(sum(sim.inv.values()))
    for sid, pools in sim.material_pending.items():
        for mid, batches in pools.items():
            if batches:
                assert sim._material_available(sid, mid, 60) <= sim.material_mass[sid][mid]
    sim.run_to_end()
    assert sum(sim.delivered["PAP-1"]) == pytest.approx(420)


def test_assay_change_is_not_replaced_by_planned_washing_quality():
    from mph_sim.domain import SimEvent
    sc = fixture()
    plan = solve(sc)
    sim = Simulator(sc, plan, [SimEvent("ASSAY", "offspec_assay", 0, "A", params={"Cd": 100.})], auto_replan=False)
    sim.run_to_end()
    assert sim.actual_washing_flows
    assert all(row["quality"]["Cd"] == 100 for row in sim.actual_washing_flows)
    assert sum(sim.delivered["PAP-1"]) == 0
    assert sim.delivered_grade["PAP-1"][2] == ""
    assert compute_kpis(sc, plan, sim)["unmet_t"] > 0


def test_modes_share_one_machine_capacity():
    plan = solve(fixture(mass=5000))
    for bucket in range(3):
        active_modes = {flow["treatment_id"] for job in plan.jobs for flow in job.material_flows
                        if flow["bucket"] == bucket and flow["treatment_id"]}
        assert len(active_modes) <= 1
        assert sum(flow["tonnage_t"] for job in plan.jobs if job.machine == "W"
                   for flow in job.material_flows if flow["bucket"] == bucket) <= 1000


@pytest.mark.parametrize("value", [-1, 0, 1.1, math.nan, math.inf, True, "bad"])
def test_bad_yield_is_rejected_atomically(value):
    sc = fixture()
    cfg = washing.settings(sc)
    cfg["modes"]["simple"]["dry_solids_yield"] = value
    before = deepcopy(sc)
    with pytest.raises(ValueError):
        washing.stage_configuration(sc, cfg)
    assert sc == before
    sc.config[washing.CONFIG_KEY] = cfg
    assert solve(sc).status == "ERROR"


def test_demo_g10_is_a_specification_not_a_relabelled_output():
    sc = fixture()
    assert sc.destinations[0].requested_grade == "G10"
    assert sc.grade("G10").is_specification
    assert not sc.grade("G10").quality
    assert all(spec.hard for spec in sc.destinations[0].spec)
    plan = solve(sc)
    assert all(flow["output_grade"] != "G10" for job in plan.jobs for flow in job.material_flows)
    pap_jobs = [job for job in plan.jobs if job.dest_id == "PAP-1"]
    assert pap_jobs
    assert all(job.certified_grade == "G10" and job.output_grade == "G10"
               for job in pap_jobs)
    assert all(flow["certified_grade"] == "G10" for job in pap_jobs
               for flow in job.material_flows)


def test_pap_demand_is_always_g10_across_presets_and_treatment_toggle():
    sc = datasets.end_to_end()

    def assert_g10_contract(scenario):
        assert scenario.grade("G10").is_specification
        assert all(
            scenario.destination(destination_id).requested_grade == "G10"
            for destination_id in washing.PAP_DESTINATION_IDS)
        assert {
            entry.requested_grade for entry in scenario.demand
            if entry.dest_id in washing.PAP_DESTINATION_IDS
        } == {"G10"}
        assert not [
            issue for issue in validate_scenario(scenario)
            if issue.code == "PAP_REQUIRES_G10"]

    assert_g10_contract(sc)
    for profile_id in datasets.PLANNING_DEMAND_PROFILES:
        datasets.apply_planning_demand_profile(sc, profile_id)
        assert_g10_contract(sc)

    cfg = washing.settings(sc)
    cfg["enabled"] = True
    sc = washing.stage_configuration(sc, cfg)
    assert_g10_contract(sc)
    cfg = washing.settings(sc)
    cfg["enabled"] = False
    sc = washing.stage_configuration(sc, cfg)
    assert_g10_contract(sc)

    sc.demand[0].requested_grade = "TBT"
    assert any(issue.code == "PAP_REQUIRES_G10"
               for issue in validate_scenario(sc))


def test_hot_session_migration_rewrites_legacy_pap_campaigns_to_g10():
    sc = datasets.end_to_end()
    sc.config["BUILTIN_BASELINE_REVISION"] = \
        "2026-09-19-prewashing-feed-blend-v37"
    for destination_id in washing.PAP_DESTINATION_IDS:
        sc.destination(destination_id).requested_grade = "TBT"
    for entry in sc.demand:
        if entry.dest_id in washing.PAP_DESTINATION_IDS:
            entry.requested_grade = "TBT"

    assert datasets.migrate_loaded_builtin(sc) == "solver"
    assert all(sc.destination(destination_id).requested_grade == "G10"
               for destination_id in washing.PAP_DESTINATION_IDS)
    assert {entry.requested_grade for entry in sc.demand
            if entry.dest_id in washing.PAP_DESTINATION_IDS} == {"G10"}


def test_washing_monitor_reports_source_output_and_certified_pap_receipts():
    sc = fixture()
    plan = solve(sc)
    treatment = treatment_quality_rows(sc, plan)
    assert not treatment.empty
    assert set(treatment["treatment"]) == {"Simple washing"}
    assert treatment["source_BPL"].iloc[0] == pytest.approx(RAW_LOW["BPL"])
    assert treatment["output_BPL"].iloc[0] == pytest.approx(65.2736)
    assert treatment["feed_t"].sum() == pytest.approx(1000)
    assert treatment["output_moisture"].iloc[0] == pytest.approx(18.)
    assert treatment["product_t"].sum() == pytest.approx(881)
    runs = treatment_run_rows(sc, treatment)
    assert len(runs) == 1
    assert runs["Treatment"].iloc[0] == "Simple washing"
    assert runs["Feed [t]"].iloc[0] == pytest.approx(1000)
    assert runs["Product [t]"].iloc[0] == pytest.approx(881)
    assert runs["Recovered TSM [t]"].iloc[0] == pytest.approx(722.42)
    assert runs["Unit cost [$/TSM]"].iloc[0] == pytest.approx(.63)
    assert runs["Treatment cost [$]"].iloc[0] == pytest.approx(455.1246)

    receipts = pap_g10_receipt_rows(sc, plan)
    assert not receipts.empty
    assert set(receipts["received_grade"]) == {"G10"}
    assert set(receipts["certification"]) == {"WITHIN G10"}

    sim = Simulator(sc, plan, auto_replan=False)
    sim.run_to_end()
    actual = treatment_quality_rows(sc, plan, sim, actual=True)
    assert not actual.empty
    assert set(actual["basis"]) == {"Executed actual"}
    actual_receipts = pap_g10_receipt_rows(sc, plan, sim)
    assert set(actual_receipts["received_grade"]) == {"G10"}


def test_roundtrip_and_disable_preserve_g10_receiving_contract():
    sc = fixture()
    restored = scenario_from_json(scenario_to_json(sc))
    assert restored == sc
    cfg = washing.settings(restored)
    cfg["enabled"] = False
    disabled = washing.stage_configuration(restored, cfg)
    assert disabled.destinations[0].requested_grade == "G10"
    assert disabled.destinations[0].spec
    assert all(spec.hard for spec in disabled.destinations[0].spec)
    assert not disabled.config.get("_WASHING_MODEL_CATALOG")
    disabled_plan = solve(disabled)
    assert all(job.certified_grade == "" for job in disabled_plan.jobs)
    assert all(job.output_grade != "G10" for job in disabled_plan.jobs)


def test_missing_assay_does_not_become_zero():
    raw = dict(RAW_GLOBAL)
    del raw["CO2"]
    sc = fixture(raw)
    plan = solve(sc)
    assert sum(plan.delivered["PAP-1"]) == 0
    assert not washing.treated_quality(raw, washing.DEFAULT_MODES["full"]).get("CO2")


def test_both_builtin_mines_offer_the_two_washing_modes():
    from mph_sim.datasets import end_to_end
    from mph_sim.routes import RouteGraph
    from mph_sim.optimizer import build_candidates
    sc = end_to_end()
    cfg = washing.defaults()
    cfg["enabled"] = True
    sc = washing.stage_configuration(sc, cfg)
    candidates, _ = build_candidates(sc, RouteGraph.from_scenario(sc))
    for stage in ("BEN-WASH-L1", "BEN-WASH-L2", "BEN-WASH-L3", "LOU-WASH-L1", "LOU-WASH-L2"):
        assert {c.treatment_id for c in candidates if c.process_stage == stage} == {"simple", "full"}


def test_fixed_washing_feed_grade_and_quantity_are_honored():
    from mph_sim.domain import FixedJob
    sc = fixture(demand=420)
    sc.fixed_jobs = [FixedJob("LOCK", "W", "reclaim", "A", rate_tph=600,
                              start_bucket=0, end_bucket=0, grade="RAW")]
    plan = solve(sc)
    assert plan.status in ("OPTIMAL", "FEASIBLE"), plan.reasons
    job = next(job for job in plan.jobs if job.id == "LOCK")
    assert job.locked and job.tonnage_t == 600
    assert all(row["grade"] == "RAW" for row in job.material_flows)


def test_optional_minimum_complete_job_still_applies_to_washing():
    sc = fixture(demand=70)
    sc.config["MIN_JOB_VOLUME_T"] = 250
    plan = solve(sc)
    assert plan.status in ("OPTIMAL", "FEASIBLE"), plan.reasons
    assert all(job.tonnage_t >= 250 for job in plan.jobs)


@pytest.mark.parametrize("bad", [[], "", 0, None])
def test_malformed_falsey_configuration_is_not_silently_defaulted(bad):
    sc = fixture()
    sc.config[washing.CONFIG_KEY] = bad
    assert solve(sc).status == "ERROR"


def test_builtin_derived_feed_ceilings_are_yield_aware_and_reversible():
    from mph_sim.datasets import end_to_end
    original = end_to_end()
    cfg = washing.defaults()
    cfg["enabled"] = True
    staged = washing.stage_configuration(original, cfg)
    assert staged.spaces == original.spaces
    assert staged.fixed_jobs == original.fixed_jobs
    assert staged.config["TERMINAL_INVENTORY_EXACT_T"] == original.config["TERMINAL_INVENTORY_EXACT_T"]
    assert staged.config["CORRIDOR_DISPATCH_TARGET_T"] == original.config["CORRIDOR_DISPATCH_TARGET_T"]
    # Every source pile now opts into optimized blending. L2's feed ceiling
    # covers the lowest recovery across BOTH eligible sources and treatments.
    assert sum(entry.rate_tph for entry in staged.demand if entry.dest_id == "BEN-WASH-L2") == 8086
    assert not any(stage.startswith("BEN-RAW-STACK") for stage in staged.config["PROCESS_STAGE_OUTPUT_TARGET_T"])
    cfg["enabled"] = False
    disabled = washing.stage_configuration(staged, cfg)
    assert disabled.demand == original.demand
    assert disabled.config["PROCESS_STAGE_OUTPUT_TARGET_T"] == original.config["PROCESS_STAGE_OUTPUT_TARGET_T"]


def test_custom_raw_feed_quotas_are_not_rewritten():
    from mph_sim.datasets import end_to_end
    sc = end_to_end()
    next(entry for entry in sc.demand if entry.dest_id == "BEN-WASH-L2").rate_tph += 1
    rows = [deepcopy(entry) for entry in sc.demand if entry.dest_id == "BEN-WASH-L2"]
    cfg = washing.defaults()
    cfg["enabled"] = True
    staged = washing.stage_configuration(sc, cfg)
    assert [entry for entry in staged.demand if entry.dest_id == "BEN-WASH-L2"] == rows
    assert "Custom" in staged.config["_WASHING_DERIVED_POLICY_NOTE"]


def test_ready_material_and_pending_delay_survive_replanning():
    from mph_sim.domain import FixedJob
    sc = fixture(demand=420)
    sc.sim.horizon_buckets = 5
    sc.config[washing.CONFIG_KEY]["modes"]["simple"]["residence_min"] = 90
    sc.demand = [DemandEntry("PAP-1", t, 420 if t == 4 else 0, requested_grade="G10") for t in range(5)]
    sc.fixed_jobs = [FixedJob("W0", "W", "reclaim", "A", rate_tph=600, start_bucket=0, end_bucket=0, grade="RAW")]
    plan = solve(sc)
    assert plan.status in ("OPTIMAL", "FEASIBLE"), plan.reasons
    sim = Simulator(sc, plan, auto_replan=False)
    sim.advance_minutes(60)
    snap = sim.snapshot_scenario()
    snap.fixed_jobs = []
    snap.sim.horizon_buckets = 4
    snap.sim.horizon_minutes = 240
    replanned = solve_plan(snap, SolveOptions(horizon_offset=1, max_time_s=2, max_wall_time_s=10))
    assert replanned.status in ("OPTIMAL", "FEASIBLE"), replanned.reasons
    assert sum(replanned.delivered["PAP-1"]) == 420
    assert replanned.breakdown["minute_timing_errors"] == 0
    ready = max(batch["ready_minute"] for pools in snap.config["_WASHING_MODEL_PENDING"].values()
                for batches in pools.values() for batch in batches)
    for job in replanned.jobs:
        if job.dest_id == "PAP-1":
            assert min(window.start_minute for window in job.execution_windows) >= ready


def test_three_day_rolling_carries_material_catalog_and_inventory():
    sc = fixture(demand=0, mass=5000)
    sc.sim.horizon_buckets = 72
    sc.sim.horizon_minutes = 4320
    sc.config["WEEKLY_ROLLING_SOLVE_ENABLED"] = True
    sc.demand = [DemandEntry("PAP-1", t, 420 if t in (23, 47, 71) else 0, requested_grade="G10") for t in range(72)]
    plan = solve_plan(sc, SolveOptions(max_time_s=6, max_wall_time_s=15))
    assert plan.status in ("OPTIMAL", "FEASIBLE"), plan.reasons
    assert sum(plan.delivered["PAP-1"]) == 1260
    assert len(plan.material_inventory["A"][next(iter(plan.material_inventory["A"]))]) == 73
    assert_material_balances(plan)
    sim = Simulator(sc, plan, auto_replan=False)
    sim.run_to_end()
    assert sum(sim.delivered["PAP-1"]) == pytest.approx(1260)


def test_treated_stream_identity_survives_dispatch_transit_and_stacking():
    sc = fixture(demand=0)
    sc.sites.append(Site("H", "Receiving hub", "hub"))
    sc.spaces.append(StockSpace("HP", "H", "yard", "open", 5000, 0, "LEGACY", node_id="HP", material_class="washed"))
    sc.nodes += [Node("EXIT", "H", "corridor_exit"), Node("HP", "H", "space"), Node("OUT", "S", "tower")]
    sc.nodes[2].site = "H"
    sc.destinations[0].site = "H"
    sc.links = [Link("A-P", "A", "P", 1000), Link("FEED", "P", "OUT", 1000), Link("C", "OUT", "EXIT", 1000, transit_buckets=1),
                Link("STACK", "EXIT", "HP", 1000), Link("HP-D", "HP", "D", 1000)]
    sc.machines += [Machine("ST", "H", "stacker", 0, 1000, ["HP"], setup_min=0),
                    Machine("HR", "H", "reclaimer", 0, 1000, ["HP"], setup_min=0)]
    sc.sim.horizon_buckets = 6
    sc.sim.horizon_minutes = 360
    sc.demand = [DemandEntry("PAP-1", t, 700 if t == 5 else 0, requested_grade="G10") for t in range(6)]
    plan = solve(sc)
    assert plan.status in ("OPTIMAL", "FEASIBLE"), plan.reasons
    assert sum(plan.delivered["PAP-1"]) == 700
    assert any(job.kind == "dispatch" for job in plan.jobs)
    assert any(job.kind == "stack" and job.space == "HP" for job in plan.jobs)
    sim = Simulator(sc, plan, auto_replan=False)
    sim.run_to_end()
    assert sum(sim.delivered["PAP-1"]) == pytest.approx(700)
    assert sim.delivered_q["PAP-1"][5]["Cd"] == pytest.approx(12.35)
    assert sim.unstacked_t == pytest.approx(0)
    for sid, pools in sim.material_mass.items():
        assert sum(pools.values()) == pytest.approx(sim.inv[sid], abs=1e-5)


def test_cost_selects_cheaper_option_at_equal_recovery_and_service():
    both_modes_qualify = dict(
        BPL=58., Cd=16., MgO=1.1, SiO2=10., CO2=8.5, moisture=15.)
    sc = fixture(both_modes_qualify, demand=360)
    modes = sc.config[washing.CONFIG_KEY]["modes"]
    modes["simple"]["dry_solids_yield"] = .6
    modes["simple"]["cost_per_tsm_t"] = 10.
    modes["full"]["cost_per_tsm_t"] = 1.
    plan = solve(sc)
    assert sum(plan.delivered["PAP-1"]) == 360
    assert mode_feed(plan, "full") == 535
    assert mode_feed(plan, "simple") == 0
    assert plan.breakdown["washing_treatment_cost"] == pytest.approx(295.32)


def test_unpriced_mode_is_not_optimized_as_a_free_route():
    sc = fixture()
    sc.config[washing.CONFIG_KEY]["modes"]["simple"]["cost_per_tsm_t"] = 100.
    sc.config[washing.CONFIG_KEY]["modes"]["full"]["cost_per_tsm_t"] = None
    assert solve(sc).status == "ERROR"


def test_actual_moisture_change_recalculates_wet_output_and_preserves_dry_yield():
    from mph_sim.domain import SimEvent
    sc = fixture()
    sc.config[washing.CONFIG_KEY]["modes"]["simple"]["changes_pct"]["moisture"] = -100.
    sc.config[washing.CONFIG_KEY]["modes"]["full"]["available"] = False
    plan = solve(sc)
    assert mode_feed(plan, "simple") == 1000
    sim = Simulator(sc, plan, [SimEvent("MOIST", "offspec_assay", 0, "A", params={"moisture": 20.})], auto_replan=False)
    sim.run_to_end()
    actual = compute_kpis(sc, plan, sim)
    assert actual["washing_simple_feed_dry_t"] == pytest.approx(800)
    assert actual["washing_simple_product_dry_t"] == pytest.approx(679.5294117647061)
    assert actual["washing_simple_actual_dry_yield_pct"] == pytest.approx(84.94117647058825)
    assert sum(sim.delivered["PAP-1"]) == pytest.approx(679.5294117647061)


def test_receiving_specification_cannot_be_used_as_stock_assays():
    sc = fixture()
    sc.spaces[0].grade = "G10"
    plan = solve(sc)
    assert plan.status == "ERROR"
    assert any("receiving specification" in reason for reason in plan.reasons)


def test_material_components_share_one_physical_stacking_command():
    from dataclasses import replace
    from mph_sim.optimizer import Cand
    from mph_sim.routes import Path
    a = Cand("stack", "ST", "P", grade="A", path=Path(["STACK"], 0, 1000), material_id="A")
    b = replace(a, grade="B", material_id="B")
    assert a.gkey() == b.gkey()


def test_legacy_transit_uses_grade_even_when_parcel_has_a_material_id():
    from mph_sim.domain import BeltProfileEntry
    sc = Scenario("legacy-parcel", sites=[Site("H", "Hub", "hub")],
        grades=[Grade("G", "Existing product", quality={"BPL": 70., "moisture": 15.}, material_class="washed")],
        spaces=[StockSpace("HP", "H", "yard", "open", 5000, 0, "G", node_id="HP", material_class="washed")],
        machines=[Machine("ST", "H", "stacker", 0, 1000, ["HP"], setup_min=0),
                  Machine("HR", "H", "reclaimer", 0, 1000, ["HP"], setup_min=0)],
        nodes=[Node("ENTRY", "H", "tower"), Node("EXIT", "H", "corridor_exit"),
               Node("HP", "H", "space"), Node("D", "H", "dest")],
        links=[Link("C", "ENTRY", "EXIT", 1000, transit_buckets=1),
               Link("STACK", "EXIT", "HP", 1000), Link("HP-D", "HP", "D", 1000)],
        destinations=[Destination("UNIT", "H", "D")],
        demand=[DemandEntry("UNIT", t, 700 if t == 2 else 0) for t in range(3)],
        belt_profile=[BeltProfileEntry("C", 0, 700, "G", material_id="saved-material-identity")],
        sim=SimParams(horizon_buckets=3, max_solve_s=2), config={"OBJECTIVE_MODE": "service_first"})
    plan = solve(sc)
    assert plan.status in ("OPTIMAL", "FEASIBLE"), plan.reasons
    assert sum(plan.delivered["UNIT"]) == 700
    assert plan.breakdown["surge_t"] == 0


def test_internal_stacking_transfer_replays_from_stock_not_corridor_arrivals():
    sc = fixture(demand=420)
    sc.process_transfers[0].operation_kind = "stack"
    sc.machines[0].kind = "stacker"
    sc.machines[0].reachable_spaces = ["P"]
    plan = solve(sc)
    assert plan.status in ("OPTIMAL", "FEASIBLE"), plan.reasons
    assert any(job.transfer_id and job.kind == "stack" for job in plan.jobs)
    sim = Simulator(sc, plan, auto_replan=False)
    sim.run_to_end()
    assert sum(sim.delivered["PAP-1"]) == pytest.approx(420)
    assert sum(sim.material_mass["A"].values()) == pytest.approx(sim.inv["A"])


def test_replay_preserves_fractional_measured_stock_after_integer_planning():
    sc = fixture(mass=1000.25, demand=700)
    plan = solve(sc)
    sim = Simulator(sc, plan, auto_replan=False)
    sim.run_to_end()
    assert sum(sim.delivered["PAP-1"]) == pytest.approx(700)
    assert sim.inv["A"] == pytest.approx(205.25)
    assert sim.inv["A"] % 1 == pytest.approx(.25)
    assert sum(sim.material_mass["A"].values()) == pytest.approx(205.25)
