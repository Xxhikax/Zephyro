"""System Monitor skill — monitoreo de RAM, CPU, disco, red y procesos."""
import asyncio
import re
from typing import Optional

import psutil

from core.request_context import RequestContext
from skills.base import BaseSkill


# Patterns agrupados por categoria
_PATTERNS = {
    "ram": [
        re.compile(r'(cu[aá]nt[ao]|uso|consumo).*(ram|memoria)', re.IGNORECASE),
        re.compile(r'(ram|memoria).*(us[ao]|consum|libre|disponible)', re.IGNORECASE),
    ],
    "cpu": [
        re.compile(r'(uso|consumo|temperatura|carga).*(cpu|procesador)', re.IGNORECASE),
        re.compile(r'(cpu|procesador).*(uso|consumo|temperatura|carga)', re.IGNORECASE),
    ],
    "disk": [
        re.compile(r'(espacio|uso|consumo).*(disco|almacenamiento)', re.IGNORECASE),
        re.compile(r'(disco|almacenamiento).*(libre|disponible|us[ao])', re.IGNORECASE),
    ],
    "network": [
        re.compile(r'(uso|consumo|estad[ií]sticas?).*(red|network|internet)', re.IGNORECASE),
        re.compile(r'(red|network).*(uso|consumo|estad[ií]sticas?)', re.IGNORECASE),
    ],
    "processes": [
        re.compile(r'(procesos|tareas).*(sistema|corriendo|activos)', re.IGNORECASE),
        re.compile(r'(mu[eé]strame|lista|ver).*(procesos|tareas)', re.IGNORECASE),
    ],
    "full": [
        re.compile(r'(estado|info|informaci[oó]n).*(sistema|pc|computador)', re.IGNORECASE),
    ],
}

# Slash command → categoria
_SLASH_MAP = {
    "/ram": "ram",
    "/cpu": "cpu",
    "/disk": "disk",
    "/red": "network",
    "/system": "full",
}


class SystemMonitorSkill(BaseSkill):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def matches_command(self, user_input: str) -> bool:
        # Slash commands via BaseSkill triggers
        if super().matches_command(user_input):
            return True
        # Natural language patterns
        lower = user_input.lower().strip()
        for patterns in _PATTERNS.values():
            for p in patterns:
                if p.search(lower):
                    return True
        return False

    async def handle(self, user_input: str, ctx: RequestContext) -> Optional[str]:
        if not self.matches_command(user_input):
            return None

        category = self._detect_category(user_input)

        handlers = {
            "ram": self._format_ram,
            "cpu": self._format_cpu,
            "disk": self._format_disk,
            "network": self._format_network,
            "processes": self._format_top_processes,
            "full": self._format_full_report,
        }
        handler = handlers.get(category, self._format_full_report)
        return await handler()

    def _detect_category(self, user_input: str) -> str:
        lower = user_input.lower().strip()

        # Check slash commands first
        for slash, cat in _SLASH_MAP.items():
            if lower == slash or lower.startswith(slash + " "):
                return cat

        # Check regex patterns
        for cat, patterns in _PATTERNS.items():
            for p in patterns:
                if p.search(lower):
                    return cat

        return "full"

    # ── Sub-handlers ─────────────────────────────────────────────────

    async def _format_ram(self) -> str:
        mem = psutil.virtual_memory()
        lines = [
            "-- RAM --",
            f"Total:      {self._human_bytes(mem.total)}",
            f"Usado:      {self._human_bytes(mem.used)}",
            f"Disponible: {self._human_bytes(mem.available)}",
            f"Uso:        {mem.percent}% {self._bar(mem.percent)}",
        ]
        return "\n".join(lines)

    async def _format_cpu(self) -> str:
        per_cpu = await asyncio.to_thread(
            psutil.cpu_percent, interval=0.5, percpu=True
        )
        overall = sum(per_cpu) / len(per_cpu) if per_cpu else 0.0

        lines = [
            "-- CPU --",
            f"Global: {overall:.1f}% {self._bar(overall)}",
        ]
        for i, pct in enumerate(per_cpu):
            lines.append(f"  Core {i}: {pct:5.1f}% {self._bar(pct)}")

        try:
            load1, load5, load15 = psutil.getloadavg()
            lines.append(f"Load avg: {load1:.2f} / {load5:.2f} / {load15:.2f}")
        except (AttributeError, OSError):
            pass

        return "\n".join(lines)

    async def _format_disk(self) -> str:
        lines = ["-- Disco --"]
        for part in psutil.disk_partitions():
            if "squashfs" in part.fstype or "/snap/" in part.mountpoint:
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except PermissionError:
                continue
            lines.append(
                f"{part.mountpoint} ({part.fstype})\n"
                f"  Total: {self._human_bytes(usage.total)}  "
                f"Usado: {self._human_bytes(usage.used)}  "
                f"Libre: {self._human_bytes(usage.free)}\n"
                f"  Uso:  {usage.percent}% {self._bar(usage.percent)}"
            )
        return "\n".join(lines)

    async def _format_network(self) -> str:
        counters = psutil.net_io_counters()
        lines = [
            "-- Red --",
            f"Enviados:  {self._human_bytes(counters.bytes_sent)}",
            f"Recibidos: {self._human_bytes(counters.bytes_recv)}",
            f"Paquetes enviados:  {counters.packets_sent:,}",
            f"Paquetes recibidos: {counters.packets_recv:,}",
        ]
        return "\n".join(lines)

    async def _format_top_processes(self, n: int = 5) -> str:
        procs = []
        for p in psutil.process_iter(["pid", "name", "memory_percent"]):
            try:
                info = p.info
                if info["memory_percent"] is not None:
                    procs.append(info)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        procs.sort(key=lambda x: x["memory_percent"], reverse=True)
        top = procs[:n]

        lines = [f"-- Top {n} procesos (por RAM) --"]
        for p in top:
            lines.append(
                f"  {p['name']:<25} PID {p['pid']:<7} "
                f"{p['memory_percent']:.1f}%"
            )
        return "\n".join(lines)

    async def _format_full_report(self) -> str:
        parts = await asyncio.gather(
            self._format_ram(),
            self._format_cpu(),
            self._format_disk(),
            self._format_network(),
            self._format_top_processes(),
        )
        return "\n\n".join(parts)

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _bar(percent: float, width: int = 20) -> str:
        filled = int(round(percent / 100 * width))
        return "[" + "\u2593" * filled + "\u2591" * (width - filled) + "]"

    @staticmethod
    def _human_bytes(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if abs(n) < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} PB"
