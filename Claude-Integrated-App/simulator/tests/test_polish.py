"""Visual/interpretability polish tests: S3 corridor-diagram geometry and
on-spec KPI semantics (incl. the belt-parcel zero-quality regression)."""
import copy
import hashlib
import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mph_sim import datasets                                   # noqa: E402
from mph_sim.domain import (Destination, Evidence, Grade, Job, Link, Node,
                            PlanResult, Scenario, Site, SimParams, SpecRange,
                            StockSpace)                        # noqa: E402
from mph_sim.kpis import compute_kpis                          # noqa: E402
from mph_sim.optimizer import SolveOptions, solve_plan         # noqa: E402
from mph_sim.simulator import Simulator                        # noqa: E402


# ====================== S3 corridor diagram geometry =========================
@pytest.fixture(scope="module")
def s3_fig():
    from ui.pages_overview import _value_chain_fig
    return _value_chain_fig(datasets.end_to_end(), None, "three-site", None)


def _corridor_arrows(fig):
    """Arrow annotations terminating at the hub's left edge."""
    return [a for a in fig.layout.annotations
            if getattr(a, "showarrow", False) and a.ax is not None
            and a.x < 1.5]          # hub is at x=1.7; dest arrows end at 2.88


def _corridor_labels(fig):
    return [a for a in fig.layout.annotations
            if a.text and ("C1" in str(a.text) or "C2" in str(a.text))
            and getattr(a, "bgcolor", None)]     # boxed labels only


def _pt_seg_dist(px, py, x0, y0, x1, y1):
    dx, dy = x1 - x0, y1 - y0
    if dx == dy == 0:
        return math.hypot(px - x0, py - y0)
    t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (x0 + t * dx), py - (y0 + t * dy))


def test_c1_c2_arrows_terminate_at_m16(s3_fig):
    arrows = _corridor_arrows(s3_fig)
    assert len(arrows) == 2, "exactly two corridor arrows expected"
    for a in arrows:
        # arrowhead (x,y) sits at the hub edge; tail (ax,ay) at a mine yard
        assert 0.9 <= a.x <= 1.15, f"arrow must end at M16 edge, got x={a.x}"
        assert abs(a.y) < 0.3, "arrowhead converges to the hub centreline"
        assert a.ax < a.x, "arrow must point from mine toward M16 (left→right)"
        assert abs(a.ay) > 0.7, "arrow tail must start at a mine yard"


def test_corridor_labels_off_the_shafts(s3_fig):
    arrows = _corridor_arrows(s3_fig)
    labels = _corridor_labels(s3_fig)
    assert len(labels) == 2, "two boxed corridor labels expected"
    for lab in labels:
        dmin = min(_pt_seg_dist(lab.x, lab.y, a.ax, a.ay, a.x, a.y)
                   for a in arrows)
        assert dmin >= 0.3, (f"label at ({lab.x},{lab.y}) sits too close to an "
                             f"arrow shaft (distance {dmin:.2f})")


def test_corridor_labels_distinct_vertical_positions(s3_fig):
    labels = _corridor_labels(s3_fig)
    ys = sorted(a.y for a in labels)
    assert abs(ys[1] - ys[0]) >= 1.0, "C1/C2 labels must not share a band"
    # one above, one below the horizontal hub axis
    assert ys[0] < 0 < ys[1]


def test_corridor_labels_content(s3_fig):
    for lab in _corridor_labels(s3_fig):
        txt = str(lab.text)
        assert "km" in txt and "t/h" in txt and "transit" in txt \
            and "in transit" in txt, f"incomplete corridor label: {txt}"


def test_louta_evidence_basis_visible_without_internal_tbc_shorthand(s3_fig):
    texts = " | ".join(str(a.text) for a in s3_fig.layout.annotations if a.text)
    assert "TBC" not in texts.upper()
    assert "LOUTA STOCKYARD · SMS" in texts
    assert "source yard · inv " in texts and "· planning basis" in texts, (
        "Louta planning-basis warning must stay visible in clean wording")
    # Benguerir status: project-team-confirmed current B02 table configuration
    assert "B02 table" in texts and "current configuration" in texts


