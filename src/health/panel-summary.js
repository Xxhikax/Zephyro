/* health/panel-summary.js — Panel de Resumen semanal
   Expone window.HEALTH_PANEL_RESUMEN = { onShow, refresh } */

(function () {
  'use strict';

  const WEEK_LABELS = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom'];

  let _weekly   = null;
  let _trend    = null;

  /* ── Gauge de adherencia ─────────────────────────────────────── */
  function renderAdherence(weekly) {
    const canvas = document.getElementById('hw-sum-gauge');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    const nutritionAdh = (weekly && weekly.avg_adherence_score) || 0;
    const fitnessDone  = (weekly && weekly.sessions_completed) || 0;
    const fitnessPlan  = (weekly && weekly.sessions_planned)   || 1;
    const fitnessAdh   = fitnessDone / Math.max(fitnessPlan, 1);
    const combined     = Math.round((nutritionAdh * 0.5 + fitnessAdh * 0.5) * 100);

    window.HEALTH_CHARTS.drawGauge(ctx, combined, { label: 'adherencia semanal' });

    const el = document.getElementById('hw-sum-adh-detail');
    if (el) {
      el.innerHTML = `
        <span>Nutrición: ${Math.round(nutritionAdh * 100)}%</span>
        <span> · </span>
        <span>Ejercicio: ${Math.round(fitnessAdh * 100)}%</span>
      `;
    }
  }

  /* ── Bar chart de flags de insulina ─────────────────────────── */
  function renderFlagChart(weekly) {
    const canvas = document.getElementById('hw-sum-flags-chart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    const days7 = (weekly && weekly.daily_summaries) || [];
    const values = WEEK_LABELS.map(function (_, i) {
      return (days7[i] && days7[i].insulin_flags_count) || 0;
    });

    window.HEALTH_CHARTS.drawBars(ctx, values, '#ffaa00', {
      labels: WEEK_LABELS,
      maxValue: Math.max(5, Math.max.apply(null, values) || 1),
      barGap: 6,
      barRadius: 3,
    });
  }

  /* ── Sparkline de peso ───────────────────────────────────────── */
  function renderWeightSparkline(trend) {
    const canvas = document.getElementById('hw-sum-weight-spark');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');

    const points = (trend || []).map(function (p) { return p.weight_kg || p[1] || 0; });
    if (points.length < 2) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = 'rgba(91,207,166,0.5)';
      ctx.font = '11px "JetBrains Mono", monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('Sin datos de peso aún', canvas.width / 2, canvas.height / 2);
      return;
    }

    window.HEALTH_CHARTS.drawSparkline(ctx, points, '#00FF88', { fill: true, lineWidth: 2 });

    // Min / max labels
    const min = Math.min.apply(null, points);
    const max = Math.max.apply(null, points);
    const last = points[points.length - 1];
    const el = document.getElementById('hw-sum-weight-label');
    if (el) {
      el.textContent = `Actual: ${last.toFixed(1)} kg  |  Rango: ${min.toFixed(1)}–${max.toFixed(1)} kg`;
    }
  }

  /* ── Recomendaciones ─────────────────────────────────────────── */
  function renderRecommendations(weekly) {
    const el = document.getElementById('hw-sum-recs');
    if (!el) return;

    const recs = (weekly && weekly.recommendations) || [];
    if (!recs.length) {
      el.innerHTML = '<div class="hw-empty">Sin recomendaciones esta semana</div>';
      return;
    }

    el.innerHTML = recs.map(function (r) {
      return `<div class="hw-rec-item">
        <span class="hw-rec-icon">💡</span>
        <span class="hw-rec-text">${r}</span>
      </div>`;
    }).join('');
  }

  /* ── Weight log form ─────────────────────────────────────────── */
  async function logWeight() {
    const input = document.getElementById('hw-sum-weight-input');
    const msg   = document.getElementById('hw-sum-weight-msg');
    if (!input) return;

    const kg = parseFloat(input.value);
    if (isNaN(kg) || kg <= 0) {
      if (msg) msg.textContent = 'Ingresá un peso válido en kg.';
      return;
    }

    const btn = document.getElementById('hw-sum-weight-btn');
    if (btn) { btn.disabled = true; btn.textContent = '…'; }

    const res = await window.HEALTH_API.logWeight(kg);

    if (btn) { btn.disabled = false; btn.textContent = 'Registrar'; }

    if (res.ok) {
      input.value = '';
      if (msg) msg.textContent = '✓ Peso registrado';
      await refresh();
      setTimeout(function () { if (msg) msg.textContent = ''; }, 2000);
    } else {
      if (msg) msg.textContent = 'Error: ' + (res.error || '');
    }
  }

  /* ── Build panel ─────────────────────────────────────────────── */
  function renderPanel() {
    const panel = document.getElementById('hw-panel-resumen');
    if (!panel) return;

    panel.innerHTML = `
      <div class="hw-panel-header">
        <h2 class="hw-panel-title">Resumen semanal</h2>
      </div>

      <div class="hw-sum-gauge-wrap">
        <canvas id="hw-sum-gauge" width="200" height="130" style="max-width:200px"></canvas>
        <div id="hw-sum-adh-detail" class="hw-sum-adh-detail"></div>
      </div>

      <div class="hw-section-title">GLYCEMIC_FLAGS</div>
      <canvas id="hw-sum-flags-chart" width="340" height="100" style="width:100%;max-width:340px"></canvas>

      <div class="hw-section-title">WEIGHT_TREND</div>
      <canvas id="hw-sum-weight-spark" width="340" height="80" style="width:100%;max-width:340px"></canvas>
      <div id="hw-sum-weight-label" class="hw-weight-label"></div>

      <div class="hw-steps-form" style="margin-top:8px">
        <input type="number" id="hw-sum-weight-input" class="hw-input" placeholder="Ej: 96.5 kg" step="0.1" min="30" max="300">
        <button class="hw-btn-ghost" id="hw-sum-weight-btn">Registrar peso</button>
      </div>
      <p id="hw-sum-weight-msg" class="hw-msg"></p>

      <div class="hw-section-title">COACH_INSIGHTS</div>
      <div id="hw-sum-recs" class="hw-rec-list">
        <div class="hw-empty">Cargando…</div>
      </div>
    `;

    document.getElementById('hw-sum-weight-btn').addEventListener('click', logWeight);
    document.getElementById('hw-sum-weight-input').addEventListener('keydown', function (e) {
      if (e.key === 'Enter') logWeight();
    });
  }

  async function refresh() {
    try {
      const [weeklyRes, trendRes] = await Promise.all([
        window.HEALTH_API.getWeekly(),
        window.HEALTH_API.getWeightTrend(),
      ]);

      _weekly = (weeklyRes.ok && weeklyRes.data) || null;
      _trend  = (trendRes.ok  && Array.isArray(trendRes.data)) ? trendRes.data : null;

      renderAdherence(_weekly);
      renderFlagChart(_weekly);
      renderWeightSparkline(_trend);
      renderRecommendations(_weekly);
    } catch (e) {
      console.error('[summary] refresh error', e);
    }
  }

  function onShow() {
    if (!document.getElementById('hw-sum-gauge')) renderPanel();
    refresh();
  }

  window.HEALTH_PANEL_RESUMEN = { onShow, refresh };
})();
