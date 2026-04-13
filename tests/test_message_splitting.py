"""Tests for Discord/Telegram message splitting logic."""

from operator_use.gateway.channels.discord import _split_message as discord_split


# --- Discord _split_message ---


def test_split_short_message():
    chunks = discord_split("hello", max_len=2000)
    assert chunks == ["hello"]


def test_split_empty_message():
    assert discord_split("") == []


def test_split_exact_max_len():
    msg = "a" * 2000
    chunks = discord_split(msg, max_len=2000)
    assert chunks == [msg]


def test_split_over_max_len_on_newline():
    msg = "line one\n" + "line two\n" + "x" * 2000
    chunks = discord_split(msg, max_len=2000)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 2000


def test_split_over_max_len_on_space():
    words = ["word"] * 600
    msg = " ".join(words)  # 600 * 5 = 3000 chars with spaces
    chunks = discord_split(msg, max_len=2000)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 2000


def test_split_no_breaks_force_split():
    msg = "a" * 5000
    chunks = discord_split(msg, max_len=2000)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 2000


def test_split_preserves_all_content():
    msg = "hello world\nfoo bar\nbaz"
    chunks = discord_split(msg, max_len=10)
    rejoined = "".join(chunks)
    # All content should be present (whitespace may be stripped at split points)
    for word in ["hello", "world", "foo", "bar", "baz"]:
        assert word in rejoined


def test_split_custom_max_len():
    msg = "abcdefghij"  # 10 chars
    chunks = discord_split(msg, max_len=5)
    assert len(chunks) == 2
    assert chunks[0] == "abcde"
    assert chunks[1] == "fghij"


def test_split_multiline_each_chunk_within_limit():
    lines = [f"line {i}" for i in range(100)]
    msg = "\n".join(lines)
    chunks = discord_split(msg, max_len=50)
    for chunk in chunks:
        assert len(chunk) <= 50
