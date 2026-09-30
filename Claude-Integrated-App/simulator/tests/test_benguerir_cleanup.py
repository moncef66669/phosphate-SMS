"""Benguerir evidence / reporting / UI-consistency regression suite (21-Jul-2026).

Guards the defects fixed in the cleanup pass:

  * the generated Markdown shipped as Rev F while the PDF shipped as Rev H,
    because the Markdown header was replaced by an exact literal match that
    silently no-opped;
  * an EXTERNAL citation `Q80130-BEN-B02-ME-BOD-0001 Rev B §2.3.2` was
    renumbered to the non-existent `§15.3.2` by the Functional-Design
    section renumbering;
  * the report claimed the raw and washed train interfaces compete for the
    same piles/reclaimers — they are different yards;
  * the raw yard was described as having "no tabulated capacities" when
    Table 20 states 400,000 t total live capacity;
  * simulator coverage of the Benguerir stages was not stated honestly.

Everything here is text/geometry evidence. Nothing in this file may change a
solver plan — `test_signatures_unchanged` is the guard for that.
"""
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from mph_sim import datasets
from mph_sim.optimizer import SolveOptions, solve_plan
from ui.layout_geometry import GEOMETRY_REVISION, PileGeo, geometry_for
from ui.process_mimic import _display_fill_pct, mimic_figure_key

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# The Claude-integrated simulator can be launched from a nested app folder
# while report sources and generated evidence remain at the engineering-project
# root.  Keep this evidence suite runnable in both layouts.
if not (PROJECT_ROOT / "report_sources").is_dir():
    PROJECT_ROOT = PROJECT_ROOT.parent
REPORT_DIR = PROJECT_ROOT / "generated_outputs" / "reports" / "current"
REPORT_MD = REPORT_DIR / "MPH-SMS-Job-Allocation-Project-Report.md"
REPORT_PDF = REPORT_DIR / "MPH-SMS-Job-Allocation-Project-Report.pdf"
REPORT_HTML = REPORT_DIR / "_report.html"
SOURCE_DIR = PROJECT_ROOT / "report_sources"

#: the issued revision — read from the pipeline's own single source of truth
_spec = importlib.util.spec_from_file_location(
    "_revision", SOURCE_DIR / "_revision.py")
_revision = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_revision)

_secref_spec = importlib.util.spec_from_file_location(
    "_secref", SOURCE_DIR / "_secref.py")
_secref = importlib.util.module_from_spec(_secref_spec)
_secref_spec.loader.exec_module(_secref)


def test_mimic_widget_key_busts_stale_geometry_payloads():
    key = mimic_figure_key("overview", "CHAIN", 0)
    assert GEOMETRY_REVISION in key
    assert key.endswith("_0")


@pytest.mark.parametrize("mass, expected", [
    (None, 0.0), (-10.0, 0.0), (50.0, 50.0), (150.0, 100.0),
])
def test_reference_display_fill_is_clamped(mass, expected):
    pile = PileGeo("REF", 0, 0, 1, 1, modelled=False,
                   capacity_t=100, display_mass_t=mass)
    assert _display_fill_pct(pile) == expected


def _nb(text: str) -> str:
    """Normalise the non-breaking hyphens used in document numbers."""
    return text.replace("‑", "-").replace("‐", "-")


@pytest.fixture(scope="module")
def report_md() -> str:
    assert REPORT_MD.exists(), "run report_sources/_mergemd.py first"
    return REPORT_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def report_html() -> str:
    assert REPORT_HTML.exists(), "run report_sources/_merge.py first"
    return REPORT_HTML.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def scope_md() -> str:
    """Canonical Markdown source, testable before the report is regenerated."""
    return (SOURCE_DIR / "MPH-OSBL-SMS-Job-Planning-Automation-Scope.md"
            ).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def scope_html() -> str:
    """Canonical HTML source, kept semantically aligned with ``scope_md``."""
    return (SOURCE_DIR / "_scope.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pdf_text() -> str:
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(REPORT_PDF))
    return "\n".join(p.extract_text() or "" for p in reader.pages)


@pytest.fixture(scope="module")
def s3():
    return datasets.end_to_end()


# ---- 1. the Markdown and the PDF are the SAME revision -------------------------
def test_markdown_identifies_itself_as_the_issued_revision(report_md):
    header = report_md.split("# PART I")[0]
    assert f"**Rev.:** {_revision.REV}  **Date:** {_revision.REV_DATE}" in header
    # the stale header the pipeline used to ship
    assert "**Rev.:** F  **Date:** 16 July 2026" not in header
    assert "Draft scope definition" not in header, \
        "Markdown kept the un-replaced scope-note title block"


