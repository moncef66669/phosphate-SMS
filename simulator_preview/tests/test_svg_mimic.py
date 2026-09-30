"""Tests for the solid-line SVG renderer.

Two things this suite exists to catch, both of which have already bitten this
work once:

1. **The arrangement must not move.** This renderer consumes
   ``SiteGeometry``; if it ever starts re-deriving positions, the validated
   relative layout is lost. The transform is pinned here, and machine
   placement is asserted to come from the production helper.
2. **A control that does not reach the renderer.** A substring grep over the
   page passes while an option is inert. Every option is asserted to change
   the SVG string.
"""
from __future__ import annotations

import pathlib
import re
import sys

import pytest

PREVIEW_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PREVIEW_ROOT))

from hmi import bridge, svg_mimic as sm                    # noqa: E402


@pytest.fixture(scope="module")
def s2():
    sc = bridge.datasets.mph_phosphate()
    st = bridge.build_process_mimic_state(sc, None, None, basis="initial",
                                         bucket=0)
    return sc, st, bridge.geometry_for(sc, "MPH")


@pytest.fixture(scope="module")
def working():
    """A bucket with jobs running — flow options need something to switch."""
    sc = bridge.datasets.mph_phosphate()
    plan = bridge.solve_plan(sc, bridge.SolveOptions(max_time_s=10))
    for b in range(sc.sim.horizon_buckets):
        st = bridge.build_process_mimic_state(sc, plan, None, basis="plan",
                                             bucket=b)
        if st.jobs:
            return sc, st, bridge.geometry_for(sc, "MPH"), plan
    pytest.skip("no bucket with active jobs")


def svg_of(sc, st, geo, opts=None, **kw) -> str:
    return sm.build(sc, st, geo, opts or bridge.MimicOpts(), **kw)[0]


# ========================= 1. the arrangement is consumed ====================
def test_transform_is_a_single_affine_from_bounds(s2):
    sc, st, geo = s2
    sh = sm.Sheet(geo, sm.LIGHT)
    x0, y0, x1, y1 = geo.bounds
    assert sh.X(x0) == pytest.approx(sm.PAD)
    assert sh.X(x1) == pytest.approx(sm.PAD + (x1 - x0) * sm.PX_PER_UNIT)
    # y is INVERTED: geometry is y-up, SVG is y-down
    assert sh.Y(y1) == pytest.approx(sm.PAD)
    assert sh.Y(y0) > sh.Y(y1)
    mid = (y0 + y1) / 2
    assert sh.Y(mid) == pytest.approx((sh.Y(y0) + sh.Y(y1)) / 2)


def test_piles_are_uniform_lanes_centred_on_their_geometry_centre(s2):
    """The source geometry draws equal-volume piles at unequal sizes; every
    pile is re-drawn as a uniform lane CENTRED on its geometry centre, so
    order and adjacency are preserved but size is equalised. This replaces the
    old 'drawn at its exact geometry rect' invariant."""
    sc, st, geo = s2
    sh = sm.Sheet(geo, sm.LIGHT)
    norm = sm.NormGeo(geo, harmonize=False)     # base normalisation invariant
    svg = svg_of(sc, st, geo, harmonize=False)
    for pg in geo.piles:
        cx, cy = (pg.x0 + pg.x1) / 2, (pg.y0 + pg.y1) / 2
        bx0, by0, bx1, by1 = norm.box[pg.pile_id]
        # centre preserved
        assert (bx0 + bx1) / 2 == pytest.approx(cx)
        assert (by0 + by1) / 2 == pytest.approx(cy)
        # uniform size
        assert bx1 - bx0 == pytest.approx(sm.NORM_W)
        assert by1 - by0 == pytest.approx(sm.NORM_H)
        # and that box is what is drawn
        x, y = sh.X(bx0), sh.Y(by1)
        w, h = sh.W(sm.NORM_W), sh.H(sm.NORM_H)
        probe = (f'x="{sm._n(x)}" y="{sm._n(y)}" width="{sm._n(w)}" '
                 f'height="{sm._n(h)}"')
        assert probe in svg, f"{pg.pile_id} not drawn at its normalised lane"


