"""Phosphate value-chain job-allocation & planning simulator — Streamlit entry.

Domain A is a three-site system (see the current report revision): the Louta
and Benguerir mine
stockyards feed the MPH M16 receiving hub through dedicated corridors C1/C2;
M17 sulphur is the separate Domain B on the same platform.

Engineering MVP. Runs offline by default on configurable datasets + simulated
feedback. An optional read-only RabbitMQ SpaceValue connector is implemented
but remains uncommissioned until the site ICD/configuration is supplied; plan
submission and equipment control are not live. Values marked ASSUMED/TBC are
placeholders, not engineering facts.

UX model:
  * the top domain bar exposes BG + LO + MPH; MS is a clearly disabled future
    workspace and never swaps the loaded model;
  * Plan → S0 is the operator-facing three-site planning scenario. S0 maps to
    the established S3 dataset without renaming solver or persisted entities;
  * S0 exposes explicit Balanced, BG train and Full-flow demand scenarios;
  * the sidebar owns the single Generate Plan action, which queues a staged
    solve (validate → build model → warm start → CP-SAT → results);
  * a failed solve keeps the previous plan;
  * navigation = Configure → Plan → Monitor, with legacy specialist tools
    retained under Advanced (?page=N deep links still work;
    ?scn=s1|s2|s3 pre-loads a scenario).

Run:  streamlit run app.py
"""
import time

import streamlit as st

st.set_page_config(page_title="Phosphate Value-Chain Job-Allocation Simulator",
                   page_icon="🏗️", layout="wide")

from mph_sim import datasets                      # noqa: E402
from ui import common, theme
from ui.report_ref import report_ref                      # noqa: E402
from ui import (pages_data, pages_meta, pages_monitor, pages_overview,  # noqa: E402
                pages_planning, process_mimic)

common.init_state()
theme.inject_css()
ss = st.session_state

PAGES = {
    "1 · Executive Overview": pages_monitor.page_kpi_flow_schedule,
    "2 · Input Data": pages_data.page_configure,
    "3 · Stockyards & Inventory": pages_data.page_configure,
    "4 · Routes & Equipment": pages_data.page_configure,
    "5 · Demand & Quality": pages_data.page_demand,
    "6 · Planning Workspace": pages_planning.page_planning,
    "7 · Simulation Control": pages_planning.page_simulation,
    "8 · Results & KPIs": pages_overview.page_results,
    "9 · Job Schedule": pages_monitor.page_kpi_flow_schedule,
    "10 · Exceptions & Replanning": pages_planning.page_exceptions,
    "11 · Three-SMS Integration Console": pages_meta.page_asp,
    "12 · Assumptions & Data Gaps": pages_meta.page_assumptions,
    "13 · Scenario Comparison": pages_overview.page_compare,
    "14 · Audit & Export": pages_meta.page_audit,
    "15 · Global Stockyard Layout & Flow": pages_monitor.page_kpi_flow_schedule,
    "16 · Machine Coverage": pages_monitor.page_machine_coverage,
    "17 · Grade Profiles": pages_data.page_grade_profiles,
}
assert set(PAGES) == set(common.ALL_PAGE_LABELS), "nav model out of sync"

