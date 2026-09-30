"""Multi-grade stockpile-zone support (experimental, disabled by default).

The baseline model keeps ONE dominant/designated grade and one mass-weighted
quality snapshot per pile and solve (assumption S3-A7). This module adds the
smallest clean zone model on top of it WITHOUT touching the solver core:

    expand_pile_zones(sc)  ->  a deep-copied Scenario in which every zoned
    pile is replaced by per-zone sub-spaces ("HP-01#Z1", ...) that share the
    parent's route-graph node.

Because zones become ordinary StockSpaces, the ENTIRE existing constraint
stack applies unchanged per zone: inventory mass balance, capacity limits,
anti-contamination via ``designated_grade``, mass-weighted delivered
quality, machine exclusivity and the same-pile concurrency rules. The
x-boundaries of each zone bound its StartX/StopX cuts.

Deliberate representation choices (all inputs, nothing derived):
  * zone count, boundaries, grades, masses and capacities are REQUIRED
    inputs (``Scenario.pile_zones``) — the zone/campaign rules of every
    real site are TBC (S3-A2/S3-A7), so no built-in scenario defines any;
  * stacking goes only into a zone designated for the stacked grade or an
    explicitly free/empty zone (grade "") — richer compatibility matrices
    are missing engineering data and are NOT invented;
  * reclaiming is allowed only from zones flagged ``reclaim_face=True``
    (an input); automatic face-progression across zones inside one horizon
    is NOT modelled — that sequencing rule is exactly the missing input;
  * pile-level safety stock is not silently allocated to zones (explicit
    per-zone ``safety_stock_t`` input only).

When ``MULTI_GRADE_ZONES_ENABLED`` is false or a scenario carries no zone
inputs, ``expand_pile_zones`` returns the scenario UNCHANGED (identity) —
baseline behaviour and the deterministic S3 signature are preserved.
"""
from __future__ import annotations

import copy
from typing import Dict, List

from .domain import Scenario, StockSpace

MASS_TOL_T = 1.0        # zone-mass vs pile-mass reconciliation tolerance


def zones_enabled(sc: Scenario) -> bool:
    cfg = getattr(sc, "config", {}) or {}
    return bool(cfg.get("MULTI_GRADE_ZONES_ENABLED", False)) and \
        bool(getattr(sc, "pile_zones", []))


def zones_for(sc: Scenario, space_id: str):
    return [z for z in getattr(sc, "pile_zones", [])
            if z.space_id == space_id]


def validate_zones(sc: Scenario) -> List[str]:
    """Zone inputs are engineering data — reject anything inconsistent
    instead of silently repairing it."""
    problems: List[str] = []
    space_by_id = {s.id: s for s in sc.spaces}
    grade_ids = {g.id for g in sc.grades}
    by_space: Dict[str, list] = {}
    for z in getattr(sc, "pile_zones", []):
        by_space.setdefault(z.space_id, []).append(z)

    for pid, zs in by_space.items():
        sp = space_by_id.get(pid)
        if sp is None:
            problems.append(f"zone parent space '{pid}' does not exist")
            continue
        ids = [z.zone_id for z in zs]
        if len(ids) != len(set(ids)):
            problems.append(f"{pid}: duplicate zone ids {sorted(ids)}")
        for z in zs:
            if not (sp.x_from - 1e-6 <= z.x_from < z.x_to
                    <= sp.x_to + 1e-6):
                problems.append(
                    f"{pid}/{z.zone_id}: x-range [{z.x_from}, {z.x_to}] "
                    f"outside the pile range [{sp.x_from}, {sp.x_to}]")
            if z.grade and z.grade not in grade_ids:
                problems.append(f"{pid}/{z.zone_id}: unknown grade "
                                f"'{z.grade}'")
            if z.capacity_t <= 0:
                problems.append(f"{pid}/{z.zone_id}: zone capacity is a "
                                "required input (got "
                                f"{z.capacity_t})")
            if z.mass_t > z.capacity_t + MASS_TOL_T:
                problems.append(f"{pid}/{z.zone_id}: mass {z.mass_t:,.0f} t "
                                f"exceeds zone capacity {z.capacity_t:,.0f} t")
            if z.mass_t > 1 and not z.grade:
                problems.append(f"{pid}/{z.zone_id}: a zone holding mass "
                                "must state its grade")
        for a in zs:
            for b in zs:
                if a.zone_id < b.zone_id and \
                        a.x_from < b.x_to - 1e-6 and b.x_from < a.x_to - 1e-6:
                    problems.append(f"{pid}: zones {a.zone_id}/{b.zone_id} "
                                    "overlap in x")
        cap_sum = sum(z.capacity_t for z in zs)
        if cap_sum > sp.capacity_t + MASS_TOL_T:
            problems.append(f"{pid}: zone capacities sum to {cap_sum:,.0f} t "
                            f"> pile capacity {sp.capacity_t:,.0f} t")
        mass_sum = sum(z.mass_t for z in zs)
        if abs(mass_sum - sp.mass_t) > MASS_TOL_T:
            problems.append(
                f"{pid}: zone masses sum to {mass_sum:,.0f} t but the pile "
                f"snapshot holds {sp.mass_t:,.0f} t — reconcile the inputs")
        if mass_sum > MASS_TOL_T and not any(z.reclaim_face for z in zs):
            problems.append(f"{pid}: no zone is flagged reclaim_face — the "
                            "active face is a required input")
    return problems


