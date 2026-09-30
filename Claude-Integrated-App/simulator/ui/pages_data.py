"""Pages: 2 Grades/Quality/Inputs · 3 Stockyards · 4 Routes · 5 Demand."""
from __future__ import annotations

import io
import json
import math
import re
from copy import deepcopy
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from mph_sim import (blend_preferences, blending, datasets, flow_config as flow_model,
                     prewash_blending, washing)
from mph_sim.domain import DemandEntry, Evidence, Grade, SpecRange
from mph_sim.flow_config import (destination_line_status,
                                 process_stage_enabled,
                                 set_destination_line_enabled)
from mph_sim.grade_catalog import (DEFAULT_GRADE_CATALOG_PATH,
                                   GradeCatalogError, save_grade_catalog)
from mph_sim.grade_ids import RAW_GRADE_IDS, RAW_PENDING_GRADE_ID
from mph_sim.inventory_snapshot import (
    SnapshotParseError, apply_inventory_snapshot, parse_inventory_snapshot,
    snapshot_diff_rows, snapshot_template_csv, validate_inventory_snapshot,
)
from mph_sim.persistence import (TABLE_REGISTRY, entities_to_df, export_excel,
                                 import_table, scenario_tables)
from mph_sim.routes import RouteGraph, reachability_matrix, space_node

from . import common, theme
from .blend_matrix_table import component_columns, matrix_frame, parse_matrix_frame
from .theme import (BLUE, DGREEN, GREEN, GREY, NAVY, ORANGE, RED, VIOLET,
                    ev_badge, plot, plotly_layout, series_fig, show_df)

UNITS = {  # column -> unit shown in help/captions
    "capacity_t": "t", "mass_t": "t", "reserved_t": "t", "safety_stock_t": "t",
    "rate_min_tph": "t/h", "rate_max_tph": "t/h", "capacity_tph": "t/h",
    "min_stable_tph": "t/h", "rate_tph": "t/h", "demand_default_tph": "t/h",
    "transit_buckets": "buckets", "x_from": "m", "x_to": "m",
    "travel_speed_m_min": "m/min", "setup_min": "min", "min_separation_m": "m",
    "changeover_min": "min", "start_stop_limit_per_h": "1/h",
    "age_days": "days", "position_x": "m", "rock_value": "value/t",
}


# =========================================================== quality configuration
def _render_destination_quality_specs(scn):
    """Editable destination specifications; configuration, never plan output."""
    st.markdown("#### Destination quality specifications")
    st.caption(
        "Configure min/max/target limits and whether each limit is hard or "
        "soft. The planner reads these values when a new plan is generated.")
    for d in scn.destinations:
        with st.expander(
                f"{d.id} — {len(d.spec)} quality parameters",
                expanded=bool(d.spec)):
            sdf = pd.DataFrame([{
                "param": spec.param,
                "min": spec.pmin,
                "max": spec.pmax,
                "target": spec.target,
                "averaging [h]": spec.averaging_h,
                "margin": spec.margin,
                "hard": spec.hard,
            } for spec in d.spec])
            if sdf.empty:
                sdf = pd.DataFrame(columns=[
                    "param", "min", "max", "target",
                    "averaging [h]", "margin", "hard",
                ])
            edited = st.data_editor(
                sdf, num_rows="dynamic", key=f"quality_spec_{d.id}")
            if st.button("Apply specification", key=f"apply_quality_spec_{d.id}"):
                d.spec = [SpecRange(
                    param=str(row["param"]),
                    pmin=None if pd.isna(row["min"]) else float(row["min"]),
                    pmax=None if pd.isna(row["max"]) else float(row["max"]),
                    target=(None if pd.isna(row["target"])
                            else float(row["target"])),
                    averaging_h=float(row["averaging [h]"] or 1.0),
                    margin=float(row["margin"] or 0.0),
                    hard=bool(row["hard"]),
                ) for _, row in edited.iterrows()
                    if str(row["param"]).strip()]
                stale = common.invalidate_outputs()
                common.revalidate()
                st.success(
                    "specification applied"
                    + (" — previous plan cleared" if stale else ""))


# ==================================================== compact operator Configure
_CONFIGURE_SITE_LABELS = {
    "LOUTA": "Louta",
    "BENGUERIR": "Benguerir",
    "MPH": "MPH",
}
_PROCESS_LINE_LABELS = {
    "LT-WASH-L1": "Washing line 1",
    "LT-WASH-L2": "Washing line 2",
    "BG-WASH-L1": "Washing line 1",
    "BG-WASH-L2": "Washing line 2",
    "BG-WASH-L3": "Washing line 3",
}
_MPH_DESTINATION_LINES = ("PAP-1", "PAP-2", "DRY-FEED")
_INTERFACE_AREAS = {"mine-interface", "process-interface"}
_BLEND_STAGE_PRE_WASHING = "Pre-washing stockpiles"
_BLEND_STAGE_MPH_HUMID = "MPH humid-phosphate stockpiles"
_BLEND_STAGE_PRE_DRYING = "Pre-drying"
_BLEND_STAGE_OPTIONS = (
    _BLEND_STAGE_PRE_WASHING,
    _BLEND_STAGE_MPH_HUMID,
    _BLEND_STAGE_PRE_DRYING,
)
_BLEND_MATRIX_COLUMNS = (
    "Matrix",
    "Mode",
    "Inputs / share rule",
    "Resulting grade",
    "Batch [t]",
    "Ready hour",
    "Applicable stockpiles",
    "Currently assigned",
    "Enabled at",
)


def _configure_sites(scn) -> list[str]:
    """Return loaded operational sites in the stable flow order."""
    present = {
        getattr(entity, "site", "")
        for collection in (
            scn.spaces, scn.machines, scn.destinations,
        )
        for entity in collection
    }
    ordered = [
        site_id for site_id in _CONFIGURE_SITE_LABELS
        if site_id in present
    ]
    return ordered or sorted(site for site in present if site)


def _process_lines_for_site(scn, site_id: str) -> list[str]:
    """Return only process lines backed by stages in the loaded scenario."""
    prefixes = {
        "LOUTA": "LT-",
        "BENGUERIR": "BG-",
    }
    prefix = prefixes.get(site_id)
    if not prefix:
        return []
    loaded_stages = {
        transfer.process_stage for transfer in scn.process_transfers
    } | {
        destination.process_stage for destination in scn.destinations
        if destination.process_stage
    }
    return [
        line_id
        for line_id, stages in flow_model.PROCESS_LINE_STAGE_MAP.items()
        if line_id.startswith(prefix)
        and any(stage in loaded_stages for stage in stages)
    ]


def _planning_machines_for_site(scn, site_id: str):
    """Exclude future/reference machines that the planner cannot schedule."""
    return [
        machine for machine in scn.machines
        if machine.site == site_id
        and bool(getattr(machine, "planning_enabled", True))
        and str(getattr(machine, "lifecycle", "EXISTING")).upper() != "FUTURE"
    ]


def _physical_stock_for_site(scn, site_id: str):
    """Keep physical stockpiles; omit short-lived hoppers and WIP buffers."""
    return [
        space for space in scn.spaces
        if space.site == site_id and space.area not in _INTERFACE_AREAS
    ]


def _validate_stock_editor(edited: pd.DataFrame, stock_by_id: dict):
    """Return validated mass changes without mutating the scenario."""
    masses: dict[str, float] = {}
    errors: list[str] = []
    seen: set[str] = set()
    for row in edited.to_dict("records"):
        stock_id = str(row.get("Stockpile", "")).strip()
        if stock_id in seen:
            errors.append(f"{stock_id or 'blank row'} appears more than once")
            continue
        seen.add(stock_id)
        stock = stock_by_id.get(stock_id)
        if stock is None:
            errors.append(f"Unknown stockpile: {stock_id or 'blank row'}")
            continue
        try:
            mass_t = float(row.get("Starting stock [t]"))
        except (TypeError, ValueError):
            errors.append(f"{stock_id}: starting stock must be a number")
            continue
        if not math.isfinite(mass_t):
            errors.append(f"{stock_id}: starting stock must be finite")
        elif mass_t < 0 or mass_t > float(stock.capacity_t):
            errors.append(
                f"{stock_id}: enter 0–{float(stock.capacity_t):,.0f} t")
        else:
            masses[stock_id] = mass_t
    missing = set(stock_by_id) - seen
    if missing:
        errors.append("Missing stockpile rows: " + ", ".join(sorted(missing)))
    return masses, errors


_GRADE_ID_COLUMN = "Grade ID"
_GRADE_NAME_COLUMN = "Grade name"
_GRADE_CLASS_COLUMN = "Material state"
_GRADE_SOURCE_COLUMN = "Raw feed grade"
_GRADE_BASIS_COLUMN = "Basis"
_GRADE_USAGE_COLUMN = "Stockpiles using grade"
_GRADE_OVERRIDE_COLUMN = "With pile-specific assay"
_HIDDEN_CHEMISTRY_PARAMETERS = {"CaO_CO2"}
_GRADE_YIELD_COLUMN = "Beneficiation yield [%]"
_RAW_MOISTURE_COLUMN = "Raw moisture [%]"
_PRODUCT_MOISTURE_COLUMN = "Wet-product moisture [%]"
_RAW_TO_TSM_COLUMN = "Raw → TSM factor [%]"
_WET_PER_TSM_COLUMN = "Wet product [t/TSM]"


def _grade_conversion_grades(scn):
    """Return washed conversion grades grouped by their raw-feed family."""
    selected = [
        grade for grade in scn.grades
        if getattr(grade, "beneficiation_yield", None) is not None
    ]
    source_order = {
        "RAW-BT": 0,
        "RAW-BT-HIGH-CD": 1,
        "RAW-BT-LOW-CD": 2,
    }
    merchant_order = {"TBT": 0, "BT": 1, "MT": 2}

    def sort_key(grade):
        grade_id = str(grade.id)
        merchant_family = next(
            (family for family in merchant_order
             if grade_id == family or grade_id.startswith(f"{family}-")),
            grade_id,
        )
        source_grade_id = getattr(grade, "source_grade_id", "")
        return (
            source_order.get(source_grade_id, len(source_order)),
            merchant_order.get(merchant_family, len(merchant_order)),
            grade_id,
        )

    return sorted(selected, key=sort_key)


def _grade_yield_moisture_frame(scn) -> pd.DataFrame:
    """Build the stage-aware mass-conversion editor.

    The model fields retain fractions for yield and percentages for moisture;
    this frame presents every input as a percentage and derives only concise
    operator checks from them.
    """
    rows = []
    for grade in _grade_conversion_grades(scn):
        yield_fraction = getattr(grade, "beneficiation_yield", None)
        raw_moisture = getattr(grade, "raw_moisture_pct", None)
        product_moisture = getattr(grade, "product_moisture_pct", None)
        raw_to_tsm = None
        if yield_fraction is not None and raw_moisture is not None:
            raw_to_tsm = (
                float(yield_fraction)
                * (1.0 - float(raw_moisture) / 100.0)
                * 100.0
            )
        wet_per_tsm = None
        if product_moisture is not None and float(product_moisture) < 100.0:
            wet_per_tsm = 1.0 / (1.0 - float(product_moisture) / 100.0)
        rows.append({
            _GRADE_ID_COLUMN: grade.id,
            _GRADE_NAME_COLUMN: theme.operator_text(grade.name),
            _GRADE_SOURCE_COLUMN: getattr(grade, "source_grade_id", ""),
            _GRADE_YIELD_COLUMN: (
                None if yield_fraction is None
                else 100.0 * float(yield_fraction)
            ),
            _RAW_MOISTURE_COLUMN: raw_moisture,
            _PRODUCT_MOISTURE_COLUMN: product_moisture,
            _RAW_TO_TSM_COLUMN: raw_to_tsm,
            _WET_PER_TSM_COLUMN: wet_per_tsm,
        })
    return pd.DataFrame(rows, columns=[
        _GRADE_ID_COLUMN,
        _GRADE_NAME_COLUMN,
        _GRADE_SOURCE_COLUMN,
        _GRADE_YIELD_COLUMN,
        _RAW_MOISTURE_COLUMN,
        _PRODUCT_MOISTURE_COLUMN,
        _RAW_TO_TSM_COLUMN,
        _WET_PER_TSM_COLUMN,
    ])


def _stage_grade_yield_moisture_rows(staged,
                                     edited: pd.DataFrame) -> list[str]:
    """Validate and stage one mass-conversion row per merchant grade."""
    errors: list[str] = []
    grades = _grade_conversion_grades(staged)
    expected = {grade.id for grade in grades}
    rows_by_id: dict[str, dict] = {}
    for row in edited.to_dict("records"):
        grade_id = _editor_text(row.get(_GRADE_ID_COLUMN))
        if not grade_id:
            errors.append("A row is missing its protected Grade ID")
        elif grade_id in rows_by_id:
            errors.append(f"{grade_id}: duplicate grade row")
        else:
            rows_by_id[grade_id] = row

    missing = sorted(expected - set(rows_by_id))
    unexpected = sorted(set(rows_by_id) - expected)
    if missing:
        errors.append("Missing grade rows: " + ", ".join(missing))
    if unexpected:
        errors.append("Unknown grade rows: " + ", ".join(unexpected))
    if errors:
        return errors

    parsed_by_grade: dict[str, tuple[float, float, float]] = {}
    for grade in grades:
        row = rows_by_id[grade.id]
        values = []
        for column in (
                _GRADE_YIELD_COLUMN,
                _RAW_MOISTURE_COLUMN,
                _PRODUCT_MOISTURE_COLUMN):
            parsed = _optional_finite_number(
                row.get(column), f"{grade.id} · {column}", errors)
            if parsed is None:
                errors.append(f"{grade.id} · {column} is required")
            values.append(parsed)
        if any(value is None for value in values):
            continue
        yield_pct, raw_moisture, product_moisture = values
        if not 0.0 < yield_pct <= 100.0:
            errors.append(
                f"{grade.id} · {_GRADE_YIELD_COLUMN} must be above 0 and "
                "at most 100")
        if not 0.0 <= raw_moisture < 100.0:
            errors.append(
                f"{grade.id} · {_RAW_MOISTURE_COLUMN} must be at least 0 "
                "and below 100")
        if not 0.0 <= product_moisture < 100.0:
            errors.append(
                f"{grade.id} · {_PRODUCT_MOISTURE_COLUMN} must be at least 0 "
                "and below 100")
        parsed_by_grade[grade.id] = (
            yield_pct / 100.0,
            raw_moisture,
            product_moisture,
        )
    if errors:
        return errors

    for grade in grades:
        before = (
            getattr(grade, "beneficiation_yield", None),
            getattr(grade, "raw_moisture_pct", None),
            getattr(grade, "product_moisture_pct", None),
        )
        parsed = parsed_by_grade[grade.id]
        # A percentage round-trip through pandas must not create a false
        # override solely from binary floating-point representation.
        after = tuple(
            old_value
            if old_value is not None and math.isclose(
                float(old_value), float(new_value),
                rel_tol=0.0, abs_tol=1e-12)
            else new_value
            for old_value, new_value in zip(before, parsed)
        )
        grade.beneficiation_yield = after[0]
        grade.raw_moisture_pct = after[1]
        grade.product_moisture_pct = after[2]
        if after != before:
            field_names = (
                "beneficiation_yield",
                "raw_moisture_pct",
                "product_moisture_pct",
            )
            _record_grade_profile_override(
                staged,
                "grade_mass_conversion",
                grade.id,
                [field_name for field_name, old_value, new_value
                 in zip(field_names, before, after)
                 if old_value != new_value],
            )
    return []


def _quality_column_map(scn) -> dict[str, str]:
    """Map registered quality names to unambiguous, unit-aware UI columns."""
    return {
        param.name: (
            f"{param.name} [{param.unit}]" if param.unit else param.name
        )
        for param in scn.quality_params
    }


def _grade_profile_frame(scn) -> pd.DataFrame:
    """Return the compact, numeric grade-vector editor shown to operators."""
    columns = _quality_column_map(scn)
    rows = []
    # Follow the physical flow: known raw/screened feed grades first, then the
    # pending raw placeholder, then washed products grouped by Cd lineage.
    # Keeping raw grades at the top also makes the complete feed catalogue
    # visible without scrolling through every washed variant.
    conversion_grades = _grade_conversion_grades(scn)
    conversion_ids = {grade.id for grade in conversion_grades}
    grade_by_id = {grade.id: grade for grade in scn.grades}
    raw_grades = [
        grade_by_id[grade_id]
        for grade_id in (*RAW_GRADE_IDS, RAW_PENDING_GRADE_ID)
        if grade_id in grade_by_id and grade_id not in conversion_ids
    ]
    ordered_ids = {
        grade.id for grade in (*raw_grades, *conversion_grades)
    }
    ordered_grades = raw_grades + conversion_grades + [
        grade for grade in scn.grades if grade.id not in ordered_ids
    ]
    for grade in ordered_grades:
        if getattr(grade, "is_specification", False):
            continue  # G10 intervals live in the treatment/receiving editor.
        used = [
            stock for stock in scn.spaces
            if stock.grade == grade.id
        ]
        row = {
            _GRADE_ID_COLUMN: grade.id,
            _GRADE_NAME_COLUMN: grade.name,
            _GRADE_CLASS_COLUMN: (
                grade.material_class.replace("_", " ").title()
                if grade.material_class else "Unspecified"
            ),
            _GRADE_SOURCE_COLUMN: getattr(grade, "source_grade_id", ""),
            _GRADE_BASIS_COLUMN: theme.operator_text(
                grade.evidence.status).title(),
            _GRADE_USAGE_COLUMN: len(used),
            _GRADE_OVERRIDE_COLUMN: sum(bool(stock.quality) for stock in used),
        }
        for param_name, display_name in columns.items():
            row[display_name] = grade.quality.get(param_name)
        rows.append(row)
    return pd.DataFrame(rows)


def _optional_finite_number(value, label: str, errors: list[str]):
    """Parse an optional editor number while collecting a concise error."""
    if value is None or pd.isna(value) or (
            isinstance(value, str) and not value.strip()):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        errors.append(f"{label} must be numeric")
        return None
    if not math.isfinite(parsed):
        errors.append(f"{label} must be finite")
        return None
    return parsed


def _grade_references(scn, grade_id: str) -> list[str]:
    """Return concise model references that make a grade unsafe to delete."""
    references: set[str] = set()
    for stock in scn.spaces:
        if stock.grade == grade_id:
            references.add(f"stockpile {stock.id}")
        if stock.designated_grade == grade_id:
            references.add(f"stacking designation for {stock.id}")
    for segment in scn.segments:
        if segment.grade == grade_id:
            references.add(f"segment in {segment.space_id}")
    for zone in getattr(scn, "pile_zones", []):
        if zone.grade == grade_id:
            references.add(f"zone {zone.space_id}#{zone.zone_id}")
    for destination in scn.destinations:
        if destination.requested_grade == grade_id:
            references.add(f"destination {destination.id}")
    for entry in scn.demand:
        if getattr(entry, "requested_grade", "") == grade_id:
            references.add(f"demand {entry.dest_id}")
    for transfer in scn.process_transfers:
        if transfer.output_grade == grade_id:
            references.add(f"process transfer {transfer.id}")
    for profile in scn.material_profiles:
        if profile.grade_ids == [grade_id]:
            references.add(f"sole grade in material profile {profile.id}")
    for entry in scn.belt_profile:
        if entry.grade == grade_id:
            references.add(f"in-transit material on {entry.link_id}")
    for entry in scn.exogenous:
        if entry.grade == grade_id:
            references.add(f"external flow {entry.id}")
    for job in scn.fixed_jobs:
        if job.grade == grade_id:
            references.add(f"fixed job {job.id}")
    for grade in scn.grades:
        if grade.id != grade_id and grade.source_grade_id == grade_id:
            references.add(f"source of grade {grade.id}")
    # Recipe components, resulting grades and carried construction progress
    # are executable references even when no current stock uses that grade.
    from mph_sim.blending import chevron_settings
    settings = chevron_settings(scn)
    for space_id, result_grade in settings["result_grades"].items():
        if result_grade == grade_id:
            references.add(f"result of blend {space_id}")
    for field in ("recipes", "share_bounds_by_space", "proposed_recipes_by_space"):
        for space_id, components in settings[field].items():
            if any(isinstance(row, dict) and row.get("grade_id") == grade_id
                   for row in components):
                references.add(f"component of blend {space_id}")
    for space_id, state in settings["campaign_states_by_space"].items():
        if grade_id in state.get("stacked_t_by_grade", {}) or any(
                row.get("grade_id") == grade_id
                for row in state.get("resolved_recipe", [])):
            references.add(f"carried blend progress in {space_id}")
    return sorted(references)


def _material_class_from_display(value) -> str:
    displayed = _editor_text(value)
    if not displayed or displayed.lower() == "unspecified":
        return ""
    return displayed.lower().replace("/", " ").replace("-", " ").replace(
        " ", "_")


def _stage_grade_profile_rows(staged, edited: pd.DataFrame) -> list[str]:
    """Stage additions, safe deletions and chemistry edits from the table."""
    errors: list[str] = []
    expected = {grade.id for grade in staged.grades if not getattr(grade, "is_specification", False)}
    display_ids = {
        theme.operator_text(grade_id): grade_id for grade_id in expected
    }
    rows_by_id: dict[str, dict] = {}
    for row in edited.to_dict("records"):
        display_id = _editor_text(row.get(_GRADE_ID_COLUMN))
        grade_id = display_ids.get(display_id, display_id.upper())
        if not display_id:
            errors.append("A grade row is missing its protected Grade ID")
        elif not re.fullmatch(r"[A-Z][A-Z0-9_-]*", grade_id):
            errors.append(
                f"{display_id}: use an uppercase ID containing only letters, "
                "numbers, hyphens or underscores")
        elif grade_id in rows_by_id:
            errors.append(f"{grade_id}: duplicate grade row")
        else:
            rows_by_id[grade_id] = row

    deleted = sorted(expected - set(rows_by_id))
    for grade_id in deleted:
        references = _grade_references(staged, grade_id)
        if references:
            errors.append(
                f"{grade_id} cannot be deleted while referenced by "
                + ", ".join(references[:4])
                + ("…" if len(references) > 4 else ""))
    if errors:
        return errors

    param_by_name = {param.name: param for param in staged.quality_params}
    quality_columns = _quality_column_map(staged)
    for grade_id, row in rows_by_id.items():
        grade = staged.grade(grade_id)
        created = grade is None
        if created:
            material_class = _material_class_from_display(
                row.get(_GRADE_CLASS_COLUMN))
            if material_class not in {"raw_screened", "washed"}:
                errors.append(
                    f"{grade_id}: choose Raw Screened or Washed")
                continue
            grade = Grade(
                id=grade_id,
                name=grade_id,
                material_class=material_class,
                evidence=Evidence(
                    source="Operator-managed grade catalogue",
                    status="ASSUMED",
                    confidence="L",
                    note="Created in Configure > Grade configuration.",
                ),
            )
            if material_class == "washed":
                template = next((
                    item for item in staged.grades
                    if item.material_class == "washed"
                    and item.beneficiation_yield is not None
                ), None)
                if template is not None:
                    grade.source_grade_id = template.source_grade_id
                    grade.beneficiation_yield = template.beneficiation_yield
                    grade.raw_moisture_pct = template.raw_moisture_pct
                    grade.product_moisture_pct = template.product_moisture_pct
            staged.grades.append(grade)
        material_class = _material_class_from_display(
            row.get(_GRADE_CLASS_COLUMN))
        if material_class not in {"raw_screened", "washed"}:
            errors.append(f"{grade_id}: choose Raw Screened or Washed")
            continue
        before_material_class = grade.material_class
        before_quality = dict(grade.quality)

        # Start from the full vector so registered parameters intentionally
        # hidden from this compact table are preserved. A blank visible cell
        # still removes that visible assay value.
        quality = dict(grade.quality)
        for param_name, display_name in quality_columns.items():
            if display_name not in row:
                continue
            parsed = _optional_finite_number(
                row.get(display_name),
                f"{grade.id} · {display_name}",
                errors,
            )
            if parsed is None:
                quality.pop(param_name, None)
                continue
            if parsed < 0:
                errors.append(
                    f"{grade.id} · {display_name} cannot be negative")
                continue
            if param_by_name[param_name].unit == "%" and parsed > 100:
                errors.append(
                    f"{grade.id} · {display_name} cannot exceed 100")
                continue
            quality[param_name] = parsed
        grade.material_class = material_class
        grade.quality = quality
        changed_fields = []
        if created:
            changed_fields.append("created")
        if grade.material_class != before_material_class:
            changed_fields.append("material_class")
        if grade.quality != before_quality:
            changed_fields.append("quality")
        if changed_fields:
            _record_grade_profile_override(
                staged,
                "grade",
                grade.id,
                changed_fields,
                grade.evidence,
            )
    if errors:
        return errors
    if deleted:
        for profile in staged.material_profiles:
            removed = [
                grade_id for grade_id in profile.grade_ids
                if grade_id in deleted
            ]
            if removed:
                profile.grade_ids = [
                    grade_id for grade_id in profile.grade_ids
                    if grade_id not in deleted
                ]
                _record_grade_profile_override(
                    staged,
                    "material_profile",
                    profile.id,
                    ["allowed grades"],
                    profile.evidence,
                )
        staged.grades = [
            grade for grade in staged.grades if grade.id not in deleted
        ]
        for grade_id in deleted:
            _record_grade_profile_override(
                staged, "grade", grade_id, ["deleted"])
    return errors


def _key_value_frame(values: dict[str, str],
                     key_column: str,
                     value_column: str) -> pd.DataFrame:
    return pd.DataFrame(
        [{key_column: key, value_column: value}
         for key, value in values.items()],
        columns=[key_column, value_column],
    )


