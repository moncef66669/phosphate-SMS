"""Pages: 1 Executive Overview · 8 Results & KPIs · 13 Scenario Comparison."""
from __future__ import annotations

import html
import re

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from mph_sim.campaigns import short_parcel_penalty
from mph_sim.kpis import compute_kpis
from mph_sim.energy import compute_plan_energy, energy_model_parameters
from mph_sim.optimizer import BIG_PENALTY
from mph_sim import washing

from . import common, theme
from .pages_planning import OPERATIONAL_PRIORITY_DEFAULTS
from .report_ref import report_ref
from .theme import (BLUE, DGREEN, DORANGE, GREEN, GREY, NAVY, ORANGE, RED,
                    gantt_figure, jobs_to_gantt_df, plot, plotly_layout,
                    series_fig, show_df)


def _kpis():
    scn, p, s = common.sc(), common.plan(), common.sim()
    if p is None:
        return None
    return compute_kpis(scn, p, s)


def washing_kpi_rows(scn, kpis):
    cfg = washing.settings(scn)
    rows = []
    for key, mode in cfg["modes"].items():
        prefix = f"washing_{key}_"
        feed = float(kpis.get(prefix + "feed_dry_t", 0))
        product = float(kpis.get(prefix + "product_dry_t", 0))
        cost = mode["cost_per_tsm_t"]
        rows.append({"Treatment": mode["name"], "Dry feed [t]": feed,
                     "Recovered dry product [t]": product,
                     "Dry solids lost [t]": feed - product,
                     "Realized dry yield [%]": 100 * product / feed if feed else None,
                     f"Treatment cost [{cfg['cost_unit']}]": "Unconfigured" if cost is None else product * cost,
                     "Residence [min]": "Inherit line" if mode["residence_min"] is None else mode["residence_min"]})
    return rows


def planner_objective_weight_items(scn) -> list[tuple[str, str]]:
    """Return the configured planner coefficients for compact Monitor display."""
    w = scn.weights
    rows = [
        ("w₁ Demand penalty", f"{w.w1_unmet:g}"),
        ("w₂ Quality penalty", f"{w.w2_offspec:g}"),
        ("w₃ Process cost",
         "off" if not w.w3_process_cost else f"{w.w3_process_cost:g}"),
        ("w₄ Give-away penalty", f"{w.w4_giveaway:g}"),
        ("w₅ Machine-change penalty", f"{w.w5_moves:g}"),
        ("w₆ Safety-stock penalty", f"{w.w6_safety:g}"),
        ("w₇ Utilisation reward",
         "off" if not w.w7_utilisation else f"{w.w7_utilisation:g}"),
    ]
    return rows


def _operational_priority_value(scn, key: str):
    """Read a config-backed operational priority without inventing validity."""
    default = OPERATIONAL_PRIORITY_DEFAULTS[key]
    raw = scn.config.get(key, default)
    if isinstance(default, bool):
        return raw if isinstance(raw, bool) else None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not value.is_integer() or value < 0:
        return None
    return int(value)


def operational_priority_items(scn) -> list[tuple[str, str]]:
    """Return compact, explicitly provisional operational coefficients."""
    enabled = _operational_priority_value(scn, "ENERGY_OBJECTIVE_ENABLED")
    energy = _operational_priority_value(
        scn, "ENERGY_OBJECTIVE_CENTIKWH_PENALTY")

    def display(key: str) -> str:
        value = _operational_priority_value(scn, key)
        return "invalid" if value is None else f"{value:g}"

    energy_label = (
        "invalid" if enabled is None or energy is None else
        f"on · {energy:g}/centi-kWh" if enabled else "off"
    )
    return [
        ("Feed-gap minute penalty",
         display("CONTINUITY_GAP_MINUTE_PENALTY")),
        ("Interruption episode penalty",
         display("CONTINUITY_EPISODE_PENALTY")),
        ("Work/link energy objective", energy_label),
        ("Machine start penalty", display("EQUIPMENT_START_PENALTY")),
        ("Machine stop penalty", display("EQUIPMENT_STOP_PENALTY")),
        ("Group changeover penalty",
         display("EQUIPMENT_CHANGEOVER_PENALTY")),
    ]


def _recorded_breakdown_value(breakdown, key: str, fallback=None):
    """Return a numeric solver record or an honest legacy-plan marker."""
    value = breakdown.get(key, fallback)
    if value is None:
        return "not recorded"
    try:
        return float(value)
    except (TypeError, ValueError):
        return "invalid record"


def continuous_feed_delivery_summary(kpis) -> dict[str, object]:
    """Select duration/episode KPIs, with an explicit legacy-plan fallback."""
    gap_hours = kpis.get("continuous_feed_gap_hours")
    if gap_hours is not None:
        try:
            gap_value = float(gap_hours)
        except (TypeError, ValueError):
            gap_value = 0.0
        episodes = kpis.get("continuous_feed_interruption_count")
        try:
            episode_value = int(episodes) if episodes is not None else None
        except (TypeError, ValueError):
            episode_value = None
        maximum = kpis.get("continuous_feed_max_episode_minutes")
        try:
            maximum_value = float(maximum) if maximum is not None else None
        except (TypeError, ValueError):
            maximum_value = None
        return {
            "gap_hours": gap_value,
            "episode_count": episode_value,
            "max_episode_minutes": maximum_value,
            "legacy": False,
        }

    # Results produced before continuity policies were introduced only carry
    # an affected-bucket duration and cannot reconstruct episode frequency.
    try:
        legacy_hours = float(kpis.get("feed_interruptions_h", 0.0) or 0.0)
    except (TypeError, ValueError):
        legacy_hours = 0.0
    return {
        "gap_hours": legacy_hours,
        "episode_count": None,
        "max_episode_minutes": None,
        "legacy": True,
    }


