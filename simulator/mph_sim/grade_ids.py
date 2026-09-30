"""Canonical chemical-grade identifiers and legacy import compatibility.

Grade IDs identify chemistry, not a mine or material-handling stage. Physical
site/stage differences belong on ``MaterialProfile`` and inventory records.
"""
from __future__ import annotations

import dataclasses
from typing import Any


RAW_PENDING_GRADE_ID = "RAW-TBC"
RAW_BT_GRADE_IDS = (
    "RAW-BT-GLOBAL",
    "RAW-BT-HIGH-CD",
    "RAW-BT-LOW-CD",
)
CANONICAL_RAW_GRADE_NAMES = {
    RAW_PENDING_GRADE_ID: "Raw/screened phosphate — chemical grade TBC",
    "RAW-BT-GLOBAL": "Raw BT — Global profile",
    "RAW-BT-HIGH-CD": "Raw BT — High-Cd profile",
    "RAW-BT-LOW-CD": "Raw BT — Low-Cd profile",
}

# Case-insensitive at the boundary so copied operator-facing values such as
# ``MINE-RAW-pending`` remain importable. Exports always emit canonical IDs.
GRADE_ID_ALIASES = {
    "MINE-RAW-TBC": RAW_PENDING_GRADE_ID,
    "MINE-RAW-PENDING": RAW_PENDING_GRADE_ID,
    "RAW-PENDING": RAW_PENDING_GRADE_ID,
    "LOU-RAW-BT-GLOBAL": "RAW-BT-GLOBAL",
    "LOU-RAW-BT-HIGH-CD": "RAW-BT-HIGH-CD",
    "LOU-RAW-BT-LOW-CD": "RAW-BT-LOW-CD",
}
_ALIASES_CASEFOLD = {
    alias.casefold(): canonical
    for alias, canonical in GRADE_ID_ALIASES.items()
}


def canonical_grade_id(value: Any) -> Any:
    """Return a canonical grade ID while preserving non-string values."""
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return stripped
    return _ALIASES_CASEFOLD.get(stripped.casefold(), stripped)


def _canonicalize_config(value: Any, parent_key: str = "") -> tuple[Any, bool]:
    changed = False
    key_upper = parent_key.upper()
    grade_collection = (
        key_upper.endswith("_GRADE_IDS")
        or key_upper.endswith("_GRADE_CATALOG")
    )
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            key_text = str(key)
            key_lower = key_text.lower()
            if key_lower in {
                "grade", "grade_id", "output_grade", "requested_grade",
                "designated_grade",
            } and isinstance(item, str):
                canonical = canonical_grade_id(item)
                result[key] = canonical
                changed |= canonical != item
            elif grade_collection and isinstance(item, str):
                canonical = canonical_grade_id(item)
                result[key] = canonical
                changed |= canonical != item
            else:
                canonical, nested_changed = _canonicalize_config(
                    item, key_text)
                result[key] = canonical
                changed |= nested_changed
        return result, changed
    if isinstance(value, list):
        result = []
        for item in value:
            if grade_collection and isinstance(item, str):
                canonical = canonical_grade_id(item)
                result.append(canonical)
                changed |= canonical != item
            else:
                canonical, nested_changed = _canonicalize_config(
                    item, parent_key)
                result.append(canonical)
                changed |= nested_changed
        return result, changed
    if grade_collection and isinstance(value, str):
        canonical = canonical_grade_id(value)
        return canonical, canonical != value
    return value, False


def canonicalize_scenario_grade_ids(scenario) -> bool:
    """Canonicalize every grade-bearing field of a Scenario in place.

    Exact duplicate Grade rows created by an old+new mixed export are collapsed
    only when their complete canonical dataclass payloads match. Conflicting
    duplicates remain visible so normal scenario validation can reject them.
    """
    changed = False
    canonical_grades = []
    exact_payloads: dict[str, dict] = {}
    for grade in scenario.grades:
        old_id = grade.id
        grade.id = canonical_grade_id(grade.id)
        aliased = grade.id != old_id
        changed |= aliased
        if aliased and grade.id in CANONICAL_RAW_GRADE_NAMES:
            canonical_name = CANONICAL_RAW_GRADE_NAMES[grade.id]
            if grade.name != canonical_name:
                grade.name = canonical_name
                changed = True
            if grade.id in RAW_BT_GRADE_IDS and grade.profile_id:
                grade.profile_id = ""
                changed = True
        payload = dataclasses.asdict(grade)
        previous = exact_payloads.get(grade.id)
        if previous == payload:
            changed = True
            continue
        canonical_grades.append(grade)
        exact_payloads.setdefault(grade.id, payload)
    scenario.grades = canonical_grades

    for profile in scenario.material_profiles:
        canonical_ids = []
        for grade_id in profile.grade_ids:
            canonical = canonical_grade_id(grade_id)
            changed |= canonical != grade_id
            if canonical not in canonical_ids:
                canonical_ids.append(canonical)
            else:
                changed = True
        profile.grade_ids = canonical_ids

    field_groups = (
        (getattr(scenario, "spaces", []), ("grade", "designated_grade")),
        (getattr(scenario, "segments", []), ("grade",)),
        (getattr(scenario, "pile_zones", []), ("grade",)),
        (getattr(scenario, "destinations", []), ("requested_grade",)),
        (getattr(scenario, "exogenous", []), ("grade",)),
        (getattr(scenario, "process_transfers", []), ("output_grade",)),
        (getattr(scenario, "belt_profile", []), ("grade",)),
        (getattr(scenario, "fixed_jobs", []), ("grade",)),
    )
    for items, fields in field_groups:
        for item in items:
            for field_name in fields:
                old_id = getattr(item, field_name, "")
                canonical = canonical_grade_id(old_id)
                if canonical != old_id:
                    setattr(item, field_name, canonical)
                    changed = True

    config, config_changed = _canonicalize_config(
        getattr(scenario, "config", {}))
    if config_changed:
        scenario.config = config
        changed = True
    return changed
