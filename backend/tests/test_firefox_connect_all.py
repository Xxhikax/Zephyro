#!/usr/bin/env python3
"""
Attempt to connect to Firefox remote agent via various WebSocket URLs.
"""
import asyncio
import aiohttp
import subprocess
import os
import signal
import json

FIREFOX_BIN = "firefox"
PORT_REMOTE = 9222
PORT_MARIONETTE = 2828

async def fetch_json(session, url):
    try:
        async with session.get(url, headers={'Accept': 'application/json'}) as resp:
            if resp.status == 200:
                return await resp.json()
            else:
                return None
    except:
        return None

async def try_connect_playwright(playwright, ws_url):
    try:
        browser = await playwright.firefox.connect(ws_url)
        print(f"  SUCCESS connected via {ws_url}")
        await browser.close()
        return True
    except Exception as e:
        print(f"  FAILED {ws_url}: {e}")
        return False

async def test():
    # Kill existing firefox with same ports
    subprocess.run(["pkill", "-f", f"--remote-debugging-port {PORT_REMOTE}"], 
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await asyncio.sleep(1)
    
    # Start Firefox with remote debugging and marionette
    profile_dir = "/tmp/firefox-connect-test"
    if os.path.exists(profile_dir):
        import shutil
        shutil.rmtree(profile_dir)
    os.makedirs(profile_dir, exist_ok=True)
    
    cmd = [
        FIREFOX_BIN,
        "--remote-debugging-port", str(PORT_REMOTE),
        "--marionette",
        "--profile", profile_dir,
        "--new-instance",
        "--headless",
        "about:blank"
    ]
    print(f"Starting Firefox: {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    try:
        await asyncio.sleep(2)  # wait for startup
        
        async with aiohttp.ClientSession() as session:
            # Try to get JSON endpoints
            print("Testing JSON endpoints...")
            for port in [PORT_REMOTE, PORT_MARIONETTE]:
                for path in ['/json/list', '/json/version', '/session', '/']:
                    url = f'http://localhost:{port}{path}'
                    data = await fetch_json(session, url)
                    if data is not None:
                        print(f"  {url} -> {json.dumps(data)[:200]}")
                    else:
                        print(f"  {url} -> no JSON")
            
            # Try to fetch raw HTTP response
            for port in [PORT_REMOTE, PORT_MARIONETTE]:
                url = f'http://localhost:{port}'
                try:
                    async with session.get(url) as resp:
                        text = await resp.text()
                        if 'httpd.js' in text:
                            print(f"  {url} -> httpd.js generic page")
                        else:
                            print(f"  {url} -> {text[:100]}")
                except:
                    pass
        
        # Now attempt Playwright connection with possible WebSocket URLs
        from playwright.async_api import async_playwright
        playwright = await async_playwright().start()
        try:
            # Try common WebSocket URLs
            candidates = [
                f'ws://localhost:{PORT_REMOTE}/',
                f'ws://localhost:{PORT_REMOTE}/session',
                f'ws://localhost:{PORT_REMOTE}/devtools/browser',
                f'ws://localhost:{PORT_MARIONETTE}/',
                f'ws://localhost:{PORT_MARIONETTE}/session',
            ]
            # Also try to get WebSocket URL from /json/list if exists (maybe different port)
            # We'll attempt to fetch from port 9222 with path /json/list again using aiohttp with plain text
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get(f'http://localhost:{PORT_REMOTE}/json/list') as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for item in data:
                                if 'webSocketDebuggerUrl' in item:
                                    candidates.append(item['webSocketDebuggerUrl'])
                except:
                    pass
            
            print(f"Trying {len(candidates)} WebSocket URLs...")
            for ws_url in candidates:
                if await try_connect_playwright(playwright, ws_url):
                    print(f"  ✓ Connected using {ws_url}")
                    break
            else:
                print("  No connection succeeded")
        finally:
            await playwright.stop()
    finally:
        proc.terminate()
        proc.wait()
        print("Firefox terminated")

if __name__ == '__main__':
    asyncio.run(test())