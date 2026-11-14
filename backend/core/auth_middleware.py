"""
Auth middleware for Zephyro — FastAPI Depends() based authentication.
Supports both Authorization: Bearer <token> and X-Token header (backward compat).
"""
from fastapi import Header, HTTPException, Depends
from typing import Optional
from datetime import datetime


def get_token_from_header(
    authorization: Optional[str] = Header(None),
    x_token: Optional[str] = Header(None, alias="X-Token"),
) -> str:
    """
    Extract token from Authorization: Bearer <token> header.
    Also accepts X-Token header for backward compatibility.
    Precedence: Authorization: Bearer → X-Token → 401.
    """
    if authorization and authorization.startswith("Bearer "):
        return authorization.split(" ", 1)[1]
    if x_token:
        return x_token
    raise HTTPException(status_code=401, detail="Token requerido")


def require_auth(token: str = Depends(get_token_from_header)) -> dict:
    """Validate token, touch last_seen, return session dict."""
    from web_app import sessions  # circular import avoided by lazy import
    session = sessions.get(token)
    if not session:
        raise HTTPException(status_code=401, detail="Sesión inválida")
    session["last_seen"] = datetime.now()
    return session


def require_admin(session: dict = Depends(require_auth)) -> dict:
    """Require admin role."""
    if session.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Se requiere rol admin")
    return session
