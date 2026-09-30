"""Acceptance tests for offline/live initial-inventory snapshots.

The importer deliberately has a small neutral schema.  A snapshot is parsed
first, validated against a loaded Scenario, and only then applied.  These
tests pin the important safety property: a bad row must never leave a partly
updated planning state.
"""
from __future__ import annotations

import copy
import csv
import io
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from mph_sim import datasets
from mph_sim.inventory_snapshot import (
    apply_inventory_snapshot,
    inventory_snapshot_from_space_values,
    parse_inventory_snapshot,
    validate_inventory_snapshot,
)
from mph_sim.persistence import scenario_to_json
from mph_sim.optimizer import SolveOptions, solve_plan


NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
CAPTURED = (NOW - timedelta(hours=2)).isoformat()


def _json_snapshot(piles, *, captured_at=CAPTURED, site="MPH",
                   source="YardMaster test export") -> bytes:
    return json.dumps({
        "schema_version": "1.0",
        "captured_at": captured_at,
        "source": source,
        "site": site,
        "piles": piles,
    }).encode("utf-8")


CSV_FIELDS = [
    "captured_at", "source", "site", "pile_id", "record_type",
    "mass_t", "grade", "quality", "x_from", "x_to", "age_days",
]


def _csv_snapshot(rows) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for row in rows:
        base = {name: "" for name in CSV_FIELDS}
        base.update({
            "captured_at": CAPTURED,
            "source": "YardMaster test export",
            "site": "MPH",
        })
        base.update(row)
        writer.writerow(base)
    return buf.getvalue().encode("utf-8-sig")


def _parse_json(piles, **meta):
    return parse_inventory_snapshot(
        _json_snapshot(piles, **meta), "inventory_snapshot.json")


def _apply(sc, snapshot, **kwargs):
    return apply_inventory_snapshot(
        sc, snapshot, now=NOW, max_age_hours=24, **kwargs)


def _validate(sc, snapshot, **kwargs):
    return validate_inventory_snapshot(
        sc, snapshot, now=NOW, max_age_hours=24, **kwargs)


def _issue_text(result) -> str:
    return " ".join(
        f"{getattr(i, 'code', '')} {getattr(i, 'message', i)}"
        for i in result.issues
    ).lower()


def test_legacy_raw_grade_id_is_canonicalized_at_snapshot_boundary():
    sc = datasets.end_to_end()
    snapshot = _parse_json([{
        "pile_id": "110LG-OL1",
        "mass_t": 123_000,
        "grade": "LOU-RAW-BT-GLOBAL",
    }], site="LOUTA")
    assert snapshot.piles[0].grade == "RAW-BT"
    result = _validate(sc, snapshot, require_complete=False)
    assert "unknown_grade" not in _issue_text(result)


def test_json_and_csv_aggregate_snapshots_have_equivalent_effect():
    quality = {"BPL": 72.5, "Cd": 24.0, "moisture": 13.0}
    js = _parse_json([{
        "pile_id": "HP-03", "mass_t": 81_250, "grade": "MT",
        "quality": quality,
    }])
    cs = parse_inventory_snapshot(_csv_snapshot([{
        "pile_id": "HP-03", "record_type": "pile", "mass_t": 81_250,
        "grade": "MT", "quality": json.dumps(quality),
    }]), "inventory_snapshot.csv")

    jres = _apply(datasets.mph_phosphate(), js)
    cres = _apply(datasets.mph_phosphate(), cs)

    assert jres.applied and cres.applied
    jsp, csp = jres.scenario.space("HP-03"), cres.scenario.space("HP-03")
    assert (jsp.mass_t, jsp.grade, jsp.quality) == \
        (csp.mass_t, csp.grade, csp.quality)
    assert jsp.mass_t == pytest.approx(81_250)
    assert jsp.quality == quality
    assert set(jres.changed_space_ids) == set(cres.changed_space_ids) == {"HP-03"}


def test_aggregate_only_replaces_total_and_clears_stale_segments_atomically():
    sc = datasets.mph_phosphate()
    original = scenario_to_json(sc)
    assert any(s.space_id == "HP-01" for s in sc.segments)
    snapshot = _parse_json([{
        "pile_id": "HP-01", "mass_t": 82_000, "grade": "TBT",
        "quality": {"BPL": 73.2, "Cd": 22.8},
    }])

    result = _apply(sc, snapshot)

    assert result.applied
    assert scenario_to_json(sc) == original, "successful apply must return a new scenario"
    assert result.scenario.space("HP-01").mass_t == pytest.approx(82_000)
    assert result.scenario.space("HP-01").state != "empty"
    assert not [s for s in result.scenario.segments if s.space_id == "HP-01"]
    assert result.mass_delta_t == pytest.approx(2_000)


