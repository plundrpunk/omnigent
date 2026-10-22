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
    if _is_validation_error_list(detail):
        # Routes that re-raise pydantic errors as HTTPException (session create)
        # get a readable "loc: msg" message instead of a JSON dump. ``detail``
        # is passed through unchanged: upstream contract-tests that route's
        # exact legacy 422 list, ``input`` included.
        message = _validation_message(detail)
    else:
        message = detail if isinstance(detail, str) else json.dumps(detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": code, "message": message}, "detail": raw_detail},
        headers=exc.headers,
    )


def _is_validation_error_list(detail: Any) -> bool:
    """Whether ``detail`` is a pydantic/FastAPI-style list of error dicts."""
    return (
        isinstance(detail, list)
        and len(detail) > 0
        and all(isinstance(e, dict) and "loc" in e and "msg" in e for e in detail)
    )


def _validation_message(errors: Any) -> str:
    """Render ``loc: msg`` pairs; an empty loc (model-level error) is just ``msg``."""
    parts = []
    for error in errors:
        loc = ".".join(str(part) for part in error["loc"])
        parts.append(f"{loc}: {error['msg']}" if loc else str(error["msg"]))
    return "; ".join(parts)


def _strip_input(errors: Any) -> list[dict[str, Any]]:
    """FastAPI's default error shape minus the submitted ``input`` values."""
    return [
        {"loc": list(error["loc"]), "msg": error["msg"], "type": error.get("type")}
        for error in errors
    ]


async def validation_error_response(request: Request, exc: Exception) -> JSONResponse:
    """Report field locations and reasons without echoing submitted input."""
    del request
    if not isinstance(exc, RequestValidationError):
        raise exc
    message = _validation_message(exc.errors())
    detail = _strip_input(exc.errors())
    return JSONResponse(
        status_code=422,
        content={"error": {"code": ErrorCode.INVALID_INPUT, "message": message}, "detail": detail},
    )


def register_api_error_handlers(app: FastAPI) -> None:
    """Install before adding routes so runtime and OpenAPI agree on errors."""
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, validation_error_response)
    app.router.responses.update(
        {
            "default": {"model": APIErrorResponse, "description": "API error"},
            422: {"model": APIErrorResponse, "description": "Invalid request"},
        }
    )
