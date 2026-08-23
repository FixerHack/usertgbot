"""Check profiles: what "broken" means for a given dataset.

Previously the check/fix logic hard-coded reputation_records columns. A profile
externalizes that — which field is the id, which fields must be non-null, what
makes a duplicate, an optional derived-consistency rule, and how (or whether)
to auto-fill nulls — so file sources (CSV/JSON) can be validated against
arbitrary schemas, not just reputation records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ConsistencyRule:
    """Asserts result_field == a_field - b_field (integers)."""

    result_field: str
    a_field: str
    b_field: str


@dataclass
class CheckConfig:
    name: str
    id_field: str = "id"
    unique_id: bool = True                         # False => fix rebuilds instead of delete-by-id
    required_fields: tuple[str, ...] = ()          # must be non-blank
    duplicate_keys: tuple[str, ...] = ()           # () disables dup detection
    orphan_field: str | None = None                # None disables orphan checks
    sort_field: str = "updated_at"                 # dedup tie-break (keep latest)
    consistency: ConsistencyRule | None = None
    null_fill: dict[str, Any] = field(default_factory=dict)  # empty => delete unfillable
    csv_columns: tuple[str, ...] | None = None     # headerless CSV column names


REPUTATION = CheckConfig(
    name="reputation",
    id_field="id",
    required_fields=("positive_count", "negative_count"),
    duplicate_keys=("user_id", "chat_id"),
    orphan_field="user_id",
    sort_field="updated_at",
    consistency=ConsistencyRule("score", "positive_count", "negative_count"),
    null_fill={"positive_count": 0, "negative_count": 0},
)

CONTACTS = CheckConfig(
    name="contacts",
    id_field="telegram_id",
    unique_id=False,  # telegram_id repeats across rows — that's what we dedup
    required_fields=("phone_number",),
    duplicate_keys=("telegram_id",),
    orphan_field=None,
    sort_field="telegram_id",
    consistency=None,
    null_fill={},  # a missing phone can't be fabricated -> such rows are dropped
    csv_columns=("telegram_id", "first_name", "username", "phone_number"),
)

PROFILES: dict[str, CheckConfig] = {p.name: p for p in (REPUTATION, CONTACTS)}


def get_profile(name: str | None) -> CheckConfig:
    if not name:
        return REPUTATION
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(f"Unknown profile '{name}'. Available: {', '.join(PROFILES)}")
