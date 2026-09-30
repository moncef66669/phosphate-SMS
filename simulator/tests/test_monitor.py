"""Stockyard Monitor tests: coverage, palette, dynamics, states, rendering."""
import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mph_sim import datasets                                    # noqa: E402
from mph_sim.domain import SpecRange                            # noqa: E402
from mph_sim.optimizer import SolveOptions, solve_plan          # noqa: E402
from mph_sim.simulator import Simulator                         # noqa: E402
from ui.stockyard_monitor import (EXPECTED_COVERAGE, build_monitor_state,
                                  grade_color, site_figure)     # noqa: E402


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


# ---- coverage: every pile/machine exactly once --------------------------------
def test_every_phosphate_pile_rendered_exactly_once(s3):
    ms = build_monitor_state(s3)
    ids = [p.id for sv in ms.sites.values() for p in sv.piles]
    assert sorted(ids) == sorted(set(ids)), "duplicated pile in monitor"
    expected = set().union(*(c["spaces"] for c in EXPECTED_COVERAGE.values()))
    assert set(ids) == expected
    assert ms.discrepancies == []


def test_every_machine_rendered_exactly_once(s3):
    ms = build_monitor_state(s3)
    ids = [m.id for sv in ms.sites.values() for m in sv.machines]
    assert sorted(ids) == sorted(set(ids))
    expected = set().union(*(c["machines"] for c in EXPECTED_COVERAGE.values()))
    assert set(ids) == expected


def test_coverage_gaps_reported_not_modelled(s3):
    ms = build_monitor_state(s3)
    items = " ".join(g[1] for g in ms.gaps)
    # The six-pile Benguerir chain and all nine of its mobile machines are
    # active solver resources, so none may remain in the unmodelled register.
    assert "ST3" not in items
    assert "BG-RAW-ST-1" not in items
    assert "BG-RAW-BWR" not in items
    # Only the unreconciled B01 physical device tags remain a disclosure gap;
    # TM1/TSM2/TSM3 themselves are finite planning interfaces.
    assert "04BG-DT-01" in items and "04BG-RC-01" in items
    assert "102MG-ST-01" in items
    louta = [gap for gap in ms.gaps if gap[0] == "LOUTA"]
    reasons = " | ".join(gap[2] for gap in louta)
    assert "MODELLING_GAP[MISSING_ROUTE_RATE_REACH]" in reasons
    assert not any("110/120LG" in gap[1] or "130LG-ST-01" in gap[1]
                   for gap in louta)


# ---- palette --------------------------------------------------------------------
def test_grade_colors_deterministic_and_distinct():
    assert grade_color("TBT") == "#1f7a8c"
    assert grade_color("BT") == "#d98b2b"
    assert grade_color("MT") == "#6a3fa0"
    assert grade_color("") == grade_color(None) == "#b8c2cb"
    # unknown grades: stable, repeatable fallback
    a = grade_color("Q-FUTURE-77")
    assert a == grade_color("Q-FUTURE-77")
    assert a.startswith("#") and len(a) == 7
    assert grade_color("Q-A") == grade_color("Q-A")


# ---- pile visual semantics ---------------------------------------------------------
def test_empty_and_designated_piles(s3):
    ms = build_monitor_state(s3)
    hp07 = next(p for sv in ms.sites.values() for p in sv.piles
                if p.id == "HP-07")
    assert hp07.mass_t <= 1
    assert hp07.grade == ""                       # empty -> neutral fill
    assert "designated" in hp07.grade_label
    assert hp07.designated_grade in ("BT", "TBT")


def test_fill_percentage_clamped(s3):
    sc = copy.deepcopy(s3)
    sc.space("HP-01").mass_t = sc.space("HP-01").capacity_t * 3  # corrupt input
    ms = build_monitor_state(sc)
    p = next(p for sv in ms.sites.values() for p in sv.piles if p.id == "HP-01")
    assert p.fill_pct == 100.0


def test_safety_stock_warning(s3):
    sc = copy.deepcopy(s3)
    sc.space("HP-02").mass_t = 1_000        # safety stock is 15,000
    ms = build_monitor_state(sc)
    p = next(p for sv in ms.sites.values() for p in sv.piles if p.id == "HP-02")
    assert p.below_safety
    assert any("below safety" in w for w in p.warnings)


def test_hard_vs_soft_offspec(s3):
    sc = copy.deepcopy(s3)
    # soft spec violated -> NOT critical
    ms = build_monitor_state(sc)
    p = next(p for sv in ms.sites.values() for p in sv.piles if p.id == "HP-02")
    assert p.offspec_hard == []              # BT Cd 30 > 28 but spec is soft
    # make the Cd limit hard -> pile flags a hard violation
    for d in sc.destinations:
        for s_ in d.spec:
            if s_.param == "Cd":
                s_.hard = True
    ms2 = build_monitor_state(sc)
    p2 = next(p for sv in ms2.sites.values() for p in sv.piles
              if p.id == "HP-02")
    assert any("Cd" in v for v in p2.offspec_hard)


def test_reachability_from_config_not_proximity(s3):
    ms = build_monitor_state(s3)
    lt1 = next(m for sv in ms.sites.values() for m in sv.machines
               if m.id == "140LG-RC-01")
    lt2 = next(m for sv in ms.sites.values() for m in sv.machines
               if m.id == "140LG-RC-02")
    # Conservative S0 reach pending the product-yard GA: one reclaimer per
    # assumed product lane, never proximity-derived cross-reach.
    assert set(lt1.reachable) == {"LT-P1"}
    assert set(lt2.reachable) == {"LT-P2"}
    rc = next(m for sv in ms.sites.values() for m in sv.machines
              if m.id == "RC01-A")
    # bay-adjacency reach (PFD-0001[3] + project-team rule, 25-Jul-2026):
    # RC01-A translates on M16A-A-T11 between bays 1 and 2 only
    assert set(rc.reachable) == {"HP-01", "HP-05", "HP-02", "HP-06"}


