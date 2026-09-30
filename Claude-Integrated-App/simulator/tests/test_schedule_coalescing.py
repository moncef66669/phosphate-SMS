# -*- coding: utf-8 -*-
"""Focused contracts for the operator schedule's continuous-job view."""

import pandas as pd
import pytest

from mph_sim import datasets
from mph_sim.domain import Job, JobBucketFlow, JobExecutionWindow
from mph_sim.timing import VALIDATED
from ui.theme import jobs_to_gantt_df


PATH = ["L-RCL-HP-01", "L-H-A", "L-RTE-A-PAP"]


def _two_slice_job(*, second_start_minute: int = 60) -> Job:
    """One parent job with exact, independently validated bucket slices."""
    return Job(
        id="J-CONTINUOUS",
        machine="RC01-A",
        kind="reclaim",
        space="HP-01",
        dest_id="PAP-1",
        grade="TBT",
        rate_tph=675.0,
        tonnage_t=1350.0,
        output_tonnage_t=1100.0,
        start_bucket=0,
        end_bucket=1,
        path=list(PATH),
        bucket_flows=[
            JobBucketFlow(
                bucket=0, tonnage_t=600.0, path=list(PATH),
                output_tonnage_t=500.0),
            JobBucketFlow(
                bucket=1, tonnage_t=750.0, path=list(PATH),
                output_tonnage_t=600.0),
        ],
        execution_windows=[
            JobExecutionWindow(
                solver_start_bucket=0,
                solver_end_bucket=0,
                start_minute=0,
                duration_min=60,
                tonnage_t=600.0,
                rate_tph=600.0,
                basis="EXACT_TEST_TIMING",
                status=VALIDATED,
            ),
            JobExecutionWindow(
                solver_start_bucket=1,
                solver_end_bucket=1,
                start_minute=second_start_minute,
                duration_min=30,
                tonnage_t=750.0,
                rate_tph=1500.0,
                basis="EXACT_TEST_TIMING",
                status=VALIDATED,
            ),
        ],
    )


def test_touching_validated_slices_render_as_one_continuous_job():
    sc = datasets.mph_phosphate()

    timeline = jobs_to_gantt_df(
        sc, [_two_slice_job()], coalesce_execution_windows=True)

    assert len(timeline) == 1
    row = timeline.iloc[0]
    assert row.parent_job == "J-CONTINUOUS"
    assert row.start == sc.sim.bucket_dt(0)
    assert row.end == sc.sim.bucket_dt(0) + pd.Timedelta(minutes=90)
    assert row.execution_duration_min == 90
    assert row.tonnage_t == pytest.approx(1350.0)
    assert row.output_tonnage_t == pytest.approx(1100.0)
    # The displayed rate is the physical rate across the combined work time,
    # not an arithmetic average of the two slice rates.
    assert row.rate_tph == pytest.approx(900.0)
    assert row.timing_status == VALIDATED
    assert row.timing_basis == "EXACT_TEST_TIMING"
    assert row.execution_window_count == 2
    assert all(link_id in row.path for link_id in PATH)
    assert all(row.path.count(link_id) == 1 for link_id in PATH)


def test_real_gap_between_slices_remains_visible_as_two_bars():
    sc = datasets.mph_phosphate()

    timeline = jobs_to_gantt_df(
        sc, [_two_slice_job(second_start_minute=70)],
        coalesce_execution_windows=True)

    assert len(timeline) == 2
    assert list(timeline.execution_duration_min) == [60, 30]
    assert timeline.iloc[0].end == sc.sim.bucket_dt(0) + \
        pd.Timedelta(minutes=60)
    assert timeline.iloc[1].start == sc.sim.bucket_dt(0) + \
        pd.Timedelta(minutes=70)
    assert timeline.tonnage_t.sum() == pytest.approx(1350.0)
    assert timeline.output_tonnage_t.sum() == pytest.approx(1100.0)


def test_touching_slices_with_different_routes_do_not_merge():
    sc = datasets.mph_phosphate()
    job = _two_slice_job()
    alternate_path = ["L-RCL-HP-01", "L-H-B", "L-RTE-B-PAP"]
    job.bucket_flows[1].path = alternate_path

    timeline = jobs_to_gantt_df(
        sc, [job], coalesce_execution_windows=True)

    assert len(timeline) == 2
    assert timeline.iloc[0].path != timeline.iloc[1].path
    assert all(link_id in timeline.iloc[0].path for link_id in PATH)
    assert all(link_id in timeline.iloc[1].path
               for link_id in alternate_path)


def test_touching_slices_with_different_timing_basis_merge_and_disclose_both():
    """The refinement basis is metadata, not a physical event.

    Two exactly touching hours of one activity group are one continuous run
    whether the minute timing of each hour came from maximum-rate compression,
    the native bucket rate or stable-run expansion.  Splitting the bar on that
    difference cut continuous runs at hours where only the derivation changed,
    which is one of the display-level contributors to S0 job fragmentation.
    The combined row must still disclose every contributing basis.
    """
    sc = datasets.mph_phosphate()
    job = _two_slice_job()
    job.execution_windows[1].basis = "VALIDATED_FALLBACK_TIMING"

    timeline = jobs_to_gantt_df(
        sc, [job], coalesce_execution_windows=True)

    [row] = timeline.to_dict("records")
    assert row["timing_basis"] == (
        "MIXED: EXACT_TEST_TIMING + VALIDATED_FALLBACK_TIMING")
    assert row["timing_status"] == VALIDATED
    assert row["execution_window_count"] == 2
    # No mass and no elapsed time is lost or invented by the merge.
    assert row["tonnage_t"] == pytest.approx(job.tonnage_t)
    split = jobs_to_gantt_df(sc, [job], coalesce_execution_windows=False)
    assert len(split) == 2
    assert row["execution_duration_min"] ==         split.execution_duration_min.sum()


def test_single_sub_hour_window_stays_sub_hour_in_continuous_view():
    sc = datasets.mph_phosphate()
    job = Job(
        id="J-SHORT",
        machine="RC01-A",
        kind="reclaim",
        space="HP-01",
        dest_id="PAP-1",
        grade="TBT",
        rate_tph=1500.0,
        tonnage_t=300.0,
        start_bucket=0,
        end_bucket=0,
        path=list(PATH),
        bucket_flows=[JobBucketFlow(0, 300.0, list(PATH))],
        execution_windows=[JobExecutionWindow(
            solver_start_bucket=0,
            solver_end_bucket=0,
            start_minute=7,
            duration_min=12,
            tonnage_t=300.0,
            rate_tph=1500.0,
            basis="EXACT_TEST_TIMING",
            status=VALIDATED,
        )],
    )

    [row] = jobs_to_gantt_df(
        sc, [job], coalesce_execution_windows=True).to_dict("records")

    assert row["execution_duration_min"] == 12
    assert row["start"] == sc.sim.bucket_dt(0) + pd.Timedelta(minutes=7)
    assert row["end"] == sc.sim.bucket_dt(0) + pd.Timedelta(minutes=19)


def test_raw_execution_slice_mode_remains_available_for_diagnostics():
    sc = datasets.mph_phosphate()

    raw = jobs_to_gantt_df(
        sc, [_two_slice_job()], coalesce_execution_windows=False)

    assert len(raw) == 2
    assert list(raw.execution_duration_min) == [60, 30]
    assert list(raw.tonnage_t) == pytest.approx([600.0, 750.0])
    assert list(raw.output_tonnage_t) == pytest.approx([500.0, 600.0])
    assert set(raw.parent_job) == {"J-CONTINUOUS"}