# -------- demo mode (?demo=1|s1|s2|s3) — used for visual QA screenshots -------
@st.cache_resource(show_spinner="preparing demo bundle (one-off solve)…")
def _demo_bundle(which: str, data_revision: str):
    # ``data_revision`` participates in Streamlit's cache key so a corrected
    # built-in scenario cannot be served from an older cached demo bundle.
    del data_revision
    import copy as _copy
    from mph_sim.domain import SimEvent
    from mph_sim.kpis import compute_kpis
    from mph_sim.optimizer import SolveOptions, solve_plan
    from mph_sim.simulator import Simulator
    from mph_sim.validation import validate_scenario
    if which == "s1":
        sc0 = datasets.sulphur_minimal()
        events = [SimEvent("DM1", "reduced_rate", 5, "M17-RC01",
                           magnitude=0.6, duration_buckets=3)]
        auto = True
    elif which == "s3":
        sc0 = datasets.end_to_end()
        events = []           # keep the S3 demo replan-free (solves are heavy)
        auto = False
    elif which == "s3w":      # S3 + warning showcase (below safety stock)
        sc0 = datasets.end_to_end()
        events = [SimEvent("DMW", "inventory_discrepancy", 4, "HP-02",
                           magnitude=-80_000)]
        auto = False          # replan-free: pure state showcase for QA
    elif which == "s3u":      # S3 + one corrupted job -> unmapped-queue QA demo
        sc0 = datasets.end_to_end()
        p0 = solve_plan(sc0, SolveOptions(max_time_s=8))
        p0.jobs[0].machine = "GHOST-RC-99"   # unknown machine: no SMS owner
        p0.jobs[0].comment = "QA showcase — deliberately unmappable job"
        from mph_sim.kpis import compute_kpis as _ck
        k0 = _ck(sc0, p0, None)
        return sc0, p0, None, validate_scenario(sc0), k0, k0
    elif which in ("s3b02off", "s3raw", "s3train"):
        # Benguerir B02 QA showcases (19-Jul-2026):
        #   s3b02off — non-baseline SENSITIVITY: B02 current configuration
        #              disabled — RC2/RC3/ST3 reference only, no jobs;
        #   s3raw    — conditional raw-ore -> C2 route ENABLED (prerequisite
        #              assumed met for the demo);
        #   s3train  — washed-product train demand > 0 (demo value) so the
        #              product train route is ACTIVE and competes with C2.
        sc0 = datasets.end_to_end(
            ben_b02_enabled=(which != "s3b02off"),
            ben_raw_to_c2=(which == "s3raw"))
        if which == "s3train":
            for d in sc0.destinations:
                if d.id == "BG-TRAIN-PROD":
                    d.demand_default_tph = 800     # QA demo value — scope TBC
        p0 = solve_plan(sc0, SolveOptions(max_time_s=8))
        from mph_sim.kpis import compute_kpis as _ck
        k0 = _ck(sc0, p0, None)
        return sc0, p0, None, validate_scenario(sc0), k0, k0
    elif which == "s3rc2":
        # QA showcase: 140LG-RC-01 in maintenance for the whole horizon ->
        # the plan selects the ALTERNATIVE C1 feeder 140LG-RC-02 (either
        # product reclaimer may feed C1; the second is availability, not
        # extra corridor throughput). Also doubles as the maintenance/outage
        # state capture.
        from mph_sim.domain import CalendarEntry
        sc0 = datasets.end_to_end()
        sc0.calendars.append(CalendarEntry(
            "140LG-RC-01", 0, sc0.sim.horizon_buckets - 1,
            "maintenance (QA showcase — RC-02 feeder selection)"))
        p0 = solve_plan(sc0, SolveOptions(max_time_s=8))
        from mph_sim.kpis import compute_kpis as _ck
        k0 = _ck(sc0, p0, None)
        return sc0, p0, None, validate_scenario(sc0), k0, k0
    elif which in ("s3cw", "s3cb"):
        # QA showcase: same-pile concurrency with the stack work zone pushed
        # toward the reclaim face -> warning (s3cw) / violation (s3cb).
        # Display-data mutation only — never presented as a solver output.
        sc0 = datasets.end_to_end()
        p0 = solve_plan(sc0, SolveOptions(max_time_s=3))
        # warning band: min_sep (50) <= sep < min_sep + 2*buffer (100)
        target_sep = 75.0 if which == "s3cw" else 25.0
        done = False
        for t in range(sc0.sim.horizon_buckets):
            if done:
                break
            by_sp = {}
            for j in p0.jobs:
                if j.start_bucket <= t <= j.end_bucket:
                    by_sp.setdefault(j.space, []).append(j)
            for spid, js in sorted(by_sp.items()):
                st_j = [j for j in js if j.kind == "stack"]
                ot_j = [j for j in js if j.kind in ("reclaim", "dispatch")]
                if st_j and ot_j:
                    face = max(j.stop_x for j in ot_j)
                    for j in st_j:
                        w = j.stop_x - j.start_x
                        j.start_x = round(face + target_sep, 1)
                        j.stop_x = round(j.start_x + w, 1)
                        j.comment += " · QA showcase (zone shifted)"
                    done = True
                    break
        from mph_sim.kpis import compute_kpis as _ck
        k0 = _ck(sc0, p0, None)
        return sc0, p0, None, validate_scenario(sc0), k0, k0
    else:
        sc0 = datasets.mph_phosphate()
        events = [SimEvent("DM1", "machine_failure", 6, "RC01-A",
                           duration_buckets=3)]
        auto = True
    p0 = solve_plan(sc0, SolveOptions(max_time_s=8))
    s0 = Simulator(_copy.deepcopy(sc0), _copy.deepcopy(p0), events=events,
                   auto_replan=auto)
    for _ in range(10):
        s0.step()
    k_plan = compute_kpis(sc0, p0, None)
    k_sim = compute_kpis(sc0, p0, s0)
    return sc0, p0, s0, validate_scenario(sc0), k_plan, k_sim


