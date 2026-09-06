"""Shared fixtures for the transformer test-suite."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Make ``transformer`` importable when pytest runs from the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture
def client() -> TestClient:
    """A TestClient bound to the application under test."""
    from transformer.app import app

    with TestClient(app) as test_client:
        yield test_client
