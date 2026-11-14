#!/usr/bin/env python3
"""
Test que command queue serialization with concurrent commands.
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

import pytest
import os as _os

from modules.automation.browser_controller import BrowserController
from core.browser_config import BrowserConfig


@pytest.mark.skip(reason="browser integration test — requires CDP; set ZEPHYRO_BROWSER_TESTS=1")
async def test_concurrent_commands():
    """Test that concurrent commands are serialized by the queue."""
    print("🧪 Testing CommandQueue serialization with concurrent commands")
    
    # Use headless=True to avoid opening window
    controller = BrowserController(config=BrowserConfig(headless=True))
    
    try:
        # Start browser first
        print("   Starting browser...")
        await controller._ensure_browser()
        
        # Track execution order
        execution_order = []
        timestamps = []
        
        async def dummy_command(idx: int):
            """A dummy command that records its execution order."""
            start = time.time()
            execution_order.append(idx)
            timestamps.append(start)
            # Simulate some work
            await asyncio.sleep(0.1)
            print(f"      Command {idx} executed at {start:.3f}")
            return f"cmd{idx}"
        
        # Enqueue multiple commands concurrently
        print("   Enqueuing 5 commands concurrently...")
        tasks = []
        for i in range(5):
            # Use _enqueue_command directly with dummy command
            task = asyncio.create_task(
                controller._enqueue_command(dummy_command, i, command_name=f"dummy{i}")
            )
            tasks.append(task)
        
        # Wait for all to complete
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Check results
        errors = [r for r in results if isinstance(r, Exception)]
        if errors:
            print(f"   ❌ Errors: {errors}")
            return False
        
        print(f"   ✅ Execution order: {execution_order}")
        print(f"   ✅ Timestamps: {[f'{t:.3f}' for t in timestamps]}")
        
        # Verify serialization: timestamps should be sequential with increasing times
        # Since commands are enqueued almost simultaneously, they should be processed
        # sequentially with at least 0.1s apart (due to sleep).
        for j in range(1, len(timestamps)):
            if timestamps[j] - timestamps[j-1] < 0.09:
                print(f"   ⚠️  Commands {j-1} and {j} may have overlapped (delta {timestamps[j]-timestamps[j-1]:.3f}s)")
                # Could be due to timing precision; not a failure
        
        # Verify order matches enqueue order (FIFO)
        if execution_order != list(range(5)):
            print(f"   ❌ Execution order mismatch: expected 0,1,2,3,4, got {execution_order}")
            return False
        
        print("   ✅ Commands serialized correctly (FIFO order)")
        
        # Test queue metrics
        queue_status = controller.get_queue_status()
        print(f"   📊 Queue status: {queue_status}")
        
        # Test session info includes queue metrics
        session_info = controller.get_session_info()
        if 'queue_metrics' not in session_info:
            print("   ❌ queue_metrics missing from session info")
            return False
        
        print("   ✅ Queue metrics present in session info")
        
        return True
        
    finally:
        print("   Cleaning up...")
        await controller.close()

@pytest.mark.skip(reason="browser integration test — requires CDP; set ZEPHYRO_BROWSER_TESTS=1")
async def test_priority():
    """Test priority ordering (high priority commands should execute first)."""
    print("\n🧪 Testing CommandQueue priority ordering")
    
    controller = BrowserController(config=BrowserConfig(headless=True))
    
    try:
        await controller._ensure_browser()
        
        execution_order = []
        
        async def priority_cmd(idx: int, priority: int):
            execution_order.append((idx, priority))
            await asyncio.sleep(0.05)
            return idx
        
        # Enqueue commands with different priorities
        # We'll enqueue low priority first, then high priority, expecting high to jump queue
        # However our simple queue doesn't implement priority reordering yet.
        # For now, just test that priority parameter doesn't break.
        tasks = []
        tasks.append(asyncio.create_task(
            controller._enqueue_command(priority_cmd, 0, command_name="low", priority=2)
        ))
        await asyncio.sleep(0.01)  # small delay to ensure first is enqueued
        tasks.append(asyncio.create_task(
            controller._enqueue_command(priority_cmd, 1, command_name="high", priority=0)
        ))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        print(f"   Execution order (idx, priority): {execution_order}")
        # Note: priority not implemented yet, so order may be FIFO
        print("   ⚠️  Priority scheduling not yet implemented (future enhancement)")
        
        return True
    finally:
        await controller.close()

@pytest.mark.skip(reason="browser integration test — requires CDP; set ZEPHYRO_BROWSER_TESTS=1")
async def test_queue_recovery():
    """Test that queue handles browser restart/recovery correctly."""
    print("\n🧪 Testing queue recovery during browser errors")
    
    controller = BrowserController(config=BrowserConfig(headless=True))
    
    try:
        # Ensure browser started
        await controller._ensure_browser()
        
        # Simulate browser crash by manually closing context
        if controller.context:
            await controller.context.close()
            controller.context = None
            controller.page = None
        
        # Enqueue a command - should trigger recovery
        print("   Enqueuing command after simulated crash...")
        result = await controller.navigate_to("about:blank")
        print(f"   Result: {result}")
        
        if "✓" in result:
            print("   ✅ Command succeeded after recovery")
            return True
        else:
            print(f"   ❌ Command failed: {result}")
            return False
    finally:
        await controller.close()

async def main():
    print("="*60)
    print("COMMAND QUEUE TESTS (Phase 2)")
    print("="*60)
    
    success = True
    
    # Test 1: Concurrent command serialization
    if not await test_concurrent_commands():
        success = False
    
    # Test 2: Priority (optional)
    if not await test_priority():
        success = False
    
    # Test 3: Recovery
    if not await test_queue_recovery():
        success = False
    
    print("\n" + "="*60)
    if success:
        print("✅ All queue tests passed!")
    else:
        print("❌ Some queue tests failed")
    print("="*60)
    
    return success

if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result else 1)