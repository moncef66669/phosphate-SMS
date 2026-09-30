"""The other three Monitor-section pages, rebuilt as console pages.

Fidelity, stated honestly: SITUATION, SCHEDULE and COVERAGE are NEW compact
console pages built directly from the plan/scenario. They are not ports of
``pages_overview.page_overview``, ``pages_planning.page_schedule`` or
``page_machine_coverage`` and do not carry those pages' full feature sets
(scenario comparison, replan controls, export trees, editable tables). The
review target of this preview is STOCKYARD LAYOUT & FLOW, which is at full
function parity; these three exist so the console reads as a real product
rather than a single screen.
"""
from __future__ import annotations

from typing import Dict, List

import plotly.graph_objects as go
import streamlit as st

from . import canvas, conditions, tokens as t, widgets as w
from .bridge import (build_process_mimic_state, compute_kpis, evidence_counts,
                     geometry_for, site_tag, views_for)

_KIND_COLOR = {"stack": t.INFO, "reclaim": t.OK, "dispatch": t.ACCENT,
               "transfer": t.FUTURE}


# ================================ situation ==================================
def render_situation(sc, plan, sim, issues) -> None:
    state = build_process_mimic_state(
        sc, plan, sim, basis="plan" if plan is not None else "initial",
        bucket=sc.sim.horizon_buckets if plan is not None else 0)
    geo = geometry_for(sc, views_for(sc)[0], show_future=False)
    conds = conditions.collect(sc, state, issues=issues,
                               geometry_gaps=len(geo.gaps))
    n = conditions.counts(conds)

    w.condition_ribbon([
        w.RibbonCell("alarm", n[conditions.ALARM], conditions.ALARM),
        w.RibbonCell("caution", n[conditions.CAUTION], conditions.CAUTION),
        w.RibbonCell("advisory", n[conditions.ADVISORY], conditions.ADVISORY),
    ], message=conditions.headline(conds))

    if plan is None:
        w.panel_head("Planning readiness", "no plan on file")
        st.markdown(
            '<div class="cx-list"><div class="cx-empty">'
            "This scenario is loaded but not solved. Use GENERATE PLAN in the "
            "title bar; the layout &amp; flow page shows the t0 initial state "
            "in the meantime.</div></div>", unsafe_allow_html=True)

    k = compute_kpis(sc, plan, sim) if plan is not None else None
    tiles: List[w.Kpi] = [
        w.Kpi("Scenario", state.monitor.scenario_name.split(" —")[0],
              sub=f"{len(sc.spaces)} spaces · {len(sc.machines)} machines "
                  f"· {len(sc.links)} links", tone="info"),
        w.Kpi("Horizon", f"{sc.sim.horizon_buckets}",
              unit=f"× {sc.sim.dt_h:g} h", sub=f"seed {sc.sim.seed}",
              tone="info"),
    ]
    if k is not None:
        tiles += [
            w.Kpi("Plan", plan.status, sub=f"{len(plan.jobs)} jobs",
                  tone="ok" if plan.status in ("OPTIMAL", "FEASIBLE")
                  else "caut"),
            w.Kpi("Service", f"{k['service_pct']:.1f}", "%",
                  bar_pct=float(k["service_pct"]),
                  tone="ok" if k["service_pct"] >= 99.95 else "caut"),
            w.Kpi("On-spec", f"{k['onspec_pct']:.1f}", "%",
                  bar_pct=float(k["onspec_pct"]),
                  tone="ok" if k["onspec_pct"] >= 99.95 else "caut"),
            w.Kpi("Unmet", w.fmt_t(float(k["unmet_t"])),
                  tone="ok" if k["unmet_t"] <= 1e-6 else "alarm"),
            w.Kpi("Throughput", w.fmt_t(float(k["throughput_t"])),
                  sub=f"solver {k['solver_wall_s']:.1f} s", tone="info"),
            w.Kpi("Terminal inventory",
                  w.fmt_t(float(k["inventory_total_end_t"])),
                  sub="all modelled spaces", tone="info"),
        ]
    w.kpi_strip(tiles)

    left, right = st.columns([1.35, 1], gap="small")
    with left:
        w.panel_head("Corridors", "inter-site transport")
        rows = []
        for c in state.monitor.corridors:
            rows.append((f"{c.id} · from {c.from_site}",
                         f"{c.capacity_tph:,.0f} t/h · transit {c.transit_h:g} h"
                         + (f" · in transit {c.in_transit_t:,.0f} t"
                            if c.in_transit_t is not None else "")
                         + (f" · next {c.next_arrival}"
                            if c.next_arrival else "")))
        if rows:
            w.kv_table(rows)
        else:
            st.markdown('<div class="cx-list"><div class="cx-empty">'
                        "single-site scenario — no corridors</div></div>",
                        unsafe_allow_html=True)

        if k is not None:
            w.panel_head("Demand service", "per destination")
            dst = []
            sev = {}
            for did, row in sorted(k["per_destination"].items()):
                key = did
                dst.append((key,
                            f"{row['delivered_t']:,.0f} / "
                            f"{row['demand_t']:,.0f} t · "
                            f"{row['service_pct']:.1f} %"))
                sev[key] = ("ok" if row["service_pct"] >= 99.95
                            else "caut" if row["service_pct"] > 0
                            else "alarm" if row["demand_t"] > 0 else "")
            w.kv_table(dst, severities=sev)

    with right:
        w.panel_head("Conditions", f"{len(conds)} total")
        w.condition_list(conditions.rows(conds)[:14],
                         empty="all monitored conditions normal")
        w.panel_head("Evidence mix", "scenario entities")
        ev = evidence_counts(sc)
        total = sum(ev.values()) or 1
        w.kv_table([(kk, f"{vv}  ({100.0 * vv / total:.0f} %)")
                    for kk, vv in ev.items()],
                   severities={"CONFIRMED": "ok", "ASSUMED": "caut",
                               "TBC": "caut"})

    if plan is None:
        return

    tl, tr = st.columns([1.55, 1], gap="small")
    with tl:
        w.panel_head("Stock trajectory", "modelled spaces per bucket")
        by_site: Dict[str, List[float]] = {}
        T = sc.sim.horizon_buckets + 1
        for s in sc.spaces:
            ser = plan.inventory.get(s.id) or [s.mass_t]
            acc = by_site.setdefault(s.site, [0.0] * T)
            for i in range(T):
                acc[i] += ser[min(i, len(ser) - 1)]
        fig = go.Figure()
        for site, ser in sorted(by_site.items()):
            fig.add_trace(go.Scatter(
                x=list(range(len(ser))), y=ser, mode="lines", name=site,
                line=dict(width=1.8),
                hovertemplate=f"{site}<br>bucket %{{x}}<br>"
                              "%{y:,.0f} t<extra></extra>"))
        fig.update_xaxes(title="plan bucket", dtick=2)
        fig.update_yaxes(title="tonnes on ground")
        canvas.chart(fig, height=250, show_legend=len(by_site) > 1)
        st.plotly_chart(fig, width="stretch",
                        config={"displaylogo": False,
                                "displayModeBar": False},
                        key="cx_stock_traj")
    with tr:
        w.panel_head("Machine utilisation", "busy buckets / horizon")
        util = k["machine_utilisation_pct"]
        w.kv_table([(mid, f"{val:.0f} %") for mid, val in
                    sorted(util.items(), key=lambda kv: -kv[1])],
                   severities={mid: ("caut" if val <= 0 else "")
                               for mid, val in util.items()})


