"""Operator Monitor workspaces, including washing-treatment traceability."""
from __future__ import annotations

import math

import pandas as pd
import streamlit as st

from mph_sim import washing
from mph_sim.kpis import planned_delivery_quality

from . import common, pages_overview, pages_planning, process_mimic, theme


def page_kpi_flow_schedule() -> None:
    """Render the KPI overview, global flow and full job schedule in order."""
    pages_overview.page_overview(monitor_compact=True)
    st.divider()
    process_mimic.render_global_flow(
        common.sc(), common.plan(), common.sim())
    st.divider()
    pages_planning.page_schedule()


def page_machine_coverage() -> None:
    """Render the dedicated machine coverage and usage workspace."""
    pages_planning.page_machine_coverage()


def _quality_status(quality: dict, specs) -> str:
    """Classify one known assay vector against every G10 interval."""
    for spec in specs:
        value = quality.get(spec.param)
        try:
            known = value is not None and math.isfinite(float(value))
        except (TypeError, ValueError):
            known = False
        if not known:
            return "UNKNOWN — not certifiable"
        if spec.pmin is not None and float(value) < spec.pmin + spec.margin - 1e-9:
            return "OUTSIDE G10"
        if spec.pmax is not None and float(value) > spec.pmax - spec.margin + 1e-9:
            return "OUTSIDE G10"
    return "WITHIN G10"


def _bucket_time(scn, bucket: int) -> pd.Timestamp:
    return pd.Timestamp(scn.sim.start_iso) + pd.Timedelta(
        hours=float(bucket) * float(scn.sim.dt_h))


def treatment_quality_rows(scn, plan, sim=None, *, actual: bool = False) -> pd.DataFrame:
    """One auditable row per washing component and bucket.

    Source and output identities remain physical stream identities.  G10 is
    evaluated in a separate column because final PAP certification may be the
    result of blending several treated components.
    """
    columns = [
        "basis", "job", "bucket", "time", "washing_line", "machine",
        "treatment", "source_space", "target_space", "source_grade",
        "output_stream", "feed_t", "product_t", "product_TSM_t",
        "unit_cost_per_TSM", "treatment_cost", "mass_loss_t",
        "dry_solids_yield_pct", "individual_G10_check",
        *[f"source_{parameter}" for parameter in washing.PARAMETERS],
        *[f"output_{parameter}" for parameter in washing.PARAMETERS],
    ]
    if plan is None:
        return pd.DataFrame(columns=columns)

    jobs = list(sim.jobs.values()) if sim is not None else list(plan.jobs)
    by_id = {job.id: job for job in jobs}
    if actual:
        source_rows = list(getattr(sim, "actual_washing_flows", []) or []) \
            if sim is not None else []
        entries = [(by_id.get(str(flow.get("job_id", ""))), flow)
                   for flow in source_rows]
    else:
        entries = [
            (job, flow)
            for job in jobs
            for flow in (getattr(job, "material_flows", []) or [])
            if flow.get("treatment_id")
        ]

    cfg = washing.settings(scn)
    specs = washing.g10_spec(cfg)
    records = []
    for job, flow in entries:
        treatment_id = str(flow.get("treatment_id", ""))
        if not treatment_id:
            continue
        mode = cfg.get("modes", {}).get(treatment_id, {})
        source_quality = dict(flow.get("quality", {}) or {})
        output_quality = dict(flow.get("output_quality", {}) or {})
        feed_t = float(flow.get("tonnage_t", 0.0) or 0.0)
        product_t = float(flow.get("output_tonnage_t", 0.0) or 0.0)
        output_moisture = output_quality.get("moisture")
        product_tsm = (
            product_t * (1 - float(output_moisture) / 100)
            if output_moisture is not None else None)
        unit_cost = mode.get("cost_per_tsm_t")
        bucket = int(flow.get("bucket", 0) or 0)
        line = str(flow.get("process_stage", "") or
                   getattr(job, "process_stage", "") or "—")
        machine = str(flow.get("machine", "") or
                      getattr(job, "machine", "") or "—")
        record = {
            "basis": "Executed actual" if actual else "Planned",
            "job": str(flow.get("job_id", "") or
                       getattr(job, "id", "") or "—"),
            "bucket": bucket,
            "time": _bucket_time(scn, bucket),
            "washing_line": line,
            "machine": machine,
            "treatment": str(mode.get("name", treatment_id)),
            "source_space": str(flow.get("source_space", "") or
                                getattr(job, "source_space", "") or "—"),
            "target_space": str(flow.get("target_space", "") or
                                getattr(job, "target_space", "") or "—"),
            "source_grade": str(flow.get("grade", "") or "—"),
            "output_stream": str(flow.get("output_grade", "") or "—"),
            "feed_t": feed_t,
            "product_t": product_t,
            "product_TSM_t": product_tsm,
            "unit_cost_per_TSM": unit_cost,
            "treatment_cost": (
                product_tsm * float(unit_cost)
                if product_tsm is not None and unit_cost is not None
                else None),
            "mass_loss_t": feed_t - product_t,
            "dry_solids_yield_pct": (
                100 * float(mode.get("dry_solids_yield", 0.0) or 0.0)),
            "individual_G10_check": _quality_status(output_quality, specs),
        }
        for parameter in washing.PARAMETERS:
            record[f"source_{parameter}"] = source_quality.get(parameter)
            record[f"output_{parameter}"] = output_quality.get(parameter)
        records.append(record)
    return pd.DataFrame(records, columns=columns).sort_values(
        ["bucket", "washing_line", "job"], kind="stable",
    ).reset_index(drop=True) if records else pd.DataFrame(columns=columns)