# ---- dynamics -------------------------------------------------------------------------
def test_plan_bucket_drives_machine_and_flows(s3, s3_plan):
    j = next(j for j in s3_plan.jobs if j.kind == "reclaim")
    ms = build_monitor_state(s3, plan=s3_plan, basis="plan",
                             bucket=j.start_bucket)
    m = next(m for sv in ms.sites.values() for m in sv.machines
             if m.id == j.machine)
    assert m.status == "Reclaiming"
    assert m.target_space == j.space
    assert m.rate_tph == pytest.approx(j.rate_tph)
    p = next(p for sv in ms.sites.values() for p in sv.piles
             if p.id == j.space)
    assert p.outbound_tph >= j.rate_tph - 1e-6
    assert p.active


def test_simulation_updates_mass_and_quality(s3, s3_plan, s3_sim):
    ms0 = build_monitor_state(s3, s3_plan, s3_sim, basis="sim", bucket=0)
    ms8 = build_monitor_state(s3, s3_plan, s3_sim, basis="sim", bucket=8)
    m0 = {p.id: p.mass_t for sv in ms0.sites.values() for p in sv.piles}
    m8 = {p.id: p.mass_t for sv in ms8.sites.values() for p in sv.piles}
    assert m0 != m8, "simulated bucket change must change displayed masses"
    changed = [pid for pid in m0 if abs(m0[pid] - m8[pid]) > 100]
    assert changed, "at least one pile must have moved material"
    q8 = {p.id: p.quality for sv in ms8.sites.values() for p in sv.piles}
    assert any("BPL" in q for q in q8.values())
    # view is read-only: underlying scenario untouched
    assert s3.space("HP-01").mass_t == 80_000


def test_bucket_view_does_not_mutate_scenario(s3, s3_plan):
    before = {sp.id: sp.mass_t for sp in s3.spaces}
    for b in (0, 5, 12, 23):
        build_monitor_state(s3, plan=s3_plan, basis="plan", bucket=b)
    assert {sp.id: sp.mass_t for sp in s3.spaces} == before


# ---- scenario states ---------------------------------------------------------------------
def test_s1_empty_state():
    ms = build_monitor_state(datasets.sulphur_minimal())
    assert ms.sites == {}
    assert "No phosphate stockyards" in ms.message


def test_s2_m16_only():
    ms = build_monitor_state(datasets.mph_phosphate())
    assert list(ms.sites) == ["MPH"]
    assert ms.message is None
    # S2's only delayed link is the pseudo-corridor stand-in from an MPH
    # source node — no mine site may appear as a corridor origin
    assert all(c.from_site == "MPH" for c in ms.corridors)


def test_s3_three_sites(s3):
    ms = build_monitor_state(s3)
    assert list(ms.sites) == ["LOUTA", "BENGUERIR", "MPH"]
    assert {c.id for c in ms.corridors} == {"C1", "C2"}
    assert all(c.transit_h > 0 for c in ms.corridors)
    corridors = {c.id: c for c in ms.corridors}
    assert corridors["C1"].from_site == "LOUTA"
    assert corridors["C2"].from_site == "BENGUERIR"
    assert corridors["C1"].transit_h < corridors["C2"].transit_h
    louta_outlets = " ".join(label for label, _, _
                             in ms.sites["LOUTA"].outlets)
    benguerir_outlets = " ".join(label for label, _, _
                                 in ms.sites["BENGUERIR"].outlets)
    assert "transit time = 2 h" in louta_outlets
    assert "transit time = 4 h" in benguerir_outlets
    for stale_word in ("provisional", "nearer", "farther"):
        assert stale_word not in (louta_outlets + benguerir_outlets).lower()
    # schematic labelling for the mine yards
    assert "NOT TO SCALE" in ms.sites["LOUTA"].schematic_note
    assert "NOT TO SCALE" in ms.sites["BENGUERIR"].schematic_note
    assert "TBC" in ms.sites["LOUTA"].banner
    assert "TBC" in ms.sites["BENGUERIR"].banner


# ---- figures -----------------------------------------------------------------------------
def test_site_figures_render(s3, s3_plan, s3_sim):
    ms = build_monitor_state(s3, s3_plan, s3_sim, basis="sim", bucket=5)
    for sv in ms.sites.values():
        fig = site_figure(sv, show_flows=True, show_safety=True,
                          show_evidence=True)
        assert len(fig.layout.shapes) >= len(sv.piles)      # lanes drawn
        texts = " ".join(str(a.text) for a in fig.layout.annotations if a.text)
        for p in sv.piles:
            assert p.id in texts, f"{p.id} label missing"
        for m in sv.machines:
            assert m.id in texts, f"{m.id} label missing"
        assert sv.schematic_note in texts


def test_headless_page_renders_monitor():
    from streamlit.testing.v1 import AppTest
    from conftest import nav_to
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
    at.run()
    nav_to(at, "3 · Stockyards & Inventory")
    assert not at.exception
    body = " ".join(str(m.value) for m in at.markdown)
    assert "MPH M16 phosphate hub" in body          # S3 includes the hub


# ---- determinism guard ----------------------------------------------------------------------
def test_monitor_does_not_change_plan_signature(s3):
    res = solve_plan(datasets.end_to_end(), SolveOptions(max_time_s=3))
    assert res.status in {"OPTIMAL", "FEASIBLE"}
    assert res.breakdown["unmet_t"] == 0
    assert res.breakdown["spill_t"] == 0
    assert sum(res.surge_unstacked) == 0
