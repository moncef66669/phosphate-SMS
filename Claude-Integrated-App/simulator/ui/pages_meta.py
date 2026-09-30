"""Pages: 11 Three-SMS Integration Console · 12 Assumptions & Data Gaps ·
14 Audit & Export."""
from __future__ import annotations

import dataclasses
import io
import json
import zipfile
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from mph_sim.coordinates import job_repere_label
from mph_sim.kpis import compute_kpis
from mph_sim.persistence import export_excel, scenario_tables, scenario_to_json

from . import common, theme
from .theme import ev_badge, show_df


# ============================================ 11. Three-SMS Integration Console
MODE_META = {  # state dot colour + label (never implies live connectivity)
    "reference": ("#8a949e", "Reference only"),
    "offline-simulated": ("#2f6fb3", "Offline simulated"),
    "configured": ("#b97a14", "Configured (not connected)"),
    "connected": ("#2e7d32", "Connected"),
    "error": ("#b23b3b", "Error / unavailable"),
}

_RULE_ACTIONS = {
    "ParameterSanity": "correct the job parameters before submission",
    "WorkArea": "check the device work-area configuration for this pile",
    "InventoryAvailability": "time-phased dependency or shortage — the pile "
                             "may be filled by stacking planned EARLIER in the "
                             "same horizon; submit in rolling dispatch waves / "
                             "re-evaluate at dispatch time",
    "SameDeviceOverlap": "re-sequence the two jobs on this device",
    "WorkCalendar": "move the job outside the blocked calendar window",
    "DeviceAvailability": "confirm device availability before dispatch",
    "SpatialConflict": "increase machine separation or re-time one job",
    "FIFO": "advisory — older same-grade material available on another pile",
}

_SITE_COVERAGE = {
    "LOUTA": [
        "Modelled stocked route: finite mine handoff → `110LG-ST-01` → "
        "two 110LG ore lanes → `120LG-RC-01/-02` → washing L1/L2 → "
        "`130LG-ST-01` → LT-P1/P2 → `140LG-RC-01/-02` → C1.",
        "The visible S0 policy requests exactly 4,000 t recovered output per "
        "wash line and 8,000 t on C1, with ore/product lanes restored to "
        "opening inventory. Machine-coverage reward is zero.",
        "Washing uses documented Global/Low-Cd feed and recovery values; "
        "line/grade allocation and overall 30-minute residence are explicit "
        "demonstration assumptions. Downstream stacking cannot start before "
        "that dependency.",
        "Conservative reach is one ore/product reclaimer per assumed lane; "
        "shared RB4/RB5, SL11 and C1 confluences prevent simultaneous peers. "
        "The generic BoD lists one ore and one product reclaimer while the "
        "later tagged process description lists two of each.",
        "L01 documents two dedicated `102MG-ST-01/-02` stackers at 2,200 t/h "
        "and one shared `110MG-RC-01` reclaimer. They remain reference-only "
        "until live stock, metric reach and the L01→L02 solver interface are "
        "configured.",
    ],
    "BENGUERIR": [
        "The active model has **six physical piles**: BG-MINE-1/-2 and "
        "BG-RAW-1/-2 at 200,000 t each, plus washed-product BG-P1/P2 at "
        "191,512 t each. The mine-pile capacities and 50% starting fills are "
        "explicit simulation assumptions, not live surveyed inventory.",
        "TM1/TSM2/TSM3 are finite mine-hopper planning interfaces. Each of "
        "the three washing campaigns also has screened and washed WIP "
        "interfaces; these six WIP rows are process buffers, not additional "
        "on-ground stockpiles.",
        "All **nine mobile machines** participate in scheduling: two mine "
        "stackers + one shared mine reclaimer, two screened-pile stackers + "
        "one shared screened-pile reclaimer, and `ST3`/`RC2`/`RC3`. "
        "`ST3`/`RC2`/`RC3` and their B02 rates are confirmed; the new mine "
        "machine identities/reach and reused 2,100/2,500 t/h envelope are "
        "simulation assumptions.",
        "Three selectable washing lines connect the hopper → mine pile → "
        "screened pile → wash → product-pile chain. Campaign allocation, "
        "30-minute dependency, and borrowed feed/recovery cases are exposed "
        "simulation assumptions; disabling a line removes its full staged "
        "route from planning.",
        "Raw and washed train interfaces remain available as editable "
        "demonstration demand (1,000/1,200 t/h documented interface; one "
        "shared loading station assumed). C2 remains the washed-product "
        "route to M16; raw-to-C2 is a separate optional routing.",
    ],
    "MPH": [
        "M16 stacker (`ST01-A/B/C`) and reclaimer (`RC01-A/B`) jobs over the "
        "eight humid stock spaces HP-01…HP-08.",
        "PAP and DRY destination (reclaim/feed) jobs. The PAP receiving line "
        "can be enabled or disabled in Scenario Planning without deleting "
        "its configured demand.",
        "ASP mapping based on the **detailed MPH FRS** "
        "(Q80130-MZI-M16-TAK-45000183621-DG); RabbitMQ integration is not "
        "configured "
        "(H1).",
    ],
}


