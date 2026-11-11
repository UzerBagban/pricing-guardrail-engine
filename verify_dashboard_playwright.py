"""Playwright automated verification script for Executive Dashboard."""

import asyncio
import os
import sys
from playwright.async_api import async_playwright


async def run_verification():
    html_path = os.path.abspath("dashboard.html")
    file_url = f"file:///{html_path.replace(os.sep, '/')}"
    print(f"Loading Executive Dashboard at: {file_url}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1440, "height": 1000})
        page = await context.new_page()

        # Capture console messages
        page.on("console", lambda msg: print(f"[Browser Console] {msg.type}: {msg.text}"))

        await page.goto(file_url, wait_until="networkidle")

        # 1. Title verification
        title = await page.title()
        print(f"Page Title: {title}")
        assert "Pricing Guardrail" in title, f"Unexpected title: {title}"

        # 2. Verify KPI values are populated (not '--')
        kpi_events = await page.inner_text("#kpi-total-events")
        kpi_dollars = await page.inner_text("#kpi-dollars-saved")
        kpi_suppressions = await page.inner_text("#kpi-suppressions")
        kpi_margin = await page.inner_text("#kpi-avg-margin")

        print(f"KPI Total Events: {kpi_events}")
        print(f"KPI Margin Protected: {kpi_dollars}")
        print(f"KPI Suppressions: {kpi_suppressions}")
        print(f"KPI Avg Margin: {kpi_margin}")

        assert kpi_events != "--" and int(kpi_events.replace(",", "")) > 0, "KPI events not loaded"
        assert "$" in kpi_dollars, "KPI dollars not formatted properly"
        assert "%" in kpi_margin, "KPI margin not formatted properly"

        # 3. Verify Chart canvas elements are present and sized
        charts = [
            "#chart-price-distribution",
            "#chart-status-donut",
            "#chart-vrp-trend",
            "#chart-category-margin",
        ]
        for selector in charts:
            canvas = page.locator(selector)
            assert await canvas.is_visible(), f"Canvas {selector} is not visible"
            box = await canvas.bounding_box()
            assert box and box["width"] > 100 and box["height"] > 100, f"Canvas {selector} has invalid size: {box}"
            print(f"Verified Chart: {selector} (width: {box['width']}, height: {box['height']})")

        # 4. Verify AI Executive Memos
        memos = page.locator(".memo-card")
        memo_count = await memos.count()
        print(f"Diagnostic Memos Count: {memo_count}")
        assert memo_count >= 3, f"Expected at least 3 memos, found {memo_count}"

        # 5. Verify Table Rows
        rows = page.locator("#sku-table-body tr")
        row_count = await rows.count()
        print(f"Initial Table Rows: {row_count}")
        assert row_count > 0, "No table rows rendered"

        # 6. Test Filter: Search for ELEC
        await page.fill("#filter-search", "ELEC")
        await page.wait_for_timeout(300)
        filtered_rows = page.locator("#sku-table-body tr")
        filtered_count = await filtered_rows.count()
        print(f"Rows after searching 'ELEC': {filtered_count}")
        assert 0 < filtered_count <= row_count, "Search filter failed"

        # Clear search
        await page.fill("#filter-search", "")
        await page.wait_for_timeout(300)

        # 7. Test Cost Modal Interaction
        first_btn = page.locator("#sku-table-body tr button").first
        await first_btn.click()
        await page.wait_for_timeout(300)

        modal = page.locator("#modal-backdrop")
        assert await modal.is_visible(), "Unit economics modal did not open"
        modal_title = await page.inner_text("#modal-sku-title")
        print(f"Opened Modal: {modal_title}")

        # Capture Screenshot with Modal open
        os.makedirs("docs/screenshots", exist_ok=True)
        modal_screenshot = "docs/screenshots/dashboard_modal.png"
        await page.screenshot(path=modal_screenshot)
        print(f"Saved modal screenshot to {modal_screenshot}")

        # Close Modal
        await page.click(".modal-close")
        await page.wait_for_timeout(300)
        assert not await modal.is_visible(), "Modal did not close"

        # Capture Full Dashboard Screenshot
        full_screenshot = "docs/screenshots/dashboard_full.png"
        await page.screenshot(path=full_screenshot, full_page=True)
        print(f"Saved full dashboard screenshot to {full_screenshot}")

        await browser.close()
        print("\nALL PLAYWRIGHT FRONTEND ASSERTIONS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    asyncio.run(run_verification())
