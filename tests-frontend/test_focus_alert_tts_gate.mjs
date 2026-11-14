// Gate de TTS de las alertas de foco (_handleFocusAlert en src/chat.js).
// Correr: node tests-frontend/test_focus_alert_tts_gate.mjs
//
// Bug medido en vivo (2026-10-04): el gate exigía `callMode` y nada más. La alerta de
// nivel 2 llegó a los 2 minutos mientras el usuario miraba YouTube FUERA de call mode,
// así que solo se imprimió en una ventana que no estaba mirando. Un aviso de distracción
// que solo escribe no avisa: la única situación en la que importa es justo cuando la
// atención está en otra parte.
//
// Decisión de producto: habla también fuera de call mode, pero SOLO con un Pomodoro
// corriendo. Arrancar un ciclo es pedir explícitamente que te vigilen; fuera de eso
// Zephyro no habla sin que lo hayas iniciado (el modo estudio suelto sigue en texto).
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

console.log("Gate de TTS en alertas de foco:\n");

// ── Guardas estructurales ────────────────────────────────────────────────────
console.log(" estructura:");
check("el gate ya no exige callMode solo",
      !/if \(this\.callMode && this\.ttsEnabled && shouldSpeak/.test(src));
check("deriva pomodoroActive de CallState.pomodoroPhase",
      /const pomodoroActive = !!\(window\.CallState\?\.get\(\)\?\.pomodoroPhase\)/.test(src));
check("el gate acepta callMode O pomodoroActive",
      /\(this\.callMode \|\| pomodoroActive\) && this\.ttsEnabled && shouldSpeak && !muted && msg/.test(src));
check("sigue respetando should_speak (nivel>=2)",
      /const shouldSpeak = payload\.should_speak === true/.test(src));
check("sigue respetando muted",
      /const muted = !!\(window\.CallState\?\.get\(\)\?\.muted\)/.test(src));
check("sigue encolando si ya está hablando",
      /if \(this\.isTtsSpeaking\) \{\s*\n\s*this\._queueFocusSpeak\(msg\)/.test(src));

// ── Réplica de la condición, para cubrir la tabla de casos ───────────────────
function speaks({ callMode, pomodoroActive, ttsEnabled, shouldSpeak, muted, msg }) {
  return !!((callMode || pomodoroActive) && ttsEnabled && shouldSpeak && !muted && msg);
}

const base = { callMode: false, pomodoroActive: false, ttsEnabled: true,
               shouldSpeak: true, muted: false, msg: "volvé al trabajo" };

console.log("\n casos que DEBEN hablar:");
check("call mode, sin pomodoro", speaks({ ...base, callMode: true }));
check("pomodoro activo, sin call mode  <-- el bug original",
      speaks({ ...base, pomodoroActive: true }));
check("ambos", speaks({ ...base, callMode: true, pomodoroActive: true }));

console.log("\n casos que NO deben hablar:");
check("ni call mode ni pomodoro (modo estudio suelto)", !speaks(base));
check("pomodoro pero TTS apagado",
      !speaks({ ...base, pomodoroActive: true, ttsEnabled: false }));
check("pomodoro pero muteado",
      !speaks({ ...base, pomodoroActive: true, muted: true }));
check("pomodoro pero nivel 1 (should_speak false)",
      !speaks({ ...base, pomodoroActive: true, shouldSpeak: false }));
check("pomodoro pero mensaje vacío",
      !speaks({ ...base, pomodoroActive: true, msg: "" }));

// ── pomodoroPhase tiene que limpiarse al terminar el ciclo ───────────────────
console.log("\n la señal se apaga cuando el pomodoro termina:");
check("session_complete pone pomodoroPhase en null",
      /action === 'session_complete'[\s\S]{0,400}pomodoroPhase: null/.test(src));
check("cancelled pone pomodoroPhase en null",
      /action === 'cancelled'[\s\S]{0,400}pomodoroPhase: null/.test(src));

console.log(failures === 0 ? "\nTodo verde." : `\n${failures} fallo(s).`);
process.exit(failures === 0 ? 0 : 1);