def test_segment_only_snapshot_derives_conserved_total_grade_and_quality():
    snapshot = _parse_json([{
        "pile_id": "HP-02",
        "segments": [
            {"x_from": 0, "x_to": 120, "mass_t": 30_000,
             "grade": "TBT", "quality": {"BPL": 74.0, "Cd": 22.0},
             "age_days": 12.0},
            {"x_from": 120, "x_to": 300, "mass_t": 50_000,
             "grade": "BT", "quality": {"BPL": 68.0, "Cd": 30.0},
             "age_days": 3.0},
        ],
    }])

    result = _apply(datasets.mph_phosphate(), snapshot)

    assert result.applied
    sp = result.scenario.space("HP-02")
    assert sp.mass_t == pytest.approx(80_000)
    assert sp.grade == "BT", "prevalent grade is the largest segment by mass"
    assert sp.quality["BPL"] == pytest.approx(70.25)
    assert sp.quality["Cd"] == pytest.approx(27.0)
    segs = [s for s in result.scenario.segments if s.space_id == "HP-02"]
    assert [(s.x_from, s.x_to, s.mass_t, s.grade, s.age_days) for s in segs] == [
        (0, 120, 30_000, "TBT", 12.0),
        (120, 300, 50_000, "BT", 3.0),
    ]
    assert sum(s.mass_t for s in segs) == pytest.approx(sp.mass_t)
    assert result.mass_delta_t == pytest.approx(-10_000)


def test_flat_csv_segment_rows_preserve_quality_and_age():
    rows = [
        {"pile_id": "HP-05", "record_type": "segment", "x_from": 0,
         "x_to": 100, "mass_t": 25_000, "grade": "TBT",
         "quality": json.dumps({"BPL": 75.0, "Cd": 20.0}), "age_days": 15},
        {"pile_id": "HP-05", "record_type": "segment", "x_from": 100,
         "x_to": 300, "mass_t": 45_000, "grade": "TBT",
         "quality": json.dumps({"BPL": 73.0, "Cd": 24.0}), "age_days": 4},
    ]
    snapshot = parse_inventory_snapshot(
        _csv_snapshot(rows), "yardmaster_space_values.csv")

    result = _apply(datasets.mph_phosphate(), snapshot)

    assert result.applied
    segs = [s for s in result.scenario.segments if s.space_id == "HP-05"]
    assert [s.age_days for s in segs] == [15, 4]
    assert segs[0].quality == {"BPL": 75.0, "Cd": 20.0}
    assert result.scenario.space("HP-05").mass_t == pytest.approx(70_000)


def test_declared_total_with_segments_is_not_double_counted():
    snapshot = _parse_json([{
        "pile_id": "HP-02", "mass_t": 80_000, "grade": "BT",
        "segments": [
            {"x_from": 0, "x_to": 150, "mass_t": 40_000,
             "grade": "BT", "quality": {"BPL": 68.0}, "age_days": 4},
            {"x_from": 150, "x_to": 300, "mass_t": 40_000,
             "grade": "BT", "quality": {"BPL": 68.0}, "age_days": 2},
        ],
    }])

    result = _apply(datasets.mph_phosphate(), snapshot)

    assert result.applied
    assert result.scenario.space("HP-02").mass_t == pytest.approx(80_000)
    assert sum(s.mass_t for s in result.scenario.segments
               if s.space_id == "HP-02") == pytest.approx(80_000)
    assert result.mass_delta_t == pytest.approx(-10_000)


def test_declared_total_and_segment_sum_must_reconcile():
    sc = datasets.mph_phosphate()
    snapshot = _parse_json([{
        "pile_id": "HP-02", "mass_t": 81_500, "grade": "BT",
        "segments": [
            {"x_from": 0, "x_to": 150, "mass_t": 40_000,
             "grade": "BT", "quality": {}, "age_days": 4},
            {"x_from": 150, "x_to": 300, "mass_t": 40_000,
             "grade": "BT", "quality": {}, "age_days": 2},
        ],
    }])

    validation = _validate(sc, snapshot)
    result = _apply(sc, snapshot)

    assert validation.has_errors
    assert "reconcil" in _issue_text(validation) or "sum" in _issue_text(validation)
    assert not result.applied and result.issues
    assert result.mass_delta_t == pytest.approx(0)


