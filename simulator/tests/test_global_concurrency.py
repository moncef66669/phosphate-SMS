"""Global Stockyard Layout & Flow page (ex "Global Process Flow") + corrected machine placement + same-pile
stacking/reclaiming concurrency (USER-CONFIRMED rule, 15-Jul-2026)."""
import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mph_sim import datasets                                     # noqa: E402
from mph_sim.domain import (BeltProfileEntry, DemandEntry,       # noqa: E402
                            Destination, Grade, Link, Machine, Node,
                            Scenario, SimParams, Site, StockSpace)
from mph_sim.optimizer import SolveOptions, solve_plan           # noqa: E402
from mph_sim.simulator import Simulator                          # noqa: E402
from ui import common                                            # noqa: E402
from ui.layout_geometry import (M16_PHYSICAL_TAGS,               # noqa: E402
                                geometry_for, global_geometry,
                                m16_geometry, validate_geometry)
from ui.process_mimic import (MimicOpts, build_process_mimic_state,  # noqa: E402
                              process_mimic_figure)


@pytest.fixture(scope="module")
def s3():
    return datasets.end_to_end()


@pytest.fixture(scope="module")
def s3_plan(s3):
    return solve_plan(s3, SolveOptions(max_time_s=3))


# ================== A. Global Stockyard Layout & Flow page =========================
def test_global_page_exists_under_monitor():
    assert "15 · Global Stockyard Layout & Flow" in common.NAV_GROUPS["Monitor"]
    assert "15 · Global Stockyard Layout & Flow" in common.ALL_PAGE_LABELS
    nums = [l.split(" ·")[0] for l in common.ALL_PAGE_LABELS]
    assert len(nums) == len(set(nums)), "duplicate page number"


def test_global_three_site_blocks_visible(s3):
    g = global_geometry(s3)
    zl = " ".join(z.label for z in g.zones)
    assert "LOUTA" in zl and "BENGUERIR" in zl and "M16" in zl
    sites = {p.pile_id[:2] for p in g.piles}
    assert {p.pile_id for p in g.piles} >= {"BG-P1", "BG-P2", "LT-P1",
                                            "LT-P2"} | \
        {f"HP-{i:02d}" for i in range(1, 9)}


def test_c1_connects_louta_to_m16_and_c2_benguerir(s3):
    g = global_geometry(s3)
    c1 = next(c for c in g.conveyors if c.cid == "C1")
    c2 = next(c for c in g.conveyors if c.cid == "C2")
    assert c1.link_ids == ["C1"] and c2.link_ids == ["C2"]
    assert c1.sub == "transit 2 h"
    assert c2.sub == "transit 4 h"
    for stale_word in ("provisional", "nearer", "farther"):
        assert stale_word not in (c1.sub + c2.sub).lower()
    lt = g.pile("LT-P1")
    m16_if = next(e for e in g.equipment
                  if e.kind == "corridor_in" and "C1" in e.label)
    assert lt.x0 <= c1.pts[0][0] <= lt.x1 + 20, \
        "C1 must start at the Louta product-yard block"
    assert abs(c1.pts[-1][0] - m16_if.x) < 3 and \
        abs(c1.pts[-1][1] - m16_if.y) < 1.5, "C1 must end at the M16 interface"
    bg_if = next(e for e in g.equipment
                 if e.kind == "corridor_in" and "C2" in e.label)
    bg = g.pile("BG-P1")
    assert bg.x0 <= c2.pts[0][0] <= bg.x1 + 25
    assert abs(c2.pts[-1][0] - bg_if.x) < 3

    def _length(points):
        return sum(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
                   for (x0, y0), (x1, y1) in zip(points, points[1:]))

    assert _length(c1.pts) < _length(c2.pts), \
        "NOT-TO-SCALE composite must still show Louta/C1 as relatively nearer"


def test_no_direct_102mg_to_c1(s3):
    for g in (global_geometry(s3), geometry_for(s3, "LOUTA")):
        for c in g.conveyors:
            if "102MG" in c.cid:
                assert not c.planning_enabled
                assert "C1" not in c.link_ids
        mine = [p for p in g.piles if p.pile_id.startswith("102MG")]
        y_mine = min(p.y0 for p in mine)
        for c in g.conveyors:
            if "C1" in c.link_ids:
                assert all(py < y_mine for _, py in c.pts), \
                    "C1 conveyor reaches into 102MG mine storage"


