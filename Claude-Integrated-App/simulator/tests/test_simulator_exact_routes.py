"""Execution must preserve exact path shares, route limits and contingencies."""
import copy

import pytest

from mph_sim.domain import (CalendarEntry, DemandEntry, Destination, Grade, Job,
                            JobBucketFlow, JobExecutionWindow, Link, Machine,
                            Node, PlanResult, Scenario, SimEvent, SimParams,
                            Site, StockSpace)
from mph_sim.simulator import Simulator


def two_paths():
    sc = Scenario("two route replay", sim=SimParams(horizon_buckets=1))
    sc.sites = [Site("S", "S", "hub")]
    sc.grades = [Grade("G", "G", {"BPL": 71})]
    sc.spaces = [StockSpace("P", "S", "M16-humid", "closed", 1000, 500, "G",
                            x_from=0, x_to=100)]
    sc.nodes = [Node("SPACE:P", "S", "space"), Node("X", "S", "tower"),
                Node("D", "S", "dest")]
    sc.links = [Link("TRUNK", "SPACE:P", "X", 100),
                Link("A", "X", "D", 60), Link("B", "X", "D", 40)]
    sc.machines = [Machine("RC", "S", "reclaimer", 0, 100, ["P"],
                           position_x=50, setup_min=0)]
    sc.destinations = [Destination("OUT", "S", "D", demand_default_tph=100)]
    job = Job("J", "RC", "reclaim", "P", dest_id="OUT", grade="G",
              rate_tph=100, tonnage_t=100, path=["TRUNK", "A"],
              start_x=50, stop_x=50,
              bucket_flows=[JobBucketFlow(0, 60, ["TRUNK", "A"]),
                            JobBucketFlow(0, 40, ["TRUNK", "B"])])
    return sc, job


@pytest.mark.parametrize("legacy", [False, True])
def test_split_routes_attribute_each_branch_and_common_trunk_once(legacy):
    sc, job = two_paths()
    sim = Simulator(sc, PlanResult("FEASIBLE", jobs=[job]), auto_replan=False)
    sim._step_bucket_legacy() if legacy else sim.step()
    assert sim.moved_by_job["J"] == pytest.approx(100)
    assert sim.inv["P"] == pytest.approx(400)
    assert sim.link_load["TRUNK"][0] == pytest.approx(100)
    assert sim.link_load["A"][0] == pytest.approx(60)
    assert sim.link_load["B"][0] == pytest.approx(40)


def test_one_route_counts_a_shared_group_once_when_two_member_links_are_in_series():
    sc, job = two_paths()
    sc.link("TRUNK").shared_group = "SERIES"
    sc.link("A").shared_group = "SERIES"
    sc.link("TRUNK").capacity_tph = 200
    sc.link("A").capacity_tph = 200
    sim = Simulator(sc, PlanResult("FEASIBLE", jobs=[job]), auto_replan=False)
    sim.step()
    assert sim.moved_by_job["J"] == pytest.approx(100)


@pytest.mark.parametrize("legacy", [False, True])
def test_non_dominant_unavailable_path_holds_job_without_automatic_rerouting(legacy):
    sc, job = two_paths()
    sc.link("B").available = False
    sim = Simulator(sc, PlanResult("FEASIBLE", jobs=[job]), auto_replan=False)
    sim._step_bucket_legacy() if legacy else sim.step()
    assert sim.moved_by_job.get("J", 0) == 0
    assert sim.inv["P"] == 500
    assert all(series[0] == 0 for series in sim.link_load.values())


def test_exact_non_dominant_path_trip_preserves_only_completed_prefix():
    sc, job = two_paths()
    job.execution_windows = [JobExecutionWindow(0, 0, 0, 60, 100, 100, status="VALIDATED")]
    event = SimEvent("E", "conveyor_trip", 0, "B", minute=30, duration_minutes=30)
    sim = Simulator(sc, PlanResult("FEASIBLE", jobs=[job]), [event], auto_replan=False)
    sim.step()
    assert sim.jobs["J"].status == "CANCELLED"
    assert sim.moved_by_job["J"] == pytest.approx(50)
    assert sim.link_load["A"][0] == pytest.approx(30)
    assert sim.link_load["B"][0] == pytest.approx(20)
    assert sim.interrupted_jobs["J"]["remaining_t"] == pytest.approx(50)


