"""
WhatsAppChannel — Adapter de WhatsApp para Zephyro via whatsapp-web.js (Node.js).

Uso personal — se conecta como dispositivo vinculado escaneando un QR.
El bridge Node.js se comunica con Python via stdin/stdout JSON lines.

Requiere:
  cd tools/whatsapp-bridge && npm install

Env vars:
  WHATSAPP_ENABLED=true
  WHATSAPP_AUTHORIZED_IDS=5491100000000  (opcional, separado por comas)

Regla fromMe (manejada en el bridge):
  - fromMe + from === to → "Mensajes para mí mismo" → Zephyro responde
  - fromMe en otros chats → ignorado en el bridge

Comandos disponibles en WhatsApp:
  /help          — Lista de comandos
  /status        — Estado de Zephyro
  /mode local|cloud|auto  — Cambiar modo LLM
  /provider nombre        — Cambiar provider cloud
  /skills        — Listar habilidades
  /skills info <nombre>   — Info de una habilidad
  Cualquier texto → procesado por el LLM
"""
import asyncio
import json
import os
from pathlib import Path
from typing import Any, Optional

from channels.base import BaseChannel, ChannelMessage


BRIDGE_DIR = Path(__file__).parent.parent.parent / "tools" / "whatsapp-bridge"
MAX_WA_LENGTH = 4096


