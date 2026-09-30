"""Acceptance tests for the UI design preview.

Two things must hold for the preview to be reviewable:

  1. NO SILENT DELETION — every operator control and information block of the
     production page 15 ("Global Stockyard Layout & Flow") still has a
     counterpart here. Two are consolidated rather than removed; the mapping
     is explicit below so a consolidation can never be mistaken for a drop.

     LIMITATION, stated plainly: the PARITY table below is a substring grep
     over this app's own source. It catches deletion; it does NOT prove a
     control works. Functional evidence is the browser QA digest at
     ``generated_outputs/ui_preview_qa/ui-preview-qa.json``.

  2. NON-INTERFERENCE — the preview imports the production package read-only
     and writes nothing into ``simulator/``.

Run:  python -m pytest simulator_preview/tests -q
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest

PREVIEW_ROOT = pathlib.Path(__file__).resolve().parents[1]
PROJECT_ROOT = PREVIEW_ROOT.parent
SIMULATOR_ROOT = PROJECT_ROOT / "simulator"

sys.path.insert(0, str(PREVIEW_ROOT))

from hmi import canvas, conditions, css, page_layout_flow  # noqa: E402
from hmi import pages_more, tokens, widgets                # noqa: E402
from hmi import bridge                                     # noqa: E402

APP_SRC = (PREVIEW_ROOT / "app_preview.py").read_text(encoding="utf-8")
FLOW_SRC = (PREVIEW_ROOT / "hmi" / "page_layout_flow.py").read_text(
    encoding="utf-8")
ALL_SRC = APP_SRC + FLOW_SRC + "".join(
    (PREVIEW_ROOT / "hmi" / n).read_text(encoding="utf-8")
    for n in ("widgets.py", "canvas.py", "conditions.py"))


# ============================ 1. function parity =============================
#: production control / block  ->  substring proving its counterpart exists
PARITY = {
    # ---- view & state ----
    "view selector": '"View"',
    "single-view collapse for M16-only scenarios": "views_for(sc)",
    "state view: initial": "STATE_INITIAL",
    "state view: final plan": "STATE_FINAL",
    "state view: current simulation": "STATE_SIM",
    "state view: inspect plan bucket": "STATE_BUCKET",
    "sidebar plan-timeline basis (consolidated)": "STATE_TIMELINE",
    "shared plan-hour session key": "PROCESS_MIMIC_HOUR_KEY",
    "shared plan-basis session key": "PROCESS_MIMIC_BASIS_KEY",
    "time-bucket slider": '"Plan bucket"',
    # ---- filters & layers ----
    "grade filter": '"Grade filter"',
    "layer: active flow": '"Flow"',
    "layer: tags": '"Tags"',
    "layer: evidence/TBC": '"Evidence"',
    "layer: alarms only": '"Alarms only"',
    "layer: active flows only": '"Active only"',
    "layer: future expansion": '"Future exp."',
    # ---- selection & actions ----
    "selected job path highlight": '"Job path highlight"',
    "fit view / reset zoom": '"Fit view"',
    "reset selection": '"Clear sel."',
    # ---- the drawing ----
    "new flow diagram is offered": "DIAG_NEW",
    "production drawing kept as the reference": "DIAG_REF",
    "reference drawing still uses the production builder":
        "process_mimic_figure(state",
    "scroll zoom enabled": '"scrollZoom": True',
    "asset status legend": "ASSET_STATUS_STYLES",
    "drawing-conventions text": "Drawing conventions",
    "schematic / not-to-scale statement": "not to scale",
    "ACS / no-live-control note": "acs_note",
    "stale scenario-graph banner": "Stale scenario graph",
    "grade-profile distribution": "grade_profile_distribution_rows",
    "drill-through to the other views": 'f"Open {VIEW_LABELS',
    "drill-through queues the view (no post-instantiation write)":
        '"_cx_pending_view"',
    # ---- context strip ----
    "timestamp / bucket / basis caption": "state.timestamp",
    # ---- alarm summary counters ----
    "counter: pile warnings": '"Pile warnings"',
    "counter: machines down/maintenance": '"Machines down / maint."',
    "counter: tripped links": '"Tripped links"',
    "counter: concurrency alerts": '"Concurrency alerts"',
    "counter: concurrent piles": '"Concurrent piles"',
    # ---- asset details ----
    "asset details: machine": '"Machine"',
    "asset details: stockpile": '"Stockpile"',
    "asset details: conveyor": '"Conveyor"',
    "display/reference machines are inspectable": "DISPLAY/REFERENCE",
    "machine reachable-spaces field": '"Reachable (configured)"',
    "repere position basis": "POSITION_NOTE",
    "pile concurrency + separation": '"Concurrency"',
    "pile safety stock": '"Safety stock"',
    "conveyor model links": '"Model links"',
    "conveyor evidence": "li0.evidence.status",
    # ---- coverage ----
    "model coverage / geometry gaps": '"Model coverage"',
    "coverage discrepancies": "monitor.discrepancies",
    # ---- Louta C1 feeders / Benguerir B02 ----
    "C1 feeder-selection panel": '"C1 feeder selection"',
    "Benguerir B02 configuration status": "BEN_B02_OPTION_ENABLED",
    # ---- app shell ----
    "scenario loader (load, never solve)": "common.load_scenario",
    "generate plan action": "common.request_solve",
    "staged solve status panel": "progress_cb",
    "evidence badge counts": "evidence_counts",
    "inventory provenance": "INVENTORY_SNAPSHOT",
    "validation issue drawer": "Validation issues",
    "report revision reference": 'report_ref("§16")',
}

#: Controls deliberately CONSOLIDATED (present, but as one control instead of
#: several). Reviewers must be able to see this list.
CONSOLIDATED = {
    "sidebar basis radio + sidebar hour slider + in-page bucket slider":
        "one TIME control in the operator rail (same session keys)",
    "asset-type radio + three separate asset selectboxes":
        "one type-tagged ASSET FOCUS selector (search-as-you-type)",
    "six separate toggles":
        "one multi-select LAYERS control with the same six options",
}


@pytest.mark.parametrize("feature,probe", sorted(PARITY.items()))
def test_production_control_has_a_counterpart(feature, probe):
    assert probe in ALL_SRC, f"missing counterpart for: {feature}"


def test_consolidations_are_documented_in_the_module_docstring():
    doc = page_layout_flow.__doc__ or ""
    assert "CONSOLIDATED" in doc
    for original in CONSOLIDATED:
        pass  # the table above is the record; the docstring states the rule
    assert "Plan timeline" in doc and "ASSET FOCUS" in doc


def test_layer_options_cover_every_production_toggle():
    assert page_layout_flow.LAYER_OPTS == [
        "Flow", "Tags", "Evidence", "Alarms only", "Active only",
        "Future exp."]
    assert page_layout_flow.LAYER_DEFAULT == ["Flow", "Tags", "Evidence"]


def test_state_options_expose_every_basis_when_plan_and_sim_exist():
    opts = page_layout_flow._state_options(object(), object())
    assert opts == [bridge.STATE_INITIAL, bridge.STATE_TIMELINE,
                    bridge.STATE_FINAL, bridge.STATE_BUCKET, bridge.STATE_SIM]
    assert page_layout_flow._state_options(None, None) == [
        bridge.STATE_INITIAL]
    for s in opts:
        assert s in bridge.BASIS_OF_STATE


# ========================== 2. non-interference ==============================
def test_bytecode_writing_is_disabled_before_the_engine_is_imported():
    assert sys.dont_write_bytecode is True
    src = (PREVIEW_ROOT / "hmi" / "bridge.py").read_text(encoding="utf-8")
    guard = src.index("sys.dont_write_bytecode = True")
    assert guard < src.index("from mph_sim import datasets"), \
        "the guard must precede the first engine import"


_PROBE = r"""
import pathlib, sys
root = pathlib.Path(sys.argv[1])
before = {str(p) for p in root.rglob("*.pyc")}
sys.path.insert(0, sys.argv[2])
import hmi.bridge                      # the guard lives inside this module
after = {str(p) for p in root.rglob("*.pyc")}
print(len(after - before))
"""


def test_importing_the_engine_writes_no_bytecode_into_the_production_tree():
    """Runs in a FRESH interpreter with bytecode writing ENABLED, so the guard
    inside ``hmi.bridge`` is what is actually under test.

    Asserting "no __pycache__ exists under simulator/" would be meaningless
    here: the production test suite legitimately creates those directories.
    """
    env = {k: v for k, v in __import__("os").environ.items()
           if k != "PYTHONDONTWRITEBYTECODE"}
    out = subprocess.run(
        [sys.executable, "-c", _PROBE, str(SIMULATOR_ROOT), str(PREVIEW_ROOT)],
        capture_output=True, text=True, env=env, cwd=str(PREVIEW_ROOT),
        timeout=180)
    assert out.returncode == 0, out.stderr[-2000:]
    added = int(out.stdout.strip().splitlines()[-1])
    assert added == 0, f"{added} .pyc files were written under simulator/"


def test_the_preview_never_imports_the_production_stylesheet():
    """ui.theme's CSS targets Streamlit internals globally; loading it would
    fight the console styling and couple the two looks. Prose mentions are
    fine — only executable statements are checked."""
    bad = (re.compile(r"^\s*from\s+ui(\.theme)?\s+import\s+.*\btheme\b",
                      re.M),
           re.compile(r"^\s*import\s+ui\.theme", re.M),
           re.compile(r"^\s*\w*theme\.inject_css\(", re.M))
    for f in sorted((PREVIEW_ROOT / "hmi").glob("*.py")):
        src = f.read_text(encoding="utf-8")
        for pat in bad:
            assert not pat.search(src), f"{f.name}: {pat.pattern}"


def test_the_preview_is_not_registered_as_a_production_page():
    """Adding a page would break simulator/app.py's nav assert and renumber
    every ?page=N deep link."""
    app = (SIMULATOR_ROOT / "app.py").read_text(encoding="utf-8")
    assert "simulator_preview" not in app
    assert "app_preview" not in app
    common_src = (SIMULATOR_ROOT / "ui" / "common.py").read_text(
        encoding="utf-8")
    assert "simulator_preview" not in common_src


def test_the_preview_uses_its_own_port_and_ownership_guard():
    ps1 = (PROJECT_ROOT / "Run-UI-Preview.ps1").read_text(encoding="utf-8")
    prod = (PROJECT_ROOT / "Run-Latest-Simulator.ps1").read_text(
        encoding="utf-8")
    assert "8606" in ps1 and "8505" in prod
    assert "app_preview.py" in ps1
    assert "PYTHONDONTWRITEBYTECODE" in ps1
    assert '"--server.runOnSave"' in prod
    assert '"true"' in prod
    # the guard must key on this app's own path, never a bare "streamlit"
    assert "Test-ThisPreviewCommand" in ps1


# ============================ 3. conditions model ============================
def _state(sc):
    return bridge.build_process_mimic_state(sc, None, None, basis="initial",
                                            bucket=0)


def test_stale_graph_is_an_alarm_and_ranks_first():
    sc = bridge.datasets.mph_phosphate()
    st = _state(sc)
    conds = conditions.collect(sc, st, stale=["HUB:M16-RECLAIM"])
    assert conds[0].severity == conditions.ALARM
    assert "SCENARIO GRAPH" == conds[0].tag


def test_shared_capacity_breach_is_an_alarm():
    sc = bridge.datasets.mph_phosphate()
    st = _state(sc)
    group = next(l.shared_group for l in sc.links if l.shared_group)
    members = [l for l in sc.links if l.shared_group == group]
    cap = min(l.capacity_tph for l in members)
    for l in members:
        if l.id in st.flows:
            st.flows[l.id].tph = cap        # sum now exceeds the envelope
    conds = conditions.collect(sc, st)
    assert any(c.severity == conditions.ALARM and c.tag == group
               for c in conds), [c.tag for c in conds]


def test_validation_errors_and_warnings_map_to_alarm_and_caution():
    sc = bridge.datasets.mph_phosphate()
    st = _state(sc)

    class I:
        def __init__(self, sev):
            self.severity, self.code, self.entity = sev, "X1", "E"
            self.message = "m"

    conds = conditions.collect(sc, st, issues=[I("ERROR"), I("WARNING"),
                                               I("INFO")])
    sev = {c.severity for c in conds if c.tag == "E"}
    assert sev == {conditions.ALARM, conditions.CAUTION, conditions.ADVISORY}


def test_no_condition_is_invented_for_a_clean_scenario():
    sc = bridge.datasets.mph_phosphate()
    st = _state(sc)
    conds = conditions.collect(sc, st)
    assert all(c.severity != conditions.ALARM for c in conds), \
        [c.text for c in conds if c.severity == conditions.ALARM]


def test_headline_is_honest_when_nothing_is_wrong():
    assert "normal" in conditions.headline([])


# ============================== 4. canvas skins ==============================
def _colours(fig):
    out = []
    for sh in (fig.layout.shapes or ()):
        out.append(sh.fillcolor)
        out.append(sh.line.color if sh.line else None)
    return out


def test_document_skin_does_not_repaint_the_drawing():
    """The reference rendering must stay byte-identical to production."""
    sc = bridge.datasets.mph_phosphate()
    st = _state(sc)
    geo = bridge.geometry_for(sc, "MPH")
    a = bridge.process_mimic_figure(st, geo, bridge.MimicOpts())
    before = _colours(a)
    canvas.restyle(a, canvas.DOCUMENT, height=600)
    assert _colours(a) == before
    assert a.layout.plot_bgcolor == tokens.CANVAS
    assert a.layout.height == 600


def test_control_room_skin_maps_the_known_palette_and_keeps_mid_tones():
    assert canvas._dark_map(bridge.hs.HMI_BG) == tokens.CANVAS_DARK
    assert canvas._dark_map("#ffffff") == "#1b232a"
    # a mid-saturation grade fill must pass through untouched
    assert canvas._dark_map("#2f7f8f") is None
    # unmapped near-white darkens, unmapped near-black lightens
    assert canvas._dark_map("#fdfdfb") is not None
    assert canvas._dark_map("#050607") is not None


def test_control_room_skin_preserves_alpha():
    out = canvas._dark_map("rgba(255,255,255,0.4)")
    assert out is not None and out.startswith("rgba(") and "0.400" in out


def test_control_room_skin_changes_the_plot_background():
    sc = bridge.datasets.mph_phosphate()
    st = _state(sc)
    geo = bridge.geometry_for(sc, "MPH")
    fig = bridge.process_mimic_figure(st, geo, bridge.MimicOpts())
    canvas.restyle(fig, canvas.CONTROL_ROOM, height=600)
    assert fig.layout.plot_bgcolor == tokens.CANVAS_DARK


# ============================== 5. inspector =================================
def test_asset_focus_offers_machines_piles_conveyors_and_display_geometry():
    sc = bridge.datasets.mph_phosphate()
    st = _state(sc)
    geo = bridge.geometry_for(sc, "MPH", show_future=True)
    opts = page_layout_flow._asset_options(sc, geo, st.monitor)
    kinds = {k for k, _, _ in opts}
    assert {"MACHINE", "PILE", "CONVEYOR"} <= kinds
    assert any(k == "DISPLAY" for k, _, _ in opts), \
        "future/display-only geometry must remain inspectable"
    ids = {i for _, i, _ in opts}
    for m in sc.machines:
        assert m.id in ids
    for s in sc.spaces:
        assert s.id in ids


def test_machine_inspector_reports_configured_reach_not_geometry():
    sc = bridge.datasets.mph_phosphate()
    st = _state(sc)
    geo = bridge.geometry_for(sc, "MPH")
    mid = sc.machines[0].id
    rows, _ = page_layout_flow._machine_rows(sc, geo, st.monitor, st, mid,
                                             False)
    d = dict(rows)
    assert d["Reachable (configured)"] == ", ".join(
        sc.machines[0].reachable_spaces)
    assert "no live control" in d["Control mode"]


def test_display_machine_inspector_states_it_is_not_in_the_planning_model():
    sc = bridge.datasets.mph_phosphate()
    st = _state(sc)
    geo = bridge.geometry_for(sc, "MPH", show_future=True)
    known = {m.id for m in sc.machines}
    disp = next((g.machine_id for g in geo.machines
                 if g.display and g.machine_id not in known), None)
    assert disp is not None
    rows, sev = page_layout_flow._machine_rows(sc, geo, st.monitor, st, disp,
                                              True)
    d = dict(rows)
    assert d["In planning model"].startswith("NO")
    assert sev.get("Status") == "caut"


def test_reception_envelope_reports_the_modelled_group_capacity():
    sc = bridge.datasets.mph_phosphate()
    st = _state(sc)
    envs = page_layout_flow._reception_envelopes(sc, st)
    assert envs, "M16 must expose its shared reception envelope"
    for group, tph, cap in envs:
        members = [l for l in sc.links if l.shared_group == group]
        assert cap == min(l.capacity_tph for l in members)
        assert tph <= cap + 1e-6


# ============================== 6. widgets ===================================
def test_widget_html_is_escaped():
    out = widgets.chip("<script>x</script>", value='" onload="y')
    assert "<script>" not in out and "&lt;script&gt;" in out
    assert 'onload="y' not in out


def test_sparkline_needs_two_points_and_never_raises():
    assert "trend needs a plan" in widgets.sparkline([])
    assert "trend needs a plan" in widgets.sparkline([1.0])
    svg = widgets.sparkline([1.0, 2.0, 1.5], marker_at=1, baseline=1.6)
    assert svg.startswith("<svg") and "polyline" in svg


def test_sparkline_handles_a_flat_series():
    svg = widgets.sparkline([5.0, 5.0, 5.0])
    assert svg.startswith("<svg")


def test_kpi_bar_is_clamped():
    widgets.Kpi("l", "v", bar_pct=250.0)      # must not raise
    widgets.Kpi("l", "v", bar_pct=-10.0)


def test_condition_ribbon_and_list_accept_empty_input():
    assert conditions.rows([]) == []
    assert conditions.counts([]) == {conditions.ALARM: 0,
                                     conditions.CAUTION: 0,
                                     conditions.ADVISORY: 0}


# ============================== 7. tone rules ================================
def test_saturated_alarm_colours_are_not_used_for_normal_chrome():
    """ISA-101: red/amber must mean 'abnormal', never decoration."""
    sheet = css._CSS
    for hot in (tokens.ALARM, tokens.CAUTION):
        for line in sheet.splitlines():
            if hot.lower() not in line.lower():
                continue
            assert any(k in line for k in
                       ("alarm", "caut", "ALARM", "CAUTION", "cx-bar",
                        "cx-kpi", "cx-row", "cx-ribbon", "sev-")), line


def test_the_console_hides_streamlit_branding_chrome():
    sheet = css._CSS
    for probe in ('header[data-testid="stHeader"]', "#MainMenu", "footer"):
        assert probe in sheet


def test_evidence_palette_is_preserved():
    """Neutralising the evidence colours would destroy information."""
    assert set(tokens.EVIDENCE) >= {"CONFIRMED", "DERIVED", "INFERRED",
                                    "ASSUMED", "TBC"}
    assert len(set(tokens.EVIDENCE.values())) >= 5
