const WS_STATES = {
    CONNECTED: 'CONNECTED',
    RECONNECTING: 'RECONNECTING',
    DISCONNECTED: 'DISCONNECTED',
    ERROR: 'ERROR'
};

const wsClient = {
    socket: null,
    state: WS_STATES.DISCONNECTED,
    sendQueue: [],
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
        // Mark as connecting while opening socket
        this.setState(WS_STATES.RECONNECTING, {
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
            // Drain send queue
            try {
                while (this.sendQueue && this.sendQueue.length) {
                    const payload = this.sendQueue.shift();
                    this.socket.send(JSON.stringify(payload));
                }
            } catch (e) {
                console.error('[WS] Error draining sendQueue', e);
            }
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

        // UX-02: Exponential backoff con jitter (evita thundering-herd)
        // delay = min(base * 2^attempt, 30s) + jitter(0..1s)
        const baseMs = 800;
        const expDelay = Math.min(baseMs * Math.pow(2, this.reconnectAttempts - 1), 30_000);
        const jitter = Math.random() * 1000;
        const delayMs = Math.round(expDelay + jitter);

        setTimeout(() => this.connect(), delayMs);
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

    sendChat(message, mode = 'auto', opts = {}) {
        const requestId = (opts && opts.requestId)
            || (crypto?.randomUUID ? crypto.randomUUID() : `req-${Date.now()}-${Math.random().toString(36).slice(2)}`);
        const payload = {
            token: this.token || window.auth?.getToken?.(),
            message,
            mode,
            request_id: requestId
        };
        if (opts && opts.speak) payload.speak = true;
        if (opts && opts.tts) payload.tts = true;
        if (opts && opts.stream_tts) payload.stream_tts = true;
        if (opts && opts.reasoning) payload.reasoning = true;
        if (opts && opts.vision_context) payload.vision_context = opts.vision_context;
        if (!this.isConnected()) {
            this.sendQueue.push(payload);
            return requestId;
        }
        this.socket.send(JSON.stringify(payload));
        return requestId;
    },

    sendCancel(requestId) {
        if (!requestId) return false;
        const payload = { type: 'cancel', request_id: requestId };
        if (!this.isConnected()) return false;
        this.socket.send(JSON.stringify(payload));
        return true;
    }
};

window.addEventListener('beforeunload', () => {
    wsClient.disconnect();
});

window.WS_STATES = WS_STATES;
window.wsClient = wsClient;
