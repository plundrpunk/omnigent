"""First-run theme selection, persistence and terminal restoration."""

import io
import os
import select
import termios
import tty
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from omnigent.repl import _theme_picker as picker


@pytest.fixture(autouse=True)
def isolated_os(monkeypatch):
    # Keep terminal mocks out of pytest/xdist's own I/O and size detection.
    monkeypatch.setattr(
        picker,
        "os",
        SimpleNamespace(read=os.read, write=os.write, get_terminal_size=os.get_terminal_size),
    )


@pytest.fixture
def terminal(monkeypatch):
    monkeypatch.setattr(
        picker,
        "sys",
        SimpleNamespace(
            stdin=SimpleNamespace(isatty=lambda: True, fileno=lambda: 17),
            stdout=SimpleNamespace(isatty=lambda: True, fileno=lambda: 18),
        ),
    )
    monkeypatch.setattr(termios, "tcgetattr", lambda fd: ["saved"])
    restore = Mock()
    monkeypatch.setattr(termios, "tcsetattr", restore)
    monkeypatch.setattr(tty, "setraw", Mock())
    monkeypatch.setattr(tty, "setcbreak", Mock())
    save = Mock()
    monkeypatch.setattr(picker, "update_user_config", save)
    return restore, save


@pytest.mark.parametrize(
    ("keys", "escaped", "expected"),
    [
        (["j", "\n"], [], "dark"),
        (["k", "\r"], [], "dark"),
        (["\x1b", "\n"], ["[", "A"], "dark"),
        (["\x1b", "\n"], ["[", "B"], "dark"),
        (["\x1b"], [None], "light"),
        (["\x03"], [], "light"),
        ([None], [], "light"),
        (["?", "\x1b", "\n"], ["X"], "light"),
    ],
)
def test_picker_navigation_saves_selection_and_restores_terminal(
    terminal, monkeypatch, keys, escaped, expected
):
    restore, save = terminal
    monkeypatch.setattr(picker, "_detect_terminal_background", lambda: None)
    monkeypatch.setattr(picker, "_read_raw_byte", Mock(side_effect=keys))
    monkeypatch.setattr(picker, "_read_raw_byte_timeout", Mock(side_effect=escaped))
    output = io.StringIO()
    result = picker.startup_theme_picker(out=output)
    assert result.name == expected
    save.assert_called_once_with(theme=expected)
    restore.assert_called_once_with(17, termios.TCSADRAIN, ["saved"])
    assert "Choose your theme" in output.getvalue()
    assert output.getvalue().endswith("\x1b[J")


@pytest.mark.parametrize("detected", ["dark", "light", None])
@pytest.mark.parametrize("broken_tty", [False, True])
def test_noninteractive_or_unavailable_tty_uses_detected_default(
    terminal, monkeypatch, detected, broken_tty
):
    restore, save = terminal
    monkeypatch.setattr(picker, "_detect_terminal_background", lambda: detected)
    if broken_tty:
        monkeypatch.setattr(termios, "tcgetattr", Mock(side_effect=termios.error("unavailable")))
    else:
        monkeypatch.setattr(picker.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    expected = "dark" if detected == "dark" else "light"
    output = io.StringIO()
    assert picker.startup_theme_picker(out=output).name == expected
    save.assert_called_once_with(theme=expected)
    restore.assert_not_called()
    assert output.getvalue() == ""


@pytest.mark.parametrize(
    "response,expected",
    [
        (b"\x1b]11;rgb:0000/0000/0000\x1b\\", "dark"),
        (b"\x1b]11;rgb:ffff/ffff/ffff\x07", "light"),
        (b"\x1b]11;rgb:00/00/00\x07", "dark"),
        (b"not an OSC response\x07", None),
        (b"\x1b]11;rgb:ff/ff\x07", None),
        (b"\x1b]11;rgb:zz/zz/zz\x07", None),
    ],
)
def test_background_detection_queries_terminal_and_restores(
    terminal, monkeypatch, response, expected
):
    restore, _ = terminal
    write = Mock()
    monkeypatch.setattr(picker.os, "write", write)
    monkeypatch.setattr(select, "select", Mock(return_value=([17], [], [])))
    monkeypatch.setattr(picker.os, "read", Mock(return_value=response))
    assert picker._detect_terminal_background() == expected
    write.assert_called_once_with(18, b"\x1b]11;?\x1b\\")
    restore.assert_called_once_with(17, termios.TCSADRAIN, ["saved"])


@pytest.mark.parametrize(
    "failure", ["timeout", "empty", "read_error", "raw_error", "not_tty", "read_timeout"]
)
def test_background_detection_failure_returns_unknown_and_restores(terminal, monkeypatch, failure):
    restore, _ = terminal
    monkeypatch.setattr(picker.os, "write", Mock())
    monkeypatch.setattr(select, "select", Mock(return_value=([17], [], [])))
    monkeypatch.setattr(picker.os, "read", Mock(return_value=b""))
    if failure == "timeout":
        monkeypatch.setattr(select, "select", Mock(return_value=([], [], [])))
    elif failure == "read_timeout":
        monkeypatch.setattr(select, "select", Mock(side_effect=[([17], [], []), ([], [], [])]))
    elif failure == "read_error":
        monkeypatch.setattr(picker.os, "read", Mock(side_effect=OSError("gone")))
    elif failure == "raw_error":
        monkeypatch.setattr(termios, "tcgetattr", Mock(side_effect=termios.error("gone")))
    elif failure == "not_tty":
        monkeypatch.setattr(picker.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    assert picker._detect_terminal_background() is None
    assert restore.call_count == (0 if failure in {"raw_error", "not_tty"} else 1)


@pytest.mark.parametrize(
    "size,expected", [(20, 40), (120, 120), (OSError(), 80), (ValueError(), 80)]
)
def test_terminal_width_is_bounded(monkeypatch, size, expected):
    monkeypatch.setattr(
        picker.os,
        "get_terminal_size",
        Mock(
            side_effect=size if isinstance(size, Exception) else None,
            return_value=SimpleNamespace(columns=size),
        ),
    )
    assert picker._term_width() == expected


def test_raw_byte_timeout_and_eof(monkeypatch):
    read_fd, write_fd = os.pipe()
    try:
        assert picker._read_raw_byte_timeout(read_fd, timeout=0) is None
        os.write(write_fd, b"j")
        assert picker._read_raw_byte_timeout(read_fd, timeout=0) == "j"
        os.close(write_fd)
        assert picker._read_raw_byte(read_fd) is None
    finally:
        os.close(read_fd)
    output = io.StringIO()
    picker._clear_picker(output, 0)
    assert output.getvalue() == ""
