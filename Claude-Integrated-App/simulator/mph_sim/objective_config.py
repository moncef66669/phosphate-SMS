"""Shared validation/defaults for operational planner objective terms."""
from __future__ import annotations

import math
from typing import List

from .campaigns import (CAMPAIGN_POLICY_KEY, campaign_policy_errors,
                        SHORT_PARCEL_PENALTY_DEFAULT, short_parcel_penalty_errors)
from .continuity import continuity_policy_errors
from .domain import OBJECTIVE_WEIGHT_MAX, Scenario
from .energy import energy_objective_parameter_errors


OPERATIONAL_PENALTY_DEFAULTS = {
    "CONTINUITY_GAP_MINUTE_PENALTY": 10,
    "CONTINUITY_EPISODE_PENALTY": 100,
    "ENERGY_OBJECTIVE_CENTIKWH_PENALTY": 1,
    "EQUIPMENT_START_PENALTY": 20,
    "EQUIPMENT_STOP_PENALTY": 10,
    "EQUIPMENT_CHANGEOVER_PENALTY": 20,
    # Cost of holding a machine running through one bucket without moving
    # material. ASSUMED: it must be cheaper than a stop plus a later start
    # (10 + 20) or holding would never be chosen, and dearer than nothing or
    # the planner would hold everywhere for free. No plant figure exists for
    # the energy/wear of an idling drive; see assumption S3-A34.
    "EQUIPMENT_HOLD_BUCKET_PENALTY": 8,
}

#: How the solve is bounded, and therefore whether the plan is reproducible.
#:
#: ``fast``          - CP-SAT portfolio (CP_SAT_WORKERS) with wall-clock pacing.
#:                     Predictable latency; the returned plan may differ between
#:                     runs whenever the model has tied optima, because which
#:                     parallel worker wins and where the wall cutoff lands are
#:                     both machine-load dependent. NOT reproducible - do not
#:                     quote a KPI from a single fast run.
#: ``reproducible``  - single worker, deterministic-time budget only. Wall clock
#:                     degrades to a pure hang guard. Identical inputs, config
#:                     and seed return an identical plan (acceptance #11), at the
#:                     cost of unpredictable wall time.
SOLVE_MODES = ("fast", "reproducible")

SCHEDULING_DEFAULTS = {
    # Imported scenarios retain their established weighted contract. Built-in
    # phosphate scenarios explicitly select service_first when installed.
    "OBJECTIVE_MODE": "weighted",
    # Default preserves existing behaviour; reproducibility is opt-in.
    "SOLVE_MODE": "fast",
    "MIN_JOB_VOLUME_T": 0.0,
    "OBJECTIVE_SECONDARY_WALL_TIME_S": 15.0,
    "OBJECTIVE_SECONDARY_DETERMINISTIC_TIME_S": 4.0,
    "SERVICE_DEGRADATION_T": 0.0,
    "SHORT_PARCEL_PENALTY": SHORT_PARCEL_PENALTY_DEFAULT,
}


def scheduling_values(scenario):
    return {key: scenario.config.get(key, default)
            for key, default in SCHEDULING_DEFAULTS.items()}


def scheduling_errors(values):
    errors = []
    if values.get("OBJECTIVE_MODE", "weighted") not in ("service_first", "weighted"):
        errors.append("OBJECTIVE_MODE must be service_first or weighted")
    if values.get("SOLVE_MODE", "fast") not in SOLVE_MODES:
        errors.append("SOLVE_MODE must be " + " or ".join(SOLVE_MODES))
    for key in ("MIN_JOB_VOLUME_T", "SERVICE_DEGRADATION_T",
                "OBJECTIVE_SECONDARY_WALL_TIME_S", "OBJECTIVE_SECONDARY_DETERMINISTIC_TIME_S"):
        raw = values.get(key, SCHEDULING_DEFAULTS[key])
        try:
            number = float(raw)
        except (TypeError, ValueError, OverflowError):
            number = float("nan")
        if isinstance(raw, bool) or not math.isfinite(number) or number < 0:
            errors.append(f"{key} must be a finite nonnegative number")
        elif "TIME" in key and number > 120:
            errors.append(f"{key} must not exceed 120 seconds")
        elif key in ("MIN_JOB_VOLUME_T", "SERVICE_DEGRADATION_T") and number > 2**53 - 1:
            errors.append(f"{key} exceeds exact whole-tonne numerical range")
    raw = values.get("SHORT_PARCEL_PENALTY", SHORT_PARCEL_PENALTY_DEFAULT)
    try:
        number = float(raw)
    except (TypeError, ValueError, OverflowError):
        number = float("nan")
    if isinstance(raw, bool) or not math.isfinite(number) or number < 0 or not number.is_integer() or number > 9999:
        errors.append("SHORT_PARCEL_PENALTY must be a whole number between 0 and 9999")
    return errors


