// ============================================
// SETTINGS.JS - Panel de Configuración
// ============================================

const settingsBtn = document.getElementById('settings-btn');
const settingsPanel = document.getElementById('settings-panel');
const closeSettingsBtn = document.getElementById('close-settings');
const overlay = document.getElementById('overlay');
const menuBtn = document.getElementById('menu-btn');
const identifyBtn = document.getElementById('identify-btn');
const providerSelector = document.getElementById('provider-selector');
const providerSection = document.getElementById('provider-section');

// Abrir panel de configuración
settingsBtn.addEventListener('click', () => {
    settingsPanel.classList.add('active');
    overlay.classList.add('active');
    updateSettingsPanel();
    updateStatus();
});

// Abrir desde menú móvil
if (menuBtn) {
    menuBtn.addEventListener('click', () => {
        settingsPanel.classList.add('active');
        overlay.classList.add('active');
        updateSettingsPanel();
        updateStatus();
    });
}

// Actualizar información del panel de configuración
function updateSettingsPanel() {
    const name = window.auth.getName();
    const role = window.auth.getRole();
    const token = window.auth.getToken();
    
    // Actualizar nombre
    const nameEl = document.getElementById('settings-user-name');
    if (nameEl) {
        nameEl.textContent = name || '-';
    }
    
    // Actualizar rol
    const roleEl = document.getElementById('settings-user-role');
    if (roleEl) {
        const roleText = role === 'admin' ? 'Administrador' : 
                        role === 'guest' ? 'Invitado' : 
                        role || '-';
        roleEl.textContent = roleText;
    }
    
    // Actualizar estado de sesión
    const sessionEl = document.getElementById('settings-session-status');
    if (sessionEl) {
        sessionEl.textContent = token ? 'Activa' : 'Inactiva';
    }
}

// Exportar función globalmente
window.updateSettingsPanel = updateSettingsPanel;

// Cerrar panel
closeSettingsBtn.addEventListener('click', closeSettings);
overlay.addEventListener('click', closeSettings);

function closeSettings() {
    settingsPanel.classList.remove('active');
    overlay.classList.remove('active');
}

// Modo IA (Radio buttons)
const modeRadios = document.querySelectorAll('input[name="mode"]');
modeRadios.forEach(radio => {
    radio.addEventListener('change', (e) => {
        const mode = e.target.value;
        const role = window.auth.getRole();
        
        // Verificar si invitado intenta usar cloud
        if (role === 'guest' && mode === 'cloud') {
            alert('Modo cloud no disponible para invitados');
            document.querySelector('input[name="mode"][value="auto"]').checked = true;
            return;
        }
        
        // Actualizar selector en header
        const modeSelector = document.getElementById('mode-selector');
        modeSelector.value = mode;
        
        // Actualizar variable global
        window.currentMode = mode;
    });
});

// Sincronizar con selector del header
const modeSelector = document.getElementById('mode-selector');
modeSelector.addEventListener('change', (e) => {
    const mode = e.target.value;
    const radio = document.querySelector(`input[name="mode"][value="${mode}"]`);
    if (radio) {
        radio.checked = true;
    }
});

// Identificarse
if (identifyBtn) {
    identifyBtn.addEventListener('click', async () => {
        const role = window.auth.getRole();
        
        if (role === 'admin') {
            alert('Ya estás identificado como Bruno (Administrador)');
            return;
        }
        
        // Invitado: preguntar nombre
        const name = prompt('¿Cuál es tu nombre?');
        if (!name) return;
        
        try {
            const response = await window.auth.authFetch(`${API_BASE}/api/auth/identify?name=${encodeURIComponent(name)}`, {
                method: 'POST'
            });
            
            if (!response.ok) {
                if (response.status === 401 && typeof window.handleUnauthorized === 'function') {
                    window.handleUnauthorized();
                } else {
                    alert('Error del servidor');
                }
                return;
            }
            
            const data = await response.json();
            
            if (data.success) {
                window.auth.setName(name);
                alert(data.message);
                
                // Actualizar UI
                document.getElementById('user-name').textContent = name;
                updateSettingsPanel();
            }
        } catch (error) {
            console.error('Error al identificarse:', error);
            alert('Error al identificarse');
        }
    });
}

// Cambiar proveedor cloud (Solo admin)
if (providerSelector) {
    // Ocultar si es invitado
    if (window.auth.getRole() === 'guest') {
        providerSection.style.display = 'none';
    }

    // Mostrar/ocultar sección de modelo Anthropic
    function updateAnthropicModelSection(provider) {
        const section = document.getElementById('anthropic-model-section');
        if (section) {
            section.style.display = (provider === 'anthropic') ? 'block' : 'none';
        }
    }

    updateAnthropicModelSection(providerSelector.value);

    providerSelector.addEventListener('change', async (e) => {
        const provider = e.target.value;
        updateAnthropicModelSection(provider);
        
        try {
            const response = await window.auth.authFetch(`${API_BASE}/api/settings/provider?provider=${provider}`, {
                method: 'POST'
            });
            
            if (!response.ok) {
                if (response.status === 401 && typeof window.handleUnauthorized === 'function') {
                    window.handleUnauthorized();
                } else {
                    alert('Error del servidor');
                }
                return;
            }
            
            const data = await response.json();
            
            if (data.success) {
                alert(`Proveedor cambiado a ${provider}`);
                updateStatus();
            } else {
                alert('Error al cambiar proveedor: ' + data.message);
            }
        } catch (error) {
            console.error('Error al cambiar proveedor:', error);
            alert('Error de conexión');
        }
    });
}

// ── Selector de modelo Anthropic ────────────────────────────────────────
const anthropicModelBtns = document.querySelectorAll('.anthropic-model-btn');
const anthropicModelActive = document.getElementById('anthropic-model-active');

anthropicModelBtns.forEach(btn => {
    btn.addEventListener('click', async () => {
        const model = btn.dataset.model;

        try {
            const response = await window.auth.authFetch(
                `${API_BASE}/api/settings/anthropic-model?model=${encodeURIComponent(model)}`,
                { method: 'POST' }
            );
            const data = await response.json();

            if (data.success) {
                anthropicModelBtns.forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                if (anthropicModelActive) anthropicModelActive.textContent = model;
            } else {
                alert('Error al cambiar modelo: ' + (data.message || data.detail));
            }
        } catch (error) {
            console.error('Error al cambiar modelo Anthropic:', error);
            alert('Error de conexión');
        }
    });
});

// Deshabilitar modo cloud para invitados
if (window.auth.getRole() === 'guest') {
    const cloudModeRadio = document.getElementById('cloud-mode-radio');
    if (cloudModeRadio) {
        cloudModeRadio.disabled = true;
        const label = cloudModeRadio.closest('label');
        if (label) {
            label.style.opacity = '0.5';
            label.style.cursor = 'not-allowed';
        }
    }
}

// Actualizar estado cada 30 segundos
setInterval(() => {
    if (settingsPanel.classList.contains('active')) {
        updateStatus();
        updateSettingsPanel();
    }
}, 30000);

// Inicializar panel al cargar
document.addEventListener('DOMContentLoaded', () => {
    updateSettingsPanel();
});
