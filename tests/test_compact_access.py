import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from discord_mcp.community import CommunityBot
from test_community import settings


def test_private_ticket_excludes_developer_role():
    async def run():
        bot = CommunityBot(settings())
        roles = [Mock(name=name) for name in ('Owner', 'Admin', 'Moderator', 'Developer')]
        for role, name in zip(roles, ('Owner', 'Admin', 'Moderator', 'Developer')):
            role.name = name
        category = SimpleNamespace(id=5, name='── TICKETS ──')
        channel = Mock(mention='<#6>', send=AsyncMock())
        guild = SimpleNamespace(id=1, roles=roles, categories=[category], channels=[],
                                default_role=Mock(), me=Mock(), create_text_channel=AsyncMock(return_value=channel))
        member = Mock(spec=discord.Member, id=4, display_name='reader', mention='<@4>')
        interaction = SimpleNamespace(guild=guild, user=member, response=SimpleNamespace(send_message=AsyncMock()))
        await bot.open_ticket(interaction, 'report')
        overwrites = guild.create_text_channel.await_args.kwargs['overwrites']
        assert roles[3] not in overwrites
        assert all(role in overwrites for role in roles[:3])
        assert overwrites[guild.default_role].view_channel is False
        assert overwrites[member].view_channel is True
        await bot.close()
    asyncio.run(run())


def test_developer_cannot_close_someone_elses_ticket():
    async def run():
        bot = CommunityBot(settings())
        developer = Mock(name='Developer')
        developer.name = 'Developer'
        member = Mock(spec=discord.Member, id=4, roles=[developer], guild_permissions=discord.Permissions(manage_messages=True))
        channel = Mock(spec=discord.TextChannel, topic='ticket-owner:7;type:report', delete=AsyncMock())
        channel.category = SimpleNamespace(name='── TICKETS ──')
        interaction = SimpleNamespace(guild=SimpleNamespace(id=1), channel=channel, user=member,
                                      response=SimpleNamespace(send_message=AsyncMock()))
        await bot.close_ticket(interaction)
        channel.delete.assert_not_awaited()
        await bot.close()
    asyncio.run(run())


def test_join_notice_links_to_existing_general():
    async def run():
        bot = CommunityBot(settings())
        welcome = SimpleNamespace(name='👋・welcome', send=AsyncMock())
        general = SimpleNamespace(name='general', mention='<#22>')
        guild = SimpleNamespace(id=1, roles=[], text_channels=[welcome, general])
        member = SimpleNamespace(guild=guild, mention='<@4>')
        await bot.on_member_join(member)
        description = welcome.send.await_args.kwargs['embed'].description
        assert '<#22>' in description
        assert '#introductions' not in description
        await bot.close()
    asyncio.run(run())
