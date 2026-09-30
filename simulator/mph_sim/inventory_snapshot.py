"""Validated starting-inventory snapshots for planning runs.

This module is deliberately independent of Streamlit and of the transport
used to obtain the values.  A CSV/XLSX/JSON export and a future RabbitMQ
``SpaceValue`` response can therefore pass through the same atomic validation
and application path.

The planning model consumes :class:`~mph_sim.domain.StockSpace.mass_t` and the
aggregate pile quality.  Segment rows are retained for FIFO/display purposes
and are also aggregated back to the parent pile so the optimiser never sees a
stale total.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

import pandas as pd

from .domain import Scenario, Segment
from .grade_ids import canonical_grade_id


SCHEMA_VERSION = "1.0"
MASS_RECONCILIATION_TOLERANCE_T = 1.0
FUTURE_CLOCK_SKEW_MINUTES = 5.0
VALID_STATES = {"empty", "building", "full", "reclaiming", "mixed", "reserved"}


class SnapshotParseError(ValueError):
    """The uploaded file cannot be interpreted as an inventory snapshot."""


@dataclass
class SegmentSnapshot:
    x_from: float
    x_to: float
    mass_t: float
    grade: str = ""
    quality: Dict[str, float] = field(default_factory=dict)
    age_days: float = 0.0
    row_number: int = 0


@dataclass
class PileSnapshot:
    pile_id: str
    site: str = ""
    mass_t: Optional[float] = None
    grade: str = ""
    quality: Dict[str, float] = field(default_factory=dict)
    state: str = ""
    segments: List[SegmentSnapshot] = field(default_factory=list)
    aggregate_rows: int = 0
    row_numbers: List[int] = field(default_factory=list)
    site_conflict: bool = False


@dataclass
class InventorySnapshot:
    captured_at: datetime
    source: str
    piles: List[PileSnapshot]
    schema_version: str = SCHEMA_VERSION
    source_file: str = ""
    payload_sha256: str = ""
    acquisition_mode: str = "file"
    site_captured_at: Dict[str, str] = field(default_factory=dict)
    connector_config_revision: str = ""

    @property
    def pile_ids(self) -> List[str]:
        return [p.pile_id for p in self.piles]


@dataclass(frozen=True)
class SnapshotIssue:
    severity: str                 # ERROR | WARNING | INFO
    code: str
    message: str
    entity: str = ""


@dataclass
class SnapshotValidationResult:
    snapshot: InventorySnapshot
    issues: List[SnapshotIssue]
    resolved_ids: Dict[str, str] = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "ERROR" for i in self.issues)


@dataclass
class SnapshotApplyResult:
    applied: bool
    scenario: Scenario
    issues: List[SnapshotIssue]
    changed_space_ids: List[str] = field(default_factory=list)
    mass_delta_t: float = 0.0

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "ERROR" for i in self.issues)


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _text(value: Any) -> str:
    return "" if _is_blank(value) else str(value).strip()


def _float(value: Any, label: str, *, optional: bool = False) -> Optional[float]:
    if _is_blank(value):
        if optional:
            return None
        raise SnapshotParseError(f"{label} is required")
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise SnapshotParseError(f"{label} must be numeric (received {value!r})") from exc
    if not math.isfinite(out):
        raise SnapshotParseError(f"{label} must be finite")
    return out


def _quality(value: Any, row: Mapping[str, Any], label: str) -> Dict[str, float]:
    raw: Dict[str, Any] = {}
    if not _is_blank(value):
        if isinstance(value, Mapping):
            raw.update(value)
        elif isinstance(value, str):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError as exc:
                raise SnapshotParseError(
                    f"{label} quality must be a JSON object") from exc
            if not isinstance(decoded, Mapping):
                raise SnapshotParseError(f"{label} quality must be a JSON object")
            raw.update(decoded)
        else:
            raise SnapshotParseError(f"{label} quality must be a JSON object")

    # Flat exports can use q_BPL, q_Cd, ... instead of a JSON cell.
    for key, item in row.items():
        key_s = str(key)
        if key_s.startswith("q_") and not _is_blank(item):
            raw[key_s[2:]] = item

    out: Dict[str, float] = {}
    for key, item in raw.items():
        name = str(key).strip()
        if not name:
            raise SnapshotParseError(f"{label} contains an empty quality name")
        out[name] = _float(item, f"{label} quality.{name}")  # type: ignore[assignment]
    return out


def _aware_datetime(value: Any, label: str = "captured_at") -> datetime:
    text = _text(value)
    if not text:
        raise SnapshotParseError(f"{label} is required")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SnapshotParseError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SnapshotParseError(
            f"{label} must include a timezone offset (for example +01:00 or Z)")
    return parsed.astimezone(timezone.utc)


def _first(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in row and not _is_blank(row[name]):
            return row[name]
    return None


def _normalise_flat_rows(rows: Iterable[Mapping[str, Any]], *,
                         metadata: Optional[Mapping[str, Any]] = None,
                         filename: str = "") -> InventorySnapshot:
    metadata = dict(metadata or {})
    materialised = [dict(r) for r in rows]
    if not materialised:
        raise SnapshotParseError("snapshot contains no rows")

    captured_values: List[datetime] = []
    source_values: List[str] = []
    default_captured = _first(metadata, "captured_at", "snapshot_at")
    default_source = _text(_first(metadata, "source", "source_system"))
    default_site = _text(_first(metadata, "site", "site_id"))
    schema_version = _text(metadata.get("schema_version")) or SCHEMA_VERSION

    grouped: Dict[str, PileSnapshot] = {}
    order: List[str] = []
    for idx, row in enumerate(materialised, start=2):
        captured_raw = _first(row, "captured_at", "snapshot_at") or default_captured
        captured_values.append(_aware_datetime(captured_raw, f"row {idx} captured_at"))
        row_source = _text(_first(row, "source", "source_system")) or default_source
        if row_source:
            source_values.append(row_source)
        pile_id = _text(_first(row, "pile_id", "pile", "space_id"))
        if not pile_id:
            raise SnapshotParseError(f"row {idx} pile_id is required")
        site = _text(_first(row, "site", "site_id")) or default_site
        record_type = (_text(row.get("record_type")) or "pile").lower()
        if record_type in ("aggregate", "space"):
            record_type = "pile"
        if record_type not in ("pile", "segment"):
            raise SnapshotParseError(
                f"row {idx} record_type must be 'pile' or 'segment'")

        if pile_id not in grouped:
            grouped[pile_id] = PileSnapshot(pile_id=pile_id, site=site)
            order.append(pile_id)
        pile = grouped[pile_id]
        if site and pile.site and site != pile.site:
            pile.site_conflict = True
        elif site:
            pile.site = site
        pile.row_numbers.append(idx)

        if record_type == "segment":
            pile.segments.append(SegmentSnapshot(
                x_from=_float(_first(row, "x_from", "x_from_m"),
                              f"row {idx} x_from"),  # type: ignore[arg-type]
                x_to=_float(_first(row, "x_to", "x_to_m"),
                            f"row {idx} x_to"),  # type: ignore[arg-type]
                mass_t=_float(row.get("mass_t"), f"row {idx} mass_t"),  # type: ignore[arg-type]
                grade=canonical_grade_id(
                    _text(_first(row, "grade", "prevalent_grade"))),
                quality=_quality(row.get("quality"), row, f"row {idx}"),
                age_days=_float(row.get("age_days"),
                                f"row {idx} age_days", optional=True) or 0.0,
                row_number=idx,
            ))
        else:
            pile.aggregate_rows += 1
            pile.mass_t = _float(row.get("mass_t"), f"row {idx} mass_t",
                                 optional=True)
            pile.grade = canonical_grade_id(
                _text(_first(row, "grade", "prevalent_grade")))
            pile.quality = _quality(row.get("quality"), row, f"row {idx}")
            pile.state = _text(row.get("state")).lower()

    first_captured = captured_values[0]
    if any(v != first_captured for v in captured_values[1:]):
        raise SnapshotParseError(
            "all rows must use one coherent captured_at timestamp")
    unique_sources = sorted(set(source_values))
    if len(unique_sources) > 1:
        raise SnapshotParseError("all rows must use one coherent source")
    source = unique_sources[0] if unique_sources else (default_source or filename)
    return InventorySnapshot(
        captured_at=first_captured,
        source=source,
        piles=[grouped[k] for k in order],
        schema_version=schema_version,
        source_file=filename,
    )


def _normalise_nested_json(obj: Mapping[str, Any], filename: str) -> InventorySnapshot:
    piles = obj.get("piles")
    if not isinstance(piles, list) or not piles:
        raise SnapshotParseError("JSON snapshot must contain a non-empty 'piles' list")
    flat: List[Dict[str, Any]] = []
    for pidx, item in enumerate(piles, start=1):
        if not isinstance(item, Mapping):
            raise SnapshotParseError(f"piles[{pidx}] must be an object")
        pile_id = _first(item, "pile_id", "pile", "space_id")
        inherited = {
            "captured_at": _first(item, "captured_at", "snapshot_at")
            or _first(obj, "captured_at", "snapshot_at"),
            "source": _first(item, "source", "source_system")
            or _first(obj, "source", "source_system"),
            "site": _first(item, "site", "site_id")
            or _first(obj, "site", "site_id"),
            "pile_id": pile_id,
        }
        # An object containing only identity + segments is a segment-only pile;
        # otherwise retain its aggregate declaration as a pile row.
        aggregate_keys = {"mass_t", "grade", "prevalent_grade", "quality", "state"}
        if aggregate_keys.intersection(item):
            row = dict(inherited)
            row.update({k: v for k, v in item.items() if k != "segments"})
            row["record_type"] = "pile"
            flat.append(row)
        segments = item.get("segments", [])
        if segments is None:
            segments = []
        if not isinstance(segments, list):
            raise SnapshotParseError(f"piles[{pidx}].segments must be a list")
        for segment in segments:
            if not isinstance(segment, Mapping):
                raise SnapshotParseError(
                    f"piles[{pidx}].segments entries must be objects")
            row = dict(inherited)
            row.update(segment)
            row["record_type"] = "segment"
            flat.append(row)
        if not aggregate_keys.intersection(item) and not segments:
            # Keep an identity-only declaration so validation can explain that
            # it lacks both a total and segments.
            flat.append({**inherited, "record_type": "pile", "mass_t": None})
    metadata = {
        "captured_at": _first(obj, "captured_at", "snapshot_at"),
        "source": _first(obj, "source", "source_system"),
        "site": _first(obj, "site", "site_id"),
        "schema_version": obj.get("schema_version", SCHEMA_VERSION),
    }
    return _normalise_flat_rows(flat, metadata=metadata, filename=filename)


def parse_inventory_snapshot(data: bytes, filename: str) -> InventorySnapshot:
    """Parse a CSV, XLSX or JSON snapshot without changing a scenario.

    JSON supports a nested ``piles`` envelope or a flat ``rows`` list.  Flat
    files use ``record_type=pile|segment``.  ``space_id``/``pile`` and
    ``site_id`` are accepted aliases for external integration exports.
    """
    if not isinstance(data, (bytes, bytearray)):
        raise SnapshotParseError("snapshot payload must be bytes")
    suffix = Path(filename).suffix.lower()
    payload = bytes(data)
    if suffix == ".json":
        try:
            obj = json.loads(payload.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SnapshotParseError(f"invalid JSON snapshot: {exc}") from exc
        if isinstance(obj, list):
            snapshot = _normalise_flat_rows(obj, filename=filename)
        elif isinstance(obj, Mapping) and isinstance(obj.get("piles"), list):
            snapshot = _normalise_nested_json(obj, filename)
        elif isinstance(obj, Mapping) and isinstance(obj.get("rows"), list):
            snapshot = _normalise_flat_rows(
                obj["rows"], metadata=obj, filename=filename)
        else:
            raise SnapshotParseError(
                "JSON must be a list of rows or an object containing 'piles'/'rows'")
    elif suffix == ".csv":
        try:
            df = pd.read_csv(io.BytesIO(payload), encoding="utf-8-sig",
                             dtype=object)
        except Exception as exc:
            raise SnapshotParseError(f"invalid CSV snapshot: {exc}") from exc
        snapshot = _normalise_flat_rows(df.to_dict("records"), filename=filename)
    elif suffix in (".xlsx", ".xlsm"):
        try:
            book = pd.ExcelFile(io.BytesIO(payload))
            sheet_lookup = {s.lower(): s for s in book.sheet_names}
            metadata: Dict[str, Any] = {}
            if "metadata" in sheet_lookup:
                mdf = pd.read_excel(book, sheet_name=sheet_lookup["metadata"],
                                    dtype=object)
                if {"key", "value"}.issubset(mdf.columns):
                    metadata = {str(r["key"]): r["value"]
                                for _, r in mdf.iterrows() if not _is_blank(r["key"])}
                elif not mdf.empty:
                    metadata = {str(k): v for k, v in mdf.iloc[0].items()
                                if not _is_blank(v)}
            rows: List[Dict[str, Any]] = []
            if "piles" in sheet_lookup:
                pdf = pd.read_excel(book, sheet_name=sheet_lookup["piles"],
                                    dtype=object)
                for row in pdf.to_dict("records"):
                    rows.append({**row, "record_type": "pile"})
                if "segments" in sheet_lookup:
                    sdf = pd.read_excel(book, sheet_name=sheet_lookup["segments"],
                                        dtype=object)
                    for row in sdf.to_dict("records"):
                        rows.append({**row, "record_type": "segment"})
            else:
                df = pd.read_excel(book, sheet_name=book.sheet_names[0], dtype=object)
                rows = df.to_dict("records")
            snapshot = _normalise_flat_rows(rows, metadata=metadata, filename=filename)
        except SnapshotParseError:
            raise
        except Exception as exc:
            raise SnapshotParseError(f"invalid XLSX snapshot: {exc}") from exc
    else:
        raise SnapshotParseError("snapshot file must be CSV, XLSX or JSON")

    snapshot.payload_sha256 = hashlib.sha256(payload).hexdigest()
    return snapshot


def inventory_snapshot_from_space_values(
        rows: Iterable[Mapping[str, Any]], *, captured_at: str | datetime,
        source: str, site: str = "", acquisition_mode: str = "adapter",
        site_captured_at: Optional[Mapping[str, str]] = None,
        connector_config_revision: str = "") -> InventorySnapshot:
    """Normalize adapter ``SpaceValue`` totals into the snapshot contract.

    The existing simulated adapter returns one aggregate object per pile.  A
    future RabbitMQ adapter can call this same function after decoding its wire
    response; transport configuration and credentials remain outside this
    module.  ``x_from``/``x_to`` on aggregate responses are intentionally not
    promoted to segment detail.
    """
    captured_text = (captured_at.isoformat() if isinstance(captured_at, datetime)
                     else str(captured_at))
    source_rows = [dict(row) for row in rows]
    flat = []
    for item0 in source_rows:
        item = dict(item0)
        item.update({
            "captured_at": captured_text,
            "source": source,
            "site": _text(_first(item, "site", "site_id")) or site,
        })
        item.setdefault("record_type", "pile")
        flat.append(item)
    snapshot = _normalise_flat_rows(flat, metadata={
        "captured_at": captured_text, "source": source, "site": site,
        "schema_version": SCHEMA_VERSION,
    }, filename="adapter:SpaceValue")
    canonical = json.dumps(source_rows, sort_keys=True,
                           default=str).encode("utf-8")
    snapshot.payload_sha256 = hashlib.sha256(canonical).hexdigest()
    snapshot.acquisition_mode = acquisition_mode
    snapshot.site_captured_at = dict(site_captured_at or {})
    snapshot.connector_config_revision = connector_config_revision
    return snapshot


def _resolved_id(pile_id: str, space_ids: set[str],
                 mappings: Mapping[str, str]) -> Optional[str]:
    if pile_id in mappings:
        target = str(mappings[pile_id]).strip()
        return target if target in space_ids else None
    return pile_id if pile_id in space_ids else None


def validate_inventory_snapshot(
        scenario: Scenario,
        snapshot: InventorySnapshot,
        mappings: Optional[Mapping[str, str]] = None,
        max_age_hours: Optional[float] = 24.0,
        now: Optional[datetime] = None,
        require_complete: bool = False,
) -> SnapshotValidationResult:
    """Validate identifiers, freshness, mass, quality and segment geometry."""
    mappings = dict(mappings or {})
    issues: List[SnapshotIssue] = []

    def error(code: str, message: str, entity: str = "") -> None:
        issues.append(SnapshotIssue("ERROR", code, message, entity))

    def warning(code: str, message: str, entity: str = "") -> None:
        issues.append(SnapshotIssue("WARNING", code, message, entity))

    now_utc = now or datetime.now(timezone.utc)
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        error("NAIVE_NOW", "validation time must include a timezone")
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    now_utc = now_utc.astimezone(timezone.utc)
    captured_utc = snapshot.captured_at
    if captured_utc.tzinfo is None or captured_utc.utcoffset() is None:
        error("NAIVE_CAPTURE_TIME",
              "snapshot captured_at must include a timezone")
        captured_utc = captured_utc.replace(tzinfo=timezone.utc)
    captured_utc = captured_utc.astimezone(timezone.utc)
    age_h = (now_utc - captured_utc).total_seconds() / 3600.0
    if age_h < -(FUTURE_CLOCK_SKEW_MINUTES / 60.0):
        error("FUTURE_SNAPSHOT",
              f"snapshot is {-age_h:.2f} h in the future; check source clock/timezone")
    if max_age_hours is not None:
        if max_age_hours < 0:
            error("BAD_FRESHNESS_LIMIT", "max_age_hours must be non-negative")
        elif age_h > max_age_hours:
            error("STALE_SNAPSHOT",
                  f"snapshot age {age_h:.2f} h exceeds freshness limit "
                  f"{max_age_hours:g} h")
    if snapshot.schema_version not in ("1", SCHEMA_VERSION):
        error("UNSUPPORTED_SCHEMA",
              f"snapshot schema_version '{snapshot.schema_version}' is not supported")
    if not snapshot.piles:
        error("EMPTY_SNAPSHOT", "snapshot contains no piles")

    space_ids = {s.id for s in scenario.spaces}
    grade_ids = {g.id for g in scenario.grades}
    quality_ids = {q.name for q in scenario.quality_params}
    resolved: Dict[str, str] = {}

    # Mapping is explicit and one-to-one.  Reject bad targets even before a
    # row is applied, so a typo cannot silently fall back to an identity ID.
    for external, internal in mappings.items():
        if internal not in space_ids:
            error("UNKNOWN_MAPPING_TARGET",
                  f"mapping target '{internal}' for '{external}' is not a loaded pile",
                  str(external))
    used_sources = {p.pile_id for p in snapshot.piles}
    target_to_sources: Dict[str, List[str]] = defaultdict(list)
    for source_id in used_sources:
        target = _resolved_id(source_id, space_ids, mappings)
        if target:
            target_to_sources[target].append(source_id)
    for target, sources in target_to_sources.items():
        if len(sources) > 1:
            error("DUPLICATE_MAPPING_TARGET",
                  f"multiple source piles {sorted(sources)} map to '{target}'", target)

    seen_resolved: set[str] = set()
    for pile in snapshot.piles:
        target = _resolved_id(pile.pile_id, space_ids, mappings)
        if target is None:
            error("UNKNOWN_PILE",
                  f"source pile '{pile.pile_id}' has no exact loaded ID or explicit mapping",
                  pile.pile_id)
            continue
        resolved[pile.pile_id] = target
        if target in seen_resolved:
            error("DUPLICATE_PILE", f"pile '{target}' is declared more than once", target)
            continue
        seen_resolved.add(target)
        sp = scenario.space(target)

        if scenario.config.get("MULTI_GRADE_ZONES_ENABLED") and any(
                z.space_id == target for z in scenario.pile_zones):
            error("ACTIVE_ZONES_UNSUPPORTED",
                  f"pile '{target}' has active multi-grade zones; import matching "
                  "zone/campaign data instead of replacing only its parent total",
                  target)

        if pile.site_conflict:
            error("INCONSISTENT_SITE",
                  f"source rows for '{pile.pile_id}' contain different sites", target)
        if pile.site and pile.site != sp.site:
            error("SITE_MISMATCH",
                  f"source site '{pile.site}' does not match {target} site '{sp.site}'",
                  target)
        if pile.aggregate_rows > 1:
            error("DUPLICATE_AGGREGATE",
                  f"pile '{pile.pile_id}' has {pile.aggregate_rows} aggregate rows",
                  target)
        if pile.aggregate_rows and pile.mass_t is None and not pile.segments:
            error("MISSING_MASS", f"aggregate row for '{target}' has no mass_t", target)
        if not pile.aggregate_rows and not pile.segments:
            error("NO_INVENTORY_VALUE",
                  f"pile '{target}' has neither an aggregate mass nor segments", target)

        if pile.mass_t is not None:
            if pile.mass_t < 0:
                error("NEGATIVE_MASS", f"pile '{target}' mass cannot be negative", target)
            if pile.mass_t > sp.capacity_t + MASS_RECONCILIATION_TOLERANCE_T:
                error("OVER_CAPACITY",
                      f"pile '{target}' mass {pile.mass_t:g} t exceeds capacity "
                      f"{sp.capacity_t:g} t", target)
        if pile.grade and pile.grade not in grade_ids:
            error("UNKNOWN_GRADE", f"pile '{target}' grade '{pile.grade}' is unknown", target)
        _validate_grade_compatibility(scenario, sp, pile.grade, error)
        _validate_quality(pile.quality, quality_ids, target, error)
        if pile.state and pile.state not in VALID_STATES:
            error("BAD_STATE",
                  f"pile '{target}' state '{pile.state}' is not supported", target)

        ordered_segments = sorted(pile.segments, key=lambda x: (x.x_from, x.x_to))
        previous: Optional[SegmentSnapshot] = None
        for seg in ordered_segments:
            if seg.mass_t < 0:
                error("NEGATIVE_MASS",
                      f"segment row {seg.row_number} for '{target}' has negative mass",
                      target)
            if seg.age_days < 0:
                error("NEGATIVE_AGE",
                      f"segment row {seg.row_number} for '{target}' has negative age",
                      target)
            if seg.x_to <= seg.x_from:
                error("BAD_SEGMENT_RANGE",
                      f"segment row {seg.row_number} x_to must exceed x_from", target)
            if seg.x_from < sp.x_from or seg.x_to > sp.x_to:
                error("SEGMENT_OUT_OF_BOUNDS",
                      f"segment row {seg.row_number} [{seg.x_from:g}, {seg.x_to:g}] m "
                      f"is outside pile bounds [{sp.x_from:g}, {sp.x_to:g}] m", target)
            if previous is not None and seg.x_from < previous.x_to:
                error("SEGMENT_OVERLAP",
                      f"segments for '{target}' overlap near x={seg.x_from:g} m", target)
            previous = seg
            effective_grade = seg.grade or pile.grade or sp.grade
            if effective_grade and effective_grade not in grade_ids:
                error("UNKNOWN_GRADE",
                      f"segment row {seg.row_number} grade '{effective_grade}' is unknown",
                      target)
            _validate_grade_compatibility(scenario, sp, effective_grade, error)
            _validate_quality(seg.quality, quality_ids, target, error)

        if pile.segments:
            segment_total = sum(s.mass_t for s in pile.segments)
            if segment_total > sp.capacity_t + MASS_RECONCILIATION_TOLERANCE_T:
                error("OVER_CAPACITY",
                      f"segments for '{target}' total {segment_total:g} t, exceeding "
                      f"capacity {sp.capacity_t:g} t", target)
            if pile.mass_t is not None and abs(pile.mass_t - segment_total) > \
                    MASS_RECONCILIATION_TOLERANCE_T:
                error("MASS_RECONCILIATION",
                      f"pile '{target}' total {pile.mass_t:g} t does not reconcile "
                      f"with segment sum {segment_total:g} t (tolerance "
                      f"{MASS_RECONCILIATION_TOLERANCE_T:g} t)", target)

        # Mass-only snapshots are useful, but their grade/quality remains the
        # scenario basis and must be visible rather than silently called live.
        has_segment_quality = bool(pile.segments) and all(
            bool(s.quality) for s in pile.segments if s.mass_t > 0)
        if not pile.quality and not has_segment_quality:
            warning("QUALITY_FALLBACK",
                    f"pile '{target}' has no measured quality; planning keeps/derives "
                    "the scenario grade-quality basis", target)

    covered = set(resolved.values())
    missing = sorted(space_ids - covered)
    if missing:
        message = (f"snapshot is missing {len(missing)} of {len(space_ids)} loaded piles: "
                   + ", ".join(missing))
        if require_complete:
            error("INCOMPLETE_SNAPSHOT", message)
        else:
            warning("PARTIAL_SNAPSHOT", message + "; unchanged piles keep scenario values")

    return SnapshotValidationResult(snapshot=snapshot, issues=issues,
                                    resolved_ids=resolved)


def _validate_quality(quality: Mapping[str, float], known: set[str], entity: str,
                      error) -> None:
    for key, value in quality.items():
        if key not in known:
            error("UNKNOWN_QUALITY_PARAM",
                  f"pile '{entity}' quality parameter '{key}' is not configured", entity)
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            error("BAD_QUALITY", f"pile '{entity}' quality '{key}' is not finite", entity)


def _validate_grade_compatibility(scenario: Scenario, sp, grade_id: str,
                                  error) -> None:
    if not grade_id:
        return
    grade = scenario.grade(grade_id)
    if not grade:
        return
    if sp.material_class and grade.material_class and \
            sp.material_class != grade.material_class:
        error("GRADE_CLASS_MISMATCH",
              f"pile '{sp.id}' class '{sp.material_class}' is incompatible with "
              f"grade '{grade_id}' class '{grade.material_class}'", sp.id)
    profile = scenario.material_profile(sp.profile_id) if sp.profile_id else None
    if profile and profile.grade_ids and grade_id not in profile.grade_ids:
        error("PROFILE_GRADE_MISMATCH",
              f"grade '{grade_id}' is not in pile '{sp.id}' profile "
              f"'{profile.id}' grade family", sp.id)


def _weighted_quality(scenario: Scenario, segments: List[SegmentSnapshot],
                      pile_grade: str) -> Dict[str, float]:
    totals: Dict[str, float] = defaultdict(float)
    weights: Dict[str, float] = defaultdict(float)
    for seg in segments:
        grade_id = seg.grade or pile_grade
        grade = scenario.grade(grade_id) if grade_id else None
        values = seg.quality or (dict(grade.quality) if grade else {})
        for key, value in values.items():
            totals[key] += float(value) * seg.mass_t
            weights[key] += seg.mass_t
    return {key: totals[key] / weights[key] for key in totals if weights[key] > 0}


def apply_inventory_snapshot(
        scenario: Scenario,
        snapshot: InventorySnapshot,
        mappings: Optional[Mapping[str, str]] = None,
        max_age_hours: Optional[float] = 24.0,
        now: Optional[datetime] = None,
        require_complete: bool = False,
) -> SnapshotApplyResult:
    """Return a snapshot-updated deep copy; never mutate ``scenario``.

    If validation fails, ``applied`` is false and ``scenario`` is an unchanged
    deep copy of the input so callers can use one predictable return type.
    """
    validation = validate_inventory_snapshot(
        scenario, snapshot, mappings=mappings, max_age_hours=max_age_hours,
        now=now, require_complete=require_complete)
    candidate = copy.deepcopy(scenario)
    if validation.has_errors:
        return SnapshotApplyResult(False, candidate, validation.issues)

    changed: List[str] = []
    delta = 0.0
    for pile in snapshot.piles:
        target = validation.resolved_ids[pile.pile_id]
        sp = candidate.space(target)
        old_mass = sp.mass_t

        # Any provided pile replaces the old t0 segment basis.  This is
        # important for aggregate-only updates: retaining demo ages would make
        # the FIFO display look live while the geometry is actually stale.
        candidate.segments = [s for s in candidate.segments if s.space_id != target]

        if pile.segments:
            effective_grade = pile.grade
            if not effective_grade:
                grade_mass: Counter[str] = Counter()
                for seg in pile.segments:
                    grade_mass[seg.grade or sp.grade] += seg.mass_t
                if grade_mass:
                    effective_grade = sorted(
                        grade_mass, key=lambda g: (-grade_mass[g], g))[0]
            new_segments: List[Segment] = []
            for seg in sorted(pile.segments, key=lambda x: (x.x_from, x.x_to)):
                new_segments.append(Segment(
                    space_id=target,
                    x_from=seg.x_from,
                    x_to=seg.x_to,
                    mass_t=seg.mass_t,
                    grade=seg.grade or pile.grade or sp.grade,
                    quality=dict(seg.quality),
                    age_days=seg.age_days,
                ))
            candidate.segments.extend(new_segments)
            sp.mass_t = sum(s.mass_t for s in pile.segments)
            if effective_grade:
                sp.grade = effective_grade
            sp.quality = (dict(pile.quality) if pile.quality else
                          _weighted_quality(candidate, pile.segments, sp.grade))
        else:
            sp.mass_t = float(pile.mass_t or 0.0)
            if pile.grade:
                sp.grade = pile.grade
                if not pile.quality:
                    # An explicit new grade must not retain an explicit quality
                    # vector belonging to the old grade.  Empty quality means
                    # Scenario.quality_of falls back to the new grade vector.
                    sp.quality = {}
            if pile.quality:
                sp.quality = dict(pile.quality)

        if pile.state:
            sp.state = pile.state
        elif sp.mass_t <= MASS_RECONCILIATION_TOLERANCE_T:
            sp.state = "empty"
        elif sp.mass_t >= 0.99 * sp.capacity_t:
            sp.state = "full"
        else:
            sp.state = "mixed"
        delta += sp.mass_t - old_mass
        changed.append(target)

    applied_at = now or datetime.now(timezone.utc)
    if applied_at.tzinfo is None or applied_at.utcoffset() is None:
        applied_at = applied_at.replace(tzinfo=timezone.utc)
    applied_at = applied_at.astimezone(timezone.utc)
    covered = sorted(set(changed))
    quality_count = sum(
        1 for p in snapshot.piles
        if p.quality or (
            any(s.mass_t > 0 for s in p.segments)
            and all(bool(s.quality) for s in p.segments if s.mass_t > 0)))
    segment_count = sum(1 for p in snapshot.piles if p.segments)
    candidate.config["INVENTORY_SNAPSHOT"] = {
        "schema_version": snapshot.schema_version,
        "captured_at": snapshot.captured_at.isoformat(),
        "applied_at": applied_at.isoformat(),
        "source": snapshot.source,
        "source_file": snapshot.source_file,
        "payload_sha256": snapshot.payload_sha256,
        "acquisition_mode": snapshot.acquisition_mode,
        "site_captured_at": dict(snapshot.site_captured_at),
        "connector_config_revision": snapshot.connector_config_revision,
        "covered_piles": covered,
        "covered_count": len(covered),
        "expected_count": len(candidate.spaces),
        "complete": len(covered) == len(candidate.spaces),
        "quality_pile_count": quality_count,
        "segment_pile_count": segment_count,
        "mass_delta_t": delta,
        "id_mappings": dict(mappings or {}),
    }
    # A starting inventory snapshot defines planning t0.
    candidate.sim.start_iso = snapshot.captured_at.isoformat()
    return SnapshotApplyResult(True, candidate, validation.issues,
                               changed_space_ids=covered, mass_delta_t=delta)


def snapshot_template_csv(scenario: Scenario) -> bytes:
    """Return a UTF-8 CSV template with exact model IDs and blank live fields."""
    rows = []
    for sp in scenario.spaces:
        rows.append({
            "captured_at": "",
            "source": "",
            "site": sp.site,
            "pile_id": sp.id,
            "record_type": "pile",
            "mass_t": "",
            "grade": "",
            "quality": "",
            "state": "",
            "x_from": "",
            "x_to": "",
            "age_days": "",
            "model_capacity_t": sp.capacity_t,
            "scenario_mass_t_reference": sp.mass_t,
        })
    return pd.DataFrame(rows).to_csv(index=False).encode("utf-8-sig")


def snapshot_diff_rows(scenario: Scenario, snapshot: InventorySnapshot,
                       mappings: Optional[Mapping[str, str]] = None) -> List[dict]:
    """Build a UI-friendly, non-mutating before/after summary."""
    mappings = dict(mappings or {})
    space_ids = {s.id for s in scenario.spaces}
    rows: List[dict] = []
    for pile in snapshot.piles:
        target = _resolved_id(pile.pile_id, space_ids, mappings)
        if target is None:
            rows.append({
                "source pile": pile.pile_id, "model pile": "unmapped",
                "site": pile.site or "—", "current mass [t]": None,
                "snapshot mass [t]": (sum(s.mass_t for s in pile.segments)
                                       if pile.segments else pile.mass_t),
                "delta [t]": None, "grade": pile.grade or "—",
                "quality supplied": bool(pile.quality or
                                           any(s.quality for s in pile.segments)),
                "segments": len(pile.segments),
            })
            continue
        sp = scenario.space(target)
        new_mass = (sum(s.mass_t for s in pile.segments)
                    if pile.segments else pile.mass_t)
        rows.append({
            "source pile": pile.pile_id,
            "model pile": target,
            "site": sp.site,
            "current mass [t]": sp.mass_t,
            "snapshot mass [t]": new_mass,
            "delta [t]": (new_mass - sp.mass_t if new_mass is not None else None),
            "grade": pile.grade or ("derived from segments" if pile.segments
                                      else f"keep {sp.grade}"),
            "quality supplied": bool(pile.quality or
                                       any(s.quality for s in pile.segments)),
            "segments": len(pile.segments),
        })
    return rows
