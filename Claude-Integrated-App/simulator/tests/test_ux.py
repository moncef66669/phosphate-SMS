"""UX-refinement tests: scenario-state integrity, staged solve flow,
grouped navigation, empty state, and safe KPI formatting."""
from copy import deepcopy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest          # noqa: E402

from conftest import nav_to                       # noqa: E402
from mph_sim import datasets, prewash_blending   # noqa: E402
from mph_sim.domain import PlanResult             # noqa: E402
from mph_sim.validation import validate_scenario  # noqa: E402
from ui import common, theme                     # noqa: E402

APP = str(ROOT / "app.py")


def _fresh():
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert not at.exception, at.exception
    return at


def _plan_generate_button(at):
    """Return the single shared sidebar solve action."""
    buttons = [
        b for b in at.sidebar.button if "Generate Plan" in str(b.label)
    ]
    assert len(buttons) == 1
    assert not [
        b for b in at.main.button if "Generate Plan" in str(b.label)
    ]
    return buttons[0]


def _inject_fast_s1_solve(at):
    """Queue the small diagnostic scenario without exposing a UI selector.

    The operator UI deliberately offers only S0.  S1 remains useful here as a
    fast harness for the generic staged solve executor.
    """
    scenario = datasets.sulphur_minimal()
    at.session_state["scenario_name"] = scenario.name
    at.session_state["scenario"] = scenario
    at.session_state["plan"] = None
    at.session_state["sim"] = None
    at.session_state["issues"] = validate_scenario(scenario)
    at.session_state["solve_request"] = True


def _visible_text(root):
    values = []
    for element_type in (
        "subheader", "markdown", "caption", "info", "warning", "success",
    ):
        values.extend(
            str(element.value) for element in getattr(root, element_type)
        )
    return "\n".join(values)


def _select_blend_stage(at, stage_label):
    [stage] = [control for control in at.main.segmented_control
               if control.label == "Blending stage"]
    stage.set_value(stage_label).run()
    assert not at.exception, at.exception


# ---- 1./2./3. S0 alias and domain navigation integrity -----------------------
def test_default_s0_alias_loads_three_site_and_has_sidebar_selector():
    at = _fresh()

    assert common.PLANNING_SCENARIOS == {
        "S0": datasets.S3_SCENARIO_NAME,
    }
    assert common.planning_scenario_dataset("S0") == \
        datasets.S3_SCENARIO_NAME
    assert common.planning_scenario_code(datasets.S3_SCENARIO_NAME) == "S0"
    assert at.session_state["planning_scenario_code"] == "S0"
    assert at.session_state["scenario_name"] == datasets.S3_SCENARIO_NAME
    assert at.session_state["scenario"].name == datasets.S3_SCENARIO_NAME
    assert {sp.site for sp in at.session_state["scenario"].spaces} == {
        "BENGUERIR", "LOUTA", "MPH",
    }
    assert at.session_state["plan"] is None
    assert at.session_state["sim"] is None
    assert len(at.sidebar.selectbox) == 1
    sidebar_selector = at.sidebar.selectbox[0]
    assert sidebar_selector.label == "Planning scenario"
    assert sidebar_selector.options == [
        "S0 · BG + LO + MPH · no plan yet"]
    assert sidebar_selector.value == "S0"


def test_plan_page_exposes_only_s0_and_loaded_dataset_agrees():
    at = _fresh()

    assert at.sidebar.radio[0].value == "Plan"
    assert at.sidebar.radio[1].options == [
        "Demand planning", "Routing availability", "Blending",
    ]
    scenario_selector = at.main.selectbox[0]
    assert scenario_selector.label == "Planning scenario"
    assert scenario_selector.options == ["S0 — BG + LO + MPH"]
    assert scenario_selector.value == "S0"
    assert at.session_state["scenario_name"] == datasets.S3_SCENARIO_NAME
    assert at.session_state["scenario"].name == datasets.S3_SCENARIO_NAME
    assert any(s.role == "mine" for s in at.session_state["scenario"].sites)


def test_top_domain_nav_ms_placeholder_preserves_s0_plan_and_disables_solve():
    at = _fresh()
    at.session_state["plan"] = PlanResult(
        status="FEASIBLE", solver_wall_s=1.0)
    at.run()

    domain = at.segmented_control[0]
    assert domain.label == "Domain"
    assert domain.options == [common.PRIMARY_DOMAIN, common.FUTURE_DOMAIN]
    assert domain.value == common.PRIMARY_DOMAIN
    assert _plan_generate_button(at).disabled is False

    domain.set_value(common.FUTURE_DOMAIN)
    at.run()
    assert not at.exception, at.exception
    assert "coming later" in _visible_text(at.main).lower()
    assert at.session_state["scenario_name"] == datasets.S3_SCENARIO_NAME
    assert at.session_state["plan"].status == "FEASIBLE"
    assert _plan_generate_button(at).disabled is True

    at.segmented_control[0].set_value(common.PRIMARY_DOMAIN)
    at.run()
    assert not at.exception, at.exception
    assert at.session_state["scenario_name"] == datasets.S3_SCENARIO_NAME
    assert at.session_state["plan"].status == "FEASIBLE"
    assert _plan_generate_button(at).disabled is False