def treatment_run_rows(scn, rows: pd.DataFrame) -> pd.DataFrame:
    """Collapse bucket/component records into one operator row per job."""
    columns = [
        "Basis", "Treatment job", "Washing line", "Machine", "Treatment",
        "Start", "End", "Source", "Output stream", "Feed [t]",
        "Product [t]", "Recovered TSM [t]", "Unit cost [$/TSM]",
        "Treatment cost [$]", "Mass loss [t]", "Wet mass yield [%]",
        "Component G10 check",
    ]
    if rows.empty:
        return pd.DataFrame(columns=columns)
    duration = pd.Timedelta(hours=float(scn.sim.dt_h))
    records = []
    for job_id, group in rows.groupby("job", sort=False, dropna=False):
        feed = float(group["feed_t"].sum())
        product = float(group["product_t"].sum())
        product_tsm = float(group["product_TSM_t"].sum())
        unit_costs = [float(value) for value in group["unit_cost_per_TSM"]
                      if pd.notna(value)]
        treatment_cost = float(group["treatment_cost"].sum()) \
            if group["treatment_cost"].notna().any() else None
        checks = list(dict.fromkeys(
            str(value) for value in group["individual_G10_check"]
            if pd.notna(value)))
        records.append({
            "Basis": " / ".join(dict.fromkeys(
                str(value) for value in group["basis"])),
            "Treatment job": str(job_id),
            "Washing line": " / ".join(dict.fromkeys(
                str(value) for value in group["washing_line"])),
            "Machine": " / ".join(dict.fromkeys(
                str(value) for value in group["machine"])),
            "Treatment": " / ".join(dict.fromkeys(
                str(value) for value in group["treatment"])),
            "Start": group["time"].min(),
            "End": group["time"].max() + duration,
            "Source": " / ".join(dict.fromkeys(
                f"{space} ({grade})"
                for space, grade in zip(
                    group["source_space"], group["source_grade"]))),
            "Output stream": " / ".join(dict.fromkeys(
                str(value) for value in group["output_stream"])),
            "Feed [t]": feed,
            "Product [t]": product,
            "Recovered TSM [t]": product_tsm,
            "Unit cost [$/TSM]": (
                unit_costs[0]
                if unit_costs and all(math.isclose(
                    value, unit_costs[0]) for value in unit_costs)
                else None),
            "Treatment cost [$]": treatment_cost,
            "Mass loss [t]": feed - product,
            "Wet mass yield [%]": 100 * product / feed if feed else None,
            "Component G10 check": " / ".join(checks),
        })
    return pd.DataFrame(records, columns=columns).sort_values(
        ["Start", "Washing line", "Treatment job"], kind="stable",
    ).reset_index(drop=True)


