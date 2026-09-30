"""Reproduce paired studies and preserve complete inputs, plans and outcomes.

python run_business_case.py --output ../../generated_outputs/project_review/experiments
python run_business_case.py --full-project --budget 4 --presets BASELINE LOW_LOCAL_STOCK
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time

from mph_sim.benchmark import BenchmarkOptions, run_benchmark
from mph_sim.coordination_benchmark import make_coordination_scenario, run_coordination_benchmark
from mph_sim.datasets import end_to_end
from mph_sim.grade_catalog import apply_saved_grade_catalog
from mph_sim.persistence import load_scenario
from mph_sim.scenarios import BUSINESS_CASE_PRESETS, make_business_case_scenario


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("../../generated_outputs/project_review/experiments"))
    parser.add_argument("--budget", type=float, default=3)
    parser.add_argument("--full-project", action="store_true")
    parser.add_argument("--scenario", type=Path, help="Exact exported project scenario; otherwise use current built-in plus saved grade catalogue")
    parser.add_argument("--presets", nargs="+", choices=list(BUSINESS_CASE_PRESETS), default=["BASELINE", "LOW_LOCAL_STOCK", "QUALITY_STRESS"])
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    options = BenchmarkOptions(max_solve_s=args.budget)
    studies = [("coordination_outage", make_coordination_scenario(True, 100)),
               ("coordination_control", make_coordination_scenario(False, 100)),
               ("coordination_capacity_150", make_coordination_scenario(True, 150))]
    if args.full_project:
        base = load_scenario(args.scenario) if args.scenario else end_to_end()
        if not args.scenario:
            apply_saved_grade_catalog(base)
        studies += [("project_" + preset.lower(), make_business_case_scenario(base, preset))
                    for preset in args.presets]
    summaries = []
    for name, scenario in studies:
        started = time.monotonic()
        print(f"Starting {name}", flush=True)
        path = output / name
        path.mkdir(exist_ok=True)
        (path / "scenario.json").write_text(json.dumps(asdict(scenario), indent=2, allow_nan=False), encoding="utf-8")
        runner = run_coordination_benchmark if name.startswith("coordination") else run_benchmark
        result = runner(scenario, options, lambda message: print(f"  {message}", flush=True))
        payload = result.to_dict(include_plans=True)
        payload["run_metadata"] = {"budget_per_window": args.budget, "wall_s": time.monotonic() - started}
        (path / "comparison.json").write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
        summaries.append({"study": name, **result.to_dict()})
        (output / "summary.json").write_text(json.dumps(summaries, indent=2, allow_nan=False), encoding="utf-8")
        print(f"Completed {name}: {result.deltas}", flush=True)
    print(f"Evidence written to {output}")


if __name__ == "__main__":
    main()
