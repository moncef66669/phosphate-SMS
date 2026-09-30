"""Reproducible, explicitly assumed variants for planning comparisons.

Every variant starts from a deep copy of the supplied scenario. Neither the
built-in baseline nor an operator's saved catalogue is rewritten. The same
returned scenario must be supplied to every allocation policy in a comparison.
"""
from __future__ import annotations

from copy import deepcopy
from math import ceil, floor, isfinite
from typing import Mapping

from .domain import Assumption, CalendarEntry, DemandEntry, Evidence, Scenario


BUSINESS_CASE_PRESETS = {
    "BASELINE": {
        "label": "Current planning basis",
        "description": "Use the current demand, grades, inventory and resources.",
        "parameters": {},
    },
    "PEAK_DEMAND": {
        "label": "Evening demand peak",
        "description": (
            "Concentrate final delivery within the last eight hours of each "
            "day, preserving each day's requested tonnes by unit and grade."),
        "parameters": {"peak_start_hour": 16.0, "peak_multiplier": 2.0},
    },
    "RECLAIMER_OUTAGE": {
        "label": "Local reclaimer outage",
        "description": "Add a timed outage without changing engineering rates.",
        "parameters": {
            "machine_id": "RC01-A", "start_hour": 8.0, "duration_hours": 4.0},
    },
    "QUALITY_STRESS": {
        "label": "Tighter receiving quality",
        "description": (
            "Tighten selected receiving limits and make them hard. This is a "
            "sensitivity case; achievable supply is determined by the model."),
        "parameters": {
            "destination_id": "PAP-1", "bpl_increase": 2.0,
            "cd_decrease": 5.0},
    },
    "LOW_LOCAL_STOCK": {
        "label": "Low M16 starting stock",
        "description": (
            "Reduce M16 humid stock to an assumed fraction of current mass, "
            "making supply timing and grade availability more consequential."),
        "parameters": {"stock_fraction": 0.2},
    },
}


def _number(parameters: Mapping, key: str, low: float,
            high: float | None = None) -> float:
    try:
        value = float(parameters[key])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"{key} must be a finite number") from None
    if not isfinite(value) or value < low or (
            high is not None and value > high):
        interval = f"{low:g} to {high:g}" if high is not None else f"at least {low:g}"
        raise ValueError(f"{key} must be finite and {interval}")
    return value


def _peak_demand(sc: Scenario, parameters: Mapping) -> list[dict]:
    peak_start = _number(parameters, "peak_start_hour", 0, 23.999)
    multiplier = _number(parameters, "peak_multiplier", 1, 20)
    horizon = sc.sim.horizon_buckets
    by_key = {(entry.dest_id, entry.bucket): entry for entry in sc.demand}
    changes = []
    for destination in sc.destinations:
        if destination.demand_kind == "process_feed":
            continue
        # Keep uncommissioned future-unit templates intact.
        from .flow_config import destination_line_operating_state
        if destination_line_operating_state(sc, destination.id) == "FUTURE":
            continue
        groups: dict[tuple, list[tuple[int, float, str, int]]] = {}
        for bucket in range(horizon):
            grade = sc.demand_grade(destination.id, bucket)
            priority = sc.demand_priority(destination.id, bucket)
            stamp = sc.sim.bucket_dt(bucket)
            rate = float(sc.demand_tph(destination.id, bucket))
            if not isfinite(rate) or rate < 0:
                raise ValueError(f"{destination.id}: invalid demand rate")
            groups.setdefault((stamp.date(), grade, priority), []).append((bucket, rate, grade, priority))
        for (date, grade, priority), rows in groups.items():
            original = sum(rate for _, rate, _, _ in rows)
            weighted = [
                rate * (multiplier if (
                    sc.sim.bucket_dt(bucket).hour
                    + sc.sim.bucket_dt(bucket).minute / 60.0) >= peak_start else 1.0)
                for bucket, rate, _, _ in rows
            ]
            factor = original / sum(weighted) if sum(weighted) > 0 else 1.0
            for (bucket, _, _, _), value in zip(rows, weighted):
                by_key[(destination.id, bucket)] = DemandEntry(
                    destination.id, bucket, value * factor,
                    requested_grade=grade, priority=priority)
            if original > 0:
                changes.append({"entity": destination.id, "date": date.isoformat(),
                                "grade": grade, "priority": priority,
                                "quantity_t": original * sc.sim.dt_h})
    sc.demand = [entry for _, entry in sorted(by_key.items())]
    return changes


