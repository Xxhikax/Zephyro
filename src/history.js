// ============================================
// HISTORY.JS - Panel de Historial Simplificado
// ============================================

var historyPanel, historyBtn, closeHistoryBtn, historyList;
var historyData = [];
var currentConversationId = null;

// Abrir panel de historial
window.openHistoryPanel = function() {
    console.log('[History] ✓ Abriendo panel');
    
    if (!historyPanel) historyPanel = document.getElementById('history-panel');
    var overlayEl = document.getElementById('overlay');
    
    if (historyPanel) {
        historyPanel.classList.add('active');
        if (overlayEl) overlayEl.classList.add('active');
        loadHistory();
    }
};

// Cerrar panel
window.closeHistoryPanel = function() {
    if (!historyPanel) historyPanel = document.getElementById('history-panel');
    var overlayEl = document.getElementById('overlay');
    
    if (historyPanel) historyPanel.classList.remove('active');
    if (overlayEl) overlayEl.classList.remove('active');
};

// Cargar historial
async function loadHistory() {
    if (!historyList) historyList = document.getElementById('history-list');
    
    try {
        if (historyList) historyList.innerHTML = '<div class="loading">Cargando...</div>';
        
        const token = window.auth ? window.auth.getToken() : localStorage.getItem('authToken');
        if (!token) {
            if (historyList) historyList.innerHTML = '<div class="error">No autenticado</div>';
            return;
        }
        
        const response = await window.auth.authFetch(`${API_BASE}/api/history/list`);
        
        if (!response.ok) {
            if (response.status === 401 && typeof window.handleUnauthorized === 'function') {
                window.handleUnauthorized();
                return;
            }
            const errData = await response.json().catch(() => ({}));
            if (historyList) historyList.innerHTML = `<div class="error">Error: ${errData.detail || 'Error del servidor'}</div>`;
            return;
        }
        
        const data = await response.json();
        
        if (data.success) {
            historyData = data.conversations;
            renderHistory();
        } else {
            if (historyList) historyList.innerHTML = `<div class="error">Error: ${data.error}</div>`;
        }
    } catch (error) {
        console.error('[History] Error:', error);
        if (historyList) historyList.innerHTML = '<div class="error">Error de conexión</div>';
    }
}

// Renderizar historial simplificado
function renderHistory() {
    if (!historyList) historyList = document.getElementById('history-list');
    
    if (historyData.length === 0) {
        if (historyList) historyList.innerHTML = '<div class="empty">No hay conversaciones aún</div>';
        return;
    }
    
    const html = historyData.map(conv => {
        // Arreglar formato de fecha
        let dateStr = 'Fecha inválida';
        try {
            const date = new Date(conv.created_at);
            if (!isNaN(date.getTime())) {
                dateStr = date.toLocaleDateString('es-ES', { 
                    day: '2-digit',
                    month: 'short',
                    hour: '2-digit',
                    minute: '2-digit'
                });
            }
        } catch (e) {
            console.error('[History] Error parseando fecha:', conv.created_at);
        }
        
        // Usar título o primera parte de mensaje
        const title = conv.title || (conv.messages && conv.messages[0] ? truncate(conv.messages[0].user_message, 60) : 'Sin título');
        const messageCount = conv.message_count || (conv.messages ? conv.messages.length : 1);
        
        return `
            <div class="history-item" onclick="viewConversation(${conv.id})">
                <div class="conv-title">${title}</div>
                <div class="conv-meta">
                    <span class="conv-date">📅 ${dateStr}</span>
                    <span class="conv-count">💬 ${messageCount} mensaje${messageCount > 1 ? 's' : ''}</span>
                </div>
            </div>
        `;
    }).join('');
    
    if (historyList) historyList.innerHTML = html;
}

