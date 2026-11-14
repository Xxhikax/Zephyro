// Regressions from the 2026-10 code review.
// Run: node tests-frontend/test_tts_echo_and_prices.mjs
//
// 1. Barge-in: on speakers the mic picks up Zephyro's own TTS. Without an echo
//    guard that transcript cut the reply and was sent back as a user message.
// 2. speakResponse stripped "$...$" as LaTeX with no price guard:
//    "cuesta $50 y el pro $120" was spoken as "cuesta 120".
//
// Like test_stop_phrase_boundary.mjs, this extracts the REAL code from src/chat.js
// instead of replicating it.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(__dirname, "..", "src", "chat.js"), "utf8");

let failures = 0;
function check(name, cond) {
  console.log((cond ? "  ok   " : "  FAIL ") + name);
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

// ── Echo guard ───────────────────────────────────────────────────────────────
const body = sliceMethods(src, "_rememberSpoken(text) {", "_looksLikeTtsEcho(text) {");
// eslint-disable-next-line no-eval
const app = eval(`({\n${body}\n})`);

app._rememberSpoken("El modo Pomodoro arranca con veinticinco minutos de trabajo y después un descanso corto.");
check("echo of the reply is discarded",
  app._looksLikeTtsEcho("veinticinco minutos de trabajo y después"));
check("echo survives STT punctuation/accents",
  app._looksLikeTtsEcho("Veinticinco minutos, de trabajo."));
check("command over the reply still interrupts",
  !app._looksLikeTtsEcho("pausá el pomodoro"));
check("command sharing topic words still interrupts",
  !app._looksLikeTtsEcho("cuántos minutos de descanso me quedan"));
check("short utterances are never echo",
  !app._looksLikeTtsEcho("descanso corto"));

const streamed = {};
Object.assign(streamed, eval(`({\n${body}\n})`));
for (let i = 0; i < 6; i++) streamed._rememberSpoken(`oración número ${i} del streaming de prueba`);
check("streamed sentences stay in the window",
  streamed._looksLikeTtsEcho("oración número 0 del streaming"));

// ── Price-safe inline math strip ─────────────────────────────────────────────
const m = src.match(/\.replace\(\/\\\$\(\[\^\$\\n\]\+\)\\\$\/g, \(m, inner\) => \{[\s\S]*?\n {12}\}\)/);
check("inline-math replacer found in speakResponse", !!m);
if (m) {
  // eslint-disable-next-line no-eval
  const strip = (s) => eval(`s${m[0]}`);
  check("prices survive", strip("cuesta $50 y el pro $120 al mes") === "cuesta $50 y el pro $120 al mes");
  check("TeX is stripped", strip("la fórmula $x^2 + 1$ crece") === "la fórmula  crece");
  check("short symbol is stripped", strip("el valor $\\pi$ es") === "el valor  es");
  check("big-O is stripped", strip("corre en $O(n)$ tiempo") === "corre en  tiempo");
}

if (failures) {
  console.log(`\n${failures} FAIL`);
  process.exit(1);
}
console.log("\nall ok");
