"""Pure userbot formatting/decision helpers."""

from shared.settings_schema import LinkButton, MeCard
from userbot import formatting


def test_format_user_info():
    info = formatting.TargetInfo(
        entity_id=123,
        is_chat=False,
        title_or_name="Bob Ross",
        username="bob",
        is_premium=True,
    )
    text = formatting.format_target_info(info)
    assert "Bob Ross" in text
    assert "123" in text
    assert "@bob" in text
    assert "⭐️" in text  # premium badge + label


def test_format_user_info_no_username():
    info = formatting.TargetInfo(entity_id=1, is_chat=False, title_or_name="X")
    assert "без юзернейму" in formatting.format_target_info(info)


def test_format_chat_info():
    info = formatting.TargetInfo(
        entity_id=-100, is_chat=True, title_or_name="My Group", members_count=42
    )
    text = formatting.format_target_info(info)
    assert "My Group" in text
    assert "42" in text


def test_format_info_escapes_html():
    info = formatting.TargetInfo(entity_id=1, is_chat=False, title_or_name="<script>")
    assert "<script>" not in formatting.format_target_info(info)
    assert "&lt;script&gt;" in formatting.format_target_info(info)


def test_build_me_card_folds_link():
    card = MeCard(text="Hi", link="https://t.me/x")
    text, buttons = formatting.build_me_card(card)
    assert "Hi" in text and "https://t.me/x" in text
    assert buttons == []


def test_format_link_buttons():
    buttons = [LinkButton(text="IG", url="https://instagram.com/x")]
    out = formatting.format_link_buttons(buttons)
    assert 'href="https://instagram.com/x"' in out
    assert ">IG<" in out


def test_ban_action():
    assert formatting.ban_action(is_private=True) == "ban"
    assert formatting.ban_action(is_private=False) == "leave"


def test_entity_ref():
    assert formatting.entity_ref(42, username="bob") == "@bob"
    assert 'tg://user?id=42' in formatting.entity_ref(42, name="Bob Ross")
    assert formatting.entity_ref(42) == "<code>42</code>"
    assert formatting.entity_ref(None) == "невідомо"


def test_notification_bodies():
    who = formatting.entity_ref(42, username="bob")
    chat = formatting.entity_ref(555, name="Group")

    deleted = formatting.format_deleted_notice(chat, who, "bye")
    assert "Видалене" in deleted and "@bob" in deleted and "bye" in deleted

    edited = formatting.format_edited_notice(chat, who, "old", "new")
    assert "old" in edited and "new" in edited

    ar = formatting.format_autoresponder_notice(who, "hi there")
    assert "Автовідповідач" in ar and "hi there" in ar

    vo = formatting.format_view_once_notice(who, "voice")
    assert "Одноразове" in vo and "голосове" in vo


def test_deleted_notice_escapes_html():
    out = formatting.format_deleted_notice("<code>1</code>", "<code>2</code>", "<b>x</b>")
    assert "<b>x</b>" not in out
    assert "&lt;b&gt;" in out


def test_info_includes_phone():
    info = formatting.TargetInfo(
        entity_id=1, is_chat=False, title_or_name="X", phone="380501234567"
    )
    assert "+380501234567" in formatting.format_target_info(info)
