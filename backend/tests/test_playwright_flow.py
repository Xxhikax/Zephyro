#!/usr/bin/env python3
"""
Test the exact Playwright flow for YouTube playback.
"""
import asyncio
import os
import sys
import logging

os.environ["ZEPHYRO_DEBUG"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.automation.browser_controller import BrowserController
from modules.automation.planner import WebAutomationPlanner

async def test_youtube_playback():
    print("=== Testing YouTube playback flow ===")
    
    # Create browser without persistent profile first
    print("\n1. Creating BrowserController...")
    bc = BrowserController(headless=False, persistent_user_data_dir=None)
    
    print("\n2. Testing navigation to YouTube...")
    try:
        result = await bc.navigate_to("https://www.youtube.com")
        print(f"   Result: {result}")
        print(f"   Current URL: {bc.page.url if bc.page else 'No page'}")
        
        # Wait for page to load completely
        await asyncio.sleep(2)
        
        print("\n3. Testing search box finding...")
        try:
            search_box = await bc._find_element("youtube", "search_box")
            print(f"   Search box found: {search_box}")
            
            print("\n4. Testing search...")
            await search_box.fill("gold in the ceiling")
            await search_box.press("Enter")
            print("   Search executed")
            
            await asyncio.sleep(3)
            
            print("\n5. Testing video item finding...")
            video_item = await bc._find_element("youtube", "video_item")
            print(f"   Video item found: {video_item}")
            
            print("\n6. Clicking video...")
            await video_item.click()
            print("   Video clicked")
            
            await asyncio.sleep(3)
            
            print("\n7. Testing player container...")
            player = await bc._find_element("youtube", "player_container")
            print(f"   Player container found: {player}")
            
            print("\n8. Testing play control...")
            result = await bc.control_media("play")
            print(f"   Play control: {result}")
            
        except Exception as e:
            print(f"   ERROR in element finding: {e}")
            import traceback
            traceback.print_exc()
            
    except Exception as e:
        print(f"   ERROR in navigation: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n9. Closing browser...")
    await bc.close()
    
    return True

async def test_planner_analysis():
    print("\n=== Testing planner analysis ===")
    
    planner = WebAutomationPlanner()
    
    # Simulate router analysis for "abre youtube y reproduce gold in the ceiling"
    analysis = {
        "intent": "automation",
        "automation_intent": "PLAY_MEDIA",
        "provider": "youtube",
        "action": "open",
        "target": "firefox",
        "search_query": "gold in the ceiling",
        "complexity": "simple"
    }
    
    print(f"Input analysis: {analysis}")
    
    plan = planner.build_plan(analysis)
    print(f"\nGenerated plan ({len(plan)} steps):")
    for i, step in enumerate(plan):
        print(f"  {i+1}. {step}")
    
    return plan

async def main():
    print("Testing Playwright YouTube flow...")
    
    plan = await test_planner_analysis()
    
    if plan:
        success = await test_youtube_playback()
        print(f"\n=== SUMMARY ===")
        print(f"Plan generation: SUCCESS ({len(plan)} steps)")
        print(f"Playwright flow: {'SUCCESS' if success else 'FAILED'}")
        return success
    else:
        print("Plan generation failed")
        return False

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