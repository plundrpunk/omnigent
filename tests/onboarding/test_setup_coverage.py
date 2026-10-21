"""Profile onboarding workflows with isolated config files and fake OAuth."""

from __future__ import annotations

import configparser
import io
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from rich.console import Console

from omnigent.onboarding import setup as s


@pytest.fixture
def profiles(monkeypatch, tmp_path):
    monkeypatch.setattr(s, "sys", SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: False)))
    monkeypatch.setattr(s, "time", SimpleNamespace(sleep=s.time.sleep))
    monkeypatch.setattr(
        s,
        "subprocess",
        SimpleNamespace(run=s.subprocess.run, TimeoutExpired=s.subprocess.TimeoutExpired),
    )
    specs = tuple(
        s.ProfileSpec(name, f"https://{name}.example", name, True)
        for name in ("ready", "alias", "fresh", "wrong")
    )
    module = ModuleType("omnigent.onboarding.internal_beta")
    module.DEFAULT_PROFILES = specs
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(tmp_path / ".databrickscfg"))
    monkeypatch.delenv(s.SKIP_ENV_VAR, raising=False)
    monkeypatch.setattr(s, "find_databricks_cli", lambda: "/fake/databricks")
    return specs


def test_classification_distinguishes_ready_alias_oauth_and_collision(profiles):
    ready, alias, fresh, wrong = profiles
    actions = s._compute_actions(
        {"ready": ready.host + "/", "old-alias": alias.host, "wrong": "https://old.example"}
    )
    assert actions.ready == (ready,)
    assert actions.aliasable == (("old-alias", alias),)
    assert actions.oauth == (fresh,)
    assert actions.wrong_host == ((wrong, "https://old.example"),)


def test_profile_alias_and_removal_preserve_other_sections(profiles):
    path = s._databrickscfg_path()
    path.write_text(
        "[source]\nhost=https://source.example\nauth_type=databricks-cli\n[keep]\nhost=https://keep.example\n"
    )
    s._alias_profile("source", "alias")
    cfg = configparser.ConfigParser()
    cfg.read(path)
    assert dict(cfg["alias"]) == dict(cfg["source"])
    assert s._remove_profile_section("alias") is True
    assert s._remove_profile_section("alias") is False
    cfg = configparser.ConfigParser()
    cfg.read(path)
    assert cfg.sections() == ["source", "keep"]
    assert not path.with_name(path.name + ".write").exists()
    with pytest.raises(ValueError, match="alias source"):
        s._alias_profile("missing", "new")


def test_remove_absent_config_is_noop(profiles):
    assert s._remove_profile_section("missing") is False
    assert not s._databrickscfg_path().exists()


@pytest.mark.parametrize("result,expected", [(0, True), (1, False), (KeyboardInterrupt(), False)])
def test_login_reports_failure_and_only_delays_success(monkeypatch, profiles, result, expected):
    run = Mock(
        side_effect=result if isinstance(result, BaseException) else None,
        return_value=SimpleNamespace(returncode=result),
    )
    monkeypatch.setattr(s.subprocess, "run", run)
    sleep = Mock()
    monkeypatch.setattr(s.time, "sleep", sleep)
    spec = profiles[0]
    assert s._login_profile("/fake/db", spec, Console(file=io.StringIO())) is expected
    run.assert_called_once_with(
        ["/fake/db", "auth", "login", "--host", spec.host, "--profile", spec.name], check=False
    )
    if expected:
        sleep.assert_called_once_with(s._OAUTH_BETWEEN_LOGIN_SLEEP_SECONDS)
    else:
        sleep.assert_not_called()


def test_alias_failures_do_not_stop_remaining_aliases(monkeypatch, profiles):
    alias = Mock(side_effect=[ValueError("missing"), OSError("read only"), None])
    monkeypatch.setattr(s, "_alias_profile", alias)
    output = io.StringIO()
    pairs = tuple(("source", p) for p in profiles[:3])
    assert s._apply_silent_aliases(pairs, Console(file=output)) == 1
    assert alias.call_count == 3
    assert "could not alias" in output.getvalue()


