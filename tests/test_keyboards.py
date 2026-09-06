"""Keyboard builders and code masking."""

from management_bot import keyboards


def test_masked_code():
    assert keyboards.masked_code("") == "_____"
    assert keyboards.masked_code("12") == "12___"
    assert keyboards.masked_code("12345") == "12345"
    assert keyboards.masked_code("1234567") == "12345"  # never over CODE_LENGTH


def test_code_keyboard_layout():
    kb = keyboards.code_keyboard()
    rows = kb.inline_keyboard
    assert len(rows) == 5  # 1-9 in three rows + control row + SMS-resend row

    labels = [btn.text for row in rows for btn in row]
    for digit in "123456789":
        assert digit in labels
    assert "0" in labels
    assert "⬅️" in labels
    assert "✅" in labels

    # Digit callbacks carry the right prefix; controls are distinct.
    all_cb = [btn.callback_data for row in rows for btn in row]
    assert f"{keyboards.CB_DIGIT}7" in all_cb
    assert keyboards.CB_BACKSPACE in all_cb
    assert keyboards.CB_SUBMIT in all_cb
    assert keyboards.CB_RESEND_SMS in all_cb


def test_phone_request_keyboard():
    kb = keyboards.phone_request_keyboard()
    button = kb.keyboard[0][0]
    assert button.request_contact is True


# --- the feature switches grid ---------------------------------------------


def _labels(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


def test_every_switch_in_the_schema_has_a_button():
    """A field with no button can only be changed by editing the database —
    which for a user-facing switch means it does not exist."""
    from management_bot.handlers.settings import _FEATURE_ROWS
    from shared.settings_schema import Features

    on_screen = {field for row in _FEATURE_ROWS for field, _ in row}
    assert on_screen == set(Features._FIELDS)


def test_the_send_switch_is_hidden_on_a_plan_without_send():
    """A switch for something the plan does not grant is misleading either
    way round: off looks like the reason it does not work."""
    from management_bot.handlers.settings import _features_menu
    from shared.i18n import t
    from shared.settings_schema import Features

    standard = _labels(_features_menu(Features(), "uk", tariff="standard"))
    assert not any(t("uk", "feat_send") in label for label in standard)

    pro = _labels(_features_menu(Features(), "uk", tariff="pro"))
    assert any(t("uk", "feat_send") in label for label in pro)


def test_switches_show_their_state():
    from management_bot.handlers.settings import _features_menu
    from shared.i18n import t
    from shared.settings_schema import Features

    labels = _labels(_features_menu(Features(viewonce_video=False), "uk", tariff="pro"))
    video = next(l for l in labels if t("uk", "feat_viewonce_video") in l)
    assert video.startswith("❌")


def test_the_settings_menu_gives_long_labels_their_own_row():
    """Three short labels fit across the top; the longer names were being
    truncated into "Автовідповід…" when they shared a row."""
    from management_bot.handlers.settings import _main_menu

    rows = [[b.text for b in row] for row in _main_menu(True, "uk").inline_keyboard]
    assert len(rows[0]) == 3
    assert all(len(row) == 1 for row in rows[1:]), "one per row below the first"


def test_the_account_button_follows_whether_one_is_connected():
    from management_bot.handlers.settings import _main_menu
    from shared.i18n import t

    connected = _main_menu(True, "uk").inline_keyboard[-1][0]
    assert connected.text == t("uk", "set_btn_unlink")

    empty = _main_menu(False, "uk").inline_keyboard[-1][0]
    assert empty.text == t("uk", "set_btn_link")
