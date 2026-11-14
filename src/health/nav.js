/* health/nav.js — Sidebar de navegación + footer dinámico
   Expone window.HEALTH_NAV = { show(name), refresh() } */

(function () {
  'use strict';

  const PANELS = ['nutricion', 'ejercicio', 'agua', 'pasos', 'resumen'];
  let _active = 'nutricion';

  function show(name) {
    if (!PANELS.includes(name)) return;
    _active = name;

    // Nav items
    document.querySelectorAll('.hw-nav-item').forEach(function (el) {
      el.classList.toggle('active', el.dataset.panel === name);
    });

    // Panels
    PANELS.forEach(function (p) {
      const el = document.getElementById('hw-panel-' + p);
      if (el) el.style.display = (p === name) ? 'flex' : 'none';
    });

    // Notify each panel to refresh if it has a refresh fn
    const fn = window['HEALTH_PANEL_' + name.toUpperCase()];
    if (fn && typeof fn.onShow === 'function') fn.onShow();
  }

  function buildNav() {
    const nav = document.getElementById('hw-nav');
    if (!nav) return;

    const items = [
      { key: 'nutricion', label: 'Nutrición'  },
      { key: 'ejercicio', label: 'Ejercicio'  },
      { key: 'agua',      label: 'Agua'       },
      { key: 'pasos',     label: 'Pasos'      },
      { key: 'resumen',   label: 'Resumen'    },
    ];

    // Marcador tactical (▸ / ·) lo aporta CSS vía .hw-nav-item::before
    nav.innerHTML = items.map(function (item) {
      return `<button class="hw-nav-item${item.key === _active ? ' active' : ''}"
                data-panel="${item.key}"
                title="${item.label}">
        <span class="hw-nav-label">${item.label}</span>
      </button>`;
    }).join('');

    nav.querySelectorAll('.hw-nav-item').forEach(function (btn) {
      btn.addEventListener('click', function () { show(btn.dataset.panel); });
    });
  }

  async function refreshFooter() {
    const footerRacha = document.getElementById('hw-footer-racha');
    const footerKcal  = document.getElementById('hw-footer-kcal');
    if (!footerRacha && !footerKcal) return;

    try {
      const [statsRes, dailyRes] = await Promise.all([
        window.HEALTH_API.getStats(),
        window.HEALTH_API.getDaily(),
      ]);

      if (statsRes.ok && footerRacha) {
        const streak = (statsRes.data && statsRes.data.streak && statsRes.data.streak.current) || 0;
        footerRacha.textContent = `STREAK · ${streak}d`;
      }

      if (dailyRes.ok && footerKcal) {
        const daily    = dailyRes.data || {};
        const profile  = daily.profile  || {};
        const consumed = daily.totals   || {};
        const target   = (profile.daily_calories || 2000);
        const eaten    = Math.round(consumed.calories || 0);
        const left     = Math.max(0, target - eaten);
        footerKcal.textContent = `KCAL_LEFT · ${left}`;
      }
    } catch (e) {
      // silencioso
    }
  }

  function init() {
    buildNav();

    // Refresh footer cada 60s
    refreshFooter();
    setInterval(refreshFooter, 60000);
  }

  window.HEALTH_NAV = { show, refresh: refreshFooter, init };

  document.addEventListener('DOMContentLoaded', init);
})();
