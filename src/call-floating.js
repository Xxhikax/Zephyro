// Thin floating window. Listens to Tauri events from main for state + duration.
// Sends restore/end requests back via invoke.
(function () {
    const invoke = window.__TAURI__?.core?.invoke || window.__TAURI__?.invoke;
    const listen = window.__TAURI__?.event?.listen;

    const $phase = document.getElementById('fl-phase');
    const $sub = document.getElementById('fl-sub');
    const $dur = document.getElementById('fl-dur');
    const $pomo = document.getElementById('fl-pomo');
    const $pomoPhase = document.getElementById('fl-pomo-phase');
    const $pomoCycle = document.getElementById('fl-pomo-cycle');
    const $pomoNext = document.getElementById('fl-pomo-next');
    const $body = document.body;
    const $diag = document.getElementById('fl-diag');
    const $diagSummary = document.getElementById('fl-diag-summary');

    function fmtMin(sec) {
        if (!sec) return '';
        const m = Math.round(sec / 60);
        return m >= 1 ? `${m}min` : `${sec}s`;
    }

    function nextLabel(phaseDetail) {
        if (phaseDetail === 'work') return 'trabajo';
        if (phaseDetail === 'long_break') return 'descanso largo';
        if (phaseDetail === 'short_break') return 'descanso';
        return '';
    }

    let lastStartedAt = 0;
    let currentDiagRequestId = null;
    let _pomoInt = null;

    function applyState(s) {
        if (s.startedAt) lastStartedAt = s.startedAt;
        const phase = (s.phase || 'listening').toLowerCase();
        $body.classList.remove('phase-idle', 'phase-listening', 'phase-thinking', 'phase-speaking',
            'phase-pomodoro_work', 'phase-pomodoro_break');
        $body.classList.add('phase-' + phase);
        $phase.textContent = phase.replace('_', ' ').toUpperCase();
        $sub.textContent = s.sessionId ? ('#' + s.sessionId) : 'zephyro · call';
        if (typeof s.startedAt === 'number' && s.startedAt > 0) {
            const elapsed = Date.now() - s.startedAt;
            const m = Math.floor(elapsed / 60000);
            const sec = Math.floor((elapsed / 1000) % 60);
            $dur.textContent = `${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
        }
        // Pomodoro state in floating window — countdown + phase + cycle + next preview.
        if ($pomo) {
            const hasPomo = s.pomodoroStartedAt > 0 && s.pomodoroTotalSec > 0;
            $pomo.style.display = hasPomo ? '' : 'none';
            if (hasPomo) {
                // Phase badge (TRABAJO/DESCANSO with color via data-phase attr).
                if ($pomoPhase) {
                    const detail = s.pomodoroPhaseDetail || s.pomodoroPhase || 'work';
                    const label = detail === 'work'
                        ? 'TRABAJO'
                        : (detail === 'long_break' ? 'DESCANSO LARGO' : 'DESCANSO');
                    $pomoPhase.textContent = label;
                    $pomoPhase.dataset.phase = detail === 'work' ? 'work' : 'break';
                }
                if ($pomoCycle) {
                    const c = s.pomodoroCycle || 0;
                    const t = s.pomodoroTotalCycles || 0;
                    $pomoCycle.textContent = c && t ? `${c}/${t}` : '';
                }
                if ($pomoNext) {
                    const nextDur = fmtMin(s.pomodoroNextDurationSec || 0);
                    const nextLbl = nextLabel(s.pomodoroNextPhase);
                    $pomoNext.textContent = (nextDur && nextLbl)
                        ? `luego: ${nextLbl} ${nextDur}` : '';
                }
                // Pintar el countdown AHORA (no esperar el primer tick a los
                // 500ms). En background la main pushea estado muy seguido, así que
                // applyState corre repetido; si dependiéramos del setInterval, este
                // se recrearía antes de cada tick y NUNCA pintaría → quedaba el
                // placeholder "25:00" del HTML congelado. Render inmediato + tick.
                const renderPomo = () => {
                    if (s.pomodoroPaused) { $pomo.textContent = '⏸ PAUSED'; return; }
                    const elapsed = (Date.now() - s.pomodoroStartedAt) / 1000;
                    const rem = Math.max(0, s.pomodoroTotalSec - elapsed);
                    const mm = String(Math.floor(rem / 60)).padStart(2, '0');
                    const ss = String(Math.floor(rem % 60)).padStart(2, '0');
                    $pomo.textContent = `⏱ ${mm}:${ss}`;
                    if (rem <= 0 && _pomoInt) { clearInterval(_pomoInt); _pomoInt = null; }
                };
                renderPomo();
                if (_pomoInt) clearInterval(_pomoInt);
                _pomoInt = setInterval(renderPomo, 500);
            } else {
                if (_pomoInt) { clearInterval(_pomoInt); _pomoInt = null; }
                if ($pomoPhase) $pomoPhase.textContent = '';
                if ($pomoCycle) $pomoCycle.textContent = '';
                if ($pomoNext) $pomoNext.textContent = '';
            }
        }
    }

    function showDiag(payload) {
        if (!$diag) return;
        currentDiagRequestId = payload.request_id || null;
        $diagSummary.textContent = payload.hypothesis_summary || payload.text || '—';
        $diag.classList.add('visible');
        // Resize the window to fit the expanded panel.
        try { invoke?.('set_floating_height', { height: 260 }); } catch (_) {}
    }

    function hideDiag() {
        if (!$diag) return;
        $diag.classList.remove('visible');
        currentDiagRequestId = null;
        try { invoke?.('set_floating_height', { height: 92 }); } catch (_) {}
    }

    if (listen) {
        listen('call-state', (evt) => applyState(evt.payload || {})).catch(() => {});
        listen('diag-state', (evt) => {
            const p = evt.payload || {};
            if (!p.request_id || p.status === 'resolved' || p.status === 'cancelled' || p.status === 'idle') {
                hideDiag();
                return;
            }
            if (p.status === 'awaiting_approval') {
                showDiag(p);
            }
        }).catch(() => {});
    }

    // Local ticking timer (updates duration between state pushes)
    setInterval(() => {
        if (!lastStartedAt) return;
        const elapsed = Date.now() - lastStartedAt;
        const m = Math.floor(elapsed / 60000);
        const sec = Math.floor((elapsed / 1000) % 60);
        $dur.textContent = `${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
    }, 500);

    document.getElementById('fl-restore')?.addEventListener('click', async () => {
        try { await invoke?.('exit_background_mode'); } catch (e) { console.warn(e); }
    });
    document.getElementById('fl-end')?.addEventListener('click', async () => {
        try { await invoke?.('end_call_from_floating'); } catch (e) { console.warn(e); }
    });

    // Diag approval buttons
    function sendDiagAction(action) {
        if (!currentDiagRequestId) return;
        try {
            invoke?.('diag_response', { requestId: currentDiagRequestId, action });
        } catch (e) { console.warn('[floating] diag_response failed', e); }
        // Optimistic collapse — main window will confirm via diag-state.
        hideDiag();
    }
    document.getElementById('fd-view')?.addEventListener('click', () => sendDiagAction('view'));
    document.getElementById('fd-ignore')?.addEventListener('click', () => sendDiagAction('ignore'));
    document.getElementById('fd-more')?.addEventListener('click', () => sendDiagAction('more_context'));
})();
