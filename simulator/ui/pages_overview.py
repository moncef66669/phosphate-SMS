"""Pages: 1 Executive Overview · 8 Results & KPIs · 13 Scenario Comparison."""
from __future__ import annotations

import html
import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from mph_sim.kpis import compute_kpis

from . import common, theme
from .report_ref import report_ref
from .theme import (BLUE, DGREEN, DORANGE, GREEN, GREY, NAVY, ORANGE, RED,
                    gantt_figure, jobs_to_gantt_df, plot, plotly_layout,
                    series_fig, show_df)


def _kpis():
    scn, p, s = common.sc(), common.plan(), common.sim()
    if p is None:
        return None
    return compute_kpis(scn, p, s)


def _physical_inventory_by_site(scn, inventory) -> dict[str, list[float]]:
    """Aggregate physical stockpiles without hopper or process-WIP buffers."""
    if not inventory:
        return {}
    physical_spaces = {
        space.id: space
        for space in scn.spaces
        if space.area not in {"mine-interface", "process-interface"}
    }
    available = [
        list(values)
        for space_id, values in inventory.items()
        if space_id in physical_spaces and values
    ]
    if not available:
        return {}
    points = max(len(values) for values in available)
    totals: dict[str, list[float]] = {}
    for space_id, values in inventory.items():
        space = physical_spaces.get(space_id)
        if space is None or not values:
            continue
        series = list(values)
        if len(series) < points:
            series.extend([series[-1]] * (points - len(series)))
        site_values = totals.setdefault(space.site, [0.0] * points)
        for index, value in enumerate(series[:points]):
            site_values[index] += float(value)
    return dict(sorted(totals.items()))


def _inventory_site_figure(scn, inventory) -> go.Figure:
    """Readable inventory trend with one trace per operating site."""
    totals = _physical_inventory_by_site(scn, inventory)
    fig = go.Figure()
    colors = {
        "BENGUERIR": GREEN,
        "LOUTA": ORANGE,
        "MPH": NAVY,
    }
    for site, values in totals.items():
        site_label = "MPH" if site == "MPH" else site.title()
        fig.add_scatter(
            x=list(range(len(values))),
            y=values,
            name=site_label,
            mode="lines",
            line=dict(color=colors.get(site, BLUE), width=3),
            hovertemplate=(
                f"<b>{site_label}</b><br>"
                "Boundary %{x}<br>Inventory %{y:,.0f} t"
                "<extra></extra>"
            ),
        )
    fig = plotly_layout(fig, "Physical stock inventory by site", 330)
    fig.update_layout(
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
        margin=dict(l=72, r=24, t=48, b=48),
    )
    fig.update_xaxes(title="planning boundary")
    fig.update_yaxes(title="inventory [t]", rangemode="tozero")
    return fig


def _demand_delivery_figure(k) -> go.Figure:
    """Compare horizon demand and delivery with two semantic traces."""
    rows = [
        (
            destination,
            float(values.get("demand_t", 0.0)),
            float(values.get("delivered_t", 0.0)),
        )
        for destination, values in k["per_destination"].items()
    ]
    rows.sort(key=lambda row: (max(row[1], row[2]), row[0]))
    labels = [row[0] for row in rows]
    fig = go.Figure()
    fig.add_bar(
        x=[row[1] for row in rows],
        y=labels,
        name="Demand",
        orientation="h",
        marker_color="#cbd5e1",
        text=[theme.fmt_t(row[1]) for row in rows],
        textposition="outside",
        cliponaxis=False,
        hovertemplate="<b>%{y}</b><br>Demand %{x:,.0f} t<extra></extra>",
    )
    fig.add_bar(
        x=[row[2] for row in rows],
        y=labels,
        name="Delivered",
        orientation="h",
        marker_color=NAVY,
        text=[theme.fmt_t(row[2]) for row in rows],
        textposition="outside",
        cliponaxis=False,
        hovertemplate="<b>%{y}</b><br>Delivered %{x:,.0f} t<extra></extra>",
    )
    height = max(320, 112 + 34 * len(rows))
    fig = plotly_layout(
        fig, "Demand and planned delivery", height, barmode="group")
    fig.update_layout(
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
        margin=dict(l=145, r=78, t=48, b=48),
    )
    fig.update_xaxes(title="horizon total [t]", rangemode="tozero")
    fig.update_yaxes(title="", categoryorder="array", categoryarray=labels)
    return fig


def _offspec_figure(offspec: dict[str, list[float]]) -> go.Figure:
    """Compact destination-level soft-quality deviation summary."""
    all_rows = sorted(
        ((destination, float(sum(values)))
         for destination, values in offspec.items()),
        key=lambda row: (row[1], row[0]),
    )
    rows = [row for row in all_rows if row[1] > 1e-9]
    labels = [row[0] for row in rows]
    values = [row[1] for row in rows]
    fig = go.Figure()
    if rows:
        fig.add_bar(
            x=values,
            y=labels,
            name="Soft-quality deviation",
            orientation="h",
            marker_color=RED,
            text=[f"{value:,.0f}" for value in values],
            textposition="outside",
            cliponaxis=False,
            hovertemplate=(
                "<b>%{y}</b><br>Deviation %{x:,.0f} t·unit<extra></extra>"
            ),
            showlegend=False,
        )
    else:
        fig.add_annotation(
            text="No planned soft-quality deviation",
            showarrow=False,
            font=dict(size=14, color=DGREEN),
        )
    height = max(300, 110 + 34 * len(rows))
    fig = plotly_layout(
        fig, "Planned soft-quality deviation", height)
    fig.update_layout(margin=dict(l=145, r=72, t=48, b=48))
    fig.update_xaxes(title="deviation [t·unit]", rangemode="tozero")
    fig.update_yaxes(title="", categoryorder="array", categoryarray=labels)
    return fig


