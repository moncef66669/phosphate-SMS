"""STOCKYARD LAYOUT & FLOW — redesigned operations console page.

Information architecture (the actual change; the drawing is unchanged):

    condition ribbon        one ranked answer to "is anything wrong?"
    KPI strip               plan outcome + the M16 reception envelope
    operator rail (left)    view · state · time · layers · focus · actions
    process graphic (mid)   full-bleed, framed like a drawing sheet
    inspector (right)       ASSET / CONDITIONS / FLOWS / COVERAGE, always
                            visible instead of collapsed expanders
    drawer (bottom)         conventions, evidence, provenance, digest export

``tests/test_function_parity.py`` is a DELETION GUARD: it proves every
production control still has a counterpart in this source, not that each one
works. Functional evidence is the browser QA in
``generated_outputs/ui_preview_qa/ui-preview-qa.json``, which exercises about a
third of that table. Two controls are CONSOLIDATED rather than removed:

  * the production sidebar "Plan timeline" basis + hour slider and the
    in-page bucket slider become one TIME control in the rail (the same
    session keys are written, so behaviour is identical);
  * the production radio + three separate asset selectboxes become one
    type-tagged ASSET FOCUS selector (Streamlit selectboxes are
    search-as-you-type, so this is strictly more capable).
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional, Sequence, Tuple

import streamlit as st
import streamlit.components.v1 as components

from . import (canvas, conditions, svg_mimic, svg_viewer,
               tokens as t, widgets as w)
from .bridge import (BASIS_OF_STATE, DISPLAY_BADGE, POSITION_NOTE,
                     PROCESS_MIMIC_BASIS_KEY, PROCESS_MIMIC_HOUR_KEY,
                     STATE_BUCKET, STATE_FINAL, STATE_INITIAL, STATE_SIM,
                     STATE_TIMELINE, VIEW_LABELS, MimicOpts,
                     build_process_mimic_state, format_repere,
                     format_repere_range, geometry_for,
                     geometry_model_mismatch,
                     grade_profile_distribution_figure,
                     grade_profile_distribution_rows, hs,
                     process_mimic_figure, site_tag, views_for)

# The production module keeps conveyor-state resolution private. Reusing it
# (rather than re-deriving it) keeps the inspector consistent with the
# drawing; it is imported explicitly so the coupling is visible.
from ui.process_mimic import _conv_state, _machine_vm, _pile_vm   # noqa: E402

#: The drawing switch. SOLID keeps the production ARRANGEMENT and re-renders
#: it as hand-authored SVG with real stroke hierarchy; REFERENCE is the
#: unchanged production Plotly drawing, for side-by-side comparison.
#: (An earlier stream/cross-section re-arrangement was reviewed and rejected —
#: it re-laid the yard out, which lost the validated relative positions. Its
#: code and tests remain in ``flow_diagram.py`` but it is no longer offered.)
DIAG_NEW = "Solid (SVG)"
DIAG_REF = "Production (reference)"
DIAGRAMS = [DIAG_NEW, DIAG_REF]

LAYER_OPTS = ["Flow", "Tags", "Evidence", "Alarms only", "Active only",
              "Future exp."]
LAYER_DEFAULT = ["Flow", "Tags", "Evidence"]

_LAYER_HELP = ("Flow = live/planned material flow · Tags = equipment tags · "
               "Evidence = TBC/ASSUMED markers · Alarms only = suppress "
               "normal-state detail · Active only = dim idle routes · "
               "Future exp. = show the future buildout (never solver "
               "inventory)")


# ---------------------------------------------------------------- controls ---
def _seg(container, label: str, options: Sequence[str], default: str,
         key: str, help: str = "", fmt=None) -> str:
    """Segmented control that cannot return None (deselect falls back)."""
    val = container.segmented_control(
        label, list(options),
        default=default if default in options else options[0],
        key=key, help=help or None,
        format_func=fmt if fmt else (lambda v: v))
    return val if val in options else (default if default in options
                                       else options[0])


def _state_options(plan, sim) -> List[str]:
    opts = [STATE_INITIAL]
    if plan is not None:
        opts += [STATE_TIMELINE, STATE_FINAL, STATE_BUCKET]
    if sim is not None:
        opts.append(STATE_SIM)
    return opts


def _resolve_bucket(sc, plan, sim, state_sel: str, rail) -> Tuple[int, str]:
    """(bucket, caption). Writes the shared session keys the production
    sidebar owns, so the two apps agree on the displayed plan time."""
    horizon = sc.sim.horizon_buckets
    st.session_state[PROCESS_MIMIC_BASIS_KEY] = state_sel
    if state_sel == STATE_INITIAL:
        st.session_state[PROCESS_MIMIC_HOUR_KEY] = 0
        rail.caption("scenario snapshot t0 · slider paused")
        return 0, "t0 · initial inventory boundary"
    if state_sel == STATE_FINAL:
        st.session_state[PROCESS_MIMIC_HOUR_KEY] = int(
            round(horizon * sc.sim.dt_h))
        rail.caption("terminal boundary · no active job at T")
        return horizon, f"T={horizon} · terminal inventory boundary"
    if state_sel == STATE_SIM:
        b = sim.bucket if sim is not None else 0
        rail.caption("latest simulation bucket · slider paused")
        return b, f"simulation bucket {b}"
    if state_sel == STATE_TIMELINE:
        max_h = max(0, int(round(horizon * sc.sim.dt_h)))
        hour = rail.slider("Plan hour", 0, max_h or 1,
                           min(int(st.session_state.get(
                               PROCESS_MIMIC_HOUR_KEY, 0) or 0), max_h),
                           key="cx_hour", format="t = %d h",
                           disabled=max_h == 0,
                           help="t=0 is the initial boundary; t=T the "
                                "terminal boundary. Shared with every view.")
        st.session_state[PROCESS_MIMIC_HOUR_KEY] = hour
        b = max(0, min(horizon, int(round(hour / max(sc.sim.dt_h, 1e-9)))))
        return b, f"plan replay · t = {hour} h · bucket {b}"
    # STATE_BUCKET — engineering replay by bucket index
    b = rail.slider("Plan bucket", 0, max(horizon, 1),
                    min(int(st.session_state.get("cx_bucket", horizon) or 0),
                        horizon),
                    key="cx_bucket",
                    help="engineering replay: inspect one plan bucket")
    st.session_state[PROCESS_MIMIC_HOUR_KEY] = int(round(b * sc.sim.dt_h))
    return b, f"plan bucket {b} of {horizon}"


# ------------------------------------------------------------ asset focus ----
def _asset_options(sc, geo, ms) -> List[Tuple[str, str, str]]:
    """(kind, id, label) for every inspectable asset, machines first."""
    out: List[Tuple[str, str, str]] = []
    known = set()
    for sv in ms.sites.values():
        for m in sv.machines:
            known.add(m.id)
            out.append(("MACHINE", m.id, f"⚙ {site_tag(m.site, m.id)}"))
    for mg in geo.machines:
        if mg.display and mg.machine_id not in known:
            out.append(("DISPLAY", mg.machine_id,
                        f"⚙ {mg.machine_id}  ·  DISPLAY/REFERENCE"))
    for sv in ms.sites.values():
        for p in sv.piles:
            out.append(("PILE", p.id, f"▤ {site_tag(p.site, p.id)}"))
    for c in geo.conveyors:
        if c.planning_enabled:
            out.append(("CONVEYOR", c.cid, f"═ {c.tag or c.cid}"))
    return out


def _machine_rows(sc, geo, ms, state, mid: str,
                  display_only: bool) -> Tuple[List, Dict]:
    mg = next((m for m in geo.machines if m.machine_id == mid), None)
    if display_only:
        return ([
            ("Asset", (mg.label or mid) if mg else mid),
            ("Status", "DISPLAY / REFERENCE geometry"),
            ("Line", mg.rail_id if mg else "—"),
            ("Type", mg.kind if mg else "—"),
            ("Physical tag", (mg.physical_tag or "TBC") if mg else "TBC"),
            ("In planning model",
             "NO — absent from Scenario.machines; no solver jobs, no state"),
            ("Package scope", (mg.package_scope or "—") if mg else "—"),
            ("Planning scope", (mg.planning_scope or "—") if mg else "—"),
            ("Serves (display)", ", ".join(mg.served()) if mg else "—"),
            ("Evidence / note", (mg.note or "—") if mg else "—"),
        ], {"Status": "caut", "In planning model": "caut"})
    vm = _machine_vm(ms, mid)
    mrec = next((m for m in sc.machines if m.id == mid), None)
    if vm is None or mrec is None:
        return ([("Asset", mid), ("Status", "not found")], {})
    pv = state.machine_pos.get(mid)
    if pv is not None and pv.repere is not None:
        position = f"{pv.space} · {format_repere(pv.repere)}"
        if pv.x_m is not None:
            position += f" · x={pv.x_m:g} m"
    else:
        position = f"x={mrec.position_x:g} m"
    down = mid in state.down_machines
    status = "Unavailable (outage)" if down else vm.status
    sev = {"Unavailable (outage)": "alarm", "Unavailable": "caut",
           "Maintenance": "caut"}.get(status, "ok" if vm.job_id else "")
    return ([
        ("Site / line", f"{vm.site} / {mrec.rail}"),
        ("Tag", site_tag(vm.site, vm.id)),
        ("Physical tag", (next((g.physical_tag for g in geo.machines
                                if g.machine_id == mid and g.physical_tag),
                               "") or "—")),
        ("Type", vm.kind),
        ("State", status),
        ("Control mode", "planning mimic — no live control"),
        ("Current job", vm.job_id or "—"),
        ("Position", position),
        ("Work range", format_repere_range(*pv.work_repere)
         if pv is not None and pv.work_repere is not None else "—"),
        ("Rate / max", f"{vm.rate_tph:,.0f} / {vm.rate_max_tph:,.0f} t/h"),
        ("Source / destination",
         (vm.target_space or "—") if vm.kind == "stacker"
         else f"{vm.target_space or '—'} → {vm.dest_id or '—'}"),
        ("Reachable (configured)", ", ".join(vm.reachable) or "—"),
        ("Position basis", POSITION_NOTE),
        ("Evidence", vm.evidence_status),
    ], {"State": sev})


def _pile_rows(sc, state, ms, pid: str) -> Tuple[List, Dict, Optional[list]]:
    p = _pile_vm(ms, pid)
    if p is None:
        return ([("Asset", pid), ("Status", "not found")], {}, None)
    cvm = state.concurrency.get(pid)
    sprec = next((s for s in sc.spaces if s.id == pid), None)
    sev: Dict[str, str] = {}
    if p.below_safety:
        sev["Safety stock"] = "caut"
    if p.offspec_hard:
        sev["Quality"] = "alarm"
    if cvm is not None:
        sev["Concurrency"] = {"violation": "alarm", "warning": "caut",
                              "ok": "ok"}.get(cvm.status, "")
    rows = [
        ("Site", p.site),
        ("Tag", site_tag(p.site, p.id)),
        ("State", p.state),
        ("Mass / capacity", f"{p.mass_t:,.0f} / {p.capacity_t:,.0f} t"),
        ("Fill", f"{p.fill_pct:.0f} %"),
        ("Grade / profile", p.grade_label),
        ("Designated grade", p.designated_grade or "free"),
        ("Material state",
         ((sprec.material_class or "—") + " · " + (sprec.profile_id or "—"))
         if sprec else "—"),
        ("In / out", f"{p.inbound_tph:,.0f} / {p.outbound_tph:,.0f} t/h"
         + (" — CONCURRENT" if cvm else "")),
        ("Concurrency", (f"{cvm.status.upper()} · sep {cvm.sep_m:,.0f} m ≥ "
                         f"min {cvm.req_m:,.0f} m (ASSUMED)")
         if cvm else "—"),
        ("Safety stock", f"{p.safety_stock_t:,.0f} t"
         + (" — BELOW" if p.below_safety else "")),
        ("Hard off-spec", ", ".join(p.offspec_hard) or "none"),
        ("Quality", " · ".join(f"{k} {v:g}" for k, v in p.quality.items())
         or "—"),
        ("Evidence", f"{p.evidence_status} · {p.evidence_source[:60]}"),
    ]
    return rows, sev, None


def _conveyor_rows(sc, geo, state, opts, cid: str) -> Tuple[List, Dict]:
    cg = next((c for c in geo.conveyors if c.cid == cid), None)
    if cg is None:
        return ([("Asset", cid), ("Status", "not found")], {})
    cstate, tph, cap, _, _ = _conv_state(state, cg, opts)
    li0 = next((l for l in sc.links
                if l.id == (cg.link_ids[0] if cg.link_ids else "")), None)
    sev = {"State": {"active": "ok", "tripped": "alarm",
                     "future": "", "idle": ""}.get(cstate, "")}
    rows = [
        ("Tag", cg.tag or cg.cid),
        ("Model links", ", ".join(cg.link_ids)
         or "display-only (planning_enabled = False)"),
        ("State", cstate),
        ("Flow / capacity", f"{tph:,.0f} / {cap:,.0f} t/h"
         if tph is not None and cap else "—"),
        ("Utilisation", f"{100.0 * tph / cap:.0f} %"
         if tph is not None and cap else "—"),
        ("Shared group", (li0.shared_group or "—") if li0 else "—"),
        ("Conflict group", (li0.conflict_group or "—") if li0 else "—"),
        ("Gate", (li0.gate_id or "—") if li0 else "—"),
        ("Changeover", (f"{li0.changeover_min:g} min"
                        + (" — documented, NOT modelled"
                           if li0.changeover_min == 0 else ""))
         if li0 else "—"),
        ("Transit", f"{li0.transit_buckets * sc.sim.dt_h:g} h"
         if li0 and li0.transit_buckets else "—"),
        ("Evidence", li0.evidence.status if li0 else "TBC"),
    ]
    return rows, sev


# ------------------------------------------------------------------- KPIs ----
def _reception_envelopes(sc, state) -> List[Tuple[str, float, float]]:
    groups: Dict[str, list] = {}
    for l in sc.links:
        if l.shared_group:
            groups.setdefault(l.shared_group, []).append(l)
    out = []
    for g, links in sorted(groups.items()):
        cap = min(l.capacity_tph for l in links)
        tph = sum(state.flows[l.id].tph for l in links if l.id in state.flows)
        out.append((g, tph, cap))
    return out


def _kpis(sc, plan, sim, state, conds) -> List[w.Kpi]:
    items: List[w.Kpi] = []
    n = conditions.counts(conds)
    items.append(w.Kpi(
        "Plan status", plan.status if plan is not None else "NO PLAN",
        sub=(f"{len(plan.jobs)} jobs · obj {plan.objective:,.0f}"
             if plan is not None else "generate a plan to populate KPIs"),
        tone=("ok" if plan is not None and plan.status in ("OPTIMAL",
                                                          "FEASIBLE")
              else "caut")))
    if plan is not None:
        from .bridge import compute_kpis
        k = compute_kpis(sc, plan, sim)
        svc = float(k["service_pct"])
        ons = float(k["onspec_pct"])
        unmet = float(k["unmet_t"])
        items += [
            w.Kpi("Demand service", f"{svc:.1f}", "%",
                  sub=f"{k['feed_interruptions_buckets']} feed-gap buckets",
                  tone="ok" if svc >= 99.95 else
                       "caut" if svc >= 95 else "alarm",
                  bar_pct=svc),
            w.Kpi("On-spec", f"{ons:.1f}", "%",
                  sub=("principal driver: "
                       + (f"{k['onspec_principal']['destination']} "
                          f"{k['onspec_principal']['parameter']}"
                          if k.get("onspec_principal") else "none")),
                  tone="ok" if ons >= 99.95 else
                       "caut" if ons >= 95 else "alarm",
                  bar_pct=ons),
            w.Kpi("Unmet demand", w.fmt_t(unmet),
                  sub="cumulative over the evaluated horizon",
                  tone="ok" if unmet <= 1e-6 else "alarm"),
            w.Kpi("Throughput", w.fmt_t(float(k["throughput_t"])),
                  sub=f"solver {k['solver_wall_s']:.1f} s · "
                      f"{k['evaluated_buckets']} buckets", tone="info"),
            w.Kpi("Safety breaches",
                  f"{k['safety_stock_violation_buckets']}",
                  sub="space × bucket below safety stock",
                  tone="ok" if not k["safety_stock_violation_buckets"]
                       else "caut"),
        ]
    for g, tph, cap in _reception_envelopes(sc, state):
        head = max(0.0, cap - tph)
        pct = 100.0 * tph / cap if cap else 0.0
        items.append(w.Kpi(
            f"Envelope {g}", f"{head:,.0f}", "t/h free",
            sub=f"{tph:,.0f} of {cap:,.0f} t/h shared",
            tone="alarm" if tph > cap + 1e-6 else
                 "caut" if pct > 95 else "ok",
            bar_pct=pct))
    items.append(w.Kpi(
        "Conditions", f"{n[conditions.ALARM]}/{n[conditions.CAUTION]}",
        sub="alarm / caution at the displayed bucket",
        tone="alarm" if n[conditions.ALARM] else
             "caut" if n[conditions.CAUTION] else "ok"))
    return items


# ------------------------------------------------------------------ legend ---
def _asset_legend() -> str:
    """Rebuilt from the production ASSET_STATUS_STYLES so the semantics stay
    single-source while the markup belongs to this console."""
    out = []
    for key in ("ACTIVE", "IDLE", "UNAVAILABLE", "FUTURE"):
        s = hs.ASSET_STATUS_STYLES[key]
        tone = {"ACTIVE": "ok", "IDLE": "mute", "UNAVAILABLE": "alarm",
                "FUTURE": "info"}[key]
        out.append(w.chip(s["label"], icon=s["icon"], tone=tone))
    return w.chips(out)


CONVENTIONS = (
    "<b>Drawing conventions</b> — pile fill = material grade · machine body "
    "is neutral and its lamp carries equipment state · belt chevrons carry "
    "conveyor state and flow direction · blue outline = selected · amber = "
    "maintenance/warning · dashed = TBC/ASSUMED or reference-only · work-zone "
    "brackets + hatched band = concurrent stack/reclaim zones with the "
    "protected buffer (green sep OK · amber approaching limit · red overlap) "
    "· S/BS/A = sampler / belt scale / analyser (positions TBC) · process-line "
    "cards are static configuration status (● OPERATING · ◇ FUTURE), not live "
    "equipment state. Every colour is duplicated by an icon, pattern or text.")


# ================================== render ===================================
def render(sc, plan, sim, issues) -> None:
    # Drill-through queue. The View control is a KEYED widget created below in
    # the operator rail; assigning `cx_view` after that raises
    # StreamlitAPIException (the same hazard `ui.common.goto_page` documents
    # for the production sidebar radios). Buttons therefore queue the target
    # and it is applied here, before the widget exists.
    pending_view = st.session_state.pop("_cx_pending_view", None)
    ms_views = views_for(sc)
    if pending_view in ms_views:
        st.session_state["cx_view"] = pending_view

    ribbon_slot = st.container()
    kpi_slot = st.container()
    # The drawing is the primary object on this page, so it gets the width.
    rail_col, main_col, insp_col = st.columns([0.95, 5.15, 1.45],
                                              gap="small")

    # ------------------------------------------------------------- rail ----
    with rail_col:
        w.panel_head("Operator", "view · state · layers")
        view = _seg(st, "View", ms_views,
                    st.session_state.get("cx_view", ms_views[0]),
                    "cx_view", fmt=lambda v: VIEW_LABELS.get(v, v),
                    help="a single-site scenario exposes one view")
        state_opts = _state_options(plan, sim)
        state_sel = _seg(st, "State", state_opts,
                         st.session_state.get("cx_state", state_opts[0]),
                         "cx_state",
                         fmt=lambda v: {STATE_INITIAL: "Initial",
                                        STATE_TIMELINE: "Timeline",
                                        STATE_FINAL: "Final",
                                        STATE_BUCKET: "Bucket",
                                        STATE_SIM: "Sim"}.get(v, v),
                         help="Initial = t0 · Timeline = hourly replay · "
                              "Final = inventory[T] · Bucket = engineering "
                              "replay · Sim = latest simulation bucket")
        bucket, time_caption = _resolve_bucket(sc, plan, sim, state_sel, st)

        layers = st.pills("Layers", LAYER_OPTS,
                          selection_mode="multi",
                          default=st.session_state.get("cx_layers",
                                                       LAYER_DEFAULT),
                          key="cx_layers", help=_LAYER_HELP)
        layers = list(layers or [])

        grades = st.multiselect("Grade filter", [g.id for g in sc.grades],
                                default=[], key="cx_grades",
                                help="show only piles/flows of these grades")

        state = build_process_mimic_state(
            sc, plan, sim, basis=BASIS_OF_STATE[state_sel], bucket=bucket)
        jobs_by_id = {j.id: j for j in state.jobs}
        job_ids = ["—"] + list(jobs_by_id)
        sel_job = st.selectbox(
            "Job path highlight", job_ids, key="cx_job",
            format_func=lambda jid: jid if jid == "—" else
            (f"{jid} · {jobs_by_id[jid].space} · "
             f"{jobs_by_id[jid].repere_label} · "
             f"{jobs_by_id[jid].tonnage_t / 1000:,.1f} kt"))
        sel_job = "" if sel_job == "—" else sel_job

        geo = geometry_for(sc, view,
                           show_future="Future exp." in layers)
        assets = _asset_options(sc, geo, state.monitor)
        by_label = {lbl: (kind, aid) for kind, aid, lbl in assets}
        focus_labels = ["—"] + list(by_label)
        prev = st.session_state.get("cx_focus", "—")
        focus_lbl = st.selectbox(
            "Asset focus", focus_labels,
            index=focus_labels.index(prev) if prev in focus_labels else 0,
            key="cx_focus",
            help="type to search any machine, stockpile or conveyor — the "
                 "inspector and the drawing follow this selection")
        focus_kind, focus_id = by_label.get(focus_lbl, ("", ""))

        b1, b2 = st.columns(2)
        if b1.button("Fit view", key="cx_fit", width="stretch",
                     help="reset zoom/pan on the graphic"):
            st.session_state["cx_zoom"] = \
                st.session_state.get("cx_zoom", 0) + 1
        if b2.button("Clear sel.", key="cx_clear", width="stretch",
                     help="clear the job and asset selection"):
            for k in ("cx_job", "cx_focus"):
                st.session_state.pop(k, None)
            st.rerun()

        diagram = _seg(st, "Diagram", DIAGRAMS,
                       st.session_state.get("cx_diagram", DIAG_NEW),
                       "cx_diagram",
                       fmt=lambda v: {DIAG_NEW: "Solid", DIAG_REF: "Ref."}[v],
                       help="Solid = the SAME arrangement re-drawn in SVG "
                            "with solid line work · Ref. = the current "
                            "production drawing, unchanged, for comparison")
        skin = _seg(st, "Canvas", canvas.MODES,
                    st.session_state.get("cx_skin", canvas.DOCUMENT),
                    "cx_skin",
                    help="Document = ISA-101 light-grey drawing sheet "
                         "(reference rendering) · Control room = dark skin")
        density = _seg(st, "Graphic height", list(t.GRAPHIC_HEIGHT),
                       st.session_state.get("cx_density", "Standard"),
                       "cx_density",
                       fmt=lambda v: {"Comfortable": "Tall",
                                      "Standard": "Std",
                                      "Dense": "Short"}[v],
                       help="Tall 760 px · Std 660 px · Short 560 px — pick "
                            "Short on a laptop so the inspector stays in "
                            "view")

    opts = MimicOpts(
        show_flows="Flow" in layers,
        show_tags="Tags" in layers,
        show_evidence="Evidence" in layers,
        alarms_only="Alarms only" in layers,
        active_flows_only="Active only" in layers,
        grade_filter=list(grades),
        selected_job=sel_job,
        selected_asset=focus_id,
        phase=state.bucket)

    stale = geometry_model_mismatch(sc, geo)
    gaps = list(geo.gaps) + list(state.monitor.gaps)
    conds = conditions.collect(sc, state, issues=issues, stale=stale,
                               geometry_gaps=len(gaps))
    n = conditions.counts(conds)

    # ---------------------------------------------------------- ribbon -----
    with ribbon_slot:
        w.condition_ribbon([
            w.RibbonCell("alarm", n[conditions.ALARM], conditions.ALARM),
            w.RibbonCell("caution", n[conditions.CAUTION],
                         conditions.CAUTION),
            w.RibbonCell("advisory", n[conditions.ADVISORY],
                         conditions.ADVISORY),
        ], message=conditions.headline(conds))
        if stale:
            st.error(
                "**Stale scenario graph — flows are not shown.** This drawing "
                "is the corrected layout, but the loaded scenario is "
                "missing/still carrying: " + ", ".join(stale[:8])
                + ". Re-load the built-in scenario (or re-import a current "
                  "snapshot) so the plan and the mimic describe the same "
                  "yard.", icon="⛔")
    with kpi_slot:
        w.kpi_strip(_kpis(sc, plan, sim, state, conds))

    # ---------------------------------------------------------- graphic ----
    with main_col:
        if state.monitor.message:
            w.frame_head(f"{VIEW_LABELS.get(view, view)} — no drawing")
            st.info(state.monitor.message)
        else:
            w.frame_head(
                f"{VIEW_LABELS.get(view, view)} · layout & flow",
                [w.chip("diagram",
                        value="SOLID" if diagram == DIAG_NEW
                        else "REFERENCE",
                        tone="info" if diagram == DIAG_NEW else "mute"),
                 w.chip("basis", value=state_sel, tone="info"),
                 w.chip("time", value=time_caption, mono=True),
                 w.chip(state.timestamp, tone="mute", mono=True),
                 w.chip("geometry", value="schematic — not to scale",
                        tone="mute")])
            body = st.container(
                key="cx_graphic_dark" if skin == canvas.CONTROL_ROOM
                else "cx_graphic")
            if diagram == DIAG_NEW:
                dark = skin == canvas.CONTROL_ROOM
                svg, _ = svg_mimic.build(sc, state, geo, opts, dark=dark,
                                         basis=state_sel)
                # components.html cannot auto-size, so the viewer gets a fixed
                # frame and the SHEET pans/zooms inside it. Label detail tiers
                # reveal as the operator zooms in — see hmi/svg_viewer.py.
                with body:
                    components.html(
                        svg_viewer.html(svg,
                                        height=t.VIEWER_H.get(density, 660),
                                        dark=dark),
                        height=t.VIEWER_H.get(density, 660) + 8,
                        scrolling=False)
                    lg, lg_ar = svg_mimic.legend_svg(dark, px_width=1140)
                    components.html(
                        f'<div style="margin:0;background:'
                        f'{"#131a20" if dark else "#eef1f3"}">{lg}</div>',
                        height=int(1140 * lg_ar) + 8, scrolling=False)
            else:
                fig = process_mimic_figure(state, geo, opts)
                canvas.restyle(fig, skin,
                               height=t.GRAPHIC_HEIGHT.get(density, 660))
                body.plotly_chart(
                    fig, width="stretch", config=canvas.PLOT_CONFIG,
                    key=f"cx_fig_{view}_{diagram[:3]}_{skin}_{density}_"
                        f"{st.session_state.get('cx_zoom', 0)}")
            st.markdown(
                '<div class="cx-note">'
                + "&nbsp;&nbsp;".join([
                    _asset_legend(),
                    w.chip("scroll = zoom", tone="mute"),
                    w.chip("drag = pan", tone="mute"),
                    w.chip("double-click = reset", tone="mute")])
                + "</div>", unsafe_allow_html=True)

            if len(ms_views) > 1:
                cols = st.columns(len(ms_views) - 1)
                others = [v for v in ms_views if v != view]
                for i, v in enumerate(others):
                    if cols[i].button(f"Open {VIEW_LABELS.get(v, v)}",
                                      key=f"cx_go_{v}", width="stretch"):
                        st.session_state["_cx_pending_view"] = v
                        st.rerun()

            dist = grade_profile_distribution_rows(state, geo)
            if dist and view in ("LOUTA", "BENGUERIR"):
                with st.expander("Grade-profile distribution "
                                 f"({len(dist)} rows)", expanded=False):
                    st.caption(
                        "Starting pile composition by mass. Assumed bands are "
                        "schematic. Raw/ore and washed-product profiles are "
                        "separate material states — never compare or "
                        "substitute them.")
                    dfig = grade_profile_distribution_figure(dist)
                    if dfig is not None:
                        canvas.restyle(dfig, skin, height=260)
                        st.plotly_chart(
                            dfig, width="stretch",
                            config={"displaylogo": False,
                                    "displayModeBar": False},
                            key=f"cx_dist_{view}_{skin}")

    # -------------------------------------------------------- inspector ----
    with insp_col:
        tabs = st.tabs(["ASSET", f"COND · {len(conds)}", "FLOWS", "COVERAGE"])

        with tabs[0]:
            if not focus_id:
                w.panel_head("Asset", "none selected")
                st.markdown('<div class="cx-list"><div class="cx-empty">'
                            "pick an asset in ASSET FOCUS — the drawing "
                            "highlights it</div></div>",
                            unsafe_allow_html=True)
                w.panel_head("Scenario", state.basis)
                w.kv_table([
                    ("Scenario", state.monitor.scenario_name.split(" —")[0]),
                    ("Sites", ", ".join(sorted(state.monitor.sites))),
                    ("Displayed bucket", f"{state.bucket} / "
                                         f"{state.monitor.max_bucket}"),
                    ("Active jobs", f"{len(state.jobs)}"),
                    ("Concurrent piles", f"{len(state.concurrency)}"),
                    ("Control mode", "planning / monitoring — no commands"),
                ])
            elif focus_kind in ("MACHINE", "DISPLAY"):
                w.panel_head("Machine", focus_id)
                rows, sev = _machine_rows(sc, geo, state.monitor, state,
                                          focus_id,
                                          focus_kind == "DISPLAY")
                w.kv_table(rows, severities=sev,
                           text_cols={"Position basis", "Evidence / note",
                                      "In planning model", "Control mode"})
            elif focus_kind == "PILE":
                w.panel_head("Stockpile", focus_id)
                rows, sev, _ = _pile_rows(sc, state, state.monitor, focus_id)
                w.kv_table(rows, severities=sev,
                           text_cols={"Evidence", "Material state"})
                series = (plan.inventory.get(focus_id)
                          if plan is not None else None)
                if series:
                    sp = next((s for s in sc.spaces if s.id == focus_id), None)
                    st.markdown(
                        '<div class="cx-panel-h"><b>Inventory trend</b>'
                        f'<span>{len(series)} buckets</span></div>'
                        + w.sparkline(
                            series, width=240, marker_at=min(
                                state.bucket, len(series) - 1),
                            baseline=(sp.safety_stock_t if sp
                                      and sp.safety_stock_t > 0 else None)),
                        unsafe_allow_html=True)
                    w.note(f"min <b>{w.fmt_t(min(series))}</b> · "
                           f"max <b>{w.fmt_t(max(series))}</b> · "
                           f"end <b>{w.fmt_t(series[-1])}</b>"
                           + (" · amber dash = safety stock"
                              if sp and sp.safety_stock_t > 0 else ""))
            else:
                w.panel_head("Conveyor", focus_id)
                rows, sev = _conveyor_rows(sc, geo, state, opts, focus_id)
                w.kv_table(rows, severities=sev,
                           text_cols={"Model links", "Changeover"})

        with tabs[1]:
            w.panel_head("Conditions",
                         f"{n[conditions.ALARM]}A / "
                         f"{n[conditions.CAUTION]}C / "
                         f"{n[conditions.ADVISORY]}I")
            w.condition_list(conditions.rows(conds),
                             empty="all monitored conditions normal")
            w.panel_head("Counters", "displayed bucket")
            w.kv_table([
                ("Pile warnings",
                 str(sum(len(p.warnings) for sv in state.monitor.sites.values()
                         for p in sv.piles))),
                ("Machines down / maint.",
                 str(sum(1 for sv in state.monitor.sites.values()
                         for m in sv.machines
                         if m.status in ("Unavailable", "Maintenance")))),
                ("Tripped links",
                 str(sum(1 for f in state.flows.values() if f.tripped))),
                ("Concurrency alerts",
                 str(sum(1 for c in state.concurrency.values()
                         if c.status != "ok"))),
                ("Concurrent piles",
                 f"{len(state.concurrency)} (sep ≥ min: "
                 f"{sum(1 for c in state.concurrency.values() if c.status == 'ok')})"),
            ])

        with tabs[2]:
            envs = _reception_envelopes(sc, state)
            if envs:
                w.panel_head("Shared envelopes", "sum-capacity groups")
                w.kv_table([(g, f"{tph:,.0f} / {cap:,.0f} t/h "
                                f"({100.0 * tph / cap if cap else 0:.0f} %)")
                            for g, tph, cap in envs])
            active = sorted(
                ((f.link_id, f) for f in state.flows.values()
                 if f.running or f.tripped),
                key=lambda kv: -kv[1].tph)
            w.panel_head("Links carrying flow", f"{len(active)} active")
            if active:
                w.kv_table(
                    [(lid, f"{f.tph:,.0f} / {f.cap_tph:,.0f} t/h"
                           f"{'  TRIPPED' if f.tripped else ''}")
                     for lid, f in active[:24]],
                    severities={lid: ("alarm" if f.tripped else "ok")
                                for lid, f in active[:24]})
            else:
                st.markdown('<div class="cx-list"><div class="cx-empty">'
                            "no link carries flow at this bucket</div></div>",
                            unsafe_allow_html=True)
            # Louta C1 feeder selection — either product reclaimer may feed C1
            feeders = [m for m in sc.machines
                       if m.site == "LOUTA" and m.kind == "reclaimer"]
            if len(feeders) > 1 and any(l.id == "C1" for l in sc.links):
                w.panel_head("C1 feeder selection", "Louta product reclaim")
                rows = []
                for m in feeders:
                    j = next((j for j in state.jobs if j.machine == m.id),
                             None)
                    if j is not None:
                        rows.append((site_tag("LOUTA", m.id),
                                     f"ACTIVE — {j.rate_tph:,.0f} t/h → C1"))
                    else:
                        vm = _machine_vm(state.monitor, m.id)
                        s = vm.status if vm else "Idle"
                        if m.id in state.down_machines:
                            s = "Unavailable"
                        rows.append((site_tag("LOUTA", m.id), {
                            "Maintenance": "MAINTENANCE — calendar window",
                            "Unavailable": "UNAVAILABLE — outage",
                        }.get(s, "available (standby)")))
                c1 = state.flows.get("C1")
                rows.append(("C1 corridor",
                             f"{c1.tph:,.0f} / {c1.cap_tph:,.0f} t/h"
                             if c1 else "—"))
                rows.append(("Feeder policy",
                             "either reclaimer (3,000 t/h nom each) · "
                             "simultaneous partial rate TBC"))
                w.kv_table(rows)

        with tabs[3]:
            w.panel_head("Model coverage", f"{len(gaps)} gaps")
            st.caption("Geometry is schematic — topology per the reviewed "
                       "layout, metric positions TBC (no approved GA/plot "
                       "plan). Display-only assets are never solver "
                       "resources.")
            if gaps:
                import pandas as pd
                st.dataframe(
                    pd.DataFrame([{"site": g[0], "item": g[1],
                                   "status": g[2]} for g in gaps]),
                    width="stretch", height=280, hide_index=True)
            if state.monitor.discrepancies:
                st.error("Coverage discrepancies: "
                         + " · ".join(state.monitor.discrepancies))
            w.panel_head("Geometry source", geo.site_id)
            w.kv_table([("Title", geo.title),
                        ("Source", geo.source[:120]),
                        ("Not to scale", "yes" if geo.not_to_scale else "no"),
                        ("Piles drawn", str(len(geo.piles))),
                        ("Machine lines", str(len(geo.rails))),
                        ("Machines drawn", str(len(geo.machines))),
                        ("Conveyors drawn", str(len(geo.conveyors)))],
                       text_cols={"Title", "Source"})

    # ----------------------------------------------------------- drawer ----
    _drawer(sc, plan, state, geo, conds, view, state_sel, skin, gaps)


def _digest(sc, plan, state, geo, conds, view: str, basis: str) -> dict:
    """Machine-readable snapshot of exactly what the console is showing."""
    return {
        "view": view,
        "basis": basis,
        "bucket": state.bucket,
        "timestamp": state.timestamp,
        "scenario": state.monitor.scenario_name,
        "plan": None if plan is None else {
            "status": plan.status, "jobs": len(plan.jobs),
            "objective": plan.objective,
            "signature": getattr(plan, "signature", ""),
        },
        "geometry": {"site": geo.site_id, "piles": len(geo.piles),
                     "rails": len(geo.rails),
                     "machines": len(geo.machines)},
        "flows": {lid: {"tph": f.tph, "cap_tph": f.cap_tph,
                        "running": f.running, "tripped": f.tripped}
                  for lid, f in sorted(state.flows.items())},
        "shared_envelopes": [{"group": g, "tph": tph, "cap_tph": cap}
                             for g, tph, cap in _reception_envelopes(
                                 sc, state)],
        "conditions": [{"severity": c.severity, "tag": c.tag,
                        "text": c.text, "source": c.source} for c in conds],
    }


def _drawer(sc, plan, state, geo, conds, view, basis, skin, gaps) -> None:
    from .bridge import engine_versions, report_ref
    v = engine_versions()
    cfg = getattr(sc, "config", {}) or {}

    with st.expander("Conventions · evidence · provenance · export",
                     expanded=False):
        c1, c2 = st.columns([2.3, 1])
        with c1:
            w.note(CONVENTIONS)
            w.note("<b>Console conventions</b> — chrome is graphite so it "
                   "never competes with the drawing; saturated red/amber are "
                   "reserved for abnormal conditions only; the "
                   "<b>Document</b> canvas is the reference rendering "
                   "(ISA-101 light grey) and is produced by the production "
                   "figure builder unchanged. <b>Control room</b> applies a "
                   "bounded colour transform and is a presentation "
                   "experiment.")
            w.note(state.acs_note)
            if "BEN_B02_OPTION_ENABLED" in cfg:
                if cfg.get("BEN_B02_OPTION_ENABLED"):
                    w.note(
                        "<b>Benguerir B02 current configuration</b> — the "
                        "project team accepts the table as current; '**' is "
                        "source notation only (ST3/RC2/RC3, "
                        "Q80130-BEN-B02-ME-BOD-0001 Rev B) · raw→C2 "
                        "conditional route <b>"
                        + ("ENABLED" if cfg.get("BEN_RAW_TO_C2_ENABLED")
                           else "disabled")
                        + "</b> · washing-line display baseline: Line 1 "
                          "OPERATING, Lines 2–3 FUTURE (numeric↔A/B/C and "
                          "as-built reconciliation TBC)")
                else:
                    w.note("Benguerir B02 current configuration <b>DISABLED "
                           "for this non-baseline sensitivity</b> — machines "
                           "and routes are reference-only (dashed, no jobs)")
        with c2:
            w.panel_head("Provenance", "read-only")
            w.kv_table([
                ("Geometry revision", v["geometry_revision"]),
                ("Baseline revision", v["baseline_revision"]),
                ("Evidence basis", report_ref("§16")),
                ("Streamlit", v["streamlit"]),
                ("Plotly", v["plotly"]),
                ("Engine path", v["simulator_root"]),
            ])
            st.download_button(
                "Download state digest (JSON)",
                data=json.dumps(_digest(sc, plan, state, geo, conds, view,
                                        basis), indent=2, default=str),
                file_name=f"console-digest-{view.lower()}-"
                          f"b{state.bucket}.json",
                mime="application/json", width="stretch",
                key="cx_digest",
                help="exactly what this screen is showing — flows, shared "
                     "envelopes and every ranked condition")
