"""Operator-facing continuity-policy editor contracts."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from conftest import nav_to
from mph_sim import datasets
from ui import common
from ui.pages_planning import (
    _commit_continuity_policy_values,
    continuity_policy_values,
    validate_continuity_policy_values,
)


APP = str(Path(__file__).resolve().parents[1] / "app.py")


def test_policy_values_expose_fixed_destination_rows_without_mutation():
    scenario = datasets.end_to_end()
    before = deepcopy(scenario.config)

    values = continuity_policy_values(scenario)

    assert list(values) == [destination.id for destination in scenario.destinations]
    assert values["PAP-1"]["mode"] == "continuous_feed"
    assert values["PAP-1"]["minimum_tph"] == 0.0
    assert values["PAP-1"]["max_gap_min"] == 0
    assert scenario.config == before


def test_policy_validation_uses_domain_rules_and_requires_complete_register():
    scenario = datasets.end_to_end()
    values = continuity_policy_values(scenario)
    missing_id = next(iter(values))
    del values[missing_id]
    values["NOT-A-DESTINATION"] = {
        "mode": "always_on",
        "minimum_tph": -1,
        "max_gap_min": 2.5,
    }

    errors = validate_continuity_policy_values(scenario, values)

    assert any("does not match a configured destination" in error
               for error in errors)
    assert any("mode must be one of" in error for error in errors)
    assert any("minimum_tph must be a finite nonnegative number" in error
               for error in errors)
    assert any("max_gap_min must be a nonnegative integer" in error
               for error in errors)
    assert any(f"missing configured destinations: {missing_id}" in error
               for error in errors)


def test_invalid_policy_commit_is_atomic(monkeypatch):
    scenario = datasets.end_to_end()
    before = deepcopy(scenario.config)
    values = continuity_policy_values(scenario)
    values["PAP-1"]["maximum_gap_min"] = 30
    values["PAP-1"]["max_gap_min"] = 1.5
    calls: list[str] = []
    monkeypatch.setattr(
        common, "invalidate_outputs",
        lambda: calls.append("invalidate") or True)
    monkeypatch.setattr(
        common, "revalidate", lambda: calls.append("revalidate"))

    with pytest.raises(ValueError, match="nonnegative integer"):
        _commit_continuity_policy_values(scenario, values, basis="test")

    assert scenario.config == before
    assert calls == []


def test_policy_commit_records_basis_and_invalidates_once(monkeypatch):
    scenario = datasets.end_to_end()
    values = continuity_policy_values(scenario)
    values["PAP-1"] = {
        "mode": "continuous_feed",
        "minimum_tph": 125.5,
        # Share of this bucket's own request that must arrive before it counts
        # as served; it is stored alongside the approved rate, not instead of
        # it, so an editor round-trip cannot silently drop either field.
        "minimum_service_fraction": 0.5,
        "max_gap_min": 45,
    }
    calls: list[str] = []
    monkeypatch.setattr(
        common, "invalidate_outputs",
        lambda: calls.append("invalidate") or True)
    monkeypatch.setattr(
        common, "revalidate", lambda: calls.append("revalidate"))

    changed, stale = _commit_continuity_policy_values(
        scenario, values, basis="OPS-STD-42 approved 2026-09-10")

    assert (changed, stale) == (True, True)
    assert calls == ["invalidate", "revalidate"]
    assert scenario.config["CONTINUITY_POLICIES"]["PAP-1"] == {
        "mode": "continuous_feed",
        "minimum_tph": 125.5,
        "minimum_service_fraction": 0.5,
        "max_gap_min": 45,
    }
    assert scenario.config["CONTINUITY_POLICY_BASIS"] == \
        "OPS-STD-42 approved 2026-09-10"
    audit = scenario.config["CONTINUITY_POLICY_OVERRIDES"][-1]
    assert audit["basis"] == "OPS-STD-42 approved 2026-09-10"
    assert audit["after"] == scenario.config["CONTINUITY_POLICIES"]


def test_unchanged_policy_is_a_noop(monkeypatch):
    scenario = datasets.end_to_end()
    before = deepcopy(scenario.config)
    calls: list[str] = []
    monkeypatch.setattr(
        common, "invalidate_outputs",
        lambda: calls.append("invalidate") or True)
    monkeypatch.setattr(
        common, "revalidate", lambda: calls.append("revalidate"))

    changed, stale = _commit_continuity_policy_values(
        scenario, continuity_policy_values(scenario),
        basis="A different note must not turn a no-op into a policy change")

    assert (changed, stale) == (False, False)
    assert calls == []
    assert scenario.config == before


def test_plan_page_exposes_policy_rows_and_hard_limit_warning():
    at = AppTest.from_file(APP, default_timeout=300).run()
    nav_to(at, common.PLAN_LANDING_PAGE)
    assert not at.exception, at.exception

    headings = "\n".join(str(item.value) for item in at.main.markdown)
    warnings = "\n".join(str(item.value) for item in at.main.warning)
    button_labels = {button.label for button in at.main.button}
    destinations = at.session_state["scenario"].destinations
    mode_labels = {
        selectbox.label for selectbox in at.main.selectbox
        if selectbox.label.startswith("Mode ")
    }

    assert "Continuous-feed policy" in headings
    assert "positive maximum gap is a hard planning constraint" in warnings
    assert "Set maximum gap to 0" in warnings
    assert "Apply continuous-feed policies" in button_labels
    assert mode_labels == {
        f"Mode — {destination.id}" for destination in destinations
    }
