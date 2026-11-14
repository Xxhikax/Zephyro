// Test de regresión de los guards del call mode (src/chat.js).
// Correr con: node tests-frontend/test_callmode_guards.mjs
// Verifica el fix anti-eco: el exit phrase NO cierra el modo cuando el audio se
// grabó mientras Zephyro hablaba (isInterruptCandidate = probable eco del TTS).
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(__dirname, "..", "src", "chat.js"), "utf8");

let failures = 0;
function check(name, cond) {
  console.log((cond ? "  ok  " : "  FAIL ") + name);
  if (!cond) failures++;
}

console.log("Call mode guards (src/chat.js):");

// 1. El exit phrase ahora exige !isInterruptCandidate (anti-eco).
check("exit phrase condicionado a !isInterruptCandidate",
      /!isInterruptCandidate\s*&&\s*this\._matchesExitPhrase/.test(src));

// 2. TTS stall watchdog: marks _ttsTimedOut and is re-armed on every progress step
//    (synth resolved + chunk played), so a long multi-chunk answer is never cut by a
//    fixed total budget (replaced the old length-proportional timeout).
check("TTS timeout marca _ttsTimedOut", src.includes("this._ttsTimedOut = true"));
check("TTS stall watchdog se re-arma en cada progreso",
      /const _ttsStallMs = \d+/.test(src) &&
      (src.match(/_armStall\(\);/g) || []).length >= 3);

// 3. El catch avisa cuando se cortó por stall (no queda mudo sin aviso).
check("avisa corte de TTS por stall",
      /if \(this\._ttsTimedOut\)[\s\S]{0,160}se colgó/.test(src));

// 4. Replica de la lógica del guard: exit phrase sólo cierra si NO es interrupt.
function shouldClose({ fromCallMode, isInterruptCandidate, isExit }) {
  // refleja: if (fromCallMode && !isInterruptCandidate && matchesExitPhrase)
  return fromCallMode && !isInterruptCandidate && isExit;
}
check("eco (interrupt) con exit phrase → NO cierra",
      shouldClose({ fromCallMode: true, isInterruptCandidate: true, isExit: true }) === false);
check("exit phrase en idle → cierra",
      shouldClose({ fromCallMode: true, isInterruptCandidate: false, isExit: true }) === true);
check("interrupt sin exit phrase → NO cierra",
      shouldClose({ fromCallMode: true, isInterruptCandidate: true, isExit: false }) === false);

console.log(failures === 0 ? "\nALL OK" : `\n${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
