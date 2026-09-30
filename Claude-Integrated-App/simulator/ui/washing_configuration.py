"""Staged treatment/G10 settings; no chemistry or physical stocks relabelled."""
from copy import deepcopy

import pandas as pd
import streamlit as st

from mph_sim import washing
from . import common


def _optional(value):
    return None if value is None or pd.isna(value) else float(value)


def configuration_from_frames(original, active, changes, modes, limits):
    cfg = deepcopy(original)
    cfg["enabled"] = bool(active)
    cfg["cost_unit"] = washing.COST_UNIT
    for frame, column, expected in ((changes, "Characteristic", set(washing.PARAMETERS)),
                                     (limits, "Characteristic", set(washing.PARAMETERS)),
                                     (modes, "Mode", {"simple", "full"})):
        if set(frame[column]) != expected or len(frame) != len(expected):
            raise ValueError("Treatment/characteristic identifiers cannot be added, renamed or deleted")
    for row in changes.to_dict("records"):
        for key in ("simple", "full"):
            cfg["modes"][key]["changes_pct"][row["Characteristic"]] = float(row[key])
    for row in modes.to_dict("records"):
        mode = cfg["modes"][row["Mode"]]
        mode["available"] = bool(row["Available"])
        mode["dry_solids_yield"] = float(row["Dry mass yield [%]"]) / 100
        mode["cost_per_tsm_t"] = _optional(row["Cost / TSM tonne"])
        mode["residence_min"] = _optional(row["Residence override [min]"])
    for row in limits.to_dict("records"):
        cfg["g10_limits"][row["Characteristic"]] = [float(row["Min"]), float(row["Max"])]
    return cfg


