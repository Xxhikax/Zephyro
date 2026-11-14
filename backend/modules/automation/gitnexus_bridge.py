"""
GitNexus Bridge — Comunicación MCP stdio con GitNexus CLI
Gestiona un subprocess `npx gitnexus mcp` y expone tools read-only
vía JSON-RPC sobre stdin/stdout.

Soporte multi-repo: cada tool acepta parámetro `repo` opcional.

Tools expuestas (read-only):
  - list_repos()
  - search(query, repo=None)
  - context(symbol, repo=None)
  - impact(symbol, repo=None, depth=3)
  - detect_changes(repo=None)
"""

import asyncio
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("gitnexus_bridge")


def _stderr_log_level(text: str) -> int:
    """Nivel de logging para una línea de stderr del MCP de GitNexus.

    El servidor usa pino, que escribe JSON con `level` numérico: 30=info,
    40=warn, 50=error. Las líneas que no son JSON (banner de versión, avisos de
    npx) son informativas. Solo warn/error suben a WARNING/ERROR, para que
    living_state no las convierta en "issues" y el DiagnosticAgent no las
    reporte como causa raíz.
    """
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            level = int(json.loads(stripped).get("level", 30))
        except (ValueError, TypeError, json.JSONDecodeError):
            return logging.DEBUG
        if level >= 50:
            return logging.ERROR
        if level >= 40:
            return logging.WARNING
        return logging.DEBUG
    # Texto plano: solo tratamos como error lo que se nombra como tal.
    low = stripped.lower()
    if any(k in low for k in ("error", "exception", "traceback", "fatal", "cannot", "failed")):
        return logging.ERROR
    return logging.DEBUG


