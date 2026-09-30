"""Session-state plumbing shared by all pages (no optimisation logic here).

Also owns the navigation model (3 primary sections plus Advanced), the
operator-facing domain/planning-scenario aliases, and the
solve-request state machine (staged progress, duplicate-solve guard, plan is
only replaced on a successful solve).
"""
from __future__ import annotations

import copy
import os
import time
from pathlib import Path
from typing import List, Optional

import streamlit as st

from mph_sim import (blend_preferences, datasets, mine_arrivals,
                     prewash_blending, washing)
from mph_sim.analysis_tools import (BUNDLE_SCHEMA_VERSION, PROJECT_ID,
                                    build_run_bundle)
from mph_sim.domain import Scenario, PlanResult, SimEvent
from mph_sim.grade_catalog import (GradeCatalogError,
                                   apply_saved_grade_catalog)
from mph_sim.optimizer import SolveOptions, solve_plan
from mph_sim.kpis import compute_kpis
from mph_sim.persistence import RunStore
from mph_sim.simulator import Simulator
from mph_sim.validation import validate_scenario, issue_counts


# ---------------------------------------------------------------- navigation
# Canonical labels keep the original numeric IDs so existing ``?page=N`` links,
# tests and programmatic navigation remain stable.  The sidebar renders the
# shorter operator-facing titles in PAGE_TITLES.
NAV_GROUPS = {
    "Configure": ["2 · Input Data", "3 · Stockyards & Inventory",
                  "4 · Routes & Equipment", "17 · Grade Profiles",
                  "23 · Washing Treatments", "24 · Mine Arrivals"],
    "Plan": ["5 · Demand & Quality",
             "18 · Routing Availability",
             "19 · Planned Contingencies",
             "20 · Blending"],
    "Monitor": ["1 · Executive Overview",
                "15 · Global Stockyard Layout & Flow",
                "9 · Job Schedule", "8 · Results & KPIs",
                "16 · Machine Coverage", "22 · Washing Treatments",
                "21 · Business Case"],
    "Advanced": ["6 · Planning Workspace",
                 "7 · Simulation Control",
                 "10 · Exceptions & Replanning",
                 "11 · Three-SMS Integration Console",
                 "12 · Assumptions & Data Gaps",
                 "13 · Scenario Comparison", "14 · Audit & Export"],
}

MONITOR_LANDING_PAGE = "1 · Executive Overview"
MONITOR_RESULTS_PAGE = "8 · Results & KPIs"
MONITOR_MACHINE_COVERAGE_PAGE = "16 · Machine Coverage"
MONITOR_WASHING_TREATMENTS_PAGE = "22 · Washing Treatments"
CONFIGURE_LANDING_PAGE = "2 · Input Data"
CONFIGURE_GRADE_PROFILES_PAGE = "17 · Grade Profiles"
CONFIGURE_WASHING_TREATMENTS_PAGE = "23 · Washing Treatments"
CONFIGURE_MINE_ARRIVALS_PAGE = "24 · Mine Arrivals"
LEGACY_CONFIGURE_GRADE_YIELD_PAGE = "18 · Grade Yield & Moisture"
PLAN_ROUTING_PAGE = "18 · Routing Availability"
PLAN_CONTINGENCIES_PAGE = "19 · Planned Contingencies"
PLAN_BLENDING_PAGE = "20 · Blending"
CONFIGURE_VISIBLE_PAGE_MAP = {
    CONFIGURE_LANDING_PAGE: CONFIGURE_LANDING_PAGE,
    "3 · Stockyards & Inventory": CONFIGURE_LANDING_PAGE,
    "4 · Routes & Equipment": CONFIGURE_LANDING_PAGE,
    CONFIGURE_GRADE_PROFILES_PAGE: CONFIGURE_GRADE_PROFILES_PAGE,
    CONFIGURE_WASHING_TREATMENTS_PAGE: CONFIGURE_WASHING_TREATMENTS_PAGE,
    CONFIGURE_MINE_ARRIVALS_PAGE: CONFIGURE_MINE_ARRIVALS_PAGE,
}
MONITOR_VISIBLE_PAGE_MAP = {
    MONITOR_LANDING_PAGE: MONITOR_LANDING_PAGE,
    "9 · Job Schedule": MONITOR_LANDING_PAGE,
    "15 · Global Stockyard Layout & Flow": MONITOR_LANDING_PAGE,
    MONITOR_RESULTS_PAGE: MONITOR_RESULTS_PAGE,
    MONITOR_MACHINE_COVERAGE_PAGE: MONITOR_MACHINE_COVERAGE_PAGE,
    MONITOR_WASHING_TREATMENTS_PAGE: MONITOR_WASHING_TREATMENTS_PAGE,
}
PLAN_VISIBLE_PAGE_MAP = {
    PLAN_ROUTING_PAGE: PLAN_ROUTING_PAGE,
    # The former table-based contingency editor is consolidated into the
    # diagram-backed routing workspace.  Keep page 19 as a stable deep-link
    # alias, but never expose a duplicate operator page.
    PLAN_CONTINGENCIES_PAGE: PLAN_ROUTING_PAGE,
}

