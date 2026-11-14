/**
 * dashboard.js — Zephyro Dashboard HUD Components
 *
 * Particle background + telemetry WS + request log + footer stats
 */
(function () {
    'use strict';

    const REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    /* ═══════════════════════════════════════════════════════════
       ParticleCanvas — 22 breathing dots, no connections
       ═══════════════════════════════════════════════════════════ */
    class ParticleCanvas {
        constructor(canvas) {
            this._c = canvas;
            this._ctx = canvas.getContext('2d');
            this._ps = [];
            this._frame = 0;
            this._id = null;
            this._running = false;
        }

        start() {
            if (this._running || REDUCED) return;
            this._running = true;
            this._resize();
            this._spawn();
            this._loop();
            window.addEventListener('resize', this._onResize);
        }

        stop() {
            this._running = false;
            if (this._id) { cancelAnimationFrame(this._id); this._id = null; }
            window.removeEventListener('resize', this._onResize);
        }

        _onResize = () => { if (this._running) { this._resize(); this._spawn(); } };

        _resize() {
            this._c.width = this._c.offsetWidth || window.innerWidth;
            this._c.height = this._c.offsetHeight || window.innerHeight;
        }

        _spawn() {
            const w = this._c.width, h = this._c.height;
            this._ps = Array.from({ length: 22 }, () => ({
                x: Math.random() * w,
                y: Math.random() * h,
                vx: (Math.random() - 0.5) * 0.08,
                vy: (Math.random() - 0.5) * 0.08,
                r: Math.random() * 1.2 + 0.5,
                phase: Math.random() * Math.PI * 2,
            }));
        }

        _loop = () => {
            if (!this._running) return;
            const ctx = this._ctx;
            const w = this._c.width, h = this._c.height;
            ctx.clearRect(0, 0, w, h);

            this._frame++;
            for (const p of this._ps) {
                p.x += p.vx;
                p.y += p.vy;
                if (p.x < 0 || p.x > w) p.vx *= -1;
                if (p.y < 0 || p.y > h) p.vy *= -1;

                const alpha = 0.3 + 0.7 * Math.abs(Math.sin(this._frame * 0.01 + p.phase));
                ctx.beginPath();
                ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
                ctx.fillStyle = '#2dd67f';
                ctx.globalAlpha = alpha;
                ctx.fill();
            }
            ctx.globalAlpha = 1;
            this._id = requestAnimationFrame(this._loop);
        };
    }

    /* ═══════════════════════════════════════════════════════════
       TelemetryManager — connects to WS /api/system/telemetry
       ═══════════════════════════════════════════════════════════ */
    class TelemetryManager {
        constructor() {
            this._ws = null;
            this._running = false;
            this._reconnectTimer = null;
            this._listeners = [];
        }

        connect() {
            if (this._running) return;
            this._running = true;
            this._open();
        }

        disconnect() {
            this._running = false;
            clearTimeout(this._reconnectTimer);
            if (this._ws) { this._ws.close(); this._ws = null; }
        }

        onUpdate(fn) { this._listeners.push(fn); }

        _open() {
            if (!this._running) return;
            try {
                this._ws = new WebSocket(WS_BASE + '/api/system/telemetry');
                this._ws.onmessage = (ev) => {
                    try {
                        const data = JSON.parse(ev.data);
                        for (const fn of this._listeners) fn(data);
                    } catch (_) {}
                };
                this._ws.onclose = () => this._retry();
                this._ws.onerror = () => { try { this._ws.close(); } catch(_){} };
            } catch (_) {
                this._retry();
            }
        }

        _retry() {
            if (!this._running) return;
            this._reconnectTimer = setTimeout(() => this._open(), 4000);
        }
    }

    /* ═══════════════════════════════════════════════════════════
       DashboardController — wires telemetry, skills, log, footer
       ═══════════════════════════════════════════════════════════ */
    class DashboardController {
        constructor() {
            this._particles = null;
            this._telem = new TelemetryManager();
            this._footerInterval = null;
            this._reqLogItems = [];
            this._maxReqLog = 8;
        }

        init() {
            // Particles
            const canvas = document.getElementById('dash-particles');
            if (canvas) {
                this._particles = new ParticleCanvas(canvas);
                this._particles.start();
            }

            // Telemetry
            this._telem.onUpdate((data) => this._onTelemetry(data));
            this._telem.connect();

            // Footer stats — update every 1.8s
            this._footerInterval = setInterval(() => this._updateFooterStats(), 1800);

            // Resizable sidebars
            this._initResizeHandles();
        }

        destroy() {
            this._particles?.stop();
            this._telem.disconnect();
            clearInterval(this._footerInterval);
            this._cleanupResize?.();
        }

        _initResizeHandles() {
            const layout = document.querySelector('.dash-layout');
            if (!layout) return;

            const handles = layout.querySelectorAll('.dash-resize-handle');

            // Track current sizes — start from CSS defaults or saved values
            let leftW = parseInt(localStorage.getItem('dash-col-left')) || 260;
            let rightW = parseInt(localStorage.getItem('dash-col-right')) || 300;

            const applyColumns = () => {
                layout.style.gridTemplateColumns = `${leftW}px 4px minmax(0,1fr) 4px ${rightW}px`;
            };

            // Apply saved sizes on load
            applyColumns();

            let active = null;
            let startX = 0;
            let startLeft = 0;
            let startRight = 0;

            const onMouseMove = (e) => {
                if (!active) return;
                e.preventDefault();
                const dx = e.clientX - startX;
                const side = active.dataset.resize;

                if (side === 'left') {
                    leftW = Math.max(180, Math.min(450, startLeft + dx));
                    localStorage.setItem('dash-col-left', leftW);
                } else {
                    rightW = Math.max(200, Math.min(500, startRight - dx));
                    localStorage.setItem('dash-col-right', rightW);
                }
                applyColumns();
            };

            const onMouseUp = () => {
                if (active) active.classList.remove('active');
                active = null;
                document.body.style.cursor = '';
                document.body.style.userSelect = '';
            };

            handles.forEach(h => {
                h.addEventListener('mousedown', (e) => {
                    e.preventDefault();
                    active = h;
                    h.classList.add('active');
                    startX = e.clientX;
                    startLeft = leftW;
                    startRight = rightW;
                    document.body.style.cursor = 'col-resize';
                    document.body.style.userSelect = 'none';
                });
            });

            document.addEventListener('mousemove', onMouseMove);
            document.addEventListener('mouseup', onMouseUp);

            this._cleanupResize = () => {
                document.removeEventListener('mousemove', onMouseMove);
                document.removeEventListener('mouseup', onMouseUp);
            };
        }

        addRequestLog(model, latencyMs) {
            const now = new Date();
            const h = String(now.getHours()).padStart(2, '0');
            const m = String(now.getMinutes()).padStart(2, '0');
            const s = String(now.getSeconds()).padStart(2, '0');

            const container = document.getElementById('dash-req-log');
            if (!container) return;

            const div = document.createElement('div');
            div.className = 'req-log-item';
            div.innerHTML = `<span class="req-time">${h}:${m}:${s}</span> · <span class="req-model">[${this._esc(model)}]</span> :: ${latencyMs}ms`;

            if (container.firstChild) {
                container.insertBefore(div, container.firstChild);
            } else {
                container.appendChild(div);
            }

            // Trim
            while (container.children.length > this._maxReqLog) {
                container.removeChild(container.lastChild);
            }
        }

        _onTelemetry(data) {
            const tel = data.telemetry;
            if (!tel) return;

            // CPU
            this._updateBar('cpu', tel.cpu_load != null ? Math.round(tel.cpu_load * 100) : null, 100, '%');
            // RAM — use real total from backend
            const ramTotal = tel.mem_total_gb || 16;
            const ramUsed = tel.mem_free_gb != null ? +(ramTotal - tel.mem_free_gb).toFixed(1) : null;
            this._updateBar('ram', ramUsed, ramTotal, 'GB',
                ` / ${ramTotal} GB`);
            // VRAM
            const vramTotal = tel.vram_total_gb || null;
            this._updateBar('vram', tel.vram_used_gb != null ? +tel.vram_used_gb.toFixed(1) : null,
                vramTotal || 4, 'GB',
                vramTotal ? ` / ${vramTotal} GB` : '');
            // GPU temp
            this._updateBar('gputemp', tel.gpu_temp, 100, '°C');
            // Net up
            const netEl = document.getElementById('telem-val-netup');
            if (netEl) {
                if (tel.net_up_kbps != null) {
                    netEl.textContent = tel.net_up_kbps + ' KB/s';
                } else {
                    netEl.textContent = '--';
                }
            }

            // Skills
            if (data.skills) this._renderSkills(data.skills);

            // Modules — update dots
            if (data.modules) this._renderModuleDots(data.modules);

            // Clock
            const clockEl = document.getElementById('dash-clock');
            if (clockEl) {
                const now = new Date();
                const h = String(now.getHours()).padStart(2, '0');
                const m = String(now.getMinutes()).padStart(2, '0');
                const s = String(now.getSeconds()).padStart(2, '0');
                clockEl.textContent = h + ':' + m + ':' + s;
            }
        }

        _updateBar(id, value, max, unit, suffix) {
            const valEl = document.getElementById('telem-val-' + id);
            const fillEl = document.getElementById('telem-fill-' + id);
            if (!valEl || !fillEl) return;

            if (value == null) {
                valEl.textContent = '--';
                fillEl.style.width = '0%';
                return;
            }

            const pct = Math.min(100, (value / max) * 100);
            const warn = pct > 80;

            valEl.textContent = value + (suffix || (unit ? unit : ''));
            valEl.className = 'telem-value' + (warn ? ' warn' : '');
            fillEl.style.width = pct + '%';
            fillEl.className = 'telem-fill' + (warn ? ' warn' : '');
        }

        _renderSkills(skills) {
            const list = document.getElementById('dash-skills-list');
            const countEl = document.getElementById('dash-skills-count');
            if (!list) return;

            const names = Array.isArray(skills) ? skills : Object.keys(skills);
            if (countEl) countEl.textContent = '[' + names.length + ']';

            // Only re-render if changed
            const cur = list.dataset.hash;
            const hash = names.join(',');
            if (cur === hash) return;
            list.dataset.hash = hash;

            list.innerHTML = '';
            names.forEach((name, i) => {
                const div = document.createElement('div');
                div.className = 'skill-item';
                const delay = -(i * 0.3);
                div.innerHTML = `<span class="skill-dot pulse" style="animation-delay:${delay}s"></span> ${this._esc(name)}`;
                list.appendChild(div);
            });
        }

        _renderModuleDots(modules) {
            // modules is an array of strings like "ws_chat", "rest_voice" etc
            // or an object { name: state }
        }

        _updateFooterStats() {
            // WS stats from wsClient if available
            const client = window.wsClient;
            if (!client) return;

            const txEl = document.getElementById('dash-ws-tx');
            const rxEl = document.getElementById('dash-ws-rx');
            const pingEl = document.getElementById('dash-ping');

            if (txEl) txEl.textContent = '0.0 KB/s';
            if (rxEl) rxEl.textContent = '0.0 KB/s';
            if (pingEl && client._lastLatency != null) {
                pingEl.textContent = client._lastLatency + 'ms';
            }
        }

        _esc(s) {
            const d = document.createElement('span');
            d.textContent = String(s);
            return d.innerHTML;
        }
    }

    // Export
    window.DashboardController = DashboardController;
})();
