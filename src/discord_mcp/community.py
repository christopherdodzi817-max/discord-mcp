from __future__ import annotations

import logging
import re
import time
import asyncio
from datetime import datetime, timezone
from collections.abc import Mapping

import discord
from discord import app_commands

from .config import Settings
from .moderation import ActivityTracker, MentionTracker, RuleSignal, find_invite_signal, find_term_signals
from .naming import channel_key


SELF_ASSIGNABLE_ROLES: tuple[tuple[str, str, str], ...] = (
    ("Creator Interest", "Tell the community you are interested in the Creator program.", "🎨"),
    ("Playtester Interest", "Show interest in playtests; staff approval is still required for access.", "🧪"),
    ("Early Supporter", "Support the community and unlock supporter perks.", "⭐"),
)

TICKET_TYPES: tuple[tuple[str, str, str, discord.ButtonStyle], ...] = (
    ("support", "Support", "📩", discord.ButtonStyle.primary),
    ("creator", "Creator Application", "🎥", discord.ButtonStyle.success),
    ("staff", "Staff Application", "🛡️", discord.ButtonStyle.secondary),
    ("report", "Report / Appeal", "⚠️", discord.ButtonStyle.danger),
)

STAFF_ROLE_NAMES = frozenset({"Moderator", "Developer", "Admin"})
TICKET_STAFF_ROLE_NAMES = frozenset({"Owner", "Admin", "Moderator"})


def is_ticket_staff(member: discord.Member) -> bool:
    return member.guild_permissions.administrator or any(
        role.name in TICKET_STAFF_ROLE_NAMES for role in member.roles
    )


def escape_evidence_excerpt(content: str) -> str:
    return discord.utils.escape_mentions(discord.utils.escape_markdown(content[:300]))[:300]


def is_staff_member(member: discord.Member) -> bool:
    permissions = getattr(member, "guild_permissions", None)
    if permissions is None:
        return False
    return bool(permissions.administrator or permissions.manage_messages) or any(
        role.name in STAFF_ROLE_NAMES for role in member.roles
    )


def is_staff_role(role: discord.Role) -> bool:
    permissions = role.permissions
    return role.name in STAFF_ROLE_NAMES or bool(permissions.administrator or permissions.manage_messages)


def staff_log_privacy_issues(channel: discord.TextChannel, guild: discord.Guild) -> list[str]:
    """Return visibility risks that make a moderation log unsafe for incident evidence."""
    issues = []
    if channel.permissions_for(guild.default_role).view_channel:
        issues.append("visible to @everyone")
    roles = guild.roles
    role_ids = {role.id for role in roles}
    for role in roles:
        if role.id != guild.default_role.id and not is_staff_role(role) and channel.permissions_for(role).view_channel:
            issues.append(f"visible to nonstaff role {role.name} ({role.id})")
    overwrites = channel.overwrites
    if not isinstance(overwrites, Mapping):
        issues.append("permission overwrites cannot be verified")
    else:
        for target, overwrite in overwrites.items():
            bot_member = guild.me
            if bot_member is not None and target.id == bot_member.id:
                continue
            if target.id not in role_ids and overwrite.view_channel is True and not is_staff_member(target):
                issues.append(f"visible to nonstaff member {target.id}")
    return issues


class RoleMenuView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        for name, description, emoji in SELF_ASSIGNABLE_ROLES:
            self.add_item(RoleToggleButton(name, description, emoji))


