# -*- coding: utf-8 -*-
"""Benguerir B02 correction-pass acceptance tests (19-Jul-2026).

Authoritative source: Q80130-BEN-B02-ME-BOD-0001 Rev B, IFR, 05-Feb-2025,
§§2.3.1–2.3.3 and §2.6 (Benguerir Beneficiation Expansion — mechanical BoD).
"""
import copy
import glob
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mph_sim import datasets                                     # noqa: E402
from mph_sim.domain import PlanResult, Scenario                  # noqa: E402
from mph_sim.optimizer import SolveOptions, solve_plan           # noqa: E402
from mph_sim.routes import RouteGraph                            # noqa: E402
from mph_sim.simulator import Simulator                          # noqa: E402
from ui.layout_geometry import geometry_for                      # noqa: E402
from ui.stockyard_monitor import build_monitor_state             # noqa: E402

B02_ID = "Q80130-BEN-B02-ME-BOD-0001"
B02_CITE = ("Q80130-BEN-B02-ME-BOD-0001 Rev B, IFR, 05-Feb-2025, "
            "§§2.3.1–2.3.3 and §2.6")


@pytest.fixture(scope="module")
def s3():
    return datasets.end_to_end()


@pytest.fixture(scope="module")
def s3_plan(s3):
    return solve_plan(s3, SolveOptions(max_time_s=3))


def _bg_entities(sc):
    ms = [m for m in sc.machines if m.site == "BENGUERIR"]
    sp = [s for s in sc.spaces if s.site == "BENGUERIR"]
    ls = [l for l in sc.links if l.id.startswith(("L-BG", "C2"))]
    return ms, sp, ls


# ---- 1. exact document identity, no unreferenced datasheet as source ----------
def test_b02_document_identity_cited(s3):
    items = [
        *(s3.machine(mid) for mid in ("ST3", "RC2", "RC3")),
        *(s3.space(pid) for pid in ("BG-P1", "BG-P2")),
        *(s3.link(lid) for lid in (
            "L-BG-P1-OUT", "L-BG-P2-OUT", "L-BG-PROD-C2",
            "L-BG-TRAIN-PROD", "L-BG-TRAIN-RAW")),
    ]
    for it in items:
        assert B02_ID in it.evidence.source, \
            f"{getattr(it, 'id', it)}: Benguerir evidence must cite the B02 BoD"
    site = next(s for s in s3.sites if s.id == "BENGUERIR")
    assert B02_CITE in site.evidence.source
    # the old "doc ref TBC" datasheet survives ONLY as a named historical
    # conflict (autonomy / 3-qualities), never as a primary source
    import inspect
    src = inspect.getsource(datasets)
    assert "_BGDS" not in src


# ---- 2. no unqualified 10-day autonomy claim ----------------------------------
def test_no_unqualified_ten_day_autonomy():
    for path in glob.glob(str(ROOT / "**" / "*.py"), recursive=True) + \
            glob.glob(str(ROOT / "*.md")):
        if "__pycache__" in path or "test_benguerir_b02" in path:
            continue
        lines = open(path, encoding="utf-8").read().splitlines()
        for i, ln in enumerate(lines):
            if "10-day" in ln or "10 day" in ln.lower():
                ctx = " ".join(lines[max(0, i - 3):i + 4]).lower()
                assert "conflict" in ctx, \
                    f"{path}:{i + 1}: 10-day autonomy must be a NAMED conflict"


def test_combined_autonomy_arithmetic_and_display(s3):
    total = sum(s3.space(pid).capacity_t for pid in ("BG-P1", "BG-P2"))
    assert total == 383_024
    assert total / 2600 / 24 == pytest.approx(6.14, abs=0.01)
    assert total / 3120 / 24 == pytest.approx(5.12, abs=0.01)
    g = geometry_for(s3, "BENGUERIR")
    zl = " ".join(z.label for z in g.zones)
    assert "≈6.1 d nominal" in zl and "≈5.1 d design" in zl
    assert "5/6 d" in zl
    # 5-6 days is COMBINED-yard, never assigned per pile
    assert "5/6 d per pile" not in zl


