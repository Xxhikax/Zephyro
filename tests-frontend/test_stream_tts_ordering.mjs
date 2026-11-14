// Regression: streaming-TTS chunks must play in order and the stream must always close.
// Run: node tests-frontend/test_stream_tts_ordering.mjs
//
// Bugs covered:
//  1. _handleTtsChunk awaited decodeAudioData without serializing: a short later chunk that
//     decoded first was scheduled first (out-of-order audio), and if it was the is_last chunk
//     its onended finalized the stream and cut the rest.
//  2. A terminal {audio:null, is_last:true} frame crashed in atob/decode and returned before
//     the is_last branch → call mode stayed stuck on "speaking".
//
// Extracts the REAL methods from src/chat.js (no replica) and runs them with a fake AudioContext.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(__dirname, "..", "src", "chat.js"), "utf8");

let failures = 0;
const out = (line) => process.stdout.write(line + "\n");
function check(name, cond) {
  out((cond ? "  ok   " : "  FAIL ") + name);
  if (!cond) failures++;
}

function sliceMethods(source, firstMember, lastMethod) {
  const start = source.indexOf(`    ${firstMember}`);
  if (start < 0) throw new Error(`missing ${firstMember} in chat.js`);
  const lastAt = source.indexOf(`    ${lastMethod}`, start);
  if (lastAt < 0) throw new Error(`missing ${lastMethod} in chat.js`);
  const close = source.indexOf("\n    },", lastAt);
  return source.slice(start, close + "\n    },".length);
}

const body = sliceMethods(src, "async _handleTtsChunk(payload) {", "_teardownStreamTts(");
// eslint-disable-next-line no-eval
const methods = eval(`({\n${body}\n})`);

globalThis.window = { CallState: { setPhase() {}, incTurn() {} }, CallMode: { attachTtsAnalyserNode() {} } };
// chat.js logs every chunk; keep the test output to check() lines only.
console.log = () => {};
console.warn = () => {};

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function makeCtx(scheduled) {
  return {
    state: "running",
    currentTime: 0,
    destination: {},
    createAnalyser: () => ({ connect() {}, disconnect() {} }),
    // Decode latency is encoded in the payload byte: chunk 0 is the slowest.
    decodeAudioData: async (buf) => {
      const delay = new Uint8Array(buf)[0];
      await sleep(delay);
      return { duration: 0.01, tag: delay };
    },
    createBufferSource() {
      const node = { connect() {}, stop() {}, onended: null };
      node.start = (t) => scheduled.push({ tag: node.buffer.tag, t, node });
      return node;
    },
  };
}

function makeChat(scheduled) {
  const chat = Object.assign({
    callMode: true, ttsEnabled: true,
    _streamTtsGen: 0, _streamTtsRequestId: "r1", _streamTtsQueue: [], _streamTtsEnd: 0,
    _streamTtsText: "", _streamTtsActive: true, _streamTtsCtx: null, _streamTtsTimer: null,
    finalized: 0,
    _rememberSpoken() {},
  }, methods);
  chat._streamTtsSharedCtx = makeCtx(scheduled);
  const realFinalize = chat._finalizeStreamTts.bind(chat);
  chat._finalizeStreamTts = () => { chat.finalized++; realFinalize(); };
  return chat;
}

const b64 = (byte) => Buffer.from([byte]).toString("base64");

out("Streaming TTS ordering (src/chat.js):");

// 1. Out-of-order decode completion must not reorder playback.
{
  const scheduled = [];
  const chat = makeChat(scheduled);
  const delays = [40, 5, 1];
  await Promise.all(delays.map((d, i) => chat._handleTtsChunk({
    request_id: "r1", chunk_index: i, audio: b64(d), text: `s${i}`, is_last: i === delays.length - 1,
  })));
  check("chunks scheduled in chunk_index order", scheduled.map((s) => s.tag).join(",") === "40,5,1");
  check("start times are monotonic", scheduled.every((s, i) => i === 0 || s.t >= scheduled[i - 1].t));
  check("only the last source finalizes", scheduled[2].node.onended && !scheduled[0].node.onended);
  check("not finalized before playback ends", chat.finalized === 0);
  scheduled[2].node.onended();
  check("finalized when last source ends", chat.finalized === 1);
  check("stream state reset", chat._streamTtsActive === false && chat._streamTtsRequestId === null);
}

// 2. Terminal frame with audio:null after real chunks waits for scheduled audio, then finalizes.
{
  const scheduled = [];
  const chat = makeChat(scheduled);
  await chat._handleTtsChunk({ request_id: "r1", chunk_index: 0, audio: b64(1), text: "a", is_last: false });
  await chat._handleTtsChunk({ request_id: "r1", chunk_index: 1, audio: null, text: "", is_last: true });
  await sleep(80);
  check("audio:null terminal finalizes after drain", chat.finalized === 1);
}

// 3. Turn where every sentence failed: only the terminal frame arrives → state must reset.
{
  const chat = makeChat([]);
  await chat._handleTtsChunk({ request_id: "r1", chunk_index: 0, audio: null, text: "", is_last: true });
  check("no-audio turn does not stay stuck", chat._streamTtsActive === false);
}

// 4. Chunks from a cancelled generation are dropped.
{
  const scheduled = [];
  const chat = makeChat(scheduled);
  const p = chat._handleTtsChunk({ request_id: "r1", chunk_index: 0, audio: b64(20), text: "a", is_last: false });
  chat._streamTtsGen++;
  await p;
  check("stale-generation chunk not scheduled", scheduled.length === 0);
}

out(failures === 0 ? "\nALL OK" : `\n${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