def test_hot_reload_migrates_stale_s3_corridor_times_and_clears_outputs():
    at = _fresh()
    stale = at.session_state["scenario"]
    stale.config.pop("BUILTIN_BASELINE_REVISION", None)
    stale.link("C1").transit_buckets = 4
    stale.link("C2").transit_buckets = 2
    at.session_state["compare"] = {"stale baseline": {"service_pct": 100.0}}

    at.run()

    assert not at.exception, at.exception
    migrated = at.session_state["scenario"]
    assert migrated.link("C1").transit_buckets == 2
    assert migrated.link("C2").transit_buckets == 4
    assert len([p for p in migrated.belt_profile if p.link_id == "C1"]) == 2
    # CORRECTED 25-Jul-2026 (S2-A9/S3-A4): the corridors share ONE current
    # 2,600 t/h reception path, so the pre-loaded profile alternates them —
    # C1 in the first buckets, C2 in the ones C1 leaves free — instead of
    # implying a physically impossible 5,200 t/h aggregate arrival.
    assert len([p for p in migrated.belt_profile if p.link_id == "C2"]) == 2
    per_bucket = {}
    for entry in migrated.belt_profile:
        if entry.link_id in ("C1", "C2"):
            per_bucket[entry.arrive_bucket] =                 per_bucket.get(entry.arrive_bucket, 0.0) + entry.mass_t
    assert max(per_bucket.values()) <= 2600 + 1e-6, per_bucket
    assert migrated.config["BUILTIN_BASELINE_REVISION"] == \
        datasets.S3_BASELINE_REVISION
    assert at.session_state["plan"] is None
    assert at.session_state["sim"] is None
    assert at.session_state["compare"] == {}


def test_metadata_only_migration_refreshes_raw_mass_without_solver_change():
    scenario = datasets.end_to_end()
    scenario.config["BUILTIN_BASELINE_REVISION"] = "stale-display-metadata"
    before_corridors = {
        link_id: scenario.link(link_id).transit_buckets
        for link_id in ("C1", "C2")
    }
    before_belt = list(scenario.belt_profile)

    migration = datasets.migrate_loaded_builtin(scenario)

    assert migration == "metadata"
    assert {
        link_id: scenario.link(link_id).transit_buckets
        for link_id in ("C1", "C2")
    } == before_corridors
    assert scenario.belt_profile == before_belt
    raw_source = next(node for node in scenario.nodes
                      if node.id == "SRC:BG-RAW")
    assert "BG-RAW-1 = 200,000 t" in raw_source.label
    assert "BG-RAW-2 = 200,000 t" in raw_source.label
    raw_assumption = next(a for a in scenario.assumptions
                          if a.id == "S3-A14")
    assert "BG-RAW-1 = 200,000 t" in raw_assumption.text
    assert "BG-RAW-2 = 200,000 t" in raw_assumption.text
    mass_assumption = next(a for a in scenario.assumptions
                           if a.id == "S3-A19")
    assert "starting" in mass_assumption.text
    assert "100,000 t" in mass_assumption.text
    assert "50%" in mass_assumption.text
    proxy_assumption = next(a for a in scenario.assumptions
                            if a.id == "S3-A20")
    assert proxy_assumption.status == "ASSUMED"
    assert "multiple grade profiles" in proxy_assumption.text
    assert "50% BT + 50% High-Cd" in proxy_assumption.text
    assert "50% BT + 50% Low-Cd" in proxy_assumption.text
    assert scenario.config["BG_RAW_DISPLAY_START_MASS_T"] == {
        "BG-RAW-1": 100_000.0, "BG-RAW-2": 100_000.0,
    }
    assert scenario.config["BG_RAW_DISPLAY_GRADE_IDS"] == {
        "BG-RAW-1": "RAW-BT",
        "BG-RAW-2": "RAW-BT",
    }
    assert len(
        scenario.config["BG_RAW_DISPLAY_GRADE_COMPONENTS"]["BG-RAW-1"]
    ) == 2
    assert len(
        scenario.config["BG_RAW_DISPLAY_GRADE_COMPONENTS"]["BG-RAW-2"]
    ) == 2
    assert "schematic" in \
        scenario.config["BG_RAW_DISPLAY_GRADE_COMPOSITION_BASIS"]
    assert "BG_RAW_DISPLAY_GRADE_ALTERNATIVES" not in scenario.config
    assert scenario.grade("RAW-BT").quality["BPL"] == 56.06
    assert "CO2" in {p.name for p in scenario.quality_params}
    assert scenario.config["LOU_MINE_102MG_DISPLAY_CAPACITY_T"] == {
        "102MG-SP-01": 100_000.0, "102MG-SP-02": 100_000.0,
    }
    assert scenario.config["LOU_MINE_102MG_DISPLAY_START_MASS_T"] == {
        "102MG-SP-01": 50_000.0, "102MG-SP-02": 50_000.0,
    }
    assert len(
        scenario.config[
            "LOU_MINE_102MG_DISPLAY_GRADE_COMPONENTS"]["102MG-SP-01"]
    ) == 2
    mine_assumption = next(a for a in scenario.assumptions
                           if a.id == "S3-A25")
    assert mine_assumption.status == "ASSUMED"
    assert "display-only" in mine_assumption.text
    assert "494,274 t" in mine_assumption.text


