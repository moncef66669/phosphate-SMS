"""Session-state plumbing shared by all pages (no optimisation logic here).

Also owns the navigation model (3 primary sections plus Advanced), the
operator-facing domain/planning-scenario aliases, and the
solve-request state machine (staged progress, duplicate-solve guard, plan is
only replaced on a successful solve).
"""
from __future__ import annotations

import copy
import time
from typing import List, Optional

import streamlit as st

from mph_sim import datasets
from mph_sim.domain import Scenario, PlanResult, SimEvent
from mph_sim.optimizer import SolveOptions, solve_plan
from mph_sim.persistence import RunStore
from mph_sim.simulator import Simulator
from mph_sim.validation import validate_scenario, issue_counts


# ---------------------------------------------------------------- navigation
# Canonical labels keep the original numeric IDs so existing ``?page=N`` links,
# tests and programmatic navigation remain stable.  The sidebar renders the
# shorter operator-facing titles in PAGE_TITLES.
NAV_GROUPS = {
    "Configure": ["2 · Input Data", "3 · Stockyards & Inventory",
                  "4 · Routes & Equipment", "17 · Grade Profiles"],
    "Plan": ["5 · Demand & Quality"],
    "Monitor": ["1 · Executive Overview",
                "15 · Global Stockyard Layout & Flow",
                "9 · Job Schedule", "16 · Machine Coverage"],
    "Advanced": ["6 · Planning Workspace",
                 "7 · Simulation Control", "8 · Results & KPIs",
                 "10 · Exceptions & Replanning",
                 "11 · Three-SMS Integration Console",
                 "12 · Assumptions & Data Gaps",
                 "13 · Scenario Comparison", "14 · Audit & Export"],
}

MONITOR_LANDING_PAGE = "1 · Executive Overview"
MONITOR_MACHINE_COVERAGE_PAGE = "16 · Machine Coverage"
CONFIGURE_LANDING_PAGE = "2 · Input Data"
CONFIGURE_GRADE_PROFILES_PAGE = "17 · Grade Profiles"
CONFIGURE_VISIBLE_PAGE_MAP = {
    CONFIGURE_LANDING_PAGE: CONFIGURE_LANDING_PAGE,
    "3 · Stockyards & Inventory": CONFIGURE_LANDING_PAGE,
    "4 · Routes & Equipment": CONFIGURE_LANDING_PAGE,
    CONFIGURE_GRADE_PROFILES_PAGE: CONFIGURE_GRADE_PROFILES_PAGE,
}
MONITOR_VISIBLE_PAGE_MAP = {
    MONITOR_LANDING_PAGE: MONITOR_LANDING_PAGE,
    "9 · Job Schedule": MONITOR_LANDING_PAGE,
    "15 · Global Stockyard Layout & Flow": MONITOR_LANDING_PAGE,
    MONITOR_MACHINE_COVERAGE_PAGE: MONITOR_MACHINE_COVERAGE_PAGE,
}

# The canonical grouping above remains complete so existing page numbers,
# exports, tests and programmatic links stay stable. The operator sidebar
# consolidates the KPI, flow and schedule routes into one page and keeps
# Machine Coverage as the second visible Monitor page.
VISIBLE_NAV_GROUPS = {
    **NAV_GROUPS,
    "Configure": [
        CONFIGURE_LANDING_PAGE,
        CONFIGURE_GRADE_PROFILES_PAGE,
    ],
    "Monitor": [MONITOR_LANDING_PAGE, MONITOR_MACHINE_COVERAGE_PAGE],
}

