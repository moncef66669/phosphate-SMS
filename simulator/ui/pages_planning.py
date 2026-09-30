"""Planning, schedule, coverage, simulation and replanning pages."""
from __future__ import annotations

import copy

import pandas as pd
import streamlit as st

from mph_sim.coordinates import x_interval_to_reperes
from mph_sim.domain import EVENT_TYPES, FixedJob, SimEvent
from mph_sim.optimizer import blend_fractions

from . import common, theme
from .theme import (gantt_figure, jobs_to_gantt_df, plot, series_fig, show_df)


def _load_selected_planning_scenario():
    """Widget callback for future multi-scenario Plan workspaces."""
    code = st.session_state.get(
        "planning_scenario_code", common.DEFAULT_PLANNING_SCENARIO)
    target = common.planning_scenario_dataset(code)
    if st.session_state.get("scenario_name") != target:
        common.load_scenario(target)
        st.session_state.flash = (
            f"{common.planning_scenario_label(code)} loaded — "
            "no plan generated yet."
        )


def render_planning_scenario_selector():
    """Render the Plan-owned scenario selector without renaming model S3.

    Only S0 is commissioned today. The mapping is intentionally centralised
    so adding another planning scenario later extends the selector without
    bringing back the global S1/S2/S3 dataset picker.
    """
    code = st.selectbox(
        "Planning scenario",
        options=list(common.PLANNING_SCENARIOS),
        key="planning_scenario_code",
        format_func=common.planning_scenario_label,
        on_change=_load_selected_planning_scenario,
        help="S0 is the operator-facing code for the existing S3 three-site "
             "planning dataset. The alias does not change solver entities, "
             "routes or persisted results.",
    )
    target = common.planning_scenario_dataset(code)
    active = st.session_state.get("scenario_name") == target
    if active:
        scn = common.sc()
        horizon_h = scn.sim.horizon_buckets * scn.sim.dt_h
        st.caption(
            f"Active · BG + LO + MPH · {horizon_h:g} h horizon · "
            "internal model S3"
        )
    else:
        st.warning(
            "A diagnostic or imported dataset is active. S0 always maps to "
            "the built-in S3 three-site model."
        )
        if st.button("Load S0", key="load_s0_from_plan"):
            common.load_scenario(target)
            st.session_state.flash = (
                "S0 loaded — no plan generated yet."
            )
            st.rerun()


def render_plan_run_panel():
    """Compact validation/result panel; Generate Plan lives in the sidebar."""
    scn = common.sc()
    p = common.plan()
    issues = st.session_state.get("issues", [])
    n_err = sum(1 for issue in issues if issue.severity == "ERROR")
    n_wrn = sum(1 for issue in issues if issue.severity == "WARNING")
    horizon_h = scn.sim.horizon_buckets * scn.sim.dt_h
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
        summary[1].metric("Horizon", f"{horizon_h:g} h")
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
                "Use **Generate Plan** in the sidebar after reviewing inputs.")

        if solving:
            st.info("Plan generation is in progress. The staged solver status "
                    "appears above this page.")
        elif p is not None:
            unmet_t = sum(sum(values) for values in p.unmet.values())
            st.success(
                f"Current plan: {p.status} · {len(p.jobs)} jobs · "
                f"{unmet_t:,.0f} t unmet · {p.solver_wall_s:.1f} s")


