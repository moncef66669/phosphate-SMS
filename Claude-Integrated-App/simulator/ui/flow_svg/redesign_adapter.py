"""Canonical, read-only data adapter for the redesigned flow viewer.

The production planning model and :mod:`ui.layout_geometry` remain the source
of truth.  This module deliberately does not import or translate the mock IDs
from the visual handoff prototype.  It only packages the already-resolved
``MimicState`` and ``SiteGeometry`` into JSON-safe metadata that a browser
viewer can consume.

The adapter is pure: it never mutates the scenario, plan, simulation, geometry
or display options.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

from mph_sim.flow_config import (
    PROCESS_LINE_STAGE_MAP,
    destination_line_operating_state,
    destination_line_status,
)

from ..stockyard_monitor import grade_color
from .bridge import format_repere, format_repere_range

SCHEMA_VERSION = 1
_PUBLIC_TBC = re.compile(r"\bTBC\b", re.IGNORECASE)
_SITE_ORDER = ("LOUTA", "BENGUERIR", "MPH")
_GRADE_LABELS = {
    "RAW-BT": "BT",
    "RAW-BT-HIGH-CD": "HIGH-CD",
    "RAW-BT-LOW-CD": "LOW-CD",
    "RAW-TBC": "RAW-PENDING",
}


def _public_text(value: object) -> str:
    """Return short operator-facing text without internal TBC shorthand."""
    return _PUBLIC_TBC.sub("pending", str(value or ""))


def _number(value: object) -> Optional[float]:
    """Return a finite JSON number, or ``None`` for missing/non-finite data."""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _compact_mass(value: Optional[float]) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1000:
        return f"{value / 1000:,.1f} kt"
    return f"{value:,.0f} t"


def _compact_rate(value: Optional[float], capacity: Optional[float]) -> str:
    if value is None and capacity is None:
        return "—"
    if capacity is None:
        return f"{value or 0:,.0f} t/h"
    return f"{value or 0:,.0f} / {capacity:,.0f} t/h"


def _details(**items: object) -> List[Dict[str, str]]:
    """Produce the compact ordered key/value contract used by hover cards."""
    return [
        {"key": str(key), "value": _public_text(value)}
        for key, value in items.items()
        if value is not None and str(value) != ""
    ]


def _unique(values: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(value for value in values if value))


def _machine_state(state, mg, vm) -> str:
    """Mirror the production SVG machine-state resolution."""
    if mg.future:
        return "Future"
    if mg.machine_id in state.down_machines:
        return "Unavailable"
    if vm is None:
        return "Reference only" if mg.display else "Idle"
    status = vm.status
    if status == "Idle" and not getattr(vm, "planning_enabled", True):
        status = "Planning disabled"
    pos = state.machine_pos.get(mg.machine_id)
    if status == "Idle" and pos is not None and pos.travelling:
        status = "Travelling"
    return status


def _route_state(value: str) -> str:
    return {
        "running": "Running",
        "idle": "Idle",
        "tripped": "Unavailable",
        "future": "Future",
    }.get(str(value).lower(), str(value).title())


def _line_state(status: str) -> str:
    return {
        "OPERATING": "Available",
        "AVAILABLE": "Available",
        "DISABLED": "Disabled",
        "FUTURE": "Future",
    }.get(str(status).upper(), str(status).title())


def _destination_state(status: str, lifecycle: str) -> str:
    if status == "DISABLED":
        return "Disabled"
    if lifecycle == "FUTURE":
        return "Future"
    return "Available"


def _configured_demand_tph(sc, destination_id: str, bucket: int) -> float:
    """Read the saved demand without the Future lifecycle suppression."""
    value = None
    for entry in sc.demand:
        if entry.dest_id == destination_id and entry.bucket == bucket:
            value = entry.rate_tph
    if value is None:
        value = sc.destination(destination_id).demand_default_tph
    return float(value)


def _configured_demand_grade(sc, destination_id: str, bucket: int) -> str:
    """Return the campaign grade active at the selected planning hour."""
    resolver = getattr(sc, "demand_grade", None)
    if callable(resolver):
        return str(resolver(destination_id, bucket) or "")
    return str(sc.destination(destination_id).requested_grade or "")


def _represented_sites(sc, state, geo) -> List[str]:
    if geo.site_id in _SITE_ORDER:
        return [geo.site_id]
    present = set(state.monitor.sites)
    if not present:
        present = {site.id for site in sc.sites}
    return [site for site in _SITE_ORDER if site in present]


class _Context:
    """Small lookup bundle shared by the asset serializers."""

    def __init__(self, sc, state, geo, opts):
        self.sc = sc
        self.state = state
        self.geo = geo
        self.opts = opts
        self.spaces = {space.id: space for space in sc.spaces}
        self.machines = {machine.id: machine for machine in sc.machines}
        self.links = {link.id: link for link in sc.links}
        self.nodes = {node.id: node for node in sc.nodes}
        self.destinations = {dest.id: dest for dest in sc.destinations}
        self.pile_vm = {
            pile.id: pile
            for site in state.monitor.sites.values()
            for pile in site.piles
        }
        self.machine_vm = {
            machine.id: machine
            for site in state.monitor.sites.values()
            for machine in site.machines
        }
        self.rails = {rail.rail_id: rail for rail in geo.rails}
        self.geometry_machines = {
            machine.machine_id: machine for machine in geo.machines
        }
        self.job_by_machine = {job.machine: job for job in state.jobs}
        self.selected_job = next(
            (job for job in state.jobs if job.id == opts.selected_job), None)

    def endpoint_asset_ids(self, link_ids: Sequence[str]) -> List[str]:
        out: List[str] = []
        for link_id in link_ids:
            link = self.links.get(link_id)
            if link is None:
                continue
            for node_id in (link.src, link.dst):
                for prefix in ("SPACE:", "DEST:"):
                    if node_id.startswith(prefix):
                        candidate = node_id[len(prefix):]
                        if candidate in self.spaces or \
                                candidate in self.destinations:
                            out.append(candidate)
        return _unique(out)

    def site_for(
            self,
            asset_id: str,
            *,
            source_ids: Sequence[str] = (),
            related_ids: Sequence[str] = (),
    ) -> str:
        if asset_id in self.spaces:
            return self.spaces[asset_id].site
        if asset_id in self.machines:
            return self.machines[asset_id].site
        if asset_id in self.destinations:
            return self.destinations[asset_id].site

        sites: List[str] = []
        for related_id in related_ids:
            if related_id in self.spaces:
                sites.append(self.spaces[related_id].site)
            elif related_id in self.machines:
                sites.append(self.machines[related_id].site)
            elif related_id in self.destinations:
                sites.append(self.destinations[related_id].site)
        for link_id in source_ids:
            link = self.links.get(link_id)
            if link is None:
                continue
            for node_id in (link.src, link.dst):
                node = self.nodes.get(node_id)
                if node is not None and node.site in _SITE_ORDER:
                    sites.append(node.site)
        sites = _unique(sites)
        if len(sites) == 1:
            return sites[0]
        if len(sites) > 1:
            return "GLOBAL"

        upper = asset_id.upper()
        if upper.startswith(("HP-", "ST01-", "RC01-", "M16", "PAP",
                             "DRY-")):
            return "MPH"
        if upper.startswith(("BG-", "BEN", "RC2", "RC3", "ST3")):
            return "BENGUERIR"
        if upper.startswith(("LT-", "LOU", "102MG", "110MG", "110LG",
                             "120LG", "130LG", "140LG")):
            return "LOUTA"
        return self.geo.site_id if self.geo.site_id in _SITE_ORDER else "GLOBAL"


def _pile_assets(ctx: _Context) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for pg in ctx.geo.piles:
        vm = ctx.pile_vm.get(pg.pile_id)
        blend_campaign = ctx.state.blend_campaigns.get(pg.pile_id, {})
        if not isinstance(blend_campaign, dict):
            blend_campaign = {}
        blend_components = [
            component for component in blend_campaign.get("composition", [])
            if isinstance(component, dict)
            and float(component.get("target_fraction", 0.0) or 0.0) > 0
        ]
        blend_location_status = str(
            ctx.state.blend_location_status.get(pg.pile_id, "") or "")
        blend_location_recipe = str(
            ctx.state.blend_location_labels.get(pg.pile_id, "") or "")
        mass = _number(
            vm.mass_t if vm is not None else pg.display_mass_t or 0.0)
        capacity = _number(
            vm.capacity_t if vm is not None else pg.capacity_t)
        fill = (
            _number(vm.fill_pct)
            if vm is not None else
            (100.0 * mass / capacity
             if mass is not None and capacity not in (None, 0.0) else 0.0)
        )
        repere = list(ctx.state.space_repere_ends.get(
            pg.pile_id, (0, 1000)))
        bands: List[Dict[str, Any]] = []
        if vm is not None and vm.segments:
            for segment in vm.segments:
                segment_mass = _number(segment.mass_t)
                bands.append({
                    "gradeId": segment.grade,
                    "label": segment.grade,
                    "massT": segment_mass,
                    "inventoryFraction": (
                        segment_mass / mass
                        if segment_mass is not None and mass not in (None, 0.0)
                        else 0.0
                    ),
                    "repereFrom": round(
                        repere[0] + segment.frac_from
                        * (repere[1] - repere[0])),
                    "repereTo": round(
                        repere[0] + segment.frac_to
                        * (repere[1] - repere[0])),
                })
        elif vm is not None and vm.grade:
            bands.append({
                "gradeId": vm.grade,
                "label": vm.grade_label or vm.grade,
                "massT": mass,
                "inventoryFraction": 1.0 if mass else 0.0,
                "repereFrom": repere[0],
                "repereTo": repere[1] if mass else repere[0],
            })
        else:
            for component in pg.display_grade_components:
                component_mass = (
                    mass * component.mass_fraction if mass is not None else None)
                bands.append({
                    "gradeId": component.grade_id,
                    "label": (
                        component.grade_label or component.grade_name
                        or component.grade_id
                    ),
                    "massT": component_mass,
                    "inventoryFraction": component.mass_fraction,
                    "repereFrom": round(
                        repere[0] + component.frac_from
                        * (repere[1] - repere[0])),
                    "repereTo": round(
                        repere[0] + component.frac_to
                        * (repere[1] - repere[0])),
                })
            if not bands and pg.display_grade_id:
                bands.append({
                    "gradeId": pg.display_grade_id,
                    "label": pg.display_grade_label or pg.display_grade_id,
                    "massT": mass,
                    "inventoryFraction": 1.0 if mass else 0.0,
                    "repereFrom": repere[0],
                    "repereTo": repere[1] if mass else repere[0],
                })

        # A generated blend matrix is a planned composition for this physical
        # pile. Display it as proportional bands and keep the status explicit;
        # this does not pretend to reconstruct individual chevron layers.
        if blend_components:
            bands = []
            cumulative = 0.0
            occupied_fraction = max(min((fill or 0.0) / 100.0, 1.0), 0.0)
            for component in blend_components:
                fraction = float(component.get(
                    "target_fraction", 0.0) or 0.0)
                grade_id = str(component.get("grade_id", "") or "")
                start = cumulative
                cumulative += fraction
                bands.append({
                    "gradeId": grade_id,
                    "label": grade_id,
                    "massT": (mass * fraction if mass is not None else None),
                    "inventoryFraction": fraction,
                    "repereFrom": round(repere[0] + start
                                         * occupied_fraction
                                         * (repere[1] - repere[0])),
                    "repereTo": round(repere[0] + cumulative
                                       * occupied_fraction
                                       * (repere[1] - repere[0])),
                })

        grade_ids = _unique(
            str(band["gradeId"]) for band in bands if band.get("gradeId"))
        state_name = (
            "Future" if pg.future else
            "Reference only" if not pg.modelled else
            "Active" if vm is not None and vm.active else
            "Empty" if not mass else
            "Available"
        )
        grade_text = " / ".join(grade_ids) or "—"
        blend_matrix_text = " + ".join(
            f"{component.get('grade_id', '')} "
            f"{100 * float(component.get('target_fraction', 0.0) or 0.0):.1f}%"
            for component in blend_components)
        blend_quality = blend_campaign.get("resulting_quality", {})
        blend_quality_text = " · ".join(
            f"{parameter} {float(value):g}"
            for parameter, value in sorted(blend_quality.items())) \
            if isinstance(blend_quality, dict) else ""
        receiving_checks = [
            check for check in blend_campaign.get("receiving_checks", [])
            if isinstance(check, dict)]
        receiving_text = (
            "Within all intervals"
            if receiving_checks and all(
                bool(check.get("on_spec", False))
                for check in receiving_checks)
            else "Outside at least one interval"
            if receiving_checks else "")
        quantity = (
            f"{_compact_mass(mass)} / {_compact_mass(capacity)}"
            f" physical ({fill or 0:.0f}%)"
            if capacity else f"{_compact_mass(mass)} physical"
        )
        tsm_equivalent = _number(
            vm.tsm_equivalent_t if vm is not None else None)
        tsm_label = vm.tsm_label if vm is not None else ""
        material_class = vm.material_class if vm is not None else ""
        out.append({
            "kind": "pile",
            "id": pg.pile_id,
            "site": ctx.site_for(pg.pile_id),
            "label": _public_text(pg.canvas_label or pg.label or pg.pile_id),
            "state": state_name,
            "modelled": bool(pg.modelled),
            "displayOnly": not bool(pg.modelled),
            "future": bool(pg.future),
            "massT": mass,
            "capacityT": capacity,
            "fillPct": fill,
            "materialClass": material_class,
            "tsmEquivalentT": tsm_equivalent,
            "tsmLabel": tsm_label,
            "gradeIds": grade_ids,
            "gradeBands": bands,
            "blendLocation": bool(blend_location_status),
            "blendStatus": blend_location_status,
            "blendRecipe": blend_location_recipe or blend_matrix_text,
            "repereFrom": repere[0],
            "repereTo": repere[1],
            "selected": (
                ctx.opts.selected_asset == pg.pile_id
                or bool(ctx.selected_job
                        and ctx.selected_job.space == pg.pile_id)
            ),
            "details": _details(
                Tonnage=quantity,
                **({tsm_label: _compact_mass(tsm_equivalent)}
                   if tsm_equivalent is not None and tsm_label else {}),
                Grade=grade_text,
                **({
                    "Blending location": "M16 · " + pg.pile_id,
                    "Blend status": blend_location_status,
                    "Blend recipe": (
                        blend_location_recipe or blend_matrix_text
                        or "Recipe not set"),
                } if blend_location_status else {}),
                **({
                    "Blend mode": (
                        "Optimized shares"
                        if str(blend_campaign.get("mode", "")) == "OPTIMIZED"
                        else "Fixed matrix"),
                    "Blend decision": str(blend_campaign.get(
                        "decision_status", "")),
                    "Blend matrix": blend_matrix_text,
                    "Blend quality": blend_quality_text,
                    "Receiving checks": receiving_text,
                } if blend_components else {}),
                Position=format_repere_range(*repere),
            ),
        })
    return out


def _rail_assets(ctx: _Context) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for rail in ctx.geo.rails:
        machine_ids = [
            machine.machine_id for machine in ctx.geo.machines
            if machine.rail_id == rail.rail_id
        ]
        travel = [
            round(1000 * rail.travel[0]),
            round(1000 * rail.travel[1]),
        ]
        out.append({
            "kind": "rail",
            "id": rail.rail_id,
            "site": ctx.site_for(
                rail.rail_id,
                related_ids=[*rail.flanks, *machine_ids],
            ),
            "label": _public_text(rail.label or rail.rail_id),
            "state": (
                "Future" if machine_ids and all(
                    ctx.geometry_machines[mid].future for mid in machine_ids)
                else "Configured"
            ),
            "machineType": rail.kind,
            "machineIds": machine_ids,
            "servedPileIds": list(rail.flanks),
            "workingSides": rail.working_sides,
            "travelRepere": travel,
            "selected": (
                ctx.opts.selected_asset == rail.rail_id
                or bool(ctx.selected_job
                        and ctx.selected_job.machine in machine_ids)
            ),
            "details": _details(
                Type=f"{rail.kind.replace('_', ' ')} machine line",
                Serves=", ".join(rail.flanks) or "—",
                Travel=format_repere_range(*travel),
            ),
        })
    return out


def _machine_assets(ctx: _Context) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for mg in ctx.geo.machines:
        vm = ctx.machine_vm.get(mg.machine_id)
        machine_state = _machine_state(ctx.state, mg, vm)
        pos = ctx.state.machine_pos.get(mg.machine_id)
        position = (
            pos.repere if pos is not None and pos.repere is not None
            else mg.home_repere
        )
        served = (
            list(vm.reachable) if vm is not None and vm.reachable
            else mg.served()
        )
        rate = _number(vm.rate_tph if vm is not None else None)
        maximum = _number(vm.rate_max_tph if vm is not None else None)
        job_id = vm.job_id if vm is not None else ""
        target_id = vm.target_space if vm is not None else ""
        destination_id = vm.dest_id if vm is not None else ""
        job_text = job_id or "—"
        if job_id and target_id:
            job_text += f" → {target_id}"
            if destination_id:
                job_text += f" → {destination_id}"
        out.append({
            "kind": "machine",
            "id": mg.machine_id,
            "site": ctx.site_for(
                mg.machine_id, related_ids=[*served, mg.rail_id]),
            "label": _public_text(mg.label or mg.machine_id),
            "state": machine_state,
            "machineType": vm.kind if vm is not None else mg.kind,
            "railId": mg.rail_id,
            "servedPileIds": served,
            "displayOnly": bool(mg.display and vm is None),
            "planningEnabled": (
                bool(vm.planning_enabled) if vm is not None else False),
            "future": bool(mg.future),
            "jobId": job_id or None,
            "targetId": target_id or None,
            "destinationId": destination_id or None,
            "positionRepere": int(position),
            "workRepere": (
                list(pos.work_repere)
                if pos is not None and pos.work_repere is not None else None
            ),
            "rateTph": rate,
            "maximumRateTph": maximum,
            "selected": (
                ctx.opts.selected_asset == mg.machine_id
                or bool(ctx.selected_job
                        and ctx.selected_job.machine == mg.machine_id)
            ),
            "details": _details(
                State=machine_state,
                Job=job_text,
                Position=format_repere(int(position)),
                Rate=_compact_rate(rate, maximum),
            ),
        })
    return out


def _route_grade_ids(ctx: _Context, conveyor) -> List[str]:
    if conveyor.machine_id:
        return _unique(
            job.grade for job in ctx.state.jobs
            if job.machine == conveyor.machine_id and job.grade)
    return _unique(
        ctx.state.flows[link_id].grade
        for link_id in conveyor.link_ids
        if link_id in ctx.state.flows and ctx.state.flows[link_id].grade)


def _route_assets(ctx: _Context) -> List[Dict[str, Any]]:
    # Import lazily to keep this adapter independent of Streamlit render setup.
    from ..process_mimic import _conv_state

    out: List[Dict[str, Any]] = []
    for conveyor in ctx.geo.conveyors:
        raw_state, flow, capacity, _grade_color, selected = _conv_state(
            ctx.state, conveyor, ctx.opts)
        flow = _number(flow)
        capacity = _number(capacity)
        source_ids = list(conveyor.link_ids)
        endpoint_ids = ctx.endpoint_asset_ids(source_ids)
        model_links = []
        for link_id in source_ids:
            link = ctx.links.get(link_id)
            if link is not None:
                model_links.append({
                    "id": link.id,
                    "fromNodeId": link.src,
                    "toNodeId": link.dst,
                })
        state_name = _route_state(raw_state)
        out.append({
            "kind": "route",
            "id": conveyor.cid,
            "site": ctx.site_for(
                conveyor.cid,
                source_ids=source_ids,
                related_ids=[*endpoint_ids, conveyor.machine_id],
            ),
            "label": _public_text(conveyor.tag or conveyor.cid),
            "state": state_name,
            "linkIds": source_ids,
            "machineId": conveyor.machine_id or None,
            "endpointAssetIds": endpoint_ids,
            "modelLinks": model_links,
            "planningEnabled": bool(conveyor.planning_enabled),
            "displayOnly": not bool(conveyor.planning_enabled),
            "future": bool(conveyor.future),
            "flowTph": flow,
            # ``None`` is intentional for grouped visual routes: summing link
            # capacities would invent a physical belt capacity.
            "capacityTph": capacity,
            "gradeIds": _route_grade_ids(ctx, conveyor),
            "selected": bool(selected),
            "details": _details(
                Type="conveyor / route",
                State=state_name,
                Flow=_compact_rate(flow, capacity),
                Links=", ".join(source_ids) or "display only",
            ),
        })
    return out


def _process_line_assets(ctx: _Context) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for line in ctx.geo.process_lines:
        state_name = _line_state(line.status)
        related_destinations = [
            destination.id for destination in ctx.sc.destinations
            if destination.process_stage
            and destination.process_stage
            in PROCESS_LINE_STAGE_MAP.get(line.line_id, ())
        ]
        residence = _number(line.residence_minutes)
        out.append({
            "kind": "processLine",
            "id": line.line_id,
            "site": ctx.site_for(
                line.line_id, related_ids=related_destinations),
            "label": _public_text(line.label or line.line_id),
            "state": state_name,
            "configurationStatus": str(line.status).upper(),
            "residenceMinutes": residence,
            "treatmentTime": _public_text(line.treatment_time) or None,
            "destinationIds": related_destinations,
            "selected": ctx.opts.selected_asset == line.line_id,
            "details": _details(
                Type="process line",
                Status=state_name,
                Residence=(
                    f"{residence:g} min" if residence is not None else "—"),
                Treatment=_public_text(line.treatment_time) or None,
            ),
        })
    return out


def _equipment_identity(equipment, index: int, site: str) -> tuple[str, str]:
    """Use the renderer's canonical identity resolver for exact SVG parity."""
    # Imported lazily so package initialization remains acyclic.
    from .svg_mimic import _equipment_identity as renderer_identity

    return renderer_identity(equipment, index, site)


