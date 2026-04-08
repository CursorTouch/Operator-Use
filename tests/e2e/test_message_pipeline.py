"""E2E tests for the full agent message pipeline. Require full stack -- skipped by default."""
import pytest


class TestMessagePipeline:
    @pytest.mark.skip(reason="Requires full stack -- see issue #10")
    def test_message_to_response_roundtrip(self):
        pass

    @pytest.mark.skip(reason="Requires full stack -- see issue #10")
    def test_tool_execution_in_pipeline(self):
        pass

    @pytest.mark.skip(reason="Requires full stack -- see issue #10")
    def test_multi_agent_delegation(self):
        pass
