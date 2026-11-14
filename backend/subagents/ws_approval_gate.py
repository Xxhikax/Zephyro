"""
WebSocketApprovalGate — ApprovalGate implementation over the Tauri WS bridge.

Persists state to `storage/diagnostics/{request_id}.json` so the client can
resume after disconnect/reload, and resolves asyncio.Events when the user
clicks an approval button.
"""

import asyncio
import logging
import time
import uuid
from typing import Any, Optional

from .diagnostic_store import DiagnosticStore

log = logging.getLogger("zephyro.diagnostic.ws_gate")


class WebSocketApprovalGate:
    """ApprovalGate over WebSocket + JSON persistence."""

    def __init__(
        self,
        connection_registry: Any,
        store: DiagnosticStore,
        event_prefix: str = "diag",
    ) -> None:
        """
        event_prefix: prefix de los mensajes WS al frontend. Default "diag" para
        DiagnosticAgent. Para TerminalController usar "terminal", lo cual emite
        `terminal:approval`, `terminal:progress`, `terminal:resolved`, etc.
        """
        self._registry = connection_registry
        self._store = store
        self._prefix = event_prefix
        # Maps request_id → (event, resolver_result_holder)
        self._pending: dict[str, asyncio.Event] = {}

    def _make_request_id(self) -> str:
        return uuid.uuid4().hex[:8]

    async def start(self, user_id: str) -> str:
        """Allocate a request_id and mark status=running in the store."""
        request_id = self._make_request_id()
        self._store.save(request_id, {
            "request_id": request_id,
            "user_id": str(user_id),
            "status": "running",
            "created_at": time.time(),
            "progress_log": [],
            "action": None,
        })
        return request_id

    async def _push_or_broadcast(self, user_id: str, payload: dict) -> None:
        """Push to a specific user or broadcast to all when user_id is unknown."""
        if user_id:
            await self._registry.push(user_id, payload)
        else:
            await self._registry.broadcast(payload)

    async def send_approval_request(self, context_id: Any, text: str) -> str:
        """
        For the WS gate, `context_id` is actually the request_id returned by start().
        This deviates slightly from the Telegram adapter (where context_id = chat_id),
        because the WS side owns the request_id from the moment the task starts —
        it's what was returned to the HTTP caller.

        Nota: el payload NO lleva el timeout. El timeout real vive en el caller
        de ``wait_for_approval`` (DiagnosticAgent 300 s, TerminalController
        120 s), y agregar el parámetro acá obligaría a tocar los otros tres
        adapters del protocolo. El frontend usa su propio fallback.
        """
        request_id = str(context_id)
        state = self._store.load(request_id) or {"request_id": request_id}
        user_id = str(state.get("user_id", ""))

        state.update({
            "status": "awaiting_approval",
            "hypothesis_summary": text,
            "awaiting_since": time.time(),
        })
        self._store.save(request_id, state)
        self._pending[request_id] = asyncio.Event()

        await self._push_or_broadcast(user_id, {
            "type": f"{self._prefix}:approval",
            "request_id": request_id,
            "hypothesis_summary": text,
        })
        return request_id

    async def wait_for_approval(self, request_id: str, timeout: float = 300.0) -> str:
        event = self._pending.get(request_id)
        if event is None:
            # Si ya fue resuelto antes de que esperemos (raro), usar store
            state = self._store.load(request_id) or {}
            return state.get("action") or "ignore"
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            state = self._store.load(request_id) or {}
            action = state.get("action") or "ignore"
            return action
        except asyncio.TimeoutError:
            self._store.update(request_id, status="expired", action="ignore")
            state = self._store.load(request_id) or {}
            user_id = str(state.get("user_id", ""))
            await self._push_or_broadcast(user_id, {
                "type": f"{self._prefix}:expired",
                "request_id": request_id,
            })
            return "ignore"
        finally:
            self._pending.pop(request_id, None)

    async def send_message(self, context_id: Any, text: str) -> None:
        """Push a progress message for this request (context_id is request_id)."""
        request_id = str(context_id)
        self._store.append_progress(request_id, text)
        state = self._store.load(request_id) or {}
        user_id = str(state.get("user_id", ""))
        await self._push_or_broadcast(user_id, {
            "type": f"{self._prefix}:progress",
            "request_id": request_id,
            "text": text,
        })

    async def send_resolved(
        self, context_id: Any, summary: str = "", action: str = "completed"
    ) -> bool:
        """Cierra el run: marca el store y empuja el evento terminal a la UI.

        Devuelve False si el run ya tenía un estado terminal — el camino normal
        de aprobación lo resuelve `routers/chat.py`, y sin este guard el panel
        recibiría dos cierres. El store es la fuente de verdad, no un flag en
        memoria: sobrevive a que el agente se reinstancie.
        """
        request_id = str(context_id)
        state = self._store.load(request_id) or {}
        if state.get("status") in ("resolved", "expired"):
            return False

        self._store.update(request_id, status="resolved", action=action)
        user_id = str(state.get("user_id", ""))
        await self._push_or_broadcast(user_id, {
            "type": f"{self._prefix}:resolved",
            "request_id": request_id,
            "action": action,
            "summary": summary,
        })
        return True

    async def send_chunk(self, request_id: str, text: str) -> None:
        """Push a fix-prompt chunk to the UI (used for final 'view' output)."""
        state = self._store.load(request_id) or {}
        user_id = str(state.get("user_id", ""))
        await self._registry.push(user_id, {
            "type": f"{self._prefix}:chunk",
            "request_id": request_id,
            "text": text,
        })

    async def request_model_choice(
        self,
        user_id: str,
        problem_type: str,
        options: list[dict],
        timeout: float = 120.0,
    ) -> str:
        """
        Crea una nueva sesión de model-choice, envía la tarjeta al frontend y espera respuesta.
        Retorna el `id` de la opción elegida (o la primera opción como default en timeout).
        """
        request_id = self._make_request_id()
        self._store.save(request_id, {
            "request_id": request_id,
            "user_id": user_id,
            "status": "awaiting_model_choice",
            "created_at": time.time(),
            "awaiting_since": time.time(),
            "progress_log": [],
            "action": None,
        })
        self._pending[request_id] = asyncio.Event()

        default_choice = options[0]["id"] if options else "ignore"

        # timeout_sec y default_choice viajan al frontend para que el aviso de
        # call mode pueda ser concreto ("en 30 s sigo con <default>") en vez de
        # dejar que el gate auto-elija en silencio.
        await self._registry.push(user_id, {
            "type": f"{self._prefix}:model_choice",
            "request_id": request_id,
            "problem_type": problem_type,
            "options": options,
            "timeout_sec": timeout,
            "default_choice": default_choice,
        })

        try:
            await asyncio.wait_for(self._pending[request_id].wait(), timeout=timeout)
            loaded = self._store.load(request_id) or {}
            return loaded.get("action") or default_choice
        except asyncio.TimeoutError:
            self._store.update(request_id, status="expired", action=default_choice)
            await self._registry.push(user_id, {
                "type": f"{self._prefix}:expired",
                "request_id": request_id,
                "reason": "timeout",
                "auto_action": default_choice,
            })
            return default_choice
        finally:
            self._pending.pop(request_id, None)

    def resolve(self, request_id: str, action: str) -> bool:
        """Called by the WS handler when the user clicks a button."""
        # Allow any action string (model choice IDs or classic view/ignore/more_context)
        _classic = {"view", "ignore", "more_context"}
        if action not in _classic:
            # Treat as model choice or custom action — store as-is
            pass
        self._store.update(request_id, action=action, resolved_at=time.time())
        event = self._pending.get(request_id)
        if event is None:
            return False
        event.set()
        return True

    def cancel(self, request_id: str) -> None:
        """Explicit cancellation from the client — resolves as ignore + deletes."""
        event = self._pending.get(request_id)
        self._store.update(request_id, status="cancelled", action="ignore")
        if event:
            event.set()
