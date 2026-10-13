"""Data update coordinator for stats.fm."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import StatsFmClient, StatsFmError
from .const import (
    DOMAIN,
    HISTORY_CONCURRENCY,
    HISTORY_DAYS,
    SCAN_INTERVAL,
    TOP_LIMIT,
    TOP_RANGE,
)

if TYPE_CHECKING:
    from .now_playing import StatsFmNowPlayingCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class StatsFmRuntimeData:
    """Objects attached to the config entry."""

    stats: StatsFmCoordinator
    now_playing: StatsFmNowPlayingCoordinator


type StatsFmConfigEntry = ConfigEntry[StatsFmRuntimeData]


@dataclass(slots=True)
class StatsFmData:
    """Snapshot of one polling cycle. ``None`` = endpoint failed or private."""

    stats: dict[str, dict[str, Any] | None] = field(default_factory=dict)
    top_tracks: list[dict[str, Any]] | None = None
    top_artists: list[dict[str, Any]] | None = None
    recent: list[dict[str, Any]] | None = None
    # Oldest → newest, today last: {"date", "minutes", "streams"}
    days: list[dict[str, Any]] = field(default_factory=list)


def _ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


class StatsFmCoordinator(DataUpdateCoordinator[StatsFmData]):
    """Polls all stats.fm endpoints in parallel."""

    config_entry: StatsFmConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: StatsFmConfigEntry, client: StatsFmClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        # Finished days don't change any more (apart from sync lag) → fetch once.
        self._day_cache: dict[date, dict[str, Any]] = {}

    async def _async_update_data(self) -> StatsFmData:
        now = dt_util.now()
        today = dt_util.start_of_local_day(now)
        # Calendar windows in HA's local timezone (week starts Monday).
        windows = {
            "today": today,
            "week": today - timedelta(days=today.weekday()),
            "month": today.replace(day=1),
            "year": today.replace(month=1, day=1),
        }
        before = _ms(now)

        jobs: dict[str, Any] = {
            period: self.client.stream_stats(after_ms=_ms(start), before_ms=before)
            for period, start in windows.items()
        }
        jobs["total"] = self.client.stream_stats(range_="lifetime")
        jobs["top_tracks"] = self.client.top_tracks(TOP_RANGE, TOP_LIMIT)
        jobs["top_artists"] = self.client.top_artists(TOP_RANGE, TOP_LIMIT)
        jobs["recent"] = self.client.recent_streams(1)
        jobs["history"] = self._update_day_cache(today)

        results = dict(
            zip(jobs, await asyncio.gather(*jobs.values(), return_exceptions=True), strict=True)
        )

        failed = {k: v for k, v in results.items() if isinstance(v, BaseException)}
        # history swallows its own errors, so it doesn't count towards "everything failed"
        if len(failed.keys() - {"history"}) == len(results) - 1:
            raise UpdateFailed(f"All stats.fm requests failed: {next(iter(failed.values()))}")
        for key, err in failed.items():
            if not isinstance(err, StatsFmError):
                _LOGGER.exception("Unexpected error fetching %s", key, exc_info=err)
            else:
                _LOGGER.debug("stats.fm %s unavailable: %s", key, err)

        def ok(key: str) -> Any:
            return None if key in failed else results[key]

        stats = {p: ok(p) for p in (*windows, "total")}
        return StatsFmData(
            stats=stats,
            top_tracks=ok("top_tracks"),
            top_artists=ok("top_artists"),
            recent=ok("recent"),
            days=self._days(today, stats["today"]),
        )

    async def _update_day_cache(self, today: datetime) -> None:
        """Fetch finished days that are missing; always refresh yesterday (sync lag)."""
        sem = asyncio.Semaphore(HISTORY_CONCURRENCY)
        starts = [today - timedelta(days=i) for i in range(HISTORY_DAYS - 1, 0, -1)]
        wanted = {s.date() for s in starts}
        for old in set(self._day_cache) - wanted:
            del self._day_cache[old]

        async def fetch(start: datetime, end: datetime) -> None:
            async with sem:
                try:
                    item = await self.client.stream_stats(after_ms=_ms(start), before_ms=_ms(end))
                except StatsFmError as err:
                    _LOGGER.debug("stats.fm history %s failed: %s", start.date(), err)
                    return
            self._day_cache[start.date()] = {
                "minutes": round((item.get("durationMs") or 0) / 60000, 1),
                "streams": item.get("count") or 0,
            }

        yesterday = (today - timedelta(days=1)).date()
        await asyncio.gather(
            *(
                fetch(start, end)
                for start, end in zip(starts, [*starts[1:], today], strict=True)
                if start.date() not in self._day_cache or start.date() == yesterday
            )
        )

    def _days(self, today: datetime, today_stats: dict[str, Any] | None) -> list[dict[str, Any]]:
        out = [{"date": d.isoformat(), **self._day_cache[d]} for d in sorted(self._day_cache)]
        if today_stats is not None:
            out.append(
                {
                    "date": today.date().isoformat(),
                    "minutes": round((today_stats.get("durationMs") or 0) / 60000, 1),
                    "streams": today_stats.get("count") or 0,
                }
            )
        return out
