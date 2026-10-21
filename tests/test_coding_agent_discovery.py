"""Deterministic discovery checks without probing installed personal CLIs."""

import os
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from omnigent import coding_agent_discovery as discovery


@pytest.fixture(autouse=True)
def empty_catalog(monkeypatch):
    monkeypatch.setattr(discovery, "_catalog_cache", {})


def test_search_path_and_executable_override(tmp_path, monkeypatch):
    extra = tmp_path / "bin"
    extra.mkdir()
    monkeypatch.setattr(discovery, "_EXTRA_BIN_DIRS", (str(extra), str(tmp_path / "absent")))
    monkeypatch.setenv("PATH", "/inherited")
    assert discovery.search_path() == f"{extra}{os.pathsep}/inherited"
    spec = discovery.SPECS_BY_KEY["claude"]
    binary = extra / "claude"
    binary.write_text("stub")
    monkeypatch.setenv("CLAUDE_CLI_PATH", str(binary))
    assert discovery.resolve_binary(spec) is None
    binary.chmod(0o700)
    assert discovery.resolve_binary(spec) == str(binary)
    monkeypatch.delenv("CLAUDE_CLI_PATH")
    assert discovery.resolve_binary(spec) == str(binary)
    binary.unlink()
    assert discovery.resolve_binary(spec) is None


@pytest.mark.asyncio
async def test_run_captures_output_and_isolates_parent_environment(monkeypatch):
    proc = SimpleNamespace(
        returncode=3, communicate=AsyncMock(return_value=(b" out \n", b"err\xff"))
    )
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(discovery.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(discovery, "search_path", lambda: "/safe/bin")
    monkeypatch.setenv("CLAUDECODE", "parent")
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert await discovery._run("/agent", ["--version"], cwd="/tmp") == (3, "out", "err\ufffd")
    assert spawn.call_args.args == ("/agent", "--version")
    kwargs = spawn.call_args.kwargs
    assert kwargs["cwd"] == "/tmp"
    assert kwargs["env"]["PATH"] == "/safe/bin"
    assert kwargs["env"]["NO_COLOR"] == "1"
    assert "CLAUDECODE" not in kwargs["env"]
    assert kwargs["stdin"] == discovery.asyncio.subprocess.DEVNULL


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [OSError("missing"), ValueError("invalid argv")])
async def test_run_reports_spawn_failure(monkeypatch, error):
    monkeypatch.setattr(discovery.asyncio, "create_subprocess_exec", AsyncMock(side_effect=error))
    assert await discovery._run("/missing", []) == (127, "", str(error))


