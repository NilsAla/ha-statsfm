"""Minimal async client for the public stats.fm API."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import API_BASE, REQUEST_TIMEOUT

HEADERS = {"Accept": "application/json", "User-Agent": "HomeAssistant-statsfm/0.1"}


class StatsFmError(Exception):
    """Generic stats.fm API error."""


class StatsFmNotFoundError(StatsFmError):
    """User does not exist."""


class StatsFmPrivateError(StatsFmError):
    """Requested data is hidden by the user's privacy settings."""


class StatsFmClient:
    """Read-only client for one stats.fm user."""

    def __init__(self, session: aiohttp.ClientSession, user_id: str) -> None:
        self._session = session
        self._user_id = user_id

    async def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        url = f"{API_BASE}/users/{quote(self._user_id, safe='')}{path}"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.get(url, params=params, headers=HEADERS) as resp:
                    if resp.status == 404:
                        raise StatsFmNotFoundError(f"{path or 'user'} not found")
                    if resp.status in (401, 403):
                        raise StatsFmPrivateError(f"{path or 'user'} is private")
                    resp.raise_for_status()
                    return await resp.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise StatsFmError(f"Request to {path or '/'} failed: {err}") from err

    async def user(self) -> dict[str, Any]:
        """Return the user profile."""
        return (await self._get(""))["item"]

    async def stream_stats(
        self,
        *,
        after_ms: int | None = None,
        before_ms: int | None = None,
        range_: str | None = None,
    ) -> dict[str, Any]:
        """Return aggregated stream stats (durationMs, count, cardinality)."""
        params: dict[str, str] = {}
        if range_:
            params["range"] = range_
        if after_ms is not None:
            params["after"] = str(after_ms)
        if before_ms is not None:
            params["before"] = str(before_ms)
        return (await self._get("/streams/stats", params))["items"]

    async def top_tracks(self, range_: str, limit: int) -> list[dict[str, Any]]:
        """Return top tracks for a stats.fm range."""
        data = await self._get("/top/tracks", {"range": range_, "limit": str(limit)})
        return data.get("items") or []

    async def top_artists(self, range_: str, limit: int) -> list[dict[str, Any]]:
        """Return top artists for a stats.fm range."""
        data = await self._get("/top/artists", {"range": range_, "limit": str(limit)})
        return data.get("items") or []

    async def recent_streams(self, limit: int = 1) -> list[dict[str, Any]]:
        """Return the most recent streams."""
        data = await self._get("/streams/recent", {"limit": str(limit)})
        return data.get("items") or []

    async def current_stream(self) -> dict[str, Any] | None:
        """Return what the user is playing right now (stats.fm asks Spotify live)."""
        return (await self._get("/streams/current")).get("item")

    async def track_stats(self, track_id: int) -> dict[str, Any]:
        """Return lifetime stream stats for one stats.fm track id."""
        return (await self._get(f"/streams/tracks/{track_id}/stats"))["items"]

    async def artist_stats(self, artist_id: int) -> dict[str, Any]:
        """Return lifetime stream stats for one stats.fm artist id."""
        return (await self._get(f"/streams/artists/{artist_id}/stats"))["items"]

    async def track_streams(self, track_id: int, limit: int) -> list[dict[str, Any]]:
        """Return single streams of a track, newest first."""
        data = await self._get(f"/streams/tracks/{track_id}", {"limit": str(limit)})
        return data.get("items") or []
