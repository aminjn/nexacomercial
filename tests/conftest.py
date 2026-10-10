import os
import tempfile

os.environ.setdefault("APP_LLM_PROVIDER", "fake")
os.environ.setdefault("APP_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("APP_SCHEDULER_ENABLED", "false")
os.environ.setdefault("APP_SECRET_KEY", "test-secret")

import pytest  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from app import models as m  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    from app.config import settings
    saved = dict(settings.__dict__)  # tests that save the settings form must not leak into the next test
    SQLModel.metadata.drop_all(m.engine())
    SQLModel.metadata.create_all(m.engine())
    yield
    settings.__dict__.update(saved)