def plan_generation_kpi_rows(scn, plan, _plan_kpis) -> list[dict[str, object]]:
    """Return the planner's real objective drivers with operator-facing units.

    The values are deliberately kept on their physical/raw bases.  They are
    not presented as additive objective contributions because CP-SAT applies
    integer scaling, fixed emergency penalties and tie-breakers internally.
    """
    b = plan.breakdown or {}
    w = scn.weights
    # Use only generated-plan jobs here.  Simulation throughput can differ
    # after execution/replanning and must not be presented as an objective
    # basis for the original solve.
    commanded_t = float(sum(job.tonnage_t for job in plan.jobs))
    handled_t = _recorded_breakdown_value(b, "handled_t", commanded_t)
    unmet_t = b.get("unmet_t")
    if unmet_t is None:
        unmet_t = sum(sum(series) for series in plan.unmet.values())
    # Match the optimiser's w4 basis exactly: each reclaim source is charged
    # for its own positive BPL excess before blending at the destination.
    # The general operational KPI instead measures the final blended product,
    # which is useful elsewhere but is not the planner objective term.
    giveaway_basis = 0.0
    for job in plan.jobs:
        if job.kind != "reclaim" or not job.dest_id:
            continue
        try:
            destination = scn.destination(job.dest_id)
        except KeyError:
            continue
        target = next(
            (spec.target for spec in destination.spec
             if spec.param == "BPL" and spec.target),
            None,
        )
        if target is None:
            continue
        source_bpl = scn.quality_of(job.space).get("BPL", 0.0)
        giveaway_basis += max(source_bpl - target, 0.0) * job.tonnage_t

    gap_priority = _operational_priority_value(
        scn, "CONTINUITY_GAP_MINUTE_PENALTY")
    episode_priority = _operational_priority_value(
        scn, "CONTINUITY_EPISODE_PENALTY")
    energy_enabled = _operational_priority_value(
        scn, "ENERGY_OBJECTIVE_ENABLED")
    energy_priority = _operational_priority_value(
        scn, "ENERGY_OBJECTIVE_CENTIKWH_PENALTY")
    start_priority = _operational_priority_value(
        scn, "EQUIPMENT_START_PENALTY")
    stop_priority = _operational_priority_value(
        scn, "EQUIPMENT_STOP_PENALTY")
    changeover_priority = _operational_priority_value(
        scn, "EQUIPMENT_CHANGEOVER_PENALTY")

    def priority_text(key: str, value) -> str:
        return f"{key} = {'invalid' if value is None else f'{value:g}'}"

    def penalty_effect(value, *, enabled: bool = True) -> str:
        if value is None:
            return "Invalid config"
        return "Penalty" if enabled and value > 0 else "Inactive"

    try:
        active_bucket_priority = max(
            int(scn.config.get("ACTIVE_BUCKET_PENALTY", 0) or 0), 0)
    except (TypeError, ValueError):
        active_bucket_priority = 0
    short_parcel_priority = short_parcel_penalty(scn)
    hold_priority = _operational_priority_value(
        scn, "EQUIPMENT_HOLD_BUCKET_PENALTY")

    def equipment_priority_text(key: str, value) -> str:
        shown = "invalid" if value is None else f"{value:g}"
        return f"w₅ × {key} = {w.w5_moves:g} × {shown}"

    washing_rows = []
    if washing.enabled(scn):
        cfg = washing.settings(scn)
        priced = all(mode["cost_per_tsm_t"] is not None
                     for mode in cfg["modes"].values() if mode["available"])
        washing_rows = [
            {"Objective driver": "Washing raw dry-feed use",
             "Plan result": b.get("washing_raw_feed_dry_t", 0), "Unit": "dry feed t",
             "Configured priority": "after qualified service, before treatment cost",
             "Effect": "Minimize", "Meaning": "higher recovery reduces raw feed for protected qualified service"},
            {"Objective driver": "Washing treatment cost",
             "Plan result": b.get("washing_treatment_cost", 0) if priced else "Unconfigured",
             "Unit": cfg["cost_unit"], "Configured priority": "after raw dry-feed use",
             "Effect": "Minimize" if priced else "Inactive",
             "Meaning": "mode cost per recovered TSM tonne; separate from the legacy grade-cost curve"},
        ]
    return washing_rows + [
        {
            "Objective driver": "Unmet demand",
            "Plan result": float(unmet_t),
            "Unit": "t",
            "Configured priority": f"w₁ = {w.w1_unmet:g}",
            "Effect": "Penalty",
            "Meaning": "requested destination mass not delivered",
        },
        {
            "Objective driver": "Continuous-feed gap duration",
            "Plan result": _recorded_breakdown_value(
                b, "continuity_gap_minutes"),
            "Unit": "min",
            "Configured priority": priority_text(
                "CONTINUITY_GAP_MINUTE_PENALTY", gap_priority),
            "Effect": penalty_effect(gap_priority),
            "Meaning": "scheduled minutes below a configured continuous-feed threshold",
        },
        {
            "Objective driver": "Feed-interruption episodes",
            "Plan result": _recorded_breakdown_value(
                b, "continuity_episode_count"),
            "Unit": "episodes",
            "Configured priority": priority_text(
                "CONTINUITY_EPISODE_PENALTY", episode_priority),
            "Effect": penalty_effect(episode_priority),
            "Meaning": "new scheduled continuous-feed interruption episodes",
        },
        {
            "Objective driver": "Soft-quality deviation",
            "Plan result": float(b.get("offspec_t_unit", 0.0)),
            "Unit": "t·quality-unit",
            "Configured priority": f"w₂ = {w.w2_offspec:g}",
            "Effect": "Penalty",
            "Meaning": "mass-weighted violation of soft receiving limits",
        },
        {
            "Objective driver": "Process cost",
            "Plan result": "inactive",
            "Unit": "—",
            "Configured priority": f"w₃ = {w.w3_process_cost:g}",
            "Effect": "Inactive",
            "Meaning": "no GradeCostModel curve is configured",
        },
        {
            "Objective driver": "BPL give-away",
            "Plan result": float(giveaway_basis),
            "Unit": "t·BPL-point",
            "Configured priority": f"w₄ = {w.w4_giveaway:g}",
            "Effect": "Penalty",
            "Meaning": "positive source-reclaim BPL excess over the destination "
                       "target, before destination blending",
        },
        {
            "Objective driver": "All scheduled state transitions",
            "Plan result": float(b.get("moves", 0.0)),
            "Unit": "events",
            "Configured priority": f"w₅ = {w.w5_moves:g}",
            "Effect": "Penalty",
            "Meaning": "exact starts + stops + activity-group changeovers",
        },
        {
            "Objective driver": "Scheduled machine starts",
            "Plan result": _recorded_breakdown_value(b, "scheduled_starts"),
            "Unit": "events",
            "Configured priority": equipment_priority_text(
                "EQUIPMENT_START_PENALTY", start_priority),
            "Effect": penalty_effect(
                start_priority, enabled=bool(w.w5_moves)),
            "Meaning": "idle-to-positive-flow transitions in the generated schedule",
        },
        {
            "Objective driver": "Scheduled machine stops",
            "Plan result": _recorded_breakdown_value(b, "scheduled_stops"),
            "Unit": "events",
            "Configured priority": equipment_priority_text(
                "EQUIPMENT_STOP_PENALTY", stop_priority),
            "Effect": penalty_effect(
                stop_priority, enabled=bool(w.w5_moves)),
            "Meaning": "positive-flow-to-idle transitions in the generated schedule",
        },
        {
            "Objective driver": "Scheduled group changeovers",
            "Plan result": _recorded_breakdown_value(
                b, "scheduled_group_changeovers"),
            "Unit": "events",
            "Configured priority": equipment_priority_text(
                "EQUIPMENT_CHANGEOVER_PENALTY", changeover_priority),
            "Effect": penalty_effect(
                changeover_priority, enabled=bool(w.w5_moves)),
            "Meaning": "operating transitions between scheduled activity groups",
        },
        {
            "Objective driver": "Scheduled active machine time",
            "Plan result": _recorded_breakdown_value(b, "active_machine_h"),
            "Unit": "machine·h",
            "Configured priority": (
                f"ACTIVE_BUCKET_PENALTY = {active_bucket_priority:g}"),
            "Effect": ("Penalty" if active_bucket_priority else "Reported"),
            "Meaning": "positive-flow scheduled time; positioning is excluded",
        },
        {
            "Objective driver": "Productive work + route energy",
            "Plan result": _recorded_breakdown_value(
                b, "work_link_energy_kwh"),
            "Unit": "modelled kWh",
            "Configured priority": (
                priority_text("ENERGY_OBJECTIVE_CENTIKWH_PENALTY",
                              energy_priority)
                + (" (enabled)" if energy_enabled is True else " (off)"
                   if energy_enabled is False else " (invalid enable flag)")),
            "Effect": penalty_effect(
                energy_priority, enabled=energy_enabled is True),
            "Meaning": "ASSUMED productive-work and traversed-link energy; idle, positioning and start/stop energy excluded",
        },
        {
            "Objective driver": "Safety-stock shortfall",
            "Plan result": float(b.get("safety_short_t", 0.0)),
            "Unit": "t·bucket",
            "Configured priority": f"w₆ = {w.w6_safety:g}",
            "Effect": "Penalty",
            "Meaning": "shortfall below safety stock accumulated by bucket",
        },
        {
            "Objective driver": "Handled-flow reward basis",
            "Plan result": handled_t,
            "Unit": "t handled",
            "Configured priority": f"w₇ = {w.w7_utilisation:g}",
            "Effect": "Reward",
            "Meaning": "commanded tonnes rewarded only when w₇ is above zero",
        },
        {
            "Objective driver": "Short parcels below the floor",
            "Plan result": _recorded_breakdown_value(b, "short_parcel_buckets"),
            "Unit": "buckets",
            "Configured priority": (
                f"SHORT_PARCEL_PENALTY = {short_parcel_priority:g}"),
            "Effect": penalty_effect(
                short_parcel_priority, enabled=bool(w.w5_moves)),
            "Meaning": "buckets run below the configured minimum parcel; "
                       "kept possible and priced, never discarded",
        },
        {
            "Objective driver": "EXECUTED machine starts (minute schedule)",
            "Plan result": _recorded_breakdown_value(b, "executed_starts"),
            "Unit": "events",
            "Configured priority": "reported, not priced",
            "Effect": "Reported",
            "Meaning": "actual starts once the minute windows are placed; "
                       "larger than the hourly count whenever a bucket run is "
                       "broken into separate material bursts",
        },
        {
            "Objective driver": "EXECUTED machine stops (minute schedule)",
            "Plan result": _recorded_breakdown_value(b, "executed_stops"),
            "Unit": "events",
            "Configured priority": "reported, not priced",
            "Effect": "Reported",
            "Meaning": "actual stops once the minute windows are placed",
        },
        {
            "Objective driver": "EXECUTED group changeovers (minute schedule)",
            "Plan result": _recorded_breakdown_value(
                b, "executed_changeovers"),
            "Unit": "events",
            "Configured priority": "reported, not priced",
            "Effect": "Reported",
            "Meaning": "group changes covered by reserved positioning or the "
                       "configured held-gap allowance",
        },
        {
            "Objective driver": "Held running, no material",
            "Plan result": _recorded_breakdown_value(
                b, "executed_hold_minutes"),
            "Unit": "minutes",
            "Configured priority": "reported, not priced",
            "Effect": "Reported",
            "Meaning": "explicit held state between two bursts of the SAME "
                       "activity group within the configured allowance; any "
                       "longer blank gap is counted as a real stop and start",
        },
        {
            "Objective driver": "Machine idle (stopped)",
            "Plan result": _recorded_breakdown_value(
                b, "executed_idle_minutes"),
            "Unit": "minutes",
            "Configured priority": "reported, not priced",
            "Effect": "Reported",
            "Meaning": "blank machine time that is NOT held running; never "
                       "presented as continuous production",
        },
        {
            "Objective driver": "Held running by explicit decision",
            "Plan result": _recorded_breakdown_value(
                b, "scheduled_hold_buckets"),
            "Unit": "buckets",
            "Configured priority": (
                f"w\u2085 \u00d7 EQUIPMENT_HOLD_BUCKET_PENALTY = "
                f"{w.w5_moves:g} \u00d7 {hold_priority if hold_priority is not None else 'invalid'}"),
            "Effect": penalty_effect(
                hold_priority, enabled=bool(w.w5_moves)),
            "Meaning": "buckets the plan pays to keep a machine running "
                       "through without moving material, instead of "
                       "commanding a token parcel to bridge the gap",
        },
        {
            "Objective driver": "Hourly vs executed transition residual",
            "Plan result": _recorded_breakdown_value(
                b, "transition_reconciliation_residual"),
            "Unit": "events",
            "Configured priority": "acceptance bound: must be 0",
            "Effect": "Reported",
            "Meaning": (
                "executed events minus hourly events minus the "
                f"{_recorded_breakdown_value(b, 'transition_reclassified_changeovers')} "
                "changeovers whose real positioning gap makes them a stop "
                "plus a start. Any other value means the two layers disagree "
                "about the schedule itself"),
        },
        {
            "Objective driver": "Physical runs vs allocation jobs",
            "Plan result": _recorded_breakdown_value(b, "physical_runs"),
            "Unit": "runs",
            "Configured priority": "reported, not priced",
            "Effect": "Reported",
            "Meaning": (
                "one machine, one activity group, continuous in time \u2014 the "
                "object an operator schedules. "
                f"{_recorded_breakdown_value(b, 'parent_jobs')} allocation "
                "jobs and "
                f"{_recorded_breakdown_value(b, 'allocation_slices')} "
                "per-bucket slices describe the same work"),
        },
        {
            "Objective driver": "Unstacked-arrival emergency slack",
            "Plan result": float(b.get("surge_t", 0.0)),
            "Unit": "t emergency slack",
            "Configured priority": f"fixed penalty = {BIG_PENALTY:g}",
            "Effect": "Penalty",
            "Meaning": "corridor arrivals that could not be stacked in-bucket",
        },
        {
            "Objective driver": "Space-overflow emergency slack",
            "Plan result": float(b.get("spill_t", 0.0)),
            "Unit": "t emergency slack",
            "Configured priority": f"fixed penalty = {BIG_PENALTY:g}",
            "Effect": "Penalty",
            "Meaning": "material above a stock-space capacity constraint",
        },
    ] + ([{
        "Objective driver": "Commanded-flow tie-breaker",
        "Plan result": handled_t,
        "Unit": "t commanded",
        "Configured priority": (
            "FLOW_TONNE_PENALTY = "
            f"{max(int(scn.config.get('FLOW_TONNE_PENALTY', 0) or 0), 0):g}"
        ),
        "Effect": "Penalty",
        "Meaning": "small configured cost on every commanded flow tonne",
    }] if max(int(scn.config.get("FLOW_TONNE_PENALTY", 0) or 0), 0) else []) + ([{
        "Objective driver": "Machine-coverage tie-breaker",
        "Plan result": int(b.get("planning_used_machines", 0) or 0),
        "Unit": "eligible machines used",
        "Configured priority": (
            "MACHINE_COVERAGE_REWARD = "
            f"{max(int(scn.config.get('MACHINE_COVERAGE_REWARD', 0) or 0), 0):g}"
        ),
        "Effect": "Reward",
        "Meaning": "small configured reward for each eligible machine used",
    }] if max(int(scn.config.get("MACHINE_COVERAGE_REWARD", 0) or 0), 0) else [])


