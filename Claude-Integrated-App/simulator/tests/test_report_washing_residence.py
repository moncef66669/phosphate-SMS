"""Focused report/register consistency checks for washing residence timing."""
import importlib.util
import re
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = PROJECT_ROOT / "report_sources"
REPORT_DIR = PROJECT_ROOT / "generated_outputs" / "reports" / "current"
EXPECTED = "30 min residence · ASSUMED"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _first_text_diagram_after(markdown: str, heading: str) -> str:
    section = markdown.split(heading, 1)[1]
    return section.split("```text", 1)[1].split("```", 1)[0]


def test_s3_summary_table_carries_washing_residence_assumption():
    assumptions = _read(PROJECT_ROOT / "simulator" / "ASSUMPTIONS.md")
    s3_table = assumptions.split(
        "### S3 — Three-site phosphate value chain", 1)[1].split(
            "## B. Engine/model simplifications", 1)[0]
    row = re.search(r"^\| S3-A26 \|.*$", s3_table, flags=re.MULTILINE)
    assert row, "S3-A26 is missing from the top S3 assumptions table"
    assert "**ASSUMED overall washing-line residence = 0.5 h (30 min)**" \
        in row.group(0)
    assert (
        "`WASHING_LINE_RESIDENCE_TIME_MODELLED="
        "{LOUTA:true,BENGUERIR:false}`"
    ) in row.group(0)
    assert "zero-start WIP buffers" in row.group(0)
    assert "minute-level 30 min dependency" in row.group(0)


def test_html_washing_steps_show_the_compact_assumed_residence():
    for path in (
        SOURCE_DIR / "_scope.html",
        REPORT_DIR / "_report.html",
    ):
        cards = re.findall(
            r'<div class="step key"[^>]*>'
            r'<strong>WASHING / BENEFICIATION</strong>.*?</div>',
            _read(path),
            flags=re.DOTALL,
        )
        assert len(cards) == 2, path
        assert all(EXPECTED in card for card in cards), path


def test_markdown_washing_diagrams_show_the_compact_assumed_residence():
    for path in (
        SOURCE_DIR / "MPH-OSBL-SMS-Job-Planning-Automation-Scope.md",
        REPORT_DIR / "MPH-SMS-Job-Allocation-Project-Report.md",
    ):
        markdown = _read(path)
        benguerir = _first_text_diagram_after(
            markdown,
            "### 13.3 Benguerir — two connected yards",
        )
        louta = _first_text_diagram_after(
            markdown,
            "### 13.5 Louta — four stages",
        )
        assert EXPECTED in benguerir, path
        assert EXPECTED in louta, path


def test_rev_x_states_the_exact_assumed_duration():
    spec = importlib.util.spec_from_file_location(
        "_revision_washing_residence",
        SOURCE_DIR / "_revision.py",
    )
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    rev_x = next(row for row in revision.REVISIONS if row.rev == "X")
    assert "0.5 h (30 min) ASSUMED" in rev_x.scope


def test_issued_pdf_carries_the_compact_and_exact_duration_labels():
    pypdf = pytest.importorskip("pypdf")
    pdf = pypdf.PdfReader(str(
        REPORT_DIR / "MPH-SMS-Job-Allocation-Project-Report.pdf"))
    text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert EXPECTED in text
    # Chrome may split adjacent glyph/font runs without preserving the
    # visual space in extracted PDF text.
    assert re.search(r"0\.5 h \(30 min\)\s*ASSUMED", text)
