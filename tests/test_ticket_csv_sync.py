import csv
from pathlib import Path
from unittest.mock import patch

from backend.services.ticket_csv_sync import sync_csv
from backend.services_rubric_logging import FIELDS


def test_sync_writes_all_rows_and_is_idempotent(tmp_path):
    rows = [{field: "" for field in FIELDS} for _ in range(2)]
    rows[0]["ticket_id"] = "FF-1"
    rows[1]["ticket_id"] = "FF-2"
    rows[1]["complaint"] = "=SUM(1,2)"
    path = tmp_path / "reports" / "tickets.csv"
    with patch("backend.services.ticket_csv_sync._rows", return_value=rows):
        assert sync_csv(path) == 2
        assert sync_csv(path) == 2
    with path.open(newline="", encoding="utf-8") as stream:
        records = list(csv.DictReader(stream))
    assert len(records) == 2
    assert list(records[0]) == FIELDS
    assert records[1]["complaint"] == "'=SUM(1,2)"
    assert not list(path.parent.glob("*.tmp"))


def test_sync_failure_preserves_prior_file(tmp_path):
    path = tmp_path / "tickets.csv"
    path.write_text("previous-good-snapshot", encoding="utf-8")
    with patch("backend.services.ticket_csv_sync._rows", side_effect=RuntimeError("database offline")):
        try:
            sync_csv(path)
        except RuntimeError:
            pass
        else:
            assert False, "Expected failure"
    assert path.read_text(encoding="utf-8") == "previous-good-snapshot"