def _empty_state(scn):
    """Planning-readiness workflow instead of a dashboard of empty cards."""
    ss = st.session_state
    issues = ss.issues
    n_err = sum(1 for i in issues if i.severity == "ERROR")
    n_wrn = sum(1 for i in issues if i.severity == "WARNING")
    n_asm = sum(1 for grp in (scn.spaces, scn.machines, scn.links,
                              scn.destinations, scn.grades, scn.sites)
                for it in grp if it.evidence.status in ("ASSUMED", "TBC"))
    dom = sorted({s_.site for s_ in scn.spaces})
    short = html.escape(
        common.planning_scenario_code(scn.name)
        or scn.name.split(" —")[0]
    )
    sites = html.escape(" · ".join(dom))
    warning_word = "warning" if n_wrn == 1 else "warnings"
    error_word = "error" if n_err == 1 else "errors"

    if n_err:
        badge_class = "blocked"
        badge_text = f"Blocked · {n_err} {error_word}"
        title = "Resolve input errors before planning"
        step_class = "blocked"
        step_note = f"{n_err} blocking {error_word} · {n_wrn} {warning_word}"
    elif n_wrn:
        badge_class = "warning"
        badge_text = f"Ready with {n_wrn} {warning_word}"
        title = "Ready to generate a plan"
        step_class = "active"
        step_note = f"{n_wrn} {warning_word} to review · no blockers"
    else:
        badge_class = "ready"
        badge_text = "Inputs ready"
        title = "Ready to generate a plan"
        step_class = "done"
        step_note = "Validation passed · no blockers"

    important = next((i for i in issues if i.severity == "ERROR"), None)
    if important is None:
        important = next((i for i in issues if i.severity == "WARNING"), None)

    with st.container(key="overview_empty_state", border=True):
        st.markdown(
            f'<div class="plan-ready-head">'
            f'<div><div class="plan-ready-eyebrow">Planning readiness</div>'
            f'<div class="plan-ready-title">{title}</div>'
            f'<div class="plan-ready-copy">No plan generated yet. Review the '
            f'loaded scenario and assumptions, then generate a plan.</div></div>'
            f'<span class="plan-ready-badge {badge_class}">{badge_text}</span>'
            f'</div>', unsafe_allow_html=True)

        st.markdown(
            f'<div class="plan-ready-facts">'
            f'<div class="plan-ready-fact"><span>Loaded scenario</span>'
            f'<b>{short}</b></div>'
            f'<div class="plan-ready-fact"><span>Sites / domain</span>'
            f'<b>{sites}</b></div>'
            f'<div class="plan-ready-fact"><span>Planning horizon</span>'
            f'<b>{scn.sim.horizon_buckets:g} × {scn.sim.dt_h:g} h</b></div>'
            f'<div class="plan-ready-fact"><span>Evidence / assumptions</span>'
            f'<b>{n_asm} planning notes · '
            f'{len(scn.assumptions)} assumptions</b></div>'
            f'</div>', unsafe_allow_html=True)

        st.markdown(
            f'<div class="plan-ready-steps">'
            f'<div class="plan-ready-step done"><span class="step-num">✓</span>'
            f'<div><b>1 · Scenario loaded</b><small>{short} is active</small>'
            f'</div></div>'
            f'<div class="plan-ready-step {step_class}">'
            f'<span class="step-num">2</span><div><b>2 · Check inputs</b>'
            f'<small>{step_note}</small></div></div>'
             f'<div class="plan-ready-step pending"><span class="step-num">3</span>'
             f'<div><b>3 · Generate plan</b><small>Use the sidebar action</small>'
             f'</div></div></div>', unsafe_allow_html=True)

        if important is not None:
            issue_class = "error" if important.severity == "ERROR" else "warning"
            issue_icon = "✖" if important.severity == "ERROR" else "⚠"
            st.markdown(
                f'<div class="plan-ready-issue {issue_class}">'
                f'<b>{issue_icon} {html.escape(important.severity.title())} · '
                f'{html.escape(important.code)}</b>'
                f'<span>{html.escape(theme.operator_text(important.message))}'
                f'</span></div>',
                unsafe_allow_html=True)
        else:
            st.markdown(
                '<div class="plan-ready-issue ok"><b>✓ Inputs validated</b>'
                '<span>No blocking errors or warnings were found.</span></div>',
                unsafe_allow_html=True)

        b1, b2, b3 = st.columns(3, vertical_alignment="center")
        if b1.button("Review assumptions", key="es_assump", width="stretch"):
            common.goto_page("12 · Assumptions & Data Gaps")
            st.rerun()
        if b2.button("Validate inputs", key="es_validate", width="stretch"):
            iss = common.revalidate()
            e = sum(1 for i in iss if i.severity == "ERROR")
            (st.success if e == 0 else st.error)(
                f"Validation complete: {e} errors · "
                f"{sum(1 for i in iss if i.severity == 'WARNING')} warnings. "
                "Details are in the issue drawer below.")
        if b3.button("Open S0 plan", key="es_run_plan", width="stretch"):
            common.goto_page("5 · Demand & Quality")
            st.rerun()
        st.markdown(
            '<div class="plan-ready-next"><b>Next:&nbsp;</b>open&nbsp;'
            '<b>Plan → S0</b>, review demand, then use&nbsp;'
            '<b>Generate Plan</b>&nbsp;in the sidebar.</div>',
            unsafe_allow_html=True)

        st.markdown(
            '<div class="plan-ready-preview"><b>After generation</b>'
            '<span>Service & quality KPIs</span><span>Value-chain flow</span>'
            '<span>Job timeline</span><span>Site operating status</span>'
            '</div>', unsafe_allow_html=True)


