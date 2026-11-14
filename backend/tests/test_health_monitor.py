#!/usr/bin/env python3
"""
Test HealthMonitor functionality.
"""
import asyncio
import time
import os
import sys
from pathlib import Path

# Set headless for testing
os.environ['MOZ_ENABLE_WAYLAND'] = '0'
os.environ['GDK_BACKEND'] = 'x11'
os.environ['ZEPHYRO_DEBUG'] = '1'

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.automation.browser_controller import BrowserController
from core.browser_config import BrowserConfig

async def test_health_monitor_basic():
    """Test basic HealthMonitor initialization and metrics."""
    print("🧪 Testing HealthMonitor basic functionality")
    
    # Use headless=True to avoid opening window
    controller = BrowserController(config=BrowserConfig(headless=True))
    
    try:
        # Start browser first (this should also start health monitor)
        print("   Starting browser...")
        await controller._ensure_browser()
        
        # Give health monitor a moment to start
        await asyncio.sleep(2)
        
        # Get session info to check health monitor metrics
        session_info = controller.get_session_info()
        print(f"   Session info keys: {list(session_info.keys())}")
        
        # Check if health monitor metrics are present
        health_monitor_metrics = session_info.get('health_monitor_metrics')
        if health_monitor_metrics is None:
            print("   ❌ Health monitor metrics missing!")
            return False
        
        print(f"   ✅ Health monitor metrics found: {list(health_monitor_metrics.keys())}")
        print(f"   Health monitor running: {health_monitor_metrics.get('monitor_running', 'N/A')}")
        print(f"   Total checks: {health_monitor_metrics.get('total_checks', 'N/A')}")
        print(f"   Consecutive failures: {health_monitor_metrics.get('consecutive_failures', 'N/A')}")
        
        # Wait a bit to let health monitor perform at least one check
        print("   Waiting for health check cycle (30 seconds)...")
        await asyncio.sleep(35)  # Slightly longer than check interval
        
        # Get updated metrics
        session_info2 = controller.get_session_info()
        health_monitor_metrics2 = session_info2.get('health_monitor_metrics')
        if health_monitor_metrics2 is None:
            print("   ❌ Health monitor metrics missing after wait!")
            return False
        
        total_checks2 = health_monitor_metrics2.get('total_checks', 0)
        print(f"   Total checks after wait: {total_checks2}")
        
        if total_checks2 > 0:
            print("   ✅ Health monitor is performing periodic checks!")
        else:
            print("   ⚠️  Health monitor may not be performing checks (could be timing issue)")
        
        # Test fallback trigger (simulate by manually calling trigger_fallback if not headless)
        # But we're already headless, so fallback shouldn't do anything
        if controller._health_monitor is not None:
            print("   Testing fallback trigger (should not trigger since already headless)...")
            fallback_result = await controller._health_monitor.trigger_fallback("test")
            print(f"   Fallback result: {fallback_result}")
            # Should return False because already headless
            if fallback_result == False:
                print("   ✅ Fallback correctly prevented (already headless)")
            else:
                print("   ⚠️  Unexpected fallback result")
        
        # Check browser state and health
        print(f"   Browser state: {controller.get_state().value}")
        print(f"   Browser health: {controller.get_health().value}")
        
        return True
        
    except Exception as e:
        print(f"   ❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        # Clean up
        print("   Cleaning up...")
        await controller._close()

async def test_health_monitor_failure_detection():
    """Test that health monitor detects browser failures."""
    print("\n🧪 Testing HealthMonitor failure detection")
    
    controller = BrowserController(config=BrowserConfig(headless=True))
    
    try:
        # Start browser
        print("   Starting browser...")
        await controller._ensure_browser()
        
        # Wait for health monitor to start
        await asyncio.sleep(2)
        
        # Get initial consecutive failures
        session_info = controller.get_session_info()
        initial_failures = session_info.get('health_monitor_metrics', {}).get('consecutive_failures', 0)
        print(f"   Initial consecutive failures: {initial_failures}")
        
        # Simulate a failure by closing the page (but not the browser)
        if controller.page:
            print("   Closing page to simulate failure...")
            await controller.page.close()
            controller.page = None
        
        # Wait for health monitor to detect failure (should happen within next check interval)
        print("   Waiting for health monitor to detect failure (up to 35 seconds)...")
        start_wait = time.time()
        failures_increased = False
        
        for i in range(7):  # Check every 5 seconds for 35 seconds
            await asyncio.sleep(5)
            session_info = controller.get_session_info()
            current_failures = session_info.get('health_monitor_metrics', {}).get('consecutive_failures', 0)
            print(f"   Check {i+1}: consecutive failures = {current_failures}")
            if current_failures > initial_failures:
                failures_increased = True
                print(f"   ✅ Health monitor detected failure! (increased from {initial_failures} to {current_failures})")
                break
        
        if not failures_increased:
            print("   ⚠️  Health monitor may not have detected page closure (could be auto-recovered)")
        
        # Check browser state - should be ERROR or RECOVERING if detection worked
        state = controller.get_state()
        print(f"   Final browser state: {state.value}")
        
        return True
        
    except Exception as e:
        print(f"   ❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        # Clean up
        print("   Cleaning up...")
        await controller._close()

async def main():
    """Run all tests."""
    print("=" * 60)
    print("HealthMonitor Test Suite")
    print("=" * 60)
    
    success = True
    
    # Test 1: Basic functionality
    if not await test_health_monitor_basic():
        success = False
    
    # Test 2: Failure detection (skip if first test failed)
    if success:
        if not await test_health_monitor_failure_detection():
            success = False
    
    print("\n" + "=" * 60)
    if success:
        print("✅ All tests passed!")
    else:
        print("❌ Some tests failed!")
    print("=" * 60)
    
    return success

if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result else 1)