"""Chat router — send message, feedback, learning stats, WebSocket."""
import asyncio
import json
import time
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from core.auth_middleware import get_token_from_header, require_auth, require_admin
from core.connection_registry import registry as connection_registry
from core.request_context import RequestContext

router = APIRouter()


def get_orchestrator(request: Request):
    return request.app.state.orchestrator


async def _push_sentence_chunks(response: str, request_id: str, safe_send, next_index: int) -> int:
    """Synthesize a finished response sentence by sentence and push each as a tts_chunk
    (is_last=False). Synthesis of sentence N+1 runs while N is being sent/played.
    Returns the next free chunk_index; failed or empty sentences are skipped."""
    from routers.voice import synthesize_tts
    from modules.voice.tts_chunking import split_tts_sentences
    from core.response_sanitizer import prepare_for_tts

    # Sanitize the FULL response first — the Fuentes/URLs strip is DOTALL across the whole
    # text, so per sentence it can't remove the block. The visible transcript keeps the
    # sources via the `response` frame; only the spoken audio is cleaned.
    spoken = prepare_for_tts(response)
    sentences = [s.strip() for s in (split_tts_sentences(spoken) or [spoken])]
    sentences = [s for s in sentences if s]
    if not sentences:
        return next_index

    def _start(i: int):
        return asyncio.create_task(synthesize_tts(sentences[i], placeholder=False))

    upcoming = _start(0)
    try:
        for i, sentence in enumerate(sentences):
            current = upcoming
            upcoming = _start(i + 1) if i + 1 < len(sentences) else None
            try:
                tts = await current
            except asyncio.CancelledError:
                raise
            except Exception:
                continue
            if tts.get("success") and tts.get("audio"):
                await safe_send({
                    "type": "tts_chunk",
                    "request_id": request_id,
                    "chunk_index": next_index,
                    "audio": tts["audio"],
                    "text": sentence,
                    "is_last": False,
                })
                next_index += 1
    finally:
        if upcoming is not None and not upcoming.done():
            upcoming.cancel()
    return next_index


class ChatMessage(BaseModel):
    message: str
    mode: Optional[str] = None


class FeedbackRequest(BaseModel):
    interaction_id: int
    score: int
    feedback_type: Optional[str] = "explicit"


@router.post("/chat/send")
async def send_message(
    request: Request,
    message: ChatMessage,
    token: str = Depends(get_token_from_header),
    session: dict = Depends(require_auth),
):
    """Enviar mensaje al asistente"""
    orchestrator = get_orchestrator(request)

    force_mode = None
    if message.mode == "local":
        force_mode = "local"
    elif message.mode == "cloud":
        if session["role"] == "guest":
            return {
                "success": False,
                "error": "Modo cloud no disponible para invitados"
            }
        force_mode = "cloud"

    ctx = RequestContext(
        user_role=session["role"],
        user_name=session.get("name"),
        authenticated=session.get("authenticated", False),
        session_id=token,
        channel_type="websocket",
        force_mode=force_mode,
    )

    try:
        start_time = time.perf_counter()
        # handle_input reads the mode from its argument, not from ctx.force_mode.
        response = await orchestrator.handle_input(
            message.message, ctx=ctx, speak=False, force_mode=force_mode,
        )
        elapsed_time = time.perf_counter() - start_time

        return {
            "success": True,
            "response": response,
            "elapsed_time": round(elapsed_time, 2),
            "mode_used": force_mode or "auto",
            "interaction_id": orchestrator._last_interaction_id,
        }
    except Exception as e:
        error_msg = str(e)
        # Auto-capture de errores como bug reports (P6)
        if hasattr(orchestrator, "self_state"):
            try:
                orchestrator.self_state._create_bug_report(
                    bug_type="exception",
                    user_input=message.message,
                    response=error_msg,
                    intent="unknown",
                    details={"exception": error_msg, "error_type": type(e).__name__},
                )
            except Exception:
                pass
        return {
            "success": False,
            "error": error_msg
        }