# =========================================================== 1. Executive
def page_overview(monitor_compact: bool = False):
    st.subheader("KPIs" if monitor_compact else "Executive Overview")
    scn, p, s = common.sc(), common.plan(), common.sim()
    mode = common.chain_mode(scn)
    if mode == "m16-only":
        st.warning("M16-only demonstration. Upstream corridor arrivals are "
                   "simulated inputs. Use S3 for the complete three-site "
                   "phosphate value chain.")
    k = _kpis()
    if k is None:
        _empty_state(scn)
        return
    T = scn.sim.horizon_buckets

    # ---- executive KPIs: one measure per decision --------------------------
    tot_dem = sum(v["demand_t"] for v in k["per_destination"].values())
    tot_del = sum(min(v["delivered_t"], v["demand_t"])
                  for v in k["per_destination"].values())
    bottlenecks = sum(1 for util in k["route_utilisation_pct"].values()
                      if util >= 85.0)
    c = st.columns(4)
    c[0].metric("Demand service", theme.fmt_pct(k["service_pct"]),
                help="delivered tonnage capped at demand / total demand")
    c[0].caption(f"{theme.fmt_t(tot_del)} / {theme.fmt_t(tot_dem)} · "
                 f"unmet {theme.fmt_t(k['unmet_t'])}")
    c[1].metric(
        "On-spec delivery", theme.fmt_pct(k["onspec_pct"]),
        help="Share of (destination × bucket) pairs WITH DELIVERED material "
             "whose mass-weighted blend quality lies inside every "
             "configured limit. Zero-delivery buckets are not evaluated "
             "(feed gaps count in demand service instead). Soft limits are "
             "penalised (w₂) but never block the plan; hard limits are "
             "enforced by the optimiser.")
    qb = k.get("quality_breakdown", [])
    if any(not r["hard"] for r in qb):
        _asm = any(r["evidence"] in ("ASSUMED", "TBC") for r in qb)
        c[1].caption("soft specifications"
                     + (" · incl. ASSUMED limits" if _asm else ""))
    c[2].metric("Stock-risk buckets",
                f"{k['safety_stock_violation_buckets']}",
                help="buckets below a configured safety-stock level")
    c[2].caption("below configured safety stock")
    c[3].metric("Routes ≥85%", f"{bottlenecks}",
                help="routes whose maximum utilisation is at least 85 %")
    c[3].caption("high-utilisation routes")

    # ---- highest-priority warnings ---------------------------------------
    warns = [i for i in st.session_state.issues if i.severity in ("ERROR", "WARNING")]
    if p:
        warns_p = p.reasons[:5]
    else:
        warns_p = []
    if warns or warns_p:
        with st.container(border=True):
            st.markdown("**Highest-priority warnings**")
            for i in warns[:4]:
                st.markdown(
                    f"- `{i.severity}` {theme.operator_text(i.message)}")
            for r in warns_p:
                st.markdown(f"- `PLAN` {theme.operator_text(r)}")

    # The consolidated Monitor has a dedicated Stockyard Layout tab. Retain
    # the process mimic on legacy/standalone overview renders, while the KPIs
    # tab stays focused on decisions, trends and site status.
    if not monitor_compact:
        st.markdown("#### Process mimic — " + {
            "three-site": ("Domain A: LOUTA + BENGUERIR → MPH M16 hub ("
                           + report_ref() + ")"),
            "m16-only": "M16 hub only (mines not modelled in this scenario)",
            "sulphur": (
                "Domain B: M17 sulphur (no phosphate chain in this scenario)"),
        }[mode])
        if mode == "sulphur":
            plot(_value_chain_fig(scn, k, mode, s))
        else:
            from .process_mimic import render_process_mimic
            render_process_mimic(scn, p, s, collapse_controls=True)
    if mode == "three-site":
        _site_cards(scn, p, s, k)

    # ---- on-spec breakdown (only when below 100 %) --------------------------
    if k and k["onspec_pct"] < 100 - 1e-9 and k.get("quality_breakdown"):
        with st.expander(f"🔍 On-spec breakdown — {k['onspec_pct']:.1f} % "
                         "(why not 100 %)", expanded=False):
            pr = k.get("onspec_principal")
            if pr and not pr["hard"]:
                asm = (" assumed" if pr["evidence"] in ("ASSUMED", "TBC")
                       else "")
                st.markdown(
                    f"On-spec performance includes{asm} **soft quality "
                    f"limits**. The current result is driven primarily by "
                    f"**{pr['destination']} {pr['parameter']}** buckets "
                    f"({pr['violations']} violating); **the plan remains "
                    f"feasible because this specification is configured as "
                    f"soft** (violations carry an objective penalty w₂ and "
                    f"are reported transparently — they are not errors).")
            elif pr and pr["hard"]:
                st.markdown(
                    f"Driven primarily by **{pr['destination']} "
                    f"{pr['parameter']}** buckets on a **hard** "
                    f"specification. The optimiser blocks any allocation "
                    f"violating a hard limit, so these violations come from "
                    f"simulated execution deviations, not from the plan.")
            st.caption("**Hard** violation → blocks feasibility (the "
                       "optimiser leaves demand unmet rather than violate "
                       "it). **Soft** violation → permitted with an "
                       "objective penalty (w₂) and reported here. "
                       "Specifications tagged as planning assumptions are "
                       "placeholders, "
                       "not engineering facts.")
            bdf = pd.DataFrame([{
                "destination": r["destination"], "parameter": r["parameter"],
                "limit": (f"≥ {r['limit_min']:g}" if r["limit_min"] is not None
                          else "") + (f" ≤ {r['limit_max']:g}"
                                      if r["limit_max"] is not None else ""),
                "hard/soft": "hard" if r["hard"] else "soft",
                "evidence": r["evidence"],
                "compliant/evaluated": f"{r['compliant']}/{r['evaluated']}",
                "compliance %": round(r["compliance_pct"], 1),
                "max dev": round(r["max_dev"], 2),
            } for r in k["quality_breakdown"]])
            show_df(bdf)
            st.caption("Full breakdown with filters and violation "
                       "magnitudes: **Results & KPIs** page.")


    # The combined Monitor page deliberately stops at the KPI/site-status
    # summary. Project-team request (29-Jul-2026): temporarily hide the
    # inventory, demand/delivery and quality-deviation graphs while retaining
    # their helpers for a possible later detailed-results view.
    if monitor_compact:
        return

    # ---- planning performance ----------------------------------------------
    # Keep this page decision-oriented: aggregate physical inventory by site,
    # use two semantic demand traces, and leave minute-accurate machine usage
    # to its dedicated Monitor page.
    x = list(range(k.get("evaluated_buckets", T)))
    st.markdown("#### Planning performance")
    inv_src = s.inv_series if s else p.inventory if p else None
    if inv_src:
        plot(_inventory_site_figure(scn, inv_src))
        st.caption(
            "Physical stockpiles only; short-term mine hoppers and process "
            "WIP buffers are excluded from this overview.")

    demand_col, quality_col = st.columns([1.15, 0.85])
    with demand_col:
        plot(_demand_delivery_figure(k))
        st.caption(
            "Horizon totals. Zero-demand destinations remain visible so an "
            "inactive train or plant feed is explicit.")
    with quality_col:
        has_bpl = any(sp_.param == "BPL" for d in scn.destinations
                      for sp_ in d.spec)
        if s and has_bpl:
            qfig = go.Figure()
            for i, d in enumerate(scn.destinations):
                if not d.spec:
                    continue
                bpl = [s.delivered_q[d.id][t].get("BPL") for t in x]
                qfig.add_scatter(
                    x=x, y=bpl, name=d.id, mode="lines",
                    line=dict(
                        color=theme.MACHINE_COLORS[
                            i % len(theme.MACHINE_COLORS)],
                        width=2),
                    hovertemplate=(
                        f"<b>{d.id}</b><br>Bucket %{{x}}"
                        "<br>BPL %{y:.2f}%<extra></extra>"),
                )
                mn = next(
                    (sp.pmin for sp in d.spec if sp.param == "BPL"), None)
                if mn:
                    qfig.add_hline(
                        y=mn, line_dash="dot", line_color=RED)
            qfig.update_yaxes(title="BPL [%]")
            qfig.update_xaxes(title="bucket")
            qfig.update_layout(
                legend=dict(
                    orientation="h", yanchor="bottom", y=1.0, x=0))
            plot(plotly_layout(
                qfig, "Delivered quality against specification", 330))
        elif p and p.offspec:
            plot(_offspec_figure(p.offspec))
            st.caption(
                "Zero is on-spec; non-zero values are soft-quality "
                "deviations, not delivered tonnage.")

    # ---- current job timeline ------------------------------------------------
    if p and not monitor_compact:
        st.markdown("#### Current job timeline")
        jobs = list(s.jobs.values()) if s else p.jobs
        df = jobs_to_gantt_df(scn, jobs)
        register = theme.timeline_machine_register(scn, p)
        st.caption(
            "Current solver-controlled machines are listed. An empty row "
            "retains the plan's structured idle or modelling-gap reason; "
            "reference/future assets are available in Job Schedule on request.")
        plot(gantt_figure(
            df, color_by="kind", group_by="machine",
            height=theme.timeline_height(register, minimum=340),
            row_register=register, show_tonnage=True))


