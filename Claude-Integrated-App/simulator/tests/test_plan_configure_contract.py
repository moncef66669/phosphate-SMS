"""Acceptance contract for the operator-facing Plan workspace.

The UI deliberately has one commissioned planning scenario today:

* Plan contains demand, routing and planned-contingency pages (canonical deep
  links ``?page=5``, ``?page=18`` and ``?page=19``).
* S0 is a UI alias for the existing internal S3 three-site dataset.
* One shared scenario selector sits above every Plan page.
* The demand editors remain on the demand page.
* The only Generate Plan action lives in the sidebar.
* Detailed planning controls retain page 6 under Advanced.
* Grade characteristics remain in Configure, outside Plan.

The navigation/solve-state test substitutes a tiny valid model under the S0
identity.  This exercises the real sidebar solve flow without repeatedly
running the comparatively expensive S3 optimization.  A separate assertion
pins S0 to the real three-site dataset.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

from conftest import nav_to  # noqa: E402
from mph_sim import datasets  # noqa: E402
from mph_sim.domain import PlanResult  # noqa: E402
from mph_sim.validation import validate_scenario  # noqa: E402
from ui import common  # noqa: E402
from ui.pages_data import (_demand_quantity_t,  # noqa: E402
                           _validate_louta_s0_policy)


APP = str(ROOT / "app.py")


def _fresh() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert not at.exception, at.exception
    return at


def _numbered_page(group: str, number: int) -> str:
    return next(
        label
        for label in common.NAV_GROUPS[group]
        if label.split(" ", 1)[0] == str(number)
    )


def _visible_text(root) -> str:
    values: list[str] = []
    for element_type in (
        "subheader",
        "header",
        "markdown",
        "caption",
        "info",
        "success",
        "warning",
        "error",
    ):
        for element in getattr(root, element_type):
            values.append(str(element.value))
    for status in root.status:
        values.append(str(status.label))
    return "\n".join(values)


def _frame_column_sets(root) -> list[set[str]]:
    return [set(map(str, frame.value.columns)) for frame in root.dataframe]


def _generate_buttons(root):
    return [
        button
        for button in root.button
        if "Generate Plan" in str(button.label)
    ]


def _install_fast_model_under_s0(at: AppTest, monkeypatch) -> None:
    """Replace only the test's model payload, preserving the public S0 ID."""
    scenario = datasets.sulphur_minimal()
    scenario.name = datasets.S3_SCENARIO_NAME

    # A deliberately renamed test payload is not a built-in S3 object and must
    # not pass through the production built-in migration during AppTest reruns.
    monkeypatch.setattr(datasets, "migrate_loaded_builtin", lambda _scenario: "")

    at.session_state["scenario_name"] = datasets.S3_SCENARIO_NAME
    at.session_state["scenario"] = scenario
    at.session_state["plan"] = None
    at.session_state["sim"] = None
    at.session_state["issues"] = validate_scenario(scenario)
    at.session_state["solve_request"] = False
    at.session_state["solving"] = False
    at.session_state["planning_scenario_code"] = "S0"
    at.session_state["nav_section"] = "Plan"
    at.session_state["nav_page_Plan"] = common.PLAN_LANDING_PAGE
    at.run()
    assert not at.exception, at.exception


def test_navigation_contract_has_blending_page_and_page_six_is_advanced():
    demand_page = _numbered_page("Plan", 5)
    settings_page = _numbered_page("Advanced", 6)

    assert common.NAV_GROUPS["Plan"] == [
        demand_page,
        common.PLAN_ROUTING_PAGE,
        common.PLAN_CONTINGENCIES_PAGE,
        common.PLAN_BLENDING_PAGE,
    ]
    assert settings_page not in common.NAV_GROUPS["Plan"]
    assert common.page_title(demand_page) == "Demand planning"
    assert common.page_title(common.PLAN_ROUTING_PAGE) == \
        "Routing availability"
    assert common.page_title(common.PLAN_CONTINGENCIES_PAGE) == \
        "Planned contingencies"
    assert common.page_title(common.PLAN_BLENDING_PAGE) == "Blending"
    assert common.page_title(settings_page) == "Planning settings"
    assert common.PLAN_LANDING_PAGE == demand_page


