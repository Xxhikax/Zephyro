"""Tests de prompt_cache."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os

from core.prompt_cache import (
    StructuredSystemPrompt, CacheableBlock,
    build_structured_system, sanitize_for_cache, supports_caching,
)


def test_cacheable_block_emits_cache_control():
    b = CacheableBlock(text="hello", cacheable=True, name="test")
    out = b.to_anthropic()
    assert out == {
        "type": "text", "text": "hello",
        "cache_control": {"type": "ephemeral"},
    }


def test_dynamic_block_no_cache_control():
    b = CacheableBlock(text="dynamic", cacheable=False)
    out = b.to_anthropic()
    assert out == {"type": "text", "text": "dynamic"}
    assert "cache_control" not in out


def test_build_structured_system_orders_blocks():
    sp = build_structured_system(
        identity="I am Zephyro",
        capabilities="I can do X",
        skills_summary="- skill-A\n- skill-B",
        session_specific="usuario actual: bruno",
    )
    assert len(sp) == 4
    assert sp.blocks[0].name == "identity"
    assert sp.blocks[0].cacheable is True
    assert sp.blocks[3].name == "session"
    assert sp.blocks[3].cacheable is False


def test_structured_to_anthropic_content():
    sp = build_structured_system(
        identity="ID", capabilities="CAP",
        skills_summary="SK", session_specific="SES",
    )
    content = sp.to_anthropic_content()
    cacheable_count = sum(1 for c in content if "cache_control" in c)
    assert cacheable_count == 3  # identity + capabilities + skills_summary


def test_structured_to_plain_text():
    sp = build_structured_system(
        identity="A", capabilities="B",
        skills_summary="C", session_specific="D",
    )
    text = sp.to_plain_text()
    assert "A" in text and "B" in text and "C" in text and "D" in text


def test_skips_empty_blocks():
    sp = build_structured_system(
        identity="X", capabilities="", skills_summary="   ", session_specific="Y",
    )
    assert len(sp) == 2  # solo X e Y


def test_sanitize_replaces_home_path():
    home = os.path.expanduser("~")
    text = f"abre archivo {home}/Documents/x.txt"
    sanitized = sanitize_for_cache(text)
    assert home not in sanitized
    assert "<HOME>" in sanitized


def test_sanitize_replaces_repo_paths():
    text = "Working on D:\\zephyro_repos\\fastapi and D:/zephyro_repos/express"
    sanitized = sanitize_for_cache(text)
    assert "fastapi" not in sanitized or "<REPO>" in sanitized


def test_supports_caching_anthropic():
    assert supports_caching("anthropic") is True
    assert supports_caching("Claude_CLI") is True
    assert supports_caching("openai") is False
    assert supports_caching("deepseek") is False
    assert supports_caching(None) is False


def test_estimated_cacheable_tokens():
    sp = build_structured_system(
        identity="x" * 400,  # ~100 tokens
        capabilities="y" * 800,
        skills_summary="z" * 200,
        session_specific="dynamic" * 50,
    )
    tokens = sp.estimated_cacheable_tokens()
    # 1400 chars / 4 = 350 tokens cacheables
    assert 300 < tokens < 400
