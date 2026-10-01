"""Private operator management uses the existing AMS tenant-scoped service key."""

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from omnigent.errors import OmnigentError
from omnigent.server.accounts_config import AccountsConfig
from omnigent.server.auth import AuthProvider, UnifiedAuthProvider
from omnigent.server.oidc import mint_session_cookie
from omnigent.server.routes.ams import create_ams_router

MEMORY = "11111111-1111-4111-8111-111111111111"
OTHER_MEMORY = "22222222-2222-4222-8222-222222222222"
CONTINUATION = "33333333-3333-4333-8333-333333333333"
ROOT = "/v1/ams/management"


class Permissions:
    def __init__(self, admin: bool = True):
        self.admin = admin

    def is_admin(self, user: str) -> bool:
        return self.admin


def app_client(auth: AuthProvider | None, permissions: Any = None) -> TestClient:
    app = FastAPI()
    app.include_router(create_ams_router(auth, permissions), prefix="/v1")

    @app.exception_handler(OmnigentError)
    async def auth_error(request, exc):
        return JSONResponse(status_code=exc.http_status, content={"error": exc.message})

    return TestClient(app)


@pytest.fixture()
def upstream(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    monkeypatch.setenv("AMS_BASE_URL", "https://ams.example.test")
    monkeypatch.setenv("AMS_API_KEY", "synthetic-default-owner-key")
    monkeypatch.setenv("OMNIGENT_ACCOUNTS_INIT_ADMIN_USERNAME", "operator")
    requests: list[httpx.Request] = []
    records = {
        MEMORY: {"memory_id": MEMORY, "status": "active", "user_id": "default-owner"},
        OTHER_MEMORY: {"memory_id": OTHER_MEMORY, "status": "active", "user_id": "other-owner"},
    }

    def serve(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["X-API-Key"] == "synthetic-default-owner-key"
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        assert "x-forwarded-email" not in request.headers
        if request.url.path == "/api/v1/memories/":
            return httpx.Response(
                200,
                json={
                    "memories": [r for r in records.values() if r["user_id"] == "default-owner"],
                    "total": 1,
                },
            )
        if request.url.path.startswith("/api/v1/memories/"):
            record = records.get(request.url.path.rsplit("/", 1)[-1])
            if not record or record["user_id"] != "default-owner":
                return httpx.Response(404, json={"detail": "Memory not found"})
            if request.method == "DELETE":
                if request.url.params["soft_delete"] == "true":
                    record["status"] = "archived"
                else:
                    del records[record["memory_id"]]
                return httpx.Response(204)
            return httpx.Response(200, json={**record, "full_content": "Operator memory"})
        if request.url.path == "/api/v1/continuations/pending":
            return httpx.Response(
                200,
                json={
                    "continuations": [
                        {"continuation_id": CONTINUATION, "original_goal": "Finish private work"}
                    ]
                },
            )
        if request.url.path == f"/api/v1/continuations/{CONTINUATION}":
            return httpx.Response(
                200, json={"continuation_id": CONTINUATION, "next_action": "Inspect"}
            )
        raise AssertionError(f"Unexpected outbound route: {request.url}")

    real_client = httpx.AsyncClient

    def mock_client(**kwargs: Any) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(serve), **kwargs)

    monkeypatch.setattr("omnigent.server.routes.ams.httpx.AsyncClient", mock_client)
    return requests


def test_existing_local_operator_can_browse_inspect_and_archive(upstream):
    auth = UnifiedAuthProvider(source="header", local_single_user=True)
    with app_client(auth) as client:
        assert client.get(f"{ROOT}/status").json()["available"] is True
        assert not upstream  # Access checks do not contact AMS.
        data = client.get(f"{ROOT}/memories/?status=active&limit=25&offset=0").json()
        assert [r["memory_id"] for r in data["memories"]] == [MEMORY]
        assert client.get(f"{ROOT}/memories/{MEMORY}").json()["full_content"] == "Operator memory"
        assert (
            client.get(f"{ROOT}/continuations/pending?limit=50&project=private").status_code == 200
        )
        assert (
            client.get(f"{ROOT}/continuations/{CONTINUATION}").json()["next_action"] == "Inspect"
        )
        response = client.delete(f"{ROOT}/memories/{MEMORY}?soft_delete=true")
        assert response.status_code == 204 and response.content == b""
        assert client.get(f"{ROOT}/memories/{MEMORY}").json()["status"] == "archived"
        response = client.delete(f"{ROOT}/memories/{MEMORY}?soft_delete=false")
        assert response.status_code == 204 and response.content == b""
        assert client.get(f"{ROOT}/memories/{MEMORY}").status_code == 404


@pytest.mark.parametrize("caller", [None, "local", "wife", "brother", "another-admin"])
def test_other_accounts_and_forged_local_headers_never_reach_default_owner(caller, upstream):
    auth = UnifiedAuthProvider(source="header", local_single_user=False)
    headers = {"X-Forwarded-Email": caller} if caller else {}
    expected = 401 if caller in (None, "local") else 403
    with app_client(auth, Permissions()) as client:
        for method, path in (
            ("GET", "status"),
            ("GET", "memories/"),
            ("GET", f"memories/{MEMORY}"),
            ("DELETE", f"memories/{MEMORY}?soft_delete=false"),
        ):
            assert (
                client.request(method, f"{ROOT}/{path}", headers=headers).status_code == expected
            )
    assert not upstream


@pytest.mark.parametrize(
    "caller,admin,expected",
    [("operator", True, 200), ("wife", True, 403), ("operator", False, 403), (None, True, 401)],
)
def test_signed_accounts_cookie_requires_exact_operator_and_existing_admin(
    caller, admin, expected, upstream
):
    config = AccountsConfig(
        cookie_secret=b"test-only-cookie-secret-32-bytes!!",
        session_ttl_hours=8,
        base_url="http://localhost:6767",
        init_admin_password=None,
        invite_ttl_seconds=3600,
        magic_ttl_seconds=60,
    )
    auth = UnifiedAuthProvider(source="accounts", accounts_config=config)
    with app_client(auth, Permissions(admin)) as client:
        if caller:
            client.cookies.set(
                config.session_cookie_name,
                mint_session_cookie(
                    user_id=caller,
                    cookie_secret=config.cookie_secret,
                    ttl_hours=8,
                    provider="accounts",
                ),
            )
        assert client.get(f"{ROOT}/status").status_code == expected
        response = client.get(f"{ROOT}/memories/")
        assert response.status_code == expected
        if expected == 200:
            assert response.json()["memories"][0]["memory_id"] == MEMORY
        else:
            assert not upstream


def test_operator_resolves_from_existing_bootstrap_without_new_setting(monkeypatch, upstream):
    monkeypatch.delenv("OMNIGENT_ACCOUNTS_INIT_ADMIN_USERNAME")
    monkeypatch.setattr("omnigent.server.accounts_bootstrap.getpass.getuser", lambda: "operator")
    auth = UnifiedAuthProvider(source="header", local_single_user=False)
    with app_client(auth, Permissions()) as client:
        assert (
            client.get(f"{ROOT}/status", headers={"X-Forwarded-Email": "operator"}).json()[
                "available"
            ]
            is True
        )
        assert (
            client.get(f"{ROOT}/status", headers={"X-Forwarded-Email": "wife"}).status_code == 403
        )
    assert not upstream


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "memories/forged-id"),
        ("GET", "memories/stats"),
        ("GET", "memories/search"),
        ("POST", "memories/"),
        ("PUT", f"memories/{MEMORY}"),
        ("PATCH", f"memories/{MEMORY}"),
        ("DELETE", f"continuations/{CONTINUATION}"),
        ("POST", f"continuations/{CONTINUATION}/claim"),
        ("POST", f"continuations/{CONTINUATION}/complete"),
        ("DELETE", "continuations/expired"),
        ("GET", "memories//admin"),
        ("GET", "memories/%2e%2e/admin"),
    ],
)
def test_only_selected_management_actions_are_allowed(method, path, upstream):
    auth = UnifiedAuthProvider(source="header", local_single_user=True)
    with app_client(auth) as client:
        assert client.request(method, f"{ROOT}/{path}").status_code == 403
    assert not upstream