# ================================================= 6. Planning Workspace
def page_planning():
    st.subheader("Planning settings")
    scn = common.sc()

    with st.form("planning_settings_form", border=True):
        st.markdown("#### Planning settings")
        c = st.columns(7)
        horizon_buckets = int(c[0].number_input(
            "Horizon [buckets]", value=scn.sim.horizon_buckets, min_value=2,
            help="planning horizon T; one bucket = Δt hours"))
        dt_h = c[1].number_input("Δt [h]", value=float(scn.sim.dt_h),
                                 min_value=0.25, step=0.25)
        frozen_buckets = int(c[2].number_input(
            "Frozen horizon [buckets]", value=scn.sim.frozen_buckets, min_value=0,
            help="jobs inside this window survive a replan unchanged"))
        max_solve_s = c[3].number_input(
            "Max solve budget", value=float(scn.sim.max_solve_s), min_value=1.0,
            help="CP-SAT deterministic-time budget (≈s; wall may be 2–5×)")
        c[4].selectbox("Solver", ["cp-sat (OR-Tools)"],
                       help="single deterministic backend in this MVP")
        seed = int(c[5].number_input("Seed", value=scn.sim.seed))
        deviation_tolerance = c[6].number_input(
            "Deviation tolerance", value=float(scn.sim.deviation_tolerance),
            min_value=0.0, max_value=1.0, step=0.05)

        st.markdown("**Objective weights** — w₁ must dominate (report §15.2)")
        w = scn.weights
        c = st.columns(7)
        w1_unmet = c[0].number_input(
            "w₁ unmet", value=float(w.w1_unmet), key="pw1")
        w2_offspec = c[1].number_input(
            "w₂ off-spec", value=float(w.w2_offspec), key="pw2")
        w3_process_cost = c[2].number_input(
            "w₃ cost", value=float(w.w3_process_cost), key="pw3",
            help="inactive: GradeCostModel curves not received")
        w4_giveaway = c[3].number_input(
            "w₄ give-away", value=float(w.w4_giveaway), key="pw4")
        w5_moves = c[4].number_input(
            "w₅ moves", value=float(w.w5_moves), key="pw5")
        w6_safety = c[5].number_input(
            "w₆ safety", value=float(w.w6_safety), key="pw6")
        w7_utilisation = c[6].number_input(
            "w₇ util", value=float(w.w7_utilisation), key="pw7")
        apply_settings = st.form_submit_button("Apply planning settings")

    if apply_settings:
        scn.sim.horizon_buckets = horizon_buckets
        scn.sim.dt_h = dt_h
        scn.sim.frozen_buckets = frozen_buckets
        scn.sim.max_solve_s = max_solve_s
        scn.sim.seed = seed
        scn.sim.deviation_tolerance = deviation_tolerance
        w.w1_unmet = w1_unmet
        w.w2_offspec = w2_offspec
        w.w3_process_cost = w3_process_cost
        w.w4_giveaway = w4_giveaway
        w.w5_moves = w5_moves
        w.w6_safety = w6_safety
        w.w7_utilisation = w7_utilisation
        stale = common.invalidate_outputs()
        common.revalidate()
        st.success(
            "planning settings applied"
            + (" — previous plan cleared" if stale else ""))

    # ---- maintenance windows & operator-fixed jobs ----------------------------
    left, right = st.columns(2)
    with left:
        st.markdown("**Maintenance windows (work calendars)**")
        cdf = pd.DataFrame([{"scope": c_.scope, "start": c_.start_bucket,
                             "end": c_.end_bucket, "reason": c_.reason}
                            for c_ in scn.calendars])
        if cdf.empty:
            cdf = pd.DataFrame(columns=["scope", "start", "end", "reason"])
        ced = st.data_editor(cdf, num_rows="dynamic", key="cal_ed", height=160)
        if st.button("Apply calendars"):
            from mph_sim.domain import CalendarEntry
            scn.calendars = [CalendarEntry(str(r["scope"]), int(r["start"]),
                                           int(r["end"]), str(r["reason"] or ""))
                             for _, r in ced.iterrows() if str(r["scope"]).strip()]
            stale = common.invalidate_outputs()
            common.revalidate()
            st.success("applied" + (" — previous plan cleared" if stale else ""))
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

    df = jobs_to_gantt_df(scn, p.jobs)
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
                        grade=j.grade, start_repere=r0, stop_repere=r1))
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
    horizon_h = float(scn.sim.horizon_buckets) * float(scn.sim.dt_h)
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


