"""Rendered local smoke check and evidence for the comparison workflow."""
from pathlib import Path
import json
import math
import zipfile
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "generated_outputs/project_review/visual_qa"
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe", headless=True)
    page = browser.new_page(viewport={"width": 1600, "height": 1500}, device_scale_factor=1)
    page.goto("http://localhost:8505/?page=21&scn=s3", wait_until="networkidle")
    page.get_by_role("button", name="Run paired comparison", exact=True).wait_for(timeout=60000)
    page.get_by_role("button", name="Run paired comparison", exact=True).click()
    page.get_by_text("Paired results", exact=True).wait_for(timeout=60000)
    page.get_by_text("Chart measure", exact=True).wait_for()
    assert page.locator('[data-testid="stException"]').count() == 0
    page.screenshot(path=str(OUT / "business-final.png"), full_page=True)
    page.get_by_text("Chart measure", exact=True).scroll_into_view_if_needed()
    page.screenshot(path=str(OUT / "business-chart-final.png"), full_page=True)
    with page.expect_download() as pending:
        page.get_by_role("button", name="Download comparison and reproducible inputs").click()
    exported = OUT / "business-ui-export.zip"
    pending.value.save_as(str(exported))
    with zipfile.ZipFile(exported) as z:
        assert {"comparison.json", "scenario.json", "scorecard.csv", "economic_sensitivity.json"} <= set(z.namelist())
        result = json.loads(z.read("comparison.json"))
        assert result["deltas"]["final_product_on_time_t"] == 0
        assert math.isclose(result["deltas"]["receiving_queue_t_h"], -750, abs_tol=1e-6)
    page.get_by_text("M16 receiving interruption", exact=True).click()
    page.get_by_text("Inputs or study changed. Run the comparison again to update the results.", exact=True).wait_for()
    page.get_by_role("button", name="Run paired comparison", exact=True).click()
    page.get_by_text("Paired results", exact=True).wait_for(timeout=60000)
    with page.expect_download() as pending:
        page.get_by_role("button", name="Download comparison and reproducible inputs").click()
    control = OUT / "business-ui-control.zip"
    pending.value.save_as(str(control))
    with zipfile.ZipFile(control) as z:
        result = json.loads(z.read("comparison.json"))
        assert math.isclose(result["deltas"]["receiving_queue_t_h"], 0, abs_tol=1e-6)
        assert result["deltas"]["final_product_on_time_t"] == 0
    page.get_by_text("Current project scenario", exact=True).click()
    page.get_by_text("Scenario", exact=True).wait_for()
    assert page.locator('[data-testid="stException"]').count() == 0
    page.screenshot(path=str(OUT / "business-project-final.png"), full_page=True)
    page.goto("http://localhost:8505/?page=2&scn=s3", wait_until="networkidle")
    page.get_by_text("Starting inventory snapshot", exact=True).wait_for(timeout=60000)
    page.get_by_text("Starting inventory snapshot", exact=True).click()
    assert page.locator('[data-testid="stException"]').count() == 0
    page.get_by_text("Starting inventory snapshot", exact=True).scroll_into_view_if_needed()
    page.screenshot(path=str(OUT / "inventory-input-final.png"), full_page=True)
    browser.close()
print("PASS: rendered comparison, chart, reproducible download, stale-input guard, no-outage control, current-project controls and inventory snapshot panel")