class GitNexusBridge:
    """
    Puente async hacia GitNexus MCP server (JSON-RPC sobre stdio).
    Ciclo de vida: start() → query tools → stop()
    Auto-reconnect en caso de crash del proceso hijo.
    """

    # Timeout por defecto para una llamada MCP (segundos)
    DEFAULT_TIMEOUT = 30.0
    # Reintentos de reconexión antes de declarar el bridge muerto
    MAX_RECONNECT_ATTEMPTS = 3
    # Tiempo entre reintentos de reconexión
    RECONNECT_DELAY = 2.0

    def __init__(self):
        self._process: Optional[asyncio.subprocess.Process] = None
        self._request_id: int = 0
        self._pending: Dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._alive: bool = False
        self._reconnect_attempts: int = 0
        self._lock = asyncio.Lock()
        self._npx_path: Optional[str] = None

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    async def start(self) -> bool:
        """
        Inicia el proceso MCP de GitNexus.
        Returns True si el bridge está operativo, False si falla.
        """
        # Verificar que npx/gitnexus exista
        self._npx_path = shutil.which("npx")
        if not self._npx_path:
            logger.warning("[GitNexus] npx no encontrado. Node.js no instalado?")
            return False

        # Verificar que gitnexus esté instalado
        gitnexus_path = shutil.which("gitnexus")
        if not gitnexus_path:
            # Intentar con npx directamente (puede tener gitnexus como dep local)
            logger.info("[GitNexus] gitnexus CLI no en PATH, se usará 'npx gitnexus'")

        try:
            return await self._spawn_process()
        except Exception as e:
            logger.error(f"[GitNexus] Error iniciando proceso MCP: {e}")
            return False

    async def _spawn_process(self) -> bool:
        """Arranca el subprocess MCP."""
        try:
            self._process = await asyncio.create_subprocess_exec(
                self._npx_path, "gitnexus", "mcp",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._alive = True
            self._reconnect_attempts = 0

            # Iniciar lector de respuestas en background
            self._reader_task = asyncio.create_task(self._read_responses())
            # Leer stderr del proceso MCP para diagnosticar crashes
            self._stderr_task = asyncio.create_task(self._read_stderr())

            # Enviar initialize (handshake MCP)
            init_result = await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "zephyro", "version": "2.0"}
            })

            if init_result is not None:
                # Enviar notifications/initialized
                await self._send_notification("notifications/initialized", {})
                logger.info("[GitNexus] Bridge MCP iniciado correctamente")
                return True
            else:
                logger.error("[GitNexus] Handshake MCP falló")
                await self.stop()
                return False

        except FileNotFoundError:
            logger.error("[GitNexus] npx o gitnexus no encontrado")
            return False
        except Exception as e:
            logger.error(f"[GitNexus] Error spawning process: {e}")
            return False

    async def stop(self):
        """Detiene el proceso MCP limpiamente."""
        self._alive = False

        for task_name, task in [("reader", self._reader_task), ("stderr", self._stderr_task)]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass
            self._process = None

        # Cancelar todas las futures pendientes
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("GitNexus bridge stopped"))
        self._pending.clear()

        logger.info("[GitNexus] Bridge detenido")

    async def _reconnect(self) -> bool:
        """Intenta reconectar al proceso MCP."""
        if self._reconnect_attempts >= self.MAX_RECONNECT_ATTEMPTS:
            logger.error("[GitNexus] Máximo de reintentos alcanzado")
            return False

        self._reconnect_attempts += 1
        logger.info(
            f"[GitNexus] Reconectando (intento {self._reconnect_attempts}/"
            f"{self.MAX_RECONNECT_ATTEMPTS})..."
        )

        await self.stop()
        await asyncio.sleep(self.RECONNECT_DELAY)
        return await self._spawn_process()

    @property
    def is_available(self) -> bool:
        """True si el bridge está operativo."""
        return (
            self._alive
            and self._process is not None
            and self._process.returncode is None
        )

    async def health_check(self) -> Dict[str, Any]:
        """Verifica el estado del bridge."""
        if not self.is_available:
            return {"status": "down", "reason": "process not running"}

        try:
            repos = await self.list_repos()
            repo_list = repos if isinstance(repos, list) else []
            return {
                "status": "ok",
                "repos_count": len(repo_list),
                "repos": repo_list,
            }
        except Exception as e:
            return {"status": "degraded", "reason": str(e)}

    # =========================================================================
    # JSON-RPC COMMUNICATION
    # =========================================================================

    async def _send_request(
        self, method: str, params: Dict[str, Any],
        timeout: float = DEFAULT_TIMEOUT
    ) -> Optional[Any]:
        """Envía un request JSON-RPC y espera la respuesta."""
        if not self._process or not self._process.stdin:
            return None

        async with self._lock:
            self._request_id += 1
            req_id = self._request_id

        message = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }

        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending[req_id] = future

        try:
            line = json.dumps(message) + "\n"
            self._process.stdin.write(line.encode("utf-8"))
            await self._process.stdin.drain()

            result = await asyncio.wait_for(future, timeout=timeout)
            return result

        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            logger.warning(f"[GitNexus] Timeout en request {method} (id={req_id})")
            return None
        except Exception as e:
            self._pending.pop(req_id, None)
            logger.error(f"[GitNexus] Error en request {method}: {e}")
            return None

    async def _send_notification(self, method: str, params: Dict[str, Any]):
        """Envía una notificación JSON-RPC (sin id, sin respuesta)."""
        if not self._process or not self._process.stdin:
            return

        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }

        try:
            line = json.dumps(message) + "\n"
            self._process.stdin.write(line.encode("utf-8"))
            await self._process.stdin.drain()
        except Exception as e:
            logger.error(f"[GitNexus] Error enviando notificación {method}: {e}")

    async def _read_responses(self):
        """Lee respuestas del proceso MCP en loop."""
        try:
            while self._alive and self._process and self._process.stdout:
                line = await self._process.stdout.readline()
                if not line:
                    # Proceso cerró stdout
                    if self._alive:
                        logger.warning("[GitNexus] Proceso MCP cerró stdout")
                        asyncio.create_task(self._handle_process_death())
                    break

                try:
                    data = json.loads(line.decode("utf-8").strip())
                except json.JSONDecodeError:
                    continue

                # Respuesta a un request
                if "id" in data and data["id"] in self._pending:
                    future = self._pending.pop(data["id"])
                    if not future.done():
                        if "error" in data:
                            future.set_exception(
                                RuntimeError(
                                    f"MCP error: {data['error'].get('message', str(data['error']))}"
                                )
                            )
                        else:
                            future.set_result(data.get("result"))

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[GitNexus] Error en reader: {e}")

    async def _read_stderr(self):
        """Lee y registra stderr del proceso MCP para diagnosticar crashes.

        El MCP escribe TODO por stderr, incluido su banner de versión y sus logs
        de arranque (pino JSON con `level` numérico: 30=info, 40=warn, 50=error).
        Loguearlo entero como WARNING hacía que living_state levantara cada línea
        como "issue" y el DiagnosticAgent contara "4 errores encontrados" cuando
        no había ninguno. Acá se respeta el nivel real de cada línea.
        """
        try:
            stderr = self._process.stderr
            if stderr is None:
                return
            while self._alive:
                line = await stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if not text:
                    continue
                logger.log(_stderr_log_level(text), "[GitNexus] [stderr] %s", text)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[GitNexus] Error leyendo stderr: {e}")

    async def _handle_process_death(self):
        """Maneja la muerte inesperada del proceso MCP."""
        if not self._alive:
            return
        exit_code = self._process.returncode if self._process else "unknown"
        logger.warning(
            "[GitNexus] Proceso MCP murió (exit code: %s), intento %d/%d",
            exit_code, self._reconnect_attempts + 1, self.MAX_RECONNECT_ATTEMPTS,
        )

        if self._reconnect_attempts >= self.MAX_RECONNECT_ATTEMPTS:
            logger.error("[GitNexus] Circuit breaker abierto — no se reintenta más")
            return

        success = await self._reconnect()
        if not success:
            logger.error("[GitNexus] No se pudo reconectar al proceso MCP")

    # =========================================================================
    # UTILITY: limpiar respuestas MCP
    # =========================================================================

    @staticmethod
    def _extract_json(text: str) -> str:
        """
        Extrae el bloque JSON de una respuesta MCP que puede tener
        markdown decorativo al final (e.g. '---\\n**Next:** ...').
        """
        text = text.strip()
        if not text:
            return text

        # Si empieza con [ o {, buscar dónde termina el JSON
        if text.startswith(("[", "{")):
            bracket = "]" if text.startswith("[") else "}"
            depth = 0
            in_string = False
            escape = False
            for i, ch in enumerate(text):
                if escape:
                    escape = False
                    continue
                if ch == "\\":
                    escape = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch in ("{", "["):
                    depth += 1
                elif ch in ("}", "]"):
                    depth -= 1
                    if depth == 0:
                        return text[: i + 1]
        return text

    # =========================================================================
    # MCP TOOL CALLS (read-only)
    # =========================================================================

    async def _call_tool(
        self, tool_name: str, arguments: Dict[str, Any],
        timeout: float = DEFAULT_TIMEOUT
    ) -> Optional[Any]:
        """Invoca una tool MCP y retorna el resultado."""
        if not self.is_available:
            # Intentar reconectar una vez
            success = await self._reconnect()
            if not success:
                return None

        result = await self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        }, timeout=timeout)

        if result and isinstance(result, dict):
            # MCP tools/call devuelve {content: [{type, text}], isError?}
            content = result.get("content", [])
            if result.get("isError"):
                error_text = content[0].get("text", "Unknown error") if content else "Unknown error"
                logger.error(f"[GitNexus] Tool {tool_name} error: {error_text}")
                return {"error": error_text}

            # Extraer texto del primer content item
            if content and isinstance(content, list):
                text = content[0].get("text", "")
                # GitNexus MCP a veces agrega markdown al final del JSON
                # (e.g. "---\n**Next:** ..."), hay que limpiar antes de parsear
                cleaned = self._extract_json(text)
                try:
                    return json.loads(cleaned)
                except (json.JSONDecodeError, TypeError):
                    return text

        return result

    async def list_repos(self) -> Optional[List[Dict[str, Any]]]:
        """Lista todos los repositorios indexados."""
        return await self._call_tool("list_repos", {})

    async def search(
        self, query: str, repo: Optional[str] = None
    ) -> Optional[Any]:
        """
        Búsqueda híbrida (BM25 + semántica + RRF) en el knowledge graph.
        Args:
            query: Texto de búsqueda
            repo: Nombre del repositorio (opcional, busca en todos si None)
        """
        args = {"query": query}
        if repo:
            args["repo"] = repo
        return await self._call_tool("search", args)

    async def context(
        self, symbol: str, repo: Optional[str] = None
    ) -> Optional[Any]:
        """
        Vista 360° de un símbolo (refs entrantes/salientes, procesos).
        Args:
            symbol: Nombre del símbolo (función, clase, método)
            repo: Nombre del repositorio (opcional)
        """
        args = {"symbol": symbol}
        if repo:
            args["repo"] = repo
        return await self._call_tool("context", args)

    async def impact(
        self, symbol: str, repo: Optional[str] = None, depth: int = 3
    ) -> Optional[Any]:
        """
        Análisis de blast radius con profundidad y confianza.
        Args:
            symbol: Nombre del símbolo a analizar
            repo: Nombre del repositorio (opcional)
            depth: Profundidad del análisis (default 3)
        """
        args = {"symbol": symbol, "depth": depth}
        if repo:
            args["repo"] = repo
        return await self._call_tool("impact", args)

    async def detect_changes(
        self, repo: Optional[str] = None
    ) -> Optional[Any]:
        """
        Git-diff → mapea líneas cambiadas a procesos afectados.
        Args:
            repo: Nombre del repositorio (opcional)
        """
        args = {}
        if repo:
            args["repo"] = repo
        return await self._call_tool("detect_changes", args)

    # =========================================================================
    # UTILITY: formatear resultados para inyección en prompts
    # =========================================================================

    def format_for_prompt(self, tool_name: str, result: Any, max_tokens: int = 800) -> str:
        """
        Formatea el resultado de una tool para inyectar en el prompt del LLM.
        Trunca si excede max_tokens estimados (~4 chars/token).
        """
        if result is None:
            return ""
        if isinstance(result, dict) and "error" in result:
            return f"[GitNexus Error: {result['error']}]"

        try:
            if isinstance(result, (dict, list)):
                text = json.dumps(result, ensure_ascii=False, indent=2)
            else:
                text = str(result)
        except Exception:
            text = str(result)

        # Truncar si excede límite
        max_chars = max_tokens * 4
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... [resultado truncado]"

        return f"[CODE CONTEXT — {tool_name}]\n{text}\n[/CODE CONTEXT]"

    def format_for_display(self, tool_name: str, result: Any) -> Dict[str, Any]:
        """
        Formatea el resultado para display estructurado en el frontend.
        Returns dict con tipo, datos y metadatos para rendering.
        """
        if result is None:
            return {"type": "error", "message": "Sin resultado de GitNexus"}
        if isinstance(result, dict) and "error" in result:
            return {"type": "error", "message": result["error"]}

        return {
            "type": "code_analysis",
            "tool": tool_name,
            "data": result,
            "timestamp": time.time(),
        }