# ---- 4. staged executor solves its loaded scenario ---------------------------
def test_generate_plan_solves_loaded_scenario():
    at = _fresh()
    _inject_fast_s1_solve(at)
    at.run()
    plan = at.session_state["plan"]
    assert plan is not None and plan.status in ("OPTIMAL", "FEASIBLE")
    # every job belongs to the S1 (M17) machine set — proves no S2 leakage
    assert all(j.machine.startswith("M17") for j in plan.jobs)


# ---- 5. solve passes through the expected stages ------------------------------
def test_solve_stage_sequence():
    at = _fresh()
    _inject_fast_s1_solve(at)
    at.run()
    assert at.session_state["solve_stage_log"] == common.SOLVE_STAGES
    assert at.session_state["solving"] is False
    assert at.session_state["solve_request"] is False


# ---- 6. duplicate solve requests are prevented ---------------------------------
def test_duplicate_solve_requests_prevented():
    at = _fresh()
    # simulate a queued request
    at.session_state["solve_request"] = True
    assert common.request_solve.__doc__          # sanity
    # second request must be refused while one is queued/running
    from ui.common import request_solve
    import streamlit as st
    # emulate via the same session-state dict the app uses
    class _SS(dict):
        def get(self, k, d=None):
            return super().get(k, d)
    ss = _SS(solve_request=True)
    orig = st.session_state
    try:
        st.session_state = ss                     # type: ignore
        assert request_solve() is False
        ss["solve_request"] = False
        ss["solving"] = True
        assert request_solve() is False
        ss["solving"] = False
        assert request_solve() is True
    finally:
        st.session_state = orig                   # type: ignore


# ---- 7./8. empty state vs dashboard -------------------------------------------
def test_empty_state_when_no_plan():
    at = _fresh()
    nav_to(at, "1 · Executive Overview")
    sidebar = " ".join(str(m.value) for m in at.sidebar.markdown)
    body = " ".join(
        str(m.value)
        for element_type in ("markdown", "info")
        for m in getattr(at.main, element_type)
    )
    scn = at.session_state["scenario"]
    n_assumed = sum(
        1
        for group in (scn.spaces, scn.machines, scn.links,
                      scn.destinations, scn.grades, scn.sites)
        for item in group
        if item.evidence.status in ("ASSUMED", "TBC")
    )
    n_warnings = sum(
        1 for issue in at.session_state["issues"]
        if issue.severity == "WARNING"
    )
    assert "No plan generated" in body
    assert "left sidebar" in body
    assert "Planning readiness" in sidebar
    if n_warnings:
        suffix = "warning" if n_warnings == 1 else "warnings"
        assert f"Ready with {n_warnings} {suffix}" in sidebar
    else:
        assert "Inputs ready" in sidebar
    assert "Scenario" in sidebar
    assert "Inputs" in sidebar
    assert "Generate" in sidebar
    assert f"{n_assumed} planning notes" in sidebar
    assert "Ready to generate a plan" not in body
    # no dashboard KPI cards before a plan exists
    assert len(at.metric) == 0
    assert not any("Process mimic" in str(m.value)
                   for m in at.markdown)


def test_empty_state_review_action_navigates_to_assumptions():
    at = _fresh()
    nav_to(at, "1 · Executive Overview")
    review = [b for b in at.button if b.label == "Review assumptions"]
    assert len(review) == 1
    review[0].click()
    at.run()
    assert at.sidebar.radio[0].value == "Advanced"
    assert at.sidebar.radio[1].value == "12 · Assumptions & Data Gaps"


