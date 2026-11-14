"""Tests de provider_config y opencode_config_writer."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from core.provider_config import (
    ProviderConfig, get_provider_config, reset_provider_config, TASK_KINDS,
)
from modules.code_analysis.opencode_config_writer import (
    write_providers, read_existing, opencode_config_path,
)
from modules.code_analysis.pricing_table import estimate_cost, PRICING


# ── ProviderConfig ─────────────────────────────────────────────────────

@pytest.fixture
def tmp_cfg(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    storage = tmp_path / "providers.json"
    cfg = ProviderConfig(storage_path=storage)
    return cfg


def test_seed_creates_known_providers(tmp_cfg):
    names = [p["name"] for p in tmp_cfg.list_providers()]
    assert "anthropic" in names
    assert "deepseek" in names
    assert "openrouter" in names
    assert "claude_cli" in names


def test_default_task_assignment_includes_all_kinds(tmp_cfg):
    assignments = tmp_cfg.task_assignments()
    for kind in TASK_KINDS:
        assert kind in assignments


def test_update_persists(tmp_cfg, tmp_path):
    tmp_cfg.update("deepseek", enabled=True, default_model="deepseek/deepseek-v4-flash")
    # Recargar
    cfg2 = ProviderConfig(storage_path=tmp_cfg.storage_path)
    deepseek = cfg2.get("deepseek")
    assert deepseek.enabled is True
    assert deepseek.default_model == "deepseek/deepseek-v4-flash"


def test_update_api_key_writes_env(tmp_cfg, tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    monkeypatch.chdir(tmp_path)
    # Patch _find_env_file para apuntar a tmp
    from core import provider_config as mod
    monkeypatch.setattr(mod, "_find_env_file", lambda: env_file)

    tmp_cfg.update("deepseek", api_key="sk-test-1234")
    assert env_file.exists()
    content = env_file.read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY=sk-test-1234" in content
    # El env del proceso también debe haberse actualizado
    import os
    assert os.environ.get("DEEPSEEK_API_KEY") == "sk-test-1234"


def test_assign_task_validates(tmp_cfg):
    tmp_cfg.assign_task("introspection_t2", provider="deepseek", model="deepseek/deepseek-v4-pro")
    assert tmp_cfg.get_task("introspection_t2") == {
        "provider": "deepseek", "model": "deepseek/deepseek-v4-pro",
    }
    with pytest.raises(KeyError):
        tmp_cfg.assign_task("nonexistent_task", provider="deepseek")
    with pytest.raises(KeyError):
        tmp_cfg.assign_task("chat", provider="nonexistent_provider")


def test_record_test_result(tmp_cfg):
    tmp_cfg.record_test_result("anthropic", ok=True)
    e = tmp_cfg.get("anthropic")
    assert e.last_test_ok is True
    assert e.last_test_at is not None


def test_redacted_dict_no_api_key(tmp_cfg, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-supersecret-xyz")
    e = tmp_cfg.get("deepseek")
    d = e.to_dict()
    assert "api_key" not in d
    assert d["api_key_set"] is True
    assert d["api_key_hint"] == "***-xyz"


def test_singleton_reset(tmp_cfg):
    instance1 = get_provider_config()
    reset_provider_config()
    instance2 = get_provider_config()
    assert instance1 is not instance2


# ── opencode_config_writer ─────────────────────────────────────────────

def test_write_providers_creates_file(tmp_path):
    cfg_path = tmp_path / "opencode.jsonc"
    write_providers(
        [{
            "name": "deepseek",
            "base_url": "https://api.deepseek.com",
            "api_key_env": "DEEPSEEK_API_KEY",
            "models": ["deepseek/deepseek-v4-pro"],
        }],
        path=cfg_path,
    )
    assert cfg_path.exists()
    parsed = read_existing(cfg_path)
    assert "deepseek" in parsed["provider"]
    block = parsed["provider"]["deepseek"]
    assert block["options"]["baseURL"] == "https://api.deepseek.com"
    assert block["options"]["apiKey"] == {"env": "DEEPSEEK_API_KEY"}
    assert "deepseek-v4-pro" in block["models"]


def test_write_providers_preserves_unknown(tmp_path):
    cfg_path = tmp_path / "opencode.jsonc"
    cfg_path.write_text(
        json.dumps({
            "provider": {
                "custom_user_provider": {"options": {"foo": "bar"}}
            }
        }),
        encoding="utf-8",
    )
    write_providers(
        [{
            "name": "deepseek",
            "base_url": "https://api.deepseek.com",
            "api_key_env": "DEEPSEEK_API_KEY",
            "models": ["deepseek/deepseek-v4-pro"],
        }],
        path=cfg_path,
        preserve_unknown=True,
    )
    parsed = read_existing(cfg_path)
    assert "custom_user_provider" in parsed["provider"]
    assert "deepseek" in parsed["provider"]


def test_write_providers_creates_backup(tmp_path):
    cfg_path = tmp_path / "opencode.jsonc"
    cfg_path.write_text('{"provider": {}}', encoding="utf-8")
    write_providers(
        [{"name": "deepseek", "base_url": "x", "api_key_env": "Y", "models": []}],
        path=cfg_path,
    )
    backups = list(tmp_path.glob("opencode.jsonc.bak.*"))
    assert len(backups) == 1


# ── pricing_table ──────────────────────────────────────────────────────

def test_estimate_cost_anthropic_sonnet():
    cost = estimate_cost(
        "anthropic", "claude-sonnet-4-6",
        input_tokens=1_000_000, output_tokens=500_000,
    )
    # 3 + 7.50 = 10.50
    assert abs(cost - 10.50) < 0.01


def test_estimate_cost_deepseek_cheap():
    cost = estimate_cost(
        "deepseek", "deepseek-flash",
        input_tokens=1_000_000, output_tokens=1_000_000,
    )
    # Precio peak oficial (2026-09): 0.30 + 1.20 = 1.50
    assert abs(cost - 1.50) < 0.01


def test_estimate_cost_current_anthropic_generation():
    cost = estimate_cost(
        "anthropic", "claude-sonnet-5",
        input_tokens=1_000_000, output_tokens=1_000_000,
    )
    # 2 + 10 = 12
    assert abs(cost - 12.0) < 0.01


def test_load_migrates_legacy_model_ids(tmp_path, monkeypatch):
    """Un providers.json viejo (Sonnet 4.6, Opus 4.7, deepseek-r1) se migra al
    catálogo vigente al cargar y se persiste."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    storage = tmp_path / "providers.json"
    storage.write_text(json.dumps({
        "providers": {
            "anthropic": {
                "name": "anthropic", "backend": "anthropic_sdk", "enabled": True,
                "api_key_env": "ANTHROPIC_API_KEY", "base_url": None,
                "default_model": "claude-sonnet-4-6",
                "models": ["claude-haiku-4-5", "claude-sonnet-4-6", "claude-sonnet-5",
                           "claude-opus-4-7", "claude-opus-5"],
            },
            "deepseek": {
                "name": "deepseek", "backend": "opencode_cli", "enabled": True,
                "api_key_env": "DEEPSEEK_API_KEY", "base_url": "https://api.deepseek.com",
                "default_model": "deepseek/deepseek-r1",
                "models": ["deepseek/deepseek-v4-pro", "deepseek/deepseek-r1"],
            },
        },
        "task_assignment": {
            "chat": {"provider": "anthropic", "model": "claude-sonnet-4-6"},
            "diagnostic_t3_reasoning": {"provider": "anthropic", "model": "claude-opus-4-7"},
            "introspection_t2": {"provider": "deepseek", "model": "deepseek/deepseek-r1"},
        },
    }), encoding="utf-8")

    cfg = ProviderConfig(storage_path=storage)

    anth = cfg.get("anthropic")
    assert anth.default_model == "claude-sonnet-5-5"
    assert not {"claude-sonnet-4-6", "claude-sonnet-5", "claude-opus-5",
                "claude-haiku-4-5"} & set(anth.models)
    assert {"claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5",
            "claude-fable-5-1"}.issubset(anth.models)
    assert len(anth.models) == len(set(anth.models))  # legacy duplicados colapsan

    ds = cfg.get("deepseek")
    assert "deepseek/deepseek-r1" not in ds.models
    assert ds.default_model == "deepseek/deepseek-v4-pro"

    tasks = cfg.task_assignments()
    assert tasks["chat"]["model"] == "claude-sonnet-5-5"
    assert tasks["diagnostic_t3_reasoning"]["model"] == "claude-opus-5-5"
    # retirado sin equivalente → default del catálogo para esa tarea
    assert tasks["introspection_t2"]["model"] == "deepseek/deepseek-v4-pro"

    # Persistido: una segunda carga ya no necesita migrar
    on_disk = json.loads(storage.read_text(encoding="utf-8"))
    assert on_disk["providers"]["anthropic"]["default_model"] == "claude-sonnet-5-5"
    cfg2 = ProviderConfig(storage_path=storage)
    assert cfg2._migrate_legacy_models() is False


def test_estimate_cost_unknown_model_returns_zero():
    cost = estimate_cost("fictional_provider", "fictional_model",
                        input_tokens=10000, output_tokens=10000)
    assert cost == 0.0


def test_pricing_table_completeness():
    """Cada provider conocido debe tener al menos un modelo en pricing."""
    providers_in_pricing = {k.split("/", 1)[0] for k in PRICING}
    expected = {"anthropic", "deepseek", "openai", "gemini"}
    assert expected.issubset(providers_in_pricing)