@pytest.mark.parametrize(
    "path",
    [
        "memories/?user_id=other-owner",
        f"memories/{MEMORY}?user_id=other-owner",
        "continuations/pending?org_id=other-owner",
        "memories/?limit=101",
        "memories/?limit=0",
        "memories/?offset=-1",
        "continuations/pending?limit=51",
        "memories/?status=deleted",
        "memories/?memory_tier=other",
        "memories/?status=active&status=archived",
        f"memories/{MEMORY}?soft_delete=true&soft_delete=false",
    ],
)
def test_owner_overrides_and_invalid_queries_are_rejected(path, upstream):
    auth = UnifiedAuthProvider(source="header", local_single_user=True)
    with app_client(auth) as client:
        assert client.get(f"{ROOT}/{path}").status_code == 422
    assert not upstream


@pytest.mark.parametrize(
    "query", ["", "?soft_delete=maybe", "?soft_delete=false&user_id=other-owner"]
)
def test_delete_requires_an_explicit_selected_action(query, upstream):
    with app_client(UnifiedAuthProvider(source="header", local_single_user=True)) as client:
        assert client.delete(f"{ROOT}/memories/{MEMORY}{query}").status_code == 422
    assert not upstream


def test_other_tenant_record_ids_remain_unavailable(upstream):
    with app_client(UnifiedAuthProvider(source="header", local_single_user=True)) as client:
        assert client.get(f"{ROOT}/memories/{OTHER_MEMORY}").status_code == 404
        assert (
            client.delete(f"{ROOT}/memories/{OTHER_MEMORY}?soft_delete=false").status_code == 404
        )
        assert client.get(f"{ROOT}/memories/{MEMORY}").json()["full_content"] == "Operator memory"
    assert len(upstream) == 3


@pytest.mark.parametrize("missing", ["AMS_BASE_URL", "AMS_API_KEY"])
def test_missing_connection_is_specific_and_does_not_forward(missing, monkeypatch, upstream):
    monkeypatch.delenv(missing)
    with app_client(UnifiedAuthProvider(source="header", local_single_user=True)) as client:
        status = client.get(f"{ROOT}/status").json()
        assert status["available"] is False and missing in status["reason"]
        assert client.get(f"{ROOT}/memories/").status_code == 503
    assert not upstream


def test_disabled_auth_and_missing_permission_store_cannot_enable_management(upstream):
    with app_client(None) as client:
        assert client.get(f"{ROOT}/status").status_code == 401
    auth = UnifiedAuthProvider(source="header", local_single_user=False)
    with app_client(auth) as client:
        assert (
            client.get(f"{ROOT}/memories/", headers={"X-Forwarded-Email": "operator"}).status_code
            == 403
        )
    assert not upstream


def test_body_and_unlisted_methods_are_not_forwarded(upstream):
    with app_client(UnifiedAuthProvider(source="header", local_single_user=True)) as client:
        assert (
            client.request(
                "DELETE", f"{ROOT}/memories/{MEMORY}?soft_delete=true", json={"user_id": "other"}
            ).status_code
            == 422
        )
        assert client.request("HEAD", f"{ROOT}/memories/{MEMORY}").status_code == 405
        assert client.request("OPTIONS", f"{ROOT}/memories/{MEMORY}").status_code == 405
    assert not upstream
