"""One error shape for the whole API: {"error": {"code", "message", "details"}}."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def not_found(entity: str = "Resource") -> AppError:
    return AppError(404, "not_found", f"{entity} not found")


def conflict(message: str, details: Any = None) -> AppError:
    return AppError(409, "conflict", message, details)


def bad_request(message: str, details: Any = None, code: str = "bad_request") -> AppError:
    return AppError(400, code, message, details)


def forbidden(message: str = "You do not have permission to do that") -> AppError:
    return AppError(403, "forbidden", message)


def _body(code: str, message: str, details: Any = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    429: "rate_limited",
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError):
        return JSONResponse(_body(exc.code, exc.message, exc.details), status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        details = [
            {"loc": [str(p) for p in e.get("loc", [])], "message": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()
        ]
        return JSONResponse(_body("validation_error", "Request validation failed", details), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = _STATUS_CODES.get(exc.status_code, "http_error")
        return JSONResponse(
            _body(code, str(exc.detail) if exc.detail else code),
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("Unhandled error: %s", exc)
        return JSONResponse(_body("internal_error", "An unexpected error occurred"), status_code=500)
