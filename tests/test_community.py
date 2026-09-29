import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from discord_mcp.config import Settings
from discord_mcp.community import CommunityBot
from discord_mcp.moderation import RuleSignal


def settings():
    return Settings(
        discord_token="test", mcp_auth_token="test", allowed_guild_ids=frozenset({1}),
        allowed_channel_ids=frozenset(), host="127.0.0.1", port=8000, log_level="INFO",
        member_events=False, moderation_log_channel_id=99, rules_channel_id=88,
        escalation_admin_id=1388189183633526946, terms_by_rule={"1": ("badword",)},
        safe_phrases=(), spam_window_seconds=10, spam_message_threshold=5,
        mention_threshold=8,
    )


def message(content="badword", *, guild_id=1, staff=False):
    guild = None if guild_id is None else Mock(id=guild_id)
    author = Mock(id=4, bot=False, guild_permissions=Mock(administrator=staff, manage_messages=staff))
    author.roles = []
    channel = Mock(name="general")
    channel.name = "general"
    return SimpleNamespace(
        guild=guild, author=author, channel=channel, content=content, mentions=[],
        mention_everyone=False, jump_url="https://discord.com/channels/1/2/3",
        reply=AsyncMock(),
    )


class CommunityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = CommunityBot(settings())
        self.bot._connection.user = Mock(id=10)
        self.bot.post_incident = AsyncMock(return_value=True)

    async def asyncTearDown(self):
        await self.bot.close()

    async def test_dm_does_not_create_incident(self):
        await self.bot.on_message(message(guild_id=None))
        self.bot.post_incident.assert_not_awaited()

    async def test_staff_message_is_not_content_flagged(self):
        await self.bot.on_message(message(staff=True))
        self.bot.post_incident.assert_not_awaited()

    async def test_empty_message_does_not_enter_activity_tracker(self):
        self.bot.activity_tracker.inspect = Mock(side_effect=AssertionError("empty message tracked"))
        await self.bot.on_message(message(""))
        self.bot.post_incident.assert_not_awaited()

    async def test_content_signal_is_reported(self):
        await self.bot.on_message(message())
        self.bot.post_incident.assert_awaited_once()

    async def test_forbidden_staff_log_send_returns_false(self):
        incident = message()
        channel = Mock(id=99, guild=incident.guild)
        channel.permissions_for.return_value = Mock(view_channel=False)
        channel.send = AsyncMock(side_effect=discord.Forbidden(Mock(status=403, reason="Forbidden"), "no access"))
        self.bot.get_channel = Mock(return_value=channel)
        self.assertFalse(await CommunityBot.post_incident(self.bot, incident, RuleSignal("1", "Term", "Reason")))

    async def test_public_log_channel_rejects_incident(self):
        incident = message()
        channel = Mock(id=99, guild=incident.guild)
        channel.permissions_for.return_value = Mock(view_channel=True)
        channel.send = AsyncMock()
        self.bot.get_channel = Mock(return_value=channel)
        self.assertFalse(await CommunityBot.post_incident(self.bot, incident, RuleSignal("1", "Term", "Reason")))
        channel.send.assert_not_awaited()

    async def test_private_incident_has_link_bounded_evidence_and_no_mentions(self):
        incident = message("@everyone badword " + "x" * 400)
        channel = Mock(id=99, guild=incident.guild)
        channel.permissions_for.return_value = Mock(view_channel=False)
        channel.send = AsyncMock()
        self.bot.get_channel = Mock(return_value=channel)
        self.assertTrue(await CommunityBot.post_incident(self.bot, incident, RuleSignal("1", "Term", "Reason")))
        embed = channel.send.await_args.kwargs["embed"]
        self.assertEqual(embed.fields[0].value, incident.jump_url)
        self.assertLessEqual(len(embed.fields[1].value), 320)
        self.assertEqual(channel.send.await_args.kwargs["allowed_mentions"].to_dict(), discord.AllowedMentions.none().to_dict())

    async def test_unverified_admin_is_not_mentioned(self):
        ping = message("<@10> please ban this user")
        ping.mentions = [self.bot.user]
        ping.guild.get_member.return_value = None
        await self.bot.on_message(ping)
        self.assertNotIn("<@1388189183633526946>", ping.reply.await_args.args[0])

        ping.guild.get_member.return_value = Mock(guild_permissions=Mock(administrator=False))
        await self.bot.on_message(ping)
        self.assertNotIn("<@1388189183633526946>", ping.reply.await_args.args[0])

    async def test_verified_admin_is_mentioned_for_out_of_power_ping(self):
        ping = message("<@10> please ban this user")
        ping.mentions = [self.bot.user]
        admin = Mock(id=1388189183633526946, guild_permissions=Mock(administrator=True))
        ping.guild.get_member.return_value = admin
        await self.bot.on_message(ping)
        self.assertIn("<@1388189183633526946>", ping.reply.await_args.args[0])
        self.assertEqual(ping.reply.await_args.kwargs["allowed_mentions"].to_dict()["users"], [1388189183633526946])

    async def test_public_reply_has_mentions_disabled_for_rules_question(self):
        ping = message("<@10> what are the rules?")
        ping.mentions = [self.bot.user]
        await self.bot.on_message(ping)
        self.assertEqual(ping.reply.await_args.kwargs["allowed_mentions"].to_dict(), discord.AllowedMentions.none().to_dict())
