/**
 * skill-drafts.js — UI admin para revisar drafts de skills auto-generadas
 * (Hermes-style learning, Parte B).
 *
 * FAB "DRAFTS (N)" si hay drafts pendientes. Modal muestra cada draft con:
 *   - SKILL.md body (descripcion + triggers)
 *   - skill.py (codigo, syntax-highlighted simple)
 *   - Grafo de uses_tools (cruce A<->B: muestra que herramientas invoca)
 *   - Risk level pill
 *   - source_envelope_ids (request_ids que originaron el draft)
 *   - Botones APROBAR / RECHAZAR
 *
 * Solo admin.
 */
(function () {
    'use strict';

    const API_BASE = (typeof window.API_BASE === 'string' && window.API_BASE) || '';

    const state = {
        drafts: [],
        loaded: false,
        pollIntervalId: null,
    };

    function getToken() {
        try { return (window.auth && window.auth.getToken()) || ''; }
        catch (_) { return ''; }
    }

    function isAdmin() {
        try { return (window.auth && window.auth.getRole()) === 'admin'; }
        catch (_) { return false; }
    }

    function $(id) { return document.getElementById(id); }
    function esc(s) {
        const d = document.createElement('span');
        d.textContent = String(s == null ? '' : s);
        return d.innerHTML;
    }

    // ── Styles ───────────────────────────────────────────────────────────────

    function ensureStyles() {
        if ($('drafts-styles')) return;
        const style = document.createElement('style');
        style.id = 'drafts-styles';
        style.textContent = `
            .drafts-modal-backdrop {
                position: fixed; inset: 0; background: rgba(0,0,0,.55);
                z-index: 9000; display: flex; align-items: center; justify-content: center;
            }
            .drafts-modal {
                background: #0d1117; color: #e6edf3;
                border: 1px solid #30363d; border-radius: 8px;
                width: min(920px, 95vw); max-height: 90vh;
                display: flex; flex-direction: column;
                box-shadow: 0 20px 60px rgba(0,0,0,.5);
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
            }
            .drafts-modal-head {
                display: flex; align-items: center; justify-content: space-between;
                padding: 14px 18px; border-bottom: 1px solid #21262d;
            }
            .drafts-modal-head h2 {
                margin: 0; font-size: 14px; letter-spacing: .08em; color: #f0883e;
            }
            .drafts-modal-body {
                padding: 16px 18px; overflow-y: auto; flex: 1;
            }
            .drafts-empty {
                color: #8b949e; text-align: center; padding: 40px 20px;
                font-size: 13px;
            }
            .draft-card {
                background: #161b22; border: 1px solid #21262d; border-radius: 6px;
                padding: 14px 16px; margin-bottom: 14px;
            }
            .draft-head {
                display: flex; align-items: center; justify-content: space-between;
                margin-bottom: 6px; gap: 10px;
            }
            .draft-name {
                font-size: 14px; font-weight: 600; color: #e6edf3;
                font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
            }
            .draft-pill {
                display: inline-block; padding: 2px 8px; border-radius: 10px;
                font-size: 10.5px; letter-spacing: .04em; margin-left: 4px;
            }
            .draft-pill.risk-low { background: rgba(46,160,67,.15); color: #56d364; border: 1px solid #2ea043; }
            .draft-pill.risk-medium { background: rgba(240,136,62,.15); color: #f0883e; border: 1px solid #f0883e; }
            .draft-pill.risk-high { background: rgba(207,34,34,.18); color: #ff7b72; border: 1px solid #cf2222; }
            .draft-desc {
                color: #c9d1d9; font-size: 12.5px; margin-bottom: 10px;
            }
            .draft-section { margin: 10px 0; }
            .draft-section-title {
                font-size: 11px; letter-spacing: .08em; color: #8b949e;
                font-weight: 600; margin-bottom: 4px;
            }
            .draft-triggers {
                display: flex; flex-wrap: wrap; gap: 4px;
            }
            .draft-trigger {
                background: #0d1117; border: 1px solid #21262d;
                padding: 2px 7px; border-radius: 4px; font-size: 11px;
                font-family: ui-monospace, monospace; color: #79c0ff;
            }
            .draft-tools {
                display: flex; flex-wrap: wrap; gap: 4px;
            }
            .draft-tool {
                background: rgba(121,192,255,.10);
                border: 1px solid #1f6feb;
                padding: 2px 8px; border-radius: 10px;
                font-size: 11px; color: #79c0ff;
                font-family: ui-monospace, monospace;
            }
            .draft-tool.write { border-color: #f0883e; color: #f0883e; }
            .draft-tool.destructive { border-color: #cf2222; color: #ff7b72; }
            .draft-code {
                background: #010409; border: 1px solid #21262d; border-radius: 5px;
                padding: 10px 12px; font-family: ui-monospace, "Cascadia Code", Consolas, monospace;
                font-size: 11.5px; color: #e6edf3; overflow-x: auto;
                max-height: 280px; overflow-y: auto; white-space: pre;
                line-height: 1.45;
            }
            .draft-meta {
                font-size: 11px; color: #8b949e; margin-top: 6px;
                font-family: ui-monospace, monospace;
            }
            .draft-actions {
                display: flex; gap: 8px; justify-content: flex-end; margin-top: 12px;
            }
            .draft-btn {
                background: transparent; color: #c9d1d9; border: 1px solid #30363d;
                padding: 6px 12px; border-radius: 5px; cursor: pointer;
                font-size: 12px; letter-spacing: .04em;
            }
            .draft-btn:hover { border-color: #6e7681; }
            .draft-btn-approve { background: #238636; border-color: #2ea043; color: #fff; }
            .draft-btn-approve:hover { background: #2ea043; }
            .draft-btn-reject { background: transparent; border-color: #cf2222; color: #ff7b72; }
            .draft-btn-reject:hover { background: rgba(207,34,34,.15); }
            .draft-modal-close {
                background: transparent; color: #8b949e;
                border: 1px solid #30363d; padding: 5px 10px; border-radius: 5px;
                cursor: pointer; font-size: 11.5px;
            }
            .draft-modal-close:hover { color: #f97583; border-color: #cf2222; }
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
        const btn = $('drafts-fab');
        if (!btn) return;
        if (!btn._draftsWired) {
            btn.addEventListener('click', async () => { openModal(); await refresh(); });
            btn._draftsWired = true;
        }
        updateFabBadge();
    }

    function updateFabBadge() {
        const fab = $('drafts-fab');
        const label = $('drafts-fab-label');
        if (!fab || !label) return;
        const n = state.drafts.length;
        label.textContent = n > 0 ? `DRAFTS (${n})` : 'DRAFTS';
        fab.classList.toggle('has-pending', n > 0);
    }

    function removeFab() {
        const sec = document.getElementById('admin-tools-section');
        if (sec) sec.style.display = 'none';
    }

    // ── Data ─────────────────────────────────────────────────────────────────

    async function refresh() {
        const tok = getToken();
        if (!tok) return;
        try {
            const res = await fetch(`${API_BASE}/api/skills/drafts`, {
                headers: { 'Authorization': `Bearer ${tok}` },
            });
            if (!res.ok) {
                console.warn('[drafts] GET fallo:', res.status);
                return;
            }
            const data = await res.json();
            state.drafts = data.drafts || [];
            state.loaded = true;
            updateFabBadge();
            if ($('drafts-modal-body')) renderModalBody();
        } catch (e) {
            console.error('[drafts] fetch error:', e);
        }
    }

    async function approve(slug) {
        const tok = getToken();
        const res = await fetch(
            `${API_BASE}/api/skills/drafts/${encodeURIComponent(slug)}/approve`,
            { method: 'POST', headers: { 'Authorization': `Bearer ${tok}` } },
        );
        if (!res.ok) {
            const txt = await res.text().catch(() => '');
            alert(`Error aprobando: ${res.status}\n${txt}`);
            return false;
        }
        return true;
    }

    async function reject(slug) {
        const tok = getToken();
        const res = await fetch(
            `${API_BASE}/api/skills/drafts/${encodeURIComponent(slug)}/reject`,
            { method: 'POST', headers: { 'Authorization': `Bearer ${tok}` } },
        );
        if (!res.ok) {
            alert(`Error rechazando: ${res.status}`);
            return false;
        }
        return true;
    }

    // ── Modal ────────────────────────────────────────────────────────────────

    function openModal() {
        if ($('drafts-modal-backdrop')) return;
        const backdrop = document.createElement('div');
        backdrop.className = 'drafts-modal-backdrop';
        backdrop.id = 'drafts-modal-backdrop';
        backdrop.innerHTML = `
            <div class="drafts-modal" role="dialog" aria-labelledby="drafts-modal-title">
                <div class="drafts-modal-head">
                    <h2 id="drafts-modal-title">SKILL DRAFTS · auto-generadas</h2>
                    <button class="draft-modal-close" id="drafts-modal-close">CERRAR</button>
                </div>
                <div class="drafts-modal-body" id="drafts-modal-body">
                    <p style="color:#8b949e">Cargando...</p>
                </div>
            </div>
        `;
        document.body.appendChild(backdrop);
        backdrop.addEventListener('click', (e) => {
            if (e.target === backdrop) closeModal();
        });
        $('drafts-modal-close').addEventListener('click', closeModal);
    }

    function closeModal() {
        const bd = $('drafts-modal-backdrop');
        if (bd) bd.remove();
    }

    function renderModalBody() {
        const body = $('drafts-modal-body');
        if (!body) return;

        if (state.drafts.length === 0) {
            body.innerHTML = `
                <div class="drafts-empty">
                    No hay drafts pendientes.<br>
                    Cuando ejecutes tareas complejas y exitosas, Zephyro propondrá skills aquí.
                </div>
            `;
            return;
        }

        body.innerHTML = state.drafts.map(renderDraftCard).join('');
        wireActions();
    }

    function renderDraftCard(d) {
        const risk = d.risk_level || 'low';
        const triggers = (d.triggers || []).map(
            (t) => `<span class="draft-trigger">${esc(t)}</span>`
        ).join('');
        const tools = (d.uses_tools || []).map((t) => {
            let cls = '';
            if (t.includes('write')) cls = 'write';
            else if (t.includes('destructive')) cls = 'destructive';
            return `<span class="draft-tool ${cls}">${esc(t)}</span>`;
        }).join('') || '<em style="color:#8b949e">(ninguna declarada)</em>';
        const sources = (d.source_envelope_ids || []).map(esc).join(', ');
        const lintLine = d.lint_summary
            ? `<div class="draft-meta">linter: ${esc(d.lint_summary)}</div>`
            : '';

        return `
            <div class="draft-card" data-slug="${esc(d.slug)}">
                <div class="draft-head">
                    <div>
                        <span class="draft-name">${esc(d.name)}</span>
                        <span class="draft-pill risk-${esc(risk)}">RISK: ${esc(risk.toUpperCase())}</span>
                    </div>
                    <div class="draft-actions">
                        <button class="draft-btn draft-btn-reject" data-action="reject"
                                data-slug="${esc(d.slug)}">RECHAZAR</button>
                        <button class="draft-btn draft-btn-approve" data-action="approve"
                                data-slug="${esc(d.slug)}">APROBAR</button>
                    </div>
                </div>
                <div class="draft-desc">${esc(d.description || '_(sin descripción)_')}</div>

                <div class="draft-section">
                    <div class="draft-section-title">TRIGGERS</div>
                    <div class="draft-triggers">${triggers || '<em style="color:#8b949e">(ninguno)</em>'}</div>
                </div>

                <div class="draft-section">
                    <div class="draft-section-title">USES TOOLS · grafo de capacidades</div>
                    <div class="draft-tools">${tools}</div>
                </div>

                <div class="draft-section">
                    <div class="draft-section-title">SKILL.PY</div>
                    <pre class="draft-code">${esc(d.skill_py || '')}</pre>
                </div>

                <div class="draft-section">
                    <div class="draft-section-title">SKILL.MD BODY</div>
                    <pre class="draft-code">${esc(d.skill_md_body || '')}</pre>
                </div>

                <div class="draft-meta">origen: ${sources || '<em>—</em>'}</div>
                ${lintLine}
            </div>
        `;
    }

    function wireActions() {
        document.querySelectorAll('#drafts-modal-body button[data-action]').forEach((btn) => {
            btn.addEventListener('click', async () => {
                const action = btn.getAttribute('data-action');
                const slug = btn.getAttribute('data-slug');
                btn.disabled = true;
                if (action === 'approve') {
                    btn.textContent = 'aprobando...';
                    const ok = await approve(slug);
                    if (ok) {
                        await refresh();
                    } else {
                        btn.disabled = false;
                        btn.textContent = 'APROBAR';
                    }
                } else if (action === 'reject') {
                    if (!confirm(`¿Rechazar draft '${slug}'?`)) {
                        btn.disabled = false;
                        return;
                    }
                    btn.textContent = 'rechazando...';
                    const ok = await reject(slug);
                    if (ok) {
                        await refresh();
                    } else {
                        btn.disabled = false;
                        btn.textContent = 'RECHAZAR';
                    }
                }
            });
        });
    }

    // ── Bootstrap ────────────────────────────────────────────────────────────

    function init() {
        ensureFab();
        if (isAdmin()) refresh();
        window.addEventListener('zephyro:auth-changed', () => {
            if (isAdmin()) { ensureFab(); refresh(); }
            else removeFab();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    // Polling cada 60s (drafts pueden aparecer en background via scheduler)
    setInterval(() => {
        if (isAdmin()) {
            if (!$('drafts-fab')) ensureFab();
            refresh();
        } else {
            removeFab();
        }
    }, 60000);
})();