def _sev_chip(n: int, colour: str, label: str) -> str:
    w = "700" if n else "400"
    return (f'<span style="color:{colour if n else "#8a949e"};'
            f'font-weight:{w}">{n} {label}</span>')


def _queue_df(scn, b, state_rev: str) -> pd.DataFrame:
    from mph_sim.integration import job_domain
    from mph_sim.timing import (VALIDATED, display_execution_window,
                                effective_execution_rate_cap,
                                validated_execution_windows, window_end_dt,
                                window_start_dt)
    parent_by_item = getattr(b, "item_parent_jobs", {})
    err_n, warn_n = {}, {}
    for i in b.validation_issues:
        job_id = parent_by_item.get(i.item_id, i.item_id)
        if i.severity == "ERROR":
            err_n[job_id] = err_n.get(job_id, 0) + 1
        elif i.severity == "WARNING":
            warn_n[job_id] = warn_n.get(job_id, 0) + 1
    items_by = {}
    for item in b.items:
        items_by.setdefault(
            parent_by_item.get(item.JobId, item.JobId), item)
    rows = []
    for j in b.jobs:
        it = items_by.get(j.id)
        val = ("—" if it is None else
               f"ERROR ({err_n[j.id]})" if j.id in err_n else
               f"WARNING ({warn_n[j.id]})" if j.id in warn_n else "OK")
        cfg_warn = ("site master data / interface pending"
                    if b.site_id in ("LOUTA", "BENGUERIR")
                    else "RabbitMQ interface pending (H1)")
        if job_domain(scn, j).startswith("B"):
            cfg_warn = "Domain B — Sulphur (M17) · " + cfg_warn
        validated = validated_execution_windows(j)
        if validated:
            display_rows = [
                (window_start_dt(scn, window),
                 window_end_dt(scn, window), window.rate_tph,
                 window.duration_min, window.tonnage_t, VALIDATED,
                 window.basis, window.solver_start_bucket,
                 window.solver_end_bucket,
                 j.id if len(validated) == 1 else f"{j.id}·W{index}")
                for index, window in enumerate(validated, start=1)
            ]
        else:
            window, timing_status = display_execution_window(scn, j)
            start = scn.sim.bucket_dt(j.start_bucket)
            end = scn.sim.bucket_dt(j.end_bucket + 1)
            display_rows = [(
                start, end, j.rate_tph,
                int(round((end - start).total_seconds() / 60.0)),
                j.tonnage_t, timing_status, window.basis,
                j.start_bucket, j.end_bucket, j.id)]

        for (start, end, rate_tph, duration_min, tonnage_t,
             timing_status, timing_basis, start_bucket, end_bucket,
             row_job_id) in display_rows:
            rows.append({
                "sms_site": b.sms_id, "batch_rev": state_rev,
                "job_id": row_job_id, "parent_job": j.id,
                "device": j.machine, "job_type": it.JobType if it else j.kind,
                "method": it.Method if it else j.method, "pile": j.space,
                "material": it.Material if it else j.grade,
                "grade": j.grade,
                "source_dest": (
                    it.ReferenceLocation if it else (j.dest_id or "—")),
                "start": start.strftime("%d %H:%M"),
                "end": end.strftime("%d %H:%M"),
                "start_bucket": start_bucket,
                "end_bucket": end_bucket,
                "solver_buckets": f"B{start_bucket}–B{end_bucket}",
                "parent_solver_buckets":
                    f"B{j.start_bucket}–B{j.end_bucket}",
                "timing_status": timing_status,
                "timing_basis": timing_basis,
                "duration_min": duration_min,
                "tonnage_t": round(tonnage_t, 1),
                "parent_tonnage_t": round(j.tonnage_t, 1),
                "rate_tph": round(rate_tph),
                "solver_rate_tph": round(j.rate_tph),
                "execution_rate_cap_tph": round(
                    effective_execution_rate_cap(scn, j), 1),
                "repere_range": job_repere_label(scn.space(j.space), j),
                "StartX": j.start_x, "StopX": j.stop_x,
                "internal_status": j.status,
                "integration_state": b.item_states.get(j.id, "—"),
                "validation": val, "config_warning": cfg_warn,
                "_dur_h": round(duration_min / 60.0, 2)})
    return pd.DataFrame(rows)


def _issues_df(b) -> pd.DataFrame:
    sev_order = {"ERROR": 0, "WARNING": 1, "INFO": 2}
    rows = [{"severity": i.severity, "rule": i.rule, "job_id": i.item_id,
             "sms_site": b.sms_id, "message": i.message,
             "suggested_action": _RULE_ACTIONS.get(i.rule, "review")}
            for i in sorted(b.validation_issues,
                            key=lambda i: sev_order.get(i.severity, 9))]
    return pd.DataFrame(rows)


