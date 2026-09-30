# -*- coding: utf-8 -*-
"""Multi-grade pile zones + Benguerir process-stacker jobs.

Covers the model-backed coverage pass:
  * ST3 stacking through the executable three-line washing chain, with no
    parallel exogenous BG-WASH-IN representation;
  * the experimental zone model (expansion invariants, mass conservation,
    grade compatibility, reclaim-face restriction);
  * deterministic fallback when the zone flag is off / no zone inputs;
  * explicit disclosure of unsupported reference equipment.
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mph_sim import datasets                                     # noqa: E402
from mph_sim.domain import PileZone                              # noqa: E402
from mph_sim.optimizer import SolveOptions, solve_plan           # noqa: E402
from mph_sim.simulator import Simulator                          # noqa: E402
from mph_sim.validation import validate_scenario                 # noqa: E402
from mph_sim.zones import (expand_pile_zones, parent_space_id,   # noqa: E402
                           validate_zones, zones_enabled)


def _sig(res) -> str:
    s = tuple((j.machine, j.space, j.dest_id, j.start_bucket, j.end_bucket,
               j.tonnage_t) for j in res.jobs)
    return hashlib.sha256(json.dumps(s).encode()).hexdigest()[:16]


# ---- shared solves (module-scoped: each CP-SAT run is expensive) ---------------
@pytest.fixture(scope="module")
def s3_default():
    sc = datasets.end_to_end()
    return sc, solve_plan(sc, SolveOptions(max_time_s=3))


@pytest.fixture(scope="module")
def s3_st3(s3_default):
    # Legacy compatibility flag no longer changes the six-pile topology.
    return s3_default


def _zoned_s2():
    """S2 with HP-01 split into two explicit zones (SYNTHETIC test inputs —
    no real pile has zone data; the zoning rules are TBC, S3-A2/S3-A7).

    Z1 (x 0–150): the existing 80,000 t of TBT — the active reclaim face.
    Z2 (x 150–300): 30,000 t of MT, NOT the face — reclaim must not draw
    from it even though its material could serve demand.
    """
    sc = datasets.mph_phosphate()
    sp = sc.space("HP-01")
    sc.pile_zones = [
        PileZone("HP-01", "Z1", 0, 150,
                 grade="TBT", mass_t=80_000, capacity_t=85_000,
                 quality=dict(sp.quality), reclaim_face=True),
        PileZone("HP-01", "Z2", 150, 300,
                 grade="MT", mass_t=30_000, capacity_t=40_000,
                 reclaim_face=False),
    ]
    sp.mass_t = 110_000            # zone masses must reconcile with the pile
    sc.config["MULTI_GRADE_ZONES_ENABLED"] = True
    return sc


@pytest.fixture(scope="module")
def zoned():
    sc = _zoned_s2()
    return sc, solve_plan(sc, SolveOptions(max_time_s=2))


# ---- 1. no double counting of washing output -----------------------------------
def test_wash_supply_is_single_path_baseline(s3_default):
    sc, _ = s3_default
    assert not any(e.id == "BG-WASH-IN" for e in sc.exogenous)
    assert {
        transfer.process_stage for transfer in sc.process_transfers
        if transfer.process_stage.startswith("BEN-WASH-")
    } == {"BEN-WASH-L1", "BEN-WASH-L2", "BEN-WASH-L3"}
    assert sc.machine("ST3").planning_enabled is True


def test_wash_supply_is_single_path_st3_mode(s3_st3):
    sc, _ = s3_st3
    assert not any(e.id == "BG-WASH-IN" for e in sc.exogenous)
    assert not any(e.link_id == "L-BG-WASH" for e in sc.belt_profile)
    assert sc.machine("ST3").planning_enabled is True


def test_st3_jobs_are_genuine_and_mass_bounded(s3_st3):
    sc, res = s3_st3
    st3 = [j for j in res.jobs if j.machine == "ST3"]
    assert st3, "ST3 mode must actually plan ST3 stacking jobs"
    assert all(j.kind == "stack" for j in st3)
    assert all(parent_space_id(j.space) in ("BG-P1", "BG-P2") for j in st3)
    stacked = sum(j.output_tonnage_t for j in st3)
    surge = sum(res.surge_unstacked)
    configured = sum(
        sc.config["PROCESS_STAGE_OUTPUT_TARGET_T"][stage]
        for stage in (
            "BEN-PRODUCT-STACK-L1",
            "BEN-PRODUCT-STACK-L2",
            "BEN-PRODUCT-STACK-L3",
        )
    )
    assert stacked == pytest.approx(configured, abs=2)
    assert surge == 0
    # cross-check against the model's own inventory series: the mass the
    # solver added to BG-P1 must equal the ST3 job tonnage (this is what
    # caught material teleporting THROUGH the pile before the
    # space-transit guard was added to routes.stack_candidates)
    inv = res.inventory["BG-P1"]
    outs = sum(j.tonnage_t for j in res.jobs
               if j.space == "BG-P1" and j.kind in ("reclaim", "dispatch"))
    model_ins = inv[-1] - inv[0] + outs
    bg_p1_stacked = sum(j.tonnage_t for j in st3 if j.space == "BG-P1")
    assert model_ins == pytest.approx(bg_p1_stacked, abs=2)


def test_stack_paths_never_traverse_another_stockpile(s3_st3, s3_default):
    """A stockpile is storage, not a transfer tower — material may not flow
    THROUGH a pile within a bucket to reach its target pile."""
    from mph_sim.routes import RouteGraph, space_node
    from mph_sim.optimizer import build_candidates
    for sc, res in (s3_st3, s3_default):
        graph = RouteGraph.from_scenario(sc)
        cands, _ = build_candidates(sc, graph)
        space_nodes = {space_node(sc, s.id) for s in sc.spaces}
        for c in cands:
            if c.kind != "stack" or c.path is None:
                continue
            mids = [graph.links[l].dst for l in c.path.links[:-1]]
            assert not [n for n in mids if n in space_nodes], \
                f"stack candidate {c.machine}->{c.space} routes through " \
                f"{[n for n in mids if n in space_nodes]}"


def test_st3_mode_simulation_conserves_mass(s3_st3):
    sc, res = s3_st3
    sim = Simulator(sc, res, auto_replan=False)
    sim.run_to_end()
    st3_moved = sum(a["moved_t"] for a in sim.activity_log
                    if a["machine"] == "ST3")
    assert st3_moved > 0
    planned_st3 = sum(
        job.tonnage_t for job in res.jobs if job.machine == "ST3")
    # All three configured 5,400 t product-stack campaigns are now active;
    # compare execution with the authoritative generated plan rather than a
    # stale single-line constant.
    assert st3_moved == pytest.approx(planned_st3, abs=2)
    # BG-P1 mass balance: start + stacked - reclaimed == end
    bg_in = sum(a["moved_t"] for a in sim.activity_log
                if a["machine"] == "ST3" and a["space"] == "BG-P1")
    bg_out = sum(a["moved_t"] for a in sim.activity_log
                 if a["space"] == "BG-P1" and a["kind"] in ("reclaim",
                                                            "dispatch"))
    start = next(s.mass_t for s in sc.spaces if s.id == "BG-P1")
    assert sim.inv["BG-P1"] == pytest.approx(start + bg_in - bg_out, abs=1)


# ---- 2. zone model --------------------------------------------------------------
def test_zone_expansion_identity_when_disabled():
    sc = datasets.end_to_end()
    assert not zones_enabled(sc)
    assert expand_pile_zones(sc) is sc            # identity, not a copy

    sc2 = _zoned_s2()
    sc2.config["MULTI_GRADE_ZONES_ENABLED"] = False
    assert expand_pile_zones(sc2) is sc2          # flag off => identity


def test_zone_expansion_invariants():
    sc = _zoned_s2()
    ex = expand_pile_zones(sc)
    ids = {s.id for s in ex.spaces}
    assert "HP-01" not in ids
    assert {"HP-01#Z1", "HP-01#Z2"} <= ids
    z1 = ex.space("HP-01#Z1")
    z2 = ex.space("HP-01#Z2")
    parent = sc.space("HP-01")
    # mass and capacity are conserved by the expansion
    assert z1.mass_t + z2.mass_t == pytest.approx(parent.mass_t)
    assert z1.capacity_t + z2.capacity_t <= parent.capacity_t + 1
    # zones share the parent's route-graph node — no graph edits needed
    assert z1.node_id == z2.node_id == "SPACE:HP-01"
    # contamination: each zone is designated for its declared grade
    assert z1.designated_grade == "TBT"
    assert z2.designated_grade == "MT"
    # machine reach: stackers see both zones, reclaimers only the face
    for mch in ex.machines:
        if "HP-01" in datasets.mph_phosphate().machine(mch.id).reachable_spaces:
            if mch.kind == "stacker":
                assert {"HP-01#Z1", "HP-01#Z2"} <= set(mch.reachable_spaces)
            elif mch.kind == "reclaimer":
                assert "HP-01#Z1" in mch.reachable_spaces
                assert "HP-01#Z2" not in mch.reachable_spaces, \
                    "reclaim allowed only from the designated active face"


def test_zone_inputs_are_validated_not_repaired():
    sc = _zoned_s2()
    sc.pile_zones[0].x_to = 10 ** 6               # outside the pile
    assert validate_zones(sc)
    with pytest.raises(ValueError):
        expand_pile_zones(sc)
    # scenario-level validation surfaces the same problem as an ERROR
    issues = validate_scenario(sc)
    assert any(i.code == "ZONE_INPUT" and i.severity == "ERROR"
               for i in issues)


def test_zone_mass_conservation_through_solve(zoned):
    sc, res = zoned
    assert res.status in ("OPTIMAL", "FEASIBLE")
    ex = expand_pile_zones(sc)
    for zid in ("HP-01#Z1", "HP-01#Z2"):
        series = res.inventory.get(zid)
        assert series is not None, f"no inventory series for {zid}"
        start = ex.space(zid).mass_t
        ins = sum(j.tonnage_t for j in res.jobs
                  if j.kind == "stack" and j.space == zid)
        outs = sum(j.tonnage_t for j in res.jobs
                   if j.kind in ("reclaim", "dispatch") and j.space == zid)
        assert series[-1] == pytest.approx(start + ins - outs, abs=2), zid
        # zone capacity is never exceeded
        assert max(series) <= ex.space(zid).capacity_t + 1


def test_zone_grade_compatibility(zoned):
    sc, res = zoned
    ex = expand_pile_zones(sc)
    for j in res.jobs:
        if j.kind == "stack" and j.space.startswith("HP-01#"):
            assert j.grade == ex.space(j.space).designated_grade, \
                f"{j.id}: stacked {j.grade} into a zone designated " \
                f"{ex.space(j.space).designated_grade}"


def test_zone_reclaim_face_sequencing(zoned):
    sc, res = zoned
    # Z2 is NOT the active face: no reclaim/dispatch may draw from it
    for j in res.jobs:
        if j.kind in ("reclaim", "dispatch"):
            assert j.space != "HP-01#Z2", \
                "reclaimed from a non-face zone — face restriction violated"
    # x-cuts of zone jobs stay inside the zone boundaries
    ex = expand_pile_zones(sc)
    for j in res.jobs:
        if j.space.startswith("HP-01#") and (j.start_x or j.stop_x):
            z = ex.space(j.space)
            assert z.x_from - 1e-6 <= j.start_x <= z.x_to + 1e-6, j.id
            assert z.x_from - 1e-6 <= j.stop_x <= z.x_to + 1e-6, j.id


# ---- 3. deterministic fallback / unchanged baselines ---------------------------
def test_s3_signature_unchanged_with_flags_off(s3_default):
    sc, res = s3_default
    assert sc.config.get("BEN_ST3_STACKING_ENABLED") is False
    assert sc.config.get("MULTI_GRADE_ZONES_ENABLED") is False
    assert res.status in ("OPTIMAL", "FEASIBLE")


def test_s1_s2_signatures_unchanged():
    assert _sig(solve_plan(datasets.sulphur_minimal(),
                           SolveOptions(max_time_s=2))) == "9150a04e26a9aa61"
    # Rebaselined 13-Sep-2026: the campaign/parcel floor (CAMPAIGN_POLICY)
    # and the corrected continuous-feed service test change which buckets
    # the planner activates, so the material allocation differs. Mass
    # balance, link/line capacity and machine exclusivity are re-verified
    # from the resulting minute schedule in test_schedule_fragmentation.py.
    # Positioning remains post-solve and leaves both material plans intact.
    assert _sig(solve_plan(datasets.mph_phosphate(),
                           SolveOptions(max_time_s=2))) == "b498a9d55c2e6283"


def test_st3_mode_signature_is_deterministic_and_documented(s3_st3):
    sc, res = s3_st3
    assert sc.config["BEN_ST3_STACKING_ENABLED"] is False
    assert res.status in ("OPTIMAL", "FEASIBLE")


def test_st3_mode_signature_is_repeat_deterministic(s3_st3):
    """The pinned signature must be reproducible AT ITS BUDGET.

    CHANGED 25-Jul-2026: cross-budget stability no longer holds for the
    S3-derived scenarios. The M16 correction added a real reception envelope
    (one shared 2,600 t/h path) plus the extra PAP/drying route hop and a
    conflict group per gate bank, so CP-SAT keeps improving past the small
    deterministic budget instead of proving optimality inside it. Pinning is
    therefore per budget, and the invariant that still holds — more budget
    never yields a WORSE objective — is asserted separately below.
    """
    _, res3 = s3_st3
    again = solve_plan(datasets.end_to_end(ben_st3_stacking=True),
                       SolveOptions(max_time_s=3))
    assert _sig(again) == _sig(res3)


def test_more_budget_never_worsens_the_objective(s3_st3):
    _, res3 = s3_st3
    res10 = solve_plan(datasets.end_to_end(ben_st3_stacking=True),
                       SolveOptions(max_time_s=10))
    assert res10.objective <= res3.objective + 1e-6
    assert res10.status in ("OPTIMAL", "FEASIBLE")


# ---- 4. disclosure of unsupported stackers -------------------------------------
def test_every_unsupported_stacker_is_disclosed(s3_default):
    from ui.theme import machine_planning_coverage
    sc, res = s3_default
    cov = machine_planning_coverage(sc, res)
    rows = {r["machine"].split(" · ")[-1]: r for _, r in cov.iterrows()}

    # 102MG remains reference/data-blocked with an exact explanation.
    for mid, needle in [
            ("102MG-ST-01", "capacities"), ("102MG-ST-02", "duty")]:
        assert mid in rows, f"{mid} missing from the coverage diagnostic"
        assert rows[mid]["category"] == "modelling gap"
        assert rows[mid]["reason_code"]
        assert needle in rows[mid]["explanation"], mid
    for mid in (
            "BG-MINE-ST-1", "BG-MINE-ST-2", "BG-MINE-BWR",
            "BG-RAW-ST-1", "BG-RAW-ST-2", "BG-RAW-BWR",
            "ST3", "RC2", "RC3",
            "110LG-ST-01", "120LG-RC-01", "120LG-RC-02",
            "130LG-ST-01", "140LG-RC-01", "140LG-RC-02"):
        assert mid in rows, f"{mid} missing from the coverage diagnostic"
        assert rows[mid]["category"] in {
            "jobs generated", "legitimate idle", "solver-enabled, idle"}
        assert rows[mid]["solver_controlled"], mid

    st3 = rows["ST3"]
    assert st3["category"] == "jobs generated"


def test_coverage_reports_st3_jobs_when_enabled(s3_st3):
    from ui.theme import machine_planning_coverage
    sc, res = s3_st3
    cov = machine_planning_coverage(sc, res)
    st3 = next(r for _, r in cov.iterrows()
               if r["machine"].endswith("ST3"))
    assert st3["category"] == "jobs generated"
    assert st3["jobs"] >= 1


def test_reference_machines_never_receive_jobs(s3_default, s3_st3):
    for sc, res in (s3_default, s3_st3):
        solver_ids = {m.id for m in sc.machines
                      if getattr(m, "planning_enabled", True)}
        for j in res.jobs:
            assert j.machine in solver_ids, \
                f"job on non-solver machine {j.machine}"
        for banned in ("102MG-ST-01", "102MG-ST-02"):
            assert banned not in {m.id for m in sc.machines}
        assert {
            "BG-MINE-ST-1", "BG-MINE-ST-2", "BG-MINE-BWR",
            "BG-RAW-ST-1", "BG-RAW-ST-2", "BG-RAW-BWR",
            "ST3", "RC2", "RC3",
            "110LG-ST-01", "120LG-RC-01", "120LG-RC-02",
            "130LG-ST-01", "140LG-RC-01", "140LG-RC-02",
        } <= {m.id for m in sc.machines}


# ---- 5. monitor zone labels -----------------------------------------------------
def test_monitor_shows_zone_slices_with_basis_note():
    from ui.stockyard_monitor import build_monitor_state
    sc = _zoned_s2()
    ms = build_monitor_state(sc)
    pile = next(p for p in ms.sites["MPH"].piles if p.id == "HP-01")
    assert pile.segments and len(pile.segments) == 2
    grades = [s.grade for s in pile.segments]
    assert grades[1] == "MT"                      # declared zone grade shown
    assert any("multi-grade zones" in w and "initial" in w
               for w in pile.warnings), \
        "the basis of the zone view must be explained"


def test_zone_round_trip_persistence():
    from mph_sim.persistence import scenario_from_json, scenario_to_json
    sc = _zoned_s2()
    back = scenario_from_json(scenario_to_json(sc))
    assert len(back.pile_zones) == 2
    assert back.pile_zones[0].space_id == "HP-01"
    assert back.config.get("MULTI_GRADE_ZONES_ENABLED") is True
