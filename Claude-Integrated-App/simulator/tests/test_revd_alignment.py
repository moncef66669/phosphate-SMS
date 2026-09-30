"""Rev-D alignment tests: three-site Domain-A hierarchy, branding, and
scenario-aware overview topology (report Rev D, 13-Jul-2026)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mph_sim import datasets                                  # noqa: E402
from ui.common import chain_mode, SITE_FILTERS, match_site_filter  # noqa: E402


def _src(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


UI_SOURCES = ["app.py", "ui/common.py", "ui/theme.py", "ui/pages_overview.py",
              "ui/pages_data.py", "ui/pages_planning.py", "ui/pages_meta.py",
              "mph_sim/datasets.py"]


# ---- 1. no active UI text says report Rev C --------------------------------
def test_no_rev_c_in_active_ui_text():
    for f in UI_SOURCES:
        assert "Rev C" not in _src(f), f"stale 'Rev C' reference in {f}"
        assert "Rev-C" not in _src(f), f"stale 'Rev-C' reference in {f}"


def test_the_app_states_the_CURRENT_report_revision():
    """CHANGED 25-Jul-2026: the app no longer hard-codes a revision letter.

    `ui/report_ref.py` reads the current revision from the single source
    (`report_sources/_revision.py`), so a report re-issue can never leave the
    UI citing a superseded revision.
    """
    from ui.report_ref import REV, report_ref
    assert REV and REV != "D", "the app must not pin the historical Rev D"
    assert "report Rev D" not in _src("app.py")
    assert "report_ref(" in _src("app.py")
    assert report_ref("§16") == f"report Rev {REV} §16"


# ---- 2. branding reflects the complete value chain --------------------------
def test_app_title_reflects_value_chain():
    app = _src("app.py")
    assert 'page_title="Phosphate Value-Chain Job-Allocation Simulator"' in app
    assert "Phosphate Value-Chain" in app          # sidebar header
    assert "Louta & Benguerir Mine Stockyards → MPH M16 Hub · M17 Sulphur" in app
    assert "MPH Job-Allocation Simulator" not in app


# ---- 3./4./5. S3 topology ----------------------------------------------------
@pytest.fixture(scope="module")
def s3():
    return datasets.end_to_end()


def test_s3_contains_three_peer_sites(s3):
    ids = {s.id for s in s3.sites}
    assert {"LOUTA", "BENGUERIR", "MPH"} <= ids
    roles = {s.id: s.role for s in s3.sites}
    assert roles["LOUTA"] == "mine" and roles["BENGUERIR"] == "mine"
    assert roles["MPH"] == "hub"


def _link_sites(sc, link_id):
    link = sc.link(link_id)
    node_site = {n.id: n.site for n in sc.nodes}
    return node_site[link.src], node_site[link.dst]


def test_c1_connects_louta_to_m16(s3):
    src, dst = _link_sites(s3, "C1")
    assert src == "LOUTA"
    assert dst == "MPH"
    assert s3.link("C1").dst == "TOWER:M16-REC"      # the M16 receiving tower
    assert s3.link("C1").transit_buckets >= 1


def test_c2_connects_benguerir_to_m16(s3):
    src, dst = _link_sites(s3, "C2")
    assert src == "BENGUERIR"
    assert dst == "MPH"
    assert s3.link("C2").dst == "TOWER:M16-REC"
    assert s3.link("C2").transit_buckets >= 1


def test_louta_c1_is_nearer_than_benguerir_c2(s3):
    """Project-team correction 22-Jul-2026: preserve identities and order."""
    assert _link_sites(s3, "C1") == ("LOUTA", "MPH")
    assert _link_sites(s3, "C2") == ("BENGUERIR", "MPH")
    assert s3.link("C1").transit_buckets < s3.link("C2").transit_buckets
    assert "NEARER" in s3.link("C1").evidence.note
    assert "FARTHER" in s3.link("C2").evidence.note


def test_s3_is_named_and_promoted(s3):
    assert s3.name.startswith("S3 — Three-site phosphate value chain")
    assert "Louta + Benguerir → M16" in s3.name
    names = list(datasets.BUILTIN_SCENARIOS)
    assert names[0].startswith("S3"), "S3 must be first in the selector"
    assert "PRIMARY Domain-A scenario" in s3.description


# ---- 6. S2 identifies its arrivals as simulated inputs ----------------------
def test_s2_declares_simulated_arrivals():
    sc = datasets.mph_phosphate()
    assert "simulated corridor arrivals" in sc.name.lower()
    assert "SIMULATED" in sc.description
    assert "NOT" in sc.description and "S3" in sc.description
    # the stand-in source node says so too
    node = next(n for n in sc.nodes if n.id == "SOURCE:CORRIDOR")
    assert "SIMULATED" in node.label
    # and the overview shows the required notice (rendered check is in
    # test_s2_overview_shows_notice; here we pin the wording fragments)
    ov = _src("ui/pages_overview.py")
    for frag in ("M16-only demonstration", "simulated inputs",
                 "Use S3 for the complete three-site"):
        assert frag in ov


# ---- 7./8. scenario-aware overview topology ---------------------------------
def test_chain_mode_per_scenario():
    assert chain_mode(datasets.sulphur_minimal()) == "sulphur"
    assert chain_mode(datasets.mph_phosphate()) == "m16-only"
    assert chain_mode(datasets.end_to_end()) == "three-site"


def test_s1_has_no_phosphate_chain():
    sc = datasets.sulphur_minimal()
    assert chain_mode(sc) == "sulphur"
    assert not any(sp.area.startswith("M16") for sp in sc.spaces)
    assert not any(s.role == "mine" for s in sc.sites)


def test_s3_overview_renders_three_site_chain():
    """Overview page renders the S3 chain figure + site cards headlessly."""
    from streamlit.testing.v1 import AppTest
    from mph_sim.optimizer import SolveOptions, solve_plan
    from mph_sim.validation import validate_scenario
    sc = datasets.end_to_end()
    sc.sim.max_solve_s = 3
    plan = solve_plan(sc)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=240)
    at.run()
    at.session_state["scenario"] = sc
    at.session_state["scenario_name"] = sc.name
    at.session_state["plan"] = plan
    at.session_state["sim"] = None
    at.session_state["issues"] = validate_scenario(sc)
    from conftest import nav_to
    nav_to(at, "1 · Executive Overview")
    assert not at.exception, at.exception
    body = " ".join(str(m.value) for m in at.markdown)
    assert "LOUTA + BENGUERIR → MPH M16 hub" in body
    assert "Site status — three peer Domain-A sites" in body


def test_s2_overview_shows_notice():
    from streamlit.testing.v1 import AppTest
    from mph_sim.validation import validate_scenario
    from conftest import nav_to

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=240)
    at.run()
    # S2 remains an internal diagnostic dataset but is no longer exposed in
    # the operator sidebar, whose only planning case is S0. Inject it directly
    # to retain the scenario-aware overview regression.
    sc = datasets.mph_phosphate()
    at.session_state["scenario"] = sc
    at.session_state["scenario_name"] = sc.name
    at.session_state["plan"] = None
    at.session_state["sim"] = None
    at.session_state["issues"] = validate_scenario(sc)
    nav_to(at, "1 · Executive Overview")
    assert not at.exception
    assert any("M16-only demonstration" in str(w.value) for w in at.warning)


# ---- site/domain filter vocabulary -------------------------------------------
def test_site_filter_vocabulary():
    assert SITE_FILTERS == ["LOUTA", "BENGUERIR", "MPH M16", "MPH M17",
                            "Domain A — phosphate", "Domain B — sulphur"]
    assert match_site_filter("MPH M16", "MPH", "M16-humid")
    assert not match_site_filter("MPH M16", "MPH", "M17-open")
    assert match_site_filter("Domain A — phosphate", "LOUTA", "mine-yard")
    assert match_site_filter("Domain A — phosphate", "MPH", "M16-humid")
    assert not match_site_filter("Domain A — phosphate", "MPH", "M17-open")
    assert match_site_filter("Domain B — sulphur", "MPH", "M17-closed")


def test_s3_validates_clean(s3):
    """Regression: mine stockpile route nodes must exist (SPACE:BG-P1/P2,
    SPACE:LT-P1/P2) — missing nodes previously raised 4 validation ERRORs
    that blocked Generate Plan on the Planning page."""
    from mph_sim.validation import validate_scenario
    errs = [i for i in validate_scenario(s3) if i.severity == "ERROR"]
    assert not errs, [f"{i.code}: {i.message}" for i in errs]


# ---- 9. determinism unchanged (S3 numerical inputs untouched) -----------------
def test_s3_solve_still_deterministic_after_alignment():
    from mph_sim.optimizer import SolveOptions, solve_plan
    sigs = []
    for _ in range(2):
        sc = datasets.end_to_end()
        res = solve_plan(sc, SolveOptions(max_time_s=3))
        sigs.append(tuple((j.machine, j.space, j.dest_id, j.start_bucket,
                           j.end_bucket, j.tonnage_t) for j in res.jobs))
    assert sigs[0] == sigs[1]
