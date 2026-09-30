# -*- coding: utf-8 -*-
"""Post-solve minute timing remains subordinate to native solver buckets."""
import copy
import io
import json

import pandas as pd

from mph_sim import datasets
from mph_sim.adapters import (SimulatedAspAdapter, job_to_plan_item,
                              job_to_plan_items)
from mph_sim.domain import (CalendarEntry, Job, JobBucketFlow,
                            JobExecutionWindow,
                            PlanResult)
from mph_sim.integration import build_sms_batches, plan_revision_of
from mph_sim.optimizer import SolveOptions, solve_plan
from mph_sim.timing import (BUCKET_ONLY, UNVALIDATED, VALIDATED,
                            display_execution_window,
                            refine_execution_timing,
                            timing_export_rows,
                            validate_execution_windows,
                            validated_execution_windows)
from ui.pages_meta import integration_zip_entries
from ui.theme import RED, gantt_figure, jobs_to_gantt_df


PATH_A = ["L-RCL-HP-01", "L-H-A", "L-RTE-A-PAP"]


def _reclaim(sc, job_id="J-TIME", machine="RC01-A", space="HP-01",
             tonnage=300.0, start=0, end=0, path=None):
    sp = sc.space(space)
    return Job(
        id=job_id, machine=machine, kind="reclaim", space=space,
        dest_id="PAP-1", grade=sp.grade, rate_tph=tonnage,
        tonnage_t=tonnage, start_bucket=start, end_bucket=end,
        path=list(path or PATH_A), start_x=sp.x_from, stop_x=sp.x_to)


def test_legacy_job_stays_on_explicit_bucket_envelope():
    sc = datasets.mph_phosphate()
    job = _reclaim(sc, start=2, end=3, tonnage=600)

    result = refine_execution_timing(sc, [job])

    assert result.valid
    assert job.execution_windows[0].status == BUCKET_ONLY
    assert not validated_execution_windows(job)
    row = jobs_to_gantt_df(sc, [job]).iloc[0]
    assert row.start == sc.sim.bucket_dt(2)
    assert row.end == sc.sim.bucket_dt(4)
    assert row.timing_status == BUCKET_ONLY
    assert row.solver_window == "B2–B3"
    item = job_to_plan_item(sc, job)
    assert item.StartTime == sc.sim.bucket_dt(2).isoformat()
    assert item.PlannedFlowRate == job.rate_tph


def test_exact_profile_gets_integer_minute_window_and_drives_views():
    sc = datasets.mph_phosphate()
    job = _reclaim(sc)
    job.bucket_flows = [JobBucketFlow(0, 300.0, PATH_A)]

    result = refine_execution_timing(sc, [job])

    assert result.valid and result.fallback_buckets == []
    [window] = validated_execution_windows(job)
    # The confirmed H/T13 path is the limiting 1,500 t/h resource:
    # 300 t / 1,500 t/h = 12 whole minutes.
    assert window.duration_min == 12
    assert window.rate_tph == 1500
    assert window.basis == "EARLIEST_FEASIBLE_MAX_RATE"
    row = jobs_to_gantt_df(sc, [job]).iloc[0]
    assert row.start == sc.sim.bucket_dt(0)
    assert row.end == sc.sim.bucket_dt(0) + pd.Timedelta(minutes=12)
    assert row.rate_tph == 1500
    assert row.solver_rate_tph == 300
    assert row.timing_status == VALIDATED
    item = job_to_plan_item(sc, job)
    assert item.StartTime == sc.sim.bucket_dt(0).isoformat()
    assert item.PlannedTonnage == 300
    assert item.PlannedFlowRate == 1500


def test_jobs_sharing_a_link_are_staggered_inside_the_bucket():
    sc = datasets.mph_phosphate()
    a = _reclaim(sc, "J-A", "RC01-A", "HP-01", tonnage=750)
    b_path = ["L-RCL-HP-03", "L-H-A", "L-RTE-A-PAP"]
    b = _reclaim(sc, "J-B", "RC01-B", "HP-03", tonnage=750, path=b_path)
    a.bucket_flows = [JobBucketFlow(0, 750, PATH_A)]
    b.bucket_flows = [JobBucketFlow(0, 750, b_path)]

    result = refine_execution_timing(sc, [a, b])

    assert result.valid
    assert result.fallback_buckets == []
    starts = []
    for job in (a, b):
        [window] = validated_execution_windows(job)
        starts.append(window.start_minute)
        assert window.duration_min == 30
        assert window.rate_tph == 1500
        assert window.basis == "EARLIEST_FEASIBLE_MAX_RATE"
    assert sorted(starts) == [0, 30]
    assert validate_execution_windows(sc, [a, b]) == []


