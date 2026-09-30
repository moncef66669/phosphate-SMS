"""Operator scheduling rules, independent from demand presets and site edits."""
from copy import deepcopy

import streamlit as st

from mph_sim.objective_config import (SCHEDULING_DEFAULTS, scheduling_errors,
                                     scheduling_values)
from . import common


def stage_scheduling_configuration(scenario, values):
    unknown = set(values) - set(SCHEDULING_DEFAULTS)
    if unknown:
        raise ValueError("Unknown scheduling settings: " + ", ".join(sorted(unknown)))
    proposed = scheduling_values(scenario)
    proposed.update(values)
    errors = scheduling_errors(proposed)
    if errors:
        raise ValueError(" · ".join(errors))
    staged = deepcopy(scenario)
    staged.config.update({key: str(value)
                          if key in ("OBJECTIVE_MODE", "SOLVE_MODE")
                          else float(value)
                          for key, value in proposed.items()})
    return staged


def render_scheduling_configuration(scenario):
    values = scheduling_values(scenario)
    nonce = int(st.session_state.get("scheduling_config_nonce", 0))
    with st.form(f"scheduling_configuration_{scenario.name}_{nonce}"):
        st.markdown("#### Scheduling policy")
        mode_col, volume_col = st.columns(2)
        mode = mode_col.selectbox(
            "Objective mode", ["service_first", "weighted"],
            index=0 if values["OBJECTIVE_MODE"] == "service_first" else 1,
            format_func=lambda v: "Service-first (protected priorities)" if v == "service_first" else "Weighted (comparison)",
            help="Service-first protects qualified on-time delivery before bounded continuity and operating-cost passes. Weighted retains the comparison objective.")
        minimum = volume_col.number_input(
            "Minimum job volume [t]", min_value=0.0,
            value=float(values["MIN_JOB_VOLUME_T"]), step=1.0,
            help="0 disables the additional minimum. Positive values apply to a complete contiguous machine/activity job, not each hourly bucket. The whole-tonne solver rounds the minimum up. It can reduce service or conflict with fixed jobs.")
        allowance = st.number_input(
            "Permitted priority-weighted service loss [t]", min_value=0.0,
            value=float(values["SERVICE_DEGRADATION_T"]), step=1.0,
            help="Service-first only: maximum cumulative degradation from the primary qualified-service incumbent, measured in order-priority-weighted tonnes. Default 0. This is not an unweighted percentage.")
        solve_mode = st.selectbox(
            "Solve mode", ["fast", "reproducible"],
            index=0 if values["SOLVE_MODE"] == "fast" else 1,
            format_func=lambda v: (
                "Fast (parallel · plan may vary between runs)" if v == "fast"
                else "Reproducible (single worker · identical plan each run)"),
            help=(
                "Fast runs a parallel CP-SAT portfolio bounded by wall clock: "
                "predictable latency, but the returned plan can differ between "
                "identical runs whenever the model has tied optima, so a KPI "
                "from a single fast run is one draw, not a value. Reproducible "
                "uses one worker and a deterministic-time budget only, so the "
                "same inputs, configuration and seed always return the same "
                "plan; wall time then becomes unpredictable. Use reproducible "
                "for quoted KPIs, the business case and acceptance evidence."))
        parcel_penalty = st.number_input(
            "Short-parcel preference penalty", min_value=0, max_value=9999,
            value=int(values["SHORT_PARCEL_PENALTY"]), step=1,
            help="Independent from machine-start/stop weights. Prices below-preference hourly commands under the separate campaign policy; it does not impose the complete-job minimum above. In service-first mode this is a last-tier preference. 0 disables its cost.")
        st.caption(
            "Job boundaries include activity changes, rate changes, fixed-job boundaries and rolling-window splits. "
            "Physical minimum stable rates still apply when the configured volume is 0. "
            "Qualification minimizes uncredited off-spec dispatch first. Later scheduling candidates "
            "must pass minute validation and cannot increase actual starts, stops or changeovers.")
        submitted = st.form_submit_button(
            "Apply scheduling configuration", type="primary",
            disabled=bool(st.session_state.get("solving", False)))
    if submitted:
        proposed = dict(OBJECTIVE_MODE=mode, SOLVE_MODE=solve_mode,
                        MIN_JOB_VOLUME_T=minimum,
                        SERVICE_DEGRADATION_T=allowance, SHORT_PARCEL_PENALTY=parcel_penalty)
        try:
            staged = stage_scheduling_configuration(scenario, proposed)
        except ValueError as exc:
            st.error(f"Nothing was applied. {exc}")
            return
        if scheduling_values(staged) != values:
            st.session_state["scenario"] = staged
            stale = common.invalidate_outputs()
            common.revalidate()
            st.session_state["operational_config_flash"] = (
                "Scheduling configuration applied" + (" · previous plan cleared" if stale else ""))
        else:
            st.session_state["operational_config_flash"] = "No scheduling changes to apply."
        st.session_state["scheduling_config_nonce"] = nonce + 1
        st.rerun()
