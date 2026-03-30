"""Security tests for gateway access control. Unskipped as fixes land per SECURITY_ROADMAP.md."""
import pytest


class TestGatewayAuth:
    @pytest.mark.skip(reason="Pending fix in issue #21 [Phase 1.3.2]")
    def test_allow_from_empty_denies_all(self):
        pass

    @pytest.mark.skip(reason="Pending fix in issue #21 [Phase 1.3.2]")
    def test_allow_from_with_valid_user_permits(self):
        pass

    @pytest.mark.skip(reason="Pending fix in issue #21 [Phase 1.3.2]")
    def test_allow_from_with_invalid_user_denies(self):
        pass