# ---- 3. no "never merged" facility claim ---------------------------------------
def test_stages_connected_not_never_merged():
    for path in glob.glob(str(ROOT / "**" / "*.py"), recursive=True) + \
            glob.glob(str(ROOT / "*.md")):
        if "__pycache__" in path or "test_" in Path(path).name:
            continue
        t = open(path, encoding="utf-8").read()
        assert "never merged" not in t.lower(), path
        assert "NOT merged with cfg B" not in t, path
        assert "superseding design?" not in t, path
    import inspect
    from ui import layout_geometry
    assert "process stages connected" in " ".join(
        g[2] for g in layout_geometry._bg_gaps(True))


# ---- 4. two washed piles at exactly 191,512 t ----------------------------------
def test_two_washed_piles_exact_capacity(s3):
    bg = [s3.space(pid) for pid in ("BG-P1", "BG-P2")]
    assert len(bg) == 2
    assert all(sp.capacity_t == 191_512 for sp in bg)


# ---- 6/7. ONE twin-boom ST3; current-state lifecycle ----------------------------
def test_one_st3_twin_boom_not_two_stackers(s3):
    stackers = [m for m in s3.machines
                if m.site == "BENGUERIR" and m.kind == "stacker"]
    assert {m.id for m in stackers} == {
        "BG-MINE-ST-1", "BG-MINE-ST-2",
        "BG-RAW-ST-1", "BG-RAW-ST-2", "ST3",
    }
    for view in ("BENGUERIR", "CHAIN", "GLOBAL"):
        g = geometry_for(s3, view)
        st3 = [m for m in g.machines if m.machine_id == "ST3"]
        assert len(st3) == 1 and st3[0].twin_boom, view
        assert not any("BG-STACKER" in m.machine_id for m in g.machines)


def test_st3_rc2_rc3_current_configuration(s3):
    for mid in ("ST3", "RC2", "RC3"):
        m = s3.machine(mid)
        assert m.lifecycle == "EXISTING"
        assert "CURRENT CONFIGURATION" in m.evidence.note
        assert "source notation only" in m.evidence.note
        assert B02_ID in m.evidence.source


# ---- 8. non-baseline disabled sensitivity gets no solver jobs -------------------
def test_disabled_current_configuration_sensitivity_receives_no_jobs(s3_plan):
    sc = datasets.end_to_end(ben_b02_enabled=False)
    for mid in ("RC2", "RC3", "ST3"):
        assert sc.machine(mid).planning_enabled is False
    res = solve_plan(sc, SolveOptions(max_time_s=2))
    assert res.status in ("OPTIMAL", "FEASIBLE")
    assert not [j for j in res.jobs if j.machine in ("RC2", "RC3", "ST3")]
    assert [job for job in s3_plan.jobs if job.machine == "ST3"]


# ---- 9/10. display labels use documented physical tags --------------------------
def test_display_labels_are_documented_physical_tags(s3):
    from ui.labels import machine_display_label
    assert machine_display_label(s3, "ST3") == "BEN · ST3"
    assert machine_display_label(s3, "RC2") == "BEN · RC2"
    assert machine_display_label(s3, "RC3") == "BEN · RC3"
    for view in ("BENGUERIR", "CHAIN", "GLOBAL"):
        g = geometry_for(s3, view)
        labels = {m.machine_id: m.label for m in g.machines
                  if m.machine_id in ("ST3", "RC2", "RC3")}
        assert labels["ST3"].endswith("BEN · ST3")
        assert labels["RC2"].endswith("BEN · RC2")
        assert labels["RC3"].endswith("BEN · RC3")
        for m in g.machines:
            assert "BG-RC-0" not in m.label and "BG-STACKER" not in m.label