PAGE_TITLES = {
    "1 · Executive Overview": "KPI flow diagram and Job scheduling",
    "2 · Input Data": "Operational configuration",
    "3 · Stockyards & Inventory": "Stockyards & Starting Inventory",
    "4 · Routes & Equipment": "Machines & Paths",
    "5 · Demand & Quality": "S0 · Three-site planning",
    "6 · Planning Workspace": "Planning settings",
    "7 · Simulation Control": "Simulation Control",
    "8 · Results & KPIs": "Detailed Results",
    "9 · Job Schedule": "Jobs & Schedule",
    "10 · Exceptions & Replanning": "Exceptions & Replanning",
    "11 · Three-SMS Integration Console": "Three-SMS Integration",
    "12 · Assumptions & Data Gaps": "Assumptions & Data Gaps",
    "13 · Scenario Comparison": "Scenario Comparison",
    "14 · Audit & Export": "Audit & Export",
    "15 · Global Stockyard Layout & Flow": "Stockyards & Flow",
    "16 · Machine Coverage": "Machine coverage",
    "17 · Grade Profiles": "Grade profiles",
}

ALL_PAGE_LABELS = sorted((l for ls in NAV_GROUPS.values() for l in ls),
                         key=lambda l: int(l.split(" ·")[0]))
PAGE_TO_GROUP = {l: g for g, ls in NAV_GROUPS.items() for l in ls}
NO_PLAN_LANDING_PAGE = "5 · Demand & Quality"
PLAN_LANDING_PAGE = "5 · Demand & Quality"

# Operator navigation deliberately exposes only the commissioned planning
# scope. S0 is a UI code; the solver and persisted built-in dataset retain
# their established S3 identity so no route, constraint or result semantics
# change during the navigation redesign.
PRIMARY_DOMAIN = "BG + LO + MPH"
FUTURE_DOMAIN = "MS"
DOMAIN_OPTIONS = (PRIMARY_DOMAIN, FUTURE_DOMAIN)
PLANNING_SCENARIOS = {"S0": datasets.S3_SCENARIO_NAME}
DEFAULT_PLANNING_SCENARIO = "S0"


def planning_scenario_dataset(code: str) -> str:
    """Return the internal built-in dataset name behind a UI scenario code."""
    try:
        return PLANNING_SCENARIOS[code]
    except KeyError as exc:
        raise ValueError(f"unknown planning scenario: {code}") from exc


def planning_scenario_code(dataset_name: str) -> Optional[str]:
    """Return the UI code for an internal built-in dataset, when exposed."""
    return next(
        (code for code, name in PLANNING_SCENARIOS.items()
         if name == dataset_name),
        None,
    )


def planning_scenario_label(code: str) -> str:
    """Compact operator label used by the Plan workspace and sidebar."""
    if code == "S0":
        return "S0 — BG + LO + MPH"
    return code


def page_title(label: str) -> str:
    """Return the concise operator-facing title for a canonical page label."""
    return PAGE_TITLES.get(label, label)


def visible_page_label(label: str) -> str:
    """Map a canonical route to its visible sidebar page."""
    return CONFIGURE_VISIBLE_PAGE_MAP.get(
        label, MONITOR_VISIBLE_PAGE_MAP.get(label, label))


def page_label_from_qp(qp_value) -> Optional[str]:
    """'?page=N' -> page label (1-based, clamped), None if unusable."""
    try:
        n = int(str(qp_value))
    except (TypeError, ValueError):
        return None
    n = max(1, min(n, len(ALL_PAGE_LABELS)))
    return ALL_PAGE_LABELS[n - 1]


def goto_page(label: str):
    """Queue programmatic navigation for the next app rerun.

    Page actions execute after the keyed sidebar radios have been created, so
    mutating those widget values immediately raises a StreamlitAPIException.
    The app consumes this small handoff before it instantiates navigation on
    the following rerun.
    """
    if label not in PAGE_TO_GROUP:
        raise ValueError(f"unknown page label: {label}")
    st.session_state["_pending_page"] = label
    try:
        st.query_params["page"] = label.split(" ·")[0]
    except Exception:
        pass


# ---------------------------------------------------------------- solve flow
SOLVE_STAGES = ["validating inputs",
                "building route & constraint model",
                "generating warm start",
                "solving CP-SAT",
                "preparing results"]
