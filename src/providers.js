/**
 * providers.js — Panel admin para gestionar providers LLM (Anthropic, DeepSeek,
 * OpenRouter, Claude CLI, etc.) y su asignacion a tareas.
 *
 * Solo visible para usuarios con role admin. Los guests no ven el FAB.
 */
(function () {
    'use strict';

    const API_BASE = (typeof window.API_BASE === 'string' && window.API_BASE) || '';

    const state = {
        providers: [],
        taskAssignment: {},
        taskKinds: [],
        binaries: {},
        loaded: false,
    };

    function getToken() {
        try {
            return (window.auth && window.auth.getToken()) || '';
        } catch (_) { return ''; }
    }

    function getRole() {
        try {
            return (window.auth && window.auth.getRole()) || 'guest';
        } catch (_) { return 'guest'; }
    }

    function isAdmin() {
        return getRole() === 'admin';
    }

    function $(id) { return document.getElementById(id); }
    function esc(s) {
        const d = document.createElement('span');
        d.textContent = String(s == null ? '' : s);
        return d.innerHTML;
    }

    // ── Styles ───────────────────────────────────────────────────────────────

    function ensureStyles() {
        if ($('providers-styles')) return;
        const style = document.createElement('style');
        style.id = 'providers-styles';
        style.textContent = `
            .prov-modal-backdrop {
                position: fixed; inset: 0; background: rgba(0,0,0,.5);
                z-index: 9000; display: flex; align-items: center; justify-content: center;
            }
            .prov-modal {
                background: #0d1117; color: #e6edf3;
                border: 1px solid #30363d; border-radius: 8px;
                width: min(820px, 95vw); max-height: 88vh;
                display: flex; flex-direction: column;
                box-shadow: 0 20px 60px rgba(0,0,0,.5);
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
            }
            .prov-modal-head {
                display: flex; align-items: center; justify-content: space-between;
                padding: 14px 18px; border-bottom: 1px solid #21262d;
            }
            .prov-modal-head h2 {
                margin: 0; font-size: 14px; letter-spacing: .08em; color: #f0883e;
            }
            .prov-modal-body {
                padding: 16px 18px; overflow-y: auto; flex: 1;
            }
            .prov-section { margin-bottom: 22px; }
            .prov-section h3 {
                margin: 0 0 10px 0; font-size: 12px; letter-spacing: .08em;
                color: #8b949e; font-weight: 600;
            }
            .prov-card {
                background: #161b22; border: 1px solid #21262d; border-radius: 6px;
                padding: 12px 14px; margin-bottom: 10px;
            }
            .prov-card-head {
                display: flex; align-items: center; justify-content: space-between;
                gap: 8px; margin-bottom: 6px;
            }
            .prov-card-title {
                font-size: 13.5px; font-weight: 600; color: #e6edf3;
            }
            .prov-card-meta {
                font-size: 11.5px; color: #8b949e; margin-bottom: 8px;
            }
            .prov-pill {
                display: inline-block; padding: 2px 8px; border-radius: 10px;
                font-size: 11px; letter-spacing: .04em;
            }
            .prov-pill.enabled { background: rgba(46,160,67,.15); color: #56d364; border: 1px solid #2ea043; }
            .prov-pill.disabled { background: rgba(139,148,158,.12); color: #8b949e; border: 1px solid #30363d; }
            .prov-pill.error { background: rgba(207,34,34,.18); color: #ff7b72; border: 1px solid #cf2222; }
            .prov-card-actions {
                display: flex; gap: 6px; flex-wrap: wrap;
            }
            .prov-btn {
                background: transparent; color: #c9d1d9; border: 1px solid #30363d;
                padding: 5px 11px; border-radius: 5px; cursor: pointer;
                font-size: 11.5px; letter-spacing: .04em;
            }
            .prov-btn:hover { border-color: #6e7681; background: #21262d; }
            .prov-btn-primary { background: #238636; border-color: #2ea043; color: #fff; }
            .prov-btn-primary:hover { background: #2ea043; }

            .prov-form { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
            .prov-form label { font-size: 11.5px; color: #8b949e; }
            .prov-form input, .prov-form select {
                background: #0d1117; color: #e6edf3; border: 1px solid #30363d;
                border-radius: 5px; padding: 6px 9px; font-size: 12.5px;
                font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
            }
            .prov-task-row {
                display: flex; align-items: center; gap: 10px;
                padding: 6px 0; border-bottom: 1px dashed #21262d;
            }
            .prov-task-row:last-child { border-bottom: none; }
            .prov-task-name { flex: 0 0 180px; font-size: 12px; color: #c9d1d9; }
            .prov-task-row select {
                background: #0d1117; color: #e6edf3; border: 1px solid #30363d;
                border-radius: 5px; padding: 4px 7px; font-size: 12px; flex: 1;
            }
            .prov-bin-info {
                font-size: 11.5px; color: #8b949e; margin-top: 6px;
                font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
            }
            .prov-bin-found { color: #56d364; }
            .prov-bin-missing { color: #f97583; }
            .prov-modal-close {
                background: transparent; color: #8b949e;
                border: 1px solid #30363d; padding: 5px 10px; border-radius: 5px;
                cursor: pointer; font-size: 11.5px;
            }
            .prov-modal-close:hover { color: #f97583; border-color: #cf2222; }
        `;
        document.head.appendChild(style);
    }

    // ── FAB ──────────────────────────────────────────────────────────────────

    function _showAdminSection() {
        const sec = document.getElementById('admin-tools-section');
        if (sec) sec.style.display = '';
    }

    function ensureFab() {
        if (!isAdmin()) return;
        ensureStyles();
        _showAdminSection();
        const btn = $('prov-fab');
        if (!btn || btn._provWired) return;
        btn._provWired = true;
        btn.addEventListener('click', () => { openModal(); refresh(); });
    }

    function removeFab() {
        const sec = document.getElementById('admin-tools-section');
        if (sec) sec.style.display = 'none';
    }

    // ── Data fetch ───────────────────────────────────────────────────────────

    async function refresh() {
        const tok = getToken();
        if (!tok) return;
        try {
            const res = await fetch(`${API_BASE}/api/providers`, {
                headers: { 'Authorization': `Bearer ${tok}` },
            });
            if (!res.ok) {
                console.warn('[providers] GET /providers fallo:', res.status);
                return;
            }
            const data = await res.json();
            state.providers = data.providers || [];
            state.taskAssignment = data.task_assignment || {};
            state.taskKinds = data.task_kinds || [];
            state.binaries = data.binaries || {};
            state.loaded = true;
            renderModalBody();
        } catch (e) {
            console.error('[providers] fetch error:', e);
        }
    }

    async function configure(name, body) {
        const tok = getToken();
        const res = await fetch(`${API_BASE}/api/providers/${encodeURIComponent(name)}/configure`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${tok}`, 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) {
            const txt = await res.text().catch(() => '');
            alert(`Error configurando ${name}: ${res.status}\n${txt}`);
            return null;
        }
        return res.json();
    }

    async function testProvider(name) {
        const tok = getToken();
        const res = await fetch(`${API_BASE}/api/providers/${encodeURIComponent(name)}/test`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${tok}` },
        });
        return res.json();
    }

    async function assignTask(task, provider, model) {
        const tok = getToken();
        const res = await fetch(`${API_BASE}/api/providers/tasks/${encodeURIComponent(task)}/assign`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${tok}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider, model }),
        });
        return res.json();
    }

    // ── Modal ────────────────────────────────────────────────────────────────

    function openModal() {
        if ($('prov-modal-backdrop')) return;
        const backdrop = document.createElement('div');
        backdrop.className = 'prov-modal-backdrop';
        backdrop.id = 'prov-modal-backdrop';
        backdrop.innerHTML = `
            <div class="prov-modal" role="dialog" aria-labelledby="prov-modal-title">
                <div class="prov-modal-head">
                    <h2 id="prov-modal-title">PROVIDERS</h2>
                    <button class="prov-modal-close" id="prov-modal-close">CERRAR</button>
                </div>
                <div class="prov-modal-body" id="prov-modal-body">
                    <p style="color:#8b949e">Cargando...</p>
                </div>
            </div>
        `;
        document.body.appendChild(backdrop);
        backdrop.addEventListener('click', (e) => {
            if (e.target === backdrop) closeModal();
        });
        $('prov-modal-close').addEventListener('click', closeModal);
    }

    function closeModal() {
        const bd = $('prov-modal-backdrop');
        if (bd) bd.remove();
    }

    function renderModalBody() {
        const body = $('prov-modal-body');
        if (!body) return;

        const binsHtml = renderBinaries();
        const providersHtml = state.providers.map(renderProviderCard).join('');
        const tasksHtml = renderTaskAssignments();

        body.innerHTML = `
            <div class="prov-section">
                <h3>BINARIOS DETECTADOS</h3>
                <div class="prov-bin-info">${binsHtml}</div>
            </div>
            <div class="prov-section">
                <h3>PROVIDERS (${state.providers.length})</h3>
                ${providersHtml}
            </div>
            <div class="prov-section">
                <h3>ASIGNACION DE TAREAS</h3>
                ${tasksHtml}
            </div>
        `;

        wireProviderActions();
        wireTaskAssignments();
    }

    function renderBinaries() {
        return Object.entries(state.binaries).map(([name, path]) => {
            const cls = path ? 'prov-bin-found' : 'prov-bin-missing';
            const display = path ? esc(path) : 'no encontrado';
            return `<div><span class="${cls}">${esc(name)}</span>: ${display}</div>`;
        }).join('');
    }

    function renderProviderCard(p) {
        const enabledPill = p.enabled
            ? '<span class="prov-pill enabled">ACTIVO</span>'
            : '<span class="prov-pill disabled">INACTIVO</span>';
        const errPill = p.last_test_ok === false
            ? `<span class="prov-pill error" title="${esc(p.last_test_error || '')}">FALLO</span>`
            : '';
        const keyHint = p.api_key_set
            ? `<code style="color:#56d364">${esc(p.api_key_hint || '***')}</code>`
            : '<em style="color:#f97583">no configurada</em>';
        const baseUrl = p.base_url ? esc(p.base_url) : '<em>(default)</em>';
        const modelsList = (p.models || []).map(esc).join(', ') || '<em>(vacio)</em>';

        return `
            <div class="prov-card" data-provider="${esc(p.name)}">
                <div class="prov-card-head">
                    <span class="prov-card-title">${esc(p.name)}</span>
                    <span>${enabledPill} ${errPill}</span>
                </div>
                <div class="prov-card-meta">
                    backend: <code>${esc(p.backend)}</code> ·
                    api_key: ${keyHint} ·
                    base_url: ${baseUrl}
                </div>
                <div class="prov-card-meta">modelos: ${modelsList}</div>
                <div class="prov-form" data-form-for="${esc(p.name)}" hidden>
                    ${p.api_key_env ? `
                    <label>API Key (${esc(p.api_key_env)})</label>
                    <input type="password" data-field="api_key" placeholder="sk-...">
                    ` : ''}
                    <label>Modelo default</label>
                    <input type="text" data-field="default_model"
                           value="${esc(p.default_model || '')}"
                           placeholder="${esc((p.models || [])[0] || '')}">
                    <label>Base URL (override)</label>
                    <input type="text" data-field="base_url" value="${esc(p.base_url || '')}"
                           placeholder="${esc(p.base_url || '(default)')}">
                </div>
                <div class="prov-card-actions">
                    <button class="prov-btn" data-action="toggle" data-name="${esc(p.name)}">
                        ${p.enabled ? 'DESACTIVAR' : 'ACTIVAR'}
                    </button>
                    <button class="prov-btn" data-action="edit" data-name="${esc(p.name)}">EDITAR</button>
                    <button class="prov-btn" data-action="test" data-name="${esc(p.name)}">PROBAR</button>
                </div>
            </div>
        `;
    }

    function renderTaskAssignments() {
        return state.taskKinds.map((task) => {
            const current = state.taskAssignment[task] || {};
            const options = state.providers.map((p) => {
                const sel = current.provider === p.name ? 'selected' : '';
                return `<option value="${esc(p.name)}" ${sel}>${esc(p.name)}</option>`;
            }).join('');
            return `
                <div class="prov-task-row">
                    <span class="prov-task-name">${esc(task)}</span>
                    <select data-task="${esc(task)}" data-kind="provider">
                        <option value="">— sin asignar —</option>
                        ${options}
                    </select>
                    <input type="text" placeholder="modelo (opcional)"
                           value="${esc(current.model || '')}"
                           data-task="${esc(task)}" data-kind="model"
                           style="background:#0d1117;color:#e6edf3;border:1px solid #30363d;border-radius:5px;padding:4px 7px;font-size:11.5px;flex:1;">
                </div>
            `;
        }).join('');
    }

    function wireProviderActions() {
        document.querySelectorAll('#prov-modal-body button[data-action]').forEach((btn) => {
            btn.addEventListener('click', async (ev) => {
                ev.preventDefault();
                const action = btn.getAttribute('data-action');
                const name = btn.getAttribute('data-name');

                if (action === 'toggle') {
                    const current = state.providers.find((p) => p.name === name);
                    if (!current) return;
                    btn.disabled = true;
                    await configure(name, { enabled: !current.enabled });
                    await refresh();
                } else if (action === 'edit') {
                    const form = document.querySelector(`[data-form-for="${CSS.escape(name)}"]`);
                    if (form) {
                        form.hidden = !form.hidden;
                        if (!form.hidden) {
                            // Agregar boton GUARDAR si no existe
                            if (!form.querySelector('.prov-btn-save')) {
                                const save = document.createElement('button');
                                save.className = 'prov-btn prov-btn-primary prov-btn-save';
                                save.textContent = 'GUARDAR';
                                save.addEventListener('click', async (e) => {
                                    e.preventDefault();
                                    const body = {};
                                    form.querySelectorAll('[data-field]').forEach((inp) => {
                                        const f = inp.getAttribute('data-field');
                                        const v = inp.value.trim();
                                        if (v) body[f] = v;
                                    });
                                    save.disabled = true;
                                    save.textContent = 'guardando...';
                                    await configure(name, body);
                                    await refresh();
                                });
                                form.appendChild(save);
                            }
                        }
                    }
                } else if (action === 'test') {
                    btn.disabled = true;
                    btn.textContent = 'PROBANDO...';
                    const result = await testProvider(name);
                    btn.disabled = false;
                    btn.textContent = 'PROBAR';
                    const msg = result.ok
                        ? `✓ ${name} OK (${result.latency_ms}ms)`
                        : `✗ ${name}: ${result.error}`;
                    alert(msg);
                    await refresh();
                }
            });
        });
    }

    function wireTaskAssignments() {
        document.querySelectorAll('#prov-modal-body select[data-task]').forEach((sel) => {
            sel.addEventListener('change', async () => {
                const task = sel.getAttribute('data-task');
                const provider = sel.value;
                const modelInp = document.querySelector(
                    `input[data-task="${CSS.escape(task)}"][data-kind="model"]`
                );
                const model = modelInp ? modelInp.value.trim() : null;
                if (!provider) return;
                await assignTask(task, provider, model || null);
            });
        });
        document.querySelectorAll('#prov-modal-body input[data-task][data-kind="model"]').forEach((inp) => {
            inp.addEventListener('blur', async () => {
                const task = inp.getAttribute('data-task');
                const sel = document.querySelector(
                    `select[data-task="${CSS.escape(task)}"][data-kind="provider"]`
                );
                const provider = sel ? sel.value : null;
                if (!provider) return;
                await assignTask(task, provider, inp.value.trim() || null);
            });
        });
    }

    // ── Bootstrap ────────────────────────────────────────────────────────────

    function init() {
        // Mostrar/ocultar FAB segun role
        ensureFab();
        // Si el role cambia (login/logout) actualizar
        window.addEventListener('zephyro:auth-changed', () => {
            if (isAdmin()) ensureFab();
            else removeFab();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Re-check periodico (login puede pasar despues del DOMContentLoaded)
    setInterval(() => {
        if (isAdmin() && !$('prov-fab')) ensureFab();
        else if (!isAdmin() && $('prov-fab')) removeFab();
    }, 2000);
})();
