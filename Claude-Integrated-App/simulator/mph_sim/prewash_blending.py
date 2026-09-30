"""Pre-washing feed-blend policy shared by configuration and optimisation.

The first implementation deliberately models a *daily washing-feed campaign*
through the documented/common reclaim headers.  It does not claim spatial
pile-face homogenisation. Enabled source stockpiles contribute to a shared
policy on each available washing line, which must draw the configured shares
during the civil day and apply one common treatment to that daily campaign.
"""
from __future__ import annotations

from copy import deepcopy
import math


CONFIG_KEY = "PRE_WASHING_BLENDING"
MODEL_VERSION = 4
SCOPE_DAILY = "DAILY"
BLEND_MODE_MATRIX = "MATRIX"
BLEND_MODE_OPTIMIZED = "OPTIMIZED"
BLEND_MODES = (BLEND_MODE_MATRIX, BLEND_MODE_OPTIMIZED)


def default_config(*, enabled: bool = False) -> dict:
    """Return the built-in S0 commissioning policy.

    The 5--95 % bounds are intentionally broad commissioning limits rather
    than a claimed plant recipe.  They prevent token components while leaving
    the optimiser free to select the quality/economic balance.
    """
    return {
        "model_version": MODEL_VERSION,
        "enabled": bool(enabled),
        "scope": SCOPE_DAILY,
        "evidence": (
            "ASSUMED_COMMISSIONING_BOUNDS; physical common-header routes "
            "modelled, plant blend matrices TBC"
        ),
        "points": {
            "BEN-PREWASH": {
                "name": "Benguerir screened-product pre-washing feed",
                "site": "BENGUERIR",
                "share_mode": BLEND_MODE_OPTIMIZED,
                "process_stages": [
                    "BEN-WASH-L1", "BEN-WASH-L2", "BEN-WASH-L3"],
                "enabled_process_stages": [
                    "BEN-WASH-L1", "BEN-WASH-L2", "BEN-WASH-L3"],
                "enabled_source_spaces": ["BG-RAW-1", "BG-RAW-2"],
                "allowed_treatment_modes": ["simple", "full"],
                "min_input_grade_t": 0.0,
                "optimized_components": [
                    {"source_space": "BG-RAW-1", "grade_id": "RAW-TBT",
                     "min_fraction": 0.05, "max_fraction": 0.95},
                    {"source_space": "BG-RAW-2",
                     "grade_id": "RAW-BT-LOW-CD",
                     "min_fraction": 0.05, "max_fraction": 0.95},
                ],
                "matrix_catalog": [],
            },
            "LOU-PREWASH": {
                "name": "Louta homogenized-ore pre-washing feed",
                "site": "LOUTA",
                "share_mode": BLEND_MODE_OPTIMIZED,
                "process_stages": ["LOU-WASH-L1", "LOU-WASH-L2"],
                "enabled_process_stages": ["LOU-WASH-L1", "LOU-WASH-L2"],
                "enabled_source_spaces": ["110LG-OL1", "110LG-OL2"],
                "allowed_treatment_modes": ["simple", "full"],
                "min_input_grade_t": 0.0,
                "optimized_components": [
                    {"source_space": "110LG-OL1", "grade_id": "RAW-BT",
                     "min_fraction": 0.05, "max_fraction": 0.95},
                    {"source_space": "110LG-OL2",
                     "grade_id": "RAW-BT-LOW-CD",
                     "min_fraction": 0.05, "max_fraction": 0.95},
                ],
                "matrix_catalog": [],
            },
        },
    }


def settings(sc) -> dict:
    raw = sc.config.get(CONFIG_KEY, {})
    if not isinstance(raw, dict):
        return deepcopy(raw)
    result = default_config(enabled=False)
    if raw:
        result.update(deepcopy(raw))
    points = result.get("points", {})
    if isinstance(points, dict):
        for point_id, point in points.items():
            if not isinstance(point, dict):
                continue
            legacy = deepcopy(point.get("components", []))
            point.setdefault("optimized_components", deepcopy(legacy))
            if "matrix_catalog" not in point:
                targets = []
                for row in legacy:
                    try:
                        targets.append(float(row.get("target_fraction")))
                    except (AttributeError, TypeError, ValueError):
                        targets = []
                        break
                point["matrix_catalog"] = ([{
                    "id": f"{point_id}-MATRIX-01",
                    "name": f"{point_id} matrix 1",
                    "components": legacy,
                }] if len(targets) >= 2 else [])
            point["components"] = deepcopy(
                point.get("optimized_components", legacy))
    result["model_version"] = max(
        int(result.get("model_version", 0) or 0), MODEL_VERSION)
    return result