class RoleToggleButton(discord.ui.Button):
    def __init__(self, role_name: str, description: str, emoji: str) -> None:
        super().__init__(
            label=role_name,
            emoji=emoji,
            style=discord.ButtonStyle.secondary,
            custom_id=f"fishing:role:{role_name.lower().replace(' ', '-')}",
        )
        self.role_name = role_name
        self.description = description

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Use this in the server, not a DM.", ephemeral=True)
            return
        role = discord.utils.get(interaction.guild.roles, name=self.role_name)
        if role is None:
            await interaction.response.send_message(
                f"The **{self.role_name}** role is not configured yet.", ephemeral=True
            )
            return
        try:
            if role in interaction.user.roles:
                await interaction.user.remove_roles(role, reason="Self-serve community role removal")
                message = f"Removed **{self.role_name}** from your roles."
            else:
                await interaction.user.add_roles(role, reason="Self-serve community role selection")
                message = f"Added **{self.role_name}**. Welcome to that part of the community!"
        except discord.Forbidden:
            message = "I cannot manage that role yet. Move my bot role above the community roles in Server Settings → Roles."
        await interaction.response.send_message(message, ephemeral=True)


class TicketPanelView(discord.ui.View):
    def __init__(self, bot: "CommunityBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot
        for ticket_key, label, emoji, style in TICKET_TYPES:
            self.add_item(TicketButton(ticket_key, label, emoji, style, bot))


class TicketButton(discord.ui.Button):
    def __init__(
        self,
        ticket_key: str,
        label: str,
        emoji: str,
        style: discord.ButtonStyle,
        bot: "CommunityBot",
    ) -> None:
        super().__init__(label=label, emoji=emoji, style=style, custom_id=f"fishing:ticket:{ticket_key}")
        self.ticket_key = ticket_key
        self.bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.open_ticket(interaction, self.ticket_key)


class CloseTicketView(discord.ui.View):
    def __init__(self, bot: "CommunityBot") -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.add_item(CloseTicketButton(bot))


class CloseTicketButton(discord.ui.Button):
    def __init__(self, bot: "CommunityBot") -> None:
        super().__init__(label="Close Ticket", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="fishing:ticket:close")
        self.bot = bot

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.bot.close_ticket(interaction)


class CommunityBot(discord.Client):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = settings.member_events
        super().__init__(intents=intents)
        self.settings = settings
        self.tree = app_commands.CommandTree(self)
        self.log = logging.getLogger("discord-mcp.community")
        self._ticket_panels_ready = False
        self.activity_tracker = ActivityTracker(
            settings.spam_window_seconds, settings.spam_message_threshold, 3, settings.mention_threshold
        )
        self.mention_tracker = MentionTracker(settings.repeated_mention_limit, settings.repeated_mention_window_seconds)
        self._mention_lock = asyncio.Lock()
        self._mention_history_ready = asyncio.Event()
        self._mention_history_complete = False

        @self.tree.command(name="roles", description="Choose optional community roles and perks.")
        async def roles(interaction: discord.Interaction) -> None:
            if interaction.guild is None or interaction.guild.id not in settings.allowed_guild_ids:
                await interaction.response.send_message("This command is not available here.", ephemeral=True)
                return
            embed = discord.Embed(
                title="Choose your community roles",
                description=(
                    "Tap a role to add or remove it. These roles unlock relevant spaces and help people find you.\n\n"
                    "Staff roles are assigned by the team and cannot be self-selected."
                ),
                color=discord.Color.blurple(),
            )
            for name, description, emoji in SELF_ASSIGNABLE_ROLES:
                embed.add_field(name=f"{emoji} {name}", value=description, inline=False)
            await interaction.response.send_message(embed=embed, view=RoleMenuView(), ephemeral=True)

    async def setup_hook(self) -> None:
        self.add_view(RoleMenuView())
        self.add_view(TicketPanelView(self))
        self.add_view(CloseTicketView(self))
        for guild_id in self.settings.allowed_guild_ids:
            guild = discord.Object(id=guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)

    def is_staff(self, member: discord.Member) -> bool:
        return is_staff_member(member)

    def resolve_escalation_admin(self, guild: discord.Guild) -> discord.Member | None:
        member = guild.get_member(self.settings.escalation_admin_id)
        if member is None or member.id != self.settings.escalation_admin_id:
            return None
        return member if member.guild_permissions.administrator else None

    async def on_message(self, message: discord.Message) -> None:
        guild = message.guild
        if guild is None or guild.id not in self.settings.allowed_guild_ids or message.author.bot:
            return
        content = message.content or ""
        if not content.strip():
            return
        await self._check_repeated_mentions(message)
        if self.user is not None and any(user.id == self.user.id for user in message.mentions):
            await self._answer_ping(message)
        author = message.author
        if not hasattr(author, "guild_permissions"):
            author = guild.get_member(author.id)
        if author is None or not hasattr(author, "guild_permissions") or self.is_staff(author):
            return
        signals = find_term_signals(content, self.settings.terms_by_rule or {}, self.settings.safe_phrases)
        invite = find_invite_signal(content, message.channel.name)
        if invite is not None:
            signals.append(invite)
        signals.extend(self.activity_tracker.inspect(
            (guild.id, message.author.id), content, len(message.mentions), message.mention_everyone, time.monotonic()
        ))
        for signal in signals:
            await self.post_incident(message, signal)

    async def _check_repeated_mentions(self, message: discord.Message) -> None:
        targets = [user.id for user in message.mentions if not user.bot and user.id != message.author.id]
        if not targets:
            return
        await self._mention_history_ready.wait()
        async with self._mention_lock:
            now = time.time()
            triggered = self.mention_tracker.record(
                message.guild.id, message.author.id, message.id, targets,
                message.created_at.timestamp(), now,
            )
            if not triggered:
                return
            response = (
                f"<@{message.author.id}> **Warning:** you have tagged the same member more than "
                f"{self.settings.repeated_mention_limit} times in the past "
                f"{self.settings.repeated_mention_window_seconds / 60:g} minutes. "
                "Please avoid repeated pings and use a ticket if you need help."
            )
            sent = False
            try:
                await message.reply(
                    response, mention_author=False,
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False, roles=False, users=[message.author], replied_user=False,
                    ),
                )
                sent = True
            except discord.HTTPException:
                self.log.warning("Could not send repeated-ping warning in channel %s", message.channel.id)
            reason = (
                f"More than {self.settings.repeated_mention_limit} direct-ping messages within "
                f"{self.settings.repeated_mention_window_seconds:g} seconds; target IDs: "
                + ", ".join(str(target) for target in triggered) + ". "
                + ("Warning sent to the sender." if sent else "Could not send a public warning; staff review needed.")
            )
            logged = await self.post_incident(message, RuleSignal("3", "Repeated member pings", reason))
            if sent or logged:
                self.mention_tracker.mark_warned(message.guild.id, message.author.id, now)

    async def _restore_mention_history(self) -> None:
        """Recover the rolling window without replaying warnings for old messages."""
        self._mention_history_ready.clear()
        async with self._mention_lock:
            now = time.time()
            before = datetime.fromtimestamp(now, timezone.utc)
            after = datetime.fromtimestamp(now - self.settings.repeated_mention_window_seconds, timezone.utc)
            complete = True
            try:
                for guild in self.guilds:
                    if guild.id not in self.settings.allowed_guild_ids:
                        continue
                    channels = {channel.id: channel for channel in [*guild.text_channels, *guild.threads]}
                    for channel in channels.values():
                        try:
                            count = 0
                            async for message in channel.history(limit=1000, after=after, before=before):
                                count += 1
                                if message.author.bot:
                                    if self.user is not None and message.author.id == self.user.id:
                                        public_warning = re.match(
                                            r'^<@(\d+)> \*\*Warning:\*\* you have tagged the same member more than ',
                                            message.content or '',
                                        )
                                        if public_warning:
                                            self.mention_tracker.mark_warned(
                                                guild.id, int(public_warning.group(1)), message.created_at.timestamp(),
                                            )
                                    if (channel.id == self.settings.moderation_log_channel_id
                                            and self.user is not None and message.author.id == self.user.id
                                            and not staff_log_privacy_issues(channel, guild)):
                                        for embed in message.embeds:
                                            if ((embed.description or '').startswith('**Repeated member pings**')
                                                    and embed.footer.text == 'HeadMod incident v1 - Staff review required'):
                                                for field in embed.fields:
                                                    if field.name == 'Author ID' and field.value.isdecimal():
                                                        self.mention_tracker.mark_warned(guild.id, int(field.value), message.created_at.timestamp())
                                    continue
                                targets = [user.id for user in message.mentions if not user.bot]
                                self.mention_tracker.record(
                                    guild.id, message.author.id, message.id, targets,
                                    message.created_at.timestamp(), now, historical=True,
                                )
                            if count >= 1000:
                                complete = False
                                self.log.warning("Repeated-ping history exceeded recovery limit in channel %s", channel.id)
                        except discord.HTTPException:
                            complete = False
                            self.log.warning("Could not recover repeated-ping history in channel %s", channel.id)
                self._mention_history_complete = complete
            finally:
                self._mention_history_ready.set()

    async def _answer_ping(self, message: discord.Message) -> None:
        rules = f"<#{self.settings.rules_channel_id}>"
        response = f"I can point you to the published rules in {rules} and report possible concerns for staff review."
        allowed_mentions = discord.AllowedMentions.none()
        if re.search(r"\b(?:ban|kick|timeout|mute|delete|punish|role|roles|permission|permissions|promote|demote|grant|revoke)\b", message.content, re.IGNORECASE):
            admin = self.resolve_escalation_admin(message.guild)
            if admin is not None:
                response += f" A server administrator can decide on that request: <@{admin.id}>."
                allowed_mentions = discord.AllowedMentions(
                    everyone=False, roles=False, users=[admin], replied_user=False
                )
            else:
                response += " Please ask a server administrator to review that request."
        try:
            await message.reply(response, allowed_mentions=allowed_mentions, mention_author=False)
        except discord.HTTPException:
            self.log.warning("Could not answer HeadMod ping in channel %s", message.channel.id)

    async def post_incident(self, message: discord.Message, signal: RuleSignal) -> bool:
        guild = message.guild
        if guild is None or guild.id not in self.settings.allowed_guild_ids:
            return False
        channel_id = self.settings.moderation_log_channel_id
        if self.settings.allowed_channel_ids and channel_id not in self.settings.allowed_channel_ids:
            self.log.warning("Moderation log channel %s is not allowlisted", channel_id)
            return False
        channel = self.get_channel(channel_id)
        if channel is None or channel.guild.id != guild.id or not hasattr(channel, "send"):
            self.log.warning("Moderation log channel %s is unavailable in guild %s", channel_id, guild.id)
            return False
        privacy_issues = staff_log_privacy_issues(channel, guild)
        if privacy_issues:
            self.log.warning("Moderation log channel %s is unsafe (%s); refusing incident", channel_id, "; ".join(privacy_issues))
            return False
        excerpt = escape_evidence_excerpt(message.content)
        embed = discord.Embed(
            title=f"Possible rule {signal.rule_id} concern",
            description=f"**{discord.utils.escape_markdown(signal.label)}**\n{discord.utils.escape_markdown(signal.reason)}",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Message", value=message.jump_url, inline=False)
        embed.add_field(name="Evidence excerpt", value=excerpt or "[No text]", inline=False)
        embed.add_field(name="Author ID", value=str(message.author.id), inline=True)
        embed.set_footer(text="HeadMod incident v1 - Staff review required")
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            self.log.warning("Could not send moderation incident to channel %s", channel_id)
            return False
        return True

    async def open_ticket(self, interaction: discord.Interaction, ticket_key: str) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Use this in the server, not a DM.", ephemeral=True)
            return
        if interaction.guild.id not in self.settings.allowed_guild_ids:
            await interaction.response.send_message("Tickets are not available here.", ephemeral=True)
            return
        ticket_label = next((item[1] for item in TICKET_TYPES if item[0] == ticket_key), None)
        if ticket_label is None:
            await interaction.response.send_message("Unknown ticket type.", ephemeral=True)
            return

        guild = interaction.guild
        category = discord.utils.find(lambda item: channel_key(item.name) == "tickets", guild.categories)
        if category is None:
            await interaction.response.send_message("The ticket category is not ready yet.", ephemeral=True)
            return
        owner_marker = f"ticket-owner:{interaction.user.id};"
        existing = discord.utils.find(
            lambda channel: isinstance(channel, discord.TextChannel)
            and channel.category_id == category.id
            and (channel.topic or "").startswith(owner_marker),
            guild.channels,
        )
        if existing is not None:
            await interaction.response.send_message(f"You already have an open ticket: {existing.mention}", ephemeral=True)
            return

        safe_name = re.sub(r"[^a-z0-9-]", "", interaction.user.display_name.lower().replace(" ", "-"))
        channel_name = f"ticket-{ticket_key}-{safe_name or interaction.user.id}"[:90]
        overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
        }
        for role_name in TICKET_STAFF_ROLE_NAMES:
            role = discord.utils.get(guild.roles, name=role_name)
            if role is not None:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
        if guild.me is not None:
            overwrites[guild.me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True)
        try:
            channel = await guild.create_text_channel(
                channel_name,
                category=category,
                topic=f"{owner_marker}type:{ticket_key}",
                overwrites=overwrites,
                reason=f"{ticket_label} ticket opened by {interaction.user}",
            )
        except discord.Forbidden:
            await interaction.response.send_message("I need Manage Channels permission to open tickets.", ephemeral=True)
            return

        prompts = {
            "support": "Describe what you need help with and include screenshots or error details if useful.",
            "creator": "Send your YouTube, TikTok, Twitch, Kick, or other public creator links, plus one recent example and what you want to create for the community.",
            "staff": "Tell us your timezone, moderation/community experience, availability, and why you want to help. Do not share private information.",
            "report": "Describe what happened, include relevant message links/screenshots, and avoid posting private information you do not own.",
        }
        embed = discord.Embed(
            title=f"{ticket_label} Ticket",
            description=prompts[ticket_key],
            color=discord.Color.blurple(),
        )
        await channel.send(content=interaction.user.mention, embed=embed, view=CloseTicketView(self))
        await interaction.response.send_message(f"Your private ticket is ready: {channel.mention}", ephemeral=True)

    async def close_ticket(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if interaction.guild is None or not isinstance(channel, discord.TextChannel) or channel.category is None or channel_key(channel.category.name) != "tickets":
            await interaction.response.send_message("This button only works inside a ticket.", ephemeral=True)
            return
        owner_id = (channel.topic or "").split(";", 1)[0].removeprefix("ticket-owner:")
        if str(interaction.user.id) != owner_id and not (isinstance(interaction.user, discord.Member) and is_ticket_staff(interaction.user)):
            await interaction.response.send_message("Only the ticket owner or staff can close this ticket.", ephemeral=True)
            return
        await interaction.response.send_message("Closing ticket…", ephemeral=True)
        await channel.delete(reason=f"Ticket closed by {interaction.user}")

    async def ensure_roles_panel(self, guild: discord.Guild) -> None:
        channel = discord.utils.find(lambda item: channel_key(item.name) == "roles", guild.text_channels)
        if channel is None:
            return
        marker = "HeadMod role directory v1"
        embed = discord.Embed(title="🏷️ Server roles", color=discord.Color.blurple(), description=(
            "**Team roles**\n👑 Owner — server ownership and final decisions.\n"
            "🛡️ Admin — server administration.\n🔨 Moderator — community safety and support.\n"
            "🛠️ Developer — development room and approved playtests; no access to private support reports.\n\n"
            "Team roles are assigned by the server owner or authorized administrators.\n\n"
            "**Community roles**\n🎨 Creator — approved creator lounge access.\n🧪 Playtester — approved playtesting room access.\n"
            "🎣 Community Member — regular member.\n\n"
            "**Choose optional roles below**\n🎨 Creator Interest • 🧪 Playtester Interest • ⭐ Early Supporter\n"
            "Tap a button to add or remove its role. Early Supporter opens the perks lounge. "
            "Interest roles do not grant staff or approved-program access."
        ))
        embed.set_footer(text=marker)
        try:
            existing = None
            async for message in channel.history(limit=25):
                if message.author.id == self.user.id and any(item.footer.text == marker for item in message.embeds):
                    existing = message
                    break
            if existing is not None:
                await existing.edit(embed=embed, view=RoleMenuView(), allowed_mentions=discord.AllowedMentions.none())
            else:
                await channel.send(embed=embed, view=RoleMenuView(), allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            self.log.warning("Could not publish roles panel in channel %s", channel.id)

    async def on_ready(self) -> None:
        await self._restore_mention_history()
        if self._ticket_panels_ready:
            return
        self._ticket_panels_ready = True
        for guild in self.guilds:
            if guild.id not in self.settings.allowed_guild_ids:
                continue
            await self.ensure_roles_panel(guild)
            panel_channel = discord.utils.find(
                lambda channel: isinstance(channel, discord.TextChannel) and channel_key(channel.name) == "open-a-ticket",
                guild.text_channels,
            )
            if panel_channel is None:
                continue
            try:
                recent = [message async for message in panel_channel.history(limit=25)]
                has_panel = any(
                    message.author.id == self.user.id
                    and any(component.custom_id == "fishing:ticket:support" for row in message.components for component in row.children)
                    for message in recent
                )
                if not has_panel:
                    embed = discord.Embed(
                        title="Open a private ticket",
                        description="Choose what you need. Only you and the staff team can see the ticket.",
                        color=discord.Color.blurple(),
                    )
                    await panel_channel.send(embed=embed, view=TicketPanelView(self))
            except discord.Forbidden:
                self.log.warning("Could not create the ticket panel in #%s", panel_channel.name)

    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild.id not in self.settings.allowed_guild_ids:
            return
        member_role = discord.utils.get(member.guild.roles, name="Community Member")
        if member_role is not None:
            try:
                await member.add_roles(member_role, reason="Automatic community member role")
            except discord.Forbidden:
                self.log.warning("Could not assign Community Member role to %s", member.id)
        welcome = discord.utils.find(
            lambda channel: channel_key(channel.name) == "welcome",
            member.guild.text_channels,
        )
        if welcome is None:
            return
        general = discord.utils.find(lambda channel: channel_key(channel.name) == "general", member.guild.text_channels)
        hello_channel = general.mention if general is not None else "the community chat"
        embed = discord.Embed(
            title="A new angler just joined! 🎣",
            description=(
                f"Welcome {member.mention}! Say hello in {hello_channel}, then use **/roles** "
                "to choose the community spaces you want to see."
            ),
            color=discord.Color.teal(),
        )
        try:
            await welcome.send(embed=embed)
        except discord.Forbidden:
            self.log.warning("Could not send join message in #%s", welcome.name)

    async def on_member_remove(self, member: discord.Member) -> None:
        if member.guild.id not in self.settings.allowed_guild_ids:
            return
        departures = discord.utils.find(
            lambda channel: isinstance(channel, discord.TextChannel)
            and channel_key(channel.name) == "member-departures",
            member.guild.text_channels,
        )
        if departures is None:
            return
        name = discord.utils.escape_markdown(member.name)
        try:
            await departures.send(f"`{name}` (`{member.id}`) left the server.")
        except discord.Forbidden:
            self.log.warning("Could not log a member departure in #%s", departures.name)

