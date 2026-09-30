"""Visual theme + shared chart helpers.

Industrial planning-console styling on the report palette:
navy = navigation/normal planning · orange = hub & primary actions ·
green = feasible/compliant · amber = assumed/soft/warnings ·
red = blocking errors only · grey = supporting.

Type scale (CSS below): page title 1.25rem / section 1.0rem / card label
0.72rem / KPI value 1.5rem / body 0.9rem / caption 0.78rem.
Spacing system: 4/8/12/16/24 px.
"""
from __future__ import annotations

import datetime as dtm
import html
import json
import re
from typing import Any, Dict, List, Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from mph_sim.coordinates import (format_repere, job_repere_label,
                                 job_repere_range, x_to_repere)
from mph_sim.movement import estimate_transition, work_midpoint_x
from mph_sim.timing import (VALIDATED, display_execution_window,
                            effective_execution_rate_cap,
                            minute_dt, validated_execution_windows,
                            window_end_dt, window_start_dt)

# report palette
NAVY = "#1f4e79"
BLUE = "#2e6da4"
LBLUE = "#5b7fa6"
GREEN = "#2e7d32"
DGREEN = "#2e6d4f"
ORANGE = "#d98b2b"
DORANGE = "#b5651d"
GREY = "#7d8a97"
TEXT_GREY = "#4d5b66"        # WCAG-friendly caption grey (≥7:1 on white)
VIOLET = "#6a3fa0"
BLEND_PURPLE = "#7b4bb7"
RED = "#b23b3b"
AMBER = "#b97a14"            # darker amber for text on light backgrounds
BG = "#f3f6f9"

EVIDENCE_COLORS = {"CONFIRMED": GREEN, "DERIVED": BLUE, "INFERRED": ORANGE,
                   "ASSUMED": AMBER, "TBC": GREY, "OPEN": AMBER}
SEVERITY_COLORS = {"ERROR": RED, "WARNING": AMBER, "INFO": BLUE}

MACHINE_COLORS = [NAVY, GREEN, ORANGE, VIOLET, BLUE, DORANGE, DGREEN, GREY,
                  "#8a5fbf", "#4a7a9f"]

# Short, operator-facing site prefixes.  Equipment IDs remain unchanged in
# the engine and exports; these labels remove ambiguity when several SMS yards
# share a timeline or selector.
# site-qualified labels live in the shared, dependency-free ui.labels module
# (single source of truth — see labels.py). Re-exported here so existing
# `from ui.theme import machine_display_label` call sites keep working.
from .labels import (SITE_PREFIXES, machine_display_label,  # noqa: F401
                     site_prefix, site_tag)

CSS = f"""
<style>
  /* ---------- layout & rhythm (4/8/12/16/24) ---------- */
  .block-container {{ padding-top: 2.75rem; padding-bottom: 1.5rem;
                      max-width: 1500px; }}
  div[data-testid="stVerticalBlock"] {{ gap: 0.65rem; }}
  section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"]
      {{ gap: 0.5rem; }}

  /* ---------- type scale ---------- */
  h1, h2, h3 {{ color: {NAVY}; }}
  h3 {{ font-size: 1.25rem; font-weight: 700; margin: 0 0 .25rem 0;
        padding-bottom: 2px; }}
  h4 {{ color: {NAVY}; font-size: 1.0rem; font-weight: 650;
        margin: .5rem 0 .25rem 0; padding-bottom: 2px;
        border-bottom: 1px solid #dfe6ec; }}
  h5 {{ color: {BLUE}; font-size: .9rem; font-weight: 600;
        margin: .4rem 0 .2rem 0; }}
  p, li {{ font-size: .9rem; }}
  div[data-testid="stCaptionContainer"], .stCaption, small {{
        color: {TEXT_GREY} !important; font-size: .78rem !important; }}

  /* ---------- KPI cards ---------- */
  div[data-testid="stMetric"] {{
     background: {BG}; border: 1px solid #cdd6df; border-radius: 4px;
     padding: 6px 10px; }}
  div[data-testid="stMetric"] label {{ color: #33546e;
     font-size: .72rem; text-transform: uppercase; letter-spacing: .03em; }}
  div[data-testid="stMetricValue"] {{ font-size: 1.5rem; color: #17364f; }}

  /* secondary mini-KPI strip */
  .mini-kpis {{ display: flex; flex-wrap: wrap; gap: 8px 20px;
     background: #fafbfc; border: 1px solid #e3e9ef; border-radius: 4px;
     padding: 6px 12px; font-size: .8rem; color: {TEXT_GREY}; }}
  .mini-kpis b {{ color: #17364f; font-size: .88rem; }}

  /* ---------- process-mimic equipment state strip ---------- */
  .asset-state-legend {{
     display: flex; align-items: center; flex-wrap: wrap; gap: 6px 10px;
     border: 1px solid #d7e0e7; border-radius: 5px; background: #f8fafb;
     padding: 6px 10px; color: {TEXT_GREY}; font-size: .74rem; }}
  .asset-state-title {{
     color: #17364f; font-weight: 700; margin-right: 2px; }}
  .asset-state-chip {{
     display: inline-flex; align-items: center; gap: 4px;
     border: 1px solid #d7e0e7; border-radius: 999px; background: #fff;
     padding: 2px 7px; color: #334754; }}
  .asset-state-chip i {{ font-style: normal; font-size: .9rem;
     line-height: 1; }}
  .asset-state-chip.is-future {{ border-style: dashed; }}
  .asset-state-note {{ color: {TEXT_GREY}; margin-left: auto; }}

  /* ---------- sidebar ---------- */
  .app-brand {{ color: {NAVY}; font-size: 1.02rem; line-height: 1.25;
     border-bottom: 2px solid {NAVY}; padding-bottom: 6px; margin-bottom: 4px; }}
  .app-brand span {{ font-size: .72rem; color: {TEXT_GREY}; }}
  .loaded-line {{ font-size: .8rem; color: #17364f; background: {BG};
     border: 1px solid #cdd6df; border-left: 3px solid {ORANGE};
     border-radius: 3px; padding: 4px 8px; }}
  .st-key-sidebar_planning_scenario_code [data-baseweb="select"] > div {{
     border-left: 3px solid {ORANGE} !important;
     background: {BG}; font-size: .78rem; }}
  div[role="radiogroup"] label {{ padding: 1px 0; }}
  div[role="radiogroup"] label p {{ font-size: .85rem; }}

  /* ---------- compact planning scenario context ---------- */
  .planning-envelope-strip {{ display: flex; align-items: center;
     flex-wrap: wrap; gap: 5px 18px; min-height: 42px;
     margin: -2px 0 0; padding: 6px 10px;
     color: #334754; background: {BG}; border: 1px solid #d6e0e8;
     border-left: 3px solid {ORANGE}; border-radius: 5px;
     font-size: .76rem; line-height: 1.2; }}
  .planning-envelope-strip > b {{ color: {NAVY}; white-space: nowrap;
     font-size: .78rem; }}
  .planning-envelope-strip > span {{ display: inline-flex;
     align-items: baseline; gap: 4px; white-space: nowrap; }}
  .planning-envelope-strip small {{ color: {GREY}; font-size: .62rem;
     font-weight: 700; letter-spacing: .035em; text-transform: uppercase; }}
  @media (max-width: 900px) {{
    .planning-envelope-strip {{ gap: 6px 12px; }}
    .planning-envelope-strip > b {{ flex-basis: 100%; }}
  }}

  /* ---------- top domain navbar ---------- */
  .st-key-domain_navbar {{
     width: 100%; max-width: 100%; border: 0; background: transparent;
     padding: 0 !important; margin: 0 0 8px 0 !important;
     overflow: visible !important; }}
  .st-key-domain_navbar div[data-testid="stWidgetLabel"] {{
     display: none !important; }}
  .st-key-domain_navbar div[data-testid="stButtonGroup"],
  .st-key-domain_navbar [role="radiogroup"] {{
     width: fit-content !important; max-width: 100% !important;
     gap: 3px !important; overflow: visible !important;
     border: 1px solid #cbd6df; border-radius: 8px;
     background: #f5f8fa; padding: 3px !important; }}
  .st-key-domain_navbar [role="radio"] {{
     display: inline-flex !important; align-items: center !important;
     justify-content: center !important; flex: 0 0 auto !important;
     width: auto !important; min-width: 8rem !important;
     height: 36px !important; min-height: 36px !important;
     padding: 0 16px !important; border-radius: 6px !important;
     font-weight: 650 !important; overflow: visible !important; }}
  .st-key-domain_navbar [role="radio"] p {{
     margin: 0 !important; padding: 0 !important;
     font-size: .84rem !important; line-height: 1.15 !important;
     white-space: nowrap !important; }}
  @media (max-width: 700px) {{
    .block-container {{ padding-top: 3.25rem; }}
    .st-key-domain_navbar div[data-testid="stButtonGroup"],
    .st-key-domain_navbar [role="radiogroup"] {{
       width: 100% !important; }}
    .st-key-domain_navbar [role="radio"] {{
       min-width: 0 !important; flex: 1 1 0 !important;
       padding: 0 10px !important; }}
  }}

  /* ---------- status / context bar ---------- */
  .statusbar {{ display: flex; align-items: center; flex-wrap: wrap;
     gap: 5px 10px; background: {NAVY}; color: #fff; border-radius: 5px;
     padding: 7px 12px; font-size: .8rem; margin-bottom: 6px; }}
  .statusbar b {{ color: #ffd9a0; }}
  .sb-dim {{ color: #b9cbdc; }}
  .status-item {{ display: inline-flex; align-items: center;
     white-space: nowrap; }}
  .warnchip {{ background: {ORANGE}; color: #2d1e05; border-radius: 3px;
     padding: 1px 7px; margin-left: 6px; font-size: .74rem; font-weight: 600; }}
  .okchip {{ background: {GREEN}; color: #fff; border-radius: 3px;
     padding: 1px 7px; margin-left: 6px; font-size: .74rem; font-weight: 600; }}
  .errchip {{ background: {RED}; color: #fff; border-radius: 3px;
     padding: 1px 7px; margin-left: 6px; font-size: .74rem; font-weight: 600; }}

  /* ---------- badges ---------- */
  .ev-badge {{ display: inline-block; border-radius: 3px; padding: 0 6px;
     color: #fff; font-size: .7rem; font-weight: 600; margin-right: 2px; }}

  /* ---------- empty state ---------- */
  .empty-state {{ border: 1px solid #cdd6df; border-left: 4px solid {ORANGE};
     border-radius: 4px; background: #fcfdfe; padding: 16px 20px; }}
  .empty-state h4 {{ border: none; margin-top: 0; }}

  /* Compact no-plan workflow, colocated with the primary sidebar action. */
  .st-key-sidebar_planning_readiness {{
     border-left: 4px solid {ORANGE} !important;
     border-radius: 7px !important; background: #fff;
     padding: 2px 5px 5px !important; margin-top: 6px; }}
  .st-key-sidebar_planning_readiness button {{
     min-height: 34px; font-size: .78rem; font-weight: 600; }}
  .sidebar-ready-head {{ display: flex; align-items: center;
     justify-content: space-between; gap: 6px; margin-bottom: 3px; }}
  .sidebar-ready-eyebrow {{ color: {BLUE}; font-size: .66rem;
     font-weight: 750; letter-spacing: .065em; text-transform: uppercase; }}
  .sidebar-ready-badge {{ display: inline-flex; align-items: center;
     white-space: nowrap; border-radius: 999px; padding: 2px 6px;
     font-size: .62rem; font-weight: 700; }}
  .sidebar-ready-badge.ready {{ color: #205c2a; background: #edf7ef;
     border: 1px solid #a9d5af; }}
  .sidebar-ready-badge.warning {{ color: #6f4707; background: #fff7e8;
     border: 1px solid #e6c37e; }}
  .sidebar-ready-badge.blocked {{ color: #842c2c; background: #fff0f0;
     border: 1px solid #e0aaaa; }}
  .sidebar-ready-title {{ color: #17364f; font-size: .9rem;
     line-height: 1.25; font-weight: 700; margin-bottom: 7px; }}
  .sidebar-ready-progress {{ display: flex; align-items: center;
     padding: 0 7px; }}
  .sidebar-ready-progress span {{ display: grid; place-items: center;
     flex: 0 0 21px; width: 21px; height: 21px; border-radius: 50%;
     color: #fff; background: {NAVY}; font-size: .65rem; font-weight: 750; }}
  .sidebar-ready-progress i {{ flex: 1 1 auto; height: 2px;
     background: #cbd6df; }}
  .sidebar-ready-progress span.done {{ background: {GREEN}; }}
  .sidebar-ready-progress span.active {{ background: {AMBER}; }}
  .sidebar-ready-progress span.blocked {{ background: {RED}; }}
  .sidebar-ready-step-labels {{ display: grid; grid-template-columns: repeat(3, 1fr);
     color: {TEXT_GREY}; font-size: .61rem; text-align: center;
     margin: 3px 0 7px; }}
  .sidebar-ready-facts {{ display: grid; gap: 2px; border-top: 1px solid #e3e9ef;
     padding-top: 6px; color: {TEXT_GREY}; font-size: .69rem;
     line-height: 1.3; }}
  .sidebar-ready-facts b {{ color: #17364f; }}
  .sidebar-ready-issue {{ display: grid; gap: 1px; border-radius: 4px;
     padding: 5px 7px; margin: 6px 0; font-size: .68rem; line-height: 1.25; }}
  .sidebar-ready-issue.warning {{ color: #5e420e; background: #fff8e9;
     border: 1px solid #ead09b; }}
  .sidebar-ready-issue.error {{ color: #762929; background: #fff1f1;
     border: 1px solid #e1b1b1; }}
  .sidebar-ready-issue.ok {{ color: #245a2c; background: #f0f8f1;
     border: 1px solid #b7d9bc; }}

  /* ---------- actions: orange = primary planning action ---------- */
  button[kind="primary"], button[data-testid="stBaseButton-primary"] {{
     background-color: {DORANGE} !important;
     border: 1px solid #8f4d10 !important; color: #fff !important; }}
  button[kind="primary"]:hover {{ background-color: #a45a12 !important; }}

  /* ---------- accessibility ---------- */
  button:focus-visible, [role="radio"]:focus-visible,
  [role="combobox"]:focus-visible, a:focus-visible {{
     outline: 2px solid {DORANGE} !important; outline-offset: 2px; }}
  div[data-testid="stAlert"] p {{ font-size: .85rem; color: #3a3323; }}
</style>
"""


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)


