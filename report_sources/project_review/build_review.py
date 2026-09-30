"""Build the review from source narrative and saved paired experiment evidence."""
from pathlib import Path
import html
import json
import re

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "generated_outputs" / "project_review"
OUT.mkdir(parents=True, exist_ok=True)
summary_path = OUT / "experiments" / "summary.json"
studies = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else []
parts = []
markdown = []


def title(text, level=2):
    parts.append(f"<h{level}>{html.escape(text)}</h{level}>")
    markdown.append("#" * level + " " + text)


def paragraph(text, css=""):
    parts.append(f'<p class="{css}">{html.escape(text)}</p>')
    markdown.append(text)


def table(headers, rows):
    opening = '<table class="keep">' if headers[0].startswith(("Current basis:", "Artifact")) else '<table>'
    parts.append(opening + '<thead><tr>' + ''.join('<th>'+html.escape(str(x))+'</th>' for x in headers) + '</tr></thead><tbody>' + ''.join('<tr>'+''.join('<td>'+html.escape(str(x))+'</td>' for x in row)+'</tr>' for row in rows) + '</tbody></table>')
    markdown.append('| ' + ' | '.join(headers) + ' |\n| ' + ' | '.join('---' for _ in headers) + ' |\n' + '\n'.join('| '+' | '.join(str(x).replace('|','/') for x in row)+' |' for row in rows))


title("MPH · Project review and business-case evidence", 1)
paragraph("8 September 2026 · Review of report Rev AC and the recovered current application · Engineering simulation, not commissioned control", "subtitle")
title("Decision brief")
paragraph("The project has a substantial working foundation: seven-day planning, coordination across three SMS, physical resource constraints, dated demand campaigns and M16 blend construction. This review recovered the application matching report Rev AC from the OneDrive backup and restored it in Claude-Integrated-App. The main launcher now uses this copy; original files and histories remain preserved.")
paragraph("The implemented corrections strengthen the credibility of the results: physical mass conservation, actual blend readiness, missing assays, campaign priorities, operation dependencies and consistent performance measures. The operator comparison is reproducible and separates illustrative simulation results from industrial benefits still to be demonstrated.")
table(["Finding", "Demonstrated result", "Evidence boundary"], [
    ["Controlled terminal service", "600 / 600 t for the strongest local rule and the model", "No demonstrated tonnage uplift"],
    ["Receiving congestion", "Peak: 200 to 0 t; holding: 750 to 0 tonne-hours", "Assumed rates and finite 500 t receiving buffer"],
    ["No-interruption control", "Same service, no additional holding for an effective local protocol", "Null result retained"],
    ["Investment return", "No annual saving presumed", "Requires operating data and validated costs"],
])
paragraph("The supported value in the controlled case is lower receiving congestion and less temporary stock. This example does not establish a production, staffing or margin gain for the plant. The completed tooling can test those hypotheses using explicit local protocols, common inputs and verifiable acceptance criteria.", "callout")

title("1. Scope and recovered application")
paragraph("The requested generated/_outputs path is obsolete; the reviewed PDF is generated_outputs/reports/current/MPH-SMS-Job-Allocation-Project-Report.pdf. The original simulator folder was materially older than Rev AC. The newer application was recovered from Desktop/Desktop onedrive backup/SMS/.../Claude-Integrated-App into this workspace. recovery_manifest.json records source and destination paths and initial file hashes. The backup and older simulator remain preserved; the root start/stop scripts now target the recovered app.")
table(["Area", "Current model", "Evidence boundary"], [
    ["Three-site planning", "Louta, Benguerir and M16 with delayed C1/C2 transport", "Engineering capacities coexist with assumed stock, assays, targets and route detail"],
    ["Demand", "Seven-day dated grade campaigns, resource availability and copied stress cases", "Scenario demand is not an approved production forecast"],
    ["Benguerir / Louta processes", "Finite stock/WIP and recovery-bearing campaign transfers", "Configured grade transformation; no calibrated process chemistry model"],
    ["M16 blending", "Fixed or optimized construction matrix, integer physical tonnes, ready boundary and execution lock", "No spatial layer/reclaim-face model; other blend points remain outside implementation"],
    ["Execution / integration", "Minute timing validation, ownership partition and simulated ASP gate", "Read-only inventory connector uncommissioned; no live plan submission or PLC actuation"],
    ["Economics", "Paired outcome comparison and explicit contribution/cost sensitivity", "No governed GradeCostModel or approved financial baseline"],
])

