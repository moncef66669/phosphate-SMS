"""Stage parity for the blending policy/catalogue UI, without running a solver."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from streamlit.proto.WidgetStates_pb2 import WidgetState

from conftest import nav_to
from mph_sim import prewash_blending
from ui import common


APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture(autouse=True)
def isolated_blend_preferences(monkeypatch, tmp_path):
    """Exercise real persistence without touching operator preferences."""
    destination = tmp_path / "blending_preferences.json"
    monkeypatch.setenv("MPH_BLEND_PREFERENCES_PATH", str(destination))
    return destination


def _fresh_blending():
    at = AppTest.from_file(APP, default_timeout=300).run()
    nav_to(at, common.PLAN_BLENDING_PAGE)
    assert not at.exception, at.exception
    return at


def _control(at, kind, label):
    [control] = [item for item in getattr(at.main, kind) if item.label == label]
    return control


def _location(at, point_id):
    label = ("Louta pre-washing" if point_id == "LOU-PREWASH"
             else "Benguerir pre-washing")
    _control(at, "segmented_control", "Pre-washing location").set_value(label).run()
    assert not at.exception, at.exception


def _enablement_table(at):
    [editor] = [frame for frame in at.main.dataframe
                if "Enable blending" in frame.value.columns
                and "Space ID" in frame.value.columns]
    return editor


def _set_stockpiles(at, selected_ids):
    """Send the frontend editor event so Streamlit actually runs on_change.

    AppTest doesn't expose a data-editor set_value method. Directly assigning
    session state skips the callback, so send its normal JSON widget payload.
    No Apply button is clicked or needed.
    """
    editor = _enablement_table(at)
    states = at._tree.get_widget_states()
    retained = [state for state in states.widgets if state.id != editor.proto.id]
    del states.widgets[:]
    states.widgets.extend(retained)
    states.widgets.append(WidgetState(
        id=editor.proto.id,
        string_value=json.dumps({
            "edited_rows": {
                str(index): {"Enable blending": space_id in selected_ids}
                for index, space_id in enumerate(editor.value["Space ID"])},
            "added_rows": [], "deleted_rows": [],
        })))
    at._run(states)
    assert not at.exception, at.exception


def _selected_stockpiles(at):
    frame = _enablement_table(at).value
    return set(frame.loc[frame["Enable blending"] == True, "Space ID"])


def _catalogue(point):
    components = point["optimized_components"]
    return [{
        "id": f"TEST-MATRIX-{number}",
        "name": f"Preserved hidden description {number}",
        "components": [{
            "source_space": row["source_space"],
            "grade_id": row["grade_id"],
            "target_fraction": share,
        } for row, share in zip(components, shares)],
    } for number, shares in enumerate(((.4, .6), (.6, .4)), 1)]


def test_blending_lands_on_prewashing_but_preserves_stage_on_reruns():
    at = _fresh_blending()
    assert _control(at, "segmented_control", "Blending stage").value == \
        "Pre-washing stockpiles"

    _control(at, "segmented_control", "Blending stage").set_value(
        "MPH humid-phosphate stockpiles").run()
    at.run()
    assert not at.exception, at.exception
    assert _control(at, "segmented_control", "Blending stage").value == \
        "MPH humid-phosphate stockpiles"

    _control(at, "number_input", "Minimum quantity per input grade [t]").set_value(
        600.0).run()
    assert _control(at, "segmented_control", "Blending stage").value == \
        "MPH humid-phosphate stockpiles"
    nav_to(at, common.PLAN_ROUTING_PAGE)
    nav_to(at, common.PLAN_BLENDING_PAGE)
    assert not at.exception, at.exception
    assert _control(at, "segmented_control", "Blending stage").value == \
        "Pre-washing stockpiles"


@pytest.mark.parametrize("point_id", ["LOU-PREWASH", "BEN-PREWASH"])
def test_prewashing_enablement_autosaves_stockpiles_only_at_selected_location(
        point_id, isolated_blend_preferences):
    at = _fresh_blending()
    scn = at.session_state["scenario"]
    scn.config[prewash_blending.CONFIG_KEY]["points"][point_id][
        "matrix_catalog"] = _catalogue(
            scn.config[prewash_blending.CONFIG_KEY]["points"][point_id])
    _location(at, point_id)
    before = prewash_blending.settings(scn)
    other_id = next(key for key in before["points"] if key != point_id)
    source_ids = list(_enablement_table(at).value["Space ID"])
    _set_stockpiles(at, {source_ids[-1]})
    assert not at.exception, at.exception
    assert not at.main.error, at.main.error
    updated = at.session_state["scenario"].config[prewash_blending.CONFIG_KEY]
    assert updated["points"][point_id]["enabled_source_spaces"] == [source_ids[-1]]
    assert updated["points"][other_id] == before["points"][other_id]
    assert updated["points"][point_id]["matrix_catalog"] == \
        before["points"][point_id]["matrix_catalog"]
    assert updated["points"][point_id]["share_mode"] == "OPTIMIZED"
    assert updated["enabled"] is True
    assert isolated_blend_preferences.is_file()
    assert _selected_stockpiles(at) == {source_ids[-1]}

    # Navigate away immediately after the checkbox event, without Apply.
    nav_to(at, common.PLAN_ROUTING_PAGE)
    nav_to(at, common.PLAN_BLENDING_PAGE)
    _location(at, point_id)
    assert _selected_stockpiles(at) == {source_ids[-1]}
    assert at.session_state["scenario"].config[
        prewash_blending.CONFIG_KEY]["points"][point_id][
            "enabled_source_spaces"] == [source_ids[-1]]

    # A new browser session reloads the selective preference from disk.
    fresh = _fresh_blending()
    _location(fresh, point_id)
    assert _selected_stockpiles(fresh) == {source_ids[-1]}


@pytest.mark.parametrize("point_id", ["LOU-PREWASH", "BEN-PREWASH"])
def test_prewashing_matrices_remain_one_row_each_and_save_shared_catalogue(point_id):
    at = _fresh_blending()
    scn = at.session_state["scenario"]
    point = scn.config[prewash_blending.CONFIG_KEY]["points"][point_id]
    point["matrix_catalog"] = _catalogue(point)
    _location(at, point_id)
    before = prewash_blending.settings(scn)
    other_id = next(key for key in before["points"] if key != point_id)
    [editor] = [frame for frame in at.main.dataframe
                if "Matrix ID" in frame.value.columns]
    assert len(editor.value) == 2
    assert "Matrix name" not in editor.value.columns
    columns = [f"{row['grade_id']} @ {row['source_space']} [%]"
               for row in point["optimized_components"]]
    assert list(editor.value.columns) == ["Matrix ID", *columns]
    at.session_state[f"prewash_catalog_wide_{point_id}"] = {
        "edited_rows": {0: {columns[0]: 35.0, columns[1]: 65.0}},
        "added_rows": [], "deleted_rows": [],
    }
    _control(at, "button", "Apply blending matrices").click().run()
    assert not at.exception, at.exception
    assert not at.main.error, at.main.error
    updated = at.session_state["scenario"].config[prewash_blending.CONFIG_KEY]
    expected = deepcopy(before["points"][point_id]["matrix_catalog"])
    expected[0]["components"][0]["target_fraction"] = .35
    expected[0]["components"][1]["target_fraction"] = .65
    assert updated["points"][point_id]["matrix_catalog"] == expected
    assert updated["points"][point_id]["enabled_source_spaces"] == \
        before["points"][point_id]["enabled_source_spaces"]
    assert updated["points"][point_id]["share_mode"] == "OPTIMIZED"
    assert updated["points"][other_id] == before["points"][other_id]


def test_prewashing_master_toggle_autosaves_without_losing_stockpile_choices():
    at = _fresh_blending()
    source_ids = list(_enablement_table(at).value["Space ID"])
    _set_stockpiles(at, {source_ids[-1]})
    before = prewash_blending.settings(at.session_state["scenario"])
    _control(at, "toggle", "Enable pre-washing feed blending").set_value(False).run()
    assert not at.exception, at.exception
    assert not at.main.error, at.main.error
    updated = at.session_state["scenario"].config[prewash_blending.CONFIG_KEY]
    assert updated["enabled"] is False
    for point_id in before["points"]:
        assert updated["points"][point_id]["enabled_source_spaces"] == \
            before["points"][point_id]["enabled_source_spaces"]
    fresh = _fresh_blending()
    assert _control(fresh, "toggle", "Enable pre-washing feed blending").value is False
    assert _selected_stockpiles(fresh) == {source_ids[-1]}
    _control(fresh, "toggle", "Enable pre-washing feed blending").set_value(True).run()
    assert fresh.session_state["scenario"].config[prewash_blending.CONFIG_KEY][
        "enabled"] is True
    assert _selected_stockpiles(fresh) == {source_ids[-1]}


def test_prewashing_share_selection_mode_applies_to_the_whole_location():
    at = _fresh_blending()
    scn = at.session_state["scenario"]
    point = scn.config[prewash_blending.CONFIG_KEY]["points"]["LOU-PREWASH"]
    point["matrix_catalog"] = _catalogue(point)
    at.run()
    before = prewash_blending.settings(scn)
    _control(at, "radio", "Share selection mode").set_value("Blend matrix").run()
    _control(at, "button", "Apply blending policy").click().run()
    assert not at.exception, at.exception
    assert not at.main.error, at.main.error
    cfg = at.session_state["scenario"].config[prewash_blending.CONFIG_KEY]
    assert cfg["points"]["LOU-PREWASH"]["share_mode"] == "MATRIX"
    assert cfg["points"]["LOU-PREWASH"]["enabled_source_spaces"] == \
        before["points"]["LOU-PREWASH"]["enabled_source_spaces"]
    assert cfg["points"]["LOU-PREWASH"]["matrix_catalog"] == \
        before["points"]["LOU-PREWASH"]["matrix_catalog"]
    assert cfg["points"]["BEN-PREWASH"] == before["points"]["BEN-PREWASH"]


def test_prewashing_policy_rows_are_individual_stockpiles_with_own_inventory():
    at = _fresh_blending()
    editor = _enablement_table(at)
    assert list(editor.value.columns) == [
        "Enable blending", "Stockpile", "Space ID", "Input grade",
        "Inventory [t]", "Capacity [t]",
        "Planned input [t]"]
    assert len(editor.value["Space ID"]) == len(set(editor.value["Space ID"]))
    spaces = {space.id: space for space in at.session_state["scenario"].spaces}
    for row in editor.value.to_dict("records"):
        space = spaces[row["Space ID"]]
        assert row["Input grade"] == space.grade
        assert row["Inventory [t]"] == pytest.approx(space.mass_t)
        assert row["Capacity [t]"] == pytest.approx(space.capacity_t)


def test_prewashing_locations_keep_independent_stockpile_choices():
    at = _fresh_blending()
    saved = {}
    for point_id in ("LOU-PREWASH", "BEN-PREWASH"):
        _location(at, point_id)
        ids = list(_enablement_table(at).value["Space ID"])
        saved[point_id] = {ids[0] if point_id == "LOU-PREWASH" else ids[-1]}
        _set_stockpiles(at, saved[point_id])
    for point_id, enabled in saved.items():
        _location(at, point_id)
        assert _selected_stockpiles(at) == enabled
    fresh = _fresh_blending()
    for point_id, enabled in saved.items():
        _location(fresh, point_id)
        assert _selected_stockpiles(fresh) == enabled


def test_repeated_stockpile_checkbox_edits_preserve_explicit_empty_selection():
    at = _fresh_blending()
    ids = list(_enablement_table(at).value["Space ID"])
    for selected in ({ids[-1]}, set(ids), set()):
        _set_stockpiles(at, selected)
        assert not at.main.error, at.main.error
        assert _selected_stockpiles(at) == selected
        assert set(at.session_state["scenario"].config[
            prewash_blending.CONFIG_KEY]["points"]["LOU-PREWASH"][
                "enabled_source_spaces"]) == selected
    fresh = _fresh_blending()
    assert _selected_stockpiles(fresh) == set()
    assert fresh.session_state["scenario"].config[
        prewash_blending.CONFIG_KEY]["points"]["LOU-PREWASH"][
            "enabled_source_spaces"] == []


def test_mph_stockpile_enablement_autosaves_and_restores_new_session():
    at = _fresh_blending()
    _control(at, "segmented_control", "Blending stage").set_value(
        "MPH humid-phosphate stockpiles").run()
    before = deepcopy(at.session_state["scenario"].config["CHEVRON_STACKING"])
    _set_stockpiles(at, {"HP-07", "HP-08"})
    assert not at.main.error, at.main.error
    updated = at.session_state["scenario"].config["CHEVRON_STACKING"]
    assert set(updated["enabled_spaces"]) == {"HP-07", "HP-08"}
    for key in ("matrix_catalog", "global_mode", "min_input_grade_t"):
        assert updated[key] == before[key]
    nav_to(at, common.PLAN_ROUTING_PAGE)
    nav_to(at, common.PLAN_BLENDING_PAGE)
    _control(at, "segmented_control", "Blending stage").set_value(
        "MPH humid-phosphate stockpiles").run()
    assert _selected_stockpiles(at) == {"HP-07", "HP-08"}
    fresh = _fresh_blending()
    _control(fresh, "segmented_control", "Blending stage").set_value(
        "MPH humid-phosphate stockpiles").run()
    assert _selected_stockpiles(fresh) == {"HP-07", "HP-08"}


@pytest.mark.parametrize("stage", ["Pre-washing stockpiles",
                                   "MPH humid-phosphate stockpiles"])
def test_failed_checkbox_save_rolls_back_scenario_and_displays_error(
        monkeypatch, stage, isolated_blend_preferences):
    from mph_sim.blend_preferences import BlendPreferencesError

    at = _fresh_blending()
    _control(at, "segmented_control", "Blending stage").set_value(stage).run()
    before = deepcopy(at.session_state["scenario"].config)
    enabled_before = _selected_stockpiles(at)

    def fail_write(*args, **kwargs):
        raise BlendPreferencesError("Disk unavailable for test")

    monkeypatch.setattr(common, "save_blend_enablement_preferences", fail_write)
    choices = set(_enablement_table(at).value["Space ID"])
    requested = ({"HP-07", "HP-08"} if stage.startswith("MPH")
                 else choices - enabled_before)
    _set_stockpiles(at, requested)
    assert at.session_state["scenario"].config == before
    assert _selected_stockpiles(at) == enabled_before
    assert any("Disk unavailable for test" in str(error.value)
               for error in at.main.error)
    assert not isolated_blend_preferences.exists()


def test_failed_master_switch_save_rolls_back(monkeypatch):
    from mph_sim.blend_preferences import BlendPreferencesError

    at = _fresh_blending()
    before = deepcopy(at.session_state["scenario"].config)

    def fail_write(*args, **kwargs):
        raise BlendPreferencesError("Cannot write blending preferences")

    monkeypatch.setattr(common, "save_blend_enablement_preferences", fail_write)
    _control(at, "toggle", "Enable pre-washing feed blending").set_value(False).run()
    assert not at.exception, at.exception
    assert at.session_state["scenario"].config == before
    assert _control(at, "toggle", "Enable pre-washing feed blending").value is True
    assert any("Cannot write blending preferences" in str(error.value)
               for error in at.main.error)


def test_pre_drying_has_matching_sections_but_no_fake_operational_settings():
    at = _fresh_blending()
    before = deepcopy(at.session_state["scenario"].config)
    _control(at, "segmented_control", "Blending stage").set_value("Pre-drying").run()
    assert not at.exception, at.exception
    headings = [item.value for item in at.main.markdown]
    assert "#### Blending Policy" in headings
    assert "#### Blending Matrices" in headings
    assert headings.index("#### Blending Policy") < headings.index(
        "#### Blending Matrices")
    mode = _control(at, "radio", "Share selection mode")
    assert mode.options == ["Blend matrix", "Optimized shares"]
    assert mode.disabled is True
    assert _control(at, "button", "Apply blending policy").disabled is True
    assert _control(at, "button", "Apply blending matrices").disabled is True
    tables = [item.value for item in at.main.dataframe
              if "Enable blending" in item.value.columns
              or "Matrix ID" in item.value.columns]
    assert len(tables) == 2
    assert all(frame.empty for frame in tables)
    assert "Enable blending" in tables[0].columns
    assert list(tables[1].columns) == ["Matrix ID"]
    assert any("Pre-drying blending is not configured" in item.value
               for item in at.main.info)
    assert at.session_state["scenario"].config == before


@pytest.mark.parametrize("point_id", ["LOU-PREWASH", "BEN-PREWASH"])
def test_prewashing_additional_inputs_only_offer_connected_site_stockpiles(point_id):
    at = _fresh_blending()
    _location(at, point_id)
    scn = at.session_state["scenario"]
    point = prewash_blending.settings(scn)["points"][point_id]
    inputs = _control(at, "multiselect", "Additional input grades")
    assert inputs.options
    sources = {space.id: space for space in scn.spaces}
    required_stages = set(point["process_stages"])
    for option in inputs.options:
        grade_id, source_id = option.removesuffix(" [%]").split(" @ ")
        assert sources[source_id].site == point["site"]
        assert sources[source_id].grade == grade_id
        connected_stages = {
            transfer.process_stage for transfer in scn.process_transfers
            if transfer.source_space == source_id}
        assert required_stages <= connected_stages
    inputs.set_value([inputs.options[-1]]).run()
    assert not at.exception, at.exception
    [editor] = [frame for frame in at.main.dataframe
                if "Matrix ID" in frame.value.columns]
    assert inputs.options[-1] in editor.value.columns
    assert editor.value.empty
    assert "Matrix name" not in editor.value.columns