def pap_g10_receipt_rows(scn, plan, sim=None) -> pd.DataFrame:
    """Bucket-level PAP receipts with the assay evidence behind G10."""
    columns = [
        "basis", "destination", "bucket", "time", "received_grade",
        "received_t", "certification",
        *washing.PARAMETERS,
    ]
    if plan is None:
        return pd.DataFrame(columns=columns)
    actual = sim is not None
    series = sim.delivered if actual else plan.delivered
    cfg = washing.settings(scn)
    destination_ids = set(cfg.get("pap_destinations", []))
    offset = int(getattr(plan, "horizon_offset", 0) or 0)
    records = []
    for destination in scn.destinations:
        if destination.id not in destination_ids:
            continue
        rates = series.get(destination.id, [])
        for local_bucket, rate_tph in enumerate(rates):
            mass = float(rate_tph or 0.0) * float(scn.sim.dt_h)
            if mass <= 1e-9:
                continue
            bucket = local_bucket + offset
            quality = (
                dict(sim.delivered_q[destination.id][local_bucket])
                if actual else
                planned_delivery_quality(
                    scn, plan, destination, bucket)
            )
            status = _quality_status(quality, destination.spec)
            reported_grade = (
                str(sim.delivered_grade[destination.id][local_bucket])
                if actual else "G10"
            )
            # Never display an off-spec or unknown actual receipt as G10.
            if status != "WITHIN G10":
                reported_grade = "NOT CERTIFIED"
            record = {
                "basis": "Executed actual" if actual else "Planned",
                "destination": destination.id,
                "bucket": bucket,
                "time": _bucket_time(scn, bucket),
                "received_grade": reported_grade or "NOT CERTIFIED",
                "received_t": mass,
                "certification": status,
            }
            record.update({parameter: quality.get(parameter)
                           for parameter in washing.PARAMETERS})
            records.append(record)
    return pd.DataFrame(records, columns=columns)


