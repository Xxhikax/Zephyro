#!/usr/bin/env python3
"""
Test type fixes and simplified browser controller.
"""
import asyncio
import os
import sys
import logging
from pathlib import Path

# Enable debug logging
os.environ["ZEPHYRO_DEBUG"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.automation.browser_controller import BrowserController

import pytest
import os as _os

@pytest.mark.skip(reason="browser integration test — requires CDP; set ZEPHYRO_BROWSER_TESTS=1")
async def test_browser_controller():
    if not _os.getenv("ZEPHYRO_BROWSER_TESTS"):
        pytest.skip("browser integration test — requires CDP; set ZEPHYRO_BROWSER_TESTS=1")
    print("Testing BrowserController with simplified profile...")
    
    # Use headless=True to avoid opening visible browser during test
    # Use simple profile directory
    profile_dir = Path.home() / ".cache" / "zephyro" / "firefox_profile_simple"
    profile_dir.mkdir(parents=True, exist_ok=True)
    
    controller = BrowserController(
        headless=True,
        persistent_user_data_dir=str(profile_dir),
        firefox_profile=None,
        firefox_executable=None
    )
    
    try:
        print("Navigating to example.com...")
        result = await controller.navigate_to("example.com")
        print(f"Result: {result}")
        
        # Take a screenshot
        result = await controller.screenshot()
        print(f"Screenshot: {result}")
        
        # Close browser
        result = await controller.close()
        print(f"Close: {result}")
        
        print("Test passed!")
    except Exception as e:
        print(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Ensure browser is closed
        if controller.browser:
            await controller.browser.close()
        if controller.playwright:
            await controller.playwright.stop()
    
    return True

if __name__ == "__main__":
    success = asyncio.run(test_browser_controller())
    sys.exit(0 if success else 1)