def test_empty_state_error_is_blocked_not_ready():
    from mph_sim.validation import Issue

    at = _fresh()
    nav_to(at, "1 · Executive Overview")
    at.session_state["issues"] = [
        Issue("ERROR", "QA_BLOCKER", "test blocking input", "QA")]
    at.run()
    sidebar = " ".join(str(m.value) for m in at.sidebar.markdown)
    assert "Blocked · 1 error" in sidebar
    assert "Resolve input errors before planning" in sidebar
    assert "Ready with" not in sidebar


def test_generate_plan_has_one_primary_location():
    at = _fresh()
    sidebar = [b for b in at.sidebar.button
               if "Generate Plan" in str(b.label)]
    main_generate = [
        b for b in at.main.button
        if "Generate Plan" in str(b.label)
        or "Generate plan" in str(b.label)
    ]
    assert len(sidebar) == 1, \
        "the sidebar must own exactly one shared primary solve action"
    assert len(main_generate) == 0


def test_dashboard_appears_after_plan():
    at = _fresh()
    _inject_fast_s1_solve(at)
    at.run()
    assert at.sidebar.radio[0].value == "Plan"
    assert at.sidebar.radio[1].value == "5 · Demand & Quality"
    nav_to(at, "1 · Executive Overview")
    body = " ".join(str(m.value) for m in at.markdown)
    assert "No plan generated" not in body
    assert "Process mimic" in body
    assert len(at.metric) >= 4                    # concise primary KPI row
    assert [m.label for m in at.metric[:4]] == [
        "Demand satisfaction", "On-spec delivery", "Stock-risk buckets",
        "Routes ≥85%",
    ]


# ---- 9./10./11. navigation ------------------------------------------------------
def test_canonical_routes_and_visible_operator_pages_remain_complete():
    labels = [l for ls in common.NAV_GROUPS.values() for l in ls]
    assert len(labels) == 24
    assert len(set(labels)) == 24
    assert set(labels) == set(common.ALL_PAGE_LABELS)
    assert list(common.NAV_GROUPS) == [
        "Configure", "Plan", "Monitor", "Advanced"]
    assert common.NAV_GROUPS["Configure"] == [
        "2 · Input Data", "3 · Stockyards & Inventory",
        "4 · Routes & Equipment",
        common.CONFIGURE_GRADE_PROFILES_PAGE,
        common.CONFIGURE_WASHING_TREATMENTS_PAGE,
        common.CONFIGURE_MINE_ARRIVALS_PAGE,
    ]
    assert common.NAV_GROUPS["Plan"] == [
        "5 · Demand & Quality",
        common.PLAN_ROUTING_PAGE,
        common.PLAN_CONTINGENCIES_PAGE,
        common.PLAN_BLENDING_PAGE,
    ]
    assert common.NAV_GROUPS["Monitor"] == [
        "1 · Executive Overview", "15 · Global Stockyard Layout & Flow",
        "9 · Job Schedule", common.MONITOR_RESULTS_PAGE,
        "16 · Machine Coverage",
        common.MONITOR_WASHING_TREATMENTS_PAGE,
        "21 · Business Case"]
    assert common.VISIBLE_NAV_GROUPS["Monitor"] == [
        common.MONITOR_LANDING_PAGE,
        common.MONITOR_RESULTS_PAGE,
        common.MONITOR_MACHINE_COVERAGE_PAGE,
        common.MONITOR_WASHING_TREATMENTS_PAGE,
        "21 · Business Case",
    ]
    assert common.VISIBLE_NAV_GROUPS["Configure"] == [
        common.CONFIGURE_LANDING_PAGE,
        common.CONFIGURE_GRADE_PROFILES_PAGE,
        common.CONFIGURE_WASHING_TREATMENTS_PAGE,
        common.CONFIGURE_MINE_ARRIVALS_PAGE,
    ]
    assert common.VISIBLE_NAV_GROUPS["Plan"] == [
        "5 · Demand & Quality", common.PLAN_ROUTING_PAGE,
        common.PLAN_BLENDING_PAGE,
    ]
    assert common.visible_page_label(common.PLAN_CONTINGENCIES_PAGE) == \
        common.PLAN_ROUTING_PAGE
    assert common.VISIBLE_NAV_GROUPS["Advanced"] == \
        common.NAV_GROUPS["Advanced"]
    assert set(common.NAV_GROUPS["Advanced"]) == {
        "6 · Planning Workspace",
        "7 · Simulation Control",
        "10 · Exceptions & Replanning",
        "11 · Three-SMS Integration Console",
        "12 · Assumptions & Data Gaps", "13 · Scenario Comparison",
        "14 · Audit & Export"}