def test_independent_jobs_overlap_at_the_bucket_start():
    sc = datasets.mph_phosphate()
    a = _reclaim(sc, "J-A", "RC01-A", "HP-01", tonnage=300)
    b_path = ["L-RCL-HP-03", "L-H-B", "L-RTE-B-PAP"]
    b = _reclaim(sc, "J-B", "RC01-B", "HP-03", tonnage=300, path=b_path)
    a.bucket_flows = [JobBucketFlow(0, 300, PATH_A)]
    b.bucket_flows = [JobBucketFlow(0, 300, b_path)]

    result = refine_execution_timing(sc, [a, b])

    assert result.valid and result.fallback_buckets == []
    assert [validated_execution_windows(job)[0].start_minute
            for job in (a, b)] == [0, 0]
    assert validate_execution_windows(sc, [a, b]) == []


def test_cross_space_job_reserves_translation_and_setup_minutes():
    sc = datasets.mph_phosphate()
    first = _reclaim(
        sc, "J-POS-A", "RC01-A", "HP-01",
        tonnage=1500, start=0, end=0)
    second_path = ["L-RCL-HP-05", "L-H-A", "L-RTE-A-PAP"]
    second = _reclaim(
        sc, "J-POS-B", "RC01-A", "HP-05",
        tonnage=300, start=1, end=1, path=second_path)
    first.start_x, first.stop_x = 0, 20
    second.start_x, second.stop_x = 280, 300
    first.bucket_flows = [JobBucketFlow(0, 1500, PATH_A)]
    second.bucket_flows = [JobBucketFlow(1, 300, second_path)]

    result = refine_execution_timing(sc, [first, second])

    assert result.valid
    assert result.fallback_buckets == []
    [first_window] = validated_execution_windows(first)
    [second_window] = validated_execution_windows(second)
    assert first_window.start_minute == 0
    assert first_window.duration_min == 60
    # 280 m / 30 m/min rounds up to 10 min, plus 10 min setup.
    assert second_window.start_minute == 80
    assert second_window.duration_min == 12
    assert second_window.positioning_start_minute == 60
    assert second_window.positioning_duration_min == 20
    assert not [
        issue for issue in validate_execution_windows(sc, [first, second])
        if issue.code == "MACHINE_POSITIONING_GAP"
    ]

    timeline = jobs_to_gantt_df(
        sc, [first, second], include_positioning=True)
    positioning = timeline[timeline.kind == "positioning"].iloc[0]
    assert positioning.start_time.endswith("01:00")
    assert positioning.end_time.endswith("01:20")
    assert positioning.positioning_duration_min == 20
    assert positioning.gap_status == "RESERVED_ESTIMATE"


def test_positioning_can_finish_early_before_a_blocked_calendar_bucket():
    sc = datasets.mph_phosphate()
    sc.calendars.append(CalendarEntry(
        "RC01-A", 1, 1, "planned maintenance"))
    first = _reclaim(
        sc, "J-POS-EARLY-A", "RC01-A", "HP-01",
        tonnage=300, start=0, end=0)
    second_path = ["L-RCL-HP-05", "L-H-A", "L-RTE-A-PAP"]
    second = _reclaim(
        sc, "J-POS-EARLY-B", "RC01-A", "HP-05",
        tonnage=1500, start=2, end=2, path=second_path)
    first.start_x, first.stop_x = 0, 20
    second.start_x, second.stop_x = 280, 300
    first.bucket_flows = [JobBucketFlow(0, 300, PATH_A)]
    second.bucket_flows = [JobBucketFlow(2, 1500, second_path)]

    result = refine_execution_timing(sc, [first, second])

    assert result.valid
    [second_window] = validated_execution_windows(second)
    assert second_window.start_minute == 120
    # The latest free 20-minute slot is 00:40–01:00. The machine then
    # waits through the blocked 01:00–02:00 bucket.
    assert second_window.positioning_start_minute == 40
    assert second_window.positioning_duration_min == 20
    assert validate_execution_windows(sc, [first, second]) == []
    timeline = jobs_to_gantt_df(
        sc, [first, second], include_positioning=True)
    positioning = timeline[timeline.kind == "positioning"].iloc[0]
    assert positioning.start_time.endswith("00:40")
    assert positioning.end_time.endswith("01:00")
    assert positioning.gap_status == "RESERVED_ESTIMATE"


