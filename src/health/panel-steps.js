/* health/panel-steps.js — Panel de Pasos
   Expone window.HEALTH_PANEL_PASOS = { onShow, refresh } */

(function () {
  'use strict';

  const TARGET_STEPS = 8000;
  let _weekly = null;

  const WEEK_LABELS = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom'];

  function renderStepCount(daily) {
    const el = document.getElementById('hw-steps-count');
    if (!el) return;
    const logs    = (daily && daily.step_logs) || [];
    const todayS  = new Date().toISOString().split('T')[0];
    const todayLog = logs.find(function (l) { return l.log_date === todayS; });
    const steps   = (todayLog && todayLog.steps) || 0;
    const bonus   = (todayLog && todayLog.bonus_kcal) || 0;
    const pct     = Math.min(100, Math.round(steps / TARGET_STEPS * 100));

    el.innerHTML = `
      <div class="hw-steps-big">${steps.toLocaleString('es-PE')}</div>
      <div class="hw-steps-target">/ ${TARGET_STEPS.toLocaleString('es-PE')} pasos (${pct}%)</div>
      ${bonus > 0
        ? `<div class="hw-steps-bonus">+${bonus} kcal bonus por actividad extra</div>`
        : `<div class="hw-steps-bonus hw-steps-bonus-dim">Sin bonus kcal aún (≤ ${TARGET_STEPS} pasos)</div>`}
    `;
  }

  function renderBarChart(weekly) {
    const canvas = document.getElementById('hw-steps-chart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    const days  = (weekly && weekly.step_logs_7d) || [];
    // Normalize to 7 slots Mon-Sun
    const values = WEEK_LABELS.map(function (_, i) {
      return (days[i] && days[i].steps) || 0;
    });

    window.HEALTH_CHARTS.drawBars(ctx, values, '#00FF88', {
      labels: WEEK_LABELS,
      maxValue: Math.max(TARGET_STEPS, Math.max.apply(null, values) || 1),
      barGap: 6,
      barRadius: 4,
    });
  }

  async function logStepsManual() {
    const input = document.getElementById('hw-steps-input');
    const msg   = document.getElementById('hw-steps-msg');
    if (!input) return;

    const n = parseInt(input.value.trim(), 10);
    if (isNaN(n) || n <= 0) {
      if (msg) msg.textContent = 'Ingresá un número válido.';
      return;
    }

    const btn = document.getElementById('hw-steps-log-btn');
    if (btn) { btn.disabled = true; btn.textContent = '…'; }

    const res = await window.HEALTH_API.logSteps(n);

    if (btn) { btn.disabled = false; btn.textContent = 'Registrar'; }

    if (res.ok) {
      input.value = '';
      if (msg) msg.textContent = '✓ Pasos registrados';
      await refresh();
      setTimeout(function () { if (msg) msg.textContent = ''; }, 2000);
    } else {
      if (msg) msg.textContent = 'Error: ' + (res.error || '');
    }
  }

  function renderPanel() {
    const panel = document.getElementById('hw-panel-pasos');
    if (!panel) return;

    panel.innerHTML = `
      <div class="hw-panel-header">
        <h2 class="hw-panel-title">Pasos</h2>
      </div>

      <div id="hw-steps-count" class="hw-steps-hero">
        <div class="hw-steps-big">—</div>
        <div class="hw-steps-target">Cargando…</div>
      </div>

      <div class="hw-section-title">WEEK_GRID</div>
      <canvas id="hw-steps-chart" width="340" height="120" style="width:100%;max-width:340px"></canvas>

      <div class="hw-section-title">MANUAL_ENTRY</div>
      <div class="hw-steps-form">
        <input type="number" id="hw-steps-input" class="hw-input" placeholder="Ej: 3500" min="1" max="99999">
        <button class="hw-btn-primary" id="hw-steps-log-btn">Registrar</button>
      </div>
      <p id="hw-steps-msg" class="hw-msg"></p>

      <div class="hw-section-title">HEALTH_CONNECT</div>
      <div class="hw-health-connect-placeholder">
        <span class="hw-hc-icon">📱</span>
        <div>
          <div class="hw-hc-title">Sincronización automática (próximamente)</div>
          <div class="hw-hc-desc">Conectá Health Connect o Samsung Health para sincronizar pasos automáticamente desde tu teléfono.</div>
        </div>
        <button class="hw-btn-ghost" disabled>Conectar</button>
      </div>
    `;

    document.getElementById('hw-steps-log-btn').addEventListener('click', logStepsManual);
    document.getElementById('hw-steps-input').addEventListener('keydown', function (e) {
      if (e.key === 'Enter') logStepsManual();
    });
  }

  async function refresh() {
    try {
      const [dailyRes, weeklyRes] = await Promise.all([
        window.HEALTH_API.getDaily(),
        window.HEALTH_API.getWeekly(),
      ]);

      const daily  = (dailyRes.ok  && dailyRes.data)  || null;
      _weekly      = (weeklyRes.ok && weeklyRes.data)  || null;

      renderStepCount(daily);
      renderBarChart(_weekly);
    } catch (e) {
      console.error('[steps] refresh error', e);
    }
  }

  function onShow() {
    if (!document.getElementById('hw-steps-chart')) renderPanel();
    refresh();
  }

  window.HEALTH_PANEL_PASOS = { onShow, refresh };
})();
