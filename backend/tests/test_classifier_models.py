"""
Contrato de los clasificadores/routers que llaman a un LLM.

Haiku 5.5 (tier ``fast``) piensa por default y devuelve 400 con ``temperature``
no-default. Los clasificadores con ``max_tokens`` chico deben mandar
``thinking: disabled``, no mandar ``temperature`` y leer el bloque ``text``
(el 0 puede ser ``thinking``). Los helpers DeepSeek del router usan su modelo
fijo: el selector de modelo del chat N3 no los toca.

Sin red: los clientes se parchean y se inspeccionan los kwargs.
"""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from core.model_catalog import ANTHROPIC_MODELS


def _block(kind: str, text: str = ""):
    if kind == "thinking":
        return SimpleNamespace(type="thinking", thinking="pensando...", signature="sig")
    return SimpleNamespace(type="text", text=text)


# ── intent_detector.classify_with_haiku ────────────────────────────────

def _run_intent(content):
    from core import intent_detector
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=SimpleNamespace(content=content))
    with patch.object(intent_detector.anthropic, "AsyncAnthropic", return_value=client):
        result = asyncio.run(intent_detector.classify_with_haiku("abrí spotify"))
    return result, client.messages.create.call_args.kwargs


def test_intent_classifier_sends_thinking_off_and_no_temperature():
    _, kwargs = _run_intent([_block("text", "command")])
    assert kwargs["model"] == ANTHROPIC_MODELS["fast"]
    assert kwargs["thinking"] == {"type": "disabled"}
    assert "temperature" not in kwargs


def test_intent_classifier_skips_thinking_block():
    result, _ = _run_intent([_block("thinking"), _block("text", "command")])
    assert result == "command"


def test_intent_classifier_without_text_block_falls_back_to_statement():
    result, _ = _run_intent([_block("thinking")])
    assert result == "statement"


# ── web classifier (needs_web_search) ──────────────────────────────────

def test_web_classifier_factory_contract():
    from core.llm_helpers import build_haiku_classifier
    with patch("langchain_anthropic.ChatAnthropic") as chat_anthropic:
        build_haiku_classifier("sk-ant-test", max_tokens=10)
    kwargs = chat_anthropic.call_args.kwargs
    assert kwargs["model"] == ANTHROPIC_MODELS["fast"]
    assert kwargs["thinking"] == {"type": "disabled"}
    assert kwargs["max_tokens"] == 10
    assert "temperature" not in kwargs


def test_web_classifier_factory_builds_real_client():
    """La versión instalada de langchain-anthropic acepta ``thinking`` (si no, el
    orchestrator caería en silencio a llm_fast)."""
    from core.llm_helpers import build_haiku_classifier
    model = build_haiku_classifier("sk-ant-test", max_tokens=10)
    assert model.thinking == {"type": "disabled"}
    assert model.model == ANTHROPIC_MODELS["fast"]


# ── vision ─────────────────────────────────────────────────────────────

def test_vision_text_skips_thinking_block():
    from modules.vision.image_analysis import _anthropic_text
    result = {"content": [
        {"type": "thinking", "thinking": "...", "signature": "s"},
        {"type": "text", "text": "Una captura de VS Code"},
    ]}
    assert _anthropic_text(result) == "Una captura de VS Code"
    assert _anthropic_text({"content": []}) == ""


def test_vision_payload_has_thinking_off(tmp_path):
    from modules.vision import image_analysis as ia
    img = tmp_path / "x.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
    analyzer = ia.ImageAnalyzer(api_key="", anthropic_key="sk-ant-test")
    response = MagicMock(status_code=200)
    response.json.return_value = {"content": [{"type": "text", "text": "ok"}]}
    with patch.object(ia.requests, "post", return_value=response) as post:
        assert analyzer._analyze_anthropic(str(img), "describí") == "ok"
    payload = post.call_args.kwargs["json"]
    assert payload["model"] == ANTHROPIC_MODELS["fast"]
    assert payload["thinking"] == {"type": "disabled"}
    assert "temperature" not in payload


# ── aislamiento: el selector DeepSeek no toca el router ────────────────

@pytest.mark.parametrize("selected", ["deepseek-flash", "deepseek-v4-pro"])
def test_router_extractor_ignores_chat_model_selection(monkeypatch, selected):
    from core import llm_helpers
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_MODEL", selected)  # lo que elegiría la UI para el chat
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))], usage=None,
    ))
    with patch("openai.AsyncOpenAI", return_value=client), \
         patch.object(llm_helpers, "_track_usage"):
        asyncio.run(llm_helpers.call_deepseek_flash_json("x"))
        asyncio.run(llm_helpers.call_deepseek_flash("x"))
    for call in client.chat.completions.create.call_args_list:
        assert call.kwargs["model"] == "deepseek-flash"
        assert call.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
