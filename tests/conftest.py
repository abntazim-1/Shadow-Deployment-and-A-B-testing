import pytest
import os

@pytest.fixture(autouse=True, scope="session")
def use_test_db(tmp_path_factory):
    """Redirect SQLite to a temp DB for the entire test session."""
    db = tmp_path_factory.mktemp("data") / "test.db"
    os.environ["SQLITE_DB_PATH"] = f"sqlite:///{db}"
    yield