def test_pdf_identifies_itself_as_the_issued_revision(pdf_text):
    head = pdf_text[:3000]
    assert f"Rev.: {_revision.REV}" in head
    assert _revision.REV_DATE in head


def test_markdown_and_pdf_agree_on_revision(report_md, pdf_text):
    md_rev = re.search(r"\*\*Rev\.:\*\* ([A-Z]+)", report_md).group(1)
    pdf_rev = re.search(r"Rev\.: ([A-Z]+)", pdf_text).group(1)
    assert md_rev == pdf_rev == _revision.REV


def test_both_deliverables_carry_the_same_revision_history(report_md,
                                                           report_html):
    # The full audit list remains in _revision.REVISIONS; the issued report
    # deliberately prints the compact decision-relevant history.
    for row in _revision.DISPLAY_REVISIONS:
        assert f"| {row.rev} | {row.date} |" in report_md, row.rev
        assert f"<td>{row.rev}</td><td>{row.date}</td>" in report_html, row.rev


def test_revision_history_is_in_date_order():
    _revision.check_order()          # raises if a row is out of order


def test_pdf_page_count_is_derived_not_asserted():
    """The page count must be READ from the artefact, never hard-coded."""
    pypdf = pytest.importorskip("pypdf")
    pages = len(pypdf.PdfReader(str(REPORT_PDF)).pages)
    assert pages > 0
    context = (PROJECT_ROOT / "CLAUDE-CONTEXT.md").read_text(encoding="utf-8")
    claimed = re.search(
        rf"Rev {re.escape(_revision.REV)}, [^,\n]+, (\d+) pages",
        context)
    assert claimed, "CLAUDE-CONTEXT.md no longer states a page count"
    assert int(claimed.group(1)) == pages, (
        f"CLAUDE-CONTEXT.md claims {claimed.group(1)} pages, the PDF has "
        f"{pages}")


# ---- 2. external document citations are never renumbered ----------------------
def test_external_b02_citation_keeps_its_own_section_number(report_md,
                                                            report_html):
    for text, name in ((report_md, "markdown"), (report_html, "html")):
        flat = _nb(text)
        assert "Q80130-BEN-B02-ME-BOD-0001 Rev B §2.3.2" in flat, name
        assert "§15.3.2" not in flat, (
            f"{name}: an external citation was renumbered by the internal "
            "Functional-Design section mapping")


def test_no_internal_section_number_collides_with_an_external_one(report_md):
    # §15.3.2 / §15.3.3 / §15.6.x are FD-internal numbers that do not exist:
    # the FD only has §15.1-§15.8. Any deeper number is a mangled citation.
    bad = re.findall(r"§1[45678]\.\d+\.\d+", report_md)
    assert not bad, f"mangled external citations: {sorted(set(bad))}"


def test_secref_protects_both_written_forms_of_the_citation():
    """The short `Rev B §2.3.2` form is what the old literal list missed."""
    for cite in ("Q80130‑BEN‑B02‑ME‑BOD‑0001 Rev B §2.3.2",
                 "Q80130-BEN-B02-ME-BOD-0001 Rev B, IFR, 05-Feb-2025 §2.3.2",
                 "Table 20 vs Q80130-BEN-B02-ME-BOD-0001 Rev B §2.3.2"):
        masked = _secref.protect_external_sections(cite)
        assert "§" not in masked, cite
        assert _secref.restore_sections(masked) == cite


def test_secref_leaves_internal_cross_references_alone():
    """A bare cross-reference has no document cue and must stay renumberable."""
    for internal in ("<td>scheduler concurrency rule (§2.4)</td>",
                     "| blend = LP (in x, §2.3) | planning |",
                     "second feasibility gate, §2.7"):
        assert _secref.protect_external_sections(internal) == internal


# ---- 3. upstream and downstream resources are separate ------------------------
FORBIDDEN_SHARING = (
    "compete for the same piles",
    "for the same piles/reclaimers",
    "competition between train loading and corridor dispatch",
)


def test_report_never_says_raw_and_washed_share_piles_or_reclaimers(report_md):
    for phrase in FORBIDDEN_SHARING:
        assert phrase not in report_md, phrase