_demo = st.query_params.get("demo")
if _demo in ("1", "s1", "s2", "s3", "s3w", "s3u", "s3cw", "s3cb",
             "s3rc2", "s3b02off", "s3raw", "s3train") and ss.plan is None:
    import copy as _copy
    sc0, p0, s0, iss0, k_plan, k_sim = _demo_bundle(
        "s2" if _demo == "1" else _demo, datasets.S3_BASELINE_REVISION)
    ss.scenario = _copy.deepcopy(sc0)
    ss.scenario_name = sc0.name
    ss.plan = _copy.deepcopy(p0)
    ss.sim = _copy.deepcopy(s0)
    ss.issues = list(iss0)
    ss.compare = {
        "baseline (plan)": {k: v for k, v in k_plan.items()
                            if isinstance(v, (int, float, str))},
        "with demo event (sim)": {k: v for k, v in k_sim.items()
                                  if isinstance(v, (int, float, str))},
    }

# ----- ?scn=s0|s1|s2|s3: pre-load a scenario (LOAD only, never solve) ---------
_scn = st.query_params.get("scn")
if _scn and not ss.get("_scn_qp_done"):
    ss._scn_qp_done = True
    if str(_scn).lower() == "s0":
        target = common.planning_scenario_dataset("S0")
    else:
        target = next((n for n in datasets.BUILTIN_SCENARIOS
                       if n.lower().startswith(str(_scn).lower())), None)
    if target and target != ss.scenario_name:
        common.load_scenario(target)

# -------- navigation state (init once from ?page=N) ----------------------------
# Sanitize retained Monitor/Plan/Configure/Integrate/Govern widget values
# before mounting the revised controls.
if ss.get("nav_section") not in common.VISIBLE_NAV_GROUPS:
    lbl = common.page_label_from_qp(st.query_params.get("page"))
    if lbl is None:
        lbl = (common.NO_PLAN_LANDING_PAGE if ss.plan is None
               else common.PLAN_LANDING_PAGE)
    grp = common.PAGE_TO_GROUP[lbl]
    ss.nav_section = grp
    ss[f"nav_page_{grp}"] = common.visible_page_label(lbl)

for _grp, _pages in common.VISIBLE_NAV_GROUPS.items():
    _key = f"nav_page_{_grp}"
    if ss.get(_key) not in _pages:
        ss[_key] = _pages[0]

# Page buttons queue navigation because the keyed sidebar radios cannot be
# mutated after they have been instantiated in the current Streamlit run.
_pending_page = ss.pop("_pending_page", None)
if _pending_page in common.PAGE_TO_GROUP:
    _pending_group = common.PAGE_TO_GROUP[_pending_page]
    ss.nav_section = _pending_group
    ss[f"nav_page_{_pending_group}"] = common.visible_page_label(
        _pending_page)