@router.post("/feedback")
async def submit_feedback(
    request: Request,
    feedback: FeedbackRequest,
    session: dict = Depends(require_auth),
):
    """Registra feedback explícito sobre una respuesta."""
    orchestrator = get_orchestrator(request)
    fs = getattr(orchestrator, "feedback_system", None)
    if fs is None:
        return {"success": False, "message": "Feedback system no disponible"}
    if feedback.score not in (-1, 0, 1):
        raise HTTPException(status_code=400, detail="score debe ser -1, 0 o 1")
    ok = fs.submit_feedback(feedback.interaction_id, feedback.score, feedback.feedback_type or "explicit")

    if ok and feedback.score == 1:
        fss = getattr(orchestrator, "few_shot_selector", None)
        if fss:
            try:
                with fs._conn() as conn:
                    row = conn.execute(
                        "SELECT user_input, response, intent, importance_score FROM interactions WHERE id=?",
                        (feedback.interaction_id,),
                    ).fetchone()
                if row:
                    fss.add_example(
                        user_input=row["user_input"],
                        response=row["response"],
                        intent=row["intent"],
                        importance_score=row["importance_score"],
                    )
            except Exception:
                pass

    return {"success": ok, "message": "Feedback registrado" if ok else "Interacción no encontrada"}


@router.get("/learning/stats")
async def get_learning_stats(
    request: Request,
    session: dict = Depends(require_admin),
):
    """Estadísticas del sistema de aprendizaje adaptativo."""
    orchestrator = get_orchestrator(request)
    fs = getattr(orchestrator, "feedback_system", None)
    fss = getattr(orchestrator, "few_shot_selector", None)
    ft = getattr(orchestrator, "finetune_manager", None)
    re_ = getattr(orchestrator, "reflection_engine", None)
    return {
        "feedback": fs.get_stats() if fs else None,
        "few_shot_count": fss.count() if fss else None,
        "finetune": ft.get_status() if ft else None,
        "reflection_active": bool(re_ and re_.get_current_reflection()),
    }


