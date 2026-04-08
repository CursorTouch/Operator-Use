import pytest

def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: end-to-end tests requiring full agent stack")

@pytest.fixture
def mock_llm_response():
    return "Task complete."
