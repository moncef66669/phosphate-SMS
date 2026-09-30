"""Transactional UI contract for synthetic treatment configuration."""
from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from conftest import nav_to
from mph_sim import washing
from ui import common
from ui.washing_configuration import configuration_from_frames
from ui.pages_overview import washing_kpi_rows


def fresh_page():
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=300)
    at.run()
    nav_to(at, common.CONFIGURE_WASHING_TREATMENTS_PAGE)
    assert not at.exception
    return at


def test_washing_configuration_has_a_dedicated_configure_page():
    at = fresh_page()
    assert at.sidebar.radio[0].value == "Configure"
    assert at.sidebar.radio[1].value == \
        common.CONFIGURE_WASHING_TREATMENTS_PAGE
    assert at.sidebar.radio[1].options == [
        "Operational configuration",
        "Grade configuration",
        "Washing treatments",
        "Mine arrivals",
    ]
    assert [item.value for item in at.main.subheader] == [
        "Washing treatments"]
    sections = {str(item.value) for item in at.main.markdown}
    assert "##### Quality improvement by washing option" in sections
    assert "##### Washing-option operating parameters" in sections
    assert "##### PAP G10 acceptance limits" in sections
    assert "Apply washing treatments & G10 contract" in [
        button.label for button in at.main.button]
    toggles = [item for item in at.toggle
               if item.label ==
               "Enable washing treatment decisions"]
    assert len(toggles) == 1
    assert toggles[0].value is True
    assert "Treatment cost unit" not in [
        item.label for item in at.text_input
    ]
    visible_warnings = " ".join(
        str(item.value) for item in (*at.main.warning, *at.sidebar.warning)
    )
    assert "WASHING_PROVISIONAL_CALIBRATION" not in visible_warnings
    assert "Provisional behavior-calibrated" not in visible_warnings


def test_default_on_toggle_is_applied_to_s0_and_can_be_disabled():
    from mph_sim.domain import PlanResult

    at = fresh_page()
    sc = deepcopy(at.session_state["scenario"])
    at.session_state["plan"] = PlanResult(status="FEASIBLE")
    toggle = next(item for item in at.toggle if item.label ==
                  "Enable washing treatment decisions")
    assert toggle.value is True
    assert washing.enabled(sc)
    toggle.set_value(False).run()
    assert at.session_state["scenario"] == sc
    next(item for item in at.button
         if item.label == "Apply washing treatments & G10 contract").click().run()

    assert not at.exception, at.exception
    assert not washing.enabled(at.session_state["scenario"])
    assert at.session_state["plan"] is None
    assert any("PAP G10 contract were applied" in str(item.value)
               for item in at.main.success)
    applied = at.session_state["scenario"]
    assert [(s.id, s.grade, s.mass_t, s.quality) for s in applied.spaces] == [
        (s.id, s.grade, s.mass_t, s.quality) for s in sc.spaces]
    assert applied.config["MIN_JOB_VOLUME_T"] == 0
    assert washing.settings(applied)["modes"]["simple"]["dry_solids_yield"] == .85
    assert washing.settings(applied)["modes"]["full"]["dry_solids_yield"] == .65
    assert washing.settings(applied)["modes"]["simple"]["changes_pct"]["moisture"] == 20
    assert washing.settings(applied)["modes"]["full"]["changes_pct"]["moisture"] == 20
    assert washing.settings(applied)["modes"]["simple"]["changes_pct"]["BPL"] == 12
    assert washing.settings(applied)["modes"]["full"]["changes_pct"]["BPL"] == 23
    assert washing.settings(applied)["modes"]["simple"]["cost_per_tsm_t"] == .63
    assert washing.settings(applied)["modes"]["full"]["cost_per_tsm_t"] == 1.04
    assert washing.settings(applied)["cost_unit"] == "$"


def test_legacy_demo_defaults_are_migrated_in_active_sessions():
    from mph_sim.datasets import end_to_end

    sc = end_to_end()
    legacy = washing.defaults()
    legacy.pop("model_version")
    legacy["modes"]["simple"]["dry_solids_yield"] = .70
    legacy["modes"]["full"]["dry_solids_yield"] = .60
    legacy["modes"]["simple"]["changes_pct"] = dict(zip(
        washing.PARAMETERS, (35., -20., -30., -60., -50., 0.)))
    legacy["modes"]["full"]["changes_pct"] = dict(zip(
        washing.PARAMETERS, (45., -60., -60., -80., -70., 0.)))
    for mode in legacy["modes"].values():
        mode.pop("cost_per_tsm_t")
        mode["cost_per_dry_feed_t"] = None
    legacy["cost_unit"] = "cost units"
    legacy["g10_limits"]["BPL"] = [71., 90.]
    sc.config[washing.CONFIG_KEY] = legacy

    migrated = washing.settings(sc)
    assert migrated["model_version"] == washing.MODEL_VERSION
    assert migrated["modes"]["simple"]["dry_solids_yield"] == .85
    assert migrated["modes"]["full"]["dry_solids_yield"] == .65
    assert migrated["modes"]["simple"]["changes_pct"]["moisture"] == 20.
    assert migrated["modes"]["full"]["changes_pct"]["moisture"] == 20.
    assert migrated["modes"]["simple"]["changes_pct"]["BPL"] == 12.
    assert migrated["modes"]["full"]["changes_pct"]["BPL"] == 23.
    assert migrated["modes"]["simple"]["cost_per_tsm_t"] == .63
    assert migrated["modes"]["full"]["cost_per_tsm_t"] == 1.04
    assert migrated["cost_unit"] == "$"