def plan_generation_check_rows(plan) -> list[dict[str, object]]:
    """Return solver/post-solve checks recorded on a generated plan."""
    b = plan.breakdown or {}
    eligible = int(b.get("planning_eligible_machines", 0) or 0)
    used = int(b.get("planning_used_machines", 0) or 0)
    coverage = b.get("planning_machine_coverage_pct")
    if eligible == 0:
        coverage_text = "N/A (0/0)"
    elif coverage is None:
        coverage_text = "not recorded"
    else:
        coverage_text = f"{float(coverage):.1f}% ({used}/{eligible})"
    rows = [
        {
            "Plan-generation check": "Planning machine coverage",
            "Result": coverage_text,
            "Unit / basis": "eligible solver machines used",
        },
        {
            "Plan-generation check": "Unstacked corridor arrivals",
            "Result": float(b.get("surge_t", 0.0)),
            "Unit / basis": "t emergency slack",
        },
        {
            "Plan-generation check": "Space overflow",
            "Result": float(b.get("spill_t", 0.0)),
            "Unit / basis": "t emergency slack",
        },
        {
            "Plan-generation check": "Validated execution windows",
            "Result": int(b.get("minute_timing_validated_windows", 0) or 0),
            "Unit / basis": "minute-schedule windows",
        },
        {
            "Plan-generation check": "Minute timing errors",
            "Result": int(b.get("minute_timing_errors", 0) or 0),
            "Unit / basis": "conflicts requiring replanning",
        },
        {
            "Plan-generation check": "Bucket timing fallbacks",
            "Result": int(b.get("minute_timing_fallback_buckets", 0) or 0),
            "Unit / basis": "planning buckets",
        },
    ]
    scope = str(getattr(plan, "solver_optimality_scope", "whole_plan"))
    if scope == "mixed_component_reference":
        rows.extend([
            {
                "Plan-generation check": "Tactical component solver gap",
                "Result": float(b.get(
                    "planning_tactical_relative_gap_pct", 0.0)),
                "Unit / basis": "% component gap; not a merged-plan certificate",
            },
            {
                "Plan-generation check": "Near-term component solver gap",
                "Result": float(b.get(
                    "planning_near_relative_gap_pct", 0.0)),
                "Unit / basis": "% component gap; not a merged-plan certificate",
            },
        ])
    else:
        rows.append({
            "Plan-generation check": "Solver relative gap",
            "Result": float(getattr(
                plan, "solver_relative_gap_pct", 0.0)),
            "Unit / basis": (
                "% conditional-window aggregate"
                if scope == "conditional_rolling_windows" else
                "% whole-plan objective bound"),
        })
    rows.extend([
        {
            "Plan-generation check": "Solver branches",
            "Result": int(getattr(plan, "solver_branches", 0)),
            "Unit / basis": "CP-SAT search branches",
        },
        {
            "Plan-generation check": "Solver conflicts",
            "Result": int(getattr(plan, "solver_conflicts", 0)),
            "Unit / basis": "CP-SAT search conflicts",
        },
    ])
    return rows


def _physical_inventory_by_site(scn, inventory) -> dict[str, list[float]]:
    """Aggregate physical stockpiles without hopper or process-WIP buffers."""
    if not inventory:
        return {}
    physical_spaces = {
        space.id: space
        for space in scn.spaces
        if space.area not in {"mine-interface", "process-interface"}
    }
    available = [
        list(values)
        for space_id, values in inventory.items()
        if space_id in physical_spaces and values
    ]
    if not available:
        return {}
    points = max(len(values) for values in available)
    totals: dict[str, list[float]] = {}
    for space_id, values in inventory.items():
        space = physical_spaces.get(space_id)
        if space is None or not values:
            continue
        series = list(values)
        if len(series) < points:
            series.extend([series[-1]] * (points - len(series)))
        site_values = totals.setdefault(space.site, [0.0] * points)
        for index, value in enumerate(series[:points]):
            site_values[index] += float(value)
    return dict(sorted(totals.items()))


def _inventory_site_figure(scn, inventory) -> go.Figure:
    """Readable inventory trend with one trace per operating site."""
    totals = _physical_inventory_by_site(scn, inventory)
    fig = go.Figure()
    colors = {
        "BENGUERIR": GREEN,
        "LOUTA": ORANGE,
        "MPH": NAVY,
    }
    for site, values in totals.items():
        site_label = "MPH" if site == "MPH" else site.title()
        fig.add_scatter(
            x=list(range(len(values))),
            y=values,
            name=site_label,
            mode="lines",
            line=dict(color=colors.get(site, BLUE), width=3),
            hovertemplate=(
                f"<b>{site_label}</b><br>"
                "Boundary %{x}<br>Inventory %{y:,.0f} t"
                "<extra></extra>"
            ),
        )
    fig = plotly_layout(fig, "Physical stock inventory by site", 330)
    fig.update_layout(
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
        margin=dict(l=72, r=24, t=48, b=48),
    )
    fig.update_xaxes(title="planning boundary")
    fig.update_yaxes(title="inventory [t]", rangemode="tozero")
    return fig


def _demand_delivery_figure(k) -> go.Figure:
    """Compare horizon demand and delivery with two semantic traces."""
    rows = [
        (
            destination,
            float(values.get("demand_t", 0.0)),
            float(values.get("delivered_t", 0.0)),
        )
        for destination, values in k["per_destination"].items()
    ]
    rows.sort(key=lambda row: (max(row[1], row[2]), row[0]))
    labels = [row[0] for row in rows]
    fig = go.Figure()
    fig.add_bar(
        x=[row[1] for row in rows],
        y=labels,
        name="Demand",
        orientation="h",
        marker_color="#cbd5e1",
        text=[theme.fmt_t(row[1]) for row in rows],
        textposition="outside",
        cliponaxis=False,
        hovertemplate="<b>%{y}</b><br>Demand %{x:,.0f} t<extra></extra>",
    )
    fig.add_bar(
        x=[row[2] for row in rows],
        y=labels,
        name="Delivered",
        orientation="h",
        marker_color=NAVY,
        text=[theme.fmt_t(row[2]) for row in rows],
        textposition="outside",
        cliponaxis=False,
        hovertemplate="<b>%{y}</b><br>Delivered %{x:,.0f} t<extra></extra>",
    )
    height = max(320, 112 + 34 * len(rows))
    fig = plotly_layout(
        fig, "Demand and planned delivery", height, barmode="group")
    fig.update_layout(
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
        margin=dict(l=145, r=78, t=48, b=48),
    )
    fig.update_xaxes(title="horizon total [t]", rangemode="tozero")
    fig.update_yaxes(title="", categoryorder="array", categoryarray=labels)
    return fig


def _offspec_figure(offspec: dict[str, list[float]]) -> go.Figure:
    """Compact destination-level soft-quality deviation summary."""
    all_rows = sorted(
        ((destination, float(sum(values)))
         for destination, values in offspec.items()),
        key=lambda row: (row[1], row[0]),
    )
    rows = [row for row in all_rows if row[1] > 1e-9]
    labels = [row[0] for row in rows]
    values = [row[1] for row in rows]
    fig = go.Figure()
    if rows:
        fig.add_bar(
            x=values,
            y=labels,
            name="Soft-quality deviation",
            orientation="h",
            marker_color=RED,
            text=[f"{value:,.0f}" for value in values],
            textposition="outside",
            cliponaxis=False,
            hovertemplate=(
                "<b>%{y}</b><br>Deviation %{x:,.0f} t·unit<extra></extra>"
            ),
            showlegend=False,
        )
    else:
        fig.add_annotation(
            text="No planned soft-quality deviation",
            showarrow=False,
            font=dict(size=14, color=DGREEN),
        )
    height = max(300, 110 + 34 * len(rows))
    fig = plotly_layout(
        fig, "Planned soft-quality deviation", height)
    fig.update_layout(margin=dict(l=145, r=72, t=48, b=48))
    fig.update_xaxes(title="deviation [t·unit]", rangemode="tozero")
    fig.update_yaxes(title="", categoryorder="array", categoryarray=labels)
    return fig


def render_sidebar_planning_readiness(scn):
    """Render the no-plan readiness workflow beside the primary solve action."""
    ss = st.session_state
    issues = ss.issues
    n_err = sum(1 for i in issues if i.severity == "ERROR")
    n_wrn = sum(1 for i in issues if i.severity == "WARNING")
    n_asm = sum(1 for grp in (scn.spaces, scn.machines, scn.links,
                              scn.destinations, scn.grades, scn.sites)
                for it in grp if it.evidence.status in ("ASSUMED", "TBC"))
    dom = sorted({s_.site for s_ in scn.spaces})
    short = html.escape(
        common.planning_scenario_code(scn.name)
        or scn.name.split(" —")[0]
    )
    sites = html.escape(" · ".join(dom))
    warning_word = "warning" if n_wrn == 1 else "warnings"
    error_word = "error" if n_err == 1 else "errors"

    if n_err:
        badge_class = "blocked"
        badge_text = f"Blocked · {n_err} {error_word}"
        title = "Resolve input errors before planning"
        step_class = "blocked"
        step_note = f"{n_err} blocking {error_word} · {n_wrn} {warning_word}"
    elif n_wrn:
        badge_class = "warning"
        badge_text = f"Ready with {n_wrn} {warning_word}"
        title = "Ready to generate a plan"
        step_class = "active"
        step_note = f"{n_wrn} {warning_word} to review · no blockers"
    else:
        badge_class = "ready"
        badge_text = "Inputs ready"
        title = "Ready to generate a plan"
        step_class = "done"
        step_note = "Validation passed · no blockers"
    completed_checks = 1 if (n_err or n_wrn) else 2

    important = next((i for i in issues if i.severity == "ERROR"), None)
    if important is None:
        important = next((i for i in issues if i.severity == "WARNING"), None)

    with st.container(key="sidebar_planning_readiness", border=True):
        st.markdown(
            f'<div class="sidebar-ready-head">'
            f'<div class="sidebar-ready-eyebrow">Planning readiness</div>'
            f'<span class="sidebar-ready-badge {badge_class}">{badge_text}</span>'
            f'</div><div class="sidebar-ready-title">{title}</div>'
            f'<div class="sidebar-ready-progress" aria-label="Planning progress: '
            f'{completed_checks} of three checks complete">'
            f'<span class="done">✓</span><i></i>'
            f'<span class="{step_class}">2</span><i></i>'
            f'<span class="pending">3</span></div>'
            f'<div class="sidebar-ready-step-labels"><span>Scenario</span>'
            f'<span>Inputs</span><span>Generate</span></div>',
            unsafe_allow_html=True)

        st.markdown(
            f'<div class="sidebar-ready-facts">'
            f'<span><b>{short}</b> · {sites}</span>'
            f'<span>{scn.sim.horizon_buckets:g} × {scn.sim.dt_h:g} h · '
            f'{n_asm} planning notes · {len(scn.assumptions)} assumptions</span>'
            f'<span>{html.escape(step_note)}</span></div>',
            unsafe_allow_html=True)

        if important is not None:
            issue_class = "error" if important.severity == "ERROR" else "warning"
            issue_icon = "✖" if important.severity == "ERROR" else "⚠"
            st.markdown(
                f'<div class="sidebar-ready-issue {issue_class}">'
                f'<b>{issue_icon} {html.escape(important.severity.title())} · '
                f'{html.escape(important.code)}</b>'
                f'<span>{html.escape(theme.operator_text(important.message))}'
                f'</span></div>',
                unsafe_allow_html=True)
        else:
            st.markdown(
                '<div class="sidebar-ready-issue ok"><b>✓ Inputs validated</b>'
                '<span>No blockers or warnings.</span></div>',
                unsafe_allow_html=True)

        if st.button("Review assumptions", key="es_assump", width="stretch"):
            common.goto_page("12 · Assumptions & Data Gaps")
            st.rerun()
        if st.button("Validate inputs", key="es_validate", width="stretch"):
            iss = common.revalidate()
            e = sum(1 for i in iss if i.severity == "ERROR")
            (st.success if e == 0 else st.error)(
                f"Validation complete: {e} errors · "
                f"{sum(1 for i in iss if i.severity == 'WARNING')} warnings. "
                "Details are in the issue drawer below.")
        if st.button("Review S0 demand", key="es_run_plan", width="stretch"):
            common.goto_page("5 · Demand & Quality")
            st.rerun()


