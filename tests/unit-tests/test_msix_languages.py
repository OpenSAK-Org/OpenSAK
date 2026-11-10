"""
tests/unit-tests/test_msix_languages.py — the MSIX manifest declares every
UI language, so the Microsoft Store lists them all under "Supported
languages" (not only "English (United States)").

The Store reads that list from <Resources> in the package manifest, not from
the Store listing texts, so the manifest has to follow AVAILABLE_LANGUAGES
whenever a language is added.
"""

import xml.etree.ElementTree as ET
from pathlib import Path

from opensak.lang import AVAILABLE_LANGUAGES

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "packaging" / "msix" / "AppxManifest.xml.template"
NS = {"m": "http://schemas.microsoft.com/appx/manifest/foundation/windows10"}

# OpenSAK language code -> BCP-47 tag used in the manifest.
EXPECTED_TAGS = {
    "da": "da-dk",
    "en": "en-us",
    "fr": "fr-fr",
    "nl": "nl-nl",
    "pt": "pt-pt",
    "cs": "cs-cz",
    "se": "sv-se",  # "se" is Northern Sami in BCP-47; OpenSAK uses it for Swedish
    "de": "de-de",
    "de_CH": "de-ch",
    "pl": "pl-pl",
    "es": "es-es",
}


def _manifest_languages() -> list[str]:
    root = ET.parse(TEMPLATE).getroot()
    return [
        r.get("Language", "").lower()
        for r in root.findall("m:Resources/m:Resource", NS)
    ]


def test_every_ui_language_has_a_manifest_tag_mapping():
    missing = set(AVAILABLE_LANGUAGES) - set(EXPECTED_TAGS)
    assert not missing, (
        f"New UI language(s) {sorted(missing)}: add a BCP-47 tag to "
        "EXPECTED_TAGS here and a <Resource> to AppxManifest.xml.template"
    )


def test_manifest_declares_every_ui_language():
    declared = _manifest_languages()
    expected = {EXPECTED_TAGS[code] for code in AVAILABLE_LANGUAGES}
    assert set(declared) == expected
    assert len(declared) == len(set(declared)), "duplicate <Resource> entries"


def test_english_is_the_default_manifest_language():
    assert _manifest_languages()[0] == "en-us"
