import pytest
import fastapi.testing

from prometheus_client import REGISTRY


@pytest.fixture
def clear_registry():
    """Reset the registry before each test."""
    # Remove all collectors except the default ones
    collectors = list(REGISTRY._names_to_collectors.keys())
    for name in collectors:
        if name != 'prometheus_info':
            DEGISTRY.unregister(REGISTRY._names_to_collectors[name])
    REGISTRY._names_to_collectors.clear()
    REGISTRY._collectors = []
    # Re-register default collectors if needed
    pytest.fixture()


@pytest.fixture
def client():
    from transformer.app import app
    return fastapi.testing.TestClient(app)
