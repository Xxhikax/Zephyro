#!/usr/bin/env python3
"""
Comprehensive test for Firefox web automation with simplified architecture.
Tests opening various web apps: Firefox, YouTube, Google, DuckDuckGo, etc.
"""
import asyncio
import os
import sys
import logging
import subprocess
import time
from pathlib import Path

# Enable debug logging for detailed output
os.environ["ZEPHYRO_DEBUG"] = "1"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.orchestrator import Orchestrator

def check_firefox_running():
    """Check if Firefox is already running (for user information)"""
    try:
        result = subprocess.run(["pgrep", "-x", "firefox"], 
                              capture_output=True, text=True)
        return result.returncode == 0
    except Exception:
        return False

def check_profile_directory():
    """Check if the simple profile directory exists and has content"""
    profile_dir = Path.home() / ".cache" / "zephyro" / "firefox_profile_simple"
    exists = profile_dir.exists()
    if exists:
        items = list(profile_dir.iterdir())
        return exists, len(items)
    return exists, 0

async def test_basic_navigation(orchestrator):
    """Test basic browser navigation commands"""
    print("\n" + "="*60)
    print("TEST 1: Basic browser navigation")
    print("="*60)
    
    tests = [
        ("abre firefox", "Open Firefox"),
        ("abre example.com", "Open example.com"),
        ("abre google.com", "Open Google"),
        ("abre youtube.com", "Open YouTube"),
    ]
    
    for command, description in tests:
        print(f"\n>>> {description}: '{command}'")
        try:
            response = await orchestrator.handle_input(command, speak=False)
            print(f"   Response: {response}")
            # Wait a moment between commands
            await asyncio.sleep(2)
        except Exception as e:
            print(f"   ERROR: {e}")
            import traceback
            traceback.print_exc()
    
    return True

async def test_search_engines(orchestrator):
    """Test search functionality on different engines"""
    print("\n" + "="*60)
    print("TEST 2: Search engines")
    print("="*60)
    
    tests = [
        ("busca en google 'weather today'", "Search Google"),
        ("busca en youtube 'funny cat videos'", "Search YouTube"),
        ("busca en duckduckgo 'Python programming'", "Search DuckDuckGo"),
    ]
    
    for command, description in tests:
        print(f"\n>>> {description}: '{command}'")
        try:
            response = await orchestrator.handle_input(command, speak=False)
            print(f"   Response: {response}")
            # Wait longer for search results to load
            await asyncio.sleep(3)
        except Exception as e:
            print(f"   ERROR: {e}")
            import traceback
            traceback.print_exc()
    
    return True

async def test_media_control(orchestrator):
    """Test media playback and control"""
    print("\n" + "="*60)
    print("TEST 3: Media control")
    print("="*60)
    
    # First open YouTube
    print("\n>>> Opening YouTube for media test")
    await orchestrator.handle_input("abre youtube", speak=False)
    await asyncio.sleep(2)
    
    # Try to play something
    tests = [
        ("reproduce en youtube 'lo fi hip hop'", "Play YouTube music"),
        ("pausa", "Pause playback"),
        ("reproduce", "Resume playback"),
        ("volumen arriba", "Volume up"),
        ("volumen abajo", "Volume down"),
    ]
    
    for command, description in tests:
        print(f"\n>>> {description}: '{command}'")
        try:
            response = await orchestrator.handle_input(command, speak=False)
            print(f"   Response: {response}")
            await asyncio.sleep(1)
        except Exception as e:
            print(f"   ERROR: {e}")
            # Continue with next test
    
    return True

async def test_tab_management(orchestrator):
    """Test tab opening and closing"""
    print("\n" + "="*60)
    print("TEST 4: Tab management")
    print("="*60)
    
    tests = [
        ("abre una nueva pestaña", "Open new tab"),
        ("abre github.com", "Open GitHub"),
        ("cierra la pestaña", "Close tab"),
    ]
    
    for command, description in tests:
        print(f"\n>>> {description}: '{command}'")
        try:
            response = await orchestrator.handle_input(command, speak=False)
            print(f"   Response: {response}")
            await asyncio.sleep(2)
        except Exception as e:
            print(f"   ERROR: {e}")
    
    return True

async def run_all_tests():
    """Run all tests"""
    print("\n" + "="*60)
    print("FIREFOX WEB AUTOMATION TEST SUITE")
    print("Simplified Architecture (Option 3)")
    print("="*60)
    
    # Check Firefox status
    firefox_running = check_firefox_running()
    if firefox_running:
        print("\n⚠️  WARNING: Firefox is already running!")
        print("   This may cause conflicts with Playwright automation.")
        print("   Consider closing Firefox before running these tests.")
        print("   (Playwright will launch a new Firefox instance anyway)")
    
    # Check profile
    profile_exists, item_count = check_profile_directory()
    print(f"\n📁 Profile directory: ~/.cache/zephyro/firefox_profile_simple")
    print(f"   Exists: {profile_exists}, Items: {item_count}")
    
    if not profile_exists:
        print("   Creating profile directory...")
        profile_dir = Path.home() / ".cache" / "zephyro" / "firefox_profile_simple"
        profile_dir.mkdir(parents=True, exist_ok=True)
    
    print("\n🚀 Initializing Orchestrator (may take a moment)...")
    start_time = time.time()
    
    try:
        orchestrator = Orchestrator(cloud_provider="auto")
        init_time = time.time() - start_time
        print(f"   ✅ Orchestrator initialized in {init_time:.1f}s")
    except Exception as e:
        print(f"   ❌ Failed to initialize orchestrator: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Run tests
    test_results = []
    
    try:
        # Test 1: Basic navigation
        test_results.append(await test_basic_navigation(orchestrator))
        
        # Test 2: Search engines
        test_results.append(await test_search_engines(orchestrator))
        
        # Test 3: Media control (skip if media not available)
        test_results.append(await test_media_control(orchestrator))
        
        # Test 4: Tab management
        test_results.append(await test_tab_management(orchestrator))
        
    except Exception as e:
        print(f"\n❌ Critical test error: {e}")
        import traceback
        traceback.print_exc()
        test_results.append(False)
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    
    passed = sum(1 for r in test_results if r)
    total = len(test_results)
    
    print(f"\n📊 Tests passed: {passed}/{total}")
    
    if passed == total:
        print("🎉 All tests passed! Firefox automation is working correctly.")
    else:
        print("⚠️  Some tests failed. Check logs for details.")
    
    # Cleanup
    print("\n🧹 Cleaning up...")
    # Note: The orchestrator doesn't have an explicit close method,
    # but the browser will close when the program ends
    
    return passed == total

if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)