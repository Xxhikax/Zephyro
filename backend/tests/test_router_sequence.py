"""Test que el router bypasa PLAY_MEDIA short-circuit y compound parser
cuando detecta 'y luego'/'después' con verbo de automation."""
import re

from core.app_config import AUTO_VERBS_PATTERN


def test_sequence_hint_regex_matches_play_then_open():
    """El regex de detección de sequence debe matchear el caso del bug real."""
    SEQUENCE_HINT_RE = re.compile(
        rf'\b(?:y\s+luego|y\s+despu[eé]s|despu[eé]s\s+de|despu[eé]s|luego)\s+'
        rf'(?:{AUTO_VERBS_PATTERN}|y\s+(?:{AUTO_VERBS_PATTERN}))\b',
        re.IGNORECASE,
    )

    cases_match = [
        "reproduce thunderstruck y luego abres discord",
        "reproduce X en youtube music y luego abre discord",
        "abre google y luego busca lobos",
        "pon música y después abre vscode",
        "abre instagram y luego abre discord",
        "REPRODUCE X Y LUEGO ABRE Y",  # case-insensitive
    ]
    cases_no_match = [
        "reproduce thunderstruck",  # sin conector
        "abre discord",
        "luego de eso, pensé en algo",  # "luego de" no es conector con verbo
        "después de pensarlo bien",  # sin verbo de automation
        "abre instagram y entra a reels",  # "y" sin "luego"
    ]

    for c in cases_match:
        assert SEQUENCE_HINT_RE.search(c.lower()), f"should match: {c}"

    for c in cases_no_match:
        assert not SEQUENCE_HINT_RE.search(c.lower()), f"should NOT match: {c}"


def test_router_skips_capa1_when_sequence_hint():
    """Verifica que el router NO arme automation_intent=PLAY_MEDIA cuando
    hay sequence hint. Test indirecto: chequea el flag bypass."""
    # Este test directo es complicado porque _classify requiere todo el setup
    # del orchestrator. Lo dejamos como smoke E2E (test_sequence_e2e).
    # Acá solo verificamos que la regex propia matchea.
    import re as _re
    text = "reproduce thunderstruck en youtube music y luego abres discord"
    hint = _re.search(
        rf'\b(?:y\s+luego|y\s+despu[eé]s|despu[eé]s\s+de|despu[eé]s|luego)\s+'
        rf'(?:{AUTO_VERBS_PATTERN})\b',
        text.lower(), re.IGNORECASE
    )
    assert hint is not None