title("2. Findings and implemented improvements")
table(["ID / significance", "Finding", "Implemented response"], [
    ["R01 · critical", "Current launcher and current report referred to different generations of the app", "Recovered matching app, preserved original copies and recorded provenance; launcher corrected"],
    ["R02 · critical", "An interrupted construction campaign could be treated as ready by time alone", "Require actual component completion and readiness; lock recipe on first physical stacking; withhold blends with unknown or violating hard receiving assays"],
    ["R03 · critical", "Missing component assays could disappear from averages", "Unknown chemistry remains unknown; constrained unknown material cannot become a zero-valued diluent; explicit unknown-tonnage KPIs"],
    ["R04 · high", "Fixed shares could report chemistry different from whole-tonne scheduled composition", "Calculate fixed blend chemistry from the actual integer-tonne allocation; reject rounded-away required components"],
    ["R05 · critical", "Invalid minute jobs or their dependent stages could imply available material", "Hold invalid execution instructions and propagate producer failures through dependent stages and integration validation"],
    ["R06 · high", "Replay could lose mass in full-target exogenous transfers and omit process-feed actuals", "Clip source withdrawal to target space first; credit physical process input separately from recovered output; track unstacked actuals"],
    ["R07 · high", "KPI totals hid earlier shortages, counted repeated handling as output, and used stale pile chemistry", "Due-bucket terminal service; daily grade/priority process service; exact flows and selected blend quality; terminal-versus-handled tonnes; mass on-spec and unknown quality"],
    ["R08 · high", "Partial/replanned KPI views used inconsistent horizon or elapsed-time bases", "Offset-aware plan arrays; elapsed-minute actual demand and inventory; window-aware quality; physical safety exposure"],
    ["R09 · high", "Demand apply could flatten hourly shape, mutate partially or clear C2", "Atomic synchronization, retain detailed shape, real clock offsets, stage checks and preserve C2 by default with visible reset option"],
    ["R10 · high", "Grade deletion and persistence missed operational references or invalid numbers", "Guard designated/exogenous/construction references, validate finite chemistry/conversions and use unique atomic catalogue writes"],
    ["R11 · medium", "Unmet penalties ignored receiving-unit priority; dated campaign urgency was not represented", "Optional per-campaign priority with unit-level inheritance, preserved across resampling/replanning; objective coefficients and daily process groups respect priority; optional weight profiles and history"],
    ["R12 · high", "No defensible operator counterfactual or attributable benefit calculation", "Independent site-local protocols, stronger baseline selection, common physical replay, null controls, scenario fingerprints, full exports and guarded valuation"],
    ["R13 · high", "Consolidated navigation made the existing inventory snapshot controls unreachable", "Restore the snapshot panel in Configure, preserve preview/apply gates and keep stale-preview explanations visible across navigation"],
])
paragraph("These changes do not invent engineering ratings, approved grade limits or actual operator behavior. Default grade data and the saved operator catalogue were retained. Stress cases are copied scenarios with parameter and change manifests. Site assay data, equipment limits and project hypotheses remain distinguishable.")
paragraph("Stock and delivery mixing preserve unknown assays instead of filling them with zero. Chemical composition uses dry-solids weighting when assay or configured stage moisture is available; legacy vectors without moisture retain an equal-dry-fraction assumption. Washing outputs still use explicitly configured output-grade vectors: those are declared process-model assumptions, not inferred laboratory measurements.")
paragraph("Additional scheduling and replay hardening reserves cross-pile preparation against effective machine/route throughput and audits the resulting minute schedule. A bounded alternative job-order retry resolves shared-belt fragmentation only when the complete schedule passes physical validation. Exact route profiles now determine moved mass, outages and individual/shared link capacities; unallocated routes are not silently substituted. Migration is idempotent after upgrade and fresh S2/S3 factories are canonical, preventing navigation from clearing a newly generated plan. Explicit unknown-quality markers preserve unassayed stock and in-transit parcels across snapshot, JSON save/load and replanning.")

