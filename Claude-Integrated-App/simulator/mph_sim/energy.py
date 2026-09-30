"""Auditable planning estimate for electricity and movement performance.

The simulator does not have motor curves or meter telemetry.  This module
therefore keeps every electrical coefficient explicit and configurable.  It
uses physical job tonnes, route-link traversals and the scheduler's validated
positioning hours; conflicts are reported as planning defects and never
masquerade as consumed energy.
"""
from __future__ import annotations

import math
from typing import Iterable, Mapping

from .domain import PlanResult, Scenario


DEFAULT_REFERENCE_KWH_PER_T = 1.0
DEFAULT_LINK_KWH_PER_T = 0.04
DEFAULT_POSITIONING_POWER_KW = 200.0
DEFAULT_WORK_KWH_PER_T = {
    "stack": 0.12,
    "reclaim": 0.15,
    "dispatch": 0.10,
    "default": 0.12,
}

# CP-SAT accepts integer objective coefficients.  One unit represents one
# centi-kWh so the current two-decimal planning intensities are retained
# exactly without introducing unnecessarily large coefficients.
ENERGY_OBJECTIVE_SCALE = 100
# A deliberately generous modelling guard, not a statement of expected plant
# performance.  Values above this are still reportable, but cannot steer an
# integer optimizer safely without a reviewed scaling/bounds study.
MAX_OBJECTIVE_ENERGY_INTENSITY_KWH_PER_T = 100.0


