"""Discovery of coding-agent CLIs installed on *this* machine.

Omnigent already knows a great deal about *models* — :mod:`omnigent.model_catalog`
resolves providers and lists their models over HTTP, backed by the provider
catalogs under ``omnigent/onboarding/providers/model_catalog/``. What it has
never known is what is actually **installed on the box it is running on**.

That gap matters because the AMS bridge cannot fill it. ``AMS_BASE_URL``
normally points at a remote Automaton Memory System, so asking it "which
coding agents are available?" answers for the *remote* host, not this one.
Local capability has to be resolved locally, which is what this module does::

    PATH  +  well-known per-tool install dirs  ->  shutil.which

Every agent found is reported with its resolved path, version, and model
catalog. Agents that are absent are reported as ``installed=False`` rather
than as a path that cannot exist.

Model catalogs come from the tool itself wherever a listing command exists
(``opencode models``, ``ollama list``, ``aider --list-models``); the rest fall
back to a curated list, and ``accepts_arbitrary_model`` records whether a
free-form model string is still legal at spawn time.

Discovered agents are cross-referenced against
:data:`omnigent.native_coding_agents.NATIVE_CODING_AGENTS` so callers can tell
which of them Omnigent can already boot as a native TUI, and which are merely
present on disk.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from omnigent.native_coding_agents import native_coding_agent_for_terminal_name

logger = logging.getLogger(__name__)


#: Directories searched in addition to ``PATH``. Order matters: earlier wins.
#: Several vendors install outside ``PATH`` by default (grok, kimi), which is
#: why a bare :func:`shutil.which` against the inherited environment is not
#: enough.
_EXTRA_BIN_DIRS: tuple[str, ...] = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "~/.local/bin",
    "~/.npm-global/bin",
    "~/.bun/bin",
    "~/.cargo/bin",
    "~/.deno/bin",
    "~/.volta/bin",
    "~/go/bin",
    "~/.grok/bin",
    "~/.kimi-code/bin",
    "/opt/homebrew/opt/node/bin",
)

_PROBE_TIMEOUT_S = 20.0
_MODEL_PROBE_TIMEOUT_S = 30.0
_CATALOG_TTL_S = 300.0


def search_path() -> str:
    """Return ``PATH`` augmented with the install dirs that exist here.

    :returns: An ``os.pathsep``-joined search path.
    """
    parts: list[str] = []
    for raw in _EXTRA_BIN_DIRS:
        candidate = Path(os.path.expanduser(raw))
        if candidate.is_dir():
            parts.append(str(candidate))
    parts.append(os.environ.get("PATH", ""))
    return os.pathsep.join(part for part in parts if part)


@dataclass(frozen=True)
class ModelEntry:
    """One selectable model for a coding agent."""

    id: str
    label: str
    #: ``"probe"`` when the CLI itself reported it, ``"static"`` when curated.
    source: str = "static"


@dataclass(frozen=True)
class CodingAgentSpec:
    """How to find one coding-agent CLI and enumerate its models."""

    key: str
    display_name: str
    binary: str
    vendor: str
    version_args: tuple[str, ...] = ("--version",)
    #: Flag used to pin a model at spawn. Empty when the model is positional.
    model_flag: str = "--model"
    #: ``"command"`` (ask the CLI) or ``"static"`` (curated list).
    models_strategy: str = "static"
    models_command: tuple[str, ...] = ()
    static_models: tuple[ModelEntry, ...] = ()
    login_command: str = ""
    #: ``"native"``, ``"adapter"`` or ``"none"`` — Agent Client Protocol support.
    acp: str = "none"
    acp_note: str = ""
    accepts_arbitrary_model: bool = True
    notes: str = ""


def _model(model_id: str, label: str | None = None) -> ModelEntry:
    return ModelEntry(id=model_id, label=label or model_id, source="static")


#: The registry. Adding an agent is one entry here; discovery, the model
#: catalog and the ``/v1/coding-agents`` payload all follow from it.
CODING_AGENT_SPECS: tuple[CodingAgentSpec, ...] = (
    CodingAgentSpec(
        key="claude",
        display_name="Claude Code",
        binary="claude",
        vendor="Anthropic",
        model_flag="--model",
        static_models=(
            _model("opus", "Claude Opus (alias)"),
            _model("sonnet", "Claude Sonnet (alias)"),
            _model("haiku", "Claude Haiku (alias)"),
        ),
        login_command="claude auth login",
        acp="adapter",
        acp_note="via claude-agent-acp",
        notes="Aliases track the latest model; full ids also accepted.",
    ),
    CodingAgentSpec(
        key="codex",
        display_name="Codex CLI",
        binary="codex",
        vendor="OpenAI",
        model_flag="-m",
        static_models=(
            _model("gpt-5.3-codex"),
            _model("gpt-5.2-codex"),
            _model("gpt-5.1-codex"),
            _model("gpt-5.1-codex-max"),
            _model("gpt-5.1-codex-mini"),
            _model("gpt-5-codex"),
        ),
        login_command="codex login",
        acp="adapter",
        acp_note="via codex-acp",
    ),
    CodingAgentSpec(
        key="gemini",
        display_name="Gemini CLI",
        binary="gemini",
        vendor="Google",
        model_flag="-m",
        static_models=(
            _model("gemini-3.1-pro"),
            _model("gemini-3.1-flash"),
            _model("gemini-2.5-pro"),
            _model("gemini-2.5-flash"),
        ),
        login_command="gemini auth login",
        acp="native",
        notes=(
            "The individual Code Assist tier was discontinued for this client; "
            "expect an eligibility error until migrated or API-keyed."
        ),
    ),
    CodingAgentSpec(
        key="kimi",
        display_name="Kimi Code",
        binary="kimi",
        vendor="Moonshot",
        model_flag="-m",
        static_models=(
            _model("kimi-code/k3", "Kimi K3"),
            _model("kimi-code/k2.5", "Kimi K2.5"),
            _model("kimi-code/k2", "Kimi K2"),
        ),
        login_command="kimi login",
        notes="Default model is read from ~/.kimi-code/config.toml.",
    ),
    CodingAgentSpec(
        key="grok",
        display_name="Grok CLI",
        binary="grok",
        vendor="xAI",
        model_flag="-m",
        static_models=(
            _model("grok-code-fast-1", "Grok Code Fast"),
            _model("grok-4", "Grok 4"),
            _model("grok-4-fast", "Grok 4 Fast"),
        ),
        login_command="set GROK_API_KEY or configure ~/.grok/user-settings.json",
    ),
    CodingAgentSpec(
        key="copilot",
        display_name="GitHub Copilot CLI",
        binary="copilot",
        vendor="GitHub",
        model_flag="--model",
        static_models=(
            _model("auto", "Auto (Copilot picks)"),
            _model("gpt-5.4"),
            _model("gpt-5.3-codex"),
            _model("claude-opus-4-5"),
            _model("claude-sonnet-4-5"),
        ),
        login_command="copilot login",
    ),
    CodingAgentSpec(
        key="opencode",
        display_name="opencode",
        binary="opencode",
        vendor="opencode",
        model_flag="--model",
        models_strategy="command",
        models_command=("models",),
        login_command="opencode auth login",
        acp="native",
        notes=(
            "Aggregates its own routes plus Fireworks, Ollama, OpenAI and "
            "Anthropic. ACP-native, so it can be driven over stdio JSON-RPC "
            "rather than a hand-written TUI hook."
        ),
    ),
    CodingAgentSpec(
        key="ollama",
        display_name="Ollama",
        binary="ollama",
        vendor="Local",
        model_flag="",  # positional: `ollama run <model>`
        models_strategy="command",
        models_command=("list",),
        accepts_arbitrary_model=False,
        notes="Local inference; the model is a positional argument, not a flag.",
    ),
    CodingAgentSpec(
        key="aider",
        display_name="Aider",
        binary="aider",
        vendor="Aider",
        model_flag="--model",
        models_strategy="command",
        models_command=("--no-check-update", "--yes", "--list-models"),
        login_command="set the provider API key aider expects",
        notes=(
            "Interactive-first: pass --no-check-update --yes in automation and "
            "run in a writable cwd. Its model list is litellm's full catalog, "
            "so expect ~1200 entries and filter before display."
        ),
    ),
)

SPECS_BY_KEY: dict[str, CodingAgentSpec] = {s.key: s for s in CODING_AGENT_SPECS}


async def _run(
    path: str,
    args: Sequence[str],
    timeout: float = _PROBE_TIMEOUT_S,
    cwd: str | None = None,
) -> tuple[int, str, str]:
    """Run *path* with *args* under a hard timeout.

    :param path: Absolute path to the binary.
    :param args: Arguments to pass.
    :param timeout: Seconds before the child is killed.
    :param cwd: Working directory, when the tool insists on a writable one.
    :returns: ``(returncode, stdout, stderr)``; ``124`` on timeout.
    """
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)  # don't leak a parent Claude session
    env["PATH"] = search_path()
    env.setdefault("NO_COLOR", "1")
    try:
        proc = await asyncio.create_subprocess_exec(
            path,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            env=env,
            cwd=cwd,
        )
    except (OSError, ValueError) as exc:
        return 127, "", str(exc)

    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return 124, "", f"timed out after {timeout}s"
    return (
        proc.returncode or 0,
        out.decode(errors="replace").strip(),
        err.decode(errors="replace").strip(),
    )


def resolve_binary(spec: CodingAgentSpec) -> str | None:
    """Locate *spec*'s binary on this machine.

    An explicit ``<KEY>_CLI_PATH`` environment override wins; otherwise ``PATH``
    plus :data:`_EXTRA_BIN_DIRS` is searched.

    :param spec: The agent to locate.
    :returns: An absolute path, or ``None`` when not installed.
    """
    override = os.environ.get(f"{spec.key.upper()}_CLI_PATH", "").strip()
    if override:
        candidate = Path(os.path.expanduser(override))
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
        return None
    return shutil.which(spec.binary, path=search_path())


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _clean(line: str) -> str:
    return _ANSI_RE.sub("", line).strip()


def _parse_models(spec: CodingAgentSpec, stdout: str) -> list[ModelEntry]:
    """Parse a listing command's stdout into model entries."""
    lines = [c for c in (_clean(x) for x in stdout.splitlines()) if c]
    entries: list[ModelEntry] = []

    if spec.key == "ollama":
        # Tabular: NAME  ID  SIZE  MODIFIED — drop the header row.
        for line in lines:
            if line.upper().startswith("NAME"):
                continue
            name = line.split()[0]
            if name:
                entries.append(ModelEntry(id=name, label=name, source="probe"))
        return entries

    if spec.key == "aider":
        # "- model-name" bullets beneath a "Models which match ..." header.
        for line in lines:
            if line.startswith("- "):
                model_id = line[2:].strip()
                if model_id:
                    entries.append(
                        ModelEntry(id=model_id, label=model_id, source="probe")
                    )
        return entries

    # Default (opencode and anything else): one bare id per line.
    for line in lines:
        if " " in line or "\t" in line:
            continue
        entries.append(ModelEntry(id=line, label=line, source="probe"))
    return entries


