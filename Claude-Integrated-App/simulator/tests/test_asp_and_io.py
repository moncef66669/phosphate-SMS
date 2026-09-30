"""ASP rule severities, deterministic replay, scenario import/export,
run store, and the end-to-end smoke test."""
import copy
import json

import pandas as pd
import pytest

from mph_sim import datasets
from mph_sim.adapters import (AspPlanItem, RabbitMqAspAdapter,
                              SimulatedAspAdapter, job_to_plan_item)
from mph_sim.domain import Job, SimEvent
from mph_sim.kpis import compute_kpis
from mph_sim.optimizer import SolveOptions, solve_plan
from mph_sim.persistence import (RunStore, export_excel, import_table,
                                 scenario_from_json, scenario_tables,
                                 scenario_to_json)
from mph_sim.simulator import Simulator


def _item(sc, **over):
    base = dict(JobId="T1", Device="RC01-A", JobType="Reclaiming", Method="bench",
                Pile="HP-02", Material="BT", Grade="BT", ReferenceLocation="PAP-1",
                StartTime=sc.sim.bucket_dt(1).isoformat(), StartX=0.0, StopX=100.0,
                PlannedTonnage=5000.0, PlannedFlowRate=1000.0, Comment="")
    base.update(over)
    return AspPlanItem(**base)


# ------------------------------------------------- ASP severities (8 rules)
class TestAspRuleSeverities:
    @pytest.fixture(autouse=True)
    def setup(self, s2):
        self.sc = s2
        self.asp = SimulatedAspAdapter(s2)

    def _sev(self, issues, rule):
        return [i.severity for i in issues if i.rule == rule]

    def test_parameter_sanity_error(self):
        issues = self.asp.submit_plan_items([
            _item(self.sc, Pile="NOPE"),                       # invalid pile
            _item(self.sc, PlannedFlowRate=-5),                # invalid rate
            _item(self.sc, StartX=100, StopX=100)])            # empty range
        sevs = self._sev(issues, "ParameterSanity")
        assert sevs and set(sevs) == {"ERROR"}

    def test_work_area_error(self):
        m = self.sc.machine("RC01-A")
        m.work_area["HP-02"] = [0.0, 50.0]
        try:
            issues = self.asp.submit_plan_items([_item(self.sc, StopX=200.0)])
            assert self._sev(issues, "WorkArea") == ["ERROR"]
        finally:
            m.work_area.pop("HP-02", None)

    def test_inventory_error(self):
        issues = self.asp.submit_plan_items([
            _item(self.sc, JobId="MASS", PlannedTonnage=100_000),
            _item(self.sc, JobId="GRADE", Grade="TBT",
                  StartTime=self.sc.sim.bucket_dt(200).isoformat())])
        sevs = self._sev(issues, "InventoryAvailability")
        assert sevs and set(sevs) == {"ERROR"}

    def test_same_device_overlap_error(self):
        issues = self.asp.submit_plan_items([
            _item(self.sc, JobId="A"),
            _item(self.sc, JobId="B")])                        # same device, same time
        assert self._sev(issues, "SameDeviceOverlap") == ["ERROR"]

    def test_work_calendar_error(self):
        issues = self.asp.submit_plan_items([
            _item(self.sc, Device="ST01-C", JobType="Stacking",
                  StartTime=self.sc.sim.bucket_dt(1).isoformat())])
        assert self._sev(issues, "WorkCalendar") == ["ERROR"]  # ST01-C blocked 0-5

    def test_device_availability_warning(self):
        m = self.sc.machine("RC01-B")
        m.available = False
        try:
            issues = self.asp.submit_plan_items([_item(self.sc, Device="RC01-B")])
            assert self._sev(issues, "DeviceAvailability") == ["WARNING"]
        finally:
            m.available = True

    def test_spatial_conflict_warning(self):
        issues = self.asp.submit_plan_items([
            _item(self.sc, JobId="A", Device="RC01-A", StartX=0, StopX=100),
            _item(self.sc, JobId="B", Device="ST01-A", JobType="Stacking",
                  StartX=110, StopX=200)])                     # gap 10 m < 50 m
        assert self._sev(issues, "SpatialConflict") == ["WARNING"]

    def test_fifo_info(self):
        # HP-02 has older segments than... use HP-06 (BT, no segments -> age 0)
        issues = self.asp.submit_plan_items([_item(self.sc, Pile="HP-06")])
        assert self._sev(issues, "FIFO") == ["INFO"]

    def test_rabbitmq_adapter_is_stub(self):
        ad = RabbitMqAspAdapter(site_id="MPH", sms_id="MPH-SMS",
                                endpoint="amqp://placeholder")
        with pytest.raises(NotImplementedError):
            ad.submit_plan_items([])
        assert ad.health()["mode"] == "not-configured"


def test_job_to_plan_item_mapping(s2, s2_plan):
    j = next(j for j in s2_plan.jobs if j.kind == "reclaim")
    it = job_to_plan_item(s2, j)
    assert it.Device == j.machine and it.Pile == j.space
    assert it.JobType == "Reclaiming"
    assert it.PlannedTonnage == j.tonnage_t
    assert it.StopX > it.StartX
    assert it.Grade  # material grade always mapped


