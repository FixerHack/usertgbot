"""Identity a server-side Telethon connection presents to the account owner.

Telethon otherwise reports the machine's own hardware ("PC 64bit", the host's
kernel version) in the owner's Settings -> Devices list — an anonymous entry
in a foreign datacenter, which invites them to hit "terminate" and silently
kill the subscription they're paying for.

Every place that opens a server-side TelegramClient (userbot workers, the
Mini App backend's own post-login verification) must use the SAME three
fields, not just non-default ones — device_model/system_version/app_version
update live on an already-existing session (verified empirically), so two
call sites disagreeing would have one silently overwrite what the other set.
"""

from __future__ import annotations

DEVICE_MODEL = "User Agent Bot"
SYSTEM_VERSION = "1.0"
APP_VERSION = "1.0"


def device_kwargs() -> dict[str, str]:
    return {"device_model": DEVICE_MODEL, "system_version": SYSTEM_VERSION, "app_version": APP_VERSION}