def _equipment_state(ctx: _Context, equipment, equipment_id: str) -> str:
    if equipment_id in ctx.destinations:
        lifecycle = destination_line_operating_state(ctx.sc, equipment_id)
        status = destination_line_status(ctx.sc, equipment_id)
        return _destination_state(status, lifecycle)
    flows = [
        ctx.state.flows[link_id] for link_id in equipment.link_ids
        if link_id in ctx.state.flows
    ]
    if not flows:
        return "Configured"
    if any(flow.running for flow in flows):
        return "Running"
    if flows and all(flow.tripped for flow in flows):
        return "Unavailable"
    return "Idle"


def _equipment_assets(ctx: _Context) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for index, equipment in enumerate(ctx.geo.equipment):
        endpoint_ids = ctx.endpoint_asset_ids(equipment.link_ids)
        equipment_site = ctx.site_for(
            str(equipment.label or ""),
            source_ids=equipment.link_ids,
            related_ids=endpoint_ids,
        )
        if equipment.kind == "corridor_in":
            equipment_site = "MPH"
        elif equipment.kind == "corridor_out" and equipment.link_ids:
            first_link = ctx.links.get(equipment.link_ids[0])
            source_node = (
                ctx.nodes.get(first_link.src) if first_link is not None
                else None
            )
            if source_node is not None and source_node.site:
                equipment_site = source_node.site
        equipment_id, asset_kind = _equipment_identity(
            equipment, index, equipment_site)
        state_name = _equipment_state(ctx, equipment, equipment_id)
        detail_rows = _details(
            Type=equipment.kind.replace("_", " "),
            State=state_name,
            Links=", ".join(equipment.link_ids) or "—",
        )
        if equipment_id == "BG-TRAIN":
            train_rows: List[Dict[str, str]] = []
            for destination_id, label in (
                ("BG-TRAIN-PROD", "Product demand"),
                ("BG-TRAIN-RAW", "Raw demand"),
            ):
                destination = ctx.destinations.get(destination_id)
                if destination is None:
                    continue
                rate = ctx.sc.demand_tph(
                    destination_id, ctx.state.bucket)
                grade = _configured_demand_grade(
                    ctx.sc, destination_id, ctx.state.bucket) or "—"
                train_rows.extend(_details(
                    **{label: f"{rate:,.0f} t/h · {grade}"}
                ))
            detail_rows[2:2] = train_rows
        out.append({
            "kind": asset_kind,
            "role": equipment.kind,
            "id": equipment_id,
            "site": equipment_site,
            "label": _public_text(equipment.label or equipment.kind),
            "state": state_name,
            "linkIds": list(equipment.link_ids),
            "endpointAssetIds": endpoint_ids,
            "selected": (
                ctx.opts.selected_asset == equipment_id
                or ctx.opts.selected_asset in equipment.link_ids
            ),
            "details": detail_rows,
        })
    return out


