"""Three-SMS integration tests: partitioning integrity, site-scoped
validation, feedback/inventory separation, exports, rendering, determinism."""
import copy
import dataclasses
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mph_sim import datasets                                     # noqa: E402
from mph_sim.adapters import (AspPlanItem, SimulatedAspAdapter,  # noqa: E402
                              job_to_plan_item)
from mph_sim.integration import (COMMON_PROFILE,                 # noqa: E402
                                 MultiSmsAdapterRegistry, SMS_SITES,
                                 build_sms_batches, default_sms_configs,
                                 job_domain, partition_jobs_by_sms,
                                 scenario_site_projection,
                                 space_values_for_site, unmapped_reason)
from mph_sim.optimizer import SolveOptions, solve_plan           # noqa: E402


@pytest.fixture(scope="module")
def s3():
    return datasets.end_to_end()


@pytest.fixture(scope="module")
def s3_plan(s3):
    return solve_plan(s3, SolveOptions(max_time_s=3))


@pytest.fixture(scope="module")
def s3_part(s3, s3_plan):
    return partition_jobs_by_sms(s3, s3_plan.jobs)


# ---- partitioning integrity ---------------------------------------------------
def test_every_s3_job_has_exactly_one_owner(s3, s3_plan, s3_part):
    ids = [j.id for k in s3_part for j in s3_part[k]]
    assert sorted(ids) == sorted(set(ids)), "job duplicated across SMS"
    assert len(ids) == len(s3_plan.jobs), "job lost during partitioning"


def test_counts_sum_to_plan_and_no_unmapped(s3_plan, s3_part):
    assert sum(len(v) for v in s3_part.values()) == len(s3_plan.jobs)
    assert s3_part["UNMAPPED"] == []


def test_ownership_follows_machine_site(s3, s3_part):
    msite = {m.id: m.site for m in s3.machines}
    for site in SMS_SITES:
        assert all(msite[j.machine] == site for j in s3_part[site])


def test_s3_full_flow_creates_declared_work_for_all_three_sites(s3_part):
    assert s3_part["LOUTA"], "the explicit stocked route must create work"
    assert s3_part["MPH"], "configured PAP/dry demand must create work"
    assert s3_part["BENGUERIR"], (
        "washing, train and C2 demand must create Benguerir work")


def test_site_mismatch_and_unknown_machine_become_visible_errors(s3, s3_plan):
    mph_jobs = [
        job for job in s3_plan.jobs
        if any(
            machine.id == job.machine and machine.site == "MPH"
            for machine in s3.machines
        )
    ]
    assert len(mph_jobs) >= 2
    jobs = copy.deepcopy(mph_jobs[:2])
    jobs[0].machine = "GHOST-RC-99"                  # unknown machine
    jobs[1].space = "LT-P1"                          # MPH machine, Louta pile
    part = partition_jobs_by_sms(s3, jobs)
    unmapped_ids = {j.id for j in part["UNMAPPED"]}
    assert jobs[0].id in unmapped_ids and jobs[1].id in unmapped_ids
    assert "unknown machine" in unmapped_reason(s3, jobs[0])
    assert "mismatch" in unmapped_reason(s3, jobs[1])
    # never silently assigned to MPH
    assert jobs[0].id not in {j.id for j in part["MPH"]}
    st = build_sms_batches(s3, type("P", (), {"jobs": jobs})())
    assert len(st.unmapped) == 2


# ---- batches per scenario ---------------------------------------------------------
def test_s3_produces_three_active_batches(s3, s3_plan):
    st = build_sms_batches(s3, s3_plan)
    for site in SMS_SITES:
        b = st.batches[site]
        assert st.configs[site].connection_mode == "offline-simulated"
        active = {j.id: j for j in b.jobs if j.status != "CANCELLED"}
        assert set(b.item_parent_jobs) == {item.JobId for item in b.items}
        timing_blocked = {
            issue.item_id for issue in b.validation_issues
            if issue.rule == "ExecutionTiming"}
        assert set(b.item_parent_jobs.values()) == \
            set(active) - timing_blocked
        # One validated execution window becomes one runnable ASP item.
        # Parent grouping must preserve the solver job's complete mass.
        for job_id, job in active.items():
            mapped = [item for item in b.items
                      if b.item_parent_jobs.get(item.JobId) == job_id]
            if job_id in timing_blocked:
                assert mapped == []
            else:
                assert sum(item.PlannedTonnage for item in mapped) == \
                    pytest.approx(job.tonnage_t)
        assert b.sms_id == f"{site}-SMS"


