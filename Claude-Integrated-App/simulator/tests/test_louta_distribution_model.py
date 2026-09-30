# -*- coding: utf-8 -*-
"""Focused S3 Louta starting-inventory and grade-distribution regressions."""

import pytest

from mph_sim import datasets


EXPECTED_DISTRIBUTIONS = {
    "LT-P1": [
        {"component_id": "C1", "mass_fraction": 0.6, "grade_id": "TBT"},
        {"component_id": "C2", "mass_fraction": 0.4, "grade_id": "BT"},
    ],
    "LT-P2": [
        {"component_id": "C1", "mass_fraction": 0.6, "grade_id": "BT"},
        {"component_id": "C2", "mass_fraction": 0.4, "grade_id": "MT"},
    ],
}
EXPECTED_QUALITY = {
    "LT-P1": {
        "BPL": 65.0,
        "Cd": 33.0,
        "MgO": 1.28,
        "SiO2": 3.22,
        "moisture": 12.0,
    },
    "LT-P2": {
        "BPL": 70.4,
        "Cd": 26.8,
        "MgO": 0.98,
        "SiO2": 2.48,
        "moisture": 12.0,
    },
}


def test_louta_product_piles_start_half_full_with_mixed_composites():
    scenario = datasets.end_to_end()

    for pile_id, dominant_grade in (("LT-P1", "TBT"), ("LT-P2", "BT")):
        pile = scenario.space(pile_id)
        assert pile.capacity_t == 191_512
        assert pile.mass_t == 95_756
        assert pile.mass_t / pile.capacity_t == pytest.approx(0.5)
        assert pile.grade == dominant_grade
        assert pile.state == "mixed"
        assert pile.quality == pytest.approx(EXPECTED_QUALITY[pile_id])


def test_louta_distribution_contract_is_explicit_and_non_spatial():
    scenario = datasets.end_to_end()

    distributions = scenario.config["PILE_GRADE_DISTRIBUTIONS"]
    for pile_id, expected in EXPECTED_DISTRIBUTIONS.items():
        assert distributions[pile_id] == expected
    assert {"110LG-OL1", "110LG-OL2"} <= set(distributions)
    basis = scenario.config["PILE_GRADE_DISTRIBUTION_BASIS"]
    assert {"110LG-OL1", "110LG-OL2", "LT-P1", "LT-P2"} <= set(basis)
    for pile_id in ("LT-P1", "LT-P2"):
        assert "ASSUMED" in basis[pile_id]
        assert "TBC" in basis[pile_id]
        assert "schematic and non-spatial" in basis[pile_id]

    # Distribution bands are composition metadata, not invented solver zones.
    assert not [
        zone for zone in scenario.pile_zones
        if zone.space_id in {"LT-P1", "LT-P2"}
    ]


def test_louta_aggregate_quality_is_exact_mass_weighted_profile_quality():
    scenario = datasets.end_to_end()

    for pile_id, components in EXPECTED_DISTRIBUTIONS.items():
        expected = {}
        for component in components:
            grade = scenario.grade(component["grade_id"])
            for parameter, value in grade.quality.items():
                expected[parameter] = (
                    expected.get(parameter, 0.0)
                    + component["mass_fraction"] * value
                )
        assert scenario.space(pile_id).quality == pytest.approx(expected)


