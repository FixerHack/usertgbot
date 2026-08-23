"""In-memory cache of recently-seen messages, per worker.

Telethon's MessageDeleted event carries only ids (no content), and
MessageEdited doesn't include the previous text — so to auto-save deleted/edited
messages we must remember what came through. Bounded FIFO so memory stays flat.

For non-channel chats (regular DMs and small groups), Telegram's delete update
(UpdateDeleteMessages) doesn't carry a peer at all, so Telethon reports
`event.chat_id = None` for those deletions — there is no protocol-level way to
know which chat a plain message_id belonged to. `pop_by_message_id` is the
fallback for that case: it tracks the most recent chat each message_id was
seen in and resolves from there. Collisions are possible (different chats can
reuse the same id) but rare in practice, and far better than never detecting
DM deletions at all.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass


@dataclass
class CachedMessage:
    chat_id: int
    message_id: int
    sender_id: int | None
    text: str | None


class RecentMessageCache:
    def __init__(self, max_size: int = 2000) -> None:
        self._max_size = max_size
        self._items: "OrderedDict[tuple[int, int], CachedMessage]" = OrderedDict()
        # message_id -> most-recently-seen chat_id, for the chat_id=None case
        self._by_message_id: dict[int, int] = {}

    def remember(
        self, chat_id: int, message_id: int, sender_id: int | None, text: str | None
    ) -> None:
        key = (chat_id, message_id)
        self._items[key] = CachedMessage(chat_id, message_id, sender_id, text)
        self._items.move_to_end(key)
        self._by_message_id[message_id] = chat_id
        while len(self._items) > self._max_size:
            evicted_key, _ = self._items.popitem(last=False)
            if self._by_message_id.get(evicted_key[1]) == evicted_key[0]:
                del self._by_message_id[evicted_key[1]]

    def get(self, chat_id: int, message_id: int) -> CachedMessage | None:
        return self._items.get((chat_id, message_id))

    def pop(self, chat_id: int, message_id: int) -> CachedMessage | None:
        item = self._items.pop((chat_id, message_id), None)
        if item is not None and self._by_message_id.get(message_id) == chat_id:
            del self._by_message_id[message_id]
        return item

    def pop_by_message_id(self, message_id: int) -> CachedMessage | None:
        """Fallback for deletions with no known chat_id (DMs/small groups)."""
        chat_id = self._by_message_id.get(message_id)
        if chat_id is None:
            return None
        return self.pop(chat_id, message_id)

    def __len__(self) -> int:
        return len(self._items)
