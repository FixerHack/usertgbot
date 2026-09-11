"""Cross-service query helpers used by both management_bot and userbot.

Pure DB access over `db.models` only — no imports from any service package.
All time inputs are passed in explicitly so the logic is deterministic in tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from math import ceil

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    ChatRecording,
    ClonedUser,
    MutedUser,
    RecordedMessage,
    CheckUsage,
    IgnoredChat,
    MediaBlob,
    SavedMessage,
    Session,
    Subscription,
    SubscriptionStatus,
    User,
    UserSettings,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _month_start(today: date) -> date:
    return today.replace(day=1)


# --- users -----------------------------------------------------------------


async def get_user_by_telegram_id(session: AsyncSession, telegram_id: int) -> User | None:
    result = await session.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalar_one_or_none()


async def set_user_language(session: AsyncSession, telegram_id: int, language_code: str | None) -> None:
    """Update a known user's auto-detected language (no-op if they aren't in
    the DB yet, or if they've locked in a manual choice via `set_user_language_manual`)."""
    if not language_code:
        return
    user = await get_user_by_telegram_id(session, telegram_id)
    if user is not None and not user.language_locked:
        user.language_code = language_code
        await session.flush()


async def set_user_language_manual(session: AsyncSession, telegram_id: int, lang: str) -> None:
    """Explicit user choice (e.g. a /settings language picker) — always wins
    over the Telegram-reported `language_code` from then on."""
    user = await get_user_by_telegram_id(session, telegram_id)
    if user is not None:
        user.language_code = lang
        user.language_locked = True
        await session.flush()


async def get_effective_language(session: AsyncSession, telegram_id: int) -> str | None:
    """The DB-resolved language for a known user, or None if they aren't known yet
    (caller should then fall back to the live Telegram-reported language)."""
    user = await get_user_by_telegram_id(session, telegram_id)
    return user.language_code if user is not None else None


async def is_user_blocked(session: AsyncSession, telegram_id: int) -> bool:
    user = await get_user_by_telegram_id(session, telegram_id)
    return bool(user and user.is_blocked)


# --- sessions --------------------------------------------------------------


async def deactivate_user_sessions(session: AsyncSession, user_id: int) -> int:
    """Mark all of a user's active sessions inactive (unlink). Returns count."""
    result = await session.execute(
        select(Session).where(Session.user_id == user_id, Session.is_active.is_(True))
    )
    rows = result.scalars().all()
    for row in rows:
        row.is_active = False
    await session.flush()
    return len(rows)


async def deactivate_session(
    session: AsyncSession, session_id: int, *, only_if_session_is: bytes | None = None
) -> bool:
    """Deactivate a session; returns whether it actually happened.

    `only_if_session_is` guards a real production race: save_session upserts
    onto the SAME row when an account is re-linked, so a worker still holding
    the OLD (now revoked) key would otherwise deactivate a row that already
    contains the user's BRAND NEW, working session — leaving them "not
    connected" seconds after a successful login.

    Guarded on the stored ciphertext, not on updated_at: SQLite's
    CURRENT_TIMESTAMP only has second resolution, so a re-link within the same
    second as the original save would compare equal and the guard would pass
    when it must not (caught by the tests for exactly this).
    """
    result = await session.execute(select(Session).where(Session.id == session_id))
    row = result.scalar_one_or_none()
    if row is None:
        return False
    if only_if_session_is is not None and row.encrypted_session != only_if_session_is:
        return False
    row.is_active = False
    await session.flush()
    return True


# --- subscriptions ---------------------------------------------------------


async def get_active_subscription_for_user(
    session: AsyncSession, user_id: int, *, now: datetime | None = None
) -> Subscription | None:
    """Active, non-expired subscription for an internal user id (latest first).

    Ordered by id rather than created_at: two rows written in the same second
    tie on the timestamp, and with tariff changes and renewals that is no
    longer a theoretical case.
    """
    now = now or _utcnow()
    result = await session.execute(
        select(Subscription)
        .where(Subscription.user_id == user_id, Subscription.status == SubscriptionStatus.ACTIVE)
        .order_by(Subscription.id.desc())
    )
    for sub in result.scalars():
        if sub.expires_at is None or as_aware(sub.expires_at) > now:
            return sub
    return None


