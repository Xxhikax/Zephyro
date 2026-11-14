// compact-mode.js — jarvis-overlay toggle
// Atajos:
//   Ctrl+K          → toggle in-window (siempre disponible)
//   Alt+Space       → toggle global (requiere tauri-plugin-global-shortcut)
// Estado persistido en localStorage.

(function () {
    const STORAGE_KEY = 'zephyro.compactMode';

    function isCompact() {
        return document.body.classList.contains('compact-mode');
    }

    function setCompact(enabled) {
        const wasCompact = isCompact();
        document.body.classList.toggle('compact-mode', enabled);
        try {
            localStorage.setItem(STORAGE_KEY, enabled ? '1' : '0');
        } catch (e) { /* ignore */ }

        if (enabled !== wasCompact) {
            document.dispatchEvent(new CustomEvent('zephyro:compact-mode-change', {
                detail: { enabled }
            }));
        }
    }

    function toggleCompact() {
        setCompact(!isCompact());
    }

    // Indicador visual
    function ensureIndicator() {
        if (document.getElementById('compact-mode-indicator')) return;
        const el = document.createElement('div');
        el.id = 'compact-mode-indicator';
        el.textContent = '◇ COMPACT  [Ctrl+K]';
        document.body.appendChild(el);
    }

    // Restaurar estado al cargar (solo si el usuario lo dejó activo)
    function restoreState() {
        try {
            if (localStorage.getItem(STORAGE_KEY) === '1') {
                setCompact(true);
            }
        } catch (e) { /* ignore */ }
    }

    // Keybinding in-window
    function bindKeys() {
        document.addEventListener('keydown', (e) => {
            // Ctrl+K — toggle compact
            if (e.ctrlKey && !e.shiftKey && !e.altKey &&
                (e.key === 'k' || e.key === 'K')) {
                e.preventDefault();
                toggleCompact();
            }
            // Esc en compact mode → salir
            if (e.key === 'Escape' && isCompact()) {
                // No interferir si hay un modal abierto
                const hasModal = document.querySelector('.history-overlay.active, .conv-detail-modal.active');
                if (!hasModal) {
                    setCompact(false);
                }
            }
        });
    }

    // Listener para señales del backend Rust (global shortcut Alt+Space)
    function bindTauriEvent() {
        if (!window.__TAURI__) return;
        try {
            const listen = window.__TAURI__.event?.listen;
            if (typeof listen === 'function') {
                listen('compact-mode-toggle', () => toggleCompact());
            }
        } catch (e) { /* plugin no disponible */ }
    }

    // API pública
    window.ZephyroCompact = {
        toggle: toggleCompact,
        enable: () => setCompact(true),
        disable: () => setCompact(false),
        isActive: isCompact,
    };

    // Init on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            ensureIndicator();
            restoreState();
            bindKeys();
            bindTauriEvent();
        });
    } else {
        ensureIndicator();
        restoreState();
        bindKeys();
        bindTauriEvent();
    }
})();
