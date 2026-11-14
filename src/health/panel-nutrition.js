/* health/panel-nutrition.js — Panel de Nutrición
   Expone window.HEALTH_PANEL_NUTRICION = { onShow, refresh } */

(function () {
  'use strict';

  const MACROS = [
    { key: 'calories',  label: 'Kcal',    color: '#00FF88', targetKey: 'daily_calories'  },
    { key: 'protein_g', label: 'Proteína', color: '#19FFA2', targetKey: 'daily_protein_g' },
    { key: 'carbs_g',   label: 'Carbos',   color: '#ffaa00', targetKey: 'daily_carbs_g'   },
    { key: 'fats_g',    label: 'Grasas',   color: '#ff9944', targetKey: 'daily_fats_g'    },
    { key: 'fiber_g',   label: 'Fibra',    color: '#44aaff', targetKey: 'daily_fiber_g'   },
  ];

  const INSULIN_NUDGES = {
    'arroz blanco':   'El arroz blanco eleva la insulina rápidamente. Considerá cambiarlo por quinua o arroz integral en porción controlada.',
    'pan blanco':     'El pan blanco tiene alto índice glucémico. Una alternativa: pan integral o de masa madre.',
    'papa':           'La papa hervida eleva la insulina. Servida fría o con vinagre baja su índice glucémico.',
    'azúcar':         'La azúcar añadida dispara picos de insulina. Probá con stevia o canela para endulzar.',
    'fideos blancos': 'Los fideos blancos tienen alto IG. Cambialos por pasta integral al dente (IG más bajo).',
    'gaseosa':        'Las gaseosas tienen azúcar libre — van directo al torrente. Reemplazá por agua con gas o infusión.',
    'jugo':           'Los jugos concentran el azúcar sin la fibra de la fruta. Mejor comer la fruta entera.',
    'galleta':        'Las galletas industriales combinan azúcar + grasa + harina refinada. Optá por nueces o fruta.',
  };

  function getDefaultNudge(item) {
    const lower = (item || '').toLowerCase();
    for (const key of Object.keys(INSULIN_NUDGES)) {
      if (lower.includes(key)) return INSULIN_NUDGES[key];
    }
    return 'Este alimento puede elevar la insulina rápidamente. Considerá una alternativa de menor índice glucémico.';
  }

  let _currentDate = new Date().toISOString().split('T')[0];
  let _profile = {};
  let _daily   = {};
  let _modalOpen = false;

  /* ── Rings ───────────────────────────────────────────────────── */
  function renderRings(daily, profile) {
    const totals  = daily.totals  || {};
    const targets = profile;

    MACROS.forEach(function (m) {
      const canvas = document.getElementById('hw-ring-' + m.key);
      if (!canvas) return;
      const ctx    = canvas.getContext('2d');
      const target = targets[m.targetKey] || 1;
      const value  = totals[m.key] || 0;
      const pct    = Math.min(100, Math.round(value / target * 100));
      const labelV = Math.round(value);
      const labelT = Math.round(target);
      window.HEALTH_CHARTS.drawRing(ctx, pct, m.color, `${labelV}\n${m.label}`, { lineWidth: 10, fontSize: 11 });
    });
  }

  /* ── Food list ───────────────────────────────────────────────── */
  function renderFoodList(daily) {
    const list = document.getElementById('hw-food-list');
    if (!list) return;
    const logs = (daily && daily.food_logs) || [];

    if (!logs.length) {
      list.innerHTML = '<div class="hw-empty">Sin comidas registradas hoy</div>';
      return;
    }

    list.innerHTML = logs.map(function (log) {
      const flag  = log.insulin_flag;
      const items = (log.flagged_items || []).slice(0, 3);
      const nudge = flag ? getDefaultNudge(items[0] || '') : '';
      const meal  = { breakfast: 'Desayuno', lunch: 'Almuerzo', dinner: 'Cena', other: 'Snack' }[log.meal_type] || 'Comida';
      const timeStr = log.logged_at ? new Date(log.logged_at).toLocaleTimeString('es-PE', { hour: '2-digit', minute: '2-digit' }) : '';

      return `<div class="hw-food-item${flag ? ' hw-food-flagged' : ''}">
        <div class="hw-food-header">
          <span class="hw-food-meal">${meal}</span>
          <span class="hw-food-time">${timeStr}</span>
          ${flag ? `<span class="hw-insulin-badge" title="${nudge}">⚠ Glucémico</span>` : ''}
        </div>
        <div class="hw-food-desc">${log.description || ''}</div>
        <div class="hw-food-macros">
          <span>${Math.round(log.calories || 0)} kcal</span>
          <span>${Math.round(log.protein_g || 0)}g prot</span>
          <span>${Math.round(log.carbs_g || 0)}g carb</span>
        </div>
        ${flag && nudge ? `<div class="hw-insulin-nudge">💡 ${nudge}</div>` : ''}
      </div>`;
    }).join('');
  }

  /* ── Modal de registro ───────────────────────────────────────── */
  function openModal() {
    const modal = document.getElementById('hw-food-modal');
    if (modal) { modal.style.display = 'flex'; _modalOpen = true; }
    const tabText = document.getElementById('hw-food-tab-text');
    const tabPhoto = document.getElementById('hw-food-tab-photo');
    if (tabText) switchModalTab('text');
  }

  function closeModal() {
    const modal = document.getElementById('hw-food-modal');
    if (modal) { modal.style.display = 'none'; _modalOpen = false; }
    const input = document.getElementById('hw-food-text-input');
    if (input) input.value = '';
    const fileInput = document.getElementById('hw-food-file');
    if (fileInput) fileInput.value = '';
    const msgEl = document.getElementById('hw-food-modal-msg');
    if (msgEl) msgEl.textContent = '';
  }

  function switchModalTab(tab) {
    const textPane  = document.getElementById('hw-food-pane-text');
    const photoPane = document.getElementById('hw-food-pane-photo');
    const tabText   = document.getElementById('hw-food-tab-text');
    const tabPhoto  = document.getElementById('hw-food-tab-photo');

    if (textPane)  textPane.style.display  = tab === 'text'  ? 'block' : 'none';
    if (photoPane) photoPane.style.display = tab === 'photo' ? 'block' : 'none';
    if (tabText)   tabText.classList.toggle('active',  tab === 'text');
    if (tabPhoto)  tabPhoto.classList.toggle('active', tab === 'photo');
  }

  async function submitFood() {
    const msgEl    = document.getElementById('hw-food-modal-msg');
    const textInput = document.getElementById('hw-food-text-input');
    const fileInput = document.getElementById('hw-food-file');
    const mealSel   = document.getElementById('hw-food-meal-type');

    const text  = textInput ? textInput.value.trim() : '';
    const file  = fileInput && fileInput.files.length ? fileInput.files[0] : null;
    const meal  = mealSel ? mealSel.value : 'other';

    if (!text && !file) {
      if (msgEl) msgEl.textContent = 'Describí la comida o adjuntá una foto.';
      return;
    }

    if (msgEl) msgEl.textContent = 'Analizando…';
    const btn = document.getElementById('hw-food-submit-btn');
    if (btn) btn.disabled = true;

    const res = await window.HEALTH_API.analyzeFoodQuick(text, file, meal);

    if (btn) btn.disabled = false;

    if (!res.ok) {
      if (msgEl) msgEl.textContent = 'Error: ' + (res.error || 'intenta de nuevo');
      return;
    }

    if (msgEl) msgEl.textContent = 'Registrado! Actualizando…';
    setTimeout(function () {
      closeModal();
      refresh();
    }, 800);
  }

  function buildModal() {
    const existing = document.getElementById('hw-food-modal');
    if (existing) return;

    const modal = document.createElement('div');
    modal.id    = 'hw-food-modal';
    modal.className = 'hw-modal-overlay';
    modal.style.display = 'none';
    modal.innerHTML = `
      <div class="hw-modal-box">
        <div class="hw-modal-header">
          <span>Registrar comida</span>
          <button class="hw-modal-close" id="hw-food-modal-close">✕</button>
        </div>
        <div class="hw-modal-tabs">
          <button id="hw-food-tab-text"  class="hw-modal-tab active">Texto</button>
          <button id="hw-food-tab-photo" class="hw-modal-tab">Foto</button>
        </div>
        <div class="hw-modal-body">
          <div id="hw-food-pane-text">
            <textarea id="hw-food-text-input" class="hw-textarea"
              placeholder="Ej: almuerzo: pollo a la brasa sin papas, ensalada de lechuga y tomate"></textarea>
          </div>
          <div id="hw-food-pane-photo" style="display:none">
            <div class="hw-drop-zone" id="hw-food-drop">
              <span>Arrastrá una foto acá o hacé click</span>
              <input type="file" id="hw-food-file" accept="image/*" style="display:none">
            </div>
            <div id="hw-food-drop-preview"></div>
          </div>
          <select id="hw-food-meal-type" class="hw-select" style="margin-top:10px">
            <option value="breakfast">Desayuno</option>
            <option value="lunch" selected>Almuerzo</option>
            <option value="dinner">Cena</option>
            <option value="other">Snack</option>
          </select>
          <p id="hw-food-modal-msg" class="hw-modal-msg"></p>
        </div>
        <div class="hw-modal-footer">
          <button class="hw-btn-ghost" id="hw-food-cancel-btn">Cancelar</button>
          <button class="hw-btn-primary" id="hw-food-submit-btn">Registrar</button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);

    modal.addEventListener('click', function (e) {
      if (e.target === modal) closeModal();
    });
    document.getElementById('hw-food-modal-close').addEventListener('click', closeModal);
    document.getElementById('hw-food-cancel-btn').addEventListener('click', closeModal);
    document.getElementById('hw-food-submit-btn').addEventListener('click', submitFood);

    document.getElementById('hw-food-tab-text').addEventListener('click', function () { switchModalTab('text'); });
    document.getElementById('hw-food-tab-photo').addEventListener('click', function () { switchModalTab('photo'); });

    // Drop zone
    const dropZone = document.getElementById('hw-food-drop');
    const fileInput = document.getElementById('hw-food-file');
    dropZone.addEventListener('click', function () { fileInput.click(); });
    dropZone.addEventListener('dragover', function (e) { e.preventDefault(); dropZone.classList.add('dragging'); });
    dropZone.addEventListener('dragleave', function () { dropZone.classList.remove('dragging'); });
    dropZone.addEventListener('drop', function (e) {
      e.preventDefault();
      dropZone.classList.remove('dragging');
      if (e.dataTransfer.files.length) {
        fileInput.files = e.dataTransfer.files;
        showDropPreview(e.dataTransfer.files[0]);
      }
    });
    fileInput.addEventListener('change', function () {
      if (fileInput.files.length) showDropPreview(fileInput.files[0]);
    });
  }

  function showDropPreview(file) {
    const preview = document.getElementById('hw-food-drop-preview');
    if (!preview) return;
    const reader = new FileReader();
    reader.onload = function (e) {
      preview.innerHTML = `<img src="${e.target.result}" class="hw-drop-img-preview">`;
    };
    reader.readAsDataURL(file);
  }

  /* ── Render panel ────────────────────────────────────────────── */
  function renderPanel() {
    const panel = document.getElementById('hw-panel-nutricion');
    if (!panel) return;

    panel.innerHTML = `
      <div class="hw-panel-header">
        <h2 class="hw-panel-title">Nutrición</h2>
        <div class="hw-date-picker-wrap">
          <input type="date" id="hw-nutr-date" class="hw-date-input" value="${_currentDate}">
        </div>
        <button class="hw-btn-primary" id="hw-food-add-btn">+ Registrar comida</button>
      </div>

      <div class="hw-section-title">MACRO_INTAKE</div>
      <div class="hw-rings-row">
        ${MACROS.map(m => `
          <div class="hw-ring-wrap">
            <canvas id="hw-ring-${m.key}" width="90" height="90"></canvas>
          </div>
        `).join('')}
      </div>

      <div class="hw-section-title">MEAL_LOG</div>
      <div id="hw-food-list" class="hw-food-list">
        <div class="hw-empty">Cargando…</div>
      </div>
    `;

    document.getElementById('hw-food-add-btn').addEventListener('click', openModal);

    const dateInput = document.getElementById('hw-nutr-date');
    dateInput.addEventListener('change', function () {
      _currentDate = dateInput.value;
      refresh();
    });
  }

  async function refresh() {
    try {
      const [dailyRes, profileRes] = await Promise.all([
        window.HEALTH_API.getDaily(_currentDate),
        window.HEALTH_API.getNutritionProfile(),
      ]);

      _daily   = (dailyRes.ok   && dailyRes.data)   || {};
      _profile = (profileRes.ok && profileRes.data)  || { daily_calories: 2000, daily_protein_g: 175, daily_carbs_g: 150, daily_fats_g: 65, daily_fiber_g: 35 };

      renderRings(_daily, _profile);
      renderFoodList(_daily);
    } catch (e) {
      console.error('[nutrition] refresh error', e);
    }
  }

  function onShow() {
    if (!document.getElementById('hw-panel-nutricion') ||
        !document.getElementById('hw-ring-calories')) {
      renderPanel();
    }
    buildModal();
    refresh();
  }

  window.HEALTH_PANEL_NUTRICION = { onShow, refresh };
})();