def test_all_m16_phase1_spaces_render_globally(s3):
    g = global_geometry(s3)
    hp = {p.pile_id for p in g.piles if p.pile_id.startswith("HP-")}
    assert hp == {f"HP-{i:02d}" for i in range(1, 9)}
    st = build_process_mimic_state(s3)
    fig = process_mimic_figure(st, g)
    texts = " ".join(str(a.text) for a in fig.layout.annotations if a.text)
    for i in range(1, 9):
        assert f"HP-{i:02d}" in texts


# ==================== B/C. physical machine placement ==============================
# CORRECTED 25-Jul-2026 (PFD-0001[3]): there is no shared T06/T11 corridor —
# each machine sits on its own canonical line, flanked only by the bays it can
# reach. The full bay/reachability matrix is asserted in test_m16_bays.py.
def test_stackers_each_on_their_own_line(s3):
    g = m16_geometry(s3)
    homes = {m.machine_id: m.rail_id for m in g.machines if not m.display}
    assert homes["ST01-A"] == "M16A-A-T06"
    assert homes["ST01-B"] == "M16A-B-T06"
    assert homes["ST01-C"] == "M16A-C-T06"
    assert g.rail("T06") is None, "the shared T06 corridor must not come back"
    for mid, expected in (("ST01-A", {"HP-01", "HP-05"}),
                          ("ST01-B", {"HP-02", "HP-06", "HP-03", "HP-07"}),
                          ("ST01-C", {"HP-04", "HP-08"})):
        rail = g.rail(homes[mid])
        assert rail.kind == "stacker"
        assert set(rail.flanks) == expected, mid
    assert not validate_geometry(g)


def test_reclaimers_each_on_their_own_line(s3):
    g = m16_geometry(s3)
    homes = {m.machine_id: m.rail_id for m in g.machines if not m.display}
    assert homes["RC01-A"] == "M16A-A-T11"
    assert homes["RC01-B"] == "M16A-B-T11"
    assert g.rail("T11") is None, "the shared T11 corridor must not come back"
    assert set(g.rail("M16A-A-T11").flanks) == {"HP-01", "HP-05",
                                                "HP-02", "HP-06"}
    assert set(g.rail("M16A-B-T11").flanks) == {"HP-03", "HP-07",
                                                "HP-04", "HP-08"}
    # physical tag mapping (PFD-0001[3] equipment list) — internal IDs unchanged
    tags = {m.machine_id: m.physical_tag for m in g.machines
            if m.physical_tag and not m.display}
    assert tags == M16_PHYSICAL_TAGS
    assert set(tags) == {"ST01-A", "ST01-B", "ST01-C", "RC01-A", "RC01-B"}


def test_geometry_validation_all_views(s3):
    for view in ("GLOBAL", "CHAIN", "LOUTA", "BENGUERIR", "MPH"):
        assert validate_geometry(geometry_for(s3, view)) == [], view


def test_future_units_behind_toggle_never_solver_inventory(s3):
    g0 = geometry_for(s3, "MPH", show_future=False)
    assert not any(p.future for p in g0.piles)
    g1 = geometry_for(s3, "MPH", show_future=True)
    fut = [p for p in g1.piles if p.future]
    assert fut and all(not p.modelled for p in fut)
    assert not any(sp.id == fut[0].pile_id for sp in s3.spaces)


# ===================== D. same-pile concurrency (solver) ===========================
def _mini_scenario(cap=100_000, length=300.0, allowed=True,
                   init_mass=40_000) -> Scenario:
    sc = Scenario(name="MINI — concurrency", description="test",
                  sim=SimParams(horizon_buckets=6, dt_h=1.0, seed=7,
                                max_solve_s=2))
    sc.sites = [Site("MPH", "hub", "hub",
                     same_pile_concurrency_allowed=allowed,
                     concurrency_evidence="USER_CONFIRMED (test)")]
    sc.grades = [Grade("G", "G", {"BPL": 66.0})]
    sc.spaces = [StockSpace("P1", "MPH", "M16", "open", cap, init_mass, "G",
                            designated_grade="G", x_from=0, x_to=length)]
    sc.machines = [
        Machine("ST", "MPH", "stacker", 100, 1500, ["P1"], rail="T06"),
        Machine("RC", "MPH", "reclaimer", 100, 1500, ["P1"], rail="T11"),
    ]
    sc.nodes = [Node("SRC", "MPH", "source", "SRC"),
                Node("TWR", "MPH", "tower", "TWR"),
                Node("SPACE:P1", "MPH", "space", "P1"),
                Node("DEST:D1", "MPH", "dest", "D1")]
    sc.links = [
        Link("L-IN", "SRC", "TWR", capacity_tph=2000, transit_buckets=1),
        Link("L-STK-P1", "TWR", "SPACE:P1", capacity_tph=2000),
        Link("L-RCL-P1", "SPACE:P1", "DEST:D1", capacity_tph=2000),
    ]
    sc.destinations = [Destination("D1", "MPH", "DEST:D1",
                                   demand_default_tph=1200)]
    sc.belt_profile = [BeltProfileEntry("L-IN", t, 1200.0, "G")
                       for t in range(1, 5)]
    return sc