def preplan_kpis(scn) -> dict[str, float | int]:
    """Return useful KPI baselines that only depend on scenario inputs."""
    horizon = int(scn.sim.horizon_buckets)
    demand_rows = [
        entry for entry in scn.demand
        if 0 <= int(entry.bucket) < horizon and entry.rate_tph > 0
    ]
    total_capacity_t = sum(max(float(space.capacity_t), 0.0)
                           for space in scn.spaces)
    opening_inventory_t = sum(max(float(space.mass_t), 0.0)
                              for space in scn.spaces)
    protected_spaces = [space for space in scn.spaces
                        if float(space.safety_stock_t) > 0]
    scheduled_controls = sum(
        1 for entry in scn.calendars
        if int(entry.start_bucket) < horizon and int(entry.end_bucket) >= 0
    )
    demand_by_bucket: dict[int, float] = {}
    for entry in demand_rows:
        demand_by_bucket[int(entry.bucket)] = (
            demand_by_bucket.get(int(entry.bucket), 0.0)
            + float(entry.rate_tph)
        )
    horizon_h = horizon * float(scn.sim.dt_h)
    planning_machines = [
        machine for machine in scn.machines
        if bool(getattr(machine, "planning_enabled", True))
    ]
    ready_machines = sum(bool(machine.available)
                         for machine in planning_machines)
    return {
        "requested_demand_t": sum(
            float(entry.rate_tph) * float(scn.sim.dt_h)
            for entry in demand_rows
        ),
        "demand_destinations": len({entry.dest_id for entry in demand_rows}),
        "opening_inventory_t": opening_inventory_t,
        "opening_capacity_pct": (
            100.0 * opening_inventory_t / total_capacity_t
            if total_capacity_t > 0 else 0.0
        ),
        "spaces_below_safety": sum(
            float(space.mass_t) < float(space.safety_stock_t) - 1e-6
            for space in protected_spaces
        ),
        "protected_spaces": len(protected_spaces),
        "scheduled_controls": scheduled_controls,
        "average_demand_tph": (
            sum(float(entry.rate_tph) * float(scn.sim.dt_h)
                for entry in demand_rows) / horizon_h
            if horizon_h > 0 else 0.0
        ),
        "peak_demand_tph": max(demand_by_bucket.values(), default=0.0),
        "ready_machines": ready_machines,
        "planning_machines": len(planning_machines),
        "machine_readiness_pct": (
            100.0 * ready_machines / len(planning_machines)
            if planning_machines else 0.0
        ),
        "storage_headroom_t": max(total_capacity_t - opening_inventory_t, 0.0),
        "storage_headroom_pct": (
            100.0 * max(total_capacity_t - opening_inventory_t, 0.0)
            / total_capacity_t if total_capacity_t > 0 else 0.0
        ),
    }


def _empty_state(scn):
    """Show input-derived KPI context before a plan has been generated."""
    baseline = preplan_kpis(scn)
    def count_label(value: int, singular: str,
                    plural: str | None = None) -> str:
        return f"{value} {singular if value == 1 else plural or singular + 's'}"

    st.markdown("#### Pre-run KPI baseline")
    st.caption(
        "Calculated from the active scenario inputs. Generate a plan to add "
        "service, quality, utilisation and execution KPIs."
    )
    c = st.columns(4)
    c[0].metric("Requested demand", theme.fmt_t(
        baseline["requested_demand_t"]))
    c[0].caption(
        count_label(baseline["demand_destinations"],
                    "active receiving destination"))
    c[1].metric("Opening inventory", theme.fmt_t(
        baseline["opening_inventory_t"]))
    c[1].caption(
        f"{baseline['opening_capacity_pct']:.1f}% of modelled capacity")
    c[2].metric("Opening stock risk",
                count_label(baseline["spaces_below_safety"], "space"))
    c[2].caption(
        count_label(baseline["protected_spaces"],
                    "safety-stock limit checked",
                    "safety-stock limits checked"))
    c[3].metric(
        "Demand satisfaction",
        "Pending plan",
        help="Delivered demand divided by requested demand. It requires a "
             "generated plan and is not assumed from input capacity.",
    )
    c[3].caption("Target 100% · calculated after generation")

    st.markdown("##### Capacity & performance envelope")
    c = st.columns(4)
    c[0].metric("Average demand rate",
                f"{baseline['average_demand_tph']:,.0f} t/h")
    c[0].caption("average requested load across the horizon")
    c[1].metric("Peak demand rate",
                f"{baseline['peak_demand_tph']:,.0f} t/h")
    c[1].caption("highest combined receiving request")
    c[2].metric(
        "Equipment ready",
        f"{baseline['ready_machines']} / {baseline['planning_machines']}",
    )
    c[2].caption(
        f"{baseline['machine_readiness_pct']:.1f}% of planning-enabled machines")
    c[3].metric("Storage headroom", theme.fmt_t(
        baseline["storage_headroom_t"]))
    c[3].caption(
        f"{baseline['storage_headroom_pct']:.1f}% of capacity available")
    _render_energy_movement_kpis(scn, None, None)


def _energy_movement_result(scn, plan, kpis):
    """Combine core energy accounting with validated movement scheduling."""
    usage = theme.machine_usage_summary(scn, plan)
    applicable = usage[usage["usage_applicable"].fillna(False)] \
        if not usage.empty else usage
    positioning_by_machine = {
        str(row["machine_id"]): float(row["positioning_h"])
        for _, row in applicable.iterrows()
        if pd.notna(row["positioning_h"])
    }
    conflict_h = float(pd.to_numeric(
        applicable.get("conflict_h", pd.Series(dtype=float)),
        errors="coerce").fillna(0.0).sum())
    conflict_count = int(pd.to_numeric(
        applicable.get("conflict_count", pd.Series(dtype=float)),
        errors="coerce").fillna(0).sum())
    # Electricity is a full generated-plan estimate.  Do not divide the full
    # plan's energy by only the elapsed delivery of a partial simulation.
    plan_kpis = compute_kpis(scn, plan, None)
    result = compute_plan_energy(
        scn,
        plan,
        float(plan_kpis.get("final_product_on_time_t", 0.0) or 0.0),
        positioning_hours_by_machine=positioning_by_machine,
        conflict_hours=conflict_h,
        conflict_count=conflict_count,
    )
    kpis.update({
        "specific_electricity_kwh_per_t":
            result["specific_kwh_per_delivered_t"],
        "energy_reference_kwh_per_t": result["reference_kwh_per_t"],
        "modelled_energy_kwh": result["total_kwh"],
        "positioning_h": result["positioning_h"],
        "machine_conflicts": result["conflict_count"],
        "handling_factor": result["handling_factor"],
    })
    return result


def _render_energy_model_editor(scn) -> None:
    """Expose the provisional energy reference and coefficients."""
    params = energy_model_parameters(scn)
    work = dict(params["work_kwh_per_t"])
    with st.expander("Energy KPI reference & model assumptions", expanded=False):
        st.caption(
            "Replace these provisional planning coefficients with approved "
            "motor/load measurements and an agreed metered baseline. Reference "
            "and positioning settings affect KPI reporting. When the opt-in "
            "energy objective is enabled, productive-work and route-link "
            "coefficients also affect the next plan; positioning remains "
            "outside that objective."
        )
        first = st.columns(3)
        reference = first[0].number_input(
            "Reference [kWh/useful t]", min_value=0.001,
            value=float(params["reference_kwh_per_t"]), step=0.05,
            key="energy_reference_input")
        link_intensity = first[1].number_input(
            "Conveyor link [kWh/t/link]", min_value=0.0,
            value=float(params["link_kwh_per_t"]), step=0.01,
            key="energy_link_input")
        positioning_power = first[2].number_input(
            "Positioning power [kW]", min_value=0.0,
            value=float(params["positioning_power_kw"]), step=10.0,
            key="energy_positioning_power_input")
        second = st.columns(3)
        stack = second[0].number_input(
            "Stacking [kWh/t]", min_value=0.0,
            value=float(work["stack"]), step=0.01,
            key="energy_stack_input")
        reclaim = second[1].number_input(
            "Reclaiming [kWh/t]", min_value=0.0,
            value=float(work["reclaim"]), step=0.01,
            key="energy_reclaim_input")
        dispatch = second[2].number_input(
            "Dispatch/other [kWh/t]", min_value=0.0,
            value=float(work["dispatch"]), step=0.01,
            key="energy_dispatch_input")
        if st.button("Apply energy model", key="apply_energy_model"):
            scn.config["ENERGY_REFERENCE_KWH_PER_T"] = float(reference)
            scn.config["ENERGY_LINK_KWH_PER_T"] = float(link_intensity)
            scn.config["ENERGY_POSITIONING_POWER_KW"] = float(
                positioning_power)
            scn.config["ENERGY_WORK_KWH_PER_T"] = {
                "stack": float(stack),
                "reclaim": float(reclaim),
                "dispatch": float(dispatch),
                "default": float(dispatch),
            }
            stale = False
            if scn.config.get("ENERGY_OBJECTIVE_ENABLED", False) is True:
                stale = common.invalidate_outputs()
            common.revalidate()
            notice = "Energy model updated for the active scenario."
            if stale:
                notice += " Previous plan cleared because the energy objective is enabled."
            st.session_state["energy_model_notice"] = notice
            st.rerun()