def enabled(sc) -> bool:
    cfg = settings(sc)
    return isinstance(cfg, dict) and cfg.get("enabled") is True


def point_mode(point: dict) -> str:
    """Return the share-decision mode, preserving legacy variable policies."""
    mode = str(point.get(
        "share_mode", BLEND_MODE_OPTIMIZED) or "").strip().upper()
    return mode if mode in BLEND_MODES else mode


def effective_components(point: dict) -> list[dict]:
    """Return component bounds consumed by the optimizer.

    Fixed matrices use equal lower/upper shares. Optimized policies retain
    their configured envelope. This is the single translation boundary used
    by both the UI summary and CP-SAT constraints.
    """
    components = candidate_components(point)
    if point_mode(point) != BLEND_MODE_MATRIX:
        return components
    catalog = eligible_matrix_catalog(point)
    if len(catalog) != 1:
        return []
    return [{
        **deepcopy(component),
        "min_fraction": component.get("target_fraction"),
        "max_fraction": component.get("target_fraction"),
    } for component in catalog[0]["components"]]


def matrix_catalog(point: dict) -> list[dict]:
    """Stored recipes, independent of current stockpile enablement."""
    raw = point.get("matrix_catalog", [])
    if not isinstance(raw, list):
        return []
    result = []
    seen = set()
    for index, entry in enumerate(raw, 1):
        if not isinstance(entry, dict):
            continue
        matrix_id = str(entry.get(
            "id", f"MATRIX-{index:02d}") or f"MATRIX-{index:02d}")
        if matrix_id in seen:
            continue
        seen.add(matrix_id)
        components = [deepcopy(row) for row in entry.get(
            "components", []) if isinstance(row, dict)]
        result.append({
            "id": matrix_id,
            "name": str(entry.get("name", matrix_id) or matrix_id),
            "components": components,
        })
    return result


def enabled_source_spaces(point: dict) -> list[str]:
    """Source opt-ins; old imported policies retain their line-based meaning.

    Presence, not truthiness, of the new key makes it authoritative. An empty
    list therefore disables blend participation without deleting any recipe.
    """
    if "enabled_source_spaces" in point:
        raw = point.get("enabled_source_spaces", [])
        return list(dict.fromkeys(str(source) for source in raw)) \
            if isinstance(raw, list) else []
    if not point.get("enabled_process_stages", point.get("process_stages", [])):
        return []
    rows = list(point.get("optimized_components", point.get("components", [])))
    rows += [row for entry in matrix_catalog(point) for row in entry["components"]]
    return list(dict.fromkeys(
        str(row.get("source_space", "")) for row in rows
        if isinstance(row, dict) and row.get("source_space")))


def effective_process_stages(point: dict) -> list[str]:
    """Stockpile policy applies to all lines, subject to flow availability."""
    return list(point.get("process_stages", [])) if \
        "enabled_source_spaces" in point else list(point.get(
            "enabled_process_stages", point.get("process_stages", [])))


def eligible_matrix_catalog(point: dict) -> list[dict]:
    """Exclude entire recipes needing disabled sources; never renormalize."""
    enabled_sources = set(enabled_source_spaces(point))
    result = []
    for entry in matrix_catalog(point):
        try:
            positive = [row for row in entry["components"]
                        if float(row.get("target_fraction", 0)) > 0]
        except (TypeError, ValueError):
            continue  # configuration_errors reports malformed stored recipes
        if len(positive) >= 2 and all(
                str(row.get("source_space", "")) in enabled_sources
                for row in positive):
            result.append(entry)
    return result


def candidate_components(point: dict) -> list[dict]:
    """Union of site-level sources eligible under the active policy."""
    enabled_sources = set(enabled_source_spaces(point))
    if point_mode(point) == BLEND_MODE_OPTIMIZED:
        return [deepcopy(row) for row in point.get(
            "optimized_components", point.get("components", []))
            if str(row.get("source_space", "")) in enabled_sources]
    rows = []
    seen = set()
    for entry in eligible_matrix_catalog(point):
        for row in entry["components"]:
            if str(row.get("source_space", "")) not in enabled_sources or \
                    float(row.get("target_fraction", 0)) <= 0:
                continue
            key = (str(row.get("source_space", "")),
                   str(row.get("grade_id", "")))
            if key not in seen:
                rows.append(deepcopy(row))
                seen.add(key)
    return rows


