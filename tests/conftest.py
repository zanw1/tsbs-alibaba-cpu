"""Automatically clean every test artifact, including on failed tests."""

from collections.abc import Iterator
from pathlib import Path
import tempfile

import pytest


@pytest.fixture
def workspace() -> Iterator[Path]:
    """Own each test's inputs, outputs, and transient evidence."""
    with tempfile.TemporaryDirectory(prefix="tsbs-alibaba-cpu-test-") as temporary:
        yield Path(temporary)
    return None
