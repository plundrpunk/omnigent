"""Tests for the AMS bridge write table (``/v1/ams/*`` mutations, P1).

The table is method + exact path: the four Models & Assignment routes
forward (with the API key attached server-side and the write logged),
everything else — wrong method, prefix rides, traversal — answers 403.
The AMS itself is faked at the httpx layer so tests see exactly what
would go over the wire.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

_captured: list[dict[str, Any]] = []


@pytest.fixture()
def ams_env(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Configure the bridge and capture outbound AMS requests."""
    monkeypatch.setenv("AMS_BASE_URL", "http://ams.test")
    monkeypatch.setenv("AMS_API_KEY", "sekret")
    _captured.clear()

    real_async_client = httpx.AsyncClient

    class _FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            self._kwargs = kwargs

        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
            _captured.append(
                {
                    "method": method,
                    "url": url,
                    "json": kwargs.get("json"),
                    "headers": kwargs.get("headers") or {},
                }
            )
            return httpx.Response(200, json={"ok": True, "echo": kwargs.get("json")})

        async def get(self, url: str, **kwargs: Any) -> httpx.Response:
            return await self.request("GET", url, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    # Keep a handle so nothing else in the process is affected after teardown.
    assert httpx.AsyncClient is not real_async_client
    return _captured


async def test_role_mappings_put_forwards_with_key(
    client: httpx.AsyncClient, ams_env: list[dict[str, Any]]
) -> None:
    """The role-mappings write reaches AMS with the key attached."""
    resp = await client.put(
        "/v1/ams/api/v1/llm-providers/role-mappings",
        json={"orchestrator": "grok_code"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    (sent,) = ams_env
    assert sent["method"] == "PUT"
    assert sent["url"] == "http://ams.test/api/v1/llm-providers/role-mappings"
    assert sent["json"] == {"orchestrator": "grok_code"}
    assert sent["headers"]["X-API-Key"] == "sekret"


async def test_spawn_defaults_and_agent_patch_and_directive_forward(
    client: httpx.AsyncClient, ams_env: list[dict[str, Any]]
) -> None:
    cases = [
        ("put", "/v1/ams/api/v1/llm-providers/spawn-defaults", {"model": "k3"}),
        ("patch", "/v1/ams/api/v1/agents/tl-sales", {"config": {"default_model": "grok"}}),
        ("post", "/v1/ams/api/warden/agents/tl-sales/directive", {"directive": "reload"}),
    ]
    for method, url, body in cases:
        resp = await getattr(client, method)(url, json=body)
        assert resp.status_code == 200, (method, url, resp.text)
    assert [c["method"] for c in ams_env] == ["PUT", "PATCH", "POST"]


@pytest.mark.parametrize(
    "method, url",
    [
        # Wrong method for a listed path.
        ("post", "/v1/ams/api/v1/llm-providers/role-mappings"),
        ("put", "/v1/ams/api/warden/agents/tl-x/directive"),
        # Unlisted paths, including prefix rides on listed ones.
        ("put", "/v1/ams/api/v1/llm-providers"),
        ("put", "/v1/ams/api/v1/llm-providers/role-mappings/extra"),
        ("patch", "/v1/ams/api/v1/agents/tl-x/config"),
        ("post", "/v1/ams/api/v1/memories"),
        ("delete", "/v1/ams/api/v1/agents/tl-x"),
        # Traversal can't ride the table (read-guard normalization reused).
        ("patch", "/v1/ams/api/v1/agents/../admin"),
    ],
)
async def test_everything_off_table_is_403_or_405(
    client: httpx.AsyncClient, ams_env: list[dict[str, Any]], method: str, url: str
) -> None:
    """No generic mutation power: off-table writes never reach AMS."""
    resp = await client.request(method.upper(), url, json={})
    assert resp.status_code in (403, 405)
    assert ams_env == []


async def test_write_unconfigured_is_503(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AMS_BASE_URL", raising=False)
    resp = await client.put("/v1/ams/api/v1/llm-providers/role-mappings", json={})
    assert resp.status_code == 503


async def test_ams_error_passes_through_verbatim(
    client: httpx.AsyncClient, ams_env: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """AMS 400s (e.g. unregistered provider) surface as themselves."""

    class _ErrClient:
        def __init__(self, **kwargs: Any) -> None: ...

        async def __aenter__(self) -> "_ErrClient":
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
            return httpx.Response(
                400, json={"detail": "Provider 'nope' not registered. Available: []"}
            )

    monkeypatch.setattr(httpx, "AsyncClient", _ErrClient)
    resp = await client.put(
        "/v1/ams/api/v1/llm-providers/role-mappings", json={"agent": "nope"}
    )
    assert resp.status_code == 400
    assert "not registered" in json.dumps(resp.json()["detail"])
