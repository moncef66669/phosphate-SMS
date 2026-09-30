"""Scenario-controlled stockpile blending policies.

Chevron blending is a stacking operation: the stacker travels repeatedly
along the pile and deposits thin layers from successive quality campaigns.
It is deliberately separate from simultaneous reclaim-feed blending.
"""
from __future__ import annotations

from math import ceil, floor, isfinite
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

from .domain import UNKNOWN_STOCK_QUALITY_KEY


CHEVRON_CONFIG_KEY = "CHEVRON_STACKING"

BLEND_MODE_MATRIX = "MATRIX"
BLEND_MODE_OPTIMIZED = "OPTIMIZED"
BLEND_MODES = (BLEND_MODE_MATRIX, BLEND_MODE_OPTIMIZED)
CHEVRON_MODEL_VERSION = 7

CAMPAIGN_NOT_STARTED = "NOT_STARTED"
CAMPAIGN_BUILDING = "BUILDING"
CAMPAIGN_COMPLETE = "COMPLETE"
CAMPAIGN_DEPLETED = "DEPLETED"
_CAMPAIGN_STATUSES = {
    CAMPAIGN_NOT_STARTED,
    CAMPAIGN_BUILDING,
    CAMPAIGN_COMPLETE,
    CAMPAIGN_DEPLETED,
}


