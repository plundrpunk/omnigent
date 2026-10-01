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


def app_client(
    auth: AuthProvider | None,
    permissions: Any = Permissions(),
    *,
    bind_host: str | None = "127.0.0.1",
    base_url: str = "http://127.0.0.1:6767",
    client_host: str = "127.0.0.1",
) -> TestClient:
    app = FastAPI()
    app.include_router(create_ams_router(auth, permissions, bind_host=bind_host), prefix="/v1")

    @app.exception_handler(OmnigentError)
    async def auth_error(request, exc):
        return JSONResponse(status_code=exc.http_status, content={"error": exc.message})

    return TestClient(app, base_url=base_url, client=(client_host, 50000))


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
        if request.url.path == "/health":
            # Startup health is mocked separately from protected record requests.
            return httpx.Response(200, json={"status": "healthy"})
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
        if request.url.path == "/api/v1/memories/search":
            return httpx.Response(200, json={"memories": [records[MEMORY]]})
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
    [
        ("operator", True, 403),
        ("wife", True, 403),
        ("operator", False, 403),
        ("local", True, 401),
        (None, True, 401),
    ],
)
def test_named_accounts_are_denied_without_persisted_binding(caller, admin, expected, upstream):
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


def test_bootstrap_name_is_not_a_persisted_operator_binding(monkeypatch, upstream):
    monkeypatch.delenv("OMNIGENT_ACCOUNTS_INIT_ADMIN_USERNAME")
    monkeypatch.setattr("omnigent.server.accounts_bootstrap.getpass.getuser", lambda: "operator")
    auth = UnifiedAuthProvider(source="header", local_single_user=False)
    with app_client(auth, Permissions()) as client:
        for name in ("operator", "wife"):
            assert (
                client.get(f"{ROOT}/status", headers={"X-Forwarded-Email": name}).status_code
                == 403
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


@pytest.mark.parametrize(
    "bind_host,base_url,client_host",
    [
        (None, "http://127.0.0.1:6767", "127.0.0.1"),
        ("0.0.0.0", "http://127.0.0.1:6767", "127.0.0.1"),
        ("::", "http://127.0.0.1:6767", "::1"),
        ("127.0.0.1", "http://192.0.2.1:6767", "127.0.0.1"),
        ("127.0.0.1", "http://127.0.0.1:6767", "192.0.2.10"),
    ],
)
def test_local_sentinel_requires_verified_loopback_bind_and_socket(
    bind_host, base_url, client_host, upstream
):
    auth = UnifiedAuthProvider(source="header", local_single_user=True)
    with app_client(
        auth, bind_host=bind_host, base_url=base_url, client_host=client_host
    ) as client:
        assert client.delete(f"{ROOT}/memories/{MEMORY}?soft_delete=false").status_code == 403
        assert client.get("/v1/ams/api/v1/memories/").status_code == 403
        assert (
            client.post("/v1/ams/api/v1/memories/search", json={"query": "private"}).status_code
            == 403
        )
    assert not upstream


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "attacker.example:6767"},
        {"Host": "127.0.0.1:1234"},
        {"Origin": "https://attacker.example"},
        {"Origin": "http://127.0.0.1:1234"},
        {"Origin": "null"},
        {"Origin": "http://127.0.0.1:6767/extra"},
        {"Forwarded": "for=192.0.2.2"},
        {"X-Forwarded-For": "192.0.2.2"},
        {"X-Forwarded-Host": "127.0.0.1:6767"},
        {"X-Real-IP": "192.0.2.2"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
    ],
)
def test_hostile_browser_and_proxy_requests_do_not_use_local_identity(headers, upstream):
    with app_client(UnifiedAuthProvider(source="header", local_single_user=True)) as client:
        assert (
            client.delete(
                f"{ROOT}/memories/{MEMORY}?soft_delete=false", headers=headers
            ).status_code
            == 403
        )
        assert client.get("/v1/ams/api/v1/memories/", headers=headers).status_code == 403
        assert (
            client.post(
                "/v1/ams/api/v1/memories/search", headers=headers, json={"query": "private"}
            ).status_code
            == 403
        )
    assert not upstream


def test_direct_same_origin_local_requests_still_work(upstream):
    with app_client(UnifiedAuthProvider(source="header", local_single_user=True)) as client:
        headers = {"Origin": "http://127.0.0.1:6767", "Sec-Fetch-Site": "same-origin"}
        assert client.get(f"{ROOT}/status", headers=headers).json()["available"] is True
        assert client.get("/v1/ams/api/v1/memories/", headers=headers).status_code == 200
        assert (
            client.post(
                "/v1/ams/api/v1/memories/search", headers=headers, json={"query": "private"}
            ).status_code
            == 200
        )
        assert (
            client.delete(
                f"{ROOT}/memories/{MEMORY}?soft_delete=true", headers=headers
            ).status_code
            == 204
        )


def test_local_admin_denial_cannot_be_bypassed(upstream):
    with app_client(
        UnifiedAuthProvider(source="header", local_single_user=True), Permissions(False)
    ) as client:
        assert client.delete(f"{ROOT}/memories/{MEMORY}?soft_delete=false").status_code == 403
        assert client.get("/v1/ams/api/v1/memories/").status_code == 403
        assert (
            client.post("/v1/ams/api/v1/memories/search", json={"query": "private"}).status_code
            == 403
        )
    assert not upstream


