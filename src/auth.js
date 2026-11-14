let authToken = null;
let userRole = null;
let userName = null;

function bindAuthUI() {
    const loginBtn = document.getElementById('login-btn');
    const guestBtn = document.getElementById('guest-btn');
    const passwordInput = document.getElementById('password-input');
    const logoutBtn = document.getElementById('logout-btn');

    loginBtn?.addEventListener('click', () => login(passwordInput?.value || ''));
    guestBtn?.addEventListener('click', () => login(''));
    passwordInput?.addEventListener('keypress', (event) => {
        if (event.key === 'Enter') {
            login(passwordInput.value || '');
        }
    });
    logoutBtn?.addEventListener('click', logout);
}

function setLoginMessage(text, tone = 'dim') {
    const loginMessage = document.getElementById('login-message');
    if (!loginMessage) return;

    loginMessage.textContent = text;
    loginMessage.classList.remove('tone-dim', 'tone-ok', 'tone-error');
    loginMessage.classList.add(`tone-${tone}`);
}

function openOpsScreen() {
    const loginScreen = document.getElementById('login-screen');
    const appScreen = document.getElementById('app-screen') || document.getElementById('chat-screen');
    const userBadge = document.getElementById('system-user');

    loginScreen?.classList.remove('active');
    appScreen?.classList.add('active');
    if (userBadge) {
        userBadge.textContent = `${userName || 'INVITADO'} / ${String(userRole || 'guest').toUpperCase()}`;
    }

    if (window.ZephyroTerminalApp?.start) {
        window.ZephyroTerminalApp.start();
    }

    const adminSec = document.getElementById('admin-tools-section');
    if (adminSec) adminSec.style.display = userRole === 'admin' ? '' : 'none';

    window.dispatchEvent(new Event('zephyro:auth-changed'));
}

function openLoginScreen(expired = false) {
    const loginScreen = document.getElementById('login-screen');
    const appScreen = document.getElementById('app-screen') || document.getElementById('chat-screen');
    const passwordInput = document.getElementById('password-input');

    appScreen?.classList.remove('active');
    loginScreen?.classList.add('active');
    if (passwordInput) passwordInput.value = '';

    if (window.ZephyroTerminalApp?.shutdown) {
        window.ZephyroTerminalApp.shutdown();
    }

    if (expired) {
        setLoginMessage('Sesión expirada. Reautentica.', 'error');
    } else {
        setLoginMessage('', 'dim');
    }
}

async function login(password, _retries = 3) {
    setLoginMessage('Autenticando...', 'dim');

    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 5000);

        const response = await fetch(`${API_BASE}/api/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: password || null }),
            signal: controller.signal,
        });
        clearTimeout(timeoutId);

        const data = await response.json();
        if (!data?.success || !data?.token) {
            setLoginMessage('Error de autenticación.', 'error');
            if (window.ZephyroLogin?.triggerAuthFail) window.ZephyroLogin.triggerAuthFail();
            return;
        }

        // Wrong password → guest fallback: trigger glitch
        if (response.status === 401 || (password && data.role === 'guest')) {
            if (window.ZephyroLogin?.triggerAuthFail) window.ZephyroLogin.triggerAuthFail();
        }

        authToken = data.token;
        userRole = data.role;
        userName = data.name || null;

        localStorage.setItem('authToken', authToken);
        localStorage.setItem('userRole', userRole || 'guest');
        if (userName) localStorage.setItem('userName', userName);

        setLoginMessage(data.message || 'Acceso concedido.', 'ok');
        setTimeout(openOpsScreen, 380);
        window.dispatchEvent(new Event('zephyro:auth-changed'));
    } catch (error) {
        console.error('[Auth] login error:', error);
        if (_retries > 0) {
            setLoginMessage(`Backend iniciando... reintentando (${_retries})`, 'dim');
            setTimeout(() => login(password, _retries - 1), 2500);
        } else {
            setLoginMessage('No se pudo conectar al backend.', 'error');
        }
    }
}

async function logout() {
    try {
        if (authToken) {
            await fetch(`${API_BASE}/api/auth/logout?token=${authToken}`, { method: 'POST' });
        }
    } catch (error) {
        console.error('[Auth] logout error:', error);
    } finally {
        authToken = null;
        userRole = null;
        userName = null;
        localStorage.clear();
        openLoginScreen(false);
        window.dispatchEvent(new Event('zephyro:auth-changed'));
    }
}

window.handleUnauthorized = function handleUnauthorized() {
    authToken = null;
    userRole = null;
    userName = null;
    localStorage.clear();
    openLoginScreen(true);
    // Trigger glitch burst on the title
    if (window.ZephyroLogin?.triggerAuthFail) {
        setTimeout(() => window.ZephyroLogin.triggerAuthFail(), 400);
    }
};

async function restoreSession() {
    const savedToken = localStorage.getItem('authToken');
    // On every page load/restart → session expired
    localStorage.clear();
    if (savedToken) {
        openLoginScreen(true);
    }

    // Health-check del backend al arrancar (sin bloquear el login)
    try {
        const r = await fetch(`${API_BASE}/`, { method: 'GET', signal: AbortSignal.timeout(3000) });
        if (r.ok || r.status < 500) {
            // Backend listo, no mostrar nada
        }
    } catch (_) {
        setLoginMessage('Backend iniciando, espera un momento...', 'dim');
    }
}

window.auth = {
    getToken: () => authToken,
    getRole: () => userRole,
    getName: () => userName,
    logout,
    setName: (name) => {
        userName = name;
        localStorage.setItem('userName', name || '');
    },
    // Auth transport helpers (Phase 2): tokens travel as headers, not ?token=
    authHeaders: () => {
        const t = authToken;
        return t ? { 'Authorization': `Bearer ${t}`, 'X-Token': t } : {};
    },
    authFetch: (url, opts = {}) => {
        const t = authToken;
        if (!t) return fetch(url, opts);
        const headers = { ...(opts.headers || {}), 'Authorization': `Bearer ${t}`, 'X-Token': t };
        return fetch(url, { ...opts, headers });
    },
};

document.addEventListener('DOMContentLoaded', () => {
    bindAuthUI();
    restoreSession();
});
