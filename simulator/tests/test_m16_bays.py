# -*- coding: utf-8 -*-
"""Acceptance suite for the M16 stockyard + PAP/drying-feed correction
(25-Jul-2026), against `Q80130-MZI-M16-ME-PFD-0001[3]` Rev 3 (IFU) with
`Q80130-MZI-M16-PR-PRO-0001` Rev 2 as the supporting source:

  * eight current 125,000 t hangars in FOUR side-by-side two-hangar rows,
    with the five current machine lines interleaved between them and spanning
    each served pair — no shared T06/T11 corridor and no detached corridor;
  * distinct canonical line ids M16A-A/B/C-T06 and M16A-A/B-T11;
  * the project-team bay-adjacency reach matrix enforced in the DOMAIN model,
    the route candidates, scenario validation, the ASP work-area rule and the
    UI highlighting — never all-to-all;
  * two separate clamshell/rod gate banks (M16A-A-H01…H04 / M16A-B-H01…H04)
    with the PAP/drying confluence explicitly TBC;
  * four future hangars + M16A-C-RC01 + M16A-D-ST01 only behind the Future
    Expansion toggle, never solver resources;
  * existing capacities, varied starting masses, grade bands, repères and
    Initial/Final state behaviour preserved;
  * the completed Louta and Benguerir geometry untouched.
"""
import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mph_sim import datasets                                       # noqa: E402
from mph_sim.adapters import (SimulatedAspAdapter,                 # noqa: E402
                              job_to_plan_item)
from mph_sim.domain import FixedJob, Job, PlanResult               # noqa: E402
from mph_sim.optimizer import SolveOptions, solve_plan             # noqa: E402
from mph_sim.routes import (RouteGraph, reachability_matrix,       # noqa: E402
                            reclaim_candidates, stack_candidates)
from mph_sim.validation import validate_scenario                   # noqa: E402
from ui.layout_geometry import (benguerir_geometry, geometry_for,   # noqa: E402
                                louta_geometry, m16_geometry,
                                validate_geometry)
from ui.process_mimic import (MimicOpts,                           # noqa: E402
                              build_process_mimic_state,
                              machine_draw_pos, process_mimic_figure)

HP = [f"HP-{i:02d}" for i in range(1, 9)]
BAYS = {1: ("HP-01", "HP-05"), 2: ("HP-02", "HP-06"),
        3: ("HP-03", "HP-07"), 4: ("HP-04", "HP-08")}
#: the authoritative reach matrix of this correction
REACH = {
    "ST01-A": {"HP-01", "HP-05"},
    "RC01-A": {"HP-01", "HP-05", "HP-02", "HP-06"},
    "ST01-B": {"HP-02", "HP-06", "HP-03", "HP-07"},
    "RC01-B": {"HP-03", "HP-07", "HP-04", "HP-08"},
    "ST01-C": {"HP-04", "HP-08"},
}
LINES = {"ST01-A": "M16A-A-T06", "RC01-A": "M16A-A-T11",
         "ST01-B": "M16A-B-T06", "RC01-B": "M16A-B-T11",
         "ST01-C": "M16A-C-T06"}
#: initial masses/grades that must survive this pass unchanged
START = {"HP-01": (80_000, "TBT"), "HP-02": (90_000, "BT"),
         "HP-03": (70_000, "MT"), "HP-04": (60_000, "TBT"),
         "HP-05": (74_000, "TBT"), "HP-06": (50_000, "BT"),
         "HP-07": (0, "BT"), "HP-08": (0, "TBT")}


@pytest.fixture(scope="module")
def s2():
    return datasets.mph_phosphate()


@pytest.fixture(scope="module")
def s3():
    return datasets.end_to_end()


@pytest.fixture(scope="module")
def s2_plan(s2):
    return solve_plan(s2, SolveOptions(max_time_s=2))


def _texts(fig):
    return " ".join(str(a.text) for a in fig.layout.annotations if a.text)


def _hovers(fig):
    return [str(t) for tr in fig.data
            for t in (tr.hovertext if isinstance(tr.hovertext, (list, tuple))
                      else [])]


