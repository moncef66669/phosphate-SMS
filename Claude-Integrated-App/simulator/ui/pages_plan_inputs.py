"""Focused operator inputs for the Plan workspace.

Scenario selection is owned by :mod:`app` and intentionally does not appear
inside these page renderers.  Keeping that shared control outside the page
functions makes switching between demand, routing and contingencies feel like
one planning workspace rather than three separate scenario forms.
"""
from __future__ import annotations

import streamlit as st

from . import common, pages_planning
from .flow_configuration import render_global_flow_resource_configuration


def page_routing_availability() -> None:
    """Edit state and rate through the shared global flow drawing."""
    from .pages_data import _render_louta_s0_policy

    st.subheader("Flow resources & routing")
    st.caption(
        "Use the same global drawing as Monitor. Select All, Louta, "
        "Benguerir or MPH inside the drawing, then click a machine, conveyor, "
        "washing line or receiving plant. Every selectable resource supports "
        "an entire-run availability/rate setting or an exact timed outage or "
        "derate. Demand is retained; an unreachable requirement is reported "
        "as unmet."
    )
    scn = common.sc()
    render_global_flow_resource_configuration(scn)
    with st.expander("Louta stocked-route policy", expanded=False):
        _render_louta_s0_policy(common.sc())
    st.divider()
    pages_planning.render_plan_run_panel()


def page_planned_contingencies() -> None:
    """Backward-compatible alias for the consolidated routing workspace."""
    page_routing_availability()
