"""Google Calendar API — sync bidireccional con ScheduleService.

Env vars necesarias en .env:
  GOOGLE_CLIENT_ID       — OAuth2 client ID de Google Cloud Console
  GOOGLE_CLIENT_SECRET   — OAuth2 client secret
  GOOGLE_CALENDAR_ID     — ID del calendario (default: "primary")
  GOOGLE_CALENDAR_SYNC   — "true" para activar sync (default: "false")

Flujo OAuth2:
  1. get_auth_url() → URL para abrir en browser
  2. exchange_code(code) → intercambia por access_token + refresh_token
  3. Token se persiste en storage/google_token.json con refresh automático
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import httpx

CREDS_PATH = Path(__file__).resolve().parent.parent.parent / "storage" / "google_creds.json"
TOKEN_PATH = Path(__file__).resolve().parent.parent.parent / "storage" / "google_token.json"

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


class GoogleCalendarClient:
    """Wrapper async para Google Calendar API v3."""

    def __init__(self):
        self._client_id = os.getenv("GOOGLE_CLIENT_ID", "")
        self._client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
        self._calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "primary")
        self._token: Optional[dict] = None
        self._load_token()

    def is_configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def is_authenticated(self) -> bool:
        return self._token is not None

    def _load_token(self):
        if TOKEN_PATH.exists():
            try:
                self._token = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._token = None

    def _save_token(self):
        if self._token:
            TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
            TOKEN_PATH.write_text(json.dumps(self._token, indent=2, ensure_ascii=False), encoding="utf-8")

    async def get_auth_url(self) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
        }
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)

    async def exchange_code(self, code: str) -> bool:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
                    "grant_type": "authorization_code",
                },
            )
            if r.status_code != 200:
                return False
            self._token = r.json()
            self._token["obtained_at"] = time.time()
            self._save_token()
            return True

    async def _get_access_token(self) -> Optional[str]:
        if not self._token:
            return None
        expires_in = self._token.get("expires_in", 3600)
        obtained_at = self._token.get("obtained_at", 0)
        if time.time() > obtained_at + expires_in - 300:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "refresh_token": self._token.get("refresh_token"),
                        "grant_type": "refresh_token",
                    },
                )
                if r.status_code == 200:
                    new_data = r.json()
                    self._token.update(new_data)
                    self._token["obtained_at"] = time.time()
                    self._save_token()
        return self._token.get("access_token")

    async def push_event(self, event: dict) -> Optional[str]:
        """
        Crea evento en Google Calendar.
        event: {"title", "start_ts_utc", "end_ts_utc", "notes", "category"}
        Retorna gcal_id o None si falla.
        """
        token = await self._get_access_token()
        if not token:
            return None

        body = {
            "summary": event["title"],
            "description": f"[{event.get('category', 'other')}] {event.get('notes', '')}",
            "start": {
                "dateTime": datetime.fromtimestamp(event["start_ts_utc"], tz=timezone.utc).isoformat(),
            },
            "end": {
                "dateTime": datetime.fromtimestamp(event["end_ts_utc"], tz=timezone.utc).isoformat(),
            },
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"https://www.googleapis.com/calendar/v3/calendars/{self._calendar_id}/events",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
            if r.status_code in (200, 201):
                return r.json().get("id")
        return None

    async def update_event(self, gcal_id: str, event: dict) -> bool:
        """Actualiza un evento existente en Google Calendar."""
        token = await self._get_access_token()
        if not token:
            return False

        body = {
            "summary": event["title"],
            "description": f"[{event.get('category', 'other')}] {event.get('notes', '')}",
            "start": {
                "dateTime": datetime.fromtimestamp(event["start_ts_utc"], tz=timezone.utc).isoformat(),
            },
            "end": {
                "dateTime": datetime.fromtimestamp(event["end_ts_utc"], tz=timezone.utc).isoformat(),
            },
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.put(
                f"https://www.googleapis.com/calendar/v3/calendars/{self._calendar_id}/events/{gcal_id}",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
            return r.status_code in (200, 201)

    async def delete_event(self, gcal_id: str) -> bool:
        token = await self._get_access_token()
        if not token:
            return False
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.delete(
                f"https://www.googleapis.com/calendar/v3/calendars/{self._calendar_id}/events/{gcal_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            return r.status_code in (200, 204)

    async def pull_events(self, days: int = 7) -> list[dict]:
        """Trae eventos de los proximos `days` dias desde Google Calendar."""
        token = await self._get_access_token()
        if not token:
            return []
        now = datetime.now(tz=timezone.utc)
        time_max = (now + timedelta(days=days)).isoformat()
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"https://www.googleapis.com/calendar/v3/calendars/{self._calendar_id}/events",
                params={
                    "timeMin": now.isoformat(),
                    "timeMax": time_max,
                    "singleEvents": "true",
                    "orderBy": "startTime",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            if r.status_code != 200:
                return []
            return r.json().get("items", [])

    def revoke(self) -> bool:
        """Elimina el token local (desconecta Google Calendar)."""
        try:
            if TOKEN_PATH.exists():
                TOKEN_PATH.unlink()
            self._token = None
            return True
        except OSError:
            return False