# ---- 11. raw and washed stages both render --------------------------------------
def test_raw_and_washed_stages_render(s3):
    for view in ("BENGUERIR", "CHAIN", "GLOBAL"):
        g = geometry_for(s3, view)
        raw = [p for p in g.piles if p.pile_id.startswith("BG-RAW")]
        assert len(raw) == 2, f"{view}: raw stage missing"
        assert all(p.modelled and not p.tbc for p in raw), \
            f"{view}: raw piles must bind to live scheduler spaces"
        assert {"BG-P1", "BG-P2"} <= {p.pile_id for p in g.piles}
        zl = " ".join(z.label for z in g.zones)
        # zone renamed 20-Jul-2026: "SCREENED-PRODUCT ORE STORAGE —
        # PRE-BENEFICIATION" (Table 20, USER_CONFIRMED) — no longer
        # contains the literal substring "RAW"
        # the compact CHAIN/GLOBAL band cannot fit the full detail-view
        # zone name; both forms start with the same stage identifier
        assert "SCREENED-PRODUCT ORE" in zl
        assert "WASHING" in zl.upper()


# ---- 12/13/14. distinct, material-aware, conditional routes ---------------------
def test_raw_washing_and_corridor_paths_distinct(s3):
    graph = RouteGraph.from_scenario(s3)
    # washed product -> C2 head via the product leg
    p = graph.best_path("SPACE:BG-P1", "TOWER:M16-REC")
    assert p is not None and "L-BG-PROD-C2" in p.links and "C2" in p.links
    assert "L-BG-RAW-C2" not in p.links
    # raw -> C2 is CONDITIONAL: closed by default
    assert s3.link("L-BG-RAW-C2").available is False
    assert graph.paths("SRC:BG-RAW", "TOWER:M16-REC") == []
    # enabling the prerequisite opens the raw path over the SAME corridor
    sc_raw = datasets.end_to_end(ben_raw_to_c2=True)
    graph_raw = RouteGraph.from_scenario(sc_raw)
    pr = graph_raw.best_path("SRC:BG-RAW", "TOWER:M16-REC")
    assert pr is not None and "L-BG-RAW-C2" in pr.links and "C2" in pr.links


def test_material_compatibility_raw_vs_washed(s3):
    assert s3.link("L-BG-RAW-C2").material_class == "raw"
    assert s3.link("L-BG-PROD-C2").material_class == "washed"
    # even with the raw route enabled, raw can never reach the PRODUCT train
    # route and washed can never reach the RAW train route (topology-enforced)
    sc_raw = datasets.end_to_end(ben_raw_to_c2=True)
    graph = RouteGraph.from_scenario(sc_raw)
    assert graph.paths("SRC:BG-RAW", "DEST:BG-TRAIN-PROD",
                       only_available=False) == []
    for sp in ("SPACE:BG-P1", "SPACE:BG-P2"):
        assert graph.paths(sp, "DEST:BG-TRAIN-RAW",
                           only_available=False) == []


# ---- 15. one physical corridor envelope -----------------------------------------
def test_c2_flow_respects_single_corridor_capacity(s3, s3_plan):
    for t in range(s3.sim.horizon_buckets):
        tot = sum(j.rate_tph for j in s3_plan.jobs
                  if j.kind == "dispatch" and j.dest_id == "C2"
                  and j.start_bucket <= t <= j.end_bucket)
        assert tot <= 2600 + 1e-6, f"bucket {t}: C2 at {tot} t/h"
    # nominal vs design kept separate — planning uses nominal
    assert s3.link("C2").capacity_tph == 2600
    assert s3.link("C2").capacity_design_tph == 3120
    assert s3.config.get("CAPACITY_ENVELOPE") == "nominal"


def test_nominal_and_design_capacities_stored(s3):
    assert s3.machine("ST3").rate_max_tph == 2100
    assert s3.machine("ST3").rate_design_tph == 2520
    for mid in ("RC2", "RC3"):
        assert s3.machine(mid).rate_max_tph == 2600
        assert s3.machine(mid).rate_design_tph == 3120
        # 500 t/h minimum is ASSUMED, never attributed to the BoD
        assert "ASSUMED" in s3.machine(mid).evidence.note
        assert "min stable 500" in s3.machine(mid).evidence.note.lower() \
            or "500 t/h ASSUMED" in s3.machine(mid).evidence.note
    for lid in ("L-BG-TRAIN-RAW", "L-BG-TRAIN-PROD"):
        assert s3.link(lid).capacity_tph == 1000
        assert s3.link(lid).capacity_design_tph == 1200