def _editor_text(value) -> str:
    """Normalize a nullable data-editor cell to trimmed text."""
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _restore_operator_text(value, original) -> str:
    """Keep an internal evidence token when its public text was not edited."""
    displayed = _editor_text(value)
    original_text = str(original)
    if displayed == theme.operator_text(original_text):
        return original_text
    return displayed


def _record_grade_profile_override(staged,
                                   entity_type: str,
                                   entity_id: str,
                                   fields: list[str],
                                   evidence=None):
    """Keep an auditable boundary between document data and operator edits."""
    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "entity_type": entity_type,
        "entity": entity_id,
        "fields": list(fields),
    }
    history = staged.config.get("GRADE_PROFILE_OVERRIDES", [])
    history = list(history) if isinstance(history, list) else []
    history.append(record)
    staged.config["GRADE_PROFILE_OVERRIDES"] = history[-100:]

    if evidence is None:
        return
    prior_source = str(getattr(evidence, "source", "") or "unspecified")
    prior_status = str(getattr(evidence, "status", "") or "unspecified")
    prior_note = str(getattr(evidence, "note", "") or "")
    if not prior_source.startswith("Operator override"):
        prior = f"Previous basis: {prior_status} · {prior_source}."
        if prior_note:
            prior += f" {prior_note}"
    else:
        prior = prior_note
    evidence.source = "Operator override · Configure → Grade configuration"
    evidence.status = "ASSUMED"
    evidence.confidence = "L"
    evidence.note = (
        f"Operator-edited fields: {', '.join(fields)}. {prior}".strip()
    )


def _parse_key_value_rows(edited: pd.DataFrame,
                          key_column: str,
                          value_column: str,
                          *,
                          allowed_keys: set[str] | None = None):
    values: dict[str, str] = {}
    errors: list[str] = []
    for row in edited.to_dict("records"):
        key = _editor_text(row.get(key_column))
        value = _editor_text(row.get(value_column))
        if not key and not value:
            continue
        if not key:
            errors.append(f"{key_column} cannot be blank")
            continue
        if not value:
            errors.append(f"{key}: {value_column.lower()} cannot be blank")
            continue
        if key in values:
            errors.append(f"{key}: duplicate {key_column.lower()}")
            continue
        if allowed_keys is not None and key not in allowed_keys:
            errors.append(f"{key}: parameter is not registered")
            continue
        values[key] = value
    return values, errors


def _stage_material_profile(staged,
                            profile_id: str,
                            *,
                            label: str,
                            grade_ids: list[str],
                            quality_ranges: pd.DataFrame,
                            physical_properties: pd.DataFrame,
                            transformation_note: str) -> list[str]:
    """Apply one material-state profile without exposing structural IDs."""
    errors: list[str] = []
    profile = staged.material_profile(profile_id)
    if profile is None:
        return [f"Unknown material profile: {profile_id}"]
    before = (
        profile.label,
        list(profile.grade_ids),
        dict(profile.quality_ranges),
        dict(profile.physical_properties),
        profile.transformation_note,
    )
    clean_label = _restore_operator_text(label, profile.label)
    if not clean_label:
        errors.append(f"{profile_id}: profile name cannot be blank")

    grade_ids = [_editor_text(grade_id) for grade_id in grade_ids]
    if len(grade_ids) != len(set(grade_ids)):
        errors.append(f"{profile_id}: allowed grades contain duplicates")
    if not grade_ids:
        errors.append(f"{profile_id}: select at least one allowed grade")
    for grade_id in grade_ids:
        grade = staged.grade(grade_id)
        if grade is None:
            errors.append(f"{profile_id}: unknown grade {grade_id}")
        elif grade.material_class and \
                grade.material_class != profile.material_class:
            errors.append(
                f"{profile_id}: {grade_id} is {grade.material_class}, not "
                f"{profile.material_class}")

    ranges, range_errors = _parse_key_value_rows(
        quality_ranges,
        "Parameter",
        "Documented range",
        allowed_keys={param.name for param in staged.quality_params},
    )
    properties, property_errors = _parse_key_value_rows(
        physical_properties,
        "Property",
        "Documented value",
    )
    errors.extend(range_errors)
    errors.extend(property_errors)
    if errors:
        return errors
    ranges = {
        key: (
            _restore_operator_text(value, profile.quality_ranges[key])
            if key in profile.quality_ranges else value
        )
        for key, value in ranges.items()
    }
    properties = {
        key: (
            _restore_operator_text(value, profile.physical_properties[key])
            if key in profile.physical_properties else value
        )
        for key, value in properties.items()
    }

    profile.label = clean_label
    profile.grade_ids = grade_ids
    profile.quality_ranges = ranges
    profile.physical_properties = properties
    profile.transformation_note = _restore_operator_text(
        transformation_note, profile.transformation_note)
    after = (
        profile.label,
        list(profile.grade_ids),
        dict(profile.quality_ranges),
        dict(profile.physical_properties),
        profile.transformation_note,
    )
    if after != before:
        field_names = (
            "label",
            "allowed grades",
            "quality ranges",
            "physical properties",
            "transformation note",
        )
        changed_fields = [
            field_name
            for field_name, old_value, new_value
            in zip(field_names, before, after)
            if old_value != new_value
        ]
        _record_grade_profile_override(
            staged,
            "material_profile",
            profile.id,
            changed_fields,
            profile.evidence,
        )
    return []


def _quality_parameter_frame(scn) -> pd.DataFrame:
    return pd.DataFrame([{
        "Parameter": param.name,
        "Unit": param.unit,
        "Description": param.description,
    } for param in scn.quality_params])


def _stage_quality_parameters(staged, edited: pd.DataFrame) -> list[str]:
    """Update units/descriptions while keeping referenced parameter IDs fixed."""
    errors: list[str] = []
    expected = {param.name for param in staged.quality_params}
    rows_by_name: dict[str, dict] = {}
    for row in edited.to_dict("records"):
        name = str(row.get("Parameter", "")).strip()
        if not name:
            errors.append("A parameter row is missing its protected name")
        elif name in rows_by_name:
            errors.append(f"{name}: duplicate parameter row")
        else:
            rows_by_name[name] = row
    if expected - set(rows_by_name):
        errors.append(
            "Missing parameter rows: "
            + ", ".join(sorted(expected - set(rows_by_name))))
    if set(rows_by_name) - expected:
        errors.append(
            "Unknown parameter rows: "
            + ", ".join(sorted(set(rows_by_name) - expected)))
    if errors:
        return errors
    changed: list[str] = []
    for param in staged.quality_params:
        row = rows_by_name[param.name]
        unit = _editor_text(row.get("Unit"))
        if not unit:
            errors.append(f"{param.name}: unit cannot be blank")
            continue
        if unit != param.unit:
            errors.append(
                f"{param.name}: unit is protected; convert the underlying "
                "grade and limit values before changing units")
            continue
        description = _restore_operator_text(
            row.get("Description"), param.description)
        if description != param.description:
            changed.append(param.name)
        param.description = description
    if not errors and changed:
        _record_grade_profile_override(
            staged,
            "quality_parameter_register",
            ", ".join(changed),
            ["description"],
        )
    return errors


def _spec_frame(destination, scn=None) -> pd.DataFrame:
    unit_by_param = {
        param.name: param.unit for param in scn.quality_params
    } if scn is not None else {}
    return pd.DataFrame([{
        "Parameter": spec.param,
        "Unit": unit_by_param.get(spec.param, ""),
        "Min": spec.pmin,
        "Max": spec.pmax,
        "Target": spec.target,
        "Hard limit": spec.hard,
        "Averaging [h]": spec.averaging_h,
        "Margin": spec.margin,
    } for spec in destination.spec], columns=[
        "Parameter", "Unit", "Min", "Max", "Target", "Hard limit",
        "Averaging [h]", "Margin",
    ])


def _train_export_spec_frame(scn) -> pd.DataFrame:
    """Return the train contracts in the standard one-grade-per-row shape."""
    columns = [
        "Grade ID", "Material state", "BPL [%]", "Cd [ppm]", "MgO [%]",
        "SiO2 [%]", "SiO2_R [%]", "F [%]", "P2O5_SC [%]", "CO2 [%]",
        "moisture [%]", "P2O5 [%]", "Destination", "Evidence",
    ]
    column_by_source_characteristic = {
        "BPL": "BPL [%]",
        "Cd": "Cd [ppm]",
        "MgO": "MgO [%]",
        "SiO2": "SiO2 [%]",
        "SiO2 T": "SiO2 [%]",
        "SiO2 R": "SiO2_R [%]",
        "F": "F [%]",
        "P2O5 S,C": "P2O5_SC [%]",
        "CO2": "CO2 [%]",
        "H2O": "moisture [%]",
        "P2O5": "P2O5 [%]",
    }
    rows = []
    for grade in scn.grades:
        if not getattr(grade, "is_specification", False) or \
                "BG-TRAIN-PROD" not in getattr(
                    grade, "receiving_destinations", []):
            continue
        row = {column: "" for column in columns}
        row.update({
            "Grade ID": grade.id,
            "Material state": "Receiving specification",
            "Destination": ", ".join(grade.receiving_destinations),
            "Evidence": grade.evidence.status,
        })
        for source_parameter, source_range in \
                grade.source_quality_ranges.items():
            column = column_by_source_characteristic.get(source_parameter)
            if column is not None:
                # Units live in the column headings, matching the chemical
                # grade table. Keep the source inequality/range notation.
                row[column] = re.sub(
                    r"\s*(?:%|ppm)\s*$", "", str(source_range)).strip()
        rows.append(row)
    return pd.DataFrame(rows, columns=columns)


def _stage_destination_specs(staged,
                             destination_id: str,
                             edited: pd.DataFrame) -> list[str]:
    """Parse and validate receiving limits before changing the staged model."""
    errors: list[str] = []
    registered = {param.name for param in staged.quality_params}
    unit_by_param = {
        param.name: param.unit for param in staged.quality_params
    }
    specs: list[SpecRange] = []
    destination = staged.destination(destination_id)
    before = list(destination.spec)
    seen: set[str] = set()
    for row in edited.to_dict("records"):
        param = _editor_text(row.get("Parameter"))
        numeric_values = [
            row.get("Min"), row.get("Max"), row.get("Target"),
            row.get("Averaging [h]"), row.get("Margin"),
        ]
        if not param and all(
                value is None or pd.isna(value) for value in numeric_values):
            continue
        if not param:
            errors.append("A receiving-limit row is missing its parameter")
            continue
        if param not in registered:
            errors.append(f"{param}: parameter is not registered")
            continue
        if param in seen:
            errors.append(f"{param}: duplicate receiving limit")
            continue
        seen.add(param)
        pmin = _optional_finite_number(
            row.get("Min"), f"{param} minimum", errors)
        pmax = _optional_finite_number(
            row.get("Max"), f"{param} maximum", errors)
        target = _optional_finite_number(
            row.get("Target"), f"{param} target", errors)
        averaging = _optional_finite_number(
            row.get("Averaging [h]"), f"{param} averaging window", errors)
        margin = _optional_finite_number(
            row.get("Margin"), f"{param} margin", errors)
        averaging = 1.0 if averaging is None else averaging
        margin = 0.0 if margin is None else margin
        if pmin is None and pmax is None and target is None:
            errors.append(
                f"{param}: enter at least a minimum, maximum or target")
        for value_name, value in (
                ("minimum", pmin), ("maximum", pmax), ("target", target)):
            if value is not None and value < 0:
                errors.append(f"{param}: {value_name} cannot be negative")
            if value is not None and unit_by_param[param] == "%" and \
                    value > 100:
                errors.append(
                    f"{param}: {value_name} cannot exceed 100%")
        if pmin is not None and pmax is not None and pmin > pmax:
            errors.append(f"{param}: minimum cannot exceed maximum")
        if target is not None and pmin is not None and target < pmin:
            errors.append(f"{param}: target is below the minimum")
        if target is not None and pmax is not None and target > pmax:
            errors.append(f"{param}: target is above the maximum")
        if averaging <= 0:
            errors.append(f"{param}: averaging window must be greater than 0")
        if margin < 0:
            errors.append(f"{param}: margin cannot be negative")
        specs.append(SpecRange(
            param=param,
            pmin=pmin,
            pmax=pmax,
            target=target,
            hard=bool(row.get("Hard limit", False)),
            averaging_h=averaging,
            margin=margin,
        ))
    if errors:
        return errors
    destination.spec = specs
    if destination.spec != before:
        _record_grade_profile_override(
            staged,
            "receiving_limits",
            destination_id,
            ["spec"],
        )
    return []


def _new_validation_errors(original, staged):
    """Return only ERROR issues introduced by a staged configuration edit."""
    from mph_sim.validation import validate_scenario

    before = {
        (issue.code, issue.entity, issue.message)
        for issue in validate_scenario(original)
        if issue.severity == "ERROR"
    }
    return [
        issue for issue in validate_scenario(staged)
        if issue.severity == "ERROR"
        and (issue.code, issue.entity, issue.message) not in before
    ]


def _commit_grade_profile_change(original, staged, message: str) -> bool:
    """Atomically commit one grade-page edit and invalidate derived outputs."""
    if staged == original:
        st.info("No grade-profile changes to apply.")
        return False
    introduced = _new_validation_errors(original, staged)
    if introduced:
        st.error(
            "Nothing was applied. "
            + " · ".join(issue.message for issue in introduced[:5]))
        return False
    before_audit = original.config.get("GRADE_PROFILE_OVERRIDES", [])
    after_audit = staged.config.get("GRADE_PROFILE_OVERRIDES", [])
    before_audit = before_audit if isinstance(before_audit, list) else []
    after_audit = after_audit if isinstance(after_audit, list) else []
    new_audit = [record for record in after_audit
                 if record not in before_audit]
    try:
        catalog_path = save_grade_catalog(staged)
    except GradeCatalogError as exc:
        st.error(
            "Nothing was applied because the permanent grade catalogue "
            f"could not be saved: {exc}")
        return False
    st.session_state["scenario"] = staged
    st.session_state["grade_catalog_error"] = ""
    if new_audit:
        overrides = list(st.session_state.get("overrides_log", []))
        overrides.extend({
            "action": "update grade configuration",
            **record,
        } for record in new_audit)
        st.session_state["overrides_log"] = overrides[-100:]
    stale = common.invalidate_outputs()
    common.revalidate()
    st.session_state["grade_profiles_flash"] = (
        message + f" · saved permanently to {catalog_path}"
        + (" · previous plan cleared" if stale else ""))
    st.session_state["grade_profiles_nonce"] = (
        int(st.session_state.get("grade_profiles_nonce", 0)) + 1)
    st.rerun()
    return True


def page_configure():
    """Single operator-facing configuration workspace.

    Rare engineering topology work stays behind the advanced diagram. The
    default surface exposes only inputs that normally change a planning run.
    """
    from .flow_configuration import render_flow_availability_configuration

    st.subheader("Operational configuration")
    st.caption(
        "Set the equipment and starting-stock basis for the next plan. "
        "Changes are staged and applied together; an existing plan is cleared "
        "only when a value actually changes.")
    st.caption(
        "Grade chemistry, yield and moisture, material profiles, and "
        "receiving limits are together under **Configure → Grade "
        "configuration**.")
    scn = common.sc()
    sites = _configure_sites(scn)
    if not sites:
        st.info("This scenario has no configurable operational sites.")
        return

    flash = st.session_state.pop("operational_config_flash", "")
    if flash:
        st.success(flash)

    from .scheduling_configuration import render_scheduling_configuration
    render_scheduling_configuration(scn)

    site_id = st.radio(
        "Site",
        options=sites,
        format_func=lambda value: _CONFIGURE_SITE_LABELS.get(value, value),
        horizontal=True,
        key="operational_config_site",
    )
    process_lines = _process_lines_for_site(scn, site_id)
    machines = _planning_machines_for_site(scn, site_id)
    stocks = _physical_stock_for_site(scn, site_id)
    machine_by_id = {machine.id: machine for machine in machines}
    stock_by_id = {stock.id: stock for stock in stocks}
    nonce = int(st.session_state.get("operational_config_nonce", 0))

    with st.form(f"operational_config_form_{site_id}_{nonce}"):
        line_col, machine_col = st.columns([0.85, 1.35])
        desired_process_lines: dict[str, str] = {}
        desired_destination_lines: dict[str, str] = {}

        with line_col:
            st.markdown("#### Line status")
            if process_lines:
                for line_id in process_lines:
                    current = flow_model.process_line_operating_state(
                        scn, line_id)
                    desired_process_lines[line_id] = st.selectbox(
                        _PROCESS_LINE_LABELS.get(line_id, line_id),
                        options=["Available", "Disabled", "Future"],
                        index={"AVAILABLE": 0, "DISABLED": 1, "FUTURE": 2}[
                            current],
                        key=f"operational_line_{site_id}_{line_id}_{nonce}",
                        help=(
                            f"{line_id} · Disabled is an existing line taken "
                            "out of service; Future is not yet active. Both "
                            "retain configured demand."
                        ),
                    )

            if site_id == "MPH":
                destination_ids = {
                    destination.id for destination in scn.destinations
                }
                for destination_id in _MPH_DESTINATION_LINES:
                    if destination_id not in destination_ids:
                        continue
                    current = flow_model.destination_line_operating_state(
                        scn, destination_id)
                    desired_destination_lines[destination_id] = st.selectbox(
                        destination_id,
                        options=["Available", "Disabled", "Future"],
                        index={"AVAILABLE": 0, "DISABLED": 1, "FUTURE": 2}[
                            current],
                        key=(
                            f"operational_destination_{destination_id}_"
                            f"{nonce}"
                        ),
                        help=(
                            "Disabled keeps demand visible as unmet if the "
                            "line is required. Future retains its demand "
                            "template but excludes it from active planning."
                        ),
                    )
                    if destination_id == "PAP-2":
                        st.caption(
                            "36,000 t / 24 h design demand · same as PAP-1")
                        if desired_destination_lines[destination_id] == \
                                "Available":
                            st.warning(
                                "At full templates, PAP-1 + PAP-2 + DRY-FEED "
                                "request 91,200 t/day against the current "
                                "72,000 t/day M16 outbound envelope. Reduce a "
                                "demand or accept unmet demand.")
            if not process_lines and not desired_destination_lines:
                st.caption("No configurable lines at this site.")

        with machine_col:
            st.markdown("#### Machine availability")
            if machines:
                machine_frame = pd.DataFrame([{
                    "Machine": machine.id,
                    "Role": str(machine.kind).title(),
                    "Available": bool(machine.available),
                } for machine in machines])
                edited_machines = st.data_editor(
                    machine_frame,
                    key=f"operational_machine_editor_{site_id}_{nonce}",
                    hide_index=True,
                    width="stretch",
                    disabled=["Machine", "Role"],
                    column_config={
                        "Available": st.column_config.CheckboxColumn(
                            "Available",
                            help="Available machines may receive jobs.",
                        ),
                    },
                )
            else:
                edited_machines = pd.DataFrame(
                    columns=["Machine", "Role", "Available"])
                st.caption("No planner-backed machines at this site.")

        st.markdown("#### Starting stock")
        st.caption(
            "Physical stockpiles only. Mine hoppers and process WIP buffers "
            "are deliberately excluded from this quick planning basis.")
        if stocks:
            stock_frame = pd.DataFrame([{
                "Stockpile": stock.id,
                "Grade": theme.operator_text(stock.grade),
                "Starting stock [t]": float(stock.mass_t),
                "Capacity [t]": float(stock.capacity_t),
                "Fill [%]": (
                    100.0 * float(stock.mass_t) / float(stock.capacity_t)
                    if stock.capacity_t else 0.0
                ),
            } for stock in stocks])
            edited_stocks = st.data_editor(
                stock_frame,
                key=f"operational_stock_editor_{site_id}_{nonce}",
                hide_index=True,
                width="stretch",
                disabled=["Stockpile", "Grade", "Capacity [t]", "Fill [%]"],
                column_config={
                    "Starting stock [t]": st.column_config.NumberColumn(
                        "Starting stock [t]",
                        min_value=0.0,
                        step=500.0,
                        format="%.0f",
                    ),
                    "Capacity [t]": st.column_config.NumberColumn(
                        "Capacity [t]", format="%.0f"),
                    "Fill [%]": st.column_config.NumberColumn(
                        "Fill [%]", format="%.1f"),
                },
            )
        else:
            edited_stocks = pd.DataFrame(
                columns=["Stockpile", "Starting stock [t]"])
            st.caption("No physical stockpiles at this site.")

        submitted = st.form_submit_button(
            f"Apply {_CONFIGURE_SITE_LABELS.get(site_id, site_id)} "
            "configuration",
            type="primary",
            width="stretch",
            disabled=bool(st.session_state.get("solving", False)),
        )

    if submitted:
        masses, stock_errors = _validate_stock_editor(
            edited_stocks, stock_by_id) if stocks else ({}, [])
        machine_rows = {
            str(row["Machine"]): bool(row["Available"])
            for row in edited_machines.to_dict("records")
            if str(row.get("Machine", "")) in machine_by_id
        }
        missing_machines = set(machine_by_id) - set(machine_rows)
        if missing_machines:
            stock_errors.append(
                "Missing machine rows: " + ", ".join(sorted(missing_machines)))

        if stock_errors:
            st.error("Nothing was applied. " + " · ".join(stock_errors))
        else:
            staged = deepcopy(scn)
            changed = False
            try:
                for line_id, desired in desired_process_lines.items():
                    desired_state = str(desired).strip().upper()
                    current_state = flow_model.process_line_operating_state(
                        staged, line_id)
                    if current_state != desired_state:
                        flow_model.set_process_line_operating_state(
                            staged, line_id, desired_state)
                        changed = True

                for destination_id, desired in (
                        desired_destination_lines.items()):
                    desired_state = str(desired).strip().upper()
                    current_state = (
                        flow_model.destination_line_operating_state(
                            staged, destination_id))
                    if desired_state != current_state:
                        flow_model.set_destination_line_operating_state(
                            staged, destination_id, desired_state)
                        changed = True

                for machine_id, available in machine_rows.items():
                    machine = staged.machine(machine_id)
                    if bool(machine.available) != available:
                        machine.available = available
                        changed = True

                for stock_id, mass_t in masses.items():
                    stock = staged.space(stock_id)
                    if not math.isclose(
                            float(stock.mass_t), mass_t,
                            rel_tol=0.0, abs_tol=1e-6):
                        stock.mass_t = mass_t
                        changed = True
            except (KeyError, ValueError) as exc:
                st.error(f"Nothing was applied. {exc}")
            else:
                if changed:
                    st.session_state["scenario"] = staged
                    stale = common.invalidate_outputs()
                    common.revalidate()
                    message = (
                        f"{_CONFIGURE_SITE_LABELS.get(site_id, site_id)} "
                        "configuration applied"
                        + (" · previous plan cleared" if stale else "")
                    )
                else:
                    message = "No configuration changes to apply."
                st.session_state["operational_config_flash"] = message
                st.session_state["operational_config_nonce"] = nonce + 1
                st.rerun()

    # The former Stockyards route now opens this configuration workspace.
    # Keep its timestamped inventory preview/apply flow reachable alongside
    # manual starting-stock edits, outside their separate submit form.
    _render_inventory_snapshot_panel(scn)

    with st.expander("Advanced · interactive route diagram", expanded=False):
        st.caption(
            "Use this only when an individual conveyor route must be changed. "
            "Reference and future equipment remain read-only.")
        if st.toggle(
                "Load route diagram",
                value=False,
                key=f"operational_load_route_diagram_{site_id}"):
            render_flow_availability_configuration(scn)


def _render_grade_yield_moisture_editor(scn, nonce: int):
    """Render the mass-conversion editor inside Grade configuration."""
    st.markdown("#### Beneficiation yield & moisture")
    st.caption(
        "Washed grades are grouped by their protected raw-feed Cd family. "
        "Set the conversion basis for each merchant-grade variant here. "
        "These values drive Benguerir beneficiation; Louta retains its "
        "separate documented washing cases.")
    st.caption(
        "The visible defaults are editable scenario assumptions: 12% raw "
        "moisture is the midpoint of the documented 10–14% range, and 17.5% "
        "wet-product moisture is the midpoint of the documented 15–20% "
        "range. They are not measured live values.")
    st.info(
        "Planning and equipment rates remain in physical/as-handled tonnes. "
        "Beneficiation yield reduces dry product mass; moisture relates each "
        "physical stage to its TSM equivalent. After drying, 1 physical dry "
        "tonne = 1 TSM.")
    frame = _grade_yield_moisture_frame(scn)
    if frame.empty:
        st.info("This scenario has no merchant grades to configure.")
        return

    st.caption(
        "Derived checks: raw → TSM factor = (1 − raw moisture) × yield; "
        "wet product t/TSM accounts for retained product water.")
    with st.form(f"grade_yield_moisture_form_{nonce}"):
        edited = st.data_editor(
            frame,
            key=f"grade_yield_moisture_editor_{nonce}",
            hide_index=True,
            width="stretch",
            height=min(440, 72 + 36 * max(len(frame), 1)),
            disabled=[
                _GRADE_ID_COLUMN,
                _GRADE_NAME_COLUMN,
                _GRADE_SOURCE_COLUMN,
                _RAW_TO_TSM_COLUMN,
                _WET_PER_TSM_COLUMN,
            ],
            column_config={
                _GRADE_ID_COLUMN: st.column_config.TextColumn(
                    _GRADE_ID_COLUMN,
                    help="Shared merchant-grade ID; protected."),
                _GRADE_NAME_COLUMN: st.column_config.TextColumn(
                    _GRADE_NAME_COLUMN),
                _GRADE_SOURCE_COLUMN: st.column_config.TextColumn(
                    _GRADE_SOURCE_COLUMN,
                    help=(
                        "Protected lineage: raw/screened feed grade whose "
                        "Cd profile is carried into this washed variant."
                    ),
                ),
                _GRADE_YIELD_COLUMN: st.column_config.NumberColumn(
                    _GRADE_YIELD_COLUMN,
                    min_value=0.01,
                    max_value=100.0,
                    step=0.01,
                    format="%.2f",
                    help=(
                        "TSM produced per tonne of dry solids entering "
                        "beneficiation."
                    ),
                ),
                _RAW_MOISTURE_COLUMN: st.column_config.NumberColumn(
                    _RAW_MOISTURE_COLUMN,
                    min_value=0.0,
                    max_value=99.99,
                    step=0.1,
                    format="%.1f",
                    help="Water share of raw physical feed, on a wet basis.",
                ),
                _PRODUCT_MOISTURE_COLUMN: st.column_config.NumberColumn(
                    _PRODUCT_MOISTURE_COLUMN,
                    min_value=0.0,
                    max_value=99.99,
                    step=0.1,
                    format="%.1f",
                    help=(
                        "Water share of washed physical product before "
                        "drying, on a wet basis."
                    ),
                ),
                _RAW_TO_TSM_COLUMN: st.column_config.NumberColumn(
                    _RAW_TO_TSM_COLUMN,
                    format="%.2f",
                    help="Expected TSM from 100 t of raw physical feed.",
                ),
                _WET_PER_TSM_COLUMN: st.column_config.NumberColumn(
                    _WET_PER_TSM_COLUMN,
                    format="%.3f",
                    help="Wet physical product tonnes containing 1 TSM.",
                ),
            },
        )
        apply_values = st.form_submit_button(
            "Apply yield & moisture",
            type="primary",
            disabled=bool(st.session_state.get("solving", False)),
        )

    if apply_values:
        staged = deepcopy(scn)
        errors = _stage_grade_yield_moisture_rows(staged, edited)
        if errors:
            st.error("Nothing was applied. " + " · ".join(errors[:8]))
        else:
            _commit_grade_profile_change(
                scn, staged, "Grade yield and moisture applied")


