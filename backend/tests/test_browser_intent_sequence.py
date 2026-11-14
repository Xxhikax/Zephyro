"""
Tests para multi-action sequence en browser_intent_schema + routing a DeepSeek.
"""
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.browser_intent_schema import (
    parse_browser_action,
    extract_browser_intent,
    BROWSER_ACTION_SCHEMA,
)


# ── Schema definition ────────────────────────────────────────────────────

def test_schema_has_sequence_action():
    """El enum de acciones debe incluir 'sequence'."""
    actions = BROWSER_ACTION_SCHEMA["properties"]["action"]["enum"]
    assert "sequence" in actions


def test_schema_has_steps_field():
    """El schema debe tener un campo 'steps' (array) para sequence."""
    props = BROWSER_ACTION_SCHEMA["properties"]
    assert "steps" in props
    assert props["steps"]["type"] == "array"


def test_schema_target_not_required():
    """'target' ya no es required (porque sequence usa 'steps')."""
    assert BROWSER_ACTION_SCHEMA["required"] == ["action"]


# ── parse_browser_action: sequence handling ──────────────────────────────

def test_parse_sequence_2_steps():
    """play_media + navigate = sequence con 2 steps parseados."""
    raw = json.dumps({
        "action": "sequence",
        "steps": [
            {"action": "play_media", "target": "thunderstruck", "provider": "youtube_music"},
            {"action": "navigate", "target": "discord", "provider": "generic"},
        ],
    })
    parsed = parse_browser_action(raw)
    assert parsed is not None
    assert parsed["automation_intent"] == "SEQUENCE"
    assert parsed["_source"] == "capa1.5_llm_extractor_sequence"
    assert len(parsed["steps"]) == 2
    assert parsed["steps"][0]["automation_intent"] == "PLAY_MEDIA"
    assert parsed["steps"][0]["search_query"] == "thunderstruck"
    assert parsed["steps"][0]["provider"] == "youtube_music"
    assert parsed["steps"][1]["automation_intent"] == "NAVIGATE"
    assert parsed["steps"][1]["target"] == "discord"


def test_parse_sequence_3_steps():
    """3 steps con search + navigate + click."""
    raw = json.dumps({
        "action": "sequence",
        "steps": [
            {"action": "navigate", "target": "google", "provider": "google"},
            {"action": "search", "target": "lobos del rio", "provider": "google"},
            {"action": "click", "target": "primer resultado"},
        ],
    })
    parsed = parse_browser_action(raw)
    assert parsed["automation_intent"] == "SEQUENCE"
    assert len(parsed["steps"]) == 3


def test_parse_sequence_empty_steps_returns_none():
    """Sequence sin steps válidos retorna None."""
    raw = json.dumps({"action": "sequence", "steps": []})
    assert parse_browser_action(raw) is None


def test_parse_sequence_all_none_steps_returns_none():
    """Sequence cuyos steps son todos 'none' filtra y queda vacía."""
    raw = json.dumps({
        "action": "sequence",
        "steps": [
            {"action": "none"},
            {"action": "none"},
        ],
    })
    assert parse_browser_action(raw) is None


def test_parse_sequence_skips_invalid_steps():
    """Steps que no son dict se ignoran; los válidos se parsean."""
    raw = json.dumps({
        "action": "sequence",
        "steps": [
            "invalid",
            {"action": "navigate", "target": "youtube", "provider": "youtube"},
            42,
        ],
    })
    parsed = parse_browser_action(raw)
    assert parsed is not None
    assert len(parsed["steps"]) == 1


# ── Backward compat: single actions still work ──────────────────────────

def test_parse_single_navigate_still_works():
    raw = json.dumps({"action": "navigate", "target": "youtube", "provider": "youtube"})
    parsed = parse_browser_action(raw)
    assert parsed is not None
    assert parsed["automation_intent"] == "NAVIGATE"


def test_parse_single_play_media_still_works():
    raw = json.dumps({
        "action": "play_media",
        "target": "thunderstruck acdc",
        "provider": "youtube_music",
    })
    parsed = parse_browser_action(raw)
    assert parsed["automation_intent"] == "PLAY_MEDIA"
    assert parsed["search_query"] == "thunderstruck acdc"


def test_parse_action_none_returns_none():
    raw = json.dumps({"action": "none"})
    assert parse_browser_action(raw) is None


# ── DeepSeek routing ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_extract_uses_deepseek_when_key_present(monkeypatch):
    """Si DEEPSEEK_API_KEY está presente, debe usar DeepSeek Flash JSON."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("BROWSER_INTENT_PROVIDER", "auto")

    fake_llm = MagicMock()
    fake_llm.generate = MagicMock(return_value='{"action":"none"}')

    with patch("core.llm_helpers.call_deepseek_flash_json", new_callable=AsyncMock) as mock_ds:
        mock_ds.return_value = json.dumps({
            "action": "navigate",
            "target": "youtube",
            "provider": "youtube",
        })
        result = await extract_browser_intent("abre youtube", fake_llm)

    assert mock_ds.called
    assert result is not None
    assert result["automation_intent"] == "NAVIGATE"
    # llm_manager.generate NO debe haberse llamado si DeepSeek funcionó
    fake_llm.generate.assert_not_called()


@pytest.mark.asyncio
async def test_extract_fallback_to_haiku_when_deepseek_fails(monkeypatch):
    """Si DeepSeek devuelve vacío/error, fallback a llm_manager.generate (Haiku)."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("BROWSER_INTENT_PROVIDER", "auto")

    fake_llm = MagicMock()
    fake_llm.generate = MagicMock(return_value=json.dumps({
        "action": "navigate",
        "target": "discord",
        "provider": "generic",
    }))

    with patch("core.llm_helpers.call_deepseek_flash_json", new_callable=AsyncMock) as mock_ds:
        mock_ds.return_value = None   # DeepSeek failed
        result = await extract_browser_intent("abre discord", fake_llm)

    assert mock_ds.called
    fake_llm.generate.assert_called_once()
    assert result is not None
    assert result["automation_intent"] == "NAVIGATE"
    assert result["target"] == "discord"


@pytest.mark.asyncio
async def test_extract_skips_deepseek_when_no_key(monkeypatch):
    """Sin DEEPSEEK_API_KEY, no llama a DeepSeek y va directo a Haiku."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("BROWSER_INTENT_PROVIDER", "auto")

    fake_llm = MagicMock()
    fake_llm.generate = MagicMock(return_value=json.dumps({
        "action": "navigate", "target": "youtube", "provider": "youtube",
    }))

    with patch("core.llm_helpers.call_deepseek_flash_json", new_callable=AsyncMock) as mock_ds:
        result = await extract_browser_intent("abre youtube", fake_llm)

    mock_ds.assert_not_called()
    fake_llm.generate.assert_called_once()
    assert result["automation_intent"] == "NAVIGATE"


@pytest.mark.asyncio
async def test_extract_deepseek_only_override_no_fallback(monkeypatch):
    """Con BROWSER_INTENT_PROVIDER=deepseek y DeepSeek fail, NO cae a Haiku."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("BROWSER_INTENT_PROVIDER", "deepseek")

    fake_llm = MagicMock()
    fake_llm.generate = MagicMock(return_value="should-not-be-called")

    with patch("core.llm_helpers.call_deepseek_flash_json", new_callable=AsyncMock) as mock_ds:
        mock_ds.return_value = None
        result = await extract_browser_intent("abre youtube", fake_llm)

    assert mock_ds.called
    fake_llm.generate.assert_not_called()
    assert result is None
