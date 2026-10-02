import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
import httpx

from discord_mcp.app import create_app
from discord_mcp.community import CommunityBot
from discord_mcp.config import Settings
from test_community import settings, message


def ping(message_id, *, staff=False, author_id=4, target_id=7, target_bot=False, at=None):
    item = message(f'<@{target_id}> hello {message_id}', staff=staff)
    item.id = message_id
    item.author.id = author_id
    item.mentions = [Mock(id=target_id, bot=target_bot)]
    item.created_at = datetime.fromtimestamp(900 + message_id if at is None else at, timezone.utc)
    return item


async def history(items):
    for item in items:
        yield item


class MentionWarningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = CommunityBot(settings())
        self.bot._connection.user = Mock(id=10)
        self.bot.post_incident = AsyncMock(return_value=True)
        if hasattr(self.bot, '_mention_history_ready'):
            self.bot._mention_history_ready.set()

    async def asyncTearDown(self):
        await self.bot.close()

    async def send_pings(self, **kwargs):
        items = [ping(message_id, **kwargs) for message_id in range(1, 5)]
        with patch('discord_mcp.community.time.time', return_value=1000):
            for item in items:
                await self.bot.on_message(item)
        return items

    async def test_fourth_ping_warns_sender_without_pinging_target(self):
        items = await self.send_pings()
        for item in items[:3]:
            item.reply.assert_not_awaited()
        items[3].reply.assert_awaited_once()
        content = items[3].reply.await_args.args[0]
        self.assertIn('Warning', content)
        self.assertIn('<@4>', content)
        self.assertNotIn('<@7>', content)
        allowed = items[3].reply.await_args.kwargs['allowed_mentions'].to_dict()
        self.assertEqual(allowed['users'], [4])
        self.assertNotIn('everyone', allowed.get('parse', []))
        self.assertNotIn('roles', allowed.get('parse', []))
        self.assertFalse(items[3].reply.await_args.kwargs['mention_author'])
        signal = self.bot.post_incident.await_args.args[1]
        self.assertEqual(signal.label, 'Repeated member pings')

    async def test_staff_are_also_warned(self):
        items = await self.send_pings(staff=True)
        items[3].reply.assert_awaited_once()

    async def test_self_and_bot_targets_are_excluded(self):
        for kwargs in ({'author_id': 7}, {'target_bot': True}):
            items = await self.send_pings(**kwargs)
            for item in items:
                item.reply.assert_not_awaited()

    async def test_bot_authors_are_excluded(self):
        for message_id in range(1, 5):
            item = ping(message_id)
            item.author.bot = True
            await self.bot.on_message(item)
            item.reply.assert_not_awaited()

    async def test_hourly_warning_cooldown(self):
        await self.send_pings()
        item = ping(5)
        with patch('discord_mcp.community.time.time', return_value=1010):
            await self.bot.on_message(item)
        item.reply.assert_not_awaited()

    async def test_reply_failure_is_logged_and_does_not_crash(self):
        items = [ping(mid) for mid in range(1, 5)]
        items[-1].reply.side_effect = discord.Forbidden(Mock(status=403, reason='Forbidden'), 'no reply access')
        with patch('discord_mcp.community.time.time', return_value=1000):
            for item in items:
                await self.bot.on_message(item)
        items[-1].reply.assert_awaited_once()
        self.assertIn('could not', self.bot.post_incident.await_args.args[1].reason.lower())

    async def test_failed_reply_and_log_do_not_consume_warning(self):
        self.bot.post_incident.return_value = False
        items = [ping(mid) for mid in range(1, 5)]
        items[-1].reply.side_effect = discord.Forbidden(Mock(status=403, reason='Forbidden'), 'no reply access')
        with patch('discord_mcp.community.time.time', return_value=1000):
            for item in items:
                await self.bot.on_message(item)
            retry = ping(5)
            await self.bot.on_message(retry)
        retry.reply.assert_awaited_once()

    async def test_history_initializes_counts_without_replaying_warnings(self):
        self.assertTrue(hasattr(self.bot, '_restore_mention_history'))
        items = [ping(mid) for mid in range(1, 4)]
        guild = items[0].guild
        channel = SimpleNamespace(id=2, history=Mock(return_value=history(items)))
        guild.text_channels = [channel]
        guild.threads = []
        self.bot._connection._guilds = {1: guild}
        with patch('discord_mcp.community.time.time', return_value=1000):
            await self.bot._restore_mention_history()
            current = ping(4, at=1001)
            await self.bot.on_message(current)
        for item in items:
            item.reply.assert_not_awaited()
        current.reply.assert_awaited_once()
        self.assertTrue(self.bot._mention_history_complete)
        self.assertTrue(self.bot._mention_history_ready.is_set())

    async def test_history_failure_keeps_live_warning_processing_available(self):
        self.assertTrue(hasattr(self.bot, '_restore_mention_history'))
        guild = message().guild
        channel = SimpleNamespace(id=2, history=Mock(side_effect=discord.Forbidden(Mock(status=403, reason='Forbidden'), 'no access')))
        guild.text_channels = [channel]
        guild.threads = []
        self.bot._connection._guilds = {1: guild}
        with patch('discord_mcp.community.time.time', return_value=1000):
            await self.bot._restore_mention_history()
        self.assertFalse(self.bot._mention_history_complete)
        self.assertTrue(self.bot._mention_history_ready.is_set())

    async def test_private_log_restores_recent_warning_cooldown(self):
        self.assertTrue(hasattr(self.bot, '_restore_mention_history'))
        logged = ping(4)
        logged.author.id = 10
        logged.author.bot = True
        embed = discord.Embed(description='**Repeated member pings**\nWarning sent.')
        embed.add_field(name='Author ID', value='4')
        embed.set_footer(text='HeadMod incident v1 - Staff review required')
        logged.embeds = [embed]
        channel = SimpleNamespace(id=99, history=Mock(return_value=history([logged])))
        guild = logged.guild
        guild.text_channels = [channel]
        guild.threads = []
        self.bot._connection._guilds = {1: guild}
        with patch('discord_mcp.community.staff_log_privacy_issues', return_value=[]), patch('discord_mcp.community.time.time', return_value=1000):
            await self.bot._restore_mention_history()
            items = await self.send_pings()
        for item in items:
            item.reply.assert_not_awaited()

    async def test_health_reports_active_limits(self):
        service = SimpleNamespace(bot=self.bot)
        with patch('discord_mcp.app.DiscordService', return_value=service):
            app = create_app(self.bot.settings)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            result = (await client.get('/health')).json()
        self.assertEqual(result['mention_protection']['limit'], 3)
        self.assertEqual(result['mention_protection']['window_seconds'], 3600)
        self.assertTrue(result['mention_protection']['ready'])

    async def test_public_warning_recovers_cooldown_when_staff_log_failed(self):
        logged = ping(9, at=950)
        logged.author.id = 10
        logged.author.bot = True
        logged.content = '<@4> **Warning:** you have tagged the same member more than 3 times in the past 60 minutes. Please avoid repeated pings and use a ticket if you need help.'
        logged.embeds = []
        items = [ping(mid) for mid in range(1, 5)]
        guild = logged.guild
        guild.text_channels = [SimpleNamespace(id=2, history=Mock(return_value=history([*items, logged])))]
        guild.threads = []
        self.bot._connection._guilds = {1: guild}
        with patch('discord_mcp.community.time.time', return_value=1000):
            await self.bot._restore_mention_history()
            current = ping(5, at=1001)
            await self.bot.on_message(current)
        current.reply.assert_not_awaited()


class MentionSettingsTests(unittest.TestCase):
    def test_default_hourly_mention_limits(self):
        config = settings()
        self.assertEqual(getattr(config, 'repeated_mention_limit', None), 3)
        self.assertEqual(getattr(config, 'repeated_mention_window_seconds', None), 3600)

    def test_invalid_hourly_limit_rejected(self):
        with patch.dict('os.environ', {'DISCORD_BOT_TOKEN': 'test', 'MCP_AUTH_TOKEN': 'test', 'ALLOWED_GUILD_IDS': '1', 'REPEATED_MENTION_LIMIT': '0'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'REPEATED_MENTION_LIMIT'):
                Settings.from_env()
