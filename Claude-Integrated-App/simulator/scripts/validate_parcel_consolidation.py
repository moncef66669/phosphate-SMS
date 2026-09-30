"""Reproduce the soft-parcel S0 experiment without changing saved settings.

Run from simulator/: python scripts/validate_parcel_consolidation.py --repeat 2
Evidence is written to a temporary directory, not the application database.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mph_sim import datasets
from mph_sim.grade_catalog import apply_saved_grade_catalog
from mph_sim.optimizer import solve_plan
from mph_sim.runs import micro_parcel_metrics
from mph_sim.validation import validate_scenario


def run(repeat=2):
    directory = Path(tempfile.mkdtemp(prefix="s0-parcel-consolidation-"))
    summaries = []
    for index in range(repeat):
        sc = datasets.get_scenario(datasets.S3_SCENARIO_NAME)
        datasets.apply_planning_demand_profile(
            sc, datasets.DEFAULT_PLANNING_DEMAND_PROFILE)
        apply_saved_grade_catalog(sc)
        sc.config["CAMPAIGN_POLICY"]["default"]["allow_short_parcel"] = True
        sc.config["PROCESS_STAGE_TARGET_SHORTFALL_MAX_T"] = {}
        sc.config["PARCEL_CONSOLIDATION_ENABLED"] = True
        sc.config["PARCEL_CONSOLIDATION_WALL_TIME_S"] = 15.0
        sc.config["PARCEL_CONSOLIDATION_DETERMINISTIC_TIME_S"] = 4.0
        sc.config["WEEKLY_WINDOW_WALL_TIME_S"] = 55.0
        sc.config["WEEKLY_TOTAL_WALL_TIME_S"] = 385.0
        errors = [issue for issue in validate_scenario(sc)
                  if issue.severity == "ERROR"]
        if errors:
            raise ValueError(errors)
        print(f"RUN {index + 1}/{repeat}", flush=True)
        started = time.monotonic()
        result = solve_plan(sc, progress_cb=lambda s: print(s, flush=True))
        metrics = micro_parcel_metrics(sc, result.jobs)
        summary = {
            "run": index + 1, "status": result.status,
            "wall_s": time.monotonic() - started,
            "unmet_t": sum(sum(a) for a in result.unmet.values()),
            "physical_runs": result.breakdown.get("physical_runs"),
            "micro": metrics,
            "one_tonne_jobs": sum(0 < job.tonnage_t <= 1.001
                                   for job in result.jobs),
            "consolidation": {k: v for k, v in result.breakdown.items()
                              if "consolidation" in k},
            "stage_shortfall_t": result.breakdown.get(
                "process_stage_target_shortfall_t", 0),
            "minute_timing_errors": result.breakdown.get("minute_timing_errors"),
            "minimum_inventory_t": min(
                (x for a in result.inventory.values() for x in a), default=0),
            "max_capacity_excess_t": max((
                x - sc.space(sid).capacity_t
                for sid, a in result.inventory.items() for x in a), default=0),
            "handled_mass_residual_t": result.breakdown.get("handled_t", 0)
                - sum(j.tonnage_t for j in result.jobs),
            "details": [r for r in result.reasons
                        if r.startswith(("PARCEL_", "WEEKLY_", "SOLVER_"))],
        }
        summaries.append(summary)
        (directory / f"plan-{index + 1}.json").write_text(
            json.dumps(asdict(result), indent=2), encoding="utf-8")
        (directory / f"scenario-{index + 1}.json").write_text(
            json.dumps(asdict(sc), indent=2, default=str), encoding="utf-8")
        (directory / "summary.json").write_text(
            json.dumps(summaries, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2), flush=True)
    print(f"EVIDENCE {directory}", flush=True)
    return summaries


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=2, choices=range(1, 5))
    run(parser.parse_args().repeat)
