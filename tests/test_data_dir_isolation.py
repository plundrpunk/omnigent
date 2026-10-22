"""Regression coverage for pytest runtime-data isolation."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import textwrap
from importlib import import_module
from pathlib import Path

import httpx
import keyring.errors
import pytest

from omnigent import cli
from omnigent.host import local_server
from omnigent.onboarding import harness_auth, provider_config, secrets
from tests._helpers.live_server import isolated_local_server, isolated_process_env


@pytest.mark.parametrize("config_override", [False, True])
@pytest.mark.parametrize("keyring_unavailable", [False, True])
def test_onboarding_config_and_secrets_with_inaccessible_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config_override: bool,
    keyring_unavailable: bool,
) -> None:
    home = tmp_path / "inaccessible-home"
    home.mkdir()
    state = tmp_path / "state"
    config_dir = tmp_path / "config-override" if config_override else state
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(state))
    monkeypatch.delenv("OMNIGENT_CONFIG_HOME", raising=False)
    monkeypatch.delenv("OMNIGENT_INFERENCE_CONFIG", raising=False)
    if config_override:
        monkeypatch.setenv("OMNIGENT_CONFIG_HOME", str(config_dir))
    monkeypatch.setenv("OMNIGENT_DISABLE_KEYRING", "0" if keyring_unavailable else "1")

    def unavailable_keyring(*args: object) -> None:
        if not keyring_unavailable:
            pytest.fail("Disabled keyring must not be accessed")
        raise keyring.errors.KeyringError("test backend unavailable")

    for method in ("set_password", "get_password", "delete_password"):
        monkeypatch.setattr(keyring, method, unavailable_keyring)

    home.chmod(0o000)
    try:
        load, save = harness_auth._config_writer()
        assert load() == {}
        assert provider_config._config_path() == str(config_dir / "config.yaml")
        secrets.store_secret("isolated", "test-provider-key")
        settings = provider_config.provider_entry_settings(
            "isolated",
            {
                "kind": "key",
                "openai": {
                    "base_url": "https://provider.example.test/v1",
                    "api_key_ref": "keychain:isolated",
                },
            },
            make_default=True,
        )
        save(settings, deep_merge_providers=True)
        save({"host": {"name": "isolated-host"}}, deep_merge_providers=False)
        config = load()
        assert config["providers"] == settings["providers"]
        assert config["host"] == {"name": "isolated-host"}
        provider = provider_config.load_providers(config)["isolated"]
        assert provider.family("openai").api_key == "test-provider-key"
        assert (config_dir / "config.yaml").is_file()
        secret_file = config_dir / "secrets.json"
        assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600
        secret_file.chmod(0o644)
        secrets.store_secret("other", "test-other-key")
        assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600
        secrets.delete_secret("isolated")
        assert secrets._read_secrets_file() == {"other": "test-other-key"}
        if config_override:
            assert not state.exists()
    finally:
        home.chmod(0o700)
    assert not list(home.iterdir())


@pytest.mark.parametrize("override", ["OMNIGENT_DATA_DIR", "OMNIGENT_CONFIG_HOME"])
def test_migration_skips_home_probe_with_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, override: str
) -> None:
    monkeypatch.delenv("OMNIGENT_DATA_DIR", raising=False)
    monkeypatch.delenv("OMNIGENT_CONFIG_HOME", raising=False)
    monkeypatch.setenv(override, str(tmp_path / "isolated"))
    monkeypatch.setattr(cli, "_STATE_DIR", tmp_path / "forbidden-state")
    original_exists = Path.exists

    def forbidden_exists(path: Path) -> bool:
        if path == cli._STATE_DIR:
            raise AssertionError(f"Migration probed {path} despite an isolation override")
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", forbidden_exists)
    cli._migrate_legacy_state_dir()


def test_server_and_cli_start_with_inaccessible_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "inaccessible-home"
    home.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (state / "config.yaml").write_text("session_title_instructions: isolated-config\n")
    touched = tmp_path / "home-access"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(state))
    # The server helper owns its environment; install this scenario's fault
    # before importing the CLI, preserving its process lifecycle and cleanup.
    bootstrap = textwrap.dedent(f"""\
        import os
        from pathlib import Path
        from pytest import MonkeyPatch

        patch = MonkeyPatch()
        patch.setenv("HOME", {str(home)!r})
        patch.setenv("OMNIGENT_DATA_DIR", {str(state)!r})
        patch.delenv("OMNIGENT_CONFIG_HOME", raising=False)
        patch.delenv("OMNIGENT_ADMIN_CREDENTIALS_PATH", raising=False)
        original_stat = Path.stat

        def guarded_stat(path, *args, **kwargs):
            if path == Path({str(home)!r}) / ".omnigent" or path.is_relative_to(
                Path({str(home)!r}) / ".omnigent"
            ):
                Path({str(touched)!r}).write_text(str(path))
                raise PermissionError(f"Forbidden home-state probe: {{path}}")
            return original_stat(path, *args, **kwargs)

        patch.setattr(Path, "stat", guarded_stat)
        from omnigent.cli import main
        main()
        """)
    home.chmod(0o000)
    try:
        with isolated_local_server(
            tmp_path / "server",
            bootstrap=bootstrap,
            env={"OMNIGENT_SKIP_WEB_UI": "true"},
            cwd=tmp_path,
        ) as base_url:
            with httpx.Client(base_url=base_url, trust_env=False) as client:
                assert client.get("/health").status_code == 200
            result = subprocess.run(
                [sys.executable, "-c", bootstrap, "config", "list"],
                env=isolated_process_env(tmp_path / "cli-home", {}, None),
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            assert "isolated-config" in result.stdout
            assert not touched.exists(), touched.read_text() if touched.exists() else ""
    finally:
        home.chmod(0o700)


@pytest.mark.parametrize(
    ("module_name", "attribute", "suffix"),
    [
        ("cli", "_GLOBAL_CONFIG_PATH", "config.yaml"),
        ("cli", "_GLOBAL_AGENTS_DIR", "agents"),
        ("config", "global_config_path", "config.yaml"),
        ("onboarding.providers", "_catalog_cache_root", "model-catalog"),
        ("inner.egress.ca", "_default_cache_dir", "egress"),
        ("host.identity", "CONFIG_PATH", "config.yaml"),
        ("host.local_server", "_local_data_dir", "."),
        ("install_ledger", "state_dir", "."),
        ("models.model_catalog_store", "_data_dir", "."),
        ("runner._entry", "_runner_config_path", "config.yaml"),
        ("runner.identity", "_default_runner_id_path", "runners/runner_id"),
        ("server.dictation", "_asr_dir", "models/dictation/asr"),
        ("server.dictation", "_punct_dir", "models/dictation/punct"),
        ("update_check", "_CACHE_FILE", ".update_check.json"),
        ("harnesses.codex_native.bridge", "bridge_root", "codex-native"),
        ("harnesses.pi_native.bridge", "bridge_root", "pi-native"),
        ("harnesses.opencode_native.bridge", "bridge_root", "opencode-native"),
        ("harnesses.antigravity_native.bridge", "bridge_root", "antigravity-native"),
    ],
)
def test_state_paths_use_shared_data_dir(
    monkeypatch: pytest.MonkeyPatch, module_name: str, attribute: str, suffix: str
) -> None:
    monkeypatch.delenv("OMNIGENT_CONFIG_HOME", raising=False)
    monkeypatch.delenv("OMNIGENT_DICTATION_MODEL_DIR", raising=False)
    monkeypatch.delenv("OMNIGENT_DICTATION_PUNCT_DIR", raising=False)
    value = getattr(import_module(f"omnigent.{module_name}"), attribute)
    resolved = value() if callable(value) else value
    assert resolved == Path(os.environ["OMNIGENT_DATA_DIR"]) / suffix


def test_pytest_uses_an_isolated_omnigent_data_dir() -> None:
    """Daemon state used by tests must never resolve under the real home."""
    test_data_dir = Path(os.environ["OMNIGENT_DATA_DIR"]).resolve()
    real_data_dir = (Path.home() / ".omnigent").resolve()

    assert test_data_dir != real_data_dir
    assert local_server._LOCAL_SERVER_PID_PATH.resolve().is_relative_to(test_data_dir)
    assert cli._HOST_PID_PATH.resolve().is_relative_to(test_data_dir)
    assert cli._daemon_registry_dir().resolve().is_relative_to(test_data_dir)


def test_pytest_does_not_inherit_debug_log_delivery_credentials() -> None:
    """Pytest startup also isolates the log sink in processes spawned by tests."""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("OMNIGENT_DEBUG_LOG_")
    }
    env.update(
        OMNIGENT_DEBUG_LOG_CLIENT_ID="test-client",
        OMNIGENT_DEBUG_LOG_CLIENT_SECRET="test-secret",
        OMNIGENT_DEBUG_LOG_WORKSPACE_URL="https://workspace.example.test",
        OMNIGENT_DEBUG_LOG_ENDPOINT=(
            "https://123.zerobus.example.test/zerobus/v1/tables/catalog.schema.logs/insert"
        ),
    )
    check = "from omnigent.debug_logging import config_from_env; assert config_from_env() is None"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import tests.conftest; import subprocess, sys; "
            f"{check}; subprocess.run([sys.executable, '-c', {check!r}], check=True)",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