def test_s2_mph_only_and_arrivals_are_not_jobs():
    s2 = datasets.mph_phosphate()
    p2 = solve_plan(s2, SolveOptions(max_time_s=2))
    st = build_sms_batches(s2, p2)
    assert len(st.batches["MPH"].jobs) == len(p2.jobs)
    for mine in ("LOUTA", "BENGUERIR"):
        assert st.configs[mine].connection_mode == "reference"
        assert st.batches[mine].jobs == []
        assert "Reference instance — not included in S2" \
            in st.batches[mine].note
    # Simulated corridor arrivals (pseudo-corridor scenario inputs) never
    # become SMS jobs. Validated jobs may map to several execution-window
    # items, but every item has exactly one internal optimiser parent.
    assert "simulated corridor arrivals" in s2.name
    batch = st.batches["MPH"]
    active_ids = {
        j.id for j in p2.jobs
        if j.status != "CANCELLED"
        and st.batches["MPH"].item_states[j.id]
        != "Rejected (simulated)"}
    assert set(batch.item_parent_jobs) == {item.JobId for item in batch.items}
    assert set(batch.item_parent_jobs.values()) == active_ids
    msite = {m.id: m.site for m in s2.machines}
    assert all(msite[j.machine] == "MPH" for j in p2.jobs)
    assert st.unmapped == []


def test_s1_mph_jobs_labelled_domain_b():
    s1 = datasets.sulphur_minimal()
    p1 = solve_plan(s1, SolveOptions(max_time_s=2))
    assert p1.jobs
    assert {job_domain(s1, j) for j in p1.jobs} == {"B — Sulphur (M17)"}
    st = build_sms_batches(s1, p1)
    assert len(st.batches["MPH"].jobs) == len(p1.jobs)
    assert st.batches["LOUTA"].dispatch_state == "Reference only"


# ---- common wire schema ------------------------------------------------------------
def test_all_three_sms_share_the_asp_plan_item_schema(s3, s3_plan):
    st = build_sms_batches(s3, s3_plan)
    fields = {f.name for f in dataclasses.fields(AspPlanItem)}
    assert "Site" not in fields and "SmsId" not in fields, \
        "site/SMS ownership must stay in the envelope, not the wire schema"
    for site in SMS_SITES:
        for it in st.batches[site].items:
            assert isinstance(it, AspPlanItem)
            assert set(dataclasses.asdict(it)) == fields
            assert not it.Grade.startswith(("LOU-RAW-", "MINE-RAW-"))
    # The identical mapper serves every active SMS in the full-flow profile.
    for site in SMS_SITES:
        j = st.batches[site].jobs[0]
        assert job_to_plan_item(s3, j) in st.batches[site].items or \
            job_to_plan_item(s3, j).JobId == j.id
    louta_raw = {
        item.Grade for item in st.batches["LOUTA"].items
        if item.Grade.startswith("RAW-")
    }
    assert louta_raw
    assert louta_raw <= {
        "RAW-TBC", "RAW-BT-GLOBAL", "RAW-BT-HIGH-CD", "RAW-BT-LOW-CD"}


# ---- site-scoped validation -----------------------------------------------------------
def test_validation_scoped_to_own_site_items(s3, s3_plan):
    st = build_sms_batches(s3, s3_plan)
    for site in SMS_SITES:
        b = st.batches[site]
        own = {i.JobId for i in b.items} | {j.id for j in b.jobs} | {""}
        assert {i.item_id for i in b.validation_issues} <= own, \
            f"{site} validation references foreign jobs"


def test_projection_contains_only_site_assets(s3):
    for site in SMS_SITES:
        pr = scenario_site_projection(s3, site)
        assert {sp.site for sp in pr.spaces} == {site}
        assert {m.site for m in pr.machines} == {site}
        own_spaces = {sp.id for sp in pr.spaces}
        assert all(g.space_id in own_spaces for g in pr.segments)


