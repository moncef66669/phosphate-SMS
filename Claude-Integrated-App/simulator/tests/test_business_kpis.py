"""Business outcome metrics must not reward late, unknown or duplicate mass."""
import pytest

from mph_sim.domain import (Scenario, Site, StockSpace, Grade, Destination,
                            SpecRange, SimParams, Job, JobBucketFlow, PlanResult)
from mph_sim.kpis import compute_kpis
from mph_sim.simulator import Simulator


def fixture(delivered=(100, 100)):
    sc = Scenario("accounting", sites=[Site("X", "X", "hub")],
                  sim=SimParams(horizon_buckets=2))
    sc.grades = [Grade("G", "G", {"BPL": 70., "Cd": 20.})]
    sc.spaces = [StockSpace("P", "X", "humid", "open", 1000., 800., "G")]
    sc.destinations = [Destination("D", "X", "DEST:D", demand_default_tph=100,
                                  spec=[SpecRange("BPL", pmin=68), SpecRange("Cd", pmax=25)])]
    job = Job("J", "M", "reclaim", "P", dest_id="D", grade="G",
              start_bucket=0, end_bucket=1, rate_tph=sum(delivered)/2,
              tonnage_t=sum(delivered),
              bucket_flows=[JobBucketFlow(t, m) for t, m in enumerate(delivered)])
    plan = PlanResult("FEASIBLE", jobs=[job], delivered={"D": list(delivered)},
                      inventory={"P": [800, 700, 600]})
    return sc, plan


def test_late_delivery_does_not_cancel_an_earlier_shortage():
    sc, plan = fixture((0, 200))
    k = compute_kpis(sc, plan)
    assert k["service_pct"] == 50
    assert k["unmet_t"] == 100
    assert k["final_product_on_time_t"] == 100


def test_process_feed_is_not_counted_again_as_final_product():
    sc, plan = fixture()
    sc.destinations.append(Destination("WASH", "X", "DEST:WASH",
                                      demand_default_tph=500, demand_kind="process_feed"))
    plan.delivered["WASH"] = [0, 1000]
    k = compute_kpis(sc, plan)
    assert k["final_product_delivered_t"] == 200
    assert k["service_pct"] == 100  # process feed retains horizon-total contract


def test_quality_counts_tonnes_and_unknowns_instead_of_equal_size_buckets():
    sc, plan = fixture((10, 190))
    sc.spaces.append(StockSpace("Q", "X", "humid", "open", 1000, 800, "G",
                               quality={"BPL": 70., "Cd": 30.}))
    plan.jobs[0].bucket_flows = [JobBucketFlow(0, 10)]
    plan.jobs.append(Job("J2", "M", "reclaim", "Q", dest_id="D", grade="G",
                         start_bucket=1, end_bucket=1, rate_tph=190, tonnage_t=190,
                         bucket_flows=[JobBucketFlow(1, 190)]))
    k = compute_kpis(sc, plan)
    assert k["onspec_pct"] == 50
    assert k["on_spec_mass_pct"] == 5
    assert k["off_spec_mass_t"] == 190
    del sc.spaces[1].quality["Cd"]
    k = compute_kpis(sc, plan)
    assert k["quality_unknown_t"] == 190
    assert k["off_spec_mass_t"] == 0
    assert k["on_spec_mass_pct"] == 5


def test_selected_blend_quality_supersedes_empty_initial_pile_snapshot():
    sc, plan = fixture()
    sc.spaces[0].quality = {"BPL": 0., "Cd": 100.}
    plan.blend_campaigns["P"] = {"ready_minute": 0,
                                      "resulting_quality": {"BPL": 70., "Cd": 20.}}
    assert compute_kpis(sc, plan)["on_spec_mass_pct"] == 100
    plan.blend_campaigns["P"]["ready_minute"] = 60
    assert compute_kpis(sc, plan)["quality_unknown_t"] == 100


def test_empty_simulation_has_no_future_demand_or_division_by_zero():
    sc, plan = fixture()
    sim = Simulator(sc, plan)
    k = compute_kpis(sc, plan, sim)
    assert k["evaluated_buckets"] == 0
    assert k["final_product_demand_t"] == 0
    assert k["on_spec_mass_pct"] is None
    assert k["inventory_total_end_t"] == 800


def test_missing_one_component_assay_never_certifies_the_whole_mix():
    sc, plan = fixture()
    sc.spaces.append(StockSpace("Q", "X", "humid", "open", 1000, 800, "G",
                               quality={"BPL": 70.}))
    plan.jobs[0].bucket_flows = [JobBucketFlow(0, 50), JobBucketFlow(1, 50)]
    plan.jobs.append(Job("J2", "M2", "reclaim", "Q", dest_id="D", grade="G",
                         start_bucket=0, end_bucket=1, rate_tph=50, tonnage_t=100))
    assert compute_kpis(sc, plan)["quality_unknown_t"] == 200


