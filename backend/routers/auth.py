"""Auth router — login, logout, identify endpoints."""
import secrets
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from core.auth_middleware import require_auth

router = APIRouter()


class LoginRequest(BaseModel):
    password: Optional[str] = None


def _get_sessions(request: Request) -> dict:
    """Access sessions dict from app module."""
    import web_app
    return web_app.sessions


def _get_admin_password(request: Request) -> str:
    import web_app
    return web_app.ADMIN_PASSWORD


@router.post("/auth/login")
async def login(request: Request, body: LoginRequest):
    """Autenticar usuario (admin o guest)"""
    sessions = _get_sessions(request)
    ADMIN_PASSWORD = _get_admin_password(request)
    token = secrets.token_urlsafe(32)

    if ADMIN_PASSWORD and body.password == ADMIN_PASSWORD:
        sessions[token] = {
            "role": "admin",
            "name": "Bruno",
            "authenticated": False,
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        }
        return {
            "success": True,
            "token": token,
            "role": "admin",
            "name": "Bruno",
            "message": "Bienvenido, Bruno. Acceso completo activado."
        }
    elif body.password == "" or body.password is None:
        sessions[token] = {
            "role": "guest",
            "name": None,
            "authenticated": False,
            "created_at": datetime.now(),
            "last_seen": datetime.now(),
        }
        return {
            "success": True,
            "token": token,
            "role": "guest",
            "name": None,
            "message": "Modo Invitado activado. Solo acceso a modelo local."
        }
    else:
        from routers.telemetry import record_failed_auth
        record_failed_auth()
        return {
            "success": False,
            "token": None,
            "role": None,
            "name": None,
            "message": "Contraseña incorrecta."
        }


@router.post("/auth/logout")
async def logout(request: Request, token: Optional[str] = None):
    """Cerrar sesión — soft/idempotent: missing or invalid token returns success:false, never 401."""
    sessions = _get_sessions(request)
    if token and token in sessions:
        del sessions[token]
        return {"success": True, "message": "Sesión cerrada correctamente"}
    return {"success": False, "message": "Sesión no encontrada"}


@router.post("/auth/identify")
async def identify(request: Request, session: dict = Depends(require_auth), name: Optional[str] = None):
    """Identificar usuario (comando 'identificame')"""
    session["last_seen"] = datetime.now()

    if session["role"] == "admin":
        session["authenticated"] = True
        return {
            "success": True,
            "message": f"Bienvenido, {session['name']}."
        }
    else:
        if name:
            session["name"] = name
        else:
            session["name"] = "Invitado"
        session["authenticated"] = True
        return {
            "success": True,
            "message": f"Bienvenido, {session['name']}. Estás usando Zephyro en modo invitado."
        }
