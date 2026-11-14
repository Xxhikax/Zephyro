#!/usr/bin/env python3
"""
Test close tab command.

Unit test: verifies router classifies "cierra la pestaña" correctly (always runs).
Integration test: executes against real Edge via CDP (requires ZEPHYRO_BROWSER_TESTS=1).

WARNING: The integration part uses CDP attach to the user's real Edge profile.
BrowserConfig.headless=True does NOT launch a separate headless browser — it kills
the user's Edge and relaunches it with --headless=new + --remote-debugging-port,
locking the profile. Never run without ZEPHYRO_BROWSER_TESTS guard.
"""
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

import asyncio
import pytest
from unittest.mock import MagicMock
from core.router import IntentRouter

_BROWSER_TESTS = os.environ.get("ZEPHYRO_BROWSER_TESTS", "").strip() == "1"


class MockContextManager:
    def __init__(self):
        self.last_automation = None

    def get_last_automation(self):
        return self.last_automation

    def set_last_automation(self, analysis):
        self.last_automation = analysis

    def add(self, *args, **kwargs):
        pass


def test_close_tab_routing():
    """Router classifies 'cierra la pestaña' as automation close_tab."""
    mock_llm = MagicMock()
    mock_llm.generate = MagicMock(return_value='{"intent": "automation", "action": "open", "target": "firefox"}')
    mock_ctx = MockContextManager()
    mock_ctx.set_last_automation({"analysis": {"intent": "automation", "action": "open", "target": "firefox"}})

    router = IntentRouter(mock_llm, context_manager=mock_ctx)
    parsed = asyncio.run(router.classify_and_parse("cierra la pestaña"))
    logger.info(f"Parsed: {parsed}")
    assert parsed.get("intent") == "automation"
    assert parsed.get("automation_intent") == "INTERACT"
    assert parsed.get("action") == "close_tab"


@pytest.mark.skipif(not _BROWSER_TESTS, reason="real Edge CDP integration — set ZEPHYRO_BROWSER_TESTS=1 to run")
def test_close_tab_real_browser():
    """Executes close_tab against real Edge via CDP. Requires Edge running."""
    from modules.automation.app_controller import AppController
    from core.browser_config import BrowserConfig

    app_controller = AppController(config=BrowserConfig(headless=True))
    asyncio.run(app_controller.browser.navigate_to("https://example.com"))

    mock_llm = MagicMock()
    mock_ctx = MockContextManager()
    mock_ctx.set_last_automation({"analysis": {"intent": "automation", "action": "open", "target": "firefox"}})
    router = IntentRouter(mock_llm, context_manager=mock_ctx)
    parsed = asyncio.run(router.classify_and_parse("cierra la pestaña"))

    result = asyncio.run(app_controller.execute(parsed))
    logger.info(f"Close tab result: {result}")
    assert "cerrada" in result.lower() or "✓" in result

    asyncio.run(app_controller.browser.close())
    logger.info("Test passed.")


if __name__ == "__main__":
    try:
        test_close_tab_routing()
        if _BROWSER_TESTS:
            test_close_tab_real_browser()
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        raise