def test_report_states_the_correct_stage_separation(report_md):
    assert "do not share piles or reclaimers" in report_md
    assert "Raw and washed stages share no piles/reclaimers" in report_md
    # EVERY train-station sharing mention must read as an assumption
    mentions = re.findall(r"one (?:loading )?station[^.|<)]{0,60}", report_md)
    assert mentions, "the shared train-loading station is no longer mentioned"
    for m in mentions:
        assert "ASSUMED/TBC" in m, m


# ---- 4. simulator coverage is disclosed honestly ------------------------------
def test_scope_discloses_the_six_pile_solver_chain(scope_md, scope_html):
    for text, name in ((scope_md, "markdown"), (scope_html, "html")):
        flat = re.sub(r"<[^>]+>", " ", text)
        flat = re.sub(r"\s+", " ", _nb(flat))
        for expected in (
            "six physical",
            "BG-MINE-1/2",
            "BG-RAW-1/2",
            "BG-P1/P2",
            "TM1",
            "TSM2",
            "TSM3",
            "all nine",
            "ST3",
            "RC2",
            "RC3",
        ):
            assert expected in flat, f"{name}: {expected}"


def test_scope_retires_the_exogenous_shortcut_and_plans_st3(
        scope_md, scope_html):
    stale = (
        "bypasses ST3",
        "ST3 jobs not implemented",
        "ST3 is current but its jobs are not implemented",
        "upstream raw piles/machines are reference-only",
        "washing-line rectangles are also reference-only",
    )
    for text, name in ((scope_md, "markdown"), (scope_html, "html")):
        assert all(phrase not in text for phrase in stale), name
        assert "BG-WASH-IN" in text, name
        assert "retired and inactive" in text, name
        assert "recovered-product stacking through" in text, name


def test_scope_keeps_unverified_benguerir_details_explicit(scope_md):
    section = scope_md[scope_md.index(
        "Benguerir — six physical piles"):]
    section = section[:section.index("### 13.4")]
    for caveat in (
        "physical tags",
        "metric GA coordinates",
        "borrowed",
        "not live Benguerir assays",
        "approved operating recipes",
        "**TBC**",
    ):
        assert caveat in section, caveat


def test_report_does_not_invent_a_machinegeo_planning_enabled_field(report_md):
    """`MachineGeo` has display/, not planning_enabled — do not misstate it."""
    for m in re.finditer(r"MachineGeo[^|\n]{0,120}", report_md):
        assert "planning_enabled" not in m.group(0), m.group(0)


def test_report_distinguishes_target_scope_from_current_implementation(
        report_md):
    assert "## 5. Functional Scope" in report_md
    assert "**Current simulator coverage:**" in report_md


# ---- 5. scenario and geometry agree with the report ---------------------------
PHYSICAL_PILES = {
    "BG-MINE-1": 200_000,
    "BG-MINE-2": 200_000,
    "BG-RAW-1": 200_000,
    "BG-RAW-2": 200_000,
    "BG-P1": 191_512,
    "BG-P2": 191_512,
}
BG_MACHINES = {
    "BG-MINE-ST-1", "BG-MINE-ST-2", "BG-MINE-BWR",
    "BG-RAW-ST-1", "BG-RAW-ST-2", "BG-RAW-BWR",
    "ST3", "RC2", "RC3",
}
HOPPERS = {"BG-HOP-TM1", "BG-HOP-TSM2", "BG-HOP-TSM3"}
WASH_LINES = {"BEN-WASH-L1", "BEN-WASH-L2", "BEN-WASH-L3"}


def test_six_physical_piles_and_nine_machines_are_solver_backed(s3):
    spaces = {space.id: space for space in s3.spaces}
    machines = {machine.id: machine for machine in s3.machines
                if machine.site == "BENGUERIR"}
    assert {pid: spaces[pid].capacity_t for pid in PHYSICAL_PILES} == \
        PHYSICAL_PILES
    assert set(machines) == BG_MACHINES
    assert all(machine.planning_enabled for machine in machines.values())


def test_three_hoppers_are_interfaces_not_extra_physical_piles(s3):
    spaces = {space.id: space for space in s3.spaces}
    assert HOPPERS <= set(spaces)
    assert all(spaces[hopper].area == "mine-interface"
               for hopper in HOPPERS)
    physical = {
        space.id for space in s3.spaces
        if space.site == "BENGUERIR"
        and space.area not in {"mine-interface", "process-interface"}
    }
    assert set(PHYSICAL_PILES) <= physical
    assert not HOPPERS & physical


