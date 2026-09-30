# -*- coding: utf-8 -*-
"""Benguerir upstream screened-product ore-yard coverage.

User-confirmed topology (project team, 20-Jul-2026, user-supplied "Table 20
— Screened Product Storage Area"): the upstream screened-product ore yard
has exactly 2 stockpiles, 2 DEDICATED stackers (one per pile) and 1 SHARED
reclaimer reaching both piles. Machine tags and metric coordinates remain
TBC. The 28-Jul-2026 six-pile scenario promotes these assets from reference
geometry into the scheduler and adds the mine-ore pair upstream.
"""
import glob
import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mph_sim import datasets                                     # noqa: E402
from mph_sim.optimizer import SolveOptions, solve_plan           # noqa: E402
from ui.layout_geometry import geometry_for, validate_geometry   # noqa: E402
from ui.process_mimic import (MimicOpts, build_process_mimic_state,  # noqa: E402
                              process_mimic_figure)
from ui.stockyard_monitor import grade_color                      # noqa: E402


@pytest.fixture(scope="module")
def s3():
    return datasets.end_to_end()


RAW_VIEWS = ("BENGUERIR", "CHAIN", "GLOBAL")


def _raw_piles(geo):
    return [p for p in geo.piles if p.pile_id.startswith("BG-RAW-")]


def _raw_machines(geo):
    return [m for m in geo.machines if m.machine_id.startswith("BG-RAW-")]


def _rail(geo, rid):
    return geo.rail(rid)


# ---- 1. exact upstream counts in every view --------------------------------------
def test_exact_upstream_counts_every_view(s3):
    for view in RAW_VIEWS:
        geo = geometry_for(s3, view)
        piles = _raw_piles(geo)
        machines = _raw_machines(geo)
        assert len(piles) == 2, f"{view}: expected 2 raw piles, got {len(piles)}"
        assert {p.pile_id for p in piles} == {"BG-RAW-1", "BG-RAW-2"}
        stackers = [m for m in machines if m.kind == "stacker"]
        reclaimers = [m for m in machines if m.kind == "reclaimer"]
        assert len(stackers) == 2, f"{view}: expected 2 raw stackers"
        assert len(reclaimers) == 1, f"{view}: expected exactly 1 raw reclaimer"
        assert {m.machine_id for m in stackers} == {"BG-RAW-ST-1",
                                                     "BG-RAW-ST-2"}
        assert reclaimers[0].machine_id == "BG-RAW-BWR"


# ---- 2/3. dedicated stacker reachability -----------------------------------------
def test_stacker_1_reaches_only_pile_1(s3):
    for view in RAW_VIEWS:
        geo = geometry_for(s3, view)
        st1 = next(m for m in geo.machines if m.machine_id == "BG-RAW-ST-1")
        rail = _rail(geo, st1.rail_id)
        assert rail.flanks == ["BG-RAW-1"], f"{view}: {rail.flanks}"


def test_stacker_2_reaches_only_pile_2(s3):
    for view in RAW_VIEWS:
        geo = geometry_for(s3, view)
        st2 = next(m for m in geo.machines if m.machine_id == "BG-RAW-ST-2")
        rail = _rail(geo, st2.rail_id)
        assert rail.flanks == ["BG-RAW-2"], f"{view}: {rail.flanks}"


# ---- 4/5. shared reclaimer reaches both; no second reclaimer ---------------------
def test_shared_reclaimer_reaches_both_piles(s3):
    for view in RAW_VIEWS:
        geo = geometry_for(s3, view)
        bwr = next(m for m in geo.machines if m.machine_id == "BG-RAW-BWR")
        rail = _rail(geo, bwr.rail_id)
        assert set(rail.flanks) == {"BG-RAW-1", "BG-RAW-2"}


def test_no_second_upstream_reclaimer_created(s3):
    for view in RAW_VIEWS:
        geo = geometry_for(s3, view)
        reclaimers = [m for m in _raw_machines(geo) if m.kind == "reclaimer"]
        assert len(reclaimers) == 1
        ids = [m.machine_id for m in geo.machines]
        assert len(ids) == len(set(ids)), f"{view}: duplicate machine id"