_OPERATOR_TEXT_RULES = (
    (re.compile(r"\b(?:ASSUMED/TBC|TBC/ASSUMED)\b", re.IGNORECASE),
     "planning basis"),
    (re.compile(r"\bDERIVED/TBC\b", re.IGNORECASE), "derived"),
    (re.compile(r"\bSCOPE_TBC\b", re.IGNORECASE), "scope pending"),
    (re.compile(r"\bDOCUMENT_RECONCILIATION_TBC\b", re.IGNORECASE),
     "document reconciliation pending"),
    (re.compile(r"(?<![A-Za-z0-9])TBC(?![A-Za-z0-9])", re.IGNORECASE),
     "pending"),
)


def operator_text(value: Any) -> str:
    """Return operator-facing text without internal evidence shorthand.

    Engineering data, exports and evidence objects keep their original
    ``TBC`` status.  This presentation boundary only replaces the acronym
    with plain-language wording so the operating interface stays concise.
    """
    text = str(value)
    for pattern, replacement in _OPERATOR_TEXT_RULES:
        text = pattern.sub(replacement, text)
    return text


def _operator_value(value: Any) -> Any:
    if isinstance(value, str):
        return operator_text(value)
    if isinstance(value, dict):
        return {
            operator_text(key) if isinstance(key, str) else key:
            _operator_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_operator_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_operator_value(item) for item in value)
    return value


