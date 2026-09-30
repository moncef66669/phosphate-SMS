"""Planning, schedule, coverage, simulation and replanning pages."""
from __future__ import annotations

import copy
import math
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from mph_sim.coordinates import x_interval_to_reperes
from mph_sim.continuity import (CONTINUITY_MODES, continuity_policy_errors,
                                parse_continuity_policies)
from mph_sim.domain import (CalendarEntry, EVENT_TYPES, OBJECTIVE_WEIGHT_MAX,
                            FixedJob, ObjectiveWeights, SimEvent)
from mph_sim.flow_config import PROCESS_LINE_STAGE_MAP
from mph_sim.campaigns import (CAMPAIGN_POLICY_BASIS, CAMPAIGN_POLICY_KEY,
                               POLICY_FIELDS,
                               SHORT_PARCEL_PENALTY_DEFAULT,
                               SHORT_PARCEL_PENALTY_KEY,
                               campaign_policy_errors,
                               default_campaign_policy_config)
from mph_sim.objective_config import OPERATIONAL_PENALTY_DEFAULTS
from mph_sim.optimizer import blend_fractions
from mph_sim.runs import micro_parcel_metrics

from . import common, theme
from .theme import (gantt_figure, jobs_to_gantt_df, plot, series_fig, show_df)


# Active solver terms only. Process cost is intentionally absent because no
# commissioned cost curves exist and the optimiser does not use w3 today.
PLANNER_WEIGHT_SPECS = (
    ("w1_unmet", "Demand fulfilment",
     "Weighted penalty for each unmet tonne; aggregate secondary costs may still "
     "outweigh it. A larger coefficient does not guarantee demand-first planning."),
    ("w2_offspec", "Quality compliance",
     "Penalty for soft quality deviation; hard limits remain constraints."),
    ("w4_giveaway", "BPL give-away",
     "Penalty for reclaiming material above the receiving-unit BPL target."),
    ("w5_moves", "Machine changes",
     "Global multiplier for exact scheduled starts, stops and activity-group "
     "changeovers; the relative event costs are configured below."),
    ("w6_safety", "Safety-stock protection",
     "Penalty for each tonne below a configured safety-stock target."),
    ("w7_utilisation", "Useful utilisation",
     "Reward for useful scheduled throughput; zero turns the reward off."),
)


# Operational objective controls live in ``Scenario.config`` so they can be
# introduced without changing the established w1...w7 import/export contract.
# These are policy defaults, not calibrated economic or reliability costs.
OPERATIONAL_PRIORITY_DEFAULTS = {
    **OPERATIONAL_PENALTY_DEFAULTS,
    "ENERGY_OBJECTIVE_ENABLED": False,
}

OPERATIONAL_PRIORITY_SPECS = (
    ("CONTINUITY_GAP_MINUTE_PENALTY", "Feed-gap minute penalty",
     "Penalty for each scheduled minute below the continuous-feed threshold."),
    ("CONTINUITY_EPISODE_PENALTY", "Interruption episode penalty",
     "Penalty each time a new continuous-feed interruption begins."),
    ("ENERGY_OBJECTIVE_CENTIKWH_PENALTY", "Work/link energy penalty",
     "Penalty per modelled centi-kWh of productive machine work and route-link traversal."),
    ("EQUIPMENT_START_PENALTY", "Machine start penalty",
     "Penalty for each scheduled positive-flow machine start."),
    ("EQUIPMENT_STOP_PENALTY", "Machine stop penalty",
     "Penalty for each scheduled positive-flow machine stop."),
    ("EQUIPMENT_CHANGEOVER_PENALTY", "Group changeover penalty",
     "Penalty for switching an operating machine between scheduled activity groups."),
    ("EQUIPMENT_HOLD_BUCKET_PENALTY", "Held-running penalty",
     "Penalty for keeping a machine running through a bucket without moving "
     "material. Must be below a stop plus a later start or holding is never "
     "chosen, and above zero or the planner holds everywhere for free. "
     "ASSUMED \u2014 no plant figure exists for an idling drive."),
)


CONTINUITY_MODE_LABELS = {
    "continuous_feed": "Continuous feed",
    "batch_delivery": "Batch delivery",
    "flexible_process_campaign": "Flexible process campaign",
    "none": "None / not scored",
}

CONTINUITY_POLICY_DEFAULT = {
    "mode": "none",
    "minimum_tph": 0.0,
    # Share of a demand-active bucket's OWN requested mass that must arrive
    # before that bucket counts as served. This exists because the absolute
    # one-tonne test rewarded token deliveries; it is NOT an approved plant
    # stable-feed rate. Zero restores the historic absolute-tonne rule.
    "minimum_service_fraction": 0.0,
    "max_gap_min": 0,
}


def continuity_policy_values(scn) -> dict[str, dict[str, object]]:
    """Return one editable policy row for every configured destination.

    The destination register is authoritative and is never inferred from the
    policy mapping. Missing legacy rows receive a neutral display default; the
    scenario is not mutated by this helper.
    """
    raw = scn.config.get("CONTINUITY_POLICIES", {})
    raw = raw if isinstance(raw, dict) else {}
    rows: dict[str, dict[str, object]] = {}
    for destination in scn.destinations:
        policy = raw.get(destination.id, {})
        policy = policy if isinstance(policy, dict) else {}
        rows[destination.id] = {
            "mode": policy.get("mode", CONTINUITY_POLICY_DEFAULT["mode"]),
            "minimum_tph": policy.get(
                "minimum_tph", CONTINUITY_POLICY_DEFAULT["minimum_tph"]),
            "minimum_service_fraction": policy.get(
                "minimum_service_fraction",
                CONTINUITY_POLICY_DEFAULT["minimum_service_fraction"]),
            "max_gap_min": policy.get(
                "max_gap_min", CONTINUITY_POLICY_DEFAULT["max_gap_min"]),
        }
    return rows


def validate_continuity_policy_values(
        scn, values: object) -> list[str]:
    """Validate a complete, fixed-destination continuity policy register."""
    destination_ids = [destination.id for destination in scn.destinations]
    errors = continuity_policy_errors(values, destination_ids)
    if not isinstance(values, dict):
        return errors
    provided = {str(destination_id).strip() for destination_id in values}
    expected = set(destination_ids)
    missing = sorted(expected - provided)
    extra = sorted(provided - expected)
    if missing:
        errors.append(
            "CONTINUITY_POLICIES is missing configured destinations: "
            + ", ".join(missing))
    if extra:
        errors.append(
            "CONTINUITY_POLICIES contains destinations outside the fixed "
            "register: " + ", ".join(extra))
    return errors


def _normalise_continuity_policy_values(
        scn, values: object) -> dict[str, dict[str, object]]:
    """Return the storage representation after full validation."""
    errors = validate_continuity_policy_values(scn, values)
    if errors:
        raise ValueError(" ".join(errors))
    destination_ids = [destination.id for destination in scn.destinations]
    policies = parse_continuity_policies(values, destination_ids)
    return {
        destination_id: {
            "mode": policy.mode,
            "minimum_tph": float(policy.minimum_tph),
            "minimum_service_fraction": float(
                policy.minimum_service_fraction),
            "max_gap_min": int(policy.max_gap_min),
        }
        for destination_id, policy in policies.items()
    }


def _commit_continuity_policy_values(
        scn, values: object, *, basis: str = "") -> tuple[bool, bool]:
    """Atomically commit destination policies and invalidate stale outputs."""
    requested = _normalise_continuity_policy_values(scn, values)
    current_raw = scn.config.get("CONTINUITY_POLICIES", {})
    try:
        current = _normalise_continuity_policy_values(scn, current_raw)
    except ValueError:
        current = None
    if current == requested:
        return False, False

    recorded_basis = str(basis or "").strip() or (
        "ASSUMED operator policy; minimum-feed and hard-gap limits require "
        "plant approval")
    before = copy.deepcopy(current_raw)
    scn.config["CONTINUITY_POLICIES"] = requested
    scn.config["CONTINUITY_POLICY_BASIS"] = recorded_basis
    history = scn.config.get("CONTINUITY_POLICY_OVERRIDES", [])
    history = list(history) if isinstance(history, list) else []
    history.append({
        "at": datetime.now(timezone.utc).isoformat(),
        "before": before,
        "after": copy.deepcopy(requested),
        "basis": recorded_basis,
    })
    scn.config["CONTINUITY_POLICY_OVERRIDES"] = history
    stale = common.invalidate_outputs()
    common.revalidate()
    return True, stale


def operational_priority_values(scn) -> dict[str, object]:
    """Return configured operational priorities with safe legacy defaults."""
    values: dict[str, object] = {}
    for field, default in OPERATIONAL_PRIORITY_DEFAULTS.items():
        raw = scn.config.get(field, default)
        if field == "ENERGY_OBJECTIVE_ENABLED":
            values[field] = raw if isinstance(raw, bool) else raw
            continue
        try:
            values[field] = float(raw)
        except (TypeError, ValueError):
            values[field] = float("nan")
    return values


def validate_operational_priority_values(
        values: dict[str, object]) -> list[str]:
    """Validate config-backed integer priorities before committing them."""
    errors: list[str] = []
    enabled = values.get("ENERGY_OBJECTIVE_ENABLED")
    if not isinstance(enabled, bool):
        errors.append("Energy objective enabled must be true or false.")
    labels = {field: label for field, label, _help in OPERATIONAL_PRIORITY_SPECS}
    for field, label in labels.items():
        try:
            value = float(values[field])
        except (KeyError, TypeError, ValueError):
            errors.append(f"{label} must be a number.")
            continue
        if not math.isfinite(value):
            errors.append(f"{label} must be finite.")
        elif (value < 0 or value > OBJECTIVE_WEIGHT_MAX or
              not value.is_integer()):
            errors.append(
                f"{label} must be a whole number between 0 and "
                f"{OBJECTIVE_WEIGHT_MAX:g}.")
    return errors


def _commit_operational_priority_values(
        scn, values: dict[str, object]) -> tuple[bool, bool]:
    """Commit one config policy atomically and invalidate stale outputs."""
    errors = validate_operational_priority_values(values)
    if errors:
        raise ValueError(" ".join(errors))
    requested = {
        field: (bool(value) if field == "ENERGY_OBJECTIVE_ENABLED"
                else int(float(value)))
        for field, value in values.items()
        if field in OPERATIONAL_PRIORITY_DEFAULTS
    }
    current = operational_priority_values(scn)
    unchanged = all(current.get(field) == value
                    for field, value in requested.items())
    if unchanged:
        return False, False
    before = {
        field: current.get(field, default)
        for field, default in OPERATIONAL_PRIORITY_DEFAULTS.items()
    }
    scn.config.update(requested)
    history = scn.config.get("OPERATIONAL_PRIORITY_OVERRIDES", [])
    history = list(history) if isinstance(history, list) else []
    history.append({
        "at": datetime.now(timezone.utc).isoformat(),
        "before": before,
        "after": dict(requested),
        "basis": "ASSUMED operator policy; uncalibrated",
    })
    scn.config["OPERATIONAL_PRIORITY_OVERRIDES"] = history
    stale = common.invalidate_outputs()
    common.revalidate()
    return True, stale


def _render_operational_priority_controls(scn, *, key_prefix: str) -> None:
    """Render continuity, work/link energy and equipment-cycle priorities."""
    nonce = int(st.session_state.get("operational_priorities_nonce", 0))
    flash_key = f"operational_priorities_flash_{key_prefix}"
    with st.container(border=True):
        st.markdown("#### Operational priorities")
        st.caption(
            "Operational policy: protect continuous feed before selecting for "
            "energy or equipment-cycle efficiency. These coefficients are "
            "ASSUMED and uncalibrated; their different units are not directly "
            "comparable. Zero disables an individual penalty. Machine start, "
            "stop and group-change costs are relative event costs multiplied "
            "by the global w5 Machine changes priority.")
        st.caption(
            "The energy objective covers modelled productive work and traversed "
            "route links only. It excludes idle and positioning energy; displayed "
            "positioning remains a separate estimate based on assumed geometry.")
        flash = st.session_state.pop(flash_key, "")
        if flash:
            st.success(flash)

        current = operational_priority_values(scn)
        current_errors = validate_operational_priority_values(current)
        if current_errors:
            st.error(" ".join(current_errors))
        defaults = dict(OPERATIONAL_PRIORITY_DEFAULTS)
        with st.form(f"operational_priorities_{key_prefix}_{nonce}"):
            energy_enabled = st.toggle(
                "Enable productive-work and route-energy objective",
                value=(current.get("ENERGY_OBJECTIVE_ENABLED")
                       if isinstance(current.get("ENERGY_OBJECTIVE_ENABLED"), bool)
                       else bool(defaults["ENERGY_OBJECTIVE_ENABLED"])),
                help=("Opt-in while work/link coefficients are provisional. "
                      "This does not optimize or claim exact positioning energy."),
                key=f"operational_energy_enabled_{key_prefix}_{nonce}")
            columns = st.columns(3)
            edited: dict[str, object] = {
                "ENERGY_OBJECTIVE_ENABLED": bool(energy_enabled),
            }
            for index, (field, label, help_text) in enumerate(
                    OPERATIONAL_PRIORITY_SPECS):
                value = current.get(field, defaults[field])
                try:
                    valid = (math.isfinite(float(value)) and float(value) >= 0
                             and float(value) <= OBJECTIVE_WEIGHT_MAX)
                except (TypeError, ValueError):
                    valid = False
                if not valid:
                    value = defaults[field]
                edited[field] = int(columns[index % 3].number_input(
                    label, min_value=0, max_value=int(OBJECTIVE_WEIGHT_MAX),
                    value=int(round(float(value))), step=1, format="%d",
                    help=(f"{help_text} ASSUMED / uncalibrated. Internal "
                          f"config key: {field}."),
                    key=f"operational_priority_{key_prefix}_{field}_{nonce}"))
            apply_col, reset_col, _ = st.columns([1, 1, 3])
            apply_clicked = apply_col.form_submit_button(
                "Apply operational priorities", type="primary",
                width="stretch")
            reset_clicked = reset_col.form_submit_button(
                "Reset operational defaults", width="stretch")

        if not (apply_clicked or reset_clicked):
            return
        requested = defaults if reset_clicked else edited
        errors = validate_operational_priority_values(requested)
        if errors:
            st.error("Nothing was applied. " + " ".join(errors))
            return
        changed, stale = _commit_operational_priority_values(scn, requested)
        if changed:
            message = ("Operational defaults restored" if reset_clicked else
                       "Operational priorities applied")
            if stale:
                message += "; previous plan cleared"
        else:
            message = "No operational-priority changes to apply."
        st.session_state[flash_key] = message
        st.session_state["operational_priorities_nonce"] = nonce + 1
        st.rerun()


