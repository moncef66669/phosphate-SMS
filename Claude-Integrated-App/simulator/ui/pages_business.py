"""Paired allocation experiments, with scope and assumptions beside results."""
from __future__ import annotations

from dataclasses import asdict
import io
import json
import zipfile

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from mph_sim.benchmark import (BenchmarkOptions, benchmark_provenance, run_benchmark,
                               scenario_fingerprint, value_sensitivity)
from mph_sim.scenarios import BUSINESS_CASE_PRESETS, make_business_case_scenario
from . import common, theme


def comparison_table(result):
    rows = []
    for arm in result.arms.values():
        k = arm.kpis
        rows.append({
            "Policy": arm.label,
            "Status": ("TIMING / PHYSICAL CHECK FAILED" if arm.physical_errors else arm.plan.status),
            "Eligible for benefit claim": arm.valid,
            "On-time terminal t": k.get("final_product_on_time_t"),
            "Service %": k.get("final_product_service_pct"),
            "Known on-spec t": k.get("on_spec_mass_t"),
            "Unknown assay t": k.get("quality_unknown_t"),
            "Unstacked t": k.get("unstacked_t"),
            "Receiving queue peak t": k.get("receiving_queue_peak_t"),
            "Receiving holding t·h": k.get("receiving_queue_t_h"),
            "Handling t": k.get("handled_t"),
            "Timing errors": k.get("minute_timing_errors"),
        })
    return pd.DataFrame(rows)


def benchmark_zip(result, scenario, economics=None):
    if scenario_fingerprint(scenario) != result.input_fingerprint:
        raise ValueError("Comparison inputs do not match the recorded scenario fingerprint")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("comparison.json", json.dumps(result.to_dict(include_plans=True),
                                               indent=2, ensure_ascii=False, allow_nan=False))
        z.writestr("scenario.json", json.dumps(asdict(scenario), indent=2,
                                             ensure_ascii=False, allow_nan=False))
        z.writestr("scorecard.csv", comparison_table(result).to_csv(index=False))
        if economics is not None:
            z.writestr("economic_sensitivity.json", json.dumps(economics, indent=2))
    return buffer.getvalue()


def _scenario_controls(base):
    selected = st.selectbox("Scenario", list(BUSINESS_CASE_PRESETS),
                            format_func=lambda key: BUSINESS_CASE_PRESETS[key]["label"])
    definition = BUSINESS_CASE_PRESETS[selected]
    st.caption(definition["description"])
    parameters = {}
    if definition["parameters"]:
        with st.expander("Scenario assumptions", expanded=True):
            for key, default in definition["parameters"].items():
                label = key.replace("_", " ").capitalize()
                if key == "machine_id":
                    choices = [m.id for m in base.machines if m.kind in ("reclaimer", "stacker_reclaimer")]
                    if not choices:
                        choices = [m.id for m in base.machines]
                    parameters[key] = st.selectbox(label, choices,
                        index=choices.index(default) if default in choices else 0)
                elif key == "destination_id":
                    choices = [d.id for d in base.destinations if d.demand_kind != "process_feed"]
                    parameters[key] = st.selectbox(label, choices,
                        index=choices.index(default) if default in choices else 0)
                else:
                    parameters[key] = st.number_input(label, min_value=0.0,
                                                      value=float(default), key="bc_" + key)
    try:
        return make_business_case_scenario(base, selected, parameters)
    except ValueError as exc:
        st.error(str(exc))
        return None


