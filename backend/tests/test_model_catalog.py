"""
Tests de core/model_catalog.py y de cómo LLMManager lo consume.

Sin red: los clientes LangChain se parchean sobre core.llm_manager (donde el
SUT los bindea) y solo se inspeccionan los kwargs de construcción.
"""
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from core import model_catalog as mc

BACKEND = Path(__file__).resolve().parents[1]


# ── model_catalog puro ─────────────────────────────────────────────────

def test_valid_ids_are_current_generation():
    assert mc.ANTHROPIC_VALID_MODELS == [
        "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1",
    ]
    assert mc.ANTHROPIC_DEFAULT_MODEL == "claude-sonnet-5-5"
    assert mc.DEEPSEEK_CHAT_MODEL == "deepseek-flash"
    assert mc.DEEPSEEK_PRO_MODEL == "deepseek-v4-pro"
    assert mc.DEEPSEEK_VALID_MODELS == ["deepseek-flash", "deepseek-v4-pro"]
    assert mc.DEEPSEEK_DEFAULT_MODEL == "deepseek-flash"


@pytest.mark.parametrize("model", mc.ANTHROPIC_VALID_MODELS)
def test_resolve_valid_returns_same(model):
    assert mc.resolve_anthropic_model(model) == model


@pytest.mark.parametrize("legacy,expected", [
    ("claude-sonnet-4-6", "claude-sonnet-5-5"),
    ("claude-sonnet-5", "claude-sonnet-5-5"),
    ("claude-opus-4-6", "claude-opus-5-5"),
    ("claude-opus-4-7", "claude-opus-5-5"),
    ("claude-opus-5", "claude-opus-5-5"),
    ("claude-fable-5", "claude-fable-5-1"),
    ("claude-haiku-4-5", "claude-haiku-5-5"),
    ("claude-haiku-4-5-20251001", "claude-haiku-5-5"),
])
def test_resolve_legacy_remaps(legacy, expected):
    assert mc.resolve_anthropic_model(legacy) == expected


@pytest.mark.parametrize("bad", [None, "", "   ", "deepseek-chat", "claude-3-5-sonnet-20241022", "gpt-4o"])
def test_resolve_unknown_returns_none(bad):
    assert mc.resolve_anthropic_model(bad) is None


@pytest.mark.parametrize("model,expected", [
    ("deepseek-flash", "deepseek-flash"),
    ("deepseek-v4-pro", "deepseek-v4-pro"),
    (" deepseek-v4-pro ", "deepseek-v4-pro"),
    ("deepseek-v4-flash", "deepseek-flash"),
    ("deepseek-chat", "deepseek-flash"),
    ("deepseek-reasoner", "deepseek-v4-pro"),
])
def test_resolve_deepseek(model, expected):
    assert mc.resolve_deepseek_model(model) == expected


@pytest.mark.parametrize("bad", [None, "", "claude-sonnet-5-5", "deepseek/deepseek-v4-pro", "deepseek-r1"])
def test_resolve_deepseek_unknown_returns_none(bad):
    assert mc.resolve_deepseek_model(bad) is None


def test_aliases_cover_every_tier():
    assert mc.ANTHROPIC_ALIASES == {
        "haiku": "claude-haiku-5-5",
        "sonnet": "claude-sonnet-5-5",
        "opus": "claude-opus-5-5",
        "fable": "claude-fable-5-1",
    }


def test_no_retired_ids_in_catalog():
    retired = {"deepseek-chat", "deepseek-reasoner", "deepseek-v4-flash",
               "claude-sonnet-4-20250514", "claude-opus-4-1-20250805",
               "claude-haiku-4-5", "claude-sonnet-5", "claude-opus-5"}
    exported = {
        mc.DEEPSEEK_CHAT_MODEL, mc.DEEPSEEK_FLASH_MODEL, mc.DEEPSEEK_PRO_MODEL,
        *mc.DEEPSEEK_VALID_MODELS,
        *mc.ANTHROPIC_VALID_MODELS, *mc.ANTHROPIC_ALIASES.values(),
    }
    assert not (retired & exported)


def test_cloud_chat_model_by_provider():
    assert mc.cloud_chat_model("deepseek") == "deepseek-flash"
    assert mc.cloud_chat_model("gemini") == mc.GEMINI_CHAT_MODEL
    assert mc.cloud_chat_model("openai") == mc.OPENAI_CHAT_MODEL
    assert mc.cloud_chat_model("anthropic") is None
    assert mc.cloud_chat_model(None) is None


def test_classifier_thinking_off_is_disabled():
    # Solo válido para el tier fast (Haiku 5.5). Sonnet/Opus/Fable devuelven 400.
    assert mc.ANTHROPIC_CLASSIFIER_THINKING_OFF == {"type": "disabled"}


def test_no_hardcoded_claude_ids_outside_catalog():
    """Ningún módulo de producción repite un ID ``claude-*``: todos importan del catálogo."""
    pattern = re.compile(r"""["']claude-(haiku|sonnet|opus|fable)-[0-9][0-9a-z-]*["']""")
    offenders = []
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if rel.startswith(("tests/", "venv/")) or rel == "core/model_catalog.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if pattern.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{rel}:{lineno}")
    assert offenders == []


# ── LLMManager ─────────────────────────────────────────────────────────

