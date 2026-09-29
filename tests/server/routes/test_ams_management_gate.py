"""Management must never forward through an unbound shared key."""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnigent.server.routes.ams import create_ams_router


@pytest.mark.parametrize("caller", [None, "owner-a", "owner-b"])
def test_management_denies_every_caller_without_outbound_requests(caller):
    app = FastAPI()
    app.include_router(create_ams_router(), prefix="/v1")
    with (
        patch("omnigent.server.routes.ams.require_user", return_value=caller),
        patch("omnigent.server.routes.ams.httpx.AsyncClient") as outbound,
        TestClient(app) as client,
    ):
        assert client.get("/v1/ams/management/status").json()["available"] is False
        for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            for path in (
                "memories/",
                "memories/11111111-1111-4111-8111-111111111111?soft_delete=false",
                "memories/forged-id",
                "continuations/pending",
            ):
                assert client.request(method, f"/v1/ams/management/{path}").status_code == 503
        outbound.assert_not_called()


@pytest.mark.parametrize("caller", [None, "owner-a@example.test", "owner-b@example.test"])
def test_management_retains_real_auth_and_denies_configured_shared_key(caller, monkeypatch):
    from fastapi.responses import JSONResponse

    from omnigent.errors import OmnigentError
    from omnigent.server.auth import UnifiedAuthProvider

    monkeypatch.setenv("AMS_BASE_URL", "https://ams.example.test")
    monkeypatch.setenv("AMS_API_KEY", "synthetic-shared-key")
    app = FastAPI()
    auth = UnifiedAuthProvider(
        source="header", local_single_user=False, header_name="X-Forwarded-Email"
    )
    app.include_router(create_ams_router(auth), prefix="/v1")

    @app.exception_handler(OmnigentError)
    async def auth_error(request, exc):
        return JSONResponse(status_code=exc.http_status, content={"error": exc.message})

    headers = {"X-Forwarded-Email": caller} if caller else {}
    with (
        patch("omnigent.server.routes.ams.httpx.AsyncClient") as outbound,
        TestClient(app) as client,
    ):
        status = client.get("/v1/ams/management/status", headers=headers)
        assert status.status_code == (200 if caller else 401)
        if caller:
            assert status.json()["available"] is False
        for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            response = client.request(
                method,
                "/v1/ams/management/memories/forged-id?user_id=other-owner&soft_delete=false",
                headers=headers,
            )
            expected = 405 if method in ("HEAD", "OPTIONS") else (503 if caller else 401)
            assert response.status_code == expected
        outbound.assert_not_called()
