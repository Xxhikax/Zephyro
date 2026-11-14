import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

pytest_plugins = ('pytest_asyncio',)


@pytest.fixture
def mock_session():
    return {
        "role": "admin",
        "name": "test",
        "authenticated": True,
        "created_at": __import__("datetime").datetime.now(),
        "last_seen": __import__("datetime").datetime.now(),
    }


@pytest.fixture
def mock_orchestrator():
    orc = MagicMock()
    orc.handle_input = AsyncMock(return_value="mocked response")
    orc.handle_input_stream = AsyncMock(return_value=iter(["chunk1", "chunk2"]))
    orc.llm = MagicMock()
    orc.llm.get_status = MagicMock(return_value={
        "local_available": True,
        "cloud_available": False,
        "is_online": False,
        "provider": "none",
    })
    orc._last_interaction_id = 1
    return orc


@pytest.fixture
def app_client(mock_orchestrator, monkeypatch):
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    monkeypatch.setattr("core.orchestrator.Orchestrator", lambda **kw: mock_orchestrator)
    from web_app import app
    return TestClient(app)

# ── Reloj fijo para los tests de notificaciones ──────────────────────────────
# `DEFAULT_RULES` trae `quiet_from=23, quiet_to=8`. Los tests que no pasan
# `now=` explicito tomaban `time.time()` real, asi que entre las 23:00 y las
# 08:00 las reglas suprimian la entrega y 9 tests fallaban: el suite solo
# pasaba 15 horas por dia. Lo detecto el baseline a las 00:06 del 2026-10-02.
#
# Un test que falla segun la hora es peor que no tenerlo: entrena a ignorar el
# rojo. `evaluate()` y `emit()` ya aceptaban `now`; lo que faltaba era que el
# DEFAULT tambien fuera deterministico.
#
# NO es autouse: solo lo piden los archivos de notificaciones, para no
# congelar el reloj de tests ajenos.
@pytest.fixture
def frozen_notification_clock(monkeypatch):
    """Fija el reloj del NotificationCenter al mediodia local."""
    from datetime import datetime

    from core import notification_center as nc

    fixed = datetime.now(nc.LOCAL_TZ).replace(
        hour=12, minute=0, second=0, microsecond=0
    ).timestamp()

    class _Clock:
        @staticmethod
        def time():
            return fixed

    # Se parchea en `core.notification_center`, donde el nombre `time` esta
    # bindeado en call-time, no en el modulo `time` global.
    monkeypatch.setattr(nc, "time", _Clock)
    return fixed