def _has_effective_blend(point: dict) -> bool:
    if not effective_process_stages(point):
        return False
    if point_mode(point) == BLEND_MODE_MATRIX:
        return bool(eligible_matrix_catalog(point))
    rows = candidate_components(point)
    if len({row.get("source_space") for row in rows}) < 2:
        return False
    try:
        lower = [float(row["min_fraction"]) for row in rows]
        upper = [float(row["max_fraction"]) for row in rows]
    except (KeyError, TypeError, ValueError):
        return False
    return (all(math.isfinite(lo) and math.isfinite(hi) and 0 <= lo <= hi <= 1
                for lo, hi in zip(lower, upper))
            and sum(lo > 0 for lo in lower) >= 2
            and sum(lower) <= 1 + 1e-9 and sum(upper) >= 1 - 1e-9)


def enabled_points(sc) -> dict:
    cfg = settings(sc)
    if not isinstance(cfg, dict) or cfg.get("enabled") is not True:
        return {}
    points = cfg.get("points", {})
    if not isinstance(points, dict):
        return {}
    return {point_id: point for point_id, point in points.items()
            if isinstance(point, dict) and _has_effective_blend(point)}


def point_for_stage(sc, stage: str):
    for point_id, point in enabled_points(sc).items():
        active_stages = effective_process_stages(point)
        if stage in active_stages:
            return point_id, point
    return None


def source_spaces_for_stage(sc, stage: str) -> set[str]:
    found = point_for_stage(sc, stage)
    if found is None:
        return set()
    return {
        str(row.get("source_space", ""))
        for row in candidate_components(found[1])
        if str(row.get("source_space", ""))
    }