@pytest.mark.parametrize(
    "answer,tty,overwrite",
    [
        ("yes", True, True),
        ("n", True, False),
        (EOFError(), True, False),
        (KeyboardInterrupt(), True, False),
        ("yes", False, False),
    ],
)
def test_wrong_host_only_replaced_after_explicit_consent(
    monkeypatch, profiles, answer, tty, overwrite
):
    monkeypatch.setattr(s.sys.stdin, "isatty", lambda: tty)
    monkeypatch.setattr(
        "builtins.input",
        Mock(
            side_effect=answer if isinstance(answer, BaseException) else None, return_value=answer
        ),
    )
    events = []
    monkeypatch.setattr(s, "_remove_profile_section", lambda name: events.append(("remove", name)))
    monkeypatch.setattr(
        s, "_login_profile", lambda cli, spec, console: events.append(("login", spec.name))
    )
    s._handle_wrong_host(
        "/fake/db", profiles[0], "https://old.example", Console(file=io.StringIO())
    )
    assert events == ([("remove", "ready"), ("login", "ready")] if overwrite else [])


@pytest.mark.parametrize("success", [False, True])
def test_onboarding_rechecks_actual_profiles_after_alias_repair_and_login(
    monkeypatch, profiles, success
):
    ready, alias, _fresh, wrong = profiles
    hosts = {ready.name: ready.host, "old-alias": alias.host, wrong.name: "https://old.example"}
    monkeypatch.setattr(s, "_existing_profile_hosts", lambda: dict(hosts))
    alias_calls = []

    def alias_profile(source, target):
        alias_calls.append((source, target))
        hosts[target] = hosts[source]

    monkeypatch.setattr(s, "_alias_profile", alias_profile)
    repairs = []

    def repair(cli, spec, current, console):
        repairs.append(spec.name)
        hosts[spec.name] = spec.host

    monkeypatch.setattr(s, "_handle_wrong_host", repair)
    logins = []

    def login(cli, spec, console):
        logins.append(spec.name)
        if success:
            hosts[spec.name] = spec.host
        return success

    monkeypatch.setattr(s, "_login_profile", login)
    assert s.run_onboarding() is success
    assert alias_calls == [("old-alias", "alias")]
    assert repairs == ["wrong"]
    assert logins == ["fresh"]


@pytest.mark.parametrize("mode,expected", [("skip", False), ("no_cli", False), ("ready", True)])
def test_onboarding_early_exit(monkeypatch, profiles, mode, expected):
    if mode == "skip":
        monkeypatch.setenv(s.SKIP_ENV_VAR, "1")
    elif mode == "no_cli":
        monkeypatch.setattr(s, "find_databricks_cli", lambda: None)
    hosts = Mock(return_value={p.name: p.host for p in profiles})
    monkeypatch.setattr(s, "_existing_profile_hosts", hosts)
    login = Mock()
    monkeypatch.setattr(s, "_login_profile", login)
    assert s.run_onboarding() is expected
    login.assert_not_called()
    if mode != "ready":
        hosts.assert_not_called()


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("", True),
        ("y", True),
        ("yes", True),
        ("n", False),
        (EOFError(), False),
        (KeyboardInterrupt(), False),
    ],
)
def test_preflight_only_runs_onboarding_with_consent(monkeypatch, profiles, answer, expected):
    monkeypatch.setattr(s.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(s, "_existing_profile_hosts", lambda: {"wrong": "https://old.example"})
    monkeypatch.setattr(
        "builtins.input",
        Mock(
            side_effect=answer if isinstance(answer, BaseException) else None, return_value=answer
        ),
    )
    run = Mock()
    monkeypatch.setattr(s, "run_onboarding", run)
    s.maybe_run_onboarding()
    assert run.call_count == int(expected)


def test_preflight_silent_aliases_avoid_prompt_when_they_complete_setup(monkeypatch, profiles):
    monkeypatch.setattr(s.sys.stdin, "isatty", lambda: True)
    hosts = {p.name: p.host for p in profiles}
    hosts["existing"] = hosts.pop("alias")
    monkeypatch.setattr(s, "_existing_profile_hosts", lambda: dict(hosts))
    monkeypatch.setattr(
        s, "_alias_profile", lambda source, target: hosts.update({target: hosts[source]})
    )
    prompt = Mock(side_effect=AssertionError("all profiles are ready"))
    monkeypatch.setattr("builtins.input", prompt)
    s.maybe_run_onboarding()
    assert hosts["alias"] == profiles[1].host
    s.maybe_run_onboarding()
    prompt.assert_not_called()