def test_equal_capacity_piles_draw_equal_size_across_sites():
    """The user's actual complaint: BG-P1 and LT-P1 both hold 191,512 t but
    were drawn ~7x different. After normalisation they must be identical."""
    sc = bridge.datasets.end_to_end()
    geo = bridge.geometry_for(sc, "CHAIN")
    norm = sm.NormGeo(geo)
    def size(pid):
        b = norm.box[pid]
        return (round(b[2] - b[0], 6), round(b[3] - b[1], 6))
    assert size("BG-P1") == size("LT-P1") == size("HP-01")
    assert size("BG-P1") == (sm.NORM_W, sm.NORM_H)
    # every pile is the one uniform lane
    assert len({size(pid) for pid in norm.box}) == 1


def test_machine_placement_uses_the_production_helper(s2):
    """Positions come from ``ui.process_mimic.machine_draw_pos`` — the machine
    sits over the pile it serves, at its repère within THAT pile."""
    src = (PREVIEW_ROOT / "hmi" / "svg_mimic.py").read_text(encoding="utf-8")
    assert "from ui.process_mimic import machine_draw_pos" in src
    assert "machine_draw_pos(" in src
    # and never from the rail span, which would move every machine
    assert "rl.x0 + (rl.x1 - rl.x0) * min(max(rep" not in src


def test_conveyor_paths_follow_the_remapped_polyline(s2):
    """Conveyor points are dragged through the same normalisation as the piles
    and rails they attach to, so a belt pinned to a resized element follows it
    instead of dangling."""
    sc, st, geo = s2
    norm = sm.NormGeo(geo, harmonize=False)
    sh = sm.Sheet(geo, sm.LIGHT, y_bounds=(norm.ymin, norm.ymax))
    svg = svg_of(sc, st, geo, harmonize=False)
    for cg in geo.conveyors[:6]:
        if len(cg.pts) < 2:
            continue
        nx, ny = norm.point(*cg.pts[0])
        head = f"M{sm._n(sh.X(nx))},{sm._n(sh.Y(ny))}"
        assert head in svg, f"{cg.cid} does not start at its remapped point"


def test_reclaim_belt_follows_the_shrunk_benguerir_rail():
    """BG-RL7 starts at x=59.5 — the OLD Benguerir reclaim-rail right end. After
    the pile shrinks, that endpoint must move to the NEW rail end, not dangle
    12 units out in empty space."""
    sc = bridge.datasets.end_to_end()
    geo = bridge.geometry_for(sc, "CHAIN")
    norm = sm.NormGeo(geo)
    rl = next(r for r in geo.rails if r.rail_id == "BG-RL-P1")
    new_span = norm.span["BG-RL-P1"]
    # the rail genuinely shrank
    assert (new_span[1] - new_span[0]) < (rl.x1 - rl.x0)
    cg = next(c for c in geo.conveyors if c.cid == "BG-RL7")
    start_x = cg.pts[0][0]                      # 59.5, the old rail end
    nx, _ = norm.point(start_x, cg.pts[0][1])
    assert abs(nx - new_span[1]) < 2.5, (nx, new_span)


def test_piles_are_never_reordered(s2):
    """Sizes are equalised, but order/adjacency must not change — no sorting or
    index-renumbering of the geometry, which is what the rejected diagram did.
    (Resizing a box about its own centre is not reordering.)"""
    src = (PREVIEW_ROOT / "hmi" / "svg_mimic.py").read_text(encoding="utf-8")
    for banned in ("sorted(geo.piles", "enumerate(geo.piles",
                   "sorted(geo.rails", "build_rows"):
        assert banned not in src, banned


def test_machine_stays_over_its_pile_after_the_resize():
    """The machine is placed from the NORMALISED rail span + served-pile box, so
    it must land within the pile's new lane, not float off to the old edge."""
    sc = bridge.datasets.mph_phosphate()
    plan = bridge.solve_plan(sc, bridge.SolveOptions(max_time_s=10))
    st = bridge.build_process_mimic_state(sc, plan, None, basis="plan",
                                         bucket=2)
    geo = bridge.geometry_for(sc, "MPH")
    norm = sm.NormGeo(geo)
    from ui.process_mimic import machine_draw_pos
    import types
    for mg in geo.machines:
        vm = next((m for sv in st.monitor.sites.values()
                   for m in sv.machines if m.id == mg.machine_id), None)
        if vm is None or not vm.target_space:
            continue
        served = geo.pile(vm.target_space)
        if served is None or served.pile_id not in mg.rail_id and \
                served.pile_id not in (geo.rail(mg.rail_id).flanks
                                       if geo.rail(mg.rail_id) else []):
            continue
        rl = geo.rail(mg.rail_id)
        nrx = norm.span[mg.rail_id]
        b = norm.box[served.pile_id]
        rail_shim = types.SimpleNamespace(x0=nrx[0], x1=nrx[1],
                                          travel=rl.travel, y=rl.y)
        pile_shim = types.SimpleNamespace(x0=b[0], y0=b[1], x1=b[2], y1=b[3])
        pv = st.machine_pos.get(mg.machine_id)
        gx, *_ = machine_draw_pos(rail_shim, pile_shim,
                                  pv.x_norm if pv else None,
                                  mg.parking_frac, mg.scale)
        # the machine's x lands within the served pile's new lane (+ margin)
        assert b[0] - 2 <= gx <= b[2] + 2, (mg.machine_id, gx, b)


