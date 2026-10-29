"""Background services: watching the drive and ripping discs to MP3."""

import logging
import os
import subprocess
import threading
import time

from . import metadata

log = logging.getLogger(__name__)

POLL_INTERVAL = 2.0
MP3_BITRATE = "320k"


class DiscMonitor:
    """Polls the drive and keeps track of the inserted disc and its metadata."""

    def __init__(self, drive, library):
        self.drive = drive
        self.library = library
        self._lock = threading.Lock()
        self.status = "not_ready"
        self.toc = None
        self.disc_id = None
        self.lookup = None  # searching | found | not_found | offline | cached | manual
        self._meta = None
        self.changed = threading.Event()

    def start(self):
        threading.Thread(target=self._run, name="disc-monitor", daemon=True).start()

    def _run(self):
        while True:
            try:
                self._poll()
            except Exception:
                log.exception("drive poll failed")
            time.sleep(POLL_INTERVAL)

    def _poll(self):
        status = self.drive.status()
        if status != "ok":
            with self._lock:
                if self.status != status or self.disc_id:
                    log.info("drive: %s", status)
                self.status, self.toc, self.disc_id, self._meta, self.lookup = status, None, None, None, None
            return
        if self.disc_id:
            return
        try:
            toc = self.drive.read_toc()
        except OSError as e:
            log.debug("TOC not readable yet: %s", e)
            return
        if toc is None:
            with self._lock:
                self.status = "no_audio"
            return
        disc_id = toc.disc_id()
        log.info("disc inserted: %s (%d tracks)", disc_id, toc.track_count)
        meta = self.library.meta(disc_id)
        with self._lock:
            self.status, self.toc, self.disc_id = "ok", toc, disc_id
            if meta:
                self._meta = meta
                self.lookup = "manual" if meta.get("source") == "manual" else "cached"
            else:
                self._meta = metadata.placeholder(toc)
                self.lookup = "searching"
        if not meta:
            self.refresh_metadata()

    def refresh_metadata(self):
        with self._lock:
            toc, disc_id = self.toc, self.disc_id
            if not toc:
                return False
            self.lookup = "searching"
        threading.Thread(target=self._lookup, args=(toc, disc_id), daemon=True).start()
        return True

    def _lookup(self, toc, disc_id):
        try:
            meta = metadata.lookup(toc)
            result = "found" if meta else "not_found"
        except OSError as e:
            log.warning("MusicBrainz unavailable: %s", e)
            meta, result = None, "offline"
        if meta:
            self.library.save_meta(disc_id, meta)
            cover = metadata.fetch_cover(meta)
            if cover:
                self.library.save_cover(disc_id, cover)
        with self._lock:
            if self.disc_id != disc_id:
                return
            self.lookup = result
            if meta:
                self._meta = meta

    def set_metadata(self, meta):
        with self._lock:
            if not self.disc_id:
                return False
            meta["source"] = "manual"
            self.library.save_meta(self.disc_id, meta)
            self._meta, self.lookup = meta, "manual"
            return True

    def current(self):
        """Snapshot: (status, toc, disc_id, meta, lookup)."""
        with self._lock:
            return self.status, self.toc, self.disc_id, self._meta, self.lookup


def cd_input_args(device, toc, number, offset=0.0, paranoia="disable"):
    start, duration = toc.track_bounds(number)
    offset = max(0.0, min(offset, duration - 0.1))
    return [
        "-f", "libcdio", "-paranoia_mode", paranoia,
        "-ss", f"{start + offset:.3f}", "-t", f"{duration - offset:.3f}",
        "-i", device,
    ]


