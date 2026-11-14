/**
 * diagnostic.js — Zephyro DiagnosticAgent client (Tauri dashboard bridge).
 *
 * Responsabilidades:
 *   - Botón flotante "Diagnosticar" en el HUD.
 *   - Modal con progress log + card de aprobación + chunks del fix_prompt.
 *   - Routing de mensajes WS `diag:*` (llamado por websocket.js).
 *   - Resume automático al reconectar (GET /self/diagnose/pending).
 *
 * Contrato WS (ver backend/subagents/ws_approval_gate.py):
 *   backend → frontend: diag:progress, diag:approval, diag:chunk, diag:resolved, diag:expired
 *   frontend → backend: diag:approval_response { action: view|ignore|more_context }, diag:cancel
 */
(function () {
    'use strict';

    const API_BASE = (typeof window.API_BASE === 'string' && window.API_BASE) || '';

    const state = {
        currentRequestId: null,
        status: 'idle',           // idle | running | awaiting_approval | resolved | error
        progressLog: [],          // [{ts, text}]
        hypothesisSummary: '',
        chunks: [],
        userContext: '',          // solicitud del usuario que desencadenó el diagnóstico
        rendered: false,
        lastNotifiedApprovalId: null,  // dedup de notificaciones diag:approval por request_id
    };

    // ── DOM helpers ──────────────────────────────────────────────────────────
    function $(id) { return document.getElementById(id); }
    function esc(s) {
        const d = document.createElement('span');
        d.textContent = String(s);
        return d.innerHTML;
    }
    function nowHMS() {
        const d = new Date();
        const pad = (n) => String(n).padStart(2, '0');
        return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    }

    // ── FAB button ───────────────────────────────────────────────────────────
    function ensureFab() {
        const btn = $('diag-fab');
        if (!btn) return btn;
        if (!btn._diagWired) {
            btn.addEventListener('click', () => {
                // Toggle: si la modal está abierta, cerrarla
                if ($('diag-modal-backdrop')) {
                    closeModal();
                    return;
                }
                // Si está resuelto o con error, resetear estado para que el modal muestre idle
                if (state.status === 'resolved' || state.status === 'error') {
                    state.currentRequestId = null;
                    state.status = 'idle';
                    state.progressLog = [];
                    state.chunks = [];
                    state.hypothesisSummary = '';
                    state.userContext = '';
                    updateFabBadge();
                }
                // Siempre abrir el modal; el usuario decide si iniciar diagnóstico desde adentro
                openModal();
            });
            btn._diagWired = true;
        }
        return btn;
    }
    function updateFabBadge() {
        const btn = $('diag-fab');
        if (!btn) return;
        btn.classList.toggle('has-pending',
            state.status === 'awaiting_approval' || state.status === 'running');
    }

    // ── Modal ────────────────────────────────────────────────────────────────
    function openModal() {
        if ($('diag-modal-backdrop')) return;
        const backdrop = document.createElement('div');
        backdrop.className = 'diag-modal-backdrop';
        backdrop.id = 'diag-modal-backdrop';
        backdrop.innerHTML = `
            <div class="diag-modal" role="dialog" aria-labelledby="diag-modal-title">
                <div class="diag-modal-head">
                    <span id="diag-modal-title">DIAGNÓSTICO
                        <span id="diag-status-pill" class="diag-status-pill running">${esc(state.status)}</span>
                    </span>
                    <button class="diag-modal-close" id="diag-modal-close">CERRAR</button>
                </div>
                <div class="diag-modal-body">
                    <div id="diag-context-slot"></div>
                    <div id="diag-idle-slot"></div>
                    <div id="diag-progress-bar-wrap" class="diag-progress-bar-wrap">
                        <div id="diag-progress-bar-fill" class="diag-progress-bar-fill"></div>
                        <span id="diag-progress-bar-label">iniciando…</span>
                    </div>
                    <div class="diag-progress-log" id="diag-progress-log"></div>
                    <div id="diag-approval-slot"></div>
                    <div id="diag-chunks-slot"></div>
                </div>
            </div>`;
        document.body.appendChild(backdrop);
        $('diag-modal-close').addEventListener('click', closeModal);
        backdrop.addEventListener('click', (e) => {
            if (e.target === backdrop) closeModal();
        });
        state.rendered = true;
        renderAll();
    }

    function renderProgressBar() {
        const wrap = $('diag-progress-bar-wrap');
        const fill = $('diag-progress-bar-fill');
        const label = $('diag-progress-bar-label');
        if (!fill || !label) return;

        // Ocultar barra cuando no hay diagnóstico activo
        if (wrap) wrap.style.display = state.status === 'idle' ? 'none' : '';

        // Heurística de progreso basada en el estado + cantidad de eventos.
        // El agente emite ~5-7 mensajes durante una corrida normal. Usamos eso
        // como base para la barra. Estados terminales saltan a 100%.
        let pct = 0;
        let txt = 'iniciando…';
        const phases = Math.max(7, state.progressLog.length);
        switch (state.status) {
            case 'idle':
                pct = 0; txt = ''; break;
            case 'running':
                pct = Math.min(85, 10 + (state.progressLog.length / phases) * 75);
                txt = `analizando… (${state.progressLog.length} fase${state.progressLog.length === 1 ? '' : 's'})`;
                break;
            case 'awaiting_approval':
                pct = 90; txt = 'esperando tu aprobación'; break;
            case 'awaiting_model_choice':
                pct = 88; txt = 'esperando elección de modelo'; break;
            case 'resolved':
                pct = 100; txt = 'completado ✓'; break;
            case 'error':
                pct = 100; txt = 'error / expirado'; break;
        }
        fill.style.width = `${pct}%`;
        fill.dataset.status = state.status;
        label.textContent = txt;
    }
    function closeModal() {
        const el = $('diag-modal-backdrop');
        if (el) el.remove();
        state.rendered = false;
    }

    function renderAll() {
        if (!state.rendered) return;
        const pill = $('diag-status-pill');
        if (pill) {
            pill.className = 'diag-status-pill ' + ({
                running: 'running',
                awaiting_approval: 'awaiting',
                resolved: 'resolved',
                error: 'error',
                idle: 'idle',
            }[state.status] || 'running');
            pill.textContent = state.status.replace('_', ' ');
        }
        renderContext();
        renderIdle();
        renderProgressBar();
        renderProgressLog();
        renderApproval();
        renderChunks();
    }

    function renderContext() {
        const slot = $('diag-context-slot');
        if (!slot) return;
        if (!state.userContext) { slot.innerHTML = ''; return; }
        slot.innerHTML = `<div class="diag-context-box"><span class="diag-context-label">CONTEXTO</span>${esc(state.userContext)}</div>`;
    }

    function renderIdle() {
        const slot = $('diag-idle-slot');
        if (!slot) return;
        if (state.status !== 'idle') { slot.innerHTML = ''; return; }
        slot.innerHTML = `
            <div class="diag-idle-wrap">
                <p class="diag-idle-hint">No hay diagnóstico activo.</p>
                <button class="diag-start-btn" id="diag-start-btn">Iniciar diagnóstico</button>
            </div>`;
        const btn = $('diag-start-btn');
        if (btn) btn.addEventListener('click', startDiagnose);
    }

    // ── Notificación en chat (terminal) cuando el diagnostic cambia de estado ──
    function notifyChat(text, kind = 'CORE') {
        try {
            const app = window.APP;
            if (app && typeof app.print === 'function') {
                app.print(kind, text);
            }
        } catch (_) { /* noop */ }
    }

    function renderProgressLog() {
        const box = $('diag-progress-log');
        if (!box) return;
        box.innerHTML = state.progressLog.map(entry =>
            `<div class="diag-line"><span class="diag-line-ts">${esc(entry.ts)}</span>${esc(entry.text)}</div>`
        ).join('');
        box.scrollTop = box.scrollHeight;
    }

    function renderApproval() {
        const slot = $('diag-approval-slot');
        if (!slot) return;
        if (state.status !== 'awaiting_approval' || !state.hypothesisSummary) {
            slot.innerHTML = '';
            return;
        }
        slot.innerHTML = `
            <div class="diag-approval-card">
                <div class="diag-approval-title">⚠ Aprobación requerida</div>
                <div class="diag-approval-summary">${esc(state.hypothesisSummary)}</div>
                <div class="diag-approval-actions">
                    <button class="diag-approval-btn primary" data-action="view">Ver diagnóstico</button>
                    <button class="diag-approval-btn danger"  data-action="ignore">Ignorar</button>
                    <button class="diag-approval-btn"          data-action="more_context">Más contexto</button>
                </div>
            </div>`;
        slot.querySelectorAll('.diag-approval-btn').forEach(btn => {
            btn.addEventListener('click', () => sendApprovalResponse(btn.dataset.action));
        });
    }

    function renderChunks() {
        const slot = $('diag-chunks-slot');
        if (!slot) return;
        if (!state.chunks.length) { slot.innerHTML = ''; return; }
        slot.innerHTML = state.chunks
            .map(c => `<div class="diag-chunk-block">${esc(c)}</div>`).join('');
    }

    // ── Backend calls ────────────────────────────────────────────────────────
    function token() {
        return (window.auth && window.auth.getToken && window.auth.getToken()) || null;
    }

    async function startDiagnose() {
        const tok = token();
        if (!tok) {
            console.warn('[diag] No token — user must be logged in as admin');
            return;
        }
        try {
            const res = await window.auth.authFetch(`${API_BASE}/api/self/diagnose`, {
                method: 'POST',
            });
            const data = await res.json();
            if (!data.success) throw new Error(data.detail || 'diagnose failed');
            state.currentRequestId = data.request_id;
            state.status = 'running';
            state.progressLog = [{ ts: nowHMS(), text: '🔍 Diagnóstico iniciado' }];
            state.hypothesisSummary = '';
            state.chunks = [];
            openModal();
            updateFabBadge();
        } catch (err) {
            console.error('[diag] startDiagnose error', err);
            alert('No se pudo iniciar el diagnóstico: ' + (err.message || err));
        }
    }

    function sendApprovalResponse(action) {
        if (!state.currentRequestId) return;
        const payload = {
            type: 'diag:approval_response',
            request_id: state.currentRequestId,
            action,
            token: token(),
        };
        const ws = window.wsClient;
        if (ws && ws.isConnected()) {
            ws.socket.send(JSON.stringify(payload));
        }
    }

    function sendModelChoice(requestId, choice) {
        const ws = window.wsClient;
        if (!ws || !ws.isConnected()) return;
        ws.socket.send(JSON.stringify({
            type: 'diag:model_choice_response',
            request_id: requestId,
            choice,
            token: token(),
        }));
    }

    function renderModelChoiceCard(requestId, problemType, options) {
        const slot = document.getElementById('diag-approval-slot');
        if (!slot) return;
        // Limpiar tarjeta anterior si existe
        const prev = slot.querySelector('.diag-model-choice-card');
        if (prev) prev.remove();

        const card = document.createElement('div');
        card.className = 'diag-model-choice-card';
        const btnHtml = options.map(o =>
            `<button class="diag-model-btn" data-choice="${escHtml(o.id)}">
                <strong>${escHtml(o.label)}</strong>
                <span class="diag-model-desc">${escHtml(o.description || '')}</span>
            </button>`
        ).join('');
        card.innerHTML =
            `<p class="diag-choice-title">🔧 Diagnóstico Tier 3 — ${escHtml(problemType)}</p>` +
            `<p class="diag-choice-subtitle">Selecciona el modelo:</p>` +
            btnHtml;

        card.querySelectorAll('.diag-model-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                sendModelChoice(requestId, btn.dataset.choice);
                card.remove();
                state.progressLog.push({ ts: nowHMS(), text: `🤖 Modelo elegido: ${btn.dataset.choice}` });
                renderProgressLog();
            });
        });
        slot.appendChild(card);
        openModal();
    }

    function escHtml(str) {
        return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    async function fetchPending() {
        const tok = token();
        if (!tok) return;
        try {
            const res = await window.auth.authFetch(`${API_BASE}/api/self/diagnose/pending`);
            const data = await res.json();
            if (!data.success) return;
            const pending = data.pending || [];
            if (!pending.length) return;
            const d = pending[0];
            state.currentRequestId = d.request_id;
            state.status = d.status || 'running';
            state.progressLog = (d.progress_log || []).map(e => ({
                ts: e.ts ? new Date(e.ts * 1000).toLocaleTimeString() : nowHMS(),
                text: e.text,
            }));
            state.hypothesisSummary = d.hypothesis_summary || '';
            updateFabBadge();
            // No abrir modal automáticamente — el usuario puede hacer click en el FAB.
            // Forwardear a call mode si aplica.
            forwardToCallMode();
        } catch (err) {
            console.warn('[diag] fetchPending failed', err);
        }
    }

    // ── WS message routing ──────────────────────────────────────────────────
    function forwardToCallMode() {
        // call-mode.js escucha estos eventos personalizados.
        try {
            window.dispatchEvent(new CustomEvent('zephyro:diag-state', {
                detail: {
                    request_id: state.currentRequestId,
                    status: state.status,
                    hypothesis_summary: state.hypothesisSummary,
                }
            }));
        } catch (_) {}
    }

    function handleWsMessage(payload) {
        const t = payload && payload.type;
        if (!t || !t.startsWith('diag:')) return;
        const rid = payload.request_id;

        switch (t) {
            case 'diag:progress': {
                // Lazily adopt request_id when triggered from chat (FAB not used,
                // so currentRequestId is still null but the diagnostic is running).
                if (state.currentRequestId === null && state.status === 'idle' && rid) {
                    state.currentRequestId = rid;
                    state.status = 'running';
                    updateFabBadge();
                }
                if (rid !== state.currentRequestId) return;
                const _text = payload.text || '';
                // "Contexto: ..." mensajes van al banner de contexto, no al progress log
                if (_text.startsWith('Contexto: ')) {
                    state.userContext = _text.slice('Contexto: '.length);
                    renderContext();
                } else {
                    state.progressLog.push({ ts: nowHMS(), text: _text });
                    renderProgressLog();
                }
                break;
            }
            case 'diag:model_choice':
                state.currentRequestId = rid;
                state.status = 'awaiting_model_choice';
                updateFabBadge();
                renderModelChoiceCard(rid, payload.problem_type || '', payload.options || []);
                // timeout_sec / default_choice vienen del gate (ws_approval_gate.py):
                // call-mode.js los usa para avisar en voz alta cuánto queda antes
                // de que el gate elija solo.
                window.dispatchEvent(new CustomEvent('zephyro:diag-state', {
                    detail: {
                        request_id: rid,
                        status: 'model_choice',
                        options: payload.options,
                        problem_type: payload.problem_type || '',
                        timeout_sec: payload.timeout_sec || 0,
                        default_choice: payload.default_choice || '',
                    }
                }));
                break;
            case 'diag:model_choice_confirmed':
                state.progressLog.push({ ts: nowHMS(), text: `✅ Modelo confirmado: ${payload.choice || '?'}` });
                renderProgressLog();
                break;
            case 'diag:approval':
                state.currentRequestId = rid;
                state.status = 'awaiting_approval';
                state.hypothesisSummary = payload.hypothesis_summary || payload.text || '';
                updateFabBadge();
                renderAll();
                forwardToCallMode();
                // Notificar al usuario en el chat (visible aunque la modal esté
                // cerrada). Dedup por request_id: el escalado more_context
                // re-emite diag:approval para el mismo diagnóstico.
                if (state.lastNotifiedApprovalId !== rid) {
                    state.lastNotifiedApprovalId = rid;
                    notifyChat(
                        '⚠ Diagnóstico necesita tu aprobación — abre el panel para revisar la hipótesis.',
                        'DIAG'
                    );
                }
                break;
            case 'diag:chunk':
                if (rid !== state.currentRequestId) return;
                state.chunks.push(payload.text || '');
                renderChunks();
                break;
            case 'diag:resolved': {
                if (rid !== state.currentRequestId) return;
                state.status = 'resolved';
                state.progressLog.push({ ts: nowHMS(), text: `✅ Resuelto (${payload.action || 'ok'})` });
                updateFabBadge();
                renderAll();
                // Frase legible en vez del enum interno (view/more_context/...)
                const actionLabels = {
                    view: 'hipótesis enviada al panel',
                    more_context: 're-analizado con más contexto',
                    ignore: 'ignorado por el usuario',
                    cancel: 'cancelado',
                    // 'completed' lo emite el cierre garantizado del agente
                    // (run() → gate.send_resolved) cuando no hubo hallazgos.
                    completed: 'sin hallazgos que reportar',
                };
                const actionLabel = actionLabels[payload.action] || 'completado';
                notifyChat(`✓ Diagnóstico completado — ${actionLabel}.`, 'DIAG');
                if (payload.action === 'ignore' || payload.action === 'cancel') {
                    setTimeout(() => {
                        if (state.status === 'resolved') {
                            state.currentRequestId = null;
                            state.status = 'idle';
                            state.progressLog = [];
                            state.hypothesisSummary = '';
                            state.chunks = [];
                            updateFabBadge();
                            closeModal();
                        }
                    }, 1500);
                }
                forwardToCallMode();
                break;
            }
            case 'diag:expired': {
                if (rid !== state.currentRequestId) return;
                state.status = 'error';
                // auto_action: el gate ya siguió solo con la opción por defecto
                // (model_choice). Decirlo, en vez de reportar solo "expirado".
                const auto = payload.auto_action || '';
                const expiredTxt = auto
                    ? `⌛ Sin respuesta a tiempo — seguí con ${auto}.`
                    : '⌛ Diagnóstico expirado (timeout sin respuesta).';
                state.progressLog.push({ ts: nowHMS(), text: expiredTxt });
                updateFabBadge();
                renderAll();
                notifyChat(expiredTxt, 'DIAG');
                window.dispatchEvent(new CustomEvent('zephyro:diag-state', {
                    detail: { request_id: rid, status: 'expired', auto_action: auto }
                }));
                break;
            }
        }
    }

    // ── Bootstrap ────────────────────────────────────────────────────────────
    function init() {
        ensureFab();
        updateFabBadge();
        if (window.wsClient && typeof window.wsClient.onMessage === 'function') {
            window.wsClient.onMessage(handleWsMessage);
        }
        // Resume al cargar (y cuando cambia el estado WS a CONNECTED).
        fetchPending();
        if (window.wsClient && typeof window.wsClient.onStateChange === 'function') {
            window.wsClient.onStateChange((st) => {
                if (st === 'CONNECTED') setTimeout(fetchPending, 400);
            });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Export (útil para call-mode.js)
    window.zephyroDiagnostic = {
        getState: () => ({ ...state }),
        startDiagnose,
        openModal,
        closeModal,
        sendApprovalResponse,
    };
})();
