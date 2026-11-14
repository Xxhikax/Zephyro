#!/usr/bin/env python3
"""
Test Firefox remote debugging connection.
Usage:
1. Start Firefox manually with remote debugging:
   firefox --remote-debugging-port 9222 --marionette
2. Run this script.
"""
import asyncio
import logging
import sys
sys.path.insert(0, '.')

from modules.automation.browser_controller import BrowserController

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

async def test_connection():
    print("Testing Firefox remote debugging connection...")
    controller = BrowserController(headless=False)
    try:
        # Start playwright
        from playwright.async_api import async_playwright
        playwright = await async_playwright().start()
        print("Playwright started")
        
        # Try to connect to existing Firefox
        browser = await controller._try_connect_to_existing_firefox(playwright, port=9222)
        if browser:
            print("SUCCESS: Connected to existing Firefox")
            # Get existing context or create new
            contexts = browser.contexts
            if contexts:
                context = contexts[0]
                print(f"Using existing context ({len(contexts)} contexts)")
            else:
                context = await browser.new_context(viewport={'width': 1280, 'height': 720})
                print("Created new context")
            pages = context.pages
            if pages:
                page = pages[0]
                print(f"Using existing page ({len(pages)} pages)")
            else:
                page = await context.new_page()
                print("Created new page")
            
            # Navigate to test page
            await page.goto('https://example.com')
            title = await page.title()
            print(f"Page title: {title}")
            
            # Close browser (but note: this will close the connected Firefox instance)
            # Actually we should not close the browser, just disconnect.
            await browser.close()
            print("Browser closed (disconnected)")
        else:
            print("FAILED: Could not connect to Firefox on port 9222")
            print("Make sure Firefox is running with --remote-debugging-port 9222 --marionette")
        
        await playwright.stop()
        print("Playwright stopped")
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

async def test_restart_browser_with_connection():
    print("\n--- Testing BrowserController._restart_browser with connection ---")
    controller = BrowserController(headless=False)
    try:
        await controller._restart_browser()
        print("SUCCESS: _restart_browser completed")
        if controller.browser and controller.browser.is_connected():
            print("Browser is connected")
            # Try to navigate
            result = await controller.navigate_to('https://example.com')
            print(f"Navigate result: {result}")
        else:
            print("Browser not connected")
    except Exception as e:
        print(f"ERROR in _restart_browser: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if controller.browser:
            await controller.browser.close()
        if controller.playwright:
            await controller.playwright.stop()

if __name__ == '__main__':
    # First test direct connection
    asyncio.run(test_connection())
    # Then test browser controller's restart logic
    asyncio.run(test_restart_browser_with_connection())