def make_business_case_scenario(
        base: Scenario, preset_id: str,
        overrides: Mapping | None = None) -> Scenario:
    """Return a copied scenario carrying a reproducible sensitivity manifest.

    Applying different cases to ``base`` never compounds their changes. To
    combine cases explicitly, pass a previously returned variant as the base;
    ``BUSINESS_CASE_HISTORY`` retains each transformation. Times for outages
    are elapsed hours from the current planning horizon start.
    """
    if preset_id not in BUSINESS_CASE_PRESETS:
        raise ValueError(f"Unknown business-case preset: {preset_id}")
    definition = BUSINESS_CASE_PRESETS[preset_id]
    parameters = deepcopy(definition["parameters"])
    overrides = dict(overrides or {})
    unknown = set(overrides) - set(parameters)
    if unknown:
        raise ValueError("Unknown scenario parameters: " + ", ".join(sorted(unknown)))
    parameters.update(overrides)
    sc = deepcopy(base)
    if sc.sim.horizon_buckets <= 0 or not isfinite(sc.sim.dt_h) or sc.sim.dt_h <= 0:
        raise ValueError("Scenario requires a positive finite planning horizon")
    changes: list[dict] = []

    if preset_id == "PEAK_DEMAND":
        changes = _peak_demand(sc, parameters)
    elif preset_id == "RECLAIMER_OUTAGE":
        machine_id = str(parameters["machine_id"])
        machines = {machine.id: machine for machine in sc.machines}
        machine = machines.get(machine_id)
        if machine is None or machine.kind != "reclaimer" or not machine.planning_enabled:
            raise ValueError("Select a registered, planner-controlled reclaimer")
        horizon_h = sc.sim.horizon_buckets * sc.sim.dt_h
        start_h = _number(parameters, "start_hour", 0, horizon_h)
        duration_h = _number(parameters, "duration_hours", 1 / 60, horizon_h)
        end_h = start_h + duration_h
        if end_h > horizon_h:
            raise ValueError("Outage must fit inside the planning horizon")
        start_minute, end_minute = int(round(start_h * 60)), int(round(end_h * 60))
        if end_minute <= start_minute:
            raise ValueError("Outage must last at least one minute")
        sc.calendars.append(CalendarEntry(
            machine_id, int(floor(start_h / sc.sim.dt_h)),
            int(ceil(end_h / sc.sim.dt_h)) - 1,
            reason="ASSUMED business-case sensitivity outage",
            start_minute=start_minute, end_minute=end_minute))
        changes.append({"entity": machine_id, "start_minute": start_minute,
                        "end_minute": end_minute})
    elif preset_id == "QUALITY_STRESS":
        destination_id = str(parameters["destination_id"])
        destination = next((item for item in sc.destinations
                            if item.id == destination_id), None)
        if destination is None or destination.demand_kind == "process_feed":
            raise ValueError("Select a registered final receiving unit")
        adjustments = {"BPL": _number(parameters, "bpl_increase", 0, 100),
                       "Cd": _number(parameters, "cd_decrease", 0)}
        for param, adjustment in adjustments.items():
            if adjustment == 0:
                continue
            spec = next((item for item in destination.spec if item.param == param), None)
            field = "pmin" if param == "BPL" else "pmax"
            before = getattr(spec, field, None)
            if before is None:
                raise ValueError(f"{destination_id} needs a configured {param} {field} limit")
            after = float(before) + adjustment * (1 if param == "BPL" else -1)
            if after < 0 or (param == "BPL" and after > 100):
                raise ValueError(f"{param} stress produces an invalid limit")
            setattr(spec, field, after)
            if (spec.pmin is not None and spec.pmax is not None
                    and spec.pmin + spec.margin > spec.pmax - spec.margin):
                raise ValueError(f"{param} stress leaves an empty permitted interval")
            spec.hard = True
            changes.append({"entity": destination_id, "parameter": param,
                            "field": field, "before": before, "after": after, "hard": True})
    elif preset_id == "LOW_LOCAL_STOCK":
        fraction = _number(parameters, "stock_fraction", 0, 1)
        stock_ids = {stock.id for stock in sc.spaces
                     if stock.site == "MPH" and stock.area == "M16-humid"}
        if not stock_ids:
            raise ValueError("Low M16 stock case requires MPH humid stockpiles")
        # Preserve reservations and safety targets. A shortage against either
        # is an outcome to report, not an input silently made easier.
        for stock in sc.spaces:
            if stock.id not in stock_ids:
                continue
            before = stock.mass_t
            stock.mass_t *= fraction
            stock.evidence = Evidence(
                source="Business-case sensitivity", status="ASSUMED", confidence="L",
                note=f"Starting mass multiplied by {fraction:g}; prior mass {before:g} t.")
            changes.append({"entity": stock.id, "before_mass_t": before,
                            "after_mass_t": stock.mass_t})
        for segment in sc.segments:
            if segment.space_id in stock_ids:
                segment.mass_t *= fraction
        for zone in sc.pile_zones:
            if zone.space_id in stock_ids:
                zone.mass_t *= fraction
        # A running construction campaign carries its own physical mass
        # ledger; reducing only its pile would make an inconsistent snapshot.
        from .blending import chevron_campaign_state
        for stock_id in stock_ids:
            if chevron_campaign_state(sc, stock_id)["status"] in {"BUILDING", "COMPLETE"}:
                raise ValueError("Apply low-stock sensitivity before starting a blend campaign")

    manifest = {"preset_id": preset_id, "label": definition["label"],
                "basis": "CURRENT INPUTS" if preset_id == "BASELINE" else "ASSUMED SENSITIVITY",
                "parameters": parameters, "changes": changes,
                "source_scenario": base.name,
                "description": definition["description"]}
    history = sc.config.get("BUSINESS_CASE_HISTORY", [])
    history = list(history) if isinstance(history, list) else []
    sc.config["BUSINESS_CASE_HISTORY"] = [*history, manifest]
    sc.config["BUSINESS_CASE_SCENARIO"] = manifest
    if preset_id != "BASELINE":
        sc.assumptions.append(Assumption(
            id=f"BC-{preset_id}-{len(history) + 1}",
            text=definition["label"], module="business-case sensitivity",
            source="Explicit synthetic scenario; not engineering evidence",
            placeholder=str(parameters), impact=definition["description"],
            action="Replace assumptions with dated operations data before investment approval"))
    return sc
