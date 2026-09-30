"""Acceptance tests for the "Global Stockyard Layout & Flow" page rework
(18-Jul-2026): Louta process staging (102MG → 110/120LG ore storage →
beneficiation → 130/140LG product storage → C1), the documented 140LG
reclaim topology with the C1 feeder-selection rule, Benguerir cfg A/cfg B
configuration boundary, and machine-selection reachability highlighting."""
import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mph_sim import datasets                                     # noqa: E402
from mph_sim.domain import Job                                   # noqa: E402
from mph_sim.optimizer import SolveOptions, solve_plan           # noqa: E402
from ui import common                                            # noqa: E402
from ui.layout_geometry import (C1_FEEDER_TXT,                   # noqa: E402
                                geometry_for, global_geometry,
                                louta_geometry, validate_geometry)
from ui.process_mimic import (MimicOpts, _conv_state,            # noqa: E402
                              build_process_mimic_state,
                              process_mimic_figure)
from ui.stockyard_monitor import EXPECTED_COVERAGE               # noqa: E402


@pytest.fixture(scope="module")
def s3():
    return datasets.end_to_end()


@pytest.fixture(scope="module")
def s3_plan(s3):
    return solve_plan(s3, SolveOptions(max_time_s=3))


def _texts(fig):
    return " ".join(str(a.text) for a in fig.layout.annotations if a.text)


def _with_louta_dispatch(plan, machine="140LG-RC-01"):
    """Explicit display-state fixture; baseline demand need not use C1."""
    planned = copy.deepcopy(plan)
    planned.jobs = [j for j in planned.jobs
                    if not (j.kind == "dispatch"
                            and j.machine.startswith("140LG"))]
    job = Job("TEST-LT-C1", machine, "dispatch", "LT-P1", "C1",
              "TBT", 750.0, 750.0, 0, 0,
              path=["L-LT-P1-OUT", "C1"])
    planned.jobs.append(job)
    return planned, job


# ---- 1. page + all three site zones in global mode --------------------------------
def test_page_named_global_stockyard_layout_and_flow():
    lbl = "15 · Global Stockyard Layout & Flow"
    assert lbl in common.NAV_GROUPS["Monitor"]
    assert lbl in common.ALL_PAGE_LABELS


def test_global_mode_renders_three_site_zones(s3):
    g = global_geometry(s3)
    zl = " ".join(z.label for z in g.zones)
    assert "LOUTA" in zl and "BENGUERIR" in zl and "M16" in zl
    # each site zone contains a recognisable yard: lanes + rails + machines
    for prefix in ("LT-P", "BG-P", "HP-"):
        assert any(p.pile_id.startswith(prefix) for p in g.piles), prefix
    # one product reclaim line per product planning line (25-Jul-2026)
    assert {"140LG-RL1", "140LG-RL2"} <= {r.rail_id for r in g.rails}
    fig = process_mimic_figure(build_process_mimic_state(s3), g)
    t = _texts(fig)
    assert "GLOBAL STOCKYARD LAYOUT & FLOW — SCHEMATIC, NOT TO SCALE" not in t
    # Pile names are plain text above each outline; descriptive stage names
    # and evidence remain in zone headings and hover details.
    assert "MINE STOCKPILE-01" in t and "BG-P1" in t and "HP-01" in t
    for pid, canvas_name in (
            ("102MG-SP-01", "MINE STOCKPILE-01"),
            ("BG-P1", "BG-P1"),
            ("HP-01", "HP-01")):
        name = next(a for a in fig.layout.annotations
                    if a.text == canvas_name)
        assert name.y > g.pile(pid).y1 and name.bgcolor is None


