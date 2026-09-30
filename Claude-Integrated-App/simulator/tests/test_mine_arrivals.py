"""Physical tonnes, civil-day timing and persistence of mine replenishment.

These checks use explicit daily quantities, independently reconstruct the
ExogenousFlow inputs consumed by the solver, and retain the opening stock.
"""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from mph_sim import datasets, mine_arrivals as arrivals, washing
from mph_sim.domain import ExogenousFlow
from mph_sim.optimizer import build_candidates
from mph_sim.persistence import scenario_from_json, scenario_to_json
from mph_sim.routes import RouteGraph


@pytest.fixture
def scenario():
    sc = datasets.end_to_end()
    washing_cfg = washing.settings(sc)
    washing_cfg["enabled"] = True
    sc = washing.stage_configuration(sc, washing_cfg)
    arrivals.install_defaults(sc)
    return sc


def _configuration(sc, rows, *, enabled=True, policy="OPERATING"):
    cfg = arrivals.settings(sc)
    requested = {(day, site): volumes for day, site, volumes in rows}
    count = max(7, max((day for day, _, _ in rows), default=1))
    cfg.update(enabled=enabled, stock_policy=policy, days=[
        {"day": day, "site": site,
         "volumes_t": dict(requested.get((day, site), {}))}
        for day in range(1, count + 1)
        for site in ("BENGUERIR", "LOUTA")
    ])
    return cfg


def _managed(sc):
    return [flow for flow in sc.exogenous
            if flow.id.startswith(arrivals.FLOW_PREFIX)]


def _bucket_tonnes(sc, site, grade):
    return [sum(flow.rate_tph * sc.sim.dt_h for flow in _managed(sc)
                if sc.space(flow.target_space).site == site
                and flow.grade == grade
                and flow.start_bucket <= bucket <= flow.end_bucket)
            for bucket in range(sc.sim.horizon_buckets)]


def test_installed_defaults_cover_week_with_small_mt_share_and_daily_variation(scenario):
    assert arrivals.supported(scenario)
    cfg = arrivals.settings(scenario)
    assert cfg["version"] == 1
    assert cfg["enabled"] is True
    assert cfg["stock_policy"] == "OPERATING"
    assert cfg["evidence"]
    assert len(cfg["days"]) == 14
    assert {(row["day"], row["site"]) for row in cfg["days"]} == {
        (day, site) for day in range(1, 8)
        for site in ("BENGUERIR", "LOUTA")}
    for row in cfg["days"]:
        quantities = row["volumes_t"]
        total = sum(quantities.values())
        assert total > 0
        assert all(type(value) is int and value >= 0 for value in quantities.values())
        assert 0.04 <= quantities["RAW-MT"] / total <= 0.06
        assert quantities["RAW-TBT"] > quantities["RAW-MT"]
        assert sum(value for grade, value in quantities.items()
                   if grade.startswith("RAW-BT")) > quantities["RAW-MT"]
    for site in ("BENGUERIR", "LOUTA"):
        totals = [sum(row["volumes_t"].values()) for row in cfg["days"]
                  if row["site"] == site]
        assert len(set(totals)) > 1
    assert _managed(scenario)
    assert arrivals.settings(datasets.end_to_end()) == cfg


def test_raw_choices_are_only_known_raw_materials(scenario):
    choices = arrivals.raw_grades(scenario)
    assert {"RAW-TBT", "RAW-MT", "RAW-BT"} <= set(choices)
    assert "RAW-BT-NOMINAL" not in choices
    assert "TBT" not in choices
    assert "G10" not in choices
    assert all(scenario.grade(grade).material_class.startswith("raw")
               for grade in choices)


def test_catalog_deletion_prunes_only_zero_columns_and_preserves_weekly_supply(scenario, tmp_path):
    from mph_sim.grade_catalog import apply_saved_grade_catalog, save_grade_catalog
    before_flows = deepcopy(scenario.exogenous)
    before_stocks = deepcopy(scenario.spaces)
    before = arrivals.settings(scenario)
    assert all(row["volumes_t"].get("RAW-TBC") == 0 for row in before["days"])
    path = tmp_path / "grades.json"
    save_grade_catalog([g for g in scenario.grades if g.id != "RAW-TBC"], path)
    assert apply_saved_grade_catalog(scenario, path)
    after = arrivals.settings(scenario)
    assert all("RAW-TBC" not in row["volumes_t"] for row in after["days"])
    assert [sum(r["volumes_t"].values()) for r in after["days"]] == [
        sum(r["volumes_t"].values()) for r in before["days"]]
    assert scenario.exogenous == before_flows
    assert scenario.spaces == before_stocks
    assert not arrivals.configuration_errors(scenario)
    assert not arrivals.reconcile_grade_catalog(scenario)  # idempotent


