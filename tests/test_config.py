import unittest
from unittest.mock import patch

from discord_mcp.config import Settings


class SettingsTests(unittest.TestCase):
    def env(self, **overrides):
        values = {
            "DISCORD_BOT_TOKEN": "test-token", "MCP_AUTH_TOKEN": "test-auth",
            "ALLOWED_GUILD_IDS": "123", "ALLOWED_CHANNEL_IDS": "",
            "MODERATION_TERMS_RULE_1": "one,two", "SPAM_WINDOW_SECONDS": "10",
            "SPAM_MESSAGE_THRESHOLD": "5", "MENTION_THRESHOLD": "8",
        }
        values.update(overrides)
        return patch.dict("os.environ", values, clear=True)

    def test_parses_terms_and_positive_thresholds(self):
        with self.env():
            settings = Settings.from_env()
        self.assertEqual(settings.terms_by_rule["1"], ("one", "two"))
        self.assertEqual(settings.spam_window_seconds, 10)
        self.assertEqual(settings.spam_message_threshold, 5)
        self.assertEqual(settings.mention_threshold, 8)

    def test_rejects_nonnumeric_escalation_admin(self):
        with self.env(ESCALATION_ADMIN_ID="somebody"):
            with self.assertRaisesRegex(ValueError, "ESCALATION_ADMIN_ID"):
                Settings.from_env()

    def test_rejects_zero_spam_threshold(self):
        with self.env(SPAM_MESSAGE_THRESHOLD="0"):
            with self.assertRaisesRegex(ValueError, "SPAM_MESSAGE_THRESHOLD"):
                Settings.from_env()

    def test_rejects_nonfinite_spam_window(self):
        for value in ("nan", "inf", "-inf"):
            with self.subTest(value=value), self.env(SPAM_WINDOW_SECONDS=value):
                with self.assertRaisesRegex(ValueError, "SPAM_WINDOW_SECONDS"):
                    Settings.from_env()
