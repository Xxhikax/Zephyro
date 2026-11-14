#!/usr/bin/env python3
"""
Test browser failure with orchestrator.
"""
import asyncio
import os
import sys
import logging

# Enable debug logging
os.environ["ZEPHYRO_DEBUG"] = "1"
logging.basicConfig(level=logging.DEBUG)

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.orchestrator import Orchestrator

async def main():
    print("=== TEST BROWSER FAILURE ===")
    print("Command: 'abre youtube'")
    
    # Initialize orchestrator (headless=False already set)
    orchestrator = Orchestrator(cloud_provider="auto")
    orchestrator.user_role = "admin"
    orchestrator.authenticated = True
    orchestrator.user_name = "Bruno"
    
    try:
        result = await orchestrator.handle_input(
            user_input="abre youtube",
            speak=False
        )
        print(f"\nResult: {result}")
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    print("\n=== TEST COMPLETE ===")

if __name__ == "__main__":
    asyncio.run(main())