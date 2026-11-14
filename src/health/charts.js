/* health/charts.js — Utilidades de visualización sin dependencias externas
   Canvas 2D y SVG inline. Todas las funciones son puras (no guardan estado). */

(function () {
  'use strict';

  /* ── drawRing ──────────────────────────────────────────────────
     ctx       : CanvasRenderingContext2D
     percent   : 0-100
     color     : CSS color string
     label     : texto central (puede ser multilinea con \n)
     opts      : { trackColor, lineWidth, fontSize }
  ──────────────────────────────────────────────────────────────── */
  function drawRing(ctx, percent, color, label, opts) {
    opts = opts || {};
    const w = ctx.canvas.width;
    const h = ctx.canvas.height;
    const cx = w / 2;
    const cy = h / 2;
    const lw = opts.lineWidth || Math.max(8, w * 0.1);
    const r  = (Math.min(w, h) / 2) - lw - 2;
    const trackColor = opts.trackColor || 'rgba(0,255,136,0.08)';
    const pct = Math.min(Math.max(percent, 0), 100);
    const startAngle = -Math.PI / 2;
    const endAngle   = startAngle + (2 * Math.PI * pct / 100);

    ctx.clearRect(0, 0, w, h);

    // Track
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, 2 * Math.PI);
    ctx.strokeStyle = trackColor;
    ctx.lineWidth   = lw;
    ctx.lineCap     = 'round';
    ctx.stroke();

    // Fill arc
    if (pct > 0) {
      ctx.beginPath();
      ctx.arc(cx, cy, r, startAngle, endAngle);
      ctx.strokeStyle = color;
      ctx.lineWidth   = lw;
      ctx.lineCap     = 'round';
      ctx.stroke();
    }

    // Label
    if (label) {
      const lines  = label.split('\n');
      const fsize  = opts.fontSize || Math.max(10, w * 0.14);
      ctx.fillStyle = '#CFFFEA';
      ctx.font      = `600 ${fsize}px "JetBrains Mono", monospace`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      const lineH = fsize * 1.25;
      const totalH = lines.length * lineH;
      lines.forEach(function (line, i) {
        const y = cy - totalH / 2 + lineH * i + lineH / 2;
        ctx.fillText(line, cx, y);
      });
    }
  }

  /* ── drawBars ──────────────────────────────────────────────────
     ctx    : CanvasRenderingContext2D
     values : array of numbers (same scale)
     color  : CSS color string
     opts   : { labels, maxValue, barGap, barRadius }
  ──────────────────────────────────────────────────────────────── */
  function drawBars(ctx, values, color, opts) {
    opts = opts || {};
    const w   = ctx.canvas.width;
    const h   = ctx.canvas.height;
    const n   = values.length;
    if (!n) return;
    const gap  = opts.barGap !== undefined ? opts.barGap : 4;
    const maxV = opts.maxValue || Math.max.apply(null, values) || 1;
    const barW = (w - gap * (n + 1)) / n;
    const padB = opts.labels ? 18 : 4;

    ctx.clearRect(0, 0, w, h);

    values.forEach(function (v, i) {
      const barH  = Math.max(2, ((v || 0) / maxV) * (h - padB - 4));
      const x     = gap + i * (barW + gap);
      const y     = h - padB - barH;
      const rad   = opts.barRadius !== undefined ? opts.barRadius : 3;

      ctx.fillStyle = color;
      if (rad && barH > rad * 2) {
        ctx.beginPath();
        ctx.moveTo(x + rad, y);
        ctx.lineTo(x + barW - rad, y);
        ctx.quadraticCurveTo(x + barW, y, x + barW, y + rad);
        ctx.lineTo(x + barW, y + barH);
        ctx.lineTo(x, y + barH);
        ctx.lineTo(x, y + rad);
        ctx.quadraticCurveTo(x, y, x + rad, y);
        ctx.closePath();
        ctx.fill();
      } else {
        ctx.fillRect(x, y, barW, barH);
      }

      if (opts.labels && opts.labels[i]) {
        ctx.fillStyle   = 'rgba(91,207,166,0.65)';
        ctx.font        = '9px "JetBrains Mono", monospace';
        ctx.textAlign   = 'center';
        ctx.textBaseline = 'bottom';
        ctx.fillText(opts.labels[i], x + barW / 2, h);
      }
    });
  }

  /* ── drawSparkline ─────────────────────────────────────────────
     ctx    : CanvasRenderingContext2D
     values : array of numbers
     color  : CSS color string
     opts   : { fill, lineWidth }
  ──────────────────────────────────────────────────────────────── */
  function drawSparkline(ctx, values, color, opts) {
    opts = opts || {};
    const w  = ctx.canvas.width;
    const h  = ctx.canvas.height;
    if (!values || values.length < 2) return;

    const minV = Math.min.apply(null, values);
    const maxV = Math.max.apply(null, values);
    const range = (maxV - minV) || 1;
    const n  = values.length;
    const lw = opts.lineWidth || 2;
    const pad = lw + 2;

    ctx.clearRect(0, 0, w, h);

    function px(i) { return pad + i * ((w - pad * 2) / (n - 1)); }
    function py(v) { return h - pad - ((v - minV) / range) * (h - pad * 2); }

    ctx.beginPath();
    ctx.moveTo(px(0), py(values[0]));
    values.forEach(function (v, i) {
      if (i > 0) ctx.lineTo(px(i), py(v));
    });
    ctx.strokeStyle = color;
    ctx.lineWidth   = lw;
    ctx.lineJoin    = 'round';
    ctx.stroke();

    if (opts.fill) {
      ctx.lineTo(px(n - 1), h);
      ctx.lineTo(px(0), h);
      ctx.closePath();
      ctx.fillStyle = color.replace(')', ', 0.15)').replace('rgb', 'rgba');
      ctx.fill();
    }

    // Dot on last point
    ctx.beginPath();
    ctx.arc(px(n - 1), py(values[n - 1]), lw + 1, 0, 2 * Math.PI);
    ctx.fillStyle = color;
    ctx.fill();
  }

  /* ── drawBottle ────────────────────────────────────────────────
     svg     : SVGElement (the container)
     percent : 0-100 (total_ml / 3000 * 100)
  ──────────────────────────────────────────────────────────────── */
  function drawBottle(svg, percent) {
    const NS  = 'http://www.w3.org/2000/svg';
    const pct = Math.min(Math.max(percent, 0), 100);
    svg.innerHTML = '';
    svg.setAttribute('viewBox', '0 0 60 140');

    // Bottle outline path
    const bottle = document.createElementNS(NS, 'path');
    bottle.setAttribute('d',
      'M22 8 C22 4 38 4 38 8 L40 20 C44 24 46 30 46 36 L46 120 C46 128 14 128 14 120 L14 36 C14 30 16 24 20 20 Z'
    );
    bottle.setAttribute('fill', 'none');
    bottle.setAttribute('stroke', 'rgba(0,255,136,0.3)');
    bottle.setAttribute('stroke-width', '1.5');
    svg.appendChild(bottle);

    // Fill level (clip from bottom)
    const fillH = (112 * pct / 100); // max fill height in svg units
    const fillY = 128 - fillH;

    const clipId = 'hw-bottle-clip-' + Math.random().toString(36).slice(2, 6);
    const defs   = document.createElementNS(NS, 'defs');
    const clip   = document.createElementNS(NS, 'clipPath');
    clip.setAttribute('id', clipId);
    const clipRect = document.createElementNS(NS, 'rect');
    clipRect.setAttribute('x', '0');
    clipRect.setAttribute('y', String(fillY));
    clipRect.setAttribute('width', '60');
    clipRect.setAttribute('height', '140');
    clip.appendChild(clipRect);
    defs.appendChild(clip);
    svg.appendChild(defs);

    const fill = document.createElementNS(NS, 'path');
    fill.setAttribute('d',
      'M22 8 C22 4 38 4 38 8 L40 20 C44 24 46 30 46 36 L46 120 C46 128 14 128 14 120 L14 36 C14 30 16 24 20 20 Z'
    );
    fill.setAttribute('fill', 'rgba(0,255,136,0.55)');
    fill.setAttribute('clip-path', `url(#${clipId})`);
    svg.appendChild(fill);

    // Percent text
    const text = document.createElementNS(NS, 'text');
    text.setAttribute('x', '30');
    text.setAttribute('y', '75');
    text.setAttribute('text-anchor', 'middle');
    text.setAttribute('dominant-baseline', 'middle');
    text.setAttribute('fill', '#CFFFEA');
    text.setAttribute('font-size', '11');
    text.setAttribute('font-family', '"JetBrains Mono", monospace');
    text.textContent = Math.round(pct) + '%';
    svg.appendChild(text);
  }

  /* ── drawGauge ─────────────────────────────────────────────────
     ctx   : CanvasRenderingContext2D
     value : 0-100 (adherencia)
     opts  : { color, trackColor, label }
  ──────────────────────────────────────────────────────────────── */
  function drawGauge(ctx, value, opts) {
    opts = opts || {};
    const w  = ctx.canvas.width;
    const h  = ctx.canvas.height;
    const cx = w / 2;
    const cy = h * 0.72;
    const r  = Math.min(cx, cy) * 0.82;
    const lw = Math.max(10, r * 0.18);
    const pct = Math.min(Math.max(value, 0), 100);

    const trackColor = opts.trackColor || 'rgba(0,255,136,0.08)';
    // Color: green if >= 80, warning if >= 50, danger otherwise
    const color = opts.color || (pct >= 80 ? '#00FF88' : pct >= 50 ? '#FFC857' : '#FF4B5C');

    const startA = Math.PI;          // 180°
    const endA   = 2 * Math.PI;      // 360° (only half circle)
    const fillEnd = startA + (Math.PI * pct / 100);

    ctx.clearRect(0, 0, w, h);

    // Track (half circle)
    ctx.beginPath();
    ctx.arc(cx, cy, r, startA, endA);
    ctx.strokeStyle = trackColor;
    ctx.lineWidth   = lw;
    ctx.lineCap     = 'round';
    ctx.stroke();

    // Fill arc
    if (pct > 0) {
      ctx.beginPath();
      ctx.arc(cx, cy, r, startA, fillEnd);
      ctx.strokeStyle = color;
      ctx.lineWidth   = lw;
      ctx.lineCap     = 'round';
      ctx.stroke();
    }

    // Value text
    const fsize = Math.max(16, w * 0.16);
    ctx.fillStyle = '#CFFFEA';
    ctx.font = `700 ${fsize}px "JetBrains Mono", monospace`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(Math.round(pct) + '%', cx, cy - r * 0.15);

    if (opts.label) {
      ctx.fillStyle = 'rgba(91,207,166,0.65)';
      ctx.font = `500 ${Math.max(9, fsize * 0.5)}px "JetBrains Mono", monospace`;
      ctx.fillText(opts.label, cx, cy + fsize * 0.6);
    }
  }

  window.HEALTH_CHARTS = { drawRing, drawBars, drawSparkline, drawBottle, drawGauge };
})();
