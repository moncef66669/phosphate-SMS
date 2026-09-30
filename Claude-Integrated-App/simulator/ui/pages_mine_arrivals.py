"""Daily, grade-resolved mine supply with one transactional Apply boundary."""
from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import math

import pandas as pd
import streamlit as st

from mph_sim import mine_arrivals
from . import common, theme


DAY_COLUMN = "Day"
DATE_COLUMN = "Date"
TOTAL_COLUMN = "Daily total [t]"
STOCK_POLICIES = {
    "OPERATING": "Operating inventory",
    "EXACT_DAILY": "Exact daily stock targets",
}


def arrival_frame(scenario, configuration: dict, site: str) -> pd.DataFrame:
    """Display one row per day, keeping physical tonnes numeric."""
    grades = mine_arrivals.raw_grades(scenario)
    rows = []
    for entry in sorted(
            (row for row in configuration["days"] if row["site"] == site),
            key=lambda row: row["day"]):
        day = int(entry["day"])
        values = {grade: float(entry["volumes_t"].get(grade, 0.0))
                  for grade in grades}
        rows.append({
            DAY_COLUMN: day,
            DATE_COLUMN: (scenario.sim.bucket_dt(0) + timedelta(
                days=day - 1)).strftime("%Y-%m-%d"),
            **values,
            TOTAL_COLUMN: sum(values.values()),
        })
    return pd.DataFrame(
        rows, columns=[DAY_COLUMN, DATE_COLUMN, *grades, TOTAL_COLUMN])


def configuration_from_frames(scenario, original: dict, enabled: bool,
                              stock_policy: str,
                              frames: dict[str, pd.DataFrame]) -> dict:
    """Validate every table before returning a proposal; never mutate inputs."""
    if set(frames) != set(mine_arrivals.SITE_LABELS):
        raise ValueError("Both mine arrival tables are required.")
    if stock_policy not in STOCK_POLICIES:
        raise ValueError("Select a supported stock policy.")
    grades = mine_arrivals.raw_grades(scenario)
    proposal = deepcopy(original)
    proposal["enabled"] = bool(enabled)
    proposal["stock_policy"] = stock_policy
    proposal["days"] = []
    for site, frame in frames.items():
        expected = arrival_frame(scenario, original, site)
        if (set(frame.columns) != set(expected.columns)
                or len(frame) != len(expected)):
            raise ValueError("Arrival days and grade columns cannot be added or removed.")
        expected_dates = dict(zip(expected[DAY_COLUMN], expected[DATE_COLUMN]))
        seen_days = set()
        for row in frame.to_dict("records"):
            day = row[DAY_COLUMN]
            if (isinstance(day, bool) or day not in expected_dates
                    or day in seen_days
                    or row[DATE_COLUMN] != expected_dates[day]):
                raise ValueError("Arrival day and date identifiers cannot be changed.")
            seen_days.add(day)
            values = {}
            for grade in grades:
                value = row[grade]
                try:
                    quantity = float(value)
                except (TypeError, ValueError):
                    raise ValueError(
                        f"{mine_arrivals.SITE_LABELS[site]}, day {day}, "
                        f"{grade}: enter a non-negative quantity in tonnes.") from None
                if (isinstance(value, bool) or not math.isfinite(quantity)
                        or quantity < 0 or quantity != round(quantity)):
                    raise ValueError(
                        f"{mine_arrivals.SITE_LABELS[site]}, day {day}, "
                        f"{grade}: enter a finite, non-negative quantity in whole tonnes.")
                values[grade] = quantity
            proposal["days"].append({
                "day": int(day), "site": site, "volumes_t": values,
            })
    day_order = {(row["site"], row["day"]): index
                 for index, row in enumerate(original["days"])}
    proposal["days"].sort(key=lambda row: day_order[(row["site"], row["day"])])
    return proposal


def _source_capacity_rows(scenario) -> list[dict]:
    """Expose existing upstream buffers without claiming live hopper sizing."""
    source_ids = getattr(mine_arrivals, "SITE_SOURCE_SPACES", {})
    known_ids = {space_id for ids in source_ids.values() for space_id in ids}
    return [{
        "Site": mine_arrivals.SITE_LABELS[space.site],
        "Arrival buffer": space.id,
        "Starting stock [t]": float(space.mass_t),
        "Planning capacity [t]": float(space.capacity_t),
    } for space in scenario.spaces
        if space.site in mine_arrivals.SITE_LABELS
        and (space.id in known_ids or space.area == "mine-interface")]