def test_all_three_benguerir_washing_lines_are_demand_driven(s3):
    destinations = {destination.id: destination
                    for destination in s3.destinations}
    assert WASH_LINES <= set(destinations)
    assert all(destinations[line].demand_kind == "process_feed"
               for line in WASH_LINES)
    stages = {
        transfer.process_stage for transfer in s3.process_transfers
        if transfer.process_stage.startswith("BEN-")
    }
    assert len(stages) == 15


@pytest.mark.parametrize("view", ["CHAIN", "GLOBAL", "BENGUERIR"])
def test_all_views_bind_the_six_physical_piles_and_nine_machines(s3, view):
    geo = geometry_for(s3, view)
    piles = {pile.pile_id: pile for pile in geo.piles
             if pile.pile_id in PHYSICAL_PILES}
    machines = {machine.machine_id: machine for machine in geo.machines
                if machine.machine_id in BG_MACHINES}
    assert set(piles) == set(PHYSICAL_PILES), view
    assert all(pile.modelled for pile in piles.values()), view
    assert set(machines) == BG_MACHINES, view
    assert not any(machine.display for machine in machines.values()), view


def test_current_assumptions_describe_conserved_benguerir_jobs(s3):
    by_id = {assumption.id: assumption for assumption in s3.assumptions}
    a11 = by_id["S3-A11"].text
    a17 = by_id["S3-A17"].text
    assert "material-conserving planned jobs" in a11
    assert "routes the recovered output through ST3" in a11
    assert "retired exogenous BG-WASH-IN representation is inactive" in a17


# ---- 8. no source conflation -------------------------------------------------
def test_b01_is_never_credited_with_two_stackers(report_md):
    """B01 evidence = ONE 2,200 t/h stacker + ONE reclaimer 04BG-RC-01."""
    flat = _nb(report_md)
    for window in re.finditer(r"[^|\n]{0,200}04BG-RC-01[^|\n]{0,200}", flat):
        text = window.group(0)
        assert not re.search(r"2 stackers|two stackers|2 dedicated stackers",
                             text), text
    assert "1 reclaimer `04BG‑RC‑01`" in report_md or \
           "1 reclaimer 04BG-RC-01" in flat


def test_table20_and_b02_reclaimer_rates_stay_separately_traceable(report_md):
    flat = _nb(report_md)
    assert "2,100/2,500 TPH humid vs B02 expansion interface: 2,600/3,120" \
        in flat
    assert "not resolved by inference" in flat
    # never asserted as the same physical machine
    assert "same physical reclaimer" not in flat


def test_table20_provenance_is_split_into_three_levels(scope_md):
    section = scope_md[
        scope_md.index("Benguerir — six physical piles"):]
    section = section[:section.index("**Current simulator coverage:**")]
    assert "Table 20 document no./rev" in section
    assert "User-confirmed" in section
    assert "none invented" in section


def test_no_active_text_claims_the_raw_capacity_is_untabulated(scope_md, s3):
    assert "no tabulated capacities" not in scope_md
    blob = " ".join(f"{a.text} {a.placeholder} {a.impact}"
                    for a in s3.assumptions)
    assert "no tabulated capacities" not in blob
    assert "400,000 t total live capacity" in blob


# ---- 9. determinism ----------------------------------------------------------
def test_signatures_unchanged_by_the_cleanup_pass():
    def sig_of(sc, budget):
        res = solve_plan(sc, SolveOptions(max_time_s=budget))
        sig = tuple((j.machine, j.space, j.dest_id, j.start_bucket,
                     j.end_bucket, j.tonnage_t) for j in res.jobs)
        return hashlib.sha256(json.dumps(sig).encode()).hexdigest()[:16]

    assert sig_of(datasets.sulphur_minimal(), 2) == "9150a04e26a9aa61"
    # Rebaselined 13-Sep-2026: the campaign/parcel floor (CAMPAIGN_POLICY)
    # and the corrected continuous-feed service test change which buckets
    # the planner activates, so the material allocation differs. Mass
    # balance, link/line capacity and machine exclusivity are re-verified
    # from the resulting minute schedule in test_schedule_fragmentation.py.
    # Minute positioning is post-solve and leaves material signatures intact.
    assert sig_of(datasets.mph_phosphate(), 2) == "b498a9d55c2e6283"
    s3_bg_train = datasets.end_to_end()
    datasets.apply_planning_demand_profile(s3_bg_train, "BG_TRAIN")
    # Rebaselined for the commissioned seven-day BG_TRAIN demand calendar.
    assert sig_of(s3_bg_train, 3) == "853415c7f5070d37"