def render_result(result, scenario):
    st.markdown("#### Paired results")
    for warning in result.warnings:
        st.warning(warning)
    locals_ = [a for key, a in result.arms.items() if key != "coordinated"]
    if locals_ and all(a.decisions and all("Retained all sources" in d.get("reason", "")
                                          for d in a.decisions) for a in locals_):
        st.info("All local policies retained the complete source pool in this run. This source-selection study has no active policy contrast; its zero difference cannot establish operator/model equivalence. Use the independent-SMS study to test cross-site visibility.")
    if result.best_local_id and result.deltas:
        baseline = result.arms[result.best_local_id]
        c = st.columns(3)
        c[0].metric("Additional on-time tonnes", f"{result.deltas.get('final_product_on_time_t', 0):+,.0f} t")
        c[1].metric("Service improvement", f"{result.deltas.get('final_product_service_pct', 0):+.1f} points")
        if "receiving_queue_t_h" in result.deltas:
            c[2].metric("Receiving holding change", f"{result.deltas['receiving_queue_t_h']:+,.0f} t·h",
                        help="Negative means less material waits at the assumed receiving interface.")
        else:
            c[2].metric("Unstacked change", f"{result.deltas.get('unstacked_t', 0):+,.0f} t")
        st.caption("Headline comparator: " + baseline.label +
                   ". Selected by highest on-time delivery, then known compliant mass. Negative results are retained.")
    table = comparison_table(result)
    compact = ["Policy", "Status", "On-time terminal t", "Service %"]
    compact += [column for column in ("Receiving queue peak t", "Receiving holding t·h")
                if table[column].notna().any()]
    st.dataframe(table[compact], hide_index=True, width="stretch")
    fig = go.Figure()
    valid_arms = [a for a in result.arms.values() if a.valid]
    measures = {"On-time terminal tonnes": "final_product_on_time_t",
                "Unmet terminal tonnes": "final_product_unmet_t"}
    if "receiving_queue_t_h" in result.deltas:
        measures = {"Receiving holding exposure [t·h]": "receiving_queue_t_h", **measures}
    measure = st.selectbox("Chart measure", list(measures))
    fig.add_bar(x=[a.kpis.get(measures[measure], 0) for a in valid_arms],
                y=[a.label.replace("Independent SMS: ", "").replace("Coordinated model: shared site state", "Coordinated model") for a in valid_arms],
                orientation="h", marker_color=["#178777" if a.id == "coordinated" else "#8fa0b1" for a in valid_arms],
                text=[f"{a.kpis.get(measures[measure], 0):,.0f}" for a in valid_arms], textposition="auto")
    fig.update_layout(height=310, xaxis_title=measure, yaxis=dict(autorange="reversed"),
                      margin=dict(l=20, r=15, t=20, b=20))
    if valid_arms:
        st.plotly_chart(fig, width="stretch")
    else:
        st.warning("No plan passed the comparison's execution checks. Planned tonnes are shown for diagnosis only.")
    with st.expander("Scope, information available and decision log"):
        st.dataframe(table, hide_index=True, width="stretch")
        for text in result.assumptions:
            st.markdown("- " + text)
        st.code(result.input_fingerprint, language=None)
        st.caption("Input fingerprint covers demand, stock, grades, resources and settings.")
        for arm in result.arms.values():
            st.markdown("**" + arm.label + "**")
            for error in arm.physical_errors:
                st.error(error)
            for warning in arm.warnings:
                st.warning(warning)
            if arm.decisions:
                st.dataframe(pd.DataFrame(arm.decisions), hide_index=True, width="stretch")
    economic = None
    with st.expander("Economic sensitivity — enter approved assumptions"):
        st.caption("The simulation supplies tonnes. Contribution, recurrence and costs are your assumptions. "
                   "Use one currency throughout. This is not a plant benefit forecast.")
        st.caption("Value earlier delivery only when it avoids a documented production loss, missed sale or penalty. "
                   "Holding exposure is not automatically monetized as lost product.")
        c = st.columns(2)
        contribution = c[0].number_input("Net contribution per additional conforming tonne", min_value=0.0, value=0.0)
        periods = c[1].number_input("Comparable non-overlapping periods per year", min_value=0.0, value=0.0)
        cost = c[0].number_input("Annual operating cost", min_value=0.0, value=0.0)
        investment = c[1].number_input("One-time implementation cost", min_value=0.0, value=0.0)
        economic = value_sensitivity(result, contribution, periods, cost, investment)
        if contribution > 0 and periods > 0 and economic.get("available"):
            st.write(economic)
        else:
            st.info("Enter a contribution and recurrence to calculate a sensitivity. No financial saving is assumed by default.")
    st.download_button("Download comparison and reproducible inputs",
                       benchmark_zip(result, scenario, economic),
                       file_name="sms-allocation-business-case.zip", mime="application/zip")


def page_business_case():
    st.subheader("Business case · local SMS and coordinated planning")
    study = st.radio("Study", ["Independent SMS operators", "Current project scenario"], horizontal=True)
    st.caption("Paired inputs, explicit policies and physical accounting. These are simulations of allocation rules; actual operator performance requires operating logs.")
    budget = st.number_input("Solver budget per window", min_value=1.0, max_value=30.0,
                             value=3.0, step=1.0,
                             help="Deterministic solver time; wall time may be longer. Identical budget for optimized arms.")
    if study == "Independent SMS operators":
        from mph_sim.coordination_benchmark import make_coordination_scenario, run_coordination_benchmark
        c = st.columns(2)
        outage = c[0].checkbox("M16 receiving interruption", value=True,
                               help="A local receiving interruption tests upstream visibility. Turn it off for the control case.")
        capacity = c[1].number_input("Shared receiving capacity [t/h]", min_value=50.0,
                                     max_value=300.0, value=100.0, step=25.0)
        scenario = make_coordination_scenario(outage, capacity)
        st.info("Controlled three-site experiment: each mine follows its own dispatch rule; M16 sees local arrivals and stock. "
                "The model sees all three sites and their corridor timing. Rates, stock and demand here are synthetic assumptions.")
        st.caption("The full washing chain and spatial reclaim-face chemistry are outside this experiment. "
                   "The current-project study below tests the full model separately.")
        execute = lambda progress: run_coordination_benchmark(scenario, BenchmarkOptions(max_solve_s=budget), progress)
    else:
        scenario = _scenario_controls(common.sc())
        if scenario is None:
            return
        st.info("Full project model: local rules select source portfolios. All arms share the global physical scheduler, including blending and dispatch timing. "
                "This study measures source-allocation sensitivity; the independent-SMS study measures coordination.")
        execute = lambda progress: run_benchmark(scenario, BenchmarkOptions(max_solve_s=budget), progress)
    key = (study, scenario_fingerprint(scenario), budget)
    if st.button("Run paired comparison", type="primary"):
        with st.status("Running paired allocation policies…", expanded=True) as status:
            try:
                result = execute(lambda label: st.write(label))
            except Exception as exc:
                status.update(label="Comparison did not complete", state="error")
                st.error(str(exc))
                return
            st.session_state["business_result"] = (key, result, scenario)
            status.update(label="Comparison complete", state="complete", expanded=False)
    saved = st.session_state.get("business_result")
    if saved:
        if saved[0] != key:
            st.warning("Inputs or study changed. Run the comparison again to update the results.")
        elif saved[1].provenance.get("source_sha256") != benchmark_provenance(BenchmarkOptions(max_solve_s=budget))["source_sha256"]:
            st.warning("The model implementation changed after this comparison. Run it again to refresh the evidence.")
        else:
            render_result(saved[1], saved[2])