# ---- 2. Louta separated into the four process stages -------------------------------
def test_louta_four_stage_separation(s3):
    g = louta_geometry(s3)
    zl = " ".join(z.label for z in g.zones)
    assert "MINE SMS" in zl                              # L01 mine storage
    assert "102MG" in zl
    assert "HOMOGENIZED ORE STORAGE" in zl               # 110/120LG stage
    assert "494,274" in zl and "247,137" in zl
    assert "WASHING / BENEFICIATION — 2 PARALLEL LINES" in zl
    assert "PRODUCT STORAGE" in zl and "383,024" in zl   # L02 product storage
    # the removed empty placeholder block never comes back
    assert "MINE / screening" not in zl
    assert not any(z.label.startswith("MINE (") for z in g.zones)
    # 102MG mine storage stays reference-only; 110LG ore inventory is live
    for pid in ("102MG-SP-01", "102MG-SP-02"):
        p = g.pile(pid)
        assert p is not None and not p.modelled and p.tbc, pid
    for pid in ("110LG-OL1", "110LG-OL2"):
        p = g.pile(pid)
        assert p is not None and p.modelled and p.tbc, pid
    # The two washing cards identify solver-backed process stages; the fixed
    # plant lines are not invented as mobile machines.
    lines = {line.line_id: line for line in g.process_lines}
    assert set(lines) == {"LT-WASH-L1", "LT-WASH-L2"}
    assert {line.status for line in lines.values()} == {"OPERATING"}
    assert [line.label for line in g.process_lines] == ["LINE L1", "LINE L2"]
    # the vague derived per-line rate is off the canvas
    assert "584" not in zl
    assert not any("584" in e.label for e in g.equipment)


def test_louta_ore_stage_machinery_matches_process_description(s3):
    g = louta_geometry(s3)
    solver = {m.machine_id: m for m in g.machines if not m.display}
    # exactly ONE ore stacker, on the rail that flanks BOTH ore piles
    assert [m for m in g.machines if m.machine_id.startswith("110LG-ST")] \
        and "110LG-ST-01" in solver
    stacker = solver["110LG-ST-01"]
    assert stacker.kind == "stacker"
    assert set(g.rail(stacker.rail_id).flanks) == {"110LG-OL1", "110LG-OL2"}
    assert "2,100 t/h wet" in stacker.note and "110LG-BC-01" in stacker.note
    # exactly TWO ore reclaimers, one per pile, at their R500 home position
    for mid, pid in (("120LG-RC-01", "110LG-OL1"),
                     ("120LG-RC-02", "110LG-OL2")):
        m = solver[mid]
        assert m.kind == "reclaimer"
        assert m.served_pile == pid and m.home_repere == 500
        assert g.rail(m.rail_id).flanks == [pid]
        assert "3,000 t/h wet" in m.note
    # the source discrepancy is evidence-layer only — never a canvas warning
    assert not any("count conflict" in s.text.lower() for s in g.stamps)
    fig = process_mimic_figure(build_process_mimic_state(s3), g)
    canvas = _texts(fig)
    assert "RC count" not in canvas and "Reclaimer count conflict" not in canvas
    gaps = " ".join(gp[2] for gp in g.gaps)
    assert "generic BoD Rev B table lists ONE ore reclaimer" in gaps
    assert "does NOT state their pairing" in gaps


# ---- 3/4. no 102MG→C1; C1 begins at the product-reclaim network --------------------
def test_no_102mg_or_ore_stage_route_to_c1(s3):
    for view in ("LOUTA", "CHAIN", "GLOBAL"):
        g = geometry_for(s3, view)
        for c in g.conveyors:
            if "102MG" in c.cid:
                assert not c.planning_enabled, f"{view}/{c.cid}"
                assert "C1" not in c.link_ids, f"{view}/{c.cid}"
            if c.cid in {"RB1/RB2", "RB3", "RB4/RB5", "RB6", "RB7"}:
                assert "C1" not in c.link_ids, f"{view}/{c.cid}"