def operator_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Copy a dataframe and sanitise its displayed values recursively."""
    if df is None:
        return df
    out = df.copy(deep=True)
    out.columns = [operator_text(col) for col in out.columns]
    out.index = [operator_text(idx) if isinstance(idx, str) else idx
                 for idx in out.index]
    for col in out.columns:
        out[col] = out[col].map(_operator_value)
    return out


def restore_operator_frame(edited: pd.DataFrame,
                           original: pd.DataFrame) -> pd.DataFrame:
    """Restore hidden evidence tokens in cells the operator did not edit.

    Editable configuration tables show plain-language presentation text.  If
    a displayed cell is left unchanged, this maps it back to the exact source
    value before import so opening and applying a table cannot rewrite the
    engineering evidence register.
    """
    if edited is None or original is None:
        return edited
    restored = edited.copy()
    common_rows = min(len(restored), len(original))
    for row_pos in range(common_rows):
        for col in original.columns:
            display_col = operator_text(col)
            if display_col not in restored.columns:
                continue
            source_value = original.iloc[row_pos][col]
            edited_value = restored.iloc[row_pos][display_col]
            if (isinstance(source_value, str)
                    and isinstance(edited_value, str)
                    and edited_value == operator_text(source_value)):
                restored.iat[
                    row_pos, restored.columns.get_loc(display_col)
                ] = source_value
    reverse_columns = {
        operator_text(col): col for col in original.columns
        if operator_text(col) != col
    }
    if reverse_columns:
        restored = restored.rename(columns=reverse_columns)
    return restored


def operator_figure(fig: go.Figure) -> go.Figure:
    """Copy a Plotly figure with every visible/hover string sanitised."""
    # JSON normalization also converts numpy-backed trace text/customdata to
    # regular lists, ensuring hover payloads cannot bypass the sanitizer.
    return go.Figure(_operator_value(json.loads(fig.to_json())))


def ev_badge(status: str, count: Optional[int] = None) -> str:
    c = EVIDENCE_COLORS.get(status, GREY)
    public_status = "REVIEW" if status == "TBC" else operator_text(status)
    label = (f"{count} {public_status}"
             if count is not None else public_status)
    return f'<span class="ev-badge" style="background:{c}">{label}</span>'


# ---------------------------------------------------------------- formatting
def fmt_t(v) -> str:
    """Compact tonnes — never lets Streamlit truncate with an ellipsis."""
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e6:
        return f"{v / 1e6:,.2f} Mt"
    if a >= 10_000:
        return f"{v / 1e3:,.1f} kt"
    return f"{v:,.0f} t"


def fmt_t_bar(v) -> str:
    """Very compact tonnes label intended to remain readable inside a bar."""
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e6:
        return f"{v / 1e6:.1f} Mt"
    if a >= 1_000:
        return f"{v / 1e3:.1f} kt"
    return f"{v:,.0f} t"


def fmt_pct(v) -> str:
    return "—" if v is None else f"{v:.1f}"


def mini_kpis(items: List[tuple]):
    """Secondary KPI strip: [(label, value_str), ...]."""
    cells = "".join(
        f"<span>{operator_text(lbl)}&nbsp;"
        f"<b>{operator_text(val)}</b></span>"
                    for lbl, val in items)
    st.markdown(f'<div class="mini-kpis">{cells}</div>',
                unsafe_allow_html=True)


def plotly_layout(fig: go.Figure, title: str = "", height: int = 320,
                  **kw) -> go.Figure:
    if title:
        kw["title"] = dict(text=title, font=dict(size=14, color=NAVY))
    fig.update_layout(
        height=height, margin=dict(l=40, r=20, t=42 if title else 16, b=36),
        plot_bgcolor="#fcfdfe", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Segoe UI, Calibri, sans-serif", size=12),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0,
                    font=dict(size=10)),
        **kw)
    fig.update_xaxes(gridcolor="#e3e9ef", zerolinecolor="#cdd6df")
    fig.update_yaxes(gridcolor="#e3e9ef", zerolinecolor="#cdd6df")
    return fig


def bucket_time(sc, b: int) -> dtm.datetime:
    return sc.sim.bucket_dt(b)


_GANTT_COLUMNS = [
    "job", "parent_job", "machine", "machine_label", "kind", "space",
    "source", "target", "site", "area", "destination", "grade",
    "output_grade", "treatment", "rate_tph", "solver_rate_tph",
    "execution_rate_cap_tph", "tonnage_t", "output_tonnage_t",
    "parent_tonnage_t", "tonnage_label", "mass_basis", "material_class",
    "tsm_equivalent_t", "tsm_label", "tsm_tonnage_label",
    "start", "end", "start_time",
    "end_time", "solver_start", "solver_end", "solver_window",
    "parent_solver_window", "execution_duration_min", "timing_status",
    "timing_basis", "execution_window_count", "process_stage", "depends_on",
    "dependency_delay_min", "purpose", "status", "origin", "locked", "path",
    "stacking_method", "blend_mode", "blend_recipe",
    "chevron_passes_per_cycle", "chevron_grade_passes",
    "chevron_pass_status", "chevron_equivalent_passes",
    "start_repere", "stop_repere", "repere_range", "start_x_m", "stop_x_m",
    "start_bucket", "end_bucket", "parent_start_bucket", "parent_end_bucket",
    "activity_for_job", "from_position", "to_position",
    "translation_distance_m", "translation_duration_min",
    "setup_duration_min", "positioning_duration_min", "available_gap_min",
    "positioning_basis", "gap_status",
    "reserved_positioning_start_minute",
    "reserved_positioning_duration_min",
    # Schedule identity: physical run vs allocation slice vs planning-day
    # boundary. See mph_sim.runs and Job.physical_run_id.
    "physical_run", "physical_run_job_count", "allocation_slice_count",
    "continues_previous_planning_day", "physical_run_hold_min",
    "rate_credibility",
]


def _work_midpoint_x(sc, row: pd.Series) -> float:
    return work_midpoint_x(
        sc, str(row["space"]),
        float(row.get("start_x_m", 0.0) or 0.0),
        float(row.get("stop_x_m", 0.0) or 0.0))


def _position_label(sc, space_id: str, x_m: float) -> tuple[str, int]:
    try:
        repere = int(x_to_repere(sc.space(space_id), x_m))
        return f"{space_id} · {format_repere(repere)}", repere
    except (StopIteration, ValueError, TypeError):
        return f"{space_id} · x={x_m:,.1f} m", 0


def _positioning_rows(sc, production: pd.DataFrame) -> List[dict]:
    """Derived non-material preparation rows between different work spaces."""
    if production.empty:
        return []
    eligible = production[
        ~production["status"].isin(["CANCELLED", "REJECTED"])
    ]
    rows: List[dict] = []
    plan_start = dtm.datetime.fromisoformat(sc.sim.start_iso)
    width_min = max(int(round(float(sc.sim.dt_h) * 60.0)), 1)

    for machine_id, machine_rows in eligible.groupby(
            "machine", sort=True):
        ordered = machine_rows.sort_values(
            ["start", "end", "parent_job", "job"]).to_dict("records")
        previous = None
        for current in ordered:
            if previous is None:
                previous = current
                continue
            if previous["parent_job"] == current["parent_job"] or \
                    previous["space"] == current["space"]:
                previous = current
                continue

            machine = sc.machine(machine_id)
            from_x = _work_midpoint_x(sc, pd.Series(previous))
            to_x = _work_midpoint_x(sc, pd.Series(current))
            estimate = estimate_transition(
                machine, from_x, to_x, space_changed=True)
            if estimate.total_min <= 0:
                previous = current
                continue

            required = int(estimate.total_min)
            just_in_time_start = (
                current["start"] - dtm.timedelta(minutes=required))
            available_gap = max(int(round(
                (current["start"] - previous["end"]).total_seconds() / 60.0
            )), 0)
            validated = (
                previous["timing_status"] == VALIDATED
                and current["timing_status"] == VALIDATED)
            reserved = (
                validated and just_in_time_start >= previous["end"])
            recorded_start = current.get(
                "reserved_positioning_start_minute")
            recorded_duration = current.get(
                "reserved_positioning_duration_min")
            has_recorded_slot = (
                validated and pd.notna(recorded_start)
                and int(recorded_duration or 0) == required)
            if has_recorded_slot:
                start = plan_start + dtm.timedelta(
                    minutes=int(recorded_start))
                end = start + dtm.timedelta(minutes=required)
                reserved = (
                    start >= previous["end"] and end <= current["start"])
            if reserved:
                if not has_recorded_slot:
                    start = just_in_time_start
                    end = current["start"]
                activity_kind = "positioning"
                status = "RESERVED_ESTIMATE"
            else:
                # A red conflict bar begins where the prior work ends. It
                # never masquerades as a successfully reserved green move.
                start = previous["end"]
                end = start + dtm.timedelta(minutes=required)
                activity_kind = "positioning conflict"
                status = "POSITIONING_CONFLICT"
            from_position, from_repere = _position_label(
                sc, previous["space"], from_x)
            to_position, to_repere = _position_label(
                sc, current["space"], to_x)
            start_minute = int(round(
                (start - plan_start).total_seconds() / 60.0))
            end_minute = int(round(
                (end - plan_start).total_seconds() / 60.0))
            start_bucket = start_minute // width_min
            end_bucket = max((end_minute - 1) // width_min, start_bucket)
            next_job = str(current["parent_job"] or current["job"])
            rows.append({
                "job": (
                    f"POS-{next_job}" if reserved
                    else f"POS-CONFLICT-{next_job}"),
                "parent_job": next_job,
                "machine": machine_id,
                "machine_label": current["machine_label"],
                "kind": activity_kind,
                "space": current["space"],
                "source": previous["space"],
                "target": current["space"],
                "site": current["site"],
                "area": current["area"],
                "destination": "—",
                "grade": "—",
                "output_grade": "—",
                "rate_tph": None,
                "solver_rate_tph": None,
                "execution_rate_cap_tph": None,
                "tonnage_t": None,
                "output_tonnage_t": None,
                "parent_tonnage_t": None,
                "tonnage_label": (
                    f"↔ {required} min" if reserved
                    else f"! {required} min"),
                "mass_basis": "non-material",
                "material_class": "",
                "tsm_equivalent_t": None,
                "tsm_label": "",
                "tsm_tonnage_label": "",
                "start": start,
                "end": end,
                "start_time": start.strftime("%Y-%m-%d %H:%M"),
                "end_time": end.strftime("%Y-%m-%d %H:%M"),
                "solver_start": None,
                "solver_end": None,
                "solver_window": (
                    "non-material preparation"
                    if reserved else "preparation gap not reserved"),
                "parent_solver_window": current["parent_solver_window"],
                "execution_duration_min": required,
                "timing_status": status,
                "timing_basis": estimate.basis,
                "execution_window_count": 1,
                "process_stage": activity_kind,
                "depends_on": "—",
                "dependency_delay_min": 0.0,
                "purpose": (
                    f"Position for {next_job}" if reserved else
                    f"Resolve positioning gap before {next_job}"),
                "status": "PLANNED" if reserved else "ATTENTION",
                "origin": "DERIVED",
                "locked": False,
                "path": "—",
                "start_repere": from_repere,
                "stop_repere": to_repere,
                "repere_range": f"{from_position} → {to_position}",
                "start_x_m": from_x,
                "stop_x_m": to_x,
                "start_bucket": start_bucket,
                "end_bucket": end_bucket,
                "parent_start_bucket": current["parent_start_bucket"],
                "parent_end_bucket": current["parent_end_bucket"],
                "activity_for_job": next_job,
                "from_position": from_position,
                "to_position": to_position,
                "translation_distance_m": estimate.distance_m,
                "translation_duration_min": estimate.translation_min,
                "setup_duration_min": estimate.setup_min,
                "positioning_duration_min": required,
                "available_gap_min": available_gap,
                "positioning_basis": estimate.basis,
                "gap_status": status,
                "reserved_positioning_start_minute": (
                    start_minute if reserved else None),
                "reserved_positioning_duration_min": (
                    required if reserved else 0),
                # Positioning is non-material activity: it belongs to
                # no physical material run of its own.
                "physical_run": "—",
                "physical_run_job_count": 1,
                "allocation_slice_count": 0,
                "continues_previous_planning_day": False,
                "physical_run_hold_min": 0,
                "rate_credibility": "NON_MATERIAL",
            })
            previous = current
    return rows


def _coalesce_execution_display_rows(
        rows: List[dict], parent_job_id: str) -> List[dict]:
    """Combine touching bucket slices into honest continuous job runs.

    A parent solver job already has one machine/source/target/grade identity.
    The minute overlay nevertheless emits one execution window per native
    planning bucket.  For operator schedules those windows are one continuous
    run when they touch exactly, retain the same route, and need no new
    positioning reservation.  Rate changes alone do not split a job; the
    displayed rate is total physical mass divided by continuous run time.

    The *refinement basis* deliberately does not split a run either.  Whether
    one hour of a campaign was timed by maximum-rate compression, by the
    native bucket rate or by stable-run expansion is metadata about how the
    minute overlay was derived — it is not a physical event, and using it as a
    split criterion cut continuous runs at hours where only the derivation
    changed.  A combined run reports every contributing basis.
    """
    if not rows:
        return []

    runs: List[dict] = []
    for source in sorted(rows, key=lambda row: (row["start"], row["end"])):
        current = dict(source)
        current["execution_window_count"] = 1
        current["timing_bases"] = [current.get("timing_basis")]
        previous = runs[-1] if runs else None
        touching = bool(
            previous is not None
            and previous["end"] == current["start"]
            and int(current["start_bucket"]) ==
                int(previous["end_bucket"]) + 1
            and previous.get("path") == current.get("path")
            and previous.get("timing_status") == current.get("timing_status")
            # Two different physical runs are two different bars, whatever
            # their bucket indices suggest.
            and previous.get("physical_run") == current.get("physical_run")
            and current.get("reserved_positioning_start_minute") is None
            and int(current.get(
                "reserved_positioning_duration_min", 0) or 0) == 0
        )
        if not touching:
            runs.append(current)
            continue

        duration_min = (
            int(previous.get("execution_duration_min", 0) or 0)
            + int(current.get("execution_duration_min", 0) or 0)
        )
        tonnage_t = (
            float(previous.get("tonnage_t", 0.0) or 0.0)
            + float(current.get("tonnage_t", 0.0) or 0.0)
        )
        previous["end"] = current["end"]
        previous["end_bucket"] = current["end_bucket"]
        previous["solver_window"] = (
            f"B{previous['start_bucket']}–B{current['end_bucket']}"
        )
        previous["execution_duration_min"] = duration_min
        previous["tonnage_t"] = tonnage_t
        previous["output_tonnage_t"] = (
            float(previous.get("output_tonnage_t", 0.0) or 0.0)
            + float(current.get("output_tonnage_t", 0.0) or 0.0)
        )
        previous["rate_tph"] = (
            tonnage_t * 60.0 / duration_min if duration_min else 0.0
        )
        previous["execution_window_count"] += 1
        for basis in current["timing_bases"]:
            if basis not in previous["timing_bases"]:
                previous["timing_bases"].append(basis)

    for index, run in enumerate(runs, start=1):
        run["job"] = (
            parent_job_id if len(runs) == 1
            else f"{parent_job_id}·R{index}"
        )
        bases = [basis for basis in run.pop("timing_bases", []) if basis]
        run["timing_basis"] = (
            bases[0] if len(bases) <= 1 else "MIXED: " + " + ".join(bases))
    return runs


def _material_tsm_info(job, display, material_class):
    """Use actual stream assays, never the first component's grade alias."""
    flows = [flow for flow in job.material_flows
             if display["start_bucket"] <= flow["bucket"] <= display["end_bucket"]]
    total = sum(float(flow["tonnage_t"]) for flow in flows)
    if not total:
        return None, ""
    recovered = bool(job.treatment_id)
    quantity = "output_tonnage_t" if recovered else "tonnage_t"
    quality = "output_quality" if recovered else "quality"
    if any("moisture" not in flow[quality] for flow in flows):
        return None, ""
    dry = sum(float(flow[quantity]) * (1 - float(flow[quality]["moisture"]) / 100)
              for flow in flows) * float(display["tonnage_t"]) / total
    label = ("expected recovered TSM" if recovered else
             "dry feed equivalent" if material_class in ("raw", "raw_screened") else
             "TSM" if material_class == "dry" else "TSM equivalent")
    return dry, label


