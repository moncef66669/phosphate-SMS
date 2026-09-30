"""Independent SMS-gate regressions; no optimizer or live connection required."""
import copy
from dataclasses import asdict, replace
from datetime import timedelta

import pytest

from mph_sim.adapters import AspPlanItem, SimulatedAspAdapter, _utc_time
from mph_sim.asp_inventory import InventoryProjection
from mph_sim.coordinates import repere_interval_to_x
from mph_sim.domain import (Grade, Machine, Scenario, Segment, StockSpace,
                            UNKNOWN_STOCK_QUALITY_KEY)


@pytest.fixture
def sc():
    return Scenario(
        "spatial gate", grades=[Grade("A", "A", {"BPL": 65}),
                                Grade("B", "B", {"BPL": 70})],
        spaces=[StockSpace("P", "MPH", "M16-humid", "closed", 2000, 1000,
                           "A", quality={"BPL": 65}, x_from=0, x_to=100)],
        segments=[Segment("P", 0, 50, 500, "A", {"BPL": 65}),
                  Segment("P", 50, 100, 500, "A", {"BPL": 65})],
        machines=[Machine("R", "MPH", "reclaimer", 0, 10000, ["P"]),
                  Machine("R2", "MPH", "reclaimer", 0, 10000, ["P"]),
                  Machine("S", "MPH", "stacker", 0, 10000, ["P"])])


def item(sc, ident="A", hour=0, **kw):
    args = dict(JobId=ident, Device="R", JobType="Reclaiming", Method="bench",
                Pile="P", Material="A", Grade="A", ReferenceLocation="plant",
                StartTime=(sc.sim.bucket_dt(0) + timedelta(hours=hour)).isoformat(),
                StartX=0, StopX=100, PlannedTonnage=600, PlannedFlowRate=1000)
    args.update(kw)
    return AspPlanItem(**args)


def errors(sc, items):
    return [i for i in SimulatedAspAdapter(sc).submit_plan_items(items)
            if i.severity == "ERROR"]


def test_sequential_overdraw_rejected_and_input_order_irrelevant(sc):
    a, b = item(sc), item(sc, "B", 1)
    assert [(i.item_id, i.rule) for i in errors(sc, [a, b])] == [
        ("B", "InventoryAvailability")]
    assert errors(sc, [b, a]) == errors(sc, [a, b])
    assert "400 t" in errors(sc, [a, b])[0].message


def test_repere_range_is_depleted_not_the_whole_pile(sc):
    lo, hi = repere_interval_to_x(sc.space("P"), 0, 500)
    a = item(sc, PlannedTonnage=400, StartX=lo, StopX=hi)
    b = item(sc, "B", 1, PlannedTonnage=300, StartX=lo, StopX=hi)
    c = item(sc, "C", 2, PlannedTonnage=500, StartX=50, StopX=100)
    assert [i.item_id for i in errors(sc, [a, b, c])] == ["B"]


def test_partially_overlapping_range_keeps_exact_remaining_mass(sc):
    a = item(sc, StopX=50, PlannedTonnage=400)
    b = item(sc, "B", 1, StartX=25, StopX=75, PlannedTonnage=301)
    assert "300 t" in errors(sc, [a, b])[0].message
    assert not errors(sc, [a, replace(b, PlannedTonnage=300)])


def test_nonuniform_segments_override_uniform_pile_approximation(sc):
    sc.segments = [Segment("P", 0, 10, 900, "A", {"BPL": 65}),
                   Segment("P", 10, 100, 100, "A", {"BPL": 65})]
    assert errors(sc, [item(sc, StartX=50, PlannedTonnage=60)])


def test_grade_checked_in_requested_range_not_dominant_pile_label(sc):
    sc.segments[1].grade = "B"
    sc.segments[1].quality = {"BPL": 70}
    b = item(sc, StartX=50, Grade="B", PlannedTonnage=400)
    assert not errors(sc, [b])
    assert any("Grade mismatch" in i.message for i in errors(
        sc, [replace(b, Grade="A")]))
    assert errors(sc, [item(sc, PlannedTonnage=400)])  # tied prevalent grades


def test_empty_gaps_are_not_filled_from_pile_total(sc):
    sc.segments = [Segment("P", 0, 50, 1000, "A", {"BPL": 65})]
    assert errors(sc, [item(sc, StartX=50, PlannedTonnage=1)])