def test_s0_maps_to_the_real_s3_three_site_model():
    assert common.PLANNING_SCENARIOS == {
        "S0": datasets.S3_SCENARIO_NAME,
    }
    assert common.planning_scenario_dataset("S0") == \
        datasets.S3_SCENARIO_NAME
    assert common.planning_scenario_code(datasets.S3_SCENARIO_NAME) == "S0"

    scenario = datasets.get_scenario(
        common.planning_scenario_dataset("S0"))
    assert scenario.name == datasets.S3_SCENARIO_NAME
    assert {(site.id, site.role) for site in scenario.sites} == {
        ("BENGUERIR", "mine"),
        ("LOUTA", "mine"),
        ("MPH", "hub"),
    }


def test_demand_page_has_one_shared_scenario_selector_and_demand_editors():
    at = _fresh()
    s0_page = _numbered_page("Plan", 5)
    nav_to(at, s0_page)

    assert len(at.sidebar.selectbox) == 1
    assert at.sidebar.selectbox[0].label == "Planning scenario"
    assert at.sidebar.selectbox[0].value == "S0"
    selector = next(item for item in at.main.selectbox
                    if item.label == "Planning scenario")
    assert selector.label == "Planning scenario"
    assert selector.options == ["S0 \u2014 BG + LO + MPH"]
    assert selector.value == "S0"
    demand_selector = next(item for item in at.main.selectbox
                           if item.label == "Demand preset")
    assert demand_selector.label == "Demand preset"
    assert demand_selector.options == [
        "Balanced production", "Benguerir train campaign",
        "Full-flow all-machine demonstration", "Blending showcase"]
    assert demand_selector.value == "FULL_FLOW"
    assert at.session_state["scenario_name"] == datasets.S3_SCENARIO_NAME
    scenario = at.session_state["scenario"]
    assert scenario.config["ACTIVE_PLANNING_DEMAND_PROFILE"] == \
        "FULL_FLOW"
    assert "HP-07" in scenario.config["CHEVRON_STACKING"]["enabled_spaces"]
    editor_keys = {frame.key for frame in at.main.dataframe}
    assert {"weekly_demand_editor", "demand_editor"} <= editor_keys
    assert "Configure · Louta stocked-route policy" not in _visible_text(at.main)


def test_each_plan_page_has_one_shared_scenario_selector_at_the_top():
    at = _fresh()
    for page in common.NAV_GROUPS["Plan"]:
        nav_to(at, page)
        selectors = [
            selector for selector in at.main.selectbox
            if selector.label == "Planning scenario"
        ]
        assert len(selectors) == 1, page
        assert selectors[0].value == "S0"
        envelope_blocks = [
            item for item in at.main.markdown
            if "Active planning envelope" in str(item.value)
        ]
        assert len(envelope_blocks) == 1, page


def test_operator_can_clone_select_and_configure_a_custom_scenario():
    at = _fresh()
    nav_to(at, common.PLAN_LANDING_PAGE)

    name = next(item for item in at.main.text_input
                if item.label == "Scenario name")
    name.set_value("High demand week")
    create = next(item for item in at.main.button
                  if item.label == "Create from active")
    create.click().run()

    assert not at.exception, at.exception
    assert at.session_state["planning_scenario_code"] == "S1"
    assert at.session_state["scenario_name"] == "S1 — High demand week"
    selector = next(item for item in at.main.selectbox
                    if item.label == "Planning scenario")
    assert selector.options == [
        "S0 — BG + LO + MPH", "S1 — High demand week"]
    assert selector.value == "S1"
    sidebar_selector = at.sidebar.selectbox[0]
    assert sidebar_selector.options == [
        "S0 · BG + LO + MPH", "S1 · High demand week · no plan yet"]
    assert sidebar_selector.value == "S1"
    assert next(
        item for item in at.sidebar.button
        if "Generate Plan" in str(item.label)).disabled is False

    custom = at.session_state["scenario"]
    custom.config["TEST_CUSTOM_SETTING"] = "retained"
    sidebar_selector.set_value("S0").run()
    assert at.session_state["scenario_name"] == datasets.S3_SCENARIO_NAME
    assert next(item for item in at.main.selectbox
                if item.label == "Planning scenario").value == "S0"
    sidebar_selector = at.sidebar.selectbox[0]
    sidebar_selector.set_value("S1").run()
    assert at.session_state["scenario"].config["TEST_CUSTOM_SETTING"] == \
        "retained"
    assert next(item for item in at.main.selectbox
                if item.label == "Planning scenario").value == "S1"

    configure = next(item for item in at.main.button
                     if item.label == "Configure active")
    configure.click().run()
    assert at.sidebar.radio[0].value == "Configure"
    assert at.sidebar.radio[1].value == common.CONFIGURE_LANDING_PAGE