def test_batch_gate_revalidates_stale_positioning_metadata():
    sc = datasets.mph_phosphate()
    first = _reclaim(
        sc, "J-POS-GATE-A", "RC01-A", "HP-01",
        tonnage=1500, start=0, end=0)
    second_path = ["L-RCL-HP-05", "L-H-A", "L-RTE-A-PAP"]
    second = _reclaim(
        sc, "J-POS-GATE-B", "RC01-A", "HP-05",
        tonnage=300, start=1, end=1, path=second_path)
    first.start_x, first.stop_x = 0, 20
    second.start_x, second.stop_x = 280, 300
    first.bucket_flows = [JobBucketFlow(0, 1500, PATH_A)]
    second.bucket_flows = [JobBucketFlow(1, 300, second_path)]
    assert refine_execution_timing(sc, [first, second]).valid

    # Simulate stale/corrupt stored metadata without changing its status flag.
    second.execution_windows[0].positioning_start_minute = 0
    assert second.execution_windows[0].status == VALIDATED
    assert "MACHINE_POSITIONING_GAP" in {
        issue.code for issue
        in validate_execution_windows(sc, [first, second])}

    state = build_sms_batches(
        sc, PlanResult("FEASIBLE", jobs=[first, second]), validate=False)
    batch = state.batches["MPH"]

    assert {item.JobId for item in batch.items} == {first.id}
    assert batch.item_states[second.id] == "Rejected (simulated)"
    assert any(
        issue.rule == "ExecutionTiming"
        and issue.item_id == second.id
        and "MACHINE_POSITIONING_GAP" in issue.message
        for issue in batch.validation_issues)


def _fallback_transition_jobs(sc, *, include_later=False):
    """Force a valid bucket-average fallback around one cross-space move."""
    hp05_path = ["L-RCL-HP-05", "L-H-A", "L-RTE-A-PAP"]
    hp04_path = ["L-RCL-HP-04", "L-H-A", "L-RTE-A-PAP"]
    first = _reclaim(
        sc, "J-FB-A", "RC01-A", "HP-01",
        tonnage=300, start=0, end=0)
    moving = _reclaim(
        sc, "J-FB-B", "RC01-A", "HP-05",
        tonnage=751, start=2, end=2, path=hp05_path)
    shared = _reclaim(
        sc, "J-FB-C", "RC01-B", "HP-04",
        tonnage=749, start=2, end=2, path=hp04_path)
    first.start_x, first.stop_x = 0, 20
    moving.start_x, moving.stop_x = 280, 300
    first.bucket_flows = [JobBucketFlow(0, 300, PATH_A)]
    moving.bucket_flows = [JobBucketFlow(2, 751, hp05_path)]
    shared.bucket_flows = [JobBucketFlow(2, 749, hp04_path)]
    jobs = [first, moving, shared]
    if include_later:
        later = _reclaim(
            sc, "J-FB-D", "RC01-A", "HP-01",
            tonnage=300, start=3, end=3)
        later.start_x, later.stop_x = 0, 20
        later.bucket_flows = [JobBucketFlow(3, 300, PATH_A)]
        jobs.append(later)
    return jobs


def test_fallback_reconciliation_keeps_early_calendar_free_positioning():
    sc = datasets.mph_phosphate()
    sc.calendars.append(CalendarEntry(
        "RC01-A", 1, 1, "planned maintenance"))
    jobs = _fallback_transition_jobs(sc)

    result = refine_execution_timing(sc, jobs)

    assert result.valid
    assert result.fallback_buckets == [2]
    assert {issue.code for issue in result.issues} == {
        "BUCKET_AVERAGE_FALLBACK"}
    [moving_window] = validated_execution_windows(jobs[1])
    # 751/1,500 and 749/1,500 need 31 + 30 integer minutes, so the
    # material windows fall back to concurrent bucket-average rates.
    assert moving_window.basis == "BUCKET_AVERAGE_FALLBACK"
    assert moving_window.start_minute == 120
    assert moving_window.duration_min == 60
    # Global reconciliation must retain a feasible move before blocked B1.
    assert moving_window.positioning_start_minute == 40
    assert moving_window.positioning_duration_min == 20
    assert validate_execution_windows(sc, jobs) == []