title("3. KPI and objective design")
table(["Measure", "Definition", "Decision use"], [
    ["Terminal on-time service", "Σ min(delivery, demand) within each due bucket / terminal demand", "Detect shortages that later deliveries cannot erase"],
    ["Process-feed service", "Credit physical feed inside the same date/grade/priority campaign", "Avoid mixing internal feed with saleable output or borrowing across dates"],
    ["Known on-spec mass", "Compliant terminal tonnes / tonnes delivered to units with configured limits", "Unknown assays remain in the denominator; no evaluated mass gives N/A"],
    ["Conforming on-time tonnes", "Demand-capped terminal tonnes with known compliant chemistry", "Only eligible physical quantity for the optional production-value sensitivity"],
    ["Handled tonnes", "Total physical movements across all stages", "Workload and handling intensity; never labelled final production"],
    ["Safety exposure", "Σ max(safety stock − inventory, 0) × physical hours", "Compare inventory resilience across different horizons/resolutions"],
    ["Receiving holding", "Peak queue, terminal queue and end-of-bucket tonne-hours", "Coordination/staging burden in the controlled experiment"],
    ["Execution integrity", "Timing failures, physical audit errors and withheld dependent jobs", "A FEASIBLE tactical score alone cannot support an executable-benefit claim"],
])
table(["Optional profile", "Unmet", "Off-spec", "Giveaway", "Moves", "Safety", "Process / utilization"], [
    ["Balanced (existing)", 1000, 200, 5, 2, 20, "0 / 0"],
    ["Quality emphasis", 1000, 500, 10, 2, 20, "0 / 0"],
    ["Inventory continuity", 1000, 200, 5, 2, 100, "0 / 0"],
])
paragraph("These profiles are uncalibrated scenario assumptions, not recommended operating targets. Weighted objectives permit trade-offs: making unmet weight larger than each individual secondary weight does not establish a lexicographic guarantee over a long horizon. Hard receiving limits remain a separate constraint choice. Process cost stays inactive until governed cost curves exist. Compare operational KPIs across profiles; do not compare objective scores with different coefficients as a financial benefit.")

title("4. Operator comparison: experimental design and results")
paragraph("The independent-SMS experiment uses a synthetic 12-hour reduction: two mine product inventories, two one-hour corridors, a shared receiver, local M16 stocks and a hard 71 BPL 50/50 receiving blend. Every arm has the same 600 t final demand, initial stock, fixed 250 t nomination per mine, rates, calendars and finite 500 t holding interface. Local mine functions receive only local state and their nomination. The M16 rule sees its own demand/calendar and current arrivals. No random errors, deliberate off-spec choices or optimizer-authored local jobs are used.")
paragraph("Three protocols are retained: earliest release, even pacing, and agreed alternating slots. The strongest valid local protocol is selected by on-time service, then known compliance, then less holding. The coordinated model sees all site state. A common independent replay checks paths, inventories, capacities, calendars, exclusivity, delays, quality and total mass. Missed reception remains held material; overflow invalidates the plan instead of generating a monetizable disposal loss.")
rows = []
for study in studies:
    if not study["study"].startswith("coordination"):
        continue
    for arm_id, arm in study["arms"].items():
        k = arm["kpis"]
        rows.append([study["study"].replace("coordination_", ""), arm["label"].replace("Independent SMS: ", "").replace("Coordinated model: shared site state", "coordinated"),
                     f"{k.get('final_product_on_time_t', 0):,.0f}",
                     f"{k.get('receiving_queue_peak_t', 0):,.0f}",
                     f"{k.get('receiving_queue_t_h', 0):,.0f}",
                     "pass" if not arm["physical_errors"] and arm.get("plan_status") in ("FEASIBLE", "OPTIMAL") else "invalid"])
