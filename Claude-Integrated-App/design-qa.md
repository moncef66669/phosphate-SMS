# Claude stockyard redesign QA

- Reference: `Global Stockyard Layout Redesign/handoff/global-stockyard-layout.html`
- Integrated template: `simulator/ui/flow_svg/claude_stockyard_template.html`
- Runtime renderer: `simulator/ui/flow_svg/redesign_viewer.py`
- Monitor integration: `simulator/ui/process_mimic.py`

## Automated checks

- Python compilation: passed.
- Focused redesign adapter/viewer/integration tests: 22 passed.
- The redesign branch is guarded against invoking the legacy SVG renderer.
- Production JSON is script-safe and the PAP-2 lifecycle control is read-only.
- Temporary foreground startup check: Streamlit health returned `ok` and the
  monitor URL returned HTTP 200 on port 8515.

## Visual comparison

Status: **blocked pending an interactive browser capture**.

The Codex sandbox confirmed that Streamlit and the monitor route start, but it
terminates detached desktop processes when the command exits. Run
`RUN-CLAUDE-INTEGRATED-APP.cmd`, open the monitor flow page, and compare that
screen at the same viewport with the Claude reference before marking visual
fidelity as passed.