def _box(fig, x, y, title, sub, color, w=0.5, h=0.32, dash=None,
         fill="#ffffff", tsize=12):
    fig.add_shape(type="rect", x0=x - w, x1=x + w, y0=y - h, y1=y + h,
                  line=dict(color=color, width=2.4, dash=dash), fillcolor=fill)
    fig.add_annotation(x=x, y=y + 0.07, text=f"<b>{title}</b>", showarrow=False,
                       font=dict(size=tsize, color=color))
    if sub:
        fig.add_annotation(x=x, y=y - 0.14, text=sub, showarrow=False,
                           font=dict(size=10, color="#4d5b66"))


def _arrow(fig, x0, y0, x1, y1, label="", color=GREY, dash=None, above=True):
    """Arrow from (x0,y0) pointing AT (x1,y1)."""
    fig.add_annotation(x=x1, y=y1, ax=x0, ay=y0, xref="x", yref="y",
                       axref="x", ayref="y", showarrow=True, arrowhead=2,
                       arrowwidth=3, arrowcolor=color)
    if dash:  # arrows can't dash: overlay a dashed line for "simulated" feel
        fig.add_shape(type="line", x0=x0, x1=x1, y0=y0, y1=y1,
                      line=dict(color=color, width=1, dash=dash))
    if label:
        fig.add_annotation(x=(x0 + x1) / 2, y=(y0 + y1) / 2 + (0.16 if above
                                                               else -0.16),
                           text=label, showarrow=False,
                           font=dict(size=9.5, color=NAVY))


