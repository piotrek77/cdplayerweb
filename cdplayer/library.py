"""Storage of disc metadata, covers and ripped MP3 files.

Layout:
  <data_dir>/discs/<disc_id>.json   cached/edited metadata of every disc seen
  <data_dir>/discs/<disc_id>.jpg    cached cover
  <music_dir>/<Artist>/<Album (Year)>/NN - Title.mp3
  <music_dir>/<Artist>/<Album (Year)>/cover.jpg
  <music_dir>/<Artist>/<Album (Year)>/.cdplayer.json   disc ID, metadata and file list

The music directory is scanned for .cdplayer.json files on start, so ripped
albums are found even after they were moved around inside it.
"""

import json
import logging
import os
import re
import shutil
import threading

log = logging.getLogger(__name__)

ALBUM_FILE = ".cdplayer.json"


def safe_name(text, limit=120):
    text = re.sub(r'[\x00-\x1f/\\:*?"<>|]', "_", text).strip().strip(".")
    return text[:limit].rstrip() or "_"


def _write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


class Library:
    def __init__(self, music_dir, data_dir):
        self.music_dir = os.path.abspath(music_dir)
        self.discs_dir = os.path.join(os.path.abspath(data_dir), "discs")
        os.makedirs(self.music_dir, exist_ok=True)
        os.makedirs(self.discs_dir, exist_ok=True)
        self._lock = threading.RLock()
        self._albums = {}  # disc_id -> album directory
        self.scan()

    def scan(self):
        albums = {}
        for root, _dirs, files in os.walk(self.music_dir):
            if ALBUM_FILE in files:
                data = _read_json(os.path.join(root, ALBUM_FILE))
                if data and data.get("disc_id"):
                    albums[data["disc_id"]] = root
        with self._lock:
            self._albums = albums
        log.info("library: %d ripped album(s) in %s", len(albums), self.music_dir)

    # -- metadata -----------------------------------------------------------

    def _album(self, disc_id):
        with self._lock:
            album_dir = self._albums.get(disc_id)
        if not album_dir:
            return None, None
        data = _read_json(os.path.join(album_dir, ALBUM_FILE))
        if not data:
            return None, None
        return album_dir, data

    def meta(self, disc_id):
        _dir, album = self._album(disc_id)
        if album:
            return album["meta"]
        return _read_json(os.path.join(self.discs_dir, disc_id + ".json"))

    def save_meta(self, disc_id, meta):
        with self._lock:
            _write_json(os.path.join(self.discs_dir, disc_id + ".json"), meta)

    def cover_path(self, disc_id):
        album_dir, _ = self._album(disc_id)
        candidates = [os.path.join(self.discs_dir, disc_id + ".jpg")]
        if album_dir:
            candidates.insert(0, os.path.join(album_dir, "cover.jpg"))
        return next((p for p in candidates if os.path.isfile(p)), None)

    def save_cover(self, disc_id, data):
        path = os.path.join(self.discs_dir, disc_id + ".jpg")
        with open(path + ".tmp", "wb") as f:
            f.write(data)
        os.replace(path + ".tmp", path)

    # -- ripped files -------------------------------------------------------

    def track_file(self, disc_id, number):
        album_dir, album = self._album(disc_id)
        if not album:
            return None
        name = album["files"].get(str(number))
        path = os.path.join(album_dir, name) if name else None
        return path if path and os.path.isfile(path) else None

    def ripped_tracks(self, disc_id):
        album_dir, album = self._album(disc_id)
        if not album:
            return set()
        return {
            int(n) for n, name in album["files"].items()
            if os.path.isfile(os.path.join(album_dir, name))
        }

    def album_dir(self, disc_id, meta):
        """Return (creating if needed) the directory a disc is ripped into."""
        with self._lock:
            existing = self._albums.get(disc_id)
            if existing and os.path.isdir(existing):
                return existing
            album = meta["album"]
            if meta.get("year"):
                album += f" ({meta['year']})"
            if meta.get("disc_total", 1) > 1:
                album += f" [CD {meta.get('disc_number', 1)}]"
            path = os.path.join(self.music_dir, safe_name(meta["artist"]), safe_name(album))
            info = os.path.join(path, ALBUM_FILE)
            if os.path.exists(info) and (_read_json(info) or {}).get("disc_id") != disc_id:
                path += f" [{disc_id[:8]}]"
            os.makedirs(path, exist_ok=True)
            _write_json(os.path.join(path, ALBUM_FILE), {"disc_id": disc_id, "meta": meta, "files": {}})
            cover = os.path.join(self.discs_dir, disc_id + ".jpg")
            if os.path.isfile(cover):
                shutil.copyfile(cover, os.path.join(path, "cover.jpg"))
            self._albums[disc_id] = path
            return path

    def track_filename(self, meta, number):
        track = next(t for t in meta["tracks"] if t["number"] == number)
        return safe_name(f"{number:02d} - {track['title']}") + ".mp3"

    def register_track(self, disc_id, number, filename):
        with self._lock:
            album_dir = self._albums[disc_id]
            info = os.path.join(album_dir, ALBUM_FILE)
            data = _read_json(info)
            data["files"][str(number)] = filename
            _write_json(info, data)