TYPICAL_SOLVE_S = {"S1": 2, "S2": 16, "S3": 50}


def typical_solve_s(name: str) -> int:
    return TYPICAL_SOLVE_S.get((name or "")[:2], 30)


def request_solve() -> bool:
    """Queue a solve of the LOADED scenario. Returns False if one is already
    running/queued (duplicate-solve guard)."""
    ss = st.session_state
    if ss.get("solving") or ss.get("solve_request"):
        return False
    ss.solve_request = True
    return True


def init_state():
    ss = st.session_state
    if ss.get("domain_nav") not in DOMAIN_OPTIONS:
        ss.domain_nav = PRIMARY_DOMAIN
    if ss.get("planning_scenario_code") not in PLANNING_SCENARIOS:
        ss.planning_scenario_code = DEFAULT_PLANNING_SCENARIO
    if "scenario" not in ss:
        # S3 is the project's primary Domain-A planning run and therefore the
        # operator-facing default. S2 remains available as the faster M16-only
        # diagnostic scenario.
        name = next(n for n in datasets.BUILTIN_SCENARIOS if n.startswith("S3"))
        ss.scenario_name = name
        ss.scenario = datasets.get_scenario(name)
        ss.plan = None
        ss.sim = None
        ss.issues = validate_scenario(ss.scenario)
        ss.queued_events = []
        ss.compare = {}
        ss.last_solve = None
        ss.run_store = RunStore("runs.db")
        ss.overrides_log = []
        ss.inventory_snapshot_history = []
        ss.inventory_snapshot_upload_nonce = 0
        ss.sms_live_health_by_site = {}
        ss.sms_live_pull_preview = None
        ss.sms_live_config_revision = ""
        ss.sms_live_pull_in_progress = False
        ss.sms_live_pull_last_issues = []

    # A Streamlit hot reload does not rebuild an object already stored in
    # session_state. Apply narrow, versioned built-in migrations before any
    # page reads the scenario, and invalidate outputs that were solved against
    # the superseded inputs. Imported scenarios are intentionally untouched.
    migration = (datasets.migrate_loaded_builtin(ss.scenario)
                 if ss.get("scenario_name") in (datasets.S3_SCENARIO_NAME,
                                                datasets.S2_SCENARIO_NAME)
                 else "")
    if migration:
        ss.issues = validate_scenario(ss.scenario)
        if migration == "solver":
            ss.plan = None
            ss.sim = None
            ss.queued_events = []
            ss.compare = {}
            ss.last_solve = None
            ss.last_solve_result = None
            ss.solve_request = False
            ss.solving = False
            ss.flash = (
                "Built-in planning baseline updated. The previous plan was "
                "cleared because it predates staged mine-to-M16 routing, "
                "exact demand balance and validated minute execution windows. "
                "Generate a new plan to use the current model."
                if ss.get("scenario_name") == datasets.S2_SCENARIO_NAME else
                "S3 planning baseline updated: mine flows now stage through "
                "C1/C2 and M16, exact demand blocks over-delivery, and the "
                "full-flow fleet exercise is now the default demand profile. "
                "It uses genuine connected work across Louta, Benguerir and "
                "MPH without assigning reference/future assets. The previous "
                "plan was cleared; generate a new plan for validated minute "
                "execution windows.")
        else:
            ss.flash = ("Benguerir raw-pile starting state refreshed — "
                        "BG-RAW-1/2 = 100,000 t start mass each (50%); "
                        "multi-profile ASSUMED compositions are displayed "
                        "(P1 Global/High-Cd, P2 Global/Low-Cd); no live "
                        "Benguerir assay or segment snapshot is loaded.")


