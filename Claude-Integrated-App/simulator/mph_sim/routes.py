"""Route-network model — layer 3.

A real directed graph of nodes and links (capacities, minimum stable rates,
availability, transit delay, shared/conflict groups, gate state, changeover),
deliberately separate from the ASP free-text 'Route' (report §14.2).
Self-contained (no networkx): the networks are tiny (<100 nodes).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .domain import Link, Node, Scenario


@dataclass
class Path:
    links: List[str]
    transit_buckets: int
    capacity_tph: float              # bottleneck capacity along the path

    def uses(self, link_id: str) -> bool:
        return link_id in self.links


class RouteGraph:
    def __init__(self, nodes: List[Node], links: List[Link]):
        self.nodes: Dict[str, Node] = {n.id: n for n in nodes}
        self.links: Dict[str, Link] = {l.id: l for l in links}
        self._out: Dict[str, List[Link]] = {}
        for l in links:
            self._out.setdefault(l.src, []).append(l)

    @classmethod
    def from_scenario(cls, sc: Scenario) -> "RouteGraph":
        return cls(sc.nodes, sc.links)

    # ---- path enumeration ----------------------------------------------
    def paths(self, src: str, dst: str, only_available: bool = True,
              max_paths: int = 8) -> List[Path]:
        """All simple paths src -> dst (DFS), shortest (fewest links) first."""
        out: List[Path] = []

        def dfs(node: str, seen: Tuple[str, ...], acc: List[Link]):
            if len(out) >= max_paths:
                return
            if node == dst:
                if acc:
                    out.append(Path(
                        links=[l.id for l in acc],
                        transit_buckets=sum(l.transit_buckets for l in acc),
                        capacity_tph=min(l.capacity_tph for l in acc),
                    ))
                return
            for l in self._out.get(node, []):
                if only_available and not l.available:
                    continue
                if l.dst in seen:
                    continue
                dfs(l.dst, seen + (l.dst,), acc + [l])

        dfs(src, (src,), [])
        out.sort(key=lambda p: (len(p.links), -p.capacity_tph))
        return out

    def best_path(self, src: str, dst: str) -> Optional[Path]:
        ps = self.paths(src, dst)
        return ps[0] if ps else None

    # ---- analyses --------------------------------------------------------
    def conflict_groups(self) -> Dict[str, List[str]]:
        groups: Dict[str, List[str]] = {}
        for l in self.links.values():
            if l.conflict_group:
                groups.setdefault(l.conflict_group, []).append(l.id)
        return {g: ls for g, ls in groups.items() if len(ls) > 1}

    def shared_groups(self) -> Dict[str, List[str]]:
        groups: Dict[str, List[str]] = {}
        for l in self.links.values():
            if l.shared_group:
                groups.setdefault(l.shared_group, []).append(l.id)
        return {g: ls for g, ls in groups.items() if len(ls) > 1}

    def unavailable_links(self) -> List[str]:
        return [l.id for l in self.links.values() if not l.available]

    def bottlenecks(self, link_load_tph: Dict[str, float],
                    threshold: float = 0.85) -> List[Tuple[str, float]]:
        """Links loaded above threshold of capacity: (link_id, utilisation)."""
        res = []
        for lid, load in link_load_tph.items():
            l = self.links.get(lid)
            if l and l.capacity_tph > 0:
                u = load / l.capacity_tph
                if u >= threshold:
                    res.append((lid, u))
        return sorted(res, key=lambda x: -x[1])


def reachability_matrix(sc: Scenario) -> Dict[str, Dict[str, bool]]:
    """machine id -> {space id -> reachable}. Configured data, not inferred."""
    out: Dict[str, Dict[str, bool]] = {}
    for m in sc.machines:
        out[m.id] = {s.id: (s.id in m.reachable_spaces) for s in sc.spaces
                     if s.site == m.site}
    return out


def space_node(sc: Scenario, space_id: str) -> str:
    sp = sc.space(space_id)
    return sp.node_id or f"SPACE:{space_id}"


def reclaim_candidates(sc: Scenario, graph: RouteGraph, paths_per_pair: int = 2):
    """(machine, space, destination, Path) tuples the optimiser may use.

    Filters: reclaimer kind, machine availability flag, configured reachability,
    same site, and existence of an available, storage-transit-free path from the
    space to the destination. A final-destination reclaim candidate must not
    cross a delayed corridor: mine reclaimers dispatch into C1/C2, their material
    arrives and is stacked at M16, and only an M16 reclaimer may then serve
    PAP/drying. Up to `paths_per_pair` parallel local paths are offered per tuple.
    """
    cands = []
    for m in sc.machines:
        if m.kind != "reclaimer" or not m.available or \
                not getattr(m, "planning_enabled", True):
            continue          # planning-disabled (e.g. unselected OPTIONAL
            #                   equipment) must NEVER receive solver jobs
        for sid in m.reachable_spaces:
            try:
                sp = sc.space(sid)
            except StopIteration:
                continue
            src = space_node(sc, sid)
            for d in sc.destinations:
                if getattr(d, "demand_kind", "delivery") == "process_feed":
                    # Washing-line feed is fulfilled only by its explicit
                    # yield-bearing ProcessTransfer. Treating the WIP node as
                    # a normal destination would bypass the process model.
                    continue
                valid_paths = (
                    p for p in graph.paths(src, d.node_id)
                    if p.transit_buckets == 0
                    and space_transit_free(sc, graph, p)
                )
                for p in list(valid_paths)[:paths_per_pair]:
                    cands.append((m.id, sid, d.id, p))
    return cands


def space_transit_free(sc: Scenario, graph: RouteGraph, p: Path) -> bool:
    """True when no INTERMEDIATE node of the path is a stock space.

    A stockpile is storage, not a transfer tower: material cannot flow
    THROUGH it within a bucket. Without this guard, a path such as
    wash-tower → BG-P1 → BG-OUT → C2 → M16 would let the solver teleport
    material across a pile (observed with the experimental wash-supply
    exit before this filter).
    """
    space_nodes = {space_node(sc, s.id) for s in sc.spaces}
    return not any(graph.links[l].dst in space_nodes for l in p.links[:-1])


def stack_candidates(sc: Scenario, graph: RouteGraph, source_node: str):
    """(stacker, space, Path) tuples for stacking arrivals from source_node.

    Only space-transit-free paths qualify (see ``space_transit_free``) —
    stacking reaches its target pile over conveyors, never through
    another stockpile.
    """
    cands = []
    for m in sc.machines:
        if m.kind != "stacker" or not m.available or \
                not getattr(m, "planning_enabled", True):
            continue
        for sid in m.reachable_spaces:
            try:
                sp = sc.space(sid)
            except StopIteration:
                continue
            p = next((pp for pp in graph.paths(source_node,
                                               space_node(sc, sid))
                      if space_transit_free(sc, graph, pp)), None)
            if p is not None:
                cands.append((m.id, sid, p))
    return cands
