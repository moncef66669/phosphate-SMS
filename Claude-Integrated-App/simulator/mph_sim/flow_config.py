"""Small, model-facing flow-configuration helpers.

Process-line state is deliberately separate from mobile-machine availability
and conveyor availability.  A washing-line card owns its complete campaign
chain, while shared mobile machines and conveyor trunks remain independent
assets.

Configured targets are never overwritten when a line is disabled.  The
helpers below expose the *effective* targets seen by the planner so the saved
values are available again when the line is re-enabled.
"""
from __future__ import annotations

from typing import Dict, Set, Tuple

from .domain import (DESTINATION_LINE_STATE_CONFIG, DESTINATION_LINE_STATES,
                     Scenario)


PROCESS_LINE_ENABLED_CONFIG = "PROCESS_LINE_ENABLED"
PROCESS_LINE_STATES = ("AVAILABLE", "DISABLED", "FUTURE")

# Stable UI/model line ids -> process stages controlled by that physical line.
PROCESS_LINE_STAGE_MAP: Dict[str, Tuple[str, ...]] = {
    "LT-WASH-L1": ("LOU-WASH-L1", "LOU-PRODUCT-STACK-L1"),
    "LT-WASH-L2": ("LOU-WASH-L2", "LOU-PRODUCT-STACK-L2"),
    "BG-WASH-L1": (
        "BEN-MINE-STACK-L1", "BEN-MINE-RECLAIM-L1",
        "BEN-RAW-STACK-L1", "BEN-WASH-L1",
        "BEN-PRODUCT-STACK-L1",
    ),
    "BG-WASH-L2": (
        "BEN-MINE-STACK-L2", "BEN-MINE-RECLAIM-L2",
        "BEN-RAW-STACK-L2", "BEN-WASH-L2",
        "BEN-PRODUCT-STACK-L2",
    ),
    "BG-WASH-L3": (
        "BEN-MINE-STACK-L3", "BEN-MINE-RECLAIM-L3",
        "BEN-RAW-STACK-L3", "BEN-WASH-L3",
        "BEN-PRODUCT-STACK-L3",
    ),
}

_LOUTA_WASH_OUTPUT_STAGES = ("LOU-WASH-L1", "LOU-WASH-L2")
_STAGE_TO_LINE = {
    stage: line_id
    for line_id, stages in PROCESS_LINE_STAGE_MAP.items()
    for stage in stages
}


def process_line_modelled(line_id: str) -> bool:
    """Whether ``line_id`` has an executable process model."""
    return line_id in PROCESS_LINE_STAGE_MAP


def _configured_line_states(sc: Scenario) -> dict:
    raw = sc.config.get(PROCESS_LINE_ENABLED_CONFIG, {})
    return raw if isinstance(raw, dict) else {}


def disabled_process_lines(sc: Scenario) -> Set[str]:
    """Return explicitly planning-disabled, model-backed process lines.

    Missing entries mean enabled, preserving backward compatibility for every
    existing scenario and imported JSON snapshot.
    """
    return {
        line_id for line_id in PROCESS_LINE_STAGE_MAP
        if process_line_operating_state(sc, line_id) != "AVAILABLE"
    }


def process_line_operating_state(sc: Scenario, line_id: str) -> str:
    """Return Available, Disabled or Future for a process line.

    Legacy boolean values remain supported: ``True`` means Available and
    ``False`` means Disabled. Missing entries default to Available.
    """
    if not process_line_modelled(line_id):
        raise ValueError(f"process line '{line_id}' has no executable model")
    value = _configured_line_states(sc).get(line_id, True)
    if isinstance(value, bool):
        return "AVAILABLE" if value else "DISABLED"
    normalized = str(value).strip().upper()
    return normalized if normalized in PROCESS_LINE_STATES else "AVAILABLE"


def set_process_line_operating_state(
        sc: Scenario, line_id: str, state: str) -> None:
    """Persist a three-state process-line lifecycle without changing demand."""
    normalized = str(state).strip().upper()
    if normalized not in PROCESS_LINE_STATES:
        raise ValueError(
            "process-line state must be AVAILABLE, DISABLED or FUTURE")
    if not process_line_modelled(line_id):
        raise ValueError(f"process line '{line_id}' has no executable model")
    raw = sc.config.get(PROCESS_LINE_ENABLED_CONFIG, {})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(f"{PROCESS_LINE_ENABLED_CONFIG} must be an object")
    states = dict(raw)
    # Preserve the established boolean persistence contract for current
    # assets; only Future needs the additional lifecycle value.
    states[line_id] = (
        True if normalized == "AVAILABLE"
        else False if normalized == "DISABLED"
        else "FUTURE"
    )
    sc.config[PROCESS_LINE_ENABLED_CONFIG] = states


def set_process_line_enabled(
        sc: Scenario, line_id: str, enabled: bool) -> None:
    """Persist one model-backed line's planning state without changing targets.

    Raises ``ValueError`` for unknown/unmodelled lines and malformed
    configuration instead of silently accepting a display-only card.
    """
    if not process_line_modelled(line_id):
        raise ValueError(
            f"process line '{line_id}' has no executable model")
    if not isinstance(enabled, bool):
        raise ValueError("process-line enabled state must be boolean")
    set_process_line_operating_state(
        sc, line_id, "AVAILABLE" if enabled else "DISABLED")


