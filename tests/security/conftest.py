import pytest


@pytest.fixture
def workspace_dir(tmp_path):
    return tmp_path

@pytest.fixture
def mock_config():
    return {"workspace": "/tmp/test_workspace", "debug": False}
