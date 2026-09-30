"""Reproducible planning comparisons with explicit experimental boundaries.

The full-scenario experiment varies *local reclaim source portfolios*.  It
retains the same global feasibility scheduler in every arm, so it does not
measure human performance or the value of cross-site information.  The
separate ``coordination_benchmark`` module implements a genuinely independent
three-SMS dispatcher on a deliberately small, auditable physical scenario.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import platform
from dataclasses import asdict, dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .domain import PlanResult, Scenario
from .kpis import compute_kpis
from .optimizer import SolveOptions, solve_plan
from .routes import RouteGraph, reclaim_candidates


SUCCESS_STATUSES = {"FEASIBLE", "OPTIMAL"}


@dataclass
class BenchmarkOptions:
    max_solve_s: float = 3.0
    local_policies: tuple = ("local_fifo", "local_quality")


@dataclass
class BenchmarkArm:
    id: str
    label: str
    plan: PlanResult
    kpis: Dict[str, object] = field(default_factory=dict)
    decisions: List[dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    physical_errors: List[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.plan.status in SUCCESS_STATUSES and not self.physical_errors


@dataclass
class BenchmarkResult:
    scenario_name: str
    input_fingerprint: str
    experiment: str
    arms: Dict[str, BenchmarkArm]
    best_local_id: str = ""
    deltas: Dict[str, object] = field(default_factory=dict)
    assumptions: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    provenance: Dict[str, object] = field(default_factory=dict)

    def to_dict(self, include_plans: bool = False) -> dict:
        result = asdict(self)
        if not include_plans:
            for arm in result["arms"].values():
                plan = arm.pop("plan")
                arm["plan_status"] = plan["status"]
                arm["job_count"] = len(plan["jobs"])
                arm["solver_wall_s"] = plan["solver_wall_s"]
        return result


def scenario_fingerprint(sc: Scenario) -> str:
    """Content fingerprint includes demand, initial state and all constraints."""
    payload = json.dumps(asdict(sc), sort_keys=True, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:20]


def benchmark_provenance(options: BenchmarkOptions) -> dict:
    """Record the implementation as well as data used in a paired run."""
    try:
        solver_version = version("ortools")
    except PackageNotFoundError:
        solver_version = "not installed"
    module_dir = Path(__file__).resolve().parent
    source_hashes = {}
    for path in sorted(module_dir.glob("*.py")):
        source_hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"benchmark_schema_version": 1,
            "evaluation_basis": "paired planned physical flows; no automatic operational forecast",
            "python_version": platform.python_version(),
            "ortools_version": solver_version,
            "solver_budget_per_window_deterministic_s": options.max_solve_s,
            "source_sha256": source_hashes}


def _number(kpis: dict, key: str, default: float = 0.0) -> float:
    value = kpis.get(key, default)
    return float(value) if value is not None else default


def finish_comparison(result: BenchmarkResult) -> BenchmarkResult:
    """Select the strongest successful local result, never the weakest arm.

    The ordering is terminal on-time tonnes, then known on-spec tonnes, then
    less holding exposure/unstacked material. Objective scores cannot rank policies.
    Signed deltas deliberately retain negative results.
    """
    locals_ = [arm for key, arm in result.arms.items()
               if key != "coordinated" and arm.valid]
    if not locals_:
        result.warnings.append("No successful local baseline; no benefit is claimed.")
        return result
    best = max(locals_, key=lambda arm: (
        _number(arm.kpis, "final_product_on_time_t"),
        _number(arm.kpis, "on_spec_mass_t"),
        -_number(arm.kpis, "receiving_queue_t_h"),
        -_number(arm.kpis, "unstacked_t"), arm.id))
    result.best_local_id = best.id
    coordinated = result.arms.get("coordinated")
    if coordinated is None or not coordinated.valid:
        result.warnings.append("Coordinated solve failed; no benefit is claimed.")
        return result
    keys = ("final_product_on_time_t", "final_product_on_spec_on_time_t", "final_product_service_pct",
            "on_spec_mass_t", "on_spec_mass_pct", "quality_unknown_t",
            "unstacked_t", "safety_stock_deficit_t_h", "travel_total_m",
            "receiving_queue_peak_t", "receiving_queue_end_t", "receiving_queue_t_h")
    result.deltas = {
        key: (_number(coordinated.kpis, key) - _number(best.kpis, key))
        for key in keys
        if key in coordinated.kpis and key in best.kpis
    }
    baseline = _number(best.kpis, "final_product_on_time_t")
    gain = result.deltas.get("final_product_on_time_t", 0.0)
    result.deltas["on_time_relative_change_pct"] = (
        100.0 * gain / baseline if baseline > 1e-9 else None)
    if gain < -1e-6:
        result.warnings.append(
            "The coordinated incumbent serves less on-time product than the "
            "best local arm at this solve budget. Retain the negative result; "
            "a time-limited feasible solve is not proof of optimality.")
    if any(arm.plan.status == "FEASIBLE" and "cp-sat" in arm.plan.solver
           for arm in result.arms.values()):
        result.warnings.append(
            "At least one result is FEASIBLE rather than proven optimal; "
            "this compares the returned plans at the stated solve budget.")
    return result


def _source_age(sc: Scenario, sid: str) -> float:
    segments = [s for s in sc.segments if s.space_id == sid and s.mass_t > 0]
    mass = sum(s.mass_t for s in segments)
    return sum(s.mass_t * s.age_days for s in segments) / mass if mass else 0.0


def _quality_score(sc: Scenario, sid: str, dest) -> tuple:
    quality = sc.quality_of(sid)
    missing = violations = 0
    deviation = 0.0
    for spec in dest.spec:
        if spec.param not in quality:
            missing += 1
            continue
        value = quality[spec.param]
        low = spec.pmin + spec.margin if spec.pmin is not None else None
        high = spec.pmax - spec.margin if spec.pmax is not None else None
        violation = max((low - value) if low is not None else 0,
                        (value - high) if high is not None else 0, 0)
        violations += int(violation > 1e-9)
        scale = max(abs(high - low), 1.0) if low is not None and high is not None else 1.0
        deviation += violation / scale
        if spec.target is not None:
            deviation += abs(value - spec.target) / scale
    return missing, violations, deviation


def local_source_commitments(sc: Scenario, policy: str) -> tuple:
    """Select source portfolios using only each destination's own SMS data.

    Every policy understands local quality limits, local demand priorities,
    initial usable stock and machinery reach. FIFO age falls back to distance
    when segment ages are absent. If no individual source is on-spec, or known
    initial stock is insufficient, all local alternatives remain available to
    the common scheduler; missing information never invents a doomed choice.
    Blending/process/reception and cross-site dispatch remain unrestricted.
    """
    if policy not in ("local_fifo", "local_quality"):
        raise ValueError(f"Unknown local source policy: {policy}")
    candidates = reclaim_candidates(sc, RouteGraph.from_scenario(sc))
    by_dest: Dict[str, dict] = {}
    for mid, sid, did, path in candidates:
        machine, space, dest = sc.machine(mid), sc.space(sid), sc.destination(did)
        if machine.site == space.site == dest.site:
            by_dest.setdefault(did, {}).setdefault(sid, []).append((mid, path))
    # Reserve stock separately within each SMS; remote demand never enters
    # either the order or ranking of that site's decisions.
    reserved: Dict[str, float] = {}
    decisions, banned = [], []
    destinations = sorted(sc.destinations, key=lambda d: (d.site, -d.priority, d.id))
    for dest in destinations:
        sources = by_dest.get(dest.id, {})
        demand_t = sum(max(sc.demand_tph(dest.id, t), 0.0) * sc.sim.dt_h
                       for t in range(sc.sim.horizon_buckets))
        if not sources or demand_t <= 0:
            continue
        def distance(sid):
            pile = sc.space(sid)
            centre = (pile.x_from + pile.x_to) / 2
            return min(abs(sc.machine(mid).position_x - centre)
                       for mid, _ in sources[sid])
        def usable(sid):
            pile = sc.space(sid)
            return max(pile.mass_t - pile.safety_stock_t - reserved.get(sid, 0), 0)
        def rank(sid):
            q = _quality_score(sc, sid, dest)
            common = (q[0], q[1])
            if policy == "local_quality":
                return common + (q[2], distance(sid), -_source_age(sc, sid), sid)
            return common + (-_source_age(sc, sid), distance(sid), q[2], sid)
        ranked = sorted(sources, key=rank)
        individually_compliant = [sid for sid in ranked
                                  if _quality_score(sc, sid, dest)[:2] == (0, 0)]
        # A dated grade change can need a different source later. This
        # conservative portfolio experiment retains those alternatives rather
        # than pretending a snapshot ranking is a campaign optimizer.
        demand_grades = {sc.demand_grade(dest.id, t)
                         for t in range(sc.sim.horizon_buckets)
                         if sc.demand_tph(dest.id, t) > 0}
        reason = "Local source priority using available starting stock"
        selected, remaining = [], demand_t
        if len(demand_grades) > 1 or not individually_compliant or \
                sum(usable(sid) for sid in individually_compliant) < demand_t:
            selected = ranked
            reason = ("Retained all sources: blend, replenishment or dated-grade "
                      "flexibility is needed; no local snapshot restriction")
        else:
            for sid in individually_compliant:
                amount = min(usable(sid), remaining)
                if amount <= 0:
                    continue
                selected.append(sid)
                reserved[sid] = reserved.get(sid, 0.0) + amount
                remaining -= amount
                if remaining <= 1e-6:
                    break
        # Existing human commitments have precedence in every experiment.
        selected = list(dict.fromkeys(selected + [
            fixed.space for fixed in sc.fixed_jobs
            if fixed.dest_id == dest.id and fixed.space in sources]))
        for sid in ranked:
            if sid not in selected:
                for mid, _ in sources[sid]:
                    banned.append((mid, sid, dest.id, 0, sc.sim.horizon_buckets - 1))
        decisions.append({"site": dest.site, "destination": dest.id,
                          "demand_t": demand_t, "source_priority": ranked,
                          "selected_sources": selected, "reason": reason})
    return sorted(set(banned)), decisions


def run_benchmark(sc: Scenario, options: Optional[BenchmarkOptions] = None,
                  progress_cb: Optional[Callable[[str], None]] = None) -> BenchmarkResult:
    """Full current-scenario source-allocation sensitivity; no state mutation."""
    options = options or BenchmarkOptions()
    if not math.isfinite(options.max_solve_s) or options.max_solve_s <= 0:
        raise ValueError("max_solve_s must be finite and positive")
    snapshot = copy.deepcopy(sc)
    result = BenchmarkResult(
        snapshot.name, scenario_fingerprint(snapshot), "local_source_portfolios", {},
        assumptions=[
            "All arms use identical demand, initial inventory, grade data, calendars, "
            "physical constraints, objective weights and deterministic solve budget.",
            "Local rules choose terminal reclaim source portfolios using only the "
            "destination SMS's inventory, ages, quality, demand and machine positions.",
            "The same global CP-SAT scheduler chooses timing, process flows, "
            "cross-site dispatch and blending for every arm. This is an optimistic "
            "source-policy baseline, not a blinded-operator simulation.",
            "Results are planned physical tonnes, not measured plant performance. "
            "Internal washing/process transfers are excluded from terminal benefit.",
            f"Solve budget per solve/window: {options.max_solve_s:g} deterministic seconds. "
            "Weekly rolling and mixed-resolution windows retain their scenario settings."])
    result.provenance = benchmark_provenance(options)
    labels = {"local_fifo": "Local FIFO / nearest-source portfolio",
              "local_quality": "Local quality-aware source portfolio",
              "coordinated": "Coordinated model"}
    for policy in tuple(options.local_policies) + ("coordinated",):
        if progress_cb:
            progress_cb(labels.get(policy, policy))
        banned, decisions = ([], []) if policy == "coordinated" else \
            local_source_commitments(snapshot, policy)
        plan = solve_plan(copy.deepcopy(snapshot), SolveOptions(
            banned=banned, max_time_s=options.max_solve_s))
        kpis = compute_kpis(snapshot, plan) if plan.status in SUCCESS_STATUSES else {}
        kpis["unstacked_t"] = sum(plan.surge_unstacked)
        timing_error_count = int(plan.breakdown.get("minute_timing_errors", 0) or 0)
        result.arms[policy] = BenchmarkArm(
            policy, labels[policy], plan, kpis, decisions,
            warnings=[] if plan.status in SUCCESS_STATUSES else list(plan.reasons),
            physical_errors=[f"Execution timing incomplete: {timing_error_count} errors"]
            if timing_error_count else [])
    return finish_comparison(result)


def value_sensitivity(result: BenchmarkResult, contribution_per_t: float,
                      annual_periods: float = 0.0, annual_cost: float = 0.0,
                      implementation_cost: float = 0.0) -> dict:
    """User-entered break-even arithmetic, never a claimed economic forecast.

    Contribution must already be net of marginal production/transport cost.
    No grade-value placeholders, throughput double counting, or automatic
    extrapolation of a favourable demonstration day are used.
    """
    values = (contribution_per_t, annual_periods, annual_cost, implementation_cost)
    if any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("Financial sensitivity inputs must be finite and non-negative")
    if not result.deltas or not result.best_local_id:
        return {"available": False, "reason": "A successful paired comparison is required"}
    if "final_product_on_spec_on_time_t" not in result.deltas:
        return {"available": False, "reason":
                "Verified on-spec, on-time terminal tonnes are required for valuation"}
    tonnes = float(result.deltas["final_product_on_spec_on_time_t"])
    per_period = tonnes * contribution_per_t
    annual_gross = per_period * annual_periods
    annual_net = annual_gross - annual_cost
    return {"available": True, "incremental_on_time_t": tonnes,
            "incremental_on_spec_on_time_t": tonnes,
            "contribution_per_period": per_period,
            "annual_periods_assumed": annual_periods,
            "annual_contribution": annual_gross,
            "annual_net_benefit": annual_net,
            "simple_payback_years": implementation_cost / annual_net
            if annual_net > 0 else None,
            "break_even_contribution_per_t":
            (annual_cost + implementation_cost) / (tonnes * annual_periods)
            if tonnes > 0 and annual_periods > 0 else None,
            "basis": "User-assumed marginal contribution on incremental verified on-spec, "
                     "on-time terminal tonnes; first-year break-even includes implementation. "
                     "Illustrative sensitivity, not a validated financial forecast."}
