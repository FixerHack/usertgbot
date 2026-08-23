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
    assert len(rows) == 4  # 1-9 in three rows + control row

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


def test_phone_request_keyboard():
    kb = keyboards.phone_request_keyboard()
    button = kb.keyboard[0][0]
    assert button.request_contact is True
