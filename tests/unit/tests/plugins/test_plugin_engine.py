"""Unit tests for plugin engine — matcher logic, JSON loading, rule parsing,
regex discovery, per-plugin params, hook events, and CC→ADK bridge semantics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opensage.plugins.claude_code_hook_loader import (
    _CC_ONLY_ACTION_FIELDS,
    _UNBRIDGEABLE_EVENTS,
    SUPPORTED_EVENTS,
    ClaudeCodeHookPlugin,
    PluginAction,
    _name_matches,
    _parse_json_sources,
    _tool_matches,
)

# ---------------------------------------------------------------------------
# _name_matches helper
# ---------------------------------------------------------------------------


class TestNameMatches:
    def test_exact_match(self):
        assert _name_matches("bash", "bash") is True

    def test_case_insensitive(self):
        assert _name_matches("Bash", "bash") is True
        assert _name_matches("BASH", "bash") is True
        assert _name_matches("bash", "BASH") is True

    def test_no_match(self):
        assert _name_matches("bash", "read_file") is False

    def test_pipe_separated_first(self):
        assert _name_matches("bash|read_file", "bash") is True

    def test_pipe_separated_second(self):
        assert _name_matches("bash|read_file", "read_file") is True

    def test_pipe_separated_miss(self):
        assert _name_matches("bash|read_file", "write_file") is False

    def test_pipe_separated_case_insensitive(self):
        assert _name_matches("Bash|Read_File", "read_file") is True

    def test_pipe_with_spaces(self):
        assert _name_matches("bash | read_file", "read_file") is True


# ---------------------------------------------------------------------------
# _tool_matches
# ---------------------------------------------------------------------------


class TestToolMatches:
    def test_wildcard_star(self):
        assert _tool_matches("*", "anything") is True

    def test_empty_matcher(self):
        assert _tool_matches("", "anything") is True

    def test_simple_name(self):
        assert _tool_matches("bash", "bash") is True
        assert _tool_matches("bash", "read_file") is False

    def test_argument_pattern_match(self):
        assert (
            _tool_matches("bash(npm test*)", "bash", {"command": "npm test --watch"})
            is True
        )

    def test_argument_pattern_no_match(self):
        assert (
            _tool_matches("bash(npm test*)", "bash", {"command": "git status"}) is False
        )

    def test_argument_pattern_wrong_tool(self):
        assert (
            _tool_matches("bash(npm test*)", "read_file", {"command": "npm test"})
            is False
        )

    def test_argument_pattern_no_args(self):
        assert _tool_matches("bash(npm test*)", "bash", None) is False
        assert _tool_matches("bash(npm test*)", "bash", {}) is False

    def test_argument_pattern_non_string_arg(self):
        assert _tool_matches("bash(npm*)", "bash", {"lines": 42}) is False

    def test_argument_pattern_multiple_args(self):
        assert (
            _tool_matches(
                "bash(npm*)", "bash", {"timeout": 30, "command": "npm install"}
            )
            is True
        )


# ---------------------------------------------------------------------------
# JSON loading and rule parsing
# ---------------------------------------------------------------------------


class TestJsonParsing:
    def _write_json(self, tmp_path: Path, name: str, data: dict) -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_load_simple_post_tool_use(self, tmp_path):
        config = {
            "PostToolUse": [
                {
                    "matcher": "bash",
                    "hooks": [{"type": "prompt", "prompt": "Check the output."}],
                }
            ]
        }
        self._write_json(tmp_path, "plugin.json", config)

        result = _parse_json_sources(["plugin.json"], tmp_path)

        assert len(result["PostToolUse"]) == 1
        rule = result["PostToolUse"][0]
        assert rule.matcher == "bash"
        assert len(rule.actions) == 1
        assert rule.actions[0].type == "prompt"
        assert rule.actions[0].prompt == "Check the output."

    def test_load_pre_tool_use(self, tmp_path):
        config = {
            "PreToolUse": [
                {
                    "matcher": "write_file",
                    "hooks": [{"type": "prompt", "prompt": "Think before writing."}],
                }
            ]
        }
        self._write_json(tmp_path, "plugin.json", config)

        result = _parse_json_sources(["plugin.json"], tmp_path)
        assert result["PreToolUse"][0].actions[0].prompt == "Think before writing."

    def test_load_command_action(self, tmp_path):
        config = {
            "PostToolUse": [
                {
                    "matcher": "bash(git commit*)",
                    "hooks": [{"type": "command", "command": "git status"}],
                }
            ]
        }
        self._write_json(tmp_path, "plugin.json", config)

        result = _parse_json_sources(["plugin.json"], tmp_path)
        action = result["PostToolUse"][0].actions[0]
        assert action.type == "command"
        assert action.command == "git status"

    def test_transient_flag(self, tmp_path):
        config = {
            "PostToolUse": [
                {
                    "matcher": "*",
                    "hooks": [{"type": "prompt", "prompt": "hint", "transient": True}],
                }
            ]
        }
        self._write_json(tmp_path, "plugin.json", config)

        result = _parse_json_sources(["plugin.json"], tmp_path)
        assert result["PostToolUse"][0].actions[0].transient is True

    def test_transient_defaults_false(self, tmp_path):
        config = {
            "PostToolUse": [
                {"matcher": "*", "hooks": [{"type": "prompt", "prompt": "Persistent."}]}
            ]
        }
        self._write_json(tmp_path, "plugin.json", config)

        result = _parse_json_sources(["plugin.json"], tmp_path)
        assert result["PostToolUse"][0].actions[0].transient is False

    def test_shorthand_rule(self, tmp_path):
        config = {
            "PostToolUse": [
                {"matcher": "bash", "type": "prompt", "prompt": "Shorthand style."}
            ]
        }
        self._write_json(tmp_path, "plugin.json", config)

        result = _parse_json_sources(["plugin.json"], tmp_path)
        assert result["PostToolUse"][0].actions[0].prompt == "Shorthand style."

    def test_multiple_sources_merged(self, tmp_path):
        self._write_json(
            tmp_path,
            "a.json",
            {
                "PostToolUse": [
                    {"matcher": "bash", "hooks": [{"type": "prompt", "prompt": "A"}]}
                ]
            },
        )
        self._write_json(
            tmp_path,
            "b.json",
            {
                "PostToolUse": [
                    {
                        "matcher": "read_file",
                        "hooks": [{"type": "prompt", "prompt": "B"}],
                    }
                ]
            },
        )

        result = _parse_json_sources(["a.json", "b.json"], tmp_path)
        assert len(result["PostToolUse"]) == 2

    def test_missing_source_skipped(self, tmp_path):
        result = _parse_json_sources(["nonexistent.json"], tmp_path)
        assert len(result["PostToolUse"]) == 0

    def test_unsupported_event_ignored(self, tmp_path):
        config = {
            "PostToolUse": [
                {"matcher": "bash", "hooks": [{"type": "prompt", "prompt": "ok"}]}
            ],
            "Notification": [
                {"matcher": "*", "hooks": [{"type": "prompt", "prompt": "ignored"}]}
            ],
        }
        self._write_json(tmp_path, "plugin.json", config)

        result = _parse_json_sources(["plugin.json"], tmp_path)
        assert len(result["PostToolUse"]) == 1

    def test_invalid_action_type_skipped(self, tmp_path):
        config = {
            "PostToolUse": [
                {
                    "matcher": "bash",
                    "hooks": [
                        {"type": "unknown_type", "prompt": "bad"},
                        {"type": "prompt", "prompt": "good"},
                    ],
                }
            ]
        }
        self._write_json(tmp_path, "plugin.json", config)

        result = _parse_json_sources(["plugin.json"], tmp_path)
        assert len(result["PostToolUse"][0].actions) == 1
        assert result["PostToolUse"][0].actions[0].prompt == "good"

    def test_gemini_cli_before_tool_alias(self, tmp_path):
        config = {
            "BeforeTool": [
                {"matcher": "bash", "hooks": [{"type": "prompt", "prompt": "pre hint"}]}
            ]
        }
        self._write_json(tmp_path, "plugin.json", config)

        result = _parse_json_sources(["plugin.json"], tmp_path)
        assert len(result["PreToolUse"]) == 1
        assert result["PreToolUse"][0].actions[0].prompt == "pre hint"

    def test_gemini_cli_after_tool_alias(self, tmp_path):
        config = {
            "AfterTool": [
                {"matcher": "*", "hooks": [{"type": "prompt", "prompt": "post hint"}]}
            ]
        }
        self._write_json(tmp_path, "plugin.json", config)

        result = _parse_json_sources(["plugin.json"], tmp_path)
        assert len(result["PostToolUse"]) == 1
        assert result["PostToolUse"][0].actions[0].prompt == "post hint"


# ---------------------------------------------------------------------------
# Built-in hooks
# ---------------------------------------------------------------------------


class TestBuiltinClaudeCodeHookPlugins:
    _HOOKS_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent
    _HOOKS_DIR = (
        _HOOKS_DIR / "src" / "opensage" / "plugins" / "default" / "claude_code_hooks"
    )

    def test_load_careful_edit(self):
        result = _parse_json_sources(
            [str(self._HOOKS_DIR / "careful_edit.json")], Path.cwd()
        )
        assert len(result["PreToolUse"]) >= 1
        assert len(result["PostToolUse"]) >= 1
        assert "str_replace" in result["PreToolUse"][0].matcher
        assert result["PreToolUse"][0].actions[0].type == "prompt"

    def test_load_test_output_review(self):
        result = _parse_json_sources(
            [str(self._HOOKS_DIR / "test_output_review.json")], Path.cwd()
        )
        assert len(result["PostToolUse"]) >= 1
        assert "pytest" in result["PostToolUse"][0].matcher
        assert result["PostToolUse"][0].actions[0].type == "prompt"

    def test_load_verify_after_change(self):
        result = _parse_json_sources(
            [str(self._HOOKS_DIR / "verify_after_change.json")], Path.cwd()
        )
        assert len(result["PostToolUse"]) >= 1
        assert "git diff" in result["PostToolUse"][0].matcher
        assert result["PostToolUse"][0].actions[0].type == "prompt"

    def test_missing_source_skipped(self):
        result = _parse_json_sources(["/nonexistent/path.json"], Path.cwd())
        assert len(result["PostToolUse"]) == 0


# ---------------------------------------------------------------------------
# ClaudeCodeHookPlugin._get_actions (integration with matcher)
# ---------------------------------------------------------------------------


class TestGetActions:
    def _plugin_with_rules(self, tmp_path: Path) -> ClaudeCodeHookPlugin:
        config = {
            "PostToolUse": [
                {
                    "matcher": "bash",
                    "hooks": [{"type": "prompt", "prompt": "bash hint"}],
                },
                {
                    "matcher": "bash|read_file",
                    "hooks": [{"type": "prompt", "prompt": "multi hint"}],
                },
                {
                    "matcher": "bash(git commit*)",
                    "hooks": [{"type": "command", "command": "git status"}],
                },
            ],
            "PreToolUse": [
                {
                    "matcher": "write_file",
                    "hooks": [{"type": "prompt", "prompt": "write warning"}],
                },
            ],
        }
        path = tmp_path / "plugin.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return ClaudeCodeHookPlugin(sources=["plugin.json"], base_dir=str(tmp_path))

    def test_matches_bash_simple(self, tmp_path):
        plugin = self._plugin_with_rules(tmp_path)
        actions = plugin._get_actions("PostToolUse", "bash", None)
        assert len(actions) == 2
        assert {a.prompt for a in actions} == {"bash hint", "multi hint"}

    def test_matches_bash_with_git_commit_arg(self, tmp_path):
        plugin = self._plugin_with_rules(tmp_path)
        actions = plugin._get_actions(
            "PostToolUse", "bash", {"command": "git commit -m 'test'"}
        )
        assert len(actions) == 3

    def test_matches_read_file(self, tmp_path):
        plugin = self._plugin_with_rules(tmp_path)
        actions = plugin._get_actions("PostToolUse", "read_file", None)
        assert len(actions) == 1
        assert actions[0].prompt == "multi hint"

    def test_no_match(self, tmp_path):
        plugin = self._plugin_with_rules(tmp_path)
        assert plugin._get_actions("PostToolUse", "unknown_tool", None) == []

    def test_pre_tool_use_event(self, tmp_path):
        plugin = self._plugin_with_rules(tmp_path)
        actions = plugin._get_actions("PreToolUse", "write_file", None)
        assert len(actions) == 1
        assert actions[0].prompt == "write warning"

    def test_unknown_event(self, tmp_path):
        plugin = self._plugin_with_rules(tmp_path)
        assert plugin._get_actions("Notification", "bash", None) == []


# ---------------------------------------------------------------------------
# Result injection
# ---------------------------------------------------------------------------


class TestResultInjection:
    def test_inject_into_empty_result(self):
        result = {}
        action = PluginAction(type="prompt", prompt="hint text")
        ClaudeCodeHookPlugin._inject_into_result(result, "hint text", action)
        assert "[Plugin] hint text" in result["output"]

    def test_inject_appends_to_existing(self):
        result = {"output": "original output"}
        action = PluginAction(type="prompt", prompt="extra")
        ClaudeCodeHookPlugin._inject_into_result(result, "extra", action)
        assert result["output"].startswith("original output")
        assert "[Plugin] extra" in result["output"]

    def test_inject_transient_falls_back(self):
        result = {"output": "orig"}
        action = PluginAction(type="prompt", prompt="transient hint", transient=True)
        ClaudeCodeHookPlugin._inject_into_result(result, "transient hint", action)
        assert "[Plugin] transient hint" in result["output"]


# ---------------------------------------------------------------------------
# load_plugins integration
# ---------------------------------------------------------------------------


class TestLoadPluginsUnified:
    def test_json_creates_named_hook_plugin(self):
        from opensage.plugins import load_plugins

        plugins = load_plugins(enabled_plugins=["careful_edit"])
        assert len(plugins) == 1
        assert plugins[0].name == "careful_edit"
        assert len(plugins[0].config["PreToolUse"]) >= 1
        assert len(plugins[0].config["PostToolUse"]) >= 1

    def test_multiple_json_each_becomes_plugin(self):
        from opensage.plugins import load_plugins

        plugins = load_plugins(enabled_plugins=["careful_edit", "test_output_review"])
        assert len(plugins) == 2
        assert plugins[0].name == "careful_edit"
        assert plugins[1].name == "test_output_review"

    def test_mixed_python_and_json_preserves_order(self):
        from opensage.plugins import load_plugins

        plugins = load_plugins(
            enabled_plugins=["quota_after_tool_plugin", "careful_edit"]
        )
        assert len(plugins) == 2
        assert plugins[0].name == "quota_after_tool"
        assert plugins[1].name == "careful_edit"

    def test_python_only_no_hook_plugin(self):
        from opensage.plugins import load_plugins

        plugins = load_plugins(enabled_plugins=["quota_after_tool_plugin"])
        assert len(plugins) == 1
        assert plugins[0].name == "quota_after_tool"

    def test_unknown_plugin_raises(self):
        from opensage.plugins import load_plugins

        with pytest.raises(ValueError, match="Unknown plugin"):
            load_plugins(enabled_plugins=["nonexistent_thing"])

    def test_empty_enabled(self):
        from opensage.plugins import load_plugins

        assert load_plugins(enabled_plugins=[]) == []
        assert load_plugins(enabled_plugins=None) == []

    def test_user_plugin_dir_json(self, tmp_path):
        from opensage.plugins import load_plugins

        user_plugins = tmp_path / "plugins"
        user_plugins.mkdir()
        (user_plugins / "my_plugin.json").write_text(
            json.dumps(
                {
                    "PostToolUse": [
                        {
                            "matcher": "bash",
                            "hooks": [{"type": "prompt", "prompt": "user plugin"}],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        plugins = load_plugins(enabled_plugins=["my_plugin"], agent_dir=tmp_path)
        assert len(plugins) == 1
        assert plugins[0].name == "my_plugin"
        assert plugins[0].config["PostToolUse"][0].actions[0].prompt == "user plugin"

    def test_user_plugin_dir_not_exists(self):
        from opensage.plugins import load_plugins

        plugins = load_plugins(
            enabled_plugins=["careful_edit"], agent_dir="/nonexistent/agent"
        )
        assert len(plugins) == 1
        assert plugins[0].name == "careful_edit"

    def test_local_plugin_dir_defaults_to_home_local(self, tmp_path, monkeypatch):
        from opensage.plugins import load_plugins

        local_plugins = tmp_path / ".local" / "opensage" / "plugins"
        local_plugins.mkdir(parents=True)
        (local_plugins / "local_only.json").write_text(
            json.dumps(
                {
                    "PostToolUse": [
                        {
                            "matcher": "bash",
                            "hooks": [{"type": "prompt", "prompt": "local plugin"}],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        plugins = load_plugins(enabled_plugins=["local_only"])
        assert len(plugins) == 1
        assert plugins[0].name == "local_only"


# ---------------------------------------------------------------------------
# DoomLoopDetectorPlugin
# ---------------------------------------------------------------------------


class TestDoomLoopDetectorPlugin:
    @pytest.fixture()
    def plugin(self):
        from opensage.plugins.default.adk_plugins.doom_loop_detector_plugin import (
            DoomLoopDetectorPlugin,
        )

        return DoomLoopDetectorPlugin(threshold=3)

    @pytest.fixture()
    def tool_context(self):
        """Minimal mock for ToolContext with a state dict."""

        class _Ctx:
            def __init__(self):
                self.state = {}

        return _Ctx()

    @pytest.fixture()
    def tool(self):
        class _Tool:
            name = "str_replace_editor"

        return _Tool()

    @pytest.mark.asyncio
    async def test_no_loop_first_call(self, plugin, tool, tool_context):
        result = await plugin.before_tool_callback(
            tool=tool,
            tool_args={"path": "/a.py", "old_str": "x"},
            tool_context=tool_context,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_loop_two_identical(self, plugin, tool, tool_context):
        args = {"path": "/a.py", "old_str": "x"}
        await plugin.before_tool_callback(
            tool=tool, tool_args=args, tool_context=tool_context
        )
        result = await plugin.before_tool_callback(
            tool=tool, tool_args=args, tool_context=tool_context
        )
        assert result is None  # only 2, threshold is 3

    @pytest.mark.asyncio
    async def test_loop_detected_on_third(self, plugin, tool, tool_context):
        args = {"path": "/a.py", "old_str": "x"}
        await plugin.before_tool_callback(
            tool=tool, tool_args=args, tool_context=tool_context
        )
        await plugin.before_tool_callback(
            tool=tool, tool_args=args, tool_context=tool_context
        )
        result = await plugin.before_tool_callback(
            tool=tool, tool_args=args, tool_context=tool_context
        )
        assert result is not None
        assert "DOOM LOOP DETECTED" in result["output"]

    @pytest.mark.asyncio
    async def test_different_call_resets(self, plugin, tool, tool_context):
        args_a = {"path": "/a.py", "old_str": "x"}
        args_b = {"path": "/b.py", "old_str": "y"}
        await plugin.before_tool_callback(
            tool=tool, tool_args=args_a, tool_context=tool_context
        )
        await plugin.before_tool_callback(
            tool=tool, tool_args=args_a, tool_context=tool_context
        )
        # Interject a different call
        await plugin.before_tool_callback(
            tool=tool, tool_args=args_b, tool_context=tool_context
        )
        # Now same as args_a again — should NOT trigger (streak broken)
        result = await plugin.before_tool_callback(
            tool=tool, tool_args=args_a, tool_context=tool_context
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_different_tool_name(self, plugin, tool_context):
        class _ToolA:
            name = "tool_a"

        class _ToolB:
            name = "tool_b"

        args = {"x": 1}
        await plugin.before_tool_callback(
            tool=_ToolA(), tool_args=args, tool_context=tool_context
        )
        await plugin.before_tool_callback(
            tool=_ToolA(), tool_args=args, tool_context=tool_context
        )
        # Same args but different tool name — should NOT trigger
        result = await plugin.before_tool_callback(
            tool=_ToolB(), tool_args=args, tool_context=tool_context
        )
        assert result is None


# ---------------------------------------------------------------------------
# ReadBeforeEditPlugin
# ---------------------------------------------------------------------------


class TestReadBeforeEditPlugin:
    @pytest.fixture()
    def plugin(self):
        from opensage.plugins.default.adk_plugins.read_before_edit_plugin import (
            ReadBeforeEditPlugin,
        )

        return ReadBeforeEditPlugin()

    @pytest.fixture()
    def tool_context(self):
        class _Ctx:
            def __init__(self):
                self.state = {}

        return _Ctx()

    def _make_tool(self, name):
        class _Tool:
            pass

        t = _Tool()
        t.name = name
        return t

    @pytest.mark.asyncio
    async def test_warn_on_blind_edit(self, plugin, tool_context):
        edit_tool = self._make_tool("str_replace_editor")
        args = {"path": "/workspace/foo.py", "old_str": "x", "new_str": "y"}

        # before_tool sets the warning flag
        await plugin.before_tool_callback(
            tool=edit_tool, tool_args=args, tool_context=tool_context
        )
        assert "_read_before_edit_warn" in tool_context.state

        # after_tool injects the warning
        result = {"output": "Edit applied."}
        await plugin.after_tool_callback(
            tool=edit_tool, tool_args=args, tool_context=tool_context, result=result
        )
        assert "WARNING" in result["warning"]
        assert "without reading" in result["warning"]

    @pytest.mark.asyncio
    async def test_no_warn_after_read(self, plugin, tool_context):
        read_tool = self._make_tool("view_file")
        edit_tool = self._make_tool("str_replace_editor")
        path = "/workspace/foo.py"

        # First read the file
        await plugin.after_tool_callback(
            tool=read_tool,
            tool_args={"path": path},
            tool_context=tool_context,
            result={"output": "file content"},
        )

        # Then edit — should NOT warn
        await plugin.before_tool_callback(
            tool=edit_tool,
            tool_args={"path": path, "old_str": "x", "new_str": "y"},
            tool_context=tool_context,
        )
        assert "_read_before_edit_warn" not in tool_context.state

    @pytest.mark.asyncio
    async def test_create_file_no_warn(self, plugin, tool_context):
        create_tool = self._make_tool("create_file")
        args = {"path": "/workspace/new.py", "content": "hello"}

        result = await plugin.before_tool_callback(
            tool=create_tool, tool_args=args, tool_context=tool_context
        )
        assert result is None
        assert "_read_before_edit_warn" not in tool_context.state

    @pytest.mark.asyncio
    async def test_multiple_edits_after_one_read(self, plugin, tool_context):
        read_tool = self._make_tool("view_file")
        edit_tool = self._make_tool("str_replace_editor")
        path = "/workspace/foo.py"

        # Read once
        await plugin.after_tool_callback(
            tool=read_tool,
            tool_args={"path": path},
            tool_context=tool_context,
            result={"output": "content"},
        )

        # Edit twice — both should be fine
        for _ in range(2):
            await plugin.before_tool_callback(
                tool=edit_tool,
                tool_args={"path": path, "old_str": "a", "new_str": "b"},
                tool_context=tool_context,
            )
            assert "_read_before_edit_warn" not in tool_context.state


# ---------------------------------------------------------------------------
# Regex patterns in enabled list
# ---------------------------------------------------------------------------


class TestRegexPatterns:
    def test_wildcard_enables_all(self):
        from opensage.plugins import _CLAUDE_CODE_HOOK_DIR, load_plugins

        plugins = load_plugins(enabled_plugins=[".*"])
        assert len(plugins) >= 1
        names = [p.name for p in plugins]
        # Each CC hook JSON becomes its own named plugin
        json_stems = {
            f.stem
            for f in _CLAUDE_CODE_HOOK_DIR.glob("*.json")
            if not f.name.startswith("_")
        }
        for stem in json_stems:
            assert stem in names

    def test_regex_suffix_pattern(self):
        from opensage.plugins import load_plugins

        plugins = load_plugins(enabled_plugins=[".*_plugin"])
        assert len(plugins) >= 1
        # .*_plugin only matches .py stems, not .json stems
        assert all(not p.name.endswith(".json") for p in plugins)
        assert len(plugins) >= 1

    def test_regex_prefix_pattern(self):
        from opensage.plugins import load_plugins

        plugins = load_plugins(enabled_plugins=["doom_.*"])
        assert len(plugins) == 1
        assert plugins[0].name == "doom_loop_detector"

    def test_regex_mixed_with_literal(self):
        from opensage.plugins import load_plugins

        plugins = load_plugins(enabled_plugins=["doom_.*", "careful_edit"])
        names = [p.name for p in plugins]
        assert "doom_loop_detector" in names
        assert "careful_edit" in names

    def test_regex_dedup(self):
        from opensage.plugins import load_plugins

        # "doom_loop_detector_plugin" matches both literally and via regex
        plugins = load_plugins(enabled_plugins=["doom_loop_detector_plugin", "doom_.*"])
        doom_count = sum(1 for p in plugins if p.name == "doom_loop_detector")
        assert doom_count == 1

    def test_regex_no_match_warns(self):
        from opensage.plugins import load_plugins

        plugins = load_plugins(enabled_plugins=["zzz_nonexistent_pattern_.*"])
        assert plugins == []

    def test_regex_user_dir(self, tmp_path):
        from opensage.plugins import load_plugins

        user_plugins = tmp_path / "plugins"
        user_plugins.mkdir()
        (user_plugins / "my_custom.json").write_text(
            json.dumps(
                {
                    "PostToolUse": [
                        {
                            "matcher": "*",
                            "hooks": [{"type": "prompt", "prompt": "custom"}],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        # regex should also discover user plugins
        plugins = load_plugins(enabled_plugins=["my_.*"], agent_dir=tmp_path)
        assert len(plugins) == 1
        assert plugins[0].name == "my_custom"


# ---------------------------------------------------------------------------
# Per-plugin params
# ---------------------------------------------------------------------------


class TestPluginParams:
    def test_params_passed_to_constructor(self):
        from opensage.plugins import load_plugins

        plugins = load_plugins(
            enabled_plugins=["doom_loop_detector_plugin"],
            adk_plugin_params={"doom_loop_detector_plugin": {"threshold": 7}},
        )
        assert len(plugins) == 1
        assert plugins[0].threshold == 7

    def test_params_default_when_missing(self):
        from opensage.plugins import load_plugins

        plugins = load_plugins(
            enabled_plugins=["doom_loop_detector_plugin"],
            adk_plugin_params={},
        )
        assert len(plugins) == 1
        # Default threshold is 3
        assert plugins[0].threshold == 3

    def test_params_with_regex(self):
        from opensage.plugins import load_plugins

        plugins = load_plugins(
            enabled_plugins=["doom_.*"],
            adk_plugin_params={"doom_loop_detector_plugin": {"threshold": 10}},
        )
        assert len(plugins) == 1
        assert plugins[0].threshold == 10

    def test_params_config_dataclass(self):
        from opensage.config.config_dataclass import PluginsConfig

        cfg = PluginsConfig(
            enabled=["doom_loop_detector_plugin"],
            params={"doom_loop_detector_plugin": {"threshold": 5}},
        )
        assert cfg.params["doom_loop_detector_plugin"]["threshold"] == 5


# ---------------------------------------------------------------------------
# New JSON hook events (Improvement 3)
# ---------------------------------------------------------------------------


class TestNewHookEvents:
    """Tests for the 5 new event types in JSON hooks."""

    def _write_json(self, tmp_path: Path, name: str, data: dict) -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_supported_events_complete(self):
        assert "PreToolUse" in SUPPORTED_EVENTS
        assert "PostToolUse" in SUPPORTED_EVENTS
        assert "UserPromptSubmit" in SUPPORTED_EVENTS
        assert "SessionStart" in SUPPORTED_EVENTS
        # PostToolUseFailure is unbridgeable, not in SUPPORTED_EVENTS
        assert "PostToolUseFailure" not in SUPPORTED_EVENTS
        assert "PostToolUseFailure" in _UNBRIDGEABLE_EVENTS
        # BeforeModel/AfterModel are NOT CC hooks — they're ADK-only
        assert "BeforeModel" not in SUPPORTED_EVENTS
        assert "AfterModel" not in SUPPORTED_EVENTS
        assert len(SUPPORTED_EVENTS) == 4

    def test_parse_post_tool_use_failure_warns_and_skips(self, tmp_path, caplog):
        """PostToolUseFailure should log warning and be skipped (unbridgeable)."""
        config = {
            "PostToolUseFailure": [
                {
                    "matcher": "bash",
                    "hooks": [{"type": "prompt", "prompt": "Tool failed, try again."}],
                }
            ]
        }
        self._write_json(tmp_path, "plugin.json", config)
        import logging

        with caplog.at_level(
            logging.WARNING, logger="opensage.plugins.claude_code_hook_loader"
        ):
            result = _parse_json_sources(["plugin.json"], tmp_path)
        # Should not be parsed into config (field no longer exists)
        assert "PostToolUseFailure" not in result
        # Should warn about being unbridgeable
        assert (
            "unbridgeable" in caplog.text.lower() or "swallows" in caplog.text.lower()
        )

    def test_parse_user_prompt_submit_event(self, tmp_path):
        config = {
            "UserPromptSubmit": [
                {
                    "matcher": "*",
                    "hooks": [{"type": "prompt", "prompt": "Preprocess user input."}],
                }
            ]
        }
        self._write_json(tmp_path, "plugin.json", config)
        result = _parse_json_sources(["plugin.json"], tmp_path)
        assert len(result["UserPromptSubmit"]) == 1

    def test_parse_session_start_event(self, tmp_path):
        config = {
            "SessionStart": [
                {
                    "matcher": "*",
                    "hooks": [{"type": "prompt", "prompt": "Initialize session."}],
                }
            ]
        }
        self._write_json(tmp_path, "plugin.json", config)
        result = _parse_json_sources(["plugin.json"], tmp_path)
        assert len(result["SessionStart"]) == 1

    def test_mixed_old_and_new_events(self, tmp_path, caplog):
        config = {
            "PreToolUse": [
                {"matcher": "bash", "hooks": [{"type": "prompt", "prompt": "pre"}]}
            ],
            "PostToolUse": [
                {"matcher": "*", "hooks": [{"type": "prompt", "prompt": "post"}]}
            ],
            "PostToolUseFailure": [
                {"matcher": "bash", "hooks": [{"type": "prompt", "prompt": "fail"}]}
            ],
            "SessionStart": [
                {"matcher": "*", "hooks": [{"type": "prompt", "prompt": "init"}]}
            ],
        }
        self._write_json(tmp_path, "plugin.json", config)
        import logging

        with caplog.at_level(
            logging.WARNING, logger="opensage.plugins.claude_code_hook_loader"
        ):
            result = _parse_json_sources(["plugin.json"], tmp_path)
        assert len(result["PreToolUse"]) == 1
        assert len(result["PostToolUse"]) == 1
        # PostToolUseFailure is skipped (unbridgeable)
        assert "PostToolUseFailure" not in result
        assert len(result["SessionStart"]) == 1

    def test_before_model_ignored_as_unsupported(self, tmp_path):
        """BeforeModel is NOT a CC hook — should be ignored by the parser."""
        config = {
            "BeforeModel": [
                {"matcher": "*", "hooks": [{"type": "prompt", "prompt": "not cc"}]}
            ],
            "PostToolUse": [
                {"matcher": "*", "hooks": [{"type": "prompt", "prompt": "ok"}]}
            ],
        }
        self._write_json(tmp_path, "plugin.json", config)
        result = _parse_json_sources(["plugin.json"], tmp_path)
        assert len(result["PostToolUse"]) == 1
        assert "BeforeModel" not in result

    @pytest.mark.asyncio
    async def test_session_start_command_skipped(self, tmp_path):
        """Command actions should be skipped for SessionStart (no sandbox)."""
        config = {
            "SessionStart": [
                {
                    "matcher": "*",
                    "hooks": [{"type": "command", "command": "echo hello"}],
                }
            ]
        }
        path = tmp_path / "plugin.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        plugin = ClaudeCodeHookPlugin(sources=["plugin.json"], base_dir=str(tmp_path))

        class _CallbackContext:
            pass

        llm_request = type("LlmRequest", (), {"contents": [object()]})()
        result = await plugin.before_model_callback(
            callback_context=_CallbackContext(),
            llm_request=llm_request,
        )
        assert result is None
        # Command action should not inject anything (only prompts do)
        assert len(llm_request.contents) == 1  # unchanged


# ---------------------------------------------------------------------------
# CC-only field detection (#7)
# ---------------------------------------------------------------------------


class TestCCOnlyFieldWarnings:
    """CC-only hook fields (decision, permissionDecision, updatedInput) should
    produce warnings at parse time."""

    def _write_json(self, tmp_path: Path, name: str, data: dict) -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_decision_field_warns(self, tmp_path, caplog):
        config = {
            "PreToolUse": [
                {
                    "matcher": "bash",
                    "hooks": [
                        {"type": "prompt", "prompt": "check", "decision": "deny"}
                    ],
                }
            ]
        }
        self._write_json(tmp_path, "plugin.json", config)
        import logging

        with caplog.at_level(
            logging.WARNING, logger="opensage.plugins.claude_code_hook_loader"
        ):
            result = _parse_json_sources(["plugin.json"], tmp_path)
        assert "decision" in caplog.text
        assert "cannot be bridged" in caplog.text
        # Rule is still parsed (just warned)
        assert len(result["PreToolUse"]) == 1

    def test_updated_input_field_warns(self, tmp_path, caplog):
        config = {
            "PreToolUse": [
                {
                    "matcher": "bash",
                    "hooks": [
                        {
                            "type": "prompt",
                            "prompt": "modify",
                            "updatedInput": {"command": "safe_cmd"},
                        }
                    ],
                }
            ]
        }
        self._write_json(tmp_path, "plugin.json", config)
        import logging

        with caplog.at_level(
            logging.WARNING, logger="opensage.plugins.claude_code_hook_loader"
        ):
            _parse_json_sources(["plugin.json"], tmp_path)
        assert "updatedInput" in caplog.text
        assert "cannot be bridged" in caplog.text

    def test_permission_decision_field_warns(self, tmp_path, caplog):
        config = {
            "PreToolUse": [
                {
                    "matcher": "bash",
                    "hooks": [
                        {
                            "type": "prompt",
                            "prompt": "deny",
                            "permissionDecision": "deny",
                        }
                    ],
                }
            ]
        }
        self._write_json(tmp_path, "plugin.json", config)
        import logging

        with caplog.at_level(
            logging.WARNING, logger="opensage.plugins.claude_code_hook_loader"
        ):
            _parse_json_sources(["plugin.json"], tmp_path)
        assert "permissionDecision" in caplog.text

    def test_no_warning_for_normal_fields(self, tmp_path, caplog):
        config = {
            "PostToolUse": [
                {
                    "matcher": "bash",
                    "hooks": [{"type": "prompt", "prompt": "check output"}],
                }
            ]
        }
        self._write_json(tmp_path, "plugin.json", config)
        import logging

        with caplog.at_level(
            logging.WARNING, logger="opensage.plugins.claude_code_hook_loader"
        ):
            _parse_json_sources(["plugin.json"], tmp_path)
        for field in _CC_ONLY_ACTION_FIELDS:
            assert field not in caplog.text


# ---------------------------------------------------------------------------
# CC → ADK bridge semantics
# ---------------------------------------------------------------------------


class TestBridgeSemantics:
    """Verify that each CC event's bridge behavior matches documented semantics.

    Tests the cross-callback injection pattern:
    - before_tool_callback stores context in pending lists
    - before_model_callback injects pending context transiently
    - on_event_callback persists context to session history
    """

    @pytest.mark.asyncio
    async def test_pre_tool_use_stores_prompt_for_model(self, tmp_path):
        """PreToolUse prompt should be stored for later injection, not logged as no-effect."""
        config = {
            "PreToolUse": [
                {
                    "matcher": "bash",
                    "hooks": [{"type": "prompt", "prompt": "Be careful."}],
                }
            ]
        }
        path = tmp_path / "plugin.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        plugin = ClaudeCodeHookPlugin(sources=["plugin.json"], base_dir=str(tmp_path))

        class _Tool:
            name = "bash"

        class _Ctx:
            state = {}

        result = await plugin.before_tool_callback(
            tool=_Tool(),
            tool_args={"command": "ls"},
            tool_context=_Ctx(),
        )
        assert result is None  # does not block tool
        # Prompt stored for injection by before_model_callback
        assert len(plugin._pending_pre_tool_context) == 1
        assert "Be careful." in plugin._pending_pre_tool_context[0]
        assert len(plugin._pending_pre_tool_persist) == 1

    @pytest.mark.asyncio
    async def test_before_model_injects_pre_tool_context(self, tmp_path):
        """before_model_callback should inject pending PreToolUse context."""
        config = {
            "PreToolUse": [
                {
                    "matcher": "bash",
                    "hooks": [{"type": "prompt", "prompt": "Be careful."}],
                }
            ]
        }
        path = tmp_path / "plugin.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        plugin = ClaudeCodeHookPlugin(sources=["plugin.json"], base_dir=str(tmp_path))

        # Simulate before_tool_callback having stored context
        plugin._pending_pre_tool_context.append("Be careful.")
        plugin._session_start_fired = True  # skip SessionStart

        class _CallbackContext:
            pass

        contents = [object()]  # existing content
        llm_request = type("LlmRequest", (), {"contents": contents})()

        result = await plugin.before_model_callback(
            callback_context=_CallbackContext(),
            llm_request=llm_request,
        )
        assert result is None
        # Should have appended a Content with the prompt
        assert len(llm_request.contents) == 2
        injected = llm_request.contents[1]
        assert injected.role == "user"
        assert "Be careful." in injected.parts[0].text
        # Pending list should be cleared
        assert len(plugin._pending_pre_tool_context) == 0

    @pytest.mark.asyncio
    async def test_before_model_injects_session_start_once(self, tmp_path):
        """SessionStart prompt should be injected on first model call only."""
        config = {
            "SessionStart": [
                {
                    "matcher": "*",
                    "hooks": [{"type": "prompt", "prompt": "Welcome."}],
                }
            ]
        }
        path = tmp_path / "plugin.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        plugin = ClaudeCodeHookPlugin(sources=["plugin.json"], base_dir=str(tmp_path))

        class _CallbackContext:
            pass

        # First call — should inject
        contents1 = [object()]
        llm_request1 = type("LlmRequest", (), {"contents": contents1})()
        await plugin.before_model_callback(
            callback_context=_CallbackContext(),
            llm_request=llm_request1,
        )
        assert len(llm_request1.contents) == 2
        assert "Welcome." in llm_request1.contents[1].parts[0].text
        assert plugin._session_start_fired is True

        # Second call — should NOT inject again
        contents2 = [object()]
        llm_request2 = type("LlmRequest", (), {"contents": contents2})()
        await plugin.before_model_callback(
            callback_context=_CallbackContext(),
            llm_request=llm_request2,
        )
        assert len(llm_request2.contents) == 1  # unchanged

    @pytest.mark.asyncio
    async def test_on_event_persists_context(self, tmp_path):
        """on_event_callback should persist pending context to session history."""
        config = {
            "PreToolUse": [
                {
                    "matcher": "bash",
                    "hooks": [{"type": "prompt", "prompt": "Be careful."}],
                }
            ]
        }
        path = tmp_path / "plugin.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        plugin = ClaudeCodeHookPlugin(sources=["plugin.json"], base_dir=str(tmp_path))

        # Simulate pending persist data
        plugin._pending_pre_tool_persist.append("Be careful.")

        appended_events = []

        class _SessionService:
            async def append_event(self, *, session, event):
                appended_events.append(event)

        class _Session:
            pass

        class _InvocationContext:
            invocation_id = "test-inv-id"
            session_service = _SessionService()
            session = _Session()

        class _Event:
            pass

        result = await plugin.on_event_callback(
            invocation_context=_InvocationContext(),
            event=_Event(),
        )
        assert result is None
        # Should have persisted one event
        assert len(appended_events) == 1
        assert appended_events[0].author == "user"
        assert "Be careful." in appended_events[0].content.parts[0].text
        # Pending list should be cleared
        assert len(plugin._pending_pre_tool_persist) == 0

    @pytest.mark.asyncio
    async def test_post_tool_use_injects_into_result(self, tmp_path):
        """PostToolUse prompt should be injected into tool result."""
        config = {
            "PostToolUse": [
                {
                    "matcher": "*",
                    "hooks": [{"type": "prompt", "prompt": "Review output."}],
                }
            ]
        }
        path = tmp_path / "plugin.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        plugin = ClaudeCodeHookPlugin(sources=["plugin.json"], base_dir=str(tmp_path))

        class _Tool:
            name = "bash"

        class _Ctx:
            state = {}

        result = {"output": "original output"}
        await plugin.after_tool_callback(
            tool=_Tool(),
            tool_args={},
            tool_context=_Ctx(),
            result=result,
        )
        assert "Review output." in result["output"]
        assert "original output" in result["output"]

    def test_post_tool_use_failure_not_bridged(self, tmp_path, caplog):
        """PostToolUseFailure should log warning and not create rules."""
        config = {
            "PostToolUseFailure": [
                {
                    "matcher": "bash",
                    "hooks": [
                        {"type": "prompt", "prompt": "Try a different approach."}
                    ],
                }
            ]
        }
        path = tmp_path / "plugin.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        import logging

        with caplog.at_level(
            logging.WARNING, logger="opensage.plugins.claude_code_hook_loader"
        ):
            plugin = ClaudeCodeHookPlugin(
                sources=["plugin.json"], base_dir=str(tmp_path)
            )
        # No PostToolUseFailure field on config
        assert "PostToolUseFailure" not in plugin.config
        # Warning should mention it's unbridgeable
        assert (
            "unbridgeable" in caplog.text.lower() or "swallows" in caplog.text.lower()
        )

    @pytest.mark.asyncio
    async def test_user_prompt_submit_appends_context(self, tmp_path):
        """UserPromptSubmit prompt should append to user_message.parts."""
        config = {
            "UserPromptSubmit": [
                {
                    "matcher": "*",
                    "hooks": [{"type": "prompt", "prompt": "Think step by step."}],
                }
            ]
        }
        path = tmp_path / "plugin.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        plugin = ClaudeCodeHookPlugin(sources=["plugin.json"], base_dir=str(tmp_path))

        class _Part:
            def __init__(self, text):
                self.text = text

        class _UserMessage:
            def __init__(self):
                self.parts = [_Part("user prompt")]

        class _InvocationContext:
            pass

        msg = _UserMessage()
        result = await plugin.on_user_message_callback(
            invocation_context=_InvocationContext(),
            user_message=msg,
        )
        assert result is None
        assert len(msg.parts) == 2
        assert "Think step by step." in msg.parts[1].text
