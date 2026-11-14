#!/usr/bin/env python3
"""
Quick test to see if browser opens with orchestrator after profile deletion.
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
    
    print("\nTesting command: 'abre youtube'")
    response = await orchestrator.handle_input("abre youtube", speak=False)
    print(f"Response: {response}")
    
    # Wait a bit to see if browser stays open
    await asyncio.sleep(3)
    
    # Clean up
    print("\nCleaning up...")
    # The orchestrator doesn't have a close method, but browser will be closed automatically
    # when the program ends

if __name__ == "__main__":
    asyncio.run(test())