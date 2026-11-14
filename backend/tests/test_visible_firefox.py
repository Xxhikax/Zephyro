#!/usr/bin/env python3
"""
Test Firefox with visible window (headless=False).
User should see a Firefox window open.
"""
import asyncio
import os
import sys
import time
import subprocess
from pathlib import Path

os.environ["ZEPHYRO_DEBUG"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.automation.browser_controller import BrowserController

async def test_visible():
    print("🔥 Testing Firefox with VISIBLE window (headless=False)")
    print("   You should see a Firefox window open in a few seconds...")
    
    # Check Firefox status
    result = subprocess.run(["pgrep", "-x", "firefox"], capture_output=True, text=True)
    if result.returncode == 0:
        print(f"   ⚠️  Firefox is already running (PID: {result.stdout.strip()})")
        print("   This may cause conflicts. Consider closing Firefox first.")
    
    # Use a fresh profile to avoid issues
    profile_dir = Path.home() / ".cache" / "zephyro" / "firefox_visible_test"
    if profile_dir.exists():
        import shutil
        shutil.rmtree(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"   📁 Using fresh profile: {profile_dir}")
    
    controller = BrowserController(
        headless=False,  # IMPORTANT: Visible window!
        persistent_user_data_dir=str(profile_dir),
        firefox_profile=None,
        firefox_executable=None
    )
    
    try:
        start = time.time()
        
        print("\n1. Opening YouTube (visible window should appear)...")
        result = await controller.navigate_to("youtube.com")
        print(f"   Result: {result}")
        
        # Wait longer for user to see the window
        print("\n2. Waiting 10 seconds for you to see the Firefox window...")
        print("   Look for a Firefox window with YouTube opened.")
        for i in range(10, 0, -1):
            print(f"   {i}...")
            await asyncio.sleep(1)
        
        # Take screenshot to verify page loaded
        print("\n3. Taking screenshot to verify page loaded...")
        result = await controller.screenshot()
        print(f"   Screenshot: {result}")
        
        # Get page info
        if controller.page and not controller.page.is_closed():
            title = await controller.page.title()
            url = controller.page.url
            print(f"\n4. Page information:")
            print(f"   Title: {title}")
            print(f"   URL: {url}")
        
        elapsed = time.time() - start
        print(f"\n✅ Test completed in {elapsed:.1f}s")
        
        # Ask user if they saw the window
        print("\n" + "="*60)
        print("QUESTION: Did you see a Firefox window open with YouTube?")
        print("="*60)
        print("1. Yes, I saw it!")
        print("2. No, nothing appeared")
        print("3. I saw something but it closed quickly")
        
        return True
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        
        # Try to take error screenshot if possible
        try:
            if controller.page:
                error_screenshot = await controller._take_debug_screenshot("error")
                print(f"   Error screenshot: {error_screenshot}")
        except:
            pass
            
        return False
    finally:
        print("\n🧹 Cleaning up...")
        # Keep browser open for a moment so user can see it
        print("   Browser will close in 5 seconds...")
        await asyncio.sleep(5)
        
        if controller.browser:
            await controller.browser.close()
            print("   Browser closed.")
        if controller.playwright:
            await controller.playwright.stop()
            print("   Playwright stopped.")

if __name__ == "__main__":
    success = asyncio.run(test_visible())
    sys.exit(0 if success else 1)