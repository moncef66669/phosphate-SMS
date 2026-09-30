"""Rejected producer jobs cannot certify dispatchable downstream material."""
from mph_sim import datasets
from mph_sim.domain import Job, JobBucketFlow, JobExecutionWindow, PlanResult
from mph_sim.integration import build_sms_batches
from mph_sim.timing import (
    UNVALIDATED, VALIDATED, refine_execution_timing,
    validate_execution_windows, validated_execution_windows,
)


def _job(scenario, identifier, machine, space, bucket, tonnes):
    stream = "A" if machine == "RC01-A" else "B"
    path = [f"L-RCL-{space}", f"L-H-{stream}", f"L-RTE-{stream}-PAP"]
    pile = scenario.space(space)
    result = Job(
        id=identifier, machine=machine, kind="reclaim", space=space,
        dest_id="PAP-1", grade=pile.grade, rate_tph=tonnes,
        tonnage_t=tonnes, start_bucket=bucket, end_bucket=bucket,
        path=path, start_x=pile.x_from, stop_x=pile.x_to)
    result.bucket_flows = [JobBucketFlow(bucket, tonnes, path)]
    return result


def test_validator_does_not_treat_rejected_production_as_completed():
    scenario = datasets.mph_phosphate()
    producer = _job(scenario, "PRODUCER", "RC01-A", "HP-01", 0, 300)
    producer.process_stage = "A"
    producer.target_space = "HP-03"
    producer.execution_windows = [JobExecutionWindow(
        0, 0, 0, 12, 300, 1500, status=UNVALIDATED)]
    consumer = _job(scenario, "CONSUMER", "RC01-B", "HP-03", 1, 300)
    consumer.depends_on_stage = "A"
    consumer.source_space = "HP-03"
    consumer.execution_windows = [JobExecutionWindow(
        1, 1, 60, 12, 300, 1500, status=VALIDATED)]
    issues = validate_execution_windows(scenario, [producer, consumer])
    assert any(issue.code == "MISSING_PROCESS_DEPENDENCY"
               and issue.job_id == "CONSUMER" for issue in issues)


def test_timing_holds_propagate_through_multiple_process_stages():
    scenario = datasets.mph_phosphate()
    first = _job(scenario, "FIRST", "RC01-A", "HP-01", 0, 1500)
    producer = _job(scenario, "PRODUCER", "RC01-A", "HP-05", 1, 1500)
    producer.process_stage = "A"
    producer.target_space = "HP-03"
    consumer = _job(scenario, "CONSUMER", "RC01-B", "HP-03", 2, 100)
    consumer.process_stage = "B"
    consumer.depends_on_stage = "A"
    consumer.source_space = "HP-03"
    consumer.target_space = "HP-02"
    downstream = _job(scenario, "DOWNSTREAM", "RC01-A", "HP-02", 3, 100)
    downstream.depends_on_stage = "B"
    downstream.source_space = "HP-02"
    jobs = [first, producer, consumer, downstream]
    result = refine_execution_timing(scenario, jobs)
    assert validated_execution_windows(first)
    for job in (producer, consumer, downstream):
        assert not validated_execution_windows(job)
        assert any(window.status == UNVALIDATED for window in job.execution_windows)
    assert any(issue.code == "MACHINE_POSITIONING_GAP"
               and issue.job_id == "PRODUCER" for issue in result.issues)
    assert any(issue.code == "MISSING_PROCESS_DEPENDENCY"
               and issue.job_id == "DOWNSTREAM" for issue in result.issues)


