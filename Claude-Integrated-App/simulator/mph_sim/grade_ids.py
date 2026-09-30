"""Canonical chemical-grade identifiers and legacy import compatibility.

Grade IDs identify chemistry, not a mine or material-handling stage. Physical
site/stage differences belong on ``MaterialProfile`` and inventory records.
"""
from __future__ import annotations

import dataclasses
from typing import Any


RAW_PENDING_GRADE_ID = "RAW-TBC"
RAW_BT_GRADE_IDS = (
    "RAW-BT",
    "RAW-BT-HIGH-CD",
    "RAW-BT-LOW-CD",
)
# Additional documented B02 raw-feed families.  Keep these separate from
# ``RAW_BT_GRADE_IDS``: the latter is the three-profile Cd lineage used by the
# current wash-route mapping, while these rows extend the shared chemistry
# catalogue without changing an active campaign.
B02_RAW_GRADE_IDS = (
    "RAW-MT",
    "RAW-TBT",
)
RAW_GRADE_IDS = (
    "RAW-MT",
    *RAW_BT_GRADE_IDS,
    "RAW-TBT",
)
MERCHANT_GRADE_FAMILIES = ("TBT", "BT", "MT")

# Washed-product chemistry retains the Cd lineage of the raw feed.  The
# Historic TBT/BT/MT identifiers remain the base-Cd variants for backwards
# compatibility; the explicit suffixes add the two other raw-feed profiles.
WASHED_GRADE_BY_RAW_AND_FAMILY = {
    "RAW-BT": {
        "TBT": "TBT",
        "BT": "BT",
        "MT": "MT",
    },
    "RAW-BT-HIGH-CD": {
        "TBT": "TBT-HIGH-CD",
        "BT": "BT-HIGH-CD",
        "MT": "MT-HIGH-CD",
    },
    "RAW-BT-LOW-CD": {
        "TBT": "TBT-LOW-CD",
        "BT": "BT-LOW-CD",
        "MT": "MT-LOW-CD",
    },
}
WASHED_GRADE_IDS = tuple(
    WASHED_GRADE_BY_RAW_AND_FAMILY[raw_grade][family]
    for family in MERCHANT_GRADE_FAMILIES
    for raw_grade in RAW_BT_GRADE_IDS
)
WASHED_SOURCE_GRADE = {
    washed_grade: raw_grade
    for raw_grade, by_family in WASHED_GRADE_BY_RAW_AND_FAMILY.items()
    for washed_grade in by_family.values()
}
CANONICAL_RAW_GRADE_NAMES = {
    RAW_PENDING_GRADE_ID: "Raw/screened phosphate — chemical grade TBC",
    "RAW-MT": "Raw MT — nominal profile",
    "RAW-BT": "BT",
    "RAW-BT-HIGH-CD": "Raw BT — High-Cd profile",
    "RAW-BT-LOW-CD": "Raw BT — Low-Cd profile",
    "RAW-TBT": "Raw TBT — nominal profile",
}

# Case-insensitive at the boundary so copied operator-facing values such as
# ``MINE-RAW-pending`` remain importable. Exports always emit canonical IDs.
GRADE_ID_ALIASES = {
    "MINE-RAW-TBC": RAW_PENDING_GRADE_ID,
    "MINE-RAW-PENDING": RAW_PENDING_GRADE_ID,
    "RAW-PENDING": RAW_PENDING_GRADE_ID,
    # Removed in September 2026: this duplicated the operational RAW-BT
    # identity without an independent process lineage.  Retained only as an
    # import alias so old scenarios and saved mine-arrival profiles preserve
    # their mass under the canonical RAW-BT grade.
    "RAW-BT-NOMINAL": "RAW-BT",
    # Backward compatibility for scenarios and SMS snapshots created before
    # the operator-facing BT terminology was adopted.
    "RAW-BT-GLOBAL": "RAW-BT",
    "LOU-RAW-BT-GLOBAL": "RAW-BT",
    "RAW-BT-AVERAGE": "RAW-BT",
    "LOU-RAW-BT-AVERAGE": "RAW-BT",
    "LOU-RAW-BT": "RAW-BT",
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
        or key_upper.endswith("_GRADE_SOURCE_MAPPING")
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
    native_grade_ids = {
        grade.id for grade in scenario.grades
        if grade.id != "RAW-BT-NOMINAL"
    }
    exact_payloads: dict[str, dict] = {}
    for grade in scenario.grades:
        old_id = grade.id
        grade.id = canonical_grade_id(grade.id)
        aliased = grade.id != old_id
        changed |= aliased
        # Prefer the real RAW-BT definition when an old export contains both
        # it and the deleted duplicate nominal row.  A legacy scenario that
        # contains only the old row is still made importable by the alias.
        if old_id == "RAW-BT-NOMINAL" and grade.id in native_grade_ids:
            changed = True
            continue
        if aliased and grade.id in CANONICAL_RAW_GRADE_NAMES:
            canonical_name = CANONICAL_RAW_GRADE_NAMES[grade.id]
            if grade.name != canonical_name:
                grade.name = canonical_name
                changed = True
            if grade.id in RAW_GRADE_IDS and grade.profile_id:
                grade.profile_id = ""
                changed = True
        old_source = getattr(grade, "source_grade_id", "")
        canonical_source = canonical_grade_id(old_source)
        if canonical_source != old_source:
            grade.source_grade_id = canonical_source
            changed = True
        # ``source_grade_id`` was introduced after existing scenario exports.
        # Backfill only a missing value for a known washed grade; preserve a
        # non-empty mismatch so validation can report it instead of silently
        # rewriting an operator/configuration error.
        expected_source = WASHED_SOURCE_GRADE.get(grade.id, "")
        if expected_source and not getattr(grade, "source_grade_id", ""):
            grade.source_grade_id = expected_source
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
        (getattr(scenario, "demand", []), ("requested_grade",)),
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