# ====================== on-spec KPI calculation ==============================
def _mini_scenario(spec, bpl=70.0, cd=30.0) -> Scenario:
    sc = Scenario(name="mini", sites=[Site("X", "X", "hub")])
    sc.grades = [Grade("G", "G", {"BPL": bpl, "Cd": cd})]
    sc.spaces = [StockSpace("SP", "X", "M16-humid", "open", 10_000, 8_000, "G")]
    sc.nodes = [Node("SPACE:SP", "X", "space"), Node("DEST:D", "X", "dest")]
    sc.links = [Link("L", "SPACE:SP", "DEST:D", capacity_tph=1000)]
    sc.destinations = [Destination("D", "X", "DEST:D", demand_default_tph=500,
                                   spec=spec,
                                   evidence=Evidence(status="ASSUMED",
                                                     source="unit-test"))]
    sc.sim = SimParams(horizon_buckets=4)
    return sc


def _mini_plan(sc, delivered) -> PlanResult:
    """Plan with given delivered t/h per bucket, backed by matching jobs."""
    jobs = []
    for t, v in enumerate(delivered):
        if v > 0:
            jobs.append(Job(id=f"J{t}", machine="M", kind="reclaim",
                            space="SP", dest_id="D", grade="G", rate_tph=v,
                            tonnage_t=v, start_bucket=t, end_bucket=t))
    return PlanResult(status="FEASIBLE", jobs=jobs,
                      delivered={"D": list(delivered)},
                      inventory={"SP": [8000.0] * 5})


def test_onspec_ignores_zero_delivery_buckets():
    sc = _mini_scenario([SpecRange("BPL", pmin=71.0)])       # BPL 70 -> violates
    k = compute_kpis(sc, _mini_plan(sc, [0, 0, 500, 500]))
    row = k["quality_breakdown"][0]
    assert row["evaluated"] == 2, "zero-delivery buckets must not be evaluated"
    assert row["violations"] == 2
    assert k["onspec_pct"] == 0.0     # both delivered buckets violate


def test_onspec_min_limit():
    sc = _mini_scenario([SpecRange("BPL", pmin=71.0)], bpl=70.0)
    k = compute_kpis(sc, _mini_plan(sc, [500] * 4))
    row = k["quality_breakdown"][0]
    assert row["violations"] == 4
    assert row["avg_dev"] == pytest.approx(1.0)   # 71 - 70
    assert row["max_dev"] == pytest.approx(1.0)
    # compliant case
    sc2 = _mini_scenario([SpecRange("BPL", pmin=69.0)], bpl=70.0)
    k2 = compute_kpis(sc2, _mini_plan(sc2, [500] * 4))
    assert k2["onspec_pct"] == 100.0
    assert k2["onspec_principal"] is None


def test_onspec_max_limit():
    sc = _mini_scenario([SpecRange("Cd", pmax=28.0)], cd=30.0)
    k = compute_kpis(sc, _mini_plan(sc, [500] * 4))
    row = k["quality_breakdown"][0]
    assert row["violations"] == 4
    assert row["max_dev"] == pytest.approx(2.0)   # 30 - 28
    sc2 = _mini_scenario([SpecRange("Cd", pmax=31.0)], cd=30.0)
    k2 = compute_kpis(sc2, _mini_plan(sc2, [500] * 4))
    assert k2["onspec_pct"] == 100.0


def test_soft_violation_keeps_plan_feasible():
    """Impossible SOFT limit: plan still delivers, off-spec is reported."""
    sc = datasets.sulphur_minimal()
    sc.destinations[0].spec = [SpecRange("moisture", pmax=0.1, hard=False)]
    plan = solve_plan(sc, SolveOptions(max_time_s=3))
    assert plan.status in ("OPTIMAL", "FEASIBLE")
    delivered = sum(sum(v) for v in plan.delivered.values())
    assert delivered > 0, "soft violation must not stop delivery"
    assert sum(plan.offspec.get("MOLTEN-S", [0])) > 0, "penalty reported"