def _render_energy_movement_kpis(scn, plan, kpis) -> None:
    """Render primary electricity, positioning and conflict KPIs."""
    notice = st.session_state.pop("energy_model_notice", "")
    if notice:
        st.success(notice)
    params = energy_model_parameters(scn)
    valid_plan = (
        plan is not None
        and str(getattr(plan, "status", "") or "").upper()
        in {"OPTIMAL", "FEASIBLE"}
    )
    result = (
        _energy_movement_result(scn, plan, kpis)
        if valid_plan and kpis is not None else None
    )
    st.markdown("#### Energy & movement performance")
    cards = st.columns(6)
    if result is None:
        cards[0].metric("Specific electricity", "Pending plan")
        cards[1].metric(
            "Reference", f"{float(params['reference_kwh_per_t']):.2f} kWh/t")
        cards[2].metric("Reference gap", "Pending plan")
        cards[3].metric("Positioning hours", "Pending plan")
        cards[4].metric("Machine conflicts", "Pending plan")
        cards[5].metric("Handling factor", "Pending plan")
    else:
        specific = result["specific_kwh_per_delivered_t"]
        variance = result["reference_variance_kwh_per_t"]
        variance_pct = result["reference_variance_pct"]
        cards[0].metric(
            "Specific electricity",
            f"{specific:.2f} kWh/t" if specific is not None else "N/A",
            help="Modelled electricity divided by useful on-time external "
                 "delivery. Rehandling increases energy without increasing "
                 "the denominator.")
        cards[1].metric(
            "Reference", f"{result['reference_kwh_per_t']:.2f} kWh/t")
        cards[2].metric(
            "Reference gap",
            f"{variance:+.2f} kWh/t" if variance is not None else "N/A")
        cards[2].caption(
            (f"{abs(variance_pct):.1f}% {result['reference_status']}"
             if variance_pct is not None else "no useful delivery denominator"))
        cards[3].metric("Positioning hours", f"{result['positioning_h']:.1f} h")
        cards[3].caption(f"{result['positioning_kwh']:,.0f} kWh modelled")
        cards[4].metric("Machine conflicts", f"{result['conflict_count']}")
        cards[4].caption(f"{result['conflict_h']:.1f} h unresolved exposure")
        handling = result["handling_factor"]
        cards[5].metric(
            "Handling factor", f"{handling:.2f}×" if handling is not None else "N/A")
        cards[5].caption("machine-handled t / useful delivered t")
        st.caption(
            f"Modelled electricity: **{result['total_kwh']:,.0f} kWh** · "
            f"productive machines {result['work_kwh']:,.0f} kWh · conveyor "
            f"traversals {result['link_kwh']:,.0f} kWh · empty positioning "
            f"{result['positioning_kwh']:,.0f} kWh. Conflicts are planning "
            "alerts, not counted as consumed electricity."
        )
    st.caption(
        "Reference and electrical coefficients are "
        + ("scenario-configured." if params["configured"] else
           "provisional ASSUMED values until plant meter and motor-load data are supplied.")
    )
    for warning in params["warnings"]:
        st.warning(str(warning))
    _render_energy_model_editor(scn)
    _render_equipment_time_kpis(scn, plan)


def _render_equipment_time_kpis(scn, plan) -> None:
    """Show fleet-time use and non-productive exposure on the KPI page."""
    result = theme.fleet_usage_kpis(scn, plan)
    st.markdown("#### Equipment time & utilisation")
    st.caption(
        "Scheduled starts, stops, group changes, productive hours and empty "
        "travel are wear-risk and efficiency proxies. They are not measured "
        "equipment efficiency, component wear or remaining useful life; those "
        "require load, condition and maintenance-history telemetry."
    )
    cards = st.columns(6)
    if not result["valid_plan"] or not result["eligible_machines"]:
        cards[0].metric("Equipment used", "Pending plan")
        cards[1].metric("Productive utilisation", "Pending plan")
        cards[2].metric("Scheduled occupation", "Pending plan")
        cards[3].metric("Idle capacity", "Pending plan")
        cards[4].metric("Positioning / empty travel", "Pending plan")
        cards[5].metric("Conflict-loss exposure", "Pending plan")
        st.caption(
            "Generate a valid plan to allocate available machine-hours into "
            "productive work, positioning and idle time.")
        return

    available_h = float(result["available_h"])
    cards[0].metric(
        "Equipment used",
        f"{float(result['fleet_participation_pct']):.1f}%",
        help="Solver-controlled, available machines with productive material "
             "work in this plan.")
    cards[0].caption(
        f"{result['used_machines']} / {result['eligible_machines']} machines - "
        f"{available_h:,.1f} available machine-h")
    cards[1].metric(
        "Productive utilisation",
        f"{float(result['productive_utilisation_pct']):.1f}%",
        help="Material-handling work hours divided by available machine-hours.")
    cards[1].caption(f"{float(result['work_h']):,.1f} h material work")
    cards[2].metric(
        "Scheduled occupation",
        f"{float(result['scheduled_occupation_pct']):.1f}%",
        help="Productive work plus successfully reserved positioning divided "
             "by available machine-hours.")
    cards[2].caption(
        f"{float(result['occupied_h']):,.1f} h work + positioning")
    cards[3].metric(
        "Idle capacity", f"{float(result['idle_pct']):.1f}%",
        help="Unallocated available machine-hours divided by total available "
             "machine-hours. Idle capacity is not automatically lost "
             "production because demand or routing may not require it.")
    cards[3].caption(
        f"{float(result['idle_h']):,.1f} h unallocated - not automatically "
        "lost production")
    cards[4].metric(
        "Positioning / empty travel",
        f"{float(result['positioning_loss_pct']):.1f}%",
        help="Successfully reserved positioning and empty-travel hours divided "
             "by available machine-hours.")
    cards[4].caption(
        f"{float(result['positioning_h']):,.1f} h non-productive occupation")
    cards[5].metric(
        "Conflict-loss exposure",
        f"{float(result['conflict_exposure_pct']):.1f}%",
        help="Unresolved positioning-conflict hours divided by available "
             "machine-hours. This is exposure, not executed time, and is "
             "therefore outside the availability split.")
    cards[5].caption(
        f"{float(result['conflict_h']):,.1f} h across "
        f"{result['conflict_count']} unresolved positioning conflicts")

    residual = float(result["reconciliation_error_h"])
    st.caption(
        "Availability split: productive "
        f"**{float(result['productive_utilisation_pct']):.1f}%** + positioning "
        f"**{float(result['positioning_loss_pct']):.1f}%** + idle "
        f"**{float(result['idle_pct']):.1f}%** = **100.0%** "
        f"({available_h:,.1f} available machine-h). Calendar-blocked time is "
        "excluded. Conflict exposure is reported separately and is not "
        "double-counted as executed time."
    )
    if abs(residual) > 1e-6:
        st.error(
            f"Equipment-time reconciliation error: {residual:+.3f} h. "
            "Review overlapping work or calendar reservations.")