# ================================= schedule ==================================
def render_schedule(sc, plan) -> None:
    if plan is None:
        w.panel_head("Job schedule", "no plan")
        st.markdown('<div class="cx-list"><div class="cx-empty">'
                    "generate a plan to populate the schedule</div></div>",
                    unsafe_allow_html=True)
        return

    machines = sorted({j.machine for j in plan.jobs})
    order = {m: i for i, m in enumerate(machines)}
    fig = go.Figure()
    for kind in sorted({j.kind for j in plan.jobs}):
        js = [j for j in plan.jobs if j.kind == kind]
        fig.add_trace(go.Bar(
            name=kind, orientation="h",
            y=[order[j.machine] for j in js],
            base=[j.start_bucket for j in js],
            x=[j.end_bucket - j.start_bucket + 1 for j in js],
            marker=dict(color=_KIND_COLOR.get(kind, t.NEUTRAL),
                        line=dict(color=t.INK_700, width=1)),
            customdata=[[j.id, j.space, j.dest_id or "—", j.rate_tph,
                         j.tonnage_t, j.grade] for j in js],
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>bucket %{base}–"
                "%{x}<br>space %{customdata[1]}<br>dest %{customdata[2]}"
                "<br>%{customdata[3]:,.0f} t/h · %{customdata[4]:,.0f} t"
                "<br>grade %{customdata[5]}<extra>" + kind + "</extra>"),
        ))
    fig.update_layout(barmode="overlay", bargap=0.35)
    fig.update_yaxes(tickmode="array", tickvals=list(range(len(machines))),
                     ticktext=machines, autorange="reversed")
    fig.update_xaxes(title="plan bucket", dtick=2,
                     range=[-0.5, sc.sim.horizon_buckets + 0.5])
    canvas.chart(fig, height=max(220, 26 * len(machines) + 70),
                 show_legend=True)

    w.frame_head("Job schedule — machine × bucket", [
        w.chip("jobs", value=str(len(plan.jobs)), tone="info"),
        w.chip("status", value=plan.status,
               tone="ok" if plan.status in ("OPTIMAL", "FEASIBLE")
               else "caut"),
        w.chip("objective", value=f"{plan.objective:,.0f}", mono=True)])
    st.plotly_chart(fig, width="stretch",
                    config={"displaylogo": False, "displayModeBar": False},
                    key="cx_gantt")

    dl, dr = st.columns([1.55, 1], gap="small")
    with dl:
        w.panel_head("Delivery profile", "per destination, per bucket")
        pf = go.Figure()
        for d in sc.destinations:
            ser = plan.delivered.get(d.id)
            if not ser or not any(ser):
                continue
            pf.add_trace(go.Bar(
                name=d.id, x=list(range(len(ser))),
                y=[v * sc.sim.dt_h for v in ser],
                hovertemplate=f"{d.id}<br>bucket %{{x}}<br>"
                              "%{y:,.0f} t<extra></extra>"))
        pf.update_layout(barmode="stack", bargap=0.15)
        pf.update_xaxes(title="plan bucket", dtick=2)
        pf.update_yaxes(title="tonnes delivered")
        canvas.chart(pf, height=250, show_legend=True)
        st.plotly_chart(pf, width="stretch",
                        config={"displaylogo": False,
                                "displayModeBar": False},
                        key="cx_delivery")
    with dr:
        w.panel_head("Plan objective breakdown", "solver terms")
        rows = [(kk, f"{vv:,.1f}") for kk, vv in
                sorted((plan.breakdown or {}).items())]
        w.kv_table(rows or [("breakdown", "not reported by this solve")])
        w.panel_head("Reasons / notes", f"{len(plan.reasons)}")
        w.condition_list([("ADVISORY", "SOLVER", r)
                          for r in plan.reasons[:8]],
                         empty="solver reported no notes")

    with st.expander(f"Job list ({len(plan.jobs)})", expanded=False):
        import pandas as pd
        st.dataframe(pd.DataFrame([{
            "job": j.id, "machine": j.machine, "kind": j.kind,
            "space": j.space, "dest": j.dest_id, "grade": j.grade,
            "from": j.start_bucket, "to": j.end_bucket,
            "t/h": round(j.rate_tph, 1), "tonnes": round(j.tonnage_t, 1),
            "comment": j.comment,
        } for j in plan.jobs]), width="stretch", height=340,
            hide_index=True)


