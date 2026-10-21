"""Behavior coverage for legacy setup prompts and generated agent configurations."""

from __future__ import annotations

import builtins
import io
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from rich.console import Console

from omnigent.onboarding import wizard as w


@pytest.fixture(autouse=True)
def isolated_wizard(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "os", SimpleNamespace(read=os.read, environ=os.environ))
    monkeypatch.setattr(
        w, "sys", SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: False), stdout=sys.stdout)
    )
    monkeypatch.setattr(w, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(w, "console", Console(file=io.StringIO(), width=100))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)


def feed(monkeypatch, name, values):
    mock = Mock(side_effect=values)
    monkeypatch.setattr(w, name, mock)
    return mock


@pytest.fixture
def terminal(monkeypatch):
    import select
    import termios
    import tty

    output = io.StringIO()
    monkeypatch.setattr(w.sys, "stdin", SimpleNamespace(isatty=lambda: True, fileno=lambda: 123))
    monkeypatch.setattr(w.sys, "stdout", output)
    monkeypatch.setattr(termios, "tcgetattr", lambda fd: ["original"])
    restore = Mock()
    monkeypatch.setattr(termios, "tcsetattr", restore)
    monkeypatch.setattr(tty, "setcbreak", Mock())
    monkeypatch.setattr(select, "select", lambda *args: ([123], [], []))

    def supply(keys, *, escape=False):
        monkeypatch.setattr(w.sys, "stdout", output)
        monkeypatch.setattr(w.os, "read", Mock(side_effect=keys))
        if escape:
            monkeypatch.setattr(select, "select", lambda *args: ([], [], []))
        return output, restore

    return supply


@pytest.mark.parametrize(
    "multi,keys,expected",
    [
        (False, [b"\x1b", b"[", b"B", b"\r"], 2),
        (False, [b"\x1b", b"[", b"A", b"\r"], 2),
        (True, [b"\r", b" ", b" ", b" ", b"\x1b", b"[", b"B", b" ", b"\r"], [0, 2]),
    ],
)
def test_arrow_menu_skips_disabled_and_toggles(terminal, multi, keys, expected):
    out, restore = terminal(keys)
    assert (
        w._arrow_menu(["Alpha\nDetails", "Unavailable\nReason", "Beta"], disabled={1}, multi=multi)
        == expected
    )
    assert "Details" in out.getvalue()
    assert "Reason" in out.getvalue()
    restore.assert_called_once()
    assert restore.call_args.args[2] == ["original"]


@pytest.mark.parametrize(
    "key,exception", [(b"\x1b", w._GoBack), (b"\x03", SystemExit), (b"\x04", SystemExit)]
)
def test_arrow_menu_cancellation_restores_terminal(terminal, key, exception):
    _, restore = terminal([key], escape=True)
    with pytest.raises(exception):
        w._arrow_menu(["Only"])
    restore.assert_called_once()


def test_arrow_menu_ignores_unknown_and_disallowed_escape(terminal):
    terminal([b"\x1b", b"x", b"\x1b", b"[", b"C", b"z", b"\r"])
    assert w._arrow_menu(["Only"], allow_back=False) == 0


