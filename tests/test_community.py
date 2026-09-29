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
    if guild is not None:
        guild.default_role = Mock(id=guild_id)
        guild.roles = [guild.default_role]
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

    async def test_uncached_user_message_is_not_content_flagged(self):
        item = message()
        item.author = discord.User(state=Mock(), data={"id": "4", "username": "reader", "discriminator": "0", "avatar": None})
        item.guild.get_member.return_value = None
        await self.bot.on_message(item)
        self.bot.post_incident.assert_not_awaited()

    async def test_empty_message_does_not_enter_activity_tracker(self):
        self.bot.activity_tracker.inspect = Mock(side_effect=AssertionError("empty message tracked"))
        await self.bot.on_message(message(""))
        self.bot.post_incident.assert_not_awaited()

    async def test_content_signal_is_reported(self):
        await self.bot.on_message(message())
        self.bot.post_incident.assert_awaited_once()

    async def test_activity_does_not_cross_guilds(self):
        self.bot.settings = Settings(**{**self.bot.settings.__dict__, "allowed_guild_ids": frozenset({1, 2}), "spam_message_threshold": 3})
        self.bot.activity_tracker.message_threshold = 3
        self.bot.activity_tracker.repeat_threshold = 3
        for guild_id in (1, 1, 2):
            await self.bot.on_message(message("ordinary", guild_id=guild_id))
        self.bot.post_incident.assert_not_awaited()

    async def test_forbidden_staff_log_send_returns_false(self):
        incident = message()
        channel = Mock(id=99, guild=incident.guild, overwrites={})
        channel.permissions_for.return_value = Mock(view_channel=False)
        channel.send = AsyncMock(side_effect=discord.Forbidden(Mock(status=403, reason="Forbidden"), "no access"))
        self.bot.get_channel = Mock(return_value=channel)
        self.assertFalse(await CommunityBot.post_incident(self.bot, incident, RuleSignal("1", "Term", "Reason")))

    async def test_public_log_channel_rejects_incident(self):
        incident = message()
        channel = Mock(id=99, guild=incident.guild, overwrites={})
        channel.permissions_for.return_value = Mock(view_channel=True)
        channel.send = AsyncMock()
        self.bot.get_channel = Mock(return_value=channel)
        self.assertFalse(await CommunityBot.post_incident(self.bot, incident, RuleSignal("1", "Term", "Reason")))
        channel.send.assert_not_awaited()

    async def test_log_visible_to_nonstaff_role_rejects_incident(self):
        incident = message()
        role = Mock(id=5, name="Community", permissions=Mock(administrator=False, manage_messages=False))
        role.name = "Community"
        incident.guild.roles = [role]
        incident.guild.default_role = Mock(id=1)
        channel = Mock(id=99, guild=incident.guild, overwrites={})
        channel.permissions_for.side_effect = lambda subject: Mock(view_channel=subject is role)
        channel.send = AsyncMock()
        self.bot.get_channel = Mock(return_value=channel)
        self.assertFalse(await CommunityBot.post_incident(self.bot, incident, RuleSignal("1", "Term", "Reason")))
        channel.send.assert_not_awaited()

    async def test_role_change_ping_escalates_to_verified_admin(self):
        ping = message("<@10> please change this user's role permissions")
        ping.mentions = [self.bot.user]
        ping.guild.get_member.return_value = Mock(id=1388189183633526946, guild_permissions=Mock(administrator=True))
        await self.bot.on_message(ping)
        self.assertIn("<@1388189183633526946>", ping.reply.await_args.args[0])

    async def test_log_visible_to_nonstaff_member_rejects_incident(self):
        incident = message()
        member = Mock(id=7, guild_permissions=Mock(administrator=False, manage_messages=False), roles=[])
        channel = Mock(id=99, guild=incident.guild, overwrites={member: discord.PermissionOverwrite(view_channel=True)})
        channel.permissions_for.return_value = Mock(view_channel=False)
        channel.send = AsyncMock()
        self.bot.get_channel = Mock(return_value=channel)
        self.assertFalse(await CommunityBot.post_incident(self.bot, incident, RuleSignal("1", "Term", "Reason")))
        channel.send.assert_not_awaited()

    async def test_log_allows_own_bot_member_overwrite(self):
        incident = message()
        bot_member = Mock(id=10, guild_permissions=Mock(administrator=False, manage_messages=False), roles=[])
        incident.guild.me = bot_member
        channel = Mock(id=99, guild=incident.guild, overwrites={bot_member: discord.PermissionOverwrite(view_channel=True)})
        channel.permissions_for.return_value = Mock(view_channel=False)
        channel.send = AsyncMock()
        self.bot.get_channel = Mock(return_value=channel)
        self.assertTrue(await CommunityBot.post_incident(self.bot, incident, RuleSignal("1", "Term", "Reason")))

    async def test_private_incident_has_link_bounded_evidence_and_no_mentions(self):
        incident = message("@everyone badword " + "x" * 400)
        channel = Mock(id=99, guild=incident.guild, overwrites={})
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
