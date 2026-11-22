# SPDX-License-Identifier: GPL-3.0-or-later
"""Bilingual string table for the GOO NPR add-on.

Modules keep their own strings close to the code and register them with
:func:`register`.  This keeps translations maintainable and avoids one giant
central dictionary that every contributor has to touch.

The active language is resolved from (in order):

1. the add-on preference ``language`` (``AUTO`` follows Blender's UI language),
2. Blender's ``preferences.view.language``,
3. English.
"""

from __future__ import annotations

import bpy

DEFAULT_LANGUAGE = "en_US"
SUPPORTED_LANGUAGES = ("en_US", "zh_CN")

#: key -> {language: text}
_STRINGS: dict[str, dict[str, str]] = {}

#: Resolved language cache, invalidated by :func:`invalidate`.
_resolved: str | None = None


def register(strings: dict[str, dict[str, str]]) -> None:
    """Merge a module level string table into the global table.

    ``strings`` maps a message key to a mapping of language code to text::

        register({"goo.my_key": {"en_US": "Hello", "zh_CN": "你好"}})
    """
    for key, translations in strings.items():
        bucket = _STRINGS.setdefault(key, {})
        bucket.update(translations)


def _pref_language() -> str | None:
    try:
        prefs = bpy.context.preferences.addons[__package__.split(".")[0]].preferences
    except (AttributeError, KeyError):
        return None
    lang = getattr(prefs, "language", "AUTO")
    if lang and lang != "AUTO":
        return lang
    return None


def current_language() -> str:
    """Return the language code used for UI strings."""
    global _resolved
    if _resolved:
        return _resolved
    lang = _pref_language()
    if lang is None:
        try:
            ui_lang = str(bpy.context.preferences.view.language)
        except AttributeError:
            ui_lang = DEFAULT_LANGUAGE
        lang = "zh_CN" if ui_lang.lower().startswith("zh") else DEFAULT_LANGUAGE
    _resolved = lang if lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
    return _resolved


def invalidate() -> None:
    """Drop the cached language, e.g. after the user changed preferences."""
    global _resolved
    _resolved = None


def t(key: str, **kwargs) -> str:
    """Translate ``key`` for the current language and format it.

    Unknown keys fall back to the key itself so missing strings are visible but
    never break the UI.
    """
    translations = _STRINGS.get(key)
    if not translations:
        text = key
    else:
        text = translations.get(current_language()) or translations.get(DEFAULT_LANGUAGE) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def bilingual(key: str) -> str:
    """Return ``english / 中文`` for tooltips where both languages are wanted."""
    translations = _STRINGS.get(key) or {}
    en = translations.get("en_US")
    zh = translations.get("zh_CN")
    if en and zh and en != zh:
        return f"{en} / {zh}"
    return en or zh or key


def available_keys() -> list[str]:
    """Return every registered message key (used by tests)."""
    return sorted(_STRINGS)
