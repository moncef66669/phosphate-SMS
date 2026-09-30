"""Bounded, incumbent-protecting objective passes and job-volume constraints.

All masses are whole physical tonnes. Higher-priority incumbent values are
protected explicitly; FEASIBLE passes are not certificates of optimality.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import time


@dataclass
class ObjectivePasses:
    solver: object
    records: list = field(default_factory=list)
    branches: int = 0
    conflicts: int = 0
    deterministic_time: float = 0.0


def value(solver, expression):
    return int(expression) if isinstance(expression, int) else int(solver.value(expression))


def refine_objectives(model, incumbent, *, levels, wall_s, deterministic_s,
                      seed, accept_candidate=None, initial_protection=(),
                      repair_constraints=None, accept_equal=False,
                      level_acceptance=None, deterministic_only=False):
    """Improve later goals without degrading ANY earlier incumbent goal.

    levels includes the already-solved first goal. Candidate extraction and
    minute validation happen before acceptance when a callback is supplied.
    Failure, timeout, or rejection leaves the last accepted incumbent intact.

    ``deterministic_only`` removes wall clock from every decision that can
    change the returned plan: which levels run, how long each pass gets, and
    how many retries it makes are then functions of the deterministic budget
    alone. Wall time survives only as a hang guard. Without it, an idle machine
    completes more refinement levels than a loaded one and silently returns a
    different plan - the dominant cause of non-reproducible solves.
    """
    from ortools.sat.python import cp_model

    result = ObjectivePasses(incumbent)
    started = time.monotonic()
    protected = list(initial_protection)
    remaining_deterministic = float(deterministic_s)
    for index, (name, expression, allowance) in enumerate(levels):
        previous = value(result.solver, expression)
        if index == 0:
            protected.append((expression, previous + int(allowance)))
            continue
        remaining = (float("inf") if deterministic_only
                     else float(wall_s) - (time.monotonic() - started))
        record = dict(name=name, before=previous, after=previous,
                      accepted=False, status="BUDGET_EXHAUSTED")
        result.records.append(record)
        if remaining <= 0 or remaining_deterministic <= 0:
            break
        candidate_model = model.clone()
        candidate_model.clear_hints()
        for var_index in range(len(model.proto.variables)):
            variable = model.get_int_var_from_proto_index(var_index)
            candidate_model.add_hint(
                candidate_model.get_int_var_from_proto_index(var_index),
                value(result.solver, variable))
        for goal, ceiling in protected:
            candidate_model.add(goal <= ceiling)
        candidate_model.minimize(expression)
        solver = cp_model.CpSolver()
        solver.parameters.num_workers = 1
        solver.parameters.random_seed = int(seed)
        passes_left = max(len(levels) - index, 1)
        # A flat guard in deterministic mode: a pass must never be cut short at
        # a load-dependent point, only at its deterministic allowance.
        solver.parameters.max_time_in_seconds = (
            max(float(wall_s), 0.001) if deterministic_only else
            max((float(wall_s) - (time.monotonic() - started)) / passes_left,
                0.001))
        solver.parameters.max_deterministic_time = max(
            remaining_deterministic / passes_left, 0.001)
        try:
            pass_deadline = time.monotonic() + solver.parameters.max_time_in_seconds
            for retry in range(3):
                if deterministic_only:
                    remaining_pass = float(wall_s)
                else:
                    remaining_pass = min(
                        pass_deadline - time.monotonic(),
                        float(wall_s) - (time.monotonic() - started))
                if remaining_pass <= 0 or remaining_deterministic <= 0:
                    break
                solver.parameters.max_time_in_seconds = max(remaining_pass, .001)
                solver.parameters.max_deterministic_time = max(remaining_deterministic / passes_left, .001)
                status = solver.solve(candidate_model)
                record["status"] = solver.status_name(status)
                result.branches += int(solver.num_branches)
                result.conflicts += int(solver.num_conflicts)
                spent = float(solver.response_proto.deterministic_time)
                result.deterministic_time += spent
                remaining_deterministic -= spent
                if status not in (cp_model.FEASIBLE, cp_model.OPTIMAL):
                    break
                after = value(solver, expression)
                contracts_ok = all(value(solver, goal) <= ceiling
                                   for goal, ceiling in protected)
                if contracts_ok and (after < previous or accept_equal):
                    acceptance = (level_acceptance or {}).get(name, accept_candidate)
                    reason = acceptance(solver, result.solver) if acceptance else ""
                    if reason:
                        record["status"] = "REJECTED/" + str(reason)
                        if (reason == "MINUTE_EXECUTION" and repair_constraints
                                and retry < 2 and repair_constraints(candidate_model, solver)):
                            record["repair_retries"] = retry + 1
                            candidate_model.clear_hints()
                            solver.parameters.repair_hint = True
                            continue
                    else:
                        result.solver = solver
                        record.update(accepted=True, after=after)
                break
        except Exception as exc:
            record["status"] = "ERROR/" + type(exc).__name__
        protected.append((expression, value(result.solver, expression) + int(allowance)))
    return result


def add_minimum_job_volume(model, *, groups, activity, flows, horizon,
                           minimum_t, signatures=None):
    """Hard minimum on complete contiguous jobs, NOT individual buckets.

    Signatures identify extraction splits (derates and operator-fixed jobs).
    The requested minimum is never clipped to one bucket's capacity.
    """
    minimum = int(math.ceil(float(minimum_t)))
    if minimum <= 0:
        return 0
    signatures = signatures or {}
    count = 0
    for group_index, (group, members) in enumerate(groups.items()):
        upper = sum(max(int(flows[(ci, t)].proto.domain[len(flows[(ci, t)].proto.domain) - 1]), 0)
                    for ci in members for t in range(horizon))
        previous = 0
        for t in range(horizon):
            active = activity[(group, t)]
            total = sum(flows[(ci, t)] for ci in members)
            accumulated = model.new_int_var(0, upper, f"job_volume_{group_index}_{t}")
            boundary = t == 0 or signatures.get((group, t)) != signatures.get((group, t - 1))
            if boundary:
                model.add(accumulated == total)
            else:
                model.add(accumulated == previous + total).only_enforce_if(
                    [active, activity[(group, t - 1)]])
                model.add(accumulated == total).only_enforce_if(activity[(group, t - 1)].Not())
                model.add(accumulated == 0).only_enforce_if(active.Not())
            ending = t == horizon - 1 or signatures.get((group, t)) != signatures.get((group, t + 1))
            if ending:
                model.add(accumulated >= minimum).only_enforce_if(active)
            else:
                model.add(accumulated >= minimum).only_enforce_if(
                    [active, activity[(group, t + 1)].Not()])
            previous = accumulated
            count += 1
    return count
