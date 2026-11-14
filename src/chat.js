const APP = {
    mode: 'local',
    provider: 'auto',
    anthropicModel: null,
    deepseekModel: null,
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
    isTtsSpeaking: false,
    isProcessing: false,
    currentRequestId: null,
    currentTtsAudio: null,
    statusBubbleEl: null,
    _statusBubbleShownAt: 0,
    _statusBubbleClearTimer: null,
    _interruptCandidate: false,
    _cooldownUntil: 0,
    _ttsGen: 0,
    _ttsAbortCtrl: null,
    _awaitingResponseAudio: null,  // request_id cuyo audio (push tts_audio) esperamos en call mode
    lastResponseText: '',          // texto del último response (para la transcripción del audio empujado)
    // Streaming TTS state (P4 Fase 2 — tts_chunk consumer)
    _streamTtsActive: false,       // per-turn flag: true when stream_tts was sent for this turn
    _streamTtsGen: 0,              // independent invalidation counter (NOT shared with _ttsGen)
    _streamTtsRequestId: null,     // request_id of the active streaming turn (stale-chunk guard)
    _streamTtsSharedCtx: null,     // AudioContext pre-created in startCallMode (user gesture, never suspended)
    _streamTtsCtx: null,           // per-request AudioContext (points to shared or lazily-created)
    _streamTtsAnalyser: null,      // shared AnalyserNode feeding the orb visualizer
    _streamTtsEnd: 0,              // absolute ctx.currentTime tail for gapless scheduling
    _streamTtsQueue: [],           // scheduled AudioBufferSourceNodes for cancellation
    _streamTtsTimer: null,         // W1 watchdog setTimeout handle
    _streamTtsText: '',            // accumulated spoken text for consolidated transcript
    _streamTtsEnded: false,        // finalize-once gate (true when is_last or watchdog fires)
    _stopPhrases: { es: [], en: [] },
    _exitPhrases: { es: [], en: [] },
    CALL_BARGE_IN_MAX_WORDS: 3,
    CALL_EXIT_MAX_WORDS: 5,
    CALL_BARGE_IN_MAX_MS: 1800,
    CALL_COOLDOWN_MS: 500,
    callStream: null,
    callAudioContext: null,
    callAnalyser: null,
    callVadInterval: null,
    callRecorder: null,
    callRecordingChunks: [],
    callSpeechStarted: false,
    callSilenceStart: 0,
    callSpeechDuration: 0,
    callSafetyTimer: null,
    lastTranscriptions: [],
    _callMimeTypeCache: undefined,
    _speechStart: 0,
    CALL_VAD_THRESHOLD: 0.03,
    // 1100ms saves ~100ms per turn vs the previous 1200ms while still
    // preserving phrase-ending tolerance. The earlier 400ms was too aggressive
    // (cut off endings); 1100ms is the conservative landing after testing.
    CALL_SILENCE_MS: 1100,
    _vadConfirmCount: 0,     // frames consecutivos por encima del umbral (gate anti-ruido)
    CALL_MIN_SPEECH_MS: 250,
    CALL_MAX_SEGMENT_MS: 30000,
    CALL_SAFETY_TIMEOUT_MS: 3600000,
    ttsEnabled: false,
    sttEngine: 'local',
    ttsEngine: 'local',
    reasoningMode: false,
    visionWatching: false,
    currentAttachment: null,
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
        this.refs.voiceBtn = document.getElementById('voice-btn');
        this.refs.ttsBtn = document.getElementById('tts-btn');
        this.refs.callBtn = document.getElementById('call-btn');
        this.refs.callOverlay = document.getElementById('call-overlay');
        this.refs.callMatrixCanvas = document.getElementById('call-matrix-bg');
        this.refs.callLabel = document.querySelector('.call-label');
        this.refs.callOrb = document.querySelector('.call-orb');
        this.refs.newConvBtn = document.getElementById('new-conv-btn');
        this.refs.version = document.getElementById('system-version');
        this.refs.logout = document.getElementById('logout-btn');
        this.refs.attachImgBtn = document.getElementById('attach-img-btn');
        this.refs.visionBtn = document.getElementById('vision-btn');
        this.refs.imageFileInput = document.getElementById('image-file-input');
        this.refs.attachmentBar = document.getElementById('attachment-bar');
        this.refs.attachmentName = document.getElementById('attachment-name');
        this.refs.attachmentThumb = document.getElementById('attachment-thumb');
        this.refs.attachmentRemove = document.getElementById('attachment-remove');
        this.refs.terminalPanel = document.querySelector('.terminal-panel');

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
        document.getElementById('pomo-stop-btn')?.addEventListener('click', () => {
            if (this.refs.input) this.refs.input.value = 'para el pomodoro';
            this.onSubmit();
        });
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
            this._syncCallMeta();
            this.print('SYSTEM', `Modo actualizado a ${this.mode}.`);
        });

        this.refs.providerApplyBtn?.addEventListener('click', () => {
            this.setProvider(this.refs.providerSelector?.value || 'auto');
        });

        // Show/hide the provider's model section immediately when dropdown changes
        this.refs.providerSelector?.addEventListener('change', () => {
            this._syncModelSections(this.refs.providerSelector.value);
        });

        // Model buttons (old .anthropic-model-btn + .model-row inside each provider section)
        document.querySelectorAll('.anthropic-model-btn').forEach(btn => {
            btn.addEventListener('click', () => this.setAnthropicModel(btn.dataset.model, btn));
        });
        document.querySelectorAll('.model-section .model-row[data-model]').forEach(row => {
            const provider = row.closest('.model-section')?.dataset.provider;
            row.addEventListener('click', () => {
                if (provider === 'deepseek') this.setDeepseekModel(row.dataset.model);
                else this.setAnthropicModel(row.dataset.model, row);
            });
        });

        // Routing tier toggle (seg-toggle)
        document.querySelectorAll('.seg-toggle-btn[data-mode]').forEach(btn => {
            btn.addEventListener('click', () => {
                const val = btn.dataset.mode;
                // Map local/cloud to select value
                const mapped = val === 'cloud' ? 'cloud' : 'local';
                if (this.refs.modeSelector) this.refs.modeSelector.value = mapped;
                this.mode = mapped;
                this.updateTopBar();
                this.updateModuleStatus('AUTH', 'CONNECTED', `mode ${this.mode}`);
                this._syncCallMeta();
                this.print('SYSTEM', `Modo actualizado a ${this.mode}.`);
                // Update toggle visual
                document.querySelectorAll('.seg-toggle-btn[data-mode]').forEach(b => {
                    b.classList.toggle('active', b.dataset.mode === val);
                });
            });
        });

        // Botones de STT engine
        document.querySelectorAll('.stt-engine-btn').forEach(btn => {
            btn.addEventListener('click', () => this.setSttEngine(btn.dataset.engine, btn));
        });

        // Botones de TTS engine
        document.querySelectorAll('.tts-engine-btn').forEach(btn => {
            btn.addEventListener('click', () => this.setTtsEngine(btn.dataset.engine, btn));
        });

        this.refs.reasonBtn = document.getElementById('reason-btn');
        this.refs.historyBtn?.addEventListener('click', () => this.showHistory());
        this.refs.callBtn?.addEventListener('click', () => this.toggleCallMode());
        this.refs.ttsBtn?.addEventListener('click', () => this.toggleTTS());
        this.refs.reasonBtn?.addEventListener('click', () => this.toggleReasoning());
        this.refs.newConvBtn?.addEventListener('click', () => this.newConversation());

        // Image attach button
        this.refs.attachImgBtn?.addEventListener('click', () => this.refs.imageFileInput?.click());
        this.refs.imageFileInput?.addEventListener('change', (e) => this.onImageSelected(e));
        this.refs.attachmentRemove?.addEventListener('click', () => this.clearAttachment());

        // Vision continuous button
        this.refs.visionBtn?.addEventListener('click', () => this.toggleContinuousVision());

        // Drag-and-drop on terminal panel
        const tp = this.refs.terminalPanel;
        if (tp) {
            tp.addEventListener('dragover', (e) => { e.preventDefault(); tp.classList.add('drag-over'); });
            tp.addEventListener('dragleave', () => tp.classList.remove('drag-over'));
            tp.addEventListener('drop', (e) => { e.preventDefault(); tp.classList.remove('drag-over'); this.onImageDrop(e); });
        }

        // Paste image from clipboard
        document.addEventListener('paste', (e) => this.onImagePaste(e));

        // Push-to-talk: hold VOICE button to record, release to transcribe
        const vBtn = this.refs.voiceBtn;
        if (vBtn) {
            const startVoice = (e) => { e.preventDefault(); this.startVoiceCapture(); };
            const stopVoice = (e) => { e.preventDefault(); this.stopVoiceCapture(); };
            vBtn.addEventListener('mousedown', startVoice);
            vBtn.addEventListener('touchstart', startVoice, { passive: false });
            vBtn.addEventListener('mouseup', stopVoice);
            vBtn.addEventListener('mouseleave', stopVoice);
            vBtn.addEventListener('touchend', stopVoice, { passive: false });
            vBtn.addEventListener('touchcancel', stopVoice, { passive: false });
        }

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

        if (window.NotificationSystem) window.NotificationSystem.init();

        this.updateTopBar();
    },

    async start() {
        this.inputLocked = true;
        this.booted = false;
        this.clearTerminal();

        const token = window.auth?.getToken?.();
        window.wsClient.setToken(token);

        // Cold boot is a fresh conversation: clear any stale server-side short-term
        // context left over from a prior frontend session that reused this token.
        this._resetServerConversation();

        await this.runBootSequence();
        this.inputLocked = false;
        this.booted = true;
        if (this.statusPoller) clearInterval(this.statusPoller);
        // UX-01: Usar Page Visibility API para pausar polling cuando la pestaña está oculta
        this.statusPoller = setInterval(() => {
            if (!document.hidden) this.refreshSystemStatus();
        }, 15000);
        // Reanudar poll inmediatamente al volver a la pestaña
        if (!this._visibilityBound) {
            document.addEventListener('visibilitychange', () => {
                if (!document.hidden && this.booted) this.refreshSystemStatus();
            });
            this._visibilityBound = true;
        }
        this.print('SYSTEM', 'Terminal operativo. Esperando comandos.');
        this.print('SYSTEM', 'Comandos: /status /mode /provider /history /mic /call /diag-mic /clear /restart /logs /code');
        this.refs.input?.focus();

        // Init dashboard controller
        if (window.DashboardController && !this._dashCtrl) {
            this._dashCtrl = new window.DashboardController();
            this._dashCtrl.init();
        }
    },

    shutdown() {
        this.inputLocked = true;
        this.booted = false;
        this.stopVoiceCapture();
        this.stopCallMode();
        this.stopContinuousVision();
        this._hideCallOverlay();
        this.callMode = false;
        if (this._dashCtrl) { this._dashCtrl.destroy(); this._dashCtrl = null; }
        if (this.refs.callBtn) {
            this.refs.callBtn.classList.remove('active');
            this.refs.callBtn.textContent = 'CALL OFF';
        }
        if (this.refs.voiceBtn) {
            this.refs.voiceBtn.classList.remove('active');
            this.refs.voiceBtn.textContent = 'VOICE';
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
            await this._loadStopPhrases();
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

    stripEmoji(text) {
        // Emoji pictographs render with poor contrast against the phosphor
        // terminal theme. Strip them from displayed lines only (logBuffer
        // keeps the raw text). Plain glyphs like "✓" or "•" are not
        // Extended_Pictographic and survive.
        return String(text ?? '')
            .replace(/\p{Extended_Pictographic}/gu, '')
            .replace(/[️‍]/g, '')
            .replace(/ {2,}/g, ' ')
            .trim();
    },

    _notifyAgendaIfChanged(text) {
        // Voice/chat schedule CRUD responses ("Evento #102 agendado: ...")
        // must refresh the agenda UIs immediately — the standalone agenda
        // window has no other way to learn about externally created events.
        // A false positive only costs one extra fetch.
        const t = String(text || '');
        if (!/\bevento/i.test(t)) return;
        if (!/\b(agendad|eliminad|actualizad|modificad|reprogramad)/i.test(t)) return;
        try { window.AGENDA?.refresh?.(); } catch (_e) { /* noop */ }
        try { window.AW?.refresh?.(); } catch (_e) { /* noop */ }
        try {
            const emit = window.__TAURI__?.event?.emit;
            if (emit) emit('agenda:changed');
        } catch (_e) { /* noop */ }
    },

    print(label, text) {
        if (!this.refs.terminal) return;

        const line = document.createElement('div');
        line.className = `terminal-line label-${String(label || '').toLowerCase()}`;
        const isResponse = String(label || '').toUpperCase() === 'RESPONSE';
        if (isResponse && window.RichRender?.available()) {
            // Rich path: markdown/LaTeX via RichRender. No stripEmoji here —
            // its whitespace collapse breaks table alignment, and the backend
            // already strips emojis from responses (sanitize_response).
            line.classList.add('rich-response');
            line.innerHTML = `<span class="label">[${this.sanitize(label)}]</span> `;
            line.appendChild(window.RichRender.render(text));
        } else {
            line.innerHTML = `<span class="label">[${this.sanitize(label)}]</span> ${this.sanitize(this.stripEmoji(text))}`;
        }
        this.refs.terminal.appendChild(line);
        this.refs.terminal.scrollTop = this.refs.terminal.scrollHeight;

        this.logBuffer.push(`[${label}] ${text}`);
        if (this.logBuffer.length > 200) this.logBuffer.shift();
    },

    /**
     * Print structured data (tables, collapsible sections, badges).
     * Used for code analysis results from GitNexus.
     */
    printStructured(label, data) {
        if (!this.refs.terminal) return;

        const line = document.createElement('div');
        line.className = `terminal-line label-${String(label || '').toLowerCase()} structured-result`;

        let html = `<span class="label">[${this.sanitize(label)}]</span> `;

        if (data && data.type === 'error') {
            html += `<span class="code-badge badge-high">ERROR</span> ${this.sanitize(data.message || 'Error desconocido')}`;
        } else if (data && data.type === 'code_analysis') {
            const tool = data.tool || 'search';
            const elapsed = data.elapsed_time ? ` (${data.elapsed_time}s)` : '';
            html += `<span class="code-badge badge-tool">${this.sanitize(tool.toUpperCase())}</span>${this.sanitize(elapsed)}<br>`;

            // Render the result data as collapsible JSON
            const resultData = data.result || data.data;
            if (resultData) {
                const jsonStr = typeof resultData === 'string' ? resultData : JSON.stringify(resultData, null, 2);
                const preview = jsonStr.length > 300 ? jsonStr.substring(0, 300) + '...' : jsonStr;
                html += `<details class="code-details"><summary>Ver resultado completo</summary><pre class="code-pre">${this.sanitize(jsonStr)}</pre></details>`;
                html += `<pre class="code-pre code-preview">${this.sanitize(preview)}</pre>`;
            }
        } else if (data && data.repos) {
            // List repos response
            html += '<strong>Repositorios indexados:</strong><br>';
            if (Array.isArray(data.repos) && data.repos.length > 0) {
                data.repos.forEach(r => {
                    const name = typeof r === 'string' ? r : (r.name || r.path || JSON.stringify(r));
                    html += `<span class="code-badge badge-repo">📦</span> ${this.sanitize(String(name))}<br>`;
                });
            } else {
                html += '<em>No hay repositorios indexados.</em>';
            }
        } else {
            // Generic fallback
            html += `<pre class="code-pre">${this.sanitize(JSON.stringify(data, null, 2))}</pre>`;
        }

        line.innerHTML = html;
        this.refs.terminal.appendChild(line);
        this.refs.terminal.scrollTop = this.refs.terminal.scrollHeight;
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
        if (payload?.type === 'ack') {
            return;
        }

        if (payload?.type === 'typing') {
            if (this.processingLineTimer) {
                clearTimeout(this.processingLineTimer);
                this.processingLineTimer = null;
            }
            // Only print if timer hadn't already fired (avoids double print)
            if (!this._processingPrinted) {
                this._processingPrinted = true;
                this.print('CORE', 'processing...');
            }
            return;
        }

        if (payload?.type === 'status') {
            this._handleStatusEvent(payload);
            return;
        }

        // Frames de un turno que ya abandonamos (watchdog o cancelación). Si los
        // procesáramos, pisarían el estado del turno en curso.
        if (this._isAbandonedFrame(payload)) {
            console.warn('[ws] frame de turno abandonado, descartado:',
                         payload?.type, payload?.request_id);
            return;
        }

        if (payload?.type === 'cancelled') {
            this.resolveLatency();
            this._clearTurnWatchdog();
            this._processingPrinted = false;
            this._clearStatusBubble();
            this.isProcessing = false;
            this.currentRequestId = null;
            this.print('SYSTEM', 'Cancelado.');
            if (this.callMode) {
                window.CallState?.flashCancelled?.();
                window.CallState?.setPhase('listening');
            }
            return;
        }

        if (payload?.type === 'context:update') {
            this._handleContextUpdate(payload);
            return;
        }

        if (payload?.type === 'response') {
            this.resolveLatency();
            this._clearTurnWatchdog();
            this._processingPrinted = false;
            this._clearStatusBubble();
            this.isProcessing = false;
            this.currentRequestId = null;
            const responseText = payload.response || '(sin respuesta)';
            this.print('RESPONSE', responseText);
            this._notifyAgendaIfChanged(responseText);
            // Call mode: show Zephyro's transcript as soon as the text arrives — NOT after
            // TTS playback finishes. Single source of truth for the call transcript (the
            // streaming-TTS finalize + legacy tts_audio paths no longer append it).
            if (this.callMode && responseText && responseText !== '(sin respuesta)') {
                window.CallState?.appendTranscript({ who: 'zephyro', text: responseText });
            }
            // Feed real per-component latencies into the HUD. Backend includes
            // these in the WS payload (see routers/chat.py `_ms`). Without this,
            // the HUD only sees stt/tts and llm column stays at "—".
            if (this.callMode && window.CallState?.recordLatency) {
                if (typeof payload.llm_ms === 'number') {
                    window.CallState.recordLatency('llm', payload.llm_ms);
                }
                if (typeof payload.total_ms === 'number') {
                    window.CallState.patch({ totalAvgMs: payload.total_ms });
                }
            }
            // En call mode con TTS: siempre armar _awaitingResponseAudio.
            // Si el streaming arranca (tts_chunk llegan), toman el control y el tts_audio
            // nunca llega (backend lo omite). Si el backend hace fallback a tts_audio
            // (e.g. prompt corto < min_length), el frame llega y se reproduce normalmente.
            if (this.callMode && this.ttsEnabled) {
                this._awaitingResponseAudio = payload.request_id || true;
                this.lastResponseText = responseText;
                this._ttsGen++;
                // Liberar el flag ACÁ, en el punto de invalidación, no en el finally del
                // speakResponse viejo. Ese bump invalida un filler en vuelo (los de las
                // fases del pomodoro), que entonces sale por su early return sin pasar
                // por el reset guardado por `myGen === this._ttsGen` → isTtsSpeaking
                // quedaba en true PARA SIEMPRE, _interruptCandidate quedaba en true, y
                // todo lo que dijeras después moría en el gate de anti-overlap.
                // Soltarlo en el finally del viejo sería una carrera: una generación
                // vieja terminando tarde apagaría el flag de una NUEVA que ya está
                // hablando. Acá es síncrono y anterior a cualquier speak nuevo, y quien
                // hable después lo vuelve a poner en true por su cuenta.
                this.isTtsSpeaking = false;
                if (this.currentTtsAudio) {
                    try { this.currentTtsAudio.pause(); this.currentTtsAudio.src = ''; } catch (_) {}
                    this.currentTtsAudio = null;
                }
                window.CallState?.patch({ statusMessage: this._streamTtsActive ? 'Reproduciendo...' : 'Generando audio...' });
                // The turn watchdog was just cleared, and the audio watchdog only starts once a
                // chunk is scheduled. Cover the gap: if no audio ever arrives (synthesis stalled,
                // terminal frame lost), release call mode instead of staying on "speaking".
                if (this._streamTtsActive && !this._streamTtsTimer) {
                    const gen = this._streamTtsGen;
                    this._streamTtsTimer = setTimeout(() => {
                        if (gen === this._streamTtsGen) this._markStreamTtsLast(gen, null);
                    }, 30000);
                }
            } else if (this.ttsEnabled) {
                this.speakResponse(responseText);
            } else if (this.callMode) {
                window.CallState?.setPhase('listening');
            }
            this.refreshSystemStatus();
            return;
        }

        if (payload?.type === 'tts_audio') {
            // Audio del response empujado por el backend (call mode). Reemplaza el
            // fetch /api/voice/tts del response: no hay fetch cliente que abortar en
            // la ventana texto→audio. El barge-in / CALL OFF siguen cortando el audio
            // ya reproduciéndose (intencional), vía currentTtsAudio.
            const awaited = this._awaitingResponseAudio;
            this._awaitingResponseAudio = null;
            // Si llegó para otro request (turno viejo) o ya no esperamos, ignorar.
            if (!awaited || (typeof awaited === 'string' && payload.request_id && awaited !== payload.request_id)) {
                return;
            }
            if (!payload.success || !payload.audio) {
                if (this.callMode) {
                    this.print('WARN', 'TTS: audio no disponible — respuesta visible en texto.');
                    if (payload.error) console.warn('[tts_audio]', payload.error);
                    window.CallState?.setPhase('listening');
                }
                return;
            }
            // Backend fell back to legacy tts_audio (e.g. prompt too short for streaming).
            // Reset streaming flag so no stale chunks are awaited.
            if (this._streamTtsActive) this._streamTtsActive = false;
            this._playPushedAudio(payload.audio, this.lastResponseText || '');
            return;
        }

        if (payload?.type === 'tts_chunk') {
            this._handleTtsChunk(payload);
            return;
        }

        if (payload?.type === 'error' || payload?.error) {
            this.resolveLatency();
            this._clearTurnWatchdog();
            this._processingPrinted = false;
            this._clearStatusBubble();
            this.isProcessing = false;
            this.currentRequestId = null;
            this.print('ERROR', payload.error || 'Error desconocido');
            this._awaitingResponseAudio = null;
            if (this.callMode) window.CallState?.setPhase('listening');
            return;
        }

        if (payload?.type === 'reminder' || payload?.type === 'briefing') {
            if (window.NotificationSystem) {
                window.NotificationSystem.handleWsMessage(payload);
            }
            return;
        }

        if (payload?.type === 'pomodoro') {
            this._handlePomodoroWs(payload);
            return;
        }

        if (payload?.type === 'break_reminder') {
            this._handleBreakReminder(payload);
            return;
        }

        if (payload?.type === 'focus_alert') {
            this._handleFocusAlert(payload);
            return;
        }

        if (payload?.type === 'vision_focus_update') {
            this._handleVisionFocusUpdate(payload);
            return;
        }

        if (payload?.type === 'focus_report') {
            if (payload.report) this._showFocusReportPanel(payload.report);
            return;
        }

        if (payload?.type === 'study_mode') {
            const msg = payload.action === 'start'
                ? '[Modo estudio activado — monitoreando distracciones]'
                : '[Modo estudio desactivado]';
            this.print('SISTEMA', msg);
            return;
        }

        if (payload?.type === 'diagnostic_running') {
            this._handleDiagnosticStatus(payload);
            return;
        }

        if (payload?.type === 'verification') {
            const v = payload.verdict || '';
            const evidence = payload.rule_result?.evidence || '';
            let color = null, ttl = 8000, label = '';
            if (v === 'FAIL' || v.includes('FAIL')) {
                color = '#ff5050'; label = 'VER FAIL';
            } else if (v === 'WARN' || v.includes('WARN')) {
                color = '#ffaa00'; label = 'VER WARN';
            } else if (v.includes('[?]') || v === 'unknown') {
                color = '#3a5060'; ttl = 5000; label = 'VER ?';
            }
            if (color && evidence) {
                const line = document.createElement('div');
                line.className = 'verif-note';
                line.style.cssText = `color:${color};font-size:0.78em;padding:1px 6px;opacity:0.75;font-style:italic`;
                line.textContent = `[${label}] ${evidence}`;
                const out = document.querySelector('.terminal-output') || document.querySelector('#terminal');
                if (out) out.appendChild(line);
                setTimeout(() => line.remove(), ttl);
            }
            return;
        }
    },

    _handleDiagnosticStatus(payload) {
        const orb = document.getElementById('call-orb-container');
        if (payload.status === 'started') {
            const out = document.querySelector('.terminal-output') || document.querySelector('#terminal');
            if (out) {
                const line = document.createElement('div');
                line.id = 'diag-status-line';
                line.className = 'diag-status-line';
                const tierLabel = payload.tier ? ` · ${payload.tier}` : '';
                line.textContent = `[ DIAGNÓSTICO EN CURSO${tierLabel} ]`;
                out.appendChild(line);
            }
            orb?.classList.add('diagnostic-running');
        } else {
            document.getElementById('diag-status-line')?.remove();
            orb?.classList.remove('diagnostic-running');
            if (payload.summary) this.print('DIAGNÓSTICO', payload.summary);
            // Speak a short result in call mode: without this the user is left
            // in silence after a background diagnostic finishes (panel-only UX).
            const spoken = payload.spoken_summary || payload.summary || '';
            const muted = !!(window.CallState?.get()?.muted);
            if (this.callMode && this.ttsEnabled && !muted && spoken) {
                if (this.isTtsSpeaking) {
                    this._queueFocusSpeak(spoken);
                } else {
                    this.speakResponse(spoken, { isFiller: true });
                }
            }
        }
    },

    _handleFocusAlert(payload) {
        const level = payload.level || 1;
        const msg = payload.message || '';
        // Backend sets should_speak only for level≥2 — avoids interrupting the
        // user for the soft level-1 reminder. Older payloads default to false.
        const shouldSpeak = payload.should_speak === true;
        const muted = !!(window.CallState?.get()?.muted);

        const prefixes = { 1: 'FOCO', 2: 'FOCO', 3: 'ALERTA FOCO', 4: 'ENFORCEMENT' };
        this.print(prefixes[level] || 'FOCO', msg);

        // Badge en el orb de call mode
        const orb = document.getElementById('call-orb-container');
        if (orb) {
            orb.classList.remove('focus-l1', 'focus-l2', 'focus-l3', 'focus-l4');
            if (level < 4) orb.classList.add(`focus-l${level}`);
        }

        // Cuenta regresiva en nivel 3
        if (level === 3 && payload.enforcement_in) {
            this._startFocusCountdown(payload.enforcement_in);
        } else if (level >= 4) {
            this._clearFocusCountdown();
            if (orb) orb.classList.remove('focus-l1', 'focus-l2', 'focus-l3', 'focus-l4');
        }

        // TTS gating: call mode ON **o** un Pomodoro corriendo, TTS habilitado,
        // nivel≥2 y sin mutear. Si Zephyro está hablando, se encola para no pisarse.
        //
        // El gate exigía `callMode` y nada más, y eso rompía el propósito de la alerta:
        // medido en vivo el 2026-10-04, la de nivel 2 llegó a los 2 min mientras el
        // usuario miraba YouTube fuera de call mode — o sea que le escribió en una
        // ventana que no estaba mirando. Un aviso de distracción que solo escribe no
        // avisa nada, porque la única situación en la que importa es justo cuando la
        // atención está en otra parte.
        //
        // Por qué el Pomodoro habilita la voz y el modo estudio no: arrancar un ciclo
        // es pedir explícitamente que te vigilen. Fuera de eso, Zephyro no habla sin
        // que lo hayas iniciado. `pomodoroPhase` queda en null al completarse o
        // cancelarse la sesión, así que sirve como señal de "hay ciclo activo".
        const pomodoroActive = !!(window.CallState?.get()?.pomodoroPhase);
        if ((this.callMode || pomodoroActive) && this.ttsEnabled && shouldSpeak && !muted && msg) {
            if (this.isTtsSpeaking) {
                this._queueFocusSpeak(msg);
            } else {
                this.speakResponse(msg, { isFiller: true });
            }
        }
    },

    _queueFocusSpeak(msg) {
        this._focusSpeakQueue = this._focusSpeakQueue || [];
        this._focusSpeakQueue.push(msg);
        if (this._focusSpeakDrainInt) return;
        this._focusSpeakDrainInt = setInterval(() => {
            if (this.isTtsSpeaking) return;
            const next = this._focusSpeakQueue.shift();
            if (!next) {
                clearInterval(this._focusSpeakDrainInt);
                this._focusSpeakDrainInt = null;
                return;
            }
            if (!this.callMode || !this.ttsEnabled) {
                this._focusSpeakQueue = [];
                clearInterval(this._focusSpeakDrainInt);
                this._focusSpeakDrainInt = null;
                return;
            }
            this.speakResponse(next, { isFiller: true });
        }, 400);
    },

    _handleVisionFocusUpdate(payload) {
        // Deprecated — vision results now flow through the unified _handleFocusAlert
        // pipeline. Keeping this as a no-op for backward compatibility with any
        // backend instance that still emits the legacy event.
    },

    _startFocusCountdown(totalSec) {
        if (this._focusCdInt) clearInterval(this._focusCdInt);
        const deadline = Date.now() + totalSec * 1000;
        this._focusCdInt = setInterval(() => {
            const remaining = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
            if (remaining <= 0) {
                clearInterval(this._focusCdInt);
                this._focusCdInt = null;
                return;
            }
            // Actualizar label del countdown en terminal
            const el = document.getElementById('focus-countdown-line');
            if (el) el.textContent = `[Cerrando pestañas en ${remaining}s si no volvés al foco]`;
        }, 1000);
        // Insertar línea de countdown en el terminal
        const out = document.getElementById('terminal-output');
        if (out) {
            const line = document.createElement('div');
            line.id = 'focus-countdown-line';
            line.className = 'focus-countdown';
            line.textContent = `[Cerrando pestañas en ${totalSec}s si no volvés al foco]`;
            out.appendChild(line);
            out.scrollTop = out.scrollHeight;
        }
    },

    _clearFocusCountdown() {
        if (this._focusCdInt) {
            clearInterval(this._focusCdInt);
            this._focusCdInt = null;
        }
        const el = document.getElementById('focus-countdown-line');
        if (el) el.remove();
    },

    _handlePomodoroWs(payload) {
        const action = payload.action;
        const startedAt = payload.started_at ? Math.round(payload.started_at * 1000) : 0;
        const totalSec = payload.total_sec || 0;
        const cycle = payload.cycle || 1;
        const totalCycles = payload.total_cycles || 4;
        const task = payload.task || '';
        // New fields from _broadcast_pomodoro_state (backend); fallbacks keep
        // older payloads working without breakage.
        const phaseDetail = payload.phase_detail || payload.phase || null;
        const paused = payload.paused === true;
        const nextPhase = payload.next_phase || '';
        const nextDurationSec = payload.next_duration_sec || 0;
        const extendPendingMin = payload.extend_pending_min || 0;

        const patchBase = {
            pomodoroPhaseDetail: phaseDetail,
            pomodoroPaused: paused,
            pomodoroNextPhase: nextPhase,
            pomodoroNextDurationSec: nextDurationSec,
            pomodoroExtendPendingMin: extendPendingMin,
        };

        if (action === 'start') {
            window.CallState?.patch({
                ...patchBase,
                pomodoroStartedAt: startedAt,
                pomodoroTotalSec: totalSec,
                pomodoroCycle: cycle,
                pomodoroTotalCycles: totalCycles,
                pomodoroTask: task,
                pomodoroPhase: 'work',
            });
            if (this.callMode) window.CallState?.setPhase('pomodoro_work');
            this._showPomoBar('TRABAJO', cycle, totalCycles, task);
            this._startPomoBarTick('work', totalSec);

        } else if (action === 'end_break') {
            window.CallState?.patch({
                ...patchBase,
                pomodoroStartedAt: startedAt,
                pomodoroTotalSec: totalSec,
                pomodoroCycle: cycle,
                pomodoroTotalCycles: totalCycles,
                pomodoroTask: task,
                pomodoroPhase: 'work',
            });
            if (this.callMode) window.CallState?.setPhase('pomodoro_work');
            this._showPomoBar('TRABAJO', cycle, totalCycles, task);
            this._startPomoBarTick('work', totalSec);
            // Notificar inicio del nuevo ciclo — nombra la tarea si la hay para
            // re-anclar el foco. (El detector de distracciones se reactiva al
            // entrar en fase work, así que si el usuario no vuelve, avisa solo.)
            const backTo = task ? ` Volvemos a "${task}".` : '';
            const breakEndMsg = `Descanso terminado.${backTo} Arrancamos ciclo ${cycle} de ${totalCycles}.`;
            this.print('ZEPHYRO', breakEndMsg);
            // F2: transición clave → hablar también en chat mode (no solo call).
            if (this.ttsEnabled) this.speakResponse(breakEndMsg, { isFiller: true });

        } else if (action === 'end_work') {
            const breakMin = payload.break_min || Math.round(totalSec / 60) || 5;
            window.CallState?.patch({
                ...patchBase,
                pomodoroStartedAt: startedAt,
                pomodoroTotalSec: totalSec,
                pomodoroCycle: cycle,
                pomodoroTotalCycles: totalCycles,
                pomodoroPhase: 'break',
            });
            if (this.callMode) window.CallState?.setPhase('pomodoro_break');
            this._showPomoBar('DESCANSO', cycle, totalCycles, '');
            this._startPomoBarTick('break', totalSec);
            // Notificar fin del ciclo de trabajo
            const endWorkMsg = `Ciclo ${cycle} completado. Descanso de ${breakMin} min — descansa un poco.`;
            this.print('ZEPHYRO', endWorkMsg);
            // F2: transición clave → hablar también en chat mode (no solo call).
            if (this.ttsEnabled) this.speakResponse(endWorkMsg, { isFiller: true });

        } else if (action === 'session_complete') {
            this._hidePomoBar();
            if (this.callMode) window.CallState?.setPhase('listening');
            window.CallState?.patch({
                ...patchBase,
                pomodoroStartedAt: 0, pomodoroTotalSec: 0, pomodoroCycle: 0,
                pomodoroPhase: null, pomodoroPhaseDetail: null,
                pomodoroPaused: false, pomodoroNextPhase: '', pomodoroNextDurationSec: 0,
                pomodoroExtendPendingMin: 0,
            });
            const completeMsg = `Sesión Pomodoro completada. ${totalCycles} ciclo(s). Buen trabajo!`;
            this.print('ZEPHYRO', completeMsg);
            // F2: cierre de sesión → hablar también en chat mode (no solo call).
            if (this.ttsEnabled) this.speakResponse(completeMsg, { isFiller: true });

        } else if (action === 'cancelled') {
            this._hidePomoBar();
            if (this.callMode) window.CallState?.setPhase('listening');
            window.CallState?.patch({
                pomodoroStartedAt: 0, pomodoroTotalSec: 0, pomodoroCycle: 0,
                pomodoroPhase: null, pomodoroPhaseDetail: null,
                pomodoroPaused: false, pomodoroNextPhase: '', pomodoroNextDurationSec: 0,
                pomodoroExtendPendingMin: 0,
            });

        } else if (action === 'paused') {
            window.CallState?.patch({ ...patchBase, pomodoroPaused: true });
            const msg = 'Pomodoro pausado.';
            this.print('ZEPHYRO', msg);
            if (this.callMode && this.ttsEnabled) this.speakResponse(msg, { isFiller: true });

        } else if (action === 'resumed') {
            window.CallState?.patch({
                ...patchBase,
                pomodoroStartedAt: startedAt,  // backend restarts the deadline clock on resume
                pomodoroTotalSec: totalSec,
                pomodoroPaused: false,
            });
            const msg = 'Pomodoro reanudado.';
            this.print('ZEPHYRO', msg);
            if (this.callMode && this.ttsEnabled) this.speakResponse(msg, { isFiller: true });
            // Restart the chat-bar tick from the new deadline.
            const phaseTxt = (phaseDetail === 'work') ? 'work' : 'break';
            this._startPomoBarTick(phaseTxt, totalSec);

        } else if (action === 'extended') {
            window.CallState?.patch({
                ...patchBase,
                pomodoroStartedAt: startedAt,
                pomodoroTotalSec: totalSec,
            });
            // Re-tick the chat bar against the new total_sec.
            const phaseTxt = (phaseDetail === 'work') ? 'work' : 'break';
            this._startPomoBarTick(phaseTxt, totalSec);
        }

        // Clear any stale focus badge whenever the pomodoro phase transitions.
        // Otherwise an old focus-l2 on the orb would persist into a new work cycle.
        if (['start', 'end_work', 'end_break', 'session_complete', 'cancelled'].includes(action)) {
            const orb = document.getElementById('call-orb-container');
            if (orb) orb.classList.remove('focus-l1', 'focus-l2', 'focus-l3', 'focus-l4');
            this._clearFocusCountdown();
        }

        // Sync HUD fields in call-on mode
        this._syncPomoHud(cycle, totalCycles);
    },

    _showPomoBar(phaseLabel, cycle, totalCycles, task) {
        const bar = document.getElementById('pomo-chat-bar');
        if (!bar) return;
        // Hide the chat bar ONLY when the call overlay is actually rendering.
        // In MIN'd state (callMode=true but overlay faded out), the user is
        // looking at the chat panel — they should still see the countdown.
        // We key off overlay class state instead of `this.callMode` so the
        // bar reappears the moment MIN finishes its fade-out timeout.
        const overlay = document.getElementById('call-overlay');
        const overlayVisible = overlay
            && overlay.classList.contains('active')
            && !overlay.classList.contains('fade-out');
        if (overlayVisible) {
            bar.style.display = 'none';
            return;
        }
        const labelEl = document.getElementById('pomo-chat-label');
        const phaseEl = document.getElementById('pomo-chat-phase');
        if (labelEl) labelEl.textContent = task ? `POMO #${cycle}/${totalCycles} · ${task}` : `POMO #${cycle}/${totalCycles}`;
        if (phaseEl) phaseEl.textContent = phaseLabel;
        bar.style.display = '';
        bar.dataset.phase = phaseLabel === 'TRABAJO' ? 'work' : 'break';
        this._bindPomoBarControls();
    },

    _refreshPomoBar() {
        // Re-evaluate Pomodoro bar visibility against the current CallState.
        // Called by call-mode.js when the overlay shows/hides so the chat
        // bar can take over (or step back) without waiting for the next WS
        // event.
        const cs = window.CallState?.get() || {};
        if (!cs.pomodoroStartedAt || !cs.pomodoroPhase) {
            this._hidePomoBar();
            return;
        }
        const label = cs.pomodoroPhase === 'work' ? 'TRABAJO' : 'DESCANSO';
        this._showPomoBar(label, cs.pomodoroCycle, cs.pomodoroTotalCycles, cs.pomodoroTask);
        const remaining = Math.max(
            0,
            (cs.pomodoroTotalSec || 0) - ((Date.now() - cs.pomodoroStartedAt) / 1000),
        );
        // Only (re)start the tick if the bar is actually visible — _showPomoBar
        // may have left display='none' if the overlay is up.
        const bar = document.getElementById('pomo-chat-bar');
        if (bar && bar.style.display !== 'none') {
            this._startPomoBarTick(cs.pomodoroPhase === 'work' ? 'work' : 'break', remaining);
        }
    },

    _bindPomoBarControls() {
        if (this._pomoBarBound) return;
        this._pomoBarBound = true;
        const call = async (path, body) => {
            try {
                const url = new URL(`${API_BASE}/api${path}`);
                const opts = {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                };
                if (body) opts.body = JSON.stringify(body);
                opts.headers = { ...opts.headers, ...window.auth.authHeaders() };
                await fetch(url, opts);
            } catch (err) {
                console.warn('[pomo-bar] control failed', path, err);
            }
        };
        document.getElementById('pomo-pause-btn')?.addEventListener('click', () => {
            const paused = !!(window.CallState?.get()?.pomodoroPaused);
            call(paused ? '/pomodoro/resume' : '/pomodoro/pause');
        });
        document.getElementById('pomo-extend-btn')?.addEventListener('click', () => {
            call('/pomodoro/extend', { minutes: 5, mode: 'current' });
        });
    },

    _hidePomoBar() {
        const bar = document.getElementById('pomo-chat-bar');
        if (bar) bar.style.display = 'none';
        if (this._pomoBarInt) { clearInterval(this._pomoBarInt); this._pomoBarInt = null; }
    },

    // Bug 5: pide al backend (autoridad) el Pomodoro activo y reconstruye
    // CallState + la barra. Llamado al conectar/reconectar el WS, de modo que
    // un F5 nunca pierde el timer y un reinicio del backend no deja fantasmas.
    async _rehydratePomodoro() {
        try {
            const url = new URL(`${API_BASE}/api/pomodoro/status`);
            const res = await window.auth.authFetch(url);
            if (!res.ok) return;
            const data = await res.json();
            const CS = window.CallState;
            if (!data || !data.active) {
                CS?.patch?.({
                    pomodoroStartedAt: 0, pomodoroTotalSec: 0, pomodoroCycle: 0,
                    pomodoroTotalCycles: 0, pomodoroTask: '',
                    pomodoroPhase: null, pomodoroPhaseDetail: null, pomodoroPaused: false,
                });
                this._hidePomoBar?.();
                return;
            }
            const remainingSec = Number(data.remaining_sec) || 0;
            const totalSec = Number(data.total_sec) || 0;
            const phaseSimple = (data.phase === 'work') ? 'work' : 'break';
            // Rehidratamos con la duración TOTAL real de la fase y el inicio del
            // servidor (mismo shape que el broadcast WS 'start'). Antes se usaba
            // el restante como total + startedAt=now, lo que ENCOGÍA el pomodoro
            // a lo que quedaba en cada reconexión (45 min terminaba en 25). Con
            // total_sec + started_at el countdown queda idéntico al real.
            let startedAtMs;
            let totalForState;
            if (totalSec > 0 && data.started_at) {
                startedAtMs = Math.round(data.started_at * 1000);
                totalForState = totalSec;
            } else {
                // Fallback (backend viejo sin total_sec): comportamiento anterior.
                startedAtMs = Date.now();
                totalForState = remainingSec;
            }
            CS?.patch?.({
                pomodoroStartedAt: startedAtMs,
                pomodoroTotalSec: totalForState,
                pomodoroCycle: data.cycle || 1,
                pomodoroTotalCycles: data.total_cycles || 1,
                pomodoroTask: data.task || '',
                pomodoroPhase: phaseSimple,
                pomodoroPhaseDetail: data.phase_detail || data.phase || phaseSimple,
                pomodoroPaused: !!data.paused,
            });
            this._refreshPomoBar?.();
            if (this.callMode) CS?.setPhase?.(`pomodoro_${phaseSimple}`);
        } catch (_) { /* best-effort */ }
    },

    _startPomoBarTick(phase, totalSec) {
        if (this._pomoBarInt) { clearInterval(this._pomoBarInt); this._pomoBarInt = null; }
        const startMs = Date.now();
        const tick = () => {
            const elapsed = (Date.now() - startMs) / 1000;
            const remaining = Math.max(0, totalSec - elapsed);
            const m = String(Math.floor(remaining / 60)).padStart(2, '0');
            const s = String(Math.floor(remaining % 60)).padStart(2, '0');
            const timeEl = document.getElementById('pomo-chat-time');
            if (timeEl) timeEl.textContent = `${m}:${s}`;
            if (remaining <= 0) { clearInterval(this._pomoBarInt); this._pomoBarInt = null; }
        };
        tick();
        this._pomoBarInt = setInterval(tick, 500);
    },

    _syncPomoHud(cycle, totalCycles) {
        const field = document.getElementById('pomo-hdr-field');
        const cycleField = document.getElementById('pomo-hdr-cycle');
        const ctlGroup = document.getElementById('pomo-hdr-controls');
        const cycleEl = document.getElementById('call-pomo-cycle');
        const skipBtn = document.getElementById('call-pomo-skip-btn');
        const pauseBtn = document.getElementById('call-pomo-pause-btn');
        if (field) field.style.display = '';
        if (cycleField) cycleField.style.display = '';
        if (ctlGroup) ctlGroup.style.display = '';
        if (cycleEl) cycleEl.textContent = `${cycle}/${totalCycles}`;
        // Skip-break only meaningful during break phases — hide it otherwise.
        const cs = window.CallState?.get() || {};
        if (skipBtn) {
            skipBtn.style.display = (cs.pomodoroPhase === 'break') ? '' : 'none';
        }
        if (pauseBtn) {
            pauseBtn.textContent = cs.pomodoroPaused ? '▶' : '⏸';
        }
        // HUD countdown is driven by _startPomodoroTimer in call-mode.js
        // We dispatch an event so call-mode.js can start its interval
        window.dispatchEvent(new CustomEvent('zephyro:pomodoro-sync', {
            detail: { cycle, totalCycles }
        }));
    },

    // F2: panel de resumen al terminar/cancelar una sesión Pomodoro.
    // El backend manda `focus_report` con el texto (foco %, pausas, distracciones).
    // En vez de un simple log, lo mostramos como tarjeta dismissible en el terminal.
    _showFocusReportPanel(report) {
        // Siempre dejar el texto en el log como respaldo accesible.
        this.print('ZEPHYRO', report);
        const out = this.refs?.terminal;
        if (!out) return;
        const card = document.createElement('div');
        card.className = 'focus-report-card';
        const lines = String(report).split('\n').filter(l => l.trim());
        const title = document.createElement('div');
        title.className = 'focus-report-title';
        title.textContent = '⏹  Sesión de foco — resumen';
        card.appendChild(title);
        for (const ln of lines) {
            const row = document.createElement('div');
            row.className = 'focus-report-row';
            row.textContent = ln;
            card.appendChild(row);
        }
        // Fila de estadísticas del día (best-effort, asíncrona).
        const statsRow = document.createElement('div');
        statsRow.className = 'focus-report-row';
        statsRow.style.marginTop = '6px';
        statsRow.style.opacity = '0.7';
        card.appendChild(statsRow);
        this._fillPomodoroStats(statsRow);

        const close = document.createElement('button');
        close.className = 'focus-report-close';
        close.textContent = 'OK';
        close.addEventListener('click', () => card.remove());
        card.appendChild(close);
        out.appendChild(card);
        out.scrollTop = out.scrollHeight;
    },

    async _fillPomodoroStats(rowEl) {
        try {
            const url = new URL(`${API_BASE}/api/pomodoro/stats`);
            const res = await window.auth.authFetch(url);
            if (!res.ok) return;
            const s = await res.json();
            const today = s.today ?? 0;
            const mins = s.today_minutes ?? 0;
            const total = s.total ?? 0;
            rowEl.textContent = `📊 Hoy: ${today} pomodoro(s) · ${mins} min · total histórico: ${total}`;
        } catch (_) { /* best-effort */ }
    },

    _handleBreakReminder(payload) {
        const msg = payload.message || '¿Tomamos un descanso activo?';
        const mins = payload.elapsed_minutes || 0;
        if (this.callMode) {
            // Call mode active: just add to chat, don't open overlay
            this.print('ZEPHYRO', msg);
        } else {
            // Show break pill at bottom-center
            let pill = document.getElementById('aw-break-pill');
            if (!pill) {
                pill = document.createElement('div');
                pill.id = 'aw-break-pill';
                document.body.appendChild(pill);
            }
            pill.textContent = `🧘 ${msg}`;
            pill.classList.add('visible');
            setTimeout(() => pill.classList.remove('visible'), 8000);
            // Briefly pulse the orb container without opening call overlay
            const orb = document.getElementById('call-orb-container');
            if (orb) {
                orb.classList.add('aw-break-pulse');
                setTimeout(() => orb.classList.remove('aw-break-pulse'), 3000);
            }
        }
        console.info(`[break-reminder] ${mins} min de sesión de estudio`);
    },

    _handleStatusEvent(payload) {
        const action = payload.action || 'thinking';
        const message = payload.message || 'Procesando...';
        this.print('CORE', `${action}: ${message}`);
        this._renderStatusBubble(message);
        if (this.callMode) {
            const phase = action === 'searching' ? 'researching' : 'thinking';
            // setPhase (no patch) para que las flechas del pipeline y micGated se
            // actualicen con la fase; luego el mensaje (setPhase preserva statusMessage
            // en thinking/researching, así que el patch siguiente no se pisa).
            window.CallState?.setPhase(phase);
            window.CallState?.patch({ statusMessage: message });
            // Voice the filler aloud during call mode so the user hears
            // "déjame pensarlo un momento" instead of dead air.
            // GUARD: si el response real ya llegó (esperamos su audio empujado), NO
            // hablar un filler tardío — pisaría/cancelaría la voz del response
            // (race filler-vs-response en respuestas lentas/complejas).
            if (this.ttsEnabled && !this._awaitingResponseAudio) {
                this.speakResponse(message, { isFiller: true });
            }
        }
    },

    _handleContextUpdate(payload) {
        const chip = document.getElementById('context-chip');
        if (!chip) return;

        const project = payload.active_project;
        const url = payload.current_url;
        const window_title = payload.active_window;
        const origin = payload.detected_by || 'auto';

        let label = project;
        let icon = '◆';
        if (!label && url) {
            try {
                label = new URL(url).hostname;
                icon = '🌐';
            } catch (_) {
                label = url.slice(0, 40);
            }
        }
        if (!label && window_title) {
            label = window_title.slice(0, 32);
            icon = '▦';
        }
        if (!label) {
            chip.classList.add('context-chip--idle');
            chip.classList.remove('context-chip--manual');
            chip.querySelector('.context-chip__label').textContent = '—';
            return;
        }

        chip.classList.remove('context-chip--idle');
        chip.classList.toggle('context-chip--manual', origin === 'manual');
        chip.querySelector('.context-chip__icon').textContent = icon;
        chip.querySelector('.context-chip__label').textContent = label;
        chip.title = `${origin}: ${window_title || ''}` + (url ? `\n${url}` : '');

        // Flash animation
        chip.classList.add('context-chip--flash');
        setTimeout(() => chip.classList.remove('context-chip--flash'), 250);
    },

    // Tiempo mínimo que una burbuja de status permanece en pantalla. Evita el
    // "parpadeo" cuando la respuesta llega apenas después de mostrar el filler
    // (ej. filler a 3.5s, respuesta a 3.8s → se vería 300ms).
    STATUS_BUBBLE_MIN_MS: 1200,

    _renderStatusBubble(message) {
        // Burbuja efímera mostrada en el terminal mientras se procesa.
        // Reemplaza la anterior de inmediato (immediate=true) si ya existe.
        this._clearStatusBubble(true);
        if (!this.refs.terminal) return;
        const line = document.createElement('div');
        line.className = 'log-line status-bubble';
        line.dataset.role = 'status';
        line.textContent = `[CORE] ${message}`;
        this.refs.terminal.appendChild(line);
        this.refs.terminal.scrollTop = this.refs.terminal.scrollHeight;
        this.statusBubbleEl = line;
        this._statusBubbleShownAt = Date.now();
    },

    _clearStatusBubble(immediate = false) {
        const doRemove = () => {
            if (this._statusBubbleClearTimer) {
                clearTimeout(this._statusBubbleClearTimer);
                this._statusBubbleClearTimer = null;
            }
            if (this.statusBubbleEl && this.statusBubbleEl.parentNode) {
                this.statusBubbleEl.parentNode.removeChild(this.statusBubbleEl);
            }
            this.statusBubbleEl = null;
            this._statusBubbleShownAt = 0;
        };
        if (!this.statusBubbleEl) {
            if (this._statusBubbleClearTimer) {
                clearTimeout(this._statusBubbleClearTimer);
                this._statusBubbleClearTimer = null;
            }
            return;
        }
        const shownFor = this._statusBubbleShownAt ? Date.now() - this._statusBubbleShownAt : this.STATUS_BUBBLE_MIN_MS;
        if (!immediate && shownFor < this.STATUS_BUBBLE_MIN_MS) {
            // Diferir el borrado hasta cumplir el mínimo en pantalla.
            if (this._statusBubbleClearTimer) clearTimeout(this._statusBubbleClearTimer);
            this._statusBubbleClearTimer = setTimeout(doRemove, this.STATUS_BUBBLE_MIN_MS - shownFor);
            return;
        }
        doRemove();
    },

    async _loadStopPhrases() {
        try {
            const [stopRes, exitRes] = await Promise.all([
                fetch(`${API_BASE}/api/config/stop_phrases`),
                fetch(`${API_BASE}/api/config/exit_phrases`)
            ]);
            if (stopRes.ok) {
                const data = await stopRes.json();
                if (data && typeof data === 'object') {
                    this._stopPhrases = { es: data.es || [], en: data.en || [] };
                }
            }
            if (exitRes.ok) {
                const data = await exitRes.json();
                if (data && typeof data === 'object') {
                    this._exitPhrases = { es: data.es || [], en: data.en || [] };
                }
            }
        } catch (e) {
            console.warn('[stop_phrases] no se pudo cargar:', e?.message);
        }
    },

    _normalizePhrase(text) {
        if (!text) return '';
        return String(text).toLowerCase()
            .normalize('NFD').replace(/[̀-ͯ]/g, '')
            .replace(/[¿?¡!.,;:]/g, '')
            .trim();
    },

    // Filler phrases removed from the EDGES of an utterance before retrying a pool
    // match. Whole units only: "por favor" is one entry, so a bare "por" is never
    // stripped out of a real command.
    _EDGE_FILLERS: [
        'por favor', 'un momento', 'please',
        'momento', 'zephyro', 'che', 'ya', 'ahora',
    ],

    _normalizeForPool(text) {
        return this._normalizePhrase(text).replace(/\s+/g, ' ').trim();
    },

    _stripEdgeFillers(norm) {
        // Longest first, so "detente un momento" loses "un momento" rather than just
        // "momento" (which would leave an unmatchable "detente un").
        const fillers = this._EDGE_FILLERS.slice()
            .sort((a, b) => b.split(' ').length - a.split(' ').length || b.length - a.length);
        let s = norm;
        let changed = true;
        while (changed) {
            changed = false;
            for (const f of fillers) {
                if (s === f) continue;  // a pure-filler utterance is never stripped to nothing
                if (s.startsWith(f + ' ')) {
                    s = s.slice(f.length + 1).trim();
                    changed = true;
                }
                if (s.endsWith(' ' + f)) {
                    s = s.slice(0, s.length - f.length - 1).trim();
                    changed = true;
                }
            }
        }
        return s;
    },

    // Whole-utterance match against a phrase pool.
    //
    // This used to be an n-gram scan: a one-word pool phrase matched ANY utterance of
    // up to 3 words containing it as a contiguous run. "para" is a stop phrase, so
    // "para el pomodoro" matched it and died at the call-mode gate in
    // transcribeAndDispatch — the command never reached the backend and the Pomodoro
    // kept running. The same defect let "fin de semana" hang up the call.
    //
    // Exact match first, then one retry with edge fillers removed. Internal tokens are
    // never removed, so a command keeps its object ("el pomodoro") and fails the match.
    // Failing in that direction is deliberate: an unmatched utterance gets dispatched,
    // which is recoverable, while a false match is swallowed, which is not.
    _matchesPhrasePool(text, pool) {
        const norm = this._normalizeForPool(text);
        if (!norm) return false;
        const normPool = (pool || []).map((p) => this._normalizeForPool(p)).filter(Boolean);
        if (normPool.includes(norm)) return true;
        const stripped = this._stripEdgeFillers(norm);
        return stripped !== norm && normPool.includes(stripped);
    },

    _matchesStopPhrase(text) {
        const pool = [...(this._stopPhrases.es || []), ...(this._stopPhrases.en || [])];
        return this._matchesPhrasePool(text, pool);
    },

    _matchesExitPhrase(text) {
        const pool = [...(this._exitPhrases.es || []), ...(this._exitPhrases.en || [])];
        return this._matchesPhrasePool(text, pool);
    },

    // ── Turnos abandonados + watchdog ───────────────────────────────────────────
    // Un turno puede no volver nunca: el wait_for de 120s del backend se derrota con
    // un asyncio.to_thread no cancelable (reproducción TTS, STT), y el cliente no
    // tenía NINGÚN timeout por turno — isProcessing, currentRequestId,
    // _streamTtsActive e isTtsSpeaking solo se limpiaban con un frame o a mano.
    //
    // El watchdog es más largo que los 120s del backend A PROPÓSITO: si el backend va
    // a contestar o a tirar error, queremos SU frame. Un deadline más corto abandonaría
    // turnos sanos que tardan 100s. Esto es red de último recurso, no una política de
    // cancelación.
    TURN_WATCHDOG_MS: 150000,
    _turnWatchdogTimer: null,
    _turnWatchdogRid: null,
    _abandonedRequests: [],
    _REQUEST_SCOPED_FRAMES: ['response', 'error', 'cancelled'],

    _abandonRequest(rid) {
        if (!rid) return;
        if (!this._abandonedRequests.includes(rid)) this._abandonedRequests.push(rid);
        while (this._abandonedRequests.length > 20) this._abandonedRequests.shift();
    },

    // Los frames de un turno abandonado NO pueden tocar el estado: el handler de
    // `response` limpia isProcessing y currentRequestId sin mirar el request_id, así que
    // una respuesta vieja que llega tarde le apaga el "procesando" al turno NUEVO y le
    // roba el audio vía _awaitingResponseAudio. Mismo criterio que el guard de chunks de
    // TTS por request_id que ya existía.
    _isAbandonedFrame(payload) {
        const rid = payload?.request_id;
        if (!rid) return false;  // sin id no se puede discriminar: se procesa
        const scoped = this._REQUEST_SCOPED_FRAMES.includes(payload?.type) || !!payload?.error;
        return scoped && this._abandonedRequests.includes(rid);
    },

    _armTurnWatchdog(requestId) {
        this._clearTurnWatchdog();
        this._turnWatchdogRid = requestId || null;
        this._turnWatchdogTimer = setTimeout(() => {
            this._turnWatchdogTimer = null;
            this._onTurnWatchdogExpired(requestId || null);
        }, this.TURN_WATCHDOG_MS);
    },

    _clearTurnWatchdog() {
        if (this._turnWatchdogTimer) clearTimeout(this._turnWatchdogTimer);
        this._turnWatchdogTimer = null;
        this._turnWatchdogRid = null;
    },

    _onTurnWatchdogExpired(requestId) {
        // Si ya arrancó otro turno, este watchdog no es de nadie.
        if (requestId && this.currentRequestId && this.currentRequestId !== requestId) return;
        if (!this.isProcessing && !this._streamTtsActive && !this.isTtsSpeaking) return;
        // Marcar ANTES de cancelar: cancelCurrentRequest limpia currentRequestId.
        this._abandonRequest(requestId || this.currentRequestId);
        this.print('SYSTEM', 'El turno no respondió a tiempo — micrófono liberado.');
        // cancelCurrentRequest ya hace el teardown del streaming TTS, apaga los cuatro
        // flags, manda el cancel al backend y devuelve el orbe a 'listening'.
        this.cancelCurrentRequest('watchdog');
    },

    cancelCurrentRequest(reason = 'user') {
        // The `response` frame clears currentRequestId while streamed audio may still be
        // playing (the backend keeps synthesizing the tail): cancel that turn too, so a
        // barge-in also stops the backend, not only local playback.
        const rid = this.currentRequestId || this._streamTtsRequestId;
        // Marcar el turno como abandonado antes de limpiar su id: si el backend ya
        // tenía la respuesta en vuelo, su frame llega DESPUÉS de este cancel y sin
        // esto pisaría el estado del turno siguiente.
        this._abandonRequest(rid);
        this._clearTurnWatchdog();
        this._ttsGen++;  // invalidar cualquier speakResponse en vuelo
        this._awaitingResponseAudio = null;  // descartar audio empujado pendiente
        // Stop any active streaming TTS pipeline before doing anything else.
        if (this._streamTtsCtx || this._streamTtsActive) {
            this._teardownStreamTts({ cancelled: true });
        }
        // Abortar la fetch TTS en vuelo (Piper generando) para liberar el backend
        // de inmediato en un barge-in — si no, la generación sigue corriendo y solo
        // se descarta al volver vía el guard de _ttsGen.
        if (this._ttsAbortCtrl) { this._ttsAbortCtrl.abort(); this._ttsAbortCtrl = null; }
        try {
            if (this.currentTtsAudio) {
                this.currentTtsAudio.pause();
                this.currentTtsAudio.currentTime = 0;
                this.currentTtsAudio = null;
            }
        } catch (_) {}
        this.isTtsSpeaking = false;
        this.isProcessing = false;
        this.currentRequestId = null;
        this._clearStatusBubble();
        if (rid && window.wsClient?.sendCancel) {
            window.wsClient.sendCancel(rid);
        }
        this._cooldownUntil = Date.now() + this.CALL_COOLDOWN_MS;
        this.print('SYSTEM', `Cancelado (${reason}).`);
        if (this.callMode) {
            window.CallState?.flashCancelled?.();
            window.CallState?.setPhase('listening');
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
        // Update header dots
        const dotWs = document.getElementById('dot-ws');
        if (dotWs) {
            dotWs.className = 'dot ' + (this.wsState === window.WS_STATES?.CONNECTED ? 'green' : 'red');
        }
    },

    updateCoreStatus(text, tone = 'connected') {
        if (!this.refs.coreStatus) return;
        this.refs.coreStatus.textContent = text;
        this.refs.coreStatus.dataset.state = tone;
        // Update core dot
        const dotCore = document.getElementById('dot-core');
        if (dotCore) {
            const c = tone === 'connected' ? 'green' : tone === 'degraded' ? 'amber' : 'red';
            dotCore.className = 'dot ' + c;
        }
    },

    updateModuleStatus(name, state, hint = '') {
        // Support both old .module-item and new .mod-row
        const row = document.querySelector(`.module-item[data-module="${name}"]`)
            || document.querySelector(`.mod-row[data-module="${name}"]`);
        if (!row) return;

        row.dataset.state = state;
        const valueEl = row.querySelector('.module-state');
        if (valueEl) valueEl.textContent = state;
        // Update dot color for .mod-row
        const dot = row.querySelector('.mod-dot');
        if (dot) {
            dot.className = 'mod-dot';
            if (state === 'CONNECTED') dot.classList.add('green');
            else if (state === 'RECONNECTING' || state === 'DEGRADED') dot.classList.add('amber');
            else if (state === 'ERROR') dot.classList.add('red');
            else dot.classList.add('dim');
        }
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
                this._syncModelSections(this.provider);
            }
            // Sincronizar botón activo del modelo Anthropic
            if (data?.models?.anthropic_model) {
                this.anthropicModel = data.models.anthropic_model;
                document.querySelectorAll('.anthropic-model-btn').forEach(btn => {
                    btn.classList.toggle('active', btn.dataset.model === data.models.anthropic_model);
                });
                this._markActiveModelRow('anthropic', data.models.anthropic_model);
                const lbl = document.getElementById('anthropic-model-active');
                if (lbl) lbl.textContent = data.models.anthropic_model;
            }
            if (data?.models?.deepseek_model) {
                this.deepseekModel = data.models.deepseek_model;
                this._markActiveModelRow('deepseek', data.models.deepseek_model);
            }
            this._syncCallMeta();

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

        // If there's an attached image, send it with the prompt
        if (this.currentAttachment) {
            this.refs.input.value = '';
            await this.sendImageWithPrompt(message || 'Analiza esta imagen en detalle.');
            return;
        }

        if (!message) return;

        this.refs.input.value = '';

        if (message.startsWith('/')) {
            await this.executeCommand(message);
            return;
        }

        await this.dispatchMessage(message, 'USER');
    },

    async dispatchMessage(message, source = 'USER') {
        // Reset streaming flag defensively — an early return must not leave a stale TRUE.
        this._streamTtsActive = false;

        this.print(source, `> ${message}`);
        this.print('SYSTEM', 'transmitting via WebSocket...');

        this.startLatencyClock();

        if (this.processingLineTimer) clearTimeout(this.processingLineTimer);
        this._processingPrinted = false;
        this.processingLineTimer = setTimeout(() => {
            this.processingLineTimer = null;
            this._processingPrinted = true;
            this.print('CORE', 'processing...');
        }, 300);
        const opts = {};
        if (this.reasoningMode) opts.reasoning = true;
        if (this.lastVisionContext) opts.vision_context = this.lastVisionContext;
        // Call mode: pedir al backend que genere y empuje el audio del response.
        // stream_tts enables the streaming TTS path (tts_chunk frames) for call mode.
        if (this.callMode && this.ttsEnabled) {
            opts.tts = true;
            opts.stream_tts = true;
            this._streamTtsActive = true;
        }
        // Smart routing for call mode (see above comment on _sendMode).
        const _callSendMode = this.callMode ? 'auto' : this.mode;
        const sent = window.wsClient.sendChat(message, _callSendMode, opts);
        if (sent && this._streamTtsActive) this._streamTtsRequestId = typeof sent === 'string' ? sent : null;
        if (sent) {
            this.currentRequestId = typeof sent === 'string' ? sent : null;
            this.isProcessing = true;
            this._armTurnWatchdog(this.currentRequestId);
            if (this.callMode) window.CallState?.setPhase('thinking');
            return;
        }

        this.print('SYSTEM', 'WebSocket no disponible. Fallback REST /api/chat/send');
        if (this.callMode) window.CallState?.setPhase('thinking');
        await this.sendViaRest(message);
    },

    async sendViaRest(message) {
        try {
            const token = window.auth?.getToken?.();
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
            this.print('RESPONSE', responseText);
            if (this.ttsEnabled) this.speakResponse(responseText);
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
            const token = window.auth?.getToken?.();
            const response = await window.auth.authFetch(`${API_BASE}/api/settings/provider?provider=${encodeURIComponent(provider)}`, {
                method: 'POST'
            });
            const data = await response.json();
            if (!response.ok || !data?.success) {
                this.print('ERROR', data?.detail || data?.message || 'No se pudo cambiar provider');
                return;
            }

            this.provider = provider;
            this._syncCallMeta();
            this.print('SYSTEM', `Cloud provider => ${provider}`);
            this._syncModelSections(provider);

            await this.refreshSystemStatus();
        } catch (error) {
            this.print('ERROR', error.message || 'Error al cambiar provider');
        }
    },

    async setAnthropicModel(model, clickedBtn) {
        try {
            const token = window.auth?.getToken?.();
            const response = await window.auth.authFetch(
                `${API_BASE}/api/settings/anthropic-model?model=${encodeURIComponent(model)}`,
                { method: 'POST' }
            );
            const data = await response.json();
            if (!response.ok || !data?.success) {
                this.print('ERROR', data?.detail || data?.message || 'No se pudo cambiar modelo');
                return;
            }
            // Actualizar botones
            document.querySelectorAll('.anthropic-model-btn').forEach(b => b.classList.toggle('active', b === clickedBtn));
            this._markActiveModelRow('anthropic', model);
            const lbl = document.getElementById('anthropic-model-active');
            if (lbl) lbl.textContent = model;
            this.anthropicModel = model;
            this._syncCallMeta();
            this.print('SYSTEM', `Modelo Anthropic => ${model}`);
        } catch (error) {
            this.print('ERROR', error.message || 'Error al cambiar modelo');
        }
    },

    async setDeepseekModel(model) {
        try {
            const response = await window.auth.authFetch(
                `${API_BASE}/api/settings/deepseek-model?model=${encodeURIComponent(model)}`,
                { method: 'POST' }
            );
            const data = await response.json();
            if (!response.ok || !data?.success) {
                this.print('ERROR', data?.detail || data?.message || 'No se pudo cambiar modelo');
                return;
            }
            this._markActiveModelRow('deepseek', data.model);
            this.deepseekModel = data.model;
            this._syncCallMeta();
            this.print('SYSTEM', `Modelo DeepSeek => ${data.model}`);
        } catch (error) {
            this.print('ERROR', error.message || 'Error al cambiar modelo');
        }
    },

    // Shows only the MODEL_ROUTING section of the active provider.
    _syncModelSections(provider) {
        document.querySelectorAll('.model-section[data-provider]').forEach(section => {
            section.style.display = section.dataset.provider === provider ? 'block' : 'none';
        });
    },

    // Scoped to its own section: a global selector would clear the other provider's row.
    _markActiveModelRow(provider, model) {
        document.querySelectorAll(`.model-section[data-provider="${provider}"] .model-row[data-model]`)
            .forEach(row => row.classList.toggle('active', row.dataset.model === model));
    },

    async setSttEngine(engine, clickedBtn) {
        try {
            const token = window.auth?.getToken?.();
            const response = await window.auth.authFetch(
                `${API_BASE}/api/settings/stt-engine?engine=${encodeURIComponent(engine)}`,
                { method: 'POST' }
            );
            const data = await response.json();
            if (!response.ok || !data?.success) {
                this.print('ERROR', data?.detail || data?.message || 'No se pudo cambiar STT engine');
                return;
            }
            this.sttEngine = engine;
            document.querySelectorAll('.stt-engine-btn').forEach(b => b.classList.remove('active'));
            if (clickedBtn) clickedBtn.classList.add('active');
            this._syncCallMeta();
            this.print('SYSTEM', `STT engine => ${engine.toUpperCase()}`);
        } catch (error) {
            this.print('ERROR', error.message || 'Error al cambiar STT engine');
        }
    },

    async setTtsEngine(engine, clickedBtn) {
        try {
            const token = window.auth?.getToken?.();
            const response = await window.auth.authFetch(
                `${API_BASE}/api/settings/tts-engine?engine=${encodeURIComponent(engine)}`,
                { method: 'POST' }
            );
            const data = await response.json();
            if (!response.ok || !data?.success) {
                this.print('ERROR', data?.detail || data?.message || 'No se pudo cambiar TTS engine');
                return;
            }
            this.ttsEngine = engine;
            document.querySelectorAll('.tts-engine-btn').forEach(b => b.classList.remove('active'));
            if (clickedBtn) clickedBtn.classList.add('active');
            this._syncCallMeta();
            this.print('SYSTEM', `TTS engine => ${engine.toUpperCase()}`);
        } catch (error) {
            this.print('ERROR', error.message || 'Error al cambiar TTS engine');
        }
    },

    async showHistory() {
        this.refs.historyBtn?.classList.add('active');
        this.refs.historyOverlay?.classList.add('open');

        if (this.refs.historyList) {
            this.refs.historyList.innerHTML = '<div class="history-loading">Loading conversations...</div>';
        }

        try {
            const token = window.auth?.getToken?.();
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
            const token = window.auth?.getToken?.();
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
            const token = window.auth?.getToken?.();
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
            const token = window.auth?.getToken?.();
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
            const token = window.auth?.getToken?.();
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

    async startVoiceCapture() {
        // Ignore if already recording or call mode is running
        if (this.isMicRecording) return;

        try {
            this.micStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    sampleRate: { ideal: 16000 },
                    channelCount: { exact: 1 },
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true,
                }
            });
            this.micChunks = [];
            this.micRecorder = new MediaRecorder(this.micStream, { mimeType: 'audio/webm' });
            this.isMicRecording = true;
            this.refs.voiceBtn?.classList.add('active');
            if (this.refs.voiceBtn) this.refs.voiceBtn.textContent = '● REC';
            this.print('SYSTEM', 'Grabando... suelta para enviar.');

            this.micRecorder.ondataavailable = (event) => {
                if (event.data?.size > 0) this.micChunks.push(event.data);
            };

            this.micRecorder.onstop = async () => {
                const blob = new Blob(this.micChunks, { type: 'audio/webm' });
                this.micChunks = [];
                if (blob.size > 0) {
                    await this.transcribeAndDispatch(blob, false);
                }
            };

            this.micRecorder.start();
        } catch (error) {
            this.print('ERROR', `Micrófono: ${error.message}`);
            this.stopVoiceCapture();
        }
    },

    stopVoiceCapture() {
        if (!this.isMicRecording) return;
        try {
            if (this.micRecorder && this.micRecorder.state !== 'inactive') {
                this.micRecorder.stop();
            }
        } catch (_) { /* ignore */ }
        this.micStream?.getTracks?.().forEach((track) => track.stop());
        this.micStream = null;
        this.isMicRecording = false;
        this.refs.voiceBtn?.classList.remove('active');
        if (this.refs.voiceBtn) this.refs.voiceBtn.textContent = 'VOICE';
    },

    async _runMicDiagnostic() {
        const token = window.auth?.getToken?.();
        if (!token) {
            this.print('ERROR', 'Sin sesión.');
            return;
        }
        this.print('SYSTEM', 'DIAG-MIC: capturando 3 segundos...');
        let stream = null;
        let audioContext = null;
        try {
            stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    sampleRate: { ideal: 16000 },
                    channelCount: { exact: 1 },
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: false,
                }
            });

            // Frontend RMS sample
            audioContext = new (window.AudioContext || window.webkitAudioContext)();
            const source = audioContext.createMediaStreamSource(stream);
            const analyser = audioContext.createAnalyser();
            analyser.fftSize = 2048;
            source.connect(analyser);
            const silentGain = audioContext.createGain();
            silentGain.gain.value = 0;
            analyser.connect(silentGain);
            silentGain.connect(audioContext.destination);

            // Record 3s
            const mime = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/webm']
                .find(t => MediaRecorder.isTypeSupported(t)) || '';
            const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : {});
            const chunks = [];
            recorder.ondataavailable = (e) => { if (e.data?.size > 0) chunks.push(e.data); };
            recorder.start(100);

            // Sample frontend RMS during the 3s
            const rmsSamples = [];
            const sampleInterval = setInterval(() => {
                const buf = new Float32Array(analyser.fftSize);
                analyser.getFloatTimeDomainData(buf);
                rmsSamples.push(Math.sqrt(buf.reduce((s, v) => s + v * v, 0) / buf.length));
            }, 100);

            await this.sleep(3000);
            clearInterval(sampleInterval);

            const stopped = new Promise(res => { recorder.onstop = res; });
            recorder.stop();
            await stopped;

            const rmsAvg = rmsSamples.length ? rmsSamples.reduce((a, b) => a + b, 0) / rmsSamples.length : 0;
            const rmsMax = rmsSamples.length ? Math.max(...rmsSamples) : 0;
            this.print('SYSTEM', `DIAG-MIC frontend: rms_avg=${rmsAvg.toFixed(4)} rms_max=${rmsMax.toFixed(4)} samples=${rmsSamples.length}`);

            const blob = new Blob(chunks, { type: recorder.mimeType || mime || 'audio/webm' });
            this.print('SYSTEM', `DIAG-MIC: enviando ${blob.size} bytes al backend...`);

            const fd = new FormData();
            fd.append('audio', blob, `diag.${(recorder.mimeType || mime).includes('ogg') ? 'ogg' : 'webm'}`);
            if (this.sttEngine && this.sttEngine !== 'local') fd.append('engine', this.sttEngine);

            const res = await fetch(`${API_BASE}/api/voice/diag`, { method: 'POST', headers: window.auth.authHeaders(), body: fd });
            const data = await res.json();
            if (!res.ok || !data?.success) {
                this.print('ERROR', `DIAG-MIC backend: ${data?.error || res.statusText}`);
                return;
            }
            this.print('SYSTEM', `DIAG-MIC backend: engine=${data.engine} size=${data.size_bytes}B duration=${data.duration_s}s`);
            this.print('SYSTEM', `DIAG-MIC backend: rms_post_ffmpeg=${data.rms_post_ffmpeg} threshold_recomendado=${data.recommended_vad_threshold}`);
            if (data.transcription) {
                this.print('SYSTEM', `DIAG-MIC transcripción: "${data.transcription}"`);
            } else {
                this.print('SYSTEM', `DIAG-MIC transcripción: (vacía — no se detectó voz)`);
            }
            if (data.errors && data.errors.length) {
                this.print('ERROR', `DIAG-MIC errores: ${data.errors.join(' | ')}`);
            }
        } catch (err) {
            this.print('ERROR', `DIAG-MIC error: ${err.message}`);
        } finally {
            try { stream?.getTracks().forEach(t => t.stop()); } catch (_) {}
            try { if (audioContext && audioContext.state !== 'closed') await audioContext.close(); } catch (_) {}
        }
    },

    toggleTTS() {
        this.ttsEnabled = !this.ttsEnabled;
        if (this.refs.ttsBtn) {
            this.refs.ttsBtn.textContent = `TTS: ${this.ttsEnabled ? 'ON' : 'OFF'}`;
            this.refs.ttsBtn.classList.toggle('active', this.ttsEnabled);
        }
        // Update IO device indicator
        const ioTts = document.getElementById('io-tts');
        if (ioTts) {
            ioTts.textContent = this.ttsEnabled ? 'READY' : 'OFF';
            ioTts.className = 'io-val ' + (this.ttsEnabled ? 'ready' : 'off');
        }
        this.print('SYSTEM', `Respuesta por voz: ${this.ttsEnabled ? 'ACTIVADA' : 'DESACTIVADA'}`);
    },

    toggleReasoning() {
        this.reasoningMode = !this.reasoningMode;
        if (this.refs.reasonBtn) {
            this.refs.reasonBtn.textContent = this.reasoningMode ? 'THINK: ON' : 'THINK';
            this.refs.reasonBtn.classList.toggle('active', this.reasoningMode);
        }
        this.print('SYSTEM', `Modo razonamiento: ${this.reasoningMode ? 'ACTIVADO (más lento, más preciso)' : 'DESACTIVADO'}`);
    },

    async speakResponse(text, opts = {}) {
        const token = window.auth?.getToken?.();
        if (!token || !text?.trim()) return;

        const isFiller = !!opts.isFiller;
        const myGen = ++this._ttsGen;

        // Detener audio previo sin resetear isTtsSpeaking — seguimos en "hablando"
        if (this.currentTtsAudio) {
            try { this.currentTtsAudio.pause(); this.currentTtsAudio.src = ''; } catch (_) {}
            this.currentTtsAudio = null;
        }

        // Clean markdown/symbols for cleaner speech. Table rows, separator
        // lines and LaTeX are display-only: drop them entirely instead of
        // reading their symbols aloud.
        const cleanText = text
            .replace(/```[\s\S]*?```/g, '')
            .replace(/\$\$[\s\S]*?\$\$/g, '')
            .split('\n')
            .filter(l => !/^\s*\|/.test(l) && !/^\s*[|\-+: ]+\s*$/.test(l))
            .join(' ')
            // Inline math only when it looks like TeX — same guard as the backend's
            // _strip_inline_math. "$50 y el pro $120" is two prices, not math.
            .replace(/\$([^$\n]+)\$/g, (m, inner) => {
                const c = inner.trim();
                const isMath = /[\\^_{}=]/.test(c)
                    || (c.length <= 3 && !/\d/.test(c))
                    || /^[A-Za-z]{1,3}\([^()]{0,16}\)$/.test(c);
                return isMath ? '' : m;
            })
            .replace(/[*_`#>|]/g, '')
            .replace(/\s+/g, ' ')
            .trim()
            .slice(0, 2000); // Limit length
        if (!cleanText) { this.isTtsSpeaking = false; return; }
        // Kept for the barge-in echo guard (transcribeAndDispatch).
        this._rememberSpoken(cleanText);

        // Mantener isTtsSpeaking=true durante todo el ciclo para que el barge-in no se rompa
        this.isTtsSpeaking = true;
        if (!isFiller && this.callMode) {
            window.CallState?.patch({ statusMessage: 'Generando audio...' });
            window.CallState?.appendTranscript({ who: 'zephyro', text: cleanText });
            window.CallState?.setPhase('speaking');
        }

        // Split into sentence chunks so the FIRST audio plays in ~1.5-2s instead of
        // waiting for one big clip (Fish Audio synth time scales with length → ~20s for
        // long answers). The next chunk is synthesized WHILE the current one plays.
        const chunks = this._splitSentencesForTts(cleanText);

        this._ttsAbortCtrl?.abort();
        const _abort = new AbortController();
        this._ttsAbortCtrl = _abort;
        this._ttsTimedOut = false;
        // Stall watchdog: aborts only if a chunk's SYNTHESIS hangs (no progress for
        // _ttsStallMs), NOT after a fixed total budget. Playback of a long multi-chunk
        // answer is real-time and legitimate, so it must never trip the guard — the timer
        // is re-armed on every fetch/playback step. Fixes long answers cut off mid-speech.
        const _ttsStallMs = 45000;
        let _ttsTimeoutId = null;
        const _armStall = () => {
            if (_ttsTimeoutId) clearTimeout(_ttsTimeoutId);
            _ttsTimeoutId = setTimeout(() => { this._ttsTimedOut = true; _abort.abort(); }, _ttsStallMs);
        };
        const _ttsT0 = performance.now();
        let _firstLogged = false;

        try {
            // Prefetch pipeline: kick off chunk i+1 synthesis before awaiting playback of chunk i.
            _armStall();
            let pending = this._fetchTtsAudio(chunks[0], _abort.signal);
            for (let i = 0; i < chunks.length; i++) {
                const data = await pending;
                _armStall(); // chunk synth resolved → progress, reset the stall guard
                if (myGen !== this._ttsGen || !this.isTtsSpeaking) return;
                pending = (i + 1 < chunks.length)
                    ? this._fetchTtsAudio(chunks[i + 1], _abort.signal)
                    : Promise.resolve(null);

                if (!data?.success || !data?.audio) {
                    if (data?.error) { this.print('ERROR', `TTS: ${data.error}`); }
                    continue;
                }
                if (!_firstLogged) {
                    if (!isFiller && this.callMode) {
                        window.CallState?.recordLatency('tts', Math.round(performance.now() - _ttsT0));
                    }
                    _firstLogged = true;
                }
                await this._playTtsPiece(data.audio, myGen, _abort.signal);
                _armStall(); // chunk finished playing → progress, reset the stall guard
                if (myGen !== this._ttsGen) return;
            }
        } catch (error) {
            // El abort intencional (cerrar call / barge-in) se silencia; el corte por
            // stall de síntesis (_ttsTimedOut) SÍ se avisa para que no quede mudo sin explicación.
            if (!isFiller && this.callMode) {
                if (this._ttsTimedOut) {
                    this.print('WARN', 'TTS: la generación de voz se colgó, se cortó el audio — la tenés completa en texto.');
                } else if (error.name !== 'AbortError') {
                    this.print('WARN', `TTS: fallo de audio (${error.message}) — respuesta visible en texto.`);
                }
            }
            console.warn('[TTS]', error.name, error.message);
        } finally {
            clearTimeout(_ttsTimeoutId);
            if (this._ttsAbortCtrl === _abort) this._ttsAbortCtrl = null;
            this._ttsTimedOut = false;
        }

        if (myGen === this._ttsGen) {
            this.isTtsSpeaking = false;
            if (!isFiller && this.callMode) {
                window.CallState?.incTurn();
                window.CallState?.setPhase('listening');
            }
        }
    },

    // Split text into sentence-sized TTS chunks (min ~60 chars so prosody stays natural).
    _splitSentencesForTts(text) {
        const parts = text.match(/[^.!?…]+[.!?…]+|\S[^.!?…]*$/g) || [text];
        const out = [];
        let buf = '';
        for (const p of parts) {
            const piece = p.trim();
            if (!piece) continue;
            buf = buf ? `${buf} ${piece}` : piece;
            if (buf.length >= 60) { out.push(buf); buf = ''; }
        }
        if (buf) out.push(buf);
        return out.length ? out : [text];
    },

    // Fetch TTS audio for a single chunk. Returns parsed JSON ({success, audio, error}).
    async _fetchTtsAudio(text, signal) {
        const fd = new FormData();
        fd.append('text', text);
        fd.append('engine', this.ttsEngine || 'local');
        const resp = await fetch(`${API_BASE}/api/voice/tts`, { method: 'POST', headers: window.auth.authHeaders(), body: fd, signal });
        return await resp.json();
    },

    // Play one base64 WAV piece; resolves when playback ends, errors, is paused
    // (external barge-in) or the abort signal fires. Honors the TTS generation counter.
    _playTtsPiece(base64Audio, myGen, signal) {
        return new Promise((resolve) => {
            let settled = false;
            const finish = () => { if (!settled) { settled = true; resolve(); } };
            try {
                const bin = atob(base64Audio);
                const bytes = new Uint8Array(bin.length);
                for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
                const url = URL.createObjectURL(new Blob([bytes], { type: 'audio/wav' }));
                const audio = new Audio(url);
                this.currentTtsAudio = audio;
                if (this.callMode) window.CallMode?.attachTtsAudio(audio);
                const done = () => {
                    try { URL.revokeObjectURL(url); } catch (_) {}
                    if (this.currentTtsAudio === audio) this.currentTtsAudio = null;
                    try { audio.onended = audio.onerror = audio.onpause = null; } catch (_) {}
                    finish();
                };
                audio.onended = done;
                audio.onerror = done;
                audio.onpause = done; // external barge-in pause
                if (signal) {
                    if (signal.aborted) { done(); return; }
                    signal.addEventListener('abort', done, { once: true });
                }
                if (myGen !== this._ttsGen) { done(); return; }
                audio.play().catch(() => done());
            } catch (_) { finish(); }
        });
    },

    // Reproduce el audio del response EMPUJADO por el backend en call mode
    // (type:"tts_audio"). A diferencia de speakResponse, no hace fetch ni usa
    // AbortController: el audio ya viene listo, así que no hay ventana de aborto
    // por teardown del call en el camino texto→audio. El barge-in / CALL OFF
    // siguen cortando lo que ya suena vía currentTtsAudio (intencional).
    async _playPushedAudio(base64Audio, transcriptText = '') {
        try {
            // Cortar cualquier audio previo (filler) sin tocar el flag de "hablando".
            if (this.currentTtsAudio) {
                try { this.currentTtsAudio.pause(); this.currentTtsAudio.src = ''; } catch (_) {}
                this.currentTtsAudio = null;
            }
            const myGen = ++this._ttsGen;
            const binaryStr = atob(base64Audio);
            const bytes = new Uint8Array(binaryStr.length);
            for (let i = 0; i < binaryStr.length; i++) bytes[i] = binaryStr.charCodeAt(i);
            const audioBlob = new Blob([bytes], { type: 'audio/wav' });
            const audioUrl = URL.createObjectURL(audioBlob);
            const audio = new Audio(audioUrl);
            this.currentTtsAudio = audio;
            this.isTtsSpeaking = true;

            if (this.callMode) {
                // transcript already appended at the `response` frame (single source of truth)
                window.CallState?.setPhase('speaking');
                window.CallMode?.attachTtsAudio(audio);
            }

            audio.onended = () => {
                URL.revokeObjectURL(audioUrl);
                if (this.currentTtsAudio === audio) this.currentTtsAudio = null;
                setTimeout(() => {
                    // Solo limpiar si seguimos siendo el audio vigente (no nos superó otro).
                    if (myGen === this._ttsGen) this.isTtsSpeaking = false;
                    if (this.callMode) {
                        window.CallState?.incTurn();
                        window.CallState?.setPhase('listening');
                    }
                }, 400);
            };
            audio.onerror = () => {
                URL.revokeObjectURL(audioUrl);
                if (this.currentTtsAudio === audio) this.currentTtsAudio = null;
                this.isTtsSpeaking = false;
                if (this.callMode) window.CallState?.setPhase('listening');
            };
            await audio.play();
        } catch (error) {
            this.isTtsSpeaking = false;
            if (this.callMode) {
                if (error.name !== 'AbortError') {
                    this.print('WARN', `TTS: fallo de audio (${error.message}) — respuesta visible en texto.`);
                }
                window.CallState?.setPhase('listening');
            }
            console.warn('[tts_audio]', error.name, error.message);
        }
    },

    // ─── Streaming TTS pipeline (P4 Fase 2) ──────────────────────────────────

    async _handleTtsChunk(payload) {
        // Guard: stray chunk outside call mode or TTS disabled
        if (!this.callMode || !this.ttsEnabled) {
            console.warn('[STREAM-TTS] chunk dropped: callMode=', this.callMode, 'ttsEnabled=', this.ttsEnabled);
            return;
        }

        // Guard: stale request_id — discard silently (REQ-2, SC-4)
        if (payload.request_id !== this._streamTtsRequestId) {
            console.warn('[STREAM-TTS] chunk dropped: request_id mismatch — chunk.rid=', payload.request_id, 'expected=', this._streamTtsRequestId, 'idx=', payload.chunk_index);
            return;
        }

        console.log('[STREAM-TTS] chunk arrived idx=', payload.chunk_index, 'is_last=', payload.is_last, 'audio_len=', (payload.audio||'').length);

        // Streaming TTS skips speakResponse: feed the barge-in echo guard here.
        if (payload.text) {
            this._rememberSpoken(payload.text);
        }

        // Serialize decode+schedule in arrival order. decodeAudioData is async and a short
        // later chunk could otherwise finish decoding first and play out of order — or, if it
        // was the is_last chunk, finalize the stream and cut the remaining audio.
        const myGen = this._streamTtsGen;
        const prev = this._streamTtsChain || Promise.resolve();
        this._streamTtsChain = prev
            .then(() => this._processTtsChunk(payload, myGen))
            .catch((err) => console.warn('[STREAM-TTS] chunk processing failed:', err));
        return this._streamTtsChain;
    },

    async _processTtsChunk(payload, myGen) {
        if (myGen !== this._streamTtsGen) return;

        // Terminal frame without audio (backend always closes the stream with is_last, even
        // when the last sentence failed to synthesize): finish once scheduled audio drains.
        if (!payload.audio) {
            if (payload.is_last === true) this._markStreamTtsLast(myGen, null);
            return;
        }

        // Set up the AudioContext on the first chunk of a turn.
        // Prefer the shared context pre-created in startCallMode (user gesture → always 'running').
        // Fall back to lazy creation only if the shared ctx is unavailable or closed (e.g. error).
        if (!this._streamTtsCtx) {
            if (this._streamTtsSharedCtx && this._streamTtsSharedCtx.state !== 'closed') {
                this._streamTtsCtx = this._streamTtsSharedCtx;
                console.log('[STREAM-TTS] using shared AudioContext, state=', this._streamTtsCtx.state);
            } else {
                this._streamTtsCtx = new (window.AudioContext || window.webkitAudioContext)();
                console.log('[STREAM-TTS] fallback: created new AudioContext, state=', this._streamTtsCtx.state);
                if (this._streamTtsCtx.state === 'suspended') {
                    await this._streamTtsCtx.resume().catch((e) => console.warn('[STREAM-TTS] resume failed:', e));
                    console.log('[STREAM-TTS] after resume, state=', this._streamTtsCtx.state);
                }
            }
            // Build the AnalyserNode for this turn and wire it to the orb (D4, REQ-8)
            this._streamTtsAnalyser = this._streamTtsCtx.createAnalyser();
            this._streamTtsAnalyser.fftSize = 256;
            this._streamTtsAnalyser.connect(this._streamTtsCtx.destination);
            window.CallMode?.attachTtsAnalyserNode(this._streamTtsAnalyser);
        }

        // Stale-gen check before decode (could have been cancelled while ctx was creating)
        if (myGen !== this._streamTtsGen) {
            console.warn('[STREAM-TTS] chunk dropped after ctx create: gen changed', myGen, '->', this._streamTtsGen);
            return;
        }

        // Capture ctx locally — it may be nulled by teardown during the async decode
        const ctx = this._streamTtsCtx;
        if (!ctx) {
            console.warn('[STREAM-TTS] chunk dropped: ctx torn down before decode');
            return;
        }

        // Decode base64 WAV → ArrayBuffer → AudioBuffer
        let audioBuffer;
        try {
            const binaryStr = atob(payload.audio);
            const bytes = new Uint8Array(binaryStr.length);
            for (let i = 0; i < binaryStr.length; i++) bytes[i] = binaryStr.charCodeAt(i);
            audioBuffer = await ctx.decodeAudioData(bytes.buffer);
            console.log('[STREAM-TTS] decode OK idx=', payload.chunk_index, 'duration=', audioBuffer.duration.toFixed(3), 's');
        } catch (err) {
            console.warn('[STREAM-TTS] decodeAudioData FAILED idx=', payload.chunk_index, err);
            if (payload.is_last === true) this._markStreamTtsLast(myGen, null);
            return;
        }

        // Stale-gen check after async decode
        if (myGen !== this._streamTtsGen) {
            console.warn('[STREAM-TTS] chunk dropped after decode: gen changed', myGen, '->', this._streamTtsGen);
            return;
        }
        if (!this._streamTtsCtx) {
            console.warn('[STREAM-TTS] chunk dropped: ctx torn down during decode');
            return;
        }

        // Gapless scheduling (REQ-3, D-W9)
        // Use the local ctx (same reference — closing it sets state to 'closed', doesn't null it)

        const startAt = Math.max(ctx.currentTime, this._streamTtsEnd);
        const source = ctx.createBufferSource();
        source.buffer = audioBuffer;
        source.connect(this._streamTtsAnalyser);
        console.log('[STREAM-TTS] scheduling idx=', payload.chunk_index, 'startAt=', startAt.toFixed(3), 'ctx.state=', ctx.state, 'ctx.currentTime=', ctx.currentTime.toFixed(3));
        source.start(startAt);
        this._streamTtsEnd = startAt + audioBuffer.duration;
        this._streamTtsQueue.push(source);

        // Accumulate spoken text for consolidated transcript (D7)
        if (payload.text) this._streamTtsText += payload.text;

        // Reset W1 watchdog on every chunk (D5, REQ-5). This is a SAFETY NET for a dead
        // stream — the real terminator is the is_last frame below. It counts from the END
        // of the audio already scheduled, not from chunk arrival: measuring from arrival cut
        // long buffered answers while they were still playing.
        this._armStreamTtsWatchdog(ctx, myGen);

        window.CallState?.setPhase('speaking');

        // is_last: mark stream complete, wire onended to finalize (REQ-4)
        if (payload.is_last === true) this._markStreamTtsLast(myGen, source);
    },

    _armStreamTtsWatchdog(ctx, myGen) {
        clearTimeout(this._streamTtsTimer);
        const pendingMs = Math.max(0, (this._streamTtsEnd - ctx.currentTime) * 1000);
        this._streamTtsTimer = setTimeout(() => {
            if (myGen === this._streamTtsGen) this._finalizeStreamTts();
        }, pendingMs + 15000);
    },

    _markStreamTtsLast(myGen, source) {
        this._streamTtsEnded = true;
        clearTimeout(this._streamTtsTimer);
        this._streamTtsTimer = null;
        const finalize = () => { if (myGen === this._streamTtsGen) this._finalizeStreamTts(); };
        if (source) {
            source.onended = finalize;
            return;
        }
        // No source for the terminal frame: wait for whatever is still scheduled to play.
        const ctx = this._streamTtsCtx;
        const pendingMs = ctx ? Math.max(0, (this._streamTtsEnd - ctx.currentTime) * 1000) : 0;
        if (pendingMs > 0) {
            this._streamTtsTimer = setTimeout(finalize, pendingMs + 50);
        } else if (ctx) {
            finalize();
        } else {
            // Nothing ever played this turn (e.g. every sentence failed): reset state directly
            // so call mode doesn't stay stuck on "speaking" accepting only stop phrases.
            this._teardownStreamTts({ cancelled: false });
        }
    },

    _finalizeStreamTts() {
        // Idempotent guard — check for already-closed context
        if (!this._streamTtsCtx) return;
        clearTimeout(this._streamTtsTimer);
        this._streamTtsTimer = null;
        // NOTE: transcript is appended at the `response` frame (when text arrives), not
        // here — appending at finalize delayed it until audio playback completed.
        this._teardownStreamTts({ cancelled: false });
    },

    _teardownStreamTts({ cancelled = false } = {}) {
        // Advance gen — invalidates all in-flight and future chunks (D6, REQ-7)
        this._streamTtsGen++;

        // Stop all queued AudioBufferSourceNodes (REQ-7)
        for (const source of this._streamTtsQueue) {
            try { source.stop(); } catch (_) { /* may have already ended */ }
        }
        this._streamTtsQueue = [];
        this._streamTtsEnd = 0;
        this._streamTtsChain = null;

        // Cancel watchdog (REQ-5, REQ-7)
        clearTimeout(this._streamTtsTimer);
        this._streamTtsTimer = null;

        // Disconnect and clean up the per-turn AnalyserNode before nulling it, so Web Audio
        // doesn't accumulate disconnected nodes in the shared context's internal graph.
        if (this._streamTtsAnalyser) {
            try { this._streamTtsAnalyser.disconnect(); } catch (_) {}
        }

        // Close only if it is NOT the shared context.  The shared context lives for the entire
        // call session (created in startCallMode, closed in stopCallMode) so we can reuse it
        // turn-to-turn without re-creating it from a non-user-gesture call stack.
        if (this._streamTtsCtx && this._streamTtsCtx !== this._streamTtsSharedCtx) {
            try { this._streamTtsCtx.close(); } catch (_) {}
        }
        this._streamTtsCtx = null;

        // Reset all streaming state
        this._streamTtsAnalyser = null;
        this._streamTtsText = '';
        this._streamTtsEnded = false;
        this._streamTtsActive = false;
        this._streamTtsRequestId = null;

        // Detach orb analyser — attachTtsAnalyserNode(null) stops the RAF loop and clears
        // _ttsAnalyser without triggering the full _detachTts teardown (avoids circular call:
        // _detachTts → _teardownStreamTts → _detachTts).
        window.CallMode?.attachTtsAnalyserNode?.(null);

        // Transition call mode back to listening, unless this was a cancel (barge-in)
        if (!cancelled && this.callMode) {
            window.CallState?.incTurn();
            window.CallState?.setPhase('listening');
        }
    },

    // ─── End streaming TTS pipeline ──────────────────────────────────────────

    newConversation() {
        this.closeHistoryPanel();
        this.clearTerminal();
        this.historyCache = [];
        // Reset the backend short-term context so old turns don't bleed into the new
        // conversation (the auth token is reused, so the server can't detect this on its own).
        this._resetServerConversation();
        this.print('SYSTEM', '─────────────────────────────────────');
        this.print('SYSTEM', 'Nueva conversación iniciada.');
        this.print('SYSTEM', '─────────────────────────────────────');
        this.refs.input?.focus();
    },

    // Fire-and-forget: clear the backend in-RAM short-term context (history deque +
    // cross-turn buffer) without touching long-term memory. Used by newConversation()
    // and once on cold boot.
    _resetServerConversation() {
        const token = window.auth?.getToken?.();
        if (!token) return;
        try {
            window.auth.authFetch(`${API_BASE}/api/session/reset`, {
                method: 'POST',
            }).catch(() => {});
        } catch (_) { /* best-effort */ }
    },

    async toggleCallMode() {
        if (this.callLoopRunning && !this.callMode) return; // Guard against transition race
        this.callMode = !this.callMode;
        this.refs.callBtn?.classList.toggle('active', this.callMode);
        if (this.refs.callBtn) this.refs.callBtn.textContent = this.callMode ? 'CALL ON' : 'CALL OFF';
        // Update IO device
        const ioCall = document.getElementById('io-call');
        if (ioCall) { ioCall.textContent = this.callMode ? 'READY' : 'OFF'; ioCall.className = 'io-val ' + (this.callMode ? 'ready' : 'off'); }

        // Fire-and-forget signal so the focus monitor can relax messaging
        // grace (Discord/WhatsApp) while we're on a JARVIS call. Independent
        // of mode changes below.
        try {
            const token = window.auth?.getToken?.();
            if (token) {
                window.auth.authFetch(`${API_BASE}/api/focus/jarvis-call-mode`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ active: this.callMode }),
                }).catch(() => {});
            }
        } catch (_) { /* signal is best-effort */ }

        if (!this.callMode) {
            this.stopCallMode();
            this._hideCallOverlay();
            this.print('SYSTEM', 'Modo llamada continua desactivado.');
            // CallMode.hide() schedules a setTimeout that calls _refreshPomoBar
            // after the overlay's fade-out completes — the bar will reappear
            // automatically if a Pomodoro is still active.
            return;
        }

        // Entering call mode: hide the chat bar (the orb + HUD take over).
        const cs = window.CallState?.get() || {};
        if (cs.pomodoroStartedAt) {
            const bar = document.getElementById('pomo-chat-bar');
            if (bar) bar.style.display = 'none';
            if (this._pomoBarInt) { clearInterval(this._pomoBarInt); this._pomoBarInt = null; }
        }

        // Auto-enable TTS when entering call mode — voice mode needs audio response
        if (!this.ttsEnabled) {
            this.ttsEnabled = true;
            if (this.refs.ttsBtn) {
                this.refs.ttsBtn.textContent = 'TTS: ON';
                this.refs.ttsBtn.classList.add('active');
            }
            this.print('SYSTEM', 'TTS activado automáticamente para modo llamada.');
        }

        // Recargar frases si el boot las cargó vacías (backend no estaba listo aún)
        if (!this._exitPhrases.es?.length) await this._loadStopPhrases();

        // Stop push-to-talk if active before entering call mode
        this.stopVoiceCapture();
        this._showCallOverlay();
        this.print('SYSTEM', 'Modo llamada continua activado.');
        await this.startCallMode();
    },

    // Returns the best MediaRecorder MIME type supported by this browser/engine.
    // WebKit on Linux (GStreamer) often doesn't support audio/webm; ogg/opus works better.
    _getCallMimeType() {
        if (this._callMimeTypeCache !== undefined) return this._callMimeTypeCache;
        const candidates = [
            'audio/webm;codecs=opus',
            'audio/webm',
            'audio/ogg;codecs=opus',
            'audio/ogg',
        ];
        this._callMimeTypeCache = candidates.find(t => MediaRecorder.isTypeSupported(t)) || '';
        console.info('[CALL] MediaRecorder MIME:', this._callMimeTypeCache || '(browser default)');
        return this._callMimeTypeCache;
    },

    async _setVisionInterval(interval) {
        if (!this.visionWatching) return;
        try {
            const formData = new FormData();
            formData.append('interval', String(interval));
            await fetch(`${API_BASE}/api/vision/continuous/start`, { method: 'POST', headers: window.auth.authHeaders(), body: formData });
        } catch (_) {}
    },

    async startCallMode() {
        if (this.callLoopRunning) return;
        this.callLoopRunning = true;
        this.callRecordingChunks = [];
        this.callSpeechStarted = false;
        this.callSilenceStart = 0;
        this.callSpeechDuration = 0;
        this.lastTranscriptions = [];
        this._vadConfirmCount = 0;
        if (this.visionWatching) this._setVisionInterval(4);

        try {
            // AGC desactivado por defecto: distorsiona voz tenue y produce
            // fragmentos parciales (ej. "no te escucho" -> "No te sefilo").
            // Override opcional con localStorage 'mic_agc=true'.
            const agcEnabled = (typeof localStorage !== 'undefined' && localStorage.getItem('mic_agc') === 'true');
            this.callStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    sampleRate: { ideal: 16000 },
                    channelCount: { exact: 1 },
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: agcEnabled,
                }
            });
        } catch (error) {
            this.callLoopRunning = false;
            if (error.name === 'NotAllowedError' || error.name === 'PermissionDeniedError') {
                this.callMode = false;
                this.refs.callBtn?.classList.remove('active');
                if (this.refs.callBtn) this.refs.callBtn.textContent = 'CALL OFF';
                this.print('ERROR', 'Permiso de micrófono denegado. Modo llamada desactivado.');
            } else {
                this.print('ERROR', `Micrófono: ${error.message}`);
            }
            return;
        }

        // Setup AudioContext for VAD.
        // Use the original callStream directly for AudioContext — no cloning needed.
        // Cloning tracks triggers GStreamer format re-negotiation on WebKit/Linux,
        // causing gst_value_collect_int_range assertion failures that leave the
        // cloned pipeline returning zeros — VAD never fires. The original stream's
        // GStreamer pipeline is already established and works correctly.
        // MediaRecorder still clones to avoid sharing a pipeline with AudioContext.
        this._callClonedVadTracks = null; // no VAD clone — use original stream
        this.callAudioContext = new (window.AudioContext || window.webkitAudioContext)();

        // CRITICAL: WebKit starts AudioContext in suspended state (autoplay policy).
        // Without resume(), getFloatTimeDomainData() always returns zeros → VAD never fires.
        if (this.callAudioContext.state === 'suspended') {
            await this.callAudioContext.resume();
        }

        const source = this.callAudioContext.createMediaStreamSource(this.callStream);
        this.callAnalyser = this.callAudioContext.createAnalyser();
        this.callAnalyser.fftSize = 2048;
        source.connect(this.callAnalyser);

        // CRITICAL (WebKitGTK/GStreamer): the Web Audio graph MUST reach the
        // destination node or GStreamer will not schedule the source pipeline to
        // produce samples — getFloatTimeDomainData() returns all zeros and VAD
        // never fires. Route through a zero-gain node so nothing is audible.
        const silentGain = this.callAudioContext.createGain();
        silentGain.gain.value = 0;
        this.callAnalyser.connect(silentGain);
        silentGain.connect(this.callAudioContext.destination);

        // Pre-create a separate AudioContext for streaming TTS playback while we are still in the
        // user-gesture call stack (same as callAudioContext above). WebView2 autoplay policy keeps
        // a lazily-created AudioContext suspended even after resume() when called from a WS callback —
        // creating it here guarantees it starts in 'running' state and source.start() actually plays.
        try {
            this._streamTtsSharedCtx = new (window.AudioContext || window.webkitAudioContext)();
            if (this._streamTtsSharedCtx.state === 'suspended') {
                await this._streamTtsSharedCtx.resume().catch(() => {});
            }
        } catch (_) {
            this._streamTtsSharedCtx = null;
        }

        // Calibrate: average 5 snapshots over 1s for stability.
        // WebKitGTK/GStreamer pipelines can take >1s to warm up — a single early
        // snapshot often returns all zeros, leaving the VAD threshold at the
        // hardcoded minimum and preventing voice detection.
        let sumRms = 0;
        const CALIB_SAMPLES = 5;
        for (let i = 0; i < CALIB_SAMPLES; i++) {
            await this.sleep(200);
            const buf = new Float32Array(this.callAnalyser.fftSize);
            this.callAnalyser.getFloatTimeDomainData(buf);
            sumRms += Math.sqrt(buf.reduce((s, v) => s + v * v, 0) / buf.length);
        }
        const ambientRms = sumRms / CALIB_SAMPLES;
        // Additive threshold: ambient + fixed voice increment (0.02).
        // The old multiplier formula (ambient * 2.5) is too aggressive for noisy mics:
        // ambient=0.12 → threshold=0.30, which voice rarely exceeds.
        // Fórmula aditiva + margen extra para tolerar micrófonos ruidosos sin
        // llegar a ser inalcanzable (el multiplicador ×2.5 era demasiado alto).
        // ambient=0.12 → threshold=0.155 (antes 0.14). El gate de confirmación
        // de 3 frames elimina los picos transitorios que sí superan este umbral.
        // Floor: CALL_VAD_THRESHOLD (0.018) para micrófonos limpios.
        // Si ambient < 0.002 sospechamos pipeline frío / mic mute: no creemos en
        // el ambient bajísimo y endurecemos el threshold para evitar disparos por ruido.
        const vadThreshold = (ambientRms < 0.002)
            ? 0.05
            : Math.max(ambientRms + 0.035, this.CALL_VAD_THRESHOLD);
        console.info(`[CALL] VAD calibrado — ruido:${ambientRms.toFixed(4)} umbral:${vadThreshold.toFixed(4)}`);

        if (ambientRms === 0) {
            this.print('SYSTEM', '⚠ AudioContext no recibe datos del micrófono (RMS=0). Intenta hablar igual, puede tardar en arrancar.');
        } else {
            this.print('SYSTEM', `VAD listo (umbral:${vadThreshold.toFixed(4)}) — habla para que te escuche.`);
        }

        // Safety timeout (1 hour)
        this.callSafetyTimer = setTimeout(() => {
            this.print('SYSTEM', 'Modo llamada desactivado por inactividad (60 min).');
            this.toggleCallMode();
        }, this.CALL_SAFETY_TIMEOUT_MS);

        // Wire the tactical HUD to this analyser + stream.
        window.CallMode?.attachMicAnalyser(this.callAnalyser, this.callStream);
        this._syncCallMeta();
        window.CallState?.setPhase('listening');

        // If a Pomodoro session was already running while call mode was off, restore
        // its phase and kick off the HUD countdown. pomodoroStartedAt/pomodoroPhase
        // survive startSession() since that call only resets session-level fields.
        const _cs = window.CallState?.get() || {};
        if (_cs.pomodoroStartedAt && _cs.pomodoroTotalSec && _cs.pomodoroPhase) {
            window.CallState?.setPhase(`pomodoro_${_cs.pomodoroPhase}`);
            this._syncPomoHud(_cs.pomodoroCycle, _cs.pomodoroTotalCycles);
        }

        // VAD tick every 50ms
        this.callVadInterval = setInterval(() => this._callVadTick(vadThreshold), 50);
    },

    _callVadTick(vadThreshold) {
        if (!this.callMode || !this.callAnalyser) return;

        // Cooldown post-cancel: ignorar audio durante la ventana de gracia
        if (Date.now() < this._cooldownUntil) {
            if (this.callRecorder?.state === 'recording') {
                try { this.callRecorder.stop(); } catch (_) {}
                this.callRecordingChunks = [];
                this.callSpeechStarted = false;
            }
            return;
        }

        // Si Zephyro está hablando o procesando, sí grabamos pero solo aceptamos
        // la transcripción si matchea una stop phrase (barge-in). Esto unifica
        // anti-overlap (descartar preguntas nuevas) y barge-in (escuchar cancelaciones).
        // _streamTtsActive covers the streaming TTS path, which never sets isTtsSpeaking.
        this._interruptCandidate = (this.isTtsSpeaking || this.isProcessing || this._streamTtsActive);

        const buffer = new Float32Array(this.callAnalyser.fftSize);
        this.callAnalyser.getFloatTimeDomainData(buffer);
        const rms = Math.sqrt(buffer.reduce((s, v) => s + v * v, 0) / buffer.length);
        const now = Date.now();

        // Update overlay visual feedback from RMS
        this._updateCallOverlayVAD(rms, vadThreshold);

        if (rms > vadThreshold) {
            // Voice detected
            this.callSilenceStart = 0;
            this._vadConfirmCount++;

            // PRE-ROLL: arrancar el MediaRecorder ya en el primer frame por encima
            // del umbral, antes de confirmar voz. Si los próximos frames no
            // confirman, el segmento se descarta (callSpeechStarted=false →
            // callSpeechDuration=0 → onstop no envía). Si sí confirman, los
            // chunks capturados entre count=1 y count=3 forman el prefijo (~100ms)
            // y evitan cortar el inicio de la palabra (ej. "escucho" → "sefilo").
            if (!this.callRecorder && !this.callSpeechStarted) {
                this._startCallRecorder();
            }

            // Gate anti-ruido: exigir ≥3 frames consecutivos (~150ms) por encima
            // del umbral antes de considerar que hay voz real.
            // Esto descarta golpes, toses breves, voces de fondo puntuales, etc.
            if (!this.callSpeechStarted && this._vadConfirmCount >= 3) {
                this.callSpeechStarted = true;
                this._speechStart = now;
            }
            if (this.callSpeechStarted) {
                this.callSpeechDuration = now - (this._speechStart || now);
            }

            // Safety: max segment length
            if (this.callSpeechDuration >= this.CALL_MAX_SEGMENT_MS) {
                if (this.callRecorder?.state === 'recording') {
                    try { this.callRecorder.stop(); } catch (_) {}
                }
            }
        } else {
            // Silence — resetear el gate de confirmación
            this._vadConfirmCount = 0;
            if (this.callSpeechStarted) {
                if (this.callSilenceStart === 0) this.callSilenceStart = now;
                const silenceDuration = now - this.callSilenceStart;
                if (silenceDuration >= this.CALL_SILENCE_MS) {
                    if (this.callRecorder?.state === 'recording') {
                        try { this.callRecorder.stop(); } catch (_) {}
                    }
                }
            } else if (this.callRecorder?.state === 'recording') {
                // Pre-roll que nunca confirmó voz: descartar (ruido/glitch).
                try { this.callRecorder.stop(); } catch (_) {}
            }
        }
    },

    _startCallRecorder() {
        this.callSpeechDuration = 0;
        this.callRecordingChunks = [];

        // Use track.clone() to create a separate GStreamer pipeline for
        // recording — this fully isolates MediaRecorder from the AudioContext
        // VAD pipeline, preventing gst_value_collect_int_range errors on WebKit/Linux.
        const mimeType = this._getCallMimeType();
        this._callClonedRecTracks = this.callStream.getAudioTracks().map(t => t.clone());
        const recStream = new MediaStream(this._callClonedRecTracks);
        this.callRecorder = new MediaRecorder(
            recStream,
            mimeType ? { mimeType } : {}
        );
        this.callRecorder.ondataavailable = (e) => {
            if (e.data?.size > 0) this.callRecordingChunks.push(e.data);
        };
        this.callRecorder.onstop = async () => {
            const wasConfirmed = this.callSpeechStarted;
            const chunks = this.callRecordingChunks;
            const mime = this.callRecorder?.mimeType || mimeType || 'audio/webm';
            const duration = this.callSpeechDuration;
            // Limpiar primero para que el siguiente tick pueda re-armar
            this.callRecordingChunks = [];
            this.callSpeechStarted = false;
            this.callRecorder = null;
            this._callClonedRecTracks?.forEach(t => t.stop());
            this._callClonedRecTracks = null;
            if (wasConfirmed && chunks.length && duration >= this.CALL_MIN_SPEECH_MS) {
                const blob = new Blob(chunks, { type: mime });
                await this.transcribeAndDispatch(blob, true);
            }
        };
        try {
            // Chunks de 100ms para mayor granularidad en el pre-roll
            this.callRecorder.start(100);
        } catch (err) {
            console.error('[CALL] MediaRecorder start failed:', err);
            this.callRecorder = null;
            this._callClonedRecTracks?.forEach(t => t.stop());
            this._callClonedRecTracks = null;
        }
    },

    stopCallMode() {
        if (this.visionWatching) this._setVisionInterval(10);
        clearInterval(this.callVadInterval);
        clearTimeout(this.callSafetyTimer);
        this.callVadInterval = null;
        this.callSafetyTimer = null;
        this._vadConfirmCount = 0;
        // Abort any in-flight TTS fetch so it doesn't play after call ends
        if (this._ttsAbortCtrl) { this._ttsAbortCtrl.abort(); this._ttsAbortCtrl = null; }
        // Stop audio that may still be playing
        if (this.currentTtsAudio) {
            try { this.currentTtsAudio.pause(); this.currentTtsAudio.src = ''; } catch (_) {}
            this.currentTtsAudio = null;
        }
        if (this.callRecorder && this.callRecorder.state !== 'inactive') {
            try { this.callRecorder.stop(); } catch (_) {}
        }
        // Stop all cloned tracks first (separate GStreamer pipelines)
        this._callClonedVadTracks?.forEach(t => t.stop());
        this._callClonedRecTracks?.forEach(t => t.stop());
        this._callClonedVadTracks = null;
        this._callClonedRecTracks = null;
        this.callStream?.getTracks().forEach(t => t.stop());
        if (this.callAudioContext && this.callAudioContext.state !== 'closed') {
            this.callAudioContext.close().catch(() => {});
        }
        if (this._streamTtsSharedCtx && this._streamTtsSharedCtx.state !== 'closed') {
            try { this._streamTtsSharedCtx.close(); } catch (_) {}
        }
        this._streamTtsSharedCtx = null;
        this.callStream = null;
        this.callAudioContext = null;
        this.callAnalyser = null;
        this.callRecorder = null;
        this.callRecordingChunks = [];
        this.callSpeechStarted = false;
        this.isTtsSpeaking = false;
        this.callLoopRunning = false;
        this._awaitingResponseAudio = null;
    },

    // ===== Call Overlay Visual Methods =====

    _showCallOverlay() {
        // Delegate to tactical HUD module.
        window.CallMode?.mount();
        window.CallState?.startSession();
        window.CallMode?.show();
    },

    _hideCallOverlay() {
        window.CallState?.endSession();
        window.CallMode?.hide();
    },

    // Pushes the currently-active LLM provider/model, mode, and STT engine
    // into CallState so the HUD header reflects the real configuration.
    _syncCallMeta() {
        if (!window.CallState) return;
        let model = '—';
        if (this.provider === 'anthropic' && this.anthropicModel) {
            model = this.anthropicModel;
        } else if (this.provider === 'deepseek' && this.deepseekModel) {
            model = this.deepseekModel;
        } else if (this.provider && this.provider !== 'auto') {
            model = this.provider;
        } else if (this.mode === 'local') {
            model = 'llama3.2';
        } else {
            model = this.mode;
        }
        window.CallState.patch({
            provider: this.provider,
            mode: this.mode,
            model: model,
            sttEngine: (this.sttEngine || 'local').toUpperCase(),
            ttsEngine: (this.ttsEngine || 'local').toUpperCase(),
        });
    },

    _updateCallOverlayVAD(rms, threshold) {
        // Feed raw VAD telemetry into the HUD store. Phase transitions
        // are owned by the STT/TTS lifecycle hooks, not VAD directly —
        // VAD only drives the "voice_detected" row + speech_prob readout.
        if (!window.CallState) return;
        const isSpeaking = rms > threshold;
        const prob = threshold > 0 ? Math.min(1, rms / (threshold * 1.5)) : 0;
        const now = Date.now();
        const s = window.CallState.get();
        window.CallState.patch({
            vadProb: prob,
            vadDetected: isSpeaking,
            vadSilenceMs: isSpeaking ? 0 : (this.callSilenceStart ? now - this.callSilenceStart : 0),
            vadChunks: (s.vadChunks || 0) + (isSpeaking ? 1 : 0),
        });

        // Phase fallback: solo re-afirmar 'listening' desde fases QUIETAS.
        // Antes esto reseteaba cualquier fase != 'thinking' cada 50ms, lo que
        // borraba 'researching' (búsqueda web) y su banner al instante. Ahora
        // nunca degradamos una fase activa (thinking/researching/speaking/pomodoro/
        // cancelled) desde el VAD — solo es un no-op seguro cuando ya estamos quietos.
        if (!this.isTtsSpeaking && (s.phase === 'listening' || s.phase === 'idle')) {
            window.CallState.setPhase('listening');
        }
    },

    // Matrix rain is now owned by CallMatrix (see call-matrix.js). These stay
    // as no-op shims in case any external caller still references them.
    _startCallMatrix() {},
    _stopCallMatrix() {},

    _isWhisperHallucination(text) {
        if (text.length < 3) return true;
        const lower = text.toLowerCase();
        const hallucinations = [
            'subtítulos', 'amara.org', 'thanks for watching', 'suscríbete',
            'www.', 'http', 'música de fondo', 'aplausos', '[música]',
            'gracias por ver', 'no olvides', 'dale like',
        ];
        return hallucinations.some(h => lower.includes(h));
    },

    // Streamed chunks arrive faster than they play, so keep enough sentences
    // to still hold the one coming out of the speakers.
    _rememberSpoken(text) {
        this._recentSpokenTexts = [...(this._recentSpokenTexts || []).slice(-7), text];
    },

    // Echo = the transcript reproduces a run of what Zephyro just said: 3+ words in a
    // row found verbatim in a recent TTS text, and most of its words shared. A
    // command spoken over the reply ("pausá el pomodoro") rarely copies 3
    // consecutive words of it, so it still interrupts.
    _looksLikeTtsEcho(text) {
        const norm = (s) => (s || '').toLowerCase()
            .normalize('NFD').replace(/[̀-ͯ]/g, '')
            .replace(/[^\p{L}\p{N}\s]/gu, ' ')
            .split(/\s+/).filter(Boolean);
        const words = norm(text);
        if (words.length < 3) return false;
        return (this._recentSpokenTexts || []).some((spoken) => {
            const spokenWords = norm(spoken);
            const haystack = ` ${spokenWords.join(' ')} `;
            const vocab = new Set(spokenWords);
            const shared = words.filter(w => vocab.has(w)).length / words.length;
            if (shared < 0.5) return false;
            for (let i = 0; i + 3 <= words.length; i++) {
                if (haystack.includes(` ${words.slice(i, i + 3).join(' ')} `)) return true;
            }
            return false;
        });
    },

    async transcribeAndDispatch(audioBlob, fromCallMode) {
        const token = window.auth?.getToken?.();
        if (!token) return;

        // En modo call, si Zephyro está hablando/procesando esta grabación es candidata
        // a barge-in. Capturamos el flag AHORA porque la transcripción es async y el
        // estado podría cambiar mientras esperamos.
        const isInterruptCandidate = fromCallMode && this._interruptCandidate;

        // Derive file extension from the blob's actual MIME type so the server
        // applies the right ffmpeg conversion (ogg vs webm vs wav).
        const mime = audioBlob.type || '';
        const ext = mime.includes('ogg') ? 'ogg' : mime.includes('mp4') ? 'mp4' : 'webm';
        const filename = fromCallMode ? `call.${ext}` : `mic.${ext}`;

        const formData = new FormData();
        formData.append('audio', audioBlob, filename);
        if (this.sttEngine && this.sttEngine !== 'local') {
            formData.append('engine', this.sttEngine);
        }

        if (fromCallMode) window.CallState?.setPhase('thinking');
        const _sttT0 = performance.now();
        try {
            const response = await fetch(`${API_BASE}/api/voice/stt`, {
                method: 'POST',
                headers: window.auth.authHeaders(),
                body: formData
            });
            const data = await response.json();

            if (!response.ok || !data?.success || !data?.text?.trim()) {
                if (!fromCallMode && data?.error) this.print('ERROR', `STT: ${data.error}`);
                if (fromCallMode) window.CallState?.setPhase('listening');
                return;
            }

            const text = data.text.trim();
            if (fromCallMode) {
                window.CallState?.recordLatency('stt', Math.round(performance.now() - _sttT0));
                window.CallState?.appendTranscript({ who: 'bruno', text });
            }

            if (fromCallMode && this._isWhisperHallucination(text)) {
                console.warn('[STT] Hallucination filtered:', text);
                window.CallState?.setPhase('listening');
                return;
            }

            // Detect repetition loop (same text 3 times in a row)
            if (fromCallMode && this.lastTranscriptions.length >= 3 &&
                this.lastTranscriptions.every(t => t === text)) {
                console.warn('[STT] Repetition loop detected, skipping:', text);
                window.CallState?.setPhase('listening');
                return;
            }
            if (fromCallMode) {
                this.lastTranscriptions = [...this.lastTranscriptions.slice(-2), text];
            }

            // Exit phrase: cierra el call mode entero. NO se evalúa cuando el
            // audio se grabó mientras Zephyro hablaba/procesaba (isInterruptCandidate):
            // ahí lo más probable es un ECO del propio TTS que Whisper transcribió
            // como frase de salida → cerraba el modo solo. En interrupt sólo se
            // acepta stop phrase (cancelar), abajo. Para cerrar, decilo en idle.
            if (fromCallMode && !isInterruptCandidate && this._matchesExitPhrase(text)) {
                this.print('SYSTEM', `STT (exit) => ${text}`);
                if (this.isProcessing || this.isTtsSpeaking) this.cancelCurrentRequest('voz');
                await this.toggleCallMode();
                return;
            }

            // Grabación nacida mientras Zephyro hablaba/procesaba. Antes TODO lo que
            // no fuese stop phrase se descartaba acá con un console.debug: un comando
            // dicho encima de su respuesta ("pará el pomodoro") se perdía sin dejar
            // una sola línea en el log. Ahora interrumpe y obedece — cortamos lo que
            // esté diciendo y seguimos al dispatch de abajo.
            if (isInterruptCandidate) {
                if (this._matchesStopPhrase(text)) {
                    this.print('SYSTEM', `STT (cancel) => ${text}`);
                    this.cancelCurrentRequest('voz');
                    return;
                }
                // On speakers the mic picks up Zephyro's own TTS. Without this guard
                // that echo cut the reply and came back as a user message.
                if (this._looksLikeTtsEcho(text)) {
                    this.print('SYSTEM', `STT (eco descartado) => ${text}`);
                    window.CallState?.setPhase(
                        this.isTtsSpeaking ? 'speaking' : (this.isProcessing ? 'thinking' : 'listening')
                    );
                    return;
                }
                this.print('SYSTEM', `STT (interrumpe) => ${text}`);
                this.cancelCurrentRequest('voz');
                // cae al dispatch
            } else if (fromCallMode && this._matchesStopPhrase(text)) {
                // Stop phrase sin nada que cancelar. Se traga para no mandarle al LLM
                // un "pará" sin contexto, pero NUNCA en silencio: un comando que
                // desaparece sin línea de log es el peor bug posible de debuggear.
                // Y hay que devolver el orbe a 'listening' — quedaba en 'thinking'
                // desde el inicio de este handler, que es el "se colgó" que se veía.
                this.print('SYSTEM', `STT (stop, nada en curso) => ${text}`);
                window.CallState?.setPhase('listening');
                return;
            }

            this.print('SYSTEM', `STT => ${text}`);
            await this.dispatchMessage(text, 'USER');
        } catch (error) {
            this.print('ERROR', `STT error: ${error.message}`);
            // El orbe quedó en 'thinking' al entrar a este handler: sin esto, un fallo
            // de STT lo deja "pensando" para siempre y parece que se colgó.
            if (fromCallMode) window.CallState?.setPhase('listening');
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
            if (this.isMicRecording) {
                this.stopVoiceCapture();
            } else {
                await this.startVoiceCapture();
            }
            return;
        }

        if (command === '/call') {
            await this.toggleCallMode();
            return;
        }

        if (command === '/diag-mic') {
            await this._runMicDiagnostic();
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

        // ---------- SelfState / Reflection / Bugs (admin) ----------
        if (command === '/reflect') {
            const token = window.auth?.getToken?.();
            if (!token) { this.print('ERROR', 'No autenticado'); return; }
            this.print('SYSTEM', 'Ejecutando reflexión...');
            try {
                const resp = await window.auth.authFetch(`${API_BASE}/api/self/reflect`, { method: 'POST' });
                const data = await resp.json();
                if (resp.ok && data.success) this.print('RESPONSE', data.reflection || JSON.stringify(data));
                else this.print('ERROR', data.error || JSON.stringify(data));
            } catch (e) {
                this.print('ERROR', `Reflect error: ${e.message}`);
            }
            return;
        }

        if (command === '/selfstate') {
            const token = window.auth?.getToken?.();
            if (!token) { this.print('ERROR', 'No autenticado'); return; }
            try {
                const resp = await window.auth.authFetch(`${API_BASE}/api/self/state`);
                const data = await resp.json();
                if (resp.ok && data.success) this.print('RESPONSE', JSON.stringify(data.state, null, 2));
                else this.print('ERROR', data.error || JSON.stringify(data));
            } catch (e) {
                this.print('ERROR', `SelfState error: ${e.message}`);
            }
            return;
        }

        if (command === '/bugs') {
            const token = window.auth?.getToken?.();
            if (!token) { this.print('ERROR', 'No autenticado'); return; }
            try {
                const resp = await window.auth.authFetch(`${API_BASE}/api/self/bugs`);
                const data = await resp.json();
                if (resp.ok && data.success) this.print('RESPONSE', JSON.stringify(data.bugs, null, 2));
                else this.print('ERROR', data.error || JSON.stringify(data));
            } catch (e) {
                this.print('ERROR', `Bugs error: ${e.message}`);
            }
            return;
        }

        if (command === '/living') {
            const token = window.auth?.getToken?.();
            if (!token) { this.print('ERROR', 'No autenticado'); return; }
            const tierArg = arg1 ? parseInt(arg1) : 2;
            const tier = (tierArg >= 1 && tierArg <= 3) ? tierArg : 2;
            this.print('SYSTEM', `Obteniendo estado del sistema (tier ${tier})...`);
            try {
                const resp = await window.auth.authFetch(`${API_BASE}/api/self/living?tier=${tier}`);
                const data = await resp.json();
                if (resp.ok && data.success) this.print('RESPONSE', data.snapshot);
                else this.print('ERROR', data.error || JSON.stringify(data));
            } catch (e) {
                this.print('ERROR', `Living error: ${e.message}`);
            }
            return;
        }

        if (command === '/introspect') {
            const token = window.auth?.getToken?.();
            if (!token) { this.print('ERROR', 'No autenticado'); return; }
            this.print('SYSTEM', 'Generando introspección honesta...');
            try {
                const resp = await window.auth.authFetch(`${API_BASE}/api/self/introspect`);
                const data = await resp.json();
                if (resp.ok && data.success) this.print('RESPONSE', data.introspection);
                else this.print('ERROR', data.error || JSON.stringify(data));
            } catch (e) {
                this.print('ERROR', `Introspect error: ${e.message}`);
            }
            return;
        }

        if (command === '/capacidades') {
            const token = window.auth?.getToken?.();
            if (!token) { this.print('ERROR', 'No autenticado'); return; }
            try {
                const resp = await window.auth.authFetch(`${API_BASE}/api/self/capacidades`);
                const data = await resp.json();
                if (resp.ok && data.success) this.print('RESPONSE', data.capacidades);
                else this.print('ERROR', data.error || JSON.stringify(data));
            } catch (e) {
                this.print('ERROR', `Capacidades error: ${e.message}`);
            }
            return;
        }

        // ---------- TTS helper: /tts <texto> ----------
        if (command === '/tts') {
            const originalText = raw.replace(/^\/tts\s*/i, '').trim();
            if (!originalText) { this.print('ERROR', 'Uso: /tts <texto>'); return; }
            try {
                await playAudio(originalText);
                this.print('SYSTEM', 'Reproduciendo TTS...');
            } catch (e) {
                this.print('ERROR', `TTS error: ${e.message}`);
            }
            return;
        }

        // ---------- Code Analysis (GitNexus) ----------
        if (command === '/code') {
            const token = window.auth?.getToken?.();
            if (!token) { this.print('ERROR', 'No autenticado'); return; }

            const subArgs = raw.replace(/^\/code\s*/i, '').trim();
            const subCommand = (subArgs.split(/\s+/)[0] || 'help').toLowerCase();
            const subQuery = subArgs.replace(/^\S+\s*/, '').trim();

            // /code help
            if (subCommand === 'help' || !subArgs) {
                this.print('SYSTEM', 'Comandos de análisis de código (GitNexus):');
                this.print('SYSTEM', '  /code repos          — Listar repositorios indexados');
                this.print('SYSTEM', '  /code search <query> — Búsqueda híbrida en código');
                this.print('SYSTEM', '  /code impact <sym>   — Análisis de blast radius');
                this.print('SYSTEM', '  /code context <sym>  — Vista 360° de un símbolo');
                this.print('SYSTEM', '  /code changes        — Cambios git → procesos afectados');
                this.print('SYSTEM', '  /code health         — Estado del bridge GitNexus');
                this.print('SYSTEM', '  /code index <path>   — Indexar un repositorio');
                this.print('SYSTEM', '  /code explore        — Abrir GitNexus Web UI');
                return;
            }

            // /code repos
            if (subCommand === 'repos') {
                this.print('SYSTEM', 'Consultando repositorios...');
                try {
                    const resp = await window.auth.authFetch(`${API_BASE}/api/code/repos`);
                    const data = await resp.json();
                    if (data.success) this.printStructured('CODE', { repos: data.repos });
                    else this.print('ERROR', data.error || 'Error obteniendo repos');
                } catch (e) { this.print('ERROR', `Code repos error: ${e.message}`); }
                return;
            }

            // /code search <query>
            if (subCommand === 'search') {
                if (!subQuery) { this.print('ERROR', 'Uso: /code search <query>'); return; }
                this.print('SYSTEM', `Buscando: "${subQuery}"...`);
                try {
                    const resp = await window.auth.authFetch(`${API_BASE}/api/code/search`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ query: subQuery })
                    });
                    const data = await resp.json();
                    if (data.success) this.printStructured('CODE', { type: 'code_analysis', tool: 'search', result: data.result, elapsed_time: data.elapsed_time });
                    else this.print('ERROR', data.error || 'Error en búsqueda');
                } catch (e) { this.print('ERROR', `Search error: ${e.message}`); }
                return;
            }

            // /code impact <symbol>
            if (subCommand === 'impact') {
                if (!subQuery) { this.print('ERROR', 'Uso: /code impact <símbolo>'); return; }
                this.print('SYSTEM', `Analizando impacto de: "${subQuery}"...`);
                try {
                    const resp = await window.auth.authFetch(`${API_BASE}/api/code/impact`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ symbol: subQuery })
                    });
                    const data = await resp.json();
                    if (data.success) this.printStructured('CODE', { type: 'code_analysis', tool: 'impact', result: data.result, elapsed_time: data.elapsed_time });
                    else this.print('ERROR', data.error || 'Error en análisis de impacto');
                } catch (e) { this.print('ERROR', `Impact error: ${e.message}`); }
                return;
            }

            // /code context <symbol>
            if (subCommand === 'context') {
                if (!subQuery) { this.print('ERROR', 'Uso: /code context <símbolo>'); return; }
                this.print('SYSTEM', `Obteniendo contexto de: "${subQuery}"...`);
                try {
                    const resp = await window.auth.authFetch(`${API_BASE}/api/code/context`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ symbol: subQuery })
                    });
                    const data = await resp.json();
                    if (data.success) this.printStructured('CODE', { type: 'code_analysis', tool: 'context', result: data.result, elapsed_time: data.elapsed_time });
                    else this.print('ERROR', data.error || 'Error obteniendo contexto');
                } catch (e) { this.print('ERROR', `Context error: ${e.message}`); }
                return;
            }

            // /code changes
            if (subCommand === 'changes') {
                this.print('SYSTEM', 'Detectando cambios...');
                try {
                    const resp = await window.auth.authFetch(`${API_BASE}/api/code/changes`, { method: 'POST' });
                    const data = await resp.json();
                    if (data.success) this.printStructured('CODE', { type: 'code_analysis', tool: 'detect_changes', result: data.result, elapsed_time: data.elapsed_time });
                    else this.print('ERROR', data.error || 'Error detectando cambios');
                } catch (e) { this.print('ERROR', `Changes error: ${e.message}`); }
                return;
            }

            // /code health
            if (subCommand === 'health') {
                this.print('SYSTEM', 'Verificando GitNexus...');
                try {
                    const resp = await window.auth.authFetch(`${API_BASE}/api/code/health`);
                    const data = await resp.json();
                    this.printStructured('CODE', data);
                } catch (e) { this.print('ERROR', `Health error: ${e.message}`); }
                return;
            }

            // /code index <path>
            if (subCommand === 'index') {
                if (!subQuery) { this.print('ERROR', 'Uso: /code index /ruta/al/repo'); return; }
                this.print('SYSTEM', `Indexando: ${subQuery} (puede tardar minutos)...`);
                try {
                    const resp = await window.auth.authFetch(`${API_BASE}/api/code/index`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ repo_path: subQuery })
                    });
                    const data = await resp.json();
                    if (data.success) this.print('SYSTEM', '✓ Repositorio indexado correctamente');
                    else this.print('ERROR', data.error || 'Error al indexar');
                } catch (e) { this.print('ERROR', `Index error: ${e.message}`); }
                return;
            }

            // /code explore — open GitNexus Web UI
            if (subCommand === 'explore') {
                this.print('SYSTEM', 'Abriendo GitNexus Web UI...');
                try {
                    // Try Tauri IPC to open a new window
                    if (window.__TAURI__) {
                        const { invoke } = window.__TAURI__.core;
                        await invoke('open_gitnexus_ui');
                        this.print('SYSTEM', '✓ GitNexus Web UI abierta en ventana separada');
                    } else {
                        // Fallback: open in system browser
                        window.open('http://localhost:3000', '_blank');
                        this.print('SYSTEM', '✓ Abierto en navegador externo');
                    }
                } catch (e) {
                    // Fallback
                    window.open('http://localhost:3000', '_blank');
                    this.print('SYSTEM', '✓ Abierto en navegador externo');
                }
                return;
            }

            this.print('ERROR', `Subcomando desconocido: /code ${subCommand}. Usa /code help`);
            return;
        }

        this.print('ERROR', `Comando desconocido: ${raw}`);
    },

    // ── Image Attachment ─────────────────────────────────────────────────
    onImageSelected(event) {
        const file = event.target.files?.[0];
        if (file) this.attachImage(file);
    },

    onImageDrop(event) {
        const file = event.dataTransfer?.files?.[0];
        if (file && file.type.startsWith('image/')) {
            this.attachImage(file);
        }
    },

    onImagePaste(event) {
        if (!this.booted) return;
        const items = event.clipboardData?.items;
        if (!items) return;
        for (const item of items) {
            if (item.type.startsWith('image/')) {
                event.preventDefault();
                const blob = item.getAsFile();
                if (blob) {
                    const file = new File([blob], `pasted-${Date.now()}.png`, { type: blob.type });
                    this.attachImage(file);
                }
                break;
            }
        }
    },

    attachImage(file) {
        if (file.size > 10 * 1024 * 1024) {
            this.print('SYSTEM', 'Imagen demasiado grande (máx 10MB)');
            return;
        }
        this.currentAttachment = file;

        // Show attachment bar
        if (this.refs.attachmentName) this.refs.attachmentName.textContent = file.name;
        if (this.refs.attachmentThumb) {
            const url = URL.createObjectURL(file);
            this.refs.attachmentThumb.src = url;
            this.refs.attachmentThumb.style.display = 'block';
        }
        if (this.refs.attachmentBar) this.refs.attachmentBar.style.display = 'flex';
        if (this.refs.attachImgBtn) this.refs.attachImgBtn.classList.add('has-attachment');

        this.refs.input?.focus();
    },

    clearAttachment() {
        this.currentAttachment = null;
        if (this.refs.attachmentBar) this.refs.attachmentBar.style.display = 'none';
        if (this.refs.attachmentThumb) {
            this.refs.attachmentThumb.style.display = 'none';
            this.refs.attachmentThumb.src = '';
        }
        if (this.refs.attachImgBtn) this.refs.attachImgBtn.classList.remove('has-attachment');
        if (this.refs.imageFileInput) this.refs.imageFileInput.value = '';
    },

    async sendImageWithPrompt(prompt) {
        if (!this.currentAttachment) return;

        const formData = new FormData();
        formData.append('file', this.currentAttachment);
        formData.append('prompt', prompt || 'Analiza esta imagen en detalle.');
        formData.append('use_cloud', 'true');

        this.print('USER', `> ${prompt || 'Analizar imagen'} [IMG: ${this.currentAttachment.name}]`);
        this.clearAttachment();

        this.print('CORE', 'analyzing image...');

        try {
            const resp = await fetch(`${API_BASE}/api/vision/analyze`, {
                method: 'POST',
                headers: window.auth.authHeaders(),
                body: formData,
            });
            const data = await resp.json();

            if (data.success) {
                this.print('RESPONSE', data.analysis);
                if (data.elapsed_time) {
                    this.print('SYSTEM', `Vision analysis: ${data.elapsed_time}s`);
                }
            } else {
                this.print('SYSTEM', `Vision error: ${data.error}`);
            }
        } catch (err) {
            this.print('SYSTEM', `Vision error: ${err.message}`);
        }
    },

    // ── Continuous Vision ────────────────────────────────────────────────
    async toggleContinuousVision() {
        if (this.visionWatching) {
            await this.stopContinuousVision();
        } else {
            await this.startContinuousVision();
        }
    },

    async startContinuousVision() {
        try {
            const formData = new FormData();
            formData.append('interval', '10');

            const resp = await fetch(`${API_BASE}/api/vision/continuous/start`, {
                method: 'POST',
                headers: window.auth.authHeaders(),
                body: formData,
            });
            const data = await resp.json();

            if (data.success) {
                this.visionWatching = true;
                if (this.refs.visionBtn) {
                    this.refs.visionBtn.classList.add('active');
                    this.refs.visionBtn.textContent = 'WATCHING';
                }
                const ioWatch = document.getElementById('io-watch');
                if (ioWatch) { ioWatch.textContent = 'READY'; ioWatch.className = 'io-val ready'; }
                this.print('SYSTEM', 'Observación continua de pantalla activada.');

                // Start polling context updates
                this._visionPoller = setInterval(() => this.pollVisionContext(), 12000);
            } else {
                this.print('SYSTEM', `Vision error: ${data.error || data.message}`);
            }
        } catch (err) {
            this.print('SYSTEM', `Vision error: ${err.message}`);
        }
    },

    async stopContinuousVision() {
        try {
            const formData = new FormData();

            await fetch(`${API_BASE}/api/vision/continuous/stop`, {
                method: 'POST',
                headers: window.auth.authHeaders(),
                body: formData,
            });
        } catch (e) { /* ignore */ }

        this.visionWatching = false;
        if (this._visionPoller) {
            clearInterval(this._visionPoller);
            this._visionPoller = null;
        }
        if (this.refs.visionBtn) {
            this.refs.visionBtn.classList.remove('active');
            this.refs.visionBtn.textContent = 'WATCH: OFF';
        }
        const ioWatch2 = document.getElementById('io-watch');
        if (ioWatch2) { ioWatch2.textContent = 'OFF'; ioWatch2.className = 'io-val off'; }
        this.print('SYSTEM', 'Observación continua detenida.');
    },

    async pollVisionContext() {
        if (!this.visionWatching) return;
        try {
            const resp = await window.auth.authFetch(`${API_BASE}/api/vision/continuous/status`);
            const data = await resp.json();
            const ctx = data.context || {};
            const activity = ctx.user_activity || ctx.summary || '';
            if (activity) {
                this.updateModuleStatus('REST VISION', 'CONNECTED', activity);
                const app = ctx.active_app ? `App: ${ctx.active_app}. ` : '';
                const hint = ctx.context_hint ? ` ${ctx.context_hint}` : '';
                this.lastVisionContext = `${app}Actividad: ${activity}.${hint}`.trim();
            }
        } catch (e) { /* ignore */ }
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
window.APP = APP;

document.addEventListener('DOMContentLoaded', () => {
    APP.init();
});