# ---- 1. capacities, masses, grades and repères preserved ------------------------
@pytest.mark.parametrize("scenario", ["s2", "s3"])
def test_eight_hangars_keep_capacity_mass_grade_and_reperes(scenario, s2, s3):
    sc = s2 if scenario == "s2" else s3
    spaces = {sp.id: sp for sp in sc.spaces if sp.id in HP}
    assert sorted(spaces) == HP
    for pid, (mass, grade) in START.items():
        assert spaces[pid].capacity_t == 125_000, pid
        assert spaces[pid].mass_t == mass, pid          # varied, NOT 50%
        assert spaces[pid].grade == grade, pid
        assert (spaces[pid].repere_at_x_from,
                spaces[pid].repere_at_x_to) == (0, 1000), pid
    assert sum(sp.capacity_t for sp in spaces.values()) == 1_000_000


def test_pile_labels_stay_concise_and_hover_carries_the_evidence(s2):
    geo = m16_geometry(s2)
    fig = process_mimic_figure(build_process_mimic_state(s2), geo)
    canvas = _texts(fig)
    assert "80 kt · TBT" in canvas
    assert "50 kt · BT" in canvas
    assert "<b>HP-01</b> ·" not in canvas
    for pid in ("HP-01", "HP-06"):
        name = next(a for a in fig.layout.annotations if a.text == pid)
        assert name.y > geo.pile(pid).y1
        assert name.bgcolor is None
    # capacity/evidence/served machines/repères live in the hover, not the lane
    assert "125,000" not in canvas
    hover = [h for h in _hovers(fig) if h.startswith("<b>HP-01</b>")]
    assert hover, "HP-01 must have a hover"
    for fragment in ("80,000 / 125,000 t", "bay 1", "ST01-A (M16A-A-T06)",
                     "RC01-A (M16A-A-T11)", "R0–R1000", "INFERRED"):
        assert fragment in hover[0], fragment


# ---- 2/3. four two-hangar bays and five DISTINCT machine lines -------------------
def test_four_two_hangar_bays_are_rendered(s2):
    g = m16_geometry(s2)
    # Each bay is a single horizontal row of two side-by-side hangars. This
    # is the visual contract that makes one translating machine line's access
    # to both members of the pair immediately legible.
    for bay_no, piles in BAYS.items():
        lanes = [g.pile(pid) for pid in piles]
        assert all(lane is not None for lane in lanes), bay_no
        left, right = sorted(lanes, key=lambda lane: lane.x0)
        assert left.y0 == pytest.approx(right.y0), bay_no
        assert left.y1 == pytest.approx(right.y1), bay_no
        assert left.x1 < right.x0, bay_no
        box = [z for z in g.zones
               if z.x0 <= left.x0 and z.x1 >= right.x1
               and z.y0 <= left.y0 and z.y1 >= left.y1]
        assert box, f"bay {bay_no} has no grouping box"
    assert len([z for z in g.zones if not z.label]) >= 4
    # bays are separated: consecutive bays are split by a machine line
    ordered = sorted(BAYS.items(),
                     key=lambda kv: -g.pile(kv[1][0]).y0)
    for (bay_a, piles_a), (bay_b, piles_b) in zip(ordered, ordered[1:]):
        low = min(g.pile(p).y0 for p in piles_a)
        high = max(g.pile(p).y1 for p in piles_b)
        assert any(high < r.y < low for r in g.rails), \
            f"no machine line between bays {bay_a} and {bay_b}"


def test_each_serving_machine_line_spans_both_hangars_in_a_bay(s2):
    g = m16_geometry(s2)
    for bay_no, pile_ids in BAYS.items():
        pair = [g.pile(pid) for pid in pile_ids]
        for mid, (_line, bays, _kind) in datasets.M16_MACHINE_LINES.items():
            if bay_no not in bays:
                continue
            rail = g.rail(LINES[mid])
            assert rail.flank_depth == 1
            assert set(pile_ids) <= set(rail.flanks)
            assert rail.x0 <= min(p.x0 for p in pair)
            assert rail.x1 >= max(p.x1 for p in pair)
            assert not any(p.y0 <= rail.y <= p.y1 for p in pair)