table(["Study", "Policy", "On-time t", "Peak held t", "Holding t·h", "Physical audit"], rows)
outage = next((s for s in studies if s["study"] == "coordination_outage"), None)
if outage:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    labels = ["Earliest release", "Even pacing", "Alternating slots", "Coordinated"]
    values = [a["kpis"].get("receiving_queue_t_h", 0) for a in outage["arms"].values()]
    fig, ax = plt.subplots(figsize=(9, 2.7), layout="constrained")
    bars = ax.barh(labels, values, color=["#9caebb"] * 3 + ["#208574"], height=.6)
    ax.invert_yaxis()
    ax.bar_label(bars, labels=[f"{v:,.0f}" for v in values], padding=5)
    ax.set_xlim(0, max(values) * 1.14)
    ax.set_xlabel("Receiving holding exposure [t·h] · all policies deliver 600 t")
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.set_axisbelow(True)
    ax.grid(axis="x", alpha=.18)
    chart = OUT / "coordination-holding.png"
    fig.savefig(chart, dpi=180, facecolor="white")
    fig.savefig(OUT / "coordination-holding.svg", facecolor="white")
    plt.close(fig)
    parts.append('<figure><img style="width:100%" src="coordination-holding.png" alt="Holding exposure: earliest 1400, paced 800, alternating 750, coordinated zero tonne-hours"><figcaption>Controlled outage case; assumed finite holding capacity, identical terminal service.</figcaption></figure>')
    markdown.append("![Receiving holding exposure](coordination-holding.png)")
paragraph("Result: the outage case demonstrates 200 t less peak staging and 750 t·h less holding against the strongest local rule; all policies deliver 600 t. The no-outage control preserves the null result. Higher receiving capacity is included as a sensitivity. The model does not optimize the added holding buffer, so its no-holding plan is a conservative feasible member of the common replay. Neither the buffer nor the scaled capacities represent confirmed MPH equipment.", "callout")

title("5. Full-project sensitivity and remaining modeling gaps")
paragraph("The second experiment runs the actual current project scenario with local FIFO/nearest and quality-aware source portfolios versus unconstrained coordinated allocation. Timing, cross-site dispatch, processes and blending still share the same global CP-SAT scheduler across these arms. This is an optimistic source-selection baseline, explicitly separate from the genuinely site-blind experiment. It cannot be labelled a full-plant human simulation.")
rows = []
for study in studies:
    if not study["study"].startswith("project_"):
        continue
    for arm in study["arms"].values():
        k = arm["kpis"]
        rows.append([study["study"], arm["label"], arm.get("plan_status", ""),
                     f"{k.get('final_product_on_time_t', 0):,.0f}", len(arm["physical_errors"]),
                     "; ".join(arm["physical_errors"][:2]) or ("No comparison audit rejection" if arm.get("plan_status") in ("FEASIBLE", "OPTIMAL") else "No feasible solver result")])
if rows:
    table(["Case", "Policy", "Solver", "Planned on-time t", "Rejections", "Qualification"], rows)
    for study in studies:
        if study["study"].startswith("project_"):
            if study.get("deltas"):
                d = study["deltas"]
                paragraph(f"{study['study']}: versus {study.get('best_local_id')}, signed on-time change {d.get('final_product_on_time_t', 0):+,.0f} t; known conforming on-time change {d.get('final_product_on_spec_on_time_t', 0):+,.0f} t. This source-policy sensitivity shares global scheduling across arms and remains distinct from the independent-SMS experiment.")
            else:
                paragraph(f"{study['study']}: no valid paired benefit claim; inspect solver and timing qualifications in the exported plans.")
else:
    paragraph("Full-project result files are pending the final execution-timing verification. The app already supports Current basis, Evening peak, Reclaimer outage, Tighter receiving quality and Low M16 stock cases; results are withheld from monetary comparison when solve or timing checks fail.")
