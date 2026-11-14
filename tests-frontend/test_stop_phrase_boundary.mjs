// Regresión: una stop phrase de una palabra se comía comandos enteros.
// Correr: node tests-frontend/test_stop_phrase_boundary.mjs
//
// Bug en vivo (Call Mode ON): "para el pomodoro" no hacía NADA — cero líneas en el
// log, el pomodoro seguía corriendo, el orbe quedaba en 'thinking'. Causa: "para" es
// stop phrase y el matcher viejo (_ngramMatch) buscaba la frase del pool como run
// contiguo dentro de utterances de hasta 3 palabras, así que "para el pomodoro"
// matcheaba "para" y moría en el gate de transcribeAndDispatch, sin print.
// El mismo defecto hacía que "fin de semana" colgara la llamada vía _matchesExitPhrase.
//
// Este test NO replica la lógica: extrae los métodos REALES de src/chat.js y los
// ejecuta contra los pools REALES de backend/config/*.json. Una réplica puede pasar
// mientras el original está roto.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");
const src = readFileSync(join(root, "src", "chat.js"), "utf8");

let failures = 0;
function check(name, cond) {
  console.log((cond ? "  ok   " : "  FAIL ") + name);
  if (!cond) failures++;
}

// ── Extraer los métodos reales del object literal de chat.js ─────────────────
function sliceMethods(source, firstMember, lastMethod) {
  const start = source.indexOf(`    ${firstMember}`);
  if (start < 0) throw new Error(`no encontré ${firstMember} en chat.js`);
  const lastAt = source.indexOf(`    ${lastMethod}`, start);
  if (lastAt < 0) throw new Error(`no encontré ${lastMethod} en chat.js`);
  const close = source.indexOf("\n    },", lastAt);
  if (close < 0) throw new Error(`no encontré el cierre de ${lastMethod}`);
  return source.slice(start, close + "\n    },".length);
}

const body = sliceMethods(src, "_normalizePhrase(text) {", "_matchesExitPhrase(text) {");
// eslint-disable-next-line no-eval
const M = eval(`({\n${body}\n})`);

M._stopPhrases = JSON.parse(
  readFileSync(join(root, "backend", "config", "stop_phrases.json"), "utf8"));
M._exitPhrases = JSON.parse(
  readFileSync(join(root, "backend", "config", "exit_phrases.json"), "utf8"));

console.log("Stop/exit phrase boundary (métodos reales de src/chat.js):\n");

// ── 1. El bug original: comandos de dominio NO son stop phrases ──────────────
console.log(" comandos que deben despacharse (no son cancelaciones):");
for (const cmd of [
  "para el pomodoro",
  "pará el pomodoro",
  "Pará el pomodoro.",
  "para el pomodoro zephyro",
  "cancela la alarma",
  "cancela el recordatorio",
  "detene la musica",
  "stop the pomodoro",
  "cancel the timer",
]) {
  check(`"${cmd}" → despacha`, M._matchesStopPhrase(cmd) === false);
}

// ── 2. Las cancelaciones reales siguen funcionando ───────────────────────────
console.log("\n cancelaciones que deben seguir cancelando:");
for (const stop of [
  "para",
  "pará",
  "Pará.",
  "para ya",
  "para ahora",
  "pará zephyro",
  "para por favor",
  "para por favor ahora",
  "ya basta",
  "basta",
  "ya callate",
  "callate",
  "detente",
  "detente un momento",
  "silencio",
  "cancela",
  "para de hablar",
  "stop",
  "stop please",
  "stop talking",
  "be quiet",
]) {
  check(`"${stop}" → cancela`, M._matchesStopPhrase(stop) === true);
}

// ── 3. Exit phrases: mismo límite de borde ───────────────────────────────────
console.log("\n exit phrases (cierran la llamada) vs frases que la contienen:");
for (const exit of ["fin", "chau", "chao", "adiós", "cuelga", "termina la llamada", "bye"]) {
  check(`"${exit}" → cierra`, M._matchesExitPhrase(exit) === true);
}
for (const notExit of [
  "fin de semana",
  "chau nomas",
  "el fin del mundo",
  "termina el informe",
  "cuelga la foto",
]) {
  check(`"${notExit}" → NO cierra`, M._matchesExitPhrase(notExit) === false);
}

// ── 4. Invariantes del stripping de rellenos ─────────────────────────────────
console.log("\n invariantes del stripping:");
check("una frase que es puro relleno no se vacía ('ya' no matchea)",
      M._matchesStopPhrase("ya") === false);
check("'por' suelto nunca se saca ('para por' no es cancelación)",
      M._matchesStopPhrase("para por") === false);
check("'un momento' se saca como unidad, no solo 'momento'",
      M._stripEdgeFillers("detente un momento") === "detente");
check("relleno interno NO se saca (queda el objeto del comando)",
      M._stripEdgeFillers("para el pomodoro") === "para el pomodoro");
check("vacío / nulo no matchea", M._matchesStopPhrase("") === false
      && M._matchesStopPhrase(null) === false
      && M._matchesStopPhrase(undefined) === false);

// ── 5. El matcher viejo no puede volver ──────────────────────────────────────
console.log("\n guardas estructurales:");
check("_ngramMatch eliminado de chat.js", !/_ngramMatch/.test(src));
check("el gate de interrupt ya no descarta en silencio",
      !/console\.debug\('\[barge-in\] descartado/.test(src));
check("el swallow idle imprime y resetea la fase",
      /STT \(stop, nada en curso\)/.test(src)
      && /STT \(stop, nada en curso\)[\s\S]{0,200}setPhase\('listening'\)/.test(src));
check("el comando sobre TTS interrumpe y cae al dispatch",
      /STT \(interrumpe\)/.test(src));

console.log(failures === 0
  ? "\nTodo verde."
  : `\n${failures} fallo(s).`);
process.exit(failures === 0 ? 0 : 1);
