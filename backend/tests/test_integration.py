#!/usr/bin/env python3
"""
Integration test for browser automation with orchestrator.
Simulates the command "abre youtube y reproduce gold in the ceiling".
"""
import asyncio
import os
import sys
import logging

# Enable debug logging
os.environ["ZEPHYRO_DEBUG"] = "1"

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.orchestrator import Orchestrator

async def main():
    print("=== INTEGRATION TEST: Browser Automation ===")
    print("Command: 'abre youtube y reproduce gold in the ceiling'")
    
    # Initialize orchestrator (headless=False already set)
    orchestrator = Orchestrator(cloud_provider="auto")
    orchestrator.user_role = "admin"
    orchestrator.authenticated = True
    orchestrator.user_name = "Bruno"
    
    try:
        result = await orchestrator.handle_input(
            user_input="abre youtube y reproduce gold in the ceiling",
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