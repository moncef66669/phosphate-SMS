from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_URL = "http://127.0.0.1:8519/?page=15&scn=s3"
OUT = Path(__file__).resolve().parent.parent / "generated_outputs" / "simulator_visual_qa"
OUT.mkdir(parents=True, exist_ok=True)


def select_view(page, label: str) -> None:
    target = page.get_by_text(label, exact=True)
    target.last.click()
    page.wait_for_timeout(1800)


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1920, "height": 1200})
    console_errors: list[str] = []
    page_errors: list[str] = []
    page.on(
        "console",
        lambda message: console_errors.append(message.text)
        if message.type == "error"
        else None,
    )
    page.on("pageerror", lambda error: page_errors.append(str(error)))

    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=120_000)
    page.wait_for_selector(
        '[data-testid="stAppViewContainer"]', timeout=120_000
    )
    page.wait_for_timeout(8_000)

    body = page.locator("body").inner_text()
    domain_labels = [
        label for label in ("BG + LO + MPH", "MS") if label in body
    ]
    page.screenshot(
        path=str(OUT / "operator-cleanup-global-1920x1200.png"),
        full_page=False,
    )

    view_outputs = {}
    for label, filename in (
        ("Benguerir", "operator-cleanup-benguerir-1920x1200.png"),
        ("Louta", "operator-cleanup-louta-1920x1200.png"),
    ):
        select_view(page, label)
        current_body = page.locator("body").inner_text()
        plots = page.locator(".js-plotly-plot")
        plot_count = plots.count()
        page.screenshot(path=str(OUT / filename), full_page=False)
        if plot_count:
            plots.last.screenshot(
                path=str(OUT / filename.replace(".png", "-mimic.png"))
            )
        view_outputs[label] = {
            "plot_count": plot_count,
            "has_residence_time": "Residence time: 30 min" in current_body,
            "has_tbc": "TBC" in current_body.upper(),
        }

    result = {
        "url": page.url,
        "domain_labels": domain_labels,
        "global_has_tbc": "TBC" in body.upper(),
        "views": view_outputs,
        "console_errors": console_errors,
        "page_errors": page_errors,
    }
    print(json.dumps(result, indent=2))
    browser.close()
