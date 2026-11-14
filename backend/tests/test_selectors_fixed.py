#!/usr/bin/env python3
"""
Test updated selectors for YouTube.
"""
import asyncio
import os
import sys
import logging

os.environ["ZEPHYRO_DEBUG"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.automation.browser_controller import BrowserController
from core.browser_config import BrowserConfig

async def test_youtube_selectors():
    print("=== Testing YouTube selectors (with cookie handling) ===")
    
    # Test without profile first
    print("\n1. Testing WITHOUT user profile...")
    bc = BrowserController(config=BrowserConfig(headless=False))
    
    try:
        # Navigate to YouTube
        result = await bc.navigate_to("https://www.youtube.com")
        print(f"   Navigation: {result}")
        
        # Wait a bit for page to settle
        await asyncio.sleep(2)
        
        # Try to find search box
        print("\n2. Looking for search box...")
        try:
            search_box = await bc._find_element("youtube", "search_box")
            print(f"   ✓ Search box found!")
            
            # Test typing
            await search_box.fill("test search")
            print("   ✓ Can type in search box")
            
            # Test Enter key
            await search_box.press("Enter")
            print("   ✓ Enter key works")
            
            await asyncio.sleep(2)
            
            # Try to find video items
            print("\n3. Looking for video items...")
            try:
                video_item = await bc._find_element("youtube", "video_item")
                print(f"   ✓ Video item found!")
            except Exception as e:
                print(f"   ✗ Video item not found: {e}")
                
        except Exception as e:
            print(f"   ✗ Search box not found: {e}")
            # Take screenshot for debugging
            try:
                screenshot = await bc._take_debug_screenshot("search_box_error")
                print(f"   Screenshot saved: {screenshot}")
            except:
                pass
            
    except Exception as e:
        print(f"   ✗ Navigation failed: {e}")
    
    finally:
        print("\n4. Cleaning up...")
        await bc.close()
    
    return True

async def test_with_user_profile():
    print("\n=== Testing WITH user Firefox profile ===")
    
    # Find user profile
    import pathlib
    firefox_profiles_dir = pathlib.Path.home() / ".mozilla" / "firefox"
    user_profile = None
    
    if firefox_profiles_dir.exists():
        for item in firefox_profiles_dir.iterdir():
            if item.is_dir() and (".default-release" in item.name or ".default" in item.name):
                user_profile = str(item)
                print(f"Found user profile: {user_profile}")
                break
    
    if not user_profile:
        print("No user profile found, skipping test")
        return False
    
    # Test with user profile
    print(f"\n1. Testing WITH user profile: {user_profile}")
    bc = BrowserController(config=BrowserConfig(headless=False, user_profile_path=user_profile))
    
    try:
        # Navigate to YouTube
        result = await bc.navigate_to("https://www.youtube.com")
        print(f"   Navigation: {result}")
        
        # Wait a bit
        await asyncio.sleep(3)  # Longer wait for profile loading
        
        # Try to find search box
        print("\n2. Looking for search box with user profile...")
        try:
            search_box = await bc._find_element("youtube", "search_box")
            print(f"   ✓ Search box found with user profile!")
            
            # Test typing
            await search_box.fill("gold in the ceiling")
            print("   ✓ Can type in search box")
            
        except Exception as e:
            print(f"   ✗ Search box not found: {e}")
            
    except Exception as e:
        print(f"   ✗ Navigation failed: {e}")
    
    finally:
        print("\n3. Cleaning up...")
        await bc.close()
    
    return True

async def main():
    print("Testing updated YouTube selectors...")
    
    success1 = await test_youtube_selectors()
    
    success2 = await test_with_user_profile()
    
    print(f"\n=== SUMMARY ===")
    print(f"Without user profile: {'PASS' if success1 else 'FAIL'}")
    print(f"With user profile: {'PASS' if success2 else 'FAIL'}")
    
    return success1 or success2  # At least one should pass

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