def page_washing_treatments_configuration():
    """Dedicated Configure workspace for washing options and PAP G10."""
    st.subheader("Washing treatments")
    st.caption(
        "Enable the washing model and edit the Simple washing and Full "
        "washing assumptions used by the next generated plan.")
    flash = st.session_state.pop("washing_configuration_flash", "")
    if flash:
        st.success(flash)
    from .washing_configuration import render_washing_configuration
    render_washing_configuration(common.sc())


def page_grade_profiles():
    """Single Configure workspace for grade data and mass conversion."""
    st.subheader("Grade configuration")
    st.caption(
        "Configure shared chemistry, beneficiation mass conversion, material "
        "states and receiving limits in one place. The same grade catalogue "
        "is used at Louta, Benguerir and MPH; changes apply to the next "
        "generated plan.")
    scn = common.sc()
    nonce = int(st.session_state.get("grade_profiles_nonce", 0))
    flash = st.session_state.pop("grade_profiles_flash", "")
    if flash:
        st.success(flash)
    catalog_error = st.session_state.get("grade_catalog_error", "")
    if catalog_error:
        st.error(f"Persistent grade catalogue could not be loaded: {catalog_error}")

    grade_tab, yield_tab, profile_tab, export_tab, limit_tab = st.tabs([
        "Chemical grades",
        "Yield & moisture",
        "Material profiles",
        "Train export grades",
        "Receiving limits",
    ])

    with grade_tab:
        st.markdown("#### Chemical grade vectors")
        st.caption(
            "Blank means no assay value is configured. Stockpile-specific "
            "assays may override these shared grade vectors.")
        st.caption(
            "The moisture entry in this table is a quality/delivery value. "
            "Use the **Yield & moisture** tab for stage mass conversion. "
            "Raw/screened feed grades are listed first, followed by the "
            "washed-product variants.")
        st.caption(
            "Use the table's row controls to add or delete grades. Changes "
            f"are saved permanently in `{DEFAULT_GRADE_CATALOG_PATH}`. A "
            "grade that is still used by the model must be reassigned before "
            "it can be deleted.")
        grade_frame = _grade_profile_frame(scn)
        grade_frame = grade_frame.drop(columns=[
            _GRADE_NAME_COLUMN,
            _GRADE_SOURCE_COLUMN,
            _GRADE_BASIS_COLUMN,
            _GRADE_USAGE_COLUMN,
            _GRADE_OVERRIDE_COLUMN,
            *[
                display_name
                for param_name, display_name in _quality_column_map(scn).items()
                if param_name in _HIDDEN_CHEMISTRY_PARAMETERS
            ],
        ], errors="ignore")
        quality_columns = {
            param_name: display_name
            for param_name, display_name in _quality_column_map(scn).items()
            if param_name not in _HIDDEN_CHEMISTRY_PARAMETERS
        }
        grade_column_config = {
            _GRADE_ID_COLUMN: st.column_config.TextColumn(
                _GRADE_ID_COLUMN,
                required=True,
                help=(
                    "Permanent model ID. Use uppercase letters, numbers, "
                    "hyphens or underscores. Referenced IDs cannot be "
                    "renamed or deleted."
                ),
            ),
            _GRADE_CLASS_COLUMN: st.column_config.SelectboxColumn(
                _GRADE_CLASS_COLUMN,
                options=["Raw Screened", "Washed"],
                required=True,
            ),
        }
        for param_name, display_name in quality_columns.items():
            param = next(
                item for item in scn.quality_params
                if item.name == param_name)
            grade_column_config[display_name] = \
                st.column_config.NumberColumn(
                    display_name,
                    min_value=0.0,
                    max_value=100.0 if param.unit == "%" else None,
                    format="%.3f",
                    help=param.description or None,
                )

        with st.form(f"grade_vectors_form_{nonce}"):
            edited_grades = st.data_editor(
                theme.operator_frame(grade_frame),
                key=f"grade_vectors_editor_{nonce}",
                hide_index=True,
                width="stretch",
                height=min(440, 72 + 36 * max(len(grade_frame), 1)),
                num_rows="dynamic",
                column_config=grade_column_config,
            )
            apply_grades = st.form_submit_button(
                "Apply chemical grade values",
                type="primary",
                disabled=bool(st.session_state.get("solving", False)),
            )
        if apply_grades:
            staged = deepcopy(scn)
            errors = _stage_grade_profile_rows(staged, edited_grades)
            if errors:
                st.error("Nothing was applied. " + " · ".join(errors[:8]))
            else:
                _commit_grade_profile_change(
                    scn, staged, "Chemical grade values applied")

    with yield_tab:
        _render_grade_yield_moisture_editor(scn, nonce)

    with profile_tab:
        st.markdown("#### Material state profiles")
        st.caption(
            "Profiles describe where material is in the process, which grades "
            "are allowed, and the documented ranges behind that state. The "
            "numeric planning vector remains on the chemical grade.")
        profile_ids = [profile.id for profile in scn.material_profiles]
        if not profile_ids:
            st.info("No material profiles are configured.")
        else:
            if st.session_state.get("material_profile_selector") not in \
                    profile_ids:
                st.session_state["material_profile_selector"] = profile_ids[0]
            profile_id = st.selectbox(
                "Material profile",
                options=profile_ids,
                format_func=lambda value: theme.operator_text(
                    f"{value} — {scn.material_profile(value).label}"),
                key="material_profile_selector",
            )
            profile = scn.material_profile(profile_id)
            st.caption(
                f"{profile.site} · {profile.stage} · "
                f"{profile.material_class.replace('_', ' ')}")
            compatible_grades = [
                grade.id for grade in scn.grades
                if not grade.material_class
                or grade.material_class == profile.material_class
            ]
            with st.form(f"material_profile_form_{profile_id}_{nonce}"):
                profile_label = st.text_input(
                    "Profile name", value=theme.operator_text(profile.label))
                allowed_grades = st.multiselect(
                    "Allowed grades",
                    options=compatible_grades,
                    default=[
                        grade_id for grade_id in profile.grade_ids
                        if grade_id in compatible_grades
                    ],
                    help=(
                        "Only grades with the same material state can be "
                        "selected."
                    ),
                    format_func=theme.operator_text,
                )
                range_col, property_col = st.columns(2)
                with range_col:
                    st.markdown("**Documented quality ranges**")
                    range_editor = st.data_editor(
                        _key_value_frame(
                            {
                                key: theme.operator_text(value)
                                for key, value
                                in profile.quality_ranges.items()
                            },
                            "Parameter",
                            "Documented range",
                        ),
                        key=f"profile_ranges_{profile_id}_{nonce}",
                        hide_index=True,
                        width="stretch",
                        num_rows="dynamic",
                        column_config={
                            "Parameter": st.column_config.SelectboxColumn(
                                "Parameter",
                                options=[
                                    param.name
                                    for param in scn.quality_params
                                ],
                            ),
                        },
                    )
                with property_col:
                    st.markdown("**Physical properties**")
                    property_editor = st.data_editor(
                        _key_value_frame(
                            {
                                key: theme.operator_text(value)
                                for key, value
                                in profile.physical_properties.items()
                            },
                            "Property",
                            "Documented value",
                        ),
                        key=f"profile_properties_{profile_id}_{nonce}",
                        hide_index=True,
                        width="stretch",
                        num_rows="dynamic",
                    )
                profile_note = st.text_area(
                    "Transformation / use note",
                    value=theme.operator_text(profile.transformation_note),
                )
                apply_profile = st.form_submit_button(
                    "Apply material profile",
                    type="primary",
                    disabled=bool(st.session_state.get("solving", False)),
                )
            if apply_profile:
                staged = deepcopy(scn)
                errors = _stage_material_profile(
                    staged,
                    profile_id,
                    label=profile_label,
                    grade_ids=allowed_grades,
                    quality_ranges=range_editor,
                    physical_properties=property_editor,
                    transformation_note=profile_note,
                )
                if errors:
                    st.error(
                        "Nothing was applied. " + " · ".join(errors[:8]))
                else:
                    _commit_grade_profile_change(
                        scn, staged, f"{profile_id} material profile applied")

    with export_tab:
        st.markdown("#### Train export receiving grades")
        st.caption(
            "Select these grade profiles on Configure → Weekly demand for "
            "BG-TRAIN-PROD. They are hard receiving specifications, not "
            "physical stock labels; the optimizer may blend any assayed "
            "washed grades that satisfy the selected interval.")
        export_frame = _train_export_spec_frame(scn)
        if export_frame.empty:
            st.info("No train export receiving grades are configured.")
        else:
            st.dataframe(
                theme.operator_frame(export_frame),
                hide_index=True,
                width="stretch",
            )
            st.caption(
                "P2O5 on BG-22/BG10 is retained as source metadata because "
                "BPL is the active equivalent phosphate constraint. Source "
                "'<’ bounds are preserved verbatim and currently use the "
                "displayed inclusive numeric maximum. BG11 and BG AD require "
                "their distinct SiO2_R, F and P2O5_SC assays; unassayed "
                "material is held rather than treated as zero.")

    with limit_tab:
        st.markdown("#### Receiving quality limits")
        st.caption(
            "These are acceptance limits, not assay values. Hard limits must "
            "be met; soft limits may use planned deviation. The current MVP "
            "checks each planning bucket even when an averaging window is "
            "recorded.")
        destination_ids = [
            destination.id for destination in scn.destinations
        ]
        if not destination_ids:
            st.info("No receiving units are configured.")
        else:
            if st.session_state.get("grade_limit_destination") not in \
                    destination_ids:
                st.session_state["grade_limit_destination"] = \
                    destination_ids[0]
            destination_id = st.selectbox(
                "Receiving unit",
                destination_ids,
                key="grade_limit_destination",
            )
            destination = scn.destination(destination_id)
            requested = destination.requested_grade or "any configured grade"
            st.caption(
                f"Requested material: {requested} · "
                f"{destination.demand_kind.replace('_', ' ')}")
            with st.form(
                    f"destination_limits_form_{destination_id}_{nonce}"):
                spec_editor = st.data_editor(
                    _spec_frame(destination, scn),
                    key=f"destination_limits_{destination_id}_{nonce}",
                    hide_index=True,
                    width="stretch",
                    num_rows="dynamic",
                    column_config={
                        "Parameter": st.column_config.SelectboxColumn(
                            "Parameter",
                            options=[
                                param.name for param in scn.quality_params
                            ],
                        ),
                        "Unit": st.column_config.TextColumn(
                            "Unit",
                            help="Defined by the protected parameter register.",
                        ),
                        "Min": st.column_config.NumberColumn(
                            "Min", min_value=0.0, format="%.3f"),
                        "Max": st.column_config.NumberColumn(
                            "Max", min_value=0.0, format="%.3f"),
                        "Target": st.column_config.NumberColumn(
                            "Target", min_value=0.0, format="%.3f"),
                        "Hard limit": st.column_config.CheckboxColumn(
                            "Hard limit",
                            help="Off means a soft, penalised deviation.",
                        ),
                        "Averaging [h]": st.column_config.NumberColumn(
                            "Averaging [h]", min_value=0.01, format="%.2f"),
                        "Margin": st.column_config.NumberColumn(
                            "Margin", min_value=0.0, format="%.3f"),
                    },
                    disabled=["Unit"],
                )
                apply_limits = st.form_submit_button(
                    "Apply receiving limits",
                    type="primary",
                    disabled=bool(st.session_state.get("solving", False)),
                )
            if apply_limits:
                staged = deepcopy(scn)
                errors = _stage_destination_specs(
                    staged, destination_id, spec_editor)
                if errors:
                    st.error(
                        "Nothing was applied. " + " · ".join(errors[:8]))
                else:
                    _commit_grade_profile_change(
                        scn,
                        staged,
                        f"{destination_id} receiving limits applied",
                    )

    with st.expander(
            "Advanced · quality parameter register", expanded=False):
        st.markdown("#### Quality parameter register")
        st.caption(
            "Parameter names are protected because grade vectors and receiving "
            "limits refer to them. Units are also protected because changing a "
            "unit requires conversion of every stored value.")
        with st.form(f"quality_parameter_form_{nonce}"):
            parameter_editor = st.data_editor(
                theme.operator_frame(_quality_parameter_frame(scn)),
                key=f"quality_parameter_editor_{nonce}",
                hide_index=True,
                width="stretch",
                disabled=["Parameter", "Unit"],
            )
            apply_parameters = st.form_submit_button(
                "Apply parameter details",
                type="primary",
                disabled=bool(st.session_state.get("solving", False)),
            )
        if apply_parameters:
            staged = deepcopy(scn)
            errors = _stage_quality_parameters(staged, parameter_editor)
            if errors:
                st.error("Nothing was applied. " + " · ".join(errors[:8]))
            else:
                _commit_grade_profile_change(
                    scn, staged, "Quality parameter details applied")


# =========================================================== 2. Input Data
def page_input_data():
    st.subheader("Grades, Quality & Model Inputs")
    st.caption(
        "Every table carries **value · unit · source · evidence status · "
        "confidence**. Working assumptions and open inputs are demonstration "
        "values; edit or replace them before operational use.")
    scn = common.sc()

    tables = scenario_tables(scn)
    st.markdown("#### Grade and quality characteristics")
    st.caption(
        "Grades, mine/washed material profiles and registered quality "
        "parameters are configured here. They are intentionally excluded "
        "from the Plan section.")
    order = [
        "grades", "quality_params", "material_profiles",
        "sites", "spaces", "segments", "machines", "links", "nodes",
        "destinations", "demand", "calendars", "exogenous",
        "belt_profile", "fixed_jobs", "assumptions",
    ]
    tabs = st.tabs([t.replace("_", " ") for t in order])

    for tab, name in zip(tabs, order):
        with tab:
            df = tables[name]
            unit_note = ", ".join(f"`{c}` [{u}]" for c, u in UNITS.items()
                                  if c in getattr(df, "columns", []))
            if unit_note:
                st.caption("Units: " + unit_note)
            if df.empty:
                st.info("no rows")
                continue
            edited = st.data_editor(
                theme.operator_frame(df), num_rows="dynamic",
                key=f"edit_{name}", height=300)
            import_df = theme.restore_operator_frame(edited, df)
            c1, c2, c3, c4 = st.columns([1, 1, 1, 3])
            if c1.button("Apply edits", key=f"apply_{name}"):
                try:
                    import_table(scn, name, import_df)
                    stale = common.invalidate_outputs()
                    issues = common.revalidate()
                    errs = [i for i in issues if i.severity == "ERROR"]
                    st.success(
                        f"applied — validation: {len(errs)} errors"
                        + (" · previous plan cleared" if stale else ""))
                except Exception as e:  # surface, don't crash
                    st.error(f"import failed: {e}")
            c2.download_button("CSV", import_df.to_csv(index=False).encode(),
                               f"{name}.csv", key=f"csv_{name}")
            c3.download_button(
                "JSON", import_df.to_json(orient="records", indent=1).encode(),
                f"{name}.json", key=f"json_{name}")
            up = c4.file_uploader("import CSV/XLSX/JSON", key=f"up_{name}",
                                  type=["csv", "xlsx", "json"],
                                  label_visibility="collapsed")
            if up is not None:
                try:
                    if up.name.endswith(".csv"):
                        ndf = pd.read_csv(up)
                    elif up.name.endswith(".xlsx"):
                        ndf = pd.read_excel(up)
                    else:
                        ndf = pd.DataFrame(json.load(up))
                    import_table(scn, name, ndf)
                    stale = common.invalidate_outputs()
                    common.revalidate()
                    st.success(f"imported {len(ndf)} rows into {name} — "
                               "reload the page tab to see them"
                               + (" · previous plan cleared" if stale else ""))
                except Exception as e:
                    st.error(f"import failed: {e}")

    _render_destination_quality_specs(scn)

    st.divider()
    st.download_button("⬇ Export whole scenario (XLSX)", export_excel(scn),
                       f"{scn.name}.xlsx")


# ================================================= 3. Stockyards & Inventory
def _render_inventory_snapshot_panel(scn):
    """Preview and atomically apply the planning run's t0 inventory."""
    meta = scn.config.get("INVENTORY_SNAPSHOT", {})
    title = "Starting inventory snapshot"
    with st.expander(title, expanded=not bool(meta)):
        if meta:
            captured = str(meta.get("captured_at", "—"))
            source = str(meta.get("source", "—"))
            covered = int(meta.get("covered_count", 0))
            expected = int(meta.get("expected_count", len(scn.spaces)))
            acquisition = str(meta.get("acquisition_mode", "file"))
            quality_covered = int(meta.get("quality_pile_count", 0))
            basis_label = ("live read-only snapshot"
                           if acquisition == "live-read-only" else "applied snapshot")
            if meta.get("complete"):
                st.success(
                    f"Current run basis: {basis_label} · {covered}/{expected} "
                    f"piles · quality supplied for {quality_covered}/{covered} "
                    f"piles · captured {captured} · source: {source}")
            else:
                st.warning(
                    f"Mixed inventory basis: snapshot covers {covered}/{expected} "
                    f"piles; untouched piles retain scenario values · captured "
                    f"{captured} · source: {source}")
            if acquisition == "live-read-only" and quality_covered < covered:
                st.warning(
                    "Live mass coverage exceeds measured-quality coverage. Piles "
                    "without supplied quality retain/derive the scenario grade-"
                    "quality basis; commission grade and analysis mappings before "
                    "using this as an operational quality plan.")
        else:
            st.warning(
                "Current run basis: built-in scenario inventory. Initial pile "
                "masses/contents marked as planning assumptions are "
                "demonstration values.")

        st.caption(
            "Upload a timestamped SMS/YardMaster export, or fetch a commissioned "
            "read-only SpaceValue preview, before generating a plan. Previewing "
            "never changes the scenario; Apply replaces t0 inventory atomically "
            "and clears any plan or simulation based on older values.")
        st.caption(
            "Scope: pile inventory only. Active jobs, equipment state and "
            "in-transit C1/C2 belt material remain separate scenario inputs and "
            "must share the same t0 for a complete operational run.")
        st.caption(
            "Segment quality/age is retained for display and FIFO; the current "
            "planner uses its mass-weighted pile quality unless explicit, "
            "validated multi-grade zone/campaign inputs are enabled.")

        # A live pull is still only a canonical preview. It must pass this
        # exact same validation/diff/apply gate as a file snapshot.
        live_entry = st.session_state.get("sms_live_pull_preview")
        if live_entry:
            from mph_sim.rabbitmq_inventory import (
                load_live_inventory_configs, scenario_inventory_fingerprint,
            )
            current_bundle = load_live_inventory_configs()
            stale_preview = (
                live_entry.get("scenario_fingerprint") !=
                scenario_inventory_fingerprint(scn)
                or live_entry.get("config_revision", "") !=
                current_bundle.revision_hash)
            if stale_preview:
                common.clear_live_inventory_preview()
                live_entry = None
                # Sidebar navigation can rerun this page more than once.
                # Retain the reason until replacement input arrives so the
                # operator can see why the fetched preview disappeared.
                st.session_state["inventory_snapshot_discard_notice"] = (
                    "A fetched live preview was discarded because the "
                    "scenario or connector configuration changed.")
            else:
                st.session_state.pop("inventory_snapshot_discard_notice", None)

        discard_notice = st.session_state.get("inventory_snapshot_discard_notice")
        if discard_notice:
            st.warning(discard_notice)

        source_options = (["Fetched live SpaceValues", "Upload file"]
                          if live_entry else ["Upload file"])
        source_mode = st.radio("Snapshot source", source_options,
                               horizontal=True, key="inventory_snapshot_source")
        live_mode = source_mode == "Fetched live SpaceValues"

        c1, c2, c3 = st.columns([1.2, 1, 1])
        c1.download_button(
            "Download CSV template",
            snapshot_template_csv(scn),
            "inventory_snapshot_template.csv",
            mime="text/csv",
            help="Fill captured_at (with timezone), source and mass_t. Exact model "
                 "pile IDs and capacities are included as references.")
        if live_mode:
            c2.checkbox(
                "Require all loaded piles", value=True, disabled=True,
                key="inventory_live_require_complete",
                help="Live planning snapshots always require every loaded pile.")
            # Never let a prior file-import widget choice weaken the live
            # all-site/complete-pile safety gate.
            require_complete = True
        else:
            require_complete = c2.checkbox(
                "Require all loaded piles", value=True,
                key="inventory_file_require_complete",
                help="Recommended for a planning run. Turn off only for an "
                     "explicit partial patch; untouched piles keep their "
                     "scenario values.")
        max_age = c3.number_input(
            "Maximum age [h]", min_value=0.0,
            value=0.5 if live_mode else 24.0,
            step=0.1 if live_mode else 1.0,
            key=("inventory_live_max_age" if live_mode
                 else "inventory_file_max_age"),
            help="Snapshots older than this limit are blocked.")
        if not require_complete:
            st.warning("Partial patch selected — the run will mix snapshot values "
                       "with unchanged scenario/demo inventory.")

        mappings = {}
        mapping_error = ""
        if live_mode:
            snapshot = live_entry.get("snapshot")
            st.info(
                "Fetched read-only from: "
                + ", ".join(live_entry.get("requested_sites", []))
                + ". Source timestamps: "
                + " · ".join(f"{k}={v}" for k, v in
                              sorted(snapshot.site_captured_at.items())))
            if st.button("Discard fetched live preview"):
                common.clear_live_inventory_preview()
                st.rerun()
        else:
            use_mapping = st.checkbox(
                "External pile IDs differ from model IDs",
                help="No fuzzy matching is performed. Provide an explicit external-to-"
                     "model mapping when SMS identifiers differ.")
            if use_mapping:
                mapping_text = st.text_area(
                    "Pile-ID mapping (JSON object)", value="{}",
                    placeholder='{"SMS-M16-H01": "HP-01", "SMS-B02-P01": "BG-P1"}',
                    height=90)
                try:
                    parsed_mapping = json.loads(mapping_text or "{}")
                    if not isinstance(parsed_mapping, dict):
                        raise ValueError("mapping must be a JSON object")
                    mappings = {str(k).strip(): str(v).strip()
                                for k, v in parsed_mapping.items()}
                except (json.JSONDecodeError, ValueError) as exc:
                    mapping_error = str(exc)
                    st.error(f"ID mapping is invalid: {exc}")

            nonce = st.session_state.get("inventory_snapshot_upload_nonce", 0)
            uploaded = st.file_uploader(
                "Inventory snapshot (CSV, XLSX or JSON)",
                type=["csv", "xlsx", "json"],
                key=f"inventory_snapshot_upload_{nonce}")
            if uploaded is None:
                st.caption(
                    "Minimum usable data: captured_at, pile_id and mass_t. Grade and "
                    "quality improve blending; segments with age improve FIFO visibility.")
                return

            if mapping_error:
                return
            try:
                snapshot = parse_inventory_snapshot(uploaded.getvalue(), uploaded.name)
            except SnapshotParseError as exc:
                st.error(f"Snapshot cannot be parsed: {exc}")
                return
            st.session_state.pop("inventory_snapshot_discard_notice", None)

        validation = validate_inventory_snapshot(
            scn, snapshot, mappings=mappings, max_age_hours=float(max_age),
            now=datetime.now(timezone.utc), require_complete=require_complete)
        diff = snapshot_diff_rows(scn, snapshot, mappings=mappings)
        resolved = len(set(validation.resolved_ids.values()))
        quality_count = sum(
            1 for p in snapshot.piles
            if p.quality or (
                any(s.mass_t > 0 for s in p.segments)
                and all(bool(s.quality) for s in p.segments if s.mass_t > 0)))
        segment_count = sum(1 for p in snapshot.piles if p.segments)
        total_delta = sum(float(r["delta [t]"]) for r in diff
                          if r.get("delta [t]") is not None)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Captured at", snapshot.captured_at.isoformat())
        m2.metric("Pile coverage", f"{resolved}/{len(scn.spaces)}")
        m3.metric("Net inventory Δ", f"{total_delta:+,.0f} t")
        m4.metric("Quality / segments",
                  f"{quality_count} / {segment_count} piles")
        st.dataframe(theme.operator_frame(pd.DataFrame(diff)),
                     use_container_width=True,
                     hide_index=True)

        errors = [i for i in validation.issues if i.severity == "ERROR"]
        warnings = [i for i in validation.issues if i.severity == "WARNING"]
        if errors:
            st.error(f"Blocked: {len(errors)} validation error(s). Nothing has "
                     "been changed.")
        elif warnings:
            st.warning(f"Valid with {len(warnings)} warning(s). Review before apply.")
        else:
            st.success("Snapshot is valid and ready to apply.")
        if validation.issues:
            st.dataframe(theme.operator_frame(pd.DataFrame([{
                "severity": i.severity, "code": i.code,
                "entity": i.entity or "—", "message": i.message,
            } for i in validation.issues])), use_container_width=True,
                hide_index=True)

        quality_acknowledged = True
        if live_mode and quality_count < resolved:
            quality_acknowledged = st.checkbox(
                "I understand that live mass is incomplete as a live quality "
                "basis and unmapped quality will retain/derive scenario values",
                value=False, key="inventory_live_quality_fallback_ack")

        has_old_outputs = common.plan() is not None or common.sim() is not None
        confirmed = True
        if has_old_outputs:
            confirmed = st.checkbox(
                "I understand the current plan/simulation will be cleared",
                value=False)
        can_apply = (not validation.has_errors and confirmed
                     and quality_acknowledged
                     and not st.session_state.get("solving", False))
        if st.button("Apply snapshot and clear current plan/simulation",
                     type="primary", disabled=not can_apply,
                     use_container_width=True):
            result = apply_inventory_snapshot(
                scn, snapshot, mappings=mappings,
                max_age_hours=float(max_age), now=datetime.now(timezone.utc),
                require_complete=require_complete)
            if common.commit_inventory_snapshot(result):
                st.success(
                    f"Applied {len(result.changed_space_ids)} pile(s); net "
                    f"inventory change {result.mass_delta_t:+,.0f} t. Planning "
                    "horizon t0 now matches the snapshot timestamp.")
                st.rerun()
            else:
                st.error("Snapshot was not applied because validation changed; "
                         "review the issues and try again.")