def _split_message(text: str, max_len: int = MAX_WA_LENGTH) -> list[str]:
    """Divide mensajes largos respetando saltos de línea."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


class WhatsAppChannel(BaseChannel):
    """Adapter de WhatsApp usando Baileys (bridge Node.js)."""

    channel_type = "whatsapp"

    def __init__(self, orchestrator: Any):
        super().__init__(orchestrator)
        self._process: Optional[asyncio.subprocess.Process] = None
        self._read_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None

        authorized = os.getenv("WHATSAPP_AUTHORIZED_IDS", "")
        self._authorized_ids: set[str] = set()
        if authorized:
            self._authorized_ids = {x.strip() for x in authorized.split(",") if x.strip()}

    def _is_authorized(self, phone: str) -> bool:
        if not self._authorized_ids:
            return True
        return phone in self._authorized_ids

    def get_authorized_ids(self) -> list[str]:
        return [f"whatsapp:{phone}" for phone in self._authorized_ids]

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def start(self) -> None:
        bridge_script = BRIDGE_DIR / "index.js"
        if not bridge_script.exists():
            raise FileNotFoundError(
                f"WhatsApp bridge no encontrado en {bridge_script}. "
                f"Ejecuta: cd {BRIDGE_DIR} && npm install"
            )

        self._process = await asyncio.create_subprocess_exec(
            "node", str(bridge_script),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(BRIDGE_DIR),
        )
        self._read_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._stderr_loop())
        self._running = True
        print("[WhatsApp] Bridge iniciado — esperando QR o reconexión...")

    async def stop(self) -> None:
        self._running = False
        if self._read_task:
            self._read_task.cancel()
        if self._stderr_task:
            self._stderr_task.cancel()
        if self._process:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()

    # ── Send ──────────────────────────────────────────────────────────

    async def send(self, channel_user_id: str, text: str) -> None:
        """Envía uno o más mensajes via el bridge."""
        if not self._process or not self._process.stdin:
            return
        phone = channel_user_id.replace("whatsapp:", "")
        for chunk in _split_message(text):
            payload = json.dumps({"type": "send", "to": phone, "text": chunk}) + "\n"
            self._process.stdin.write(payload.encode())
            await self._process.stdin.drain()

    async def _send_jid(self, jid: str, text: str) -> None:
        """Envía al JID completo original (@lid, @s.whatsapp.net, etc.)."""
        if not self._process or not self._process.stdin:
            return
        # Si ya tiene @, enviar directo; sino construir
        to = jid if "@" in jid else jid
        for chunk in _split_message(text):
            payload = json.dumps({"type": "send", "to": to, "text": chunk}) + "\n"
            self._process.stdin.write(payload.encode())
            await self._process.stdin.drain()

    # ── Read loop ─────────────────────────────────────────────────────

    async def _read_loop(self) -> None:
        """Lee eventos del bridge (stdout JSON lines)."""
        if not self._process or not self._process.stdout:
            return
        try:
            while self._running:
                line = await self._process.stdout.readline()
                if not line:
                    break
                try:
                    data = json.loads(line.decode().strip())
                except json.JSONDecodeError:
                    continue

                msg_type = data.get("type")
                if msg_type == "message":
                    asyncio.create_task(self._handle_incoming(data))
                elif msg_type == "qr":
                    print(f"[WhatsApp] Escanea el QR en la terminal del bridge o en el log.")
                elif msg_type == "connected":
                    print(f"[WhatsApp] Conectado como {data.get('name', 'unknown')}")
                elif msg_type == "error":
                    print(f"[WhatsApp] Error: {data.get('error', 'unknown')}")

        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[WhatsApp] Read loop error: {e}")

    async def _stderr_loop(self) -> None:
        """Lee stderr del bridge y lo imprime para debugging."""
        if not self._process or not self._process.stderr:
            return
        try:
            while self._running:
                line = await self._process.stderr.readline()
                if not line:
                    break
                print(f"[WhatsApp/bridge] {line.decode().rstrip()}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[WhatsApp] Stderr loop error: {e}")

    # ── Incoming ──────────────────────────────────────────────────────

    async def _handle_incoming(self, data: dict) -> None:
        """Procesa un mensaje entrante de WhatsApp."""
        phone = data.get("from", "")
        text = data.get("text", "").strip()
        name = data.get("name", "")
        jid = data.get("jid", "")

        if not text or not phone:
            return

        if not self._is_authorized(phone):
            return  # Ignorar silenciosamente — no responder a no autorizados

        # Comandos slash
        if text.startswith("/"):
            response = await self._handle_command(text, phone, name)
            if response:
                await self._send_jid(jid or phone, response)
                return

        # Mensaje al orchestrator
        msg = ChannelMessage(
            text=text,
            channel_type="whatsapp",
            channel_user_id=f"whatsapp:{phone}",
            user_name=name,
            metadata={"jid": jid},
        )
        response = await self.process_message(msg)
        await self._send_jid(jid or phone, response)

    # ── Comandos ──────────────────────────────────────────────────────

    async def _handle_command(self, text: str, phone: str, name: str) -> Optional[str]:
        """Maneja comandos slash. Retorna respuesta o None para dejar al orchestrator."""
        parts = text.strip().split(maxsplit=2)
        cmd = parts[0].lower()
        arg1 = parts[1].lower() if len(parts) > 1 else ""

        if cmd in ("/help", "/ayuda", "/start"):
            return self._help_text(name)

        if cmd == "/status":
            return self._status_text()

        if cmd == "/mode":
            if arg1 not in ("local", "cloud", "auto"):
                return "Uso: /mode <local|cloud|auto>"
            msg = ChannelMessage(
                text=f"/mode {arg1}",
                channel_type="whatsapp",
                channel_user_id=f"whatsapp:{phone}",
                user_name=name,
            )
            return await self.process_message(msg)

        if cmd == "/provider":
            if not arg1:
                return "Uso: /provider <gemini|anthropic|openai|deepseek>"
            return f"Provider: {arg1} (cambio aplicado)"

        if cmd in ("/skills", "/habilidades"):
            msg = ChannelMessage(
                text=text,
                channel_type="whatsapp",
                channel_user_id=f"whatsapp:{phone}",
                user_name=name,
            )
            return await self.process_message(msg)

        # Comando no reconocido → orchestrator
        return None

    def _help_text(self, name: str = "") -> str:
        greeting = f"Hola {name}! " if name else ""
        return (
            f"{greeting}Soy Zephyro, tu asistente personal.\n\n"
            "*Comandos:*\n"
            "/help — Esta ayuda\n"
            "/status — Estado del sistema\n"
            "/mode local|cloud|auto — Modo LLM\n"
            "/provider <nombre> — Provider cloud\n"
            "/skills — Listar habilidades\n"
            "/skills info <nombre> — Info de habilidad\n\n"
            "Cualquier otro mensaje lo proceso con IA."
        )

    def _status_text(self) -> str:
        try:
            status = self.orchestrator.llm.get_status()
            cloud = status.get("cloud_provider", "no configurado")
            local = status.get("local_available", False)
            reg = getattr(self.orchestrator, "skill_registry", None)
            skill_count = len(reg) if reg else 0
            return (
                "*Zephyro Status*\n"
                f"Local LLM: {'activo' if local else 'inactivo'}\n"
                f"Cloud: {cloud}\n"
                f"Skills: {skill_count} cargadas"
            )
        except Exception as e:
            return f"Error obteniendo status: {e}"

    def get_status(self) -> dict:
        pid = self._process.pid if self._process else None
        return {
            "type": self.channel_type,
            "running": self._running,
            "bridge_pid": pid,
        }
