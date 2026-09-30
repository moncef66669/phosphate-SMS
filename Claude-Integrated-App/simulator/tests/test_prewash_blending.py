"""Input/output validation for the pre-washing blend decision."""
from copy import deepcopy

import pytest

from mph_sim import datasets, prewash_blending, washing
from mph_sim.datasets import end_to_end
from mph_sim.domain import (DemandEntry, Destination, Grade, Link, Machine,
                            Node, ObjectiveWeights, ProcessTransfer, Scenario,
                            SimParams, Site, StockSpace)
from mph_sim.optimizer import (SolveOptions, _candidate_serves_destination,
                               build_candidates, solve_plan)
from mph_sim.routes import RouteGraph
from mph_sim.validation import validate_scenario


HIGH = dict(BPL=55.11, Cd=25., MgO=1.50, SiO2=16.28,
            CO2=8.41, moisture=15.)
LOW = dict(BPL=58.28, Cd=13., MgO=1.13, SiO2=10.22,
           CO2=8.75, moisture=15.)


def blend_fixture():
    sc = Scenario(
        "prewash-blend-test",
        sites=[Site("S", "Test", "mine")],
        grades=[
            Grade("HIGH", "High impurity raw", quality=HIGH,
                  material_class="raw_screened"),
            Grade("LOW", "Low impurity raw", quality=LOW,
                  material_class="raw_screened"),
            Grade("PRODUCT", "Product", quality={},
                  material_class="washed"),
        ],
        spaces=[
            StockSpace("A", "S", "raw", "open", 5000, 1000, "HIGH",
                       node_id="A", material_class="raw_screened"),
            StockSpace("B", "S", "raw", "open", 5000, 1000, "LOW",
                       node_id="B", material_class="raw_screened"),
            StockSpace("P", "S", "product", "open", 5000, 0, "PRODUCT",
                       node_id="P", material_class="washed"),
        ],
        machines=[
            Machine("WA", "S", "reclaimer", 0, 1000, ["A"], setup_min=0),
            Machine("WB", "S", "reclaimer", 0, 1000, ["B"], setup_min=0),
            Machine("R", "S", "reclaimer", 0, 1000, ["P"], setup_min=0),
        ],
        nodes=[Node("A", "S", "space"), Node("B", "S", "space"),
               Node("P", "S", "space"), Node("D", "S", "dest")],
        links=[Link("A-P", "A", "P", 1000),
               Link("B-P", "B", "P", 1000),
               Link("P-D", "P", "D", 1000)],
        destinations=[Destination("PAP-1", "S", "D")],
        demand=[DemandEntry("PAP-1", bucket, 500 if bucket == 2 else 0)
                for bucket in range(3)],
        process_transfers=[
            ProcessTransfer(
                "WASH-A", "WA", "A", "P", ["A-P"],
                operation_kind="reclaim", process_stage="WASH",
                treatment_family="washing"),
            ProcessTransfer(
                "WASH-B", "WB", "B", "P", ["B-P"],
                operation_kind="reclaim", process_stage="WASH",
                treatment_family="washing"),
        ],
        sim=SimParams(horizon_buckets=3, max_solve_s=3),
        weights=ObjectiveWeights(w4_giveaway=0, w6_safety=0),
        config={"OBJECTIVE_MODE": "service_first"},
    )
    wash_cfg = washing.defaults()
    wash_cfg["enabled"] = True
    sc = washing.stage_configuration(sc, wash_cfg)
    sc.config[prewash_blending.CONFIG_KEY] = {
        "model_version": 1,
        "enabled": True,
        "scope": "DAILY",
        "evidence": "test",
        "points": {
            "TEST": {
                "name": "test point", "site": "S",
                "process_stages": ["WASH"],
                "enabled_process_stages": ["WASH"],
                "allowed_treatment_modes": ["full"],
                "components": [
                    {"source_space": "A", "grade_id": "HIGH",
                     "min_fraction": .40, "max_fraction": .60},
                    {"source_space": "B", "grade_id": "LOW",
                     "min_fraction": .40, "max_fraction": .60},
                ],
            }
        },
    }
    return sc


def test_builtin_prewash_inputs_have_complete_physical_route_matrix():
    sc = end_to_end()
    assert prewash_blending.enabled(sc)
    assert prewash_blending.configuration_errors(sc) == []
    assert not [issue for issue in validate_scenario(sc)
                if issue.code == "BAD_PREWASH_BLEND"]
    candidates, _ = build_candidates(sc, RouteGraph.from_scenario(sc))
    for point in prewash_blending.enabled_points(sc).values():
        for stage in prewash_blending.effective_process_stages(point):
            expected = {row["source_space"] for row in
                        prewash_blending.candidate_components(point)}
            actual = {candidate.source_space for candidate in candidates
                      if candidate.process_stage == stage}
            assert expected <= actual


