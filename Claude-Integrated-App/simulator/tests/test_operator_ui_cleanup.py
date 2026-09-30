"""Regression tests for the operator-facing evidence-text cleanup.

The engineering model keeps its original evidence vocabulary.  Only copies
crossing the Streamlit/Plotly presentation boundary are rewritten.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

from ui import theme  # noqa: E402


APP = ROOT / "app.py"
TBC_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])TBC(?![A-Za-z0-9])",
    flags=re.IGNORECASE,
)


def _assert_operator_clean(value: Any) -> None:
    text = str(value)
    assert not TBC_TOKEN.search(text), text


def _visible_text(root) -> str:
    """Collect operator-visible AppTest copy, controls and tables."""
    values: list[str] = []
    for element_type in (
        "title", "header", "subheader", "markdown", "caption", "info",
        "warning", "error", "success", "text",
    ):
        values.extend(
            str(element.value)
            for element in getattr(root, element_type, ())
        )
    for element_type in (
        "button", "radio", "segmented_control", "selectbox", "multiselect",
        "toggle", "number_input", "text_input",
    ):
        for element in getattr(root, element_type, ()):
            values.append(str(element.label))
            if hasattr(element, "options"):
                values.append(str(element.options))
            if hasattr(element, "value"):
                values.append(str(element.value))
    values.extend(
        str(element.value)
        for element in getattr(root, "dataframe", ())
    )
    return "\n".join(values)


def test_operator_text_removes_evidence_shorthand_without_mutating_source():
    raw = (
        "ASSUMED/TBC · DERIVED/TBC · SCOPE_TBC · "
        "DOCUMENT_RECONCILIATION_TBC · route TBC"
    )

    displayed = theme.operator_text(raw)

    _assert_operator_clean(displayed)
    assert "planning basis" in displayed
    assert "derived" in displayed
    assert "scope pending" in displayed
    assert "document reconciliation pending" in displayed
    assert "route pending" in displayed
    assert raw.endswith("route TBC")


def test_operator_frame_sanitises_a_deep_copy_and_keeps_raw_frame_unchanged():
    raw = pd.DataFrame(
        {
            "Evidence TBC": [
                "ASSUMED/TBC",
                {
                    "status": "TBC",
                    "notes": ["DERIVED/TBC", ("route TBC",)],
                },
            ],
            "Value": ["unchanged", "SCOPE_TBC"],
        },
        index=["ROW TBC", "confirmed-row"],
    )
    raw_snapshot = raw.copy(deep=True)

    displayed = theme.operator_frame(raw)

    _assert_operator_clean(displayed.columns.tolist())
    _assert_operator_clean(displayed.index.tolist())
    _assert_operator_clean(displayed.to_dict())
    assert raw.equals(raw_snapshot)
    assert raw.columns[0] == "Evidence TBC"
    assert raw.iloc[1, 0]["status"] == "TBC"
    assert raw.iloc[1, 0]["notes"][0] == "DERIVED/TBC"


def test_restore_operator_frame_restores_only_untouched_source_tokens():
    raw = pd.DataFrame(
        {
            "Basis TBC": ["ASSUMED/TBC", "route TBC"],
            "Rate": [1000, 1200],
        }
    )
    displayed = theme.operator_frame(raw)
    displayed.loc[1, "Basis pending"] = "operator confirmed"

    restored = theme.restore_operator_frame(displayed, raw)

    assert restored.columns.tolist() == ["Basis TBC", "Rate"]
    assert restored.loc[0, "Basis TBC"] == "ASSUMED/TBC"
    assert restored.loc[1, "Basis TBC"] == "operator confirmed"
    assert raw.loc[1, "Basis TBC"] == "route TBC"


def test_operator_figure_sanitises_visible_and_hover_payloads_on_a_copy():
    raw = go.Figure(
        go.Bar(
            x=[1, 2],
            y=[2, 3],
            name="TBC route",
            text=["ASSUMED/TBC", "derived/TBC"],
            hovertext=["position TBC", "scope_tbc"],
            customdata=[
                ["DOCUMENT_RECONCILIATION_TBC"],
                [{"evidence": "TBC"}],
            ],
        )
    )
    raw.update_layout(
        title="TBC demonstration",
        annotations=[
            dict(x=1, y=2, text="machine TBC", showarrow=False),
        ],
    )
    raw_json = raw.to_json()

    displayed = theme.operator_figure(raw)

    _assert_operator_clean(displayed.to_json())
    assert raw.to_json() == raw_json
    assert TBC_TOKEN.search(raw_json)
    assert displayed is not raw


def test_domain_navbar_source_contract_is_compact_and_uses_current_dom():
    app_source = APP.read_text(encoding="utf-8")
    theme_source = (ROOT / "ui" / "theme.py").read_text(encoding="utf-8")
    navbar_source = app_source.split(
        "# =========================== DOMAIN NAVBAR", 1
    )[1].split(
        "# ============================= CONTEXT BAR", 1
    )[0]

    assert re.search(r'width\s*=\s*"content"', navbar_source)
    assert re.search(
        r'label_visibility\s*=\s*"collapsed"', navbar_source
    )
    assert 'data-testid="stButtonGroup"' in theme_source
    assert '[role="radiogroup"]' in theme_source
    assert '[role="radio"]' in theme_source
    assert "stSegmentedControl" not in theme_source


def test_default_operator_ui_does_not_render_internal_tbc_shorthand():
    at = AppTest.from_file(str(APP), default_timeout=300)
    at.run()

    assert not at.exception, at.exception
    _assert_operator_clean(_visible_text(at.sidebar))
    _assert_operator_clean(_visible_text(at.main))

