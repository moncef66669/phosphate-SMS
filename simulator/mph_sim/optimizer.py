"""Planning / optimisation engine — layer 4.

Bucketised MILP on CP-SAT (OR-Tools), implementing report §15.1–15.4:
  * mass flows x[machine, space, dest, path, t] are the primary variables
    (blend fractions are derived AFTER solving — never independent vars);
  * one activity boolean per (machine, space, destination) GROUP — a machine
    works one job at a time, but its stream may split over parallel route
    legs (e.g. the two H01–H04 conveyors to PAP);
  * inventory mass balance with Δt, initial + terminal conditions (eq. 1);
  * exact demand balance `delivered + unmet = demand` with dominant w1
    (eq. 4), preventing zero-demand or surplus delivery;
  * per-bucket mass-weighted quality constraints, hard or soft (eq. 3);
  * corridor transport delay with initial belt profile (eq. 5);
  * machine exclusivity, reachability, work calendars, travel/setup gaps,
    min-stable rates, route-link capacities, shared/conflict groups,
    stockpile capacity, safety stock (soft), move/start-stop penalties,
    operator-fixed jobs, banned tuples (operator rejections).

Integer scaling: masses in whole tonnes / bucket; quality unit ×100.
The engine is UI-agnostic: Scenario in -> PlanResult out.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .coordinates import (format_repere_range, repere_interval_to_x)
from .domain import FixedJob, Job, JobBucketFlow, PlanResult, Scenario
from .flow_config import (effective_corridor_targets,
                          effective_process_stage_targets,
                          process_stage_enabled)
from .movement import estimate_space_transition
from .routes import (RouteGraph, Path, reclaim_candidates, stack_candidates,
                     space_node)
from .timing import refine_execution_timing

QSCALE = 100          # quality unit scale (BPL 71.0 -> 7100)
WSCALE = 10           # objective weight scale to integers
BIG_PENALTY = 10 ** 5  # emergency slacks (spill / unstacked arrivals) — must
#                        dominate w1·WSCALE (default 10^4/t) without exploding
#                        the objective bound
QSLACK_MAX = 10 ** 8   # tight domains matter for CP-SAT propagation


@dataclass
class Cand:
    """One allocatable (machine, space, destination-or-corridor, path) tuple."""
    kind: str                 # reclaim | stack | dispatch
    machine: str
    space: str
    dest_id: str = ""         # destination id (reclaim) / corridor link id (dispatch)
    grade: str = ""           # stacking grade (stack cands are per grade)
    path: Optional[Path] = None
    exit_node: str = ""       # dispatch: corridor exit (arrival) node
    transfer_id: str = ""      # explicit Scenario.process_transfers row
    source_space: str = ""
    target_space: str = ""
    operation_kind: str = ""   # stack | reclaim for process transfers
    output_grade: str = ""
    yield_factor: float = 1.0
    process_stage: str = ""
    residence_min: int = 0
    depends_on_stage: str = ""
    dependency_delay_min: int = 0
    purpose: str = ""

    def gkey(self) -> Tuple:
        return (self.kind, self.machine, self.space, self.dest_id,
                self.grade, self.exit_node, self.transfer_id)

    def input_space(self) -> str:
        if self.kind == "process":
            return self.source_space
        return self.space if self.kind in ("reclaim", "dispatch") else ""

    def output_space(self) -> str:
        if self.kind == "process":
            return self.target_space
        return self.space if self.kind == "stack" else ""

    def display_kind(self) -> str:
        return self.operation_kind if self.kind == "process" else self.kind


@dataclass
class SolveOptions:
    horizon_offset: int = 0                    # first bucket index (for replans)
    banned: List[Tuple[str, str, str, int, int]] = field(default_factory=list)
    # (machine, space, dest_id, from_bucket, to_bucket) tuples forced to zero
    machine_rate_factor: Dict[str, float] = field(default_factory=dict)
    max_time_s: Optional[float] = None
    log: bool = False


def _louta_policy_mass_error(sc: Scenario) -> str:
    """Reject a fixed-terminal S0 policy that must spill internal output."""
    if not bool(sc.config.get("LOU_S0_STOCKED_ROUTE_ENABLED", False)):
        return ""
    corridor = effective_corridor_targets(sc)
    stages = effective_process_stage_targets(sc)
    try:
        c1_t = float(corridor.get("C1", 0.0) or 0.0)
        wash_t = sum(float(stages.get(stage, 0.0) or 0.0)
                     for stage in ("LOU-WASH-L1", "LOU-WASH-L2"))
    except (TypeError, ValueError):
        return "Louta stocked-route targets must be numeric."
    if abs(c1_t - wash_t) > 1e-6:
        return (
            f"Louta stocked-route mass mismatch: C1 is {c1_t:g} t but "
            f"L1 + L2 washing output is {wash_t:g} t.")
    return ""


def _fixed_matches_group(sc: Scenario, fj: FixedJob, gk: Tuple) -> bool:
    """Whether an operator-fixed instruction selects this candidate group."""
    if gk[0] != fj.kind or gk[1] != fj.machine or gk[2] != fj.space:
        return False
    if fj.dest_id:
        if gk[3] != fj.dest_id:
            return False
    elif gk[0] != "stack" and gk[3]:
        return False
    if fj.grade:
        group_grade = gk[4] or sc.space(gk[2]).grade
        if group_grade != fj.grade:
            return False
    return True


def _fixed_for_bucket(sc: Scenario, gk: Tuple,
                      absolute_bucket: int) -> Optional[FixedJob]:
    """Return the deterministic fixed instruction active for a group/bucket."""
    return next((
        fj for fj in sc.fixed_jobs
        if _fixed_matches_group(sc, fj, gk)
        and fj.start_bucket <= absolute_bucket <= fj.end_bucket
    ), None)


def build_candidates(sc: Scenario, graph: RouteGraph) -> Tuple[List[Cand], Dict[str, List[Cand]]]:
    """All candidate tuples + stacking candidates grouped by corridor exit node."""
    cands: List[Cand] = []
    for (m, s, d, p) in reclaim_candidates(sc, graph):
        cands.append(Cand(
            "reclaim", m, s, dest_id=d, path=p,
            purpose=f"Serve configured destination demand at {d}"))

    # Corridor dispatch: any reclaimer whose space has a path to a delayed
    # link's source, except through an internal process-transfer leg. Without
    # this boundary an upstream ore reclaimer could incorrectly "dispatch"
    # through the washing and product-stacking graph as if those transformations
    # were ordinary conveyors.
    process_only_links = {
        link_id
        for transfer in (getattr(sc, "process_transfers", []) or [])
        for link_id in transfer.path
    }
    delayed = [l for l in sc.links if l.transit_buckets > 0]
    corridor_targets = effective_corridor_targets(sc)
    for l in delayed:
        for m in sc.machines:
            if m.kind != "reclaimer" or not m.available or \
                    not getattr(m, "planning_enabled", True):
                continue
            for sid in m.reachable_spaces:
                try:
                    sp = sc.space(sid)
                except StopIteration:
                    continue
                p = graph.best_path(space_node(sc, sid), l.src)
                if p is not None and not process_only_links.intersection(p.links):
                    full = Path(links=p.links + [l.id],
                                transit_buckets=p.transit_buckets + l.transit_buckets,
                                capacity_tph=min(p.capacity_tph, l.capacity_tph))
                    target = float(corridor_targets.get(l.id, 0.0) or 0.0)
                    purpose = (
                        f"Meet configured {l.id} corridor allocation "
                        f"({target:g} t)"
                        if target > 0 else
                        f"Replenish downstream inventory through {l.id}"
                    )
                    cands.append(Cand(
                        "dispatch", m.id, sid, dest_id=l.id,
                        grade=sp.grade, path=full, exit_node=l.dst,
                        purpose=purpose))

    # stacking of arrivals at each corridor exit node, per grade
    stack_by_exit: Dict[str, List[Cand]] = {}
    grades = [g.id for g in sc.grades] or [""]
    for l in delayed:
        sc_cands = stack_candidates(sc, graph, l.dst)
        lst = []
        for (m, sid, p) in sc_cands:
            sp = sc.space(sid)
            for g in grades:
                # anti-contamination: stack g only if space designated for g,
                # currently holds g, or is empty & undesignated (snapshot rule)
                allowed = (sp.designated_grade == g or
                           (sp.designated_grade == "" and (sp.mass_t <= 1 or sp.grade == g)))
                if allowed:
                    lst.append(Cand(
                        "stack", m, sid, grade=g, path=p, exit_node=l.dst,
                        purpose="Place required corridor arrivals in receiving stock"))
        stack_by_exit[l.dst] = lst
        cands.extend(lst)

    # Explicit internal material transfers.  These rows are the only way a
    # solver machine may move material between two storage/process spaces;
    # geometry proximity never creates a candidate.
    for transfer in getattr(sc, "process_transfers", []) or []:
        if not process_stage_enabled(sc, transfer.process_stage) or (
                transfer.depends_on_stage
                and not process_stage_enabled(sc, transfer.depends_on_stage)):
            continue
        try:
            machine = sc.machine(transfer.machine)
            source = sc.space(transfer.source_space)
            target = sc.space(transfer.target_space)
            links = [sc.link(link_id) for link_id in transfer.path]
        except StopIteration:
            continue
        if not machine.available or not getattr(
                machine, "planning_enabled", True) or not links:
            continue
        work_space = (target.id if transfer.operation_kind == "stack"
                      else source.id)
        if work_space not in machine.reachable_spaces or \
                any(not link.available for link in links):
            continue
        # Validation checks route continuity.  Candidate construction remains
        # defensive for imported scenarios that have not yet been validated.
        if links[0].src != space_node(sc, source.id) or \
                links[-1].dst != space_node(sc, target.id) or \
                any(left.dst != right.src
                    for left, right in zip(links, links[1:])):
            continue
        path = Path(
            links=list(transfer.path),
            transit_buckets=sum(link.transit_buckets for link in links),
            capacity_tph=min(link.capacity_tph for link in links))
        cands.append(Cand(
            "process", machine.id, work_space,
            grade=source.grade, path=path,
            transfer_id=transfer.id,
            source_space=source.id, target_space=target.id,
            operation_kind=transfer.operation_kind,
            output_grade=transfer.output_grade or target.grade,
            yield_factor=float(transfer.yield_factor),
            process_stage=transfer.process_stage,
            residence_min=int(transfer.residence_min),
            depends_on_stage=transfer.depends_on_stage,
            dependency_delay_min=int(transfer.dependency_delay_min),
            purpose=transfer.purpose))
    return cands, stack_by_exit


def _candidate_serves_destination(sc: Scenario, cand: Cand, destination) -> bool:
    """Whether a material candidate fulfils this destination's demand."""
    if getattr(destination, "demand_kind", "delivery") == "process_feed":
        if cand.kind != "process" or \
                cand.process_stage != getattr(destination, "process_stage", ""):
            return False
        requested = getattr(destination, "requested_grade", "") or ""
        if requested:
            try:
                return sc.space(cand.source_space).grade == requested
            except StopIteration:
                return False
        return True
    return cand.kind == "reclaim" and cand.dest_id == destination.id


def _candidate_quality_space(cand: Cand) -> str:
    return cand.source_space if cand.kind == "process" else cand.space


