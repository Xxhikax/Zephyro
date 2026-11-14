"""
ApprovalGate Protocol — canal-agnostic interface for diagnostic approval flows.

Duck-typed so DiagnosticAgent can run over Telegram, WebSocket (Tauri), or any
future channel without knowing the transport. Implementations live alongside
their channel:
    - TelegramApprovalGate → backend/channels/telegram_channel.py
    - WebSocketApprovalGate → backend/subagents/ws_approval_gate.py
"""

from typing import Protocol, runtime_checkable, Union

ContextId = Union[int, str]


@runtime_checkable
class ApprovalGate(Protocol):
    """Transport-agnostic contract for sending progress and awaiting user approval."""

    async def send_approval_request(self, context_id: ContextId, text: str) -> str:
        """
        Show an approval prompt to the user. Returns a request_id the agent can
        use to wait on the response. For Telegram this is the message_id (as str);
        for WebSocket it's a uuid prefix.
        """
        ...

    async def wait_for_approval(self, request_id: str, timeout: float = 300.0) -> str:
        """
        Block until the user picks an action. Returns one of:
            "view" | "ignore" | "more_context"
        On timeout, returns "ignore".
        """
        ...

    async def send_message(self, context_id: ContextId, text: str) -> None:
        """Push a plain progress message to the user (no approval UI)."""
        ...