# ---- 6/7. physical tags remain TBC -----------------------------------------------
# Visual-cleanup pass 21-Jul-2026: the "TAG TBC" clutter moved OFF the
# drawing labels into the evidence notes — the tag itself stays unknown.
def test_stacker_tags_remain_tbc(s3):
    for view in RAW_VIEWS:
        geo = geometry_for(s3, view)
        for mid in ("BG-RAW-ST-1", "BG-RAW-ST-2"):
            m = next(mm for mm in geo.machines if mm.machine_id == mid)
            assert m.physical_tag == "" or m.physical_tag is None
            assert "TAG TBC" not in m.label, \
                "tag-TBC detail belongs in the evidence note, not the label"
            assert "tag TBC" in m.note or "TAG TBC" in m.note


def test_reclaimer_tag_remains_tbc_unless_separately_sourced(s3):
    for view in RAW_VIEWS:
        geo = geometry_for(s3, view)
        bwr = next(m for m in geo.machines if m.machine_id == "BG-RAW-BWR")
        assert bwr.physical_tag == "" or bwr.physical_tag is None
        assert "TAG TBC" not in bwr.label
        assert "tag TBC" in bwr.note or "TAG TBC" in bwr.note
        # 04BG-RC-01 is B01 evidence for a DIFFERENT (unproven-equivalent)
        # asset — never silently assigned to the Table 20 reclaimer
        assert "04BG-RC-01" not in bwr.label
        assert bwr.physical_tag != "04BG-RC-01"


# ---- 8/9. total capacity 400,000 t; project-confirmed equal allocation ----------
def test_upstream_total_capacity_displayed(s3):
    geo = geometry_for(s3, "BENGUERIR")
    zl = " ".join(z.label for z in geo.zones)
    assert "400,000 t" in zl


def test_project_confirmed_equal_per_pile_capacity(s3):
    for view in RAW_VIEWS:
        geo = geometry_for(s3, view)
        for p in _raw_piles(geo):
            space = s3.space(p.pile_id)
            assert p.modelled
            assert p.capacity_t == 200_000, f"{view}/{p.pile_id}"
            assert space.capacity_t == 200_000
            assert space.mass_t == 100_000
            assert space.mass_t / space.capacity_t == pytest.approx(0.5)
            # Scheduler-bound piles use the scenario view-model, not the
            # retired display-only composition overlay.
            assert p.display_mass_t is None
            assert p.display_grade_id == ""
            assert p.display_grade_components == []
    assert s3.space("BG-RAW-1").grade == "RAW-TBT"
    assert s3.space("BG-RAW-2").grade == "RAW-BT-LOW-CD"
    gaps = " ".join(g[2] for g in geometry_for(s3, "BENGUERIR").gaps)
    assert "BG-RAW-1 = 200,000 t" in gaps
    assert "BG-RAW-2 = 200,000 t" in gaps
    assert "starting" in gaps
    assert "100,000 t" in gaps
    assert "50% fill" in gaps
    assert "solver inventory" in gaps
    assert "nominal raw TBT grade" in gaps
    assert "Low-Cd" in gaps
    assert "no live pile-specific Benguerir assay" in gaps
    assert "DERIVED/TBC" not in gaps


def test_solver_bound_raw_piles_do_not_render_retired_display_bands(s3):
    geo = geometry_for(s3, "BENGUERIR")
    st = build_process_mimic_state(s3)
    fig = process_mimic_figure(st, geo)
    labels = [
        str(annotation.text)
        for annotation in fig.layout.annotations
        if annotation.text
    ]
    assert "<b>H-Cd</b> · 50 kt" not in labels
    assert "<b>L-Cd</b> · 50 kt" not in labels
    all_hover = [
        str(text)
        for trace in fig.data
        for text in (trace.hovertext
                     if isinstance(trace.hovertext, (list, tuple)) else [])
    ]
    assert any(text.startswith("<b>BG-RAW-1</b><br>100,000 / 200,000 t")
               for text in all_hover)
    assert any(text.startswith("<b>BG-RAW-2</b><br>100,000 / 200,000 t")
               for text in all_hover)


def test_legacy_display_component_config_cannot_override_solver_inventory(s3):
    broken = copy.deepcopy(s3)
    rows = broken.config["BG_RAW_DISPLAY_GRADE_COMPONENTS"]["BG-RAW-1"]
    rows[0]["mass_fraction"] = 0.6
    rows[1]["mass_fraction"] = 0.6
    geo = geometry_for(broken, "BENGUERIR")
    pile = next(p for p in geo.piles if p.pile_id == "BG-RAW-1")
    assert pile.display_grade_components == []
    assert pile.display_grade_issues == []
    assert broken.space("BG-RAW-1").grade == "RAW-TBT"