@pytest.mark.parametrize("view", ["MPH", "CHAIN", "GLOBAL"])
def test_horizontal_pair_contract_survives_every_m16_view(view, s3):
    g = geometry_for(s3, view)
    for pile_ids in BAYS.values():
        left, right = sorted((g.pile(pid) for pid in pile_ids),
                             key=lambda pile: pile.x0)
        assert left.y0 == pytest.approx(right.y0), view
        assert left.y1 == pytest.approx(right.y1), view
        assert left.x1 < right.x0, view


def test_same_repere_on_pair_translates_machine_along_shared_line(s2):
    g = m16_geometry(s2)
    rail = g.rail("M16A-A-T06")
    left = g.pile("HP-01")
    right = g.pile("HP-05")
    lpos = machine_draw_pos(rail, left, 0.5)
    rpos = machine_draw_pos(rail, right, 0.5)
    assert lpos[1] == rpos[1] == rail.y
    assert lpos[0] < rpos[0]
    assert left.x0 <= lpos[2] <= left.x1
    assert right.x0 <= rpos[2] <= right.x1
    assert left.y0 <= lpos[3] <= left.y1
    assert right.y0 <= rpos[3] <= right.y1


@pytest.mark.parametrize("view", ["MPH", "CHAIN", "GLOBAL"])
def test_no_shared_t06_or_t11_corridor_remains(view, s3):
    g = geometry_for(s3, view)
    rail_ids = {r.rail_id for r in g.rails}
    assert "T06" not in rail_ids and "T11" not in rail_ids
    assert {"M16A-A-T06", "M16A-B-T06", "M16A-C-T06",
            "M16A-A-T11", "M16A-B-T11"} <= rail_ids, view
    # each M16 machine sits on its own line and no line carries two machines
    m16_lines = [m.rail_id for m in g.machines
                 if m.machine_id in LINES and not m.display]
    assert sorted(m16_lines) == sorted(LINES.values())
    assert len(set(m16_lines)) == 5
    for mid, line in LINES.items():
        mg = next(m for m in g.machines if m.machine_id == mid)
        assert mg.rail_id == line, mid
    # and no "machine corridor" zone detached from the bays
    assert not any("machine corridor" in z.label for z in g.zones), view


def test_machine_lines_flank_exactly_their_served_bays(s2):
    g = m16_geometry(s2)
    for mid, expected in REACH.items():
        rail = g.rail(LINES[mid])
        assert rail is not None, mid
        assert set(rail.flanks) == expected, mid
        assert rail.kind == ("stacker" if mid.startswith("ST") else "reclaimer")
        # the line never crosses through a hangar
        for pid in rail.flanks:
            pile = g.pile(pid)
            assert not (pile.y0 <= rail.y <= pile.y1), f"{mid}/{pid}"
    assert validate_geometry(g) == []


# ---- 4. the reach matrix is the DOMAIN model, not just a drawing -----------------
@pytest.mark.parametrize("scenario", ["s2", "s3"])
def test_domain_reachability_matrix_is_bay_adjacent(scenario, s2, s3):
    sc = s2 if scenario == "s2" else s3
    for mid, expected in REACH.items():
        machine = sc.machine(mid)
        assert set(machine.reachable_spaces) == expected, mid
        assert machine.rail == LINES[mid], mid
        assert len(machine.reachable_spaces) < 8, "all-to-all reach is back"
    matrix = reachability_matrix(sc)
    for mid, expected in REACH.items():
        assert {pid for pid, ok in matrix[mid].items() if ok and pid in HP} \
            == expected, mid


def test_route_candidates_never_offer_an_unreachable_pile(s2):
    graph = RouteGraph.from_scenario(s2)
    for mid, sid, _dest, _path in reclaim_candidates(s2, graph):
        if mid in REACH:
            assert sid in REACH[mid], f"{mid} offered {sid}"
    for mid, sid, _path in stack_candidates(s2, graph, "TOWER:M16-REC"):
        if mid in REACH:
            assert sid in REACH[mid], f"{mid} offered {sid}"
    # every bay is still stackable and reclaimable by SOMEBODY
    stackable = {sid for mid, sid, _ in
                 stack_candidates(s2, graph, "TOWER:M16-REC")}
    assert set(HP) <= stackable
    reclaimable = {sid for _m, sid, _d, _p in reclaim_candidates(s2, graph)}
    assert set(HP) <= reclaimable


