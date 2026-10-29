"""Disc metadata from MusicBrainz and cover art from the Cover Art Archive."""

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from . import __version__

log = logging.getLogger(__name__)

USER_AGENT = f"cdplayerweb/{__version__} ( https://musicbrainz.org/doc/MusicBrainz_API )"
MB_URL = "https://musicbrainz.org/ws/2/discid/{}?toc={}&inc=recordings+artist-credits+release-groups&fmt=json"
COVER_URLS = (
    "https://coverartarchive.org/release/{}/front-500",
    "https://coverartarchive.org/release-group/{}/front-500",
)


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _credit(artist_credit):
    return "".join(c["name"] + c.get("joinphrase", "") for c in artist_credit or [])


def placeholder(toc):
    """Metadata for a disc we know nothing about."""
    return {
        "artist": "Nieznany wykonawca",
        "album": "Nieznana płyta",
        "year": "",
        "disc_number": 1,
        "disc_total": 1,
        "mbid": None,
        "release_group": None,
        "source": "none",
        "tracks": [
            {"number": n, "title": f"Utwór {n:02d}", "artist": ""}
            for n in range(toc.first, toc.last + 1)
        ],
    }


def _pick(releases, disc_id, track_count):
    """Choose the best (release, medium) pair; prefer exact disc ID and cover art."""
    candidates = []
    for release in releases:
        for medium in release.get("media", []):
            exact = any(d.get("id") == disc_id for d in medium.get("discs", []))
            if not exact and len(medium.get("tracks", [])) != track_count:
                continue
            has_cover = release.get("cover-art-archive", {}).get("front", False)
            candidates.append((exact, has_cover, release, medium))
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    return candidates[0][2], candidates[0][3]


def lookup(toc):
    """Query MusicBrainz. Returns a metadata dict or None if the disc is unknown.

    Raises OSError when MusicBrainz cannot be reached.
    """
    disc_id = toc.disc_id()
    try:
        data = json.loads(_get(MB_URL.format(urllib.parse.quote(disc_id), toc.mb_toc())))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    releases = data.get("releases", [])
    picked = _pick(releases, disc_id, toc.track_count)
    if not picked:
        return None
    release, medium = picked
    album_artist = _credit(release.get("artist-credit"))
    tracks = []
    for n, track in zip(range(toc.first, toc.last + 1), medium["tracks"]):
        artist = _credit(track.get("artist-credit"))
        tracks.append({
            "number": n,
            "title": track.get("title") or f"Utwór {n:02d}",
            "artist": artist if artist != album_artist else "",
        })
    return {
        "artist": album_artist or "Nieznany wykonawca",
        "album": release.get("title") or "Nieznana płyta",
        "year": (release.get("date") or "")[:4],
        "disc_number": medium.get("position", 1),
        "disc_total": len(release.get("media", [])) or 1,
        "mbid": release["id"],
        "release_group": (release.get("release-group") or {}).get("id"),
        "source": "musicbrainz",
        "tracks": tracks,
    }


def fetch_cover(meta):
    """Download front cover JPEG bytes, or None."""
    ids = (meta.get("mbid"), meta.get("release_group"))
    for template, mbid in zip(COVER_URLS, ids):
        if not mbid:
            continue
        try:
            return _get(template.format(mbid))
        except (OSError, urllib.error.HTTPError) as e:
            log.info("no cover at %s: %s", template.format(mbid), e)
    return None
