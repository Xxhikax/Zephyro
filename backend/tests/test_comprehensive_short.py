#!/usr/bin/env python3
"""
Comprehensive tests with short intervals for faster execution.
"""
import asyncio
import time
import os
import sys
import logging
from unittest.mock import Mock, patch, AsyncMock
from pathlib import Path

# Set headless for testing
os.environ['MOZ_ENABLE_WAYLAND'] = '0'
os.environ['GDK_BACKEND'] = 'x11'
os.environ['ZEPHYRO_DEBUG'] = '1'

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.automation.browser_controller import BrowserController, BrowserState, BrowserHealth
from core.browser_config import BrowserConfig

def create_controller_with_fast_health_monitor(headless=True):
    """Create a BrowserController with health monitor check interval reduced to 2 seconds."""
    controller = BrowserController(config=BrowserConfig(headless=headless))
    # Reduce health monitor check interval if monitor exists
    if controller._health_monitor:
        controller._health_monitor._check_interval = 2
    return controller

async def test_state_transitions():
    print("🧪 Testing state transitions")
    controller = create_controller_with_fast_health_monitor(headless=True)
    try:
        assert controller.get_state() == BrowserState.CLOSED
        await controller._set_state(BrowserState.STARTING, "test")
        assert controller.get_state() == BrowserState.STARTING
        history = controller.get_state_history()
        assert len(history) >= 1
        print("   ✅ State transitions logged correctly")
        return True
    except Exception as e:
        print(f"   ❌ State transition test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await controller._close()

async def test_command_queue_high_load():
    print("🧪 Testing command queue under high load")
    controller = create_controller_with_fast_health_monitor(headless=True)
    try:
        await controller._ensure_browser()
        num_commands = 10
        results = []
        async def dummy_command(idx):
            await asyncio.sleep(0.001)
            return f"cmd_{idx}"
        tasks = []
        for i in range(num_commands):
            task = asyncio.create_task(
                controller._enqueue_command(dummy_command, i, command_name=f"dummy_{i}")
            )
            tasks.append(task)
        results = await asyncio.gather(*tasks, return_exceptions=True)
        failures = [r for r in results if isinstance(r, Exception)]
        if failures:
            print(f"   ❌ {len(failures)} commands failed")
            return False
        queue_status = controller.get_queue_status()
        assert queue_status['queue_initialized'] == True
        assert queue_status['queue_size'] == 0
        print(f"   ✅ {num_commands} commands processed via queue")
        return True
    except Exception as e:
        print(f"   ❌ Command queue load test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await controller._close()

async def test_health_monitor_fast_detection():
    """Test health monitor detection with fast check interval."""
    print("🧪 Testing health monitor fast detection")
    controller = create_controller_with_fast_health_monitor(headless=True)
    try:
        await controller._ensure_browser()
        # Ensure health monitor is running with short interval
        if controller._health_monitor:
            controller._health_monitor._check_interval = 2
        # Get initial consecutive failures
        session_info = controller.get_session_info()
        initial_failures = session_info.get('health_monitor_metrics', {}).get('consecutive_failures', 0)
        # Close page to simulate failure
        if controller.page:
            await controller.page.close()
            controller.page = None
        # Wait for health monitor to detect (max 3 cycles of 2 seconds)
        for i in range(6):  # 6 * 1 second = 6 seconds max
            await asyncio.sleep(1)
            session_info = controller.get_session_info()
            current_failures = session_info.get('health_monitor_metrics', {}).get('consecutive_failures', 0)
            if current_failures > initial_failures:
                print(f"   ✅ Health monitor detected page closure (failures: {current_failures})")
                return True
        print("   ❌ Health monitor did not detect page closure within expected time")
        return False
    except Exception as e:
        print(f"   ❌ Health monitor detection test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await controller._close()

async def test_health_monitor_memory_check_mocked():
    print("🧪 Testing health monitor memory check (mocked)")
    with patch('modules.automation.browser_controller.psutil') as mock_psutil:
        mock_process = Mock()
        mock_process.info = {'pid': 123, 'name': 'firefox', 'memory_info': Mock()}
        mock_process.memory_info.return_value.rss = 10 * 1024 * 1024 * 1024  # 10GB
        mock_psutil.process_iter.return_value = [mock_process]
        controller = create_controller_with_fast_health_monitor(headless=True)
        try:
            await controller._ensure_browser()
            if controller._health_monitor:
                await controller._health_monitor._check_memory_usage()
                health = controller.get_health()
                if health == BrowserHealth.DEGRADED:
                    print("   ✅ Health monitor detected high memory usage")
                    return True
                else:
                    print(f"   ❌ Health not degraded, current health: {health}")
                    return False
            else:
                print("   ⚠️  Health monitor not initialized")
                return False
        except Exception as e:
            print(f"   ❌ Health monitor memory check test failed: {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            await controller._close()

async def test_recovery_after_crash():
    print("🧪 Testing recovery after crash")
    controller = create_controller_with_fast_health_monitor(headless=True)
    try:
        await controller._ensure_browser()
        controller.browser = None
        controller.context = None
        controller.page = None
        await controller._set_state(BrowserState.ERROR, "simulated_crash")
        result = await controller.navigate_to("https://duckduckgo.com")
        if result.startswith("✓"):
            print("   ✅ Browser recovered after simulated crash")
            return True
        else:
            print(f"   ❌ Recovery failed, result: {result}")
            return False
    except Exception as e:
        print(f"   ❌ Recovery after crash test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await controller._close()

async def test_automatic_fallback_mocked():
    """Test automatic fallback using mocked headful failure."""
    print("🧪 Testing automatic fallback (mocked)")
    # Create controller with headless=False but mock the launch to simulate headful
    with patch('modules.automation.browser_controller.async_playwright') as mock_playwright:
        mock_browser = AsyncMock()
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_page.is_closed.return_value = False
        mock_context.pages = [mock_page]
        mock_context.browser = mock_browser
        mock_browser.contexts = [mock_context]
        mock_browser.is_connected.return_value = True
        mock_playwright.return_value.start.return_value = AsyncMock(firefox=AsyncMock(launch=AsyncMock(return_value=mock_browser)))
        
        controller = BrowserController(config=BrowserConfig(headless=False))
        # Reduce health monitor interval
        if controller._health_monitor:
            controller._health_monitor._check_interval = 2
            controller._health_monitor._max_consecutive_failures_before_fallback = 2
            controller._health_monitor._health_metrics['consecutive_failures'] = 2
            controller._health_monitor._health_metrics['headless_fallback_used'] = False
            controller._health_monitor._last_fallback_trigger_time = 0
        
        try:
            await controller._ensure_browser()
            # Simulate health check failure by mocking is_healthy to return False
            with patch.object(controller, 'is_healthy', return_value=False):
                # Wait for health monitor to trigger fallback
                for _ in range(4):
                    await asyncio.sleep(1)
                    if controller._health_monitor._health_metrics.get('fallback_triggered'):
                        print("   ✅ Fallback triggered (simulated)")
                        return True
            print("   ❌ Fallback not triggered")
            return False
        except Exception as e:
            print(f"   ❌ Automatic fallback test failed: {e}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            await controller._close()

async def main():
    print("=" * 60)
    print("BrowserController Comprehensive Test Suite (Fast)")
    print("=" * 60)
    
    tests = [
        test_state_transitions,
        test_command_queue_high_load,
        test_health_monitor_fast_detection,
        test_health_monitor_memory_check_mocked,
        test_recovery_after_crash,
        test_automatic_fallback_mocked,
    ]
    
    success = True
    for test_func in tests:
        if not await test_func():
            success = False
    
    print("\n" + "=" * 60)
    if success:
        print("✅ All comprehensive tests passed!")
    else:
        print("❌ Some comprehensive tests failed!")
    print("=" * 60)
    
    return success

if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result else 1)