/* api-window.js — Ventana de gestión de APIs (uso, costo, latencia, keys).
   Fetch hacia /api/providers/*. Token leído desde localStorage 'authToken'
   (compartido entre ventanas Tauri del mismo origin, igual que la ventana Salud). */

(function () {
  'use strict';

  const API_BASE = 'http://localhost:8000';

  /* ── State ───────────────────────────────────────────────────────── */
  let _period = 'today';
  let _active = 'providers';
  let _providers = [];
  let _stats = {};
  let _totals = { requests: 0, cost_usd: 0 };

  const PERIODS = [
    { key: 'today', label: 'Hoy'    },
    { key: 'week',  label: 'Semana' },
    { key: 'month', label: 'Mes'    },
    { key: 'all',   label: 'Total'  },
  ];

  /* ── Helpers ─────────────────────────────────────────────────────── */
  function getToken() {
    return (window.auth && window.auth.getToken && window.auth.getToken())
      || localStorage.getItem('authToken')
      || '';
  }

  function esc(s) {
    const d = document.createElement('span');
    d.textContent = String(s == null ? '' : s);
    return d.innerHTML;
  }

  async function req(method, path, body) {
    try {
      const t = getToken();
      // Los endpoints /providers usan require_admin → token vía header
      // (Authorization: Bearer / X-Token), no query param.
      const headers = {};
      if (t) {
        headers['Authorization'] = `Bearer ${t}`;
        headers['X-Token'] = t;
      }
      const opts = { method, headers };
      if (body) {
        headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
      }
      const r = await fetch(`${API_BASE}${path}`, opts);
      const data = await r.json().catch(() => ({}));
      if (!r.ok) return { ok: false, data: null, error: data.detail || `HTTP ${r.status}` };
      return { ok: true, data, error: null };
    } catch (e) {
      return { ok: false, data: null, error: e.message };
    }
  }

  function fmtTokens(n) {
    n = n || 0;
    if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
    if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';
    return String(n);
  }

  function fmtCost(usd) {
    usd = usd || 0;
    if (usd === 0) return '$0';
    if (usd < 0.01) return '$' + usd.toFixed(4);
    return '$' + usd.toFixed(2);
  }

  function fmtAgo(ts) {
    if (!ts) return '—';
    const diff = Date.now() / 1000 - ts;
    if (diff < 60) return 'hace ' + Math.floor(diff) + 's';
    if (diff < 3600) return 'hace ' + Math.floor(diff / 60) + 'min';
    if (diff < 86400) return 'hace ' + Math.floor(diff / 3600) + 'h';
    return 'hace ' + Math.floor(diff / 86400) + 'd';
  }

  /* ── API calls ───────────────────────────────────────────────────── */
  async function fetchProviders() { return req('GET', '/api/providers'); }
  async function fetchUsage(period) { return req('GET', `/api/providers/usage?period=${period}`); }
  async function fetchLog() { return req('GET', '/api/providers/usage/log?limit=80'); }
  async function testProvider(name) { return req('POST', `/api/providers/${name}/test`, {}); }
  async function configureProvider(name, apiKey) {
    return req('POST', `/api/providers/${name}/configure`, { api_key: apiKey });
  }

  /* ── Stats merge ─────────────────────────────────────────────────── */
  function statsFor(providerName) {
    // usage store registra cloud con el mismo name; "ollama_local" → "local"
    if (_stats[providerName]) return _stats[providerName];
    if (providerName === 'ollama_local' && _stats['local']) return _stats['local'];
    return null;
  }

  /* ── Nav ─────────────────────────────────────────────────────────── */
  function buildNav() {
    const nav = document.getElementById('aw-nav');
    if (!nav) return;
    const items = [
      { key: 'providers', label: 'Providers' },
      { key: 'activity',  label: 'Actividad' },
    ];
    nav.innerHTML = items.map(function (it) {
      return `<button class="aw-nav-item${it.key === _active ? ' active' : ''}" data-panel="${it.key}">
        <span class="aw-nav-label">${it.label}</span>
      </button>`;
    }).join('');
    nav.querySelectorAll('.aw-nav-item').forEach(function (btn) {
      btn.addEventListener('click', function () { show(btn.dataset.panel); });
    });
  }

  function show(name) {
    _active = name;
    document.querySelectorAll('.aw-nav-item').forEach(function (el) {
      el.classList.toggle('active', el.dataset.panel === name);
    });
    ['providers', 'activity'].forEach(function (p) {
      const el = document.getElementById('aw-panel-' + p);
      if (el) el.style.display = (p === name) ? 'flex' : 'none';
    });
    if (name === 'providers') renderProviders();
    else renderActivity();
  }

  /* ── Footer ──────────────────────────────────────────────────────── */
  function renderFooter() {
    const c = document.getElementById('aw-footer-cost');
    const r = document.getElementById('aw-footer-reqs');
    if (c) c.textContent = `COST · ${fmtCost(_totals.cost_usd)}`;
    if (r) r.textContent = `REQS · ${_totals.requests || 0}`;
  }

  /* ── Providers panel ─────────────────────────────────────────────── */
  function renderProviders() {
    const panel = document.getElementById('aw-panel-providers');
    if (!panel) return;

    const periodBtns = PERIODS.map(function (p) {
      return `<button class="aw-period-btn${p.key === _period ? ' active' : ''}" data-period="${p.key}">${p.label}</button>`;
    }).join('');

    const cloudOrder = ['anthropic', 'deepseek', 'openai', 'gemini', 'openrouter', 'claude_cli', 'ollama_local'];
    const sorted = _providers.slice().sort(function (a, b) {
      const ia = cloudOrder.indexOf(a.name); const ib = cloudOrder.indexOf(b.name);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });

    const cards = sorted.map(renderCard).join('');

    panel.innerHTML = `
      <div class="aw-panel-header">
        <h2 class="aw-panel-title">Providers</h2>
        <div class="aw-period-row">${periodBtns}</div>
      </div>
      <div class="aw-section-title">USAGE · ${esc(_period.toUpperCase())}</div>
      <div class="aw-card-grid">${cards || '<div class="aw-empty">Sin providers configurados</div>'}</div>
    `;

    panel.querySelectorAll('.aw-period-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        _period = btn.dataset.period;
        refresh();
      });
    });

    bindCardActions(panel);
  }

  function renderCard(p) {
    const st = statsFor(p.name) || {};
    const keyOk = p.api_key_set || p.api_key_env == null;  // claude_cli/ollama no usan key
    const dotCls = keyOk ? 'aw-key-ok' : 'aw-key-none';
    const keyLabel = p.api_key_env == null
      ? 'sin key'
      : (p.api_key_set ? (p.api_key_hint || 'key ok') : 'sin key');
    const idle = (st.requests || 0) === 0;

    const lastTest = p.last_test_at
      ? `test ${p.last_test_ok ? '✓' : '✗'} ${fmtAgo(p.last_test_at)}`
      : 'sin test';

    return `
      <div class="aw-prov-card${idle ? ' aw-prov-idle' : ''}" data-prov="${esc(p.name)}">
        <div class="aw-prov-head">
          <span class="aw-key-dot ${dotCls}"></span>
          <span class="aw-prov-name">${esc(p.name)}</span>
          <span class="aw-key-label">${esc(keyLabel)}</span>
        </div>
        <div class="aw-metrics">
          <div class="aw-metric">
            <span class="aw-metric-val">${st.requests || 0}</span>
            <span class="aw-metric-label">Requests</span>
          </div>
          <div class="aw-metric">
            <span class="aw-metric-val">${fmtCost(st.cost_usd)}</span>
            <span class="aw-metric-label">Costo est.</span>
          </div>
          <div class="aw-metric">
            <span class="aw-metric-val aw-muted">${fmtTokens(st.tokens_in)} / ${fmtTokens(st.tokens_out)}</span>
            <span class="aw-metric-label">Tokens in / out</span>
          </div>
          <div class="aw-metric">
            <span class="aw-metric-val aw-muted">${st.avg_latency_ms || 0}ms</span>
            <span class="aw-metric-label">Latencia media</span>
          </div>
        </div>
        <div class="aw-prov-meta">
          <span>${esc(p.default_model || '—')}</span>
          <span>${esc(lastTest)}</span>
          ${st.errors ? `<span class="aw-prov-err">${st.errors} err</span>` : ''}
        </div>
        ${p.last_test_error && !p.last_test_ok ? `<div class="aw-prov-err">${esc(p.last_test_error.slice(0, 120))}</div>` : ''}
        <div class="aw-prov-actions">
          <button class="aw-btn-ghost aw-test-btn" data-prov="${esc(p.name)}">Test</button>
          ${p.api_key_env != null ? `<button class="aw-btn-ghost aw-editkey-btn" data-prov="${esc(p.name)}">✎ Editar key</button>` : ''}
        </div>
        <div class="aw-key-slot" data-prov="${esc(p.name)}"></div>
      </div>
    `;
  }

  function bindCardActions(root) {
    root.querySelectorAll('.aw-test-btn').forEach(function (btn) {
      btn.addEventListener('click', async function () {
        const name = btn.dataset.prov;
        btn.disabled = true;
        const orig = btn.textContent;
        btn.textContent = 'Probando…';
        const res = await testProvider(name);
        if (res.ok && res.data) {
          btn.textContent = res.data.ok ? `✓ ${res.data.latency_ms}ms` : '✗ Falló';
        } else {
          btn.textContent = '✗ Error';
        }
        setTimeout(function () { btn.disabled = false; btn.textContent = orig; refresh(); }, 1800);
      });
    });

    root.querySelectorAll('.aw-editkey-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        const name = btn.dataset.prov;
        const slot = root.querySelector(`.aw-key-slot[data-prov="${CSS.escape(name)}"]`);
        if (!slot) return;
        if (slot.querySelector('.aw-key-form')) { slot.innerHTML = ''; return; }
        slot.innerHTML = `
          <div class="aw-key-form">
            <input type="password" class="aw-input aw-key-input" placeholder="Pegá la nueva API key…" autocomplete="off">
            <div class="aw-key-form-actions">
              <button class="aw-btn-primary aw-key-save">Guardar</button>
              <button class="aw-btn-ghost aw-key-cancel">Cancelar</button>
            </div>
            <div class="aw-form-msg"></div>
          </div>`;
        const input = slot.querySelector('.aw-key-input');
        const msg = slot.querySelector('.aw-form-msg');
        input.focus();
        slot.querySelector('.aw-key-cancel').addEventListener('click', function () { slot.innerHTML = ''; });
        slot.querySelector('.aw-key-save').addEventListener('click', async function () {
          const val = input.value.trim();
          if (!val) { msg.textContent = 'La key no puede estar vacía.'; return; }
          msg.textContent = 'Guardando…';
          const res = await configureProvider(name, val);
          if (res.ok) {
            msg.textContent = '✓ Key actualizada';
            setTimeout(function () { slot.innerHTML = ''; refresh(); }, 900);
          } else {
            msg.textContent = 'Error: ' + (res.error || 'no se pudo guardar');
          }
        });
      });
    });
  }

  /* ── Activity panel ──────────────────────────────────────────────── */
  async function renderActivity() {
    const panel = document.getElementById('aw-panel-activity');
    if (!panel) return;
    panel.innerHTML = `
      <div class="aw-panel-header"><h2 class="aw-panel-title">Actividad</h2></div>
      <div class="aw-section-title">RECENT_CALLS</div>
      <div id="aw-log-body"><div class="aw-empty">Cargando registro…</div></div>
    `;
    const res = await fetchLog();
    const body = document.getElementById('aw-log-body');
    if (!body) return;
    const entries = (res.ok && res.data && res.data.entries) || [];
    if (!entries.length) {
      body.innerHTML = '<div class="aw-empty">Sin llamadas registradas todavía</div>';
      return;
    }
    const rows = entries.map(function (e) {
      const t = new Date(e.ts * 1000).toLocaleTimeString('es-PE', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      return `<div class="aw-log-row${e.success ? '' : ' aw-log-err'}">
        <span class="aw-log-time">${esc(t)}</span>
        <span class="aw-log-prov">${esc(e.provider)}</span>
        <span class="aw-log-model" title="${esc(e.model || '')}">${esc(e.model || '—')}</span>
        <span class="aw-log-tok">${fmtTokens(e.tokens_in)}/${fmtTokens(e.tokens_out)}</span>
        <span class="aw-log-cost">${fmtCost(e.cost_usd)}</span>
        <span class="aw-log-lat">${e.latency_ms || 0}ms</span>
      </div>`;
    }).join('');
    body.innerHTML = `
      <div class="aw-log-list">
        <div class="aw-log-head">
          <span>Hora</span><span>Provider</span><span>Modelo</span>
          <span>Tok i/o</span><span>Costo</span><span>Lat</span>
        </div>
        ${rows}
      </div>`;
  }

  /* ── Refresh ─────────────────────────────────────────────────────── */
  async function refresh() {
    const [provRes, usageRes] = await Promise.all([fetchProviders(), fetchUsage(_period)]);
    _providers = (provRes.ok && provRes.data && provRes.data.providers) || [];
    if (usageRes.ok && usageRes.data) {
      _stats = usageRes.data.stats || {};
      _totals = usageRes.data.totals || { requests: 0, cost_usd: 0 };
    } else {
      _stats = {}; _totals = { requests: 0, cost_usd: 0 };
    }
    renderFooter();
    if (_active === 'providers') renderProviders();
    else renderActivity();
  }

  /* ── Init ────────────────────────────────────────────────────────── */
  function init() {
    buildNav();
    const rb = document.getElementById('aw-refresh');
    if (rb) rb.addEventListener('click', refresh);
    refresh();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