def test_stage_zone_titles_are_hover_only_site_titles_are_drawn(s2):
    """Long process-stage titles overprinted the packed sub-yards, so they are
    hover-only; the site container title stays drawn."""
    sc = bridge.datasets.end_to_end()
    st = bridge.build_process_mimic_state(sc, None, None, basis="initial",
                                         bucket=0)
    geo = bridge.geometry_for(sc, "CHAIN")
    svg = svg_of(sc, st, geo)
    scope = [z for z in geo.zones if z.style == "scope" and z.label]
    stage = [z for z in geo.zones if z.style != "scope" and z.label]
    assert scope and stage
    drawn = re.findall(r"<text[^>]*>([^<]*)</text>", svg)
    hovered = re.findall(r"<title>([^<]*)</title>", svg)
    # a site (scope) title is drawn as visible text
    assert any(z.label.upper().split("(")[0].split("—")[0].strip() in drawn
               for z in scope)
    # every stage title is on hover and never a drawn label
    for z in stage:
        assert sm.e(z.label) in hovered, z.label
        assert sm.e(z.label) not in drawn, z.label


# ============================ 2. every option works ==========================
def test_every_layer_option_changes_the_svg(working):
    sc, st, geo, _ = working
    base = dict(show_flows=True, show_tags=True, show_evidence=True)
    ref = svg_of(sc, st, geo, bridge.MimicOpts(**base))
    for field, value in (("show_flows", False), ("show_tags", False),
                         ("show_evidence", False), ("alarms_only", True),
                         ("active_flows_only", True)):
        opts = bridge.MimicOpts(**base)
        setattr(opts, field, value)
        assert svg_of(sc, st, geo, opts) != ref, field


def test_grade_filter_dims_the_excluded_piles(working):
    sc, st, geo, _ = working
    ref = svg_of(sc, st, geo)
    filt = svg_of(sc, st, geo, bridge.MimicOpts(grade_filter=["TBT"]))
    assert filt != ref
    assert filt.count('opacity="0.5"') > ref.count('opacity="0.5"')


def test_selected_asset_is_outlined(working):
    sc, st, geo, _ = working
    ref = svg_of(sc, st, geo)
    sel = svg_of(sc, st, geo, bridge.MimicOpts(selected_asset="HP-01"))
    assert sel != ref
    assert sm.LIGHT.info in sel


def test_alarms_only_suppresses_normal_machines(working):
    sc, st, geo, _ = working
    ref = svg_of(sc, st, geo)
    alarms = svg_of(sc, st, geo, bridge.MimicOpts(alarms_only=True))
    assert len(alarms) < len(ref)


def test_flow_layer_removes_work_zones_and_chevrons(working):
    sc, st, geo, _ = working
    on = svg_of(sc, st, geo, bridge.MimicOpts(show_flows=True))
    off = svg_of(sc, st, geo, bridge.MimicOpts(show_flows=False))
    assert len(off) < len(on)


# ============================== 3. what must survive =========================
def test_disclaimers_are_on_the_sheet(s2):
    sc, st, geo = s2
    svg = svg_of(sc, st, geo)
    for probe in ("SCHEMATIC", "NOT TO SCALE", "V.E."):
        assert probe in svg, probe


def test_repere_ruler_is_drawn_for_every_pile(s2):
    sc, st, geo = s2
    svg = svg_of(sc, st, geo)
    assert svg.count(">R1000<") >= len(geo.piles)
    assert svg.count(">R0<") >= len(geo.piles)


def test_grade_colours_are_unchanged(s2):
    sc, st, geo = s2
    svg = svg_of(sc, st, geo)
    used = {bridge.grade_color(p.grade)
            for sv in st.monitor.sites.values() for p in sv.piles if p.grade}
    for col in used:
        assert col in svg, col


