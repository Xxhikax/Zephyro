#!/usr/bin/env python3
"""
Test YouTube playback command.
"""
import asyncio
import os
import sys
import logging

# Enable debug logging
os.environ["ZEPHYRO_DEBUG"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.orchestrator import Orchestrator

async def test():
    print("Initializing orchestrator...")
    orchestrator = Orchestrator(cloud_provider="auto")
    
    print("\nTesting command: 'abre youtube y reproduce gold in the ceiling'")
    # We'll use handle_input with speak=False
    response = await orchestrator.handle_input("abre youtube y reproduce gold in the ceiling", speak=False)
    print(f"Response: {response}")
    
    # Wait a bit to see if browser stays open
    await asyncio.sleep(5)
    
    print("\nTest complete.")

if __name__ == "__main__":
    asyncio.run(test())