def test_planned_jobs_respect_the_reach_matrix(s2, s2_plan):
    assert s2_plan.status in ("OPTIMAL", "FEASIBLE")
    for job in s2_plan.jobs:
        if job.machine in REACH and job.space:
            assert job.space in REACH[job.machine], \
                f"{job.machine} → {job.space}"


# ---- 5. an unreachable job is REJECTED at every gate ----------------------------
def test_unreachable_fixed_job_is_a_validation_error(s2):
    sc = copy.deepcopy(s2)
    sc.fixed_jobs = [FixedJob("FX-BAD", "ST01-A", "stack", "HP-08",
                              rate_tph=1000, start_bucket=1, end_bucket=2,
                              grade="TBT")]
    codes = {i.code for i in validate_scenario(sc) if i.severity == "ERROR"}
    assert "UNREACHABLE_FIXED" in codes
    # the same job on a reachable hangar is accepted
    sc.fixed_jobs[0].space = "HP-01"
    codes_ok = {i.code for i in validate_scenario(sc) if i.severity == "ERROR"}
    assert "UNREACHABLE_FIXED" not in codes_ok


def test_unreachable_job_is_an_asp_work_area_error(s2):
    bad = Job("J-BAD", "ST01-A", "stack", "HP-08", grade="TBT",
              rate_tph=1000, tonnage_t=1000, start_bucket=1, end_bucket=1,
              start_x=0.0, stop_x=150.0)
    good = Job("J-OK", "ST01-A", "stack", "HP-01", grade="TBT",
               rate_tph=1000, tonnage_t=1000, start_bucket=1, end_bucket=1,
               start_x=0.0, stop_x=150.0)
    asp = SimulatedAspAdapter(s2, site_id="MPH")
    issues_bad = asp.submit_plan_items([job_to_plan_item(s2, bad)])
    work_area = [i for i in issues_bad if i.rule == "WorkArea"]
    assert work_area, "ASP must raise a WorkArea finding"
    assert all(i.severity == "ERROR" for i in work_area)
    assert "No work area configured for ST01-A on HP-08" in         work_area[0].message
    issues_good = asp.submit_plan_items([job_to_plan_item(s2, good)])
    assert not [i for i in issues_good if i.rule == "WorkArea"]


def test_optimiser_cannot_produce_an_unreachable_job_even_when_forced(s2):
    """Blocking the only stacker of a bay must not leak the job to another."""
    sc = copy.deepcopy(s2)
    sc.machine("ST01-A").available = False        # bay 1 has no other stacker
    res = solve_plan(sc, SolveOptions(max_time_s=2))
    assert res.status in ("OPTIMAL", "FEASIBLE")
    assert all(j.machine != "ST01-A" for j in res.jobs)
    for job in res.jobs:
        if job.machine in REACH and job.space:
            assert job.space in REACH[job.machine]
    assert not any(j.kind == "stack" and j.space in BAYS[1] for j in res.jobs)


# ---- 6/7. selection and job highlighting ---------------------------------------
@pytest.mark.parametrize("mid", list(REACH))
def test_machine_selection_highlights_only_served_piles(mid, s2):
    g = m16_geometry(s2)
    fig = process_mimic_figure(build_process_mimic_state(s2), g,
                               MimicOpts(selected_asset=mid))
    rings = [s for s in fig.layout.shapes
             if s.type == "rect" and s.line.dash == "dot"
             and s.line.color == "#1f6fb3"]
    assert len(rings) == len(REACH[mid]), mid
    ring_x0 = sorted(round(s.x0 + 0.35, 1) for s in rings)
    assert ring_x0 == sorted(round(g.pile(pid).x0, 1) for pid in REACH[mid])


