from copy import deepcopy
import json

import pytest

from mph_sim import blend_preferences as preferences
from mph_sim.domain import ProcessTransfer, Scenario, StockSpace


MPH = "CHEVRON_STACKING"
PREWASH = "PRE_WASHING_BLENDING"


def _scenario(name="S0"):
    spaces = [
        StockSpace("HP-07", "MPH", "M16-humid", "open", 10000, 500, "BT"),
        StockSpace("HP-08", "MPH", "M16-humid", "open", 10000, 0, "BT"),
        StockSpace("DP-01", "MPH", "M16-dry", "open", 10000, 0, "BT"),
        StockSpace("LOU-1", "LOUTA", "mine-yard", "open", 5000, 1000, "RAW"),
        StockSpace("LOU-2", "LOUTA", "mine-yard", "open", 5000, 1000, "RAW"),
        StockSpace("LOU-3", "LOUTA", "mine-yard", "open", 5000, 1000, "RAW"),
        StockSpace("BEN-1", "BENGUERIR", "mine-yard", "open", 5000, 1000, "RAW"),
    ]
    return Scenario(name=name, spaces=spaces, process_transfers=[
        ProcessTransfer("ROUTE-3", "MACHINE", "LOU-3", "HP-07",
                        process_stage="LOU-WASH-L1"),
        ProcessTransfer("OTHER-SITE", "MACHINE", "BEN-1", "HP-07",
                        process_stage="LOU-WASH-L1"),
    ], config={
        MPH: {"enabled": True, "enabled_spaces": ["HP-07"],
              "global_mode": "MATRIX", "matrix_catalog": [{"id": "KEEP"}],
              "campaign_states_by_space": {"HP-07": {"state": "BUILDING"}},
              "proposed_recipes_by_space": {"HP-08": [{"grade_id": "BT"}]}},
        PREWASH: {"enabled": True, "points": {
            "LOU-PREWASH": {"site": "LOUTA", "process_stages": ["LOU-WASH-L1"],
                "enabled_source_spaces": ["LOU-1", "LOU-2"],
                "optimized_components": [{"source_space": "LOU-1"}],
                "matrix_catalog": [{"id": "L", "components": [
                    {"source_space": "LOU-2"}]}], "share_mode": "OPTIMIZED"},
            "BEN-PREWASH": {"site": "BENGUERIR", "process_stages": [],
                "enabled_source_spaces": [],
                "components": [{"source_space": "BEN-1"}]}},
            "scope": "DAILY"},
        "UNRELATED": {"objective": 100, "WASHING": False},
    })


def _write(path, records):
    path.write_text(json.dumps({"version": 1, "scenarios": records}), encoding="utf-8")


def test_roundtrip_survives_new_scenario_without_saving_model_state(tmp_path):
    path = tmp_path / "config" / "blending_preferences.json"
    original = _scenario()
    original.config[MPH]["enabled_spaces"] = ["HP-08"]
    original.config[PREWASH]["points"]["LOU-PREWASH"]["enabled_source_spaces"] = ["LOU-3"]
    before = deepcopy(original)

    assert preferences.save_blend_enablement(original, path) == path
    assert original == before
    stored = json.loads(path.read_text(encoding="utf-8"))["scenarios"]["S0"]
    assert stored == {
        MPH: {"enabled": True, "enabled_spaces": ["HP-08"]},
        PREWASH: {"enabled": True, "points": {
            "LOU-PREWASH": {"enabled_source_spaces": ["LOU-3"]},
            "BEN-PREWASH": {"enabled_source_spaces": []}}},
    }
    restarted = _scenario()
    assert preferences.apply_saved_blend_enablement(restarted, path)
    assert restarted == original
    assert not preferences.apply_saved_blend_enablement(restarted, path)


def test_scenarios_are_saved_and_restored_independently(tmp_path):
    path = tmp_path / "preferences.json"
    first = _scenario("FIRST")
    second = _scenario("SECOND")
    second.config[MPH].update(enabled=False, enabled_spaces=[])
    preferences.save_blend_enablement(first, path)
    preferences.save_blend_enablement(second, path)
    loaded_first, loaded_second = _scenario("FIRST"), _scenario("SECOND")
    assert not preferences.apply_saved_blend_enablement(loaded_first, path)
    assert preferences.apply_saved_blend_enablement(loaded_second, path)
    assert loaded_first == first
    assert loaded_second == second
    missing = _scenario("UNSAVED")
    assert not preferences.apply_saved_blend_enablement(missing, path)
    assert missing == _scenario("UNSAVED")


def test_explicit_false_and_empty_lists_are_not_replaced_by_defaults(tmp_path):
    path = tmp_path / "preferences.json"
    original = _scenario()
    original.config[MPH].update(enabled=False, enabled_spaces=[])
    original.config[PREWASH]["enabled"] = False
    original.config[PREWASH]["points"]["LOU-PREWASH"]["enabled_source_spaces"] = []
    preferences.save_blend_enablement(original, path)
    loaded = _scenario()
    assert preferences.apply_saved_blend_enablement(loaded, path)
    assert loaded == original