@pytest.mark.parametrize("source_based", [False, True])
def test_daily_blend_respects_input_shares_and_one_washing_mode(source_based):
    sc = blend_fixture()
    if source_based:
        sc.config[prewash_blending.CONFIG_KEY]["points"]["TEST"][
            "enabled_source_spaces"] = ["A", "B"]
    plan = solve_plan(
        sc, SolveOptions(max_time_s=3, max_wall_time_s=10))
    assert plan.status in ("OPTIMAL", "FEASIBLE"), plan.reasons
    flows = [flow for job in plan.jobs if job.process_stage == "WASH"
             for flow in job.material_flows]
    by_source = {
        source: sum(flow["tonnage_t"] for job in plan.jobs
                    if job.process_stage == "WASH"
                    and job.source_space == source
                    for flow in job.material_flows)
        for source in ("A", "B")
    }
    total = sum(by_source.values())
    assert total > 0
    assert .40 <= by_source["A"] / total <= .60
    assert .40 <= by_source["B"] / total <= .60
    assert len({flow["treatment_id"] for flow in flows}) == 1
    assert sum(plan.delivered["PAP-1"]) == pytest.approx(500)


def test_fixed_matrix_enforces_exact_daily_source_percentages():
    sc = blend_fixture()
    point = sc.config[prewash_blending.CONFIG_KEY]["points"]["TEST"]
    point["share_mode"] = prewash_blending.BLEND_MODE_MATRIX
    point["components"][0]["target_fraction"] = .50
    point["components"][1]["target_fraction"] = .50

    plan = solve_plan(
        sc, SolveOptions(max_time_s=3, max_wall_time_s=10))
    assert plan.status in ("OPTIMAL", "FEASIBLE"), plan.reasons
    by_source = {
        source: sum(flow["tonnage_t"] for job in plan.jobs
                    if job.process_stage == "WASH"
                    and job.source_space == source
                    for flow in job.material_flows)
        for source in ("A", "B")
    }
    assert sum(by_source.values()) > 0
    assert by_source["A"] == by_source["B"]


def test_multiple_site_matrices_are_alternatives_not_line_assignments():
    sc = blend_fixture()
    point = sc.config[prewash_blending.CONFIG_KEY]["points"]["TEST"]
    point["share_mode"] = prewash_blending.BLEND_MODE_MATRIX
    point["matrix_catalog"] = [
        {"id": "M50", "components": [
            {"source_space": "A", "grade_id": "HIGH",
             "target_fraction": .50},
            {"source_space": "B", "grade_id": "LOW",
             "target_fraction": .50},
        ]},
        {"id": "M40", "components": [
            {"source_space": "A", "grade_id": "HIGH",
             "target_fraction": .40},
            {"source_space": "B", "grade_id": "LOW",
             "target_fraction": .60},
        ]},
    ]

    assert prewash_blending.configuration_errors(sc) == []
    plan = solve_plan(sc, SolveOptions(max_time_s=3, max_wall_time_s=10))
    assert plan.status in ("OPTIMAL", "FEASIBLE"), plan.reasons
    by_source = {
        source: sum(flow["tonnage_t"] for job in plan.jobs
                    if job.process_stage == "WASH"
                    and job.source_space == source
                    for flow in job.material_flows)
        for source in ("A", "B")
    }
    total = sum(by_source.values())
    share_a = by_source["A"] / total
    assert share_a == pytest.approx(.50) or share_a == pytest.approx(.40)


def test_invalid_fixed_matrix_is_rejected_before_solve():
    sc = blend_fixture()
    point = sc.config[prewash_blending.CONFIG_KEY]["points"]["TEST"]
    point["share_mode"] = prewash_blending.BLEND_MODE_MATRIX
    point["components"][0]["target_fraction"] = .70
    point["components"][1]["target_fraction"] = .40

    messages = prewash_blending.configuration_errors(sc)
    assert any("fixed matrix shares must sum to 100%" in message
               for message in messages)
    plan = solve_plan(sc, SolveOptions(max_time_s=1, max_wall_time_s=5))
    assert plan.status == "ERROR"


