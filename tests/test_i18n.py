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
    assert i18n.features("ru", "premium")[0] == "Всё из Pro"


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


def test_no_key_is_left_untranslated():
    """All three slots identical AND containing Cyrillic is the signature of a
    key that was added in Ukrainian and never translated — a plain-Latin or
    emoji-only string repeating across languages is legitimate."""
    import re

    cyrillic = re.compile(r"[а-яїієґА-ЯЇІЄҐ]")
    # Language names are deliberately identical everywhere: a language picker
    # is only useful if each option reads in its OWN language, whatever the
    # UI is currently set to.
    endonyms = {"lang_uk", "lang_ru", "lang_en"}
    untranslated = [
        key
        for key, (uk, ru, en) in i18n._TR.items()
        if key not in endonyms and uk == ru == en and cyrillic.search(uk)
    ]
    assert untranslated == [], f"same Cyrillic text in all 3 languages: {untranslated}"


def test_format_placeholders_match_across_languages():
    """A `{name}` slot present in one language but missing in another either
    silently drops information or raises KeyError at call time — neither is
    visible until a user in that language hits the message."""
    import re

    slots = re.compile(r"\{(\w+)\}")
    mismatched = {
        key: [sorted(set(slots.findall(s))) for s in row]
        for key, row in i18n._TR.items()
        if len({frozenset(slots.findall(s)) for s in row}) > 1
    }
    assert mismatched == {}, f"placeholder sets differ between languages: {mismatched}"


def test_new_miniapp_and_info_keys_are_translated():
    assert i18n.t("en", "wa_code_title") == "Enter the code"
    assert i18n.t("ru", "wa_done_title") == "Аккаунт подключён"
    assert i18n.t("en", "ub_info_no_username") == "🔗 no username"
    assert i18n.t("ru", "adm_stopped").startswith("🛑")


def test_every_plan_has_localized_feature_bullets():
    """The bullets shown on a tariff card come from here alone. They used to be
    duplicated as an untranslated list on TariffPlan that nothing read — two
    lists to keep in sync, one of them invisible."""
    from shared.tariffs import PLANS, get_plan

    for tariff in PLANS:
        plan_id = get_plan(tariff).id
        for lang in ("uk", "ru", "en"):
            bullets = i18n.features(lang, plan_id)
            assert bullets, f"{plan_id}/{lang} has no feature bullets"
        uk, en = i18n.features("uk", plan_id), i18n.features("en", plan_id)
        assert uk != en, f"{plan_id} feature bullets look untranslated"


def test_tariff_plan_carries_no_display_strings():
    """Guards the drift that was just removed: plan data is numbers and flags,
    the words live here."""
    from shared.tariffs import get_plan

    plan = get_plan("pro")
    assert not hasattr(plan, "features")