async def supersede_other_active(
    session: AsyncSession, *, user_id: int, keep_id: int, now: datetime | None = None
) -> list[Subscription]:
    """Retire every other live subscription of this user, and say which.

    One user, one plan. Buying Pro halfway through a Standard month replaces
    it outright — the new plan runs a full period from now, and the old row
    stops being active instead of sitting alongside it. Without this the two
    overlap and which one applies depends on sort order, which is not a thing
    a paying customer should be exposed to.

    Returns the retired rows so the caller can stop anything still set to
    charge for them; a superseded card mandate that keeps billing is the
    expensive half of this problem.
    """
    now = now or _utcnow()
    result = await session.execute(
        select(Subscription).where(
            Subscription.user_id == user_id,
            Subscription.id != keep_id,
            Subscription.status == SubscriptionStatus.ACTIVE,
        )
    )
    retired = []
    for sub in result.scalars():
        if sub.expires_at is not None and as_aware(sub.expires_at) <= now:
            continue  # already over; leave its history alone
        sub.status = SubscriptionStatus.CANCELLED
        retired.append(sub)
    if retired:
        await session.flush()
    return retired


async def get_active_subscription_by_telegram_id(
    session: AsyncSession, telegram_id: int, *, now: datetime | None = None
) -> Subscription | None:
    user = await get_user_by_telegram_id(session, telegram_id)
    if user is None:
        return None
    return await get_active_subscription_for_user(session, user.id, now=now)