def test_hard_violation_blocks_allocation():
    """Impossible HARD limit: solver stays feasible via demand slack but
    refuses to deliver violating material (blocking semantics)."""
    sc = datasets.sulphur_minimal()
    sc.destinations[0].spec = [SpecRange("moisture", pmax=0.1, hard=True)]
    plan = solve_plan(sc, SolveOptions(max_time_s=3))
    assert plan.status in ("OPTIMAL", "FEASIBLE")
    assert sum(plan.delivered.get("MOLTEN-S", [0])) == 0
    assert sum(plan.unmet.get("MOLTEN-S", [0])) > 0


def test_s3_breakdown_identifies_actual_principal():
    sc = datasets.end_to_end()
    k = compute_kpis(sc, solve_plan(sc, SolveOptions(max_time_s=3)))
    pr = k["onspec_principal"]
    rows = k["quality_breakdown"]
    if k["onspec_pct"] >= 100.0 - 1e-9:
        assert pr is None
    else:
        worst = max(rows, key=lambda r: (r["violations"], r["sum_dev"]))
        assert pr["destination"] == worst["destination"]
        assert pr["parameter"] == worst["parameter"]
        assert pr["hard"] == worst["hard"]


def test_assumed_specs_carry_evidence_status():
    sc = datasets.end_to_end()
    k = compute_kpis(sc, solve_plan(sc, SolveOptions(max_time_s=3)))
    rows = {(r["destination"], r["parameter"]): r
            for r in k["quality_breakdown"]}
    assert rows[("DRY-FEED", "Cd")]["evidence"] == "ASSUMED"
    assert rows[("DRY-FEED", "Cd")]["source"] == "placeholder (this MVP)"
    # UI shows the evidence column in both breakdown tables
    src = (ROOT / "ui" / "pages_overview.py").read_text(encoding="utf-8")
    assert src.count('"evidence"') >= 2


# ====================== belt-parcel quality regression =======================
def test_belt_parcels_default_to_grade_quality():
    """Regression (fixed 14-Jul-2026): belt-profile parcels without an explicit
    quality vector blended in as ZERO quality and diluted stacked piles
    (S3 sim-basis on-spec collapsed to ~50 % with impossible BPL ≈ 49)."""
    sc = datasets.end_to_end()
    plan = solve_plan(sc, SolveOptions(max_time_s=3))
    sim = Simulator(copy.deepcopy(sc), copy.deepcopy(plan), auto_replan=False)
    tbt = sc.grade("TBT").quality
    for parcel in sim.belts:
        assert parcel.quality, "parcel quality must default to grade vector"
        if parcel.grade == "TBT":
            assert parcel.quality["BPL"] == pytest.approx(tbt["BPL"])
    sim.run_to_end()
    for d in sc.destinations:
        for t in range(sim.bucket):
            q = sim.delivered_q[d.id][t]
            if sim.delivered[d.id][t] > 0 and "BPL" in q:
                assert q["BPL"] > 60.0, (
                    f"{d.id}@{t}: BPL {q['BPL']:.1f} — zero-quality dilution "
                    "regression")


# ====================== deterministic plan signature ==========================
def test_s3_plan_signature_pinned():
    """Deterministic BG-train profile signature (budget 3, seed 42).

    The selectable site-focused profile retains its pinned allocation even
    though FULL_FLOW is now the fresh S0 default.
    """
    sc = datasets.end_to_end()
    datasets.apply_planning_demand_profile(sc, "BG_TRAIN")
    res = solve_plan(sc, SolveOptions(max_time_s=3))
    sig = tuple((j.machine, j.space, j.dest_id, j.start_bucket, j.end_bucket,
                 j.tonnage_t) for j in res.jobs)
    h = hashlib.sha256(json.dumps(sig).encode()).hexdigest()[:16]
    # Rebaselined 13-Sep-2026: the campaign/parcel floor (CAMPAIGN_POLICY)
    # and the corrected continuous-feed service test change which buckets
    # the planner activates, so the material allocation differs. Mass
    # balance, link/line capacity and machine exclusivity are re-verified
    # from the resulting minute schedule in test_schedule_fragmentation.py.
    # Positioning is a post-solve minute activity and does not alter this
    # authoritative material-allocation signature.
    assert h == "853415c7f5070d37", (
        f"S3 plan signature changed unexpectedly: {h}")
