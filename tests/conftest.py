import pytest
import os

@pytest.fixture(autouse=True, scope="session")
def use_test_db(tmp_path_factory):
    """Redirect SQLite to a temp DB for the entire test session."""
    db = tmp_path_factory.mktemp("data") / "test.db"
    db_url = f"sqlite:///{db}"
    os.environ["SQLITE_DB_PATH"] = db_url
    
    # Update settings so get_connection uses the temp DB
    from src.core.config import settings
    settings.sqlite_db_path = db_url
    
    # Initialize schema in the temp DB
    from src.storage.sqlite_store import init_db
    init_db()
    
    yield