def test_fifo_does_not_cross_sites(s3):
    # older same-grade material on a LOUTA pile must NOT raise FIFO advice
    # for an MPH reclaim item — the MPH projection excludes mine piles
    sc = copy.deepcopy(s3)
    hp = sc.space("HP-01")
    lt = sc.space("LT-P1")
    lt.grade = hp.grade                       # same grade, other site
    for sp in sc.spaces:                      # make LT-P1 the ONLY older
        if sp.id not in ("HP-01", "LT-P1"):   # same-grade candidate anywhere
            sp.grade = "OTHER-GRADE"
    from mph_sim.domain import Segment
    sc.segments.append(Segment("LT-P1", 0, 100, 50_000, hp.grade,
                               quality={}, age_days=900))
    item = job_to_plan_item(sc, _mk_reclaim_job(sc, "RC01-A", "HP-01"))
    asp = SimulatedAspAdapter(scenario_site_projection(sc, "MPH"),
                              site_id="MPH")
    issues = asp.submit_plan_items([item])
    assert not any("LT-P1" in i.message for i in issues)
    # sanity: unscoped whole-scenario gate WOULD see the cross-site pile
    issues_all = SimulatedAspAdapter(sc).submit_plan_items([item])
    assert any("LT-P1" in i.message and i.rule == "FIFO"
               for i in issues_all)


def test_spatial_conflict_does_not_cross_sites(s3):
    # two overlapping close operations on piles of different sites never pair
    sc = copy.deepcopy(s3)
    ja = _mk_reclaim_job(sc, "RC01-A", "HP-01")
    jb = _mk_reclaim_job(sc, "RC2", "BG-P1")
    ia, ib = job_to_plan_item(sc, ja), job_to_plan_item(sc, jb)
    for site, item in (("MPH", ia), ("BENGUERIR", ib)):
        asp = SimulatedAspAdapter(scenario_site_projection(sc, site),
                                  site_id=site, current_pile_only=False,
                                  min_distance_m=10_000)
        issues = asp.submit_plan_items([item])   # the other site's item absent
        assert not any(i.rule == "SpatialConflict" for i in issues)


def _mk_reclaim_job(sc, machine, space):
    from mph_sim.domain import Job
    sp = sc.space(space)
    return Job(id=f"T-{machine}-{space}", machine=machine, space=space,
               kind="reclaim", grade=sp.grade, dest_id="DEST:PAP-1",
               start_bucket=0, end_bucket=1, tonnage_t=500, rate_tph=500,
               start_x=sp.x_from, stop_x=sp.x_to, status="PLANNED")


# ---- feedback / inventory separation ------------------------------------------------
def test_inventory_response_only_own_spaces(s3):
    assert {r["pile"] for r in space_values_for_site(s3, "LOUTA")} \
        == {
            "LT-MINE-FEED-GLOBAL", "LT-MINE-FEED-LOW-CD",
            "110LG-OL1", "110LG-OL2",
            "LT-WASH-L1-WIP", "LT-WASH-L2-WIP",
            "LT-P1", "LT-P2",
        }
    assert {r["pile"] for r in space_values_for_site(s3, "BENGUERIR")} \
        == {"BG-P1", "BG-P2"}
    assert {r["pile"] for r in space_values_for_site(s3, "MPH")} \
        == {f"HP-{i:02d}" for i in range(1, 9)}


def test_job_updates_scoped_by_site(s3, s3_plan):
    from mph_sim.integration import job_updates_for_site
    from mph_sim.simulator import Simulator
    sim = Simulator(copy.deepcopy(s3), copy.deepcopy(s3_plan),
                    auto_replan=False)
    for _ in range(4):
        sim.step()
    msite = {m.id: m.site for m in s3.machines}
    for site in SMS_SITES:
        ups = job_updates_for_site(s3, sim, site)
        for u in ups:
            assert msite[sim.jobs[u["job_id"]].machine] == site