# The canonical grouping above remains complete so existing page numbers,
# exports, tests and programmatic links stay stable. The operator sidebar
# consolidates the KPI, flow and schedule routes into one page, then exposes
# plan-generation results and machine coverage as focused Monitor pages.
VISIBLE_NAV_GROUPS = {
    **NAV_GROUPS,
    "Configure": [
        CONFIGURE_LANDING_PAGE,
        CONFIGURE_GRADE_PROFILES_PAGE,
        CONFIGURE_WASHING_TREATMENTS_PAGE,
        CONFIGURE_MINE_ARRIVALS_PAGE,
    ],
    "Plan": [NAV_GROUPS["Plan"][0], PLAN_ROUTING_PAGE, PLAN_BLENDING_PAGE],
    "Monitor": [MONITOR_LANDING_PAGE, MONITOR_RESULTS_PAGE,
                MONITOR_MACHINE_COVERAGE_PAGE,
                MONITOR_WASHING_TREATMENTS_PAGE, "21 · Business Case"],
}

PAGE_TITLES = {
    "21 · Business Case": "Business case",
    "1 · Executive Overview": "KPI flow diagram and Job scheduling",
    "2 · Input Data": "Operational configuration",
    "3 · Stockyards & Inventory": "Stockyards & Starting Inventory",
    "4 · Routes & Equipment": "Machines & Paths",
    "5 · Demand & Quality": "Demand planning",
    "6 · Planning Workspace": "Planning settings",
    "7 · Simulation Control": "Simulation Control",
    "8 · Results & KPIs": "Plan results",
    "9 · Job Schedule": "Jobs & Schedule",
    "10 · Exceptions & Replanning": "Exceptions & Replanning",
    "11 · Three-SMS Integration Console": "Three-SMS Integration",
    "12 · Assumptions & Data Gaps": "Assumptions & Data Gaps",
    "13 · Scenario Comparison": "Scenario Comparison",
    "14 · Audit & Export": "Audit & Export",
    "15 · Global Stockyard Layout & Flow": "Stockyards & Flow",
    "16 · Machine Coverage": "Machine coverage",
    "22 · Washing Treatments": "Treatment monitoring",
    "23 · Washing Treatments": "Washing treatments",
    "24 · Mine Arrivals": "Mine arrivals",
    "17 · Grade Profiles": "Grade configuration",
    PLAN_ROUTING_PAGE: "Routing availability",
    PLAN_CONTINGENCIES_PAGE: "Planned contingencies",
    PLAN_BLENDING_PAGE: "Blending",
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


def _custom_planning_scenarios() -> dict:
    """Return this session's user-created planning workspaces."""
    try:
        return st.session_state.get("custom_planning_scenarios", {})
    except Exception:
        return {}


def planning_scenario_codes() -> list[str]:
    """Stable built-in-first scenario order used by the Plan selector."""
    return [*PLANNING_SCENARIOS, *_custom_planning_scenarios()]


def planning_scenario_dataset(code: str) -> str:
    """Return the internal dataset/workspace name behind a UI scenario code."""
    if code in PLANNING_SCENARIOS:
        return PLANNING_SCENARIOS[code]
    record = _custom_planning_scenarios().get(code)
    if record is not None:
        return str(record["scenario"].name)
    raise ValueError(f"unknown planning scenario: {code}")


def planning_scenario_code(dataset_name: str) -> Optional[str]:
    """Return the UI code for a built-in or user-created planning scenario."""
    builtin = next(
        (code for code, name in PLANNING_SCENARIOS.items()
         if name == dataset_name),
        None,
    )
    if builtin is not None:
        return builtin
    return next(
        (code for code, record in _custom_planning_scenarios().items()
         if record["scenario"].name == dataset_name),
        None,
    )


def planning_scenario_label(code: str) -> str:
    """Compact operator label used by the Plan workspace and sidebar."""
    if code == "S0":
        return "S0 — BG + LO + MPH"
    record = _custom_planning_scenarios().get(code)
    if record is not None:
        return f"{code} — {record['label']}"
    return code


def create_custom_planning_scenario(label: str) -> str:
    """Clone the active workspace under the next available operator code."""
    clean_label = " ".join(str(label or "").split()).strip()
    if not clean_label:
        raise ValueError("Enter a scenario name.")
    if len(clean_label) > 60:
        raise ValueError("Use a scenario name of 60 characters or fewer.")
    registry = _custom_planning_scenarios()
    existing = {
        str(record.get("label", "")).casefold()
        for record in registry.values()
    }
    if clean_label.casefold() in existing:
        raise ValueError("A scenario with that name already exists.")
    used = set(PLANNING_SCENARIOS) | set(registry)
    number = 1
    while f"S{number}" in used:
        number += 1
    code = f"S{number}"
    cloned = copy.deepcopy(sc())
    cloned.name = f"{code} — {clean_label}"
    cloned.config["PLANNING_SCENARIO_WORKSPACE"] = {
        "code": code,
        "label": clean_label,
        "source": planning_scenario_code(st.session_state.scenario_name)
                  or st.session_state.scenario_name,
    }
    registry[code] = {"label": clean_label, "scenario": cloned}
    st.session_state.custom_planning_scenarios = registry
    return code


def rename_custom_planning_scenario(code: str, label: str) -> None:
    """Rename a custom workspace without changing its stable scenario code."""
    clean_label = " ".join(str(label or "").split()).strip()
    if not clean_label:
        raise ValueError("Enter a scenario name.")
    if len(clean_label) > 60:
        raise ValueError("Use a scenario name of 60 characters or fewer.")
    registry = _custom_planning_scenarios()
    if code not in registry:
        raise ValueError("The built-in S0 scenario cannot be renamed.")
    if any(
            other_code != code
            and str(record.get("label", "")).casefold()
            == clean_label.casefold()
            for other_code, record in registry.items()):
        raise ValueError("A scenario with that name already exists.")
    record = registry[code]
    old_name = record["scenario"].name
    record["label"] = clean_label
    record["scenario"].name = f"{code} — {clean_label}"
    record["scenario"].config.setdefault(
        "PLANNING_SCENARIO_WORKSPACE", {})["label"] = clean_label
    if st.session_state.get("scenario_name") == old_name:
        st.session_state.scenario_name = record["scenario"].name
        st.session_state.scenario = record["scenario"]
    st.session_state.custom_planning_scenarios = registry


def load_planning_scenario(code: str) -> None:
    """Load a built-in baseline or the retained custom workspace."""
    if code in PLANNING_SCENARIOS:
        load_scenario(PLANNING_SCENARIOS[code])
        return
    record = _custom_planning_scenarios().get(code)
    if record is None:
        raise ValueError(f"unknown planning scenario: {code}")
    load_scenario(record["scenario"].name, record["scenario"])


def page_title(label: str) -> str:
    """Return the concise operator-facing title for a canonical page label."""
    return PAGE_TITLES.get(label, label)


def visible_page_label(label: str) -> str:
    """Map a canonical route to its visible sidebar page."""
    if label == LEGACY_CONFIGURE_GRADE_YIELD_PAGE:
        return CONFIGURE_GRADE_PROFILES_PAGE
    return CONFIGURE_VISIBLE_PAGE_MAP.get(label, PLAN_VISIBLE_PAGE_MAP.get(
        label, MONITOR_VISIBLE_PAGE_MAP.get(label, label)))


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
TYPICAL_SOLVE_S = {"S1": 2, "S2": 16, "S3": 84}


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


def blend_preferences_path():
    """Keep automated tests isolated from the operator's saved settings."""
    override = os.environ.get("MPH_BLEND_PREFERENCES_PATH")
    if override:
        return Path(override)
    if "PYTEST_CURRENT_TEST" in os.environ:
        return None
    return blend_preferences.DEFAULT_BLEND_PREFERENCES_PATH


def save_blend_enablement_preferences(scenario):
    path = blend_preferences_path()
    if path is not None:
        blend_preferences.save_blend_enablement(scenario, path)


def _restore_blend_enablement_preferences(scenario):
    path = blend_preferences_path()
    if path is None:
        return
    try:
        blend_preferences.apply_saved_blend_enablement(scenario, path)
        st.session_state.pop("blend_preferences_error", None)
    except blend_preferences.BlendPreferencesError as exc:
        st.session_state["blend_preferences_error"] = str(exc)


def save_mine_arrivals_preferences(scenario):
    """Save applied supply while keeping tests clear of operator preferences."""
    override = os.environ.get("MPH_MINE_ARRIVALS_PATH")
    if "PYTEST_CURRENT_TEST" in os.environ and not override:
        return None
    return mine_arrivals.save_preferences(
        scenario, Path(override) if override else None)


def _restore_mine_arrivals_preferences(scenario):
    override = os.environ.get("MPH_MINE_ARRIVALS_PATH")
    if "PYTEST_CURRENT_TEST" in os.environ and not override:
        return
    try:
        mine_arrivals.apply_saved_preferences(
            scenario, Path(override) if override else None)
        st.session_state.pop("mine_arrivals_preferences_error", None)
    except (ValueError, OSError) as exc:
        st.session_state["mine_arrivals_preferences_error"] = str(exc)


def init_state():
    ss = st.session_state
    new_session = "scenario" not in ss
    if "custom_planning_scenarios" not in ss:
        ss.custom_planning_scenarios = {}
    if ss.get("domain_nav") not in DOMAIN_OPTIONS:
        ss.domain_nav = PRIMARY_DOMAIN
    if ss.get("planning_scenario_code") not in planning_scenario_codes():
        ss.planning_scenario_code = DEFAULT_PLANNING_SCENARIO
    if "scenario" not in ss:
        # S3 is the project's primary Domain-A planning run and therefore the
        # operator-facing default. S2 remains available as the faster M16-only
        # diagnostic scenario.
        name = next(n for n in datasets.BUILTIN_SCENARIOS if n.startswith("S3"))
        ss.scenario_name = name
        ss.scenario = datasets.get_scenario(name)
        # S0 is the operator demonstration workspace.  Activate its explicit
        # demand showcase at load time. Blending is enabled with optimized
        # shares, while occupied MPH piles retain their original material.
        datasets.apply_planning_demand_profile(
            ss.scenario, datasets.DEFAULT_PLANNING_DEMAND_PROFILE)
        default_washing = washing.defaults()
        default_washing["enabled"] = True
        ss.scenario = washing.stage_configuration(
            ss.scenario, default_washing)
        mine_arrivals.install_defaults(ss.scenario)
        if "PYTEST_CURRENT_TEST" not in os.environ:
            try:
                apply_saved_grade_catalog(ss.scenario)
            except GradeCatalogError as exc:
                ss.grade_catalog_error = str(exc)
        _restore_blend_enablement_preferences(ss.scenario)
        _restore_mine_arrivals_preferences(ss.scenario)
        ss._mine_arrivals_initialized = True
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
        if "PYTEST_CURRENT_TEST" not in os.environ:
            try:
                apply_saved_grade_catalog(ss.scenario)
                ss.grade_catalog_error = ""
            except GradeCatalogError as exc:
                ss.grade_catalog_error = str(exc)
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
                "S0 now uses the single Integrated weekly operations preset. "
                "The assumed Louta and Benguerir product buffers now start at "
                "10,000 t each so demand exercises washing and stacking while "
                "the first daily solve retains a practical operating buffer; the previous plan "
                "was cleared. Generate a new weekly plan.")
        else:
            ss.flash = ("Benguerir raw-pile starting state refreshed — "
                        "BG-RAW-1/2 = 100,000 t start mass each (50%); "
                        "multi-profile ASSUMED compositions are displayed "
                        "(P1 Global/High-Cd, P2 Global/Low-Cd); no live "
                        "Benguerir assay or segment snapshot is loaded.")

    # Only migrate hot built-in UI sessions. Imported legacy scenarios retain
    # their documented line-based policy until explicitly edited.
    old_prewash = ss.scenario.config.get(prewash_blending.CONFIG_KEY, {})
    if (ss.get("scenario_name") == datasets.S3_SCENARIO_NAME
            and isinstance(old_prewash, dict) and old_prewash
            and int(old_prewash.get("model_version", 0) or 0) < 4):
        updated_prewash = prewash_blending.settings(ss.scenario)
        for point in updated_prewash.get("points", {}).values():
            point.setdefault("enabled_source_spaces",
                             prewash_blending.enabled_source_spaces(point))
        updated_prewash["model_version"] = prewash_blending.MODEL_VERSION
        ss.scenario.config[prewash_blending.CONFIG_KEY] = updated_prewash
        invalidate_outputs()
        revalidate()

    # Apply the user's all-enabled/optimized request once to hot S0 sessions.
    # Do not reset later checkbox/mode edits on page navigation or reruns.
    if (ss.get("scenario_name") == datasets.S3_SCENARIO_NAME
            and int(ss.scenario.config.get("BLENDING_DEFAULTS_REVISION", 0)) < 1):
        datasets.apply_all_enabled_blending_defaults(ss.scenario)
        save_blend_enablement_preferences(ss.scenario)
        for key in list(ss):
            if str(key).startswith(("mph_blending_", "prewash_")):
                del ss[key]
        invalidate_outputs()
        revalidate()
        ss.flash = ("Blending defaults updated: all modeled stockpiles enabled, "
                    "optimized shares, and both washing options available. "
                    "Occupied MPH piles retain their stock until emptied. "
                    "Generate a new plan; subsequent policy edits are preserved.")

    # Washing is part of the operator-facing S0 baseline. Hot Streamlit
    # sessions created before that baseline existed retain an old Scenario
    # object, so install the default once and discard the incompatible plan.
    raw_washing = ss.scenario.config.get(washing.CONFIG_KEY)
    try:
        washing_version = int(
            raw_washing.get("model_version", 0) or 0
            if isinstance(raw_washing, dict) else 0)
    except (TypeError, ValueError):
        washing_version = 0
    if (not new_session
            and ss.get("scenario_name") == datasets.S3_SCENARIO_NAME
            and "BUILTIN_BASELINE_REVISION" in ss.scenario.config
            and washing_version < washing.MODEL_VERSION):
        default_washing = washing.settings(ss.scenario)
        default_washing["model_version"] = washing.MODEL_VERSION
        default_washing["enabled"] = True
        ss.scenario = washing.stage_configuration(
            ss.scenario, default_washing)
        ss.plan = None
        ss.sim = None
        ss.queued_events = []
        ss.compare = {}
        ss.last_solve = None
        ss.last_solve_result = None
        ss.solve_request = False
        ss.solving = False
        ss.issues = validate_scenario(ss.scenario)
        ss.flash = (
            "Washing calibration updated. The previous plan was cleared "
            "because its treatment qualities and G10 certification use an "
            "older calibration; generate a new plan.")


    # Retained Streamlit sessions predate new configuration pages. Restore
    # the supply overlay once; ordinary reruns never overwrite applied edits.
    if not ss.get("_mine_arrivals_initialized"):
        if (ss.get("scenario_name") == datasets.S3_SCENARIO_NAME
                and mine_arrivals.supported(ss.scenario)):
            previous = copy.deepcopy(ss.scenario)
            if (mine_arrivals.CONFIG_KEY not in ss.scenario.config
                    and (not washing.enabled(ss.scenario)
                         or ss.scenario.sim.mixed_resolution_enabled)):
                # Preserve the operator's selected material model/grid.
                # The new supply editor stays available with arrivals off.
                arrival_defaults = mine_arrivals.settings(ss.scenario)
                arrival_defaults["enabled"] = False
                ss.scenario = mine_arrivals.staged_configuration(
                    ss.scenario, arrival_defaults)
            else:
                mine_arrivals.install_defaults(ss.scenario)
            _restore_mine_arrivals_preferences(ss.scenario)
            if ss.scenario != previous:
                ss.mine_arrivals_nonce = ss.get("mine_arrivals_nonce", 0) + 1
                invalidate_outputs()
                revalidate()
        ss._mine_arrivals_initialized = True


    # Existing browser sessions retain arrival columns made before the saved
    # grade catalogue was applied. Repair zero-only stale references on reload;
    # no tonnes, flows, operator settings or solved outputs change.
    if mine_arrivals.reconcile_grade_catalog(ss.scenario):
        ss.mine_arrivals_nonce = ss.get("mine_arrivals_nonce", 0) + 1
        ss.issues = validate_scenario(ss.scenario)


