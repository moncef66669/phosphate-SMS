"""Focused regressions for minute-exact simulation contingencies."""
from __future__ import annotations

import copy

import pytest

from mph_sim import datasets
from mph_sim.domain import (Job, JobBucketFlow, JobExecutionWindow, PlanResult,
                            SimEvent)
from mph_sim.simulator import Simulator


def _one_hour_reclaim():
    scenario = datasets.sulphur_minimal()
    scenario.sim.horizon_buckets = 2
    scenario.exogenous = []
    job = Job(
        id="J-EXACT",
        machine="M17-RC01",
        kind="reclaim",
        space="M17-CLOSED-1",
        dest_id="MOLTEN-S",
        rate_tph=600.0,
        tonnage_t=600.0,
        start_bucket=0,
        end_bucket=0,
        path=["L-M17-RECLAIM"],
        bucket_flows=[JobBucketFlow(
            bucket=0, tonnage_t=600.0, path=["L-M17-RECLAIM"])],
        execution_windows=[JobExecutionWindow(
            solver_start_bucket=0,
            solver_end_bucket=0,
            start_minute=0,
            duration_min=60,
            tonnage_t=600.0,
            rate_tph=600.0,
            status="VALIDATED")],
    )
    return scenario, PlanResult(status="FEASIBLE", jobs=[job])


def test_exact_minute_failure_preserves_prefix_and_replans_live_state(
        monkeypatch):
    scenario, plan = _one_hour_reclaim()
    initial_mass = scenario.space("M17-CLOSED-1").mass_t
    captured = {}

    def fake_solve(snapshot, options):
        captured["mass_t"] = snapshot.space("M17-CLOSED-1").mass_t
        captured["horizon"] = snapshot.sim.horizon_buckets
        captured["offset"] = options.horizon_offset
        return PlanResult(status="FEASIBLE", jobs=[], solver_wall_s=0.01)

    monkeypatch.setattr("mph_sim.simulator.solve_plan", fake_solve)
    event = SimEvent(
        "FAIL-23", "machine_failure", 0, "M17-RC01",
        minute=23, duration_minutes=60)
    sim = Simulator(scenario, plan, events=[event], auto_replan=True)

    sim.step()

    # 600 t/h for the completed 23-minute prefix; no mass is rolled back when
    # the event closes the job and the remaining horizon is solved again.
    assert sim.moved_by_job["J-EXACT"] == pytest.approx(230.0)
    assert sim.inv["M17-CLOSED-1"] == pytest.approx(initial_mass - 230.0)
    assert sim.delivered["MOLTEN-S"][0] == pytest.approx(230.0)
    assert sim.interrupted_jobs["J-EXACT"] == {
        "minute": 23,
        "reason": "machine_failure:M17-RC01",
        "completed_t": pytest.approx(230.0),
        "remaining_t": pytest.approx(370.0),
    }
    assert sim.jobs["J-EXACT"].status == "CANCELLED"
    assert sim.replans == 1
    assert captured == {
        "mass_t": pytest.approx(initial_mass - 230.0),
        "horizon": 1,
        "offset": 1,
    }
    event_log = next(record for record in sim.log
                     if record.kind == "EVENT")
    assert event_log.detail["event_minute"] == 23


def test_temporary_rate_reduction_expires_inside_same_bucket():
    scenario, plan = _one_hour_reclaim()
    event = SimEvent(
        "RATE-15", "reduced_rate", 0, "M17-RC01", magnitude=0.5,
        minute=15, duration_minutes=30)
    sim = Simulator(scenario, plan, events=[event], auto_replan=False)

    sim.step()

    # 15 min at 600 + 30 min at 300 + 15 min restored to 600 = 450 t.
    assert sim.moved_by_job["J-EXACT"] == pytest.approx(450.0)
    assert "M17-RC01" not in sim.machine_rate_factor
    assert "M17-RC01" not in sim.machine_rate_factor_until_minute


def test_overlapping_derates_restore_the_still_active_earlier_window():
    scenario, plan = _one_hour_reclaim()
    events = [
        SimEvent(
            "RATE-LONG", "reduced_rate", 0, "M17-RC01", magnitude=0.5,
            minute=10, duration_minutes=40),
        SimEvent(
            "RATE-SHORT", "reduced_rate", 0, "M17-RC01", magnitude=0.8,
            minute=20, duration_minutes=10),
    ]
    sim = Simulator(scenario, plan, events=events, auto_replan=False)

    sim.advance_minutes(30)

    # The short 80% cap has ended, but the original 50% cap remains through
    # minute 50 and is represented as such in a snapshot taken at minute 30.
    assert sim.rate_factor("M17-RC01") == pytest.approx(0.5)
    assert sim.machine_rate_factor_until_minute["M17-RC01"] == 50
    caps = [entry for entry in sim.snapshot_scenario().calendars
            if entry.scope == "M17-RC01" and entry.action == "RATE_CAP"]
    assert [(entry.end_minute, entry.rate_tph) for entry in caps] == [
        (50, pytest.approx(
            scenario.machine("M17-RC01").rate_max_tph * 0.5))]

    sim.advance_minutes(30)

    # 10 min at 600 + 40 min at 300 + 10 min at 600 = 400 t.
    assert sim.moved_by_job["J-EXACT"] == pytest.approx(400.0)
    assert "M17-RC01" not in sim.machine_rate_windows
    assert "M17-RC01" not in sim.machine_rate_factor
    assert "M17-RC01" not in sim.machine_rate_factor_until_minute