def test_stack_available_only_at_completion_and_only_in_its_range(sc):
    sc.space("P").mass_t = 0
    sc.segments = []
    stack = item(sc, "S", Device="S", JobType="Stacking", StopX=50,
                 PlannedTonnage=1000, PlannedFlowRate=500)
    early = item(sc, "EARLY", 1, StopX=50, PlannedTonnage=100)
    ready = item(sc, "READY", 2, StopX=50, PlannedTonnage=1000)
    assert [i.item_id for i in errors(sc, [ready, early, stack])] == ["EARLY"]
    wrong_place = replace(ready, StartX=50, StopX=100)
    assert errors(sc, [stack, wrong_place])


@pytest.mark.parametrize("invalid", ["range", "rate", "calendar", "overlap"])
def test_invalid_stack_never_credits_material(sc, invalid):
    from mph_sim.domain import CalendarEntry
    sc.space("P").mass_t = 0
    sc.segments = []
    stack = item(sc, "S", Device="S", JobType="Stacking", PlannedTonnage=500)
    others = []
    if invalid == "range":
        stack.StartX = -1
    elif invalid == "rate":
        stack.PlannedFlowRate = -1
    elif invalid == "calendar":
        sc.calendars = [CalendarEntry("S", 0, 1, "maintenance")]
    else:
        others = [item(sc, "FIRST", Device="S", JobType="Stacking",
                       PlannedTonnage=100)]
    reclaim = item(sc, "R", 2, PlannedTonnage=500)
    assert any(i.item_id == "R" and i.rule == "InventoryAvailability"
               for i in errors(sc, others + [stack, reclaim]))


def test_invalid_reclaim_does_not_drain_stock(sc):
    wrong = item(sc, "WRONG", Grade="B")
    right = item(sc, "RIGHT", 1, PlannedTonnage=1000)
    assert [i.item_id for i in errors(sc, [wrong, right])] == ["WRONG"]


def test_simultaneous_reclaims_cannot_double_reserve(sc):
    a, b = item(sc), item(sc, "B", Device="R2")
    assert [i.item_id for i in errors(sc, [b, a])] == ["B"]


def test_pending_stacks_reserve_capacity(sc):
    sc.space("P").capacity_t = 1800
    a = item(sc, "A", Device="S", JobType="Stacking", PlannedTonnage=600)
    b = item(sc, "B", Device="R2", JobType="Stacking", PlannedTonnage=600)
    assert any("capacity" in i.message for i in errors(sc, [a, b]))


def test_reserved_reclaim_does_not_free_capacity_until_completion(sc):
    sc.space("P").capacity_t = 1000
    reclaim = item(sc, PlannedTonnage=1000, PlannedFlowRate=500)
    stack = item(sc, "S", 1, Device="S", JobType="Stacking", PlannedTonnage=500)
    assert any("capacity" in i.message for i in errors(sc, [reclaim, stack]))
    stack.StartTime = item(sc, hour=2).StartTime
    assert not errors(sc, [reclaim, stack])


def test_reverse_traversal_uses_same_inventory_interval(sc):
    forward = item(sc, StartX=10, StopX=90)
    assert errors(sc, [forward]) == errors(sc, [replace(
        forward, StartX=90, StopX=10)]) == []


def test_unknown_local_assay_cannot_be_hidden_by_pile_quality(sc):
    sc.config["ASP_ALLOW_ASSUMED_INVENTORY"] = False
    sc.segments[1].quality = {}
    assert any("analysis" in i.message for i in errors(sc, [item(sc)]))
    assert not errors(sc, [item(sc, StopX=50, PlannedTonnage=500)])


def test_explicit_unknown_quality_not_replaced_by_grade_catalog(sc):
    sc.segments[0].quality = {}
    sc.config[UNKNOWN_STOCK_QUALITY_KEY] = ["P"]
    assert errors(sc, [item(sc, StopX=50, PlannedTonnage=300)])


def test_aggregate_fallback_disclosed_and_can_be_disabled(sc):
    sc.segments = []
    assert not errors(sc, [item(sc)])
    issues = SimulatedAspAdapter(sc).submit_plan_items([item(sc)])
    assert any(i.rule == "InventoryBasis" and "uniform" in i.message for i in issues)
    sc.config["ASP_ALLOW_ASSUMED_INVENTORY"] = False
    assert errors(sc, [item(sc)])


