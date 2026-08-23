"""`restore` — rolls a source back to its most recent backup."""

from db_maintenance.backup import restore_records
from db_maintenance.sources.base import DataSource


async def run_restore(source: DataSource) -> int:
    return await restore_records(source)
