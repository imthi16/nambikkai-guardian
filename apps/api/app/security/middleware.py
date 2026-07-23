"""ASGI middlewares and CORS wiring for API hardening.

Ordering matters. `configure_security` installs, from outermost to innermost:
CORS -> security headers -> global rate limit -> request-body cap -> routes.
The headers middleware sits outside the rate-limit and body-cap middlewares so
their rejection responses (429/413) still carry the hardened headers.
"""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.auth.rate_limit import RateLimiter, SlidingWindowRateLimiter
from app.config import Settings
from app.security.events import log_security_event

# Endpoints that must stay reachable for liveness/readiness probes even when a
# caller is otherwise rate limited.
_RATE_LIMIT_EXEMPT_PATHS = frozenset({"/health", "/api/v1/health"})

Dispatch = Callable[[Request], Awaitable[Response]]


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _error_response(status_code: int, code: str, message: str, **headers: str) -> JSONResponse:
    """Match the `{detail: {code, message}}` shape used by app.auth.errors."""
    return JSONResponse(
        status_code=status_code,
        content={"detail": {"code": code, "message": message}},
        headers=headers or None,
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach hardened response headers and drop the server-version banner."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        content_security_policy: str,
        hsts_enabled: bool,
        hsts_max_age_seconds: int,
    ) -> None:
        super().__init__(app)
        self._csp = content_security_policy
        self._hsts_enabled = hsts_enabled
        self._hsts_max_age_seconds = hsts_max_age_seconds

    async def dispatch(self, request: Request, call_next: Dispatch) -> Response:
        response = await call_next(request)
        headers = response.headers
        headers["X-Content-Type-Options"] = "nosniff"
        headers["X-Frame-Options"] = "DENY"
        headers["Referrer-Policy"] = "no-referrer"
        headers["Cross-Origin-Opener-Policy"] = "same-origin"
        headers["Cross-Origin-Resource-Policy"] = "same-origin"
        headers["Permissions-Policy"] = (
            "geolocation=(), camera=(), microphone=(), browsing-topics=()"
        )
        headers["Content-Security-Policy"] = self._csp
        headers["Cache-Control"] = headers.get("Cache-Control", "no-store")
        if self._hsts_enabled:
            headers["Strict-Transport-Security"] = (
                f"max-age={self._hsts_max_age_seconds}; includeSubDomains"
            )
        # Do not advertise the server implementation or version.
        headers["Server"] = "attest"
        return response


class GlobalRateLimitMiddleware(BaseHTTPMiddleware):
    """Cap total requests per client IP, independent of the auth-only limiter."""

    def __init__(self, app: ASGIApp, *, limiter: RateLimiter, window_seconds: int) -> None:
        super().__init__(app)
        self._limiter = limiter
        self._window_seconds = window_seconds

    async def dispatch(self, request: Request, call_next: Dispatch) -> Response:
        if request.url.path in _RATE_LIMIT_EXEMPT_PATHS:
            return await call_next(request)
        client = _client_key(request)
        if not self._limiter.allow(client):
            log_security_event("rate_limited", client=client, path=request.url.path)
            return _error_response(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "rate_limited",
                "Too many requests; retry later.",
                **{"Retry-After": str(self._window_seconds)},
            )
        return await call_next(request)


class RequestBodyLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose declared body exceeds the configured cap.

    This is a coarse memory-exhaustion guard for the whole surface; the upload
    route additionally streams with its own byte cap, so the limit here is set
    at or above the upload cap by configuration.
    """

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        super().__init__(app)
        self._max_body_bytes = max_body_bytes

    async def dispatch(self, request: Request, call_next: Dispatch) -> Response:
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                length = int(declared)
            except ValueError:
                return _error_response(
                    status.HTTP_400_BAD_REQUEST,
                    "invalid_content_length",
                    "The Content-Length header is not a valid integer.",
                )
            if length > self._max_body_bytes:
                log_security_event(
                    "request_body_too_large",
                    client=_client_key(request),
                    path=request.url.path,
                    declared_bytes=length,
                )
                return _error_response(
                    status.HTTP_413_CONTENT_TOO_LARGE,
                    "request_body_too_large",
                    "The request body exceeds the allowed size.",
                )
        return await call_next(request)


def configure_security(app: FastAPI, settings: Settings) -> None:
    """Install the CORS policy and hardening middlewares on `app`."""
    app.state.global_rate_limiter = SlidingWindowRateLimiter(
        attempts=settings.global_rate_limit_attempts,
        window_seconds=settings.global_rate_limit_window_seconds,
    )
    # Innermost custom middleware added first; Starlette runs the last-added
    # middleware outermost, so headers wrap the rate-limit/body-cap responses.
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=settings.max_request_body_bytes,
    )
    app.add_middleware(
        GlobalRateLimitMiddleware,
        limiter=app.state.global_rate_limiter,
        window_seconds=settings.global_rate_limit_window_seconds,
    )
    app.add_middleware(
        SecurityHeadersMiddleware,
        content_security_policy=settings.security_csp,
        hsts_enabled=settings.security_hsts_enabled,
        hsts_max_age_seconds=settings.security_hsts_max_age_seconds,
    )
    # Bearer-token auth carries no cookies, so credentials are never reflected;
    # an empty allowlist means same-origin only.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )


__all__ = [
    "GlobalRateLimitMiddleware",
    "RequestBodyLimitMiddleware",
    "SecurityHeadersMiddleware",
    "configure_security",
]
