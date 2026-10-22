"""Deployment switch between upstream's web look and the fleet (Automaton OS) look.

``OMNIGENT_FLEET_UI`` is read once when the app is built and advertised to the
SPA as ``fleet_ui`` on ``GET /v1/info``. When it is off (the default) the web UI
renders exactly as upstream: system theme, Otto starfish branding, and the
pinned-tab workspace rail. When it is on, the SPA uses the fleet look: dark
default theme, the wolf mascot with "Automaton OS" naming, and a Run tab that is
always present.

The switch is deliberately separate from ``OMNIGENT_FEATURES``: release features
are advertised as an exact map that upstream pins in tests, and this is a fork
presentation choice rather than a temporary release gate.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

FLEET_UI_ENV_VAR = "OMNIGENT_FLEET_UI"
_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})


def fleet_ui_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Return whether ``OMNIGENT_FLEET_UI`` selects the fleet web look.

    :param environ: Environment mapping; defaults to :data:`os.environ`.
    :returns: True for ``1``/``true``/``yes``/``on`` (any case); False when
        unset, empty, or any other value.
    """
    source = os.environ if environ is None else environ
    return source.get(FLEET_UI_ENV_VAR, "").strip().lower() in _TRUTHY_VALUES
