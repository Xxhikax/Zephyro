(function () {
    'use strict';

    const POLL_INTERVAL_MS = 15_000;
    let _pollTimer = null;

    function _token() {
        return window.auth?.getToken?.() || '';
    }

    function _print(level, msg) {
        window.ZephyroTerminalApp?.print?.(level, msg);
    }

    // ── Visibility ────────────────────────────────────────────────────────────

    function _showUpdateBar(changedFiles) {
        const bar = document.getElementById('backend-update-bar');
        if (!bar) return;
        bar.style.display = '';
        if (changedFiles && changedFiles.length) {
            const restartBtn = document.getElementById('restart-backend-btn');
            if (restartBtn) {
                restartBtn.title = `Archivos modificados: ${changedFiles.join(', ')} — reinicia para aplicar`;
                restartBtn.classList.add('pulse');
            }
        }
    }

    function _hideUpdateBar() {
        const bar = document.getElementById('backend-update-bar');
        if (!bar) return;
        bar.style.display = 'none';
        const restartBtn = document.getElementById('restart-backend-btn');
        if (restartBtn) restartBtn.classList.remove('pulse');
    }

    // ── Polling ───────────────────────────────────────────────────────────────

    async function _poll() {
        const token = _token();
        if (!token) return;
        try {
            const res = await window.auth.authFetch(
                `${API_BASE}/api/system/needs-restart`
            );
            if (!res.ok) return;
            const data = await res.json();
            if (data.needs_restart) {
                _showUpdateBar(data.changed_files);
            }
        } catch (_) {
            // Backend offline — no-op; WebSocket reconnect handles the UI
        }
    }

    function startPolling() {
        if (_pollTimer) return;
        _poll();
        _pollTimer = setInterval(_poll, POLL_INTERVAL_MS);
    }

    // ── Reload config (no downtime) ───────────────────────────────────────────

    async function reloadConfig() {
        const btn = document.getElementById('reload-config-btn');
        const token = _token();
        if (!token || !btn) return;

        const orig = btn.textContent;
        btn.textContent = '...';
        btn.disabled = true;
        btn.classList.add('active');

        try {
            const res = await window.auth.authFetch(
                `${API_BASE}/api/system/reload-config`,
                { method: 'POST' }
            );
            const data = await res.json();
            if (res.ok && data.success) {
                btn.textContent = '✓ OK';
                _print('SYSTEM', `Config recargado: ${data.reloaded.join(', ')}`);
            } else {
                btn.textContent = '✗ ERR';
                _print('ERROR', data.detail || 'Reload config falló');
            }
        } catch (e) {
            btn.textContent = '✗ ERR';
            _print('ERROR', `Reload config: ${e.message}`);
        } finally {
            setTimeout(() => {
                btn.textContent = orig;
                btn.disabled = false;
                btn.classList.remove('active');
            }, 2500);
        }
    }

    // ── Full restart (~2.5 s downtime) ────────────────────────────────────────

    async function restartBackend() {
        const btn = document.getElementById('restart-backend-btn');
        const token = _token();
        if (!token || !btn) return;

        if (!confirm('Reiniciar el backend completo (~2.5 s de downtime). ¿Continuar?')) return;

        btn.textContent = 'WAIT...';
        btn.disabled = true;
        btn.classList.add('active');
        btn.classList.remove('pulse');
        _print('SYSTEM', 'Reiniciando backend...');

        try {
            await window.auth.authFetch(
                `${API_BASE}/api/system/restart`,
                { method: 'POST' }
            );
        } catch (_) {
            // El socket puede cerrarse antes de que llegue la respuesta — es esperado
        }

        // Esperar que el nuevo proceso arranque, luego reconectar
        setTimeout(() => {
            _hideUpdateBar();
            window.wsClient?.restart?.();
            btn.textContent = '⟳ RESTART';
            btn.disabled = false;
            btn.classList.remove('active');
            _print('SYSTEM', 'Backend reiniciado — reconectando...');
        }, 3500);
    }

    // ── Init ──────────────────────────────────────────────────────────────────

    function init() {
        document.getElementById('reload-config-btn')
            ?.addEventListener('click', reloadConfig);
        document.getElementById('restart-backend-btn')
            ?.addEventListener('click', restartBackend);

        // Start polling once the user is authenticated
        // auth.js fires 'auth:login' or sets a flag — wait for first token
        const waitForAuth = setInterval(() => {
            if (_token()) {
                clearInterval(waitForAuth);
                startPolling();
            }
        }, 1000);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    window.ZephyroSystem = { reloadConfig, restartBackend, startPolling };
})();
