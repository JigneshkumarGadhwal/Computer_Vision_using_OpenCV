"""API authentication: static bearer token (ZIGGGY_API_TOKEN).

* Token set   -> every /api route except /api/health requires ``Authorization: Bearer <token>``
                 (the SSE stream may pass ``?token=`` because EventSource cannot set headers).
* Token unset -> only loopback clients are served; the server refuses to bind a non-loopback
                 address without a token (see main.py).
"""
from __future__ import annotations

import hmac

from fastapi import HTTPException, Request, status

LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


def check_auth(request: Request) -> None:
    settings = request.app.state.core.settings
    token = settings.api_token.get_secret_value() if settings.api_token else None
    if not token:
        host = request.client.host if request.client else ""
        if host in LOOPBACK or settings.allow_insecure_no_auth:
            return
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required (set ZIGGGY_API_TOKEN)")
    supplied = None
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        supplied = h[7:].strip()
    elif request.url.path.endswith("/stream"):
        supplied = request.query_params.get("token")
    if not supplied or not hmac.compare_digest(supplied, token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing token", headers={"WWW-Authenticate": "Bearer"})
