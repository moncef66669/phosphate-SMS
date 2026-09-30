"""OPERATIONS CONSOLE — UI design preview (separate app, review only).

A second, independent Streamlit entry point for reviewing a
production/industrial console design for the Monitor section, above all
"Stockyard Layout & Flow". It imports the production engine and view-models
read-only from ``../simulator`` and writes nothing there.

Deliberately NOT a page inside the production app: ``simulator/app.py``
asserts ``set(PAGES) == set(common.ALL_PAGE_LABELS)`` and resolves ``?page=N``
positionally, so adding an entry would break that assert and renumber every
existing deep link (including the ``?page=15`` used by the QA drivers).

Run:  streamlit run app_preview.py           (from simulator_preview/)
  or: ..\RUN-UI-PREVIEW.cmd

Deep links:  ?page=flow|situation|schedule|coverage   ?scn=s1|s2|s3
             ?demo=1|s2|s3       (one-off solve, for screenshots)
             ?demo=states        review showcase: machines in several real
                                 states at once (maintenance calendar entry +
                                 simulated failure window)
"""
from __future__ import annotations

import datetime as dtm
import time

import streamlit as st

st.set_page_config(page_title="MPH Operations Console — UI preview",
                   page_icon="◧", layout="wide",
                   initial_sidebar_state="collapsed")

from hmi import css, page_layout_flow, pages_more, tokens as t  # noqa: E402
from hmi import widgets as w                                    # noqa: E402
from hmi.bridge import (CalendarEntry, SolveOptions,             # noqa: E402
                        common, compute_kpis, datasets, engine_versions,
                        evidence_counts, report_ref, solve_plan,
                        validate_scenario)

css.inject()
common.init_state()
ss = st.session_state

PAGES = {
    "flow": ("Layout & flow", "Stockyard layout & flow"),
    "situation": ("Situation", "Situation overview"),
    "schedule": ("Schedule", "Job schedule"),
    "coverage": ("Coverage", "Machine coverage"),
}


# ----------------------------------------------------- demo bundle (QA) ------
@st.cache_resource(show_spinner="preparing preview bundle (one-off solve)…")
def _demo(which: str, revision: str):
    """``states`` is a REVIEW showcase: it puts real, differently-stated
    machines on one screen so the symbol/state language can be checked. The
    states come from the model (a maintenance calendar entry and a simulated
    failure window), not from painted-on colours."""
    del revision
    if which == "states":
        # A maintenance calendar entry on ST01-C plus the plan's own bucket-2
        # activity puts STACKING, RECLAIMING, TRAVELLING and MAINTENANCE on one
        # screen. All four come from the model; nothing is painted on. The
        # remaining states in the key (Idle, Unavailable, Future) need an idle
        # bucket, a simulated outage or the Future layer respectively.
        sc0 = datasets.mph_phosphate()
        sc0.calendars.append(CalendarEntry(
            "ST01-C", 0, sc0.sim.horizon_buckets - 1,
            "maintenance (review showcase — symbol/state key)"))
        p0 = solve_plan(sc0, SolveOptions(max_time_s=10))
        return sc0, p0, validate_scenario(sc0), None
    sc0 = (datasets.sulphur_minimal() if which == "s1"
           else datasets.end_to_end() if which == "s3"
           else datasets.mph_phosphate())
    p0 = solve_plan(sc0, SolveOptions(max_time_s=8))
    return sc0, p0, validate_scenario(sc0), None


_qp = dict(st.query_params)
_demo_key = str(_qp.get("demo", "")).lower()
if _demo_key in ("1", "s1", "s2", "s3", "states") and ss.get("plan") is None:
    import copy
    sc0, p0, iss0, sim0 = _demo("s2" if _demo_key == "1" else _demo_key,
                                datasets.BUILTIN_BASELINE_REVISION)
    ss.scenario = copy.deepcopy(sc0)
    ss.scenario_name = sc0.name
    ss.plan = copy.deepcopy(p0)
    ss.sim = copy.deepcopy(sim0) if sim0 is not None else None
    ss.issues = list(iss0)
    if _demo_key == "states":
        # seed the rail controls BEFORE they are instantiated (assigning a
        # keyed widget afterwards raises StreamlitAPIException)
        ss.cx_state = "Inspect plan bucket"
        ss.cx_bucket = 2

_scn = str(_qp.get("scn", "")).lower()
if _scn and not ss.get("_cx_scn_done"):
    ss._cx_scn_done = True
    target = next((n for n in datasets.BUILTIN_SCENARIOS
                   if n.lower().startswith(_scn)), None)
    if target and target != ss.scenario_name:
        common.load_scenario(target)

if ss.get("cx_page") not in PAGES:
    ss.cx_page = (str(_qp.get("page", "flow")).lower()
                  if str(_qp.get("page", "flow")).lower() in PAGES
                  else "flow")

# ================================ TITLE BAR ==================================
sc = common.sc()
plan = common.plan()
sim = common.sim()
ev = evidence_counts(sc)
ver = engine_versions()

tb, tb_page, tb_scn, tb_go = st.columns([3.05, 1.65, 1.5, 0.72],
                                        gap="small",
                                        vertical_alignment="center")