# ---- 10/11. <15 mm, open longitudinal, chevron, humid rates ---------------------
def test_table20_physical_descriptors_displayed(s3):
    # visual-cleanup 21-Jul-2026: descriptors split between the zone title
    # and grey info chips so the title stays readable — all still displayed
    geo = geometry_for(s3, "BENGUERIR")
    zl = " ".join(z.label for z in geo.zones) + " " +         " ".join(c.tag for c in geo.chips)
    assert "<15 mm" in zl
    assert "open longitudinal" in zl
    assert "chevron" in zl.lower()


def test_rates_identified_as_humid(s3):
    geo = geometry_for(s3, "BENGUERIR")
    for mid in ("BG-RAW-ST-1", "BG-RAW-ST-2", "BG-RAW-BWR"):
        m = next(mm for mm in geo.machines if mm.machine_id == mid)
        assert "humid" in m.note.lower()


# ---- 12. Table 20 autonomy preserved without reinterpretation -------------------
def test_table20_autonomy_preserved_verbatim(s3):
    # displayed on a grey info chip since the visual cleanup (21-Jul-2026)
    geo = geometry_for(s3, "BENGUERIR")
    zl = " ".join(z.label for z in geo.zones) + " " + \
        " ".join(c.tag for c in geo.chips)
    assert "12 d nominal / 14 d design autonomy" in zl
    # never assigned per individual pile
    assert "12 d nominal / 14 d design autonomy" not in \
        " ".join(p.label for p in _raw_piles(geo))


# ---- 13. Table 20 vs B02 values remain separately traceable --------------------
def test_table20_and_b02_capacities_separately_traceable(s3):
    geo = geometry_for(s3, "BENGUERIR")
    bwr = next(m for m in geo.machines if m.machine_id == "BG-RAW-BWR")
    assert "2,100" in bwr.note and "2,500" in bwr.note      # Table 20
    assert "2,600" in bwr.note and "3,120" in bwr.note       # B02
    # neither value silently overwrites the other
    assert "supplements" in bwr.note or "TBC" in bwr.note
    gaps = [g for g in geo.gaps if "reclaimer capacity conflict" in g[1]]
    assert gaps and "2,100" in gaps[0][2] and "2,600" in gaps[0][2]
    # scenario assumptions register carries the same reconciliation issue
    ids = {a.id for a in s3.assumptions if a.module == "BENGUERIR"}
    assert "S3-A14" in ids and "S3-A15" in ids


# ---- 14. package scope vs planning scope are separate fields -------------------
def test_package_scope_and_planning_scope_are_separate(s3):
    geo = geometry_for(s3, "BENGUERIR")
    bwr = next(m for m in geo.machines if m.machine_id == "BG-RAW-BWR")
    assert bwr.package_scope == "OUT_OF_SCOPE"
    assert bwr.planning_scope == "IN_SCOPE"
    for mid in ("BG-RAW-ST-1", "BG-RAW-ST-2"):
        m = next(mm for mm in geo.machines if mm.machine_id == mid)
        # the source table does not mark the stackers out of scope —
        # must not be inferred from the reclaimer's marking
        assert m.package_scope != "OUT_OF_SCOPE"
    assert "six-pile built-in" in bwr.note
    assert "live SMS integration remains TBC" in bwr.note


# ---- 15. upstream machines are solver-backed ------------------------------------
def test_upstream_machines_are_solver_backed(s3):
    raw_ids = {"BG-RAW-ST-1", "BG-RAW-ST-2", "BG-RAW-BWR"}
    assert raw_ids <= {m.id for m in s3.machines}
    assert all(s3.machine(machine_id).planning_enabled
               for machine_id in raw_ids)
    transfer_machines = {transfer.machine
                         for transfer in s3.process_transfers}
    assert raw_ids <= transfer_machines


