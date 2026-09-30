"""Focused checks for the Claude handoff drawing and production binding."""
from __future__ import annotations

import json
import inspect
import re

import pytest

from ui.flow_svg import redesign_viewer
from ui import process_mimic


METADATA = {
    "timestamp": "13 Jul 2026 · 06:00",
    "basis": "Plan timeline",
    "site": "CHAIN",
    "initialSite": "MPH",
    "filters": {"gradeIds": ["TBT"]},
    "grades": [
        {"id": "TBT", "label": "TBT", "color": "#1c6b73"},
    ],
    "assets": [
        {
            "id": "HP-01",
            "kind": "pile",
            "site": "MPH",
            "label": "HP-01",
            "massT": 80_000,
            "capacityT": 125_000,
            "fillPct": 64,
            "materialClass": "washed",
            "tsmEquivalentT": 66_000,
            "tsmLabel": "TSM equivalent",
            "repereFrom": 0,
            "repereTo": 1000,
            "gradeBands": [
                {"gradeId": "TBT", "inventoryFraction": 1.0},
            ],
        },
        {
            "id": "ST01-A",
            "kind": "machine",
            "site": "MPH",
            "label": "ST01-A",
            "state": "Stacking",
            "jobId": "J-S",
            "targetId": "HP-01",
            "positionRepere": 350,
            "rateTph": 1_500,
            "maximumRateTph": 2_000,
        },
        {
            "id": "RC01-A",
            "kind": "machine",
            "site": "MPH",
            "label": "RC01-A",
            "state": "Reclaiming",
            "jobId": "J-R",
            "targetId": "HP-05",
            "positionRepere": 600,
            "rateTph": 1_200,
            "maximumRateTph": 2_500,
        },
        {
            "id": "PAP-1",
            "kind": "destination",
            "site": "MPH",
            "lifecycleState": "Available",
            "requestedGrade": "TBT",
            "demandRateTph": 1_500,
            "activeDeliveryRateTph": 1_200,
        },
        {
            "id": "PAP-2",
            "kind": "destination",
            "site": "MPH",
            "lifecycleState": "Future",
            "requestedGrade": "TBT",
            "demandRateTph": 0,
            "configuredDemandRateTph": 1_500,
            "activeDeliveryRateTph": 0,
        },
    ],
    "destinations": [],
}


def _host_data(document: str) -> dict:
    match = re.search(
        r"var HOST_DATA = (.*?);\s*Object\.keys\(HOST_DATA\.gradeColors",
        document,
    )
    assert match
    return json.loads(match.group(1))


def test_viewer_renders_the_actual_claude_handoff_not_the_old_svg_wrapper():
    document = redesign_viewer.html(
        metadata=METADATA, height=720, dark=False)

    assert "var LAYOUT =" in document
    assert "world: { w: 2800, h: 1620 }" in document
    assert "GLOBAL STOCKYARD" in document
    assert "BENGUERIR" in document
    assert "MPH M16" in document
    assert "M16 OUT COLLECTOR" in document
    assert 'id="stage"' in document
    assert 'id="world"' in document
    assert 'data-viewer-height="720"' in document
    assert "__PRODUCTION_DATA__" not in document
    assert "__PRODUCTION_SVG__" not in document
    assert "production-svg" not in document
    for behavior in (
        "connectedSet",
        "showHover",
        "assetAt",
        "mousedown",
        "wheel",
        "dblclick",
    ):
        assert behavior in document


def test_production_state_drives_visual_slots_and_pap2_is_read_only():
    document = redesign_viewer.html(
        metadata=METADATA, height=760, dark=False)
    data = _host_data(document)

    assert "var DATA = clone(HOST_DATA);" in document
    assert data["stateTime"] == METADATA["timestamp"]
    assert data["initialSite"] == "MPH"
    assert data["selectedGrades"] == ["TBT"]
    assert data["gradeColors"]["TBT"] == "#1c6b73"
    pile = next(
        item for item in data["piles"] if item["id"] == "HP-01"
    )
    assert pile["massT"] == 80_000
    assert pile["materialClass"] == "washed"
    assert pile["tsmEquivalentT"] == 66_000
    assert pile["tsmLabel"] == "TSM equivalent"
    assert pile["grades"] == [{
        "gradeId": "TBT",
        "grade": "TBT",
        "massT": 80_000,
        "inventoryShare": 1.0,
        "fraction": 0.64,
        "repereFrom": 0,
        "repereTo": 0,
    }]
    assert "kt physical (" in document
    assert "if (p.tsmEquivalentT != null && p.tsmLabel)" in document
    assert (
        "var occupiedWidth = ref.w * (spaceEnd - spaceStart)"
        " * occupiedFraction" in document
    )
    assert "var bx = occupiedStart + occupiedWidth * startShare" in document
    assert "b.grade + ' · ' + kt(gradeMass)" in document
    assert "physical t/h" in document

    stacker = next(
        item for item in data["machines"] if item["id"] == "M16-ST-A")
    duplicate_reclaimer = next(
        item for item in data["machines"] if item["id"] == "M16-RC-B")
    assert stacker["canonicalId"] == "ST01-A"
    assert stacker["state"] == "Stacking"
    assert duplicate_reclaimer["canonicalId"] == "RC01-A"
    assert duplicate_reclaimer["isPrimary"] is False

    assert 'id="pap2State" disabled' in document
    assert 'id="timeBack" disabled' in document
    assert 'id="timeFwd" disabled' in document
    assert "pap2State').onchange" not in document
    assert "timeBack').onclick" not in document
    assert "timeFwd').onclick" not in document


