"""Bearer token authentication for serve endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException, Request


def create_auth_dependency(auth_token: Optional[str]):
    """Create FastAPI auth dependency that validates bearer token.

    Accepts either Authorization: Bearer <token> or x-smithers-key: <token>.
    Returns 401 if token is missing or invalid when auth_token is not None.
    """

    async def auth_dependency(
        request: Request,
        authorization: Optional[str] = Header(None),
        x_smithers_key: Optional[str] = Header(None),
    ) -> None:
        if auth_token is None:
            return

        token = None
        if authorization and authorization.startswith("Bearer "):
            token = authorization[7:]
        elif x_smithers_key:
            token = x_smithers_key

        if token != auth_token:
            raise HTTPException(
                status_code=401,
                detail={
                    "error": {
                        "code": "UNAUTHORIZED",
                        "message": "invalid or missing token",
                    }
                },
            )

    return auth_dependency
