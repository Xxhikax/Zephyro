#!/usr/bin/env python3
"""
Test Firefox with Wayland workarounds for visibility issues.
"""
import asyncio
import os
import sys
import time
import subprocess
import shutil
from pathlib import Path

# Force X11 instead of Wayland for Firefox
os.environ['MOZ_ENABLE_WAYLAND'] = '0'
os.environ['GDK_BACKEND'] = 'x11'
# Ensure DISPLAY is set
if not os.environ.get('DISPLAY'):
    os.environ['DISPLAY'] = ':0'

print("🔧 Configuring Firefox for X11 instead of Wayland")
print(f"   MOZ_ENABLE_WAYLAND={os.environ.get('MOZ_ENABLE_WAYLAND')}")
print(f"   GDK_BACKEND={os.environ.get('GDK_BACKEND')}")
print(f"   DISPLAY={os.environ.get('DISPLAY')}")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.automation.browser_controller import BrowserController

async def test_with_x11():
    print("\n🔥 Testing Firefox with X11 workaround")
    
    # Close existing Firefox to avoid conflicts
    print("1. Checking existing Firefox processes...")
    result = subprocess.run(['pgrep', '-x', 'firefox'], capture_output=True, text=True)
    if result.returncode == 0:
        pids = result.stdout.strip().split()
        print(f"   ⚠️  Firefox is running (PIDs: {', '.join(pids)})")
        print("   Closing Firefox to avoid conflicts...")
        subprocess.run(['pkill', '-x', 'firefox'])
        await asyncio.sleep(2)
    
    # Create fresh profile
    profile_dir = Path.home() / ".cache" / "zephyro" / "firefox_x11_test"
    if profile_dir.exists():
        shutil.rmtree(profile_dir)
    profile_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"2. Using fresh profile: {profile_dir}")
    
    # Create browser controller with visible window
    controller = BrowserController(
        headless=False,  # Must be False for visible window
        persistent_user_data_dir=str(profile_dir),
        firefox_profile=None,
        firefox_executable=None
    )
    
    try:
        print("3. Opening YouTube...")
        start = time.time()
        
        result = await controller.navigate_to("youtube.com")
        print(f"   Result: {result}")
        
        # Wait for window to appear
        print("4. Waiting 15 seconds for window to appear...")
        print("   Please look for a Firefox window with YouTube")
        print("   Check all workspaces and virtual desktops.")
        
        for i in range(15, 0, -1):
            print(f"   {i}...")
            await asyncio.sleep(1)
        
        # Verify page loaded
        if controller.page and not controller.page.is_closed():
            title = await controller.page.title()
            url = controller.page.url
            print(f"\n5. Page loaded successfully:")
            print(f"   Title: {title}")
            print(f"   URL: {url}")
            
            # Take screenshot
            print("6. Taking screenshot...")
            screenshot_result = await controller.screenshot()
            print(f"   {screenshot_result}")
            
            # List windows using wmctrl
            try:
                windows = subprocess.run(['wmctrl', '-l'], capture_output=True, text=True)
                if windows.returncode == 0:
                    firefox_windows = [w for w in windows.stdout.split('\n') if 'firefox' in w.lower()]
                    if firefox_windows:
                        print("7. Firefox windows detected:")
                        for win in firefox_windows[:3]:
                            print(f"   - {win}")
                    else:
                        print("7. No Firefox windows found in wmctrl")
                else:
                    print("7. wmctrl not available")
            except Exception as e:
                print(f"7. Window listing error: {e}")
        
        elapsed = time.time() - start
        print(f"\n✅ Test completed in {elapsed:.1f}s")
        
        # Keep browser open for inspection
        print("\n" + "="*60)
        print("QUESTION: Did you see a Firefox window?")
        print("="*60)
        print("The browser will stay open for 30 seconds.")
        print("Check all workspaces, taskbar, and window list.")
        
        await asyncio.sleep(30)
        
        return True
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        print("\n🧹 Cleaning up...")
        if controller.browser:
            await controller.browser.close()
        if controller.playwright:
            await controller.playwright.stop()

if __name__ == "__main__":
    print("="*60)
    print("FIREFOX VISIBILITY TEST WITH X11 WORKAROUND")
    print("Environment: KDE + Wayland detected")
    print("="*60)
    
    success = asyncio.run(test_with_x11())
    
    if success:
        print("\n✅ Test completed. If you didn't see a window:")
        print("   1. Firefox may be opening minimized")
        print("   2. Check system tray or taskbar")
        print("   3. Try Alt+Tab to switch windows")
        print("   4. Or use headless=True for automation")
    else:
        print("\n❌ Test failed. Firefox may not be compatible with current setup.")
        print("   Consider using headless mode for automation.")
    
    sys.exit(0 if success else 1)