def _destination_assets(ctx: _Context, represented_sites: Sequence[str]
                        ) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    visible_labels = {
        str(equipment.label or "").strip()
        for equipment in ctx.geo.equipment
        if equipment.kind == "destbox"
    }
    process_line_ids = {line.line_id for line in ctx.geo.process_lines}
    horizon = max(0, int(ctx.sc.sim.horizon_buckets))
    dt_h = float(ctx.sc.sim.dt_h)
    for destination in ctx.sc.destinations:
        if destination.site not in represented_sites:
            continue
        lifecycle = destination_line_operating_state(
            ctx.sc, destination.id)
        route_status = destination_line_status(ctx.sc, destination.id)
        state_name = _destination_state(route_status, lifecycle)
        demand = _number(ctx.sc.demand_tph(
            destination.id, ctx.state.bucket))
        configured_demand = _number(_configured_demand_tph(
            ctx.sc, destination.id, ctx.state.bucket))
        requested_grade = _configured_demand_grade(
            ctx.sc, destination.id, ctx.state.bucket)
        horizon_demand = _number(sum(
            ctx.sc.demand_tph(destination.id, bucket) * dt_h
            for bucket in range(horizon)
        ))
        active_delivery = _number(sum(
            job.rate_tph for job in ctx.state.jobs
            if job.dest_id == destination.id
        ))
        visual_ids: List[str] = []
        if destination.id in visible_labels:
            visual_ids.append(destination.id)
        if destination.id in ("BG-TRAIN-PROD", "BG-TRAIN-RAW") \
                and "BG-TRAIN" in visible_labels:
            visual_ids.append("BG-TRAIN")
        for line_id, stages in PROCESS_LINE_STAGE_MAP.items():
            if destination.process_stage in stages and \
                    line_id in process_line_ids:
                visual_ids.append(line_id)
        visual_ids = _unique(visual_ids)
        out.append({
            "kind": "destination",
            "id": destination.id,
            "site": destination.site,
            "label": destination.id,
            "state": state_name,
            "lifecycleState": lifecycle.title(),
            "routeStatus": route_status.title(),
            "requestedGrade": requested_grade,
            "demandKind": destination.demand_kind,
            "demandRateTph": demand,
            "configuredDemandRateTph": configured_demand,
            "horizonDemandT": horizon_demand,
            "activeDeliveryRateTph": active_delivery,
            "visualIds": visual_ids,
            "drawn": bool(visual_ids),
            "selected": ctx.opts.selected_asset == destination.id,
            "details": _details(
                Lifecycle=state_name,
                Grade=requested_grade or "—",
                Demand=f"{demand or 0:,.0f} t/h",
                Delivery=f"{active_delivery or 0:,.0f} t/h",
            ),
        })
    return out


