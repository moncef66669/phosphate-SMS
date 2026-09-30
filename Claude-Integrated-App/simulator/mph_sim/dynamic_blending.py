"""Demand-driven stockpile construction constraints for the scheduling model.

Amounts are physical tonnes. Enabling a pile permits an optional blend; it
does not prescribe its quantity, output identity, or a completion deadline.
"""
from __future__ import annotations

from fractions import Fraction
import math

from . import blending


def add_dynamic_pile(model, sc, space_id, cands, flows, horizon, *,
                     step_min, window_start_min, fixed_proposals=False):
    """Return selected input tonnes, activity, readiness and matrix variables.

The stockpile's physical capacity supplies the variable upper bound. The
only extra absolute input floor is the configured minimum per selected grade.
An already started pile retains its exact selected composition/total.
"""
    capacity = max(int(math.floor(sc.space(space_id).capacity_t)), 0)
    state = blending.chevron_campaign_state(sc, space_id)
    recipe = (state.get("resolved_recipe") or
              (blending.chevron_recipe(sc, space_id)
               if fixed_proposals else []))
    prior = {str(key): max(int(round(float(value))), 0)
             for key, value in state.get("stacked_t_by_grade", {}).items()}
    stack_indices = [ci for ci, cand in enumerate(cands)
                     if cand.display_kind() == "stack"
                     and cand.output_space() == space_id]
    reclaim_indices = [ci for ci, cand in enumerate(cands)
                       if cand.kind == "reclaim" and cand.space == space_id]
    input_grades = sorted(set(blending.chevron_component_grade_ids(
        sc, space_id)) | set(prior) | {str(row["grade_id"]) for row in recipe})
    active = model.new_bool_var(f"blend_active_{space_id}")
    total = model.new_int_var(0, capacity, f"blend_total_{space_id}")
    model.add(total <= capacity * active)
    model.add(total >= active)
    targets, used = {}, {}
    # A new policy floor cannot retroactively change an already selected or
    # physically started mixture (including a fine-grid refinement proposal).
    minimum = (1 if recipe else max(int(math.ceil(
        blending.chevron_minimum_input_t(sc))), 1))
    for grade_id in input_grades:
        target = model.new_int_var(0, capacity,
                                   f"blend_t_{space_id}_{grade_id}")
        selected = model.new_bool_var(f"blend_used_{space_id}_{grade_id}")
        model.add(target <= capacity * selected)
        model.add(target >= minimum * selected)
        model.add(selected <= active)
        targets[grade_id], used[grade_id] = target, selected
    model.add(sum(targets.values()) == total)
    model.add(sum(used.values()) >= 2 * active)

    selectors = {}
    locked = bool(recipe)
    if locked:
        amounts = {str(row["grade_id"]): int(round(float(
            row.get("tonnage_t", 0)))) for row in recipe}
        # Legacy physically frozen records may store fractions only.
        if not sum(amounts.values()):
            amounts = {str(row["grade_id"]): int(row["tonnage_t"])
                       for row in blending.chevron_batch_allocation(sc, space_id)}
        for grade_id, target in targets.items():
            model.add(target == amounts.get(grade_id, 0))
        model.add(active == 1)
    elif blending.chevron_mode(sc, space_id) == blending.BLEND_MODE_MATRIX:
        catalog = blending.chevron_matrix_catalog(sc)
        selectors = {entry["id"]: model.new_bool_var(
            f"blend_matrix_{space_id}_{entry['id']}") for entry in catalog}
        model.add(sum(selectors.values()) == active)
        for entry in catalog:
            shares = {str(row["grade_id"]): Fraction(
                str(row["target_fraction"])).limit_denominator(1000000)
                      for row in entry["components"]}
            for grade_id, target in targets.items():
                fraction = shares.get(grade_id, Fraction(0))
                model.add(fraction.denominator * target ==
                          fraction.numerator * total).only_enforce_if(
                              selectors[entry["id"]])
    else:
        for row in blending.chevron_component_bounds(sc, space_id):
            grade_id = str(row["grade_id"])
            lower = Fraction(str(row.get("min_fraction", 0))).limit_denominator(1000000)
            upper = Fraction(str(row.get("max_fraction", 1))).limit_denominator(1000000)
            model.add(lower.denominator * targets[grade_id] >=
                      lower.numerator * total)
            model.add(upper.denominator * targets[grade_id] <=
                      upper.numerator * total)

    for ci in stack_indices:
        if cands[ci].grade not in targets:
            for t in range(horizon):
                model.add(flows[ci, t] == 0)
    for grade_id, target in targets.items():
        built = sum(flows[ci, t] for ci in stack_indices
                    if cands[ci].grade == grade_id for t in range(horizon))
        model.add(prior.get(grade_id, 0) + built <= target)
        if not locked:
            model.add(prior.get(grade_id, 0) + built == target)
        elif fixed_proposals:
            ready_min = blending.chevron_ready_minute(sc, space_id)
            if ready_min <= window_start_min + horizon * step_min:
                model.add(prior.get(grade_id, 0) + built == target)

    ready = {}
    cumulative = sum(prior.values())
    for t in range(horizon + 1):
        flag = model.new_bool_var(f"blend_ready_{space_id}_{t}")
        ready[t] = flag
        model.add(flag <= active)
        model.add(cumulative == total).only_enforce_if(flag)
        model.add(cumulative <= total - 1).only_enforce_if([active, flag.Not()])
        if t:
            model.add(flag >= ready[t - 1])
        if t < horizon:
            for ci in reclaim_indices:
                model.add(flows[ci, t] == 0).only_enforce_if(flag.Not())
            cumulative += sum(flows[ci, t] for ci in stack_indices)
    return dict(targets=targets, used=used, active=active, total=total,
                ready=ready, selectors=selectors, capacity=capacity,
                stack_indices=stack_indices, reclaim_indices=reclaim_indices)