_catalog_cache: dict[str, tuple[float, list[ModelEntry]]] = {}


def clear_coding_agent_cache() -> None:
    """Drop cached model listings so the next scan re-probes."""
    _catalog_cache.clear()


async def probe_models(
    spec: CodingAgentSpec,
    path: str,
    *,
    filter_term: str = "",
    use_cache: bool = True,
) -> list[ModelEntry]:
    """Ask the agent which models it can run, falling back to the curated list.

    :param spec: The agent to probe.
    :param path: Resolved binary path.
    :param filter_term: Listing filter, for CLIs that require one.
    :param use_cache: Whether to honour the TTL cache.
    :returns: Model entries, never empty when a static list exists.
    """
    cache_key = f"{spec.key}:{filter_term}"
    if use_cache:
        cached = _catalog_cache.get(cache_key)
        if cached and (time.time() - cached[0]) < _CATALOG_TTL_S:
            return cached[1]

    models: list[ModelEntry] = []
    if spec.models_strategy == "command" and spec.models_command:
        args = list(spec.models_command)
        if spec.key == "aider":
            args.append(filter_term or ".")  # the flag requires a filter
        cwd = "/tmp" if spec.key == "aider" else None
        code, out, err = await _run(
            path, args, timeout=_MODEL_PROBE_TIMEOUT_S, cwd=cwd
        )
        if code == 0 and out:
            models = _parse_models(spec, out)
        else:
            logger.warning(
                "coding agent %s model probe failed (exit %s): %s",
                spec.key,
                code,
                (err or out)[:200],
            )

    if not models:
        models = list(spec.static_models)

    if use_cache:
        _catalog_cache[cache_key] = (time.time(), models)
    return models


