"""Input validation — layer 2. Produces a typed issue list (never raises)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List

from .coordinates import REPERE_SECTION_COUNT, repere_interval_to_x
from .domain import Scenario
from .flow_config import (PROCESS_LINE_ENABLED_CONFIG,
                          effective_corridor_targets,
                          effective_process_stage_targets,
                          process_line_modelled, process_stage_enabled)
from .routes import RouteGraph, space_node


@dataclass
class Issue:
    severity: str      # ERROR | WARNING | INFO
    code: str
    message: str
    entity: str = ""


def validate_scenario(sc: Scenario) -> List[Issue]:
    issues: List[Issue] = []
    err = lambda c, m, e="": issues.append(Issue("ERROR", c, m, e))
    warn = lambda c, m, e="": issues.append(Issue("WARNING", c, m, e))
    info = lambda c, m, e="": issues.append(Issue("INFO", c, m, e))

    space_ids = {s.id for s in sc.spaces}
    grade_ids = {g.id for g in sc.grades}
    node_ids = {n.id for n in sc.nodes}
    dest_ids = {d.id for d in sc.destinations}
    machine_ids = {m.id for m in sc.machines}
    site_ids = {s.id for s in sc.sites}
    profile_ids = {p.id for p in sc.material_profiles}

    # duplicates
    for name, ids in [("space", [s.id for s in sc.spaces]),
                      ("grade", [g.id for g in sc.grades]),
                      ("material profile", [p.id for p in sc.material_profiles]),
                      ("machine", [m.id for m in sc.machines]),
                      ("process transfer",
                       [p.id for p in sc.process_transfers]),
                      ("link", [l.id for l in sc.links]),
                      ("node", [n.id for n in sc.nodes]),
                      ("destination", [d.id for d in sc.destinations])]:
        dupes = {i for i in ids if ids.count(i) > 1}
        for d in dupes:
            err("DUPLICATE_ID", f"duplicate {name} id '{d}'", d)

    # spaces
    for s in sc.spaces:
        if s.site not in site_ids:
            err("UNKNOWN_SITE", f"space {s.id}: unknown site '{s.site}'", s.id)
        if s.capacity_t <= 0:
            err("BAD_CAPACITY", f"space {s.id}: capacity must be > 0 t", s.id)
        if s.mass_t < 0:
            err("NEGATIVE_MASS", f"space {s.id}: negative inventory", s.id)
        if s.mass_t > s.capacity_t * 1.001:
            err("OVER_CAPACITY", f"space {s.id}: mass {s.mass_t:.0f} t exceeds capacity "
                f"{s.capacity_t:.0f} t", s.id)
        if s.grade and s.grade not in grade_ids:
            err("UNKNOWN_GRADE", f"space {s.id}: unknown grade '{s.grade}'", s.id)
        if s.profile_id and s.profile_id not in profile_ids:
            err("UNKNOWN_PROFILE", f"space {s.id}: unknown material profile "
                f"'{s.profile_id}'", s.id)
        profile = sc.material_profile(s.profile_id) if s.profile_id else None
        grade = sc.grade(s.grade) if s.grade else None
        if profile and profile.site != s.site:
            err("PROFILE_SITE_MISMATCH", f"space {s.id}: profile {profile.id} "
                f"belongs to {profile.site}, not {s.site}", s.id)
        if profile and s.material_class and profile.material_class != s.material_class:
            err("PROFILE_CLASS_MISMATCH", f"space {s.id}: material class "
                f"'{s.material_class}' conflicts with profile {profile.id} "
                f"class '{profile.material_class}'", s.id)
        if grade and s.material_class and grade.material_class and \
                grade.material_class != s.material_class:
            err("GRADE_CLASS_MISMATCH", f"space {s.id}: grade {grade.id} is "
                f"'{grade.material_class}', not '{s.material_class}'", s.id)
        if profile and grade and profile.grade_ids and \
                grade.id not in profile.grade_ids:
            err("PROFILE_GRADE_MISMATCH", f"space {s.id}: grade {grade.id} "
                f"is not declared for profile {profile.id}", s.id)
        if not s.quality and not (s.grade and sc.grade(s.grade)):
            warn("NO_QUALITY", f"space {s.id}: no quality vector and no grade lookup — "
                 "quality constraints will treat it as 0", s.id)
        if s.safety_stock_t > s.capacity_t:
            warn("SAFETY_GT_CAP", f"space {s.id}: safety stock exceeds capacity", s.id)
        if s.x_to <= s.x_from:
            err("BAD_XRANGE", f"space {s.id}: x_to must exceed x_from", s.id)
        try:
            rep_from = int(getattr(s, "repere_at_x_from", 0))
            rep_to = int(getattr(s, "repere_at_x_to", 1000))
        except (TypeError, ValueError, OverflowError):
            err("BAD_REPERE_FRAME", f"space {s.id}: repère endpoints must "
                "be integer boundaries R0 and R1000", s.id)
        else:
            rep_span = abs(rep_to - rep_from)
            if rep_span != REPERE_SECTION_COUNT or \
                    {rep_from, rep_to} != {0, REPERE_SECTION_COUNT}:
                err("BAD_REPERE_FRAME", f"space {s.id}: repère endpoints "
                    f"must be R0/R{REPERE_SECTION_COUNT} (either direction) "
                    f"and define exactly {REPERE_SECTION_COUNT} equal "
                    "sections", s.id)

    # material profiles / stage-specific grade families
    for g in sc.grades:
        if g.profile_id and g.profile_id not in profile_ids:
            err("UNKNOWN_PROFILE", f"grade {g.id}: unknown material profile "
                f"'{g.profile_id}'", g.id)
        gp = sc.material_profile(g.profile_id) if g.profile_id else None
        if gp and g.material_class and gp.material_class != g.material_class:
            err("PROFILE_CLASS_MISMATCH", f"grade {g.id}: class "
                f"'{g.material_class}' conflicts with profile {gp.id} class "
                f"'{gp.material_class}'", g.id)

    for p in sc.material_profiles:
        if p.site not in site_ids:
            err("UNKNOWN_SITE", f"material profile {p.id}: unknown site "
                f"'{p.site}'", p.id)
        if not p.material_class:
            err("NO_MATERIAL_CLASS", f"material profile {p.id}: class is empty", p.id)
        if not p.grade_ids:
            warn("NO_PROFILE_GRADES", f"material profile {p.id}: no grade "
                 "family declared", p.id)
        for gid in p.grade_ids:
            g = sc.grade(gid)
            if not g:
                err("UNKNOWN_GRADE", f"material profile {p.id}: unknown grade "
                    f"'{gid}'", p.id)
            elif g.material_class and g.material_class != p.material_class:
                err("GRADE_CLASS_MISMATCH", f"material profile {p.id}: grade "
                    f"{gid} class '{g.material_class}' conflicts with "
                    f"'{p.material_class}'", p.id)

    # machines
    for m in sc.machines:
        if m.site not in site_ids:
            err("UNKNOWN_SITE", f"machine {m.id}: unknown site '{m.site}'", m.id)
        if m.rate_max_tph <= 0:
            err("BAD_RATE", f"machine {m.id}: rate_max must be > 0", m.id)
        if m.rate_min_tph > m.rate_max_tph:
            err("BAD_RATE", f"machine {m.id}: min stable rate exceeds max rate", m.id)
        if not math.isfinite(float(m.travel_speed_m_min)) or \
                m.travel_speed_m_min <= 0:
            err("BAD_TRAVEL_SPEED", f"machine {m.id}: travel speed must be "
                "finite and > 0 m/min", m.id)
        if not math.isfinite(float(m.setup_min)) or m.setup_min < 0:
            err("BAD_SETUP_TIME", f"machine {m.id}: setup time must be "
                "finite and non-negative", m.id)
        if not m.reachable_spaces:
            warn("NO_REACH", f"machine {m.id}: no reachable spaces configured — it can "
                 "never be allocated", m.id)
        for sid in m.reachable_spaces:
            if sid not in space_ids:
                err("UNKNOWN_SPACE", f"machine {m.id}: reachable space '{sid}' unknown", m.id)

    # links / graph
    for l in sc.links:
        if l.src not in node_ids:
            err("UNKNOWN_NODE", f"link {l.id}: unknown src node '{l.src}'", l.id)
        if l.dst not in node_ids:
            err("UNKNOWN_NODE", f"link {l.id}: unknown dst node '{l.dst}'", l.id)
        if l.capacity_tph <= 0:
            err("BAD_CAPACITY", f"link {l.id}: capacity must be > 0 tph", l.id)
        if l.min_stable_tph > l.capacity_tph:
            err("BAD_RATE", f"link {l.id}: min stable exceeds capacity", l.id)
        if l.transit_buckets < 0:
            err("BAD_DELAY", f"link {l.id}: negative transit", l.id)

    # explicit process transfers: no geometry-derived shortcuts
    link_by_id = {link.id: link for link in sc.links}
    for transfer in sc.process_transfers:
        if transfer.machine not in machine_ids:
            err("UNKNOWN_MACHINE", f"process transfer {transfer.id}: "
                f"unknown machine '{transfer.machine}'", transfer.id)
        if transfer.source_space not in space_ids:
            err("UNKNOWN_SPACE", f"process transfer {transfer.id}: unknown "
                f"source space '{transfer.source_space}'", transfer.id)
        if transfer.target_space not in space_ids:
            err("UNKNOWN_SPACE", f"process transfer {transfer.id}: unknown "
                f"target space '{transfer.target_space}'", transfer.id)
        if transfer.source_space == transfer.target_space:
            err("PROCESS_SELF_TRANSFER", f"process transfer {transfer.id}: "
                "source and target must differ", transfer.id)
        if transfer.operation_kind not in ("stack", "reclaim", "transfer"):
            err("BAD_JOB_KIND", f"process transfer {transfer.id}: unknown "
                f"operation kind '{transfer.operation_kind}'", transfer.id)
        if not (0 < transfer.yield_factor <= 1):
            err("BAD_PROCESS_YIELD", f"process transfer {transfer.id}: "
                "yield_factor must be in (0, 1]", transfer.id)
        if transfer.residence_min < 0 or transfer.dependency_delay_min < 0:
            err("BAD_DELAY", f"process transfer {transfer.id}: process "
                "delays must be non-negative", transfer.id)
        if transfer.output_grade and transfer.output_grade not in grade_ids:
            err("UNKNOWN_GRADE", f"process transfer {transfer.id}: unknown "
                f"output grade '{transfer.output_grade}'", transfer.id)
        if not transfer.path:
            err("NO_PROCESS_ROUTE", f"process transfer {transfer.id}: "
                "explicit route path is required", transfer.id)
            continue
        unknown_links = [lid for lid in transfer.path if lid not in link_by_id]
        if unknown_links:
            err("UNKNOWN_LINK", f"process transfer {transfer.id}: unknown "
                f"route link(s) {', '.join(unknown_links)}", transfer.id)
            continue
        ordered = [link_by_id[lid] for lid in transfer.path]
        for left, right in zip(ordered, ordered[1:]):
            if left.dst != right.src:
                err("BROKEN_PROCESS_ROUTE", f"process transfer {transfer.id}: "
                    f"{left.id} ends at {left.dst}, but {right.id} starts "
                    f"at {right.src}", transfer.id)
        if transfer.source_space in space_ids and \
                ordered[0].src != space_node(sc, transfer.source_space):
            err("BROKEN_PROCESS_ROUTE", f"process transfer {transfer.id}: "
                "path does not start at the source-space node", transfer.id)
        if transfer.target_space in space_ids and \
                ordered[-1].dst != space_node(sc, transfer.target_space):
            err("BROKEN_PROCESS_ROUTE", f"process transfer {transfer.id}: "
                "path does not end at the target-space node", transfer.id)
        if transfer.machine in machine_ids:
            machine = sc.machine(transfer.machine)
            work_space = (transfer.target_space
                          if transfer.operation_kind == "stack"
                          else transfer.source_space)
            if work_space not in machine.reachable_spaces:
                err("UNREACHABLE_PROCESS", f"process transfer {transfer.id}: "
                    f"{transfer.machine} cannot reach {work_space}",
                    transfer.id)
            expected_kind = ("stacker" if transfer.operation_kind == "stack"
                             else "reclaimer"
                             if transfer.operation_kind == "reclaim"
                             else machine.kind)
            if machine.kind != expected_kind:
                err("PROCESS_MACHINE_KIND", f"process transfer {transfer.id}: "
                    f"{transfer.operation_kind} requires {expected_kind}, "
                    f"not {machine.kind}", transfer.id)

    line_states = sc.config.get(PROCESS_LINE_ENABLED_CONFIG, {})
    if not isinstance(line_states, dict):
        err(
            "BAD_PROCESS_LINE_CONFIG",
            f"{PROCESS_LINE_ENABLED_CONFIG} must be an object",
            PROCESS_LINE_ENABLED_CONFIG)
    else:
        for line_id, enabled in line_states.items():
            if not process_line_modelled(str(line_id)):
                err(
                    "UNMODELLED_PROCESS_LINE",
                    f"process line {line_id}: no executable model exists",
                    str(line_id))
            if not isinstance(enabled, bool):
                err(
                    "BAD_PROCESS_LINE_STATE",
                    f"process line {line_id}: enabled state must be boolean",
                    str(line_id))

    if bool(sc.config.get("LOU_S0_STOCKED_ROUTE_ENABLED", False)):
        corridor = effective_corridor_targets(sc)
        stages = effective_process_stage_targets(sc)
        try:
            c1_t = float(corridor.get("C1", 0.0) or 0.0)
            wash_t = sum(float(stages.get(stage, 0.0) or 0.0)
                         for stage in ("LOU-WASH-L1", "LOU-WASH-L2"))
        except (TypeError, ValueError):
            err("LOUTA_POLICY_MASS_MISMATCH",
                "Louta stocked-route targets must be numeric.",
                "LOUTA")
        else:
            if abs(c1_t - wash_t) > 1e-6:
                err(
                    "LOUTA_POLICY_MASS_MISMATCH",
                    f"Louta C1 target {c1_t:g} t must equal the combined "
                    f"L1/L2 washing output {wash_t:g} t.",
                    "LOUTA")

    # Destinations need a route that matches how their demand is fulfilled.
    # Final deliveries use the ordinary route graph.  Process-feed demand is
    # fulfilled only by the input side of an explicit ProcessTransfer, so a
    # generic graph path must not make a washing-line demand look serviceable.
    graph = RouteGraph.from_scenario(sc)
    for d in sc.destinations:
        demand_kind = getattr(d, "demand_kind", "delivery") or "delivery"
        # Use the scenario's effective demand accessor so a configured Future
        # receiving line may retain its demand template without being reported
        # as active, unreachable demand.
        has_demand = any(
            sc.demand_tph(d.id, bucket) > 0
            for bucket in range(sc.sim.horizon_buckets))
        if demand_kind not in ("delivery", "process_feed"):
            err(
                "BAD_DEMAND_KIND",
                f"destination {d.id}: unknown demand kind '{demand_kind}'",
                d.id)
        if d.node_id not in node_ids:
            err("UNKNOWN_NODE", f"destination {d.id}: unknown node '{d.node_id}'", d.id)
            continue

        if demand_kind == "process_feed":
            stage = getattr(d, "process_stage", "") or ""
            if not stage:
                err(
                    "NO_PROCESS_STAGE",
                    f"destination {d.id}: process-feed demand requires a "
                    "process stage",
                    d.id)
            else:
                matching_transfers = []
                for transfer in sc.process_transfers:
                    if transfer.process_stage != stage:
                        continue
                    if not process_stage_enabled(
                            sc, transfer.process_stage) or (
                            transfer.depends_on_stage
                            and not process_stage_enabled(
                                sc, transfer.depends_on_stage)):
                        continue
                    try:
                        machine = sc.machine(transfer.machine)
                        source = sc.space(transfer.source_space)
                        target = sc.space(transfer.target_space)
                        route = [sc.link(link_id)
                                 for link_id in transfer.path]
                    except StopIteration:
                        continue
                    if d.requested_grade and source.grade != d.requested_grade:
                        continue
                    if not machine.available or not getattr(
                            machine, "planning_enabled", True) or not route:
                        continue
                    work_space = (
                        target.id if transfer.operation_kind == "stack"
                        else source.id)
                    if work_space not in machine.reachable_spaces or any(
                            not link.available for link in route):
                        continue
                    if route[0].src != space_node(sc, source.id) or \
                            route[-1].dst != space_node(sc, target.id) or \
                            any(left.dst != right.src
                                for left, right in zip(route, route[1:])):
                        continue
                    matching_transfers.append(transfer)

                if not matching_transfers:
                    grade_note = (
                        f" and source grade '{d.requested_grade}'"
                        if d.requested_grade else "")
                    if has_demand:
                        warn(
                            "NO_PROCESS_FEED_ROUTE",
                            f"destination {d.id}: no available process "
                            f"transfer matches stage '{stage}'{grade_note} "
                            "— positive feed demand can only be unmet",
                            d.id)
                    else:
                        info(
                            "NO_PROCESS_FEED_ROUTE_INACTIVE",
                            f"destination {d.id}: no available process "
                            f"transfer matches stage '{stage}'{grade_note}, "
                            "but feed demand is zero in this scenario",
                            d.id)
            for sp in d.spec:
                if sp.pmin is not None and sp.pmax is not None and \
                        sp.pmin > sp.pmax:
                    err(
                        "BAD_SPEC",
                        f"destination {d.id}: spec {sp.param} min > max",
                        d.id)
            continue

        feeders = [s.id for s in sc.spaces
                   if graph.best_path(space_node(sc, s.id), d.node_id)]
        if not feeders:
            if has_demand:
                warn(
                    "NO_FEED_PATH",
                    f"destination {d.id}: no available route-graph path from "
                    "any stock space — positive demand can only be unmet",
                    d.id)
            else:
                info(
                    "NO_FEED_PATH_INACTIVE",
                    f"destination {d.id}: no available route-graph path, but "
                    "demand is zero in this scenario",
                    d.id)
        for sp in d.spec:
            if sp.pmin is not None and sp.pmax is not None and sp.pmin > sp.pmax:
                err("BAD_SPEC", f"destination {d.id}: spec {sp.param} min > max", d.id)

    # demand
    for e in sc.demand:
        if e.dest_id not in dest_ids:
            err("UNKNOWN_DEST", f"demand entry: unknown destination '{e.dest_id}'", e.dest_id)
        if e.rate_tph < 0:
            err("NEGATIVE_DEMAND", f"demand entry {e.dest_id}@{e.bucket}: negative rate", e.dest_id)

    # calendars / fixed jobs
    for c in sc.calendars:
        if c.scope != "GLOBAL" and c.scope not in machine_ids:
            err("UNKNOWN_MACHINE", f"calendar: unknown machine '{c.scope}'", c.scope)
        if c.end_bucket < c.start_bucket:
            err("BAD_WINDOW", f"calendar {c.scope}: end before start", c.scope)
    for fj in sc.fixed_jobs:
        if fj.machine not in machine_ids:
            err("UNKNOWN_MACHINE", f"fixed job {fj.id}: unknown machine", fj.id)
        if fj.space not in space_ids:
            err("UNKNOWN_SPACE", f"fixed job {fj.id}: unknown space", fj.id)
        if fj.machine in machine_ids and fj.space in space_ids:
            if fj.space not in sc.machine(fj.machine).reachable_spaces:
                err("UNREACHABLE_FIXED", f"fixed job {fj.id}: machine {fj.machine} cannot "
                    f"reach space {fj.space}", fj.id)
        if fj.dest_id and fj.dest_id not in dest_ids:
            err("UNKNOWN_DEST", f"fixed job {fj.id}: unknown destination", fj.id)
        if fj.kind not in ("stack", "reclaim", "dispatch"):
            err("BAD_JOB_KIND", f"fixed job {fj.id}: unknown kind "
                f"'{fj.kind}'", fj.id)
        if fj.grade and fj.grade not in grade_ids:
            err("UNKNOWN_GRADE", f"fixed job {fj.id}: unknown grade "
                f"'{fj.grade}'", fj.id)
        has_r0 = fj.start_repere is not None
        has_r1 = fj.stop_repere is not None
        if has_r0 != has_r1:
            err("INCOMPLETE_REPERE_RANGE", f"fixed job {fj.id}: both start "
                "and stop repères are required", fj.id)
        elif has_r0 and fj.space in space_ids:
            try:
                r0 = float(fj.start_repere)
                r1 = float(fj.stop_repere)
                if not r0.is_integer() or not r1.is_integer():
                    raise ValueError("job repères must be integer boundaries")
                x0, x1 = repere_interval_to_x(
                    sc.space(fj.space), r0, r1)
            except (TypeError, ValueError, OverflowError) as exc:
                err("BAD_REPERE_RANGE", f"fixed job {fj.id}: {exc}", fj.id)
            else:
                if fj.machine in machine_ids:
                    m = sc.machine(fj.machine)
                    wa = m.work_area.get(fj.space,
                                         [sc.space(fj.space).x_from,
                                          sc.space(fj.space).x_to])
                    if x0 < wa[0] - 1e-6 or x1 > wa[1] + 1e-6:
                        err("REPERE_OUTSIDE_WORK_AREA",
                            f"fixed job {fj.id}: repère range maps outside "
                            f"{fj.machine}'s work area on {fj.space}", fj.id)
        elif not has_r0:
            info("FIXED_JOB_AUTO_RANGE", f"fixed job {fj.id}: no repère "
                 "range supplied; the planner will derive a work interval",
                 fj.id)
        for c in sc.calendars:
            if c.scope in ("GLOBAL", fj.machine) and not (
                    fj.end_bucket < c.start_bucket or fj.start_bucket > c.end_bucket):
                err("FIXED_VS_CALENDAR", f"fixed job {fj.id} overlaps calendar block on "
                    f"{c.scope} (buckets {c.start_bucket}-{c.end_bucket})", fj.id)

    # weights sanity (w1 must dominate — report §15.3 eq. 4)
    w = sc.weights
    others = [w.w2_offspec, w.w3_process_cost, w.w4_giveaway, w.w5_moves,
              w.w6_safety, w.w7_utilisation]
    if any(w.w1_unmet <= o for o in others):
        warn("W1_NOT_DOMINANT", "w1 (unmet demand) does not dominate every other weight — "
             "demand may be sacrificed for secondary objectives")

    # sim params
    if sc.sim.horizon_buckets < 2:
        err("BAD_HORIZON", "horizon must be at least 2 buckets")
    if sc.sim.dt_h <= 0:
        err("BAD_DT", "dt must be positive")
    if all(
            sc.demand_tph(destination.id, bucket) == 0
            for destination in sc.destinations
            for bucket in range(sc.sim.horizon_buckets)):
        info("NO_DEMAND", "no demand configured — plan will be empty")

    # experimental multi-grade zones: zone rows are engineering inputs —
    # inconsistent inputs are ERRORS (the expansion refuses to guess);
    # zones present with the flag off are flagged so they are not silently
    # ignored
    from .zones import validate_zones, zones_enabled
    if getattr(sc, "pile_zones", []):
        if zones_enabled(sc):
            for pz in validate_zones(sc):
                err("ZONE_INPUT", pz)
        else:
            info("ZONES_INACTIVE",
                 f"{len(sc.pile_zones)} pile-zone rows present but "
                 "MULTI_GRADE_ZONES_ENABLED is off — baseline "
                 "one-grade-per-pile model in force")

    # evidence hygiene
    n_assumed = sum(1 for s in sc.spaces if s.evidence.status in ("ASSUMED", "TBC")) + \
        sum(1 for m in sc.machines if m.evidence.status in ("ASSUMED", "TBC")) + \
        sum(1 for l in sc.links if l.evidence.status in ("ASSUMED", "TBC"))
    if n_assumed:
        info("ASSUMED_DATA", f"{n_assumed} entities carry ASSUMED/TBC evidence — see the "
             "Assumptions & Data Gaps page")
    return issues


def issue_counts(issues: List[Issue]) -> Dict[str, int]:
    out = {"ERROR": 0, "WARNING": 0, "INFO": 0}
    for i in issues:
        out[i.severity] = out.get(i.severity, 0) + 1
    return out