def _concurrent_buckets(sc, plan):
    """[(bucket, space, stack_jobs, out_jobs)] — SAME-pile concurrency."""
    out = []
    for t in range(sc.sim.horizon_buckets):
        by_space = {}
        for j in plan.jobs:
            if j.start_bucket <= t <= j.end_bucket:
                by_space.setdefault(j.space, []).append(j)
        for spid, js in sorted(by_space.items()):
            st_ = [j for j in js if j.kind == "stack"]
            ot = [j for j in js if j.kind in ("reclaim", "dispatch")]
            if st_ and ot:
                out.append((t, spid, st_, ot))
    return out


def test_concurrency_allowed_when_zones_separated():
    sc = _mini_scenario()
    plan = solve_plan(sc, SolveOptions(max_time_s=2))
    assert plan.status in ("OPTIMAL", "FEASIBLE")
    conc = _concurrent_buckets(sc, plan)
    assert conc, "expected same-pile stack+reclaim concurrency"
    for t, spid, st_, ot in conc:
        gap = min(j.start_x for j in st_) - max(j.stop_x for j in ot)
        assert gap >= 0, "work zones must not overlap"
    # machine double-booking remains prohibited
    for t in range(sc.sim.horizon_buckets):
        mids = [j.machine for j in plan.jobs
                if j.start_bucket <= t <= j.end_bucket]
        assert len(mids) == len(set(mids))


def test_concurrency_rejected_when_room_insufficient():
    # pile too short for two zones + separation: usable share = 0
    sc = _mini_scenario(cap=4_000, length=80.0, init_mass=2_000)
    plan = solve_plan(sc, SolveOptions(max_time_s=2))
    assert plan.status in ("OPTIMAL", "FEASIBLE")
    assert not _concurrent_buckets(sc, plan), \
        "concurrency must be rejected when zones cannot be separated"


def test_concurrency_blocked_by_site_configuration():
    sc = _mini_scenario(allowed=False)
    plan = solve_plan(sc, SolveOptions(max_time_s=2))
    assert plan.status in ("OPTIMAL", "FEASIBLE")
    assert not _concurrent_buckets(sc, plan), \
        "site-configured prohibition must hold (config-driven, not " \
        "categorical)"


def test_no_passthrough_reclaim_during_concurrency():
    # start EMPTY: reclaiming may only use start-of-bucket inventory while
    # stacking runs, so bucket-t arrivals are reclaimable from t+1 only
    sc = _mini_scenario(init_mass=0)
    plan = solve_plan(sc, SolveOptions(max_time_s=2))
    inv = plan.inventory["P1"]
    for t, spid, st_, ot in _concurrent_buckets(sc, plan):
        out_t = sum(j.rate_tph * 1.0 for j in ot)
        assert out_t <= inv[t] + 1.0, \
            "concurrent reclaim may only consume start-of-bucket inventory"


def test_concurrent_pile_inventory_mass_balance(s3, s3_plan):
    """Inventory[p,end] = Inventory[p,0] + outputs-in - inputs-out (+exo).

    Process jobs consume ``tonnage_t`` from ``source_space`` and place
    ``output_tonnage_t`` in ``target_space`` after applying the configured
    yield.  Using the explicit flow fields keeps this conservation check valid
    for both ordinary stockyard jobs and the stocked Louta process chain.
    """
    T = s3.sim.horizon_buckets
    for spid, series in s3_plan.inventory.items():
        ins = sum(j.output_tonnage_t for j in s3_plan.jobs
                  if j.target_space == spid)
        outs = sum(j.tonnage_t for j in s3_plan.jobs
                   if j.source_space == spid)
        exo = 0.0
        for e in s3.exogenous:
            for t in range(T):
                if e.start_bucket <= t <= e.end_bucket:
                    if e.target_space == spid:
                        exo += e.rate_tph * s3.sim.dt_h
                    if e.source_space == spid:
                        exo -= e.rate_tph * s3.sim.dt_h
        assert series[-1] == pytest.approx(series[0] + ins - outs + exo,
                                           abs=2.0), \
            f"{spid}: mass balance violated over the horizon"