def test_job_highlighting_uses_the_target_pile_and_repere(s2):
    from mph_sim.coordinates import repere_interval_to_x
    sp = s2.space("HP-05")
    xa, xb = repere_interval_to_x(sp, 400, 600)
    job = Job("J-R", "RC01-A", "reclaim", "HP-05", dest_id="PAP-1",
              grade="TBT", rate_tph=1200, tonnage_t=1200,
              start_bucket=0, end_bucket=0, start_x=xa, stop_x=xb,
              path=["L-RCL-HP-05", "L-H-A-PAP"])
    plan = PlanResult("FEASIBLE", jobs=[job])
    state = build_process_mimic_state(s2, plan, basis="plan", bucket=0)
    assert state.machine_pos["RC01-A"].repere == 500
    assert state.machine_pos["RC01-A"].work_repere == (400, 600)
    assert state.machine_pos["RC01-A"].space == "HP-05"
    g = m16_geometry(s2)
    fig = process_mimic_figure(state, g, MimicOpts(selected_job="J-R"))
    canvas = _texts(fig)
    assert "R400–R600" in canvas
    # the machine draws its boom into HP-05 on its own line — not elsewhere
    rail = g.rail("M16A-A-T11")
    pile = g.pile("HP-05")
    gx, gy, tipx, tipy, _ = machine_draw_pos(
        rail, pile, state.machine_pos["RC01-A"].x_norm)
    assert gy == rail.y
    assert pile.x0 <= tipx <= pile.x1 and pile.y0 <= tipy <= pile.y1
    # only the job's own route is highlighted
    from ui.process_mimic import _conv_state
    selected = {c.cid for c in g.conveyors
                if _conv_state(state, c, MimicOpts(selected_job="J-R"))[4]}
    assert selected, "the job's route must highlight"
    for cid in selected:
        cg = next(c for c in g.conveyors if c.cid == cid)
        assert any(li in job.path for li in cg.link_ids), cid
    # ... and no ring is drawn around a pile the machine cannot serve
    rings = [s for s in fig.layout.shapes
             if s.type == "rect" and s.line.dash == "dot"
             and s.line.color == "#1f6fb3"]
    assert len(rings) == 0        # no machine selected, only a job


def test_idle_machine_reports_r500_as_its_reference_position(s2):
    state = build_process_mimic_state(s2, basis="initial", bucket=0)
    for mid in REACH:
        pos = state.machine_pos[mid]
        assert pos.repere == 500, mid
        assert pos.work_repere is None, mid


# ---- 8. two SEPARATE gate banks, PAP/drying confluence TBC -----------------------
def test_two_separate_clamshell_gate_banks_are_displayed(s2):
    g = m16_geometry(s2)
    gates = [e for e in g.equipment if e.kind == "gate"]
    assert len(gates) == 8, "four gates per bank, two banks"
    # each bank routes ONLY its own stream's links — that binding, not the
    # short H0n caption, is what keeps the two banks separate
    per_stream = {"A": [], "B": []}
    for e in gates:
        # Rev V: a gate symbol is bound to its CONFIRMED bank link; the
        # PAP/drying choice is a separate DERIVED route link downstream
        assert len(e.link_ids) == 1, e.label
        bank = e.link_ids[0]
        assert bank in ("L-H-A", "L-H-B"), bank
        per_stream[bank[-1]].append(e.label)
    for stream, labels in per_stream.items():
        assert sorted(labels) == ["H01", "H02", "H03", "H04"], stream
    # each bank is named in full on its own evidence chip
    chips = " ".join(c.tag for c in g.chips)
    assert "M16A-A-H01…H04" in chips and "M16A-B-H01…H04" in chips
    # no collapsed single global bank
    assert chips.count("H01…H04") == 2
    confluence = [e for e in g.equipment
                  if e.kind == "destbox" and "CONFLUENCE" in e.label.upper()]
    assert confluence and "TBC" in confluence[0].label


@pytest.mark.parametrize("scenario", ["s2", "s3"])
def test_reclaim_streams_stay_separate_in_the_model(scenario, s2, s3):
    sc = s2 if scenario == "s2" else s3
    node_ids = {n.id for n in sc.nodes}
    assert {"HUB:M16-RCL-A", "HUB:M16-RCL-B"} <= node_ids
    assert "HUB:M16-RECLAIM" not in node_ids, "the merged hub must be gone"
    for stream, piles in (("A", BAYS[1] + BAYS[2]), ("B", BAYS[3] + BAYS[4])):
        for pid in piles:
            link = sc.link(f"L-RCL-{pid}")
            assert link.dst == f"HUB:M16-RCL-{stream}", pid
        bank = sc.link(f"L-H-{stream}")
        assert bank.src == f"HUB:M16-RCL-{stream}"
        assert bank.dst == f"HUB:M16-T13-{stream}"
        assert bank.gate_id == f"M16A-{stream}-H01…H04"
        pap = sc.link(f"L-RTE-{stream}-PAP")
        dry = sc.link(f"L-RTE-{stream}-DRY")
        assert pap.src == dry.src == f"HUB:M16-T13-{stream}"
        assert pap.capacity_tph == 1500 and dry.capacity_tph == 1299
        # one outbound belt per bank -> the bank feeds PAP or drying (DERIVED)
        assert pap.conflict_group == dry.conflict_group ==             f"M16A-{stream}-ROUTE"
    # no permanent H-number → destination claim anywhere
    blob = " ".join(f"{l.gate_id} {l.evidence.note}" for l in sc.links
                    if l.id.startswith(("L-H-", "L-RTE-")))
    assert "2-of-4" not in blob
    # the destination assignment is explicitly disclaimed on the route links
    route_notes = " ".join(l.evidence.note for l in sc.links
                           if l.id.startswith("L-RTE-")).lower()
    assert "neither" in route_notes and "states which" in route_notes
    assert "not modelled" in blob.lower()


