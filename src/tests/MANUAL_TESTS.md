# Manual Tests — Pomodoro × Call Mode × Focus

Frontend regressions for the Pomodoro / Call / Focus rework. No JS test
runner is configured in this repo, so these are documented as repeatable
manual steps. Run them whenever any of the following change:

- `src/chat.js` (`_handleFocusAlert`, `_handlePomodoroWs`, `_showPomoBar`)
- `src/call-mode.js` (`_startTimers`, `_startPomodoroTimer`, `_snapshotPayload`)
- `src/call-floating.js` (BG widget rendering)
- `src/call-state.js` (pomodoro fields)
- Backend `monitor.py` payload shape (`_broadcast_pomodoro_state`)

Each test lists the steps, the **expected** result, and the **regression**
that justifies it. If you change the implementation, update the doc.

---

## 14. Focus alert level 1 stays silent (no TTS)

**Steps**
1. `/call` → wait for `READY`.
2. `/mode cloud` (ensure TTS active).
3. Start a 1-cycle × 2 min Pomodoro: "inicia pomodoro de 2 minutos".
4. Open `youtube.com/watch?v=…` in any window.
5. Wait ~10 seconds for the first `[FOCO]` alert to appear in chat.

**Expected**
- `[FOCO]` text appears in chat.
- **No TTS** — Zephyro stays silent. (Level 1 ships `should_speak=false`.)

**Why**
Level 1 is the soft reminder. Speaking it every grace expiry would drown the
user during a normal study session. Bug existed pre-refactor — TTS fired on
every level regardless.

---

## 15. Focus alert level ≥ 2 speaks, but queues behind active TTS

**Steps**
1. Same setup as #14 but reach level 2 (~2 min in YouTube).
2. While the focus level 2 message is firing, immediately ask Zephyro a
   normal question ("¿qué hora es?") so it starts speaking a reply.
3. Trigger another level 2/3 distraction event during the reply.

**Expected**
- The new focus alert does **not** interrupt the ongoing reply.
- After the reply finishes, the queued alert speaks within ~0.5 s.

**Why**
Pre-refactor `_handleFocusAlert` called `speakResponse` unconditionally,
talking over Zephyro's own reply.

---

## 16. Muted call suppresses focus TTS

**Steps**
1. Start call mode + Pomodoro.
2. Press the MUTE button in the HUD.
3. Provoke a level-3 distraction.

**Expected**
- `[ALERTA FOCO]` appears in chat.
- No TTS plays.

**Why**
`_handleFocusAlert` now reads `state.muted` before speaking.

---

## 17. Pomo chat bar hidden during call mode

**Steps**
1. Start a Pomodoro in chat mode (no call).
2. Confirm `#pomo-chat-bar` is visible at the top of the terminal.
3. Toggle `/call` ON.

**Expected**
- `#pomo-chat-bar` disappears the moment call mode activates.
- The orb + HUD `POMO MM:SS` carry the state instead.
- Toggle `/call` OFF → the bar reappears with a live countdown that matches
  the HUD time it had a moment ago.

**Why**
Pre-refactor the colored bar leaked behind the call overlay as a stray
yellow/green rectangle. `_showPomoBar` now early-returns in call mode and
`toggleCallMode` re-shows it on exit.

---

## 18. Orb focus badge clears on phase transition

**Steps**
1. Call mode + Pomodoro work phase.
2. Trigger a focus alert (open YouTube). Wait until orb shows the focus-l2
   ring (≈ 2 min).
3. Wait for the work phase to end → break phase starts.

**Expected**
- The moment the `end_work` WS event fires, the orb's `focus-l*` class is
  removed. No stale red/yellow ring carries into the break.

**Why**
`_handlePomodoroWs` now clears `focus-l1..4` on every phase transition.

---

## 19. Pomodoro timer resumes after MIN → restore

**Steps**
1. Call mode + Pomodoro 1 × 5 min running.
2. Note current HUD countdown (e.g. 04:30).
3. Click MIN.
4. Wait 20 seconds.
5. Click the floating CALL_ACTIVE pill to restore.

**Expected**
- HUD countdown reads ~04:10 — it kept advancing, did **not** stick at
  04:30, and did **not** wait for the next WS event to start ticking again.

