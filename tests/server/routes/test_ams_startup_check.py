"""AMS bridge fail-loud startup check (2026-08-20 Training-page outage).

A server booted with a missing or dead AMS bridge must say so at boot,
not 503 silently per-request. OMNIGENT_REQUIRE_AMS=1 turns the shout
into an abort.
"""

from __future__ import annotations

import logging

import pytest

from omnigent.server.routes.ams import check_bridge_at_startup


@pytest.fixture
def quiet_logger() -> logging.Logger:
    logger = logging.getLogger("test.ams.startup")
    logger.setLevel(logging.DEBUG)
    return logger


async def test_unconfigured_warns_but_boots(monkeypatch, caplog, quiet_logger) -> None:
    monkeypatch.delenv("AMS_BASE_URL", raising=False)
    monkeypatch.delenv("OMNIGENT_REQUIRE_AMS", raising=False)
    with caplog.at_level(logging.WARNING, logger="test.ams.startup"):
        result = await check_bridge_at_startup(logger=quiet_logger)
    assert result["state"] == "unconfigured"
    assert any(
        "NOT CONFIGURED" in r.message or "NOT CONFIGURED" in r.getMessage() for r in caplog.records
    )


async def test_unconfigured_aborts_when_required(monkeypatch, quiet_logger) -> None:
    monkeypatch.delenv("AMS_BASE_URL", raising=False)
    monkeypatch.setenv("OMNIGENT_REQUIRE_AMS", "1")
    with pytest.raises(RuntimeError):
        await check_bridge_at_startup(logger=quiet_logger)


async def test_healthy_bridge_is_ok(monkeypatch, quiet_logger) -> None:
    monkeypatch.setenv("AMS_BASE_URL", "https://ams.example")
    monkeypatch.setenv("AMS_API_KEY", "k")
    monkeypatch.delenv("OMNIGENT_REQUIRE_AMS", raising=False)

    async def probe(base_url: str, api_key: str) -> int:
        assert base_url == "https://ams.example"
        assert api_key == "k"
        return 200

    result = await check_bridge_at_startup(probe=probe, logger=quiet_logger)
    assert result["state"] == "ok"


async def test_unreachable_shouts_but_boots(monkeypatch, caplog, quiet_logger) -> None:
    monkeypatch.setenv("AMS_BASE_URL", "https://ams.example")
    monkeypatch.delenv("OMNIGENT_REQUIRE_AMS", raising=False)

    async def probe(base_url: str, api_key: str) -> int:
        raise ConnectionError("boom")

    with caplog.at_level(logging.ERROR, logger="test.ams.startup"):
        result = await check_bridge_at_startup(probe=probe, logger=quiet_logger)
    assert result["state"] == "unreachable"
    assert any("UNREACHABLE" in r.getMessage() for r in caplog.records)


async def test_unreachable_aborts_when_required(monkeypatch, quiet_logger) -> None:
    monkeypatch.setenv("AMS_BASE_URL", "https://ams.example")
    monkeypatch.setenv("OMNIGENT_REQUIRE_AMS", "true")

    async def probe(base_url: str, api_key: str) -> int:
        raise ConnectionError("boom")

    with pytest.raises(RuntimeError):
        await check_bridge_at_startup(probe=probe, logger=quiet_logger)


async def test_unhealthy_status_shouts_but_boots(monkeypatch, quiet_logger) -> None:
    monkeypatch.setenv("AMS_BASE_URL", "https://ams.example")
    monkeypatch.delenv("OMNIGENT_REQUIRE_AMS", raising=False)

    async def probe(base_url: str, api_key: str) -> int:
        return 500

    result = await check_bridge_at_startup(probe=probe, logger=quiet_logger)
    assert result["state"] == "unhealthy"
    assert "500" in result["detail"]


@pytest.mark.parametrize("api_key", ["", "test-key"])
async def test_default_probe_uses_health_path_headers_and_timeout(monkeypatch, api_key) -> None:
    import httpx

    from omnigent.server.routes import ams

    monkeypatch.setenv("AMS_BASE_URL", "https://ams.example/")
    monkeypatch.setenv("AMS_API_KEY", api_key)
    monkeypatch.setenv("OMNIGENT_REQUIRE_AMS", "yes")
    requests = []

    def respond(request):
        requests.append(request)
        assert str(request.url) == "https://ams.example/health"
        assert request.headers.get("X-API-Key", "") == api_key
        assert request.extensions["timeout"]["read"] == 5.0
        return httpx.Response(200)

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        ams.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    result = await check_bridge_at_startup()
    assert result == {"state": "ok", "base_url": "https://ams.example", "detail": "healthy"}
    assert len(requests) == 1


@pytest.mark.parametrize("required", ["yes", " TRUE ", "1"])
async def test_unhealthy_required_bridge_aborts(monkeypatch, required, quiet_logger) -> None:
    monkeypatch.setenv("AMS_BASE_URL", "https://ams.example")
    monkeypatch.setenv("OMNIGENT_REQUIRE_AMS", required)

    async def probe(base_url, api_key):
        return 401

    with pytest.raises(RuntimeError, match="required but unhealthy: HTTP 401"):
        await check_bridge_at_startup(probe=probe, logger=quiet_logger)


async def test_server_lifespan_checks_bridge_before_starting_harness(app, monkeypatch) -> None:
    from unittest.mock import AsyncMock

    from omnigent.server import app as app_module
    from omnigent.server.routes import ams

    check = AsyncMock(side_effect=RuntimeError("AMS bridge required but unreachable"))
    start = AsyncMock()
    monkeypatch.setattr(ams, "check_bridge_at_startup", check)
    monkeypatch.setattr(app_module.HarnessProcessManager, "start", start)
    with pytest.raises(RuntimeError, match="AMS bridge required"):
        async with app.router.lifespan_context(app):
            pytest.fail("unhealthy required bridge must abort startup")
    check.assert_awaited_once_with()
    start.assert_not_awaited()