def _corridor_label(scn, link) -> str:
    km = ""
    m = re.search(r"(\d+)\s*km", link.evidence.note or "")
    if m:
        km = f"{m.group(1)} km · "
    hrs = link.transit_buckets * scn.sim.dt_h
    return (f"<b>{link.id}</b> · {km}{link.capacity_tph:,.0f} t/h · "
            f"transit time = {hrs:g} h")


def _inv_t(scn, s, site: str, area_prefix: str = "") -> float:
    tot = 0.0
    for sp in scn.spaces:
        if sp.site == site and (not area_prefix or sp.area.startswith(area_prefix)):
            tot += (s.inv[sp.id] if s else sp.mass_t)
    return tot


def _value_chain_fig(scn, k, mode: str, s=None) -> go.Figure:
    """Scenario-aware Domain schematic. Arrows always point TOWARD M16
    (receiving hub) from the mine source yards (current-report hierarchy)."""
    fig = go.Figure()
    node_site = {n.id: n.site for n in scn.nodes}

    if mode == "sulphur":
        _box(fig, 0, 0, "M17 OPEN STORE",
             f"2 × 100 kt · inv {_inv_t(scn, s, 'MPH', 'M17-open') / 1000:,.0f} kt",
             BLUE)
        _box(fig, 1.6, 0, "M17 CLOSED STORE",
             f"inv {_inv_t(scn, s, 'MPH', 'M17-closed') / 1000:,.0f} kt", BLUE)
        _box(fig, 3.2, 0, "MOLTEN-S", "sulphur melting units · destination",
             DGREEN)
        _arrow(fig, 0.45, 0, 1.15, 0, "manual payloaders 300 t/h (exogenous)",
               GREY, dash="dot")
        _arrow(fig, 2.05, 0, 2.75, 0, "RC01 · 1,100 t/h", GREEN)
        fig.add_annotation(x=1.6, y=-0.75, showarrow=False,
                           font=dict(size=9.5, color=GREY),
                           text="Domain B (M17 sulphur) — separate planning "
                                "domain; no phosphate chain in this scenario")
        fig.update_xaxes(visible=False, range=[-0.6, 3.8])
        fig.update_yaxes(visible=False, range=[-1.0, 0.7])
        return plotly_layout(fig, height=240)

    # destinations at MPH (right column)
    dests = [d for d in scn.destinations if d.site == "MPH"]
    hub_x, hub_y = 1.7, 0.0
    for i, d in enumerate(dests):
        y = 0.9 - 1.8 * i / max(len(dests) - 1, 1) if len(dests) > 1 else 0.0
        sub = "destination · wet process" if d.id.startswith("PAP") else             "dest → drying → dry store → rail → SPH"
        _box(fig, 3.4, y, d.id, sub, DGREEN, w=0.56)
        srv = ""
        if k and d.id in k.get("per_destination", {}):
            srv = f"service {k['per_destination'][d.id]['service_pct']:.0f} %"
        _arrow(fig, hub_x + 0.55, hub_y + (y - hub_y) * 0.35, 2.88, y,
               srv, GREEN, above=(y >= 0))

    hub_inv = _inv_t(scn, s, "MPH", "M16") / 1000
    _box(fig, hub_x, hub_y, "MPH M16 HUB · SMS",
         f"receiving · blending · buffer · inv {hub_inv:,.0f} kt",
         DORANGE, w=0.66, h=0.38, fill="#fdf6ea", tsize=13)

    if mode == "m16-only":
        _box(fig, 0, 0, "SIMULATED corridor-arrival input",
             "mines NOT modelled in S2 — see S3", GREY, w=0.62, dash="dash",
             fill="#f5f6f8")
        _arrow(fig, 0.65, 0, hub_x - 0.65, 0,
               "simulated arrivals (belt-profile input)", GREY, dash="dot")
        fig.update_xaxes(visible=False, range=[-0.75, 4.05])
        fig.update_yaxes(visible=False, range=[-1.45, 1.45])
        return plotly_layout(fig, height=280)

    # three-site: mine source yards feed the hub (arrows point AT M16).
    # Corridor labels live in white boxes clearly OFF the arrow shafts
    # (top corridor above, bottom corridor below), tied by dotted leader lines.
    corridors = [l for l in scn.links if l.transit_buckets > 0]
    mines = [st_ for st_ in scn.sites if st_.role == "mine"]
    ys = {m.id: 1.05 - 2.1 * i / max(len(mines) - 1, 1) if len(mines) > 1
          else 0.0 for i, m in enumerate(mines)}
    for m in mines:
        y = ys[m.id]
        tbc = any(sp.site == m.id and sp.evidence.status in ("ASSUMED", "TBC")
                  for sp in scn.spaces)
        sub = (f"source yard · inv {_inv_t(scn, s, m.id) / 1000:,.0f} kt"
               + (" · planning basis" if m.id == "LOUTA" else
                  " · B02 table · current configuration" if m.id == "BENGUERIR"
                  else " · confirmed (reference pending)" if tbc else ""))
        _box(fig, 0, y, f"{m.id} STOCKYARD · SMS", sub, NAVY, w=0.66, h=0.34,
             tsize=12)
        link = next((l for l in corridors if node_site.get(l.src) == m.id),
                    None)
        if link:
            x0, y0 = 0.65, y
            x1 = hub_x - 0.64
            y1 = hub_y + (0.18 if y > 0 else -0.18)
            _arrow(fig, x0, y0, x1, y1, "", NAVY)        # shaft — no label
            intransit = (sum(pp.mass_t for pp in s.belts
                             if pp.link_id == link.id
                             and pp.arrive_bucket >= s.bucket) if s else
                         sum(bp.mass_t for bp in scn.belt_profile
                             if bp.link_id == link.id))
            lbl = (_corridor_label(scn, link)
                   + f"<br>in transit ≈ {intransit / 1000:,.1f} kt")
            ly = 1.62 if y > 0 else -1.62                # off-shaft position
            fig.add_annotation(x=0.98, y=ly, text=lbl, showarrow=False,
                               font=dict(size=10.5, color=NAVY),
                               bgcolor="rgba(255,255,255,0.97)",
                               bordercolor="#9fb0be", borderwidth=1,
                               borderpad=5)
            fig.add_shape(type="line", x0=0.98,
                          y0=ly + (-0.16 if y > 0 else 0.16),
                          x1=(x0 + x1) / 2, y1=(y0 + y1) / 2,
                          line=dict(color=GREY, width=0.8, dash="dot"))
    # bottom caption (train outlet folded in — keeps the canvas clean)
    train = next((d for d in scn.destinations if d.site == "BENGUERIR"), None)
    cap = (f"Domain A hierarchy ({report_ref()}): the two mine source yards feed "
           "the M16 receiving hub through dedicated corridors C1/C2 — three "
           "peer controlled sites, one coordinated plan")
    if train is not None:
        cap += (f"<br>{train.id}: Benguerir train outlet (planning disabled · "
                "demand "
                f"{train.demand_default_tph:g} t/h)")
    fig.add_annotation(x=1.7, y=-2.16, showarrow=False,
                       font=dict(size=9.5, color=GREY), text=cap)
    fig.update_xaxes(visible=False, range=[-0.75, 4.05])
    fig.update_yaxes(visible=False, range=[-2.4, 1.95])
    return plotly_layout(fig, height=380)


