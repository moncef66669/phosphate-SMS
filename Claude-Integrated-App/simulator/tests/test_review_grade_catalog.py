"""Invalid grade values must never replace a usable persistent catalogue."""
import json

import pytest

from mph_sim.domain import Grade
from mph_sim.grade_catalog import GradeCatalogError, read_grade_catalog, save_grade_catalog


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, "67", True])
def test_invalid_quality_write_retains_existing_catalogue(tmp_path, bad):
    path = tmp_path / "grades.json"
    save_grade_catalog([Grade("A", "A", {"BPL": 67.0})], path)
    original = path.read_bytes()
    with pytest.raises(GradeCatalogError):
        save_grade_catalog([Grade("A", "A", {"BPL": bad})], path)
    assert path.read_bytes() == original
    assert len(list(tmp_path.iterdir())) == 1


def test_invalid_stored_conversion_is_rejected_before_overlay(tmp_path):
    path = tmp_path / "grades.json"
    path.write_text(json.dumps({"version": 1, "grades": [{
        "id": "A", "name": "A", "product_moisture_pct": 100}]}))
    with pytest.raises(GradeCatalogError, match="below 100"):
        read_grade_catalog(path)


def test_valid_catalogue_round_trip_retains_lineage_and_conversion(tmp_path):
    path = tmp_path / "grades.json"
    grades = [Grade("A", "A", {"BPL": 67.0}, source_grade_id="RAW",
                    beneficiation_yield=0.7, raw_moisture_pct=10,
                    product_moisture_pct=12)]
    save_grade_catalog(grades, path)
    assert read_grade_catalog(path) == grades
    assert len(list(tmp_path.iterdir())) == 1