def test_stale_louta_baseline_migrates_as_solver_relevant():
    scenario = datasets.end_to_end()
    scenario.config["BUILTIN_BASELINE_REVISION"] = "pre-louta-distribution"
    scenario.config.pop("PILE_GRADE_DISTRIBUTIONS")
    scenario.config.pop("PILE_GRADE_DISTRIBUTION_BASIS")
    scenario.destinations = [
        destination for destination in scenario.destinations
        if destination.id not in {
            "LOU-WASH-L1", "LOU-WASH-L2", "BEN-WASH-L1",
        }
    ]
    scenario.grade("TBT").quality["BPL"] = 74.0
    scenario.grade("MT").quality["BPL"] = 63.0
    for pile_id, mass, grade in (
        ("LT-P1", 120_000, "TBT"),
        ("LT-P2", 100_000, "BT"),
    ):
        pile = scenario.space(pile_id)
        pile.capacity_t = 190_000
        pile.mass_t = mass
        pile.grade = grade
        pile.quality = {}
        pile.state = "building"

    migration = datasets.migrate_loaded_builtin(scenario)

    assert migration == "solver"
    assert scenario.config["BUILTIN_BASELINE_REVISION"] == \
        datasets.S3_BASELINE_REVISION
    assert {
        "LOU-WASH-L1", "LOU-WASH-L2", "BEN-WASH-L1",
    } <= {destination.id for destination in scenario.destinations}
    assert scenario.destination("LOU-WASH-L1").demand_kind == "process_feed"
    assert scenario.destination("LOU-WASH-L2").process_stage == "LOU-WASH-L2"
    assert scenario.grade("TBT").quality["BPL"] == pytest.approx(63.0)
    assert scenario.grade("MT").quality["BPL"] == pytest.approx(74.0)
    for pile_id, expected in EXPECTED_DISTRIBUTIONS.items():
        assert scenario.config["PILE_GRADE_DISTRIBUTIONS"][pile_id] == expected
    assert {"110LG-OL1", "110LG-OL2"} <= set(
        scenario.config["PILE_GRADE_DISTRIBUTIONS"])
    for pile_id, dominant_grade in (("LT-P1", "TBT"), ("LT-P2", "BT")):
        pile = scenario.space(pile_id)
        assert pile.capacity_t == 191_512
        assert pile.mass_t == 95_756
        assert pile.grade == dominant_grade
        assert pile.state == "mixed"
        assert pile.quality == pytest.approx(EXPECTED_QUALITY[pile_id])

    assumption = next(
        item for item in scenario.assumptions if item.id == "S3-A21"
    )
    assert assumption.status == "ASSUMED"
    assert "schematic and non-spatial" in assumption.text


def test_pre_process_chain_hot_session_is_rebuilt_atomically():
    scenario = datasets.end_to_end()
    scenario.config["BUILTIN_BASELINE_REVISION"] = "pre-louta-process-chain"
    scenario.spaces = [
        space for space in scenario.spaces
        if space.site != "LOUTA" or space.id in {"LT-P1", "LT-P2"}
    ]
    scenario.machines = [
        machine for machine in scenario.machines
        if machine.site != "LOUTA" or machine.id.startswith("140LG")
    ]
    scenario.nodes = [node for node in scenario.nodes if node.site != "LOUTA"]
    scenario.links = [
        link for link in scenario.links if not link.id.startswith("L-LT-")
    ]
    del scenario.process_transfers
    for key in list(scenario.config):
        if key.startswith(("LOU_S0_", "PROCESS_", "TERMINAL_")):
            scenario.config.pop(key)

    assert datasets.migrate_loaded_builtin(scenario) == "solver"
    assert {space.id for space in scenario.spaces
            if space.site == "LOUTA"} == {
        "LT-MINE-FEED-GLOBAL", "LT-MINE-FEED-LOW-CD",
        "110LG-OL1", "110LG-OL2",
        "LT-WASH-L1-WIP", "LT-WASH-L2-WIP", "LT-P1", "LT-P2",
    }
    assert {machine.id for machine in scenario.machines
            if machine.site == "LOUTA"} == {
        "110LG-ST-01", "120LG-RC-01", "120LG-RC-02",
        "130LG-ST-01", "140LG-RC-01", "140LG-RC-02",
    }
    assert len(scenario.process_transfers) == 6
    assert scenario.config["WASHING_LINE_RESIDENCE_TIME_MODELLED"] == {
        "LOUTA": True, "BENGUERIR": False,
    }
    assert scenario.config["MACHINE_COVERAGE_REWARD"] == 0
    assert datasets.migrate_loaded_builtin(scenario) == ""
