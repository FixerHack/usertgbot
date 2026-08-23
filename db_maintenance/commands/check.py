"""`check` — scans records (from any DataSource) and reports broken data:
nulls where not expected, orphan records without a user, duplicates by the
profile's key, and derived-consistency violations. Read-only.

Driven by a profiles.CheckConfig; the default profile reproduces the original
reputation_records behavior exactly.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from db_maintenance.profiles import REPUTATION, CheckConfig, ConsistencyRule
from db_maintenance.sources.base import DataSource


@dataclass
class CheckReport:
    scanned: int = 0
    null_violations: list[Any] = field(default_factory=list)
    orphans: list[Any] = field(default_factory=list)
    duplicates: list[tuple[Any, Any]] = field(default_factory=list)
    inconsistent_scores: list[Any] = field(default_factory=list)
    orphan_check_supported: bool = True

    @property
    def broken_count(self) -> int:
        distinct_broken_rows = set(self.null_violations) | set(self.orphans) | set(self.inconsistent_scores)
        return len(distinct_broken_rows) + len(self.duplicates)


def _is_blank(value: Any) -> bool:
    return value is None or value == ""


def _consistency_fails(row: dict[str, Any], rule: ConsistencyRule) -> bool:
    try:
        return int(row[rule.result_field]) != int(row[rule.a_field]) - int(row[rule.b_field])
    except (KeyError, TypeError, ValueError):
        return False  # non-numeric / missing => not a consistency violation


async def run_check(source: DataSource, config: CheckConfig = REPUTATION) -> CheckReport:
    records = await source.load_records()
    user_ids = await source.load_user_ids() if config.orphan_field else None
    report = CheckReport(
        scanned=len(records),
        orphan_check_supported=config.orphan_field is not None and user_ids is not None,
    )

    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)

    for row in records:
        row_id = row.get(config.id_field)

        if any(_is_blank(row.get(f)) for f in config.required_fields):
            report.null_violations.append(row_id)
        elif config.consistency and _consistency_fails(row, config.consistency):
            report.inconsistent_scores.append(row_id)

        if config.orphan_field and user_ids is not None and row.get(config.orphan_field) not in user_ids:
            report.orphans.append(row_id)

        if config.duplicate_keys:
            groups[tuple(row.get(k) for k in config.duplicate_keys)].append(row)

    report.duplicates = [key for key, rows in groups.items() if len(rows) > 1]
    return report
