#!/usr/bin/env python3
"""
Test the full chain: router -> planner -> app_controller -> browser_controller.
"""
import asyncio
import os
import sys
import logging

os.environ["ZEPHYRO_DEBUG"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.llm_manager import LLMManager
from core.context_manager import ContextManager
from core.router import IntentRouter
from modules.automation.app_controller import AppController
from core.browser_config import BrowserConfig

async def test_full_chain():
    print("=== Testing full chain for 'abre youtube y reproduce gold in the ceiling' ===")
    
    # 1. Router analysis
    print("\n1. Router analysis...")
    llm_fast = LLMManager(local_model="llama3.2:1b", preset="rapido")
    context = ContextManager()
    router = IntentRouter(llm_fast, context)
    
    command = "abre youtube y reproduce gold in the ceiling"
    analysis = await router.classify_and_parse(command)
    print(f"   Command: {command}")
    print(f"   Analysis: {analysis}")
    
    # Check if analysis is correct
    if analysis.get('automation_intent') != 'PLAY_MEDIA':
        print(f"   ERROR: Expected PLAY_MEDIA, got {analysis.get('automation_intent')}")
        return False
    if analysis.get('provider') != 'youtube':
        print(f"   ERROR: Expected provider youtube, got {analysis.get('provider')}")
        return False
    
    print("   ✓ Router analysis correct")
    
    # 2. Planner test
    print("\n2. Planner test...")
    from modules.automation.planner import WebAutomationPlanner
    planner = WebAutomationPlanner()
    plan = planner.build_plan(analysis)
    print(f"   Generated plan ({len(plan)} steps):")
    for i, step in enumerate(plan):
        print(f"     {i+1}. {step}")
    
    # Check plan structure
    if len(plan) < 3:
        print(f"   ERROR: Plan too short, expected at least 3 steps")
        return False
    
    # Check first step is navigate to youtube
    if plan[0].get('type') != 'navigate':
        print(f"   ERROR: First step should be 'navigate', got {plan[0].get('type')}")
        return False
    
    print("   ✓ Plan generated correctly")
    
    # 3. AppController execution (partial - just first few steps)
    print("\n3. Testing AppController execution (first 2 steps)...")
    app_controller = AppController(config=BrowserConfig(headless=False))
    
    try:
        # Execute first step (navigate)
        print(f"   Executing step 1: {plan[0]}")
        result1 = await app_controller._execute_atomic_action(plan[0])
        print(f"   Result: {result1}")
        
        if not result1.startswith("✓"):
            print(f"   ERROR: First step failed")
            return False
        
        # Wait for page to load
        await asyncio.sleep(2)
        
        # Execute second step (search)
        print(f"   Executing step 2: {plan[1]}")
        result2 = await app_controller._execute_atomic_action(plan[1])
        print(f"   Result: {result2}")
        
        if not result2.startswith("✓"):
            print(f"   ERROR: Second step failed")
            return False
        
        print("   ✓ First 2 steps executed successfully")
        
        # Wait for search results
        await asyncio.sleep(3)
        
        # Check what engine was used in search
        step2_params = plan[1].get('params', {})
        engine = step2_params.get('engine', 'unknown')
        print(f"   Search engine used: {engine}")
        
        if engine != 'youtube':
            print(f"   WARNING: Search engine should be 'youtube', got '{engine}'")
            print(f"   This might cause DuckDuckGo to open instead of YouTube search")
        
    except Exception as e:
        print(f"   ERROR in execution: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    finally:
        print("\n4. Cleaning up...")
        await app_controller.cleanup()
    
    print("\n=== TEST COMPLETE ===")
    return True

async def test_why_duckduckgo():
    """Test specifically why DuckDuckGo might be opening"""
    print("\n=== Investigating DuckDuckGo issue ===")
    
    from modules.automation.planner import WebAutomationPlanner
    from modules.automation.browser_controller import BrowserController
    
    # Test different scenarios
    test_cases = [
        {
            "name": "PLAY_MEDIA with youtube provider",
            "analysis": {
                "intent": "automation",
                "automation_intent": "PLAY_MEDIA",
                "provider": "youtube",
                "action": "open",
                "target": "firefox",
                "search_query": "gold in the ceiling",
                "complexity": "simple"
            }
        },
        {
            "name": "NAVIGATE to youtube.com",
            "analysis": {
                "intent": "automation",
                "automation_intent": "NAVIGATE",
                "provider": "generic",
                "action": "open",
                "target": "youtube.com",
                "complexity": "simple"
            }
        },
        {
            "name": "SEARCH with generic provider",
            "analysis": {
                "intent": "automation",
                "automation_intent": "SEARCH",
                "provider": "generic",
                "action": "open",
                "target": "gold in the ceiling",
                "search_query": "gold in the ceiling",
                "complexity": "simple"
            }
        }
    ]
    
    planner = WebAutomationPlanner()
    
    for test_case in test_cases:
        print(f"\nTest: {test_case['name']}")
        print(f"Analysis: {test_case['analysis']}")
        
        plan = planner.build_plan(test_case['analysis'])
        print(f"Plan ({len(plan)} steps):")
        for i, step in enumerate(plan):
            print(f"  {i+1}. {step}")
        
        # Check if any step would open DuckDuckGo
        for i, step in enumerate(plan):
            if step.get('type') == 'search':
                engine = step.get('params', {}).get('engine', '')
                if engine == 'duckduckgo':
                    print(f"  ⚠️ Step {i+1} would open DuckDuckGo!")
                elif engine == 'youtube':
                    print(f"  ✓ Step {i+1} would search on YouTube")
        
        print("-" * 50)
    
    return True

async def main():
    print("Testing full execution chain...")
    
    # Run the DuckDuckGo investigation first
    await test_why_duckduckgo()
    
    # Run full chain test
    success = await test_full_chain()
    
    print(f"\n=== FINAL RESULT ===")
    print(f"Full chain test: {'PASS' if success else 'FAIL'}")
    
    return success

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