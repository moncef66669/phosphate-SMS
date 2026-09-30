"""Durable, scenario-specific operator switches for stockpile blending.

This overlay deliberately stores enablement only.  It is not a scenario export:
recipes, optimization policy, grades, inventory and physical campaign state
remain owned by the scenario.  Callers decide which built-in scenarios receive
the overlay; importing a scenario does not implicitly load this file.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from .domain import Scenario


PREFERENCES_VERSION = 1
DEFAULT_BLEND_PREFERENCES_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "blending_preferences.json"
)
_MPH = "CHEVRON_STACKING"
_PREWASH = "PRE_WASHING_BLENDING"


class BlendPreferencesError(ValueError):
    """Saved enablement could not be read, validated or safely written."""


def _object(value, label: str, allowed: set[str]) -> None:
    if not isinstance(value, dict) or set(value) - allowed:
        raise BlendPreferencesError(f"{label} must be an object with only {sorted(allowed)}")


def _enabled(value: dict, label: str) -> None:
    if "enabled" in value and type(value["enabled"]) is not bool:
        raise BlendPreferencesError(f"{label}.enabled must be a boolean")


def _ids(value, label: str) -> list[str]:
    if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value):
        raise BlendPreferencesError(f"{label} must be a list of non-blank IDs")
    return list(dict.fromkeys(value))


def _validate_record(record) -> None:
    _object(record, "scenario preferences", {_MPH, _PREWASH})
    if _MPH in record:
        mph = record[_MPH]
        _object(mph, _MPH, {"enabled", "enabled_spaces"})
        _enabled(mph, _MPH)
        if "enabled_spaces" in mph:
            _ids(mph["enabled_spaces"], f"{_MPH}.enabled_spaces")
    if _PREWASH in record:
        prewash = record[_PREWASH]
        _object(prewash, _PREWASH, {"enabled", "points"})
        _enabled(prewash, _PREWASH)
        points = prewash.get("points", {})
        if not isinstance(points, dict):
            raise BlendPreferencesError(f"{_PREWASH}.points must be an object")
        for point_id, point in points.items():
            if not isinstance(point_id, str) or not point_id.strip():
                raise BlendPreferencesError("pre-washing point IDs must be non-blank")
            _object(point, point_id, {"enabled_source_spaces"})
            if "enabled_source_spaces" in point:
                _ids(point["enabled_source_spaces"], f"{point_id}.enabled_source_spaces")


def _read(target: Path) -> dict:
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"version": PREFERENCES_VERSION, "scenarios": {}}
    except (OSError, UnicodeError) as exc:
        raise BlendPreferencesError(f"cannot read {target}: {exc}") from exc
    try:
        payload = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise BlendPreferencesError(f"invalid blending preferences in {target}: {exc}") from exc
    _object(payload, "blending preferences", {"version", "scenarios"})
    if (type(payload.get("version")) is not int
            or payload["version"] != PREFERENCES_VERSION):
        raise BlendPreferencesError(f"unsupported blending preferences version in {target}")
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, dict):
        raise BlendPreferencesError("blending preferences must contain a scenarios object")
    for name, record in scenarios.items():
        if not isinstance(name, str) or not name.strip():
            raise BlendPreferencesError("scenario preference names must be non-blank")
        _validate_record(record)
    return payload


def _known_mph_spaces(sc: Scenario) -> set[str]:
    return {space.id for space in sc.spaces
            if space.site == "MPH" and space.area == "M16-humid"}


def _known_source_spaces(sc: Scenario, point: dict) -> set[str]:
    rows = []
    for key in ("components", "optimized_components"):
        if isinstance(point.get(key), list):
            rows.extend(point[key])
    for matrix in point.get("matrix_catalog", []) or []:
        if isinstance(matrix, dict) and isinstance(matrix.get("components"), list):
            rows.extend(matrix["components"])
    candidates = {row.get("source_space") for row in rows if isinstance(row, dict)
                  and isinstance(row.get("source_space"), str)}
    stages = point.get("process_stages", [])
    candidates.update(transfer.source_space for transfer in sc.process_transfers
                      if transfer.process_stage in stages)
    site = point.get("site")
    return {space.id for space in sc.spaces if space.id in candidates
            and (not site or space.site == site)}


def _snapshot(sc: Scenario) -> dict:
    if not isinstance(sc.name, str) or not sc.name.strip():
        raise BlendPreferencesError("scenario name must be non-blank")
    record = {}
    for key in (_MPH, _PREWASH):
        if key not in sc.config:
            continue
        config = sc.config[key]
        if not isinstance(config, dict):
            raise BlendPreferencesError(f"{key} must be an object")
        selected = {"enabled": config["enabled"]} if "enabled" in config else {}
        if key == _MPH and "enabled_spaces" in config:
            selected["enabled_spaces"] = [item for item in _ids(
                config["enabled_spaces"], f"{key}.enabled_spaces")
                if item in _known_mph_spaces(sc)]
        if key == _PREWASH and "points" in config:
            points = config["points"]
            if not isinstance(points, dict):
                raise BlendPreferencesError(f"{key}.points must be an object")
            selected["points"] = {}
            for point_id, point in points.items():
                if not isinstance(point, dict):
                    raise BlendPreferencesError(f"{point_id} must be an object")
                if "enabled_source_spaces" in point:
                    known = _known_source_spaces(sc, point)
                    selected["points"][point_id] = {"enabled_source_spaces": [
                        item for item in _ids(point["enabled_source_spaces"],
                                              f"{point_id}.enabled_source_spaces")
                        if item in known]}
        record[key] = selected
    _validate_record(record)
    return record


def save_blend_enablement(sc: Scenario, path: str | Path | None = None) -> Path:
    """Merge enablement for this scenario and atomically replace the file.

    Invalid existing files are reported, never silently reset.  No scenario
    field is mutated, including when a write fails.
    """
    target = Path(path) if path is not None else DEFAULT_BLEND_PREFERENCES_PATH
    snapshot = _snapshot(sc)
    payload = _read(target)
    record = payload["scenarios"].setdefault(sc.name, {})
    for key, switches in snapshot.items():
        existing = record.setdefault(key, {})
        for field, value in switches.items():
            if field == "points":
                existing.setdefault("points", {}).update(value)
            else:
                existing[field] = value
    temporary = None
    try:
        serialized = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        target.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=target.parent,
                prefix=f".{target.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except (OSError, TypeError, ValueError) as exc:
        raise BlendPreferencesError(f"cannot save {target}: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # A cleanup error must not hide the original save failure.
    return target


def apply_saved_blend_enablement(sc: Scenario, path: str | Path | None = None) -> bool:
    """Apply known-stockpile switches only, returning whether config changed.

    Omitted sections/points remain unchanged.  Stale IDs and unknown points
    cannot create stockpiles or policy locations.  All validation precedes the
    single scenario assignment, so malformed preferences cannot partially apply.
    """
    target = Path(path) if path is not None else DEFAULT_BLEND_PREFERENCES_PATH
    record = _read(target)["scenarios"].get(sc.name, {})
    updated = deepcopy(sc.config)
    for key, switches in record.items():
        config = updated.get(key)
        if not isinstance(config, dict):
            continue
        if "enabled" in switches:
            config["enabled"] = switches["enabled"]
        if key == _MPH and "enabled_spaces" in switches:
            known = _known_mph_spaces(sc)
            config["enabled_spaces"] = [item for item in _ids(
                switches["enabled_spaces"], f"{key}.enabled_spaces") if item in known]
        if key == _PREWASH:
            points = config.get("points", {})
            if not isinstance(points, dict):
                continue
            for point_id, saved in switches.get("points", {}).items():
                point = points.get(point_id)
                if not isinstance(point, dict) or "enabled_source_spaces" not in saved:
                    continue
                known = _known_source_spaces(sc, point)
                point["enabled_source_spaces"] = [item for item in _ids(
                    saved["enabled_source_spaces"], f"{point_id}.enabled_source_spaces")
                    if item in known]
    if updated == sc.config:
        return False
    sc.config = updated
    return True