def jobs_to_gantt_df(
        sc, jobs: List, *, include_positioning: bool = False,
        coalesce_execution_windows: bool = False) -> pd.DataFrame:
    jobs = list(jobs)
    rows = []
    for j in jobs:
        source = getattr(j, "source_space", "") or (
            j.space if j.kind in ("reclaim", "dispatch") else "")
        target = getattr(j, "target_space", "") or (
            j.space if j.kind == "stack" else (j.dest_id or ""))
        process_stage = getattr(j, "process_stage", "") or ""
        depends_on = getattr(j, "depends_on_stage", "") or ""
        dependency_delay_min = float(
            getattr(j, "dependency_delay_min", 0.0) or 0.0)
        purpose = getattr(j, "purpose", "") or (
            f"Supply {j.dest_id} corridor"
            if j.kind == "dispatch" and j.dest_id else "")
        explicit_output_grade = getattr(j, "output_grade", "") or ""
        output_grade = explicit_output_grade or j.grade
        output_tonnage_t = float(
            getattr(j, "output_tonnage_t", 0.0) or 0.0)
        try:
            machine_obj = sc.machine(j.machine)
            site = machine_obj.site
            # ``dispatch`` is a material-route verb in the backend.  On a
            # machine schedule the physical operation is reclaim, regardless
            # of whether the recovered material feeds a plant or a corridor.
            operation_kind = "reclaim" if j.kind == "dispatch" else j.kind
        except (StopIteration, AttributeError):
            site = "?"
            operation_kind = j.kind
        process_stage = process_stage or operation_kind
        space = None
        try:
            space = sc.space(j.space)
            area = space.area
            r0, r1 = job_repere_range(space, j)
            repere_range = job_repere_label(space, j)
        except (StopIteration, ValueError, TypeError):
            area = ""
            r0, r1, repere_range = None, None, "—"
            for sid in (source, target):
                if not sid:
                    continue
                try:
                    area = sc.space(sid).area
                    break
                except (StopIteration, AttributeError):
                    continue
        try:
            execution_rate_cap = effective_execution_rate_cap(sc, j)
        except (StopIteration, ValueError, TypeError, AttributeError):
            execution_rate_cap = float(
                getattr(j, "execution_rate_cap_tph", 0.0) or 0.0)
        canonical_start = getattr(j, "start_minute", None)
        canonical_end = getattr(j, "end_minute", None)
        solver_start = (
            minute_dt(sc, int(canonical_start))
            if canonical_start is not None
            else bucket_time(sc, j.start_bucket))
        solver_end = (
            minute_dt(sc, int(canonical_end))
            if canonical_end is not None
            else bucket_time(sc, j.end_bucket + 1))
        validated = validated_execution_windows(j)
        if validated:
            display_rows = []
            bucket_flows = getattr(j, "bucket_flows", []) or []
            parent_path = " → ".join(getattr(j, "path", []) or [])
            for index, window in enumerate(validated, start=1):
                window_path = " | ".join(
                    " → ".join(flow.path)
                    for flow in bucket_flows
                    if window.solver_start_bucket <= flow.bucket
                    <= window.solver_end_bucket and flow.path)
                window_output_t = sum(
                    float(getattr(flow, "output_tonnage_t", 0.0) or 0.0)
                    for flow in bucket_flows
                    if window.solver_start_bucket <= flow.bucket
                    <= window.solver_end_bucket)
                if window_output_t <= 0 and output_tonnage_t > 0 \
                        and j.tonnage_t > 0:
                    window_output_t = (
                        output_tonnage_t * window.tonnage_t / j.tonnage_t)
                display_rows.append({
                    "job": (j.id if len(validated) == 1 else
                            f"{j.id}·W{index}"),
                    "start": window_start_dt(sc, window),
                    "end": window_end_dt(sc, window),
                    "rate_tph": window.rate_tph,
                    "tonnage_t": window.tonnage_t,
                    "output_tonnage_t": window_output_t,
                    "start_bucket": window.solver_start_bucket,
                    "end_bucket": window.solver_end_bucket,
                    "solver_window": (
                        f"B{window.solver_start_bucket}–"
                        f"B{window.solver_end_bucket}"),
                    "execution_duration_min": window.duration_min,
                    "timing_status": VALIDATED,
                    "timing_basis": window.basis,
                    "reserved_positioning_start_minute":
                        window.positioning_start_minute,
                    "reserved_positioning_duration_min":
                        window.positioning_duration_min,
                    "path": window_path or parent_path,
                    # Identity of THIS window's physical run. One job can hold
                    # windows in several runs, so this must never be taken
                    # from the parent job.
                    "physical_run": (
                        getattr(window, "physical_run_id", "") or "—"),
                    "physical_run_job_count": int(getattr(
                        window, "physical_run_job_count", 1) or 1),
                    "physical_run_hold_min": int(getattr(
                        window, "physical_run_hold_min", 0) or 0),
                    "continues_previous_planning_day": bool(getattr(
                        window, "continues_previous_planning_day", False)),
                    "rate_credibility": getattr(
                        window, "rate_credibility", "") or "STABLE_RATE",
                })
            if coalesce_execution_windows:
                display_rows = _coalesce_execution_display_rows(
                    display_rows, j.id)
        else:
            display_window, timing_status = display_execution_window(sc, j)
            display_rows = [{
                "job": j.id,
                "start": window_start_dt(sc, display_window),
                "end": window_end_dt(sc, display_window),
                "rate_tph": j.rate_tph,
                "tonnage_t": j.tonnage_t,
                "output_tonnage_t": output_tonnage_t,
                "start_bucket": j.start_bucket,
                "end_bucket": j.end_bucket,
                "solver_window": f"B{j.start_bucket}–B{j.end_bucket}",
                "execution_duration_min": display_window.duration_min,
                "timing_status": timing_status,
                "timing_basis": display_window.basis,
                "reserved_positioning_start_minute": None,
                "reserved_positioning_duration_min": 0,
                "path": " → ".join(getattr(j, "path", []) or []),
                # No validated window: the job's own bucket envelope is the
                # authoritative reservation, so its job-level run applies.
                "physical_run": getattr(j, "physical_run_id", "") or "—",
                "physical_run_job_count": int(getattr(
                    j, "physical_run_job_count", 1) or 1),
                "physical_run_hold_min": int(getattr(
                    j, "physical_run_hold_minutes", 0) or 0),
                "continues_previous_planning_day": bool(getattr(
                    j, "continues_previous_planning_day", False)),
                "rate_credibility": "BUCKET_ENVELOPE",
            }]

        for display in display_rows:
            start = display["start"]
            end = display["end"]
            material_class = (
                str(getattr(space, "material_class", "") or "")
                .strip().lower()
                if space is not None else "")
            grade_for_tsm = (
                explicit_output_grade
                if material_class in ("raw", "raw_screened")
                else j.grade)
            tsm_equivalent_t, tsm_label = sc.physical_tsm_info(
                float(display["tonnage_t"] or 0.0),
                material_class,
                grade_for_tsm,
            )
            if getattr(j, "material_flows", None):
                tsm_equivalent_t, tsm_label = _material_tsm_info(j, display, material_class)
            tsm_tonnage_label = (
                f"{fmt_t_bar(tsm_equivalent_t)} {tsm_label}"
                if tsm_equivalent_t is not None and tsm_label else "")
            blend_recipe = " + ".join(
                f"{component.get('grade_id', '')} "
                f"{100 * float(component.get('target_fraction', 0.0) or 0.0):.0f}%"
                for component in (getattr(j, "blend_recipe", []) or [])
                if isinstance(component, dict)
            )
            rows.append({
                "job": display["job"], "parent_job": j.id,
                "machine": j.machine,
                "machine_label": f"{site_prefix(site)} · {j.machine}",
                "kind": operation_kind,
                "space": j.space,
                "source": source or "—", "target": target or "—",
                "site": site, "area": area,
                "destination": j.dest_id or "—", "grade": j.grade,
                "output_grade": output_grade,
                "treatment": getattr(j, "treatment_id", "") or "",
                "rate_tph": display["rate_tph"],
                "solver_rate_tph": j.rate_tph,
                "execution_rate_cap_tph": execution_rate_cap,
                "tonnage_t": display["tonnage_t"],
                "output_tonnage_t": display["output_tonnage_t"],
                "parent_tonnage_t": j.tonnage_t,
                "tonnage_label": fmt_t_bar(display["tonnage_t"]),
                "mass_basis": "physical/as-handled",
                "material_class": material_class or "unknown",
                "tsm_equivalent_t": tsm_equivalent_t,
                "tsm_label": tsm_label,
                "tsm_tonnage_label": tsm_tonnage_label,
                "start": start, "end": end,
                "start_time": start.strftime("%Y-%m-%d %H:%M"),
                "end_time": end.strftime("%Y-%m-%d %H:%M"),
                "solver_start": solver_start, "solver_end": solver_end,
                "solver_window": display["solver_window"],
                "parent_solver_window": f"B{j.start_bucket}–B{j.end_bucket}",
                "execution_duration_min":
                    display["execution_duration_min"],
                "timing_status": display["timing_status"],
                "timing_basis": display["timing_basis"],
                "execution_window_count":
                    display.get(
                        "execution_window_count",
                        len(getattr(j, "execution_windows", []) or [])),
                "process_stage": process_stage,
                "depends_on": depends_on or "—",
                "dependency_delay_min": dependency_delay_min,
                "purpose": purpose or "—",
                "stacking_method": getattr(j, "method", "") or "direct",
                "blend_mode": getattr(j, "blend_mode", "") or "—",
                "blend_recipe": blend_recipe or "—",
                "chevron_passes_per_cycle": int(getattr(
                    j, "chevron_passes_per_cycle", 0) or 0),
                "chevron_grade_passes": int(getattr(
                    j, "chevron_grade_passes", 0) or 0),
                "chevron_pass_status": dict(getattr(
                    j, "chevron_pass_estimate", {}) or {}).get("status", ""),
                "chevron_equivalent_passes": dict(getattr(
                    j, "chevron_pass_estimate", {}) or {}).get("equivalent_passes"),
                "status": j.status, "origin": j.origin,
                "locked": j.locked, "path": display["path"] or "—",
                "start_repere": r0, "stop_repere": r1,
                "repere_range": repere_range,
                "start_x_m": j.start_x, "stop_x_m": j.stop_x,
                "start_bucket": display["start_bucket"],
                "end_bucket": display["end_bucket"],
                "parent_start_bucket": j.start_bucket,
                "parent_end_bucket": j.end_bucket,
                "activity_for_job": "—",
                "from_position": "—",
                "to_position": "—",
                "translation_distance_m": None,
                "translation_duration_min": None,
                "setup_duration_min": None,
                "positioning_duration_min": None,
                "available_gap_min": None,
                "reserved_positioning_start_minute":
                    display["reserved_positioning_start_minute"],
                "reserved_positioning_duration_min":
                    display["reserved_positioning_duration_min"],
                "positioning_basis": "—",
                "gap_status": "—",
                "physical_run": display.get("physical_run", "—"),
                "physical_run_job_count": int(
                    display.get("physical_run_job_count", 1) or 1),
                "allocation_slice_count": int(getattr(
                    j, "allocation_slice_count", 1) or 1),
                "continues_previous_planning_day": bool(
                    display.get("continues_previous_planning_day", False)),
                "physical_run_hold_min": int(
                    display.get("physical_run_hold_min", 0) or 0),
                "rate_credibility": display.get(
                    "rate_credibility", "STABLE_RATE"),
            })
    production = pd.DataFrame(rows, columns=_GANTT_COLUMNS)
    if include_positioning:
        positioning = _positioning_rows(sc, production)
        if positioning:
            production = pd.concat([
                production,
                pd.DataFrame(positioning, columns=_GANTT_COLUMNS),
            ], ignore_index=True)
            production = production.sort_values(
                ["machine_label", "start", "kind", "job"],
                kind="stable").reset_index(drop=True)
    return production