# ------------------------------------------------- deterministic replay (AC 11)
def test_deterministic_replay():
    def run():
        sc = datasets.sulphur_minimal()
        plan = solve_plan(sc, SolveOptions(max_time_s=3))
        sim = Simulator(sc, plan, events=[
            SimEvent("E1", "reduced_rate", 5, "M17-RC01", magnitude=0.5,
                     duration_buckets=3)])
        sim.run_to_end()
        k = compute_kpis(sc, plan, sim)
        jobsig = tuple(sorted((j.id, j.machine, j.space, j.start_bucket,
                               j.end_bucket, j.tonnage_t, j.status)
                              for j in sim.jobs.values()))
        return jobsig, k["service_pct"], k["unmet_t"], sim.replans
    a, b = run(), run()
    assert a == b, "identical scenario + seed must reproduce the identical run"


# ------------------------------------------------- scenario import/export
def test_scenario_json_roundtrip(s3):
    txt = scenario_to_json(s3)
    sc2 = scenario_from_json(txt)
    assert sc2.name == s3.name
    assert len(sc2.spaces) == len(s3.spaces)
    assert len(sc2.links) == len(s3.links)
    assert sc2.sim.seed == s3.sim.seed
    assert sc2.weights.w1_unmet == s3.weights.w1_unmet
    sp0, sp1 = s3.spaces[0], sc2.spaces[0]
    assert sp0.id == sp1.id and sp0.capacity_t == sp1.capacity_t
    assert sp0.evidence.status == sp1.evidence.status
    d0, d1 = s3.destinations[0], sc2.destinations[0]
    assert [s.param for s in d0.spec] == [s.param for s in d1.spec]
    # roundtrip is loss-free for planning: same plan from both
    p1 = solve_plan(s3, SolveOptions(max_time_s=3))
    p2 = solve_plan(sc2, SolveOptions(max_time_s=3))
    assert [(j.machine, j.space, j.tonnage_t) for j in p1.jobs] == \
        [(j.machine, j.space, j.tonnage_t) for j in p2.jobs]


def test_table_export_import(s2):
    sc = copy.deepcopy(s2)
    tables = scenario_tables(sc)
    assert set(tables) >= {"spaces", "machines", "links", "destinations",
                           "demand", "assumptions"}
    df = tables["spaces"]
    assert "evidence_status" in df.columns and "source" in df.columns
    # edit a value via the table path and re-import
    df.loc[df["id"] == "HP-02", "mass_t"] = 77_777.0
    import_table(sc, "spaces", df)
    assert sc.space("HP-02").mass_t == 77_777.0
    assert sc.space("HP-02").evidence.status in ("CONFIRMED", "DERIVED",
                                                 "INFERRED", "ASSUMED", "TBC")


def test_excel_export(s1, tmp_path):
    data = export_excel(s1)
    fp = tmp_path / "scenario.xlsx"
    fp.write_bytes(data)
    x = pd.read_excel(fp, sheet_name="spaces")
    assert "capacity_t" in x.columns and len(x) == 3


def test_run_store(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    rid = store.record("test-scn", 42, "cp-sat", "OPTIMAL", 1.5, 2,
                       {"service_pct": 98.6, "unmet_t": 10.0})
    assert rid >= 1
    h = store.history()
    assert len(h) == 1
    assert json.loads(h.iloc[0]["kpis"])["service_pct"] == 98.6


# ------------------------------------------------- end-to-end smoke (required)
def test_e2e_smoke(tmp_path):
    """Load scenario -> plan -> simulate -> inject failure -> replan -> export."""
    sc = datasets.mph_phosphate()
    sc.sim.max_solve_s = 6
    plan = solve_plan(sc)
    assert plan.status in ("OPTIMAL", "FEASIBLE")
    assert plan.jobs

    sim = Simulator(sc, plan, events=[
        SimEvent("F1", "machine_failure", 4, "RC01-A", duration_buckets=3)])
    for _ in range(8):
        sim.step()
    assert sim.replans >= 1, "failure must trigger a replan"
    assert any(r.kind == "EVENT" for r in sim.log)
    assert any(r.kind == "REPLAN" for r in sim.log)
    sim.run_to_end()

    k = compute_kpis(sc, plan, sim)
    assert 0 < k["service_pct"] <= 100
    assert k["throughput_t"] > 0
    assert k["replans"] == sim.replans

    # ASP preview on the final job set
    asp = SimulatedAspAdapter(sc)
    items = [job_to_plan_item(sc, j) for j in sim.jobs.values()
             if j.status in ("PLANNED", "RUNNING", "FINISHED")]
    issues = asp.submit_plan_items(items)
    assert all(i.severity in ("ERROR", "WARNING", "INFO") for i in issues)

    # exports
    (tmp_path / "scenario.json").write_text(scenario_to_json(sc), encoding="utf-8")
    tables = scenario_tables(sc)
    for name, df in tables.items():
        df.to_csv(tmp_path / f"{name}.csv", index=False)
    ev = pd.DataFrame([{"bucket": r.bucket, "kind": r.kind, "message": r.message}
                       for r in sim.log])
    ev.to_csv(tmp_path / "event_log.csv", index=False)
    assert (tmp_path / "scenario.json").exists()
    assert (tmp_path / "spaces.csv").exists()
    assert len(ev) > 0
