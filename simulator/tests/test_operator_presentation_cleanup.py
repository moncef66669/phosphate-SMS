"""Operator-presentation contract for the compact domain bar and clean mimic."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from plotly.utils import PlotlyJSONEncoder
from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from conftest import nav_to  # noqa: E402
from mph_sim import datasets  # noqa: E402
from ui import common, theme  # noqa: E402
from ui.layout_geometry import geometry_for  # noqa: E402
from ui.process_mimic import (MimicOpts, build_process_mimic_state,  # noqa: E402
                              process_mimic_figure)


APP = str(ROOT / "app.py")


def _annotation_texts(fig) -> list[str]:
    return [str(annotation.text or "")
            for annotation in fig.layout.annotations]


def _visible_contract_text(root) -> str:
    values: list[str] = []
    element_types = (
        "markdown", "caption", "info", "warning", "error", "success",
        "subheader", "header", "title", "text", "metric", "button",
        "toggle", "checkbox", "radio", "selectbox", "multiselect",
        "segmented_control", "dataframe",
    )
    for element_type in element_types:
        for element in getattr(root, element_type, []):
            for attr in ("value", "label", "options", "help"):
                value = getattr(element, attr, None)
                if value is not None:
                    values.append(str(value))
    return "\n".join(values)


def test_domain_bar_is_compact_and_keeps_the_two_requested_domains():
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert not at.exception, at.exception
    assert at.segmented_control[0].options == ["BG + LO + MPH", "MS"]

    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'width="content"' in source
    assert 'label_visibility="collapsed"' in source


def test_residence_time_is_written_once_per_washing_block():
    scenario = datasets.end_to_end()
    state = build_process_mimic_state(
        scenario, basis="initial", bucket=0)

    expected = {"BENGUERIR": 1, "LOUTA": 1, "GLOBAL": 2}
    for view, count in expected.items():
        fig = process_mimic_figure(
            state, geometry_for(scenario, view),
            MimicOpts(show_evidence=True))
        texts = _annotation_texts(fig)
        assert sum("30 min residence" in text
                   for text in texts) == count, view
        assert all("Residence time:" not in text for text in texts), view


def test_operator_text_hides_generic_underscore_suffixed_tbc():
    displayed = theme.operator_text("FOO_TBC")

    assert displayed == "FOO_pending"
    assert "TBC" not in displayed.upper()


def test_rendered_mimic_figures_never_expose_internal_tbc_shorthand():
    scenario = datasets.end_to_end()
    state = build_process_mimic_state(
        scenario, basis="initial", bucket=0)
    for view in ("GLOBAL", "CHAIN", "LOUTA", "BENGUERIR", "MPH"):
        raw = process_mimic_figure(
            state, geometry_for(scenario, view),
            MimicOpts(show_evidence=True))
        public = theme.operator_figure(raw)
        blob = json.dumps(
            public.to_plotly_json(), cls=PlotlyJSONEncoder).upper()
        assert "TBC" not in blob, view


def test_all_streamlit_pages_hide_internal_tbc_shorthand():
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert not at.exception, at.exception

    for page in common.ALL_PAGE_LABELS:
        nav_to(at, page)
        assert not at.exception, (page, at.exception)
        text = _visible_contract_text(at.main) + "\n" + \
            _visible_contract_text(at.sidebar)
        assert "TBC" not in text.upper(), page