def test_c1_begins_at_louta_product_reclaim_network(s3):
    g = louta_geometry(s3)
    # documented topology: RL1/RL2 are machine-bound routes; C1 take-off is
    # bound to the C1 link and sits inside the product-reclaim stage band
    rl1 = next(c for c in g.conveyors if c.cid == "RL1")
    rl2 = next(c for c in g.conveyors if c.cid == "RL2")
    assert rl1.machine_id == "140LG-RC-01" and "CV-03" in rl1.tag
    assert rl2.machine_id == "140LG-RC-02" and "CV-04" in rl2.tag
    c1 = next(c for c in g.conveyors if "C1" in c.link_ids)
    prod_top = max(g.pile("LT-P1").y1, g.pile("LT-P2").y1) + 6
    assert all(py < prod_top for _, py in c1.pts), \
        "C1 must start at the product-reclaim network, not upstream"
    # two product flip gates (140LG-FG-01/-02) select RL3/RL4; the ore stage
    # has its own documented flip gates and must not be counted here
    assert len([e for e in g.equipment
                if e.kind == "diverter" and "140LG-FG" in e.label]) == 2
    assert any(e.kind == "diverter" and "120LG-FG" in e.label
               for e in g.equipment)


# ---- 5/6. both feeders visible; lane-balanced S0; C1 <= 2,600 t/h ------------------
def test_both_140lg_reclaimers_follow_lane_balanced_s0_policy(s3, s3_plan):
    for view in ("LOUTA", "CHAIN", "GLOBAL"):
        g = geometry_for(s3, view)
        solver = {m.machine_id for m in g.machines if not m.display}
        assert {"140LG-RC-01", "140LG-RC-02"} <= solver, view

    # The transparent S0 campaign is lane-balanced: each washing line makes
    # 4,000 t, each product line returns to its exact starting inventory, and
    # each product reclaimer can reach only its corresponding product pile.
    assert s3.config["LOU_WASH_LINE_OUTPUT_TARGET_T"] == {
        "LOU-WASH-L1": 4000.0,
        "LOU-WASH-L2": 4000.0,
    }
    assert s3.machine("140LG-RC-01").reachable_spaces == ["LT-P1"]
    assert s3.machine("140LG-RC-02").reachable_spaces == ["LT-P2"]
    dispatched = {
        mid: sum(j.tonnage_t for j in s3_plan.jobs
                 if j.kind == "dispatch" and j.dest_id == "C1"
                 and j.machine == mid)
        for mid in ("140LG-RC-01", "140LG-RC-02")
    }
    assert dispatched == {"140LG-RC-01": 4000.0, "140LG-RC-02": 4000.0}

    # Conservative route-confluence policy: never run both C1 feeders in the
    # same hourly material bucket.
    for t in range(s3.sim.horizon_buckets):
        active = {j.machine for j in s3_plan.jobs
                  if j.kind == "dispatch" and j.dest_id == "C1"
                  and j.start_bucket <= t <= j.end_bucket}
        assert len(active) <= 1, f"bucket {t}: both C1 feeders active"

    # Under this explicitly configured lane balance, taking either dedicated
    # reclaimer out cannot meet both exact line targets.  Cross-pile reach is
    # not inferred from the available design evidence.
    for down in ("140LG-RC-01", "140LG-RC-02"):
        sc2 = copy.deepcopy(s3)
        sc2.machine(down).available = False
        res = solve_plan(sc2, SolveOptions(max_time_s=2))
        assert res.status == "INFEASIBLE", down


def test_c1_flow_never_exceeds_2600(s3, s3_plan):
    for t in range(s3.sim.horizon_buckets):
        tot = sum(j.rate_tph for j in s3_plan.jobs
                  if j.kind == "dispatch" and j.dest_id == "C1"
                  and j.start_bucket <= t <= j.end_bucket)
        assert tot <= 2600 + 1e-6, f"bucket {t}: C1 at {tot} t/h"


def test_feeder_policy_stamped_on_louta_view(s3):
    g = louta_geometry(s3)
    assert any(C1_FEEDER_TXT in s.text for s in g.stamps)
    fig = process_mimic_figure(build_process_mimic_state(s3), g)
    assert "C1 FEEDER SELECTION" in _texts(fig)


# ---- 7/8. every planning entity exactly once, on a configured rail ------------------
def test_every_planning_entity_exactly_once_all_views(s3):
    expected_sp = {
        space_id
        for coverage in EXPECTED_COVERAGE.values()
        for space_id in coverage["spaces"]
        if s3.space(space_id).area not in {
            "mine-interface", "process-interface"}
    }
    expected_m = set().union(*(c["machines"]
                               for c in EXPECTED_COVERAGE.values()))
    for view in ("CHAIN", "GLOBAL"):
        g = geometry_for(s3, view)
        pids = [p.pile_id for p in g.piles]
        mids = [m.machine_id for m in g.machines]
        assert len(pids) == len(set(pids)), view
        assert len(mids) == len(set(mids)), view
        assert {p.pile_id for p in g.piles if p.modelled} == expected_sp
        assert {m.machine_id for m in g.machines
                if not m.display} == expected_m


