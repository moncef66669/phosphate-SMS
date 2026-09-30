# -*- coding: utf-8 -*-
"""Benguerir washing-line planning cards (28-Jul-2026).

The three cards remain visible in detail and composite views and map to the
three executable campaign lines in the six-pile planning chain.
"""
import sys
from itertools import combinations
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mph_sim import datasets                                      # noqa: E402
from mph_sim.flow_config import PROCESS_LINE_STAGE_MAP            # noqa: E402
from mph_sim.optimizer import build_candidates                    # noqa: E402
from mph_sim.routes import RouteGraph                             # noqa: E402
from ui import hmi_symbols as hs                                  # noqa: E402
from ui.layout_geometry import (ProcessLineGeo, benguerir_geometry,  # noqa: E402
                                geometry_for, validate_geometry)
from ui.process_mimic import (build_process_mimic_state,           # noqa: E402
                              process_mimic_figure)


LINE_IDS = ("BG-WASH-L1", "BG-WASH-L2", "BG-WASH-L3")
EXPECTED = {
    "BG-WASH-L1": ("LINE 1", "OPERATING"),
    "BG-WASH-L2": ("LINE 2", "OPERATING"),
    "BG-WASH-L3": ("LINE 3", "OPERATING"),
}


@pytest.fixture(scope="module")
def s3():
    return datasets.end_to_end()


def _lines(geo):
    """Benguerir washing cards only.

    Composite CHAIN/GLOBAL views also carry the Louta washing cards
    (25-Jul-2026); this Benguerir suite is deliberately scoped to the
    `BG-WASH-*` cards so it keeps testing Benguerir behaviour alone.
    """
    return {line.line_id: line for line in geo.process_lines
            if line.line_id.startswith("BG-WASH")}


def _washing_zone(geo):
    # the Benguerir washing zone is the one naming the filtration/settling
    # stages — the Louta zone in composite views is a different stage
    zones = [z for z in geo.zones
             if "WASHING" in z.label.upper() and "FILTRATION" in z.label.upper()]
    assert len(zones) == 1, (
        f"{geo.site_id}: expected one Benguerir washing zone, got "
        f"{[z.label for z in zones]}")
    return zones[0]


def _annotation_texts(fig):
    return [str(a.text) for a in fig.layout.annotations if a.text]


def test_process_line_status_styles_are_central_and_accessible():
    styles = hs.PROCESS_LINE_STATUS_STYLES
    assert set(styles) == {"OPERATING", "FUTURE", "DISABLED"}
    for status, style in styles.items():
        assert set(style) >= {"label", "icon", "line", "fill", "dash"}
        assert style["label"] == status
        assert style["icon"], f"{status}: colour must be duplicated by an icon"
        assert style["line"] and style["fill"]
    assert styles["OPERATING"] != styles["FUTURE"]
    assert styles["FUTURE"]["dash"] not in (None, "", "solid"), \
        "future status must remain legible without colour"


def test_detail_and_compact_have_exact_three_planning_cards(s3):
    detail = benguerir_geometry(s3)
    compact = benguerir_geometry(s3, compact=True)
    for name, geo in (("detail", detail), ("compact", compact)):
        lines = _lines(geo)
        assert set(lines) == set(LINE_IDS), name
        assert len(geo.process_lines) == 3, name    # Benguerir-only geometry
        assert all(isinstance(line, ProcessLineGeo)
                   for line in geo.process_lines)
        assert {line_id: (line.label, line.status)
                for line_id, line in lines.items()} == EXPECTED


def test_chain_and_global_inherit_the_compact_benguerir_cards(s3):
    for view in ("CHAIN", "GLOBAL"):
        lines = _lines(geometry_for(s3, view))
        assert set(lines) == set(LINE_IDS), view
        assert {line_id: (line.label, line.status)
                for line_id, line in lines.items()} == EXPECTED


def test_process_line_cards_stay_inside_washing_zone_without_overlap(s3):
    views = {
        "BENGUERIR-detail": benguerir_geometry(s3),
        "BENGUERIR-compact": benguerir_geometry(s3, compact=True),
        "CHAIN": geometry_for(s3, "CHAIN"),
        "GLOBAL": geometry_for(s3, "GLOBAL"),
    }
    eps = 1e-9
    for view, geo in views.items():
        zone = _washing_zone(geo)
        bg_lines = list(_lines(geo).values())
        for line in bg_lines:
            assert line.x0 + eps < line.x1 and line.y0 + eps < line.y1, \
                f"{view}/{line.line_id}: invalid rectangle"
            assert zone.x0 - eps <= line.x0 < line.x1 <= zone.x1 + eps, \
                f"{view}/{line.line_id}: outside washing-zone x bounds"
            assert zone.y0 - eps <= line.y0 < line.y1 <= zone.y1 + eps, \
                f"{view}/{line.line_id}: outside washing-zone y bounds"
        # no card of ANY site may overlap another in a composite view
        for a, b in combinations(geo.process_lines, 2):
            separated = (a.x1 <= b.x0 or b.x1 <= a.x0 or
                         a.y1 <= b.y0 or b.y1 <= a.y0)
            assert separated, f"{view}: {a.line_id} overlaps {b.line_id}"


def test_process_line_cards_render_with_named_shapes_and_status_text(s3):
    state = build_process_mimic_state(s3)
    for view, geo in (
            ("BENGUERIR-detail", benguerir_geometry(s3)),
            ("BENGUERIR-compact", benguerir_geometry(s3, compact=True)),
            ("CHAIN", geometry_for(s3, "CHAIN")),
            ("GLOBAL", geometry_for(s3, "GLOBAL"))):
        fig = process_mimic_figure(state, geo)
        shape_names = {str(shape.name) for shape in fig.layout.shapes
                       if shape.name}
        assert {f"process-line:{line_id}" for line_id in LINE_IDS} \
            <= shape_names, view

        texts = _annotation_texts(fig)
        for line_id, (label, status) in EXPECTED.items():
            matches = [text for text in texts
                       if label in text and status in text]
            assert matches, (
                f"{view}/{line_id}: one annotation must duplicate lifecycle "
                f"status in text; annotations={texts}")


def test_process_lines_map_to_executable_campaign_stages(s3):
    a18 = next(a for a in s3.assumptions if a.id == "S3-A18")
    assert "OPERATING" in a18.text
    assert "commissioning/as-built" in a18.text
    assert "machine schedule" in a18.impact

    candidates, _ = build_candidates(s3, RouteGraph.from_scenario(s3))
    stages = {
        candidate.process_stage for candidate in candidates
        if candidate.process_stage.startswith("BEN-")
    }
    assert stages == {
        stage for line_id in LINE_IDS
        for stage in PROCESS_LINE_STAGE_MAP[line_id]
    }


def test_washing_line_geometry_validates_in_all_inherited_views(s3):
    for view in ("BENGUERIR", "CHAIN", "GLOBAL"):
        geo = geometry_for(s3, view)
        assert validate_geometry(geo) == [], view
    assert validate_geometry(benguerir_geometry(s3, compact=True)) == []
