#!/usr/bin/env python3
"""
Test orchestrator with browser automation.
"""
import asyncio
import os
import sys
import logging

# Enable debug logging
os.environ["ZEPHYRO_DEBUG"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.orchestrator import Orchestrator

async def main():
    print("=== Testing orchestrator with browser command ===")
    print("Initializing orchestrator...")
    orchestrator = Orchestrator(cloud_provider="auto")
    orchestrator.user_role = "admin"
    orchestrator.authenticated = True
    orchestrator.user_name = "Bruno"
    
    command = "abre youtube y reproduce gold in the ceiling"
    print(f"\nCommand: {command}")
    
    try:
        result = await orchestrator.handle_input(command, speak=False)
        print(f"\nResult: {result}")
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    print("\n=== Test complete ===")
    return True

if __name__ == "__main__":
    try:
        success = asyncio.run(main())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"Unhandled error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)