def page_mine_arrivals() -> None:
    st.subheader("Mine arrivals")
    scenario = common.sc()
    if not mine_arrivals.supported(scenario):
        st.info("Load a BG + LO + MPH planning scenario to configure mine arrivals.")
        return

    flash = st.session_state.pop("mine_arrivals_flash", None)
    if flash:
        st.success(flash)
    if st.session_state.get("mine_arrivals_preferences_error"):
        st.error(st.session_state["mine_arrivals_preferences_error"])

    configuration = mine_arrivals.settings(scenario)
    grades = mine_arrivals.raw_grades(scenario)
    nonce = int(st.session_state.get("mine_arrivals_nonce", 0))
    st.caption(
        "Edit daily mine supply in whole physical, as-handled tonnes. Each grade "
        "keeps its own chemistry as it enters the upstream mine buffers. "
        "Apply saves both mines together and retains the settings across "
        "page changes and app restarts.")
    st.caption(
        "Incoming raw grades remain separate in the material balance. "
        "Pre-washing blend percentages currently control source-stockpile "
        "shares, not individual incoming-grade shares.")
    st.info(
        "The starting arrival profile is a synthetic planning assumption, "
        "not measured plant production. Lower-grade BT/TBT supply exceeds "
        "MT, with day-to-day variation. Replace it with the mine production "
        "plan when available.")

    frames = {}
    with st.form(f"mine_arrivals_form_{nonce}"):
        enabled = st.toggle(
            "Enable daily mine arrivals", value=bool(configuration["enabled"]),
            key=f"mine_arrivals_enabled_{nonce}",
            help="Disable to plan from starting inventory without this daily supply. "
                 "Entered quantities are retained. Use Apply to save.")
        stock_policy = st.radio(
            "Stock policy", options=list(STOCK_POLICIES), horizontal=True,
            index=list(STOCK_POLICIES).index(configuration["stock_policy"]),
            format_func=STOCK_POLICIES.get,
            key=f"mine_arrivals_stock_policy_{nonce}")
        st.caption(
            "Operating inventory carries stock forward continuously as "
            "arrivals, reclaiming and washing change each pile. Exact daily "
            "stock targets restore the historical end-of-day targets and "
            "can restrict the plan. Neither option resets inventory.")
        for site, label in mine_arrivals.SITE_LABELS.items():
            st.markdown(f"#### {label}")
            frame = arrival_frame(scenario, configuration, site)
            frames[site] = st.data_editor(
                frame, hide_index=True, width="stretch", num_rows="fixed",
                disabled=[DAY_COLUMN, DATE_COLUMN, TOTAL_COLUMN],
                key=f"mine_arrivals_{site}_{nonce}",
                column_config={
                    DAY_COLUMN: st.column_config.NumberColumn(format="%d"),
                    **{grade: st.column_config.NumberColumn(
                        theme.operator_text(f"{grade} [t]"), min_value=0.0, step=1.0,
                        format="localized", required=True) for grade in grades},
                    TOTAL_COLUMN: st.column_config.NumberColumn(format="localized"),
                })
            st.caption(
                f"{label} · {len(frame)}-day total: "
                f"{frame[TOTAL_COLUMN].sum():,.0f} t · average: "
                f"{frame[TOTAL_COLUMN].mean():,.0f} t/day. "
                "Totals update after Apply.")
        submitted = st.form_submit_button(
            "Apply mine arrivals", type="primary",
            disabled=bool(st.session_state.get("solving")
                          or st.session_state.get("solve_request")))

    if submitted:
        try:
            proposal = configuration_from_frames(
                scenario, configuration, enabled, stock_policy, frames)
            staged = mine_arrivals.staged_configuration(scenario, proposal)
            common.save_mine_arrivals_preferences(staged)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            st.error(f"Nothing was applied. {exc}")
            return
        changed = staged != scenario
        if changed:
            st.session_state.scenario = staged
            common.invalidate_outputs()
            common.revalidate()
        st.session_state.pop("mine_arrivals_preferences_error", None)
        st.session_state.mine_arrivals_nonce = nonce + 1
        st.session_state.mine_arrivals_flash = (
            "Mine arrivals saved. Generate a new plan to use these inputs."
            if changed else "Mine arrivals saved.")
        st.rerun()

    with st.expander("Arrival sources, capacities and planning assumptions"):
        st.caption(theme.operator_text(configuration.get("evidence", "")))
        st.caption(
            "Arrivals enter the upstream buffers and still pass through the "
            "modeled stackers, conveyors, stockpiles and washing lines. "
            "Their capacity and availability limits remain active. Buffer "
            "capacities shown below are planning inputs; they are not live "
            "hopper measurements. Large deliveries can make a plan infeasible.")
        rows = _source_capacity_rows(scenario)
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