def isolated_source_target_shortages(scenario, *, horizon_offset=0):
    """Proven shortages only: unit-yield stages fed by unreplenished sources.

    This deliberately does not claim a general network feasibility test.
    Forecast targets are warnings; explicit commitments are contradictions.
    """
    from .flow_config import effective_process_stage_targets
    targets = effective_process_stage_targets(scenario)
    raw_planning = scenario.config.get("PLANNING_PROCESS_STAGE_TARGETS", [])
    planning = set(raw_planning) if isinstance(raw_planning, list) and all(isinstance(v, str) for v in raw_planning) else set()
    uses = {}
    for stage, target in targets.items():
        transfers = [tr for tr in scenario.process_transfers if tr.process_stage == stage]
        if len(transfers) != 1:
            continue
        transfer = transfers[0]
        if transfer.mass_conversion or transfer.yield_factor != 1:
            continue
        try:
            tonnes = max(int(round(float(target))), 0)
        except (TypeError, ValueError, OverflowError):
            continue  # Normal input validation reports malformed targets.
        uses.setdefault(transfer.source_space, []).append((stage, tonnes, stage in planning))
    shortages = []
    for sid, stages in uses.items():
        source = next((space for space in scenario.spaces if space.id == sid), None)
        if source is None:
            continue
        nodes = {sid, f"SPACE:{sid}", source.node_id}
        if (any(tr.target_space == sid for tr in scenario.process_transfers)
                or any(link.dst in nodes for link in scenario.links)):
            continue
        try:
            available = int(round(source.mass_t))
        except (TypeError, ValueError, OverflowError):
            continue
        for flow in scenario.exogenous:
            if flow.target_space != sid:
                continue
            for bucket in range(horizon_offset, horizon_offset + scenario.sim.horizon_buckets):
                if flow.start_bucket <= bucket <= flow.end_bucket:
                    try:
                        available += int(round(flow.rate_tph * scenario.sim.dt_h))
                    except (TypeError, ValueError, OverflowError):
                        continue
        required = sum(tonnes for _, tonnes, _ in stages)
        hard_required = sum(tonnes for _, tonnes, soft in stages if not soft)
        if required > available:
            shortages.append(dict(source=sid, available_t=available, target_t=required,
                                  shortage_t=required - available,
                                  hard=hard_required > available,
                                  stages=[stage for stage, _, _ in stages]))
    return shortages


def operational_penalty(scenario: Scenario, key: str) -> int:
    """Return one already-validated nonnegative integer penalty."""
    return int(scenario.config.get(
        key, OPERATIONAL_PENALTY_DEFAULTS[key]))


def operational_objective_errors(scenario: Scenario) -> List[str]:
    """Validate every scenario value that can steer the new CP-SAT terms."""
    errors = continuity_policy_errors(
        scenario.config.get("CONTINUITY_POLICIES", {}),
        (destination.id for destination in scenario.destinations))
    errors.extend(scheduling_errors(scheduling_values(scenario)))
    for key, known in (("PLANNING_PROCESS_STAGE_TARGETS", {
            tr.process_stage for tr in scenario.process_transfers}),
            ("PLANNING_CORRIDOR_TARGETS", {link.id for link in scenario.links})):
        raw = scenario.config.get(key, [])
        if not isinstance(raw, list) or any(not isinstance(v, str) or v not in known for v in raw):
            errors.append(f"{key} must be a list of configured identifiers")
    for key, default in OPERATIONAL_PENALTY_DEFAULTS.items():
        raw = scenario.config.get(key, default)
        try:
            value = float(raw)
        except (TypeError, ValueError, OverflowError):
            value = float("nan")
        if (isinstance(raw, bool) or not math.isfinite(value) or value < 0
                or not value.is_integer() or value > OBJECTIVE_WEIGHT_MAX):
            errors.append(
                f"{key} must be a whole number between 0 and "
                f"{OBJECTIVE_WEIGHT_MAX:g}")

    energy_enabled = scenario.config.get(
        "ENERGY_OBJECTIVE_ENABLED", False)
    if not isinstance(energy_enabled, bool):
        errors.append("ENERGY_OBJECTIVE_ENABLED must be true or false")
        energy_enabled = False
    errors.extend(energy_objective_parameter_errors(
        scenario, enabled=bool(energy_enabled)))
    errors.extend(campaign_policy_errors(
        scenario.config.get(CAMPAIGN_POLICY_KEY),
        (machine.id for machine in scenario.machines)))
    errors.extend(short_parcel_penalty_errors(scenario))
    batching_enabled = scenario.config.get("PARCEL_CONSOLIDATION_ENABLED", False)
    if not isinstance(batching_enabled, bool):
        errors.append("PARCEL_CONSOLIDATION_ENABLED must be true or false")
    for key in ("PARCEL_CONSOLIDATION_WALL_TIME_S",
                "PARCEL_CONSOLIDATION_DETERMINISTIC_TIME_S"):
        raw = scenario.config.get(key, 15.0 if "WALL" in key else 4.0)
        try:
            value = float(raw)
        except (TypeError, ValueError, OverflowError):
            value = float("nan")
        if (isinstance(raw, bool) or not math.isfinite(value)
                or value <= 0 or value > 120):
            errors.append(f"{key} must be finite and between 0 (exclusive) and 120")
    return errors
