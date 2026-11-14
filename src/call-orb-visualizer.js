// AudioOrbVisualizer — FFT ring (64 bars) + central orb + crosshairs.
// States: 'idle'|'listening'|'thinking'|'researching'|'speaking'|'cancelled'
//         'pomodoro_work'|'pomodoro_break'
(function () {
    const NS = 'http://www.w3.org/2000/svg';
    const BARS = 64;
    const RING_INNER = 140;
    const BAR_MIN = 8;
    const BAR_MAX = 70;

    const COLORS = {
        idle:           { ring: '#1f5a4a', orb: '#1a4d3d', glow: '#1a4d3d', label: '#4c9b7e' },
        listening:      { ring: '#00FF88', orb: '#00FF88', glow: '#00FF88', label: '#00FF88' },
        thinking:       { ring: '#FFA726', orb: '#FFA726', glow: '#8a5a12', label: '#FFA726' },
        researching:    { ring: '#22D3EE', orb: '#0EA5E9', glow: '#0EA5E9', label: '#22D3EE' },
        speaking:       { ring: '#06B6D4', orb: '#06B6D4', glow: '#06B6D4', label: '#06B6D4' },
        cancelled:      { ring: '#EF4444', orb: '#EF4444', glow: '#EF4444', label: '#EF4444' },
        pomodoro_work:  { ring: '#FFEB3B', orb: '#FFEB3B', glow: '#FFA000', label: '#FFEB3B' },
        pomodoro_break: { ring: '#00CC66', orb: '#00CC66', glow: '#007040', label: '#00CC66' },
    };

    class AudioOrbVisualizer {
        constructor(container, { size = 520 } = {}) {
            this.container = container;
            this.size = size;
            this.cx = size / 2;
            this.cy = size / 2;
            this.phase = 'idle';
            this.analyser = null;
            this.freqData = null;
            // Separate analyser for TTS audio. The mic gets gated/muted while
            // Zephyro speaks (anti-echo), so the orb has nothing to react to
            // during the 'speaking' phase. We pick the TTS analyser instead.
            this.ttsAnalyser = null;
            this.ttsFreqData = null;
            this.running = false;
            this.rafId = null;
            this.lastIdleBar = new Array(BARS).fill(0);
            this.idleTime = 0;
            // Color de fase interpolado en cada frame (transición suave entre
            // estados). El fill de SVG y las custom properties no transicionan
            // por CSS de forma fiable en WebKitGTK, así que lo hacemos en _tick.
            const _idle = COLORS.idle;
            this._curRing = this._hexToRgb(_idle.ring);
            this._curOrb = this._hexToRgb(_idle.orb);
            this._targetRing = { ...this._curRing };
            this._targetOrb = { ...this._curOrb };
            this._curHaloOp = 0.3;
            this._targetHaloOp = 0.3;
            this._build();
        }

        _build() {
            const size = this.size;
            const svg = document.createElementNS(NS, 'svg');
            svg.setAttribute('viewBox', `0 0 ${size} ${size}`);
            svg.setAttribute('class', 'call-orb-svg');
            svg.setAttribute('width', '100%');
            svg.setAttribute('height', '100%');
            svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');

            // Defs — glow filter
            const defs = document.createElementNS(NS, 'defs');
            defs.innerHTML = `
                <filter id="orb-glow" x="-50%" y="-50%" width="200%" height="200%">
                    <feGaussianBlur stdDeviation="8" result="blur"/>
                    <feMerge>
                        <feMergeNode in="blur"/>
                        <feMergeNode in="SourceGraphic"/>
                    </feMerge>
                </filter>
                <radialGradient id="orb-grad" cx="50%" cy="50%" r="50%">
                    <stop offset="0%" stop-color="var(--orb-color, #00FF88)" stop-opacity="1"/>
                    <stop offset="60%" stop-color="var(--orb-color, #00FF88)" stop-opacity="0.6"/>
                    <stop offset="100%" stop-color="var(--orb-color, #00FF88)" stop-opacity="0"/>
                </radialGradient>
            `;
            svg.appendChild(defs);

            // Outer dashed circles (tactical rings)
            const ringRadii = [180, 200, 220];
            ringRadii.forEach((r, i) => {
                const c = document.createElementNS(NS, 'circle');
                c.setAttribute('cx', this.cx);
                c.setAttribute('cy', this.cy);
                c.setAttribute('r', r);
                c.setAttribute('fill', 'none');
                c.setAttribute('stroke', 'currentColor');
                c.setAttribute('stroke-width', '1');
                c.setAttribute('stroke-opacity', 0.15 + i * 0.05);
                c.setAttribute('stroke-dasharray', i === 1 ? '4 6' : '2 4');
                c.classList.add('call-orb-tactical-ring');
                svg.appendChild(c);
            });

            // Crosshairs N/S/E/W labels
            const labels = [
                { x: this.cx, y: this.cy - 230, t: 'N' },
                { x: this.cx, y: this.cy + 240, t: 'S' },
                { x: this.cx - 230, y: this.cy + 4, t: 'W' },
                { x: this.cx + 230, y: this.cy + 4, t: 'E' },
            ];
            labels.forEach(({ x, y, t }) => {
                const tx = document.createElementNS(NS, 'text');
                tx.setAttribute('x', x);
                tx.setAttribute('y', y);
                tx.setAttribute('text-anchor', 'middle');
                tx.setAttribute('class', 'call-orb-cross-label');
                tx.textContent = t;
                svg.appendChild(tx);
            });

            // Crosshair ticks (12 around)
            const tickGroup = document.createElementNS(NS, 'g');
            tickGroup.setAttribute('class', 'call-orb-ticks');
            for (let i = 0; i < 24; i++) {
                const a = (i / 24) * Math.PI * 2 - Math.PI / 2;
                const r1 = 225;
                const r2 = i % 6 === 0 ? 235 : 230;
                const x1 = this.cx + Math.cos(a) * r1;
                const y1 = this.cy + Math.sin(a) * r1;
                const x2 = this.cx + Math.cos(a) * r2;
                const y2 = this.cy + Math.sin(a) * r2;
                const ln = document.createElementNS(NS, 'line');
                ln.setAttribute('x1', x1);
                ln.setAttribute('y1', y1);
                ln.setAttribute('x2', x2);
                ln.setAttribute('y2', y2);
                ln.setAttribute('stroke', 'currentColor');
                ln.setAttribute('stroke-width', '1');
                ln.setAttribute('stroke-opacity', 0.35);
                tickGroup.appendChild(ln);
            }
            svg.appendChild(tickGroup);

            // Bars group (64 FFT bars around ring)
            this.barsGroup = document.createElementNS(NS, 'g');
            this.barsGroup.setAttribute('class', 'call-orb-bars');
            this.bars = [];
            for (let i = 0; i < BARS; i++) {
                const rect = document.createElementNS(NS, 'rect');
                rect.setAttribute('width', 3);
                rect.setAttribute('height', BAR_MIN);
                rect.setAttribute('rx', 1.5);
                rect.setAttribute('fill', 'currentColor');
                this.barsGroup.appendChild(rect);
                this.bars.push(rect);
            }
            svg.appendChild(this.barsGroup);

            // Central orb (two concentric circles + halo)
            this.halo = document.createElementNS(NS, 'circle');
            this.halo.setAttribute('cx', this.cx);
            this.halo.setAttribute('cy', this.cy);
            this.halo.setAttribute('r', 80);
            this.halo.setAttribute('fill', 'url(#orb-grad)');
            this.halo.setAttribute('filter', 'url(#orb-glow)');
            this.halo.setAttribute('opacity', '0.85');
            this.halo.classList.add('call-orb-halo');
            svg.appendChild(this.halo);

            this.core = document.createElementNS(NS, 'circle');
            this.core.setAttribute('cx', this.cx);
            this.core.setAttribute('cy', this.cy);
            this.core.setAttribute('r', 26);
            this.core.setAttribute('fill', 'currentColor');
            this.core.classList.add('call-orb-core-dot');
            svg.appendChild(this.core);

            this.container.innerHTML = '';
            this.container.appendChild(svg);
            this.svg = svg;

            this._layoutBars();
            this.setState('idle');
        }

        _layoutBars() {
            for (let i = 0; i < BARS; i++) {
                const angle = (i / BARS) * Math.PI * 2 - Math.PI / 2;
                const x = this.cx + Math.cos(angle) * RING_INNER;
                const y = this.cy + Math.sin(angle) * RING_INNER;
                const rot = (angle * 180) / Math.PI + 90;
                const rect = this.bars[i];
                rect.setAttribute('x', x - 1.5);
                rect.setAttribute('y', y);
                rect.setAttribute('transform', `rotate(${rot} ${x} ${y})`);
            }
        }

        attachAnalyser(analyser) {
            this.analyser = analyser;
            if (analyser) {
                this.freqData = new Uint8Array(analyser.frequencyBinCount);
            } else {
                this.freqData = null;
            }
        }

        attachTtsAnalyser(analyser) {
            this.ttsAnalyser = analyser;
            if (analyser) {
                this.ttsFreqData = new Uint8Array(analyser.frequencyBinCount);
            } else {
                this.ttsFreqData = null;
            }
        }

        _hexToRgb(hex) {
            const h = hex.replace('#', '');
            return {
                r: parseInt(h.slice(0, 2), 16),
                g: parseInt(h.slice(2, 4), 16),
                b: parseInt(h.slice(4, 6), 16),
            };
        }

        _applyColors() {
            const ring = `rgb(${this._curRing.r | 0},${this._curRing.g | 0},${this._curRing.b | 0})`;
            const orb = `rgb(${this._curOrb.r | 0},${this._curOrb.g | 0},${this._curOrb.b | 0})`;
            this.svg.style.color = ring;                       // currentColor: rings, bars, ticks
            this.svg.style.setProperty('--orb-color', orb);    // radial gradient del halo
            this.core.setAttribute('fill', orb);
            this.halo.setAttribute('opacity', this._curHaloOp.toFixed(3));
        }

        // Estado de SESIÓN (pomodoro) derivado de la fuente de verdad (CallState),
        // NO del micPhase. El pomodoro es un estado de FONDO de larga duración; el
        // micPhase (listening/speaking/thinking) es momentáneo y se pisa en cada
        // turno. Leerlo acá hace que el orbe refleje el pomodoro de forma estable
        // sin depender de que alguien llame setState('pomodoro_work').
        _sessionPhase() {
            const s = window.CallState ? window.CallState.get() : null;
            if (!s || !s.pomodoroStartedAt || !s.pomodoroTotalSec) return null;
            if (s.pomodoroPhase === 'work') return 'pomodoro_work';
            if (s.pomodoroPhase === 'break') return 'pomodoro_break';
            return null;
        }

        // Fase EFECTIVA = combinación de micPhase + sessionPhase con prioridad:
        // los estados de interacción (speaking/thinking/researching/cancelled) ganan
        // mientras ocurren (feedback inmediato); en reposo (idle/listening) cae al
        // pomodoro si hay sesión activa. Color y animación usan esta única fuente.
        _effectivePhase() {
            const mic = this.phase;
            if (mic === 'speaking' || mic === 'thinking'
                || mic === 'researching' || mic === 'cancelled') {
                return mic;
            }
            return this._sessionPhase() || mic;
        }

        _resolveColorTargets(phase) {
            const c = COLORS[phase] || COLORS.idle;
            this._targetRing = this._hexToRgb(c.ring);
            this._targetOrb = this._hexToRgb(c.orb);
            this._targetHaloOp = phase === 'thinking' || phase === 'researching' ? 0.4
                               : phase === 'cancelled' ? 0.95
                               : phase === 'idle' ? 0.3 : 0.85;
        }

        setState(phase) {
            if (!COLORS[phase]) phase = 'idle';
            this.phase = phase;
            // Los destinos de color se calculan desde la fase EFECTIVA (no el
            // micPhase crudo), así un setState('listening') durante un pomodoro
            // no apaga el amarillo. _tick los recalcula cada frame.
            const eff = this._effectivePhase();
            this._resolveColorTargets(eff);
            // Si el loop no está corriendo (orbe oculto / primer setState), no habrá
            // _tick que interpole — aplicar de inmediato para no quedar en idle.
            if (!this.running) {
                this._curRing = { ...this._targetRing };
                this._curOrb = { ...this._targetOrb };
                this._curHaloOp = this._targetHaloOp;
                this._applyColors();
            }
            this.svg.classList.remove(
                'phase-idle', 'phase-listening', 'phase-thinking',
                'phase-researching', 'phase-speaking', 'phase-cancelled',
                'phase-pomodoro_work', 'phase-pomodoro_break'
            );
            this.svg.classList.add('phase-' + eff);
        }

        start() {
            if (this.running) return;
            this.running = true;
            const loop = () => {
                if (!this.running) return;
                this._tick();
                this.rafId = requestAnimationFrame(loop);
            };
            loop();
        }

        stop() {
            this.running = false;
            if (this.rafId) cancelAnimationFrame(this.rafId);
            this.rafId = null;
        }

        _tick() {
            // Fase EFECTIVA (micPhase + pomodoro de fondo). Se reevalúa cada frame
            // porque el sessionPhase puede cambiar sin un setState (el pomodoro
            // arranca/termina, o el micPhase pasa a reposo) — así el color sigue
            // al pomodoro de forma estable.
            const phase = this._effectivePhase();
            const t = performance.now() / 1000;
            let intensity = 0;

            // Recalcular destinos de color cada frame desde la fase efectiva.
            this._resolveColorTargets(phase);

            // Transición suave de color/halo hacia la fase actual (~250-300ms).
            // Smoothing exponencial: a 60fps converge en ~15 frames con LERP=0.18.
            const LERP = 0.18;
            this._curRing.r += (this._targetRing.r - this._curRing.r) * LERP;
            this._curRing.g += (this._targetRing.g - this._curRing.g) * LERP;
            this._curRing.b += (this._targetRing.b - this._curRing.b) * LERP;
            this._curOrb.r += (this._targetOrb.r - this._curOrb.r) * LERP;
            this._curOrb.g += (this._targetOrb.g - this._curOrb.g) * LERP;
            this._curOrb.b += (this._targetOrb.b - this._curOrb.b) * LERP;
            this._curHaloOp += (this._targetHaloOp - this._curHaloOp) * LERP;
            this._applyColors();

            // Pick the right FFT source: TTS analyser while Zephyro speaks
            // (mic is gated then), mic analyser while user speaks.
            let srcAnalyser = null;
            let srcFreqData = null;
            if (phase === 'speaking' && this.ttsAnalyser && this.ttsFreqData) {
                srcAnalyser = this.ttsAnalyser;
                srcFreqData = this.ttsFreqData;
            } else if (phase === 'listening' && this.analyser && this.freqData) {
                srcAnalyser = this.analyser;
                srcFreqData = this.freqData;
            }

            if (srcAnalyser && srcFreqData) {
                srcAnalyser.getByteFrequencyData(srcFreqData);
                // Map FFT bins to 64 bars — skip DC, focus on lower half (voice range)
                const len = srcFreqData.length;
                const usable = Math.floor(len * 0.55);
                const perBar = Math.max(1, Math.floor(usable / BARS));
                for (let i = 0; i < BARS; i++) {
                    let sum = 0;
                    const start = i * perBar + 1;
                    for (let j = 0; j < perBar; j++) sum += srcFreqData[start + j] || 0;
                    let v = sum / perBar / 255;
                    // Soften
                    v = Math.pow(v, 0.85);
                    intensity = Math.max(intensity, v);
                    // Smooth
                    this.lastIdleBar[i] = this.lastIdleBar[i] * 0.6 + v * 0.4;
                    const h = BAR_MIN + this.lastIdleBar[i] * (BAR_MAX - BAR_MIN);
                    this.bars[i].setAttribute('height', h.toFixed(1));
                }
            } else if (phase === 'speaking') {
                // Fallback: TTS analyser hasn't been attached yet (e.g., first utterance).
                // Drive bars procedurally so the orb still has motion synced to the
                // 7Hz core pulse defined below.
                const pulse = 0.55 + Math.sin(t * 7) * 0.25;
                for (let i = 0; i < BARS; i++) {
                    const wob = Math.sin(t * 5 + i * 0.4) * 0.18;
                    const v = Math.max(0.12, pulse + wob);
                    this.lastIdleBar[i] = this.lastIdleBar[i] * 0.55 + v * 0.45;
                    const h = BAR_MIN + this.lastIdleBar[i] * (BAR_MAX - BAR_MIN) * 0.7;
                    this.bars[i].setAttribute('height', h.toFixed(1));
                }
                intensity = 0.6;
            } else if (phase === 'thinking') {
                // Two counter-rotating ripples around the ring + soft breath.
                // Designed to be unmistakably alive — the previous breath × 0.08
                // was visually static. The two ripples create an interference
                // pattern that reads as "actively processing" rather than idle.
                const headA = (t * 0.45) % 1;
                const headB = (1 - (t * 0.30) % 1);
                const breath = 0.55 + Math.sin(t * 1.8) * 0.20;
                for (let i = 0; i < BARS; i++) {
                    const pos = i / BARS;
                    const da = Math.min(Math.abs(pos - headA), Math.abs(pos - headA + 1), Math.abs(pos - headA - 1));
                    const db = Math.min(Math.abs(pos - headB), Math.abs(pos - headB + 1), Math.abs(pos - headB - 1));
                    const litA = Math.max(0, 1 - da / 0.22);
                    const litB = Math.max(0, 1 - db / 0.22) * 0.7;
                    const v = Math.min(1, (litA + litB) * 0.55 + breath * 0.25);
                    this.lastIdleBar[i] = this.lastIdleBar[i] * 0.7 + v * 0.3;
                    const h = BAR_MIN + this.lastIdleBar[i] * (BAR_MAX - BAR_MIN) * 0.55;
                    this.bars[i].setAttribute('height', h.toFixed(1));
                }
                intensity = 0.35;
            } else if (phase === 'researching') {
                // Onda viajera: una "punta de búsqueda" recorre el anillo
                const head = (t * 0.8) % 1;       // 0..1 da la vuelta cada 1.25s
                const tailWidth = 0.18;            // 18% del anillo se ilumina
                for (let i = 0; i < BARS; i++) {
                    const pos = i / BARS;
                    let dist = pos - head;
                    if (dist > 0) dist -= 1;       // wrap negativo: la cabeza viene de adelante
                    dist = Math.abs(dist);
                    const lit = dist < tailWidth ? (1 - dist / tailWidth) : 0;
                    const h = BAR_MIN + lit * (BAR_MAX - BAR_MIN) * 0.65;
                    this.bars[i].setAttribute('height', h.toFixed(1));
                    this.lastIdleBar[i] = lit;
                }
                intensity = 0.25;
            } else if (phase === 'cancelled') {
                // Flash rojo intenso, todas las barras al máximo y decaen
                for (let i = 0; i < BARS; i++) {
                    this.lastIdleBar[i] = Math.max(this.lastIdleBar[i] * 0.85, 0.6);
                    const h = BAR_MIN + this.lastIdleBar[i] * (BAR_MAX - BAR_MIN);
                    this.bars[i].setAttribute('height', h.toFixed(1));
                }
                intensity = 0.5;
            } else if (phase === 'pomodoro_work') {
                // Clockwise progress sweep based on elapsed time of current phase.
                // Bars before the sweep head light up; head bar is brightest.
                const s = window.CallState ? window.CallState.get() : null;
                const startMs = s ? s.pomodoroStartedAt : 0;
                const totalSec = s ? s.pomodoroTotalSec : 1500;
                const elapsed = startMs > 0 ? (Date.now() - startMs) / 1000 : 0;
                const progress = Math.min(1, elapsed / Math.max(1, totalSec));
                const headBar = Math.floor(progress * BARS);
                for (let i = 0; i < BARS; i++) {
                    let v;
                    const dist = ((headBar - i) + BARS) % BARS;
                    if (dist === 0) {
                        v = 0.85 + Math.sin(t * 3) * 0.1;  // tip shimmer
                    } else if (dist < BARS * 0.35) {
                        v = Math.max(0.08, 0.55 * (1 - dist / (BARS * 0.35)));
                    } else {
                        v = 0.04;
                    }
                    this.lastIdleBar[i] = this.lastIdleBar[i] * 0.75 + v * 0.25;
                    const h = BAR_MIN + this.lastIdleBar[i] * (BAR_MAX - BAR_MIN) * 0.65;
                    this.bars[i].setAttribute('height', h.toFixed(1));
                }
                intensity = 0.15;
            } else if (phase === 'pomodoro_break') {
                // Breathing verde suave — relajante, 0.8 Hz (~1.25s/ciclo)
                const breath = 0.3 + Math.sin(t * 0.8 * Math.PI * 2) * 0.25;
                for (let i = 0; i < BARS; i++) {
                    const wob = Math.sin(t * 0.5 + i * 0.12) * 0.04;
                    const v = Math.max(0.05, breath + wob);
                    this.lastIdleBar[i] = this.lastIdleBar[i] * 0.8 + v * 0.2;
                    const h = BAR_MIN + this.lastIdleBar[i] * (BAR_MAX - BAR_MIN) * 0.45;
                    this.bars[i].setAttribute('height', h.toFixed(1));
                }
                intensity = 0.08;
            } else {
                // Idle — decay
                for (let i = 0; i < BARS; i++) {
                    this.lastIdleBar[i] *= 0.88;
                    const h = BAR_MIN + this.lastIdleBar[i] * 10;
                    this.bars[i].setAttribute('height', h.toFixed(1));
                }
            }

            // Core pulse — speed depends on phase
            let coreR = 26;
            if (phase === 'speaking') {
                coreR = 24 + Math.sin(t * 7) * 4 + intensity * 8;
            } else if (phase === 'listening') {
                coreR = 24 + Math.sin(t * 2.2) * 2 + intensity * 6;
            } else if (phase === 'thinking') {
                // Dual-frequency pulse: slow underlying breath + faster overlay
                // so the core appears to "think harder" rather than idle.
                coreR = 24 + Math.sin(t * 1.8) * 3 + Math.sin(t * 4.4) * 1.5;
            } else if (phase === 'researching') {
                coreR = 23 + Math.sin(t * 3.0) * 2.5;
            } else if (phase === 'cancelled') {
                coreR = 30 + Math.sin(t * 12) * 3;
            } else if (phase === 'pomodoro_work') {
                coreR = 25 + Math.sin(t * 1.2) * 2.5;
            } else if (phase === 'pomodoro_break') {
                coreR = 23 + Math.sin(t * 0.8 * Math.PI * 2) * 4;
            } else {
                coreR = 20 + Math.sin(t * 1.0) * 1;
            }
            this.core.setAttribute('r', coreR.toFixed(1));

            // Halo breathe
            const haloSpeed = phase === 'speaking' ? 5
                            : phase === 'researching' ? 3
                            : phase === 'cancelled' ? 8
                            : phase === 'pomodoro_break' ? (0.8 * Math.PI * 2)
                            : 1.8;
            const haloR = 70 + Math.sin(t * haloSpeed) * 6 + intensity * 20;
            this.halo.setAttribute('r', haloR.toFixed(1));
        }

        destroy() {
            this.stop();
            this.analyser = null;
            this.freqData = null;
            this.ttsAnalyser = null;
            this.ttsFreqData = null;
            if (this.container) this.container.innerHTML = '';
        }
    }

    window.AudioOrbVisualizer = AudioOrbVisualizer;
})();
