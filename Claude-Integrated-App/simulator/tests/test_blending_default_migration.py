"""Explicit baseline revision is applied once, not after every page change."""
from copy import deepcopy
from pathlib import Path

from streamlit.testing.v1 import AppTest

from mph_sim import datasets, prewash_blending


def test_hot_session_applies_requested_blending_defaults_once(monkeypatch, tmp_path):
    monkeypatch.setenv("MPH_BLEND_PREFERENCES_PATH", str(tmp_path / "blend.json"))
    app = str(Path(__file__).resolve().parents[1] / "app.py")
    at = AppTest.from_file(app, default_timeout=120).run()
    sc = at.session_state["scenario"]
    sc.config.pop("BLENDING_DEFAULTS_REVISION", None)
    sc.config["CHEVRON_STACKING"].update(enabled_spaces=["HP-07"], global_mode="MATRIX")
    point = sc.config[prewash_blending.CONFIG_KEY]["points"]["BEN-PREWASH"]
    point["enabled_source_spaces"] = []
    point["allowed_treatment_modes"] = ["full"]
    stocks = {s.id: s.mass_t for s in sc.spaces}
    catalogue = deepcopy(sc.config["CHEVRON_STACKING"]["matrix_catalog"])
    at.run()
    assert not at.exception, at.exception
    sc = at.session_state["scenario"]
    assert sc.config["BLENDING_DEFAULTS_REVISION"] == 1
    assert sc.config["CHEVRON_STACKING"]["global_mode"] == "OPTIMIZED"
    assert len(sc.config["CHEVRON_STACKING"]["enabled_spaces"]) == 8
    assert set(sc.config[prewash_blending.CONFIG_KEY]["points"]["BEN-PREWASH"]["enabled_source_spaces"]) == {"BG-RAW-1", "BG-RAW-2"}
    assert {s.id: s.mass_t for s in sc.spaces} == stocks
    assert sc.config["CHEVRON_STACKING"]["matrix_catalog"] == catalogue
    sc.config["CHEVRON_STACKING"]["enabled_spaces"] = ["HP-07"]
    sc.config["CHEVRON_STACKING"]["global_mode"] = "MATRIX"
    at.run()
    assert not at.exception, at.exception
    assert at.session_state["scenario"].config["CHEVRON_STACKING"]["enabled_spaces"] == ["HP-07"]
    assert at.session_state["scenario"].config["CHEVRON_STACKING"]["global_mode"] == "MATRIX"