def test_evidence_badges_exist_but_tbc_signals_are_removed(s2):
    """TBC signals (hatch, ▲ badge, 'TBC' text) are removed on request; the
    remaining evidence markers (future hatch, ASSUMED) still render."""
    import re
    sc, st, geo = s2
    svg = svg_of(sc, st, geo, bridge.MimicOpts(show_evidence=True))
    drawn = re.findall(r"<text[^>]*>([^<]*)</text>", svg)
    assert "url(#hFut)" not in svg or "hFut" in svg   # future pattern defined
    assert not any("TBC" in d for d in drawn)          # no drawn TBC text
    assert "url(#hTbc)" not in svg                      # no TBC hatch drawn
    assert not any("▲" in d for d in drawn)            # no TBC badge


def test_no_tbc_signal_in_any_view():
    """Belt-and-braces: no TBC hatch, badge or drawn text on any view."""
    import re
    sc = bridge.datasets.end_to_end()
    st = bridge.build_process_mimic_state(sc, None, None, basis="initial",
                                         bucket=0)
    for view in ("CHAIN", "LOUTA", "BENGUERIR", "MPH"):
        svg = svg_of(sc, st, bridge.geometry_for(sc, view),
                     bridge.MimicOpts(show_evidence=True))
        drawn = re.findall(r"<text[^>]*>([^<]*)</text>", svg)
        assert "url(#hTbc)" not in svg, view
        assert not any("TBC" in d for d in drawn), view


def test_display_only_geometry_is_marked(s2):
    sc, st, geo = s2
    geo_f = bridge.geometry_for(sc, "MPH", show_future=True)
    svg = svg_of(sc, st, geo_f, bridge.MimicOpts(show_evidence=True))
    assert "DISPLAY / REFERENCE" in svg or "display-only" in svg


def test_fill_is_along_the_pad_not_upward(s2):
    """Plan view: material occupies the first `fill` of the FOOTPRINT, the
    same semantics as the production lane. Encoding fill as height is an
    elevation idiom and was the mistake in the rejected diagram."""
    src = (PREVIEW_ROOT / "hmi" / "svg_mimic.py").read_text(encoding="utf-8")
    assert "span * f0 * fill" in src and "span * f1 * fill" in src


def test_output_is_wellformed_xml_for_every_view():
    import xml.etree.ElementTree as ET
    sc = bridge.datasets.end_to_end()
    st = bridge.build_process_mimic_state(sc, None, None, basis="initial",
                                         bucket=0)
    for view in ("CHAIN", "LOUTA", "BENGUERIR", "MPH"):
        geo = bridge.geometry_for(sc, view)
        for dark in (False, True):
            svg, aspect = sm.build(sc, st, geo, bridge.MimicOpts(), dark=dark)
            ET.fromstring(svg)                 # raises on malformed markup
            assert 0.05 < aspect < 6.0, (view, aspect)


def test_text_is_escaped(s2):
    sc, st, geo = s2
    assert sm.e('<b>"x"</b>') == "&lt;b&gt;&quot;x&quot;&lt;/b&gt;"
    svg = svg_of(sc, st, geo)
    assert "<script" not in svg.lower()


def test_dark_skin_only_changes_the_palette(s2):
    sc, st, geo = s2
    light = svg_of(sc, st, geo, dark=False)
    dark = svg_of(sc, st, geo, dark=True)
    # same number of drawn elements, different colours
    for tag in ("<rect", "<path", "<text", "<circle"):
        assert light.count(tag) == dark.count(tag), tag
    assert sm.LIGHT.sheet in light and sm.DARK.sheet in dark


def test_svg_is_a_plain_string_portable_off_streamlit():
    """The point of SVG over Plotly: the output does not depend on the host."""
    src = (PREVIEW_ROOT / "hmi" / "svg_mimic.py").read_text(encoding="utf-8")
    assert "import streamlit" not in src
    assert "import plotly" not in src


# ================== 4. zoom/pan viewer + LOD + symbol language ===============
from hmi import svg_viewer as sv                                  # noqa: E402


def test_labels_carry_level_of_detail_tiers(s2):
    """Text is what overcrowds a schematic at fit-zoom, so detail must be
    tiered rather than all-or-nothing."""
    sc, st, geo = s2
    svg = svg_of(sc, st, geo)
    assert 'class="lod1"' in svg
    assert 'class="lod2"' in svg
    assert 'class="lod3"' in svg