def test_every_machine_on_a_configured_rail(s3):
    for view in ("CHAIN", "GLOBAL", "LOUTA", "BENGUERIR", "MPH"):
        g = geometry_for(s3, view)
        assert validate_geometry(g) == [], view
        for m in g.machines:
            if not m.display:
                assert m.rail_id and g.rail(m.rail_id) is not None, \
                    f"{view}/{m.machine_id} not on a configured rail"


# ---- 9. reachability highlighting matches the Scenario configuration ----------------
def test_reachability_highlight_matches_scenario_config(s3):
    st = build_process_mimic_state(s3)
    g = geometry_for(s3, "MPH")
    m = s3.machine("RC01-A")
    fig = process_mimic_figure(st, g, MimicOpts(selected_asset="RC01-A"))
    # every reachable pile gets a blue dotted focus ring at lane bounds ±0.35
    rings = [s for s in fig.layout.shapes
             if s.type == "rect" and s.line.dash == "dot"
             and s.line.color == "#1f6fb3"]
    reach_lanes = [g.pile(pid) for pid in m.reachable_spaces
                   if g.pile(pid) is not None]
    # bay-adjacency reach: RC01-A serves bays 1-2 = four hangars (25-Jul-2026)
    assert len(rings) == len(reach_lanes) == 4
    ring_x0 = sorted(round(s.x0 + 0.35, 1) for s in rings)
    assert ring_x0 == sorted(round(p.x0, 1) for p in reach_lanes)
    # Louta: conservative one-reclaimer-per-product-line reach means selecting
    # 140LG-RC-01 highlights LT-P1 only.
    gl = geometry_for(s3, "LOUTA")
    figl = process_mimic_figure(build_process_mimic_state(s3), gl,
                                MimicOpts(selected_asset="140LG-RC-01"))
    ringsl = [s for s in figl.layout.shapes
              if s.type == "rect" and s.line.dash == "dot"
              and s.line.color == "#1f6fb3"]
    assert len(ringsl) == 1       # exactly LT-P1 (configured reach)


# ---- 10. active machines visually connect to the active pile ------------------------
def test_active_machine_boom_reaches_active_pile(s3, s3_plan):
    plan, j = _with_louta_dispatch(s3_plan)
    st = build_process_mimic_state(s3, plan, basis="plan",
                                   bucket=j.start_bucket)
    from ui.process_mimic import machine_draw_pos
    g = louta_geometry(s3)
    rail = g.rail("140LG-RL1")            # RC-01's own product-line-1 rail
    pg = g.pile(j.space)
    pv = st.machine_pos[j.machine]
    gx, gy, tipx, tipy, side = machine_draw_pos(rail, pg, pv.x_norm)
    assert gy == rail.y
    assert pg.x0 <= tipx <= pg.x1 and pg.y0 <= tipy <= pg.y1, \
        "boom tip must land in the active pile lane"


# ---- 11. Benguerir staged topology (B02 correction pass, 19-Jul-2026) --------------
def test_benguerir_staged_topology_b02(s3):
    g = geometry_for(s3, "BENGUERIR")
    zl = " ".join(z.label for z in g.zones)
    # zone renamed 20-Jul-2026 to reflect the USER_CONFIRMED Table 20
    # screened-product ore-storage topology (upstream of beneficiation)
    assert "SCREENED-PRODUCT ORE STORAGE" in zl
    assert "PRE-BENEFICIATION" in zl
    assert "400,000 t" in zl
    assert "WASHING / FILTRATION / SETTLING" in zl
    assert "WASHED-PRODUCT STORAGE" in zl
    # B01 evidence retained as the connected upstream stage — stages
    # connected, tag registers not merged (reconciliation TBC)
    chips = " ".join(ch.tag + " " + ch.note for ch in g.chips)
    assert "B01 2023" in chips and "stages connected" in chips
    # C2 leaves the washed-product reclaim network, below the raw band
    c2 = next(c for c in g.conveyors if "C2" in c.link_ids)
    raw_y = min(p.y0 for p in g.piles if p.pile_id.startswith("BG-RAW"))
    assert all(py < raw_y for _, py in c2.pts), \
        "C2 must not originate from the raw reference band"
    # B01 mass-rate conflicts preserved, not normalised
    gaps = " ".join(gp[2] for gp in g.gaps)
    assert "1,208" in gaps and "630" in gaps


