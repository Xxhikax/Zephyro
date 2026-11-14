#!/usr/bin/env python3
"""
Test Firefox window visibility with X11 window detection.
"""
import asyncio
import os
import sys
import subprocess
import time
from pathlib import Path

# Force X11 for visibility
os.environ['MOZ_ENABLE_WAYLAND'] = '0'
os.environ['GDK_BACKEND'] = 'x11'

print("🖥️  Testing Firefox window visibility")
print(f"   DISPLAY={os.environ.get('DISPLAY', 'Not set')}")
print(f"   MOZ_ENABLE_WAYLAND={os.environ.get('MOZ_ENABLE_WAYLAND')}")
print(f"   GDK_BACKEND={os.environ.get('GDK_BACKEND')}")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.automation.browser_controller import BrowserController

def get_x11_windows():
    """Get list of X11 windows using xwininfo."""
    try:
        result = subprocess.run(
            ['xwininfo', '-root', '-tree'],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout
    except Exception as e:
        return f"Error getting windows: {e}"

def get_window_ids():
    """Extract window IDs for Firefox."""
    try:
        result = subprocess.run(
            ['xprop', '-root'],
            capture_output=True, text=True, timeout=5
        )
        lines = result.stdout.split('\n')
        # Look for _NET_CLIENT_LIST
        for line in lines:
            if '_NET_CLIENT_LIST(WINDOW)' in line:
                ids = line.split('#')[-1].strip()
                return ids
        return "No _NET_CLIENT_LIST found"
    except Exception as e:
        return f"Error: {e}"

def capture_screen(filename):
    """Capture entire screen using import (ImageMagick)."""
    try:
        subprocess.run(['import', '-window', 'root', filename], check=True)
        return True
    except Exception as e:
        print(f"   Failed to capture screen: {e}")
        return False

async def test():
    # Fresh profile
    profile_dir = Path.home() / ".cache" / "zephyro" / "firefox_window_test"
    if profile_dir.exists():
        import shutil
        shutil.rmtree(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    
    controller = BrowserController(
        headless=False,
        persistent_user_data_dir=str(profile_dir),
        firefox_profile=None,
        firefox_executable=None
    )
    
    try:
        print("\n1. Checking X11 windows before launch...")
        print(get_x11_windows())
        
        print("\n2. Opening YouTube...")
        result = await controller.navigate_to("youtube.com")
        print(f"   {result}")
        
        print("\n3. Waiting 5 seconds for window to appear...")
        await asyncio.sleep(5)
        
        print("\n4. Checking X11 windows after launch...")
        windows = get_x11_windows()
        print(windows)
        
        # Look for Firefox in output
        if 'Firefox' in windows or 'firefox' in windows:
            print("\n✅ Firefox window detected in X11 window tree!")
        else:
            print("\n⚠️  No Firefox window found in X11 window tree.")
        
        print("\n5. Getting window IDs...")
        print("   " + get_window_ids())
        
        print("\n6. Capturing screen screenshot...")
        screen_file = "/tmp/firefox_screen_test.png"
        if capture_screen(screen_file):
            print(f"   Screen captured: {screen_file}")
            # Check if file has size > 0
            if os.path.getsize(screen_file) > 0:
                print("   Screenshot saved successfully.")
            else:
                print("   Warning: screenshot file is empty.")
        else:
            print("   Could not capture screen.")
        
        # Verify page loaded
        if controller.page:
            title = await controller.page.title()
            print(f"\n7. Page loaded: {title}")
            # Screenshot via playwright
            screenshot = await controller.screenshot()
            print(f"   Playwright screenshot: {screenshot}")
        
        print("\n8. Waiting 10 more seconds (keep looking for window)...")
        await asyncio.sleep(10)
        
        return True
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        print("\n🧹 Closing browser...")
        if controller.browser:
            await controller.browser.close()
        # Clean up profile
        try:
            shutil.rmtree(profile_dir)
        except:
            pass

if __name__ == "__main__":
    print("="*60)
    print("FIREFOX WINDOW VISIBILITY TEST")
    print("="*60)
    print("This test will check if Firefox window appears in X11.")
    print("="*60)
    
    success = asyncio.run(test())
    
    if success:
        print("\n✅ Test completed.")
        print("   Check output above for window detection.")
    else:
        print("\n❌ Test failed.")
        
    sys.exit(0 if success else 1)