def test_current_scenario_overlay_cannot_change_usd_cost_currency():
    from mph_sim.datasets import end_to_end

    sc = end_to_end()
    cfg = washing.defaults()
    cfg["cost_unit"] = "EUR"
    sc.config[washing.CONFIG_KEY] = cfg

    assert washing.settings(sc)["cost_unit"] == washing.COST_UNIT == "$"


def test_untouched_v5_calibration_migrates_without_overwriting_custom_modes():
    from mph_sim.datasets import end_to_end

    sc = end_to_end()
    v5 = washing.defaults()
    v5["model_version"] = 5
    v5["modes"]["simple"]["changes_pct"] = dict(zip(
        washing.PARAMETERS, (12., -15., -15., -45., -50., 20.)))
    v5["modes"]["full"]["changes_pct"] = dict(zip(
        washing.PARAMETERS, (24., -40., -45., -70., -60., 20.)))
    v5["g10_limits"] = {
        "BPL": [65., 90.], "Cd": [0., 18.], "MgO": [0., 1.2],
        "SiO2": [0., 6.], "CO2": [0., 4.5], "moisture": [0., 20.],
    }
    sc.config[washing.CONFIG_KEY] = v5

    migrated = washing.settings(sc)
    assert migrated["model_version"] == 6
    assert migrated["modes"]["simple"]["changes_pct"] == \
        washing.DEFAULT_MODES["simple"]["changes_pct"]
    assert migrated["modes"]["full"]["changes_pct"] == \
        washing.DEFAULT_MODES["full"]["changes_pct"]
    assert migrated["g10_limits"] == washing.DEFAULT_LIMITS

    v5["modes"]["simple"]["changes_pct"]["BPL"] = 13.
    sc.config[washing.CONFIG_KEY] = v5
    assert washing.settings(sc)["modes"]["simple"]["changes_pct"]["BPL"] == 13.


def test_legacy_s0_washing_off_session_is_enabled_and_old_plan_cleared():
    from mph_sim.domain import PlanResult

    at = fresh_page()
    scenario = deepcopy(at.session_state["scenario"])
    scenario.config[washing.CONFIG_KEY]["model_version"] = 2
    scenario.config[washing.CONFIG_KEY]["enabled"] = False
    at.session_state["scenario"] = scenario
    at.session_state["plan"] = PlanResult(status="FEASIBLE")

    at.run()

    assert not at.exception, at.exception
    assert washing.enabled(at.session_state["scenario"])
    assert at.session_state["scenario"].config[
        washing.CONFIG_KEY]["model_version"] == washing.MODEL_VERSION
    assert at.session_state["plan"] is None


def test_invalid_identifier_edit_is_rejected_before_staging():
    cfg = washing.defaults()
    before = deepcopy(cfg)
    with pytest.raises(ValueError):
        configuration_from_frames(cfg, True, pd.DataFrame({"Characteristic": ["BPL"]}),
                                  pd.DataFrame(), pd.DataFrame())
    assert cfg == before


def test_kpi_table_uses_recovered_tsm_cost_basis_and_empty_yield():
    from mph_sim.datasets import end_to_end
    rows = washing_kpi_rows(end_to_end(), {})
    assert len(rows) == 2
    assert all(row["Realized dry yield [%]"] is None for row in rows)
    assert all(row["Treatment cost [$]"] == 0 for row in rows)
def test_gantt_reports_recovered_dry_mass_from_actual_streams():
    from ui.theme import jobs_to_gantt_df
    from test_washing_treatments import fixture, solve
    sc = fixture()
    cfg = deepcopy(sc.config["WASHING_TREATMENTS"])
    cfg["modes"]["simple"]["changes_pct"]["moisture"] = -100.
    cfg["modes"]["full"]["available"] = False
    cfg["g10_limits"]["BPL"][0] = 60.
    sc = washing.stage_configuration(sc, cfg)
    plan = solve(sc)
    df = jobs_to_gantt_df(sc, plan.jobs)
    rows = df[df["treatment"] == "simple"]
    assert len(rows)
    assert rows["tsm_equivalent_t"].sum() == pytest.approx(722)
    assert set(rows["tsm_label"]) == {"expected recovered TSM"}
