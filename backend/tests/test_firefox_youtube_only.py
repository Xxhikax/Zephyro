#!/usr/bin/env python3
"""
Quick test to verify Firefox can open YouTube.
"""
import asyncio
import os
import sys
import time
from pathlib import Path

os.environ["ZEPHYRO_DEBUG"] = "0"  # Less verbose

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.automation.browser_controller import BrowserController

async def test():
    print("🔥 Testing Firefox with YouTube...")
    print(f"   Profile: ~/.cache/zephyro/firefox_profile_simple")
    print(f"   Headless: True (no visible window)")
    
    profile_dir = Path.home() / ".cache" / "zephyro" / "firefox_profile_simple"
    profile_dir.mkdir(parents=True, exist_ok=True)
    
    controller = BrowserController(
        headless=True,
        persistent_user_data_dir=str(profile_dir),
        firefox_profile=None,
        firefox_executable=None
    )
    
    try:
        start = time.time()
        
        print("1. Opening YouTube...")
        result = await controller.navigate_to("youtube.com")
        print(f"   ✓ {result}")
        
        print("2. Getting page title...")
        # The navigate_to already returns title, but we can verify page is working
        # by checking if controller.page is alive
        if controller.page and not controller.page.is_closed():
            title = await controller.page.title()
            print(f"   ✓ Title: {title}")
        
        elapsed = time.time() - start
        print(f"\n✅ SUCCESS: Firefox opened YouTube in {elapsed:.1f}s")
        print("   The simplified architecture (Option 3) is working!")
        
        return True
    except Exception as e:
        print(f"\n❌ FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if controller.browser:
            await controller.browser.close()
        if controller.playwright:
            await controller.playwright.stop()

if __name__ == "__main__":
    success = asyncio.run(test())
    sys.exit(0 if success else 1)