def as_aware(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes; treat those as UTC for comparison.

    Public because the same rule has to hold anywhere a stored timestamp is
    compared to "now" — a second copy of it is a second thing to get wrong.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


# --- .check quota ----------------------------------------------------------


@dataclass
class QuotaResult:
    allowed: bool
    used: int
    quota: int

    @property
    def remaining(self) -> int:
        return max(self.quota - self.used, 0)


async def _get_or_reset_usage(session: AsyncSession, user_id: int, today: date) -> CheckUsage:
    period = _month_start(today)
    result = await session.execute(select(CheckUsage).where(CheckUsage.user_id == user_id))
    row = result.scalar_one_or_none()
    if row is None:
        row = CheckUsage(user_id=user_id, period_start=period, used=0)
        session.add(row)
        await session.flush()
    elif row.period_start < period:
        row.period_start = period
        row.used = 0
        await session.flush()
    return row


async def peek_check_quota(
    session: AsyncSession, user_id: int, quota: int, *, today: date | None = None
) -> QuotaResult:
    row = await _get_or_reset_usage(session, user_id, today or _utcnow().date())
    return QuotaResult(allowed=row.used < quota, used=row.used, quota=quota)


async def consume_check(
    session: AsyncSession, user_id: int, quota: int, *, today: date | None = None
) -> QuotaResult:
    """Try to spend one .check; increments only when allowed."""
    row = await _get_or_reset_usage(session, user_id, today or _utcnow().date())
    if row.used >= quota:
        return QuotaResult(allowed=False, used=row.used, quota=quota)
    row.used += 1
    await session.flush()
    return QuotaResult(allowed=True, used=row.used, quota=quota)


# --- user settings ---------------------------------------------------------


async def get_or_create_settings(session: AsyncSession, user_id: int) -> UserSettings:
    result = await session.execute(select(UserSettings).where(UserSettings.user_id == user_id))
    row = result.scalar_one_or_none()
    if row is None:
        row = UserSettings(user_id=user_id)
        session.add(row)
        await session.flush()
    return row


# The hourly cap's window, and a hard ceiling on how many entries the column
# may hold — the cap itself is at most a handful, so anything beyond this is a
# bug rather than a user.
_SEND_WINDOW_SECONDS = 3600
_SEND_HISTORY_MAX = 50


@dataclass(frozen=True)
class SendAllowance:
    """Whether .send may run, and if not, which limit stopped it.

    Two limits, reported separately: telling someone to "wait 4 minutes" when
    they have actually used up the hour sends them back to try again and fail
    again.
    """

    allowed: bool
    reason: str = ""            # "" | "cooldown" | "hourly"
    remaining_seconds: int = 0


def _recent_sends(row, window_seconds: int, now: datetime) -> list[datetime]:
    """Timestamps still inside the window, oldest first. Anything unparseable
    is dropped rather than raised on: a corrupt entry must not lock someone
    out of a feature they paid for."""
    out = []
    for raw in row.send_history or []:
        try:
            moment = as_aware(datetime.fromisoformat(str(raw)))
        except ValueError:
            continue
        if (now - moment).total_seconds() < window_seconds:
            out.append(moment)
    return sorted(out)


async def peek_send_allowance(
    session: AsyncSession,
    user_id: int,
    *,
    cooldown_seconds: int,
    max_per_hour: int = 0,
    now: datetime | None = None,
) -> SendAllowance:
    now = now or _utcnow()
    row = await get_or_create_settings(session, user_id)

    if row.last_send_at is not None:
        remaining = cooldown_seconds - (now - as_aware(row.last_send_at)).total_seconds()
        if remaining > 0:
            return SendAllowance(False, "cooldown", max(0, ceil(remaining)))

    if max_per_hour > 0:
        recent = _recent_sends(row, _SEND_WINDOW_SECONDS, now)
        if len(recent) >= max_per_hour:
            # Free again when the OLDEST of them ages out of the window.
            wait = _SEND_WINDOW_SECONDS - (now - recent[0]).total_seconds()
            return SendAllowance(False, "hourly", max(0, ceil(wait)))

    return SendAllowance(True)


async def peek_send_cooldown(
    session: AsyncSession, user_id: int, cooldown_seconds: int, *, now: datetime | None = None
) -> tuple[bool, int]:
    """Whether .send may run right now, and how many whole seconds remain if
    not. Per-user (see UserSettings.last_send_at) — the tariff is bought per
    user, so gating per-session would let someone with two connected phone
    numbers just alternate between them to dodge the cooldown."""
    now = now or _utcnow()
    row = await get_or_create_settings(session, user_id)
    if row.last_send_at is None:
        return True, 0
    elapsed = (now - as_aware(row.last_send_at)).total_seconds()
    remaining = cooldown_seconds - elapsed
    return remaining <= 0, max(0, ceil(remaining))


async def mark_send_used(session: AsyncSession, user_id: int, *, now: datetime | None = None) -> None:
    now = now or _utcnow()
    row = await get_or_create_settings(session, user_id)
    row.last_send_at = now.astimezone(timezone.utc).replace(tzinfo=None)
    # Pruned on write so the column cannot grow without bound; the window is
    # the only thing anyone ever asks about.
    history = [m.isoformat() for m in _recent_sends(row, _SEND_WINDOW_SECONDS, now)]
    history.append(now.astimezone(timezone.utc).isoformat())
    row.send_history = history[-_SEND_HISTORY_MAX:]
    await session.flush()


# --- mutes -----------------------------------------------------------------


async def get_mutes(session: AsyncSession, owner_user_id: int) -> list[MutedUser]:
    result = await session.execute(select(MutedUser).where(MutedUser.owner_user_id == owner_user_id))
    return list(result.scalars())


async def set_mute(
    session: AsyncSession,
    *,
    owner_user_id: int,
    chat_id: int,
    target_user_id: int,
    until: datetime | None,
) -> MutedUser:
    """Mute, or re-mute with a new deadline. `until` None means until
    `.unmute` — re-running `.mute` on someone already muted replaces the
    deadline rather than stacking a second row."""
    result = await session.execute(
        select(MutedUser).where(
            MutedUser.owner_user_id == owner_user_id,
            MutedUser.chat_id == chat_id,
            MutedUser.target_user_id == target_user_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = MutedUser(owner_user_id=owner_user_id, chat_id=chat_id, target_user_id=target_user_id)
        session.add(row)
    row.until = until.astimezone(timezone.utc).replace(tzinfo=None) if until is not None else None
    await session.flush()
    return row


async def clear_mute(session: AsyncSession, *, owner_user_id: int, chat_id: int, target_user_id: int) -> bool:
    result = await session.execute(
        select(MutedUser).where(
            MutedUser.owner_user_id == owner_user_id,
            MutedUser.chat_id == chat_id,
            MutedUser.target_user_id == target_user_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True


async def set_me_card(session: AsyncSession, user_id: int, card: dict | None) -> UserSettings:
    row = await get_or_create_settings(session, user_id)
    row.me_card = card
    await session.flush()
    return row


async def set_autoresponder(session: AsyncSession, user_id: int, config: dict | None) -> UserSettings:
    row = await get_or_create_settings(session, user_id)
    row.autoresponder = config
    await session.flush()
    return row


async def set_features(session: AsyncSession, user_id: int, features: dict) -> UserSettings:
    row = await get_or_create_settings(session, user_id)
    row.features = features
    await session.flush()
    return row


# --- saved messages --------------------------------------------------------


async def save_captured_message(
    session: AsyncSession,
    *,
    owner_user_id: int,
    chat_id: int,
    message_id: int,
    event_type: str,
    sender_id: int | None = None,
    text: str | None = None,
    previous_text: str | None = None,
) -> SavedMessage:
    row = SavedMessage(
        owner_user_id=owner_user_id,
        chat_id=chat_id,
        message_id=message_id,
        sender_id=sender_id,
        event_type=event_type,
        text=text,
        previous_text=previous_text,
    )
    session.add(row)
    await session.flush()
    return row


# --- media -----------------------------------------------------------------


async def set_media(
    session: AsyncSession, owner_user_id: int, purpose: str, data: bytes, mime: str | None = None
) -> MediaBlob:
    """Upsert the single image blob for (owner, purpose)."""
    result = await session.execute(
        select(MediaBlob).where(
            MediaBlob.owner_user_id == owner_user_id, MediaBlob.purpose == purpose
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = MediaBlob(owner_user_id=owner_user_id, purpose=purpose, data=data, mime=mime)
        session.add(row)
    else:
        row.data = data
        row.mime = mime
    await session.flush()
    return row


async def delete_media(session: AsyncSession, owner_user_id: int, purpose: str) -> None:
    result = await session.execute(
        select(MediaBlob).where(
            MediaBlob.owner_user_id == owner_user_id, MediaBlob.purpose == purpose
        )
    )
    row = result.scalar_one_or_none()
    if row is not None:
        await session.delete(row)
        await session.flush()


async def list_saved_messages(
    session: AsyncSession, owner_user_id: int, *, limit: int = 20
) -> list[SavedMessage]:
    result = await session.execute(
        select(SavedMessage)
        .where(SavedMessage.owner_user_id == owner_user_id)
        .order_by(SavedMessage.id.desc())  # monotonic => chronological, no same-second ties
        .limit(limit)
    )
    return list(result.scalars())


# --- ignored chats -----------------------------------------------------------


async def add_ignored_chat(
    session: AsyncSession, owner_user_id: int, chat_id: int, chat_title: str | None = None
) -> None:
    result = await session.execute(
        select(IgnoredChat).where(
            IgnoredChat.owner_user_id == owner_user_id, IgnoredChat.chat_id == chat_id
        )
    )
    if result.scalar_one_or_none() is not None:
        return
    session.add(IgnoredChat(owner_user_id=owner_user_id, chat_id=chat_id, chat_title=chat_title))
    await session.flush()


async def remove_ignored_chat(session: AsyncSession, owner_user_id: int, chat_id: int) -> None:
    result = await session.execute(
        select(IgnoredChat).where(
            IgnoredChat.owner_user_id == owner_user_id, IgnoredChat.chat_id == chat_id
        )
    )
    row = result.scalar_one_or_none()
    if row is not None:
        await session.delete(row)
        await session.flush()


async def list_ignored_chats(session: AsyncSession, owner_user_id: int) -> list[IgnoredChat]:
    result = await session.execute(
        select(IgnoredChat)
        .where(IgnoredChat.owner_user_id == owner_user_id)
        .order_by(IgnoredChat.created_at.desc())
    )
    return list(result.scalars())


async def is_chat_ignored(session: AsyncSession, owner_user_id: int, chat_id: int) -> bool:
    result = await session.execute(
        select(IgnoredChat.chat_id).where(
            IgnoredChat.owner_user_id == owner_user_id, IgnoredChat.chat_id == chat_id
        )
    )
    return result.scalar_one_or_none() is not None


# --- chat recordings -------------------------------------------------------

# A transcript is meant to be read. Past this many lines it is a database
# problem wearing a .txt file, so recording stops and says so rather than
# growing quietly.
MAX_RECORDED_MESSAGES = 20_000


async def get_active_recordings(session: AsyncSession, owner_user_id: int) -> list[ChatRecording]:
    result = await session.execute(
        select(ChatRecording).where(
            ChatRecording.owner_user_id == owner_user_id,
            ChatRecording.stopped_at.is_(None),
        )
    )
    return list(result.scalars())


async def start_recording(
    session: AsyncSession, *, owner_user_id: int, chat_id: int, chat_title: str | None
) -> ChatRecording:
    row = ChatRecording(owner_user_id=owner_user_id, chat_id=chat_id, chat_title=chat_title)
    session.add(row)
    await session.flush()
    return row


async def stop_recording(
    session: AsyncSession,
    recording_id: int,
    *,
    owner_user_id: int | None = None,
    now: datetime | None = None,
) -> None:
    """Stop one recording. `owner_user_id` scopes it to that owner.

    The id arrives from a button, so it is a number someone can change. Callers
    do check ownership before calling, but a transcript of a private chat is
    the wrong thing to guard by caller discipline alone.
    """
    row = await session.get(ChatRecording, recording_id)
    if row is not None and owner_user_id is not None and row.owner_user_id != owner_user_id:
        return
    if row is not None and row.stopped_at is None:
        row.stopped_at = (now or _utcnow()).astimezone(timezone.utc).replace(tzinfo=None)
        await session.flush()


async def add_recorded_message(
    session: AsyncSession,
    *,
    recording_id: int,
    sent_at: datetime,
    sender: str,
    is_outgoing: bool,
    text: str,
) -> None:
    session.add(
        RecordedMessage(
            recording_id=recording_id,
            sent_at=sent_at.astimezone(timezone.utc).replace(tzinfo=None) if sent_at.tzinfo else sent_at,
            sender=sender,
            is_outgoing=is_outgoing,
            text=text,
        )
    )
    await session.flush()


async def count_recorded(session: AsyncSession, recording_id: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(RecordedMessage).where(RecordedMessage.recording_id == recording_id)
    ) or 0


async def get_recorded_messages(
    session: AsyncSession, recording_id: int, *, owner_user_id: int | None = None
) -> list[RecordedMessage]:
    """The messages of one recording; `owner_user_id` scopes it to that owner.

    Same reason as `stop_recording`: the id comes from a button, and what it
    returns is somebody's conversation.
    """
    conditions = [RecordedMessage.recording_id == recording_id]
    if owner_user_id is not None:
        conditions.append(
            RecordedMessage.recording_id.in_(
                select(ChatRecording.id).where(ChatRecording.owner_user_id == owner_user_id)
            )
        )
    result = await session.execute(
        select(RecordedMessage)
        .where(*conditions)
        # id, not sent_at: two messages in the same second are common in a
        # chat, and the order they arrived in is the order they were said.
        .order_by(RecordedMessage.id)
    )
    return list(result.scalars())


# --- clones ----------------------------------------------------------------


async def get_clones(session: AsyncSession, owner_user_id: int) -> list[ClonedUser]:
    result = await session.execute(select(ClonedUser).where(ClonedUser.owner_user_id == owner_user_id))
    return list(result.scalars())


async def set_clone(
    session: AsyncSession, *, owner_user_id: int, chat_id: int, target_user_id: int
) -> ClonedUser:
    result = await session.execute(
        select(ClonedUser).where(
            ClonedUser.owner_user_id == owner_user_id,
            ClonedUser.chat_id == chat_id,
            ClonedUser.target_user_id == target_user_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = ClonedUser(owner_user_id=owner_user_id, chat_id=chat_id, target_user_id=target_user_id)
        session.add(row)
        await session.flush()
    return row


async def clear_clone(session: AsyncSession, *, owner_user_id: int, chat_id: int, target_user_id: int) -> bool:
    result = await session.execute(
        select(ClonedUser).where(
            ClonedUser.owner_user_id == owner_user_id,
            ClonedUser.chat_id == chat_id,
            ClonedUser.target_user_id == target_user_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True
