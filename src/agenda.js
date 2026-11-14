/* Agenda panel — sidebar con grupos HOY / MAÑANA / PRÓXIMOS. */
(function () {
    const API = (typeof API_BASE !== 'undefined') ? API_BASE : 'http://localhost:8000';

    const AGENDA = {
        listEl: null,
        refreshBtn: null,
        _refreshTimer: null,
        _fetching: false,

        init() {
            this.listEl = document.getElementById('agenda-list');
            this.refreshBtn = document.getElementById('agenda-refresh-btn');
            if (!this.listEl) return;

            if (this.refreshBtn) {
                this.refreshBtn.addEventListener('click', () => this.refresh());
            }

            window.addEventListener('zephyro:auth-changed', () => this.refresh());

            // Sync en vivo: la ventana aparte de Agenda emite 'agenda:changed'
            // tras cada CRUD; refrescamos el sidebar al instante.
            try {
                const listen = window.__TAURI__ && window.__TAURI__.event && window.__TAURI__.event.listen;
                if (listen) listen('agenda:changed', () => this.refresh());
            } catch (_e) { /* noop */ }

            this.refresh();
            // Store timer ID so it can be cancelled if needed
            this._refreshTimer = setInterval(() => {
                if (this.getToken()) this.refresh();
            }, 60000);
        },

        destroy() {
            if (this._refreshTimer !== null) {
                clearInterval(this._refreshTimer);
                this._refreshTimer = null;
            }
        },

        getToken() {
            try {
                if (window.auth && typeof window.auth.getToken === 'function') {
                    return window.auth.getToken() || '';
                }
                return localStorage.getItem('authToken') || '';
            } catch (_e) {
                return '';
            }
        },

        async refresh() {
            const token = this.getToken();
            if (!token) {
                this.renderEmpty('sin sesión');
                return;
            }
            // Prevent concurrent fetches
            if (this._fetching) return;
            this._fetching = true;
            if (this.refreshBtn) this.refreshBtn.disabled = true;

            const now = new Date();
            const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
            const end = new Date(start.getTime() + 7 * 86400000);

            const url = `${API}/api/schedule/events`
                + `?from_=${encodeURIComponent(start.toISOString())}`
                + `&to=${encodeURIComponent(end.toISOString())}`;

            try {
                const resp = await window.auth.authFetch(url);
                if (!resp.ok) {
                    this.renderEmpty(`error ${resp.status}`);
                    return;
                }
                let data;
                try {
                    data = await resp.json();
                } catch (_parseErr) {
                    this.renderEmpty('respuesta inválida');
                    return;
                }
                this.render(data.events || []);
            } catch (_err) {
                this.renderEmpty('offline');
            } finally {
                this._fetching = false;
                if (this.refreshBtn) this.refreshBtn.disabled = false;
            }
        },

        render(events) {
            const now = new Date();
            const localKey = d => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
            const todayKey    = localKey(now);
            const tomorrowKey = localKey(new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1));

            const groups = { hoy: [], manana: [], proximos: [] };
            for (const ev of events) {
                const key = localKey(new Date(ev.start_iso));
                if (key === todayKey) groups.hoy.push(ev);
                else if (key === tomorrowKey) groups.manana.push(ev);
                else groups.proximos.push(ev);
            }

            const parts = [];
            parts.push(this.renderGroup('HOY', groups.hoy));
            parts.push(this.renderGroup('MAÑANA', groups.manana));
            parts.push(this.renderGroup('PRÓXIMOS DÍAS', groups.proximos));
            this.listEl.innerHTML = parts.join('');
        },

        renderGroup(label, events) {
            let inner;
            if (!events.length) {
                inner = `<div class="agenda-empty">— sin eventos —</div>`;
            } else {
                inner = events.map(ev => {
                    const dt = new Date(ev.start_iso);
                    const cat = (ev.category || 'other').replace(/[^a-z_]/gi, '');
                    const title = this.escape(ev.title || '');
                    const pad = n => String(n).padStart(2, '0');
                    const startFmt = `${pad(dt.getHours())}:${pad(dt.getMinutes())}`;
                    const endDt = ev.end_iso ? new Date(ev.end_iso) : null;
                    const timeRange = endDt
                        ? `${startFmt} – ${pad(endDt.getHours())}:${pad(endDt.getMinutes())}`
                        : startFmt;
                    const recurIcon = ev.parent_id ? ' <span class="agenda-recur-icon">↻</span>' : '';
                    return `<div class="agenda-event cat-${cat}">`
                        + `<span class="agenda-ev-title" title="${title}">${title}${recurIcon}</span>`
                        + `<span class="agenda-ev-time">${timeRange}</span>`
                        + `</div>`;
                }).join('');
            }
            return `<div class="agenda-group">`
                + `<div class="agenda-group-label">┌─ ${label}</div>`
                + inner
                + `</div>`;
        },

        renderEmpty(reason) {
            this.listEl.innerHTML =
                `<div class="agenda-empty">— ${reason} —</div>`;
        },

        escape(s) {
            return String(s).replace(/[&<>"']/g, c => ({
                '&': '&amp;', '<': '&lt;', '>': '&gt;',
                '"': '&quot;', "'": '&#39;'
            }[c]));
        },
    };

    window.AGENDA = AGENDA;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => AGENDA.init());
    } else {
        AGENDA.init();
    }
})();