def test_refresh_and_saved_profile_repair_zero_only_deleted_grade(scenario, tmp_path):
    path = tmp_path / "mine_arrivals.json"
    arrivals.save_preferences(scenario, path)
    original_flows = deepcopy(scenario.exogenous)
    scenario.grades = [g for g in scenario.grades if g.id != "RAW-TBC"]
    arrivals.refresh(scenario)
    assert scenario.exogenous == original_flows
    assert not arrivals.configuration_errors(scenario)
    arrivals.apply_saved_preferences(scenario, path)
    assert scenario.exogenous == original_flows
    assert not arrivals.configuration_errors(scenario)
    assert all("RAW-TBC" not in row["volumes_t"]
               for row in arrivals.settings(scenario)["days"])


@pytest.mark.parametrize("value", [1, -1, True, "0", float("nan"), float("inf")])
def test_catalog_repair_never_hides_nonzero_or_malformed_supply(scenario, value):
    cfg = arrivals.settings(scenario)
    cfg["days"][0]["volumes_t"]["RAW-DELETED"] = value
    scenario.config[arrivals.CONFIG_KEY] = cfg
    arrivals.reconcile_grade_catalog(scenario)
    assert "RAW-DELETED" in scenario.config[arrivals.CONFIG_KEY]["days"][0]["volumes_t"]
    with pytest.raises(ValueError, match="BENGUERIR day 1.*RAW-DELETED"):
        arrivals.staged_configuration(scenario, cfg)


def test_known_nonraw_grade_zero_is_still_invalid(scenario):
    cfg = arrivals.settings(scenario)
    cfg["days"][0]["volumes_t"]["TBT"] = 0
    with pytest.raises(ValueError, match="TBT"):
        arrivals.staged_configuration(scenario, cfg)


def test_settings_and_staged_config_do_not_share_mutable_state(scenario):
    before = deepcopy(scenario)
    cfg = _configuration(scenario, [(1, "LOUTA", {"RAW-MT": 111})])
    staged = arrivals.staged_configuration(scenario, cfg)
    cfg["days"][1]["volumes_t"]["RAW-MT"] = 999
    assert arrivals.settings(staged)["days"][1]["volumes_t"]["RAW-MT"] == 111
    detached = arrivals.settings(staged)
    detached["days"].clear()
    assert arrivals.settings(staged)["days"]
    assert scenario == before


@pytest.mark.parametrize("dt_h", [1.0, 0.5, 0.25])
def test_complete_days_conserve_whole_physical_tonnes_at_each_resolution(scenario, dt_h):
    scenario.sim.dt_h = dt_h
    scenario.sim.horizon_buckets = int(48 / dt_h)
    scenario.sim.start_iso = "2026-09-21T00:00:00"
    rows = [(1, "BENGUERIR", {"RAW-TBT": 101, "RAW-MT": 17}),
            (2, "BENGUERIR", {"RAW-TBT": 203}),
            (1, "LOUTA", {"RAW-BT": 97}),
            (2, "LOUTA", {"RAW-BT": 211})]
    before = deepcopy(scenario)
    cfg = _configuration(scenario, rows)
    staged = arrivals.staged_configuration(scenario, cfg)
    assert scenario == before
    assert staged is not scenario
    per_day = int(24 / dt_h)
    for day, site, quantities in rows:
        for grade, quantity in quantities.items():
            series = _bucket_tonnes(staged, site, grade)
            daily = series[(day - 1) * per_day:day * per_day]
            assert sum(daily) == pytest.approx(quantity)
            assert all(value == pytest.approx(round(value)) for value in daily)
    assert all(flow.source_space == "" for flow in _managed(staged))
    assert len({flow.id for flow in _managed(staged)}) == len(_managed(staged))
    assert staged.spaces == before.spaces
    assert arrivals.staged_configuration(scenario, cfg) == staged


def test_civil_day_clipping_and_midnight_crossing_bucket(scenario):
    scenario.sim.start_iso = "2026-09-21T23:30:00"
    scenario.sim.dt_h = 1.0
    scenario.sim.horizon_buckets = 2
    cfg = _configuration(scenario, [
        (1, "BENGUERIR", {"RAW-TBT": 2400}),
        (2, "BENGUERIR", {"RAW-TBT": 4800}),
    ])
    staged = arrivals.staged_configuration(scenario, cfg)
    # First bucket: 30 min at day 1's 100 t/h, 30 min at day 2's 200 t/h.
    assert _bucket_tonnes(staged, "BENGUERIR", "RAW-TBT") == [150, 200]