def test_operator_page_titles_are_clear_unique_and_unnumbered():
    titles = [common.page_title(label) for label in common.ALL_PAGE_LABELS]
    assert len(titles) == len(set(titles))
    assert all(not title[0].isdigit() for title in titles)
    assert common.PAGE_TO_GROUP["9 · Job Schedule"] == "Monitor"
    assert common.PAGE_TO_GROUP[common.MONITOR_RESULTS_PAGE] == "Monitor"
    assert common.page_title("9 · Job Schedule") == "Jobs & Schedule"


def test_monitor_page_titles_match_the_operator_workspaces():
    assert common.page_title(common.MONITOR_LANDING_PAGE) == \
        "KPI flow diagram and Job scheduling"
    assert common.page_title(common.MONITOR_MACHINE_COVERAGE_PAGE) == \
        "Machine coverage"
    assert common.page_title(common.MONITOR_RESULTS_PAGE) == "Plan results"
    assert common.page_title(common.MONITOR_WASHING_TREATMENTS_PAGE) == \
        "Treatment monitoring"
    assert common.page_title(common.CONFIGURE_WASHING_TREATMENTS_PAGE) == \
        "Washing treatments"


def test_configure_legacy_aliases_and_grade_profiles_resolve_independently():
    assert common.page_title(common.CONFIGURE_LANDING_PAGE) == \
        "Operational configuration"
    for canonical in (
            "2 · Input Data",
            "3 · Stockyards & Inventory",
            "4 · Routes & Equipment"):
        assert common.visible_page_label(canonical) == \
            common.CONFIGURE_LANDING_PAGE
    assert common.visible_page_label(
        common.CONFIGURE_GRADE_PROFILES_PAGE
    ) == common.CONFIGURE_GRADE_PROFILES_PAGE
    assert common.visible_page_label(
        common.CONFIGURE_WASHING_TREATMENTS_PAGE
    ) == common.CONFIGURE_WASHING_TREATMENTS_PAGE
    assert common.visible_page_label(
        common.LEGACY_CONFIGURE_GRADE_YIELD_PAGE
    ) == common.CONFIGURE_GRADE_PROFILES_PAGE


def test_no_plan_lands_on_plan_with_operator_facing_titles():
    at = _fresh()
    assert at.sidebar.radio[0].options == [
        "Configure", "Plan", "Monitor", "Advanced"]
    assert at.sidebar.radio[0].value == "Plan"
    assert at.sidebar.radio[1].value == "5 · Demand & Quality"
    assert at.sidebar.radio[1].options == [
        "Demand planning", "Routing availability", "Blending",
    ]


