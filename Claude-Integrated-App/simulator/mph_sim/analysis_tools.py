"""Evidence-bundle adapter and agent-callable tools for the MPH simulator.

The normalized bundle is intentionally broader than a result explanation.  It
preserves scalar inputs, decision/state/output series and KPIs so an agent can
answer exact variability questions later.  ``result_analysis`` contains the
project-neutral query engine; only this module knows MPH domain objects.
"""
from __future__ import annotations

from dataclasses import asdict
import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from result_analysis import AnalysisToolError, ResultAnalysisTools

from .domain import PlanResult, Scenario
from .kpis import compute_kpis, planned_delivery_quality
from .model_context import (find_code_mapping, find_definition,
                            load_model_context, select_formulation_section)
from .optimizer import (SolveOptions, build_candidates, job_group_key,
                        solve_plan)
from .persistence import RunStore, scenario_from_json, scenario_to_json
from .routes import RouteGraph
from .validation import validate_scenario


BUNDLE_SCHEMA_VERSION = "1.2"
PROJECT_ID = "mph-stockyard"


def _finite_or_none(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _measurement(
        namespace: str, name: str, *, value: Any = None,
        values: Optional[Iterable[Any]] = None, entity_type: str = "",
        entity_id: str = "", unit: str = "", basis: str = "",
        source_path: str = "", index: Optional[Dict[str, Any]] = None,
        data_quality: str = "derived") -> Dict[str, Any]:
    result = {
        "id": ":".join(filter(None, (namespace, name, entity_type, entity_id))),
        "namespace": namespace, "name": name,
        "entity_type": entity_type, "entity_id": entity_id,
        "unit": unit, "basis": basis, "source_path": source_path,
        "data_quality": data_quality,
    }
    if values is not None:
        result["values"] = [_finite_or_none(item) for item in values]
        result["index"] = dict(index or {"kind": "ordinal", "offset": 0})
    else:
        result["value"] = _finite_or_none(value)
    return result


def _time_index(sc: Scenario, *, offset: int = 0,
                alignment: str = "bucket") -> Dict[str, Any]:
    return {
        "kind": "time", "start": sc.sim.start_iso,
        "step_minutes": sc.sim.native_bucket_minutes(),
        "offset": int(offset), "alignment": alignment,
    }


def _unit_for_kpi(name: str) -> str:
    lowered = name.lower()
    if lowered.endswith("_pct") or "utilisation_pct" in lowered:
        return "%"
    if lowered.endswith("_tph"):
        return "t/h"
    if lowered.endswith("_minutes") or lowered.endswith("_min"):
        return "min"
    if lowered.endswith("_t_h"):
        return "t.h"
    if lowered.endswith("_hours") or lowered.endswith("_h"):
        return "h"
    if lowered.endswith("_kwh"):
        return "kWh"
    if lowered.endswith("_t") or "tonnage" in lowered:
        return "t"
    if lowered.endswith("_m") or lowered.startswith("travel"):
        return "m"
    if lowered.endswith("_s"):
        return "s"
    return ""


def _flatten_config_scalars(value: Any, prefix: str = ""):
    if isinstance(value, Mapping):
        for key in sorted(value, key=str):
            path = f"{prefix}.{key}" if prefix else str(key)
            yield from _flatten_config_scalars(value[key], path)
    elif isinstance(value, (str, int, float, bool)) or value is None:
        yield prefix, value


def _candidate_id(candidate) -> str:
    payload = json.dumps({
        "group": list(candidate.gkey()),
        "path": list(candidate.path.links if candidate.path else []),
        "material_id": candidate.material_id,
        "output_material_id": candidate.output_material_id,
    }, sort_keys=True, separators=(",", ":"))
    return "candidate-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _candidate_alternatives(sc: Scenario, plan: PlanResult) -> List[Dict[str, Any]]:
    candidates, _ = build_candidates(sc, RouteGraph.from_scenario(sc))
    tonnes_by_group: Dict[tuple, float] = {}
    paths_by_group: Dict[tuple, set] = {}
    for job in plan.jobs:
        key = tuple(job_group_key(job))
        tonnes_by_group[key] = tonnes_by_group.get(key, 0.0) + float(job.tonnage_t)
        paths = paths_by_group.setdefault(key, set())
        paths.update(tuple(flow.path) for flow in job.bucket_flows if flow.path)
        if job.path:
            paths.add(tuple(job.path))
    rows = []
    for candidate in candidates:
        key = tuple(candidate.gkey())
        path = tuple(candidate.path.links if candidate.path else ())
        selected_group_t = tonnes_by_group.get(key, 0.0)
        rows.append({
            "id": _candidate_id(candidate),
            "decision_id": "group-" + hashlib.sha256(
                repr(key).encode("utf-8")).hexdigest()[:16],
            "alternative": candidate.treatment_id or candidate.display_kind(),
            "kind": candidate.display_kind(), "machine": candidate.machine,
            "space": candidate.space, "source_space": candidate.source_space,
            "target_space": candidate.target_space,
            "destination": candidate.dest_id, "grade": candidate.grade,
            "output_grade": candidate.output_grade,
            "treatment_id": candidate.treatment_id,
            "route": list(path), "eligible": True,
            "eligibility_reason": (
                "generated after equipment, reachability, route, lifecycle and "
                "configured process-stage filters"),
            "hard_feasible": None,
            "constraint_failures": [],
            "selected": bool(selected_group_t > 0 and (
                not paths_by_group.get(key) or path in paths_by_group[key])),
            "selected_group_t": selected_group_t,
            "yield_factor": float(candidate.yield_factor),
            "residence_min": int(candidate.residence_min),
            "candidate_group": list(key),
        })
    return rows


def _derived_constraints(sc: Scenario, plan: PlanResult) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for space in sc.spaces:
        series = [float(value) for value in plan.inventory.get(
            space.id, [space.mass_t])]
        maximum = max(series, default=float(space.mass_t))
        minimum = min(series, default=float(space.mass_t))
        rows.append({
            "name": "stock_capacity", "entity": space.id,
            "lhs": maximum, "sense": "<=", "rhs": float(space.capacity_t),
            "slack": float(space.capacity_t) - maximum,
            "binding": abs(float(space.capacity_t) - maximum) <= 1e-6,
            "unit": "t", "basis": "maximum planned boundary inventory",
        })
        if space.safety_stock_t > 0:
            rows.append({
                "name": "safety_stock", "entity": space.id,
                "lhs": minimum, "sense": ">=",
                "rhs": float(space.safety_stock_t),
                "slack": minimum - float(space.safety_stock_t),
                "binding": abs(minimum - float(space.safety_stock_t)) <= 1e-6,
                "unit": "t", "basis": "minimum planned boundary inventory",
            })
    dt = float(sc.sim.dt_h)
    offset = max(int(plan.horizon_offset), 0)
    for destination in sc.destinations:
        delivered = list(plan.delivered.get(destination.id, []))
        unmet = list(plan.unmet.get(destination.id, []))
        residuals = []
        for local_bucket in range(max(len(delivered), len(unmet))):
            absolute_bucket = offset + local_bucket
            demand_t = sc.demand_tph(destination.id, absolute_bucket) * dt
            delivered_t = (float(delivered[local_bucket]) * dt
                           if local_bucket < len(delivered) else 0.0)
            unmet_t = float(unmet[local_bucket]) \
                if local_bucket < len(unmet) else 0.0
            residuals.append(delivered_t + unmet_t - demand_t)
        rows.append({
            "name": "demand_balance", "entity": destination.id,
            "lhs": max((abs(value) for value in residuals), default=0.0),
            "sense": "==", "rhs": 0.0,
            "slack": -max((abs(value) for value in residuals), default=0.0),
            "binding": True, "unit": "t",
            "basis": "maximum absolute bucket residual",
        })
        for spec in destination.spec:
            margins = []
            for local_bucket in range(len(delivered)):
                if float(delivered[local_bucket]) <= 0:
                    continue
                quality = planned_delivery_quality(
                    sc, plan, destination, offset + local_bucket)
                value = quality.get(spec.param)
                if value is None or not math.isfinite(float(value)):
                    continue
                if spec.pmin is not None:
                    margins.append(float(value) - float(spec.pmin))
                if spec.pmax is not None:
                    margins.append(float(spec.pmax) - float(value))
            if margins:
                margin = min(margins)
                rows.append({
                    "name": f"quality_margin.{spec.param}",
                    "entity": destination.id, "lhs": margin,
                    "sense": ">=", "rhs": 0.0, "slack": margin,
                    "binding": abs(margin) <= 1e-6,
                    "unit": next((p.unit for p in sc.quality_params
                                  if p.name == spec.param), ""),
                    "basis": "minimum delivered-quality margin",
                    "hard": bool(spec.hard),
                })
    return rows


def build_run_bundle(
        sc: Scenario, plan: PlanResult,
        kpis: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build the immutable evidence contract stored with every new run."""
    kpis = dict(kpis or compute_kpis(sc, plan))
    start = sc.sim.bucket_dt(0)
    end = sc.sim.bucket_dt(sc.sim.horizon_buckets)
    scenario_payload = asdict(sc)
    plan_payload = asdict(plan)
    fingerprint = hashlib.sha256(
        scenario_to_json(sc).encode("utf-8")).hexdigest()[:20]
    measurements: List[Dict[str, Any]] = []

    for field, value in asdict(sc.weights).items():
        measurements.append(_measurement(
            "input", f"objective_weight.{field}", value=value,
            unit="configured weight", basis="scenario input",
            source_path=f"/weights/{field}"))
    for field, value in asdict(sc.sim).items():
        measurements.append(_measurement(
            "input", f"simulation.{field}", value=value,
            basis="scenario input", source_path=f"/sim/{field}"))
    for path, value in _flatten_config_scalars(sc.config):
        measurements.append(_measurement(
            "input", f"config.{path}", value=value,
            basis="scenario input",
            source_path="/config/" + path.replace(".", "/")))
    for grade in sc.grades:
        for parameter, value in grade.quality.items():
            unit = next((row.unit for row in sc.quality_params
                         if row.name == parameter), "")
            measurements.append(_measurement(
                "input", f"quality.{parameter}", value=value,
                entity_type="grade", entity_id=grade.id, unit=unit,
                basis="configured grade assay",
                source_path=f"/grades/{grade.id}/quality/{parameter}",
                data_quality=grade.evidence.status.lower()))
    for space in sc.spaces:
        for name, value, unit in (
                ("initial_inventory", space.mass_t, "t"),
                ("capacity", space.capacity_t, "t"),
                ("safety_stock", space.safety_stock_t, "t")):
            measurements.append(_measurement(
                "input", name, value=value, entity_type="space",
                entity_id=space.id, unit=unit, basis="scenario input",
                source_path=(f"/spaces/{space.id}/mass_t" if name == "initial_inventory"
                             else f"/spaces/{space.id}/{name}_t")))
    for machine in sc.machines:
        for name, value in (("rate_min", machine.rate_min_tph),
                            ("rate_max", machine.rate_max_tph)):
            measurements.append(_measurement(
                "input", name, value=value, entity_type="machine",
                entity_id=machine.id, unit="t/h", basis="scenario input",
                source_path=f"/machines/{machine.id}/{name}_tph",
                data_quality=machine.evidence.status.lower()))
    for link in sc.links:
        for name, value, unit in (
                ("capacity", link.capacity_tph, "t/h"),
                ("minimum_stable_rate", link.min_stable_tph, "t/h"),
                ("transit_buckets", link.transit_buckets, "bucket"),
                ("available", link.available, "boolean")):
            field = {"capacity": "capacity_tph",
                     "minimum_stable_rate": "min_stable_tph"}.get(name, name)
            measurements.append(_measurement(
                "input", name, value=value, entity_type="route",
                entity_id=link.id, unit=unit, basis="scenario input",
                source_path=f"/links/{link.id}/{field}",
                data_quality=link.evidence.status.lower()))
    for transfer in sc.process_transfers:
        for name, value, unit in (
                ("yield_factor", transfer.yield_factor, "output t/input t"),
                ("residence_min", transfer.residence_min, "min"),
                ("dependency_delay_min", transfer.dependency_delay_min, "min")):
            measurements.append(_measurement(
                "input", name, value=value, entity_type="process_transfer",
                entity_id=transfer.id, unit=unit, basis="scenario input",
                source_path=f"/process_transfers/{transfer.id}/{name}"))

    offset = max(int(plan.horizon_offset), 0)
    for destination in sc.destinations:
        horizon = max((len(plan.delivered.get(destination.id, [])),
                       len(plan.unmet.get(destination.id, []))), default=0)
        demand = [sc.demand_tph(destination.id, offset + bucket)
                  for bucket in range(horizon)]
        for namespace, name, values, unit, basis, source in (
                ("input", "demand_rate", demand, "t/h",
                 "requested native-bucket average rate", "scenario.demand"),
                ("output", "delivered_rate", plan.delivered.get(destination.id, []),
                 "t/h", "planned native-bucket average rate", "plan.delivered"),
                ("output", "unmet_mass", plan.unmet.get(destination.id, []),
                 "t", "planned mass per native bucket", "plan.unmet"),
                ("diagnostic", "offspec_slack", plan.offspec.get(destination.id, []),
                 "t.quality_unit", "planned soft-spec slack per bucket", "plan.offspec"),
                ("state", "feed_gap", plan.feed_gap.get(destination.id, []),
                 "fraction", "planned fraction of bucket in feed gap", "plan.feed_gap"),
                ("state", "feed_interruption_start",
                 plan.feed_interruption_start.get(destination.id, []), "count",
                 "planned interruption starts per bucket",
                 "plan.feed_interruption_start")):
            if values:
                measurements.append(_measurement(
                    namespace, name, values=values,
                    entity_type="destination", entity_id=destination.id,
                    unit=unit, basis=basis, source_path=source,
                    index=_time_index(sc, offset=offset)))
    for space_id, values in plan.inventory.items():
        measurements.append(_measurement(
            "state", "inventory", values=values, entity_type="space",
            entity_id=space_id, unit="t",
            basis="planned bucket-boundary physical inventory",
            source_path="plan.inventory",
            index=_time_index(sc, offset=offset, alignment="bucket boundary")))
    for entity_id, values in plan.process_reject.items():
        measurements.append(_measurement(
            "output", "process_reject", values=values,
            entity_type="process", entity_id=entity_id, unit="t",
            basis="planned rejected mass per bucket",
            source_path="plan.process_reject",
            index=_time_index(sc, offset=offset)))
    if plan.surge_unstacked:
        measurements.append(_measurement(
            "output", "surge_unstacked", values=plan.surge_unstacked,
            unit="t", basis="unstacked arrivals per bucket",
            source_path="plan.surge_unstacked",
            index=_time_index(sc, offset=offset)))
    horizon_count = max((len(values) for values in plan.delivered.values()),
                        default=max(sc.sim.horizon_buckets - offset, 0))
    for job in plan.jobs:
        input_mass = [0.0] * horizon_count
        output_mass = [0.0] * horizon_count
        for flow in job.bucket_flows:
            local = int(flow.bucket) - offset
            if 0 <= local < horizon_count:
                input_mass[local] += float(flow.tonnage_t)
                output_mass[local] += float(
                    flow.output_tonnage_t or flow.tonnage_t)
        if not job.bucket_flows:
            for bucket in range(job.start_bucket, job.end_bucket + 1):
                local = bucket - offset
                if 0 <= local < horizon_count:
                    input_mass[local] += float(job.rate_tph) * sc.sim.dt_h
                    output_mass[local] += float(job.rate_tph) * sc.sim.dt_h
        for name, values, basis in (
                ("job_input_mass", input_mass,
                 "solver-selected input mass per native bucket"),
                ("job_output_mass", output_mass,
                 "solver-selected output mass per native bucket")):
            measurements.append(_measurement(
                "decision", name, values=values, entity_type="job",
                entity_id=job.id, unit="t", basis=basis,
                source_path="plan.jobs.bucket_flows",
                index=_time_index(sc, offset=offset)))

    special_entity_kpis = {
        "machine_utilisation_pct": ("machine", "%"),
        "route_utilisation_pct": ("route", "%"),
        "route_peak_utilisation_pct": ("route", "%"),
        "safety_stock_min_margin_t": ("space", "t"),
        "inventory_end_t": ("space", "t"),
        "starts_stops": ("machine", "count"),
        "travel_m": ("machine", "m"),
    }
    for name, value in kpis.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            measurements.append(_measurement(
                "kpi", name, value=value, unit=_unit_for_kpi(name),
                basis="whole evaluated horizon", source_path=f"kpis.{name}"))
        elif name in special_entity_kpis and isinstance(value, Mapping):
            entity_type, unit = special_entity_kpis[name]
            for entity_id, entity_value in value.items():
                measurements.append(_measurement(
                    "kpi", name, value=entity_value,
                    entity_type=entity_type, entity_id=str(entity_id),
                    unit=unit, basis="whole evaluated horizon",
                    source_path=f"kpis.{name}.{entity_id}"))
    for destination_id, row in (kpis.get("per_destination", {}) or {}).items():
        for name, value in row.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                measurements.append(_measurement(
                    "kpi", name, value=value, entity_type="destination",
                    entity_id=str(destination_id), unit=_unit_for_kpi(name),
                    basis=str(row.get("service_basis", "destination horizon")),
                    source_path=f"kpis.per_destination.{destination_id}.{name}"))

    decisions = []
    for job in plan.jobs:
        decisions.append({
            "id": job.id, "alternative": job.treatment_id or job.kind,
            "quantity": float(job.tonnage_t), "unit": "t input",
            "start": (sc.sim.minute_dt(job.start_minute).isoformat()
                      if job.start_minute is not None else
                      sc.sim.bucket_dt(job.start_bucket).isoformat()),
            "end": (sc.sim.minute_dt(job.end_minute).isoformat()
                    if job.end_minute is not None else
                    sc.sim.bucket_dt(job.end_bucket + 1).isoformat()),
            "machine": job.machine, "space": job.space,
            "source_space": job.source_space or job.space,
            "target_space": job.target_space,
            "destination": job.dest_id, "grade": job.grade,
            "output_grade": job.output_grade,
            "certified_grade": job.certified_grade,
            "treatment_id": job.treatment_id,
            "attributes": {
                "route": list(job.path), "rate_tph": float(job.rate_tph),
                "output_tonnage_t": float(job.output_tonnage_t),
                "process_stage": job.process_stage,
                "physical_run_ids": list(job.physical_run_ids),
            },
        })

    quality_rows = []
    for assumption in sc.assumptions:
        quality_rows.append({
            "field": assumption.id, "status": assumption.status.lower(),
            "source": assumption.source, "note": assumption.text,
        })
    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "project": PROJECT_ID,
        "model_context": load_model_context(),
        "run": {
            "id": None, "scenario": sc.name, "model_type": "optimization",
            "model_version": "mph_sim/0.1.0", "data_revision": fingerprint,
            "started_at": None,
            "horizon": {"start": start.isoformat(), "end": end.isoformat(),
                        "step_minutes": sc.sim.native_bucket_minutes()},
            "seed": int(sc.sim.seed),
        },
        "termination": {
            "status": plan.status,
            "reason": list(plan.reasons), "objective": plan.objective,
            "best_bound": plan.solver_best_bound,
            "gap_pct": plan.solver_relative_gap_pct,
            "elapsed_s": plan.solver_wall_s, "solver": plan.solver,
            "optimality_scope": plan.solver_optimality_scope,
        },
        "objective": {
            "mode": str(sc.config.get("OBJECTIVE_MODE", "weighted")),
            "passes": copy.deepcopy(plan.objective_pass_records),
            "weights": asdict(sc.weights),
            "breakdown": {key: _finite_or_none(value)
                          for key, value in plan.breakdown.items()},
        },
        "decisions": decisions,
        "alternatives": _candidate_alternatives(sc, plan),
        "candidate_scope": {
            "generated_candidates_only": True,
            "note": ("Alternatives rejected before candidate generation are "
                     "represented only by machine dispositions/validation; "
                     "hard_feasible null means no forced-alternative solve."),
            "machine_dispositions": copy.deepcopy(plan.machine_dispositions),
        },
        "constraints": _derived_constraints(sc, plan),
        "kpis": copy.deepcopy(kpis),
        "measurements": measurements,
        "data_quality": quality_rows,
        "artifacts": [
            {"kind": "config", "location": "snapshots.scenario"},
            {"kind": "solution", "location": "snapshots.plan"},
        ],
        "snapshots": {"scenario": scenario_payload, "plan": plan_payload},
    }


def _path_tokens(path: str) -> List[str]:
    if not isinstance(path, str) or not path.startswith("/"):
        raise AnalysisToolError(
            "counterfactual paths must be JSON-pointer-like, e.g. "
            "'/weights/w1_unmet' or '/machines/RC01/rate_max_tph'")
    return [token.replace("~1", "/").replace("~0", "~")
            for token in path.split("/")[1:] if token != ""]


def _list_item(rows: List[Any], token: str) -> Any:
    if token.isdigit():
        index = int(token)
        if index >= len(rows):
            raise AnalysisToolError(f"list index {index} is out of range")
        return rows[index]
    matches = [row for row in rows if isinstance(row, Mapping) and any(
        str(row.get(field, "")) == token
        for field in ("id", "name", "dest_id", "space_id", "zone_id"))]
    if len(matches) != 1:
        raise AnalysisToolError(
            f"list selector {token!r} matched {len(matches)} rows; use an index")
    return matches[0]


def _apply_change(snapshot: Dict[str, Any], path: str, value: Any) -> Any:
    tokens = _path_tokens(path)
    if not tokens or tokens[0] not in {
            "weights", "sim", "config", "machines", "links", "spaces",
            "destinations", "demand", "process_transfers", "grades",
            "calendars", "exogenous"}:
        raise AnalysisToolError(
            "counterfactual may modify only validated scenario inputs")
    current: Any = snapshot
    for token in tokens[:-1]:
        if isinstance(current, list):
            current = _list_item(current, token)
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            raise AnalysisToolError(f"counterfactual path not found: {path}")
    leaf = tokens[-1]
    if isinstance(current, list):
        raise AnalysisToolError(
            "counterfactual path must name a field, not a whole list item")
    if not isinstance(current, dict) or leaf not in current:
        raise AnalysisToolError(f"counterfactual path not found: {path}")
    previous = current[leaf]
    current[leaf] = value
    return previous


class MphAnalysisTools(ResultAnalysisTools):
    """Tool registry for an agent or UI working with MPH run artifacts."""

    def __init__(self, db_path: str | Path = "runs.db"):
        self.store = RunStore(db_path)
        super().__init__(self.store)

    def _context(self, run_id: Optional[int] = None) -> Dict[str, Any]:
        current = load_model_context()
        if run_id is None:
            return current
        bundle = self.store.load_run_bundle(int(run_id))
        stored = bundle.get("model_context")
        if not isinstance(stored, Mapping):
            result = copy.deepcopy(current)
            result["context_source"] = "current_project_fallback"
            result["context_warning"] = (
                "This run predates embedded model context; current semantics "
                "may not exactly describe the historical run.")
            return result
        result = copy.deepcopy(dict(stored))
        result["context_source"] = "run_snapshot"
        result["current_project"] = {
            "package_version": current.get("package_version"),
            "implementation_fingerprint": current.get(
                "implementation_fingerprint"),
            "package_current": current.get("package_current"),
        }
        result["drift_from_current"] = bool(
            result.get("package_version") != current.get("package_version")
            or result.get("implementation_fingerprint") !=
            current.get("implementation_fingerprint"))
        return result

    def get_model_context(
            self, run_id: Optional[int] = None,
            section: Optional[str] = None) -> Dict[str, Any]:
        context = self._context(run_id)
        selection = select_formulation_section(context, section)
        return {
            "run_id": run_id,
            "context_source": context.get("context_source", "current_project"),
            "project_id": context.get("project_id"),
            "package_version": context.get("package_version"),
            "implementation_fingerprint": context.get(
                "implementation_fingerprint"),
            "package_current": context.get("package_current"),
            "drift_from_current": context.get("drift_from_current", False),
            "context_warning": context.get("context_warning"),
            **selection,
        }

    def _definition(self, category: str, identifier: str,
                    run_id: Optional[int] = None) -> Dict[str, Any]:
        context = self._context(run_id)
        try:
            definition = find_definition(context, category, identifier)
        except KeyError as exc:
            raise AnalysisToolError(str(exc)) from exc
        return {
            "run_id": run_id,
            "context_source": context.get("context_source", "current_project"),
            "package_version": context.get("package_version"),
            "implementation_fingerprint": context.get(
                "implementation_fingerprint"),
            "category": category,
            "definition": definition,
        }

    def get_variable_definition(
            self, identifier: str,
            run_id: Optional[int] = None) -> Dict[str, Any]:
        return self._definition("variables", identifier, run_id)

    def get_objective_term_definition(
            self, identifier: str,
            run_id: Optional[int] = None) -> Dict[str, Any]:
        return self._definition("objective_terms", identifier, run_id)

    def get_constraint_definition(
            self, identifier: str,
            run_id: Optional[int] = None) -> Dict[str, Any]:
        return self._definition("constraints", identifier, run_id)

    def get_kpi_definition(
            self, identifier: str,
            run_id: Optional[int] = None) -> Dict[str, Any]:
        return self._definition("kpis", identifier, run_id)

    def get_code_mapping(
            self, identifier: str,
            run_id: Optional[int] = None) -> Dict[str, Any]:
        context = self._context(run_id)
        try:
            mapping = find_code_mapping(context, identifier)
        except KeyError as exc:
            raise AnalysisToolError(str(exc)) from exc
        return {
            "run_id": run_id,
            "context_source": context.get("context_source", "current_project"),
            "package_version": context.get("package_version"),
            "implementation_fingerprint": context.get(
                "implementation_fingerprint"),
            **mapping,
        }

    def run_counterfactual(
            self, baseline_run_id: int, changes: List[Dict[str, Any]], *,
            max_time_s: Optional[float] = None,
            persist: bool = False) -> Dict[str, Any]:
        if not changes or len(changes) > 20:
            raise AnalysisToolError("provide between 1 and 20 input changes")
        baseline = self.store.load_run_bundle(int(baseline_run_id))
        scenario_snapshot = copy.deepcopy(
            baseline.get("snapshots", {}).get("scenario"))
        if not isinstance(scenario_snapshot, dict):
            raise AnalysisToolError("baseline run has no scenario snapshot")
        applied = []
        for change in changes:
            if not isinstance(change, Mapping) or "path" not in change \
                    or "value" not in change:
                raise AnalysisToolError(
                    "each change requires exact 'path' and 'value' fields")
            previous = _apply_change(
                scenario_snapshot, str(change["path"]), change["value"])
            applied.append({"path": change["path"], "before": previous,
                            "after": change["value"]})
        scenario = scenario_from_json(json.dumps(scenario_snapshot))
        issues = validate_scenario(scenario)
        errors = [asdict(issue) for issue in issues if issue.severity == "ERROR"]
        if errors:
            return {
                "baseline_run_id": int(baseline_run_id), "status": "INVALID",
                "changes": applied, "validation_errors": errors,
                "persisted": False,
            }
        budget = (float(max_time_s) if max_time_s is not None
                  else float(scenario.sim.max_solve_s))
        result = solve_plan(scenario, SolveOptions(max_time_s=budget))
        kpis = compute_kpis(scenario, result)
        bundle = build_run_bundle(scenario, result, kpis)
        run_id = None
        if persist:
            run_id = self.store.record(
                scenario.name, scenario.sim.seed, result.solver, result.status,
                result.solver_wall_s, 0,
                {"objective": result.objective,
                 "unmet_t": kpis.get("unmet_t", 0.0),
                 "service_pct": kpis.get("service_pct"),
                 "jobs": len(result.jobs)},
                note=f"counterfactual of run {baseline_run_id}", artifact=bundle,
                schema_version=BUNDLE_SCHEMA_VERSION, project=PROJECT_ID)
        baseline_kpis = baseline.get("kpis", {})
        kpi_deltas = {}
        for key in sorted(set(baseline_kpis) & set(kpis)):
            left, right = baseline_kpis[key], kpis[key]
            if (isinstance(left, (int, float)) and not isinstance(left, bool)
                    and isinstance(right, (int, float))
                    and not isinstance(right, bool)
                    and math.isfinite(float(left))
                    and math.isfinite(float(right))):
                kpi_deltas[key] = float(right) - float(left)
        treatment_summary: Dict[str, Dict[str, float]] = {}
        for job in result.jobs:
            if not job.treatment_id:
                continue
            row = treatment_summary.setdefault(
                job.treatment_id,
                {"job_count": 0.0, "input_t": 0.0, "output_t": 0.0})
            row["job_count"] += 1
            row["input_t"] += float(job.tonnage_t)
            row["output_t"] += float(job.output_tonnage_t)
        comparison = {
            "status": result.status,
            "run_id": run_id,
            "objective": result.objective,
            "objective_delta": (
                float(result.objective)
                - float(baseline.get("termination", {}).get("objective") or 0.0)),
            "unmet_t": kpis.get("unmet_t"),
            "service_pct": kpis.get("service_pct"),
            "jobs": len(result.jobs),
            "solver_wall_s": result.solver_wall_s,
            "breakdown": result.breakdown,
            "kpi_deltas": kpi_deltas,
            "treatment_summary": treatment_summary,
        }
        return {
            "baseline_run_id": int(baseline_run_id),
            "counterfactual_run_id": run_id,
            "status": result.status, "changes": applied,
            "comparison": comparison, "persisted": bool(persist),
            "causal_isolation": (
                "isolated" if len(changes) == 1 else
                "multiple inputs changed; attribution is not isolated"),
        }

    @staticmethod
    def tool_definitions() -> List[Dict[str, Any]]:
        """Strongly typed function definitions for an agent runtime."""
        query_properties = {
            "run_id": {"type": "integer"},
            "measurement_id": {"type": "string"},
            "namespace": {"type": "string"},
            "name": {"type": "string"},
            "entity_type": {"type": "string"},
            "entity_id": {"type": "string"},
            "start_index": {"type": "integer"},
            "end_index": {"type": "integer"},
            "start_time": {"type": "string"},
            "end_time": {"type": "string"},
        }
        definitions = [
            ("get_model_context", "Read the verified formulation, version and implementation fingerprint; use run_id for historical semantics.",
             {"type": "object", "properties": {
                 "run_id": {"type": "integer"}, "section": {"type": "string"}},
              "additionalProperties": False}),
            ("get_variable_definition", "Resolve a stable model variable id, units, domain, meaning and code references.",
             {"type": "object", "properties": {
                 "identifier": {"type": "string"}, "run_id": {"type": "integer"}},
              "required": ["identifier"], "additionalProperties": False}),
            ("get_objective_term_definition", "Resolve a stable objective-term id, priority, basis and code references.",
             {"type": "object", "properties": {
                 "identifier": {"type": "string"}, "run_id": {"type": "integer"}},
              "required": ["identifier"], "additionalProperties": False}),
            ("get_constraint_definition", "Resolve a stable constraint id, hardness, basis and code references.",
             {"type": "object", "properties": {
                 "identifier": {"type": "string"}, "run_id": {"type": "integer"}},
              "required": ["identifier"], "additionalProperties": False}),
            ("get_kpi_definition", "Resolve a stable KPI id, formula, unit, denominator and basis.",
             {"type": "object", "properties": {
                 "identifier": {"type": "string"}, "run_id": {"type": "integer"}},
              "required": ["identifier"], "additionalProperties": False}),
            ("get_code_mapping", "Map a stable formulation concept id to implementation and run-evidence paths.",
             {"type": "object", "properties": {
                 "identifier": {"type": "string"}, "run_id": {"type": "integer"}},
              "required": ["identifier"], "additionalProperties": False}),
            ("list_runs", "List model runs and detailed-bundle availability.",
             {"type": "object", "properties": {"limit": {"type": "integer"}},
              "additionalProperties": False}),
            ("load_run", "Load run evidence; snapshots are excluded unless requested.",
             {"type": "object", "properties": {
                 "run_id": {"type": "integer"},
                 "include_snapshots": {"type": "boolean"}},
              "required": ["run_id"], "additionalProperties": False}),
            ("catalog_run_data", "List exact input, variable, output and KPI measurements before querying.",
             {"type": "object", "properties": {
                 "run_id": {"type": "integer"}, "namespace": {"type": "string"},
                 "name_contains": {"type": "string"}},
              "required": ["run_id"], "additionalProperties": False}),
            ("query_run_data", "Read exact scalar or indexed values with units and basis.",
             {"type": "object", "properties": query_properties,
              "required": ["run_id"], "additionalProperties": False}),
            ("analyze_variability", "Compute exact descriptive variability for a selected parameter, variable or KPI.",
             {"type": "object", "properties": query_properties,
              "required": ["run_id"], "additionalProperties": False}),
            ("get_objective_breakdown", "Return solver termination, objective mode, tiers, weights and contributions.",
             {"type": "object", "properties": {"run_id": {"type": "integer"}},
              "required": ["run_id"], "additionalProperties": False}),
            ("get_candidate_decisions", "List generated alternatives and whether each was selected.",
             {"type": "object", "properties": {
                 "run_id": {"type": "integer"}, "selected": {"type": "boolean"},
                 "machine": {"type": "string"}, "treatment": {"type": "string"}},
              "required": ["run_id"], "additionalProperties": False}),
            ("explain_decision", "Return a decision evidence packet for interpretation, without inventing a cause.",
             {"type": "object", "properties": {
                 "run_id": {"type": "integer"}, "decision_id": {"type": "string"}},
              "required": ["run_id", "decision_id"],
              "additionalProperties": False}),
            ("compare_runs", "Compare exact scalar measurements or series means between two runs.",
             {"type": "object", "properties": {
                 "baseline_run_id": {"type": "integer"},
                 "comparison_run_id": {"type": "integer"},
                 "measurement_ids": {"type": "array", "items": {"type": "string"}}},
              "required": ["baseline_run_id", "comparison_run_id"],
              "additionalProperties": False}),
            ("run_counterfactual", "Solve an isolated copied scenario with explicit input changes; never mutates the baseline.",
             {"type": "object", "properties": {
                 "baseline_run_id": {"type": "integer"},
                 "changes": {"type": "array", "items": {"type": "object",
                     "properties": {"path": {"type": "string"}, "value": {}},
                     "required": ["path", "value"], "additionalProperties": False}},
                 "max_time_s": {"type": "number"},
                 "persist": {"type": "boolean"}},
              "required": ["baseline_run_id", "changes"],
              "additionalProperties": False}),
        ]
        return [{"type": "function", "name": name,
                 "description": description, "parameters": parameters}
                for name, description, parameters in definitions]

    def execute_tool(self, name: str, arguments: Mapping[str, Any]) -> Dict[str, Any]:
        """Validated local dispatcher suitable for a function-call loop."""
        handlers = {
            "get_model_context": self.get_model_context,
            "get_variable_definition": self.get_variable_definition,
            "get_objective_term_definition": self.get_objective_term_definition,
            "get_constraint_definition": self.get_constraint_definition,
            "get_kpi_definition": self.get_kpi_definition,
            "get_code_mapping": self.get_code_mapping,
            "list_runs": self.list_runs,
            "load_run": self.load_run,
            "catalog_run_data": self.catalog_run_data,
            "query_run_data": self.query_run_data,
            "analyze_variability": self.analyze_variability,
            "get_objective_breakdown": self.get_objective_breakdown,
            "get_candidate_decisions": self.get_candidate_decisions,
            "explain_decision": self.explain_decision,
            "compare_runs": self.compare_runs,
            "run_counterfactual": self.run_counterfactual,
        }
        if name not in handlers:
            raise AnalysisToolError(f"unknown analysis tool {name!r}")
        try:
            return handlers[name](**dict(arguments))
        except TypeError as exc:
            raise AnalysisToolError(f"invalid arguments for {name}: {exc}") from exc
