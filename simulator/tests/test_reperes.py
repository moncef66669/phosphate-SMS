"""Operational repère coordinates over canonical metre geometry."""
import copy
import json

import pytest

from mph_sim import datasets
from mph_sim.adapters import job_to_plan_item
from mph_sim.coordinates import (REPERE_SECTION_COUNT, job_repere_label,
                                 repere_interval_to_x, repere_to_x,
                                 x_interval_to_reperes, x_to_repere)
from mph_sim.domain import FixedJob, Job, PlanResult, StockSpace
from mph_sim.integration import plan_revision_of
from mph_sim.optimizer import SolveOptions, solve_plan
from mph_sim.persistence import scenario_from_json, scenario_to_json
from mph_sim.validation import validate_scenario
from ui.layout_geometry import m16_geometry
from ui.process_mimic import (MimicOpts, build_process_mimic_state,
                              process_mimic_figure)
from ui.theme import jobs_to_gantt_df


def _space(**overrides):
    values = dict(
        id="P1", site="MPH", area="yard", kind="open",
        capacity_t=100_000, mass_t=50_000, grade="G",
        x_from=100.0, x_to=600.0,
    )
    values.update(overrides)
    return StockSpace(**values)


def test_default_repere_frame_has_1000_equal_intervals():
    sp = _space()
    assert REPERE_SECTION_COUNT == 1000
    assert [repere_to_x(sp, r) for r in (0, 250, 500, 750, 1000)] == \
        pytest.approx([100, 225, 350, 475, 600])
    assert [x_to_repere(sp, x) for x in (100, 225, 350, 475, 600)] == \
        [0, 250, 500, 750, 1000]
    assert repere_interval_to_x(sp, 400, 600) == pytest.approx((300, 400))


def test_reversed_repere_orientation_preserves_ascending_physical_x():
    sp = _space(repere_at_x_from=1000, repere_at_x_to=0)
    assert repere_to_x(sp, 0) == pytest.approx(600)
    assert repere_to_x(sp, 1000) == pytest.approx(100)
    assert repere_interval_to_x(sp, 400, 600) == pytest.approx((300, 400))
    assert x_interval_to_reperes(sp, 300, 400) == (400, 600)


def test_invalid_repere_ranges_are_rejected():
    sp = _space()
    with pytest.raises(ValueError):
        repere_to_x(sp, 1001)
    with pytest.raises(ValueError):
        repere_interval_to_x(sp, 600, 400)


def test_validation_requires_exact_integer_r0_r1000_convention():
    sc = datasets.sulphur_minimal()
    sc.spaces[0].repere_at_x_from = 100
    sc.spaces[0].repere_at_x_to = 1100
    assert "BAD_REPERE_FRAME" in {i.code for i in validate_scenario(sc)}

    sc = datasets.sulphur_minimal()
    sc.fixed_jobs = [
        FixedJob(
            "FX-FRACTIONAL", "M17-RC01", "reclaim", "M17-CLOSED-1",
            dest_id="MOLTEN-S", rate_tph=600, start_bucket=2, end_bucket=4,
            grade="S-STD", start_repere=400.5, stop_repere=600,
        )
    ]
    assert "BAD_REPERE_RANGE" in {i.code for i in validate_scenario(sc)}


def test_operator_fixed_job_keeps_grade_and_r400_r600():
    sc = datasets.sulphur_minimal()
    sc.fixed_jobs = [
        FixedJob(
            "FX-RANGE", "M17-RC01", "reclaim", "M17-CLOSED-1",
            dest_id="MOLTEN-S", rate_tph=600, start_bucket=2, end_bucket=4,
            grade="S-STD", start_repere=400, stop_repere=600,
        )
    ]
    plan = solve_plan(sc, SolveOptions(max_time_s=4))
    assert plan.status in ("OPTIMAL", "FEASIBLE")
    fixed = next(j for j in plan.jobs if j.id == "FX-RANGE")
    assert fixed.origin == "HUMAN"
    assert fixed.locked
    assert fixed.grade == "S-STD"
    assert job_repere_label(sc.space(fixed.space), fixed) == "R400–R600"
    # The common ASP wire contract remains physical metres.
    item = job_to_plan_item(sc, fixed)
    assert (item.StartX, item.StopX) == pytest.approx(
        repere_interval_to_x(sc.space(fixed.space), 400, 600))


def test_plan_identity_and_gantt_include_work_location():
    sc = datasets.mph_phosphate()
    sp = sc.space("HP-01")
    xa, xb = repere_interval_to_x(sp, 400, 600)
    job = Job(
        "J-R", "ST01-A", "stack", "HP-01", grade="TBT",
        rate_tph=1000, tonnage_t=1000, start_x=xa, stop_x=xb,
    )
    moved = copy.deepcopy(job)
    moved.start_x, moved.stop_x = repere_interval_to_x(sp, 500, 700)
    assert plan_revision_of([job]) != plan_revision_of([moved])
    row = jobs_to_gantt_df(sc, [job]).iloc[0]
    assert row.repere_range == "R400–R600"
    assert (row.start_repere, row.stop_repere) == (400, 600)


def test_old_scenario_json_loads_default_frame_and_new_fixed_range_roundtrips():
    sc = datasets.sulphur_minimal()
    payload = json.loads(scenario_to_json(sc))
    payload["spaces"][0].pop("repere_at_x_from")
    payload["spaces"][0].pop("repere_at_x_to")
    old = scenario_from_json(json.dumps(payload))
    assert old.spaces[0].repere_at_x_from == 0
    assert old.spaces[0].repere_at_x_to == 1000

    sc.fixed_jobs = [
        FixedJob("F", "M17-RC01", "reclaim", "M17-CLOSED-1",
                 grade="S-STD", start_repere=125, stop_repere=375)
    ]
    restored = scenario_from_json(scenario_to_json(sc))
    assert restored.fixed_jobs[0].grade == "S-STD"
    assert (restored.fixed_jobs[0].start_repere,
            restored.fixed_jobs[0].stop_repere) == (125, 375)


def test_mimic_draws_pile_ruler_work_range_and_machine_repere():
    sc = datasets.mph_phosphate()
    sp = sc.space("HP-01")
    xa, xb = repere_interval_to_x(sp, 400, 600)
    job = Job(
        "J-R", "ST01-A", "stack", "HP-01", grade="TBT",
        rate_tph=1000, tonnage_t=1000, start_bucket=0, end_bucket=0,
        start_x=xa, stop_x=xb,
    )
    plan = PlanResult("FEASIBLE", jobs=[job])
    state = build_process_mimic_state(sc, plan, basis="plan", bucket=0)
    assert state.machine_pos["ST01-A"].repere == 500
    fig = process_mimic_figure(
        state, m16_geometry(sc), MimicOpts(show_tags=True))
    labels = " ".join(str(a.text) for a in fig.layout.annotations)
    assert "R400–R600" in labels
    assert "R0" in labels and "R1000" in labels
    assert "ST01-A · R500" in labels