def test_blending_page_uses_three_stage_matrix_catalogues():
    at = _fresh()
    nav_to(at, common.PLAN_BLENDING_PAGE)
    assert not at.exception, at.exception

    [stage] = [control for control in at.main.segmented_control
               if control.label == "Blending stage"]
    assert stage.options == [
        "Pre-washing stockpiles",
        "MPH humid-phosphate stockpiles",
        "Pre-drying",
    ]
    assert stage.value == "Pre-washing stockpiles"
    _select_blend_stage(at, "MPH humid-phosphate stockpiles")
    assert any("MPH-HUMID-01" in str(frame.value)
               for frame in at.main.dataframe)
    mph_text = _visible_text(at.main)
    assert "Blending Policy" in mph_text
    assert "Blending Matrices" in mph_text
    assert "Stockpile-specific blending enablement" not in mph_text
    assert "Stockpile enablement" not in mph_text
    assert any(button.label == "Apply blending policy"
               for button in at.main.button)
    assert any(button.label == "Apply blending matrices"
               for button in at.main.button)
    frame_columns = [list(frame.value.columns) for frame in at.main.dataframe]
    policy_table = [
        "Enable blending", "Stockpile", "Space ID", "Inventory [t]",
        "Capacity [t]", "Campaign state", "Selected by latest plan"]
    catalog = at.session_state["scenario"].config[
        "CHEVRON_STACKING"]["matrix_catalog"]
    grade_columns = list(dict.fromkeys(
        f"{component['grade_id']} [%]"
        for entry in catalog for component in entry["components"]))
    matrix_table = ["Matrix ID", *grade_columns]
    assert policy_table in frame_columns
    assert matrix_table in frame_columns
    assert frame_columns.index(policy_table) < frame_columns.index(matrix_table)
    [matrix_editor] = [frame for frame in at.main.dataframe
                       if list(frame.value.columns) == matrix_table]
    assert len(matrix_editor.value) == len(catalog) == 2
    assert "Matrix name" not in matrix_editor.value.columns
    assert list(matrix_editor.value["Matrix ID"]) == [
        entry["id"] for entry in catalog]
    assert {component["grade_id"] for component in catalog[0]["components"]} \
        != {component["grade_id"] for component in catalog[1]["components"]}
    assert matrix_editor.value.iloc[0][
        ["Matrix ID", "TBT [%]", "BT-LOW-CD [%]"]].to_dict() == {
        "Matrix ID": "MPH-HUMID-01",
        "TBT [%]": 10.0,
        "BT-LOW-CD [%]": 90.0,
    }
    for row_index, entry in enumerate(catalog):
        for component in entry["components"]:
            assert matrix_editor.value.loc[
                row_index, f"{component['grade_id']} [%]"] == pytest.approx(
                    component["target_fraction"] * 100.0)

    _select_blend_stage(at, "Pre-washing stockpiles")
    assert not at.exception, at.exception
    assert "feed-blend campaign" in _visible_text(at.main)
    [location] = [control for control in at.main.segmented_control
                  if control.label == "Pre-washing location"]
    assert location.options == [
        "Louta pre-washing", "Benguerir pre-washing"]
    assert location.value == "Louta pre-washing"
    [share_mode] = [control for control in at.main.radio
                    if control.label == "Share selection mode"]
    assert share_mode.options == ["Blend matrix", "Optimized shares"]
    assert share_mode.value == "Optimized shares"
    [bounds] = [
        frame for frame in at.main.dataframe
        if list(frame.value.columns) == [
            "Source stockpile", "Input grade", "Minimum mass [%]",
            "Maximum mass [%]"]
    ]
    assert set(bounds.value["Minimum mass [%]"]) == {5.0}
    assert set(bounds.value["Maximum mass [%]"]) == {95.0}
    [matrix_editor] = [
        frame for frame in at.main.dataframe
        if list(frame.value.columns) == [
            "Matrix ID", "RAW-BT @ 110LG-OL1 [%]",
            "RAW-BT-LOW-CD @ 110LG-OL2 [%]"]
    ]
    assert matrix_editor.value.empty
    page_text = _visible_text(at.main)
    assert "Â" not in page_text
    assert "â" not in page_text
    assert "Blending Policy" in page_text
    assert "Blending Matrices" in page_text
    assert "common reclaim header" in page_text
    [policy_editor] = [
        frame for frame in at.main.dataframe
        if list(frame.value.columns) == [
            "Enable blending", "Stockpile", "Space ID", "Input grade",
            "Inventory [t]", "Capacity [t]",
            "Planned input [t]"]
    ]
    assert list(policy_editor.value["Space ID"]) == [
        "110LG-OL1", "110LG-OL2"]
    assert list(policy_editor.value["Enable blending"]) == [True, True]
    assert "Enable blending on these washing lines" not in {
        control.label for control in at.main.multiselect}
    frame_columns = [list(frame.value.columns) for frame in at.main.dataframe]
    assert frame_columns.index(list(policy_editor.value.columns)) < \
        frame_columns.index(list(matrix_editor.value.columns))
    [toggle] = [control for control in at.main.toggle
                if control.label == "Enable pre-washing feed blending"]
    assert toggle.value is True

    location.set_value("Benguerir pre-washing")
    at.run()
    assert not at.exception, at.exception
    [matrix_editor] = [
        frame for frame in at.main.dataframe
        if list(frame.value.columns) == [
            "Matrix ID", "RAW-TBT @ BG-RAW-1 [%]",
            "RAW-BT-LOW-CD @ BG-RAW-2 [%]"]
    ]
    assert matrix_editor.value.empty

    [stage] = [control for control in at.main.segmented_control
               if control.label == "Blending stage"]
    stage.set_value("Pre-drying")
    at.run()
    assert not at.exception, at.exception
    assert "Pre-drying blending is not configured in the current model" \
        in _visible_text(at.main)