# =========================================================== 1. Executive
def page_overview(monitor_compact: bool = False):
    st.subheader("KPIs" if monitor_compact else "Executive Overview")
    scn, p, s = common.sc(), common.plan(), common.sim()
    mode = common.chain_mode(scn)
    if mode == "m16-only":
        st.warning("M16-only demonstration. Upstream corridor arrivals are "
                   "simulated inputs. Use S3 for the complete three-site "
                   "phosphate value chain.")
    k = _kpis()
    if k is None:
        _empty_state(scn)
        return
    T = scn.sim.horizon_buckets

    # ---- executive KPIs: one measure per decision --------------------------
    tot_dem = sum(v["demand_t"] for v in k["per_destination"].values())
    tot_del = sum(v["on_time_t"]
                  for v in k["per_destination"].values())
    bottlenecks = sum(1 for util in k["route_peak_utilisation_pct"].values()
                      if util >= 85.0)
    c = st.columns(4)
    c[0].metric("Demand satisfaction", theme.fmt_pct(k["service_pct"]),
                help="Due-bucket delivery / demand; process feed is credited within its daily grade campaign.")
    c[0].caption(f"{theme.fmt_t(tot_del)} / {theme.fmt_t(tot_dem)} · "
                 f"unmet {theme.fmt_t(k['unmet_t'])}")
    c[1].metric(
        "On-spec delivered mass", (theme.fmt_pct(k["on_spec_mass_pct"])
                                   if k["on_spec_mass_pct"] is not None else "N/A"),
        help="Known compliant terminal tonnes / terminal tonnes delivered to specified units. Missing assays remain unknown; zero delivery gives N/A.")
    if k["quality_unknown_t"]:
        c[1].caption(f"{k['quality_unknown_t']:,.0f} t with unknown compliance")
    qb = k.get("quality_breakdown", [])
    if any(not r["hard"] for r in qb):
        _asm = any(r["evidence"] in ("ASSUMED", "TBC") for r in qb)
        c[1].caption("soft specifications"
                     + (" · incl. ASSUMED limits" if _asm else ""))
    c[2].metric("Stock-risk buckets",
                f"{k['safety_stock_violation_buckets']}",
                help="buckets below a configured safety-stock level")
    c[2].caption("below configured safety stock")
    c[3].metric("Routes ≥85%", f"{bottlenecks}",
                help="routes whose maximum utilisation is at least 85 %")
    c[3].caption("high-utilisation routes")

    _render_energy_movement_kpis(scn, p, k)
    if washing.enabled(scn):
        st.markdown("##### Washing mass recovery — " + ("executed" if s is not None else "planned"))
        st.caption("SYNTHETIC DEMO. Qualified G10 service is protected first, then raw dry-feed use, then configured treatment costs and operating objectives. Integer-tonne recovery can slightly lower realized yield. Selective material pools are not a spatial stockpile-mixing model.")
        st.dataframe(pd.DataFrame(washing_kpi_rows(scn, k)), hide_index=True, width="stretch")

    if monitor_compact:
        st.markdown("##### Planner objective weights")
        theme.mini_kpis(planner_objective_weight_items(scn))
        flow_penalty = max(
            int(scn.config.get("FLOW_TONNE_PENALTY", 0) or 0), 0)
        active_bucket_penalty = max(
            int(scn.config.get("ACTIVE_BUCKET_PENALTY", 0) or 0), 0)
        coverage_reward = max(
            int(scn.config.get("MACHINE_COVERAGE_REWARD", 0) or 0), 0)
        st.caption(
            "Configured coefficients used to select a plan—not KPI results. "
            "Penalties increase the objective; rewards reduce it; 0 means "
            "inactive. Terms use different units and internal scaling, so "
            "the numbers are priorities rather than percentages. "
            f"Protected controls: emergency-slack penalty "
            f"{BIG_PENALTY:g}. Protected tie-breakers: commanded-flow "
            f"penalty {flow_penalty:g}; active-time penalty "
            f"{active_bucket_penalty:g}; machine-coverage reward "
            f"{coverage_reward:g}."
        )
        st.markdown("##### Operational priorities")
        theme.mini_kpis(operational_priority_items(scn))
        st.caption(
            "ASSUMED, uncalibrated policy values: continuous-feed protection "
            "is the operational priority; energy and equipment-cycle terms "
            "select among feasible alternatives and do not establish an exact "
            "cross-unit ranking. Start, stop and group-change costs are "
            "multiplied by w₅. Work/link energy is opt-in and excludes idle "
            "and positioning energy."
        )

    # ---- highest-priority warnings ---------------------------------------
    warns = [i for i in st.session_state.issues if i.severity in ("ERROR", "WARNING")]
    if p:
        warns_p = p.reasons[:5]
    else:
        warns_p = []
    if warns or warns_p:
        with st.container(border=True):
            st.markdown("**Highest-priority warnings**")
            for i in warns[:4]:
                st.markdown(
                    f"- `{i.severity}` {theme.operator_text(i.message)}")
            for r in warns_p:
                st.markdown(f"- `PLAN` {theme.operator_text(r)}")

    # The consolidated Monitor has a dedicated Stockyard Layout tab. Retain
    # the process mimic on legacy/standalone overview renders, while the KPIs
    # tab stays focused on decisions, trends and site status.
    if not monitor_compact:
        st.markdown("#### Process mimic — " + {
            "three-site": ("Domain A: LOUTA + BENGUERIR → MPH M16 hub ("
                           + report_ref() + ")"),
            "m16-only": "M16 hub only (mines not modelled in this scenario)",
            "sulphur": (
                "Domain B: M17 sulphur (no phosphate chain in this scenario)"),
        }[mode])
        if mode == "sulphur":
            plot(_value_chain_fig(scn, k, mode, s))
        else:
            from .process_mimic import render_process_mimic
            render_process_mimic(scn, p, s, collapse_controls=True)
    if mode == "three-site":
        _site_cards(scn, p, s, k)

    # ---- on-spec breakdown (only when below 100 %) --------------------------
    if k and k["onspec_pct"] < 100 - 1e-9 and k.get("quality_breakdown"):
        with st.expander(f"🔍 On-spec breakdown — {k['onspec_pct']:.1f} % "
                         "(why not 100 %)", expanded=False):
            pr = k.get("onspec_principal")
            if pr and not pr["hard"]:
                asm = (" assumed" if pr["evidence"] in ("ASSUMED", "TBC")
                       else "")
                st.markdown(
                    f"On-spec performance includes{asm} **soft quality "
                    f"limits**. The current result is driven primarily by "
                    f"**{pr['destination']} {pr['parameter']}** buckets "
                    f"({pr['violations']} violating); **the plan remains "
                    f"feasible because this specification is configured as "
                    f"soft** (violations carry an objective penalty w₂ and "
                    f"are reported transparently — they are not errors).")
            elif pr and pr["hard"]:
                st.markdown(
                    f"Driven primarily by **{pr['destination']} "
                    f"{pr['parameter']}** buckets on a **hard** "
                    f"specification. The optimiser blocks any allocation "
                    f"violating a hard limit, so these violations come from "
                    f"simulated execution deviations, not from the plan.")
            st.caption("**Hard** violation → blocks feasibility (the "
                       "optimiser leaves demand unmet rather than violate "
                       "it). **Soft** violation → permitted with an "
                       "objective penalty (w₂) and reported here. "
                       "Specifications tagged as planning assumptions are "
                       "placeholders, "
                       "not engineering facts.")
            bdf = pd.DataFrame([{
                "destination": r["destination"], "parameter": r["parameter"],
                "limit": (f"≥ {r['limit_min']:g}" if r["limit_min"] is not None
                          else "") + (f" ≤ {r['limit_max']:g}"
                                      if r["limit_max"] is not None else ""),
                "hard/soft": "hard" if r["hard"] else "soft",
                "evidence": r["evidence"],
                "compliant/evaluated": f"{r['compliant']}/{r['evaluated']}",
                "compliance %": round(r["compliance_pct"], 1),
                "max dev": round(r["max_dev"], 2),
            } for r in k["quality_breakdown"]])
            show_df(bdf)
            st.caption("Full breakdown with filters and violation "
                       "magnitudes: **Results & KPIs** page.")


    # The combined Monitor page deliberately stops at the KPI/site-status
    # summary. Project-team request (29-Jul-2026): temporarily hide the
    # inventory, demand/delivery and quality-deviation graphs while retaining
    # their helpers for a possible later detailed-results view.
    if monitor_compact:
        return

    # ---- planning performance ----------------------------------------------
    # Keep this page decision-oriented: aggregate physical inventory by site,
    # use two semantic demand traces, and leave minute-accurate machine usage
    # to its dedicated Monitor page.
    x = list(range(k.get("evaluated_buckets", T)))
    st.markdown("#### Planning performance")
    inv_src = s.inv_series if s else p.inventory if p else None
    if inv_src:
        plot(_inventory_site_figure(scn, inv_src))
        st.caption(
            "Physical stockpiles only; short-term mine hoppers and process "
            "WIP buffers are excluded from this overview.")

    demand_col, quality_col = st.columns([1.15, 0.85])
    with demand_col:
        plot(_demand_delivery_figure(k))
        st.caption(
            "Horizon totals. Zero-demand destinations remain visible so an "
            "inactive train or plant feed is explicit.")
    with quality_col:
        has_bpl = any(sp_.param == "BPL" for d in scn.destinations
                      for sp_ in d.spec)
        if s and has_bpl:
            qfig = go.Figure()
            for i, d in enumerate(scn.destinations):
                if not d.spec:
                    continue
                bpl = [s.delivered_q[d.id][t].get("BPL") for t in x]
                qfig.add_scatter(
                    x=x, y=bpl, name=d.id, mode="lines",
                    line=dict(
                        color=theme.MACHINE_COLORS[
                            i % len(theme.MACHINE_COLORS)],
                        width=2),
                    hovertemplate=(
                        f"<b>{d.id}</b><br>Bucket %{{x}}"
                        "<br>BPL %{y:.2f}%<extra></extra>"),
                )
                mn = next(
                    (sp.pmin for sp in d.spec if sp.param == "BPL"), None)
                if mn:
                    qfig.add_hline(
                        y=mn, line_dash="dot", line_color=RED)
            qfig.update_yaxes(title="BPL [%]")
            qfig.update_xaxes(title="bucket")
            qfig.update_layout(
                legend=dict(
                    orientation="h", yanchor="bottom", y=1.0, x=0))
            plot(plotly_layout(
                qfig, "Delivered quality against specification", 330))
        elif p and p.offspec:
            plot(_offspec_figure(p.offspec))
            st.caption(
                "Zero is on-spec; non-zero values are soft-quality "
                "deviations, not delivered tonnage.")

    # ---- current job timeline ------------------------------------------------
    if p and not monitor_compact:
        st.markdown("#### Current job timeline")
        jobs = list(s.jobs.values()) if s else p.jobs
        df = jobs_to_gantt_df(
            scn, jobs, coalesce_execution_windows=True)
        register = theme.timeline_machine_register(scn, p)
        st.caption(
            "Current solver-controlled machines are listed. An empty row "
            "retains the plan's structured idle or modelling-gap reason; "
            "reference/future assets are available in Job Schedule on request.")
        plot(gantt_figure(
            df, color_by="kind", group_by="machine",
            height=theme.timeline_height(register, minimum=340),
            row_register=register, show_tonnage=True))


def _box(fig, x, y, title, sub, color, w=0.5, h=0.32, dash=None,
         fill="#ffffff", tsize=12):
    fig.add_shape(type="rect", x0=x - w, x1=x + w, y0=y - h, y1=y + h,
                  line=dict(color=color, width=2.4, dash=dash), fillcolor=fill)
    fig.add_annotation(x=x, y=y + 0.07, text=f"<b>{title}</b>", showarrow=False,
                       font=dict(size=tsize, color=color))
    if sub:
        fig.add_annotation(x=x, y=y - 0.14, text=sub, showarrow=False,
                           font=dict(size=10, color="#4d5b66"))


def _arrow(fig, x0, y0, x1, y1, label="", color=GREY, dash=None, above=True):
    """Arrow from (x0,y0) pointing AT (x1,y1)."""
    fig.add_annotation(x=x1, y=y1, ax=x0, ay=y0, xref="x", yref="y",
                       axref="x", ayref="y", showarrow=True, arrowhead=2,
                       arrowwidth=3, arrowcolor=color)
    if dash:  # arrows can't dash: overlay a dashed line for "simulated" feel
        fig.add_shape(type="line", x0=x0, x1=x1, y0=y0, y1=y1,
                      line=dict(color=color, width=1, dash=dash))
    if label:
        fig.add_annotation(x=(x0 + x1) / 2, y=(y0 + y1) / 2 + (0.16 if above
                                                               else -0.16),
                           text=label, showarrow=False,
                           font=dict(size=9.5, color=NAVY))


def _corridor_label(scn, link) -> str:
    km = ""
    m = re.search(r"(\d+)\s*km", link.evidence.note or "")
    if m:
        km = f"{m.group(1)} km · "
    hrs = link.transit_buckets * scn.sim.dt_h
    return (f"<b>{link.id}</b> · {km}{link.capacity_tph:,.0f} t/h · "
            f"transit time = {hrs:g} h")


def _inv_t(scn, s, site: str, area_prefix: str = "") -> float:
    tot = 0.0
    for sp in scn.spaces:
        if sp.site == site and (not area_prefix or sp.area.startswith(area_prefix)):
            tot += (s.inv[sp.id] if s else sp.mass_t)
    return tot