def test_fallback_reconciliation_reschedules_later_machine_work():
    sc = datasets.mph_phosphate()
    jobs = _fallback_transition_jobs(sc, include_later=True)

    result = refine_execution_timing(sc, jobs)

    assert result.valid
    assert result.fallback_buckets == [2]
    [fallback_window] = validated_execution_windows(jobs[1])
    [later_window] = validated_execution_windows(jobs[3])
    assert fallback_window.basis == "BUCKET_AVERAGE_FALLBACK"
    assert fallback_window.start_minute == 120
    assert fallback_window.duration_min == 60
    # The B2 fallback ends on HP-05 at 03:00. Reconciliation must move back
    # to HP-01 before starting the short B3 job, not leave it at 03:00.
    assert later_window.positioning_start_minute == 180
    assert later_window.positioning_duration_min == 20
    assert later_window.start_minute == 200
    assert later_window.duration_min == 12
    assert validate_execution_windows(sc, jobs) == []


def test_impossible_positioning_gap_is_flagged_without_material_fallback():
    sc = datasets.mph_phosphate()
    first = _reclaim(
        sc, "J-POS-FULL-A", "RC01-A", "HP-01",
        tonnage=1500, start=0, end=0)
    second_path = ["L-RCL-HP-05", "L-H-A", "L-RTE-A-PAP"]
    second = _reclaim(
        sc, "J-POS-FULL-B", "RC01-A", "HP-05",
        tonnage=1500, start=1, end=1, path=second_path)
    first.start_x, first.stop_x = 0, 20
    second.start_x, second.stop_x = 280, 300
    first.bucket_flows = [JobBucketFlow(0, 1500, PATH_A)]
    second.bucket_flows = [JobBucketFlow(1, 1500, second_path)]

    result = refine_execution_timing(sc, [first, second])

    assert not result.valid
    assert result.fallback_buckets == []
    assert {issue.code for issue in result.issues} == {
        "MACHINE_POSITIONING_GAP"}
    assert "only 0 min is available" in result.issues[0].message
    assert first.execution_windows[0].status == VALIDATED
    assert second.execution_windows[0].status == UNVALIDATED
    assert second.execution_windows[0].basis == "MAX_RATE_REFINED"
    assert first.tonnage_t + second.tonnage_t == 3000
    assert job_to_plan_items(sc, second) == []

    state = build_sms_batches(
        sc, PlanResult("FEASIBLE", jobs=[first, second]), validate=True)
    batch = state.batches["MPH"]
    assert {item.JobId for item in batch.items} == {first.id}
    assert batch.item_states[second.id] == "Rejected (simulated)"
    timing_issues = [
        issue for issue in batch.validation_issues
        if issue.rule == "ExecutionTiming"]
    assert len(timing_issues) == 1
    assert timing_issues[0].item_id == second.id
    assert "no ASP item was emitted" in timing_issues[0].message

    timeline = jobs_to_gantt_df(
        sc, [first, second], include_positioning=True)
    conflict = timeline[
        timeline.kind == "positioning conflict"].iloc[0]
    assert conflict.gap_status == "POSITIONING_CONFLICT"
    assert conflict.available_gap_min == 0
    assert conflict.status == "ATTENTION"
    assert pd.isna(conflict.tonnage_t)
    assert pd.isna(conflict.reserved_positioning_start_minute)
    assert conflict.reserved_positioning_duration_min == 0
    figure = gantt_figure(
        timeline, color_by="kind", group_by="machine")
    colors = {trace.name: trace.marker.color for trace in figure.data
              if getattr(trace, "name", "")}
    assert colors["positioning conflict"] == RED


def test_mixed_execution_status_displays_as_unvalidated():
    sc = datasets.mph_phosphate()
    job = _reclaim(sc, tonnage=600, start=0, end=1)
    job.bucket_flows = [
        JobBucketFlow(0, 300, PATH_A),
        JobBucketFlow(1, 300, PATH_A),
    ]
    job.execution_windows = [
        JobExecutionWindow(
            0, 0, 0, 12, 300, 1500, status=VALIDATED),
        JobExecutionWindow(
            1, 1, 60, 12, 300, 1500, status=UNVALIDATED),
    ]

    _window, status = display_execution_window(sc, job)

    assert status == UNVALIDATED
    assert job_to_plan_items(sc, job) == []


