"""Smoke tests for the stats.fm integration (API mocked)."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.statsfm.api import (
    StatsFmClient,
    StatsFmNotFoundError,
    StatsFmPrivateError,
)
from custom_components.statsfm.const import DOMAIN

USER = {
    "id": "nils123",
    "customId": "nils",
    "displayName": "Nils",
    "privacySettings": {"streamStats": True},
}
TRACK = {
    "name": "Song A",
    "durationMs": 200000,
    "artists": [{"name": "Artist X"}, {"name": "Artist Y"}],
    "albums": [{"name": "Album", "image": "https://img/a.jpg"}],
}


NP_TRACK = {
    "id": 699079,
    "name": "Just Lose It",
    "externalIds": {"spotify": ["6v6WjsyLdY189IewecL2x7"]},
    "artists": [{"id": 12707, "name": "Eminem"}],
    "albums": [{"name": "Curtain Call", "image": "https://img/e.jpg"}],
}


def stats(minutes: float, count: int) -> dict:
    return {
        "durationMs": int(minutes * 60000),
        "count": count,
        "cardinality": {"tracks": 3, "artists": 2, "albums": 1},
    }


@pytest.fixture(autouse=True)
def auto_enable(enable_custom_integrations):
    yield


async def fake_stream_stats(self, *, after_ms=None, before_ms=None, range_=None):
    if range_ == "lifetime":
        return stats(100000, 30000)
    # Smaller after_ms = longer window -> more minutes
    return stats(round((before_ms - after_ms) / 3_600_000, 1), 1)


@pytest.fixture
def mock_client():
    with (
        patch.object(StatsFmClient, "user", AsyncMock(return_value=USER)),
        patch.object(StatsFmClient, "stream_stats", fake_stream_stats),
        patch.object(
            StatsFmClient,
            "top_tracks",
            AsyncMock(return_value=[{"position": 1, "streams": 42, "playedMs": 600000, "track": TRACK}]),
        ),
        patch.object(
            StatsFmClient,
            "top_artists",
            AsyncMock(side_effect=StatsFmPrivateError("private")),
        ),
        patch.object(
            StatsFmClient,
            "recent_streams",
            AsyncMock(return_value=[{"endTime": "2026-10-13T12:00:00.000Z", "track": TRACK}]),
        ),
        patch.object(StatsFmClient, "current_stream", AsyncMock(return_value={"isPlaying": True, "track": NP_TRACK})),
        patch.object(StatsFmClient, "track_stats", AsyncMock(return_value={"count": 9, "durationMs": 1793399})),
        patch.object(
            StatsFmClient,
            "artist_stats",
            AsyncMock(return_value={"count": 874, "durationMs": 201636149, "cardinality": {"tracks": 78}}),
        ),
        patch.object(
            StatsFmClient,
            "track_streams",
            AsyncMock(return_value=[{"endTime": "2026-07-18T03:40:00.000Z"}] * 8 + [{"endTime": "2016-10-20T08:48:19.000Z"}]),
        ),
    ):
        yield


async def test_config_flow(hass: HomeAssistant, mock_client) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"user_id": "https://stats.fm/user/nils/"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "stats.fm (Nils)"
    assert result["data"] == {"user_id": "nils123"}
    assert result["result"].unique_id == "nils123"
    await hass.async_block_till_done()

    # duplicate
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"user_id": "@nils"})
    assert result["type"] is FlowResultType.ABORT


async def test_config_flow_errors(hass: HomeAssistant) -> None:
    for side_effect, ret, err in (
        (StatsFmNotFoundError("x"), None, "user_not_found"),
        (None, {**USER, "privacySettings": {"streamStats": False}}, "stats_private"),
    ):
        with patch.object(StatsFmClient, "user", AsyncMock(side_effect=side_effect, return_value=ret)):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"user_id": "nils"}
            )
            assert result["errors"] == {"base": err}


async def test_sensors(hass: HomeAssistant, mock_client) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    entry = MockConfigEntry(domain=DOMAIN, unique_id="nils123", title="stats.fm (Nils)", data={"user_id": "nils123"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    reg = er.async_get(hass)
    ids = {e.translation_key: e.entity_id for e in er.async_entries_for_config_entry(reg, entry.entry_id)}
    print(sorted(ids.values()))
    assert len(ids) == 14
    days = hass.states.get(ids["listening_time_today"]).attributes["days"]
    assert len(days) == 30 and days[-1]["streams"] == 1, days[-3:]

    total = hass.states.get(ids["listening_time_total"])
    assert float(total.state) == 100000
    assert total.attributes["unit_of_measurement"] == "min"
    assert total.attributes["state_class"] == "total_increasing"
    assert total.attributes["unique_artists"] == 2

    vals = [float(hass.states.get(ids[f"listening_time_{p}"]).state) for p in ("today", "week", "month", "year")]
    assert vals == sorted(vals), vals

    top = hass.states.get(ids["top_track"])
    assert top.state == "Artist X, Artist Y – Song A"
    assert top.attributes["entity_picture"] == "https://img/a.jpg"
    assert top.attributes["tracks"][0]["minutes"] == 10.0

    # private endpoint -> only that entity unavailable
    assert hass.states.get(ids["top_artist"]).state == STATE_UNAVAILABLE

    last = hass.states.get(ids["last_track"])
    assert last.attributes["album"] == "Album"
    assert last.attributes["duration_s"] == 200

    # disabled-by-default stream counters
    assert reg.async_get(ids["streams_week"]).disabled_by is not None
    assert hass.states.get(ids["streams_today"]).state == "1"

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_api_params(hass: HomeAssistant, aioclient_mock) -> None:
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    aioclient_mock.get(
        "https://api.stats.fm/api/v1/users/nils123/streams/stats",
        json={"items": stats(1, 1)},
    )
    aioclient_mock.get("https://api.stats.fm/api/v1/users/ghost", status=404)
    client = StatsFmClient(async_get_clientsession(hass), "nils123")
    await client.stream_stats(after_ms=1, before_ms=2)
    _, url, _, _ = aioclient_mock.mock_calls[-1]
    assert url.query == {"after": "1", "before": "2"}
    with pytest.raises(StatsFmNotFoundError):
        await StatsFmClient(async_get_clientsession(hass), "ghost").user()


async def test_now_playing(hass: HomeAssistant, mock_client) -> None:
    reg = er.async_get(hass)
    sp = reg.async_get_or_create("media_player", "spotify", "acc1", suggested_object_id="spotify_nils")
    hass.states.async_set(sp.entity_id, "idle", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id="nils123", title="stats.fm (Nils)", data={"user_id": "nils123"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    np_id = reg.async_get_entity_id("sensor", DOMAIN, "nils123_now_playing")
    assert hass.states.get(np_id).state == "unknown"

    hass.states.async_set(sp.entity_id, "playing", {"media_content_id": "spotify:track:6v6WjsyLdY189IewecL2x7"})
    await hass.async_block_till_done()
    st = hass.states.get(np_id)
    assert st.state == "9", st
    a = st.attributes
    assert a["artist_streams"] == 874 and a["artist_tracks"] == 78
    assert a["first_heard"].startswith("2016-10-20") and a["last_heard"].startswith("2026-07-18")
    assert a["track_minutes"] == 29.9
    assert a["entity_picture"] == "https://img/e.jpg"

    # position update of the same track must not trigger another lookup
    calls = StatsFmClient.current_stream.call_count
    hass.states.async_set(sp.entity_id, "playing", {"media_content_id": "spotify:track:6v6WjsyLdY189IewecL2x7", "media_position": 30})
    await hass.async_block_till_done()
    assert StatsFmClient.current_stream.call_count == calls

    # truncated stream list -> no first_heard
    StatsFmClient.track_stats.return_value = {"count": 5000, "durationMs": 1}
    hass.states.async_set(sp.entity_id, "playing", {"media_content_id": "spotify:track:6v6WjsyLdY189IewecL2x7".replace("6v6", "7v6")})
    NP_TRACK["externalIds"]["spotify"] = ["7v6WjsyLdY189IewecL2x7"]
    await hass.async_block_till_done()
    # debouncer cooldown may delay; force refresh
    await entry.runtime_data.now_playing.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(np_id).attributes["first_heard"] is None
    assert await hass.config_entries.async_unload(entry.entry_id)