def test_unknown_ids_wrong_site_and_nonhumid_piles_never_apply(tmp_path):
    path = tmp_path / "preferences.json"
    _write(path, {"S0": {
        MPH: {"enabled_spaces": ["HP-08", "DP-01", "LOU-1", "REMOVED"]},
        PREWASH: {"points": {
            "LOU-PREWASH": {"enabled_source_spaces": [
                "LOU-2", "LOU-3", "BEN-1", "HP-07", "MISSING"]},
            "UNKNOWN-SITE": {"enabled_source_spaces": ["LOU-1"]}}},
    }})
    loaded = _scenario()
    assert preferences.apply_saved_blend_enablement(loaded, path)
    assert loaded.config[MPH]["enabled_spaces"] == ["HP-08"]
    assert loaded.config[PREWASH]["points"]["LOU-PREWASH"]["enabled_source_spaces"] == ["LOU-2", "LOU-3"]
    assert "UNKNOWN-SITE" not in loaded.config[PREWASH]["points"]


def test_omitted_sections_and_new_points_are_preserved(tmp_path):
    path = tmp_path / "preferences.json"
    _write(path, {"S0": {PREWASH: {"points": {
        "LOU-PREWASH": {"enabled_source_spaces": []}}}}})
    loaded = _scenario()
    loaded.config[PREWASH]["points"]["NEW-SITE"] = {"enabled_source_spaces": ["CUSTOM"]}
    expected = deepcopy(loaded)
    expected.config[PREWASH]["points"]["LOU-PREWASH"]["enabled_source_spaces"] = []
    assert preferences.apply_saved_blend_enablement(loaded, path)
    assert loaded == expected


def test_partial_scenario_save_keeps_previously_saved_other_sections_and_points(tmp_path):
    path = tmp_path / "preferences.json"
    original = _scenario()
    preferences.save_blend_enablement(original, path)
    partial = _scenario()
    partial.config.pop(MPH)
    partial.config[PREWASH]["points"].pop("BEN-PREWASH")
    partial.config[PREWASH]["points"]["LOU-PREWASH"]["enabled_source_spaces"] = []
    preferences.save_blend_enablement(partial, path)
    saved = json.loads(path.read_text(encoding="utf-8"))["scenarios"]["S0"]
    assert saved[MPH]["enabled_spaces"] == ["HP-07"]
    assert saved[PREWASH]["points"]["BEN-PREWASH"] == {"enabled_source_spaces": []}


@pytest.mark.parametrize("malformed", [
    "{", "[]", '{"version": true, "scenarios": {}}',
    '{"version": 1, "scenarios": []}',
    json.dumps({"version": 1, "scenarios": {"S0": {MPH: {"enabled": 1}}}}),
    json.dumps({"version": 1, "scenarios": {"S0": {MPH: {"enabled_spaces": "HP-07"}}}}),
    json.dumps({"version": 1, "scenarios": {"S0": {PREWASH: {"points": {
        "LOU-PREWASH": {"enabled_source_spaces": [False]}}}}}}),
    json.dumps({"version": 1, "scenarios": {"S0": {MPH: {"matrix_catalog": []}}}}),
])
def test_malformed_files_never_partially_apply_or_get_overwritten(tmp_path, malformed):
    path = tmp_path / "preferences.json"
    path.write_text(malformed, encoding="utf-8")
    original = _scenario()
    before = deepcopy(original)
    with pytest.raises(preferences.BlendPreferencesError):
        preferences.apply_saved_blend_enablement(original, path)
    assert original == before
    with pytest.raises(preferences.BlendPreferencesError):
        preferences.save_blend_enablement(original, path)
    assert path.read_text(encoding="utf-8") == malformed
    assert original == before


def test_validation_of_later_record_prevents_earlier_record_partial_application(tmp_path):
    path = tmp_path / "preferences.json"
    _write(path, {"S0": {MPH: {"enabled_spaces": []}},
                  "BROKEN": {PREWASH: {"enabled": "yes"}}})
    original = _scenario()
    before = deepcopy(original)
    with pytest.raises(preferences.BlendPreferencesError):
        preferences.apply_saved_blend_enablement(original, path)
    assert original == before


def test_write_failure_preserves_previous_file_and_cleans_temp(tmp_path, monkeypatch):
    path = tmp_path / "preferences.json"
    original = _scenario()
    preferences.save_blend_enablement(original, path)
    previous = path.read_bytes()
    original.config[MPH]["enabled_spaces"] = []
    before = deepcopy(original)

    def fail_replace(*args):
        raise OSError("simulated permission failure")

    monkeypatch.setattr(preferences.os, "replace", fail_replace)
    with pytest.raises(preferences.BlendPreferencesError, match="simulated permission failure"):
        preferences.save_blend_enablement(original, path)
    assert path.read_bytes() == previous
    assert original == before
    assert list(tmp_path.iterdir()) == [path]


def test_missing_file_is_read_only_and_default_path_can_be_isolated(tmp_path, monkeypatch):
    path = tmp_path / "config" / "preferences.json"
    monkeypatch.setattr(preferences, "DEFAULT_BLEND_PREFERENCES_PATH", path)
    original = _scenario()
    assert not preferences.apply_saved_blend_enablement(original)
    assert not path.parent.exists()
    preferences.save_blend_enablement(original)
    assert path.exists()


@pytest.mark.parametrize("field,value", [("enabled", "true"), ("enabled_spaces", [1])])
def test_invalid_scenario_switches_do_not_create_file(tmp_path, field, value):
    path = tmp_path / "preferences.json"
    original = _scenario()
    original.config[MPH][field] = value
    with pytest.raises(preferences.BlendPreferencesError):
        preferences.save_blend_enablement(original, path)
    assert not path.exists()