def test_both_destinations_remain_servable_after_the_correction(s2, s2_plan):
    delivered = {}
    for job in s2_plan.jobs:
        if job.kind in ("reclaim", "dispatch"):
            delivered[job.dest_id] = delivered.get(job.dest_id, 0) + job.tonnage_t
    assert delivered.get("PAP-1", 0) > 0 and delivered.get("DRY-FEED", 0) > 0
    from mph_sim.kpis import compute_kpis
    kpis = compute_kpis(s2, s2_plan, None)
    assert kpis["unmet_t"] == pytest.approx(0.0, abs=1e-6)
    assert kpis["service_pct"] == pytest.approx(100.0, abs=1e-6)
    assert kpis["onspec_pct"] == pytest.approx(100.0, abs=1e-6)


# ---- 9. future expansion behind the toggle only ---------------------------------
def test_future_assets_only_appear_with_the_toggle(s2):
    plain = m16_geometry(s2)
    future = m16_geometry(s2, show_future=True)
    assert not [p for p in plain.piles if p.future]
    assert not [m for m in plain.machines if m.machine_id.endswith("*")]
    assert not [r for r in plain.rails
                if r.rail_id in ("M16A-C-T11", "M16A-D-T06")]
    future_piles = [p for p in future.piles if p.future]
    assert len(future_piles) == 4
    assert all(p.capacity_t == 125_000 and not p.modelled for p in future_piles)
    assert {m.machine_id for m in future.machines if m.display} == {
        "RC01-C*", "ST01-D*"}
    assert {r.rail_id for r in future.rails} >= {"M16A-C-T11", "M16A-D-T06"}
    # hidden future consumes no layout space
    assert plain.bounds[3] < future.bounds[3]
    assert validate_geometry(future) == []
    # never solver resources
    assert not [m for m in s2.machines if m.id.endswith("*")]
    assert not [sp for sp in s2.spaces if sp.id.endswith("*")]


def test_future_extension_keeps_the_pfd_sequence(s2):
    g = m16_geometry(s2, show_future=True)
    c_t06 = g.rail("M16A-C-T06").y
    c_t11 = g.rail("M16A-C-T11").y
    d_t06 = g.rail("M16A-D-T06").y
    bay5 = [g.pile(p) for p in ("HP-09*", "HP-11*")]
    bay6 = [g.pile(p) for p in ("HP-10*", "HP-12*")]
    assert all(c_t11 < p.y0 < c_t06 for p in bay5), "bay 5 sits C-T06 → C-T11"
    assert all(d_t06 < p.y0 < c_t11 for p in bay6), "bay 6 sits C-T11 → D-T06"
    for pair in (bay5, bay6):
        left, right = sorted(pair, key=lambda pile: pile.x0)
        assert left.y0 == pytest.approx(right.y0)
        assert left.y1 == pytest.approx(right.y1)
        assert left.x1 < right.x0