def test_partial_civil_day_does_not_compress_whole_daily_volume(scenario):
    scenario.sim.start_iso = "2026-09-21T06:00:00"
    scenario.sim.dt_h = 0.5
    scenario.sim.horizon_buckets = 36
    cfg = _configuration(scenario, [(1, "LOUTA", {"RAW-MT": 333})])
    staged = arrivals.staged_configuration(scenario, cfg)
    expected = 333 - round(333 * 6 / 24)
    assert sum(_bucket_tonnes(staged, "LOUTA", "RAW-MT")) == expected


def test_refresh_observes_changed_horizon_without_rebasing_civil_days(scenario):
    scenario.sim.start_iso = "2026-09-21T00:00:00"
    scenario.sim.horizon_buckets = 48
    cfg = _configuration(scenario, [
        (1, "BENGUERIR", {"RAW-TBT": 2400}),
        (2, "BENGUERIR", {"RAW-TBT": 4800}),
    ])
    staged = arrivals.staged_configuration(scenario, cfg)
    staged.sim.horizon_buckets = 30
    arrivals.refresh(staged)
    assert sum(_bucket_tonnes(staged, "BENGUERIR", "RAW-TBT")) == 3600
    staged.sim.horizon_buckets = 6
    arrivals.refresh(staged, horizon_offset=24)
    flows = _managed(staged)
    assert {bucket for flow in flows for bucket in range(flow.start_bucket, flow.end_bucket + 1)} == set(range(24, 30))
    assert all(flow.rate_tph == 200 for flow in flows)
    assert sum(flow.rate_tph * staged.sim.dt_h * (flow.end_bucket - flow.start_bucket + 1) for flow in flows) == 1200


def test_new_arrival_grade_reaches_raw_stockpile_and_typed_candidates(scenario):
    cfg = _configuration(scenario, [(1, "BENGUERIR", {"RAW-MT": 1200})])
    staged = arrivals.staged_configuration(scenario, cfg)
    wash_cfg = washing.settings(staged)
    wash_cfg["enabled"] = True
    staged = washing.stage_configuration(staged, wash_cfg)
    candidates, _ = build_candidates(staged, RouteGraph.from_scenario(staged))
    targets = {flow.target_space for flow in _managed(staged)}
    assert targets
    assert any(candidate.kind == "process" and candidate.grade == "RAW-MT"
               and candidate.source_space in targets and candidate.material_id
               for candidate in candidates)
    assert any(candidate.kind == "process" and candidate.grade == "RAW-MT"
               and "RAW" in candidate.target_space and candidate.material_id
               for candidate in candidates)
    assert staged.spaces == scenario.spaces


@pytest.mark.parametrize("grade,value", [
    ("RAW-TBT", -1), ("RAW-TBT", float("nan")),
    ("RAW-TBT", float("inf")), ("RAW-TBT", 1.25),
    ("RAW-TBT", True), ("RAW-TBT", "100"),
    ("UNKNOWN", 100), ("TBT", 100), ("G10", 100),
])
def test_invalid_quantities_or_nonraw_grades_cannot_mutate_scenario(scenario, grade, value):
    cfg = _configuration(scenario, [(1, "BENGUERIR", {grade: value})])
    before = deepcopy(scenario)
    with pytest.raises(ValueError):
        arrivals.staged_configuration(scenario, cfg)
    assert scenario == before


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 2), ("enabled", "true"),
    ("stock_policy", "INVALID"), ("days", None),
    ("days", [{"day": 0, "site": "LOUTA", "volumes_t": {"RAW-MT": 1}}]),
    ("days", [{"day": 1, "site": "MPH", "volumes_t": {"RAW-MT": 1}}]),
    ("days", [{"day": 1.5, "site": "LOUTA", "volumes_t": {"RAW-MT": 1}}]),
    ("days", [{"day": True, "site": "LOUTA", "volumes_t": {"RAW-MT": 1}}]),
    ("days", [{"day": 1, "site": "LOUTA", "volumes_t": []}]),
])
def test_malformed_settings_are_rejected_atomically(scenario, field, value):
    cfg = arrivals.settings(scenario)
    cfg[field] = value
    before = deepcopy(scenario)
    with pytest.raises(ValueError):
        arrivals.staged_configuration(scenario, cfg)
    assert scenario == before