def load_scenario(name: str, obj: Optional[Scenario] = None):
    ss = st.session_state
    ss.scenario_name = name
    ss.scenario = obj if obj is not None else datasets.get_scenario(name)
    ss.plan = None
    ss.sim = None
    ss.queued_events = []
    ss.compare = {}
    ss.issues = validate_scenario(ss.scenario)
    ss.last_solve = None
    ss.last_solve_result = None
    ss.solve_request = False
    ss.solving = False
    exposed_code = planning_scenario_code(name)
    if (exposed_code is not None
            and ss.get("planning_scenario_code") != exposed_code):
        ss.planning_scenario_code = exposed_code
    ss.inventory_snapshot_preview = None
    ss.inventory_snapshot_upload_nonce = ss.get(
        "inventory_snapshot_upload_nonce", 0) + 1
    ss.sms_live_health_by_site = {}
    ss.sms_live_pull_preview = None
    ss.sms_live_config_revision = ""
    ss.sms_live_pull_in_progress = False
    ss.sms_live_pull_last_issues = []


def commit_inventory_snapshot(result) -> bool:
    """Commit a validated, pure snapshot result and invalidate stale outputs.

    ``apply_inventory_snapshot`` performs all validation on a deep copy.  This
    UI boundary only swaps that copy into session state after a successful
    result, ensuring a plan/simulation can never remain attached to old t0
    inventory.
    """
    if not getattr(result, "applied", False):
        return False
    ss = st.session_state
    ss.scenario = result.scenario
    ss.plan = None
    ss.sim = None
    ss.queued_events = []
    ss.last_solve = None
    ss.last_solve_result = None
    ss.solve_request = False
    ss.issues = validate_scenario(ss.scenario)
    ss.inventory_snapshot_preview = None
    ss.inventory_snapshot_upload_nonce = ss.get(
        "inventory_snapshot_upload_nonce", 0) + 1
    ss.sms_live_health_by_site = {}
    ss.sms_live_pull_preview = None
    ss.sms_live_pull_in_progress = False
    ss.sms_live_pull_last_issues = []
    history = list(ss.get("inventory_snapshot_history", []))
    history.append(dict(ss.scenario.config.get("INVENTORY_SNAPSHOT", {})))
    ss.inventory_snapshot_history = history[-20:]
    return True


def store_live_inventory_preview(result, config_revision: str = "") -> bool:
    """Store only canonical/safe live-preview data in session state."""
    if getattr(result, "snapshot", None) is None:
        return False
    st.session_state.sms_live_pull_preview = {
        "snapshot": result.snapshot,
        "issues": list(getattr(result, "issues", [])),
        "requested_sites": list(getattr(result, "requested_sites", [])),
        "scenario_fingerprint": str(
            getattr(result, "scenario_fingerprint", "")),
        "config_revision": str(config_revision or ""),
    }
    st.session_state.sms_live_config_revision = str(config_revision or "")
    return True


def clear_live_inventory_preview():
    st.session_state.sms_live_pull_preview = None


def sc() -> Scenario:
    return st.session_state.scenario


def plan() -> Optional[PlanResult]:
    return st.session_state.plan


def sim() -> Optional[Simulator]:
    return st.session_state.sim


def invalidate_outputs() -> bool:
    """Clear results derived from inputs that have just been committed.

    Returns ``True`` when a plan or simulation existed. Keeping this operation
    central prevents an edited demand, quality specification, calendar, fixed
    job or model table from remaining attached to stale solver output.
    """
    ss = st.session_state
    had_outputs = ss.get("plan") is not None or ss.get("sim") is not None
    ss.plan = None
    ss.sim = None
    ss.queued_events = []
    ss.compare = {}
    ss.last_solve = None
    ss.last_solve_result = None
    ss.solve_request = False
    ss.solving = False
    return had_outputs


def revalidate():
    st.session_state.issues = validate_scenario(sc())
    return st.session_state.issues


