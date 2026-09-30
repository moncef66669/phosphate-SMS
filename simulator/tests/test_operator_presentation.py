"""Operator-presentation contract.

Engineering evidence keeps its internal status tokens; rendered copy, tables
and Plotly payloads must use concise operator language.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mph_sim import datasets  # noqa: E402
from ui import theme  # noqa: E402
from ui.layout_geometry import geometry_for  # noqa: E402
from ui.process_mimic import (build_process_mimic_state,  # noqa: E402
                              process_mimic_figure)


def test_operator_text_hides_internal_evidence_shorthand():
    samples = (
        "ASSUMED/TBC",
        "DERIVED/TBC split",
        "SCOPE_TBC",
        "RAW-TBC",
        "position TBC",
    )
    for sample in samples:
        assert "tbc" not in theme.operator_text(sample).lower()


def test_every_operator_mimic_payload_hides_internal_evidence_shorthand():
    scenario = datasets.end_to_end()
    state = build_process_mimic_state(scenario)
    for view in ("GLOBAL", "CHAIN", "LOUTA", "BENGUERIR", "MPH"):
        raw = process_mimic_figure(state, geometry_for(scenario, view))
        displayed = theme.operator_figure(raw)
        payload = json.dumps(
            displayed.to_plotly_json(), default=str, ensure_ascii=False)
        assert "tbc" not in payload.lower(), view


def test_internal_evidence_status_is_not_rewritten():
    scenario = datasets.end_to_end()
    statuses = {
        item.evidence.status
        for group in (
            scenario.spaces, scenario.machines, scenario.links,
            scenario.destinations, scenario.grades, scenario.sites,
        )
        for item in group
    }
    assert "TBC" in statuses
