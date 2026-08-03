# Cloud Discord MCP

A small, cloud-ready Discord bot and Model Context Protocol (MCP) server.

This project is intentionally locked down for a first deployment:

- Streamable HTTP MCP endpoint at `/mcp`
- Bearer authentication for MCP clients
- Guild and channel allowlists
- Read tools by default
- Message sending requires an explicit `confirm=true`
- No administrator permission, bans, deletes, role edits, or channel edits
- Health endpoint at `/health`

## 1. Create local secrets

Copy `.env.example` to `.env` and fill it in. Never commit `.env` or paste a bot token into chat.

```powershell
Copy-Item .env.example .env
```

Required values:

- `DISCORD_BOT_TOKEN`: the current token from Discord Developer Portal → Bot
- `MCP_AUTH_TOKEN`: a separate long random token used by MCP clients
- `ALLOWED_GUILD_IDS`: comma-separated server IDs

`ALLOWED_CHANNEL_IDS` is optional. If set, write tools can only use those channels.

## 2. Run locally

Python 3.11+ is recommended.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python -m discord_mcp
```

The MCP endpoint is `http://localhost:8000/mcp`.

## 3. Cloud deployment

Deploy the included `Dockerfile` to an always-on container service. Set the same environment variables in the provider's secret manager. The service must expose port `8000` and provide HTTPS in front of the container.

When deployed, configure the MCP client with the HTTPS URL ending in `/mcp` and send:

```http
Authorization: Bearer YOUR_MCP_AUTH_TOKEN
```

## Available tools

- `list_servers`
- `get_channels`
- `read_messages`
- `send_message` (requires `confirm=true`)

Add moderation and server-management tools only after this baseline is tested.