# ---- 12. per-machine route state (RL1/RL2) -----------------------------------------
def test_rl_routes_follow_their_machine_state(s3, s3_plan):
    g = louta_geometry(s3)
    rl1 = next(c for c in g.conveyors if c.cid == "RL1")
    rl2 = next(c for c in g.conveyors if c.cid == "RL2")
    plan, j = _with_louta_dispatch(s3_plan, "140LG-RC-01")
    st = build_process_mimic_state(s3, plan, basis="plan",
                                   bucket=j.start_bucket)
    s1, tph1, cap1, _, _ = _conv_state(st, rl1, MimicOpts())
    s2, tph2, _, _, _ = _conv_state(st, rl2, MimicOpts())
    assert s1 == "running" and tph1 == pytest.approx(j.rate_tph)
    assert cap1 == 3000
    assert s2 == "idle" and tph2 == 0     # exclusivity: RC-02 not running


# ---- 13. drawing never mutates the model -------------------------------------------
def test_layout_page_does_not_mutate_inputs(s3, s3_plan):
    before = {sp.id: sp.mass_t for sp in s3.spaces}
    jobs_before = [(j.id, j.machine, j.rate_tph) for j in s3_plan.jobs]
    for view in ("GLOBAL", "LOUTA", "BENGUERIR", "MPH", "CHAIN"):
        st = build_process_mimic_state(s3, s3_plan, basis="plan", bucket=3)
        process_mimic_figure(st, geometry_for(s3, view),
                             MimicOpts(selected_asset="140LG-RC-01",
                                       active_flows_only=True))
    assert {sp.id: sp.mass_t for sp in s3.spaces} == before
    assert [(j.id, j.machine, j.rate_tph) for j in s3_plan.jobs] == jobs_before


# ---- 14. everything stays inside the canvas bounds ---------------------------------
def test_geometry_elements_within_bounds(s3):
    for view in ("GLOBAL", "CHAIN", "LOUTA", "BENGUERIR", "MPH"):
        g = geometry_for(s3, view)
        x0, y0, x1, y1 = g.bounds
        for p in g.piles:
            assert x0 <= p.x0 <= p.x1 <= x1 and y0 <= p.y0 <= p.y1 <= y1, \
                f"{view}/{p.pile_id}"
        for r in g.rails:
            assert x0 <= r.x0 <= r.x1 <= x1 and y0 <= r.y <= y1, \
                f"{view}/{r.rail_id}"
        for c in g.conveyors:
            for (px, py) in c.pts:
                assert x0 - 1e-6 <= px <= x1 + 1e-6, f"{view}/{c.cid}"
                assert y0 - 1e-6 <= py <= y1 + 1e-6, f"{view}/{c.cid}"
        for z in g.zones:
            assert x0 <= z.x0 <= z.x1 <= x1 and y0 <= z.y0 <= z.y1 <= y1, \
                f"{view}/zone {z.label[:30]}"


# ---- 15. page renders headlessly with the demo bundles ------------------------------
def test_site_detail_selector_and_locator_render(s3, s3_plan):
    # figures for every site-detail selection render with the plan state
    for view in ("GLOBAL", "LOUTA", "BENGUERIR", "MPH", "CHAIN"):
        st = build_process_mimic_state(s3, s3_plan, basis="plan", bucket=0)
        fig = process_mimic_figure(st, geometry_for(s3, view))
        assert len(fig.layout.shapes) > 10, view