def test_validator_rejects_an_unphysical_validated_window():
    sc = datasets.mph_phosphate()
    job = _reclaim(sc)
    job.bucket_flows = [JobBucketFlow(0, 300, PATH_A)]
    job.execution_windows = [JobExecutionWindow(
        0, 0, 0, 1, 300, 18_000, status=VALIDATED)]

    issues = validate_execution_windows(sc, [job])

    assert {i.code for i in issues} >= {"MACHINE_RATE", "LINK_CAPACITY"}


def test_residence_validation_uses_matured_output_mass_after_stage_finish():
    sc = datasets.end_to_end()
    wash_path = [
        "L-LT-OL1-RB", "L-LT-RB45-COMMON", "L-LT-RB6-L1"]
    stack_path = ["L-LT-WIP1-SL11", "L-LT-SL11-P1"]

    def wash(job_id, start_minute):
        return Job(
            job_id, "120LG-RC-01", "reclaim", "110LG-OL1",
            grade="RAW-BT-GLOBAL", rate_tph=500, tonnage_t=250,
            start_bucket=0, end_bucket=0, path=wash_path,
            source_space="110LG-OL1", target_space="LT-WASH-L1-WIP",
            output_grade="TBT", output_tonnage_t=200,
            process_stage="LOU-WASH-L1",
            bucket_flows=[
                JobBucketFlow(0, 250, wash_path, output_tonnage_t=200)],
            execution_windows=[
                JobExecutionWindow(
                    0, 0, start_minute, 30, 250, 500, status=VALIDATED)],
        )

    downstream = Job(
        "J-STACK", "130LG-ST-01", "stack", "LT-WASH-L1-WIP",
        grade="TBT", rate_tph=1000, tonnage_t=300,
        start_bucket=1, end_bucket=1, path=stack_path,
        source_space="LT-WASH-L1-WIP", target_space="LT-P1",
        output_grade="TBT", output_tonnage_t=300,
        process_stage="LOU-PRODUCT-STACK-L1",
        depends_on_stage="LOU-WASH-L1", dependency_delay_min=30,
        bucket_flows=[
            JobBucketFlow(1, 300, stack_path, output_tonnage_t=300)],
        execution_windows=[
            JobExecutionWindow(1, 1, 60, 18, 300, 1000,
                               status=VALIDATED)],
    )
    jobs = [wash("J-WASH-1", 0), wash("J-WASH-2", 30), downstream]

    issues = validate_execution_windows(sc, jobs)

    assert "PROCESS_RESIDENCE_DELAY" in {issue.code for issue in issues}
    downstream.execution_windows[0].start_minute = 90
    assert validate_execution_windows(sc, jobs) == []


def test_validator_rejects_duplicate_same_job_bucket_windows():
    sc = datasets.mph_phosphate()
    job = _reclaim(sc, tonnage=500)
    job.bucket_flows = [JobBucketFlow(0, 500, PATH_A)]
    job.execution_windows = [
        JobExecutionWindow(
            0, 0, 0, 60, 500, 500, status=VALIDATED),
        JobExecutionWindow(
            0, 0, 0, 60, 500, 500, status=VALIDATED),
    ]

    issues = validate_execution_windows(sc, [job])

    assert {issue.code for issue in issues} >= {
        "DUPLICATE_BUCKET_WINDOW", "MACHINE_OVERLAP"}


def test_derated_solve_windows_respect_rounded_effective_machine_cap():
    sc = datasets.sulphur_minimal()
    machine = sc.machine("M17-RC01")
    factor = 0.7871
    expected_cap = round(machine.rate_max_tph * factor * sc.sim.dt_h) \
        / sc.sim.dt_h

    plan = solve_plan(sc, SolveOptions(
        max_time_s=3, machine_rate_factor={"M17-RC01": factor}))
    jobs = [job for job in plan.jobs if job.machine == "M17-RC01"]

    assert jobs
    assert {job.execution_rate_cap_tph for job in jobs} == {expected_cap}
    windows = [window for job in jobs
               for window in validated_execution_windows(job)]
    assert windows
    assert max(window.rate_tph for window in windows) <= \
        expected_cap + 1e-6
    assert not any(issue.code == "EFFECTIVE_MACHINE_RATE_CAP"
                   for issue in validate_execution_windows(sc, jobs))
    timeline = jobs_to_gantt_df(sc, jobs)
    assert set(timeline.execution_rate_cap_tph) == {expected_cap}
    assert max(item.PlannedFlowRate for job in jobs
               for item in job_to_plan_items(sc, job)) <= \
        expected_cap + 1e-6
    assert {row["execution_rate_cap_tph"]
            for row in timing_export_rows(jobs, sc)} == {expected_cap}