@pytest.mark.parametrize("caller", ["wife", "brother", "operator", "another-admin"])
def test_alternate_memory_routes_cannot_bypass_named_account_denial(caller, upstream):
    with app_client(
        UnifiedAuthProvider(source="header", local_single_user=False), Permissions()
    ) as client:
        headers = {"X-Forwarded-Email": caller}
        assert client.get("/v1/ams/api/v1/memories/", headers=headers).status_code == 403
        assert client.get(f"/v1/ams/api/v1/memories/{MEMORY}", headers=headers).status_code == 403
        assert (
            client.post(
                "/v1/ams/api/v1/memories/search", headers=headers, json={"query": "private"}
            ).status_code
            == 403
        )
    assert not upstream


def test_alternate_memory_routes_reject_owner_overrides(upstream):
    with app_client(UnifiedAuthProvider(source="header", local_single_user=True)) as client:
        assert client.get("/v1/ams/api/v1/memories/?user_id=other").status_code == 422
        assert (
            client.post(
                "/v1/ams/api/v1/memories/search", json={"query": "private", "user_id": "other"}
            ).status_code
            == 422
        )
    assert not upstream


@pytest.mark.parametrize(
    "bind_host,caller,admin,origin,expected,source",
    [
        ("127.0.0.1", None, True, "http://127.0.0.1:6767", 200, "header"),
        ("0.0.0.0", None, True, "http://127.0.0.1:6767", 403, "header"),
        (None, None, True, "http://127.0.0.1:6767", 403, "header"),
        ("127.0.0.1", None, False, "http://127.0.0.1:6767", 403, "header"),
        ("127.0.0.1", None, True, "https://attacker.example", 403, "header"),
        ("127.0.0.1", "wife", True, "http://127.0.0.1:6767", 403, "header"),
        ("127.0.0.1", "operator", True, "http://127.0.0.1:6767", 403, "header"),
        ("127.0.0.1", "local", True, "http://127.0.0.1:6767", 401, "header"),
        ("127.0.0.1", "operator", True, "http://127.0.0.1:6767", 403, "accounts"),
        ("127.0.0.1", "wife", True, "http://127.0.0.1:6767", 403, "accounts"),
        ("127.0.0.1", "local", True, "http://127.0.0.1:6767", 401, "accounts"),
        ("127.0.0.1", "setup-chosen-owner", True, "http://127.0.0.1:6767", 403, "accounts"),
    ],
)
def test_real_app_factory_enforces_private_boundary(
    bind_host, caller, admin, origin, expected, source, upstream, db_uri, tmp_path, runtime_init
):
    from omnigent.runtime.agent_cache import AgentCache
    from omnigent.server.app import create_app
    from omnigent.stores.agent_store.sqlalchemy_store import SqlAlchemyAgentStore
    from omnigent.stores.artifact_store.local import LocalArtifactStore
    from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore
    from omnigent.stores.file_store.sqlalchemy_store import SqlAlchemyFileStore
    from omnigent.stores.permission_store.sqlalchemy_store import SqlAlchemyPermissionStore

    permissions = SqlAlchemyPermissionStore(db_uri)
    permissions.ensure_user("local", is_admin=admin)
    permissions.set_admin("local", admin)
    if caller and caller not in ("local", "setup-chosen-owner"):
        permissions.ensure_user(caller, is_admin=True)
    config = AccountsConfig(
        cookie_secret=b"test-only-cookie-secret-32-bytes!!",
        session_ttl_hours=8,
        base_url="http://127.0.0.1:6767",
        init_admin_password=None,
        invite_ttl_seconds=3600,
        magic_ttl_seconds=60,
    )
    account_store = None
    if source == "accounts":
        from omnigent.server.accounts_store import SqlAlchemyAccountStore
        from omnigent.server.passwords import hash_password

        account_store = SqlAlchemyAccountStore(db_uri)
        account_store.create_user_with_password(
            "setup-chosen-owner", hash_password("test-only-owner-password"), is_admin=True
        )
    auth = UnifiedAuthProvider(
        source=source,
        accounts_config=config if source == "accounts" else None,
        local_single_user=True,
    )
    artifacts = LocalArtifactStore(str(tmp_path / "private-artifacts"))
    app = create_app(
        agent_store=SqlAlchemyAgentStore(db_uri),
        file_store=SqlAlchemyFileStore(db_uri),
        conversation_store=SqlAlchemyConversationStore(db_uri),
        artifact_store=artifacts,
        agent_cache=AgentCache(artifact_store=artifacts, cache_dir=tmp_path / "private-cache"),
        permission_store=permissions,
        auth_provider=auth,
        account_store=account_store,
        bind_host=bind_host,
        server_config={},
    )
    headers = {"Origin": origin}
    if caller:
        headers["X-Forwarded-Email"] = caller
    with TestClient(app, base_url="http://127.0.0.1:6767", client=("127.0.0.1", 50000)) as client:
        if source == "accounts" and caller:
            client.cookies.set(
                config.session_cookie_name,
                mint_session_cookie(
                    user_id=caller,
                    cookie_secret=config.cookie_secret,
                    ttl_hours=8,
                    provider="accounts",
                ),
            )
        assert client.get(f"{ROOT}/memories/", headers=headers).status_code == expected
        assert client.get("/v1/ams/api/v1/memories/", headers=headers).status_code == expected
        assert (
            client.post(
                "/v1/ams/api/v1/memories/search", headers=headers, json={"query": "private"}
            ).status_code
            == expected
        )
        deletion = client.delete(f"{ROOT}/memories/{MEMORY}?soft_delete=true", headers=headers)
        assert deletion.status_code == (204 if expected == 200 else expected)
    if expected != 200:
        assert not upstream
