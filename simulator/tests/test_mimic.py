"""Process-mimic tests: geometry integrity, evidence discipline, symbols,
dynamic state, rail constraints, determinism."""
import copy
import sys
from pathlib import Path

import plotly.graph_objects as go
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mph_sim import datasets                                     # noqa: E402
from mph_sim.optimizer import SolveOptions, solve_plan           # noqa: E402
from mph_sim.simulator import Simulator                          # noqa: E402
from ui import hmi_symbols as hs, theme                          # noqa: E402
from ui.layout_geometry import (SITE_PREFIX, chain_geometry,     # noqa: E402
                                geometry_for, louta_geometry,
                                m16_geometry)
from ui.process_mimic import (MimicOpts, _conv_state,            # noqa: E402
                              build_process_mimic_state,
                              machine_draw_pos, process_mimic_figure)
from ui.stockyard_monitor import EXPECTED_COVERAGE               # noqa: E402


@pytest.fixture(scope="module")
def s3():
    return datasets.end_to_end()


@pytest.fixture(scope="module")
def s3_plan(s3):
    return solve_plan(s3, SolveOptions(max_time_s=3))


@pytest.fixture(scope="module")
def s3_sim(s3, s3_plan):
    sim = Simulator(copy.deepcopy(s3), copy.deepcopy(s3_plan),
                    auto_replan=False)
    for _ in range(8):
        sim.step()
    return sim


def test_composite_layout_batches_plotly_primitives(s3, monkeypatch):
    """Prevent the O(n²) incremental Plotly build that blanked the UI."""
    incremental_calls = []

    def record_incremental(*args, **kwargs):
        incremental_calls.append((args, kwargs))

    monkeypatch.setattr(go.Figure, "add_shape", record_incremental)
    monkeypatch.setattr(go.Figure, "add_annotation", record_incremental)
    state = build_process_mimic_state(s3)
    fig = process_mimic_figure(state, chain_geometry(s3))
    assert not incremental_calls, \
        "composite mimic must batch shapes/annotations before Figure creation"
    assert len(fig.layout.shapes) > 200
    assert len(fig.layout.annotations) > 100
    assert fig.layout.height == 700


# ---- 1/2: every modelled pile & machine exactly once ---------------------------
def test_every_modelled_stockpile_exactly_once(s3):
    for view in ("CHAIN", "LOUTA", "BENGUERIR", "MPH"):
        geo = geometry_for(s3, view)
        ids = [p.pile_id for p in geo.piles]
        assert len(ids) == len(set(ids)), f"duplicate pile in {view}"
    chain = chain_geometry(s3)
    modelled = {p.pile_id for p in chain.piles if p.modelled}
    # Mine-feed and washing-WIP interface buffers belong in monitor coverage,
    # but they are not physical stockpile lanes in the yard layout.
    expected = {
        space_id
        for coverage in EXPECTED_COVERAGE.values()
        for space_id in coverage["spaces"]
        if s3.space(space_id).area not in {
            "mine-interface", "process-interface"}
    }
    assert modelled == expected


def test_every_machine_exactly_once_and_solver_set_complete(s3):
    chain = chain_geometry(s3)
    ids = [m.machine_id for m in chain.machines]
    assert len(ids) == len(set(ids))
    solver = {m.machine_id for m in chain.machines if not m.display}
    expected = set().union(*(c["machines"]
                             for c in EXPECTED_COVERAGE.values()))
    assert solver == expected


# ---- 3: both 102MG stackers in Louta -------------------------------------------
def test_102mg_st_01_and_02_appear_in_louta(s3):
    for geo in (louta_geometry(s3), chain_geometry(s3)):
        disp = {m.machine_id: m for m in geo.machines if m.display}
        assert "102MG-ST-01" in disp and "102MG-ST-02" in disp
        for mid in ("102MG-ST-01", "102MG-ST-02"):
            m = disp[mid]
            assert m.kind == "stacker"
            assert "CONFIRMED" in m.note and "TBC" in m.note
            # display machines are never solver resources
            assert m.display


