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

async function login(password) {
    setLoginMessage('Autenticando...', 'dim');

    try {
        const response = await fetch(`${API_BASE}/api/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: password || null })
        });

        const data = await response.json();
        if (!data?.success || !data?.token) {
            setLoginMessage('Error de autenticación.', 'error');
            return;
        }

        authToken = data.token;
        userRole = data.role;
        userName = data.name || null;

        localStorage.setItem('authToken', authToken);
        localStorage.setItem('userRole', userRole || 'guest');
        if (userName) localStorage.setItem('userName', userName);

        setLoginMessage(data.message || 'Acceso concedido.', 'ok');
        setTimeout(openOpsScreen, 380);
    } catch (error) {
        console.error('[Auth] login error:', error);
        setLoginMessage('No se pudo conectar al backend.', 'error');
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
    }
}

window.handleUnauthorized = function handleUnauthorized() {
    authToken = null;
    userRole = null;
    userName = null;
    localStorage.clear();
    openLoginScreen(true);
};

async function restoreSession() {
    const savedToken = localStorage.getItem('authToken');
    const savedRole = localStorage.getItem('userRole');
    const savedName = localStorage.getItem('userName');

    if (!savedToken) return;

    try {
        const response = await fetch(`${API_BASE}/api/status`, {
            headers: { 'Authorization': `Bearer ${savedToken}`, 'X-Token': savedToken }
        });
        if (!response.ok) {
            localStorage.clear();
            return;
        }

        authToken = savedToken;
        userRole = savedRole || 'guest';
        userName = savedName || null;
        openOpsScreen();
    } catch (error) {
        console.error('[Auth] session restore error:', error);
        localStorage.clear();
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