@pytest.mark.asyncio
async def test_run_kills_timed_out_probe(monkeypatch):
    proc = SimpleNamespace(communicate=AsyncMock(side_effect=TimeoutError), kill=Mock())
    monkeypatch.setattr(discovery.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
    assert await discovery._run("/agent", [], timeout=0.01) == (124, "", "timed out after 0.01s")
    proc.kill.assert_called_once_with()


@pytest.mark.parametrize(
    "key,stdout,expected",
    [
        ("ollama", "NAME ID SIZE MODIFIED\nllama3:latest abc 1GB today\n", ["llama3:latest"]),
        ("aider", "Models which match:\n- provider/model\nignored header\n", ["provider/model"]),
        ("opencode", "\x1b[32mprovider/model\x1b[0m\n\nignore banner\nx\ty\n", ["provider/model"]),
    ],
)
def test_parse_models_accepts_only_catalog_entries(key, stdout, expected):
    entries = discovery._parse_models(discovery.SPECS_BY_KEY[key], stdout)
    assert [entry.id for entry in entries] == expected
    assert all(entry.label == entry.id and entry.source == "probe" for entry in entries)


@pytest.mark.asyncio
async def test_probe_models_cache_expiry_bypass_and_clear(monkeypatch):
    spec = discovery.SPECS_BY_KEY["opencode"]
    run = AsyncMock(return_value=(0, "vendor/first", ""))
    clock = Mock(return_value=1000.0)
    monkeypatch.setattr(discovery, "_run", run)
    monkeypatch.setattr(discovery, "time", SimpleNamespace(time=clock))
    assert (await discovery.probe_models(spec, "/agent"))[0].id == "vendor/first"
    run.return_value = (0, "vendor/second", "")
    assert (await discovery.probe_models(spec, "/agent"))[0].id == "vendor/first"
    assert run.await_count == 1
    assert (await discovery.probe_models(spec, "/agent", use_cache=False))[0].id == "vendor/second"
    assert (await discovery.probe_models(spec, "/agent"))[0].id == "vendor/first"
    clock.return_value += discovery._CATALOG_TTL_S + 1
    assert (await discovery.probe_models(spec, "/agent"))[0].id == "vendor/second"
    discovery.clear_coding_agent_cache()
    assert discovery._catalog_cache == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("filter_term,expected", [("", "."), ("claude", "claude")])
async def test_aider_probe_uses_writable_cwd_and_required_filter(
    monkeypatch, filter_term, expected
):
    spec = discovery.SPECS_BY_KEY["aider"]
    run = AsyncMock(return_value=(0, "- model-one", ""))
    monkeypatch.setattr(discovery, "_run", run)
    assert (await discovery.probe_models(spec, "/aider", filter_term=filter_term))[
        0
    ].id == "model-one"
    run.assert_awaited_once_with(
        "/aider",
        [*spec.models_command, expected],
        timeout=discovery._MODEL_PROBE_TIMEOUT_S,
        cwd="/tmp",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [(1, "", "not logged in"), (0, "", ""), (0, "not a model", "")])
async def test_failed_or_empty_model_probe_falls_back_to_curated_models(monkeypatch, result):
    spec = replace(
        discovery.SPECS_BY_KEY["opencode"],
        static_models=(discovery.ModelEntry("fallback", "Fallback"),),
    )
    monkeypatch.setattr(discovery, "_run", AsyncMock(return_value=result))
    assert await discovery.probe_models(spec, "/agent") == list(spec.static_models)


@pytest.mark.asyncio
async def test_static_models_do_not_start_subprocess(monkeypatch):
    run = AsyncMock()
    monkeypatch.setattr(discovery, "_run", run)
    spec = discovery.SPECS_BY_KEY["claude"]
    assert await discovery.probe_models(spec, "/claude") == list(spec.static_models)
    run.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result,expected",
    [
        ((0, "\n\x1b[32m1.2.3\x1b[0m\nother", ""), "1.2.3"),
        ((0, "", "v2\n"), "v2"),
        ((1, "v1", "error"), None),
        ((0, " \n", ""), None),
    ],
)
async def test_probe_version_cleans_first_nonempty_line(monkeypatch, result, expected):
    monkeypatch.setattr(discovery, "_run", AsyncMock(return_value=result))
    assert await discovery.probe_version(discovery.SPECS_BY_KEY["claude"], "/claude") == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("installed,with_models", [(False, True), (True, False), (True, True)])
async def test_discover_agent_reports_installation_and_optional_catalog(
    monkeypatch, installed, with_models
):
    monkeypatch.setattr(discovery, "resolve_binary", lambda spec: "/claude" if installed else None)
    version = AsyncMock(return_value="1.0")
    models = AsyncMock(return_value=[discovery.ModelEntry("opus", "Opus")])
    monkeypatch.setattr(discovery, "probe_version", version)
    monkeypatch.setattr(discovery, "probe_models", models)
    record = await discovery.discover_agent(
        discovery.SPECS_BY_KEY["claude"], with_models=with_models
    )
    assert record["installed"] is installed
    assert record["path"] == ("/claude" if installed else None)
    assert record["version"] == ("1.0" if installed else None)
    assert record["native_harness"] is not None
    assert record["models"] == (
        [{"id": "opus", "label": "Opus", "source": "static"}] if installed and with_models else []
    )
    assert version.await_count == int(installed)
    assert models.await_count == int(installed and with_models)


@pytest.mark.asyncio
async def test_scan_preserves_failures_and_counts_only_installed_models(monkeypatch):
    specs = discovery.CODING_AGENT_SPECS[:3]
    monkeypatch.setattr(discovery, "CODING_AGENT_SPECS", specs)
    probe = AsyncMock(
        side_effect=[
            {
                "key": specs[0].key,
                "installed": True,
                "native_harness": "native",
                "models": ["one", "two"],
            },
            RuntimeError("probe failed"),
            {"key": specs[2].key, "installed": False, "models": []},
        ]
    )
    monkeypatch.setattr(discovery, "discover_agent", probe)
    monkeypatch.setattr(
        discovery, "time", SimpleNamespace(time=Mock(side_effect=[1000.0, 1000.125]))
    )
    result = await discovery.discover_coding_agents(with_models=False)
    assert result["counts"] == {"known": 3, "installed": 1, "native": 1, "models": 2}
    assert result["duration_ms"] == 125
    assert result["scanned_at"] == 1000.0
    assert result["agents"][1]["error"] == "probe failed"
    assert result["agents"][1]["installed"] is False
    assert all(call.kwargs == {"with_models": False} for call in probe.await_args_list)
