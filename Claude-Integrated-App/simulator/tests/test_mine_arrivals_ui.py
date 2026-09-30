"""Operator contracts for dated mine supply, atomic Apply and persistence."""
from copy import deepcopy
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from conftest import nav_to
from mph_sim import datasets, mine_arrivals, washing
from mph_sim.domain import PlanResult
from ui import common
from ui.pages_mine_arrivals import (
    DAY_COLUMN, DATE_COLUMN, TOTAL_COLUMN, arrival_frame,
    configuration_from_frames,
)


APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture(autouse=True)
def isolated_preferences(monkeypatch, tmp_path):
    path = tmp_path / "mine_arrivals.json"
    monkeypatch.setenv("MPH_MINE_ARRIVALS_PATH", str(path))
    return path


def fresh_page():
    at = AppTest.from_file(APP, default_timeout=300).run()
    assert not at.exception, at.exception
    nav_to(at, common.CONFIGURE_MINE_ARRIVALS_PAGE)
    assert not at.exception, at.exception
    return at


def editors(at):
    return [frame for frame in at.main.dataframe
            if str(frame.key).startswith("mine_arrivals_")]


def apply_button(at):
    return next(button for button in at.main.button
                if button.label == "Apply mine arrivals")


def test_dedicated_page_has_two_numeric_daily_tables_and_source_information():
    at = fresh_page()
    assert at.sidebar.radio[0].value == "Configure"
    assert at.sidebar.radio[1].value == common.CONFIGURE_MINE_ARRIVALS_PAGE
    assert "Mine arrivals" in at.sidebar.radio[1].options
    tables = editors(at)
    assert len(tables) == 2
    grades = mine_arrivals.raw_grades(at.session_state["scenario"])
    for table in tables:
        assert list(table.value.columns) == [
            DAY_COLUMN, DATE_COLUMN, *grades, TOTAL_COLUMN]
        assert list(table.value[DAY_COLUMN]) == list(range(1, 8))
        assert table.value[DATE_COLUMN].nunique() == 7
        assert all(table.value[grade].dtype.kind == "f" for grade in grades)
        assert table.value[TOTAL_COLUMN].tolist() == pytest.approx(
            table.value[grades].sum(axis=1).tolist())
        assert table.value[TOTAL_COLUMN].nunique() > 1
        assert table.value["RAW-TBT"].sum() > table.value["RAW-MT"].sum()
    assert any("synthetic planning assumption" in item.value for item in at.info)
    assert any("capacities" in item.label for item in at.expander)
    assert {item.label for item in at.main.toggle} == {"Enable daily mine arrivals"}


@pytest.mark.parametrize("invalid", [-1.0, 0.5, float("nan"), float("inf"), None, True])
def test_invalid_daily_quantity_is_rejected_without_mutating_configuration(invalid):
    scenario = datasets.end_to_end()
    original = mine_arrivals.settings(scenario)
    before = deepcopy(original)
    frames = {site: arrival_frame(scenario, original, site)
              for site in mine_arrivals.SITE_LABELS}
    frames["BENGUERIR"]["RAW-MT"] = frames["BENGUERIR"]["RAW-MT"].astype(object)
    frames["BENGUERIR"].loc[0, "RAW-MT"] = invalid
    with pytest.raises(ValueError, match="quantity"):
        configuration_from_frames(scenario, original, True, "OPERATING", frames)
    assert original == before


def test_changed_day_identifier_is_rejected():
    scenario = datasets.end_to_end()
    original = mine_arrivals.settings(scenario)
    frames = {site: arrival_frame(scenario, original, site)
              for site in mine_arrivals.SITE_LABELS}
    frames["LOUTA"].loc[0, DATE_COLUMN] = "1999-01-01"
    with pytest.raises(ValueError, match="identifiers"):
        configuration_from_frames(scenario, original, True, "OPERATING", frames)


def test_applied_grade_quantity_survives_navigation_and_restart(isolated_preferences):
    at = fresh_page()
    before = deepcopy(at.session_state["scenario"])
    at.session_state["plan"] = PlanResult(status="FEASIBLE")
    editor = next(item for item in editors(at) if "BENGUERIR" in str(item.key))
    quantity = float(editor.value.iloc[0]["RAW-MT"]) + 100.0
    at.session_state[editor.key] = {
        "edited_rows": {0: {"RAW-MT": quantity}},
        "added_rows": [], "deleted_rows": [],
    }
    apply_button(at).click().run()
    assert not at.exception, at.exception
    assert not at.main.error, at.main.error
    assert isolated_preferences.is_file()
    assert at.session_state["plan"] is None
    saved = mine_arrivals.settings(at.session_state["scenario"])
    day = next(row for row in saved["days"]
               if row["site"] == "BENGUERIR" and row["day"] == 1)
    assert day["volumes_t"]["RAW-MT"] == quantity
    assert [(space.id, space.capacity_t) for space in before.spaces] == [
        (space.id, space.capacity_t) for space in at.session_state["scenario"].spaces]
    nav_to(at, common.CONFIGURE_GRADE_PROFILES_PAGE)
    nav_to(at, common.CONFIGURE_MINE_ARRIVALS_PAGE)
    assert mine_arrivals.settings(at.session_state["scenario"]) == saved
    restarted = fresh_page()
    assert mine_arrivals.settings(restarted.session_state["scenario"]) == saved