def _machine_disposition(plan, machine_id: str) -> Dict[str, Any]:
    """Return one machine's structured planning disposition, if available."""
    dispositions = getattr(plan, "machine_dispositions", {}) if plan else {}
    if not isinstance(dispositions, dict):
        return {}
    raw = dispositions.get(machine_id, {})
    return raw if isinstance(raw, dict) else {}


def _disposition_row_status(disposition: Dict[str, Any],
                            fallback: str) -> str:
    """Compact timeline-safe status while retaining the structured code."""
    code = str(disposition.get("code", "") or "").strip()
    reason = str(disposition.get("reason", "") or "").strip()
    classification = str(
        disposition.get("classification", "") or "").strip()
    parts = [part for part in (code, reason or classification) if part]
    return operator_text(" · ".join(parts) if parts else fallback)


def timeline_machine_register(sc, plan=None,
                              include_reference: bool = False) -> pd.DataFrame:
    """Machines disclosed on a timeline.

    The default is current solver-controlled equipment. Reference, disabled
    and future geometry is merged only when ``include_reference`` is true.
    When available, the optimiser's structured machine disposition supplies
    the concise no-job reason shown on an empty row.
    """
    rows = []
    seen = set()

    def add(mid: str, site: str, kind: str, status: str, display: bool,
            area: str = ""):
        if mid in seen:
            return
        seen.add(mid)
        rows.append({
            "machine": mid,
            "machine_label": site_tag(site, mid),
            "site": site,
            "area": area,
            "kind": kind,
            "row_status": status,
            "display_reference": display,
        })

    jobs_by_machine = {}
    if plan is not None:
        for job in getattr(plan, "jobs", []) or []:
            jobs_by_machine[job.machine] = \
                jobs_by_machine.get(job.machine, 0) + 1

    for m in sc.machines:
        planning_enabled = getattr(m, "planning_enabled", True)
        disposition = _machine_disposition(plan, m.id)
        solver_controlled = bool(
            disposition.get("solver_controlled", planning_enabled)
            if disposition else planning_enabled)
        if not solver_controlled and not include_reference:
            continue
        if jobs_by_machine.get(m.id):
            count = jobs_by_machine[m.id]
            status = f"jobs generated · {count} job(s)"
        elif disposition:
            status = _disposition_row_status(
                disposition, "idle · no allocated job")
        elif not planning_enabled:
            status = ("current equipment · disabled in this scenario"
                      if getattr(m, "lifecycle", "") == "EXISTING"
                      else "reference-only · no solver jobs")
        elif not getattr(m, "available", True):
            status = "unavailable in this scenario"
        else:
            status = "idle · no allocated job"
        try:
            area = sc.space(m.reachable_spaces[0]).area \
                if m.reachable_spaces else ""
        except (StopIteration, AttributeError):
            area = ""
        add(m.id, m.site, m.kind, status,
            not solver_controlled, area)

    # Only the optional disclosure view imports evidence geometry. The
    # primary schedule therefore cannot be crowded by display/future assets.
    if include_reference and any(
            getattr(s, "role", "") == "mine" for s in sc.sites):
        try:
            from .layout_geometry import benguerir_geometry, louta_geometry
            for site, geo in (("LOUTA", louta_geometry(sc)),
                              ("BENGUERIR", benguerir_geometry(sc))):
                for mg in geo.machines:
                    if mg.display:
                        if mg.machine_id in {
                                "102MG-ST-01", "102MG-ST-02",
                                "110MG-RC-01"}:
                            status = (
                                "MISSING_ROUTE_RATE_REACH · reference-only "
                                "102MG equipment")
                        else:
                            status = (
                                "future equipment · no solver jobs"
                                if getattr(mg, "future", False)
                                else "reference equipment · no solver jobs")
                        add(mg.machine_id, site, mg.kind,
                            status, True, "mine/reference")
        except (KeyError, StopIteration, AttributeError):
            # Imported/custom scenarios may not contain the full S3 geometry;
            # domain machines above remain a complete safe fallback.
            pass

    register = pd.DataFrame(rows, columns=[
        "machine", "machine_label", "site", "area", "kind", "row_status",
        "display_reference",
    ])
    register.attrs["timeline_start"] = sc.sim.bucket_dt(0)
    return register


def timeline_height(register: pd.DataFrame, minimum: int = 380) -> int:
    """Readable height for all machine rows without compressing labels."""
    n = 0 if register is None else len(register)
    return max(minimum, min(820, 92 + 28 * n))


def _timeline_order(labels, row_register: Optional[pd.DataFrame] = None,
                    df: Optional[pd.DataFrame] = None,
                    y_field: str = "machine_label") -> List[str]:
    site_order = {"MPH": 0, "LOU": 1, "BEN": 2}
    kind_order = {"stacker": 0, "stack": 0,
                  "reclaimer": 1, "reclaim": 1, "dispatch": 2}
    kind_by_label = (
        dict(zip(row_register["machine_label"], row_register["kind"]))
        if row_register is not None and not row_register.empty else {})
    if df is not None and not df.empty and "kind" in df.columns \
            and y_field in df.columns:
        kind_by_label.update(dict(zip(df[y_field], df["kind"])))
    return sorted(
        labels,
        key=lambda label: (
            site_order.get(str(label).partition(" · ")[0], 9),
            kind_order.get(kind_by_label.get(label, ""), 9),
            str(label)))


