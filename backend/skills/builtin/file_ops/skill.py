"""File Operations skill — wrapper sobre modules/filesystem/file_ops.py"""
import re
from typing import Optional

from core.request_context import RequestContext
from skills.base import BaseSkill


class FileOpsSkill(BaseSkill):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._ops = None

    def _get_ops(self):
        if self._ops is None:
            from modules.filesystem.file_ops import FileOps
            self._ops = FileOps()
        return self._ops

    def matches_command(self, user_input: str) -> bool:
        lower = user_input.lower().strip()
        return (
            lower.startswith("lee el archivo ")
            or lower.startswith("lee archivo ")
            or lower.startswith("lista archivos")
            or lower.startswith("lista de archivos")
            or lower.startswith("crea archivo ")
            or lower.startswith("crea un archivo ")
        )

    async def handle(self, user_input: str, ctx: RequestContext) -> Optional[str]:
        if not self.matches_command(user_input):
            return None

        lower = user_input.lower().strip()

        try:
            ops = self._get_ops()
        except Exception as e:
            return f"Operaciones de archivos no disponibles: {e}"

        if lower.startswith("lee el archivo ") or lower.startswith("lee archivo "):
            return self._read(user_input, ops)
        elif lower.startswith("lista archivos") or lower.startswith("lista de archivos"):
            return self._list(user_input, ops)
        elif lower.startswith("crea archivo ") or lower.startswith("crea un archivo "):
            return self._create(user_input, ops)

        return None

    def _read(self, user_input: str, ops) -> str:
        match = re.search(r'(?:lee el archivo|lee archivo)\s+(.+)', user_input, re.IGNORECASE)
        if not match:
            return "Especifica el path del archivo. Ej: 'lee el archivo /home/brunoq/notas.txt'"

        path = match.group(1).strip().strip('"\'')
        success, content = ops.read(path)

        if success:
            lines = content.split('\n')
            preview = '\n'.join(lines[:50])
            suffix = f"\n[... {len(lines)-50} líneas más]" if len(lines) > 50 else ""
            return f"**{path}** ({len(lines)} líneas):\n\n```\n{preview}{suffix}\n```"
        return f"Error: {content}"

    def _list(self, user_input: str, ops) -> str:
        match = re.search(r'(?:lista de archivos|lista archivos)\s+(?:en\s+)?(.+)', user_input, re.IGNORECASE)
        path = match.group(1).strip() if match else None
        success, listing = ops.list_dir(path)
        return listing if success else f"Error: {listing}"

    def _create(self, user_input: str, ops) -> str:
        match = re.search(
            r'(?:crea un archivo|crea archivo)\s+([^\s]+)\s+(?:con|contenido:)?\s*(.*)',
            user_input, re.IGNORECASE | re.DOTALL
        )
        if not match:
            return "Especifica: 'crea archivo nombre.txt con [contenido]'"

        filename = match.group(1).strip()
        content = match.group(2).strip() if match.group(2) else ""
        success, msg = ops.create_file(filename, content)
        return msg