def destination_line_link_ids(
        sc: Scenario, destination_id: str) -> Tuple[str, ...]:
    """Return the route legs that enter one final receiving line directly.

    A destination-line switch operates on the real route graph. It does not
    alter the destination, its requested grade, or any demand entries.
    """
    destination = sc.destination(destination_id)
    return tuple(
        link.id for link in sc.links
        if link.dst == destination.node_id
    )


def destination_line_operating_state(
        sc: Scenario, destination_id: str) -> str:
    """Return the lifecycle state of one final receiving line.

    ``AVAILABLE`` means its route legs are selected planning assets. ``FUTURE``
    keeps the destination and demand template in the scenario, but
    :meth:`Scenario.demand_tph` exposes zero active demand and the route legs
    remain unavailable to the planner.

    Missing configuration is inferred from the route lifecycle so older JSON
    snapshots remain backward compatible.
    """
    link_ids = destination_line_link_ids(sc, destination_id)
    raw = sc.config.get(DESTINATION_LINE_STATE_CONFIG, {})
    if isinstance(raw, dict):
        configured = str(raw.get(destination_id, "")).strip().upper()
        if configured in DESTINATION_LINE_STATES:
            return configured
    if link_ids and all(
            str(sc.link(link_id).lifecycle).upper() == "FUTURE"
            for link_id in link_ids):
        return "FUTURE"
    if link_ids and not any(sc.link(link_id).available for link_id in link_ids):
        return "DISABLED"
    return "AVAILABLE"


def set_destination_line_operating_state(
        sc: Scenario, destination_id: str, state: str) -> None:
    """Set a receiving line to Available, Disabled or Future.

    The transition is atomic across every direct route leg. It deliberately
    does not overwrite the destination's configured demand or per-bucket
    profile: Future demand is filtered at read time and becomes active again
    when the line is selected as Available.
    """
    normalized = str(state).strip().upper()
    if normalized not in DESTINATION_LINE_STATES:
        raise ValueError(
            "destination-line state must be AVAILABLE, DISABLED or FUTURE")
    link_ids = destination_line_link_ids(sc, destination_id)
    if not link_ids:
        raise ValueError(
            f"destination line '{destination_id}' has no executable route")

    raw = sc.config.get(DESTINATION_LINE_STATE_CONFIG, {})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"{DESTINATION_LINE_STATE_CONFIG} must be an object")

    available = normalized == "AVAILABLE"
    lifecycle = "FUTURE" if normalized == "FUTURE" else "SELECTED"
    for link_id in link_ids:
        link = sc.link(link_id)
        link.available = available
        link.lifecycle = lifecycle

    states = dict(raw)
    states[destination_id] = normalized
    sc.config[DESTINATION_LINE_STATE_CONFIG] = states


def destination_line_status(sc: Scenario, destination_id: str) -> str:
    """Return lifecycle/availability status for one receiving line."""
    link_ids = destination_line_link_ids(sc, destination_id)
    if not link_ids:
        return "UNMODELLED"
    if destination_line_operating_state(sc, destination_id) == "FUTURE":
        return "FUTURE"
    available = [bool(sc.link(link_id).available) for link_id in link_ids]
    if all(available):
        return "ENABLED"
    if any(available):
        return "PARTIAL"
    return "DISABLED"


def set_destination_line_enabled(
        sc: Scenario, destination_id: str, enabled: bool) -> None:
    """Switch every direct route leg to a destination, retaining its demand."""
    if not isinstance(enabled, bool):
        raise ValueError("destination-line enabled state must be boolean")
    if destination_line_operating_state(sc, destination_id) == "FUTURE":
        raise ValueError(
            f"destination line '{destination_id}' is Future; select it as "
            "Available before changing operational availability")
    set_destination_line_operating_state(
        sc, destination_id, "AVAILABLE" if enabled else "DISABLED")


def process_stage_enabled(sc: Scenario, stage: str) -> bool:
    """Whether a process stage is enabled by its owning model-backed line."""
    line_id = _STAGE_TO_LINE.get(stage)
    return line_id not in disabled_process_lines(sc) if line_id else True


def effective_process_stage_targets(sc: Scenario) -> dict:
    """Configured process targets with disabled-line stages removed."""
    raw = sc.config.get("PROCESS_STAGE_OUTPUT_TARGET_T", {})
    if not isinstance(raw, dict):
        return {}
    return {
        stage: target for stage, target in raw.items()
        if process_stage_enabled(sc, str(stage))
    }


def effective_corridor_targets(sc: Scenario) -> dict:
    """Return corridor targets after applying Louta process-line state.

    When either Louta line is disabled, effective C1 tonnage is the sum of the
    still-active washing-output targets.  The configured C1 and stage targets
    remain untouched, so re-enabling restores the prior plan inputs.
    """
    raw = sc.config.get("CORRIDOR_DISPATCH_TARGET_T", {})
    targets = dict(raw) if isinstance(raw, dict) else {}
    if disabled_process_lines(sc):
        stage_targets = effective_process_stage_targets(sc)
        targets["C1"] = sum(
            float(stage_targets.get(stage, 0.0) or 0.0)
            for stage in _LOUTA_WASH_OUTPUT_STAGES
        )
    return targets