baseline = next((s for s in studies if s["study"] == "project_baseline"), None)
if baseline:
    k = baseline["arms"]["coordinated"]["kpis"]
    table(["Current basis: receiving unit", "Demand t", "On-time t", "Service %"], [
        [key, f"{d['demand_t']:,.0f}", f"{d['on_time_t']:,.0f}", f"{d['service_pct']:.1f}"]
        for key, d in k.get("per_destination", {}).items() if d["demand_t"] > 0])
    paragraph(f"Current-basis diagnosis: terminal demand is {k.get('final_product_demand_t', 0):,.0f} t and unmet terminal demand is {k.get('final_product_unmet_t', 0):,.0f} t. Of {k.get('quality_evaluated_t', 0):,.0f} delivered tonnes subject to configured quality limits, {k.get('on_spec_mass_t', 0):,.0f} t are compliant and {k.get('off_spec_mass_t', 0):,.0f} t violate at least one limit. Another {k.get('quality_unspecified_demand_t', 0):,.0f} t of demand has no configured receiving assay limits; it is not silently certified as conforming product.")
    paragraph("The default DRY-FEED Cd limit is an assumed soft limit. Its violations therefore represent an allowed objective trade-off, not proof of compliance. PAP-1 service and end-to-end material availability require demand/supply reconciliation before the current weekly scenario becomes an operating target. Internal campaign completion alone cannot establish final-customer service.")
    decisions = baseline["arms"].get("local_fifo", {}).get("decisions", [])
    retained = sum("Retained all sources" in d.get("reason", "") for d in decisions)
    if decisions and retained == len(decisions):
        paragraph("Comparator activation: every active local source portfolio in all three saved full-project cases retains all sources because blending, replenishment or dated grades require flexibility. Thus these zero source-selection deltas have no active policy contrast and cannot establish operator/model equivalence. The controlled independent-SMS study remains the evidence for the information-sharing mechanism; a full-plant operator study requires explicit local dispatch rules over the entire process chain.")
paragraph("Remaining gaps require explicit treatment: surveyed geometry and route confluences; measured grade/assay distributions; local communication and backpressure protocols; actual operator job history; spatial layer/reclaim-face chemistry; calibrated washing product response; approval of non-M16 blend points; and complete live integration contracts. A large initial stock can legitimately hide the value of upstream coordination. Low-stock and disruption cases reveal dependencies but must retain their assumed status. A time-limited feasible incumbent is not a proof of optimum.")
paragraph("One remaining timing limit is conservative treatment of process output supported only by nonzero initial process-buffer stock; current default buffers start empty. The example exports have been refreshed to the current model, including the seven-day S3 horizon. Normal startup uses the current factory scenario plus the saved grade catalogue.")

title("6. Verification and reproducibility")
for filename, label in (("review_acceptance.log", "Final focused acceptance"), ("sample_acceptance.log", "Refreshed sample verification"), ("inventory_ui_acceptance.log", "Inventory and navigation verification"), ("final_regression.log", "Broad regression before final corrections")):
    path = OUT / filename
    if path.exists():
        raw = path.read_bytes()
        lines = raw.decode("utf-16" if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else "utf-8", errors="replace").splitlines()
        summaries = [line for line in lines if re.search(r"\d+ passed", line)]
        paragraph(label + ": " + (summaries[-1] if summaries else "run pending completion"))
paragraph("The broad regression records the code before the final bounded timing-order retry and ordinary-transfer assay correction; its provenance footer identifies source changes. Final focused acceptance and regenerated experiment audits take precedence for those corrections. Detailed broad-suite failures are classified in TEST_FAILURE_CLASSIFICATION.md. An earlier run during edits is retained as diagnostic evidence only. Browser QA checks the rendered comparison page, local-policy run, no-outage control, current-project controls and export behavior.")
table(["Artifact", "Purpose"], [
    ["generated_outputs/project_review/experiments/*/scenario.json", "Complete immutable input snapshot per study"],
    ["generated_outputs/project_review/experiments/*/comparison.json", "Every policy, plan, KPI, decision, qualification and provenance"],
    ["generated_outputs/project_review/recovery_manifest.json", "Original recovered source location and hashes"],
    ["Claude-Integrated-App/simulator/run_business_case.py", "Repeat controlled studies and optional project variants"],
    ["Claude-Integrated-App/simulator/BUSINESS_CASE_METHOD.md", "Operator information sets, fairness contract and physical boundaries"],
    ["generated_outputs/project_review/visual_qa/", "Rendered app/report evidence"],
])