def _finite_nonnegative(value, fallback: float,
                        warnings: list[str], label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        parsed = float("nan")
    if not math.isfinite(parsed) or parsed < 0:
        warnings.append(f"{label} is invalid; using {fallback:g}.")
        return fallback
    return parsed


def _configured_work_intensities(config: Mapping[str, object],
                                 warnings: list[str]) -> dict[str, object]:
    """Return the configured work map, disclosing malformed containers.

    The legacy implementation accepted mappings and values convertible by
    ``dict``.  Retain that behaviour, while treating a malformed container in
    the same auditable way as a malformed individual coefficient.
    """
    raw = config.get("ENERGY_WORK_KWH_PER_T", {}) or {}
    try:
        return dict(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        warnings.append(
            "Work energy intensities are invalid; using defaults.")
        return {}


def energy_model_parameters(scenario: Scenario) -> dict[str, object]:
    """Resolve scenario overrides without hiding invalid input values."""
    warnings: list[str] = []
    config = scenario.config
    reference = _finite_nonnegative(
        config.get("ENERGY_REFERENCE_KWH_PER_T",
                   DEFAULT_REFERENCE_KWH_PER_T),
        DEFAULT_REFERENCE_KWH_PER_T, warnings,
        "Specific-electricity reference")
    link_intensity = _finite_nonnegative(
        config.get("ENERGY_LINK_KWH_PER_T",
                   DEFAULT_LINK_KWH_PER_T),
        DEFAULT_LINK_KWH_PER_T, warnings,
        "Conveyor-link energy intensity")
    positioning_power = _finite_nonnegative(
        config.get("ENERGY_POSITIONING_POWER_KW",
                   DEFAULT_POSITIONING_POWER_KW),
        DEFAULT_POSITIONING_POWER_KW, warnings,
        "Positioning power")
    configured_work = _configured_work_intensities(config, warnings)
    work_intensity = {
        kind: _finite_nonnegative(
            configured_work.get(kind, fallback), fallback, warnings,
            f"{kind} work energy intensity")
        for kind, fallback in DEFAULT_WORK_KWH_PER_T.items()
    }
    configured = any(key in config for key in (
        "ENERGY_REFERENCE_KWH_PER_T", "ENERGY_LINK_KWH_PER_T",
        "ENERGY_POSITIONING_POWER_KW", "ENERGY_WORK_KWH_PER_T"))
    return {
        "reference_kwh_per_t": reference,
        "link_kwh_per_t": link_intensity,
        "positioning_power_kw": positioning_power,
        "work_kwh_per_t": work_intensity,
        "configured": configured,
        "warnings": warnings,
    }


def work_route_energy_coefficient(
        scenario: Scenario,
        operation_kind: str,
        path_links: Iterable[str] | None = None,
) -> int:
    """Return integer centi-kWh per handled tonne for one candidate path.

    ``operation_kind`` is the candidate's display/physical operation kind
    (for example, a process candidate may display as ``stack`` or
    ``reclaim``).  Conveyor energy follows the reporting model and counts each
    distinct path link once.  Invalid configured values retain the reporting
    fallback here; an enabled optimiser must separately call
    :func:`energy_objective_parameter_errors` and reject any returned errors.
    """
    params = energy_model_parameters(scenario)
    work_rates = dict(params["work_kwh_per_t"])
    kind = str(operation_kind or "").strip().lower()
    work_kwh_per_t = float(
        work_rates.get(kind, work_rates["default"]))
    distinct_link_count = len(set(path_links or ()))
    total_kwh_per_t = (
        work_kwh_per_t
        + distinct_link_count * float(params["link_kwh_per_t"])
    )
    if not math.isfinite(total_kwh_per_t):
        # Reporting must remain available even when individually finite
        # coefficients overflow only after route-link multiplication.  An
        # enabled objective rejects the raw inputs below.
        total_kwh_per_t = (
            DEFAULT_WORK_KWH_PER_T.get(
                kind, DEFAULT_WORK_KWH_PER_T["default"])
            + distinct_link_count * DEFAULT_LINK_KWH_PER_T)
    return int(round(total_kwh_per_t * ENERGY_OBJECTIVE_SCALE))


def energy_objective_parameter_errors(
        scenario: Scenario,
        *,
        enabled: bool = True,
) -> list[str]:
    """Validate coefficients that affect the work/route energy objective.

    Reporting deliberately falls back from malformed assumptions so a Monitor
    page remains usable.  Optimisation must be stricter: when its energy term
    is enabled, a malformed configured link or work coefficient is returned as
    an error instead of silently steering a plan with a fallback.  Reference
    SEC and positioning power are not checked because neither participates in
    this work/route objective.
    """
    if not enabled:
        return []

    config = scenario.config
    errors: list[str] = []

    def validate(value, label: str) -> None:
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError):
            parsed = float("nan")
        if not math.isfinite(parsed) or parsed < 0:
            errors.append(f"{label} must be finite and nonnegative.")
        elif parsed > MAX_OBJECTIVE_ENERGY_INTENSITY_KWH_PER_T:
            errors.append(
                f"{label} must not exceed "
                f"{MAX_OBJECTIVE_ENERGY_INTENSITY_KWH_PER_T:g} kWh/t "
                "when the energy objective is enabled.")

    if "ENERGY_LINK_KWH_PER_T" in config:
        validate(config["ENERGY_LINK_KWH_PER_T"],
                 "Conveyor-link energy intensity")

    if "ENERGY_WORK_KWH_PER_T" in config:
        raw_work = config.get("ENERGY_WORK_KWH_PER_T") or {}
        try:
            configured_work = dict(raw_work)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            errors.append("Work energy intensities must be a mapping.")
            configured_work = {}
        for kind in DEFAULT_WORK_KWH_PER_T:
            if kind in configured_work:
                validate(configured_work[kind],
                         f"{kind} work energy intensity")

    # Defensively validate the resolved values as well as explicit overrides.
    # This also protects the optimiser if a future default is edited badly.
    params = energy_model_parameters(scenario)
    validate(params["link_kwh_per_t"],
             "Resolved conveyor-link energy intensity")
    for kind, value in dict(params["work_kwh_per_t"]).items():
        validate(value, f"Resolved {kind} work energy intensity")
    return errors


def compute_plan_energy(
        scenario: Scenario,
        plan: PlanResult,
        useful_delivered_t: float,
        *,
        positioning_hours_by_machine: Mapping[str, float] | None = None,
        conflict_hours: float = 0.0,
        conflict_count: int = 0,
) -> dict[str, object]:
    """Estimate plan electricity with an explicit useful-tonne denominator.

    ``useful_delivered_t`` is the external, on-time delivered mass.  Using it
    as the denominator makes repeated blending/rehandling visible: every job
    and conveyor traversal adds kWh, while the useful product tonnes do not
    increase merely because the same material was moved again.
    """
    params = energy_model_parameters(scenario)
    work_rates = dict(params["work_kwh_per_t"])
    handled_t = 0.0
    work_kwh = 0.0
    link_kwh = 0.0
    for job in plan.jobs:
        status = str(getattr(job, "status", "PLANNED") or "PLANNED").upper()
        if status in {"CANCELLED", "REJECTED"}:
            continue
        mass_t = max(float(getattr(job, "tonnage_t", 0.0) or 0.0), 0.0)
        handled_t += mass_t
        kind = str(getattr(job, "kind", "") or "").lower()
        work_kwh += mass_t * float(
            work_rates.get(kind, work_rates["default"]))

        bucket_flows = list(getattr(job, "bucket_flows", []) or [])
        if bucket_flows:
            link_tonnes = sum(
                max(float(flow.tonnage_t), 0.0)
                * len(set(flow.path or getattr(job, "path", []) or []))
                for flow in bucket_flows
            )
        else:
            link_tonnes = mass_t * len(set(getattr(job, "path", []) or []))
        link_kwh += link_tonnes * float(params["link_kwh_per_t"])

    positioning = dict(positioning_hours_by_machine or {})
    positioning_h = sum(max(float(hours), 0.0)
                        for hours in positioning.values())
    positioning_kwh = positioning_h * float(
        params["positioning_power_kw"])
    total_kwh = work_kwh + link_kwh + positioning_kwh
    delivered_t = max(float(useful_delivered_t or 0.0), 0.0)
    specific = total_kwh / delivered_t if delivered_t > 0 else None
    reference = float(params["reference_kwh_per_t"])
    variance = specific - reference if specific is not None else None
    variance_pct = (
        100.0 * variance / reference
        if variance is not None and reference > 0 else None)
    if variance is None:
        reference_status = "pending"
    elif abs(variance) <= 1e-9:
        reference_status = "at reference"
    elif variance > 0:
        reference_status = "above reference"
    else:
        reference_status = "below reference"

    return {
        "total_kwh": total_kwh,
        "work_kwh": work_kwh,
        "link_kwh": link_kwh,
        "positioning_kwh": positioning_kwh,
        "specific_kwh_per_delivered_t": specific,
        "reference_kwh_per_t": reference,
        "reference_variance_kwh_per_t": variance,
        "reference_variance_pct": variance_pct,
        "reference_status": reference_status,
        "handled_t": handled_t,
        "useful_delivered_t": delivered_t,
        "handling_factor": handled_t / delivered_t if delivered_t else None,
        "positioning_h": positioning_h,
        "conflict_h": max(float(conflict_hours or 0.0), 0.0),
        "conflict_count": max(int(conflict_count or 0), 0),
        "model_configured": bool(params["configured"]),
        "model_warnings": list(params["warnings"]),
    }
