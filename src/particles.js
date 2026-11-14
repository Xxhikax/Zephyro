/**
 * particles.js — Red de partículas animada para el login de Zephyro
 * Canvas 2D puro, sin dependencias.
 */
(function initLoginParticles() {
    const canvas = document.getElementById('particle-bg');
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    const COLOR  = '#00FF88';
    const MAX_DIST = 110;   // px — distancia máxima para trazar línea
    const NUM      = 48;    // número de partículas

    let particles = [];
    let animId    = null;
    let running   = false;

    /* ─── Tamaño del canvas ──────────────────────────── */
    function resize() {
        canvas.width  = canvas.offsetWidth  || window.innerWidth;
        canvas.height = canvas.offsetHeight || window.innerHeight;
    }

    /* ─── Crear partículas ───────────────────────────── */
    function spawnParticles() {
        particles = [];
        for (let i = 0; i < NUM; i++) {
            particles.push({
                x:  Math.random() * canvas.width,
                y:  Math.random() * canvas.height,
                vx: (Math.random() - 0.5) * 0.35,
                vy: (Math.random() - 0.5) * 0.35,
                r:  Math.random() * 1.2 + 0.4,
            });
        }
    }

    /* ─── Dibujar frame ──────────────────────────────── */
    function draw() {
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        // Líneas de conexión
        ctx.lineWidth = 0.5;
        for (let i = 0; i < particles.length; i++) {
            for (let j = i + 1; j < particles.length; j++) {
                const dx   = particles[i].x - particles[j].x;
                const dy   = particles[i].y - particles[j].y;
                const dist = Math.sqrt(dx * dx + dy * dy);
                if (dist < MAX_DIST) {
                    ctx.beginPath();
                    ctx.moveTo(particles[i].x, particles[i].y);
                    ctx.lineTo(particles[j].x, particles[j].y);
                    ctx.strokeStyle = COLOR;
                    ctx.globalAlpha = (1 - dist / MAX_DIST) * 0.14;
                    ctx.stroke();
                }
            }
        }

        // Puntos
        for (const p of particles) {
            ctx.beginPath();
            ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
            ctx.fillStyle = COLOR;
            ctx.globalAlpha = 0.45;
            ctx.fill();
        }

        ctx.globalAlpha = 1;
    }

    /* ─── Mover partículas ───────────────────────────── */
    function update() {
        for (const p of particles) {
            p.x += p.vx;
            p.y += p.vy;
            // Rebotar en bordes
            if (p.x < 0 || p.x > canvas.width)  p.vx *= -1;
            if (p.y < 0 || p.y > canvas.height)  p.vy *= -1;
        }
    }

    /* ─── Loop principal ─────────────────────────────── */
    function loop() {
        update();
        draw();
        animId = requestAnimationFrame(loop);
    }

    function start() {
        if (running) return;
        running = true;
        resize();
        spawnParticles();
        loop();
    }

    function stop() {
        running = false;
        if (animId) { cancelAnimationFrame(animId); animId = null; }
    }

    /* ─── Eventos ────────────────────────────────────── */
    window.addEventListener('resize', () => {
        if (!running) return;
        resize();
        spawnParticles();
    });

    // Pausar si la pestaña está oculta
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) stop();
        else if (document.getElementById('login-screen')?.classList.contains('active')) start();
    });

    /* ─── Observar visibilidad del login screen ──────── */
    const loginScreen = document.getElementById('login-screen');
    if (!loginScreen) return;

    const observer = new MutationObserver(() => {
        if (loginScreen.classList.contains('active')) {
            start();
        } else {
            stop();
        }
    });
    observer.observe(loginScreen, { attributes: true, attributeFilter: ['class'] });

    // Arrancar si ya está activo al cargar
    if (loginScreen.classList.contains('active')) start();
})();