def test_unknown_machine_profile_fails_safely_as_bucket_only():
    sc = datasets.mph_phosphate()
    job = _reclaim(sc)
    job.machine = "GHOST-RC"
    job.bucket_flows = [JobBucketFlow(0, 300, PATH_A)]

    result = refine_execution_timing(sc, [job])

    assert not result.valid
    assert {issue.code for issue in result.issues} >= {"UNKNOWN_MACHINE"}
    assert job.execution_windows[0].status == BUCKET_ONLY
    assert not validated_execution_windows(job)


def test_unavailable_machine_window_never_remains_validated():
    sc = datasets.mph_phosphate()
    sc.machine("RC01-A").available = False
    job = _reclaim(sc)
    job.bucket_flows = [JobBucketFlow(0, 300, PATH_A)]

    result = refine_execution_timing(sc, [job])

    assert not result.valid
    assert {issue.code for issue in result.issues} >= {
        "MACHINE_UNAVAILABLE"}
    assert job.execution_windows[0].status == UNVALIDATED
    assert not validated_execution_windows(job)


def test_configured_exclusive_machine_group_cannot_be_minute_staggered():
    sc = datasets.end_to_end()
    jobs = []
    for index, machine in enumerate(("140LG-RC-01", "140LG-RC-02"), 1):
        job = Job(
            f"J-EXCL-{index}", machine, "dispatch", "LT-P1", dest_id="C1",
            grade=sc.space("LT-P1").grade, rate_tph=1000, tonnage_t=1000,
            start_bucket=0, end_bucket=0, start_x=0, stop_x=300,
            bucket_flows=[JobBucketFlow(0, 1000, [])])
        jobs.append(job)

    result = refine_execution_timing(sc, jobs)

    assert not result.valid
    assert {issue.code for issue in result.issues} >= {
        "EXCLUSIVE_MACHINE_GROUP"}
    assert all(job.execution_windows[0].status == UNVALIDATED
               for job in jobs)


def test_multi_bucket_job_exports_one_validated_item_per_window():
    sc = datasets.mph_phosphate()
    job = _reclaim(sc, tonnage=600, start=0, end=1)
    job.bucket_flows = [
        JobBucketFlow(0, 300, PATH_A),
        JobBucketFlow(1, 300, PATH_A),
    ]
    assert refine_execution_timing(sc, [job]).valid

    items = job_to_plan_items(sc, job)

    assert [item.JobId for item in items] == ["J-TIME-W01", "J-TIME-W02"]
    assert [item.StartTime for item in items] == [
        sc.sim.bucket_dt(0).isoformat(),
        sc.sim.bucket_dt(1).isoformat(),
    ]
    assert all(item.PlannedTonnage == 300 for item in items)
    assert all(item.PlannedFlowRate == 1500 for item in items)
    timeline = jobs_to_gantt_df(sc, [job])
    assert list(timeline.job) == ["J-TIME·W1", "J-TIME·W2"]
    assert set(timeline.parent_job) == {"J-TIME"}
    assert list(timeline.execution_duration_min) == [12, 12]
    assert list(timeline.start) == [
        sc.sim.bucket_dt(0), sc.sim.bucket_dt(1)]
    assert list(timeline.end) == [
        sc.sim.bucket_dt(0) + pd.Timedelta(minutes=12),
        sc.sim.bucket_dt(1) + pd.Timedelta(minutes=12)]
    assert timeline.tonnage_t.sum() == job.tonnage_t
    # The singular compatibility mapper never invents one continuous
    # minute-level interval for two separate execution segments.
    assert job_to_plan_item(sc, job).PlannedFlowRate == job.rate_tph


