/* health/panel-fitness.js — Panel de Ejercicio
   Expone window.HEALTH_PANEL_EJERCICIO = { onShow, refresh } */

(function () {
  'use strict';

  const DAYS = ['Lun', 'Mar', 'Mié', 'Jue', 'Vie', 'Sáb', 'Dom'];

  let _stats = null;
  let _plan  = null;

  /* ── Helpers ─────────────────────────────────────────────────── */
  function todayISODate() {
    return new Date().toISOString().split('T')[0];
  }

  function weekDates() {
    // Returns ISO date strings for Mon-Sun of current week
    const now  = new Date();
    const day  = now.getDay(); // 0=Sun
    const diff = (day === 0) ? -6 : 1 - day;
    const mon  = new Date(now);
    mon.setDate(now.getDate() + diff);
    return Array.from({ length: 7 }, function (_, i) {
      const d = new Date(mon);
      d.setDate(mon.getDate() + i);
      return d.toISOString().split('T')[0];
    });
  }

  /* ── Calendar ────────────────────────────────────────────────── */
  function renderCalendar(stats) {
    const cal = document.getElementById('hw-fit-calendar');
    if (!cal) return;

    const sessions  = (stats && stats.sessions) || [];
    const week      = weekDates();
    const today     = todayISODate();

    // Build a map date → status
    const dayMap = {};
    sessions.forEach(function (s) {
      const d = (s.planned_at || '').split('T')[0];
      if (d) dayMap[d] = dayMap[d] || s.status;
    });

    cal.innerHTML = week.map(function (date, i) {
      const status  = dayMap[date];
      let icon = '—';
      let cls  = 'hw-cal-day';
      if (status === 'completed')             { icon = '✓'; cls += ' hw-cal-done'; }
      else if (status === 'missed')           { icon = '✗'; cls += ' hw-cal-miss'; }
      else if (date === today && status === 'planned') { icon = '●'; cls += ' hw-cal-today'; }
      return `<div class="${cls}" title="${date}">
        <span class="hw-cal-label">${DAYS[i]}</span>
        <span class="hw-cal-icon">${icon}</span>
      </div>`;
    }).join('');
  }

  /* ── Streak ──────────────────────────────────────────────────── */
  function renderStreak(stats) {
    const el = document.getElementById('hw-fit-streak');
    if (!el) return;
    const streak = (stats && stats.streak && stats.streak.current) || 0;
    el.innerHTML = `<span class="hw-streak-num">${streak}</span><span class="hw-streak-label">días de racha</span>`;
  }

  /* ── Plan card ───────────────────────────────────────────────── */
  function renderPlanCard(plan) {
    const el = document.getElementById('hw-fit-plan-card');
    if (!el) return;

    if (!plan || !plan.id) {
      el.innerHTML = `<div class="hw-empty">Sin plan activo. Generá uno para empezar.</div>`;
      return;
    }

    const raw  = plan.raw || {};
    const freq = plan.weekly_frequency || 0;
    const ctx  = plan.context || 'home';
    const rat  = plan.rationale || '';

    el.innerHTML = `
      <div class="hw-plan-meta">
        <span class="hw-plan-tag">${ctx}</span>
        <span class="hw-plan-freq">${freq}x / semana</span>
      </div>
      ${rat ? `<p class="hw-plan-rationale">${rat}</p>` : ''}
    `;
  }

  /* ── Upcoming sessions ───────────────────────────────────────── */
  function renderUpcoming(stats) {
    const el = document.getElementById('hw-fit-upcoming');
    if (!el) return;

    const sessions = (stats && stats.sessions) || [];
    const today    = todayISODate();
    const upcoming = sessions
      .filter(function (s) { return s.status === 'planned' && (s.planned_at || '') >= today; })
      .sort(function (a, b) { return (a.planned_at || '').localeCompare(b.planned_at || ''); })
      .slice(0, 3);

    if (!upcoming.length) {
      el.innerHTML = '<div class="hw-empty">Sin sesiones próximas</div>';
      return;
    }

    el.innerHTML = upcoming.map(function (s, idx) {
      const dt  = s.planned_at ? new Date(s.planned_at) : null;
      const ds  = dt ? dt.toLocaleDateString('es-PE', { weekday: 'short', day: 'numeric', month: 'short' }) : '—';
      const ts  = dt ? dt.toLocaleTimeString('es-PE', { hour: '2-digit', minute: '2-digit' }) : '—';
      const isToday = (s.planned_at || '').startsWith(today);

      return `<div class="hw-session-card${isToday ? ' hw-session-today' : ''}">
        <div class="hw-session-info">
          <span class="hw-session-type">${s.session_type || 'Entrenamiento'}</span>
          <span class="hw-session-dt">${ds} · ${ts}</span>
          <span class="hw-session-dur">${s.duration_min || 0} min</span>
        </div>
        ${isToday ? `<button class="hw-btn-small hw-btn-done" data-sid="${s.id}">✓ Hecho</button>` : ''}
      </div>`;
    }).join('');

    // Bind "Marcar como hecho"
    el.querySelectorAll('.hw-btn-done').forEach(function (btn) {
      btn.addEventListener('click', async function () {
        btn.disabled = true;
        btn.textContent = '…';
        const res = await window.HEALTH_API.logSession(btn.dataset.sid, '');
        if (res.ok) {
          btn.textContent = '✓ Listo';
          setTimeout(refresh, 600);
        } else {
          btn.textContent = 'Error';
          btn.disabled = false;
        }
      });
    });
  }

  /* ── Regenerar plan ──────────────────────────────────────────── */
  async function regeneratePlan() {
    const btn = document.getElementById('hw-fit-regen-btn');
    if (btn) { btn.disabled = true; btn.textContent = 'Generando…'; }

    const res = await window.HEALTH_API.generatePlan('home');

    if (btn) { btn.disabled = false; btn.textContent = 'Regenerar plan'; }

    if (res.ok) {
      await refresh();
    } else {
      const msg = document.getElementById('hw-fit-msg');
      if (msg) msg.textContent = 'Error al generar: ' + (res.error || '');
    }
  }

  /* ── Build panel ─────────────────────────────────────────────── */
  function renderPanel() {
    const panel = document.getElementById('hw-panel-ejercicio');
    if (!panel) return;

    panel.innerHTML = `
      <div class="hw-panel-header">
        <h2 class="hw-panel-title">Ejercicio</h2>
        <button class="hw-btn-ghost" id="hw-fit-regen-btn">Regenerar plan</button>
      </div>

      <div id="hw-fit-streak" class="hw-streak-wrap">
        <span class="hw-streak-num">0</span><span class="hw-streak-label">días de racha</span>
      </div>

      <div class="hw-section-title">WEEK_GRID</div>
      <div id="hw-fit-calendar" class="hw-calendar-row"></div>

      <div class="hw-section-title">ACTIVE_PLAN</div>
      <div id="hw-fit-plan-card" class="hw-plan-card">
        <div class="hw-empty">Cargando…</div>
      </div>

      <div class="hw-section-title">UPCOMING_SESSIONS</div>
      <div id="hw-fit-upcoming" class="hw-session-list">
        <div class="hw-empty">Cargando…</div>
      </div>

      <p id="hw-fit-msg" class="hw-msg"></p>
    `;

    document.getElementById('hw-fit-regen-btn').addEventListener('click', regeneratePlan);
  }

  async function refresh() {
    try {
      const [statsRes, planRes] = await Promise.all([
        window.HEALTH_API.getStats(),
        window.HEALTH_API.getCurrentPlan(),
      ]);

      _stats = (statsRes.ok && statsRes.data) || null;
      _plan  = (planRes.ok  && planRes.data)  || null;

      renderCalendar(_stats);
      renderStreak(_stats);
      renderPlanCard(_plan);
      renderUpcoming(_stats);
    } catch (e) {
      console.error('[fitness] refresh error', e);
    }
  }

  function onShow() {
    if (!document.getElementById('hw-fit-calendar')) renderPanel();
    refresh();
  }

  window.HEALTH_PANEL_EJERCICIO = { onShow, refresh };
})();