# ================================ SIDEBAR =====================================
with st.sidebar:
    st.markdown(
        '<div class="app-brand">🏗️ <b>Phosphate Value-Chain '
        'Job-Allocation Simulator</b><br>'
        '<span>Louta & Benguerir Mine Stockyards → MPH M16 Hub · M17 Sulphur</span><br>'
        '<span>engineering MVP · offline by default · no live control</span>'
        '</div>', unsafe_allow_html=True)

    sec = st.radio("Section", list(common.VISIBLE_NAV_GROUPS),
                   key="nav_section", horizontal=True)
    page = st.radio("Page", common.VISIBLE_NAV_GROUPS[sec],
                    key=f"nav_page_{sec}", format_func=common.page_title)
    try:
        st.query_params["page"] = page.split(" ·")[0]
    except Exception:
        pass

    st.divider()
    scn = common.sc()
    _scenario_code = (
        common.planning_scenario_code(ss.scenario_name)
        or ss.scenario_name.split(" —")[0]
    )
    _s0_loaded = (
        ss.scenario_name
        == common.planning_scenario_dataset(
            common.DEFAULT_PLANNING_SCENARIO)
    )
    plan_state = ("no plan yet" if ss.plan is None
                  else f"plan {ss.plan.status}")
    _sidebar_scope = (
        "BG + LO + MPH" if _s0_loaded else "diagnostic dataset"
    )
    st.markdown(
        f'<div class="loaded-line"><b>{_scenario_code}</b> · '
        f'{_sidebar_scope} · {plan_state}</div>',
        unsafe_allow_html=True,
    )

    # The only operator-facing solve action. It always runs the current S0
    # working copy (the internal S3 dataset); MS remains a UI placeholder and
    # cannot accidentally queue the phosphate solver. Keep this action before
    # supporting evidence/inventory details so it remains visible on compact
    # screens.
    _n_sidebar_errors = sum(
        1 for issue in ss.get("issues", []) if issue.severity == "ERROR")
    _sidebar_solving = bool(
        ss.get("solving") or ss.get("solve_request"))
    _primary_domain_active = (
        ss.get("domain_nav", common.PRIMARY_DOMAIN)
        == common.PRIMARY_DOMAIN)
    if st.button(
            "▶ Generate Plan",
            type="primary",
            key="sidebar_generate_plan",
            width="stretch",
            disabled=(
                _sidebar_solving
                or _n_sidebar_errors > 0
                or not _primary_domain_active
                or not _s0_loaded),
            help="Generate S0 using the existing S3 three-site planning "
                 "dataset. This queues validation and the deterministic "
                 "CP-SAT solve; it does not issue live equipment commands."):
        if common.request_solve():
            st.rerun()
    if not _primary_domain_active:
        st.caption("MS planning is coming later.")
    elif not _s0_loaded:
        st.caption("Open Plan and load S0 before generating a plan.")
    elif _n_sidebar_errors:
        st.caption(
            f"Resolve {_n_sidebar_errors} validation error"
            f"{'s' if _n_sidebar_errors != 1 else ''} before planning.")
    elif _sidebar_solving:
        st.caption("Plan generation is in progress.")
    else:
        st.caption("Runs S0 · 24 h three-site planning.")

    ev = {"CONFIRMED": 0, "DERIVED": 0, "INFERRED": 0, "ASSUMED": 0, "TBC": 0}
    for items in (scn.spaces, scn.machines, scn.links, scn.destinations,
                  scn.grades, scn.sites):
        for it in items:
            ev[it.evidence.status] = ev.get(it.evidence.status, 0) + 1
    st.markdown(
        " ".join(
            theme.ev_badge(k, v)
            for k, v in ev.items()
            if v and k != "TBC"
        ),
        unsafe_allow_html=True,
    )
    dom = sorted({s.site for s in scn.spaces})
    st.caption(f"Sites: {', '.join(dom)} · horizon "
               f"{scn.sim.horizon_buckets}×{scn.sim.dt_h:g} h · "
               f"seed {scn.sim.seed}")
    inv_meta = scn.config.get("INVENTORY_SNAPSHOT", {})
    if inv_meta:
        inv_scope = ("complete" if inv_meta.get("complete") else "partial")
        st.caption(
            f"Inventory: **{inv_scope} snapshot** · "
            f"{inv_meta.get('covered_count', 0)}/"
            f"{inv_meta.get('expected_count', len(scn.spaces))} piles · "
            f"{inv_meta.get('captured_at', 'timestamp unavailable')}")
    else:
        st.caption("Inventory: **scenario/demo starting values** — upload an SMS "
                   "snapshot on Stockyards & Inventory for an operational run.")

    # One shared, hourly plan-replay control for every process-mimic view.
    # t=0 is the initial inventory boundary with bucket-0 jobs visible; t=24
    # is the terminal boundary after the final work bucket. Imported scenarios
    # are clamped to their available horizon and converted to the nearest
    # native bucket by the renderer.
    _page_id = page.split(" ", 1)[0]
    _monitor_stockyard_view = page == common.MONITOR_LANDING_PAGE
    if ss.plan is not None and _monitor_stockyard_view:
        st.divider()
        st.markdown("##### Stockyard time & state")
        _basis_key = process_mimic.PROCESS_MIMIC_BASIS_KEY
        _basis_options = process_mimic.process_mimic_sidebar_states(
            ss.plan, ss.sim)
        if ss.get(_basis_key) not in _basis_options:
            ss[_basis_key] = process_mimic.PROCESS_MIMIC_PLAN_TIMELINE
        _horizon_h = scn.sim.horizon_buckets * scn.sim.dt_h
        st.caption(
            f"Initial = t0 · Plan timeline = selected hour · Final = "
            f"t{_horizon_h:g} h · Simulation = latest executed bucket")
        _display_basis = st.radio(
            "Stockyard state", _basis_options, key=_basis_key,
            help="Plan timeline follows the hour slider. Initial and Final "
                 "show the two inventory boundaries; Current simulation uses "
                 "the latest simulation bucket. State histories are stored "
                 "at native planning-bucket boundaries, so minute values are "
                 "not interpolated.")
        _max_display_h = \
            process_mimic.process_mimic_display_hour_limit(scn)
        _time_key = process_mimic.PROCESS_MIMIC_HOUR_KEY
        try:
            _saved_hour = int(round(float(ss.get(_time_key, 0))))
        except (TypeError, ValueError):
            _saved_hour = 0
        ss[_time_key] = max(0, min(_max_display_h, _saved_hour))
        if _max_display_h:
            _display_hour = st.slider(
                "Plan hour", min_value=0, max_value=_max_display_h, step=1,
                key=_time_key, format="t = %d h",
                disabled=(
                    _display_basis != process_mimic.PROCESS_MIMIC_PLAN_TIMELINE),
                help="Shared across the Monitor KPI/flow page, Louta, "
                     "Benguerir and MPH views. t=0 is the initial boundary; "
                     "t=24 is the "
                     "terminal boundary for the built-in 24-hour plans. "
                     f"Native state Δt = {scn.sim.dt_h:g} h; the selected "
                     "hour maps to the nearest stored boundary, with no "
                     "minute interpolation.")
            if _display_basis == process_mimic.PROCESS_MIMIC_INITIAL_STATE:
                st.caption("Scenario initial snapshot · time slider paused")
            elif _display_basis == process_mimic.PROCESS_MIMIC_FINAL_STATE:
                st.caption("Terminal plan boundary · time slider paused")
            elif _display_basis == \
                    process_mimic.PROCESS_MIMIC_SIMULATION_STATE:
                st.caption("Current simulation bucket · time slider paused")
            elif _display_hour == 0:
                st.caption("Initial boundary · bucket-0 jobs/flows shown")
            elif _display_hour == _max_display_h:
                st.caption("Terminal boundary · no active job")
            else:
                st.caption(f"Plan replay · t = {_display_hour} h")
        else:
            st.caption("No positive display horizon in this scenario.")

    st.caption("mph_sim 0.1.0 · CP-SAT deterministic · "
               f"evidence: {report_ref('§16')}")

