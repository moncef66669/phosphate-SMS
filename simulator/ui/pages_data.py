"""Pages: 2 Grades/Quality/Inputs · 3 Stockyards · 4 Routes · 5 Demand."""
from __future__ import annotations

import io
import json
import math
from copy import deepcopy
from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from mph_sim import datasets, flow_config as flow_model
from mph_sim.domain import DemandEntry, SpecRange
from mph_sim.flow_config import (destination_line_status,
                                 process_stage_enabled,
                                 set_destination_line_enabled)
from mph_sim.inventory_snapshot import (
    SnapshotParseError, apply_inventory_snapshot, parse_inventory_snapshot,
    snapshot_diff_rows, snapshot_template_csv, validate_inventory_snapshot,
)
from mph_sim.persistence import (TABLE_REGISTRY, entities_to_df, export_excel,
                                 import_table, scenario_tables)
from mph_sim.routes import RouteGraph, reachability_matrix, space_node

from . import common, theme
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
_GRADE_BASIS_COLUMN = "Basis"
_GRADE_USAGE_COLUMN = "Stockpiles using grade"
_GRADE_OVERRIDE_COLUMN = "With pile-specific assay"


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
    for grade in scn.grades:
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


def _stage_grade_profile_rows(staged, edited: pd.DataFrame) -> list[str]:
    """Apply a protected-ID grade table to a staged scenario."""
    errors: list[str] = []
    expected = {grade.id for grade in staged.grades}
    display_ids = {
        theme.operator_text(grade_id): grade_id for grade_id in expected
    }
    rows_by_id: dict[str, dict] = {}
    for row in edited.to_dict("records"):
        display_id = _editor_text(row.get(_GRADE_ID_COLUMN))
        grade_id = display_ids.get(display_id, display_id)
        if not display_id:
            errors.append("A grade row is missing its protected Grade ID")
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

    param_by_name = {param.name: param for param in staged.quality_params}
    quality_columns = _quality_column_map(staged)
    registered = set(param_by_name)
    for grade in staged.grades:
        row = rows_by_id[grade.id]
        before_name = grade.name
        before_quality = dict(grade.quality)
        name = _restore_operator_text(
            row.get(_GRADE_NAME_COLUMN), grade.name)
        if not name:
            errors.append(f"{grade.id}: grade name cannot be blank")
            continue

        # Preserve any imported, unregistered measurements rather than
        # silently deleting them from a fixed operator-facing editor.
        quality = {
            key: value for key, value in grade.quality.items()
            if key not in registered
        }
        for param_name, display_name in quality_columns.items():
            parsed = _optional_finite_number(
                row.get(display_name),
                f"{grade.id} · {display_name}",
                errors,
            )
            if parsed is None:
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
        grade.name = name
        grade.quality = quality
        changed_fields = []
        if grade.name != before_name:
            changed_fields.append("name")
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
    evidence.source = "Operator override · Configure → Grade profiles"
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
    st.session_state["scenario"] = staged
    if new_audit:
        overrides = list(st.session_state.get("overrides_log", []))
        overrides.extend({
            "action": "configure grade profiles",
            **record,
        } for record in new_audit)
        st.session_state["overrides_log"] = overrides[-100:]
    stale = common.invalidate_outputs()
    common.revalidate()
    st.session_state["grade_profiles_flash"] = (
        message + (" · previous plan cleared" if stale else ""))
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
        "Grade chemistry, material profiles and receiving limits are on "
        "**Configure → Grade profiles**.")
    scn = common.sc()
    sites = _configure_sites(scn)
    if not sites:
        st.info("This scenario has no configurable operational sites.")
        return

    flash = st.session_state.pop("operational_config_flash", "")
    if flash:
        st.success(flash)

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
    disabled_lines = flow_model.disabled_process_lines(scn)
    nonce = int(st.session_state.get("operational_config_nonce", 0))

    with st.form(f"operational_config_form_{site_id}_{nonce}"):
        line_col, machine_col = st.columns([0.85, 1.35])
        desired_process_lines: dict[str, bool] = {}
        desired_destination_lines: dict[str, object] = {}

        with line_col:
            st.markdown("#### Line status")
            if process_lines:
                for line_id in process_lines:
                    desired_process_lines[line_id] = st.toggle(
                        _PROCESS_LINE_LABELS.get(line_id, line_id),
                        value=line_id not in disabled_lines,
                        key=f"operational_line_{site_id}_{line_id}_{nonce}",
                        help=f"{line_id} · retains configured demand when off",
                    )

            if site_id == "MPH":
                destination_ids = {
                    destination.id for destination in scn.destinations
                }
                for destination_id in _MPH_DESTINATION_LINES:
                    if destination_id not in destination_ids:
                        continue
                    if destination_id == "PAP-2":
                        current = flow_model.destination_line_operating_state(
                            scn, destination_id)
                        desired_destination_lines[destination_id] = st.selectbox(
                            destination_id,
                            options=["Available", "Future"],
                            index=0 if current == "AVAILABLE" else 1,
                            key=(
                                f"operational_destination_{destination_id}_"
                                f"{nonce}"
                            ),
                            help=(
                                "Future retains the 36 kt demand template but "
                                "keeps PAP-2 outside the active plan."
                            ),
                        )
                        st.caption(
                            "36,000 t / 24 h design demand · same as PAP-1")
                        if desired_destination_lines[destination_id] == \
                                "Available":
                            st.warning(
                                "At full templates, PAP-1 + PAP-2 + DRY-FEED "
                                "request 91,200 t/day against the current "
                                "72,000 t/day M16 outbound envelope. Reduce a "
                                "demand or accept unmet demand.")
                    else:
                        status = destination_line_status(
                            scn, destination_id)
                        desired_destination_lines[destination_id] = st.toggle(
                            destination_id,
                            value=status == "ENABLED",
                            key=(
                                f"operational_destination_{destination_id}_"
                                f"{nonce}"
                            ),
                            help=(
                                "Controls the modelled route legs without "
                                "deleting configured demand."
                            ),
                        )
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
                for line_id, available in desired_process_lines.items():
                    current = line_id not in flow_model.disabled_process_lines(
                        staged)
                    if current != bool(available):
                        flow_model.set_process_line_enabled(
                            staged, line_id, bool(available))
                        changed = True

                for destination_id, desired in (
                        desired_destination_lines.items()):
                    if destination_id == "PAP-2":
                        desired_state = str(desired).strip().upper()
                        current_state = (
                            flow_model.destination_line_operating_state(
                                staged, destination_id))
                        if desired_state != current_state:
                            flow_model.set_destination_line_operating_state(
                                staged, destination_id, desired_state)
                            changed = True
                    else:
                        desired_available = bool(desired)
                        current_available = destination_line_status(
                            staged, destination_id) == "ENABLED"
                        if desired_available != current_available:
                            set_destination_line_enabled(
                                staged, destination_id, desired_available)
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

    with st.expander("Advanced · interactive route diagram", expanded=False):
        st.caption(
            "Use this only when an individual conveyor route must be changed. "
            "Reference and future equipment remain read-only.")
        if st.toggle(
                "Load route diagram",
                value=False,
                key=f"operational_load_route_diagram_{site_id}"):
            render_flow_availability_configuration(scn)


