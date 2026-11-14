// Regresión del fix de rehidratación del Pomodoro (chat.js _rehydratePomodoro).
// Correr con: node tests-frontend/test_pomodoro_rehydrate.mjs
//
// Bug en vivo: un pomodoro de 45 min se mostraba como 25 min en la ventana
// flotante. Causa: _rehydratePomodoro usaba el tiempo RESTANTE del servidor como
// duración TOTAL y reanclaba startedAt='ahora'. En cada reconexión del WS (varias
// por sesión) el pomodoro se "encogía" a lo que quedaba. Fix: el backend expone
// total_sec + started_at y el front los usa → el countdown queda idéntico al real.
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

console.log("Pomodoro rehydrate (chat.js):");

// 1. El fix usa total_sec del backend (ya no el restante como total).
check("rehydrate lee data.total_sec", /Number\(data\.total_sec\)/.test(src));
check("rehydrate lee data.started_at", /data\.started_at/.test(src));

// 2. Ya NO existe el bug `pomodoroTotalSec: remainingSec` directo.
check("NO asigna remainingSec como total directo",
      !/pomodoroTotalSec:\s*remainingSec\b/.test(src));

// 3. Mantiene fallback para backend viejo (sin total_sec).
check("fallback a remaining si falta total_sec",
      /totalForState = remainingSec/.test(src));

// ── Réplica de la lógica: reconstrucción del countdown tras rehidratar ──────
// Simula el backend autoritativo y verifica que el countdown reconstruido en el
// front coincide con el restante REAL (no se encoge al restante-como-total).
function rehydrate(data, nowMs) {
  const remainingSec = Number(data.remaining_sec) || 0;
  const totalSec = Number(data.total_sec) || 0;
  let startedAtMs, totalForState;
  if (totalSec > 0 && data.started_at) {
    startedAtMs = Math.round(data.started_at * 1000);
    totalForState = totalSec;
  } else {
    startedAtMs = nowMs;
    totalForState = remainingSec;
  }
  // Countdown que renderiza la flotante: total - (now - started).
  const elapsed = (nowMs - startedAtMs) / 1000;
  const shownRemaining = Math.max(0, totalForState - elapsed);
  return { totalForState, shownRemaining };
}

// Pomodoro de 45 min, arrancó hace 5 min (server epoch). Reconexión AHORA.
const now = 1_000_000_000_000; // ms arbitrario
const startedAt = now / 1000 - 5 * 60; // hace 5 min, en segundos epoch
const r = rehydrate(
  { active: true, total_sec: 45 * 60, started_at: startedAt,
    remaining_sec: 40 * 60, phase: "work" },
  now,
);
check("total reconstruido = 45 min (no se encoge)", r.totalForState === 45 * 60);
check("restante mostrado = 40 min reales (±1s)",
      Math.abs(r.shownRemaining - 40 * 60) < 1);

// Reconexión repetida: NO debe encoger más (idempotente con el mismo server).
const r2 = rehydrate(
  { active: true, total_sec: 45 * 60, started_at: startedAt,
    remaining_sec: 40 * 60, phase: "work" },
  now + 1000,
);
check("segunda reconexión sigue mostrando 45 min de total",
      r2.totalForState === 45 * 60);

// Fallback (backend viejo sin total_sec): degrada al restante, sin romper.
const rOld = rehydrate({ active: true, remaining_sec: 30 * 60, phase: "work" }, now);
check("fallback backend viejo no rompe", rOld.totalForState === 30 * 60);

console.log(failures === 0 ? "\nALL OK" : `\n${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