def test_disabled_policy_preserves_single_source_baseline():
    sc = blend_fixture()
    sc.config[prewash_blending.CONFIG_KEY]["enabled"] = False
    # Make the low-quality source unavailable to prove the blend constraints
    # disappear rather than silently retaining their minimum share.
    sc.space("B").mass_t = 0
    plan = solve_plan(
        sc, SolveOptions(max_time_s=3, max_wall_time_s=10))
    assert plan.status in ("OPTIMAL", "FEASIBLE"), plan.reasons
    assert not [job for job in plan.jobs
                if job.process_stage == "WASH" and job.source_space == "B"]


def test_invalid_share_envelope_is_rejected_before_solve():
    sc = blend_fixture()
    bad = deepcopy(sc.config[prewash_blending.CONFIG_KEY])
    bad["points"]["TEST"]["components"][0]["min_fraction"] = .8
    bad["points"]["TEST"]["components"][1]["min_fraction"] = .8
    bad["points"]["TEST"]["components"][0]["max_fraction"] = .9
    bad["points"]["TEST"]["components"][1]["max_fraction"] = .9
    sc.config[prewash_blending.CONFIG_KEY] = bad
    messages = prewash_blending.configuration_errors(sc)
    assert any("minimum shares sum above 100%" in message
               for message in messages)
    plan = solve_plan(sc, SolveOptions(max_time_s=1, max_wall_time_s=5))
    assert plan.status == "ERROR"
    assert any(reason.startswith("BAD_PREWASH_BLEND")
               for reason in plan.reasons)


@pytest.mark.parametrize("revision", [
    datasets.BUILTIN_BASELINE_REVISION,
    "2026-09-15-s0-soft-parcel-batching-v33",
])
def test_hot_session_migration_installs_missing_prewash_inputs(revision):
    sc = end_to_end()
    sc.config["BUILTIN_BASELINE_REVISION"] = revision
    sc.config.pop(prewash_blending.CONFIG_KEY)
    sc.process_transfers = [
        transfer for transfer in sc.process_transfers
        if not transfer.id.endswith("BLEND-ALT")]

    assert datasets.migrate_loaded_builtin(sc) == "solver"
    assert prewash_blending.configuration_errors(sc) == []
    assert sc.config[prewash_blending.CONFIG_KEY]["enabled"] is True
    assert sc.config[prewash_blending.CONFIG_KEY]["model_version"] == 4
    assert sc.config[prewash_blending.CONFIG_KEY]["points"][
        "LOU-PREWASH"]["share_mode"] == "OPTIMIZED"
    assert sc.config[prewash_blending.CONFIG_KEY]["points"][
        "LOU-PREWASH"]["enabled_process_stages"] == ["LOU-WASH-L1", "LOU-WASH-L2"]
    assert sc.config[prewash_blending.CONFIG_KEY]["points"][
        "LOU-PREWASH"]["enabled_source_spaces"] == ["110LG-OL1", "110LG-OL2"]
    assert {
        transfer.id for transfer in sc.process_transfers
        if transfer.id.endswith("BLEND-ALT")
    } == {
        "BG-L1-WASH-BLEND-ALT",
        "BG-L2-WASH-BLEND-ALT",
        "BG-L3-WASH-BLEND-ALT",
        "LOU-WASH-L1-LOW-CD-BLEND-ALT",
        "LOU-WASH-L2-GLOBAL-BLEND-ALT",
    }
    assert datasets.migrate_loaded_builtin(sc) == ""


def _source_policy_fixture(enabled_sources):
    sc = blend_fixture()
    cfg = sc.config[prewash_blending.CONFIG_KEY]
    cfg["model_version"] = 4
    cfg["points"]["TEST"]["enabled_source_spaces"] = enabled_sources
    return sc


def _feed_demand(sc, stage="WASH", grade="HIGH"):
    # Isolate source-routing semantics from the typed washing yield/minute
    # reconciliation already covered by the treatment tests above.
    sc.config[washing.CONFIG_KEY]["enabled"] = False
    sc.config["OBJECTIVE_MODE"] = "weighted"
    sc.destinations.append(Destination(
        "FEED", "S", "P", requested_grade=grade,
        demand_kind="process_feed", process_stage=stage))
    sc.demand = [DemandEntry("FEED", 0, 500)]