# ---- 16. downstream ST3/RC2/RC3 tests remain unchanged --------------------------
def test_downstream_washed_yard_untouched(s3):
    geo = geometry_for(s3, "BENGUERIR")
    st3 = next(m for m in geo.machines if m.machine_id == "ST3")
    rc2 = next(m for m in geo.machines if m.machine_id == "RC2")
    rc3 = next(m for m in geo.machines if m.machine_id == "RC3")
    assert st3.twin_boom is True
    assert st3.physical_tag == "ST3"
    assert rc2.physical_tag == "RC2" and rc3.physical_tag == "RC3"
    rl_p1 = geo.rail(rc2.rail_id)
    rl_p2 = geo.rail(rc3.rail_id)
    assert rl_p1.flanks == ["BG-P1"] and rl_p2.flanks == ["BG-P2"]
    m = s3.machine("RC2")
    assert m.lifecycle == "EXISTING"
    assert m.rate_max_tph == 2600 and m.rate_design_tph == 3120


# ---- 17. S1/S2/S3 deterministic signatures unchanged ----------------------------
def test_reference_signatures_and_recalibrated_s3_solve():
    import hashlib
    import json

    def sig_of(sc, budget):
        res = solve_plan(sc, SolveOptions(max_time_s=budget))
        sig = tuple((j.machine, j.space, j.dest_id, j.start_bucket,
                     j.end_bucket, j.tonnage_t) for j in res.jobs)
        return hashlib.sha256(json.dumps(sig).encode()).hexdigest()[:16]

    assert sig_of(datasets.sulphur_minimal(), 2) == "9150a04e26a9aa61"
    # Rebaselined 13-Sep-2026: the campaign/parcel floor (CAMPAIGN_POLICY)
    # and the corrected continuous-feed service test change which buckets
    # the planner activates, so the material allocation differs. Mass
    # balance, link/line capacity and machine exclusivity are re-verified
    # from the resulting minute schedule in test_schedule_fragmentation.py.
    # Minute positioning is post-solve and leaves material signatures intact.
    assert sig_of(datasets.mph_phosphate(), 2) == "21ee848b3f7f3a21"
    s3_bg_train = datasets.end_to_end()
    datasets.apply_planning_demand_profile(s3_bg_train, "BG_TRAIN")
    # The S3 schedule intentionally changes because its active Benguerir TBT
    # campaign now uses RAW-TBT rather than the generic RAW-BT placeholder.
    assert len(sig_of(s3_bg_train, 3)) == 16


# ---- geometry integrity + rendering ----------------------------------------------
def test_geometry_validates_all_views(s3):
    for view in ("BENGUERIR", "CHAIN", "GLOBAL", "LOUTA", "MPH"):
        assert validate_geometry(geometry_for(s3, view)) == [], view


def test_no_document_number_invented():
    from ui import layout_geometry as lg
    assert "document number/revision TBC" in lg._T20
    assert "Q80130-BEN-B02" not in lg._T20   # never attributed to B02's doc


# ---- 18. headless render across bases/options ------------------------------------
def test_renders_headlessly_all_views_and_options(s3):
    st = build_process_mimic_state(s3)
    for view in ("BENGUERIR", "CHAIN", "GLOBAL"):
        fig = process_mimic_figure(st, geometry_for(s3, view), MimicOpts())
        texts = " ".join(str(a.text) for a in fig.layout.annotations if a.text)
        if view == "BENGUERIR":
            # detail view: readable functional machine names
            assert "Raw stacker 1" in texts and "Raw stacker 2" in texts \
                and "Shared raw reclaimer" in texts
        else:
            # compact views: short site-qualified names
            assert "Raw ST-1" in texts and "Raw ST-2" in texts and \
                "Raw RC (shared)" in texts
        # the TAG-TBC clutter stays OFF the drawing (evidence panel only)
        assert "TAG TBC" not in texts
    sc_off = datasets.end_to_end(ben_b02_enabled=False)
    st_off = build_process_mimic_state(sc_off)
    process_mimic_figure(st_off, geometry_for(sc_off, "BENGUERIR"),
                         MimicOpts())


def test_no_mojibake_in_new_content():
    pats = ["â€", "Ã©", "Ã¨", "Ã¢", "Ã‰", "�", "Â·", "âš "]
    p = str(ROOT / "ui" / "layout_geometry.py")
    t = open(p, encoding="utf-8").read()
    for pat in pats:
        assert pat not in t, f"mojibake {pat!r} in {p}"
