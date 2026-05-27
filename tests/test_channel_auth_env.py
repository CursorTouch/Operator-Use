"""Tests for ChannelAuthManager env-var fallback."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from operator_use.auth.channels import ChannelAuthManager


@pytest.fixture
def auth_path(tmp_path):
    return tmp_path / "channels_auth.json"


def make_manager(auth_path, env: dict[str, str] | None = None):
    original = {k: os.environ.get(k) for k in (env or {})}
    for k, v in (env or {}).items():
        os.environ[k] = v
    try:
        return ChannelAuthManager(auth_path)
    finally:
        for k, orig in original.items():
            if orig is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = orig


# ── No file, no env vars → all empty ─────────────────────────────────────────

class TestNoFileNoEnv:
    def test_telegram_empty(self, auth_path):
        mgr = ChannelAuthManager(auth_path)
        assert mgr.telegram.bot_token == ''

    def test_discord_empty(self, auth_path):
        mgr = ChannelAuthManager(auth_path)
        assert mgr.discord.bot_token == ''

    def test_slack_empty(self, auth_path):
        mgr = ChannelAuthManager(auth_path)
        assert mgr.slack.bot_token == ''
        assert mgr.slack.app_token == ''

    def test_twitch_empty(self, auth_path):
        mgr = ChannelAuthManager(auth_path)
        assert mgr.twitch.token == ''


# ── Env vars used when file absent ───────────────────────────────────────────

class TestEnvVarFallback:
    def test_telegram_from_env(self, auth_path):
        mgr = make_manager(auth_path, {'TELEGRAM_BOT_TOKEN': 'tg-secret'})
        assert mgr.telegram.bot_token == 'tg-secret'

    def test_discord_from_env(self, auth_path):
        mgr = make_manager(auth_path, {'DISCORD_BOT_TOKEN': 'dc-secret'})
        assert mgr.discord.bot_token == 'dc-secret'

    def test_slack_bot_from_env(self, auth_path):
        mgr = make_manager(auth_path, {'SLACK_BOT_TOKEN': 'xoxb-bot', 'SLACK_APP_TOKEN': 'xapp-app'})
        assert mgr.slack.bot_token == 'xoxb-bot'
        assert mgr.slack.app_token == 'xapp-app'

    def test_twitch_from_env(self, auth_path):
        mgr = make_manager(auth_path, {'TWITCH_TOKEN': 'twitch-tok'})
        assert mgr.twitch.token == 'twitch-tok'


# ── File value takes precedence over env var ──────────────────────────────────

class TestFilePrecedence:
    def test_file_beats_env_telegram(self, auth_path):
        auth_path.write_text(json.dumps({'telegram': {'bot_token': 'file-token'}}))
        mgr = make_manager(auth_path, {'TELEGRAM_BOT_TOKEN': 'env-token'})
        assert mgr.telegram.bot_token == 'file-token'

    def test_file_beats_env_discord(self, auth_path):
        auth_path.write_text(json.dumps({'discord': {'bot_token': 'file-dc'}}))
        mgr = make_manager(auth_path, {'DISCORD_BOT_TOKEN': 'env-dc'})
        assert mgr.discord.bot_token == 'file-dc'

    def test_file_beats_env_slack(self, auth_path):
        auth_path.write_text(json.dumps({'slack': {'bot_token': 'file-bot', 'app_token': 'file-app'}}))
        mgr = make_manager(auth_path, {'SLACK_BOT_TOKEN': 'env-bot', 'SLACK_APP_TOKEN': 'env-app'})
        assert mgr.slack.bot_token == 'file-bot'
        assert mgr.slack.app_token == 'file-app'

    def test_file_beats_env_twitch(self, auth_path):
        auth_path.write_text(json.dumps({'twitch': {'token': 'file-twitch'}}))
        mgr = make_manager(auth_path, {'TWITCH_TOKEN': 'env-twitch'})
        assert mgr.twitch.token == 'file-twitch'


# ── Partial file — env fills missing fields ───────────────────────────────────

class TestPartialFileFallback:
    def test_slack_bot_from_file_app_from_env(self, auth_path):
        auth_path.write_text(json.dumps({'slack': {'bot_token': 'file-bot', 'app_token': ''}}))
        mgr = make_manager(auth_path, {'SLACK_APP_TOKEN': 'env-app'})
        assert mgr.slack.bot_token == 'file-bot'
        assert mgr.slack.app_token == 'env-app'

    def test_only_missing_fields_filled(self, auth_path):
        auth_path.write_text(json.dumps({'telegram': {'bot_token': 'file-tg'}}))
        mgr = make_manager(auth_path, {
            'TELEGRAM_BOT_TOKEN': 'env-tg',
            'DISCORD_BOT_TOKEN': 'env-dc',
        })
        assert mgr.telegram.bot_token == 'file-tg'   # file wins
        assert mgr.discord.bot_token == 'env-dc'      # env fills missing


# ── set_* still persists to file ─────────────────────────────────────────────

class TestSettersStillWork:
    def test_set_telegram_persists(self, auth_path):
        mgr = ChannelAuthManager(auth_path)
        mgr.set_telegram(bot_token='new-token')
        mgr2 = ChannelAuthManager(auth_path)
        assert mgr2.telegram.bot_token == 'new-token'

    def test_set_overrides_env(self, auth_path):
        mgr = make_manager(auth_path, {'DISCORD_BOT_TOKEN': 'env-val'})
        mgr.set_discord(bot_token='set-val')
        mgr2 = make_manager(auth_path, {'DISCORD_BOT_TOKEN': 'env-val'})
        assert mgr2.discord.bot_token == 'set-val'