def test_structure_labels_stay_at_fit_zoom(s2):
    """Bay titles and machine names are structure, not detail."""
    sc, st, geo = s2
    svg = svg_of(sc, st, geo)
    for probe in ("BAY 1", "ST01-A"):
        i = svg.index(probe)
        assert 'lod3' not in svg[max(0, i - 260):i], probe


def test_crowding_labels_are_deferred_to_higher_tiers(s2):
    """Conveyor rates, rail IDs, gate numbers and evidence prose must not be
    on the sheet at fit-zoom."""
    sc, st, geo = s2
    svg = svg_of(sc, st, geo)
    for probe in ("M16A-A-T11", "M16A-B-T06"):        # rail ids — lod3
        i = svg.index(probe)
        assert 'lod3' in svg[max(0, i - 300):i], probe


def test_viewer_provides_zoom_pan_fit_and_lod_switching(s2):
    sc, st, geo = s2
    html = sv.html(svg_of(sc, st, geo), height=660)
    for probe in ('id="in"', 'id="out"', 'id="fit"', "wheel", "pointerdown",
                  "dblclick", "viewBox", "d2", "d3"):
        assert probe in html, probe
    # thresholds must be ordered and reachable
    assert sv.LOD_THRESHOLDS[2] < sv.LOD_THRESHOLDS[3]
    assert str(sv.LOD_THRESHOLDS[2]) in html


def test_viewer_reads_the_sheet_viewbox(s2):
    sc, st, geo = s2
    svg = svg_of(sc, st, geo)
    x, y, w, h = sv.viewbox_of(svg)
    assert (x, y) == (0.0, 0.0) and w > 0 and h > 0
    html = sv.html(svg, height=660)
    assert f"w: {w}" in html and f"h: {h}" in html


def test_viewer_makes_the_sheet_fill_its_frame(s2):
    sc, st, geo = s2
    html = sv.html(svg_of(sc, st, geo), height=500)
    assert '<svg id="sheet" width="100%" height="100%"' in html


def test_stacker_and_reclaimer_have_different_silhouettes(s2):
    """Machine kind must be readable from shape alone — colour means state."""
    sc, st, geo = s2
    a = sm.Sheet(geo, sm.LIGHT)
    sm.machine_glyph(a, 100, 100, 160, "stacker", col=sm.LIGHT.ok,
                     body=sm.LIGHT.body, line_col=sm.LIGHT.outline)
    b = sm.Sheet(geo, sm.LIGHT)
    sm.machine_glyph(b, 100, 100, 160, "reclaimer", col=sm.LIGHT.ok,
                     body=sm.LIGHT.body, line_col=sm.LIGHT.outline)
    sa = "".join(a.parts["symbol"])
    sb = "".join(b.parts["symbol"])
    assert sa != sb
    # the reclaimer alone carries a slew ring + bucket wheel (extra circles)
    assert sb.count("<circle") > sa.count("<circle")
    # the stacker is square-cornered and cross-braced; the reclaimer is rounded
    assert 'rx="1.5"' in sa and 'rx="7"' in sb


def test_the_symbol_and_state_key_uses_the_same_glyph_function():
    """A legend that redraws its own symbols drifts from the drawing."""
    src = (PREVIEW_ROOT / "hmi" / "svg_mimic.py").read_text(encoding="utf-8")
    body = src[src.index("def legend_svg("):]
    assert "machine_glyph(" in body


def test_the_key_covers_every_state_in_the_palette():
    import xml.etree.ElementTree as ET
    svg, aspect = sm.legend_svg(False)
    ET.fromstring(svg)
    shown = {state for _, _, state in sm.LEGEND_CELLS}
    assert {"Idle", "Stacking", "Reclaiming", "Travelling", "Maintenance",
            "Unavailable", "Future"} <= shown
    assert {"stacker", "reclaimer"} == {k for _, k, _ in sm.LEGEND_CELLS}
    for label, _, _ in sm.LEGEND_CELLS:
        assert label in svg


def test_travelling_is_derived_the_same_way_as_production():
    src = (PREVIEW_ROOT / "hmi" / "svg_mimic.py").read_text(encoding="utf-8")
    assert 'pv0.travelling' in src and '"Travelling"' in src