# ================================= coverage ==================================
def render_coverage(sc, plan) -> None:
    machines = [m for m in sc.machines]
    spaces = [s.id for s in sc.spaces]
    if not machines or not spaces:
        st.info("no machines/spaces in this scenario")
        return

    used: Dict[str, set] = {}
    if plan is not None:
        for j in plan.jobs:
            used.setdefault(j.machine, set()).add(j.space)

    z, text = [], []
    for m in machines:
        zr, tr = [], []
        for s in spaces:
            reach = s in m.reachable_spaces
            planned = s in used.get(m.id, ())
            zr.append(2 if planned else (1 if reach else 0))
            tr.append("USED" if planned else ("reach" if reach else ""))
        z.append(zr)
        text.append(tr)

    fig = go.Figure(go.Heatmap(
        z=z, x=spaces, y=[m.id for m in machines], text=text,
        texttemplate="%{text}", textfont=dict(size=8),
        colorscale=[[0.0, t.INK_600], [0.49, t.INK_600],
                    [0.5, "#2b4a63"], [0.99, "#2b4a63"],
                    [1.0, t.OK_MUTED]],
        zmin=0, zmax=2, showscale=False, xgap=1, ygap=1,
        hovertemplate="machine %{y}<br>space %{x}<br>%{text}<extra></extra>"))
    canvas.chart(fig, height=max(200, 22 * len(machines) + 90), grid=False)
    fig.update_xaxes(side="top", tickangle=-40)

    w.frame_head("Machine reachability × stockpile", [
        w.chip("configured reach", tone="info"),
        w.chip("used by the plan", tone="ok"),
        w.chip("not reachable", tone="mute"),
        w.chip("reach is CONFIGURED, never inferred from geometry",
               tone="mute")])
    st.plotly_chart(fig, width="stretch",
                    config={"displaylogo": False, "displayModeBar": False},
                    key="cx_coverage")

    left, right = st.columns(2, gap="small")
    with left:
        w.panel_head("Reach per machine", "configured spaces")
        w.kv_table([(site_tag(m.site, m.id),
                     f"{len(m.reachable_spaces)} · "
                     + (", ".join(m.reachable_spaces) or "none"))
                    for m in machines],
                   severities={site_tag(m.site, m.id): "caut"
                               for m in machines if not m.reachable_spaces})
    with right:
        w.panel_head("Spaces with no reach", "planning blind spots")
        orphan = [s for s in spaces
                  if not any(s in m.reachable_spaces for m in machines)]
        if orphan:
            w.condition_list([("CAUTION", s, "no machine can reach this space")
                              for s in orphan])
        else:
            w.condition_list([], empty="every space is reachable by ≥1 machine")
        if plan is not None:
            w.panel_head("Utilisation", "busy buckets / horizon")
            k = compute_kpis(sc, plan, None)
            util = k["machine_utilisation_pct"]
            w.kv_table([(mid, f"{val:.0f} %")
                        for mid, val in sorted(util.items(),
                                               key=lambda kv: -kv[1])])