def _safe_continuity_display_row(
        row: dict[str, object]) -> tuple[str, float, float, int]:
    """Return form-safe values while validation keeps bad source data visible."""
    mode = str(row.get("mode", "none") or "none").strip().lower()
    if mode not in CONTINUITY_MODES:
        mode = "none"
    try:
        minimum_tph = float(row.get("minimum_tph", 0.0))
    except (TypeError, ValueError, OverflowError):
        minimum_tph = 0.0
    if not math.isfinite(minimum_tph) or minimum_tph < 0:
        minimum_tph = 0.0
    try:
        fraction = float(row.get("minimum_service_fraction", 0.0))
    except (TypeError, ValueError, OverflowError):
        fraction = 0.0
    if not math.isfinite(fraction) or not 0.0 <= fraction <= 1.0:
        fraction = 0.0
    try:
        maximum_gap = float(row.get("max_gap_min", 0))
    except (TypeError, ValueError, OverflowError):
        maximum_gap = 0.0
    if (not math.isfinite(maximum_gap) or maximum_gap < 0 or
            not maximum_gap.is_integer()):
        maximum_gap = 0.0
    return mode, minimum_tph, fraction, int(maximum_gap)


def _render_continuity_policy_controls(scn, *, key_prefix: str) -> None:
    """Render one non-addable continuity-policy row per destination."""
    nonce = int(st.session_state.get("continuity_policies_nonce", 0))
    flash_key = f"continuity_policies_flash_{key_prefix}"
    with st.container(border=True):
        st.markdown("#### Continuous-feed policy")
        st.caption(
            "Each configured destination has one fixed policy row. Only "
            "Continuous feed rows contribute feed-gap duration and episode "
            "penalties. Minimum feed 0 t/h with a 0 % service share uses the "
            "evidence-neutral no-feed test (less than 1 physical tonne in an "
            "active bucket) — which a one-tonne token delivery satisfies. "
            "The service share instead requires that fraction of the hour's "
            "OWN requested tonnes; it is demand-derived and asserts no "
            "approved stable feed rate.")
        st.warning(
            "A positive maximum gap is a hard planning constraint and can "
            "make the plan infeasible. Set maximum gap to 0 when no approved "
            "hard limit exists.")
        flash = st.session_state.pop(flash_key, "")
        if flash:
            st.success(flash)

        current = continuity_policy_values(scn)
        source = scn.config.get("CONTINUITY_POLICIES", {})
        current_errors = validate_continuity_policy_values(scn, source)
        if current_errors:
            st.error(
                "Stored policy needs correction. The form shows safe fallback "
                "values for invalid cells. " + " ".join(current_errors))

        with st.form(f"continuity_policies_{key_prefix}_{nonce}"):
            header = st.columns([1.4, 1.4, 1.0, 1.1, 1.0])
            header[0].markdown("**Destination**")
            header[1].markdown("**Operating mode**")
            header[2].markdown("**Minimum t/h**")
            header[3].markdown("**Served share of request**")
            header[4].markdown("**Maximum gap min**")
            edited: dict[str, dict[str, object]] = {}
            for destination in scn.destinations:
                destination_id = destination.id
                (mode, minimum_tph, fraction,
                 maximum_gap) = _safe_continuity_display_row(
                    current[destination_id])
                columns = st.columns([1.4, 1.4, 1.0, 1.1, 1.0])
                columns[0].markdown(
                    f"**{destination_id}**  \n"
                    f"{destination.site} · "
                    f"{getattr(destination, 'demand_kind', 'delivery')}")
                selected_mode = columns[1].selectbox(
                    f"Mode — {destination_id}",
                    options=list(CONTINUITY_MODES),
                    index=list(CONTINUITY_MODES).index(mode),
                    format_func=lambda value: CONTINUITY_MODE_LABELS[value],
                    label_visibility="collapsed",
                    key=(f"continuity_mode_{key_prefix}_{destination_id}_"
                         f"{nonce}"))
                selected_minimum = columns[2].number_input(
                    f"Minimum feed — {destination_id} [t/h]",
                    min_value=0.0, value=float(minimum_tph), step=1.0,
                    label_visibility="collapsed",
                    key=(f"continuity_minimum_{key_prefix}_{destination_id}_"
                         f"{nonce}"))
                selected_fraction = columns[3].number_input(
                    f"Served share — {destination_id} [0-1]",
                    min_value=0.0, max_value=1.0, value=float(fraction),
                    step=0.05, format="%.2f",
                    label_visibility="collapsed",
                    key=(f"continuity_fraction_{key_prefix}_{destination_id}_"
                         f"{nonce}"))
                selected_maximum = int(columns[4].number_input(
                    f"Maximum gap — {destination_id} [min]",
                    min_value=0, value=int(maximum_gap), step=1, format="%d",
                    label_visibility="collapsed",
                    key=(f"continuity_maximum_{key_prefix}_{destination_id}_"
                         f"{nonce}")))
                edited[destination_id] = {
                    "mode": selected_mode,
                    "minimum_tph": float(selected_minimum),
                    "minimum_service_fraction": float(selected_fraction),
                    "max_gap_min": selected_maximum,
                }

            basis = st.text_input(
                "Policy basis / approval reference",
                value=str(scn.config.get("CONTINUITY_POLICY_BASIS", "")),
                placeholder="e.g. operating standard, trial or approved limit",
                help=("Stored with the override audit entry. If blank, the "
                      "policy is explicitly marked assumed and unapproved."),
                key=f"continuity_basis_{key_prefix}_{nonce}")
            apply_clicked = st.form_submit_button(
                "Apply continuous-feed policies", type="primary")

        if not apply_clicked:
            return
        errors = validate_continuity_policy_values(scn, edited)
        if errors:
            st.error("Nothing was applied. " + " ".join(errors))
            return
        changed, stale = _commit_continuity_policy_values(
            scn, edited, basis=basis)
        message = (
            "Continuous-feed policies applied"
            + ("; previous plan cleared" if stale else "")
            if changed else
            "No continuous-feed policy changes to apply.")
        st.session_state[flash_key] = message
        st.session_state["continuity_policies_nonce"] = nonce + 1
        st.rerun()


def planner_weight_values(weights: ObjectiveWeights) -> dict[str, float]:
    values = {}
    for field, _label, _help in PLANNER_WEIGHT_SPECS:
        try:
            values[field] = float(getattr(weights, field))
        except (TypeError, ValueError):
            values[field] = float("nan")
    return values


PLANNER_WEIGHT_PRESETS = {
    "BALANCED": {
        "label": "Balanced (current baseline)",
        "description": "Current baseline coefficients for service, quality and stock protection.",
        "values": planner_weight_values(ObjectiveWeights()),
    },
    "QUALITY": {
        "label": "Quality emphasis",
        "description": "Increase soft quality and BPL give-away penalties; hard limits still apply.",
        "values": planner_weight_values(ObjectiveWeights(w2_offspec=500.0, w4_giveaway=10.0)),
    },
    "CONTINUITY": {
        "label": "Inventory continuity",
        "description": "Increase the penalty for falling below configured safety-stock targets.",
        "values": planner_weight_values(ObjectiveWeights(w6_safety=100.0)),
    },
}


def validate_planner_weight_values(
        values: dict[str, float]) -> tuple[list[str], str]:
    """Validate active weights and return a non-blocking dominance warning."""
    parsed: dict[str, float] = {}
    errors: list[str] = []
    for field, label, _help in PLANNER_WEIGHT_SPECS:
        try:
            value = float(values[field])
        except (KeyError, TypeError, ValueError):
            errors.append(f"{label} must be a number.")
            continue
        if not math.isfinite(value):
            errors.append(f"{label} must be finite.")
        elif (value < 0 or value > OBJECTIVE_WEIGHT_MAX or
              not value.is_integer()):
            errors.append(
                f"{label} must be a whole number between 0 and "
                f"{OBJECTIVE_WEIGHT_MAX:g}.")
        else:
            parsed[field] = value
    if errors:
        return errors, ""
    secondary = max(value for field, value in parsed.items()
                    if field != "w1_unmet")
    warning = ""
    if parsed["w1_unmet"] <= secondary:
        warning = (
            "Demand fulfilment is not larger than every secondary priority. "
            "A larger value is a demand-first heuristic, not a mathematical "
            "guarantee, because terms use different units."
        )
    return [], warning


def _commit_planner_weight_values(
        scn, values: dict[str, float]) -> tuple[bool, bool]:
    def unchanged(field, value):
        try:
            current = float(getattr(scn.weights, field))
        except (TypeError, ValueError):
            return False
        return math.isfinite(current) and math.isclose(
            current, float(value), rel_tol=0.0, abs_tol=1e-12)

    changed = any(not unchanged(field, value)
                  for field, value in values.items())
    if not changed:
        return False, False
    before = planner_weight_values(scn.weights)
    for field, value in values.items():
        setattr(scn.weights, field, float(value))
    history = scn.config.get("PLANNER_WEIGHT_OVERRIDES", [])
    history = list(history) if isinstance(history, list) else []
    history.append({"at": datetime.now(timezone.utc).isoformat(),
                    "before": before, "after": dict(values),
                    "basis": "ASSUMED operator policy; uncalibrated"})
    scn.config["PLANNER_WEIGHT_OVERRIDES"] = history
    stale = common.invalidate_outputs()
    common.revalidate()
    return True, stale


CAMPAIGN_FIELD_SPECS = (
    ("min_parcel_t", "Minimum parcel [t/bucket]",
     "Smallest material parcel worth commanding in one planning bucket. A "
     "smaller parcel stays possible but must pay the short-parcel penalty and "
     "is reported."),
    ("min_run_min", "Minimum run [min]",
     "Shortest continuous material run worth starting a machine for. The "
     "minute overlay lengthens a shorter burst up to this duration when the "
     "machine stable-rate floor and its own bucket allow it."),
    ("max_hold_min", "Maximum held gap [min]",
     "Longest blank gap between two bursts of the SAME activity group that is "
     "represented as the machine being deliberately held running. A longer "
     "gap is counted as a real stop followed by a real start."),
    ("min_dwell_buckets", "Minimum dwell [buckets]",
     "Consecutive buckets a machine stays in one activity group once started. "
     "1 disables the constraint; a larger value is a hard campaign-length "
     "rule and can reduce demand service."),
)


def campaign_policy_values(scn) -> dict[str, object]:
    """Return the editable scenario-default campaign policy."""
    raw = scn.config.get(CAMPAIGN_POLICY_KEY)
    raw = raw if isinstance(raw, dict) else {}
    block = raw.get("default")
    block = block if isinstance(block, dict) else {}
    values: dict[str, object] = {
        "enabled": bool(raw.get("enabled", True)),
        "prefer_continuous": bool(block.get(
            "prefer_continuous", POLICY_FIELDS["prefer_continuous"])),
        "allow_short_parcel": bool(block.get(
            "allow_short_parcel", POLICY_FIELDS["allow_short_parcel"])),
    }
    for field, _label, _help in CAMPAIGN_FIELD_SPECS:
        values[field] = block.get(field, POLICY_FIELDS[field])
    values[SHORT_PARCEL_PENALTY_KEY] = scn.config.get(
        SHORT_PARCEL_PENALTY_KEY, SHORT_PARCEL_PENALTY_DEFAULT)
    return values


def _commit_campaign_policy_values(
        scn, values: dict[str, object], *,
        basis: str = "") -> tuple[bool, bool]:
    """Commit the scenario-default campaign policy atomically.

    Per-machine-kind and per-machine overrides remain configuration-only; this
    form edits the scenario default, which is what the S0 demonstration uses.
    """
    raw = scn.config.get(CAMPAIGN_POLICY_KEY)
    raw = copy.deepcopy(raw) if isinstance(raw, dict) else \
        default_campaign_policy_config()
    block = dict(raw.get("default") if isinstance(
        raw.get("default"), dict) else {})
    block.update({
        "min_parcel_t": float(values["min_parcel_t"]),
        "min_run_min": int(values["min_run_min"]),
        "max_hold_min": int(values["max_hold_min"]),
        "min_dwell_buckets": int(values["min_dwell_buckets"]),
        "prefer_continuous": bool(values["prefer_continuous"]),
        "allow_short_parcel": bool(values["allow_short_parcel"]),
    })
    raw["default"] = block
    raw["enabled"] = bool(values["enabled"])
    penalty = int(float(values[SHORT_PARCEL_PENALTY_KEY]))
    errors = campaign_policy_errors(
        raw, (machine.id for machine in scn.machines))
    if penalty < 0:
        errors.append(SHORT_PARCEL_PENALTY_KEY + " must be 0 or more")
    if errors:
        raise ValueError(" ".join(errors))
    if raw == scn.config.get(CAMPAIGN_POLICY_KEY) and \
            penalty == scn.config.get(SHORT_PARCEL_PENALTY_KEY):
        return False, False
    before = copy.deepcopy(scn.config.get(CAMPAIGN_POLICY_KEY))
    recorded_basis = str(basis or "").strip() or CAMPAIGN_POLICY_BASIS
    raw["basis"] = recorded_basis
    scn.config[CAMPAIGN_POLICY_KEY] = raw
    scn.config[SHORT_PARCEL_PENALTY_KEY] = penalty
    scn.config["CAMPAIGN_POLICY_BASIS"] = recorded_basis
    history = scn.config.get("CAMPAIGN_POLICY_OVERRIDES", [])
    history = list(history) if isinstance(history, list) else []
    history.append({"at": datetime.now(timezone.utc).isoformat(),
                    "before": before, "after": copy.deepcopy(raw),
                    "basis": recorded_basis})
    scn.config["CAMPAIGN_POLICY_OVERRIDES"] = history
    stale = common.invalidate_outputs()
    common.revalidate()
    return True, stale


