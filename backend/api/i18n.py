"""Translated content fields (G§5.8): `<name>_i18n = JSONField(default=dict)` holding {"ka": ..., "en": ...}."""

from django.conf import settings
from django.utils.translation import get_language


def tr(value: dict | None, lang: str | None = None) -> str:
    """Pick the active (or given) language, fall back to the default language, then to any non-empty value."""
    if not value:
        return ""
    lang = (lang or get_language() or settings.LANGUAGE_CODE).split("-")[0]
    return value.get(lang) or value.get(settings.LANGUAGE_CODE) or next((v for v in value.values() if v), "")