def test_duplicate_day_site_rows_are_rejected(scenario):
    cfg = _configuration(scenario, [(1, "LOUTA", {"RAW-MT": 100})])
    cfg["days"].append({"day": 1, "site": "LOUTA", "volumes_t": {"RAW-TBT": 200}})
    before = deepcopy(scenario)
    with pytest.raises(ValueError):
        arrivals.staged_configuration(scenario, cfg)
    assert scenario == before


def test_missing_day_site_is_rejected_instead_of_silently_losing_supply(scenario):
    cfg = arrivals.settings(scenario)
    cfg["days"] = [row for row in cfg["days"]
                   if (row["day"], row["site"]) != (7, "LOUTA")]
    before = deepcopy(scenario)
    with pytest.raises(ValueError, match="cover the planning horizon"):
        arrivals.staged_configuration(scenario, cfg)
    assert scenario == before


def test_missing_inlets_and_legacy_material_model_reject_enabled_arrivals(scenario):
    cfg = arrivals.settings(scenario)
    unsupported = datasets.sulphur_minimal()
    unsupported_before = deepcopy(unsupported)
    assert not arrivals.supported(unsupported)
    assert not arrivals.install_defaults(unsupported)
    with pytest.raises(ValueError):
        arrivals.staged_configuration(unsupported, cfg)
    assert unsupported == unsupported_before
    legacy = datasets.end_to_end()
    legacy_before = deepcopy(legacy)
    with pytest.raises(ValueError, match="grade-resolved material model"):
        arrivals.staged_configuration(legacy, cfg)
    assert legacy == legacy_before


@pytest.mark.parametrize("site,volumes", [
    ("BENGUERIR", {"RAW-TBT": 50_401}),
    ("LOUTA", {"RAW-TBT": 30_000, "RAW-MT": 30_000}),
])
def test_supply_beyond_existing_stacking_envelope_is_rejected(scenario, site, volumes):
    cfg = _configuration(scenario, [(1, site, volumes)])
    before = deepcopy(scenario)
    with pytest.raises(ValueError, match="stacking envelope"):
        arrivals.staged_configuration(scenario, cfg)
    assert scenario == before


def test_disabled_is_sticky_and_retains_schedule_and_unrelated_inputs(scenario):
    custom = ExogenousFlow("USER-FEED", "BG-RAW-1", 7, "RAW-TBT")
    scenario.exogenous.append(custom)
    cfg = _configuration(scenario, [(1, "LOUTA", {"RAW-MT": 75})], enabled=False)
    staged = arrivals.staged_configuration(scenario, cfg)
    assert not _managed(staged)
    assert custom in staged.exogenous
    assert arrivals.settings(staged)["days"] == cfg["days"]
    before = deepcopy(staged)
    arrivals.install_defaults(staged)
    assert staged == before
    restored = scenario_from_json(scenario_to_json(staged))
    assert arrivals.settings(restored) == arrivals.settings(staged)
    assert restored.exogenous == staged.exogenous
    assert restored.spaces == staged.spaces


def test_policy_switch_preserves_custom_terminal_targets_zero_wip_and_stock(scenario):
    scenario.config["TERMINAL_INVENTORY_EXACT_T"]["BG-RAW-1"] = 12345.0
    scenario.config["TERMINAL_INVENTORY_MIN_T"]["BG-RAW-2"] = 6789.0
    stock = deepcopy(scenario.spaces)
    zero_wip = {key: value for key, value in
                scenario.config["TERMINAL_INVENTORY_EXACT_T"].items()
                if "WIP" in key and value == 0}
    assert zero_wip
    for policy in ("EXACT_DAILY", "OPERATING", "EXACT_DAILY"):
        cfg = _configuration(scenario, [(1, "LOUTA", {"RAW-MT": 75})], policy=policy)
        scenario = arrivals.staged_configuration(scenario, cfg)
        assert scenario.spaces == stock
        assert scenario.config["TERMINAL_INVENTORY_EXACT_T"]["BG-RAW-1"] == 12345
        assert scenario.config["TERMINAL_INVENTORY_MIN_T"]["BG-RAW-2"] == 6789
        assert all(scenario.config["TERMINAL_INVENTORY_EXACT_T"].get(key) == value
                   for key, value in zero_wip.items())