# ---- 10. state behaviour + no regressions elsewhere ------------------------------
def test_initial_and_final_state_switching_still_correct(s2, s2_plan):
    T = s2.sim.horizon_buckets
    initial = build_process_mimic_state(s2, s2_plan, basis="initial", bucket=0)
    final = build_process_mimic_state(s2, s2_plan, basis="plan", bucket=T)
    assert initial.bucket == 0 and final.bucket == T
    piles_i = {p.id: p for p in initial.monitor.sites["MPH"].piles}
    piles_f = {p.id: p for p in final.monitor.sites["MPH"].piles}
    for pid, (mass, _grade) in START.items():
        assert piles_i[pid].mass_t == pytest.approx(mass)
        series = s2_plan.inventory[pid]
        assert len(series) == T + 1
        assert piles_f[pid].mass_t == pytest.approx(series[T])
    assert not final.jobs, "no job is active at the terminal boundary"
    assert build_process_mimic_state(
        s2, s2_plan, basis="plan", bucket=T + 4).bucket == T


def test_louta_and_benguerir_geometry_unchanged(s3):
    lou = louta_geometry(s3)
    assert [p.pile_id for p in lou.piles] == [
        "102MG-SP-01", "102MG-SP-02", "110LG-OL1", "110LG-OL2",
        "LT-P1", "LT-P2"]
    assert {r.rail_id for r in lou.rails} == {
        "102MG-ST1-R", "110MG-R", "102MG-ST2-R",
        "120LG-R1", "110LG-R", "120LG-R2",
        "140LG-RL1", "130LG-R", "140LG-RL2"}
    assert {line.line_id for line in lou.process_lines} == {
        "LT-WASH-L1", "LT-WASH-L2"}
    for pid, cap, mass in (("110LG-OL1", 247_137.0, 123_568.5),
                           ("110LG-OL2", 247_137.0, 123_568.5)):
        pile = lou.pile(pid)
        space = s3.space(pid)
        assert pile.modelled and pile.display_mass_t is None
        assert space.capacity_t == pytest.approx(cap)
        assert space.mass_t == pytest.approx(mass)
    for geo in (benguerir_geometry(s3), benguerir_geometry(s3, compact=True)):
        assert [p.pile_id for p in geo.piles] == [
            "BG-MINE-1", "BG-MINE-2", "BG-RAW-1", "BG-RAW-2",
            "BG-P1", "BG-P2"]
        assert {m.machine_id for m in geo.machines} == {
            "BG-MINE-ST-1", "BG-MINE-BWR", "BG-MINE-ST-2",
            "BG-RAW-ST-1", "BG-RAW-BWR", "BG-RAW-ST-2", "ST3", "RC2", "RC3"}
        raw = [p for p in geo.piles if p.pile_id.startswith("BG-RAW")]
        assert all(p.capacity_t == 200_000 for p in raw)
        assert all(p.modelled and p.display_mass_t is None for p in raw)
        assert all(s3.space(p.pile_id).mass_t == pytest.approx(100_000.0)
                   for p in raw)


def test_every_view_still_validates_and_renders(s3):
    state = build_process_mimic_state(s3)
    for view in ("MPH", "LOUTA", "BENGUERIR", "CHAIN", "GLOBAL"):
        geo = geometry_for(s3, view)
        assert validate_geometry(geo) == [], view
        fig = process_mimic_figure(state, geo)
        assert len(fig.layout.shapes) > 10, view
        x0, y0, x1, y1 = geo.bounds
        for pile in geo.piles:
            assert x0 <= pile.x0 <= pile.x1 <= x1, f"{view}/{pile.pile_id}"
            assert y0 <= pile.y0 <= pile.y1 <= y1, f"{view}/{pile.pile_id}"
        for equip in geo.equipment:
            assert x0 - 1e-6 <= equip.x - equip.w / 2, f"{view}/{equip.label}"
            assert equip.x + equip.w / 2 <= x1 + 1e-6, f"{view}/{equip.label}"


def test_drawing_never_mutates_the_scenario_or_plan(s2, s2_plan):
    before = {sp.id: sp.mass_t for sp in s2.spaces}
    jobs_before = [(j.id, j.machine, j.space) for j in s2_plan.jobs]
    for show_future in (False, True):
        state = build_process_mimic_state(s2, s2_plan, basis="plan", bucket=3)
        process_mimic_figure(state, m16_geometry(s2, show_future=show_future),
                             MimicOpts(selected_asset="RC01-A"))
    assert {sp.id: sp.mass_t for sp in s2.spaces} == before
    assert [(j.id, j.machine, j.space) for j in s2_plan.jobs] == jobs_before
