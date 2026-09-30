# Report sources

The current issued content is published as two reports:

- scope, objectives, and functionality from the maintained scope source;
- model formulation and optimization algorithms generated from the verified
  `Claude-Integrated-App/simulator/model_knowledge/MODEL_FORMULATION.md`.

The older combined report remains preserved as a historical deliverable. Its
functional-design formulation must not override the current code-backed model
package.

The HTML files are the styled sources and the Markdown files are synchronized text mirrors. A content change must be applied to both formats.

## Editorial single-source rules

- Business case and outcome KPIs: scope §2.
- Scope/authority boundary: scope §4; interfaces/deployment: scope §6.
- Physical assets, layout and current model coverage: scope §13.
- Entities: functional design §1; equations/constraints: §2.
- Evidence values, status, conflicts and owner/actions: evidence register §3.
- Simulation/live-integration gaps: readiness §4; acceptance measures: §5.

Other sections should cross-reference these canonical homes instead of
restating capacities, corridor values, ASP boundaries or KPI definitions.

## Source pairs

- `_scope.html` and `MPH-OSBL-SMS-Job-Planning-Automation-Scope.md`
- `_fd.html` and `MPH-OSBL-SMS-Job-Allocation-Functional-Design.md`

## Build

Run from any working directory:

```powershell
python <project-root>\report_sources\_merge.py
python <project-root>\report_sources\_mergemd.py
```

The scripts resolve paths from their own location and write `_report.html` and `MPH-SMS-Job-Allocation-Project-Report.md` to `generated_outputs/reports/current/`.

To regenerate both current split reports and their PDFs from canonical sources:

```powershell
python <project-root>\report_sources\_build_split_reports.py --pdf
```

This writes the two Markdown/HTML/PDF report sets under
`generated_outputs/reports/current/`. Update the model formulation in
`model_knowledge/`; do not hand-edit the generated formulation report.
