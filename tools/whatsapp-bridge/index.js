/**
 * JARVIS WhatsApp Bridge — whatsapp-web.js
 *
 * Comunicación con Python via stdin/stdout JSON lines.
 *
 * Protocolo:
 *   stdout → Python:
 *     {"type": "qr", "qr": "..."}
 *     {"type": "connected", "name": "..."}
 *     {"type": "message", "from": "5491100000000", "text": "hola", "name": "Bruno", "jid": "5491100000000@c.us"}
 *     {"type": "error", "error": "..."}
 *
 *   stdin ← Python:
 *     {"type": "send", "to": "5491100000000@c.us", "text": "respuesta"}
 *
 * Regla fromMe:
 *   - fromMe + from === to  → "Mensajes para mí mismo" → JARVIS responde
 *   - fromMe + from !== to  → mensaje enviado a otro → ignorar
 */

const { Client, LocalAuth, MessageTypes } = require('whatsapp-web.js');
const qrcode = require('qrcode-terminal');
const path = require('path');

const AUTH_DIR = path.join(__dirname, 'auth_state');

function emit(obj) {
  process.stdout.write(JSON.stringify(obj) + '\n');
}

// IDs de mensajes enviados por el bot — ignorarlos en message_create
const sentByBot = new Set();

const client = new Client({
  authStrategy: new LocalAuth({ dataPath: AUTH_DIR }),
  puppeteer: {
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
    ],
  },
});

client.on('qr', (qr) => {
  emit({ type: 'qr', qr });
  qrcode.generate(qr, { small: true });
});

client.on('ready', () => {
  const info = client.info;
  const name = info?.pushname || info?.wid?.user || 'unknown';
  emit({ type: 'connected', name });
});

client.on('auth_failure', (msg) => {
  emit({ type: 'error', error: `Auth failure: ${msg}` });
});

client.on('disconnected', (reason) => {
  emit({ type: 'error', error: `Disconnected: ${reason}` });
  process.exit(1);
});

// message_create captura TODOS los mensajes (incluyendo fromMe)
// message solo captura los de otros — necesitamos message_create para self-chat
client.on('message_create', async (msg) => {
  // Ignorar mensajes que el propio bot envió (evita loop en self-chat)
  const msgId = msg.id?._serialized;
  if (msgId && sentByBot.has(msgId)) {
    sentByBot.delete(msgId);
    return;
  }

  // Solo texto plano
  if (msg.type !== MessageTypes.TEXT && msg.type !== 'chat') return;
  const text = msg.body || '';
  if (!text) return;

  const fromJid = msg.from;  // "5491100000000@c.us" o "group-id@g.us"
  const toJid   = msg.to;    // número propio@c.us

  if (msg.fromMe) {
    // fromMe + from === to → "Mensajes para mí mismo" → JARVIS responde
    if (fromJid !== toJid) return;  // enviado a otro → ignorar
  }

  const phone = fromJid.split('@')[0];
  const contact = await msg.getContact().catch(() => null);
  const name = contact?.pushname || contact?.name || '';

  emit({ type: 'message', from: phone, text, name, jid: fromJid });
});

// Leer stdin: mensajes salientes desde Python
process.stdin.setEncoding('utf8');
let stdinBuf = '';
process.stdin.on('data', async (chunk) => {
  stdinBuf += chunk;
  const lines = stdinBuf.split('\n');
  stdinBuf = lines.pop();  // último fragmento incompleto
  for (const line of lines) {
    if (!line.trim()) continue;
    try {
      const data = JSON.parse(line);
      if (data.type === 'send' && data.to && data.text) {
        const jid = data.to.includes('@') ? data.to : `${data.to}@c.us`;
        const sent = await client.sendMessage(jid, data.text);
        if (sent?.id?._serialized) sentByBot.add(sent.id._serialized);
      }
    } catch (e) {
      emit({ type: 'error', error: `stdin parse error: ${e.message}` });
    }
  }
});

client.initialize().catch((err) => {
  emit({ type: 'error', error: err.message });
  process.exit(1);
});
