"""
Tests para el path WS streaming-TTS (Fase 1 — backend only).

Verifica el método `_generate_with_streaming_tts_ws` del Orchestrator
con LLM y TTS completamente mockeados (sin red, sin Piper, sin Fish).

Convenciones:
- Todos los patches van sobre el módulo donde el nombre se resuelve en
  call-time (ver tests-safety.md: "contamination de globals").
- Nunca se instancian BrowserConfig / AppController / LLMManager real /
  Piper real / Fish real.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── Fixtures / helpers ────────────────────────────────────────────────────────

TOKENS_3_SENTENCES = ["Hola mundo. ", "¿Cómo estás? ", "Bien gracias."]
TOKENS_SHORT = ["Corto."]


def _make_fake_llm(tokens: list[str]):
    """Crea un fake LLM cuyo generate_stream devuelve los tokens dados."""
    llm = MagicMock()
    llm.generate_stream.return_value = iter(tokens)
    return llm


def _make_orchestrator_stub():
    """
    Construye un stub mínimo de Orchestrator para poder llamar
    `_generate_with_streaming_tts_ws` directamente sin instanciar nada real.
    """
    # Importamos la clase para que la inyección de método funcione,
    # pero NO llamamos __init__ (que levantaría LLMManager real, etc.).
    from core.orchestrator import Orchestrator
    stub = object.__new__(Orchestrator)
    # Bind el método de instancia al stub
    stub._generate_with_streaming_tts_ws = (
        Orchestrator._generate_with_streaming_tts_ws.__get__(stub, Orchestrator)
    )
    return stub


# ── Task 4.2 — ordered frames con chunk_index monotónico e is_last ───────────

@pytest.mark.asyncio
async def test_ordered_chunks_and_is_last():
    """
    Con 3 oraciones el on_chunk debe llamarse 3 veces, con chunk_index
    0, 1, 2 en orden y is_last=False siempre: el frame terminal lo emite
    routers/chat.py una sola vez por turno.
    """
    call_counter = [0]
    calls: list[tuple] = []

    async def _on_chunk(audio_b64, text, idx, is_last):
        calls.append((audio_b64, text, idx, is_last))

    stub = _make_orchestrator_stub()
    fake_llm = _make_fake_llm(TOKENS_3_SENTENCES)

    async def _synth_ordered(text, **_kw):
        n = call_counter[0]
        call_counter[0] += 1
        return {"success": True, "audio": f"b64-{n}"}

    with patch("core.orchestrator.synthesize_tts", side_effect=_synth_ordered):
        result = await stub._generate_with_streaming_tts_ws(
            llm=fake_llm,
            prompt="hola",
            force_local=False,
            force_cloud=False,
            preset="balanceado",
            on_chunk=_on_chunk,
        )

    assert len(calls) == 3, f"Esperaba 3 chunks, recibí {len(calls)}"
    indices = [c[2] for c in calls]
    assert indices == [0, 1, 2], f"chunk_index no es monotónico: {indices}"
    assert all(c[3] is False for c in calls), "El orchestrator nunca marca is_last"
    assert [c[0] for c in calls] == ["b64-0", "b64-1", "b64-2"], "Audio fuera de orden"
    assert result  # devuelve el texto acumulado


# ── Task 4.3 — opt-in OFF no emite tts_chunk ─────────────────────────────────

@pytest.mark.asyncio
async def test_opt_in_off_emits_no_chunks():
    """
    Cuando tts_chunk_callback es None (stream_tts=False), el predicate
    _ws_stream es False y _generate_with_streaming_tts_ws NO se llama.
    Verificamos que el predicate se evalúa correctamente.
    """
    from core.request_context import RequestContext

    ctx = RequestContext(
        user_role="user",
        user_name=None,
        authenticated=True,
        session_id="test-session",
    )
    # stream_tts=False por defecto → tts_chunk_callback = None
    assert ctx.stream_tts is False
    assert ctx.tts_chunk_callback is None

    # El predicate _ws_stream depende de ambos → debe ser False
    ws_stream = bool(
        getattr(ctx, "stream_tts", False)
        and getattr(ctx, "tts_chunk_callback", None) is not None
    )
    assert ws_stream is False, "_ws_stream debe ser False cuando stream_tts=False"


# ── Task 4.5 — cancellation ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancellation_stops_chunks():
    """
    Si on_chunk lanza CancelledError en la primera llamada, el loop se
    detiene y CancelledError se propaga.
    """
    call_counter = [0]
    calls_made: list[int] = []

    async def _on_chunk_cancel(audio_b64, text, idx, is_last):
        calls_made.append(idx)
        raise asyncio.CancelledError()

    stub = _make_orchestrator_stub()
    fake_llm = _make_fake_llm(TOKENS_3_SENTENCES)

    synth_counter = [0]

    async def _synth(t, **_kw):
        n = synth_counter[0]
        synth_counter[0] += 1
        return {"success": True, "audio": f"b64-{n}"}

    with patch("core.orchestrator.synthesize_tts", side_effect=_synth):
        with pytest.raises(asyncio.CancelledError):
            await stub._generate_with_streaming_tts_ws(
                llm=fake_llm,
                prompt="prompt largo " * 5,
                force_local=False,
                force_cloud=False,
                preset="balanceado",
                on_chunk=_on_chunk_cancel,
            )

    # on_chunk sólo se llamó 1 vez antes del CancelledError
    assert len(calls_made) == 1, f"Esperaba 1 llamada antes de cancel, hubo {len(calls_made)}"


# ── Task 4.6 — per-sentence error isolation ──────────────────────────────────

@pytest.mark.asyncio
async def test_per_sentence_error_isolation():
    """
    Si synthesize_tts falla en la oración índice 1 (segunda), los chunks
    de las oraciones 0 y 2 se pushean con indices monotónicos (0, 1) y la
    excepción NO se propaga (el turno sigue abierto).
    """
    calls: list[tuple] = []

    async def _on_chunk(audio_b64, text, idx, is_last):
        calls.append((audio_b64, text, idx, is_last))

    stub = _make_orchestrator_stub()
    # 3 oraciones en los tokens
    fake_llm = _make_fake_llm(TOKENS_3_SENTENCES)

    sentence_call = [0]

    async def _synth_fails_on_1(text, **_kw):
        n = sentence_call[0]
        sentence_call[0] += 1
        if n == 1:
            raise RuntimeError("TTS error simulado en oración 1")
        return {"success": True, "audio": f"b64-{n}"}

    with patch("core.orchestrator.synthesize_tts", side_effect=_synth_fails_on_1):
        result = await stub._generate_with_streaming_tts_ws(
            llm=fake_llm,
            prompt="hola",
            force_local=False,
            force_cloud=False,
            preset="balanceado",
            on_chunk=_on_chunk,
        )

    # Sólo oraciones 0 y 2 emitidas → 2 chunks, indices 0 y 1
    assert len(calls) == 2, f"Esperaba 2 chunks (oración 1 saltada), hubo {len(calls)}"
    emitted_indices = [c[2] for c in calls]
    assert emitted_indices == [0, 1], f"Indices no monotónicos: {emitted_indices}"
    # No se propagó excepción (el método retornó)
    assert result is not None


# ── Task 4.7 — sanitización aplicada ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_sanitization_applied():
    """
    Los tokens con emoji se limpian via sanitize antes de llegar a TTS.
    El texto en on_chunk no debe contener el emoji original.
    """
    calls: list[tuple] = []

    async def _on_chunk(audio_b64, text, idx, is_last):
        calls.append((audio_b64, text, idx, is_last))

    stub = _make_orchestrator_stub()
    # Oración larga para superar el min-length via tokens acumulados
    tokens_with_emoji = ["Hola ", "mundo. ", "¿Cómo", " estás? ", "Bien gracias."]
    tokens_with_emoji[1] = "🎉 mundo. "  # insertar emoji

    fake_llm = _make_fake_llm(tokens_with_emoji)
    synth_counter = [0]

    async def _synth(t, **_kw):
        n = synth_counter[0]
        synth_counter[0] += 1
        return {"success": True, "audio": f"b64-{n}"}

    with patch("core.orchestrator.synthesize_tts", side_effect=_synth):
        await stub._generate_with_streaming_tts_ws(
            llm=fake_llm,
            prompt="hola",
            force_local=False,
            force_cloud=False,
            preset="balanceado",
            on_chunk=_on_chunk,
        )

    # Al menos una llamada emitida
    assert len(calls) >= 1, "No se emitió ningún chunk"
    # Ningún texto en los chunks debe contener el emoji 🎉
    for audio_b64, text, idx, is_last in calls:
        assert "🎉" not in text, f"Emoji no sanitizado en chunk {idx}: '{text}'"


# ── Latency audit 2026-10 — regressions ──────────────────────────────────────

import threading
import time


@pytest.mark.asyncio
async def test_first_sentence_emitted_before_generation_ends():
    """No hold-one: the first sentence must be spoken while the LLM is still generating.
    Before, sentence 1 waited for sentence 2 so it could be marked is_last."""
    release = threading.Event()
    emitted_before_release: list[int] = []

    def _tokens():
        yield "Primera oración completa. "
        release.wait(timeout=5)
        yield "Segunda oración."

    llm = MagicMock()
    llm.generate_stream.return_value = _tokens()
    calls: list[tuple] = []

    async def _on_chunk(audio_b64, text, idx, is_last):
        calls.append((text, idx))
        if not release.is_set():
            emitted_before_release.append(idx)
            release.set()

    async def _synth(text, **_kw):
        return {"success": True, "audio": "b64"}

    stub = _make_orchestrator_stub()
    with patch("core.orchestrator.synthesize_tts", side_effect=_synth):
        await stub._generate_with_streaming_tts_ws(
            llm=llm, prompt="p", force_local=False, force_cloud=False,
            preset="rapido", on_chunk=_on_chunk,
        )
    assert emitted_before_release == [0], "first chunk must not wait for the second sentence"
    assert [c[1] for c in calls] == [0, 1]


@pytest.mark.asyncio
async def test_concurrent_synthesis_keeps_order():
    """Synthesis runs 2-in-flight; a fast later sentence must not overtake a slow earlier one."""
    llm = _make_fake_llm(["Uno lento aquí. ", "Dos rápido aquí. ", "Tres rápido aquí."])
    delays = {"Uno lento aquí.": 0.15, "Dos rápido aquí.": 0.0, "Tres rápido aquí.": 0.0}
    in_flight = [0]
    max_in_flight = [0]

    async def _synth(text, **_kw):
        in_flight[0] += 1
        max_in_flight[0] = max(max_in_flight[0], in_flight[0])
        await asyncio.sleep(delays.get(text, 0))
        in_flight[0] -= 1
        return {"success": True, "audio": text}

    calls: list[str] = []

    async def _on_chunk(audio_b64, text, idx, is_last):
        calls.append(audio_b64)

    stub = _make_orchestrator_stub()
    with patch("core.orchestrator.synthesize_tts", side_effect=_synth):
        await stub._generate_with_streaming_tts_ws(
            llm=llm, prompt="p", force_local=False, force_cloud=False,
            preset="rapido", on_chunk=_on_chunk,
        )
    assert calls == ["Uno lento aquí.", "Dos rápido aquí.", "Tres rápido aquí."]
    assert max_in_flight[0] == 2, f"expected 2 syntheses in flight, got {max_in_flight[0]}"


@pytest.mark.asyncio
async def test_cancel_stops_token_consumption():
    """Cancelling the turn must stop the pump thread from draining the LLM stream."""
    consumed = [0]

    def _endless():
        while True:
            consumed[0] += 1
            time.sleep(0.005)
            yield "palabra otra cosa más acá. "

    llm = MagicMock()
    llm.generate_stream.return_value = _endless()

    async def _synth(text, **_kw):
        return {"success": True, "audio": "b64"}

    async def _on_chunk(*_a):
        return None

    stub = _make_orchestrator_stub()
    with patch("core.orchestrator.synthesize_tts", side_effect=_synth):
        task = asyncio.create_task(stub._generate_with_streaming_tts_ws(
            llm=llm, prompt="p", force_local=False, force_cloud=False,
            preset="rapido", on_chunk=_on_chunk,
        ))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    await asyncio.sleep(0.05)
    after_cancel = consumed[0]
    await asyncio.sleep(0.1)
    assert consumed[0] - after_cancel <= 1, "pump kept consuming tokens after cancel"


@pytest.mark.asyncio
async def test_force_cloud_token_streams_instead_of_blocking_generate():
    """force_cloud used to skip token streaming (blocking generate + per-sentence TTS)."""
    llm = _make_fake_llm(["Hola desde la nube. ", "Segunda."])

    async def _synth(text, **_kw):
        return {"success": True, "audio": "b64"}

    async def _on_chunk(*_a):
        return None

    stub = _make_orchestrator_stub()
    with patch("core.orchestrator.synthesize_tts", side_effect=_synth):
        await stub._generate_with_streaming_tts_ws(
            llm=llm, prompt="p", force_local=False, force_cloud=True,
            preset="rapido", on_chunk=_on_chunk,
        )
    llm.generate_stream.assert_called_once()
    assert llm.generate_stream.call_args.kwargs["force_cloud"] is True
    llm.generate.assert_not_called()


@pytest.mark.asyncio
async def test_router_fallback_pushes_ordered_chunks_without_terminal():
    """routers/chat.py fallback: ordered chunks, is_last=False, failed sentences skipped,
    returns the next free index (the caller sends the single terminal frame)."""
    from routers import chat as chat_router

    sent: list[dict] = []

    async def _send(frame):
        sent.append(frame)

    async def _synth(text, **kw):
        assert kw.get("placeholder") is False
        if text.startswith("Falla"):
            return {"success": False, "error": "x"}
        await asyncio.sleep(0.05 if text.startswith("Uno") else 0)
        return {"success": True, "audio": text}

    with patch("routers.voice.synthesize_tts", side_effect=_synth):
        nxt = await chat_router._push_sentence_chunks(
            "Uno primero. Falla esta. Tres final.", "rid", _send, 0,
        )
    assert [f["audio"] for f in sent] == ["Uno primero.", "Tres final."]
    assert [f["chunk_index"] for f in sent] == [0, 1]
    assert all(f["is_last"] is False for f in sent)
    assert nxt == 2


def test_chunk_cleanup_never_speaks_listo_placeholder():
    from core.response_sanitizer import prepare_for_tts
    assert prepare_for_tts("https://example.com/a", placeholder=False) == ""
    assert prepare_for_tts("https://example.com/a") == "Listo."  # full-response default kept


def test_stream_cleaner_suppresses_sources_section():
    from core.response_sanitizer import make_stream_tts_cleaner
    clean = make_stream_tts_cleaner()
    assert clean("El dólar cerró en alza.") == "El dólar cerró en alza."
    assert clean("**Fuentes:** Reuters, Infobae.") == ""
    assert clean("Ámbito Financiero, nota del día.") == "", "sources section must stay suppressed"


def test_generate_stream_does_not_restart_after_partial_cloud_output():
    """A cloud error mid-stream must truncate, not append a fresh local answer."""
    from core.llm_manager import LLMManager

    mgr = object.__new__(LLMManager)
    mgr.cloud_provider = "deepseek"
    mgr.ollama_available = True
    mgr._active_cloud_model_name = lambda: "deepseek-flash"

    def _stream(_prompt):
        yield MagicMock(content="Parte uno. ")
        raise RuntimeError("connection reset")

    mgr.cloud_model = MagicMock()
    mgr.cloud_model.stream.side_effect = _stream
    mgr._stream_ollama = MagicMock(side_effect=AssertionError("must not restart locally"))
    mgr.generate = MagicMock(side_effect=AssertionError("must not restart locally"))

    assert list(mgr.generate_stream("p", force_cloud=True)) == ["Parte uno. "]


@pytest.mark.asyncio
async def test_text_returns_before_audio_drain_with_ctx():
    """With ctx, the text returns when GENERATION ends; remaining syntheses finish in
    ctx.tts_drain (the WS handler sends `response` first, then awaits the drain)."""
    from core.request_context import RequestContext

    ctx = RequestContext(user_role="admin", user_name=None, authenticated=True, session_id="s")
    llm = _make_fake_llm(["Uno aquí. ", "Dos aquí. ", "Tres aquí."])
    gate = asyncio.Event()

    async def _synth(text, **_kw):
        if text != "Uno aquí.":
            await gate.wait()
        return {"success": True, "audio": text}

    calls: list[str] = []

    async def _on_chunk(audio_b64, text, idx, is_last):
        calls.append(audio_b64)

    stub = _make_orchestrator_stub()
    with patch("core.orchestrator.synthesize_tts", side_effect=_synth):
        text = await asyncio.wait_for(stub._generate_with_streaming_tts_ws(
            llm=llm, prompt="p", force_local=False, force_cloud=False,
            preset="rapido", on_chunk=_on_chunk, ctx=ctx,
        ), timeout=2)
        assert "Tres aquí." in text
        assert ctx.tts_drain is not None and not ctx.tts_drain.done()
        gate.set()
        await asyncio.wait_for(ctx.tts_drain, timeout=2)
    assert calls == ["Uno aquí.", "Dos aquí.", "Tres aquí."]


def test_stream_cleaner_sources_heading_inside_code_fence_is_code():
    from core.response_sanitizer import make_stream_tts_cleaner
    clean = make_stream_tts_cleaner()
    assert clean("```python") == ""
    assert clean("Sources: list = []") == ""
    assert clean("```") == ""
    assert clean("Después del código sigue la explicación.") != "", "fence must not arm in_sources"
