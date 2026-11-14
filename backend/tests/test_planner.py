#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')

from modules.automation.planner import WebAutomationPlanner

def test_planner():
    planner = WebAutomationPlanner()
    
    # Test SEARCH intent with generic query
    analysis = {
        "intent": "automation",
        "automation_intent": "SEARCH",
        "provider": "generic",
        "action": "open",
        "target": "firefox",
        "search_query": "gatos"
    }
    plan = planner.build_plan(analysis)
    print("SEARCH 'gatos' plan:")
    for step in plan:
        print(f"  {step}")
    print()
    
    # Test NAVIGATE with browser and navigate_target
    analysis2 = {
        "intent": "automation",
        "automation_intent": "NAVIGATE",
        "provider": "generic",
        "action": "open",
        "target": "firefox",
        "navigate_target": "youtube"
    }
    plan2 = planner.build_plan(analysis2)
    print("NAVIGATE to youtube via firefox plan:")
    for step in plan2:
        print(f"  {step}")
    print()
    
    # Test PLAY_MEDIA with youtube
    analysis3 = {
        "intent": "automation",
        "automation_intent": "PLAY_MEDIA",
        "provider": "youtube",
        "action": "play",
        "target": "youtube",
        "search_query": "lo-fi"
    }
    plan3 = planner.build_plan(analysis3)
    print("PLAY_MEDIA youtube 'lo-fi' plan:")
    for step in plan3:
        print(f"  {step}")
    print()
    
    # Test NAVIGATE to instagram (web app)
    analysis4 = {
        "intent": "automation",
        "automation_intent": "NAVIGATE",
        "provider": "generic",
        "action": "open",
        "target": "instagram"
    }
    plan4 = planner.build_plan(analysis4)
    print("NAVIGATE to instagram plan:")
    for step in plan4:
        print(f"  {step}")
    print()

if __name__ == "__main__":
    test_planner()