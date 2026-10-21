"""Management must never forward through an unbound shared key."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
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


def test_management_openapi_has_stable_unique_operation_ids():
    app = FastAPI()
    app.include_router(create_ams_router(), prefix="/v1")
    operations = app.openapi()["paths"]["/v1/ams/management/{path}"]

    assert set(operations) == {"get", "post", "put", "patch", "delete"}
    for method, operation in operations.items():
        assert operation["operationId"] == (
            f"management_unavailable_v1_ams_management__path__{method}"
        )


@pytest.mark.parametrize("method", ["get", "post", "put", "patch", "delete"])
def test_management_openapi_declares_required_path_parameter(method):
    app = FastAPI()
    app.include_router(create_ams_router(), prefix="/v1")
    operation = app.openapi()["paths"]["/v1/ams/management/{path}"][method]

    parameter = next(p for p in operation["parameters"] if p["name"] == "path")
    assert parameter["in"] == "path"
    assert parameter["required"] is True
    assert parameter["schema"]["type"] == "string"


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
def test_management_handler_receives_full_nested_path(method, monkeypatch):
    app = FastAPI()
    app.include_router(create_ams_router(), prefix="/v1")
    route = next(
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path == "/v1/ams/management/{path:path}"
        and method in route.methods
    )
    handler = AsyncMock(wraps=route.dependant.call)
    monkeypatch.setattr(route.dependant, "call", handler)
    path = "memories/nested/record-id"

    with TestClient(app) as client:
        response = client.request(method, f"/v1/ams/management/{path}")

    assert response.status_code == 503
    assert response.json() == {"detail": "AMS account ownership is not verified"}
    handler.assert_awaited_once()
    assert handler.await_args.kwargs["path"] == path
