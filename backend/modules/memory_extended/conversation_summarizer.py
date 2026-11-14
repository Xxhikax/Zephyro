"""
ConversationSummarizer: Resumidor automático de conversaciones largas.

Cada N mensajes genera un resumen comprimido de la conversación
para mantener el contexto sin saturar el contexto del LLM.
Usa el propio LLM local para resumir (sin costo adicional).
"""
import time
import threading
import os
import json
from typing import List, Dict, Optional, Callable, Any


class ConversationSummarizer:
    """
    Resumidor automático de conversaciones.

    Estrategia:
    - Se activa automáticamente cada `summarize_every` mensajes
    - Genera un resumen comprimido del diálogo anterior
    - El resumen se inyecta en el contexto del LLM como memoria
    - Los mensajes originales pueden archivarse o descartarse
    """

    def __init__(
        self,
        summarize_every: int = 20,
        max_summary_tokens: int = 400,
        llm_callback: Optional[Callable[[str], str]] = None,
    ):
        """
        Args:
            summarize_every: Número de mensajes entre resúmenes automáticos.
            max_summary_tokens: Longitud máxima aproximada del resumen (en palabras).
            llm_callback: Función que recibe un prompt y devuelve un resumen.
                          Si es None, usa resumen heurístico (sin LLM).
        """
        self.summarize_every = summarize_every
        self.max_summary_tokens = max_summary_tokens
        self.llm_callback = llm_callback

        self._summaries: List[Dict] = []  # Resúmenes generados
        self._message_count: int = 0       # Mensajes desde el último resumen
        self._lock = threading.Lock()

        # Ruta de persistencia
        storage_dir = os.path.join(
            os.path.dirname(__file__), "../../../storage"
        )
        os.makedirs(storage_dir, exist_ok=True)
        self._summaries_path = os.path.join(storage_dir, "conversation_summaries.json")
        self._load()

        print(
            f"[Summarizer] Activo. Resumen cada {self.summarize_every} mensajes. "
            f"{len(self._summaries)} resúmenes cargados."
        )

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    def _load(self):
        if os.path.exists(self._summaries_path):
            try:
                with open(self._summaries_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._summaries = data.get("summaries", [])
                    self._message_count = data.get("message_count", 0)
            except Exception as e:
                print(f"[Summarizer] Error cargando resúmenes: {e}")

    def _save(self):
        try:
            tmp = self._summaries_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "summaries": self._summaries,
                        "message_count": self._message_count,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            os.replace(tmp, self._summaries_path)
        except Exception as e:
            print(f"[Summarizer] Error guardando resúmenes: {e}")

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def on_message(self, messages: List[Dict]) -> Optional[str]:
        """
        Llamar cada vez que se añade un mensaje al historial.

        Args:
            messages: Lista completa de mensajes hasta ahora.

        Returns:
            El resumen generado si se disparó, o None.
        """
        with self._lock:
            self._message_count += 1
            if self._message_count >= self.summarize_every:
                summary = self._generate_summary(messages)
                if summary:
                    self._summaries.append({
                        "summary": summary,
                        "timestamp": time.time(),
                        "messages_covered": len(messages),
                    })
                    self._message_count = 0
                    self._save()
                    return summary
        return None

    def get_context_for_llm(self, max_summaries: int = 3) -> str:
        """
        Devuelve los últimos resúmenes formateados para incluir en el contexto del LLM.
        """
        with self._lock:
            recent = self._summaries[-max_summaries:]

        if not recent:
            return ""

        parts = ["## Resumen de conversaciones anteriores:"]
        for item in recent:
            ts = time.strftime("%d/%m %H:%M", time.localtime(item["timestamp"]))
            parts.append(f"[{ts}] {item['summary']}")

        return "\n".join(parts)

    def get_all_summaries(self) -> List[Dict]:
        """Devuelve todos los resúmenes guardados."""
        with self._lock:
            return list(self._summaries)

    def reset_count(self):
        """Reinicia el contador de mensajes (útil al inicio de sesión)."""
        with self._lock:
            self._message_count = 0

    def clear_summaries(self):
        """Borra todos los resúmenes."""
        with self._lock:
            self._summaries = []
            self._message_count = 0
            self._save()

    # ------------------------------------------------------------------
    # Generación de resúmenes
    # ------------------------------------------------------------------

    def _generate_summary(self, messages: List[Dict]) -> str:
        """Genera un resumen de los mensajes proporcionados."""
        if not messages:
            return ""

        # Tomar los últimos N mensajes para resumir
        window = messages[-self.summarize_every:]

        if self.llm_callback:
            return self._summarize_with_llm(window)
        else:
            return self._summarize_heuristic(window)

    def _summarize_with_llm(self, messages: List[Dict]) -> str:
        """Usa el LLM para generar un resumen."""
        try:
            # Formatear los mensajes como texto
            dialogue = []
            for msg in messages:
                role = msg.get("role", "?")
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    prefix = "Usuario" if role == "user" else "Zephyro"
                    dialogue.append(f"{prefix}: {content[:300]}")

            if not dialogue:
                return ""

            dialogue_text = "\n".join(dialogue)
            prompt = (
                f"Resume esta conversación en máximo {self.max_summary_tokens} palabras, "
                f"destacando los temas principales, decisiones y datos importantes:\n\n"
                f"{dialogue_text}\n\n"
                f"Resumen:"
            )

            summary = self.llm_callback(prompt)
            if summary:
                # Limpiar y truncar
                summary = summary.strip()
                words = summary.split()
                if len(words) > self.max_summary_tokens:
                    summary = " ".join(words[:self.max_summary_tokens]) + "..."
                return summary
        except Exception as e:
            print(f"[Summarizer] Error en LLM: {e}")
            return self._summarize_heuristic(messages)

        return ""

    def _summarize_heuristic(self, messages: List[Dict]) -> str:
        """
        Resumen heurístico sin LLM: extrae las líneas más informativas.
        Útil como fallback cuando el LLM no está disponible.
        """
        key_lines = []
        seen = set()

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if not isinstance(content, str):
                continue

            # Solo tomar frases sustanciales (>30 chars, no duplicadas)
            for line in content.split("\n"):
                line = line.strip()
                if (
                    len(line) > 30
                    and line not in seen
                    and not line.startswith("#")
                ):
                    seen.add(line)
                    prefix = "Usuario" if role == "user" else "Zephyro"
                    key_lines.append(f"{prefix}: {line[:200]}")
                    if len(key_lines) >= 8:
                        break
            if len(key_lines) >= 8:
                break

        if not key_lines:
            return ""

        return "Conversación anterior: " + " | ".join(key_lines[:5])

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, Any]:
        """Devuelve estadísticas del resumidor."""
        with self._lock:
            return {
                "total_summaries": len(self._summaries),
                "messages_since_last_summary": self._message_count,
                "summarize_every": self.summarize_every,
                "llm_enabled": self.llm_callback is not None,
                "last_summary_time": (
                    self._summaries[-1]["timestamp"] if self._summaries else None
                ),
            }