def page_simulation():
    st.subheader("Simulation Control")
    scn, p = common.sc(), common.plan()
    if p is None:
        st.info("Generate S0 with the sidebar **Generate Plan** action first.")
        return
    st.caption("Closed loop per report §15.8: plan → validate → dispatch → "
               "execute → feedback (JobUpdated) → detect deviation → replan.")

    auto = st.toggle("Auto-replan on deviation/event", value=True,
                     help="off = deviations are logged but the plan is kept")
    s = common.ensure_sim(auto_replan=auto)
    s.auto_replan = auto

    c = st.columns(7)
    if c[0].button("▶ Step 1"):
        s.step()
    if c[1].button("▶▶ Step 4"):
        for _ in range(4):
            if not s.step():
                break
    if c[2].button("⏵ Run to end"):
        with st.spinner("simulating…"):
            s.run_to_end()
    speed = c[3].selectbox("Speed", ["1 bucket/click", "4 buckets/click",
                                     "run-to-end"], index=0,
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
    c[6].metric("Bucket", f"{s.bucket}/{s.T}")

    # ---- event injection -------------------------------------------------------
    with st.container(border=True):
        st.markdown("**Inject event**")
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
        bucket = int(c[2].number_input("At bucket", value=max(s.bucket, 0),
                                       min_value=0, max_value=s.T - 1))
        mag = c[3].number_input("Magnitude", value=0.5 if et == "reduced_rate"
                                else 0.0)
        dur = int(c[4].number_input("Duration [buckets]", value=2, min_value=1))
        pjson = c[5].text_input("Params JSON", value='{"BPL": 66.0}'
                                if et == "offspec_assay" else "")
        if st.button("Inject"):
            import json as _json
            params = {}
            if pjson.strip():
                try:
                    params = _json.loads(pjson)
                except Exception:
                    st.error("bad params JSON")
            ev = SimEvent(f"UI{len(s.events) + 1}", et, bucket,
                          "" if tgt == "—" else tgt, mag, dur, params)
            s.inject(ev)
            st.session_state.queued_events.append(ev)
            st.success(f"queued {et} at bucket {bucket}")

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
    df = jobs_to_gantt_df(scn, list(s.jobs.values()))
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
    "grade", "output_grade", "tonnage_t", "output_tonnage_t", "rate_tph",
    "start_time", "end_time", "execution_duration_min", "depends_on",
    "dependency_delay_min", "path", "purpose", "repere_range",
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


def page_schedule():
    st.subheader("Job Schedule")
    scn, p, s = common.sc(), common.plan(), common.sim()
    if p is None:
        typical_s = common.typical_solve_s(
            st.session_state.scenario_name.split(" —")[0])
        st.info(
            "No job schedule yet — S0 has not been generated. Click "
            "**Generate Plan** in the left sidebar; the first solve usually "
            f"takes about {typical_s} s.")
        return
    jobs = list(s.jobs.values()) if s else p.jobs
    df = jobs_to_gantt_df(scn, jobs, include_positioning=True)
    show_reference = st.toggle(
        "Show reference/future equipment", value=False,
        key="schedule_show_reference",
        help=("Adds documented display, disabled and future assets to the "
              "timeline; they remain ineligible for solver jobs."))
    machine_register = theme.timeline_machine_register(
        scn, p, include_reference=show_reference)
    st.caption(
        "Primary view: current solver-controlled equipment. Empty rows show "
        "a structured no-job reason; reference/future rows are optional. "
        "Blue is reclaim, orange is stack, and green is non-material "
        "positioning for the next job. Positioning time is estimated from "
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
    regq = machine_register
    if sitef:
        if not q.empty:
            keep = q.apply(lambda r: any(common.match_site_filter(
                o, r["site"], r["area"]) for o in sitef), axis=1)
            q = q[keep]
        regkeep = regq.apply(lambda r: any(common.match_site_filter(
            o, r["site"], r["area"]) for o in sitef), axis=1)
        regq = regq[regkeep]
    if mach:
        q = q[q.machine_label.isin(mach)]
        regq = regq[regq.machine_label.isin(mach)]
    if kind:
        q = q[q.kind.isin(kind)]
        regq = regq[regq.kind.isin(kind)]
    if stat:
        q = q[q.status.isin(stat)]
    plot(gantt_figure(
        q, color_by="kind", group_by=grp,
        height=(theme.timeline_height(regq, minimum=430)
                if grp == "machine" else 430),
        row_register=regq if grp == "machine" else None,
        show_tonnage=True))
    show_df(schedule_display_frame(q), height=330)
    activity_export = q.copy()
    activity_export["machine_id"] = activity_export["machine"]
    activity_export["machine"] = activity_export["machine_label"]
    activity_export = activity_export.drop(columns=["machine_label"])
    material_export = activity_export[
        ~activity_export["kind"].isin(
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
def page_exceptions():
    st.subheader("Exceptions & Replanning")
    scn, s = common.sc(), common.sim()
    if s is None:
        st.info("Run the simulation first (Simulation Control).")
        return
    rows = []
    for r in s.log:
        if r.kind in ("EVENT", "DEVIATION", "REPLAN"):
            rows.append({
                "bucket": r.bucket,
                "time": scn.sim.bucket_dt(r.bucket).strftime("%d %H:%M"),
                "type": r.kind, "asset": r.asset, "detail": r.message})
    st.markdown("#### Event / deviation / replan log")
    show_df(pd.DataFrame(rows), height=300)

    if not s.plan_history:
        st.caption("no replans yet")
        return
    st.markdown("#### Before / after plan comparison")
    labels = [f"replan #{i + 1} @bucket {b} ({reason})"
              for i, (b, _, _, reason) in enumerate(s.plan_history)]
    sel = st.selectbox("Replan", labels)
    idx = labels.index(sel)
    bucket, before, after, reason = s.plan_history[idx]
    b_ids = {j.id for j in before if j.status not in ("CANCELLED", "REJECTED")}
    a_ids = {j.id for j in after if j.status not in ("CANCELLED", "REJECTED")}
    cancelled = sorted(b_ids - a_ids)
    added = sorted(a_ids - b_ids)
    c = st.columns(4)
    c[0].metric("Trigger", reason.split(":")[0])
    c[1].metric("Jobs cancelled/moved", len(cancelled))
    c[2].metric("Jobs added", len(added))
    c[3].metric("Solve time [s]",
                f"{s.replan_solve_s[idx]:.1f}" if idx < len(s.replan_solve_s) else "—")
    unresolved = [r for r in s.plan.reasons] if s.plan else []
    if unresolved:
        st.warning(
            "Unresolved consequences: "
            + " · ".join(theme.operator_text(r) for r in unresolved))
    left, right = st.columns(2)
    with left:
        plot(gantt_figure(jobs_to_gantt_df(scn, before), color_by="kind",
                          group_by="machine", height=330,
                          title=f"BEFORE (at bucket {bucket})"))
    with right:
        plot(gantt_figure(jobs_to_gantt_df(scn, after), color_by="kind",
                          group_by="machine", height=330, title="AFTER"))
    if cancelled:
        st.caption("cancelled/moved: " + ", ".join(cancelled[:20]))
    if added:
        st.caption("added: " + ", ".join(added[:20]))
