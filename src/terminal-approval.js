/**
 * terminal-approval.js — Modal de aprobacion para comandos PowerShell ejecutados
 * por la TerminalSkill / GitHubSkill / IntrospectionSkill.
 *
 * Contrato WS (ver backend/subagents/ws_approval_gate.py con prefix="terminal"):
 *   backend -> frontend: terminal:progress, terminal:approval, terminal:chunk,
 *                        terminal:resolved, terminal:expired
 *   frontend -> backend: terminal:approval_response { action: view|ignore },
 *                        terminal:cancel
 *
 * Diferencias con diagnostic.js:
 *   - Modal mas simple (un solo prompt + 2 botones aprobar/rechazar)
 *   - Codigo monoespaciado para mostrar el comando
 *   - "view" = aprobar, "ignore" = rechazar
 */
(function () {
    'use strict';

    const state = {
        currentRequestId: null,
        cmdPreview: '',
        active: false,
    };

    function $(id) { return document.getElementById(id); }
    function esc(s) {
        const d = document.createElement('span');
        d.textContent = String(s);
        return d.innerHTML;
    }

    // ── Modal ────────────────────────────────────────────────────────────────

    function ensureStyles() {
        if ($('term-approval-styles')) return;
        const style = document.createElement('style');
        style.id = 'term-approval-styles';
        style.textContent = `
            .term-modal-backdrop {
                position: fixed; inset: 0; background: rgba(0,0,0,.55);
                z-index: 9050; display: flex; align-items: center; justify-content: center;
            }
            .term-modal {
                background: #0e1116; color: #e6edf3;
                border: 1px solid #30363d; border-radius: 8px;
                width: min(620px, 90vw); padding: 18px 20px;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
                box-shadow: 0 20px 60px rgba(0,0,0,.5);
            }
            .term-modal h3 { margin: 0 0 8px 0; font-size: 14px; letter-spacing: .04em; color: #f0883e; }
            .term-modal p { margin: 0 0 12px 0; font-size: 13px; color: #c9d1d9; }
            .term-modal pre {
                background: #161b22; border: 1px solid #21262d; border-radius: 6px;
                padding: 10px 12px; font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
                font-size: 12.5px; color: #e6edf3; white-space: pre-wrap; word-break: break-all;
                max-height: 240px; overflow-y: auto;
            }
            .term-modal-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 14px; }
            .term-btn {
                background: transparent; color: #c9d1d9; border: 1px solid #30363d;
                padding: 7px 14px; border-radius: 6px; cursor: pointer;
                font-size: 12.5px; letter-spacing: .04em;
            }
            .term-btn:hover { border-color: #6e7681; }
            .term-btn-approve { background: #238636; border-color: #2ea043; color: #fff; }
            .term-btn-approve:hover { background: #2ea043; }
            .term-btn-reject { background: #cf2222; border-color: #da3633; color: #fff; }
            .term-btn-reject:hover { background: #da3633; }
        `;
        document.head.appendChild(style);
    }

    function openModal() {
        if (state.active) return;
        ensureStyles();
        const backdrop = document.createElement('div');
        backdrop.className = 'term-modal-backdrop';
        backdrop.id = 'term-modal-backdrop';
        backdrop.innerHTML = `
            <div class="term-modal" role="dialog" aria-labelledby="term-modal-title">
                <h3 id="term-modal-title">APROBAR COMANDO</h3>
                <p>Zephyro quiere ejecutar este comando. Revisa antes de aprobar.</p>
                <pre id="term-modal-cmd">${esc(state.cmdPreview)}</pre>
                <div class="term-modal-actions">
                    <button class="term-btn term-btn-reject" id="term-btn-reject">RECHAZAR</button>
                    <button class="term-btn term-btn-approve" id="term-btn-approve">APROBAR</button>
                </div>
            </div>
        `;
        document.body.appendChild(backdrop);
        state.active = true;

        backdrop.addEventListener('click', (e) => {
            if (e.target === backdrop) respond('ignore');
        });
        $('term-btn-approve').addEventListener('click', () => respond('view'));
        $('term-btn-reject').addEventListener('click', () => respond('ignore'));

        // Atajos: Esc rechaza, Ctrl/Cmd+Enter aprueba
        document.addEventListener('keydown', onKeyDown);
    }

    function closeModal() {
        const bd = $('term-modal-backdrop');
        if (bd) bd.remove();
        state.active = false;
        document.removeEventListener('keydown', onKeyDown);
    }

    function onKeyDown(ev) {
        if (!state.active) return;
        if (ev.key === 'Escape') {
            ev.preventDefault();
            respond('ignore');
        } else if ((ev.metaKey || ev.ctrlKey) && ev.key === 'Enter') {
            ev.preventDefault();
            respond('view');
        }
    }

    function getToken() {
        try {
            return (window.zephyroToken)
                || sessionStorage.getItem('zephyro_token')
                || localStorage.getItem('zephyro_token')
                || '';
        } catch (_) { return ''; }
    }

    function respond(action) {
        if (!state.currentRequestId) {
            closeModal();
            return;
        }
        const ws = window.wsClient;
        if (ws && ws.socket && typeof ws.isConnected === 'function' && ws.isConnected()) {
            try {
                ws.socket.send(JSON.stringify({
                    type: 'terminal:approval_response',
                    request_id: state.currentRequestId,
                    action,
                    token: getToken(),
                }));
            } catch (e) {
                console.error('[terminal] error enviando respuesta:', e);
            }
        }
        state.currentRequestId = null;
        state.cmdPreview = '';
        closeModal();
    }

    // ── WS handler ───────────────────────────────────────────────────────────

    function handleWsMessage(payload) {
        const t = payload && payload.type;
        if (!t || !t.startsWith('terminal:')) return;
        const rid = payload.request_id;

        switch (t) {
            case 'terminal:approval':
                state.currentRequestId = rid;
                state.cmdPreview = payload.hypothesis_summary || payload.text || '(sin preview)';
                openModal();
                break;
            case 'terminal:resolved':
                if (rid !== state.currentRequestId) return;
                closeModal();
                state.currentRequestId = null;
                break;
            case 'terminal:expired':
                if (rid !== state.currentRequestId) return;
                console.warn('[terminal] aprobacion expirada:', rid);
                closeModal();
                state.currentRequestId = null;
                break;
            case 'terminal:progress':
                // Opcional: mostrar progreso (por ahora no hace falta)
                break;
        }
    }

    // ── Bootstrap ────────────────────────────────────────────────────────────

    function init() {
        if (window.wsClient && typeof window.wsClient.onMessage === 'function') {
            window.wsClient.onMessage(handleWsMessage);
        } else {
            // Reintentar despues de que websocket.js cargue
            setTimeout(init, 200);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
