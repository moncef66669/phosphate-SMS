"""Machine-positioning estimates shared by planning and operator views.

Positioning is equipment activity, not material movement.  It therefore has
no tonnes, grade or conveyor path.  The current demonstration inputs use
schematic metric coordinates plus ``Machine.travel_speed_m_min`` and
``Machine.setup_min``; every result from this module is an estimate until
surveyed rail coordinates, operating speeds and a live machine position are
provided.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .domain import Machine, Scenario


POSITIONING_BASIS = "ESTIMATED_FROM_ASSUMED_TRAVEL_INPUTS"


@dataclass(frozen=True)
class TransitionEstimate:
    """Non-material preparation required between two machine work locations."""

    distance_m: float
    translation_min: int
    setup_min: int
    total_min: int
    basis: str = POSITIONING_BASIS


def _whole_minutes(value: float) -> int:
    return max(int(math.ceil(float(value) - 1e-12)), 0)


def space_midpoint_x(sc: Scenario, space_id: str) -> float:
    space = sc.space(space_id)
    return (float(space.x_from) + float(space.x_to)) / 2.0


def work_midpoint_x(
        sc: Scenario, space_id: str, start_x_m: float = 0.0,
        stop_x_m: float = 0.0) -> float:
    """Best available work location for a planned production interval."""
    start = float(start_x_m or 0.0)
    stop = float(stop_x_m or 0.0)
    if stop > start:
        return (start + stop) / 2.0
    return space_midpoint_x(sc, space_id)


def estimate_transition(
        machine: Machine, from_x_m: float, to_x_m: float,
        *, space_changed: bool) -> TransitionEstimate:
    """Estimate translation and setup without creating a material job.

    Setup/changeover applies when the machine changes work spaces.  Same-space
    face progression is treated as part of the production job because the
    model does not contain travel direction or a final boom/gantry endpoint.
    """
    if not space_changed:
        return TransitionEstimate(0.0, 0, 0, 0)
    distance = abs(float(to_x_m) - float(from_x_m))
    translation = _whole_minutes(
        distance / max(float(machine.travel_speed_m_min), 1e-9))
    setup = _whole_minutes(max(float(machine.setup_min), 0.0))
    return TransitionEstimate(
        distance_m=distance,
        translation_min=translation,
        setup_min=setup,
        total_min=translation + setup)


def estimate_space_transition(
        sc: Scenario, machine_id: str, from_space: str,
        to_space: str) -> TransitionEstimate:
    machine = sc.machine(machine_id)
    return estimate_transition(
        machine,
        space_midpoint_x(sc, from_space),
        space_midpoint_x(sc, to_space),
        space_changed=from_space != to_space)

