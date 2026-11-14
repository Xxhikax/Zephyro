/**
 * login.js — Zephyro Tactical Command Interface v2
 *
 * Boot sequence timings (ms):
 *   50   → topbar
 *   300  → orb (fade+blur)
 *   500  → SYSTEM_TELEMETRY
 *   700  → NETWORK_MAP (nodes line-by-line +80ms each)
 *   1100 → ACTIVE_MODULES (items one-by-one +150ms each)
 *   1300 → AGENT_ROUTING
 *   1700 → LAST_EVENTS
 *   1900 → SECURITY
 *   2200 → ZEPHYRO title (glow)
 *   2300 → WARNING box
 *   2600 → login form
 *   3000 → encrypted footer
 *   3200 → shortcuts bar
 */
(function () {
    'use strict';

    const REDUCED_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    /* ════════════════════════════════════════════════════════
       SystemStatus — WS /api/system/telemetry
       ════════════════════════════════════════════════════════ */
    class SystemStatus {
        constructor() {
            this._ws            = null;
            this._listeners     = [];
            this._reconnectTimer= null;
            this._running       = false;
            this._uptimeBase    = null;
            this._uptimeBaseAt  = null;
        }

        connect() {
            if (this._running) return;
            this._running = true;
            this._open();
        }

        disconnect() {
            this._running = false;
            clearTimeout(this._reconnectTimer);
            this._ws?.close();
            this._ws = null;
        }

        onUpdate(fn) { this._listeners.push(fn); }

        getLocalUptime() {
            if (this._uptimeBase === null) return null;
            const elapsed = Math.floor((Date.now() - this._uptimeBaseAt) / 1000);
            const total = this._uptimeBase + elapsed;
            const h = Math.floor(total / 3600);
            const m = Math.floor((total % 3600) / 60);
            const s = total % 60;
            return `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
        }

        _open() {
            if (!this._running) return;
            try {
                this._ws = new WebSocket(`${WS_BASE}/api/system/telemetry`);
                this._ws.onmessage = (ev) => {
                    try {
                        const data = JSON.parse(ev.data);
                        if (data.uptime && this._uptimeBase === null) {
                            const parts = data.uptime.split(':').map(Number);
                            this._uptimeBase   = parts[0]*3600 + parts[1]*60 + parts[2];
                            this._uptimeBaseAt = Date.now();
                        }
                        for (const fn of this._listeners) fn(data);
                    } catch (_) {}
                };
                this._ws.onclose = () => this._scheduleReconnect();
                this._ws.onerror = () => this._ws?.close();
            } catch (_) {
                this._scheduleReconnect();
            }
        }

        _scheduleReconnect() {
            if (!this._running) return;
            this._reconnectTimer = setTimeout(() => this._open(), 4000);
        }
    }

    /* ════════════════════════════════════════════════════════
       BackgroundCanvas
       Layers: dot grid → radial pulses → particles → scanline
       ════════════════════════════════════════════════════════ */
    class BackgroundCanvas {
        constructor(canvas) {
            this._canvas     = canvas;
            this._ctx        = canvas.getContext('2d');
            this._particles  = [];
            this._pulses     = [];
            this._scanY      = -100;
            this._scanOn     = false;
            this._animId     = null;
            this._running    = false;
            this._pulseTimers= [];
            this._scanTimer  = null;
            this._orbCenter  = { x: 0, y: 0 };
            this.NUM      = 70;
            this.MAX_DIST = 100;
            this.C        = '#00FF88';
            this.GRID     = 48;
        }

        setOrbCenter(x, y) { this._orbCenter = { x, y }; }

        start() {
            if (this._running || REDUCED_MOTION) return;
            this._running = true;
            this._resize();
            this._spawnParticles();
            this._loop();
            // 3 staggered radial pulses, each group repeats every 4s
            for (let i = 0; i < 3; i++) {
                setTimeout(() => {
                    if (!this._running) return;
                    this._spawnPulse();
                    const id = setInterval(() => { if (this._running) this._spawnPulse(); }, 4000);
                    this._pulseTimers.push(id);
                }, i * 1300);
            }
            // Scanline: first at 2.5s, then every 8s
            setTimeout(() => { if (this._running) this._startScanline(); }, 2500);
            this._scanTimer = setInterval(() => { if (this._running) this._startScanline(); }, 8000);
            window.addEventListener('resize', this._onResize);
        }

        stop() {
            this._running = false;
            if (this._animId) { cancelAnimationFrame(this._animId); this._animId = null; }
            this._pulseTimers.forEach(clearInterval);
            this._pulseTimers = [];
            clearInterval(this._scanTimer);
            window.removeEventListener('resize', this._onResize);
        }

        _onResize = () => {
            if (!this._running) return;
            this._resize();
            this._spawnParticles();
        };

        _resize() {
            const w = this._canvas.offsetWidth  || window.innerWidth;
            const h = this._canvas.offsetHeight || window.innerHeight;
            this._canvas.width  = w;
            this._canvas.height = h;
            if (!this._orbCenter.x) {
                this._orbCenter = { x: w / 2, y: h * 0.38 };
            }
        }

        _spawnParticles() {
            const w = this._canvas.width, h = this._canvas.height;
            this._particles = Array.from({ length: this.NUM }, () => ({
                x:  Math.random() * w,
                y:  Math.random() * h,
                vx: (Math.random() - 0.5) * 0.22,
                vy: (Math.random() - 0.5) * 0.22,
                r:  Math.random() * 0.9 + 0.4,
            }));
        }

        _spawnPulse() {
            const x   = this._orbCenter.x || this._canvas.width  / 2;
            const y   = this._orbCenter.y || this._canvas.height * 0.38;
            const max = Math.max(this._canvas.width, this._canvas.height) * 0.7;
            this._pulses.push({ x, y, r: 8, maxR: max, alpha: 0.20 });
        }

        _startScanline() {
            if (this._scanOn) return;
            this._scanOn = true;
            this._scanY  = -30;
        }

        _loop = () => {
            if (!this._running) return;
            const ctx = this._ctx;
            const w   = this._canvas.width, h = this._canvas.height;
            ctx.clearRect(0, 0, w, h);

            // Layer 1 — dot grid 12% opacity
            ctx.fillStyle = this.C;
            for (let x = this.GRID; x < w; x += this.GRID) {
                for (let y = this.GRID; y < h; y += this.GRID) {
                    ctx.globalAlpha = 0.12;
                    ctx.beginPath();
                    ctx.arc(x, y, 0.75, 0, Math.PI * 2);
                    ctx.fill();
                }
            }

            // Layer 2 — radial pulses
            for (let i = this._pulses.length - 1; i >= 0; i--) {
                const p = this._pulses[i];
                p.r    += 1.6;
                p.alpha *= 0.983;
                if (p.alpha < 0.003 || p.r > p.maxR) { this._pulses.splice(i, 1); continue; }
                ctx.beginPath();
                ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
                ctx.strokeStyle = this.C;
                ctx.globalAlpha = p.alpha;
                ctx.lineWidth   = 1;
                ctx.stroke();
            }

            // Layer 3 — particle connections
            ctx.lineWidth = 0.4;
            const ps = this._particles;
            for (let i = 0; i < ps.length; i++) {
                for (let j = i + 1; j < ps.length; j++) {
                    const dx = ps[i].x - ps[j].x, dy = ps[i].y - ps[j].y;
                    const d  = Math.sqrt(dx*dx + dy*dy);
                    if (d < this.MAX_DIST) {
                        ctx.beginPath();
                        ctx.moveTo(ps[i].x, ps[i].y);
                        ctx.lineTo(ps[j].x, ps[j].y);
                        ctx.strokeStyle = this.C;
                        ctx.globalAlpha = (1 - d / this.MAX_DIST) * 0.10;
                        ctx.stroke();
                    }
                }
            }
            // Layer 3b — particles
            for (const p of ps) {
                p.x += p.vx; p.y += p.vy;
                if (p.x < 0 || p.x > w) p.vx *= -1;
                if (p.y < 0 || p.y > h) p.vy *= -1;
                ctx.beginPath();
                ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
                ctx.fillStyle   = this.C;
                ctx.globalAlpha = 0.32;
                ctx.fill();
            }

            // Layer 4 — scanline
            if (this._scanOn) {
                this._scanY += (h + 60) / (8 * 60);
                const g = ctx.createLinearGradient(0, this._scanY - 22, 0, this._scanY + 22);
                g.addColorStop(0,   'transparent');
                g.addColorStop(0.5, 'rgba(0,255,136,0.07)');
                g.addColorStop(1,   'transparent');
                ctx.fillStyle   = g;
                ctx.globalAlpha = 1;
                ctx.fillRect(0, this._scanY - 22, w, 44);
                if (this._scanY > h + 30) this._scanOn = false;
            }

            ctx.globalAlpha = 1;
            this._animId = requestAnimationFrame(this._loop);
        };
    }

    /* ════════════════════════════════════════════════════════
       OrbCanvas — fully canvas-based animated orb
       ════════════════════════════════════════════════════════ */
    class OrbCanvas {
        constructor(container) {
            this._container = container;
            this._canvas    = document.createElement('canvas');
            this._canvas.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;display:block;';
            container.style.position = 'relative';
            container.appendChild(this._canvas);
            this._ctx      = this._canvas.getContext('2d');
            this._animId   = null;
            this._t0       = null;
            this._expPulses= [];
            this._expTimer = null;
            this.S         = 220;
        }

        start() {
            if (this._animId) return;
            this._canvas.width  = this.S;
            this._canvas.height = this.S;
            this._t0 = performance.now();
            this._expTimer = setInterval(() => this._spawnExp(), 3000);
            this._spawnExp();
            this._loop();
        }

        stop() {
            if (this._animId) { cancelAnimationFrame(this._animId); this._animId = null; }
            clearInterval(this._expTimer);
        }

        _spawnExp() { this._expPulses.push({ born: performance.now() }); }

        _loop = () => {
            const ctx = this._ctx;
            const s = this.S, cx = s/2, cy = s/2;
            const now = performance.now();
            const t   = (now - this._t0) / 1000;
            const G   = '#00FF88';

            ctx.clearRect(0, 0, s, s);

            // Background radial glow
            const glow = ctx.createRadialGradient(cx, cy, 0, cx, cy, 95);
            glow.addColorStop(0, 'rgba(0,255,136,0.07)');
            glow.addColorStop(1, 'rgba(0,255,136,0)');
            ctx.fillStyle   = glow;
            ctx.globalAlpha = 1;
            ctx.fillRect(0, 0, s, s);

            // Outer dashed ring — very slow rotation 25s
            ctx.save();
            ctx.translate(cx, cy);
            ctx.rotate((t / 25) * Math.PI * 2);
            ctx.translate(-cx, -cy);
            ctx.beginPath();
            ctx.arc(cx, cy, 90, 0, Math.PI * 2);
            ctx.strokeStyle = G;
            ctx.globalAlpha = 0.18;
            ctx.lineWidth   = 1;
            ctx.setLineDash([7, 5, 2, 5]);
            ctx.stroke();
            ctx.setLineDash([]);
            ctx.restore();

            // Middle ring — pulsing r 48→52 every 2.4s
            const pulse = Math.sin(t * Math.PI * 2 / 2.4);
            const midR  = 50 + pulse * 2;
            ctx.beginPath();
            ctx.arc(cx, cy, midR, 0, Math.PI * 2);
            ctx.strokeStyle = G;
            ctx.globalAlpha = 0.38;
            ctx.lineWidth   = 1.5;
            ctx.setLineDash([]);
            ctx.stroke();

            // Secondary slow counter-rotating ring
            ctx.save();
            ctx.translate(cx, cy);
            ctx.rotate(-(t / 18) * Math.PI * 2);
            ctx.translate(-cx, -cy);
            ctx.beginPath();
            ctx.arc(cx, cy, 70, 0, Math.PI * 2);
            ctx.strokeStyle = G;
            ctx.globalAlpha = 0.10;
            ctx.lineWidth   = 0.5;
            ctx.setLineDash([3, 10]);
            ctx.stroke();
            ctx.setLineDash([]);
            ctx.restore();

            // Inner ring — synchronized pulse
            const innerR = 30 + pulse * 1.5;
            ctx.beginPath();
            ctx.arc(cx, cy, innerR, 0, Math.PI * 2);
            ctx.strokeStyle = G;
            ctx.globalAlpha = 0.22;
            ctx.lineWidth   = 1;
            ctx.setLineDash([4, 4]);
            ctx.stroke();
            ctx.setLineDash([]);

            // Radar sweep + 3 ghost trails (revolution every 4s)
            const sweepAngle = (t / 4) * Math.PI * 2;
            const trails = [
                { off: 0,     alpha: 0.55 },
                { off: -0.22, alpha: 0.28 },
                { off: -0.44, alpha: 0.13 },
                { off: -0.66, alpha: 0.05 },
            ];
            for (const tr of trails) {
                const a = sweepAngle + tr.off;
                ctx.save();
                ctx.translate(cx, cy);
                ctx.beginPath();
                ctx.moveTo(0, 0);
                ctx.lineTo(Math.cos(a) * 88, Math.sin(a) * 88);
                ctx.strokeStyle = G;
                ctx.globalAlpha = tr.alpha;
                ctx.lineWidth   = 1;
                ctx.stroke();
                ctx.restore();
            }

            // Radar fan fill
            ctx.save();
            ctx.translate(cx, cy);
            ctx.beginPath();
            ctx.moveTo(0, 0);
            ctx.arc(0, 0, 88, sweepAngle - 1.1, sweepAngle);
            ctx.closePath();
            const fanG = ctx.createRadialGradient(0, 0, 0, 0, 0, 88);
            fanG.addColorStop(0, 'rgba(0,255,136,0.10)');
            fanG.addColorStop(1, 'rgba(0,255,136,0)');
            ctx.fillStyle   = fanG;
            ctx.globalAlpha = 1;
            ctx.fill();
            ctx.restore();

            // Expansion pulses every 3s (20px→100px fadeout 1.8s)
            for (let i = this._expPulses.length - 1; i >= 0; i--) {
                const ep = this._expPulses[i];
                const progress = (now - ep.born) / 1800;
                if (progress >= 1) { this._expPulses.splice(i, 1); continue; }
                const r = 20 + progress * 80;
                const a = (1 - progress) * 0.45;
                ctx.beginPath();
                ctx.arc(cx, cy, r, 0, Math.PI * 2);
                ctx.strokeStyle = G;
                ctx.globalAlpha = a;
                ctx.lineWidth   = 1;
                ctx.stroke();
            }

            // Crosshairs N/S/E/W
            ctx.globalAlpha = 0.28;
            ctx.strokeStyle = G;
            ctx.lineWidth   = 0.6;
            const gap = midR + 4, len = 18;
            for (const [dx, dy] of [[0,-1],[0,1],[-1,0],[1,0]]) {
                ctx.beginPath();
                ctx.moveTo(cx + dx*gap,      cy + dy*gap);
                ctx.lineTo(cx + dx*(gap+len), cy + dy*(gap+len));
                ctx.stroke();
                // tick
                const px = cx + dx*(gap+len), py = cy + dy*(gap+len);
                ctx.beginPath();
                ctx.moveTo(px - dy*3, py - dx*3);
                ctx.lineTo(px + dy*3, py + dx*3);
                ctx.stroke();
            }

            // Center dot + glow
            const dp = 0.65 + 0.35 * Math.sin(t * Math.PI * 2 / 1.2);
            const cg = ctx.createRadialGradient(cx, cy, 0, cx, cy, 14);
            cg.addColorStop(0, `rgba(0,255,136,${(0.45 * dp).toFixed(2)})`);
            cg.addColorStop(1, 'rgba(0,255,136,0)');
            ctx.fillStyle   = cg;
            ctx.globalAlpha = 1;
            ctx.beginPath();
            ctx.arc(cx, cy, 14, 0, Math.PI * 2);
            ctx.fill();
            ctx.beginPath();
            ctx.arc(cx, cy, 3.5, 0, Math.PI * 2);
            ctx.fillStyle   = G;
            ctx.globalAlpha = dp;
            ctx.fill();

            ctx.globalAlpha = 1;
            this._animId = requestAnimationFrame(this._loop);
        };
    }

    /* ════════════════════════════════════════════════════════
       TelemetryPanel — flash on value change
       ════════════════════════════════════════════════════════ */
    class TelemetryPanel {
        constructor(el) {
            this._el   = el;
            this._prev = {};
        }

        update(tel) {
            if (!tel) return;
            const vals = {
                cpu_load:    tel.cpu_load    != null ? tel.cpu_load.toFixed(2)       : '--',
                mem_free:    tel.mem_free_gb != null ? `${tel.mem_free_gb} GB`       : '--',
                gpu_temp:    tel.gpu_temp    != null ? `${tel.gpu_temp}°C`          : '--',
                net_latency: tel.net_latency != null ? `${tel.net_latency}ms`       : '--',
                inference:   tel.inference   || '--',
                vectors:     tel.vectors     != null ? tel.vectors.toLocaleString() : '--',
            };
            for (const [key, val] of Object.entries(vals)) {
                const row  = this._el.querySelector(`[data-key="${key}"]`);
                if (!row) continue;
                const span = row.querySelector('.telemetry-value');
                if (!span) continue;
                if (val !== this._prev[key]) {
                    span.textContent = val;
                    span.className   = 'telemetry-value';
                    if (key === 'inference') {
                        if (val === 'READY')                          span.classList.add('ready');
                        if (val === 'DEGRADED' || val === 'OFFLINE')  span.classList.add('degraded');
                    }
                    if (!REDUCED_MOTION) {
                        span.classList.add('flash');
                        span.addEventListener('animationend', () => span.classList.remove('flash'), { once: true });
                    }
                    this._prev[key] = val;
                }
                if (key === 'cpu_load' && tel.cpu_load != null) {
                    const bar = row.querySelector('.telemetry-bar-fill');
                    if (bar) bar.style.width = `${Math.min(100, tel.cpu_load * 100)}%`;
                }
            }
        }
    }

    /* ════════════════════════════════════════════════════════
       BootSequence — blur + fade + translateY stagger
       ════════════════════════════════════════════════════════ */
    class BootSequence {
        constructor() { this._items = []; this._played = false; }

        add(el, ms) {
            if (!el) return;
            el.classList.add('boot-elem');
            this._items.push({ el, ms });
        }

        play() {
            if (this._played) return;
            this._played = true;
            if (REDUCED_MOTION) {
                for (const { el } of this._items) el.classList.add('boot-visible');
                return;
            }
            for (const { el, ms } of this._items) {
                setTimeout(() => el.classList.add('boot-visible'), ms);
            }
        }

        reset() {
            this._played = false;
            for (const { el } of this._items) el.classList.remove('boot-visible');
        }
    }

    /* ════════════════════════════════════════════════════════
       LoginController
       ════════════════════════════════════════════════════════ */
    class LoginController {
        constructor() {
            this._status           = new SystemStatus();
            this._bg               = null;
            this._orb              = null;
            this._telPanel         = null;
            this._boot             = new BootSequence();
            this._clockId          = null;
            this._uptimeId         = null;
            this._glitchId         = null;
            this._defaultsRendered = false;
        }

        init() {
            const screen = document.getElementById('login-screen');
            if (!screen) return;

            const bgCanvas = document.getElementById('login-bg-canvas');
            if (bgCanvas) this._bg = new BackgroundCanvas(bgCanvas);

            const orbContainer = document.getElementById('orb-container');
            if (orbContainer) this._orb = new OrbCanvas(orbContainer);

            const telEl = document.getElementById('telemetry-panel');
            if (telEl) this._telPanel = new TelemetryPanel(telEl);

            this._registerBoot();
            this._status.onUpdate(data => this._onData(data));

            const obs = new MutationObserver(() => {
                if (screen.classList.contains('active')) this._start();
                else this._stop();
            });
            obs.observe(screen, { attributes: true, attributeFilter: ['class'] });
            if (screen.classList.contains('active')) this._start();

            document.addEventListener('visibilitychange', () => {
                if (document.hidden) this._bg?.stop();
                else if (screen.classList.contains('active')) this._bg?.start();
            });
        }

        _start() {
            this._defaultsRendered = false;
            this._bg?.start();
            this._orb?.start();
            this._status.connect();
            this._boot.play();
            this._startClock();
            this._startUptimeTick();
            this._scheduleGlitch();
            this._renderDefaults();
            // Push orb center to background after layout
            requestAnimationFrame(() => {
                const orbEl    = document.getElementById('orb-container');
                const bgCanvas = document.getElementById('login-bg-canvas');
                if (orbEl && bgCanvas && this._bg) {
                    const orb  = orbEl.getBoundingClientRect();
                    const canv = bgCanvas.getBoundingClientRect();
                    this._bg.setOrbCenter(
                        orb.left - canv.left + orb.width  / 2,
                        orb.top  - canv.top  + orb.height / 2
                    );
                }
            });
        }

        _stop() {
            this._bg?.stop();
            this._orb?.stop();
            this._status.disconnect();
            clearInterval(this._clockId);
            clearInterval(this._uptimeId);
            clearTimeout(this._glitchId);
        }

        _registerBoot() {
            const schedule = [
                ['login-topbar',          50],
                ['orb-container',        300],
                ['telemetry-panel',      500],
                ['network-panel',        700],
                ['modules-panel',       1100],
                ['routing-panel',       1300],
                ['events-panel',        1700],
                ['security-panel',      1900],
                ['login-title-block',   2200],
                ['warning-panel',       2300],
                ['login-form-container',2600],
                ['login-encrypted',     3000],
                ['login-bottombar',     3200],
            ];
            for (const [id, ms] of schedule) {
                this._boot.add(document.getElementById(id), ms);
            }
        }

        _onData(data) {
            this._telPanel?.update(data.telemetry);
            if (data.modules?.length)  this._renderModules(data.modules);
            if (data.routing?.length)  this._renderRouting(data.routing);
            if (data.events?.length)   this._renderEvents(data.events);
            this._renderSecurity(data.security);
            this._renderWarnings(data);
            this._updateDots(data);
        }

        _renderDefaults() {
            if (this._defaultsRendered) return;
            this._defaultsRendered = true;

            const netNodes = [
                { label: 'primary_node',     depth: 0, online: true  },
                { label: '192.168.1.14',     depth: 1, online: true  },
                { label: 'arch_satellite',   depth: 2, online: true  },
                { label: '192.168.1.22',     depth: 1, online: true  },
                { label: 'inference_gpu',    depth: 2, online: true  },
                { label: 'rtx_5060ti',       depth: 3, online: false },
                { label: 'telegram_bridge',  depth: 1, online: true  },
                { label: 'api.telegram.org', depth: 2, online: true  },
            ];
            this._buildNetDom(netNodes, 700);

            const mods = ['chromadb.core', 'fastapi.gateway', 'telegram.bridge', 'playwright.drv', 'stt.whisper [IDLE]'];
            this._buildModsDom(mods, 1100);

            this._renderRouting([
                { tier: 1, name: 'local.ollama' },
                { tier: 2, name: 'haiku 4.5'    },
                { tier: 3, name: 'sonnet 5'     },
                { tier: 4, name: 'opus 5'       },
            ]);
            this._renderEvents([]);
            this._renderSecurity({ failed_attempts: 0, lockout_in: 5 });
        }

        _buildNetDom(nodes, bootMs) {
            const content = document.querySelector('#network-panel .panel-content');
            if (!content) return;
            content.innerHTML = '';
            const pres = ['', '└ ', '  └ ', '    └ '];
            nodes.forEach((node, i) => {
                const div = document.createElement('div');
                div.className = 'network-node line-hidden';
                const pre = pres[node.depth] ?? '    └ ';
                const cls = node.online ? 'net-dot-on' : 'net-dot-off';
                const sym = node.online ? '●' : '○';
                div.innerHTML = `<span class="net-pre">${pre}</span><span class="${cls}">${sym}</span> ${this._esc(node.label)}`;
                content.appendChild(div);
                setTimeout(() => div.classList.add('line-visible'), bootMs + 80 + i * 80);
            });
        }

        _buildModsDom(mods, bootMs) {
            const content = document.querySelector('#modules-panel .panel-content');
            if (!content) return;
            content.innerHTML = '';
            mods.forEach((mod, i) => {
                const div  = document.createElement('div');
                div.className = 'module-item line-hidden';
                const idle = mod.includes('[IDLE]');
                div.innerHTML  = `<span class="${idle ? 'mod-dot-idle' : 'mod-dot-on'}">◆</span> ${this._esc(mod)}`;
                content.appendChild(div);
                setTimeout(() => div.classList.add('line-visible'), bootMs + 80 + i * 150);
            });
        }

        _renderModules(modules) {
            const content = document.querySelector('#modules-panel .panel-content');
            if (!content) return;
            const cur = [...content.querySelectorAll('.module-item')].map(e => e.textContent.trim()).join('|');
            if (cur === modules.join('|')) return;
            content.innerHTML = '';
            modules.forEach((mod) => {
                const div  = document.createElement('div');
                div.className = 'module-item line-visible';
                const idle = mod.includes('[IDLE]');
                div.innerHTML  = `<span class="${idle ? 'mod-dot-idle' : 'mod-dot-on'}">◆</span> ${this._esc(mod)}`;
                content.appendChild(div);
            });
        }

        _renderRouting(routing) {
            const content = document.querySelector('#routing-panel .panel-content');
            if (!content) return;
            content.innerHTML = '';
            for (const r of routing) {
                const div = document.createElement('div');
                div.className = 'routing-row';
                div.innerHTML = `<span class="routing-tier">tier_${r.tier}</span><span class="routing-name">${this._esc(r.name)}</span>`;
                content.appendChild(div);
            }
        }

        _renderEvents(events) {
            const content = document.querySelector('#events-panel .panel-content');
            if (!content) return;
            if (!events.length) {
                content.innerHTML = '<div class="event-item"><span class="event-time">--:--</span> <span class="event-text">awaiting_connection</span></div>';
                return;
            }
            content.innerHTML = '';
            const now = new Date();
            events.forEach((evt, i) => {
                const d = new Date(now.getTime() - i * 3000);
                const h = String(d.getHours()).padStart(2,'0');
                const m = String(d.getMinutes()).padStart(2,'0');
                const s = String(d.getSeconds()).padStart(2,'0');
                const div = document.createElement('div');
                div.className = 'event-item';
                div.innerHTML = `<span class="event-time">[${h}:${m}:${s}]</span> <span class="event-text">${this._esc(evt)}</span>`;
                content.appendChild(div);
            });
        }

        _renderSecurity(sec) {
            const content = document.querySelector('#security-panel .panel-content');
            if (!content) return;
            const fa = sec?.failed_attempts ?? 0;
            const li = sec?.lockout_in ?? 5;
            content.innerHTML = `
                <div class="security-row"><span class="sec-lbl">failed_attempts</span><span class="sec-val${fa > 0 ? ' warn' : ''}">${fa}</span></div>
                <div class="security-row"><span class="sec-lbl">lockout_in</span><span class="sec-val">${li} tries</span></div>
                <div class="security-row"><span class="sec-lbl">last_ip</span><span class="sec-val">190.233.x.x</span></div>
                <div class="security-row"><span class="sec-lbl">geo</span><span class="sec-val">Lima, PE</span></div>
            `;
        }

        _renderWarnings(data) {
            const el      = document.getElementById('warning-panel');
            const content = el?.querySelector('.panel-content');
            if (!el || !content) return;
            const ws = [];
            if (data.telemetry?.inference === 'DEGRADED') ws.push('core_degraded');
            if (data.telemetry?.inference === 'OFFLINE')  ws.push('core_offline');
            if ((data.security?.failed_attempts ?? 0) > 2) ws.push('auth_alert');
            if ((data.telemetry?.cpu_load ?? 0) > 0.85)    ws.push('high_cpu_load');
            el.style.visibility = ws.length ? '' : 'hidden';
            content.innerHTML   = ws.map(w => `<div class="hud-warn-item">${w}</div>`).join('');
        }

        _updateDots(data) {
            const dots = document.querySelectorAll('.topbar-status-dot');
            if (dots.length < 3) return;
            dots[0].className = 'status-dot green topbar-status-dot';
            const inf = data.telemetry?.inference;
            const c   = inf === 'READY' ? 'green' : inf === 'DEGRADED' ? 'amber' : 'red';
            dots[1].className = `status-dot ${c} topbar-status-dot`;
            dots[2].className = 'status-dot red topbar-status-dot';
        }

        _startClock() {
            clearInterval(this._clockId);
            const el = document.getElementById('login-clock');
            if (!el) return;
            const tick = () => {
                const utc   = Date.now() + new Date().getTimezoneOffset() * 60000;
                const local = new Date(utc - 5 * 3600000);
                const h = String(local.getHours()).padStart(2,'0');
                const m = String(local.getMinutes()).padStart(2,'0');
                const s = String(local.getSeconds()).padStart(2,'0');
                el.textContent = `${h}:${m}:${s} UTC-05`;
            };
            tick();
            this._clockId = setInterval(tick, 1000);
        }

        _startUptimeTick() {
            clearInterval(this._uptimeId);
            this._uptimeId = setInterval(() => {
                const up = this._status.getLocalUptime();
                const el = document.getElementById('login-uptime');
                if (el && up) el.textContent = up;
            }, 1000);
        }

        _scheduleGlitch() {
            clearTimeout(this._glitchId);
            const delay = 3500 + Math.random() * 2000;
            this._glitchId = setTimeout(() => {
                this._doGlitch('normal');
                this._scheduleGlitch();
            }, delay);
        }

        _doGlitch(type = 'normal') {
            if (REDUCED_MOTION) return;
            const title = document.querySelector('.jarvis-title');
            if (!title) return;
            title.classList.remove('glitch-normal', 'glitch-burst');
            void title.offsetWidth;
            title.classList.add(type === 'strong' ? 'glitch-burst' : 'glitch-normal');
            title.addEventListener('animationend', () => title.classList.remove('glitch-normal', 'glitch-burst'), { once: true });
        }

        triggerGlitch()   { this._doGlitch('strong'); }

        triggerAuthFail() {
            const input = document.getElementById('password-input');
            if (input && !REDUCED_MOTION) {
                input.classList.add('shake');
                input.addEventListener('animationend', () => input.classList.remove('shake'), { once: true });
            }
            this._doGlitch('strong');
        }

        _esc(s) {
            const d = document.createElement('div');
            d.textContent = String(s);
            return d.innerHTML;
        }
    }

    /* ─── Init ─────────────────────────────────────────────── */
    const controller = new LoginController();
    document.addEventListener('DOMContentLoaded', () => controller.init());
    window.ZephyroLogin = {
        triggerGlitch:   () => controller.triggerGlitch(),
        triggerAuthFail: () => controller.triggerAuthFail(),
        controller,
    };
})();