def _site_cards(scn, p, s, k):
    """S3 site-level status cards (Louta / Benguerir / M16 + corridors)."""
    st.markdown("#### Site status — three peer Domain-A sites")
    node_site = {n.id: n.site for n in scn.nodes}
    corridors = {node_site.get(l.src, "?"): l
                 for l in scn.links if l.transit_buckets > 0}
    T = scn.sim.horizon_buckets
    dt = scn.sim.dt_h
    bucket = s.bucket if s else 0
    elapsed_h = max(bucket * dt, dt)

    def dispatch_tph(link) -> tuple[float, str]:
        if s:
            moved = sum(s.moved_by_job.get(j.id, 0.0)
                        for j in s.jobs.values()
                        if j.kind == "dispatch" and j.dest_id == link.id)
            return moved / elapsed_h, "actual"
        if p:
            tot = sum(j.tonnage_t for j in p.jobs
                      if j.kind == "dispatch" and j.dest_id == link.id)
            return tot / (T * dt), "planned avg"
        return 0.0, "no plan"

    def in_transit(link) -> float:
        if s:
            return sum(pp.mass_t for pp in s.belts
                       if pp.link_id == link.id and pp.arrive_bucket >= bucket)
        return sum(bp.mass_t for bp in scn.belt_profile
                   if bp.link_id == link.id)

    def next_arrival(link) -> str:
        if s:
            arr = [pp.arrive_bucket for pp in s.belts
                   if pp.link_id == link.id and pp.arrive_bucket >= bucket]
        else:
            arr = [bp.arrive_bucket for bp in scn.belt_profile
                   if bp.link_id == link.id and bp.arrive_bucket >= 0]
        if not arr:
            return "—"
        return scn.sim.bucket_dt(min(arr)).strftime("%d %b %H:%M")

    def received_tph() -> tuple[float, str]:
        if s:
            moved = sum(s.moved_by_job.get(j.id, 0.0)
                        for j in s.jobs.values() if j.kind == "stack")
            return moved / elapsed_h, "actual"
        if p:
            tot = sum(j.tonnage_t for j in p.jobs if j.kind == "stack")
            return tot / (T * dt), "planned avg"
        return 0.0, "no plan"

    for site_id, note in (("LOUTA", "planning-basis placeholders"),
                          ("BENGUERIR", "B02 table · current configuration")):
        link = corridors.get(site_id)
        c = st.columns(4)
        c[0].metric(f"{site_id} inventory [t]",
                    f"{_inv_t(scn, s, site_id):,.0f}", help=note)
        if link:
            rate, basis = dispatch_tph(link)
            c[1].metric(f"{link.id} dispatch [t/h] ({basis})", f"{rate:,.0f}")
            c[2].metric(f"{link.id} in-transit [t]", f"{in_transit(link):,.0f}")
            c[3].metric(f"{link.id} next arrival", next_arrival(link))
    c = st.columns(4)
    rate, basis = received_tph()
    c[0].metric("M16 hub inventory [t]", f"{_inv_t(scn, s, 'MPH', 'M16'):,.0f}")
    c[1].metric(f"M16 received flow [t/h] ({basis})", f"{rate:,.0f}")
    pd_k = k.get("per_destination", {}) if k else {}
    for i, did in enumerate([d.id for d in scn.destinations
                             if d.site == "MPH"][:2]):
        v = pd_k.get(did, {}).get("service_pct")
        c[2 + i].metric(f"{did} service [%]",
                        f"{v:.1f}" if v is not None else "—")
    st.caption("Evidence: corridor parameters CONFIRMED/DERIVED (TBE/PFD + "
               "datasheet); Benguerir yard figures are confirmed values with "
               "the document reference pending; Louta storage totals and "
               "key equipment are source-backed where stated, while equal "
               "splits, starting fills and grade shares remain explicit "
               "assumptions. The 102MG mine piles use the S3-A25 display-only "
               "100,000 t capacity / 50,000 t t0 baseline; their actual "
               "capacity and live SMS state are not loaded.")


