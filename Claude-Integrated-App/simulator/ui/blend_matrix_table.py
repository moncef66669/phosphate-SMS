"""Lossless row-per-recipe editing for shared blend catalogues.

The solver's component-list representation is unchanged. Source-qualified
columns keep pre-washing inputs distinct even when their grade names match.
"""
from __future__ import annotations

import math

import pandas as pd


def component_columns(catalog, extra_components=()):
    """Return ordered percentage-column labels and their input identities."""
    columns = {}
    components = [row for entry in catalog for row in entry["components"]]
    for row in [*components, *extra_components]:
        grade = str(row.get("grade_id", "") or "").strip()
        source = str(row.get("source_space", "") or "").strip()
        if not grade:
            continue
        identity = {"grade_id": grade}
        if source:
            identity["source_space"] = source
        label = f"{grade} @ {source} [%]" if source else f"{grade} [%]"
        if label in columns and columns[label] != identity:
            raise ValueError(f"Ambiguous matrix input column: {label}")
        columns[label] = identity
    return columns


def matrix_frame(catalog, columns):
    """Pivot components into a single editable row for each matrix."""
    rows = []
    for entry in catalog:
        row = {"Matrix ID": entry["id"]}
        for label, identity in columns.items():
            parts = [part for part in entry["components"] if
                     str(part.get("grade_id", "")) == identity["grade_id"] and
                     str(part.get("source_space", "") or "") == identity.get("source_space", "")]
            row[label] = (100.0 * sum(float(part.get("target_fraction", 0) or 0)
                                      for part in parts) if parts else None)
        rows.append(row)
    return pd.DataFrame(
        rows, columns=["Matrix ID", *columns]).reset_index(drop=True)


def _text(value):
    return "" if value is None or pd.isna(value) else str(value).strip()


def parse_matrix_frame(frame, columns, original_catalog=()):
    """Validate every edited row before reconstructing the component lists."""
    entries, seen = [], set()
    names = {entry["id"]: entry.get("name", entry["id"]) for entry in original_catalog}
    for _, row in frame.iterrows():
        matrix_id = _text(row.get("Matrix ID"))
        components = []
        has_share = False
        for label, identity in columns.items():
            value = row.get(label)
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            # NaN is how the grid represents a blank numeric cell. Infinity
            # and non-numeric text are invalid, not an omitted input.
            if pd.isna(value):
                continue
            has_share = True
            try:
                percent = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"{matrix_id or 'New matrix'}: {label} must be numeric.") from None
            if not math.isfinite(percent) or not 0 <= percent <= 100:
                raise ValueError(f"{matrix_id or 'New matrix'}: {label} must be between 0 and 100%.")
            if percent > 0:
                components.append({**identity, "target_fraction": percent / 100.0})
        if not matrix_id and not has_share:
            continue
        if not matrix_id:
            raise ValueError("Every matrix needs a Matrix ID.")
        if matrix_id in seen:
            raise ValueError(f"Matrix ID '{matrix_id}' is duplicated; use one row per matrix.")
        seen.add(matrix_id)
        if len(components) < 2:
            raise ValueError(f"{matrix_id}: a matrix needs at least two inputs with positive percentages.")
        total = sum(part["target_fraction"] for part in components)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"{matrix_id}: percentages must total 100% (now {100 * total:g}%).")
        entries.append({"id": matrix_id, "name": names.get(matrix_id) or matrix_id,
                        "components": components})
    return entries
