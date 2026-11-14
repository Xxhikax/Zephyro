"""
VerificationAgent — validador post-ejecución de skills.

Suscriptor de SKILL_EXECUTED en el event bus. Fire-and-forget: la
respuesta al usuario ya fue enviada; la verificación llega 1-3s después
como mensaje WS `{"type": "verification", ...}`.

Dos caminos:
  A) Reglas deterministas (gratis, <10ms): returncode, git log, os.path.exists
  B) Coherence check con DeepSeek Flash (<$0.0001, ~1s): solo para skills
     donde puede haber alucinación real (git, terminal, github, file-ops)
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("zephyro.verification")

# Skills con regla determinista disponible
_RULE_MAP: dict[str, str] = {
    "git-ops":     "_verify_git",
    "git_ops":     "_verify_git",
    "github":      "_verify_git",
    "file-ops":    "_verify_file",
    "file_ops":    "_verify_file",
    "terminal":    "_verify_terminal",
    "reminders":   "_verify_storage",
    "schedule":    "_verify_storage",
    "app-launcher": "_verify_app_launched",
}

# Skills donde el LLM puede alucinar — activa coherence check
_LLM_CHECK_SKILLS: frozenset[str] = frozenset({
    "git-ops", "git_ops", "github", "terminal", "file-ops", "file_ops",
})

# Patrón: LLM describió una acción futura en vez de confirmar ejecución
_DESCRIBE_INSTEAD_RE = re.compile(
    r'\b(voy\s+a\s+(?:ejecutar|navegar|abrir|realizar|intentar)|'
    r'navegaré|abriré|proceder[eé]\s+a|realizaré|llevaré\s+a\s+cabo|'
    r'lo\s+har[eé]|deber[ií]a\s+(?:estar|funcionar|abrirse|verse))\b',
    re.IGNORECASE,
)
# Patrón: confirmación real de ejecución (no descripción futura)
_CONFIRMED_EXECUTION_RE = re.compile(
    r'✓|abriendo|navegando|pestaña\s+abierta|ejecutado|completado|listo|'
    r'abrió|navegó|se\s+abrió|está\s+abierto|redirigiendo|redirigió',
    re.IGNORECASE,
)


class VerificationAgent:
    """
    Suscriptor de SKILL_EXECUTED.

    Uso:
        agent = VerificationAgent(conn_registry=registry, code_root="/path/to/repo")
        get_bus().subscribe(SKILL_EXECUTED, agent.on_skill_executed)
    """

    def __init__(
        self,
        conn_registry,
        code_root: str = ".",
        audit_path: Optional[str] = None,
    ) -> None:
        self._conn_registry = conn_registry
        self._code_root = Path(code_root)
        self._audit_path = Path(audit_path) if audit_path else None

    # ── Event bus entry point ──────────────────────────────────────────

    async def on_skill_executed(self, event: dict) -> None:
        """Listener async del event bus — lanza verificación fire-and-forget."""
        import asyncio
        asyncio.create_task(self._verify_async(event))

    # ── Core verification pipeline ─────────────────────────────────────

    async def _verify_async(self, event: dict) -> None:
        skill_name = event.get("skill_name", "unknown")
        user_input = event.get("input_preview", "")
        response = event.get("response", "")

        # Paso 1: regla determinista (gratis, instantánea)
        rule_result: Optional[dict] = None
        rule_fn_name = _RULE_MAP.get(skill_name)
        if rule_fn_name:
            try:
                rule_result = await getattr(self, rule_fn_name)(event)
            except Exception as exc:
                log.debug("[verification] rule '%s' falló: %s", rule_fn_name, exc)
                rule_result = {"ok": None, "evidence": f"rule error: {exc}"}

        # Paso 2: coherence check con DeepSeek Flash (solo skills de alto riesgo)
        llm_result: Optional[dict] = None
        if skill_name in _LLM_CHECK_SKILLS and response:
            try:
                llm_result = await self._llm_coherence_check(user_input, response, rule_result)
            except Exception as exc:
                log.debug("[verification] LLM check falló: %s", exc)

        # Paso 3: construir veredicto y notificar
        verdict = self._build_verdict(skill_name, rule_result, llm_result)
        await self._notify_clients(skill_name, verdict, rule_result, llm_result)

        # Paso 3b: para app-launcher emitir además un evento específico
        if skill_name == "app-launcher":
            await self._notify_app_launched(event, rule_result)

        if self._audit_path:
            self._write_audit_log(skill_name, user_input, response, verdict)

    # ── Reglas deterministas ───────────────────────────────────────────

    async def _verify_git(self, event: dict) -> dict:
        """Verifica git ops con `git log --oneline -1`."""
        try:
            proc = subprocess.run(
                ["git", "log", "--oneline", "-1"],
                capture_output=True, text=True, timeout=5,
                cwd=str(self._code_root),
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return {"ok": True, "evidence": proc.stdout.strip()}
            return {"ok": None, "evidence": proc.stderr.strip() or "no commits found"}
        except FileNotFoundError:
            return {"ok": None, "evidence": "git not in PATH"}
        except subprocess.TimeoutExpired:
            return {"ok": None, "evidence": "git log timeout"}

    async def _verify_terminal(self, event: dict) -> dict:
        """Verifica terminal skill extrayendo exit code de la respuesta."""
        response = event.get("response", "")
        m = re.search(r'\bexit\s+(\d+)\b', response)
        if m:
            code = int(m.group(1))
            return {"ok": code == 0, "evidence": f"exit_code={code}"}
        return {"ok": None, "evidence": "exit code not found in response"}

    async def _verify_file(self, event: dict) -> dict:
        """Verifica file-ops buscando paths en input/respuesta."""
        combined = event.get("input_preview", "") + " " + event.get("response", "")
        # buscar paths entre backticks o comillas
        candidates = re.findall(r'`([^`]+\.[a-zA-Z]{2,6})`|"([^"]+\.[a-zA-Z]{2,6})"', combined)
        for groups in candidates:
            for path_str in groups:
                if not path_str:
                    continue
                p = Path(path_str)
                if not p.is_absolute():
                    p = self._code_root / path_str
                if p.exists():
                    size = p.stat().st_size
                    return {"ok": True, "evidence": f"{path_str}: exists ({size} bytes)"}
                return {"ok": False, "evidence": f"{path_str}: NOT FOUND"}
        return {"ok": None, "evidence": "no file paths detected"}

    async def _verify_app_launched(self, event: dict) -> dict:
        """
        Verifica que tras lanzar una app: el PID existe y la ventana es visible.
        Casos:
          - intended_native + fell_back_to_web → FAIL (app no encontrada, abrió URL)
          - already_running=True → ok (la app ya tenía ventana)
          - pid None             → no verificable (ej. web app o proceso multi-fork)
          - pid muerto           → fail
          - pid vivo + ventana   → ok
          - pid vivo sin ventana → fail (tray-only / crash temprano)
        """
        import asyncio as _asyncio
        import psutil
        from core.platform import wait_for_window

        extra = event.get("extra", {}) or {}

        # Caso especial: usuario pidió app nativa pero el launcher cayó a URL
        if extra.get("intended_native") and extra.get("fell_back_to_web"):
            app_name = extra.get("app", "?")
            url = extra.get("url", "")
            return {
                "ok": False,
                "evidence": (
                    f"'{app_name}' pedida como app nativa pero abrió URL ({url[:60]}). "
                    f"El binario no fue encontrado en el sistema."
                ),
            }

        pid = extra.get("pid")
        process_name = extra.get("process_name") or extra.get("app") or "?"
        already_running = bool(extra.get("already_running"))

        if already_running:
            return {"ok": True, "evidence": f"{process_name}: ya estaba abierto"}

        # URL correctness check: para web navigation, comparar target solicitado vs URL abierta.
        # Funciona aunque no haya PID (tabs de browser no tienen PID trackeable).
        if not pid:
            target = (extra.get("app") or process_name or "").lower().strip()
            actual_url = extra.get("url", "")
            if target and actual_url:
                try:
                    from core.app_config import WEB_APP_URLS as _WEB_APP_URLS
                    import re as _re
                    expected_url = _WEB_APP_URLS.get(target)
                    if expected_url:
                        # Comparar dominios (ignorar www. y path)
                        def _domain(url: str) -> str:
                            return _re.sub(r'^https?://(www\.)?', '', url).split('/')[0].lower()
                        exp_d = _domain(expected_url)
                        act_d = _domain(actual_url)
                        if exp_d and exp_d != act_d:
                            return {
                                "ok": False,
                                "evidence": (
                                    f"target='{target}': esperaba {exp_d} "
                                    f"pero se abrió {act_d}"
                                ),
                            }
                        return {"ok": True, "evidence": f"URL correcta: {act_d}"}
                except Exception:
                    pass
            return {"ok": None, "evidence": f"no PID capturable para {process_name}"}

        try:
            if not psutil.pid_exists(int(pid)):
                return {"ok": False, "evidence": f"pid {pid} no existe (proceso terminó)"}
            proc = psutil.Process(int(pid))
            try:
                status = proc.status()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                status = "unknown"
            if status == psutil.STATUS_ZOMBIE:
                return {"ok": False, "evidence": f"pid {pid} zombie"}
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return {"ok": False, "evidence": f"pid {pid} inaccesible"}

        # Apps de CONSOLA: la ventana NO es de ellas. Medido el 2026-10-02 en
        # Windows 11 — `powershell` queda con una `PseudoConsoleWindow` de 0x0
        # y título vacío, y la consola real pertenece a `WindowsTerminal.exe`,
        # un proceso que ya existía y no es hijo. Cruzar ventana por PID es
        # estructuralmente imposible acá, así que exigirla daría un falso
        # "crash" en CADA consola — y una alarma que suena siempre enseña a
        # ignorar la alarma. Para una consola, vivir ES el criterio.
        from core.platform import is_console_app
        if is_console_app(extra.get("app") or process_name):
            return {
                "ok": True,
                "evidence": (
                    f"{process_name} pid={pid} vivo (app de consola: la ventana "
                    f"la hostea el terminal, no el proceso)"
                ),
            }

        has_window = await _asyncio.to_thread(wait_for_window, int(pid), 3.0)
        if has_window:
            return {"ok": True, "evidence": f"{process_name} pid={pid}, ventana visible"}
        return {
            "ok": False,
            "evidence": f"{process_name} pid={pid} sin ventana visible (tray-only o crash)",
        }

    async def _verify_storage(self, event: dict) -> dict:
        """Verifica skills de storage buscando patrones de error en la respuesta."""
        response = event.get("response", "").lower()
        error_patterns = ("error", "falló", "failed", "no pude", "cannot", "exception", "traceback")
        for pat in error_patterns:
            if pat in response:
                return {"ok": False, "evidence": f"error pattern found: '{pat}'"}
        return {"ok": True, "evidence": "no error patterns detected"}

    # ── LLM coherence check ────────────────────────────────────────────

    async def _llm_coherence_check(
        self,
        user_input: str,
        response: str,
        rule_result: Optional[dict],
    ) -> dict:
        """DeepSeek Flash: detecta si la respuesta describe algo que no ocurrió."""
        from core.llm_helpers import call_deepseek_flash

        evidence_str = json.dumps(rule_result) if rule_result else "ninguna"
        prompt = (
            f'Verifica coherencia entre lo que pidió el usuario y la respuesta del asistente.\n\n'
            f'Input usuario: "{user_input[:200]}"\n'
            f'Respuesta asistente: "{response[:300]}"\n'
            f'Evidencia del sistema: {evidence_str}\n\n'
            f'Responde SOLO con JSON válido, sin texto adicional:\n'
            f'{{"coherent": true, "issue": null}}'
        )
        raw = await call_deepseek_flash(prompt)
        if not raw:
            return {"coherent": None, "issue": "DeepSeek Flash unavailable"}

        try:
            m = re.search(r'\{[^}]+\}', raw, re.DOTALL)
            if m:
                data = json.loads(m.group(0))
                return {
                    "coherent": bool(data.get("coherent", True)),
                    "issue": data.get("issue"),
                }
        except (json.JSONDecodeError, TypeError):
            pass
        return {"coherent": None, "issue": f"parse failed: {raw[:60]}"}

    # ── Helpers ────────────────────────────────────────────────────────

    def _build_verdict(
        self,
        skill_name: str,
        rule_result: Optional[dict],
        llm_result: Optional[dict],
    ) -> str:
        parts: list[str] = []

        if rule_result is not None:
            ok = rule_result.get("ok")
            marker = "ok" if ok is True else ("?" if ok is None else "FAIL")
            parts.append(f"[{marker}] {rule_result.get('evidence', '')}")

        if llm_result is not None and llm_result.get("coherent") is not None:
            if not llm_result["coherent"]:
                issue = llm_result.get("issue") or "incoherence detected"
                parts.append(f"[INCOHERENT] {issue}")

        if not parts:
            return "verificación completada sin evidencia"
        return " | ".join(parts)

    async def _notify_app_launched(
        self,
        event: dict,
        rule_result: Optional[dict],
    ) -> None:
        """Emite evento app:launched específico — más útil que el `verification` genérico."""
        extra = event.get("extra", {}) or {}
        ok = (rule_result or {}).get("ok")
        if ok is True:
            verdict = "ok"
        elif ok is False:
            verdict = "fail"
        else:
            verdict = "unknown"
        payload = {
            "type": "app:launched",
            "app": extra.get("app", "?"),
            "process_name": extra.get("process_name"),
            "pid": extra.get("pid"),
            "already_running": bool(extra.get("already_running")),
            "verdict": verdict,
            "reason": (rule_result or {}).get("evidence", ""),
            "ts": time.time(),
        }
        try:
            await self._conn_registry.broadcast(payload)
        except Exception as exc:
            log.debug("[verification] app:launched broadcast failed: %s", exc)

    async def _notify_clients(
        self,
        skill_name: str,
        verdict: str,
        rule_result: Optional[dict],
        llm_result: Optional[dict],
    ) -> None:
        payload = {
            "type": "verification",
            "skill": skill_name,
            "verdict": verdict,
            "rule_result": rule_result,
            "llm_check": llm_result,
            "coherent": llm_result.get("coherent") if llm_result else None,
        }
        try:
            await self._conn_registry.broadcast(payload)
        except Exception as exc:
            log.debug("[verification] broadcast failed: %s", exc)

    def _write_audit_log(
        self,
        skill_name: str,
        user_input: str,
        response: str,
        verdict: str,
    ) -> None:
        entry = {
            "ts": time.time(),
            "skill": skill_name,
            "input": user_input[:80],
            "response": response[:200],
            "verdict": verdict,
        }
        try:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self._audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            log.debug("[verification] audit write failed: %s", exc)

    # ── Automation / cloud-chat verification ──────────────────────────────

    async def on_automation_executed(self, event: dict) -> None:
        """Listener para AUTOMATION_EXECUTED — detecta 'describió en vez de ejecutar'."""
        import asyncio
        asyncio.create_task(self._verify_automation_async(event))

    async def _verify_automation_async(self, event: dict) -> None:
        response = event.get("response", "")
        if not response:
            return
        source = event.get("_source", "")

        describes = bool(_DESCRIBE_INSTEAD_RE.search(response))
        confirms = bool(_CONFIRMED_EXECUTION_RE.search(response))

        if describes and not confirms:
            evidence = "La respuesta describe una acción futura en vez de confirmar su ejecución."
        elif source == "cloud_fast_lane" and confirms:
            # Cloud fast-lane is LLM chat only — it CANNOT have executed anything.
            evidence = "El LLM afirmó haber ejecutado una acción, pero ningún módulo de automatización corrió en este turno."
        else:
            return  # no warning needed

        log.info("[verification] AUTOMATION WARN (%s) — %s", source, evidence)
        await self._notify_clients(
            skill_name="automation",
            verdict="WARN",
            rule_result={"ok": False, "evidence": evidence},
            llm_result=None,
        )
