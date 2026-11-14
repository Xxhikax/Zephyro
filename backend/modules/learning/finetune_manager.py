"""
FineTuneManager — Gestiona ciclos de fine-tuning vía OpenAI API.

Flujo:
1. Monitorea si hay suficientes ejemplos nuevos (umbral configurable)
2. Exporta JSONL con TrainingDataCurator
3. Sube el archivo y lanza un job de fine-tuning en OpenAI
4. Monitorea el job en background
5. Cuando termina, registra el modelo fine-tuneado

El modelo fine-tuneado puede consultarse con get_active_model() e
integrarse en LLMManager como variante OpenAI.

Para Ollama local (futuro): genera Modelfile para cargar adaptador LoRA.
"""
import json
import os
import time
import threading
from pathlib import Path
from typing import Optional, Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from modules.learning.feedback_system   import FeedbackSystem
    from modules.learning.training_curator  import TrainingDataCurator

STATE_FILE = Path(__file__).parent.parent.parent / "storage" / "finetune_state.json"

_MIN_EXAMPLES   = 100   # Mínimo de ejemplos positivos para lanzar un job
_BASE_MODEL     = "gpt-4o-mini-2024-07-18"   # Modelo base para fine-tuning


class FineTuneManager:
    """
    Gestiona el ciclo completo de fine-tuning vía OpenAI.
    El modelo resultante se usa en LLMManager como proveedor OpenAI alternativo.
    """

    def __init__(self, openai_api_key: Optional[str] = None):
        self._api_key = openai_api_key or os.getenv("OPENAI_API_KEY", "")
        self._state: Dict[str, Any] = {
            "active_model":    None,   # ID del modelo fine-tuneado listo para usar
            "pending_job_id":  None,   # Job en curso
            "last_finetune_at": None,
            "total_cycles":    0,
        }
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._load_state()

    # ------------------------------------------------------------------
    # Estado
    # ------------------------------------------------------------------

    def _load_state(self):
        try:
            if STATE_FILE.exists():
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                self._state.update(data)
        except Exception as e:
            print(f"[FineTune] No se pudo cargar estado: {e}")

    def _save_state(self):
        try:
            STATE_FILE.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[FineTune] Error guardando estado: {e}")

    def get_active_model(self) -> Optional[str]:
        """
        Devuelve el ID del modelo fine-tuneado si está listo, o None.
        LLMManager puede usar esto como model= en la llamada a OpenAI.
        """
        return self._state.get("active_model")

    def get_status(self) -> Dict[str, Any]:
        return {
            "active_model":    self._state.get("active_model"),
            "pending_job_id":  self._state.get("pending_job_id"),
            "last_finetune_at": self._state.get("last_finetune_at"),
            "total_cycles":    self._state.get("total_cycles", 0),
            "openai_available": bool(self._api_key),
        }

    # ------------------------------------------------------------------
    # Lanzar fine-tuning
    # ------------------------------------------------------------------

    def maybe_finetune(
        self,
        feedback_system: "FeedbackSystem",
        curator: "TrainingDataCurator",
        threshold: int = _MIN_EXAMPLES,
    ) -> Optional[str]:
        """
        Lanza un job de fine-tuning si hay suficientes ejemplos nuevos.
        Devuelve job_id si se lanzó, None en caso contrario.

        Nota: no lanza si ya hay un job pendiente.
        """
        if not self._api_key:
            print("[FineTune] Sin API key de OpenAI — fine-tuning desactivado.")
            return None

        if self._state.get("pending_job_id"):
            print("[FineTune] Ya hay un job en curso, esperando.")
            return None

        available = curator.count_available(feedback_system)
        if available < threshold:
            print(f"[FineTune] {available}/{threshold} ejemplos — umbral no alcanzado.")
            return None

        # Exportar JSONL
        jsonl_path = curator.export_jsonl(feedback_system)
        if jsonl_path is None:
            return None

        # Lanzar job
        job_id = self._launch_job(jsonl_path)
        if job_id:
            self._state["pending_job_id"]   = job_id
            self._state["last_finetune_at"] = time.time()
            self._save_state()
            # Monitorear en background
            t = threading.Thread(target=self._monitor_job, args=(job_id,), daemon=True)
            t.start()
        return job_id

    def _launch_job(self, jsonl_path: Path) -> Optional[str]:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self._api_key)

            with open(jsonl_path, "rb") as f:
                file_resp = client.files.create(file=f, purpose="fine-tune")

            job = client.fine_tuning.jobs.create(
                training_file=file_resp.id,
                model=_BASE_MODEL,
                hyperparameters={"n_epochs": 3},
            )
            print(f"[FineTune] Job lanzado: {job.id} (modelo base: {_BASE_MODEL})")
            return job.id

        except ImportError:
            print("[FineTune] openai SDK no instalado.")
        except Exception as e:
            print(f"[FineTune] Error lanzando job: {e}")
        return None

    def _monitor_job(self, job_id: str):
        """Monitorea el job en background, actualizando el estado cuando termina."""
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self._api_key)

            while True:
                time.sleep(300)  # Verificar cada 5 minutos
                try:
                    job = client.fine_tuning.jobs.retrieve(job_id)
                except Exception:
                    continue

                if job.status == "succeeded":
                    self._state["active_model"]   = job.fine_tuned_model
                    self._state["pending_job_id"] = None
                    self._state["total_cycles"]   = self._state.get("total_cycles", 0) + 1
                    self._save_state()
                    print(f"[FineTune] Modelo listo: {job.fine_tuned_model}")
                    break

                if job.status in ("failed", "cancelled"):
                    self._state["pending_job_id"] = None
                    self._save_state()
                    print(f"[FineTune] Job {job_id} terminó con estado: {job.status}")
                    break

        except ImportError:
            pass
        except Exception as e:
            print(f"[FineTune] Error monitoreando job: {e}")

    # ------------------------------------------------------------------
    # Verificar job en curso al reiniciar
    # ------------------------------------------------------------------

    def resume_monitoring(self):
        """Retoma el monitoreo de un job pendiente al reiniciar el servidor."""
        job_id = self._state.get("pending_job_id")
        if job_id and self._api_key:
            print(f"[FineTune] Retomando monitoreo del job pendiente: {job_id}")
            t = threading.Thread(target=self._monitor_job, args=(job_id,), daemon=True)
            t.start()
