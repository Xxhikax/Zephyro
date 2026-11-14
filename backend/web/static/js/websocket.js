const WS_STATES = {
    CONNECTED: 'CONNECTED',
    RECONNECTING: 'RECONNECTING',
    DISCONNECTED: 'DISCONNECTED',
    ERROR: 'ERROR'
};

const wsClient = {
    socket: null,
    state: WS_STATES.DISCONNECTED,
    reconnectAttempts: 0,
    maxReconnectAttempts: 20,
    reconnectDelayMs: 1400,
    intentionalClose: false,
    token: null,
    messageListeners: [],
    stateListeners: [],

    setToken(token) {
        this.token = token;
    },

    onMessage(listener) {
        if (typeof listener === 'function') this.messageListeners.push(listener);
    },

    onStateChange(listener) {
        if (typeof listener === 'function') this.stateListeners.push(listener);
    },

    setState(nextState, meta = {}) {
        this.state = nextState;
        this.stateListeners.forEach((listener) => listener(nextState, meta));
    },

    connect() {
        this.intentionalClose = false;
        this.setState(this.reconnectAttempts > 0 ? WS_STATES.RECONNECTING : WS_STATES.DISCONNECTED, {
            attempts: this.reconnectAttempts
        });

        try {
            this.socket = new WebSocket(`${WS_BASE}/api/ws`);
        } catch (error) {
            this.setState(WS_STATES.ERROR, { error });
            this.scheduleReconnect();
            return;
        }

        this.socket.onopen = () => {
            this.reconnectAttempts = 0;
            this.setState(WS_STATES.CONNECTED);
        };

        this.socket.onmessage = (event) => {
            let payload;
            try {
                payload = JSON.parse(event.data);
            } catch (error) {
                this.setState(WS_STATES.ERROR, { error: new Error('Payload WebSocket inválido') });
                return;
            }
            this.messageListeners.forEach((listener) => listener(payload));
        };

        this.socket.onerror = (error) => {
            this.setState(WS_STATES.ERROR, { error });
        };

        this.socket.onclose = () => {
            this.setState(WS_STATES.DISCONNECTED);
            if (!this.intentionalClose) this.scheduleReconnect();
        };
    },

    scheduleReconnect() {
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            this.setState(WS_STATES.ERROR, { fatal: true });
            return;
        }

        this.reconnectAttempts += 1;
        this.setState(WS_STATES.RECONNECTING, { attempts: this.reconnectAttempts });
        setTimeout(() => this.connect(), this.reconnectDelayMs);
    },

    restart() {
        this.reconnectAttempts = 0;
        this.disconnect();
        this.connect();
    },

    disconnect() {
        this.intentionalClose = true;
        if (this.socket) {
            this.socket.close();
        }
        this.socket = null;
        this.setState(WS_STATES.DISCONNECTED);
    },

    isConnected() {
        return this.socket && this.socket.readyState === WebSocket.OPEN;
    },

    sendChat(message, mode = 'auto') {
        if (!this.isConnected()) return false;
        this.socket.send(JSON.stringify({
            token: this.token || window.auth?.getToken?.(),
            message,
            mode
        }));
        return true;
    }
};

window.addEventListener('beforeunload', () => {
    wsClient.disconnect();
});

window.WS_STATES = WS_STATES;
window.wsClient = wsClient;
