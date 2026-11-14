#!/usr/bin/env python3
"""
Test browser controller async methods for event loop conflicts.
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.automation.browser_controller import BrowserController

async def test_navigate():
    """Test navigate_to within existing event loop."""
    browser = BrowserController(headless=True)
    try:
        # This should not raise "Cannot run the event loop while another loop is running"
        result = await browser.navigate_to("https://example.com")
        print(f"Navigate result: {result}")
        assert result.startswith("✓") or result.startswith("✗")
        # If error due to network, that's fine; we just need no event loop conflict.
    finally:
        await browser.close()

async def test_concurrent_calls():
    """Test multiple async calls."""
    browser = BrowserController(headless=True)
    try:
        # Run multiple tasks (they will be serialized by executor)
        tasks = [
            browser.navigate_to("https://example.com"),
            browser.open_and_search("test", "duckduckgo"),
            browser.get_text(),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, r in enumerate(results):
            print(f"Task {i} result: {r}")
            if isinstance(r, Exception):
                # Allow exceptions due to network/playwright, but not event loop errors
                assert "Cannot run the event loop while another loop is running" not in str(r)
    finally:
        await browser.close()

async def main():
    print("Testing browser controller async methods...")
    await test_navigate()
    print("Navigate test passed.")
    await test_concurrent_calls()
    print("Concurrent calls test passed.")
    print("\n✅ SUCCESS: No event loop conflicts detected.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)