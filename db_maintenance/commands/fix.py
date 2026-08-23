"""`fix` — auto-repairs what `check` found: dedup (keep latest by the profile's
sort field), fill/drop unexpected nulls, delete orphan records. Always backs up
the source first via db_maintenance.backup.backup_records.

Two strategies, chosen by the profile:

* unique_id=True (default, e.g. reputation/DB) — targeted delete-by-id + field
  updates, cheap against a live database.
* unique_id=False (e.g. a contacts CSV, where the id repeats across duplicate
  rows) — can't delete a single row by a non-unique id, so the kept set is
  rebuilt in memory and written back via replace_all.
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from db_maintenance.backup import backup_records
from db_maintenance.commands.check import CheckReport, _is_blank
from db_maintenance.profiles import REPUTATION, CheckConfig
from db_maintenance.sources.base import DataSource


@dataclass
class FixReport:
    fixed: int = 0
    deleted: int = 0


async def run_fix(source: DataSource, report: CheckReport, config: CheckConfig = REPUTATION) -> FixReport:
    if report.broken_count == 0:
        return FixReport()

    await backup_records(source)
    records = await source.load_records()

    if config.unique_id:
        return await _fix_by_id(source, records, report, config)
    return await _fix_by_rebuild(source, records, config)


async def _fix_by_id(source, records, report, config) -> FixReport:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        if config.duplicate_keys:
            groups[tuple(row.get(k) for k in config.duplicate_keys)].append(row)

    delete_ids: set[Any] = set()

    for key in report.duplicates:
        rows = sorted(
            groups[key],
            key=lambda r: (r.get(config.sort_field) or "", r.get(config.id_field)),
            reverse=True,
        )
        delete_ids.update(row.get(config.id_field) for row in rows[1:])

    delete_ids.update(report.orphans)

    updates: dict[Any, dict[str, Any]] = {}
    for row_id in report.null_violations:
        if row_id in delete_ids:
            continue
        if config.null_fill:
            updates[row_id] = dict(config.null_fill)
        else:
            delete_ids.add(row_id)

    await source.apply_fixes(delete_ids=list(delete_ids), updates=updates)
    return FixReport(fixed=len(updates), deleted=len(delete_ids))


async def _fix_by_rebuild(source, records, config) -> FixReport:
    """For non-unique ids: recompute the surviving rows and replace_all."""
    indexed = list(enumerate(records))
    drop: set[int] = set()

    # 1. dedup: keep the latest row per duplicate-key group
    if config.duplicate_keys:
        groups: dict[tuple[Any, ...], list[tuple[int, dict]]] = defaultdict(list)
        for idx, row in indexed:
            groups[tuple(row.get(k) for k in config.duplicate_keys)].append((idx, row))
        for items in groups.values():
            if len(items) > 1:
                ordered = sorted(items, key=lambda ir: (ir[1].get(config.sort_field) or "", ir[0]), reverse=True)
                drop.update(idx for idx, _ in ordered[1:])

    # 2. null violations: fill in place, or drop when unfillable
    fixed = 0
    for idx, row in indexed:
        if idx in drop:
            continue
        if any(_is_blank(row.get(f)) for f in config.required_fields):
            if config.null_fill:
                row.update(config.null_fill)
                fixed += 1
            else:
                drop.add(idx)

    # 3. orphans
    if config.orphan_field:
        user_ids = await source.load_user_ids()
        if user_ids is not None:
            for idx, row in indexed:
                if row.get(config.orphan_field) not in user_ids:
                    drop.add(idx)

    kept = [row for idx, row in indexed if idx not in drop]
    await source.replace_all(kept)
    return FixReport(fixed=fixed, deleted=len(drop))
