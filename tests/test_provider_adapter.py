"""Provider-adapter launch contract: Claude build_launch parity."""

import json
import shlex

import pytest

from src.services import session_notify
from src.services.adapters.claude_adapter import ClaudeHistoryAdapter
from src.services.provider_adapter import (
    HistoryAdapter,
    McpEndpoint,
    ProviderAdapter,
)


@pytest.fixture
def adapter():
    return ClaudeHistoryAdapter()


def build(adapter, tmp_path, *, mcp=None, prompt=None, notifications=False):
    plan = adapter.build_launch(
        project_path=tmp_path,
        session_name="cc-test123",
        mcp=mcp,
        extra_system_prompt=prompt,
        notifications=notifications,
    )
    return plan


def cleanup(plan):
    for path in plan.temp_files:
        path.unlink(missing_ok=True)


def test_alias_is_provider_adapter():
    assert HistoryAdapter is ProviderAdapter


def test_bare_launch(adapter, tmp_path):
    plan = build(adapter, tmp_path)
    assert plan.command == "claude"
    assert plan.temp_files == []
    assert plan.env == {}


def test_mcp_flags_and_config_payload(adapter, tmp_path):
    plan = build(adapter, tmp_path, mcp=McpEndpoint(port=20417))
    try:
        argv = shlex.split(plan.command)
        assert argv[0] == "claude"
        assert "--strict-mcp-config" in argv
        config_path = argv[argv.index("--mcp-config") + 1]
        config = json.loads(open(config_path, encoding="utf-8").read())
        # The historical payload, byte-for-byte semantics: env placeholders
        # intact, no literal token anywhere.
        assert config == {
            "mcpServers": {
                "code-companion": {
                    "type": "http",
                    "url": "http://127.0.0.1:${CC_MCP_PORT}/mcp",
                    "headers": {"Authorization": "Bearer ${CC_MCP_TOKEN}"},
                }
            }
        }
        assert [str(p) for p in plan.temp_files] == [config_path]
    finally:
        cleanup(plan)


def test_notify_settings_payload(adapter, tmp_path):
    plan = build(adapter, tmp_path, notifications=True)
    try:
        argv = shlex.split(plan.command)
        settings_path = argv[argv.index("--settings") + 1]
        payload = json.loads(open(settings_path, encoding="utf-8").read())
        assert payload == session_notify.hook_settings("cc-test123")
    finally:
        cleanup(plan)


def test_system_prompt_quoted_last(adapter, tmp_path):
    prompt = "You are working in a “worktree” — don't merge; ask $USER."
    plan = build(adapter, tmp_path, prompt=prompt)
    argv = shlex.split(plan.command)
    # shlex round-trip preserves the prompt exactly (quotes, unicode, $).
    assert argv[argv.index("--append-system-prompt") + 1] == prompt
    assert argv[-1] == prompt


def test_all_features_flag_order(adapter, tmp_path):
    plan = build(
        adapter, tmp_path,
        mcp=McpEndpoint(port=20417), prompt="p", notifications=True,
    )
    try:
        argv = shlex.split(plan.command)
        # Historical flag order: mcp config, settings, system prompt.
        order = [argv.index(f) for f in
                 ("--strict-mcp-config", "--settings", "--append-system-prompt")]
        assert order == sorted(order)
        assert len(plan.temp_files) == 2
    finally:
        cleanup(plan)


def test_omissions(adapter, tmp_path):
    plan = build(adapter, tmp_path, mcp=None, prompt=None, notifications=False)
    for flag in ("--mcp-config", "--settings", "--append-system-prompt"):
        assert flag not in plan.command


def test_capabilities_and_metadata(adapter):
    assert adapter.provider_id == "claude"
    assert adapter.instruction_filenames == ("CLAUDE.md",)
    caps = adapter.capabilities
    assert caps.mcp and caps.notifications and caps.system_prompt_append
    assert caps.notification_clears and caps.resume


def test_mcp_endpoint_url_forms():
    ep = McpEndpoint(port=20417)
    assert ep.url() == "http://127.0.0.1:${CC_MCP_PORT}/mcp"
    assert ep.url(literal_port=True) == "http://127.0.0.1:20417/mcp"