def test_external_pile_mapping_is_explicit_and_unknown_ids_fail():
    sc = datasets.mph_phosphate()
    snapshot = _parse_json([{
        "pile_id": "SMS-M16-H01", "mass_t": 85_000, "grade": "TBT",
        "quality": {"BPL": 74.0},
    }])

    bad = _validate(sc, snapshot)
    good = _apply(sc, snapshot, mappings={"SMS-M16-H01": "HP-01"})

    assert bad.has_errors and "SMS-M16-H01".lower() in _issue_text(bad)
    assert good.applied
    assert set(good.changed_space_ids) == {"HP-01"}
    assert good.scenario.space("HP-01").mass_t == pytest.approx(85_000)


def test_mapping_to_an_unknown_internal_pile_is_rejected():
    sc = datasets.mph_phosphate()
    before = scenario_to_json(sc)
    snapshot = _parse_json([{
        "pile_id": "SMS-M16-H01", "mass_t": 85_000, "grade": "TBT",
    }])

    result = _apply(sc, snapshot, mappings={"SMS-M16-H01": "HP-99"})

    assert not result.applied and result.issues
    assert "HP-99".lower() in _issue_text(result)
    assert scenario_to_json(sc) == before


def test_recent_snapshot_is_valid_and_stale_snapshot_is_rejected():
    sc = datasets.mph_phosphate()
    recent = _parse_json([{"pile_id": "HP-01", "mass_t": 80_000,
                           "grade": "TBT"}],
                         captured_at=(NOW - timedelta(hours=24)).isoformat())
    stale = _parse_json([{"pile_id": "HP-01", "mass_t": 80_000,
                          "grade": "TBT"}],
                        captured_at=(NOW - timedelta(hours=24, seconds=1)).isoformat())

    assert not _validate(sc, recent).has_errors, \
        "a snapshot exactly on the configured freshness boundary is accepted"
    bad = _validate(sc, stale)
    assert bad.has_errors
    assert "stale" in _issue_text(bad) or "fresh" in _issue_text(bad)


def test_partial_snapshot_changes_only_listed_piles():
    sc = datasets.mph_phosphate()
    hp02_before = copy.deepcopy(sc.space("HP-02"))
    hp02_segments = copy.deepcopy(
        [s for s in sc.segments if s.space_id == "HP-02"])
    snapshot = _parse_json([{
        "pile_id": "HP-01", "mass_t": 84_000, "grade": "TBT",
    }])

    result = _apply(sc, snapshot, require_complete=False)

    assert result.applied
    assert set(result.changed_space_ids) == {"HP-01"}
    assert result.scenario.space("HP-02") == hp02_before
    assert [s for s in result.scenario.segments if s.space_id == "HP-02"] == \
        hp02_segments


def test_require_complete_rejects_missing_site_piles_without_mutation():
    sc = datasets.mph_phosphate()
    before = scenario_to_json(sc)
    snapshot = _parse_json([{
        "pile_id": "HP-01", "mass_t": 84_000, "grade": "TBT",
    }])

    result = _apply(sc, snapshot, require_complete=True)

    assert not result.applied and result.issues
    assert "complete" in _issue_text(result) or "missing" in _issue_text(result)
    assert set(result.changed_space_ids) == set()
    assert result.mass_delta_t == pytest.approx(0)
    assert scenario_to_json(sc) == before
    assert scenario_to_json(result.scenario) == before


def test_one_invalid_pile_aborts_the_entire_multi_pile_snapshot():
    sc = datasets.mph_phosphate()
    before = scenario_to_json(sc)
    snapshot = _parse_json([
        {"pile_id": "HP-01", "mass_t": 83_000, "grade": "TBT",
         "quality": {"BPL": 73.0}},
        {"pile_id": "HP-02", "mass_t": 200_000, "grade": "BT",
         "quality": {"BPL": 68.0}},       # capacity is 125,000 t
    ])

    result = _apply(sc, snapshot)

    assert not result.applied and result.issues
    assert "capacity" in _issue_text(result)
    assert set(result.changed_space_ids) == set()
    assert result.mass_delta_t == pytest.approx(0)
    assert scenario_to_json(sc) == before
    assert scenario_to_json(result.scenario) == before


def test_zero_mass_snapshot_sets_empty_state_without_changing_configuration():
    sc = datasets.mph_phosphate()
    old = copy.deepcopy(sc.space("HP-02"))
    snapshot = _parse_json([{"pile_id": "HP-02", "mass_t": 0}])

    result = _apply(sc, snapshot)

    assert result.applied
    sp = result.scenario.space("HP-02")
    assert sp.mass_t == 0 and sp.state == "empty"
    assert not [s for s in result.scenario.segments if s.space_id == "HP-02"]
    assert sp.capacity_t == old.capacity_t
    assert sp.site == old.site and sp.area == old.area
    assert sp.safety_stock_t == old.safety_stock_t
    assert sp.designated_grade == old.designated_grade
    assert sp.evidence == old.evidence