# =========================================================== 8. Results & KPIs
def page_results():
    st.subheader("Results & KPIs")
    scn, p, s = common.sc(), common.plan(), common.sim()
    if p is None:
        st.info("Generate S0 with the sidebar **Generate Plan** action first.")
        return
    k = _kpis()
    basis = "simulation actuals" if s else "plan values (no simulation run yet)"
    st.caption(f"Basis: **{basis}**")

    c = st.columns(6)
    c[0].metric("Demand service [%]", f"{k['service_pct']:.1f}")
    c[1].metric("Unmet demand [t]", f"{k['unmet_t']:,.0f}")
    c[2].metric("Feed interruptions [buckets]", k["feed_interruptions_buckets"])
    c[3].metric("On-spec [%]", f"{k['onspec_pct']:.1f}")
    c[4].metric("Quality give-away [t·unit]", f"{k['giveaway_t_unit']:,.0f}")
    c[5].metric("Throughput [t]", f"{k['throughput_t']:,.0f}")
    c = st.columns(6)
    c[0].metric("Safety-stock violations [buckets]",
                k["safety_stock_violation_buckets"])
    c[1].metric("Changeovers / moves", f"{k['changeovers']:.0f}")
    c[2].metric("Replans", k["replans"])
    c[3].metric("Solver wall [s]", f"{k['solver_wall_s']:.1f}")
    c[4].metric("Plan stability [%]", f"{k['plan_stability_pct']:.0f}")
    c[5].metric("Plan status", k["plan_status"])

    st.markdown("#### Per destination")
    show_df(pd.DataFrame(k["per_destination"]).T.round(1))

    left, right = st.columns(2)
    with left:
        util = k["machine_utilisation_pct"]
        plot(series_fig([theme.machine_display_label(scn, mid) for mid in util],
                        {"%": list(util.values())},
                        "Machine utilisation [%]", "%", "bar", 280))
        if k["travel_m"]:
            plot(series_fig([theme.machine_display_label(scn, mid)
                             for mid in k["travel_m"]],
                            {"m": list(k["travel_m"].values())},
                            "Machine travel [m] (geometry ASSUMED)", "m",
                            "bar", 260))
    with right:
        ru = {l: u for l, u in k["route_utilisation_pct"].items() if u > 0}
        if ru:
            plot(series_fig(list(ru.keys()), {"%": list(ru.values())},
                            "Route utilisation [%] (loaded links)", "%",
                            "bar", 280))
        if k["starts_stops"]:
            plot(series_fig([theme.machine_display_label(scn, mid)
                             for mid in k["starts_stops"]],
                            {"count": list(k["starts_stops"].values())},
                            "Starts / stops per machine", "count", "bar", 260))

    if k.get("quality_breakdown"):
        st.markdown("#### Quality-compliance breakdown")
        st.caption("On-spec = share of (destination × bucket) pairs with "
                   "delivered material inside every limit; zero-delivery "
                   "buckets are not evaluated. Soft limits are penalised "
                   "(w₂), never blocking; a hard-limit violation in actuals "
                   "means an execution deviation. Planning-basis values are "
                   "placeholder "
                   "spec.")
        qdf = pd.DataFrame(k["quality_breakdown"]).drop(columns=["sum_dev"])
        qdf["avg_dev"] = qdf["avg_dev"].round(3)
        qdf["max_dev"] = qdf["max_dev"].round(3)
        qdf["compliance_pct"] = qdf["compliance_pct"].round(1)
        c1, c2 = st.columns(2)
        dsel = c1.multiselect("Destination filter",
                              sorted(qdf["destination"].unique()))
        psel = c2.multiselect("Parameter filter",
                              sorted(qdf["parameter"].unique()))
        if dsel:
            qdf = qdf[qdf["destination"].isin(dsel)]
        if psel:
            qdf = qdf[qdf["parameter"].isin(psel)]
        qdf = qdf.rename(columns={
            "limit_min": "min", "limit_max": "max",
            "compliance_pct": "compliance %",
            "violations": "violation count",
            "avg_dev": "avg dev", "max_dev": "max dev",
            "source": "source / assumption id"})
        qdf["hard"] = qdf["hard"].map({True: "hard", False: "soft"})
        show_df(qdf[["destination", "parameter", "min", "max", "hard",
                     "evidence", "source / assumption id", "evaluated",
                     "compliant", "compliance %", "violation count",
                     "avg dev", "max dev"]])
    if k["quality_deviation_t_unit"] and any(
            v > 0 for v in k["quality_deviation_t_unit"].values()):
        st.caption("Aggregate deviation mass [t·unit outside spec]: "
                   + " · ".join(f"{p_}: {v:,.1f}" for p_, v in
                                k["quality_deviation_t_unit"].items()))

    if k["planned_vs_actual"]:
        st.markdown("#### Planned vs actual (per job)")
        show_df(pd.DataFrame(k["planned_vs_actual"]))

    if p.reasons:
        st.markdown("#### Reason codes")
        for r in p.reasons:
            st.markdown(f"- `{theme.operator_text(r)}`")
    if p.relaxations:
        with st.expander("Suggested relaxations"):
            for r in p.relaxations:
                st.markdown(f"- {theme.operator_text(r)}")

    st.divider()
    if st.button("📌 Snapshot KPIs for comparison"):
        label = f"{st.session_state.scenario_name} @{pd.Timestamp.now():%H:%M:%S}"
        st.session_state.compare[label] = {
            kk: vv for kk, vv in k.items()
            if isinstance(vv, (int, float, str))}
        st.success(f"stored: {label}")


