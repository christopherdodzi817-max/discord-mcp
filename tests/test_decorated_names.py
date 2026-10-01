from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import asyncio

from discord_mcp.community import CommunityBot
from discord_mcp.moderation import find_invite_signal
from test_community import settings


def test_decorated_discovery_allows_invites():
    assert find_invite_signal('Visit https://discord.gg/example', '🌐・server-discovery') is None


def test_decorated_welcome_receives_join_notice():
    async def run():
        bot = CommunityBot(settings())
        welcome = SimpleNamespace(name='👋・welcome', send=AsyncMock())
        guild = SimpleNamespace(id=1, roles=[], text_channels=[welcome])
        member = SimpleNamespace(guild=guild, mention='<@4>')
        await bot.on_member_join(member)
        assert welcome.send.await_count == 1
        await bot.close()
    asyncio.run(run())


def test_decorated_roles_channel_receives_panel():
    async def run():
        bot = CommunityBot(settings())
        bot._connection.user = Mock(id=10)
        channel = Mock(name='roles')
        channel.name = '🏷️・roles'
        channel.send = AsyncMock()
        async def history(**kwargs):
            if False:
                yield None
        channel.history = history
        guild = SimpleNamespace(id=1, roles=[], text_channels=[channel])
        await bot.ensure_roles_panel(guild)
        assert channel.send.await_count == 1
        assert len(channel.send.call_args.kwargs['view'].children) == 3
        await bot.close()
    asyncio.run(run())
