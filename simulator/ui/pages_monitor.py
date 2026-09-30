"""Two-page operator Monitor workspace."""
from __future__ import annotations

import streamlit as st

from . import common, pages_overview, pages_planning, process_mimic


def page_kpi_flow_schedule() -> None:
    """Render the KPI overview, global flow and full job schedule in order."""
    pages_overview.page_overview(monitor_compact=True)
    st.divider()
    process_mimic.render_global_flow(
        common.sc(), common.plan(), common.sim())
    st.divider()
    pages_planning.page_schedule()


def page_machine_coverage() -> None:
    """Render the dedicated machine coverage and usage workspace."""
    pages_planning.page_machine_coverage()