def test_mph_blending_policy_saves_quantity_floor_without_fixed_campaign_inputs():
    at = _fresh()
    nav_to(at, common.PLAN_BLENDING_PAGE)
    _select_blend_stage(at, "MPH humid-phosphate stockpiles")
    before = deepcopy(at.session_state["scenario"].config["CHEVRON_STACKING"])
    [minimum] = [control for control in at.main.number_input
                 if control.label == "Minimum quantity per input grade [t]"]
    assert minimum.value == 500.0
    assert not ({"Campaign batch [t]", "Reclaim ready hour", "Chevron passes",
                 "Minimum non-zero selected component [%]"}
                & {control.label for control in at.main.number_input})
    assert "Resulting grade" not in {
        control.label for control in at.main.selectbox}

    minimum.set_value(750.0)
    [apply_button] = [button for button in at.main.button
                      if button.label == "Apply blending policy"]
    apply_button.click().run()
    assert not at.exception, at.exception
    assert not at.main.error, at.main.error
    updated = at.session_state["scenario"].config["CHEVRON_STACKING"]
    assert updated["quantity_mode"] == "DEMAND_DRIVEN"
    assert updated["min_input_grade_t"] == 750.0
    assert updated["matrix_catalog"] == before["matrix_catalog"]
    assert updated["enabled_spaces"] == before["enabled_spaces"]
    assert updated.get("campaign_states_by_space", {}) == before.get(
        "campaign_states_by_space", {})
    assert not {"global_batch_t", "global_ready_minute", "global_result_grade",
                "global_minimum_component_fraction", "passes_per_cycle"} & updated.keys()

    [mode] = [control for control in at.main.radio
              if control.label == "Share selection mode"]
    mode.set_value("Optimized shares").run()
    [apply_button] = [button for button in at.main.button
                      if button.label == "Apply blending policy"]
    apply_button.click().run()
    assert not at.exception, at.exception
    assert not at.main.error, at.main.error
    updated = at.session_state["scenario"].config["CHEVRON_STACKING"]
    assert updated["global_mode"] == "OPTIMIZED"
    assert updated["min_input_grade_t"] == 750.0
    assert updated["matrix_catalog"] == before["matrix_catalog"]


def test_prewashing_location_exposes_safe_fixed_and_optimized_share_modes():
    at = _fresh()
    nav_to(at, common.PLAN_BLENDING_PAGE)
    [stage] = [control for control in at.main.segmented_control
               if control.label == "Blending stage"]
    stage.set_value("Pre-washing stockpiles")
    at.run()

    [share_mode] = [control for control in at.main.radio
                    if control.label == "Share selection mode"]
    share_mode.set_value("Blend matrix")
    at.run()
    [matrix_editor] = [
        frame for frame in at.main.dataframe
        if list(frame.value.columns) == [
            "Matrix ID", "RAW-BT @ 110LG-OL1 [%]",
            "RAW-BT-LOW-CD @ 110LG-OL2 [%]"]
    ]
    assert matrix_editor.value.empty
    [apply_button] = [button for button in at.main.button
                      if button.label == "Apply blending policy"]
    apply_button.click().run()
    assert not at.exception, at.exception
    cfg = at.session_state["scenario"].config["PRE_WASHING_BLENDING"]
    louta = cfg["points"]["LOU-PREWASH"]
    assert louta["share_mode"] == "OPTIMIZED"
    assert any("matrix mode requires at least one catalogue matrix"
               in str(error.value).lower()
               for error in at.main.error)

    # A valid persisted matrix renders as the active policy. Numerical
    # enforcement is independently covered by test_prewash_blending.py.
    louta["share_mode"] = "MATRIX"
    louta["matrix_catalog"] = [{
        "id": "LOU-M1", "name": "Louta matrix 1", "components": [
            {**component, "target_fraction": share}
            for component, share in zip(
                louta["optimized_components"], (.4, .6))]}]
    at.run()
    assert prewash_blending.configuration_errors(
        at.session_state["scenario"]) == []
    [matrix_editor] = [
        frame for frame in at.main.dataframe
        if list(frame.value.columns) == [
            "Matrix ID", "RAW-BT @ 110LG-OL1 [%]",
            "RAW-BT-LOW-CD @ 110LG-OL2 [%]"]
    ]
    assert matrix_editor.value.to_dict("records") == [{
        "Matrix ID": "LOU-M1",
        "RAW-BT @ 110LG-OL1 [%]": 40.0,
        "RAW-BT-LOW-CD @ 110LG-OL2 [%]": 60.0,
    }]


@pytest.mark.parametrize("tbt_percent", [10.0, 20.0])
def test_mph_blending_matrices_apply_preserves_metadata_and_saves_percentages(
        tbt_percent):
    at = _fresh()
    nav_to(at, common.PLAN_BLENDING_PAGE)
    _select_blend_stage(at, "MPH humid-phosphate stockpiles")
    before = deepcopy(at.session_state["scenario"].config[
        "CHEVRON_STACKING"]["matrix_catalog"])
    if tbt_percent != 10.0:
        at.session_state["mph_shared_matrix_catalog_wide"] = {
            "edited_rows": {0: {
                "TBT [%]": tbt_percent,
                "BT-LOW-CD [%]": 100.0 - tbt_percent,
            }},
            "added_rows": [], "deleted_rows": [],
        }
    expected = deepcopy(before)
    expected[0]["components"][0]["target_fraction"] = tbt_percent / 100.0
    expected[0]["components"][1]["target_fraction"] = (
        100.0 - tbt_percent) / 100.0

    [apply_button] = [button for button in at.main.button
                      if button.label == "Apply blending matrices"]
    apply_button.click().run()

    assert not at.exception, at.exception
    assert not at.main.error, at.main.error
    assert at.session_state["scenario"].config[
        "CHEVRON_STACKING"]["matrix_catalog"] == expected


