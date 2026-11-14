// Regresión del fix del countdown de la ventana flotante (call-floating.js/html).
// Correr: node tests-frontend/test_floating_pomo_timer.mjs
//
// Bug en vivo: la flotante mostraba "25:00" ESTÁTICO. Causa: el HTML tenía
// "25:00" hardcodeado como placeholder y el setInterval que lo actualiza se
// recreaba en CADA applyState (la main pushea estado muy seguido en background),
// reseteándose antes del primer tick a 500ms → nunca pintaba → quedaba el
// placeholder congelado. Fix: render inmediato en applyState + placeholder neutro.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const js = readFileSync(join(__dirname, "..", "src", "call-floating.js"), "utf8");
const html = readFileSync(join(__dirname, "..", "src", "call-floating.html"), "utf8");

let failures = 0;
function check(name, cond) {
  console.log((cond ? "  ok  " : "  FAIL ") + name);
  if (!cond) failures++;
}

console.log("Floating pomodoro timer (call-floating):");

// 1. El HTML ya NO tiene el placeholder "25:00" engañoso.
check("HTML sin placeholder 25:00", !/id="fl-pomo"[^>]*>[^<]*25:00/.test(html));

// 2. Hay render inmediato (no se depende solo del setInterval).
check("render inmediato (renderPomo llamado fuera del interval)",
      /renderPomo\(\);\s*\n\s*if \(_pomoInt\)/.test(js));
check("el interval reusa renderPomo", /setInterval\(renderPomo, 500\)/.test(js));

// ── Réplica de la lógica de render: el countdown refleja el total real ──────
function renderPomo(s, nowMs) {
  if (s.pomodoroPaused) return "⏸ PAUSED";
  const elapsed = (nowMs - s.pomodoroStartedAt) / 1000;
  const rem = Math.max(0, s.pomodoroTotalSec - elapsed);
  const mm = String(Math.floor(rem / 60)).padStart(2, "0");
  const ss = String(Math.floor(rem % 60)).padStart(2, "0");
  return `⏱ ${mm}:${ss}`;
}

const now = 1_000_000_000_000;
// Pomodoro de 45 min recién arrancado → muestra ~45:00, NO 25:00.
check("45 min recién arrancado → 45:00 (no 25:00)",
      renderPomo({ pomodoroTotalSec: 45 * 60, pomodoroStartedAt: now }, now) === "⏱ 45:00");

// Tras 5 min → disminuye a 40:00 (no estático).
check("tras 5 min → 40:00 (disminuye)",
      renderPomo({ pomodoroTotalSec: 45 * 60, pomodoroStartedAt: now - 5 * 60 * 1000 }, now)
      === "⏱ 40:00");

// Pausado → PAUSED.
check("pausado → ⏸ PAUSED",
      renderPomo({ pomodoroTotalSec: 45 * 60, pomodoroStartedAt: now, pomodoroPaused: true }, now)
      === "⏸ PAUSED");

console.log(failures === 0 ? "\nALL OK" : `\n${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
