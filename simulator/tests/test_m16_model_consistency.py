# -*- coding: utf-8 -*-
"""Regression suite for the M16/PAP model-consistency correction
(25-Jul-2026, report Rev V):

  * fresh built-ins, MIGRATED sessions and the exported sample data describe
    the same M16 topology — no obsolete `HUB:M16-RECLAIM`, generic `T06`/`T11`
    rail names, all-to-all reach or fixed H01/H02→PAP + H03/H04→DRY split;
  * the CONFIRMED gate-bank discharge is separated from the DERIVED/TBC
    PAP-versus-drying route selection, and evidence classes say so;
  * one shared 2,600 t/h current reception envelope (M16A-A-T01), including
    the corrected alternating in-transit profile;
  * the documented 10-minute changeover is NOT modelled and never claims to be;
  * a gate bank renders active when ANY bound link is active;
  * Future expansion extends ST01-C to bay 5 under the same adjacency rule and
    marks HP-09*…HP-12* as inferred display-only identifiers;
  * a stale scenario graph under corrected geometry is detected, not drawn.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mph_sim import datasets                                       # noqa: E402
from mph_sim.domain import Job, PlanResult                         # noqa: E402
from mph_sim.optimizer import SolveOptions, solve_plan             # noqa: E402
from mph_sim.persistence import scenario_from_json                 # noqa: E402
from mph_sim.validation import validate_scenario                   # noqa: E402
from ui.layout_geometry import m16_geometry, validate_geometry     # noqa: E402
from ui.process_mimic import (MimicOpts,                           # noqa: E402
                              build_process_mimic_state,
                              geometry_model_mismatch,
                              process_mimic_figure)
from ui.report_ref import REV, report_ref                          # noqa: E402

SAMPLE = ROOT / "sample_data"
HP = [f"HP-{i:02d}" for i in range(1, 9)]
REACH = {
    "ST01-A": {"HP-01", "HP-05"},
    "RC01-A": {"HP-01", "HP-05", "HP-02", "HP-06"},
    "ST01-B": {"HP-02", "HP-06", "HP-03", "HP-07"},
    "RC01-B": {"HP-03", "HP-07", "HP-04", "HP-08"},
    "ST01-C": {"HP-04", "HP-08"},
}
OBSOLETE_LINKS = {"L-H-PAP-A", "L-H-PAP-B", "L-H-DRY-A", "L-H-DRY-B",
                  "L-H-A-PAP", "L-H-A-DRY", "L-H-B-PAP", "L-H-B-DRY"}


@pytest.fixture(scope="module")
def s2():
    return datasets.mph_phosphate()


@pytest.fixture(scope="module")
def s3():
    return datasets.end_to_end()


def _exported(slug):
    return scenario_from_json(
        (SAMPLE / slug / "scenario.json").read_text(encoding="utf-8"))


# ---- 1. canonical data: fresh == migrated == exported ---------------------------
@pytest.mark.parametrize("slug,builder", [("s2", datasets.mph_phosphate),
                                          ("s3", datasets.end_to_end)])
def test_exported_sample_data_matches_the_fresh_builtin(slug, builder):
    exported = _exported(slug)
    fresh = builder()
    assert datasets.m16_topology_fingerprint(exported) == \
        datasets.m16_topology_fingerprint(fresh)
    assert exported.config.get("BUILTIN_BASELINE_REVISION") == \
        datasets.BUILTIN_BASELINE_REVISION
    assert not [i for i in validate_scenario(exported)
                if i.severity == "ERROR"]


@pytest.mark.parametrize("slug,builder", [("s2", datasets.mph_phosphate),
                                          ("s3", datasets.end_to_end)])
def test_a_stale_session_migrates_to_the_corrected_m16(slug, builder):
    stale = _exported(slug)
    # simulate a session stored before this correction
    stale.config["BUILTIN_BASELINE_REVISION"] = "2026-07-01-superseded"
    hub = type(stale.nodes[0])("HUB:M16-RECLAIM", "MPH", "gate",
                               "obsolete merged reclaim hub", 3, 4)
    stale.nodes.append(hub)
    for machine in stale.machines:
        if machine.id in REACH:
            machine.reachable_spaces = list(HP)      # the old all-to-all reach
            machine.rail = "T06" if machine.id.startswith("ST") else "T11"
    outcome = datasets.migrate_loaded_builtin(stale)
    assert outcome == "solver", "a stale M16 graph must invalidate the plan"
    fresh = builder()
    assert datasets.m16_topology_fingerprint(stale) == \
        datasets.m16_topology_fingerprint(fresh)
    assert not any(n.id == "HUB:M16-RECLAIM" for n in stale.nodes)
    assert not [l for l in stale.links if l.id in OBSOLETE_LINKS]
    for mid, expected in REACH.items():
        assert set(stale.machine(mid).reachable_spaces) == expected, mid
        assert stale.machine(mid).rail.startswith("M16A-"), mid
    assert not [i for i in validate_scenario(stale) if i.severity == "ERROR"]
    # a migrated session is current: migrating again is a no-op
    assert datasets.migrate_loaded_builtin(stale) == ""


@pytest.mark.parametrize("slug", ["s2", "s3"])
def test_no_obsolete_m16_artefacts_anywhere(slug):
    exported = _exported(slug)
    blob = (SAMPLE / slug / "links.csv").read_text(encoding="utf-8") + \
        (SAMPLE / slug / "nodes.csv").read_text(encoding="utf-8") + \
        (SAMPLE / slug / "machines.csv").read_text(encoding="utf-8")
    for obsolete in ("HUB:M16-RECLAIM", "L-H-PAP-A", "L-H-DRY-A", "H-SWING"):
        assert obsolete not in blob, obsolete
    assert not any(n.id == "HUB:M16-RECLAIM" for n in exported.nodes)
    for machine in exported.machines:
        if machine.id in REACH:
            assert machine.rail not in ("T06", "T11")
            assert len(machine.reachable_spaces) < 8
    # the old fixed H→destination split must not survive in any gate id
    gates = {l.gate_id for l in exported.links if l.gate_id}
    assert gates <= {"M16A-A-H01…H04", "M16A-B-H01…H04"}, gates


def test_stale_graph_under_corrected_geometry_is_detected(s2):
    fresh_ok = geometry_model_mismatch(s2, m16_geometry(s2))
    assert fresh_ok == []
    stale = _exported("s2")
    stale.links = [l for l in stale.links if not l.id.startswith("L-RTE-")]
    missing = geometry_model_mismatch(stale, m16_geometry(stale))
    assert missing, "a missing route link must be reported, not drawn silently"
    assert all(m.startswith("L-RTE-") for m in missing)


# ---- 2. PAP/drying route semantics ---------------------------------------------
@pytest.mark.parametrize("scenario", ["s2", "s3"])
def test_gate_bank_is_confirmed_and_route_selection_is_derived(scenario, s2, s3):
    sc = s2 if scenario == "s2" else s3
    for stream in ("A", "B"):
        bank = sc.link(f"L-H-{stream}")
        assert bank.src == f"HUB:M16-RCL-{stream}"
        assert bank.dst == f"HUB:M16-T13-{stream}"
        assert bank.capacity_tph == 1500
        assert bank.gate_id == f"M16A-{stream}-H01…H04"
        assert bank.gate_position == "", "the bank carries no destination"
        assert bank.evidence.status == "CONFIRMED"
        assert bank.conflict_group == "", "physical discharge is not a choice"
        for route_id, gate_position, cap in (
                ("PAP", "PAP", 1500),
                ("PAP2", "PAP-2", 1500),
                ("DRY", "DRY", 1299)):
            route = sc.link(f"L-RTE-{stream}-{route_id}")
            assert route.src == f"HUB:M16-T13-{stream}"
            assert route.capacity_tph == cap
            assert route.gate_position == gate_position
            assert route.conflict_group == f"M16A-{stream}-ROUTE"
            assert route.evidence.status == "DERIVED", \
                "confirmed belt ratings must not confirm the ASSIGNMENT"
            assert "NOT MODELLED" in route.evidence.note
            if route_id == "PAP2":
                assert route.available is False
                assert route.lifecycle == "FUTURE"
    # and no link claims a modelled changeover
    assert all(l.changeover_min == 0.0 for l in sc.links)


def test_route_tbc_equipment_is_dashed_amber(s2):
    geo = m16_geometry(s2)
    confluence = [e for e in geo.equipment
                  if e.kind == "destbox" and "CONFLUENCE" in e.label]
    assert confluence and confluence[0].tbc
    selectors = [e for e in geo.equipment
                 if e.kind == "diverter" and e.tbc
                 and any(li.startswith("L-RTE-") for li in e.link_ids)]
    assert len(selectors) == 2
    fig = process_mimic_figure(build_process_mimic_state(s2), geo)
    amber_dashed = [s for s in fig.layout.shapes
                    if getattr(s.line, "color", None) == "#b97a14"
                    and getattr(s.line, "dash", None) == "dash"]
    assert amber_dashed, "the TBC switchyard must not use confirmed styling"
    # the gates themselves stay CONFIRMED equipment: bound to the bank link
    gate_links = {tuple(e.link_ids) for e in geo.equipment
                  if e.kind == "gate"}
    assert gate_links == {("L-H-A",), ("L-H-B",)}


def test_assumptions_classify_the_route_and_reception_evidence(s2):
    by_id = {a.id: a for a in s2.assumptions}
    a4 = by_id["S2-A4"]
    assert "DERIVED/TBC" in a4.text and "NOT " in a4.text.upper()
    assert "changeover_min = 0" in a4.text
    a9 = by_id["S2-A9"]
    assert a9.status == "CONFIRMED"
    assert "M16VP02" in a9.source and "FUTURE" in a9.source
    assert "2,600 t/h" in a9.text
    a10 = by_id["S2-A10"]
    assert a10.status == "ASSUMED"
    assert "36,000 t/24 h" in a10.text
    assert "72,000 t/day" in a10.text
    blob = " ".join(f"{a.text} {a.placeholder}" for a in s2.assumptions)
    for stale_hash in ("dd752a28eceece76", "36eecc23e05da50b",
                       "33140d5110a3ef0a", "bba8081a6b6d1526"):
        assert stale_hash not in blob


# ---- 3. solver: one shared reception envelope ----------------------------------
@pytest.mark.parametrize("scenario", ["s2", "s3"])
def test_reception_envelope_is_one_shared_2600_group(scenario, s2, s3):
    sc = s2 if scenario == "s2" else s3
    groups = {l.shared_group for l in sc.links if l.id.startswith("L-STK-")}
    assert groups == {datasets.M16_RECEPTION_GROUP}
    assert all(sc.link(f"L-STK-{p}").capacity_tph == 2600 for p in HP)


def test_planned_stacking_never_exceeds_the_reception_envelope(s3):
    plan = solve_plan(s3, SolveOptions(max_time_s=3))
    per_bucket = {}
    for job in plan.jobs:
        if job.kind == "stack" and job.space in HP:
            per_bucket[job.start_bucket] = \
                per_bucket.get(job.start_bucket, 0.0) + job.rate_tph
    assert per_bucket, "S3 must stack corridor arrivals at M16"
    assert max(per_bucket.values()) <= 2600 + 1e-6, per_bucket


def test_initial_in_transit_respects_the_shared_reception(s3):
    per_bucket = {}
    for entry in s3.belt_profile:
        if entry.link_id in ("C1", "C2"):
            per_bucket[entry.arrive_bucket] = \
                per_bucket.get(entry.arrive_bucket, 0.0) + entry.mass_t
    assert per_bucket, "S3 keeps a pre-loaded corridor profile"
    assert max(per_bucket.values()) <= 2600 + 1e-6, per_bucket
    # both corridors still carry material — the fix is alternation, not removal
    assert sum(e.mass_t for e in s3.belt_profile if e.link_id == "C1") > 0
    assert sum(e.mass_t for e in s3.belt_profile if e.link_id == "C2") > 0


# ---- 4. UI ---------------------------------------------------------------------
def test_gate_bank_is_active_when_any_bound_link_is_active(s2):
    """A drying-only bucket must still light bank A."""
    space = "HP-05"                      # bay 1 -> A stream
    job = Job("J-DRY", "RC01-A", "reclaim", space, dest_id="DRY-FEED",
              grade="TBT", rate_tph=1000, tonnage_t=1000,
              start_bucket=0, end_bucket=0, start_x=0.0, stop_x=150.0,
              path=["L-RCL-HP-05", "L-H-A", "L-RTE-A-DRY"])
    state = build_process_mimic_state(s2, PlanResult("FEASIBLE", jobs=[job]),
                                      basis="plan", bucket=0)
    assert state.flows["L-RTE-A-DRY"].running
    assert not state.flows["L-RTE-A-PAP"].running
    geo = m16_geometry(s2)
    fig = process_mimic_figure(state, geo)
    # the four bank-A gate symbols are drawn with the running colour
    green = [s for s in fig.layout.shapes
             if s.type == "path" and getattr(s.line, "color", "") == "#2e7d32"]
    assert green, "the active bank must render running, not idle"


def test_future_expansion_extends_st01c_to_bay_5(s2):
    plain = m16_geometry(s2)
    future = m16_geometry(s2, show_future=True)
    assert set(plain.rail("M16A-C-T06").flanks) == {"HP-04", "HP-08"}
    assert set(future.rail("M16A-C-T06").flanks) == {
        "HP-04", "HP-08", "HP-09*", "HP-11*"}
    st01c = next(m for m in future.machines if m.machine_id == "ST01-C")
    assert set(st01c.served_piles) == {"HP-04", "HP-08", "HP-09*", "HP-11*"}
    # display only: the planning reach never gains a future hangar
    assert set(s2.machine("ST01-C").reachable_spaces) == {"HP-04", "HP-08"}
    assert validate_geometry(future) == []


def test_future_identifiers_are_marked_inferred(s2):
    future = m16_geometry(s2, show_future=True)
    for pid in ("HP-09*", "HP-11*", "HP-10*", "HP-12*"):
        pile = future.pile(pid)
        assert pile is not None and pile.future and not pile.modelled
        assert "INFERRED" in pile.display_note
    chips = " ".join(c.note for c in future.chips)
    assert "INFERRED display-only identifiers" in chips


def test_report_revision_label_is_central_not_hard_coded():
    assert REV and REV != "D", "the app must not pin an old report revision"
    assert report_ref("§16") == f"report Rev {REV} §16"
    for name in ("app.py", "ui/pages_overview.py"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "report Rev D" not in text, name
