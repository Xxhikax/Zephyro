const APP = {
    mode: 'local',
    provider: 'auto',
    wsState: window.WS_STATES?.DISCONNECTED || 'DISCONNECTED',
    wsLatencyMs: null,
    booted: false,
    inputLocked: true,
    lastStatus: null,
    pendingMessageAt: null,
    processingLineTimer: null,
    statusPoller: null,
    historyCache: [],
    historyFilter: 'all',
    historyCurrentConv: null,
    isMicRecording: false,
    micRecorder: null,
    micStream: null,
    micChunks: [],
    callMode: false,
    callLoopRunning: false,
    _callResponseResolve: null,
    logBuffer: [],
    refs: {},

    init() {
        this.refs.terminal = document.getElementById('terminal-output');
        this.refs.input = document.getElementById('message-input');
        this.refs.sendBtn = document.getElementById('send-btn');
        this.refs.mode = document.getElementById('mode-value');
        this.refs.wsLatency = document.getElementById('ws-latency');
        this.refs.coreStatus = document.getElementById('core-status');
        this.refs.wsStatus = document.getElementById('ws-state');
        this.refs.modeSelector = document.getElementById('mode-selector');
        this.refs.providerSelector = document.getElementById('provider-selector');
        this.refs.providerApplyBtn = document.getElementById('provider-apply-btn');
        this.refs.historyBtn = document.getElementById('history-btn');
        this.refs.micBtn = document.getElementById('mic-btn');
        this.refs.callBtn = document.getElementById('call-btn');
        this.refs.version = document.getElementById('system-version');
        this.refs.logout = document.getElementById('logout-btn');

        // History panel refs
        this.refs.historyOverlay = document.getElementById('history-overlay');
        this.refs.historyList = document.getElementById('history-list');
        this.refs.historySearch = document.getElementById('history-search');
        this.refs.historyCloseBtn = document.getElementById('history-close-btn');
        this.refs.historyStatsBar = document.getElementById('history-stats-bar');
        this.refs.histStatTotal = document.getElementById('hist-stat-total');
        this.refs.histStatImportant = document.getElementById('hist-stat-important');

        // Conversation detail modal refs
        this.refs.convDetailModal = document.getElementById('conv-detail-modal');
        this.refs.convDetailTitle = document.getElementById('conv-detail-title');
        this.refs.convDetailBody = document.getElementById('conv-detail-body');
        this.refs.convDetailClose = document.getElementById('conv-detail-close');
        this.refs.convDetailDelete = document.getElementById('conv-detail-delete');
        this.refs.convDetailResume = document.getElementById('conv-detail-resume');

        this.refs.sendBtn?.addEventListener('click', () => this.onSubmit());
        this.refs.input?.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') {
                event.preventDefault();
                this.onSubmit();
            }
        });

        this.refs.modeSelector?.addEventListener('change', (event) => {
            this.mode = event.target.value;
            this.updateTopBar();
            this.updateModuleStatus('AUTH', 'CONNECTED', `mode ${this.mode}`);
            this.print('SYSTEM', `Modo actualizado a ${this.mode}.`);
        });

        this.refs.providerApplyBtn?.addEventListener('click', () => {
            this.setProvider(this.refs.providerSelector?.value || 'auto');
        });

        // Botones de modelo Anthropic
        document.querySelectorAll('.anthropic-model-btn').forEach(btn => {
            btn.addEventListener('click', () => this.setAnthropicModel(btn.dataset.model, btn));
        });

        this.refs.historyBtn?.addEventListener('click', () => this.showHistory());
        this.refs.micBtn?.addEventListener('click', () => this.toggleMicrophone());
        this.refs.callBtn?.addEventListener('click', () => this.toggleCallMode());

        // History panel events
        this.refs.historyCloseBtn?.addEventListener('click', () => this.closeHistoryPanel());
        this.refs.historyOverlay?.addEventListener('click', (e) => {
            if (e.target === this.refs.historyOverlay) this.closeHistoryPanel();
        });
        this.refs.historySearch?.addEventListener('input', (e) => {
            clearTimeout(this._searchTimer);
            this._searchTimer = setTimeout(() => this.onHistorySearch(e.target.value.trim()), 350);
        });

        // Filter chips
        document.querySelectorAll('.filter-chip').forEach((chip) => {
            chip.addEventListener('click', () => {
                document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
                chip.classList.add('active');
                this.historyFilter = chip.dataset.filter || 'all';
                this.renderHistoryList();
            });
        });

        // Conversation detail modal events
        this.refs.convDetailClose?.addEventListener('click', () => this.closeConvDetailModal());
        this.refs.convDetailModal?.addEventListener('click', (e) => {
            if (e.target === this.refs.convDetailModal) this.closeConvDetailModal();
        });
        this.refs.convDetailDelete?.addEventListener('click', () => this.deleteCurrentConversation());
        this.refs.convDetailResume?.addEventListener('click', () => this.resumeCurrentConversation());

        this.refs.logout?.addEventListener('click', () => {
            if (typeof window.auth?.logout === 'function') {
                window.auth.logout();
            }
        });

        if (this.refs.version && window.APP_VERSION) {
            this.refs.version.textContent = window.APP_VERSION;
        }

        window.wsClient.onStateChange((state, meta) => this.onWsStateChange(state, meta));
        window.wsClient.onMessage((payload) => this.onWsMessage(payload));

        this.updateTopBar();
    },

    async start() {
        this.inputLocked = true;
        this.booted = false;
        this.clearTerminal();

        const token = window.auth?.getToken?.();
        window.wsClient.setToken(token);

        await this.runBootSequence();
        this.inputLocked = false;
        this.booted = true;
        if (this.statusPoller) clearInterval(this.statusPoller);
        this.statusPoller = setInterval(() => this.refreshSystemStatus(), 15000);
        this.print('SYSTEM', 'Terminal operativo. Esperando comandos.');
        this.print('SYSTEM', 'Comandos: /status /mode /provider /history /mic /call /clear /restart /logs');
        this.refs.input?.focus();
    },

    shutdown() {
        this.inputLocked = true;
        this.booted = false;
        this.stopMicrophone();
        this.callMode = false;
        if (this.refs.callBtn) {
            this.refs.callBtn.classList.remove('active');
            this.refs.callBtn.textContent = 'CALL OFF';
        }
        window.wsClient.disconnect();
        if (this.statusPoller) {
            clearInterval(this.statusPoller);
            this.statusPoller = null;
        }
        this.clearTerminal();
        this.wsLatencyMs = null;
        this.wsState = window.WS_STATES.DISCONNECTED;
        this.updateTopBar();
    },

    async runBootSequence() {
        const boot = async (line, fn = null, delay = 500) => {
            this.print('BOOT', line);
            if (typeof fn === 'function') await fn();
            await this.sleep(delay);
        };

        await boot('Initializing core...');
        await boot('Connecting WebSocket...', async () => {
            window.wsClient.connect();
        }, 680);
        await boot('Syncing REST modules...', async () => {
            await this.refreshSystemStatus();
        }, 620);
        await boot('System ready.', null, 260);
    },

    sleep(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
    },

    clearTerminal() {
        if (this.refs.terminal) this.refs.terminal.innerHTML = '';
    },

    sanitize(text) {
        return String(text)
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;');
    },

    print(label, text) {
        if (!this.refs.terminal) return;

        const line = document.createElement('div');
        line.className = `terminal-line label-${String(label || '').toLowerCase()}`;
        line.innerHTML = `<span class="label">[${this.sanitize(label)}]</span> ${this.sanitize(text)}`;
        this.refs.terminal.appendChild(line);
        this.refs.terminal.scrollTop = this.refs.terminal.scrollHeight;

        this.logBuffer.push(`[${label}] ${text}`);
        if (this.logBuffer.length > 200) this.logBuffer.shift();
    },

    printResponse(text, interactionId) {
        if (!this.refs.terminal) return;

        const line = document.createElement('div');
        line.className = 'terminal-line label-response';
        line.innerHTML = `<span class="label">[RESPONSE]</span> ${this.sanitize(text)}`;

        if (interactionId) {
            const fb = document.createElement('span');
            fb.className = 'feedback-btns';
            fb.innerHTML = `<button class="fb-btn fb-good" title="Buena respuesta" data-id="${interactionId}">+</button>`
                         + `<button class="fb-btn fb-bad"  title="Mala respuesta"  data-id="${interactionId}">-</button>`;
            fb.querySelector('.fb-good').addEventListener('click', () => this.sendFeedback(interactionId, 1, fb));
            fb.querySelector('.fb-bad' ).addEventListener('click', () => this.sendFeedback(interactionId, -1, fb));
            line.appendChild(fb);
        }

        this.refs.terminal.appendChild(line);
        this.refs.terminal.scrollTop = this.refs.terminal.scrollHeight;
        this.logBuffer.push(`[RESPONSE] ${text}`);
        if (this.logBuffer.length > 200) this.logBuffer.shift();
    },

    async sendFeedback(interactionId, score, btnContainer) {
        if (!window.auth?.getToken?.()) return;
        try {
            await window.auth.authFetch(`${API_BASE}/api/feedback`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ interaction_id: interactionId, score, feedback_type: 'explicit' }),
            });
            // Deshabilitar botones tras votar
            if (btnContainer) {
                btnContainer.querySelectorAll('.fb-btn').forEach(b => { b.disabled = true; b.style.opacity = '0.4'; });
                const mark = score === 1 ? '+' : '-';
                btnContainer.querySelector(score === 1 ? '.fb-good' : '.fb-bad').style.opacity = '1';
            }
        } catch (_) { /* falla silenciosamente */ }
    },

    onWsStateChange(state, meta = {}) {
        this.wsState = state;
        this.updateTopBar();

        if (state === window.WS_STATES.CONNECTED) {
            this.print('SYSTEM', 'Canal WebSocket activo.');
            this.updateModuleStatus('WS CHAT', 'CONNECTED');
        } else if (state === window.WS_STATES.RECONNECTING) {
            const attempt = meta.attempts ? ` intento ${meta.attempts}` : '';
            this.print('SYSTEM', `Reconectando WebSocket...${attempt}`);
            this.updateModuleStatus('WS CHAT', 'RECONNECTING');
        } else if (state === window.WS_STATES.DISCONNECTED) {
            this.print('ERROR', 'WebSocket desconectado.');
            this.updateModuleStatus('WS CHAT', 'DISCONNECTED');
        } else if (state === window.WS_STATES.ERROR) {
            this.print('ERROR', 'WebSocket en estado ERROR.');
            this.updateModuleStatus('WS CHAT', 'ERROR');
        }
    },

    onWsMessage(payload) {
        if (payload?.type === 'typing') {
            this.print('CORE', 'processing...');
            return;
        }

        if (payload?.type === 'response') {
            this.resolveLatency();
            const responseText = payload.response || '';
            this.printResponse(responseText || '(sin respuesta)', payload.interaction_id || null);
            this.refreshSystemStatus();
            if (this._callResponseResolve) {
                this._callResponseResolve(responseText);
            } else if (this.callMode && typeof playAudio === 'function') {
                playAudio(responseText);
            }
            return;
        }

        if (payload?.type === 'error' || payload?.error) {
            this.resolveLatency();
            this.print('ERROR', payload.error || 'Error desconocido');
            return;
        }
    },

    startLatencyClock() {
        this.pendingMessageAt = performance.now();
    },

    resolveLatency() {
        if (this.pendingMessageAt == null) return;
        this.wsLatencyMs = Math.max(1, Math.round(performance.now() - this.pendingMessageAt));
        this.pendingMessageAt = null;
        this.updateTopBar();
    },

    updateTopBar() {
        if (this.refs.mode) this.refs.mode.textContent = String(this.mode).toUpperCase();
        if (this.refs.wsLatency) {
            if (this.wsState === window.WS_STATES?.CONNECTED) {
                this.refs.wsLatency.textContent = this.wsLatencyMs ? `${this.wsLatencyMs}ms` : 'ONLINE';
                this.refs.wsLatency.dataset.state = 'CONNECTED';
            } else {
                this.refs.wsLatency.textContent = '--';
                this.refs.wsLatency.dataset.state = this.wsState || 'DISCONNECTED';
            }
        }
        if (this.refs.wsStatus) {
            this.refs.wsStatus.textContent = this.wsState;
            this.refs.wsStatus.dataset.state = this.wsState;
        }
    },

    updateCoreStatus(text, tone = 'connected') {
        if (!this.refs.coreStatus) return;
        this.refs.coreStatus.textContent = text;
        this.refs.coreStatus.dataset.state = tone;
    },

    updateModuleStatus(name, state, hint = '') {
        const row = document.querySelector(`.module-item[data-module="${name}"]`);
        if (!row) return;

        row.dataset.state = state;
        const valueEl = row.querySelector('.module-state');
        if (valueEl) valueEl.textContent = state;
        if (hint) row.title = hint;
    },

    async refreshSystemStatus() {
        const token = window.auth?.getToken?.();
        if (!token) {
            this.updateCoreStatus('OFFLINE', 'disconnected');
            this.updateModuleStatus('AUTH', 'DISCONNECTED');
            return;
        }

        try {
            const response = await window.auth.authFetch(`${API_BASE}/api/status`);
            if (!response.ok) {
                if (response.status === 401 && typeof window.handleUnauthorized === 'function') {
                    window.handleUnauthorized();
                }
                throw new Error('status no disponible');
            }

            const data = await response.json();
            this.lastStatus = data;

            const localAvailable = !!data?.models?.local_available;
            this.updateCoreStatus(localAvailable ? 'ONLINE' : 'DEGRADED', localAvailable ? 'connected' : 'reconnecting');

            this.updateModuleStatus('AUTH', 'CONNECTED', data?.user?.role || 'guest');
            this.updateModuleStatus('REST VOICE', 'CONNECTED');
            this.updateModuleStatus('REST VISION', 'CONNECTED');
            this.updateModuleStatus('REST FILES', 'CONNECTED');
            this.updateModuleStatus('HISTORY', data?.user?.role === 'admin' ? 'CONNECTED' : 'DISCONNECTED');

            if (data?.models?.cloud_provider) {
                this.provider = data.models.cloud_provider;
                if (this.refs.providerSelector) this.refs.providerSelector.value = this.provider;

                // Mostrar/ocultar selector de modelo Anthropic
                const anthropicSection = document.getElementById('anthropic-model-section');
                if (anthropicSection) {
                    anthropicSection.style.display = this.provider === 'anthropic' ? 'block' : 'none';
                }
            }
            // Sincronizar botón activo del modelo Anthropic
            if (data?.models?.anthropic_model) {
                document.querySelectorAll('.anthropic-model-btn').forEach(btn => {
                    btn.classList.toggle('active', btn.dataset.model === data.models.anthropic_model);
                });
                const lbl = document.getElementById('anthropic-model-active');
                if (lbl) lbl.textContent = data.models.anthropic_model;
            }

            if (this.wsState !== window.WS_STATES.CONNECTED) {
                this.updateModuleStatus('WS CHAT', this.wsState);
            }
        } catch (error) {
            console.error('[APP] refresh status error:', error);
            this.updateCoreStatus('OFFLINE', 'error');
            this.updateModuleStatus('REST VOICE', 'ERROR');
            this.updateModuleStatus('REST VISION', 'ERROR');
            this.updateModuleStatus('REST FILES', 'ERROR');
            this.updateModuleStatus('AUTH', 'ERROR');
            this.updateModuleStatus('HISTORY', 'ERROR');
        }
    },

    async onSubmit() {
        if (this.inputLocked || !this.booted) return;

        const raw = this.refs.input?.value || '';
        const message = raw.trim();
        if (!message) return;

        this.refs.input.value = '';

        if (message.startsWith('/')) {
            await this.executeCommand(message);
            return;
        }

        await this.dispatchMessage(message, 'USER');
    },

    async dispatchMessage(message, source = 'USER') {
        this.print(source, `> ${message}`);
        this.print('SYSTEM', 'transmitting via WebSocket...');

        this.startLatencyClock();

        if (this.processingLineTimer) clearTimeout(this.processingLineTimer);
        this.processingLineTimer = setTimeout(() => this.print('CORE', 'processing...'), 300);

        const sent = window.wsClient.sendChat(message, this.mode);
        if (sent) return;

        this.print('SYSTEM', 'WebSocket no disponible. Fallback REST /api/chat/send');
        await this.sendViaRest(message);
    },

    async sendViaRest(message) {
        try {
            const response = await window.auth.authFetch(`${API_BASE}/api/chat/send`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message, mode: this.mode })
            });

            const data = await response.json();
            this.resolveLatency();

            if (!response.ok || !data?.success) {
                this.print('ERROR', data?.error || 'Fallo en /api/chat/send');
                return;
            }

            const responseText = data.response || '(sin respuesta)';
            this.printResponse(responseText, data.interaction_id || null);
            if (this._callResponseResolve) this._callResponseResolve(responseText);
            await this.refreshSystemStatus();
        } catch (error) {
            this.resolveLatency();
            this.print('ERROR', error.message || 'Error REST');
        }
    },

    async setProvider(provider) {
        const role = window.auth?.getRole?.();
        if (role !== 'admin') {
            this.print('ERROR', 'Solo admin puede cambiar proveedor cloud.');
            return;
        }

        try {
            const response = await window.auth.authFetch(`${API_BASE}/api/settings/provider?provider=${encodeURIComponent(provider)}`, {
                method: 'POST'
            });
            const data = await response.json();
            if (!response.ok || !data?.success) {
                this.print('ERROR', data?.detail || data?.message || 'No se pudo cambiar provider');
                return;
            }

            this.provider = provider;
            this.print('SYSTEM', `Cloud provider => ${provider}`);

            // Mostrar/ocultar selector de modelo Anthropic
            const anthropicSection = document.getElementById('anthropic-model-section');
            if (anthropicSection) {
                anthropicSection.style.display = provider === 'anthropic' ? 'block' : 'none';
            }

            await this.refreshSystemStatus();
        } catch (error) {
            this.print('ERROR', error.message || 'Error al cambiar provider');
        }
    },

    async setAnthropicModel(model, clickedBtn) {
        try {
            const response = await window.auth.authFetch(
                `${API_BASE}/api/settings/anthropic-model?model=${encodeURIComponent(model)}`,
                { method: 'POST' }
            );
            const data = await response.json();
            if (!response.ok || !data?.success) {
                this.print('ERROR', data?.detail || data?.message || 'No se pudo cambiar modelo');
                return;
            }
            document.querySelectorAll('.anthropic-model-btn').forEach(b => b.classList.remove('active'));
            if (clickedBtn) clickedBtn.classList.add('active');
            const lbl = document.getElementById('anthropic-model-active');
            if (lbl) lbl.textContent = model;
            this.print('SYSTEM', `Modelo Anthropic => ${model}`);
        } catch (error) {
            this.print('ERROR', error.message || 'Error al cambiar modelo');
        }
    },

    async showHistory() {
        this.refs.historyBtn?.classList.add('active');
        this.refs.historyOverlay?.classList.add('open');

        if (this.refs.historyList) {
            this.refs.historyList.innerHTML = '<div class="history-loading">Loading conversations...</div>';
        }

        try {
            const response = await window.auth.authFetch(`${API_BASE}/api/history/list?limit=50`);
            const data = await response.json();

            if (!response.ok || !data?.success) {
                if (this.refs.historyList) {
                    this.refs.historyList.innerHTML = `<div class="history-empty">${data?.detail || data?.error || 'Could not load history.'}</div>`;
                }
                return;
            }

            this.historyCache = data.conversations || [];

            // Stats
            if (this.refs.histStatTotal) this.refs.histStatTotal.textContent = `Total: ${data.total || this.historyCache.length}`;
            if (this.refs.histStatImportant) this.refs.histStatImportant.textContent = `Important: ${data.important_count || 0}`;

            this.renderHistoryList();
        } catch (error) {
            if (this.refs.historyList) {
                this.refs.historyList.innerHTML = `<div class="history-empty">Error: ${error.message}</div>`;
            }
        }
    },

    closeHistoryPanel() {
        this.refs.historyOverlay?.classList.remove('open');
        this.refs.historyBtn?.classList.remove('active');
        if (this.refs.historySearch) this.refs.historySearch.value = '';
        this.historyFilter = 'all';
        document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
        document.querySelector('.filter-chip[data-filter="all"]')?.classList.add('active');
    },

    renderHistoryList() {
        if (!this.refs.historyList) return;

        let filtered = this.historyCache;

        if (this.historyFilter !== 'all') {
            filtered = filtered.filter(c => (c.importance_level || 'trivial') === this.historyFilter);
        }

        if (!filtered.length) {
            this.refs.historyList.innerHTML = '<div class="history-empty">No conversations found.</div>';
            return;
        }

        this.refs.historyList.innerHTML = filtered.map((conv) => {
            const title = conv.title || conv.messages?.[0]?.user_message?.slice(0, 80) || 'Untitled';
            const preview = conv.messages?.[0]?.user_message || '';
            const level = conv.importance_level || 'trivial';
            const date = conv.created_at ? new Date(conv.created_at).toLocaleString('es-ES', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '--';
            const msgCount = conv.message_count || conv.messages?.length || 0;

            return `<div class="history-card" data-conv-id="${conv.id}">
                <div class="history-card-top">
                    <span class="history-card-title">${this.sanitize(title)}</span>
                    <span class="history-card-badge ${level}">${level.toUpperCase()}</span>
                </div>
                <div class="history-card-preview">${this.sanitize(preview.slice(0, 120))}</div>
                <div class="history-card-meta">
                    <span>\u25f7 ${date}</span>
                    <span>\u2709 ${msgCount} msgs</span>
                    ${conv.saved_to_memory ? '<span style="color:var(--accent)">\u2605 MEM</span>' : ''}
                </div>
            </div>`;
        }).join('');

        // Bind click events to each card
        this.refs.historyList.querySelectorAll('.history-card').forEach((card) => {
            card.addEventListener('click', () => {
                const id = card.dataset.convId;
                this.openConvDetail(id);
            });
        });
    },

    async onHistorySearch(query) {
        if (!query) {
            this.renderHistoryList();
            return;
        }

        try {
            const response = await window.auth.authFetch(`${API_BASE}/api/history/search?query=${encodeURIComponent(query)}&limit=30`, {
                method: 'POST'
            });
            const data = await response.json();

            if (!response.ok || !data?.success) {
                return;
            }

            // Map search results (individual messages) into displayable cards
            const results = data.results || [];
            if (!results.length) {
                if (this.refs.historyList) {
                    this.refs.historyList.innerHTML = '<div class="history-empty">No results found.</div>';
                }
                return;
            }

            // Render search results
            this.refs.historyList.innerHTML = results.map((msg) => {
                const preview = msg.user_input || '';
                const date = msg.timestamp ? new Date(msg.timestamp).toLocaleString('es-ES', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }) : '--';
                const level = msg.importance_level || 'trivial';

                return `<div class="history-card" data-conv-id="${msg.id}">
                    <div class="history-card-top">
                        <span class="history-card-title">${this.sanitize(preview.slice(0, 80))}</span>
                        <span class="history-card-badge ${level}">${level.toUpperCase()}</span>
                    </div>
                    <div class="history-card-preview">${this.sanitize((msg.assistant_response || '').slice(0, 120))}</div>
                    <div class="history-card-meta">
                        <span>\u25f7 ${date}</span>
                    </div>
                </div>`;
            }).join('');

            this.refs.historyList.querySelectorAll('.history-card').forEach((card) => {
                card.addEventListener('click', () => this.openConvDetail(card.dataset.convId));
            });
        } catch (error) {
            // silently fail
        }
    },

    async openConvDetail(id) {
        if (!id) return;

        // Try to find in cache first (grouped conversations have messages array)
        const cached = this.historyCache.find(c => String(c.id) === String(id));

        if (cached && cached.messages && cached.messages.length > 0) {
            this.historyCurrentConv = cached;
            this.renderConvDetail(cached.title || 'Conversation', cached.messages, id);
            return;
        }

        // Fetch from API
        try {
            const response = await window.auth.authFetch(`${API_BASE}/api/history/conversation/${encodeURIComponent(id)}`);
            const data = await response.json();

            if (!response.ok || !data?.success || !data?.conversation) {
                this.print('ERROR', data?.error || 'Could not load conversation');
                return;
            }

            const conv = data.conversation;
            this.historyCurrentConv = conv;

            // Single conversation entry
            const messages = [{
                user_message: conv.user_input,
                assistant_response: conv.assistant_response,
                timestamp: conv.timestamp
            }];

            this.renderConvDetail(conv.conversation_title || conv.user_input?.slice(0, 50) || 'Conversation', messages, id);
        } catch (error) {
            this.print('ERROR', error.message);
        }
    },

    renderConvDetail(title, messages, convId) {
        if (this.refs.convDetailTitle) this.refs.convDetailTitle.textContent = title;
        if (this.refs.convDetailDelete) this.refs.convDetailDelete.dataset.convId = convId;
        if (this.refs.convDetailResume) this.refs.convDetailResume.dataset.convId = convId;

        if (this.refs.convDetailBody) {
            this.refs.convDetailBody.innerHTML = messages.map((msg) => {
                let html = '';
                if (msg.user_message) {
                    html += `<div class="conv-msg user-msg">
                        <div class="conv-msg-label">\u25b8 USER</div>
                        <div class="conv-msg-text">${this.sanitize(msg.user_message)}</div>
                    </div>`;
                }
                if (msg.assistant_response) {
                    html += `<div class="conv-msg assistant-msg">
                        <div class="conv-msg-label">\u25b8 Zephyro</div>
                        <div class="conv-msg-text">${this.sanitize(msg.assistant_response)}</div>
                    </div>`;
                }
                return html;
            }).join('');
        }

        this.refs.convDetailModal?.classList.add('open');
    },

    closeConvDetailModal() {
        this.refs.convDetailModal?.classList.remove('open');
        this.historyCurrentConv = null;
    },

    async deleteCurrentConversation() {
        const id = this.refs.convDetailDelete?.dataset.convId;
        if (!id) return;

        try {
            const response = await window.auth.authFetch(`${API_BASE}/api/history/delete/${encodeURIComponent(id)}`, {
                method: 'DELETE'
            });
            const data = await response.json();

            if (!response.ok || !data?.success) {
                this.print('ERROR', data?.detail || data?.error || 'Could not delete');
                return;
            }

            this.print('SYSTEM', `Conversation #${id} deleted.`);
            this.closeConvDetailModal();

            // Remove from cache and re-render
            this.historyCache = this.historyCache.filter(c => String(c.id) !== String(id));
            this.renderHistoryList();
        } catch (error) {
            this.print('ERROR', error.message);
        }
    },

    resumeCurrentConversation() {
        const conv = this.historyCurrentConv;
        if (!conv) return;

        // Get the last user message to pre-fill or just print the conversation context
        const messages = conv.messages || [];
        if (messages.length) {
            this.closeConvDetailModal();
            this.closeHistoryPanel();
            this.print('SYSTEM', `Resumed conversation: ${conv.title || conv.conversation_title || ''}`);
            messages.forEach((msg) => {
                if (msg.user_message) this.print('USER', `> ${msg.user_message}`);
                if (msg.assistant_response) this.print('RESPONSE', msg.assistant_response);
            });
            this.print('SYSTEM', 'Context loaded. Continue typing.');
        } else {
            // Single message
            this.closeConvDetailModal();
            this.closeHistoryPanel();
            if (conv.user_input) this.print('USER', `> ${conv.user_input}`);
            if (conv.assistant_response) this.print('RESPONSE', conv.assistant_response);
            this.print('SYSTEM', 'Context loaded. Continue typing.');
        }
        this.refs.input?.focus();
    },

    async loadHistoryConversation(id) {
        if (!id) {
            this.print('ERROR', 'Uso: /history load <id>');
            return;
        }

        try {
            const response = await window.auth.authFetch(`${API_BASE}/api/history/conversation/${encodeURIComponent(id)}`);
            const data = await response.json();

            if (!response.ok || !data?.success) {
                this.print('ERROR', data?.detail || data?.error || 'No se pudo cargar conversación');
                return;
            }

            const messages = data?.conversation?.messages || [];
            if (!messages.length) {
                this.print('SYSTEM', `Conversación #${id} vacía.`);
                return;
            }

            this.print('SYSTEM', `Cargando conversación #${id}`);
            messages.forEach((msg) => {
                if (msg.user_message) this.print('USER', `> ${msg.user_message}`);
                if (msg.assistant_response) this.print('RESPONSE', msg.assistant_response);
            });
        } catch (error) {
            this.print('ERROR', error.message || 'Error al cargar conversación');
        }
    },

    async toggleMicrophone() {
        if (this.isMicRecording) {
            this.stopMicrophone();
            return;
        }

        try {
            this.micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
            this.micChunks = [];
            this.micRecorder = new MediaRecorder(this.micStream, { mimeType: 'audio/webm' });
            this.isMicRecording = true;
            this.refs.micBtn?.classList.add('active');
            if (this.refs.micBtn) this.refs.micBtn.textContent = 'MIC REC';
            this.print('SYSTEM', 'Micrófono grabando... click MIC para detener.');

            this.micRecorder.ondataavailable = (event) => {
                if (event.data?.size > 0) this.micChunks.push(event.data);
            };

            this.micRecorder.onstop = async () => {
                const blob = new Blob(this.micChunks, { type: 'audio/webm' });
                await this.transcribeAndDispatch(blob, false);
                this.micChunks = [];
            };

            this.micRecorder.start();
        } catch (error) {
            this.print('ERROR', `Micrófono: ${error.message}`);
            this.stopMicrophone();
        }
    },

    stopMicrophone() {
        if (this.micRecorder && this.micRecorder.state !== 'inactive') {
            this.micRecorder.stop();
        }
        this.micStream?.getTracks?.().forEach((track) => track.stop());
        this.micStream = null;
        this.isMicRecording = false;
        this.refs.micBtn?.classList.remove('active');
        if (this.refs.micBtn) this.refs.micBtn.textContent = 'MIC';
    },

    async toggleCallMode() {
        this.callMode = !this.callMode;
        this.refs.callBtn?.classList.toggle('active', this.callMode);
        if (this.refs.callBtn) this.refs.callBtn.textContent = this.callMode ? 'CALL ON' : 'CALL OFF';

        if (!this.callMode) {
            this._callResponseResolve = null;
            this.print('SYSTEM', 'Modo llamada continua desactivado.');
            return;
        }

        this.print('SYSTEM', 'Modo llamada continua activado.');
        if (!this.callLoopRunning) this.runCallLoop();
    },

    async runCallLoop() {
        this.callLoopRunning = true;
        while (this.callMode) {
            try {
                const blob = await this.captureAudioChunk(4000);
                if (!blob) { await this.sleep(500); continue; }

                // Create promise BEFORE sending to avoid race condition
                const responsePromise = new Promise((resolve) => {
                    this._callResponseResolve = resolve;
                });

                const dispatched = await this.transcribeAndDispatch(blob, true);

                if (!dispatched) {
                    // STT failed or silence — don't wait 30s for a response that won't come
                    this._callResponseResolve = null;
                    continue;
                }

                const responseText = await Promise.race([
                    responsePromise,
                    this.sleep(30000).then(() => null),
                ]);
                this._callResponseResolve = null;

                // Await TTS so mic doesn't pick up Zephyro's own voice
                if (responseText && typeof playAudio === 'function') {
                    await playAudio(responseText);
                }
            } catch (error) {
                this._callResponseResolve = null;
                this.print('ERROR', `CALL loop: ${error.message}`);
                await this.sleep(1200);
            }
        }
        this._callResponseResolve = null;
        this.callLoopRunning = false;
    },

    captureAudioChunk(durationMs = 3500) {
        return new Promise(async (resolve, reject) => {
            let stream;
            try {
                stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                const recorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
                const chunks = [];

                recorder.ondataavailable = (event) => {
                    if (event.data?.size > 0) chunks.push(event.data);
                };

                recorder.onstop = () => {
                    stream.getTracks().forEach((track) => track.stop());
                    if (!chunks.length) {
                        resolve(null);
                        return;
                    }
                    resolve(new Blob(chunks, { type: 'audio/webm' }));
                };

                recorder.onerror = (event) => {
                    stream.getTracks().forEach((track) => track.stop());
                    reject(new Error(event.error?.message || 'Error MediaRecorder'));
                };

                recorder.start();
                setTimeout(() => {
                    if (recorder.state !== 'inactive') recorder.stop();
                }, durationMs);
            } catch (error) {
                if (stream) stream.getTracks().forEach((track) => track.stop());
                reject(error);
            }
        });
    },

    async transcribeAndDispatch(audioBlob, fromCallMode) {
        if (!window.auth?.getToken?.()) return false;

        const formData = new FormData();
        formData.append('audio', audioBlob, fromCallMode ? 'call.webm' : 'mic.webm');

        try {
            const response = await fetch(`${API_BASE}/api/voice/stt`, {
                method: 'POST',
                headers: window.auth.authHeaders(),
                body: formData
            });
            const data = await response.json();

            if (!response.ok || !data?.success || !data?.text?.trim()) {
                // Always show real errors; suppress only normal silence in call mode
                const isSilence = data?.error === 'Silencio detectado' || data?.error === 'No se detectó voz en el audio';
                if (!fromCallMode || !isSilence) {
                    if (data?.error) this.print('SYSTEM', `STT: ${data.error}`);
                }
                return false;
            }

            const text = data.text.trim();
            this.print('SYSTEM', `STT => ${text}`);
            await this.dispatchMessage(text, 'USER');
            return true;
        } catch (error) {
            this.print('ERROR', `STT error: ${error.message}`);
            return false;
        }
    },

    async executeCommand(raw) {
        const [command, arg1, arg2] = raw.toLowerCase().split(/\s+/);

        if (command === '/clear') {
            this.clearTerminal();
            this.print('SYSTEM', 'Terminal limpiada.');
            return;
        }

        if (command === '/status') {
            await this.refreshSystemStatus();
            this.print('SYSTEM', `WS=${this.wsState} | MODE=${this.mode.toUpperCase()} | LAT=${this.wsLatencyMs ? `${this.wsLatencyMs}ms` : '--'} | PROVIDER=${this.provider}`);
            return;
        }

        if (command === '/mode') {
            if (!arg1 || !['local', 'cloud', 'auto'].includes(arg1)) {
                this.print('ERROR', 'Uso: /mode local|cloud|auto');
                return;
            }

            this.mode = arg1;
            if (this.refs.modeSelector) this.refs.modeSelector.value = arg1;
            this.updateTopBar();
            this.print('SYSTEM', `Modo cambiado a ${arg1.toUpperCase()}.`);
            return;
        }

        if (command === '/provider') {
            if (!arg1) {
                this.print('SYSTEM', `Provider actual: ${this.provider}`);
                return;
            }
            await this.setProvider(arg1);
            return;
        }

        if (command === '/history') {
            if (arg1 === 'load' && arg2) {
                await this.loadHistoryConversation(arg2);
            } else if (arg1 === 'close') {
                this.closeHistoryPanel();
            } else {
                await this.showHistory();
            }
            return;
        }

        if (command === '/mic') {
            await this.toggleMicrophone();
            return;
        }

        if (command === '/call') {
            await this.toggleCallMode();
            return;
        }

        if (command === '/restart') {
            this.print('SYSTEM', 'Reiniciando canal táctico...');
            window.wsClient.restart();
            await this.refreshSystemStatus();
            return;
        }

        if (command === '/logs') {
            this.print('SYSTEM', 'Últimos logs locales:');
            this.logBuffer.slice(-12).forEach((line) => this.print('SYSTEM', line));
            return;
        }

        this.print('ERROR', `Comando desconocido: ${raw}`);
    }
};

