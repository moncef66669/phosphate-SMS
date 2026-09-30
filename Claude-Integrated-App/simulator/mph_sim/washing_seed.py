"""Non-authoritative physical seed; the full material model must verify it."""
from copy import deepcopy
import math

from . import prewash_blending, washing
from .flow_config import PROCESS_LINE_STAGE_MAP


def _g10_seed_variant(sc, variants):
    """Prefer the highest-recovery treatment that can satisfy G10.

    The legacy physical seed has no grade-resolved receiving chemistry. Its
    former highest-yield rule therefore selected Simple washing even when that
    output could never satisfy the configured G10 contract. The authoritative
    full model still decides the final mode; this only supplies a credible
    starting point for the weekly search.
    """
    catalogue = sc.config.get("_WASHING_MODEL_CATALOG", {})
    limits = washing.settings(sc).get("g10_limits", {})

    def qualifies(candidate):
        quality = catalogue.get(
            candidate.output_material_id, {}).get("quality", {})
        for parameter, bounds in limits.items():
            if parameter not in quality:
                return False
            value = float(quality[parameter])
            if value < float(bounds[0]) or value > float(bounds[1]):
                return False
        return True

    qualified = [candidate for candidate in variants if qualifies(candidate)]
    eligible = qualified or variants
    return max(eligible, key=lambda candidate: (
        candidate.yield_factor, candidate.treatment_id))