def test_xlsx_piles_and_segments_sheets_and_provenance_roundtrip():
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pd.DataFrame([
            {"key": "captured_at", "value": CAPTURED},
            {"key": "source", "value": "YardMaster workbook"},
            {"key": "site", "value": "MPH"},
        ]).to_excel(writer, sheet_name="metadata", index=False)
        pd.DataFrame([{
            "pile_id": "HP-05", "mass_t": 70_000, "grade": "TBT",
        }]).to_excel(writer, sheet_name="piles", index=False)
        pd.DataFrame([
            {"pile_id": "HP-05", "x_from": 0, "x_to": 100,
             "mass_t": 25_000, "grade": "TBT", "age_days": 10},
            {"pile_id": "HP-05", "x_from": 100, "x_to": 300,
             "mass_t": 45_000, "grade": "TBT", "age_days": 2},
        ]).to_excel(writer, sheet_name="segments", index=False)

    snapshot = parse_inventory_snapshot(buf.getvalue(), "inventory.xlsx")
    result = _apply(datasets.mph_phosphate(), snapshot)

    assert result.applied
    assert result.scenario.space("HP-05").mass_t == pytest.approx(70_000)
    meta = result.scenario.config["INVENTORY_SNAPSHOT"]
    assert meta["source"] == "YardMaster workbook"
    assert meta["payload_sha256"]
    assert result.scenario.sim.start_iso == snapshot.captured_at.isoformat()
    restored = json.loads(scenario_to_json(result.scenario))
    assert restored["config"]["INVENTORY_SNAPSHOT"]["covered_piles"] == ["HP-05"]


def test_ui_commit_invalidates_outputs_only_after_success(monkeypatch):
    from ui import common

    class State(dict):
        __getattr__ = dict.__getitem__
        __setattr__ = dict.__setitem__

    sc = datasets.mph_phosphate()
    snapshot = _parse_json([{
        "pile_id": "HP-01", "mass_t": 81_000, "grade": "TBT",
    }])
    result = _apply(sc, snapshot)
    state = State(
        scenario=sc, scenario_name=sc.name, plan=object(), sim=object(),
        queued_events=[object()], last_solve=1.0, last_solve_result=object(),
        solve_request=True, issues=[], inventory_snapshot_history=[],
        inventory_snapshot_upload_nonce=0,
    )
    monkeypatch.setattr(common, "st", SimpleNamespace(session_state=state))

    assert common.commit_inventory_snapshot(result)
    assert state.scenario.space("HP-01").mass_t == pytest.approx(81_000)
    assert state.plan is None and state.sim is None
    assert state.queued_events == [] and state.solve_request is False
    assert state.inventory_snapshot_upload_nonce == 1
    assert state.inventory_snapshot_history[-1]["covered_piles"] == ["HP-01"]


def test_adapter_space_values_use_the_same_atomic_pipeline():
    sc = datasets.mph_phosphate()
    rows = [{
        "pile": "HP-03", "x_from": 0, "x_to": 300,
        "mass_t": 79_000, "grade": "MT",
        "quality": {"BPL": 70.0, "Cd": 26.0},
    }]
    snapshot = inventory_snapshot_from_space_values(
        rows, captured_at=CAPTURED, source="simulated SpaceValue", site="MPH")
    result = _apply(sc, snapshot)

    assert result.applied
    assert result.scenario.space("HP-03").mass_t == pytest.approx(79_000)
    assert not [s for s in result.scenario.segments if s.space_id == "HP-03"]
    assert result.scenario.config["INVENTORY_SNAPSHOT"]["source"] == \
        "simulated SpaceValue"


def test_solver_initial_inventory_is_the_applied_snapshot_mass():
    sc = datasets.sulphur_minimal()
    applied_masses = (60_100, 40_200, 55_300)
    snapshot = _parse_json([
        {"pile_id": sp.id, "mass_t": mass, "grade": "S-STD"}
        for sp, mass in zip(sc.spaces, applied_masses)
    ], site="MPH")
    result = _apply(sc, snapshot, require_complete=True)
    assert result.applied

    plan = solve_plan(result.scenario, SolveOptions(max_time_s=2))

    assert plan.status in ("OPTIMAL", "FEASIBLE")
    assert [plan.inventory[sp.id][0] for sp in result.scenario.spaces] == \
        list(applied_masses)