@pytest.mark.parametrize(
    "multi,answers,expected",
    [(False, ["oops", "2", "9", "3"], 2), (True, ["oops", "2", "1,3"], [0, 2])],
)
def test_numbered_menu_rejects_disabled_and_invalid(monkeypatch, multi, answers, expected):
    monkeypatch.setattr(w.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(w.click, "prompt", Mock(side_effect=answers))
    assert w._arrow_menu(["A", "Disabled", "C"], disabled={1}, multi=multi) == expected
    assert "Invalid selection" in w.console.file.getvalue()


@pytest.mark.parametrize("hidden", [False, True])
def test_terminal_text_edits_and_masks_input(terminal, hidden):
    out, restore = terminal([b"a", b"b", b"\x7f", b"c", b"\r"])
    assert w._text_prompt("Value", hide_input=hidden, default="fallback") == "ac"
    assert ("Value: **" if hidden else "Value: ac") in out.getvalue()
    if hidden:
        assert "Value: ac" not in out.getvalue()
    restore.assert_called_once()


@pytest.mark.parametrize(
    "key,exception", [(b"\x1b", w._GoBack), (b"\x03", SystemExit), (b"\x04", SystemExit)]
)
def test_terminal_text_cancel_restores_state(terminal, key, exception):
    _, restore = terminal([key], escape=True)
    with pytest.raises(exception):
        w._text_prompt("Value")
    restore.assert_called_once()


def test_terminal_text_ignores_arrow_sequence_and_accepts_default(terminal):
    terminal([b"\x1b", b"[A", b"\x08", b"\r"])
    assert w._text_prompt("Value", default="kept", allow_back=False) == "kept"


@pytest.mark.parametrize(
    "raw,default,expected", [(" value ", None, "value"), ("", "saved", "saved"), ("", None, None)]
)
def test_text_fallback_trims_and_handles_empty(monkeypatch, raw, default, expected):
    monkeypatch.setattr(w.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(w.click, "prompt", Mock(return_value=raw))
    if expected is None:
        with pytest.raises(w._GoBack):
            w._text_prompt("Value", default=default)
    else:
        assert w._text_prompt("Value", default=default) == expected


@pytest.mark.parametrize(
    "content,expected",
    [
        (None, []),
        ("invalid config", []),
        ("[DEFAULT]\nhost=x\n", ["DEFAULT"]),
        ("[one]\nhost=x\n[two]\nhost=y\n", ["one", "two"]),
    ],
)
def test_profile_discovery(monkeypatch, tmp_path, content, expected):
    if content is not None:
        (tmp_path / ".databrickscfg").write_text(content)
    assert w._list_databricks_profiles() == expected


def test_harness_detection_uses_installed_binaries_and_packages(monkeypatch):
    monkeypatch.setattr(
        w.shutil, "which", lambda binary: "/bin/codex" if binary == "codex" else None
    )
    assert w._detect_coding_agents() == {"claude-sdk": None, "codex": "/bin/codex", "pi": None}
    original = builtins.__import__

    def importing(name, *args, **kwargs):
        if name == "agents":
            return SimpleNamespace()
        if name == "google.antigravity":
            raise ImportError(name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)
    assert w._detect_api_harnesses() == {"openai-agents": True, "antigravity": False}


@pytest.mark.parametrize(
    "choice,profiles,text,expected",
    [
        (
            0,
            [],
            ["test-only-key", "https://llm.example/v1"],
            {"type": "api_key", "api_key": "test-only-key", "base_url": "https://llm.example/v1"},
        ),
        (0, [], ["test-only-key", ""], {"type": "api_key", "api_key": "test-only-key"}),
        (1, ["a", "b", "c", "d"], ["b"], {"type": "databricks", "profile": "b"}),
        (1, ["sole"], ["sole"], {"type": "databricks", "profile": "sole"}),
    ],
)
def test_global_auth_collects_only_selected_credentials(
    monkeypatch, choice, profiles, text, expected
):
    feed(monkeypatch, "_list_databricks_profiles", [profiles])
    feed(monkeypatch, "_arrow_menu", [choice])
    prompts = feed(monkeypatch, "_text_prompt", text)
    assert w._prompt_global_auth() == (expected, None)
    if choice == 0:
        assert prompts.call_args_list[0].kwargs["hide_input"] is True


@pytest.mark.parametrize("responses", [[w._GoBack()], [0, w._GoBack()]])
def test_global_auth_can_back_out_from_credentials(monkeypatch, responses):
    feed(monkeypatch, "_arrow_menu", responses)
    feed(monkeypatch, "_text_prompt", [w._GoBack()])
    assert w._prompt_global_auth() == (None, None)


@pytest.mark.parametrize(
    "current,response,expected",
    [
        (None, "", None),
        (None, w._GoBack(), None),
        ("https://server.example", w._GoBack(), "https://server.example"),
        ("https://server.example", "", "https://server.example"),
        (None, "https://new.example", "https://new.example"),
    ],
)
def test_server_prompt_preserves_existing_on_cancel(monkeypatch, current, response, expected):
    feed(monkeypatch, "_text_prompt", [response])
    assert w._prompt_server_url(current) == expected


def test_existing_files_sorted_and_explicit_path_validated(monkeypatch, tmp_path):
    assert w._find_existing_configs() == []
    a = w._save_yaml("name: a", "a.yaml")
    b = w._save_yaml("name: b", "b.yaml")
    os.utime(a, (1, 1))
    os.utime(b, (2, 2))
    assert w._find_existing_configs() == [b, a]
    wrong = tmp_path / "wrong.txt"
    wrong.touch()
    feed(
        monkeypatch, "_text_prompt", [str(tmp_path / "missing"), str(tmp_path), str(wrong), str(a)]
    )
    assert w._prompt_agent_config_path() == a
    output = w.console.file.getvalue()
    assert "does not exist" in output
    assert "not a file" in output
    assert ".yaml or .yml" in output


@pytest.mark.parametrize(
    "choices,expected",
    [([0], None), ([1, 0], "a"), ([1, 1], "custom"), ([1, w._GoBack(), 0], None)],
)
def test_existing_config_menu_navigation(monkeypatch, choices, expected):
    feed(monkeypatch, "_arrow_menu", choices)
    feed(monkeypatch, "_prompt_agent_config_path", [Path("custom.yaml")])
    result = w._prompt_existing_or_new([Path("a.yaml")])
    assert (result.stem if result else None) == expected


def test_empty_explicit_config_path_goes_back(monkeypatch):
    feed(monkeypatch, "_text_prompt", [" "])
    with pytest.raises(w._GoBack):
        w._prompt_agent_config_path()


@pytest.mark.parametrize("index,expected", [(0, 1), (2, 2), (6, 3)])
def test_use_case_picker_mapping(monkeypatch, index, expected):
    picker = feed(monkeypatch, "_arrow_menu", [index])
    assert w._prompt_use_case() == expected
    assert picker.call_args.kwargs["disabled"] == {1, 3, 4, 5}


def test_agent_name_collision_and_sanitization(monkeypatch):
    import random

    w._save_yaml("name: taken", "my_coding_agent.yaml")
    monkeypatch.setattr(random, "randint", lambda *args: 42)
    assert w._default_agent_name(1) == "my_coding_agent_42"
    assert w._default_agent_name(2) == "my_coding_team"
    feed(monkeypatch, "_text_prompt", ["my_coding_agent", "  My New! Agent  "])
    assert w._prompt_agent_name(1) == "my_new_agent"
    assert w._sanitize_agent_name("!!!") == "my_agent"
    assert "already exists" in w.console.file.getvalue()


@pytest.mark.parametrize("multi", [False, True])
def test_pick_coding_agents_disables_missing_tools(monkeypatch, multi):
    picker = feed(monkeypatch, "_arrow_menu", [[1, 2] if multi else 1])
    detected = {"claude-sdk": None, "codex": "/bin/codex", "pi": "/bin/pi"}
    result = (w._show_coding_agents_and_pick_multi if multi else w._show_coding_agents_and_pick)(
        detected
    )
    assert ([v.harness for v in result] if multi else result.harness) == (
        ["codex", "pi"] if multi else "codex"
    )
    assert picker.call_args.kwargs["disabled"] == {0}
    assert picker.call_args.kwargs["default"] == 1
    assert (w._show_coding_agents_and_pick_multi if multi else w._show_coding_agents_and_pick)(
        {"codex": None}
    ) is None


@pytest.mark.parametrize(
    "choice,env,profiles,text,expected",
    [
        (0, {}, [], ["test-key", "gpt-test"], {"api_key": "test-key", "model": "gpt-test"}),
        (
            0,
            {"OPENAI_API_KEY": "env-test-key"},
            [],
            ["gpt-test"],
            {"api_key": None, "model": "gpt-test"},
        ),
        (
            1,
            {},
            [],
            ["https://llm.example/v1", "test-key", "custom-model"],
            {"base_url": "https://llm.example/v1", "api_key": "test-key", "model": "custom-model"},
        ),
        (
            1,
            {"OPENAI_API_KEY": "env-test-key", "OPENAI_BASE_URL": "https://env.example/v1"},
            [],
            ["custom-model"],
            {"base_url": "https://env.example/v1", "api_key": None},
        ),
        (2, {}, ["one"], ["db-model"], {"profile": "one", "model": "db-model"}),
        (2, {}, ["one", "two"], ["db-model"], {"profile": "two", "model": "db-model"}),
    ],
)
def test_supervisor_credentials(monkeypatch, choice, env, profiles, text, expected):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    feed(monkeypatch, "_list_databricks_profiles", [profiles])
    feed(monkeypatch, "_arrow_menu", [choice, 1])
    feed(monkeypatch, "_text_prompt", text)
    config = w._prompt_openai_agents_config()
    assert config.harness == "openai-agents"
    for name, value in expected.items():
        assert getattr(config, name) == value


def test_supervisor_credentials_back_resets_partial_values(monkeypatch):
    feed(monkeypatch, "_arrow_menu", [1, 0])
    feed(
        monkeypatch,
        "_text_prompt",
        ["https://abandoned.example", w._GoBack(), "test-key", "model"],
    )
    config = w._prompt_openai_agents_config()
    assert config.base_url is None
    assert config.api_key == "test-key"


@pytest.mark.parametrize("api", [False, True])
def test_supervisor_preserves_collaboration_task(monkeypatch, api):
    feed(monkeypatch, "_text_prompt", ["Write then review"])
    feed(monkeypatch, "_detect_api_harnesses", [{"openai-agents": api, "antigravity": False}])
    feed(monkeypatch, "_arrow_menu", [1 if api else 0])
    if api:
        feed(
            monkeypatch,
            "_prompt_openai_agents_config",
            [w._SupervisorConfig("openai-agents", model="model")],
        )
    result = w._prompt_supervisor({"codex": None if api else "/bin/codex"})
    assert result.task == "Write then review"
    assert result.harness == ("openai-agents" if api else "codex")


def test_supervisor_without_harness_exits(monkeypatch):
    feed(monkeypatch, "_text_prompt", ["Task"])
    feed(monkeypatch, "_detect_api_harnesses", [{}])
    with pytest.raises(SystemExit, match="1"):
        w._prompt_supervisor({"codex": None})


@pytest.mark.parametrize(
    "supervisor",
    [
        w._SupervisorConfig("codex"),
        w._SupervisorConfig(
            "openai-agents", model="model", task="Write and review", profile="team"
        ),
        w._SupervisorConfig("openai-agents", api_key="test-secret"),
    ],
)
def test_generated_yaml_has_workers_and_no_literal_secrets(supervisor):
    workers = [w._AgentChoice("claude-sdk", "Claude Code"), w._AgentChoice("codex", "Codex")]
    text = w._generate_multi_agent_yaml("team", workers, supervisor)
    config = yaml.load(text, Loader=yaml.BaseLoader)
    assert config["name"] == "team"
    assert config["executor"]["harness"] == supervisor.harness
    assert config["async"] == "true"
    assert config["cancellable"] == "true"
    assert config["tools"]["claude_sdk_worker"]["executor"]["harness"] == "claude-sdk"
    assert config["tools"]["codex_worker"]["max_sessions"] == "4"
    assert "test-secret" not in text
    if supervisor.profile:
        assert config["executor"]["auth"] == {"type": "databricks", "profile": "team"}
        assert "Write and review" in config["prompt"]
    elif supervisor.api_key:
        assert config["executor"]["auth"]["api_key"] == "$OPENAI_API_KEY"
    else:
        assert "auth" not in config["executor"]
    single = yaml.safe_load(w._generate_single_agent_yaml("solo", workers[0]))
    assert single["executor"] == {"harness": "claude-sdk"}
    assert single["name"] == "solo"


@pytest.mark.parametrize(
    "supervisor,auth",
    [
        (None, None),
        (w._SupervisorConfig("codex", profile="team"), {"type": "databricks", "profile": "team"}),
        (
            w._SupervisorConfig("openai-agents", api_key="test-secret"),
            {"type": "api_key", "api_key": "$OPENAI_API_KEY"},
        ),
    ],
)
def test_finish_setup_persists_default_and_preview(monkeypatch, tmp_path, supervisor, auth):
    import omnigent.cli as cli

    save = Mock()
    monkeypatch.setattr(cli, "_save_global_config", save)
    path = w._save_yaml("name: chosen\n", "chosen.yaml")
    w._finish_new_setup(path, path.read_text(), supervisor)
    expected = {"default_agent": str(path)}
    if auth:
        expected["auth"] = auth
    save.assert_called_once_with(expected)
    assert "Agent config preview" in w.console.file.getvalue()
    save.reset_mock()
    w._finish_existing_setup(path)
    save.assert_called_once_with({"default_agent": str(path)})


@pytest.mark.parametrize(
    "existing,reconfigure",
    [
        ({}, None),
        (
            {
                "server": "https://old.example",
                "auth": {"type": "databricks", "profile": "old"},
                "default_agent": "old.yaml",
            },
            1,
        ),
        ({"auth": {"type": "databricks", "profile": "old"}}, w._GoBack()),
    ],
)
def test_setup_entry_persists_requested_choices(monkeypatch, tmp_path, existing, reconfigure):
    import omnigent.cli as cli

    monkeypatch.setattr(cli, "_load_global_config", lambda: existing)
    save = Mock()
    monkeypatch.setattr(cli, "_save_global_config", save)
    feed(monkeypatch, "_prompt_server_url", ["https://new.example"])
    auth = {"type": "databricks", "profile": "new"}
    auth_prompt = feed(monkeypatch, "_prompt_global_auth", [(auth, None)])
    feed(monkeypatch, "_text_prompt", [str(tmp_path / "agent.yaml")])
    if reconfigure is not None:
        feed(monkeypatch, "_arrow_menu", [reconfigure])
    w.run_wizard_and_launch()
    expected = {"server": "https://new.example", "default_agent": str(tmp_path / "agent.yaml")}
    if not isinstance(reconfigure, Exception):
        expected["auth"] = auth
    else:
        auth_prompt.assert_not_called()
    save.assert_called_once_with(expected)


def test_setup_entry_skipping_everything_does_not_write(monkeypatch):
    import omnigent.cli as cli

    monkeypatch.setattr(cli, "_load_global_config", dict)
    save = Mock()
    monkeypatch.setattr(cli, "_save_global_config", save)
    feed(monkeypatch, "_prompt_server_url", [None])
    feed(monkeypatch, "_prompt_global_auth", [(None, None)])
    feed(monkeypatch, "_text_prompt", [w._GoBack()])
    w.run_wizard_and_launch()
    save.assert_not_called()
    assert "No changes" in w.console.file.getvalue()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Wizard routes the Antigravity supervisor choice through OpenAI configuration "
        "and loses the selected harness"
    ),
)
def test_antigravity_supervisor_keeps_selected_harness(monkeypatch):
    feed(monkeypatch, "_detect_api_harnesses", [{"openai-agents": False, "antigravity": True}])
    feed(monkeypatch, "_arrow_menu", [2, 0])
    feed(monkeypatch, "_text_prompt", ["Coordinate reviews", "test-only-key", "gemini-test"])
    result = w._prompt_supervisor({"codex": None})
    assert result.harness == "antigravity"