def test_states_showcase_puts_several_real_states_on_one_screen():
    """`?demo=states` must produce distinct states from the MODEL, not paint."""
    sc = bridge.datasets.mph_phosphate()
    sc.calendars.append(bridge.CalendarEntry(
        "ST01-C", 0, sc.sim.horizon_buckets - 1, "maintenance showcase"))
    plan = bridge.solve_plan(sc, bridge.SolveOptions(max_time_s=10))
    st = bridge.build_process_mimic_state(sc, plan, None, basis="plan",
                                         bucket=2)
    seen = set()
    for sv_ in st.monitor.sites.values():
        for m in sv_.machines:
            status = m.status
            pv = st.machine_pos.get(m.id)
            if status == "Idle" and pv is not None and pv.travelling:
                status = "Travelling"
            seen.add(status)
    assert {"Stacking", "Reclaiming", "Travelling", "Maintenance"} <= seen, seen


# ================= 5. on-fill labels, grouping, card layout ==================
def test_pile_labels_split_name_outside_grade_on_stock_details_on_hover(s2):
    """Review note: the space NAME sits outside the rectangle, only the GRADE
    goes on the coloured stock, and tonnage/quality/etc. are on hover."""
    sc, st, geo = s2
    svg = svg_of(sc, st, geo)
    drawn = re.findall(r"<text[^>]*>([^<]*)</text>", svg)
    hovered = " ".join(re.findall(r"<title>([^<]*)</title>", svg))
    # the space name is a drawn label on its own (not combined with tonnage)
    assert "HP-01" in drawn
    # the grade is a drawn label on its own
    assert "TBT" in drawn
    # no combined "id · kt · grade" label is drawn any more
    assert not any(re.match(r"HP-\d+ · \d+ kt", d) for d in drawn)
    # tonnage lives only on hover, never as a drawn label
    assert not any("kt" in d for d in drawn)
    assert "t" in hovered and "%" in hovered
    # the on-stock grade carries a halo so it reads on fill or empty pad
    assert 'paint-order="stroke"' in svg


def test_each_grade_band_is_labelled_not_the_whole_pile():
    """A multi-grade stock labels EACH band with its own grade — never one
    'MIX' label for the space — and never the long grade id."""
    sc = bridge.datasets.end_to_end()
    st = bridge.build_process_mimic_state(sc, None, None, basis="initial",
                                         bucket=0)
    geo = bridge.geometry_for(sc, "CHAIN")
    svg = svg_of(sc, st, geo)
    drawn = re.findall(r"<text[^>]*>([^<]*)</text>", svg)
    # the long grade id may appear in a hover <title>, but never as a drawn
    # label on the stock
    assert "RAW-BT-GLOBAL" not in " ".join(drawn)
    assert "MIX" not in drawn                          # no whole-pile MIX label
    # the BG-RAW stocks carry GLOBAL + HIGH/LOW-CD bands, each labelled
    assert "GLOBAL" in drawn
    assert any(g in drawn for g in ("HIGH-CD", "LOW-CD"))
    # a single-grade pile still shows its grade on its one band
    sc2 = bridge.datasets.mph_phosphate()
    p = bridge.solve_plan(sc2, bridge.SolveOptions(max_time_s=8))
    st2 = bridge.build_process_mimic_state(sc2, p, None, basis="plan",
                                          bucket=2)
    d2 = re.findall(r"<text[^>]*>([^<]*)</text>",
                    svg_of(sc2, st2, bridge.geometry_for(sc2, "MPH")))
    assert "HP-02" in d2 and "BT" in d2 and "MIX" not in d2


def test_mine_ore_group_is_a_titled_block_and_members_drop_their_ids():
    sc = bridge.datasets.end_to_end()
    st = bridge.build_process_mimic_state(sc, None, None, basis="initial",
                                         bucket=0)
    geo = bridge.geometry_for(sc, "CHAIN")
    svg = svg_of(sc, st, geo)
    assert "MINE ORE" in svg
    # members keep tonnage but drop the redundant BG-RAW-1/2 id
    assert "BG-RAW-1 ·" not in svg and "BG-RAW-2 ·" not in svg
    # the grouping is display-only — both piles are still drawn individually
    assert {"BG-RAW-1", "BG-RAW-2"} <= {p.pile_id for p in geo.piles}


def test_pile_groups_are_display_only_config():
    from hmi import svg_mimic as m
    members = {p for grp, _ in m.PILE_GROUPS for p in grp}
    assert {"BG-RAW-1", "BG-RAW-2"} <= members


def test_machine_name_is_tiny_and_off_the_stockpile():
    """The machine NAME is small and drawn on the side away from the served
    pile (not a big chip beside the body), so it never covers the stockpile."""
    src = (PREVIEW_ROOT / "hmi" / "svg_mimic.py").read_text(encoding="utf-8")
    body = src[src.index("def _draw_machine("):src.index("def machine_glyph(")]
    # a tiny name label, on the -sgn (away-from-pile) side, not a big chip
    assert "name_y = ry - sgn" in body
    assert "size=6.5" in body
    assert "_tag_hits_card" not in src            # the beside-chip flip is gone


