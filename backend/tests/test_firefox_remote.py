#!/usr/bin/env python3
"""
Test Firefox remote debugging connection with proper startup and polling.
"""
import asyncio
import aiohttp
import subprocess
import time
import sys
import os
import signal

FIREFOX_BIN = "firefox"
PORT = 9222

async def check_firefox_remote(session, max_attempts=30, delay=0.5):
    """Poll remote debugging endpoint until it responds."""
    url = f'http://localhost:{PORT}/json/list'
    for i in range(max_attempts):
        try:
            async with session.get(url, timeout=2) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    print(f"Remote debugging ready, {len(data)} targets")
                    return data
                else:
                    print(f"HTTP {resp.status}, retrying...")
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if i == 0:
                print(f"Waiting for Firefox remote debugging...")
            pass
        await asyncio.sleep(delay)
    return None

async def test():
    # Kill any existing firefox with same port
    subprocess.run(["pkill", "-f", f"--remote-debugging-port {PORT}"], 
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await asyncio.sleep(1)
    
    # Start Firefox with remote debugging
    profile_dir = "/tmp/firefox-remote-test"
    if os.path.exists(profile_dir):
        import shutil
        shutil.rmtree(profile_dir)
    os.makedirs(profile_dir, exist_ok=True)
    
    cmd = [
        FIREFOX_BIN,
        "--remote-debugging-port", str(PORT),
        "--marionette",
        "--profile", profile_dir,
        "--new-instance",
        "--headless",
        "about:blank"
    ]
    print(f"Starting Firefox: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    try:
        async with aiohttp.ClientSession() as session:
            targets = await check_firefox_remote(session)
            if not targets:
                print("ERROR: Firefox remote debugging not available after waiting")
                proc.terminate()
                return
            
            # Connect with Playwright
            from playwright.async_api import async_playwright
            playwright = await async_playwright().start()
            try:
                ws_url = targets[0]['webSocketDebuggerUrl']
                print(f"Connecting to WebSocket: {ws_url}")
                browser = await playwright.firefox.connect(ws_url)
                print("SUCCESS: Connected to Firefox via Playwright")
                
                # Create a page and do something
                contexts = browser.contexts
                if contexts:
                    context = contexts[0]
                    pages = context.pages
                    if pages:
                        page = pages[0]
                    else:
                        page = await context.new_page()
                else:
                    context = await browser.new_context()
                    page = await context.new_page()
                
                await page.goto('https://example.com')
                title = await page.title()
                print(f"Page title: {title}")
                
                # Close browser (disconnect)
                await browser.close()
                print("Browser disconnected")
            finally:
                await playwright.stop()
    finally:
        proc.terminate()
        proc.wait()
        print("Firefox terminated")

if __name__ == '__main__':
    asyncio.run(test())