def _fleet_cards(scn, state, sim):
    from mph_sim.integration import (PLANNING_RESPONSIBILITY,
                                     job_updates_for_site,
                                     space_values_for_site)
    cols = st.columns(3)
    for c, site in zip(cols, ("LOUTA", "BENGUERIR", "MPH")):
        cfg, b = state.configs[site], state.batches[site]
        dot, mode_lbl = MODE_META[cfg.connection_mode]
        n = b.issue_counts()
        life = {}
        for v in b.item_states.values():
            life[v] = life.get(v, 0) + 1
        life_txt = " · ".join(f"{v} {k.split(' (')[0]}"
                              for k, v in sorted(life.items())) or "—"
        spaces = space_values_for_site(scn, site, sim)
        ups = job_updates_for_site(scn, sim, site)
        last_up = f"bucket {max(u['bucket'] for u in ups)}" if ups else "—"
        with c, st.container(border=True):
            st.markdown(
                f'<span style="color:{dot};font-size:1.1em">●</span> '
                f'**{cfg.display_name}** &nbsp;'
                f'<span style="color:#4d5b66">{mode_lbl}</span>',
                unsafe_allow_html=True)
            st.caption(PLANNING_RESPONSIBILITY[site])
            st.markdown(
                f"profile `{cfg.adapter_profile}` · endpoint "
                f"{'`' + cfg.endpoint + '`' if cfg.endpoint else '**not configured**'}"
                f"<br>jobs in batch **{len(b.jobs)}** · "
                + _sev_chip(n['ERROR'], '#b23b3b', 'E') + " "
                + _sev_chip(n['WARNING'], '#b97a14', 'W') + " "
                + _sev_chip(n['INFO'], '#2f6fb3', 'I')
                + f"<br>lifecycle: {life_txt}"
                f"<br>spaces exposed **{len(spaces)}** · last JobUpdated "
                f"{last_up} · inventory sync "
                f"{'on view (offline)' if spaces else '—'}",
                unsafe_allow_html=True)
            st.caption(
                theme.operator_text(
                    f"{ev_badge(cfg.evidence.status)} "
                    f"{cfg.evidence.source} — {cfg.evidence.note}"),
                unsafe_allow_html=True)
            if b.note:
                st.info(theme.operator_text(b.note))


def _render_sms_tab(scn, sim, state, site):
    from mph_sim.integration import (job_updates_for_site,
                                     space_values_for_site)
    cfg, b = state.configs[site], state.batches[site]
    if cfg.connection_mode == "reference":
        st.info(theme.operator_text(
            b.note + " The instance is shown because the three-SMS "
            "architecture (identical Innomotics/YardMaster solutions) is "
            "user-confirmed; its jobs/validation appear when a scenario "
            "includes the site."))
        for line in _SITE_COVERAGE[site]:
            st.markdown("- " + line)
        return
    with st.expander("Site coverage / model scope", expanded=False):
        for line in _SITE_COVERAGE[site]:
            st.markdown("- " + line)

    # lifecycle strip — internal vs simulated vs live, never conflated
    st.caption(f"Batch `{b.plan_revision}` · generated {b.generated_at} · "
               f"state **{b.dispatch_state}** — lifecycle: Generated → "
               "Partitioned → Mapped → Submitted *(simulated)* → Evaluated "
               "*(simulated)* → Accepted/Rejected *(simulated)*"
               + (" → Dispatched/Running/Finished *(simulated)*" if sim else
                  " — run the simulation for dispatch/execution states")
               + ". **Actual live plan/execution state: none — submission "
                 "and JobUpdated are not connected.**")
    if not b.jobs:
        st.success("No jobs required for this SMS in the selected horizon.")
        return

    df = _queue_df(scn, b, state.plan_revision)
    f = st.columns([1.2, 1.2, 1, 1, 1.1, 1.6])
    f_status = f[0].multiselect("Status", sorted(df.internal_status.unique()),
                                key=f"int_st_{site}")
    f_mach = f[1].multiselect("Machine", sorted(df.device.unique()),
                              key=f"int_m_{site}")
    f_jt = f[2].multiselect("Job type", sorted(df.job_type.unique()),
                            key=f"int_jt_{site}")
    f_gr = f[3].multiselect("Grade", sorted(df.grade.unique()),
                            key=f"int_g_{site}",
                            format_func=theme.operator_text)
    f_sev = f[4].selectbox("Validation", ["All", "ERROR", "WARNING", "OK"],
                           key=f"int_sev_{site}")
    hmax = max(int(df.start_bucket.max()), 1)
    f_t = f[5].slider("Start-bucket range", 0, hmax, (0, hmax),
                      key=f"int_t_{site}")
    q = df
    if f_status:
        q = q[q.internal_status.isin(f_status)]
    if f_mach:
        q = q[q.device.isin(f_mach)]
    if f_jt:
        q = q[q.job_type.isin(f_jt)]
    if f_gr:
        q = q[q.grade.isin(f_gr)]
    if f_sev != "All":
        q = q[q.validation.str.startswith(f_sev)]
    q = q[(q.start_bucket >= f_t[0]) & (q.start_bucket <= f_t[1])]
    st.markdown(f"##### Job queue — {len(q)} of {len(df)} jobs "
                f"(owner: {b.sms_id} only)")
    show_df(q.drop(columns=["start_bucket", "_dur_h"]), height=280,
            empty_message="no jobs match the current filters")

    if st.button(f"Validate {cfg.display_name} (offline simulated)",
                 key=f"val_{site}"):
        nn = b.issue_counts()
        st.success(f"offline simulated validation re-run for {b.sms_id}: "
                   f"{nn['ERROR']} ERROR · {nn['WARNING']} WARNING · "
                   f"{nn['INFO']} INFO — no telegrams were sent")

    e1 = st.expander(f"1 · Outbound plan items — AspPlanItem[] ({len(b.items)})")
    with e1:
        st.caption(f"target SMS **{b.sms_id}** · batch `{b.plan_revision}` · "
                   f"generated {b.generated_at} · horizon "
                   f"{b.horizon_buckets} buckets. The AspPlanItem wire schema "
                   "carries NO site field — ownership lives in this envelope.")
        show_df(pd.DataFrame([dataclasses.asdict(i) for i in b.items]),
                height=240)
    with st.expander(f"2 · Evaluation response — simulated "
                     f"AspEvaluatePlanResponse ({len(b.validation_issues)})"):
        idf = _issues_df(b)
        show_df(idf, height=220,
                empty_message="no issues raised for this SMS instance")
    with st.expander("3 · Job execution feedback — JobUpdated (simulated)"):
        ups = job_updates_for_site(scn, sim, site)
        show_df(pd.DataFrame(ups), height=220,
                empty_message="run the simulation (page 7) to receive "
                              "simulated JobUpdated telegrams")
    with st.expander("4 · Inventory feedback — SpaceValueRequest/Response "
                     f"({site} spaces only)"):
        show_df(pd.DataFrame(space_values_for_site(scn, site, sim)),
                height=220, empty_message="no stock spaces owned by this SMS")


