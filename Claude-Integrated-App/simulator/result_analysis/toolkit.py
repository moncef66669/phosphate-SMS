"""Generic, deterministic tools over normalized model-result bundles.

The module deliberately contains no LLM client and no project logic.  It is a
small read-only kernel that can be wired to OpenAI function tools, another
agent framework, a CLI, or an application UI.  Domain adapters are responsible
for creating truthful measurements and for running isolated counterfactuals.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
import statistics
from typing import (Any, Dict, Iterable, List, Mapping, Optional, Protocol,
                    Sequence)


class AnalysisToolError(ValueError):
    """A caller-facing contract or lookup error."""


class RunBundleRepository(Protocol):
    """Minimal persistence contract required by :class:`ResultAnalysisTools`."""

    def load_run_bundle(self, run_id: int) -> Dict[str, Any]: ...

    def list_run_bundles(self, limit: int = 100) -> List[Dict[str, Any]]: ...


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(float(value))


def _percentile(values: Sequence[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _timestamp_at(index: Mapping[str, Any], position: int) -> str | int:
    if index.get("kind") != "time":
        return position + int(index.get("offset", 0) or 0)
    try:
        start = datetime.fromisoformat(str(index["start"]))
        step_minutes = float(index["step_minutes"])
        offset = int(index.get("offset", 0) or 0)
    except (KeyError, TypeError, ValueError) as exc:
        raise AnalysisToolError(f"invalid measurement time index: {exc}") from exc
    return (start + timedelta(minutes=(position + offset) * step_minutes)).isoformat()


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise AnalysisToolError(
            f"invalid ISO-8601 time {value!r}") from exc


def _slice_measurement(
        measurement: Mapping[str, Any], *, start_index: Optional[int] = None,
        end_index: Optional[int] = None, start_time: Optional[str] = None,
        end_time: Optional[str] = None) -> Dict[str, Any]:
    result = dict(measurement)
    if "values" not in measurement:
        return result
    values = list(measurement.get("values") or [])
    index = dict(measurement.get("index") or {"kind": "ordinal", "offset": 0})
    first = max(int(start_index or 0), 0)
    last = len(values) if end_index is None else min(max(int(end_index), 0), len(values))
    requested_start = _parse_time(start_time)
    requested_end = _parse_time(end_time)
    if requested_start is not None or requested_end is not None:
        if index.get("kind") != "time":
            raise AnalysisToolError(
                "start_time/end_time require a time-indexed measurement")
        matching = []
        for position in range(len(values)):
            timestamp = datetime.fromisoformat(str(_timestamp_at(index, position)))
            if requested_start is not None and timestamp < requested_start:
                continue
            if requested_end is not None and timestamp >= requested_end:
                continue
            matching.append(position)
        if not matching:
            first = last = 0
        else:
            first = max(first, matching[0])
            last = min(last, matching[-1] + 1)
    if last < first:
        raise AnalysisToolError("end_index must not be before start_index")
    selected = values[first:last]
    result["values"] = selected
    selected_index = dict(index)
    selected_index["offset"] = int(index.get("offset", 0) or 0) + first
    result["index"] = selected_index
    result["observations"] = [
        {"index": _timestamp_at(index, position), "value": values[position]}
        for position in range(first, last)
    ]
    result["selection"] = {
        "start_position": first, "end_position_exclusive": last,
        "selected_count": len(selected), "total_count": len(values),
    }
    return result


def _variability(measurement: Mapping[str, Any]) -> Dict[str, Any]:
    if "values" not in measurement:
        scalar = measurement.get("value")
        return {
            "count": 1 if scalar is not None else 0,
            "missing_count": 1 if scalar is None else 0,
            "constant": True,
            "value": scalar,
        }
    raw = list(measurement.get("values") or [])
    numeric_positions = [
        (position, float(value)) for position, value in enumerate(raw)
        if _is_number(value)
    ]
    missing = len(raw) - len(numeric_positions)
    if not numeric_positions:
        return {
            "count": 0, "missing_count": missing,
            "constant": None, "note": "no finite numeric observations",
        }
    positions, values = zip(*numeric_positions)
    mean = statistics.fmean(values)
    stdev = statistics.pstdev(values) if len(values) > 1 else 0.0
    minimum = min(values)
    maximum = max(values)
    min_position = positions[values.index(minimum)]
    max_position = positions[values.index(maximum)]
    index = dict(measurement.get("index") or {"kind": "ordinal", "offset": 0})
    changes = sum(
        1 for left, right in zip(values, values[1:])
        if not math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12))
    first, last = values[0], values[-1]
    return {
        "count": len(values),
        "missing_count": missing,
        "min": minimum,
        "min_index": _timestamp_at(index, min_position),
        "max": maximum,
        "max_index": _timestamp_at(index, max_position),
        "range": maximum - minimum,
        "mean": mean,
        "median": statistics.median(values),
        "population_stdev": stdev,
        "coefficient_of_variation_pct": (
            100.0 * stdev / abs(mean) if not math.isclose(mean, 0.0) else None),
        "p05": _percentile(values, 0.05),
        "p95": _percentile(values, 0.95),
        "first": first,
        "last": last,
        "delta": last - first,
        "change_count": changes,
        "constant": math.isclose(minimum, maximum, rel_tol=1e-12, abs_tol=1e-12),
    }


def _measurement_id(row: Mapping[str, Any]) -> str:
    return str(row.get("id") or ":".join(filter(None, (
        str(row.get("namespace", "")), str(row.get("name", "")),
        str(row.get("entity_type", "")), str(row.get("entity_id", "")),
    ))))


@dataclass
class ResultAnalysisTools:
    """Generic tools that operate on immutable, normalized run bundles."""

    repository: RunBundleRepository

    def list_runs(self, limit: int = 100) -> Dict[str, Any]:
        rows = self.repository.list_run_bundles(limit=max(1, min(int(limit), 500)))
        return {"runs": rows, "count": len(rows)}

    def load_run(self, run_id: int, include_snapshots: bool = False) -> Dict[str, Any]:
        bundle = dict(self.repository.load_run_bundle(int(run_id)))
        if not include_snapshots:
            bundle.pop("snapshots", None)
        return bundle

    def catalog_run_data(
            self, run_id: int, namespace: Optional[str] = None,
            name_contains: Optional[str] = None) -> Dict[str, Any]:
        bundle = self.repository.load_run_bundle(int(run_id))
        needle = str(name_contains or "").casefold()
        rows = []
        for measurement in bundle.get("measurements", []):
            if namespace and measurement.get("namespace") != namespace:
                continue
            if needle and needle not in str(measurement.get("name", "")).casefold():
                continue
            rows.append({
                "id": _measurement_id(measurement),
                "namespace": measurement.get("namespace", ""),
                "name": measurement.get("name", ""),
                "entity_type": measurement.get("entity_type", ""),
                "entity_id": measurement.get("entity_id", ""),
                "unit": measurement.get("unit", ""),
                "basis": measurement.get("basis", ""),
                "kind": "series" if "values" in measurement else "scalar",
                "count": len(measurement.get("values", []))
                         if "values" in measurement else 1,
                "source_path": measurement.get("source_path", ""),
            })
        rows.sort(key=lambda row: (
            row["namespace"], row["name"], row["entity_type"], row["entity_id"]))
        return {"run_id": int(run_id), "measurements": rows, "count": len(rows)}

    def _find_measurements(
            self, run_id: int, *, measurement_id: Optional[str] = None,
            namespace: Optional[str] = None, name: Optional[str] = None,
            entity_type: Optional[str] = None,
            entity_id: Optional[str] = None) -> List[Dict[str, Any]]:
        bundle = self.repository.load_run_bundle(int(run_id))
        matches = []
        for measurement in bundle.get("measurements", []):
            if measurement_id and _measurement_id(measurement) != measurement_id:
                continue
            if namespace and measurement.get("namespace") != namespace:
                continue
            if name and measurement.get("name") != name:
                continue
            if entity_type and measurement.get("entity_type") != entity_type:
                continue
            if entity_id and measurement.get("entity_id") != entity_id:
                continue
            matches.append(dict(measurement))
        if not matches:
            raise AnalysisToolError(
                "no measurement matched; call catalog_run_data first and use "
                "an exact id or exact namespace/name/entity")
        return matches

    def query_run_data(
            self, run_id: int, *, measurement_id: Optional[str] = None,
            namespace: Optional[str] = None, name: Optional[str] = None,
            entity_type: Optional[str] = None,
            entity_id: Optional[str] = None,
            start_index: Optional[int] = None, end_index: Optional[int] = None,
            start_time: Optional[str] = None,
            end_time: Optional[str] = None) -> Dict[str, Any]:
        matches = self._find_measurements(
            run_id, measurement_id=measurement_id, namespace=namespace,
            name=name, entity_type=entity_type, entity_id=entity_id)
        selected = [
            _slice_measurement(
                row, start_index=start_index, end_index=end_index,
                start_time=start_time, end_time=end_time)
            for row in matches
        ]
        return {"run_id": int(run_id), "measurements": selected,
                "count": len(selected)}

    def analyze_variability(self, run_id: int, **query: Any) -> Dict[str, Any]:
        matches = self.query_run_data(run_id, **query)["measurements"]
        return {
            "run_id": int(run_id),
            "results": [{
                "id": _measurement_id(row),
                "namespace": row.get("namespace", ""),
                "name": row.get("name", ""),
                "entity_type": row.get("entity_type", ""),
                "entity_id": row.get("entity_id", ""),
                "unit": row.get("unit", ""),
                "basis": row.get("basis", ""),
                "statistics": _variability(row),
            } for row in matches],
        }

    def get_objective_breakdown(self, run_id: int) -> Dict[str, Any]:
        bundle = self.repository.load_run_bundle(int(run_id))
        return {
            "run_id": int(run_id),
            "termination": bundle.get("termination", {}),
            "objective": bundle.get("objective", {}),
        }

    def get_candidate_decisions(
            self, run_id: int, selected: Optional[bool] = None,
            machine: Optional[str] = None,
            treatment: Optional[str] = None) -> Dict[str, Any]:
        bundle = self.repository.load_run_bundle(int(run_id))
        alternatives = []
        for row in bundle.get("alternatives", []):
            if selected is not None and bool(row.get("selected")) is not selected:
                continue
            if machine and row.get("machine") != machine:
                continue
            if treatment and row.get("treatment_id") != treatment:
                continue
            alternatives.append(row)
        return {
            "run_id": int(run_id),
            "alternatives": alternatives,
            "count": len(alternatives),
            "candidate_scope": bundle.get("candidate_scope", {}),
        }

    def explain_decision(self, run_id: int, decision_id: str) -> Dict[str, Any]:
        """Return evidence for an agent to explain; do not invent prose causes."""
        bundle = self.repository.load_run_bundle(int(run_id))
        decisions = [row for row in bundle.get("decisions", [])
                     if str(row.get("id")) == str(decision_id)]
        alternatives = [row for row in bundle.get("alternatives", [])
                        if str(row.get("decision_id")) == str(decision_id)
                        or str(row.get("id")) == str(decision_id)]
        if not decisions and not alternatives:
            raise AnalysisToolError(f"unknown decision/candidate id {decision_id!r}")
        anchors = decisions + alternatives
        machines = {row.get("machine") for row in anchors if row.get("machine")}
        spaces = {
            value for row in anchors
            for value in (row.get("source_space"), row.get("target_space"),
                          row.get("space")) if value
        }
        related_alternatives = [
            row for row in bundle.get("alternatives", [])
            if (machines and row.get("machine") in machines)
            or (spaces and ({row.get("source_space"), row.get("target_space"),
                             row.get("space")} & spaces))
        ]
        related_constraints = [
            row for row in bundle.get("constraints", [])
            if row.get("entity") in machines | spaces
        ]
        return {
            "run_id": int(run_id),
            "decision": decisions,
            "matching_alternatives": alternatives,
            "related_alternatives": related_alternatives,
            "related_constraints": related_constraints,
            "objective": bundle.get("objective", {}),
            "termination": bundle.get("termination", {}),
            "interpretation_note": (
                "This is an evidence packet, not a causal conclusion. Compare "
                "eligibility, constraints and objective tiers, then use an "
                "isolated counterfactual if alternatives remain indistinguishable."),
        }

    def compare_runs(
            self, baseline_run_id: int, comparison_run_id: int,
            measurement_ids: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        baseline = self.repository.load_run_bundle(int(baseline_run_id))
        comparison = self.repository.load_run_bundle(int(comparison_run_id))
        requested = set(measurement_ids or [])
        base_rows = {_measurement_id(row): row
                     for row in baseline.get("measurements", [])}
        comp_rows = {_measurement_id(row): row
                     for row in comparison.get("measurements", [])}
        common = sorted(set(base_rows) & set(comp_rows))
        if requested:
            missing = requested - set(common)
            if missing:
                raise AnalysisToolError(
                    "measurements missing from one or both runs: "
                    + ", ".join(sorted(missing)))
            common = sorted(requested)
        else:
            common = [mid for mid in common
                      if "value" in base_rows[mid] and "value" in comp_rows[mid]
                      and _is_number(base_rows[mid].get("value"))
                      and _is_number(comp_rows[mid].get("value"))]
        changes = []
        for measurement_id in common:
            left, right = base_rows[measurement_id], comp_rows[measurement_id]
            if "value" in left and "value" in right:
                base_value, comp_value = left.get("value"), right.get("value")
            else:
                base_value = _variability(left).get("mean")
                comp_value = _variability(right).get("mean")
            if not _is_number(base_value) or not _is_number(comp_value):
                continue
            delta = float(comp_value) - float(base_value)
            changes.append({
                "id": measurement_id,
                "unit": left.get("unit", ""),
                "basis": left.get("basis", ""),
                "baseline": base_value,
                "comparison": comp_value,
                "delta": delta,
                "delta_pct": (100.0 * delta / abs(float(base_value))
                              if not math.isclose(float(base_value), 0.0)
                              else None),
                "comparison_statistic": (
                    "scalar" if "value" in left else "series mean"),
            })
        return {
            "baseline_run_id": int(baseline_run_id),
            "comparison_run_id": int(comparison_run_id),
            "baseline_status": baseline.get("termination", {}).get("status"),
            "comparison_status": comparison.get("termination", {}).get("status"),
            "changes": changes,
        }
