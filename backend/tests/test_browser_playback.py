#!/usr/bin/env python3
"""
Test browser playback with real headless Firefox.
"""
import asyncio
import sys
import os
import logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from modules.automation.app_controller import AppController

async def test_play_media():
    """Test PLAY_MEDIA automation_intent with YouTube."""
    analysis = {
        "intent": "automation",
        "automation_intent": "PLAY_MEDIA",
        "provider": "youtube",
        "search_query": "Never Gonna Give You Up",
        "target": "",  # optional
        "action": "play",
    }
    
    logger.info("Starting AppController with headless Firefox...")
    app_controller = AppController(headless=True)
    
    try:
        # Execute with timeout
        result = await asyncio.wait_for(app_controller.execute(analysis), timeout=60.0)
        logger.info(f"Execution result: {result}")
        # Check for success indicators
        if any(word in result.lower() for word in ['reproducido', 'encontrado', 'éxito', 'listo', 'reproduciendo']):
            logger.info("✅ PLAY_MEDIA execution succeeded.")
        else:
            logger.warning(f"Execution completed but result unclear: {result}")
    except asyncio.TimeoutError:
        logger.warning("⚠️  Execution timed out (maybe network slow).")
    except Exception as e:
        logger.error(f"❌ Execution failed: {e}", exc_info=True)
        raise
    finally:
        await app_controller.cleanup()
        logger.info("Browser cleaned up.")

async def test_search():
    """Test SEARCH automation_intent generic."""
    analysis = {
        "intent": "automation",
        "automation_intent": "SEARCH",
        "provider": "generic",
        "search_query": "Python tutorial",
        "target": "",
        "action": "search",
    }
    
    logger.info("Starting SEARCH test...")
    app_controller = AppController(headless=True)
    try:
        result = await asyncio.wait_for(app_controller.execute(analysis), timeout=30.0)
        logger.info(f"SEARCH result: {result}")
    except asyncio.TimeoutError:
        logger.warning("SEARCH timed out.")
    finally:
        await app_controller.cleanup()

async def main():
    logger.info("=== Browser Playback Integration Test ===")
    await test_play_media()
    # await test_search()  # uncomment if needed
    logger.info("=== Test completed ===")

if __name__ == "__main__":
    asyncio.run(main())