def _fresh_live_health(item: dict, ttl_s: float = 300.0) -> bool:
    if item.get("mode") != "reachable" or not item.get("reachable"):
        return False
    try:
        checked = datetime.fromisoformat(str(item["checked_at"]))
        if checked.tzinfo is None:
            return False
        return (datetime.now(timezone.utc) - checked.astimezone(
            timezone.utc)).total_seconds() <= ttl_s
    except (KeyError, TypeError, ValueError):
        return False


def _render_live_inventory_panel(scn):
    """Safe read-only health/fetch UI; apply remains on inventory page 3."""
    from mph_sim.integration import MultiSmsInventoryRegistry
    from mph_sim.rabbitmq_inventory import (
        live_inventory_config_template, load_live_inventory_configs,
    )

    bundle = load_live_inventory_configs()
    ss = st.session_state
    if ss.get("sms_live_config_revision", "") != bundle.revision_hash:
        common.clear_live_inventory_preview()
        ss.sms_live_health_by_site = {}
        ss.sms_live_pull_last_issues = []
        ss.sms_live_config_revision = bundle.revision_hash
    registry = MultiSmsInventoryRegistry(scn, bundle=bundle)
    readiness = registry.readiness()

    with st.container(border=True):
        st.markdown("#### Live SMS inventory — read-only commissioning path")
        st.caption(
            "This capability only requests `SpaceValueResponse` inventory. "
            "It cannot submit plans or control equipment. Credentials are read "
            "from environment-variable references and are never entered here, "
            "stored in the scenario, or included in exports.")
        c0, c1 = st.columns([1, 2.3])
        c0.download_button(
            "Connector config template", live_inventory_config_template(),
            "live_inventory_config.example.json", mime="application/json")
        c1.caption(
            "Set `MPH_SMS_LIVE_INVENTORY_CONFIG_PATH` to the approved, "
            "secret-free ICD profile. Install `requirements-rabbitmq.txt`; "
            "the profile then references one protected AMQP URL environment "
            "variable per site.")

        if bundle.issues:
            for issue in bundle.issues:
                st.error(issue)
        rows = []
        health_state = dict(ss.get("sms_live_health_by_site", {}))
        for site, item in readiness.items():
            health = health_state.get(site, {})
            mode = health.get("mode", item.get("mode", "not-configured"))
            detail = (health.get("detail") or
                      "; ".join(item.get("missing_or_invalid", [])) or "ready")
            rows.append({
                "site": site,
                "config": "ready" if item.get("ready") else "not ready",
                "live health": mode,
                "mapped piles": item.get("mapped_piles", 0),
                "segment payload": item.get("records_are_segments", False),
                "profile revision": item.get("profile_revision") or "—",
                "detail": detail,
            })
        show_df(pd.DataFrame(rows), height=170)

        loaded_sites = sorted(readiness)
        all_ready = not bundle.issues and bool(loaded_sites) and all(
            readiness[s].get("ready") for s in loaded_sites)
        fresh_reachable = all(
            _fresh_live_health(health_state.get(s, {})) for s in loaded_sites)
        controls = st.columns([1, 1, 1, 1.2])
        probe_timeout = controls[0].number_input(
            "Probe timeout [s]", 0.5, 10.0, 2.0, 0.5,
            key="live_probe_timeout")
        max_age_min = controls[1].number_input(
            "Max source age [min]", 1.0, 120.0, 30.0, 1.0,
            key="live_max_age_min")
        max_skew_s = controls[2].number_input(
            "Max site skew [s]", 0.0, 600.0, 60.0, 5.0,
            key="live_max_site_skew")
        solving = ss.get("solving", False) or ss.get("solve_request", False)
        if controls[3].button(
                "Probe live endpoints", disabled=not all_ready or solving,
                use_container_width=True):
            with st.spinner("Checking read-only RabbitMQ endpoints…"):
                ss.sms_live_health_by_site = registry.probe(
                    timeout_s=float(probe_timeout))
            st.rerun()

        if not bundle.configs:
            st.info(
                "Live inventory is not configured. The simulator remains "
                "offline; file-based snapshot import on page 3 is available.")
        elif not all_ready:
            st.warning(
                "Live fetch is disabled until every loaded site has a complete "
                "ICD profile, pile mapping, TLS/connection reference and secret "
                "environment variable.")
        elif not fresh_reachable:
            st.warning(
                "Probe every loaded endpoint successfully before fetching. A "
                "reachability result expires after 5 minutes.")

        fetch_disabled = (not all_ready or not fresh_reachable or solving or
                          ss.get("sms_live_pull_in_progress", False))
        if st.button(
                "Fetch all loaded SpaceValues (read-only)",
                type="primary", disabled=fetch_disabled,
                use_container_width=True):
            ss.sms_live_pull_in_progress = True
            try:
                with st.spinner("Requesting and validating all site inventories…"):
                    result = registry.pull(
                        max_site_skew_seconds=float(max_skew_s),
                        max_age_hours=float(max_age_min) / 60.0,
                        require_complete=True)
                ss.sms_live_pull_last_issues = [dataclasses.asdict(i)
                                                for i in result.issues]
                if common.store_live_inventory_preview(
                        result, config_revision=bundle.revision_hash):
                    st.success(
                        "Live inventory fetched and normalized. No scenario "
                        "value has changed; review and explicitly apply it on "
                        "Stockyards & Inventory.")
                else:
                    common.clear_live_inventory_preview()
                    st.error("Live pull was blocked; the loaded scenario is unchanged.")
            finally:
                ss.sms_live_pull_in_progress = False

        last_issues = ss.get("sms_live_pull_last_issues", [])
        if last_issues:
            show_df(pd.DataFrame(last_issues), height=180)
        preview = ss.get("sms_live_pull_preview")
        if preview:
            snap = preview.get("snapshot")
            st.success(
                f"Preview ready · sites {', '.join(preview.get('requested_sites', []))} "
                f"· captured {snap.captured_at.isoformat() if snap else '—'} · "
                "not yet applied")
            if st.button("Review live snapshot on Stockyards & Inventory"):
                common.goto_page("3 · Stockyards & Inventory")
                st.rerun()


