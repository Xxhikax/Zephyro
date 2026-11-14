import asyncio
import threading
from typing import Any, Coroutine


def run_coro_in_new_thread(coro: Coroutine) -> Any:
    """Ejecuta una coroutine en un nuevo hilo usando asyncio.run.

    Útil como fallback cuando el hilo actual ya tiene un event loop
    en ejecución (por ejemplo bajo uvicorn/uvloop) y no se puede
    llamar a `asyncio.run` directamente.
    """
    result = {}
    exc = {}

    def target():
        try:
            result['value'] = asyncio.run(coro)
        except Exception as e:
            exc['e'] = e

    t = threading.Thread(target=target, daemon=False)
    t.start()
    t.join()

    if 'e' in exc:
        raise exc['e']
    return result.get('value')