def test_generate_plan_action_exists_only_in_sidebar():
    at = _fresh()
    for group, number in (("Plan", 5), ("Advanced", 6)):
        nav_to(at, _numbered_page(group, number))
        assert not at.exception, at.exception
        assert not _generate_buttons(at.main)
        sidebar_actions = _generate_buttons(at.sidebar)
        assert len(sidebar_actions) == 1
        assert sidebar_actions[0].disabled is False


def test_s0_excludes_grade_characteristics_and_solver_settings():
    at = _fresh()
    nav_to(at, _numbered_page("Plan", 5))

    body = _visible_text(at.main)
    assert "Demand plan by receiving unit" in body
    forbidden_copy = (
        "Quality specifications per destination",
        "Quality parameters registered",
        "Mine versus washed-phosphate material profiles",
        "Why does a pile usually show one quality?",
        "Hard / soft constraint configuration",
        "Max solve budget",
        "Frozen horizon",
    )
    for marker in forbidden_copy:
        assert marker not in body, f"configuration leaked into S0: {marker}"

    forbidden_schemas = (
        {"id", "name", "quality", "material_class"},
        {"id", "stage", "quality_ranges"},
        {"name", "unit", "description"},
        {"destination", "param", "min", "max", "hard"},
    )
    for columns in _frame_column_sets(at.main):
        assert not any(schema <= columns for schema in forbidden_schemas), (
            f"grade-characteristic table leaked into S0: {sorted(columns)}"
        )


def test_successful_sidebar_solve_opens_combined_monitor(monkeypatch):
    at = _fresh()
    _install_fast_model_under_s0(at, monkeypatch)

    sidebar_actions = _generate_buttons(at.sidebar)
    assert len(sidebar_actions) == 1
    assert sidebar_actions[0].disabled is False
    sidebar_actions[0].click()
    at.run()

    assert not at.exception, at.exception
    plan = at.session_state["plan"]
    assert plan is not None
    assert plan.status in ("OPTIMAL", "FEASIBLE")
    assert at.session_state["solve_request"] is False
    assert at.session_state["solving"] is False
    assert at.sidebar.radio[0].value == "Monitor"
    assert at.sidebar.radio[1].value == common.MONITOR_LANDING_PAGE
    visible = _visible_text(at.main)
    assert "KPIs" in visible
    assert "Global Stockyard Layout & Flow" in visible
    assert "Job Schedule" in visible


def test_committed_demand_change_invalidates_plan_and_simulation():
    at = _fresh()
    nav_to(at, _numbered_page("Plan", 5))
    at.session_state["plan"] = PlanResult(
        status="FEASIBLE", solver_wall_s=1.0)
    at.session_state["sim"] = SimpleNamespace(
        bucket=0, T=168, replans=0)
    at.run()
    assert at.session_state["plan"] is not None
    assert at.session_state["sim"] is not None

    apply_demand = [
        button for button in at.main.button
        if button.label == "Apply weekly demand"
    ]
    assert len(apply_demand) == 1
    apply_demand[0].click()
    at.run()

    assert at.session_state["plan"] is None
    assert at.session_state["sim"] is None
    assert "previous plan cleared" in _visible_text(at.main)


def test_pap2_future_switch_retains_template_and_invalidates_outputs():
    at = _fresh()
    nav_to(at, _numbered_page("Configure", 2))
    site = next(radio for radio in at.main.radio if radio.label == "Site")
    site.set_value("MPH")
    at.run()

    scenario = at.session_state["scenario"]
    demand_before = _demand_quantity_t(scenario, "PAP-2")
    assert demand_before == pytest.approx(252_000)
    assert sum(
        scenario.demand_tph("PAP-2", bucket) * scenario.sim.dt_h
        for bucket in range(scenario.sim.horizon_buckets)
    ) == pytest.approx(0)
    at.session_state["plan"] = PlanResult(
        status="FEASIBLE", solver_wall_s=1.0)
    at.session_state["sim"] = SimpleNamespace(
        bucket=0, T=168, replans=0)
    at.run()

    pap2_state = next(
        selector for selector in at.main.selectbox
        if selector.label == "PAP-2"
    )
    assert pap2_state.value == "Future"
    pap2_state.set_value("Available")
    at.run()
    assert at.session_state["plan"] is not None

    apply_configuration = next(
        button for button in at.main.button
        if button.label == "Apply MPH configuration"
    )
    apply_configuration.click()
    at.run()

    assert not at.exception, at.exception
    scenario = at.session_state["scenario"]
    assert all(
        scenario.link(link_id).available
        and scenario.link(link_id).lifecycle == "SELECTED"
        for link_id in ("L-RTE-A-PAP2", "L-RTE-B-PAP2")
    )
    assert _demand_quantity_t(scenario, "PAP-2") == \
        pytest.approx(demand_before)
    assert sum(
        scenario.demand_tph("PAP-2", bucket) * scenario.sim.dt_h
        for bucket in range(scenario.sim.horizon_buckets)
    ) == pytest.approx(demand_before)
    assert at.session_state["plan"] is None
    assert at.session_state["sim"] is None
    assert "previous plan cleared" in _visible_text(at.main)