def physical_seed(sc, candidates, solve_uniform, options):
    """Use a restricted single-option legacy search, then resolve actual pools.

    No seeded chemistry is reported as a result. In particular, legacy starting
    stocks retain their original assays. Any pool/path/rounding mismatch rejects
    the seed; the caller verifies every hard full-model constraint afterwards.
    """
    def reject(reason):
        sc.config["_WASHING_SEED_DIAGNOSTIC"] = reason
        return None

    legacy = deepcopy(sc)
    legacy.config[washing.CONFIG_KEY]["enabled"] = False
    # PAP can only receive G10-certified material created by the typed washing
    # model. The label-only bootstrap deliberately has neither treatment
    # identities nor their full assay vectors, so PAP is omitted here. The
    # authoritative full model keeps its exact receiving demand.
    pap_destinations = set(
        washing.settings(sc).get("pap_destinations", []))
    for entry in legacy.demand:
        if entry.dest_id in pap_destinations:
            entry.rate_tph = 0.0
    # Chevron dispatch/stack variables are deliberately left free in the
    # authoritative verifier below; the mapper already excludes those flows.
    # Solving the fixed chevron campaign again in this label-only precursor
    # only couples a second large combinatorial problem to the washing seed.
    # Disable construction on the precursor copy, while retaining the exact
    # campaign in ``sc`` and in the full CP-SAT model.
    chevron = legacy.config.get("CHEVRON_STACKING")
    if isinstance(chevron, dict):
        chevron["enabled_spaces"] = []
    # Keep the legacy bootstrap small: its purpose is to seed the surrounding
    # route/inventory schedule, not to solve the new typed blend decision.
    # The authoritative feasibility model re-opens the washing-stage flows and
    # enforces their source shares and common treatment mode below.
    if prewash_blending.CONFIG_KEY in legacy.config and isinstance(
            legacy.config[prewash_blending.CONFIG_KEY], dict):
        legacy.config[prewash_blending.CONFIG_KEY]["enabled"] = False
    # Derived treatment identities belong to the typed full model. Keeping
    # their zero-stock label variants in this label-only seed needlessly
    # multiplies a Chevron campaign's binary choices.
    legacy.grades = [grade for grade in legacy.grades
                     if not grade.is_specification and not grade.id.startswith("WASH-")]
    legacy.config["OBJECTIVE_MODE"] = "service_first"
    legacy.config["PARCEL_CONSOLIDATION_ENABLED"] = False
    legacy.config["_WASHING_PHYSICAL_SEED_FIRST_FEASIBLE"] = True
    # Retain the forecast semantics and service weights. Earlier bootstrap
    # code hardened every derived process/corridor target and then zeroed the
    # real objective; on the integrated network that regularly exhausted the
    # daily guard without an incumbent. This precursor is a warm start, not a
    # second production commitment, so it should seek the same demand-backed
    # direction while leaving all forecast shortfalls explicit.

    # A pre-washing blend is treated as one campaign before treatment. Seed
    # every source transfer on a line with the same mode; otherwise the hint
    # would describe post-treatment mixing and be rejected by the full model.
    catalogue = sc.config.get("_WASHING_MODEL_CATALOG", {})
    limits = washing.settings(sc).get("g10_limits", {})

    def qualifies(candidate):
        quality = catalogue.get(
            candidate.output_material_id, {}).get("quality", {})
        return all(
            parameter in quality
            and float(bounds[0]) <= float(quality[parameter])
            <= float(bounds[1])
            for parameter, bounds in limits.items())

    shared_mode_by_stage = {}
    prewash_stages = set()
    for point in prewash_blending.enabled_points(sc).values():
        sources = {
            str(row.get("source_space", ""))
            for row in prewash_blending.candidate_components(point)}
        for stage in prewash_blending.effective_process_stages(point):
            prewash_stages.add(str(stage))
            stage_variants = [
                candidate for candidate in candidates
                if candidate.process_stage == stage
                and candidate.source_space in sources
                and candidate.treatment_id]
            modes = sorted({candidate.treatment_id
                            for candidate in stage_variants})
            feasible_modes = []
            for mode in modes:
                per_source = {
                    source: [candidate for candidate in stage_variants
                             if candidate.source_space == source
                             and candidate.treatment_id == mode]
                    for source in sources}
                if all(per_source[source]
                       and any(qualifies(candidate)
                               for candidate in per_source[source])
                       for source in sources):
                    feasible_modes.append(mode)
            eligible_modes = feasible_modes or modes
            if eligible_modes:
                shared_mode_by_stage[stage] = max(
                    eligible_modes,
                    key=lambda mode: max(
                        candidate.yield_factor
                        for candidate in stage_variants
                        if candidate.treatment_id == mode))

    # The verifier deliberately re-opens the complete process chain around a
    # pre-washing decision. The label-only precursor has one aggregate stream
    # per transfer, whereas the typed model rounds recovery separately for
    # each assay pool; their intermediate WIP inventories can consequently
    # differ by a few tonnes. Those buffers may be reconciled for mapping
    # downstream legacy work because none of their process quantities is
    # fixed in the verifier. A mismatch anywhere else remains a hard reject.
    prewash_related_stages = set(prewash_stages)
    for stages in PROCESS_LINE_STAGE_MAP.values():
        if prewash_stages.intersection(stages):
            prewash_related_stages.update(stages)
    if any(stage.startswith("LOU-WASH-") for stage in prewash_stages):
        prewash_related_stages.add("LOU-ORE-STACK")
    flexible_inventory_spaces = {
        space_id
        for candidate in candidates
        if candidate.process_stage in prewash_related_stages
        for space_id in (candidate.input_space(), candidate.output_space())
        if space_id
    }

    choices = {}
    unmapped_seed_t = 0.0
    for transfer in legacy.process_transfers:
        variants = [cand for cand in candidates if cand.transfer_id == transfer.id and cand.treatment_id]
        if variants:
            shared_mode = shared_mode_by_stage.get(transfer.process_stage)
            chosen = next((
                candidate for candidate in variants
                if candidate.treatment_id == shared_mode), None)
            if chosen is None:
                chosen = _g10_seed_variant(sc, variants)
            choices[transfer.id] = chosen.treatment_id
            chosen_mode_variants = [
                candidate for candidate in variants
                if candidate.treatment_id == chosen.treatment_id]
            # The legacy transfer has only one label, while the typed source
            # pile may contain several grades with different moisture-based
            # wet-feed ceilings and output factors. Use the conservative
            # envelope across every reachable material for the selected mode;
            # otherwise the precursor can schedule (for example) 789 t of a
            # 15%-moisture grade and later map it onto a 12%-moisture candidate
            # whose physical ceiling is only 762 t.
            transfer.yield_factor = min(
                candidate.yield_factor
                for candidate in chosen_mode_variants)
            transfer.mass_conversion = ""
            mode_capacity_tph = min(
                candidate.path.capacity_tph
                for candidate in chosen_mode_variants)
            for link_id in transfer.path:
                legacy.link(link_id).capacity_tph = min(
                    legacy.link(link_id).capacity_tph,
                    math.floor(mode_capacity_tph * legacy.sim.dt_h + 1e-9)
                    / legacy.sim.dt_h)
            output_target = legacy.config.get("PROCESS_STAGE_OUTPUT_TARGET_T", {}).get(transfer.process_stage)
            if output_target:
                for stages in PROCESS_LINE_STAGE_MAP.values():
                    if transfer.process_stage in stages:
                        raw_target = math.ceil(
                            float(output_target) / transfer.yield_factor - 1e-9)
                        for stage in stages[:stages.index(transfer.process_stage)]:
                            legacy.config["PROCESS_STAGE_OUTPUT_TARGET_T"].setdefault(stage, raw_target)
    result = solve_uniform(legacy, options)
    if result.status not in ("OPTIMAL", "FEASIBLE") or result.breakdown.get("minute_timing_errors", 0):
        return reject(f"legacy physical seed status {result.status}: {result.reasons[:2]}")
    pools = deepcopy(sc.config["_WASHING_MODEL_INITIAL"])
    x, y, inventories, transit = {}, {}, {}, {}
    cumulative = {}
    catalogue = sc.config["_WASHING_MODEL_CATALOG"]
    exogenous_material = sc.config.get("_WASHING_MODEL_EXOGENOUS", {})
    from .blending import chevron_settings
    managed = set(chevron_settings(sc)["active_spaces"])
    flexible_inventory_spaces.update(managed)
    # Corridor arrivals may be stacked into any reachable M16 humid pile, not
    # only a pile currently constructing a chevron campaign. The label-only
    # precursor cannot predict the authoritative material pool chosen for an
    # ordinary receiving stack, so allow grade-pool reconciliation across the
    # complete M16 yard while still enforcing each pile's aggregate physical
    # balance and capacity.
    flexible_inventory_spaces.update(
        space.id for space in sc.spaces
        if space.site == "MPH" and space.area == "M16-humid")
    managed_requirements = {}
    for job in result.jobs:
        if job.kind != "stack" or job.space not in managed:
            continue
        for flow in job.bucket_flows:
            key = (job.exit_node, job.grade, flow.bucket)
            managed_requirements[key] = managed_requirements.get(key, 0) + int(round(flow.tonnage_t))
    for sid, materials in pools.items():
        for mid, mass in materials.items():
            inventories[(sid, mid, 0)] = float(mass)
    for entry in sc.belt_profile:
        transit[(sc.link(entry.link_id).dst, entry.material_id, entry.arrive_bucket)] = (
            transit.get((sc.link(entry.link_id).dst, entry.material_id, entry.arrive_bucket), 0.) + entry.mass_t)
    for t in range(sc.sim.horizon_buckets):
        absolute = t + options.horizon_offset
        opening_pools = deepcopy(pools)
        tasks = []
        for job in result.jobs:
            for flow in job.bucket_flows:
                if flow.bucket != absolute:
                    continue
                start = min((window.start_minute for window in job.execution_windows
                             if window.solver_start_bucket <= absolute <= window.solver_end_bucket), default=absolute * 60)
                tasks.append((start, job, list(flow.path or job.path), float(flow.tonnage_t)))
        for _start, job, path, amount in sorted(tasks, key=lambda row: (row[0], row[1].id)):
            # A label-only legacy solution cannot assign the constituent
            # recipe of an actual Chevron pile. Leave only that pile's stack
            # decision to the authoritative typed verifier. Ordinary corridor
            # dispatches and stacks must still be replayed; skipping all of
            # them whenever one Chevron existed made unrelated HP inventories
            # diverge from the physical seed.
            if (job.kind in ("stack", "reclaim") and job.space in managed
                    and not job.transfer_id):
                continue
            matches = [(ci, cand) for ci, cand in enumerate(candidates)
                       if cand.machine == job.machine and cand.space == job.space
                       and cand.display_kind() == job.kind and list(cand.path.links) == path
                       and cand.transfer_id == job.transfer_id
                       and (job.transfer_id or cand.dest_id == job.dest_id)
                       and (job.kind != "reclaim" or job.transfer_id or cand.grade == job.grade)
                       and (not cand.treatment_id or cand.treatment_id == choices.get(job.transfer_id))]
            remaining = int(round(amount))
            # A legacy grade label cannot distinguish freshly qualified
            # washed stock from old stock missing G10 assays. Prefer actual
            # treated pools for transport, subject to physical availability.
            requirement_key = None
            requirement = 0
            if matches and matches[0][1].kind == "dispatch":
                arrival = absolute + sc.link(matches[0][1].dest_id).transit_buckets
                requirement_key = (matches[0][1].exit_node, job.grade, arrival)
                requirement = managed_requirements.get(requirement_key, 0)
            ordered = sorted(matches, key=lambda pair: (
                0 if requirement and pair[1].grade == job.grade and
                     not catalogue[pair[1].material_id]["treatment_id"] else
                1 if pair[1].kind == "dispatch" and catalogue[pair[1].material_id]["treatment_id"] else
                2, pair[1].material_id))
            for ci, cand in ordered:
                sid, mid = cand.input_space(), cand.material_id
                if cand.kind == "stack":
                    key = (cand.exit_node, mid, absolute)
                    available = transit.get(key, 0.)
                else:
                    available = pools.get(sid, {}).get(mid, 0.)
                    available = min(available, opening_pools.get(sid, {}).get(mid, 0.))
                take = min(remaining, int(round(available)))
                if requirement > 0 and cand.kind == "dispatch" and cand.grade == job.grade and not catalogue[mid]["treatment_id"]:
                    take = min(take, requirement)
                if take <= 0:
                    continue
                x[(ci, t)] = x.get((ci, t), 0) + take
                if cand.kind == "stack":
                    transit[key] -= take
                else:
                    pools[sid][mid] -= take
                    opening_pools[sid][mid] = opening_pools.get(sid, {}).get(mid, 0.) - take
                out = take
                if cand.kind == "process":
                    coefficient = int(round(cand.yield_factor * 10000))
                    block = t // max(int(sc.config.get("PROCESS_YIELD_CARRY_WINDOW_BUCKETS", 0) or 0), 1)
                    carry = bool(sc.config.get("PROCESS_YIELD_CARRY_WINDOW_BUCKETS", 0))
                    before = cumulative.get((ci, block), 0) if carry else 0
                    out = ((before + take) * coefficient) // 10000 - (before * coefficient) // 10000
                    cumulative[(ci, block)] = before + take
                    y[(ci, t)] = y.get((ci, t), 0) + out
                target = cand.output_space()
                if target:
                    pools.setdefault(target, {})[cand.output_material_id] = pools.get(target, {}).get(cand.output_material_id, 0.) + out
                if cand.kind == "dispatch":
                    arrival = absolute + sc.link(cand.dest_id).transit_buckets
                    key = (cand.exit_node, mid, arrival)
                    transit[key] = transit.get(key, 0.) + take
                remaining -= take
                if (requirement_key and requirement > 0
                        and cand.grade == job.grade
                        and not catalogue[mid]["treatment_id"]):
                    requirement = max(requirement - take, 0)
                    managed_requirements[requirement_key] = requirement
                if not remaining:
                    break
            if remaining:
                availability = [
                    (candidate.material_id,
                     catalogue[candidate.material_id]["grade_id"],
                     catalogue[candidate.material_id]["treatment_id"],
                     pools.get(candidate.input_space(), {}).get(
                         candidate.material_id, 0.0),
                     opening_pools.get(candidate.input_space(), {}).get(
                         candidate.material_id, 0.0))
                    for _ci, candidate in matches
                ]
                if (matches and all(
                        candidate.input_space() in flexible_inventory_spaces
                        and (not candidate.output_space()
                             or candidate.output_space()
                             in flexible_inventory_spaces)
                        for _ci, candidate in matches)):
                    # Mine arrivals become reclaimable one bucket later in the
                    # typed model, while the legacy aggregate precursor can
                    # consume them in their arrival bucket. Keep the mapped
                    # portion as a repairable hint and let flexible boundary
                    # reconciliation absorb only this precursor mismatch.
                    # The authoritative solve never receives invented tonnes.
                    unmapped_seed_t += remaining
                    continue
                return reject(
                    f"unmapped physical seed flow {job.id}/{absolute} "
                    f"{job.machine}/{job.kind}/{job.space}/{job.grade}: "
                    f"residual {remaining}, candidate matches {len(matches)}; "
                    f"pool/opening availability {availability}")

        # Match the authoritative material balance exactly: commanded work in
        # bucket ``t`` can consume only its opening stock, while exogenous
        # flows are posted afterwards and first become reclaimable at t + 1.
        # The material preparation layer has already resolved every flow to a
        # typed assay pool, including the grade-specific mine-arrival rows.
        exogenous_in_by_space = {}
        for index, flow in enumerate(sc.exogenous):
            if not (flow.start_bucket <= absolute <= flow.end_bucket):
                continue
            mid = exogenous_material.get(str(index))
            if mid not in catalogue:
                return reject(
                    f"exogenous flow {flow.id} has no typed material mapping")
            amount = int(round(float(flow.rate_tph) * float(sc.sim.dt_h)))
            if amount < 0:
                return reject(f"exogenous flow {flow.id} has negative bucket mass")
            if flow.source_space:
                available = pools.get(flow.source_space, {}).get(mid, 0.)
                if available + 1e-6 < amount:
                    return reject(
                        f"exogenous source flow {flow.id} exceeds typed pool "
                        f"{flow.source_space}/{mid}: {amount} > {available}")
                pools[flow.source_space][mid] = available - amount
            if flow.target_space:
                pools.setdefault(flow.target_space, {})[mid] = (
                    pools.get(flow.target_space, {}).get(mid, 0.) + amount)
                if not flow.source_space:
                    exogenous_in_by_space[flow.target_space] = (
                        exogenous_in_by_space.get(flow.target_space, 0) + amount)

        for sid, materials in sc.config["_WASHING_MODEL_POSSIBLE"].items():
            if sid in managed:
                # Chevron construction is intentionally left to the typed
                # verifier above; it has no per-constituent inventory vars.
                for mid in materials:
                    inventories[(sid, mid, t + 1)] = pools.get(
                        sid, {}).get(mid, 0.)
                continue
            expected_series = result.inventory.get(sid, [])
            if len(expected_series) <= t + 1:
                return reject(
                    f"legacy physical seed has no inventory boundary for "
                    f"{sid}/{absolute}")
            expected = float(expected_series[t + 1])
            actual = sum(pools.get(sid, {}).values())
            if (sid in flexible_inventory_spaces
                    and abs(actual - expected) > 1e-6):
                difference = expected - actual
                material_order = sorted(
                    materials,
                    key=lambda mid: (-pools.get(sid, {}).get(mid, 0.), mid))
                if difference > 0:
                    if not material_order:
                        return reject(
                            f"physical seed has no typed pool for flexible "
                            f"inventory {sid}/{absolute}")
                    chosen = material_order[0]
                    pools.setdefault(sid, {})[chosen] = (
                        pools.get(sid, {}).get(chosen, 0.) + difference)
                else:
                    remaining = -difference
                    for mid in material_order:
                        take = min(
                            remaining, pools.get(sid, {}).get(mid, 0.))
                        pools[sid][mid] -= take
                        remaining -= take
                        if remaining <= 1e-6:
                            break
                    if remaining > 1e-6:
                        return reject(
                            f"physical seed cannot reconcile flexible "
                            f"inventory {sid}/{absolute}: {remaining} t")
                actual = sum(pools.get(sid, {}).values())
            excess = actual - expected
            if excess < -1e-6:
                return reject(
                    f"physical seed material/physical mismatch at "
                    f"{sid}/{absolute}: typed {actual}, physical {expected}")
            if excess > 1e-6:
                # The aggregate legacy model may use emergency overflow for
                # an outside arrival. The typed model permits the same total
                # spill, divided among material pools. Remove it
                # deterministically, preferring the material that arrived in
                # this bucket; no controlled production is discarded here.
                if excess > exogenous_in_by_space.get(sid, 0) + 1e-6:
                    return reject(
                        f"physical seed unexplained overflow at {sid}/{absolute}: "
                        f"{excess} t exceeds outside arrival "
                        f"{exogenous_in_by_space.get(sid, 0)} t")
                active_arrival_mids = {
                    exogenous_material[str(index)]
                    for index, flow in enumerate(sc.exogenous)
                    if (flow.start_bucket <= absolute <= flow.end_bucket
                        and flow.target_space == sid and not flow.source_space
                        and str(index) in exogenous_material)
                }
                order = sorted(
                    pools.get(sid, {}),
                    key=lambda mid: (
                        0 if mid in active_arrival_mids else 1,
                        -pools[sid][mid], mid))
                remaining_spill = excess
                for mid in order:
                    take = min(remaining_spill, pools[sid][mid])
                    pools[sid][mid] -= take
                    remaining_spill -= take
                    if remaining_spill <= 1e-6:
                        break
                if remaining_spill > 1e-6:
                    return reject(
                        f"physical seed could not allocate typed overflow at "
                        f"{sid}/{absolute}: {remaining_spill} t")
            for mid in materials:
                inventories[(sid, mid, t + 1)] = pools.get(sid, {}).get(mid, 0.)
            if abs(sum(pools.get(sid, {}).values()) - expected) > 1e-6:
                return reject(f"physical seed material/physical mismatch at {sid}/{absolute}")
    return x, y, inventories, {
        "deterministic_time": result.solver_deterministic_time,
        "branches": result.solver_branches,
        "conflicts": result.solver_conflicts,
        "spill_t": float(result.breakdown.get("spill_t", 0.0) or 0.0),
        "unmapped_seed_t": unmapped_seed_t,
        "treatment_modes_by_transfer": dict(choices),
    }
