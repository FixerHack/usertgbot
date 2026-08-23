"""Shared ORM models: User, Subscription, ReputationRecord, Session.

Imported by all three services, imports nothing from any of them.
"""

import enum
from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Date, ForeignKey, Index, JSON, LargeBinary, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    """Any Telegram user known to the system: subscribers who talked to
    management_bot, and people who were `.check`-ed by a userbot worker
    (they may never have subscribed to anything themselves).
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(default=None)
    full_name: Mapped[str | None] = mapped_column(default=None)
    language_code: Mapped[str | None] = mapped_column(default=None)  # Telegram lang, for i18n
    is_blocked: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    subscriptions: Mapped[list["Subscription"]] = relationship(back_populates="user")
    sessions: Mapped[list["Session"]] = relationship(back_populates="user")


class SubscriptionStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class Subscription(Base):
    """A paid tariff period for a user, created via management_bot/payment."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    tariff: Mapped[str] = mapped_column()
    status: Mapped[SubscriptionStatus] = mapped_column(default=SubscriptionStatus.PENDING)
    payment_provider: Mapped[str] = mapped_column()
    external_invoice_id: Mapped[str | None] = mapped_column(default=None)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    expires_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="subscriptions")


class ReputationRecord(Base):
    """Reputation of `user_id` as observed in `chat_id`.

    `positive_count`/`negative_count` are nullable on purpose: rows written
    before that breakdown existed (or by a buggy client) may have them
    missing — that's exactly the kind of gap db_maintenance.check looks for.
    There's intentionally no DB-level unique constraint on
    (user_id, chat_id) either — duplicate detection/dedup is db_maintenance's
    job, not a schema-level guarantee.
    """

    __tablename__ = "reputation_records"
    __table_args__ = (Index("ix_reputation_user_chat", "user_id", "chat_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    score: Mapped[int] = mapped_column(default=0)
    positive_count: Mapped[int | None] = mapped_column(default=0)
    negative_count: Mapped[int | None] = mapped_column(default=0)
    reported_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship()


class Session(Base):
    """One userbot login: an encrypted Telethon session string owned by a user."""

    __tablename__ = "sessions"
    __table_args__ = (UniqueConstraint("user_id", "phone_number", name="uq_session_user_phone"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    phone_number: Mapped[str] = mapped_column()
    encrypted_session: Mapped[bytes] = mapped_column(LargeBinary)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(default=None)

    user: Mapped["User"] = relationship(back_populates="sessions")


class UserSettings(Base):
    """Per-user configuration edited via management_bot, read by userbot.

    `me_card` and `autoresponder` are free-form JSON blobs (see
    management_bot.settings_schema for their shape) so the UI can evolve
    without a migration per field.
    """

    __tablename__ = "user_settings"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    me_card: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    autoresponder: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship()


class CheckUsage(Base):
    """Monthly `.check` counter per user; reset lazily when the month rolls."""

    __tablename__ = "check_usage"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    period_start: Mapped[date] = mapped_column(Date)
    used: Mapped[int] = mapped_column(default=0)

    user: Mapped["User"] = relationship()


class SavedMessage(Base):
    """A message an owner's userbot captured when it was deleted or edited."""

    __tablename__ = "saved_messages"
    __table_args__ = (Index("ix_saved_owner_chat", "owner_user_id", "chat_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger)
    sender_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    event_type: Mapped[str] = mapped_column()  # "deleted" | "edited"
    text: Mapped[str | None] = mapped_column(default=None)           # latest/deleted text
    previous_text: Mapped[str | None] = mapped_column(default=None)  # edits: text before
    saved_at: Mapped[datetime] = mapped_column(server_default=func.now())

    owner: Mapped["User"] = relationship()


class MediaBlob(Base):
    """Raw image bytes for a user's .me card / autoresponder.

    Stored as bytes (not a Bot API file_id) because the userbot sends them via
    Telethon, which can't reuse file_ids minted by the aiogram management bot.
    One blob per (user, purpose).
    """

    __tablename__ = "media_blobs"

    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    purpose: Mapped[str] = mapped_column(primary_key=True)  # "me" | "autoresponder"
    data: Mapped[bytes] = mapped_column(LargeBinary)
    mime: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    owner: Mapped["User"] = relationship()


class IgnoredChat(Base):
    """A chat the owner opted out of deleted/edited-message notifications for,
    via the "🚫 Ігнорувати цей чат" button on a notification, or manually in
    /settings."""

    __tablename__ = "ignored_chats"

    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    chat_title: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    owner: Mapped["User"] = relationship()