class Ripper:
    """Rips the inserted disc to tagged MP3 files, one job at a time."""

    def __init__(self, device, library, monitor):
        self.device = device
        self.library = library
        self.monitor = monitor
        self._lock = threading.Lock()
        self._proc = None
        self._cancel = False
        self.job = None

    def state(self):
        with self._lock:
            return dict(self.job) if self.job else None

    def busy(self):
        with self._lock:
            return bool(self.job and self.job["state"] == "running")

    def start(self):
        status, toc, disc_id, meta, lookup = self.monitor.current()
        if status != "ok" or not toc:
            return "Brak płyty audio w napędzie."
        if lookup == "searching":
            return "Trwa wyszukiwanie opisu płyty, spróbuj za chwilę."
        with self._lock:
            if self.job and self.job["state"] == "running":
                return "Zgrywanie już trwa."
            self._cancel = False
            self.job = {
                "disc_id": disc_id, "state": "running", "track": None,
                "done": 0, "total": toc.track_count, "percent": 0.0, "error": None,
            }
        threading.Thread(target=self._run, args=(toc, disc_id, meta), name="ripper", daemon=True).start()
        return None

    def cancel(self):
        with self._lock:
            self._cancel = True
            if self._proc:
                self._proc.kill()

    def _update(self, **kw):
        with self._lock:
            self.job.update(kw)

    def _run(self, toc, disc_id, meta):
        try:
            self._rip(toc, disc_id, meta)
        except Exception as e:
            log.exception("ripping failed")
            self._update(state="error", error=str(e))

    def _rip(self, toc, disc_id, meta):
        album_dir = self.library.album_dir(disc_id, meta)
        cover = os.path.join(album_dir, "cover.jpg")
        cover = cover if os.path.isfile(cover) else None
        total_seconds = (toc.leadout - toc.offsets[0]) / 75
        done_seconds = 0.0
        numbers = range(toc.first, toc.last + 1)
        for i, number in enumerate(numbers):
            _start, duration = toc.track_bounds(number)
            if self.library.track_file(disc_id, number):
                done_seconds += duration
                self._update(done=i + 1, percent=100 * done_seconds / total_seconds)
                continue
            if self.monitor.current()[2] != disc_id:
                self._update(state="error", error="Płyta została wyjęta z napędu.")
                return
            self._update(track=number)
            filename = self.library.track_filename(meta, number)
            target = os.path.join(album_dir, filename)
            log.info("ripping track %d -> %s", number, target)

            def progress(seconds):
                self._update(percent=100 * (done_seconds + seconds) / total_seconds)

            ok = self._encode(toc, meta, number, cover, target + ".part", progress)
            if self._cancel:
                self._remove(target + ".part")
                self._update(state="cancelled", track=None)
                return
            if not ok:
                self._remove(target + ".part")
                self._update(state="error", error=f"Błąd odczytu utworu {number}.")
                return
            os.replace(target + ".part", target)
            self.library.register_track(disc_id, number, filename)
            done_seconds += duration
            self._update(done=i + 1, percent=100 * done_seconds / total_seconds)
        self._update(state="done", track=None, percent=100.0)
        log.info("ripping finished: %s", album_dir)

    def _encode(self, toc, meta, number, cover, output, progress):
        track = next(t for t in meta["tracks"] if t["number"] == number)
        tags = {
            "title": track["title"],
            "artist": track.get("artist") or meta["artist"],
            "album_artist": meta["artist"],
            "album": meta["album"],
            "date": meta.get("year", ""),
            "track": f"{number}/{toc.last}",
            "disc": f"{meta.get('disc_number', 1)}/{meta.get('disc_total', 1)}",
            "MusicBrainz Album Id": meta.get("mbid") or "",
        }
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-progress", "pipe:1", "-y"]
        cmd += cd_input_args(self.device, toc, number, paranoia="full")
        if cover:
            cmd += ["-i", cover, "-map", "0:a", "-map", "1:v", "-c:v", "copy",
                    "-disposition:v", "attached_pic",
                    "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"]
        else:
            cmd += ["-map", "0:a"]
        cmd += ["-map_chapters", "-1", "-map_metadata", "-1"]
        cmd += ["-c:a", "libmp3lame", "-b:a", MP3_BITRATE, "-id3v2_version", "3", "-write_id3v1", "1"]
        for key, value in tags.items():
            if value:
                cmd += ["-metadata", f"{key}={value}"]
        cmd += ["-f", "mp3", output]

        with self._lock:
            if self._cancel:
                return False
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        proc = self._proc
        for line in proc.stdout:
            if line.startswith("out_time_us="):
                try:
                    progress(int(line.split("=", 1)[1]) / 1e6)
                except ValueError:
                    pass
        stderr = proc.stderr.read()
        proc.wait()
        with self._lock:
            self._proc = None
        if proc.returncode != 0 and not self._cancel:
            log.error("ffmpeg failed for track %d: %s", number, stderr.strip())
        return proc.returncode == 0

    @staticmethod
    def _remove(path):
        try:
            os.remove(path)
        except OSError:
            pass