async def probe_version(spec: CodingAgentSpec, path: str) -> str | None:
    """Return the agent's reported version, or ``None`` when it will not say."""
    code, out, err = await _run(path, list(spec.version_args))
    if code != 0:
        return None
    for raw in (out or err).splitlines():
        cleaned = _clean(raw)
        if cleaned:
            return cleaned
    return None


async def _no_models() -> list[ModelEntry]:
    return []


async def discover_agent(
    spec: CodingAgentSpec, *, with_models: bool = True
) -> dict[str, Any]:
    """Build the full record for one coding agent on this machine.

    :param spec: The agent to inspect.
    :param with_models: Whether to enumerate models (the slow part).
    :returns: A JSON-shaped record.
    """
    path = resolve_binary(spec)
    native = native_coding_agent_for_terminal_name(spec.key)
    record: dict[str, Any] = {
        "key": spec.key,
        "display_name": spec.display_name,
        "vendor": spec.vendor,
        "installed": bool(path),
        "path": path,
        "version": None,
        "model_flag": spec.model_flag,
        "models": [],
        "models_strategy": spec.models_strategy,
        "accepts_arbitrary_model": spec.accepts_arbitrary_model,
        "login_command": spec.login_command,
        "acp": spec.acp,
        "acp_note": spec.acp_note,
        # Whether Omnigent can already boot this as a native TUI session.
        "native_harness": native.harness if native else None,
        "native_agent_name": native.agent_name if native else None,
        "notes": spec.notes,
    }
    if not path:
        return record

    version, models = await asyncio.gather(
        probe_version(spec, path),
        probe_models(spec, path) if with_models else _no_models(),
    )
    record["version"] = version
    record["models"] = [asdict(m) for m in models]
    return record


