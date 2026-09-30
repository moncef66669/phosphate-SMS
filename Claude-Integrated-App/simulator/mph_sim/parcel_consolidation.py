"""Optional incumbent-preserving scheduling polish, not a minimum-load rule.

The primary solve chooses service and physical quantities. This pass may move
buffered material between eligible buckets to batch small residuals, but it
cannot change the supplied quantity/service contracts or worsen the original
objective. All original inventory, calendar, capacity and residence constraints
remain present. A failed or unhelpful polish retains the original incumbent.
"""
from __future__ import annotations

from dataclasses import dataclass
import time


@dataclass
class ConsolidationResult:
    solver: object
    attempted: bool = False
    accepted: bool = False
    status: str = "SKIPPED"
    before: int = 0
    after: int = 0
    wall_s: float = 0.0
    branches: int = 0
    conflicts: int = 0
    deterministic_time: float = 0.0
    primary_before: int = 0
    primary_after: int = 0


def _value(solver, expression) -> int:
    return int(expression) if isinstance(expression, int) else int(
        solver.value(expression))


def consolidate_parcels(model, incumbent, *, primary_expression,
                        parcel_flags, fixed_expressions, wall_s: float,
                        deterministic_s: float, seed: int,
                        accept_candidate=None, deterministic_only: bool = False):
    """Minimise short *command buckets* without purchasing lost service.

    Expressions reference original variable indices, which are unchanged by
    CpModel.clone(). No new hard parcel bound is added: a necessary short job
    remains feasible and visible. Only a strict count improvement is accepted.
    """
    from ortools.sat.python import cp_model

    flags = list(parcel_flags)
    before = sum(_value(incumbent, flag) for flag in flags)
    primary_value = _value(incumbent, primary_expression)
    result = ConsolidationResult(
        incumbent, before=before, after=before,
        primary_before=primary_value, primary_after=primary_value)
    if before == 0 or wall_s <= 0 or deterministic_s <= 0:
        return result

    started = time.monotonic()
    polished_model = model.clone()
    polished_model.clear_hints()
    # Seed a complete, valid incumbent, not the earlier greedy dispatch hint.
    for index in range(len(model.proto.variables)):
        polished_model.add_hint(
            polished_model.get_int_var_from_proto_index(index),
            _value(incumbent, model.get_int_var_from_proto_index(index)))
    fixed = list(fixed_expressions)
    fixed_values = [_value(incumbent, expression) for expression in fixed]
    for expression, value in zip(fixed, fixed_values):
        polished_model.add(expression == value)
    polished_model.add(primary_expression <= primary_value)
    polished_model.minimize(sum(flags))

    # Deterministic mode: the elapsed-time subtraction would make this pass's
    # budget - and whether it runs at all - depend on machine load.
    remaining_wall = (float(wall_s) if deterministic_only
                      else float(wall_s) - (time.monotonic() - started))
    if remaining_wall <= 0:
        result.wall_s = time.monotonic() - started
        result.status = "BUDGET_EXHAUSTED"
        return result
    result.attempted = True
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = int(seed)
    solver.parameters.max_time_in_seconds = remaining_wall
    solver.parameters.max_deterministic_time = float(deterministic_s)
    status = solver.solve(polished_model)
    result.wall_s = time.monotonic() - started
    result.status = solver.status_name(status)
    result.branches = int(solver.num_branches)
    result.conflicts = int(solver.num_conflicts)
    result.deterministic_time = float(solver.response_proto.deterministic_time)
    if status not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
        return result

    after = sum(_value(solver, flag) for flag in flags)
    # Independently check the protected contracts and objective before handing
    # the candidate to result extraction. Do not rely on solver status alone.
    protected = all(_value(solver, expression) == value
                    for expression, value in zip(fixed, fixed_values))
    if (after < before and protected
            and _value(solver, primary_expression) <= primary_value):
        rejection = accept_candidate(solver, incumbent) if accept_candidate else ""
        if rejection:
            result.status = "REJECTED/" + str(rejection)
            return result
        result.solver = solver
        result.accepted = True
        result.after = after
        result.primary_after = _value(solver, primary_expression)
    return result
