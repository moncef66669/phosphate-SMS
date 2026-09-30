"""Focused checks for the Claude-redesign production SVG viewer."""
from __future__ import annotations

import json
import re

import pytest

from ui.flow_svg import redesign_viewer


SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100"
     width="800" height="400">
  <g class="asset asset--pile" data-asset-id="HP-01"
     data-asset-kind="pile" data-site="MPH" data-grade="TBT">
    <rect x="10" y="20" width="70" height="20"/>
    <rect class="hover-target" x="10" y="20" width="70" height="20"
          fill="transparent" data-tooltip="HP-01&#10;tonnage 80,000 t">
      <title>HP-01</title>
    </rect>
  </g>
  <g class="asset asset--destination" data-asset-id="PAP-2"
     data-asset-kind="destination" data-site="MPH" data-state="Future">
    <rect x="140" y="20" width="40" height="20"/>
  </g>
</svg>
"""

METADATA = {
    "timestamp": "13 Jul 2026 · 06:00",
    "basis": "Initial scenario",
    "view": {"id": "MPH", "title": "MPH M16"},
    "filters": {"gradeIds": ["TBT"], "selectedAssetId": None},
    "grades": [{"id": "TBT", "color": "#1c6b73"}],
    "assets": [
        {
            "id": "HP-01",
            "kind": "pile",
            "site": "MPH",
            "label": "HP-01",
            "state": "Available",
            "gradeIds": ["TBT"],
            "details": [
                {"key": "Inventory", "value": "80.0 / 125.0 kt"},
                {"key": "Repère", "value": "R0–R1000"},
            ],
            "endpointAssetIds": ["PAP-2"],
        },
        {
            "id": "PAP-2",
            "kind": "destination",
            "site": "MPH",
            "label": "PAP-2",
            "state": "Future",
            "details": {"Lifecycle": "Future"},
        },
    ],
}


def _embedded_metadata(document: str) -> dict:
    match = re.search(
        r'<script id="viewer-metadata" type="application/json">(.*?)</script>',
        document,
        flags=re.DOTALL,
    )
    assert match
    return json.loads(match.group(1))


def test_redesign_viewer_wraps_production_svg_and_core_interactions():
    document = redesign_viewer.html(
        SVG, metadata=METADATA, height=720, dark=False)

    assert '<body class="theme-light">' in document
    assert 'id="sheet" width="100%" height="100%"' in document
    assert 'width="800"' not in document
    assert "--requested-height" not in document
    assert "height: min(100vh, 720px)" in document
    for control in (
        'id="siteTabs"',
        'id="gradeChips"',
        'id="stateFilter"',
        'id="zoomIn"',
        'id="zoomOut"',
        'id="fitBtn"',
        'id="legendBtn"',
        'id="hoverCard" role="tooltip"',
        'id="details"',
    ):
        assert control in document
    for behavior in (
        "passesFilters",
        "visibleSiteBounds",
        "selectAsset",
        "showHover",
        "pointerdown",
        "wheel",
        "dblclick",
    ):
        assert behavior in document


def test_toolbar_context_and_pap2_are_metadata_driven_and_read_only():
    document = redesign_viewer.html(
        SVG, metadata=METADATA, height=640, dark=False)

    assert _embedded_metadata(document) == METADATA
    assert 'id="pap2Lifecycle"' in document
    assert '<select id="pap2' not in document
    assert "showPap2Lifecycle" in document
    assert "window.updateStockyardState" not in document
    assert "timeline" not in document.lower()
    assert "timeBack" not in document
    assert "timeFwd" not in document


def test_metadata_json_cannot_break_out_of_its_script_node():
    metadata = {
        **METADATA,
        "timestamp": '</script><script>alert("x")</script>\u2028&',
    }
    document = redesign_viewer.html(
        SVG, metadata=metadata, height=720, dark=True)

    assert '<body class="theme-dark">' in document
    assert '</script><script>alert("x")' not in document
    assert r"\u003c/script\u003e\u003cscript\u003ealert" in document
    assert r"\u0026" in document
    assert _embedded_metadata(document) == metadata


def test_viewer_rejects_missing_or_invalid_viewbox():
    with pytest.raises(ValueError, match="viewBox"):
        redesign_viewer.html(
            "<svg></svg>", metadata={}, height=720)
    with pytest.raises(ValueError, match="positive"):
        redesign_viewer.html(
            '<svg viewBox="0 0 0 10"></svg>', metadata={}, height=720)