def _non_negative_float(value, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    return parsed if isfinite(parsed) and parsed >= 0 else float(default)


def _blend_mode(value) -> str:
    mode = str(value or BLEND_MODE_MATRIX).strip().upper()
    aliases = {
        "FIXED": BLEND_MODE_MATRIX,
        "RECIPE": BLEND_MODE_MATRIX,
        "AUTO": BLEND_MODE_OPTIMIZED,
        "VARIABLE": BLEND_MODE_OPTIMIZED,
        "OPTIMISED": BLEND_MODE_OPTIMIZED,
    }
    mode = aliases.get(mode, mode)
    return mode if mode in BLEND_MODES else BLEND_MODE_MATRIX


def _normalise_recipe_rows(raw) -> List[Dict[str, object]]:
    """Return positive composition rows with unit-sum physical shares.

    Resolved rows may carry exact whole-batch tonnes.  Those tonnes are kept
    for audit/export while ``target_fraction`` remains the common interface
    used by the scheduler, quality calculation and legacy matrix scenarios.
    """
    if not isinstance(raw, (list, tuple)):
        return []
    parsed: List[Tuple[str, float, float | None]] = []
    share_total = 0.0
    tonne_total = 0.0
    for component in raw:
        if not isinstance(component, Mapping):
            continue
        grade_id = str(component.get("grade_id", "") or "").strip()
        try:
            share = float(component.get(
                "target_fraction", component.get("mass_fraction", 0.0))
                or 0.0)
        except (TypeError, ValueError):
            share = 0.0
        raw_tonnage = component.get("tonnage_t", None)
        try:
            tonnage = (None if raw_tonnage is None
                       else float(raw_tonnage))
        except (TypeError, ValueError):
            tonnage = None
        if tonnage is not None and (not isfinite(tonnage) or tonnage < 0):
            tonnage = None
        if not grade_id:
            continue
        if not isfinite(share) or share < 0:
            share = 0.0
        if share <= 0 and not (tonnage is not None and tonnage > 0):
            continue
        parsed.append((grade_id, share, tonnage))
        share_total += share
        tonne_total += tonnage or 0.0
    denominator = share_total if share_total > 0 else tonne_total
    if denominator <= 0:
        return []
    result: List[Dict[str, object]] = []
    for grade_id, share, tonnage in parsed:
        fraction = (share / share_total if share_total > 0
                    else float(tonnage or 0.0) / tonne_total)
        row: Dict[str, object] = {
            "grade_id": grade_id,
            "target_fraction": fraction,
        }
        if tonnage is not None:
            row["tonnage_t"] = tonnage
        result.append(row)
    return result


def _normalise_share_bounds(raw) -> List[Dict[str, object]]:
    if not isinstance(raw, (list, tuple)):
        return []
    result: List[Dict[str, object]] = []
    for component in raw:
        if not isinstance(component, Mapping):
            continue
        grade_id = str(component.get("grade_id", "") or "").strip()
        try:
            minimum = float(component.get(
                "min_fraction", component.get("minimum_fraction", 0.0))
                or 0.0)
            maximum = float(component.get(
                "max_fraction", component.get("maximum_fraction", 1.0))
                if component.get(
                    "max_fraction", component.get(
                        "maximum_fraction", 1.0)) is not None else 1.0)
        except (TypeError, ValueError):
            continue
        if not grade_id or not isfinite(minimum) or not isfinite(maximum):
            continue
        result.append({
            "grade_id": grade_id,
            "min_fraction": minimum,
            "max_fraction": maximum,
        })
    return result


def _normalise_matrix_catalog(raw) -> List[Dict[str, object]]:
    """Return unique reusable fixed matrices.

    A catalogue entry is deliberately independent of a physical stockpile.
    Stockpiles only opt in to blending; the planner chooses one eligible
    entry for each new campaign.
    """
    if not isinstance(raw, (list, tuple)):
        return []
    result: List[Dict[str, object]] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(raw, 1):
        if not isinstance(entry, Mapping):
            continue
        matrix_id = str(entry.get(
            "id", entry.get("matrix_id", f"MPH-MATRIX-{index:02d}"))
            or f"MPH-MATRIX-{index:02d}").strip()
        if not matrix_id or matrix_id in seen_ids:
            continue
        seen_ids.add(matrix_id)
        result.append({
            "id": matrix_id,
            "name": str(entry.get("name", matrix_id) or matrix_id).strip(),
            "components": _normalise_recipe_rows(entry.get(
                "components", entry.get("recipe", []))),
        })
    return result


def _legacy_catalog(raw: Mapping) -> List[Dict[str, object]]:
    """Deduplicate per-stockpile v5 matrices during read/migration."""
    recipes = raw.get("recipes", {})
    recipes = recipes if isinstance(recipes, Mapping) else {}
    result: List[Dict[str, object]] = []
    signatures: set[tuple] = set()
    for space_id, rows in recipes.items():
        recipe = _normalise_recipe_rows(rows)
        signature = tuple(sorted(
            (str(row["grade_id"]), round(float(row["target_fraction"]), 10))
            for row in recipe))
        if len(signature) < 2 or signature in signatures:
            continue
        signatures.add(signature)
        result.append({
            "id": f"MPH-MATRIX-{len(result) + 1:02d}",
            "name": f"Migrated matrix ({space_id})",
            "components": recipe,
        })
    return result


def _normalise_campaign_state(raw) -> Dict[str, object]:
    """Return explicit physical progress without inferring it from the clock."""
    raw = raw if isinstance(raw, Mapping) else {}
    status = str(raw.get("status", CAMPAIGN_NOT_STARTED) or "").upper()
    status_aliases = {
        "PENDING": CAMPAIGN_NOT_STARTED,
        "PLANNED": CAMPAIGN_NOT_STARTED,
        "IN_PROGRESS": CAMPAIGN_BUILDING,
        "BUILT": CAMPAIGN_COMPLETE,
        "COMPLETED": CAMPAIGN_COMPLETE,
        "EMPTY": CAMPAIGN_DEPLETED,
    }
    status = status_aliases.get(status, status)
    if status not in _CAMPAIGN_STATUSES:
        status = CAMPAIGN_NOT_STARTED

    stacked_raw = raw.get("stacked_t_by_grade", {})
    stacked = {
        str(grade_id): _non_negative_float(tonnage)
        for grade_id, tonnage in (
            stacked_raw.items() if isinstance(stacked_raw, Mapping) else ())
        if str(grade_id) and _non_negative_float(tonnage) > 0
    }
    accounted_raw = raw.get("accounted_job_t_by_id", {})
    accounted = {
        str(job_id): _non_negative_float(tonnage)
        for job_id, tonnage in (
            accounted_raw.items() if isinstance(accounted_raw, Mapping) else ())
        if str(job_id) and _non_negative_float(tonnage) > 0
    }
    remaining = raw.get("remaining_inventory_t", None)
    remaining_inventory_t = (
        None if remaining is None else _non_negative_float(remaining))
    completed = raw.get("completed_minute", None)
    try:
        completed_minute = None if completed is None else max(int(completed), 0)
    except (TypeError, ValueError):
        completed_minute = None
    resolved_recipe = _normalise_recipe_rows(raw.get("resolved_recipe", []))
    resolution_source = str(raw.get("resolution_source", "") or "").upper()
    if resolution_source not in BLEND_MODES:
        resolution_source = ""
    return {
        "status": status,
        "stacked_t_by_grade": stacked,
        "accounted_job_t_by_id": accounted,
        "remaining_inventory_t": remaining_inventory_t,
        "completed_minute": completed_minute,
        "resolved_recipe": resolved_recipe,
        "resolution_source": resolution_source,
        "batch_t": _non_negative_float(raw.get("batch_t", 0)),
        "ready_minute": _non_negative_float(raw.get("ready_minute", 0)),
        "result_grade": str(raw.get("result_grade", "") or "").strip(),
        "selected_matrix": str(raw.get("selected_matrix", "") or "").strip(),
        "campaign_id": str(raw.get("campaign_id", "") or "").strip(),
    }


def _ready_minutes(scenario, raw: Mapping) -> Dict[str, int]:
    """Canonical readiness offsets in minutes from the plan origin.

    Model-version 3 introduced ``ready_bucket_by_space`` through an explicitly
    hourly UI.  Preserve those saved values as hours even when a scenario is
    subsequently resampled to a 15-minute decision grid.  Other legacy inputs
    may provide their original bucket duration explicitly.
    """
    canonical = raw.get("ready_minute_by_space", {})
    canonical = canonical if isinstance(canonical, Mapping) else {}
    legacy = raw.get("ready_bucket_by_space", {})
    legacy = legacy if isinstance(legacy, Mapping) else {}
    try:
        model_version = int(raw.get("model_version", 0) or 0)
    except (TypeError, ValueError):
        model_version = 0
    try:
        legacy_step_min = int(raw.get("legacy_ready_bucket_minutes", 0) or 0)
    except (TypeError, ValueError):
        legacy_step_min = 0
    if legacy_step_min <= 0:
        legacy_step_min = 60 if model_version >= 3 else int(
            scenario.sim.native_bucket_minutes())

    result: Dict[str, int] = {}
    for space_id in {str(key) for key in canonical} | {
            str(key) for key in legacy}:
        value = canonical.get(space_id, None)
        if value is not None:
            try:
                result[space_id] = max(int(round(float(value))), 0)
                continue
            except (TypeError, ValueError):
                pass
        try:
            result[space_id] = max(
                int(round(float(legacy.get(space_id, 0)) * legacy_step_min)),
                0,
            )
        except (TypeError, ValueError):
            result[space_id] = 0
    return result


def chevron_settings(scenario) -> Dict[str, object]:
    """Return the shared MPH policy plus backward-compatible space views."""
    raw = scenario.config.get(CHEVRON_CONFIG_KEY, {})
    if not isinstance(raw, Mapping):
        raw = {}
    try:
        model_version = int(raw.get("model_version", 0) or 0)
    except (TypeError, ValueError):
        model_version = 0
    recipes = raw.get("recipes", {})
    recipes = recipes if isinstance(recipes, Mapping) else {}
    share_bounds = raw.get("share_bounds_by_space", {})
    share_bounds = share_bounds if isinstance(share_bounds, Mapping) else {}
    proposed_recipes = raw.get("proposed_recipes_by_space", {})
    proposed_recipes = (
        proposed_recipes if isinstance(proposed_recipes, Mapping) else {})
    proposed_campaigns = raw.get("proposed_campaigns_by_space", {})
    proposed_campaigns = (
        proposed_campaigns if isinstance(proposed_campaigns, Mapping) else {})
    raw_modes = raw.get("mode_by_space", {})
    raw_modes = raw_modes if isinstance(raw_modes, Mapping) else {}
    point_labels = raw.get("point_labels", {})
    result_grades = raw.get("result_grades", {})
    batch_t_by_space = raw.get("batch_t_by_space", {})
    minimum_component_fraction_by_space = raw.get(
        "minimum_component_fraction_by_space", {})
    ready_bucket_by_space = raw.get("ready_bucket_by_space", {})
    campaign_states_raw = raw.get("campaign_states_by_space", {})
    campaign_states_raw = (
        campaign_states_raw if isinstance(campaign_states_raw, Mapping)
        else {})
    campaign_states = {
        str(space_id): _normalise_campaign_state(state)
        for space_id, state in campaign_states_raw.items()
    }
    ready_minute_by_space = _ready_minutes(scenario, raw)
    point_labels = point_labels if isinstance(point_labels, Mapping) else {}
    policy_spaces = (
        {str(space_id) for space_id in recipes}
        | {str(space_id) for space_id in share_bounds}
        | {str(space_id) for space_id in proposed_recipes}
        | {str(space_id) for space_id in proposed_campaigns}
        | {str(space_id) for space_id in raw_modes}
        | {str(space_id) for space_id in campaign_states}
        | {str(space_id) for space_id in point_labels})
    raw_enabled_spaces = raw.get("enabled_spaces", None)
    if isinstance(raw_enabled_spaces, (list, tuple, set)):
        enabled_spaces = {
            str(space_id) for space_id in raw_enabled_spaces
            if not policy_spaces or str(space_id) in policy_spaces
        }
    else:
        # Existing scenarios used one global switch.  Preserve their current
        # behaviour on first load, then let the UI persist individual spaces.
        enabled_spaces = policy_spaces if bool(raw.get("enabled", False)) else set()

    # Version 6 owns one catalogue and one decision mode for all physically
    # similar M16 humid-phosphate stockpiles.  Legacy maps are read only as a
    # migration fallback; they are expanded below so older callers remain
    # stable while no per-pile policy is implied.
    matrix_catalog = _normalise_matrix_catalog(raw.get("matrix_catalog", []))
    if not matrix_catalog:
        matrix_catalog = _legacy_catalog(raw)

    preferred_space = next(iter(sorted(enabled_spaces)), "")
    if not preferred_space:
        preferred_space = next(iter(sorted(policy_spaces)), "")

    def _legacy_value(mapping, default):
        mapping = mapping if isinstance(mapping, Mapping) else {}
        if preferred_space in mapping:
            return mapping[preferred_space]
        return next(iter(mapping.values()), default)

    global_mode = _blend_mode(raw.get(
        "global_mode",
        _legacy_value(raw_modes, BLEND_MODE_MATRIX)))
    optimized_share_bounds = _normalise_share_bounds(raw.get(
        "optimized_share_bounds",
        _legacy_value(share_bounds, [])))
    global_result_grade = str(raw.get(
        "global_result_grade",
        _legacy_value(result_grades, "")) or "").strip()
    global_batch_t = _non_negative_float(raw.get(
        "global_batch_t",
        _legacy_value(batch_t_by_space, 0)))
    global_minimum_component_fraction = _non_negative_float(raw.get(
        "global_minimum_component_fraction",
        _legacy_value(minimum_component_fraction_by_space, 0.05)), 0.05)
    global_minimum_component_fraction = min(
        global_minimum_component_fraction, 0.5)
    if "global_ready_minute" in raw:
        try:
            global_ready_minute = max(int(round(float(
                raw.get("global_ready_minute", 0) or 0))), 0)
        except (TypeError, ValueError):
            global_ready_minute = 0
    else:
        global_ready_minute = int(_legacy_value(
            ready_minute_by_space, 0) or 0)

    shared_recipe = (
        matrix_catalog[0]["components"] if len(matrix_catalog) == 1 else [])
    shared_spaces = policy_spaces | enabled_spaces
    shared_recipes = {
        space_id: [dict(row) for row in shared_recipe]
        for space_id in shared_spaces
    }
    shared_bounds = {
        space_id: [dict(row) for row in optimized_share_bounds]
        for space_id in shared_spaces
    }
    # An opt-in permits a future campaign; it must not turn opening stock
    # into an unbuilt blend or block its ordinary reclaim. Eligibility is
    # evaluated at each planning window's opening physical inventory. Once
    # construction starts its explicit state remains authoritative.
    active_spaces = set(enabled_spaces)
    if str(raw.get("quantity_mode", "")).upper() == "DEMAND_DRIVEN":
        spaces_by_id = {space.id: space for space in scenario.spaces}
        active_spaces = {
            space_id for space_id in enabled_spaces
            if campaign_states.get(space_id, {}).get(
                "status", CAMPAIGN_NOT_STARTED) != CAMPAIGN_NOT_STARTED
            or (space_id in spaces_by_id
                and float(spaces_by_id[space_id].mass_t) <= 1e-6)
        }
    return {
        "model_version": model_version,
        "quantity_mode": str(raw.get("quantity_mode", "FIXED") or "FIXED").upper(),
        "min_input_grade_t": _non_negative_float(raw.get("min_input_grade_t", 500), 500),
        "stacking_traverse_speed_m_min": raw.get("stacking_traverse_speed_m_min"),
        "enabled": bool(enabled_spaces),
        "enabled_spaces": enabled_spaces,
        "active_spaces": active_spaces,
        "priority": max(int(raw.get("priority", 1) or 1), 1),
        "passes_per_cycle": (0 if str(raw.get("quantity_mode", "")).upper() == "DEMAND_DRIVEN"
                             else max(int(raw.get("passes_per_cycle", 20) or 20), 2)),
        "matrix_catalog": matrix_catalog,
        "global_mode": global_mode,
        "optimized_share_bounds": optimized_share_bounds,
        "global_result_grade": global_result_grade,
        "global_batch_t": global_batch_t,
        "global_ready_minute": global_ready_minute,
        "global_minimum_component_fraction":
            global_minimum_component_fraction,
        "recipes": shared_recipes,
        "share_bounds_by_space": shared_bounds,
        "proposed_recipes_by_space": proposed_recipes,
        "proposed_campaigns_by_space": proposed_campaigns,
        "proposed_matrix_by_space": raw.get("proposed_matrix_by_space", {}),
        "mode_by_space": {
            space_id: global_mode for space_id in shared_spaces
        },
        "point_labels": point_labels,
        "result_grades": {
            space_id: global_result_grade for space_id in shared_spaces},
        "batch_t_by_space": {
            space_id: global_batch_t for space_id in shared_spaces},
        "minimum_component_fraction_by_space": {
            space_id: global_minimum_component_fraction
            for space_id in shared_spaces},
        "ready_bucket_by_space": (
            ready_bucket_by_space
            if isinstance(ready_bucket_by_space, Mapping) else {}),
        "ready_minute_by_space": {
            space_id: global_ready_minute for space_id in shared_spaces},
        "campaign_states_by_space": campaign_states,
    }


def _chevron_recipe_from_config(
        config: Mapping, space_id: str) -> List[Dict[str, object]]:
    """Return the effective matrix from persisted or staged configuration.

    A physically resolved campaign always wins.  An optimized draft uses the
    matrix proposed by the blend-design solver.  Only Matrix mode reads the
    operator-authored legacy ``recipes`` rows directly.
    """
    states = config.get("campaign_states_by_space", {})
    states = states if isinstance(states, Mapping) else {}
    state = _normalise_campaign_state(states.get(space_id, {}))
    if state["resolved_recipe"]:
        return [dict(row) for row in state["resolved_recipe"]]
    proposed = config.get("proposed_recipes_by_space", {})
    proposed = proposed if isinstance(proposed, Mapping) else {}
    proposed_rows = _normalise_recipe_rows(proposed.get(space_id, []))
    if proposed_rows:
        return proposed_rows
    modes = config.get("mode_by_space", {})
    modes = modes if isinstance(modes, Mapping) else {}
    mode = _blend_mode(config.get(
        "global_mode", modes.get(space_id, BLEND_MODE_MATRIX)))
    if mode == BLEND_MODE_OPTIMIZED:
        return []
    catalog = _normalise_matrix_catalog(config.get("matrix_catalog", []))
    selected = config.get("proposed_matrix_by_space", {})
    selected = selected if isinstance(selected, Mapping) else {}
    selected_id = str(selected.get(space_id, "") or "")
    if selected_id:
        for entry in catalog:
            if entry["id"] == selected_id:
                return [dict(row) for row in entry["components"]]
    if len(catalog) == 1:
        return [dict(row) for row in catalog[0]["components"]]
    if len(catalog) > 1:
        return []
    recipes = config.get("recipes", {})
    recipes = recipes if isinstance(recipes, Mapping) else {}
    return _normalise_recipe_rows(recipes.get(space_id, []))


def chevron_mode(scenario, space_id: str) -> str:
    return _blend_mode(chevron_settings(scenario)["global_mode"])


def chevron_is_demand_driven(scenario) -> bool:
    """Whether the scheduler determines each pile's quantity and ready time."""
    return chevron_settings(scenario)["quantity_mode"] == "DEMAND_DRIVEN"


def chevron_minimum_input_t(scenario) -> float:
    """Minimum physical tonnes for each input grade actually used in a blend."""
    return float(chevron_settings(scenario)["min_input_grade_t"])


def _campaign_metadata(config: Mapping, space_id: str) -> Dict[str, object]:
    """A started physical campaign takes precedence over a new plan proposal."""
    states = config.get("campaign_states_by_space", {})
    states = states if isinstance(states, Mapping) else {}
    state = _normalise_campaign_state(states.get(space_id, {}))
    if state["batch_t"] > 0 or state["resolved_recipe"]:
        return state
    proposals = config.get("proposed_campaigns_by_space", {})
    proposals = proposals if isinstance(proposals, Mapping) else {}
    proposal = proposals.get(space_id, {})
    return dict(proposal) if isinstance(proposal, Mapping) else {}


def chevron_component_bounds(
        scenario, space_id: str) -> List[Dict[str, object]]:
    raw = chevron_settings(scenario)["optimized_share_bounds"]
    return _normalise_share_bounds(raw)


def chevron_resolved_recipe(
        scenario, space_id: str) -> List[Dict[str, object]]:
    """Return only a solver-proposed or physically frozen composition."""
    settings = chevron_settings(scenario)
    state = settings["campaign_states_by_space"].get(space_id, {})
    resolved = _normalise_campaign_state(state)["resolved_recipe"]
    if resolved:
        return [dict(row) for row in resolved]
    proposed = settings["proposed_recipes_by_space"].get(space_id, [])
    return _normalise_recipe_rows(proposed)


def chevron_component_grade_ids(scenario, space_id: str) -> List[str]:
    """Eligible stacking grades for the selected policy mode."""
    if chevron_mode(scenario, space_id) == BLEND_MODE_OPTIMIZED:
        rows = chevron_component_bounds(scenario, space_id)
        resolved = chevron_resolved_recipe(scenario, space_id)
        source = resolved or rows
    else:
        source = chevron_recipe(scenario, space_id)
        if not source:
            source = [
                component
                for entry in chevron_settings(scenario)["matrix_catalog"]
                for component in entry["components"]
            ]
    return list(dict.fromkeys(
        str(row.get("grade_id", "") or "") for row in source
        if str(row.get("grade_id", "") or "")))


def chevron_minimum_component_fraction(scenario, space_id: str) -> float:
    """Minimum non-zero solver-selected component (default: 5% of batch)."""
    if chevron_is_demand_driven(scenario):
        return 0.0
    raw = chevron_settings(scenario)["global_minimum_component_fraction"]
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 0.05
    return min(max(value, 0.0), 0.5)


def chevron_recipe(scenario, space_id: str) -> List[Dict[str, object]]:
    """Return valid positive recipe components, normalised to a unit share."""
    return _chevron_recipe_from_config(chevron_settings(scenario), space_id)


def chevron_matrix_catalog(scenario) -> List[Dict[str, object]]:
    """Reusable fixed matrices eligible for every enabled M16 stockpile."""
    return [
        {"id": str(entry["id"]), "name": str(entry["name"]),
         "components": [dict(row) for row in entry["components"]]}
        for entry in chevron_settings(scenario)["matrix_catalog"]
    ]


def chevron_catalog_allocations(scenario) -> List[Dict[str, object]]:
    """Catalogue recipes converted to exact whole-tonne campaign targets."""
    batch_t = chevron_construction_batch_t(scenario, "")
    return [{
        "id": entry["id"],
        "name": entry["name"],
        "components": _allocate_recipe_tonnes(
            batch_t, [dict(row) for row in entry["components"]]),
    } for entry in chevron_matrix_catalog(scenario)]


def chevron_enabled_for_space(scenario, space_id: str) -> bool:
    settings = chevron_settings(scenario)
    return bool(space_id in settings["active_spaces"] and len(
        chevron_component_grade_ids(scenario, space_id)) >= 2)


def chevron_campaign_state(scenario, space_id: str) -> Dict[str, object]:
    """Current explicit construction state for one stockpile campaign.

    Missing state means ``NOT_STARTED``.  Neither elapsed time nor aggregate
    pile inventory is allowed to manufacture a completion state.
    """
    state = chevron_settings(scenario)["campaign_states_by_space"].get(
        space_id, {})
    return _normalise_campaign_state(state)


def chevron_campaign_is_complete(scenario, space_id: str) -> bool:
    """Whether actual/carried stacking progress completed the recipe."""
    return chevron_campaign_state(scenario, space_id)["status"] == \
        CAMPAIGN_COMPLETE


def reopen_depleted_chevron_campaigns(
        scenario, inventory_by_space: Mapping[str, object] | None = None) -> set[str]:
    """Reopen emptied v7 blend locations on a private planning/execution copy.

    Completion of the old composition and zero physical inventory are both
    required. Accounting ids survive so carrying historical jobs again cannot
    rebuild an already depleted lot. No unresolved or partly built material is
    cleared, even if its aggregate inventory happens to be zero.
    """
    if not chevron_is_demand_driven(scenario):
        return set()
    settings = chevron_settings(scenario)
    inventory = inventory_by_space if isinstance(inventory_by_space, Mapping) else {}
    reopened: set[str] = set()
    for space_id in settings["enabled_spaces"]:
        state = settings["campaign_states_by_space"].get(space_id, {})
        if state.get("status") not in {CAMPAIGN_COMPLETE, CAMPAIGN_DEPLETED}:
            continue
        remaining = state.get("remaining_inventory_t")
        if remaining is not None and float(remaining) > 1e-6:
            continue
        try:
            physical = inventory.get(space_id, scenario.space(space_id).mass_t)
            if isinstance(physical, (list, tuple)):
                if not physical:
                    continue
                physical = physical[-1]
            physical_t = float(physical)
        except (StopIteration, TypeError, ValueError):
            continue
        if not isfinite(physical_t) or physical_t < -1e-6 or physical_t > 1e-6:
            continue
        reopened.add(space_id)
    if not reopened:
        return reopened
    raw = dict(scenario.config.get(CHEVRON_CONFIG_KEY, {}) or {})
    states = dict(raw.get("campaign_states_by_space", {}) or {})
    for space_id in reopened:
        old_state = settings["campaign_states_by_space"][space_id]
        states[space_id] = _normalise_campaign_state({
            "status": CAMPAIGN_NOT_STARTED,
            "accounted_job_t_by_id": dict(old_state.get("accounted_job_t_by_id", {})),
        })
    raw["campaign_states_by_space"] = states
    for key in ("proposed_campaigns_by_space", "proposed_recipes_by_space",
                "proposed_matrix_by_space"):
        values = raw.get(key, {})
        values = dict(values) if isinstance(values, Mapping) else {}
        for space_id in reopened:
            values.pop(space_id, None)
        raw[key] = values
    scenario.config[CHEVRON_CONFIG_KEY] = raw
    return reopened


def chevron_recipe_label(scenario, space_id: str) -> str:
    if not chevron_recipe(scenario, space_id):
        if chevron_mode(scenario, space_id) == BLEND_MODE_OPTIMIZED:
            return "Solver decides percentages at plan generation"
        if len(chevron_matrix_catalog(scenario)) > 1:
            return "Planner selects one catalogue matrix per campaign"
    return " + ".join(
        f"{component['grade_id']} {100 * float(component['target_fraction']):.0f}%"
        for component in chevron_recipe(scenario, space_id)
    )


def chevron_result_grade(scenario, space_id: str) -> str:
    """Grade campaign represented by the completed blended stockpile."""
    settings = chevron_settings(scenario)
    metadata = _campaign_metadata(settings, space_id)
    configured = str(metadata.get("result_grade", "") or "").strip()
    if not configured and not chevron_is_demand_driven(scenario):
        configured = str(settings["global_result_grade"] or "").strip()
    if configured:
        return configured
    try:
        return str(scenario.space(space_id).grade or "")
    except StopIteration:
        return ""


def chevron_effective_grade(scenario, space_id: str) -> str:
    """Grade available after a planned or physically completed campaign.

    An enabled campaign uses the configured result grade for its future
    post-completion reclaim candidate.  Once construction is physically
    complete, that identity persists even if future construction is disabled.
    """
    if chevron_enabled_for_space(scenario, space_id) or \
            chevron_campaign_is_complete(scenario, space_id):
        return chevron_result_grade(scenario, space_id)
    try:
        return str(scenario.space(space_id).grade or "")
    except StopIteration:
        return ""


def chevron_construction_batch_t(scenario, space_id: str) -> int:
    """Physical tonnes that must be stacked to complete this blend batch."""
    settings = chevron_settings(scenario)
    metadata = _campaign_metadata(settings, space_id)
    raw = metadata.get("batch_t", 0)
    if not raw:
        raw = sum(_non_negative_float(row.get("tonnage_t", 0))
                  for row in metadata.get("resolved_recipe", []))
    if not raw and not chevron_is_demand_driven(scenario):
        raw = settings["global_batch_t"]
    try:
        return max(int(round(float(raw or 0))), 0)
    except (TypeError, ValueError):
        return 0


def chevron_ready_minute(scenario, space_id: str) -> int:
    """Canonical readiness offset in minutes from the original plan origin."""
    settings = chevron_settings(scenario)
    metadata = _campaign_metadata(settings, space_id)
    if metadata and (chevron_is_demand_driven(scenario) or metadata.get("ready_minute")):
        return int(_non_negative_float(metadata.get("ready_minute", 0)))
    if chevron_is_demand_driven(scenario):
        return 0
    return int(settings["global_ready_minute"] or 0)


def chevron_ready_bucket(scenario, space_id: str) -> int:
    """Backward-compatible bucket view of the canonical readiness minute."""
    step_min = max(int(scenario.sim.native_bucket_minutes()), 1)
    return int(ceil(chevron_ready_minute(
        scenario, space_id) / float(step_min)))


def _allocate_recipe_tonnes(
        batch_t: int, recipe: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Allocate one exact integer batch without rounding mass drift."""
    if len(recipe) < 2 or batch_t <= 0:
        return []
    supplied_tonnes: List[int] = []
    exact_tonnes_available = True
    for component in recipe:
        raw = component.get("tonnage_t", None)
        if raw is None:
            exact_tonnes_available = False
            break
        try:
            supplied_tonnes.append(max(int(round(float(raw))), 0))
        except (TypeError, ValueError, OverflowError):
            exact_tonnes_available = False
            break
    if exact_tonnes_available and sum(supplied_tonnes) == batch_t:
        tonnes = supplied_tonnes
    else:
        exact = [batch_t * float(component["target_fraction"])
                 for component in recipe]
        tonnes = [int(value) for value in exact]
        remaining = batch_t - sum(tonnes)
        order = sorted(range(len(recipe)),
                       key=lambda index: exact[index] - tonnes[index],
                       reverse=True)
        for index in order[:remaining]:
            tonnes[index] += 1
    return [
        {"grade_id": str(component["grade_id"]),
         "target_fraction": tonnes[index] / float(batch_t),
         "tonnage_t": tonnes[index]}
        for index, component in enumerate(recipe)
        if tonnes[index] > 0
    ]


def chevron_batch_allocation_from_config(
        scenario, space_id: str, config: Mapping) -> List[Dict[str, object]]:
    """Allocate a batch from persisted or not-yet-applied UI configuration."""
    config = config if isinstance(config, Mapping) else {}
    recipe = _chevron_recipe_from_config(config, space_id)
    batches = config.get("batch_t_by_space", {})
    batches = batches if isinstance(batches, Mapping) else {}
    try:
        metadata = _campaign_metadata(config, space_id)
        raw_batch = metadata.get("batch_t", 0)
        if not raw_batch and str(config.get("quantity_mode", "")).upper() == "DEMAND_DRIVEN":
            raw_batch = sum(_non_negative_float(row.get("tonnage_t", 0))
                            for row in recipe)
        elif not raw_batch:
            raw_batch = config.get("global_batch_t", batches.get(space_id, 0))
        batch_t = max(int(round(float(raw_batch or 0))), 0)
    except (TypeError, ValueError, OverflowError):
        batch_t = 0
    return _allocate_recipe_tonnes(batch_t, recipe)


def chevron_batch_allocation(scenario, space_id: str) -> List[Dict[str, object]]:
    """Allocate whole physical tonnes to recipe grades without mass drift."""
    return chevron_batch_allocation_from_config(
        scenario, space_id, chevron_settings(scenario))


def update_chevron_campaign_state(
        scenario, jobs: Iterable[object] = (),
        final_inventory_by_space: Mapping[str, object] | None = None, *,
        actual_tonnage_by_job: Mapping[str, object] | None = None,
        completed_through_minute: int | None = None,
        include_planned: bool = False) -> Dict[str, Dict[str, object]]:
    """Carry actual or explicitly accepted planned construction progress.

    This mutates ``Scenario.config[CHEVRON_STACKING]`` and returns its
    normalised ``campaign_states_by_space`` mapping.  By default only jobs in
    a terminal successful state are counted.  An execution caller can pass a
    cumulative ``actual_tonnage_by_job`` mapping (including interrupted jobs);
    a rolling planning caller may opt into ``include_planned=True`` when its
    terminal inventory is being carried as an accepted plan boundary.

    Accounted tonnes are stored by job id, making repeated calls idempotent and
    allowing a later cumulative actual value to contribute only its delta.
    Completion is derived solely from grade-specific physical tonnes, never
    from the configured ready time or aggregate pile mass.
    """
    raw = scenario.config.get(CHEVRON_CONFIG_KEY, {})
    raw = dict(raw) if isinstance(raw, Mapping) else {}
    settings = chevron_settings(scenario)
    states = {
        str(space_id): _normalise_campaign_state(state)
        for space_id, state in settings["campaign_states_by_space"].items()
    }
    actuals = (actual_tonnage_by_job
               if isinstance(actual_tonnage_by_job, Mapping) else {})
    successful_statuses = {"FINISHED", "COMPLETED", "EXECUTED"}
    touched: set[str] = set()
    completion_minute_by_space: Dict[str, int] = {}

    for job in jobs or ():
        if str(getattr(job, "kind", "") or "").lower() != "stack":
            continue
        if str(getattr(job, "method", "") or "").lower() != "chevron" and \
                str(getattr(job, "blend_mode", "") or "").lower() != \
                "chevron stacking":
            continue
        target = str(getattr(job, "target_space", "") or
                     getattr(job, "space", "") or "")
        grade_id = str(getattr(job, "grade", "") or "")
        job_recipe = _normalise_recipe_rows(
            getattr(job, "blend_recipe", []) or [])
        if not target or not grade_id or not (
                chevron_recipe(scenario, target) or job_recipe):
            continue
        job_id = str(getattr(job, "id", "") or "")
        if not job_id:
            continue
        if job_id in actuals:
            actual_t = _non_negative_float(actuals[job_id])
        elif include_planned or str(getattr(
                job, "status", "") or "").upper() in successful_statuses:
            actual_t = _non_negative_float(getattr(job, "tonnage_t", 0.0))
        else:
            continue

        state = states.setdefault(target, _normalise_campaign_state({}))
        if state["status"] == CAMPAIGN_DEPLETED:
            continue
        accounted = dict(state["accounted_job_t_by_id"])
        prior_t = _non_negative_float(accounted.get(job_id, 0.0))
        delta_t = actual_t - prior_t
        if abs(delta_t) <= 1e-9:
            continue
        if delta_t > 1e-9 and not state.get("campaign_id"):
            metadata = _campaign_metadata(settings, target)
            state["campaign_id"] = str(metadata.get("campaign_id", "") or "")
        if delta_t > 1e-9 and job_recipe and not include_planned and not \
                state.get("resolved_recipe"):
            # Only positive executed/accepted actual stacking freezes the
            # matrix as a physical campaign contract. Rolling-plan carry may
            # preserve planned progress internally, but it must not make an
            # unexecuted proposal look physically locked.
            state["resolved_recipe"] = job_recipe
            state["resolution_source"] = chevron_mode(scenario, target)
            metadata = _campaign_metadata(settings, target)
            state["batch_t"] = _non_negative_float(metadata.get("batch_t", 0)) or sum(
                _non_negative_float(row.get("tonnage_t", 0)) for row in job_recipe)
            if not state["batch_t"]:
                state["batch_t"] = chevron_construction_batch_t(scenario, target)
            state["ready_minute"] = chevron_ready_minute(scenario, target)
            state["result_grade"] = chevron_result_grade(scenario, target)
            selected = settings.get("proposed_matrix_by_space", {})
            state["selected_matrix"] = str(metadata.get("selected_matrix", "") or (
                selected.get(target, "") if isinstance(selected, Mapping) else ""))
            state["campaign_id"] = str(metadata.get("campaign_id", "") or "")
        stacked = dict(state["stacked_t_by_grade"])
        stacked[grade_id] = max(
            _non_negative_float(stacked.get(grade_id, 0.0)) + delta_t, 0.0)
        if stacked[grade_id] <= 1e-9:
            stacked.pop(grade_id, None)
        if actual_t <= 1e-9:
            accounted.pop(job_id, None)
        else:
            accounted[job_id] = actual_t
        state["stacked_t_by_grade"] = stacked
        state["accounted_job_t_by_id"] = accounted
        touched.add(target)
        job_end = getattr(job, "end_minute", None)
        if job_end is None:
            job_end = (int(getattr(job, "end_bucket", 0)) + 1) * int(
                scenario.sim.native_bucket_minutes())
        completion_minute_by_space[target] = max(
            completion_minute_by_space.get(target, 0), int(job_end))

    inventory = (final_inventory_by_space
                 if isinstance(final_inventory_by_space, Mapping) else {})
    for space_id, value in inventory.items():
        sid = str(space_id)
        if sid not in states and sid not in settings["recipes"]:
            continue
        if isinstance(value, (list, tuple)):
            value = value[-1] if value else 0.0
        state = states.setdefault(sid, _normalise_campaign_state({}))
        state["remaining_inventory_t"] = _non_negative_float(value)
        touched.add(sid)

    for space_id in touched:
        state = states[space_id]
        batch_t = int(round(float(state.get("batch_t", 0) or
                                  chevron_construction_batch_t(scenario, space_id))))
        state_recipe = _normalise_recipe_rows(
            state.get("resolved_recipe", []))
        allocation = _allocate_recipe_tonnes(
            batch_t, state_recipe or chevron_recipe(scenario, space_id))
        stacked = state["stacked_t_by_grade"]
        complete = bool(allocation) and all(
            _non_negative_float(stacked.get(str(row["grade_id"]), 0.0))
            + 1e-6 >= float(row["tonnage_t"])
            for row in allocation)
        if complete:
            remaining = state.get("remaining_inventory_t", None)
            if remaining is not None and float(remaining) <= 1e-6:
                state["status"] = CAMPAIGN_DEPLETED
            else:
                state["status"] = CAMPAIGN_COMPLETE
            if state.get("completed_minute") is None:
                state["completed_minute"] = (
                    max(int(completed_through_minute), 0)
                    if completed_through_minute is not None
                    else completion_minute_by_space.get(space_id, None))
        elif sum(float(value) for value in stacked.values()) > 1e-6:
            state["status"] = CAMPAIGN_BUILDING
            state["completed_minute"] = None
        else:
            state["status"] = CAMPAIGN_NOT_STARTED
            state["completed_minute"] = None

    # Persist canonical minutes at the first state-carry boundary so later
    # grid refinement never has to reinterpret a legacy bucket count.
    raw["ready_minute_by_space"] = dict(settings["ready_minute_by_space"])
    raw["campaign_states_by_space"] = {
        space_id: _normalise_campaign_state(state)
        for space_id, state in states.items()
    }
    scenario.config[CHEVRON_CONFIG_KEY] = raw
    return {
        space_id: _normalise_campaign_state(state)
        for space_id, state in raw["campaign_states_by_space"].items()
    }


def blended_quality_for_components(
        scenario, recipe: Iterable[Mapping[str, object]]) -> Dict[str, float]:
    """Return one composition's quality on the canonical stage mass basis.

    Shares are physical wet-product tonnes. Chemistry is therefore weighted
    by each component's dry-solids mass, while moisture remains weighted by
    physical mass. This helper is shared by planning and previews so different
    product moistures cannot produce contradictory displayed qualities.
    """
    recipe = _normalise_recipe_rows(list(recipe))
    if not recipe:
        return {}
    components = []
    for component in recipe:
        grade = scenario.grade(str(component["grade_id"]))
        if grade is None:
            return {}  # Unknown material must not disappear from the denominator.
        components.append((grade, float(component["target_fraction"])))
    # An assay is known for the mixture only if every positive component has
    # it. Averaging the assayed subset certifies unmeasured material and can
    # conceal a contaminant in fixed-matrix campaigns.
    parameters = sorted(set.intersection(*(
        set(grade.quality) for grade, share in components if share > 0)))
    quality: Dict[str, float] = {}
    for parameter in parameters:
        numerator = 0.0
        denominator = 0.0
        for grade, share in components:
            if not isfinite(float(grade.quality[parameter])):
                denominator = 0.0
                break
            if parameter == "moisture":
                basis = 1.0
            else:
                moisture = getattr(grade, "product_moisture_pct", None)
                if moisture is None:
                    moisture = grade.quality.get("moisture", 0.0)
                basis = max(1.0 - float(moisture or 0.0) / 100.0, 0.0)
            numerator += share * basis * float(grade.quality[parameter])
            denominator += share * basis
        if denominator > 0:
            quality[parameter] = round(numerator / denominator, 12)
    return quality


def chevron_blended_quality(scenario, space_id: str) -> Dict[str, float]:
    """Return the recipe-weighted average quality of a constructed pile.

    The first implementation represents a completed chevron campaign by its
    average composition. It does not claim layer-by-layer reclaim prediction.
    """
    if space_id in scenario.config.get(UNKNOWN_STOCK_QUALITY_KEY, []) \
            and not scenario.space(space_id).quality:
        return {}
    state = chevron_campaign_state(scenario, space_id)
    if state.get("status") == CAMPAIGN_COMPLETE and state.get("resolved_recipe"):
        measured = scenario.space(space_id).quality
        if measured:
            # Executed material may be reassayed after construction. A replan
            # must use the carried physical assay, not re-certify catalogue
            # chemistry that actuals have already disproved.
            return dict(measured)
    recipe = chevron_recipe(scenario, space_id)
    carries_blend = (
        chevron_enabled_for_space(scenario, space_id)
        or chevron_campaign_is_complete(scenario, space_id))
    if not carries_blend or len(recipe) < 2:
        return dict(scenario.quality_of(space_id))
    # Fixed percentages can round to different physical proportions in the
    # integer-tonne scheduler. Certify the material that will actually be built.
    allocation = chevron_batch_allocation(scenario, space_id)
    quality = blended_quality_for_components(scenario, allocation or recipe)
    return quality


def chevron_pass_allocation(scenario, space_id: str) -> List[Dict[str, object]]:
    """Allocate a repeatable pass cycle using largest-remainder rounding."""
    if chevron_is_demand_driven(scenario):
        # Pass counts need a scheduled quantity/rate and physical traverse
        # speed. They cannot be inferred from a share matrix alone.
        return []
    recipe = chevron_recipe(scenario, space_id)
    if len(recipe) < 2:
        return []
    passes = int(chevron_settings(scenario)["passes_per_cycle"])
    exact = [passes * float(component["target_fraction"])
             for component in recipe]
    counts = [int(value) for value in exact]
    remaining = passes - sum(counts)
    order = sorted(range(len(recipe)),
                   key=lambda index: exact[index] - counts[index],
                   reverse=True)
    for index in order[:remaining]:
        counts[index] += 1
    return [
        {"grade_id": str(component["grade_id"]),
         "target_fraction": float(component["target_fraction"]),
         "passes": counts[index]}
        for index, component in enumerate(recipe)
    ]


def _reachable_receiving_specs(scenario, space_id: str, result_grade: str):
    """Return downstream receiving specifications relevant to one campaign."""
    try:
        from .routes import RouteGraph, space_node
        graph = RouteGraph.from_scenario(scenario)
        origin = space_node(scenario, space_id)
    except (ImportError, KeyError, StopIteration, ValueError):
        return []
    relevant = []
    line_states = scenario.config.get(
        "DESTINATION_LINE_OPERATING_STATE", {})
    line_states = line_states if isinstance(line_states, Mapping) else {}
    for destination in scenario.destinations:
        if getattr(destination, "demand_kind", "delivery") != "delivery":
            continue
        if str(line_states.get(destination.id, "")).strip().upper() in {
                "DISABLED", "FUTURE"}:
            continue
        if graph.best_path(origin, destination.node_id) is None:
            continue
        requested = {
            str(getattr(destination, "requested_grade", "") or "").strip()
        }
        requested.update(
            str(getattr(entry, "requested_grade", "") or
                getattr(destination, "requested_grade", "") or "").strip()
            for entry in scenario.demand
            if entry.dest_id == destination.id and float(entry.rate_tph) > 0)
        requested.discard("")
        # A washed-product specification grade is an acceptance envelope,
        # not a physical source-grade identity. It therefore keeps every
        # routeable physical blend eligible for downstream chemistry checks.
        # Other specification classes retain their dedicated feature logic.
        physical_requested = {
            grade_id for grade_id in requested
            if not (scenario.grade(grade_id) is not None and
                    getattr(scenario.grade(grade_id),
                            "is_specification", False) and
                    str(getattr(scenario.grade(grade_id),
                                "material_class", "")) == "washed")
        }
        if result_grade and physical_requested and \
                result_grade not in physical_requested:
            continue
        relevant.append(destination)
    return relevant


def _active_receiving_specs(scenario, destination):
    """Unique receiving limits active in positive-demand campaigns."""
    specs = []
    seen = set()
    entries = [entry for entry in scenario.demand
               if entry.dest_id == destination.id and entry.rate_tph > 0]
    bundles = [scenario.demand_spec(destination.id, entry.bucket)
               for entry in entries] or [destination.spec]
    for bundle in bundles:
        for spec in bundle:
            signature = (spec.param, spec.pmin, spec.pmax, spec.target,
                         spec.hard, spec.averaging_h, spec.margin)
            if signature not in seen:
                seen.add(signature)
                specs.append(spec)
    return specs


def chevron_receiving_quality_checks(
        scenario, space_id: str) -> List[Dict[str, object]]:
    """Evaluate a resolved pile average against its reachable receivers."""
    quality = chevron_blended_quality(scenario, space_id)
    result_grade = chevron_result_grade(scenario, space_id)
    checks: List[Dict[str, object]] = []
    for destination in _reachable_receiving_specs(
            scenario, space_id, result_grade):
        for spec in _active_receiving_specs(scenario, destination):
            value = quality.get(spec.param, None)
            lower = (None if spec.pmin is None
                     else float(spec.pmin) + float(spec.margin))
            upper = (None if spec.pmax is None
                     else float(spec.pmax) - float(spec.margin))
            on_spec = value is not None and (
                lower is None or float(value) >= lower - 1e-9) and (
                upper is None or float(value) <= upper + 1e-9)
            checks.append({
                "destination": destination.id,
                "parameter": spec.param,
                "value": value,
                "minimum": lower,
                "maximum": upper,
                "target": spec.target,
                "hard": bool(spec.hard),
                "on_spec": bool(on_spec),
            })
    return checks


def resolve_optimized_blends(
        scenario,
        eligible_grade_ids_by_space: Optional[Mapping[str, Iterable[str]]] = None,
        minimum_component_t_by_space: Optional[Mapping[str, int]] = None,
) -> Tuple[Dict[str, List[Dict[str, object]]], List[str]]:
    """Preview a variable whole-batch composition without scheduling it.

    This standalone helper is retained for configuration previews and backwards
    compatibility.  Production planning does *not* use it: ``solve_plan`` puts
    the blend-tonnage variables in the same CP-SAT model as inventory, routes,
    equipment calendars and hourly jobs.  A preview produced here therefore
    must never be treated as proof that a schedulable blend exists.

    Integer variables are physical tonnes, so displayed percentages are derived
    results rather than free floating-point inputs.  The proposed matrix becomes
    a physical campaign record only when actual stacking progress is accepted by
    :func:`update_chevron_campaign_state`.

    Chemistry is mass-weighted on dry solids for washed product parameters;
    moisture itself remains physical-mass weighted.  Soft destination limits
    are minimized before material value, while hard limits remain mandatory.
    """
    settings = chevron_settings(scenario)
    enabled = [
        space_id for space_id in sorted(settings["active_spaces"])
        if chevron_mode(scenario, space_id) == BLEND_MODE_OPTIMIZED
    ]
    if not enabled:
        return {}, []
    policy_errors = chevron_config_errors(scenario)
    if policy_errors:
        return {}, policy_errors
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        return {}, [
            "Optimized blend shares require OR-Tools (pip install ortools)."]

    resolved: Dict[str, List[Dict[str, object]]] = {}
    errors: List[str] = []
    quality_scale = 100
    dry_scale = 1_000

    routeable_by_space = eligible_grade_ids_by_space or {}
    executable_minimum_by_space = minimum_component_t_by_space or {}
    for space_id in enabled:
        state = chevron_campaign_state(scenario, space_id)
        frozen = _normalise_recipe_rows(state.get("resolved_recipe", []))
        if frozen:
            resolved[space_id] = _allocate_recipe_tonnes(
                chevron_construction_batch_t(scenario, space_id), frozen)
            continue
        bounds = chevron_component_bounds(scenario, space_id)
        batch_t = chevron_construction_batch_t(scenario, space_id)
        if len(bounds) < 2 or batch_t <= 0:
            errors.append(
                f"{space_id}: optimized shares need at least two eligible "
                "qualities and a positive batch.")
            continue
        if eligible_grade_ids_by_space is not None:
            routeable = {
                str(grade_id) for grade_id in
                routeable_by_space.get(space_id, ())
            }
            unavailable = [
                str(row["grade_id"]) for row in bounds
                if float(row.get("max_fraction", 0.0)) > 0
                and str(row["grade_id"]) not in routeable
            ]
            if unavailable:
                errors.append(
                    f"{space_id}: no enabled inbound material route supplies "
                    + ", ".join(unavailable) + ".")
                continue
        model = cp_model.CpModel()
        tonnes = {}
        used = {}
        active_minimum_t = max(
            int(ceil(chevron_minimum_component_fraction(
                scenario, space_id) * batch_t - 1e-9)),
            int(executable_minimum_by_space.get(space_id, 1) or 1),
            1,
        )
        for row in bounds:
            grade_id = str(row["grade_id"])
            minimum = float(row["min_fraction"])
            maximum = float(row["max_fraction"])
            lower = max(int(ceil(minimum * batch_t - 1e-9)), 0)
            upper = min(int(floor(maximum * batch_t + 1e-9)), batch_t)
            if upper < lower:
                errors.append(
                    f"{space_id}/{grade_id}: share bounds contain no whole-"
                    "tonne allocation for this batch.")
                tonnes = {}
                break
            tonnes[grade_id] = model.new_int_var(
                lower, upper, f"blend_t_{space_id}_{grade_id}")
            used[grade_id] = model.new_bool_var(
                f"blend_used_{space_id}_{grade_id}")
            model.add(tonnes[grade_id] <= batch_t * used[grade_id])
            model.add(tonnes[grade_id] >= active_minimum_t * used[grade_id])
        if not tonnes:
            continue
        model.add(sum(tonnes.values()) == batch_t)
        model.add(sum(used.values()) >= 2)

        prior = dict(state.get("stacked_t_by_grade", {}))
        for grade_id, actual in prior.items():
            if grade_id not in tonnes and float(actual) > 1e-6:
                errors.append(
                    f"{space_id}: already-stacked quality '{grade_id}' is "
                    "not eligible in optimized mode.")
            elif grade_id in tonnes:
                model.add(tonnes[grade_id] >= int(ceil(float(actual))))
        if errors and errors[-1].startswith(f"{space_id}:"):
            continue

        result_grade = chevron_result_grade(scenario, space_id)
        destinations = _reachable_receiving_specs(
            scenario, space_id, result_grade)
        quality_slacks = []
        target_deviations = []
        max_mass_expression = max(batch_t * quality_scale * dry_scale * 200, 1)
        for destination in destinations:
            for spec in _active_receiving_specs(scenario, destination):
                grade_values = {}
                dry_coefficients = {}
                missing = []
                for grade_id in tonnes:
                    grade = scenario.grade(grade_id)
                    if grade is None or spec.param not in grade.quality:
                        missing.append(grade_id)
                        continue
                    if spec.param == "moisture":
                        dry_fraction = 1.0
                    else:
                        moisture = getattr(
                            grade, "product_moisture_pct", None)
                        if moisture is None:
                            moisture = grade.quality.get("moisture", 0.0)
                        dry_fraction = max(
                            1.0 - float(moisture or 0.0) / 100.0, 0.0)
                    dry_coefficient = int(round(dry_fraction * dry_scale))
                    dry_coefficients[grade_id] = dry_coefficient
                    grade_values[grade_id] = int(round(
                        float(grade.quality[spec.param])
                        * quality_scale * dry_coefficient))
                if missing:
                    errors.append(
                        f"{space_id}: {destination.id} constrains "
                        f"{spec.param}, missing on " + ", ".join(missing) + ".")
                    continue
                denominator = sum(
                    dry_coefficients[grade_id] * tonnes[grade_id]
                    for grade_id in tonnes)
                lhs = sum(
                    grade_values[grade_id] * tonnes[grade_id]
                    for grade_id in tonnes)
                if spec.pmin is not None:
                    bound = int(round(
                        (float(spec.pmin) + float(spec.margin))
                        * quality_scale))
                    if spec.hard:
                        model.add(lhs >= bound * denominator)
                    else:
                        slack = model.new_int_var(
                            0, max_mass_expression,
                            f"blend_qmin_{space_id}_{destination.id}_{spec.param}")
                        model.add(lhs + slack >= bound * denominator)
                        quality_slacks.append(
                            max(int(getattr(destination, "priority", 1)), 1)
                            * slack)
                if spec.pmax is not None:
                    bound = int(round(max(
                        float(spec.pmax) - float(spec.margin), 0.0)
                        * quality_scale))
                    if spec.hard:
                        model.add(lhs <= bound * denominator)
                    else:
                        slack = model.new_int_var(
                            0, max_mass_expression,
                            f"blend_qmax_{space_id}_{destination.id}_{spec.param}")
                        model.add(lhs <= bound * denominator + slack)
                        quality_slacks.append(
                            max(int(getattr(destination, "priority", 1)), 1)
                            * slack)
                if spec.target is not None:
                    target = int(round(float(spec.target) * quality_scale))
                    deviation = model.new_int_var(
                        0, max_mass_expression,
                        f"blend_target_{space_id}_{destination.id}_{spec.param}")
                    model.add(deviation >= lhs - target * denominator)
                    model.add(deviation >= target * denominator - lhs)
                    target_deviations.append(
                        max(int(getattr(destination, "priority", 1)), 1)
                        * deviation)
        if any(message.startswith(f"{space_id}:") for message in errors):
            continue

        # Use the same operator-configured quality priorities as the planning
        # model. ``rock_value`` is intentionally not used: it is an unvalidated
        # placeholder, not an approved cost for blend selection.
        try:
            offspec_weight = max(int(round(float(
                scenario.weights.w2_offspec))), 0)
            target_weight = max(int(round(float(
                scenario.weights.w4_giveaway))), 0)
        except (AttributeError, TypeError, ValueError):
            offspec_weight, target_weight = 200, 5
        objective_terms = []
        if offspec_weight and quality_slacks:
            objective_terms.append(offspec_weight * sum(quality_slacks))
        if target_weight and target_deviations:
            objective_terms.append(target_weight * sum(target_deviations))
        if objective_terms:
            model.minimize(sum(objective_terms))
        solver = cp_model.CpSolver()
        solver.parameters.num_workers = 1
        solver.parameters.random_seed = int(scenario.sim.seed)
        solver.parameters.max_deterministic_time = 1.0
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            errors.append(
                f"{space_id}: no feasible optimized blend matrix satisfies "
                "the configured share bounds and hard quality limits.")
            continue
        allocation = [
            {
                "grade_id": grade_id,
                "tonnage_t": int(solver.value(tonnes[grade_id])),
                "target_fraction": (
                    int(solver.value(tonnes[grade_id])) / float(batch_t)),
            }
            for grade_id in tonnes
            if int(solver.value(tonnes[grade_id])) > 0
        ]
        if len(allocation) < 2:
            errors.append(
                f"{space_id}: optimized solution did not contain two "
                "physical blend components.")
            continue
        resolved[space_id] = allocation

    if errors:
        return resolved, errors
    raw = scenario.config.get(CHEVRON_CONFIG_KEY, {})
    raw = dict(raw) if isinstance(raw, Mapping) else {}
    proposed = raw.get("proposed_recipes_by_space", {})
    proposed = dict(proposed) if isinstance(proposed, Mapping) else {}
    for space_id, allocation in resolved.items():
        proposed[space_id] = [dict(row) for row in allocation]
    raw["proposed_recipes_by_space"] = proposed
    scenario.config[CHEVRON_CONFIG_KEY] = raw
    return resolved, []


def _shared_chevron_config_errors(
        scenario, cfg: Mapping) -> List[str]:
    """Validate the v6 shared catalogue and per-pile enablement contract."""
    raw_enabled = cfg.get("enabled_spaces", [])
    enabled_spaces = {
        str(value) for value in raw_enabled
    } if isinstance(raw_enabled, (list, tuple, set)) else set()
    if not enabled_spaces:
        return []

    errors: List[str] = []
    spaces = {space.id: space for space in scenario.spaces}
    grades = {grade.id: grade for grade in scenario.grades}
    mode = _blend_mode(cfg.get("global_mode", BLEND_MODE_MATRIX))
    catalog = _normalise_matrix_catalog(cfg.get("matrix_catalog", []))
    bounds = _normalise_share_bounds(cfg.get("optimized_share_bounds", []))
    dynamic = str(cfg.get("quantity_mode", "")).upper() == "DEMAND_DRIVEN"
    if dynamic:
        try:
            minimum_t = float(cfg.get("min_input_grade_t", 500))
            if not isfinite(minimum_t) or minimum_t < 0:
                raise ValueError
        except (TypeError, ValueError):
            errors.append("Minimum tonnes per input grade must be finite and non-negative.")
        speed = cfg.get("stacking_traverse_speed_m_min")
        if speed is not None:
            try:
                if not isfinite(float(speed)) or float(speed) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append("Stacking traverse speed must be a positive finite value when supplied.")
    else:
        try:
            passes = int(cfg.get("passes_per_cycle", 20) or 0)
            if passes < 2:
                errors.append("Chevron passes per cycle must be at least 2.")
        except (TypeError, ValueError):
            errors.append("Chevron passes per cycle must be an integer.")

    def validate_rows(rows, label: str, *, fixed: bool) -> None:
        seen: set[str] = set()
        total = minimum_total = maximum_total = 0.0
        positive = potentially_active = 0
        for component in rows:
            grade_id = str(component.get("grade_id", "") or "").strip()
            grade = grades.get(grade_id)
            if grade is None:
                errors.append(f"{label}: unknown grade '{grade_id}'.")
            elif getattr(grade, "material_class", "") != "washed" or \
                    not getattr(grade, "quality", {}):
                errors.append(
                    f"{label}/{grade_id}: blend inputs must be washed "
                    "grades with configured quality values.")
            if grade_id in seen:
                errors.append(f"{label}: grade '{grade_id}' is duplicated.")
            seen.add(grade_id)
            if fixed:
                share = float(component.get("target_fraction", 0.0) or 0.0)
                total += share
                positive += int(share > 0)
            else:
                minimum = float(component.get("min_fraction", 0.0) or 0.0)
                maximum = float(component.get("max_fraction", 1.0)
                                if component.get("max_fraction", 1.0)
                                is not None else 1.0)
                if not isfinite(minimum) or not isfinite(maximum) or \
                        minimum < 0 or maximum < 0 or minimum > maximum or \
                        maximum > 1:
                    errors.append(
                        f"{label}/{grade_id or '?'}: require 0% <= minimum "
                        "<= maximum <= 100%.")
                minimum_total += minimum
                maximum_total += maximum
                potentially_active += int(maximum > 0)
        if fixed:
            if positive < 2:
                errors.append(
                    f"{label}: a blend matrix requires at least two qualities.")
            if positive and abs(total - 1.0) > 1e-6:
                errors.append(
                    f"{label}: fixed matrix shares must total 100% "
                    f"(now {total * 100:.1f}%).")
        else:
            if potentially_active < 2:
                errors.append(
                    "Optimized mode requires at least two potentially active "
                    "qualities.")
            if minimum_total > 1.0 + 1e-9 or maximum_total < 1.0 - 1e-9:
                errors.append(
                    "Optimized share ranges must satisfy sum(minimum) <= "
                    "100% <= sum(maximum).")

    if mode == BLEND_MODE_MATRIX:
        if not catalog:
            errors.append(
                "Matrix mode requires at least one shared catalogue matrix.")
        for entry in catalog:
            validate_rows(
                entry["components"], f"Matrix {entry['id']}", fixed=True)
    else:
        validate_rows(bounds, "Optimized-share policy", fixed=False)

    result_grade_id = str(cfg.get("global_result_grade", "") or "").strip()
    result_grade = grades.get(result_grade_id)
    if not dynamic and result_grade is None:
        errors.append(f"Unknown resulting grade '{result_grade_id}'.")
    elif not dynamic and (getattr(result_grade, "material_class", "") != "washed" or \
            not getattr(result_grade, "quality", {})):
        errors.append(
            f"resulting grade '{result_grade_id}' must be a washed grade "
            "with configured quality values.")
    batch_t = _non_negative_float(cfg.get("global_batch_t", 0))
    try:
        ready_minute = max(int(round(float(
            cfg.get("global_ready_minute", 0) or 0))), 0)
    except (TypeError, ValueError):
        ready_minute = 0
    if not dynamic and ready_minute <= 0:
        errors.append(
            "The shared reclaim-ready time must be after construction starts.")

    states_raw = cfg.get("campaign_states_by_space", {})
    states_raw = states_raw if isinstance(states_raw, Mapping) else {}
    for space_id in sorted(enabled_spaces):
        space = spaces.get(space_id)
        if space is None:
            errors.append(f"Unknown chevron target stockpile '{space_id}'.")
            continue
        if space.site != "MPH" or space.area != "M16-humid":
            errors.append(
                f"{space_id}: blend construction is limited to M16 "
                "humid-phosphate stockpiles.")
        if not any(machine.kind == "stacker" and
                   space_id in machine.reachable_spaces
                   for machine in scenario.machines):
            errors.append(
                f"{space_id}: no configured stacker can reach this stockpile.")
        if not dynamic and (batch_t <= 0 or batch_t > float(space.capacity_t)):
            errors.append(
                f"{space_id}: shared blend batch must be greater than zero "
                "and no larger than stockpile capacity.")
        state = _normalise_campaign_state(states_raw.get(space_id, {}))
        if dynamic and state["batch_t"] > float(space.capacity_t):
            errors.append(f"{space_id}: physical blend quantity exceeds stockpile capacity.")
        status = str(state["status"])
        if not dynamic and status == CAMPAIGN_NOT_STARTED and float(space.mass_t) > 1.0:
            errors.append(
                f"{space_id}: a new blend requires an empty stockpile; "
                "existing inventory cannot be relabelled.")
        elif status == CAMPAIGN_DEPLETED and (not dynamic or float(space.mass_t) > 1e-6
                or float(state.get("remaining_inventory_t") or 0) > 1e-6):
            errors.append(
                f"{space_id}: the previous blend is depleted; reset its "
                "campaign state before starting a new batch.")
        if status in {CAMPAIGN_BUILDING, CAMPAIGN_COMPLETE} and not \
                state["resolved_recipe"] and (dynamic or not (
                    mode == BLEND_MODE_MATRIX and len(catalog) == 1)):
            errors.append(
                f"{space_id}: an active campaign requires its selected "
                "matrix to be physically resolved.")
    return errors


def chevron_config_errors(scenario, settings=None) -> List[str]:
    """Validate only campaigns that currently control planning.

    Disabled draft recipes are intentionally ignored.  This lets an operator
    retain or clean up a draft (including one referring to a subsequently
    deleted grade) without blocking a different valid campaign.  A completed
    campaign remains a material-state record, but is not revalidated as a new
    construction request merely because its future-construction toggle is off.
    """
    cfg = (settings if isinstance(settings, Mapping)
           else scenario.config.get(CHEVRON_CONFIG_KEY, {}))
    cfg = cfg if isinstance(cfg, Mapping) else {}
    try:
        version = int(cfg.get("model_version", 0) or 0)
    except (TypeError, ValueError):
        version = 0
    if version >= CHEVRON_MODEL_VERSION or any(
            key in cfg for key in (
                "matrix_catalog", "global_mode", "global_batch_t")):
        return _shared_chevron_config_errors(scenario, cfg)
    recipes = cfg.get("recipes", {})
    if not isinstance(recipes, Mapping):
        return (["Chevron recipes must be a target-pile mapping."]
                if bool(cfg.get("enabled", False)) else [])
    raw_enabled = cfg.get("enabled_spaces", None)
    if isinstance(raw_enabled, (list, tuple, set)):
        enabled_spaces = {str(value) for value in raw_enabled}
    else:
        enabled_spaces = (
            {str(space_id) for space_id in recipes}
            if bool(cfg.get("enabled", False)) else set())
    if not enabled_spaces:
        return []

    errors: List[str] = []
    spaces = {space.id: space for space in scenario.spaces}
    grades = {grade.id: grade for grade in scenario.grades}
    result_grades = cfg.get("result_grades", {})
    result_grades = result_grades if isinstance(result_grades, Mapping) else {}
    modes = cfg.get("mode_by_space", {})
    modes = modes if isinstance(modes, Mapping) else {}
    share_bounds = cfg.get("share_bounds_by_space", {})
    share_bounds = share_bounds if isinstance(share_bounds, Mapping) else {}
    batches = cfg.get("batch_t_by_space", {})
    batches = batches if isinstance(batches, Mapping) else {}
    ready_minutes = _ready_minutes(scenario, cfg)
    states_raw = cfg.get("campaign_states_by_space", {})
    states_raw = states_raw if isinstance(states_raw, Mapping) else {}

    try:
        passes = int(cfg.get("passes_per_cycle", 20) or 0)
        if passes < 2:
            errors.append("Chevron passes per cycle must be at least 2.")
    except (TypeError, ValueError):
        errors.append("Chevron passes per cycle must be an integer.")

    for space_id in sorted(enabled_spaces):
        space = spaces.get(space_id)
        if space is None:
            errors.append(f"Unknown chevron target stockpile '{space_id}'.")
            continue
        if space.site != "MPH" or space.area != "M16-humid":
            errors.append(
                f"{space_id}: current blend construction is limited to M16 "
                "humid-phosphate stockpiles.")
        if not any(machine.kind == "stacker" and
                   space_id in machine.reachable_spaces
                   for machine in scenario.machines):
            errors.append(f"{space_id}: no configured stacker can reach this stockpile.")

        mode = _blend_mode(modes.get(space_id, BLEND_MODE_MATRIX))
        raw_components = (
            recipes.get(space_id, None) if mode == BLEND_MODE_MATRIX
            else share_bounds.get(space_id, None))
        if not isinstance(raw_components, (list, tuple)):
            errors.append(
                f"{space_id}: "
                + ("matrix" if mode == BLEND_MODE_MATRIX
                   else "optimized-share policy")
                + " must contain quality rows.")
            raw_components = ()
        seen: set[str] = set()
        total = 0.0
        positive = 0
        minimum_total = 0.0
        maximum_total = 0.0
        potentially_active = 0
        for component in raw_components:
            if not isinstance(component, Mapping):
                errors.append(f"{space_id}: invalid blend row.")
                continue
            grade_id = str(component.get("grade_id", "") or "").strip()
            grade = grades.get(grade_id)
            if grade is None:
                errors.append(f"{space_id}: unknown grade '{grade_id}'.")
            elif getattr(grade, "material_class", "") != "washed" or \
                    not getattr(grade, "quality", {}):
                errors.append(
                    f"{space_id}/{grade_id}: blend inputs must be washed "
                    "grades with configured quality values.")
            if grade_id in seen:
                errors.append(f"{space_id}: grade '{grade_id}' is duplicated.")
            seen.add(grade_id)
            if mode == BLEND_MODE_MATRIX:
                try:
                    share = float(component.get(
                        "target_fraction",
                        component.get("mass_fraction", 0.0)) or 0.0)
                except (TypeError, ValueError):
                    errors.append(
                        f"{space_id}/{grade_id or '?'}: fixed share is not "
                        "numeric.")
                    continue
                if not isfinite(share) or share <= 0:
                    errors.append(
                        f"{space_id}/{grade_id or '?'}: fixed matrix share "
                        "must be positive.")
                else:
                    positive += 1
                    total += share
            else:
                try:
                    minimum = float(component.get(
                        "min_fraction", 0.0) or 0.0)
                    maximum = float(component.get(
                        "max_fraction", 1.0)
                        if component.get("max_fraction", 1.0) is not None
                        else 1.0)
                except (TypeError, ValueError):
                    errors.append(
                        f"{space_id}/{grade_id or '?'}: minimum/maximum "
                        "shares must be numeric.")
                    continue
                if not isfinite(minimum) or not isfinite(maximum) or \
                        minimum < 0 or maximum < 0 or minimum > maximum or \
                        maximum > 1:
                    errors.append(
                        f"{space_id}/{grade_id or '?'}: require 0% <= "
                        "minimum <= maximum <= 100%.")
                else:
                    minimum_total += minimum
                    maximum_total += maximum
                    potentially_active += int(maximum > 0)
        if mode == BLEND_MODE_MATRIX:
            if positive < 2:
                errors.append(
                    f"{space_id}: a blend matrix requires at least two "
                    "qualities.")
            if positive and abs(total - 1.0) > 1e-6:
                errors.append(
                    f"{space_id}: fixed matrix shares must total 100% "
                    f"(now {total * 100:.1f}%).")
        else:
            if potentially_active < 2:
                errors.append(
                    f"{space_id}: optimized mode requires at least two "
                    "potentially active qualities.")
            if minimum_total > 1.0 + 1e-9 or maximum_total < 1.0 - 1e-9:
                errors.append(
                    f"{space_id}: optimized share ranges must satisfy "
                    "sum(minimum) <= 100% <= sum(maximum).")

        result_grade_id = str(result_grades.get(space_id, "") or "").strip()
        result_grade = grades.get(result_grade_id)
        if result_grade is None:
            errors.append(
                f"{space_id}: unknown resulting grade '{result_grade_id}'.")
        elif getattr(result_grade, "material_class", "") != "washed" or \
                not getattr(result_grade, "quality", {}):
            errors.append(
                f"{space_id}: resulting grade '{result_grade_id}' must be a "
                "washed grade with configured quality values.")

        batch_t = _non_negative_float(batches.get(space_id, 0))
        if batch_t <= 0 or batch_t > float(space.capacity_t):
            errors.append(
                f"{space_id}: blend batch must be greater than zero and no "
                "larger than stockpile capacity.")
        ready_minute = int(ready_minutes.get(space_id, 0) or 0)
        if ready_minute <= 0:
            errors.append(
                f"{space_id}: reclaim-ready time must be after construction starts.")

        state = _normalise_campaign_state(states_raw.get(space_id, {}))
        status = str(state["status"])
        stacked = state["stacked_t_by_grade"]
        if status == CAMPAIGN_NOT_STARTED and float(space.mass_t) > 1.0:
            errors.append(
                f"{space_id}: a new blend requires an empty stockpile; "
                "existing inventory cannot be relabelled.")
        elif status == CAMPAIGN_DEPLETED:
            errors.append(
                f"{space_id}: the previous blend is depleted; reset its "
                "campaign state before starting a new batch.")

        allocation = chevron_batch_allocation_from_config(
            scenario, space_id, cfg)
        if mode == BLEND_MODE_MATRIX and positive >= 2 and batch_t > 0 and \
                len(allocation) < 2:
            errors.append(
                f"{space_id}: integer-tonne rounding leaves fewer than two "
                "physical components; increase the batch or component shares.")
        targets = {str(row["grade_id"]): float(row["tonnage_t"])
                   for row in allocation}
        if status in {CAMPAIGN_BUILDING, CAMPAIGN_COMPLETE} and not targets:
            errors.append(
                f"{space_id}: an active campaign requires a resolved blend "
                "matrix.")
        for grade_id, tonnage in stacked.items():
            if grade_id not in targets and float(tonnage) > 1e-6:
                errors.append(
                    f"{space_id}: carried progress contains grade "
                    f"'{grade_id}' outside the active recipe.")
            elif float(tonnage) > targets.get(grade_id, 0.0) + 1e-6:
                errors.append(
                    f"{space_id}/{grade_id}: carried stacking exceeds its "
                    "configured batch allocation.")
        meets_targets = bool(targets) and all(
            float(stacked.get(grade_id, 0.0)) + 1e-6 >= target
            for grade_id, target in targets.items())
        if status == CAMPAIGN_COMPLETE and targets and not meets_targets:
            errors.append(
                f"{space_id}: COMPLETE status requires the full "
                "grade-specific batch allocation.")
        if status == CAMPAIGN_BUILDING and targets and meets_targets:
            errors.append(
                f"{space_id}: carried tonnes complete the recipe; update the "
                "campaign status to COMPLETE.")
    return errors


def chevron_job_metadata(scenario, target_space: str, grade_id: str) -> Dict[str, object]:
    """Metadata for one stack job that contributes to a chevron-built pile."""
    if not chevron_enabled_for_space(scenario, target_space):
        return {"method": "direct", "blend_mode": "",
                "blend_recipe": [], "chevron_passes_per_cycle": 0,
                "chevron_grade_passes": 0}
    recipe = chevron_recipe(scenario, target_space)
    recipe_grades = {str(component["grade_id"]) for component in recipe}
    if grade_id and grade_id not in recipe_grades:
        return {"method": "direct", "blend_mode": "",
                "blend_recipe": [], "chevron_passes_per_cycle": 0,
                "chevron_grade_passes": 0}
    grade_passes = next(
        (int(component["passes"])
         for component in chevron_pass_allocation(scenario, target_space)
         if str(component["grade_id"]) == grade_id),
        0,
    )
    return {
        "method": "chevron",
        "blend_mode": (
            "optimized shares"
            if chevron_mode(scenario, target_space) == BLEND_MODE_OPTIMIZED
            else "blend matrix"),
        "blend_recipe": recipe,
        "chevron_passes_per_cycle": (0 if chevron_is_demand_driven(scenario) else int(
            chevron_settings(scenario)["passes_per_cycle"])),
        "chevron_grade_passes": grade_passes,
    }


def chevron_job_pass_estimate(scenario, job) -> Dict[str, object]:
    """Derive traversals from flow time and an explicitly supplied stacking speed.

    A pass is one end-to-end stacking traversal, not a return cycle. Moving
    between jobs does not establish a machine's stacking traverse speed.
    """
    result: Dict[str, object] = {
        "status": "NOT_CHEVRON", "equivalent_passes": None, "passes": None,
        "flow_duration_min": 0.0, "traverse_length_m": None,
    }
    if str(getattr(job, "kind", "")).lower() != "stack" or str(
            getattr(job, "method", "")).lower() != "chevron":
        return result
    speed = _non_negative_float(chevron_settings(scenario).get(
        "stacking_traverse_speed_m_min"))
    if speed <= 0:
        result["status"] = "STACKING_SPEED_NOT_CONFIGURED"
        return result
    target = str(getattr(job, "target_space", "") or getattr(job, "space", ""))
    machine = next((item for item in scenario.machines
                    if item.id == getattr(job, "machine", "")), None)
    try:
        space = scenario.space(target)
    except StopIteration:
        result["status"] = "TRAVERSE_LENGTH_NOT_CONFIGURED"
        return result
    span = getattr(machine, "work_area", {}).get(target) if machine else None
    if not isinstance(span, (list, tuple)) or len(span) < 2:
        span = [getattr(space, "x_from", 0), getattr(space, "x_to", 0)]
    try:
        length = abs(float(span[1]) - float(span[0]))
    except (TypeError, ValueError):
        length = 0.0
    if not isfinite(length) or length <= 0:
        result["status"] = "TRAVERSE_LENGTH_NOT_CONFIGURED"
        return result
    windows = [window for window in getattr(job, "execution_windows", [])
               if str(getattr(window, "status", "")).upper() == "VALIDATED"]
    window_tonnage = sum(_non_negative_float(getattr(window, "tonnage_t", 0))
                         for window in windows)
    job_tonnage = _non_negative_float(getattr(job, "tonnage_t", 0))
    if windows and abs(window_tonnage - job_tonnage) <= max(1e-6, job_tonnage * 1e-6) and all(
            _non_negative_float(getattr(window, "rate_tph", 0)) > 0 for window in windows):
        duration_min = sum(_non_negative_float(getattr(window, "tonnage_t", 0))
                           / float(window.rate_tph) * 60.0 for window in windows)
    else:
        rate = _non_negative_float(getattr(job, "rate_tph", 0))
        duration_min = job_tonnage / rate * 60.0 if rate > 0 else 0
    if duration_min <= 0:
        result["status"] = "FLOW_DURATION_NOT_AVAILABLE"
        return result
    equivalent = duration_min * speed / length
    result.update({
        "status": "DERIVED_FROM_FLOW", "equivalent_passes": equivalent,
        "passes": int(ceil(equivalent - 1e-9)),
        "flow_duration_min": duration_min, "traverse_length_m": length,
    })
    return result


def update_chevron_job_passes(scenario, jobs: Iterable[object]) -> Dict[str, Dict[str, object]]:
    """Refresh job display counts after schedule refinement; unknowns stay zero."""
    estimates: Dict[str, Dict[str, object]] = {}
    if not chevron_is_demand_driven(scenario):
        return estimates
    for job in jobs:
        estimate = chevron_job_pass_estimate(scenario, job)
        if estimate["status"] == "NOT_CHEVRON":
            continue
        job.chevron_passes_per_cycle = 0  # No fixed repeatable cycle in v7.
        job.chevron_grade_passes = int(estimate["passes"] or 0)
        job.chevron_pass_estimate = dict(estimate)
        estimates[str(getattr(job, "id", ""))] = estimate
    return estimates