async def discover_coding_agents(*, with_models: bool = True) -> dict[str, Any]:
    """Scan this machine for every known coding agent, concurrently.

    :param with_models: Whether to enumerate each agent's models.
    :returns: A JSON-shaped payload with counts and per-agent records.
    """
    started = time.time()
    results = await asyncio.gather(
        *(discover_agent(spec, with_models=with_models) for spec in CODING_AGENT_SPECS),
        return_exceptions=True,
    )

    agents: list[dict[str, Any]] = []
    for spec, result in zip(CODING_AGENT_SPECS, results):
        if isinstance(result, BaseException):
            logger.warning("coding agent %s discovery failed: %s", spec.key, result)
            agents.append(
                {
                    "key": spec.key,
                    "display_name": spec.display_name,
                    "installed": False,
                    "path": None,
                    "error": str(result),
                    "models": [],
                }
            )
        else:
            agents.append(result)

    installed = [a for a in agents if a.get("installed")]
    return {
        "host": os.uname().nodename if hasattr(os, "uname") else "",
        "scanned_at": started,
        "duration_ms": round((time.time() - started) * 1000),
        "counts": {
            "known": len(CODING_AGENT_SPECS),
            "installed": len(installed),
            "native": len([a for a in installed if a.get("native_harness")]),
            "models": sum(len(a.get("models") or []) for a in installed),
        },
        "agents": agents,
    }
