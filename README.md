# stats.fm für Home Assistant

Custom Integration für [stats.fm](https://stats.fm) (Spotify-Hörstatistiken). Pollt die öffentliche stats.fm-API – kein Token, kein OAuth – und baut daraus Sensoren mit Long-Term-Statistics. Zusätzlich liefert sie zum gerade laufenden Spotify-Song, wie oft du ihn schon gehört hast.

## Voraussetzungen

- stats.fm-Profil mit öffentlichen **Stream-Stats**, **Top-Tracks/-Artists**, **Recently played** und **Currently playing** (stats.fm → Einstellungen → Privatsphäre).
- Für vollständige Historie: stats.fm Plus + importierte Spotify-Extended-History.
- Für „Aktueller Song“: die Spotify-Integration von Home Assistant (wird automatisch erkannt).
- Home Assistant ≥ 2025.2, Integrations-Icon ab 2026.3.

## Installation

**HACS:** HACS → ⋮ → Benutzerdefinierte Repositories → `https://github.com/NilsAla/ha-statsfm`, Typ *Integration* → stats.fm herunterladen → HA neu starten.

**Manuell:** `custom_components/statsfm/` nach `/config/custom_components/statsfm/` kopieren, HA neu starten.

Dann: Einstellungen → Geräte & Dienste → Integration hinzufügen → **stats.fm** → Username, Custom-ID oder Profil-URL. Gespeichert wird die unveränderliche stats.fm-User-ID.

## Entitäten

| Entität | Typ | Hinweis |
|---|---|---|
| Hörzeit heute / Woche / Monat / Jahr / gesamt | `duration`, min, `total_increasing` | Kalenderfenster in HA-Zeitzone, Woche ab Montag. Attribute: streams, unique tracks/artists/albums |
| Hörzeit heute → Attribut `days` | Liste | Letzte 30 Tage `{date, minutes, streams}`, ältester zuerst, heute zuletzt |
| Streams heute / gesamt | `total_increasing` | Woche/Monat/Jahr existieren, standardmäßig deaktiviert |
| Top-Track / Top-Künstler | Text | Letzte 4 Wochen, Attribut `tracks` / `artists` = Top 10, Bild als `entity_picture` |
| Zuletzt gehört | Text | artist, album, ended_at, duration_s |
| Aktueller Song | streams | Wie oft der laufende Song **vor** dieser Wiedergabe gestreamt wurde. Attribute siehe unten |

**Aktueller Song – Attribute:** `spotify_id`, `title`, `artist`, `album`, `track_minutes`, `first_heard`, `last_heard`, `artist_name`, `artist_streams`, `artist_minutes`, `artist_tracks`, `top_track_position`, `top_artist_position` (Platz in den Top 10 der letzten 4 Wochen oder `null`). `first_heard` ist `null`, wenn der Song öfter als 1000× gehört wurde (dann ist der älteste Stream nicht abrufbar).

`tracks`, `artists` und `days` sind vom Recorder ausgenommen.

## Verhalten

- **Statistik:** Polling alle 10 min, Requests parallel. Fällt ein Endpoint aus oder ist privat, wird nur die betroffene Entität `unavailable`.
- **Verlauf:** Abgeschlossene Tage werden im Speicher gecacht (nach einem Neustart einmalig 29 Requests), gestern wird wegen Sync-Verzögerung bei jedem Zyklus neu geholt.
- **Aktueller Song:** Kein Polling. Die Integration lauscht auf Trackwechsel der Spotify-`media_player` und fragt stats.fm nur dann ab (bis zu 4 Versuche à 4 s, bis stats.fm denselben Track meldet). Sonos-/Music-Assistant-Wiedergabe via Spotify Connect funktioniert, weil der Spotify-Player den Track mitführt.
- stats.fm synchronisiert Spotify mit Verzögerung (Minuten bis ~1 h); der gerade laufende Stream zählt erst danach mit.

## Debugging

```yaml
logger:
  logs:
    custom_components.statsfm: debug
```

Endpoints (unter `https://api.stats.fm/api/v1/users/<id>`): `/streams/stats?after=&before=`, `/streams/stats?range=lifetime`, `/top/tracks|artists?range=weeks`, `/streams/recent`, `/streams/current`, `/streams/tracks/<id>/stats`, `/streams/tracks/<id>`, `/streams/artists/<id>/stats`.

## Entwicklung

```bash
pip install -r requirements_test.txt
pytest
```

CI prüft hassfest, HACS-Validierung, ruff und die Tests.