// Ver conversación completa en modal
window.viewConversation = async function(id) {
    console.log('[History] Viendo conversación:', id);
    
    const conv = historyData.find(c => c.id === id);
    if (!conv) {
        console.error('[History] Conversación no encontrada');
        return;
    }
    
    currentConversationId = id;
    
    const modal = document.getElementById('conversation-modal');
    const modalUser = document.getElementById('modal-user-message');
    const modalAssistant = document.getElementById('modal-assistant-message');
    const modalDate = document.getElementById('modal-date');
    const modalImportance = document.getElementById('modal-importance');
    const modalMemory = document.getElementById('modal-memory');
    
    if (!modal) {
        console.error('[History] Modal no encontrado');
        return;
    }
    
    // Mostrar todos los mensajes de la conversación
    let userContent = '';
    let assistantContent = '';
    
    if (conv.messages && conv.messages.length > 0) {
        conv.messages.forEach((msg, idx) => {
            userContent += `${msg.user_message}\n${idx < conv.messages.length - 1 ? '\n---\n\n' : ''}`;
            assistantContent += `${msg.assistant_response}\n${idx < conv.messages.length - 1 ? '\n---\n\n' : ''}`;
        });
    } else {
        userContent = conv.user_message || 'Sin mensaje';
        assistantContent = conv.assistant_response || 'Sin respuesta';
    }
    
    // Llenar resumen
    const modalDateSummary = document.getElementById('modal-date-summary');
    const modalMessageCount = document.getElementById('modal-message-count');
    const modalImportanceSummary = document.getElementById('modal-importance-summary');
    
    if (modalDateSummary) {
        try {
            const date = new Date(conv.created_at);
            modalDateSummary.textContent = date.toLocaleDateString('es-ES', { 
                day: '2-digit',
                month: 'short',
                hour: '2-digit',
                minute: '2-digit'
            });
        } catch {
            modalDateSummary.textContent = conv.created_at;
        }
    }
    
    if (modalMessageCount) {
        const count = conv.message_count || 1;
        modalMessageCount.textContent = `${count} mensaje${count > 1 ? 's' : ''}`;
    }
    
    if (modalImportanceSummary) {
        const importanceLabels = {
            'trivial': 'Trivial',
            'moderate': 'Moderada',
            'important': 'Importante',
            'critical': 'Crítica'
        };
        modalImportanceSummary.textContent = importanceLabels[conv.importance_level] || conv.importance_level;
    }

    // Llenar datos completos
    if (modalUser) modalUser.textContent = userContent;
    if (modalAssistant) modalAssistant.textContent = assistantContent;
    if (modalDate) {
        try {
            const date = new Date(conv.created_at);
            modalDate.textContent = date.toLocaleString('es-ES');
        } catch {
            modalDate.textContent = conv.created_at;
        }
    }
    if (modalImportance) {
        const importanceLabels = {
            'trivial': 'Trivial',
            'moderate': 'Moderada',
            'important': 'Importante',
            'critical': 'Crítica'
        };
        modalImportance.textContent = importanceLabels[conv.importance_level] || conv.importance_level;
    }
    if (modalMemory) {
        modalMemory.textContent = conv.saved_to_memory ? 'Sí ✓' : 'No';
    }
    
    // Resetear estado colapsado
    const fullConv = document.getElementById('full-conversation');
    const toggleBtn = document.getElementById('toggle-conversation');
    if (fullConv) fullConv.style.display = 'none';
    if (toggleBtn) {
        toggleBtn.textContent = 'Ver conversación completa ▼';
        toggleBtn.classList.remove('expanded');
    }
    
    // Mostrar modal
    modal.style.display = 'flex';
};

// Cargar conversación en el chat
async function loadConversationInChat() {
    if (!currentConversationId) {
        console.error('[History] No hay conversación seleccionada');
        return;
    }
    
    const conv = historyData.find(c => c.id === currentConversationId);
    if (!conv) {
        console.error('[History] Conversación no encontrada');
        return;
    }
    
    console.log('[History] Cargando conversación en chat:', conv);
    
    // Cerrar modal y panel
    closeModal();
    window.closeHistoryPanel();
    
    // Limpiar chat
    const chatContainer = document.getElementById('chat-container');
    if (chatContainer) {
        chatContainer.innerHTML = '';
    }
    
    // Agregar todos los mensajes de la conversación
    if (typeof window.addMessageToChat === 'function') {
        if (conv.messages && conv.messages.length > 0) {
            conv.messages.forEach(msg => {
                window.addMessageToChat('user', msg.user_message);
                window.addMessageToChat('assistant', msg.assistant_response);
            });
        } else {
            window.addMessageToChat('user', conv.user_message);
            window.addMessageToChat('assistant', conv.assistant_response);
        }
        
        if (chatContainer) {
            chatContainer.scrollTop = chatContainer.scrollHeight;
        }
        
        console.log('[History] ✓ Conversación cargada en el chat');
    } else {
        console.error('[History] addMessageToChat no está disponible');
    }
}

// Eliminar conversación
async function deleteConversation() {
    if (!currentConversationId) return;
    
    if (!confirm('¿Estás seguro de que quieres eliminar esta conversación?')) {
        return;
    }
    
    try {
        const response = await window.auth.authFetch(`${API_BASE}/api/history/delete/${currentConversationId}`, {
            method: 'DELETE'
        });
        
        if (!response.ok) {
            if (response.status === 401 && typeof window.handleUnauthorized === 'function') {
                window.handleUnauthorized();
            } else {
                const errData = await response.json().catch(() => ({}));
                alert('Error: ' + (errData.detail || 'Error del servidor'));
            }
            return;
        }
        
        const data = await response.json();
        
        if (data.success) {
            console.log('[History] Conversación eliminada');
            closeModal();
            loadHistory();
        } else {
            alert('Error al eliminar: ' + data.error);
        }
    } catch (error) {
        console.error('[History] Error al eliminar:', error);
        alert('Error de conexión al eliminar');
    }
}

