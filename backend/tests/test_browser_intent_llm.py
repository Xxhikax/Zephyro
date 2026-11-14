"""Tests for browser intent LLM extractor — JSON parsing + schema validation."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from core.browser_intent_schema import parse_browser_action, _clean_llm_json, BROWSER_ACTION_SCHEMA


class TestCleanLLMJson:
    def test_plain_json(self):
        result = _clean_llm_json('{"action": "search", "target": "hola"}')
        assert result == '{"action": "search", "target": "hola"}'

    def test_json_in_markdown_block(self):
        result = _clean_llm_json('```json\n{"action": "search", "target": "hola"}\n```')
        assert result == '{"action": "search", "target": "hola"}'

    def test_json_with_no_lang_specifier(self):
        result = _clean_llm_json('```\n{"action": "navigate", "target": "github.com"}\n```')
        assert result == '{"action": "navigate", "target": "github.com"}'

    def test_json_with_text_around(self):
        result = _clean_llm_json('Aquí está: {"action": "none"} creo.')
        assert result == '{"action": "none"}'

    def test_no_json(self):
        result = _clean_llm_json("no hay json aquí")
        assert result == "no hay json aquí"


class TestParseBrowserAction:
    def test_navigate(self):
        result = parse_browser_action('{"action": "navigate", "target": "github.com"}')
        assert result is not None
        assert result["intent"] == "automation"
        assert result["automation_intent"] == "NAVIGATE"
        assert result["target"] == "github.com"
        assert result["_source"] == "capa1.5_llm_extractor"

    def test_search_google(self):
        result = parse_browser_action(
            '{"action": "search", "target": "cómo hacer pan", "provider": "google"}'
        )
        assert result is not None
        assert result["automation_intent"] == "SEARCH"
        assert result["provider"] == "google"
        assert result["search_query"] == "cómo hacer pan"

    def test_search_youtube(self):
        result = parse_browser_action(
            '{"action": "search", "target": "tutoriales python", "provider": "youtube"}'
        )
        assert result is not None
        assert result["automation_intent"] == "SEARCH"
        assert result["provider"] == "youtube"

    def test_play_media(self):
        result = parse_browser_action(
            '{"action": "play_media", "target": "música clásica", "provider": "youtube"}'
        )
        assert result is not None
        assert result["automation_intent"] == "PLAY_MEDIA"
        assert result["provider"] == "youtube"
        assert result["search_query"] == "música clásica"

    def test_play_media_defaults_youtube(self):
        result = parse_browser_action(
            '{"action": "play_media", "target": "lofi hip hop", "provider": "generic"}'
        )
        assert result is not None
        assert result["automation_intent"] == "PLAY_MEDIA"
        assert result["provider"] == "youtube"

    def test_click(self):
        result = parse_browser_action(
            '{"action": "click", "target": "botón iniciar sesión", "provider": "generic"}'
        )
        assert result is not None
        assert result["automation_intent"] == "INTERACT"
        assert result["interact_action"] == "click"
        assert result["interact_target"] == "botón iniciar sesión"

    def test_type(self):
        result = parse_browser_action(
            '{"action": "type", "target": "campo de búsqueda", "provider": "generic", '
            '"params": {"text": "hola mundo"}}'
        )
        assert result is not None
        assert result["automation_intent"] == "INTERACT"
        assert result["interact_action"] == "type"
        assert result["interact_text"] == "hola mundo"

    def test_scroll(self):
        result = parse_browser_action(
            '{"action": "scroll", "target": "página", "provider": "generic", '
            '"params": {"direction": "down", "amount": 500}}'
        )
        assert result is not None
        assert result["automation_intent"] == "INTERACT"
        assert result["scroll_direction"] == "down"
        assert result["scroll_amount"] == 500

    def test_playback_control_pause(self):
        result = parse_browser_action(
            '{"action": "playback_control", "target": "pause", "provider": "generic"}'
        )
        assert result is not None
        assert result["automation_intent"] == "PLAYBACK_CONTROL"
        assert result["control_action"] == "pause"

    def test_playback_control_next(self):
        result = parse_browser_action(
            '{"action": "playback_control", "target": "next", "provider": "generic"}'
        )
        assert result is not None
        assert result["control_action"] == "next"

    def test_playback_control_close_maps_to_close(self):
        result = parse_browser_action(
            '{"action": "playback_control", "target": "stop", "provider": "generic"}'
        )
        assert result is not None
        assert result["control_action"] == "close"

    def test_none_action(self):
        result = parse_browser_action('{"action": "none", "target": ""}')
        assert result is None

    def test_invalid_json(self):
        result = parse_browser_action("esto no es json")
        assert result is None

    def test_missing_required_fields(self):
        result = parse_browser_action('{"action": "navigate"}')
        assert result is not None  # target defaults to ""


class TestBrowserActionSchema:
    """Validación del schema JSON para Anthropic tool use."""

    def test_schema_has_required_fields(self):
        assert "properties" in BROWSER_ACTION_SCHEMA
        assert "action" in BROWSER_ACTION_SCHEMA["properties"]
        assert "target" in BROWSER_ACTION_SCHEMA["properties"]
        assert "required" in BROWSER_ACTION_SCHEMA
        assert "action" in BROWSER_ACTION_SCHEMA["required"]
        # Nota: `target` ya no es obligatorio — algunas acciones (screenshot,
        # playback_control, sequence) no necesitan target. Solo `action` es required.

    def test_action_enum_has_all_values(self):
        actions = BROWSER_ACTION_SCHEMA["properties"]["action"]["enum"]
        expected = {
            "navigate", "search", "play_media", "click", "type",
            "scroll", "screenshot", "extract_text", "playback_control",
            "sequence",  # añadido para soporte de instrucciones compuestas
            "none",
        }
        assert set(actions) == expected

    def test_provider_enum_has_key_services(self):
        providers = BROWSER_ACTION_SCHEMA["properties"]["provider"]["enum"]
        assert "youtube" in providers
        assert "google" in providers
        assert "spotify" in providers
        assert "generic" in providers
