import sys
from pathlib import Path

import pytest

LIFEOS_ROOT = Path(__file__).resolve().parents[1]
if str(LIFEOS_ROOT) not in sys.path:
    sys.path.insert(0, str(LIFEOS_ROOT))

from app import db  # noqa: E402


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    database = tmp_path / "lifeos-test.db"
    monkeypatch.setattr(db, "DB_PATH", str(database))
    monkeypatch.setenv("LIFEOS_API_TOKEN", "test-token")
    db.init_db()
    return database