@pytest.mark.parametrize("enabled_sources", [[], ["A"], ["B"]])
def test_inactive_stockpile_blend_preserves_ordinary_feed_and_stored_policy(
        enabled_sources):
    sc = _source_policy_fixture(enabled_sources)
    _feed_demand(sc)
    # Source opt-ins control blend participation, not ordinary reclaim.
    sc.process_transfers[1].id += "-BLEND-ALT"
    cfg_before = deepcopy(sc.config[prewash_blending.CONFIG_KEY])
    assert prewash_blending.enabled_points(sc) == {}
    assert prewash_blending.source_spaces_for_stage(sc, "WASH") == set()
    candidates, _ = build_candidates(sc, RouteGraph.from_scenario(sc))
    assert any(candidate.source_space == "A" for candidate in candidates)
    assert not any(candidate.source_space == "B" for candidate in candidates
                   if candidate.kind == "process")
    plan = solve_plan(sc, SolveOptions(max_time_s=3, max_wall_time_s=10))
    assert plan.status in ("OPTIMAL", "FEASIBLE"), plan.reasons
    assert sum(plan.delivered["FEED"]) == pytest.approx(500)
    assert sc.config[prewash_blending.CONFIG_KEY] == cfg_before


def test_source_opt_in_applies_to_all_lines_not_legacy_line_selection():
    sc = _source_policy_fixture(["A", "B"])
    point = sc.config[prewash_blending.CONFIG_KEY]["points"]["TEST"]
    point["process_stages"].append("WASH2")
    for original in list(sc.process_transfers):
        transfer = deepcopy(original)
        transfer.id += "-LINE2"
        transfer.process_stage = "WASH2"
        sc.process_transfers.append(transfer)
    _feed_demand(sc, stage="WASH2")
    assert point["enabled_process_stages"] == ["WASH"]
    assert prewash_blending.source_spaces_for_stage(sc, "WASH2") == {"A", "B"}
    assert prewash_blending.configuration_errors(sc) == []
    plan = solve_plan(sc, SolveOptions(max_time_s=3, max_wall_time_s=10))
    assert plan.status in ("OPTIMAL", "FEASIBLE"), plan.reasons
    assert sum(plan.delivered["FEED"]) == pytest.approx(500)
    by_source = {source: sum(job.tonnage_t
                            for job in plan.jobs
                            if job.process_stage == "WASH2"
                            and job.source_space == source)
                 for source in ("A", "B")}
    assert sum(by_source.values()) == pytest.approx(500)
    assert .4 <= by_source["A"] / sum(by_source.values()) <= .6


def test_legacy_import_preserves_line_selection_and_source_fallback():
    sc = blend_fixture()
    point = sc.config[prewash_blending.CONFIG_KEY]["points"]["TEST"]
    point["process_stages"].append("WASH2")
    assert prewash_blending.enabled_source_spaces(point) == ["A", "B"]
    assert prewash_blending.source_spaces_for_stage(sc, "WASH") == {"A", "B"}
    assert prewash_blending.source_spaces_for_stage(sc, "WASH2") == set()
    point["enabled_process_stages"] = []
    assert prewash_blending.enabled_source_spaces(point) == []
    assert prewash_blending.enabled_points(sc) == {}


def test_disabled_source_excludes_whole_matrix_without_renormalizing():
    sc = _source_policy_fixture(["A", "B"])
    point = sc.config[prewash_blending.CONFIG_KEY]["points"]["TEST"]
    point["share_mode"] = "MATRIX"
    point["matrix_catalog"] = [
        {"id": "AB", "components": [
            {"source_space": "A", "grade_id": "HIGH", "target_fraction": .4},
            {"source_space": "B", "grade_id": "LOW", "target_fraction": .6}]},
        {"id": "AC", "components": [
            {"source_space": "A", "grade_id": "HIGH", "target_fraction": .5},
            {"source_space": "C", "grade_id": "LOW", "target_fraction": .5}]},
    ]
    stored = deepcopy(point)
    eligible = prewash_blending.eligible_matrix_catalog(point)
    assert [entry["id"] for entry in eligible] == ["AB"]
    assert eligible[0]["components"] == point["matrix_catalog"][0]["components"]
    assert {row["source_space"] for row in
            prewash_blending.candidate_components(point)} == {"A", "B"}
    assert point == stored
    point["enabled_source_spaces"] = ["A"]
    assert prewash_blending.eligible_matrix_catalog(point) == []
    assert prewash_blending.enabled_points(sc) == {}
    assert point["matrix_catalog"] == stored["matrix_catalog"]