def page_asp():
    from mph_sim.integration import build_sms_batches, default_sms_configs
    st.subheader("Three-SMS Integration Console")
    st.caption("Louta · Benguerir · MPH — common Innomotics/YardMaster ASP "
               "profile")
    st.warning("**User-confirmed architecture**: Louta, Benguerir and MPH use "
               "identical Innomotics/YardMaster SMS solutions — same ASP "
               "job-planning concept and plan-item/validation workflow, "
               "deployed as **three independent SMS instances**. The MPH FRS "
               "defines the common interface profile; its application to the "
               "two identical mine-SMS deployments is confirmed by the user. "
               "Site-specific items still pending: endpoints/vhosts/"
               "credentials, "
               "exchange/queue/routing keys, Device/Pile IDs, Material/Grade/"
               "SubGrade mappings, AspRoutes, work calendars, min machine "
               "distance, telegram namespaces/version, permissions. Plan "
               "submission and JobUpdated below remain **offline simulated**; "
               "the separate live-inventory panel is read-only and reports "
               "reachability only after a real probe.")
    scn, p, s = common.sc(), common.plan(), common.sim()

    _render_live_inventory_panel(scn)

    c1, c2 = st.columns(2)
    min_d = c1.number_input(
        "Spatial min distance [m] (UC-28, configurable site parameter)",
        value=50.0, min_value=0.0)
    cpo = c2.toggle("CurrentPileOnly (spatial rule applies on same pile only)",
                    value=True)
    state = build_sms_batches(scn, p, s, min_distance_m=min_d,
                              current_pile_only=cpo)
    _fleet_cards(scn, state, s)
    if p is None:
        st.info("Generate S0 with the sidebar **Generate Plan** action — the three SMS "
                "instances above stay visible; batches populate once a plan "
                "exists.")
        return

    tabs = st.tabs(["All SMS", "Louta SMS", "Benguerir SMS", "MPH SMS",
                    f"Unmapped / errors ({len(state.unmapped)})"])
    with tabs[0]:
        agg = []
        for site in ("LOUTA", "BENGUERIR", "MPH"):
            b = state.batches[site]
            n = b.issue_counts()
            agg.append({"sms_site": b.sms_id, "mode":
                        MODE_META[state.configs[site].connection_mode][1],
                        "jobs": len(b.jobs), "ERROR": n["ERROR"],
                        "WARNING": n["WARNING"], "INFO": n["INFO"],
                        "dispatch_state": b.dispatch_state})
        st.markdown("##### Aggregated dispatch summary — ownership never "
                    "mixed")
        show_df(pd.DataFrame(agg))
        tot = sum(a["jobs"] for a in agg)
        st.caption(f"total {tot} jobs partitioned · "
                   f"{len(state.unmapped)} unmapped · batch "
                   f"`{state.plan_revision}` · every job has exactly one SMS "
                   "owner (Job.machine → Machine.site, cross-checked against "
                   "the pile's site)")
        if st.button("Validate all three SMS (offline simulated)"):
            for site in ("LOUTA", "BENGUERIR", "MPH"):
                nn = state.batches[site].issue_counts()
                st.success(f"{state.batches[site].sms_id}: {nn['ERROR']} "
                           f"ERROR · {nn['WARNING']} WARNING · {nn['INFO']} "
                           f"INFO — offline simulated, no telegrams sent")
        rows = []
        for site in ("LOUTA", "BENGUERIR", "MPH"):
            b = state.batches[site]
            for i in b.items:
                r = {"sms_site": b.sms_id}
                r.update(dataclasses.asdict(i))
                rows.append(r)
        with st.expander(f"Aggregate plan items with explicit `sms_site` "
                         f"column ({len(rows)})"):
            show_df(pd.DataFrame(rows), height=240)
    for tab, site in zip(tabs[1:4], ("LOUTA", "BENGUERIR", "MPH")):
        with tab:
            _render_sms_tab(scn, s, state, site)
    with tabs[4]:
        if state.unmapped:
            st.error(f"{len(state.unmapped)} job(s) could not be assigned to "
                     "exactly one SMS — they are NEVER silently assigned to "
                     "MPH and are excluded from all batches.")
            show_df(pd.DataFrame(
                [{"severity": "ERROR", "job_id": j.id, "machine": j.machine,
                  "pile": j.space, "kind": j.kind, "reason": r}
                 for j, r in state.unmapped]))
        else:
            st.success("No unmapped jobs — every generated job has exactly "
                       "one SMS owner and no job was lost or duplicated "
                       "during partitioning.")
    st.caption("Rule set per FRS (common profile): **ERROR** = ParameterSanity, "
               "WorkArea, InventoryAvailability, SameDeviceOverlap, "
               "WorkCalendar · **WARNING** = DeviceAvailability, "
               "SpatialConflict · **INFO** = FIFO. Each SMS is validated "
               "independently against its own machines, piles, segments and "
               "calendars — FIFO/spatial checks never cross sites. One ASP "
               "job = one material grade; multi-source SQP blend compliance "
               "is owned by this layer (report §15.7).")


