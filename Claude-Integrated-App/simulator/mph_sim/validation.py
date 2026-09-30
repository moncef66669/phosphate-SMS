"""Input validation — layer 2. Produces a typed issue list (never raises)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List

from .coordinates import REPERE_SECTION_COUNT, repere_interval_to_x
from .blending import chevron_config_errors, chevron_settings
from .domain import (ACTIVE_OBJECTIVE_WEIGHT_FIELDS, OBJECTIVE_WEIGHT_MAX,
                     Scenario)
from .flow_config import (PROCESS_LINE_ENABLED_CONFIG, PROCESS_LINE_STATES,
                          PROCESS_LINE_STAGE_MAP,
                          effective_corridor_targets,
                          effective_process_stage_targets,
                          process_line_modelled, process_stage_enabled)
from .grade_ids import WASHED_SOURCE_GRADE
from .objective_config import operational_objective_errors, isolated_source_target_shortages
from .routes import RouteGraph, space_node
from . import mine_arrivals, prewash_blending, washing


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
    for message in mine_arrivals.configuration_errors(sc):
        err("BAD_MINE_ARRIVALS", message)
    for message in washing.configuration_errors(sc):
        err("BAD_WASHING_CONFIGURATION", message)
    if washing.enabled(sc):
        if sc.config.get("_WASHING_DERIVED_POLICY_NOTE"):
            info("WASHING_PRESET_FEED_POLICY", sc.config["_WASHING_DERIVED_POLICY_NOTE"])
        if sc.sim.mixed_resolution_enabled:
            err("WASHING_GRID", "Grade-resolved washing currently requires the native uniform/weekly planner, not mixed resolution.")
    for space in sc.spaces:
        grade = sc.grade(space.grade)
        if space.mass_t > 0 and grade is not None and getattr(grade, "is_specification", False):
            err("SPECIFICATION_AS_STOCK", "A receiving specification cannot identify actual stock material", space.id)

    space_ids = {s.id for s in sc.spaces}
    grade_ids = {g.id for g in sc.grades}
    quality_param_ids = {p.name for p in sc.quality_params}
    node_ids = {n.id for n in sc.nodes}
    dest_ids = {d.id for d in sc.destinations}
    machine_ids = {m.id for m in sc.machines}
    site_ids = {s.id for s in sc.sites}
    link_ids = {link.id for link in sc.links}
    line_ids = (dest_ids
                | set(PROCESS_LINE_STAGE_MAP)
                | {str(d.process_stage) for d in sc.destinations
                   if d.process_stage}
                | {str(t.process_stage) for t in sc.process_transfers
                   if t.process_stage})
    profile_ids = {p.id for p in sc.material_profiles}

    for grade in sc.grades:
        specs = list(getattr(grade, "receiving_spec", []) or [])
        is_specification = bool(getattr(grade, "is_specification", False))
        if specs and not is_specification:
            err("SPEC_ON_PHYSICAL_GRADE", f"grade {grade.id}: receiving "
                "limits require is_specification=True", grade.id)
        if is_specification and not specs:
            err("EMPTY_SPECIFICATION_GRADE", f"grade {grade.id}: receiving "
                "specification has no limits", grade.id)
        for destination_id in getattr(
                grade, "receiving_destinations", []) or []:
            if destination_id not in dest_ids:
                err("UNKNOWN_SPEC_DESTINATION", f"grade {grade.id}: unknown "
                    f"receiving destination '{destination_id}'", grade.id)
        for spec in specs:
            if spec.param not in quality_param_ids:
                err("UNKNOWN_SPEC_PARAMETER", f"grade {grade.id}: unknown "
                    f"quality parameter '{spec.param}'", grade.id)
            values = [spec.margin, spec.averaging_h, *[
                value for value in (spec.pmin, spec.pmax, spec.target)
                if value is not None]]
            try:
                finite = all(math.isfinite(float(value)) for value in values)
            except (TypeError, ValueError, OverflowError):
                finite = False
            if not finite or spec.margin < 0 or spec.averaging_h <= 0:
                err("BAD_SPEC", f"grade {grade.id}: {spec.param} has invalid "
                    "limits, margin or averaging window", grade.id)
            elif spec.pmin is not None and spec.pmax is not None and \
                    spec.pmin + spec.margin > spec.pmax - spec.margin:
                err("BAD_SPEC_MARGIN", f"grade {grade.id}: {spec.param} "
                    "margin inverts the receiving interval", grade.id)

    for message in operational_objective_errors(sc):
        code = (
            "BAD_CONTINUITY_POLICY"
            if message.startswith("CONTINUITY_POLICIES") else
            "BAD_ENERGY_OBJECTIVE"
            if "energy" in message.lower() or
            message.startswith("ENERGY_") or
            "Conveyor-link" in message else
            "BAD_OPERATIONAL_PENALTY")
        err(code, message, "PLANNER_OBJECTIVE")

    for shortage in isolated_source_target_shortages(sc):
        (err if shortage["hard"] else warn)(
            "HARD_SOURCE_TARGET_CONTRADICTION" if shortage["hard"] else "FORECAST_SOURCE_SHORTAGE",
            f"Production targets request {shortage['target_t']} t from {shortage['source']}, "
            f"but only {shortage['available_t']} t are available with no replenishment route. "
            + ("Explicit commitments conflict with stock." if shortage["hard"] else
               "Derived targets may be missed; final orders remain visible as unmet."),
            shortage["source"])

    # Editable assay and receiving intervals must not create NaN CP-SAT
    # coefficients or an inverted effective interval after safety margins.
    for entity in [*sc.grades, *sc.spaces]:
        for parameter, value in entity.quality.items():
            try:
                valid = math.isfinite(float(value))
            except (TypeError, ValueError, OverflowError):
                valid = False
            if not valid:
                err("BAD_ASSAY", f"{entity.id}: {parameter} must be a finite number",
                    entity.id)
    for destination in sc.destinations:
        if not isinstance(destination.priority, int) or destination.priority < 1:
            err("BAD_PRIORITY", f"{destination.id}: priority must be a positive integer",
                destination.id)
        for spec in destination.spec:
            values = [spec.margin, spec.averaging_h, *[
                value for value in (spec.pmin, spec.pmax, spec.target)
                if value is not None]]
            try:
                finite = all(math.isfinite(float(value)) for value in values)
            except (TypeError, ValueError, OverflowError):
                finite = False
            if not finite:
                err("BAD_SPEC", f"{destination.id}: {spec.param} limits, margin "
                    "and averaging window must be finite numbers", destination.id)
            elif spec.margin < 0 or spec.averaging_h <= 0:
                err("BAD_SPEC", f"{destination.id}: {spec.param} requires a "
                    "nonnegative margin and positive averaging window", destination.id)
            elif spec.pmin is not None and spec.pmax is not None and \
                    spec.pmin + spec.margin > spec.pmax - spec.margin:
                err("BAD_SPEC_MARGIN", f"{destination.id}: {spec.param} margin "
                    "inverts the receiving interval", destination.id)

    # Chevron is a stacking policy, not an implicit reclaim blend. Validate
    # its recipe at input time so a malformed operator edit cannot be hidden
    # inside a generated plan.
    try:
        blend_errors = chevron_config_errors(sc)
    except (TypeError, ValueError, OverflowError) as exc:
        blend_errors = [f"Invalid blend configuration: {exc}"]
    for message in blend_errors:
        err("BAD_CHEVRON_RECIPE", message, "CHEVRON_STACKING")

    try:
        prewash_errors = prewash_blending.configuration_errors(sc)
    except (TypeError, ValueError, OverflowError) as exc:
        prewash_errors = [f"Invalid pre-washing blend configuration: {exc}"]
    for message in prewash_errors:
        err("BAD_PREWASH_BLEND", message, prewash_blending.CONFIG_KEY)

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
                 "excluded from quality-constrained delivery until assayed", s.id)
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

    # Optional stage-aware grade mass profiles.  Moisture is wet-basis percent
    # and beneficiation yield is dry feed solids -> zero-moisture merchant
    # product (TSM).  Grades without the profile remain fully backward
    # compatible.
    for grade in sc.grades:
        mass_values = (
            getattr(grade, "beneficiation_yield", None),
            getattr(grade, "raw_moisture_pct", None),
            getattr(grade, "product_moisture_pct", None),
        )
        if all(value is None for value in mass_values):
            continue
        if any(value is None for value in mass_values):
            err("INCOMPLETE_MASS_PROFILE", f"grade {grade.id}: beneficiation "
                "yield, raw moisture and product moisture must be configured "
                "together", grade.id)
            continue
        if not (0 < float(grade.beneficiation_yield) <= 1):
            err("BAD_BENEFICIATION_YIELD", f"grade {grade.id}: "
                "beneficiation_yield must be in (0, 1]", grade.id)
        for field_name in ("raw_moisture_pct", "product_moisture_pct"):
            value = float(getattr(grade, field_name))
            if not (0 <= value < 100):
                err("BAD_MOISTURE", f"grade {grade.id}: {field_name} must "
                    "be in [0, 100)", grade.id)

    # Known washed IDs have one canonical raw-feed Cd lineage.  This check is
    # independent of whether the upstream raw catalogue is embedded (the
    # MPH-only scenario deliberately omits it).
    for grade in sc.grades:
        expected_source = WASHED_SOURCE_GRADE.get(grade.id)
        if expected_source is None:
            continue
        actual_source = str(getattr(grade, "source_grade_id", "") or "")
        if actual_source != expected_source:
            err(
                "WASHED_SOURCE_GRADE_MISMATCH",
                f"grade {grade.id}: raw source must be "
                f"'{expected_source}', not '{actual_source or 'blank'}'",
                grade.id,
            )

    # A transformed-grade source must also resolve when the scenario contains
    # a raw-grade catalogue.  MPH-only scenarios deliberately omit upstream
    # mine grades, so only the canonical lineage check above applies there.
    raw_catalogue_present = any(
        grade.material_class == "raw_screened" and
        grade.id.startswith("RAW-BT-")
        for grade in sc.grades
    )
    if raw_catalogue_present:
        for grade in sc.grades:
            source_grade_id = str(
                getattr(grade, "source_grade_id", "") or "")
            if not source_grade_id:
                continue
            source_grade = sc.grade(source_grade_id)
            if source_grade is None:
                err("UNKNOWN_SOURCE_GRADE", f"grade {grade.id}: unknown raw "
                    f"source grade '{source_grade_id}'", grade.id)
            elif source_grade.material_class != "raw_screened":
                err("BAD_SOURCE_GRADE_CLASS", f"grade {grade.id}: source "
                    f"grade {source_grade_id} is not raw/screened", grade.id)

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
        if float(getattr(transfer, "dry_feed_capacity_tph", 0.0) or 0.0) < 0:
            err("BAD_DRY_FEED_CAPACITY", f"process transfer {transfer.id}: "
                "dry_feed_capacity_tph must be non-negative", transfer.id)
        conversion = str(getattr(transfer, "mass_conversion", "") or "")
        if conversion not in ("", "beneficiation", "drying"):
            err("BAD_MASS_CONVERSION", f"process transfer {transfer.id}: "
                f"unknown mass conversion '{conversion}'", transfer.id)
        elif conversion:
            grade = sc.grade(transfer.output_grade)
            if grade is None:
                err("MISSING_MASS_PROFILE", f"process transfer {transfer.id}: "
                    "a stage-aware conversion requires a known output grade",
                    transfer.id)
            elif conversion == "beneficiation" and \
                    grade.raw_physical_to_wet_product(1.0) is None:
                err("MISSING_MASS_PROFILE", f"process transfer {transfer.id}: "
                    f"grade {grade.id} has no complete beneficiation mass "
                    "profile", transfer.id)
            elif conversion == "drying" and \
                    grade.wet_product_physical_to_tsm(1.0) is None:
                err("MISSING_MASS_PROFILE", f"process transfer {transfer.id}: "
                    f"grade {grade.id} has no product moisture", transfer.id)
            else:
                factor = sc.transfer_output_factor(transfer)
                if not (0 < factor <= 1):
                    err("BAD_PHYSICAL_OUTPUT_FACTOR", f"process transfer "
                        f"{transfer.id}: physical output factor must be in "
                        "(0, 1]", transfer.id)
        if float(getattr(transfer, "dry_feed_capacity_tph", 0.0) or 0.0) > 0 \
                and conversion != "beneficiation":
            err("BAD_DRY_FEED_CAPACITY", f"process transfer {transfer.id}: "
                "dry_feed_capacity_tph is only valid for beneficiation",
                transfer.id)
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
            valid_state = (
                isinstance(enabled, bool)
                or str(enabled).strip().upper() in PROCESS_LINE_STATES
            )
            if not valid_state:
                err(
                    "BAD_PROCESS_LINE_STATE",
                    f"process line {line_id}: state must be Available, "
                    "Disabled or Future",
                    str(line_id))

    shortfall_limits = sc.config.get(
        "PROCESS_STAGE_TARGET_SHORTFALL_MAX_T", {})
    if not isinstance(shortfall_limits, dict):
        err("BAD_PROCESS_STAGE_SHORTFALL",
            "PROCESS_STAGE_TARGET_SHORTFALL_MAX_T must be an object")
    else:
        # A configured stage may be temporarily disabled by a process-line
        # scenario switch; its saved search cap remains valid for re-enabling.
        raw_targets = sc.config.get("PROCESS_STAGE_OUTPUT_TARGET_T", {})
        configured_targets = (
            raw_targets if isinstance(raw_targets, dict) else {})
        for stage, raw_cap in shortfall_limits.items():
            if stage not in configured_targets:
                err("BAD_PROCESS_STAGE_SHORTFALL",
                    f"{stage}: no configured process-stage target", str(stage))
                continue
            try:
                cap = float(raw_cap)
                target = float(configured_targets[stage])
            except (TypeError, ValueError, OverflowError):
                cap = float("nan")
                target = float("nan")
            if (isinstance(raw_cap, bool) or not math.isfinite(cap)
                    or not cap.is_integer() or cap < 0
                    or not math.isfinite(target) or cap > target):
                err("BAD_PROCESS_STAGE_SHORTFALL",
                    f"{stage}: shortfall cap must be a whole number from 0 "
                    f"to the configured target", str(stage))

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
        if d.id in washing.PAP_DESTINATION_IDS and \
                str(d.requested_grade or "") != "G10":
            err(
                "PAP_REQUIRES_G10",
                f"destination {d.id}: PAP demand must request G10, not "
                f"'{d.requested_grade or 'blank'}'",
                d.id)
        # Use the scenario's effective demand accessor so a configured Future
        # receiving line may retain its demand template without being reported
        # as active, unreachable demand.
        has_demand = any(
            sc.demand_tph(d.id, bucket) > 0
            for bucket in range(sc.sim.horizon_buckets))
        campaign_grades = {
            sc.demand_grade(d.id, bucket)
            for bucket in range(sc.sim.horizon_buckets)
            if sc.demand_tph(d.id, bucket) > 0
            and sc.demand_grade(d.id, bucket)
        }
        if not campaign_grades and d.requested_grade:
            campaign_grades = {d.requested_grade}
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
                    if campaign_grades and source.grade not in campaign_grades:
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

                matched_grades = {
                    sc.space(transfer.source_space).grade
                    for transfer in matching_transfers
                }
                missing_grades = campaign_grades - matched_grades
                if not matching_transfers or missing_grades:
                    grade_note = (
                        " and source grade(s) "
                        + ", ".join(sorted(missing_grades or campaign_grades))
                        if campaign_grades else "")
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
        missing_delivery_grades = {
            grade for grade in campaign_grades
            if not (sc.grade(grade) is not None and getattr(
                sc.grade(grade), "is_specification", False))
            if not any(sc.space(space_id).grade == grade
                       for space_id in feeders)
        }
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
        elif missing_delivery_grades and has_demand:
            warn(
                "NO_GRADE_FEED_PATH",
                f"destination {d.id}: no available stock-space path carries "
                "requested grade(s) "
                + ", ".join(sorted(missing_delivery_grades))
                + " — those campaign buckets can only be unmet",
                d.id)
        for sp in d.spec:
            if sp.pmin is not None and sp.pmax is not None and sp.pmin > sp.pmax:
                err("BAD_SPEC", f"destination {d.id}: spec {sp.param} min > max", d.id)

    # demand
    demand_keys = set()
    duplicate_demand_keys = set()
    for e in sc.demand:
        if e.dest_id not in dest_ids:
            err("UNKNOWN_DEST", f"demand entry: unknown destination '{e.dest_id}'", e.dest_id)
        try:
            rate = float(e.rate_tph)
        except (TypeError, ValueError):
            err("BAD_DEMAND_RATE", f"demand entry {e.dest_id}@{e.bucket}: "
                "rate must be numeric", e.dest_id)
        else:
            if not math.isfinite(rate):
                err("BAD_DEMAND_RATE", f"demand entry {e.dest_id}@{e.bucket}: "
                    "rate must be finite", e.dest_id)
            elif rate < 0:
                err("NEGATIVE_DEMAND", f"demand entry {e.dest_id}@{e.bucket}: negative rate", e.dest_id)
        if not isinstance(e.bucket, int) or not (
                0 <= e.bucket < int(sc.sim.horizon_buckets)):
            err("DEMAND_BUCKET_OUT_OF_HORIZON",
                f"demand entry {e.dest_id}@{e.bucket}: bucket must be an "
                "integer inside the configured horizon", e.dest_id)
        key = (e.dest_id, e.bucket)
        if key in demand_keys:
            duplicate_demand_keys.add(key)
        demand_keys.add(key)
        requested_grade = str(getattr(e, "requested_grade", "") or "")
        effective_grade = requested_grade
        if not effective_grade and e.dest_id in dest_ids:
            effective_grade = str(
                sc.destination(e.dest_id).requested_grade or "")
        if e.dest_id in washing.PAP_DESTINATION_IDS and \
                effective_grade != "G10":
            err(
                "PAP_REQUIRES_G10",
                f"demand entry {e.dest_id}@{e.bucket}: PAP demand must "
                f"request G10, not '{effective_grade or 'blank'}'",
                e.dest_id)
        priority = getattr(e, "priority", None)
        if priority is not None:
            try:
                parsed_priority = float(priority)
            except (TypeError, ValueError, OverflowError):
                parsed_priority = float("nan")
            if (isinstance(priority, bool) or not math.isfinite(parsed_priority)
                    or parsed_priority < 1 or not parsed_priority.is_integer()):
                err("BAD_DEMAND_PRIORITY", f"demand entry {e.dest_id}@{e.bucket}: "
                    "campaign priority must be a finite positive integer", e.dest_id)
        if requested_grade and requested_grade not in grade_ids:
            err(
                "UNKNOWN_GRADE",
                f"demand entry {e.dest_id}@{e.bucket}: unknown requested "
                f"grade '{requested_grade}'",
                e.dest_id)
        elif requested_grade:
            grade = sc.grade(requested_grade)
            allowed = list(getattr(
                grade, "receiving_destinations", []) or []) if grade else []
            if grade is not None and getattr(
                    grade, "is_specification", False) and allowed and \
                    e.dest_id not in allowed:
                err(
                    "SPECIFICATION_DESTINATION_MISMATCH",
                    f"demand entry {e.dest_id}@{e.bucket}: specification "
                    f"grade '{requested_grade}' is only valid for "
                    + ", ".join(allowed),
                    e.dest_id)
    for destination_id, bucket in sorted(duplicate_demand_keys):
        err("DUPLICATE_DEMAND_BUCKET",
            f"demand has more than one row for {destination_id}@{bucket}; "
            "weekly plans require one unambiguous rate/grade row",
            destination_id)

    # calendars / fixed jobs
    for c in sc.calendars:
        scope = str(c.scope or "")
        action = str(getattr(c, "action", "UNAVAILABLE") or
                     "UNAVAILABLE").strip().upper()
        if scope != "GLOBAL" and scope not in machine_ids:
            if scope.startswith("LINK:"):
                entity = scope[5:]
                if entity not in link_ids:
                    err("UNKNOWN_LINK", f"calendar: unknown link '{entity}'",
                        c.scope)
            elif scope.startswith("SITE:"):
                entity = scope[5:]
                if entity not in site_ids:
                    err("UNKNOWN_SITE", f"calendar: unknown site '{entity}'",
                        c.scope)
            elif scope.startswith("LINE:"):
                entity = scope[5:]
                if entity not in line_ids:
                    err("UNKNOWN_LINE", f"calendar: unknown line '{entity}'",
                        c.scope)
            else:
                # Preserve the established validation code for imported
                # legacy machine calendars.
                err("UNKNOWN_MACHINE",
                    f"calendar: unknown machine '{c.scope}'", c.scope)
        if action not in {"UNAVAILABLE", "RATE_CAP"}:
            err("BAD_RESOURCE_ACTION", f"calendar {c.scope}: action must be "
                "UNAVAILABLE or RATE_CAP", c.scope)
        rate = getattr(c, "rate_tph", None)
        if action == "RATE_CAP":
            if scope == "GLOBAL" or scope.startswith("SITE:"):
                err("BAD_RATE_SCOPE", f"calendar {c.scope}: rate caps require "
                    "a machine, LINK: or LINE: scope", c.scope)
            try:
                rate_value = float(rate)
            except (TypeError, ValueError):
                err("MISSING_RATE_CAP", f"calendar {c.scope}: RATE_CAP "
                    "requires rate_tph", c.scope)
            else:
                if not math.isfinite(rate_value) or rate_value < 0:
                    err("BAD_RATE_CAP", f"calendar {c.scope}: rate_tph must "
                        "be a finite non-negative physical t/h value", c.scope)
        if c.end_bucket < c.start_bucket:
            err("BAD_WINDOW", f"calendar {c.scope}: end before start", c.scope)
        has_start_minute = getattr(c, "start_minute", None) is not None
        has_end_minute = getattr(c, "end_minute", None) is not None
        if has_start_minute != has_end_minute:
            err("INCOMPLETE_MINUTE_WINDOW",
                f"calendar {c.scope}: both start and end minutes are required",
                c.scope)
        elif has_start_minute and (
                int(c.start_minute) < 0
                or int(c.end_minute) <= int(c.start_minute)):
            err("BAD_MINUTE_WINDOW",
                f"calendar {c.scope}: canonical minute window is invalid",
                c.scope)

    line_caps = sc.config.get("PROCESS_LINE_CAPACITY_TPH", {})
    if not isinstance(line_caps, dict):
        err("BAD_PROCESS_LINE_CAPS",
            "PROCESS_LINE_CAPACITY_TPH must be an object",
            "PROCESS_LINE_CAPACITY_TPH")
    else:
        for line_id, rate in line_caps.items():
            if str(line_id) not in line_ids:
                err("UNKNOWN_LINE", f"process-line capacity: unknown line "
                    f"'{line_id}'", str(line_id))
                continue
            try:
                rate_value = float(rate)
            except (TypeError, ValueError):
                err("BAD_PROCESS_LINE_CAP", f"process line {line_id}: "
                    "capacity must be numeric physical t/h", str(line_id))
                continue
            if not math.isfinite(rate_value) or rate_value < 0:
                err("BAD_PROCESS_LINE_CAP", f"process line {line_id}: "
                    "capacity must be a finite non-negative physical t/h",
                    str(line_id))
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
        has_start_minute = getattr(fj, "start_minute", None) is not None
        has_end_minute = getattr(fj, "end_minute", None) is not None
        if has_start_minute != has_end_minute:
            err("INCOMPLETE_MINUTE_WINDOW",
                f"fixed job {fj.id}: both start and end minutes are required",
                fj.id)
        elif has_start_minute and (
                int(fj.start_minute) < 0
                or int(fj.end_minute) <= int(fj.start_minute)):
            err("BAD_MINUTE_WINDOW",
                f"fixed job {fj.id}: canonical minute window is invalid",
                fj.id)
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
        fixed_start = (int(fj.start_minute) if has_start_minute
                       else int(fj.start_bucket)
                       * sc.sim.native_bucket_minutes())
        fixed_end = (int(fj.end_minute) if has_end_minute
                     else (int(fj.end_bucket) + 1)
                     * sc.sim.native_bucket_minutes())
        machine_site = (sc.machine(fj.machine).site
                        if fj.machine in machine_ids else "")
        for c in sc.calendars:
            if str(getattr(c, "action", "UNAVAILABLE") or
                   "UNAVAILABLE").strip().upper() != "UNAVAILABLE":
                continue
            if c.scope not in (
                    "GLOBAL", fj.machine, f"SITE:{machine_site}"):
                continue
            calendar_start, calendar_end = sc.calendar_interval_minutes(c)
            if fixed_start < calendar_end and calendar_start < fixed_end:
                err("FIXED_VS_CALENDAR",
                    f"fixed job {fj.id} overlaps calendar block on "
                    f"{c.scope}", fj.id)

    # Active objective weights must be usable by the integer-scaled solver.
    # w3_process_cost is excluded because that objective term is inactive.
    w = sc.weights
    active_weights = {
        field_name: getattr(w, field_name)
        for field_name in ACTIVE_OBJECTIVE_WEIGHT_FIELDS
    }
    invalid_weights = []
    parsed_weights = {}
    for field_name, raw_value in active_weights.items():
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            invalid_weights.append(field_name)
            continue
        if (not math.isfinite(value) or value < 0 or
                value > OBJECTIVE_WEIGHT_MAX or not value.is_integer()):
            invalid_weights.append(field_name)
        else:
            parsed_weights[field_name] = value
    if invalid_weights:
        err(
            "BAD_OBJECTIVE_WEIGHT",
            "active objective weights must be finite whole numbers between "
            f"0 and {OBJECTIVE_WEIGHT_MAX:g}: "
            + ", ".join(invalid_weights),
        )
    others = [value for field_name, value in parsed_weights.items()
              if field_name != "w1_unmet"]
    if not invalid_weights and any(
            parsed_weights["w1_unmet"] <= o for o in others):
        warn(
            "W1_NOT_DOMINANT",
            "w1 (unmet demand) is not larger than every other active weight. "
            "A larger w1 is only a demand-first heuristic because objective "
            "terms use different units; it is not a dominance guarantee.")

    # sim params
    for message in sc.sim.uniform_grid_errors():
        err("BAD_TIME_GRID", message)
    if sc.sim.horizon_buckets < 2:
        err("BAD_HORIZON", "horizon must be at least 2 buckets")
    if sc.sim.dt_h <= 0:
        err("BAD_DT", "dt must be positive")
    if bool(getattr(sc.sim, "mixed_resolution_enabled", False)):
        for message in sc.sim.mixed_resolution_errors():
            err("BAD_MIXED_RESOLUTION", message)
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
