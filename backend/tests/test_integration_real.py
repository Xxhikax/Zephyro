#!/usr/bin/env python3
"""
Integration test for enhanced Playwright web automation with real browser.

NOT a pytest test — standalone manual script. `test_browser_execution` opens a
REAL browser via AppController(BrowserConfig(headless=True)), which on Windows
attaches to (and can kill) the user's real Edge session. Its functions also take
positional args that pytest misreads as fixtures. Run via
`python tests/test_integration_real.py` (it prompts before touching the browser).
"""
__test__ = False
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from unittest.mock import MagicMock
from core.router import IntentRouter
from modules.automation.app_controller import AppController

class MockContextManager:
    """Mock context manager that provides last_automation."""
    def __init__(self):
        self.last_automation = None
        self.history = []
    
    def get_last_automation(self):
        return self.last_automation
    
    def set_last_automation(self, analysis):
        self.last_automation = analysis
    
    def add(self, role, content, skip_save=False):
        pass
    
    def clear_history(self):
        pass
    
    def add_latency(self, intent, complexity, latency):
        pass
    
    def get_average_latency(self, intent, complexity):
        return 0.5

async def test_intent_parsing():
    """Test that IntentRouter correctly extracts automation_intent and provider using heuristic path."""
    mock_llm = MagicMock()
    mock_llm.generate = MagicMock(return_value='{"intent": "automation", "action": "open", "target": "firefox"}')
    mock_ctx = MockContextManager()
    # Simulate previous browser opened
    mock_ctx.set_last_automation({
        "analysis": {"intent": "automation", "action": "open", "target": "firefox"}
    })
    
    router = IntentRouter(mock_llm, context_manager=mock_ctx)
    
    # Test case 1: PLAY_MEDIA with YouTube
    user_input = "pon Never Gonna Give You Up en YouTube"
    parsed = await router.classify_and_parse(user_input)
    print(f"\nInput: {user_input}")
    print(f"Parsed: {parsed}")
    assert parsed.get('intent') == 'automation'
    assert parsed.get('automation_intent') == 'PLAY_MEDIA'
    assert parsed.get('provider') == 'youtube'
    assert parsed.get('search_query') == 'Never Gonna Give You Up en YouTube'
    assert not mock_llm.generate.called, "LLM should not be called for heuristic path"
    print("✅ PLAY_MEDIA intent parsed correctly")
    
    # Test case 2: SEARCH generic
    user_input = "busca tutorial de Python"
    parsed = await router.classify_and_parse(user_input)
    print(f"\nInput: {user_input}")
    print(f"Parsed: {parsed}")
    assert parsed.get('intent') == 'automation'
    assert parsed.get('automation_intent') == 'SEARCH'
    assert parsed.get('provider') == 'generic'
    assert parsed.get('search_query') == 'tutorial de Python'
    print("✅ SEARCH intent parsed correctly")
    
    # Test case 3: CLOSE tab (requires previous browser context)
    user_input = "cierra la pestaña"
    parsed = await router.classify_and_parse(user_input)
    print(f"\nInput: {user_input}")
    print(f"Parsed: {parsed}")
    assert parsed.get('intent') == 'automation'
    assert parsed.get('automation_intent') == 'INTERACT'
    assert parsed.get('action') == 'close_tab'
    print("✅ CLOSE intent parsed correctly")
    
    return parsed  # return last parsed for execution test

async def test_browser_execution(parsed_command):
    """Test AppController execution with a real headless browser."""
    print("\n--- Starting browser execution test ---")
    print(f"Command to execute: {parsed_command}")
    
    from core.browser_config import BrowserConfig
    app_controller = AppController(config=BrowserConfig(headless=True))
    
    try:
        # Execute with timeout of 45 seconds (allows page load, search, and play attempt)
        result = await asyncio.wait_for(app_controller.execute(parsed_command), timeout=45.0)
        print(f"Execution result: {result}")
        # Expect a success message containing certain keywords
        success_indicators = ['reproducido', 'encontrado', 'éxito', 'listo', 'reproduciendo']
        if any(indicator in result.lower() for indicator in success_indicators):
            print("✅ Browser execution succeeded with positive result.")
        else:
            print("⚠️  Browser execution completed but result may not indicate success.")
    except asyncio.TimeoutError:
        print("⚠️  Execution timed out (maybe network slow).")
    except Exception as e:
        print(f"❌ Browser execution failed: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        await app_controller.cleanup()
        print("Browser cleaned up.")

async def main():
    print("=== Integration Test for Enhanced Playwright Automation ===")
    print("Testing intent parsing...")
    parsed = await test_intent_parsing()
    
    # Uncomment the following line to run real browser test (requires Firefox installed)
    print("\nWarning: Real browser test will open headless Firefox and interact with YouTube.")
    response = input("Do you want to run the real browser test? (y/N): ").strip().lower()
    if response == 'y':
        await test_browser_execution(parsed)
    else:
        print("Skipping real browser test.")
    
    print("\n=== Integration test completed ===")

if __name__ == "__main__":
    asyncio.run(main())