def _render_campaign_policy_controls(scn, *, key_prefix: str) -> None:
    """Render the campaign / parcel / minimum-run policy editor."""
    nonce = int(st.session_state.get("campaign_policy_nonce", 0))
    flash_key = f"campaign_policy_flash_{key_prefix}"
    with st.container(border=True):
        st.markdown("#### Campaign, parcel and minimum-run policy")
        st.caption(
            "This is where job fragmentation is controlled, in the scheduling "
            "model rather than in the chart. Without a parcel floor the hourly "
            "activity rule degenerates to one physical tonne for every machine "
            "with no stable-rate minimum, which is every stacker here.")
        st.warning(
            "ASSUMED demonstration values \u2014 NOT plant-approved operating "
            "limits. Replace them with dated equipment and operating-philosophy "
            "evidence: minimum stable belt/drive loading, permitted start "
            "frequency, drive-thermal restart limits, and how long the plant "
            "accepts running an empty belt rather than stopping it.")
        flash = st.session_state.pop(flash_key, "")
        if flash:
            st.success(flash)
        current = campaign_policy_values(scn)
        with st.form(f"campaign_policy_{key_prefix}_{nonce}"):
            edited: dict[str, object] = {}
            edited["enabled"] = st.checkbox(
                "Apply the campaign / parcel / minimum-run policy",
                value=bool(current["enabled"]),
                help=("Unchecked reproduces the previous behaviour exactly: no "
                      "parcel floor, no minimum run and no stable-run "
                      "lengthening."),
                key=f"campaign_enabled_{key_prefix}_{nonce}")
            columns = st.columns(len(CAMPAIGN_FIELD_SPECS))
            for column, (field, label, help_text) in zip(
                    columns, CAMPAIGN_FIELD_SPECS):
                if field == "min_parcel_t":
                    edited[field] = column.number_input(
                        label, min_value=0.0,
                        value=float(current[field]), step=25.0,
                        help=help_text,
                        key=f"campaign_{field}_{key_prefix}_{nonce}")
                else:
                    minimum = 1 if field == "min_dwell_buckets" else 0
                    edited[field] = int(column.number_input(
                        label, min_value=minimum,
                        value=max(int(current[field]), minimum), step=1,
                        format="%d", help=help_text,
                        key=f"campaign_{field}_{key_prefix}_{nonce}"))
            tail = st.columns(3)
            edited["allow_short_parcel"] = tail[2].checkbox(
                "Allow a priced parcel below the floor",
                value=bool(current["allow_short_parcel"]),
                help=("Checked keeps the floor relaxable: a smaller parcel "
                      "stays possible, is priced and is reported, so a "
                      "genuinely necessary short job is never lost. Unchecked "
                      "makes the floor a hard constraint, which may reduce "
                      "demand service or make the plan infeasible \u2014 "
                      "reported as unmet demand, never hidden."),
                key=f"campaign_allowshort_{key_prefix}_{nonce}")
            edited["prefer_continuous"] = tail[0].checkbox(
                "Join adjacent campaign hours into one continuous run",
                value=bool(current["prefer_continuous"]),
                help=("Lengthens each hour of a multi-hour campaign towards the "
                      "whole bucket, never below the machine stable minimum "
                      "rate, so the schedule shows one run instead of a train "
                      "of maximum-rate bursts separated by blank time."),
                key=f"campaign_continuous_{key_prefix}_{nonce}")
            edited[SHORT_PARCEL_PENALTY_KEY] = int(tail[1].number_input(
                "Short-parcel penalty", min_value=0,
                value=int(current[SHORT_PARCEL_PENALTY_KEY]), step=5,
                format="%d",
                help=("Cost of one bucket below the parcel floor, scaled by the "
                      "Machine-changes priority. A short parcel that is "
                      "genuinely necessary is still produced, priced and "
                      "reported \u2014 never discarded."),
                key=f"campaign_penalty_{key_prefix}_{nonce}"))
            basis = st.text_input(
                "Policy basis / approval reference",
                value=str(scn.config.get(
                    "CAMPAIGN_POLICY_BASIS", CAMPAIGN_POLICY_BASIS)),
                key=f"campaign_basis_{key_prefix}_{nonce}")
            submitted = st.form_submit_button("Apply campaign policy")
        if submitted:
            try:
                changed, stale = _commit_campaign_policy_values(
                    scn, edited, basis=basis)
            except ValueError as error:
                st.error(str(error))
                return
            message = ("Campaign policy updated" if changed
                       else "No campaign-policy changes to apply.")
            if changed and stale:
                message += "; previous plan cleared"
            st.session_state[flash_key] = message
            st.session_state["campaign_policy_nonce"] = nonce + 1
            st.rerun()


def render_planner_priorities(scn, *, key_prefix: str) -> None:
    """Shared active-objective editor for visible Plan and Advanced pages."""
    _render_continuity_policy_controls(scn, key_prefix=key_prefix)
    _render_campaign_policy_controls(scn, key_prefix=key_prefix)
    _render_operational_priority_controls(scn, key_prefix=key_prefix)
    nonce = int(st.session_state.get("planner_weights_nonce", 0))
    flash_key = f"planner_weights_flash_{key_prefix}"
    with st.container(border=True):
        st.markdown("#### Planner priorities")
        if scn.config.get("OBJECTIVE_MODE", "weighted") == "service_first":
            st.info(
                "Service-first mode: final on-time delivery earns priority credit only inside "
                "configured receiving intervals. Order priorities rank qualified service; "
                "these weights do not change the objective hierarchy. Machine events, "
                "quality deviations and short-parcel preferences are optimized in later "
                "bounded passes. Configure → Operational configuration contains the "
                "objective mode, complete-job minimum (default 0 t) and service-loss allowance.")
        st.caption(
            "Larger coefficients give an outcome more influence when the "
            "optimiser compares feasible plans. Changes apply to the next "
            "plan. Zero disables a term; any positive value remains active "
            "at the solver's minimum integer coefficient."
        )
        flash = st.session_state.pop(flash_key, "")
        if flash:
            st.success(flash)
        current = planner_weight_values(scn.weights)
        current_errors, current_warning = validate_planner_weight_values(current)
        if current_errors:
            st.error(" ".join(current_errors))
        elif current_warning:
            st.warning(current_warning)

        defaults = planner_weight_values(ObjectiveWeights())
        preset_id = st.selectbox(
            "Priority preset", list(PLANNER_WEIGHT_PRESETS),
            format_func=lambda key: PLANNER_WEIGHT_PRESETS[key]["label"],
            key=f"planner_preset_{key_prefix}")
        preset = PLANNER_WEIGHT_PRESETS[preset_id]
        st.caption(preset["description"])
        st.caption(" · ".join(
            f"{label}: {preset['values'][field]:g}"
            for field, label, _ in PLANNER_WEIGHT_SPECS))
        st.caption(
            "Preset weights are assumed, uncalibrated policy choices. Terms "
            "use different units: a larger demand coefficient alone does not "
            "guarantee demand takes precedence. Compare operational KPIs "
            "under identical inputs before selecting a policy. Configure "
            "mandatory quality bounds as hard Receiving limits.")
        preset_clicked = st.button(
            "Apply priority preset", key=f"apply_planner_preset_{key_prefix}",
            disabled=bool(st.session_state.get("solving", False)))
        with st.form(f"planner_priorities_{key_prefix}_{nonce}"):
            columns = st.columns(3)
            edited: dict[str, float] = {}
            for index, (field, label, help_text) in enumerate(
                    PLANNER_WEIGHT_SPECS):
                value = current.get(field, defaults[field])
                if (not math.isfinite(value) or value < 0 or
                        value > OBJECTIVE_WEIGHT_MAX):
                    value = defaults[field]
                edited[field] = float(columns[index % 3].number_input(
                    label, min_value=0, max_value=int(OBJECTIVE_WEIGHT_MAX),
                    value=int(round(value)), step=1, format="%d",
                    help=(f"{help_text} Whole-number priority; 0 disables "
                          f"the term. Internal field: {field}."),
                    key=f"planner_priority_{key_prefix}_{field}_{nonce}"))
            st.caption(
                "Process cost is omitted because no commissioned cost curves "
                "are configured and that objective term is inactive.")
            apply_col, reset_col, _ = st.columns([1, 1, 3])
            apply_clicked = apply_col.form_submit_button(
                "Apply priorities", type="primary", width="stretch")
            reset_clicked = reset_col.form_submit_button(
                "Reset defaults", width="stretch")

        if not (apply_clicked or reset_clicked or preset_clicked):
            return
        requested = (preset["values"] if preset_clicked else
                     defaults if reset_clicked else edited)
        errors, warning = validate_planner_weight_values(requested)
        if errors:
            st.error("Nothing was applied. " + " ".join(errors))
            return
        changed, stale = _commit_planner_weight_values(scn, requested)
        scn.config["ACTIVE_PLANNER_WEIGHT_PRESET"] = (
            preset_id if preset_clicked else "BALANCED" if reset_clicked else "CUSTOM")
        if changed:
            message = (f"{preset['label']} priorities applied" if preset_clicked
                       else "Default planner priorities restored" if reset_clicked
                       else "Planner priorities applied")
            if stale:
                message += "; previous plan cleared"
        else:
            message = "No planner-priority changes to apply."
        if warning:
            message += " Warning: " + warning
        st.session_state[flash_key] = message
        st.session_state["planner_weights_nonce"] = nonce + 1
        st.rerun()


def _load_selected_planning_scenario():
    """Load the built-in baseline or a user-created planning workspace."""
    code = st.session_state.get(
        "planning_scenario_code", common.DEFAULT_PLANNING_SCENARIO)
    target = common.planning_scenario_dataset(code)
    if st.session_state.get("scenario_name") != target:
        st.session_state._pending_planning_scenario_code = code
        st.session_state.flash = (
            f"{common.planning_scenario_label(code)} loaded — "
            "no plan generated yet."
        )
        st.rerun()


def effective_horizon_minutes(sim) -> int:
    """Return the canonical horizon while preserving legacy scenarios."""
    helper = getattr(sim, "effective_horizon_minutes", None)
    if callable(helper):
        return max(int(helper()), 1)
    configured = int(getattr(sim, "horizon_minutes", 0) or 0)
    if configured > 0:
        return configured
    return max(int(round(
        float(sim.horizon_buckets) * float(sim.dt_h) * 60.0)), 1)


def planning_clock_label(scn, minute: int) -> str:
    """Operator clock for an exact minute offset inside the planning week."""
    minute = max(int(minute), 0)
    helper = getattr(scn.sim, "minute_dt", None)
    if callable(helper):
        stamp = helper(minute)
    else:
        stamp = scn.sim.bucket_dt(0) + pd.Timedelta(minutes=minute)
    day_number = minute // (24 * 60) + 1
    return f"Day {day_number} · {stamp:%a %d %b %H:%M} · +{minute:,} min"


def planning_grid_facts(sim) -> dict[str, str | int]:
    """Small, stable UI contract describing the active decision grid."""
    horizon_min = effective_horizon_minutes(sim)
    horizon_h = horizon_min / 60.0
    horizon_days = horizon_h / 24.0
    mixed = bool(getattr(sim, "mixed_resolution_enabled", False))
    if mixed:
        near_step = max(int(getattr(sim, "near_term_step_min", 15)), 1)
        near_horizon = min(
            max(int(getattr(sim, "near_term_horizon_min", 240)), 0),
            horizon_min,
        )
        far_step = max(int(getattr(sim, "far_term_step_min", 60)), 1)
        near_slots = math.ceil(near_horizon / near_step)
        far_slots = math.ceil(max(horizon_min - near_horizon, 0) / far_step)
        grid = f"{near_step} min near term → {far_step} min tail"
        decisions = near_slots + far_slots
    else:
        step = max(int(round(float(sim.dt_h) * 60.0)), 1)
        decisions = math.ceil(horizon_min / step)
        grid = f"{decisions} × {step} min"
    return {
        "horizon_min": horizon_min,
        "horizon_h": f"{horizon_h:g}",
        "horizon_days": f"{horizon_days:g}",
        "grid": grid,
        "decisions": decisions,
        "mixed": int(mixed),
    }


HOURLY_WEEK_MODE = "Weekly tactical — 168 × 60 min (default)"
ROLLING_REFINEMENT_MODE = (
    "Rolling refinement — 15 min near term → 60 min tail"
)


def planning_resolution_mode(sim) -> str:
    """Operator label for the two commissioned weekly planning modes."""
    return (ROLLING_REFINEMENT_MODE
            if bool(getattr(sim, "mixed_resolution_enabled", False))
            else HOURLY_WEEK_MODE)


def stage_weekly_resolution_preset(sim, mode: str):
    """Return a weekly preset without mutating the active scenario."""
    staged = copy.deepcopy(sim)
    staged.horizon_buckets = 168
    staged.dt_h = 1.0
    staged.horizon_minutes = 7 * 24 * 60
    staged.far_term_step_min = 60
    if mode == ROLLING_REFINEMENT_MODE:
        staged.mixed_resolution_enabled = True
        staged.near_term_step_min = 15
        staged.near_term_horizon_min = 4 * 60
        staged.replan_interval_min = 15
        staged.commit_window_min = 30
    else:
        staged.mixed_resolution_enabled = False
    validator = getattr(staged, "mixed_resolution_errors", None)
    errors = list(validator()) if callable(validator) else []
    return staged, errors


def _render_planning_envelope_editor(scn) -> None:
    """Popover editor shared by Plan and advanced planning pages."""
    facts = planning_grid_facts(scn.sim)
    horizon_min = int(facts["horizon_min"])
    changes = list(getattr(scn, "calendars", []) or [])
    outages = sum(
        str(getattr(entry, "action", "UNAVAILABLE") or "UNAVAILABLE")
        .upper() != "RATE_CAP"
        for entry in changes
    )
    derates = len(changes) - outages
    with st.popover("Adjust planning envelope", width="stretch"):
        st.caption(
            f"{planning_clock_label(scn, 0)} → "
            f"{planning_clock_label(scn, horizon_min)} · "
            f"{outages} outage/maintenance windows · {derates} rate caps."
        )
        mode = planning_resolution_mode(scn.sim)
        selected = st.radio(
            "Resolution mode for the next plan",
            [HOURLY_WEEK_MODE, ROLLING_REFINEMENT_MODE],
            index=0 if mode == HOURLY_WEEK_MODE else 1,
            horizontal=True,
            key=("plan_resolution_mode_" +
                 ("rolling" if mode == ROLLING_REFINEMENT_MODE else
                  "hourly")),
            help=("The default creates 168 hourly decisions. Rolling mode "
                  "refines only the moving 4-hour near-term window."),
        )
        if selected != mode:
            st.warning(
                "This changes the next solve grid and clears the current plan. "
                "Demand and planned contingency windows are preserved.")
            if st.button(
                    "Apply selected resolution",
                    key=("apply_plan_resolution_" +
                         ("rolling" if selected == ROLLING_REFINEMENT_MODE
                          else "hourly"))):
                staged, errors = stage_weekly_resolution_preset(
                    scn.sim, selected)
                if errors:
                    st.error("Nothing was applied. " + " · ".join(errors))
                else:
                    scn.sim = staged
                    stale = common.invalidate_outputs()
                    common.revalidate()
                    st.session_state["resolution_mode_flash"] = (
                        f"{selected} applied"
                        + (" — previous plan cleared" if stale else ""))
                    st.rerun()
        flash = st.session_state.pop("resolution_mode_flash", "")
        if flash:
            st.success(flash)