def test_exact_calendar_rule_detects_partial_bucket_intersection():
    sc = datasets.mph_phosphate()
    sp = sc.space("HP-04")
    job = Job(
        "J-CALENDAR", "ST01-C", "stack", "HP-04", grade=sp.grade,
        rate_tph=2400, tonnage_t=800, start_bucket=5, end_bucket=5,
        path=["L-STK-HP-04"], start_x=sp.x_from, stop_x=sp.x_to,
        bucket_flows=[JobBucketFlow(5, 800, ["L-STK-HP-04"])],
        execution_windows=[JobExecutionWindow(
            5, 5, 5 * 60 + 50, 20, 800, 2400, status=VALIDATED)])
    item = job_to_plan_item(sc, job)

    issues = SimulatedAspAdapter(sc).submit_plan_items([item])

    assert item.StartTime.endswith("05:50:00")
    assert any(issue.rule == "WorkCalendar" for issue in issues)


def test_execution_overlay_changes_integration_revision():
    sc = datasets.mph_phosphate()
    job = _reclaim(sc)
    job.bucket_flows = [JobBucketFlow(0, 300, PATH_A)]
    assert refine_execution_timing(sc, [job]).valid
    moved = copy.deepcopy(job)
    moved.execution_windows[0].start_minute += 1

    assert plan_revision_of([job]) != plan_revision_of([moved])


def test_integration_revision_covers_wire_fields_and_ignores_input_order():
    sc = datasets.mph_phosphate()
    first = _reclaim(sc, "J-REV-A", tonnage=300)
    second = _reclaim(sc, "J-REV-B", machine="RC01-B", space="HP-03",
                      tonnage=250)

    renamed = copy.deepcopy(first)
    renamed.id = "J-REV-RENAMED"
    rerated = copy.deepcopy(first)
    rerated.rate_tph = first.rate_tph - 1

    assert plan_revision_of([first]) != plan_revision_of([renamed])
    assert plan_revision_of([first]) != plan_revision_of([rerated])
    assert plan_revision_of([first, second]) == \
        plan_revision_of([second, first])


def test_integration_export_discloses_parent_bucket_and_timing_status():
    sc = datasets.mph_phosphate()
    job = _reclaim(sc)
    job.bucket_flows = [JobBucketFlow(0, 300, PATH_A)]
    assert refine_execution_timing(sc, [job]).valid
    plan = PlanResult("FEASIBLE", jobs=[job])

    state = build_sms_batches(sc, plan, validate=False)
    assert state.batches["MPH"].item_parent_jobs == {"J-TIME": "J-TIME"}
    entries = integration_zip_entries(sc, plan, None)
    rows = pd.read_csv(io.StringIO(
        entries["integration/MPH/asp_plan_items.csv"]))
    timing = pd.read_csv(io.StringIO(
        entries["integration/MPH/execution_windows.csv"]))
    batch = json.loads(entries["integration/MPH/plan_batch.json"])

    assert rows.loc[0, "parent_job_id"] == "J-TIME"
    assert rows.loc[0, "solver_buckets"] == "B0–B0"
    assert rows.loc[0, "timing_status"] == VALIDATED
    assert rows.loc[0, "PlannedFlowRate"] == 1500
    assert timing.loc[0, "end_time"].endswith("00:12:00")
    assert {
        "positioning_start_minute", "positioning_duration_min",
    } <= set(timing.columns)
    assert batch["item_parent_jobs"] == {"J-TIME": "J-TIME"}
    assert "execution_windows.csv" in entries


def test_default_s3_has_real_sub_hour_validated_execution_rows():
    sc = datasets.end_to_end()
    plan = solve_plan(sc, SolveOptions(max_time_s=3))
    state = build_sms_batches(sc, plan, validate=False)
    jobs = [job for batch in state.batches.values() for job in batch.jobs]
    timeline = jobs_to_gantt_df(sc, jobs)

    validated = timeline[timeline.timing_status == VALIDATED]
    assert not validated.empty
    assert (validated.execution_duration_min < 60).any()
    assert "BUCKET_AVERAGE_FALLBACK" not in \
        set(validated.timing_basis)
    assert sum(len(batch.items) for batch in state.batches.values()) == \
        len(validated)
    rejected = [
        job_id for batch in state.batches.values()
        for job_id, status in batch.item_states.items()
        if status == "Rejected (simulated)"]
    assert len(rejected) == 1
    # One saturated cross-pile boundary cannot contain the assumed
    # positioning duration. It stays a material job but is explicitly
    # non-dispatchable at minute resolution until targeted replanning.
    assert (timeline.timing_status == UNVALIDATED).sum() == 1
    assert plan.breakdown["minute_timing_errors"] == 1
    assert plan.breakdown["minute_timing_fallback_buckets"] == 0