def add_receiving_quality(model, sc, space_id, decision, cands, flows,
                          horizon, offset, demand_specs_at, quality_scale):
    """Certify the actual whole-pile assay for each receiving interval used.

Destination-use literals avoid requiring a single pile to satisfy mutually
exclusive grade specifications that it never supplies. Assay constraints are
linear in input tonnes; no nominal output-grade assay replaces the mixture.
"""
    targets = decision["targets"]
    capacity = decision["capacity"]
    slacks, deviations, slacks_by_destination = [], [], {}
    dry_scale = 1000
    for destination in sc.destinations:
        indices = [ci for ci in decision["reclaim_indices"]
                   if cands[ci].dest_id == destination.id]
        if not indices:
            continue
        groups = {}
        for t in range(horizon):
            specs = demand_specs_at(destination.id, offset + t)
            signature = tuple((s.param, s.pmin, s.pmax, s.target,
                               s.hard, s.margin) for s in specs)
            groups.setdefault(signature, (specs, []))[1].append(t)
        for group_id, (specs, buckets) in enumerate(groups.values()):
            used = model.new_bool_var(
                f"blend_receiver_{space_id}_{destination.id}_{group_id}")
            quantity = sum(flows[ci, t] for ci in indices for t in buckets)
            model.add(quantity >= 1).only_enforce_if(used)
            model.add(quantity == 0).only_enforce_if(used.Not())
            for spec in specs:
                values, dry = {}, {}
                for grade_id, target in targets.items():
                    grade = sc.grade(grade_id)
                    assay = grade.quality if grade is not None else {}
                    value = assay.get(spec.param)
                    if value is None or not math.isfinite(float(value)):
                        if spec.pmin is not None or spec.pmax is not None:
                            model.add(target == 0).only_enforce_if(used)
                        continue
                    moisture = getattr(grade, "product_moisture_pct", None)
                    if moisture is None:
                        moisture = assay.get("moisture", 0)
                    coefficient = (dry_scale if spec.param == "moisture"
                                   else int(round(dry_scale * max(
                                       1 - float(moisture or 0) / 100, 0))))
                    dry[grade_id] = coefficient
                    values[grade_id] = int(round(float(value) * quality_scale)) * coefficient
                denominator = sum(dry[g] * targets[g] for g in dry)
                numerator = sum(values[g] * targets[g] for g in values)
                if spec.pmin is not None or spec.pmax is not None:
                    model.add(denominator >= 1).only_enforce_if(used)
                priority = max(int(destination.priority), 1)
                for side, limit in (("min", spec.pmin), ("max", spec.pmax)):
                    if limit is None:
                        continue
                    adjusted = (float(limit) + float(spec.margin) if side == "min"
                                else max(float(limit) - float(spec.margin), 0))
                    bound = int(round(adjusted * quality_scale))
                    slack = 0
                    if not spec.hard:
                        slack = model.new_int_var(0, max(capacity * 20000, 1),
                            f"blend_q{side}_{space_id}_{destination.id}_{group_id}_{spec.param}")
                        model.add(slack == 0).only_enforce_if(used.Not())
                        slacks.append((priority, slack))
                        slacks_by_destination.setdefault(destination.id, []).append(slack)
                    if side == "min":
                        model.add(numerator + dry_scale * slack >=
                                  bound * denominator).only_enforce_if(used)
                    else:
                        model.add(numerator <= bound * denominator +
                                  dry_scale * slack).only_enforce_if(used)
                if spec.param == "BPL" and spec.target is not None:
                    deviation = model.new_int_var(0, max(capacity * 200, 1),
                        f"blend_target_{space_id}_{destination.id}_{group_id}")
                    model.add(deviation == 0).only_enforce_if(used.Not())
                    model.add(dry_scale * quality_scale * deviation >=
                              numerator - int(round(spec.target * quality_scale)) *
                              denominator).only_enforce_if(used)
                    deviations.append((priority, deviation))
    return slacks, deviations, slacks_by_destination
