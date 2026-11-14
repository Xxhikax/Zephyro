"""
Chart renderer — convierte specs declarativos ```chart en PNG base64.

El LLM (solo tier cloud, prompt en context_manager) emite un bloque:

    ```chart
    {"type": "bar", "title": "Población", "labels": ["1970", "1980"],
     "series": [{"label": "Argentina", "values": [23.9, 28.1]}],
     "ylabel": "millones"}
    ```

`replace_chart_blocks()` (chokepoint del orchestrator, post sanitize_response)
lo reemplaza por `![gráfico](data:image/png;base64,...)` que RichRender dibuja
como <img>. El spec es JSON declarativo puro — NUNCA se evalúa código.

Tema oscuro phosphor alineado con la UI (src/style.css :root matrix).
"""
from __future__ import annotations

import base64
import io
import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

_ALLOWED_TYPES = frozenset({"line", "bar", "pie", "scatter"})
_MAX_SERIES = 10
_MAX_POINTS = 500

# Paleta phosphor (accent primero; el resto separables en fondo oscuro)
_BG = "#111917"
_FG = "#CFFFEA"
_GRID = "#1B2A25"
_CYCLE = ["#00FF88", "#19FFA2", "#FFC857", "#FF4B5C", "#5BCFA6", "#8B92B0"]

_RE_CHART_BLOCK = re.compile(r"```chart\s*\n(.*?)```", flags=re.DOTALL)

_INVALID_CHART_PLACEHOLDER = "(gráfico inválido)"


def _validate_spec(spec: Any) -> Optional[dict]:
    """Devuelve el spec normalizado o None si no cumple el contrato."""
    if not isinstance(spec, dict):
        return None
    chart_type = spec.get("type")
    if chart_type not in _ALLOWED_TYPES:
        return None
    series = spec.get("series")
    if not isinstance(series, list) or not series or len(series) > _MAX_SERIES:
        return None
    total_points = 0
    for s in series:
        if not isinstance(s, dict):
            return None
        values = s.get("values")
        if not isinstance(values, list) or not values or len(values) > _MAX_POINTS:
            return None
        for v in values:
            if v is not None and not isinstance(v, (int, float)):
                return None
            if isinstance(v, bool):
                return None
        total_points += len(values)
    if total_points > _MAX_POINTS:
        return None
    labels = spec.get("labels")
    if labels is not None and not isinstance(labels, list):
        return None
    return spec


def render_chart_spec(spec: dict) -> Optional[str]:
    """
    Renderiza un spec validado a un data URI PNG. Devuelve None ante cualquier
    problema (spec inválido, matplotlib ausente, error de render) — el caller
    decide el fallback textual.
    """
    spec = _validate_spec(spec)
    if spec is None:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:
        logger.warning("[charts] matplotlib no disponible: %s", exc)
        return None

    fig = None
    try:
        chart_type = spec["type"]
        series = spec["series"]
        labels = spec.get("labels")

        fig, ax = plt.subplots(figsize=(6.4, 3.6), dpi=110)
        fig.patch.set_facecolor(_BG)
        ax.set_facecolor(_BG)

        if chart_type == "pie":
            first = series[0]
            values = [v if v is not None else 0 for v in first["values"]]
            pie_labels = labels if labels and len(labels) == len(values) else None
            ax.pie(
                values, labels=pie_labels, colors=_CYCLE,
                textprops={"color": _FG, "fontsize": 9},
                wedgeprops={"edgecolor": _BG, "linewidth": 1},
            )
        else:
            for i, s in enumerate(series):
                values = s["values"]
                x = labels if labels and len(labels) == len(values) else list(range(len(values)))
                color = _CYCLE[i % len(_CYCLE)]
                name = str(s.get("label", f"serie {i + 1}"))
                if chart_type == "line":
                    ax.plot(x, values, color=color, label=name, linewidth=1.8)
                elif chart_type == "scatter":
                    ax.scatter(x, values, color=color, label=name, s=22)
                else:  # bar
                    offset = (i - (len(series) - 1) / 2) * 0.8 / len(series)
                    positions = [j + offset for j in range(len(values))]
                    ax.bar(positions, [v if v is not None else 0 for v in values],
                           width=0.8 / len(series), color=color, label=name)
            if chart_type == "bar" and labels:
                ax.set_xticks(range(len(labels)))
                ax.set_xticklabels([str(l) for l in labels])
            if len(series) > 1 or series[0].get("label"):
                leg = ax.legend(facecolor=_BG, edgecolor=_GRID, labelcolor=_FG, fontsize=8)
                leg.get_frame().set_alpha(0.9)
            ax.grid(True, color=_GRID, linewidth=0.6, alpha=0.7)

        for spine in ax.spines.values():
            spine.set_color(_GRID)
        ax.tick_params(colors=_FG, labelsize=8)
        if spec.get("title"):
            ax.set_title(str(spec["title"]), color=_FG, fontsize=11)
        if spec.get("xlabel"):
            ax.set_xlabel(str(spec["xlabel"]), color=_FG, fontsize=9)
        if spec.get("ylabel"):
            ax.set_ylabel(str(spec["ylabel"]), color=_FG, fontsize=9)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight", facecolor=_BG)
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except Exception as exc:
        logger.warning("[charts] render falló: %s", exc)
        return None
    finally:
        if fig is not None:
            try:
                plt.close(fig)
            except Exception:
                pass


def replace_chart_blocks(text: str) -> str:
    """
    Reemplaza cada bloque ```chart {json}``` por una imagen Markdown con el
    PNG en data URI. Ante JSON inválido o render fallido deja un placeholder
    corto — nunca JSON crudo en pantalla. Síncrono y CPU-bound: llamar vía
    asyncio.to_thread desde el orchestrator.
    """
    if not text or "```chart" not in text:
        return text

    def _repl(mo: "re.Match[str]") -> str:
        try:
            spec = json.loads(mo.group(1))
        except (json.JSONDecodeError, TypeError):
            return _INVALID_CHART_PLACEHOLDER
        data_uri = render_chart_spec(spec)
        if data_uri is None:
            return _INVALID_CHART_PLACEHOLDER
        title = spec.get("title") if isinstance(spec, dict) else None
        alt = str(title) if title else "gráfico"
        return f"![{alt}]({data_uri})"

    return _RE_CHART_BLOCK.sub(_repl, text)
