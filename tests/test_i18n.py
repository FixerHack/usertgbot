"""i18n language resolution + translation lookup + completeness."""

import pytest

from shared import i18n


@pytest.mark.parametrize(
    "code,expected",
    [
        ("uk", "uk"), ("ru", "ru"), ("en", "en"),
        ("en-US", "en"), ("ru-RU", "ru"), ("uk-UA", "uk"),
        ("de", "en"), ("fr", "en"), ("es-ES", "en"),  # unsupported -> en
        (None, "uk"), ("", "uk"),                       # missing -> default uk
    ],
)
def test_resolve_lang(code, expected):
    assert i18n.resolve_lang(code) == expected


def test_translation_lookup():
    assert i18n.t("uk", "btn_subscribe") == "💳 Тарифи"
    assert i18n.t("ru", "btn_subscribe") == "💳 Тарифы"
    assert i18n.t("en", "btn_subscribe") == "💳 Plans"


def test_translation_formatting():
    assert i18n.t("en", "support_text", contact="@bob") == "🆘 <b>Support</b>\n\nContact — @bob"


def test_unknown_key_returns_key():
    assert i18n.t("uk", "does_not_exist") == "does_not_exist"


def test_variants_covers_all_languages():
    v = i18n.variants("btn_settings")
    assert "⚙️ Налаштування" in v and "⚙️ Настройки" in v and "⚙️ Settings" in v


def test_features_localized():
    assert i18n.features("uk", "pro")[0] == "Усе зі Standard"
    assert i18n.features("en", "pro")[0] == "Everything in Standard"
    assert i18n.features("ru", "premium") == ["🚧 В разработке"]


def test_every_key_has_all_three_languages():
    for key, row in i18n._TR.items():
        assert len(row) == 3, f"{key} must have (uk, ru, en)"
        assert all(isinstance(s, str) and s for s in row), f"{key} has an empty/non-str translation"


def test_lang_of_reads_from_user():
    class _U:
        language_code = "ru"

    class _Ev:
        from_user = _U()

    assert i18n.lang_of(_Ev()) == "ru"