def page_stockyards():
    from .stockyard_monitor import render_monitor
    st.subheader("Stockyards & Inventory")
    scn = common.sc()
    s = common.sim()

    _render_inventory_snapshot_panel(scn)

    # ---- Stockyard Monitor (dynamic phosphate yards) -----------------------
    render_monitor(scn, common.plan(), s)

    st.markdown("#### Detailed inventory tables (all areas incl. M17)")
    loaded, missing = common.loaded_site_filters(scn)
    sel = st.multiselect("Site / domain filter", common.SITE_FILTERS,
                         default=loaded,
                         help="Domain A = phosphate (Louta, Benguerir, MPH M16)"
                              " · Domain B = sulphur (MPH M17)")
    st.caption("Loaded in this scenario: **" + " · ".join(loaded) + "**"
               + (" — not loaded here: " + " · ".join(missing) if missing
                  else ""))

    areas = {}
    for sp in scn.spaces:
        if sel and not any(common.match_site_filter(o, sp.site, sp.area)
                           for o in sel):
            continue
        areas.setdefault((sp.site, sp.area), []).append(sp)
    if not areas:
        st.info("no stockyards match the current filter")
        return

    for (site, area), spaces in areas.items():
        st.markdown(f"#### {site} — {area}")
        rows = []
        for sp in spaces:
            mass = s.inv[sp.id] if s else sp.mass_t
            q = (s.space_quality.get(sp.id) if s else None) or scn.quality_of(sp.id)
            segs = [g for g in scn.segments if g.space_id == sp.id]
            age = f"{min(g.age_days for g in segs):.0f}–{max(g.age_days for g in segs):.0f}" \
                if segs else "—"
            reach = [m.id for m in scn.machines if sp.id in m.reachable_spaces]
            rows.append({
                "space": sp.id, "capacity [t]": sp.capacity_t,
                "mass [t]": round(mass), "free [t]": round(sp.capacity_t - mass),
                "grade": sp.grade, "state": sp.state,
                "material class": sp.material_class or "—",
                "quality profile": sp.profile_id or "—",
                "reserved [t]": sp.reserved_t,
                "safety stock [t]": sp.safety_stock_t,
                "age [d]": age,
                "quality": " ".join(f"{k}={v:g}" for k, v in list(q.items())[:4]),
                "reachable by": ", ".join(reach) or "—",
                "evidence": sp.evidence.status,
            })
        show_df(pd.DataFrame(rows))
        plot(_yard_fig(scn, spaces, s))
    st.caption("⚠ Schematic fill representation only — x-geometry is ASSUMED "
               "until the plot plan / GA is available (no surveyed layout).")

    if scn.segments:
        st.markdown("#### Inventory segments (coarse; mirrors ASP 1-m cache)")
        show_df(entities_to_df(scn.segments))


def _yard_fig(scn, spaces, s) -> go.Figure:
    fig = go.Figure()
    for i, sp in enumerate(spaces):
        mass = s.inv[sp.id] if s else sp.mass_t
        fill = mass / sp.capacity_t if sp.capacity_t else 0
        y0 = -i
        fig.add_shape(type="rect", x0=sp.x_from, x1=sp.x_to, y0=y0 - 0.38,
                      y1=y0 + 0.38, line=dict(color=NAVY, width=1.4),
                      fillcolor="#ffffff")
        fig.add_shape(type="rect", x0=sp.x_from,
                      x1=sp.x_from + fill * (sp.x_to - sp.x_from),
                      y0=y0 - 0.38, y1=y0 + 0.38,
                      fillcolor={"TBT": GREEN, "BT": BLUE, "MT": ORANGE,
                                 "S-STD": "#c9a227"}.get(sp.grade, GREY),
                      opacity=0.55, line_width=0)
        segs = [g for g in scn.segments if g.space_id == sp.id]
        for g in segs:
            fig.add_shape(type="line", x0=g.x_from, x1=g.x_from, y0=y0 - 0.38,
                          y1=y0 + 0.38, line=dict(color=GREY, width=1, dash="dot"))
        fig.add_annotation(x=sp.x_from - 4, y=y0, text=sp.id, showarrow=False,
                           xanchor="right", font=dict(size=11, color=NAVY))
        fig.add_annotation(x=sp.x_to + 4, y=y0, xanchor="left", showarrow=False,
                           text=f"{mass:,.0f}/{sp.capacity_t:,.0f} t · {sp.grade}",
                           font=dict(size=10, color=GREY))
        if sp.safety_stock_t > 0 and sp.capacity_t:
            xs = sp.x_from + (sp.safety_stock_t / sp.capacity_t) * (sp.x_to - sp.x_from)
            fig.add_shape(type="line", x0=xs, x1=xs, y0=y0 - 0.45, y1=y0 + 0.45,
                          line=dict(color=RED, width=2, dash="dash"))
    fig.update_xaxes(title="x [m] (geometry ASSUMED)", range=[-70, max(
        sp.x_to for sp in spaces) + 130])
    fig.update_yaxes(visible=False)
    return plotly_layout(fig, height=70 + 52 * len(spaces))


# ================================================= 4. Routes & Equipment
def page_routes():
    from .flow_configuration import render_flow_availability_configuration

    st.subheader("Flow, Routes & Equipment")
    scn = common.sc()
    s = common.sim()
    g = RouteGraph.from_scenario(scn)

    loaded, missing = common.loaded_site_filters(scn)
    st.caption("Sites/domains loaded in this scenario: **"
               + " · ".join(loaded) + "**"
               + (" — not loaded here: " + " · ".join(missing)
                  if missing else ""))

    render_flow_availability_configuration(scn)

    st.divider()
    st.markdown("#### Network model details")
    st.caption("The **route graph is the real network model** owned by this layer "
               "(nodes/links/capacities/conflicts/delays) — deliberately separate "
               "from the ASP free-text `(Device, JobType, Location)` routes "
               "(report §14.2).")
    plot(_network_fig(scn, g, s))

    left, right = st.columns(2)
    with left:
        st.markdown("#### Links")
        df = entities_to_df(scn.links)
        cols = ["id", "src", "dst", "capacity_tph", "min_stable_tph",
                "transit_buckets", "available", "shared_group", "conflict_group",
                "gate_id", "gate_position", "changeover_min", "evidence_status"]
        show_df(df[[c for c in cols if c in df.columns]], height=320)
        unav = g.unavailable_links()
        if unav:
            st.warning("Unavailable links: " + ", ".join(unav))
        if s:
            bott = g.bottlenecks({li: max(v) if v else 0.0
                                  for li, v in s.link_load.items()})
            if bott:
                st.error("Bottlenecks (≥85 % of capacity at peak): "
                         + ", ".join(f"{l} ({u * 100:.0f} %)" for l, u in bott))
                st.caption("values > 100 % are an artefact of dominant-path "
                           "accounting when one machine's stream splits over "
                           "parallel legs — the plan itself respects link "
                           "capacities (tested)")
    with right:
        st.markdown("#### Active source→destination paths")
        rows = []
        for d in scn.destinations:
            for sp in scn.spaces:
                p = g.best_path(space_node(scn, sp.id), d.node_id)
                if p:
                    rows.append({"from": sp.id, "to": d.id,
                                 "links": " → ".join(p.links),
                                 "capacity [tph]": p.capacity_tph,
                                 "delay [buckets]": p.transit_buckets})
        show_df(pd.DataFrame(rows), height=320)
        conf = g.conflict_groups()
        if conf:
            st.markdown("#### Route-conflict groups (mutually exclusive)")
            show_df(pd.DataFrame([{"group": k, "links": ", ".join(v)}
                                  for k, v in conf.items()]))

    st.markdown("#### Machine ↔ pile reachability (configured, not inferred)")
    rm = reachability_matrix(scn)
    if rm:
        mat = pd.DataFrame(rm).T.fillna(False)
        fig = go.Figure(go.Heatmap(
            z=mat.astype(int).values, x=list(mat.columns), y=list(mat.index),
            colorscale=[[0, "#f0f2f5"], [1, GREEN]], showscale=False,
            xgap=2, ygap=2))
        plot(plotly_layout(fig, height=60 + 32 * len(mat)))

    st.markdown("#### Machines")
    mdf = entities_to_df(scn.machines)
    cols = ["id", "site", "kind", "rate_min_tph", "rate_max_tph", "rail",
            "travel_speed_m_min", "setup_min", "min_separation_m",
            "start_stop_limit_per_h", "available", "evidence_status", "source"]
    show_df(mdf[[c for c in cols if c in mdf.columns]])


def _network_fig(scn, g: RouteGraph, s) -> go.Figure:
    # simple layered coordinates: use node.x/.y hints; fallback by kind
    kind_x = {"source": 0, "space": 1, "tower": 2, "gate": 3, "dest": 4}
    pos = {}
    counters = {}
    for n in scn.nodes:
        x = n.x if n.x else kind_x.get(n.kind, 2)
        counters[x] = counters.get(x, 0) + 1
        y = n.y if n.y else counters[x]
        pos[n.id] = (x, y)
    fig = go.Figure()
    for l in scn.links:
        if l.src not in pos or l.dst not in pos:
            continue
        x0, y0 = pos[l.src]
        x1, y1 = pos[l.dst]
        peak = max(s.link_load[l.id]) if (s and l.id in s.link_load) else 0.0
        util = peak / l.capacity_tph if l.capacity_tph else 0
        color = RED if not l.available else (ORANGE if util >= 0.85 else
                                             GREEN if util > 0 else GREY)
        fig.add_scatter(x=[x0, x1], y=[y0, y1], mode="lines",
                        line=dict(color=color,
                                  width=1.2 + 3 * min(util, 1),
                                  dash="dot" if l.transit_buckets else "solid"),
                        hovertext=f"{l.id}: {l.capacity_tph:.0f} tph"
                                  + (f", delay {l.transit_buckets}" if l.transit_buckets else "")
                                  + (f", peak {peak:.0f} tph" if peak else "")
                                  + ("", " — UNAVAILABLE")[not l.available],
                        hoverinfo="text", showlegend=False)
    kcol = {"space": BLUE, "tower": GREY, "gate": VIOLET, "dest": DGREEN,
            "source": ORANGE}
    for n in scn.nodes:
        x, y = pos[n.id]
        fig.add_scatter(x=[x], y=[y], mode="markers+text",
                        marker=dict(size=13, color=kcol.get(n.kind, GREY)),
                        text=[n.label or n.id], textposition="bottom center",
                        textfont=dict(size=9), hovertext=n.id,
                        hoverinfo="text", showlegend=False)
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return plotly_layout(fig, "Route network (grey=idle · green=loaded · "
                              "orange=bottleneck · red=unavailable · "
                              "dotted=transport delay)", 420)


# ================================================= 5. Demand
_UNIT_COL = "Receiving unit"
_SITE_COL = "Site"
_TYPE_COL = "Requirement"
_DAY_COL = "Day"
_DATE_COL = "Date"
_GRADE_COL = "Requested grade"
_QUANTITY_COL = "Quantity [t]"
_DAILY_QUANTITY_COL = "Campaign quantity [t]"
_START_HOUR_COL = "Start hour"
_END_HOUR_COL = "End hour"
_RATE_COL = "Average rate [t/h]"
_PRIORITY_COL = "Priority"
_ROUTE_COL = "Route"

_DEMAND_SITE_ORDER = ("BENGUERIR", "LOUTA", "MPH")
_DEMAND_SITE_PRESENTATION = {
    "BENGUERIR": (
        "Benguerir",
        "Daily washing-line feed requirements and train-loading campaigns.",
    ),
    "LOUTA": (
        "Louta",
        "Daily washing-line feed requirements and the stocked-route policy.",
    ),
    "MPH": (
        "MPH",
        "Daily PAP and drying-plant delivery orders. Future or disabled lines "
        "retain their configured demand.",
    ),
}


def _demand_rows_by_site(scn) -> list[tuple[str, str, str, list[dict]]]:
    """Group the complete demand contract into stable operator site tabs."""
    grouped: dict[str, list[dict]] = {}
    rows = _demand_plan_rows(scn)
    for destination, row in zip(scn.destinations, rows):
        grouped.setdefault(destination.site, []).append(row)

    ordered_sites = [
        site_id for site_id in _DEMAND_SITE_ORDER if grouped.get(site_id)
    ]
    ordered_sites.extend(sorted(
        site_id for site_id, site_rows in grouped.items()
        if site_rows and site_id not in _DEMAND_SITE_ORDER
    ))
    sections = []
    for site_id in ordered_sites:
        label, description = _DEMAND_SITE_PRESENTATION.get(
            site_id, (theme.operator_text(site_id),
                      "Receiving-unit requirements for this site."))
        sections.append((site_id, label, description, grouped[site_id]))
    return sections


def _daily_bucket_ranges(scn) -> list[range]:
    """Return the calendar-day bucket windows in the planning horizon.

    S0 is a seven-day, one-hour model, so each complete window contains 24
    buckets.  Deriving the windows from ``start_iso`` keeps the editor correct
    if a later scenario starts at a different date (or at a non-midnight hour).
    """
    grouped: list[list[int]] = []
    current_date = None
    for bucket in range(scn.sim.horizon_buckets):
        bucket_date = scn.sim.bucket_dt(bucket).date()
        if bucket_date != current_date:
            grouped.append([])
            current_date = bucket_date
        grouped[-1].append(bucket)
    return [range(items[0], items[-1] + 1) for items in grouped if items]


def _configured_demand_grade(scn, dest_id: str, bucket: int) -> str:
    """Read the saved bucket grade, with the destination grade as fallback."""
    if hasattr(scn, "demand_grade"):
        return str(scn.demand_grade(dest_id, bucket) or "")
    grade = ""
    for entry in scn.demand:
        if entry.dest_id == dest_id and entry.bucket == bucket:
            grade = str(getattr(entry, "requested_grade", "") or grade)
    return grade or str(
        getattr(scn.destination(dest_id), "requested_grade", "") or "")


def _daily_demand_rows_by_site(
        scn) -> list[tuple[str, str, str, list[dict]]]:
    """Build dated intraday campaign rows for every receiving unit.

    A run ends when grade or priority changes, retaining distinct campaigns
    for the same receiving unit and civil date.
    """
    day_ranges = _daily_bucket_ranges(scn)
    grouped: dict[str, list[dict]] = {}
    for destination in scn.destinations:
        process_feed = (
            getattr(destination, "demand_kind", "delivery") == "process_feed"
        )
        for day_index, buckets in enumerate(day_ranges):
            first_bucket = buckets.start
            start = scn.sim.bucket_dt(first_bucket)
            bucket_list = list(buckets)
            grade_runs: list[tuple[int, int, str, int]] = []
            run_start = 0
            run_grade = _configured_demand_grade(
                scn, destination.id, bucket_list[0])
            run_priority = scn.demand_priority(destination.id, bucket_list[0])
            for offset, bucket in enumerate(bucket_list[1:], start=1):
                grade = _configured_demand_grade(
                    scn, destination.id, bucket)
                priority = scn.demand_priority(destination.id, bucket)
                if grade != run_grade or priority != run_priority:
                    grade_runs.append((run_start, offset, run_grade, run_priority))
                    run_start = offset
                    run_grade = grade
                    run_priority = priority
            grade_runs.append((run_start, len(bucket_list), run_grade, run_priority))

            for run_start, run_end, grade_id, priority in grade_runs:
                run_buckets = bucket_list[run_start:run_end]
                duration_h = len(run_buckets) * scn.sim.dt_h
                quantity_t = sum(
                    _configured_demand_tph(
                        scn, destination.id, bucket) * scn.sim.dt_h
                    for bucket in run_buckets
                )
                grouped.setdefault(destination.site, []).append({
                    _DAY_COL: f"Day {day_index + 1} · {start:%a}",
                    _DATE_COL: start.strftime("%Y-%m-%d"),
                    _UNIT_COL: destination.id,
                    _TYPE_COL: (
                        "Washing feed" if process_feed else "Final delivery"),
                    _GRADE_COL: grade_id or "—",
                    _DAILY_QUANTITY_COL: quantity_t,
                    _START_HOUR_COL: (
                        start.hour + start.minute / 60.0 + run_start * scn.sim.dt_h),
                    _END_HOUR_COL: (
                        start.hour + start.minute / 60.0 + run_end * scn.sim.dt_h),
                    _RATE_COL: (
                        quantity_t / duration_h if duration_h > 0 else 0.0),
                    _PRIORITY_COL: priority,
                    _ROUTE_COL: _demand_route_label(
                        scn, destination, grade_id),
                })

    ordered_sites = [
        site_id for site_id in _DEMAND_SITE_ORDER if grouped.get(site_id)
    ]
    ordered_sites.extend(sorted(
        site_id for site_id, site_rows in grouped.items()
        if site_rows and site_id not in _DEMAND_SITE_ORDER
    ))
    sections = []
    for site_id in ordered_sites:
        label, description = _DEMAND_SITE_PRESENTATION.get(
            site_id, (theme.operator_text(site_id),
                      "Receiving-unit requirements for this site."))
        sections.append((site_id, label, description, grouped[site_id]))
    return sections


def _configured_demand_tph(scn, dest_id: str, bucket: int) -> float:
    """Read the saved demand template without lifecycle filtering.

    ``Scenario.demand_tph`` intentionally returns zero for a Future receiving
    line. Configure and Plan editors must still display and preserve that
    line's saved template so selecting it as Available restores the order.
    """
    value = None
    for entry in scn.demand:
        if entry.dest_id == dest_id and entry.bucket == bucket:
            value = entry.rate_tph
    if value is None:
        value = scn.destination(dest_id).demand_default_tph
    return float(value)


def _demand_quantity_t(scn, dest_id: str) -> float:
    """Return the configured horizon quantity for one receiving unit."""
    return sum(
        _configured_demand_tph(scn, dest_id, bucket) * scn.sim.dt_h
        for bucket in range(scn.sim.horizon_buckets)
    )


def _demand_route_label(scn, destination, requested_grade: str = "") -> str:
    """Return the operator route state for one destination/grade campaign."""
    process_feed = (
        getattr(destination, "demand_kind", "delivery") == "process_feed"
    )
    if process_feed:
        route_modelled = False
        grade_id = requested_grade or str(
            getattr(destination, "requested_grade", "") or "")
        for transfer in getattr(scn, "process_transfers", []) or []:
            if transfer.process_stage != getattr(
                    destination, "process_stage", ""):
                continue
            try:
                source_grade = scn.space(transfer.source_space).grade
            except StopIteration:
                continue
            if not grade_id or source_grade == grade_id:
                route_modelled = True
                break
        if not process_stage_enabled(
                scn, getattr(destination, "process_stage", "")):
            return "Disabled"
        return "Modelled" if route_modelled else "Pending model"

    line_status = destination_line_status(scn, destination.id)
    if line_status == "FUTURE":
        return "Future"
    if line_status == "DISABLED":
        return "Disabled"
    if line_status == "PARTIAL":
        return "Partly available"
    return "Modelled"


def _demand_plan_rows(scn) -> list[dict]:
    """Build the concise, one-row-per-unit demand contract."""
    horizon_h = scn.sim.horizon_buckets * scn.sim.dt_h
    rows = []
    for destination in scn.destinations:
        quantity_t = _demand_quantity_t(scn, destination.id)
        process_feed = (
            getattr(destination, "demand_kind", "delivery") == "process_feed"
        )
        route_label = _demand_route_label(
            scn, destination,
            str(getattr(destination, "requested_grade", "") or ""))
        rows.append({
            _UNIT_COL: destination.id,
            _TYPE_COL: "Washing feed" if process_feed else "Final delivery",
            _GRADE_COL: getattr(destination, "requested_grade", "") or "—",
            _QUANTITY_COL: quantity_t,
            _RATE_COL: quantity_t / horizon_h if horizon_h > 0 else 0.0,
            _PRIORITY_COL: destination.priority,
            _ROUTE_COL: route_label,
        })
    return rows


def _scaled_demand_rates(scn, dest_id: str,
                         requested_quantity_t: float) -> list[float]:
    """Scale one unit's hourly shape to the requested horizon quantity.

    Existing non-zero demand keeps its shape. A newly activated unit receives
    a flat profile, so its order is immediately runnable without inventing a
    peak hour.
    """
    quantity_t = float(requested_quantity_t)
    if not math.isfinite(quantity_t) or quantity_t < 0:
        raise ValueError("Demand quantity must be a non-negative number.")
    T = scn.sim.horizon_buckets
    dt_h = scn.sim.dt_h
    if T <= 0 or dt_h <= 0:
        raise ValueError("Planning horizon and time step must be positive.")
    current = [
        _configured_demand_tph(scn, dest_id, t)
        for t in range(T)
    ]
    if any(not math.isfinite(rate) or rate < 0 for rate in current):
        raise ValueError(f"{dest_id} has an invalid hourly demand profile.")
    current_quantity_t = sum(rate * dt_h for rate in current)
    if current_quantity_t > 1e-9:
        factor = quantity_t / current_quantity_t
        return [rate * factor for rate in current]
    flat_rate_tph = quantity_t / (T * dt_h)
    return [flat_rate_tph] * T


def _demand_entries_for_quantities(
        scn, requested_quantities_t: dict[str, float]) -> list[DemandEntry]:
    """Normalise edited unit totals back to the solver's bucket demand."""
    entries = []
    for destination in scn.destinations:
        quantity_t = requested_quantities_t.get(
            destination.id, _demand_quantity_t(scn, destination.id))
        rates = _scaled_demand_rates(scn, destination.id, quantity_t)
        for bucket, rate_tph in enumerate(rates):
            if not math.isclose(
                    rate_tph, destination.demand_default_tph,
                    rel_tol=1e-9, abs_tol=1e-9):
                entries.append(
                    DemandEntry(destination.id, bucket, rate_tph))
    return entries


def _grade_display_maps(scn) -> tuple[dict[str, str], dict[str, str]]:
    """Return safe display↔model mappings for selectable order grades."""
    grades = [
        grade for grade in scn.grades
        if grade.quality or getattr(grade, "is_specification", False) or any(
            getattr(destination, "requested_grade", "") == grade.id
            for destination in scn.destinations)
    ]
    display_to_id = {"—": ""}
    id_to_display = {"": "—"}
    for grade in grades:
        grade_id = grade.id
        label = theme.operator_text(grade_id)
        if (str(getattr(grade, "material_class", "")).lower() == "washed"
                and not getattr(grade, "is_specification", False)
                and not label.lower().endswith("-w")):
            label = f"{label}-w"
        if label in display_to_id and display_to_id[label] != grade_id:
            label = f"{label} ({len(display_to_id)})"
        display_to_id[label] = grade_id
        id_to_display[grade_id] = label
    return display_to_id, id_to_display


def _demand_site_summaries(site_sections) -> list[dict[str, object]]:
    """Aggregate editable weekly demand by site and requested grade."""
    summaries = []
    for site_id, label, description, rows in site_sections:
        by_grade: dict[str, float] = {}
        for row in rows:
            grade_id = str(row.get(_GRADE_COL, "") or "")
            if grade_id == "—":
                grade_id = ""
            quantity_t = float(row.get(_DAILY_QUANTITY_COL, 0.0) or 0.0)
            by_grade[grade_id] = by_grade.get(grade_id, 0.0) + quantity_t
        summaries.append({
            "site_id": site_id,
            "label": label,
            "description": description,
            "total_t": sum(by_grade.values()),
            "by_grade": by_grade,
        })
    return summaries