def run_plan(max_time_s: Optional[float] = None,
             progress_cb=None) -> PlanResult:
    """Solve the LOADED scenario. The visible plan is only replaced when the
    solver returns a usable plan; a failed solve keeps the previous plan and
    is surfaced via `ss.last_solve_result`. Stages are reported through
    `progress_cb(stage_label)` (see SOLVE_STAGES)."""
    ss = st.session_state
    ss.solving = True
    ss.solve_stage_log = []

    def cb(stage: str):
        ss.solve_stage_log.append(stage)
        if progress_cb:
            progress_cb(stage)

    try:
        cb(SOLVE_STAGES[0])
        ss.issues = validate_scenario(ss.scenario)
        res = solve_plan(ss.scenario, SolveOptions(max_time_s=max_time_s),
                         progress_cb=cb)   # emits stages 2..5
        ss.last_solve_result = res
        if res.status in ("OPTIMAL", "FEASIBLE"):
            ss.plan = res
            ss.sim = None
        ss.last_solve = res.solver_wall_s
        ss.run_store.record(ss.scenario_name, ss.scenario.sim.seed, res.solver,
                            res.status, res.solver_wall_s, 0,
                            {"objective": res.objective,
                             "unmet_t": sum(sum(v) for v in res.unmet.values()),
                             "jobs": len(res.jobs)}, note="plan")
        return res
    finally:
        ss.solving = False
        ss.solve_request = False


def ensure_sim(auto_replan: bool = True) -> Optional[Simulator]:
    ss = st.session_state
    if ss.plan is None:
        return None
    if ss.sim is None:
        ss.sim = Simulator(copy.deepcopy(ss.scenario), copy.deepcopy(ss.plan),
                           events=list(ss.queued_events),
                           auto_replan=auto_replan)
    return ss.sim


def chain_mode(scenario: Scenario) -> str:
    """Topology class of a scenario, for scenario-aware overview rendering.

    'three-site' — mine source yards present (Louta/Benguerir -> M16, S3)
    'm16-only'   — M16 hub without modelled mines (corridor arrivals are
                   simulated inputs, S2)
    'sulphur'    — Domain B only (M17, S1)
    """
    if any(s.role == "mine" for s in scenario.sites):
        return "three-site"
    if any(sp.area.startswith("M16") for sp in scenario.spaces):
        return "m16-only"
    return "sulphur"


# site/domain filter vocabulary (requirement: expose all six everywhere a
# page filters by site, and SAY which are loaded rather than hiding them)
SITE_FILTERS = ["LOUTA", "BENGUERIR", "MPH M16", "MPH M17",
                "Domain A — phosphate", "Domain B — sulphur"]


def match_site_filter(opt: str, site: str, area: str = "") -> bool:
    a = area or ""
    return {
        "LOUTA": site == "LOUTA",
        "BENGUERIR": site == "BENGUERIR",
        "MPH M16": site == "MPH" and a.startswith("M16"),
        "MPH M17": site == "MPH" and a.startswith("M17"),
        "Domain A — phosphate": site in ("LOUTA", "BENGUERIR")
        or (site == "MPH" and a.startswith("M16")),
        "Domain B — sulphur": site == "MPH" and a.startswith("M17"),
    }.get(opt, False)


def loaded_site_filters(scenario: Scenario):
    """(loaded, not_loaded) among SITE_FILTERS for this scenario."""
    loaded = []
    for opt in SITE_FILTERS:
        if any(match_site_filter(opt, sp.site, sp.area) for sp in scenario.spaces):
            loaded.append(opt)
    return loaded, [o for o in SITE_FILTERS if o not in loaded]


def status_summary() -> dict:
    ss = st.session_state
    counts = issue_counts(ss.issues)
    p = ss.plan
    s = ss.sim
    return {
        "scenario": ss.scenario_name,
        "errors": counts.get("ERROR", 0),
        "warnings": counts.get("WARNING", 0),
        "plan_status": p.status if p else "no plan",
        "jobs": len(p.jobs) if p else 0,
        "solver_s": f"{p.solver_wall_s:.1f}s" if p else "—",
        "sim_bucket": (f"{s.bucket}/{s.T}" if s else "idle"),
        "replans": s.replans if s else 0,
        "unmet_t": (sum(sum(v) for v in p.unmet.values()) if p else None),
    }
