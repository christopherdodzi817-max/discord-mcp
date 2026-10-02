from __future__ import annotations

import contextlib
import logging
import asyncio

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from .config import Settings
from .discord_service import DiscordService
from .server import create_mcp


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str) -> None:
        super().__init__(app)
        self.token = token

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path == "/health":
            return await call_next(request)
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def create_app(settings: Settings) -> Starlette:
    logging.basicConfig(level=settings.log_level)
    discord_service = DiscordService(settings)
    mcp = create_mcp(settings, discord_service)

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette):
        async with mcp.session_manager.run():
            discord_task = asyncio.create_task(discord_service.start(), name="discord-gateway")
            try:
                # Both the bot and MCP session manager run for the app lifetime.
                yield
            finally:
                await discord_service.close()
                discord_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await discord_task

    async def health(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "discord_ready": discord_service.bot.is_ready(),
                "guild_count": len(discord_service.bot.guilds),
                "mention_protection": {
                    "enabled": True,
                    "limit": settings.repeated_mention_limit,
                    "window_seconds": settings.repeated_mention_window_seconds,
                    "ready": discord_service.bot._mention_history_ready.is_set(),
                    "history_complete": discord_service.bot._mention_history_complete,
                },
            }
        )

    app = Starlette(
        routes=[Route("/health", health), Mount("/mcp", app=mcp.streamable_http_app())],
        lifespan=lifespan,
    )
    return BearerAuthMiddleware(app, settings.mcp_auth_token)
