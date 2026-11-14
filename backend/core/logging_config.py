"""Structured logging configuration for Zephyro."""
import logging
import logging.handlers
import re
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import Optional

request_id_var: ContextVar[str] = ContextVar("request_id", default="")

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s (%(filename)s:%(lineno)d)"
_LOG_DATEFMT = "%H:%M:%S"

# Rotación: 50 MB por archivo, 5 backups (~250 MB total por handler)
_ROTATION_MAX_BYTES = 50 * 1024 * 1024
_ROTATION_BACKUP_COUNT = 5

# Patrones de secrets a redactar en logs (P3 security fix)
_REDACTERS: list[tuple[re.Pattern, str]] = [
    # Telegram bot tokens: bot<numbers>:<alphanumeric-hyphen>
    (re.compile(r'bot\d+:[\w\-]{20,}'), 'bot<REDACTED>:<REDACTED>'),
    # API keys — claves típicas de 30+ caracteres alfanuméricos tras "key=" o similar
    (re.compile(r'(api[_-]?key|apikey|token|secret)[:=]\s*[\w\-]{20,}', re.IGNORECASE), r'\1=<REDACTED>'),
    # Authorization: Bearer tokens
    (re.compile(r'Bearer\s+[\w\-.]+'), 'Bearer <REDACTED>'),
    # Bare provider keys (Anthropic/OpenAI/DeepSeek "sk-…", Tavily "tvly-…")
    (re.compile(r'\b(sk-(?:ant-)?|tvly-)[\w\-]{16,}'), r'\1<REDACTED>'),
]
# Fast-reject: only run regex when one of these substrings is present
_REDACT_FAST_CHECK = ("bot", "key", "token", "secret", "bearer", "api", "sk-", "tvly-")

# Third-party loggers whose INFO lines carry full request URLs (the Telegram bot
# token travels in the URL path: .../bot<token>/getUpdates every ~10s).
_NOISY_URL_LOGGERS = ("httpx", "httpcore")


def _redact_str(s: str) -> str:
    """Apply all redaction patterns to a single string."""
    for pattern, replacement in _REDACTERS:
        s = pattern.sub(replacement, s)
    return s


class _SecretRedactionFilter(logging.Filter):
    """Redacts secrets from the fully formatted message.

    Must be attached to HANDLERS: a logger-level filter only sees records logged on
    that exact logger, never ones propagated from children (httpx, telegram…).
    Works on getMessage() because args may be non-str objects (httpx passes URL objects).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if any(t in msg.lower() for t in _REDACT_FAST_CHECK):
            redacted = _redact_str(msg)
            if redacted != msg:
                record.msg = redacted
                record.args = None
        return True


_REDACTION_FILTER = _SecretRedactionFilter()


def _install_redaction(handler: logging.Handler) -> None:
    if _REDACTION_FILTER not in handler.filters:
        handler.addFilter(_REDACTION_FILTER)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def configure_logging(level: str = "INFO", log_file: Optional[str] = None) -> None:
    """Configura logging del backend con rotación de archivos.

    Args:
        level: Nivel de logging (INFO, DEBUG, WARNING, ERROR).
        log_file: Ruta opcional al archivo de log. Si se provee, se agrega un
                  RotatingFileHandler al logger raíz además de la salida a consola.
                  El directorio se crea automáticamente si no existe.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        level=log_level,
        format=_LOG_FORMAT,
        datefmt=_LOG_DATEFMT,
    )

    root = logging.getLogger()
    for handler in root.handlers:
        _install_redaction(handler)
    for name in _NOISY_URL_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    # Windows console defaults to cp1252: non-ASCII chars in log messages
    # (e.g. "→") raise UnicodeEncodeError inside the console StreamHandler.
    # Reconfigure its stream to UTF-8 with replacement so logging never crashes.
    for handler in root.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            stream = getattr(handler, "stream", None)
            if hasattr(stream, "reconfigure"):
                try:
                    stream.reconfigure(encoding="utf-8", errors="replace")
                except (ValueError, OSError):
                    pass

    if log_file:
        _attach_rotating_file_handler(log_file, log_level)


def _attach_rotating_file_handler(log_file: str, level: int) -> None:
    """Agrega un RotatingFileHandler al root logger si no está ya."""
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    existing_paths = {
        getattr(h, "baseFilename", None)
        for h in root.handlers
        if isinstance(h, logging.FileHandler)
    }
    if str(log_path.resolve()) in existing_paths:
        return

    fh = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=_ROTATION_MAX_BYTES,
        backupCount=_ROTATION_BACKUP_COUNT,
        encoding="utf-8",
    )
    fh.setLevel(level)
    fh.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
    _install_redaction(fh)
    root.addHandler(fh)