def test_toggle_stages_then_saves_disabled_state_across_restart():
    at = fresh_page()
    original = deepcopy(at.session_state["scenario"])
    toggle = next(item for item in at.main.toggle
                  if item.label == "Enable daily mine arrivals")
    assert toggle.value is True
    toggle.set_value(False).run()
    assert at.session_state["scenario"] == original
    next(item for item in at.main.radio if item.label == "Stock policy").set_value(
        "EXACT_DAILY").run()
    apply_button(at).click().run()
    assert not at.exception, at.exception
    saved = mine_arrivals.settings(at.session_state["scenario"])
    assert saved["enabled"] is False
    assert saved["stock_policy"] == "EXACT_DAILY"
    assert saved["days"] == mine_arrivals.settings(original)["days"]
    restarted = fresh_page()
    assert mine_arrivals.settings(restarted.session_state["scenario"]) == saved
    assert next(item for item in restarted.main.toggle
                if item.label == "Enable daily mine arrivals").value is False


def test_hot_session_with_washing_off_keeps_arrivals_disabled():
    at = fresh_page()
    scenario = deepcopy(at.session_state["scenario"])
    scenario.config.pop(mine_arrivals.CONFIG_KEY)
    scenario.exogenous = [flow for flow in scenario.exogenous
                          if not flow.id.startswith(mine_arrivals.FLOW_PREFIX)]
    scenario.config[washing.CONFIG_KEY]["enabled"] = False
    at.session_state["scenario"] = scenario
    del at.session_state["_mine_arrivals_initialized"]
    at.run()
    assert not at.exception, at.exception
    assert not washing.enabled(at.session_state["scenario"])
    assert mine_arrivals.settings(at.session_state["scenario"])["enabled"] is False
    assert next(item for item in at.main.toggle
                if item.label == "Enable daily mine arrivals").value is False


def test_hot_session_repairs_deleted_zero_grade_without_resetting_plan_or_supply():
    at = fresh_page()
    scenario = deepcopy(at.session_state["scenario"])
    before_flows = deepcopy(scenario.exogenous)
    for row in scenario.config[mine_arrivals.CONFIG_KEY]["days"]:
        row["volumes_t"]["RAW-DELETED"] = 0
    at.session_state["scenario"] = scenario
    plan = PlanResult(status="FEASIBLE")
    at.session_state["plan"] = plan
    assert at.session_state["_mine_arrivals_initialized"]
    at.run()
    assert not at.exception, at.exception
    updated = at.session_state["scenario"]
    assert updated.exogenous == before_flows
    assert at.session_state["plan"] == plan
    assert all("RAW-DELETED" not in row["volumes_t"]
               for row in mine_arrivals.settings(updated)["days"])
    assert not [i for i in at.session_state["issues"] if i.code == "BAD_MINE_ARRIVALS"]


def test_hot_session_preserves_and_identifies_invalid_positive_supply():
    at = fresh_page()
    scenario = deepcopy(at.session_state["scenario"])
    scenario.config[mine_arrivals.CONFIG_KEY]["days"][0]["volumes_t"]["RAW-DELETED"] = 100
    at.session_state["scenario"] = scenario
    # Force the ordinary validation path, just as opening an imported config.
    from mph_sim.validation import validate_scenario
    at.session_state["issues"] = validate_scenario(scenario)
    at.run()
    assert not at.exception, at.exception
    issues = [i for i in at.session_state["issues"] if i.code == "BAD_MINE_ARRIVALS"]
    assert issues and "RAW-DELETED" in issues[0].message
    assert at.session_state["scenario"].config[mine_arrivals.CONFIG_KEY]["days"][0]["volumes_t"]["RAW-DELETED"] == 100


def test_fresh_ui_uses_saved_catalog_without_stale_zero_arrival_columns(monkeypatch, tmp_path):
    from mph_sim.grade_catalog import apply_saved_grade_catalog, save_grade_catalog
    source = datasets.end_to_end()
    path = tmp_path / "grades.json"
    save_grade_catalog([g for g in source.grades if g.id != "RAW-TBC"], path)
    monkeypatch.setattr(common, "apply_saved_grade_catalog",
                        lambda sc: apply_saved_grade_catalog(sc, path))
    # The ordinary UI tests intentionally bypass saved grade catalogues. This
    # regression must exercise that production-only startup overlay as well.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    at = fresh_page()
    sc = at.session_state["scenario"]
    assert sc.grade("RAW-TBC") is None
    assert not [i for i in at.session_state["issues"] if i.code == "BAD_MINE_ARRIVALS"]
    totals = {site: sum(sum(row["volumes_t"].values())
                        for row in mine_arrivals.settings(sc)["days"]
                        if row["site"] == site)
              for site in mine_arrivals.SITE_LABELS}
    assert totals == {"BENGUERIR": 169200, "LOUTA": 84000}


def test_failed_save_keeps_scenario_and_existing_plan(monkeypatch, isolated_preferences):
    at = fresh_page()
    original = deepcopy(at.session_state["scenario"])
    plan = PlanResult(status="FEASIBLE")
    at.session_state["plan"] = plan
    next(item for item in at.main.toggle
         if item.label == "Enable daily mine arrivals").set_value(False).run()

    def fail_save(_scenario):
        raise OSError("test disk failure")

    monkeypatch.setattr(common, "save_mine_arrivals_preferences", fail_save)
    apply_button(at).click().run()
    assert not at.exception, at.exception
    assert any("Nothing was applied. test disk failure" in item.value
               for item in at.main.error)
    assert at.session_state["scenario"] == original
    assert at.session_state["plan"] == plan
    assert not isolated_preferences.exists()
