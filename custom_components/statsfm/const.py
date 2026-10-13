"""Constants for the stats.fm integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "statsfm"

API_BASE: Final = "https://api.stats.fm/api/v1"
REQUEST_TIMEOUT: Final = 15

CONF_USER_ID: Final = "user_id"

SCAN_INTERVAL: Final = timedelta(minutes=10)

# stats.fm ranges: "weeks" = last 4 weeks, "months" = last 6 months, "lifetime"
TOP_RANGE: Final = "weeks"
TOP_LIMIT: Final = 10

PERIODS: Final = ("today", "week", "month", "year", "total")

# Daily listening history exposed on the "today" sensor (past days are cached).
HISTORY_DAYS: Final = 30
HISTORY_CONCURRENCY: Final = 5

# Now playing: max single streams fetched to find the first play of a track.
TRACK_STREAMS_LIMIT: Final = 1000
# stats.fm may lag a few seconds behind Spotify after a track change.
NOW_PLAYING_RETRIES: Final = 4
NOW_PLAYING_RETRY_DELAY: Final = 4