window.addMessageToChat = (role, message) => {
    if (!APP.booted) return;
    if (role === 'assistant') APP.print('RESPONSE', message);
    else if (role === 'user') APP.print('USER', `> ${message}`);
    else APP.print('SYSTEM', message);
};

window.updateStatus = () => APP.refreshSystemStatus();
window.ZephyroTerminalApp = APP;

document.addEventListener('DOMContentLoaded', () => {
    APP.init();
});

// ============================================================
// TTS — usable from call mode (APP object) and legacy code
// ============================================================

function cleanTextForTTS(text) {
    return String(text)
        .replace(/```[\s\S]*?```/g, ' código omitido ')
        .replace(/`([^`]+)`/g, '$1')
        .replace(/\*\*([^*]+)\*\*/g, '$1')
        .replace(/\*([^*]+)\*/g, '$1')
        .replace(/#{1,6}\s/g, '')
        .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
        .replace(/[*_~>#|-]/g, '')
        .replace(/\n{2,}/g, '. ')
        .replace(/\n/g, ' ')
        .replace(/\s{2,}/g, ' ')
        .trim();
}

async function playAudio(text) {
    if (!text) return;

    let cleanText = cleanTextForTTS(text);
    const MAX_TTS_LENGTH = 500;
    if (cleanText.length > MAX_TTS_LENGTH) cleanText = cleanText.substring(0, MAX_TTS_LENGTH) + '...';
    if (!cleanText || cleanText.length < 2) return;

    if (!window.auth?.getToken?.()) return;

    try {
        const formData = new FormData();
        formData.append('text', cleanText);

        const response = await fetch(`${API_BASE}/api/voice/tts`, { method: 'POST', headers: window.auth.authHeaders(), body: formData });
        if (!response.ok) return;

        const data = await response.json();
        if (!data.success || !data.audio) return;

        // Primary path: data URI
        try {
            const audio = new Audio(`data:audio/wav;base64,${data.audio}`);
            await audio.play();
            await new Promise((resolve) => { audio.onended = resolve; audio.onerror = resolve; });
            return;
        } catch (_) { /* fall through to blob */ }

        // Blob fallback
        const raw = atob(data.audio);
        const buf = new Uint8Array(raw.length);
        for (let i = 0; i < raw.length; i++) buf[i] = raw.charCodeAt(i);
        const url = URL.createObjectURL(new Blob([buf], { type: 'audio/wav' }));
        const blobAudio = new Audio(url);
        blobAudio.onended = () => URL.revokeObjectURL(url);
        try {
            await blobAudio.play();
            await new Promise((resolve) => { blobAudio.onended = resolve; blobAudio.onerror = resolve; });
        } catch (e) {
            URL.revokeObjectURL(url);
        }
    } catch (error) {
        console.error('[TTS] playAudio error:', error);
    }
}