def test_timed_non_dominant_rate_cap_splits_minute_intervals():
    sc, job = two_paths()
    sc.calendars = [CalendarEntry("LINK:B", 0, 0, start_minute=30, end_minute=60,
                                  action="RATE_CAP", rate_tph=20)]
    sim = Simulator(sc, PlanResult("FEASIBLE", jobs=[job]), auto_replan=False)
    sim.step()
    assert sim.moved_by_job["J"] == pytest.approx(75)
    assert sim.link_load["A"][0] == pytest.approx(45)
    assert sim.link_load["B"][0] == pytest.approx(30)


@pytest.mark.parametrize("legacy", [False, True])
def test_non_dominant_capacity_preserves_share_and_scales_total_flow(legacy):
    sc, job = two_paths()
    sc.link("B").capacity_tph = 20
    sim = Simulator(sc, PlanResult("FEASIBLE", jobs=[job]), auto_replan=False)
    sim._step_bucket_legacy() if legacy else sim.step()
    assert sim.moved_by_job["J"] == pytest.approx(50)
    assert sim.link_load["A"][0] == pytest.approx(30)
    assert sim.link_load["B"][0] == pytest.approx(20)


@pytest.mark.parametrize("legacy", [False, True])
def test_simultaneous_jobs_share_remaining_link_capacity(legacy):
    sc, job = two_paths()
    machine = copy.deepcopy(sc.machine("RC"))
    machine.id = "RC2"
    sc.machines.append(machine)
    second = copy.deepcopy(job)
    second.id, second.machine = "J2", "RC2"
    sim = Simulator(sc, PlanResult("FEASIBLE", jobs=[job, second]), auto_replan=False)
    sim._step_bucket_legacy() if legacy else sim.step()
    assert sum(sim.moved_by_job.values()) == pytest.approx(100)
    assert sim.link_load["TRUNK"][0] == pytest.approx(100)


def test_unused_dominant_path_does_not_block_a_later_exact_route():
    sc, job = two_paths()
    sc.sim.horizon_buckets = 2
    sc.link("A").capacity_tph = sc.link("B").capacity_tph = 100
    job.end_bucket, job.tonnage_t = 1, 200
    job.bucket_flows = [JobBucketFlow(0, 100, ["TRUNK", "A"]),
                        JobBucketFlow(1, 100, ["TRUNK", "B"])]
    event = SimEvent("E", "conveyor_trip", 1, "A", minute=60, duration_minutes=60)
    sim = Simulator(sc, PlanResult("FEASIBLE", jobs=[job]), [event], auto_replan=False)
    sim.run_to_end()
    assert sim.moved_by_job["J"] == pytest.approx(200)
    assert sim.link_load["A"] == pytest.approx([100, 0])
    assert sim.link_load["B"] == pytest.approx([0, 100])


def test_explicit_profile_gap_does_not_replay_an_aggregate_rate():
    sc, job = two_paths()
    sc.sim.horizon_buckets = 2
    job.end_bucket = 1
    sim = Simulator(sc, PlanResult("FEASIBLE", jobs=[job]), auto_replan=False)
    sim._step_bucket_legacy()
    sim._step_bucket_legacy()
    assert sim.moved_by_job["J"] == pytest.approx(100)
    assert sim.link_load["TRUNK"] == pytest.approx([100, 0])


def test_demand_override_snapshot_preserves_effective_campaign_priority():
    sc, job = two_paths()
    sc.demand = [DemandEntry("OUT", 0, 100, requested_grade="G", priority=7)]
    sim = Simulator(sc, PlanResult("FEASIBLE", jobs=[job]), auto_replan=False)
    sim.demand_override[("OUT", 0)] = 80
    snapshot = sim.snapshot_scenario()
    assert snapshot.demand_priority("OUT", 0) == 7
    assert snapshot.demand_tph("OUT", 0) == 80
