// Call Mode orchestrator — owns the HUD lifecycle.
// Subscribes to CallState, binds AudioOrbVisualizer + CallMatrix + UI,
// wires controls to APP methods in chat.js. Does NOT touch WS/STT/TTS logic.
(function () {
    const $ = (sel, root) => (root || document).querySelector(sel);

    const CallMode = {
        _orb: null,
        _matrix: null,
        _ttsAnalyser: null,
        _ttsAudioCtx: null,
        _ttsRaf: null,
        _timerInt: null,
        _latencyInt: null,
        _vuInt: null,
        _unsub: null,
        _refs: {},
        _lastRender: {},
        _keysBound: false,

        mount() {
            if (this._mounted) return;
            this._mounted = true;
            this._cleanup = [];
            this._collectRefs();
            this._orb = new window.AudioOrbVisualizer(this._refs.orb);
            this._matrix = new window.CallMatrix(this._refs.matrix);
            this._bindControls();
            this._bindKeys();
            this._unsub = window.CallState.subscribe((s) => this._render(s));
            this._bindTauriBridge();
        },

        _trackTauri(promise) {
            // Tauri's listen() returns a promise that resolves to an unlisten
            // function. We push it into _cleanup so destroy() can release it.
            if (!promise || typeof promise.then !== 'function') return;
            promise.then((un) => {
                if (typeof un === 'function') this._cleanup.push(un);
            }).catch(() => {});
        },

        _trackWindowEvent(name, handler) {
            window.addEventListener(name, handler);
            this._cleanup.push(() => window.removeEventListener(name, handler));
        },

        _bindTauriBridge() {
            const tauri = window.__TAURI__;
            if (!tauri) return;
            const listen = tauri.event?.listen;
            const invoke = tauri.core?.invoke || tauri.invoke;

            // Floating window asked main to end the call → trigger APP.toggleCallMode.
            this._trackTauri(listen?.('call-end-requested', () => {
                const APP = window.APP;
                if (APP?.callMode) APP.toggleCallMode();
                window.CallState.patch({ background: false });
            }));

            // User restored main from the floating window — resume HUD animations.
            this._trackTauri(listen?.('call-restored', () => {
                window.CallState.patch({ background: false });
                const overlay = this._refs.overlay;
                if (overlay?.classList.contains('active')) {
                    this._matrix?.start();
                }
            }));

            // Floating just mounted and is asking for a state snapshot.
            this._trackTauri(listen?.('call-request-snapshot', () => {
                if (invoke) {
                    invoke('push_call_state', { state: this._snapshotPayload(window.CallState.get()) }).catch(() => {});
                }
            }));

            // User clicked a diag button in the floating window — forward to backend WS.
            this._trackTauri(listen?.('diag-response', (evt) => {
                const p = evt.payload || {};
                if (!p.request_id || !p.action) return;
                const diag = window.zephyroDiagnostic;
                if (diag && typeof diag.sendApprovalResponse === 'function') {
                    diag.sendApprovalResponse(p.action);
                }
            }));

            // Forward state snapshots to the floating window. Push when:
            //   - we're in background mode (floating is the visible UI), or
            //   - the Pomodoro fields just changed (the floating may still be
            //     mounted from a previous BG session even though background
            //     went false momentarily; we don't want it to render stale
            //     state). The Tauri `push_call_state` command early-returns
            //     if the floating window doesn't exist, so the extra pushes
            //     are essentially free when there's nothing to receive them.
            const unsubBg = window.CallState.subscribe((s) => {
                if (!invoke) return;
                const pomoChanged =
                    this._lastPomoStartedAt !== s.pomodoroStartedAt
                    || this._lastPomoPaused   !== s.pomodoroPaused
                    || this._lastPomoPhase    !== s.pomodoroPhase
                    || this._lastPomoCycle    !== s.pomodoroCycle;
                if (!s.background && !pomoChanged) return;
                this._lastPomoStartedAt = s.pomodoroStartedAt;
                this._lastPomoPaused    = s.pomodoroPaused;
                this._lastPomoPhase     = s.pomodoroPhase;
                this._lastPomoCycle     = s.pomodoroCycle;
                if (this._pushScheduled) return;
                this._pushScheduled = true;
                requestAnimationFrame(() => {
                    this._pushScheduled = false;
                    invoke('push_call_state', { state: this._snapshotPayload(s) }).catch(() => {});
                });
            });
            if (typeof unsubBg === 'function') this._cleanup.push(unsubBg);

            // Forward diagnostic approval state to the floating window when it arrives.
            const forwardDiagToFloating = (detail) => {
                if (!invoke) return;
                invoke('push_diag_state', { state: detail || {} }).catch(() => {});
            };
            this._trackWindowEvent('zephyro:diag-state', (evt) => {
                const d = evt.detail || {};
                const inCall = !!(window.APP && window.APP.callMode);
                if (!inCall) return;
                const phase = window.CallState.get().phase;
                const needsChoice = d.status === 'awaiting_approval' || d.status === 'model_choice';
                // The gate's clock starts now; a deferred announcement must count
                // from here, not from when it gets flushed.
                if (needsChoice && !d._receivedAt) d._receivedAt = Date.now();
                if (needsChoice && phase !== 'listening' && phase !== 'idle') {
                    window.CallState.patch({ pendingDiagNotification: d });
                    return;
                }
                forwardDiagToFloating(d);
                if (needsChoice) {
                    this._announceDiagPending(d);
                } else {
                    // Ya no espera nada: cancelar el recordatorio hablado y,
                    // si el gate resolvió solo por timeout, decirlo.
                    this._clearDiagWarning();
                    if (d.status === 'expired') this._announceDiagExpired(d);
                }
            });
            this._trackWindowEvent('zephyro:diag-flush', (evt) => {
                const d = evt.detail || {};
                forwardDiagToFloating(d);
                this._announceDiagPending(d);
            });
            this._trackWindowEvent('zephyro:pomodoro-sync', () => {
                this._startPomodoroTimer();
            });
        },

        _startPomodoroTimer() {
            if (this._pomoInt) { clearInterval(this._pomoInt); this._pomoInt = null; }
            const cdEl = this._refs.pomoCd;
            if (!cdEl) return;
            const tick = () => {
                const s = window.CallState?.get() || {};
                if (!s.pomodoroStartedAt || !s.pomodoroTotalSec) {
                    // Hide HUD fields if no active session
                    if (this._refs.pomoField) this._refs.pomoField.style.display = 'none';
                    if (this._refs.pomoCycleField) this._refs.pomoCycleField.style.display = 'none';
                    const ctlGroup = document.getElementById('pomo-hdr-controls');
                    if (ctlGroup) ctlGroup.style.display = 'none';
                    clearInterval(this._pomoInt);
                    this._pomoInt = null;
                    return;
                }
                if (s.pomodoroPaused) {
                    cdEl.textContent = '⏸ PAUSED';
                    return;
                }
                const elapsed = (Date.now() - s.pomodoroStartedAt) / 1000;
                const remaining = Math.max(0, s.pomodoroTotalSec - elapsed);
                const m = String(Math.floor(remaining / 60)).padStart(2, '0');
                const sc = String(Math.floor(remaining % 60)).padStart(2, '0');
                cdEl.textContent = `${m}:${sc}`;
            };
            tick();
            this._pomoInt = setInterval(tick, 500);
        },

        // ── Aviso HABLADO de diagnóstico pendiente ───────────────────────────
        // En call mode el usuario está en manos libres: el chime + la tarjeta en
        // la ventana flotante no alcanzan (antes había que salir del call mode
        // para descubrir que el gate esperaba una respuesta, y el model_choice
        // se auto-resolvía en silencio al vencer su timeout). Acá se dice en voz
        // alta qué está esperando y cuánto queda. La aprobación sigue siendo por
        // click en el panel / ventana flotante.
        _announceDiagPending(d) {
            this._playDiagChime();
            const detail = d || {};
            const rid = detail.request_id || '';
            const status = detail.status || '';

            // Dedup: el escalado more_context re-emite el mismo request_id, y el
            // flush post-turno puede repetir un aviso ya dado.
            const key = `${rid}:${status}`;
            if (this._lastSpokenDiagKey === key) return;
            this._lastSpokenDiagKey = key;

            const app = window.APP;
            if (!app || !app.callMode) return;

            // Panel visible sin que el usuario lo vaya a buscar. En background
            // mode la ventana flotante ya recibe el estado por push_diag_state;
            // con la ventana principal visible esa ventana NO EXISTE y el push
            // se descartaba en silencio (lib.rs: get_webview_window → return Ok),
            // así que no había ningún panel: solo el chime. Abrimos la modal.
            if (!(window.CallState?.get()?.background)) {
                try { window.zephyroDiagnostic?.openModal?.(); } catch (_) { /* no bloquear */ }
            }

            if (!app.ttsEnabled) return;

            let msg;
            if (status === 'model_choice') {
                const total = Number(detail.timeout_sec) || 0;
                const waited = detail._receivedAt ? (Date.now() - detail._receivedAt) / 1000 : 0;
                const secs = Math.max(0, Math.floor(total - waited));
                const label = this._diagChoiceLabel(detail);
                msg = label
                    ? `Necesito tu confirmación para seguir el diagnóstico con ${label}.`
                    : 'Necesito tu confirmación para elegir el modelo del diagnóstico.';
                if (secs >= 30) {
                    // Round DOWN: announcing more time than is left lets the gate
                    // auto-resolve while the user thinks they can still answer.
                    const mins = Math.floor(secs / 60);
                    msg += ` Si no me decís nada en ${mins >= 1
                        ? `${mins} minuto${mins > 1 ? 's' : ''}`
                        : `${secs} segundos`}, sigo con la opción por defecto.`;
                }
                this._scheduleDiagTimeoutWarning(rid, secs, label);
            } else {
                msg = 'Tengo una hipótesis del diagnóstico esperando tu aprobación. Está en el panel.';
            }
            try { app.speakResponse(msg, { isFiller: true }); } catch (_) { /* no bloquear */ }
        },

        _clearDiagWarning() {
            if (this._diagWarnTimer) { clearTimeout(this._diagWarnTimer); this._diagWarnTimer = null; }
        },

        _announceDiagExpired(detail) {
            const app = window.APP;
            if (!app || !app.callMode || !app.ttsEnabled) return;
            const auto = (detail && detail.auto_action) || '';
            const msg = auto
                ? `No me confirmaste a tiempo, así que el diagnóstico siguió con ${auto}.`
                : 'El diagnóstico expiró sin respuesta.';
            try { app.speakResponse(msg, { isFiller: true }); } catch (_) { /* no bloquear */ }
        },

        _diagChoiceLabel(detail) {
            const opts = Array.isArray(detail.options) ? detail.options : [];
            const def = detail.default_choice || '';
            const hit = opts.find(o => o && o.id === def) || opts[0];
            if (!hit) return '';
            return String(hit.label || hit.name || hit.id || '').trim();
        },

        // Segundo aviso poco antes de que el gate elija solo, para que el
        // usuario tenga una última chance sin mirar la pantalla.
        _scheduleDiagTimeoutWarning(rid, secs, label) {
            this._clearDiagWarning();
            if (!secs || secs < 45) return;
            const lead = Math.min(20000, Math.max(10000, secs * 1000 * 0.2));
            this._diagWarnTimer = setTimeout(() => {
                this._diagWarnTimer = null;
                const app = window.APP;
                if (!app || !app.callMode || !app.ttsEnabled) return;
                // Si ya se resolvió, el estado dejó de esperar → no molestar.
                const st = window.zephyroDiagnostic?.getState?.();
                if (st && st.status !== 'awaiting_model_choice') return;
                const tail = label ? ` Sigo con ${label}.` : '';
                try {
                    app.speakResponse(`Sigo esperando tu confirmación del diagnóstico.${tail}`,
                                      { isFiller: true });
                } catch (_) { /* no bloquear */ }
            }, Math.max(1000, secs * 1000 - lead));
        },

        _playDiagChime() {
            try {
                if (this._chimeCtx == null) {
                    const Ctx = window.AudioContext || window.webkitAudioContext;
                    if (!Ctx) return;
                    this._chimeCtx = new Ctx();
                }
                const ctx = this._chimeCtx;
                const now = ctx.currentTime;
                const osc = ctx.createOscillator();
                const gain = ctx.createGain();
                osc.type = 'sine';
                osc.frequency.setValueAtTime(880, now);
                osc.frequency.exponentialRampToValueAtTime(1320, now + 0.18);
                gain.gain.setValueAtTime(0.0001, now);
                gain.gain.exponentialRampToValueAtTime(0.15, now + 0.04);
                gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.28);
                osc.connect(gain).connect(ctx.destination);
                osc.start(now);
                osc.stop(now + 0.3);
            } catch (_) { /* silencioso */ }
        },

        _snapshotPayload(s) {
            return {
                phase: s.phase,
                sessionId: s.sessionId,
                startedAt: s.startedAt,
                latencyMs: s.latencyMs,
                muted: s.muted,
                // Pomodoro fields — included so the floating BG widget can
                // render countdown + phase + cycle + next-phase preview without
                // needing its own WebSocket connection.
                pomodoroStartedAt: s.pomodoroStartedAt,
                pomodoroTotalSec: s.pomodoroTotalSec,
                pomodoroCycle: s.pomodoroCycle,
                pomodoroTotalCycles: s.pomodoroTotalCycles,
                pomodoroPhase: s.pomodoroPhase,
                pomodoroPhaseDetail: s.pomodoroPhaseDetail,
                pomodoroPaused: s.pomodoroPaused,
                pomodoroNextPhase: s.pomodoroNextPhase,
                pomodoroNextDurationSec: s.pomodoroNextDurationSec,
            };
        },

        _collectRefs() {
            const r = this._refs;
            r.overlay       = $('#call-overlay');
            r.matrix        = $('#call-matrix-bg');
            r.orb           = $('#call-orb-container');
            r.phaseLabel    = $('#call-phase-label');
            r.duration      = $('#call-hdr-duration');
            r.codec         = $('#call-hdr-codec');
            r.latency       = $('#call-hdr-latency');
            r.stt           = $('#call-hdr-stt');
            r.model         = $('#call-hdr-model');
            r.session       = $('#call-hdr-session');
            r.btnMin        = $('#call-btn-min');
            r.btnBg         = $('#call-btn-bg');
            r.vuBars        = $('#call-vu-bars');
            r.vuValue       = $('#call-vu-value');
            r.vuDevice      = $('#call-vu-device');
            r.vuSample      = $('#call-vu-sample');
            r.vuGate        = $('#call-vu-gate');
            r.vadRow        = $('#call-vad-row');
            r.vadProb       = $('#call-vad-prob');
            r.vadSilence    = $('#call-vad-silence');
            r.vadChunks     = $('#call-vad-chunks');
            r.pipeMic       = $('#call-pipe-mic');
            r.pipeStt       = $('#call-pipe-stt');
            r.pipeLlm       = $('#call-pipe-llm');
            r.pipeTts       = $('#call-pipe-tts');
            r.latStt        = $('#call-lat-stt');
            r.latLlm        = $('#call-lat-llm');
            r.latTts        = $('#call-lat-tts');
            r.latTotal      = $('#call-lat-total');
            r.mTurns        = $('#call-m-turns');
            r.mTokIn        = $('#call-m-tokin');
            r.mTokOut       = $('#call-m-tokout');
            r.mInter        = $('#call-m-inter');
            r.mCost         = $('#call-m-cost');
            r.contextList   = $('#call-context-list');
            r.spectrum      = $('#call-spectrum');
            r.transcript    = $('#call-transcript');
            r.statusBanner  = $('#call-status-banner');
            r.statusText    = $('#call-status-text');
            r.btnMute       = $('#call-btn-mute');
            r.btnPtt        = $('#call-btn-ptt');
            r.btnSpeaker    = $('#call-btn-speaker');
            r.btnInterrupt  = $('#call-btn-interrupt');
            r.btnBgExplicit = $('#call-btn-background');
            r.btnEnd        = $('#call-btn-end');
            r.btnPttCircle  = $('#call-btn-ptt-circle');
            r.btnPause      = $('#call-btn-pause');
            r.qaLogs        = $('#call-qa-logs');
            r.qaSwitch      = $('#call-qa-switch');
            // Pomodoro HUD
            r.pomoField     = $('#pomo-hdr-field');
            r.pomoCycleField = $('#pomo-hdr-cycle');
            r.pomoCd        = $('#call-pomo-countdown');
            r.pomoCycle     = $('#call-pomo-cycle');
            r.pomoBtnPause  = $('#call-pomo-pause-btn');
            r.pomoBtnSkip   = $('#call-pomo-skip-btn');
            r.pomoBtnExtend = $('#call-pomo-extend-btn');
        },

        _bindControls() {
            const r = this._refs;
            const APP = window.APP;
            r.btnEnd?.addEventListener('click', () => APP?.toggleCallMode?.());
            r.btnInterrupt?.addEventListener('click', () => this._interrupt());
            r.btnMute?.addEventListener('click', () => this._toggleMute());
            r.btnSpeaker?.addEventListener('click', () => { /* visual only; speaker output isn't gated in JARVIS */ });
            r.btnPtt?.addEventListener('mousedown', () => this._pttDown());
            r.btnPtt?.addEventListener('mouseup',   () => this._pttUp());
            r.btnPtt?.addEventListener('mouseleave',() => this._pttUp());
            r.btnBg?.addEventListener('click',         () => this._enterBackground());
            r.btnBgExplicit?.addEventListener('click', () => this._enterBackground());
            r.btnMin?.addEventListener('click',        () => this._minimize());
            r.btnPttCircle?.addEventListener('mousedown', () => this._pttDown());
            r.btnPttCircle?.addEventListener('mouseup',   () => this._pttUp());
            r.btnPttCircle?.addEventListener('mouseleave',() => this._pttUp());
            r.btnPause?.addEventListener('click',      () => this._toggleMute());
            r.qaLogs?.addEventListener('click',        () => this._toggleLogs());
            r.qaSwitch?.addEventListener('click',      () => this._cycleMode());
            // Pomodoro HUD controls — REST calls so we don't pollute the LLM
            // turn budget with synthetic voice commands.
            r.pomoBtnPause?.addEventListener('click',  () => this._pomoCall(
                window.CallState?.get()?.pomodoroPaused ? '/pomodoro/resume' : '/pomodoro/pause'
            ));
            r.pomoBtnSkip?.addEventListener('click',   () => this._pomoCall('/pomodoro/skip-break'));
            r.pomoBtnExtend?.addEventListener('click', () => this._pomoCall(
                '/pomodoro/extend', { minutes: 5, mode: 'current' }
            ));
        },

        async _pomoCall(path, body) {
            try {
                const apiBase = (window.APP && window.APP.constructor && window.APP.constructor.API_BASE)
                    || window.API_BASE || '';
                const url = new URL(`${apiBase}/api${path}`, window.location.origin);
                url.searchParams.set('token', window.auth?.getToken?.() || '');
                const opts = { method: 'POST', headers: { 'Content-Type': 'application/json' } };
                if (body) opts.body = JSON.stringify(body);
                await fetch(url.toString(), opts);
            } catch (err) {
                console.warn('[call-mode] pomo control failed', path, err);
            }
        },

        _cycleMode() {
            const APP = window.APP;
            if (!APP) return;
            const next = APP.mode === 'local' ? 'cloud' : (APP.mode === 'cloud' ? 'auto' : 'local');
            const sel = document.getElementById('mode-selector');
            if (sel) { sel.value = next; sel.dispatchEvent(new Event('change', { bubbles: true })); }
            else { APP.mode = next; APP._syncCallMeta?.(); }
        },

        _bindKeys() {
            if (this._keysBound) return;
            this._keysBound = true;
            const onKeyDown = (e) => {
                if (!this._isActive()) return;
                if (e.repeat) return;
                switch (e.code) {
                    case 'Space':      this._pttDown(); e.preventDefault(); break;
                    case 'Escape':     this._interrupt(); e.preventDefault(); break;
                    case 'KeyM':       this._toggleMute(); e.preventDefault(); break;
                    case 'KeyB':       this._enterBackground(); e.preventDefault(); break;
                    case 'F3':         this._toggleLogs(); e.preventDefault(); break;
                }
            };
            const onKeyUp = (e) => {
                if (!this._isActive()) return;
                if (e.code === 'Space') this._pttUp();
            };
            this._trackWindowEvent('keydown', onKeyDown);
            this._trackWindowEvent('keyup', onKeyUp);
        },

        _isActive() {
            return this._refs.overlay?.classList.contains('active');
        },

        _pttDown() {
            // PTT is visual feedback — actual recording is VAD-driven in chat.js.
            this._refs.btnPtt?.classList.add('active');
        },
        _pttUp() {
            this._refs.btnPtt?.classList.remove('active');
        },

        _toggleMute() {
            const s = window.CallState.get();
            const muted = !s.muted;
            window.CallState.patch({ muted });
            const APP = window.APP;
            // Mute the mic track directly — keeps pipeline alive but drops audio.
            APP?.callStream?.getAudioTracks?.().forEach((t) => { t.enabled = !muted; });
            this._refs.btnMute?.classList.toggle('muted', muted);
        },

        _interrupt() {
            const APP = window.APP;
            if (APP?.cancelCurrentRequest) {
                APP.cancelCurrentRequest('interrupt');
            } else if (APP) {
                APP.isTtsSpeaking = false;
            }
            window.CallState.patch({ interrupts: (window.CallState.get().interrupts || 0) + 1 });
            window.CallState.setPhase('listening');
        },

        _minimize() {
            // 1) Hide the overlay (call audio + WS stay alive on main webview).
            // 2) Show a CALL_ACTIVE pill so the user can restore it.
            // 3) Minimize the OS window so the user gets the desktop back.
            const r = this._refs.overlay;
            if (!r) return;
            // Cancel any pending fade timeout to avoid racing with show()/hide()
            if (this._fadeTimeout) {
                clearTimeout(this._fadeTimeout);
                this._fadeTimeout = null;
            }
            r.classList.remove('fade-in');
            r.classList.add('fade-out');
            this._fadeTimeout = setTimeout(() => {
                r.classList.remove('active');
                this._fadeTimeout = null;
                // Overlay is gone — chat is the visible surface again. Let
                // chat re-render the Pomodoro bar so the user doesn't lose
                // the countdown when minimizing.
                try { window.APP?._refreshPomoBar?.(); } catch (_) {}
            }, 260);

            // Pill flotante — click to restore
            this._ensureCallPill();
            const pill = document.getElementById('call-active-pill');
            if (pill) pill.classList.add('visible');

            // Minimizar ventana del SO via Tauri command (matches enter_background_mode pattern)
            try {
                const invoke = window.__TAURI__?.core?.invoke || window.__TAURI__?.invoke;
                if (typeof invoke === 'function') {
                    invoke('minimize_main_window').catch((err) => {
                        console.warn('[call-mode] minimize invoke failed:', err);
                    });
                }
            } catch (err) {
                console.warn('[call-mode] minimize failed:', err);
            }
        },

        _ensureCallPill() {
            if (document.getElementById('call-active-pill')) return;
            const pill = document.createElement('div');
            pill.id = 'call-active-pill';
            pill.textContent = 'CALL_ACTIVE — click to restore';
            pill.addEventListener('click', () => this._restoreFromPill());
            document.body.appendChild(pill);
        },

        _restoreFromPill() {
            const pill = document.getElementById('call-active-pill');
            if (pill) pill.classList.remove('visible');
            this.show();
        },

        async _enterBackground() {
            // Stop the matrix animation eagerly — even if the invoke fails,
            // the user clicked BACKGROUND so the foreground UI should pause.
            this._matrix?.stop();
            try {
                const invoke = window.__TAURI__?.core?.invoke || window.__TAURI__?.invoke;
                if (typeof invoke !== 'function') {
                    throw new Error('Tauri invoke unavailable');
                }
                await invoke('enter_background_mode');
                // Only flip the JS-side flag AFTER the OS-level transition succeeded.
                // Setting it before the await left the state machine inconsistent
                // when the invoke failed (matrix stopped, HUD frozen, background=true
                // but main still visible).
                window.CallState.patch({ background: true });
            } catch (err) {
                console.warn('[call-mode] background invoke failed:', err);
                // Rollback: we didn't actually enter background, so resume animations.
                this._matrix?.start();
            }
        },

        _toggleLogs() {
            // F3: bring terminal panel to the foreground briefly (non-destructive)
            const panel = document.querySelector('.terminal-panel');
            if (!panel) return;
            panel.classList.toggle('call-mode-logs-peek');
        },

        // ===== Rendering =====

        show() {
            const overlay = this._refs.overlay;
            if (!overlay) return;
            // Cancel any pending fade-out timeout that would otherwise remove .active
            if (this._fadeTimeout) {
                clearTimeout(this._fadeTimeout);
                this._fadeTimeout = null;
            }
            overlay.classList.add('active');
            void overlay.offsetWidth;
            overlay.classList.add('fade-in');
            overlay.classList.remove('fade-out');

            // Si veníamos de _minimize, esconder el pill
            const pill = document.getElementById('call-active-pill');
            if (pill) pill.classList.remove('visible');

            this._matrix?.start();
            this._orb?.start();
            this._startTimers();
            // Overlay is back — chat bar should hide (its guard reads
            // overlay visibility now). Trigger a re-evaluation explicitly.
            try { window.APP?._refreshPomoBar?.(); } catch (_) {}
        },

        hide() {
            const overlay = this._refs.overlay;
            if (!overlay) return;
            // Cancel any pending fade timeout to avoid stacking
            if (this._fadeTimeout) {
                clearTimeout(this._fadeTimeout);
                this._fadeTimeout = null;
            }
            overlay.classList.add('fade-out');
            overlay.classList.remove('fade-in');
            this._stopTimers();
            this._matrix?.stop();
            this._orb?.stop();
            this._detachTts();
            this._refs.statusBanner?.classList.remove('active');
            // Si el pill de minimized seguía visible, esconderlo (la call terminó)
            const pill = document.getElementById('call-active-pill');
            if (pill) pill.classList.remove('visible');
            this._fadeTimeout = setTimeout(() => {
                overlay.classList.remove('active', 'fade-out');
                this._fadeTimeout = null;
                // Call fully ended — give chat a chance to re-render the
                // Pomodoro bar (if a session is still running on the backend).
                try { window.APP?._refreshPomoBar?.(); } catch (_) {}
            }, 300);
        },

        attachMicAnalyser(analyser, stream) {
            if (!this._orb) return;
            this._orb.attachAnalyser(analyser);
            // Mic device metadata
            if (stream) {
                const track = stream.getAudioTracks?.()[0];
                const settings = track?.getSettings?.() || {};
                window.CallState.patch({
                    micDevice: (track?.label || 'default').replace(/\s*\(.+\)\s*$/, '').toLowerCase().slice(0, 20),
                    micSampleRate: settings.sampleRate || 16000,
                });
            }
        },

        attachTtsAudio(audioElement) {
            // Connect the TTS <audio> element to a dedicated AnalyserNode that
            // powers BOTH the mini spectrum panel AND the central orb. Without
            // this, the orb stays static while Zephyro speaks (mic is gated to
            // prevent echo, so the mic-fed analyser returns silence).
            try {
                if (!this._ttsAudioCtx) {
                    this._ttsAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
                }
                // Browser autoplay policy: AudioContext starts suspended until user gesture.
                // If we route audio through a suspended ctx, nothing reaches the speakers.
                if (this._ttsAudioCtx.state === 'suspended') {
                    this._ttsAudioCtx.resume().catch(() => {});
                }
                // createMediaElementSource can only be called ONCE per element.
                // Cache the source node per element so re-attach during the same session
                // (e.g., visualization reset between utterances) doesn't throw.
                if (!this._ttsSrcMap) this._ttsSrcMap = new WeakMap();
                let src = this._ttsSrcMap.get(audioElement);
                if (!src) {
                    src = this._ttsAudioCtx.createMediaElementSource(audioElement);
                    src.connect(this._ttsAudioCtx.destination);
                    this._ttsSrcMap.set(audioElement, src);
                }
                const an = this._ttsAudioCtx.createAnalyser();
                an.fftSize = 256; // 128 bins — enough resolution for orb + spectrum bars
                src.connect(an);
                this._ttsAnalyser = an;
                this._ttsSrcNode = src;
                this._orb?.attachTtsAnalyser(an);
                this._runTtsSpectrum();
            } catch (err) {
                // If analyser attach fails, the caller's audio.play() still works
                // because we don't take ownership of the element here.
                console.debug('[call-mode] tts analyser attach skipped:', err.message);
            }
        },

        attachTtsAnalyserNode(analyserNode) {
            // Accept a pre-built AnalyserNode from the streaming AudioContext in chat.js.
            // Does NOT call createMediaElementSource — that would throw with a streaming ctx.
            // Pass null to detach: stops the RAF loop + orb spectrum without full _detachTts.
            if (analyserNode === null) {
                if (this._ttsRaf) { cancelAnimationFrame(this._ttsRaf); this._ttsRaf = null; }
                this._ttsAnalyser = null;
                this._orb?.attachTtsAnalyser(null);
                return;
            }
            this._ttsAnalyser = analyserNode;
            this._orb?.attachTtsAnalyser(analyserNode);
            this._runTtsSpectrum();
        },

        _runTtsSpectrum() {
            if (!this._refs.spectrum) return;
            const bars = this._refs.spectrum.querySelectorAll('.ms-bar');
            const an = this._ttsAnalyser;
            if (!an) return;
            const buf = new Uint8Array(an.frequencyBinCount);
            const step = Math.max(1, Math.floor(an.frequencyBinCount / bars.length));
            const loop = () => {
                if (!this._ttsAnalyser) return;
                an.getByteFrequencyData(buf);
                let anyActive = false;
                for (let i = 0; i < bars.length; i++) {
                    let sum = 0;
                    for (let j = 0; j < step; j++) sum += buf[i * step + j] || 0;
                    const v = sum / step / 255;
                    if (v > 0.02) anyActive = true;
                    bars[i].style.height = `${Math.max(2, v * 100)}%`;
                }
                this._refs.spectrum.classList.toggle('speaking', anyActive);
                this._refs.spectrum.classList.toggle('idle', !anyActive);
                this._ttsRaf = requestAnimationFrame(loop);
            };
            loop();
        },

        _detachTts() {
            if (this._ttsRaf) cancelAnimationFrame(this._ttsRaf);
            this._ttsRaf = null;
            // Disconnect the source node so nodes don't pile up in the audio graph
            // across multiple TTS utterances (would otherwise leak gradually).
            try { this._ttsSrcNode?.disconnect(); } catch (_) { /* noop */ }
            try { this._ttsAnalyser?.disconnect(); } catch (_) { /* noop */ }
            this._ttsSrcNode = null;
            this._ttsAnalyser = null;
            this._orb?.attachTtsAnalyser(null);
            // Keep _ttsAudioCtx alive — reusing it avoids re-creating on next call.
            // Streaming AudioContext is owned by chat.js — tear it down from here
            // so all call sites (hide, destroy, future) clean it up automatically.
            window.APP?._teardownStreamTts?.({ cancelled: true });
        },

        _startTimers() {
            this._stopTimers();
            // Duration timer
            this._timerInt = setInterval(() => {
                const s = window.CallState.get();
                if (!s.startedAt) return;
                const elapsed = Date.now() - s.startedAt;
                const h = Math.floor(elapsed / 3600000);
                const m = Math.floor((elapsed / 60000) % 60);
                const sec = Math.floor((elapsed / 1000) % 60);
                const text = `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
                if (this._refs.duration) this._refs.duration.textContent = text;
            }, 250);

            // Latency jitter — small drift around the real measured latency
            // so the HUD reads as alive rather than frozen. We NEVER invent
            // a number when no measurement exists yet; the renderer shows "—"
            // for the totals until recordLatency is called for stt/llm/tts.
            this._latencyInt = setInterval(() => {
                const s = window.CallState.get();
                const base = s.totalAvgMs || ((s.sttAvgMs || 0) + (s.llmAvgMs || 0) + (s.ttsAvgMs || 0));
                if (base > 0) {
                    const jitter = Math.round(base + (Math.random() - 0.5) * 20);
                    window.CallState.patch({ latencyMs: Math.max(40, jitter) });
                }
            }, 1500);

            // VU meter tick — samples the mic analyser at 30fps.
            this._vuInt = setInterval(() => this._tickVu(), 33);

            // Resume the Pomodoro countdown if a session is in progress. Without
            // this, returning from MIN → restore (or BG → restore) left the HUD
            // counter frozen — _startPomodoroTimer was only ever wired to fire
            // on the pomodoro-sync event, which only emits on phase transitions.
            const s = window.CallState?.get() || {};
            if (s.pomodoroStartedAt && s.pomodoroTotalSec) {
                this._startPomodoroTimer();
            }
        },

        _stopTimers() {
            if (this._timerInt) clearInterval(this._timerInt);
            if (this._latencyInt) clearInterval(this._latencyInt);
            if (this._vuInt) clearInterval(this._vuInt);
            if (this._pomoInt) clearInterval(this._pomoInt);
            this._timerInt = this._latencyInt = this._vuInt = this._pomoInt = null;
        },

        _tickVu() {
            const APP = window.APP;
            const an = APP?.callAnalyser;
            if (!an) return;
            const buf = new Float32Array(an.fftSize);
            an.getFloatTimeDomainData(buf);
            let sum = 0;
            for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
            const rms = Math.sqrt(sum / buf.length);
            window.CallState.patch({ micGain: rms });
        },

        _render(s) {
            const r = this._refs;
            if (!r.overlay) return;

            // Phase label
            const label = s.phase === 'idle' ? 'STANDBY' : s.phase.toUpperCase();
            if (r.phaseLabel) {
                r.phaseLabel.textContent = label;
                r.phaseLabel.className = 'call-orb-phase p-' + s.phase;
            }
            this._orb?.setState(s.phase);

            // Status banner: shows thinking/researching message with spinner
            if (r.statusBanner && r.statusText) {
                const busy = s.phase === 'thinking' || s.phase === 'researching';
                r.statusBanner.classList.toggle('active', busy);
                if (busy && s.statusMessage) {
                    r.statusText.textContent = s.statusMessage;
                }
            }

            // Header
            if (r.session) r.session.textContent = '#' + (s.sessionId || '—');
            if (r.codec) r.codec.textContent = s.codec;
            if (r.stt) r.stt.textContent = s.sttEngine;
            if (r.model) r.model.textContent = s.model || '—';
            if (r.latency) {
                const ms = s.latencyMs || 0;
                r.latency.textContent = `${ms}ms`;
                r.latency.classList.toggle('warn', ms > 160);
            }

            // VU meter — 10 cells driven by micGain (with small hot zone)
            if (r.vuBars) {
                const cells = r.vuBars.querySelectorAll('.vu-cell');
                const level = Math.min(1, s.micGain / 0.35);
                const lit = Math.round(level * cells.length);
                cells.forEach((c, i) => {
                    c.classList.toggle('on', i < lit);
                    c.classList.toggle('hot', i >= cells.length - 2 && i < lit);
                });
            }
            if (r.vuValue) r.vuValue.textContent = s.micGain ? s.micGain.toFixed(2) : '0.00';
            if (r.vuDevice) r.vuDevice.textContent = s.micDevice || '—';
            if (r.vuSample) r.vuSample.textContent = `${Math.round((s.micSampleRate || 16000) / 1000)}kHz/mono`;
            if (r.vuGate) r.vuGate.textContent = `${s.noiseGate || -42}dB`;

            // VAD
            if (r.vadRow) {
                r.vadRow.textContent = s.vadDetected ? 'voice_detected' : 'silence';
                r.vadRow.classList.toggle('inactive', !s.vadDetected);
            }
            if (r.vadProb) r.vadProb.textContent = (s.vadProb || 0).toFixed(2);
            if (r.vadSilence) r.vadSilence.textContent = String(s.vadSilenceMs || 0);
            if (r.vadChunks) r.vadChunks.textContent = String(s.vadChunks || 0);

            // Pipeline
            [['mic', r.pipeMic], ['stt', r.pipeStt], ['llm', r.pipeLlm], ['tts', r.pipeTts]]
                .forEach(([k, el]) => el?.classList.toggle('active', !!s.pipeline[k]));
            // Mic muted indicator while Zephyro speaks/processes (anti-overlap visual)
            r.pipeMic?.classList.toggle('muted', !!s.micGated);

            // Latencies
            if (r.latStt) r.latStt.textContent = s.sttAvgMs ? `${s.sttAvgMs}ms` : '—';
            if (r.latLlm) r.latLlm.textContent = s.llmAvgMs ? `${s.llmAvgMs}ms` : '—';
            if (r.latTts) {
                r.latTts.textContent = s.ttsAvgMs ? `${s.ttsAvgMs}ms` : '—';
                r.latTts.classList.toggle('warn', s.ttsAvgMs > 180);
            }
            if (r.latTotal) {
                const total = (s.sttAvgMs || 0) + (s.llmAvgMs || 0) + (s.ttsAvgMs || 0);
                r.latTotal.textContent = total ? `${total}ms` : '—';
            }

            // Session metrics
            if (r.mTurns)  r.mTurns.textContent  = s.turns || 0;
            if (r.mTokIn)  r.mTokIn.textContent  = s.tokensIn || 0;
            if (r.mTokOut) r.mTokOut.textContent = s.tokensOut || 0;
            if (r.mInter)  r.mInter.textContent  = s.interrupts || 0;
            if (r.mCost)   r.mCost.textContent   = `$${(s.costUsd || 0).toFixed(3)}`;

            // Active context
            if (r.contextList) {
                const ctx = s.activeContext.length ? s.activeContext : [
                    { text: 'memory.recall (0 hits)', dim: true },
                    { text: 'project: jarvis', dim: false },
                    { text: 'skill: —', dim: true },
                ];
                r.contextList.innerHTML = ctx.map((c) => {
                    const text = typeof c === 'string' ? c : c.text;
                    const cls = typeof c === 'string' ? '' : (c.dim ? 'dim' : '');
                    return `<div class="call-ctx-entry ${cls}">${escapeHtml(text)}</div>`;
                }).join('');
            }

            // Mute button
            r.btnMute?.classList.toggle('muted', !!s.muted);

            // Transcript (render only when changed)
            const tKey = s.transcript.map(e => e.id + (e.streaming ? '*' : '')).join('|');
            if (tKey !== this._lastRender.transcriptKey) {
                this._lastRender.transcriptKey = tKey;
                this._renderTranscript(s);
            }
        },

        _renderTranscript(s) {
            const el = this._refs.transcript;
            if (!el) return;
            const entries = s.transcript.slice(-s.bufferTurns * 2);
            const useRich = !!window.RichRender?.available();
            const html = entries.map((e, i) => {
                const who = `<span class="call-t-who-${e.who}">${e.who}</span>`;
                const cursor = e.streaming ? '<span class="call-t-cursor"></span>' : '';
                // Rich markdown/LaTeX only for finished assistant entries;
                // streaming text may still mutate, so it stays plain-escaped.
                const rich = useRich && e.who === 'zephyro' && !e.streaming;
                const textHtml = rich
                    ? `<span class="call-t-text" data-rich-slot="${i}"></span>`
                    : `<span class="call-t-text">${escapeHtml(e.text)}</span>`;
                return `<div class="call-t-entry ${e.streaming ? 'streaming' : ''}">
                    <span class="call-t-ts">[${e.ts}]</span>
                    ${who}
                    <span class="call-t-arrow">→</span>
                    ${textHtml}${cursor}
                </div>`;
            }).join('');
            el.innerHTML = html;
            if (useRich) {
                // Memoize rendered nodes by entry id: the transcript re-renders
                // on every state tick and re-running markdown+KaTeX each time
                // is wasteful. Pruned to the visible window, so it can't grow.
                if (!this._richCache) this._richCache = new Map();
                const liveIds = new Set();
                el.querySelectorAll('[data-rich-slot]').forEach((slot) => {
                    const e = entries[Number(slot.dataset.richSlot)];
                    if (!e) return;
                    liveIds.add(e.id);
                    let node = this._richCache.get(e.id);
                    if (!node) {
                        node = window.RichRender.render(e.text);
                        this._richCache.set(e.id, node);
                    }
                    slot.appendChild(node);
                });
                for (const id of [...this._richCache.keys()]) {
                    if (!liveIds.has(id)) this._richCache.delete(id);
                }
            }
            el.scrollTop = el.scrollHeight;
        },

        destroy() {
            // Releases all listeners and resources registered during mount().
            // Currently CallMode.mount() is treated as a one-shot init at app
            // boot, but this method exists so a future "tear down call mode"
            // flow (or hot reload) doesn't leak Tauri unlisten handles, window
            // event listeners, CallState subscriptions, timers, or audio nodes.
            if (!this._mounted) return;
            this._stopTimers();
            if (this._diagWarnTimer) { clearTimeout(this._diagWarnTimer); this._diagWarnTimer = null; }
            this._lastSpokenDiagKey = null;
            this._matrix?.stop();
            this._orb?.destroy?.();
            this._detachTts();
            try { this._unsub?.(); } catch (_) { /* noop */ }
            this._unsub = null;
            if (Array.isArray(this._cleanup)) {
                for (const fn of this._cleanup) {
                    try { fn(); } catch (_) { /* noop */ }
                }
            }
            this._cleanup = [];
            this._richCache?.clear();
            this._mounted = false;
            this._keysBound = false;
            this._orb = null;
            this._matrix = null;
        },
    };

    function escapeHtml(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    window.CallMode = CallMode;
})();