def _render_demand_site_cards(site_sections, grade_to_display) -> None:
    """Render clickable site totals with a popover grade breakdown."""
    summaries = _demand_site_summaries(site_sections)
    if not summaries:
        return
    st.markdown("#### Total demand by site")
    st.caption(
        "Select a site card to see the weekly quantities by requested quality."
    )
    st.markdown(
        """
        <style>
        .st-key-demand_site_cards [data-testid="stPopover"] > button {
            min-height: 5rem;
            justify-content: flex-start;
            text-align: left;
            border: 1px solid #c6d2df;
            border-radius: .45rem;
            background: #f2f5f8;
            color: #173a59;
            padding: .75rem 1rem;
        }
        .st-key-demand_site_cards [data-testid="stPopover"] > button:hover {
            border-color: #245987;
            background: #e9f0f7;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.container(key="demand_site_cards"):
        columns = st.columns(len(summaries))
        for column, summary in zip(columns, summaries):
            label = str(summary["label"])
            total_t = float(summary["total_t"])
            with column.popover(
                    f"**{label}**  \n{theme.fmt_t(total_t)} total",
                    width="stretch"):
                st.markdown(f"**{label} weekly demand**")
                st.caption(str(summary["description"]))
                grade_rows = []
                for grade_id, quantity_t in sorted(
                        dict(summary["by_grade"]).items(),
                        key=lambda item: (-item[1], item[0])):
                    grade_rows.append({
                        "Requested quality": grade_to_display.get(
                            grade_id,
                            theme.operator_text(grade_id) if grade_id
                            else "—"),
                        "Quantity [t]": quantity_t,
                        "Share [%]": (
                            100.0 * quantity_t / total_t
                            if total_t > 0 else 0.0),
                    })
                st.dataframe(
                    pd.DataFrame(grade_rows),
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "Quantity [t]": st.column_config.NumberColumn(
                            "Quantity [t]", format="%.0f"),
                        "Share [%]": st.column_config.NumberColumn(
                            "Share [%]", format="%.1f%%"),
                    },
                )


def _weekly_campaign_entries(
        scn, edited_rows, display_to_grade: dict[str, str]
        ) -> list[DemandEntry]:
    """Validate dated campaign rows and expand them to the solver grid.

    Several grades may be requested by the same unit on the same date, but
    positive-tonnage windows may not overlap because one solver bucket has one
    explicit campaign grade.  Missing rows mean zero demand; a complete bucket
    series is still emitted so destination defaults cannot leak back in.
    """
    day_ranges = _daily_bucket_ranges(scn)
    ranges_by_date = {
        scn.sim.bucket_dt(buckets.start).strftime("%Y-%m-%d"): buckets
        for buckets in day_ranges
    }
    destinations = {destination.id: destination
                    for destination in scn.destinations}
    valid_grade_ids = {grade.id for grade in scn.grades}
    dt_h = float(scn.sim.dt_h)
    assignments = {
        (destination.id, bucket): {
            "rate_tph": 0.0,
            "grade": str(destination.requested_grade or ""),
            "positive": False,
            "priority": None,
        }
        for destination in scn.destinations
        for bucket in range(scn.sim.horizon_buckets)
    }

    def text(value) -> str:
        if value is None or pd.isna(value):
            return ""
        return str(value).strip()

    for row_number, row in enumerate(edited_rows, start=1):
        unit_id = text(row.get(_UNIT_COL))
        date_text = text(row.get(_DATE_COL))
        if not unit_id and not date_text:
            continue
        if unit_id not in destinations:
            raise ValueError(
                f"Row {row_number}: select a valid receiving unit.")
        if date_text not in ranges_by_date:
            raise ValueError(
                f"Row {row_number}: select a date inside the planning week.")
        try:
            quantity_t = float(row[_DAILY_QUANTITY_COL])
            start_h = float(row[_START_HOUR_COL])
            end_h = float(row[_END_HOUR_COL])
        except (KeyError, TypeError, ValueError):
            raise ValueError(
                f"Row {row_number}: quantity, start hour and end hour are "
                "required.") from None
        if not all(math.isfinite(value)
                   for value in (quantity_t, start_h, end_h)):
            raise ValueError(f"Row {row_number}: values must be finite.")
        if quantity_t < 0:
            raise ValueError(
                f"Row {row_number}: campaign quantity cannot be negative.")
        raw_priority = row.get(_PRIORITY_COL)
        priority = None
        if raw_priority is not None and not pd.isna(raw_priority) and str(raw_priority).strip():
            try:
                parsed_priority = float(raw_priority)
            except (TypeError, ValueError, OverflowError):
                parsed_priority = float("nan")
            if (isinstance(raw_priority, bool) or not math.isfinite(parsed_priority)
                    or parsed_priority < 1 or not parsed_priority.is_integer()):
                raise ValueError(f"Row {row_number}: priority must be a finite positive integer.")
            priority = int(parsed_priority)

        buckets = ranges_by_date[date_text]
        first_stamp = scn.sim.bucket_dt(buckets.start)
        first_hour = first_stamp.hour + first_stamp.minute / 60.0
        last_hour = first_hour + len(buckets) * dt_h
        if not first_hour <= start_h < end_h <= last_hour:
            raise ValueError(
                f"Row {row_number}: require {first_hour:g} ≤ start hour "
                f"< end hour ≤ {last_hour:g} for {date_text}.")
        start_slot = (start_h - first_hour) / dt_h
        end_slot = (end_h - first_hour) / dt_h
        if (not math.isclose(start_slot, round(start_slot), abs_tol=1e-9)
                or not math.isclose(
                    end_slot, round(end_slot), abs_tol=1e-9)):
            raise ValueError(
                f"Row {row_number}: campaign times must align with the "
                f"{dt_h:g}-hour planning grid.")
        start_index = int(round(start_slot))
        end_index = int(round(end_slot))
        campaign_buckets = list(buckets)[start_index:end_index]
        if not campaign_buckets:
            raise ValueError(f"Row {row_number}: campaign window is empty.")

        grade_display = text(row.get(_GRADE_COL))
        grade_id = display_to_grade.get(grade_display)
        if grade_id is None and grade_display in valid_grade_ids:
            grade_id = grade_display
        if grade_id is None:
            raise ValueError(
                f"Row {row_number}: select a registered grade.")
        if unit_id in washing.PAP_DESTINATION_IDS and grade_id != "G10":
            raise ValueError(
                f"Row {row_number}: {unit_id} only accepts the G10 "
                "receiving grade.")
        if quantity_t > 0 and not grade_id:
            raise ValueError(
                f"Row {row_number}: select a grade for positive demand.")
        if grade_id and grade_id not in valid_grade_ids:
            raise ValueError(
                f"Row {row_number}: grade is not registered.")
        destination = destinations[unit_id]
        selected_grade = scn.grade(grade_id) if grade_id else None
        default_grade = scn.grade(destination.requested_grade)
        expected_class = default_grade.material_class if default_grade else ""
        if (quantity_t > 0 and selected_grade and expected_class
                and selected_grade.material_class
                and selected_grade.material_class != expected_class):
            raise ValueError(
                f"Row {row_number}: {unit_id} requires {expected_class} "
                f"material; {grade_id} is {selected_grade.material_class}.")
        allowed_destinations = list(getattr(
            selected_grade, "receiving_destinations", []) or []) \
            if selected_grade else []
        if (quantity_t > 0 and selected_grade and
                getattr(selected_grade, "is_specification", False) and
                allowed_destinations and unit_id not in allowed_destinations):
            raise ValueError(
                f"Row {row_number}: {grade_id} is a receiving specification "
                "for " + ", ".join(allowed_destinations) +
                f", not {unit_id}.")

        if quantity_t > 0:
            for bucket in campaign_buckets:
                if assignments[(unit_id, bucket)]["positive"]:
                    stamp = scn.sim.bucket_dt(bucket).strftime("%H:%M")
                    raise ValueError(
                        f"Row {row_number}: positive campaigns for "
                        f"{unit_id} overlap on {date_text} at {stamp}.")
            # Preserve deliberate intraday peaks/zero hours when the grade
            # campaign is unchanged. New grade campaigns start flat. Without
            # this, merely pressing Apply erased advanced hourly overrides.
            prior_rates = [_configured_demand_tph(scn, unit_id, bucket)
                           for bucket in campaign_buckets]
            same_grade = all(_configured_demand_grade(scn, unit_id, bucket) == grade_id
                             for bucket in campaign_buckets)
            prior_quantity = sum(prior_rates) * dt_h
            if same_grade and prior_quantity > 0 and all(
                    math.isfinite(rate) and rate >= 0 for rate in prior_rates):
                rates = [rate * quantity_t / prior_quantity for rate in prior_rates]
            else:
                rates = [quantity_t / (len(campaign_buckets) * dt_h)] * len(campaign_buckets)
            for bucket, rate_tph in zip(campaign_buckets, rates):
                assignments[(unit_id, bucket)] = {
                    "rate_tph": rate_tph,
                    "grade": grade_id,
                    "positive": True,
                    "priority": priority,
                }
        else:
            for bucket in campaign_buckets:
                current = assignments[(unit_id, bucket)]
                if not current["positive"]:
                    current["grade"] = grade_id
                    current["priority"] = priority

    return [
        DemandEntry(
            destination.id,
            bucket,
            float(assignments[(destination.id, bucket)]["rate_tph"]),
            requested_grade=str(
                assignments[(destination.id, bucket)]["grade"] or ""),
            priority=assignments[(destination.id, bucket)]["priority"],
        )
        for destination in scn.destinations
        for bucket in range(scn.sim.horizon_buckets)
    ]


def _stage_weekly_demand_change(scn, edited_rows, display_to_grade,
                              *, preserve_c2_allocation: bool = True):
    """Validate and synchronize a complete copy before replacing live state."""
    staged = deepcopy(scn)
    staged.demand = _weekly_campaign_entries(scn, edited_rows, display_to_grade)
    corridor_targets = dict(scn.config.get("CORRIDOR_DISPATCH_TARGET_T", {}) or {})
    c2_target_t = float(corridor_targets.get("C2", 0.0)) if preserve_c2_allocation else 0.0
    if not math.isfinite(c2_target_t) or c2_target_t < 0:
        raise ValueError("C2 allocation must be a finite non-negative quantity")
    datasets.synchronize_weekly_demand_targets(staged, c2_target_t=c2_target_t)
    staged.config["ACTIVE_PLANNING_DEMAND_PROFILE"] = "CUSTOM_WEEKLY"
    introduced = _new_validation_errors(scn, staged)
    if introduced:
        raise ValueError(" · ".join(issue.message for issue in introduced[:5]))
    history = staged.config.get("DEMAND_PLAN_OVERRIDES", [])
    history = list(history) if isinstance(history, list) else []
    history.append({"at": datetime.now(timezone.utc).isoformat(),
                    "action": "weekly demand edited", "campaign_rows": len(edited_rows),
                    "c2_allocation_t": c2_target_t})
    staged.config["DEMAND_PLAN_OVERRIDES"] = history
    return staged


_LOU_WASH_STAGES = ("LOU-WASH-L1", "LOU-WASH-L2")


def _target_map(value) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    out = {}
    for key, raw in value.items():
        try:
            out[str(key)] = float(raw)
        except (TypeError, ValueError):
            continue
    return out


def _validate_louta_s0_policy(enabled: bool, corridor_target_t: float,
                               wash_targets_t: dict[str, float],
                               residence_min: float,
                               horizon_min: float) -> None:
    values = [corridor_target_t, residence_min, *wash_targets_t.values()]
    if any(not math.isfinite(float(value)) for value in values):
        raise ValueError("Louta policy values must be finite numbers.")
    if corridor_target_t < 0 or any(
            value < 0 for value in wash_targets_t.values()):
        raise ValueError("Louta policy tonnage targets cannot be negative.")
    if residence_min <= 0:
        raise ValueError("Washing residence must be greater than zero minutes.")
    if residence_min > horizon_min:
        raise ValueError(
            "Washing residence must fit inside the planning horizon.")
    if enabled and corridor_target_t <= 0:
        raise ValueError(
            "The stocked-route policy needs a positive C1 supply target.")
    if enabled and sum(wash_targets_t.values()) <= 0:
        raise ValueError(
            "The stocked-route policy needs a positive washing output target.")
    if enabled and not math.isclose(
            corridor_target_t, sum(wash_targets_t.values()),
            rel_tol=0.0, abs_tol=1e-6):
        raise ValueError(
            "C1 supply must equal the combined L1 and L2 washing output.")


def _render_louta_s0_policy(scn) -> None:
    """Visible configuration for the purpose-backed S0 Louta flow policy."""
    if not any(site.id == "LOUTA" for site in scn.sites):
        return
    cfg = scn.config
    wash_cfg = _target_map(cfg.get("LOU_WASH_LINE_OUTPUT_TARGET_T", {}))
    with st.container(border=True):
        st.markdown("#### Configure · Louta stocked-route policy")
        st.caption(
            "This demonstration policy gives Louta transfers a declared "
            "planning purpose. Disable it for a demand-only plan. Values are "
            "scenario inputs and are not hidden optimiser incentives.")
        enabled = st.toggle(
            "Use the stocked Louta route in S0",
            value=bool(cfg.get("LOU_S0_STOCKED_ROUTE_ENABLED", False)),
            key="lou_s0_stocked_route_enabled")
        c = st.columns(4)
        corridor_target_t = float(c[0].number_input(
            "C1 stocked-route target [t]", min_value=0.0, step=100.0,
            value=float(cfg.get("LOU_C1_STOCKED_ROUTE_TARGET_T", 0.0) or 0.0),
            disabled=not enabled, key="lou_c1_stocked_route_target_t"))
        wash_targets_t = {
            stage: float(c[index].number_input(
                f"{stage} output target [t]", min_value=0.0, step=100.0,
                value=float(wash_cfg.get(stage, 0.0)),
                disabled=not enabled,
                key=f"lou_{stage.lower().replace('-', '_')}_output_target_t"))
            for index, stage in enumerate(_LOU_WASH_STAGES, start=1)
        }
        residence_min = float(c[3].number_input(
            "Washing residence [min]", min_value=1.0, step=1.0,
            value=float(cfg.get("LOU_WASHING_RESIDENCE_MIN", 30.0) or 30.0),
            key="lou_washing_residence_min"))

        if st.button("Apply Louta S0 policy",
                     key="apply_louta_s0_policy"):
            try:
                _validate_louta_s0_policy(
                    enabled, corridor_target_t, wash_targets_t,
                    residence_min,
                    scn.sim.horizon_buckets * scn.sim.dt_h * 60.0)
            except (TypeError, ValueError) as exc:
                st.error(theme.operator_text(str(exc)))
            else:
                effective_corridor = corridor_target_t if enabled else 0.0
                effective_wash = {
                    stage: target if enabled else 0.0
                    for stage, target in wash_targets_t.items()
                }
                cfg["LOU_S0_STOCKED_ROUTE_ENABLED"] = bool(enabled)
                cfg["LOU_C1_STOCKED_ROUTE_TARGET_T"] = effective_corridor
                cfg["LOU_WASH_LINE_OUTPUT_TARGET_T"] = effective_wash
                cfg["LOU_WASHING_RESIDENCE_MIN"] = residence_min

                corridor_targets = _target_map(
                    cfg.get("CORRIDOR_DISPATCH_TARGET_T", {}))
                corridor_targets["C1"] = effective_corridor
                cfg["CORRIDOR_DISPATCH_TARGET_T"] = corridor_targets
                process_targets = _target_map(
                    cfg.get("PROCESS_STAGE_OUTPUT_TARGET_T", {}))
                process_targets.update(effective_wash)
                cfg["PROCESS_STAGE_OUTPUT_TARGET_T"] = process_targets
                # Applying this operator policy issues explicit commitments,
                # unlike the forecast-derived preset replenishment targets.
                cfg["PLANNING_PROCESS_STAGE_TARGETS"] = [
                    stage for stage in cfg.get("PLANNING_PROCESS_STAGE_TARGETS", [])
                    if stage not in effective_wash]
                cfg["PLANNING_CORRIDOR_TARGETS"] = [
                    link for link in cfg.get("PLANNING_CORRIDOR_TARGETS", []) if link != "C1"]

                residence_h = _target_map(
                    cfg.get("WASHING_LINE_RESIDENCE_TIME_H", {}))
                residence_h["LOUTA"] = residence_min / 60.0
                cfg["WASHING_LINE_RESIDENCE_TIME_H"] = residence_h
                residence_i = int(round(residence_min))
                for transfer in getattr(scn, "process_transfers", []) or []:
                    if getattr(transfer, "process_stage", "") \
                            in _LOU_WASH_STAGES:
                        transfer.residence_min = residence_i
                    if getattr(transfer, "depends_on_stage", "") \
                            in _LOU_WASH_STAGES:
                        transfer.dependency_delay_min = residence_i

                stale = common.invalidate_outputs()
                common.revalidate()
                st.success(
                    "Louta S0 policy applied"
                    + (" — previous plan cleared" if stale else ""))


def _profile_horizon_total_t(profile: dict, field: str, destination_id: str,
                             horizon_days: float) -> float:
    """Expand a preset's nominal daily quantity over the visible horizon."""
    raw_daily = profile.get(field, {})
    if field == "mph_daily_t" and profile.get("legacy_demand_model"):
        raw_daily = {
            "PAP-1": 36_000.0,
            "PAP-2": 36_000.0,
            "DRY-FEED": 19_200.0,
        }
    daily_t = float(raw_daily.get(destination_id, 0.0)) \
        if isinstance(raw_daily, dict) else float(raw_daily)
    factors_by_destination = profile.get("daily_quantity_factors", {}) or {}
    factors = tuple(factors_by_destination.get(destination_id, ()))
    complete_days = int(math.floor(horizon_days + 1e-9))
    total_factor = sum(
        float(factors[day % len(factors)]) if factors else 1.0
        for day in range(complete_days)
    )
    partial_day = max(horizon_days - complete_days, 0.0)
    if partial_day > 1e-9:
        total_factor += partial_day * (
            float(factors[complete_days % len(factors)])
            if factors else 1.0)
    return daily_t * total_factor


def _render_planning_demand_profile(scn) -> None:
    """Render a compact weekly-template loader above the dated order table."""
    profiles = dict(datasets.PLANNING_DEMAND_PROFILES)
    active = str(scn.config.get(
        "ACTIVE_PLANNING_DEMAND_PROFILE",
        datasets.DEFAULT_PLANNING_DEMAND_PROFILE))
    if active == "CUSTOM_WEEKLY":
        driver = dict(scn.config.get("DEMAND_DRIVER_SUMMARY", {}) or {})
        profiles["CUSTOM_WEEKLY"] = {
            "label": "Custom weekly plan",
            "description": (
                "The current seven-day plant-by-plant campaign edited below. "
                "Select a preset to replace it with a demonstrated weekly pattern."),
            "bg_train_product_t": _demand_quantity_t(scn, "BG-TRAIN-PROD")
            if any(destination.id == "BG-TRAIN-PROD"
                   for destination in scn.destinations) else 0.0,
            "active_mph_demand_t": float(driver.get(
                "active_mph_demand_t", 0.0) or 0.0),
            "corridor_replenishment_t": float(driver.get(
                "corridor_replenishment_t", 0.0) or 0.0),
            "c1_dispatch_t": float(dict(scn.config.get(
                "CORRIDOR_DISPATCH_TARGET_T", {}) or {}).get("C1", 0.0)),
            "c2_dispatch_t": float(dict(scn.config.get(
                "CORRIDOR_DISPATCH_TARGET_T", {}) or {}).get("C2", 0.0)),
        }
    if active not in profiles:
        active = datasets.DEFAULT_PLANNING_DEMAND_PROFILE
    profile_ids = list(profiles)

    st.markdown("#### Demand preset")
    selector_col, action_col = st.columns([4, 1])
    with selector_col:
        selected = st.selectbox(
            "Demand preset",
            options=profile_ids,
            index=profile_ids.index(active),
            format_func=lambda profile_id: profiles[profile_id]["label"],
            key=f"planning_demand_profile_{active.lower()}",
            help=("Loading a preset replaces every dated row in the current "
                  "week. Editing the table creates a custom weekly plan."),
        )
    with action_col:
        st.caption("Replace table")
        load_preset = st.button(
            "Load preset",
            key="apply_planning_demand_profile",
            disabled=selected == "CUSTOM_WEEKLY",
            width="stretch",
        )

    profile = profiles[selected]
    horizon_days = scn.sim.horizon_buckets * scn.sim.dt_h / 24.0
    if selected == "CUSTOM_WEEKLY":
        train_total_t = float(profile["bg_train_product_t"])
        mph_total_t = float(profile["active_mph_demand_t"])
        replenishment_total_t = float(profile["corridor_replenishment_t"])
        c1_total_t = float(profile["c1_dispatch_t"])
        c2_total_t = float(profile["c2_dispatch_t"])
    else:
        train_total_t = _profile_horizon_total_t(
            profile, "bg_train_product_t", "BG-TRAIN-PROD", horizon_days)
        mph_total_t = sum(
            _profile_horizon_total_t(
                profile, "mph_daily_t", destination_id, horizon_days)
            for destination_id in ("PAP-1", "PAP-2", "DRY-FEED")
            if any(destination.id == destination_id
                   for destination in scn.destinations)
            and flow_model.destination_line_operating_state(
                scn, destination_id) != "FUTURE"
        )
        if profile.get("legacy_demand_model"):
            c1_total_t = max(
                float(profile.get("c1_dispatch_t", 0.0)), 0.0
            ) * min(horizon_days, 1.0)
            c2_total_t = max(
                float(profile.get("c2_dispatch_t", 0.0)), 0.0
            ) * horizon_days
            replenishment_total_t = c1_total_t + c2_total_t
        else:
            replenishment_total_t = mph_total_t * float(
                profile.get("corridor_replenishment_share", 1.0))
            c2_total_t = replenishment_total_t * float(
                profile.get("c2_share", 0.40))
            c1_total_t = replenishment_total_t - c2_total_t
    st.caption(
        f"{profile['description']}  Weekly chain: MPH {mph_total_t:,.0f} t → "
        f"same-week replenishment {replenishment_total_t:,.0f} t "
        f"(C1 {c1_total_t:,.0f} + C2 {c2_total_t:,.0f}); "
        f"BG train +{train_total_t:,.0f} t.")

    if load_preset:
        datasets.apply_planning_demand_profile(scn, selected)
        stale = common.invalidate_outputs()
        common.revalidate()
        st.session_state["weekly_demand_notice"] = (
            f"{profiles[selected]['label']} loaded"
            + (" — previous plan cleared" if stale else ""))
        st.rerun()


def _render_pap_line_availability(scn) -> None:
    """Expose the real PAP route group in Plan without changing its demand."""
    if not any(
            destination.id == "PAP-1"
            for destination in scn.destinations):
        return
    status = destination_line_status(scn, "PAP-1")
    if status == "UNMODELLED":
        return

    with st.container(border=True):
        st.markdown("#### PAP-1 receiving line")
        st.caption(
            "Switches both modelled A/B route legs to PAP-1. Disabling the "
            "line keeps its configured demand and grade; the next plan "
            "reports any unreachable quantity as unmet.")
        desired = st.toggle(
            "PAP-1 receiving line available",
            value=status == "ENABLED",
            key=f"pap_line_available_{status.lower()}",
        )
        if status == "PARTIAL":
            st.warning(
                "Only part of the PAP-1 route is currently available. "
                "Applying this setting synchronises both route legs.")
        if st.button(
                "Apply PAP-1 line availability",
                key="apply_pap_line_availability"):
            retained_demand_t = _demand_quantity_t(scn, "PAP-1")
            set_destination_line_enabled(scn, "PAP-1", bool(desired))
            stale = common.invalidate_outputs()
            common.revalidate()
            state_label = "enabled" if desired else "disabled"
            st.success(
                f"PAP-1 line {state_label}; demand retained at "
                f"{retained_demand_t:,.0f} t"
                + (" — previous plan cleared" if stale else ""))


def _commit_blending_enablement(config_key, updated):
    """Persist first so a failed disk write cannot pretend the edit was saved."""
    scn = common.sc()
    staged = deepcopy(scn)
    staged.config[config_key] = updated
    common.save_blend_enablement_preferences(staged)
    scn.config[config_key] = updated
    stale = common.invalidate_outputs()
    common.revalidate()
    st.session_state["blending_enablement_notice"] = (
        "success", "Blending enablement saved automatically."
        + (" Previous plan cleared; generate a new plan." if stale else ""))
    st.session_state.pop("blend_preferences_error", None)


def _checkbox_selection(widget_key, row_ids, current):
    selected = set(current)
    changes = st.session_state.get(widget_key, {}).get("edited_rows", {})
    for row_index, changeset in changes.items():
        index = int(row_index)
        if 0 <= index < len(row_ids) and "Enable blending" in changeset:
            if changeset["Enable blending"] is True:
                selected.add(row_ids[index])
            elif changeset["Enable blending"] is False:
                selected.discard(row_ids[index])
    return sorted(selected)


def _save_prewash_stockpile_enablement(point_id, space_ids, widget_key):
    cfg = prewash_blending.settings(common.sc())
    point = cfg["points"][point_id]
    point["enabled_source_spaces"] = _checkbox_selection(
        widget_key, space_ids, prewash_blending.enabled_source_spaces(point))
    cfg["model_version"] = prewash_blending.MODEL_VERSION
    try:
        _commit_blending_enablement(prewash_blending.CONFIG_KEY, cfg)
    except (blend_preferences.BlendPreferencesError, TypeError, ValueError) as exc:
        st.session_state.pop(widget_key, None)
        st.session_state["blending_enablement_notice"] = (
            "error", f"Enablement was not saved: {exc}")


def _save_prewash_master_enablement(widget_key):
    cfg = prewash_blending.settings(common.sc())
    cfg["enabled"] = bool(st.session_state[widget_key])
    try:
        _commit_blending_enablement(prewash_blending.CONFIG_KEY, cfg)
    except (blend_preferences.BlendPreferencesError, TypeError, ValueError) as exc:
        st.session_state.pop(widget_key, None)
        st.session_state["blending_enablement_notice"] = (
            "error", f"Enablement was not saved: {exc}")


def _save_mph_stockpile_enablement(space_ids, widget_key):
    scn = common.sc()
    cfg = deepcopy(scn.config.get(blending.CHEVRON_CONFIG_KEY, {}))
    requested = _checkbox_selection(
        widget_key, space_ids, blending.chevron_settings(scn)["enabled_spaces"])
    try:
        for space in scn.spaces:
            if not blending.chevron_is_demand_driven(scn) and \
                    space.id in requested and float(space.mass_t) > 1.0 and \
                    blending.chevron_campaign_state(scn, space.id)["status"] == \
                    blending.CAMPAIGN_NOT_STARTED:
                raise ValueError(f"{space.id} is not empty; existing inventory "
                                 "cannot be relabelled as a new blend.")
        cfg.update({"enabled": bool(requested), "enabled_spaces": requested,
                    "proposed_recipes_by_space": {}, "proposed_matrix_by_space": {},
                    "proposed_campaigns_by_space": {}})
        problems = blending.chevron_config_errors(scn, cfg)
        if problems:
            raise ValueError("; ".join(problems))
        _commit_blending_enablement(blending.CHEVRON_CONFIG_KEY, cfg)
    except (blend_preferences.BlendPreferencesError, TypeError, ValueError) as exc:
        st.session_state.pop(widget_key, None)
        st.session_state["blending_enablement_notice"] = (
            "error", f"Enablement was not saved: {exc}")


def _render_blending_matrix_editor(catalog, matrix_columns, *, key, disabled=False):
    """Use the same one-row-per-matrix editor at every blending stage."""
    return st.data_editor(
        matrix_frame(catalog, matrix_columns), hide_index=True, width="stretch",
        num_rows="fixed" if disabled else "dynamic", key=key, disabled=disabled,
        column_config={label: st.column_config.NumberColumn(
            min_value=0.0, max_value=100.0, step=1.0, format="%.2f",
            help="Physical input mass percentage; blank or zero excludes this input.")
            for label in matrix_columns})


def _render_unconfigured_predrying_blending():
    """Mirror the layout without exposing controls the solver cannot honor."""
    st.markdown("#### Blending Policy")
    st.info(
        "Pre-drying blending is not configured in the current model. No "
        "separate blending equipment or physical feed routes are defined for "
        "this stage. The tables remain empty and controls are unavailable "
        "until that process is modeled; MPH settings are not reused here.")
    st.radio(
        "Share selection mode", ["Blend matrix", "Optimized shares"],
        horizontal=True, key="predrying_share_mode", disabled=True)
    st.data_editor(
        pd.DataFrame(columns=[
            "Enable blending", "Stockpile", "Space ID", "Inventory [t]",
            "Capacity [t]", "Campaign state", "Selected by latest plan"]),
        hide_index=True, width="stretch", disabled=True,
        key="predrying_blending_enablement",
        column_config={"Enable blending": st.column_config.CheckboxColumn()})
    st.button("Apply blending policy", type="primary",
              key="apply_predrying_policy", disabled=True)
    st.markdown("#### Blending Matrices")
    with st.expander("Add input-grade columns"):
        st.multiselect(
            "Additional input grades", options=[], disabled=True,
            key="predrying_matrix_extra_input_grades")
    _render_blending_matrix_editor(
        [], {}, key="predrying_matrix_catalog_wide", disabled=True)
    st.caption(
        "One row will represent one complete matrix: Matrix ID and a "
        "percentage column for each input grade. No pre-drying matrices "
        "have been created or assigned.")
    st.button("Apply blending matrices", type="primary",
              key="apply_predrying_matrices", disabled=True)


def _prewash_matrix_input_options(scn, point):
    """Offer real source/grade pairs with feeds to all of this site's lines."""
    stages = set(point.get("process_stages", []))
    source_stages = {}
    for transfer in scn.process_transfers:
        if transfer.process_stage in stages:
            source_stages.setdefault(transfer.source_space, set()).add(
                transfer.process_stage)
    return [{"source_space": space.id, "grade_id": space.grade}
            for space in scn.spaces
            if stages and stages <= source_stages.get(space.id, set())
            and space.site == point.get("site") and space.grade]


def _page_blending_shared_catalogues():
    """Render shared blend catalogues with separate physical enablement."""
    matrix_mode = blending.BLEND_MODE_MATRIX
    optimized_mode = blending.BLEND_MODE_OPTIMIZED
    scn = common.sc()

    st.subheader("Blending")
    notice = st.session_state.pop("blending_enablement_notice", None)
    if notice:
        (st.error if notice[0] == "error" else st.success)(notice[1])
    if st.session_state.get("blend_preferences_error"):
        st.error("Saved blending settings could not be loaded: "
                 + st.session_state["blend_preferences_error"])
    st.caption(
        "Configure reusable recipes by process location. A recipe is never "
        "permanently assigned to a stockpile or washing line: the planner "
        "selects one eligible matrix for each active campaign. Only the "
        "enable switch and physical campaign state are equipment-specific.")
    selected_stage = st.segmented_control(
        "Blending stage", options=list(_BLEND_STAGE_OPTIONS),
        default=_BLEND_STAGE_PRE_WASHING,
        key="blending_stage_selector", selection_mode="single",
        width="stretch") or _BLEND_STAGE_PRE_WASHING

    if selected_stage == _BLEND_STAGE_PRE_DRYING:
        _render_unconfigured_predrying_blending()
        return

    mode_labels = ["Blend matrix", "Optimized shares"]
    mode_by_label = {
        "Blend matrix": matrix_mode, "Optimized shares": optimized_mode}

    if selected_stage == _BLEND_STAGE_PRE_WASHING:
        cfg = prewash_blending.settings(scn)
        points = dict(cfg.get("points", {}) or {})
        if not points:
            st.info("No pre-washing blending locations are configured.")
            return
        ordered = sorted(points, key=lambda key: (
            0 if str(points[key].get("site", "")) == "LOUTA" else 1,
            str(key)))
        location_labels = {
            key: ("Louta pre-washing" if points[key].get("site") == "LOUTA"
                  else "Benguerir pre-washing"
                  if points[key].get("site") == "BENGUERIR"
                  else str(points[key].get("name", key)))
            for key in ordered}
        selected_location = st.segmented_control(
            "Pre-washing location",
            options=[location_labels[key] for key in ordered],
            default=location_labels[ordered[0]],
            key="prewash_location_selector", selection_mode="single",
            width="stretch") or location_labels[ordered[0]]
        point_id = next(
            key for key, label in location_labels.items()
            if label == selected_location)
        point = points[point_id]

        st.markdown("#### Blending Policy")
        enabled = st.toggle(
            "Enable pre-washing feed blending",
            value=cfg.get("enabled") is True,
            key="prewash_master_enabled",
            on_change=_save_prewash_master_enablement,
            args=("prewash_master_enabled",),
            help="Master switch; stockpile-specific enablement is configured below. Saves immediately.")
        current_mode = prewash_blending.point_mode(point)
        selected_mode_label = st.radio(
            "Share selection mode", mode_labels,
            index=0 if current_mode == matrix_mode else 1,
            horizontal=True, key=f"prewash_share_mode_{point_id}",
            help=("This selection applies simultaneously to all enabled "
                  "stockpiles at this location."))
        selected_mode = mode_by_label[selected_mode_label]
        min_input_grade_t = st.number_input(
            "Minimum quantity per input grade [t]", min_value=0.0,
            step=100.0,
            value=float(point.get("min_input_grade_t", 0.0)),
            key=f"prewash_min_input_grade_t_{point_id}",
            help=("Each raw grade actually used by a daily pre-washing "
                  "blend must contribute at least this quantity."))

        stages = list(point.get("process_stages", []))
        enabled_sources = set(prewash_blending.enabled_source_spaces(point))
        optimized_rows = list(point.get(
            "optimized_components", point.get("components", [])))
        catalog = prewash_blending.matrix_catalog(point)
        input_options = component_columns([], _prewash_matrix_input_options(scn, point))
        configured_inputs = component_columns(catalog, optimized_rows)
        source_ids = {str(row["source_space"]) for row in
                      [*input_options.values(), *configured_inputs.values()]
                      if row.get("source_space")}
        source_spaces = sorted([space for space in scn.spaces if space.id in source_ids],
                               key=lambda space: space.id)
        plan = common.plan()
        enable_rows = [{
            "Enable blending": space.id in enabled_sources,
            "Stockpile": space.id,
            "Space ID": space.id,
            "Input grade": space.grade,
            "Inventory [t]": float(space.mass_t),
            "Capacity [t]": float(space.capacity_t),
            "Planned input [t]": (sum(float(job.tonnage_t) for job in plan.jobs
                                      if job.process_stage in stages
                                      and job.source_space == space.id)
                                  if plan is not None else None),
        } for space in source_spaces]
        enablement_key = f"prewash_stockpile_enablement_{point_id}"
        edited_enablement = st.data_editor(
            pd.DataFrame(enable_rows, columns=[
                "Enable blending", "Stockpile", "Space ID",
                "Input grade", "Inventory [t]", "Capacity [t]",
                "Planned input [t]"]), hide_index=True, width="stretch",
            key=enablement_key,
            on_change=_save_prewash_stockpile_enablement,
            args=(point_id, tuple(space.id for space in source_spaces), enablement_key),
            disabled=["Stockpile", "Space ID", "Input grade",
                      "Inventory [t]", "Capacity [t]", "Planned input [t]"],
            column_config={
                "Enable blending": st.column_config.CheckboxColumn(),
                "Inventory [t]": st.column_config.NumberColumn(format="%,.0f"),
                "Capacity [t]": st.column_config.NumberColumn(format="%,.0f"),
                "Planned input [t]": st.column_config.NumberColumn(format="%,.0f"),
            })
        active_sources = list(edited_enablement.loc[
            edited_enablement["Enable blending"] == True, "Space ID"].astype(str))
        st.caption(
            "Checkboxes save automatically. Each switch controls this "
            "stockpile's participation in pre-washing blends, not washing-line "
            "availability. Ordinary unblended washing remains available. "
            "One shared mode and catalogue apply at this location; no matrix "
            "is assigned to a pile. Use Apply blending policy for mode/bound changes.")
        if point_id not in prewash_blending.enabled_points(scn) and enabled:
            st.info("No pre-washing blend is currently eligible at this location. "
                    "Enable at least two compatible source stockpiles and configure "
                    "a feasible matrix or percentage bounds. Ordinary washing is unchanged.")

        edited_bounds = None
        if selected_mode == optimized_mode:
            st.markdown("##### Optimized percentage bounds")
            edited_bounds = st.data_editor(
                pd.DataFrame([{
                    "Source stockpile": row.get("source_space", ""),
                    "Input grade": row.get("grade_id", ""),
                    "Minimum mass [%]": 100.0 * float(
                        row.get("min_fraction", 0.0)),
                    "Maximum mass [%]": 100.0 * float(
                        row.get("max_fraction", 1.0)),
                } for row in optimized_rows]), hide_index=True,
                width="stretch", key=f"prewash_bounds_{point_id}",
                disabled=["Source stockpile", "Input grade"],
                column_config={
                    "Minimum mass [%]": st.column_config.NumberColumn(
                        min_value=0.0, max_value=100.0, step=1.0),
                    "Maximum mass [%]": st.column_config.NumberColumn(
                        min_value=0.0, max_value=100.0, step=1.0),
                })
            st.caption(
                "The planner chooses percentages inside these shared limits "
                "for every enabled line/day campaign.")

        if st.button("Apply blending policy", type="primary",
                     key=f"apply_prewash_blend_{point_id}"):
            updated = deepcopy(cfg)
            updated["model_version"] = prewash_blending.MODEL_VERSION
            updated["enabled"] = bool(enabled)
            updated_point = updated["points"][point_id]
            updated_point["share_mode"] = selected_mode
            updated_point["enabled_source_spaces"] = list(active_sources)
            updated_point["min_input_grade_t"] = float(min_input_grade_t)
            try:
                if selected_mode == optimized_mode and \
                        edited_bounds is not None:
                    updated_point["optimized_components"] = [{
                        "source_space": str(row["Source stockpile"]),
                        "grade_id": str(row["Input grade"]),
                        "min_fraction": float(row["Minimum mass [%]"]) / 100.0,
                        "max_fraction": float(row["Maximum mass [%]"]) / 100.0,
                    } for _, row in edited_bounds.iterrows()]
                updated_point.pop("components", None)
                probe_scenario = deepcopy(scn)
                probe_scenario.config[prewash_blending.CONFIG_KEY] = updated
                problems = prewash_blending.configuration_errors(probe_scenario)
                if problems:
                    raise ValueError("; ".join(problems))
            except (TypeError, ValueError) as exc:
                st.error(f"Cannot apply: {exc}")
            else:
                scn.config[prewash_blending.CONFIG_KEY] = updated
                stale = common.invalidate_outputs()
                common.revalidate()
                st.success(
                    f"{selected_location} policy applied"
                    + (" - previous plan cleared" if stale else ""))
                st.rerun()

        st.markdown("#### Blending Matrices")
        with st.expander("Add input-grade columns"):
            extra_inputs = st.multiselect(
                "Additional input grades", options=list(input_options),
                key=f"prewash_matrix_extra_input_grades_{point_id}",
                help=("Only existing source stockpiles with physical feeds "
                      "to this location's washing lines are offered."))
        matrix_columns = component_columns(catalog, [
            *optimized_rows, *(input_options[label] for label in extra_inputs)])
        edited_matrices = _render_blending_matrix_editor(
            catalog, matrix_columns, key=f"prewash_catalog_wide_{point_id}")
        st.caption(
            "One row is one complete matrix. Each percentage column identifies "
            "an input grade and its source stockpile. Use at least two inputs "
            "totalling 100%; leave unused inputs blank or zero. Add a row for "
            "another matrix. Every matrix is available to all enabled lines "
            "at this location when all its input stockpiles are enabled; "
            "none is assigned in advance. Disabled inputs never cause a "
            "recipe's percentages to be renormalized.")
        if selected_mode != matrix_mode:
            st.info(
                "These matrices remain configured but inactive while the Policy "
                "selection mode is Optimized shares.")
        if st.button("Apply blending matrices", type="primary",
                     key=f"apply_prewash_matrices_{point_id}"):
            updated = deepcopy(cfg)
            updated["model_version"] = prewash_blending.MODEL_VERSION
            updated_point = updated["points"][point_id]
            try:
                updated_point["matrix_catalog"] = parse_matrix_frame(
                    edited_matrices, matrix_columns, catalog)
                probe = deepcopy(updated)
                probe["points"][point_id]["share_mode"] = matrix_mode
                probe_scenario = deepcopy(scn)
                probe_scenario.config[prewash_blending.CONFIG_KEY] = probe
                problems = prewash_blending.configuration_errors(probe_scenario)
                if problems:
                    raise ValueError("; ".join(problems))
            except (TypeError, ValueError) as exc:
                st.error(f"Cannot apply: {exc}")
            else:
                scn.config[prewash_blending.CONFIG_KEY] = updated
                stale = common.invalidate_outputs()
                common.revalidate()
                st.success(
                    f"{selected_location} blending matrices applied"
                    + (" - previous plan cleared" if stale else ""))
                st.rerun()

        st.caption(
            "This is a daily feed-blend campaign through the selected site's "
            "common reclaim header, not spatial pile-face homogenisation.")
        return

    # MPH humid-phosphate stockpiles -----------------------------------------
    settings = blending.chevron_settings(scn)
    mph_spaces = sorted([
        space for space in scn.spaces
        if space.site == "MPH" and space.area == "M16-humid"],
        key=lambda space: space.id)
    if not mph_spaces:
        st.info("This scenario has no M16 humid-phosphate stockpiles.")
        return
    grade_options = [grade.id for grade in scn.grades
                     if grade.material_class == "washed" and grade.quality]

    st.markdown("#### Blending Policy")
    selected_mode_label = st.radio(
        "Share selection mode", mode_labels,
        index=0 if settings["global_mode"] == matrix_mode else 1,
        horizontal=True, key="mph_shared_blend_mode",
        help=("One mode applies to all enabled M16 humid-phosphate "
              "stockpiles simultaneously."))
    selected_mode = mode_by_label[selected_mode_label]
    plan = common.plan()
    enable_rows = []
    for index, space in enumerate(mph_spaces, 1):
        state = blending.chevron_campaign_state(scn, space.id)
        plan_campaign = (getattr(plan, "blend_campaigns", {}).get(
            space.id, {}) if plan is not None else {})
        selected_matrix = str(
            plan_campaign.get("selected_matrix", "") or "")
        if not selected_matrix and state.get("resolved_recipe"):
            selected_matrix = "Physically locked recipe"
        enable_rows.append({
            "Enable blending": space.id in settings["enabled_spaces"],
            "Stockpile": f"Humid-phosphate stockpile {index}",
            "Space ID": space.id,
            "Inventory [t]": float(space.mass_t),
            "Capacity [t]": float(space.capacity_t),
            "Campaign state": str(state["status"]).replace("_", " ").title(),
            "Selected by latest plan": selected_matrix or "-",
        })
    edited_enablement = st.data_editor(
        pd.DataFrame(enable_rows), hide_index=True, width="stretch",
        key="mph_blending_enablement",
        on_change=_save_mph_stockpile_enablement,
        args=(tuple(space.id for space in mph_spaces), "mph_blending_enablement"),
        disabled=["Stockpile", "Space ID", "Inventory [t]", "Capacity [t]",
                  "Campaign state", "Selected by latest plan"],
        column_config={
            "Enable blending": st.column_config.CheckboxColumn(),
            "Inventory [t]": st.column_config.NumberColumn(format="%,.0f"),
            "Capacity [t]": st.column_config.NumberColumn(format="%,.0f"),
        })
    st.caption(
        "Checkboxes save automatically. This is the only stockpile-specific "
        "policy input. Enabling a pile "
        "makes every shared matrix available; it does not assign one. "
        "Existing stock remains available for normal reclaim. A new blend "
        "can start only when the pile is empty at a planning-window boundary.")
    requested = set(edited_enablement.loc[
        edited_enablement["Enable blending"] == True, "Space ID"].astype(str))

    min_input_grade_t = st.number_input(
        "Minimum quantity per input grade [t]", min_value=0.0, step=100.0,
        value=float(settings.get("min_input_grade_t", 500.0)),
        key="mph_shared_min_input_grade_t",
        help=("Each grade used in a blend must contribute at least this "
              "quantity. The planner chooses the total blended quantity "
              "from demand, available material and stockpile capacity."))
    st.caption(
        "The planner determines each stockpile's blended quantity and "
        "resulting grade. Reclaim becomes available after its actual blend "
        "construction is complete; there is no fixed batch or ready hour.")
    with st.expander("Physical stacking settings"):
        stacking_traverse_speed = st.number_input(
            "Stacking traverse speed [m/min]", min_value=0.0, step=1.0,
            value=float(settings.get("stacking_traverse_speed_m_min") or 0.0),
            key="mph_stacking_traverse_speed_m_min",
            help=("Set the operating travel speed while stacking. Zero "
                  "means unknown. Pass estimates require this speed and "
                  "the stockpile traversal length, together with the "
                  "planned tonnes and stacking flow rate."))
        st.caption(
            "Chevron passes are derived from stacking duration and travel "
            "time. The plan leaves the estimate unavailable when the "
            "required physical data is missing.")

    edited_bounds = None
    if selected_mode == optimized_mode:
        st.markdown("##### Optimized percentage bounds")
        edited_bounds = st.data_editor(
            pd.DataFrame([{
                "Eligible input grade": row.get("grade_id", ""),
                "Minimum mass [%]": 100.0 * float(
                    row.get("min_fraction", 0.0)),
                "Maximum mass [%]": 100.0 * float(
                    row.get("max_fraction", 1.0)),
            } for row in settings["optimized_share_bounds"]]),
            hide_index=True, width="stretch", num_rows="dynamic",
            key="mph_shared_optimized_bounds",
            column_config={
                "Eligible input grade": st.column_config.SelectboxColumn(
                    options=grade_options, required=True),
                "Minimum mass [%]": st.column_config.NumberColumn(
                    min_value=0.0, max_value=100.0, step=1.0),
                "Maximum mass [%]": st.column_config.NumberColumn(
                    min_value=0.0, max_value=100.0, step=1.0),
            })

    if st.button("Apply blending policy", type="primary",
                 key="apply_mph_policy"):
        updated = deepcopy(dict(scn.config.get(
            blending.CHEVRON_CONFIG_KEY, {}) or {}))
        try:
            updated.update({
                "model_version": blending.CHEVRON_MODEL_VERSION,
                "quantity_mode": "DEMAND_DRIVEN",
                "global_mode": selected_mode,
                "min_input_grade_t": float(min_input_grade_t),
                "stacking_traverse_speed_m_min":
                    float(stacking_traverse_speed) or None,
                "enabled": bool(requested),
                "enabled_spaces": sorted(requested),
                "proposed_recipes_by_space": {},
                "proposed_matrix_by_space": {},
                "proposed_campaigns_by_space": {},
            })
            if selected_mode == optimized_mode and edited_bounds is not None:
                updated["optimized_share_bounds"] = [{
                    "grade_id": str(row["Eligible input grade"]),
                    "min_fraction": float(row["Minimum mass [%]"]) / 100.0,
                    "max_fraction": float(row["Maximum mass [%]"]) / 100.0,
                } for _, row in edited_bounds.iterrows()]
            for legacy_key in (
                    "mode_by_space", "recipes", "share_bounds_by_space",
                    "minimum_component_fraction_by_space", "result_grades",
                    "batch_t_by_space", "ready_minute_by_space",
                    "ready_bucket_by_space", "global_result_grade",
                    "global_batch_t", "global_ready_minute",
                    "global_minimum_component_fraction", "passes_per_cycle"):
                updated.pop(legacy_key, None)
            problems = blending.chevron_config_errors(scn, updated)
            if problems:
                raise ValueError("; ".join(problems))
        except (TypeError, ValueError) as exc:
            st.error(f"Cannot apply: {exc}")
        else:
            scn.config[blending.CHEVRON_CONFIG_KEY] = updated
            stale = common.invalidate_outputs()
            common.revalidate()
            st.success("MPH blending policy applied" + (
                " - previous plan cleared" if stale else ""))
            st.rerun()

    st.markdown("#### Blending Matrices")
    catalog = blending.chevron_matrix_catalog(scn)
    existing_inputs = {part["grade_id"] for entry in catalog for part in entry["components"]}
    with st.expander("Add input-grade columns"):
        extra_inputs = st.multiselect(
            "Additional input grades", options=grade_options,
            key="mph_matrix_extra_input_grades",
            help="Add percentage columns for grades needed in a new or existing matrix.")
    input_columns = [*extra_inputs]
    if not catalog and not input_columns:
        input_columns = [row["grade_id"] for row in settings["optimized_share_bounds"]]
    matrix_columns = component_columns(catalog, (
        {"grade_id": grade_id} for grade_id in input_columns if grade_id not in existing_inputs))
    edited_matrices = _render_blending_matrix_editor(
        catalog, matrix_columns, key="mph_shared_matrix_catalog_wide")
    st.caption(
        "One row is one complete matrix: ID and the percentage of each "
        "input grade. Use at least two grades totalling 100%; leave unused "
        "grades blank or zero. Add a row for another matrix. Every matrix is "
        "eligible at every enabled MPH stockpile; none is assigned in advance.")
    if selected_mode != matrix_mode:
        st.info(
            "These matrices remain configured but inactive while the Policy "
            "selection mode is Optimized shares.")

    if st.button("Apply blending matrices", type="primary",
                 key="apply_mph_matrices"):
        updated = deepcopy(dict(scn.config.get(
            blending.CHEVRON_CONFIG_KEY, {}) or {}))
        try:
            matrix_catalog = parse_matrix_frame(edited_matrices, matrix_columns, catalog)
            updated.update({
                "model_version": blending.CHEVRON_MODEL_VERSION,
                "quantity_mode": "DEMAND_DRIVEN",
                "min_input_grade_t": float(settings.get(
                    "min_input_grade_t", 500.0)),
                "matrix_catalog": matrix_catalog,
                "proposed_recipes_by_space": {},
                "proposed_matrix_by_space": {},
                "proposed_campaigns_by_space": {},
            })
            for legacy_key in (
                    "mode_by_space", "recipes", "share_bounds_by_space",
                    "minimum_component_fraction_by_space", "result_grades",
                    "batch_t_by_space", "ready_minute_by_space",
                    "ready_bucket_by_space", "global_result_grade",
                    "global_batch_t", "global_ready_minute",
                    "global_minimum_component_fraction", "passes_per_cycle"):
                updated.pop(legacy_key, None)
            probe_space = next(
                (space for space in mph_spaces if float(space.mass_t) <= 1.0),
                mph_spaces[0])
            probe = deepcopy(updated)
            probe["global_mode"] = matrix_mode
            probe["enabled"] = True
            probe["enabled_spaces"] = [probe_space.id]
            probe["campaign_states_by_space"] = {}
            problems = blending.chevron_config_errors(scn, probe)
            problems = [problem for problem in problems
                        if "requires an empty stockpile" not in problem]
            if problems:
                raise ValueError("; ".join(problems))
        except (TypeError, ValueError) as exc:
            st.error(f"Cannot apply: {exc}")
        else:
            scn.config[blending.CHEVRON_CONFIG_KEY] = updated
            stale = common.invalidate_outputs()
            common.revalidate()
            st.success("MPH blending matrices applied" + (
                " - previous plan cleared" if stale else ""))
            st.rerun()

    st.info(
        "For each new enabled pile campaign, matrix mode selects exactly one "
        "feasible configured matrix. Optimized mode instead chooses shares "
        "inside the Policy bounds. Once physical stacking starts, the "
        "resolved recipe is frozen for that campaign.")


def page_blending():
    return _page_blending_shared_catalogues()

    # Legacy renderer retained temporarily below for saved-widget key
    # compatibility; the shared-catalogue implementation above owns the page.
    """Configure blending where it is physically created: on an M16 pile."""
    matrix_mode = getattr(blending, "BLEND_MODE_MATRIX", "MATRIX")
    optimized_mode = getattr(blending, "BLEND_MODE_OPTIMIZED", "OPTIMIZED")

    def _component_fraction(component, batch_t=0.0):
        """Read persisted matrix rows and solver-resolved tonne rows."""
        if not isinstance(component, dict):
            return 0.0
        try:
            explicit = component.get(
                "target_fraction", component.get("mass_fraction", None))
            if explicit is not None:
                return max(float(explicit), 0.0)
            if batch_t > 0:
                return max(float(component.get("tonnage_t", 0.0)), 0.0) \
                    / float(batch_t)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
        return 0.0

    def _composition_label(components, batch_t=0.0):
        rows = []
        for component in components or ():
            grade_id = str(component.get("grade_id", "") or "").strip()
            share = _component_fraction(component, batch_t)
            if grade_id and share > 0:
                rows.append(f"{grade_id} {share * 100:.1f}%")
        return " + ".join(rows)

    def _weighted_quality(components, batch_t=0.0):
        """Return quality only when the composition is fully resolved."""
        fractions = [_component_fraction(row, batch_t)
                     for row in components or ()]
        if not fractions or abs(sum(fractions) - 1.0) > 1e-6:
            return {}
        normalized = [{
            "grade_id": str(component.get("grade_id", "") or ""),
            "target_fraction": share,
        } for component, share in zip(components, fractions) if share > 0]
        return blending.blended_quality_for_components(scn, normalized)

    def _route_eligible_grades(target_space_id):
        """Washed qualities with an inbound route to this exact stockpile."""
        washed_ids = [
            grade.id for grade in scn.grades
            if grade.material_class == "washed" and grade.quality
        ]
        if not washed_ids:
            return []
        try:
            from mph_sim.optimizer import build_candidates

            # Candidate generation applies the current blend policy. Probe a
            # copy with all washed grades so the old matrix does not prevent
            # the operator from selecting a newly eligible quality.
            probe = deepcopy(scn)
            raw_policy = dict(probe.config.get(
                blending.CHEVRON_CONFIG_KEY, {}) or {})
            enabled_probe = set(raw_policy.get("enabled_spaces", []) or [])
            enabled_probe.add(target_space_id)
            recipes_probe = dict(raw_policy.get("recipes", {}) or {})
            recipes_probe[target_space_id] = [
                {"grade_id": grade_id,
                 "target_fraction": 1.0 / len(washed_ids)}
                for grade_id in washed_ids
            ]
            modes_probe = dict(raw_policy.get("mode_by_space", {}) or {})
            modes_probe[target_space_id] = matrix_mode
            states_probe = dict(raw_policy.get(
                "campaign_states_by_space", {}) or {})
            states_probe.pop(target_space_id, None)
            raw_policy.update({
                "model_version": max(
                    int(raw_policy.get("model_version", 0) or 0), 5),
                "enabled": True,
                "enabled_spaces": sorted(enabled_probe),
                "recipes": recipes_probe,
                "mode_by_space": modes_probe,
                "campaign_states_by_space": states_probe,
            })
            probe.config[blending.CHEVRON_CONFIG_KEY] = raw_policy
            candidates, _ = build_candidates(
                probe, RouteGraph.from_scenario(probe))
            target_pairs = {
                (str(candidate.exit_node), str(candidate.grade))
                for candidate in candidates
                if candidate.kind == "stack"
                and candidate.space == target_space_id
            }
            incoming_pairs = {
                (str(candidate.exit_node), str(candidate.grade))
                for candidate in candidates if candidate.kind == "dispatch"
            }
            for parcel in probe.belt_profile:
                try:
                    incoming_pairs.add((
                        str(probe.link(parcel.link_id).dst),
                        str(parcel.grade)))
                except StopIteration:
                    continue
            eligible = {
                grade_id for exit_node, grade_id in target_pairs
                if (exit_node, grade_id) in incoming_pairs
            }
            return [grade_id for grade_id in washed_ids
                    if grade_id in eligible]
        except (AttributeError, KeyError, TypeError, ValueError):
            return []

    st.subheader("Blending")
    scn = common.sc()
    st.caption(
        "Review blending matrices by process stage. MPH humid-phosphate "
        "stockpiles share one matrix catalogue; individual stockpiles retain "
        "separate enablement and physical campaign state.")

    selected_stage = st.segmented_control(
        "Blending stage",
        options=list(_BLEND_STAGE_OPTIONS),
        default=_BLEND_STAGE_MPH_HUMID,
        key="blending_stage_selector",
        selection_mode="single",
        width="stretch",
    ) or _BLEND_STAGE_MPH_HUMID

    st.markdown("#### Blending matrix catalogue")
    if selected_stage == _BLEND_STAGE_PRE_WASHING:
        cfg = prewash_blending.settings(scn)
        currently_enabled = cfg.get("enabled") is True
        enabled = st.toggle(
            "Enable pre-washing feed blending",
            value=currently_enabled,
            help=("Applies the configured source-share limits separately to "
                  "each washing line and civil day. All components use one "
                  "common washing option in that daily campaign."),
        )
        if enabled != currently_enabled:
            cfg["enabled"] = enabled
            scn.config[prewash_blending.CONFIG_KEY] = cfg
            common.invalidate_outputs()
            common.revalidate()
            st.rerun()

        points = dict(cfg.get("points", {}) or {})
        if not points:
            st.info("No pre-washing blending locations are configured.")
            return
        point_order = sorted(
            points,
            key=lambda point_id: (
                0 if str(points[point_id].get("site", "")) == "LOUTA"
                else 1,
                str(point_id),
            ),
        )
        location_labels = {
            point_id: (
                "Louta pre-washing"
                if str(points[point_id].get("site", "")) == "LOUTA"
                else "Benguerir pre-washing"
                if str(points[point_id].get("site", "")) == "BENGUERIR"
                else str(points[point_id].get("name", point_id))
            )
            for point_id in point_order
        }
        point_by_label = {
            label: point_id for point_id, label in location_labels.items()
        }
        selected_location = st.segmented_control(
            "Pre-washing location",
            options=[location_labels[point_id] for point_id in point_order],
            default=location_labels[point_order[0]],
            key="prewash_location_selector",
            selection_mode="single",
            width="stretch",
        ) or location_labels[point_order[0]]
        point_id = point_by_label[selected_location]
        point = points[point_id]
        current_mode = prewash_blending.point_mode(point)
        mode_labels = ["Blend matrix", "Optimized shares"]
        mode_by_label = {
            "Blend matrix": prewash_blending.BLEND_MODE_MATRIX,
            "Optimized shares": prewash_blending.BLEND_MODE_OPTIMIZED,
        }
        selected_mode_label = st.radio(
            "Share selection mode",
            mode_labels,
            index=(0 if current_mode == prewash_blending.BLEND_MODE_MATRIX
                   else 1),
            horizontal=True,
            key=f"prewash_share_mode_{point_id}",
            help=("Blend matrix fixes every source percentage. Optimized "
                  "shares lets the planner choose percentages inside the "
                  "configured minimum and maximum limits."),
        )
        selected_mode = mode_by_label[selected_mode_label]

        st.markdown(f"##### {point.get('name', point_id)} · {point_id}")
        available_stages = list(point.get("process_stages", []))
        st.caption(
            "Physically routeable washing lines: "
            + ", ".join(str(stage) for stage in available_stages))
        active_stages = st.multiselect(
            "Apply this blend policy to washing lines",
            options=available_stages,
            default=[stage for stage in point.get(
                "enabled_process_stages", available_stages)
                     if stage in available_stages],
            key=f"prewash_blend_stages_{point_id}",
            help=("Only selected lines receive cross-stockpile candidates "
                  "and daily share constraints."),
        )

        component_rows = list(point.get("components", []))
        configured_targets = []
        for component in component_rows:
            try:
                configured_targets.append(float(
                    component.get("target_fraction")))
            except (TypeError, ValueError):
                configured_targets = []
                break
        if not configured_targets or not math.isclose(
                sum(configured_targets), 1.0, abs_tol=1e-9):
            # No approved fixed matrices are currently available. Keep the
            # matrix percentages blank instead of inventing a 50/50 recipe.
            configured_targets = [None] * len(component_rows)

        if selected_mode == prewash_blending.BLEND_MODE_MATRIX:
            frame = pd.DataFrame([{
                "Source stockpile": component.get("source_space", ""),
                "Input grade": component.get("grade_id", ""),
                "Fixed mass [%]": (
                    None if target is None else 100.0 * target),
            } for component, target in zip(
                component_rows, configured_targets)])
            editor_columns = {
                "Fixed mass [%]": st.column_config.NumberColumn(
                    min_value=0.0, max_value=100.0, step=1.0),
            }
        else:
            frame = pd.DataFrame([{
                "Source stockpile": component.get("source_space", ""),
                "Input grade": component.get("grade_id", ""),
                "Minimum mass [%]": 100.0 * float(
                    component.get("min_fraction", 0.0)),
                "Maximum mass [%]": 100.0 * float(
                    component.get("max_fraction", 1.0)),
            } for component in component_rows])
            editor_columns = {
                "Minimum mass [%]": st.column_config.NumberColumn(
                    min_value=0.0, max_value=100.0, step=1.0),
                "Maximum mass [%]": st.column_config.NumberColumn(
                    min_value=0.0, max_value=100.0, step=1.0),
            }
        edited = st.data_editor(
            frame, hide_index=True, width="stretch",
            key=f"prewash_blend_{point_id}_{selected_mode}",
            disabled=["Source stockpile", "Input grade"],
            column_config=editor_columns,
        )

        if st.button(
                "Apply pre-washing blend policy",
                key=f"apply_prewash_blend_{point_id}"):
            updated = deepcopy(cfg)
            updated["model_version"] = prewash_blending.MODEL_VERSION
            updated_point = updated["points"][point_id]
            updated_point["share_mode"] = selected_mode
            updated_point["enabled_process_stages"] = list(active_stages)
            original_by_source = {
                str(component.get("source_space", "")): deepcopy(component)
                for component in component_rows
            }
            rows = []
            for _, row in edited.iterrows():
                source = str(row["Source stockpile"])
                component = original_by_source[source]
                component["grade_id"] = str(row["Input grade"])
                if selected_mode == prewash_blending.BLEND_MODE_MATRIX:
                    fixed_value = row["Fixed mass [%]"]
                    component["target_fraction"] = (
                        None if pd.isna(fixed_value)
                        else float(fixed_value) / 100.0)
                else:
                    component["min_fraction"] = float(
                        row["Minimum mass [%]"]) / 100.0
                    component["max_fraction"] = float(
                        row["Maximum mass [%]"]) / 100.0
                rows.append(component)
            updated_point["components"] = rows
            problems = prewash_blending.configuration_errors(updated)
            if problems:
                st.error("Cannot apply: " + "; ".join(problems))
            else:
                scn.config[prewash_blending.CONFIG_KEY] = updated
                stale = common.invalidate_outputs()
                common.revalidate()
                st.success(
                    f"{selected_location} blend policy applied"
                    + (" — previous plan cleared" if stale else ""))
                st.rerun()

        effective_components = prewash_blending.effective_components(point)
        if current_mode == prewash_blending.BLEND_MODE_MATRIX:
            rule = " + ".join(
                f"{row.get('grade_id', '—')} from "
                f"{row.get('source_space', '—')} "
                f"{100 * float(row.get('target_fraction', 0)):.0f}%"
                for row in effective_components)
            configured_mode_label = "Blend matrix"
        else:
            rule = " + ".join(
                f"{row.get('grade_id', '—')} from "
                f"{row.get('source_space', '—')} "
                f"{100 * float(row.get('min_fraction', 0)):.0f}–"
                f"{100 * float(row.get('max_fraction', 0)):.0f}%"
                for row in effective_components)
            configured_mode_label = "Optimized shares"
        treatment_label = "/".join(
            str(mode).title() for mode in point.get(
                "allowed_treatment_modes", ["simple", "full"]))
        matrix_rows = [{
            "Matrix": point_id,
            "Mode": f"{configured_mode_label} · daily · {treatment_label}",
            "Inputs / share rule": rule,
            "Resulting grade": "Determined by assays + washing mode",
            "Batch [t]": None,
            "Ready hour": None,
            "Applicable stockpiles": ", ".join(
                str(row.get("source_space", ""))
                for row in effective_components),
            "Currently assigned": ", ".join(
                str(stage) for stage in point.get(
                    "enabled_process_stages", point.get(
                    "process_stages", []))) or "—",
            "Enabled at": (str(point.get("site", ""))
                           if currently_enabled and point.get(
                               "enabled_process_stages", []) else "—"),
        }]
        st.markdown("##### Current configured policy")
        st.dataframe(
            pd.DataFrame(matrix_rows, columns=list(_BLEND_MATRIX_COLUMNS)),
            hide_index=True, width="stretch")
        st.caption(
            "This is a daily feed-blend campaign through the selected site's "
            "common reclaim header, not a claim of spatial pile-face "
            "homogenisation. Fixed matrices are exact percentages; optimized "
            "shares are decision variables inside the displayed limits.")
        return

    if selected_stage != _BLEND_STAGE_MPH_HUMID:
        st.dataframe(
            pd.DataFrame(columns=list(_BLEND_MATRIX_COLUMNS)),
            hide_index=True,
            width="stretch",
        )
        location = (
            "before the washing lines"
            if selected_stage == _BLEND_STAGE_PRE_WASHING
            else "before the drying plant"
        )
        st.info(
            f"No verified blending matrices are configured {location}. "
            "The rows are intentionally empty until approved input grades, "
            "share rules, resulting grades and physical blending points are "
            "provided. These empty catalogues do not create optimizer "
            "decisions."
        )
        return

    st.caption(
        "Each row is a reusable MPH matrix definition, not a separate recipe "
        "for every physically similar humid-phosphate stockpile. Choose the "
        "target stockpile below to enable construction and track its campaign."
    )

    mph_spaces = sorted(
        (space for space in scn.spaces
         if space.site == "MPH" and space.area == "M16-humid"),
        key=lambda space: space.id,
    )
    if not mph_spaces:
        st.info("This scenario has no M16 humid-phosphate stockpiles.")
        return

    settings = blending.chevron_settings(scn)
    enabled_spaces = set(settings["enabled_spaces"])
    showcase = dict(scn.config.get("BLENDING_SHOWCASE", {}) or {})
    if showcase:
        st.info(
            f"Showcase active at **{showcase.get('location', 'MPH M16 · HP-07')}** — "
            f"{showcase.get('purpose', 'blending campaign')}. Recipe: "
            f"**{showcase.get('recipe', 'not set')}**; calculated reference: "
            f"**{showcase.get('quality_reference', 'not available')}**. "
            f"{showcase.get('validation_scope', '')}"
        )
    labels = {
        space.id: f"Humid-phosphate stockpile {index}"
        for index, space in enumerate(mph_spaces, 1)
    }
    id_by_label = {label: space_id for space_id, label in labels.items()}
    policy_spaces = sorted((
        set(str(space_id) for space_id in settings["recipes"])
        | set(str(space_id)
              for space_id in settings["share_bounds_by_space"])
        | set(str(space_id)
              for space_id in settings["proposed_recipes_by_space"])
        | set(str(space_id) for space_id in settings["mode_by_space"])
    ).intersection(labels))
    matrix_groups = {}
    for space_id in policy_spaces:
        mode = blending.chevron_mode(scn, space_id)
        if mode == matrix_mode:
            components = blending.chevron_recipe(scn, space_id)
            component_signature = tuple(sorted(
                (str(row.get("grade_id", "")),
                 round(float(row.get("target_fraction", 0.0)), 8))
                for row in components
                if str(row.get("grade_id", ""))))
            policy_text = _composition_label(components)
            mode_label = "Blend matrix"
        else:
            components = blending.chevron_component_bounds(scn, space_id)
            component_signature = tuple(sorted(
                (str(row.get("grade_id", "")),
                 round(float(row.get("min_fraction", 0.0)), 8),
                 round(float(row.get("max_fraction", 0.0)), 8))
                for row in components
                if str(row.get("grade_id", ""))))
            policy_text = " + ".join(
                f"{row['grade_id']} "
                f"{100 * float(row['min_fraction']):.0f}-"
                f"{100 * float(row['max_fraction']):.0f}%"
                for row in components)
            mode_label = "Optimized shares"
        result_grade = str(settings["result_grades"].get(
            space_id, "") or "")
        batch_t = float(settings["batch_t_by_space"].get(
            space_id, 0) or 0)
        ready_hour = float(settings["ready_minute_by_space"].get(
            space_id, 0) or 0) / 60.0
        signature = (
            mode, component_signature, result_grade,
            round(batch_t, 6), round(ready_hour, 6),
        )
        group = matrix_groups.setdefault(signature, {
            "mode": mode_label,
            "policy": policy_text,
            "result_grade": result_grade,
            "batch_t": batch_t,
            "ready_hour": ready_hour,
            "assigned": [],
            "enabled": [],
        })
        group["assigned"].append(space_id)
        if space_id in enabled_spaces:
            group["enabled"].append(space_id)

    matrix_rows = []
    space_to_matrix = {}
    for index, group in enumerate(matrix_groups.values(), 1):
        matrix_id = f"MPH-HUMID-{index:02d}"
        for space_id in group["assigned"]:
            space_to_matrix[space_id] = matrix_id
        matrix_rows.append({
            "Matrix": matrix_id,
            "Mode": group["mode"],
            "Inputs / share rule": group["policy"] or "Not configured",
            "Resulting grade": group["result_grade"] or "—",
            "Batch [t]": group["batch_t"],
            "Ready hour": group["ready_hour"],
            "Applicable stockpiles": f"All {len(mph_spaces)} M16 stockpiles",
            "Currently assigned": ", ".join(group["assigned"]) or "—",
            "Enabled at": ", ".join(group["enabled"]) or "—",
        })

    summary_cols = st.columns(3)
    summary_cols[0].metric("M16 stockpiles", len(mph_spaces))
    summary_cols[1].metric("Shared matrices", len(matrix_rows))
    summary_cols[2].metric(
        "Enabled stockpiles", len(enabled_spaces.intersection(labels)))
    st.dataframe(
        pd.DataFrame(matrix_rows, columns=list(_BLEND_MATRIX_COLUMNS)),
        hide_index=True,
        width="stretch",
        column_config={
            "Batch [t]": st.column_config.NumberColumn(format="%,.0f"),
            "Ready hour": st.column_config.NumberColumn(format="%.0f"),
        },
    )

    overview = []
    for space in mph_spaces:
        campaign_state = blending.chevron_campaign_state(scn, space.id)
        campaign_status = str(campaign_state["status"]).replace("_", " ").title()
        switch_status = "enabled" if space.id in enabled_spaces else "disabled"
        overview.append({
            "Stockpile": labels[space.id],
            "Inventory [t]": float(space.mass_t),
            "Capacity [t]": float(space.capacity_t),
            "Assigned matrix": space_to_matrix.get(space.id, "—"),
            "Construction [t]": sum(
                float(value) for value in campaign_state.get(
                    "stacked_t_by_grade", {}).values()),
            "State": f"{campaign_status} · {switch_status}",
        })
    st.markdown("#### MPH stockpile application and campaign state")
    st.dataframe(
        pd.DataFrame(overview), hide_index=True, width="stretch",
        column_config={
            "Inventory [t]": st.column_config.NumberColumn(format="%,.0f"),
            "Capacity [t]": st.column_config.NumberColumn(format="%,.0f"),
            "Construction [t]": st.column_config.NumberColumn(
                format="%,.0f"),
        },
    )

    st.markdown("#### Apply a shared matrix to one stockpile")
    st.caption(
        "Select the physical stockpile that will construct the blend. An "
        "unassigned stockpile starts from the first shared MPH matrix above; "
        "applying the form creates its own campaign record for execution."
    )
    default_space_id = next(
        (space.id for space in mph_spaces
         if space.id in settings["recipes"]), mph_spaces[0].id)
    template_space_id = (
        "HP-07" if "HP-07" in policy_spaces else
        policy_spaces[0] if policy_spaces else default_space_id
    )
    label_options = list(id_by_label)
    selected_label = st.selectbox(
        "Stockpile", label_options,
        index=label_options.index(labels[default_space_id]),
        help=("Use the global flow diagram to relate this numbered stockpile "
              "to its physical M16 bay."),
    )
    selected_id = id_by_label[selected_label]
    selected_space = scn.space(selected_id)
    policy_source_id = (
        selected_id if selected_id in policy_spaces else template_space_id)
    current_recipe = blending.chevron_recipe(scn, policy_source_id)
    current_matrix = [
        dict(row) for row in settings["recipes"].get(policy_source_id, [])
        if isinstance(row, dict)
    ]
    current_mode = blending.chevron_mode(scn, policy_source_id)
    current_bounds = blending.chevron_component_bounds(
        scn, policy_source_id)
    current_resolved = blending.chevron_resolved_recipe(scn, selected_id)
    current_campaign = blending.chevron_campaign_state(scn, selected_id)
    current_status = str(current_campaign["status"])
    current_plan = common.plan()
    plan_campaign = {}
    if current_plan is not None:
        candidate = getattr(current_plan, "blend_campaigns", {}).get(
            selected_id, {})
        if isinstance(candidate, dict):
            plan_campaign = candidate

    with st.container(border=True):
        available_t = max(float(selected_space.capacity_t)
                          - float(selected_space.mass_t), 0.0)
        st.markdown(f"**{selected_label}**")
        st.caption(
            f"Current inventory {selected_space.mass_t:,.0f} t / "
            f"{selected_space.capacity_t:,.0f} t · "
            f"free capacity {available_t:,.0f} t · campaign "
            f"{current_status.replace('_', ' ').lower()}.")
        can_enable = (
            float(selected_space.mass_t) <= 1.0 or
            current_status in {
                blending.CAMPAIGN_BUILDING,
                blending.CAMPAIGN_COMPLETE,
            } or selected_id in enabled_spaces)
        enabled = st.toggle(
            "Enable blend construction on this stockpile",
            value=selected_id in enabled_spaces,
            key=f"blend_enabled_{selected_id}",
            disabled=not can_enable,
        )
        if not can_enable:
            st.warning(
                "A new blend can only start on an empty stockpile. Existing "
                "inventory is never relabelled as a blend.")
        campaign_locked = current_status in {
            blending.CAMPAIGN_BUILDING,
            blending.CAMPAIGN_COMPLETE,
        }
        if campaign_locked:
            st.info(
                "Construction has started, so its mode, resolved matrix, "
                "batch and ready time are locked. The switch may pause "
                "future construction; "
                "completed blend inventory keeps its identity.")
        if current_status == blending.CAMPAIGN_DEPLETED and \
                float(selected_space.mass_t) <= 1.0:
            st.warning(
                "The previous campaign is depleted. Reset it before planning "
                "a new batch on this empty stockpile.")
            if st.button(
                    "Reset depleted campaign",
                    key=f"reset_blend_campaign_{selected_id}"):
                raw_policy = dict(scn.config.get(
                    blending.CHEVRON_CONFIG_KEY, {}) or {})
                states = dict(raw_policy.get(
                    "campaign_states_by_space", {}) or {})
                states.pop(selected_id, None)
                raw_policy["campaign_states_by_space"] = states
                proposals = dict(raw_policy.get(
                    "proposed_recipes_by_space", {}) or {})
                proposals.pop(selected_id, None)
                raw_policy["proposed_recipes_by_space"] = proposals
                raw_policy["enabled_spaces"] = [
                    space_id for space_id in raw_policy.get(
                        "enabled_spaces", []) if space_id != selected_id]
                raw_policy["enabled"] = bool(raw_policy["enabled_spaces"])
                scn.config[blending.CHEVRON_CONFIG_KEY] = raw_policy
                common.invalidate_outputs()
                common.revalidate()
                st.rerun()

        mode_labels = ["Blend matrix", "Optimized shares"]
        mode_by_label = {
            "Blend matrix": matrix_mode,
            "Optimized shares": optimized_mode,
        }
        mode_label = st.radio(
            "Share selection mode",
            mode_labels,
            index=0 if current_mode == matrix_mode else 1,
            horizontal=True,
            key=f"blend_share_mode_{selected_id}",
            disabled=not enabled or campaign_locked,
            help=("Blend matrix uses fixed mass percentages. Optimized shares "
                  "lets the planner select percentages within configured "
                  "minimum and maximum limits."),
        )
        selected_mode = mode_by_label[mode_label]
        if selected_mode == matrix_mode:
            st.caption(
                "The matrix percentages are fixed inputs to the next plan.")
        else:
            st.caption(
                "The input qualities and limits are fixed; their final mass "
                "percentages are decision variables in the next plan. The "
                "planner selects the matrix together with the dispatch and "
                "stacking schedule, using available inventory, timed outages, "
                "derates, conveyor capacity, competing equipment and "
                "downstream receiving specifications. Its construction "
                "lookahead always reaches the reclaim-ready boundary. "
                f"Quality priorities use w2={scn.weights.w2_offspec:g} "
                f"and target/give-away weight w4={scn.weights.w4_giveaway:g}. "
                "Hard limits cannot be relaxed; soft deviations remain "
                "visible in the result.")

        result_grade_options = [
            grade.id for grade in scn.grades
            if grade.material_class == "washed" and grade.quality
        ]
        route_grade_options = _route_eligible_grades(selected_id)
        input_grade_options = list(route_grade_options)
        if not route_grade_options:
            configured_grades = {
                str(row.get("grade_id", "") or "")
                for row in [*current_recipe, *current_bounds]
            }
            input_grade_options = [
                grade_id for grade_id in result_grade_options
                if grade_id in configured_grades]
            st.warning(
                "No currently supplied quality has a complete inbound route "
                "to this stockpile. Saved rows remain visible for correction.")
        current_result_grade = str(settings["result_grades"].get(
            policy_source_id, "") or selected_space.grade)
        if current_result_grade not in result_grade_options and result_grade_options:
            current_result_grade = result_grade_options[0]
        resulting_grade = st.selectbox(
            "Resulting blend grade",
            result_grade_options,
            index=(result_grade_options.index(current_result_grade)
                   if current_result_grade in result_grade_options else 0),
            key=f"blend_result_grade_{selected_id}",
            disabled=not enabled or campaign_locked,
            help=("This is the campaign grade used when the completed pile is "
                  "later assigned to PAP or DRY-FEED demand."),
        )
        configured_batch = int(round(float(settings["batch_t_by_space"].get(
            policy_source_id,
            min(12_000, selected_space.capacity_t)) or 12_000)))
        configured_batch = max(
            1, min(configured_batch, int(selected_space.capacity_t)))
        batch_t = st.number_input(
            "Blend batch quantity [physical t]",
            min_value=1, max_value=int(selected_space.capacity_t),
            value=configured_batch, step=1_000,
            key=f"blend_batch_{selected_id}",
            disabled=not enabled or campaign_locked,
        )
        max_ready_hour = max(int(math.ceil(
            scn.sim.effective_horizon_minutes() / 60.0)) - 1, 1)
        configured_ready = int(round(float(
            settings["ready_minute_by_space"].get(
                policy_source_id,
                min(48, max_ready_hour) * 60) or 60) / 60.0))
        configured_ready = max(1, min(configured_ready, max_ready_hour))
        ready_hour = st.number_input(
            "Reclaim available from planning hour",
            min_value=1, max_value=max_ready_hour,
            value=configured_ready, step=1,
            key=f"blend_ready_{selected_id}",
            disabled=not enabled or campaign_locked,
            help=("All grade-specific stacking tonnes must be completed before "
                  "this boundary. Reclaim is blocked earlier."),
        )
        passes = st.number_input(
            "Construction passes per matrix cycle",
            min_value=2, max_value=200,
            value=int(settings["passes_per_cycle"]), step=2,
            key=f"blend_passes_{selected_id}",
            disabled=not enabled or campaign_locked,
            help=("This records the intended construction cycle. It does not "
                  "change the stacker's physical flowrate."),
        )
        configured_minimum_component_pct = 100.0 * float(
            settings["minimum_component_fraction_by_space"].get(
                policy_source_id, 0.05) or 0.0)
        if selected_mode == optimized_mode:
            minimum_component_pct = st.number_input(
                "Minimum non-zero model-selected share [%]",
                min_value=0.0, max_value=50.0,
                value=min(max(configured_minimum_component_pct, 0.0), 50.0),
                step=1.0,
                key=f"blend_minimum_component_{selected_id}",
                disabled=not enabled or campaign_locked,
                help=("Prevents token quantities that are too small to form "
                      "a meaningful component. The planner also enforces the "
                      "smallest executable stacker run."),
            )
        else:
            minimum_component_pct = configured_minimum_component_pct

        # Read the operator-authored fixed matrix directly. In optimized mode
        # ``chevron_recipe`` may be the solver proposal, which must not silently
        # replace a saved matrix when the operator switches modes.
        matrix_rows = current_matrix
        if not matrix_rows and len(input_grade_options) >= 2:
            matrix_rows = [
                {"grade_id": input_grade_options[0], "target_fraction": 0.5},
                {"grade_id": input_grade_options[1], "target_fraction": 0.5},
            ]
        matrix_frame = pd.DataFrame([{
            "Input quality": str(component["grade_id"]),
            "Fixed mass [%]": 100.0 * float(component["target_fraction"]),
        } for component in matrix_rows],
            columns=["Input quality", "Fixed mass [%]"])

        bounds_rows = current_bounds
        if not bounds_rows:
            seed_grades = [str(row["grade_id"]) for row in matrix_rows]
            if len(seed_grades) < 2:
                seed_grades = input_grade_options[:2]
            bounds_rows = [
                {"grade_id": grade_id, "min_fraction": 0.05,
                 "max_fraction": 0.95}
                for grade_id in seed_grades
            ]
        bounds_frame = pd.DataFrame([{
            "Eligible input quality": str(component["grade_id"]),
            "Minimum mass [%]": 100.0 * float(component["min_fraction"]),
            "Maximum mass [%]": 100.0 * float(component["max_fraction"]),
        } for component in bounds_rows], columns=[
            "Eligible input quality", "Minimum mass [%]", "Maximum mass [%]",
        ])

        if selected_mode == matrix_mode:
            edited = st.data_editor(
                matrix_frame,
                key=f"blend_matrix_{selected_id}_{selected_mode}",
                num_rows="dynamic", hide_index=True,
                disabled=not enabled or campaign_locked,
                use_container_width=True,
                column_config={
                    "Input quality": st.column_config.SelectboxColumn(
                        options=input_grade_options, required=True),
                    "Fixed mass [%]": st.column_config.NumberColumn(
                        min_value=0.1, max_value=100.0, step=1.0,
                        format="%.1f%%", required=True),
                },
            )
            edited_matrix = edited
            edited_bounds = bounds_frame
        else:
            edited = st.data_editor(
                bounds_frame,
                key=f"blend_bounds_{selected_id}_{selected_mode}",
                num_rows="dynamic", hide_index=True,
                disabled=not enabled or campaign_locked,
                use_container_width=True,
                column_config={
                    "Eligible input quality": st.column_config.SelectboxColumn(
                        options=input_grade_options, required=True),
                    "Minimum mass [%]": st.column_config.NumberColumn(
                        min_value=0.0, max_value=100.0, step=1.0,
                        format="%.1f%%", required=True),
                    "Maximum mass [%]": st.column_config.NumberColumn(
                        min_value=0.0, max_value=100.0, step=1.0,
                        format="%.1f%%", required=True),
                },
            )
            edited_matrix = matrix_frame
            edited_bounds = edited
        if selected_mode == matrix_mode:
            share_total = float(pd.to_numeric(
                edited_matrix.get(
                    "Fixed mass [%]", pd.Series(dtype=float)),
                errors="coerce").fillna(0).sum())
            st.caption(
                f"Matrix total: {share_total:.1f}% - it must equal 100.0%.")
        else:
            minimum_total = float(pd.to_numeric(
                edited_bounds.get(
                    "Minimum mass [%]", pd.Series(dtype=float)),
                errors="coerce").fillna(0).sum())
            maximum_total = float(pd.to_numeric(
                edited_bounds.get(
                    "Maximum mass [%]", pd.Series(dtype=float)),
                errors="coerce").fillna(0).sum())
            share_total = 0.0
            st.caption(
                f"Share envelope: minima total {minimum_total:.1f}% - "
                f"maxima total {maximum_total:.1f}%. Feasibility requires "
                "minimum total <= 100% <= maximum total.")
        if selected_mode == matrix_mode and abs(share_total - 100.0) <= 1e-6:
            result_components = [{
                "grade_id": str(row.get("Input quality", "") or ""),
                "target_fraction": float(
                    row.get("Fixed mass [%]", 0.0)) / 100.0,
            } for _, row in edited_matrix.iterrows()
                if str(row.get("Input quality", "") or "").strip()]
            result_heading = "Configured fixed matrix"
        elif selected_mode == optimized_mode:
            plan_components = plan_campaign.get("composition", [])
            result_components = (
                current_resolved if campaign_locked else plan_components)
            result_heading = (
                "Physically locked matrix" if campaign_locked else
                "Planner-selected matrix for the latest plan")
        else:
            result_components = []
            result_heading = ""
        resolved_quality = (
            dict(plan_campaign.get("resulting_quality", {}) or {})
            if selected_mode == optimized_mode and not campaign_locked
            else _weighted_quality(result_components, float(batch_t)))
        if result_components and resolved_quality:
            st.markdown(f"**{result_heading}**")
            st.caption(_composition_label(result_components, float(batch_t)))
            if selected_mode == optimized_mode and not campaign_locked:
                st.caption(
                    "Selected jointly with the feasible route, equipment and "
                    "stocking schedule through this pile's ready time.")
            st.dataframe(pd.DataFrame([{
                "Parameter": parameter,
                "Weighted value": value,
            } for parameter, value in sorted(resolved_quality.items())]),
                hide_index=True, use_container_width=True,
                column_config={
                    "Weighted value": st.column_config.NumberColumn(
                        format="%.3f"),
                })
            if selected_mode == optimized_mode and not campaign_locked:
                receiving_checks = list(
                    plan_campaign.get("receiving_checks", []) or [])
                if receiving_checks:
                    st.markdown("**Receiving-interval checks**")
                    check_rows = []
                    for check in receiving_checks:
                        if not isinstance(check, dict):
                            continue
                        check_rows.append({
                            "Receiving unit": check.get("destination", ""),
                            "Parameter": check.get("parameter", ""),
                            "Blend value": check.get("value", None),
                            "Minimum": check.get("minimum", None),
                            "Maximum": check.get("maximum", None),
                            "Constraint": (
                                "Hard" if check.get("hard", False)
                                else "Soft"),
                            "Status": (
                                "Within interval"
                                if check.get("on_spec", False)
                                else "Outside interval"),
                        })
                    if check_rows:
                        st.dataframe(
                            pd.DataFrame(check_rows), hide_index=True,
                            use_container_width=True,
                            column_config={
                                "Blend value": st.column_config.NumberColumn(
                                    format="%.3f"),
                                "Minimum": st.column_config.NumberColumn(
                                    format="%.3f"),
                                "Maximum": st.column_config.NumberColumn(
                                    format="%.3f"),
                            })
                        if any(row["Status"] != "Within interval"
                               for row in check_rows):
                            st.warning(
                                "The best feasible matrix is outside at least "
                                "one soft receiving interval. Hard intervals "
                                "are never relaxed.")
        elif selected_mode == optimized_mode:
            st.info(
                "No percentages are fixed here. Generate a plan to see the "
                "planner's proposed matrix and weighted quality.")

        if st.button("Apply stockpile blending", type="primary"):
            next_settings = deepcopy(dict(scn.config.get(
                blending.CHEVRON_CONFIG_KEY, {}) or {}))
            next_recipes = {
                str(space_id): [dict(component) for component in components]
                for space_id, components in settings["recipes"].items()
            }
            next_modes = dict(settings.get("mode_by_space", {}) or {})
            next_bounds = {
                str(space_id): [dict(component) for component in components]
                for space_id, components in (
                    settings.get("share_bounds_by_space", {}) or {}).items()
            }
            next_proposals = {
                str(space_id): [dict(component) for component in components]
                for space_id, components in (
                    settings.get("proposed_recipes_by_space", {}) or {}).items()
            }
            next_result_grades = dict(settings["result_grades"])
            next_batches = dict(settings["batch_t_by_space"])
            next_ready = dict(settings["ready_minute_by_space"])
            next_minimum_components = dict(
                settings["minimum_component_fraction_by_space"])
            next_states = deepcopy(settings["campaign_states_by_space"])
            try:
                matrix_components = []
                for _, row in edited_matrix.iterrows():
                    grade_id = str(row.get("Input quality", "") or "").strip()
                    share = float(row.get("Fixed mass [%]", 0.0)) / 100.0
                    if grade_id and share > 0:
                        matrix_components.append({
                            "grade_id": grade_id,
                            "target_fraction": share,
                        })
                bound_components = []
                for _, row in edited_bounds.iterrows():
                    grade_id = str(row.get(
                        "Eligible input quality", "") or "").strip()
                    minimum = float(row.get("Minimum mass [%]", 0.0)) / 100.0
                    maximum = float(row.get("Maximum mass [%]", 0.0)) / 100.0
                    if grade_id:
                        bound_components.append({
                            "grade_id": grade_id,
                            "min_fraction": minimum,
                            "max_fraction": maximum,
                        })

                active_components = (
                    matrix_components if selected_mode == matrix_mode
                    else bound_components)
                active_grade_ids = [str(row["grade_id"])
                                    for row in active_components]
                if enabled and len(active_components) < 2:
                    raise ValueError(
                        "An enabled blend needs at least two input qualities.")
                if len(active_grade_ids) != len(set(active_grade_ids)):
                    raise ValueError("Each input quality may appear only once.")
                if enabled and any(grade_id not in route_grade_options
                                   for grade_id in active_grade_ids):
                    raise ValueError(
                        "Every enabled input quality needs a complete inbound "
                        "route to this stockpile.")
                if selected_mode == matrix_mode:
                    total = sum(float(component["target_fraction"])
                                for component in matrix_components)
                    if enabled and abs(total - 1.0) > 1e-6:
                        raise ValueError(
                            "Enabled blend-matrix shares must total 100%.")
                    if matrix_components:
                        next_recipes[selected_id] = matrix_components
                    else:
                        next_recipes.pop(selected_id, None)
                else:
                    for component in bound_components:
                        minimum = float(component["min_fraction"])
                        maximum = float(component["max_fraction"])
                        if not 0.0 <= minimum <= maximum <= 1.0:
                            raise ValueError(
                                "Every minimum must be between 0% and its "
                                "maximum; every maximum must be at most 100%.")
                    minimum_total = sum(float(row["min_fraction"])
                                        for row in bound_components)
                    maximum_total = sum(float(row["max_fraction"])
                                        for row in bound_components)
                    if enabled and not (
                            minimum_total <= 1.0 + 1e-6
                            and maximum_total + 1e-6 >= 1.0):
                        raise ValueError(
                            "Optimized-share bounds require total minima <= "
                            "100% <= total maxima.")
                    if bound_components:
                        next_bounds[selected_id] = bound_components
                    else:
                        next_bounds.pop(selected_id, None)
                next_modes[selected_id] = selected_mode
                # A policy edit invalidates the latest unexecuted proposal.
                # A locked physical matrix lives in campaign state, and the
                # controls above prevent it from being edited.
                next_proposals.pop(selected_id, None)
                next_result_grades[selected_id] = str(resulting_grade)
                next_batches[selected_id] = int(batch_t)
                next_ready[selected_id] = int(ready_hour) * 60
                next_minimum_components[selected_id] = (
                    float(minimum_component_pct) / 100.0)
                if current_status == blending.CAMPAIGN_NOT_STARTED and \
                        float(selected_space.mass_t) <= 1.0:
                    # A changed empty-pile recipe starts with no inherited
                    # physical progress.
                    next_states.pop(selected_id, None)
                next_enabled = set(enabled_spaces)
                if enabled:
                    next_enabled.add(selected_id)
                else:
                    next_enabled.discard(selected_id)
                next_settings.update({
                    "model_version": 5,
                    "enabled": bool(next_enabled),
                    "enabled_spaces": sorted(next_enabled),
                    "passes_per_cycle": int(passes),
                    "recipes": next_recipes,
                    "mode_by_space": next_modes,
                    "share_bounds_by_space": next_bounds,
                    "proposed_recipes_by_space": next_proposals,
                    "point_labels": dict(settings["point_labels"]),
                    "result_grades": next_result_grades,
                    "batch_t_by_space": next_batches,
                    "ready_minute_by_space": next_ready,
                    "minimum_component_fraction_by_space":
                        next_minimum_components,
                    "campaign_states_by_space": next_states,
                })
                next_settings.pop("ready_bucket_by_space", None)
                errors = blending.chevron_config_errors(scn, next_settings)
                if errors:
                    raise ValueError(" ".join(errors))
            except (TypeError, ValueError) as exc:
                st.error(str(exc))
            else:
                scn.config[blending.CHEVRON_CONFIG_KEY] = next_settings
                scn.config.pop("M16_PRODUCT_STOCKPILE_BLENDING", None)
                stale = common.invalidate_outputs()
                common.revalidate()
                st.success(
                    f"{selected_label} blending "
                    + ("enabled" if enabled else "disabled")
                    + (" — previous plan cleared" if stale else ""))
                st.rerun()

    st.info(
        "The planner tracks the physical tonnes stacked for every input "
        "quality. The ready time is only an earliest reclaim boundary: an "
        "unfinished matrix never becomes available just because that time has "
        "passed. In optimized mode the matrix and its feasible stacking "
        "schedule are selected together. A generated-but-unexecuted proposal "
        "may change during replanning; once physical construction starts, its "
        "campaign matrix is locked. Within-pile quality variation is not yet "
        "simulated.")
def page_demand():
    from .pages_planning import (render_plan_run_panel,
                                 render_planner_priorities)

    st.subheader("Weekly demand plan")
    scn = common.sc()
    T = scn.sim.horizon_buckets
    demand_notice = st.session_state.pop("weekly_demand_notice", "")
    if demand_notice:
        st.success(demand_notice)
    day_ranges = _daily_bucket_ranges(scn)
    if day_ranges:
        week_start = scn.sim.bucket_dt(day_ranges[0].start)
        week_end = scn.sim.bucket_dt(day_ranges[-1].start)
        st.caption(
            f"{week_start:%d %b %Y} – {week_end:%d %b %Y} · "
            f"{len(day_ranges)} dates · {T} hourly planning periods · "
            "all quantities are physical/as-handled tonnes.")

    _render_planning_demand_profile(scn)

    display_to_grade, grade_to_display = _grade_display_maps(scn)
    site_sections = _daily_demand_rows_by_site(scn)
    _render_demand_site_cards(site_sections, grade_to_display)

    st.markdown("#### Demand plan by receiving unit")
    st.caption(
        "One row is one grade campaign inside one date. A receiving unit may "
        "appear several times on the same date: split its intraday windows, "
        "then assign a grade and quantity to each campaign. Positive windows "
        "for the same unit cannot overlap. Washing-feed work remains flexible "
        "inside each campaign window.")
    weekly_editor = pd.DataFrame()
    if not site_sections:
        st.info("This scenario has no receiving-unit demand to configure.")
    else:
        rows = []
        for _site_id, label, _description, site_rows in site_sections:
            for row in site_rows:
                rows.append({_SITE_COL: label, **row})
        unit_df = pd.DataFrame(rows)
        unit_df[_GRADE_COL] = unit_df[_GRADE_COL].map(
            lambda grade: grade_to_display.get(
                "" if grade == "—" else grade,
                theme.operator_text(grade)))
        unit_df = unit_df.sort_values(
            [_DATE_COL, _SITE_COL, _UNIT_COL, _START_HOUR_COL],
            kind="stable")
        visible_columns = [
            _DATE_COL, _SITE_COL, _UNIT_COL, _GRADE_COL,
            _DAILY_QUANTITY_COL, _START_HOUR_COL, _END_HOUR_COL, _PRIORITY_COL, _TYPE_COL,
        ]
        unit_df = unit_df[visible_columns]
        max_day_h = 24.0
        weekly_editor = st.data_editor(
            theme.operator_frame(unit_df),
            key="weekly_demand_editor",
            num_rows="dynamic",
            hide_index=True,
            height=650,
            disabled=[_SITE_COL, _TYPE_COL],
            column_config={
                _DATE_COL: st.column_config.SelectboxColumn(
                    _DATE_COL,
                    options=[
                        scn.sim.bucket_dt(buckets.start).strftime("%Y-%m-%d")
                        for buckets in day_ranges
                    ],
                    required=True,
                    help="Calendar date in the seven-day planning horizon"),
                _SITE_COL: st.column_config.TextColumn(
                    _SITE_COL,
                    help="Derived from the receiving unit after Apply"),
                _UNIT_COL: st.column_config.SelectboxColumn(
                    _UNIT_COL,
                    options=[destination.id
                             for destination in scn.destinations],
                    required=True,
                    help="Receiving plant, washing line or train interface"),
                _GRADE_COL: st.column_config.SelectboxColumn(
                    _GRADE_COL, options=list(display_to_grade), required=True,
                    help=("Delivered grade, or raw feed grade for a washing "
                          "line")),
                _DAILY_QUANTITY_COL: st.column_config.NumberColumn(
                    _DAILY_QUANTITY_COL, min_value=0.0, step=100.0,
                    format="%.0f",
                    help=("Physical/as-handled tonnes required for this grade "
                          "campaign")),
                _START_HOUR_COL: st.column_config.NumberColumn(
                    _START_HOUR_COL, min_value=0.0,
                    max_value=max(max_day_h - scn.sim.dt_h, 0.0),
                    step=float(scn.sim.dt_h),
                    format="%.0f",
                    help="Hour offset from 00:00; included in the campaign"),
                _END_HOUR_COL: st.column_config.NumberColumn(
                    _END_HOUR_COL, min_value=float(scn.sim.dt_h),
                    max_value=max_day_h, step=float(scn.sim.dt_h),
                    format="%.0f",
                    help="Hour offset from 00:00; excluded from the campaign"),
                _TYPE_COL: st.column_config.TextColumn(
                    _TYPE_COL,
                    help="Final delivery or washing-line feed"),
                _PRIORITY_COL: st.column_config.NumberColumn(
                    _PRIORITY_COL, min_value=1, step=1, format="%d",
                    help="Higher values increase this campaign's unmet-demand penalty. "
                         "Blank inherits the receiving unit's default priority."),
            },
        )

    st.caption(
        "Use the table's add/delete controls to create or remove campaigns. "
        "For example, change an existing 0–24 row to 0–12 and add a second "
        "12–24 row for another grade. Edits are staged until Apply; the "
        "update is atomic across Benguerir, Louta and MPH. Unchanged grade "
        "campaigns retain their hourly profile; changed quantities scale it.")
    current_c2_t = float(dict(scn.config.get(
        "CORRIDOR_DISPATCH_TARGET_T", {}) or {}).get("C2", 0.0))
    preserve_c2 = True
    if any(link.id == "C2" for link in scn.links):
        preserve_c2 = st.checkbox(
            f"Keep current C2 allocation ({current_c2_t:,.0f} t)", value=True,
            key="weekly_keep_c2_allocation",
            help="Retains the Benguerir-to-MPH supply target when editing unit demand. "
                 "Clear this option to explicitly reset the target to zero on Apply.")
        if not preserve_c2 and current_c2_t > 0:
            st.caption("Applying this demand plan will reset the C2 supply target to zero.")
    if st.button("Apply weekly demand", type="primary"):
        try:
            edited_rows = [row for _, row in weekly_editor.iterrows()]
            staged = _stage_weekly_demand_change(
                scn, edited_rows, display_to_grade,
                preserve_c2_allocation=preserve_c2)
        except (TypeError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.session_state["scenario"] = staged
            stale = common.invalidate_outputs()
            common.revalidate()
            st.session_state["weekly_demand_notice"] = (
                "Weekly demand plan applied"
                + (" — previous plan cleared" if stale else ""))
            st.rerun()

    with st.expander("Advanced planning priorities", expanded=False):
        render_planner_priorities(scn, key_prefix="plan")

    if common.plan() is not None:
        with st.expander("Louta stocked-route policy", expanded=False):
            _render_louta_s0_policy(scn)

    with st.expander("Advanced hourly delivery overrides", expanded=False):
        st.caption(
            "Optional hourly override for final deliveries; each bucket keeps "
            "its daily campaign grade. Washing-feed operating hours remain "
            "flexible within each day and are chosen by the planner.")
        profile_destinations = [
            destination for destination in scn.destinations
            if getattr(destination, "demand_kind", "delivery") != "process_feed"
            and flow_model.destination_line_operating_state(
                scn, destination.id) != "FUTURE"
        ]
        base = {
            destination.id: [
                _configured_demand_tph(scn, destination.id, t)
                for t in range(T)
            ]
            for destination in profile_destinations
        }
        ddf = pd.DataFrame(base, index=pd.Index(range(T), name="bucket"))
        edited = st.data_editor(ddf, height=280, key="demand_editor")
        c1, c2 = st.columns([1, 4])
        if c1.button("Apply hourly profile"):
            try:
                process_destination_ids = {
                    destination.id for destination in scn.destinations
                    if getattr(
                        destination, "demand_kind", "delivery"
                    ) == "process_feed"
                }
                future_destination_ids = {
                    destination.id for destination in scn.destinations
                    if flow_model.destination_line_operating_state(
                        scn, destination.id) == "FUTURE"
                }
                preserved_destination_ids = (
                    process_destination_ids | future_destination_ids)
                entries = [
                    entry for entry in scn.demand
                    if entry.dest_id in preserved_destination_ids
                ]
                for destination in profile_destinations:
                    for bucket in range(T):
                        rate_tph = float(
                            edited.loc[bucket, destination.id])
                        if not math.isfinite(rate_tph) or rate_tph < 0:
                            raise ValueError(
                                f"{destination.id}, bucket {bucket}: "
                                "rate must be non-negative.")
                        entries.append(DemandEntry(
                            destination.id,
                            bucket,
                            rate_tph,
                            requested_grade=_configured_demand_grade(
                                scn, destination.id, bucket),
                            priority=scn.demand_priority(destination.id, bucket),
                        ))
                scn.demand = entries
            except (TypeError, ValueError) as exc:
                st.error(str(exc))
            else:
                stale = common.invalidate_outputs()
                common.revalidate()
                st.success(
                    "Hourly profile applied"
                    + (" — previous plan cleared" if stale else ""))
        with c2:
            plot(series_fig(
                list(range(T)), base, "Demand profiles", "t/h", "line", 240))

    render_plan_run_panel()
