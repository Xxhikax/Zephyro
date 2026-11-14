// Call state store — pub/sub. Owns everything the HUD renders.
// States: 'idle' | 'listening' | 'thinking' | 'researching' | 'speaking' | 'cancelled'
//         'pomodoro_work' | 'pomodoro_break'
//   - 'thinking'       → LLM cloud razonando
//   - 'researching'    → búsqueda web en curso
//   - 'cancelled'      → flash efímero post-barge-in (vuelve a 'listening' solo)
//   - 'pomodoro_work'  → sesión de trabajo Pomodoro activa
//   - 'pomodoro_break' → descanso Pomodoro activo
(function () {
    const state = {
        phase: 'idle',
        statusMessage: '',
        startedAt: 0,
        sessionId: null,
        codec: 'OPUS/48k',
        sttEngine: 'WHISPER_LOCAL',
        model: '—',
        provider: 'auto',
        mode: 'local',
        latencyMs: 0,
        sttAvgMs: 0,
        llmAvgMs: 0,
        ttsAvgMs: 0,
        totalAvgMs: 0,
        micGain: 0,
        micDevice: '—',
        micSampleRate: 16000,
        noiseGate: 0,
        vadProb: 0,
        vadSilenceMs: 0,
        vadChunks: 0,
        vadDetected: false,
        pipeline: { mic: false, stt: false, llm: false, search: false, tts: false },
        turns: 0,
        tokensIn: 0,
        tokensOut: 0,
        costUsd: 0,
        interrupts: 0,
        activeContext: [],
        transcript: [],
        bufferTurns: 4,
        muted: false,
        background: false,
        pendingDiagNotification: null,  // {request_id, hypothesis_summary} — show when we return to listening
        // Pomodoro fields — updated via WS type:"pomodoro" events
        pomodoroStartedAt: 0,   // Unix ms timestamp when current phase started
        pomodoroTotalSec: 0,    // Duration of current phase in seconds
        pomodoroCycle: 0,       // Current cycle number (1-based)
        pomodoroTotalCycles: 4,
        pomodoroTask: '',
        pomodoroPhase: null,           // 'work' | 'break' | null — coarse phase
        pomodoroPhaseDetail: null,     // 'work' | 'short_break' | 'long_break' | null
        pomodoroPaused: false,         // true while a pause is in effect
        pomodoroNextPhase: '',         // '' | 'work' | 'short_break' | 'long_break'
        pomodoroNextDurationSec: 0,    // Seconds of the upcoming phase (for "luego: …")
        pomodoroExtendPendingMin: 0,   // Minutes queued to be added by _cancellable_sleep
    };

    const listeners = new Set();

    function emit() {
        for (const fn of listeners) {
            try { fn(state); } catch (e) { console.warn('[call-state] listener error:', e); }
        }
    }

    function subscribe(fn) {
        listeners.add(fn);
        fn(state);
        return () => listeners.delete(fn);
    }

    function patch(partial) {
        Object.assign(state, partial);
        emit();
    }

    function setPhase(phase) {
        if (state.phase === phase) return;
        state.phase = phase;
        if (phase !== 'thinking' && phase !== 'researching') state.statusMessage = '';
        // Pipeline arrows follow phase
        const isProcessing = phase === 'thinking' || phase === 'researching';
        const isPomodoro = phase === 'pomodoro_work' || phase === 'pomodoro_break';
        // Cada flecha refleja su etapa real. El HUD renderiza 4 flechas
        // (mic/stt/llm/tts; no hay flecha 'search'). No hay fase STT dedicada
        // (la transcripción se mapea a 'thinking'), así que no encendemos 'stt'
        // falsamente — el arrow de mic ya indica que hubo captura. 'llm' enciende
        // en todo el procesamiento (thinking y researching, porque la búsqueda
        // sintetiza con el LLM). 'search' se mantiene para forma/futuro.
        state.pipeline = {
            mic: phase === 'listening' || isProcessing || isPomodoro,
            stt: false,
            llm: isProcessing,
            search: phase === 'researching',
            tts: phase === 'speaking',
        };
        // Mic queda visualmente "atenuado" cuando Zephyro está hablando o procesando.
        // El consumidor (HUD/orb) lee state.micGated para pintar el indicador en rojo.
        // En fases pomodoro el mic sigue activo — el usuario puede hablar para comandos.
        state.micGated = isProcessing || phase === 'speaking';
        emit();

        // Flush diagnostic notification if we just returned to a quiet phase.
        if ((phase === 'listening' || phase === 'idle') && state.pendingDiagNotification) {
            const pending = state.pendingDiagNotification;
            state.pendingDiagNotification = null;
            try {
                window.dispatchEvent(new CustomEvent('zephyro:diag-flush', { detail: pending }));
            } catch (_) {}
        }
    }

    function flashCancelled() {
        // Fase efímera para que el orb haga flash — luego el caller llama setPhase('listening')
        state.phase = 'cancelled';
        state.interrupts = (state.interrupts || 0) + 1;
        emit();
    }

    function startSession(sessionId) {
        state.sessionId = sessionId || 'call_' + Math.random().toString(36).slice(2, 6);
        state.startedAt = Date.now();
        state.phase = 'listening';
        state.transcript = [];
        state.turns = 0;
        state.tokensIn = 0;
        state.tokensOut = 0;
        state.costUsd = 0;
        state.interrupts = 0;
        emit();
    }

    function endSession() {
        state.phase = 'idle';
        state.statusMessage = '';
        state.startedAt = 0;
        state.sessionId = null;
        state.pipeline = { mic: false, stt: false, llm: false, search: false, tts: false };
        emit();
    }

    function appendTranscript(entry) {
        // entry: { who: 'bruno'|'zephyro', text, ts, streaming? }
        const now = Date.now();
        const elapsed = state.startedAt ? Math.max(0, now - state.startedAt) : 0;
        const mm = String(Math.floor(elapsed / 60000)).padStart(2, '0');
        const ss = String(Math.floor((elapsed / 1000) % 60)).padStart(2, '0');
        state.transcript.push({
            who: entry.who,
            text: entry.text,
            ts: entry.ts || `${mm}:${ss}`,
            streaming: !!entry.streaming,
            id: now + Math.random(),
        });
        // Keep last N entries (bufferTurns * 2 speakers worst case + some slack)
        const cap = Math.max(8, state.bufferTurns * 4);
        if (state.transcript.length > cap) {
            state.transcript.splice(0, state.transcript.length - cap);
        }
        emit();
    }

    function updateStreamingTranscript(text) {
        // Replace or create the active streaming entry
        const last = state.transcript[state.transcript.length - 1];
        if (last && last.streaming) {
            last.text = text;
        } else {
            appendTranscript({ who: 'bruno', text, streaming: true });
            return;
        }
        emit();
    }

    function finalizeStreamingTranscript() {
        const last = state.transcript[state.transcript.length - 1];
        if (last && last.streaming) {
            last.streaming = false;
            emit();
        }
    }

    function incTurn() {
        state.turns += 1;
        emit();
    }

    function ingestMetrics(m) {
        // m: { turns, tokens_in, tokens_out, cost_usd, interrupts, ... }
        if (typeof m.turns === 'number') state.turns = m.turns;
        if (typeof m.tokens_in === 'number') state.tokensIn = m.tokens_in;
        if (typeof m.tokens_out === 'number') state.tokensOut = m.tokens_out;
        if (typeof m.cost_usd === 'number') state.costUsd = m.cost_usd;
        if (typeof m.interrupts === 'number') state.interrupts = m.interrupts;
        if (m.active_context) state.activeContext = m.active_context;
        emit();
    }

    function recordLatency(bucket, ms) {
        // bucket: 'stt' | 'llm' | 'tts'
        const key = bucket + 'AvgMs';
        const prev = state[key] || 0;
        // Simple EMA so the display feels alive without jitter
        state[key] = prev === 0 ? ms : Math.round(prev * 0.7 + ms * 0.3);
        state.totalAvgMs = state.sttAvgMs + state.llmAvgMs + state.ttsAvgMs;
        emit();
    }

    function get() {
        return state;
    }

    window.CallState = {
        subscribe, patch, setPhase, flashCancelled,
        startSession, endSession,
        appendTranscript, updateStreamingTranscript, finalizeStreamingTranscript,
        incTurn, ingestMetrics, recordLatency,
        get,
    };
})();
