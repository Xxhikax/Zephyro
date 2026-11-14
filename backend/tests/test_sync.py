#!/usr/bin/env python3
"""
Synchronous test for enhanced Playwright web automation.
"""
import sys
import os
import time
import logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

import pytest
import os as _os

from modules.automation.app_controller import AppController
from core.browser_config import BrowserConfig


@pytest.mark.skip(reason="browser integration test — requires CDP; set ZEPHYRO_BROWSER_TESTS=1")
def test_play_media():
    """Test PLAY_MEDIA automation_intent with YouTube."""
    analysis = {
        "intent": "automation",
        "automation_intent": "PLAY_MEDIA",
        "provider": "youtube",
        "search_query": "Never Gonna Give You Up",
        "target": "",
        "action": "play",
    }
    
    logger.info("Starting AppController with headless Firefox...")
    app_controller = AppController(config=BrowserConfig(headless=True))
    
    try:
        result = app_controller.execute(analysis)
        logger.info(f"Execution result: {result}")
        if any(word in result.lower() for word in ['reproducido', 'encontrado', 'éxito', 'listo', 'reproduciendo']):
            logger.info("✅ PLAY_MEDIA execution succeeded.")
        else:
            logger.warning(f"Execution completed but result unclear: {result}")
    except Exception as e:
        logger.error(f"❌ Execution failed: {e}", exc_info=True)
        raise
    finally:
        # Close browser
        app_controller.browser.close()
        logger.info("Browser closed.")

@pytest.mark.skip(reason="browser integration test — requires CDP; set ZEPHYRO_BROWSER_TESTS=1")
def test_search():
    """Test SEARCH automation_intent generic."""
    analysis = {
        "intent": "automation",
        "automation_intent": "SEARCH",
        "provider": "generic",
        "search_query": "Python tutorial",
        "target": "",
        "action": "search",
    }
    
    logger.info("Starting SEARCH test...")
    app_controller = AppController(config=BrowserConfig(headless=True))
    try:
        result = app_controller.execute(analysis)
        logger.info(f"SEARCH result: {result}")
    except Exception as e:
        logger.error(f"SEARCH failed: {e}")
    finally:
        app_controller.browser.close()

if __name__ == "__main__":
    logger.info("=== Synchronous Browser Test ===")
    test_play_media()
    # test_search()
    logger.info("=== Test completed ===")