**Why**
`_startTimers` now re-arms `_startPomodoroTimer` if `pomodoroStartedAt` is
non-zero, instead of relying on the `zephyro:pomodoro-sync` event that only
fires on phase transitions.

---

## 20. Paused pomodoro shows ⏸ PAUSED in HUD and floating widget

**Steps**
1. Call mode + Pomodoro running.
2. Click the HUD `⏸` button (or say "pausa el pomodoro").
3. Observe HUD `POMO` field.
4. Click BG to enter floating mode.

**Expected**
- HUD shows `⏸ PAUSED` instead of MM:SS while paused.
- Floating window's countdown also shows `⏸ PAUSED`.
- Clicking the same button again (now showing `▶`) resumes. The countdown
  picks up from where it paused, **not** from the original start time.

**Why**
`_startPomodoroTimer` now early-returns when `state.pomodoroPaused` is true.
`_handlePomodoroWs` patches `pomodoroPaused` from the WS payload.

---

## 21. BG widget shows phase + cycle + next-phase preview

**Steps**
1. Start a 4-cycle × 5 min Pomodoro in call mode.
2. Press BG to enter the floating window.

**Expected**
- Floating window shows:
  - `⏱ 04:55` (countdown)
  - `TRABAJO` badge with yellow border
  - `1/4` cycle indicator
  - `luego: descanso 5min`
- After the first work phase ends, the badge flips to `DESCANSO` (green)
  and the next preview becomes `luego: trabajo 5min`.
- On cycle 4 work, preview becomes empty (session ending).

**Why**
Pre-refactor the floating widget only showed a bare countdown. The new
`_broadcast_pomodoro_state` payload includes `phase_detail` and
`next_phase` / `next_duration_sec`.

---

## 22. Extend by voice: ambiguous zone asks for confirmation

**Steps**
1. Start a Pomodoro with a 30-min work phase. Let it run for ~5 min.
2. Say "añade 10 minutos".

**Expected**
- Zephyro responds: "Te quedan ~25 min del ciclo. ¿Extender este ciclo
  10 min más, o agregar un ciclo nuevo al final? Decí 'extiende' o
  'ciclo nuevo'."
- Reply "extiende" → HUD total grows by 10 minutes (countdown jumps up).
- Reply "ciclo nuevo" instead → total cycles increases by 1, current
  countdown unchanged.

**Why**
The heuristic in `_handle_pomodoro` punts to a confirmation when
`remaining ≥ 5 min` and `minutes < 25`. The skill stashes the request in
`_pending_pomodoro_extend` and the next turn resolves it.

---

## 23. Extend by HUD button (+5 MIN) — no LLM round-trip

**Steps**
1. Pomodoro running.
2. Click `+5` button in the HUD.

**Expected**
- HUD countdown grows by ~5 minutes within ~1 second.
- No chat message from Zephyro (REST call only — no LLM turn billed).

**Why**
The HUD button posts to `/api/pomodoro/extend` directly. The skill is
bypassed entirely.

---

## 25. Vision Q&A: "mira mi pantalla" captura y responde

**Steps**
1. `/call` ON + `/mode cloud`.
2. Abre Cisco Packet Tracer (o cualquier ventana con contenido visible:
   diagrama, código, PDF, etc).
3. Di o escribe: "Zephyro, mira mi pantalla y dime qué ves".
4. Espera ~2-3 segundos.

**Expected**
- Zephyro responde con una descripción real de lo que está en la pantalla.
- **NO** debe decir "no tengo función de captura" ni similar.
- Probar variantes:
  - "qué ves en mi pantalla"
  - "ayúdame con esto que tengo en pantalla"
  - "describe lo que ves"
  - "puedes ver mi pantalla"
  - "mira esto"
- En logs del backend debería aparecer `[Vision]` / `[VisionQuery]` con el
  prompt enviado a Haiku.

**Why**
Pre-fix, ningún skill rutea esos comandos al endpoint
`/api/vision/screen-query`. El LLM principal respondía como si no tuviera
acceso a captura, lo cual era confuso porque watching mode SÍ corría visión
periódica internamente.

---

## 26. Chat bar reaparece al presionar MIN

**Steps**
1. `/call` ON + iniciar Pomodoro 2 min.
2. Confirmar que el HUD del call muestra el countdown POMO.
3. Presionar el botón MIN.

