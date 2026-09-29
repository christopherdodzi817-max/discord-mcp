import asyncio
from datetime import datetime, timezone
from functools import wraps
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from discord_mcp.config import Settings
from discord_mcp.discord_service import DiscordService
from discord_mcp.server import create_mcp
from mcp.server.fastmcp.exceptions import ToolError


def async_test(test):
    @wraps(test)
    def run():
        asyncio.run(test())
    return run


def settings(*, channels=frozenset(), guilds=frozenset({1})):
    return Settings(
        discord_token="test", mcp_auth_token="test", allowed_guild_ids=guilds,
        allowed_channel_ids=channels, host="127.0.0.1", port=8000,
        log_level="INFO", moderation_log_channel_id=99,
        terms_by_rule={"1": ("badword",)}, spam_message_threshold=3,
    )


def setup_server(config, guild, channels=()):
    guild.create_text_channel = AsyncMock()
    service = Mock(spec=DiscordService)
    service.settings = config
    service.bot = Mock()
    service.bot.user = Mock(id=10)
    service.bot.get_guild.side_effect = lambda guild_id: guild if guild_id == guild.id else None
    service.bot.get_channel.side_effect = lambda channel_id: next((c for c in channels if c.id == channel_id), None)
    service.require_guild.side_effect = lambda guild_id: DiscordService.require_guild(service, guild_id)
    service.require_channel.side_effect = lambda channel_id: DiscordService.require_channel(service, channel_id)
    return create_mcp(config, service), service


def channel(channel_id, guild, messages):
    result = Mock(id=channel_id, guild=guild, name="general")
    result.name = "general"
    result.send = AsyncMock()
    result.edit = AsyncMock()
    result.delete = AsyncMock()
    result.requested_limits = []

    def history(*, limit):
        result.requested_limits.append(limit)

        async def iterate():
            for message in messages[:limit]:
                yield message

        return iterate()

    result.history = history
    return result


def assert_read_only(channels=(), guild=None):
    for item in channels:
        item.send.assert_not_awaited()
        item.edit.assert_not_awaited()
        item.delete.assert_not_awaited()
    if guild is not None:
        guild.create_text_channel.assert_not_awaited()


@async_test
async def test_server_stats_rejects_other_guild_and_marks_incomplete_cache_approximate():
    guild = Mock(id=1, member_count=100, members=[Mock(bot=False), Mock(bot=True)])
    guild.text_channels, guild.voice_channels, guild.forums, guild.categories, guild.roles = [1], [1], [], [], [1]
    guild.chunked = False
    guild.premium_subscription_count = 2
    mcp, service = setup_server(settings(), guild)
    service.bot.is_ready.return_value = True

    with pytest.raises(ToolError, match="ALLOWED_GUILD_IDS"):
        await mcp._tool_manager.call_tool("get_server_stats", {"server_id": "2"})
    stats = await mcp._tool_manager.call_tool("get_server_stats", {"server_id": "1"})
    assert stats["total_members"] == 100
    assert stats["cached_humans"] == 1 and stats["cached_bots"] == 1
    assert stats["exact"] is False
    assert stats["text_channels"] == 1 and stats["boost_count"] == 2
    assert_read_only(guild=guild)


@async_test
async def test_moderation_flags_requires_allowlisted_log_and_headmod_marker():
    guild = Mock(id=1)
    marked = discord.Embed(title="Possible rule 1 concern", description="**Term**\nReason")
    marked.add_field(name="Message", value="https://discord.com/channels/1/2/3")
    marked.add_field(name="Evidence excerpt", value="badword")
    marked.add_field(name="Author ID", value="4")
    marked.set_footer(text="HeadMod incident v1 - Staff review required")
    ordinary = discord.Embed(title="Possible rule 1 concern")
    log = channel(99, guild, [SimpleNamespace(id=2, author=Mock(id=4), embeds=[ordinary], created_at=datetime.now(timezone.utc)),
                              SimpleNamespace(id=1, author=Mock(id=10), embeds=[marked], created_at=datetime.now(timezone.utc))])
    mcp, _ = setup_server(settings(channels=frozenset({98})), guild, [log])
    with pytest.raises(ToolError, match="ALLOWED_CHANNEL_IDS"):
        await mcp._tool_manager.call_tool("list_moderation_flags", {"server_id": "1"})

    mcp, _ = setup_server(settings(channels=frozenset({99})), guild, [log])
    flags = await mcp._tool_manager.call_tool("list_moderation_flags", {"server_id": "1", "limit": 500})
    assert log.requested_limits == [50]
    assert len(flags) == 1 and flags[0]["message_id"] == "1"
    assert_read_only([log], guild)


