#!/usr/bin/env python3
"""
Test that async refactoring works without event loop conflicts.
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.orchestrator import Orchestrator

async def main():
    print("Creating Orchestrator...")
    orchestrator = Orchestrator(cloud_provider="auto")
    print("Orchestrator created.")
    
    # Test a cache hit (should be fast and not require external services)
    print("Testing cache hit...")
    response = await orchestrator.handle_input("hola", speak=False)
    print(f"Response: {response}")
    assert "Hola Bruno" in response
    
    # Test a simple automation intent (mocked? but we can skip)
    # print("Testing automation intent...")
    # analysis = {"intent": "automation", "action": "open", "target": "firefox"}
    # response = await orchestrator.automation.execute(analysis)
    # print(f"Automation response: {response}")
    
    print("All async calls completed successfully.")
    print("No event loop conflicts detected.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
        print("\n✅ SUCCESS: Async refactoring appears to work.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)