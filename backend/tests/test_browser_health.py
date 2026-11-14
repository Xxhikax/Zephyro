#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')

import logging
logging.basicConfig(level=logging.INFO)
from modules.automation.browser_controller import BrowserController
from core.browser_config import BrowserConfig

async def test_browser():
    print("Testing BrowserController health...")
    bc = BrowserController(config=BrowserConfig(headless=True))
    
    print("1. Checking initial health...")
    healthy = bc.is_healthy()
    print(f"   Initial health: {healthy}")
    
    print("2. Trying to ensure browser...")
    try:
        await bc._ensure_browser()
        print("   _ensure_browser succeeded")
    except Exception as e:
        print(f"   _ensure_browser failed: {e}")
        return False
    
    print("3. Checking health after ensure...")
    healthy = bc.is_healthy()
    print(f"   Health after ensure: {healthy}")
    
    print("4. Trying to navigate...")
    try:
        result = await bc.navigate_to("duckduckgo.com")
        print(f"   Navigate result: {result}")
    except Exception as e:
        print(f"   Navigate failed: {e}")
        return False
    
    print("5. Closing browser...")
    try:
        result = await bc.close()
        print(f"   Close result: {result}")
    except Exception as e:
        print(f"   Close failed: {e}")
    
    return True

if __name__ == "__main__":
    import asyncio
    success = asyncio.run(test_browser())
    sys.exit(0 if success else 1)