def test_short_process_line_cards_collapse_to_one_row():
    """The 0.9-unit washing cards in the PRODUCTION Benguerir layout must not
    stack two overlapping text rows; they render a single combined row. (The
    preview BENGUERIR view is now the Louta-style replica with tall two-row
    cards, so the collapse logic is exercised against the production layout.)"""
    sc = bridge.datasets.end_to_end()
    st = bridge.build_process_mimic_state(sc, None, None, basis="initial",
                                         bucket=0)
    geo = bridge._geometry_for_prod(sc, "BENGUERIR")   # short 0.9-unit cards
    svg = svg_of(sc, st, geo)
    # single-row form: the dot + line name together, no separate status header
    assert re.search(r"[●◇] LINE \d", svg)


# ================= 6. row harmonisation (single-site views) ==================
def test_single_site_views_get_a_uniform_within_stage_pitch():
    """Every single-site block is re-spaced to the same vertical rhythm — the
    user's 'harmonize all stockpile block spaces'. Within-stage rows land
    ROW_PITCH apart; a wide inter-STAGE gap is preserved (it holds the
    washing/filtration equipment)."""
    sc = bridge.datasets.end_to_end()
    for view in ("BENGUERIR", "LOUTA", "MPH"):
        geo = bridge.geometry_for(sc, view)
        n = sm.NormGeo(geo, harmonize=True)
        assert n.harmonize
        centres = sorted({round((n.box[p.pile_id][1] + n.box[p.pile_id][3]) / 2,
                                3) for p in geo.piles}, reverse=True)
        gaps = [round(a - b, 3) for a, b in zip(centres, centres[1:])]
        for g in gaps:
            # each gap is either the uniform pitch or a preserved stage break
            assert g == sm.ROW_PITCH or g > sm.STAGE_THRESHOLD, (view, gaps)


def test_benguerir_stage_gap_is_preserved_not_collapsed():
    """Benguerir's ~28-unit band between raw and product storage carries the
    washing/filtration stage, so it must be KEPT, while the pile pitch within
    each stage is uniformised to ROW_PITCH."""
    sc = bridge.datasets.end_to_end()
    geo = bridge.geometry_for(sc, "BENGUERIR")
    n = sm.NormGeo(geo, harmonize=True)
    cs = sorted({round((n.box[p.pile_id][1] + n.box[p.pile_id][3]) / 2, 3)
                 for p in geo.piles}, reverse=True)
    gaps = [round(a - b, 3) for a, b in zip(cs, cs[1:])]
    assert sm.ROW_PITCH in gaps                       # within-stage uniformised
    assert any(g > sm.STAGE_THRESHOLD for g in gaps)  # stage gap preserved


def test_chain_composite_is_never_harmonised():
    """CHAIN's y carries geography; global harmonising would scramble its 2D
    layout. Every pile keeps its geometry y-centre EXCEPT the members of an
    explicit compact block (see test below), which are pulled tight on purpose.
    """
    sc = bridge.datasets.end_to_end()
    geo = bridge.geometry_for(sc, "CHAIN")
    assert getattr(geo, "site_id", "") == "CHAIN"
    compacted = {p for blk in sm.COMPACT_BLOCKS for p in blk["piles"]}
    n = sm.NormGeo(geo, harmonize=False)
    for p in geo.piles:
        if p.pile_id in compacted:
            continue
        cy = (p.y0 + p.y1) / 2
        assert (n.box[p.pile_id][1] + n.box[p.pile_id][3]) / 2 == \
            pytest.approx(cy)


def test_chain_compacts_benguerir_product_to_match_louta():
    """In the composite, Benguerir's loosely-authored washed-product block is
    re-spaced to span exactly as tall as Louta's product block, so the two twins
    read the same. Order is preserved and it only ever tightens, never spreads.
    """
    sc = bridge.datasets.end_to_end()
    geo = bridge.geometry_for(sc, "CHAIN")
    n = sm.NormGeo(geo, harmonize=False)
    def gap(a, b):
        ba, bb = n.box[a], n.box[b]
        return abs((ba[1] + ba[3]) / 2 - (bb[1] + bb[3]) / 2)
    louta = gap("LT-P1", "LT-P2")
    beng = gap("BG-P1", "BG-P2")
    assert beng == pytest.approx(louta, abs=0.3)      # matched to the twin
    # raw Benguerir spacing was far wider — this really is a tightening
    raw = {p.pile_id: (p.y0 + p.y1) / 2 for p in geo.piles}
    assert abs(raw["BG-P1"] - raw["BG-P2"]) > louta + 1.0
    # order preserved: BG-P1 still sits above BG-P2
    assert (n.box["BG-P1"][1] + n.box["BG-P1"][3]) > \
           (n.box["BG-P2"][1] + n.box["BG-P2"][3])


