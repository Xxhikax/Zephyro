/* notifications.js — Sistema de notificaciones nativas para Agenda y Canvas.
   - Escucha eventos WebSocket del ProactiveNotifier (reminders, briefings)
   - Muestra notificaciones nativas de Windows via Tauri plugin
   - Muestra toast in-app como fallback visual con diseño mejorado
*/
const NotificationSystem = {
    enabled: true,
    _initDone: false,

    init() {
        if (this._initDone) return;
        this._initDone = true;

        if (window.wsClient) {
            wsClient.onMessage((payload) => this.handleWsMessage(payload));
        }

        this.requestPermission();
    },

    async requestPermission() {
        if (typeof window.__TAURI__ === 'undefined') return;
        try {
            const granted = await window.__TAURI__.notification.isPermissionGranted();
            if (!granted) {
                await window.__TAURI__.notification.requestPermission();
            }
        } catch (e) {
            console.warn('[Notifications] Permiso no disponible:', e);
        }
    },

    handleWsMessage(payload) {
        if (!this.enabled) return;

        if (payload?.type === 'reminder') {
            this.showReminder(payload);
        } else if (payload?.type === 'briefing') {
            this.showBriefing(payload);
        }
    },

    async showReminder(payload) {
        if (!this.enabled) return;

        const { subtype, title, message, category_label, icon, mins_remaining } = payload;

        let notificationTitle, notificationBody;
        const fallbackIcon = icon || { schedule_event: '📅', canvas_assignment: '📝', canvas_missing: '⚠️', reminder: '⏰' }[subtype] || '🔔';

        if (subtype === 'schedule_event') {
            const timeStr = mins_remaining <= 0 ? 'AHORA' : `En ${mins_remaining} min`;
            notificationTitle = `${fallbackIcon} ${title}`;
            notificationBody = `${timeStr}  ·  ${category_label || 'Agenda'}`;
        } else if (subtype === 'canvas_assignment') {
            notificationTitle = `${fallbackIcon} Próxima entrega`;
            notificationBody = `${title || message}`;
        } else if (subtype === 'canvas_missing') {
            notificationTitle = `${fallbackIcon} Entrega faltante`;
            notificationBody = title || message || '';
        } else {
            notificationTitle = `${fallbackIcon} Recordatorio`;
            notificationBody = title || message || '';
        }

        // Toast in-app (siempre visible)
        this._showToast(notificationTitle, notificationBody, subtype);

        // Notificación nativa via Tauri
        this._sendNative(notificationTitle, notificationBody);
    },

    async showBriefing(payload) {
        if (!this.enabled) return;

        const text = payload.text || '';
        const firstLine = text.split('\n')[0] || 'Briefing del día';
        const title = '🌅 Briefing del Día';
        const body = firstLine.substring(0, 200);

        this._showToast(title, body, 'briefing');
        this._sendNative(title, body);
    },

    async _sendNative(title, body) {
        if (typeof window.__TAURI__ === 'undefined') return;
        try {
            await window.__TAURI__.notification.sendNotification({
                title: title,
                body: body,
            });
        } catch (e) {
            // Silencioso — la notificación in-app ya se mostró
        }
    },

    _showToast(title, body, type) {
        const wrapper = document.createElement('div');
        wrapper.className = 'ntf-toast';
        wrapper.innerHTML = `<strong>${this._esc(title)}</strong><span>${this._esc(body)}</span>`;

        const colors = {
            schedule_event: ['#00df80', '#0d1117'],
            canvas_assignment: ['#58a6ff', '#0d1117'],
            canvas_missing: ['#f78166', '#0d1117'],
            reminder: ['#d2a8ff', '#0d1117'],
            briefing: ['#f0c040', '#0d1117'],
        };
        const [bg, fg] = colors[type] || ['#30363d', '#c9d1d9'];

        Object.assign(wrapper.style, {
            position: 'fixed',
            top: '80px',
            right: '20px',
            maxWidth: '380px',
            padding: '14px 18px',
            borderRadius: '10px',
            background: bg,
            color: fg,
            fontSize: '13px',
            fontWeight: '500',
            zIndex: '99999',
            boxShadow: '0 4px 24px rgba(0,0,0,0.55)',
            border: '1px solid rgba(255,255,255,0.1)',
            display: 'flex',
            flexDirection: 'column',
            gap: '4px',
            animation: 'ntfSlideIn 0.35s cubic-bezier(0.4, 0, 0.2, 1)',
            pointerEvents: 'none',
        });

        document.body.appendChild(wrapper);

        setTimeout(() => {
            wrapper.style.animation = 'ntfSlideOut 0.25s ease-in forwards';
            setTimeout(() => wrapper.remove(), 260);
        }, 5000);
    },

    _esc(s) {
        if (!s) return '';
        return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }
};

// Inyectar keyframes si no existen
if (!document.getElementById('ntf-keyframes')) {
    const style = document.createElement('style');
    style.id = 'ntf-keyframes';
    style.textContent = `
        @keyframes ntfSlideIn {
            from { opacity: 0; transform: translateX(60px); }
            to   { opacity: 1; transform: translateX(0); }
        }
        @keyframes ntfSlideOut {
            from { opacity: 1; transform: translateX(0); }
            to   { opacity: 0; transform: translateX(80px); }
        }
    `;
    document.head.appendChild(style);
}

window.NotificationSystem = NotificationSystem;
