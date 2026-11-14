# modules/filesystem/file_ops.py
"""
Operaciones de sistema de archivos seguras para Zephyro.
- Read: Sin restricciones (lectura de cualquier archivo)
- Write/Create: Solo en ~/*/zephyro_workspace/ por defecto
- Edit/Delete: Solo en workspace (confirmación requerida)
- Seguridad: Blacklist de directorios críticos
- Log completo de todas las operaciones
"""
import os
import json
import time
import logging
import threading
from pathlib import Path
from typing import Optional, List, Dict, Tuple


# Configurar logging
_log_path = "./storage/file_ops.log"
os.makedirs("./storage", exist_ok=True)

logging.basicConfig(
    filename=_log_path,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
_logger = logging.getLogger("file_ops")

# Directorios en blacklist (no se puede leer ni escribir)
BLACKLIST_DIRS = frozenset([
    "/etc/shadow", "/etc/passwd", "/etc/sudoers",
    "/sys", "/proc", "/dev", "/boot",
    "/root/.ssh", "/home/brunoq/.ssh",
    "/var/log/auth.log",
])

# Solo se puede escribir en estos patrones (relativo al home del usuario)
WRITE_ALLOWED_PATTERNS = [
    "zephyro_workspace",
    "zephyro_files",
    ".zephyro",
]

# Extensiones peligrosas que no se pueden crear/editar
DANGEROUS_EXTENSIONS = frozenset([
    ".exe", ".sh", ".bash", ".zsh", ".fish",
    ".service", ".socket", ".timer",  # systemd
    ".so", ".dll", ".dylib",
])


class FileOps:
    """
    Interfaz segura de operaciones de archivos para Zephyro.

    Ejemplo de uso:
        fops = FileOps()
        content = fops.read("/home/brunoq/proyecto/main.py")
        fops.create_file("notes.txt", "Hola mundo")  # Va a zephyro_workspace/
        result = fops.list_dir("/home/brunoq/proyecto")
    """

    def __init__(self, user_home: str = None):
        self.user_home = user_home or os.path.expanduser("~")
        self.workspace = os.path.join(self.user_home, "zephyro_workspace")
        os.makedirs(self.workspace, exist_ok=True)
        _logger.info(f"FileOps inicializado. Workspace: {self.workspace}")

    # ------------------------------------------------------------------
    # Validaciones de seguridad
    # ------------------------------------------------------------------

    def _is_blacklisted(self, path: str) -> bool:
        """Verifica si el path está en la blacklist."""
        abs_path = os.path.abspath(path)
        for bl in BLACKLIST_DIRS:
            if abs_path.startswith(bl) or abs_path == bl:
                return True
        return False

    def _is_write_allowed(self, path: str) -> bool:
        """Verifica si se puede escribir en el path."""
        abs_path = os.path.abspath(path)
        # Debe estar dentro del workspace
        if abs_path.startswith(self.workspace):
            return True
        # O en algún directorio permitido en el home
        for pattern in WRITE_ALLOWED_PATTERNS:
            if pattern in abs_path:
                return True
        return False

    def _has_dangerous_extension(self, path: str) -> bool:
        """Verifica si el archivo tiene extensión peligrosa."""
        ext = Path(path).suffix.lower()
        return ext in DANGEROUS_EXTENSIONS

    def _resolve_write_path(self, filename: str) -> str:
        """Resuelve un filename a un path en el workspace."""
        if os.path.isabs(filename):
            return filename
        # Si es relativo, va al workspace
        return os.path.join(self.workspace, filename)

    # ------------------------------------------------------------------
    # READ
    # ------------------------------------------------------------------

    def read(self, path: str, max_chars: int = 50000) -> Tuple[bool, str]:
        """
        Lee el contenido de un archivo.

        Args:
            path: Ruta del archivo (absoluta o relativa al workspace)
            max_chars: Máximo de caracteres a leer

        Returns:
            (success: bool, content_or_error: str)
        """
        abs_path = os.path.abspath(path) if os.path.isabs(path) else \
                   os.path.join(self.workspace, path)

        _logger.info(f"READ: {abs_path}")

        if self._is_blacklisted(abs_path):
            return False, f"Acceso denegado: {abs_path} está en la lista de archivos protegidos."

        if not os.path.exists(abs_path):
            return False, f"Archivo no encontrado: {abs_path}"

        if not os.path.isfile(abs_path):
            return False, f"La ruta no es un archivo: {abs_path}"

        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(max_chars)

            if len(content) == max_chars:
                content += f"\n\n[... archivo truncado a {max_chars} caracteres]"

            return True, content

        except PermissionError:
            return False, f"Sin permisos para leer: {abs_path}"
        except Exception as e:
            return False, f"Error leyendo archivo: {str(e)}"

    # ------------------------------------------------------------------
    # LIST
    # ------------------------------------------------------------------

    def list_dir(self, path: str = None, show_hidden: bool = False) -> Tuple[bool, str]:
        """
        Lista el contenido de un directorio.

        Args:
            path: Directorio a listar (default: workspace)
            show_hidden: Mostrar archivos ocultos

        Returns:
            (success: bool, listing_or_error: str)
        """
        target = path or self.workspace
        abs_path = os.path.abspath(target)

        _logger.info(f"LIST: {abs_path}")

        if self._is_blacklisted(abs_path):
            return False, f"Acceso denegado: {abs_path}"

        if not os.path.exists(abs_path):
            return False, f"Directorio no encontrado: {abs_path}"

        if not os.path.isdir(abs_path):
            return False, f"No es un directorio: {abs_path}"

        try:
            entries = os.listdir(abs_path)
            if not show_hidden:
                entries = [e for e in entries if not e.startswith(".")]

            entries.sort()

            lines = [f"**{abs_path}/**\n"]
            dirs = []
            files = []

            for entry in entries:
                full = os.path.join(abs_path, entry)
                if os.path.isdir(full):
                    dirs.append(f"  📁 {entry}/")
                else:
                    size = os.path.getsize(full)
                    size_str = f"{size:,}" if size < 1024 else f"{size//1024}KB"
                    files.append(f"  📄 {entry} ({size_str})")

            lines.extend(sorted(dirs))
            lines.extend(sorted(files))
            lines.append(f"\n{len(dirs)} dirs, {len(files)} archivos")

            return True, "\n".join(lines)

        except PermissionError:
            return False, f"Sin permisos para listar: {abs_path}"
        except Exception as e:
            return False, f"Error listando directorio: {str(e)}"

    # ------------------------------------------------------------------
    # CREATE / WRITE
    # ------------------------------------------------------------------

    def create_file(self, path: str, content: str = "") -> Tuple[bool, str]:
        """
        Crea un nuevo archivo.

        Args:
            path: Nombre/ruta del archivo
            content: Contenido inicial

        Returns:
            (success: bool, message: str)
        """
        write_path = self._resolve_write_path(path)
        abs_path = os.path.abspath(write_path)

        _logger.info(f"CREATE: {abs_path}")

        if not self._is_write_allowed(abs_path):
            return False, (
                f"Solo puedo crear archivos dentro del workspace: {self.workspace}\n"
                f"El archivo se crearía en: {abs_path}"
            )

        if self._has_dangerous_extension(abs_path):
            return False, f"Extensión no permitida: {Path(abs_path).suffix}"

        if os.path.exists(abs_path):
            return False, f"El archivo ya existe: {abs_path}\nUsa 'editar' para modificarlo."

        try:
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(content)

            _logger.info(f"CREATE OK: {abs_path} ({len(content)} chars)")
            return True, f"✅ Archivo creado: {abs_path}"

        except Exception as e:
            _logger.error(f"CREATE FAILED: {abs_path} - {e}")
            return False, f"Error creando archivo: {str(e)}"

    def edit_file(self, path: str, new_content: str) -> Tuple[bool, str]:
        """
        Reemplaza el contenido completo de un archivo existente.

        Args:
            path: Ruta del archivo
            new_content: Nuevo contenido

        Returns:
            (success: bool, message: str)
        """
        write_path = self._resolve_write_path(path)
        abs_path = os.path.abspath(write_path)

        _logger.info(f"EDIT: {abs_path}")

        if not self._is_write_allowed(abs_path):
            return False, f"No tengo permisos para editar fuera del workspace."

        if not os.path.exists(abs_path):
            return False, f"Archivo no encontrado: {abs_path}"

        if self._has_dangerous_extension(abs_path):
            return False, f"Extensión no permitida para edición."

        try:
            # Crear backup
            backup_path = abs_path + ".bak"
            with open(abs_path, "r", encoding="utf-8") as f:
                original = f.read()
            with open(backup_path, "w", encoding="utf-8") as f:
                f.write(original)

            # Escribir nuevo contenido
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            _logger.info(f"EDIT OK: {abs_path}")
            return True, f"✅ Archivo editado: {abs_path}\n(Backup en {backup_path})"

        except Exception as e:
            _logger.error(f"EDIT FAILED: {abs_path} - {e}")
            return False, f"Error editando archivo: {str(e)}"

    def delete_file(self, path: str) -> Tuple[bool, str]:
        """
        Elimina un archivo (solo en workspace).

        Args:
            path: Ruta del archivo

        Returns:
            (success: bool, message: str)
        """
        write_path = self._resolve_write_path(path)
        abs_path = os.path.abspath(write_path)

        _logger.info(f"DELETE: {abs_path}")

        if not self._is_write_allowed(abs_path):
            return False, f"Solo puedo eliminar archivos dentro del workspace."

        if not os.path.exists(abs_path):
            return False, f"Archivo no encontrado: {abs_path}"

        try:
            os.remove(abs_path)
            _logger.info(f"DELETE OK: {abs_path}")
            return True, f"✅ Archivo eliminado: {abs_path}"
        except Exception as e:
            _logger.error(f"DELETE FAILED: {abs_path} - {e}")
            return False, f"Error eliminando: {str(e)}"

    # ------------------------------------------------------------------
    # SEARCH
    # ------------------------------------------------------------------

    def search_in_file(self, path: str, pattern: str) -> Tuple[bool, str]:
        """
        Busca un patrón de texto en un archivo.

        Args:
            path: Ruta del archivo
            pattern: Texto a buscar

        Returns:
            (success: bool, results: str)
        """
        success, content = self.read(path)
        if not success:
            return False, content

        import re
        lines = content.split("\n")
        matches = []
        for i, line in enumerate(lines, 1):
            if pattern.lower() in line.lower():
                matches.append(f"  L{i}: {line.rstrip()}")

        if not matches:
            return True, f"No se encontró '{pattern}' en {path}"

        return True, f"**{len(matches)} coincidencias de '{pattern}' en {path}:**\n" + "\n".join(matches[:20])

    # ------------------------------------------------------------------
    # INFO
    # ------------------------------------------------------------------

    def workspace_info(self) -> str:
        """Retorna información del workspace."""
        try:
            items = list(Path(self.workspace).rglob("*"))
            files = [f for f in items if f.is_file()]
            total_size = sum(f.stat().st_size for f in files)

            return (
                f"**Workspace:** {self.workspace}\n"
                f"📁 Archivos: {len(files)}\n"
                f"💾 Tamaño total: {total_size // 1024}KB"
            )
        except Exception as e:
            return f"Error obteniendo info del workspace: {e}"