def expand_pile_zones(sc: Scenario) -> Scenario:
    """Replace every zoned pile by per-zone sub-spaces (see module doc).

    Identity when the feature is disabled or no zone inputs exist. Raises
    ``ValueError`` on invalid zone inputs — required data is never guessed.
    """
    if not zones_enabled(sc):
        return sc
    problems = validate_zones(sc)
    if problems:
        raise ValueError("invalid pile-zone inputs: " + " · ".join(problems))

    out = copy.deepcopy(sc)
    zoned_ids = {z.space_id for z in out.pile_zones}
    new_spaces: List[StockSpace] = []
    zone_ids_by_parent: Dict[str, List[str]] = {}
    face_ids_by_parent: Dict[str, List[str]] = {}
    for sp in out.spaces:
        if sp.id not in zoned_ids:
            new_spaces.append(sp)
            continue
        for z in sorted(zones_for(out, sp.id), key=lambda z_: z_.x_from):
            zid = z.sub_space_id()
            zone_ids_by_parent.setdefault(sp.id, []).append(zid)
            if z.reclaim_face:
                face_ids_by_parent.setdefault(sp.id, []).append(zid)
            new_spaces.append(StockSpace(
                id=zid, site=sp.site, area=sp.area, kind=sp.kind,
                capacity_t=z.capacity_t, mass_t=z.mass_t,
                grade=z.grade if z.mass_t > 1 else (z.grade or ""),
                quality=dict(z.quality),
                state="mixed" if z.mass_t > 1 else "empty",
                designated_grade=z.grade,
                x_from=z.x_from, x_to=z.x_to,
                safety_stock_t=z.safety_stock_t,
                # zones share the parent's route-graph node, so every
                # existing link/path keeps working without graph edits
                node_id=sp.node_id or f"SPACE:{sp.id}",
                material_class=sp.material_class,
                profile_id=sp.profile_id,
                evidence=z.evidence))
    out.spaces = new_spaces

    for mach in out.machines:
        reach: List[str] = []
        for sid in mach.reachable_spaces:
            if sid not in zoned_ids:
                reach.append(sid)
            elif mach.kind == "reclaimer":
                # reclaim only from the designated active face (input)
                reach.extend(face_ids_by_parent.get(sid, []))
            else:
                reach.extend(zone_ids_by_parent.get(sid, []))
        mach.reachable_spaces = reach

    # zone rows are consumed by the expansion; keep the originals on the
    # UN-expanded scenario only (the expanded copy is a solver artefact)
    out.pile_zones = []
    return out


def parent_space_id(space_id: str) -> str:
    """'HP-01#Z2' -> 'HP-01'; plain ids pass through unchanged."""
    return space_id.split("#", 1)[0]


def zone_label(space_id: str) -> str:
    if "#" not in space_id:
        return space_id
    pid, zid = space_id.split("#", 1)
    return f"{pid} · zone {zid}"