def solve_plan(sc: Scenario, opts: Optional[SolveOptions] = None,
               progress_cb=None) -> PlanResult:
    """`progress_cb(stage: str)` is a pure UX notification hook fired at
    stage boundaries (model build / warm start / CP-SAT / extraction).
    It has NO effect on the model or the deterministic result."""
    opts = opts or SolveOptions()
    policy_error = _louta_policy_mass_error(sc)
    if policy_error:
        return PlanResult(
            status="ERROR",
            reasons=[f"LOUTA_POLICY_MASS_MISMATCH {policy_error}"])

    def _stage(label: str):
        if progress_cb:
            try:
                progress_cb(label)
            except Exception:
                pass    # a UI hiccup must never kill a solve

    try:
        from ortools.sat.python import cp_model
    except ImportError:  # pragma: no cover
        return PlanResult(status="NO_SOLVER",
                          reasons=["OR-Tools not installed — pip install ortools"])

    t0 = time.time()
    _stage("building route & constraint model")
    # experimental multi-grade zones: expand zoned piles into per-zone
    # sub-spaces (identity when the flag is off or no zone inputs exist —
    # baseline behaviour and deterministic signatures are unchanged)
    from .zones import expand_pile_zones, zones_enabled
    if zones_enabled(sc):
        try:
            sc = expand_pile_zones(sc)
        except ValueError as exc:
            return PlanResult(status="ERROR", reasons=[f"ZONE_INPUTS {exc}"])
    T = sc.sim.horizon_buckets
    dt = sc.sim.dt_h
    graph = RouteGraph.from_scenario(sc)
    cands, stack_by_exit = build_candidates(sc, graph)
    demand_cis = {
        destination.id: [
            ci for ci, cand in enumerate(cands)
            if _candidate_serves_destination(sc, cand, destination)
        ]
        for destination in sc.destinations
    }
    m = cp_model.CpModel()

    def itn(x: float) -> int:
        return int(round(x))

    def transition_exceeds_bucket(
            machine_id: str, from_space: str, to_space: str) -> bool:
        """Legacy bucket guard; exact preparation is refined minute-by-minute."""
        estimate = estimate_space_transition(
            sc, machine_id, from_space, to_space)
        return estimate.total_min > dt * 60.0 * 0.9

    # ---------- grouping: one activity bool per (machine,space,dest) ---------
    group_keys: List[Tuple] = []
    group_members: Dict[Tuple, List[int]] = {}
    for ci, c in enumerate(cands):
        k = c.gkey()
        if k not in group_members:
            group_members[k] = []
            group_keys.append(k)
        group_members[k].append(ci)
    g_of = {ci: c.gkey() for ci, c in enumerate(cands)}

    # ---------- variables ------------------------------------------------------
    X: Dict[Tuple[int, int], cp_model.IntVar] = {}    # (cand_idx, t) -> tonnes
    # Process-transfer output after the configured mass yield.  For 100 %
    # transfers this aliases X; for washing it is the deterministic floor of
    # input × documented recovery and the difference is reported as reject.
    Y: Dict[Tuple[int, int], object] = {}
    AG: Dict[Tuple[Tuple, int], cp_model.IntVar] = {}  # (group key, t) -> active
    for gk in group_keys:
        for t in range(T):
            AG[(gk, t)] = m.new_bool_var(f"g_{hash(gk) & 0xffffff}_{t}")
    for ci, c in enumerate(cands):
        mach = sc.machine(c.machine)
        rf = opts.machine_rate_factor.get(c.machine, 1.0)
        path_cap = c.path.capacity_tph if c.path else mach.rate_max_tph
        cap = itn(min(mach.rate_max_tph * rf, path_cap) * dt)
        for t in range(T):
            x = m.new_int_var(0, max(cap, 0), f"x_{ci}_{t}")
            m.add(x <= cap * AG[(g_of[ci], t)])
            X[(ci, t)] = x
            if c.kind == "process":
                yscale = 10_000
                ycoef = int(round(max(min(c.yield_factor, 1.0), 0.0)
                                  * yscale))
                if ycoef == yscale:
                    Y[(ci, t)] = x
                else:
                    y = m.new_int_var(0, max(cap, 0), f"y_{ci}_{t}")
                    # y = floor(x × ycoef / yscale), an exact integer
                    # transformation with at most one tonne of discretisation
                    # per active bucket.
                    m.add(yscale * y <= ycoef * x)
                    m.add(yscale * y >= ycoef * x - (yscale - 1))
                    Y[(ci, t)] = y
    # per-group total flow: machine rate ceiling + min stable when active
    for gk, members in group_members.items():
        mach = sc.machine(gk[1])
        rf = opts.machine_rate_factor.get(gk[1], 1.0)
        hi = itn(mach.rate_max_tph * rf * dt)
        lo = itn(mach.rate_min_tph * dt)
        lo = min(lo, hi)
        for t in range(T):
            tot = sum(X[(ci, t)] for ci in members)
            m.add(tot <= hi * AG[(gk, t)])
            if lo > 0:
                m.add(tot >= lo * AG[(gk, t)])
            else:
                m.add(tot >= AG[(gk, t)])   # active implies non-zero flow

    # machine exclusivity + calendars + availability
    mach_groups: Dict[str, List[Tuple]] = {}
    for gk in group_keys:
        mach_groups.setdefault(gk[1], []).append(gk)
    # Coverage is a secondary, demand-driven planning objective.  A machine
    # counts as used only when it is a real, available solver resource with at
    # least one feasible candidate and a positive-flow activity.  Reference,
    # future and planning-disabled display assets never enter this set.
    machine_used: Dict[str, cp_model.IntVar] = {}
    for mid, gks in mach_groups.items():
        machine = sc.machine(mid)
        if not machine.available or not getattr(machine, "planning_enabled", True):
            continue
        used = m.new_bool_var(f"used_{mid}")
        activities = [AG[(gk, t)] for gk in gks for t in range(T)]
        m.add(sum(activities) >= used)
        m.add(sum(activities) <= len(activities) * used)
        machine_used[mid] = used
    for mid, gks in mach_groups.items():
        for t in range(T):
            m.add(sum(AG[(gk, t)] for gk in gks) <= 1)
            if sc.machine_blocked(mid, t + opts.horizon_offset) or \
                    not sc.machine(mid).available:
                for gk in gks:
                    m.add(AG[(gk, t)] == 0)

    # corridor-feeder / route-confluence exclusivity (config data on Site):
    # at most ONE machine of each configured group active per bucket — either
    # feeder may be selected, both never run together until the confluence
    # configuration explicitly permits simultaneous partial-rate reclaim
    excl_groups: List[List[str]] = []
    for site in sc.sites:
        for grp in getattr(site, "exclusive_machine_groups", []) or []:
            grp_ = [mid for mid in grp if mid in mach_groups]
            if len(grp_) > 1:
                excl_groups.append(grp_)
    for gi, grp in enumerate(excl_groups):
        for t in range(T):
            m.add(sum(AG[(gk, t)] for mid in grp
                      for gk in mach_groups[mid]) <= 1)

    # banned tuples (operator rejections / replan exclusions)
    for (bm, bs, bd, b0, b1) in opts.banned:
        for gk in group_keys:
            if gk[1] == bm and (not bs or gk[2] == bs) and (not bd or gk[3] == bd):
                for t in range(max(b0 - opts.horizon_offset, 0),
                               min(b1 - opts.horizon_offset, T - 1) + 1):
                    m.add(AG[(gk, t)] == 0)

    # Coarse travel/setup guard + move-change penalty. Exact positioning is
    # reserved later in the minute execution schedule; the bucket model only
    # rejects a direct switch that cannot fit within almost one native bucket.
    diffs = []
    diff_map: Dict[Tuple[str, str, int], object] = {}
    for mid, gks in mach_groups.items():
        spaces = sorted({gk[2] for gk in gks})
        at = {}
        for s in spaces:
            for t in range(T):
                at[(s, t)] = sum(AG[(gk, t)] for gk in gks if gk[2] == s)
        for t in range(1, T):
            for s in spaces:
                d = m.new_bool_var(f"mv_{mid}_{s}_{t}")
                m.add(at[(s, t)] - at[(s, t - 1)] <= d)
                m.add(at[(s, t - 1)] - at[(s, t)] <= d)
                diffs.append(d)
                diff_map[(mid, s, t)] = d
            for s1 in spaces:
                for s2 in spaces:
                    if s1 >= s2:
                        continue
                    if transition_exceeds_bucket(mid, s1, s2):
                        m.add(at[(s1, t - 1)] + at[(s2, t)] <= 1)
                        m.add(at[(s2, t - 1)] + at[(s1, t)] <= 1)

    # ---------- corridor arrivals (transport delay, eq. 5) --------------------
    delayed_links = [l for l in sc.links if l.transit_buckets > 0]
    arrivals: Dict[Tuple[str, str, int], object] = {}   # (exit_node, grade, t)
    for l in delayed_links:
        L = l.transit_buckets
        for g in [gr.id for gr in sc.grades] or [""]:
            for t in range(T):
                terms = []
                const = 0
                for e in sc.belt_profile:
                    if e.link_id == l.id and e.grade == g and \
                            e.arrive_bucket - opts.horizon_offset == t:
                        const += itn(e.mass_t)
                td = t - L
                if td >= 0:
                    for ci, c in enumerate(cands):
                        if c.kind == "dispatch" and c.dest_id == l.id and \
                                sc.space(c.space).grade == g:
                            terms.append(X[(ci, td)])
                if terms or const:
                    contribution = (sum(terms) + const) if terms else const
                    key = (l.dst, g, t)
                    # C1 and C2 converge on the same M16 reception tower.
                    # Accumulate both corridors; assigning by key here used to
                    # overwrite the first corridor with the second.
                    arrivals[key] = arrivals.get(key, 0) + contribution

    # arrivals must be stacked (mass conservation at the receiving tower);
    # any un-stackable arrival goes to a heavily-penalised surge slack
    surge_slacks: Dict[int, List] = {t: [] for t in range(T)}
    surge_map: Dict[Tuple[str, str, int], object] = {}
    grades_all = [gr.id for gr in sc.grades] or [""]
    for exit_node in stack_by_exit:
        for g in grades_all:
            for t in range(T):
                arr = arrivals.get((exit_node, g, t), 0)
                stack_terms = [X[(ci, t)] for ci, c in enumerate(cands)
                               if c.kind == "stack" and c.exit_node == exit_node
                               and c.grade == g]
                if isinstance(arr, int) and arr == 0:
                    for st_ in stack_terms:
                        m.add(st_ == 0)
                else:
                    sv = m.new_int_var(0, 10 ** 7, f"sg_{exit_node}_{g}_{t}")
                    m.add(sum(stack_terms) + sv == arr)
                    surge_slacks[t].append(sv)
                    surge_map[(exit_node, g, t)] = sv

    # ---------- inventory mass balance (eq. 1) --------------------------------
    inv: Dict[Tuple[str, int], cp_model.IntVar] = {}
    spill: List[cp_model.IntVar] = []
    spill_map: Dict[Tuple[str, int], object] = {}
    no_passthrough_spaces = set(
        sc.config.get("PROCESS_NO_PASSTHROUGH_SPACES", []) or [])
    for sp in sc.spaces:
        cap = itn(sp.capacity_t)
        for t in range(T + 1):
            inv[(sp.id, t)] = m.new_int_var(0, cap, f"inv_{sp.id}_{t}")
        m.add(inv[(sp.id, 0)] == min(itn(sp.mass_t), cap))
        for t in range(T):
            outs = [X[(ci, t)] for ci, c in enumerate(cands)
                    if c.input_space() == sp.id]
            ins = [
                (Y[(ci, t)] if c.kind == "process" else X[(ci, t)])
                for ci, c in enumerate(cands)
                if c.output_space() == sp.id
            ]
            exo_in = exo_out = 0
            for e in sc.exogenous:
                if e.start_bucket <= t + opts.horizon_offset <= e.end_bucket:
                    if e.target_space == sp.id:
                        exo_in += itn(e.rate_tph * dt)
                    if e.source_space == sp.id:
                        exo_out += itn(e.rate_tph * dt)
            sv = m.new_int_var(0, 10 ** 7, f"spill_{sp.id}_{t}")
            spill.append(sv)
            spill_map[(sp.id, t)] = sv
            if sp.id in no_passthrough_spaces:
                # Material produced during bucket t becomes available at the
                # next boundary; downstream equipment may consume only the
                # start-of-bucket buffer.  This prevents an hourly model from
                # implying output before the configured sub-hour residence.
                m.add(sum(outs) <= inv[(sp.id, t)])
            m.add(inv[(sp.id, t + 1)] ==
                  inv[(sp.id, t)] + sum(ins) + exo_in - sum(outs) - exo_out - sv)

    # Process-interface buffers may pass material within a native planning
    # bucket, but may not hide carry-over between buckets.  The exact
    # sub-hour ordering is validated by the minute dependency scheduler.
    for sid in sc.config.get("PROCESS_ZERO_CARRY_SPACES", []) or []:
        if any(sp.id == sid for sp in sc.spaces):
            for t in range(T + 1):
                m.add(inv[(sid, t)] == 0)

    # Transparent terminal inventory targets.  S0 uses these as replenishment
    # purposes for stocked routes; they are configuration data, never implicit
    # optimizer behaviour.
    for sid, target in (
            sc.config.get("TERMINAL_INVENTORY_MIN_T", {}) or {}).items():
        if any(sp.id == sid for sp in sc.spaces):
            m.add(inv[(sid, T)] >= itn(float(target)))
    for sid, target in (
            sc.config.get("TERMINAL_INVENTORY_EXACT_T", {}) or {}).items():
        if any(sp.id == sid for sp in sc.spaces):
            m.add(inv[(sid, T)] == itn(float(target)))

    # Explicit process-output campaign targets (e.g. Louta L1/L2 stocked
    # route contribution). They give washing/stacking work a real planning
    # purpose and make the policy auditable in Configure. "Target" is exact:
    # allowing an unlabelled excess would create work with no declared purpose.
    for stage, target in effective_process_stage_targets(sc).items():
        stage_outputs = [
            Y[(ci, t)]
            for ci, cand in enumerate(cands)
            if cand.kind == "process" and cand.process_stage == stage
            for t in range(T)
        ]
        if stage_outputs:
            m.add(sum(stage_outputs) == itn(float(target)))

    # Process-stage residence is a material-availability rule, not merely a
    # post-solve display offset.  A transfer produced during native bucket u is
    # conservatively available after that bucket has closed plus the declared
    # residence delay.  The minute overlay may then place the dependent work
    # anywhere inside the first eligible bucket without consuming immature
    # tonnes.  Initial source inventory is treated as already mature at t0.
    dependency_groups: Dict[Tuple[str, str], List[int]] = {}
    for ci, cand in enumerate(cands):
        if cand.kind == "process" and cand.depends_on_stage:
            dependency_groups.setdefault(
                (cand.depends_on_stage, cand.source_space), []).append(ci)
    bucket_width_min = max(itn(dt * 60), 1)
    for (stage, source_space), dep_cis in dependency_groups.items():
        upstream_cis = [
            ci for ci, cand in enumerate(cands)
            if cand.kind == "process"
            and cand.process_stage == stage
            and cand.target_space == source_space
        ]
        if not upstream_cis:
            continue
        delay_min = max(
            max(int(cands[ci].dependency_delay_min), 0)
            for ci in dep_cis)
        lag_buckets = 1 + (
            delay_min + bucket_width_min - 1) // bucket_width_min
        initial_mature_t = itn(sc.space(source_space).mass_t)
        for t in range(T):
            consumed = sum(
                X[(ci, u)] for ci in dep_cis for u in range(t + 1))
            last_mature_bucket = t - lag_buckets
            produced = (
                sum(
                    Y[(ci, u)]
                    for ci in upstream_cis
                    for u in range(last_mature_bucket + 1))
                if last_mature_bucket >= 0 else 0)
            m.add(consumed <= initial_mature_t + produced)

    # Corridor/source targets are exact for the same reason: a tonne above the
    # configured stocked-route contribution would be an undeclared movement.
    for link_id, target in effective_corridor_targets(sc).items():
        dispatch_terms = [
            X[(ci, t)]
            for ci, cand in enumerate(cands)
            if cand.kind == "dispatch" and cand.dest_id == link_id
            for t in range(T)
        ]
        if dispatch_terms:
            m.add(sum(dispatch_terms) == itn(float(target)))

    # Optional steady-state replenishment policy for the three-site
    # demonstration.  It restores the M16 *supply position* rather than
    # creating arbitrary mine movements:
    #
    #   terminal M16 stock + terminal corridor in-transit
    #       <= initial M16 stock + initial corridor in-transit.
    #
    # Therefore every mine dispatch is justified by downstream consumption.
    # Material dispatched too late to arrive is retained explicitly as
    # terminal in-transit instead of disappearing beyond the horizon.
    terminal_supply_max = sc.config.get(
        "M16_TERMINAL_SUPPLY_POSITION_MAX_T")
    terminal_in_transit_terms = []
    terminal_initial_belt_t = 0
    terminal_supply_expr = None
    if terminal_supply_max is not None:
        mph_spaces = [sp for sp in sc.spaces if sp.site == "MPH"]
        for ci, c in enumerate(cands):
            if c.kind != "dispatch":
                continue
            try:
                lag = sc.link(c.dest_id).transit_buckets
            except StopIteration:
                continue
            for t in range(T):
                if t + lag >= T:
                    terminal_in_transit_terms.append(X[(ci, t)])
        terminal_initial_belt_t = sum(
            itn(e.mass_t) for e in sc.belt_profile
            if e.arrive_bucket - opts.horizon_offset >= T
        )
        terminal_supply_expr = (
            sum(inv[(sp.id, T)] for sp in mph_spaces)
            + sum(terminal_in_transit_terms)
            + terminal_initial_belt_t
        )
        m.add(terminal_supply_expr <= itn(float(terminal_supply_max)))

    # ---------- demand with unmet slack (eq. 4) --------------------------------
    unmet: Dict[Tuple[str, int], cp_model.IntVar] = {}
    delivered: Dict[Tuple[str, int], object] = {}
    for d in sc.destinations:
        cis = demand_cis[d.id]
        expressions = []
        demands = []
        for t in range(T):
            dem = itn(sc.demand_tph(d.id, t + opts.horizon_offset) * dt)
            expr = sum(X[(ci, t)] for ci in cis)
            delivered[(d.id, t)] = expr
            expressions.append(expr)
            demands.append(max(dem, 0))
        if getattr(d, "demand_kind", "delivery") == "process_feed":
            # A washing campaign is a minimum horizon feed requirement. The
            # optimiser chooses its operating buckets; a separately configured
            # recovered-output campaign may legitimately require more input.
            # Hourly demand values therefore provide the requested total, not
            # a fabricated fixed run calendar.
            total_demand = sum(demands)
            u = m.new_int_var(0, total_demand, f"u_{d.id}_total")
            m.add(sum(expressions) + u >= total_demand)
            for t in range(T):
                unmet[(d.id, t)] = u if t == 0 else 0
        else:
            for t, (expr, dem) in enumerate(zip(expressions, demands)):
                u = m.new_int_var(0, dem, f"u_{d.id}_{t}")
                # Final-unit demand is exact: no unrequested over-delivery.
                m.add(expr + u == dem)
                unmet[(d.id, t)] = u

    # ---------- quality constraints (eq. 3) -------------------------------------
    qslack: List[cp_model.IntVar] = []
    qslack_by_dest: Dict[str, List[cp_model.IntVar]] = {}
    qslack_map: Dict[Tuple, object] = {}
    for d in sc.destinations:
        d_cis = demand_cis[d.id]
        for spec in d.spec:
            for t in range(T):
                dexpr = delivered[(d.id, t)]
                if isinstance(dexpr, int):
                    continue
                lhs = sum(
                    itn(sc.quality_of(
                        _candidate_quality_space(cands[ci])
                    ).get(spec.param, 0.0) * QSCALE) * X[(ci, t)]
                    for ci in d_cis
                )
                if spec.pmin is not None:
                    bound = itn((spec.pmin + spec.margin) * QSCALE)
                    if spec.hard:
                        m.add(lhs >= bound * dexpr)
                    else:
                        s = m.new_int_var(0, QSLACK_MAX, f"qmin_{d.id}_{spec.param}_{t}")
                        m.add(lhs + s >= bound * dexpr)
                        qslack.append(s)
                        qslack_by_dest.setdefault(d.id, []).append(s)
                        qslack_map[("min", d.id, spec.param, t)] = s
                if spec.pmax is not None:
                    bound = itn(max(spec.pmax - spec.margin, 0) * QSCALE)
                    if spec.hard:
                        m.add(lhs <= bound * dexpr)
                    else:
                        s = m.new_int_var(0, QSLACK_MAX, f"qmax_{d.id}_{spec.param}_{t}")
                        m.add(lhs <= bound * dexpr + s)
                        qslack.append(s)
                        qslack_by_dest.setdefault(d.id, []).append(s)
                        qslack_map[("max", d.id, spec.param, t)] = s

    # ---------- link capacities / shared / conflict groups ----------------------
    link_users: Dict[str, List[int]] = {}
    for ci, c in enumerate(cands):
        if c.path:
            for li in c.path.links:
                link_users.setdefault(li, []).append(ci)
    for lid, users in link_users.items():
        l = sc.link(lid)
        cap = itn(l.capacity_tph * dt) if l.available else 0
        for t in range(T):
            m.add(sum(X[(ci, t)] for ci in users) <= cap)
    for gname, lids in graph.shared_groups().items():
        caps = [sc.link(li).capacity_tph for li in lids]
        gcap = itn(min(caps) * dt)
        users = sorted({ci for li in lids for ci in link_users.get(li, [])})
        if users:
            for t in range(T):
                m.add(sum(X[(ci, t)] for ci in users) <= gcap)
    conf_map: Dict[Tuple[str, str, int], object] = {}
    for gname, lids in graph.conflict_groups().items():
        for t in range(T):
            actives = []
            for li in lids:
                users = link_users.get(li, [])
                if not users:
                    continue
                b = m.new_bool_var(f"lact_{gname}_{li}_{t}")
                cap = itn(sc.link(li).capacity_tph * dt)
                m.add(sum(X[(ci, t)] for ci in users) <= cap * b)
                actives.append(b)
                conf_map[(gname, li, t)] = b
            if len(actives) > 1:
                m.add(sum(actives) <= 1)

    # ---------- safety stock (soft, w6) -------------------------------------------
    short: Dict[Tuple[str, int], cp_model.IntVar] = {}
    for sp in sc.spaces:
        if sp.safety_stock_t <= 0:
            continue
        tgt = itn(sp.safety_stock_t)
        for t in range(1, T + 1):
            s = m.new_int_var(0, tgt, f"short_{sp.id}_{t}")
            m.add(inv[(sp.id, t)] + s >= tgt)
            short[(sp.id, t)] = s

    # ---------- same-pile stack/reclaim concurrency (conditional) ---------------
    # USER-CONFIRMED operating rule (project team, 15-Jul-2026, MPH M16):
    # stacking and reclaiming MAY occur simultaneously on the same stockpile;
    # the scheduler must prevent collisions/overlapping work zones — never
    # prohibit same-pile concurrency categorically. This model never had a
    # categorical prohibition (the mass balance always admitted simultaneous
    # in/out); this block adds the missing CONSERVATIVE spatial room condition
    # plus the per-site configuration switch. Extraction assigns the actual
    # non-overlapping work zones (reclaim face at the low-x end, stacking at
    # the high-x end). Numerical separations are site parameters (values TBC —
    # demonstration defaults ASSUMED).
    #   room rule when both active on pile p at t (uniform-density basis):
    #       stack_t + out_t <= capacity * (1 - gap/length)
    #   with gap = min_machine_separation_m + 2 * work_zone_buffer_m.
    stack_gks_by_space: Dict[str, List[Tuple]] = {}
    out_gks_by_space: Dict[str, List[Tuple]] = {}
    for gk in group_keys:
        member = cands[group_members[gk][0]]
        d_ = (stack_gks_by_space
              if member.display_kind() == "stack"
              else out_gks_by_space)
        d_.setdefault(gk[2], []).append(gk)
    stack_cis_by_space: Dict[str, List[int]] = {}
    out_cis_by_space: Dict[str, List[int]] = {}
    for ci, c in enumerate(cands):
        d_ = (stack_cis_by_space
              if c.display_kind() == "stack"
              else out_cis_by_space)
        d_.setdefault(c.space, []).append(ci)
    site_by_id = {s.id: s for s in sc.sites}
    conc_vars: Dict[Tuple[str, int], Tuple] = {}   # (space,t) -> (s_any,o_any,both)
    for sp in sc.spaces:
        sgs = stack_gks_by_space.get(sp.id)
        ogs = out_gks_by_space.get(sp.id)
        if not sgs or not ogs:
            continue                      # concurrency impossible here anyway
        site = site_by_id.get(sp.site)
        allowed = getattr(site, "same_pile_concurrency_allowed", None)
        gap_m = (getattr(site, "min_machine_separation_m", 50.0)
                 + 2.0 * getattr(site, "work_zone_buffer_m", 25.0))
        L = max(sp.x_to - sp.x_from, 1.0)
        usable = itn(max(sp.capacity_t * (1.0 - gap_m / L), 0.0))
        cap = itn(sp.capacity_t)
        # the room rule can only ever bind when the combined machine rates
        # could exceed the usable share — otherwise it is omitted statically
        # (same conservative guarantee, far fewer constraints)
        machs = sorted({gk[1] for gk in sgs} | {gk[1] for gk in ogs})
        max_flow = itn(sum(sc.machine(mid).rate_max_tph for mid in machs) * dt)
        need_room = usable < max_flow
        # provable-slack pruning: inv[p,t] >= init - t*(max_out + exo_out)
        # (worst case), so while that bound still exceeds max_flow the
        # pass-through constraint cannot bind and is safely omitted
        exo_out_rate = max((e.rate_tph for e in sc.exogenous
                            if e.source_space == sp.id), default=0.0)
        drain = max_flow + itn(exo_out_rate * dt)
        init = min(itn(sp.mass_t), cap)
        for t in range(T):
            passthrough_slack = (init - t * drain) >= max_flow
            if allowed is False:          # site-configured (NOT categorical)
                for gs in sgs:
                    for go in ogs:
                        m.add(AG[(gs, t)] + AG[(go, t)] <= 1)
                conc_vars[(sp.id, t)] = ("forbidden", None, None)
                continue
            if passthrough_slack and not need_room:
                conc_vars[(sp.id, t)] = ("implied", None, None)
                continue
            # compact intermediate totals keep the added constraints short
            o_tot = m.new_int_var(0, max_flow, f"ot_{sp.id}_{t}")
            m.add(o_tot == sum(X[(ci, t)] for ci in out_cis_by_space[sp.id]))
            s_tot = None
            if need_room:
                s_tot = m.new_int_var(0, max_flow, f"st_{sp.id}_{t}")
                m.add(s_tot == sum(X[(ci, t)]
                                   for ci in stack_cis_by_space[sp.id]))
            for gs in sgs:
                # no same-bucket pass-through while this stack activity runs:
                # the reclaim face may only consume START-of-bucket inventory,
                # never material being stacked in the same hour (that would
                # put both machines on the same face — an overlapping-work-
                # zone collision). Sequential stack -> reclaim in later
                # buckets stays permitted (face handover). Pure linear
                # big-M form with the TIGHTEST valid M (max_flow bounds
                # o_tot), no auxiliary booleans — the hint stays complete.
                if not passthrough_slack:
                    m.add(o_tot <= inv[(sp.id, t)]
                          + max_flow * (1 - AG[(gs, t)]))
                if need_room:
                    # conservative spatial room (uniform-density basis),
                    # binding only when a stack AND an out activity coexist
                    big = 2 * max_flow
                    for go in ogs:
                        m.add(s_tot + o_tot <= usable
                              + big * (2 - AG[(gs, t)] - AG[(go, t)]))
            conc_vars[(sp.id, t)] = ("conditional", o_tot, s_tot)

    # ---------- operator-fixed jobs --------------------------------------------------
    fixed_missing = []
    for fj in sc.fixed_jobs:
        gmatch = None
        for gk in group_keys:
            if _fixed_matches_group(sc, fj, gk):
                gmatch = gk
                break
        if gmatch is None:
            fixed_missing.append(fj.id)
            continue
        for t in range(max(fj.start_bucket - opts.horizon_offset, 0),
                       min(fj.end_bucket - opts.horizon_offset, T - 1) + 1):
            m.add(AG[(gmatch, t)] == 1)
            if fj.rate_tph > 0:
                m.add(sum(X[(ci, t)] for ci in group_members[gmatch])
                      == itn(fj.rate_tph * dt))

    _stage("generating warm start")
    # ---------- deterministic greedy warm start (complete solution hint) --------
    # Reproducible plans need deterministic search; deterministic search needs a
    # good start. This greedy "dispatcher pass" constructs a COMPLETE, exactly
    # feasible assignment for EVERY variable family (activities, flows,
    # inventory chain, slacks, conflict booleans, move indicators) so CP-SAT's
    # first solution is already an operable plan which it then improves.
    hint_ag: Dict[Tuple[Tuple, int], int] = {}
    hint_x: Dict[Tuple[int, int], int] = {}
    used_mach: Dict[Tuple[str, int], bool] = {}
    link_load_h: Dict[Tuple[str, int], int] = {}
    shared_load_h: Dict[Tuple[str, int], int] = {}
    conflict_used: Dict[Tuple[str, int], str] = {}   # (conflict group, t) -> link
    prev_space_h: Dict[str, str] = {}
    inv_h = {sp.id: min(itn(sp.mass_t), itn(sp.capacity_t)) for sp in sc.spaces}
    link_conflict = {l.id: l.conflict_group for l in sc.links if l.conflict_group}
    link_shared = {l.id: l.shared_group for l in sc.links if l.shared_group}
    shared_cap = {g: itn(min(sc.link(li).capacity_tph for li in lids) * dt)
                  for g, lids in graph.shared_groups().items()}
    banned_set = set()
    for (bm, bs, bd, b0, b1) in opts.banned:
        for gk in group_keys:
            if gk[1] == bm and (not bs or gk[2] == bs) and (not bd or gk[3] == bd):
                for t in range(max(b0 - opts.horizon_offset, 0),
                               min(b1 - opts.horizon_offset, T - 1) + 1):
                    banned_set.add((gk, t))

    def blocked(mid: str, t: int) -> bool:
        return sc.machine_blocked(mid, t + opts.horizon_offset) or \
            not sc.machine(mid).available

    # exclusivity groups in the greedy pass (keeps the hint exactly feasible)
    excl_of: Dict[str, List[int]] = {}
    for gi, grp in enumerate(excl_groups):
        for mid in grp:
            excl_of.setdefault(mid, []).append(gi)
    excl_used: Dict[Tuple[int, int], str] = {}   # (group idx, t) -> machine

    def excl_blocked(mid: str, t: int) -> bool:
        return any(excl_used.get((gi, t), mid) != mid
                   for gi in excl_of.get(mid, ()))

    def path_room(ci: int, t: int) -> int:
        """Max extra tonnes this candidate's path can carry at t."""
        c = cands[ci]
        room = 10 ** 9
        if c.path:
            for li in c.path.links:
                l = sc.link(li)
                if not l.available:
                    return 0
                g = link_conflict.get(li)
                if g and conflict_used.get((g, t), li) != li:
                    return 0
                room = min(room, itn(l.capacity_tph * dt)
                           - link_load_h.get((li, t), 0))
            for g in {link_shared[li] for li in c.path.links if li in link_shared}:
                room = min(room, shared_cap[g] - shared_load_h.get((g, t), 0))
        return max(room, 0)

    def commit_path(ci: int, t: int, v: int):
        c = cands[ci]
        if c.path:
            for li in c.path.links:
                link_load_h[(li, t)] = link_load_h.get((li, t), 0) + v
                g = link_conflict.get(li)
                if g:
                    conflict_used[(g, t)] = li
            for g in {link_shared[li] for li in c.path.links if li in link_shared}:
                shared_load_h[(g, t)] = shared_load_h.get((g, t), 0) + v

    def assign_flow(gk: Tuple, t: int, amount: int) -> int:
        """Activate group at t; split `amount` over feasible member paths."""
        if (gk, t) in banned_set or blocked(gk[1], t) or \
                used_mach.get((gk[1], t)) or excl_blocked(gk[1], t):
            return 0
        ps = prev_space_h.get(gk[1])
        if ps is not None and transition_exceeds_bucket(
                gk[1], ps, gk[2]):
            return 0
        mach = sc.machine(gk[1])
        rf = opts.machine_rate_factor.get(gk[1], 1.0)
        lo = min(itn(mach.rate_min_tph * dt), itn(mach.rate_max_tph * rf * dt))
        rem = min(amount, itn(mach.rate_max_tph * rf * dt))
        placed = 0
        plan = []
        for ci in group_members[gk]:
            if rem <= 0:
                break
            ccap = itn(min(mach.rate_max_tph * rf,
                           cands[ci].path.capacity_tph if cands[ci].path
                           else mach.rate_max_tph) * dt)
            v = min(ccap, rem, path_room(ci, t))
            if v <= 0:
                continue
            plan.append((ci, v))
            rem -= v
            placed += v
        if placed <= 0 or placed < max(lo, 1):
            return 0
        for ci, v in plan:
            hint_x[(ci, t)] = v
            commit_path(ci, t, v)
        hint_ag[(gk, t)] = 1
        used_mach[(gk[1], t)] = True
        for gi in excl_of.get(gk[1], ()):
            excl_used[(gi, t)] = gk[1]
        return placed

    # constant arrival mass per (exit node, grade, t) — belt profile only
    arr_const: Dict[Tuple[str, str, int], int] = {}
    for l in [l_ for l_ in sc.links if l_.transit_buckets > 0]:
        for e in sc.belt_profile:
            if e.link_id == l.id:
                k = (l.dst, e.grade, e.arrive_bucket - opts.horizon_offset)
                if 0 <= k[2] < T:
                    arr_const[k] = arr_const.get(k, 0) + itn(e.mass_t)

    fixed_windows = []   # (gk, t0, t1, rate_t) resolved fixed jobs
    for fj in sc.fixed_jobs:
        for gk in group_keys:
            if _fixed_matches_group(sc, fj, gk):
                fixed_windows.append(
                    (gk, max(fj.start_bucket - opts.horizon_offset, 0),
                     min(fj.end_bucket - opts.horizon_offset, T - 1),
                     itn(fj.rate_tph * dt)))
                break

    # Hard stocked-route/process policy must be represented in the hint too.
    # The original dispatcher below is intentionally destination-demand first;
    # without this pass it hints every process/dispatch target at zero and the
    # otherwise useful complete hint is infeasible.  Build a deterministic,
    # configuration-driven process chain without changing any model constraint:
    #
    #   targeted process -> declared dependent transfer -> replenishing input
    #   stack, plus configured corridor dispatch -> delayed receiving stack.
    #
    # Static reservations mirror the solver's machine, route-confluence,
    # link/shared-capacity and site same-pile rules.  If a non-baseline scenario
    # cannot fit an action, the pass simply leaves that action unhinted; CP-SAT
    # remains authoritative.
    policy_actions: Dict[int, List[Tuple[int, int]]] = {}
    policy_arrivals: Dict[Tuple[str, str, int], int] = {}
    hint_y: Dict[Tuple[int, int], int] = {}
    reserved_machine: Dict[Tuple[str, int], int] = {}
    reserved_excl: Dict[Tuple[int, int], str] = {}
    reserved_link: Dict[Tuple[str, int], int] = {}
    reserved_shared: Dict[Tuple[str, int], int] = {}
    reserved_conflict: Dict[Tuple[str, int], str] = {}
    reserved_pile_mode: Dict[Tuple[str, int], str] = {}
    site_by_space = {
        sp.id: next((site for site in sc.sites if site.id == sp.site), None)
        for sp in sc.spaces
    }

    def cand_cap(ci: int) -> int:
        cand = cands[ci]
        mach = sc.machine(cand.machine)
        rf = opts.machine_rate_factor.get(cand.machine, 1.0)
        path_cap = cand.path.capacity_tph if cand.path else mach.rate_max_tph
        return max(itn(min(mach.rate_max_tph * rf, path_cap) * dt), 0)

    def cand_lo(ci: int) -> int:
        cand = cands[ci]
        mach = sc.machine(cand.machine)
        rf = opts.machine_rate_factor.get(cand.machine, 1.0)
        return min(itn(mach.rate_min_tph * dt),
                   itn(mach.rate_max_tph * rf * dt))

    def process_output(ci: int, amount: int) -> int:
        cand = cands[ci]
        if cand.kind != "process":
            return amount
        coef = int(round(max(min(cand.yield_factor, 1.0), 0.0) * 10_000))
        return amount if coef == 10_000 else (amount * coef) // 10_000

    def flow_chunks(total: int, cap: int, lo: int) -> List[int]:
        """Full-capacity deterministic chunks with a valid final minimum."""
        if total <= 0 or cap <= 0:
            return []
        lo = max(min(lo, cap), 1)
        chunks = []
        remaining = total
        while remaining > cap:
            take = cap
            if 0 < remaining - take < lo:
                take = remaining - lo
            if take < lo:
                return []
            chunks.append(take)
            remaining -= take
        if remaining:
            if remaining < lo:
                return []
            chunks.append(remaining)
        return chunks

    def can_reserve(ci: int, t: int, amount: int) -> bool:
        if not (0 <= t < T):
            return False
        cand = cands[ci]
        gk = g_of[ci]
        cap = cand_cap(ci)
        lo = cand_lo(ci)
        if amount <= 0 or amount > cap or amount < max(lo, 1):
            return False
        if (gk, t) in banned_set or blocked(cand.machine, t):
            return False
        if (cand.machine, t) in reserved_machine:
            return False
        for gi in excl_of.get(cand.machine, ()):
            if reserved_excl.get((gi, t), cand.machine) != cand.machine:
                return False
        # Mirror the coarse adjacent-bucket travel guard in the warm start.
        for (other_machine, tt), other_ci in reserved_machine.items():
            if other_machine != cand.machine or tt == t:
                continue
            if abs(t - tt) == 1 and transition_exceeds_bucket(
                    cand.machine, cand.space, cands[other_ci].space):
                return False
        if cand.path:
            for li in cand.path.links:
                link = sc.link(li)
                if not link.available or (
                        reserved_link.get((li, t), 0) + amount
                        > itn(link.capacity_tph * dt)):
                    return False
                conflict = link_conflict.get(li)
                if conflict and reserved_conflict.get(
                        (conflict, t), li) != li:
                    return False
            for shared in {
                    link_shared[li] for li in cand.path.links
                    if li in link_shared}:
                if (reserved_shared.get((shared, t), 0) + amount
                        > shared_cap[shared]):
                    return False
        site = site_by_space.get(cand.space)
        if getattr(site, "same_pile_concurrency_allowed", None) is False:
            mode = "stack" if cand.display_kind() == "stack" else "out"
            prior = reserved_pile_mode.get((cand.space, t))
            if prior is not None and prior != mode:
                return False
        return True

    def reserve(ci: int, t: int, amount: int):
        cand = cands[ci]
        reserved_machine[(cand.machine, t)] = ci
        for gi in excl_of.get(cand.machine, ()):
            reserved_excl[(gi, t)] = cand.machine
        if cand.path:
            for li in cand.path.links:
                reserved_link[(li, t)] = \
                    reserved_link.get((li, t), 0) + amount
                conflict = link_conflict.get(li)
                if conflict:
                    reserved_conflict[(conflict, t)] = li
            for shared in {
                    link_shared[li] for li in cand.path.links
                    if li in link_shared}:
                reserved_shared[(shared, t)] = \
                    reserved_shared.get((shared, t), 0) + amount
        site = site_by_space.get(cand.space)
        if getattr(site, "same_pile_concurrency_allowed", None) is False:
            reserved_pile_mode[(cand.space, t)] = (
                "stack" if cand.display_kind() == "stack" else "out")
        policy_actions.setdefault(t, []).append((ci, amount))

    def first_reservable(ci: int, amount: int, start: int = 0,
                         stop: Optional[int] = None) -> Optional[int]:
        end = T if stop is None else min(stop, T)
        return next((t for t in range(max(start, 0), end)
                     if can_reserve(ci, t, amount)), None)

    # 1) Exact useful output chunks for every configured process stage.
    stage_queues: Dict[str, List[Tuple[int, int, int]]] = {}
    for stage, raw_target in sorted(
            effective_process_stage_targets(sc).items()):
        target = max(itn(float(raw_target)), 0)
        stage_cis = sorted(
            (ci for ci, cand in enumerate(cands)
             if cand.kind == "process" and cand.process_stage == stage),
            key=lambda ci: (cands[ci].transfer_id, cands[ci].machine))
        if target <= 0 or not stage_cis:
            continue
        # Pass 2 emits dependent transfers from their upstream actions.  Do
        # not queue them here as well or exact stage targets are doubled.
        if all(cands[ci].depends_on_stage for ci in stage_cis):
            continue
        # Prefer the candidate with the largest useful output envelope.  Stages
        # with parallel alternatives remain deterministic without inventing a
        # split policy that configuration did not request.
        ci = max(stage_cis, key=lambda idx: (process_output(
            idx, cand_cap(idx)), -idx))
        cap = cand_cap(ci)
        lo = max(cand_lo(ci), 1)
        out_hi = process_output(ci, cap)
        out_lo = process_output(ci, lo)
        if out_hi <= 0:
            continue
        count = max((target + out_hi - 1) // out_hi, 1)
        useful_total = max(target, count * out_lo)
        if useful_total > count * out_hi:
            continue
        outputs = [out_lo] * count
        remainder = useful_total - count * out_lo
        for idx in range(count):
            add = min(remainder, out_hi - out_lo)
            outputs[idx] += add
            remainder -= add
        coef = int(round(max(min(cands[ci].yield_factor, 1.0), 0.0)
                         * 10_000))
        inputs = [
            out if coef == 10_000 else (out * 10_000 + coef - 1) // coef
            for out in outputs
        ]
        if all(lo <= amount <= cap for amount in inputs):
            stage_queues[stage] = [
                (ci, amount, process_output(ci, amount))
                for amount in inputs
            ]

    # Round-robin stages so exclusive parallel line reclaimers alternate.
    scheduled_stage: Dict[str, List[Tuple[int, int, int, int]]] = {}
    stage_cursor = {stage: 0 for stage in stage_queues}
    while any(stage_queues.values()):
        progressed = False
        for stage in sorted(stage_queues):
            queue = stage_queues[stage]
            if not queue:
                continue
            ci, amount, output = queue[0]
            t = first_reservable(ci, amount, stage_cursor[stage])
            if t is None:
                continue
            reserve(ci, t, amount)
            scheduled_stage.setdefault(stage, []).append(
                (t, ci, amount, output))
            queue.pop(0)
            stage_cursor[stage] = t + 1
            progressed = True
        if not progressed:
            break

    # 2) Follow declared stage dependencies after the configured delay.
    replenished_output: Dict[str, int] = {}
    for stage, actions in sorted(scheduled_stage.items()):
        for t, upstream_ci, _amount, output in actions:
            upstream = cands[upstream_ci]
            dep_cis = sorted(
                (ci for ci, cand in enumerate(cands)
                 if cand.kind == "process"
                 and cand.depends_on_stage == stage
                 and cand.source_space == upstream.target_space),
                key=lambda ci: (cands[ci].transfer_id, cands[ci].machine))
            if not dep_cis:
                continue
            dep_ci = dep_cis[0]
            dep = cands[dep_ci]
            lag = max(
                1 + (
                    max(dep.dependency_delay_min, 0)
                    + max(itn(dt * 60), 1) - 1
                ) // max(itn(dt * 60), 1),
                1 if dep.source_space in no_passthrough_spaces else 0)
            td = first_reservable(dep_ci, output, t + lag)
            if td is None:
                continue
            reserve(dep_ci, td, output)
            replenished_output[dep.target_space] = \
                replenished_output.get(dep.target_space, 0) + \
                process_output(dep_ci, output)

    # 3) Replenish every targeted process source through its explicit upstream
    # transfer.  Terminal-min policies then start from a materially balanced
    # hint instead of relying on CP-SAT to discover the chain from zero.
    source_consumption: Dict[str, int] = {}
    scheduled_output: Dict[str, int] = {}
    for actions in scheduled_stage.values():
        for _t, ci, amount, output in actions:
            source = cands[ci].source_space
            source_consumption[source] = \
                source_consumption.get(source, 0) + amount
            target = cands[ci].target_space
            scheduled_output[target] = \
                scheduled_output.get(target, 0) + output
    for source, total in sorted(source_consumption.items()):
        total = max(
            total - scheduled_output.get(source, 0)
            - replenished_output.get(source, 0),
            0,
        )
        upstream_cis = sorted(
            (ci for ci, cand in enumerate(cands)
             if cand.kind == "process" and cand.target_space == source
             and not cand.depends_on_stage),
            key=lambda ci: (cands[ci].transfer_id, cands[ci].machine))
        if not upstream_cis:
            continue
        ci = upstream_cis[0]
        for amount in flow_chunks(total, cand_cap(ci), cand_lo(ci)):
            t = first_reservable(ci, amount)
            if t is None:
                break
            reserve(ci, t, amount)

    # 4) Dispatch configured corridor tonnage in source-balanced chunks and
    # reserve an equal receiving-stack action at the delayed arrival bucket.
    for link_id, raw_target in sorted(effective_corridor_targets(sc).items()):
        remaining = max(itn(float(raw_target)), 0)
        dispatch_cis = sorted(
            (ci for ci, cand in enumerate(cands)
             if cand.kind == "dispatch" and cand.dest_id == link_id),
            key=lambda ci: (
                0 if replenished_output.get(cands[ci].space, 0) > 0 else 1,
                cands[ci].space, cands[ci].machine))
        # one deterministic candidate per physical source avoids duplicate
        # alternatives when several machines can reach the same pile
        selected: Dict[str, int] = {}
        for ci in dispatch_cis:
            selected.setdefault(cands[ci].space, ci)
        for source, ci in selected.items():
            if remaining <= 0:
                break
            backed = replenished_output.get(source, 0)
            quota = min(remaining, backed if backed > 0 else remaining)
            cand = cands[ci]
            try:
                lag = sc.link(link_id).transit_buckets
            except StopIteration:
                continue
            prior_arrivals = [
                tt for (exit_node, _grade, tt), amount in arr_const.items()
                if exit_node == cand.exit_node and amount > 0
            ]
            arrival_start = max(prior_arrivals, default=-1) + 1
            dispatch_start = max(arrival_start - lag, 0)
            for amount in flow_chunks(quota, cand_cap(ci), cand_lo(ci)):
                paired = False
                for t in range(dispatch_start, max(T - lag, dispatch_start)):
                    arrival_t = t + lag
                    if not can_reserve(ci, t, amount):
                        continue
                    stack_cis = sorted({
                        group_members[gk][0]
                        for gk in group_keys
                        if gk[0] == "stack" and gk[5] == cand.exit_node
                        and gk[4] == cand.grade
                    }, key=lambda idx: (
                        cands[idx].machine, cands[idx].space))
                    stack_ci = next((
                        sci for sci in stack_cis
                        if can_reserve(sci, arrival_t, amount)), None)
                    if stack_ci is None:
                        continue
                    reserve(ci, t, amount)
                    reserve(stack_ci, arrival_t, amount)
                    key = (cand.exit_node, cand.grade, arrival_t)
                    policy_arrivals[key] = \
                        policy_arrivals.get(key, 0) + amount
                    paired = True
                    break
                if not paired:
                    break
            remaining -= quota

    delivered_h: Dict[Tuple[str, int], int] = {}
    stacked_h: Dict[Tuple[str, str, int], int] = {}   # (exit, grade, t) -> placed
    ins_h: Dict[Tuple[str, int], int] = {}
    outs_h: Dict[Tuple[str, int], int] = {}
    rr = 0
    for t in range(T):
        active_space: Dict[str, str] = {}
        # 0) operator-fixed jobs take precedence
        for (gk, fw0, fw1, rate_t) in fixed_windows:
            if fw0 <= t <= fw1 and not used_mach.get((gk[1], t)):
                amt = rate_t if rate_t > 0 else \
                    max(itn(sc.machine(gk[1]).rate_min_tph * dt), 1)
                placed = assign_flow(gk, t, amt)
                if placed > 0:
                    active_space[gk[1]] = gk[2]
                    if gk[0] == "reclaim":
                        delivered_h[(gk[3], t)] = \
                            delivered_h.get((gk[3], t), 0) + placed
                        outs_h[(gk[2], t)] = outs_h.get((gk[2], t), 0) + placed
                    elif gk[0] == "stack":
                        ins_h[(gk[2], t)] = ins_h.get((gk[2], t), 0) + placed
        # 1) configuration-backed internal process / corridor policy
        for ci, amount in policy_actions.get(t, ()):
            cand = cands[ci]
            gk = g_of[ci]
            placed = assign_flow(gk, t, amount)
            if placed <= 0:
                continue
            active_space[cand.machine] = cand.space
            if cand.kind == "process":
                output = process_output(ci, placed)
                hint_y[(ci, t)] = output
                outs_h[(cand.source_space, t)] = \
                    outs_h.get((cand.source_space, t), 0) + placed
                ins_h[(cand.target_space, t)] = \
                    ins_h.get((cand.target_space, t), 0) + output
                for destination in sc.destinations:
                    if _candidate_serves_destination(
                            sc, cand, destination):
                        key = (destination.id, t)
                        delivered_h[key] = delivered_h.get(key, 0) + placed
            elif cand.kind == "dispatch":
                outs_h[(cand.space, t)] = \
                    outs_h.get((cand.space, t), 0) + placed
            elif cand.kind == "stack":
                ins_h[(cand.space, t)] = \
                    ins_h.get((cand.space, t), 0) + placed
                stacked_h[(cand.exit_node, cand.grade, t)] = \
                    stacked_h.get((cand.exit_node, cand.grade, t), 0) + placed
        # 2) stack constant arrivals round-robin over free stackers
        for (exit_node, g, tt), arr in sorted(arr_const.items()):
            if tt != t or arr <= 0:
                continue
            remaining = arr - stacked_h.get((exit_node, g, t), 0)
            sgs = sorted([gk for gk in group_keys if gk[0] == "stack"
                          and gk[5] == exit_node and gk[4] == g],
                         key=lambda gk: (gk[1], gk[2]))
            for k in range(len(sgs)):
                if remaining <= 0:
                    break
                gk = sgs[(rr + k) % max(len(sgs), 1)]
                free = itn(sc.space(gk[2]).capacity_t) - inv_h.get(gk[2], 0) \
                    - ins_h.get((gk[2], t), 0)
                placed = assign_flow(gk, t, min(remaining, max(free, 0)))
                if placed > 0:
                    active_space[gk[1]] = gk[2]
                    stacked_h[(exit_node, g, t)] = \
                        stacked_h.get((exit_node, g, t), 0) + placed
                    ins_h[(gk[2], t)] = ins_h.get((gk[2], t), 0) + placed
                    remaining -= placed
                    rr += 1
        # 3) reclaim to destinations by priority, best-quality-fit space first
        for d in sorted(sc.destinations, key=lambda d_: d_.priority):
            if getattr(d, "demand_kind", "delivery") == "process_feed":
                continue
            need = itn(sc.demand_tph(d.id, t + opts.horizon_offset) * dt) \
                - delivered_h.get((d.id, t), 0)
            if need <= 0:
                continue
            rgs = [gk for gk in group_keys if gk[0] == "reclaim" and gk[3] == d.id]

            def fit(gk):
                q = sc.quality_of(gk[2])
                pen = 0.0
                for s_ in d.spec:
                    v = q.get(s_.param)
                    if v is None:
                        continue
                    if s_.pmin is not None and v < s_.pmin:
                        pen += (s_.pmin - v) * 10
                    if s_.pmax is not None and v > s_.pmax:
                        pen += (v - s_.pmax) * 10
                return (pen, -inv_h.get(gk[2], 0), gk[1], gk[2])
            for gk in sorted(rgs, key=fit):
                if need <= 0:
                    break
                hard_bad = False
                q = sc.quality_of(gk[2])
                for s_ in d.spec:
                    if s_.hard and q.get(s_.param) is not None:
                        if (s_.pmin is not None and q[s_.param] < s_.pmin) or \
                                (s_.pmax is not None and q[s_.param] > s_.pmax):
                            hard_bad = True
                if hard_bad:
                    continue
                avail = inv_h.get(gk[2], 0) - outs_h.get((gk[2], t), 0)
                if avail <= 1:
                    continue
                placed = assign_flow(gk, t, min(need, avail))
                if placed > 0:
                    active_space[gk[1]] = gk[2]
                    delivered_h[(d.id, t)] = delivered_h.get((d.id, t), 0) + placed
                    outs_h[(gk[2], t)] = outs_h.get((gk[2], t), 0) + placed
                    need -= placed
        prev_space_h.clear()
        prev_space_h.update(active_space)
        # end-of-bucket inventory roll (matches the model equation exactly)
        for sp in sc.spaces:
            exo_in = exo_out = 0
            for e in sc.exogenous:
                if e.start_bucket <= t + opts.horizon_offset <= e.end_bucket:
                    if e.target_space == sp.id:
                        exo_in += itn(e.rate_tph * dt)
                    if e.source_space == sp.id:
                        exo_out += itn(e.rate_tph * dt)
            nxt = inv_h[sp.id] + ins_h.get((sp.id, t), 0) + exo_in \
                - outs_h.get((sp.id, t), 0) - exo_out
            spill_h = max(nxt - itn(sp.capacity_t), 0)
            m.add_hint(spill_map[(sp.id, t)], spill_h)
            inv_h[sp.id] = max(nxt - spill_h, 0)
            m.add_hint(inv[(sp.id, t + 1)], inv_h[sp.id])

    # complete the hint for every remaining variable family
    for gk in group_keys:
        for t in range(T):
            m.add_hint(AG[(gk, t)], hint_ag.get((gk, t), 0))
    for ci in range(len(cands)):
        for t in range(T):
            m.add_hint(X[(ci, t)], hint_x.get((ci, t), 0))
            if (ci, t) in Y and Y[(ci, t)] is not X[(ci, t)]:
                m.add_hint(Y[(ci, t)], hint_y.get((ci, t), 0))
    for d in sc.destinations:
        if getattr(d, "demand_kind", "delivery") == "process_feed":
            dem = sum(
                itn(sc.demand_tph(
                    d.id, t + opts.horizon_offset) * dt)
                for t in range(T)
            )
            got = sum(delivered_h.get((d.id, t), 0) for t in range(T))
            m.add_hint(unmet[(d.id, 0)], max(dem - got, 0))
        else:
            for t in range(T):
                dem = itn(sc.demand_tph(
                    d.id, t + opts.horizon_offset) * dt)
                got = delivered_h.get((d.id, t), 0)
                m.add_hint(unmet[(d.id, t)], max(dem - got, 0))
    for (kind_, did, param, t), svar in qslack_map.items():
        d = sc.destination(did)
        spec = next(s_ for s_ in d.spec if s_.param == param)
        got = delivered_h.get((did, t), 0)
        lhs = 0
        for ci in demand_cis[did]:
            if (ci, t) in hint_x:
                lhs += itn(sc.quality_of(
                    _candidate_quality_space(cands[ci])
                ).get(param, 0.0) * QSCALE) * hint_x[(ci, t)]
        if kind_ == "min":
            bound = itn((spec.pmin + spec.margin) * QSCALE)
            m.add_hint(svar, min(max(bound * got - lhs, 0), QSLACK_MAX))
        else:
            bound = itn(max(spec.pmax - spec.margin, 0) * QSCALE)
            m.add_hint(svar, min(max(lhs - bound * got, 0), QSLACK_MAX))
    for (exit_node, g, t), svar in surge_map.items():
        arrived = (arr_const.get((exit_node, g, t), 0)
                   + policy_arrivals.get((exit_node, g, t), 0))
        m.add_hint(
            svar, max(arrived - stacked_h.get((exit_node, g, t), 0), 0))
    for (gname, li, t), b in conf_map.items():
        m.add_hint(b, 1 if link_load_h.get((li, t), 0) > 0 else 0)
    at_h: Dict[Tuple[str, str, int], int] = {}
    for (gk, t), v in hint_ag.items():
        if v:
            at_h[(gk[1], gk[2], t)] = 1
    for (mid, s, t), dv in diff_map.items():
        m.add_hint(dv, abs(at_h.get((mid, s, t), 0) - at_h.get((mid, s, t - 1), 0)))
    # safety-stock shortfall hints: re-walk the hinted inventory chain
    inv_walk = {sp.id: [min(itn(sp.mass_t), itn(sp.capacity_t))]
                for sp in sc.spaces}
    for t in range(T):
        for sp in sc.spaces:
            exo_in = exo_out = 0
            for e in sc.exogenous:
                if e.start_bucket <= t + opts.horizon_offset <= e.end_bucket:
                    if e.target_space == sp.id:
                        exo_in += itn(e.rate_tph * dt)
                    if e.source_space == sp.id:
                        exo_out += itn(e.rate_tph * dt)
            nxt = inv_walk[sp.id][-1] + ins_h.get((sp.id, t), 0) + exo_in \
                - outs_h.get((sp.id, t), 0) - exo_out
            nxt = max(min(nxt, itn(sp.capacity_t)), 0)
            inv_walk[sp.id].append(nxt)
    for (sid, tt), svar in short.items():
        tgt = itn(sc.space(sid).safety_stock_t)
        m.add_hint(svar, max(tgt - inv_walk[sid][tt], 0))
    # same-pile concurrency intermediate totals (hinted from the greedy walk)
    for (spid, t), (_, o_tot, s_tot) in conc_vars.items():
        if o_tot is not None:
            m.add_hint(o_tot, outs_h.get((spid, t), 0))
        if s_tot is not None:
            m.add_hint(s_tot, ins_h.get((spid, t), 0))

    # ---------- objective ---------------------------------------------------------------
    w = sc.weights
    terms = []
    terms += [itn(w.w1_unmet * WSCALE) * u for u in unmet.values()]
    terms += [max(itn(w.w2_offspec * WSCALE / QSCALE), 1) * s for s in qslack]
    terms += [itn(w.w5_moves * WSCALE) * d_ for d_ in diffs]
    terms += [itn(w.w6_safety * WSCALE) * s for s in short.values()]
    terms += [BIG_PENALTY * s for svs in surge_slacks.values() for s in svs]
    terms += [BIG_PENALTY * s for s in spill]
    # Small positive cost on every commanded tonne removes arbitrary
    # dispatch/transfer ties.  It is deliberately subordinate to unmet demand
    # and safety penalties; configured targets still force their exact useful
    # flow.
    flow_tonne_penalty = max(
        int(sc.config.get("FLOW_TONNE_PENALTY", 0) or 0), 0)
    if flow_tonne_penalty:
        terms += [flow_tonne_penalty * x for x in X.values()]
    # Secondary tie-breaker only: prefer assigning justified demand/
    # replenishment work across planning-eligible machines. Exact destination
    # demand and the terminal supply-position ceiling above prevent this
    # reward from manufacturing surplus tonnes just to activate equipment.
    coverage_reward = max(
        int(sc.config.get("MACHINE_COVERAGE_REWARD", 0) or 0), 0)
    if coverage_reward:
        terms += [-coverage_reward * used for used in machine_used.values()]
    if w.w4_giveaway > 0:
        for ci, c in enumerate(cands):
            if c.kind != "reclaim":
                continue
            d = sc.destination(c.dest_id)
            tgt = next((s.target for s in d.spec if s.param == "BPL" and s.target),
                       None)
            if tgt is None:
                continue
            give = max(sc.quality_of(c.space).get("BPL", 0.0) - tgt, 0.0)
            coef = itn(w.w4_giveaway * WSCALE * give / 10)
            if coef > 0:
                for t in range(T):
                    terms.append(coef * X[(ci, t)])
    if w.w7_utilisation > 0:
        for key, x in X.items():
            terms.append(-itn(w.w7_utilisation * WSCALE / 10) * x)
    m.minimize(sum(terms))

    _stage("solving CP-SAT")
    budget = float(opts.max_time_s or sc.sim.max_solve_s)
    solver = cp_model.CpSolver()
    # Deterministic replay (acceptance criterion #11): single worker, fixed seed
    # and a DETERMINISTIC-time budget. A wall-clock cutoff would stop the search
    # at a machine-load-dependent point and change the returned plan.
    solver.parameters.num_workers = 1
    solver.parameters.max_deterministic_time = budget
    # hang-guard ONLY — never a pacing device: any wall cutoff that fires
    # before the deterministic budget truncates at a machine-load-dependent
    # point and silently breaks plan reproducibility (observed twice,
    # 15/16-Jul-2026, when solves ran under parallel CPU load)
    solver.parameters.max_time_in_seconds = budget * 100
    solver.parameters.random_seed = int(sc.sim.seed)
    status = solver.solve(m)
    wall = time.time() - t0
    sname = {cp_model.OPTIMAL: "OPTIMAL", cp_model.FEASIBLE: "FEASIBLE",
             cp_model.INFEASIBLE: "INFEASIBLE", cp_model.MODEL_INVALID: "ERROR",
             cp_model.UNKNOWN: "UNKNOWN"}.get(status, "ERROR")

    res = PlanResult(status=sname, solver="cp-sat", solver_wall_s=round(wall, 3),
                     horizon_offset=opts.horizon_offset)
    if fixed_missing:
        res.reasons.append("FIXED_JOB_UNMATCHED: no candidate tuple for fixed job(s) "
                           + ", ".join(fixed_missing) + " — check reachability and routes")
    if sname in ("INFEASIBLE", "ERROR", "UNKNOWN"):
        res.reasons.append(f"SOLVER_{sname}: check fixed jobs vs calendars, and route "
                           "availability; demand itself cannot cause infeasibility "
                           "(unmet slack is always present)")
        res.relaxations += ["Remove or shorten operator-fixed jobs",
                            "Re-enable unavailable machines/links",
                            "Reduce frozen-horizon length"]
        return res

    _stage("preparing results")
    # ---------- extraction (one job per group-run) ----------------------------------
    val = solver.value
    jobs: List[Job] = []
    jid = 0
    for gk in group_keys:
        members = group_members[gk]
        c0 = cands[members[0]]
        t = 0
        while t < T:
            tot_t = sum(val(X[(ci, t)]) for ci in members)
            if val(AG[(gk, t)]) == 1 and tot_t > 0:
                t1 = t
                tot = 0
                output_tot = 0
                link_flow: Dict[Tuple[str, ...], int] = {}
                bucket_flows: List[JobBucketFlow] = []
                fixed_job = _fixed_for_bucket(
                    sc, gk, t + opts.horizon_offset)
                fixed_id = fixed_job.id if fixed_job is not None else None
                while t1 < T and val(AG[(gk, t1)]) == 1 and \
                        sum(val(X[(ci, t1)]) for ci in members) > 0 and \
                        ((_fixed_for_bucket(
                            sc, gk, t1 + opts.horizon_offset).id
                          if _fixed_for_bucket(
                              sc, gk, t1 + opts.horizon_offset) is not None
                          else None) == fixed_id):
                    for ci in members:
                        v = val(X[(ci, t1)])
                        if v > 0:
                            output_v = (val(Y[(ci, t1)])
                                        if (ci, t1) in Y else v)
                            path = (list(cands[ci].path.links)
                                    if cands[ci].path else [])
                            bucket_flows.append(JobBucketFlow(
                                bucket=t1 + opts.horizon_offset,
                                tonnage_t=float(v),
                                path=path,
                                output_tonnage_t=float(output_v)))
                            if cands[ci].path:
                                key = tuple(cands[ci].path.links)
                                link_flow[key] = link_flow.get(key, 0) + v
                            output_tot += output_v
                        tot += v
                    t1 += 1
                sp = sc.space(gk[2])
                # x-cut convention: reclaim cuts sized against the START-OF-RUN
                # inventory the solver itself computed (uniform-density
                # approximation shared with the ASP inventory rule) — NOT the
                # scenario snapshot, which is stale for piles filled by
                # in-horizon stacking (e.g. initially-empty HP-07/08) and
                # would draw a full-lane reclaim face colliding with the
                # concurrent stacking zone. Stack jobs are sized against
                # capacity (they add material at the high-x end).
                display_kind = c0.display_kind()
                basis = sp.capacity_t if display_kind == "stack" \
                    else max(float(val(inv[(sp.id, t)])), 1.0)
                # 2 % headroom + ceil-rounding so the cut can never be a few
                # centimetres short of the planned tonnage (uniform density)
                import math as _math
                frac = min(tot / max(basis, 1) * 1.02, 1.0)
                cut = _math.ceil((sp.x_from + frac * (sp.x_to - sp.x_from)) * 10) / 10
                # work-zone convention for SAFE same-pile concurrency
                # (USER-CONFIRMED rule, 15-Jul-2026): reclaim/dispatch work the
                # LOW-x face; stacking builds from the HIGH-x end, so
                # concurrent zones sit at opposite ends with the separation
                # room guaranteed by the in-model condition above
                zone_len = max(cut - sp.x_from, 1.0)
                auto_start_x = (
                    round(max(sp.x_to - zone_len, sp.x_from), 1)
                    if display_kind == "stack" else round(sp.x_from, 1))
                auto_stop_x = (
                    round(sp.x_to, 1) if display_kind == "stack"
                    else max(cut, round(sp.x_from + 1.0, 1)))
                if fixed_job is not None and \
                        fixed_job.start_repere is not None and \
                        fixed_job.stop_repere is not None:
                    rx0, rx1 = repere_interval_to_x(
                        sp, fixed_job.start_repere, fixed_job.stop_repere)
                    start_x, stop_x = round(rx0, 3), round(rx1, 3)
                    range_note = " · " + format_repere_range(
                        fixed_job.start_repere, fixed_job.stop_repere)
                else:
                    start_x, stop_x = auto_start_x, auto_stop_x
                    range_note = ""
                # dominant path for execution; all used links recorded for routing
                dom = max(link_flow.items(), key=lambda kv: kv[1])[0] \
                    if link_flow else tuple()
                used_links = sorted({li for k in link_flow for li in k})
                # Persist the exact effective ceiling represented by CP-SAT's
                # integer tonnes/bucket constraint (including its rounding),
                # not the unrounded floating-point factor.
                execution_rate_cap = (
                    itn(sc.machine(gk[1]).rate_max_tph
                        * opts.machine_rate_factor.get(gk[1], 1.0) * dt)
                    / max(dt, 1e-9))
                if fixed_job is not None and fixed_job.rate_tph > 0:
                    execution_rate_cap = min(
                        execution_rate_cap,
                        itn(fixed_job.rate_tph * dt) / max(dt, 1e-9))
                jid += 1
                jobs.append(Job(
                    id=(fixed_job.id if fixed_job is not None
                        else f"J{opts.horizon_offset:03d}-{jid:03d}"),
                    machine=gk[1], kind=display_kind, space=gk[2],
                    dest_id=("" if c0.kind == "process" else gk[3]),
                    grade=((fixed_job.grade if fixed_job is not None else "")
                           or gk[4] or sp.grade),
                    rate_tph=round(tot / ((t1 - t) * dt), 1),
                    tonnage_t=float(tot),
                    start_bucket=t + opts.horizon_offset,
                    end_bucket=t1 - 1 + opts.horizon_offset,
                    path=list(dom),
                    method="chevron" if display_kind == "stack" else "bench",
                    origin="HUMAN" if fixed_job is not None else "AUTO",
                    locked=fixed_job is not None,
                    start_x=start_x, stop_x=stop_x,
                    execution_rate_cap_tph=float(execution_rate_cap),
                    bucket_flows=sorted(
                        bucket_flows,
                        key=lambda flow: (flow.bucket, tuple(flow.path))),
                    source_space=(c0.source_space if c0.kind == "process"
                                  else gk[2]
                                  if display_kind in ("reclaim", "dispatch")
                                  else ""),
                    target_space=(c0.target_space if c0.kind == "process"
                                  else gk[2] if display_kind == "stack"
                                  else gk[3]),
                    output_grade=(c0.output_grade if c0.kind == "process"
                                  else (gk[4] or sp.grade)),
                    output_tonnage_t=float(
                        output_tot if c0.kind == "process" else tot),
                    process_stage=c0.process_stage,
                    depends_on_stage=c0.depends_on_stage,
                    dependency_delay_min=c0.dependency_delay_min,
                    residence_min=c0.residence_min,
                    purpose=c0.purpose,
                    transfer_id=c0.transfer_id,
                    comment=(f"{'HUMAN' if fixed_job is not None else 'AUTO'} "
                             f"{display_kind} "
                             f"{c0.source_space or gk[2]}"
                             f" -> {c0.target_space or gk[3]}"
                            + (f" via {len(used_links)} links" if len(link_flow) > 1
                               else "")
                             + range_note)))
                t = t1
            else:
                t += 1
    res.jobs = jobs
    timing_refinement = refine_execution_timing(sc, jobs)

    for d in sc.destinations:
        res.unmet[d.id] = [
            float(_val_expr(solver, unmet[(d.id, t)]))
            for t in range(T)
        ]
        res.delivered[d.id] = [float(_val_expr(solver, delivered[(d.id, t)])) / dt
                               for t in range(T)]
    for d_id, slacks in qslack_by_dest.items():
        res.offspec[d_id] = [float(val(s)) / QSCALE for s in slacks]
    for sp in sc.spaces:
        res.inventory[sp.id] = [float(val(inv[(sp.id, t)])) for t in range(T + 1)]
        if sp.safety_stock_t > 0:
            res.safety_short[sp.id] = [float(val(short[(sp.id, t)]))
                                       for t in range(1, T + 1)]
    for stage in sorted({
            cand.process_stage for cand in cands
            if cand.kind == "process" and cand.process_stage}):
        res.process_reject[stage] = [
            float(sum(
                val(X[(ci, t)]) - val(Y[(ci, t)])
                for ci, cand in enumerate(cands)
                if cand.kind == "process"
                and cand.process_stage == stage
                and (ci, t) in Y
            ))
            for t in range(T)
        ]
    res.surge_unstacked = [float(sum(val(s) for s in surge_slacks[t]))
                           for t in range(T)]
    res.objective = float(solver.objective_value)
    res.breakdown = {
        "unmet_t": sum(sum(v) for v in res.unmet.values()),
        "offspec_t_unit": sum(sum(v) for v in res.offspec.values()),
        "safety_short_t": sum(sum(v) for v in res.safety_short.values()),
        "moves": float(sum(val(d_) for d_ in diffs)),
        "surge_t": sum(res.surge_unstacked),
        "spill_t": float(sum(val(s) for s in spill)),
        "process_reject_t": float(
            sum(sum(series) for series in res.process_reject.values())),
        "planning_eligible_machines": float(len(machine_used)),
        "planning_used_machines": float(
            sum(val(used) for used in machine_used.values())),
        "minute_timing_validated_windows": float(sum(
            1 for job in jobs for window in job.execution_windows
            if window.status == "VALIDATED")),
        "minute_timing_errors": float(sum(
            1 for issue in timing_refinement.issues
            if issue.severity == "ERROR")),
        "minute_timing_fallback_buckets": float(
            len(timing_refinement.fallback_buckets)),
    }
    eligible_count = int(res.breakdown["planning_eligible_machines"])
    used_count = int(res.breakdown["planning_used_machines"])
    res.breakdown["planning_machine_coverage_pct"] = (
        100.0 * used_count / eligible_count if eligible_count else 100.0)
    if terminal_supply_expr is not None:
        res.breakdown["terminal_supply_position_max_t"] = float(
            terminal_supply_max)
        res.breakdown["terminal_supply_position_t"] = float(
            _val_expr(solver, terminal_supply_expr))
        res.breakdown["terminal_corridor_in_transit_t"] = float(
            sum(val(term) for term in terminal_in_transit_terms)
            + terminal_initial_belt_t)
    _derive_reasons(sc, res, cands, dt)
    _derive_machine_dispositions(sc, res, cands)
    return res


def _val_expr(solver, expr):
    if isinstance(expr, int):
        return expr
    return solver.value(expr)


def _derive_reasons(sc: Scenario, res: PlanResult, cands: List[Cand], dt: float):
    """Heuristic reason codes + suggested relaxations for gaps (report ask)."""
    for d_id, series in res.unmet.items():
        tot = sum(series)
        if tot <= 0:
            continue
        d = sc.destination(d_id)
        process_feed = getattr(d, "demand_kind", "delivery") == "process_feed"
        feed_cands = [
            c for c in cands if _candidate_serves_destination(sc, c, d)
        ]
        if not feed_cands:
            route_kind = (
                f"process transfer for stage {d.process_stage}"
                if process_feed else
                "(machine, space, path) tuple"
            )
            res.reasons.append(
                f"UNMET_DEMAND[{d_id}] {tot:.0f} t: NO_ROUTE_OR_MACHINE — "
                f"no {route_kind} can feed this destination")
            res.relaxations.append(
                f"{d_id}: add an executable process transfer"
                if process_feed else
                f"{d_id}: add reachability or route links to node {d.node_id}")
            continue
        mach_cap = sum(sc.machine(mid).rate_max_tph
                       for mid in {c.machine for c in feed_cands})
        path_cap = sum({tuple(c.path.links): c.path.capacity_tph
                        for c in feed_cands if c.path}.values())
        avg_dem = sum(sc.demand_tph(d_id, t) for t in range(sc.sim.horizon_buckets)) \
            / sc.sim.horizon_buckets
        source_spaces = {
            c.source_space if process_feed else c.space
            for c in feed_cands
        }
        inv_avail = sum(sc.space(sid).mass_t for sid in source_spaces)
        need_t = avg_dem * sc.sim.horizon_buckets * dt
        cause = []
        if avg_dem > mach_cap:
            cause.append(f"machine capacity {mach_cap:.0f} tph < demand {avg_dem:.0f} tph")
        if avg_dem > path_cap:
            cause.append(f"route bottleneck {path_cap:.0f} tph < demand {avg_dem:.0f} tph")
        if inv_avail < need_t:
            cause.append(f"available inventory {inv_avail:.0f} t < required {need_t:.0f} t")
        hard_specs = [s.param for s in d.spec if s.hard]
        if hard_specs and not cause:
            cause.append(f"hard quality spec on {'/'.join(hard_specs)} restricts usable blend")
        if not cause:
            cause.append("competing destinations / calendars / conflicts within horizon")
        res.reasons.append(f"UNMET_DEMAND[{d_id}] {tot:.0f} t: " + "; ".join(cause))
        res.relaxations.append(f"{d_id}: " + "; ".join(
            {"machine": "assign more machines or raise rates",
             "route": "raise link capacity / enable parallel path",
             "available": "increase mine dispatch or initial stock",
             "hard": "convert hard spec to soft (w2) or widen the band"}.get(
                c.split(" ")[0], "review demand profile") for c in cause))
    if res.breakdown.get("surge_t", 0) > 0:
        res.reasons.append(f"UNSTACKED_ARRIVALS {res.breakdown['surge_t']:.0f} t: corridor "
                           "arrivals exceed stacking capacity — belts cannot hold material")
        res.relaxations.append("Reduce mine dispatch, free a stacker, or add stack routes")
    if res.breakdown.get("spill_t", 0) > 0:
        res.reasons.append(f"SPACE_OVERFLOW {res.breakdown['spill_t']:.0f} t: exogenous "
                           "inflow exceeds space capacity")


def _derive_machine_dispositions(
        sc: Scenario, res: PlanResult, cands: List[Cand]) -> None:
    """One structured, evidence-neutral disposition per solver machine."""
    jobs_by_machine: Dict[str, List[Job]] = {}
    cands_by_machine: Dict[str, List[Cand]] = {}
    for job in res.jobs:
        jobs_by_machine.setdefault(job.machine, []).append(job)
    for cand in cands:
        cands_by_machine.setdefault(cand.machine, []).append(cand)

    exclusive_peers: Dict[str, set] = {}
    for site in sc.sites:
        for group in getattr(site, "exclusive_machine_groups", []) or []:
            for machine_id in group:
                exclusive_peers.setdefault(machine_id, set()).update(
                    peer for peer in group if peer != machine_id)

    policy_enabled = bool(
        sc.config.get("LOU_S0_STOCKED_ROUTE_ENABLED", False))
    for machine in sc.machines:
        machine_jobs = jobs_by_machine.get(machine.id, [])
        machine_cands = cands_by_machine.get(machine.id, [])
        input_spaces = sorted({
            cand.input_space() for cand in machine_cands
            if cand.input_space()})
        output_spaces = sorted({
            cand.output_space() for cand in machine_cands
            if cand.output_space()})
        inventory_t = sum(
            res.inventory.get(space_id, [sc.space(space_id).mass_t])[0]
            for space_id in input_spaces)
        free_capacity_t = sum(
            max(sc.space(space_id).capacity_t
                - res.inventory.get(
                    space_id, [sc.space(space_id).mass_t])[-1], 0.0)
            for space_id in output_spaces)
        routes = sorted({
            " → ".join(cand.path.links)
            for cand in machine_cands if cand.path and cand.path.links})
        triggers = sorted({
            cand.purpose for cand in machine_cands if cand.purpose})
        base = {
            "solver_controlled": bool(
                machine.available
                and getattr(machine, "planning_enabled", True)),
            "reachable_spaces": list(machine.reachable_spaces),
            "route": " | ".join(routes),
            "inventory_t": float(inventory_t),
            "free_capacity_t": float(free_capacity_t),
            "job_trigger": " | ".join(triggers),
        }
        if machine_jobs:
            tonnes = sum(job.tonnage_t for job in machine_jobs)
            base.update({
                "code": "SCHEDULED",
                "reason": (f"{len(machine_jobs)} purposeful job(s), "
                           f"{tonnes:,.0f} t input"),
                "classification": "jobs generated",
            })
        elif not machine.available:
            base.update({
                "code": "UNAVAILABLE",
                "reason": (
                    "machine disabled/unavailable in the current scenario "
                    "configuration"),
                "classification": "legitimate idle",
            })
        elif not getattr(machine, "planning_enabled", True):
            base.update({
                "code": "OUTSIDE_PLANNING_SCOPE",
                "reason": "equipment is not enabled for solver scheduling",
                "classification": "modelling gap",
            })
        elif not machine_cands:
            base.update({
                "code": "MISSING_ROUTE_DATA",
                "reason": "no valid machine, stockpile and route candidate",
                "classification": "modelling gap",
            })
        elif input_spaces and inventory_t <= 0:
            base.update({
                "code": "NO_AVAILABLE_INVENTORY",
                "reason": "no source inventory is available",
                "classification": "legitimate idle",
            })
        elif output_spaces and free_capacity_t <= 0:
            base.update({
                "code": "NO_FREE_CAPACITY",
                "reason": "all reachable targets are full",
                "classification": "legitimate idle",
            })
        elif any(peer in jobs_by_machine
                 for peer in exclusive_peers.get(machine.id, set())):
            base.update({
                "code": "STANDBY_SHARED_ROUTE",
                "reason": "a mutually exclusive peer supplies the shared route",
                "classification": "legitimate idle",
            })
        elif machine.site == "LOUTA" and not policy_enabled:
            base.update({
                "code": "NO_DEMAND_OR_REPLENISHMENT_TARGET",
                "reason": "no stocked-route source allocation is configured",
                "classification": "legitimate idle",
            })
        else:
            base.update({
                "code": "NOT_SELECTED_BY_PLAN",
                "reason": "demand and configured targets are met without it",
                "classification": "legitimate idle",
            })
        res.machine_dispositions[machine.id] = base


def blend_fractions(res: PlanResult, dest_id: str, bucket: int) -> Dict[str, float]:
    """Derived reporting quantity f = x / delivered (report §15.1) — from jobs."""
    flows: Dict[str, float] = {}
    for j in res.jobs:
        if j.kind == "reclaim" and j.dest_id == dest_id and \
                j.start_bucket <= bucket <= j.end_bucket:
            flows[j.space] = flows.get(j.space, 0.0) + j.rate_tph
    tot = sum(flows.values())
    return {s: v / tot for s, v in flows.items()} if tot else {}
