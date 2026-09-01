"""Pure formatting/decision helpers for userbot commands.

No Telethon calls here — the handlers gather raw fields and pass them in, so
this stays unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.i18n import t as _i18n_t
from shared.settings_schema import LinkButton, MeCard


def _t(key: str, lang: str = "uk", **kwargs) -> str:
    return _i18n_t(lang, key, **kwargs)


@dataclass
class TargetInfo:
    """Normalized snapshot of a user or chat, ready to render."""

    entity_id: int
    is_chat: bool
    title_or_name: str
    username: str | None = None
    is_premium: bool = False
    is_verified: bool = False
    is_bot: bool = False
    members_count: int | None = None
    bio: str | None = None
    phone: str | None = None


def format_target_info(info: TargetInfo, lang: str = "uk") -> str:
    """Short, human summary for `.info` (works for both a user and a chat)."""
    lines: list[str] = []
    if info.is_chat:
        lines.append(f"💬 <b>{_esc(info.title_or_name)}</b>")
        lines.append(f"🆔 <code>{info.entity_id}</code>")
        if info.username:
            lines.append(f"🔗 @{info.username}")
        if info.members_count is not None:
            lines.append(_t("ub_info_members", lang, count=info.members_count))
    else:
        badges = "".join(
            b for b, on in (("⭐️", info.is_premium), ("✅", info.is_verified), ("🤖", info.is_bot)) if on
        )
        name = _esc(info.title_or_name) + (f" {badges}" if badges else "")
        lines.append(f"👤 <b>{name}</b>")
        lines.append(f"🆔 <code>{info.entity_id}</code>")
        lines.append(f"🔗 @{info.username}" if info.username else _t("ub_info_no_username", lang))
        if info.phone:
            lines.append(f"📱 +{info.phone.lstrip('+')}")
        lines.append(_t("ub_info_premium" if info.is_premium else "ub_info_no_premium", lang))
        if info.bio:
            lines.append(f"📝 {_esc(info.bio)}")
    return "\n".join(lines)


def build_me_card(card: MeCard) -> tuple[str, list[LinkButton]]:
    """Render a `.me` card into (text, buttons). Caller attaches the photo."""
    text = _esc(card.text) if card.text else ""
    if card.link:
        link = f'🔗 <a href="{_esc_attr(card.link)}">{_esc(card.link)}</a>'
        text = f"{text}\n{link}" if text else link
    return text.strip(), list(card.buttons)


def format_link_buttons(buttons: list[LinkButton]) -> str:
    """Userbots can't attach inline keyboards, so render link 'buttons' as
    HTML hyperlinks appended to the message body."""
    return "\n".join(f"🔗 <a href=\"{_esc_attr(b.url)}\">{_esc(b.text)}</a>" for b in buttons)


def ban_action(is_private: bool) -> str:
    """`.ban` in a DM removes the user both sides; in a group we just leave."""
    return "ban" if is_private else "leave"


# --- manager-bot notification bodies --------------------------------------


def entity_ref(
    entity_id: int | None, username: str | None = None, name: str | None = None, lang: str = "uk"
) -> str:
    """Clickable reference: @username (auto-links), else a name link to the
    user, else the bare id."""
    if username:
        return f"@{username}"
    if name:
        return f'<a href="tg://user?id={entity_id}">{_esc(name)}</a>'
    return f"<code>{entity_id}</code>" if entity_id else _t("unknown", lang)


def format_deleted_notice(chat_ref: str, sender_ref: str, text: str, lang: str = "uk") -> str:
    return (
        f"{_t('note_deleted', lang)}\n{_t('note_chat', lang)}: {chat_ref}\n"
        f"{_t('note_from', lang)}: {sender_ref}\n\n{_esc(text)}"
    )


def format_edited_notice(chat_ref: str, sender_ref: str, previous: str, new: str, lang: str = "uk") -> str:
    return (
        f"{_t('note_edited', lang)}\n{_t('note_chat', lang)}: {chat_ref}\n"
        f"{_t('note_from', lang)}: {sender_ref}\n\n"
        f"{_t('note_was', lang)}:\n{_esc(previous)}\n\n{_t('note_now', lang)}:\n{_esc(new)}"
    )


def format_autoresponder_notice(sender_ref: str, text: str, lang: str = "uk") -> str:
    return f"{_t('note_ar_fired', lang)}\n{_t('note_from', lang)}: {sender_ref}\n\n{_t('note_incoming', lang)}:\n{_esc(text)}"


def kind_label(kind: str, lang: str = "uk") -> str:
    """`"photo"`/`"voice"`/`"video"`/`"document"`/`"location"` -> localized word, e.g. "фото"."""
    return (
        _t(f"kind_{kind}", lang)
        if kind in ("photo", "voice", "video", "document", "location", "media")
        else _t("kind_media", lang)
    )


def format_view_once_notice(sender_ref: str, kind: str, lang: str = "uk") -> str:
    return f"{_t('note_view_once', lang, kind=kind_label(kind, lang))}\n{_t('note_from', lang)}: {sender_ref}"


def _esc_attr(url: str) -> str:
    return (url or "").replace("&", "&amp;").replace('"', "&quot;")


def _esc(text: str | None) -> str:
    if not text:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