def test_site_concurrency_policies_are_explicit(s3):
    mph = next(s for s in s3.sites if s.id == "MPH")
    assert mph.same_pile_concurrency_allowed is True
    assert "USER_CONFIRMED" in mph.concurrency_evidence

    louta = next(s for s in s3.sites if s.id == "LOUTA")
    assert louta.same_pile_concurrency_allowed is False
    assert "ASSUMED conservative S0 policy" in louta.concurrency_evidence
    assert "do not share a pile" in louta.concurrency_evidence

    benguerir = next(s for s in s3.sites if s.id == "BENGUERIR")
    assert benguerir.same_pile_concurrency_allowed is None


# ====================== E. concurrency visualization ==============================
def _with_same_pile_concurrency(sc, plan, space="HP-01"):
    """Explicit DISPLAY-STATE fixture: one stacking and one reclaim job on the
    same pile in the same bucket, work zones separated.

    CHANGED 25-Jul-2026: the baseline S3 plan no longer happens to contain a
    same-pile concurrent bucket — the M16 reception envelope and bay-adjacency
    reach reshaped it. The RENDERING is what this test is about, so the state
    is built explicitly (the established pattern for display fixtures) instead
    of tuning the scenario to reproduce the old plan.
    """
    import copy
    from mph_sim.domain import Job
    planned = copy.deepcopy(plan)
    sp = sc.space(space)
    length = sp.x_to - sp.x_from
    reclaim = Job("TEST-CONC-RC", "RC01-A", "reclaim", space, "PAP-1",
                  sp.grade, 900.0, 900.0, 0, 0,
                  start_x=sp.x_from, stop_x=sp.x_from + 0.2 * length,
                  path=["L-RCL-HP-01", "L-H-A", "L-RTE-A-PAP"])
    stack = Job("TEST-CONC-ST", "ST01-A", "stack", space, "",
                sp.grade, 800.0, 800.0, 0, 0,
                start_x=sp.x_from + 0.8 * length, stop_x=sp.x_to,
                path=["L-STK-HP-01"])
    planned.jobs = [j for j in planned.jobs
                    if not (j.start_bucket <= 0 <= j.end_bucket
                            and j.space == space)] + [reclaim, stack]
    return planned, 0, space, [stack], [reclaim]


def test_visualization_shows_both_jobs_and_buffer(s3, s3_plan):
    plan, t, spid, st_, ot = _with_same_pile_concurrency(s3, s3_plan)
    s3_plan = plan
    state = build_process_mimic_state(s3, s3_plan, basis="plan", bucket=t)
    cvm = state.concurrency.get(spid)
    assert cvm is not None
    assert cvm.status == "ok" and cvm.sep_m >= cvm.req_m
    fig = process_mimic_figure(state, m16_geometry(s3), MimicOpts(phase=t))
    texts = " ".join(str(a.text) for a in fig.layout.annotations if a.text)
    assert "concurrent stack+reclaim" in texts
    assert "sep" in texts and "ASSUMED" in texts
    for j in st_ + ot:                       # separate job IDs visible
        assert j.id in texts


def test_rendering_does_not_mutate_inputs(s3, s3_plan):
    before = {sp.id: sp.mass_t for sp in s3.spaces}
    jobs_before = [(j.id, j.start_x, j.stop_x) for j in s3_plan.jobs]
    sim = Simulator(copy.deepcopy(s3), copy.deepcopy(s3_plan),
                    auto_replan=False)
    for _ in range(4):
        sim.step()
    for b in (0, 2, 4):
        st_ = build_process_mimic_state(s3, s3_plan, sim, basis="sim",
                                        bucket=b)
        process_mimic_figure(st_, global_geometry(s3))
    assert {sp.id: sp.mass_t for sp in s3.spaces} == before
    assert [(j.id, j.start_x, j.stop_x) for j in s3_plan.jobs] == jobs_before


# ============================ page renders headlessly ==============================
def test_global_page_renders_headlessly():
    from conftest import nav_to
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
    at.run()
    nav_to(at, "15 · Global Stockyard Layout & Flow")
    assert not at.exception
    assert at.sidebar.radio[1].value == common.MONITOR_LANDING_PAGE
    assert not at.main.tabs
    assert [str(item.value) for item in at.main.subheader] == [
        "KPIs",
        "Global Stockyard Layout & Flow",
        "Job Schedule",
    ]
    assert at.get("iframe")