def load_scenario(name: str, obj: Optional[Scenario] = None):
    ss = st.session_state
    active_code = planning_scenario_code(ss.get("scenario_name", ""))
    if active_code in _custom_planning_scenarios() and ss.get("scenario"):
        _custom_planning_scenarios()[active_code]["scenario"] = ss.scenario
    ss.scenario_name = name
    ss.scenario = obj if obj is not None else datasets.get_scenario(name)
    if obj is None and name == datasets.S3_SCENARIO_NAME:
        datasets.apply_planning_demand_profile(
            ss.scenario, datasets.DEFAULT_PLANNING_DEMAND_PROFILE)
        default_washing = washing.defaults()
        default_washing["enabled"] = True
        ss.scenario = washing.stage_configuration(
            ss.scenario, default_washing)
        mine_arrivals.install_defaults(ss.scenario)
    if ("PYTEST_CURRENT_TEST" not in os.environ
            and obj is None and name in (
            datasets.S3_SCENARIO_NAME, datasets.S2_SCENARIO_NAME)):
        try:
            apply_saved_grade_catalog(ss.scenario)
            ss.grade_catalog_error = ""
        except GradeCatalogError as exc:
            ss.grade_catalog_error = str(exc)
    if obj is None and name in (datasets.S3_SCENARIO_NAME,
                                datasets.S2_SCENARIO_NAME):
        _restore_blend_enablement_preferences(ss.scenario)
    if obj is None and name == datasets.S3_SCENARIO_NAME:
        _restore_mine_arrivals_preferences(ss.scenario)
    ss._mine_arrivals_initialized = True
    ss.mine_arrivals_nonce = ss.get("mine_arrivals_nonce", 0) + 1
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
        run_bundle = None
        run_note = "plan"
        try:
            run_kpis = compute_kpis(ss.scenario, res)
            run_bundle = build_run_bundle(ss.scenario, res, run_kpis)
            ss.last_analysis_bundle_error = ""
        except Exception as exc:
            # Result-analysis evidence must never turn a completed solver call
            # into an application failure. Preserve the legacy summary row and
            # make the adapter failure explicit for diagnostics.
            run_kpis = {
                "unmet_t": sum(sum(values) for values in res.unmet.values()),
            }
            ss.last_analysis_bundle_error = (
                f"{type(exc).__name__}: {exc}")
            run_note = (
                "plan; result evidence bundle unavailable: "
                + ss.last_analysis_bundle_error)
        run_id = ss.run_store.record(
            ss.scenario_name, ss.scenario.sim.seed, res.solver,
            res.status, res.solver_wall_s, 0,
            {"objective": res.objective,
             "unmet_t": run_kpis.get("unmet_t", 0.0),
             "service_pct": run_kpis.get("service_pct"),
             "jobs": len(res.jobs)}, note=run_note, artifact=run_bundle,
            schema_version=BUNDLE_SCHEMA_VERSION, project=PROJECT_ID)
        ss.last_run_id = run_id
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
        # Physical runs, not allocation jobs: see mph_sim/runs.py.
        "runs": (int(p.breakdown.get("physical_runs", 0) or 0)
                 if p else 0),
    }