def test_supervisor_back_navigation_revisits_task(monkeypatch):
    feed(monkeypatch, "_detect_api_harnesses", [{}, {}])
    feed(monkeypatch, "_arrow_menu", [w._GoBack(), 0])
    feed(monkeypatch, "_text_prompt", ["Abandoned task", "Revised task"])
    assert w._prompt_supervisor({"codex": "/bin/codex"}).task == "Revised task"


def test_supervisor_back_at_first_step_propagates(monkeypatch):
    feed(monkeypatch, "_text_prompt", [w._GoBack()])
    with pytest.raises(w._GoBack):
        w._prompt_supervisor({"codex": "/bin/codex"})


def test_credentials_back_at_endpoint_picker_propagates(monkeypatch):
    feed(monkeypatch, "_arrow_menu", [w._GoBack()])
    with pytest.raises(w._GoBack):
        w._prompt_openai_agents_config()


@pytest.mark.parametrize("choice", [0, 1])
def test_global_auth_empty_credential_returns_to_type_picker(monkeypatch, choice):
    feed(monkeypatch, "_arrow_menu", [choice, 1])
    feed(monkeypatch, "_text_prompt", ["", "selected-profile"])
    assert w._prompt_global_auth() == ({"type": "databricks", "profile": "selected-profile"}, None)