def configuration_errors(sc_or_settings) -> list[str]:
    has_scenario = hasattr(sc_or_settings, "config")
    raw = (sc_or_settings.config.get(CONFIG_KEY, {})
           if has_scenario else sc_or_settings)
    if raw == {}:
        return []
    errors: list[str] = []
    if not isinstance(raw, dict):
        return ["pre-washing blending configuration must be an object"]
    cfg = settings(sc_or_settings) if has_scenario else deepcopy(raw)
    if not has_scenario:
        for point_id, point in dict(cfg.get("points", {}) or {}).items():
            if not isinstance(point, dict):
                continue
            legacy = deepcopy(point.get("components", []))
            point.setdefault("optimized_components", deepcopy(legacy))
            if "matrix_catalog" not in point:
                targets = []
                for row in legacy:
                    try:
                        targets.append(float(row.get("target_fraction")))
                    except (AttributeError, TypeError, ValueError):
                        targets = []
                        break
                point["matrix_catalog"] = ([{
                    "id": f"{point_id}-MATRIX-01",
                    "components": legacy,
                }] if len(targets) >= 2 else [])
    if type(cfg.get("enabled")) is not bool:
        errors.append("enabled must be boolean")
    if cfg.get("scope") != SCOPE_DAILY:
        errors.append("scope must be DAILY")
    points = cfg.get("points")
    if not isinstance(points, dict) or not points:
        errors.append("points must be a non-empty object")
        return errors

    known_spaces = ({space.id for space in sc_or_settings.spaces}
                    if has_scenario else None)
    known_stages = ({transfer.process_stage
                     for transfer in sc_or_settings.process_transfers}
                    if has_scenario else None)
    claimed_stages: set[str] = set()
    for point_id, point in points.items():
        prefix = str(point_id)
        if not isinstance(point, dict):
            errors.append(f"{prefix}: point must be an object")
            continue
        stages = point.get("process_stages")
        active_stages = (stages if "enabled_source_spaces" in point else
                         point.get("enabled_process_stages", stages))
        modes = point.get("allowed_treatment_modes", ["simple", "full"])
        try:
            minimum_input_t = float(point.get("min_input_grade_t", 0.0))
        except (TypeError, ValueError):
            minimum_input_t = -1.0
        if not math.isfinite(minimum_input_t) or minimum_input_t < 0:
            errors.append(
                f"{prefix}: min_input_grade_t must be a non-negative number")
        share_mode = point_mode(point)
        if share_mode not in BLEND_MODES:
            errors.append(
                f"{prefix}: share_mode must be MATRIX or OPTIMIZED")
        if not isinstance(stages, list) or not stages:
            errors.append(f"{prefix}: process_stages must be a non-empty list")
            stages = []
        if not isinstance(active_stages, list) or any(
                stage not in stages for stage in active_stages):
            errors.append(
                f"{prefix}: enabled_process_stages must be a subset of "
                "process_stages")
            active_stages = []
        if "enabled_source_spaces" in point:
            active_sources = point["enabled_source_spaces"]
            if not isinstance(active_sources, list) or any(
                    not isinstance(source, str) or not source.strip()
                    for source in active_sources):
                errors.append(
                    f"{prefix}: enabled_source_spaces must be a list of "
                    "stockpile IDs")
            elif len(set(active_sources)) != len(active_sources):
                errors.append(
                    f"{prefix}: enabled_source_spaces contains duplicate IDs")
            elif known_spaces is not None:
                for source in active_sources:
                    if source not in known_spaces:
                        errors.append(
                            f"{prefix}: unknown enabled source space {source}")
        if not isinstance(modes, list) or not modes or any(
                str(mode) not in {"simple", "full"} for mode in modes):
            errors.append(
                f"{prefix}: allowed_treatment_modes must contain simple "
                "and/or full")
        for stage in stages:
            stage = str(stage)
            if stage in claimed_stages:
                errors.append(f"{prefix}: process stage {stage} is duplicated")
            claimed_stages.add(stage)
            if known_stages is not None and stage not in known_stages:
                errors.append(f"{prefix}: unknown process stage {stage}")
        sources: set[str] = set()

        def validate_components(components, label_prefix, *, fixed):
            if not isinstance(components, list) or len(components) < 2:
                errors.append(
                    f"{label_prefix}: at least two components are required")
                return
            local_sources: set[str] = set()
            minimum_sum = maximum_sum = fixed_sum = 0.0
            positive_fixed = positive_minimum = 0
            for index, row in enumerate(components):
                label = f"{label_prefix}/component {index + 1}"
                if not isinstance(row, dict):
                    errors.append(f"{label}: component must be an object")
                    continue
                source = str(row.get("source_space", ""))
                if not source:
                    errors.append(f"{label}: source_space is required")
                elif source in local_sources:
                    errors.append(
                        f"{label}: duplicate source space {source}")
                local_sources.add(source)
                sources.add(source)
                if known_spaces is not None and source not in known_spaces:
                    errors.append(f"{label}: unknown source space {source}")
                if fixed:
                    try:
                        target = float(row.get("target_fraction"))
                    except (TypeError, ValueError):
                        errors.append(
                            f"{label}: fixed matrix share must be a number")
                        continue
                    if not math.isfinite(target) or not 0 <= target <= 1:
                        errors.append(
                            f"{label}: fixed matrix share must be in [0, 1]")
                    else:
                        fixed_sum += target
                        positive_fixed += int(target > 0)
                else:
                    try:
                        lower = float(row.get("min_fraction"))
                        upper = float(row.get("max_fraction"))
                    except (TypeError, ValueError):
                        errors.append(f"{label}: share limits must be numbers")
                        continue
                    if not math.isfinite(lower) or not math.isfinite(upper) or \
                            lower < 0 or upper > 1 or lower > upper:
                        errors.append(
                            f"{label}: require 0 <= min_fraction <= "
                            "max_fraction <= 1")
                    minimum_sum += lower
                    maximum_sum += upper
                    positive_minimum += int(lower > 0)
            if fixed:
                if abs(fixed_sum - 1.0) > 1e-9:
                    errors.append(
                        f"{label_prefix}: fixed matrix shares must sum to 100%")
                if positive_fixed < 2:
                    errors.append(
                        f"{label_prefix}: fixed matrix needs at least two "
                        "positive components")
            else:
                if minimum_sum > 1 + 1e-9:
                    errors.append(
                        f"{prefix}: minimum shares sum above 100%")
                if maximum_sum < 1 - 1e-9:
                    errors.append(
                        f"{prefix}: maximum shares sum below 100%")
                if positive_minimum < 2:
                    errors.append(
                        f"{prefix}: at least two components need a positive "
                        "minimum share")

        if share_mode == BLEND_MODE_MATRIX:
            catalog = matrix_catalog(point)
            if not catalog:
                errors.append(
                    f"{prefix}: matrix mode requires at least one catalogue "
                    "matrix")
            for entry in catalog:
                validate_components(
                    entry["components"],
                    f"{prefix}/{entry['id']}", fixed=True)
        else:
            validate_components(
                deepcopy(point.get("optimized_components", point.get(
                    "components", []))), prefix, fixed=False)

        if has_scenario and cfg.get("enabled") is True:
            if "enabled_source_spaces" in point:
                sources = {str(row["source_space"])
                           for row in candidate_components(point)}
                if not _has_effective_blend(point):
                    active_stages = []
            transfer_pairs = {
                (transfer.process_stage, transfer.source_space)
                for transfer in sc_or_settings.process_transfers
            }
            for stage in active_stages:
                for source in sources:
                    if (stage, source) not in transfer_pairs:
                        errors.append(
                            f"{prefix}: no physical transfer from {source} "
                            f"to {stage}")
    return errors
