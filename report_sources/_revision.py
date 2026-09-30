"""Issued revision and concise revision history — one source of truth."""
from datetime import datetime
from typing import List, NamedTuple


class RevRow(NamedTuple):
    rev: str
    date: str
    scope: str
    status: str = "Draft — for analysis"


# Oldest first. Detailed change evidence remains in source control and the
# evidence register; the report carries only decision-relevant summaries.
REVISIONS: List[RevRow] = [
    RevRow("A", "22 Jun 2026", "Initial project-scope note (§§1–13)",
           "Draft — for review"),
    RevRow("B", "03 Jul 2026", "Functional design added; report consolidated",
           "Draft"),
    RevRow("C", "12 Jul 2026",
           "ASP interface, route ownership, optimisation formulation, evidence "
           "register and measurable acceptance added"),
    RevRow("D", "13 Jul 2026",
           "Domain A reframed as Louta + Benguerir + M16 peer controlled yards"),
    RevRow("E", "15 Jul 2026",
           "Objective, benefit gates and operator-supervised boundary added"),
    RevRow("F", "16 Jul 2026",
           "Impact framework condensed; baseline/pilot KPI measures expanded"),
    RevRow("G", "19 Jul 2026",
           "Benguerir B02 washed-yard basis, assets, autonomy and outlets corrected"),
    RevRow("H", "20 Jul 2026",
           "Benguerir Table-20 upstream topology and capacity conflict corrected"),
    RevRow("I", "21 Jul 2026",
           "Raw/screened and washed material/grade profiles separated"),
    RevRow("J", "21 Jul 2026",
           "Editorial consolidation: repeated narrative replaced by canonical "
           "tables and block diagrams; evidence and engineering caveats retained"),
    RevRow("K", "22 Jul 2026",
           "Benguerir washing-line lifecycle baseline added to the mimic as "
           "display/reference information"),
    RevRow("L", "22 Jul 2026",
           "Corridor proximity corrected: Louta/C1 nearer, Benguerir/C2 "
           "farther; provisional distance, lag and in-transit mapping reversed"),
    RevRow("M", "22 Jul 2026",
           "Benguerir upstream 400,000 t capacity allocated equally: "
           "BG-RAW-1 and BG-RAW-2 at 200,000 t each"),
    RevRow("N", "23 Jul 2026",
           "Benguerir upstream static t0 demonstration baseline introduced "
           "(superseded by Rev S); grade and timestamped SMS telemetry unavailable"),
    RevRow("O", "24 Jul 2026",
           "Louta BT raw profile adopted as an assumed Benguerir "
           "raw-grade proxy; no live or pile-specific Benguerir assay"),
    RevRow("P", "24 Jul 2026",
           "Benguerir raw-pile display proxy expanded to assumed 50/50 "
           "multi-profile compositions with mass-weighted chemistry"),
    RevRow("Q", "24 Jul 2026",
           "Louta product piles set to 50% initial fill and assigned assumed "
           "multi-profile distributions with aggregate planning chemistry"),
    RevRow("R", "24 Jul 2026",
           "Added the user-confirmed stockpile repère convention (R0–R1000), "
           "metre-canonical conversion and remaining coordinate/ICD inputs"),
    RevRow("S", "24 Jul 2026",
           "Benguerir raw display baseline set to 50%; component hover now "
           "reports full grade, tonnes and display repère range; Initial/Final "
           "plan state views use the true terminal boundary"),
    RevRow("T", "25 Jul 2026",
           "Louta corrected to the equipment-tagged process description: "
           "two homogenized ore stockpiles (494,274 t total, one shared "
           "stacker, two bridge reclaimers), washing L1/L2 as two current "
           "parallel lines, and 102MG mine storage separated from the ore "
           "and product stages in the evidence register"),
    RevRow("U", "25 Jul 2026",
           "M16 corrected to PFD-0001[3]: four two-hangar bays with the "
           "machine lines interleaved between them, bay-adjacency machine "
           "reach replacing all-to-all reachability, and two separate "
           "clamshell gate banks with the PAP/drying confluence recorded "
           "as TBC"),
    RevRow("V", "25 Jul 2026",
           "M16 evidence and solver consistency: the clamshell gate banks "
           "stay confirmed discharge equipment while the PAP-versus-drying "
           "selection becomes a separate derived/TBC route abstraction, and "
           "current reception is modelled as one shared 2,600 t/h path "
           "because the second reception conveyor is future"),
    RevRow("W", "26 Jul 2026",
           "Louta 102MG assumed demonstration baseline adopted: two "
           "100,000 t display capacities at 50,000 t t0 each, with two "
           "25,000 t raw-profile bands per pile; actual values remain TBC, "
           "the assets stay outside solver/KPIs, and the confirmed 494,274 t "
           "remains exclusive to 110/120LG. M16 mimic bays are redrawn as "
           "side-by-side horizontal hangar pairs with each shared machine "
           "line visibly spanning both; topology, inventory and solver inputs "
           "are unchanged"),
    RevRow("X", "26 Jul 2026",
           "Washing timing added: documented scrubber residence is 2.5-3 min "
           "at Louta and 3 min at Benguerir; overall washing-line residence "
           "is separately set to 0.5 h (30 min) ASSUMED by the project team "
           "at both sites as display/config metadata, not a solver delay"),
    RevRow("Y", "28 Jul 2026",
           "Louta S0 stocked route made solver-backed from finite mine "
           "handoffs through 110/120LG, explicit L1/L2 recovery and a "
           "minimum 30-minute stage dependency, 130/140LG and C1. Coverage "
           "rewards and token jobs are disabled; 102MG remains a documented "
           "route/rate/reach modelling gap"),
    RevRow("Z", "28 Jul 2026",
           "Louta residence hardened to cumulative matured mass after washing "
           "completion; infeasible C1-versus-line targets are rejected. "
           "Schedule terminology now separates 140LG reclaim operation from "
           "its corridor-dispatch movement and purpose"),
    RevRow("AA", "28 Jul 2026",
           "Job Schedule replaces the dispatch color/category with explicit "
           "non-material machine positioning: green for minute-reserved "
           "estimates and red for gaps requiring targeted replanning. "
           "Positioning carries time/from-to details but no tonnage; material "
           "allocation stays separate, and unresolved exact-profile jobs are "
           "withheld from ASP export until their timing validates"),
    RevRow("AB", "28 Jul 2026",
           "Raw grades made mine-neutral and shared by Louta and Benguerir: "
           "RAW-TBC plus RAW-BT, RAW-BT-HIGH-CD and RAW-BT-LOW-CD. Site material "
           "profiles retain separate moisture, PSD and live-assay metadata; "
           "legacy grade IDs remain import-compatible"),
    RevRow("AC", "08 Sep 2026",
           "Implemented weekly multi-site planning, dated multi-campaign demand, "
           "stage-aware physical/TSM mass conversion, persistent grade and yield "
           "configuration, consolidated timed resource controls, explicit results "
           "and M16 pile-construction blending with fixed or jointly optimised shares"),
    RevRow("AD", "08 Sep 2026",
           "Recovered the report-aligned app; hardened blend execution, mass and "
           "assay accounting, timing dependencies, campaign priorities and demand "
           "editing; added copied stress scenarios, corrected outcome KPIs and "
           "reproducible local-SMS versus coordinated business-case experiments"),
]

