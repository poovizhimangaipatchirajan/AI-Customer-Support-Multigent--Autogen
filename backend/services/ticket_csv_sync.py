"""Best-effort, privacy-sensitive snapshot of committed tickets for rubric logging.

SQLite remains authoritative. Run in a single worker process with a shared persistent
volume; do not expose the snapshot through a public static-file server.
"""
import csv
import os
import re
import tempfile
from pathlib import Path

from backend.services_rubric_logging import FIELDS, _rows

DEFAULT_PATH = "/app/data/reports/fixflow_complaint_log.csv"


def _safe_cell(value):
    text = "" if value is None else str(value)
    # Keep consistent with the existing admin CSV endpoint.
    return "'" + text if re.match(r"^\s*[=+@\-\t\r]", text) else text


def sync_csv(path=None):
    """Rebuild the complete nine-column CSV; atomically publish after SQLite reads.

    A failed snapshot never mutates the database or replaces a valid prior file.
    Returns the number of ticket rows written.
    """
    target = Path(path or os.environ.get("FIXFLOW_RUBRIC_CSV_PATH", DEFAULT_PATH))
    rows = _rows()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="", dir=target.parent,
            prefix=".fixflow-rubric-", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({field: _safe_cell(row.get(field, "")) for field in FIELDS})
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        return len(rows)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