# ================================================= 12. Assumptions & Data Gaps
def page_assumptions():
    st.subheader("Assumptions & Data Gaps")
    st.caption("Live register — unresolved data is shown, not hidden. Fields: "
               "assumption · module · status · source · confidence · current "
               "placeholder · simulation impact · owner · action.")
    scn = common.sc()
    rows = [{"id": a.id, "assumption": a.text, "module": a.module,
             "status": a.status, "source": a.source, "confidence": a.confidence,
             "placeholder": a.placeholder, "impact": a.impact,
             "owner": a.owner, "action": a.action} for a in scn.assumptions]
    df = pd.DataFrame(rows)
    ed = st.data_editor(
        theme.operator_frame(df), num_rows="dynamic",
        height=380, key="assump_ed")
    import_ed = theme.restore_operator_frame(ed, df)
    if st.button("Apply register edits"):
        from mph_sim.domain import Assumption
        scn.assumptions = [Assumption(
            id=str(r["id"]), text=str(r["assumption"]), module=str(r["module"]),
            status=str(r["status"] or "OPEN"), source=str(r["source"] or "—"),
            confidence=str(r["confidence"] or "L"),
            placeholder=str(r["placeholder"] or ""),
            impact=str(r["impact"] or ""), owner=str(r["owner"] or ""),
            action=str(r["action"] or "")) for _, r in import_ed.iterrows()
            if str(r["id"]).strip()]
        st.success("applied")

    st.markdown("#### Evidence status of dataset entities")
    ent_rows = []
    for kind, items in [("space", scn.spaces), ("machine", scn.machines),
                        ("link", scn.links), ("destination", scn.destinations),
                        ("grade", scn.grades), ("site", scn.sites)]:
        for it in items:
            ent_rows.append({"entity": f"{kind}:{it.id}",
                             "status": it.evidence.status,
                             "confidence": it.evidence.confidence,
                             "source": it.evidence.source,
                             "note": it.evidence.note})
    edf = pd.DataFrame(ent_rows)
    counts = edf["status"].value_counts()
    st.markdown(" ".join(f"{ev_badge(k)} {v}" for k, v in counts.items()),
                unsafe_allow_html=True)
    internal_statuses = sorted(str(s_) for s_ in edf.status.unique())
    display_statuses = sorted({
        theme.operator_text(s_).upper() for s_ in internal_statuses
    })
    default_display = [
        theme.operator_text(s_).upper()
        for s_ in ("ASSUMED", "TBC")
        if s_ in set(internal_statuses)
    ]
    selected_display = st.multiselect(
        "Filter status",
        display_statuses,
        default=default_display,
    )
    selected_internal = [
        s_ for s_ in internal_statuses
        if theme.operator_text(s_).upper() in selected_display
    ]
    show_df(
        edf[edf.status.isin(selected_internal)] if selected_display else edf,
        height=300,
    )


