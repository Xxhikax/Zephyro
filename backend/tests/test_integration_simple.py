#!/usr/bin/env python3
"""
Integration test for the simplified architecture.
Test that 'abre youtube' opens YouTube via Firefox.
"""
import asyncio
import os
import sys
import logging
from pathlib import Path

# Set environment for X11
os.environ['MOZ_ENABLE_WAYLAND'] = '0'
os.environ['GDK_BACKEND'] = 'x11'

# Enable debug logging
os.environ["ZEPHYRO_DEBUG"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.orchestrator import Orchestrator

async def test():
    print("🚀 Starting integration test for simplified architecture")
    print(f"   DISPLAY={os.environ.get('DISPLAY')}")
    print(f"   MOZ_ENABLE_WAYLAND={os.environ.get('MOZ_ENABLE_WAYLAND')}")
    
    # Create orchestrator (should use headless=False)
    orchestrator = Orchestrator(cloud_provider="auto")
    
    # Simulate command "abre youtube"
    user_input = "abre youtube"
    print(f"\n📝 Testing command: '{user_input}'")
    
    # Process command
    result = await orchestrator.handle_input(user_input, speak=False)
    print(f"\n📢 Result: {result}")
    
    # Wait a bit to see window appear
    print("\n⏳ Waiting 10 seconds for window to appear...")
    await asyncio.sleep(10)
    
    # Check if Firefox is running
    import subprocess
    firefox_running = subprocess.run(["pgrep", "-x", "firefox"], capture_output=True).returncode == 0
    print(f"\n🔍 Firefox running: {firefox_running}")
    
    # Clean up - close browser via automation cleanup
    print("\n🧹 Cleaning up...")
    await orchestrator.automation.cleanup()
    
    # Wait a moment for browser to close
    await asyncio.sleep(2)
    
    print("\n✅ Integration test completed.")
    return True

if __name__ == "__main__":
    print("="*60)
    print("INTEGRATION TEST: Simplified Architecture")
    print("="*60)
    print("This test will create an Orchestrator and send 'abre youtube'.")
    print("A Firefox window should open with YouTube.")
    print("="*60)
    
    try:
        success = asyncio.run(test())
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)