def test_mph_blending_matrices_reject_invalid_total_without_changing_catalogue(
        monkeypatch):
    from ui import pages_data

    original_editor = pages_data.st.data_editor

    def invalid_matrix_editor(*args, **kwargs):
        frame = original_editor(*args, **kwargs)
        if str(kwargs.get("key", "")).startswith("mph_shared_matrix_catalog"):
            frame = frame.copy()
            frame.loc[0, "TBT [%]"] = 20.0
        return frame

    monkeypatch.setattr(pages_data.st, "data_editor", invalid_matrix_editor)
    at = _fresh()
    nav_to(at, common.PLAN_BLENDING_PAGE)
    _select_blend_stage(at, "MPH humid-phosphate stockpiles")
    before = deepcopy(at.session_state["scenario"].config["CHEVRON_STACKING"])

    [apply_button] = [button for button in at.main.button
                      if button.label == "Apply blending matrices"]
    apply_button.click().run()

    assert not at.exception, at.exception
    assert any("100" in str(error.value) for error in at.main.error)
    assert at.session_state["scenario"].config["CHEVRON_STACKING"] == before


def test_every_page_reachable_via_grouped_nav():
    at = _fresh()
    for label in common.ALL_PAGE_LABELS:
        nav_to(at, label)
        assert not at.exception, f"{label}: {at.exception}"
        assert at.sidebar.radio[1].value == \
            common.visible_page_label(label)


def test_query_param_mapping():
    assert common.page_label_from_qp("7") == "7 · Simulation Control"
    assert common.page_label_from_qp("14") == "14 · Audit & Export"
    assert common.page_label_from_qp("15") == "15 · Global Stockyard Layout & Flow"
    assert common.page_label_from_qp("16") == "16 · Machine Coverage"
    assert common.page_label_from_qp("17") == \
        common.CONFIGURE_GRADE_PROFILES_PAGE
    assert common.page_label_from_qp("18") == common.PLAN_ROUTING_PAGE
    assert common.page_label_from_qp("19") == common.PLAN_CONTINGENCIES_PAGE
    assert common.page_label_from_qp("20") == common.PLAN_BLENDING_PAGE
    assert common.page_label_from_qp("22") == \
        common.MONITOR_WASHING_TREATMENTS_PAGE
    assert common.page_label_from_qp("23") == \
        common.CONFIGURE_WASHING_TREATMENTS_PAGE
    assert common.page_label_from_qp("99") == \
        common.CONFIGURE_MINE_ARRIVALS_PAGE  # clamped
    assert common.page_label_from_qp("0") == "1 · Executive Overview"
    assert common.page_label_from_qp("junk") is None
    assert common.page_label_from_qp(None) is None
    for lbl in common.ALL_PAGE_LABELS:
        assert common.PAGE_TO_GROUP[lbl] in common.NAV_GROUPS


# ---- 13. no truncated KPI formatting -------------------------------------------
def test_fmt_t_safe_compact():
    assert theme.fmt_t(None) == "—"
    assert theme.fmt_t(0) == "0 t"
    assert theme.fmt_t(9_999) == "9,999 t"
    assert theme.fmt_t(23_000) == "23.0 kt"
    assert theme.fmt_t(416_100) == "416.1 kt"
    assert theme.fmt_t(2_400_000) == "2.40 Mt"
    for v in (0, 999, 23_000, 416_100, 39_600_000):
        assert len(theme.fmt_t(v)) <= 10, "must fit a KPI card untruncated"


def test_overview_uses_compact_formatting():
    src = (ROOT / "ui" / "pages_overview.py").read_text(encoding="utf-8")
    # the old truncating combined metric must be gone
    assert '"Demand vs planned delivery [t]"' not in src
    assert "fmt_t" in src
    assert "theme.mini_kpis([" not in src, \
        "executive overview must not restore the redundant secondary KPI strip"


# ---- 12./14. topology + determinism covered elsewhere; keep a cheap guard ------
def test_topology_and_signature_guards_still_present():
    src = (ROOT / "tests" / "test_polish.py").read_text(encoding="utf-8")
    assert "853415c7f5070d37" in src              # compact exact operational-objective S3 signature
    src2 = (ROOT / "tests" / "test_revd_alignment.py").read_text(encoding="utf-8")
    assert "test_chain_mode_per_scenario" in src2