with tb:
    plan_tone = ("ok" if plan is not None and plan.status in ("OPTIMAL",
                                                             "FEASIBLE")
                 else "mute")
    chips = [
        w.chip("SCENARIO", value=sc.name.split(" —")[0], lead=True,
               mono=True),
        w.chip("PLAN", value=(plan.status if plan is not None else "none"),
               tone=plan_tone, mono=True),
        w.chip("EVIDENCE",
               value=" ".join(f"{k[:1]}{v}" for k, v in ev.items()),
               tone="info", mono=True),
        w.chip("GEOMETRY", value=ver["geometry_revision"][:24] + "…",
               tone="mute", mono=True),
    ]
    now = dtm.datetime.now()
    w.titlebar(product="MPH OPERATIONS CONSOLE",
               subtitle="UI design preview · Monitor section · "
                        "planning & monitoring — no live control",
               right_chips=chips,
               clock_top=now.strftime("%H:%M:%S"),
               clock_bottom=now.strftime("%a %d %b %Y") + "  ·  local")

with tb_page:
    page = st.segmented_control(
        "Page", list(PAGES), default=ss.cx_page, key="cx_page",
        format_func=lambda k: PAGES[k][0],
        label_visibility="collapsed") or "flow"
    try:
        st.query_params["page"] = page
    except Exception:
        pass

with tb_scn:
    names = list(datasets.BUILTIN_SCENARIOS)
    cur = ss.scenario_name
    sel = st.selectbox("Scenario", names,
                       index=names.index(cur) if cur in names else 0,
                       key="cx_scn", label_visibility="collapsed",
                       help="selecting a scenario LOADS it (clears any plan) "
                            "— it never solves")
    if ss.get("_cx_scn_prev") is None:
        ss._cx_scn_prev = sel
    elif sel != ss._cx_scn_prev:
        ss._cx_scn_prev = sel
        common.load_scenario(sel)
        st.rerun()

with tb_go:
    solving = bool(ss.get("solving") or ss.get("solve_request"))
    if st.button("▶ PLAN", type="primary", width="stretch",
                 disabled=solving, key="cx_solve",
                 help="solve the loaded scenario with CP-SAT"):
        if common.request_solve():
            st.rerun()

# ================================ SOLVE ======================================
if ss.get("solve_request") and not ss.get("solving"):
    name = ss.scenario_name.split(" —")[0]
    box = st.status(f"Generating {name} plan — typical "
                    f"≈ {common.typical_solve_s(name)} s · keep this tab open",
                    expanded=True, state="running")
    errs = [i for i in common.revalidate() if i.severity == "ERROR"]
    if errs:
        ss.solve_request = False
        box.update(label=f"Cannot solve — {len(errs)} validation errors",
                   state="error", expanded=True)
        for e in errs[:5]:
            box.write(f"✖ {e.code}: {e.message}")
    else:
        t0 = time.time()
        step = {"i": 0}

        def _cb(stage: str):
            step["i"] += 1
            box.write(f"{step['i']}/5 · {stage} … (t+{time.time() - t0:.0f}s)")

        res = common.run_plan(progress_cb=_cb)
        if res.status in ("OPTIMAL", "FEASIBLE"):
            unmet = sum(sum(v) for v in res.unmet.values())
            box.update(label=f"✓ {name} — {res.status} · {len(res.jobs)} jobs "
                             f"· unmet {unmet:,.0f} t · "
                             f"{time.time() - t0:.1f} s",
                       state="complete", expanded=False)
            st.rerun()
        else:
            box.update(label=f"✖ solve failed — {res.status} "
                             "(previous plan kept)", state="error",
                       expanded=True)
            for r in res.reasons[:4]:
                box.write(f"✖ {r}")

# ================================= PAGE ======================================
plan = common.plan()
sim = common.sim()
issues = list(ss.get("issues", []))

if page == "flow":
    page_layout_flow.render(sc, plan, sim, issues)
elif page == "situation":
    pages_more.render_situation(sc, plan, sim, issues)
elif page == "schedule":
    pages_more.render_schedule(sc, plan)
else:
    pages_more.render_coverage(sc, plan)

# ================================ FOOTER =====================================
n_err = sum(1 for i in issues if i.severity == "ERROR")
n_wrn = sum(1 for i in issues if i.severity == "WARNING")
n_inf = len(issues) - n_err - n_wrn
inv = (sc.config or {}).get("INVENTORY_SNAPSHOT", {})

with st.expander(f"Validation issues — {n_err} errors · {n_wrn} warnings · "
                 f"{n_inf} info", expanded=False):
    if issues:
        import pandas as pd
        st.dataframe(pd.DataFrame([{
            "severity": i.severity, "code": i.code, "entity": i.entity,
            "message": i.message} for i in issues]),
            width="stretch", height=240, hide_index=True)
    else:
        st.caption("no issues")
    if st.button("Re-validate now", key="cx_reval"):
        common.revalidate()
        st.rerun()

w.footer([
    f"<b>UI DESIGN PREVIEW</b> — separate app under "
    f"<code>simulator_preview/</code>. The production simulator in "
    f"<code>simulator/</code> is imported read-only and is not modified.",
    f"<b>Engine</b> baseline <code>{ver['baseline_revision']}</code> · "
    f"geometry <code>{ver['geometry_revision']}</code> · evidence "
    f"{report_ref('§16')}",
    f"<b>Inventory</b> "
    + (f"{'complete' if inv.get('complete') else 'partial'} snapshot · "
       f"{inv.get('covered_count', 0)}/"
       f"{inv.get('expected_count', len(sc.spaces))} piles · "
       f"{inv.get('captured_at', 'timestamp unavailable')}"
       if inv else "scenario/demo starting values — no SMS snapshot loaded"),
    "<b>Not a control system</b> — no commands are issued; PLC interlocks and "
    "the independent ACS are outside this layer.",
])
