"""Sectional, model-backed availability controls for Configure.

The flow drawing remains a view of the scenario.  This module adds a thin
interaction layer: a selected diagram element can change only an availability
field/configuration that the planner actually reads.  Reference and future
geometry stays selectable for explanation, but is deliberately read-only.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import plotly.graph_objects as go
import streamlit as st

from mph_sim.domain import CalendarEntry, Scenario
from mph_sim.flow_config import (
    PROCESS_LINE_STAGE_MAP,
    destination_line_link_ids,
    destination_line_operating_state,
    destination_line_status,
    disabled_process_lines,
    effective_corridor_targets,
    process_line_modelled,
    process_line_operating_state,
    set_destination_line_enabled,
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
from .flow_svg import redesign_adapter, redesign_viewer


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


@dataclass(frozen=True)
class PlanningFlowResource:
    """One editable resource resolved from the global Monitor drawing."""

    kind: str
    label: str
    visual_kind: str
    visual_id: str
    canonical_ids: Tuple[str, ...]
    scopes: Tuple[str, ...]
    site: str
    configurable: bool
    baseline_state: str
    base_rate_tph: Optional[float]
    aggregation_mode: str = "single"
    note: str = ""

    @property
    def key(self) -> str:
        return f"{self.kind}|{'|'.join(self.canonical_ids)}"

    @property
    def timed_entries_label(self) -> str:
        return ", ".join(self.scopes)


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


# ---------------------------------------------------------------------------
# Global Plan flow configuration (the same Claude/Monitor drawing)


def _entry_action(entry: CalendarEntry) -> str:
    return str(getattr(entry, "action", "UNAVAILABLE") or
               "UNAVAILABLE").strip().upper()


def _resource_entries(
        sc: Scenario, scopes: Sequence[str]) -> List[CalendarEntry]:
    wanted = set(scopes)
    return [entry for entry in sc.calendars if entry.scope in wanted]


def _entry_summary(sc: Scenario, entry: CalendarEntry) -> str:
    start, end = sc.calendar_interval_minutes(entry)
    action = _entry_action(entry)
    if action == "RATE_CAP":
        detail = f"cap {float(entry.rate_tph or 0):,.0f} t/h"
    else:
        detail = str(entry.reason or "unavailable")
    return f"{start}–{end} min {detail}"


def _has_whole_run_maintenance(
        sc: Scenario, entries: Sequence[CalendarEntry]) -> bool:
    horizon = int(sc.sim.effective_horizon_minutes())
    return any(
        _entry_action(entry) == "UNAVAILABLE"
        and sc.calendar_interval_minutes(entry) == (0, horizon)
        and "whole-run maintenance" in str(entry.reason or "").lower()
        for entry in entries
    )


def _remove_whole_run_maintenance(
        sc: Scenario, scopes: Sequence[str]) -> None:
    wanted = set(scopes)
    horizon = int(sc.sim.effective_horizon_minutes())
    sc.calendars = [
        entry for entry in sc.calendars
        if not (
            entry.scope in wanted
            and _entry_action(entry) == "UNAVAILABLE"
            and sc.calendar_interval_minutes(entry) == (0, horizon)
            and "whole-run maintenance" in str(
                entry.reason or "").lower()
        )
    ]


def _process_line_nominal_rate_tph(
        sc: Scenario, line_id: str) -> Optional[float]:
    configured = sc.process_line_capacity_tph(line_id)
    if configured is not None:
        return configured
    stages = set(PROCESS_LINE_STAGE_MAP.get(line_id, ()))
    rates: List[float] = []
    for transfer in sc.process_transfers:
        if transfer.process_stage not in stages:
            continue
        candidates: List[float] = []
        machine = next(
            (item for item in sc.machines if item.id == transfer.machine),
            None)
        if machine is not None:
            candidates.append(float(machine.rate_max_tph))
        candidates.extend(
            float(sc.link(link_id).capacity_tph)
            for link_id in transfer.path
            if any(link.id == link_id for link in sc.links)
        )
        if candidates:
            rates.append(min(candidates))
    return min(rates) if rates else None


def _destination_base_rate_tph(
        sc: Scenario, destination_ids: Sequence[str]) -> Optional[float]:
    configured = [
        sc.process_line_capacity_tph(destination_id)
        for destination_id in destination_ids
    ]
    configured = [value for value in configured if value is not None]
    if configured:
        return min(configured)
    links = [
        sc.link(link_id)
        for destination_id in destination_ids
        for link_id in destination_line_link_ids(sc, destination_id)
    ]
    return min(float(link.capacity_tph) for link in links) if links else None


def _baseline_for_links(sc: Scenario, link_ids: Sequence[str]) -> str:
    links = [sc.link(link_id) for link_id in link_ids]
    states = [bool(link.available) for link in links]
    if not states:
        return "REFERENCE"
    if all(states):
        return "AVAILABLE"
    if any(states):
        return "PARTIAL"
    return "UNAVAILABLE"


def _annotate_planning_metadata(
        sc: Scenario, metadata: dict) -> dict:
    """Add base configuration without changing Monitor activity state."""
    metadata = deepcopy(metadata)
    metadata["planningMode"] = True
    # The exact Monitor handoff remains the drawing source.  This flag only
    # changes its presentation for routing configuration: operating grades
    # and pile readouts move to hover, while resource state stays prominent.
    metadata["displayMode"] = "routing-availability"
    machines = {machine.id: machine for machine in sc.machines}
    links = {link.id: link for link in sc.links}

    for asset in metadata.get("assets", []):
        kind = str(asset.get("kind") or "")
        asset_id = str(asset.get("id") or "")
        scopes: Tuple[str, ...] = ()
        baseline = "REFERENCE"
        base_rate: Optional[float] = None
        configurable = False
        if kind == "machine" and asset_id in machines:
            machine = machines[asset_id]
            lifecycle = str(getattr(machine, "lifecycle", "EXISTING"))
            configurable = bool(
                getattr(machine, "planning_enabled", True)
                and lifecycle != "FUTURE")
            baseline = "AVAILABLE" if machine.available else "UNAVAILABLE"
            base_rate = float(machine.rate_max_tph)
            scopes = (machine.id,)
        elif kind == "route":
            member_ids = tuple(
                str(link_id) for link_id in asset.get("linkIds", [])
                if str(link_id) in links)
            configurable = bool(
                member_ids and asset.get("planningEnabled", False)
                and not asset.get("future", False)
                and all(str(getattr(links[link_id], "lifecycle", "EXISTING"))
                        != "FUTURE" for link_id in member_ids))
            baseline = _baseline_for_links(sc, member_ids)
            base_rate = (
                min(float(links[link_id].capacity_tph)
                    for link_id in member_ids)
                if member_ids else None)
            scopes = tuple(f"LINK:{link_id}" for link_id in member_ids)
        elif kind == "processLine" and process_line_modelled(asset_id):
            configurable = True
            line_state = process_line_operating_state(sc, asset_id)
            baseline = (
                "AVAILABLE" if line_state == "AVAILABLE"
                else "FUTURE" if line_state == "FUTURE"
                else "UNAVAILABLE")
            base_rate = _process_line_nominal_rate_tph(sc, asset_id)
            scopes = (f"LINE:{asset_id}",)
        entries = _resource_entries(sc, scopes)
        if _has_whole_run_maintenance(sc, entries):
            baseline = "MAINTENANCE"
        asset.update({
            "baselineState": baseline,
            "baseRateTph": base_rate,
            "configurable": configurable,
            "timedChangeCount": len(entries),
            "timedChangeSummary": " | ".join(
                _entry_summary(sc, entry) for entry in entries),
        })

    for destination in metadata.get("destinations", []):
        destination_id = str(destination.get("id") or "")
        if not any(item.id == destination_id for item in sc.destinations):
            continue
        link_ids = destination_line_link_ids(sc, destination_id)
        lifecycle = destination_line_operating_state(sc, destination_id)
        status = destination_line_status(sc, destination_id)
        configurable = bool(link_ids and lifecycle != "FUTURE")
        baseline = (
            "PARTIAL" if status == "PARTIAL" else
            "AVAILABLE" if status == "ENABLED" else
            "UNAVAILABLE" if status == "DISABLED" else
            "FUTURE" if lifecycle == "FUTURE" else "REFERENCE"
        )
        scopes = (f"LINE:{destination_id}",)
        entries = _resource_entries(sc, scopes)
        if _has_whole_run_maintenance(sc, entries):
            baseline = "MAINTENANCE"
        destination.update({
            "baselineState": baseline,
            "baseRateTph": _destination_base_rate_tph(
                sc, (destination_id,)),
            "configurable": configurable,
            "timedChangeCount": len(entries),
            "timedChangeSummary": " | ".join(
                _entry_summary(sc, entry) for entry in entries),
        })
    return metadata


def planning_flow_snapshot(
        sc: Scenario, selected: Optional[PlanningFlowResource] = None
        ) -> Tuple[dict, List[PlanningFlowResource], Dict[Tuple[str, str], str]]:
    """Build the shared global drawing plus its canonical edit registry."""
    geo = configured_mimic_geometry(sc, "CHAIN", show_future=True)
    state = build_process_mimic_state(sc, basis="initial", bucket=0)
    opts = MimicOpts(
        show_flows=False,
        show_tags=True,
        show_evidence=False,
        selected_asset=(selected.canonical_ids[0] if selected else ""),
    )
    metadata = redesign_adapter.build_snapshot(
        sc, state, geo, opts, basis="initial")
    metadata = _annotate_planning_metadata(sc, metadata)
    handoff = redesign_viewer.handoff_snapshot(metadata)

    resources: Dict[str, PlanningFlowResource] = {}
    click_map: Dict[Tuple[str, str], str] = {}

    def add(resource: PlanningFlowResource) -> None:
        resources.setdefault(resource.key, resource)
        click_map[(resource.visual_kind, resource.visual_id)] = resource.key

    for row in handoff.get("machines", []):
        canonical = str(row.get("canonicalId") or "")
        machine = next((item for item in sc.machines
                        if item.id == canonical), None)
        if machine is None:
            continue
        resource = PlanningFlowResource(
            kind="MACHINE",
            label=canonical,
            visual_kind="machine",
            visual_id=str(row["id"]),
            canonical_ids=(canonical,),
            scopes=(canonical,),
            site=str(row.get("site") or machine.site),
            configurable=bool(row.get("configurable")),
            baseline_state=str(row.get("baselineState") or "REFERENCE"),
            base_rate_tph=(float(row["baseRateTph"])
                           if row.get("baseRateTph") is not None else None),
            aggregation_mode="single",
            note=("Mobile equipment rate is a physical/as-handled ceiling."
                  if row.get("configurable") else
                  "Display/reference equipment; not a planner resource."),
        )
        add(resource)

    for row in handoff.get("routes", []):
        member_ids = tuple(dict.fromkeys(
            str(link_id) for link_id in row.get("memberLinkIds", [])
            if any(link.id == str(link_id) for link in sc.links)))
        if not member_ids:
            continue
        label = str(row.get("id") or "Conveyor")
        resource = PlanningFlowResource(
            kind="CONVEYOR",
            label=label,
            visual_kind="route",
            visual_id=str(row["id"]),
            canonical_ids=member_ids,
            scopes=tuple(f"LINK:{link_id}" for link_id in member_ids),
            site=str(row.get("site") or "GLOBAL"),
            configurable=bool(row.get("configurable")),
            baseline_state=str(row.get("baselineState") or "REFERENCE"),
            base_rate_tph=(float(row["baseRateTph"])
                           if row.get("baseRateTph") is not None else None),
            aggregation_mode=str(
                row.get("aggregationMode") or "single").lower(),
            note=(("Aggregate rate is divided across the parallel model "
                   "conveyors. "
                   if str(row.get("aggregationMode") or "").lower()
                   == "parallel" else "")
                  + f"Controls {len(member_ids)} model conveyor link"
                  f"{'s' if len(member_ids) != 1 else ''}: "
                  + ", ".join(member_ids)),
        )
        add(resource)

    for row in handoff.get("processLines", []):
        canonical = str(row.get("canonicalId") or "")
        if not process_line_modelled(canonical):
            continue
        add(PlanningFlowResource(
            kind="PROCESS LINE",
            label=canonical,
            visual_kind="processLine",
            visual_id=str(row["id"]),
            canonical_ids=(canonical,),
            scopes=(f"LINE:{canonical}",),
            site=str(row.get("site") or ""),
            configurable=bool(row.get("configurable")),
            baseline_state=str(row.get("baselineState") or "REFERENCE"),
            base_rate_tph=(float(row["baseRateTph"])
                           if row.get("baseRateTph") is not None else None),
            aggregation_mode="single",
            note="Controls the complete solver-backed washing-line chain.",
        ))

    for row in handoff.get("destinations", []):
        canonical_ids = tuple(
            str(destination_id)
            for destination_id in row.get("canonicalIds", [])
            if any(destination.id == str(destination_id)
                   for destination in sc.destinations))
        modelled_ids = tuple(
            destination_id for destination_id in canonical_ids
            if destination_line_link_ids(sc, destination_id))
        if not modelled_ids:
            continue
        add(PlanningFlowResource(
            kind="RECEIVING PLANT",
            label=str(row.get("id") or modelled_ids[0]),
            visual_kind="destination",
            visual_id=str(row["id"]),
            canonical_ids=modelled_ids,
            scopes=tuple(f"LINE:{destination_id}"
                         for destination_id in modelled_ids),
            site=str(row.get("site") or ""),
            configurable=bool(row.get("configurable")),
            baseline_state=str(row.get("baselineState") or "REFERENCE"),
            base_rate_tph=(float(row["baseRateTph"])
                           if row.get("baseRateTph") is not None else None),
            aggregation_mode="single",
            note="Receiving-line availability and physical feed-rate ceiling.",
        ))

    ordered = list(resources.values())
    if selected is not None:
        canonical = resources.get(selected.key)
        if canonical is not None:
            metadata["initialSelection"] = {
                "kind": canonical.visual_kind,
                "id": canonical.visual_id,
            }
    return metadata, ordered, click_map


def set_planning_resource_available(
        sc: Scenario, resource: PlanningFlowResource,
        available: bool) -> None:
    """Persist one whole-horizon availability state."""
    if not resource.configurable:
        raise ValueError(f"{resource.label} is reference-only")
    if resource.kind == "MACHINE":
        sc.machine(resource.canonical_ids[0]).available = bool(available)
    elif resource.kind == "CONVEYOR":
        for link_id in resource.canonical_ids:
            sc.link(link_id).available = bool(available)
    elif resource.kind == "PROCESS LINE":
        set_process_line_enabled(
            sc, resource.canonical_ids[0], bool(available))
    elif resource.kind == "RECEIVING PLANT":
        for destination_id in resource.canonical_ids:
            set_destination_line_enabled(sc, destination_id, bool(available))
    else:
        raise ValueError(f"unsupported resource kind: {resource.kind}")


def set_planning_resource_rate(
        sc: Scenario, resource: PlanningFlowResource,
        rate_tph: float) -> None:
    """Persist one whole-horizon physical/as-handled rate ceiling."""
    rate = float(rate_tph)
    if not math.isfinite(rate) or rate <= 0:
        raise ValueError("rate must be a positive finite physical t/h value")
    if resource.kind == "MACHINE":
        machine = sc.machine(resource.canonical_ids[0])
        if rate < float(machine.rate_min_tph):
            raise ValueError(
                f"rate must be at least the machine stable rate "
                f"({machine.rate_min_tph:,.0f} t/h)")
        machine.rate_max_tph = rate
    elif resource.kind == "CONVEYOR":
        member_rates = _conveyor_member_rates(sc, resource, rate)
        links_and_rates = [
            (sc.link(link_id), member_rate)
            for link_id, member_rate in zip(
                resource.canonical_ids, member_rates)
        ]
        for link, member_rate in links_and_rates:
            if member_rate < float(link.min_stable_tph):
                raise ValueError(
                    f"allocated rate must be at least {link.id}'s stable rate "
                    f"({link.min_stable_tph:,.0f} t/h)")
        for link, member_rate in links_and_rates:
            link.capacity_tph = member_rate
    elif resource.kind in {"PROCESS LINE", "RECEIVING PLANT"}:
        raw = sc.config.get("PROCESS_LINE_CAPACITY_TPH", {})
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ValueError("PROCESS_LINE_CAPACITY_TPH must be an object")
        caps = dict(raw)
        for line_id in resource.canonical_ids:
            caps[line_id] = rate
        sc.config["PROCESS_LINE_CAPACITY_TPH"] = caps
    else:
        raise ValueError(f"unsupported resource kind: {resource.kind}")


def _conveyor_member_rates(
        sc: Scenario, resource: PlanningFlowResource,
        aggregate_rate_tph: float) -> Tuple[float, ...]:
    """Translate one visual-conveyor rate into canonical link ceilings.

    Most drawn routes represent a common/series belt and therefore apply the
    entered ceiling to every member.  A visual route explicitly marked as
    parallel represents aggregate capacity, so its ceiling is distributed in
    proportion to the members' configured capacities.  This prevents a
    3,000 t/h collector formed by two 1,500 t/h belts from accidentally
    becoming 6,000 t/h when edited from the single drawing element.
    """
    rate = float(aggregate_rate_tph)
    if resource.kind != "CONVEYOR" or \
            resource.aggregation_mode != "parallel":
        return tuple(rate for _ in resource.canonical_ids)
    capacities = [
        max(float(sc.link(link_id).capacity_tph), 0.0)
        for link_id in resource.canonical_ids
    ]
    total = sum(capacities)
    if total <= 0:
        share = rate / max(len(capacities), 1)
        return tuple(share for _ in capacities)
    return tuple(rate * capacity / total for capacity in capacities)


def add_planning_resource_period_change(
        sc: Scenario, resource: PlanningFlowResource, *,
        action: str, start_minute: int, end_minute: int,
        rate_tph: Optional[float] = None,
        reason: str = "planned unavailability") -> None:
    """Append an exact half-open change for every canonical member."""
    normalized = str(action).strip().upper()
    if normalized not in {"UNAVAILABLE", "RATE_CAP"}:
        raise ValueError("action must be UNAVAILABLE or RATE_CAP")
    horizon = int(sc.sim.effective_horizon_minutes())
    start, end = int(start_minute), int(end_minute)
    if not 0 <= start < end <= horizon:
        raise ValueError(
            f"require 0 ≤ start < end ≤ {horizon} minutes")
    rate = None
    if normalized == "RATE_CAP":
        rate = float(rate_tph) if rate_tph is not None else 0.0
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError("period rate cap must be a positive finite t/h")
    native_min = sc.sim.native_bucket_minutes()
    period_rates: Tuple[Optional[float], ...]
    if normalized == "RATE_CAP" and resource.kind == "CONVEYOR":
        period_rates = tuple(_conveyor_member_rates(sc, resource, rate))
    else:
        period_rates = tuple(rate for _ in resource.scopes)
    for scope, scope_rate in zip(resource.scopes, period_rates):
        sc.calendars.append(CalendarEntry(
            scope=scope,
            start_bucket=start // native_min,
            end_bucket=max(0, math.ceil(end / native_min) - 1),
            reason=str(reason or "planned unavailability"),
            start_minute=start,
            end_minute=end,
            action=normalized,
            rate_tph=scope_rate,
        ))


def _resource_option(resource: PlanningFlowResource) -> str:
    rate = (
        f" · {resource.base_rate_tph:,.0f} t/h"
        if resource.base_rate_tph is not None else "")
    lock = " · reference only" if not resource.configurable else ""
    return (
        f"{resource.site or 'Global'} · {resource.kind.title()} · "
        f"{resource.label} · {resource.baseline_state}{rate}{lock}")


def _render_global_resource_control(
        sc: Scenario, resource: PlanningFlowResource) -> None:
    from . import common
    from .pages_planning import planning_clock_label

    entries = _resource_entries(sc, resource.scopes)
    st.markdown(f"#### Selected · {resource.label}")
    kind_col, state_col, rate_col, period_col = st.columns(4)
    kind_col.metric("Resource type", resource.kind.title())
    state_col.metric("Entire-run state", resource.baseline_state.title())
    rate_col.metric(
        "Entire-run rate ceiling",
        (f"{resource.base_rate_tph:,.0f} physical t/h"
         if resource.base_rate_tph is not None else "Not defined"))
    period_col.metric("Timed overrides", len(entries))
    st.caption(
        f"{resource.note} Baseline settings apply for the complete planning "
        "week; timed overrides are more restrictive only and take precedence "
        "inside their exact period.")

    if not resource.configurable:
        st.info(
            "This item is visible for flow context but is not a switchable "
            "planner resource.")
        return

    change_col, duration_col = st.columns(2)
    change = change_col.radio(
        "Change",
        ["Availability / maintenance", "Rate limit (derate)"],
        horizontal=True,
        key=f"flow_plan_change_{resource.key}")
    duration = duration_col.radio(
        "Applies for",
        ["Entire 7-day run", "Scheduled period"],
        horizontal=True,
        key=f"flow_plan_duration_{resource.key}")

    horizon = int(sc.sim.effective_horizon_minutes())
    state_value = "Available"
    rate_value = float(resource.base_rate_tph or 1.0)
    start_min, end_min = 0, horizon
    reason = "planned unavailability"

    if change == "Availability / maintenance":
        state_options = (
            ["Available", "Unavailable", "Maintenance"]
            if duration == "Entire 7-day run" else
            ["Unavailable", "Maintenance"])
        state_value = st.selectbox(
            "Desired state", state_options,
            key=f"flow_plan_state_{resource.key}_{duration}")
        if duration == "Scheduled period":
            reason = st.text_input(
                "Reason",
                value=("planned maintenance" if state_value == "Maintenance"
                       else "planned unavailability"),
                key=f"flow_plan_reason_{resource.key}")
    else:
        rate_value = st.number_input(
            "Maximum physical/as-handled rate [t/h]",
            min_value=0.1,
            value=max(0.1, float(resource.base_rate_tph or 1.0)),
            step=10.0,
            key=f"flow_plan_rate_{resource.key}_{duration}")

    if duration == "Scheduled period":
        timing = st.columns(2)
        start_min = int(timing[0].number_input(
            "Start offset [min]", min_value=0,
            max_value=max(0, horizon - 1),
            value=0, step=1,
            key=f"flow_plan_start_{resource.key}_{change}"))
        end_min = int(timing[1].number_input(
            "End offset [min] (exclusive)", min_value=1,
            max_value=horizon, value=min(60, horizon), step=1,
            key=f"flow_plan_end_{resource.key}_{change}"))
        if start_min < end_min:
            st.info(
                f"Scheduled window · {planning_clock_label(sc, start_min)} → "
                f"{planning_clock_label(sc, end_min)} · "
                f"duration {end_min - start_min:,} min.")
        else:
            st.warning("End must be later than start; nothing will be applied.")

    if st.button(
            "Apply selected change", type="primary", width="stretch",
            key=f"flow_plan_apply_{resource.key}_{change}_{duration}"):
        try:
            if (duration == "Entire 7-day run"
                    and change == "Availability / maintenance"):
                _remove_whole_run_maintenance(sc, resource.scopes)
                if state_value == "Maintenance":
                    set_planning_resource_available(sc, resource, True)
                    add_planning_resource_period_change(
                        sc, resource, action="UNAVAILABLE",
                        start_minute=0, end_minute=horizon,
                        reason="whole-run maintenance")
                else:
                    set_planning_resource_available(
                        sc, resource, state_value == "Available")
            elif duration == "Entire 7-day run":
                set_planning_resource_rate(sc, resource, rate_value)
            else:
                add_planning_resource_period_change(
                    sc, resource,
                    action=("RATE_CAP" if change == "Rate limit (derate)"
                            else "UNAVAILABLE"),
                    start_minute=start_min,
                    end_minute=end_min,
                    rate_tph=(
                        rate_value if change == "Rate limit (derate)" else None),
                    reason=reason,
                )
        except ValueError as exc:
            st.error(str(exc))
        else:
            stale = common.invalidate_outputs()
            common.revalidate()
            st.session_state["flow_plan_flash"] = (
                f"Updated {resource.label}"
                + (" · previous plan cleared" if stale else ""))
            st.rerun()

    if entries:
        with st.expander(
                f"Scheduled changes for this resource ({len(entries)})",
                expanded=True):
            st.dataframe([
                {
                    "Scope": entry.scope,
                    "From": planning_clock_label(
                        sc, sc.calendar_interval_minutes(entry)[0]),
                    "To (exclusive)": planning_clock_label(
                        sc, sc.calendar_interval_minutes(entry)[1]),
                    "Duration": (
                        f"{sc.calendar_interval_minutes(entry)[1] - sc.calendar_interval_minutes(entry)[0]:,} min"),
                    "Effect": (
                        f"Rate cap {float(entry.rate_tph or 0):,.0f} t/h"
                        if _entry_action(entry) == "RATE_CAP" else
                        "Unavailable / maintenance"),
                    "Reason": str(entry.reason or "Planned resource change"),
                }
                for entry in entries
            ], hide_index=True, width="stretch")
            if st.button(
                    "Remove these period changes",
                    key=f"flow_plan_clear_periods_{resource.key}"):
                scopes = set(resource.scopes)
                sc.calendars = [entry for entry in sc.calendars
                                if entry.scope not in scopes]
                stale = common.invalidate_outputs()
                common.revalidate()
                st.session_state["flow_plan_flash"] = (
                    f"Removed period changes for {resource.label}"
                    + (" · previous plan cleared" if stale else ""))
                st.rerun()


def _scheduled_change_groups(
        sc: Scenario,
        resources: Sequence[PlanningFlowResource]) -> List[dict]:
    """Return operator-level events instead of one row per model scope."""
    from .pages_planning import planning_clock_label

    owner_by_scope: Dict[str, PlanningFlowResource] = {}
    for resource in resources:
        for scope in resource.scopes:
            owner_by_scope.setdefault(scope, resource)

    groups: Dict[Tuple[str, int, int, str, str], dict] = {}
    for entry in sc.calendars:
        start, end = sc.calendar_interval_minutes(entry)
        action = _entry_action(entry)
        reason = str(entry.reason or "planned resource change")
        resource = owner_by_scope.get(entry.scope)
        owner_key = resource.key if resource else f"SCOPE|{entry.scope}"
        key = (owner_key, start, end, action, reason)
        group = groups.setdefault(key, {
            "resource": resource,
            "entries": [],
            "start": start,
            "end": end,
            "action": action,
            "reason": reason,
        })
        group["entries"].append(entry)

    rows: List[dict] = []
    for group in groups.values():
        resource = group["resource"]
        entries = group["entries"]
        action = group["action"]
        rates = [float(entry.rate_tph) for entry in entries
                 if entry.rate_tph is not None]
        if action == "RATE_CAP" and rates:
            if resource is not None and resource.kind == "CONVEYOR" and \
                    resource.aggregation_mode == "parallel":
                displayed_rate = sum(rates)
            else:
                displayed_rate = min(rates)
            effect = f"Rate cap {displayed_rate:,.0f} physical t/h"
        else:
            effect = "Unavailable / maintenance"
        label = (
            resource.label if resource is not None else entries[0].scope)
        kind = (
            resource.kind.title() if resource is not None else "Model scope")
        site = resource.site.title() if resource is not None else "Global"
        start, end = int(group["start"]), int(group["end"])
        rows.append({
            "Resource": label,
            "Type": kind,
            "Site": site,
            "Effect": effect,
            "From": planning_clock_label(sc, start),
            "To (exclusive)": planning_clock_label(sc, end),
            "Duration": f"{end - start:,} min",
            "Reason": group["reason"],
            "_entries": tuple(entries),
            "_start": start,
        })
    return sorted(
        rows, key=lambda row: (row["_start"], row["Site"], row["Resource"]))


def _render_scheduled_change_overview(
        sc: Scenario,
        resources: Sequence[PlanningFlowResource]) -> None:
    """Review/remove diagram-created events without an editable input table."""
    from . import common

    rows = _scheduled_change_groups(sc, resources)
    st.markdown("#### Scheduled events")
    st.caption(
        "Create an outage or derate by clicking its resource in the diagram. "
        "This consolidated list is for weekly review and removal; no manual "
        "scope IDs or minute-offset table is required.")
    if not rows:
        st.info(
            "No timed events are scheduled. Select a machine, conveyor, "
            "washing line or receiving plant above, then choose Scheduled "
            "period in its control panel.")
        return

    st.dataframe([
        {key: value for key, value in row.items() if not key.startswith("_")}
        for row in rows
    ], hide_index=True, width="stretch")

    choice_col, action_col = st.columns([4, 1])
    selected_index = choice_col.selectbox(
        "Scheduled event to remove",
        options=list(range(len(rows))),
        format_func=lambda index: (
            f"{rows[index]['From']} · {rows[index]['Resource']} · "
            f"{rows[index]['Effect']}"),
        key="flow_plan_scheduled_event_remove")
    if action_col.button(
            "Remove selected event",
            key="flow_plan_remove_scheduled_event",
            type="secondary",
            width="stretch"):
        removed_ids = {id(entry) for entry in rows[selected_index]["_entries"]}
        sc.calendars = [entry for entry in sc.calendars
                        if id(entry) not in removed_ids]
        stale = common.invalidate_outputs()
        common.revalidate()
        st.session_state["flow_plan_flash"] = (
            f"Removed scheduled event for {rows[selected_index]['Resource']}"
            + (" · previous plan cleared" if stale else ""))
        st.rerun()


def render_global_flow_resource_configuration(sc: Scenario) -> None:
    """Configure resources by clicking the exact Monitor global drawing."""
    flash = st.session_state.pop("flow_plan_flash", "")
    if flash:
        st.success(flash)

    # Build the exact Monitor global drawing once.  The selected resource is
    # managed by the controls below; keeping the canvas initially unselected
    # prevents its details drawer from covering the three-site overview.
    metadata, resources, click_map = planning_flow_snapshot(sc)
    by_key = {resource.key: resource for resource in resources}
    selected_key = str(st.session_state.get("flow_plan_resource", ""))
    if selected_key not in by_key:
        selected_key = next(
            (resource.key for resource in resources
             if resource.configurable),
            resources[0].key if resources else "")
        st.session_state["flow_plan_resource"] = selected_key
    # Resource selection must not collapse the planning overview to one site.
    # Operators can still use the diagram's site tabs when they explicitly
    # want a local zoom, while every rerender starts from the global network.
    st.session_state.pop("flow_plan_focus_site", None)

    result = redesign_viewer.planning_component(
        metadata=metadata,
        height=760,
        key="flow_plan_global_diagram",
    )
    clicked = getattr(result, "selected", None)
    if isinstance(clicked, Mapping):
        raw_key = (str(clicked.get("kind") or ""),
                   str(clicked.get("id") or ""))
        mapped = click_map.get(raw_key)
        if mapped:
            if mapped != selected_key:
                st.session_state["flow_plan_resource"] = mapped
                st.rerun()
        if not mapped:
            st.session_state["flow_plan_readonly"] = (
                f"{raw_key[0] or 'Element'} {raw_key[1]} is information-only."
            )

    st.caption(
        "Green = available · red = unavailable · amber = partial · "
        "orange = maintenance · purple = future · gray = reference only · "
        "CLOCK marks exact period changes. Every conveyor has a coloured "
        "core plus an A/P/M/U/F/R state badge; primary routes also show the "
        "configured physical/as-handled rate. Hover or click for full "
        "details. Use the site tabs to focus All, Louta, Benguerir or MPH.")
    readonly = st.session_state.pop("flow_plan_readonly", "")
    if readonly:
        st.info(readonly)

    if resources:
        selected_key = st.selectbox(
            "Selected model resource",
            options=[resource.key for resource in resources],
            key="flow_plan_resource",
            format_func=lambda key: _resource_option(by_key[key]),
            help=("Keyboard-accessible alternative to clicking a machine, "
                  "conveyor, washing line or receiving plant."))
        _render_global_resource_control(sc, by_key[selected_key])

    _render_scheduled_change_overview(sc, resources)
