"""Security properties of the goal bridge that the route tests do not cover.

Each test here pins a finding from the #2 review: secret selection, owner
scoping, artifact isolation and the sandbox policy.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from omnigent.server.routes import goal as goal_mod


def _enable_exec(monkeypatch, tmp_path) -> None:
    """Execution is refused outright unless the server names writable roots."""
    monkeypatch.setenv("HA_GOAL_EXEC_ROOTS", str(tmp_path))


# --- secret and endpoint selection -----------------------------------------


def test_an_arbitrary_env_var_cannot_be_selected_as_the_key(monkeypatch):
    """The exfiltration primitive: name any secret, pair it with any URL."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-do-not-send-this")
    monkeypatch.delenv(goal_mod._API_KEY_ENV_ALLOWLIST_ENV, raising=False)

    with pytest.raises(HTTPException) as excinfo:
        goal_mod._validate_endpoint({"api_key_env": "ANTHROPIC_API_KEY"})

    assert excinfo.value.status_code == 422
    assert "allowlisted" in str(excinfo.value.detail)


def test_existing_in_the_environment_is_not_enough(monkeypatch):
    """Every secret exists in os.environ; existence is not authorisation."""
    monkeypatch.setenv("SOME_OTHER_KEY", "value")
    monkeypatch.setenv(goal_mod._API_KEY_ENV_ALLOWLIST_ENV, "HA_API_KEY")

    with pytest.raises(HTTPException):
        goal_mod._validate_endpoint({"api_key_env": "SOME_OTHER_KEY"})


def test_the_refusal_does_not_reveal_whether_the_var_exists(monkeypatch):
    """Otherwise a caller enumerates the server's secrets by name."""
    monkeypatch.setenv("REAL_SECRET", "value")
    monkeypatch.setenv(goal_mod._API_KEY_ENV_ALLOWLIST_ENV, "HA_API_KEY")

    real = pytest.raises(HTTPException)
    with real as a:
        goal_mod._validate_endpoint({"api_key_env": "REAL_SECRET"})
    with pytest.raises(HTTPException) as b:
        goal_mod._validate_endpoint({"api_key_env": "NOT_A_REAL_VAR"})

    assert str(a.value.detail) == str(b.value.detail)


def test_an_allowlisted_key_is_accepted(monkeypatch):
    monkeypatch.setenv("HA_API_KEY", "value")
    monkeypatch.setenv(goal_mod._API_KEY_ENV_ALLOWLIST_ENV, "HA_API_KEY,OTHER")

    assert goal_mod._validate_endpoint({"api_key_env": "HA_API_KEY"}) == {
        "api_key_env": "HA_API_KEY"
    }


def test_an_arbitrary_base_url_cannot_be_selected(monkeypatch):
    monkeypatch.delenv(goal_mod._BASE_URL_ALLOWLIST_ENV, raising=False)

    with pytest.raises(HTTPException) as excinfo:
        goal_mod._validate_endpoint({"base_url": "https://attacker.example"})

    assert excinfo.value.status_code == 422


def test_an_allowlisted_base_url_is_accepted(monkeypatch):
    monkeypatch.setenv(goal_mod._BASE_URL_ALLOWLIST_ENV, "http://localhost:11434")

    assert goal_mod._validate_endpoint({"base_url": "http://localhost:11434"}) == {
        "base_url": "http://localhost:11434"
    }


# --- sandbox policy ---------------------------------------------------------


def test_subprocess_sandbox_is_refused_by_default(monkeypatch, tmp_path):
    _enable_exec(monkeypatch, tmp_path)
    monkeypatch.delenv(goal_mod._ALLOW_SUBPROCESS_ENV, raising=False)

    with pytest.raises(HTTPException) as excinfo:
        goal_mod._validate_exec(
            {"command": "automaton", "sandbox": "subprocess", "workspace": str(tmp_path)}
        )

    assert excinfo.value.status_code == 422
    assert "Docker" in str(excinfo.value.detail)


def test_the_default_sandbox_is_docker(monkeypatch, tmp_path):
    _enable_exec(monkeypatch, tmp_path)

    assert goal_mod._DEFAULT_EXEC_SANDBOX == "docker"
    spec = goal_mod._validate_exec({"command": "automaton", "workspace": str(tmp_path)})
    assert spec["sandbox"] == "docker"


def test_subprocess_is_available_when_the_server_opts_in(monkeypatch, tmp_path):
    _enable_exec(monkeypatch, tmp_path)
    monkeypatch.setenv(goal_mod._ALLOW_SUBPROCESS_ENV, "1")

    spec = goal_mod._validate_exec(
        {"command": "automaton", "sandbox": "subprocess", "workspace": str(tmp_path)}
    )

    assert spec["sandbox"] == "subprocess"


# --- owner scoping ----------------------------------------------------------


def _run(run_id: str, owner: str | None) -> dict:
    return {"run_id": run_id, "owner": owner, "status": "running", "started_at": "2026-01-01"}


def test_a_run_is_not_readable_by_another_user():
    registry = goal_mod.GoalRunRegistry()
    registry.create(_run("r1", "alice"))

    assert registry.get_for("r1", "alice") is not None
    assert registry.get_for("r1", "bob") is None


def test_a_missing_run_and_someone_elses_run_are_indistinguishable():
    """Otherwise the endpoint confirms another user's run exists."""
    registry = goal_mod.GoalRunRegistry()
    registry.create(_run("r1", "alice"))

    assert registry.get_for("r1", "bob") is None
    assert registry.get_for("does-not-exist", "bob") is None


def test_listing_shows_only_your_own_runs():
    registry = goal_mod.GoalRunRegistry()
    registry.create(_run("r1", "alice"))
    registry.create(_run("r2", "bob"))

    assert [r["run_id"] for r in registry.list(owner="alice")] == ["r1"]
    assert [r["run_id"] for r in registry.list(owner="bob")] == ["r2"]
    # Single-user mode (owner=None) is unrestricted.
    assert len(registry.list()) == 2


# --- artifact isolation -----------------------------------------------------


def test_two_runs_of_the_same_goal_do_not_share_a_workdir():
    a = goal_mod._run_workdir("run-a")
    b = goal_mod._run_workdir("run-b")

    assert a != b
    assert a.name == "run-a" and b.name == "run-b"


def test_events_come_from_the_runs_own_artifact_dir(tmp_path, monkeypatch):
    """The old global 'newest artifact' fallback could serve another run's
    events entirely, including another user's."""
    monkeypatch.setenv("HA_GOAL_WORKDIR", str(tmp_path))
    monkeypatch.setenv("HA_GOAL_CWD", str(tmp_path))

    mine = goal_mod._run_workdir("mine") / "artifacts"
    theirs = goal_mod._run_workdir("theirs") / "artifacts"
    mine.mkdir(parents=True)
    theirs.mkdir(parents=True)
    (mine / "a.json").write_text("{}")
    # Newer, and would win a global mtime scan.
    (theirs / "b.json").write_text("{}")

    found = goal_mod._artifact_for({"run_id": "mine"})

    assert found is not None
    assert Path(found).parent == mine
