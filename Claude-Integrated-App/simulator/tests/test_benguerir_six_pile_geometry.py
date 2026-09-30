"""Focused geometry/SVG checks for the six-pile Benguerir arrangement."""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mph_sim import datasets  # noqa: E402
from ui.flow_svg import svg_mimic  # noqa: E402
from ui.layout_geometry import (benguerir_geometry, geometry_for,  # noqa: E402
                                validate_geometry)
from ui.process_mimic import (MimicOpts, build_process_mimic_state)  # noqa: E402


PILE_IDS = {
    "BG-MINE-1", "BG-MINE-2",
    "BG-RAW-1", "BG-RAW-2",
    "BG-P1", "BG-P2",
}
MINE_MACHINES = {"BG-MINE-ST-1", "BG-MINE-BWR", "BG-MINE-ST-2"}
HOPPER_LINKS = {
    "TM1": "L-BG-HOP-L1",
    "TSM2": "L-BG-HOP-L3",
    "TSM3": "L-BG-HOP-L2",
}
SCREENED_ROUTE_LINKS = {
    "BG-MINE-SCREEN": {
        "L-BG-MINE-RCL-OUT-L1",
        "L-BG-MINE-RCL-OUT-L2",
        "L-BG-MINE-RCL-OUT-L3",
    },
    "BG-SCREEN-RAW-1": {
        "L-BG-SCREEN-STK-OUT-L1",
        "L-BG-SCREEN-STK-OUT-L3",
    },
    "BG-SCREEN-RAW-2": {"L-BG-SCREEN-STK-OUT-L2"},
}


@pytest.fixture(scope="module")
def s3():
    return datasets.end_to_end()


def _bg_piles(geo):
    return {p.pile_id: p for p in geo.piles if p.pile_id in PILE_IDS}


def test_all_views_contain_exact_six_scheduler_bound_piles(s3):
    scenario_ids = {space.id for space in s3.spaces}
    for view in ("BENGUERIR", "CHAIN", "GLOBAL"):
        geo = geometry_for(s3, view)
        piles = _bg_piles(geo)
        assert set(piles) == PILE_IDS, view
        assert len([p for p in geo.piles if p.pile_id in PILE_IDS]) == 6
        assert all(p.modelled for p in piles.values()), view
        assert set(piles) <= scenario_ids
        assert validate_geometry(geo) == [], view


def test_capacities_are_preserved_and_first_pair_is_visually_larger(s3):
    geo = benguerir_geometry(s3)
    piles = _bg_piles(geo)
    scenario_caps = {space.id: space.capacity_t for space in s3.spaces}
    assert {pid: scenario_caps[pid] for pid in PILE_IDS} == {
        "BG-MINE-1": 200_000,
        "BG-MINE-2": 200_000,
        "BG-RAW-1": 200_000,
        "BG-RAW-2": 200_000,
        "BG-P1": 191_512,
        "BG-P2": 191_512,
    }
    assert piles["BG-MINE-1"].visual_width_factor > 1
    norm = svg_mimic.NormGeo(geo, harmonize=True)
    mine_w = norm.box["BG-MINE-1"][2] - norm.box["BG-MINE-1"][0]
    raw_w = norm.box["BG-RAW-1"][2] - norm.box["BG-RAW-1"][0]
    assert mine_w > raw_w

    louta = geometry_for(s3, "LOUTA")
    louta_norm = svg_mimic.NormGeo(louta, harmonize=True)
    lou_w = (louta_norm.box["102MG-SP-01"][2]
             - louta_norm.box["102MG-SP-01"][0])
    assert mine_w > lou_w


def test_mine_machines_and_three_hoppers_have_exact_topology(s3):
    scenario_links = {link.id for link in s3.links}
    for geo in (benguerir_geometry(s3),
                benguerir_geometry(s3, compact=True)):
        machines = {m.machine_id: m for m in geo.machines
                    if m.machine_id in MINE_MACHINES}
        assert set(machines) == MINE_MACHINES
        assert not any(machine.display for machine in machines.values())
        assert geo.rail(machines["BG-MINE-ST-1"].rail_id).flanks == [
            "BG-MINE-1"]
        assert set(geo.rail(machines["BG-MINE-BWR"].rail_id).flanks) == {
            "BG-MINE-1", "BG-MINE-2"}
        assert geo.rail(machines["BG-MINE-ST-2"].rail_id).flanks == [
            "BG-MINE-2"]

        hoppers = {e.label: e for e in geo.equipment if e.kind == "hopper"}
        assert set(hoppers) == set(HOPPER_LINKS)
        for label, link_id in HOPPER_LINKS.items():
            assert hoppers[label].link_ids == [link_id]

        routes = {c.cid: c for c in geo.conveyors
                  if c.cid in SCREENED_ROUTE_LINKS}
        assert set(routes) == set(SCREENED_ROUTE_LINKS)
        for route_id, link_ids in SCREENED_ROUTE_LINKS.items():
            assert set(routes[route_id].link_ids) == link_ids
            assert link_ids <= scenario_links
            assert routes[route_id].planning_enabled
        assert any(e.kind == "gate" and e.label == "SCREEN"
                   for e in geo.equipment)


def test_three_benguerir_washing_cards_are_operating_in_all_views(s3):
    for view in ("BENGUERIR", "CHAIN", "GLOBAL"):
        lines = [
            line for line in geometry_for(s3, view).process_lines
            if line.line_id.startswith("BG-WASH-L")
        ]
        assert [line.line_id for line in lines] == [
            "BG-WASH-L1", "BG-WASH-L2", "BG-WASH-L3"]
        assert {line.status for line in lines} == {"OPERATING"}


def test_svg_groups_the_mine_pair_and_keeps_short_operator_hovers(s3):
    geo = benguerir_geometry(s3)
    state = build_process_mimic_state(s3)
    svg, _ = svg_mimic.build(
        s3, state, geo, MimicOpts(), basis="initial")
    assert "MINE ORE" in svg
    grouped_members = {
        pid for members, _ in svg_mimic.PILE_GROUPS for pid in members}
    assert {"BG-MINE-1", "BG-MINE-2"} <= grouped_members
    assert not {"BG-RAW-1", "BG-RAW-2"} & grouped_members

    titles = [
        html.unescape(title)
        for title in re.findall(r"<title>(.*?)</title>", svg, flags=re.S)
    ]
    for pid in PILE_IDS:
        hover = next(title for title in titles
                     if title.startswith(pid + "\n"))
        assert "\ntonnage " in hover
        assert "\ngrade " in hover
        assert "\nposition R" in hover
    for label in HOPPER_LINKS:
        hover = next(title for title in titles
                     if title.startswith(label + "\nstate "))
        assert "t/h" not in hover and "m³" not in hover
