"""Persistent operator-managed grade catalogue.

The built-in engineering datasets remain deterministic source fixtures.  This
small overlay stores the catalogue edited from the app and reapplies it when a
built-in phosphate scenario is loaded, so operator changes survive browser
sessions and Streamlit restarts without rewriting generated dataset code.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, fields
import json
import math
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterable, Optional

from .domain import Evidence, Grade, Scenario


CATALOG_VERSION = 1
DEFAULT_GRADE_CATALOG_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "grade_catalog.json"
)


class GradeCatalogError(ValueError):
    """Raised when the persistent catalogue cannot be read or validated."""


def _validate_grade_values(grade: Grade) -> None:
    """Reject invalid stored numbers before replacing a usable catalogue."""
    if not isinstance(grade.quality, dict):
        raise GradeCatalogError(f"{grade.id}: quality must be an object")
    values = [(f"quality.{key}", value, 0.0, None)
              for key, value in grade.quality.items()]
    values.append(("rock_value", grade.rock_value, 0.0, None))
    for field, lower, upper in (
            ("beneficiation_yield", 0.0, 1.0),
            ("raw_moisture_pct", 0.0, 100.0),
            ("product_moisture_pct", 0.0, 100.0)):
        value = getattr(grade, field)
        if value is not None:
            values.append((field, value, lower, upper))
    for field, value, lower, upper in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise GradeCatalogError(f"{grade.id}: {field} must be numeric")
        if not math.isfinite(value) or value < lower or (
                upper is not None and value > upper):
            raise GradeCatalogError(f"{grade.id}: {field} is outside its finite valid range")
        if field == "beneficiation_yield" and value == 0:
            raise GradeCatalogError(f"{grade.id}: beneficiation_yield must be positive")
        if field in {"raw_moisture_pct", "product_moisture_pct"} and value == 100:
            raise GradeCatalogError(f"{grade.id}: {field} must be below 100")


def _grade_from_dict(value: dict) -> Grade:
    if not isinstance(value, dict):
        raise GradeCatalogError("each grade catalogue entry must be an object")
    allowed = {field.name for field in fields(Grade)}
    payload = {key: item for key, item in value.items() if key in allowed}
    evidence = payload.get("evidence")
    if isinstance(evidence, dict):
        evidence_fields = {field.name for field in fields(Evidence)}
        payload["evidence"] = Evidence(**{
            key: item for key, item in evidence.items()
            if key in evidence_fields
        })
    try:
        grade = Grade(**payload)
    except TypeError as exc:
        raise GradeCatalogError(f"invalid grade catalogue entry: {exc}") from exc
    grade.id = str(grade.id).strip()
    grade.name = str(grade.name).strip()
    if not grade.id:
        raise GradeCatalogError("grade IDs cannot be blank")
    if not grade.name:
        grade.name = grade.id
    _validate_grade_values(grade)
    return grade


def read_grade_catalog(
        path: Optional[str | Path] = None) -> Optional[list[Grade]]:
    """Read a saved catalogue; return ``None`` when none has been saved."""
    target = Path(path) if path is not None else DEFAULT_GRADE_CATALOG_PATH
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GradeCatalogError(f"cannot read {target}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("version") != CATALOG_VERSION:
        raise GradeCatalogError(
            f"unsupported grade catalogue format in {target}")
    raw_grades = payload.get("grades")
    if not isinstance(raw_grades, list):
        raise GradeCatalogError("grade catalogue must contain a grades list")
    if not raw_grades:
        raise GradeCatalogError("grade catalogue cannot be empty")
    grades = [_grade_from_dict(value) for value in raw_grades]
    ids = [grade.id for grade in grades]
    if len(ids) != len(set(ids)):
        raise GradeCatalogError("grade catalogue contains duplicate IDs")
    return grades


def apply_saved_grade_catalog(
        scenario: Scenario, path: Optional[str | Path] = None) -> bool:
    """Apply the saved catalogue and reconcile grade-list memberships.

    The saved catalogue is authoritative for operator-managed deletions. Built-in
    material profiles are recreated whenever a scenario is loaded, so any grade
    removed from the catalogue must also be removed from their allowed-grade
    lists. Operational references (stocks, demand, jobs and lineage) are guarded
    before deletion by the editor and remain validation errors if inconsistent.
    """
    saved_grades = read_grade_catalog(path)
    if saved_grades is None:
        return False

    # The persistent file is an operator overlay for physical material grades,
    # not an authority over demand-side receiving contracts. Older catalogue
    # files predate G10 and the train-export specifications; replacing the
    # complete built-in list with such a file silently removed those contracts
    # from the scenario and from the configuration UI. Preserve the current
    # code-defined specifications, while still honouring operator additions,
    # edits and deletions for every physical grade.
    builtin_specs = {
        grade.id: deepcopy(grade)
        for grade in scenario.grades
        if getattr(grade, "is_specification", False)
    }
    saved_specs = {
        grade.id: deepcopy(grade)
        for grade in saved_grades
        if getattr(grade, "is_specification", False)
    }
    # A code-defined contract wins when an old persisted copy has the same ID;
    # persisted specifications unknown to this build are retained for backward
    # compatibility with catalogues created by other project variants.
    merged_specs = {**saved_specs, **builtin_specs}
    # The same reasoning applies to a built-in *stock* family the catalogue
    # file predates: dropping it leaves the seeded hangars pointing at a grade
    # that no longer exists (UNKNOWN_GRADE) and blocks planning outright. Only
    # grades the scenario actually references are restored, and the editor
    # already blocks deleting a referenced grade, so this cannot silently undo
    # a legitimate operator deletion.
    saved_physical_ids = {
        grade.id for grade in saved_grades
        if not getattr(grade, "is_specification", False)
    }
    referenced_ids = {
        str(getattr(space, attribute, "") or "")
        for space in scenario.spaces
        for attribute in ("grade", "designated_grade")
    } | {str(segment.grade or "") for segment in scenario.segments}
    restored = [
        deepcopy(grade) for grade in scenario.grades
        if not getattr(grade, "is_specification", False)
        and grade.id in referenced_ids
        and grade.id not in saved_physical_ids
    ]
    # A catalogue saved before an assay dimension existed omits it entirely, and
    # ``quality`` is a plain dict: taking the saved vector wholesale DELETES that
    # dimension. The solver then reads the pile as unmeasured on it and excludes
    # the material from every quality-constrained delivery -- measured
    # 30-Sep-2026, where a version-1 file without CO2 made all six MPH humid
    # hangars unusable and drove PAP and DRY-FEED service to 0%.
    #
    # An omitted key is a file that predates the dimension, not an operator
    # erasing a measurement, so fill it from the built-in vector. An assay the
    # operator actually edited is present in the file and still wins.
    builtin_quality = {
        grade.id: dict(getattr(grade, "quality", None) or {})
        for grade in scenario.grades
        if not getattr(grade, "is_specification", False)
    }
    for grade in saved_grades:
        if getattr(grade, "is_specification", False):
            continue
        for key, value in builtin_quality.get(grade.id, {}).items():
            grade.quality.setdefault(key, value)
    scenario.grades = [
        grade for grade in saved_grades
        if not getattr(grade, "is_specification", False)
    ] + restored + list(merged_specs.values())
    from .washing import synchronize_pap
    synchronize_pap(scenario)
    saved_ids = {grade.id for grade in scenario.grades}
    for profile in scenario.material_profiles:
        profile.grade_ids = [
            grade_id for grade_id in profile.grade_ids
            if grade_id in saved_ids
        ]
    # Arrival defaults may have been installed against the built-in catalogue.
    # Remove deleted zero-only columns, but preserve nonzero references so a
    # real supply mismatch cannot disappear during catalogue loading.
    from .mine_arrivals import reconcile_grade_catalog
    reconcile_grade_catalog(scenario)
    return True


def save_grade_catalog(
        grades_or_scenario: Scenario | Iterable[Grade],
        path: Optional[str | Path] = None) -> Path:
    """Atomically persist the complete catalogue and return its path."""
    grades = (
        grades_or_scenario.grades
        if isinstance(grades_or_scenario, Scenario)
        else list(grades_or_scenario)
    )
    ids = [str(grade.id).strip() for grade in grades]
    if not ids or any(not grade_id for grade_id in ids):
        raise GradeCatalogError("grade catalogue must contain non-blank IDs")
    if len(ids) != len(set(ids)):
        raise GradeCatalogError("grade catalogue contains duplicate IDs")
    for grade in grades:
        _validate_grade_values(grade)
    target = Path(path) if path is not None else DEFAULT_GRADE_CATALOG_PATH
    payload = {
        "version": CATALOG_VERSION,
        "grades": [asdict(grade) for grade in grades],
    }
    temporary = None
    try:
        serialized = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        target.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=target.parent,
                prefix=f".{target.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(serialized)
        temporary.replace(target)
    except (OSError, TypeError, ValueError) as exc:
        raise GradeCatalogError(f"cannot save {target}: {exc}") from exc
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)
    return target
