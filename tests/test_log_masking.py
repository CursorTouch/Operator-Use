"""Tests for credential masking in log output."""

import logging

from operator_use.utils.log_masking import (
    CredentialMaskingFilter,
    install_credential_masking,
    mask_credentials,
)


class TestMaskCredentials:
    def test_masks_bearer_token(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc.def"
        result = mask_credentials(text)
        assert "eyJhbGciOiJIUzI1NiJ9" not in result
        assert "REDACTED" in result

    def test_masks_api_key_pattern(self):
        result = mask_credentials("Using api_key=sk-abcdefghijklmnop123456")
        assert "sk-abcdefghijklmnop123456" not in result
        assert "REDACTED" in result

    def test_masks_sk_prefix_key(self):
        result = mask_credentials("key is sk-proj-abc12345678")
        assert "abc12345678" not in result
        assert "REDACTED" in result

    def test_masks_password_in_connection_string(self):
        result = mask_credentials("Connecting to db with password=mysecretpassword123")
        assert "mysecretpassword123" not in result
        assert "REDACTED" in result

    def test_masks_secret_value(self):
        result = mask_credentials("secret=superSecretValue99")
        assert "superSecretValue99" not in result

    def test_masks_jwt(self):
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        result = mask_credentials(f"token={jwt}")
        assert jwt not in result
        assert "REDACTED" in result

    def test_masks_standalone_jwt(self):
        """JWT not preceded by a credential keyword should use JWT_REDACTED."""
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        result = mask_credentials(f"Received {jwt} from upstream")
        assert jwt not in result
        assert "JWT_REDACTED" in result

    def test_masks_authorization_header(self):
        result = mask_credentials("authorization: Bearer mytoken123")
        assert "mytoken123" not in result

    def test_masks_x_api_key_header(self):
        result = mask_credentials("x-api-key: abc123secret")
        assert "abc123secret" not in result

    def test_passthrough_safe_text(self):
        safe = "Starting server on port 8080"
        assert mask_credentials(safe) == safe

    def test_passthrough_normal_log_line(self):
        safe = "Agent loop iteration 3 of 10 completed in 1.2s"
        assert mask_credentials(safe) == safe


class TestCredentialMaskingFilter:
    def test_filter_masks_record_msg(self):
        f = CredentialMaskingFilter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "password=secret123abc", (), None
        )
        f.filter(record)
        assert "secret123abc" not in record.msg
        assert "REDACTED" in record.msg

    def test_filter_returns_true(self):
        """Filter must return True to keep the record (masking, not suppressing)."""
        f = CredentialMaskingFilter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "hello world", (), None
        )
        assert f.filter(record) is True

    def test_filter_masks_tuple_args(self):
        f = CredentialMaskingFilter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "key=%s", ("api_key=supersecret",), None
        )
        f.filter(record)
        assert "supersecret" not in str(record.args)

    def test_filter_masks_dict_args(self):
        f = CredentialMaskingFilter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "%(cred)s", (), None
        )
        # Set dict args after construction to avoid LogRecord constructor issue
        record.args = {"cred": "token=abc123xyz"}
        f.filter(record)
        assert "abc123xyz" not in str(record.args)

    def test_filter_handles_none_args(self):
        f = CredentialMaskingFilter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "no args here", None, None
        )
        assert f.filter(record) is True


class TestInstallCredentialMasking:
    def test_install_adds_filter_to_root_logger(self):
        root = logging.getLogger()
        # Remove any existing masking filters first
        root.filters = [
            f for f in root.filters if not isinstance(f, CredentialMaskingFilter)
        ]
        install_credential_masking()
        masking_filters = [
            f for f in root.filters if isinstance(f, CredentialMaskingFilter)
        ]
        assert len(masking_filters) == 1

    def test_install_idempotent(self):
        root = logging.getLogger()
        # Clean slate
        root.filters = [
            f for f in root.filters if not isinstance(f, CredentialMaskingFilter)
        ]
        install_credential_masking()
        install_credential_masking()  # second call should not add duplicate
        masking_filters = [
            f for f in root.filters if isinstance(f, CredentialMaskingFilter)
        ]
        assert len(masking_filters) == 1
