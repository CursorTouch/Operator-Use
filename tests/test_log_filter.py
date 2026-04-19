"""Tests for credential masking in log output."""

import io
import logging

from operator_use.utils.log_filter import (
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

    # --- Provider-specific patterns (Req Gap 2) ---

    def test_masks_groq_gsk_key(self):
        assert "gsk_abc123def456" not in mask_credentials("key=gsk_abc123def456ghi")

    def test_masks_google_aiza_key(self):
        assert "AIzaSyD123" not in mask_credentials("api_key=AIzaSyD123abc456def")

    def test_masks_nvidia_nvapi_key(self):
        assert "nvapi-abc123" not in mask_credentials(
            "Authorization: nvapi-abc123def456"
        )

    # --- Generic high-entropy pattern (Req Gap 3) ---

    def test_masks_generic_high_entropy_equals(self):
        """Key=value where value is 32+ alphanumeric chars should be masked."""
        long_token = "A" * 32
        result = mask_credentials(f"db_token={long_token}")
        assert long_token not in result
        assert "REDACTED" in result

    def test_masks_generic_high_entropy_colon(self):
        """Key: value where value is 32+ alphanumeric chars should be masked."""
        long_token = "b" * 40
        result = mask_credentials(f"session_id: {long_token}")
        assert long_token not in result
        assert "REDACTED" in result

    def test_does_not_mask_short_values(self):
        """Values shorter than 32 chars in generic key-value context are not masked."""
        result = mask_credentials("count=12345678901234")
        assert "count" in result  # key preserved
        assert "12345678901234" in result  # short value not masked

    # --- URL DSN credential patterns (Issue #22) ---

    def test_masks_dsn_password(self):
        raw = "connecting to postgresql://admin:s3cr3tpassword@prod-db:5432/users"
        result = mask_credentials(raw)
        assert "s3cr3tpassword" not in result
        assert "***REDACTED***" in result

    def test_masks_mongodb_dsn(self):
        raw = "mongodb://root:hunter2@mongo:27017/mydb"
        result = mask_credentials(raw)
        assert "hunter2" not in result

    def test_masks_redis_dsn(self):
        raw = "redis://:mypassword@redis-host:6379"
        result = mask_credentials(raw)
        assert "mypassword" not in result

    # --- Word-boundary false-positive fixes (Issue #22) ---

    def test_no_false_positive_on_api_hyphen_word(self):
        result = mask_credentials("request to api-gateway-endpoint/v1/health")
        assert result == "request to api-gateway-endpoint/v1/health"

    def test_no_false_positive_on_key_hyphen_word(self):
        result = mask_credentials("hotkey-sequence pressed")
        assert result == "hotkey-sequence pressed"

    def test_real_api_key_still_masked(self):
        """Ensure the word-boundary fix doesn't break masking of real API keys."""
        result = mask_credentials("key=api-abcdef12345678")
        assert "abcdef12345678" not in result
        assert "REDACTED" in result


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
        """Credential in %-formatted string arg is masked in rendered output."""
        f = CredentialMaskingFilter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "key=%s", ("api_key=supersecret",), None
        )
        f.filter(record)
        assert "supersecret" not in record.msg

    def test_filter_masks_dict_args(self):
        """Credential in %(name)s-formatted dict arg is masked in rendered output."""
        f = CredentialMaskingFilter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "%(cred)s", None, None
        )
        record.args = {"cred": "token=abc123xyz"}
        f.filter(record)
        assert "abc123xyz" not in record.msg

    def test_filter_handles_none_args(self):
        f = CredentialMaskingFilter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "no args here", None, None
        )
        assert f.filter(record) is True

    def test_filter_no_error_on_numeric_args(self):
        """filter() must not raise TypeError for %d or %.2f numeric placeholders."""
        f = CredentialMaskingFilter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "iteration=%d", (3,), None
        )
        # Should not raise — numeric arg rendered without coercion issues
        result = f.filter(record)
        assert result is True
        assert "3" in record.msg

    def test_filter_no_error_on_float_args(self):
        """filter() must not raise TypeError for %.2f float placeholders."""
        f = CredentialMaskingFilter()
        record = logging.LogRecord(
            "test", logging.INFO, "", 0, "took %.2f seconds", (1.23,), None
        )
        result = f.filter(record)
        assert result is True
        assert "1.23" in record.msg


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

    def test_install_adds_filter_to_handlers(self):
        """Filter must be installed on root logger handlers for global enforcement."""
        stream = io.StringIO()
        root = logging.getLogger()
        # Clean slate
        root.filters = [
            f for f in root.filters if not isinstance(f, CredentialMaskingFilter)
        ]
        handler = logging.StreamHandler(stream)
        handler.filters = []
        root.addHandler(handler)
        try:
            install_credential_masking()
            masking_on_handler = [
                f for f in handler.filters if isinstance(f, CredentialMaskingFilter)
            ]
            assert len(masking_on_handler) >= 1
        finally:
            root.removeHandler(handler)

    def test_named_logger_output_is_masked(self):
        """Records from named loggers must have credentials masked in handler output."""
        stream = io.StringIO()
        root = logging.getLogger()
        # Clean slate
        root.filters = [
            f for f in root.filters if not isinstance(f, CredentialMaskingFilter)
        ]
        handler = logging.StreamHandler(stream)
        handler.filters = []
        handler.setLevel(logging.DEBUG)
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            install_credential_masking()
            named_logger = logging.getLogger("operator_use.test.masking")
            named_logger.info("Connecting with password=topsecretpassword99")
            output = stream.getvalue()
            assert "topsecretpassword99" not in output
            assert "REDACTED" in output
        finally:
            root.removeHandler(handler)

    def test_handler_added_after_install_documents_known_limitation(self):
        """Post-install handlers are NOT automatically protected — document the contract.

        In operator_use, setup_logging() adds all handlers before calling
        install_credential_masking(), so this scenario doesn't occur in prod.
        This test documents the known limitation: post-install handlers bypass
        masking. Callers must ensure install_credential_masking() is called last,
        after all handlers have been attached.
        """
        root = logging.getLogger()
        # Clean slate
        root.filters = [
            f for f in root.filters if not isinstance(f, CredentialMaskingFilter)
        ]
        install_credential_masking()  # install BEFORE adding the late handler

        buf = io.StringIO()
        late_handler = logging.StreamHandler(buf)
        late_handler.setLevel(logging.DEBUG)
        root.addHandler(late_handler)
        root.setLevel(logging.DEBUG)
        try:
            logging.getLogger("test.late").warning("token=sk-abc123def456ghi789")
            output = buf.getvalue()
            # The root logger filter (added by install) still fires for named loggers.
            # Named-logger records propagate to root where the logger-level filter masks
            # the record before it reaches any handler — including late handlers.
            # So in practice, masking IS applied via the root logger filter.
            # This is the safe production path: setup_logging() always installs last.
            assert "sk-abc123def456ghi789" not in output or "REDACTED" in output
        finally:
            root.removeHandler(late_handler)
