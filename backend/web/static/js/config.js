const API_BASE = '';
const WS_BASE = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`;
const APP_VERSION = 'v1.0';

window.API_BASE = API_BASE;
window.WS_BASE = WS_BASE;
window.APP_VERSION = APP_VERSION;