# ================================================= 14. Audit & Export
def integration_zip_entries(scn, p, s) -> dict:
    """Per-SMS integration export tree (audit trail: which SMS received each
    item, when it was generated, mapping/validation/submission results and
    lifecycle state) + aggregate plan-item file with a mandatory `sms_site`
    column. Pure — returns {zip_name: payload_str}."""
    from mph_sim.adapters import AspIssue, AspPlanItem
    from mph_sim.integration import (build_sms_batches, job_updates_for_site,
                                     space_values_for_site)
    from mph_sim.timing import (BUCKET_ONLY, VALIDATED, timing_export_rows,
                                validated_execution_windows)
    ist = build_sms_batches(scn, p, s)
    item_columns = [
        "parent_job_id", "solver_buckets", "timing_status",
        *[field.name for field in dataclasses.fields(AspPlanItem)],
    ]
    issue_columns = [
        field.name for field in dataclasses.fields(AspIssue)
    ]
    timing_columns = [
        "job_id", "window", "solver_buckets", "solver_start_bucket",
        "solver_end_bucket", "start_minute", "duration_min", "end_minute",
        "tonnage_t", "rate_tph", "execution_rate_cap_tph", "timing_basis",
        "timing_status", "note", "positioning_start_minute",
        "positioning_duration_min", "start_time", "end_time",
    ]
    out, agg_rows, agg_timing_rows, summary = {}, [], [], []
    for site, b in ist.batches.items():
        n = b.issue_counts()
        summary.append({
            "sms_site": b.sms_id, "site_id": site,
            "mode": ist.configs[site].connection_mode,
            "plan_revision": b.plan_revision,
            "generated_at": b.generated_at,
            "jobs": len(b.jobs), "plan_items": len(b.items),
            "errors": n["ERROR"], "warnings": n["WARNING"], "info": n["INFO"],
            "lifecycle_state": b.dispatch_state,
            "submission": "offline simulated — no live plan submission",
            "note": b.note})
        out[f"integration/{site}/plan_batch.json"] = json.dumps({
            "sms_site": b.sms_id, "site_id": site,
            "plan_revision": b.plan_revision,
            "generated_at": b.generated_at,
            "horizon_buckets": b.horizon_buckets,
            "dispatch_state": b.dispatch_state,
            "item_states": b.item_states,
            "item_parent_jobs": b.item_parent_jobs,
            "timing_policy": (
                "minute timestamps/rates are emitted only from fully "
                "VALIDATED execution windows; unresolved exact-profile jobs "
                "emit no ASP item and require replanning"),
            "items": [dataclasses.asdict(i) for i in b.items],
        }, indent=1, default=str)
        jobs_by_id = {job.id: job for job in b.jobs}
        site_item_rows = []
        for item in b.items:
            parent_id = b.item_parent_jobs.get(item.JobId, item.JobId)
            parent = jobs_by_id.get(parent_id)
            validated = validated_execution_windows(parent) \
                if parent is not None else []
            timing_status = VALIDATED if validated else (
                parent.execution_windows[0].status
                if parent is not None and parent.execution_windows
                else BUCKET_ONLY)
            row = {
                "parent_job_id": parent_id,
                "solver_buckets": (
                    f"B{parent.start_bucket}–B{parent.end_bucket}"
                    if parent is not None else ""),
                "timing_status": timing_status,
            }
            row.update(dataclasses.asdict(item))
            site_item_rows.append(row)
            agg_rows.append({"sms_site": b.sms_id, **row})
        out[f"integration/{site}/asp_plan_items.csv"] = pd.DataFrame(
            site_item_rows, columns=item_columns).to_csv(index=False)
        site_timing_rows = timing_export_rows(b.jobs, scn)
        out[f"integration/{site}/execution_windows.csv"] = pd.DataFrame(
            site_timing_rows, columns=timing_columns).to_csv(index=False)
        agg_timing_rows.extend(
            {"sms_site": b.sms_id, **row} for row in site_timing_rows)
        out[f"integration/{site}/validation_issues.csv"] = pd.DataFrame(
            [dataclasses.asdict(i) for i in b.validation_issues],
            columns=issue_columns) \
            .to_csv(index=False)
        out[f"integration/{site}/job_updates.csv"] = pd.DataFrame(
            job_updates_for_site(scn, s, site)).to_csv(index=False)
        out[f"integration/{site}/space_values.csv"] = pd.DataFrame(
            space_values_for_site(scn, site, s)).to_csv(index=False)
    out["integration/summary.csv"] = pd.DataFrame(summary) \
        .to_csv(index=False)
    out["integration/unmapped_jobs.csv"] = pd.DataFrame(
        [{"severity": "ERROR", "job_id": j.id, "machine": j.machine,
          "pile": j.space, "reason": r} for j, r in ist.unmapped]) \
        .to_csv(index=False)
    # aggregate retained for convenience — sms_site is mandatory
    out["asp_plan_items.csv"] = pd.DataFrame(
        agg_rows, columns=["sms_site", *item_columns]).to_csv(index=False)
    out["execution_windows.csv"] = pd.DataFrame(
        agg_timing_rows,
        columns=["sms_site", *timing_columns]).to_csv(index=False)
    return out


