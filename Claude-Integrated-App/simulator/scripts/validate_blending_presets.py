"""Replayable, non-destructive blending/treatment preset experiments.

Saves exact inputs, outputs and an interpretation bundle outside operator state.
No grade, opening-stock or minimum-job-floor changes are made. Optional raw
stock drawdown is an explicitly labelled terminal-inventory-policy sensitivity.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mph_sim import datasets, mine_arrivals, prewash_blending, washing
from mph_sim.analysis_tools import build_run_bundle
from mph_sim.grade_catalog import apply_saved_grade_catalog
from mph_sim.kpis import compute_kpis
from mph_sim.optimizer import SolveOptions, solve_plan
from mph_sim.validation import validate_scenario
from validate_scheduling_objective import audit


def fixture(preset, hours, mode="profile", consolidation=False, wall=90., drawdown_pct=0.):
    sc = datasets.get_scenario(datasets.S3_SCENARIO_NAME)
    sc.sim.horizon_buckets = hours
    sc.sim.horizon_minutes = hours * 60
    datasets.apply_planning_demand_profile(sc, preset)
    apply_saved_grade_catalog(sc)
    cfg = sc.config["CHEVRON_STACKING"]
    cfg.update(enabled=True, global_mode="OPTIMIZED",
               enabled_spaces=[s.id for s in sc.spaces
                               if s.site == "MPH" and s.area == "M16-humid"])
    pre = prewash_blending.settings(sc)
    pre["enabled"] = True
    for point in pre["points"].values():
        point["enabled_source_spaces"] = list(dict.fromkeys(
            row["source_space"] for row in point["optimized_components"]))
        point["share_mode"] = "OPTIMIZED"
        if mode != "profile":
            point["allowed_treatment_modes"] = ["simple", "full"] if mode == "both" else [mode]
    sc.config[prewash_blending.CONFIG_KEY] = pre
    sc = washing.stage_configuration(sc, {**washing.settings(sc), "enabled": True})
    sc.config["MIN_JOB_VOLUME_T"] = 0.
    sc.config["PARCEL_CONSOLIDATION_ENABLED"] = consolidation
    sc.config["PARCEL_CONSOLIDATION_WALL_TIME_S"] = 15.
    sc.config["PARCEL_CONSOLIDATION_DETERMINISTIC_TIME_S"] = 4.
    sc.config["WEEKLY_WINDOW_WALL_TIME_S"] = wall
    sc.config["WEEKLY_TOTAL_WALL_TIME_S"] = wall * max(hours / 24, 1)
    if drawdown_pct:
        # Explicit offline sensitivity: permit a small drawdown, never create
        # stock or force consumption. Preserve the opening inventories/assays.
        exact = dict(sc.config.get("TERMINAL_INVENTORY_EXACT_T", {}))
        minimum = dict(sc.config.get("TERMINAL_INVENTORY_MIN_T", {}))
        for point in pre["points"].values():
            for sid in point["enabled_source_spaces"]:
                if sid in exact:
                    minimum[sid] = round(float(exact.pop(sid)) * (1 - drawdown_pct / 100))
        sc.config["TERMINAL_INVENTORY_EXACT_T"] = exact
        sc.config["TERMINAL_INVENTORY_MIN_T"] = minimum
        sc.config["VALIDATION_PREWASH_DRAWDOWN_PCT"] = drawdown_pct
    return sc


def summarize(sc, plan):
    checks = audit(sc, plan)
    kpis = compute_kpis(sc, plan)
    treatment_jobs = [j for j in plan.jobs if j.treatment_id]
    grouped = defaultdict(list)
    for job in treatment_jobs:
        for flow in job.material_flows:
            if not flow.get("treatment_id"):
                continue
            key = (job.process_stage, sc.sim.bucket_dt(int(flow["bucket"])).date().isoformat(), flow["treatment_id"])
            grouped[key].append({**flow, "source_space": job.source_space})
    campaigns = []
    for (line, day, mode), flows in grouped.items():
        sources = defaultdict(float)
        for flow in flows:
            sources[(flow["source_space"], flow["grade"])] += float(flow["tonnage_t"])
        total = sum(sources.values())
        dry_out = sum(float(f["output_tonnage_t"]) * (1 - f["output_quality"]["moisture"] / 100) for f in flows)
        quality = {}
        for param in washing.PARAMETERS:
            weights = [(float(f["output_tonnage_t"]) * (1 if param == "moisture" else
                        1 - f["output_quality"]["moisture"] / 100), f["output_quality"].get(param)) for f in flows]
            if all(v is not None for _, v in weights):
                quality[param] = sum(w * v for w, v in weights) / sum(w for w, _ in weights)
        violations = [p for p, (lo, hi) in washing.settings(sc)["g10_limits"].items()
                      if p not in quality or quality[p] < lo - 1e-6 or quality[p] > hi + 1e-6]
        campaigns.append(dict(line=line, day=day, treatment=mode, feed_t=total,
                              product_t=sum(f["output_tonnage_t"] for f in flows), product_dry_t=dry_out,
                              blended=len({s for s, _ in sources}) >= 2, output_quality=quality,
                              g10_daily_composite_violations=violations,
                              components=[dict(source=s, grade=g, feed_t=v, share_pct=100*v/total)
                                          for (s, g), v in sources.items()]))
    checks.update(
        one_tonne_jobs=sum(0 < j.tonnage_t <= 1.001 for j in plan.jobs),
        jobs_below_100t=sum(0 < j.tonnage_t < 100 for j in plan.jobs),
        minimum_treatment_job_t=min((j.tonnage_t for j in treatment_jobs), default=None),
        one_tonne_treatment_jobs=sum(0 < j.tonnage_t <= 1.001 for j in treatment_jobs),
        treatment_jobs_below_100t=sum(0 < j.tonnage_t < 100 for j in treatment_jobs),
        minimum_treatment_slice_t=min((f["tonnage_t"] for j in treatment_jobs for f in j.material_flows), default=None),
        one_tonne_treatment_slices=sum(0 < f["tonnage_t"] <= 1.001 for j in treatment_jobs for f in j.material_flows),
        treatment_slices_below_100t=sum(0 < f["tonnage_t"] < 100 for j in treatment_jobs for f in j.material_flows),
        minimum_inventory_t=min((v for vs in plan.inventory.values() for v in vs), default=0),
        maximum_capacity_excess_t=max((v - sc.space(s).capacity_t for s, vs in plan.inventory.items() for v in vs), default=0),
    )
    modes_by_day = defaultdict(set)
    for row in campaigns:
        modes_by_day[row["line"], row["day"]].add(row["treatment"])
    checks["multiple_treatment_modes_per_line_day"] = sum(len(v) > 1 for v in modes_by_day.values())
    checks["complete_inventory_horizon"] = all(
        len(plan.inventory.get(s.id, [])) == sc.sim.horizon_buckets + 1 for s in sc.spaces)
    checks["accepted_plan"] = plan.status in {"FEASIBLE", "OPTIMAL"}
    checks["all_windows_validated"] = all(w.status == "VALIDATED" for j in plan.jobs for w in j.execution_windows)
    checks["numerical_acceptance"] = bool(
        checks["accepted_plan"] and checks["complete_inventory_horizon"]
        and checks["all_windows_validated"] and not checks["minute_sweep_errors"]
        and checks["maximum_mass_residual_t"] <= 1e-6
        and checks["minimum_inventory_t"] >= -1e-6
        and checks["maximum_capacity_excess_t"] <= 1e-6
        and not checks["multiple_treatment_modes_per_line_day"])
    return dict(status=plan.status, solver_wall_s=plan.solver_wall_s, jobs=len(plan.jobs),
                treatment_jobs=len(treatment_jobs), checks=checks, kpis=kpis,
                campaigns=campaigns, reasons=plan.reasons,
                objective_passes=plan.objective_pass_records,
                small_jobs=[dict(id=j.id, machine=j.machine, stage=j.process_stage,
                                tonnes=j.tonnage_t, treatment=j.treatment_id, purpose=j.purpose)
                            for j in plan.jobs if 0 < j.tonnage_t < 100])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--presets", nargs="+",
        default=[datasets.DEFAULT_PLANNING_DEMAND_PROFILE])
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument(
        "--mode", choices=["profile", "both", "simple", "full"],
        default="profile",
        help="Keep the preset's treatment policy or override both sites.")
    parser.add_argument("--consolidation", action="store_true")
    parser.add_argument("--wall", type=float, default=90.)
    parser.add_argument("--deterministic", type=float, default=24.)
    parser.add_argument("--drawdown-pct", type=float, default=0.)
    parser.add_argument(
        "--mine-arrivals", action=argparse.BooleanOptionalAction,
        default=True,
        help=("Use the UI-default daily mine supply and operating-stock policy "
              "(default: enabled; use --no-mine-arrivals for a depletion test)."))
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    directory = Path(args.output)
    directory.mkdir(parents=True, exist_ok=True)
    summaries = []
    for preset in args.presets:
        name = f"{preset}-{args.hours}h-{args.mode}" + ("-consolidated" if args.consolidation else "")
        if args.drawdown_pct:
            name += f"-drawdown{args.drawdown_pct:g}pct"
        if args.mine_arrivals:
            name += "-mine-arrivals"
        target = directory / name
        target.mkdir(exist_ok=False)
        sc = fixture(preset, args.hours, args.mode, args.consolidation, args.wall, args.drawdown_pct)
        if args.mine_arrivals:
            mine_arrivals.install_defaults(sc)
        issues = [asdict(i) for i in validate_scenario(sc)]
        def save(filename, value):
            (target / filename).write_text(json.dumps(value, indent=2, default=str, allow_nan=False), encoding="utf-8")
        save("scenario.json", asdict(sc))
        save("validation.json", issues)
        errors = [i for i in issues if i["severity"] == "ERROR"]
        if errors:
            raise ValueError(errors)
        print(f"START {name}", flush=True)
        start = time.monotonic()
        total_wall = args.wall * max(args.hours / 24, 1)
        plan = solve_plan(sc, SolveOptions(max_time_s=args.deterministic, max_wall_time_s=total_wall),
                          progress_cb=lambda s: print(f"{name}: {s}", flush=True))
        save("plan.json", asdict(plan))
        summary = summarize(sc, plan)
        summary.update(case=name, elapsed_s=time.monotonic()-start,
                       solver_options=dict(max_time_s=args.deterministic, max_wall_time_s=total_wall,
                                           window_wall_time_s=args.wall))
        save("audit.json", summary)
        save("run_bundle.json", build_run_bundle(sc, plan, summary["kpis"]))
        summaries.append(summary)
        print(json.dumps(dict(case=name, status=plan.status, jobs=summary["jobs"],
                              washing_jobs=summary["treatment_jobs"], checks=summary["checks"],
                              blended_days=sum(c["blended"] for c in summary["campaigns"])), default=str), flush=True)
    print(f"EVIDENCE {directory}", flush=True)


if __name__ == "__main__":
    main()
