"""Sectional, model-backed availability controls for Configure.

The flow drawing remains a view of the scenario.  This module adds a thin
interaction layer: a selected diagram element can change only an availability
field/configuration that the planner actually reads.  Reference and future
geometry stays selectable for explanation, but is deliberately read-only.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

import plotly.graph_objects as go
import streamlit as st

from mph_sim.domain import Scenario
from mph_sim.flow_config import (
    disabled_process_lines,
    effective_corridor_targets,
    process_line_modelled,
    set_process_line_enabled,
)

from . import hmi_symbols as hs
from . import theme
from .layout_geometry import ConveyorGeo, MachineGeo, SiteGeometry, geometry_for
from .process_mimic import (
    MimicOpts,
    build_process_mimic_state,
    configured_mimic_geometry,
    machine_draw_pos,
    process_mimic_figure,
)


SECTION_LABELS = {
    "LOUTA": "Louta",
    "BENGUERIR": "Benguerir",
    "MPH": "MPH",
}


@dataclass(frozen=True)
class FlowConfigAsset:
    """One selectable item in the sectional Configure diagram."""

    kind: str
    asset_id: str
    label: str
    configurable: bool
    enabled: Optional[bool]
    members: Tuple[str, ...] = ()
    note: str = ""
    partial: bool = False

    @property
    def key(self) -> str:
        return f"{self.kind}|{self.asset_id}"

    @property
    def status(self) -> str:
        if not self.configurable:
            return "REFERENCE ONLY"
        if self.partial:
            return "PARTLY DISABLED"
        return "ENABLED" if self.enabled else "DISABLED"


def _machine_asset(sc: Scenario, mg: MachineGeo) -> FlowConfigAsset:
    machine = next((m for m in sc.machines if m.id == mg.machine_id), None)
    if machine is None:
        return FlowConfigAsset(
            "MACHINE", mg.machine_id, mg.label or mg.machine_id,
            False, None, note="Drawn reference equipment; not a planner resource.")
    lifecycle = str(getattr(machine, "lifecycle", "EXISTING"))
    planning_enabled = bool(getattr(machine, "planning_enabled", True))
    configurable = (
        not mg.display and not mg.future and planning_enabled
        and lifecycle != "FUTURE"
    )
    note = (
        "Operational availability used by the planner."
        if configurable else
        "Lifecycle/planning scope is fixed here; this item is not switchable."
    )
    return FlowConfigAsset(
        "MACHINE", machine.id, mg.label or machine.id,
        configurable, bool(machine.available), note=note)


def _route_asset(sc: Scenario, cg: ConveyorGeo) -> FlowConfigAsset:
    links = [next((link for link in sc.links if link.id == link_id), None)
             for link_id in cg.link_ids]
    links = [link for link in links if link is not None]
    all_members_exist = len(links) == len(cg.link_ids)
    configurable = bool(
        cg.link_ids and links and all_members_exist
        and cg.planning_enabled and not cg.future
        and all(str(getattr(link, "lifecycle", "EXISTING")) != "FUTURE"
                for link in links)
    )
    available = [bool(link.available) for link in links]
    enabled = all(available) if available else None
    partial = bool(available) and any(available) and not all(available)
    label = cg.tag or cg.sub or cg.cid
    note = (
        f"Controls {len(links)} planner link"
        + ("" if len(links) == 1 else "s") + "."
        if configurable else
        "Display/reference route; no complete switchable planner path is bound."
    )
    return FlowConfigAsset(
        "ROUTE", cg.cid, label, configurable, enabled,
        tuple(link.id for link in links), note, partial)


def _line_asset(sc: Scenario, line) -> FlowConfigAsset:
    configurable = (
        line.status in ("OPERATING", "DISABLED")
        and process_line_modelled(line.line_id))
    enabled = (
        line.line_id not in disabled_process_lines(sc)
        if configurable else None)
    note = (
        "Controls every solver-backed stage owned by this campaign line. "
        "Feed demand is retained and is reported unmet while disabled."
        if configurable else
        "This line is lifecycle/reference geometry only; no solver stage is bound."
    )
    return FlowConfigAsset(
        "LINE", line.line_id, line.label, configurable, enabled, note=note)


def flow_config_assets(sc: Scenario, geo: SiteGeometry) -> List[FlowConfigAsset]:
    """Return diagram-order assets, including read-only reference items."""
    return (
        [_line_asset(sc, line) for line in geo.process_lines]
        + [_machine_asset(sc, machine) for machine in geo.machines]
        + [_route_asset(sc, route) for route in geo.conveyors]
    )


def set_flow_asset_enabled(
        sc: Scenario, asset: FlowConfigAsset, enabled: bool) -> None:
    """Apply one reversible operational switch to the scenario."""
    if not asset.configurable:
        raise ValueError(f"{asset.asset_id} is reference-only")
    if asset.kind == "MACHINE":
        sc.machine(asset.asset_id).available = bool(enabled)
        return
    if asset.kind == "ROUTE":
        for link_id in asset.members:
            sc.link(link_id).available = bool(enabled)
        return
    if asset.kind == "LINE":
        set_process_line_enabled(sc, asset.asset_id, bool(enabled))
        return
    raise ValueError(f"unsupported flow asset kind: {asset.kind}")


def _configuration_geometry(sc: Scenario, geo: SiteGeometry) -> SiteGeometry:
    """Copy geometry and apply operational line state for rendering only."""
    view = getattr(geo, "site_id", "")
    if view in SECTION_LABELS:
        return configured_mimic_geometry(sc, view)
    configured = deepcopy(geo)
    disabled = disabled_process_lines(sc)
    for line in configured.process_lines:
        if line.line_id in disabled:
            line.status = "DISABLED"
    return configured


def _route_hit_points(route: ConveyorGeo) -> Iterable[Tuple[float, float]]:
    """Vertices plus segment midpoints make the drawn path easy to click."""
    if not route.pts:
        return ()
    points: List[Tuple[float, float]] = list(route.pts)
    points.extend(
        ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
        for (x0, y0), (x1, y1) in zip(route.pts, route.pts[1:])
    )
    return points


def _machine_hit_point(
        sc: Scenario, geo: SiteGeometry, mg: MachineGeo
) -> Optional[Tuple[float, float]]:
    anchors = getattr(geo, "_display_anchor", {})
    if mg.machine_id in anchors:
        return tuple(anchors[mg.machine_id])
    rail = geo.rail(mg.rail_id) if mg.rail_id else None
    if rail is None:
        return None
    served = geo.pile(mg.served()[0]) if len(mg.served()) == 1 else None
    x_norm = (
        min(max(mg.home_repere / 1000.0, 0.0), 1.0)
        if mg.display and served is not None else None)
    gx, gy, *_ = machine_draw_pos(
        rail, served, x_norm, mg.parking_frac, mg.scale)
    return gx, gy


def _selected_key_from_event(event) -> str:
    """Extract our typed asset key from a Streamlit Plotly selection."""
    if event is None:
        return ""
    try:
        selection = event.selection
    except AttributeError:
        selection = event.get("selection", {}) if isinstance(event, dict) else {}
    try:
        points = selection.points
    except AttributeError:
        points = selection.get("points", []) if isinstance(selection, dict) else []
    if not points:
        return ""
    point = points[-1]
    try:
        custom = point.customdata
    except AttributeError:
        custom = point.get("customdata", []) if isinstance(point, dict) else []
    if isinstance(custom, str):
        return custom
    if isinstance(custom, Sequence) and custom:
        return str(custom[0])
    return ""


def flow_configuration_figure(
        sc: Scenario,
        section: str,
        selected_key: str = "",
) -> Tuple[go.Figure, List[FlowConfigAsset]]:
    """Build the sectional diagram plus point-selection hit targets."""
    geo = _configuration_geometry(sc, geometry_for(sc, section))
    assets = flow_config_assets(sc, geo)
    by_key = {asset.key: asset for asset in assets}
    selected = by_key.get(selected_key)
    selected_id = selected.asset_id if selected is not None else ""
    state = build_process_mimic_state(sc, basis="initial", bucket=0)
    fig = process_mimic_figure(
        state, geo,
        MimicOpts(
            show_flows=False,
            show_tags=True,
            show_evidence=False,
            selected_asset=selected_id,
        ))

    xs: List[float] = []
    ys: List[float] = []
    custom: List[List[str]] = []
    text: List[str] = []
    symbols: List[str] = []

    def add_point(x: float, y: float, asset: FlowConfigAsset) -> None:
        xs.append(x)
        ys.append(y)
        custom.append([asset.key])
        text.append(
            f"<b>{asset.label}</b><br>{asset.kind.title()} · "
            f"{asset.status.lower()}<br>Click to select")
        symbols.append({
            "LINE": "square",
            "MACHINE": "circle",
            "ROUTE": "diamond",
        }[asset.kind])

    for line, asset in zip(geo.process_lines, (
            item for item in assets if item.kind == "LINE")):
        add_point((line.x0 + line.x1) / 2.0,
                  (line.y0 + line.y1) / 2.0, asset)
        if selected_key == asset.key:
            fig.add_shape(
                type="rect", x0=line.x0, y0=line.y0,
                x1=line.x1, y1=line.y1,
                line=dict(color=hs.SEL_BLUE, width=3),
                fillcolor="rgba(0,0,0,0)")

    machine_assets = {
        item.asset_id: item for item in assets if item.kind == "MACHINE"}
    for machine in geo.machines:
        point = _machine_hit_point(sc, geo, machine)
        if point is None:
            continue
        asset = machine_assets[machine.machine_id]
        add_point(*point, asset)

    route_assets = {
        item.asset_id: item for item in assets if item.kind == "ROUTE"}
    for route in geo.conveyors:
        asset = route_assets[route.cid]
        for x, y in _route_hit_points(route):
            add_point(x, y, asset)

    fig.add_trace(go.Scatter(
        x=xs, y=ys, mode="markers",
        marker=dict(
            size=18,
            opacity=0.015,
            color=hs.SEL_BLUE,
            symbol=symbols,
            line=dict(color=hs.SEL_BLUE, width=0.4)),
        customdata=custom,
        text=text,
        hovertemplate="%{text}<extra></extra>",
        showlegend=False,
        name="configuration hit targets",
    ))
    fig.update_layout(clickmode="event+select")

    if selected is not None:
        selected_points = [
            (x, y)
            for x, y, data in zip(xs, ys, custom)
            if data and data[0] == selected.key
        ]
        if selected_points:
            # One small visible focus marker confirms which element a click
            # selected. The element itself is also blue-highlighted where the
            # underlying symbol supports selection.
            x, y = selected_points[len(selected_points) // 2]
            fig.add_trace(go.Scatter(
                x=[x], y=[y], mode="markers",
                marker=dict(
                    size=12, color="rgba(31,111,179,0.12)",
                    line=dict(color=hs.SEL_BLUE, width=2),
                    symbol={"LINE": "square", "MACHINE": "circle",
                            "ROUTE": "diamond"}[selected.kind]),
                hoverinfo="skip", showlegend=False,
                name="selected configuration asset"))
    return fig, assets


def _asset_option(asset: FlowConfigAsset) -> str:
    lock = " · reference only" if not asset.configurable else ""
    return f"{asset.kind.title()} · {asset.label}{lock}"


def _render_asset_control(sc: Scenario, asset: FlowConfigAsset) -> None:
    from . import common

    palette = {
        "ENABLED": hs.RUN_GREEN,
        "DISABLED": hs.FAULT_RED,
        "PARTLY DISABLED": hs.WARN_AMBER,
        "REFERENCE ONLY": hs.STATIC_LIGHT,
    }
    left, right = st.columns([4.2, 1.4], vertical_alignment="center")
    with left:
        st.markdown(
            f"**{asset.label}** · "
            f"<span style='color:{palette[asset.status]};font-weight:700'>"
            f"{asset.status}</span>",
            unsafe_allow_html=True)
        detail = asset.note
        if asset.members and len(asset.members) <= 3:
            detail += " " + ", ".join(asset.members)
        st.caption(detail)
    with right:
        desired = not bool(asset.enabled)
        action = (
            "Not switchable" if not asset.configurable else
            "Enable selected" if desired else
            "Disable selected")
        if st.button(
                action,
                key=f"flow_cfg_apply_{asset.key}",
                type="primary" if desired else "secondary",
                disabled=not asset.configurable,
                width="stretch"):
            set_flow_asset_enabled(sc, asset, desired)
            stale = common.invalidate_outputs()
            common.revalidate()
            st.session_state["flow_cfg_flash"] = (
                f"{asset.label} {'enabled' if desired else 'disabled'}"
                + (" · previous plan cleared" if stale else ""))
            st.rerun()


def render_flow_availability_configuration(sc: Scenario) -> None:
    """Render the requested sectional select-and-disable Configure surface."""
    st.markdown("#### Flow availability")
    st.caption(
        "Choose a section, then click a line, machine, or route in the "
        "diagram. Red means disabled. Reference/future items remain visible "
        "but cannot be switched because they are not planner resources. "
        "Demand is not deleted; unreachable demand stays visible as unmet.")

    flash = st.session_state.pop("flow_cfg_flash", "")
    if flash:
        st.success(flash)

    section = st.radio(
        "Flow section",
        options=list(SECTION_LABELS),
        horizontal=True,
        format_func=SECTION_LABELS.get,
        key="flow_cfg_section")
    selected_state_key = f"flow_cfg_asset_{section}"
    selected_key = str(st.session_state.get(selected_state_key, ""))
    fig, assets = flow_configuration_figure(sc, section, selected_key)
    by_key = {asset.key: asset for asset in assets}
    if selected_key not in by_key:
        selected_key = next(
            (asset.key for asset in assets if asset.configurable),
            assets[0].key if assets else "")
        st.session_state[selected_state_key] = selected_key
        fig, assets = flow_configuration_figure(sc, section, selected_key)
        by_key = {asset.key: asset for asset in assets}

    nonce = int(st.session_state.get("flow_cfg_chart_nonce", 0))
    chart_key = f"flow_cfg_chart_{section}_{nonce}"

    def _capture_diagram_selection() -> None:
        clicked = _selected_key_from_event(st.session_state.get(chart_key))
        if clicked in by_key:
            st.session_state[selected_state_key] = clicked

    st.plotly_chart(
        theme.operator_figure(fig),
        width="stretch",
        config={"scrollZoom": True, "displaylogo": False},
        key=chart_key,
        on_select=_capture_diagram_selection,
        selection_mode="points")

    st.caption(
        "Tip: the selector below is the keyboard-accessible alternative to "
        "clicking the drawing.")

    if assets:
        options = [asset.key for asset in assets]

        def _clear_diagram_selection() -> None:
            # Plotly selection state is read-only. A new widget key clears a
            # prior diagram click so it cannot visually contradict a later
            # keyboard/selectbox choice.
            st.session_state["flow_cfg_chart_nonce"] = (
                int(st.session_state.get("flow_cfg_chart_nonce", 0)) + 1)

        selected_key = st.selectbox(
            "Selected diagram element",
            options=options,
            key=selected_state_key,
            format_func=lambda key: _asset_option(by_key[key]),
            on_change=_clear_diagram_selection)
        _render_asset_control(sc, by_key[selected_key])

    enabled = sum(
        1 for asset in assets
        if asset.configurable and asset.enabled and not asset.partial)
    disabled = sum(
        1 for asset in assets
        if asset.configurable and (not asset.enabled or asset.partial))
    reference = sum(1 for asset in assets if not asset.configurable)
    st.caption(
        f"{enabled} enabled · {disabled} disabled/partial · "
        f"{reference} reference-only")

    if section == "LOUTA":
        configured = (
            sc.config.get("CORRIDOR_DISPATCH_TARGET_T", {}) or {}).get(
                "C1", 0.0)
        effective = effective_corridor_targets(sc).get("C1", 0.0)
        if float(effective) != float(configured):
            st.info(
                f"C1 configured contribution is {float(configured):,.0f} t; "
                f"with the selected line availability, the next plan uses "
                f"{float(effective):,.0f} t. Re-enabling the line restores "
                "the configured contribution.")