def _gantt_hover_summary(row: pd.Series) -> str:
    """Return only the operator-essential facts for one timeline block."""
    def value(name: str, fallback: str = "—") -> str:
        raw = row.get(name)
        try:
            missing = pd.isna(raw)
        except (TypeError, ValueError):
            missing = False
        if raw is None or missing or str(raw).strip() in ("", "nan", "None"):
            return fallback
        return html.escape(operator_text(str(raw)))

    def number(name: str, fallback: str = "—") -> str:
        raw = row.get(name)
        try:
            if raw is None or pd.isna(raw):
                return fallback
            return f"{float(raw):,.0f}"
        except (TypeError, ValueError):
            return value(name, fallback)

    kind = value("kind", "activity")
    start = value("start_time")
    end = value("end_time")
    source = value("source")
    target = value("target")

    if kind in ("positioning", "positioning conflict"):
        next_job = value("activity_for_job")
        required = number("positioning_duration_min", "0")
        travel = number("translation_duration_min", "0")
        setup = number("setup_duration_min", "0")
        heading = (
            "Positioning conflict"
            if kind == "positioning conflict" else "Positioning"
        )
        lines = [
            f"<b>{heading}</b> · for {next_job}",
            f"{source} → {target}",
            f"{required} min · travel {travel} + setup {setup}",
        ]
        if kind == "positioning conflict":
            lines.append(
                f"Required {required} min · available "
                f"{number('available_gap_min', '0')} min"
            )
        lines.append(f"{start} → {end}")
        return "<br>".join(lines)

    grade = value("grade")
    output_grade = value("output_grade", "")
    grade_text = (
        f"{grade} → {output_grade}"
        if output_grade and output_grade != grade else grade
    )
    tonnage = value("tonnage_label")
    tsm = value("tsm_tonnage_label", "")
    rate = number("rate_tph", "0")
    mass_text = f"{tonnage} physical"
    if tsm:
        mass_text += f" · {tsm}"
    lines = [
        f"<b>{kind.title()}</b> · {value('job')}",
        f"{source} → {target}",
        f"{grade_text} · {mass_text} @ {rate} physical t/h",
    ]
    if value("physical_machine", ""):
        lines.append("Equipment " + value("physical_machine"))
    if value("treatment", ""):
        lines.append(value("treatment").title() + " washing · synthetic demo")
    if kind == "stack" and value("stacking_method", "direct") == "chevron":
        pass_status = value("chevron_pass_status", "")
        equivalent = row.get("chevron_equivalent_passes")
        if pass_status == "DERIVED_FROM_FLOW" and equivalent is not None and not pd.isna(equivalent):
            pass_note = f"estimated traversals for job {float(equivalent):,.1f}"
        elif not pass_status and float(row.get("chevron_passes_per_cycle", 0) or 0) > 0:
            pass_note = ("this quality " + number("chevron_grade_passes", "0")
                         + "/" + number("chevron_passes_per_cycle", "0")
                         + " passes per cycle")
        else:
            pass_note = {
                "STACKING_SPEED_NOT_CONFIGURED": "traversal estimate unavailable: stacking speed not configured",
                "TRAVERSE_LENGTH_NOT_CONFIGURED": "traversal estimate unavailable: pile length not configured",
                "FLOW_DURATION_NOT_AVAILABLE": "traversal estimate unavailable: flow duration not available",
            }.get(pass_status, "traversal estimate unavailable")
        lines.append(
            "Chevron layers · " + value("blend_mode", "blend matrix")
            + " · " + value("blend_recipe")
            + " · " + pass_note)
    lines.append(f"{start} → {end}")
    # Distinguish the three levels explicitly: this bar is one displayed run,
    # it belongs to a physical run that may span several allocation jobs, and
    # the allocation itself is made of per-bucket slices.
    run = value("physical_run", "")
    if run and run != "—":
        run_line = f"Physical run {run}"
        jobs_in_run = row.get("physical_run_job_count")
        try:
            jobs_in_run = int(jobs_in_run)
        except (TypeError, ValueError):
            jobs_in_run = 1
        if jobs_in_run > 1:
            run_line += f" · {jobs_in_run} allocation jobs"
        slices = row.get("allocation_slice_count")
        try:
            slices = int(slices)
        except (TypeError, ValueError):
            slices = 0
        if slices > 1:
            run_line += (
                f" · allocation job holds {slices} bucket slices")
        held = row.get("physical_run_hold_min")
        try:
            held = int(held)
        except (TypeError, ValueError):
            held = 0
        if held > 0:
            run_line += f" · {held} min held running"
        if bool(row.get("continues_previous_planning_day")):
            run_line += " · continues the previous planning day"
        lines.append(run_line)
    return "<br>".join(lines)


def gantt_figure(df: pd.DataFrame, color_by: str = "machine",
                 group_by: str = "machine", height: int = 380,
                 title: str = "", row_register: Optional[pd.DataFrame] = None,
                 show_tonnage: bool = True) -> go.Figure:
    import plotly.express as px
    registered = []
    row_status = {}
    if group_by == "machine" and row_register is not None \
            and not row_register.empty:
        registered = list(row_register["machine_label"])
        row_status = {
            label: operator_text(status)
            for label, status in zip(row_register["machine_label"],
                                     row_register["row_status"])
        }
    if df.empty:
        fig = go.Figure()
        if group_by == "machine" and registered:
            order = _timeline_order(registered, row_register)
            x0 = row_register.attrs.get(
                "timeline_start", dtm.datetime(2000, 1, 1))
            if hasattr(x0, "to_pydatetime"):
                x0 = x0.to_pydatetime()
            x1 = x0 + dtm.timedelta(hours=1)
            for label in order:
                fig.add_scatter(
                    x=[x0], y=[label], mode="markers",
                    marker=dict(size=1, opacity=0), hoverinfo="skip",
                    showlegend=False)
                fig.add_annotation(
                    x=0.01, xref="paper", y=label, yref="y",
                    text=row_status.get(label, "no planned job"),
                    showarrow=False, xanchor="left",
                    font=dict(size=9, color=GREY))
            fig.update_xaxes(range=[x0, x1])
            fig.update_yaxes(
                autorange="reversed", title="", categoryorder="array",
                categoryarray=order)
            fig = plotly_layout(fig, title, height)
            fig.update_layout(
                margin=dict(l=205, r=20, t=42 if title else 16, b=36))
            return fig
        fig.add_annotation(
            text="no jobs match the current filters", showarrow=False)
        return plotly_layout(fig, title, height)
    # Site-qualify machine labels in every shared Gantt while retaining the
    # canonical machine ID in the dataframe and hover details.
    y_field = ("machine_label" if group_by == "machine"
               and "machine_label" in df.columns else group_by)
    color_field = ("machine_label" if color_by == "machine"
                   and "machine_label" in df.columns else color_by)
    labels = set(df[y_field].unique()).union(registered)
    if group_by == "machine":
        order = _timeline_order(labels, row_register, df, y_field)
    else:
        order = sorted(labels)
    plot_df = df.copy()
    if color_by == "kind" and {"kind", "stacking_method"} <= set(
            plot_df.columns):
        # Preserve the physical operation (stack) in filters, tables and
        # exports, but give chevron construction its own timeline activity.
        # A blend is therefore visually distinct without changing scheduler
        # semantics or machine-usage accounting.
        color_field = "_schedule_activity"
        plot_df[color_field] = plot_df["kind"].astype(str)
        blend_mask = (
            plot_df["kind"].astype(str).str.lower().eq("stack")
            & plot_df["stacking_method"].astype(str).str.lower().eq(
                "chevron")
        )
        plot_df.loc[blend_mask, color_field] = "blending"
    plot_df["_hover_summary"] = plot_df.apply(
        _gantt_hover_summary, axis=1)
    text_field = "tonnage_label" if show_tonnage \
        and "tonnage_label" in df.columns else None
    fig = px.timeline(
        plot_df, x_start="start", x_end="end", y=y_field, color=color_field,
        text=text_field,
        hover_name="_hover_summary",
        color_discrete_map={
            "reclaim": NAVY,
            "positioning": GREEN,
            "positioning conflict": RED,
            "stack": ORANGE,
            "blending": BLEND_PURPLE,
        },
        color_discrete_sequence=MACHINE_COLORS,
        category_orders={y_field: order})
    fig.update_traces(hovertemplate="%{hovertext}<extra></extra>")
    if color_by == "kind":
        fig.update_layout(legend_title_text="activity")
    if text_field:
        fig.update_traces(textposition="inside", insidetextanchor="middle",
                          textfont=dict(size=10, color="#ffffff"),
                          texttemplate="<b>%{text}</b>", cliponaxis=False)

    # Materialise rows with no jobs as invisible points, then explain why the
    # row is empty.  They remain disclosure-only and never resemble jobs.
    observed = set(df[y_field].unique())
    x0 = df["start"].min()
    if hasattr(x0, "to_pydatetime"):
        # Keep strict JSON/image exporters away from pandas.Timestamp.
        x0 = x0.to_pydatetime()
    for label in (r for r in registered if r not in observed):
        fig.add_scatter(x=[x0], y=[label], mode="markers",
                        marker=dict(size=1, opacity=0), hoverinfo="skip",
                        showlegend=False)
        fig.add_annotation(x=0.01, xref="paper", y=label, yref="y",
                           text=operator_text(
                               row_status.get(label, "no planned job")),
                           showarrow=False, xanchor="left",
                           font=dict(size=9, color=GREY))
    fig.update_yaxes(autorange="reversed", title="",
                     categoryorder="array", categoryarray=order)
    fig = plotly_layout(fig, title, height)
    if "timing_status" in df.columns and \
            (df["timing_status"] == VALIDATED).any():
        fig.add_annotation(
            x=1.0, xref="paper", y=1.02, yref="paper",
            text=("VALIDATED minute overlay · green positioning fits the "
                  "minute schedule · red requires replanning"),
            showarrow=False, xanchor="right",
            font=dict(size=9, color=GREY))
    if group_by == "machine":
        # Long site-qualified equipment IDs need a real label gutter.  The
        # generic chart margin clipped them in PNG/PDF exports.
        fig.update_layout(margin=dict(
            l=205, r=20, t=42 if title else 16, b=36))
    return fig


def series_fig(x, series: Dict[str, list], title: str, ytitle: str,
               kind: str = "line", height: int = 300,
               colors: Optional[List[str]] = None) -> go.Figure:
    fig = go.Figure()
    cols = colors or MACHINE_COLORS
    for i, (name, ys) in enumerate(series.items()):
        if kind == "bar":
            fig.add_bar(x=x, y=ys, name=name, marker_color=cols[i % len(cols)])
        elif kind == "area":
            fig.add_scatter(x=x, y=ys, name=name, stackgroup="one",
                            line=dict(color=cols[i % len(cols)], width=1))
        else:
            fig.add_scatter(x=x, y=ys, name=name, mode="lines",
                            line=dict(color=cols[i % len(cols)], width=2))
    fig.update_yaxes(title=ytitle)
    return plotly_layout(fig, title, height)


def show_df(df: pd.DataFrame, height: int | None = None,
            empty_message: str = "no rows for the current scenario/filters"):
    if df is None or df.empty:
        st.caption(f"∅ {operator_text(empty_message)}")
        return
    kw = {"height": height} if height else {}
    display_df = operator_frame(df)
    try:
        st.dataframe(display_df, width="stretch", **kw)
    except TypeError:              # older streamlit
        st.dataframe(display_df, use_container_width=True, **kw)


def plot(fig: go.Figure):
    display_fig = operator_figure(fig)
    try:
        st.plotly_chart(display_fig, width="stretch")
    except TypeError:              # older streamlit
        st.plotly_chart(display_fig, use_container_width=True)

# ---- machine planning coverage -------------------------------------------------
#: Exact blockers for reference/display machines that still cannot generate
#: genuine solver jobs. Active Benguerir machines do not belong in this map.
MACHINE_BLOCKING_DATA = {
    "102MG-ST-01": ("102MG screened-product piles are not solver spaces: "
                    "actual capacities/dimensions/live mass are not loaded; "
                    "S3-A25's 100,000 t capacity + 50,000 t t0 per pile are "
                    "display-only assumptions. L01 confirms this dedicated "
                    "stacker at 2,200 t/h, but metric reach and executable "
                    "L01→L02 integration are not configured"),
    "102MG-ST-02": ("same blocker as 102MG-ST-01 — dedicated stacker and "
                    "2,200 t/h rate documented; live state, metric reach and "
                    "solver route integration are not configured"),
    "110MG-RC-01": ("L01 confirms one shared reclaimer for both 102MG "
                    "stockpiles and 110MG-CB-01 toward beneficiation; live "
                    "state, rate, metric reach and executable L01→L02 "
                    "integration are not configured"),
}


