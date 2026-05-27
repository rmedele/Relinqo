import os
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Point tests at an isolated temp SQLite DB before app imports happen.
fd, test_db_path = tempfile.mkstemp(prefix='leadrelay-test-', suffix='.db')
os.close(fd)
os.environ['DATABASE_URL'] = f'sqlite:///{test_db_path}'
os.environ['APP_ENV'] = 'test'
os.environ['SESSION_SECRET'] = 'test-session-secret'
os.environ['LLM_PROVIDER'] = 'none'
os.environ['LLM_API_KEY'] = ''

from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.database import engine as app_engine  # noqa: E402

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=app_engine)
Base.metadata.create_all(bind=app_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(scope='session', autouse=True)
def cleanup_test_db():
    yield
    app_engine.dispose()
    try:
        os.remove(test_db_path)
    except (FileNotFoundError, PermissionError):
        pass