def test_visual_aliases_do_not_create_new_canonical_assets():
    data = redesign_viewer.handoff_snapshot(METADATA)
    aliases = {item["id"]: item for item in data["machines"]}

    assert aliases["M16-ST-A"]["canonicalId"] == "ST01-A"
    assert aliases["M16-RC-A"]["canonicalId"] == "RC01-A"
    assert aliases["M16-RC-B"]["canonicalId"] == "RC01-A"
    assert aliases["M16-RC-A"]["isPrimary"] is True
    assert aliases["M16-RC-B"]["isPrimary"] is False

    pile_aliases = {item["id"]: item for item in data["piles"]}
    assert pile_aliases["BG-MINE-P1"]["canonicalId"] == "BG-MINE-1"
    assert pile_aliases["BG-RAW-P2"]["canonicalId"] == "BG-RAW-2"


def test_missing_or_unknown_canonical_data_fails_safe():
    data = redesign_viewer.handoff_snapshot({})

    assert all(
        item["state"] == "Unavailable" and not item["hasData"]
        for item in data["machines"]
    )
    assert all(
        item["state"] == "Disabled" and not item["hasData"]
        for item in data["processLines"]
    )
    assert all(
        item["lifecycleState"] == "Disabled" and not item["hasData"]
        for item in data["destinations"]
    )
    assert all(
        item["state"] == "Unavailable"
        and item["capacityTph"] is None
        and not item["hasData"]
        for item in data["routes"]
    )


def test_resolved_disabled_and_reference_states_are_not_presented_as_available():
    metadata = {
        "assets": [
            {
                "id": "PAP-1",
                "kind": "destination",
                "state": "Disabled",
                "lifecycleState": "Available",
            },
            {
                "id": "RC01-A",
                "kind": "machine",
                "state": "Reference only",
            },
        ],
    }
    data = redesign_viewer.handoff_snapshot(metadata)

    pap1 = next(
        item for item in data["destinations"] if item["id"] == "PAP-1")
    rc_a = next(
        item for item in data["machines"] if item["id"] == "M16-RC-A")
    assert pap1["lifecycleState"] == "Disabled"
    assert rc_a["state"] == "Unavailable"


def test_production_json_cannot_break_out_of_the_script():
    metadata = {
        **METADATA,
        "timestamp": '</script><script>alert("x")</script>\u2028&',
    }
    document = redesign_viewer.html(
        metadata=metadata, height=720, dark=False)

    assert '</script><script>alert("x")' not in document
    assert r"\u003c/script\u003e\u003cscript\u003ealert" in document
    assert r"\u0026" in document
    assert _host_data(document)["stateTime"] == metadata["timestamp"]


def test_viewer_rejects_invalid_inputs_and_unapproved_dark_variant():
    with pytest.raises(TypeError, match="metadata"):
        redesign_viewer.html(metadata=[], height=720)
    with pytest.raises(TypeError, match="height"):
        redesign_viewer.html(metadata={}, height="not-a-number")
    with pytest.raises(ValueError, match="no approved dark variant"):
        redesign_viewer.html(metadata={}, height=720, dark=True)


def test_redesign_render_branch_never_builds_the_legacy_svg():
    source = inspect.getsource(process_mimic.render_process_mimic)
    redesign_branch = source.split("if redesign:", 1)[1].split(
        "else:", 1)[0]
    legacy_branch = source.split("if redesign:", 1)[1].split(
        "else:", 1)[1]

    assert "redesign_adapter.build_snapshot" in redesign_branch
    assert "redesign_viewer.html" in redesign_branch
    assert "svg_mimic.build" not in redesign_branch
    assert "svg_mimic.build" in legacy_branch
