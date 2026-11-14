/* health.js — Bootstrap de la ventana Salud
   IIFE — sin módulos ES. Carga después de todos los sub-módulos. */

(function () {
  'use strict';

  let _pollInterval = null;
  const POLL_MS     = 60000; // 60 s

  /* ── Utilidad para verificar que todas las dependencias cargaron ── */
  function checkDeps() {
    return (
      window.HEALTH_API    &&
      window.HEALTH_NAV    &&
      window.HEALTH_CHARTS
    );
  }

  function initHealth() {
    if (!checkDeps()) {
      console.error('[health] Módulos no disponibles — verificá el orden de scripts en health.html');
      return;
    }

    // Verificar auth — el token debe estar en localStorage (mismo key que auth.js)
    const token = (window.auth && window.auth.getToken())
                || localStorage.getItem('authToken');

    if (!token) {
      showAuthError();
      return;
    }

    // Inicializar navegación — muestra panel inicial
    window.HEALTH_NAV.init();
    window.HEALTH_NAV.show('nutricion');

    // Polling periódico: refresca el panel activo cada 60 s
    startPolling();

    // Botón de refresh manual en header
    const refreshBtn = document.getElementById('hw-header-refresh');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', function () {
        refreshActive();
        window.HEALTH_NAV.refresh();
      });
    }
  }

  function showAuthError() {
    const main = document.getElementById('hw-main-content');
    if (main) {
      main.innerHTML = `
        <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;gap:16px;color:#5BCFA6;font-size:13px">
          <span style="font-size:32px">🔒</span>
          <p>Necesitás estar autenticado para usar el panel de Salud.</p>
          <p style="font-size:11px;opacity:0.6">Abrí Zephyro primero y luego volvé acá desde el tray o el botón Salud.</p>
        </div>`;
    }
  }

  function refreshActive() {
    // Cada panel expone onShow() que también hace refresh
    const panels = ['NUTRICION', 'EJERCICIO', 'AGUA', 'PASOS', 'RESUMEN'];
    panels.forEach(function (name) {
      const p = window['HEALTH_PANEL_' + name];
      if (p && typeof p.refresh === 'function') {
        // Solo refrescar el activo (los no-activos están ocultos, el usuario no los ve)
        const el = document.getElementById('hw-panel-' + name.toLowerCase());
        if (el && el.style.display !== 'none') {
          p.refresh();
        }
      }
    });
  }

  function startPolling() {
    if (_pollInterval) clearInterval(_pollInterval);
    _pollInterval = setInterval(function () {
      refreshActive();
      window.HEALTH_NAV.refresh();
    }, POLL_MS);
  }

  /* ── Punto de entrada ──────────────────────────────────────────── */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initHealth);
  } else {
    initHealth();
  }
})();