# ---- configuration / registry ---------------------------------------------------------
def test_default_configs_offline_and_credential_free(s3):
    cfgs = default_sms_configs()
    assert set(cfgs) == set(SMS_SITES)
    for site, c in cfgs.items():
        assert c.adapter_profile == COMMON_PROFILE
        assert c.endpoint == ""            # blank/TBC — never a credential
        assert not c.configured
        assert c.sms_id == f"{site}-SMS"
        assert c.evidence.status == "CONFIRMED"
        assert "TBC" in c.evidence.note


def test_missing_endpoint_does_not_prevent_offline_preview(s3, s3_plan):
    st = build_sms_batches(s3, s3_plan)     # endpoints all blank
    assert all(st.batches[site].items for site in SMS_SITES)
    reg = MultiSmsAdapterRegistry(s3)
    h = reg.health()
    assert all(h[s_]["mode"] == "offline-simulated" for s_ in SMS_SITES)
    assert all(not h[s_].get("connected", False) for s_ in SMS_SITES)


def test_registry_single_implementation_three_instances(s3):
    reg = MultiSmsAdapterRegistry(s3)
    ads = [reg.adapter(s_) for s_ in SMS_SITES]
    assert all(type(a) is SimulatedAspAdapter for a in ads)
    assert len({id(a) for a in ads}) == 3
    assert [a.site_id for a in ads] == list(SMS_SITES)


# ---- exports -----------------------------------------------------------------------------
def test_per_sms_export_tree_and_aggregate(s3, s3_plan):
    import io
    import pandas as pd
    from ui.pages_meta import integration_zip_entries
    entries = integration_zip_entries(s3, s3_plan, None)
    for site in SMS_SITES:
        for f in ("plan_batch.json", "asp_plan_items.csv",
                  "validation_issues.csv", "job_updates.csv",
                  "space_values.csv"):
            assert f"integration/{site}/{f}" in entries
    assert "integration/summary.csv" in entries
    assert "integration/unmapped_jobs.csv" in entries
    # per-site items are disjoint and complete
    seen = []
    for site in SMS_SITES:
        df = pd.read_csv(io.StringIO(
            entries[f"integration/{site}/asp_plan_items.csv"]))
        seen += list(df.JobId)
    assert sorted(seen) == sorted(set(seen))
    agg = pd.read_csv(io.StringIO(entries["asp_plan_items.csv"]))
    assert "sms_site" in agg.columns, "aggregate must carry sms_site"
    assert sorted(agg.JobId) == sorted(seen)
    assert set(agg.sms_site) <= {f"{s_}-SMS" for s_ in SMS_SITES}
    summ = pd.read_csv(io.StringIO(entries["integration/summary.csv"]))
    assert list(summ.columns)[0] == "sms_site"
    assert (summ.submission ==
            "offline simulated — no live plan submission").all()


# ---- UI ----------------------------------------------------------------------------------
def test_integration_console_renders_headlessly():
    from streamlit.testing.v1 import AppTest
    from conftest import nav_to
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
    at.run()
    nav_to(at, "11 · Three-SMS Integration Console")
    assert not at.exception
    text = " ".join(str(m.value) for m in at.markdown) + \
        " ".join(str(w.value) for w in at.warning)
    assert "identical Innomotics/YardMaster SMS" in text
    assert "plan submission and jobupdated" in text.lower()
    assert "offline simulated" in text.lower()
    assert "read-only" in text.lower()


# ---- determinism guard --------------------------------------------------------------------
def test_solver_signature_unchanged():
    import hashlib
    import json
    # Keep the selectable site-focused profile pinned independently of the
    # fresh S0 FULL_FLOW default.
    scenario = datasets.end_to_end()
    datasets.apply_planning_demand_profile(scenario, "BG_TRAIN")
    res = solve_plan(scenario, SolveOptions(max_time_s=3))
    sig = tuple((j.machine, j.space, j.dest_id, j.start_bucket, j.end_bucket,
                 j.tonnage_t) for j in res.jobs)
    h = hashlib.sha256(json.dumps(sig).encode()).hexdigest()[:16]
    # Positioning is a post-solve minute activity and does not alter this
    # authoritative material-allocation signature.
    assert h == "5c5d9dc60ce40f12"