# Keep the full audit history above, but print a compact decision-relevant view.
# Detailed Rev A-G changes remain recoverable from source control and the
# evidence register, so they do not consume the report's first page.
DISPLAY_REVISIONS: List[RevRow] = [
    RevRow("A-G", "22 Jun-19 Jul 2026",
           "Earlier scope, functional-design, interface, evidence and B02 "
           "development", "Superseded - see source history"),
    *REVISIONS[-3:],
]

REV = REVISIONS[-1].rev
REV_DATE_SHORT = REVISIONS[-1].date
REV_DATE = datetime.strptime(REV_DATE_SHORT, "%d %b %Y").strftime("%d %B %Y")
REV_DATE = REV_DATE.lstrip("0")


def check_order(rows: List[RevRow] = None) -> None:
    """Fail loudly if the printed Date column is not oldest-first."""
    rows = REVISIONS if rows is None else rows
    dates = [datetime.strptime(r.date, "%d %b %Y") for r in rows]
    for prev, cur, row in zip(dates, dates[1:], rows[1:]):
        if cur < prev:
            raise SystemExit(
                f"revision history out of order at Rev {row.rev} "
                f"({row.date}) — insert new rows by date, not at the end")


def as_html() -> str:
    check_order()
    head = ('<tr><th style="width:6%;">Rev.</th>'
            '<th style="width:12%;">Date</th><th>Scope of revision</th>'
            '<th style="width:17%;">Status</th></tr>')
    body = "\n".join(
        f"<tr><td>{r.rev}</td><td>{r.date}</td><td>{r.scope}</td>"
        f"<td>{r.status}</td></tr>" for r in DISPLAY_REVISIONS)
    return ('\n<table class="nb compact" style="margin:4px 0 8px;">\n'
            + head + "\n" + body + "\n</table>")


def as_markdown() -> str:
    check_order()
    rows = "\n".join(
        f"| {r.rev} | {r.date} | {r.scope.replace('&amp;', '&')} | "
        f"{r.status} |" for r in DISPLAY_REVISIONS)
    return ("\n| Rev. | Date | Scope of revision | Status |\n"
            "|---|---|---|---|\n" + rows + "\n")
