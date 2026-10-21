"""Terminal picker navigation with deterministic key streams and restoration checks."""

from __future__ import annotations

import io
import os
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from omnigent.onboarding import interactive as i


@pytest.fixture(autouse=True)
def isolated_terminal_modules(monkeypatch):
    # The test runner must retain the real process-wide I/O functions.
    monkeypatch.setattr(
        i, "os", SimpleNamespace(read=os.read, get_terminal_size=os.get_terminal_size)
    )
    monkeypatch.setattr(
        i, "sys", SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: False), stdout=sys.stdout)
    )


@pytest.fixture
def terminal(monkeypatch):
    import select
    import termios
    import tty

    out = io.StringIO()
    monkeypatch.setattr(i.sys, "stdin", SimpleNamespace(isatty=lambda: True, fileno=lambda: 123))
    monkeypatch.setattr(i.sys, "stdout", out)
    monkeypatch.setattr(termios, "tcgetattr", lambda fd: ["original"])
    restore = Mock()
    monkeypatch.setattr(termios, "tcsetattr", restore)
    monkeypatch.setattr(tty, "setcbreak", Mock())
    monkeypatch.setattr(select, "select", lambda *args: ([123], [], []))

    def supply(keys, *, escape=False):
        monkeypatch.setattr(i.sys, "stdout", out)
        monkeypatch.setattr(i.os, "read", Mock(side_effect=keys))
        if escape:
            monkeypatch.setattr(select, "select", lambda *args: ([], [], []))
        return out, restore

    return supply


@pytest.mark.parametrize(
    "keys,expected",
    [
        ([b"j", b"\r"], 3),
        ([b"k", b"\n"], 3),
        ([b"\x1b", b"[", b"B", b"\r"], 3),
        ([b"\x1b", b"[", b"A", b"\r"], 3),
        ([b"J", b"K", b"\r"], 0),
        ([b"\x1b", b"x", b"z", b""], 0),
    ],
)
def test_picker_skips_detail_rows_wraps_and_restores(terminal, keys, expected):
    out, restore = terminal(keys)
    result = i.select(
        "Choose",
        ["Alpha", "[green]default A[/]", "extra A", "Beta", "default B"],
        selectable=[True, False, False, True, False],
        descriptions=["Description", "", "", "Beta description", ""],
        clear_on_exit=True,
        status="Added Alpha",
    )
    assert result == expected
    assert "Added Alpha" in out.getvalue()
    assert "default A" in out.getvalue()
    assert out.getvalue().endswith("\033[J")
    restore.assert_called_once()
    assert restore.call_args.args[2] == ["original"]


@pytest.mark.parametrize("key", [b"\x1b", b"\x03", b"\x04"])
def test_picker_cancel_returns_minus_one(terminal, key):
    _, restore = terminal([key], escape=True)
    assert i.select("Choose", ["A"]) == -1
    restore.assert_called_once()


def test_cannot_enter_raw_mode_uses_numbered_fallback(monkeypatch, terminal):
    import termios

    terminal([])
    monkeypatch.setattr(termios, "tcgetattr", Mock(side_effect=termios.error("no tty")))
    monkeypatch.setattr(i.click, "prompt", Mock(return_value="2"))
    assert i.select("Choose", ["A", "B"]) == 1


def test_terminal_read_failure_restores_attributes(terminal, monkeypatch):
    _, restore = terminal([])
    monkeypatch.setattr(i.os, "read", Mock(side_effect=OSError("disconnected")))
    with pytest.raises(OSError, match="disconnected"):
        i.select("Choose", ["A"])
    restore.assert_called_once()


@pytest.mark.parametrize(
    "size,expected", [(20, 40), (120, 120), (OSError(), 80), (ValueError(), 80)]
)
def test_terminal_width_clamps_or_falls_back(monkeypatch, size, expected):
    monkeypatch.setattr(
        i.os,
        "get_terminal_size",
        Mock(
            side_effect=size if isinstance(size, Exception) else None,
            return_value=SimpleNamespace(columns=size),
        ),
    )
    assert i._term_width() == expected


def test_clear_screen_only_writes_escape_sequence_for_tty(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(i.sys, "stdout", out)
    i.clear_screen()
    assert out.getvalue() == ""
    monkeypatch.setattr(out, "isatty", lambda: True)
    i.clear_screen()
    assert out.getvalue() == "\033[2J\033[3J\033[H"


def test_nonselectable_default_moves_to_first_choice(terminal):
    terminal([b"\r"])
    assert i.select("Choose", ["Header", "A", "B"], default=0, selectable=[False, True, True]) == 1
