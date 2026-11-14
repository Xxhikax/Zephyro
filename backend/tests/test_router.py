#!/usr/bin/env python3
"""
Test router classification for browser commands.
"""
import sys
import os
import asyncio
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.llm_manager import LLMManager
from core.context_manager import ContextManager
from core.router import IntentRouter

async def test_router():
    llm_fast = LLMManager(local_model="llama3.2:1b", preset="rapido")
    context = ContextManager()
    router = IntentRouter(llm_fast, context)

    commands = [
        "abre youtube y reproduce gold in the ceiling",
        "abre youtube",
        "abre firefox",
        "navega a youtube.com",
        "busca gold in the ceiling en youtube",
        "reproduce gold in the ceiling",
    ]

    for cmd in commands:
        print(f"\n=== Command: '{cmd}' ===")
        analysis = await router.classify_and_parse(cmd)
        print(f"Intent: {analysis.get('intent')}")
        print(f"Automation intent: {analysis.get('automation_intent')}")
        print(f"Provider: {analysis.get('provider')}")
        print(f"Action: {analysis.get('action')}")
        print(f"Target: {analysis.get('target')}")
        print(f"Search query: {analysis.get('search_query')}")
        print(f"Navigate target: {analysis.get('navigate_target')}")
        print(f"Full analysis: {analysis}")

if __name__ == "__main__":
    asyncio.run(test_router())