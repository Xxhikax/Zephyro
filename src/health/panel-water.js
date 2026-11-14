/* health/panel-water.js — Panel de Agua
   Expone window.HEALTH_PANEL_AGUA = { onShow, refresh } */

(function () {
  'use strict';

  const TARGET_ML = 3000;

  let _daily = null;

  function renderBottle(totalMl) {
    const svg = document.getElementById('hw-water-bottle');
    if (!svg) return;
    const pct = Math.min(100, (totalMl / TARGET_ML) * 100);
    window.HEALTH_CHARTS.drawBottle(svg, pct);
  }

  function renderStatus(totalMl) {
    const el = document.getElementById('hw-water-status');
    if (!el) return;
    const left = Math.max(0, TARGET_ML - totalMl);
    el.innerHTML = `
      <span class="hw-water-total">${totalMl} ml</span>
      <span class="hw-water-sep">/ ${TARGET_ML} ml</span>
      ${left > 0 ? `<span class="hw-water-left">Faltan ${left} ml</span>` : '<span class="hw-water-ok">Meta alcanzada 🎉</span>'}
    `;
  }

  function renderTimeline(daily) {
    const el = document.getElementById('hw-water-timeline');
    if (!el) return;
    const logs = (daily && daily.water_logs) || [];

    if (!logs.length) {
      el.innerHTML = '<div class="hw-empty">Sin registros de agua hoy</div>';
      return;
    }

    el.innerHTML = logs.map(function (log) {
      const t = log.logged_at ? new Date(log.logged_at).toLocaleTimeString('es-PE', { hour: '2-digit', minute: '2-digit' }) : '—';
      return `<div class="hw-water-log">
        <span class="hw-wlog-time">${t}</span>
        <span class="hw-wlog-ml">+${log.amount_ml} ml</span>
        <span class="hw-wlog-cum">(${log.cumulative_day_ml} ml total)</span>
      </div>`;
    }).join('');
  }

  async function addWater(ml) {
    const btn = document.querySelector(`[data-ml="${ml}"]`);
    if (btn) { btn.disabled = true; btn.textContent = '…'; }

    const res = await window.HEALTH_API.logWater(ml);

    if (btn) { btn.disabled = false; btn.textContent = `+${ml}`; }

    if (res.ok) {
      await refresh();
    } else {
      const msg = document.getElementById('hw-water-msg');
      if (msg) msg.textContent = 'Error: ' + (res.error || '');
    }
  }

  function renderPanel() {
    const panel = document.getElementById('hw-panel-agua');
    if (!panel) return;

    panel.innerHTML = `
      <div class="hw-panel-header">
        <h2 class="hw-panel-title">Agua</h2>
      </div>

      <div class="hw-water-body">
        <div class="hw-water-col-bottle">
          <svg id="hw-water-bottle" xmlns="http://www.w3.org/2000/svg" width="80" height="160"></svg>
          <div id="hw-water-status" class="hw-water-status"></div>
        </div>

        <div class="hw-water-col-right">
          <div class="hw-water-btns">
            <button class="hw-btn-water" data-ml="250">+250</button>
            <button class="hw-btn-water" data-ml="500">+500</button>
            <button class="hw-btn-water" data-ml="750">+750</button>
          </div>

          <div class="hw-section-title">INTAKE_LOG</div>
          <div id="hw-water-timeline" class="hw-water-timeline">
            <div class="hw-empty">Cargando…</div>
          </div>
        </div>
      </div>

      <p id="hw-water-msg" class="hw-msg"></p>
    `;

    panel.querySelectorAll('.hw-btn-water').forEach(function (btn) {
      btn.addEventListener('click', function () {
        addWater(parseInt(btn.dataset.ml, 10));
      });
    });
  }

  async function refresh() {
    try {
      const res = await window.HEALTH_API.getDaily();
      _daily = (res.ok && res.data) || null;

      const totalMl = (_daily && _daily.totals && _daily.totals.water_ml) || 0;
      renderBottle(totalMl);
      renderStatus(totalMl);
      renderTimeline(_daily);
    } catch (e) {
      console.error('[water] refresh error', e);
    }
  }

  function onShow() {
    if (!document.getElementById('hw-water-bottle')) renderPanel();
    refresh();
  }

  window.HEALTH_PANEL_AGUA = { onShow, refresh };
})();
