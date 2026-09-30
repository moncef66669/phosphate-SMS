"""Route-layer staging guards for the three-site S3 value chain.

Mine reclaimers terminate at their corridor dispatch interface. They must not
be offered a single reclaim candidate that crosses C1/C2, passes through an M16
stockpile and reaches PAP/drying in the same planning bucket.
"""

from mph_sim import datasets
from mph_sim.flow_config import set_destination_line_operating_state
from mph_sim.optimizer import SolveOptions, build_candidates, solve_plan
from mph_sim.routes import (
    RouteGraph,
    reclaim_candidates,
    space_transit_free,
)


MINE_RECLAIMERS = {"RC2", "RC3", "140LG-RC-01", "140LG-RC-02"}
FINAL_M16_DESTINATIONS = {"PAP-1", "PAP-2", "DRY-FEED"}
M16_RECLAIMERS = {"RC01-A", "RC01-B"}


def test_final_reclaim_candidates_are_local_and_storage_transit_free():
    sc = datasets.end_to_end()
    graph = RouteGraph.from_scenario(sc)
    candidates = reclaim_candidates(sc, graph)

    assert candidates
    assert all(path.transit_buckets == 0
               for _, _, _, path in candidates)
    assert all(space_transit_free(sc, graph, path)
               for _, _, _, path in candidates)

    invalid = [
        (machine, space, destination, path.links)
        for machine, space, destination, path in candidates
        if machine in MINE_RECLAIMERS
        and destination in FINAL_M16_DESTINATIONS
    ]
    assert not invalid


def test_valid_m16_reclaim_and_mine_dispatch_candidates_remain():
    sc = datasets.end_to_end()
    graph = RouteGraph.from_scenario(sc)
    reclaim = reclaim_candidates(sc, graph)
    candidates, _ = build_candidates(sc, graph)

    local_pairs = {
        (machine, destination)
        for machine, _, destination, _ in reclaim
        if machine in M16_RECLAIMERS
    }
    assert {
        ("RC01-A", "PAP-1"), ("RC01-A", "DRY-FEED"),
        ("RC01-B", "PAP-1"), ("RC01-B", "DRY-FEED"),
    } <= local_pairs

    dispatch_pairs = {
        (candidate.machine, candidate.dest_id)
        for candidate in candidates
        if candidate.kind == "dispatch"
    }
    assert {
        ("RC2", "C2"), ("RC3", "C2"),
        ("140LG-RC-01", "C1"), ("140LG-RC-02", "C1"),
    } <= dispatch_pairs


def test_pap2_candidates_appear_only_when_line_is_available():
    sc = datasets.end_to_end()
    future = reclaim_candidates(sc, RouteGraph.from_scenario(sc))
    assert not [
        row for row in future
        if row[2] == "PAP-2"
    ]

    set_destination_line_operating_state(sc, "PAP-2", "AVAILABLE")
    available = reclaim_candidates(sc, RouteGraph.from_scenario(sc))
    assert {
        (machine, destination)
        for machine, _, destination, _ in available
        if destination == "PAP-2"
    } == {
        ("RC01-A", "PAP-2"),
        ("RC01-B", "PAP-2"),
    }


def test_s3_plan_has_no_mine_reclaim_job_direct_to_pap_or_drying():
    sc = datasets.end_to_end()
    result = solve_plan(sc, SolveOptions(max_time_s=2))

    assert result.status in ("OPTIMAL", "FEASIBLE")
    invalid = [
        job for job in result.jobs
        if job.kind == "reclaim"
        and job.machine in MINE_RECLAIMERS
        and job.dest_id in FINAL_M16_DESTINATIONS
    ]
    assert not invalid
