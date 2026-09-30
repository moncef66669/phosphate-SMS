"""Offline real-S0 scenario screening with independent execution/mass checks."""
from __future__ import annotations
import argparse
from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mph_sim import datasets
from mph_sim.domain import CalendarEntry
from mph_sim.grade_catalog import apply_saved_grade_catalog
from mph_sim.kpis import compute_kpis
from mph_sim.optimizer import solve_plan
from mph_sim.timing import validate_execution_windows


def fixture(case):
    sc = datasets.get_scenario(datasets.S3_SCENARIO_NAME)
    profile = datasets.DEFAULT_PLANNING_DEMAND_PROFILE
    if case == "heavy":
        data = deepcopy(datasets.PLANNING_DEMAND_PROFILES[profile])
        data["mph_daily_t"] = {key: round(value * 1.35) for key, value in data["mph_daily_t"].items()}
        data["bg_train_product_t"] = round(data["bg_train_product_t"] * 1.35)
        profile = "VALIDATION_HEAVY"
        datasets.PLANNING_DEMAND_PROFILES[profile] = data
    datasets.apply_planning_demand_profile(sc, profile)
    apply_saved_grade_catalog(sc)
    if case == "half-stock":
        for space in sc.spaces:
            if space.site == "MPH" and space.id.startswith("HP-"):
                space.mass_t *= .5
        for segment in sc.segments:
            if segment.space_id.startswith("HP-"):
                segment.mass_t *= .5
    if case == "outage":
        sc.calendars += [CalendarEntry("LINK:C2", 54, 65, "known outage"),
                         CalendarEntry("RC01-A", 54, 59, "known outage")]
    return sc


def audit(sc, plan):
    residuals = {}
    flows = defaultdict(float)
    for job in plan.jobs:
        for flow in job.bucket_flows:
            if job.source_space:
                flows[job.source_space] -= flow.tonnage_t
            if job.kind == "dispatch":
                continue  # delayed stack arrivals have no source inventory
            if job.target_space and any(sp.id == job.target_space for sp in sc.spaces):
                flows[job.target_space] += (flow.output_tonnage_t if flow.output_tonnage_t is not None
                                            else flow.tonnage_t)
    width = max((len(series) - 1 for series in plan.inventory.values()), default=0)
    for exo in sc.exogenous:
        for t in range(width):
            if exo.start_bucket <= t <= exo.end_bucket:
                if exo.target_space:
                    flows[exo.target_space] += round(exo.rate_tph * sc.sim.dt_h)
                if exo.source_space:
                    flows[exo.source_space] -= round(exo.rate_tph * sc.sim.dt_h)
    for sp in sc.spaces:
        series = plan.inventory.get(sp.id)
        if series:
            residuals[sp.id] = (series[-1] - round(sp.mass_t) - flows[sp.id]
                                + plan.breakdown.get(f"spill_t[{sp.id}]", 0))
    statuses = defaultdict(int)
    for job in plan.jobs:
        for window in job.execution_windows:
            statuses[window.status] += 1
    return dict(minute_statuses=dict(statuses),
                minute_sweep_errors=[asdict(issue) for issue in validate_execution_windows(sc, plan.jobs)
                                     if issue.severity == "ERROR"],
                maximum_mass_residual_t=max(map(abs, residuals.values()), default=0),
                mass_residuals_t={k: v for k, v in residuals.items() if abs(v) > 1e-6},
                minimum_job_t=min((job.tonnage_t for job in plan.jobs), default=None),
                job_minimum_violations=sum(job.tonnage_t < sc.config.get("MIN_JOB_VOLUME_T", 0)
                                           for job in plan.jobs))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", nargs="+", default=["integrated"])
    parser.add_argument("--output")
    parser.add_argument("--minimum", type=float, default=0)
    args = parser.parse_args()
    directory = Path(args.output) if args.output else Path(tempfile.mkdtemp(prefix="scheduling-objective-"))
    directory.mkdir(parents=True, exist_ok=True)
    summaries = []
    for case in args.cases:
        sc = fixture(case)
        sc.config["MIN_JOB_VOLUME_T"] = args.minimum
        print(f"START {case} minimum={args.minimum} evidence={directory}", flush=True)
        plan = solve_plan(sc, progress_cb=lambda stage: print(f"{case}: {stage}", flush=True))
        checks = audit(sc, plan)
        kpis = compute_kpis(sc, plan)
        data = dict(case=case, status=plan.status, wall_s=plan.solver_wall_s,
                    checks=checks, kpis=kpis, reasons=plan.reasons,
                    objective_passes=plan.objective_pass_records)
        for name, payload in (("scenario", asdict(sc)), ("plan", asdict(plan)), ("audit", data)):
            (directory / f"{case}-{name}.json").write_text(
                json.dumps(payload, indent=2, default=str, allow_nan=False), encoding="utf-8")
        summaries.append(data)
        (directory / "summary.json").write_text(json.dumps(summaries, indent=2, default=str), encoding="utf-8")
        print(f"DONE {case}: {plan.status}, minute={checks['minute_statuses']}, "
              f"mass_residual={checks['maximum_mass_residual_t']}, jobs={len(plan.jobs)}", flush=True)
    print(f"EVIDENCE {directory}", flush=True)


if __name__ == "__main__":
    main()
