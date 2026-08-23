"""Picks a DataSource from a CLI --source value, by file extension.

No value (or None) -> live Postgres via DB_URL, same as before this module
existed. Anything else is resolved by suffix: .db/.sqlite/.sqlite3 ->
SQLite, .csv -> CSV, .json -> JSON.

`id_field`/`columns` come from the active check profile (see
db_maintenance.profiles) so file sources can carry a non-default primary key
and headerless column names.
"""

from pathlib import Path

from db_maintenance.sources.base import DataSource
from db_maintenance.sources.csv_source import CSVSource
from db_maintenance.sources.json_source import JSONSource
from db_maintenance.sources.postgres_source import PostgresSource
from db_maintenance.sources.sqlite_source import SQLiteSource

_SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def resolve_source(
    target: str | None,
    *,
    id_field: str = "id",
    columns: tuple[str, ...] | None = None,
) -> DataSource:
    if not target:
        return PostgresSource()

    path = Path(target)
    suffix = path.suffix.lower()
    if suffix in _SQLITE_SUFFIXES:
        return SQLiteSource(path)
    if suffix == ".csv":
        return CSVSource(path, id_field=id_field, columns=columns)
    if suffix == ".json":
        return JSONSource(path, id_field=id_field)

    raise ValueError(f"Unsupported --source file type '{suffix}' (expected .db/.sqlite/.sqlite3, .csv or .json)")
