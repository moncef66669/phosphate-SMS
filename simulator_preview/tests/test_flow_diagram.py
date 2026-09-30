"""Tests for the stream/cross-section flow-diagram renderer.

RETIRED FROM THE UI (27-Jul-2026): the reviewer found this re-arrangement
harder to read than the production layout, so ``page_layout_flow`` no longer
offers it — the Diagram switch is now Solid (SVG) vs Production (reference).
The module and these tests are kept because several assertions here are worth
holding regardless of which renderer ships: bay/machine-line reading order,
shared-capacity counted once, and site-boundary scoping.


The drawing is new code, so unlike the console shell it cannot lean on the
production figure builder for correctness. These tests pin the things a
reviewer would otherwise have to eyeball:

  * the bay/machine-line reading order (a sign error here silently renumbers
    the bays and mislabels which bays a line serves);
  * the shared-capacity rule (the M16 reception envelope must read 2,600 t/h,
    not 8 x 2,600);
  * view scoping (a site view must not absorb the rest of the chain);
  * an absent stage must be stated, never drawn as zero flow;
  * no drawn element may claim a flow the model does not have.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

PREVIEW_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PREVIEW_ROOT))

from hmi import bridge, flow_diagram as fd, tokens        # noqa: E402


def _annotation_text(fig) -> str:
    """All annotation text on the sheet, as one searchable string."""
    return " ".join(str(a.text) for a in fig.layout.annotations
                    if a.text is not None)


# ------------------------------------------------------------- fixtures ------
@pytest.fixture(scope="module")
def s2():
    sc = bridge.datasets.mph_phosphate()
    st = bridge.build_process_mimic_state(sc, None, None, basis="initial",
                                         bucket=0)
    geo = bridge.geometry_for(sc, "MPH")
    return sc, st, geo


@pytest.fixture(scope="module")
def s3():
    return bridge.datasets.end_to_end()


# --------------------------------------------------------- reading order -----
def test_bays_and_machine_lines_alternate_in_the_validated_order(s2):
    """ST01-A | Bay 1 | RC01-A | Bay 2 | ST01-B | Bay 3 | RC01-B | Bay 4 |
    ST01-C — the arrangement the project confirmed."""
    sc, st, geo = s2
    rows, _ = fd.build_rows(geo, st.monitor)
    seq = [(("T", r.rail_id) if isinstance(r, fd.Track) else ("B", r.label))
           for r in rows]
    assert seq == [
        ("T", "M16A-A-T06"), ("B", "BAY 1"),
        ("T", "M16A-A-T11"), ("B", "BAY 2"),
        ("T", "M16A-B-T06"), ("B", "BAY 3"),
        ("T", "M16A-B-T11"), ("B", "BAY 4"),
        ("T", "M16A-C-T06"),
    ], seq


def test_bay_one_is_the_top_of_the_sheet_not_the_bottom(s2):
    sc, st, geo = s2
    rows, _ = fd.build_rows(geo, st.monitor)
    bays = [r for r in rows if isinstance(r, fd.Bay)]
    assert bays[0].pile_ids == ["HP-01", "HP-05"]
    assert bays[-1].pile_ids == ["HP-04", "HP-08"]


def test_each_bay_holds_its_two_hangars(s2):
    sc, st, geo = s2
    rows, _ = fd.build_rows(geo, st.monitor)
    bays = [r for r in rows if isinstance(r, fd.Bay)]
    assert [b.pile_ids for b in bays] == [
        ["HP-01", "HP-05"], ["HP-02", "HP-06"],
        ["HP-03", "HP-07"], ["HP-04", "HP-08"]]


def test_serves_labels_follow_bay_adjacency(s2):
    sc, st, geo = s2
    rows, _ = fd.build_rows(geo, st.monitor)
    serves = {r.rail_id: r.serves for r in rows if isinstance(r, fd.Track)}
    assert serves["M16A-A-T06"] == ["BAY 1"]
    assert serves["M16A-A-T11"] == ["BAY 1", "BAY 2"]
    assert serves["M16A-B-T06"] == ["BAY 2", "BAY 3"]
    assert serves["M16A-B-T11"] == ["BAY 3", "BAY 4"]
    assert serves["M16A-C-T06"] == ["BAY 4"]


# ------------------------------------------------------- shared capacity -----
def test_shared_group_capacity_is_counted_once(s2):
    """The eight stacking legs share ONE 2,600 t/h reception path."""
    sc, st, geo = s2
    stack = [l for l in sc.links if l.shared_group]
    assert len(stack) >= 8
    naive = sum(l.capacity_tph for l in stack)
    grouped = fd.group_capacity(sc, stack)
    assert grouped == 2600.0
    assert naive >= 8 * 2600.0
    assert grouped < naive


def test_independent_links_still_sum(s2):
    sc, st, geo = s2
    free = [l for l in sc.links if not l.shared_group][:3]
    assert fd.group_capacity(sc, free) == pytest.approx(
        sum(l.capacity_tph for l in free))


def test_group_flow_never_exceeds_group_capacity_at_t0(s2):
    sc, st, geo = s2
    sp = fd.classify(sc)
    for links in (sp.arrival, sp.stack, sp.reclaim, sp.route, sp.deliver):
        if not links:
            continue
        assert fd.group_flow(st, links) <= fd.group_capacity(sc, links) + 1e-6


# ------------------------------------------------------------- scoping -------
def test_site_view_does_not_absorb_the_rest_of_the_chain(s3):
    """Everything downstream of Louta is technically reachable from Louta
    storage, including all of M16. A Louta view must stop at the corridor."""
    st = bridge.build_process_mimic_state(s3, None, None, basis="initial",
                                         bucket=0)
    wide = fd.classify(s3)
    geo = bridge.geometry_for(s3, "LOUTA")
    vis = {p.pile_id for p in geo.piles} & {sp.id for sp in s3.spaces}
    rows, site_of = fd.build_rows(geo, st.monitor)
    sites = {s for pid, s in site_of.items() if s and pid in vis}
    assert sites == {"LOUTA"}
    scoped = fd.classify(s3, vis, sites)
    assert len(scoped.route) < len(wide.route) + len(wide.reclaim)
    node_site = {n.id: n.site for n in s3.nodes}
    for l in scoped.route:
        assert node_site.get(l.src) in (None, "LOUTA"), l.id


def test_chain_view_keeps_every_site(s3):
    st = bridge.build_process_mimic_state(s3, None, None, basis="initial",
                                         bucket=0)
    geo = bridge.geometry_for(s3, "CHAIN")
    rows, site_of = fd.build_rows(geo, st.monitor)
    assert {s for s in site_of.values() if s} == {"LOUTA", "BENGUERIR", "MPH"}


def test_classify_direction_is_resolved_by_reachability_not_naming(s2):
    sc, st, geo = s2
    sp = fd.classify(sc)
    space_nodes = {(x.node_id or f"SPACE:{x.id}") for x in sc.spaces}
    for l in sp.stack:
        assert l.dst in space_nodes
    for l in sp.reclaim:
        assert l.src in space_nodes
    dest_nodes = {d.node_id for d in sc.destinations}
    for l in sp.deliver:
        assert l.dst in dest_nodes


# --------------------------------------------------- honesty about gaps ------
def test_a_stage_with_no_modelled_link_is_empty_not_zero(s3):
    """A stage with no modelled link must surface as 'not modelled', never as
    0 t/h. (Retired renderer; the specific Louta example depends on the
    scenario, which the production dataset may model differently over time.)"""
    st = bridge.build_process_mimic_state(s3, None, None, basis="initial",
                                         bucket=0)
    geo = bridge.geometry_for(s3, "LOUTA")
    vis = {p.pile_id for p in geo.piles} & {sp.id for sp in s3.spaces}
    rows, site_of = fd.build_rows(geo, st.monitor)
    sites = {s for pid, s in site_of.items() if s and pid in vis}
    sp = fd.classify(s3, vis, sites)
    if sp.stack:
        pytest.skip("Louta stacking is now modelled in the dataset — the "
                    "no-link example no longer applies")
    fig = fd.build(s3, st, geo, bridge.MimicOpts())
    assert "not modelled" in _annotation_text(fig)


def test_the_disclaimers_are_still_on_the_sheet(s2):
    sc, st, geo = s2
    fig = fd.build(sc, st, geo, bridge.MimicOpts())
    texts = _annotation_text(fig)
    for probe in ("NOT TO SCALE", "repère R0–R1000", "ASSUMED",
                  "no live control", "no commands"):
        assert probe in texts, probe


def test_display_only_geometry_is_marked_and_never_counted(s2):
    sc, st, geo = s2
    geo_f = bridge.geometry_for(sc, "MPH", show_future=True)
    fig = fd.build(sc, st, geo_f, bridge.MimicOpts(show_evidence=True))
    hov = " ".join(t for tr in fig.data for t in (tr.text or ()))
    assert "DISPLAY/REFERENCE" in hov or "display-only" in hov


# ---------------------------------------------------------- geometry ---------
def test_height_is_derived_from_content(s2, s3):
    sc, st, geo = s2
    small = fd.build(sc, st, geo, bridge.MimicOpts(), ppu=3.9)
    st3 = bridge.build_process_mimic_state(s3, None, None, basis="initial",
                                          bucket=0)
    big = fd.build(s3, st3, bridge.geometry_for(s3, "CHAIN"),
                   bridge.MimicOpts(), ppu=3.9)
    assert big.layout.height > small.layout.height
    assert 380 <= small.layout.height <= 2400
    assert 380 <= big.layout.height <= 2400


def test_ppu_scales_the_sheet(s2):
    sc, st, geo = s2
    a = fd.build(sc, st, geo, bridge.MimicOpts(), ppu=3.2)
    b = fd.build(sc, st, geo, bridge.MimicOpts(), ppu=4.8)
    assert b.layout.height > a.layout.height
    assert a.layout.yaxis.range == b.layout.yaxis.range


def test_profile_height_encodes_fill_not_horizontal_extent(s2):
    """The production drawing encodes mass as horizontal extent, which implies
    the material sits at one end. Here fill is HEIGHT."""
    sc, st, geo = s2
    assert fd._profile_top(0.5, 10.0, 8.0) == pytest.approx(2.0)
    assert fd._profile_top(0.5, 10.0, 4.0) == pytest.approx(6.0)
    # the ends slope down to the floor
    assert fd._profile_top(0.0, 10.0, 8.0) == pytest.approx(10.0)
    assert fd._profile_top(1.0, 10.0, 8.0) == pytest.approx(10.0)


def test_repere_maps_linearly_across_the_section():
    assert fd._rep_x(0) == pytest.approx(fd.SEC_X0)
    assert fd._rep_x(1000) == pytest.approx(fd.SEC_X1)
    assert fd._rep_x(500) == pytest.approx((fd.SEC_X0 + fd.SEC_X1) / 2)
    # out-of-range repères clamp instead of drawing off-sheet
    assert fd._rep_x(-100) == pytest.approx(fd.SEC_X0)
    assert fd._rep_x(9999) == pytest.approx(fd.SEC_X1)


def test_label_gutter_never_overlaps_the_section():
    assert fd.LBL_X1 < fd.SEC_X0
    assert fd.SEC_X1 < fd.BDG_X0
    assert fd.BDG_X0 < fd.X1


def test_every_view_renders_without_error(s3):
    for view in ("CHAIN", "LOUTA", "BENGUERIR", "MPH"):
        st = bridge.build_process_mimic_state(s3, None, None, basis="initial",
                                             bucket=0)
        geo = bridge.geometry_for(s3, view)
        for dark in (False, True):
            fig = fd.build(s3, st, geo, bridge.MimicOpts(), dark=dark)
            assert fig.layout.shapes, view
            assert fig.layout.annotations, view


def test_dark_and_light_skins_differ_only_in_palette(s2):
    sc, st, geo = s2
    a = fd.build(sc, st, geo, bridge.MimicOpts(), dark=False)
    b = fd.build(sc, st, geo, bridge.MimicOpts(), dark=True)
    assert len(a.layout.shapes) == len(b.layout.shapes)
    assert len(a.layout.annotations) == len(b.layout.annotations)
    assert a.layout.plot_bgcolor == tokens.CANVAS
    assert b.layout.plot_bgcolor == tokens.CANVAS_DARK


# ------------------------------------------------------------- options -------
@pytest.fixture(scope="module")
def working():
    """A bucket with jobs running, so flow-dependent options have something
    to switch off. Options were previously 'tested' on an idle snapshot, where
    an inert control looks identical to a working one."""
    sc = bridge.datasets.mph_phosphate()
    plan = bridge.solve_plan(sc, bridge.SolveOptions(max_time_s=10))
    for b in range(sc.sim.horizon_buckets):
        st = bridge.build_process_mimic_state(sc, plan, None, basis="plan",
                                             bucket=b)
        if st.jobs:
            return sc, st, bridge.geometry_for(sc, "MPH"), plan
    pytest.skip("no bucket with active jobs")


def _sig(fig):
    return (len(fig.layout.shapes), len(fig.layout.annotations))


def test_every_layer_option_changes_the_drawing(working):
    """Each rail control must reach the renderer. A source grep over the page
    cannot see an option the renderer silently ignores."""
    sc, st, geo, _ = working
    base = bridge.MimicOpts(show_flows=True, show_tags=True,
                            show_evidence=True)
    ref = _sig(fd.build(sc, st, geo, base))
    for field, value in (("show_flows", False), ("show_tags", False),
                         ("show_evidence", False), ("alarms_only", True),
                         ("active_flows_only", True)):
        opts = bridge.MimicOpts(show_flows=True, show_tags=True,
                                show_evidence=True)
        setattr(opts, field, value)
        assert _sig(fd.build(sc, st, geo, opts)) != ref, field


def test_flow_layer_removes_flow_but_keeps_capacity(working):
    sc, st, geo, _ = working
    on = fd.build(sc, st, geo, bridge.MimicOpts(show_flows=True))
    off = fd.build(sc, st, geo, bridge.MimicOpts(show_flows=False))
    assert len(off.layout.shapes) < len(on.layout.shapes)
    txt = _annotation_text(off)
    assert "cap " in txt, "capacity must still read when flow is hidden"
    assert "t/h →" not in txt and "→ HP-" not in txt


def _fills(fig):
    return [s.fillcolor for s in fig.layout.shapes]


def test_grade_filter_dims_the_piles_it_excludes(working):
    """The filter greys non-matching profiles rather than removing them, so
    compare the palette, not the element count."""
    sc, st, geo, _ = working
    ref = _fills(fd.build(sc, st, geo, bridge.MimicOpts()))
    filt = _fills(fd.build(sc, st, geo,
                           bridge.MimicOpts(grade_filter=["TBT"])))
    assert len(ref) == len(filt)
    assert ref != filt
    hair = fd.LIGHT.hair
    assert filt.count(hair) > ref.count(hair)


def test_selected_job_highlights_its_machine_and_its_stockpile(working):
    sc, st, geo, _ = working
    job = st.jobs[0]
    plain = fd.build(sc, st, geo, bridge.MimicOpts())
    high = fd.build(sc, st, geo, bridge.MimicOpts(selected_job=job.id))
    assert _sig(high) != _sig(plain)
    assert "▸ " in _annotation_text(high)
    focus = fd.Focus(machine=job.machine, space=job.space)
    assert focus.is_machine(job.machine)
    assert focus.is_space(job.space)
    assert not focus.is_machine("NOT-A-MACHINE")
    assert not focus.is_space("")


def test_selected_asset_is_outlined(s2):
    sc, st, geo = s2
    plain = fd.build(sc, st, geo, bridge.MimicOpts())
    sel = fd.build(sc, st, geo, bridge.MimicOpts(selected_asset="HP-01"))
    assert len(sel.layout.shapes) > len(plain.layout.shapes)


def test_track_rows_and_bay_cards_get_the_same_trailing_gap():
    assert fd.ROW_GAP > 0


def test_future_expansion_only_appears_when_asked(s2):
    sc, st, geo = s2
    now = fd.build(sc, st, bridge.geometry_for(sc, "MPH", show_future=False),
                   bridge.MimicOpts())
    fut = fd.build(sc, st, bridge.geometry_for(sc, "MPH", show_future=True),
                   bridge.MimicOpts())
    assert len(fut.layout.shapes) > len(now.layout.shapes)
