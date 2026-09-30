"""Section-reference protection shared by `_merge.py` and `_mergemd.py`.

The consolidated report renumbers the Functional Design's INTERNAL sections
(FD §1–§5 become report §14–§18, so `§2.3` becomes `§15.3`).  That mapping
must never touch a section number that belongs to an EXTERNAL document
citation: `Q80130-BEN-B02-ME-BOD-0001 Rev B §2.3.2` is a reference into the
BBE mechanical Basis of Design and stays `§2.3.2` forever.

Earlier revisions protected external citations with a hand-listed set of
literal prefixes (`'2025 §2.'`, `'B02 §2.'`, ...).  That list silently missed
the shorter `Rev B §2.3.2` form — the document number uses non-breaking
hyphens, so `B02` is followed by `‑ME`, not by a space — and the
citation was renumbered to the non-existent `§15.3.2`.  The rule below is
structural instead of literal: a section reference is external when an
external-document cue precedes it inside the same phrase.
"""
import re

#: Sentinel that survives the renumbering pass. NUL never occurs in the
#: sources, so it cannot collide with real content.
_SENTINEL = "\x00SEC\x00"

#: Cues that mark the text as an external document citation. `‐-―`
#: covers the typographic hyphens used in the document numbers.
_DOC_CUE = (r"(?:Q80130[0-9A-Za-z‐-―\-]*"
            r"|Table\s*20"
            r"|B0[12]\b"
            r"|FRS\b"
            r"|BoD\b"
            r"|Basis of Design)")

#: A section reference that follows such a cue within the same phrase. The gap
#: may not cross markup (`<`/`>`), a Markdown table-cell boundary (`|`), a line
#: break, or another section mark — those separate one citation from the next.
_EXT_REF = re.compile(_DOC_CUE + r"(?:[^<>|§\n]{0,80}?)§§?\s*\d")

_MAX_PASSES = 8  # several references may share one leading cue


def protect_external_sections(text: str) -> str:
    """Mask the `§` of every EXTERNAL document section reference."""
    for _ in range(_MAX_PASSES):
        masked = _EXT_REF.sub(
            lambda m: m.group(0).replace("§", _SENTINEL), text)
        if masked == text:
            break
        text = masked
    return text


def restore_sections(text: str) -> str:
    """Undo `protect_external_sections` after internal renumbering."""
    return text.replace(_SENTINEL, "§")