def test_full_flow_profile_syncs_demands_and_invalidates_outputs():
    at = _fresh()
    nav_to(at, _numbered_page("Plan", 5))
    at.session_state["plan"] = PlanResult(
        status="FEASIBLE", solver_wall_s=1.0)
    at.session_state["sim"] = SimpleNamespace(bucket=0, T=168, replans=0)
    at.run()

    demand_selector = next(
        selector for selector in at.main.selectbox
        if selector.label == "Demand preset"
    )
    assert demand_selector.options == [
        "Balanced production", "Benguerir train campaign",
        "Full-flow all-machine demonstration", "Blending showcase"]
    apply_profile = [
        button for button in at.main.button
        if button.label == "Load preset"
    ]
    assert len(apply_profile) == 1
    apply_profile[0].click()
    at.run()

    assert not at.exception, at.exception
    assert at.session_state["plan"] is None
    assert at.session_state["sim"] is None
    scenario = at.session_state["scenario"]
    assert scenario.config["ACTIVE_PLANNING_DEMAND_PROFILE"] == \
        "FULL_FLOW"
    assert scenario.destination("BG-TRAIN-PROD").demand_default_tph == \
        pytest.approx(1_000)
    assert scenario.config["CORRIDOR_DISPATCH_TARGET_T"]["C2"] == \
        pytest.approx(74_200)
    assert "previous plan cleared" in _visible_text(at.main)


def test_louta_s0_policy_validation_rejects_meaningless_or_invalid_values():
    valid_targets = {"LOU-WASH-L1": 500.0, "LOU-WASH-L2": 500.0}
    _validate_louta_s0_policy(
        True, 1_000.0, valid_targets, 30.0, 24 * 60.0)

    with pytest.raises(ValueError, match="positive C1"):
        _validate_louta_s0_policy(
            True, 0.0, valid_targets, 30.0, 24 * 60.0)
    with pytest.raises(ValueError, match="positive washing"):
        _validate_louta_s0_policy(
            True, 1_000.0,
            {"LOU-WASH-L1": 0.0, "LOU-WASH-L2": 0.0},
            30.0, 24 * 60.0)
    with pytest.raises(ValueError, match="must equal"):
        _validate_louta_s0_policy(
            True, 1_100.0, valid_targets, 30.0, 24 * 60.0)
    with pytest.raises(ValueError, match="cannot be negative"):
        _validate_louta_s0_policy(
            False, -1.0, valid_targets, 30.0, 24 * 60.0)
    with pytest.raises(ValueError, match="planning horizon"):
        _validate_louta_s0_policy(
            False, 0.0, valid_targets, 24 * 60.0 + 1, 24 * 60.0)


def test_committed_louta_s0_policy_syncs_solver_keys_and_invalidates_outputs():
    at = _fresh()
    nav_to(at, _numbered_page("Plan", 5))
    at.session_state["plan"] = PlanResult(
        status="FEASIBLE", solver_wall_s=1.0)
    at.session_state["sim"] = SimpleNamespace(
        bucket=0, T=168, replans=0)
    at.run()

    apply_policy = [
        button for button in at.main.button
        if button.label == "Apply Louta S0 policy"
    ]
    assert len(apply_policy) == 1
    apply_policy[0].click()
    at.run()

    assert not at.exception, at.exception
    assert at.session_state["plan"] is None
    assert at.session_state["sim"] is None
    cfg = at.session_state["scenario"].config
    assert cfg["CORRIDOR_DISPATCH_TARGET_T"]["C1"] == \
        cfg["LOU_C1_STOCKED_ROUTE_TARGET_T"]
    for stage, target in cfg["LOU_WASH_LINE_OUTPUT_TARGET_T"].items():
        assert cfg["PROCESS_STAGE_OUTPUT_TARGET_T"][stage] == target
    assert cfg["WASHING_LINE_RESIDENCE_TIME_H"]["LOUTA"] == \
        pytest.approx(cfg["LOU_WASHING_RESIDENCE_MIN"] / 60.0)
    assert "previous plan cleared" in _visible_text(at.main)
