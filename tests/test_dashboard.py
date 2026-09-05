"""/start greeting + metrics builder and the main-menu keyboard (localized)."""

from datetime import datetime, timezone

from management_bot import dashboard, keyboards
from management_bot.storage import SessionInfo, SubscriptionInfo, UserStatus
from shared.i18n import t

NOW = datetime(2026, 8, 22, 14, 30, tzinfo=timezone.utc)


def test_start_text_no_subscription_uk():
    status = UserStatus(known=True, subscription=None, sessions=[])
    text = dashboard.build_start_text(status, NOW, "uk")
    assert "User Agent Bot" in text
    assert "22.08.2026" in text
    assert "14:30" not in text  # time removed from the dashboard
    assert "немає" in text
    assert "не підключено" in text


def test_start_text_localized_en():
    status = UserStatus(known=True, subscription=None, sessions=[])
    text = dashboard.build_start_text(status, NOW, "en")
    assert "none" in text
    assert "not connected" in text


def test_start_text_with_subscription_and_sessions():
    status = UserStatus(
        known=True,
        subscription=SubscriptionInfo(tariff="pro", status="active", expires_at=datetime(2026, 12, 31)),
        sessions=[SessionInfo(phone_number="+15551234567", last_used_at=None)],
    )
    text = dashboard.build_start_text(status, NOW, "uk")
    assert "Pro" in text  # tariff prettified via plan title
    assert "до 31.12.2026" in text
    assert "✅ підключено" in text


def test_main_menu_layout():
    """Menu spans the top row on its own; the other four pair up below it."""
    rows = [[b.text for b in row] for row in keyboards.main_menu("uk").keyboard]
    assert rows == [
        [t("uk", "btn_menu")],
        [t("uk", "btn_subscribe"), t("uk", "btn_help")],
        [t("uk", "btn_support"), t("uk", "btn_settings")],
    ]


def test_main_menu_has_no_connect_button():
    # account link/unlink lives inside Settings as an inline button, not here
    labels = [b.text for row in keyboards.main_menu("uk").keyboard for b in row]
    assert not any("Підключити" in label for label in labels)
    assert not any("Прив" in label for label in labels)


def _sub(state: str, expires=datetime(2026, 10, 5, tzinfo=timezone.utc)) -> UserStatus:
    return UserStatus(
        known=True,
        subscription=SubscriptionInfo(tariff="pro", status=state, expires_at=expires),
        sessions=[],
    )


def test_subscription_state_is_translated_not_a_raw_database_value():
    """It reached the screen as the stored English word in every language."""
    assert "активна" in dashboard.build_start_text(_sub("active"), NOW, "uk")
    assert "активна" in dashboard.build_start_text(_sub("active"), NOW, "ru")
    assert "active" in dashboard.build_start_text(_sub("active"), NOW, "en")
    assert "active" not in dashboard.build_start_text(_sub("active"), NOW, "uk")


def test_a_lapsed_subscription_reads_as_ended_not_as_a_future_date():
    """"до 05.10.2026" on a period that already ended reads as a promise, and
    the word must appear once, not on both sides of the bracket."""
    text = dashboard.build_start_text(_sub("expired"), NOW, "uk")
    assert "(завершилась 05.10.2026)" in text
    assert "до 05.10.2026" not in text
    assert text.count("завершилась") == 1


def test_pending_and_cancelled_states_have_words_too():
    assert "очікує оплати" in dashboard.build_start_text(_sub("pending"), NOW, "uk")
    assert "скасована" in dashboard.build_start_text(_sub("cancelled"), NOW, "uk")


def test_an_unknown_state_is_shown_rather_than_swallowed():
    """If the enum grows, printing the raw value is a louder failure than a
    blank space where the state should be."""
    assert "something_new" in dashboard.build_start_text(_sub("something_new"), NOW, "uk")