def page_grade_profiles():
    """Focused Configure page for grade chemistry and its acceptance rules."""
    st.subheader("Grade profiles")
    st.caption(
        "Define the shared chemical grades once, then use them at Louta, "
        "Benguerir and MPH. Changes apply to the next generated plan; protected "
        "IDs and material states cannot be renamed here.")
    scn = common.sc()
    nonce = int(st.session_state.get("grade_profiles_nonce", 0))
    flash = st.session_state.pop("grade_profiles_flash", "")
    if flash:
        st.success(flash)

    grade_tab, profile_tab, limit_tab = st.tabs([
        "Chemical grades",
        "Material profiles",
        "Receiving limits",
    ])

    with grade_tab:
        st.markdown("#### Chemical grade vectors")
        st.caption(
            "Blank means no assay value is configured. A stockpile-specific "
            "assay overrides its grade vector; the two usage columns make "
            "those overrides visible.")
        grade_frame = _grade_profile_frame(scn)
        quality_columns = _quality_column_map(scn)
        grade_column_config = {
            _GRADE_ID_COLUMN: st.column_config.TextColumn(
                _GRADE_ID_COLUMN,
                help="Stable model ID; protected because stockpiles refer to it.",
            ),
            _GRADE_NAME_COLUMN: st.column_config.TextColumn(
                _GRADE_NAME_COLUMN,
                help="Operator-facing grade name.",
            ),
            _GRADE_CLASS_COLUMN: st.column_config.TextColumn(
                _GRADE_CLASS_COLUMN),
            _GRADE_BASIS_COLUMN: st.column_config.TextColumn(
                _GRADE_BASIS_COLUMN,
                help="Evidence basis for the current numeric vector.",
            ),
            _GRADE_USAGE_COLUMN: st.column_config.NumberColumn(
                _GRADE_USAGE_COLUMN, format="%d"),
            _GRADE_OVERRIDE_COLUMN: st.column_config.NumberColumn(
                _GRADE_OVERRIDE_COLUMN, format="%d"),
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
                disabled=[
                    _GRADE_ID_COLUMN,
                    _GRADE_CLASS_COLUMN,
                    _GRADE_BASIS_COLUMN,
                    _GRADE_USAGE_COLUMN,
                    _GRADE_OVERRIDE_COLUMN,
                ],
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
                st.warning("A fetched live preview was discarded because the "
                           "scenario or connector configuration changed.")

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
_TYPE_COL = "Requirement"
_GRADE_COL = "Requested grade"
_QUANTITY_COL = "Quantity [t]"
_RATE_COL = "Average rate [t/h]"
_PRIORITY_COL = "Priority"
_ROUTE_COL = "Route"


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


def _demand_plan_rows(scn) -> list[dict]:
    """Build the concise, one-row-per-unit demand contract."""
    horizon_h = scn.sim.horizon_buckets * scn.sim.dt_h
    rows = []
    for destination in scn.destinations:
        quantity_t = _demand_quantity_t(scn, destination.id)
        process_feed = (
            getattr(destination, "demand_kind", "delivery") == "process_feed"
        )
        route_modelled = not process_feed
        route_label = "Modelled"
        if process_feed:
            for transfer in getattr(scn, "process_transfers", []) or []:
                if transfer.process_stage != getattr(
                        destination, "process_stage", ""):
                    continue
                try:
                    source_grade = scn.space(transfer.source_space).grade
                except StopIteration:
                    continue
                requested_grade = getattr(destination, "requested_grade", "")
                if not requested_grade or source_grade == requested_grade:
                    route_modelled = True
                    break
            if not process_stage_enabled(
                    scn, getattr(destination, "process_stage", "")):
                route_label = "Disabled"
            elif not route_modelled:
                route_label = "Pending model"
        else:
            line_status = destination_line_status(scn, destination.id)
            if line_status == "FUTURE":
                route_label = "Future"
            elif line_status == "DISABLED":
                route_label = "Disabled"
            elif line_status == "PARTIAL":
                route_label = "Partly available"
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
    grade_ids = [
        grade.id for grade in scn.grades
        if grade.quality or any(
            getattr(destination, "requested_grade", "") == grade.id
            for destination in scn.destinations)
    ]
    display_to_id = {"—": ""}
    id_to_display = {"": "—"}
    for grade_id in grade_ids:
        label = theme.operator_text(grade_id)
        if label in display_to_id and display_to_id[label] != grade_id:
            label = f"{label} ({len(display_to_id)})"
        display_to_id[label] = grade_id
        id_to_display[grade_id] = label
    return display_to_id, id_to_display


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


def _render_planning_demand_profile(scn) -> None:
    """Apply an operator-visible demand bundle without artificial jobs."""
    profiles = datasets.PLANNING_DEMAND_PROFILES
    profile_ids = list(profiles)
    active = str(scn.config.get(
        "ACTIVE_PLANNING_DEMAND_PROFILE",
        datasets.DEFAULT_PLANNING_DEMAND_PROFILE))
    if active not in profiles:
        active = datasets.DEFAULT_PLANNING_DEMAND_PROFILE

    with st.container(border=True):
        st.markdown("#### Demand scenario")
        selected = st.selectbox(
            "Demand scenario",
            options=profile_ids,
            index=profile_ids.index(active),
            format_func=lambda profile_id: profiles[profile_id]["label"],
            key="planning_demand_profile",
        )
        profile = profiles[selected]
        st.caption(profile["description"])

        columns = st.columns(4)
        columns[0].metric(
            "BG train order",
            f"{float(profile['bg_train_product_t']):,.0f} t")
        columns[1].metric(
            "Benguerir C2 allocation",
            f"{float(profile['c2_dispatch_t']):,.0f} t")
        columns[2].metric(
            "BG washing output",
            "5,400 t" if profile.get("bg_process_active") else "Off")
        columns[3].metric(
            "Louta stocked route",
            "8,000 t" if profile.get("louta_process_active") else "Off")

        if selected == "FULL_FLOW":
            st.caption(
                "Clean all-machine demonstration: both mine processing "
                "chains, the Benguerir train and C2/MPH receiving flow are "
                "active in one 24-hour schedule.")

        if st.button(
                "Apply demand scenario",
                key="apply_planning_demand_profile"):
            datasets.apply_planning_demand_profile(scn, selected)
            stale = common.invalidate_outputs()
            common.revalidate()
            st.success(
                f"{profiles[selected]['label']} applied"
                + (" — previous plan cleared" if stale else ""))


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


def page_demand():
    from .pages_planning import (render_plan_run_panel,
                                 render_planning_scenario_selector)

    st.subheader("S0 · Three-site planning")
    render_planning_scenario_selector()
    scn = common.sc()
    T = scn.sim.horizon_buckets
    st.caption(
        "Set the requested material grade and total quantity for each final "
        "unit or washing line, then use **Generate Plan** in the sidebar. "
        "Washing-feed timing is selected by the planner.")

    _render_planning_demand_profile(scn)
    _render_louta_s0_policy(scn)

    st.markdown("#### Demand plan by receiving unit")
    st.caption(
        "For final units, the grade identifies the delivery campaign. For "
        "washing lines, it is the raw feed grade and the quantity is a minimum "
        "horizon requirement. A pending route remains visible but cannot "
        "create jobs until its inventory, machine and route are modelled.")
    display_to_grade, grade_to_display = _grade_display_maps(scn)
    unit_df = pd.DataFrame(_demand_plan_rows(scn))
    unit_df[_GRADE_COL] = unit_df[_GRADE_COL].map(
        lambda grade: grade_to_display.get(
            "" if grade == "—" else grade, theme.operator_text(grade)))
    unit_editor = st.data_editor(
        theme.operator_frame(unit_df),
        key="unit_demand_editor",
        hide_index=True,
        height=min(420, 42 + 35 * max(len(unit_df), 1)),
        disabled=[_UNIT_COL, _TYPE_COL, _RATE_COL, _ROUTE_COL],
        column_config={
            _UNIT_COL: st.column_config.TextColumn(
                _UNIT_COL, help="Receiving process unit or dispatch interface"),
            _TYPE_COL: st.column_config.TextColumn(
                _TYPE_COL, help="Final delivery or washing-line feed"),
            _GRADE_COL: st.column_config.SelectboxColumn(
                _GRADE_COL, options=list(display_to_grade),
                help="Delivery grade, or raw feed grade for a washing line"),
            _QUANTITY_COL: st.column_config.NumberColumn(
                _QUANTITY_COL, min_value=0.0, step=100.0, format="%.0f"),
            _RATE_COL: st.column_config.NumberColumn(
                _RATE_COL, format="%.1f"),
            _PRIORITY_COL: st.column_config.NumberColumn(
                _PRIORITY_COL, min_value=1, step=1, format="%d"),
            _ROUTE_COL: st.column_config.TextColumn(
                _ROUTE_COL,
                help="Whether an executable feed route is currently available"),
        },
    )
    if st.button("Apply demand", type="primary"):
        try:
            rows_by_unit = {
                str(row[_UNIT_COL]): row
                for _, row in unit_editor.iterrows()
            }
            expected_units = {destination.id for destination in scn.destinations}
            if set(rows_by_unit) != expected_units:
                raise ValueError(
                    "The demand table must contain each receiving unit once.")
            quantities = {}
            updates = []
            valid_grade_ids = {grade.id for grade in scn.grades}
            for destination in scn.destinations:
                row = rows_by_unit[destination.id]
                quantity_t = float(row[_QUANTITY_COL])
                if not math.isfinite(quantity_t) or quantity_t < 0:
                    raise ValueError(
                        f"{destination.id}: quantity must be non-negative.")
                grade_id = display_to_grade.get(str(row[_GRADE_COL]))
                if grade_id is None:
                    raise ValueError(
                        f"{destination.id}: select a registered grade.")
                if quantity_t > 0 and not grade_id:
                    raise ValueError(
                        f"{destination.id}: select a grade for positive demand.")
                if grade_id and grade_id not in valid_grade_ids:
                    raise ValueError(
                        f"{destination.id}: grade is not registered.")
                priority = int(row[_PRIORITY_COL])
                if priority < 1:
                    raise ValueError(
                        f"{destination.id}: priority must be at least 1.")
                quantities[destination.id] = quantity_t
                updates.append((destination, grade_id, priority))

            entries = _demand_entries_for_quantities(scn, quantities)
            for destination, grade_id, priority in updates:
                destination.requested_grade = grade_id
                destination.priority = priority
            scn.demand = entries
        except (TypeError, ValueError) as exc:
            st.error(str(exc))
        else:
            stale = common.invalidate_outputs()
            common.revalidate()
            st.success(
                "Demand plan applied"
                + (" — previous plan cleared" if stale else ""))

    with st.expander("Adjust hourly profile", expanded=False):
        st.caption(
            "Optional detail for final deliveries. Washing-feed demand is a "
            "horizon total; its operating hours are chosen by the planner.")
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
                        if not math.isclose(
                                rate_tph,
                                destination.demand_default_tph,
                                rel_tol=1e-9, abs_tol=1e-9):
                            entries.append(DemandEntry(
                                destination.id, bucket, rate_tph))
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
