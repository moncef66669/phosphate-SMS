"""Operator-flow smoke test for the starting inventory snapshot panel."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from pathlib import Path

from streamlit.testing.v1 import AppTest

from mph_sim import datasets
from mph_sim.inventory_snapshot import inventory_snapshot_from_space_values
from mph_sim.rabbitmq_inventory import (
    CONFIG_JSON_ENV,
    CONFIG_PATH_ENV,
    scenario_inventory_fingerprint,
)


APP = str(Path(__file__).resolve().parents[1] / "app.py")


def _full_s3_csv() -> bytes:
    sc = datasets.end_to_end()
    buf = io.StringIO(newline="")
    fields = ["captured_at", "source", "site", "pile_id",
              "record_type", "mass_t", "grade", "quality"]
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    captured = datetime.now(timezone.utc).isoformat()
    for sp in sc.spaces:
        writer.writerow({
            "captured_at": captured,
            "source": "AppTest YardMaster export",
            "site": sp.site,
            "pile_id": sp.id,
            "record_type": "pile",
            "mass_t": sp.mass_t + (1_000 if sp.id == "HP-01" else 0),
            "grade": "",
            "quality": "",
        })
    return buf.getvalue().encode("utf-8-sig")


def _full_s3_live_snapshot(sc, *, include_quality: bool = True):
    captured = datetime.now(timezone.utc)
    rows = [{
        "pile": sp.id,
        "site": sp.site,
        "mass_t": sp.mass_t + (1_000 if sp.id == "HP-01" else 0),
        "grade": sp.grade,
        "quality": (dict(sc.quality_of(sp.id)) if include_quality else {}),
    } for sp in sc.spaces]
    site_times = {site: captured.isoformat()
                  for site in sorted({sp.site for sp in sc.spaces})}
    return inventory_snapshot_from_space_values(
        rows,
        captured_at=captured,
        source="AppTest live read-only SpaceValues",
        acquisition_mode="live-read-only",
        site_captured_at=site_times,
    )


def test_snapshot_preview_then_apply_updates_t0_and_clears_upload():
    from conftest import nav_to

    at = AppTest.from_file(APP, default_timeout=240)
    at.run()
    nav_to(at, "3 · Stockyards & Inventory")
    assert not at.exception
    assert at.file_uploader

    at.file_uploader[0].set_value((
        "s3_inventory.csv", _full_s3_csv(), "text/csv")).run()
    assert not at.exception
    expected_count = len(at.session_state["scenario"].spaces)
    assert any(m.label == "Pile coverage"
               and m.value == f"{expected_count}/{expected_count}"
               for m in at.metric)
    apply_button = next(
        b for b in at.button
        if b.label == "Apply snapshot and clear current plan/simulation")
    assert not apply_button.disabled

    apply_button.click().run()
    assert not at.exception
    sc = at.session_state["scenario"]
    assert sc.space("HP-01").mass_t == 81_000
    assert at.session_state["plan"] is None
    assert sc.config["INVENTORY_SNAPSHOT"]["complete"] is True
    assert sc.config["INVENTORY_SNAPSHOT"]["covered_count"] == expected_count
    assert at.session_state["inventory_snapshot_upload_nonce"] == 1


def test_live_inventory_console_is_safely_disabled_without_configuration(
        monkeypatch):
    """No ICD profile/secret refs means no probe, fetch, or live claim."""
    from conftest import nav_to

    monkeypatch.delenv(CONFIG_JSON_ENV, raising=False)
    monkeypatch.delenv(CONFIG_PATH_ENV, raising=False)
    at = AppTest.from_file(APP, default_timeout=240)
    at.run()
    nav_to(at, "11 · Three-SMS Integration Console")

    assert not at.exception
    probe = next(b for b in at.button if b.label == "Probe live endpoints")
    fetch = next(
        b for b in at.button
        if b.label == "Fetch all loaded SpaceValues (read-only)")
    assert probe.disabled
    assert fetch.disabled
    assert at.session_state["sms_live_health_by_site"] == {}
    assert at.session_state["sms_live_pull_preview"] is None
    visible = " ".join(str(x.value) for group in
                       (at.info, at.warning, at.caption, at.markdown)
                       for x in group).lower()
    assert "live inventory is not configured" in visible
    assert "reachability only after a real probe" in visible


def test_stored_live_preview_uses_canonical_review_and_apply_path(monkeypatch):
    """A fetched preview stays read-only until page 3 explicitly applies it."""
    from conftest import nav_to

    monkeypatch.delenv(CONFIG_JSON_ENV, raising=False)
    monkeypatch.delenv(CONFIG_PATH_ENV, raising=False)
    at = AppTest.from_file(APP, default_timeout=240)
    at.run()
    sc = at.session_state["scenario"]
    snapshot = _full_s3_live_snapshot(sc)
    at.session_state["sms_live_pull_preview"] = {
        "snapshot": snapshot,
        "issues": [],
        "requested_sites": sorted({sp.site for sp in sc.spaces}),
        "scenario_fingerprint": scenario_inventory_fingerprint(sc),
        "config_revision": "",
    }
    at.session_state["sms_live_config_revision"] = ""

    nav_to(at, "3 · Stockyards & Inventory")
    assert not at.exception
    source = next(r for r in at.radio if r.label == "Snapshot source")
    assert source.value == "Fetched live SpaceValues"
    assert not at.file_uploader
    expected_count = len(sc.spaces)
    assert any(m.label == "Pile coverage"
               and m.value == f"{expected_count}/{expected_count}"
               for m in at.metric)
    apply_button = next(
        b for b in at.button
        if b.label == "Apply snapshot and clear current plan/simulation")
    assert not apply_button.disabled

    apply_button.click().run()
    assert not at.exception
    applied = at.session_state["scenario"]
    assert applied.space("HP-01").mass_t == 81_000
    assert applied.config["INVENTORY_SNAPSHOT"]["complete"] is True
    assert (applied.config["INVENTORY_SNAPSHOT"]["acquisition_mode"] ==
            "live-read-only")
    assert at.session_state["plan"] is None
    assert at.session_state["sim"] is None
    assert at.session_state["sms_live_pull_preview"] is None
    assert at.session_state["sms_live_health_by_site"] == {}


def test_live_preview_forces_complete_coverage_after_file_partial_mode(
        monkeypatch):
    """A prior file-import choice must not weaken the live all-site gate."""
    from conftest import nav_to

    monkeypatch.delenv(CONFIG_JSON_ENV, raising=False)
    monkeypatch.delenv(CONFIG_PATH_ENV, raising=False)
    at = AppTest.from_file(APP, default_timeout=240)
    at.run()
    nav_to(at, "3 · Stockyards & Inventory")
    complete = next(
        c for c in at.checkbox if c.label == "Require all loaded piles")
    complete.set_value(False).run()

    sc = at.session_state["scenario"]
    snapshot = _full_s3_live_snapshot(sc)
    at.session_state["sms_live_pull_preview"] = {
        "snapshot": snapshot,
        "issues": [],
        "requested_sites": sorted({sp.site for sp in sc.spaces}),
        "scenario_fingerprint": scenario_inventory_fingerprint(sc),
        "config_revision": "",
    }
    at.session_state["sms_live_config_revision"] = ""
    at.run()
    source = next(r for r in at.radio if r.label == "Snapshot source")
    source.set_value("Fetched live SpaceValues").run()

    assert not at.exception
    complete = next(
        c for c in at.checkbox if c.label == "Require all loaded piles")
    assert complete.disabled
    assert complete.value is True


def test_stale_live_preview_is_discarded_before_review(monkeypatch):
    """A config-revision mismatch must never expose an applyable preview."""
    from conftest import nav_to

    monkeypatch.delenv(CONFIG_JSON_ENV, raising=False)
    monkeypatch.delenv(CONFIG_PATH_ENV, raising=False)
    at = AppTest.from_file(APP, default_timeout=240)
    at.run()
    sc = at.session_state["scenario"]
    at.session_state["sms_live_pull_preview"] = {
        "snapshot": _full_s3_live_snapshot(sc),
        "issues": [],
        "requested_sites": sorted({sp.site for sp in sc.spaces}),
        "scenario_fingerprint": scenario_inventory_fingerprint(sc),
        "config_revision": "obsolete-config-revision",
    }
    at.session_state["sms_live_config_revision"] = "obsolete-config-revision"

    nav_to(at, "3 · Stockyards & Inventory")

    assert not at.exception
    assert at.session_state["sms_live_pull_preview"] is None
    source = next(r for r in at.radio if r.label == "Snapshot source")
    assert source.options == ["Upload file"]
    assert at.file_uploader
    assert any("fetched live preview was discarded" in str(w.value).lower()
               for w in at.warning)


def test_mass_only_live_preview_requires_quality_fallback_acknowledgement(
        monkeypatch):
    """Mass can be commissioned first, but quality fallback is never silent."""
    from conftest import nav_to

    monkeypatch.delenv(CONFIG_JSON_ENV, raising=False)
    monkeypatch.delenv(CONFIG_PATH_ENV, raising=False)
    at = AppTest.from_file(APP, default_timeout=240)
    at.run()
    sc = at.session_state["scenario"]
    at.session_state["sms_live_pull_preview"] = {
        "snapshot": _full_s3_live_snapshot(sc, include_quality=False),
        "issues": [],
        "requested_sites": sorted({sp.site for sp in sc.spaces}),
        "scenario_fingerprint": scenario_inventory_fingerprint(sc),
        "config_revision": "",
    }
    nav_to(at, "3 · Stockyards & Inventory")

    assert not at.exception
    acknowledgement = next(
        c for c in at.checkbox
        if c.label.startswith("I understand that live mass is incomplete"))
    apply_button = next(
        b for b in at.button
        if b.label == "Apply snapshot and clear current plan/simulation")
    assert acknowledgement.value is False
    assert apply_button.disabled

    acknowledgement.set_value(True).run()
    apply_button = next(
        b for b in at.button
        if b.label == "Apply snapshot and clear current plan/simulation")
    assert not apply_button.disabled
