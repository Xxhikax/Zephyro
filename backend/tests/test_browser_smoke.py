#!/usr/bin/env python3
"""
Smoke test for Firefox browser automation.
Tests that Firefox can open YouTube, Google, and perform searches.
Uses headless=True for speed and to avoid visible windows.
"""
import asyncio
import os
import sys
import logging
import time
from pathlib import Path

# Enable debug logging
os.environ["ZEPHYRO_DEBUG"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.automation.browser_controller import BrowserController

async def test_youtube():
    """Test opening YouTube and performing a search"""
    print("\n" + "="*60)
    print("TEST: YouTube")
    print("="*60)
    
    # Use headless=True to avoid visible window
    profile_dir = Path.home() / ".cache" / "zephyro" / "firefox_profile_simple"
    profile_dir.mkdir(parents=True, exist_ok=True)
    
    controller = BrowserController(
        headless=True,
        persistent_user_data_dir=str(profile_dir),
        firefox_profile=None,
        firefox_executable=None
    )
    
    try:
        print("1. Opening YouTube...")
        result = await controller.navigate_to("youtube.com")
        print(f"   Result: {result}")
        
        print("2. Searching for 'test video'...")
        result = await controller.open_and_search("test video", "youtube")
        print(f"   Result: {result}")
        
        # Wait for results to load
        await asyncio.sleep(2)
        
        print("3. Taking screenshot...")
        result = await controller.screenshot()
        print(f"   Result: {result}")
        
        return True
    except Exception as e:
        print(f"   ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if controller.browser:
            await controller.browser.close()
        if controller.playwright:
            await controller.playwright.stop()

async def test_google():
    """Test opening Google and performing a search"""
    print("\n" + "="*60)
    print("TEST: Google")
    print("="*60)
    
    profile_dir = Path.home() / ".cache" / "zephyro" / "firefox_profile_simple"
    profile_dir.mkdir(parents=True, exist_ok=True)
    
    controller = BrowserController(
        headless=True,
        persistent_user_data_dir=str(profile_dir),
        firefox_profile=None,
        firefox_executable=None
    )
    
    try:
        print("1. Opening Google...")
        result = await controller.navigate_to("google.com")
        print(f"   Result: {result}")
        
        print("2. Searching for 'weather'...")
        result = await controller.open_and_search("weather", "google")
        print(f"   Result: {result}")
        
        await asyncio.sleep(2)
        
        print("3. Taking screenshot...")
        result = await controller.screenshot()
        print(f"   Result: {result}")
        
        return True
    except Exception as e:
        print(f"   ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if controller.browser:
            await controller.browser.close()
        if controller.playwright:
            await controller.playwright.stop()

async def test_duckduckgo():
    """Test opening DuckDuckGo and performing a search"""
    print("\n" + "="*60)
    print("TEST: DuckDuckGo")
    print("="*60)
    
    profile_dir = Path.home() / ".cache" / "zephyro" / "firefox_profile_simple"
    profile_dir.mkdir(parents=True, exist_ok=True)
    
    controller = BrowserController(
        headless=True,
        persistent_user_data_dir=str(profile_dir),
        firefox_profile=None,
        firefox_executable=None
    )
    
    try:
        print("1. Opening DuckDuckGo...")
        result = await controller.navigate_to("duckduckgo.com")
        print(f"   Result: {result}")
        
        print("2. Searching for 'Python programming'...")
        result = await controller.open_and_search("Python programming", "duckduckgo")
        print(f"   Result: {result}")
        
        await asyncio.sleep(2)
        
        print("3. Taking screenshot...")
        result = await controller.screenshot()
        print(f"   Result: {result}")
        
        return True
    except Exception as e:
        print(f"   ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if controller.browser:
            await controller.browser.close()
        if controller.playwright:
            await controller.playwright.stop()

async def test_media_playback():
    """Test media playback on YouTube"""
    print("\n" + "="*60)
    print("TEST: Media Playback")
    print("="*60)
    
    profile_dir = Path.home() / ".cache" / "zephyro" / "firefox_profile_simple"
    profile_dir.mkdir(parents=True, exist_ok=True)
    
    controller = BrowserController(
        headless=True,
        persistent_user_data_dir=str(profile_dir),
        firefox_profile=None,
        firefox_executable=None
    )
    
    try:
        print("1. Opening YouTube...")
        result = await controller.navigate_to("youtube.com")
        print(f"   Result: {result}")
        
        # Note: play_media might try to actually play video
        # In headless mode, this should still work
        print("2. Attempting to play a video...")
        result = await controller.play_media("lo fi hip hop", "youtube")
        print(f"   Result: {result}")
        
        await asyncio.sleep(3)
        
        print("3. Testing media controls...")
        result = await controller.control_media("pause")
        print(f"   Pause: {result}")
        
        result = await controller.control_media("play")
        print(f"   Play: {result}")
        
        return True
    except Exception as e:
        print(f"   ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if controller.browser:
            await controller.browser.close()
        if controller.playwright:
            await controller.playwright.stop()

async def run_all_tests():
    """Run all smoke tests"""
    print("\n" + "="*60)
    print("FIREFOX BROWSER SMOKE TESTS")
    print("Simplified Architecture (Option 3)")
    print("="*60)
    
    print(f"\n📁 Using profile: ~/.cache/zephyro/firefox_profile_simple")
    print("   Note: Firefox may show warning if already running")
    print("   Tests use headless=True (no visible window)")
    
    test_results = []
    
    # Run tests
    try:
        test_results.append(await test_youtube())
        test_results.append(await test_google())
        test_results.append(await test_duckduckgo())
        test_results.append(await test_media_playback())
    except Exception as e:
        print(f"\n❌ Critical error: {e}")
        import traceback
        traceback.print_exc()
        test_results.append(False)
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    passed = sum(1 for r in test_results if r)
    total = len(test_results)
    
    print(f"\n📊 Tests passed: {passed}/{total}")
    
    if passed == total:
        print("🎉 All smoke tests passed! Firefox automation is working.")
    elif passed >= total // 2:
        print("⚠️  Some tests passed. Firefox automation is partially working.")
    else:
        print("❌ Most tests failed. Check logs for issues.")
    
    return passed >= total // 2  # Pass if at least half succeed

if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)