@async_test
async def test_flags_ignore_copied_marker_and_bound_results():
    guild = Mock(id=1)
    marked = discord.Embed(title="Possible rule 1 concern")
    marked.set_footer(text="HeadMod incident v1 - Staff review required")
    now = datetime.now(timezone.utc)
    log = channel(99, guild, [
        SimpleNamespace(id=1, author=Mock(id=4), embeds=[marked], created_at=now),
        SimpleNamespace(id=2, author=Mock(id=10), embeds=[marked, marked, marked], created_at=now),
    ])
    mcp, _ = setup_server(settings(), guild, [log])
    flags = await mcp._tool_manager.call_tool("list_moderation_flags", {"server_id": "1", "limit": 1})
    assert flags == []  # The only fetched message copied the marker from another author.
    flags = await mcp._tool_manager.call_tool("list_moderation_flags", {"server_id": "1", "limit": 2})
    assert len(flags) == 2  # A message with several embeds cannot exceed the requested bound.
    assert all(flag["message_id"] == "2" for flag in flags)
    assert_read_only([log], guild)


@async_test
async def test_recent_audit_is_chronological_and_does_not_post():
    guild = Mock(id=1)
    other = Mock(id=2)
    now = datetime.now(timezone.utc)
    def message(mid, content, at):
        return SimpleNamespace(id=mid, author=Mock(id=4, bot=False, guild_permissions=Mock(administrator=False, manage_messages=False), roles=[]),
                               content=content, created_at=at, mentions=[], mention_everyone=False,
                               jump_url=f"https://discord.com/channels/1/10/{mid}",
                               delete=AsyncMock(), edit=AsyncMock())
    from datetime import timedelta
    first = message(1, "badword", now)
    second = message(2, "join discord.gg/example", now + timedelta(seconds=1))
    safe = channel(10, guild, [second, first])
    foreign = channel(11, other, [first])
    mcp, _ = setup_server(settings(), guild, [safe, foreign])
    with pytest.raises(ToolError, match="allowlisted server"):
        await mcp._tool_manager.call_tool("audit_recent_messages", {"channel_id": "11"})
    flags = await mcp._tool_manager.call_tool("audit_recent_messages", {"channel_id": "10", "limit": 500})
    assert safe.requested_limits == [50]
    assert [flag["message_id"] for flag in flags] == ["1", "2"]
    assert [flag["rule_id"] for flag in flags] == ["1", "6"]
    assert_read_only([safe, foreign], guild)
    for item in (first, second):
        item.delete.assert_not_awaited()
        item.edit.assert_not_awaited()


@async_test
async def test_server_audit_reports_everyone_administrator_without_mutation():
    guild = Mock(id=1)
    guild.default_role = Mock(id=1, name="@everyone", permissions=Mock(administrator=True, manage_roles=False))
    guild.default_role.edit = AsyncMock()
    guild.roles = [guild.default_role]
    guild.channels = []
    guild.me = Mock(guild_permissions=Mock(view_channel=True, read_message_history=True, send_messages=True, manage_channels=True))
    mcp, _ = setup_server(settings(), guild)
    findings = await mcp._tool_manager.call_tool("audit_server", {"server_id": "1"})
    assert any("@everyone" in finding["evidence"] and finding["severity"] for finding in findings)
    assert_read_only(guild=guild)
    guild.default_role.edit.assert_not_awaited()


@async_test
async def test_server_audit_checks_staff_log_and_bot_role_hierarchy():
    guild = Mock(id=1)
    guild.default_role = Mock(id=1, name="@everyone", permissions=Mock(administrator=False, manage_roles=False))
    guild.default_role.name = "@everyone"
    guild.roles = [guild.default_role, Mock(id=5, name="Creator Interest", position=5,
                                           permissions=Mock(administrator=False, manage_roles=False))]
    guild.roles[1].name = "Creator Interest"
    guild.me = Mock(top_role=Mock(position=3), guild_permissions=Mock(view_channel=True, read_message_history=True,
                                                                  send_messages=True))
    log = channel(99, guild, [])
    log.permissions_for.side_effect = lambda subject: (
        Mock(view_channel=True, manage_channels=False, manage_messages=False)
        if subject is guild.default_role else
        Mock(view_channel=False, read_message_history=False, send_messages=False, embed_links=False)
    )
    guild.channels = [log]
    mcp, _ = setup_server(settings(), guild, [log])
    findings = await mcp._tool_manager.call_tool("audit_server", {"server_id": "1"})
    evidence = " ".join(item["evidence"] for item in findings)
    assert "visible to @everyone" in evidence
    assert "lacks view_channel" in evidence
    assert "lacks read_message_history" in evidence
    assert "lacks send_messages" in evidence
    assert "lacks embed_links" in evidence
    assert "Creator Interest" in evidence and "above" in evidence
    assert_read_only([log], guild)