@pytest.mark.parametrize("damage", ["overlap", "mass", "outside", "nan"])
def test_invalid_segment_snapshot_fails_closed(sc, damage):
    if damage == "overlap":
        sc.segments[1].x_from = 49
    elif damage == "mass":
        sc.segments[1].mass_t = 499
    elif damage == "outside":
        sc.segments[1].x_to = 101
    else:
        sc.segments[1].mass_t = float("nan")
    assert errors(sc, [item(sc)])


@pytest.mark.parametrize("field,value", [("StartX", float("nan")),
                                         ("PlannedTonnage", float("inf")),
                                         ("PlannedFlowRate", 0),
                                         ("StartTime", "broken")])
def test_invalid_item_fails_without_crashing(sc, field, value):
    assert errors(sc, [item(sc, **{field: value})])


def test_duplicate_ids_fail_closed(sc):
    assert any("Duplicate" in i.message for i in errors(
        sc, [item(sc), item(sc, hour=2)]))


def test_empty_range_never_divides_by_zero_for_tiny_mass(sc):
    sc.space("P").mass_t = 0
    sc.segments = []
    assert errors(sc, [item(sc, PlannedTonnage=1e-5)])


def test_missing_assay_strict_stack_not_relabelled_as_confirmed(sc):
    sc.space("P").mass_t = 0
    sc.segments = []
    sc.config["ASP_ALLOW_ASSUMED_INVENTORY"] = False
    stack = item(sc, "S", Device="S", JobType="Stacking", PlannedTonnage=500)
    reclaim = item(sc, "R", 1, PlannedTonnage=500)
    assert any(i.item_id == "R" and "analysis" in i.message
               for i in errors(sc, [stack, reclaim]))


def test_malformed_assay_fails_closed(sc):
    sc.segments[0].quality = {"BPL": "not a number"}
    assert any("analysis" in i.message for i in errors(sc, [item(sc)]))


def test_equivalent_timezone_instants_respect_completion(sc):
    sc.space("P").mass_t = 0
    sc.segments = []
    stack = item(sc, "S", Device="S", JobType="Stacking", PlannedTonnage=1000)
    start = _utc_time(stack.StartTime)
    from datetime import timezone
    local = (start + timedelta(hours=1)).astimezone(timezone(timedelta(hours=1)))
    reclaim = item(sc, "R", StartTime=local.isoformat(), PlannedTonnage=1000)
    assert not errors(sc, [reclaim, stack])


def test_evaluation_is_repeatable_and_does_not_mutate_inputs(sc):
    before = copy.deepcopy(asdict(sc))
    jobs = [item(sc), item(sc, "B", 1)]
    adapter = SimulatedAspAdapter(sc)
    assert adapter.submit_plan_items(jobs) == adapter.submit_plan_items(jobs)
    assert asdict(sc) == before


def test_conservation_and_disjoint_ranges(sc):
    projection = InventoryProjection(sc)
    stack = item(sc, "S", Device="S", JobType="Stacking", PlannedTonnage=200)
    projection.apply(stack, _utc_time(item(sc, hour=1).StartTime))
    projection.advance(_utc_time(item(sc, hour=1).StartTime))
    for n in range(10):
        job = item(sc, str(n), StartX=n*10, StopX=(n+1)*10, PlannedTonnage=120)
        assert not projection.apply(job, _utc_time(item(sc, hour=2).StartTime))
    assert sum(s.mass_t for s in projection.lots["P"]) == pytest.approx(0)
    assert projection.reserved_reclaim["P"] == pytest.approx(1200)
    projection.advance(_utc_time(item(sc, hour=2).StartTime))
    assert projection.reserved_reclaim["P"] == pytest.approx(0)


def test_batch_ui_marks_parent_of_overdraw_as_rejected():
    from mph_sim import datasets
    from mph_sim.domain import Job, PlanResult
    from mph_sim.integration import build_sms_batches
    sc = datasets.mph_phosphate()
    sc.calendars = []
    jobs = [Job(ident, "RC01-A", "reclaim", "HP-02", grade="BT",
                rate_tph=1000, tonnage_t=54000, start_bucket=hour,
                end_bucket=hour+53, start_x=0, stop_x=300)
            for ident, hour in [("A", 0), ("B", 100)]]
    batch = build_sms_batches(sc, PlanResult("FEASIBLE", jobs=jobs)).batches["MPH"]
    assert batch.item_states["A"] == "Accepted (simulated)"
    assert batch.item_states["B"] == "Rejected (simulated)"
