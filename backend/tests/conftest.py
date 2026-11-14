from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# Isolate every test session: temp data dir, no real key, no background worker.
_TMP = Path(tempfile.mkdtemp(prefix="ocqa-test-"))
os.environ["OCQA_DATA_DIR"] = str(_TMP)
os.environ["OCQA_START_WORKER"] = "false"
os.environ["ALLOW_LOCAL_TARGETS"] = "true"
os.environ.setdefault("OCQA_SECRET_KEY", "x6X8m2v9bWm0tW7r8Gxv3d2b2b9m8p2QfS1lK1G3Y0c=")
if not os.environ.get("OCQA_E2E"):
    os.environ["MISTRAL_API_KEY"] = ""

from app.config import get_settings  # noqa: E402
from app.db.session import init_engine  # noqa: E402


@pytest.fixture(autouse=True, scope="session")
def _engine() -> Iterator[None]:
    get_settings.cache_clear()
    init_engine(get_settings().db_url)
    yield


@pytest.fixture
def tmp_data(tmp_path: Path) -> Path:
    return tmp_path