def _merge_asset_rows(rows: Sequence[Dict[str, Any]]
                      ) -> List[Dict[str, Any]]:
    """Merge visual/model facets that intentionally share one canonical ID."""
    by_id: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    list_fields = {
        "gradeIds", "linkIds", "endpointAssetIds", "servedPileIds",
        "machineIds", "destinationIds", "visualIds",
    }
    for row in rows:
        asset_id = str(row["id"])
        if asset_id not in by_id:
            by_id[asset_id] = dict(row)
            order.append(asset_id)
            continue
        current = by_id[asset_id]
        merged = {**current, **row}
        merged["kind"] = current.get("kind", row.get("kind"))
        for field in list_fields:
            if field in current or field in row:
                merged[field] = _unique([
                    *current.get(field, []),
                    *row.get(field, []),
                ])
        detail_by_key = {
            str(item.get("key", "")): dict(item)
            for item in current.get("details", [])
        }
        for item in row.get("details", []):
            detail_by_key[str(item.get("key", ""))] = dict(item)
        merged["details"] = list(detail_by_key.values())
        merged["selected"] = bool(
            current.get("selected") or row.get("selected"))
        by_id[asset_id] = merged
    return [by_id[asset_id] for asset_id in order]


def build_snapshot(sc, state, geo, opts, *, basis: str = "") -> Dict[str, Any]:
    """Return JSON-safe canonical metadata for the redesigned flow viewer.

    Parameters are the same immutable objects already consumed by the
    production SVG renderer.  ``basis`` is an optional display label override;
    it does not change the resolved state.
    """
    ctx = _Context(sc, state, geo, opts)
    represented_sites = _represented_sites(sc, state, geo)
    destinations = _destination_assets(ctx, represented_sites)
    assets = _merge_asset_rows([
        *_pile_assets(ctx),
        *_rail_assets(ctx),
        *_machine_assets(ctx),
        *_route_assets(ctx),
        *_process_line_assets(ctx),
        *_equipment_assets(ctx),
        *destinations,
    ])
    grades = [{
        "id": grade.id,
        "label": _GRADE_LABELS.get(grade.id, grade.id),
        "color": grade_color(grade.id),
        "materialClass": grade.material_class,
    } for grade in sc.grades]
    lifecycle = {
        destination["id"]: destination["lifecycleState"]
        for destination in destinations
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "timestamp": state.timestamp,
        "basis": basis or state.basis,
        "bucket": int(state.bucket),
        "site": geo.site_id,
        "view": {
            "id": geo.site_id,
            "title": _public_text(geo.title),
            "notToScale": bool(geo.not_to_scale),
            "sites": represented_sites,
        },
        "filters": {
            "gradeIds": list(opts.grade_filter),
            "activeFlowsOnly": bool(opts.active_flows_only),
            "alarmsOnly": bool(opts.alarms_only),
            "showFuture": any(
                asset.get("future", False) for asset in assets),
            "selectedAssetId": opts.selected_asset or None,
            "selectedJobId": opts.selected_job or None,
        },
        "grades": grades,
        "lifecycle": lifecycle,
        "destinations": destinations,
        "assets": assets,
    }


__all__ = ["SCHEMA_VERSION", "build_snapshot"]