def machine_planning_coverage(sc, plan=None) -> pd.DataFrame:
    """Compact coverage diagnostic: one row per known machine.

    Structured optimiser dispositions use ``jobs generated``, ``legitimate
    idle`` and ``modelling gap``. Legacy/custom plans without dispositions
    retain the earlier fallback categories and static reference explanations.
    """
    reg = timeline_machine_register(sc, plan, include_reference=True)
    jobs_by_machine = {}
    if plan is not None:
        for j in plan.jobs:
            jobs_by_machine.setdefault(j.machine, []).append(j)
    rows = []
    for _, r in reg.iterrows():
        mid = r["machine"]
        js = jobs_by_machine.get(mid, [])
        mrec = next((m for m in sc.machines if m.id == mid), None)
        disposition = _machine_disposition(plan, mid)
        if disposition:
            cat = str(
                disposition.get("classification", "") or
                ("jobs generated" if js else "legitimate idle"))
            why = str(disposition.get("reason", "") or r["row_status"])
        elif js:
            cat = "jobs generated"
            why = (f"{len(js)} job(s) · "
                   f"{sum(j.tonnage_t for j in js):,.0f} t planned")
        elif mrec is not None \
                and getattr(mrec, "planning_enabled", True) \
                and getattr(mrec, "available", True):
            cat = "solver-enabled, idle"
            why = ("optimisable — the current plan allocates no job "
                   "(demand/routing outcome, not a data gap)")
        elif mrec is not None and getattr(mrec, "lifecycle", "") == "EXISTING":
            cat = "current equipment — disabled in scenario"
            why = "planning disabled in this scenario configuration"
        elif mid in MACHINE_BLOCKING_DATA:
            cat = "modelling gap"
            why = MACHINE_BLOCKING_DATA[mid]
        else:
            cat = "reference-only / data-blocked"
            why = MACHINE_BLOCKING_DATA.get(
                mid, "display/reference geometry — no solver data")
        rows.append({
            "machine": r["machine_label"], "site": r["site"],
            "kind": r["kind"], "category": cat,
            "jobs": len(js),
            "tonnage_t": round(sum(j.tonnage_t for j in js), 0),
            "reason_code": (
                disposition.get("code", "") if disposition else
                "MISSING_ROUTE_RATE_REACH"
                if mid in {"102MG-ST-01", "102MG-ST-02",
                           "110MG-RC-01"} else
                "REFERENCE_DATA_GAP" if mid in MACHINE_BLOCKING_DATA else ""),
            "explanation": operator_text(why),
            "solver_controlled": (
                bool(disposition.get("solver_controlled"))
                if disposition and "solver_controlled" in disposition
                else bool(mrec is not None and getattr(
                    mrec, "planning_enabled", True))),
            "reachable_spaces": " · ".join(
                (disposition.get("reachable_spaces", []) or [])
                if disposition else
                (getattr(mrec, "reachable_spaces", []) if mrec else [])),
            "route": operator_text(
                disposition.get("route", "") if disposition else ""),
            "inventory_t": (
                disposition.get("inventory_t")
                if disposition else None),
            "free_capacity_t": (
                disposition.get("free_capacity_t")
                if disposition else None),
            "job_trigger": operator_text(
                disposition.get("job_trigger", "")
                if disposition else ""),
        })
    order = {
        "jobs generated": 0,
        "legitimate idle": 1,
        "solver-enabled, idle": 1,
        "modelling gap": 2,
        "current equipment — disabled in scenario": 2,
        "reference-only / data-blocked": 3,
    }
    df = pd.DataFrame(rows)
    return (
        df.sort_values(
            ["category", "site", "machine"],
            key=lambda series: series.map(order).fillna(99)
            if series.name == "category" else series)
        .reset_index(drop=True)
    )


def machine_usage_summary(sc, plan=None) -> pd.DataFrame:
    """Return one honest planning-usage row per known machine.

    Productive work uses the same validated minute windows as the Job
    Schedule.  When a job has no fully validated minute overlay,
    :func:`jobs_to_gantt_df` deliberately falls back to its solver-bucket
    reservation and the row's ``timing_basis`` discloses that lower
    precision.  Only successfully reserved (green) positioning is occupied
    time; red positioning conflicts are reported separately.

    A zero is meaningful only for a usable solver resource in a valid plan.
    Disabled, planning-scope, reference and modelling-gap equipment receives
    ``N/A`` usage values instead of a misleading 0 %.  Likewise, an absent or
    unsuccessful plan describes configuration coverage but does not invent a
    zero-work allocation.
    """
    columns = [
        "machine", "machine_id", "site", "kind", "category", "status",
        "reason_code", "reason", "solver_controlled", "usage_applicable",
        "timing_basis", "jobs", "tonnes_t", "activity_mix", "stack_h",
        "reclaim_h", "other_work_h", "work_h", "positioning_h",
        "conflict_h", "conflict_count", "occupied_h", "available_h",
        "idle_h", "usage_pct", "schedule_conflict",
    ]
    register = timeline_machine_register(
        sc, plan, include_reference=True)
    if register.empty:
        return pd.DataFrame(columns=columns)

    plan_status = str(getattr(plan, "status", "") or "").upper()
    valid_plan = plan is not None and plan_status in {"OPTIMAL", "FEASIBLE"}
    plan_jobs = list(getattr(plan, "jobs", []) or []) if valid_plan else []
    active_jobs = [
        job for job in plan_jobs
        if str(getattr(job, "status", "PLANNED")).upper()
        not in {"CANCELLED", "REJECTED"}
    ]
    timeline = (
        jobs_to_gantt_df(sc, active_jobs, include_positioning=True)
        if active_jobs else pd.DataFrame(columns=_GANTT_COLUMNS)
    )
    machines = {machine.id: machine for machine in sc.machines}
    non_operational_lifecycles = {
        "OPTIONAL_PROPOSED", "FUTURE", "REFERENCE_ONLY", "OUT_OF_SCOPE",
    }
    horizon_buckets = max(int(sc.sim.horizon_buckets), 0)
    horizon_offset = max(int(getattr(plan, "horizon_offset", 0) or 0), 0)
    dt_h = max(float(sc.sim.dt_h), 0.0)

    def _number_sum(frame: pd.DataFrame, column: str) -> float:
        if frame.empty or column not in frame:
            return 0.0
        return float(pd.to_numeric(
            frame[column], errors="coerce").fillna(0.0).sum())

    def _hours(frame: pd.DataFrame) -> float:
        return _number_sum(frame, "execution_duration_min") / 60.0

    rows = []
    for _, registered in register.iterrows():
        machine_id = str(registered["machine"])
        machine = machines.get(machine_id)
        machine_jobs = [
            job for job in active_jobs if job.machine == machine_id]
        machine_timeline = (
            timeline[timeline["machine"] == machine_id]
            if not timeline.empty else timeline
        )
        production = machine_timeline[
            ~machine_timeline["kind"].isin(
                ["positioning", "positioning conflict"])
        ] if not machine_timeline.empty else machine_timeline
        positioning = machine_timeline[
            machine_timeline["kind"] == "positioning"
        ] if not machine_timeline.empty else machine_timeline
        conflicts = machine_timeline[
            machine_timeline["kind"] == "positioning conflict"
        ] if not machine_timeline.empty else machine_timeline

        stack_h = _hours(production[production["kind"] == "stack"]) \
            if not production.empty else 0.0
        reclaim_h = _hours(
            production[production["kind"] == "reclaim"]) \
            if not production.empty else 0.0
        other = production[
            ~production["kind"].isin(["stack", "reclaim"])
        ] if not production.empty else production
        other_work_h = _hours(other)
        work_h = stack_h + reclaim_h + other_work_h
        positioning_h = _hours(positioning)
        conflict_h = _hours(conflicts)
        occupied_h = work_h + positioning_h
        conflict_count = int(len(conflicts))
        tonnes_t = float(sum(
            float(getattr(job, "tonnage_t", 0.0) or 0.0)
            for job in machine_jobs))

        disposition = _machine_disposition(plan, machine_id) \
            if valid_plan else {}
        disposition_category = str(
            disposition.get("classification", "") or "")
        lifecycle = str(
            getattr(machine, "lifecycle", "") or "").upper() \
            if machine is not None else "REFERENCE_ONLY"
        planning_enabled = bool(
            getattr(machine, "planning_enabled", True)) \
            if machine is not None else False
        available = bool(getattr(machine, "available", True)) \
            if machine is not None else False
        lifecycle_operational = lifecycle not in non_operational_lifecycles
        solver_controlled = bool(
            disposition.get(
                "solver_controlled",
                machine is not None and available and planning_enabled
                and lifecycle_operational)
        ) if valid_plan else bool(
            machine is not None and available and planning_enabled
            and lifecycle_operational)

        if machine is None:
            category = "reference-only / data-blocked"
            status = "Reference only"
            reason_code = "REFERENCE_DATA_GAP"
            reason = str(registered.get(
                "row_status", "display/reference geometry only"))
        elif not lifecycle_operational:
            category = "reference-only / data-blocked"
            status = "Outside operating scope"
            reason_code = lifecycle or "REFERENCE_DATA_GAP"
            reason = (
                f"{lifecycle.replace('_', ' ').lower()} equipment is not "
                "an active planning resource")
        elif not planning_enabled:
            category = "modelling gap"
            status = "Outside planning scope"
            reason_code = "OUTSIDE_PLANNING_SCOPE"
            reason = (
                str(disposition.get("reason", "") or "")
                if disposition else
                "equipment is not enabled for solver scheduling")
        elif not available:
            category = "legitimate idle"
            status = "Unavailable"
            reason_code = "UNAVAILABLE"
            reason = (
                "machine is disabled/unavailable in the current scenario "
                "configuration")
        elif not valid_plan:
            category = "awaiting plan"
            status = "Awaiting plan" if plan is None else "No valid plan"
            reason_code = "NO_PLAN" if plan is None else "INVALID_PLAN"
            reason = (
                "Generate a valid plan to calculate machine usage."
                if plan is None else
                f"Plan status {plan_status or 'UNKNOWN'} has no valid "
                "machine-usage allocation.")
        elif disposition_category == "modelling gap":
            category = "modelling gap"
            status = "Modelling gap"
            reason_code = str(
                disposition.get("code", "") or "MISSING_ROUTE_DATA")
            reason = str(
                disposition.get("reason", "") or
                "no executable planning candidate is available")
        else:
            category = disposition_category or (
                "jobs generated" if machine_jobs else "legitimate idle")
            status = "Scheduled" if machine_jobs else "Available, not used"
            reason_code = str(
                disposition.get("code", "") or
                ("SCHEDULED" if machine_jobs else "NOT_SELECTED_BY_PLAN"))
            reason = str(
                disposition.get("reason", "") or
                (f"{len(machine_jobs)} purposeful planned job(s)"
                 if machine_jobs else
                 "demand and configured targets are met without it"))

        usage_applicable = bool(
            valid_plan and machine is not None and solver_controlled
            and lifecycle_operational and planning_enabled and available
            and category != "modelling gap")
        available_h = float("nan")
        idle_h = float("nan")
        usage_pct = float("nan")
        schedule_conflict = False
        if usage_applicable:
            available_buckets = sum(
                1 for local_bucket in range(horizon_buckets)
                if not sc.machine_blocked(
                    machine_id, horizon_offset + local_bucket))
            available_h = available_buckets * dt_h
            if available_h > 0:
                idle_h = max(available_h - occupied_h, 0.0)
                usage_pct = 100.0 * occupied_h / available_h
                schedule_conflict = occupied_h > available_h + 1e-9
            else:
                usage_applicable = False
                status = "No available time"
                reason_code = "CALENDAR_BLOCKED"
                reason = "machine is calendar-blocked for the full plan horizon"

        if machine_jobs:
            timing_statuses = set(
                str(value) for value in production.get(
                    "timing_status", pd.Series(dtype=str)).dropna())
            if timing_statuses == {VALIDATED}:
                timing_basis = "validated minutes"
            elif VALIDATED in timing_statuses:
                timing_basis = "mixed minute / bucket"
            else:
                timing_basis = "solver buckets"
        else:
            timing_basis = "—"
        mix = []
        if stack_h > 1e-9:
            mix.append(f"stack {stack_h:.1f} h")
        if reclaim_h > 1e-9:
            mix.append(f"reclaim {reclaim_h:.1f} h")
        if other_work_h > 1e-9:
            mix.append(f"other {other_work_h:.1f} h")

        numeric = usage_applicable
        rows.append({
            "machine": str(registered["machine_label"]),
            "machine_id": machine_id,
            "site": str(registered["site"]),
            "kind": str(registered["kind"]),
            "category": category,
            "status": status,
            "reason_code": reason_code,
            "reason": operator_text(reason),
            "solver_controlled": solver_controlled,
            "usage_applicable": usage_applicable,
            "timing_basis": timing_basis,
            "jobs": len(machine_jobs) if numeric else pd.NA,
            "tonnes_t": round(tonnes_t, 1) if numeric else float("nan"),
            "activity_mix": " · ".join(mix) if mix and numeric else "—",
            "stack_h": round(stack_h, 3) if numeric else float("nan"),
            "reclaim_h": round(reclaim_h, 3) if numeric else float("nan"),
            "other_work_h": (
                round(other_work_h, 3) if numeric else float("nan")),
            "work_h": round(work_h, 3) if numeric else float("nan"),
            "positioning_h": (
                round(positioning_h, 3) if numeric else float("nan")),
            "conflict_h": round(conflict_h, 3) if numeric else float("nan"),
            "conflict_count": conflict_count if numeric else pd.NA,
            "occupied_h": round(occupied_h, 3) if numeric else float("nan"),
            "available_h": (
                round(available_h, 3) if numeric else float("nan")),
            "idle_h": round(idle_h, 3) if numeric else float("nan"),
            "usage_pct": round(usage_pct, 1) if numeric else float("nan"),
            "schedule_conflict": schedule_conflict if numeric else False,
        })
    return pd.DataFrame(rows, columns=columns)


