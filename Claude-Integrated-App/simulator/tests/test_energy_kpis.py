"""Contract tests for specific-electricity and movement KPI accounting."""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mph_sim import datasets  # noqa: E402
from mph_sim.domain import Job, PlanResult  # noqa: E402
from mph_sim.energy import (  # noqa: E402
    DEFAULT_REFERENCE_KWH_PER_T,
    compute_plan_energy,
    energy_model_parameters,
)


def _job(job_id: str = "J1", tonnes: float = 1_000.0) -> Job:
    return Job(
        id=job_id,
        machine="M17-RC01",
        kind="reclaim",
        space="S-OPEN",
        tonnage_t=tonnes,
        path=["L1", "L2"],
    )


def test_specific_energy_counts_work_links_and_positioning_separately():
    scenario = datasets.sulphur_minimal()
    plan = PlanResult(status="FEASIBLE", jobs=[_job()])

    result = compute_plan_energy(
        scenario,
        plan,
        useful_delivered_t=500.0,
        positioning_hours_by_machine={"M17-RC01": 2.0},
        conflict_hours=0.5,
        conflict_count=1,
    )

    assert result["work_kwh"] == pytest.approx(150.0)
    assert result["link_kwh"] == pytest.approx(80.0)
    assert result["positioning_kwh"] == pytest.approx(400.0)
    assert result["total_kwh"] == pytest.approx(630.0)
    assert result["specific_kwh_per_delivered_t"] == pytest.approx(1.26)
    assert result["reference_status"] == "above reference"
    assert result["handling_factor"] == pytest.approx(2.0)
    assert result["conflict_count"] == 1
    assert result["conflict_h"] == pytest.approx(0.5)


def test_rehandling_and_positioning_increase_energy_for_same_useful_tonnes():
    scenario = datasets.sulphur_minimal()
    direct = compute_plan_energy(
        scenario, PlanResult(status="FEASIBLE", jobs=[_job()]), 1_000.0)
    rehandled = compute_plan_energy(
        scenario,
        PlanResult(status="FEASIBLE", jobs=[_job("J1"), _job("J2")]),
        1_000.0,
        positioning_hours_by_machine={"M17-RC01": 1.0},
    )

    assert rehandled["handling_factor"] == pytest.approx(2.0)
    assert rehandled["total_kwh"] > direct["total_kwh"]
    assert (rehandled["specific_kwh_per_delivered_t"]
            > direct["specific_kwh_per_delivered_t"])


def test_reference_is_configurable_and_invalid_values_are_disclosed():
    scenario = datasets.sulphur_minimal()
    scenario.config["ENERGY_REFERENCE_KWH_PER_T"] = 0.4
    assert energy_model_parameters(scenario)["reference_kwh_per_t"] == 0.4

    scenario.config["ENERGY_REFERENCE_KWH_PER_T"] = float("nan")
    params = energy_model_parameters(scenario)
    assert params["reference_kwh_per_t"] == DEFAULT_REFERENCE_KWH_PER_T
    assert params["warnings"]
