"""Sensors for stats.fm."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, PERIODS
from .coordinator import StatsFmConfigEntry, StatsFmCoordinator, StatsFmData
from .now_playing import StatsFmNowPlayingCoordinator

PARALLEL_UPDATES = 0


# --- helpers ---------------------------------------------------------------


def _minutes(ms: Any) -> float | None:
    return None if ms is None else round(ms / 60000, 1)


def _artists(track: dict[str, Any]) -> str:
    return ", ".join(a.get("name", "") for a in track.get("artists") or [])


def _album_image(track: dict[str, Any]) -> str | None:
    albums = track.get("albums") or []
    return albums[0].get("image") if albums else None


def _track_label(track: dict[str, Any]) -> str:
    label = f"{_artists(track)} – {track.get('name', '?')}".strip(" –")
    return label[:255]


def _first(items: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    return items[0] if items else None


def _top_artist(data: StatsFmData) -> dict[str, Any]:
    return (_first(data.top_artists) or {}).get("artist") or {}


def _top_track_attrs(data: StatsFmData) -> dict[str, Any]:
    return {
        "range": "4 weeks",
        "tracks": [
            {
                "position": i.get("position"),
                "title": (t := i.get("track") or {}).get("name"),
                "artists": _artists(t),
                "streams": i.get("streams"),
                "minutes": _minutes(i.get("playedMs")),
                "image": _album_image(t),
            }
            for i in data.top_tracks or []
        ],
    }


def _top_artist_attrs(data: StatsFmData) -> dict[str, Any]:
    return {
        "range": "4 weeks",
        "artists": [
            {
                "position": i.get("position"),
                "name": (a := i.get("artist") or {}).get("name"),
                "streams": i.get("streams"),
                "minutes": _minutes(i.get("playedMs")),
                "genres": (a.get("genres") or [])[:3],
                "image": a.get("image"),
            }
            for i in data.top_artists or []
        ],
    }


def _recent_attrs(data: StatsFmData) -> dict[str, Any]:
    stream = _first(data.recent) or {}
    track = stream.get("track") or {}
    albums = track.get("albums") or []
    return {
        "title": track.get("name"),
        "artist": _artists(track),
        "album": albums[0].get("name") if albums else None,
        "ended_at": stream.get("endTime"),
        "duration_s": round((track.get("durationMs") or 0) / 1000) or None,
    }


# --- descriptions ----------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class StatsFmSensorDescription(SensorEntityDescription):
    """Describes a stats.fm sensor."""

    value_fn: Callable[[StatsFmData], StateType]
    available_fn: Callable[[StatsFmData], bool]
    attr_fn: Callable[[StatsFmData], dict[str, Any]] | None = None
    picture_fn: Callable[[StatsFmData], str | None] | None = None


def _period_sensors(period: str) -> tuple[StatsFmSensorDescription, ...]:
    def stats(data: StatsFmData) -> dict[str, Any]:
        return data.stats.get(period) or {}

    def available(data: StatsFmData) -> bool:
        return data.stats.get(period) is not None

    def cardinality(data: StatsFmData) -> dict[str, Any]:
        card = stats(data).get("cardinality") or {}
        attrs = {
            "streams": stats(data).get("count"),
            "unique_tracks": card.get("tracks"),
            "unique_artists": card.get("artists"),
            "unique_albums": card.get("albums"),
        }
        if period == "today":
            attrs["days"] = data.days
        return attrs

    return (
        StatsFmSensorDescription(
            key=f"listening_time_{period}",
            translation_key=f"listening_time_{period}",
            device_class=SensorDeviceClass.DURATION,
            native_unit_of_measurement=UnitOfTime.MINUTES,
            suggested_display_precision=0,
            # Resets at period start -> TOTAL_INCREASING handles that as meter reset.
            state_class=SensorStateClass.TOTAL_INCREASING,
            value_fn=lambda d: _minutes(stats(d).get("durationMs")),
            available_fn=available,
            attr_fn=cardinality,
        ),
        StatsFmSensorDescription(
            key=f"streams_{period}",
            translation_key=f"streams_{period}",
            native_unit_of_measurement="streams",
            state_class=SensorStateClass.TOTAL_INCREASING,
            entity_registry_enabled_default=period in ("today", "total"),
            value_fn=lambda d: stats(d).get("count"),
            available_fn=available,
        ),
    )


SENSORS: tuple[StatsFmSensorDescription, ...] = (
    *(s for p in PERIODS for s in _period_sensors(p)),
    StatsFmSensorDescription(
        key="top_track",
        translation_key="top_track",
        value_fn=lambda d: (
            _track_label(t["track"]) if (t := _first(d.top_tracks)) and t.get("track") else None
        ),
        available_fn=lambda d: d.top_tracks is not None,
        attr_fn=_top_track_attrs,
        picture_fn=lambda d: (
            _album_image(t.get("track") or {}) if (t := _first(d.top_tracks)) else None
        ),
    ),
    StatsFmSensorDescription(
        key="top_artist",
        translation_key="top_artist",
        value_fn=lambda d: _top_artist(d).get("name"),
        available_fn=lambda d: d.top_artists is not None,
        attr_fn=_top_artist_attrs,
        picture_fn=lambda d: _top_artist(d).get("image"),
    ),
    StatsFmSensorDescription(
        key="last_track",
        translation_key="last_track",
        value_fn=lambda d: (
            _track_label(s["track"]) if (s := _first(d.recent)) and s.get("track") else None
        ),
        available_fn=lambda d: d.recent is not None,
        attr_fn=_recent_attrs,
        picture_fn=lambda d: (
            _album_image(s.get("track") or {}) if (s := _first(d.recent)) else None
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StatsFmConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up stats.fm sensors."""
    stats = entry.runtime_data.stats
    async_add_entities(
        [
            *(StatsFmSensor(stats, desc) for desc in SENSORS),
            StatsFmNowPlayingSensor(entry.runtime_data.now_playing, stats),
        ]
    )