# ---- 16/17. train routes ---------------------------------------------------------
def test_raw_and_product_train_routes_exist(s3):
    lr = s3.link("L-BG-TRAIN-RAW")
    lp = s3.link("L-BG-TRAIN-PROD")
    assert lr.material_class == "raw" and lp.material_class == "washed"
    # ONE loading-station resource ASSUMED shared (configurable group)
    assert lr.shared_group == lp.shared_group == "BG-TRAIN-STATION"
    assert {d.id for d in s3.destinations} >= {"BG-TRAIN-RAW",
                                               "BG-TRAIN-PROD"}


def test_train_demand_is_visibly_a_demonstration_input(s3):
    for did in ("BG-TRAIN-RAW", "BG-TRAIN-PROD"):
        assert "demonstration" in s3.destination(did).evidence.note.lower()
    ms = build_monitor_state(s3)
    outlets = ms.sites["BENGUERIR"].outlets
    train = [o for o in outlets if "train" in o[0]]
    assert len(train) == 2
    assert all("editable demonstration demand" in o[2] for o in train)


# ---- 18. washing supply is an executable, non-duplicated chain -------------------
def test_bg_washing_supply_has_no_parallel_exogenous_inflow(s3):
    assert not [flow for flow in s3.exogenous if flow.id == "BG-WASH-IN"]
    assert {
        transfer.process_stage for transfer in s3.process_transfers
        if transfer.process_stage.startswith("BEN-WASH-")
    } == {"BEN-WASH-L1", "BEN-WASH-L2", "BEN-WASH-L3"}


# ---- 19. exogenous inflow conserves mass and blends quality ----------------------
def test_exogenous_inflow_mass_and_quality_conservation():
    from mph_sim.domain import (Evidence, ExogenousFlow, Grade, SimParams,
                                Site, StockSpace)
    sc = Scenario(name="MINI-EXO", quality_params=[])
    sc.sites = [Site("MPH", "hub", "hub")]
    sc.grades = [Grade("BTQ", "BT-like", {"BPL": 68.0, "Cd": 30.0}),
                 Grade("TBQ", "TBT-like", {"BPL": 74.0, "Cd": 22.0})]
    sc.spaces = [StockSpace("P1", "MPH", "M16-humid", "open", 100_000,
                            10_000, "BTQ", x_from=0, x_to=300)]
    sc.exogenous = [ExogenousFlow("IN1", target_space="P1", rate_tph=1000,
                                  grade="TBQ", source_space="",
                                  start_bucket=0, end_bucket=4)]
    sc.sim = SimParams(dt_h=1.0, horizon_buckets=6, seed=1)
    sim = Simulator(sc, PlanResult(status="FEASIBLE"), auto_replan=False)
    for _ in range(5):
        sim.step()
    # mass conservation: 10,000 + 5 × 1,000
    assert sim.inv["P1"] == pytest.approx(15_000)
    # mass-weighted quality blend (was silently unchanged before the fix)
    q = sim.space_quality["P1"]
    assert q["BPL"] == pytest.approx((10_000 * 68 + 5_000 * 74) / 15_000)
    assert q["Cd"] == pytest.approx((10_000 * 30 + 5_000 * 22) / 15_000)


def test_s3_bg_p1_mass_conservation_with_planned_stacking(s3, s3_plan):
    sim = Simulator(copy.deepcopy(s3), copy.deepcopy(s3_plan),
                    auto_replan=False)
    n = 6
    for _ in range(n):
        sim.step()
    out = sum(a["moved_t"] for a in sim.activity_log
              if a["space"] == "BG-P1" and a["kind"] in ("reclaim",
                                                         "dispatch"))
    inflow = sum(a["moved_t"] for a in sim.activity_log
                 if a["space"] == "BG-P1" and a["kind"] == "stack")
    exp = 150_000 + inflow - out
    assert sim.inv["BG-P1"] == pytest.approx(exp, abs=2.0)


