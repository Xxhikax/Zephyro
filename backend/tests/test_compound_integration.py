#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')

import logging
logging.basicConfig(level=logging.INFO)
import asyncio
from modules.automation.app_controller import AppController
from core.browser_config import BrowserConfig

async def test_compound_navigate():
    print("Testing compound command: open firefox y busca youtube")
    ac = AppController(config=BrowserConfig(headless=True))
    
    # Simulate analysis from router
    analysis = {
        "intent": "automation",
        "automation_intent": "NAVIGATE",
        "provider": "generic",
        "action": "open",
        "target": "firefox",
        "navigate_target": "youtube",
        "complexity": "simple"
    }
    
    try:
        result = await ac.execute(analysis)
        print(f"Result: {result}")
        return True
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_compound_search():
    print("Testing compound command: open firefox y busca gatos")
    ac = AppController(config=BrowserConfig(headless=True))
    
    analysis = {
        "intent": "automation",
        "automation_intent": "SEARCH",
        "provider": "generic",
        "action": "open",
        "target": "firefox",
        "search_query": "gatos",
        "complexity": "simple"
    }
    
    try:
        result = await ac.execute(analysis)
        print(f"Result: {result}")
        return True
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    success1 = await test_compound_navigate()
    success2 = await test_compound_search()
    return success1 and success2

if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)