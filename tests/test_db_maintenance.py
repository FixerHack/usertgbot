"""db_maintenance flexibility: profile-driven check/fix + headerless CSV."""

from pathlib import Path
from typing import Any

import pytest

from db_maintenance.commands.check import run_check
from db_maintenance.commands.fix import run_fix
from db_maintenance.profiles import CONTACTS, REPUTATION, get_profile
from db_maintenance.sources.base import DataSource
from db_maintenance.sources.csv_source import CSVSource


class FakeSource(DataSource):
    def __init__(self, records, user_ids=None, id_field="id"):
        self.label = "fake"
        self.id_field = id_field
        self._records = records
        self._user_ids = user_ids
        self.applied: tuple[list, dict] | None = None

    async def load_records(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._records]

    async def load_user_ids(self):
        return self._user_ids

    async def apply_fixes(self, *, delete_ids, updates):
        self.applied = (sorted(delete_ids, key=str), updates)

    async def replace_all(self, rows):
        self._records = rows
        return len(rows)


@pytest.fixture(autouse=True)
def _temp_backups(tmp_path, monkeypatch):
    from db_maintenance.config import settings

    monkeypatch.setattr(settings, "backup_dir", tmp_path / "backups")


# --- reputation profile (unchanged behavior) -------------------------------


async def test_reputation_check_detects_all_issue_types():
    records = [
        {"id": 1, "user_id": 1, "chat_id": 10, "score": 2, "positive_count": 3, "negative_count": 1},  # ok
        {"id": 2, "user_id": 1, "chat_id": 10, "score": 0, "positive_count": None, "negative_count": 0},  # null
        {"id": 3, "user_id": 1, "chat_id": 10, "score": 9, "positive_count": 1, "negative_count": 1},  # inconsistent
        {"id": 4, "user_id": 99, "chat_id": 20, "score": 0, "positive_count": 0, "negative_count": 0},  # orphan
    ]
    src = FakeSource(records, user_ids={1})
    report = await run_check(src, REPUTATION)
    assert report.scanned == 4
    assert report.null_violations == [2]
    assert report.inconsistent_scores == [3]
    assert report.orphans == [4]
    assert (1, 10) in report.duplicates  # ids 1,2,3 share user/chat


async def test_reputation_fix_fills_nulls_and_dedups():
    records = [
        {"id": 1, "user_id": 1, "chat_id": 10, "score": 0, "positive_count": 0, "negative_count": 0, "updated_at": "2026-01-01"},
        {"id": 2, "user_id": 1, "chat_id": 10, "score": 0, "positive_count": 0, "negative_count": 0, "updated_at": "2026-02-01"},
        {"id": 3, "user_id": 5, "chat_id": 30, "score": 0, "positive_count": None, "negative_count": 1, "updated_at": "2026-01-01"},
    ]
    src = FakeSource(records, user_ids={1, 5})
    report = await run_check(src, REPUTATION)
    fix = await run_fix(src, report, REPUTATION)

    delete_ids, updates = src.applied
    assert 1 in delete_ids  # older duplicate dropped, newer (id 2) kept
    assert updates[3] == {"positive_count": 0, "negative_count": 0}  # null filled
    assert fix.deleted == 1 and fix.fixed == 1


# --- contacts profile (new flexibility) ------------------------------------


async def test_contacts_check_headerless_fields():
    records = [
        {"telegram_id": "1", "first_name": "A", "username": "a", "phone_number": "79001112233"},
        {"telegram_id": "2", "first_name": "B", "username": "", "phone_number": ""},   # missing phone
        {"telegram_id": "1", "first_name": "A2", "username": "a2", "phone_number": "79004445566"},  # dup id
    ]
    src = FakeSource(records, id_field="telegram_id")
    report = await run_check(src, CONTACTS)
    assert report.null_violations == ["2"]          # blank phone flagged
    assert ("1",) in report.duplicates              # duplicate telegram_id
    assert report.orphans == []                     # orphan checks disabled for contacts


async def test_contacts_fix_deletes_unfillable_nulls_and_dedups():
    records = [
        {"telegram_id": "1", "phone_number": "79001112233", "updated_at": "2026-01-01"},
        {"telegram_id": "1", "phone_number": "79004445566", "updated_at": "2026-02-01"},  # dup id
        {"telegram_id": "2", "phone_number": "", "updated_at": ""},  # unfillable -> delete
    ]
    src = FakeSource(records, id_field="telegram_id")
    report = await run_check(src, CONTACTS)
    fix = await run_fix(src, report, CONTACTS)

    # non-unique id => rebuild via replace_all, not delete-by-id
    assert src.applied is None
    remaining = await src.load_records()
    assert len(remaining) == 1                       # one "1" kept, "2" dropped
    assert remaining[0]["phone_number"] == "79004445566"  # newer dup kept
    assert fix.deleted == 2 and fix.fixed == 0


# --- headerless CSV round-trip ---------------------------------------------


async def test_headerless_csv_check_and_fix(tmp_path: Path):
    csv_path = tmp_path / "contacts.csv"
    csv_path.write_text(
        "1,Alice,alice,79001112233\n"
        "2,Bob,,\n"                       # missing phone
        "1,Alice2,alice2,79004445566\n",  # duplicate telegram_id
        encoding="utf-8",
    )
    src = CSVSource(csv_path, id_field=CONTACTS.id_field, columns=CONTACTS.csv_columns)

    report = await run_check(src, CONTACTS)
    assert report.null_violations == ["2"]
    assert ("1",) in report.duplicates

    await run_fix(src, report, CONTACTS)
    remaining = await src.load_records()
    ids = {r["telegram_id"] for r in remaining}
    assert "2" not in ids          # unfillable null row removed
    assert len(remaining) == 1     # dup collapsed + bad row dropped
    # file stays headerless
    assert not csv_path.read_text(encoding="utf-8").startswith("telegram_id")


def test_get_profile():
    assert get_profile(None).name == "reputation"
    assert get_profile("contacts").name == "contacts"
    with pytest.raises(ValueError):
        get_profile("nonexistent")