// Nueva conversación
function newConversation() {
    console.log('[History] Iniciando nueva conversación');
    
    const chatContainer = document.getElementById('chat-container');
    if (chatContainer) {
        chatContainer.innerHTML = `
            <div class="welcome-message">
                <div class="jarvis-logo-small">
                    <div class="circle-outer"></div>
                    <div class="circle-middle"></div>
                    <div class="circle-inner"></div>
                </div>
                <h3>Hola, soy Zephyro</h3>
                <p>¿En qué puedo ayudarte hoy?</p>
            </div>
        `;
    }
    
    window.closeHistoryPanel();
    
    const messageInput = document.getElementById('message-input');
    if (messageInput) {
        messageInput.value = '';
        setTimeout(() => messageInput.focus(), 100);
    }
    
    console.log('[History] ✓ Nueva conversación lista');
}

// Limpiar todo el historial
async function clearAllHistory() {
    if (!confirm('⚠️ ¿Estás seguro de que quieres eliminar TODAS las conversaciones del historial?\n\nEsta acción no se puede deshacer.')) {
        return;
    }
    
    try {
        const response = await window.auth.authFetch(`${API_BASE}/api/history/clear`, {
            method: 'DELETE'
        });
        
        if (!response.ok) {
            if (response.status === 401 && typeof window.handleUnauthorized === 'function') {
                window.handleUnauthorized();
            } else {
                const errData = await response.json().catch(() => ({}));
                alert('Error: ' + (errData.detail || 'Error del servidor'));
            }
            return;
        }
        
        const data = await response.json();
        
        if (data.success) {
            console.log('[History] Historial completo eliminado');
            loadHistory();
            alert('✓ Todo el historial ha sido eliminado');
        } else {
            alert('Error al limpiar historial: ' + data.error);
        }
    } catch (error) {
        console.error('[History] Error al limpiar:', error);
        alert('Error de conexión al limpiar el historial');
    }
}

// Cerrar modal
function closeModal() {
    const modal = document.getElementById('conversation-modal');
    if (modal) modal.style.display = 'none';
    
    const details = document.getElementById('extra-details');
    if (details) details.style.display = 'none';
    
    currentConversationId = null;
}

// Toggle conversación completa
function toggleConversation() {
    const fullConv = document.getElementById('full-conversation');
    const btn = document.getElementById('toggle-conversation');
    if (fullConv && btn) {
        if (fullConv.style.display === 'none') {
            fullConv.style.display = 'block';
            btn.textContent = 'Ocultar conversación ▲';
            btn.classList.add('expanded');
        } else {
            fullConv.style.display = 'none';
            btn.textContent = 'Ver conversación completa ▼';
            btn.classList.remove('expanded');
        }
    }
}

// Utilidad
function truncate(text, maxLength) {
    if (!text) return '';
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength) + '...';
}

// Setup
function setupHistory() {
    console.log('[History] Setup iniciado');
    
    historyPanel = document.getElementById('history-panel');
    historyBtn = document.getElementById('history-btn');
    closeHistoryBtn = document.getElementById('close-history');
    historyList = document.getElementById('history-list');
    
    if (closeHistoryBtn) {
        closeHistoryBtn.addEventListener('click', window.closeHistoryPanel);
    }
    
    if (historyBtn) {
        historyBtn.addEventListener('click', function(e) {
            e.preventDefault();
            window.openHistoryPanel();
        });
    }
    
    const newConvBtn = document.getElementById('new-conversation-btn');
    if (newConvBtn) {
        newConvBtn.addEventListener('click', newConversation);
    }
    
    const clearBtn = document.getElementById('clear-history-btn');
    if (clearBtn) {
        clearBtn.addEventListener('click', clearAllHistory);
    }
    
    const closeModalBtn = document.getElementById('close-modal');
    if (closeModalBtn) {
        closeModalBtn.addEventListener('click', closeModal);
    }
    
    const toggleBtn = document.getElementById('toggle-conversation');
    if (toggleBtn) {
        toggleBtn.addEventListener('click', toggleConversation);
    }
    
    const loadBtn = document.getElementById('load-conversation-btn');
    if (loadBtn) {
        loadBtn.addEventListener('click', loadConversationInChat);
    }
    
    const deleteBtn = document.getElementById('delete-conversation-btn');
    if (deleteBtn) {
        deleteBtn.addEventListener('click', deleteConversation);
    }
    
    const overlayEl = document.getElementById('overlay');
    if (overlayEl) {
        overlayEl.addEventListener('click', function() {
            window.closeHistoryPanel();
            closeModal();
        });
    }
    
    const role = window.auth ? window.auth.getRole() : localStorage.getItem('userRole');
    if (role === 'guest' && historyBtn) {
        historyBtn.style.display = 'none';
    } else if (historyBtn) {
        historyBtn.style.display = 'block';
    }
    
    console.log('[History] ✓ Setup completo');
}

// Inicialización
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setupHistory);
} else {
    setupHistory();
}

window.addEventListener('load', function() {
    setTimeout(setupHistory, 100);
});

window.initHistory = setupHistory;
console.log('[History] ✓ Módulo listo');
