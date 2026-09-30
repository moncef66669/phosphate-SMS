# -*- coding: utf-8 -*-
"""Acceptance suite for the Louta process-mimic + stockyard correction
(25-Jul-2026), against the equipment-tagged Louta process description
(Rev B, 25-Apr-2023, §3.1 pp. 5–8; cover REP-00002 vs body header REP-00004):

  * 102MG mine storage stays a separate display/reference area and never
    owns the 494,274 t homogenized ore capacity;
  * the empty `MINE / screening · TBC` placeholder block is gone;
  * washing is TWO parallel OPERATING lines L1/L2, positioned between ore
    reclaiming and product storage;
  * exactly TWO solver-modelled homogenized ore stockpiles, 494,274 t total
    (DERIVED equal split), 50% start (247,137 t total), multi-band display
    composition over live/model inventory;
  * ore machinery = ONE shared stacker 110LG-ST-01 on the rail between the
    piles + TWO bridge reclaimers, one drawn across each pile at R500;
  * product machinery = ONE shared stacker 130LG-ST-01 + TWO reclaimers,
    each on its own product line, both feeding the common C1 corridor;
  * the BoD-vs-process-description reclaimer discrepancy lives in the
    evidence/assumption layer, never as a canvas warning;
  * Initial/Final state switching still resolves to inventory[T];
  * Benguerir geometry is untouched by this pass.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mph_sim import datasets                                       # noqa: E402
from mph_sim.optimizer import SolveOptions, solve_plan             # noqa: E402
from ui.layout_geometry import (benguerir_geometry, geometry_for,   # noqa: E402
                                louta_geometry, validate_geometry)
from ui.process_mimic import (MimicOpts,                           # noqa: E402
                              build_process_mimic_state,
                              geometry_model_mismatch,
                              machine_draw_pos, process_mimic_figure)

ORE_PILES = ("110LG-OL1", "110LG-OL2")
PRODUCT_PILES = ("LT-P1", "LT-P2")
ORE_TOTAL_T = 494_274.0
PRODUCT_TOTAL_T = 383_024.0
LOUTA_VIEWS = ("LOUTA", "CHAIN", "GLOBAL")


@pytest.fixture(scope="module")
def s3():
    return datasets.end_to_end()


@pytest.fixture(scope="module")
def s3_plan(s3):
    return solve_plan(s3, SolveOptions(max_time_s=3))


def _texts(fig):
    return " ".join(str(a.text) for a in fig.layout.annotations if a.text)


def _figure(s3, view, **opts):
    return process_mimic_figure(
        build_process_mimic_state(s3, basis="initial", bucket=0),
        geometry_for(s3, view), MimicOpts(**opts))


def _machine(geo, mid):
    return next(m for m in geo.machines if m.machine_id == mid)


# ---- 1. the empty MINE / screening placeholder is gone everywhere ----------------
def test_empty_mine_screening_block_removed(s3):
    for view in LOUTA_VIEWS:
        geo = geometry_for(s3, view)
        labels = [z.label for z in geo.zones]
        assert "MINE / screening" not in " ".join(labels), view
        assert not any(label.strip().upper().startswith("MINE (")
                       or label.strip().upper().startswith("SCREENING")
                       for label in labels), view
        canvas = _texts(_figure(s3, view))
        assert "MINE / screening" not in canvas, view
        # the drawing-supported 102MG mine-storage area is KEPT, with its two
        # stockpile lines
        assert any("102MG MINE STORAGE" in label or "102MG" in label
                   for label in labels), view
        for pid in ("102MG-SP-01", "102MG-SP-02"):
            assert geo.pile(pid) is not None, f"{view}/{pid}"


def test_102mg_assumed_display_baseline_is_reference_only(s3):
    expected_grades = {
        "102MG-SP-01": [
            "RAW-BT-GLOBAL", "RAW-BT-HIGH-CD"],
        "102MG-SP-02": [
            "RAW-BT-GLOBAL", "RAW-BT-LOW-CD"],
    }
    for view in LOUTA_VIEWS:
        geo = geometry_for(s3, view)
        for pid, grade_ids in expected_grades.items():
            pile = geo.pile(pid)
            assert not pile.modelled and pile.tbc
            assert pile.capacity_t == pytest.approx(100_000.0)
            assert pile.display_mass_t == pytest.approx(50_000.0)
            assert [component.grade_id
                    for component in pile.display_grade_components] == grade_ids
            assert [component.mass_fraction
                    for component in pile.display_grade_components] == \
                pytest.approx([0.5, 0.5])
            assert "ASSUMED" in pile.display_note
            assert "494" not in (pile.label or "")

    cfg = s3.config
    assert set(cfg["LOU_MINE_102MG_DISPLAY_CAPACITY_T"]) == \
        set(expected_grades)
    assert all(value == pytest.approx(100_000.0)
               for value in
               cfg["LOU_MINE_102MG_DISPLAY_CAPACITY_T"].values())
    assert all(value == pytest.approx(50_000.0)
               for value in
               cfg["LOU_MINE_102MG_DISPLAY_START_MASS_T"].values())
    assert "ASSUMED" in cfg["LOU_MINE_102MG_DISPLAY_CAPACITY_BASIS"]
    assert "494,274 t" in cfg["LOU_MINE_102MG_DISPLAY_CAPACITY_BASIS"]
    a25 = next(a for a in s3.assumptions if a.id == "S3-A25")
    assert a25.status == "ASSUMED"
    assert "no solver, KPI or plan effect" in a25.impact

    assert {"102MG-SP-01", "102MG-SP-02"}.isdisjoint(
        {space.id for space in s3.spaces})
    geo = louta_geometry(s3)
    for mid in ("102MG-ST-01", "102MG-ST-02"):
        assert _machine(geo, mid).display
        assert mid not in {m.id for m in s3.machines}
    assert "494,274 t homogenized ore-storage capacity is NOT theirs" in \
        " | ".join(reason for _, _, reason in geo.gaps)
    # no 102MG route may ever reach C1
    for view in LOUTA_VIEWS:
        for conveyor in geometry_for(s3, view).conveyors:
            if "102MG" in conveyor.cid:
                assert "C1" not in conveyor.link_ids, f"{view}/{conveyor.cid}"
                assert not conveyor.planning_enabled, f"{view}/{conveyor.cid}"


def test_102mg_uses_documented_two_stackers_and_one_shared_reclaimer(s3):
    expected = {
        "102MG-ST-01": ("stacker", ["102MG-SP-01"]),
        "110MG-RC-01": (
            "reclaimer", ["102MG-SP-01", "102MG-SP-02"]),
        "102MG-ST-02": ("stacker", ["102MG-SP-02"]),
    }
    for view in LOUTA_VIEWS:
        geo = geometry_for(s3, view)
        mine_machines = {
            machine.machine_id: machine
            for machine in geo.machines
            if machine.machine_id in expected
        }
        assert set(mine_machines) == set(expected), view
        for machine_id, (kind, served_piles) in expected.items():
            machine = mine_machines[machine_id]
            rail = geo.rail(machine.rail_id)
            assert machine.display and machine.kind == kind, (
                view, machine_id)
            assert rail.kind == kind
            assert rail.flanks == served_piles
            assert machine.served() == served_piles
            assert "Q80130-LOU-L01-PR-" in rail.source

        # Dedicated stacker booms visibly land inside their own pile lanes.
        for machine_id, pile_id in (
                ("102MG-ST-01", "102MG-SP-01"),
                ("102MG-ST-02", "102MG-SP-02")):
            machine = mine_machines[machine_id]
            rail = geo.rail(machine.rail_id)
            pile = geo.pile(pile_id)
            _, _, tip_x, tip_y, _ = machine_draw_pos(
                rail, pile, machine.home_repere / 1000.0,
                machine.parking_frac, machine.scale)
            assert pile.x0 <= tip_x <= pile.x1
            assert pile.y0 <= tip_y <= pile.y1

        reclaim = next(
            conveyor for conveyor in geo.conveyors
            if conveyor.cid == "LT-102MG-BEN")
        assert "110MG-CB-01" in reclaim.tag
        assert not reclaim.planning_enabled
        assert reclaim.link_ids == []

    scenario_machines = {machine.id for machine in s3.machines}
    assert set(expected).isdisjoint(scenario_machines)


def test_102mg_labels_and_profile_hovers_are_concise(s3):
    fig = _figure(s3, "LOUTA")
    canvas = _texts(fig)
    assert "MINE STOCKPILE-01" in canvas
    assert "MINE STOCKPILE-02" in canvas
    # 102MG-SP-01/-02 are documented sampler tags, not physical pile tags.
    assert not any(
        annotation.text in {"102MG-SP-01", "102MG-SP-02"}
        for annotation in fig.layout.annotations)
    assert canvas.count("50 kt · 50%") == 2
    assert "<b>102MG-SP-01</b>" not in canvas
    geo = louta_geometry(s3)
    for pid, canvas_name in (
            ("102MG-SP-01", "MINE STOCKPILE-01"),
            ("102MG-SP-02", "MINE STOCKPILE-02")):
        name = next(a for a in fig.layout.annotations
                    if a.text == canvas_name)
        assert name.y > geo.pile(pid).y1
        assert name.bgcolor is None
    assert canvas.count("<b>G</b> · 25 kt") == 2
    assert "<b>H-Cd</b> · 25 kt" in canvas
    assert "<b>L-Cd</b> · 25 kt" in canvas
    hover = [
        str(text)
        for trace in fig.data
        for text in (trace.hovertext
                     if isinstance(trace.hovertext, (list, tuple)) else [])
        if "25,000 t" in str(text)
    ]
    assert len(hover) == 4
    assert sum("R0–R250" in text for text in hover) == 2
    assert sum("R250–R500" in text for text in hover) == 2


# ---- 2. washing = exactly two parallel OPERATING lines --------------------------
def test_two_louta_washing_rectangles_both_operating(s3):
    for view in LOUTA_VIEWS:
        geo = geometry_for(s3, view)
        cards = [line for line in geo.process_lines
                 if line.line_id.startswith("LT-WASH")]
        assert len(cards) == 2, view
        assert {card.line_id for card in cards} == {"LT-WASH-L1", "LT-WASH-L2"}
        assert [card.label for card in cards] == ["LINE L1", "LINE L2"]
        assert {card.status for card in cards} == {"OPERATING"}, view
        assert all("3 Mt tsm/year per line" in card.note
                   or "3 Mt tsm/year" in card.note for card in cards)
        # A plant line is not invented as a mobile Machine. Its process stage
        # is represented by explicit transfers and line-specific WIP spaces.
        assert not {card.line_id for card in cards} & {
            machine.id for machine in s3.machines}
        assert {"LT-WASH-L1-WIP", "LT-WASH-L2-WIP"} <= {
            space.id for space in s3.spaces}
        assert {"LOU-WASH-L1", "LOU-WASH-L2"} <= {
            transfer.process_stage for transfer in s3.process_transfers}
        heading = [z.label for z in geo.zones
                   if "WASHING / BENEFICIATION" in z.label]
        assert heading == ["WASHING / BENEFICIATION — 2 PARALLEL LINES"], view


def test_washing_sits_between_ore_reclaim_and_product_storage(s3):
    geo = louta_geometry(s3)
    ore_bottom = min(geo.pile(pid).y0 for pid in ORE_PILES)
    product_top = max(geo.pile(pid).y1 for pid in PRODUCT_PILES)
    cards = [line for line in geo.process_lines
             if line.line_id.startswith("LT-WASH")]
    for card in cards:
        assert product_top < card.y0, card.line_id
        assert card.y1 < ore_bottom, card.line_id
    # side by side, not stacked: the two lines are parallel
    assert cards[0].x1 <= cards[1].x0 or cards[1].x1 <= cards[0].x0


# ---- 3. exactly two homogenized ore piles with the right numbers ----------------
def test_exactly_two_homogenized_ore_piles(s3):
    for view in LOUTA_VIEWS:
        geo = geometry_for(s3, view)
        ore = [p for p in geo.piles if p.pile_id.startswith("110LG-OL")]
        assert [p.pile_id for p in ore] == list(ORE_PILES), view
        for pile in ore:
            assert pile.modelled and pile.tbc
            assert pile.display_mass_t is None
            # horizontal lanes
            assert pile.x1 - pile.x0 > pile.y1 - pile.y0, pile.pile_id
        assert set(ORE_PILES) <= {s.id for s in s3.spaces}


def test_ore_capacity_and_initial_masses(s3):
    ore = [s3.space(pid) for pid in ORE_PILES]
    assert sum(p.capacity_t for p in ore) == pytest.approx(ORE_TOTAL_T)
    assert all(p.capacity_t == pytest.approx(247_137.0) for p in ore)
    assert sum(p.mass_t for p in ore) == pytest.approx(247_137.0)
    assert all(p.mass_t == pytest.approx(123_568.5) for p in ore)
    for view in LOUTA_VIEWS:
        geo = geometry_for(s3, view)
        assert all(geo.pile(pid).modelled for pid in ORE_PILES)
    # the equal split and the 50% start are declared DERIVED / ASSUMED once
    cfg = s3.config
    assert cfg["LOU_ORE_TOTAL_CAPACITY_T"] == pytest.approx(ORE_TOTAL_T)
    assert "DERIVED" in cfg["LOU_ORE_DISPLAY_CAPACITY_BASIS"]
    assert "ASSUMED" in cfg["LOU_ORE_DISPLAY_START_MASS_BASIS"]
    assert "123,568.5 t" in cfg["LOU_ORE_DISPLAY_START_MASS_BASIS"]


def test_ore_labels_stay_concise_and_show_mass_fill_and_bands(s3):
    canvas = _texts(_figure(s3, "LOUTA"))
    assert "110LG-OL1" in canvas and "110LG-OL2" in canvas
    assert canvas.count("123.6 kt · 50%") == 2
    assert "<b>110LG-OL1</b>" not in canvas
    assert canvas.count("<b>G</b> · 61.8 kt") == 2       # one band per pile
    assert "<b>H-Cd</b> · 61.8 kt" in canvas
    assert "<b>L-Cd</b> · 61.8 kt" in canvas
    # long evidence text belongs in hover/evidence, never on the lane label
    assert "DERIVED/TBC" not in canvas


def test_ore_bands_use_only_canonical_louta_profiles_with_hover_evidence(s3):
    state = build_process_mimic_state(s3, basis="initial", bucket=0)
    piles = {pile.id: pile for pile in state.monitor.sites["LOUTA"].piles}
    expected = {
        "110LG-OL1": ["RAW-BT-GLOBAL", "RAW-BT-HIGH-CD"],
        "110LG-OL2": ["RAW-BT-GLOBAL", "RAW-BT-LOW-CD"],
    }
    for pid, grade_ids in expected.items():
        pile = piles[pid]
        assert [segment.grade for segment in pile.segments] == grade_ids
        assert [(segment.frac_from, segment.frac_to)
                for segment in pile.segments] == [(0.0, 0.5), (0.5, 1.0)]
        for segment in pile.segments:
            declared = s3.grade(segment.grade).quality
            assert segment.quality == declared        # no invented chemistry
    fig = _figure(s3, "LOUTA")
    hover = [str(text) for trace in fig.data
             for text in (trace.hovertext
                          if isinstance(trace.hovertext, (list, tuple))
                          else [])]
    assert any("110LG-OL1 · RAW-BT-GLOBAL" in text
               and "61,784 t" in text for text in hover)
    assert any("110LG-OL2 · RAW-BT-LOW-CD" in text
               and "61,784 t" in text for text in hover)


def test_assumed_ore_distribution_is_tagged_in_the_assumptions_layer(s3):
    a23 = next(a for a in s3.assumptions if a.id == "S3-A23")
    assert a23.module == "LOUTA" and a23.status == "ASSUMED"
    for fragment in ("494,274", "247,137", "123,568.5", "DERIVED",
                     "50% Global + 50% High-Cd", "50% Global + 50% Low-Cd",
                     "NEVER attributed to the 102MG"):
        assert fragment in a23.text, fragment
    basis = s3.config["LOU_ORE_DISPLAY_GRADE_COMPOSITION_BASIS"]
    assert "ASSUMED" in basis and "NOT surveyed spatial order" in basis


# ---- 4. ore machinery: 1 shared stacker + 2 bridge reclaimers -------------------
def test_ore_machinery_counts_and_placement(s3):
    for view in LOUTA_VIEWS:
        geo = geometry_for(s3, view)
        ids = [m.machine_id for m in geo.machines]
        assert ids.count("110LG-ST-01") == 1, view
        assert sorted(i for i in ids if i.startswith("120LG-RC-")) == \
            ["120LG-RC-01", "120LG-RC-02"], view
        assert ids.count("130LG-ST-01") == 1, view
        assert sorted(i for i in ids if i.startswith("140LG-RC-")) == \
            ["140LG-RC-01", "140LG-RC-02"], view
        # ONE shared ore stacker on the common rail between both piles
        stacker = _machine(geo, "110LG-ST-01")
        rail = geo.rail(stacker.rail_id)
        assert set(rail.flanks) == set(ORE_PILES), view
        p1, p2 = (geo.pile(pid) for pid in ORE_PILES)
        assert min(p1.y1, p2.y1) < rail.y < max(p1.y0, p2.y0), view
        assert not stacker.twin_boom          # one boom, chevron, either pile


@pytest.mark.parametrize("mid,pid", [("120LG-RC-01", "110LG-OL1"),
                                     ("120LG-RC-02", "110LG-OL2"),
                                     ("140LG-RC-01", "LT-P1"),
                                     ("140LG-RC-02", "LT-P2")])
def test_each_reclaimer_is_geometrically_associated_with_its_pile(s3, mid, pid):
    for view in LOUTA_VIEWS:
        geo = geometry_for(s3, view)
        machine = _machine(geo, mid)
        rail = geo.rail(machine.rail_id)
        pile = geo.pile(pid)
        assert rail.flanks == [pid], f"{view}/{mid}"
        assert machine.served_pile == pid, f"{view}/{mid}"
        # the rail is adjacent to its own pile and NOT inside the gap between
        # the two piles (that gap belongs to the shared stacking rail)
        assert not (pile.y0 <= rail.y <= pile.y1)
        assert min(abs(rail.y - pile.y0), abs(rail.y - pile.y1)) <= 2.0
        # the drawn boom lands ON the served pile at its home repère
        home = machine.home_repere / 1000.0 if machine.display else None
        gx, gy, tipx, tipy, _ = machine_draw_pos(
            rail, pile if machine.display else None, home,
            machine.parking_frac, machine.scale)
        assert gy == rail.y
        if machine.display:
            assert pile.x0 <= tipx <= pile.x1 and pile.y0 <= tipy <= pile.y1


def test_cross_pile_reclaim_job_renders_parked_with_assumed_note(s3, s3_plan):
    """One rail per product line is APPLIED planning geometry.

    If a plan sends 140LG-RC-01 to LT-P2, the machine must NOT be silently
    moved onto the other line's rail — no GA or reach matrix supports that.
    It stays on its own rail and the hover states the cross-line assumption.
    """
    import copy

    from mph_sim.domain import Job

    plan = copy.deepcopy(s3_plan)
    plan.jobs = [j for j in plan.jobs
                 if not (j.kind == "dispatch" and j.machine.startswith("140LG"))]
    job = Job("TEST-LT-CROSS", "140LG-RC-01", "dispatch", "LT-P2", "C1",
              "BT", 750.0, 750.0, 0, 0, path=["L-LT-P2-OUT", "C1"])
    plan.jobs.append(job)
    state = build_process_mimic_state(s3, plan, basis="plan", bucket=0)
    geo = louta_geometry(s3)
    machine = _machine(geo, "140LG-RC-01")
    assert machine.rail_id == "140LG-RL1"          # unchanged home rail
    fig = process_mimic_figure(state, geo)
    hover = [str(text) for trace in fig.data
             for text in (trace.hovertext
                          if isinstance(trace.hovertext, (list, tuple))
                          else [])]
    cross = [text for text in hover
             if "140LG-RC-01" in text and "serving LT-P2" in text]
    assert cross, "cross-line service must be disclosed in the hover"
    assert "cross-line reach ASSUMED" in cross[0]
    # and the boom must not be drawn into a pile the rail does not flank
    assert "LT-P2" not in geo.rail("140LG-RL1").flanks


def test_solver_reclaimers_home_at_r500_without_a_job(s3):
    geo = louta_geometry(s3)
    for mid in ("120LG-RC-01", "120LG-RC-02"):
        machine = _machine(geo, mid)
        assert not machine.display and machine.home_repere == 500
    fig = _figure(s3, "LOUTA")
    hover = [str(text) for trace in fig.data
             for text in (trace.hovertext
                          if isinstance(trace.hovertext, (list, tuple))
                          else [])]
    assert any("position: 110LG-OL1 · R500" in text
               for text in hover)
    assert any("position: 110LG-OL2 · R500" in text
               for text in hover)


def test_ore_reclaim_routes_converge_into_the_common_transfer(s3):
    geo = louta_geometry(s3)
    common = next(c for c in geo.conveyors if c.cid == "RB4/RB5")
    assert "common ore transfer" in common.tag
    assert "either reclaim route → washing L1 or L2" in common.sub
    rb12 = next(c for c in geo.conveyors if c.cid == "RB1/RB2")
    rb3 = next(c for c in geo.conveyors if c.cid == "RB3")
    # both reclaim legs end at the same junction point, which starts the
    # common transfer route
    assert rb12.pts[-1][0] == rb3.pts[-1][0] == common.pts[0][0]
    assert any(e.kind == "diverter" and "120LG-FG" in e.label
               for e in geo.equipment)
    # and the common transfer feeds BOTH scrubber feed hoppers
    tags = " ".join(c.tag for c in geo.conveyors)
    assert "210L1-HP-01" in tags and "210L2-HP-01" in tags


def test_promoted_process_routes_are_bound_to_live_model_links(s3):
    from mph_sim.domain import Job, PlanResult

    plan = PlanResult(status="FEASIBLE", jobs=[
        Job(
            "TEST-L1-FEED", "120LG-RC-01", "reclaim", "110LG-OL1",
            grade="RAW-BT-GLOBAL", rate_tph=584.0, tonnage_t=584.0,
            start_bucket=0, end_bucket=0,
            path=["L-LT-OL1-RB", "L-LT-RB45-COMMON", "L-LT-RB6-L1"],
            source_space="110LG-OL1", target_space="LT-WASH-L1-WIP",
            process_stage="LOU-WASH-L1",
        ),
        Job(
            "TEST-L1-STACK", "130LG-ST-01", "stack", "LT-P1",
            grade="TBT", rate_tph=398.0, tonnage_t=398.0,
            start_bucket=0, end_bucket=0,
            path=["L-LT-WIP1-SL11", "L-LT-SL11-P1"],
            source_space="LT-WASH-L1-WIP", target_space="LT-P1",
            process_stage="LOU-PRODUCT-STACK-L1",
        ),
    ])
    state = build_process_mimic_state(
        s3, plan, basis="plan", bucket=0)
    machines = {
        machine.id: machine
        for machine in state.monitor.sites["LOUTA"].machines
    }
    assert machines["120LG-RC-01"].status == "Reclaiming"
    assert machines["130LG-ST-01"].status == "Stacking"
    assert state.flows["L-LT-OL1-RB"].tph == pytest.approx(584.0)
    assert state.flows["L-LT-RB45-COMMON"].tph == pytest.approx(584.0)
    assert state.flows["L-LT-SL11-P1"].tph == pytest.approx(398.0)

    geo = louta_geometry(s3)
    by_id = {conveyor.cid: conveyor for conveyor in geo.conveyors}
    assert by_id["RB1/RB2"].link_ids == ["L-LT-OL1-RB"]
    assert by_id["RB4/RB5"].link_ids == ["L-LT-RB45-COMMON"]
    assert "L-LT-SL11-P1" in by_id["SL11"].link_ids


# ---- 5/6. product storage + machinery ------------------------------------------
def test_product_capacity_and_initial_masses(s3):
    spaces = {space.id: space for space in s3.spaces}
    assert sum(spaces[pid].capacity_t for pid in PRODUCT_PILES) == \
        pytest.approx(PRODUCT_TOTAL_T)
    assert all(spaces[pid].capacity_t == pytest.approx(191_512.0)
               for pid in PRODUCT_PILES)
    assert sum(spaces[pid].mass_t for pid in PRODUCT_PILES) == \
        pytest.approx(191_512.0)
    assert all(spaces[pid].mass_t == pytest.approx(95_756.0)
               for pid in PRODUCT_PILES)


def test_product_lines_are_planning_lines_not_confirmed_physical_piles(s3):
    geo = louta_geometry(s3)
    zones = " ".join(z.label for z in geo.zones)
    assert "383,024 t total" in zones and "2 × 191,512 t planning lines" in zones
    assert "DERIVED equal split" in zones
    gaps = " ".join(gp[2] for gp in geo.gaps)
    assert "PHYSICAL product-pile count is NOT explicitly" in gaps
    assert "planning/solver lines" in gaps or "planning lines" in gaps
    # never claimed as confirmed
    assert "product pile count CONFIRMED" not in gaps


def test_shared_product_stacker_serves_both_lines(s3):
    for view in LOUTA_VIEWS:
        geo = geometry_for(s3, view)
        stacker = _machine(geo, "130LG-ST-01")
        rail = geo.rail(stacker.rail_id)
        assert set(rail.flanks) == set(PRODUCT_PILES), view
        p1, p2 = (geo.pile(pid) for pid in PRODUCT_PILES)
        assert min(p1.y1, p2.y1) < rail.y < max(p1.y0, p2.y0), view
        assert not stacker.display      # explicit product-stack transfer
        assert "130LG-ST-01" in {machine.id for machine in s3.machines}


def test_both_product_reclaim_routes_reach_the_common_c1_corridor(s3):
    geo = louta_geometry(s3)
    for cid, mid in (("RL1", "140LG-RC-01"), ("RL2", "140LG-RC-02")):
        conveyor = next(c for c in geo.conveyors if c.cid == cid)
        assert conveyor.machine_id == mid
    for cid, link in (("LT-P1-COLLECT", "L-LT-P1-OUT"),
                      ("LT-P2-COLLECT", "L-LT-P2-OUT")):
        conveyor = next(c for c in geo.conveyors if c.cid == cid)
        assert conveyor.link_ids == [link]
    outlet = next(e for e in geo.equipment
                  if e.kind == "corridor_out" and "C1" in e.link_ids)
    assert "C1" in outlet.label
    # corridor transit assumption preserved: Louta is the nearer site, ≈2 h
    c1 = s3.link("C1")
    assert c1.transit_buckets * s3.sim.dt_h == pytest.approx(2.0)
    assert "NEARER" in c1.evidence.note.upper()


# ---- 7. repères + evidence hygiene ---------------------------------------------
def test_every_louta_pile_keeps_the_r0_r1000_convention(s3):
    fig = _figure(s3, "LOUTA")
    canvas = _texts(fig)
    assert "R0" in canvas and "R1000" in canvas
    for space_id in PRODUCT_PILES:
        space = s3.space(space_id)
        assert (space.repere_at_x_from, space.repere_at_x_to) == (0, 1000)
    state = build_process_mimic_state(s3, basis="initial", bucket=0)
    for space_id in PRODUCT_PILES:
        assert state.space_repere_ends[space_id] == (0, 1000)
    # display-only ore piles are drawn with the same 0..1000 ruler
    geo = louta_geometry(s3)
    ruler_x = {round(pile.x0 + (pile.x1 - pile.x0) * frac, 3)
               for pile in (geo.pile(pid) for pid in ORE_PILES)
               for frac in (0.0, 1.0)}
    tick_x = {round(shape.x0, 3) for shape in fig.layout.shapes
              if shape.type == "line" and shape.x0 == shape.x1}
    assert ruler_x <= tick_x


def test_reclaimer_count_discrepancy_is_evidence_only(s3):
    for view in LOUTA_VIEWS:
        canvas = _texts(_figure(s3, view))
        assert "RC count" not in canvas, view
        assert "BoD 1 vs PD 2" not in canvas, view
        assert "count conflict" not in canvas.lower(), view
    gaps = " ".join(gp[2] for gp in louta_geometry(s3).gaps)
    assert "generic BoD Rev B table lists ONE ore reclaimer" in gaps
    assert "equipment-tagged process description specifies TWO" in gaps
    a8 = next(a for a in s3.assumptions if a.id == "S3-A8")
    assert "SOURCE DISCREPANCY" in a8.text
    assert "REP-00002" in a8.text and "REP-00004" in a8.text
    assert "conservatively maps RC-01→OL1 and RC-02→OL2" in a8.text


def test_evidence_register_separates_the_four_louta_stages(s3):
    gaps = louta_geometry(s3).gaps
    items = " | ".join(gp[1] for gp in gaps if gp[0] == "LOUTA")
    assert "102MG mine storage" in items
    assert "110/120LG homogenized ore-storage" in items
    assert "washing / beneficiation lines L1 + L2" in items
    assert "130/140LG product-storage" in items


# ---- 8. state behaviour --------------------------------------------------------
def test_initial_state_shows_the_exact_50_percent_inventories(s3):
    state = build_process_mimic_state(s3, basis="initial", bucket=0)
    piles = {p.id: p for p in state.monitor.sites["LOUTA"].piles}
    for pid in PRODUCT_PILES:
        assert piles[pid].mass_t == pytest.approx(95_756.0)
        assert piles[pid].fill_pct == pytest.approx(50.0)
    geo = louta_geometry(s3)
    for pid in ORE_PILES:
        assert geo.pile(pid).modelled
        assert piles[pid].mass_t == pytest.approx(123_568.5)
        assert piles[pid].fill_pct == pytest.approx(50.0)


def test_final_state_uses_terminal_inventory_T_not_T_minus_1(s3, s3_plan):
    T = s3.sim.horizon_buckets
    state = build_process_mimic_state(s3, s3_plan, basis="plan", bucket=T)
    assert state.bucket == T
    assert state.monitor.max_bucket == T
    piles = {p.id: p for p in state.monitor.sites["LOUTA"].piles}
    for pid in PRODUCT_PILES:
        series = s3_plan.inventory[pid]
        assert len(series) == T + 1
        assert piles[pid].mass_t == pytest.approx(series[T])
        if series[T] != pytest.approx(series[T - 1]):
            assert piles[pid].mass_t != pytest.approx(series[T - 1])
    # a bucket beyond the horizon is clamped to T, never wrapped
    assert build_process_mimic_state(
        s3, s3_plan, basis="plan", bucket=T + 5).bucket == T


def test_machine_positions_follow_the_selected_state(s3, s3_plan):
    T = s3.sim.horizon_buckets
    initial = build_process_mimic_state(s3, s3_plan, basis="initial", bucket=0)
    final = build_process_mimic_state(s3, s3_plan, basis="plan", bucket=T)
    for mid in ("140LG-RC-01", "140LG-RC-02"):
        assert initial.machine_pos[mid].repere is not None
        assert final.machine_pos[mid].repere is not None
    # no job is active at the terminal boundary
    assert not [j for j in final.jobs]


def test_only_102mg_remains_reference_only_in_louta(s3):
    """Promoted assets bind to the model; 102MG never gains fake live data."""
    scenario_machines = {m.id for m in s3.machines}
    assert {"102MG-ST-01", "102MG-ST-02"}.isdisjoint(scenario_machines)
    assert {m.id for m in s3.machines if m.site == "LOUTA"} == {
        "110LG-ST-01", "120LG-RC-01", "120LG-RC-02", "130LG-ST-01",
        "140LG-RC-01", "140LG-RC-02"}
    assert set(ORE_PILES + PRODUCT_PILES) <= {
        s.id for s in s3.spaces if s.site == "LOUTA"}
    geo = louta_geometry(s3)
    for pid in ("102MG-SP-01", "102MG-SP-02"):
        assert not geo.pile(pid).modelled
    for pid in ORE_PILES:
        assert geo.pile(pid).modelled
    for mid in ("110LG-ST-01", "120LG-RC-01", "120LG-RC-02",
                "130LG-ST-01", "140LG-RC-01", "140LG-RC-02"):
        assert not _machine(geo, mid).display
    # no reference-only asset carries a solver link
    display_ids = {m.machine_id for m in geo.machines if m.display}
    for conveyor in geo.conveyors:
        if conveyor.machine_id in display_ids:
            assert not conveyor.link_ids, conveyor.cid
    reasons = " | ".join(reason for _, _, reason in geo.gaps)
    assert "MODELLING_GAP[MISSING_ROUTE_RATE_REACH]" in reasons


# ---- 9. nothing regressed elsewhere -------------------------------------------
def test_geometry_still_validates_in_every_view(s3):
    for view in ("LOUTA", "BENGUERIR", "MPH", "CHAIN", "GLOBAL"):
        assert validate_geometry(geometry_for(s3, view)) == [], view


def test_stale_graph_check_covers_promoted_louta_spaces_and_machines(s3):
    import copy

    stale = copy.deepcopy(s3)
    stale.spaces = [space for space in stale.spaces
                    if space.id != "110LG-OL1"]
    stale.machines = [machine for machine in stale.machines
                      if machine.id != "130LG-ST-01"]
    mismatch = geometry_model_mismatch(stale, louta_geometry(stale))
    assert "space 110LG-OL1" in mismatch
    assert "machine 130LG-ST-01" in mismatch
    assert not any("102MG" in item for item in mismatch)


def test_benguerir_geometry_unchanged_by_this_pass(s3):
    detail = benguerir_geometry(s3)
    compact = benguerir_geometry(s3, compact=True)
    for geo in (detail, compact):
        assert [p.pile_id for p in geo.piles] == [
            "BG-RAW-1", "BG-RAW-2", "BG-P1", "BG-P2"]
        assert {m.machine_id for m in geo.machines} == {
            "BG-RAW-ST-1", "BG-RAW-BWR", "BG-RAW-ST-2", "ST3", "RC2", "RC3"}
        assert {line.line_id for line in geo.process_lines} == {
            "BG-WASH-L1", "BG-WASH-L2", "BG-WASH-L3"}
        assert {line.status for line in geo.process_lines} == {
            "OPERATING", "FUTURE"}
        raw = {p.pile_id: p for p in geo.piles
               if p.pile_id.startswith("BG-RAW")}
        assert all(p.capacity_t == 200_000 for p in raw.values())
        assert all(p.display_mass_t == pytest.approx(100_000.0)
                   for p in raw.values())
