"""
TrainingDataCurator — Convierte interacciones con feedback positivo al
formato JSONL de OpenAI para fine-tuning.

Criterios de inclusión:
- feedback_score >= 1 (positivo explícito o implícito)
- importance_score >= 0.5
- Longitud mínima de respuesta (no respuestas triviales)
- Sin duplicados semánticos cercanos (título distance simple)
"""
import json
import os
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from modules.learning.feedback_system import FeedbackSystem

TRAINING_DIR = Path(__file__).parent.parent.parent / "storage" / "training_data"

_DEFAULT_SYSTEM = (
    "Eres Zephyro, un asistente de IA personal de Bruno. "
    "Formal y eficiente: ve al grano, sin rodeos. "
    "PROHIBIDO usar emojis. Responde siempre en español salvo que se te pida inglés."
)


class TrainingDataCurator:
    """
    Genera datasets de entrenamiento curados a partir del feedback acumulado.
    Exporta JSONL en el formato messages[] de OpenAI fine-tuning.
    """

    MIN_RESPONSE_LEN    = 50      # Caracteres mínimos en la respuesta
    MIN_IMPORTANCE      = 0.5     # Score mínimo de importancia
    MIN_DEDUP_DISTANCE  = 20      # Prefijos distintos de esta longitud (dedup simple)

    def __init__(self):
        TRAINING_DIR.mkdir(parents=True, exist_ok=True)

    def curate(
        self,
        feedback_system: "FeedbackSystem",
        system_prompt: str = _DEFAULT_SYSTEM,
        only_new: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Lee interacciones buenas del FeedbackSystem y filtra las aptas para entrenamiento.
        Devuelve lista de dicts en formato OpenAI messages[].
        """
        raw = feedback_system.get_good_interactions(
            min_score=1,
            not_exported=only_new,
            limit=1000,
        )

        seen_prefixes: set = set()
        examples = []

        for item in raw:
            user_input = item["user_input"].strip()
            response   = item["response"].strip()

            # Filtros de calidad
            if len(response) < self.MIN_RESPONSE_LEN:
                continue
            if item.get("importance_score", 0) < self.MIN_IMPORTANCE:
                continue

            # Deduplicación simple por prefijo
            prefix = user_input[:self.MIN_DEDUP_DISTANCE].lower()
            if prefix in seen_prefixes:
                continue
            seen_prefixes.add(prefix)

            examples.append({
                "_id": item["id"],
                "messages": [
                    {"role": "system",    "content": system_prompt},
                    {"role": "user",      "content": user_input},
                    {"role": "assistant", "content": response},
                ],
            })

        return examples

    def export_jsonl(
        self,
        feedback_system: "FeedbackSystem",
        system_prompt: str = _DEFAULT_SYSTEM,
        only_new: bool = True,
    ) -> Optional[Path]:
        """
        Exporta el dataset curado a un archivo JSONL.
        Devuelve la ruta del archivo, o None si no hay datos suficientes.
        """
        examples = self.curate(feedback_system, system_prompt, only_new)
        if len(examples) < 10:
            print(f"[Curator] Solo {len(examples)} ejemplos — mínimo 10 para exportar.")
            return None

        ts = int(time.time())
        out_path = TRAINING_DIR / f"zephyro_finetune_{ts}.jsonl"

        with open(out_path, "w", encoding="utf-8") as f:
            for ex in examples:
                record = {"messages": ex["messages"]}
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        exported_ids = [ex["_id"] for ex in examples]
        feedback_system.mark_exported(exported_ids)

        print(f"[Curator] Exportados {len(examples)} ejemplos → {out_path}")
        return out_path

    def count_available(self, feedback_system: "FeedbackSystem") -> int:
        """Cuántos ejemplos positivos y no-exportados hay disponibles."""
        stats = feedback_system.get_stats()
        return stats.get("pending_training", 0)
