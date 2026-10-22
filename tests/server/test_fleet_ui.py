"""Tests for the ``OMNIGENT_FLEET_UI`` web-look switch."""

from __future__ import annotations

import pytest

from omnigent.server.fleet_ui import FLEET_UI_ENV_VAR, fleet_ui_enabled


@pytest.mark.parametrize("value", ["1", "true", "TRUE", " yes ", "on"])
def test_truthy_values_select_the_fleet_look(value: str) -> None:
    assert fleet_ui_enabled({FLEET_UI_ENV_VAR: value}) is True


@pytest.mark.parametrize("environ", [{}, {FLEET_UI_ENV_VAR: ""}, {FLEET_UI_ENV_VAR: "0"}])
def test_unset_or_falsey_keeps_upstream_look(environ: dict[str, str]) -> None:
    assert fleet_ui_enabled(environ) is False


@pytest.mark.parametrize("value", ["false", "off", "fleet"])
def test_other_values_keep_upstream_look(value: str) -> None:
    assert fleet_ui_enabled({FLEET_UI_ENV_VAR: value}) is False


def test_reads_process_environment_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FLEET_UI_ENV_VAR, "1")
    assert fleet_ui_enabled() is True
    monkeypatch.delenv(FLEET_UI_ENV_VAR)
    assert fleet_ui_enabled() is False
