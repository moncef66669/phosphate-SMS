# MPH SMS Job-Allocation Project

This workspace contains the engineering references, report sources, generated report deliverables, and the offline planning simulator for the Louta and Benguerir mine stockyards feeding the MPH M16 hub.

## Start here

- Project context and engineering decisions: `CLAUDE-CONTEXT.md`
- Current scope report: `generated_outputs/reports/current/MPH-SMS-Job-Allocation-Scope-Objectives-and-Functionalities.pdf`
- Current formulation report: `generated_outputs/reports/current/MPH-SMS-Job-Allocation-Model-Formulation-and-Optimization.pdf`
- Preserved legacy combined report: `generated_outputs/reports/current/MPH-SMS-Job-Allocation-Project-Report.pdf`
- Editable report content: `report_sources/`
- Engineering source documents: `reference_material/`
- Current simulator application: `Claude-Integrated-App/simulator/`
- Review and business case: `generated_outputs/project_review/MPH-Project-Review-and-Business-Case.pdf`

The 8 September review recovered the application matching report Rev AC from
the Desktop OneDrive backup. The root launcher now runs that restored copy.
The original `simulator/` and its history remain available as an older baseline.
Report Rev AD records the changes. In the app, **Monitor → Business case**
compares independent SMS rules and coordinated planning; it also offers full
project demand, quality, outage and low-stock sensitivities.

## Folder structure

| Folder | Purpose |
|---|---|
| `reference_material/` | Authoritative engineering PDFs and useful searchable extracts. Treat as read-only evidence. |
| `report_sources/` | Editable HTML/Markdown report sources and the two report-build scripts. |
| `generated_outputs/reports/current/` | Current consolidated HTML, Markdown, and PDF deliverables. |
| `generated_outputs/reports/archive_components/` | Superseded standalone component PDFs retained for traceability. |
| `generated_outputs/simulator_visual_qa/` | Browser screenshots used to visually verify the simulator pages. |
| `generated_outputs/external_washing_plant/` | Preserved legacy output from the separate washing-plant project; not MPH/SMS evidence. |
| `Claude-Integrated-App/simulator/` | Current Streamlit app, weekly planner, blending, scenarios, comparisons, tests and recovered history. |
| `simulator/` | Preserved older app and audit history; not the main launcher target. |
| `generated_outputs/project_review/` | Review PDF/HTML/Markdown, immutable experiment inputs/results, source provenance and verification evidence. |

## Run the latest simulator

From VS Code, press **Ctrl+Shift+B**. The default workspace task
`Simulator: Run latest (restart)` stops the previous project instance, starts
the current code, checks that it is healthy, and opens the browser. To stop it,
press **Ctrl+Shift+P**, choose `Tasks: Run Task`, then
`Simulator: Stop`.

For normal use, double-click `RUN-LATEST-SIMULATOR.cmd` at the project root.
It always launches the current absolute `Claude-Integrated-App/simulator/app.py`, replaces only an
older copy started through this launcher, clears disposable Python bytecode
caches, waits for a healthy server, and opens:

`http://localhost:8505/?page=15&scn=s3`

After Claude or Codex changes the code, run the same launcher again. Do not keep
separate simulator windows on ports 8510, 8511, or 8512; those can continue
showing an older imported layout and consume CPU/RAM. Double-click
`STOP-SIMULATOR.cmd` when finished.

PowerShell equivalents:

```powershell
.\Run-Latest-Simulator.ps1 -OpenBrowser
.\Stop-Simulator.ps1
```

Install dependencies once if the launcher reports that Streamlit is missing:

```powershell
python -m pip install -r .\Claude-Integrated-App\simulator\requirements.txt
```

## Rebuild report sources

To reproduce the business studies from the current application directory:

```powershell
Set-Location .\Claude-Integrated-App\simulator
python -X utf8 run_business_case.py --budget 3
python -X utf8 run_business_case.py --full-project --budget 3 --presets BASELINE LOW_LOCAL_STOCK QUALITY_STRESS
```

The controlled experiment currently demonstrates less receiving holding with
equal product service. It does not establish a throughput increase or plant ROI.
See `BUSINESS_CASE_METHOD.md` in that app directory for the information boundaries.

To rebuild the review narrative from saved experiment results, run
`python .\report_sources\project_review\build_review.py` from the project root.

From the project root:

```powershell
python .\report_sources\_merge.py
python .\report_sources\_mergemd.py
```

Both scripts write to `generated_outputs/reports/current/`. The PDF is then printed from `_report.html`; the exact Chrome command is recorded in `CLAUDE-CONTEXT.md`.

For the current two-report publication, including PDF rendering:

```powershell
python .\report_sources\_build_split_reports.py --pdf
```

The formulation report is generated directly from the verified
`Claude-Integrated-App/simulator/model_knowledge/MODEL_FORMULATION.md`.

## Preservation policy

- Do not edit generated report files directly; edit both source mirrors under `report_sources/` and rebuild.
- Do not delete engineering references without explicit approval.
- Python caches, temporary PDF text extracts, and review-page renders are disposable and should not be stored at the project root.
- `simulator/runs.db` is retained audit history, not a cache.
