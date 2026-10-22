"""HTTP, validation and domain failures share the server's error envelope."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel

from omnigent.errors import ErrorCode, OmnigentError


class _Input(BaseModel):
    count: int


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code",
    [
        (400, "invalid_input"),
        (401, "unauthorized"),
        (403, "forbidden"),
        (404, "not_found"),
        (405, "method_not_allowed"),
        (409, "conflict"),
        (415, "unsupported_media_type"),
        (422, "invalid_input"),
        (429, "too_many_requests"),
        (500, "internal_error"),
        (503, "service_unavailable"),
        (599, "http_error"),
    ],
)
async def test_http_errors_use_standard_envelope(app: FastAPI, status: int, code: str) -> None:
    @app.get("/v1/test-error")
    async def fail() -> None:
        raise HTTPException(status, "Request rejected", headers={"Retry-After": "10"})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/v1/test-error")
    assert response.status_code == status
    assert response.headers["retry-after"] == "10"
    assert response.json() == {
        "error": {"code": code, "message": "Request rejected"},
        "detail": "Request rejected",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "detail,expected",
    [
        (
            {"reason": "unavailable"},
            {"code": "bad_gateway", "message": '{"reason": "unavailable"}'},
        ),
        (
            {"error": {"code": "upstream_unavailable", "message": "Try again"}},
            {"code": "upstream_unavailable", "message": "Try again"},
        ),
    ],
)
async def test_structured_http_error_details(app: FastAPI, detail: dict, expected: dict) -> None:
    @app.get("/v1/test-error")
    async def fail() -> None:
        raise HTTPException(502, detail)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/test-error")
    assert response.status_code == 502
    assert response.json() == {"error": expected, "detail": detail}


@pytest.mark.asyncio
async def test_http_error_with_validation_list_hides_input(app: FastAPI) -> None:
    """A route re-raising pydantic errors as HTTPException must not echo input."""

    @app.post("/v1/test-error")
    async def fail() -> None:
        raise HTTPException(
            422,
            [
                {"type": "value_error", "loc": [], "msg": "Value error, bad", "input": "secret"},
                {"type": "int_parsing", "loc": ["body", "count"], "msg": "bad int", "input": "x"},
            ],
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/v1/test-error")
    assert response.status_code == 422
    assert response.json() == {
        "error": {"code": "invalid_input", "message": "Value error, bad; body.count: bad int"},
        "detail": [
            {"loc": [], "msg": "Value error, bad", "type": "value_error"},
            {"loc": ["body", "count"], "msg": "bad int", "type": "int_parsing"},
        ],
    }
    assert "secret" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 201, 202])
async def test_successful_model_responses_unchanged(app: FastAPI, status: int) -> None:
    @app.post("/v1/test-success", status_code=status, response_model=_Input)
    async def succeed(payload: _Input) -> dict[str, object]:
        return {"count": payload.count, "filtered_by_response_model": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/v1/test-success", json={"count": 0})
    assert response.status_code == status
    assert response.headers["content-type"] == "application/json"
    assert response.content == b'{"count":0}'


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["empty", "text", "stream", "redirect"])
async def test_successful_raw_responses_unchanged(app: FastAPI, kind: str) -> None:
    @app.get("/v1/test-success")
    async def succeed() -> Response:
        if kind == "empty":
            return Response(status_code=204, headers={"X-Result": "deleted"})
        if kind == "text":
            return Response("ready", media_type="text/plain")
        if kind == "stream":
            return StreamingResponse(
                iter([b"data: one\n\n", b"data: two\n\n"]), media_type="text/event-stream"
            )
        return RedirectResponse("/health")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/test-success")
    if kind == "empty":
        assert response.status_code == 204
        assert response.content == b""
        assert response.headers["x-result"] == "deleted"
    elif kind == "text":
        assert response.status_code == 200
        assert response.text == "ready"
        assert response.headers["content-type"] == "text/plain; charset=utf-8"
    elif kind == "stream":
        assert response.status_code == 200
        assert response.content == b"data: one\n\ndata: two\n\n"
        assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    else:
        assert response.status_code == 307
        assert response.content == b""
        assert response.headers["location"] == "/health"


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"count": "private-input"}, {}, [1, 2], "malformed-json"])
async def test_validation_errors_use_standard_envelope(app: FastAPI, body: object) -> None:
    @app.post("/v1/test-validation")
    async def validate(payload: _Input) -> dict[str, int]:
        return {"count": payload.count}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        if isinstance(body, str):
            response = await client.post(
                "/v1/test-validation",
                content=body,
                headers={"Content-Type": "application/json"},
            )
        else:
            response = await client.post("/v1/test-validation", json=body)
    assert response.status_code == 422
    error = response.json()["error"]
    assert set(response.json()) == {"error", "detail"}
    assert set(error) == {"code", "message"}
    assert all(set(item) == {"loc", "msg", "type"} for item in response.json()["detail"])
    assert error["code"] == "invalid_input"
    assert "body" in error["message"]
    assert "private-input" not in response.text


@pytest.mark.asyncio
async def test_router_errors_use_standard_envelope(app: FastAPI) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        missing = await client.get("/v1/does-not-exist")
        wrong_method = await client.post("/health")
    assert missing.status_code == 404
    assert missing.json() == {
        "error": {"code": "not_found", "message": "Not Found"},
        "detail": "Not Found",
    }
    assert wrong_method.status_code == 405
    assert wrong_method.json()["error"]["code"] == "method_not_allowed"
    assert "GET" in wrong_method.headers["allow"]


@pytest.mark.asyncio
@pytest.mark.parametrize("domain", [True, False])
async def test_existing_domain_and_internal_envelopes_preserved(
    app: FastAPI, domain: bool
) -> None:
    @app.get("/v1/test-error")
    async def fail() -> None:
        if domain:
            raise OmnigentError("Missing resource", code=ErrorCode.NOT_FOUND)
        raise RuntimeError("private implementation detail")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get("/v1/test-error")
    assert response.status_code == (404 if domain else 500)
    assert response.json() == {
        "error": {
            "code": "not_found" if domain else "internal_error",
            "message": "Missing resource" if domain else "An internal error occurred.",
        }
    }


def test_openapi_describes_standard_errors(app: FastAPI) -> None:
    spec = app.openapi()
    responses = spec["paths"]["/v1/sessions/{session_id}"]["get"]["responses"]
    for status in ("default", "422"):
        assert responses[status]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/APIErrorResponse",
        }