def _device_info(entry: StatsFmConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, str(entry.unique_id))},
        name=entry.title,
        manufacturer="stats.fm",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=f"https://stats.fm/user/{entry.unique_id}",
    )


class StatsFmSensor(CoordinatorEntity[StatsFmCoordinator], SensorEntity):
    """A stats.fm sensor."""

    _attr_has_entity_name = True
    # Lists stay in the state machine but don't bloat the recorder DB.
    _unrecorded_attributes = frozenset({"tracks", "artists", "days"})
    entity_description: StatsFmSensorDescription

    def __init__(
        self, coordinator: StatsFmCoordinator, description: StatsFmSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id}_{description.key}"
        self._attr_device_info = _device_info(entry)

    @property
    def available(self) -> bool:
        """Entity is available if its endpoint delivered data."""
        return super().available and self.entity_description.available_fn(self.coordinator.data)

    @property
    def native_value(self) -> StateType:
        """Return the sensor value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if fn := self.entity_description.attr_fn:
            return fn(self.coordinator.data)
        return None

    @property
    def entity_picture(self) -> str | None:
        """Return album/artist artwork."""
        if fn := self.entity_description.picture_fn:
            return fn(self.coordinator.data)
        return None


def _position(items: list[dict[str, Any]] | None, key: str, item_id: int | None) -> int | None:
    if item_id is None:
        return None
    for item in items or []:
        if (item.get(key) or {}).get("id") == item_id:
            return item.get("position")
    return None


class StatsFmNowPlayingSensor(CoordinatorEntity[StatsFmNowPlayingCoordinator], SensorEntity):
    """How often the currently playing song has been streamed before."""

    _attr_has_entity_name = True
    _attr_translation_key = "now_playing"
    _attr_native_unit_of_measurement = "streams"

    def __init__(
        self, coordinator: StatsFmNowPlayingCoordinator, stats: StatsFmCoordinator
    ) -> None:
        super().__init__(coordinator)
        self._stats = stats
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id}_now_playing"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> StateType:
        """Streams of the current song before this play."""
        return d.track_streams if (d := self.coordinator.data) else None

    @property
    def entity_picture(self) -> str | None:
        """Album artwork."""
        return d.image if (d := self.coordinator.data) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Track and artist details."""
        if (d := self.coordinator.data) is None:
            return None
        top = self._stats.data
        return {
            "spotify_id": d.spotify_id,
            "title": d.title,
            "artist": d.artist,
            "album": d.album,
            "track_minutes": _minutes(d.track_ms),
            "first_heard": d.first_heard.isoformat() if d.first_heard else None,
            "last_heard": d.last_heard.isoformat() if d.last_heard else None,
            "artist_name": d.artist_name,
            "artist_streams": d.artist_streams,
            "artist_minutes": _minutes(d.artist_ms),
            "artist_tracks": d.artist_tracks,
            "top_track_position": _position(top.top_tracks if top else None, "track", d.track_id),
            "top_artist_position": _position(
                top.top_artists if top else None, "artist", d.artist_id
            ),
        }
