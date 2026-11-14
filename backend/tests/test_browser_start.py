#!/usr/bin/env python3
import asyncio
import os
import sys
import signal

os.environ['MOZ_ENABLE_WAYLAND'] = '0'
os.environ['GDK_BACKEND'] = 'x11'
os.environ['ZEPHYRO_DEBUG'] = '1'

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.automation.browser_controller import BrowserController

async def test():
    print("Starting browser headless=True...")
    controller = BrowserController(headless=True)
    try:
        print("Calling navigate_to about:blank...")
        result = await controller.navigate_to("about:blank")
        print(f"Result: {result}")
        return True
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        print("Closing...")
        await controller.close()

def timeout_handler(signum, frame):
    print("TIMEOUT!")
    raise TimeoutError

if __name__ == "__main__":
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(30)
    try:
        success = asyncio.run(test())
        sys.exit(0 if success else 1)
    except TimeoutError:
        print("Test timed out after 30 seconds")
        sys.exit(1)