# =========================================================== 13. Comparison
PRESET_HELP = """Suggested comparison studies (run each, snapshot on the
Results page, then compare here):
- **Normal operation** — baseline scenario, no events
- **One MPH reclaimer unavailable** — Simulation Control → machine_failure RC01-A/B
- **Benguerir corridor delayed** — event `delayed_mine_dispatch` on C2 (S3)
- **Train loading competes with corridor** — S3, set BG-TRAIN-PROD demand > 0
- **Low high-grade inventory** — Input Data → reduce TBT pile masses
- **PAP demand increase** — Plan → S0 → raise PAP-1 demand
- **Sulphur reclaimer interruption** — S1 + machine_failure M17-RC01
- **Alternative objective weights** — Advanced → Planning settings → adjust w₁…w₇
"""


def page_compare():
    st.subheader("Scenario Comparison")
    st.caption("Side-by-side comparison of stored KPI snapshots "
               "(each row = one run snapshot).")
    comp = st.session_state.compare
    if not comp:
        st.info("No snapshots yet. Run a plan/simulation, then press "
                "*Snapshot KPIs for comparison* on the Results page.")
        st.markdown(PRESET_HELP)
        return
    df = pd.DataFrame(comp).T
    num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    show_df(df.round(1), height=min(60 + 38 * len(df), 480))
    metric = st.selectbox("Chart metric", num_cols,
                          index=num_cols.index("service_pct")
                          if "service_pct" in num_cols else 0)
    plot(series_fig(list(df.index), {metric: df[metric].tolist()},
                    f"{metric} by snapshot", metric, "bar", 320))
    c1, c2 = st.columns(2)
    if c1.button("Clear snapshots"):
        st.session_state.compare = {}
        st.rerun()
    with c2.expander("Suggested studies"):
        st.markdown(PRESET_HELP)