def _value_chain_fig(scn, k, mode: str, s=None) -> go.Figure:
    """Scenario-aware Domain schematic. Arrows always point TOWARD M16
    (receiving hub) from the mine source yards (current-report hierarchy)."""
    fig = go.Figure()
    node_site = {n.id: n.site for n in scn.nodes}

    if mode == "sulphur":
        _box(fig, 0, 0, "M17 OPEN STORE",
             f"2 × 100 kt · inv {_inv_t(scn, s, 'MPH', 'M17-open') / 1000:,.0f} kt",
             BLUE)
        _box(fig, 1.6, 0, "M17 CLOSED STORE",
             f"inv {_inv_t(scn, s, 'MPH', 'M17-closed') / 1000:,.0f} kt", BLUE)
        _box(fig, 3.2, 0, "MOLTEN-S", "sulphur melting units · destination",
             DGREEN)
        _arrow(fig, 0.45, 0, 1.15, 0, "manual payloaders 300 t/h (exogenous)",
               GREY, dash="dot")
        _arrow(fig, 2.05, 0, 2.75, 0, "RC01 · 1,100 t/h", GREEN)
        fig.add_annotation(x=1.6, y=-0.75, showarrow=False,
                           font=dict(size=9.5, color=GREY),
                           text="Domain B (M17 sulphur) — separate planning "
                                "domain; no phosphate chain in this scenario")
        fig.update_xaxes(visible=False, range=[-0.6, 3.8])
        fig.update_yaxes(visible=False, range=[-1.0, 0.7])
        return plotly_layout(fig, height=240)

    # destinations at MPH (right column)
    dests = [d for d in scn.destinations if d.site == "MPH"]
    hub_x, hub_y = 1.7, 0.0
    for i, d in enumerate(dests):
        y = 0.9 - 1.8 * i / max(len(dests) - 1, 1) if len(dests) > 1 else 0.0
        sub = "destination · wet process" if d.id.startswith("PAP") else             "dest → drying → dry store → rail → SPH"
        _box(fig, 3.4, y, d.id, sub, DGREEN, w=0.56)
        srv = ""
        if k and d.id in k.get("per_destination", {}):
            srv = f"service {k['per_destination'][d.id]['service_pct']:.0f} %"
        _arrow(fig, hub_x + 0.55, hub_y + (y - hub_y) * 0.35, 2.88, y,
               srv, GREEN, above=(y >= 0))

    hub_inv = _inv_t(scn, s, "MPH", "M16") / 1000
    _box(fig, hub_x, hub_y, "MPH M16 HUB · SMS",
         f"receiving · blending · buffer · inv {hub_inv:,.0f} kt",
         DORANGE, w=0.66, h=0.38, fill="#fdf6ea", tsize=13)

    if mode == "m16-only":
        _box(fig, 0, 0, "SIMULATED corridor-arrival input",
             "mines NOT modelled in S2 — see S3", GREY, w=0.62, dash="dash",
             fill="#f5f6f8")
        _arrow(fig, 0.65, 0, hub_x - 0.65, 0,
               "simulated arrivals (belt-profile input)", GREY, dash="dot")
        fig.update_xaxes(visible=False, range=[-0.75, 4.05])
        fig.update_yaxes(visible=False, range=[-1.45, 1.45])
        return plotly_layout(fig, height=280)

    # three-site: mine source yards feed the hub (arrows point AT M16).
    # Corridor labels live in white boxes clearly OFF the arrow shafts
    # (top corridor above, bottom corridor below), tied by dotted leader lines.
    corridors = [l for l in scn.links if l.transit_buckets > 0]
    mines = [st_ for st_ in scn.sites if st_.role == "mine"]
    ys = {m.id: 1.05 - 2.1 * i / max(len(mines) - 1, 1) if len(mines) > 1
          else 0.0 for i, m in enumerate(mines)}
    for m in mines:
        y = ys[m.id]
        tbc = any(sp.site == m.id and sp.evidence.status in ("ASSUMED", "TBC")
                  for sp in scn.spaces)
        sub = (f"source yard · inv {_inv_t(scn, s, m.id) / 1000:,.0f} kt"
               + (" · planning basis" if m.id == "LOUTA" else
                  " · B02 table · current configuration" if m.id == "BENGUERIR"
                  else " · confirmed (reference pending)" if tbc else ""))
        _box(fig, 0, y, f"{m.id} STOCKYARD · SMS", sub, NAVY, w=0.66, h=0.34,
             tsize=12)
        link = next((l for l in corridors if node_site.get(l.src) == m.id),
                    None)
        if link:
            x0, y0 = 0.65, y
            x1 = hub_x - 0.64
            y1 = hub_y + (0.18 if y > 0 else -0.18)
            _arrow(fig, x0, y0, x1, y1, "", NAVY)        # shaft — no label
            intransit = (sum(pp.mass_t for pp in s.belts
                             if pp.link_id == link.id
                             and pp.arrive_bucket >= s.bucket) if s else
                         sum(bp.mass_t for bp in scn.belt_profile
                             if bp.link_id == link.id))
            lbl = (_corridor_label(scn, link)
                   + f"<br>in transit ≈ {intransit / 1000:,.1f} kt")
            ly = 1.62 if y > 0 else -1.62                # off-shaft position
            fig.add_annotation(x=0.98, y=ly, text=lbl, showarrow=False,
                               font=dict(size=10.5, color=NAVY),
                               bgcolor="rgba(255,255,255,0.97)",
                               bordercolor="#9fb0be", borderwidth=1,
                               borderpad=5)
            fig.add_shape(type="line", x0=0.98,
                          y0=ly + (-0.16 if y > 0 else 0.16),
                          x1=(x0 + x1) / 2, y1=(y0 + y1) / 2,
                          line=dict(color=GREY, width=0.8, dash="dot"))
    # bottom caption (train outlet folded in — keeps the canvas clean)
    train = next((d for d in scn.destinations if d.site == "BENGUERIR"), None)
    cap = (f"Domain A hierarchy ({report_ref()}): the two mine source yards feed "
           "the M16 receiving hub through dedicated corridors C1/C2 — three "
           "peer controlled sites, one coordinated plan")
    if train is not None:
        cap += (f"<br>{train.id}: Benguerir train outlet (planning disabled · "
                "demand "
                f"{train.demand_default_tph:g} t/h)")
    fig.add_annotation(x=1.7, y=-2.16, showarrow=False,
                       font=dict(size=9.5, color=GREY), text=cap)
    fig.update_xaxes(visible=False, range=[-0.75, 4.05])
    fig.update_yaxes(visible=False, range=[-2.4, 1.95])
    return plotly_layout(fig, height=380)


def _site_cards(scn, p, s, k):
    """S3 site-level status cards (Louta / Benguerir / M16 + corridors)."""
    st.markdown("#### Site status — three peer Domain-A sites")
    node_site = {n.id: n.site for n in scn.nodes}
    corridors = {node_site.get(l.src, "?"): l
                 for l in scn.links if l.transit_buckets > 0}
    T = scn.sim.horizon_buckets
    dt = scn.sim.dt_h
    bucket = s.bucket if s else 0
    elapsed_h = max(bucket * dt, dt)

    def dispatch_tph(link) -> tuple[float, str]:
        if s:
            moved = sum(s.moved_by_job.get(j.id, 0.0)
                        for j in s.jobs.values()
                        if j.kind == "dispatch" and j.dest_id == link.id)
            return moved / elapsed_h, "actual"
        if p:
            tot = sum(j.tonnage_t for j in p.jobs
                      if j.kind == "dispatch" and j.dest_id == link.id)
            return tot / (T * dt), "planned avg"
        return 0.0, "no plan"

    def in_transit(link) -> float:
        if s:
            return sum(pp.mass_t for pp in s.belts
                       if pp.link_id == link.id and pp.arrive_bucket >= bucket)
        return sum(bp.mass_t for bp in scn.belt_profile
                   if bp.link_id == link.id)

    def next_arrival(link) -> str:
        if s:
            arr = [pp.arrive_bucket for pp in s.belts
                   if pp.link_id == link.id and pp.arrive_bucket >= bucket]
        else:
            arr = [bp.arrive_bucket for bp in scn.belt_profile
                   if bp.link_id == link.id and bp.arrive_bucket >= 0]
        if not arr:
            return "—"
        return scn.sim.bucket_dt(min(arr)).strftime("%d %b %H:%M")

    def received_tph() -> tuple[float, str]:
        if s:
            moved = sum(s.moved_by_job.get(j.id, 0.0)
                        for j in s.jobs.values() if j.kind == "stack")
            return moved / elapsed_h, "actual"
        if p:
            tot = sum(j.tonnage_t for j in p.jobs if j.kind == "stack")
            return tot / (T * dt), "planned avg"
        return 0.0, "no plan"

    for site_id, note in (("LOUTA", "planning-basis placeholders"),
                          ("BENGUERIR", "B02 table · current configuration")):
        link = corridors.get(site_id)
        c = st.columns(4)
        c[0].metric(f"{site_id} inventory [t]",
                    f"{_inv_t(scn, s, site_id):,.0f}", help=note)
        if link:
            rate, basis = dispatch_tph(link)
            c[1].metric(f"{link.id} dispatch [t/h] ({basis})", f"{rate:,.0f}")
            c[2].metric(f"{link.id} in-transit [t]", f"{in_transit(link):,.0f}")
            c[3].metric(f"{link.id} next arrival", next_arrival(link))
    c = st.columns(4)
    rate, basis = received_tph()
    c[0].metric("M16 hub inventory [t]", f"{_inv_t(scn, s, 'MPH', 'M16'):,.0f}")
    c[1].metric(f"M16 received flow [t/h] ({basis})", f"{rate:,.0f}")
    pd_k = k.get("per_destination", {}) if k else {}
    for i, did in enumerate([d.id for d in scn.destinations
                             if d.site == "MPH"][:2]):
        v = pd_k.get(did, {}).get("service_pct")
        c[2 + i].metric(f"{did} service [%]",
                        f"{v:.1f}" if v is not None else "—")
    st.caption("Evidence: corridor parameters CONFIRMED/DERIVED (TBE/PFD + "
               "datasheet); Benguerir yard figures are confirmed values with "
               "the document reference pending; Louta storage totals and "
               "key equipment are source-backed where stated, while equal "
               "splits, starting fills and grade shares remain explicit "
               "assumptions. The 102MG mine piles use the S3-A25 display-only "
               "100,000 t capacity / 50,000 t t0 baseline; their actual "
               "capacity and live SMS state are not loaded.")


