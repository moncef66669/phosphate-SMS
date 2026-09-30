import copy

import pytest

from mph_sim.analysis_tools import (BUNDLE_SCHEMA_VERSION, PROJECT_ID,
                                    MphAnalysisTools, build_run_bundle)
from mph_sim.kpis import compute_kpis
from mph_sim.persistence import RunStore
from result_analysis import AnalysisToolError, ResultAnalysisTools


class MemoryRepository:
    def __init__(self, bundles):
        self.bundles = bundles

    def load_run_bundle(self, run_id):
        return copy.deepcopy(self.bundles[run_id])

    def list_run_bundles(self, limit=100):
        return [{"id": run_id} for run_id in sorted(self.bundles)][:limit]


def _generic_bundle(values):
    return {
        "run": {"id": 1, "scenario": "test"},
        "termination": {"status": "OPTIMAL"},
        "objective": {"mode": "weighted"},
        "measurements": [{
            "id": "state:controlled_variable:loop:CV-1",
            "namespace": "state", "name": "controlled_variable",
            "entity_type": "loop", "entity_id": "CV-1",
            "unit": "%", "basis": "one-minute samples",
            "source_path": "history.cv",
            "values": values,
            "index": {"kind": "time", "start": "2026-01-01T00:00:00",
                      "step_minutes": 1, "offset": 0},
        }, {
            "id": "kpi:service_pct", "namespace": "kpi",
            "name": "service_pct", "unit": "%",
            "basis": "whole horizon", "value": 95.0,
        }],
    }


def test_generic_variability_supports_apc_style_series():
    tools = ResultAnalysisTools(MemoryRepository({1: _generic_bundle(
        [10.0, 12.0, None, 16.0])}))

    result = tools.analyze_variability(
        1, measurement_id="state:controlled_variable:loop:CV-1",
        start_time="2026-01-01T00:01:00")

    stats = result["results"][0]["statistics"]
    assert stats["count"] == 2
    assert stats["missing_count"] == 1
    assert stats["min"] == 12.0
    assert stats["max"] == 16.0
    assert stats["delta"] == 4.0
    assert stats["max_index"] == "2026-01-01T00:03:00"


def test_generic_catalog_requires_exact_measurement_for_query():
    tools = ResultAnalysisTools(MemoryRepository({1: _generic_bundle([1, 2])}))
    catalog = tools.catalog_run_data(1, namespace="state")
    assert catalog["count"] == 1
    with pytest.raises(AnalysisToolError, match="no measurement matched"):
        tools.query_run_data(1, namespace="state", name="missing")


def test_compare_runs_handles_scalars_and_explicit_series_means():
    left = _generic_bundle([1.0, 3.0])
    right = _generic_bundle([3.0, 5.0])
    right["measurements"][1]["value"] = 97.0
    tools = ResultAnalysisTools(MemoryRepository({1: left, 2: right}))

    scalars = tools.compare_runs(1, 2)
    series = tools.compare_runs(
        1, 2, ["state:controlled_variable:loop:CV-1"])

    assert scalars["changes"][0]["delta"] == 2.0
    assert series["changes"][0]["comparison_statistic"] == "series mean"
    assert series["changes"][0]["delta"] == 2.0


def test_run_store_preserves_legacy_history_and_full_bundle(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    bundle = _generic_bundle([1.0, 2.0])
    run_id = store.record(
        "test", 42, "solver", "OPTIMAL", 1.0, 0,
        {"service_pct": 95.0}, artifact=bundle,
        schema_version="1.1", project="test-project")

    history = store.history()
    loaded = store.load_run_bundle(run_id)
    listed = store.list_run_bundles()

    assert len(history) == 1
    assert loaded["measurements"][0]["values"] == [1.0, 2.0]
    assert loaded["run"]["id"] == run_id
    assert loaded["run"]["started_at"]
    assert listed[0]["bundle_available"] is True
    assert listed[0]["compressed_bytes"] > 0
    with pytest.raises(FileExistsError, match="immutable evidence bundle"):
        store.save_run_bundle(run_id, bundle)


def test_legacy_run_has_clear_detailed_analysis_error(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    run_id = store.record(
        "legacy", 42, "solver", "OPTIMAL", 1.0, 0,
        {"service_pct": 95.0})
    with pytest.raises(KeyError, match="predates evidence-bundle persistence"):
        store.load_run_bundle(run_id)


def test_mph_bundle_exposes_inputs_decisions_outputs_and_candidates(
        s1, s1_plan, tmp_path):
    bundle = build_run_bundle(s1, s1_plan, compute_kpis(s1, s1_plan))
    store = RunStore(tmp_path / "runs.db")
    run_id = store.record(
        s1.name, s1.sim.seed, s1_plan.solver, s1_plan.status,
        s1_plan.solver_wall_s, 0, {"jobs": len(s1_plan.jobs)},
        artifact=bundle, schema_version=BUNDLE_SCHEMA_VERSION,
        project=PROJECT_ID)
    tools = MphAnalysisTools(tmp_path / "runs.db")

    namespaces = {
        row["namespace"] for row in
        tools.catalog_run_data(run_id)["measurements"]}
    decisions = tools.catalog_run_data(run_id, namespace="decision")
    alternatives = tools.get_candidate_decisions(run_id)

    assert {"input", "decision", "state", "output", "kpi"} <= namespaces
    assert decisions["count"] == 2 * len(s1_plan.jobs)
    assert alternatives["count"] >= 1
    assert bundle["snapshots"]["scenario"]["name"] == s1.name
    assert bundle["model_context"]["package_version"] == "1.14.0"
    assert bundle["model_context"]["package_current"] is True
    assert len(tools.tool_definitions()) == 16

    context = tools.get_model_context(run_id, section="Washing and G10")
    objective = tools.get_objective_term_definition(
        "washing-raw-feed", run_id)
    constraint = tools.get_constraint_definition(
        "minimum-complete-job", run_id)
    kpi = tools.get_kpi_definition("service-pct", run_id)
    mapping = tools.get_code_mapping("washing-model", run_id)

    assert context["context_source"] == "run_snapshot"
    assert "0.85" in context["text"]
    assert objective["definition"]["priority"] == 3
    assert constraint["definition"]["hardness"] == "hard"
    assert kpi["definition"]["unit"] == "%"
    assert mapping["mapping"]["id"] == "washing-model"


def test_current_model_context_resolves_stable_variable_ids(tmp_path):
    tools = MphAnalysisTools(tmp_path / "runs.db")

    result = tools.get_variable_definition("flow-input")
    context = tools.get_model_context(section="Objective")

    assert result["definition"]["symbol"] == "X[c,t]"
    assert result["context_source"] == "current_project"
    assert context["package_current"] is True
    assert "service_first" in context["text"]


def test_counterfactual_rejects_non_scenario_paths_before_solving(
        s1, s1_plan, tmp_path):
    bundle = build_run_bundle(s1, s1_plan, compute_kpis(s1, s1_plan))
    store = RunStore(tmp_path / "runs.db")
    run_id = store.record(
        s1.name, s1.sim.seed, s1_plan.solver, s1_plan.status,
        s1_plan.solver_wall_s, 0, {}, artifact=bundle)
    tools = MphAnalysisTools(tmp_path / "runs.db")

    with pytest.raises(AnalysisToolError, match="validated scenario inputs"):
        tools.run_counterfactual(
            run_id, [{"path": "/snapshots/plan/objective", "value": 0}])
