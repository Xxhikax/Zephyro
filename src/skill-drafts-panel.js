/* skill-drafts-panel.js — Ventana flotante para aprobacion de skills auto-generadas.
   - Lista de drafts pendientes con nombre, descripcion, riesgo, triggers.
   - Vista previa del codigo Python con syntax highlight basico.
   - Botones APROBAR y RECHAZAR por draft.
   - Badge en la UI principal cuando hay drafts pendientes.
   - Escucha eventos WS tipo "skill_proposed" para recarga automatica.
*/
(function () {
    'use strict';

    const API = (typeof API_BASE !== 'undefined') ? API_BASE : 'http://localhost:8000';

    let _drafts = [];
    let _windowEl = null;
    let _badgeEl = null;
    let _dragging = false;
    let _dragX = 0, _dragY = 0;

    function token() {
        return window.auth ? window.auth.getToken() : localStorage.getItem('authToken');
    }
    function enc(s) { return encodeURIComponent(s); }
    function esc(s) {
        const d = document.createElement('span');
        d.textContent = String(s);
        return d.innerHTML;
    }

    /* ------------------------------------------------------------------ */
    /* API calls                                                           */
    /* ------------------------------------------------------------------ */
    async function loadDrafts() {
        const tk = token();
        if (!tk) return;
        try {
            const r = await window.auth.authFetch(`${API}/api/skills/drafts`);
            if (!r.ok) return;
            const data = await r.json();
            _drafts = data.drafts || [];
            updateBadge();
        } catch (_) {}
    }

    async function approveDraft(slug) {
        const tk = token();
        if (!tk) return;
        const r = await window.auth.authFetch(`${API}/api/skills/drafts/${slug}/approve`, { method: 'POST' });
        if (r.ok) {
            _drafts = _drafts.filter(d => d.slug !== slug);
            renderList();
            updateBadge();
        }
    }

    async function rejectDraft(slug) {
        const tk = token();
        if (!tk) return;
        const r = await window.auth.authFetch(`${API}/api/skills/drafts/${slug}/reject`, { method: 'POST' });
        if (r.ok) {
            _drafts = _drafts.filter(d => d.slug !== slug);
            renderList();
            updateBadge();
        }
    }

    async function previewDraft(slug) {
        const tk = token();
        if (!tk) return null;
        try {
            const r = await window.auth.authFetch(`${API}/api/skills/drafts/${slug}/preview`);
            if (!r.ok) return null;
            return await r.json();
        } catch (_) { return null; }
    }

    /* ------------------------------------------------------------------ */
    /* Badge                                                               */
    /* ------------------------------------------------------------------ */
    function ensureBadge() {
        if (_badgeEl) return;
        _badgeEl = document.createElement('span');
        _badgeEl.id = 'skill-drafts-badge';
        _badgeEl.className = 'skill-drafts-badge';
        _badgeEl.style.cssText = 'display:none;background:#ff4d4d;color:#fff;border-radius:10px;padding:1px 7px;font-size:11px;margin-left:6px;cursor:pointer;';
        _badgeEl.addEventListener('click', toggleWindow);
        const controls = document.querySelector('.chat-controls') || document.querySelector('.top-bar') || document.body;
        controls.appendChild(_badgeEl);
    }

    function updateBadge() {
        ensureBadge();
        const count = _drafts.length;
        _badgeEl.style.display = count > 0 ? 'inline-block' : 'none';
        _badgeEl.textContent = count > 0 ? `${count} draft${count !== 1 ? 's' : ''}` : '';
    }

    /* ------------------------------------------------------------------ */
    /* Window                                                              */
    /* ------------------------------------------------------------------ */
    function createWindow() {
        if (_windowEl) return;
        _windowEl = document.createElement('div');
        _windowEl.id = 'skill-drafts-window';
        _windowEl.style.cssText = `
            position:fixed;top:60px;right:20px;z-index:10000;
            width:560px;max-height:80vh;background:#1a1d2e;
            border:1px solid #2d3355;border-radius:10px;
            box-shadow:0 8px 32px rgba(0,0,0,0.5);
            display:flex;flex-direction:column;
            font-family:system-ui,sans-serif;font-size:13px;
        `;

        const header = document.createElement('div');
        header.id = 'skill-drafts-header';
        header.style.cssText = `
            padding:12px 16px;background:#232840;border-radius:10px 10px 0 0;
            cursor:move;display:flex;justify-content:space-between;align-items:center;
            user-select:none;color:#b0b8d0;font-weight:600;font-size:14px;
        `;
        header.innerHTML = `
            <span>DRAFTS DE SKILLS (${_drafts.length})</span>
            <span id="skill-drafts-close" style="cursor:pointer;font-size:18px;color:#6a7290;">&times;</span>
        `;

        const body = document.createElement('div');
        body.id = 'skill-drafts-body';
        body.style.cssText = 'padding:12px;overflow-y:auto;flex:1;';

        _windowEl.appendChild(header);
        _windowEl.appendChild(body);
        document.body.appendChild(_windowEl);

        document.getElementById('skill-drafts-close').addEventListener('click', toggleWindow);

        header.addEventListener('mousedown', (e) => {
            _dragging = true;
            _dragX = e.clientX - _windowEl.offsetLeft;
            _dragY = e.clientY - _windowEl.offsetTop;
        });
        document.addEventListener('mousemove', (e) => {
            if (!_dragging) return;
            _windowEl.style.left = (e.clientX - _dragX) + 'px';
            _windowEl.style.top = (e.clientY - _dragY) + 'px';
            _windowEl.style.right = 'auto';
        });
        document.addEventListener('mouseup', () => { _dragging = false; });
    }

    function toggleWindow() {
        if (_windowEl && _windowEl.style.display !== 'none') {
            _windowEl.style.display = 'none';
        } else {
            if (!_windowEl) createWindow();
            _windowEl.style.display = 'flex';
            renderList();
        }
    }

    /* ------------------------------------------------------------------ */
    /* Rendering                                                           */
    /* ------------------------------------------------------------------ */
    function renderList() {
        const body = document.getElementById('skill-drafts-body');
        if (!body) return;

        const headerEl = document.getElementById('skill-drafts-header');
        if (headerEl) {
            headerEl.innerHTML = `
                <span>DRAFTS DE SKILLS (${_drafts.length})</span>
                <span id="skill-drafts-close" style="cursor:pointer;font-size:18px;color:#6a7290;">&times;</span>
            `;
            document.getElementById('skill-drafts-close').addEventListener('click', toggleWindow);
        }

        if (_drafts.length === 0) {
            body.innerHTML = '<div style="color:#6a7290;text-align:center;padding:40px;">No hay drafts pendientes.</div>';
            return;
        }

        let html = '';
        _drafts.forEach((d, i) => {
            const riskColor = d.risk_level === 'high' ? '#ff4d4d' : d.risk_level === 'medium' ? '#ffd700' : '#00ff88';
            const triggers = (d.triggers || []).slice(0, 4).join(', ');
            html += `
                <div class="draft-card" style="
                    margin-bottom:10px;padding:12px;background:#232840;
                    border-radius:8px;border-left:3px solid ${riskColor};
                    color:#c0c8e0;
                ">
                    <div style="display:flex;justify-content:space-between;align-items:start;margin-bottom:6px;">
                        <strong style="font-size:14px;color:#e0e4f0;">${esc(d.name)}</strong>
                        <span style="font-size:10px;background:${riskColor}22;color:${riskColor};padding:1px 6px;border-radius:4px;">${esc(d.risk_level)}</span>
                    </div>
                    <div style="color:#9098b0;margin-bottom:6px;">${esc(d.description || 'Sin descripcion')}</div>
                    <div style="font-size:11px;color:#6a7290;margin-bottom:8px;">
                        Triggers: ${esc(triggers || 'ninguno')}
                    </div>
                    <div style="display:flex;gap:8px;">
                        <button class="draft-btn draft-btn-approve" data-slug="${esc(d.slug)}" style="
                            padding:5px 14px;background:#1a4a2a;color:#00ff88;border:1px solid #2a6a3a;
                            border-radius:5px;cursor:pointer;font-size:12px;
                        ">APROBAR</button>
                        <button class="draft-btn draft-btn-reject" data-slug="${esc(d.slug)}" style="
                            padding:5px 14px;background:#4a1a1a;color:#ff4d4d;border:1px solid #6a2a2a;
                            border-radius:5px;cursor:pointer;font-size:12px;
                        ">RECHAZAR</button>
                        <button class="draft-btn draft-btn-preview" data-slug="${esc(d.slug)}" style="
                            padding:5px 14px;background:#1a1a3a;color:#6ad0ff;border:1px solid #2a2a5a;
                            border-radius:5px;cursor:pointer;font-size:12px;
                        ">VER CODIGO</button>
                    </div>
                    <div class="draft-code-preview" id="draft-code-${esc(d.slug)}" style="display:none;margin-top:8px;"></div>
                </div>
            `;
        });
        body.innerHTML = html;

        body.querySelectorAll('.draft-btn-approve').forEach(btn => {
            btn.addEventListener('click', () => approveDraft(btn.dataset.slug));
        });
        body.querySelectorAll('.draft-btn-reject').forEach(btn => {
            btn.addEventListener('click', () => rejectDraft(btn.dataset.slug));
        });
        body.querySelectorAll('.draft-btn-preview').forEach(btn => {
            btn.addEventListener('click', async () => {
                const slug = btn.dataset.slug;
                const codeSlot = document.getElementById('draft-code-' + slug);
                if (codeSlot.style.display === 'block') {
                    codeSlot.style.display = 'none';
                    btn.textContent = 'VER CODIGO';
                    return;
                }
                const preview = await previewDraft(slug);
                if (preview) {
                    codeSlot.innerHTML = `
                        <pre style="
                            background:#0d1117;color:#c9d1d9;padding:10px;
                            border-radius:6px;font-size:11px;overflow-x:auto;
                            max-height:300px;overflow-y:auto;margin-top:6px;
                        "><code class="draft-py">${esc(preview.skill_py || 'Sin codigo')}</code></pre>
                        ${preview.lint_ok !== null ? `
                            <div style="font-size:10px;margin-top:4px;color:${preview.lint_ok ? '#00ff88' : '#ff4d4d'}">
                                Linter: ${preview.lint_ok ? 'OK' : esc(preview.lint_summary || 'Errores')}
                            </div>
                        ` : ''}
                    `;
                    codeSlot.style.display = 'block';
                    btn.textContent = 'OCULTAR CODIGO';
                }
            });
        });
    }

    /* ------------------------------------------------------------------ */
    /* WebSocket listener                                                  */
    /* ------------------------------------------------------------------ */
    function onWebSocketMessage(msg) {
        if (msg.type === 'skill_proposed') {
            loadDrafts();
        }
    }

    /* ------------------------------------------------------------------ */
    /* Init                                                                */
    /* ------------------------------------------------------------------ */
    window.skillDraftsPanel = {
        load: loadDrafts,
        toggle: toggleWindow,
        onMessage: onWebSocketMessage,
    };

    document.addEventListener('DOMContentLoaded', () => {
        setTimeout(loadDrafts, 2000);
    });

    if (window.wsClient && typeof window.wsClient.onMessage === 'function') {
        window.wsClient.onMessage(onWebSocketMessage);
    }
})();