parts.append('<section class="keep">')
title("7. Business-case completion plan")
table(["Step", "Required evidence", "Acceptance"], [
    ["1 · Approve the comparator", "Actual operator rules, SMS-visible information, nomination changes, communication delays and overrides", "Ops validates local protocols before gains are quoted"],
    ["2 · Matched historical replay", "Same timestamped inventory/assays, campaigns, machine state and disturbances for all policies", "Physical conservation, identical inputs and no invalid issued job"],
    ["3 · Hold-out and sensitivities", "Several normal and disrupted weeks, low stock, grade variability, alternate weights and stronger operator protocols", "Retain negative and null cases; show distribution, not selected best day"],
    ["4 · Shadow operation", "Proposed vs actual jobs, first-pass ASP acceptance, authoring minutes and validated replan latency", "Operator sign-off and preserved human overrides"],
    ["5 · Value and investment", "Incremental usable output or avoided delay/loss, verified holding/rehandling cost, CAPEX and OPEX", "Finance validates contribution, recurrence and non-overlapping loss categories"],
])
paragraph("For production sensitivity: annual net value = verified incremental conforming on-time tonnes per comparable period × assumed comparable periods/year × net incremental contribution per tonne − annual operating cost. First-year break-even additionally includes implementation cost. Earlier delivery earns full contribution only if it avoids a documented lost sale or production loss; otherwise use the actual avoided delay/penalty value. Holding tonne-hours do not automatically imply lost output or capital savings. The present controlled study has zero incremental conforming tonnes, so it cannot support a production-value ROI.")
paragraph("The recommended next decision is a governed replay/shadow validation using the completed comparison tooling, rather than approval of a numerical plant savings claim from demonstration inputs. Engineering-data and vendor-interface actions remain authoritative in the main project report; this review does not replace them.")
parts.append('<p class="source">Methodology reference: <a href="https://www.nist.gov/publications/credibility-consideration-digital-twins-manufacturing">NIST — Credibility consideration for digital twins in manufacturing</a>. Its emphasis on verification, validation and uncertainty supports separating software correctness from evidence that a model represents operating performance.</p>')
markdown.append("Methodology reference: [NIST — Credibility consideration for digital twins in manufacturing](https://www.nist.gov/publications/credibility-consideration-digital-twins-manufacturing).")
parts.append('</section>')

css = """
@page { size:A4; margin:17mm 15mm 17mm; }
body{font-family:Arial,sans-serif;color:#213445;font-size:9.5pt;line-height:1.45;margin:0}
h1{font-size:25pt;line-height:1.1;color:#153d5c;margin:0 0 10px}
h2{font-size:15pt;color:#153d5c;margin:24px 0 8px;break-after:avoid}
p{margin:7px 0}.subtitle{color:#677c89;font-size:9pt}.callout{background:#edf6f4;border-left:4px solid #208574;padding:12px}
table{width:100%;border-collapse:collapse;margin:9px 0 14px;font-size:8.2pt;table-layout:auto}
th{text-align:left;color:white;background:#214f70;padding:7px}td{padding:6px;border-bottom:1px solid #d6e0e6;vertical-align:top}
tr:nth-child(even){background:#f3f6f8}tr{break-inside:avoid}thead{display:table-header-group}a{color:#176f87}.source{font-size:8pt;color:#566a79}figure{margin:10px 0;break-inside:avoid}figcaption{font-size:8pt;color:#566a79}.keep{break-inside:avoid}
@media screen {body{max-width:1080px;margin:35px auto;padding:0 30px;font-size:14px}table{font-size:12px}}
"""
(OUT / "MPH-Project-Review-and-Business-Case.html").write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>MPH project review and business case</title><style>'+css+'</style><body>'+"\n".join(parts)+'</body></html>',encoding="utf-8")
(OUT / "MPH-Project-Review-and-Business-Case.md").write_text("\n\n".join(markdown)+"\n",encoding="utf-8")
print(f"Review HTML and Markdown written to {OUT}")
