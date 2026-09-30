"""Read-only bridge onto the production simulator package.

This preview app is a SEPARATE Streamlit entry point. It must never modify
anything under ``simulator/``:

  * ``sys.dont_write_bytecode`` is set BEFORE the first import so importing
    ``ui`` / ``mph_sim`` cannot drop ``__pycache__`` into the user's tree
    (the same warm-import hazard recorded in ``simulator/design-qa.md``);
  * only the STATE layer is reused — ``build_process_mimic_state``,
    ``geometry_for``, the view-models and the solver wrappers. The whole
    presentation layer (theme, CSS, page shell, panels) is new and lives here;
  * ``ui.theme.inject_css`` is deliberately NOT called: its selectors target
    Streamlit internals globally and would fight the console styling.

Because the process graphic itself is produced by the production
``process_mimic_figure``, a future geometry correction in ``simulator/``
flows into this preview automatically — there is no forked drawing code.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True          # must precede the engine imports

PREVIEW_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PREVIEW_ROOT.parent
SIMULATOR_ROOT = PROJECT_ROOT / "simulator"

if not (SIMULATOR_ROOT / "app.py").is_file():        # pragma: no cover
    raise RuntimeError(
        f"production simulator not found next to the preview: {SIMULATOR_ROOT}")

if str(SIMULATOR_ROOT) not in sys.path:
    sys.path.insert(0, str(SIMULATOR_ROOT))

# --- engine ------------------------------------------------------------------
from mph_sim import datasets                                   # noqa: E402
from mph_sim.coordinates import (format_repere,                # noqa: E402,F401
                                 format_repere_range)
from mph_sim.domain import (CalendarEntry, PlanResult,        # noqa: E402,F401
                            Scenario, SimEvent)
from mph_sim.kpis import compute_kpis                          # noqa: E402,F401
from mph_sim.optimizer import SolveOptions, solve_plan         # noqa: E402,F401
from mph_sim.simulator import Simulator                        # noqa: E402,F401
from mph_sim.validation import validate_scenario               # noqa: E402,F401

# --- state layer of the production UI (view-models only) ---------------------
from ui import common                                          # noqa: E402
from ui import hmi_symbols as hs                               # noqa: E402,F401
from ui.labels import site_tag                                 # noqa: E402,F401
from ui.layout_geometry import (GEOMETRY_REVISION,             # noqa: E402,F401
                                SiteGeometry,
                                geometry_for as _geometry_for_prod)
from ui.process_mimic import (ACS_NOTE,                        # noqa: E402,F401
                              DISPLAY_BADGE, POSITION_NOTE,
                              PROCESS_MIMIC_BASIS_KEY,
                              PROCESS_MIMIC_HOUR_KEY,
                              MimicOpts, MimicState,
                              asset_status_legend_html,
                              build_process_mimic_state,
                              geometry_model_mismatch,
                              grade_profile_distribution_figure,
                              grade_profile_distribution_rows,
                              process_mimic_figure)
from ui.report_ref import REV, REV_DATE, report_ref            # noqa: E402,F401
from ui.stockyard_monitor import grade_color                    # noqa: E402,F401


def geometry_for(sc, view: str, show_future: bool = False) -> SiteGeometry:
    """Preview override: the BENGUERIR single-site view is redrawn as a replica
    of the Louta block (design-review proposal — see ``benguerir_replica``).
    Every other view delegates unchanged to the production builder."""
    if view == "BENGUERIR":
        from .benguerir_replica import benguerir_replica_geometry
        return benguerir_replica_geometry(sc, show_future=show_future)
    return _geometry_for_prod(sc, view, show_future=show_future)


VIEW_LABELS = {
    "CHAIN": "Value chain",
    "LOUTA": "Louta",
    "BENGUERIR": "Benguerir",
    "MPH": "MPH M16",
    "GLOBAL": "Global",
}

#: State-view options, in operator order.
STATE_INITIAL = "Initial state"
STATE_FINAL = "Final plan"
STATE_SIM = "Current simulation"
STATE_BUCKET = "Inspect plan bucket"
STATE_TIMELINE = "Plan timeline"

BASIS_OF_STATE = {
    STATE_INITIAL: "initial",
    STATE_FINAL: "plan",
    STATE_BUCKET: "plan",
    STATE_TIMELINE: "plan",
    STATE_SIM: "sim",
}


def engine_versions() -> dict:
    """Provenance shown in the console footer."""
    import plotly
    import streamlit

    return {
        "geometry_revision": GEOMETRY_REVISION,
        "baseline_revision": datasets.BUILTIN_BASELINE_REVISION,
        "report_revision": REV,
        "report_date": REV_DATE,
        "streamlit": streamlit.__version__,
        "plotly": plotly.__version__,
        "simulator_root": str(SIMULATOR_ROOT),
    }


def views_for(sc: Scenario) -> list:
    """Same rule as the production page: an M16-only scenario has one view."""
    if not any(l.id == "C1" for l in sc.links):
        return ["MPH"]
    return ["CHAIN", "LOUTA", "BENGUERIR", "MPH"]


def evidence_counts(sc: Scenario) -> dict:
    ev = {"CONFIRMED": 0, "DERIVED": 0, "INFERRED": 0, "ASSUMED": 0, "TBC": 0}
    for items in (sc.spaces, sc.machines, sc.links, sc.destinations,
                  sc.grades, sc.sites):
        for it in items:
            ev[it.evidence.status] = ev.get(it.evidence.status, 0) + 1
    return {k: v for k, v in ev.items() if v}


__all__ = [n for n in dir() if not n.startswith("_")]