def render_planning_operating_context(scn=None, *, show_editor=True) -> None:
    """Show the active planning envelope as one compact summary strip."""
    scn = scn or common.sc()
    facts = planning_grid_facts(scn.sim)
    changes = list(getattr(scn, "calendars", []) or [])
    mixed = bool(facts["mixed"])
    cadence = max(int(getattr(scn.sim, "replan_interval_min", 15)), 1)

    replan_label = f"Every {cadence} min" if mixed else "On event/deviation"
    st.markdown(
        f'<div class="planning-envelope-strip">'
        f'<b>Active planning envelope</b>'
        f'<span><small>Horizon</small>{facts["horizon_days"]} days · '
        f'{facts["horizon_h"]} h</span>'
        f'<span><small>Resolution</small>{facts["grid"]}</span>'
        f'<span><small>Controls</small>{len(changes)} windows</span>'
        f'<span><small>Replanning</small>{replan_label}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    if show_editor:
        _render_planning_envelope_editor(scn)


def planning_resolution_summary(sim) -> str:
    """Concise operator-facing statement of the active planning grid."""
    horizon_min = effective_horizon_minutes(sim)
    horizon_h = horizon_min / 60.0
    horizon_days = horizon_h / 24.0
    if not bool(getattr(sim, "mixed_resolution_enabled", False)):
        step_min = max(int(round(float(sim.dt_h) * 60.0)), 1)
        decisions = math.ceil(horizon_min / step_min)
        return (
            f"Uniform weekly planning · {horizon_days:g} days / "
            f"{horizon_h:g} h / {horizon_min:,} min · {decisions} "
            f"{step_min}-min decisions · frozen window "
            f"{int(sim.frozen_buckets)} step(s)."
        )
    near_horizon_min = min(
        int(getattr(sim, "near_term_horizon_min", 240)), horizon_min)
    near_step_min = int(getattr(sim, "near_term_step_min", 15))
    far_step_min = int(getattr(sim, "far_term_step_min", 60))
    replan_min = int(getattr(sim, "replan_interval_min", 15))
    commit_min = int(getattr(sim, "commit_window_min", 30))
    return (
        f"Mixed-resolution weekly planning · {horizon_days:g} days / "
        f"{horizon_h:g} h / {horizon_min:,} min "
        f"· first {near_horizon_min:,} min ({near_horizon_min / 60:g} h) "
        f"at {near_step_min}-min steps · remaining horizon at "
        f"{far_step_min}-min steps · replan every {replan_min} min · "
        f"first {commit_min} min committed."
    )


def stage_planning_settings(
        sim, *, mixed_resolution_enabled: bool, horizon_minutes: int,
        near_term_step_min: int, near_term_horizon_min: int,
        far_term_step_min: int, replan_interval_min: int,
        commit_window_min: int, max_solve_s: float, seed: int,
        deviation_tolerance: float):
    """Stage and validate planning settings without partially mutating UI state."""
    staged = copy.deepcopy(sim)
    staged.mixed_resolution_enabled = bool(mixed_resolution_enabled)
    staged.horizon_minutes = int(horizon_minutes)
    staged.near_term_step_min = int(near_term_step_min)
    staged.near_term_horizon_min = int(near_term_horizon_min)
    staged.far_term_step_min = int(far_term_step_min)
    staged.replan_interval_min = int(replan_interval_min)
    staged.commit_window_min = int(commit_window_min)
    staged.max_solve_s = float(max_solve_s)
    staged.seed = int(seed)
    staged.deviation_tolerance = float(deviation_tolerance)

    validator = getattr(staged, "mixed_resolution_errors", None)
    if callable(validator):
        errors = list(validator())
    else:
        errors = []
        if staged.horizon_minutes <= 0:
            errors.append("Total horizon must be above 0 minutes.")
        if staged.near_term_step_min <= 0 or staged.far_term_step_min <= 0:
            errors.append("Planning step lengths must be above 0 minutes.")
        if not 0 < staged.near_term_horizon_min <= staged.horizon_minutes:
            errors.append(
                "Near-term horizon must be above 0 and no longer than the "
                "total horizon.")
        if staged.replan_interval_min <= 0:
            errors.append("Replan cadence must be above 0 minutes.")
        if not 0 <= staged.commit_window_min <= staged.horizon_minutes:
            errors.append(
                "Commit window must be between 0 and the total horizon.")
    return staged, errors


def render_planning_scenario_selector():
    """Render scenario selection, compact envelope and workspace actions."""
    render_key = "_plan_scenario_selector_rendered_this_run"
    if st.session_state.get(render_key):
        return
    st.session_state[render_key] = True

    scenario_codes = common.planning_scenario_codes()
    scenario_labels = {
        item: common.planning_scenario_label(item)
        for item in scenario_codes
    }
    code = st.selectbox(
        "Planning scenario",
        options=scenario_codes,
        key="planning_scenario_code",
        format_func=lambda item: scenario_labels.get(item, item),
        on_change=_load_selected_planning_scenario,
        help="S0 is the protected baseline. New scenarios are independent "
             "copies that retain their own inputs and planning settings.",
    )
    target = common.planning_scenario_dataset(code)
    active = st.session_state.get("scenario_name") == target
    if active:
        scn = common.sc()
        horizon_min = effective_horizon_minutes(scn.sim)
        st.caption(
            f"Active · BG + LO + MPH · {horizon_min / 60:g} h / "
            f"{horizon_min:,} min horizon · "
            + ("protected baseline S0" if code == "S0"
               else "editable scenario workspace")
        )
        render_planning_operating_context(scn, show_editor=False)
    else:
        st.warning(
            "A diagnostic or imported dataset is active. S0 always maps to "
            "the built-in S3 three-site model."
        )
        if st.button("Load S0", key="load_s0_from_plan"):
            common.load_planning_scenario(code)
            st.session_state.flash = (
                f"{common.planning_scenario_label(code)} loaded — "
                "no plan generated yet."
            )
            st.rerun()

    actions = st.columns([1.35, 1.05, 1.15, 3.45])
    with actions[0]:
        _render_planning_envelope_editor(common.sc())
    with actions[1].popover("＋ New scenario", width="stretch"):
        st.markdown("**Create scenario**")
        st.caption(
            "Copies the active scenario, including demand, inventory, routes, "
            "availability, blending and planning settings.")
        new_name = st.text_input(
            "Scenario name", key="new_planning_scenario_name",
            max_chars=60, placeholder="e.g. High demand week")
        if st.button(
                "Create from active", key="create_planning_scenario",
                type="primary", width="stretch"):
            try:
                new_code = common.create_custom_planning_scenario(new_name)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state._pending_planning_scenario_code = new_code
                st.session_state.flash = (
                    f"{common.planning_scenario_label(new_code)} created from "
                    f"{common.planning_scenario_label(code)}."
                )
                st.rerun()

        if code != common.DEFAULT_PLANNING_SCENARIO:
            st.divider()
            st.markdown("**Rename active scenario**")
            current_label = common.planning_scenario_label(code).split(
                " — ", 1)[-1]
            renamed = st.text_input(
                "New name", value=current_label,
                key=f"rename_planning_scenario_{code}", max_chars=60)
            if st.button(
                    "Save name", key=f"save_planning_scenario_name_{code}",
                    width="stretch"):
                try:
                    common.rename_custom_planning_scenario(code, renamed)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.session_state.flash = f"{code} renamed."
                    st.rerun()

    if actions[2].button(
            "Configure active", key="configure_active_scenario",
            width="stretch",
            help="Open the scenario-specific operational inputs. Demand, "
                 "routing and blending remain available in the Plan section."):
        common.goto_page(common.CONFIGURE_LANDING_PAGE)
        st.rerun()


def render_plan_run_panel():
    """Compact validation/result panel; Generate Plan lives in the sidebar."""
    scn = common.sc()
    p = common.plan()
    issues = st.session_state.get("issues", [])
    n_err = sum(1 for issue in issues if issue.severity == "ERROR")
    n_wrn = sum(1 for issue in issues if issue.severity == "WARNING")
    horizon_min = effective_horizon_minutes(scn.sim)
    demand_t = sum(
        scn.demand_tph(dest.id, bucket) * scn.sim.dt_h
        for dest in scn.destinations
        for bucket in range(scn.sim.horizon_buckets)
    )
    solving = bool(
        st.session_state.get("solving")
        or st.session_state.get("solve_request"))

    with st.container(border=True):
        st.markdown("#### Run plan")
        summary = st.columns(4)
        summary[0].metric(
            "Planning scenario",
            common.planning_scenario_code(scn.name)
            or scn.name.split(" —")[0],
        )
        summary[1].metric(
            "Horizon", f"{horizon_min / 60:g} h / {horizon_min:,} min")
        summary[2].metric("Demand", f"{demand_t / 1000:,.1f} kt")
        summary[3].metric(
            "Input health",
            f"{n_err} errors · {n_wrn} warnings")

        actions = st.columns([1, 1.4])
        if actions[0].button(
                "Validate inputs", key="plan_validate_inputs",
                width="stretch"):
            refreshed = common.revalidate()
            errors = sum(1 for issue in refreshed
                         if issue.severity == "ERROR")
            warnings = sum(1 for issue in refreshed
                           if issue.severity == "WARNING")
            (st.success if errors == 0 else st.error)(
                f"Validation complete: {errors} errors · "
                f"{warnings} warnings.")
        if p is not None:
            if actions[1].button(
                    "Open Jobs & Schedule", key="plan_open_jobs",
                    width="stretch"):
                common.goto_page("9 · Job Schedule")
                st.rerun()
        elif n_err:
            actions[1].caption("Resolve validation errors before solving.")
        else:
            actions[1].caption(
                "Use **Generate Plan** in the sidebar after reviewing inputs. "
                "The bounded weekly solve reports staged elapsed status while "
                "it runs.")

        if solving:
            st.info("Plan generation is in progress. The staged solver status "
                    "appears above this page.")
        elif p is not None:
            unmet_t = sum(sum(values) for values in p.unmet.values())
            runs = int(p.breakdown.get("physical_runs", 0) or 0)
            summary = (
                f"Current plan: {p.status} · {len(p.jobs)} allocation jobs"
                + (f" / {runs} physical runs" if runs else "")
                + f" · {p.solver_wall_s:.1f} s")
            # A solver status is not a service statement: FEASIBLE only means
            # every hard constraint was satisfied. Never present unserved
            # demand in a success banner.
            if unmet_t > 1e-6:
                st.warning(
                    f"{summary} · **{unmet_t:,.0f} t of requested demand "
                    "NOT served.** "
                    f"{p.status} means a feasible plan was found, not that "
                    "demand is met.")
            else:
                st.success(f"{summary} · all requested demand served")


_CONTINGENCY_SCOPE_TYPES = (
    "Whole system",
    "Site / plant",
    "Process / receiving line",
    "Routing",
    "Machine",
)
_CONTINGENCY_ACTION_LABELS = {
    "UNAVAILABLE": "Unavailable / maintenance",
    "RATE_CAP": "Rate cap",
}
_CONTINGENCY_ACTIONS = {
    label: action for action, label in _CONTINGENCY_ACTION_LABELS.items()
}


def _contingency_scope_parts(scope: str) -> tuple[str, str]:
    """Decode a stored calendar scope into operator-facing fields."""
    if scope == "GLOBAL":
        return "Whole system", "GLOBAL"
    if scope.startswith("SITE:"):
        return "Site / plant", scope[5:]
    if scope.startswith("LINE:"):
        return "Process / receiving line", scope[5:]
    if scope.startswith("LINK:"):
        return "Routing", scope[5:]
    return "Machine", scope


def _contingency_scope_value(scope_type: str, target: str) -> str:
    if scope_type == "Whole system":
        return "GLOBAL"
    prefixes = {
        "Site / plant": "SITE:",
        "Process / receiving line": "LINE:",
        "Routing": "LINK:",
        "Machine": "",
    }
    return prefixes[scope_type] + target


def render_planned_contingencies(scn=None):
    """Edit exact, time-bounded availability constraints used by planning."""
    scn = scn or common.sc()
    horizon_min = effective_horizon_minutes(scn.sim)
    native_min = scn.sim.native_bucket_minutes()
    flash = st.session_state.pop("planned_contingencies_flash", "")
    if flash:
        st.success(flash)

    machine_ids = {machine.id for machine in scn.machines}
    link_ids = {link.id for link in scn.links}
    site_ids = {site.id for site in scn.sites}
    process_stage_ids = {
        transfer.process_stage for transfer in scn.process_transfers
        if transfer.process_stage
    }
    line_ids = (
        set(PROCESS_LINE_STAGE_MAP)
        | process_stage_ids
        | {destination.id for destination in scn.destinations}
    )
    allowed_targets = {
        "Whole system": {"GLOBAL"},
        "Site / plant": site_ids,
        "Process / receiving line": line_ids,
        "Routing": link_ids,
        "Machine": machine_ids,
    }

    rows = []
    for entry in scn.calendars:
        scope_type, target = _contingency_scope_parts(entry.scope)
        start_min, end_min = scn.calendar_interval_minutes(entry)
        action = str(getattr(entry, "action", "UNAVAILABLE") or
                     "UNAVAILABLE").strip().upper()
        rows.append({
            "Change": _CONTINGENCY_ACTION_LABELS.get(action, action),
            "Type": scope_type,
            "Target": target,
            "Start [min]": start_min,
            "End [min]": end_min,
            "From": planning_clock_label(scn, start_min),
            "To (exclusive)": planning_clock_label(scn, end_min),
            "Duration": f"{end_min - start_min:,} min",
            "Rate cap [t/h]": getattr(entry, "rate_tph", None),
            "Planning effect": (
                "Capacity limited inside this window"
                if action == "RATE_CAP" else
                "Unavailable; reroute/reschedule or report unmet"),
            "Reason": entry.reason,
        })
    frame = pd.DataFrame(rows, columns=[
        "Change", "Type", "Target", "Start [min]", "End [min]",
        "From", "To (exclusive)", "Duration", "Rate cap [t/h]",
        "Planning effect", "Reason"])

    st.caption(
        "Schedule an outage or a physical/as-handled rate ceiling. The start "
        "is included and the end is excluded. Enter exact minute offsets; "
        "the calendar date/time preview refreshes after Apply."
    )
    outage_count = sum(row["Change"] != "Rate cap" for row in rows)
    rate_count = len(rows) - outage_count
    summary = st.columns(4)
    summary[0].metric("Outage / maintenance rows", outage_count)
    summary[1].metric("Derate rows", rate_count)
    summary[2].metric(
        "Affected targets", len({row["Target"] for row in rows}))
    summary[3].metric(
        "Planning horizon", f"{horizon_min / 1440:g} days")

    # A shared target list keeps rows editable. Apply performs the dependent
    # type/target validation atomically before changing the scenario.
    target_options = sorted(
        set().union(*allowed_targets.values())
        | {row["Target"] for row in rows}
    )
    edited = st.data_editor(
        frame,
        key="planned_contingencies_editor",
        num_rows="dynamic",
        width="stretch",
        hide_index=True,
        disabled=["From", "To (exclusive)", "Duration", "Planning effect"],
        column_config={
            "Change": st.column_config.SelectboxColumn(
                "Change", options=list(_CONTINGENCY_ACTIONS), required=True),
            "Type": st.column_config.SelectboxColumn(
                "Affected element", options=list(_CONTINGENCY_SCOPE_TYPES),
                required=True),
            "Target": st.column_config.SelectboxColumn(
                "Target ID", options=target_options, required=True,
                help="Choose a target that matches the affected element."),
            "Start [min]": st.column_config.NumberColumn(
                "Start [min]", min_value=0, max_value=horizon_min,
                step=1, required=True),
            "End [min]": st.column_config.NumberColumn(
                "End [min] (exclusive)", min_value=1,
                max_value=horizon_min, step=1, required=True),
            "Rate cap [t/h]": st.column_config.NumberColumn(
                "Rate cap [t/h]", min_value=0.0, step=50.0,
                help="Required only for a rate-cap change."),
            "From": st.column_config.TextColumn(
                "From (calendar clock)",
                help="Derived from Start [min] and the scenario start."),
            "To (exclusive)": st.column_config.TextColumn(
                "To (calendar clock, exclusive)",
                help="Derived from End [min]; the endpoint is not affected."),
            "Duration": st.column_config.TextColumn("Duration"),
            "Planning effect": st.column_config.TextColumn(
                "Expected planning effect"),
            "Reason": st.column_config.TextColumn(
                "Reason", default="planned resource change"),
        },
    )
    st.caption(
        "Process-line changes cover their complete modelled chain. Route "
        "changes affect the selected model link. Use Routing availability "
        "for full-run changes and diagram-based editing."
    )
    if not st.button(
            "Apply planned contingencies",
            key="apply_planned_contingencies", type="primary"):
        return

    staged: list[CalendarEntry] = []
    errors: list[str] = []

    def _cell_text(value, default: str = "") -> str:
        if value is None or pd.isna(value):
            return default
        return str(value).strip() or default

    for row_number, (_, row) in enumerate(edited.iterrows(), start=1):
        action_label = _cell_text(row.get("Change"))
        action = _CONTINGENCY_ACTIONS.get(action_label, "")
        scope_type = _cell_text(row.get("Type"))
        target = _cell_text(row.get("Target"))
        reason = _cell_text(row.get("Reason"), "planned resource change")
        if not scope_type and not target:
            continue
        if not action:
            errors.append(f"Row {row_number}: choose a change type.")
            continue
        if scope_type not in allowed_targets:
            errors.append(
                f"Row {row_number}: choose an affected element.")
            continue
        if scope_type == "Whole system":
            target = "GLOBAL"
        if target not in allowed_targets[scope_type]:
            errors.append(
                f"Row {row_number}: {target or 'target'} is not a valid "
                f"{scope_type.lower()} target.")
            continue
        try:
            start_value = float(row["Start [min]"])
            end_value = float(row["End [min]"])
            if not start_value.is_integer() or not end_value.is_integer():
                raise ValueError
            start_min = int(start_value)
            end_min = int(end_value)
        except (KeyError, TypeError, ValueError):
            errors.append(
                f"Row {row_number}: start and end must be whole minutes.")
            continue
        if not 0 <= start_min < end_min <= horizon_min:
            errors.append(
                f"Row {row_number}: require 0 ≤ start < end ≤ "
                f"{horizon_min} minutes.")
            continue
        rate_tph = None
        if action == "RATE_CAP":
            if scope_type in {"Whole system", "Site / plant"}:
                errors.append(
                    f"Row {row_number}: rate caps require a machine, route "
                    "or process/receiving line target.")
                continue
            try:
                rate_tph = float(row["Rate cap [t/h]"])
            except (KeyError, TypeError, ValueError):
                rate_tph = float("nan")
            if not math.isfinite(rate_tph) or rate_tph <= 0:
                errors.append(
                    f"Row {row_number}: rate cap must be above 0 t/h.")
                continue
        staged.append(CalendarEntry(
            scope=_contingency_scope_value(scope_type, target),
            start_bucket=start_min // native_min,
            end_bucket=max(0, math.ceil(end_min / native_min) - 1),
            reason=reason or "planned resource change",
            start_minute=start_min,
            end_minute=end_min,
            action=action,
            rate_tph=rate_tph,
        ))

    if errors:
        st.error("Nothing was applied. " + " · ".join(errors[:8]))
        return
    scn.calendars = staged
    stale = common.invalidate_outputs()
    common.revalidate()
    noun = "change" if len(staged) == 1 else "changes"
    st.session_state["planned_contingencies_flash"] = (
        f"Applied {len(staged)} timed resource {noun}"
        + (" — previous plan cleared" if stale else ""))
    st.rerun()


# ================================================= 6. Planning Workspace
def page_planning():
    st.subheader("Planning settings")
    scn = common.sc()
    st.info(planning_resolution_summary(scn.sim))

    with st.form("planning_settings_form", border=True):
        st.markdown("#### Resolution and control windows")
        resolution = st.columns(7)
        mixed_resolution_enabled = resolution[0].toggle(
            "Mixed resolution",
            value=bool(getattr(
                scn.sim, "mixed_resolution_enabled", False)),
            help=(
                "Use short near-term decisions and hourly tactical decisions "
                "inside one planning horizon."
            ),
        )
        horizon_minutes = int(resolution[1].number_input(
            "Total horizon [min]",
            value=effective_horizon_minutes(scn.sim),
            min_value=60,
            step=60,
            help="The current value is also shown in hours above.",
        ))
        near_term_step_min = int(resolution[2].number_input(
            "Near step [min]",
            value=int(getattr(scn.sim, "near_term_step_min", 15)),
            min_value=1,
            step=5,
            help="Decision step inside the near-term window; normally 15 min.",
        ))
        near_term_horizon_min = int(resolution[3].number_input(
            "Near horizon [min]",
            value=int(getattr(scn.sim, "near_term_horizon_min", 240)),
            min_value=1,
            step=15,
            help="Length of the detailed near-term window.",
        ))
        far_term_step_min = int(resolution[4].number_input(
            "Far step [min]",
            value=int(getattr(scn.sim, "far_term_step_min", 60)),
            min_value=1,
            step=15,
            help=(
                "Decision step after the near-term window; normally 60 min "
                "and required to match the native tactical grid in this "
                "release."
            ),
        ))
        replan_interval_min = int(resolution[5].number_input(
            "Replan cadence [min]",
            value=int(getattr(scn.sim, "replan_interval_min", 15)),
            min_value=1,
            step=5,
        ))
        commit_window_min = int(resolution[6].number_input(
            "Commit window [min]",
            value=int(getattr(scn.sim, "commit_window_min", 30)),
            min_value=0,
            step=5,
            help="Jobs inside this near-term window survive a replan unchanged.",
        ))

        st.markdown("#### Solver controls")
        c = st.columns(4)
        max_solve_s = c[0].number_input(
            "Max solve budget", value=float(scn.sim.max_solve_s), min_value=1.0,
            help="CP-SAT deterministic-time budget (≈s; wall may be 2–5×)")
        seed = int(c[1].number_input("Seed", value=scn.sim.seed))
        deviation_tolerance = c[2].number_input(
            "Deviation tolerance", value=float(scn.sim.deviation_tolerance),
            min_value=0.0, max_value=1.0, step=0.05)
        c[3].selectbox(
            "Solver", ["cp-sat (OR-Tools)"], disabled=True,
            help="Single deterministic backend in this MVP")

        apply_settings = st.form_submit_button("Apply planning settings")

    if apply_settings:
        staged_sim, errors = stage_planning_settings(
            scn.sim,
            mixed_resolution_enabled=mixed_resolution_enabled,
            horizon_minutes=horizon_minutes,
            near_term_step_min=near_term_step_min,
            near_term_horizon_min=near_term_horizon_min,
            far_term_step_min=far_term_step_min,
            replan_interval_min=replan_interval_min,
            commit_window_min=commit_window_min,
            max_solve_s=max_solve_s,
            seed=seed,
            deviation_tolerance=deviation_tolerance,
        )
        if errors:
            st.error("Nothing was applied. " + " · ".join(errors[:8]))
        else:
            scn.sim = staged_sim
            stale = common.invalidate_outputs()
            common.revalidate()
            st.success(
                "Mixed-resolution planning settings applied"
                + (" — previous plan cleared" if stale else ""))

    render_planner_priorities(scn, key_prefix="advanced")

    # ---- planned contingencies & operator-fixed jobs --------------------------
    left, right = st.columns(2)
    with left:
        st.markdown("**Scheduled resource events**")
        st.caption(
            "Known machine, line, route or site outages and rate caps are "
            "configured by selecting resources in Routing availability."
        )
        st.metric("Timed resource changes", len(scn.calendars))
        if st.button(
                "Open routing availability",
                key="advanced_open_planned_contingencies"):
            common.goto_page(common.PLAN_ROUTING_PAGE)
            st.rerun()
    with right:
        st.markdown("**Operator-fixed jobs** (must be honoured; H3 governance)")
        st.caption("Each pile has 1,000 equal longitudinal intervals "
                   "(boundaries R0–R1000). Example: stack TBT on LT-P1 from "
                   "R400 to R600 — 200 sections, or 20% of the pile length. "
                   "Metre X is derived automatically for safety and SMS "
                   "validation.")
        fdf = pd.DataFrame([{"id": f.id, "machine": f.machine, "kind": f.kind,
                             "space": f.space, "dest": f.dest_id,
                             "grade": f.grade,
                             "R from": f.start_repere,
                             "R to": f.stop_repere,
                             "rate_tph": f.rate_tph, "start": f.start_bucket,
                             "end": f.end_bucket} for f in scn.fixed_jobs])
        if fdf.empty:
            fdf = pd.DataFrame(columns=["id", "machine", "kind", "space", "dest",
                                        "grade", "R from", "R to", "rate_tph",
                                        "start", "end"])
        fed = st.data_editor(
            fdf, num_rows="dynamic", key="fj_ed", height=190,
            column_config={
                "R from": st.column_config.NumberColumn(
                    "R from", min_value=0, max_value=999, step=1,
                    help="Start boundary on the pile's R0–R1000 frame"),
                "R to": st.column_config.NumberColumn(
                    "R to", min_value=1, max_value=1000, step=1,
                    help="Stop boundary; must be greater than R from"),
            })
        if st.button("Apply fixed jobs"):
            def _txt(value) -> str:
                return "" if pd.isna(value) else str(value).strip()

            def _optional_int(value):
                return None if pd.isna(value) or str(value).strip() == "" \
                    else int(value)

            def _number(value, default=0.0):
                return default if pd.isna(value) or str(value).strip() == "" \
                    else float(value)

            scn.fixed_jobs = [FixedJob(
                id=_txt(r["id"]) or f"FX{i}", machine=_txt(r["machine"]),
                kind=_txt(r["kind"]) or "reclaim", space=_txt(r["space"]),
                dest_id=_txt(r["dest"]), rate_tph=_number(r["rate_tph"]),
                start_bucket=int(_number(r["start"])),
                end_bucket=int(_number(r["end"])),
                grade=_txt(r["grade"]),
                start_repere=_optional_int(r["R from"]),
                stop_repere=_optional_int(r["R to"]))
                for i, (_, r) in enumerate(fed.iterrows())
                if _txt(r["machine"])]
            stale = common.invalidate_outputs()
            common.revalidate()
            st.success("applied — these appear as HUMAN jobs; their grade "
                       "and repère range are preserved through replans"
                       + (" · previous plan cleared" if stale else ""))

    # ---- actions ----------------------------------------------------------------
    st.divider()
    render_plan_run_panel()

    p = common.plan()
    if p is None:
        return

    # ---- proposed plan views ------------------------------------------------------
    if p.reasons:
        with st.container(border=True):
            st.markdown("**Reason codes / gaps**")
            for r in p.reasons:
                st.markdown(f"- `{theme.operator_text(r)}`")
            if p.relaxations:
                st.markdown("**Suggested relaxations:** "
                            + " · ".join(
                                theme.operator_text(r)
                                for r in dict.fromkeys(p.relaxations)))

    # ---- machine planning coverage (compact diagnostic) ----------------------
    with st.expander("🧭 Machine planning coverage — jobs vs data-blocked",
                     expanded=False):
        st.caption(
            "Each machine is classified as jobs generated, legitimate idle "
            "or a modelling gap, with configured reach, route, inventory "
            "context, trigger and a concise reason. Reference assets remain "
            "ineligible for solver jobs.")
        cov = theme.machine_planning_coverage(scn, p)
        show_df(cov, height=min(60 + 36 * len(cov), 480))

    df = jobs_to_gantt_df(
        scn, p.jobs, coalesce_execution_windows=True)
    machine_register = theme.timeline_machine_register(scn, p)
    tabs = st.tabs(["Machine Gantt", "Pile Gantt", "Destination Gantt",
                    "Material Gantt", "Job table", "Route allocation"])
    with tabs[0]:
        st.caption(
            "Current solver-controlled equipment is shown by default. Empty "
            "rows carry the planner's concise no-job reason.")
        plot(gantt_figure(
            df, color_by="kind", group_by="machine",
            height=theme.timeline_height(machine_register, minimum=400),
            row_register=machine_register, show_tonnage=True))
    with tabs[1]:
        plot(gantt_figure(df, color_by="grade", group_by="space", height=420))
    with tabs[2]:
        d2 = df[df.destination != "—"]
        plot(gantt_figure(d2, color_by="space", group_by="destination", height=320))
    with tabs[3]:
        plot(gantt_figure(df, color_by="machine", group_by="grade", height=320))
    with tabs[4]:
        st.caption("Tick **locked** to protect a job through replans; tick "
                   "**reject** to ban it and re-run planning.")
        jt = df[["job", "parent_job", "site", "machine_label", "machine",
                 "kind", "space", "repere_range", "destination", "grade",
                 "rate_tph", "execution_rate_cap_tph", "tonnage_t",
                 "execution_duration_min",
                 "timing_status", "start_bucket", "end_bucket", "status",
                 "origin"]].copy()
        jt = jt.rename(columns={"machine_label": "machine",
                                "machine": "machine_id"})
        locked_by_job = {j.id: j.locked for j in p.jobs}
        jt["locked"] = jt["parent_job"].map(locked_by_job).fillna(False)
        jt["reject"] = False
        displayed_jt = theme.operator_frame(jt)
        edited_display = st.data_editor(
            displayed_jt, key="jobs_ed", height=300,
            disabled=[c for c in displayed_jt.columns
                      if c not in ("locked", "reject")])
        ed = theme.restore_operator_frame(edited_display, jt)
        c1, c2 = st.columns([1, 3])
        if c1.button("Apply locks / rejections & re-plan"):
            from mph_sim.optimizer import SolveOptions, solve_plan
            banned = []
            for parent_id, window_rows in ed.groupby("parent_job"):
                j = next(j for j in p.jobs if j.id == parent_id)
                j.locked = bool(window_rows["locked"].any())
                if window_rows["reject"].any():
                    banned.append((j.machine, j.space, j.dest_id,
                                   j.start_bucket, j.end_bucket))
                    st.session_state.overrides_log.append(
                        {"action": "reject", "job": j.id, "machine": j.machine})
                elif j.locked:
                    st.session_state.overrides_log.append(
                        {"action": "lock", "job": j.id, "machine": j.machine})
            fixed = list(scn.fixed_jobs)
            for j in p.jobs:
                if j.locked:
                    r0, r1 = x_interval_to_reperes(
                        scn.space(j.space), j.start_x, j.stop_x)
                    fixed.append(FixedJob(
                        id=f"LOCK-{j.id}", machine=j.machine, kind=j.kind,
                        space=j.space, dest_id=j.dest_id, rate_tph=j.rate_tph,
                        start_bucket=j.start_bucket, end_bucket=j.end_bucket,
                        grade=j.grade, start_repere=r0, stop_repere=r1,
                        start_minute=getattr(j, "start_minute", None),
                        end_minute=getattr(j, "end_minute", None)))
            snap = copy.deepcopy(scn)
            snap.fixed_jobs = fixed
            with st.spinner("re-planning with operator constraints…"):
                res = solve_plan(snap, SolveOptions(banned=banned))
            if res.status in ("OPTIMAL", "FEASIBLE"):
                st.session_state.plan = res
                st.session_state.sim = None
                st.success(f"re-planned: {len(res.jobs)} jobs ({res.status})")
                st.rerun()
            else:
                st.error(
                    f"re-plan failed: {res.status} — "
                    + "; ".join(theme.operator_text(r) for r in res.reasons))
    with tabs[5]:
        rows = []
        for j in p.jobs:
            rows.append({"job": j.id, "kind": j.kind, "path": " → ".join(j.path)
                         or "—", "rate_tph": j.rate_tph,
                         "buckets": f"{j.start_bucket}–{j.end_bucket}"})
        show_df(pd.DataFrame(rows), height=280)
        d0 = scn.destinations[0].id if scn.destinations else None
        if d0:
            b = st.slider("Blend fractions at bucket", 0,
                          scn.sim.horizon_buckets - 1, 2)
            dsel = st.selectbox("Destination", [d.id for d in scn.destinations])
            f = blend_fractions(p, dsel, b)
            if f:
                show_df(pd.DataFrame([{"space": k, "fraction f = x/delivered":
                                       round(v, 3)} for k, v in f.items()]))
            else:
                st.caption("no reclaim flows to this destination at that bucket")


# ================================================= 16. Machine Coverage
def _machine_usage_state(row: pd.Series) -> str:
    """Small operator-facing state used by the coverage-page filter."""
    raw_conflicts = row.get("conflict_count", 0)
    conflict_count = (
        0 if raw_conflicts is None or pd.isna(raw_conflicts)
        else int(raw_conflicts)
    )
    if bool(row.get("schedule_conflict", False)) or \
            conflict_count > 0:
        return "Conflict"
    if not bool(row.get("usage_applicable", False)):
        return "Usage unavailable"
    if float(row.get("occupied_h", 0.0) or 0.0) > 1e-9:
        return "Scheduled"
    return "Available, not used"


def _machine_usage_table(frame: pd.DataFrame) -> pd.DataFrame:
    """Return the compact operational view; detailed route evidence is below."""
    columns = [
        "machine", "site", "kind", "status", "jobs", "usage_pct", "work_h",
        "positioning_h", "idle_h", "tonnes_t", "timing_basis", "reason",
    ]
    if frame is None or frame.empty:
        return pd.DataFrame(columns=[
            "machine", "site", "type", "status", "jobs", "usage [%]",
            "work [h]", "positioning [h]", "idle [h]", "tonnes [t]",
            "timing", "reason",
        ])
    table = frame[columns].copy()
    table["kind"] = table["kind"].map(
        lambda value: str(value).replace("_", " ").title())

    def _display_number(value, decimals=1, thousands=False):
        if value is None or pd.isna(value):
            return "—"
        number = float(value)
        return (
            f"{number:,.{decimals}f}" if thousands
            else f"{number:.{decimals}f}"
        )

    table["jobs"] = table["jobs"].map(
        lambda value: "—" if value is None or pd.isna(value)
        else f"{int(value)}")
    for column in (
            "usage_pct", "work_h", "positioning_h", "idle_h"):
        table[column] = table[column].map(_display_number)
    table["tonnes_t"] = table["tonnes_t"].map(
        lambda value: _display_number(value, decimals=0, thousands=True))
    return table.rename(columns={
        "kind": "type",
        "usage_pct": "usage [%]",
        "work_h": "work [h]",
        "positioning_h": "positioning [h]",
        "idle_h": "idle [h]",
        "tonnes_t": "tonnes [t]",
        "timing_basis": "timing",
    })


def page_machine_coverage():
    st.subheader("Machine Coverage & Usage")
    scn, p = common.sc(), common.plan()
    plan_status = str(getattr(p, "status", "") or "").upper()
    valid_plan = p is not None and plan_status in {"OPTIMAL", "FEASIBLE"}
    horizon_h = effective_horizon_minutes(scn.sim) / 60.0
    usage = theme.machine_usage_summary(scn, p)
    usage["_usage_state"] = usage.apply(_machine_usage_state, axis=1)

    timing = sorted({
        str(value) for value in usage.loc[
            usage["timing_basis"].ne("—"), "timing_basis"]
        if str(value).strip()
    })
    timing_label = " + ".join(timing) if timing else "no scheduled work"
    if valid_plan:
        basis = (
            f"Plan {plan_status} · {horizon_h:g} h horizon · "
            f"{timing_label}"
        )
    elif p is None:
        basis = f"No plan · {horizon_h:g} h configured horizon"
    else:
        basis = (
            f"Plan {plan_status or 'UNKNOWN'} · {horizon_h:g} h horizon · "
            "usage unavailable"
        )
    st.caption(
        f"{basis}. This page reports planned scheduled use, not live "
        "equipment telemetry.")

    machine_ids = {machine.id for machine in scn.machines}
    filter_cols = st.columns([1.05, 1.05, 1.25, 1.2])
    site_options = ["All sites"] + sorted(usage["site"].dropna().unique())
    site = filter_cols[0].selectbox(
        "Site", site_options, key="machine_coverage_site")
    kind_options = ["All types"] + sorted(usage["kind"].dropna().unique())
    kind = filter_cols[1].selectbox(
        "Machine type", kind_options, key="machine_coverage_kind",
        format_func=lambda value: (
            value if str(value).startswith("All")
            else str(value).replace("_", " ").title()))
    state_options = [
        "All states", "Scheduled", "Available, not used",
        "Conflict", "Usage unavailable",
    ]
    state = filter_cols[2].selectbox(
        "Usage state", state_options, key="machine_coverage_state")
    include_reference = filter_cols[3].toggle(
        "Include reference assets", value=False,
        key="machine_coverage_reference",
        help="Shows drawing/reference equipment that is not an executable "
             "planning resource.")

    filtered = usage.copy()
    if not include_reference:
        filtered = filtered[filtered["machine_id"].isin(machine_ids)]
    if site != "All sites":
        filtered = filtered[filtered["site"] == site]
    if kind != "All types":
        filtered = filtered[filtered["kind"] == kind]
    if state != "All states":
        filtered = filtered[filtered["_usage_state"] == state]
    filtered = filtered.reset_index(drop=True)

    eligible = filtered[filtered["usage_applicable"].fillna(False)]
    used = eligible[
        pd.to_numeric(eligible["occupied_h"], errors="coerce").fillna(0.0)
        > 1e-9
    ]
    available_h = float(pd.to_numeric(
        eligible["available_h"], errors="coerce").fillna(0.0).sum())
    occupied_h = float(pd.to_numeric(
        eligible["occupied_h"], errors="coerce").fillna(0.0).sum())
    work_h = float(pd.to_numeric(
        eligible["work_h"], errors="coerce").fillna(0.0).sum())
    positioning_h = float(pd.to_numeric(
        eligible["positioning_h"], errors="coerce").fillna(0.0).sum())
    tonnes_t = float(pd.to_numeric(
        eligible["tonnes_t"], errors="coerce").fillna(0.0).sum())
    conflicts = int(pd.to_numeric(
        eligible["conflict_count"], errors="coerce").fillna(0).sum())
    fleet_usage = (
        100.0 * occupied_h / available_h if available_h > 0 else None)

    metrics = st.columns(6)
    metrics[0].metric(
        "Machines used",
        f"{len(used)} / {len(eligible)}" if valid_plan
        else f"— / {int(filtered['solver_controlled'].fillna(False).sum())}")
    metrics[1].metric(
        "Fleet scheduled use",
        f"{fleet_usage:.1f}%" if valid_plan and fleet_usage is not None else "—")
    metrics[2].metric(
        "Material work", f"{work_h:.1f} h" if valid_plan else "—")
    metrics[3].metric(
        "Reserved positioning",
        f"{positioning_h:.1f} h" if valid_plan else "—")
    metrics[4].metric(
        "Machine-handled tonnes",
        f"{tonnes_t:,.0f} t" if valid_plan else "—")
    metrics[5].metric(
        "Conflicts", f"{conflicts}" if valid_plan else "—")

    if not valid_plan:
        st.info(
            "Coverage is still shown below; usage remains unavailable until "
            "a valid plan is generated.")
    else:
        if conflicts:
            st.warning(
                f"{conflicts} positioning conflict(s) require replanning. "
                "They are alerts and are not counted as machine use.")
        schedule_conflicts = int(
            filtered["schedule_conflict"].fillna(False).sum())
        if schedule_conflicts:
            st.error(
                f"{schedule_conflicts} machine(s) exceed their available "
                "calendar time; review the plan timing.")

        st.markdown("#### Planned scheduled use")
        if filtered.empty:
            st.caption("∅ no machines match the current filters")
        else:
            plot(theme.machine_usage_figure(filtered))
        st.caption(
            "Use = material work + reserved inter-job positioning. Idle is "
            "the remaining unblocked machine time. The first job has no "
            "in-horizon positioning reservation.")

    st.markdown("#### Machine status")
    st.caption(
        "Machine-handled tonnes are input tonnes at each machine, so the "
        "same material can be counted again at a later handling stage.")
    compact = _machine_usage_table(filtered)
    show_df(
        compact,
        height=min(96 + 35 * max(len(compact), 1), 520),
        empty_message="no machines match the current filters")

    with st.expander("Why idle / model coverage details", expanded=False):
        st.caption(
            "Route, reach, inventory and trigger details for investigating an "
            "idle, unavailable or unmodelled row.")
        coverage = theme.machine_planning_coverage(scn, p)
        visible_labels = set(filtered["machine"])
        coverage = coverage[coverage["machine"].isin(visible_labels)]
        coverage = coverage[[
            "machine", "site", "kind", "category", "reason_code",
            "explanation", "solver_controlled", "reachable_spaces", "route",
            "inventory_t", "free_capacity_t", "job_trigger",
        ]].rename(columns={"explanation": "reason"})
        show_df(
            coverage,
            height=min(96 + 35 * max(len(coverage), 1), 520),
            empty_message="no coverage details match the current filters")


# ================================================= 7. Simulation Control
EVENT_HELP = {
    "machine_failure": "target=machine id · duration",
    "conveyor_trip": "target=link id · duration",
    "route_unavailable": "target=link id · duration",
    "demand_change": "target=destination · magnitude=new t/h · duration",
    "delayed_mine_dispatch": "target=corridor link · magnitude=+buckets",
    "offspec_assay": "target=space · params JSON {\"BPL\": 66}",
    "inventory_discrepancy": "target=space · magnitude=±t",
    "operator_reject": "target=job id",
    "reduced_rate": "target=machine · magnitude=factor 0–1 · duration",
    "downstream_interruption": "target=destination · duration",
    "comms_loss": "duration (feedback suppressed, deviations undetected)",
}


def sim_event_supports_minutes() -> bool:
    fields = getattr(SimEvent, "__dataclass_fields__", {})
    return "minute" in fields and "duration_minutes" in fields


def build_ui_sim_event(
        *, event_id: str, event_type: str, at_value: int,
        target: str = "", magnitude: float = 0.0, duration_value: int = 1,
        params: dict | None = None, sim_params=None) -> SimEvent:
    """Create a minute-native event while retaining legacy bucket fields."""
    bucket_width_min = max(int(round(
        float(getattr(sim_params, "dt_h", 1.0)) * 60.0)), 1)
    if sim_event_supports_minutes():
        minute = max(int(at_value), 0)
        duration_minutes = max(int(duration_value), 1)
        bucket = minute // bucket_width_min
        duration_buckets = max(
            int(math.ceil(duration_minutes / bucket_width_min)), 1)
        return SimEvent(
            id=event_id,
            etype=event_type,
            bucket=bucket,
            target=target,
            magnitude=float(magnitude),
            duration_buckets=duration_buckets,
            params=dict(params or {}),
            minute=minute,
            duration_minutes=duration_minutes,
        )
    return SimEvent(
        id=event_id,
        etype=event_type,
        bucket=max(int(at_value), 0),
        target=target,
        magnitude=float(magnitude),
        duration_buckets=max(int(duration_value), 1),
        params=dict(params or {}),
    )


MIDWEEK_TEST_EVENT_ID = "UI-MIDWEEK-MACHINE-FAILURE"


def _job_minute_interval(job, sim_params) -> tuple[int, int]:
    step = max(int(round(float(getattr(sim_params, "dt_h", 1.0)) * 60)), 1)
    start = getattr(job, "start_minute", None)
    end = getattr(job, "end_minute", None)
    start_min = (int(start) if start is not None
                 else int(getattr(job, "start_bucket", 0)) * step)
    end_min = (int(end) if end is not None
               else (int(getattr(job, "end_bucket", 0)) + 1) * step)
    return start_min, end_min


def midweek_test_target(scn, plan, minute: int) -> str:
    """Choose a machine with work at, or soon after, the weekly midpoint."""
    ranked = []
    for job in list(getattr(plan, "jobs", []) or []):
        machine = str(getattr(job, "machine", "") or "")
        if not machine:
            continue
        start_min, end_min = _job_minute_interval(job, scn.sim)
        if start_min <= minute < end_min:
            rank = (0, start_min, machine)
        elif start_min >= minute:
            rank = (1, start_min, machine)
        else:
            continue
        ranked.append((rank, machine))
    if ranked:
        return min(ranked)[1]
    available = [m.id for m in scn.machines if bool(getattr(m, "available", True))]
    return available[0] if available else (scn.machines[0].id if scn.machines else "")


def latest_replan_latency(s, index: int = -1) -> dict[str, object]:
    """Return event-detection/replan timing when simulator log detail exists."""
    replans = [record for record in getattr(s, "log", [])
               if getattr(record, "kind", "") == "REPLAN"]
    if not replans:
        return {"detected_minute": None, "completed_minute": None,
                "latency_min": None, "solve_s": None}
    resolved_index = index if index >= 0 else len(replans) - 1
    resolved_index = min(resolved_index, len(replans) - 1)
    completed = replans[resolved_index]
    completed_minute = int(getattr(completed, "detail", {}).get(
        "minute", getattr(completed, "bucket", 0) *
        int(getattr(s, "bucket_width_min", 60))))
    prior_completed_minute = -1
    if resolved_index > 0:
        prior = replans[resolved_index - 1]
        prior_completed_minute = int(getattr(prior, "detail", {}).get(
            "minute", getattr(prior, "bucket", 0) *
            int(getattr(s, "bucket_width_min", 60))))
    triggers = []
    for record in getattr(s, "log", []):
        if getattr(record, "kind", "") not in ("EVENT", "DEVIATION"):
            continue
        detected = int(getattr(record, "detail", {}).get(
            "minute", getattr(record, "bucket", 0) *
            int(getattr(s, "bucket_width_min", 60))))
        if prior_completed_minute < detected <= completed_minute:
            triggers.append(detected)
    detected_minute = max(triggers) if triggers else None
    solve_times = list(getattr(s, "replan_solve_s", []) or [])
    return {
        "detected_minute": detected_minute,
        "completed_minute": completed_minute,
        "latency_min": (completed_minute - detected_minute
                        if detected_minute is not None else None),
        "solve_s": (solve_times[resolved_index]
                    if resolved_index < len(solve_times) else None),
    }


def unexpected_event_status(s, event_id: str) -> str:
    event = next((event for event in getattr(s, "events", [])
                  if getattr(event, "id", "") == event_id), None)
    if event is None:
        return "Not queued"
    detected = [record for record in getattr(s, "log", [])
                if getattr(record, "kind", "") == "EVENT"
                and getattr(record, "detail", {}).get("event_id") == event_id]
    if not detected:
        return "Queued · awaiting event time"
    detected_minute = int(detected[-1].detail.get("minute", 0))
    completed = [record for record in getattr(s, "log", [])
                 if getattr(record, "kind", "") == "REPLAN"
                 and int(getattr(record, "detail", {}).get(
                     "minute", -1)) >= detected_minute]
    if completed:
        return "Detected · replan completed"
    if bool(getattr(s, "auto_replan", False)):
        return "Detected · replan pending"
    return "Detected · auto-replan off"


def render_simulation_replan_status(scn, s) -> None:
    """Compact execution/replanning state shared by simulation and results."""
    now = max(int(getattr(s, "current_minute", 0)), 0)
    latency = latest_replan_latency(s)
    pending = str(getattr(s, "pending_replan_reason", "") or "")
    cols = st.columns(6)
    cols[0].metric("Execution clock", planning_clock_label(scn, now))
    cols[1].metric(
        "Automatic replanning",
        "Enabled" if bool(getattr(s, "auto_replan", False)) else "Disabled")
    cols[2].metric("Replans completed", int(getattr(s, "replans", 0)))
    cols[3].metric("Pending trigger", pending or "None")
    cols[4].metric(
        "Latest replan solve",
        (f"{float(latency['solve_s']):.2f} s"
         if latency["solve_s"] is not None else "—"))
    cols[5].metric(
        "Detected → completed",
        (f"{int(latency['latency_min'])} model min"
         if latency["latency_min"] is not None else "—"),
        help=("Event-time latency from simulator log timestamps. Solver wall "
              "time is reported separately."))


def page_simulation():
    st.subheader("Simulation Control")
    scn, p = common.sc(), common.plan()
    render_planning_operating_context(scn)
    if p is None:
        st.info(
            "No executable plan exists yet. Generate S0 with the sidebar "
            "**Generate Plan** action first; the bounded 7-day solve reports "
            "live stages and elapsed time.")
        return
    st.caption("Closed loop per report §15.8: plan → validate → dispatch → "
               "execute → feedback (JobUpdated) → detect deviation → replan.")

    auto = st.toggle("Auto-replan on deviation/event", value=True,
                     help="off = deviations are logged but the plan is kept")
    s = common.ensure_sim(auto_replan=auto)
    s.auto_replan = auto
    render_simulation_replan_status(scn, s)

    st.info(
        f"**Planned controls:** {len(scn.calendars)} outage/derate window(s) "
        "are already constraints in the generated plan. **Unexpected events** "
        "below exist only in simulation, are detected at execution time and "
        "can trigger reactive replanning.")

    c = st.columns(7)
    mixed = bool(getattr(scn.sim, "mixed_resolution_enabled", False))
    near_step_min = max(int(getattr(
        scn.sim, "near_term_step_min", 15) or 15), 1)
    if c[0].button(
            f"▶ Step {near_step_min} min" if mixed else "▶ Step 1 h"):
        s.advance_minutes(near_step_min) if mixed else s.step()
    if c[1].button("▶▶ Step 1 h" if mixed else "▶▶ Step 4 h"):
        if mixed:
            s.advance_minutes(60)
        else:
            for _ in range(4):
                if not s.step():
                    break
    if c[2].button("⏵ Run to end"):
        with st.spinner("simulating…"):
            s.run_to_end()
    speed_options = ([f"{near_step_min} min/click", "60 min/click",
                      "run-to-end"] if mixed else
                     ["1 bucket/click", "4 buckets/click", "run-to-end"])
    speed = c[3].selectbox("Speed", speed_options, index=0,
                           help="MVP advances on click; no wall-clock timer")
    if c[4].button("↺ Reset"):
        st.session_state.sim = None
        st.rerun()
    if c[5].button("⟲ Replay (deterministic)"):
        st.session_state.sim = None
        s = common.ensure_sim(auto_replan=auto)
        with st.spinner("replaying stored scenario + events…"):
            s.run_to_end()
        st.success("replayed to end — identical inputs reproduce the identical "
                   "run (stored seed, deterministic solver)")
    if mixed:
        elapsed_h, elapsed_m = divmod(int(s.current_minute), 60)
        total_min = int(s.T * s.bucket_width_min)
        total_h, total_m = divmod(total_min, 60)
        c[6].metric(
            "Simulation time",
            f"{elapsed_h:02d}:{elapsed_m:02d} / {total_h:02d}:{total_m:02d}")
    else:
        c[6].metric("Execution period", f"{s.bucket}/{s.T} hourly periods")

    # ---- event injection -------------------------------------------------------
    with st.container(border=True):
        st.markdown("#### Unexpected event testing")
        st.caption(
            "Simulation-only events do not alter the planned-contingency "
            "calendar. Queue them here to test detection, interruption, "
            "replanning and recovery against the approved weekly plan.")

        template_minute = effective_horizon_minutes(scn.sim) // 2
        template_target = midweek_test_target(scn, p, template_minute)
        template_status = unexpected_event_status(s, MIDWEEK_TEST_EVENT_ID)
        template = st.columns([1.6, 1.2, 1.2, 1.3])
        template[0].metric("Mid-week test", "2 h machine failure")
        template[1].metric("Event time", planning_clock_label(
            scn, template_minute))
        template[2].metric(
            "Machine", (theme.machine_display_label(scn, template_target)
                        if template_target else "No machine available"))
        template[3].metric("Test status", template_status)
        already_queued = template_status != "Not queued"
        event_has_passed = int(getattr(s, "current_minute", 0)) > template_minute
        if template[0].button(
                "Queue mid-week failure test",
                key="queue_midweek_failure_test",
                disabled=(already_queued or event_has_passed or
                          not template_target),
                help=("Queues one unexpected two-hour machine failure at the "
                      "middle of the week; it does not edit planned controls.")):
            event = build_ui_sim_event(
                event_id=MIDWEEK_TEST_EVENT_ID,
                event_type="machine_failure",
                at_value=template_minute,
                target=template_target,
                duration_value=120,
                sim_params=scn.sim,
            )
            s.inject(event)
            if not any(getattr(item, "id", "") == event.id
                       for item in st.session_state.queued_events):
                st.session_state.queued_events.append(event)
            st.session_state["midweek_event_flash"] = (
                f"Unexpected mid-week failure queued for {template_target} at "
                f"{planning_clock_label(scn, template_minute)}.")
            st.rerun()
        if event_has_passed and not already_queued:
            template[0].caption(
                "Reset the simulation to queue the mid-week template.")
        flash = st.session_state.pop("midweek_event_flash", "")
        if flash:
            st.success(flash)

        st.markdown("**Custom unexpected event**")
        c = st.columns(6)
        et = c[0].selectbox("Type", list(EVENT_TYPES))
        st.caption(f"→ {EVENT_HELP.get(et, '')}")
        targets = {"machine_failure": [m.id for m in scn.machines],
                   "reduced_rate": [m.id for m in scn.machines],
                   "conveyor_trip": [l.id for l in scn.links],
                   "route_unavailable": [l.id for l in scn.links],
                   "delayed_mine_dispatch": [l.id for l in scn.links
                                             if l.transit_buckets > 0],
                   "demand_change": [d.id for d in scn.destinations],
                   "downstream_interruption": [d.id for d in scn.destinations],
                   "offspec_assay": [sp.id for sp in scn.spaces],
                   "inventory_discrepancy": [sp.id for sp in scn.spaces],
                   "operator_reject": [j.id for j in s.jobs.values()
                                       if j.status in ("PLANNED", "RUNNING")],
                   "comms_loss": ["—"]}.get(et, ["—"])
        machine_target = et in ("machine_failure", "reduced_rate")
        tgt = c[1].selectbox(
            "Target", targets,
            format_func=(lambda mid: theme.machine_display_label(scn, mid))
            if machine_target else str)
        minute_native = sim_event_supports_minutes()
        if minute_native:
            event_max = max(effective_horizon_minutes(scn.sim) - 1, 0)
            at_value = int(c[2].number_input(
                "At minute",
                value=min(max(int(getattr(s, "current_minute", 0)), 0),
                          event_max),
                min_value=0,
                max_value=event_max,
                step=1,
                help="Whole minutes from the planning-horizon start.",
            ))
        else:
            at_value = int(c[2].number_input(
                "At bucket", value=max(s.bucket, 0),
                min_value=0, max_value=s.T - 1))
        mag = c[3].number_input("Magnitude", value=0.5 if et == "reduced_rate"
                                else 0.0)
        duration_value = int(c[4].number_input(
            "Duration [min]" if minute_native else "Duration [buckets]",
            value=30 if minute_native else 2,
            min_value=1,
            step=5 if minute_native else 1,
        ))
        pjson = c[5].text_input("Params JSON", value='{"BPL": 66.0}'
                                if et == "offspec_assay" else "")
        if minute_native:
            event_end = min(at_value + duration_value,
                            effective_horizon_minutes(scn.sim))
            st.caption(
                f"Event window: {planning_clock_label(scn, at_value)} → "
                f"{planning_clock_label(scn, event_end)} (exclusive).")
        if st.button("Queue unexpected event"):
            import json as _json
            params = {}
            if pjson.strip():
                try:
                    params = _json.loads(pjson)
                except Exception:
                    st.error("bad params JSON")
            ev = build_ui_sim_event(
                event_id=f"UI{len(s.events) + 1}",
                event_type=et,
                at_value=at_value,
                target="" if tgt == "—" else tgt,
                magnitude=mag,
                duration_value=duration_value,
                params=params,
                sim_params=scn.sim,
            )
            s.inject(ev)
            st.session_state.queued_events.append(ev)
            if minute_native:
                st.success(
                    f"Unexpected {et} queued at "
                    f"{planning_clock_label(scn, at_value)} for "
                    f"{duration_value} min.")
            else:
                st.success(f"Unexpected {et} queued at bucket {at_value}.")

    # ---- live state ---------------------------------------------------------------
    left, right = st.columns(2)
    with left:
        x = list(range(len(next(iter(s.inv_series.values())))))
        plot(series_fig(x, dict(s.inv_series), "Inventory (simulated)", "t",
                        "line", 300))
    with right:
        T = s.T
        dlv = {d: v for d, v in s.delivered.items()}
        plot(series_fig(list(range(T)), dlv, "Delivered (actual)", "t/h",
                        "line", 300))
    df = jobs_to_gantt_df(
        scn, list(s.jobs.values()), coalesce_execution_windows=True)
    if not df.empty:
        register = theme.timeline_machine_register(scn, p)
        plot(gantt_figure(
            df, color_by="status", group_by="machine",
            height=theme.timeline_height(register, minimum=330),
            title="Job execution state", row_register=register,
            show_tonnage=True))
    st.markdown("#### Simulation log (last 25)")
    show_df(pd.DataFrame([{"bucket": r.bucket, "kind": r.kind, "asset": r.asset,
                           "message": r.message} for r in s.log[-25:]]),
            height=260)


# ================================================= 9. Job Schedule
_SCHEDULE_VISIBLE_COLUMNS = [
    "job", "parent_job", "machine", "machine_id", "process_stage", "kind",
    "activity_for_job", "source", "target", "from_position", "to_position",
    "translation_distance_m", "translation_duration_min",
    "setup_duration_min", "positioning_duration_min", "available_gap_min",
    "gap_status",
    "grade", "output_grade", "material_class", "mass_basis",
    "tonnage_t", "tsm_equivalent_t", "tsm_label",
    "output_tonnage_t", "rate_tph",
    "start_time", "end_time", "execution_duration_min", "depends_on",
    "dependency_delay_min", "path", "purpose", "repere_range",
    "stacking_method", "blend_mode", "blend_recipe", "chevron_grade_passes",
    "chevron_passes_per_cycle",
    "positioning_basis", "timing_status", "status",
]


def schedule_display_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Concise operator table while the download retains the full payload."""
    if df is None:
        return pd.DataFrame(columns=_SCHEDULE_VISIBLE_COLUMNS)
    out = df.copy()
    if "machine" in out.columns:
        out["machine_id"] = out["machine"]
    if "machine_label" in out.columns:
        out["machine"] = out["machine_label"]
    return out.reindex(columns=_SCHEDULE_VISIBLE_COLUMNS)


def schedule_day_windows(scn) -> list[tuple[str, pd.Timestamp, pd.Timestamp]]:
    """Calendar-day windows intersecting the configured planning horizon."""
    horizon_start = pd.Timestamp(scn.sim.start_iso)
    horizon_end = horizon_start + pd.Timedelta(
        minutes=int(scn.sim.effective_horizon_minutes()))
    windows: list[tuple[str, pd.Timestamp, pd.Timestamp]] = []
    cursor = horizon_start.normalize()
    day_number = 1
    while cursor < horizon_end:
        start = max(horizon_start, cursor)
        end = min(horizon_end, cursor + pd.Timedelta(days=1))
        if start < end:
            windows.append((
                f"Day {day_number} · {cursor.strftime('%a %d %b %Y')}",
                start,
                end,
            ))
            day_number += 1
        cursor += pd.Timedelta(days=1)
    return windows


def clip_schedule_to_window(
        frame: pd.DataFrame,
        window_start: pd.Timestamp,
        window_end: pd.Timestamp) -> pd.DataFrame:
    """Clip timeline rows to one day and prorate displayed interval masses."""
    if frame is None or frame.empty:
        return frame.copy() if frame is not None else pd.DataFrame()

    starts = pd.to_datetime(frame["start"])
    ends = pd.to_datetime(frame["end"])
    keep = (starts < window_end) & (ends > window_start)
    out = frame.loc[keep].copy()
    if out.empty:
        return out

    original_starts = pd.to_datetime(out["start"])
    original_ends = pd.to_datetime(out["end"])
    clipped_starts = original_starts.where(
        original_starts >= window_start, window_start)
    clipped_ends = original_ends.where(original_ends <= window_end, window_end)
    original_seconds = (
        original_ends - original_starts).dt.total_seconds().clip(lower=1.0)
    clipped_seconds = (
        clipped_ends - clipped_starts).dt.total_seconds().clip(lower=0.0)
    fractions = clipped_seconds / original_seconds

    for column in ("tonnage_t", "output_tonnage_t", "tsm_equivalent_t"):
        if column in out.columns:
            out[column] = pd.to_numeric(out[column], errors="coerce") * fractions

    out["start"] = clipped_starts
    out["end"] = clipped_ends
    out["start_time"] = clipped_starts.dt.strftime("%Y-%m-%d %H:%M")
    out["end_time"] = clipped_ends.dt.strftime("%Y-%m-%d %H:%M")
    out["execution_duration_min"] = (clipped_seconds / 60.0).round().astype(int)

    if "tonnage_label" in out.columns:
        material = ~out["kind"].isin(
            ["positioning", "positioning conflict"])
        out.loc[material, "tonnage_label"] = out.loc[
            material, "tonnage_t"].map(theme.fmt_t_bar)
        positioning = ~material
        out.loc[positioning, "tonnage_label"] = out.loc[positioning].apply(
            lambda row: (
                "!" if row["kind"] == "positioning conflict" else "↔"
            ) + f" {int(row['execution_duration_min'])} min",
            axis=1,
        )
    if "tsm_tonnage_label" in out.columns:
        out["tsm_tonnage_label"] = out.apply(
            lambda row: (
                f"{theme.fmt_t_bar(row['tsm_equivalent_t'])} {row['tsm_label']}"
                if pd.notna(row.get("tsm_equivalent_t"))
                and str(row.get("tsm_label", "")).strip()
                else ""
            ),
            axis=1,
        )
    return out


def page_schedule():
    st.subheader("Job Schedule")
    scn, p, s = common.sc(), common.plan(), common.sim()
    if p is None:
        st.info(
            "No job schedule yet — S0 has not been generated. Click "
            "**Generate Plan** in the left sidebar. The bounded weekly solve "
            "shows stages and elapsed status while it runs.")
        return
    jobs = list(s.jobs.values()) if s else p.jobs
    parcel_audit = micro_parcel_metrics(scn, jobs)
    below_floor = int(parcel_audit["below_parcel_floor_slices"])
    if scn.config.get("PARCEL_CONSOLIDATION_ENABLED", False) and s is None:
        before = int(p.breakdown.get("parcel_consolidation_before_buckets", 0))
        after = int(p.breakdown.get("parcel_consolidation_after_buckets", 0))
        st.caption(
            f"Buffered parcel consolidation: {before} → {after} short command "
            "buckets. Parcel preference is soft; service and material totals "
            "are protected. Necessary exceptions remain visible below."
        )
    if below_floor:
        st.warning(
            f"{below_floor} scheduled hourly allocation(s) are below the "
            "configured minimum parcel. These thin bars are real solver "
            "exceptions, not a chart artifact; review their machine, tonnes "
            "and campaign policy before using this schedule operationally."
        )
    stage_shortfall = float(p.breakdown.get(
        "process_stage_target_shortfall_t", 0.0) or 0.0)
    if stage_shortfall > 0:
        st.warning(
            f"Process-stage campaign targets are short by "
            f"{stage_shortfall:,.0f} t in total. The shortfall is explicit "
            "in the plan; no token machine jobs were inserted to hide it."
        )
    raw_df = jobs_to_gantt_df(
        scn, jobs, include_positioning=True,
        coalesce_execution_windows=False)
    df = jobs_to_gantt_df(
        scn, jobs, include_positioning=True,
        coalesce_execution_windows=True)
    blend_rows = df[
        df["stacking_method"].astype(str).str.lower().eq("chevron")
    ] if not df.empty else df
    if not blend_rows.empty:
        blend_job_count = blend_rows["parent_job"].nunique()
        blend_spaces = " · ".join(sorted(
            str(space_id) for space_id in blend_rows["space"].dropna().unique()
        ))
        st.success(
            f"Blending scheduled: {blend_job_count} component jobs at "
            f"{blend_spaces}. Purple bars are chevron blend construction."
        )
    else:
        st.warning(
            "No blending jobs are present in this generated plan. Load the "
            "Blending showcase demand preset (or enable a blend on the "
            "Blending page), then generate the plan again."
        )
    day_windows = schedule_day_windows(scn)
    full_horizon_label = f"Full run · {len(day_windows)} days"
    schedule_controls = st.columns([1.2, 2.0])
    show_reference = schedule_controls[0].toggle(
        "Show reference/future equipment", value=False,
        key="schedule_show_reference",
        help=("Adds documented display, disabled and future assets to the "
              "timeline; they remain ineligible for solver jobs."))
    selected_period = schedule_controls[1].selectbox(
        "Displayed period",
        [full_horizon_label, *[label for label, _start, _end in day_windows]],
        key="schedule_displayed_period",
        help=("Select one exact calendar day for a readable hourly view. "
              "This filters the display only; it does not change the plan."),
    )
    selected_window = next(
        ((start, end) for label, start, end in day_windows
         if label == selected_period),
        None,
    )
    machine_register = theme.timeline_machine_register(
        scn, p, include_reference=show_reference)
    near_step_min = int(getattr(scn.sim, "near_term_step_min", 15))
    far_step_min = int(getattr(scn.sim, "far_term_step_min", 60))
    if bool(getattr(scn.sim, "mixed_resolution_enabled", False)):
        slice_copy = (
            f"touching mixed-resolution solver slices are merged "
            f"({near_step_min}-minute near term, {far_step_min}-minute far "
            "term)"
        )
    else:
        hourly_step_min = max(int(round(float(scn.sim.dt_h) * 60.0)), 1)
        slice_copy = (
            f"touching {hourly_step_min}-minute hourly solver slices are "
            "merged"
        )
    st.caption(
        f"A production bar is one continuous job run: {slice_copy}, while a "
        "real gap, route "
        "change or positioning event "
        "starts a new run. Sub-hour work keeps its minute start/end. "
        "Block tonnages and t/h rates are physical/as-handled at the job's "
        "material stage; hover adds a stage-correct TSM value only when a "
        "grade conversion profile is available. Primary view: current "
        "solver-controlled equipment. Empty rows show "
        "a structured no-job reason; reference/future rows are optional. "
        "Blue is reclaim, orange is direct stack, purple is blending, and "
        "green is non-material positioning for the next job. Positioning "
        "time is estimated from "
        "configured travel/setup inputs; red flags a preparation gap that "
        "does not fit and requires replanning. Remaining blank time is "
        "idle/unallocated. The first scheduled job assumes the machine was "
        "positioned before this planning horizon; these bars are planned "
        "estimates, not live travel telemetry. Same-space face progression "
        "remains part of the production job.")
    loaded, missing = common.loaded_site_filters(scn)
    c = st.columns(5)
    sitef = c[0].multiselect("Site / domain", common.SITE_FILTERS,
                             help="loaded here: " + " · ".join(loaded)
                                  + (" — not loaded: " + " · ".join(missing)
                                     if missing else ""))
    machine_options = sorted(set(df.machine_label.unique()).union(
        machine_register.machine_label.unique()))
    mach = c[1].multiselect("Machine", machine_options)
    kind = c[2].multiselect("Operation", sorted(df.kind.unique()))
    stat = c[3].multiselect("Status", sorted(df.status.unique()))
    grp = c[4].selectbox(
        "Group by", ["machine", "process_stage", "source", "target",
                     "space", "destination", "grade", "site"])
    q = df
    rawq = raw_df
    regq = machine_register
    if selected_window is not None:
        window_start, window_end = selected_window
        q = clip_schedule_to_window(q, window_start, window_end)
        rawq = clip_schedule_to_window(rawq, window_start, window_end)
        regq.attrs["timeline_start"] = window_start.to_pydatetime()
    if sitef:
        if not q.empty:
            keep = q.apply(lambda r: any(common.match_site_filter(
                o, r["site"], r["area"]) for o in sitef), axis=1)
            q = q[keep]
        if not rawq.empty:
            raw_keep = rawq.apply(lambda r: any(common.match_site_filter(
                o, r["site"], r["area"]) for o in sitef), axis=1)
            rawq = rawq[raw_keep]
        regkeep = regq.apply(lambda r: any(common.match_site_filter(
            o, r["site"], r["area"]) for o in sitef), axis=1)
        regq = regq[regkeep]
    if mach:
        q = q[q.machine_label.isin(mach)]
        rawq = rawq[rawq.machine_label.isin(mach)]
        regq = regq[regq.machine_label.isin(mach)]
    if kind:
        q = q[q.kind.isin(kind)]
        rawq = rawq[rawq.kind.isin(kind)]
        regq = regq[regq.kind.isin(kind)]
    if stat:
        q = q[q.status.isin(stat)]
        rawq = rawq[rawq.status.isin(stat)]
    schedule_figure = gantt_figure(
        q, color_by="kind", group_by=grp,
        height=(theme.timeline_height(regq, minimum=430)
                if grp == "machine" else 430),
        row_register=regq if grp == "machine" else None,
        show_tonnage=True)
    if selected_window is not None:
        window_start, window_end = selected_window
        schedule_figure.update_xaxes(
            range=[window_start.to_pydatetime(), window_end.to_pydatetime()],
            tickformat="%H:%M<br>%d %b",
            dtick=3 * 60 * 60 * 1000,
        )
    plot(schedule_figure)
    show_df(schedule_display_frame(q), height=330)
    with st.expander(
            "Mixed-resolution solver allocation slices", expanded=False):
        st.caption(
            f"Diagnostic detail only: these rows retain the {near_step_min}-"
            f"minute near-term and {far_step_min}-minute far-term planning "
            "allocations behind each continuous job run.")
        show_df(schedule_display_frame(rawq), height=260)
    # The primary jobs export follows the continuous operator view.  The
    # activity export deliberately retains native bucket slices for audit and
    # downstream diagnostics.
    activity_export = rawq.copy()
    activity_export["machine_id"] = activity_export["machine"]
    activity_export["machine"] = activity_export["machine_label"]
    activity_export = activity_export.drop(columns=["machine_label"])
    material_export = q.copy()
    material_export["machine_id"] = material_export["machine"]
    material_export["machine"] = material_export["machine_label"]
    material_export = material_export.drop(columns=["machine_label"])
    material_export = material_export[
        ~material_export["kind"].isin(
            ["positioning", "positioning conflict"])
    ]
    downloads = st.columns(2)
    downloads[0].download_button(
        "⬇ material jobs CSV",
        material_export.to_csv(index=False).encode(),
        "jobs.csv")
    downloads[1].download_button(
        "⬇ machine activities CSV",
        activity_export.to_csv(index=False).encode(),
        "machine-activities.csv")


# ================================================= 10. Exceptions & Replanning
def replan_impact_summary(before, after) -> dict[str, object]:
    """Summarise retained work and recovery without relying on chart parsing."""
    excluded = {"CANCELLED", "REJECTED"}
    before_jobs = {
        job.id: job for job in before
        if str(getattr(job, "status", "")) not in excluded
    }
    after_jobs = {
        job.id: job for job in after
        if str(getattr(job, "status", "")) not in excluded
    }

    def signature(job):
        return (
            getattr(job, "machine", ""), getattr(job, "kind", ""),
            getattr(job, "space", ""), getattr(job, "dest_id", ""),
            getattr(job, "grade", ""), getattr(job, "start_bucket", 0),
            getattr(job, "end_bucket", 0), getattr(job, "start_minute", None),
            getattr(job, "end_minute", None),
            round(float(getattr(job, "tonnage_t", 0.0) or 0.0), 6),
            round(float(getattr(job, "rate_tph", 0.0) or 0.0), 6),
            tuple(getattr(job, "path", []) or []),
        )

    before_ids, after_ids = set(before_jobs), set(after_jobs)
    common_ids = before_ids & after_ids
    changed = sorted(
        job_id for job_id in common_ids
        if signature(before_jobs[job_id]) != signature(after_jobs[job_id]))
    removed = sorted(before_ids - after_ids)
    added = sorted(after_ids - before_ids)
    preserved = sorted(common_ids - set(changed))
    before_t = sum(float(getattr(job, "tonnage_t", 0.0) or 0.0)
                   for job in before_jobs.values())
    after_t = sum(float(getattr(job, "tonnage_t", 0.0) or 0.0)
                  for job in after_jobs.values())
    retained_pct = (100.0 * after_t / before_t if before_t > 0
                    else (100.0 if after_t == 0 else 0.0))
    return {
        "preserved": preserved,
        "changed": changed,
        "removed": removed,
        "added": added,
        "before_t": before_t,
        "after_t": after_t,
        "tonnage_delta_t": after_t - before_t,
        "retained_pct": retained_pct,
    }


def page_exceptions():
    st.subheader("Exceptions & Replanning")
    scn, s = common.sc(), common.sim()
    render_planning_operating_context(scn)
    if s is None:
        st.info(
            "No execution/replanning history exists. Generate a plan, then "
            "run Simulation Control or queue an unexpected event test.")
        return
    render_simulation_replan_status(scn, s)
    rows = []
    for r in s.log:
        if r.kind in ("EVENT", "DEVIATION", "REPLAN"):
            minute = int(getattr(r, "detail", {}).get(
                "minute", r.bucket * int(getattr(s, "bucket_width_min", 60))))
            source = {
                "EVENT": "Unexpected simulation event",
                "DEVIATION": "Execution deviation",
                "REPLAN": "Reactive replan",
            }[r.kind]
            rows.append({
                "minute": minute,
                "time": planning_clock_label(scn, minute),
                "source": source,
                "type": r.kind, "asset": r.asset, "detail": r.message})
    st.markdown("#### Unexpected event / deviation / reactive replan log")
    st.caption(
        "Planned outages and derates are inputs to the original plan and do "
        "not appear here. This log contains execution-time detections and "
        "their reactive replans.")
    show_df(pd.DataFrame(rows), height=300)

    if not s.plan_history:
        st.caption("No reactive replans have completed yet.")
        return
    st.markdown("#### Before / after plan comparison")
    labels = [f"replan #{i + 1} · {planning_clock_label(scn, b * int(getattr(s, 'bucket_width_min', 60)))} · {reason}"
              for i, (b, _, _, reason) in enumerate(s.plan_history)]
    sel = st.selectbox("Replan", labels)
    idx = labels.index(sel)
    bucket, before, after, reason = s.plan_history[idx]
    impact = replan_impact_summary(before, after)
    latency = latest_replan_latency(s, idx)
    c = st.columns(6)
    c[0].metric("Trigger", reason.split(":")[0])
    c[1].metric("Jobs preserved", len(impact["preserved"]))
    c[2].metric(
        "Jobs changed / removed",
        len(impact["changed"]) + len(impact["removed"]))
    c[3].metric("Jobs added", len(impact["added"]))
    c[4].metric("Remaining plan mass", f"{impact['after_t'] / 1000:,.1f} kt",
                delta=f"{impact['tonnage_delta_t'] / 1000:+,.1f} kt")
    c[5].metric("Mass retained", f"{impact['retained_pct']:.1f}%")
    timing = []
    if latency["detected_minute"] is not None:
        timing.append(
            "detected " + planning_clock_label(
                scn, int(latency["detected_minute"])))
    if latency["completed_minute"] is not None:
        timing.append(
            "replan completed " + planning_clock_label(
                scn, int(latency["completed_minute"])))
    if latency["latency_min"] is not None:
        timing.append(f"{int(latency['latency_min'])} model min detection→completion")
    if latency["solve_s"] is not None:
        timing.append(f"{float(latency['solve_s']):.2f} s solver wall time")
    if timing:
        st.caption(" · ".join(timing))
    st.caption(
        "Mass retained compares remaining planned physical/as-handled tonnes "
        "before and after the replan; it is not executed recovery. Unmet "
        "demand and unresolved constraints remain authoritative.")
    unresolved = [r for r in s.plan.reasons] if s.plan else []
    if unresolved:
        st.warning(
            "Unresolved consequences: "
            + " · ".join(theme.operator_text(r) for r in unresolved))
    left, right = st.columns(2)
    with left:
        plot(gantt_figure(jobs_to_gantt_df(
            scn, before, coalesce_execution_windows=True), color_by="kind",
                          group_by="machine", height=330,
                          title=f"BEFORE (at bucket {bucket})"))
    with right:
        plot(gantt_figure(jobs_to_gantt_df(
            scn, after, coalesce_execution_windows=True), color_by="kind",
                          group_by="machine", height=330, title="AFTER"))
    affected = list(impact["removed"]) + list(impact["changed"])
    if affected:
        st.caption("changed/removed: " + ", ".join(affected[:20]))
    if impact["added"]:
        st.caption("added: " + ", ".join(impact["added"][:20]))
