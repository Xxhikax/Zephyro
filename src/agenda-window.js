/* agenda-window.js — Ventana flotante de gestión de agenda.
   - Draggable por el title bar.
   - Tabs: HOY | SEMANA | CALENDARIO (solo clases) | AÑADIR.
   - HOY/SEMANA agrupan eventos en CLASES & CAMPUS vs ACTIVIDADES.
   - CALENDARIO muestra solo cursos universitarios con datos Canvas inline.
   - CRUD completo contra /api/schedule/*.
   - Canvas data (notas, pendientes, anuncios) en detalle de cada clase.
*/
(function () {
    const API = (typeof API_BASE !== 'undefined') ? API_BASE : 'http://localhost:8000';

    const CATS = [
        { id: 'university',       label: 'Clases',            color: '#6ad0ff' },
        { id: 'uni_exam',         label: 'Exámenes',          color: '#ff4d4d' },
        { id: 'uni_assignment',   label: 'Tareas / Entregas', color: '#ffd700' },
        { id: 'course_study',     label: 'Estudio / Curso',   color: '#b08cff' },
        { id: 'personal_project', label: 'Proyecto Personal', color: '#00ff88' },
        { id: 'client_project',   label: 'Proyecto Cliente',  color: '#ffaa00' },
        { id: 'exercise',         label: 'Ejercicio',         color: '#ff6b6b' },
        { id: 'other',            label: 'Otro',              color: '#5BCFA6' },
    ];

    // Categorías universitarias (van en sección CLASES & CAMPUS)
    const UNI_CATS = new Set(['university', 'uni_exam', 'uni_assignment']);

    /* ------------------------------------------------------------------ */
    /* State                                                                */
    /* ------------------------------------------------------------------ */
    let _events = [];
    let _activeTab = 'hoy';
    let _expandedId = null;
    let _editingId = null;
    let _dragging = false;
    let _dragOffX = 0, _dragOffY = 0;
    let _standalone = false;   // true cuando corre en su propia ventana (agenda.html)
    let _searchQuery = '';
    let _filterCat = '';
    let _searchDebounce = null;

    // Canvas cache
    let _canvas = null;
    let _canvasFetchedAt = 0;
    const CANVAS_TTL = 600000; // 10 min

    /* ------------------------------------------------------------------ */
    /* Canvas helpers                                                       */
    /* ------------------------------------------------------------------ */
    async function fetchCanvas() {
        if (_canvas && Date.now() - _canvasFetchedAt < CANVAS_TTL) return _canvas;
        const tk = token();
        if (!tk) return null;
        try {
            const r = await awFetch(`${API}/api/canvas/summary`);
            if (!r.ok) return null;
            const d = await r.json();
            if (!d.configured) return null;
            _canvas = d;
            _canvasFetchedAt = Date.now();
            return d;
        } catch (_) { return null; }
    }

    function findCanvasGrade(eventTitle, summary) {
        if (!summary || !summary.grades) return null;
        const evWords = eventTitle.toLowerCase().split(/\s+/).filter(w => w.length > 3);
        return summary.grades.find(g => {
            const cn = g.course.toLowerCase();
            if (cn.includes(eventTitle.toLowerCase()) || eventTitle.toLowerCase().includes(cn)) return true;
            const cnWords = cn.split(/\s+/).filter(w => w.length > 3);
            const overlap = evWords.filter(w => cnWords.some(cw => cw.includes(w) || w.includes(cw)));
            return overlap.length > 0 && overlap.length >= Math.min(evWords.length, cnWords.length) * 0.5;
        }) || null;
    }

    function findCanvasMissing(eventTitle, summary) {
        if (!summary || !summary.missing) return [];
        const evFirst = eventTitle.toLowerCase().split(/\s+/).find(w => w.length > 3) || '';
        return summary.missing.filter(m => {
            const c = (m.course || '').toLowerCase();
            return c.includes(evFirst) || evFirst && eventTitle.toLowerCase().includes(c.split(' ')[0]);
        });
    }

    function findCanvasAnnouncements(eventTitle, summary) {
        if (!summary || !summary.announcements) return [];
        const evFirst = eventTitle.toLowerCase().split(/\s+/).find(w => w.length > 3) || '';
        return summary.announcements.filter(a => {
            const c = (a.course || '').toLowerCase();
            return c.includes(evFirst) || evFirst && eventTitle.toLowerCase().includes(c.split(' ')[0]);
        });
    }

    async function loadCanvasDetail(id) {
        const ev = _events.find(e => e.id === id);
        if (!ev || ev.category !== 'university') return;
        const detailEl = document.getElementById(`aw-detail-${id}`);
        if (!detailEl) return;
        const canvasEl = detailEl.querySelector('.aw-canvas-detail');
        if (!canvasEl || canvasEl.dataset.loaded === 'true') return;

        const summary = await fetchCanvas();
        canvasEl.dataset.loaded = 'true';

        if (!summary) {
            canvasEl.innerHTML = '';
            return;
        }

        const grade   = findCanvasGrade(ev.title, summary);
        const missing = findCanvasMissing(ev.title, summary);
        const anncs   = findCanvasAnnouncements(ev.title, summary).slice(0, 1);

        let html = '<div class="aw-canvas-section">';
        html += '<div class="aw-canvas-header">◈ CANVAS</div>';

        if (grade && grade.score != null) {
            const sc = Number.isFinite(parseFloat(grade.score)) ? parseFloat(grade.score) : null;
            const scoreColor = sc === null ? '#5a6280' : sc >= 80 ? '#00ff88' : sc >= 60 ? '#ffd700' : '#ff4d4d';
            const scoreText = sc !== null ? `${sc.toFixed(1)}/100` : 'n/a';
            html += `<div class="aw-canvas-row">
                <span class="aw-canvas-key">nota:</span>
                <span class="aw-canvas-val" style="color:${scoreColor}">${scoreText}</span>
            </div>`;
        } else {
            html += `<div class="aw-canvas-row"><span class="aw-canvas-val" style="color:#3a4060">sin nota disponible en Canvas</span></div>`;
        }

        if (missing.length) {
            missing.slice(0, 2).forEach(m => {
                const due = m.due_str ? ` (vence ${m.due_str.slice(0,5)})` : '';
                html += `<div class="aw-canvas-row">
                    <span class="aw-canvas-key">pendiente:</span>
                    <span class="aw-canvas-val aw-canvas-warn">${esc(m.title)}${due}</span>
                </div>`;
            });
        }

        if (anncs.length) {
            html += `<div class="aw-canvas-row">
                <span class="aw-canvas-key">anuncio:</span>
                <span class="aw-canvas-val">${esc(anncs[0].title)}</span>
            </div>`;
        }

        html += '</div>';
        canvasEl.innerHTML = html;
    }

    /* ------------------------------------------------------------------ */
    /* DOM refs                                                             */
    /* ------------------------------------------------------------------ */
    const $ = id => document.getElementById(id);
    let win, titlebar, statusMsg;

    /* ------------------------------------------------------------------ */
    /* Detección de colisiones                                              */
    /* ------------------------------------------------------------------ */
    function detectOverlaps(evs) {
        // Retorna Set de IDs que tienen al menos una colisión
        const overlapping = new Set();
        for (let i = 0; i < evs.length; i++) {
            const a = evs[i];
            const aStart = new Date(a.start_iso).getTime();
            const aEnd   = new Date(a.end_iso || a.start_iso).getTime();
            for (let j = i + 1; j < evs.length; j++) {
                const b = evs[j];
                const bStart = new Date(b.start_iso).getTime();
                const bEnd   = new Date(b.end_iso || b.start_iso).getTime();
                if (aStart < bEnd && aEnd > bStart) {
                    overlapping.add(a.id);
                    overlapping.add(b.id);
                }
            }
        }
        return overlapping;
    }

    /* ------------------------------------------------------------------ */
    /* Timeline view — posiciona eventos por horas, muestra colisiones     */
    /* ------------------------------------------------------------------ */
    const TL_START_HOUR = 7;
    const TL_END_HOUR   = 22;
    const TL_PX_PER_MIN = 1.4; // px por minuto

    function renderCalendar() {
        const pane = document.getElementById('aw-calendar-pane');
        if (!pane) return;

        const now = new Date();
        const monday = new Date(now);
        monday.setDate(now.getDate() - ((now.getDay() + 6) % 7));
        monday.setHours(0, 0, 0, 0);

        const dayNames = ['LUN', 'MAR', 'MIÉ', 'JUE', 'VIE', 'SÁB', 'DOM'];
        const days = [];
        for (let i = 0; i < 7; i++) {
            const d = new Date(monday);
            d.setDate(monday.getDate() + i);
            days.push({
                date: d,
                key: dateKey(d),
                label: `${dayNames[i]} ${pad(d.getDate())}/${pad(d.getMonth() + 1)}`,
                isToday: dateKey(d) === todayKey(),
            });
        }

        const totalMins   = (TL_END_HOUR - TL_START_HOUR) * 60;
        const totalHeight = totalMins * TL_PX_PER_MIN;

        // Agrupar eventos por día
        const byDay = {};
        for (let i = 0; i < 7; i++) byDay[i] = [];
        for (const ev of _events) {
            const idx = days.findIndex(d => d.key === isoDay(ev.start_iso));
            if (idx !== -1) byDay[idx].push(ev);
        }

        // Columna de horas
        let hoursCol = `<div class="aw-tl-hours">`;
        for (let h = TL_START_HOUR; h <= TL_END_HOUR; h++) {
            const top = (h - TL_START_HOUR) * 60 * TL_PX_PER_MIN;
            hoursCol += `<div class="aw-tl-hour-label" style="top:${top}px">${pad(h)}:00</div>`;
        }
        // Línea de "ahora" si es hoy visible
        const nowH = now.getHours() + now.getMinutes() / 60;
        if (nowH >= TL_START_HOUR && nowH <= TL_END_HOUR) {
            const nowTop = (nowH - TL_START_HOUR) * 60 * TL_PX_PER_MIN;
            hoursCol += `<div class="aw-tl-now-marker hours" style="top:${nowTop}px"></div>`;
        }
        hoursCol += `</div>`;

        // Columnas de días
        let daysHTML = '';
        for (let d = 0; d < 7; d++) {
            const day = days[d];
            const evs = byDay[d];
            const overlappingIds = detectOverlaps(evs);

            const todayCls = day.isToday ? ' aw-tl-col--today' : '';
            daysHTML += `<div class="aw-tl-col${todayCls}" style="height:${totalHeight}px">`;

            // Líneas de hora de fondo
            for (let h = TL_START_HOUR; h <= TL_END_HOUR; h++) {
                const top = (h - TL_START_HOUR) * 60 * TL_PX_PER_MIN;
                daysHTML += `<div class="aw-tl-grid-line" style="top:${top}px"></div>`;
            }

            // Línea de "ahora" en columna de hoy
            if (day.isToday && nowH >= TL_START_HOUR && nowH <= TL_END_HOUR) {
                const nowTop = (nowH - TL_START_HOUR) * 60 * TL_PX_PER_MIN;
                daysHTML += `<div class="aw-tl-now-marker" style="top:${nowTop}px"></div>`;
            }

            // Calcular side-by-side para eventos solapados
            // Agrupar eventos con colisión en clusters
            const placed = evs.map(ev => {
                const eStart = new Date(ev.start_iso);
                const eEnd   = new Date(ev.end_iso || ev.start_iso);
                const topMin   = Math.max(0, (eStart.getHours() * 60 + eStart.getMinutes()) - TL_START_HOUR * 60);
                const endMin   = Math.max(0, (eEnd.getHours()   * 60 + eEnd.getMinutes())   - TL_START_HOUR * 60);
                const heightMin = Math.max(endMin - topMin, 20);
                const isOver   = overlappingIds.has(ev.id);
                return { ev, topPx: topMin * TL_PX_PER_MIN, heightPx: heightMin * TL_PX_PER_MIN, isOver };
            });

            // Assign column slots for overlapping events
            const slots = assignSlots(placed);

            for (const item of slots) {
                const { ev, topPx, heightPx, isOver, col, totalCols } = item;
                const cat      = (ev.category || 'other').replace(/[^a-z_]/gi, '');
                const title    = esc(ev.title || '');
                const eStart   = new Date(ev.start_iso);
                const eEnd     = new Date(ev.end_iso || ev.start_iso);
                const timeStr  = `${pad(eStart.getHours())}:${pad(eStart.getMinutes())}–${pad(eEnd.getHours())}:${pad(eEnd.getMinutes())}`;
                const overCls  = isOver ? ' aw-tl-event--overlap' : '';
                const recurBadge = ev.parent_id ? ' ↻' : '';

                const widthPct  = 100 / totalCols;
                const leftPct   = col * widthPct;
                const tooltipText = `${title}\n${timeStr}${ev.notes ? '\n' + ev.notes : ''}`;

                let gradeTag = '';
                if (_canvas) {
                    const g = findCanvasGrade(ev.title, _canvas);
                    if (g && g.score != null) {
                        const sc = Number.isFinite(parseFloat(g.score)) ? parseFloat(g.score) : null;
                        if (sc !== null) {
                            const col2 = sc >= 80 ? '#00ff88' : sc >= 60 ? '#ffd700' : '#ff4d4d';
                            gradeTag = `<span class="aw-tl-grade" style="color:${col2}">${sc.toFixed(0)}</span>`;
                        }
                    }
                }

                daysHTML += `
                <div class="aw-tl-event cat-${cat}${overCls}"
                    style="top:${topPx.toFixed(1)}px;height:${heightPx.toFixed(1)}px;left:${leftPct.toFixed(1)}%;width:${widthPct.toFixed(1)}%"
                    title="${esc(tooltipText)}"
                    onclick="AW._editRow(${ev.id})">
                    ${isOver ? '<span class="aw-tl-overlap-icon">⚠</span>' : ''}
                    <span class="aw-tl-ev-title">${title}${recurBadge}</span>
                    <span class="aw-tl-ev-time">${timeStr}</span>
                    ${gradeTag}
                </div>`;
            }

            daysHTML += '</div>';
        }

        // Header de días
        let headerHTML = '<div class="aw-tl-header"><div class="aw-tl-hour-spacer"></div>';
        for (const day of days) {
            const todayCls = day.isToday ? ' aw-tl-col--today' : '';
            headerHTML += `<div class="aw-tl-day-label${todayCls}">${day.label}</div>`;
        }
        headerHTML += '</div>';

        const html = `
        <div class="aw-tl-filter-note">📅 TIMELINE SEMANAL — haz clic en un evento para editarlo</div>
        <div class="aw-tl-scroll">
            ${headerHTML}
            <div class="aw-tl-body">
                ${hoursCol}
                <div class="aw-tl-days">${daysHTML}</div>
            </div>
        </div>`;

        pane.innerHTML = html;

        // Pre-fetch Canvas en background para mostrar notas en eventos
        if (!_canvas) {
            fetchCanvas().then(s => { if (s) renderCalendar(); }).catch(() => {});
        }
    }

    function assignSlots(items) {
        // Para cada item con overlap, asigna col y totalCols para side-by-side
        const result = items.map(item => ({ ...item, col: 0, totalCols: 1 }));
        if (!result.length) return result;

        // Encontrar grupos de eventos que se solapan entre sí
        const groups = [];
        const visited = new Set();

        for (let i = 0; i < result.length; i++) {
            if (visited.has(i)) continue;
            const group = [i];
            visited.add(i);
            for (let j = i + 1; j < result.length; j++) {
                if (visited.has(j)) continue;
                const a = result[i], b = result[j];
                const aEnd = a.topPx + a.heightPx, bEnd = b.topPx + b.heightPx;
                if (a.topPx < bEnd && aEnd > b.topPx) {
                    group.push(j);
                    visited.add(j);
                }
            }
            groups.push(group);
        }

        for (const group of groups) {
            const n = group.length;
            group.forEach((idx, slot) => {
                result[idx].col = slot;
                result[idx].totalCols = n;
            });
        }

        return result;
    }

    /* ------------------------------------------------------------------ */
    /* Auth token                                                           */
    /* ------------------------------------------------------------------ */
    function token() {
        if (window.auth && typeof window.auth.getToken === 'function') return window.auth.getToken() || '';
        return localStorage.getItem('authToken') || '';
    }

    /* Fetch autenticado que no depende de window.auth: agenda.html (ventana
       standalone) no carga auth.js — y no debe hacerlo: su restoreSession()
       limpia localStorage en cada carga y pisaría el token compartido. Antes
       window.auth.authFetch tiraba TypeError acá → "sin conexión". */
    function awFetch(url, opts = {}) {
        if (window.auth && typeof window.auth.authFetch === 'function') {
            return awFetch(url, opts);
        }
        const tk = token();
        if (tk) {
            opts = {
                ...opts,
                headers: { ...(opts.headers || {}), 'Authorization': `Bearer ${tk}`, 'X-Token': tk },
            };
        }
        return fetch(url, opts);
    }

    /* ------------------------------------------------------------------ */
    /* Open / Close                                                         */
    /* ------------------------------------------------------------------ */
    function open()   { win.classList.add('open'); refresh(); }
    function close()  {
        // En ventana aparte, "cerrar" cierra la ventana del SO; en modo flotante
        // (legacy, dentro del main window) solo oculta el overlay.
        if (_standalone) {
            try {
                const cw = window.__TAURI__ && window.__TAURI__.window
                    && window.__TAURI__.window.getCurrentWindow
                    && window.__TAURI__.window.getCurrentWindow();
                if (cw) { cw.close(); return; }
            } catch (_) { /* fallthrough al ocultar */ }
        }
        win.classList.remove('open');
    }
    function toggle() { win.classList.contains('open') ? close() : open(); }

    /* Tras un CRUD: refresca el sidebar del propio contexto (modo flotante legacy)
       y, en ventana aparte, emite un evento Tauri para que el main window
       refresque su sidebar de agenda en vivo. */
    function notifyChanged() {
        if (window.AGENDA && typeof window.AGENDA.refresh === 'function') window.AGENDA.refresh();
        if (_standalone) {
            try {
                const emit = window.__TAURI__ && window.__TAURI__.event && window.__TAURI__.event.emit;
                if (emit) emit('agenda:changed');
            } catch (_) { /* noop */ }
        }
    }

    /* ------------------------------------------------------------------ */
    /* Fetch events                                                         */
    /* ------------------------------------------------------------------ */
    async function refresh() {
        const tk = token();
        if (!tk) { setStatus('sin sesión'); return; }

        const now  = new Date();
        const from = new Date(now.getFullYear(), now.getMonth(), now.getDate());
        const to   = new Date(from.getTime() + 7 * 86400000);

        setStatus('actualizando…');
        try {
            const r = await awFetch(
                `${API}/api/schedule/events?from_=${enc(from.toISOString())}&to=${enc(to.toISOString())}`
            );
            if (!r.ok) { setStatus(`error ${r.status}`); return; }
            const data = await r.json();
            _events = data.events || [];
            renderCurrentTab();
            setStatus(`${_events.length} evento(s) — ${fmt(now)}`);
            if (window.AGENDA) AGENDA.refresh();
            // Pre-cargar Canvas en background
            fetchCanvas().catch(() => {});
        } catch (e) {
            setStatus('sin conexión');
        }
    }

    /* ------------------------------------------------------------------ */
    /* CRUD                                                                 */
    /* ------------------------------------------------------------------ */
    async function createEvent(payload) {
        const tk = token();
        if (!tk) return { ok: false, msg: 'Sin sesión' };
        try {
            const r = await awFetch(`${API}/api/schedule/events`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            let data;
            try { data = await r.json(); } catch (_) { data = {}; }
            if (!r.ok) return { ok: false, msg: data.detail || 'Error' };
            return { ok: true, event: data.event, data };
        } catch (_) { return { ok: false, msg: 'Sin conexión' }; }
    }

    async function updateEvent(id, payload) {
        const tk = token();
        if (!tk) return { ok: false, msg: 'Sin sesión' };
        try {
            const r = await awFetch(`${API}/api/schedule/events/${id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const data = await r.json();
            if (!r.ok) return { ok: false, msg: data.detail || `HTTP ${r.status}` };
            return { ok: true, event: data.event };
        } catch (e) { return { ok: false, msg: 'Sin conexión' }; }
    }

    async function deleteEvent(id) {
        const tk = token();
        if (!tk) return false;
        try {
            const r = await awFetch(`${API}/api/schedule/events/${id}`, { method: 'DELETE' });
            return r.ok;
        } catch (_) { return false; }
    }

    /* ------------------------------------------------------------------ */
    /* Render tabs                                                          */
    /* ------------------------------------------------------------------ */
    function renderCurrentTab() {
        switch (_activeTab) {
            case 'hoy':      renderPane('aw-hoy-pane',    renderHoy());    break;
            case 'semana':   renderPane('aw-semana-pane', renderSemana()); break;
            case 'calendar': renderCalendar();                             break;
        }
    }

    function renderPane(paneId, html) {
        const el = $(paneId);
        if (el) el.innerHTML = html;
    }

    /* HOY: agrupa en CLASES & CAMPUS vs ACTIVIDADES */
    function renderHoy() {
        const hoy = todayKey();
        const all = _events
            .filter(e => isoDay(e.start_iso) === hoy)
            .sort((a, b) => new Date(a.start_iso) - new Date(b.start_iso));
        const evs = filteredEvents(all);

        const noResults = !evs.length ? (all.length ? '— sin resultados —' : '— sin eventos hoy —') : '';

        const classes    = evs.filter(e => UNI_CATS.has(e.category || 'other'));
        const activities = evs.filter(e => !UNI_CATS.has(e.category || 'other'));

        let html = searchBarHTML();
        if (noResults) { html += `<div class="aw-empty">${noResults}</div>`; return html; }

        if (classes.length) {
            html += '<div class="aw-section-label aw-section-uni">CLASES &amp; CAMPUS</div>';
            html += classes.map(e => eventRow(e)).join('');
        }
        if (activities.length) {
            html += '<div class="aw-section-label aw-section-act">ACTIVIDADES</div>';
            html += activities.map(e => eventRow(e)).join('');
        }
        return html;
    }

    /* SEMANA: agrupa por día, dentro de cada día separa secciones */
    function renderSemana() {
        const now = new Date();
        let html = searchBarHTML();
        for (let i = 0; i < 7; i++) {
            const d  = new Date(now.getFullYear(), now.getMonth(), now.getDate() + i);
            const dk = dateKey(d);
            const allDay = _events
                .filter(e => isoDay(e.start_iso) === dk)
                .sort((a, b) => new Date(a.start_iso) - new Date(b.start_iso));
            const dayEvs = filteredEvents(allDay);

            const label = i === 0 ? 'HOY' : i === 1 ? 'MAÑANA' : fmtDay(d);
            html += `<div class="aw-section-label">${label}</div>`;

            if (!dayEvs.length) {
                const reason = allDay.length ? '— sin resultados —' : '— sin eventos —';
                html += `<div class="aw-empty">${reason}</div>`;
                continue;
            }

            const classes    = dayEvs.filter(e => UNI_CATS.has(e.category || 'other'));
            const activities = dayEvs.filter(e => !UNI_CATS.has(e.category || 'other'));

            if (classes.length) {
                html += '<div class="aw-section-label aw-section-uni" style="margin-top:4px;font-size:9px">CLASES &amp; CAMPUS</div>';
                html += classes.map(e => eventRow(e)).join('');
            }
            if (activities.length) {
                html += '<div class="aw-section-label aw-section-act" style="margin-top:4px;font-size:9px">ACTIVIDADES</div>';
                html += activities.map(e => eventRow(e)).join('');
            }
        }
        return html;
    }

    /* ------------------------------------------------------------------ */
    /* Event row                                                            */
    /* ------------------------------------------------------------------ */
    function eventRow(ev) {
        const dt    = new Date(ev.start_iso);
        const t     = `${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
        const endDt = new Date(ev.end_iso);
        const dur   = Math.round((endDt - dt) / 60000);
        const cat   = (ev.category || 'other').replace(/[^a-z_]/gi, '');
        const title = esc(ev.title || '');
        const isOpen    = _expandedId === ev.id;
        const isEditing = _editingId  === ev.id;
        const notes  = ev.notes ? esc(ev.notes) : '—';
        const endT   = `${pad(endDt.getHours())}:${pad(endDt.getMinutes())}`;

        if (isEditing) {
            const dateStr  = isoDay(ev.start_iso);
            const startT   = `${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
            const endTval  = `${pad(endDt.getHours())}:${pad(endDt.getMinutes())}`;
            const catOpts  = CATS.map(c =>
                `<option value="${c.id}" ${cat === c.id ? 'selected' : ''}>${c.label}</option>`
            ).join('');
            return `
            <div class="aw-event-row cat-${cat}" data-id="${ev.id}">
                <div class="aw-edit-form">
                    <div class="aw-edit-row">
                        <input class="aw-edit-input aw-edit-title" id="aw-edit-title-${ev.id}" value="${esc(ev.title || '')}" placeholder="Título">
                    </div>
                    <div class="aw-edit-row aw-edit-row-3">
                        <div class="aw-edit-field"><label>Fecha</label>
                            <input class="aw-edit-input" id="aw-edit-date-${ev.id}" type="date" value="${dateStr}"></div>
                        <div class="aw-edit-field"><label>Inicio</label>
                            <input class="aw-edit-input" id="aw-edit-start-${ev.id}" type="time" value="${startT}"></div>
                        <div class="aw-edit-field"><label>Fin</label>
                            <input class="aw-edit-input" id="aw-edit-end-${ev.id}" type="time" value="${endTval}"></div>
                    </div>
                    <div class="aw-edit-row aw-edit-row-2">
                        <div class="aw-edit-field"><label>Categoría</label>
                            <select class="aw-edit-input" id="aw-edit-cat-${ev.id}">${catOpts}</select></div>
                    </div>
                    <div class="aw-edit-row">
                        <div class="aw-edit-field" style="flex:1"><label>Notas</label>
                            <textarea class="aw-edit-input aw-edit-notes" id="aw-edit-notes-${ev.id}">${esc(ev.notes || '')}</textarea></div>
                    </div>
                    <div class="aw-edit-actions">
                        <button class="aw-submit" onclick="AW._saveEdit(${ev.id})">GUARDAR</button>
                        <button class="aw-cancel-btn" onclick="AW._cancelEdit()">CANCELAR</button>
                    </div>
                </div>
            </div>`;
        }

        // Placeholder Canvas para clases universitarias
        const canvasPlaceholder = (ev.category === 'university')
            ? `<div class="aw-canvas-detail" data-loaded="false"><div class="aw-canvas-loading">cargando datos Canvas…</div></div>`
            : '';

        const recurBadge = ev.parent_id ? '<span class="aw-recur-badge" title="Evento recurrente">↻</span>' : '';
        return `
        <div class="aw-event-row cat-${cat}" data-id="${ev.id}" onclick="AW._toggleDetail(${ev.id})">
            <span class="aw-ev-time">${t}</span>
            <span class="aw-ev-title" title="${title}">${title}${recurBadge}</span>
            <span class="aw-ev-cat">${catLabel(cat)}</span>
            <button class="aw-ev-edit" title="Editar"   aria-label="Editar evento" onclick="event.stopPropagation();AW._editRow(${ev.id})">&#9998;</button>
            <button class="aw-ev-del"  title="Eliminar" aria-label="Eliminar evento" onclick="event.stopPropagation();AW._deleteRow(${ev.id})">✕</button>
        </div>
        <div class="aw-event-detail ${isOpen ? 'open' : ''}" id="aw-detail-${ev.id}">
            <div class="aw-detail-row"><span class="aw-detail-key">fin:</span><span class="aw-detail-val">${endT} (${dur} min)</span></div>
            <div class="aw-detail-row"><span class="aw-detail-key">cat:</span><span class="aw-detail-val">${catLabel(cat)}</span></div>
            <div class="aw-detail-row"><span class="aw-detail-key">notas:</span><span class="aw-detail-val">${notes}</span></div>
            ${canvasPlaceholder}
        </div>`;
    }

    /* ------------------------------------------------------------------ */
    /* Tab switching                                                        */
    /* ------------------------------------------------------------------ */
    function switchTab(name) {
        _activeTab = name;
        document.querySelectorAll('.aw-tab').forEach(t =>
            t.classList.toggle('active', t.dataset.tab === name)
        );
        document.querySelectorAll('.aw-pane').forEach(p =>
            p.classList.toggle('active', p.id === `aw-${name}-pane`)
        );
        if (name !== 'add') renderCurrentTab();
    }

    /* ------------------------------------------------------------------ */
    /* Detail toggle — carga Canvas al abrir clase universitaria            */
    /* ------------------------------------------------------------------ */
    function toggleDetail(id) {
        const el = $(`aw-detail-${id}`);
        if (!el) return;
        if (_expandedId === id) {
            el.classList.remove('open');
            _expandedId = null;
        } else {
            if (_expandedId !== null) {
                const prev = $(`aw-detail-${_expandedId}`);
                if (prev) prev.classList.remove('open');
            }
            el.classList.add('open');
            _expandedId = id;
            // Cargar Canvas data para clases
            loadCanvasDetail(id).catch(() => {});
        }
    }

    /* ------------------------------------------------------------------ */
    /* Delete / Edit                                                        */
    /* ------------------------------------------------------------------ */
    function showConfirmModal(message, onConfirm) {
        const existing = document.getElementById('aw-confirm-modal');
        if (existing) existing.remove();
        const modal = document.createElement('div');
        modal.id = 'aw-confirm-modal';
        modal.innerHTML = `
            <div class="aw-modal-overlay">
                <div class="aw-modal-box">
                    <div class="aw-modal-msg">${esc(message)}</div>
                    <div class="aw-modal-actions">
                        <button class="aw-submit" id="aw-modal-yes">ELIMINAR</button>
                        <button class="aw-cancel-btn" id="aw-modal-no">CANCELAR</button>
                    </div>
                </div>
            </div>`;
        document.body.appendChild(modal);
        document.getElementById('aw-modal-yes').onclick = () => { modal.remove(); onConfirm(); };
        document.getElementById('aw-modal-no').onclick  = () => modal.remove();
    }

    async function deleteRow(id) {
        const ev = _events.find(e => e.id === id);
        const title = ev ? ev.title : `evento #${id}`;
        const isRecurring = ev && ev.parent_id;

        showConfirmModal(`¿Eliminar "${title}"?`, async () => {
            if (isRecurring) {
                await showRecurringDeleteModal(id, ev);
            } else {
                const ok = await deleteEvent(id);
                if (ok) {
                    _events = _events.filter(e => e.id !== id);
                    renderCurrentTab();
                    setStatus('Evento eliminado.');
                    notifyChanged();
                } else {
                    setStatus('Error al eliminar.');
                }
            }
        });
    }

    async function showRecurringDeleteModal(id, ev) {
        const modal = document.createElement('div');
        modal.id = 'aw-recur-modal';
        modal.innerHTML = `
            <div class="aw-modal-overlay">
                <div class="aw-modal-box">
                    <div class="aw-modal-msg">¿Eliminar solo este evento o toda la serie recurrente?</div>
                    <div class="aw-modal-actions">
                        <button class="aw-submit" id="aw-recur-one">SOLO ESTE</button>
                        <button class="aw-submit" id="aw-recur-all">TODA LA SERIE</button>
                        <button class="aw-cancel-btn" id="aw-recur-cancel">CANCELAR</button>
                    </div>
                </div>
            </div>`;
        document.body.appendChild(modal);

        document.getElementById('aw-recur-cancel').onclick = () => modal.remove();
        document.getElementById('aw-recur-one').onclick = async () => {
            modal.remove();
            const ok = await deleteEvent(id);
            if (ok) {
                _events = _events.filter(e => e.id !== id);
                renderCurrentTab();
                setStatus('Evento eliminado.');
                notifyChanged();
            } else { setStatus('Error al eliminar.'); }
        };
        document.getElementById('aw-recur-all').onclick = async () => {
            modal.remove();
            const parentId = ev.parent_id;
            const siblings = _events.filter(e => e.parent_id === parentId);
            await Promise.all(siblings.map(s => deleteEvent(s.id)));
            _events = _events.filter(e => e.parent_id !== parentId);
            renderCurrentTab();
            setStatus(`Serie eliminada (${siblings.length} eventos).`);
            notifyChanged();
        };
    }

    function editRow(id) {
        _editingId = id;
        _expandedId = null;
        renderCurrentTab();
    }

    function cancelEdit() {
        _editingId = null;
        renderCurrentTab();
    }

    async function saveEdit(id) {
        const titleEl = document.getElementById(`aw-edit-title-${id}`);
        const dateEl  = document.getElementById(`aw-edit-date-${id}`);
        const startEl = document.getElementById(`aw-edit-start-${id}`);
        const endEl   = document.getElementById(`aw-edit-end-${id}`);
        const catEl   = document.getElementById(`aw-edit-cat-${id}`);
        const notesEl = document.getElementById(`aw-edit-notes-${id}`);

        const title = titleEl ? titleEl.value.trim() : '';
        if (!title) { setStatus('Falta el título.'); return; }

        const payload = { title };
        if (catEl)   payload.category = catEl.value;
        if (notesEl) payload.notes    = notesEl.value.trim();
        if (dateEl && dateEl.value && startEl && startEl.value) {
            payload.start_iso = localToIso(dateEl.value, startEl.value);
            if (endEl && endEl.value) {
                const endIso = localToIso(dateEl.value, endEl.value);
                if (new Date(endIso) <= new Date(payload.start_iso)) {
                    setStatus('La hora de fin debe ser posterior al inicio.');
                    return;
                }
                payload.end_iso = endIso;
            }
        } else if (endEl && endEl.value) {
            const baseDate = isoDay(_events.find(e => e.id === id)?.start_iso || '');
            payload.end_iso = localToIso(baseDate, endEl.value);
        }

        const prev = _editingId;
        _editingId = null;
        renderCurrentTab();
        setStatus('Guardando…');

        const res = await updateEvent(id, payload);
        if (res.ok) {
            _events = _events.map(e => e.id === id ? res.event : e);
            renderCurrentTab();
            setStatus('Evento actualizado.');
            notifyChanged();
        } else {
            _editingId = prev;
            renderCurrentTab();
            setStatus(`Error: ${res.msg}`);
        }
    }

    /* ------------------------------------------------------------------ */
    /* Add form                                                             */
    /* ------------------------------------------------------------------ */
    async function submitAdd() {
        const titleEl  = $('aw-input-title');
        const dateEl   = $('aw-input-date');
        const startEl  = $('aw-input-start');
        const endEl    = $('aw-input-end');
        const catEl    = $('aw-input-cat');
        const recurEl  = $('aw-input-recurrence');
        const notesEl  = $('aw-input-notes');
        const feedback = $('aw-add-feedback');
        const btn      = $('aw-submit-btn');

        const title = titleEl.value.trim();
        if (!title) { showFeedback(feedback, 'Falta el título.', 'err'); return; }
        if (!dateEl.value || !startEl.value) { showFeedback(feedback, 'Falta fecha u hora de inicio.', 'err'); return; }

        const startIso   = localToIso(dateEl.value, startEl.value);
        const endIso     = endEl.value ? localToIso(dateEl.value, endEl.value) : null;
        const recurrence = recurEl ? (recurEl.value || null) : null;

        if (endIso && new Date(endIso) <= new Date(startIso)) {
            showFeedback(feedback, 'La hora de fin debe ser posterior al inicio.', 'err');
            return;
        }

        btn.disabled = true;
        showFeedback(feedback, 'Verificando colisiones…', '');

        // Avisar de solapamientos antes de guardar (no bloquea)
        const overlapWarning = await checkOverlap(startIso, endIso || '');
        if (overlapWarning) {
            showFeedback(feedback, `⚠ Solapamiento: ${overlapWarning} — guardando de todas formas`, 'warn');
            await new Promise(r => setTimeout(r, 1000));
        }

        showFeedback(feedback, recurrence ? 'Creando serie…' : 'Guardando…', '');
        const payload = {
            title,
            category:   catEl.value,
            start_iso:  startIso,
            end_iso:    endIso,
            notes:      notesEl.value.trim(),
        };
        if (recurrence) payload.recurrence = recurrence;

        const res = await createEvent(payload);

        btn.disabled = false;
        if (res.ok) {
            const count = res.data?.count;
            const msg = count && count > 1
                ? `✓ Serie creada: ${count} eventos de "${title}"`
                : `✓ Evento creado: "${title}"`;
            showFeedback(feedback, msg, 'ok');
            titleEl.value = '';
            notesEl.value = '';
            if (recurEl) recurEl.value = '';
            await refresh();
            notifyChanged();
            setTimeout(() => switchTab('hoy'), 900);
        } else {
            showFeedback(feedback, `✗ ${res.msg}`, 'err');
        }
    }

    async function checkOverlap(startIso, endIso, excludeId) {
        if (!endIso) return null;
        const tk = token();
        if (!tk) return null;
        try {
            let url = `${API}/api/schedule/check-overlap?start_iso=${enc(startIso)}&end_iso=${enc(endIso)}`;
            if (excludeId) url += `&exclude_id=${excludeId}`;
            const r = await awFetch(url);
            if (!r.ok) return null;
            let data;
            try { data = await r.json(); } catch (_) { return null; }
            if (!data.has_overlap) return null;
            return data.conflicts.map(c => c.title).join(', ');
        } catch (_) { return null; }
    }

    /* ------------------------------------------------------------------ */
    /* Draggable                                                            */
    /* ------------------------------------------------------------------ */
    function initDrag() {
        titlebar.addEventListener('mousedown', e => {
            if (e.target.closest('.aw-btn')) return;
            _dragging = true;
            const r = win.getBoundingClientRect();
            _dragOffX = e.clientX - r.left;
            _dragOffY = e.clientY - r.top;
            document.addEventListener('mousemove', onMouseMove);
            document.addEventListener('mouseup',   onMouseUp);
        });
    }
    function onMouseMove(e) {
        if (!_dragging) return;
        const vw = window.innerWidth, vh = window.innerHeight;
        let x = Math.max(0, Math.min(e.clientX - _dragOffX, vw - win.offsetWidth));
        let y = Math.max(0, Math.min(e.clientY - _dragOffY, vh - win.offsetHeight));
        win.style.left  = x + 'px';
        win.style.top   = y + 'px';
        win.style.right = 'auto';
    }
    function onMouseUp() {
        _dragging = false;
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup',   onMouseUp);
    }

    /* ------------------------------------------------------------------ */
    /* Helpers                                                              */
    /* ------------------------------------------------------------------ */
    function enc(s) { return encodeURIComponent(s); }
    function esc(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
    function pad(n) { return String(n).padStart(2, '0'); }
    function localToIso(dateStr, timeStr) {
        try {
            const d    = new Date(`${dateStr}T${timeStr}:00`);
            if (isNaN(d.getTime())) return `${dateStr}T${timeStr}:00Z`;
            const off  = -d.getTimezoneOffset();
            const sign = off >= 0 ? '+' : '-';
            const hh   = pad(Math.floor(Math.abs(off) / 60));
            const mm   = pad(Math.abs(off) % 60);
            return `${dateStr}T${timeStr}:00${sign}${hh}:${mm}`;
        } catch (_) { return `${dateStr}T${timeStr}:00Z`; }
    }
    function todayKey()  { return dateKey(new Date()); }
    function dateKey(d)  { return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`; }
    function isoDay(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        if (isNaN(d.getTime())) return iso.slice(0, 10);
        return dateKey(d);
    }
    function fmt(d)    { return `${pad(d.getHours())}:${pad(d.getMinutes())}`; }
    function fmtDay(d) {
        const days = ['Dom','Lun','Mar','Mié','Jue','Vie','Sáb'];
        return `${days[d.getDay()]} ${pad(d.getDate())}/${pad(d.getMonth()+1)}`;
    }
    function catLabel(id) { const c = CATS.find(c => c.id === id); return c ? c.label : id; }

    function filteredEvents(evs) {
        let result = evs;
        if (_searchQuery) {
            const q = _searchQuery.toLowerCase();
            result = result.filter(e =>
                (e.title || '').toLowerCase().includes(q) ||
                (e.notes || '').toLowerCase().includes(q)
            );
        }
        if (_filterCat) {
            result = result.filter(e => (e.category || 'other') === _filterCat);
        }
        return result;
    }

    function searchBarHTML() {
        const chips = [
            { id: '', label: 'Todos' },
            { id: 'university', label: 'Clases' },
            { id: 'uni_exam', label: 'Exámenes' },
            { id: 'course_study', label: 'Estudio' },
            { id: 'personal_project', label: 'Proyectos' },
            { id: 'exercise', label: 'Ejercicio' },
        ];
        const chipsHTML = chips.map(c =>
            `<button class="aw-chip${_filterCat === c.id ? ' active' : ''}" onclick="AW._filterCat('${c.id}')">${c.label}</button>`
        ).join('');
        return `
        <div class="aw-search-bar">
            <input class="aw-search-input" type="text" placeholder="Buscar evento…"
                value="${esc(_searchQuery)}" oninput="AW._search(this.value)" aria-label="Buscar eventos">
        </div>
        <div class="aw-chips">${chipsHTML}</div>`;
    }

    function setSearchQuery(q) {
        _searchQuery = q;
        clearTimeout(_searchDebounce);
        _searchDebounce = setTimeout(() => renderCurrentTab(), 180);
    }

    function setFilterCat(cat) {
        _filterCat = cat;
        renderCurrentTab();
    }
    function setStatus(msg)             { if (statusMsg) statusMsg.textContent = msg; }
    function showFeedback(el, msg, type) { el.textContent = msg; el.className = `aw-feedback ${type}`; }

    /* ------------------------------------------------------------------ */
    /* Build DOM                                                            */
    /* ------------------------------------------------------------------ */
    function buildHTML() {
        const todayStr = (() => {
            const d = new Date();
            return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;
        })();
        const catOptions = CATS.map(c => `<option value="${c.id}">${c.label}</option>`).join('');
        const legendHTML = CATS.map(c =>
            `<div class="aw-legend-item"><span class="aw-legend-dot" style="background:${c.color}"></span>${c.label}</div>`
        ).join('');

        return `
        <div id="aw-titlebar">
            <div class="aw-title">
                <span class="aw-title-dot"></span>
                AGENDA — GESTIÓN DE HORARIOS
            </div>
            <div class="aw-controls">
                <button class="aw-btn" onclick="AW.refresh()" title="Refrescar">↻ SYNC</button>
                <button class="aw-btn danger" onclick="AW.close()" title="Cerrar">✕</button>
            </div>
        </div>

        <div id="aw-tabs">
            <div class="aw-tab active" data-tab="hoy"      onclick="AW._tab('hoy')">HOY</div>
            <div class="aw-tab"        data-tab="semana"   onclick="AW._tab('semana')">SEMANA</div>
            <div class="aw-tab"        data-tab="calendar" onclick="AW._tab('calendar')">📅 CLASES</div>
            <div class="aw-tab"        data-tab="add"      onclick="AW._tab('add')">+ AÑADIR</div>
        </div>

        <div id="aw-body">
            <!-- HOY -->
            <div class="aw-pane active" id="aw-hoy-pane">
                <div class="aw-empty">Cargando…</div>
            </div>

            <!-- SEMANA -->
            <div class="aw-pane" id="aw-semana-pane">
                <div class="aw-empty">Cargando…</div>
            </div>

            <!-- CALENDARIO (solo clases) -->
            <div class="aw-pane" id="aw-calendar-pane">
                <div class="aw-empty">Cargando…</div>
            </div>

            <!-- AÑADIR -->
            <div class="aw-pane" id="aw-add-pane">
                <div class="aw-legend">${legendHTML}</div>

                <div class="aw-form-group">
                    <label>Título del evento</label>
                    <input id="aw-input-title" type="text" placeholder="ej: Clase de Sistemas Operativos" autocomplete="off">
                </div>

                <div class="aw-form-group">
                    <label>Categoría</label>
                    <select id="aw-input-cat">${catOptions}</select>
                </div>

                <div class="aw-row-2">
                    <div class="aw-form-group">
                        <label>Fecha</label>
                        <input id="aw-input-date" type="date" value="${todayStr}">
                    </div>
                    <div class="aw-form-group">
                        <label>Hora inicio</label>
                        <input id="aw-input-start" type="time" value="08:00">
                    </div>
                </div>

                <div class="aw-form-group">
                    <label>Hora fin <span style="color:#3a4060">(opcional)</span></label>
                    <input id="aw-input-end" type="time" placeholder="dejar vacío = +1h">
                </div>

                <div class="aw-form-group">
                    <label>Repetición</label>
                    <select id="aw-input-recurrence">
                        <option value="">— Sin repetición —</option>
                        <option value="weekly:MON">Todos los lunes</option>
                        <option value="weekly:TUE">Todos los martes</option>
                        <option value="weekly:WED">Todos los miércoles</option>
                        <option value="weekly:THU">Todos los jueves</option>
                        <option value="weekly:FRI">Todos los viernes</option>
                        <option value="weekly:SAT">Todos los sábados</option>
                        <option value="weekly:SUN">Todos los domingos</option>
                        <option value="daily">Diariamente</option>
                    </select>
                </div>

                <div class="aw-form-group">
                    <label>Notas / detalles</label>
                    <textarea id="aw-input-notes" placeholder="Profesor, aula, recursos…"></textarea>
                </div>

                <div class="aw-submit-row">
                    <button id="aw-submit-btn" class="aw-submit" onclick="AW._submit()">GUARDAR EVENTO</button>
                    <button class="aw-cancel-btn" onclick="AW._tab('hoy')">CANCELAR</button>
                </div>
                <div id="aw-add-feedback" class="aw-feedback"></div>
            </div>
        </div>

        <div id="aw-statusbar">
            <span id="aw-status-msg">—</span>
            <span style="color:#2a3050">AGENDA v0.3</span>
        </div>`;
    }

    /* ------------------------------------------------------------------ */
    /* Init                                                                 */
    /* ------------------------------------------------------------------ */
    function init() {
        _standalone = document.body.dataset.agendaStandalone === '1';
        let el = document.getElementById('agenda-window');
        if (!el) {
            el = document.createElement('div');
            el.id = 'agenda-window';
            document.body.appendChild(el);
        }
        el.innerHTML = buildHTML();
        win       = el;
        titlebar  = document.getElementById('aw-titlebar');
        statusMsg = document.getElementById('aw-status-msg');
        if (_standalone) {
            // Ventana aparte: siempre visible (CSS fuerza display:flex), sin drag,
            // y auto-carga porque no hay un toggle externo que la abra.
            win.classList.add('open');
            refresh();
            // Sync en vivo: el main window emite 'agenda:changed' cuando el chat
            // crea/borra eventos por voz. Antes solo el sidebar lo escuchaba y
            // esta ventana quedaba congelada hasta el SYNC manual.
            try {
                const listen = window.__TAURI__ && window.__TAURI__.event && window.__TAURI__.event.listen;
                if (listen) listen('agenda:changed', () => refresh());
            } catch (_) { /* noop */ }
            // Red de seguridad: poll periódico + refresh al recuperar el foco.
            setInterval(() => refresh(), 30000);
            window.addEventListener('focus', () => refresh());
        } else {
            initDrag();
        }
        window.addEventListener('zephyro:auth-changed', () => {
            if (_standalone || win.classList.contains('open')) refresh();
        });
        // Escape key: cancel editing or close modals
        document.addEventListener('keydown', e => {
            if (e.key !== 'Escape') return;
            const modal = document.getElementById('aw-confirm-modal') || document.getElementById('aw-recur-modal');
            if (modal) { modal.remove(); return; }
            if (_editingId !== null) { cancelEdit(); return; }
        });
    }

    /* ------------------------------------------------------------------ */
    /* Public API                                                           */
    /* ------------------------------------------------------------------ */
    window.AW = {
        open, close, toggle, refresh,
        openDetail(eventId) {
            open();
            switchTab('semana');
            setTimeout(() => toggleDetail(eventId), 400);
        },
        _tab:          switchTab,
        _toggleDetail: toggleDetail,
        _deleteRow:    deleteRow,
        _editRow:      editRow,
        _cancelEdit:   cancelEdit,
        _saveEdit:     saveEdit,
        _submit:       submitAdd,
        _search:       setSearchQuery,
        _filterCat:    setFilterCat,
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