# ---- 4: site-qualified labels ---------------------------------------------------
def test_all_machine_labels_site_qualified(s3):
    prefixes = tuple(f"{p} · " for p in SITE_PREFIX.values())
    for view in ("CHAIN", "LOUTA", "BENGUERIR", "MPH"):
        for m in geometry_for(s3, view).machines:
            assert m.label.startswith(prefixes), \
                f"{view}/{m.machine_id}: '{m.label}' not site-qualified"


# ---- 5: stacker vs reclaimer symbols visibly differ ------------------------------
def test_stacker_and_reclaimer_symbols_differ():
    f1, f2 = go.Figure(), go.Figure()
    hs.stacker(f1, 0, 0, 3, 1, "Idle", "T1")
    hs.reclaimer(f2, 0, 0, 3, 1, "Idle", "T2")
    types1 = sorted(s.type for s in f1.layout.shapes)
    types2 = sorted(s.type for s in f2.layout.shapes)
    assert types1 != types2
    # reclaimer has a bucket WHEEL (extra circle beyond the status lamp)
    assert sum(1 for s in f2.layout.shapes if s.type == "circle") > \
        sum(1 for s in f1.layout.shapes if s.type == "circle")
    # stacker has a discharge-head path triangle
    assert any(s.type == "path" for s in f1.layout.shapes)


# ---- 6: M16 shows all eight stock lines ------------------------------------------
def test_m16_displays_all_eight_lanes(s3):
    for geo in (m16_geometry(s3), chain_geometry(s3)):
        hp = {p.pile_id for p in geo.piles if p.pile_id.startswith("HP-")}
        assert hp == {f"HP-{i:02d}" for i in range(1, 9)}
    st = build_process_mimic_state(s3)
    fig = process_mimic_figure(st, m16_geometry(s3))
    texts = " ".join(str(a.text) for a in fig.layout.annotations if a.text)
    for i in range(1, 9):
        assert f"HP-{i:02d}" in texts


# ---- 7/8: conveyors are configured polylines with valid endpoints ----------------
def test_conveyors_render_from_polylines_not_box_arrows(s3):
    link_ids = {l.id for l in s3.links}
    for view in ("CHAIN", "LOUTA", "BENGUERIR", "MPH"):
        geo = geometry_for(s3, view)
        assert geo.conveyors, view
        x0, y0, x1, y1 = geo.bounds
        for c in geo.conveyors:
            assert len(c.pts) >= 2, f"{view}/{c.cid}: not a polyline"
            for (px, py) in c.pts:
                assert x0 - 1e-6 <= px <= x1 + 1e-6, f"{view}/{c.cid} x"
                assert y0 - 1e-6 <= py <= y1 + 1e-6, f"{view}/{c.cid} y"
            for li in c.link_ids:
                assert li in link_ids, \
                    f"{view}/{c.cid}: unknown model link {li}"
        # real routed geometry: multi-segment paths exist
        assert any(len(c.pts) >= 3 for c in geo.conveyors), view


# ---- 9: active job path highlights the correct conveyors -------------------------
def test_active_job_path_highlights_correct_conveyors(s3, s3_plan):
    st = build_process_mimic_state(s3, s3_plan, basis="plan", bucket=None)
    # find a bucket with an active job that has a path
    j = next(j for j in s3_plan.jobs if j.path and j.kind != "stack")
    st = build_process_mimic_state(s3, s3_plan, basis="plan",
                                   bucket=j.start_bucket)
    geo = chain_geometry(s3)
    opts = MimicOpts(selected_job=j.id)
    hits = [c.cid for c in geo.conveyors
            if _conv_state(st, c, opts)[4]]        # selected flag
    assert hits, "no conveyor highlighted for the selected job"
    for cid in hits:
        cg = next(c for c in geo.conveyors if c.cid == cid)
        assert any(li in j.path for li in cg.link_ids), \
            f"{cid} highlighted without carrying the job's links"
    # unrelated selection: nothing highlighted
    assert not [c for c in geo.conveyors
                if _conv_state(st, c, MimicOpts(selected_job="NOPE"))[4]]