def test_export_gate_holds_descendants_of_corrupted_previously_valid_metadata():
    scenario = datasets.mph_phosphate()
    producer = _job(scenario, "PRODUCER", "RC01-A", "HP-01", 0, 300)
    producer.process_stage, producer.target_space = "A", "HP-03"
    consumer = _job(scenario, "CONSUMER", "RC01-B", "HP-03", 1, 300)
    consumer.process_stage, consumer.depends_on_stage = "B", "A"
    consumer.source_space, consumer.target_space = "HP-03", "HP-02"
    downstream = _job(scenario, "DOWNSTREAM", "RC01-A", "HP-02", 2, 300)
    downstream.depends_on_stage, downstream.source_space = "B", "HP-02"
    jobs = [producer, consumer, downstream]
    assert refine_execution_timing(scenario, jobs).valid
    producer.execution_windows[0].duration_min = 1
    assert producer.execution_windows[0].status == VALIDATED
    issues = validate_execution_windows(scenario, jobs)
    assert {issue.job_id for issue in issues if issue.severity == "ERROR"} == {
        "PRODUCER", "CONSUMER", "DOWNSTREAM"}
    integration = build_sms_batches(scenario, PlanResult("FEASIBLE", jobs=jobs), validate=False)
    assert integration.batches["MPH"].items == []
    assert set(integration.batches["MPH"].item_states.values()) == {"Rejected (simulated)"}


def test_early_resource_fallback_does_not_create_false_downstream_fallback():
    scenario = datasets.mph_phosphate()
    producer = _job(scenario, "PRODUCER", "RC01-A", "HP-01", 0, 751)
    producer.process_stage, producer.target_space = "A", "HP-03"
    crowded = _job(scenario, "CROWDED", "RC01-B", "HP-03", 0, 749)
    # These exact masses need 31 + 30 minutes on the 1,500 t/h link,
    # but their simultaneous solver-average rates fit for a whole hour.
    crowded.path = ["L-RCL-HP-03", "L-H-A", "L-RTE-A-PAP"]
    crowded.bucket_flows = [JobBucketFlow(0, 749, crowded.path)]
    first = _job(scenario, "FIRST", "RC01-A", "HP-01", 1, 1500)
    moving = _job(scenario, "MOVING", "RC01-A", "HP-05", 2, 100)
    consumer = _job(scenario, "CONSUMER", "RC01-B", "HP-03", 2, 751)
    consumer.depends_on_stage, consumer.source_space = "A", "HP-03"
    jobs = [producer, crowded, first, moving, consumer]

    result = refine_execution_timing(scenario, jobs)

    assert result.valid
    assert result.fallback_buckets == [0]
    assert all(validated_execution_windows(job) for job in jobs)
    [move_window] = validated_execution_windows(moving)
    assert move_window.duration_min < 60
    assert move_window.positioning_duration_min > 0
    assert move_window.start_minute > 120
    assert validate_execution_windows(scenario, jobs) == []


def test_short_first_retry_preserves_move_under_saturated_shared_receiving_load():
    """Reproduce low-stock contention without a solver-specific incumbent."""
    scenario = datasets.mph_phosphate()
    first = _job(scenario, "FIRST", "RC01-B", "HP-03", 0, 1500)
    moving = _job(scenario, "MOVING", "RC01-B", "HP-04", 1, 1125)
    short = _job(scenario, "SHORT", "RC01-A", "HP-01", 1, 375)
    moving.path = ["L-RCL-HP-04", "L-H-A", "L-RTE-A-PAP"]
    moving.bucket_flows = [JobBucketFlow(1, 1125, moving.path)]
    # Equal work coordinates isolate the ten-minute setup requirement.
    first.start_x = first.stop_x = moving.start_x = moving.stop_x = 100
    jobs = [first, moving, short]

    result = refine_execution_timing(scenario, jobs)

    assert result.valid and result.fallback_buckets == []
    [short_window] = validated_execution_windows(short)
    [move_window] = validated_execution_windows(moving)
    assert (short_window.start_minute, short_window.duration_min) == (60, 15)
    assert (move_window.start_minute, move_window.duration_min) == (75, 45)
    assert move_window.positioning_duration_min == 10
    assert "shortest-first" in move_window.note
    assert validate_execution_windows(scenario, jobs) == []
    # Retrying or reversing the input list must produce the same accepted
    # windows and leave the hourly masses/paths intact.
    before = {job.id: [(w.start_minute, w.duration_min) for w in job.execution_windows]
              for job in jobs}
    assert refine_execution_timing(scenario, list(reversed(jobs))).valid
    assert before == {job.id: [(w.start_minute, w.duration_min) for w in job.execution_windows]
                      for job in jobs}
    assert moving.bucket_flows[0].tonnage_t == 1125