# ---- 20. generic 12 % moisture flagged as placeholder ----------------------------
def test_generic_moisture_not_presented_as_benguerir_value(s3):
    site = next(s for s in s3.sites if s.id == "BENGUERIR")
    assert site.material_profiles["washed_filtered"]["moisture_pct"] == "23"
    assert site.material_profiles["settled_product"]["moisture_pct"] \
        == "15-20".replace("-", "–")
    assert site.material_profiles["raw"]["moisture_pct"] == "10–14"
    ms = build_monitor_state(s3)
    banner = ms.sites["BENGUERIR"].banner
    assert "23 %" in banner and "15–20 %" in banner
    assert "placeholder" in banner.lower() and "12 %" in banner
    sp = s3.space("BG-P1")
    assert "placeholder" in sp.evidence.note.lower()


# ---- 21/22. schematic discipline -------------------------------------------------
def test_geometry_schematic_and_no_confirmed_shared_rail(s3):
    for view in ("BENGUERIR", "CHAIN", "GLOBAL"):
        g = geometry_for(s3, view)
        assert g.not_to_scale
        if view == "GLOBAL":
            assert not any("GLOBAL STOCKYARD LAYOUT" in s.text
                           for s in g.stamps)
        else:
            assert any("NOT TO SCALE" in s.text for s in g.stamps), view
        rl1 = g.rail("BG-RL-P1")
        rl2 = g.rail("BG-RL-P2")
        assert rl1.flanks == ["BG-P1"] and rl2.flanks == ["BG-P2"], \
            "separate schematic reclaim lines — no shared rail as fact"
        assert rl1.tbc and rl2.tbc
        assert "TBC" in rl1.confidence and "TBC" in rl2.confidence
    # solver cross-reach stays an ASSUMPTION, stated in evidence
    for mid in ("RC2", "RC3"):
        assert "ASSUMED" in s3.machine(mid).evidence.note


# ---- 23. S1/S2 deterministic signatures unchanged --------------------------------
def test_s1_s2_signatures_unchanged_by_benguerir_pass():
    import hashlib
    import json

    def sig_of(sc, budget):
        res = solve_plan(sc, SolveOptions(max_time_s=budget))
        sig = tuple((j.machine, j.space, j.dest_id, j.start_bucket,
                     j.end_bucket, j.tonnage_t) for j in res.jobs)
        return hashlib.sha256(json.dumps(sig).encode()).hexdigest()[:16]

    assert sig_of(datasets.sulphur_minimal(), 2) == "9150a04e26a9aa61"
    # Minute positioning is post-solve and leaves this signature intact.
    assert sig_of(datasets.mph_phosphate(), 2) == "89f643675afb9983"


# ---- 25. mojibake sweep ----------------------------------------------------------
def test_no_mojibake_in_sources():
    pats = ["â€", "Ã©", "Ã¨", "Ã¢", "Ã‰", "�", "Â·", "âš "]
    files = (glob.glob(str(ROOT / "**" / "*.py"), recursive=True)
             + glob.glob(str(ROOT / "**" / "*.md"), recursive=True)
             + glob.glob(str(ROOT.parent / "report_sources" / "*.html"))
             + glob.glob(str(ROOT.parent / "report_sources" / "*.md"))
             + [str(ROOT.parent / "CLAUDE-CONTEXT.md")])
    for f in files:
        # self-exclude sweep-test files: they legitimately contain the
        # pattern strings themselves inside their own `pats` list
        if "__pycache__" in f or "test_benguerir_b02" in f or \
                "test_benguerir_raw_yard" in f:
            continue
        t = open(f, encoding="utf-8").read()
        for p in pats:
            assert p not in t, f"mojibake {p!r} in {f}"
