"""Tests para skill_tools (whitelist + schemas)."""
import pytest

from core.skill_tools import (
    DIAGNOSE_TOOL,
    INTROSPECT_TOOL,
    READ_FILE_TOOL,
    all_skill_tools,
    is_path_allowed,
)


def test_tools_have_anthropic_format():
    """Cada tool debe tener name + description + input_schema."""
    for tool in (DIAGNOSE_TOOL, INTROSPECT_TOOL, READ_FILE_TOOL):
        assert "name" in tool
        assert "description" in tool
        assert "input_schema" in tool
        schema = tool["input_schema"]
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "required" in schema


def test_all_skill_tools_returns_three():
    tools = all_skill_tools()
    assert len(tools) == 3
    names = {t["name"] for t in tools}
    assert names == {"diagnose_zephyro", "introspect_code", "read_project_file"}


# ── is_path_allowed: whitelist ───────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "backend/cli.py",
    "backend/core/router.py",
    "src/chat.js",
    "src/index.html",
    "tools/whatsapp-bridge/index.js",
    "CLAUDE.md",
])
def test_allowed_paths(path):
    ok, reason = is_path_allowed(path)
    assert ok, f"path should be allowed: {path} (reason: {reason})"


@pytest.mark.parametrize("path,why", [
    (".env", "env file"),
    ("backend/.env", "env file in subdir"),
    ("cli_token.txt", "token file"),
    ("secrets/key.pem", "pem"),
    ("../etc/passwd", "traversal"),
    ("backend/../../etc/passwd", "traversal"),
    ("~/.zephyro/cli_token.txt", "home expansion"),
    ("private_key.pem", "private key"),
    ("backend/credentials.json", "credentials"),
    ("/etc/passwd", "absolute outside project"),
    ("", "empty"),
])
def test_blocked_paths(path, why):
    ok, reason = is_path_allowed(path)
    assert not ok, f"path should be BLOCKED ({why}): {path}"


def test_blocked_top_dir():
    """Paths con top-level dir no permitido deben ser rechazados."""
    ok, _ = is_path_allowed("node_modules/foo.js")
    assert not ok


def test_diagnose_tool_required_question():
    assert "question" in DIAGNOSE_TOOL["input_schema"]["required"]


def test_introspect_tool_default_tier():
    """tier debe default a 'gitnexus' (rápido)."""
    tier_prop = INTROSPECT_TOOL["input_schema"]["properties"]["tier"]
    assert tier_prop["default"] == "gitnexus"


def test_read_file_tool_line_count_documented():
    """line_count debe documentar el máximo (500)."""
    props = READ_FILE_TOOL["input_schema"]["properties"]
    assert "line_count" in props
    assert "500" in props["line_count"]["description"]
