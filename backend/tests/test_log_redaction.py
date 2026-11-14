"""Secrets never reach the log output, including records from child loggers."""
import io
import logging

import pytest

from core import logging_config as lc

TOKEN = "bot1234567890:FAKEtokenFAKEtokenFAKEtokenFAKE00"


class _FakeURL:
    """httpx passes URL objects (not str) as log args."""

    def __init__(self, url):
        self._url = url

    def __str__(self):
        return self._url


@pytest.fixture
def capture():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    lc._install_redaction(handler)
    logger = logging.getLogger("zephyro.test.redaction.child")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    yield logger, stream
    logger.removeHandler(handler)


def test_bot_token_in_url_object_arg_is_redacted(capture):
    logger, stream = capture
    url = _FakeURL(f"https://api.telegram.org/{TOKEN}/getUpdates")
    logger.info('HTTP Request: %s %s "%s"', "POST", url, "HTTP/1.1 200 OK")
    out = stream.getvalue()
    assert "FAKEtoken" not in out
    assert "bot<REDACTED>" in out
    assert "getUpdates" in out


@pytest.mark.parametrize("secret", [
    "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123",
    "sk-abcdefghijklmnopqrstuvwx",
    "tvly-abcdefghijklmnopqrstuv",
])
def test_bare_provider_keys_are_redacted(capture, secret):
    logger, stream = capture
    logger.info("using %s now", secret)
    out = stream.getvalue()
    assert secret not in out
    assert "<REDACTED>" in out


def test_clean_message_is_untouched(capture):
    logger, stream = capture
    logger.info("hola %s, todo bien", "Bruno")
    assert stream.getvalue().strip() == "hola Bruno, todo bien"


def test_configure_logging_filters_handlers_and_quiets_httpx(tmp_path):
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        lc.configure_logging("INFO", str(tmp_path / "backend.log"))
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING
        assert root.handlers
        assert all(lc._REDACTION_FILTER in h.filters for h in root.handlers)
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()
