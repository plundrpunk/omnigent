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
