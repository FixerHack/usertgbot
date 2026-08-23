"""Reputation lookup + formatting.

Takes a target user_id, queries db.ReputationRecord, and formats a reply
with score + short breakdown. Never includes sensitive fields (phone,
raw session data, internal ids of reporters, etc).
"""

from dataclasses import dataclass


@dataclass
class ReputationView:
    user_id: int
    score: int
    breakdown: dict[str, int]


async def get_reputation(user_id: int) -> ReputationView | None:
    # TODO: query db.ReputationRecord via db.session.get_session()
    return None


def format_reputation(view: ReputationView) -> str:
    # TODO: short, human-readable breakdown, no sensitive fields
    return f"Score: {view.score}"