@async_test
async def test_server_audit_reports_missing_or_wrong_guild_log():
    guild = Mock(id=1, roles=[], channels=[], me=None)
    missing, _ = setup_server(settings(), guild)
    findings = await missing._tool_manager.call_tool("audit_server", {"server_id": "1"})
    assert any("unavailable" in item["evidence"] for item in findings)

    other = Mock(id=2)
    foreign_log = channel(99, other, [])
    guild.channels = [foreign_log]
    wrong, _ = setup_server(settings(), guild, [foreign_log])
    findings = await wrong._tool_manager.call_tool("audit_server", {"server_id": "1"})
    assert any("another server" in item["evidence"] for item in findings)
    assert_read_only([foreign_log], guild)


@async_test
async def test_recent_audit_skips_uncached_user_and_bounds_escaped_evidence():
    guild = Mock(id=1)
    guild.get_member.return_value = None
    user = discord.User(state=Mock(), data={"id": "4", "username": "reader", "discriminator": "0", "avatar": None})
    now = datetime.now(timezone.utc)
    uncached = SimpleNamespace(id=1, author=user, content="badword", created_at=now, mentions=[], mention_everyone=False,
                               jump_url="https://discord.com/channels/1/10/1")
    member = Mock(id=5, bot=False, guild_permissions=Mock(administrator=False, manage_messages=False), roles=[])
    known = SimpleNamespace(id=2, author=member, content="@everyone **badword** " + "*" * 400,
                            created_at=now, mentions=[], mention_everyone=False,
                            jump_url="https://discord.com/channels/1/10/2")
    safe = channel(10, guild, [uncached, known])
    mcp, _ = setup_server(settings(), guild, [safe])
    findings = await mcp._tool_manager.call_tool("audit_recent_messages", {"channel_id": "10"})
    assert [finding["message_id"] for finding in findings] == ["2"]
    assert len(findings[0]["evidence_excerpt"]) <= 300
    assert "@everyone" not in findings[0]["evidence_excerpt"]
    assert "\\*\\*" in findings[0]["evidence_excerpt"]


@async_test
async def test_recent_audit_resolves_uncached_author_to_nonstaff_member():
    guild = Mock(id=1)
    user = discord.User(state=Mock(), data={"id": "4", "username": "reader", "discriminator": "0", "avatar": None})
    guild.get_member.return_value = Mock(id=4, guild_permissions=Mock(administrator=False, manage_messages=False), roles=[])
    item = SimpleNamespace(id=1, author=user, content="badword", created_at=datetime.now(timezone.utc),
                           mentions=[], mention_everyone=False, jump_url="https://discord.com/channels/1/10/1")
    safe = channel(10, guild, [item])
    mcp, _ = setup_server(settings(), guild, [safe])
    findings = await mcp._tool_manager.call_tool("audit_recent_messages", {"channel_id": "10"})
    assert len(findings) == 1 and findings[0]["rule_id"] == "1"
    guild.get_member.assert_called_once_with(4)


@async_test
async def test_server_audit_skips_excluded_channels_and_log_details():
    guild = Mock(id=1, roles=[], me=None)
    guild.default_role = Mock(id=1, permissions=Mock(administrator=False, manage_roles=False))
    excluded = channel(99, guild, [])
    excluded.permissions_for.side_effect = AssertionError("excluded channel inspected")
    guild.channels = [excluded]
    mcp, service = setup_server(settings(channels=frozenset({10})), guild, [excluded])
    service.bot.get_channel.side_effect = AssertionError("excluded log looked up")
    findings = await mcp._tool_manager.call_tool("audit_server", {"server_id": "1"})
    assert len(findings) == 1
    assert "outside ALLOWED_CHANNEL_IDS" in findings[0]["evidence"]


@async_test
async def test_server_audit_reports_log_visible_to_nonstaff_role():
    guild = Mock(id=1, me=None)
    guild.default_role = Mock(id=1, name="@everyone", permissions=Mock(administrator=False, manage_roles=False, manage_messages=False))
    role = Mock(id=5, name="Community", permissions=Mock(administrator=False, manage_roles=False, manage_messages=False))
    role.name = "Community"
    guild.roles = [guild.default_role, role]
    log = channel(99, guild, [])
    log.overwrites = {}
    log.permissions_for.side_effect = lambda subject: Mock(view_channel=subject is role, manage_channels=False, manage_messages=False)
    guild.channels = [log]
    mcp, _ = setup_server(settings(), guild, [log])
    findings = await mcp._tool_manager.call_tool("audit_server", {"server_id": "1"})
    assert any("Community" in item["evidence"] and "visible" in item["evidence"] for item in findings)