# =========================== DOMAIN NAVBAR ====================================
# This is deliberately a UI scope switch, not a dataset selector. Selecting
# the future MS section must preserve the loaded S0 scenario and any plan.
with st.container(key="domain_navbar"):
    _active_domain = st.segmented_control(
        "Domain",
        options=list(common.DOMAIN_OPTIONS),
        key="domain_nav",
        selection_mode="single",
        width="content",
        label_visibility="collapsed",
        help="BG + LO + MPH is the active three-site planning domain. "
             "MS is reserved for a later workspace and does not load or "
             "modify a planning dataset.",
    )

if _active_domain == common.FUTURE_DOMAIN:
    st.subheader("MS")
    st.info(
        "MS workspace — coming later. Select **BG + LO + MPH** above to "
        "return to the current S0 three-site plan. Your loaded scenario and "
        "generated plan are preserved."
    )
    st.stop()

# ============================= CONTEXT BAR ====================================
s = common.status_summary()
err_chip = (f'<span class="errchip">✖ {s["errors"]} errors</span>'
            if s["errors"] else '<span class="okchip">✓ inputs valid</span>')
warn_chip = (f'<span class="warnchip">⚠ {s["warnings"]} warnings</span>'
             if s["warnings"] else "")
unmet_txt = (f' · unmet <b>{theme.fmt_t(s["unmet_t"])}</b>'
             if s["unmet_t"] is not None else "")
_status_page_title = common.page_title(page)
if _scenario_code == "S0" and _status_page_title.startswith("S0 · "):
    _status_page_title = _status_page_title.removeprefix("S0 · ")
