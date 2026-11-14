#!/usr/bin/env python3
# Standalone manual script — NOT a pytest test. `test_parse(user_input)` takes a
# positional arg that pytest misreads as a fixture (collection error). Run via
# `python tests/test_compound.py`.
__test__ = False
import sys
import asyncio
sys.path.insert(0, '.')

from unittest.mock import MagicMock
from core.router import IntentRouter

async def test_parse(user_input):
    mock_llm = MagicMock()
    # Dummy response in case LLM is called
    mock_llm.generate = MagicMock(return_value='{"intent": "automation", "action": "open", "target": "firefox"}')
    router = IntentRouter(mock_llm, context_manager=None)
    result = await router.classify_and_parse(user_input)
    print(f"Input: {user_input}")
    print(f"Result: {result}")
    print(f"LLM called: {mock_llm.generate.called}")
    if mock_llm.generate.called:
        print(f"LLM call args: {mock_llm.generate.call_args}")
    return result

async def _main():
    test_cases = [
        "open firefox y busca youtube",
        "abre firefox y busca youtube",
        "abre instagram",
        "abre facebook",
        "open firefox",
        "open firefox y entra a youtube",
        "open firefox y busca gatos",
        "abre firefox y busca gatos",
        "reproduce music",
        "reproduce youtube music",
        "pon música",
    ]
    for tc in test_cases:
        await test_parse(tc)
        print("-" * 40)

if __name__ == "__main__":
    asyncio.run(_main())