**Expected**
- El overlay se desvanece y aparece el pill "CALL_ACTIVE" flotante.
- **Después de ~300ms** (la transición de fade-out), la barra
  `#pomo-chat-bar` aparece encima del chat con countdown vivo (ticking).
- El usuario puede ver el tiempo del pomodoro SIN tener que restaurar el
  overlay haciendo click en la pill.
- Click en la pill → overlay vuelve a aparecer → barra de chat se oculta
  inmediatamente.

**Why**
Pre-fix, `_showPomoBar` chequeaba `callMode`. Como MIN deja `callMode=true`,
la barra quedaba escondida y la pill no tenía countdown → el usuario
no veía tiempo en ninguna superficie. Ahora `_showPomoBar` chequea
visibilidad del overlay, y `_minimize`/`hide`/`show` notifican a chat para
re-evaluar.

---

## 27. BG widget se sincroniza al cambiar de fase

**Steps**
1. `/call` ON + iniciar Pomodoro 1 ciclo × 1 min (test rápido).
2. Presionar BG → la ventana flotante aparece, muestra TRABAJO + countdown.
3. Esperar a que termine el minuto de trabajo.

**Expected**
- En el momento en que el backend emite `end_work`, la ventana flotante:
  - Cambia el badge de `TRABAJO` (amarillo) a `DESCANSO` (verde).
  - El countdown se resetea al tiempo del descanso (e.g. 5 min).
  - El preview "luego: …" cambia.
- NO debe quedar mostrando el countdown anterior congelado en 00:00 ni el
  badge stale.

**Why**
Pre-fix, `_snapshotPayload` SÍ incluía pomodoro fields, pero el subscribe
solo pusheaba si `s.background === true`. El cambio de fase del pomodoro
solo dispara una vez `state.background` ya está activo, así que el push
correcto sucedía. Pero pomodoro updates DURANTE BG no siempre llegaban si
las dependencias de background se evaluaban en orden distinto. Ahora cada
cambio en `pomodoroStartedAt/Paused/Phase/Cycle` dispara push independiente
del flag background.

---

## 28. Discord gracia larga durante call mode

**Steps**
1. Iniciar Pomodoro.
2. Activar `/call` ON.
3. Abrir Discord en una ventana visible (que sea la app activa).
4. Esperar 2 minutos navegando Discord.

**Expected**
- **NO** debe aparecer alerta `[FOCO]` por Discord durante esos 2 minutos
  (la gracia ahora es 180s = 3 min cuando JARVIS callMode está ON).
- Salir de `/call` (toggle OFF).
- Volver a Discord. **Expected**: la gracia vuelve a 0 (Discord es
  distracción inmediata cuando no hay call activo).

**Verificación adicional**
- En logs del backend: `POST /api/focus/jarvis-call-mode` con
  `{active:true}` al entrar a call, `{active:false}` al salir.
- `_classify_distraction("Discord — channel", "")` retorna
  `(True, "messaging_during_call")` durante call, `(True, "app")` fuera.

---

## 29. GitHub / Canvas / StackOverflow no generan FOCO

**Steps**
1. Pomodoro en progreso.
2. Abrir github.com, stackoverflow.com, canvas.<institution>.com en distintos
   tabs/momentos.
3. Esperar 2+ minutos en cada uno (más que cualquier gracia normal).

**Expected**
- **NO** aparece ningún `[FOCO]` por estos sitios.
- El reporte final de la sesión NO los lista como distracciones.

**Why**
Pre-fix, GitHub/Canvas/etc no estaban ni en whitelist ni en blacklist →
quedaban "neutrales" y nunca generaban alerta (que es lo deseado), pero
tampoco quedaban marcadas explícitamente como permitidas. La whitelist
ampliada las hace explícitas, lo que también previene que la visión Tier 2
las clasifique erróneamente como off-task.

---

## 24. Skip break button visibility

**Steps**
1. Start Pomodoro. During work phase.
2. Inspect HUD: `call-pomo-skip-btn` should be `display:none`.
3. Wait until break phase starts.

**Expected**
- The `⏭` (skip) button appears only during break phases.
- Clicking it ends the break immediately and starts the next work cycle.

**Why**
`_syncPomoHud` toggles the skip button based on `state.pomodoroPhase`.