def test_saved_preferences_round_trip_without_saving_inventory(scenario, tmp_path):
    path = tmp_path / "config" / "mine_arrivals.json"
    cfg = _configuration(scenario, [(1, "LOUTA", {"RAW-MT": 87})], enabled=False)
    staged = arrivals.staged_configuration(scenario, cfg)
    before = deepcopy(staged)
    assert arrivals.save_preferences(staged, path) == path
    assert staged == before
    fresh = datasets.end_to_end()
    fresh.space("BG-RAW-1").mass_t = 12345
    assert arrivals.apply_saved_preferences(fresh, path)
    assert arrivals.settings(fresh) == arrivals.settings(staged)
    assert fresh.space("BG-RAW-1").mass_t == 12345
    assert not _managed(fresh)
    assert not arrivals.apply_saved_preferences(fresh, path)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert "spaces" not in stored


def test_enabled_preferences_migrate_deleted_nominal_bt_without_losing_mass(
        scenario, tmp_path):
    path = tmp_path / "preferences.json"
    cfg = _configuration(scenario, [
        (1, "BENGUERIR", {"RAW-TBT": 501, "RAW-MT": 17}),
        (1, "LOUTA", {"RAW-BT-NOMINAL": 333}),
    ])
    staged = arrivals.staged_configuration(scenario, cfg)
    day_one_louta = next(
        row for row in arrivals.settings(staged)["days"]
        if row["day"] == 1 and row["site"] == "LOUTA")
    assert day_one_louta["volumes_t"]["RAW-BT"] == 333
    assert "RAW-BT-NOMINAL" not in day_one_louta["volumes_t"]
    arrivals.save_preferences(staged, path)
    fresh = deepcopy(scenario)
    fresh.space("BG-RAW-1").mass_t = 12345
    fresh.exogenous.append(ExogenousFlow("CUSTOM", "BG-RAW-1", 1, "RAW-TBT"))
    assert arrivals.apply_saved_preferences(fresh, path)
    assert _managed(fresh) == _managed(staged)
    assert fresh.space("BG-RAW-1").mass_t == 12345
    assert any(flow.id == "CUSTOM" for flow in fresh.exogenous)
    restored = scenario_from_json(scenario_to_json(fresh))
    assert arrivals.settings(restored) == arrivals.settings(staged)
    assert restored.exogenous == fresh.exogenous
    assert not arrivals.apply_saved_preferences(fresh, path)


def test_invalid_saved_record_cannot_partially_apply(scenario, tmp_path):
    path = tmp_path / "preferences.json"
    cfg = _configuration(scenario, [(1, "LOUTA", {"RAW-MT": -1})])
    malformed = json.dumps({"version": 1, "scenarios": {scenario.name: cfg}})
    path.write_text(malformed, encoding="utf-8")
    before = deepcopy(scenario)
    with pytest.raises(ValueError):
        arrivals.apply_saved_preferences(scenario, path)
    assert scenario == before
    assert path.read_text(encoding="utf-8") == malformed


@pytest.mark.parametrize("malformed", [
    "{", "[]", '{"version": true, "scenarios": {}}',
    '{"version": 1, "scenarios": []}',
])
def test_bad_preference_file_never_changes_scenario_or_gets_overwritten(scenario, tmp_path, malformed):
    path = tmp_path / "preferences.json"
    path.write_text(malformed, encoding="utf-8")
    before = deepcopy(scenario)
    with pytest.raises(ValueError):
        arrivals.apply_saved_preferences(scenario, path)
    assert scenario == before
    with pytest.raises(ValueError):
        arrivals.save_preferences(scenario, path)
    assert path.read_text(encoding="utf-8") == malformed
    assert scenario == before


def test_write_failure_keeps_previous_file_and_scenario(scenario, tmp_path, monkeypatch):
    path = tmp_path / "preferences.json"
    arrivals.save_preferences(scenario, path)
    previous = path.read_bytes()
    cfg = _configuration(scenario, [(1, "LOUTA", {"RAW-MT": 999})])
    staged = arrivals.staged_configuration(scenario, cfg)
    before = deepcopy(staged)

    def fail_replace(*args):
        raise OSError("simulated write failure")

    monkeypatch.setattr(arrivals.os, "replace", fail_replace)
    with pytest.raises(ValueError, match="simulated write failure"):
        arrivals.save_preferences(staged, path)
    assert path.read_bytes() == previous
    assert staged == before
    assert list(tmp_path.iterdir()) == [path]


def test_missing_preferences_are_read_only(scenario, tmp_path):
    path = tmp_path / "not-created" / "preferences.json"
    before = deepcopy(scenario)
    assert not arrivals.apply_saved_preferences(scenario, path)
    assert scenario == before
    assert not path.parent.exists()