# ---- 10: machines stay on their rails ---------------------------------------------
def test_machine_positions_constrained_to_rail(s3):
    geo = m16_geometry(s3)
    for rid in ("M16A-A-T06", "M16A-A-T11", "M16A-B-T06", "M16A-B-T11",
                "M16A-C-T06"):
        rail = geo.rail(rid)
        pgs = [geo.pile(pid) for pid in rail.flanks] + [None]
        for pg in pgs:
            for x_norm in (None, -0.4, 0.0, 0.5, 1.0, 2.3):
                gx, gy, tipx, tipy, side = machine_draw_pos(
                    rail, pg, x_norm, parking_frac=-9.0)
                assert gy == rail.y, "gantry left its rail line"
                assert rail.x0 <= gx <= rail.x1, "gantry beyond rail ends"
                if pg is not None and x_norm is not None:
                    assert pg.x0 <= tipx <= pg.x1, "boom tip outside pile"
                    assert pg.y0 <= tipy <= pg.y1, "boom tip outside pile"
                    # boom points toward the served pile bank
                    assert side == (1 if pg.y0 >= rail.y else -1)


# ---- 11: bases change state without mutating inputs --------------------------------
def test_bases_change_state_without_mutation(s3, s3_plan, s3_sim):
    before = {sp.id: sp.mass_t for sp in s3.spaces}
    st_i = build_process_mimic_state(s3, s3_plan, s3_sim, basis="initial")
    st_p = build_process_mimic_state(s3, s3_plan, s3_sim, basis="plan",
                                     bucket=6)
    st_s = build_process_mimic_state(s3, s3_plan, s3_sim, basis="sim",
                                     bucket=6)
    def mass(st):
        return {p.id: p.mass_t for sv in st.monitor.sites.values()
                for p in sv.piles}
    assert mass(st_i) != mass(st_p) or mass(st_i) != mass(st_s)
    assert not any(f.running for f in st_i.flows.values())
    assert any(f.running for f in st_p.flows.values())
    assert any(f.running for f in st_s.flows.values())
    assert {sp.id: sp.mass_t for sp in s3.spaces} == before
    assert len(s3_plan.jobs) > 0     # untouched


def test_initial_and_final_plan_states_use_inventory_boundaries(s3, s3_plan):
    """Initial is inventory[0]; Final is the terminal inventory[T] boundary."""
    horizon = s3.sim.horizon_buckets
    assert all(len(series) == horizon + 1
               for series in s3_plan.inventory.values())

    initial = build_process_mimic_state(
        s3, s3_plan, basis="initial", bucket=0)
    final = build_process_mimic_state(
        s3, s3_plan, basis="plan", bucket=horizon)

    initial_mass = {
        pile.id: pile.mass_t
        for site in initial.monitor.sites.values()
        for pile in site.piles
    }
    final_mass = {
        pile.id: pile.mass_t
        for site in final.monitor.sites.values()
        for pile in site.piles
    }
    scenario_mass = {space.id: space.mass_t for space in s3.spaces}

    assert initial.basis == "initial" and initial.bucket == 0
    assert final.basis == "plan" and final.bucket == horizon
    assert initial_mass == {
        pile_id: pytest.approx(scenario_mass[pile_id])
        for pile_id in initial_mass
    }
    assert final_mass == {
        pile_id: pytest.approx(s3_plan.inventory[pile_id][horizon])
        for pile_id in final_mass
    }
    assert final.timestamp == \
        s3.sim.bucket_dt(horizon).strftime("%d %b %Y %H:%M")
    assert final.jobs == []
    assert not any(flow.running for flow in final.flows.values())
    assert any(initial_mass[pile_id] != pytest.approx(final_mass[pile_id])
               for pile_id in initial_mass)


def test_sim_failure_window_shows_unavailable():
    from mph_sim.domain import SimEvent
    s2 = datasets.mph_phosphate()
    p2 = solve_plan(s2, SolveOptions(max_time_s=2))
    sim = Simulator(copy.deepcopy(s2), copy.deepcopy(p2),
                    events=[SimEvent("E1", "machine_failure", 3, "RC01-A",
                                     duration_buckets=2)], auto_replan=False)
    for _ in range(6):
        sim.step()
    st = build_process_mimic_state(s2, p2, sim, basis="sim", bucket=3)
    assert "RC01-A" in st.down_machines
    st5 = build_process_mimic_state(s2, p2, sim, basis="sim", bucket=5)
    assert "RC01-A" not in st5.down_machines