def test_chemistry_uses_dry_solids_and_moisture_physical_mass():
    sc, plan = fixture((100, 0))
    sc.spaces[0].quality = {"BPL": 60., "Cd": 20., "moisture": 0.}
    sc.spaces.append(StockSpace("Q", "X", "humid", "open", 1000, 800, "G",
                               quality={"BPL": 80., "Cd": 20., "moisture": 50.}))
    plan.jobs[0].bucket_flows = [JobBucketFlow(0, 50)]
    plan.jobs.append(Job("J2", "M2", "reclaim", "Q", dest_id="D", grade="G",
                         start_bucket=0, end_bucket=0, rate_tph=50, tonnage_t=50))
    assert compute_kpis(sc, plan)["on_spec_mass_pct"] == 0  # 66.67 BPL, not 70


def test_zero_deliveries_are_not_reported_as_proven_mass_compliance():
    sc, plan = fixture((0, 0))
    assert compute_kpis(sc, plan)["on_spec_mass_pct"] is None


def test_raw_screened_moisture_fallback_uses_raw_stage():
    from mph_sim.kpis import planned_delivery_quality
    sc, plan = fixture((100, 0))
    sc.grades[0].material_class = "raw_screened"
    sc.grades[0].raw_moisture_pct = 0
    sc.grades[0].product_moisture_pct = 50
    sc.spaces[0].quality = {"BPL": 60.}
    sc.grades.append(Grade("G2", "Wet raw", {"BPL": 80.}, material_class="raw_screened",
                           raw_moisture_pct=50, product_moisture_pct=0))
    sc.spaces.append(StockSpace("Q", "X", "humid", "open", 1000, 800, "G2"))
    plan.jobs[0].bucket_flows = [JobBucketFlow(0, 50)]
    plan.jobs.append(Job("J2", "M2", "reclaim", "Q", dest_id="D", grade="G2",
                         start_bucket=0, end_bucket=0, rate_tph=50, tonnage_t=50))
    assert planned_delivery_quality(sc, plan, sc.destinations[0], 0)["BPL"] == pytest.approx(200 / 3)


def test_replan_uses_relative_delivery_series_and_absolute_job_times():
    sc, plan = fixture((100,))
    plan.horizon_offset = 1
    plan.jobs[0].start_bucket = plan.jobs[0].end_bucket = 1
    plan.jobs[0].bucket_flows = [JobBucketFlow(1, 100)]
    k = compute_kpis(sc, plan)
    assert k["evaluated_buckets"] == 1
    assert k["final_product_on_time_t"] == 100
    assert k["on_spec_mass_pct"] == 100


def test_process_feed_cannot_borrow_mass_from_another_day():
    sc, plan = fixture()
    sc.sim.horizon_buckets = 48
    sc.destinations[0].demand_kind = "process_feed"
    plan.delivered["D"] = [0] * 24 + [200] * 24
    k = compute_kpis(sc, plan)
    assert k["service_pct"] == 50
    assert k["unmet_t"] == 2400


def test_quality_averaging_window_is_honored_when_enabled():
    sc, plan = fixture()
    sc.spaces[0].quality = {"BPL": 66., "Cd": 20.}
    sc.spaces.append(StockSpace("Q", "X", "humid", "open", 1000, 800, "G",
                               quality={"BPL": 74., "Cd": 20.}))
    plan.jobs[0].bucket_flows = [JobBucketFlow(0, 100)]
    plan.jobs.append(Job("J2", "M2", "reclaim", "Q", dest_id="D", grade="G",
                         start_bucket=1, end_bucket=1, rate_tph=100, tonnage_t=100))
    sc.destinations[0].spec[0].averaging_h = 2
    assert compute_kpis(sc, plan)["on_spec_mass_pct"] == 50
    sc.config["QUALITY_WINDOW_AGGREGATION_ENABLED"] = True
    assert compute_kpis(sc, plan)["on_spec_mass_pct"] == 100


def test_partial_minute_actuals_share_one_elapsed_time_basis():
    sc, plan = fixture()
    sim = Simulator(sc, plan)
    sim.current_minute = 15
    sim.delivered["D"][0] = 25
    sim.delivered_q["D"][0] = {"BPL": 70., "Cd": 20.}
    sim.inv["P"] = 775
    sim.moved_by_job["J"] = 25
    k = compute_kpis(sc, plan, sim)
    assert k["evaluated_hours"] == .25
    assert k["final_product_demand_t"] == 25
    assert k["service_pct"] == 100
    assert k["inventory_total_end_t"] == 775
    assert k["handled_t"] == 25
