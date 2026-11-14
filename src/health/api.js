/* health/api.js — Wrapper fetch hacia /api/fitness/* y /api/nutrition/*
   Todas las funciones retornan { ok: bool, data, error }
   Token leído desde localStorage 'authToken' (mismo key que auth.js) */

(function () {
  'use strict';

  const API_BASE = 'http://localhost:8000';
  const USER_ID  = 'admin';

  function getToken() {
    return (window.auth && window.auth.getToken())
      || localStorage.getItem('authToken')
      || '';
  }

  function qs(extra) {
    const base = `user_id=${USER_ID}`;
    return extra ? `${base}&${extra}` : base;
  }

  async function req(method, path, body, isFormData) {
    try {
      const opts = { method };
      const t = getToken();
      if (t) opts.headers = { 'Authorization': `Bearer ${t}`, 'X-Token': t };
      if (body) {
        if (isFormData) {
          opts.body = body;            // do NOT set Content-Type for FormData
        } else {
          opts.headers = { ...(opts.headers || {}), 'Content-Type': 'application/json' };
          opts.body = JSON.stringify(body);
        }
      }
      const url = `${API_BASE}${path}`;
      const r = await fetch(url, opts);
      const data = await r.json().catch(() => ({}));
      if (!r.ok) return { ok: false, data: null, error: data.detail || `HTTP ${r.status}` };
      return { ok: true, data, error: null };
    } catch (e) {
      return { ok: false, data: null, error: e.message };
    }
  }

  /* ── FITNESS ─────────────────────────────────────────────────── */

  async function generatePlan(context) {
    return req('POST', `/api/fitness/plan/generate?${qs()}`, {
      user_id: USER_ID,
      context: context || 'home',
      force: false,
    });
  }

  async function getCurrentPlan() {
    return req('GET', `/api/fitness/plan/current?${qs()}`);
  }

  async function logSession(sessionId, notes) {
    return req('POST', `/api/fitness/session/log?${qs()}`, {
      session_id: sessionId,
      notes: notes || '',
    });
  }

  async function verifySession(sessionId, message, file) {
    const fd = new FormData();
    fd.append('session_id', sessionId);
    fd.append('message', message || '');
    if (file) fd.append('file', file);
    return req('POST', `/api/fitness/session/verify?${qs()}`, fd, true);
  }

  async function getStats() {
    return req('GET', `/api/fitness/stats?${qs()}`);
  }

  async function setPreferences(prefs) {
    return req('POST', `/api/fitness/preferences?${qs()}`, {
      user_id: USER_ID,
      ...prefs,
    });
  }

  async function getAlternatives(sessionId) {
    return req('GET', `/api/fitness/session/${sessionId}/alternatives?${qs()}`);
  }

  async function reschedule(sessionId, newTs, reason) {
    return req('POST', `/api/fitness/session/${sessionId}/reschedule?${qs()}`, {
      new_start_ts_utc: newTs,
      reason: reason || 'manual',
    });
  }

  /* ── NUTRITION ───────────────────────────────────────────────── */

  async function getNutritionProfile() {
    return req('GET', `/api/nutrition/profile?${qs()}`);
  }

  async function analyzeFoodQuick(text, file, mealType) {
    const fd = new FormData();
    fd.append('user_id', USER_ID);
    fd.append('description', text || '');
    fd.append('meal_type', mealType || 'other');
    if (file) fd.append('file', file);
    return req('POST', `/api/nutrition/food/log-quick?${qs()}`, fd, true);
  }

  async function getDaily(date) {
    const d = date || new Date().toISOString().split('T')[0];
    return req('GET', `/api/nutrition/daily?${qs('date=' + d)}`);
  }

  async function suggestMeal() {
    return req('GET', `/api/nutrition/suggest?${qs()}`);
  }

  async function logWater(ml) {
    return req('POST', `/api/nutrition/water?${qs()}`, {
      user_id: USER_ID,
      amount_ml: ml,
    });
  }

  async function logSteps(steps, date) {
    const body = { user_id: USER_ID, steps };
    if (date) body.date = date;
    return req('POST', `/api/nutrition/steps?${qs()}`, body);
  }

  async function logWeight(kg, date) {
    const body = { user_id: USER_ID, weight_kg: kg };
    if (date) body.date = date;
    return req('POST', `/api/nutrition/weight?${qs()}`, body);
  }

  async function getWeightTrend() {
    return req('GET', `/api/nutrition/weight/trend?${qs()}`);
  }

  async function getWeekly() {
    return req('GET', `/api/nutrition/weekly?${qs()}`);
  }

  /* ── Perfil de fitness (profile) ─────────────────────────────── */
  async function getProfile() {
    return req('GET', `/api/fitness/plan/current?${qs()}`);
  }

  /* ── Export ──────────────────────────────────────────────────── */
  window.HEALTH_API = {
    getToken,
    // fitness
    getProfile,
    generatePlan,
    getCurrentPlan,
    logSession,
    verifySession,
    getStats,
    setPreferences,
    getAlternatives,
    reschedule,
    // nutrition
    getNutritionProfile,
    analyzeFoodQuick,
    getDaily,
    suggestMeal,
    logWater,
    logSteps,
    logWeight,
    getWeightTrend,
    getWeekly,
  };
})();
