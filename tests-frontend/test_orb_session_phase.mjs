// Regresión del camino A: separación micPhase vs sessionPhase en el orbe
// (call-orb-visualizer.js). Correr: node tests-frontend/test_orb_session_phase.mjs
//
// Bug en vivo: al iniciar un Pomodoro el orbe NO quedaba amarillo — el call loop
// llama setState('listening') tras cada turno y pisaba 'pomodoro_work' (phase era
// una sola variable). Fix: el orbe deriva el pomodoro de CallState (fuente de
// verdad) en cada frame; los estados de interacción (speaking/thinking) ganan
// mientras ocurren, y en reposo cae al color del pomodoro.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(__dirname, "..", "src", "call-orb-visualizer.js"), "utf8");

let failures = 0;
function check(name, cond) {
  console.log((cond ? "  ok  " : "  FAIL ") + name);
  if (!cond) failures++;
}

console.log("Orb session phase (call-orb-visualizer.js):");

// 1. Existen los helpers de separación.
check("_sessionPhase definido", /_sessionPhase\s*\(\)\s*\{/.test(src));
check("_effectivePhase definido", /_effectivePhase\s*\(\)\s*\{/.test(src));
check("_sessionPhase lee CallState pomodoro",
      /_sessionPhase[\s\S]{0,400}pomodoroStartedAt/.test(src));

// 2. _tick usa la fase efectiva y recalcula color cada frame.
check("_tick usa _effectivePhase", /const phase = this\._effectivePhase\(\)/.test(src));
check("_tick recalcula color por frame",
      /_tick[\s\S]{0,600}this\._resolveColorTargets\(phase\)/.test(src));

// ── Réplica de la lógica de _effectivePhase ─────────────────────────────────
function effectivePhase(micPhase, sessionPhase) {
  if (micPhase === "speaking" || micPhase === "thinking"
      || micPhase === "researching" || micPhase === "cancelled") {
    return micPhase;
  }
  return sessionPhase || micPhase;
}

// Durante un pomodoro work, escuchando (reposo) → color pomodoro (NO listening).
check("pomodoro + listening → pomodoro_work (no se pisa)",
      effectivePhase("listening", "pomodoro_work") === "pomodoro_work");

// Durante pomodoro, escuchando idle → sigue pomodoro.
check("pomodoro + idle → pomodoro_work",
      effectivePhase("idle", "pomodoro_work") === "pomodoro_work");

// Estados de interacción ganan mientras ocurren (feedback inmediato).
check("pomodoro + speaking → speaking (Zephyro habla)",
      effectivePhase("speaking", "pomodoro_work") === "speaking");
check("pomodoro + thinking → thinking (procesando)",
      effectivePhase("thinking", "pomodoro_work") === "thinking");

// Sin pomodoro, el micPhase manda como siempre (no-regresión).
check("sin pomodoro + listening → listening",
      effectivePhase("listening", null) === "listening");
check("sin pomodoro + idle → idle",
      effectivePhase("idle", null) === "idle");

// Pomodoro break en reposo → verde de descanso.
check("pomodoro_break + listening → pomodoro_break",
      effectivePhase("listening", "pomodoro_break") === "pomodoro_break");

console.log(failures === 0 ? "\nALL OK" : `\n${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
