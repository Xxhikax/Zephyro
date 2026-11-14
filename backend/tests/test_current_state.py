#!/usr/bin/env python3
"""
Test current browser state with persistent profile disabled.
"""
import asyncio
import os
import sys
import logging

# Enable debug logging
os.environ["ZEPHYRO_DEBUG"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.automation.browser_controller import BrowserController
from core.browser_config import BrowserConfig

async def test_without_profile():
    print("=== TEST WITHOUT PERSISTENT PROFILE ===")
    print("Creating BrowserController(headless=False)")
    bc = BrowserController(config=BrowserConfig(headless=False))
    
    print("\n1. Calling _ensure_browser...")
    try:
        await bc._ensure_browser()
        print("   SUCCESS: _ensure_browser completed")
        print(f"   Browser connected: {bc.browser.is_connected() if bc.browser else False}")
        print(f"   Page closed: {bc.page.is_closed() if bc.page else True}")
    except Exception as e:
        print(f"   FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    print("\n2. Navigating to youtube.com...")
    try:
        result = await bc.navigate_to("youtube.com")
        print(f"   Result: {result}")
        print(f"   Page closed after nav: {bc.page.is_closed() if bc.page else True}")
    except Exception as e:
        print(f"   FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    print("\n3. Waiting 5 seconds...")
    await asyncio.sleep(5)
    
    print("\n4. Closing browser...")
    try:
        await bc.close()
        print("   SUCCESS: Browser closed")
    except Exception as e:
        print(f"   FAILED: {e}")
    
    return True

async def test_with_profile():
    print("\n=== TEST WITH PERSISTENT PROFILE ===")
    profile_dir = os.path.expanduser("~/.cache/zephyro/firefox_profile_test")
    print(f"Profile dir: {profile_dir}")
    bc = BrowserController(config=BrowserConfig(headless=False, user_profile_path=profile_dir))
    
    print("\n1. Calling _ensure_browser...")
    try:
        await bc._ensure_browser()
        print("   SUCCESS: _ensure_browser completed")
        print(f"   Browser connected: {bc.browser.is_connected() if bc.browser else False}")
        print(f"   Page closed: {bc.page.is_closed() if bc.page else True}")
    except Exception as e:
        print(f"   FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    print("\n2. Navigating to duckduckgo.com...")
    try:
        result = await bc.navigate_to("duckduckgo.com")
        print(f"   Result: {result}")
    except Exception as e:
        print(f"   FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    print("\n3. Closing browser...")
    try:
        await bc.close()
        print("   SUCCESS: Browser closed")
    except Exception as e:
        print(f"   FAILED: {e}")
    
    return True

async def main():
    print("Testing current browser state...")
    
    success1 = await test_without_profile()
    if success1:
        success2 = await test_with_profile()
    else:
        success2 = False
    
    print(f"\n=== SUMMARY ===")
    print(f"Without profile: {'PASS' if success1 else 'FAIL'}")
    print(f"With profile: {'PASS' if success2 else 'FAIL'}")
    
    return success1 and success2

if __name__ == "__main__":
    try:
        success = asyncio.run(main())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"Unhandled error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)