# ---- 12: TBC geometry is never rendered as confirmed --------------------------------
def test_reference_geometry_stamped_and_dashed_without_operator_tbc(s3):
    for view in ("CHAIN", "LOUTA", "BENGUERIR", "MPH"):
        geo = geometry_for(s3, view)
        assert geo.not_to_scale, view
        assert geo.stamps, f"{view}: missing NOT-TO-SCALE stamp"
    lou = louta_geometry(s3)
    for p in lou.piles:
        if p.pile_id.startswith("102MG"):
            assert not p.modelled and p.tbc
    st = build_process_mimic_state(s3)
    raw = process_mimic_figure(st, lou, MimicOpts(show_evidence=True))
    fig = theme.operator_figure(raw)
    texts = " ".join(str(a.text) for a in fig.layout.annotations if a.text)
    assert "POSITION pending" in texts
    assert "NOT TO SCALE" in texts
    assert "REFERENCE ROUTE" in texts
    assert "TBC" not in texts.upper()

    # Wording is operator-safe, while every reference-only route retains its
    # two visibly dashed belt traces.
    reference_routes = [conveyor for conveyor in lou.conveyors if conveyor.tbc]
    dashed_belts = [
        trace for trace in fig.data
        if getattr(getattr(trace, "line", None), "dash", None) == "dash"
    ]
    assert len(dashed_belts) == 2 * len(reference_routes)


# ---- 13: no direct 102MG -> C1 connection --------------------------------------------
def test_no_102mg_to_c1_link(s3):
    for geo in (louta_geometry(s3), chain_geometry(s3)):
        for c in geo.conveyors:
            if "102MG" in c.cid:
                assert not c.planning_enabled
                assert "C1" not in c.link_ids
        # conveyors bound to C1 never start inside the 102MG mine band
        mine_piles = [p for p in geo.piles if p.pile_id.startswith("102MG")]
        y_mine = min(p.y0 for p in mine_piles)
        for c in geo.conveyors:
            if "C1" in c.link_ids:
                assert all(py < y_mine for _, py in c.pts), \
                    "C1 conveyor reaches into 102MG mine storage"


# ---- 14: deterministic solver signature unchanged -------------------------------------
def test_solver_signature_unchanged():
    import hashlib
    import json
    # Keep the selectable BG train profile pinned independently of the
    # fresh S0 FULL_FLOW default.
    scenario = datasets.end_to_end()
    datasets.apply_planning_demand_profile(scenario, "BG_TRAIN")
    res = solve_plan(scenario, SolveOptions(max_time_s=3))
    sig = tuple((j.machine, j.space, j.dest_id, j.start_bucket, j.end_bucket,
                 j.tonnage_t) for j in res.jobs)
    h = hashlib.sha256(json.dumps(sig).encode()).hexdigest()[:16]
    # Positioning remains a post-solve minute activity.
    assert h == "5c5d9dc60ce40f12"


# ---- 16: page renders headlessly -------------------------------------------------------
def test_overview_mimic_renders_headlessly():
    import json
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
    at.run()
    assert not at.exception          # S3 default, empty state
    p2 = solve_plan(datasets.mph_phosphate(), SolveOptions(max_time_s=2))
    at.session_state["plan"] = p2
    at.run()
    from conftest import nav_to
    nav_to(at, "1 · Executive Overview")
    assert not at.exception
    basis_controls = [radio for radio in at.radio
                      if radio.label in ("Stockyard state", "State view")]
    assert basis_controls, "process mimic must expose a state selector"
    assert "Initial state" in basis_controls[0].options
    assert "Final plan" in basis_controls[0].options
    body = " ".join(str(m.value) for m in at.markdown)
    assert "Process mimic" in body
    specs = [json.loads(e.proto.spec) for e in at.get("plotly_chart")]
    mimic_specs = [spec for spec in specs
                   if spec.get("layout", {}).get("height") == 700
                   and len(spec.get("layout", {}).get("shapes", [])) > 200]
    assert len(mimic_specs) == 1, \
        "planned overview must mount the full process-mimic layout"
    assert len(mimic_specs[0].get("data", [])) > 40
