#!/usr/bin/env python3
# Standalone manual script — NOT a pytest test. `test_command(command, ...)`
# takes positional args that pytest misreads as fixtures (collection error), and
# it instantiates a full Orchestrator. Run via `python tests/test_orchestrator_command.py`.
__test__ = False
import sys
sys.path.insert(0, '.')

import logging
logging.basicConfig(level=logging.WARNING)  # Suppress info logs
import asyncio
from core.orchestrator import Orchestrator

async def test_command(command, expected_substring=None):
    print(f"\n=== Testing: '{command}' ===")
    orchestrator = Orchestrator(cloud_provider="auto")
    orchestrator.user_role = "admin"
    orchestrator.authenticated = True
    orchestrator.user_name = "Bruno"
    
    try:
        response = await orchestrator.handle_input(command, speak=False)
        print(f"Response: {response}")
        if expected_substring and expected_substring.lower() not in response.lower():
            print(f"ERROR: Expected substring '{expected_substring}' not found in response")
            return False
        return True
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    commands = [
        ("abre instagram", None),
        ("abre facebook", None),
        ("open firefox", None),
        ("open firefox y busca youtube", None),
        ("open firefox y busca gatos", None),
    ]
    
    results = []
    for cmd, substr in commands:
        success = await test_command(cmd, substr)
        results.append(success)
        # Small delay between commands
        await asyncio.sleep(2)
    
    print(f"\n=== Summary: {sum(results)}/{len(results)} passed ===")
    return all(results)

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)