_status_items = [
    (f'<span class="status-item"><b>{_scenario_code}</b>&nbsp;'
     f'<span class="sb-dim">{_status_page_title}</span></span>'),
    err_chip,
]
if warn_chip:
    _status_items.append(warn_chip)
if ss.plan is None:
    _status_items.append(
        '<span class="status-item"><b>Ready to plan</b></span>')
else:
    _status_items.append(
        f'<span class="status-item">plan&nbsp;<b>{s["plan_status"]}</b>'
        f'&nbsp;·&nbsp;{s["jobs"]} jobs{unmet_txt}</span>')
    if s["solver_s"] not in (None, "", "—"):
        _status_items.append(
            f'<span class="status-item">solve&nbsp;'
            f'<b>{s["solver_s"]}</b></span>')
    if s["sim_bucket"] not in (None, "", "idle", "—"):
        _status_items.append(
            f'<span class="status-item">simulation&nbsp;'
            f'<b>{s["sim_bucket"]}</b></span>')
    if s["replans"]:
        _status_items.append(
            f'<span class="status-item">replans&nbsp;'
            f'<b>{s["replans"]}</b></span>')
st.markdown(
    f'<div class="statusbar">{"".join(_status_items)}</div>',
    unsafe_allow_html=True)

if ss.get("flash"):
    st.toast(ss.flash, icon="✅")
    ss.flash = None

# ===================== SOLVE EXECUTOR (prominent, main area) ==================
if ss.get("solve_request") and not ss.get("solving"):
    _name = ss.scenario_name.split(" —")[0]
    _typ = common.typical_solve_s(_name)
    box = st.status(f"Generating {_name} plan — typical duration ≈ {_typ} s · "
                    "do not close this tab", expanded=True, state="running")
    errs = [i for i in common.revalidate() if i.severity == "ERROR"]
    if errs:
        ss.solve_request = False
        box.update(label=f"Cannot solve — {len(errs)} validation errors "
                         "(open the issue drawer at the bottom)",
                   state="error", expanded=True)
        for e in errs[:5]:
            box.write(f"✖ {e.code}: {theme.operator_text(e.message)}")
    else:
        _t0 = time.time()
        _n = {"i": 0}

        def _cb(stage: str):
            _n["i"] += 1
            box.write(f"{_n['i']}/5 · {stage} … "
                      f"(t+{time.time() - _t0:.0f}s)")

        res = common.run_plan(progress_cb=_cb)
        _el = time.time() - _t0
        if res.status in ("OPTIMAL", "FEASIBLE"):
            _unmet = sum(sum(v) for v in res.unmet.values())
            _ready = (f"✓ {_name} plan ready — {res.status} · "
                      f"{len(res.jobs)} jobs · unmet "
                      f"{theme.fmt_t(_unmet)} · {_el:.1f} s")
            box.update(label=_ready, state="complete", expanded=False)
            # Preserve completion as a one-shot toast and rerun once to mount
            # the finished plan in the Plan workspace on a clean, idle page.
            ss.flash = _ready.lstrip("✓ ")
            ss["_pending_page"] = common.PLAN_LANDING_PAGE
            try:
                st.query_params["page"] = \
                    common.PLAN_LANDING_PAGE.split(" ·")[0]
            except Exception:
                pass
            st.rerun()
        else:
            box.update(label=f"✖ solve failed — {res.status} "
                             "(previous plan kept)",
                       state="error", expanded=True)
            for r in res.reasons[:4]:
                box.write(f"✖ {theme.operator_text(r)}")

# ============================= SELECTED PAGE ==================================
PAGES[page]()

# ======================= PERSISTENT ISSUE DRAWER ==============================
issues = ss.issues
n_err = sum(1 for i in issues if i.severity == "ERROR")
n_wrn = sum(1 for i in issues if i.severity == "WARNING")
with st.expander(f"Validation issues — {n_err} errors · {n_wrn} warnings · "
                 f"{len(issues) - n_err - n_wrn} info", expanded=False):
    import pandas as pd
    if issues:
        theme.show_df(pd.DataFrame(
            [{"severity": i.severity, "code": i.code, "entity": i.entity,
              "message": i.message} for i in issues]), height=240)
    else:
        st.caption("no issues")
    if st.button("Re-validate now"):
        common.revalidate()
        st.rerun()
