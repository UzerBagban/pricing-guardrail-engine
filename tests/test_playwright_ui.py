"""Automated Playwright Frontend & UI/UX End-to-End Verification Test."""

import asyncio
import os
import unittest
from playwright.async_api import async_playwright
from fastapi.testclient import TestClient
from src.api.app import app


class TestPlaywrightModernUI(unittest.TestCase):
    def test_playwright_rendered_fastapi_dashboard(self):
        async def _run():
            client = TestClient(app)
            response = client.get("/")
            self.assertEqual(response.status_code, 200)
            html_content = response.text

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(viewport={"width": 1440, "height": 900})
                
                # Load rendered HTML content directly into browser context
                await page.set_content(html_content, wait_until="load")

                # 1. Title verification
                title = await page.title()
                self.assertIn("Pricing Guardrail", title)

                # 2. Verify KPI cards
                events_text = await page.inner_text("#kpi-total-events")
                dollars_text = await page.inner_text("#kpi-dollars-saved")
                margin_text = await page.inner_text("#kpi-avg-margin")

                self.assertNotEqual(events_text, "--")
                self.assertIn("$", dollars_text)
                self.assertIn("%", margin_text)

                # 3. Verify Canvas Charts
                chart_selectors = [
                    "#chart-price-distribution",
                    "#chart-status-donut",
                    "#chart-vrp-trend",
                    "#chart-category-margin",
                ]
                for sel in chart_selectors:
                    canvas = page.locator(sel)
                    self.assertTrue(await canvas.is_visible())

                # 4. Table interaction & Search
                initial_rows = await page.locator("#sku-table-body tr").count()
                self.assertGreater(initial_rows, 0)

                await page.fill("#filter-search", "ELEC")
                await page.wait_for_timeout(200)
                filtered_rows = await page.locator("#sku-table-body tr").count()
                self.assertGreater(filtered_rows, 0)
                self.assertLessEqual(filtered_rows, initial_rows)

                # 5. Unit Economics Modal
                first_btn = page.locator("#sku-table-body tr button").first
                await first_btn.click()
                await page.wait_for_timeout(200)
                modal = page.locator("#modal-backdrop")
                self.assertTrue(await modal.is_visible())
                
                # Close Modal
                await page.click(".modal-close")
                await page.wait_for_timeout(200)

                await browser.close()

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
