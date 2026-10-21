"""Adapt framework failures to Omnigent's existing code/message envelope."""

from __future__ import annotations

import json
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from omnigent.errors import ErrorCode
from omnigent.server.schemas import ErrorDetail


class APIErrorResponse(BaseModel):
    """The same envelope used by the application's OmnigentError handler.

    ``detail`` mirrors FastAPI's default body so clients that read it keep
    working; new clients should read ``error``.
    """

    error: ErrorDetail
    detail: Any = None


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    """Preserve HTTP status and headers while normalizing the error body."""
    del request
    if not isinstance(exc, HTTPException):
        raise exc
    if exc.status_code in {400, 422}:
        code = ErrorCode.INVALID_INPUT
    elif exc.status_code == 500:
        code = ErrorCode.INTERNAL_ERROR
    else:
        try:
            code = HTTPStatus(exc.status_code).name.lower()
        except ValueError:
            code = "http_error"
    detail = raw_detail = exc.detail
    # AMS can already return the canonical envelope. Avoid nesting it or
    # losing its machine-readable code when the proxy raises HTTPException.
    if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
        error = detail["error"]
        if isinstance(error.get("code"), str) and isinstance(error.get("message"), str):
            code, detail = error["code"], error["message"]
    message = detail if isinstance(detail, str) else json.dumps(detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": code, "message": message}, "detail": raw_detail},
        headers=exc.headers,
    )


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    """Report field locations and reasons without echoing submitted input."""
    del request
    if not isinstance(exc, RequestValidationError):
        raise exc
    message = "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors()
    )
    # FastAPI's default shape minus the submitted ``input`` values.
    detail = [
        {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"error": {"code": ErrorCode.INVALID_INPUT, "message": message}, "detail": detail},
    )


def register_api_error_handlers(app: FastAPI) -> None:
    """Install before adding routes so runtime and OpenAPI agree on errors."""
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.router.responses.update(
        {
            "default": {"model": APIErrorResponse, "description": "API error"},
            422: {"model": APIErrorResponse, "description": "Invalid request"},
        }
    )
