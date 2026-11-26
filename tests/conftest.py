import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from incident_monitor import cleaning, database  # noqa: E402


@pytest.fixture(scope="session")
def cleaned():
    return cleaning.clean_all()


@pytest.fixture()
def db_path(tmp_path, cleaned):
    path = tmp_path / "test.db"
    database.seed_database(cleaned, path)
    return path