def fleet_usage_kpis(sc, plan=None) -> Dict[str, object]:
    """Aggregate auditable fleet-time KPIs from machine usage rows.

    Available hours exclude calendar-blocked time. Productive work,
    successfully reserved positioning and idle time are mutually exclusive
    and reconcile to that availability basis. Positioning conflicts are
    unresolved exposure, so they are reported separately rather than being
    double-counted as either occupied or idle time.
    """
    usage = machine_usage_summary(sc, plan)
    applicable = (
        usage[usage["usage_applicable"].fillna(False)].copy()
        if not usage.empty else usage.copy()
    )
    valid_plan = (
        plan is not None
        and str(getattr(plan, "status", "") or "").upper()
        in {"OPTIMAL", "FEASIBLE"}
    )

    def total(column: str) -> float:
        if applicable.empty or column not in applicable:
            return 0.0
        return float(pd.to_numeric(
            applicable[column], errors="coerce").fillna(0.0).sum())

    available_h = total("available_h")
    work_h = total("work_h")
    positioning_h = total("positioning_h")
    occupied_h = work_h + positioning_h
    # Derive the aggregate remainder from the same totals used on the cards.
    # Summing per-machine values rounded to milliseconds of an hour can leave
    # a visible 0.001 h residual across a large fleet.
    idle_h = max(available_h - occupied_h, 0.0)
    conflict_h = total("conflict_h")
    conflict_count = int(round(total("conflict_count")))
    eligible_machines = int(len(applicable))
    used_machines = int((pd.to_numeric(
        applicable.get("work_h", pd.Series(dtype=float)), errors="coerce")
        .fillna(0.0) > 1e-9).sum())

    def pct(hours: float) -> Optional[float]:
        return 100.0 * hours / available_h if available_h > 0 else None

    return {
        "valid_plan": bool(valid_plan),
        "eligible_machines": eligible_machines,
        "used_machines": used_machines,
        "fleet_participation_pct": (
            100.0 * used_machines / eligible_machines
            if eligible_machines > 0 else None),
        "available_h": available_h,
        "work_h": work_h,
        "positioning_h": positioning_h,
        "idle_h": idle_h,
        "occupied_h": occupied_h,
        "productive_utilisation_pct": pct(work_h),
        "scheduled_occupation_pct": pct(occupied_h),
        "positioning_loss_pct": pct(positioning_h),
        "idle_pct": pct(idle_h),
        "conflict_h": conflict_h,
        "conflict_count": conflict_count,
        "conflict_exposure_pct": pct(conflict_h),
        "reconciliation_error_h": (
            available_h - work_h - positioning_h - idle_h),
    }


def machine_usage_figure(df: pd.DataFrame) -> go.Figure:
    """Stack work, positioning and idle hours on an availability basis."""
    fig = go.Figure()
    if df is None or df.empty:
        fig.add_annotation(
            text="no machines in the current scenario", showarrow=False)
        return plotly_layout(
            fig, "Machine usage", 280)

    frame = df.copy()
    labels = frame["machine"].astype(str).tolist()
    applicable = frame["usage_applicable"].fillna(False).astype(bool)
    numeric = frame[applicable].copy()
    unavailable = frame[~applicable].copy()

    # Invisible anchors retain disclosure-only/reference rows in the same
    # categorical register as usable machines.
    fig.add_scatter(
        x=[0.0] * len(labels), y=labels, mode="markers",
        marker=dict(size=1, opacity=0), hoverinfo="skip",
        showlegend=False)

    components = [
        ("Stack work", "stack_h", ORANGE),
        ("Reclaim work", "reclaim_h", NAVY),
        ("Other work", "other_work_h", BLUE),
        ("Positioning", "positioning_h", GREEN),
        ("Idle available", "idle_h", "#d7dee5"),
    ]
    for label, column, color in components:
        if numeric.empty:
            break
        values = pd.to_numeric(
            numeric[column], errors="coerce").fillna(0.0)
        custom = [
            [
                f"{float(row.usage_pct):.1f}%",
                str(int(row.jobs)),
                f"{float(row.tonnes_t):,.0f} t",
            ]
            for row in numeric.itertuples()
        ]
        fig.add_bar(
            x=values, y=numeric["machine"], name=label,
            orientation="h", marker_color=color, customdata=custom,
            hovertemplate=(
                "<b>%{y}</b><br>"
                + label + ": %{x:.1f} h<br>"
                "Occupied: %{customdata[0]} of available time<br>"
                "Jobs: %{customdata[1]} · handled: %{customdata[2]}"
                "<extra></extra>"))

    conflicted = numeric[
        pd.to_numeric(
            numeric["conflict_count"], errors="coerce").fillna(0) > 0
    ]
    if not conflicted.empty:
        fig.add_scatter(
            x=conflicted["available_h"], y=conflicted["machine"],
            name="Positioning conflict", mode="markers",
            marker=dict(color=RED, size=10, symbol="x", line=dict(width=2)),
            customdata=conflicted[["conflict_count", "conflict_h"]].values,
            hovertemplate=(
                "<b>%{y}</b><br>"
                "%{customdata[0]} positioning conflict(s)<br>"
                "%{customdata[1]:.1f} h cannot be reserved"
                "<extra></extra>"))

    if not numeric.empty:
        fig.add_scatter(
            x=numeric["available_h"], y=numeric["machine"],
            text=[f"  {value:.0f}%"
                  for value in numeric["usage_pct"].fillna(0.0)],
            mode="text", textposition="middle right",
            textfont=dict(size=10, color=TEXT_GREY),
            hoverinfo="skip", showlegend=False)
    if not unavailable.empty:
        fig.add_scatter(
            x=[0.0] * len(unavailable), y=unavailable["machine"],
            text=[
                f"N/A · {operator_text(status)}"
                for status in unavailable["status"]
            ],
            mode="text", textposition="middle right",
            textfont=dict(size=10, color=GREY),
            customdata=unavailable[["reason_code", "reason"]].values,
            hovertemplate=(
                "<b>%{y}</b><br>%{customdata[0]}<br>"
                "%{customdata[1]}<extra></extra>"),
            showlegend=False)

    # Preserve one readable row per machine. The former 760 px cap compressed
    # larger three-site fleets until labels and bars collided.
    height = max(320, 112 + 31 * len(frame))
    fig = plotly_layout(
        fig, "Machine usage · work + reserved positioning + idle", height)
    fig.update_layout(
        barmode="stack",
        margin=dict(l=225, r=82, t=48, b=52),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0))
    fig.update_xaxes(title="hours in available planning horizon",
                     rangemode="tozero")
    fig.update_yaxes(
        title="", autorange="reversed", categoryorder="array",
        categoryarray=labels)
    return fig