def page_washing_treatments() -> None:
    """Monitor washing lines, source/output assays and PAP G10 receipts."""
    st.subheader("Washing treatments & G10 certification")
    scn, plan, sim = common.sc(), common.plan(), common.sim()
    if not washing.enabled(scn):
        job_count = len(plan.jobs) if plan is not None else 0
        st.warning(
            "This scenario and its current plan do not include washing "
            "treatments. "
            + (f"The visible {job_count}-job plan contains zero treatment "
               "decisions, so there is no treatment quality record to plot. "
               if plan is not None else "")
            + "Enable the provisional washing calibration, then generate a new plan."
        )
        if st.button(
                "Configure washing treatments",
                type="primary",
                key="monitor_open_washing_configuration"):
            common.goto_page(common.CONFIGURE_WASHING_TREATMENTS_PAGE)
            st.rerun()
        return
    if plan is None:
        st.info(
            "No treatment schedule yet — generate the active plan from the "
            "left sidebar."
        )
        return

    has_actual = bool(sim is not None and getattr(
        sim, "actual_washing_flows", []))
    basis_options = (["Executed actual", "Planned"] if has_actual
                     else ["Planned"])
    basis = st.radio(
        "Quality basis", basis_options, horizontal=True,
        key="washing_monitor_basis",
        help=("Executed actual uses replayed source assays and recovered mass. "
              "Planned uses the optimizer's assay and yield assumptions."),
    )
    actual = basis == "Executed actual"
    rows = treatment_quality_rows(scn, plan, sim, actual=actual)
    jobs = list(sim.jobs.values()) if sim is not None else list(plan.jobs)
    treatment_jobs = [job for job in jobs if getattr(job, "treatment_id", "")]
    if actual:
        executed_ids = set(rows["job"]) if not rows.empty else set()
        treatment_jobs = [job for job in treatment_jobs
                          if job.id in executed_ids]
    timeline = theme.jobs_to_gantt_df(
        scn, treatment_jobs, coalesce_execution_windows=True)
    if not timeline.empty:
        mode_names = {
            key: value["name"]
            for key, value in washing.settings(scn)["modes"].items()
        }
        timeline["treatment"] = timeline["treatment"].map(
            lambda value: mode_names.get(value, value))
        timeline["physical_machine"] = timeline["machine_label"]
        # A washing line is an exclusive process resource and is therefore
        # presented as the schedule row, exactly as equipment is elsewhere.
        timeline["machine_label"] = timeline["process_stage"]

    controls = st.columns(2)
    line_options = sorted(rows["washing_line"].dropna().unique()) \
        if not rows.empty else []
    mode_options = sorted(rows["treatment"].dropna().unique()) \
        if not rows.empty else []
    selected_lines = controls[0].multiselect(
        "Washing line", line_options, key="washing_monitor_lines")
    selected_modes = controls[1].multiselect(
        "Treatment", mode_options, key="washing_monitor_modes")
    shown_rows = rows
    shown_timeline = timeline
    if selected_lines:
        shown_rows = shown_rows[
            shown_rows["washing_line"].isin(selected_lines)]
        if not shown_timeline.empty:
            shown_timeline = shown_timeline[
                shown_timeline["process_stage"].isin(selected_lines)]
    if selected_modes:
        shown_rows = shown_rows[
            shown_rows["treatment"].isin(selected_modes)]
        if not shown_timeline.empty:
            shown_timeline = shown_timeline[
                shown_timeline["treatment"].isin(selected_modes)]

    st.markdown("#### Washing-line schedule")
    st.caption(
        "Each row is a washing line, using the same continuous-run timeline "
        "as the machine schedule. Hover retains the physical machine, source "
        "and output stream. Actual mode limits the view to treatments already "
        "executed; the table below is the mass-and-assay record."
    )
    theme.plot(theme.gantt_figure(
        shown_timeline, color_by="treatment", group_by="machine",
        height=max(330, 100 + 42 * max(
            shown_timeline["machine_label"].nunique()
            if not shown_timeline.empty else 0, 1)),
        show_tonnage=True,
    ))

    st.markdown("#### Treatment jobs")
    st.caption(
        "One row per planned or executed washing job. The detailed table "
        "below retains each bucket and material component for assay audit."
    )
    theme.show_df(
        treatment_run_rows(scn, shown_rows), height=300,
        empty_message="no treatment job matches this view")

    if shown_rows.empty:
        st.info("No washing treatments match this view.")
    else:
        feed = float(shown_rows["feed_t"].sum())
        product = float(shown_rows["product_t"].sum())
        cost = float(shown_rows["treatment_cost"].sum()) \
            if shown_rows["treatment_cost"].notna().any() else None
        metrics = st.columns(5)
        metrics[0].metric("Washing feed", f"{feed:,.0f} t")
        metrics[1].metric("Recovered product", f"{product:,.0f} t")
        metrics[2].metric("Mass loss", f"{feed - product:,.0f} t")
        metrics[3].metric(
            "Physical yield", f"{100 * product / feed:.1f}%" if feed else "—")
        metrics[4].metric(
            "Treatment cost", f"${cost:,.2f}" if cost is not None else "—")

    st.markdown("#### Source quality → treatment output quality")
    st.caption(
        "The individual G10 check is diagnostic. A component outside an "
        "interval may still be blended with another treated component; only "
        "the final PAP bucket below is certified G10. Missing assays are never "
        "treated as zero."
    )
    theme.show_df(shown_rows, height=390)

    st.markdown("#### PAP receipts — certified grade G10 only")
    receipts = pap_g10_receipt_rows(scn, plan, sim if actual else None)
    if not receipts.empty and set(receipts["received_grade"]) != {"G10"}:
        st.error(
            "A receipt lacks G10 certification. The replay should hold this "
            "dispatch; do not use the run operationally."
        )
    else:
        st.success(
            "Every positive PAP receipt in this view is assay-qualified and "
            "reported as G10. Underlying source and treatment stream identities "
            "remain visible above for traceability."
        )
    theme.show_df(receipts, height=300,
                  empty_message="no positive PAP receipt in this view")