def test_disabled_zero_share_source_does_not_invalidate_matrix():
    point = {
        "process_stages": ["WASH"], "enabled_source_spaces": ["A", "B"],
        "share_mode": "MATRIX", "matrix_catalog": [{
            "id": "AB", "components": [
                {"source_space": "A", "target_fraction": .4},
                {"source_space": "B", "target_fraction": .6},
                {"source_space": "C", "target_fraction": 0.0}]}]}
    assert len(prewash_blending.eligible_matrix_catalog(point)) == 1
    assert {row["source_space"] for row in
            prewash_blending.candidate_components(point)} == {"A", "B"}


@pytest.mark.parametrize("enabled_sources, expected", [
    (["A", "B"], {"A": 200, "B": 300}),
    (["A", "C"], {"A": 250, "C": 250}),
    ([], {"A": 500}),
])
def test_actual_solve_selects_only_matrix_with_enabled_source_stockpiles(
        enabled_sources, expected):
    sc = _source_policy_fixture(enabled_sources)
    _feed_demand(sc)
    sc.spaces.append(StockSpace(
        "C", "S", "raw", "open", 5000, 1000, "LOW", node_id="C",
        material_class="raw_screened"))
    sc.nodes.append(Node("C", "S", "space"))
    sc.machines.append(Machine(
        "WC", "S", "reclaimer", 0, 1000, ["C"], setup_min=0))
    sc.links.append(Link("C-P", "C", "P", 1000))
    sc.process_transfers.append(ProcessTransfer(
        "WASH-C-BLEND-ALT", "WC", "C", "P", ["C-P"],
        operation_kind="reclaim", process_stage="WASH",
        treatment_family="washing"))
    point = sc.config[prewash_blending.CONFIG_KEY]["points"]["TEST"]
    point["share_mode"] = "MATRIX"
    point["matrix_catalog"] = [
        {"id": "AB", "components": [
            {"source_space": "A", "grade_id": "HIGH", "target_fraction": .4},
            {"source_space": "B", "grade_id": "LOW", "target_fraction": .6}]},
        {"id": "AC", "components": [
            {"source_space": "A", "grade_id": "HIGH", "target_fraction": .5},
            {"source_space": "C", "grade_id": "LOW", "target_fraction": .5}]},
    ]
    stored = deepcopy(point)
    assert prewash_blending.configuration_errors(sc) == []
    plan = solve_plan(sc, SolveOptions(max_time_s=3, max_wall_time_s=10))
    assert plan.status in ("OPTIMAL", "FEASIBLE"), plan.reasons
    assert sum(plan.delivered["FEED"]) == pytest.approx(500)
    actual = {source: sum(job.tonnage_t for job in plan.jobs
                          if job.process_stage == "WASH"
                          and job.source_space == source)
              for source in ("A", "B", "C")}
    assert actual == {source: pytest.approx(expected.get(source, 0))
                      for source in actual}
    assert point == stored


def test_infeasible_filtered_share_bounds_are_inactive_not_rescaled():
    sc = _source_policy_fixture(["A", "B"])
    point = sc.config[prewash_blending.CONFIG_KEY]["points"]["TEST"]
    point["components"] = [
        {"source_space": source, "grade_id": "HIGH" if source == "A" else "LOW",
         "min_fraction": .2, "max_fraction": .4}
        for source in ("A", "B", "C")]
    original = deepcopy(point)
    assert len(prewash_blending.candidate_components(point)) == 2
    assert prewash_blending.enabled_points(sc) == {}
    assert point == original
    point["enabled_source_spaces"].append("C")
    assert "TEST" in prewash_blending.enabled_points(sc)


def test_nonparticipating_source_can_still_serve_normal_feed_grade():
    sc = _source_policy_fixture(["A", "B"])
    _feed_demand(sc, grade="LOW")
    candidates, _ = build_candidates(sc, RouteGraph.from_scenario(sc))
    # Ordinary source C is not a blend source but can meet its normal grade.
    candidate = deepcopy(next(candidate for candidate in candidates
                              if candidate.kind == "process"
                              and candidate.source_space == "B"))
    stockpile = deepcopy(sc.space("B"))
    stockpile.id = "C"
    sc.spaces.append(stockpile)
    candidate.source_space = "C"
    assert _candidate_serves_destination(sc, candidate, sc.destinations[-1])
    assert not _candidate_serves_destination(
        sc, candidate, sc.destinations[-1], requested_grade="HIGH")


@pytest.mark.parametrize("bad", ["A", ["A", "A"], ["A", 1], ["MISSING"]])
def test_invalid_source_opt_in_is_rejected(bad):
    sc = _source_policy_fixture(bad)
    assert any("source" in message for message in
               prewash_blending.configuration_errors(sc))
