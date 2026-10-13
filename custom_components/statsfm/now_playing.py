"""Listening stats for the song that is currently playing on Spotify.

Event-driven: listens to the Spotify media_player entities of the HA Spotify integration
and only queries stats.fm when the track changes. No polling.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import logging
import re
from typing import TYPE_CHECKING, Any

from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import StatsFmClient, StatsFmError
from .const import NOW_PLAYING_RETRIES, NOW_PLAYING_RETRY_DELAY, TRACK_STREAMS_LIMIT

if TYPE_CHECKING:
    from .coordinator import StatsFmConfigEntry

_LOGGER = logging.getLogger(__name__)

# spotify:track:<id> or the URL-encoded Sonos variant (spotify%3atrack%3a<id>)
SPOTIFY_TRACK_RE = re.compile(r"spotify(?::|%3a)track(?::|%3a)([A-Za-z0-9]{22})", re.IGNORECASE)


def spotify_track_id(content_id: Any) -> str | None:
    """Extract the Spotify track id from a media_content_id."""
    m = SPOTIFY_TRACK_RE.search(str(content_id or ""))
    return m.group(1) if m else None


@dataclass(slots=True, frozen=True)
class NowPlayingStats:
    """Stats for one track and its first artist. Counts exclude the current play."""

    spotify_id: str
    track_id: int
    title: str | None
    artist: str | None
    album: str | None
    image: str | None
    track_streams: int
    track_ms: int
    first_heard: datetime | None
    last_heard: datetime | None
    artist_id: int | None
    artist_name: str | None
    artist_streams: int | None
    artist_ms: int | None
    artist_tracks: int | None


def _parse_ts(value: Any) -> datetime | None:
    return dt_util.parse_datetime(value) if isinstance(value, str) else None


class StatsFmNowPlayingCoordinator(DataUpdateCoordinator[NowPlayingStats | None]):
    """Refreshes on track change of a Spotify media_player."""

    config_entry: StatsFmConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: StatsFmConfigEntry, client: StatsFmClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{entry.domain}_now_playing",
            update_interval=None,
        )
        self.client = client
        self.players: list[str] = []

    @callback
    def async_start(self) -> None:
        """Find Spotify players and subscribe to their state changes."""
        reg = er.async_get(self.hass)
        self.players = sorted(
            e.entity_id
            for e in reg.entities.values()
            if e.platform == "spotify" and e.domain == "media_player" and not e.disabled
        )
        if not self.players:
            _LOGGER.info("No Spotify media_player found, now-playing stats disabled")
            return
        _LOGGER.debug("Watching %s for track changes", self.players)
        self.config_entry.async_on_unload(
            async_track_state_change_event(self.hass, self.players, self._on_player_change)
        )
        self.config_entry.async_on_unload(async_at_started(self.hass, self._on_started))

    @callback
    def _on_started(self, _hass: HomeAssistant) -> None:
        self._schedule()

    @callback
    def _on_player_change(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        old = event.data["old_state"]
        new_id = spotify_track_id(new.attributes.get("media_content_id")) if new else None
        old_id = spotify_track_id(old.attributes.get("media_content_id")) if old else None
        # Position/volume updates arrive every few seconds; only act on a real track change.
        if new_id and new_id != old_id:
            self._schedule()

    @callback
    def _schedule(self) -> None:
        wanted = self._wanted()
        if wanted and (self.data is None or self.data.spotify_id != wanted):
            self.config_entry.async_create_background_task(
                self.hass, self.async_request_refresh(), f"{self.name} refresh"
            )

    def _wanted(self) -> str | None:
        """Track id of the first Spotify player that is playing (or paused)."""
        for state in sorted(
            (s for p in self.players if (s := self.hass.states.get(p))),
            key=lambda s: s.state != "playing",
        ):
            if state.state in ("playing", "paused") and (
                tid := spotify_track_id(state.attributes.get("media_content_id"))
            ):
                return tid
        return None

    async def _async_update_data(self) -> NowPlayingStats | None:
        wanted = self._wanted()
        if not wanted or (self.data is not None and self.data.spotify_id == wanted):
            return self.data

        try:
            current = await self._current_matching(wanted)
            if current is None:
                _LOGGER.debug("stats.fm never reported %s as current track", wanted)
                return self.data
            return await self._build(wanted, current)
        except StatsFmError as err:
            raise UpdateFailed(f"stats.fm now-playing lookup failed: {err}") from err

    async def _current_matching(self, wanted: str) -> dict[str, Any] | None:
        """stats.fm resolves Spotify ids to its own ids; wait until it sees the same track."""
        for attempt in range(NOW_PLAYING_RETRIES):
            if attempt:
                await asyncio.sleep(NOW_PLAYING_RETRY_DELAY)
                if self._wanted() != wanted:  # skipped again meanwhile
                    return None
            item = await self.client.current_stream()
            track = (item or {}).get("track") or {}
            if wanted in ((track.get("externalIds") or {}).get("spotify") or []):
                return track
        return None

    async def _build(self, spotify_id: str, track: dict[str, Any]) -> NowPlayingStats:
        track_id = track["id"]
        artists = track.get("artists") or []
        albums = track.get("albums") or []
        artist = artists[0] if artists else {}

        jobs = [
            self.client.track_stats(track_id),
            self.client.track_streams(track_id, TRACK_STREAMS_LIMIT),
        ]
        if artist.get("id") is not None:
            jobs.append(self.client.artist_stats(artist["id"]))
        t_stats, streams, *a = await asyncio.gather(*jobs)
        a_stats = a[0] if a else {}

        count = t_stats.get("count") or 0
        # List is newest first. Only trust the oldest entry if we got every stream.
        first = _parse_ts(streams[-1].get("endTime")) if streams and len(streams) >= count else None
        last = _parse_ts(streams[0].get("endTime")) if streams else None

        return NowPlayingStats(
            spotify_id=spotify_id,
            track_id=track_id,
            title=track.get("name"),
            artist=", ".join(x.get("name", "") for x in artists) or None,
            album=albums[0].get("name") if albums else None,
            image=albums[0].get("image") if albums else None,
            track_streams=count,
            track_ms=t_stats.get("durationMs") or 0,
            first_heard=first,
            last_heard=last,
            artist_id=artist.get("id"),
            artist_name=artist.get("name"),
            artist_streams=a_stats.get("count"),
            artist_ms=a_stats.get("durationMs"),
            artist_tracks=(a_stats.get("cardinality") or {}).get("tracks"),
        )