# WS auth is inline PERMANENTLY — token arrives in JSON body, cannot use HTTP Depends() (auth-header-migration R1).
@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket para chat en tiempo real con soporte de cancelación (barge-in)."""
    await websocket.accept()
    inflight: dict[str, asyncio.Task] = {}
    send_lock = asyncio.Lock()
    registered_user_id: Optional[str] = None

    async def safe_send(payload: dict) -> None:
        async with send_lock:
            try:
                await websocket.send_json(payload)
            except Exception:
                pass

    async def ensure_registered(token: str) -> None:
        """Registra la WS en connection_registry la primera vez que llega un token válido."""
        nonlocal registered_user_id
        if registered_user_id is not None:
            return
        import web_app
        sess = web_app.sessions.get(token)
        if not sess:
            return
        user_id = str(sess.get("user_id") or sess.get("username") or token)
        await connection_registry.register(user_id, websocket)
        registered_user_id = user_id

    async def process_message(message_data: dict, request_id: str) -> None:
        token = message_data.get("token")
        message = message_data.get("message")
        mode = message_data.get("mode")

        import web_app
        sessions = web_app.sessions

        if token not in sessions:
            await safe_send({"type": "error", "request_id": request_id, "error": "Sesión no válida"})
            return

        session = sessions[token]
        sessions[token]["last_seen"] = datetime.now()
        orchestrator = websocket.app.state.orchestrator

        force_mode = None
        if mode == "local":
            force_mode = "local"
        elif mode == "cloud":
            if session["role"] == "guest":
                await safe_send({"type": "error", "request_id": request_id, "error": "Modo cloud no disponible para invitados"})
                return
            force_mode = "cloud"

        start_time = time.perf_counter()
        await safe_send({"type": "typing", "request_id": request_id, "message": "Zephyro está escribiendo..."})

        speak_flag = bool(message_data.get("speak", False))
        # Call mode: el frontend pide que el backend genere y EMPUJE el audio del
        # response (en vez del segundo round-trip frágil POST /api/voice/tts, que se
        # abortaba en silencio al cerrar el call). NO se forwardea como `speak` al
        # orchestrator (eso dispararía el TTS server-side → doble audio).
        tts_push = bool(message_data.get("tts", False))
        # Fase 1 streaming-tts-backend: opt-in del cliente para recibir tts_chunk frames
        # en vez del push único tts_audio. Sólo activo cuando tts_push también es True.
        stream_tts = bool(message_data.get("stream_tts", False))
        reasoning_mode = bool(message_data.get("reasoning", False))
        vision_context_payload = message_data.get("vision_context") or None

        async def status_cb(payload: dict) -> None:
            await safe_send({
                "type": "status",
                "request_id": request_id,
                "action": payload.get("action"),
                "message": payload.get("message"),
            })

        ctx = RequestContext(
            user_role=session["role"],
            user_name=session.get("name"),
            authenticated=session.get("authenticated", False),
            session_id=token,
            channel_type="websocket",
            force_mode=force_mode,
            reasoning_mode=reasoning_mode,
            request_id=request_id,
            status_callback=status_cb,
            vision_context=vision_context_payload,
            # ctx.speak = voice turn: the prompt and skills apply voice formatting + brevity
            # (the frontend sends tts/stream_tts but not `speak`). It does NOT play audio on
            # the host — that is the separate `speak` argument of handle_input below.
            speak=(speak_flag or tts_push or stream_tts),
        )

        # Fase 1 streaming-tts-backend: inyectar callback sólo cuando ambos
        # tts_push y stream_tts son True. El callback pushea tts_chunk frames
        # al WebSocket usando el mismo safe_send y request_id del turno activo.
        # _stream_chunks_sent tracks whether any chunks were actually emitted so we can
        # fall back to tts_audio when the orchestrator bypasses the streaming path
        # (e.g. tool-use loop for research queries — it skips _generate_with_streaming_tts_ws).
        _stream_chunks_sent = 0
        _next_chunk_index = 0
        if tts_push and stream_tts:
            async def _tts_chunk_cb(audio_b64: str, text: str, index: int, is_last: bool) -> None:
                nonlocal _stream_chunks_sent, _next_chunk_index
                _stream_chunks_sent += 1
                _next_chunk_index = index + 1
                await safe_send({
                    "type": "tts_chunk",
                    "request_id": request_id,
                    "chunk_index": index,
                    "audio": audio_b64,
                    "text": text,
                    "is_last": is_last,
                })
            ctx.stream_tts = True
            ctx.tts_chunk_callback = _tts_chunk_cb

        async def _await_tts_drain() -> None:
            drain = ctx.tts_drain
            if drain is None:
                return
            try:
                await drain
            except asyncio.CancelledError:
                drain.cancel()
                raise
            except Exception:
                import logging
                logging.getLogger(__name__).exception("tts drain failed")

        try:
            if message_data.get("stream", False):
                await safe_send({"type": "stream_start", "request_id": request_id})
                full_response = ""
                async for chunk in orchestrator.handle_input_stream(message, ctx=ctx):
                    full_response += chunk
                    await safe_send({"type": "chunk", "request_id": request_id, "content": chunk})
                elapsed = time.perf_counter() - start_time
                await safe_send({
                    "type": "stream_end",
                    "request_id": request_id,
                    "elapsed_time": round(elapsed, 2),
                })
                # handle_input_stream may fall back to handle_input, which can hand off
                # the tail of the streamed audio.
                if tts_push and stream_tts:
                    await _await_tts_drain()
            else:
                response = await asyncio.wait_for(
                    orchestrator.handle_input(
                        message, ctx=ctx,
                        # Host playback only when the client explicitly asked for it. Call
                        # mode gets its audio pushed over this socket: forwarding tts/stream_tts
                        # here played it a second time on the server's speakers and blocked the
                        # event loop (sync Fish Audio + sounddevice) until it finished.
                        speak=speak_flag,
                        force_mode=force_mode,
                    ),
                    timeout=120.0,
                )
                elapsed_time = time.perf_counter() - start_time
                # Extract real per-component timings populated by
                # _print_timing_stats. The HUD reads these instead of the
                # random-jitter fallback that used to live in call-mode.js.
                _t = getattr(ctx, "timings", None) or {}
                def _ms(key):
                    val = _t.get(key)
                    return round(val * 1000) if isinstance(val, (int, float)) else None
                await safe_send({
                    "type": "response",
                    "request_id": request_id,
                    "response": response,
                    "elapsed_time": round(elapsed_time, 2),
                    "mode_used": force_mode or "auto",
                    "llm_ms": _ms("llm"),
                    "router_ms": _ms("router"),
                    "context_build_ms": _ms("context_build"),
                    "total_ms": _ms("total"),
                })
                # Call mode: push audio after response text is sent.
                # stream_tts=True → tts_chunk frames + exactly ONE terminal frame (below).
                # stream_tts=False (legacy) → single tts_audio frame as before.
                if tts_push and stream_tts:
                    # Generation ended before all sentences were synthesized: the text already
                    # went out above; finish the audio. The terminal frame is sent by the
                    # outer finally, exactly once, whatever path the turn took.
                    await _await_tts_drain()
                    if response and _stream_chunks_sent == 0:
                        # The orchestrator bypassed the streaming path (fast cache, skills,
                        # tool loop with search): synthesize sentence by sentence.
                        _next_chunk_index = await _push_sentence_chunks(
                            response, request_id, safe_send, _next_chunk_index,
                        )
                elif tts_push and response:
                    from routers.voice import synthesize_tts
                    try:
                        _tts = await synthesize_tts(response)
                    except Exception as _tts_err:
                        _tts = {"success": False, "error": str(_tts_err)}
                    await safe_send({
                        "type": "tts_audio",
                        "request_id": request_id,
                        "success": bool(_tts.get("success")),
                        "audio": _tts.get("audio"),
                        "error": _tts.get("error"),
                    })
        except asyncio.CancelledError:
            await safe_send({"type": "cancelled", "request_id": request_id})
            raise
        except Exception as e:
            import logging, traceback as _tb
            logging.getLogger(__name__).error("WS handler exception:\n%s", _tb.format_exc())
            error_msg = str(e) or repr(e) or "Error interno"
            # Auto-capture de errores como bug reports (P6)
            if hasattr(orchestrator, "self_state"):
                try:
                    orchestrator.self_state._create_bug_report(
                        bug_type="websocket_exception",
                        user_input=message_data.get("message", ""),
                        response=error_msg,
                        intent="unknown",
                        details={"exception": error_msg, "error_type": type(e).__name__},
                    )
                except Exception:
                    pass
            await safe_send({"type": "error", "request_id": request_id, "error": error_msg})
        finally:
            # Barge-in / error / timeout: stop synthesizing the rest of the answer, not just
            # sending it. Then close the audio stream: the client must never wait on a
            # missing is_last (chunks always carry is_last=False).
            if ctx.tts_drain is not None and not ctx.tts_drain.done():
                ctx.tts_drain.cancel()
            if tts_push and stream_tts:
                await safe_send({
                    "type": "tts_chunk",
                    "request_id": request_id,
                    "chunk_index": _next_chunk_index,
                    "audio": None,
                    "text": "",
                    "is_last": True,
                })

    try:
        while True:
            data = await websocket.receive_text()
            try:
                message_data = json.loads(data)
            except json.JSONDecodeError:
                await safe_send({"type": "error", "error": "JSON inválido"})
                continue

            msg_type = message_data.get("type")

            if msg_type == "cancel":
                rid = message_data.get("request_id")
                task = inflight.get(rid) if rid else None
                if task and not task.done():
                    task.cancel()
                else:
                    await safe_send({"type": "cancel_ack", "request_id": rid, "found": False})
                continue

            # ── DiagnosticAgent model-choice bridge (Tauri) ─────────────────
            if msg_type == "diag:model_choice_response":
                token = message_data.get("token")
                if token:
                    await ensure_registered(token)
                rid = message_data.get("request_id")
                choice = message_data.get("choice", "")
                gate = getattr(websocket.app.state, "diagnostic_gate", None)
                if gate and rid and choice:
                    gate.resolve(rid, choice)
                await safe_send({"type": "diag:model_choice_confirmed", "request_id": rid, "choice": choice})
                continue

            # ── DiagnosticAgent approval bridge (Tauri) ──────────────────────
            if msg_type in ("diag:approval_response", "diag:cancel"):
                token = message_data.get("token")
                if token:
                    await ensure_registered(token)
                rid = message_data.get("request_id")
                action = message_data.get("action", "ignore")
                gate = getattr(websocket.app.state, "diagnostic_gate", None)
                if gate is None or not rid:
                    await safe_send({"type": "error", "error": "diag gate no disponible"})
                    continue
                if msg_type == "diag:cancel":
                    gate.cancel(rid)
                else:
                    gate.resolve(rid, action)
                await safe_send({"type": "diag:resolved", "request_id": rid, "action": action})
                continue

            # ── BrowserLogWatcher subscribe/unsubscribe ──────────────────────
            if msg_type in ("browser:subscribe", "browser:unsubscribe"):
                token = message_data.get("token")
                if token:
                    await ensure_registered(token)
                if registered_user_id is None:
                    await safe_send({"type": "error", "error": "registro WS requerido"})
                    continue
                from core.browser_log_watcher import watcher as browser_log_watcher
                if msg_type == "browser:subscribe":
                    browser_log_watcher.subscribe(registered_user_id)
                    await safe_send({"type": "browser:subscribed"})
                else:
                    browser_log_watcher.unsubscribe(registered_user_id)
                    await safe_send({"type": "browser:unsubscribed"})
                continue

            # ── TerminalController approval bridge (Tauri) ───────────────────
            if msg_type in ("terminal:approval_response", "terminal:cancel"):
                token = message_data.get("token")
                if token:
                    await ensure_registered(token)
                rid = message_data.get("request_id")
                action = message_data.get("action", "ignore")
                gate = getattr(websocket.app.state, "terminal_gate", None)
                if gate is None or not rid:
                    await safe_send({"type": "error", "error": "terminal gate no disponible"})
                    continue
                if msg_type == "terminal:cancel":
                    gate.cancel(rid)
                else:
                    gate.resolve(rid, action)
                await safe_send({"type": "terminal:resolved", "request_id": rid, "action": action})
                continue

            # Registrar en connection_registry si el mensaje trae token válido
            tok = message_data.get("token")
            if tok:
                await ensure_registered(tok)

            request_id = message_data.get("request_id") or uuid.uuid4().hex
            await safe_send({"type": "ack", "request_id": request_id})

            task = asyncio.create_task(process_message(message_data, request_id))
            inflight[request_id] = task

            def _cleanup(_t: asyncio.Task, _rid: str = request_id) -> None:
                inflight.pop(_rid, None)

            task.add_done_callback(_cleanup)

    except WebSocketDisconnect:
        for t in list(inflight.values()):
            if not t.done():
                t.cancel()
    finally:
        if registered_user_id is not None:
            try:
                await connection_registry.unregister(registered_user_id)
            except Exception:
                pass
            try:
                from core.browser_log_watcher import watcher as browser_log_watcher
                browser_log_watcher.unsubscribe(registered_user_id)
            except Exception:
                pass
