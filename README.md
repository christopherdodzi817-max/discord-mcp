# Cloud Discord MCP and HeadMod

A Discord bot with a bearer-authenticated Model Context Protocol (MCP) endpoint at `/mcp`. HeadMod reports possible rule concerns to a private staff channel for human review. It does not ban, timeout, kick, or delete messages as a moderation response.

## What HeadMod checks

HeadMod checks non-staff text messages in allowlisted servers. Live checks post an incident with a message link and short excerpt to the configured staff log; `audit_recent_messages` can scan a bounded recent history on request. A signal is a review cue, not a finding of guilt. Staff make the final decision using the published rules in `#rules`.

| Published rule | Automatic coverage | Limit |
| --- | --- | --- |
| 1 | Whole terms or phrases explicitly listed in `MODERATION_TERMS_RULE_1`. | No general understanding of harassment or intent. |
| 2 | A small built-in common profanity list, plus whole terms or phrases explicitly listed in `MODERATION_TERMS_RULE_2`. | No general hate-speech or context judgment. No bundled slur list. |
| 3 | Five messages in ten seconds by one member, three repeated identical messages in that window, an `@everyone`/`@here` mention, or eight user mentions in one message. The window, message count, and mention count are configurable. | These are per-member bursts, not a reliable coordinated-raid detector. |
| 4 | Whole terms or phrases explicitly listed in `MODERATION_TERMS_RULE_4`. | No general scam or fraud detection. |
| 5 | No automatic signal. | Requires staff review. |
| 6 | `discord.gg/...` invites outside a channel named `server-discovery`. | Does not recognize every invite format or decide whether other promotion is allowed. |
| 7 | No automatic signal. | Exploit use and punishment evasion need staff review and context. |
| 8 | No automatic signal. | HeadMod cannot adjudicate Discord or Roblox Terms of Service. |

Matching normalizes Unicode, case, and whitespace, then requires whole terms or phrases. `MODERATION_SAFE_PHRASES` can exempt an exact whole-message phrase from one configured term match; built-in profanity still applies. HeadMod cannot inspect unsolicited DMs, reliably judge harassment or image/video content, detect exploits outside the server, or adjudicate Discord or Roblox Terms of Service. Report those matters to staff with context.

## Configure Discord and secrets

Create a bot in the Discord Developer Portal. Enable the privileged **Message Content** intent for text inspection and the privileged **Server Members** intent for member join/leave features when `ENABLE_MEMBER_EVENTS=true` (the example default). Enable the same intents in the bot configuration. Give the bot **View Channels**, **Read Message History**, **Send Messages**, and **Embed Links** in the private `#staff-room` used for the moderation log. Keep `@everyone` and nonstaff roles and members unable to view that channel; HeadMod refuses to post incidents when they can access the log. The bot needs those read permissions in channels it monitors or audits, and Send Messages where it answers pings. **Manage Messages is not required**; grant it only if a later, separately approved delete feature is added. Existing community role and ticket features separately need **Manage Roles** and **Manage Channels** and a suitable role position.

Copy `.env.example` to `.env`, set both tokens, and keep `.env` out of version control:

```powershell
Copy-Item .env.example .env
```

Set `DISCORD_BOT_TOKEN` to the bot token and `MCP_AUTH_TOKEN` to a separate long random bearer token. Set `ALLOWED_GUILD_IDS` to the server ID. Set `RULES_CHANNEL_ID` to `#rules`, `MODERATION_LOG_CHANNEL_ID` to private `#staff-room`, and `ESCALATION_ADMIN_ID` to the intended administrator's user ID. The example IDs are for the current server; replace them for a different one. A HeadMod ping containing an action word such as ban, kick, timeout, mute, delete, or punish mentions that ID only when the member currently has Administrator permission; otherwise the bot asks for a server administrator without a mention. If `ALLOWED_CHANNEL_IDS` is set, include the staff log ID and every channel the MCP tools should read or write. If left empty, MCP channel access is limited to the allowlisted guilds but not to a channel subset.

The example currently contains `RULES_CHANNEL_ID=1529680640872677491`, `MODERATION_LOG_CHANNEL_ID=1533923840726663421`, and `ESCALATION_ADMIN_ID=1388189183633526946`. Confirm these against the target server before starting the bot.

Term lists are comma-separated literal terms or phrases. Add only terms staff have reviewed; leave a rule's list blank if it has no agreed terms. `SPAM_WINDOW_SECONDS`, `SPAM_MESSAGE_THRESHOLD`, and `MENTION_THRESHOLD` set the rule 3 thresholds; the identical-message threshold is currently fixed at three.

## Run and test locally

Use Python 3.11 or newer:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest -q
python -c "from discord_mcp.app import create_app; print('import ok')"
python -m discord_mcp
```

The local endpoint is `http://localhost:8000/mcp`; `/health` reports process health and Discord readiness. The test and import commands do not need real tokens.

## Deploy

Deploy `Dockerfile` as a web service with HTTPS and port `8000`. Configure the same environment values in the provider's secret manager. MCP clients connect to the HTTPS URL ending in `/mcp` with `Authorization: Bearer YOUR_MCP_AUTH_TOKEN`.

Render's Free web service can spin down after idle time and restart later, so it cannot promise 24/7 Discord monitoring. Use an always-on service when uninterrupted monitoring matters. See [Render's Free service limits](https://render.com/docs/free).

## MCP tools

- `list_servers`, `get_channels`, `read_messages`, and `get_server_stats` read allowlisted server data.
- `list_moderation_flags` reads HeadMod incident embeds from the configured private staff log.
- `audit_recent_messages` returns rule candidates from up to 50 recent messages in an allowlisted channel.
- `audit_server` reports permission concerns without changing them.
- `send_message` sends only to an allowlisted channel and requires `confirm=true`.

MCP channel reads stay within `ALLOWED_GUILD_IDS` and, when configured, `ALLOWED_CHANNEL_IDS`. The bot's community commands also provide optional roles and private tickets; closing a ticket deletes that ticket channel.