# =========================================================== 8. Results & KPIs
def page_results():
    st.subheader("Plan results")
    scn, p, s = common.sc(), common.plan(), common.sim()
    if p is None:
        _empty_state(scn)
        return
    k = _kpis()
    plan_k = compute_kpis(scn, p, None)
    b = p.breakdown or {}
    eligible = int(b.get("planning_eligible_machines", 0) or 0)
    used = int(b.get("planning_used_machines", 0) or 0)
    coverage = b.get("planning_machine_coverage_pct")
    coverage_value = (
        "N/A" if eligible == 0
        else f"{float(coverage):.1f}%" if coverage is not None
        else "not recorded"
    )

    st.markdown("#### Plan-generation outcome")
    c = st.columns(4)
    plan_unmet_t = float(b.get(
        "unmet_t", sum(sum(series) for series in p.unmet.values())))
    c[0].metric(
        "Plan status", p.status,
        delta=("all demand served" if plan_unmet_t <= 1e-6
               else f"{plan_unmet_t:,.0f} t demand NOT served"),
        delta_color=("normal" if plan_unmet_t <= 1e-6 else "inverse"),
        help=(
            "CP-SAT solve status for the generated plan. A status of FEASIBLE "
            "or OPTIMAL means every HARD constraint was satisfied — it is "
            "not a statement that the requested demand was met. Unserved "
            "tonnes are shown beneath the status."))
    mixed_objective = bool(b.get(
        "planning_objective_is_tactical_reference", 0.0))
    c[1].metric(
        ("Tactical reference score" if mixed_objective
         else "Objective score"),
        f"{p.objective:,.0f}",
        help=(
            "Hourly tactical component score; the returned fine-grid prefix "
            "replaces part of that solution, so this is not the objective or "
            "optimality certificate of the merged job set."
            if mixed_objective else
            "Internal scaled CP-SAT score; lower is better. Compare it only "
            "between runs with the same horizon, inputs and weights."))
    c[2].metric("Planned demand satisfaction", f"{plan_k['service_pct']:.1f}%",
                help="Planned delivered tonnes capped at requested tonnes.")
    c[3].metric(
        "Planning machine coverage", coverage_value,
        help="Eligible solver machines receiving at least one positive-flow "
             "activity; reference, future and planning-disabled assets are "
             "excluded.")
    if eligible == 0:
        c[3].caption("0 of 0 eligible machines")
    elif coverage is not None:
        c[3].caption(f"{used} of {eligible} eligible machines")
    if b.get("planning_mixed_resolution"):
        horizon_min = int(b.get(
            "planning_horizon_min", scn.sim.effective_horizon_minutes()))
        near_horizon_min = int(b.get(
            "planning_near_horizon_min", scn.sim.near_term_horizon_min))
        near_step_min = int(b.get(
            "planning_near_step_min", scn.sim.near_term_step_min))
        far_step_min = int(b.get(
            "planning_far_step_min", scn.sim.far_term_step_min))
        fine_jobs = sum(job.id.startswith(f"N{near_step_min}-")
                        for job in p.jobs)
        st.caption(
            f"Solver: {p.solver} · {p.solver_wall_s:.1f} s · "
            f"{len(p.jobs)} jobs ({fine_jobs} near-term) · "
            f"{horizon_min / 60:g} h horizon · first "
            f"{near_horizon_min / 60:g} h at {near_step_min}-min "
            f"resolution, then {far_step_min}-min tactical resolution")
        if b.get("planning_mixed_fallback"):
            st.warning(
                "The detailed near-term solve did not return a feasible "
                "incumbent. This result is the safe hourly tactical fallback; "
                "it is not being presented as a 15-minute plan.")
    else:
        st.caption(
            f"Solver: {p.solver} · {p.solver_wall_s:.1f} s · "
            f"{len(p.jobs)} jobs · horizon "
            f"{scn.sim.horizon_buckets}×{scn.sim.dt_h:g} h")

    _render_energy_movement_kpis(scn, p, k)

    st.markdown("#### Objective drivers")
    st.caption(
        "These are generated-plan objective bases and the priorities used "
        "for this plan; they do not use simulation actuals. Penalties increase "
        "the score and rewards reduce it. The rows are not additive contributions "
        "to the displayed score because the solver also applies integer "
        "scaling, fixed emergency penalties and deterministic tie-breakers. "
        "Configure priorities under **Plan → Planner priorities**.")
    st.caption(
        "Editable priorities are w1, w2 and w4-w7 plus the config-backed "
        "operational terms. Operational defaults are ASSUMED and uncalibrated; "
        "their different units do not establish an exact ranking. The opt-in "
        "energy row covers productive work and route links, not positioning. "
        "Emergency penalties and tie-breakers are protected controls.")
    objective_df = pd.DataFrame(plan_generation_kpi_rows(scn, p, plan_k))
    objective_df["Plan result"] = objective_df["Plan result"].map(
        lambda value: f"{value:,.1f}" if isinstance(value, (int, float))
        else value)
    show_df(objective_df)

    st.markdown("#### Plan-generation checks")
    st.caption(
        "Coverage is diagnostic, not proof that every machine should run. "
        "Emergency slack and minute timing errors should normally be zero.")
    checks_df = pd.DataFrame(plan_generation_check_rows(p))
    checks_df["Result"] = checks_df["Result"].map(
        lambda value: f"{value:,.1f}" if isinstance(value, float) else value)
    show_df(checks_df)

    basis = "simulation actuals" if s else "plan values (no simulation run yet)"
    st.markdown("#### Delivery, quality and inventory")
    st.caption(f"Operational KPI basis: **{basis}**")
    c = st.columns(4)
    c[0].metric("Unmet demand [t]", f"{k['unmet_t']:,.0f}")
    continuity = continuous_feed_delivery_summary(k)
    c[1].metric("Continuous-feed interruption [h]",
                f"{continuity['gap_hours']:,.1f}")
    if continuity["legacy"]:
        c[1].caption(
            "legacy affected-bucket duration proxy · episode count not recorded")
    else:
        episode_count = continuity["episode_count"]
        episode_text = (
            "not recorded" if episode_count is None else
            f"{episode_count:,} episode{'s' if episode_count != 1 else ''}")
        maximum = continuity["max_episode_minutes"]
        maximum_text = (
            "" if maximum is None else f" · max {maximum:,.0f} min")
        c[1].caption(episode_text + maximum_text)
    c[2].metric("On-spec mass [%]", f"{k['on_spec_mass_pct']:.1f}" if k["on_spec_mass_pct"] is not None else "N/A")
    c[3].metric("Quality give-away [t·unit]",
                f"{k['giveaway_t_unit']:,.0f}")
    c = st.columns(4)
    c[0].metric("Terminal delivery [t]", f"{k['final_product_delivered_t']:,.0f}",
                help="External receiving interfaces only; excludes internal process-feed transfers.")
    c[1].metric("Safety-stock violations [buckets]",
                k["safety_stock_violation_buckets"])
    c[2].metric("Replans", k["replans"])
    c[3].metric("Plan stability [%]", f"{k['plan_stability_pct']:.0f}")
    st.caption(f"Material handled across all stages: {k['handled_t']:,.0f} t · "
               f"Known off-spec: {k['off_spec_mass_t']:,.0f} t · "
               f"Unknown quality: {k['quality_unknown_t']:,.0f} t · "
               f"Safety-stock exposure: {k['safety_shortfall_t_h']:,.0f} t·h")
    st.caption("Quality basis: " + k["quality_basis"])

    st.markdown("#### Per destination")
    show_df(pd.DataFrame(k["per_destination"]).T.round(1))

    left, right = st.columns(2)
    with left:
        util = k["machine_utilisation_pct"]
        plot(series_fig([theme.machine_display_label(scn, mid) for mid in util],
                        {"%": list(util.values())},
                        "Machine utilisation [%]", "%", "bar", 280))
        if k["travel_m"]:
            plot(series_fig([theme.machine_display_label(scn, mid)
                             for mid in k["travel_m"]],
                            {"m": list(k["travel_m"].values())},
                            "Machine travel [m] (geometry ASSUMED)", "m",
                            "bar", 260))
    with right:
        ru = {l: u for l, u in k["route_utilisation_pct"].items() if u > 0}
        if ru:
            plot(series_fig(list(ru.keys()), {"%": list(ru.values())},
                            "Route utilisation [%] (loaded links)", "%",
                            "bar", 280))
        if k["starts_stops"]:
            plot(series_fig([theme.machine_display_label(scn, mid)
                             for mid in k["starts_stops"]],
                            {"count": list(k["starts_stops"].values())},
                            "Starts / stops per machine", "count", "bar", 260))

    if k.get("quality_breakdown"):
        st.markdown("#### Quality-compliance breakdown")
        st.caption("On-spec = share of (destination × bucket) pairs with "
                   "delivered material inside every limit; zero-delivery "
                   "buckets are not evaluated. Soft limits are penalised "
                   "(w₂), never blocking; a hard-limit violation in actuals "
                   "means an execution deviation. Planning-basis values are "
                   "placeholder "
                   "spec.")
        qdf = pd.DataFrame(k["quality_breakdown"]).drop(columns=["sum_dev"])
        qdf["avg_dev"] = qdf["avg_dev"].round(3)
        qdf["max_dev"] = qdf["max_dev"].round(3)
        qdf["compliance_pct"] = qdf["compliance_pct"].round(1)
        c1, c2 = st.columns(2)
        dsel = c1.multiselect("Destination filter",
                              sorted(qdf["destination"].unique()))
        psel = c2.multiselect("Parameter filter",
                              sorted(qdf["parameter"].unique()))
        if dsel:
            qdf = qdf[qdf["destination"].isin(dsel)]
        if psel:
            qdf = qdf[qdf["parameter"].isin(psel)]
        qdf = qdf.rename(columns={
            "limit_min": "min", "limit_max": "max",
            "compliance_pct": "compliance %",
            "violations": "violation count",
            "avg_dev": "avg dev", "max_dev": "max dev",
            "source": "source / assumption id"})
        qdf["hard"] = qdf["hard"].map({True: "hard", False: "soft"})
        show_df(qdf[["destination", "parameter", "min", "max", "hard",
                     "evidence", "source / assumption id", "evaluated",
                     "compliant", "compliance %", "violation count",
                     "avg dev", "max dev"]])
    if k["quality_deviation_t_unit"] and any(
            v > 0 for v in k["quality_deviation_t_unit"].values()):
        st.caption("Aggregate deviation mass [t·unit outside spec]: "
                   + " · ".join(f"{p_}: {v:,.1f}" for p_, v in
                                k["quality_deviation_t_unit"].items()))

    if k["planned_vs_actual"]:
        st.markdown("#### Planned vs actual (per job)")
        show_df(pd.DataFrame(k["planned_vs_actual"]))

    if p.reasons:
        st.markdown("#### Reason codes")
        for r in p.reasons:
            st.markdown(f"- `{theme.operator_text(r)}`")
    if p.relaxations:
        with st.expander("Suggested relaxations"):
            for r in p.relaxations:
                st.markdown(f"- {theme.operator_text(r)}")

    st.divider()
    if st.button("📌 Snapshot KPIs for comparison"):
        label = f"{st.session_state.scenario_name} @{pd.Timestamp.now():%H:%M:%S}"
        st.session_state.compare[label] = {
            kk: vv for kk, vv in k.items()
            if isinstance(vv, (int, float, str))}
        st.success(f"stored: {label}")


# =========================================================== 13. Comparison
PRESET_HELP = """Suggested comparison studies (run each, snapshot on the
Results page, then compare here):
- **Normal operation** — baseline scenario, no events
- **One MPH reclaimer unavailable** — Simulation Control → machine_failure RC01-A/B
- **Benguerir corridor delayed** — event `delayed_mine_dispatch` on C2 (S3)
- **Train loading competes with corridor** — S3, set BG-TRAIN-PROD demand > 0
- **Low high-grade inventory** — Input Data → reduce TBT pile masses
- **PAP demand increase** — Plan → S0 → raise PAP-1 demand
- **Sulphur reclaimer interruption** — S1 + machine_failure M17-RC01
- **Alternative objective weights** — Advanced → Planning settings → adjust w₁…w₇
"""


def page_compare():
    st.subheader("Scenario Comparison")
    st.caption("Side-by-side comparison of stored KPI snapshots "
               "(each row = one run snapshot).")
    comp = st.session_state.compare
    if not comp:
        st.info("No snapshots yet. Run a plan/simulation, then press "
                "*Snapshot KPIs for comparison* on the Results page.")
        st.markdown(PRESET_HELP)
        return
    df = pd.DataFrame(comp).T
    num_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    show_df(df.round(1), height=min(60 + 38 * len(df), 480))
    metric = st.selectbox("Chart metric", num_cols,
                          index=num_cols.index("service_pct")
                          if "service_pct" in num_cols else 0)
    plot(series_fig(list(df.index), {metric: df[metric].tolist()},
                    f"{metric} by snapshot", metric, "bar", 320))
    c1, c2 = st.columns(2)
    if c1.button("Clear snapshots"):
        st.session_state.compare = {}
        st.rerun()
    with c2.expander("Suggested studies"):
        st.markdown(PRESET_HELP)
