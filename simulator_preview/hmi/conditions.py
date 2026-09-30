"""Consolidated abnormal-condition model.

NEW in this preview. The production page reports the same underlying facts,
but scattered: pile warnings inside the monitor view-model, tripped links and
concurrency status inside the mimic state, validation issues in a drawer at
the bottom of the app, the stale-graph check as a standalone banner. An
operator has to look in four places to answer "is anything wrong?".

This module reduces all of them to one ranked list with a single severity
vocabulary, which is what the ribbon, the inspector's CONDITIONS tab and the
exported state digest all read from.

Severity vocabulary (ISA-101 style, three levels only):
    ALARM     — a modelled physical/logical violation or a blocking input
                error. Requires action before the plan can be trusted.
    CAUTION   — degraded, approaching a limit, or unavailable equipment.
    ADVISORY  — evidence/coverage information: assumed values, TBC items,
                display-only geometry. Never an equipment condition.

It reads view-models only and never mutates them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

ALARM, CAUTION, ADVISORY = "ALARM", "CAUTION", "ADVISORY"
_ORDER = {ALARM: 0, CAUTION: 1, ADVISORY: 2}


@dataclass(frozen=True)
class Condition:
    severity: str
    tag: str                 # asset / entity the condition belongs to
    text: str
    source: str              # which layer produced it (for the digest)


def _shared_group_load(sc, state) -> List[Tuple[str, float, float]]:
    """(group, tph, cap) for every shared-capacity link group with flow.

    The M16 reception envelope is modelled as ``Link.shared_group`` with the
    group capacity equal to the minimum member capacity, so an aggregate over
    that value is a real modelled breach rather than a display artefact.
    """
    groups: Dict[str, List] = {}
    for l in sc.links:
        if l.shared_group:
            groups.setdefault(l.shared_group, []).append(l)
    out = []
    for g, links in sorted(groups.items()):
        cap = min(l.capacity_tph for l in links)
        tph = sum(state.flows[l.id].tph for l in links if l.id in state.flows)
        out.append((g, tph, cap))
    return out


def collect(sc, state, *, issues: Sequence = (), stale: Sequence[str] = (),
            geometry_gaps: int = 0) -> List[Condition]:
    """Rank every abnormal condition visible at the displayed bucket."""
    out: List[Condition] = []
    ms = state.monitor

    # --- model/geometry integrity (highest: nothing below can be trusted) ----
    for item in stale:
        out.append(Condition(
            ALARM, "SCENARIO GRAPH",
            f"loaded scenario is stale — {item}", "geometry_model_mismatch"))

    # --- blocking input errors ---------------------------------------------
    for i in issues:
        sev = getattr(i, "severity", "INFO")
        if sev == "ERROR":
            out.append(Condition(ALARM, getattr(i, "entity", "") or "INPUT",
                                 f"{getattr(i, 'code', '')}: {i.message}",
                                 "validate_scenario"))
        elif sev == "WARNING":
            out.append(Condition(CAUTION, getattr(i, "entity", "") or "INPUT",
                                 f"{getattr(i, 'code', '')}: {i.message}",
                                 "validate_scenario"))

    # --- links -------------------------------------------------------------
    for lid, f in sorted(state.flows.items()):
        if f.tripped:
            out.append(Condition(ALARM, lid, "conveyor/route tripped",
                                 "MimicState.flows"))
        elif f.cap_tph and f.utilisation_pct > 100.0 + 1e-6:
            out.append(Condition(
                ALARM, lid,
                f"flow {f.tph:,.0f} t/h exceeds capacity "
                f"{f.cap_tph:,.0f} t/h", "MimicState.flows"))

    # --- shared-capacity envelopes (M16 reception) --------------------------
    for g, tph, cap in _shared_group_load(sc, state):
        if tph > cap + 1e-6:
            out.append(Condition(
                ALARM, g,
                f"shared-capacity group at {tph:,.0f} t/h against a "
                f"{cap:,.0f} t/h envelope", "Link.shared_group"))

    # --- same-pile concurrency ---------------------------------------------
    for pid, c in sorted(state.concurrency.items()):
        if c.status == "violation":
            out.append(Condition(
                ALARM, pid,
                f"work zones overlap — separation {c.sep_m:,.0f} m below the "
                f"{c.req_m:,.0f} m minimum", "ConcurrencyVM"))
        elif c.status == "warning":
            out.append(Condition(
                CAUTION, pid,
                f"separation {c.sep_m:,.0f} m approaching the "
                f"{c.req_m:,.0f} m minimum", "ConcurrencyVM"))

    # --- stockpiles --------------------------------------------------------
    for sv in ms.sites.values():
        for p in sv.piles:
            for v in p.offspec_hard:
                out.append(Condition(ALARM, p.id,
                                     f"hard quality limit breached: {v}",
                                     "PileVM.offspec_hard"))
            if p.below_safety:
                out.append(Condition(
                    CAUTION, p.id,
                    f"below safety stock — {p.mass_t:,.0f} t against "
                    f"{p.safety_stock_t:,.0f} t", "PileVM.below_safety"))
            for w in p.warnings:
                out.append(Condition(CAUTION, p.id, w, "PileVM.warnings"))

    # --- machines ----------------------------------------------------------
    for sv in ms.sites.values():
        for m in sv.machines:
            if m.id in state.down_machines:
                out.append(Condition(ALARM, m.id,
                                     "unavailable — simulated outage window",
                                     "MimicState.down_machines"))
            elif m.status == "Unavailable":
                out.append(Condition(CAUTION, m.id, "unavailable",
                                     "MachineVM.status"))
            elif m.status == "Maintenance":
                out.append(Condition(CAUTION, m.id,
                                     "maintenance calendar window",
                                     "MachineVM.status"))

    # --- coverage / evidence (advisory only) -------------------------------
    for d in ms.discrepancies:
        out.append(Condition(CAUTION, "COVERAGE", d,
                             "MonitorState.discrepancies"))
    if geometry_gaps:
        out.append(Condition(
            ADVISORY, "MODEL COVERAGE",
            f"{geometry_gaps} geometry/data gaps recorded — see the coverage "
            "drawer", "SiteGeometry.gaps + MonitorState.gaps"))
    for i in issues:
        if getattr(i, "severity", "") == "INFO":
            out.append(Condition(ADVISORY, getattr(i, "entity", "") or "INPUT",
                                 f"{getattr(i, 'code', '')}: {i.message}",
                                 "validate_scenario"))

    out.sort(key=lambda c: (_ORDER[c.severity], c.tag))
    return out


def counts(conds: Sequence[Condition]) -> Dict[str, int]:
    return {
        ALARM: sum(1 for c in conds if c.severity == ALARM),
        CAUTION: sum(1 for c in conds if c.severity == CAUTION),
        ADVISORY: sum(1 for c in conds if c.severity == ADVISORY),
    }


def headline(conds: Sequence[Condition]) -> str:
    """One-line summary for the ribbon's message cell."""
    if not conds:
        return "all monitored conditions normal at the displayed bucket"
    first = conds[0]
    extra = len(conds) - 1
    txt = f"{first.severity} · {first.tag} — {first.text}"
    return txt + (f"   (+{extra} more)" if extra else "")


def rows(conds: Sequence[Condition]) -> List[Tuple[str, str, str]]:
    return [(c.severity, c.tag, c.text) for c in conds]