@pytest.fixture
def no_cloud_keys(monkeypatch):
    for k in ("GOOGLE_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
              "ANTHROPIC_MODEL", "DEEPSEEK_MODEL"):
        monkeypatch.delenv(k, raising=False)


def _make_manager(cloud_provider):
    from core.llm_manager import LLMManager
    with patch("core.llm_manager.Ollama", return_value=MagicMock()), \
         patch("core.llm_manager.threading.Thread") as thread_cls:
        thread_cls.return_value.start = lambda: None
        return LLMManager(local_model="llama3.2:1b", cloud_provider=cloud_provider)


def _quiet_status(mgr):
    # Evitar el ping real a ollama.list() dentro de get_status (cache de 30 s)
    import time as _time
    mgr._ollama_alive_cache = False
    mgr._ollama_alive_cache_time = _time.time()


def test_deepseek_uses_flash_with_thinking_off(no_cloud_keys, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-deepseek")
    with patch("core.llm_manager.ChatOpenAI") as chat_openai:
        chat_openai.return_value = MagicMock()
        mgr = _make_manager("deepseek")
    kwargs = chat_openai.call_args.kwargs
    assert mgr.deepseek_model == "deepseek-flash"
    assert kwargs["model"] == "deepseek-flash"
    assert kwargs["base_url"] == "https://api.deepseek.com"
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


def test_deepseek_env_model_selects_pro(no_cloud_keys, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-deepseek")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-reasoner")  # legacy → pro
    with patch("core.llm_manager.ChatOpenAI") as chat_openai:
        chat_openai.return_value = MagicMock()
        mgr = _make_manager("deepseek")
    assert mgr.deepseek_model == "deepseek-v4-pro"
    assert chat_openai.call_args.kwargs["model"] == "deepseek-v4-pro"
    # Pro también va con thinking off en chat: el timeout de 30 s no lo tolera.
    assert chat_openai.call_args.kwargs["extra_body"] == {"thinking": {"type": "disabled"}}


def test_set_deepseek_model_rebuilds_chat_client(no_cloud_keys, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-deepseek")
    with patch("core.llm_manager.ChatOpenAI") as chat_openai:
        chat_openai.return_value = MagicMock()
        mgr = _make_manager("deepseek")
        _quiet_status(mgr)
        status = mgr.set_deepseek_model("deepseek-v4-pro")
    assert mgr.deepseek_model == "deepseek-v4-pro"
    assert chat_openai.call_args.kwargs["model"] == "deepseek-v4-pro"
    assert status["current_model"] == "deepseek-v4-pro"

    with pytest.raises(ValueError):
        mgr.set_deepseek_model("gpt-4o")


def test_set_deepseek_model_while_anthropic_does_not_touch_cloud_model(no_cloud_keys, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    with patch("core.llm_manager.ChatAnthropic", return_value=MagicMock()):
        mgr = _make_manager("anthropic")
    _quiet_status(mgr)
    cloud_before = mgr.cloud_model
    mgr.set_deepseek_model("deepseek-v4-pro")
    assert mgr.cloud_model is cloud_before
    assert mgr.deepseek_model == "deepseek-v4-pro"


def test_switching_provider_keeps_deepseek_selection(no_cloud_keys, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-deepseek")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    with patch("core.llm_manager.ChatOpenAI") as chat_openai, \
         patch("core.llm_manager.ChatAnthropic", return_value=MagicMock()):
        chat_openai.return_value = MagicMock()
        mgr = _make_manager("deepseek")
        _quiet_status(mgr)
        mgr.set_deepseek_model("deepseek-v4-pro")
        mgr.set_cloud_provider("anthropic")
        mgr.set_cloud_provider("deepseek")
    assert chat_openai.call_args.kwargs["model"] == "deepseek-v4-pro"


def test_anthropic_default_is_sonnet_5_5_without_temperature(no_cloud_keys, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    with patch("core.llm_manager.ChatAnthropic") as chat_anthropic:
        chat_anthropic.return_value = MagicMock()
        mgr = _make_manager("anthropic")
    kwargs = chat_anthropic.call_args.kwargs
    assert mgr.anthropic_model == "claude-sonnet-5-5"
    assert kwargs["model"] == "claude-sonnet-5-5"
    # temperature devuelve 400 en toda la generación 5.x si no es default → no se manda.
    assert "temperature" not in kwargs


def test_legacy_env_model_is_remapped(no_cloud_keys, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    mgr = _make_manager("auto")
    assert mgr.anthropic_model == "claude-sonnet-5-5"


def test_set_anthropic_model_accepts_current_and_legacy(no_cloud_keys):
    mgr = _make_manager("auto")
    mgr.get_status = MagicMock(return_value={})

    mgr.set_anthropic_model("claude-fable-5-1")
    assert mgr.anthropic_model == "claude-fable-5-1"

    mgr.set_anthropic_model("claude-opus-5")
    assert mgr.anthropic_model == "claude-opus-5-5"

    with pytest.raises(ValueError):
        mgr.set_anthropic_model("claude-3-5-sonnet-20241022")


def test_status_reports_catalog_model_for_deepseek(no_cloud_keys, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-deepseek")
    with patch("core.llm_manager.ChatOpenAI", return_value=MagicMock()):
        mgr = _make_manager("deepseek")
    _quiet_status(mgr)
    status = mgr.get_status()
    assert status["current_model"] == "deepseek-flash"
    assert status["provider"] == "deepseek"