def render_washing_configuration(sc):
    cfg = washing.settings(sc)
    raw_cfg = sc.config.get(washing.CONFIG_KEY)
    default_active = (
        bool(raw_cfg["enabled"])
        if isinstance(raw_cfg, dict) and "enabled" in raw_cfg
        else True
    )
    nonce = int(st.session_state.get("washing_configuration_nonce", 0))
    st.markdown("#### Washing treatments & PAP G10 contract")
    st.caption("For untouched built-in demand presets, generated raw-feed quotas are recalculated as yield-aware ceilings. Output/corridor targets retain their hard-or-forecast status; terminal-stock constraints and operator-fixed jobs stay hard. Operator-edited quotas are preserved and may need review.")
    if sc.config.get("_WASHING_DERIVED_POLICY_NOTE"):
        st.info(sc.config["_WASHING_DERIVED_POLICY_NOTE"])
    changes = pd.DataFrame([{"Characteristic": p, "simple": cfg["modes"]["simple"]["changes_pct"][p],
                             "full": cfg["modes"]["full"]["changes_pct"][p]} for p in washing.PARAMETERS])
    modes = pd.DataFrame([{"Mode": key, "Washing option": mode["name"],
                          "Available": mode["available"],
                          "Dry mass yield [%]": 100 * mode["dry_solids_yield"],
                          "Cost / TSM tonne": mode["cost_per_tsm_t"],
                          "Residence override [min]": mode["residence_min"]}
                         for key, mode in cfg["modes"].items()])
    for column in ("Cost / TSM tonne", "Residence override [min]"):
        modes[column] = pd.to_numeric(modes[column]).astype(float)
    limits = pd.DataFrame([{"Characteristic": p, "Min": bounds[0], "Max": bounds[1],
                           "Unit": "ppm" if p == "Cd" else "%"}
                          for p, bounds in cfg["g10_limits"].items()])
    with st.form(f"washing_configuration_{nonce}"):
        st.markdown("##### Enable washing in the planning model")
        active = st.toggle(
            "Enable washing treatment decisions",
            value=default_active,
            help=("Enabled by default for a new configuration. Turning this "
                  "off removes treatment alternatives but PAP still demands "
                  "G10. Use Apply below to save the choice."),
        )
        st.caption("Enabled by default. PAP's G10 receiving contract remains active whether treatment decisions are enabled or disabled. Existing stocks and blend recipes remain unchanged. Changes take effect only after Apply. This stage supports the native uniform/weekly planner, not mixed resolution.")
        st.markdown("##### Quality improvement by washing option")
        st.caption(
            "Relative concentration changes [%], not percentage points. "
            "The defaults make raw TBT require Full washing, while higher-"
            "quality low-Cd feed can qualify with Simple washing. Chemistry "
            "is dry-mass weighted; moisture is wet-basis. The +20% moisture "
            "change maps a typical 15% feed to 18% product moisture. Missing "
            "assays remain unknown.")
        edited_changes = st.data_editor(changes, hide_index=True, width="stretch", disabled=["Characteristic"],
            key=f"washing_changes_{nonce}",
            column_config={key: st.column_config.NumberColumn(
                "Simple washing [%]" if key == "simple" else "Full washing [%]", min_value=-100., max_value=1000.)
                for key in ("simple", "full")})
        st.markdown("##### Washing-option operating parameters")
        st.caption("Yield is recovered TSM / dry feed. Wet output also accounts for feed/product moisture. Treatment cost is charged per recovered TSM tonne (L = $0.63/TSM; LF = $1.04/TSM). Blank residence inherits the existing line value; 0 is instantaneous.")
        edited_modes = st.data_editor(modes, hide_index=True, width="stretch",
            disabled=["Mode", "Washing option"],
            column_order=["Washing option", "Available", "Dry mass yield [%]",
                          "Cost / TSM tonne", "Residence override [min]"],
            key=f"washing_modes_{nonce}", column_config={
                "Dry mass yield [%]": st.column_config.NumberColumn(min_value=.001, max_value=100.),
                "Cost / TSM tonne": st.column_config.NumberColumn(
                    "Unit cost [$/TSM]",
                    min_value=0., max_value=1_000_000., format="%.2f"),
                "Residence override [min]": st.column_config.NumberColumn(min_value=0., max_value=1_000_000., step=1.)})
        st.markdown("##### PAP G10 acceptance limits")
        st.caption("G10 is PAP's certified receiving grade, not a relabelled treatment stream. Every limit is hard: off-spec or unassayed material cannot be scheduled or replayed into PAP.")
        edited_limits = st.data_editor(limits, hide_index=True, width="stretch", disabled=["Characteristic", "Unit"],
            key=f"washing_g10_{nonce}", column_config={
                "Min": st.column_config.NumberColumn(min_value=0.),
                "Max": st.column_config.NumberColumn(min_value=0.)})
        submitted = st.form_submit_button("Apply washing treatments & G10 contract", type="primary",
                                          disabled=bool(st.session_state.get("solving", False)))
    if submitted:
        try:
            proposal = configuration_from_frames(
                cfg, active, edited_changes, edited_modes, edited_limits)
            if proposal["enabled"] and sc.sim.mixed_resolution_enabled:
                raise ValueError("Disable mixed resolution before enabling grade-resolved washing")
            staged = washing.stage_configuration(sc, proposal)
        except (TypeError, ValueError, KeyError) as exc:
            st.error(f"Nothing was applied. {exc}")
            return
        if staged != sc:
            st.session_state["scenario"] = staged
            common.invalidate_outputs()
            common.revalidate()
            st.session_state["washing_configuration_flash"] = "Washing treatment settings and the PAP G10 contract were applied; regenerate the plan."
        st.session_state["washing_configuration_nonce"] = nonce + 1
        st.rerun()
    with st.expander("Preview treatment quality and recovery by raw grade"):
        rows = []
        for grade in sc.grades:
            if not grade.material_class.startswith("raw"):
                continue
            for key, mode in cfg["modes"].items():
                try:
                    quality = washing.treated_quality(grade.quality, mode)
                    factor = washing.output_factor(grade.quality, quality, mode["dry_solids_yield"])
                    passes = all(p in quality and lo <= quality[p] <= hi for p, (lo, hi) in cfg["g10_limits"].items())
                    rows.append({"Raw grade": grade.id, "Treatment": mode["name"], **quality,
                                 "Wet mass yield [%]": round(100 * factor, 3), "Meets Demo G10": passes})
                except ValueError as exc:
                    rows.append({"Raw grade": grade.id, "Treatment": mode["name"], "Error": str(exc)})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