def page_audit():
    st.subheader("Audit & Export")
    scn, p, s = common.sc(), common.plan(), common.sim()

    st.markdown("#### Run history (SQLite `runs.db`)")
    hist = st.session_state.run_store.history()
    show_df(hist, height=220)

    st.markdown("#### Deterministic replay check (acceptance criterion #11)")
    st.caption("Re-solves the current scenario twice with the stored seed and "
               "deterministic solver settings and compares the job plans.")
    if st.button("Verify deterministic replay"):
        from mph_sim.optimizer import SolveOptions, solve_plan
        with st.spinner("solving twice…"):
            r1 = solve_plan(scn, SolveOptions(max_time_s=min(scn.sim.max_solve_s, 6)))
            r2 = solve_plan(scn, SolveOptions(max_time_s=min(scn.sim.max_solve_s, 6)))
        sig = lambda r: [(j.machine, j.space, j.dest_id, j.start_bucket,
                          j.end_bucket, j.tonnage_t) for j in r.jobs]
        if sig(r1) == sig(r2):
            st.success(f"identical plans reproduced ({len(r1.jobs)} jobs) ✓")
        else:
            st.error("plans differ — determinism broken (please report)")

    st.markdown("#### Human overrides in this session")
    if st.session_state.overrides_log:
        show_df(pd.DataFrame(st.session_state.overrides_log))
    else:
        st.caption("none recorded")

    st.markdown("#### Export bundle")
    st.caption("Complete scenario inputs · generated jobs · event log · KPI "
               "summary · validation issues · assumptions · run metadata · "
               "human overrides · per-SMS integration tree (Louta / "
               "Benguerir / MPH) — one ZIP.")
    if st.button("Build export bundle (ZIP)"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("scenario.json", scenario_to_json(scn))
            for name, df in scenario_tables(scn).items():
                z.writestr(f"tables/{name}.csv", df.to_csv(index=False))
            if p:
                jobs = list(s.jobs.values()) if s else p.jobs
                z.writestr("jobs.csv", pd.DataFrame(
                    [dataclasses.asdict(j) for j in jobs]).to_csv(index=False))
                for name, payload in integration_zip_entries(scn, p, s).items():
                    z.writestr(name, payload)
                k = compute_kpis(scn, p, s)
                z.writestr("kpi_summary.json", json.dumps(
                    {kk: vv for kk, vv in k.items()
                     if not isinstance(vv, (list, dict))}, indent=1))
                z.writestr("kpi_full.json", json.dumps(k, indent=1, default=str))
            if s:
                z.writestr("event_log.csv", pd.DataFrame(
                    [{"bucket": r.bucket, "kind": r.kind, "asset": r.asset,
                      "message": r.message} for r in s.log]).to_csv(index=False))
                z.writestr("job_updates.csv", pd.DataFrame(
                    [dataclasses.asdict(u) for u in s.job_updates])
                    .to_csv(index=False))
            z.writestr("validation_issues.csv", pd.DataFrame(
                [{"severity": i.severity, "code": i.code, "entity": i.entity,
                  "message": i.message} for i in st.session_state.issues])
                .to_csv(index=False))
            z.writestr("assumptions.csv", pd.DataFrame(
                [dataclasses.asdict(a) for a in scn.assumptions])
                .to_csv(index=False))
            z.writestr("overrides.json",
                       json.dumps(st.session_state.overrides_log, indent=1))
            z.writestr("run_metadata.json", json.dumps({
                "scenario": st.session_state.scenario_name,
                "seed": scn.sim.seed, "dt_h": scn.sim.dt_h,
                "horizon": scn.sim.horizon_buckets,
                "solver": (p.solver if p else None),
                "plan_status": (p.status if p else None),
                "solver_wall_s": (p.solver_wall_s if p else None),
                "replans": (s.replans if s else 0),
                "app": ("mph_sim MVP 0.1.0 — offline by default; optional "
                        "read-only inventory connector; no live control"),
            }, indent=1))
        st.download_button("⬇ download bundle.zip", buf.getvalue(),
                           "mph_sim_export.zip")
    st.download_button("⬇ scenario JSON (deterministic replay snapshot)",
                       scenario_to_json(scn), "scenario.json")
    st.download_button("⬇ scenario XLSX (all tables)", export_excel(scn),
                       "scenario.xlsx")
    up = st.file_uploader("Import scenario JSON", type=["json"])
    if up is not None:
        from mph_sim.persistence import scenario_from_json
        try:
            obj = scenario_from_json(up.read().decode("utf-8"))
            common.load_scenario(f"imported: {obj.name}", obj)
            st.success(f"loaded scenario '{obj.name}' — pages now use it")
        except Exception as e:
            st.error(f"import failed: {e}")
