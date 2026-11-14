// Matrix rain — throttled to ~18-20fps. Pauses on visibility change / blur.
(function () {
    const TARGET_FPS = 18;
    const FRAME_MS = 1000 / TARGET_FPS;
    const CHARS = '01{}[]();:=><+-*/\\|&!?#@$%^~αβγδλπ0xFFabcdef';

    class CallMatrix {
        constructor(canvas) {
            this.canvas = canvas;
            this.ctx = canvas.getContext('2d');
            this.running = false;
            this.lastFrame = 0;
            this.drops = [];
            this.fontSize = 14;
            this._resize = () => this._onResize();
            this._visChange = () => this._onVisibility();
        }

        _onResize() {
            const canvas = this.canvas;
            canvas.width = window.innerWidth;
            canvas.height = window.innerHeight;
            const cols = Math.floor(canvas.width / this.fontSize);
            // Preserve drops length as much as possible
            if (this.drops.length < cols) {
                while (this.drops.length < cols) this.drops.push(Math.random() * 40);
            } else if (this.drops.length > cols) {
                this.drops.length = cols;
            }
        }

        _onVisibility() {
            if (document.hidden && this.running) {
                this._pause();
            } else if (!document.hidden && this.running && !this.rafId) {
                this._loop();
            }
        }

        _pause() {
            if (this.rafId) {
                cancelAnimationFrame(this.rafId);
                this.rafId = null;
            }
        }

        start() {
            if (this.running) return;
            this.running = true;
            this._onResize();
            window.addEventListener('resize', this._resize);
            document.addEventListener('visibilitychange', this._visChange);
            this._loop();
        }

        stop() {
            this.running = false;
            this._pause();
            window.removeEventListener('resize', this._resize);
            document.removeEventListener('visibilitychange', this._visChange);
            const ctx = this.ctx;
            if (ctx && this.canvas) ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        }

        _loop() {
            if (!this.running) return;
            this.rafId = requestAnimationFrame((t) => {
                if (t - this.lastFrame >= FRAME_MS) {
                    this.lastFrame = t;
                    this._draw();
                }
                this._loop();
            });
        }

        _draw() {
            const ctx = this.ctx;
            const canvas = this.canvas;
            // Fade — slower fade = longer trails, but we keep opacity low overall
            ctx.fillStyle = 'rgba(3, 6, 12, 0.10)';
            ctx.fillRect(0, 0, canvas.width, canvas.height);
            ctx.font = `${this.fontSize}px "JetBrains Mono", "Fira Code", monospace`;

            for (let i = 0; i < this.drops.length; i++) {
                const ch = CHARS[(Math.random() * CHARS.length) | 0];
                const x = i * this.fontSize;
                const y = this.drops[i] * this.fontSize;
                // 18% opacity cap for readability — bright head, dim tail
                const headAlpha = 0.18 + Math.random() * 0.08;
                ctx.fillStyle = `rgba(0, 255, 136, ${headAlpha})`;
                ctx.fillText(ch, x, y);

                if (y > canvas.height && Math.random() > 0.975) {
                    this.drops[i] = 0;
                }
                this.drops[i]++;
            }
        }
    }

    window.CallMatrix = CallMatrix;
})();