def test_iso_timestamp_takes_precedence_over_legacy_bucket_and_minute():
    scenario, plan = _one_hour_reclaim()
    event = SimEvent(
        "ISO-20", "machine_failure", 1, "M17-RC01",
        minute=50, duration_minutes=60,
        at_iso="2026-07-13T00:20:00")
    sim = Simulator(scenario, plan, events=[event], auto_replan=False)

    sim.step()

    assert sim.moved_by_job["J-EXACT"] == pytest.approx(200.0)
    assert sim.interrupted_jobs["J-EXACT"]["minute"] == 20


def test_effective_rate_uses_active_window_not_native_bucket_width():
    scenario, plan = _one_hour_reclaim()
    job = plan.jobs[0]
    job.tonnage_t = 150.0
    job.bucket_flows[0].tonnage_t = 150.0
    job.execution_windows[0].start_minute = 10
    job.execution_windows[0].duration_min = 15
    job.execution_windows[0].tonnage_t = 150.0
    sim = Simulator(scenario, plan, auto_replan=False)

    sim.step()

    assert sim.moved_by_job["J-EXACT"] == pytest.approx(150.0)
    assert sim.activity_log[0]["active_minutes"] == 15
    assert sim.activity_log[0]["rate_tph"] == pytest.approx(600.0)
    assert sim.activity_log[0]["bucket_average_tph"] == pytest.approx(150.0)
    assert sim.job_updates[0].effective_rate_tph == pytest.approx(600.0)


def test_mixed_mode_commits_at_15_minutes_and_replans_from_exact_origin(
        monkeypatch):
    scenario, plan = _one_hour_reclaim()
    scenario.sim.mixed_resolution_enabled = True
    scenario.sim.horizon_minutes = 120
    scenario.sim.near_term_step_min = 15
    scenario.sim.near_term_horizon_min = 60
    scenario.sim.far_term_step_min = 60
    scenario.sim.replan_interval_min = 15
    scenario.sim.commit_window_min = 30
    initial_mass = scenario.space("M17-CLOSED-1").mass_t
    calls = []

    def fake_solve(snapshot, options):
        calls.append({
            "minute": options.start_minute_offset,
            "mass_t": snapshot.space("M17-CLOSED-1").mass_t,
            "horizon_min": snapshot.sim.horizon_minutes,
        })
        return PlanResult(status="FEASIBLE", jobs=[], solver_wall_s=0.01)

    monkeypatch.setattr("mph_sim.simulator.solve_plan", fake_solve)
    sim = Simulator(
        scenario, plan,
        events=[SimEvent(
            "FAIL-23", "machine_failure", 0, "M17-RC01",
            minute=23, duration_minutes=60)],
        auto_replan=True)

    # One public call crosses two real 15-minute commits.  The first rolling
    # solve starts at minute 15; the off-grid failure stops movement at 23 and
    # its residual is replanned from the next valid fine-grid origin, minute 30.
    sim.advance_minutes(30)

    assert sim.current_minute == 30
    assert sim.bucket == 0
    assert sim.moved_by_job["J-EXACT"] == pytest.approx(230.0)
    assert sim.inv["M17-CLOSED-1"] == pytest.approx(initial_mass - 230.0)
    assert sim.interrupted_jobs["J-EXACT"]["minute"] == 23
    assert [call["minute"] for call in calls] == [15, 30]
    assert calls[-1]["mass_t"] == pytest.approx(initial_mass - 230.0)
    assert all(call["horizon_min"] == 120 for call in calls)
    assert sim.last_replan_minute == 30
    assert sim.next_replan_minute == 45


def test_mixed_snapshot_freezes_exact_commit_windows_not_two_hour_buckets():
    scenario, plan = _one_hour_reclaim()
    scenario.sim.mixed_resolution_enabled = True
    scenario.sim.horizon_minutes = 120
    scenario.sim.near_term_horizon_min = 60
    scenario.sim.commit_window_min = 30
    running = plan.jobs[0]
    running.status = "RUNNING"
    running.start_minute = 0
    running.end_minute = 60

    def planned(job_id, start, end, *, origin="AUTO", locked=False):
        job = copy.deepcopy(running)
        job.id = job_id
        job.status = "PLANNED"
        job.origin = origin
        job.locked = locked
        job.execution_windows = []
        job.bucket_flows = []
        job.start_minute = start
        job.end_minute = end
        job.start_bucket = start // 60
        job.end_bucket = max((end - 1) // 60, job.start_bucket)
        job.tonnage_t = job.rate_tph * (end - start) / 60.0
        return job

    jobs = [
        running,
        planned("IN", 30, 45),
        planned("BOUNDARY", 45, 60),
        planned("OUT", 46, 61),
        planned("HUMAN", 90, 105, origin="HUMAN"),
        planned("LOCKED", 120, 135, locked=True),
    ]
    sim = Simulator(
        scenario, PlanResult(status="FEASIBLE", jobs=jobs),
        auto_replan=False)
    sim.current_minute = 15

    snapshot = sim.snapshot_scenario()
    frozen = {
        fixed.id.removeprefix("FROZEN-"):
        (fixed.start_minute, fixed.end_minute)
        for fixed in snapshot.fixed_jobs
        if fixed.id.startswith("FROZEN-")
    }

    assert frozen == {
        "J-EXACT": (15, 60),
        "IN": (30, 45),
        "BOUNDARY": (45, 60),
        "HUMAN": (90, 105),
        "LOCKED": (120, 135),
    }
