"""Single source of the CURRENT report revision shown in the app.

The report's revision letter/date live in `report_sources/_revision.py`
(`REVISIONS[-1]`), which is the one source of truth for the generated
deliverable. The simulator is a sibling of that folder rather than an importer
of it, so this module READS the file and parses the last `RevRow`. Nothing is
hard-coded: an out-of-tree checkout falls back to a neutral label instead of
claiming a revision it cannot verify.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Tuple

#: report_sources/_revision.py relative to this file (simulator/ui/..)
_REVISION_FILE = next(
    (parent / "report_sources" / "_revision.py"
     for parent in Path(__file__).resolve().parents
     if (parent / "report_sources" / "_revision.py").is_file()),
    Path(__file__).resolve().parents[2] / "report_sources" / "_revision.py")
_ROW = re.compile(r'RevRow\(\s*"([A-Z][A-Z\-]*)"\s*,\s*"([^"]+)"')
_FALLBACK = ("—", "")


def _read_last_revision() -> Tuple[str, str]:
    try:
        text = _REVISION_FILE.read_text(encoding="utf-8")
    except OSError:
        return _FALLBACK
    rows = _ROW.findall(text)
    # the display list re-uses RevRow for a summary row ("A-G"); the real
    # current revision is the last single-letter entry
    single = [r for r in rows if "-" not in r[0]]
    return single[-1] if single else (rows[-1] if rows else _FALLBACK)


REV, REV_DATE = _read_last_revision()


def report_ref(section: Optional[str] = None) -> str:
    """Citation label, e.g. `report Rev U §16` — never a hard-coded letter."""
    if REV == _FALLBACK[0]:
        return "project report" + (f" {section}" if section else "")
    label = f"report Rev {REV}"
    return f"{label} {section}" if section else label


def report_stamp() -> str:
    """Revision + date, e.g. `Rev U · 25 Jul 2026`."""
    if REV == _FALLBACK[0]:
        return "revision unavailable"
    return f"Rev {REV}" + (f" · {REV_DATE}" if REV_DATE else "")