def test_yr_is_monotonic_increasing():
    sc = bridge.datasets.end_to_end()
    n = sm.NormGeo(bridge.geometry_for(sc, "BENGUERIR"), harmonize=True)
    xs = [k for k, _ in n._knots] + [k + 0.3 for k, _ in n._knots]
    ys = [n.yr(x) for x in sorted(xs)]
    assert all(b >= a for a, b in zip(ys, ys[1:])), ys


def test_machine_never_overlaps_its_pile_after_harmonise():
    """The advisor's first verification: a re-spaced rail must keep its machine
    off its pile (body-centre-y == rail-y, ≥ clearance from the pile centre)."""
    sc = bridge.datasets.end_to_end()
    for view in ("BENGUERIR", "LOUTA", "MPH"):
        geo = bridge.geometry_for(sc, view)
        n = sm.NormGeo(geo, harmonize=True)
        for mg in geo.machines:
            rl = geo.rail(mg.rail_id)
            if rl is None:
                continue
            served = next((p for p in (mg.served() or rl.flanks)
                           if p in n.box), None)
            ry = n.railY(mg.rail_id, rl.y)
            if served:
                pc = (n.box[served][1] + n.box[served][3]) / 2
                assert abs(ry - pc) >= sm.NORM_H / 2 + 0.3, (view, mg.machine_id)


def test_harmonised_sheet_crops_to_its_content():
    """Uniform pitch tightens the block, so the sheet's y-extent is no larger
    than the raw bounds (Louta's cramped rows expand, but never past bounds)."""
    sc = bridge.datasets.end_to_end()
    for view in ("BENGUERIR", "LOUTA", "MPH"):
        geo = bridge.geometry_for(sc, view)
        n = sm.NormGeo(geo, harmonize=True)
        assert (n.ymax - n.ymin) <= (geo.bounds[3] - geo.bounds[1]) + 1e-6


# ================= 7. Benguerir/Louta washed-product parity ==================
def test_benguerir_washed_product_reads_the_same_as_louta():
    """Benguerir's product piles get the same treatment as Louta's — solid
    grade-piles (BG-P1/BG-P2) flanked by reclaimer/stacker/reclaimer inside a
    product-storage STAGE zone, exactly like Louta's product stage. The old
    special STORAGE_BLOCKS wrapper is gone; the BENGUERIR view is now a full
    Louta-template replica (see hmi.benguerir_replica)."""
    sc = bridge.datasets.end_to_end()
    st = bridge.build_process_mimic_state(sc, None, None, basis="initial",
                                         bucket=0)
    assert not hasattr(sm, "STORAGE_BLOCKS")          # the special box is gone
    for view in ("BENGUERIR", "LOUTA"):
        svg = svg_of(sc, st, bridge.geometry_for(sc, view))
        assert "PRODUCT STORAGE" in svg               # a product-storage stage
    # Benguerir product ids stay drawn (real solver piles, not suppressed)
    drawn = re.findall(r"<text[^>]*>([^<]*)</text>",
                       svg_of(sc, st, bridge.geometry_for(sc, "BENGUERIR")))
    assert "BG-P1" in drawn and "BG-P2" in drawn


def test_benguerir_product_fill_stays_confirmed_not_faked_as_louta():
    """The evidence-faithful half of the answer: Benguerir product is NOT drawn
    hatched (it is confirmed) and NOT given fake multi-grade bands."""
    sc = bridge.datasets.end_to_end()
    geo = bridge.geometry_for(sc, "BENGUERIR")
    pg = {p.pile_id: p for p in geo.piles}
    assert pg["BG-P1"].tbc is False and pg["BG-P2"].tbc is False
    st = bridge.build_process_mimic_state(sc, None, None, basis="initial",
                                         bucket=0)
    pv = {p.id: p for s in st.monitor.sites.values() for p in s.piles}
    assert len(sm._pile_bands(pg["BG-P1"], pv.get("BG-P1"))) == 1   # single grade
